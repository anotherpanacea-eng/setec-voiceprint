#!/usr/bin/env python3
"""Tests for the two bootstrap-CI engines in
``calibrate_thresholds.fixed_threshold_bootstrap_ci``.

The loop engine is the bit-exact reference for pre-1.60 ledger
entries; the numpy engine is the 50-200x-faster vectorized
implementation introduced in 1.60.0. These tests cover:

  - Identical return-dict schema across engines (so the
    aggregator and ledger consumers don't break when the engine
    swaps).
  - Statistical equivalence: CI bounds from both engines lie
    within Monte Carlo noise for 2000+ resamples on the same
    seed.
  - Engine-dispatch correctness: the ``engine=`` kwarg routes
    to the right implementation; unknown engines raise.
  - Edge cases: empty input, single-class input, n=1 trivial,
    threshold beyond observed scores.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

from calibrate_thresholds import (  # type: ignore  # noqa: E402
    _AUTO_CHUNK_MAX,
    _AUTO_CHUNK_MIN,
    _auto_chunk_size,
    _build_harness_command,
    _fixed_threshold_bootstrap_ci_loop,
    _fixed_threshold_bootstrap_ci_numpy,
    fixed_threshold_bootstrap_ci,
)


def _make_pairs(n: int, *, seed: int = 0) -> list[tuple[int, float]]:
    """Build a deterministic mock pair list. Half label=1 with
    scores normally clustered around 1.0, half label=0 around
    -1.0 — well-separated so per-resample stats are stable."""
    import random as _r
    rng = _r.Random(seed)
    pairs: list[tuple[int, float]] = []
    for i in range(n):
        label = 1 if i % 2 == 0 else 0
        # Center positives at +1, negatives at -1, both with
        # sigma=0.5. A threshold of 0.0 should give roughly
        # symmetric TPR/FPR.
        score = (1.0 if label == 1 else -1.0) + rng.gauss(0, 0.5)
        pairs.append((label, score))
    return pairs


# ----- Dispatcher behaviour -------------------------------------


class TestEngineDispatch:
    """The public ``fixed_threshold_bootstrap_ci`` dispatches on
    the ``engine`` kwarg. Default is ``"loop"`` for bit-exact
    backward compat."""

    def test_default_engine_is_loop(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
        )
        assert result is not None
        assert result["engine"] == "loop"

    def test_explicit_loop_engine(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="loop",
        )
        assert result is not None
        assert result["engine"] == "loop"

    def test_numpy_engine_returns_marker(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="numpy",
        )
        assert result is not None
        assert result["engine"] == "numpy"

    def test_unknown_engine_raises(self):
        pairs = _make_pairs(10)
        with pytest.raises(ValueError, match="Unknown bootstrap engine"):
            fixed_threshold_bootstrap_ci(
                pairs, threshold=0.0, direction="gt",
                resamples=10, confidence=0.95, seed=42,
                engine="cuda",
            )


# ----- Schema parity --------------------------------------------


class TestSchemaParity:
    """Both engines must return the same core dict-key set so the
    aggregator can consume either output. The vectorized engine
    carries one engine-specific extra (``chunk_size``) for
    ledger provenance — that's documented as an additive delta,
    not a renamed-or-removed key."""

    def test_loop_schema_is_subset_of_numpy(self):
        """Numpy returns a superset: same core keys + the
        engine-specific ``chunk_size`` for provenance."""
        pairs = _make_pairs(100)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
        )
        assert loop_out is not None
        assert numpy_out is not None
        # Loop's keys must all be present in numpy. Numpy adds
        # one documented key: ``chunk_size``.
        assert set(loop_out.keys()) <= set(numpy_out.keys())
        assert set(numpy_out.keys()) - set(loop_out.keys()) == {"chunk_size"}
        # The numpy chunk_size is a positive int, ready to be
        # persisted in the ledger.
        assert isinstance(numpy_out["chunk_size"], int)
        assert numpy_out["chunk_size"] >= 1

    def test_ci_field_shapes_match(self):
        pairs = _make_pairs(100)
        for engine in ("loop", "numpy"):
            result = fixed_threshold_bootstrap_ci(
                pairs, threshold=0.0, direction="gt",
                resamples=200, confidence=0.95, seed=42,
                engine=engine,
            )
            assert result is not None
            for key in ("tpr_ci", "fpr_ci", "precision_ci"):
                assert key in result
                assert isinstance(result[key], list)
                assert len(result[key]) == 2  # [lo, hi]
                assert result[key][0] <= result[key][1]


# ----- Statistical equivalence ---------------------------------


class TestStatisticalEquivalence:
    """The whole point of the vectorized engine is to be a
    drop-in replacement for the loop engine. Bit-exact agreement
    is impossible (different RNG streams), but CI bounds should
    converge to within Monte Carlo noise."""

    def test_ci_bounds_within_noise_for_well_separated_classes(self):
        # Well-separated classes (centered at +1 and -1, sigma 0.5)
        # mean both engines see effectively the same true TPR/FPR;
        # bootstrap-CI noise across 2000 resamples should give
        # bounds that agree to 3 significant figures.
        pairs = _make_pairs(500, seed=7)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=2000, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=2000, confidence=0.95, seed=42,
        )
        assert loop_out is not None and numpy_out is not None
        # Tolerance: each CI bound should agree to within 0.03
        # (3 percentage points) at this N and resample count.
        # Wider than strict Monte Carlo theory predicts because
        # bootstrap-CI tails can be noisy; 0.03 is generous but
        # still tight enough to catch implementation bugs.
        for key in ("tpr_ci", "fpr_ci", "precision_ci"):
            l_lo, l_hi = loop_out[key]
            n_lo, n_hi = numpy_out[key]
            assert abs(l_lo - n_lo) < 0.03, (
                f"{key}[0]: loop={l_lo} numpy={n_lo}"
            )
            assert abs(l_hi - n_hi) < 0.03, (
                f"{key}[1]: loop={l_hi} numpy={n_hi}"
            )

    def test_resample_counts_match(self):
        """Both engines should drop the same proportion of
        single-class resamples and report identical (or near-
        identical) ``resamples`` counts. With well-separated
        well-balanced inputs basically no resamples should be
        dropped, so the counts should match exactly."""
        pairs = _make_pairs(200, seed=11)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        assert loop_out is not None and numpy_out is not None
        # Tight bound: |delta| <= 1 (in case one engine's RNG
        # produces exactly one all-positive or all-negative
        # resample the other doesn't).
        assert abs(loop_out["resamples"] - numpy_out["resamples"]) <= 1


# ----- Direction handling --------------------------------------


class TestDirectionAware:
    """Both engines must respect ``direction='lt'`` (predict
    positive when score < threshold) and ``direction='gt'``
    (predict positive when score > threshold). Polarity
    inversion is one of the cathedral correctness gates."""

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_gt_direction_picks_correct_class(self, engine):
        # Centered-around-+1 positives vs -1 negatives, threshold
        # 0.0, ``gt`` direction → high TPR expected.
        pairs = _make_pairs(500, seed=13)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is not None
        # TPR CI's lower bound should be well above 0.5 (the
        # synthetic data has well-separated classes).
        assert result["tpr_ci"][0] > 0.7

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_lt_direction_inverts(self, engine):
        # Same data, threshold 0.0, but ``lt`` direction means
        # predict-positive-when-score-<-0. That inverts which
        # class gets called positive at this threshold: the
        # actual-negatives (centered at -1) score below 0,
        # actual-positives (centered at +1) score above. So
        # the bootstrap TPR (true-positive rate among
        # actual-positives, who score >0) should be LOW.
        pairs = _make_pairs(500, seed=13)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="lt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is not None
        assert result["tpr_ci"][1] < 0.3

    def test_invalid_direction_raises_numpy(self):
        pairs = _make_pairs(50)
        with pytest.raises(ValueError, match="direction must"):
            _fixed_threshold_bootstrap_ci_numpy(
                pairs, threshold=0.0, direction="bogus",
                resamples=100, confidence=0.95, seed=42,
            )


# ----- Edge cases ----------------------------------------------


class TestEdgeCases:
    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_empty_pairs_returns_none(self, engine):
        result = fixed_threshold_bootstrap_ci(
            [], threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is None

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_single_class_pairs_no_valid_resamples(self, engine):
        # All label=1 → every resample is "all positive", which
        # the both-classes-present filter drops. Expect None.
        pairs: list[tuple[int, float]] = [
            (1, float(i)) for i in range(20)
        ]
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        # Both engines should agree on None for this pathological
        # input.
        assert result is None

    def test_threshold_at_extreme_returns_consistent_bounds(self):
        """Threshold below every observed score (gt direction):
        every row predicted positive. TPR = 1, FPR = 1. Both
        engines should report CIs tightly around 1.0."""
        pairs = [(1 if i % 2 else 0, float(i)) for i in range(100)]
        for engine in ("loop", "numpy"):
            result = fixed_threshold_bootstrap_ci(
                pairs, threshold=-100.0, direction="gt",
                resamples=200, confidence=0.95, seed=42,
                engine=engine,
            )
            assert result is not None
            # All-positive predictions → TPR == 1 across all
            # resamples → CI = [1.0, 1.0].
            assert result["tpr_ci"] == [1.0, 1.0]
            assert result["fpr_ci"] == [1.0, 1.0]


# ----- Performance smoke (not a regression test) ---------------


class TestPerformanceSmoke:
    """Sanity-check the numpy engine is meaningfully faster than
    the loop engine on a moderately-sized input. Not a strict
    benchmark — those are too sensitive to per-host noise — but
    a coarse upper bound: numpy must beat loop on N=5000, R=500.
    If this test ever fails it means the vectorized
    implementation has regressed badly enough to be slower than
    pure Python, which would be a real bug."""

    def test_numpy_faster_than_loop_on_5k_rows_500_resamples(self):
        import time
        pairs = _make_pairs(5000, seed=21)
        t0 = time.perf_counter()
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        loop_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        numpy_s = time.perf_counter() - t0
        assert loop_out is not None and numpy_out is not None
        # At N=5K, R=500 the loop takes ~10s and numpy ~0.1s on
        # a typical box (100x). Assert at least 5x to leave
        # headroom for slow CI runners; if the ratio drops below
        # this something has gone wrong.
        assert numpy_s * 5 < loop_s, (
            f"numpy ({numpy_s:.2f}s) should be >=5x faster than "
            f"loop ({loop_s:.2f}s) at N=5000, R=500"
        )


# ----- Auto chunk-size + provenance (Codex P1 on PR #53) -------


class TestAutoChunkSize:
    """_auto_chunk_size caps inner-loop memory at ~500 MB by
    picking a chunk size proportional to 1/n. Codex review (P1):
    the original fixed chunk_size=200 default OOM'd at RAID
    scale; this helper makes the budget explicit + operator-
    tunable, and persists the actual chunk used in provenance."""

    def test_returns_max_for_tiny_n(self):
        assert _auto_chunk_size(0, "numpy") == _AUTO_CHUNK_MAX
        assert _auto_chunk_size(1, "numpy") == _AUTO_CHUNK_MAX

    def test_returns_max_for_small_n(self):
        """At n=5K the 500 MB cap easily fits 200 chunks, so
        small corpora keep the legacy chunk size."""
        assert _auto_chunk_size(5_000, "numpy") == _AUTO_CHUNK_MAX

    def test_scales_down_for_mage_scale(self):
        """At MAGE n=436K, numpy should land roughly in the
        middle (~100) — well under 200, well above 1."""
        chunk = _auto_chunk_size(436_000, "numpy")
        assert 50 < chunk < _AUTO_CHUNK_MAX

    def test_scales_down_aggressively_for_raid_scale(self):
        """At RAID n=8.3M, numpy should shrink to single digits
        to keep the index matrix under the 500 MB budget."""
        chunk = _auto_chunk_size(8_300_000, "numpy")
        assert chunk >= _AUTO_CHUNK_MIN
        assert chunk < 20

    def test_torch_engine_is_more_conservative(self):
        """Torch uses int64 indices (vs int32 for numpy), so for
        the same n + budget torch should pick a smaller chunk."""
        n = 1_000_000
        np_chunk = _auto_chunk_size(n, "numpy")
        torch_chunk = _auto_chunk_size(n, "torch")
        assert torch_chunk <= np_chunk

    def test_never_returns_below_minimum(self):
        """Even at absurd n the chunk shouldn't drop below 1."""
        assert _auto_chunk_size(10**12, "numpy") >= 1


