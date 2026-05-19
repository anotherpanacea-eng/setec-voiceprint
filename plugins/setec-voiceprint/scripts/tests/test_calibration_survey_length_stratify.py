#!/usr/bin/env python3
"""Length-stratified manifest subsampling for calibration_survey.py.

Roadmap item E.3 / post-1.101 follow-up: adds ``--length-stratify N
--length-buckets B --length-stratify-floor M`` to ``calibration_survey
.py``. The spec calls for proportional-with-floor sampling across
percentile-defined length buckets BEFORE scoring, so cloud-scale
calibration runs against 500K-6M entry corpora (MAGE / RAID) cover
the full length distribution rather than randomly under-weighting
the heavy tail.

Tests cover:

  * Bucket-bound math (percentile cut-points, edge cases).
  * Bucket assignment (digitize semantics).
  * End-to-end length-stratified subsampling: floors enforced,
    proportional otherwise, deterministic across re-runs with the
    same seed.
  * Composition with the existing label-stratified subsample
    (``--max-entries``): both contracts hold.
  * Manifest-write round-trip: the temp manifest is a clean,
    schema-compliant subset of the original.
  * Help text exposes the new flags.

The tests use small synthetic manifests (50-200 entries) with
``word_count`` populated so no file I/O is required.
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

import calibration_survey as cs  # type: ignore


# ------------------- Helpers ------------------------------------


def _make_entry(
    idx: int,
    *,
    word_count: int,
    ai_status: str = "pre_ai_human",
    use: str = "validation",
) -> dict:
    return {
        "id": f"entry_{idx:04d}",
        "path": f"texts/entry_{idx:04d}.txt",
        "ai_status": ai_status,
        "use": [use],
        "word_count": word_count,
    }


def _make_manifest(
    word_counts: list[int],
    *,
    statuses: list[str] | None = None,
) -> list[dict]:
    """Build a synthetic manifest with the given per-entry word counts.

    If ``statuses`` is None, half the entries are ``pre_ai_human`` and
    half ``ai_generated`` (alternating) so label-stratified subsample
    has both labels to work with.
    """
    if statuses is None:
        statuses = [
            "pre_ai_human" if i % 2 == 0 else "ai_generated"
            for i in range(len(word_counts))
        ]
    return [
        _make_entry(i, word_count=wc, ai_status=st)
        for i, (wc, st) in enumerate(zip(word_counts, statuses))
    ]


# ------------------- Bucket bounds ------------------------------


def test_percentile_bounds_five_buckets_uniform():
    """Five buckets over a uniform [0, 99] distribution split at the
    20/40/60/80 percentiles."""
    values = list(range(100))
    bounds = cs._percentile_bounds(values, 5)
    assert len(bounds) == 4
    # Linear interpolation: (n-1)*0.2 = 19.8, so cut[0] ≈ 19.8.
    assert abs(bounds[0] - 19.8) < 0.5
    assert abs(bounds[1] - 39.6) < 0.5
    assert abs(bounds[2] - 59.4) < 0.5
    assert abs(bounds[3] - 79.2) < 0.5


def test_percentile_bounds_one_bucket_returns_empty():
    """``n_buckets=1`` is the degenerate case: everything in one
    bucket, no cut-points needed."""
    assert cs._percentile_bounds(list(range(100)), 1) == []


def test_percentile_bounds_empty_values_returns_empty():
    assert cs._percentile_bounds([], 5) == []


def test_percentile_bounds_all_same_value():
    """Pathological case: every entry has the same length. All
    cut-points collapse to that value; downstream bucket assignment
    will put everything in bucket 0 (and the floor logic compensates)."""
    bounds = cs._percentile_bounds([42] * 100, 5)
    assert all(b == 42 for b in bounds)


# ------------------- Bucket assignment --------------------------


def test_assign_bucket_basic():
    bounds = [20.0, 40.0, 60.0, 80.0]
    assert cs._assign_bucket(10, bounds) == 0
    assert cs._assign_bucket(25, bounds) == 1
    assert cs._assign_bucket(45, bounds) == 2
    assert cs._assign_bucket(65, bounds) == 3
    assert cs._assign_bucket(99, bounds) == 4


def test_assign_bucket_on_boundary_goes_lower():
    """Lengths equal to a boundary land in the lower bucket
    (digitize(right=False) semantics)."""
    bounds = [20.0, 40.0]
    # 20 not < 20, so goes to bucket 1; 40 not < 40 either, bucket 2.
    assert cs._assign_bucket(20, bounds) == 1
    assert cs._assign_bucket(40, bounds) == 2


def test_assign_bucket_no_bounds_returns_zero():
    """One-bucket case: everything → bucket 0."""
    assert cs._assign_bucket(123, []) == 0


# ------------------- Length stratification ----------------------


def test_length_stratify_basic_proportional():
    """100 entries uniformly distributed in length, cap=50, B=5,
    floor=0 → each bucket samples ~10. Proportional with no
    floor adjustment needed."""
    word_counts = list(range(100, 200))  # 100..199 words
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=0, seed=42,
    )
    assert meta["applied"]
    assert meta["n_full"] == 100
    assert meta["n_used"] == len(sampled)
    # Roughly proportional: 50 cap / 5 buckets = 10 per bucket.
    for count in meta["bucket_sample_counts"].values():
        assert 8 <= count <= 12


def test_length_stratify_floor_enforced_on_tiny_tail():
    """A heavy-tailed distribution: 95 short entries + 5 long ones.
    With B=2 and floor=3, the tail bucket gets 3 (not 0 from
    proportional rounding)."""
    word_counts = [10] * 95 + [10000] * 5
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=20, n_buckets=2, floor=3, seed=42,
    )
    # Bucket 1 (the tail) should get at least its floor.
    assert int(meta["bucket_sample_counts"]["1"]) >= 3


def test_length_stratify_floor_caps_at_bucket_population():
    """A bucket with 2 entries and floor=5 takes both — never
    oversamples beyond what's available."""
    # Two distinct length values so percentile splitter creates a
    # real boundary; the smaller bucket has 2 entries.
    word_counts = [100] * 98 + [9999] * 2
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=5, seed=42,
    )
    # Whichever bucket holds the 2 outliers shouldn't have sample
    # count > 2 even though the floor asks for 5.
    for b_idx, pop in meta["bucket_populations"].items():
        if pop == 2:
            assert int(meta["bucket_sample_counts"][b_idx]) == 2


