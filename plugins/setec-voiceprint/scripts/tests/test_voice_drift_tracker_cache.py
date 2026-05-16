#!/usr/bin/env python3
"""Tests for the per-doc feature cache + per-period progress log
in voice_drift_tracker (PR feat/voice-drift-tracker-feature-cache,
1.70.0).

``build_period_profiles`` calls ``extract_features(text)`` once per
doc per call. On a 1200-doc baseline (e.g. 10 years of essays
grouped into 40 quarterly periods × 30 docs/period), the
extraction dominates wall-clock — and re-runs with a different
``--period-granularity`` against the same baseline re-extract
everything. The cache flag stores results keyed by absolute doc
path so the next run reuses them.

Pins:

  * SAVE PROGRESS — atomic per-N-docs flush of the feature cache.
  * RESUME — paths already in the cache skip extraction.
  * REFRESH — ``--refresh-feature-cache`` re-extracts.
  * MEASURE — per-period progress log to stderr (always on) with
    rate + ETA + per-period cache-hit count.
  * BACK-COMPAT — no cache flag => no on-disk cache, but progress
    log still fires.
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

import voice_drift_tracker as vdt  # type: ignore  # noqa: E402


# --------------- Helpers ----------


def _stub_extract(text: str) -> dict:
    """Cheap stand-in for ``extract_features`` so tests don't need
    spaCy. Returns the dict shape build_period_profiles consumes."""
    return {
        "features": {
            "function_words": {"the": 0.05, "of": 0.03},
            "pos_unigrams": {"NOUN": 0.25, "VERB": 0.15},
        },
        "summary": {"n_words": len(text.split())},
    }


def _grouped(tmp_path: Path, n_per_period: int = 3):
    """Create a synthetic grouped dict with 2 periods × N docs."""
    grouped = {}
    for period in ["2024-Q1", "2024-Q2"]:
        entries = []
        for i in range(n_per_period):
            p = tmp_path / f"{period}_{i}.txt"
            p.write_text(f"word " * 100, encoding="utf-8")
            entries.append(vdt.DatedEntry(
                id=f"{period}_{i}",
                path=p,
                date_str=f"2024-0{1 + i}-01",
                date_tuple=(2024, 1 + i, 1),
                extra={},
            ))
        grouped[period] = entries
    return grouped


# --------------- SAVE PROGRESS ----------


def test_feature_cache_written_during_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With feature_cache_path set, the cache is written every
    flush_every fresh extractions plus a final flush."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=3)  # 6 docs total
    cache = tmp_path / "features.json"
    save_calls: list[int] = []
    real_save = vdt._save_feature_cache

    def _spy(path, cache_dict):
        save_calls.append(len(cache_dict))
        return real_save(path, cache_dict)

    monkeypatch.setattr(vdt, "_save_feature_cache", _spy)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=2,
    )
    # At flush_every=2 across 6 fresh extractions, expect flushes
    # at 2 and 4, plus a final flush at 6 (after the loop). All
    # sizes monotonic.
    assert len(save_calls) >= 2, save_calls
    assert sorted(save_calls) == save_calls
    # Cache exists on disk.
    assert cache.exists()


# --------------- RESUME ----------


def test_resume_skips_already_cached_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Pre-populate the cache with 4 of 6 docs. The next run
    should only call extract_features for the 2 missing docs."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=3)  # 6 docs
    cache = tmp_path / "features.json"
    # Cache the first 4 docs with the new-schema fields
    # (text_hash + features_version) so the compat check passes.
    paths = [e.path for entries in grouped.values() for e in entries]
    pre_cache = {}
    for p in paths[:4]:
        feats = dict(_stub_extract("word"))
        feats["text_hash"] = vdt._doc_content_hash(p)
        feats["features_version"] = vdt.VOICE_DRIFT_FEATURES_VERSION
        pre_cache[str(p.resolve())] = feats
    vdt._save_feature_cache(cache, pre_cache)

    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    assert extract_count["n"] == 2, (
        f"expected 2 fresh extract calls (4 resumed); got "
        f"{extract_count['n']}"
    )


def test_refresh_feature_cache_re_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``refresh_cache=True`` discards prior cache."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)  # 4 docs
    cache = tmp_path / "features.json"
    paths = [e.path for entries in grouped.values() for e in entries]
    pre_cache = {
        str(p.resolve()): _stub_extract("word") for p in paths
    }
    vdt._save_feature_cache(cache, pre_cache)

    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache,
        flush_every=10, refresh_cache=True,
    )
    assert extract_count["n"] == 4  # re-extracted despite cache


# --------------- BACK-COMPAT ----------


def test_no_cache_path_back_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Without feature_cache_path, behaves like pre-1.70.0:
    every doc is extracted, no cache written."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)  # 4 docs
    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    profiles, _ = vdt.build_period_profiles(grouped)
    assert extract_count["n"] == 4
    assert len(profiles) == 2


# --------------- MEASURE ----------


def test_per_period_progress_log_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """Each period completion logs to stderr with rate + ETA +
    per-period cache-hit count."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)  # 2 periods × 2 docs
    vdt.build_period_profiles(grouped)
    captured = capsys.readouterr()
    period_lines = [
        ln for ln in captured.err.splitlines()
        if "period" in ln.lower() and "/s" in ln
    ]
    assert len(period_lines) >= 2, (
        f"expected at least 2 per-period progress lines on stderr; "
        f"got {period_lines}"
    )
    # The format includes "ETA" marker.
    progress_blob = "\n".join(period_lines)
    assert "ETA" in progress_blob


