"""Tests for 1.101.0 per-(comparator × judge × generator) direction
routing on polarity_audit and slice_bakeoff_v2.

PR #103 (1.98.0) added per-comparator-class routing to both modules
(``DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR`` /
``SIGNAL_SPECS_BY_COMPARATOR`` + ``resolve_registry_direction`` /
``resolve_signal_direction``).

PR #106 (1.100.0) extended ``variance_audit.ThresholdSpec`` with the
deeper ``direction_by_comparator_and_slice`` field +
``resolve_direction_with_slice`` helper -- a three-level nested dict
keyed by (comparator, judge, generator). That closed the symmetry
gap on the variance_audit side; the polarity_audit + slice_bakeoff_v2
modules were still per-class only.

This module pins the 1.101.0 extension that mirrors PR #106 at the
polarity_audit + slice_bakeoff_v2 level:

  * New tables ``DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE``
    (polarity_audit) and ``SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE``
    (slice_bakeoff_v2) -- three-level nested dicts keyed by
    ``(signal → comparator → judge → generator → "gt" / "lt")``.
  * New helpers ``resolve_registry_direction_with_slice`` (polarity_-
    audit) and ``resolve_signal_direction_with_slice`` (slice_bakeoff_v2)
    with the full three-layer fallback chain.
  * Existing ``resolve_registry_direction`` / ``resolve_signal_direction``
    become back-compat wrappers that call the new helpers with
    judge=None, generator=None -- pre-1.101 callers get the exact
    same answers bit-for-bit.
  * ``analyze(..., judge=None, generator=None)`` on slice_bakeoff_v2
    accepts the new kwargs and forwards them to BOTH the per-cell
    direction resolver and the integrated polarity-audit handoff
    (the load-bearing parity discipline from PR #103's fix commit,
    now extended to the deeper layer).
  * ``--judge`` + ``--generator`` CLI flags on both modules. Neither
    auto-defaults from corpus (per PR #112: judge/generator are
    slice axes within a corpus, not properties of it).

The infrastructure ships with the override tables EMPTY -- no
populated (judge, generator) cells in 1.101.0. The per-(judge ×
generator) RAID data taxonomy still needs operator data to settle;
once it does, the 13 RAID ``comparator_dependent`` cells get
populated in the tables without further plumbing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polarity_audit as pa  # type: ignore  # noqa: E402
import slice_bakeoff_v2 as sb  # type: ignore  # noqa: E402


# ============================================================================
# polarity_audit: DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE
# ============================================================================


class TestPolarityAuditSliceTableShape:
    """``DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE`` is the
    new optional three-level table. Validated at import time, with
    each leaf required to be 'gt' or 'lt' and each intermediate level
    required to be a dict. Errors name the offending path."""

    def test_shipped_table_is_empty(self):
        """1.101.0 ships the table EMPTY -- the populated 13-RAID-cell
        override is operator-side data work (item F.1), not this PR.
        Pin the shipped state so a future populate-PR has to explicitly
        update this assertion."""
        assert pa.DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE == {}

    def test_validator_accepts_well_formed_table(self):
        """A correctly-shaped three-level dict round-trips through the
        validator without error. Catches future regressions where the
        validator is tightened without updating the contract."""
        pa._validate_slice_overrides({
            "surprisal_sd": {
                "raid": {
                    "chatgpt": {"gpt-4o-mini": "lt", "gpt-4o": "gt"},
                    "claude": {"sonnet": "lt"},
                },
            },
        })

    def test_validator_rejects_invalid_leaf(self):
        """A typo at the leaf level ('xx' instead of 'gt') fails fast
        with the full (signal, comp, judge, generator) path named."""
        with pytest.raises(
            ValueError,
            match=r"surprisal_sd.*raid.*chatgpt.*gpt-4o",
        ):
            pa._validate_slice_overrides({
                "surprisal_sd": {
                    "raid": {"chatgpt": {"gpt-4o": "xx"}},
                },
            })

    def test_validator_rejects_non_dict_at_comparator_level(self):
        """A string-where-dict-expected at the comparator level fails
        fast. Catches typos like ``{"surprisal_sd": "lt"}`` (forgetting
        the comparator dict entirely)."""
        with pytest.raises(ValueError, match="dict"):
            pa._validate_slice_overrides({
                "surprisal_sd": "lt",  # type: ignore
            })

    def test_validator_rejects_non_dict_at_judge_level(self):
        """A string-where-dict-expected at the judge level fails fast."""
        with pytest.raises(ValueError, match="dict"):
            pa._validate_slice_overrides({
                "surprisal_sd": {"raid": "lt"},  # type: ignore
            })

    def test_validator_rejects_non_dict_at_generator_level(self):
        """A string-where-dict-expected at the generator level fails
        fast. Catches typos like ``{"raid": {"chatgpt": "lt"}}``."""
        with pytest.raises(ValueError, match="dict"):
            pa._validate_slice_overrides({
                "surprisal_sd": {
                    "raid": {"chatgpt": "lt"},  # type: ignore
                },
            })


# ============================================================================
# polarity_audit: resolve_registry_direction_with_slice fallback chain
# ============================================================================


class TestPolarityAuditResolveWithSliceFallback:
    """The full three-layer fallback chain (most-specific to
    least-specific):

      1. slice_overrides[signal][comp][judge][generator]
      2. overrides[signal][comp]
      3. defaults[signal]
      4. None (signal unknown)
    """

    def _defaults(self):
        return {"surprisal_sd": "gt"}

    def _per_class(self):
        return {"surprisal_sd": {"raid": "lt"}}

    def _slice(self):
        return {"surprisal_sd": {"raid": {"chatgpt": {"gpt-4o": "gt"}}}}

    def test_full_path_hit_returns_innermost_layer(self):
        """All five conditions met → return the innermost entry, even
        when the per-class entry says the opposite."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator="gpt-4o",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_missing_generator_falls_back_to_per_class(self):
        """(comp, judge) match but generator misses → per-class wins."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator="claude-sonnet",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_missing_judge_falls_back_to_per_class(self):
        """Comp matches but judge isn't in the middle dict → per-class."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "raid", judge="gemini", generator="gpt-4o",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_missing_comparator_falls_back_to_default(self):
        """Comparator isn't in either table → spec default."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "editlens", judge="chatgpt", generator="gpt-4o",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_judge_none_skips_innermost_layer(self):
        """judge=None disqualifies the inner layer; per-class wins
        even when the slice table has a matching entry."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "raid", judge=None, generator="gpt-4o",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_generator_none_skips_innermost_layer(self):
        """Symmetric: generator=None disqualifies the inner layer."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator=None,
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_all_none_returns_spec_default(self):
        """No comparator, no judge, no generator → spec default."""
        assert pa.resolve_registry_direction_with_slice(
            "surprisal_sd", None, judge=None, generator=None,
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_unknown_signal_returns_none(self):
        """Signal not in defaults at all → None. Identical to the
        per-class-only path."""
        assert pa.resolve_registry_direction_with_slice(
            "nonsense", "raid", judge="chatgpt", generator="gpt-4o",
            defaults=self._defaults(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) is None


# ============================================================================
# polarity_audit: resolve_registry_direction back-compat wrapper
# ============================================================================


class TestPolarityAuditResolveBackCompat:
    """``resolve_registry_direction(signal, comparator_class)`` is a
    thin wrapper that calls ``resolve_registry_direction_with_slice``
    with judge=None, generator=None. Pre-1.101 callers MUST get
    exactly the PR #103 fallback chain bit-for-bit -- the wrapper
    exists so the existing test suite + every production call site
    keeps working unchanged."""

    def test_wrapper_returns_per_class_when_class_set(self):
        assert pa.resolve_registry_direction(
            "surprisal_sd", "raid",
        ) == "lt"

    def test_wrapper_returns_default_when_no_class(self):
        assert pa.resolve_registry_direction("surprisal_sd") == "gt"

    def test_wrapper_returns_default_when_class_not_in_overrides(self):
        assert pa.resolve_registry_direction(
            "surprisal_sd", "editlens",
        ) == "gt"

    def test_wrapper_unknown_signal_returns_none(self):
        assert pa.resolve_registry_direction("nonsense") is None

    def test_wrapper_byte_for_byte_against_per_class_path(self):
        """For every (signal, class) pair the per-class fallback
        chain produces an answer for, the wrapper produces the same
        answer. Mechanical exhaustion of the back-compat contract:
        the new helper must be a strict superset that never changes
        a pre-1.101 answer."""
        signals = list(pa.DEFAULT_REGISTRY_DIRECTIONS.keys()) + ["nonsense"]
        classes = [None, "mage", "raid", "editlens"]
        for sig in signals:
            for cls in classes:
                via_wrapper = pa.resolve_registry_direction(sig, cls)
                via_helper = pa.resolve_registry_direction_with_slice(
                    sig, cls, judge=None, generator=None,
                )
                assert via_wrapper == via_helper, (
                    f"back-compat divergence at ({sig!r}, {cls!r}): "
                    f"wrapper={via_wrapper!r}, helper={via_helper!r}"
                )


# ============================================================================
# slice_bakeoff_v2: SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE
# ============================================================================


class TestSliceBakeoffSliceTableShape:
    """``SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE`` mirrors the
    polarity_audit table on the slicer side."""

    def test_shipped_table_is_empty(self):
        """1.101.0 ships the table EMPTY (operator-side data work
        deferred to item F.1)."""
        assert sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE == {}

    def test_validator_accepts_well_formed_table(self):
        sb._validate_slice_overrides({
            "surprisal_sd": {
                "raid": {"chatgpt": {"gpt-4o": "lt"}},
            },
        })

    def test_validator_rejects_invalid_leaf(self):
        """Path-named error so a populate-PR finds the typo fast."""
        with pytest.raises(
            ValueError,
            match=r"surprisal_sd.*raid.*chatgpt.*gpt-4o",
        ):
            sb._validate_slice_overrides({
                "surprisal_sd": {
                    "raid": {"chatgpt": {"gpt-4o": "xx"}},
                },
            })

    def test_validator_rejects_non_dict_at_judge_level(self):
        with pytest.raises(ValueError, match="dict"):
            sb._validate_slice_overrides({
                "surprisal_sd": {"raid": "lt"},  # type: ignore
            })

    def test_validator_rejects_non_dict_at_generator_level(self):
        with pytest.raises(ValueError, match="dict"):
            sb._validate_slice_overrides({
                "surprisal_sd": {
                    "raid": {"chatgpt": "lt"},  # type: ignore
                },
            })


# ============================================================================
# slice_bakeoff_v2: resolve_signal_direction_with_slice fallback chain
# ============================================================================


class TestSliceBakeoffResolveWithSliceFallback:
    """Mirror of the polarity_audit resolver tests against
    SIGNAL_SPECS (which uses a (path, direction) tuple shape rather
    than a flat signal → direction dict)."""

    def _specs(self):
        return {"surprisal_sd": ("tier4.surprisal.sd", "gt")}

    def _per_class(self):
        return {"surprisal_sd": {"raid": "lt"}}

    def _slice(self):
        return {"surprisal_sd": {"raid": {"chatgpt": {"gpt-4o": "gt"}}}}

    def test_full_path_hit_returns_innermost_layer(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator="gpt-4o",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_missing_generator_falls_back_to_per_class(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator="claude-sonnet",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_missing_judge_falls_back_to_per_class(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "raid", judge="gemini", generator="gpt-4o",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_missing_comparator_falls_back_to_default(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "editlens", judge="chatgpt", generator="gpt-4o",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_judge_none_skips_innermost_layer(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "raid", judge=None, generator="gpt-4o",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_generator_none_skips_innermost_layer(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", "raid", judge="chatgpt", generator=None,
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "lt"

    def test_all_none_returns_spec_default(self):
        assert sb.resolve_signal_direction_with_slice(
            "surprisal_sd", None, judge=None, generator=None,
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) == "gt"

    def test_unknown_signal_returns_none(self):
        assert sb.resolve_signal_direction_with_slice(
            "nonsense", "raid", judge="chatgpt", generator="gpt-4o",
            specs=self._specs(),
            overrides=self._per_class(),
            slice_overrides=self._slice(),
        ) is None


# ============================================================================
# slice_bakeoff_v2: resolve_signal_direction back-compat wrapper
# ============================================================================


class TestSliceBakeoffResolveBackCompat:
    """``resolve_signal_direction(signal, comparator_class)`` is a
    thin wrapper -- same back-compat contract as on polarity_audit."""

    def test_wrapper_returns_per_class_when_class_set(self):
        assert sb.resolve_signal_direction(
            "surprisal_sd", "raid",
        ) == "lt"

    def test_wrapper_returns_default_when_no_class(self):
        assert sb.resolve_signal_direction("surprisal_sd") == "gt"

    def test_wrapper_unknown_signal_returns_none(self):
        assert sb.resolve_signal_direction("nonsense") is None

    def test_wrapper_byte_for_byte_against_per_class_path(self):
        """Same exhaustive back-compat sweep as on the polarity_audit
        wrapper. Every pre-1.101 (signal, class) answer must match
        the new helper called with judge=None, generator=None."""
        signals = list(sb.SIGNAL_SPECS.keys()) + ["nonsense"]
        classes = [None, "mage", "raid", "editlens"]
        for sig in signals:
            for cls in classes:
                via_wrapper = sb.resolve_signal_direction(sig, cls)
                via_helper = sb.resolve_signal_direction_with_slice(
                    sig, cls, judge=None, generator=None,
                )
                assert via_wrapper == via_helper, (
                    f"back-compat divergence at ({sig!r}, {cls!r}): "
                    f"wrapper={via_wrapper!r}, helper={via_helper!r}"
                )


# ============================================================================
# slice_bakeoff_v2: analyze() honors (judge × generator) override
# ============================================================================


def _make_synthetic_cache(
    cache_dir: Path,
    phase: str,
    model_alias: str,
    n_pos: int,
    n_neg: int,
    pos_mean_offset: float = 0.6,
) -> Path:
    """Write a cache_phase{A,B}_<alias>.json with deterministic per-row
    scores. Positives are offset above negatives so the aggregate AUC
    is reliably above 0.5 on the registry's default direction (gt).
    Deterministic (no rng) so the test is bit-stable across runs.
    """
    signal_paths = {
        "A": ("tier3.adjacent_cosine.mean", "tier3.adjacent_cosine.sd"),
        "B": (
            "tier4.surprisal.mean", "tier4.surprisal.sd",
            "tier4.surprisal.autocorrelation.lag_1",
        ),
    }[phase]
    buckets = ["lt_200", "200_499", "500_999"]
    records = []
    for i in range(n_pos):
        # Spread positives across [pos_mean_offset - 0.5,
        # pos_mean_offset + 0.5] deterministically.
        spread = (i / max(1, n_pos - 1)) - 0.5
        per_signal = {
            sp: pos_mean_offset + spread for sp in signal_paths
        }
        records.append({
            "id": f"pos_{i}",
            "label": 1,
            "length_bucket": buckets[i % 3],
            "register": "essay",
            "adversarial_class": "none",
            "per_signal_scores": per_signal,
        })
    for i in range(n_neg):
        spread = (i / max(1, n_neg - 1)) - 0.5
        per_signal = {sp: spread for sp in signal_paths}
        records.append({
            "id": f"neg_{i}",
            "label": 0,
            "length_bucket": buckets[i % 3],
            "register": "essay",
            "adversarial_class": "none",
            "per_signal_scores": per_signal,
        })
    cache_path = cache_dir / f"cache_phase{phase}_{model_alias}.json"
    cache_path.write_text(
        json.dumps({"records": records}, indent=2),
        encoding="utf-8",
    )
    return cache_path


def _make_synthetic_manifest(tmp_path: Path, n_each: int) -> Path:
    path = tmp_path / "manifest.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_each):
            f.write(json.dumps({
                "id": f"pos_{i}",
                "notes": {"original_source": "synth"},
            }) + "\n")
        for i in range(n_each):
            f.write(json.dumps({
                "id": f"neg_{i}",
                "notes": {"original_source": "synth"},
            }) + "\n")
    return path


class TestAnalyzeAcceptsJudgeGeneratorKwargs:
    """``analyze(judge=..., generator=...)`` is the function-entry
    contract. Pin that the kwargs are accepted (1.101.0+ signature)."""

    def test_analyze_accepts_judge_generator_kwargs(self):
        """``analyze(judge='x', generator='y')`` does not raise
        TypeError. Pre-1.101 callers (no judge / generator) still
        work; the kwargs are optional and default to None."""
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td) / "cache"
            cache_dir.mkdir()
            manifest = Path(td) / "manifest.jsonl"
            manifest.write_text("")
            out_dir = Path(td) / "out"
            # Empty cache_dir → analyze returns 2 (no cache files).
            # We only need to confirm the kwargs are accepted.
            rc = sb.analyze(
                cache_dir=cache_dir, manifest_path=manifest,
                out_dir=out_dir, corpus="synth",
                domain_key=None, split_key=None, generator_key=None,
                crosstabs=[], min_n=10,
                do_polarity_audit=False, comparator_key=None,
                comparator_class="raid",
                judge="chatgpt", generator="gpt-4o",
            )
            assert rc == 2


class TestAnalyzeSlicePolarityAuditParityUnderInnermostOverride:
    """Load-bearing parity test: when a (judge × generator) override
    flips a signal's direction, BOTH the per-cell emission loop and
    the integrated polarity-audit handoff must read the same flipped
    direction.

    PR #103's fix commit was exactly this bug class one level up
    (per-cell loop saw the per-class override, integrated audit
    didn't); this test mirrors it at the (judge × generator) layer.
    """

    def _run(
        self, *, tmp_path: Path, slice_overrides,
        judge=None, generator=None,
    ):
        """Run analyze() with a monkeypatched slice-override table
        and return (csv_rows, polarity_audit_dict)."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        cache_dir = tmp_path / "caches"
        cache_dir.mkdir()
        # Phase B so we can exercise surprisal_sd (the empirical
        # signal-of-interest for the 13-cell taxonomy).
        _make_synthetic_cache(
            cache_dir, "B", "mxbai", n_pos=100, n_neg=100,
        )
        manifest = _make_synthetic_manifest(tmp_path, n_each=100)
        out_dir = tmp_path / "out"

        original_table = sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE
        sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE = slice_overrides
        try:
            rc = sb.analyze(
                cache_dir=cache_dir, manifest_path=manifest,
                out_dir=out_dir, corpus="synth",
                domain_key=None, split_key=None, generator_key=None,
                crosstabs=[], min_n=30,
                do_polarity_audit=True,
                comparator_key="notes.original_source",
                comparator_class="raid",
                judge=judge, generator=generator,
            )
        finally:
            sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE = original_table

        assert rc == 0
        import csv as _csv
        csv_path = out_dir / "slice_analysis.csv"
        with open(csv_path, encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        audit_path = out_dir / "polarity_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        return rows, audit

    def test_per_cell_direction_reflects_innermost_override(
        self, tmp_path: Path,
    ):
        """With slice_overrides flipping surprisal_sd to 'lt' on
        (raid, chatgpt, gpt-4o), the per-cell CSV rows for that signal
        carry the flipped direction-aware AUC (da_auc = 1 - raw_auc
        for an 'lt' signal). With the per-class default (which is 'lt'
        for surprisal_sd on raid), da_auc = 1 - raw_auc. Override to
        'gt' instead so we can SEE the difference vs. per-class."""
        # Override raid+chatgpt+gpt-4o back to 'gt' so the cell flips
        # vs. the per-class 'lt'.
        slice_overrides = {
            "surprisal_sd": {
                "raid": {"chatgpt": {"gpt-4o": "gt"}},
            },
        }
        rows_inner, _ = self._run(
            tmp_path=tmp_path / "inner",
            slice_overrides=slice_overrides,
            judge="chatgpt", generator="gpt-4o",
        )
        rows_class, _ = self._run(
            tmp_path=tmp_path / "class",
            slice_overrides=slice_overrides,
            judge=None, generator=None,
        )
        # Find the surprisal_sd ALL row in both runs.
        inner_sd = next(
            r for r in rows_inner
            if r["signal"] == "surprisal_sd" and r["slice_key"] == "ALL"
        )
        class_sd = next(
            r for r in rows_class
            if r["signal"] == "surprisal_sd" and r["slice_key"] == "ALL"
        )
        # The raw AUC is data-driven and identical across runs; the
        # direction-aware AUC differs because the inner-most override
        # flipped the direction back to 'gt' for the (chatgpt, gpt-4o)
        # cell, while the per-class fall-through is 'lt' for raid.
        assert float(inner_sd["auc"]) == float(class_sd["auc"])
        # Under 'gt' direction da_auc == auc; under 'lt' da_auc == 1 - auc.
        assert abs(
            float(inner_sd["da_auc"]) + float(class_sd["da_auc"]) - 1.0,
        ) < 1e-6, (
            "expected inner_sd.da_auc + class_sd.da_auc == 1 "
            "(gt vs. lt direction-aware flip)"
        )

    def test_integrated_polarity_audit_uses_same_direction(
        self, tmp_path: Path,
    ):
        """The integrated polarity audit MUST read surprisal_sd under
        the same direction the per-cell emission loop used. Pre-fix-
        like-PR-#103 (one layer up), the slicer would emit cells under
        direction='gt' (from the cell-level override) but the
        polarity audit would evaluate them under direction='lt' (from
        the per-class fall-through that ignored judge/generator) --
        the two outputs would disagree.

        Pin parity: the polarity audit's per-signal verdict reads
        the same registry_direction the per-cell rows were emitted
        under."""
        slice_overrides = {
            "surprisal_sd": {
                "raid": {"chatgpt": {"gpt-4o": "gt"}},
            },
        }
        rows, audit = self._run(
            tmp_path=tmp_path,
            slice_overrides=slice_overrides,
            judge="chatgpt", generator="gpt-4o",
        )
        # Find the surprisal_sd verdict in the audit. The integrated
        # audit reads each row under registry_direction =
        # signal_to_direction[signal] -- which the slicer built from
        # resolve_signal_direction_with_slice(...). With judge=chatgpt,
        # generator=gpt-4o, that resolves to 'gt' (per the inner-most
        # override).
        sd_verdict = next(
            r for r in audit.get("results", [])
            if r["signal"] == "surprisal_sd"
        )
        assert sd_verdict["registry_direction"] == "gt", (
            "polarity audit MUST see the (judge × generator) "
            "override; otherwise the per-cell direction and the "
            "integrated audit direction disagree."
        )

    def test_no_judge_no_generator_uses_per_class_direction(
        self, tmp_path: Path,
    ):
        """Without judge / generator, the integrated audit reads the
        per-class direction ('lt' for surprisal_sd on raid). This pins
        the back-compat fall-through: pre-1.101 callers see the
        pre-1.101 behavior."""
        slice_overrides = {
            "surprisal_sd": {
                "raid": {"chatgpt": {"gpt-4o": "gt"}},
            },
        }
        _, audit = self._run(
            tmp_path=tmp_path,
            slice_overrides=slice_overrides,
            judge=None, generator=None,
        )
        sd_verdict = next(
            r for r in audit.get("results", [])
            if r["signal"] == "surprisal_sd"
        )
        # Per-class entry for raid is 'lt'; the slice override is
        # ignored without judge + generator.
        assert sd_verdict["registry_direction"] == "lt"


# ============================================================================
# CLI: --judge / --generator on both modules
# ============================================================================


class TestPolarityAuditCLIExposesJudgeGenerator:
    """``--judge`` + ``--generator`` are wired on the polarity_audit
    CLI and appear in --help. Default is None (no inner-layer routing
    -- pre-1.101 behavior). NOT auto-defaulted from any other arg."""

    def test_help_lists_judge_flag(self):
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, "
                f"{str(CALIB_DIR)!r}); "
                "from polarity_audit import build_arg_parser; "
                "build_arg_parser().parse_args(['--help'])",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--judge" in result.stdout
        assert "--generator" in result.stdout

    def test_help_does_not_imply_auto_default_from_corpus(self):
        """Per PR #112: judge/generator are slice axes WITHIN a corpus,
        not properties of it. The help text must not promise the
        auto-default-from-corpus behavior that --comparator-class has
        (otherwise operators would expect it). We pin the negative
        contract: no "auto" or "auto-default" claim adjacent to the
        judge / generator help blocks."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, "
                f"{str(CALIB_DIR)!r}); "
                "from polarity_audit import build_arg_parser; "
                "build_arg_parser().parse_args(['--help'])",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        # The help block for --judge explicitly documents "NOT
        # auto-defaulted" so an operator skimming --help sees the
        # contract. Pin that phrasing.
        assert "NOT auto-defaulted" in result.stdout or \
            "not auto-defaulted" in result.stdout.lower()


class TestSliceBakeoffCLIExposesJudgeGenerator:
    """Same contract on the slice_bakeoff_v2 CLI."""

    def test_help_lists_judge_flag(self):
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, "
                f"{str(CALIB_DIR)!r}); "
                "from slice_bakeoff_v2 import build_arg_parser; "
                "build_arg_parser().parse_args(['--help'])",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "--judge" in result.stdout
        assert "--generator" in result.stdout

    def test_help_does_not_imply_auto_default_from_corpus(self):
        """Same negative contract: --judge / --generator do NOT
        auto-default from --corpus. The slicer's --comparator-class
        does (via _resolve_cli_comparator_class) but judge / generator
        explicitly do not -- per PR #112, they're slice axes."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                "import sys; sys.path.insert(0, "
                f"{str(CALIB_DIR)!r}); "
                "from slice_bakeoff_v2 import build_arg_parser; "
                "build_arg_parser().parse_args(['--help'])",
            ],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "NOT auto-defaulted" in result.stdout or \
            "not auto-defaulted" in result.stdout.lower()


# ============================================================================
# Shipped registry pin: tables empty in 1.101.0
# ============================================================================


class TestShippedSliceTablesAreEmpty:
    """1.101.0 ships the shape but NOT the data. The populated 13-
    RAID-cell override is operator-side data work (item F.1 follow-
    up). Pin the shipped state so a future populate-PR has to
    explicitly update this assertion AND the corresponding
    DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE entry in
    variance_audit (kept in lockstep across the three modules per
    PR #103's three-way-mirror discipline)."""

    def test_polarity_audit_slice_table_empty(self):
        assert pa.DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE == {}

    def test_slice_bakeoff_slice_table_empty(self):
        assert sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE == {}

    def test_no_signal_has_a_populated_slice_entry(self):
        """Belt-and-braces over the dict-equals-{} check: walk every
        signal in the per-class tables and confirm none of them
        accidentally gained a slice entry."""
        for sig in pa.DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR:
            assert sig not in pa.\
                DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE, (
                f"polarity_audit signal {sig!r} unexpectedly ships "
                f"with an inner slice override in 1.101.0; update "
                f"this test + the CHANGELOG if intentional."
            )
        for sig in sb.SIGNAL_SPECS_BY_COMPARATOR:
            assert sig not in sb.SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE, (
                f"slice_bakeoff signal {sig!r} unexpectedly ships "
                f"with an inner slice override in 1.101.0; update "
                f"this test + the CHANGELOG if intentional."
            )


# ---------------------------------------------------------------------------
# Explicit --registry-direction must outrank routing
# ---------------------------------------------------------------------------


def _write_minimal_slicer_csv(path: Path, signal: str) -> None:
    """Write a slicer CSV with the columns ``polarity_audit.load_slicer_csv``
    requires AND the structure ``build_audit`` needs: per-(model, signal)
    it needs both an aggregate row (slice_key=ALL) and at least one
    non-ALL cell row. Without the ALL aggregate, build_audit skips the
    signal entirely.
    """
    path.write_text(
        "corpus,model,signal,slice_key,slice_value,n_pos,n_neg,auc,da_auc,abs_signal\n"
        f"raid,gpt2,{signal},ALL,ALL,100,100,0.42,0.42,abs_test\n"
        f"raid,gpt2,{signal},original_source,raid_test,50,50,0.42,0.42,abs_test\n",
        encoding="utf-8",
    )


def _find_result(audit: dict, signal: str) -> dict | None:
    for r in audit.get("results", []):
        if r.get("signal") == signal:
            return r
    return None


class TestExplicitRegistryOverrideOutranksRouting:
    """Reviewer P1: ``--registry-direction sig=dir`` is the operator's
    explicit intent; it must outrank the per-comparator routing chain.

    Pre-fix, the resolver always walked the per-comparator table first
    and returned its entry if present, so ``--registry-direction
    surprisal_sd=gt --comparator-class raid`` silently resolved back
    to ``lt`` (the per-comparator override from PR #103). Manual
    what-if audits became impossible exactly where routing exists.
    """

    def test_override_preserved_when_per_comparator_entry_exists(
        self, tmp_path, monkeypatch,
    ):
        # Seed the per-comparator table with a routed direction that
        # would normally clobber the operator's override.
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR,
            "_test_sig_for_override_outranks_routing",
            {"_test_corpus": "lt"},
        )
        # Also ensure the spec default exists so the resolver has a
        # fallback layer.
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS,
            "_test_sig_for_override_outranks_routing",
            "lt",
        )
        csv_path = tmp_path / "slice.csv"
        out_json = tmp_path / "polarity_audit.json"
        _write_minimal_slicer_csv(
            csv_path, "_test_sig_for_override_outranks_routing",
        )
        rc = pa.main([
            "--input-csv", str(csv_path),
            "--out-json", str(out_json),
            "--comparator-class", "_test_corpus",
            "--registry-direction",
            "_test_sig_for_override_outranks_routing=gt",
        ])
        assert rc == 0
        audit = json.loads(out_json.read_text(encoding="utf-8"))
        result = _find_result(
            audit, "_test_sig_for_override_outranks_routing",
        )
        assert result is not None, (
            "audit produced no result block for the test signal"
        )
        assert result.get("registry_direction") == "gt", (
            "operator's --registry-direction override was silently "
            "replaced by the per-comparator routing entry; expected 'gt' "
            f"(the operator's choice) got "
            f"{result.get('registry_direction')!r}"
        )

    def test_non_overridden_signals_still_route_normally(
        self, tmp_path, monkeypatch,
    ):
        """Pin the symmetric contract: when the operator does NOT
        override a signal, routing still applies. We don't disable
        routing globally — just for explicitly-overridden signals.
        """
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR,
            "_test_sig_routes_when_not_overridden",
            {"_test_corpus": "lt"},
        )
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS,
            "_test_sig_routes_when_not_overridden",
            "gt",
        )
        csv_path = tmp_path / "slice.csv"
        out_json = tmp_path / "polarity_audit.json"
        _write_minimal_slicer_csv(
            csv_path, "_test_sig_routes_when_not_overridden",
        )
        # No --registry-direction for this signal.
        rc = pa.main([
            "--input-csv", str(csv_path),
            "--out-json", str(out_json),
            "--comparator-class", "_test_corpus",
        ])
        assert rc == 0
        audit = json.loads(out_json.read_text(encoding="utf-8"))
        result = _find_result(
            audit, "_test_sig_routes_when_not_overridden",
        )
        assert result is not None, (
            "audit produced no result block for the test signal"
        )
        assert result.get("registry_direction") == "lt", (
            "without an explicit override, routing should still apply: "
            f"_test_corpus → lt; got {result.get('registry_direction')!r}"
        )


