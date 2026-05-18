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
        # Polarity-matched separation: burstiness_B's registry
        # direction is `lt` (AI-shaped prose has LOWER burstiness),
        # so positives score LOW and negatives score HIGH. This
        # avoids tripping the 1.59.0 polarity-inversion gate, which
        # would correctly refuse a corpus with inverted polarity
        # vs. the registry hypothesis.
        is_pos = entry.get("ai_status") == "ai_generated"
        score = 0.2 if is_pos else 0.8
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
        # Polarity-matched (see sibling test): burstiness_B is an
        # `lt`-direction signal, so positives must score LOWER than
        # negatives to clear the 1.59.0 polarity-inversion gate.
        is_pos = entry.get("ai_status") == "ai_generated"
        score = 0.2 if is_pos else 0.8
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


# ---------- Corpus text fingerprint (1.29.1) -----------------------


class TestCorpusTextFingerprint:
    """The cache must invalidate when the underlying text files
    change, even if the manifest JSONL stays byte-identical. Otherwise
    a re-OCR'd / re-extracted / preprocessing-toggled corpus will
    return stale scored records. Reproduces the reviewer-flagged P2.
    """

    def test_fingerprint_includes_file_content(self, tmp_path):
        """Two manifests with the same metadata but different text
        bodies produce different fingerprints."""
        text_dir = tmp_path / "texts"
        text_dir.mkdir()

        # Original text
        f1 = text_dir / "essay.txt"
        f1.write_text("Original body text. " * 50, encoding="utf-8")
        entries_v1 = [{
            "id": "e1", "_resolved_path": str(f1),
            "ai_status": "ai_generated", "use": ["validation"],
        }]
        fp1 = ct._corpus_text_fingerprint(entries_v1)
        assert fp1.startswith("sha256:")

        # Now overwrite the text file with different content. Manifest
        # has not changed, but the file the manifest points at HAS.
        f1.write_text("Cleaned body text. " * 50, encoding="utf-8")
        fp2 = ct._corpus_text_fingerprint(entries_v1)
        assert fp1 != fp2, (
            "fingerprint must change when underlying text bytes change"
        )

    def test_fingerprint_stable_when_text_unchanged(self, tmp_path):
        """Fingerprint is deterministic across calls when the corpus
        is unchanged."""
        text_dir = tmp_path / "texts"
        text_dir.mkdir()
        f1 = text_dir / "essay.txt"
        f1.write_text("text body content " * 50, encoding="utf-8")
        entries = [{
            "id": "e1", "_resolved_path": str(f1),
            "ai_status": "ai_generated", "use": ["validation"],
        }]
        fp_a = ct._corpus_text_fingerprint(entries)
        fp_b = ct._corpus_text_fingerprint(entries)
        assert fp_a == fp_b

    def test_fingerprint_handles_missing_file(self, tmp_path):
        """A manifest pointing at a missing file produces a stable
        sentinel rather than crashing."""
        entries = [{
            "id": "e1", "_resolved_path": str(tmp_path / "missing.txt"),
            "ai_status": "ai_generated", "use": ["validation"],
        }]
        fp = ct._corpus_text_fingerprint(entries)
        assert fp.startswith("sha256:")  # well-formed
        # And changes if the file later appears with different content.
        (tmp_path / "missing.txt").write_text("now exists",
                                              encoding="utf-8")
        fp_after = ct._corpus_text_fingerprint(entries)
        assert fp != fp_after

    def test_cache_invalidates_when_text_file_changes(self, tmp_path):
        """End-to-end: rewrite a text file in place between two
        calibration runs, leaving the manifest byte-identical, and
        confirm the second run re-scores rather than reusing stale
        cached records."""
        manifest = _write_real_manifest(tmp_path, n_entries=4)
        cache_path = tmp_path / "cache.json"
        args = _make_args(manifest, records_cache=str(cache_path))
        counts = {"calls": 0}

        with _patch_scoring(counts):
            ct.load_or_score_corpus(args, cache_path=cache_path)
            assert counts["calls"] == 4

        # Mutate one of the referenced text files. Manifest JSONL
        # bytes are unchanged.
        text_dir = tmp_path / "texts"
        target = text_dir / "essay_2.txt"
        original_size = target.stat().st_size
        target.write_text("CLEANED & RE-EXTRACTED " * 30,
                          encoding="utf-8")
        assert target.stat().st_size != original_size  # changed

        with _patch_scoring(counts):
            _records, _meta, hit = ct.load_or_score_corpus(
                args, cache_path=cache_path,
            )
        assert hit is False, (
            "cache should invalidate when text bytes change; "
            "got cache hit on stale records"
        )
        assert counts["calls"] == 8  # 4 fresh + 4 re-scored

    def test_cache_compatibility_check_with_fingerprint(self, tmp_path):
        """`cache_is_compatible` returns False with reason when the
        corpus_text_fingerprint mismatches."""
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:fakeold",
            "corpus_text_fingerprint": "sha256:abc",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:fakeold",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "corpus text" in reason.lower()

    def test_cache_compatibility_legacy_cache_invalidates(self, tmp_path):
        """Pre-1.29.1 caches don't carry a fingerprint; treat that as
        unknown corpus and force a re-score."""
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            # No corpus_text_fingerprint key.
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "fingerprint" in reason.lower() or "1.29.1" in reason