def test_length_stratify_determinism_same_seed():
    """Same (entries, cap, B, floor, seed) → byte-identical sample."""
    word_counts = list(range(100, 300))
    entries = _make_manifest(word_counts)
    s1, m1 = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=2, seed=42,
    )
    s2, m2 = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=2, seed=42,
    )
    assert [e["id"] for e in s1] == [e["id"] for e in s2]
    assert m1["bucket_sample_counts"] == m2["bucket_sample_counts"]


def test_length_stratify_different_seeds_pick_different_samples():
    """Different seed → different sample (with high probability for
    sample sizes well below population)."""
    word_counts = list(range(100, 300))
    entries = _make_manifest(word_counts)
    s1, _ = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=2, seed=1,
    )
    s2, _ = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=2, seed=999,
    )
    assert [e["id"] for e in s1] != [e["id"] for e in s2]


def test_length_stratify_cap_greater_than_corpus_returns_all():
    """``cap >= n_entries`` → no subsampling (returns all + applied=False)."""
    word_counts = list(range(50))
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=100, n_buckets=5, floor=1, seed=42,
    )
    assert len(sampled) == 50
    assert meta["applied"] is False


def test_length_stratify_one_bucket_takes_uniform_sample():
    """B=1: the degenerate case. Every entry lands in bucket 0;
    sample size == cap."""
    word_counts = list(range(100))
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=20, n_buckets=1, floor=1, seed=42,
    )
    assert len(sampled) == 20
    assert meta["bucket_populations"] == {"0": 100}
    assert meta["bucket_sample_counts"] == {"0": 20}