# ---------------------------------------------------------------------------
# Provenance records routing axes
# ---------------------------------------------------------------------------


class TestSliceBakeoffProvenanceRecordsRoutingAxes:
    """Reviewer P2: ``analyze()`` now accepts comparator_class / judge /
    generator, and those values can change ``da_auc``. Two runs with
    different routing axes must leave distinguishable provenance —
    otherwise an operator inspecting ``provenance.json`` can't tell
    why their numbers changed.
    """

    def test_write_provenance_records_routing_axes(self, tmp_path):
        path = tmp_path / "provenance.json"
        sb.write_provenance(
            path,
            cache_dir=tmp_path,
            cache_files=[],
            manifest_path=tmp_path / "nonexistent_manifest.jsonl",
            corpus="raid",
            crosstabs=[["original_source"]],
            min_n=30,
            do_polarity_audit=True,
            comparator_key="notes.domain",
            comparator_class="raid",
            judge="chatgpt",
            generator="gpt-4o",
        )
        provenance = json.loads(path.read_text(encoding="utf-8"))
        assert provenance["comparator_class"] == "raid"
        assert provenance["judge"] == "chatgpt"
        assert provenance["generator"] == "gpt-4o"
        # Pre-existing fields still recorded.
        assert provenance["comparator_key"] == "notes.domain"
        assert provenance["corpus"] == "raid"

    def test_write_provenance_routing_axes_default_to_none(self, tmp_path):
        """Back-compat: when routing axes aren't supplied, the fields
        appear as null in the JSON (not absent) so downstream readers
        can rely on key presence.
        """
        path = tmp_path / "provenance.json"
        sb.write_provenance(
            path,
            cache_dir=tmp_path,
            cache_files=[],
            manifest_path=tmp_path / "nonexistent_manifest.jsonl",
            corpus="mage",
            crosstabs=[["original_source"]],
            min_n=30,
            do_polarity_audit=False,
            comparator_key=None,
        )
        provenance = json.loads(path.read_text(encoding="utf-8"))
        assert provenance.get("comparator_class") is None
        assert provenance.get("judge") is None
        assert provenance.get("generator") is None


