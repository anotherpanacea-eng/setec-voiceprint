#!/usr/bin/env python3
"""Tests for the incremental corpus-scoring cache (PR
feat/incremental-corpus-scoring, 1.69.0).

Stacked on the 1.68.0 streaming-pair-extraction branch. Applies
the four operational principles to ``calibrate_thresholds.score_
corpus``: an all-or-nothing scoring loop that crashed mid-run
lost everything. This module pins the four principles in
behavior:

  * SAVE PROGRESS — every ``--records-cache-flush-every N`` rows,
    the scored records list is written atomically with
    ``status: "in_progress"`` to the records cache path.
  * RESUME — on subsequent runs against the same cache, scoring
    skips entries whose IDs are already present in the partial.
  * MEASURE — the in-loop log line includes rate (entries/s) and
    ETA (minutes-to-completion).
  * COMPLETE-MARKER — the final write flips status to
    ``"complete"`` so future runs detect the full-cache hit path
    (back-compat with pre-1.69.0 caches that lack the field).

Re-uses the synthetic-manifest + ``score_smoothing_entry`` stub
from ``test_calibration_cache.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibrate_thresholds as ct  # type: ignore  # noqa: E402

from test_calibration_cache import (  # noqa: E402
    _write_real_manifest,
    _make_args,
    _patch_scoring,
)


# --------------- SAVE PROGRESS ----------


def test_partial_cache_written_every_flush_every_entries(tmp_path):
    """During a fresh scoring run, the records cache must be
    written with ``status: in_progress`` every N entries (where N
    is ``--records-cache-flush-every``). At N=2 across 5 entries,
    we expect flushes at i=2 and i=4 (logging fires when
    ``i % flush_every == 0 and i > 0``)."""
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(
        manifest,
        records_cache=str(cache),
        records_cache_flush_every=2,
    )
    save_calls: list[str] = []
    real_save = ct._save_score_cache

    def _spy(path, scoring_meta, records, status):
        save_calls.append(status)
        return real_save(path, scoring_meta, records, status)

    with _patch_scoring({}), mock.patch.object(
        ct, "_save_score_cache", _spy,
    ):
        records, _meta, _hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert len(records) == 5
    # Expect 2 in-progress flushes (at i=2, i=4) + 1 complete
    # save at the end.
    in_progress_count = sum(1 for s in save_calls if s == "in_progress")
    complete_count = sum(1 for s in save_calls if s == "complete")
    assert in_progress_count >= 1, (
        f"expected at least one in-progress flush; got {save_calls}"
    )
    assert complete_count == 1, (
        f"expected exactly one complete write; got {save_calls}"
    )


def test_final_cache_status_is_complete(tmp_path):
    """The cache file on disk after a successful run must have
    ``status: complete``."""
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    with _patch_scoring({}):
        ct.load_or_score_corpus(args, cache_path=cache)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload.get("status") == "complete"


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    """Atomic write contract: tmp + rename pattern leaves no
    leftover .tmp file after a clean run."""
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    with _patch_scoring({}):
        ct.load_or_score_corpus(args, cache_path=cache)
    assert cache.exists()
    assert not cache.with_suffix(cache.suffix + ".tmp").exists()


# --------------- RESUME ----------


def test_resume_skips_already_scored_entries(tmp_path):
    """Pre-populate the cache with status='in_progress' + 3 of 5
    entries. The next run should skip those 3 and score only the
    remaining 2."""
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))

    # First: score all 5 normally to get a valid records shape +
    # scoring_meta. Then truncate to the first 3 and flip status.
    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert counter["calls"] == 5
    partial = {
        "status": "in_progress",
        "scoring_meta": scoring_meta,
        "records": records[:3],  # only first 3 already scored
    }
    cache.write_text(json.dumps(partial, default=str))

    # Now re-score from the partial. The stub counter resets
    # between context managers, so we can count fresh calls.
    counter2 = {"calls": 0}
    with _patch_scoring(counter2):
        records2, _meta2, hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert hit is False, "partial cache must not register as a hit"
    assert counter2["calls"] == 2, (
        f"expected exactly 2 fresh score calls (resume skipped 3); "
        f"got {counter2['calls']}"
    )
    assert len(records2) == 5


def test_resume_preserves_already_scored_records(tmp_path):
    """The records carried forward from the partial cache must
    appear unchanged in the final records list (not re-scored)."""
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    with _patch_scoring({}):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    # Mutate the records[0] to a sentinel value and re-write as partial.
    records[0]["sentinel"] = "carried_forward_unchanged"
    partial = {
        "status": "in_progress",
        "scoring_meta": scoring_meta,
        "records": records[:2],  # entries 0 and 1
    }
    cache.write_text(json.dumps(partial, default=str))

    with _patch_scoring({}):
        records2, _meta2, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    # The sentinel record came through unchanged — re-scoring
    # would have stripped it.
    by_id = {r["id"]: r for r in records2}
    assert by_id[records[0]["id"]].get("sentinel") == (
        "carried_forward_unchanged"
    )


def test_resume_on_incompatible_partial_rescores_from_scratch(tmp_path):
    """If the partial cache exists but its scoring_meta is
    incompatible (different manifest, different tier flags, etc.),
    discard and re-score from scratch — same conservative behavior
    as for ``status: complete`` caches."""
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    # Plant a partial with bogus manifest_sha256.
    partial = {
        "status": "in_progress",
        "scoring_meta": {
            "manifest_path": str(manifest),
            "manifest_sha256": "bogus-incompatible-hash",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
        },
        "records": [
            {"id": "essay_0", "score": 0.5, "label": 1},
            {"id": "essay_1", "score": 0.6, "label": 0},
        ],
    }
    cache.write_text(json.dumps(partial))
    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, _meta, _hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    # All entries re-scored fresh; resume rejected.
    assert counter["calls"] == 4
    assert len(records) == 4


# --------------- BACKWARD COMPAT ----------


def test_pre_1_69_cache_without_status_treated_as_complete(tmp_path):
    """A cache file from before 1.69.0 has no ``status`` field.
    The loader must treat it as ``status: complete`` for back-
    compat (existing test fixtures + operator caches keep
    working)."""
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    # Build a pre-1.69 cache shape: scoring_meta + records, no status.
    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    # Strip the status field as a pre-1.69 cache would.
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload.pop("status", None)
    cache.write_text(json.dumps(payload, default=str))
    assert "status" not in json.loads(cache.read_text(encoding="utf-8"))

    counter2 = {"calls": 0}
    with _patch_scoring(counter2):
        records2, _meta2, hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    # Cache hit (legacy path); zero re-score calls.
    assert hit is True
    assert counter2["calls"] == 0


# --------------- REFRESH-CACHE × PARTIAL-RESUME (codex P2 on #68) ----------


def test_refresh_cache_discards_partial_resume(tmp_path):
    """When the operator passes ``--refresh-cache``, the partial-
    cache resume path in ``score_corpus`` must NOT fire.

    Bug fixed by this test: ``load_or_score_corpus(refresh=True)``
    only bypassed the *complete-cache hit* return path. It still
    handed ``partial_cache_path=cache_path`` to ``score_corpus``,
    which unconditionally read the partial cache and resumed from
    it — silently ignoring the operator's explicit refresh ask.
    """
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))

    # First: score all 5 normally, then flip the on-disk cache to
    # status="in_progress" with only 3 records so a resume would
    # leave 2 entries unscored.
    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert counter["calls"] == 5
    partial = {
        "status": "in_progress",
        "scoring_meta": scoring_meta,
        "records": records[:3],
    }
    cache.write_text(json.dumps(partial, default=str))

    # Now ask for a refresh. Bug behavior: only 2 entries re-scored
    # (resume kicked in). Fixed behavior: all 5 re-scored (resume
    # skipped because refresh=True).
    counter2 = {"calls": 0}
    with _patch_scoring(counter2):
        records2, _meta2, hit = ct.load_or_score_corpus(
            args, cache_path=cache, refresh=True,
        )
    assert hit is False
    assert counter2["calls"] == 5, (
        f"--refresh-cache must trigger a full re-score; got "
        f"{counter2['calls']} score calls (expected 5). The "
        f"partial-cache resume path must not fire when refresh=True."
    )
    assert len(records2) == 5


def test_refresh_cache_unlinks_prior_partial(tmp_path):
    """``--refresh-cache`` should unlink the pre-existing partial
    cache file before scoring so a crash mid-refresh leaves a clean
    partial — not a partial that interleaves the discarded prior
    run's first N records with the new pass's first M-N records."""
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    cache = tmp_path / "cache.json"
    # Plant a partial cache that we explicitly want discarded.
    partial = {
        "status": "in_progress",
        "scoring_meta": {
            "manifest_path": str(manifest),
            "manifest_sha256": "doesnt-matter-refresh-discards-without-check",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
        },
        "records": [
            {"id": "essay_99", "score": 0.99, "label": 1},
        ],
    }
    cache.write_text(json.dumps(partial))
    args = _make_args(manifest, records_cache=str(cache))

    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, _meta = ct.score_corpus(
            args,
            partial_cache_path=cache,
            flush_every=100,
            refresh=True,
        )
    # All 4 manifest entries scored fresh — no carry-forward from
    # the discarded partial. The sentinel essay_99 id from the prior
    # partial does NOT appear in the new records.
    assert counter["calls"] == 4
    ids = {r["id"] for r in records}
    assert "essay_99" not in ids, (
        f"refresh=True must not carry forward records from the prior "
        f"partial cache; got ids={ids}"
    )


def test_refresh_cache_default_false_preserves_resume(tmp_path):
    """Regression guard: without --refresh-cache, the partial-
    resume path keeps working (we didn't accidentally rip out the
    1.69.0 resume contract while fixing the refresh interaction)."""
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))

    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert counter["calls"] == 5

    partial = {
        "status": "in_progress",
        "scoring_meta": scoring_meta,
        "records": records[:3],
    }
    cache.write_text(json.dumps(partial, default=str))

    # No refresh flag: resume should fire, only 2 fresh score calls.
    counter2 = {"calls": 0}
    with _patch_scoring(counter2):
        _records2, _meta2, _hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert counter2["calls"] == 2, (
        f"resume contract regressed: expected 2 fresh score calls "
        f"with refresh=False; got {counter2['calls']}"
    )


# --------------- MEASURE (rate + ETA in progress log) ----------


def test_progress_log_includes_rate_and_eta(tmp_path, capsys):
    """The in-loop progress log fires every flush_every entries
    and includes a rate (entries/s) and ETA (minutes). With
    flush_every=2 across 5 entries, we expect at least one
    progress line in stdout."""
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    args = _make_args(
        manifest,
        records_cache=None,
        records_cache_flush_every=2,
    )
    with _patch_scoring({}):
        ct.score_corpus(args, flush_every=2)
    captured = capsys.readouterr()
    # At least one "scored N/M" line; check for the rate marker.
    progress_lines = [
        ln for ln in captured.out.splitlines()
        if "scored " in ln and "/" in ln
    ]
    assert progress_lines, (
        f"expected at least one progress line; got captured.out="
        f"{captured.out!r}"
    )
    # The new format includes "/s" (rate) and "ETA" markers.
    progress_blob = "\n".join(progress_lines)
    assert "/s" in progress_blob, (
        f"expected rate marker (/s) in progress log; got "
        f"{progress_lines}"
    )
    assert "ETA" in progress_blob, (
        f"expected ETA marker in progress log; got {progress_lines}"
    )


# --------------- CLI surface ----------


def test_calibration_survey_parser_has_flush_every_flag():
    """Confirm the new flag is exposed on calibration_survey's
    standalone CLI."""
    import calibration_survey as cs  # type: ignore
    p = cs.build_arg_parser()
    args = p.parse_args([
        "--manifest", "x.jsonl", "--fpr-target", "0.01",
    ])
    assert hasattr(args, "records_cache_flush_every")
    assert args.records_cache_flush_every == 100  # default
    args2 = p.parse_args([
        "--manifest", "x.jsonl", "--fpr-target", "0.01",
        "--records-cache-flush-every", "25",
    ])
    assert args2.records_cache_flush_every == 25


def test_calibrate_thresholds_parser_has_flush_every_flag():
    """Same flag exposed on the standalone calibrate_thresholds
    CLI for symmetry. The parser is built inline inside ``main()``;
    --help exits 0 after printing, and the flag presence in
    --help output confirms it's wired."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ct.main(["--help"])
        except SystemExit:
            pass
    help_text = buf.getvalue()
    assert "--records-cache-flush-every" in help_text, (
        "--records-cache-flush-every must appear in calibrate_"
        "thresholds --help output"
    )