def test_length_stratify_all_same_length():
    """Pathological: every entry has identical length. Bucket bounds
    collapse; everything piles into bucket 0 (the lowest-index match
    under digitize semantics), and the floor logic ensures the cap
    is met."""
    entries = _make_manifest([42] * 50)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=20, n_buckets=5, floor=1, seed=42,
    )
    # All entries should be in one bucket (the one that contains 42).
    populations = list(meta["bucket_populations"].values())
    assert max(populations) == 50
    # Sample total = cap = 20.
    assert sum(meta["bucket_sample_counts"].values()) == 20


def test_length_stratify_cap_zero_returns_empty():
    """``cap=0`` → empty sample with no buckets populated.

    The current implementation treats cap=0 as a special case: the
    surplus-scaling math forces every effective floor to 0 so no
    entries are taken. This is the expected user contract — passing
    --length-stratify 0 should produce nothing.
    """
    entries = _make_manifest(list(range(50)))
    sampled, meta = cs._length_stratify_entries(
        entries, cap=0, n_buckets=5, floor=0, seed=42,
    )
    assert len(sampled) == 0
    assert sum(meta["bucket_sample_counts"].values()) == 0


def test_length_stratify_total_matches_cap():
    """Floor + proportional logic must always sum to cap (or to the
    total population if cap > pop). Spot-checks the reconciliation
    math."""
    word_counts = list(range(1, 501))  # 500 entries, all unique
    entries = _make_manifest(word_counts)
    for cap in (50, 100, 250, 400):
        sampled, meta = cs._length_stratify_entries(
            entries, cap=cap, n_buckets=5, floor=5, seed=42,
        )
        assert len(sampled) == cap, (
            f"cap={cap}: got {len(sampled)}, expected {cap}"
        )
        assert sum(meta["bucket_sample_counts"].values()) == cap


def test_length_stratify_floor_relaxes_when_floor_x_buckets_exceeds_cap():
    """Reviewer P1: when ``floor * nonempty_buckets > cap`` every
    bucket sits at its effective floor, ``total_surplus`` is zero,
    and the original over-budget reconciliation branch couldn't
    reduce anything — so the function returned more entries than
    the operator requested. The contract is ``n_used <= cap``;
    deterministically relax the floor instead.

    Concrete: 50 entries, cap=2, B=5, floor=1 → previously returned
    5 sampled entries (one per bucket at its floor); should now
    return 2 with ``floor_relaxed=True``.
    """
    word_counts = list(range(1, 51))  # 50 entries with distinct lengths
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=2, n_buckets=5, floor=1, seed=42,
    )
    assert len(sampled) == 2, (
        f"cap=2 contract: n_used={len(sampled)} exceeds cap; "
        f"floor reconciliation didn't relax under-budget floors"
    )
    assert meta["n_used"] == 2
    assert meta["floor_relaxed"] is True, (
        "operator-visible signal that the requested floor was over-budget"
    )
    # Per-bucket sample counts must still sum to cap.
    assert sum(meta["bucket_sample_counts"].values()) == 2


def test_length_stratify_floor_relaxed_false_when_cap_honored():
    """Normal case: cap=50, B=5, floor=5 (proportional fits cleanly)
    → floor_relaxed=False. Pin: the diagnostic field is False under
    standard configurations so an operator grepping for True in the
    survey JSON only catches the unusual relaxation case.
    """
    word_counts = list(range(1, 501))  # 500 entries
    entries = _make_manifest(word_counts)
    sampled, meta = cs._length_stratify_entries(
        entries, cap=50, n_buckets=5, floor=5, seed=42,
    )
    assert len(sampled) == 50
    assert meta["floor_relaxed"] is False


# ------------------- Text length resolution ---------------------