class TestPolarityAuditJsonRecordsRoutingAxes:
    """Reviewer P2 (companion): the standalone polarity audit's output
    JSON also records the routing axes so the routing context survives
    outside the command line.
    """

    def test_polarity_audit_json_records_routing_block(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS,
            "_test_sig_for_routing_block", "gt",
        )
        csv_path = tmp_path / "slice.csv"
        out_json = tmp_path / "polarity_audit.json"
        _write_minimal_slicer_csv(csv_path, "_test_sig_for_routing_block")
        rc = pa.main([
            "--input-csv", str(csv_path),
            "--out-json", str(out_json),
            "--comparator-class", "raid",
            "--judge", "chatgpt",
            "--generator", "gpt-4o",
        ])
        assert rc == 0
        audit = json.loads(out_json.read_text(encoding="utf-8"))
        routing = audit.get("routing", {})
        assert routing.get("comparator_class") == "raid"
        assert routing.get("judge") == "chatgpt"
        assert routing.get("generator") == "gpt-4o"

    def test_polarity_audit_routing_explicit_overrides_recorded(
        self, tmp_path, monkeypatch,
    ):
        """The routing block also lists which signals were explicitly
        overridden via ``--registry-direction``. Lets the operator
        see in the output JSON which signals bypassed routing.
        """
        monkeypatch.setitem(
            pa.DEFAULT_REGISTRY_DIRECTIONS,
            "_test_sig_recorded_override", "lt",
        )
        csv_path = tmp_path / "slice.csv"
        out_json = tmp_path / "polarity_audit.json"
        _write_minimal_slicer_csv(csv_path, "_test_sig_recorded_override")
        rc = pa.main([
            "--input-csv", str(csv_path),
            "--out-json", str(out_json),
            "--registry-direction", "_test_sig_recorded_override=gt",
        ])
        assert rc == 0
        audit = json.loads(out_json.read_text(encoding="utf-8"))
        routing = audit.get("routing", {})
        assert "_test_sig_recorded_override" in (
            routing.get("explicit_registry_overrides") or []
        )
