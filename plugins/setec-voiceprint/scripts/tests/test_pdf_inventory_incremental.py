#!/usr/bin/env python3
"""Tests for the partial-inventory cache + resume in
pdf_inventory (PR feat/pdf-inventory-incremental-write, 1.70.0).

Applies SAVE PROGRESS to a script that was already parallel
(ThreadPoolExecutor with as_completed) but accumulated all
results in memory and only wrote the JSONL after every worker
completed — a crash mid-run lost everything.

This module pins:

  * SAVE PROGRESS — partial-cache sidecar JSON is written every
    ``--flush-every`` worker completions, with atomic tmp+rename.
  * RESUME — paths already in the partial cache are skipped on
    the next run; ``classify_pdf`` doesn't fire for them.
  * CLEANUP — once the final JSONL is written, the partial cache
    is deleted (the JSONL is the canonical artifact).
  * BACK-COMPAT — ``--no-incremental-cache`` reverts to the pre-
    1.70.0 monolithic behavior.
  * CLI surface — three new flags exposed.

Stubs ``classify_pdf`` so tests don't need PyPDF.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdf_inventory as pi  # type: ignore  # noqa: E402


# --------------- Helpers ----------


def _stub_classify(path: Path) -> pi.InventoryEntry:
    """Cheap stub for ``classify_pdf`` so tests don't need PyPDF."""
    return pi.InventoryEntry(
        path=str(path),
        file_hash=f"sha256:{path.name}",
        title=path.stem,
        author=None,
        creation_date=None,
        page_count=10,
        extractable=True,
        needs_ocr=False,
        has_ocr_layer=False,
        estimated_words=1000,
        classification="text_extractable",
        metadata_quality="rich",
        notes=None,
        file_size_bytes=12345,
        sample_chars_extracted=500,
    )


def _fake_pdfs(tmp_path: Path, n: int) -> list[Path]:
    """Create n empty 'PDF' files. classify_pdf is stubbed so the
    files don't need to be real PDFs — they just need to .exist()
    and have a size."""
    paths = []
    for i in range(n):
        p = tmp_path / f"doc_{i:02d}.pdf"
        p.write_bytes(b"x" * 100)
        paths.append(p)
    return paths


# --------------- CLI surface ----------


def test_incremental_cache_flags_exist():
    p = pi.build_arg_parser()
    args = p.parse_args([
        "--root", "/tmp", "--output", "/tmp/out.jsonl",
        "--allow-public-output",
    ])
    assert args.no_incremental_cache is False
    assert args.flush_every == 25
    assert args.refresh_partial is False
    args = p.parse_args([
        "--root", "/tmp", "--output", "/tmp/out.jsonl",
        "--allow-public-output",
        "--no-incremental-cache",
        "--flush-every", "5",
        "--refresh-partial",
    ])
    assert args.no_incremental_cache is True
    assert args.flush_every == 5
    assert args.refresh_partial is True


# --------------- SAVE PROGRESS ----------


def test_partial_cache_written_during_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With incremental_cache=True (default), the partial sidecar
    JSON is written every flush_every completions."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 10)
    output = tmp_path / "inv.jsonl"
    save_calls: list[int] = []
    real_save = pi._save_partial_inventory

    def _spy(partial_path, entries, **kw):
        save_calls.append(len(entries))
        return real_save(partial_path, entries, **kw)

    monkeypatch.setattr(pi, "_save_partial_inventory", _spy)
    pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
        flush_every=3,
    )
    # At flush_every=3 across 10 paths, expect flushes at 3, 6, 9,
    # plus the forced final flush. Sizes monotonically grow.
    assert len(save_calls) >= 3, save_calls
    assert sorted(save_calls) == save_calls
    # Final JSONL is the canonical artifact.
    assert output.exists()


def test_partial_cache_deleted_after_final_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The partial cache is a checkpoint, not an artifact. Once
    the final JSONL is on disk, the partial is unlinked."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 5)
    output = tmp_path / "inv.jsonl"
    pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
    )
    assert output.exists()
    partial = pi._partial_cache_path_for(output)
    assert not partial.exists(), (
        f"partial cache should be cleaned up after final write; "
        f"found at {partial}"
    )


