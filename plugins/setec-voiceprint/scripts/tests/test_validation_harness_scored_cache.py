#!/usr/bin/env python3
"""Tests for the scored-records cache + incremental checkpoint in
validation_harness (PR feat/validation-harness-scoring-checkpoint,
1.70.0).

Mirrors the pattern shipped in calibration_survey 1.69.0 (PR #68):
``--scored-records-cache`` + ``--scored-records-flush-every N``.
Pins:

  * MEASURE — the scoring loop writes a progress log line to
    stderr (not stdout, so ``--json`` output stays parseable) for
    each per-flush milestone, with rate (entries/s) and ETA.
  * SAVE PROGRESS — when the cache flag is set, the records list
    is written atomically every N entries with
    ``status: "in_progress"``; the final write flips status to
    ``"complete"``.
  * RESUME — a subsequent run with the same cache path loads the
    partial, derives scored entry IDs, and skips them.
  * BACK-COMPAT — without the cache flag, the harness behaves
    identically to pre-1.70.0 (just with the progress log to
    stderr).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import validation_harness as vh  # type: ignore  # noqa: E402


# --------------- Helpers ----------


def _stub_score_entry(entry, **kwargs):
    """Cheap stub for ``score_smoothing_entry`` so tests don't
    require spaCy / SBERT. Returns the minimum record shape the
    cache-resume logic needs (id + label + score)."""
    return {
        "id": entry.get("id") or f"line_{entry.get('_lineno', '?')}",
        "label": (
            1 if entry.get("ai_status") == "ai_generated" else 0
        ),
        "score": 0.5,
        "usable_for_metrics": True,
        "per_signal_scores": {},
    }


def _make_entries(n: int) -> list[dict]:
    return [
        {
            "id": f"entry_{i:04d}",
            "ai_status": "ai_generated" if i % 2 == 0 else "pre_ai_human",
            "_lineno": i,
        }
        for i in range(n)
    ]


# --------------- CLI surface ----------


def test_scored_records_flags_exist():
    parser_argv = ["fake_manifest.jsonl", "--no-tier2", "--no-tier3"]
    # The parser is built inside main(); inspect --help output.
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            vh.main(["--help"])
        except SystemExit:
            pass
    help_text = buf.getvalue()
    assert "--scored-records-cache" in help_text
    assert "--scored-records-flush-every" in help_text
    assert "--refresh-scored-records-cache" in help_text


# --------------- SAVE PROGRESS + RESUME ----------


def test_partial_cache_written_every_flush_every_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """At flush_every=2 across 5 entries, expect at least one
    in-progress flush + a final complete write."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    cache = tmp_path / "cache.json"
    entries = _make_entries(5)
    save_calls: list[str] = []
    real_save = vh._save_scored_records_cache

    def _spy(path, records, **kw):
        save_calls.append(kw.get("status", "?"))
        return real_save(path, records, **kw)

    monkeypatch.setattr(vh, "_save_scored_records_cache", _spy)
    vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=cache, flush_every=2,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    in_progress = sum(1 for s in save_calls if s == "in_progress")
    complete = sum(1 for s in save_calls if s == "complete")
    assert in_progress >= 1, save_calls
    assert complete == 1, save_calls


def test_resume_from_partial_skips_already_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Pre-populate the cache with 3 of 5 entries + status=
    in_progress. The next run should call score_smoothing_entry
    only for the missing 2."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    cache = tmp_path / "cache.json"
    entries = _make_entries(5)
    pre_records = [_stub_score_entry(e) for e in entries[:3]]
    cache.write_text(json.dumps({
        "status": "in_progress",
        "scoring_meta": {
            "manifest_path": "dummy.jsonl",
            "use_filter": "validation",
            "do_tier2": False,
            "do_tier3": False,
        },
        "records": pre_records,
    }))

    score_count = {"n": 0}

    def _counting_stub(entry, **kw):
        score_count["n"] += 1
        return _stub_score_entry(entry, **kw)

    monkeypatch.setattr(vh, "score_smoothing_entry", _counting_stub)
    records = vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=cache, flush_every=10,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    assert score_count["n"] == 2, (
        f"expected 2 fresh score calls (3 resumed); got "
        f"{score_count['n']}"
    )
    assert len(records) == 5