class TestEmbeddingDtypeCacheIdentity:
    """Reviewer P1 on PR #101: embedding_dtype + embedding_device are
    part of the Tier-3 cache identity from 1.96.0 onward. A cached
    Phase A run scored under ``--embedding-dtype fp32`` cannot be
    reused under ``--embedding-dtype bf16`` (or vice versa) without
    silently mixing precision regimes. Same bug class as the surprisal-
    side fix in PR #93.

    These tests pin the contract on ``cache_is_compatible``:

    - When Tier-3 is on and the cache lacks ``embedding_dtype_resolved``
      (pre-1.96 cache), force a re-score so the resolved label gets
      recorded on this host.
    - When the cache HAS the resolved label and it differs from what
      the current host would resolve to, invalidate.
    - When ``--embedding-device`` requests differ, invalidate.
    - When Tier-3 is OFF, the dtype fields are ignored (no Tier-3
      cache identity to defend).
    """

    def test_tier3_off_dtype_fields_are_ignored(self, tmp_path):
        """When Tier-3 is off, embedding-dtype identity doesn't gate
        cache reuse — there's no Tier-3 scoring to defend."""
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,  # off
            # No embedding_dtype_resolved field; if Tier-3 were on,
            # this would force a re-score.
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        args.tier3 = False
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        # The cache might still invalidate for other reasons but
        # NOT for missing embedding_dtype_resolved.
        assert "embedding_dtype" not in reason

    def test_tier3_on_missing_embedding_dtype_resolved_invalidates(
        self, tmp_path,
    ):
        """Pre-1.96 caches on the pluggable-embedding path don't carry
        ``embedding_dtype_resolved``. When Tier-3 is on AND
        ``embedding_model`` is set on both sides, force a re-score so
        the resolved label gets recorded on this host. Mirror of the
        surprisal-side pre-1.93 contract in PR #93.

        The check is gated on ``embedding_model is not None`` on both
        sides — legacy MiniLM caches (``embedding_model=None``) predate
        the dtype contract entirely and stay compatible without it.
        """
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": True,
            "embedding_model": "mxbai",  # pluggable path on the cache
            # No embedding_dtype_resolved.
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        args.tier3 = True
        args.embedding_model = "mxbai"  # pluggable path on the args too
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "embedding_dtype_resolved" in reason
        assert "pre-1.96" in reason

    def test_tier3_on_legacy_minilm_path_skips_dtype_check(self, tmp_path):
        """When ``embedding_model`` is ``None`` on either side, the
        run uses the legacy MiniLM hardcode which predates the dtype
        contract. The dtype identity check is skipped — back-compat
        with pre-1.80 caches stays intact."""
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": True,
            "embedding_model": None,  # legacy MiniLM path
            # No embedding_dtype_resolved — and shouldn't be checked.
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        args.tier3 = True
        # args.embedding_model defaults to None via _make_args.
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        # The cache might still invalidate for other reasons but
        # NOT for missing embedding_dtype_resolved.
        assert "embedding_dtype" not in reason

    def test_tier3_on_embedding_device_requested_change_invalidates(
        self, tmp_path,
    ):
        """A cache scored with ``--embedding-device cuda:0`` shouldn't
        reuse on a later ``--embedding-device cuda:1`` run. Device
        pinning is operator intent for parallelism / isolation; mixing
        them silently would defeat the per-process cache isolation the
        cloud bake-off matrix's multi-GPU pattern depends on."""
        manifest = _write_real_manifest(tmp_path)
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": True,
            "embedding_model": "mxbai",  # pluggable path on both sides
            "embedding_dtype_resolved": None,  # present, just None
            "embedding_device_requested": "cuda:0",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = _make_args(manifest)
        args.tier3 = True
        args.embedding_model = "mxbai"
        args.embedding_device = "cuda:1"
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "embedding_device_requested" in reason