# --------------- RESUME ----------


def test_resume_from_partial_skips_already_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Pre-populate the partial cache with 3 of 5 paths. The next
    run should call classify_pdf only for the 2 missing paths."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 5)
    output = tmp_path / "inv.jsonl"
    partial = pi._partial_cache_path_for(output)

    # Build a partial cache with the first 3 paths "already done".
    pre = {str(p): asdict(_stub_classify(p)) for p in paths[:3]}
    pi._save_partial_inventory(
        partial, pre, max_file_bytes=10 * 1024 * 1024,
    )

    classify_count = {"n": 0}

    def _counting_classify(path: Path) -> pi.InventoryEntry:
        classify_count["n"] += 1
        return _stub_classify(path)

    monkeypatch.setattr(pi, "classify_pdf", _counting_classify)
    summary = pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
    )
    assert classify_count["n"] == 2, (
        f"expected 2 fresh classifications (3 resumed); got "
        f"{classify_count['n']}"
    )
    # Final JSONL has all 5 entries.
    with output.open("r", encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln]
    assert len(lines) == 5
    assert summary.inventoried == 5


def test_refresh_partial_discards_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``refresh_partial=True`` ignores any existing partial and
    re-classifies."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 3)
    output = tmp_path / "inv.jsonl"
    partial = pi._partial_cache_path_for(output)
    pre = {str(p): asdict(_stub_classify(p)) for p in paths}
    pi._save_partial_inventory(
        partial, pre, max_file_bytes=10 * 1024 * 1024,
    )
    classify_count = {"n": 0}

    def _counting_classify(path: Path) -> pi.InventoryEntry:
        classify_count["n"] += 1
        return _stub_classify(path)

    monkeypatch.setattr(pi, "classify_pdf", _counting_classify)
    pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
        refresh_partial=True,
    )
    assert classify_count["n"] == 3  # re-classified despite cache


def test_incompatible_cache_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A partial cache with a different max_file_bytes is
    discarded (would silently re-use stale skip decisions)."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 3)
    output = tmp_path / "inv.jsonl"
    partial = pi._partial_cache_path_for(output)
    pre = {str(p): asdict(_stub_classify(p)) for p in paths}
    # Plant a cache with a max_file_bytes that differs from the
    # current call.
    pi._save_partial_inventory(
        partial, pre, max_file_bytes=999,
    )
    classify_count = {"n": 0}

    def _counting_classify(path: Path) -> pi.InventoryEntry:
        classify_count["n"] += 1
        return _stub_classify(path)

    monkeypatch.setattr(pi, "classify_pdf", _counting_classify)
    pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
    )
    assert classify_count["n"] == 3


# --------------- BACK-COMPAT ----------


def test_no_incremental_cache_back_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``incremental_cache=False`` reverts to pre-1.70.0 behavior:
    no partial sidecar is written."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 4)
    output = tmp_path / "inv.jsonl"
    pi.write_inventory(
        paths, output=output, workers=1,
        max_file_bytes=10 * 1024 * 1024, verbose=False,
        incremental_cache=False,
    )
    assert output.exists()
    partial = pi._partial_cache_path_for(output)
    assert not partial.exists()


def test_deterministic_output_order_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The final JSONL is in input-path order regardless of
    worker concurrency — that property survives the partial-cache
    refactor (cache is path-keyed dict; final emit walks paths in
    input order)."""
    monkeypatch.setattr(pi, "classify_pdf", _stub_classify)
    paths = _fake_pdfs(tmp_path, 8)
    output = tmp_path / "inv.jsonl"
    pi.write_inventory(
        paths, output=output, workers=4,  # threads in flight
        max_file_bytes=10 * 1024 * 1024, verbose=False,
    )
    with output.open("r", encoding="utf-8") as f:
        emitted = [json.loads(ln) for ln in f.read().splitlines() if ln]
    expected_paths = [str(p) for p in paths]
    actual_paths = [e["path"] for e in emitted]
    assert actual_paths == expected_paths
