#!/usr/bin/env python3
"""Tests for the append-mode + resume + periodic-flush behavior
in editlens_to_manifest (PR feat/editlens-to-manifest-incremental,
1.70.0).

Applies SAVE PROGRESS to the converter that wrote
``--out`` in 'w' mode and only flushed at the OS buffer's
natural cadence. EditLens parquet sources can be 1M+ rows; a
crash at row 800K of 1M lost most of the work with no resume
path. The new behavior reads the existing --out on startup,
builds the set of already-written IDs, opens in append mode,
and skips matching source rows.

Pins:

  * RESUME — existing --out's entry IDs are read and matching
    source rows are skipped.
  * APPEND — the file is opened in 'a' mode when resume engages,
    not 'w', so prior rows survive.
  * REFRESH — --refresh-output / --no-resume both force the
    pre-1.70.0 overwrite behavior.
  * MEASURE — periodic flush + stderr progress every flush_every
    rows.
  * BACK-COMPAT — when --out doesn't exist, behavior is identical
    to pre-1.70.0 (write mode, full row sweep).
  * CORRUPTED PARTIAL — a malformed line in the prior --out
    triggers a full re-write rather than silently mixing
    truncated state with new entries.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

import editlens_to_manifest as etm  # type: ignore  # noqa: E402


# --------------- Helpers ----------


def _write_csv(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


def _args(
    source: Path,
    out: Path,
    text_dir: Path,
    *,
    resume: bool = True,
    refresh_output: bool = False,
    flush_every: int = 1000,
    max_rows: int | None = None,
):
    return argparse.Namespace(
        source=str(source),
        out=str(out),
        text_dir=str(text_dir),
        text_column="text",
        label_column="label",
        label_map="0=pre_ai_human,1=ai_generated",
        register=None,
        language_status=None,
        use="validation",
        notes_columns=None,
        mixed_composite_states=None,
        preset=None,
        max_rows=max_rows,
        allow_public_output=True,
        resume=resume,
        refresh_output=refresh_output,
        flush_every=flush_every,
    )


def _rows(n: int) -> list[dict]:
    return [
        {
            "text": f"sample text body number {i} ok ok ok ok ok ok",
            "label": str(i % 2),
        }
        for i in range(n)
    ]


# --------------- RESUME ----------


def test_resume_appends_to_existing_out_and_skips_seen_ids(
    tmp_path: Path,
):
    """Run the converter on a 10-row source, manually trim the
    output to 4 entries, then re-run. The re-run should only
    write the missing 6 rows (the first 4 are already in --out
    and their IDs match)."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(10))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    # First pass: write all 10.
    args = _args(source, out, text_dir)
    rc = etm.convert(args)
    assert rc == 0
    full = [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln
    ]
    assert len(full) == 10

    # Truncate to first 4 entries (simulates a mid-run crash).
    truncated = "\n".join(full[:4]) + "\n"
    out.write_text(truncated, encoding="utf-8")
    assert sum(1 for _ in out.read_text(encoding="utf-8").splitlines()) == 4

    # Re-run with resume on (default).
    args2 = _args(source, out, text_dir)
    rc = etm.convert(args2)
    assert rc == 0
    # Output should now have all 10 entries (4 carried + 6 appended).
    lines = [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln
    ]
    assert len(lines) == 10, (
        f"expected 10 entries (4 resumed + 6 appended); got {len(lines)}"
    )
    # IDs should be unique — no duplicate entries from re-writing.
    ids = [json.loads(ln)["id"] for ln in lines]
    assert len(set(ids)) == 10


def test_no_resume_overwrites(tmp_path: Path):
    """--no-resume forces 'w' mode and full re-conversion."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(5))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    # Plant a bogus prior --out.
    out.write_text(
        json.dumps({"id": "leftover_garbage"}) + "\n",
        encoding="utf-8",
    )
    args = _args(source, out, text_dir, resume=False)
    rc = etm.convert(args)
    assert rc == 0
    lines = [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln
    ]
    # The leftover should be gone (overwrite mode).
    ids = {json.loads(ln)["id"] for ln in lines}
    assert "leftover_garbage" not in ids
    assert len(ids) == 5


def test_refresh_output_is_alias_for_no_resume(tmp_path: Path):
    """--refresh-output behaves like --no-resume (both force
    overwrite)."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(3))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    out.write_text(
        json.dumps({"id": "old_entry"}) + "\n",
        encoding="utf-8",
    )
    args = _args(source, out, text_dir, refresh_output=True)
    rc = etm.convert(args)
    assert rc == 0
    ids = {
        json.loads(ln)["id"]
        for ln in out.read_text(encoding="utf-8").splitlines()
        if ln
    }
    assert "old_entry" not in ids
    assert len(ids) == 3


def test_corrupted_prior_partial_triggers_full_rewrite(tmp_path: Path):
    """A malformed line in the existing --out (mid-row crash)
    should trigger a clean overwrite, not silent mixing of bad +
    good lines."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(3))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    out.write_text(
        '{"id": "good_one", "ai_status": "ai_generated"}\n'
        "{ this line is malformed json\n",
        encoding="utf-8",
    )
    args = _args(source, out, text_dir)
    rc = etm.convert(args)
    assert rc == 0
    lines = [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln
    ]
    # All 3 source rows present; "good_one" leftover gone.
    ids = [json.loads(ln)["id"] for ln in lines]
    assert "good_one" not in ids
    assert len(ids) == 3


# --------------- MEASURE ----------


def test_periodic_flush_logs_to_stderr(tmp_path: Path, capsys):
    """At flush_every=2 across 5 rows, expect at least one
    progress line on stderr."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(5))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    args = _args(source, out, text_dir, flush_every=2)
    etm.convert(args)
    captured = capsys.readouterr()
    progress_lines = [
        ln for ln in captured.err.splitlines()
        if "converted" in ln and "/s" in ln
    ]
    assert len(progress_lines) >= 1, (
        f"expected at least one progress line on stderr; got "
        f"err={captured.err!r}"
    )


# --------------- BACK-COMPAT ----------


def test_fresh_run_no_prior_out_back_compat(tmp_path: Path):
    """When --out doesn't exist, behavior is identical to pre-
    1.70.0: every source row is converted, no skip, no append."""
    source = tmp_path / "src.csv"
    _write_csv(source, _rows(4))
    out = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"

    args = _args(source, out, text_dir)
    rc = etm.convert(args)
    assert rc == 0
    lines = [
        ln for ln in out.read_text(encoding="utf-8").splitlines() if ln
    ]
    assert len(lines) == 4