class TestChunkSizeRespectsExplicitOverride:
    """When the operator passes an explicit chunk_size, the engine
    honors it (within positive-int bounds). Critical for the
    --bootstrap-chunk-size CLI flag to do what it says."""

    def test_explicit_chunk_size_recorded_in_result(self):
        pairs = _make_pairs(200)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="numpy",
            chunk_size=7,
        )
        assert result is not None
        assert result["chunk_size"] == 7

    def test_none_chunk_size_uses_auto(self):
        pairs = _make_pairs(200)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="numpy",
            # chunk_size omitted; should auto-size.
        )
        assert result is not None
        # n=200 is small, so we expect the max (200) cap.
        assert result["chunk_size"] == _AUTO_CHUNK_MAX

    def test_chunk_size_does_not_change_ci_bounds(self):
        """A smaller chunk_size partitions the resamples
        differently but the same RNG seed feeds the same
        sequence of resamples. CI bounds should be insensitive
        to chunk size; pin the invariant so a future refactor
        that breaks this is caught."""
        pairs = _make_pairs(300)
        big = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
            engine="numpy", chunk_size=200,
        )
        small = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
            engine="numpy", chunk_size=10,
        )
        assert big is not None and small is not None
        # CI bounds should be identical (same RNG stream,
        # same resamples — chunk boundaries are an
        # implementation detail).
        assert big["tpr_ci"] == small["tpr_ci"]
        assert big["fpr_ci"] == small["fpr_ci"]
        assert big["precision_ci"] == small["precision_ci"]