def test_complete_cache_is_full_hit_no_rescore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A cache with status=complete returns its records and skips
    the scoring loop entirely."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    cache = tmp_path / "cache.json"
    entries = _make_entries(5)
    pre_records = [_stub_score_entry(e) for e in entries]
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "manifest_path": "dummy.jsonl",
            "use_filter": "validation",
            "do_tier2": False,
            "do_tier3": False,
        },
        "records": pre_records,
    }))
    score_count = {"n": 0}

    def _counting_stub(entry, **kw):
        score_count["n"] += 1
        return _stub_score_entry(entry, **kw)

    monkeypatch.setattr(vh, "score_smoothing_entry", _counting_stub)
    records = vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=cache, flush_every=10,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    assert score_count["n"] == 0
    assert len(records) == 5


def test_refresh_flag_ignores_cache_and_rescores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``refresh_cache=True`` discards any existing cache."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    cache = tmp_path / "cache.json"
    entries = _make_entries(3)
    pre_records = [_stub_score_entry(e) for e in entries]
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "manifest_path": "dummy.jsonl",
            "use_filter": "validation",
            "do_tier2": False,
            "do_tier3": False,
        },
        "records": pre_records,
    }))
    score_count = {"n": 0}

    def _counting_stub(entry, **kw):
        score_count["n"] += 1
        return _stub_score_entry(entry, **kw)

    monkeypatch.setattr(vh, "score_smoothing_entry", _counting_stub)
    vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=cache, flush_every=10,
        refresh_cache=True,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    assert score_count["n"] == 3  # re-scored despite complete cache


def test_incompatible_cache_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A cache whose scoring_meta doesn't match (different
    manifest path) is discarded; entries are re-scored fresh."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    cache = tmp_path / "cache.json"
    entries = _make_entries(3)
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "manifest_path": "OTHER_manifest.jsonl",
            "use_filter": "validation",
            "do_tier2": False,
            "do_tier3": False,
        },
        "records": [_stub_score_entry(e) for e in entries],
    }))
    score_count = {"n": 0}

    def _counting_stub(entry, **kw):
        score_count["n"] += 1
        return _stub_score_entry(entry, **kw)

    monkeypatch.setattr(vh, "score_smoothing_entry", _counting_stub)
    vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=cache, flush_every=10,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    assert score_count["n"] == 3


def test_no_cache_path_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Back-compat: omitting cache_path produces the records list
    just like the pre-1.70 list-comp did. The progress log goes
    to stderr (not stdout), so a downstream --json parse on the
    overall harness output is unaffected."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    entries = _make_entries(3)
    records = vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=None,
    )
    assert len(records) == 3


# --------------- MEASURE ----------


def test_progress_log_goes_to_stderr_not_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """The progress log MUST go to stderr — otherwise it pollutes
    --json output on stdout (which is how the harness reports its
    final JSON payload). Regression guard for the bug caught when
    test_validation_harness_check_corpus_allows_clean_entry tried
    to ``json.loads(proc.stdout)``."""
    monkeypatch.setattr(vh, "score_smoothing_entry", _stub_score_entry)
    entries = _make_entries(5)
    vh._score_validation_entries_with_progress(
        entries,
        mattr_window=50, do_tier2=False, do_tier3=False,
        allow_non_prose=False, strip_rules=None,
        strip_aggressive=False,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        cache_path=None, flush_every=2,
        manifest_path="dummy.jsonl", use_filter="validation",
    )
    captured = capsys.readouterr()
    # The "Scoring N validation entries" announce + the per-flush
    # progress lines must NOT appear on stdout.
    assert "Scoring" not in captured.out, (
        f"stdout polluted: {captured.out!r}"
    )
    assert "scored " not in captured.out, (
        f"stdout polluted: {captured.out!r}"
    )
    # They DO appear on stderr.
    assert "Scoring" in captured.err
