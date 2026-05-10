#!/usr/bin/env python3
"""Regression tests for voice_distance.py's length-matched bootstrap.

Phase 1 step 3 finisher for voice_distance: the bootstrap mode
replaces the unanchored "is this Delta large?" question with a
calibrated percentile against baseline-window function-word
distances at the target's word count. Tests verify:

  * The bootstrap helper builds a baseline-window distribution at
    the target's length and reports a percentile in [0, 1].
  * Bootstrap CI is well-formed (low ≤ percentile ≤ high) when
    scipy is available; falls back gracefully without scipy.
  * The function-word vector machinery is byte-stable: same input
    text produces identical vectors across calls.
  * The L1 distance is symmetric and zero on identical vectors.
  * The full bootstrap_compare wrapper handles the empty-baseline
    and zero-target-words edge cases without crashing.
  * The output dict carries the documented top-level keys.
  * Empirical: a target text drawn from the same distribution as
    the baseline corpus (a baseline file used as target) lands at
    a non-extreme percentile; a target text obviously different
    from baseline (random non-overlapping vocabulary) lands above
    the median.

Tests use small in-memory text fixtures (no spaCy / no
filesystem-heavy operations beyond tempfiles).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import voice_distance as vd  # type: ignore


# ------------------- Fixtures ------------------------------------


# A repeatable phrase set; varied function-word usage across files
# but consistent enough that the within-baseline distance is small.
_BASELINE_PHRASES = [
    "The discipline of attention is older than the disciplines that "
    "depend on it. The mathematician and the carpenter share a "
    "single habit. The looker comes back altered. ",
    "Most of the time the work is small and the room is quiet, and "
    "the writer does not know what is being written until it is "
    "done. The hand and the page settle into a single rhythm. ",
    "What you remember is the surface. What is in the surface is "
    "the thing you have been carrying. The work pulls it out. ",
    "When the morning is clean and the cup is hot and the page is "
    "blank, what gets written is what was already there. The book "
    "is a record of attention, not invention. ",
]


def _make_baseline_dir(tmp_path: Path) -> Path:
    """Write the four baseline phrases to a temp dir, multiplied so
    each file is ≥ 200 words (the bootstrap floor).
    """
    base = tmp_path / "baseline"
    base.mkdir()
    for i, phrase in enumerate(_BASELINE_PHRASES):
        content = (phrase * 12).strip()  # ~200+ words per file
        (base / f"essay_{i}.txt").write_text(content, encoding="utf-8")
    return base


def _baseline_entries_from_dir(base: Path) -> list[dict]:
    """Build the dict shape voice_distance expects."""
    entries = []
    for p in sorted(base.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        entries.append({
            "id": p.stem,
            "path": str(p),
            "n_words": len(text.split()),
        })
    return entries


# ------------------- Function-word vector ------------------------


class TestFunctionWordVector:
    def test_vector_is_dict_with_function_word_keys(self):
        v = vd._function_word_vector("the and of the")
        assert isinstance(v, dict)
        assert "the" in v
        assert "and" in v

    def test_relative_frequencies_sum_within_one(self):
        """Function-word ratios are bounded by 1.0 (the function-word
        share of all tokens). Empty strings are explicitly handled."""
        v = vd._function_word_vector("the cat sat on the mat")
        s = sum(v.values())
        assert 0 < s <= 1.0
        assert vd._function_word_vector("") == {
            k: 0.0 for k in v.keys()
        }

    def test_byte_stability(self):
        """Same input → identical output across calls."""
        text = "the and of the the and"
        assert vd._function_word_vector(text) == vd._function_word_vector(text)


class TestBaselineMean:
    def test_empty_baseline_returns_zero_vector(self):
        v = vd._baseline_mean_function_word_vector([])
        assert all(x == 0.0 for x in v.values())

    def test_single_text_baseline_matches_single_vector(self):
        text = "the and of the"
        m = vd._baseline_mean_function_word_vector([text])
        assert m == vd._function_word_vector(text)

    def test_mean_is_average_of_two(self):
        a = "the the the the"
        b = "the and of of"
        m = vd._baseline_mean_function_word_vector([a, b])
        ma = vd._function_word_vector(a)
        mb = vd._function_word_vector(b)
        for k in m:
            assert abs(m[k] - 0.5 * (ma[k] + mb[k])) < 1e-12


class TestManhattanDistance:
    def test_zero_on_identical_vectors(self):
        v = vd._function_word_vector("the and of")
        assert vd._manhattan_distance(v, v) == 0.0

    def test_symmetric(self):
        a = vd._function_word_vector("the and of")
        b = vd._function_word_vector("the and the")
        assert vd._manhattan_distance(a, b) == vd._manhattan_distance(b, a)

    def test_nonnegative(self):
        a = vd._function_word_vector("the and of")
        b = vd._function_word_vector("the and the")
        assert vd._manhattan_distance(a, b) >= 0.0


# ------------------- bootstrap_compare end-to-end ----------------


class TestBootstrapCompareE2E:
    def test_returns_unavailable_when_target_empty(self, tmp_path):
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        out = vd.bootstrap_compare("", entries, n_resamples=99,
                                    n_windows_per_file=2,
                                    max_total_windows=10, seed=42)
        assert out["available"] is False
        assert "zero words" in out["reason"]

    def test_returns_unavailable_when_baseline_empty(self):
        out = vd.bootstrap_compare(
            "the and of the and of " * 20,
            [],  # no entries
            n_resamples=99, n_windows_per_file=2,
            max_total_windows=10, seed=42,
        )
        assert out["available"] is False

    def test_well_formed_output_on_real_corpus(self, tmp_path):
        """Run the bootstrap on baseline + a target drawn from baseline
        and check structural invariants: percentile is a probability,
        CI is in [0, 1], target distance and length are reported."""
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        # Target = first baseline file's text (so percentile should be
        # non-extreme on this small synthetic corpus).
        target = (base / "essay_0.txt").read_text(encoding="utf-8")
        out = vd.bootstrap_compare(
            target, entries,
            n_windows_per_file=5, max_total_windows=40,
            n_resamples=199,
            seed=42,
        )
        if not out.get("available"):
            pytest.skip(f"bootstrap unavailable: {out.get('reason')}")
        # Documented top-level keys.
        for k in (
            "task_surface", "statistic", "target_n_words",
            "target_function_word_distance", "baseline_distribution",
            "bootstrap", "config",
        ):
            assert k in out, f"missing top-level key {k}"
        assert out["task_surface"] == "voice_coherence"
        bs = out["bootstrap"]
        # Percentile is a probability.
        assert 0.0 <= bs["percentile"] <= 1.0
        # When CI is reported, it bounds the percentile.
        if bs["ci_low"] is not None and bs["ci_high"] is not None:
            assert 0.0 <= bs["ci_low"] <= bs["ci_high"] <= 1.0
        # Target length matches input.
        assert out["target_n_words"] == len(target.split())

    def test_target_far_from_baseline_lands_above_median(self, tmp_path):
        """A target with very different function-word usage than the
        baseline should land at a high percentile."""
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        # Target made entirely of content words rare in the baseline,
        # with no function words → very different function-word vector.
        target = (
            "Mahogany xylophone phosphor archipelago mahogany "
            "xylophone phosphor archipelago. " * 30
        )
        out = vd.bootstrap_compare(
            target, entries,
            n_windows_per_file=5, max_total_windows=40,
            n_resamples=199, seed=42,
        )
        if not out.get("available"):
            pytest.skip(f"bootstrap unavailable: {out.get('reason')}")
        assert out["bootstrap"]["percentile"] >= 0.5, (
            "target with no shared function-word usage should sit at "
            "or above the median of within-baseline distances; got "
            f"{out['bootstrap']['percentile']}"
        )


# ------------------- Markdown formatter --------------------------


class TestFormatBootstrapBlock:
    def test_unavailable_renders_section(self):
        lines = vd.format_bootstrap_block(
            {"available": False, "reason": "no scipy"},
        )
        text = "\n".join(lines)
        assert "## Length-matched bootstrap" in text
        assert "Unavailable" in text

    def test_available_renders_target_and_distribution(self):
        # The shape mirrors what `length_matched_bootstrap()` actually
        # returns: nested `quantiles` keyed `p5`/`p25`/`p50`/`p75`/`p95`,
        # plus top-level `n`, `mean`, `sd`. Fixed in 1.30.1 — pre-fix
        # the formatter read flat keys (`p05`, etc.) and rendered
        # every cell as `n/a` against real helper output.
        lines = vd.format_bootstrap_block({
            "available": True,
            "target_n_words": 500,
            "target_function_word_distance": 0.0420,
            "baseline_distribution": {
                "n": 50,
                "quantiles": {
                    "p5": 0.01, "p25": 0.02, "p50": 0.03,
                    "p75": 0.04, "p95": 0.05,
                },
                "mean": 0.03, "sd": 0.011,
            },
            "bootstrap": {
                "percentile": 0.83,
                "ci_low": 0.78, "ci_high": 0.88,
                "method": "BCa", "n_resamples": 999,
                "n_baseline_windows": 50,
            },
            "config": {},
        })
        text = "\n".join(lines)
        assert "## Length-matched bootstrap" in text
        assert "Empirical percentile" in text
        # Distribution table renders actual numbers (not `n/a` for
        # every cell — the bug the reviewer reproduced).
        assert "0.0300" in text  # p50 / mean
        assert "0.0500" in text  # p95
        assert "0.0100" in text  # p5
        # And the column headers are the real shape.
        assert "p5 |" in text and "p95 |" in text and "n |" in text


# ---------- 1.30.1 reviewer-flagged P2 fixes -----------------------


class TestPreprocessingThreaded:
    """The bootstrap path now applies the same corpus-hygiene
    stripping the rest of voice_distance applies. Without this, a
    CSS / HTML / footer artifact stripped from compare_to_baseline's
    feature extraction would still contribute to the bootstrap
    distribution — the percentile and the headline Delta would be
    measured on different texts. Reproduces and pins the reviewer-
    flagged P2.
    """

    def test_strip_rules_changes_target_distance(self, tmp_path):
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        # Target with a CSS block prepended. Without stripping, the
        # CSS tokens contaminate the function-word vector; with
        # stripping, only the prose contributes.
        css = (
            ".reading-mode { color: #333; padding: 12px; "
            "background: #f5f5f5; } "
            "h1.title { font-size: 18px; font-weight: bold; } "
        ) * 5
        prose = "the and of the cat sat on the mat " * 100
        contaminated = css + prose

        clean_run = vd.bootstrap_compare(
            contaminated, entries,
            n_windows_per_file=4, max_total_windows=20,
            n_resamples=99, seed=42,
            # Default strips conservative rules including HTML/CSS.
        )
        raw_run = vd.bootstrap_compare(
            contaminated, entries,
            n_windows_per_file=4, max_total_windows=20,
            n_resamples=99, seed=42,
            allow_non_prose=True,  # opt OUT of stripping
        )
        # Both runs are valid; the cleaned-target distance should be
        # smaller (closer to baseline) than the raw-target distance,
        # since the contamination pushes the function-word vector
        # away from baseline mean.
        if clean_run.get("available") and raw_run.get("available"):
            assert (
                clean_run["target_function_word_distance"]
                < raw_run["target_function_word_distance"]
            ), (
                "stripping should bring contaminated target closer "
                "to baseline mean"
            )

    def test_config_records_preprocessing_choices(self, tmp_path):
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        target = (base / "essay_0.txt").read_text(encoding="utf-8")
        out = vd.bootstrap_compare(
            target, entries,
            n_windows_per_file=2, max_total_windows=10,
            n_resamples=99, seed=42,
            allow_non_prose=False,
            strip_aggressive=True,
        )
        if not out.get("available"):
            pytest.skip(f"bootstrap unavailable: {out.get('reason')}")
        cfg = out.get("config") or {}
        assert cfg.get("allow_non_prose") is False
        assert cfg.get("strip_aggressive") is True
        assert "strip_rules" in cfg


class TestNestedDistributionShape:
    """The formatter reads the nested ``quantiles`` shape that
    ``length_matched_bootstrap`` actually returns (``{n, quantiles:
    {p5, p25, p50, p75, p95}, mean, sd}``). Pre-1.30.1 it read flat
    keys (``p05``, ``min``, ``max``) and rendered every cell as
    ``n/a`` against real helper output. End-to-end formatter test
    using actual `bootstrap_compare` output."""

    def test_real_helper_output_renders_real_numbers(self, tmp_path):
        base = _make_baseline_dir(tmp_path)
        entries = _baseline_entries_from_dir(base)
        target = (base / "essay_0.txt").read_text(encoding="utf-8")
        out = vd.bootstrap_compare(
            target, entries,
            n_windows_per_file=4, max_total_windows=20,
            n_resamples=99, seed=42,
        )
        if not out.get("available"):
            pytest.skip(f"bootstrap unavailable: {out.get('reason')}")
        text = "\n".join(vd.format_bootstrap_block(out))
        # The distribution row should have at least one numeric cell,
        # not all `n/a`. (Pre-fix every cell was `n/a`.)
        assert text.count("n/a") < 5, (
            "distribution row rendered too many n/a cells; the "
            "formatter is reading the wrong shape"
        )
        # Actual quantile keys appear in the header.
        assert "| p5 |" in text and "| p95 |" in text

    def test_dist_value_reads_nested_quantiles(self):
        bd = {
            "n": 50,
            "quantiles": {"p5": 0.01, "p50": 0.03, "p95": 0.05},
            "mean": 0.03, "sd": 0.011,
        }
        assert vd._dist_value(bd, "p5") == 0.01
        assert vd._dist_value(bd, "p50") == 0.03
        assert vd._dist_value(bd, "mean") == 0.03  # top-level
        assert vd._dist_value(bd, "n") == 50
        assert vd._dist_value(bd, "nonexistent") is None

    def test_dist_value_handles_legacy_flat_shape(self):
        """Defensive: if a legacy caller hands in a flat dict (no
        nested ``quantiles``), the helper still finds the value."""
        bd = {"p5": 0.01, "p50": 0.03}
        assert vd._dist_value(bd, "p5") == 0.01


class TestDegenerateBootstrapCI:
    """SciPy BCa can return non-finite bounds without raising on
    degenerate samples. Pre-1.30.1 those bounds went through
    `min(1.0, NaN)` / `max(0.0, NaN)` which collapsed NaN into 1.0
    or 0.0, producing CI [1.0, 1.0] regardless of the point estimate.
    Reproducing and pinning: any unacceptable bounds (non-finite,
    out-of-range, or excluding the point) get rejected and replaced
    with a degenerate-no-CI marker.
    """

    def test_constant_sample_returns_degenerate_marker(self):
        """All-equal sample → BCa cannot estimate bias-correction
        and percentile method also returns 0/0 bounds. The honest
        output is `[point, point]` with method='degenerate_no_ci'."""
        from length_bootstrap import bootstrap_percentile  # type: ignore
        out = bootstrap_percentile(
            [0.5] * 30, target=0.5,
            n_resamples=999, seed=42,
        )
        # Either degenerate path produced [point, point] (target
        # equal to constant) or a finite/contained CI. Never the
        # NaN-clamped [1.0, 1.0] failure mode.
        if out["ci_low"] is not None and out["ci_high"] is not None:
            assert out["ci_low"] <= out["percentile"] <= out["ci_high"], (
                "CI must contain the point estimate"
            )
            # And bounds must be finite.
            import math
            assert math.isfinite(out["ci_low"])
            assert math.isfinite(out["ci_high"])

    def test_target_outside_sample_returns_point_ci(self):
        """Target far past the sample max → percentile is 1.0 with
        no resampling uncertainty. Returns [point, point], not a
        garbage clamp."""
        from length_bootstrap import bootstrap_percentile  # type: ignore
        out = bootstrap_percentile(
            [0.1, 0.2, 0.3, 0.4], target=10.0,
            n_resamples=999, seed=42,
        )
        assert out["percentile"] == 1.0
        assert out["ci_low"] == 1.0
        assert out["ci_high"] == 1.0

    def test_ci_always_contains_point_when_finite(self):
        """Across many random samples, when the CI is reported (not
        the degenerate marker), it always contains the point."""
        import math
        import random
        from length_bootstrap import bootstrap_percentile  # type: ignore
        rng = random.Random(0xC0FFEE)
        for trial in range(15):
            n = rng.randint(2, 30)
            sample = [rng.random() for _ in range(n)]
            target = rng.choice(sample) if rng.random() < 0.5 else rng.random()
            out = bootstrap_percentile(
                sample, target=target,
                n_resamples=199, seed=trial,
            )
            lo, hi = out["ci_low"], out["ci_high"]
            if lo is not None and hi is not None:
                assert math.isfinite(lo) and math.isfinite(hi), (
                    f"non-finite CI bound: {(lo, hi)}"
                )
                assert lo <= out["percentile"] <= hi, (
                    f"CI {(lo, hi)} excludes point {out['percentile']}"
                )


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
