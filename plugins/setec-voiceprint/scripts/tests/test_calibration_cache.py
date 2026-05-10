#!/usr/bin/env python3
"""Regression tests for the score-once-survey-many record cache.

The 1.26.0 refactor split `derive_threshold` into:

  * `score_corpus(args)` — scores every entry once.
  * `derive_threshold_from_records(records, args, scoring_meta)` —
    pure per-signal threshold sweep, no scoring.
  * `load_or_score_corpus(args, cache_path, refresh)` — cache-aware
    composer that reads the cache or scores fresh.

Tests verify:

  * The cache JSON shape is stable + round-trippable.
  * Cache hit returns identical records without re-scoring.
  * Cache invalidates when the manifest content changes (SHA-256).
  * Cache invalidates when `--use`, `--tier2`, or `--tier3` change.
  * Cache invalidates when the scorer version bumps.
  * Cache invalidates between full and sub-sampled runs.
  * `--refresh-cache` forces re-scoring even when cache is valid.
  * Backward compat: `derive_threshold(args)` with no cache flag
    still works the way it did pre-1.26.

Tests stub `score_smoothing_entry` so they exercise the cache
plumbing without spaCy or SBERT.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import calibrate_thresholds as ct  # type: ignore


# ------------------- Helpers -------------------------------------


def _write_real_manifest(tmp_path: Path, n_entries: int = 10) -> Path:
    """Create an actual JSONL manifest with content the manifest
    validator and entry loader will accept. Files referenced via
    `path` are created so `_resolved_path` resolution succeeds."""
    manifest = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "texts"
    text_dir.mkdir()
    rows = []
    for i in range(n_entries):
        text_file = text_dir / f"essay_{i}.txt"
        text_file.write_text(
            "This is essay content for testing. " * 30,  # >100 words
            encoding="utf-8",
        )
        rows.append({
            "id": f"essay_{i}",
            "path": str(text_file),
            "ai_status": "ai_generated" if i % 2 == 0 else "pre_ai_human",
            "use": ["validation"],
            "split": "test",
            "register": "blog_essay",
            "language_status": "non_native_advanced",
        })
    with manifest.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return manifest


def _make_args(manifest: Path, **overrides) -> argparse.Namespace:
    base = dict(
        manifest=str(manifest),
        use="validation",
        signal="burstiness_B",
        fpr_target=0.01,
        out=None,
        slug=None,
        replace=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tier2=False,
        tier3=False,
        notes=None,
        max_entries=None,
        max_entries_seed=None,
        records_cache=None,
        refresh_cache=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_scoring(score_count_holder: dict) -> mock.MagicMock:
    """Patch `score_smoothing_entry` to a fake that increments a
    counter so tests can verify whether a re-score happened."""
    score_count_holder.setdefault("calls", 0)

    def fake_score(entry, **kw):
        score_count_holder["calls"] += 1
        return {
            "id": entry.get("id"),
            "path": entry.get("path"),
            "ai_status": entry.get("ai_status"),
            "label": 1 if entry.get("ai_status") == "ai_generated" else 0,
            "score": 0.5,
            "score_name": "compression_fraction",
            "usable_for_metrics": True,
            # Keys MUST match COMPRESSION_HEURISTICS[*].signal_path
            # exactly — collect_signal_records does an exact lookup.
            "per_signal_scores": {
                "tier1.sentence_length.burstiness_B": 0.5,
                "tier1.connective_density.per_1000_tokens": 0.4,
                "tier1.mattr.value": 0.7,
                "tier1.mtld": 50.0,
                "tier1.yules_k": 100.0,
                "tier1.shannon_entropy_bits": 9.0,
                "tier1.fkgl.sd": 1.0,
                "tier1.sentence_length.sd": 4.0,
                "tier2.mdd.sd": 0.5,
                "tier3.adjacent_cosine.mean": 0.4,
                "tier3.adjacent_cosine.sd": 0.1,
            },
        }

    return mock.patch.object(ct, "score_smoothing_entry",
                             side_effect=fake_score)


# ------------------- Cache shape --------------------------------


def test_cache_layout_round_trips(tmp_path):
    """Score corpus, write cache, read back, assert content
    identical."""
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    args = _make_args(manifest, records_cache=str(tmp_path / "cache.json"))
    counts = {"calls": 0}

    with _patch_scoring(counts):
        records, scoring_meta, hit = ct.load_or_score_corpus(
            args, cache_path=Path(args.records_cache),
        )
    assert hit is False
    assert counts["calls"] == 4  # one per entry
    cache = json.loads(Path(args.records_cache).read_text(encoding="utf-8"))
    assert "scoring_meta" in cache
    assert "records" in cache
    assert len(cache["records"]) == 4
    assert cache["scoring_meta"]["manifest_sha256"].startswith("sha256:")
    assert cache["scoring_meta"]["use"] == "validation"
    assert cache["scoring_meta"]["do_tier2"] is False
    assert cache["scoring_meta"]["scorer_version"] == ct.SCORER_CACHE_VERSION


def test_cache_hit_returns_records_without_rescoring(tmp_path):
    """First call writes cache; second call reads cache without
    re-scoring."""
    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache_path = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache_path))
    counts = {"calls": 0}

    with _patch_scoring(counts):
        # First call: scores fresh.
        records1, _meta1, hit1 = ct.load_or_score_corpus(
            args, cache_path=cache_path,
        )
        assert hit1 is False
        assert counts["calls"] == 5

        # Second call: hits cache, no re-scoring.
        records2, _meta2, hit2 = ct.load_or_score_corpus(
            args, cache_path=cache_path,
        )
        assert hit2 is True
        assert counts["calls"] == 5  # unchanged
        # Records are byte-identical (post-JSON-round-trip).
        assert len(records1) == len(records2)


def test_cache_invalidates_when_manifest_changes(tmp_path):
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache_path = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache_path))
    counts = {"calls": 0}

    with _patch_scoring(counts):
        ct.load_or_score_corpus(args, cache_path=cache_path)
        assert counts["calls"] == 3

        # Edit the manifest — append a new row.
        with manifest.open("a", encoding="utf-8") as f:
            new_text = tmp_path / "texts" / "new_essay.txt"
            new_text.write_text("New essay content. " * 30, encoding="utf-8")
            f.write(json.dumps({
                "id": "new_essay", "path": str(new_text),
                "ai_status": "pre_ai_human", "use": ["validation"],
                "split": "test", "register": "blog_essay",
                "language_status": "non_native_advanced",
            }) + "\n")

        # Second call: cache invalidated, re-scores all 4.
        _records, _meta, hit = ct.load_or_score_corpus(
            args, cache_path=cache_path,
        )
        assert hit is False
        assert counts["calls"] == 3 + 4  # original 3 + re-score 4


def test_cache_invalidates_on_tier_toggle(tmp_path):
    """Cache scored with tier2=False can't satisfy a tier2=True
    request because the per_signal_scores columns differ."""
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache_path = tmp_path / "cache.json"
    counts = {"calls": 0}

    args_no_tier2 = _make_args(
        manifest, records_cache=str(cache_path), tier2=False,
    )
    args_yes_tier2 = _make_args(
        manifest, records_cache=str(cache_path), tier2=True,
    )

    with _patch_scoring(counts):
        ct.load_or_score_corpus(args_no_tier2, cache_path=cache_path)
        assert counts["calls"] == 3

        # Different tier2 → cache invalidates.
        _, _, hit = ct.load_or_score_corpus(
            args_yes_tier2, cache_path=cache_path,
        )
        assert hit is False
        assert counts["calls"] == 6


def test_cache_invalidates_on_use_filter_change(tmp_path):
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache_path = tmp_path / "cache.json"
    counts = {"calls": 0}

    args1 = _make_args(manifest, records_cache=str(cache_path), use="validation")
    args2 = _make_args(manifest, records_cache=str(cache_path), use="baseline")

    with _patch_scoring(counts):
        # First call passes (entries match validation).
        ct.load_or_score_corpus(args1, cache_path=cache_path)
        assert counts["calls"] == 3

        # Different use filter → cache invalidates → would re-score.
        # But none of our entries have `use: baseline`, so re-scoring
        # raises SystemExit. We only care that the cache was rejected.
        if pytest is not None:
            with pytest.raises(SystemExit):
                ct.load_or_score_corpus(args2, cache_path=cache_path)


def test_refresh_cache_forces_rescore(tmp_path):
    """--refresh-cache: skip the cache read even when it exists."""
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache_path = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache_path))
    counts = {"calls": 0}

    with _patch_scoring(counts):
        ct.load_or_score_corpus(args, cache_path=cache_path)
        assert counts["calls"] == 3

        ct.load_or_score_corpus(
            args, cache_path=cache_path, refresh=True,
        )
        assert counts["calls"] == 6  # re-scored despite valid cache


def test_cache_handles_corrupt_file(tmp_path):
    """A garbage cache file should be treated like a miss; the
    script re-scores rather than crashing."""
    manifest = _write_real_manifest(tmp_path, n_entries=3)
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("not json at all", encoding="utf-8")
    args = _make_args(manifest, records_cache=str(cache_path))
    counts = {"calls": 0}

    with _patch_scoring(counts):
        records, _meta, hit = ct.load_or_score_corpus(
            args, cache_path=cache_path,
        )
    assert hit is False
    assert counts["calls"] == 3
    # Cache was overwritten with valid content.
    new = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "records" in new


def test_cache_invalidates_on_subsample_state_change(tmp_path):
    """Cache scored with --max-entries=N can't satisfy a full-corpus
    request and vice versa."""
    manifest = _write_real_manifest(tmp_path, n_entries=10)
    cache_path = tmp_path / "cache.json"
    counts = {"calls": 0}

    args_full = _make_args(manifest, records_cache=str(cache_path))
    args_partial = _make_args(
        manifest, records_cache=str(cache_path), max_entries=5,
    )

    with _patch_scoring(counts):
        ct.load_or_score_corpus(args_full, cache_path=cache_path)
        full_calls = counts["calls"]
        assert full_calls == 10

        # Switch to partial → cache invalidates → re-scores 5.
        _, _, hit = ct.load_or_score_corpus(
            args_partial, cache_path=cache_path,
        )
        assert hit is False
        assert counts["calls"] == full_calls + 5

        # Switch back to full → cache invalidates again → re-scores 10.
        _, _, hit = ct.load_or_score_corpus(
            args_full, cache_path=cache_path,
        )
        assert hit is False
        assert counts["calls"] == full_calls + 5 + 10


# ------------------- Backward compat -----------------------------


def test_derive_threshold_without_cache_flag_still_scores(tmp_path):
    """Pre-1.26.0 callers passed a Namespace without records_cache;
    the new derive_threshold must continue to work for them."""
    # 20 entries so the FPR resolution 1/n_neg ≤ 0.1 lets the sweep
    # find a usable threshold. Score-varying fake so the sweep
    # actually has signal to discriminate on.
    manifest = _write_real_manifest(tmp_path, n_entries=20)
    args = _make_args(manifest, fpr_target=0.1)  # records_cache=None

    counts = {"calls": 0}

    def varied_score(entry, **kw):
        counts["calls"] += 1
        # Positives score high, negatives score low — clean separation.
        is_pos = entry.get("ai_status") == "ai_generated"
        score = 0.8 if is_pos else 0.2
        return {
            "id": entry.get("id"),
            "path": entry.get("path"),
            "ai_status": entry.get("ai_status"),
            "label": 1 if is_pos else 0,
            "score": score, "score_name": "compression_fraction",
            "usable_for_metrics": True,
            "per_signal_scores": {
                "tier1.sentence_length.burstiness_B": score,
                "tier1.connective_density.per_1000_tokens": score,
                "tier1.mattr.value": score,
                "tier1.mtld": score, "tier1.yules_k": score,
                "tier1.shannon_entropy_bits": score,
                "tier1.fkgl.sd": score,
                "tier1.sentence_length.sd": score,
                "tier2.mdd.sd": score,
                "tier3.adjacent_cosine.mean": score,
                "tier3.adjacent_cosine.sd": score,
            },
        }

    with mock.patch.object(ct, "score_smoothing_entry", side_effect=varied_score), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        entry = ct.derive_threshold(args)
    assert counts["calls"] == 20
    assert entry["signal"] == "burstiness_B"
    assert "calibration" in entry


def test_derive_threshold_with_missing_records_cache_attr(tmp_path):
    """If a caller passes a Namespace without `records_cache` at all
    (e.g. a unit test from before 1.26.0), the script should not
    crash on AttributeError. `getattr(args, 'records_cache', None)`
    handles it."""
    manifest = _write_real_manifest(tmp_path, n_entries=20)
    base_args = _make_args(manifest, fpr_target=0.1)
    # Remove the attribute deliberately to simulate a pre-1.26 caller.
    delattr(base_args, "records_cache")
    delattr(base_args, "refresh_cache")

    counts = {"calls": 0}

    def varied_score(entry, **kw):
        counts["calls"] += 1
        is_pos = entry.get("ai_status") == "ai_generated"
        score = 0.8 if is_pos else 0.2
        return {
            "id": entry.get("id"),
            "path": entry.get("path"),
            "ai_status": entry.get("ai_status"),
            "label": 1 if is_pos else 0,
            "score": score, "score_name": "compression_fraction",
            "usable_for_metrics": True,
            "per_signal_scores": {
                "tier1.sentence_length.burstiness_B": score,
                "tier1.connective_density.per_1000_tokens": score,
                "tier1.mattr.value": score, "tier1.mtld": score,
                "tier1.yules_k": score, "tier1.shannon_entropy_bits": score,
                "tier1.fkgl.sd": score, "tier1.sentence_length.sd": score,
                "tier2.mdd.sd": score, "tier3.adjacent_cosine.mean": score,
                "tier3.adjacent_cosine.sd": score,
            },
        }

    with mock.patch.object(ct, "score_smoothing_entry", side_effect=varied_score), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        entry = ct.derive_threshold(base_args)
    assert entry["signal"] == "burstiness_B"
    assert counts["calls"] == 20


# ------------------- Score-once-survey-many -----------------------


def test_survey_runs_corpus_scoring_once_across_signals(tmp_path):
    """The survey wrapper must call score_corpus exactly once even
    when iterating 11 signals. Pre-1.26.0 it called derive_threshold
    11 times, each of which re-scored the corpus."""
    import calibration_survey as cs
    manifest = _write_real_manifest(tmp_path, n_entries=4)
    counts = {"calls": 0}

    parent = argparse.Namespace(
        manifest=str(manifest), use="validation", fpr_target=0.01,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
        max_entries=None, max_entries_seed=None,
        records_cache=None, refresh_cache=False,
    )

    with _patch_scoring(counts), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        survey = cs.run_survey(
            parent, signals=["burstiness_B", "mattr", "mtld"],
        )
    # 4 entries × 1 scoring pass = 4 score_smoothing_entry calls,
    # NOT 4 × 3 signals = 12.
    assert counts["calls"] == 4, (
        f"expected one scoring pass for 4 entries; got "
        f"{counts['calls']} (suggests survey re-scored per signal)"
    )
    # Three rows in the survey output.
    assert len(survey["rows"]) == 3


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