def test_entry_text_length_prefers_word_count():
    """If ``word_count`` is present, no file I/O needed."""
    entry = {"word_count": 42, "path": "/nonexistent/file.txt"}
    assert cs._entry_text_length(entry) == 42


def test_entry_text_length_falls_back_to_file(tmp_path):
    """No word_count → read text from _resolved_path and count
    whitespace-split tokens."""
    f = tmp_path / "essay.txt"
    f.write_text("one two three four five", encoding="utf-8")
    entry = {"_resolved_path": str(f)}
    assert cs._entry_text_length(entry) == 5


def test_entry_text_length_returns_none_when_unresolvable():
    entry = {"path": "/no/such/file.txt"}
    assert cs._entry_text_length(entry) is None


# ------------------- End-to-end via apply_length_stratification --


def test_apply_length_stratification_writes_manifest(tmp_path):
    """End-to-end: write a small manifest, run apply_length_
    stratification, verify the temp manifest is a valid JSONL slice."""
    word_counts = list(range(50, 250))  # 200 entries
    entries = _make_manifest(word_counts)
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    args = argparse.Namespace(
        manifest=str(manifest),
        length_stratify=50,
        length_buckets=5,
        length_stratify_floor=2,
        max_entries_seed=42,
        bootstrap_seed=42,
    )
    new_manifest, meta = cs.apply_length_stratification(args)
    assert new_manifest is not None
    assert new_manifest.is_file()
    assert meta["applied"]
    assert meta["n_used"] == 50

    # The temp manifest is parseable JSONL with the right slice size.
    lines = [
        line for line in new_manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 50
    parsed = [json.loads(line) for line in lines]
    # Each entry has the required fields + no leaked private fields.
    for e in parsed:
        assert "id" in e
        assert "path" in e
        assert "ai_status" in e
        assert "use" in e
        for k in e:
            assert not k.startswith("_"), (
                f"Leaked private field {k} in written manifest"
            )


def test_apply_length_stratification_disabled_returns_none(tmp_path):
    """No ``--length-stratify`` → no subsampling, no temp manifest."""
    args = argparse.Namespace(
        manifest=str(tmp_path / "manifest.jsonl"),
        length_stratify=None,
        length_buckets=5,
        length_stratify_floor=None,
        max_entries_seed=42,
        bootstrap_seed=42,
    )
    new_manifest, meta = cs.apply_length_stratification(args)
    assert new_manifest is None
    assert meta is None


# ------------------- Composition with --max-entries -------------


def test_run_survey_composes_length_strat_with_max_entries(tmp_path):
    """Both flags active: length-stratify writes a 100-entry temp
    manifest, then --max-entries=20 label-stratifies it further.
    The downstream scoring path receives a manifest that satisfies
    both contracts.
    """
    word_counts = list(range(100, 300))  # 200 entries
    entries = _make_manifest(word_counts)
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    captured: dict = {}

    def fake_load_or_score(inner_args, *, cache_path, refresh):
        # Capture which manifest path was passed to the scorer.
        captured["manifest_seen_by_scorer"] = inner_args.manifest
        captured["max_entries"] = inner_args.max_entries
        # Read the temp manifest to verify it's the length-stratified slice.
        lines = [
            line for line in Path(inner_args.manifest)
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        captured["n_entries_in_temp_manifest"] = len(lines)
        return ([], {}, False)

    fake_entry = {
        "signal": "burstiness_B",
        "direction": "gt",
        "fpr_target": 0.01,
        "calibration": {
            "auc": 0.7, "ap": 0.6, "empirical_tpr": 0.4,
            "empirical_fpr": 0.009, "n_pos": 50, "n_neg": 50,
            "fpr_resolution": 0.005,
        },
        "derived_value": 0.5,
    }

    args = argparse.Namespace(
        manifest=str(manifest),
        use="validation",
        fpr_target=0.01,
        out=None,
        signal=[],
        tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tpr_floor=0.05,
        aggressiveness_tolerance=0.05,
        json_only=True,
        length_stratify=100,
        length_buckets=5,
        length_stratify_floor=5,
        max_entries=20,
        max_entries_seed=42,
    )

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           side_effect=fake_load_or_score), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        survey = cs.run_survey(args, signals=["burstiness_B"])

    # Length-stratify wrote a 100-entry temp manifest.
    assert captured["n_entries_in_temp_manifest"] == 100
    # The downstream scorer's inner args carried max_entries=20 so the
    # label-stratified subsample will trim further.
    assert captured["max_entries"] == 20
    # The survey's "manifest" field reports the ORIGINAL path (audit
    # trail); the length_stratified_manifest field reports the temp.
    assert survey["manifest"] == str(manifest)
    assert survey["length_stratified_manifest"] is not None
    # The length_stratify provenance block has bucket metadata.
    ls = survey["length_stratify"]
    assert ls["n_target"] == 100
    assert ls["n_used"] == 100
    assert ls["n_full"] == 200
    assert ls["n_buckets"] == 5
    assert ls["floor"] == 5
    assert len(ls["bucket_bounds"]) == 4
    assert sum(int(v) for v in ls["bucket_sample_counts"].values()) == 100


def test_run_survey_records_length_stratify_metadata(tmp_path):
    """Survey JSON's metadata block includes the audit trail an
    operator needs to reproduce the same sample."""
    word_counts = list(range(50, 250))
    entries = _make_manifest(word_counts)
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    fake_entry = {
        "signal": "burstiness_B",
        "direction": "gt",
        "fpr_target": 0.01,
        "calibration": {
            "auc": 0.7, "ap": 0.6, "empirical_tpr": 0.4,
            "empirical_fpr": 0.009, "n_pos": 50, "n_neg": 50,
            "fpr_resolution": 0.005,
        },
        "derived_value": 0.5,
    }

    args = argparse.Namespace(
        manifest=str(manifest),
        use="validation",
        fpr_target=0.01,
        out=None,
        signal=[],
        tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tpr_floor=0.05,
        aggressiveness_tolerance=0.05,
        json_only=True,
        length_stratify=80,
        length_buckets=4,
        length_stratify_floor=10,
        max_entries=None,
        max_entries_seed=7,
    )

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        survey = cs.run_survey(args, signals=["burstiness_B"])

    ls = survey["length_stratify"]
    assert ls["seed"] == 7  # honored --max-entries-seed
    assert ls["n_buckets"] == 4
    assert ls["floor"] == 10
    assert len(ls["bucket_bounds"]) == 3  # n_buckets - 1
    # The pipeline-check flag is set even without --max-entries
    # because length-stratify is a subsample (not a full-corpus run).
    assert survey["is_pipeline_check"] is True


def test_run_survey_without_length_stratify_emits_null_block(tmp_path):
    """When --length-stratify is unset, the survey's length_stratify
    field is None (no provenance, no audit trail required)."""
    fake_entry = {
        "signal": "burstiness_B", "direction": "gt", "fpr_target": 0.01,
        "calibration": {
            "auc": 0.7, "ap": 0.6, "empirical_tpr": 0.4,
            "empirical_fpr": 0.009, "n_pos": 50, "n_neg": 50,
            "fpr_resolution": 0.005,
        },
        "derived_value": 0.5,
    }
    args = argparse.Namespace(
        manifest="dummy.jsonl", use="validation", fpr_target=0.01,
        out=None, signal=[], tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tpr_floor=0.05,
        aggressiveness_tolerance=0.05, json_only=True,
        length_stratify=None, length_buckets=5,
        length_stratify_floor=None,
        max_entries=None, max_entries_seed=None,
    )
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        survey = cs.run_survey(args, signals=["burstiness_B"])
    assert survey["length_stratify"] is None


# ------------------- CLI surface --------------------------------


def test_cli_help_lists_length_stratify_flags():
    parser = cs.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--length-stratify", "--length-buckets",
        "--length-stratify-floor",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_cli_default_buckets_is_five():
    """B=5 is the spec default. Don't break with a silent override."""
    parser = cs.build_arg_parser()
    args = parser.parse_args([
        "--manifest", "dummy.jsonl",
        "--fpr-target", "0.01",
    ])
    assert args.length_buckets == 5
    assert args.length_stratify is None  # default off
    assert args.length_stratify_floor is None  # default formula resolved at runtime


def test_cli_invalid_n_buckets_raises(tmp_path):
    """``--length-buckets 0`` is a user error caught at runtime."""
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps(_make_entry(0, word_count=100)) + "\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        manifest=str(manifest),
        length_stratify=10,
        length_buckets=0,  # invalid
        length_stratify_floor=None,
        bootstrap_seed=42,
        max_entries_seed=None,
    )
    try:
        cs.apply_length_stratification(args)
    except SystemExit as exc:
        assert "length-buckets" in str(exc)
        return
    raise AssertionError("Expected SystemExit for --length-buckets 0")