def test_cli_flags_exist():
    """Confirm the three new flags are on the standalone CLI."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            vdt.main(["--help"])
        except SystemExit:
            pass
    help_text = buf.getvalue()
    assert "--feature-cache" in help_text
    assert "--feature-cache-flush-every" in help_text
    assert "--refresh-feature-cache" in help_text


# --------------- Codex P2 on PR #73: per-doc text-hash + version check


def test_edited_doc_invalidates_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: if a baseline doc is edited or regenerated at the
    same path, the cached features must NOT be reused. The new
    text_hash check catches this."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)  # 4 docs
    cache = tmp_path / "features.json"
    # First run: populate the cache.
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    # Edit the first doc's content. Same path; different bytes.
    first_doc = grouped["2024-Q1"][0].path
    first_doc.write_text("totally different content " * 50, encoding="utf-8")

    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    assert extract_count["n"] == 1, (
        f"expected exactly 1 fresh extract for the edited doc "
        f"(3 reused); got {extract_count['n']}"
    )


def test_features_version_bump_invalidates_all_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: bumping VOICE_DRIFT_FEATURES_VERSION must
    invalidate every cached entry — the cache stamp no longer
    matches what the current code would compute."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)  # 4 docs
    cache = tmp_path / "features.json"
    # First run populates with VOICE_DRIFT_FEATURES_VERSION="1".
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    # Simulate a version bump by monkey-patching the constant for
    # the next call. Every cached entry now has a stale version.
    monkeypatch.setattr(vdt, "VOICE_DRIFT_FEATURES_VERSION", "2")

    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    assert extract_count["n"] == 4, (
        f"expected all 4 docs re-extracted under bumped version; "
        f"got {extract_count['n']}"
    )


def test_pre_fix_cache_entries_treated_as_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Back-compat: cache entries written before the
    text_hash/features_version fields existed are treated as
    cache misses (falls through to re-extraction) rather than
    served as authoritative. After one re-extraction pass the
    cache regenerates with the new schema."""
    monkeypatch.setattr(vdt, "extract_features", _stub_extract)
    grouped = _grouped(tmp_path, n_per_period=2)
    cache = tmp_path / "features.json"
    # Plant a pre-fix cache: features without text_hash/version.
    paths = [e.path for entries in grouped.values() for e in entries]
    pre_cache = {
        str(p.resolve()): _stub_extract("word")  # no compat fields
        for p in paths
    }
    vdt._save_feature_cache(cache, pre_cache)
    extract_count = {"n": 0}

    def _counting_extract(text: str) -> dict:
        extract_count["n"] += 1
        return _stub_extract(text)

    monkeypatch.setattr(vdt, "extract_features", _counting_extract)
    vdt.build_period_profiles(
        grouped, feature_cache_path=cache, flush_every=10,
    )
    # Pre-fix entries don't have text_hash or features_version,
    # so all 4 are re-extracted.
    assert extract_count["n"] == 4