class TestHarnessCommand:
    """``_build_harness_command`` composes the replay command
    stamped into the ledger. Codex review P1: the original
    version dropped --bootstrap-engine / --bootstrap-chunk-size
    so a numpy/torch-derived threshold replayed on the loop
    engine."""

    def test_default_loop_engine_omits_flag(self):
        """Loop is the default; omitting the flag in the recipe
        keeps replays of legacy ledger entries unchanged."""
        cmd = _build_harness_command(
            manifest_path=Path("m.jsonl"), use="validation",
            signal="burstiness_B", fpr_target=0.01,
            engine="loop",
        )
        assert "--bootstrap-engine" not in cmd
        assert "--bootstrap-chunk-size" not in cmd

    def test_numpy_engine_surfaces_engine_flag(self):
        cmd = _build_harness_command(
            manifest_path=Path("m.jsonl"), use="validation",
            signal="burstiness_B", fpr_target=0.01,
            engine="numpy",
        )
        assert "--bootstrap-engine numpy" in cmd

    def test_explicit_chunk_size_surfaces_flag(self):
        cmd = _build_harness_command(
            manifest_path=Path("m.jsonl"), use="validation",
            signal="burstiness_B", fpr_target=0.01,
            engine="numpy",
            chunk_size=17,
        )
        assert "--bootstrap-chunk-size 17" in cmd

    def test_auto_chunk_size_does_not_emit_flag(self):
        """When the operator didn't pass --bootstrap-chunk-size,
        the recipe shouldn't pin a specific value either — let
        the replay auto-size for the same n it sees."""
        cmd = _build_harness_command(
            manifest_path=Path("m.jsonl"), use="validation",
            signal="burstiness_B", fpr_target=0.01,
            engine="numpy",
            chunk_size=None,
        )
        assert "--bootstrap-chunk-size" not in cmd

    def test_manifest_path_with_spaces_is_shell_quoted(self):
        """Codex P2 (PR #53): the operator's runtime workspace
        lives under ``Claude Cowork Working Folder``, whose path
        contains spaces. The unquoted recipe would split the path
        across tokens — copy-paste replay would fail with
        ``manifest: file not found`` or worse, silently pick up a
        differently-named manifest. ``shlex.quote`` wraps the
        path in single quotes when it contains whitespace.

        Verify by parsing the recipe back with ``shlex.split`` and
        confirming the manifest argument round-trips to the
        original path."""
        import shlex
        path_with_spaces = Path(
            "/users/anotherpanacea/Documents/"
            "Claude Cowork Working Folder/manifest.jsonl"
        )
        cmd = _build_harness_command(
            manifest_path=path_with_spaces,
            use="validation",
            signal="burstiness_B", fpr_target=0.01,
            engine="numpy",
        )
        # The quoted path appears verbatim as a single shell
        # token (round-trips through shlex.split).
        tokens = shlex.split(cmd)
        manifest_idx = tokens.index("--manifest")
        assert tokens[manifest_idx + 1] == str(path_with_spaces), (
            f"--manifest argument didn't round-trip through "
            f"shlex.split: got {tokens[manifest_idx + 1]!r}, "
            f"expected {str(path_with_spaces)!r}"
        )
        # And the recipe text contains the shlex-quoted form, not
        # the bare unquoted path.
        assert shlex.quote(str(path_with_spaces)) in cmd

    def test_use_with_spaces_is_shell_quoted(self):
        """Defense in depth: even if ``--use`` is normally a bare
        identifier, an operator-supplied value with whitespace
        should still round-trip cleanly."""
        import shlex
        cmd = _build_harness_command(
            manifest_path=Path("m.jsonl"),
            use="validation set",  # space in the value
            signal="burstiness_B", fpr_target=0.01,
        )
        tokens = shlex.split(cmd)
        use_idx = tokens.index("--use")
        assert tokens[use_idx + 1] == "validation set"

    def test_shell_safe_tokens_are_not_quoted(self):
        """``shlex.quote`` is a no-op for tokens without
        whitespace or shell metacharacters. Pin this by using
        values guaranteed shell-safe on both POSIX and Windows
        (no path separators, no special chars) so the test
        works cross-platform — the real cross-platform concern
        is just that paths with spaces don't break, which is
        covered by ``test_manifest_path_with_spaces_is_shell_quoted``
        above."""
        cmd = _build_harness_command(
            manifest_path=Path("manifest.jsonl"),  # no slashes
            use="validation",
            signal="burstiness_B", fpr_target=0.01,
        )
        # The bare 'manifest.jsonl' should appear unquoted —
        # shlex.quote leaves shell-safe identifiers alone.
        assert "--manifest manifest.jsonl" in cmd
        # And nothing gets wrapped over-aggressively (no stray
        # quote characters around the flag name itself).
        assert "'--manifest'" not in cmd