def test_cli_nonpositive_length_stratify_raises(tmp_path):
    """Reviewer P2: ``--length-stratify 0`` (or a negative value)
    previously fell through to the ``return None, None`` branch,
    silently disabling sampling and letting a full-corpus run
    proceed. Reject explicitly with SystemExit so an operator who
    typed the wrong N gets a clean error rather than a multi-hour
    surprise on RAID/MAGE.
    """
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps(_make_entry(0, word_count=100)) + "\n",
        encoding="utf-8",
    )
    for bad_n in (0, -1, -1000):
        args = argparse.Namespace(
            manifest=str(manifest),
            length_stratify=bad_n,
            length_buckets=5,
            length_stratify_floor=None,
            bootstrap_seed=42,
            max_entries_seed=None,
        )
        try:
            cs.apply_length_stratification(args)
        except SystemExit as exc:
            assert "length-stratify" in str(exc)
            assert str(bad_n) in str(exc)
            continue
        raise AssertionError(
            f"Expected SystemExit for --length-stratify {bad_n}; "
            "nonpositive values must be rejected explicitly rather "
            "than silently disabling sampling"
        )


def test_run_survey_rejects_length_stratify_zero_on_cli_path(tmp_path):
    """Follow-up regression: the previous P2 fix raised SystemExit
    inside ``apply_length_stratification`` but ``run_survey`` only
    called into the validator when ``length_stratify`` was *truthy*.
    ``--length-stratify 0`` is falsy, so the validator was bypassed
    and the CLI proceeded to a full-corpus run. Fix changed the
    guard to ``is not None``; this test pins the contract through
    ``run_survey`` (the CLI path), not through the validator helper
    directly.
    """
    word_counts = list(range(100, 200))
    entries = _make_manifest(word_counts)
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    for bad_n in (0, -1):
        args = argparse.Namespace(
            manifest=str(manifest),
            use="validation",
            fpr_target=0.01,
            out=None,
            signal=[],
            tier2=False, tier3=False,
            bootstrap_resamples=10, bootstrap_confidence=0.95,
            bootstrap_seed=42,
            tpr_floor=0.05,
            aggressiveness_tolerance=0.05,
            json_only=True,
            length_stratify=bad_n,
            length_buckets=5,
            length_stratify_floor=None,
            max_entries=None,
            max_entries_seed=42,
        )
        try:
            # Patch the scorer in case the guard ever silently lets us
            # through — we don't want to actually run the full corpus.
            with mock.patch.object(cs.ct, "load_or_score_corpus",
                                   return_value=([], {}, False)):
                cs.run_survey(args, signals=["burstiness_B"])
        except SystemExit as exc:
            assert "length-stratify" in str(exc)
            assert str(bad_n) in str(exc)
            continue
        raise AssertionError(
            f"--length-stratify {bad_n} reached run_survey but did not "
            "raise SystemExit; the CLI path skipped the validator "
            "because the guard was truthiness-based instead of "
            "`is not None`-based"
        )


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