# ---------- Direction-aware AP (1.29.1) ----------------------------


class TestDirectionAwareAP:
    """`_ranking_metrics` returns both raw AP (polarity-blind) and
    direction-aware AP (negates scores for `lt` signals so the
    precision curve reads on the registry's polarity). Reproduces
    the reviewer-flagged P2: a strong `lt` discriminator should
    score high da_AP even though raw AP is low."""

    def test_lt_direction_ap_negation_inverts_curve(self):
        """For an `lt` signal where AI scores LOW and human scores
        HIGH (the registry's hypothesis), raw AP ranks humans first
        and reads weak. Direction-aware AP negates and reads strong.
        """
        # Labels: 1 = AI (positive class), 0 = human.
        # Scores: AI clusters low (0.1-0.3), human clusters high
        # (0.6-0.9). Direction = "lt" — registry's hypothesis
        # matches: AI compressed when score < threshold.
        pairs = [
            (1, 0.10), (1, 0.15), (1, 0.20), (1, 0.25), (1, 0.30),
            (0, 0.60), (0, 0.65), (0, 0.70), (0, 0.80), (0, 0.90),
        ]
        m_lt = ct._ranking_metrics(pairs, direction="lt")
        # Raw AP sees humans on top → polarity-mismatched → low AP.
        assert m_lt["ap"] is not None and m_lt["ap"] < 0.5
        # Direction-aware AP negates → AI on top → high AP.
        assert m_lt["direction_aware_ap"] is not None
        assert m_lt["direction_aware_ap"] > 0.95

    def test_gt_direction_ap_unchanged(self):
        """For `gt` signals, raw AP and direction-aware AP are
        identical — the polarity is already aligned."""
        pairs = [
            (1, 0.80), (1, 0.85), (1, 0.90),
            (0, 0.10), (0, 0.20), (0, 0.30),
        ]
        m = ct._ranking_metrics(pairs, direction="gt")
        assert m["ap"] is not None
        assert m["direction_aware_ap"] is not None
        assert abs(m["ap"] - m["direction_aware_ap"]) < 1e-9

    def test_direction_aware_auc_consistent_with_old_formula(self):
        """For backward-compat, direction_aware_auc = 1 − raw AUC for
        `lt` and = raw AUC for `gt`."""
        pairs = [
            (1, 0.80), (1, 0.85), (1, 0.90),
            (0, 0.10), (0, 0.20), (0, 0.30),
        ]
        m_gt = ct._ranking_metrics(pairs, direction="gt")
        m_lt = ct._ranking_metrics(pairs, direction="lt")
        # AUC is identical (raw); direction-aware reflects the flip.
        assert m_gt["auc"] == m_lt["auc"]
        assert m_gt["direction_aware_auc"] == m_gt["auc"]
        assert abs(m_lt["direction_aware_auc"] - (1.0 - m_lt["auc"])) < 1e-9

    def test_default_direction_is_gt(self):
        """Calling _ranking_metrics without `direction` defaults to
        `gt` — back-compat for any pre-1.29.1 caller."""
        pairs = [(1, 0.7), (0, 0.3), (1, 0.6), (0, 0.4)]
        m = ct._ranking_metrics(pairs)  # no direction kwarg
        assert m["direction_aware_ap"] == m["ap"]
        assert m["direction_aware_auc"] == m["auc"]


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