class TestProvenanceRecordsEngine:
    """End-to-end: a real derive_threshold call should record the
    selected engine + chunk_size in ``entry["calibration"]`` and
    surface ``--bootstrap-engine`` in ``entry["harness_command"]``.

    Codex review (PR #53, P1): without this the ledger couldn't
    tell which implementation produced the CI, and the replay
    command silently regressed to the default loop engine.
    """

    @staticmethod
    def _make_args(**overrides):
        import argparse
        base = dict(
            manifest="dummy.jsonl",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            out=None,
            slug=None,
            replace=False,
            bootstrap_resamples=20,
            bootstrap_confidence=0.95,
            bootstrap_seed=42,
            bootstrap_engine="numpy",
            bootstrap_chunk_size=None,
            tier2=False,
            tier3=False,
            notes=None,
            max_entries=None,
            max_entries_seed=None,
            records_cache=None,
            refresh_cache=False,
            allow_polarity_inversion=False,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def _stub_pipeline(self, monkeypatch):
        """Stub the manifest/scoring/sweep path so the test
        exercises ONLY the engine-threading code, not spaCy
        or HF I/O."""
        import calibrate_thresholds as ct
        monkeypatch.setattr(
            ct, "collect_signal_records",
            lambda records, signal_path: [
                (i % 2, float(i)) for i in range(40)
            ],
        )
        monkeypatch.setattr(
            ct, "sweep_threshold",
            lambda pairs, direction, target: {
                "available": True, "threshold": 20.0,
                "fpr_resolution": 0.05,
                "fpr": 0.05, "tpr": 0.5, "precision": 0.5,
                "n_pos": 20, "n_neg": 20,
            },
        )
        monkeypatch.setattr(
            ct, "_ranking_metrics",
            lambda pairs, *, direction: {
                "auc": 0.80, "ap": 0.78,
                "direction_aware_auc": 0.80,
                "direction_aware_ap": 0.78,
            },
        )
        monkeypatch.setattr(
            ct, "_load_fetch_record", lambda manifest_path: {},
        )

    def test_numpy_engine_recorded_in_provenance(self, monkeypatch):
        import calibrate_thresholds as ct
        self._stub_pipeline(monkeypatch)
        args = self._make_args(bootstrap_engine="numpy")
        entry = ct.derive_threshold_from_records(
            [], args=args, scoring_meta={},
        )
        cal = entry["calibration"]
        assert cal["bootstrap_engine"] == "numpy"
        # Chunk size auto-sized for the synthetic n=40 → at the
        # _AUTO_CHUNK_MAX (small corpus).
        assert cal["bootstrap_chunk_size"] == _AUTO_CHUNK_MAX

    def test_explicit_chunk_size_recorded_in_provenance(
        self, monkeypatch,
    ):
        import calibrate_thresholds as ct
        self._stub_pipeline(monkeypatch)
        args = self._make_args(
            bootstrap_engine="numpy",
            bootstrap_chunk_size=33,
        )
        entry = ct.derive_threshold_from_records(
            [], args=args, scoring_meta={},
        )
        cal = entry["calibration"]
        assert cal["bootstrap_engine"] == "numpy"
        assert cal["bootstrap_chunk_size"] == 33

    def test_harness_command_carries_engine_flag(self, monkeypatch):
        import calibrate_thresholds as ct
        self._stub_pipeline(monkeypatch)
        args = self._make_args(
            bootstrap_engine="numpy",
            bootstrap_chunk_size=33,
        )
        entry = ct.derive_threshold_from_records(
            [], args=args, scoring_meta={},
        )
        cmd = entry["harness_command"]
        assert "--bootstrap-engine numpy" in cmd
        assert "--bootstrap-chunk-size 33" in cmd

    def test_loop_engine_does_not_emit_engine_flag(self, monkeypatch):
        """Default behavior is unchanged: a ledger entry derived
        with the loop engine omits the flag, so replays of
        pre-1.60 entries match byte-for-byte."""
        import calibrate_thresholds as ct
        self._stub_pipeline(monkeypatch)
        args = self._make_args(bootstrap_engine="loop")
        entry = ct.derive_threshold_from_records(
            [], args=args, scoring_meta={},
        )
        cmd = entry["harness_command"]
        assert "--bootstrap-engine" not in cmd
        assert "--bootstrap-chunk-size" not in cmd
        # Engine field still recorded (so consumers can read it
        # uniformly without inferring from omission).
        assert entry["calibration"]["bootstrap_engine"] == "loop"
        # Loop engine doesn't have chunk_size; field is None.
        assert entry["calibration"]["bootstrap_chunk_size"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
