"""Tests for 1.100.0 per-(comparator × judge × generator) direction routing.

PR #103 (1.98.0) added per-comparator-class routing via
``ThresholdSpec.direction_by_comparator: dict[str, str]``. But
the 2026-05-18 RAID 5K bake-off surfaced 13
``comparator_dependent`` cells where the direction differs by
``(LM-judge × generator-family)`` within the same comparator
class -- finer than per-comparator-class. This module pins the
1.100.0 extension that adds the deeper routing layer:

  * New field ``direction_by_comparator_and_slice:
    dict[str, dict[str, dict[str, str]]] | None`` -- three-level
    nested dict keyed by (comparator, judge, generator).
  * New helper ``resolve_direction_with_slice(spec, comparator_class,
    judge=None, generator=None) -> str`` with the full three-layer
    fallback chain.
  * Existing ``resolve_direction(spec, comparator_class)`` becomes
    a back-compat wrapper that calls the new helper with
    judge=None, generator=None -- pre-1.100 callers get the
    exact same answers bit-for-bit.
  * ``classify_compression(..., judge=None, generator=None)`` and
    ``audit_windows(..., judge=None, generator=None)`` accept the
    new kwargs.
  * ``--judge`` + ``--generator`` CLI flags on
    ``variance_audit.py``.
  * ``thresholds_used`` JSON surfaces ``judge`` + ``generator``
    alongside ``comparator_class`` + the new
    ``direction_by_comparator_and_slice`` field.

The infrastructure ships with the override table EMPTY -- no
populated (judge, generator) cells in 1.100.0. The per-(judge ×
generator) RAID data taxonomy still needs operator data to
settle; once it does, the 13 RAID ``comparator_dependent`` cells
get populated in the field without further plumbing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import variance_audit as va  # type: ignore  # noqa: E402


# ---------- ThresholdSpec field shape ----------------------------


class TestThresholdSpecDeeperFieldShape:
    """``direction_by_comparator_and_slice`` is the new optional
    field. Validated at construction with the offending path
    named in the error (so a typo deep in the nested dict
    surfaces fast)."""

    def test_field_defaults_to_none(self):
        """Pre-1.100 callers building a spec without the field get
        ``direction_by_comparator_and_slice=None`` -- the no-
        override case where ``resolve_direction_with_slice`` falls
        back to either the per-class entry or the spec default."""
        spec = va.ThresholdSpec(
            signal_path="t.x", value=0.5, direction="gt",
            weight=1.0, length_floor=100,
            status="heuristic",
        )
        assert spec.direction_by_comparator_and_slice is None

    def test_field_accepts_three_level_nested_dict(self):
        """The full three-level shape -- comparator → judge →
        generator → 'gt'/'lt' -- round-trips correctly through
        construction."""
        spec = va.ThresholdSpec(
            signal_path="t.x", value=0.5, direction="gt",
            weight=1.0, length_floor=100,
            status="heuristic",
            direction_by_comparator_and_slice={
                "raid": {
                    "chatgpt": {"gpt-4o-mini": "lt", "gpt-4o": "gt"},
                    "claude": {"sonnet": "lt"},
                },
            },
        )
        assert spec.direction_by_comparator_and_slice == {
            "raid": {
                "chatgpt": {"gpt-4o-mini": "lt", "gpt-4o": "gt"},
                "claude": {"sonnet": "lt"},
            },
        }

    def test_field_rejects_invalid_leaf_direction(self):
        """A typo at the leaf level ('xx' instead of 'gt') fails
        fast with the full (comp, judge, generator) path named."""
        with pytest.raises(ValueError, match=r"raid.*chatgpt.*gpt-4o"):
            va.ThresholdSpec(
                signal_path="t.x", value=0.5, direction="gt",
                weight=1.0, length_floor=100,
                status="heuristic",
                direction_by_comparator_and_slice={
                    "raid": {"chatgpt": {"gpt-4o": "xx"}},
                },
            )

    def test_field_rejects_non_dict_at_judge_level(self):
        """A string-where-dict-expected at the middle level fails
        fast. Catches schema-shape typos like
        ``{"raid": "lt"}`` (forgetting the inner judge dict) by
        complaining about the type."""
        with pytest.raises(ValueError, match="dict"):
            va.ThresholdSpec(
                signal_path="t.x", value=0.5, direction="gt",
                weight=1.0, length_floor=100,
                status="heuristic",
                direction_by_comparator_and_slice={
                    "raid": "lt",  # type: ignore
                },
            )

    def test_field_rejects_non_dict_at_generator_level(self):
        """A string-where-dict-expected at the inner level fails
        fast. Catches typos like ``{"raid": {"chatgpt": "lt"}}``
        (forgetting the generator level)."""
        with pytest.raises(ValueError, match="dict"):
            va.ThresholdSpec(
                signal_path="t.x", value=0.5, direction="gt",
                weight=1.0, length_floor=100,
                status="heuristic",
                direction_by_comparator_and_slice={
                    "raid": {"chatgpt": "lt"},  # type: ignore
                },
            )

    def test_can_be_set_alongside_per_comparator_field(self):
        """The two fields coexist. The new deeper field doesn't
        replace ``direction_by_comparator``; both are optional
        independently, and the resolver falls back through the
        chain."""
        spec = va.ThresholdSpec(
            signal_path="t.x", value=0.5, direction="gt",
            weight=1.0, length_floor=100,
            status="heuristic",
            direction_by_comparator={"raid": "lt"},
            direction_by_comparator_and_slice={
                "raid": {"chatgpt": {"gpt-4o": "gt"}},
            },
        )
        assert spec.direction_by_comparator == {"raid": "lt"}
        assert spec.direction_by_comparator_and_slice == {
            "raid": {"chatgpt": {"gpt-4o": "gt"}},
        }


# ---------- resolve_direction_with_slice fallback chain ----------


class TestResolveDirectionWithSliceFallbackChain:
    """The full three-layer fallback chain (most-specific to
    least-specific):

      1. direction_by_comparator_and_slice[comp][judge][gen]
      2. direction_by_comparator[comp]
      3. direction
    """

    def _spec(self, *, by_comparator=None, by_slice=None):
        return va.ThresholdSpec(
            signal_path="t.x", value=0.5, direction="gt",
            weight=1.0, length_floor=100,
            status="heuristic",
            direction_by_comparator=by_comparator,
            direction_by_comparator_and_slice=by_slice,
        )

    def test_full_path_hit_returns_innermost_layer(self):
        """All five conditions met (comparator + judge + generator
        all set; both tables set; full path in nested dict) →
        return the innermost entry."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        # Innermost wins even though comparator-class entry says 'lt'.
        assert va.resolve_direction_with_slice(
            spec, "raid", judge="chatgpt", generator="gpt-4o",
        ) == "gt"

    def test_missing_generator_falls_back_to_per_class(self):
        """(comp, judge) match but generator isn't in the inner
        dict → fall back to the per-comparator-class entry."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        # generator='claude-sonnet' isn't in the inner dict.
        assert va.resolve_direction_with_slice(
            spec, "raid", judge="chatgpt", generator="claude-sonnet",
        ) == "lt"

    def test_missing_judge_falls_back_to_per_class(self):
        """(comp, ...) matches comparator but judge isn't in the
        middle dict → fall back to per-class entry."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        # judge='gemini' isn't in the middle dict.
        assert va.resolve_direction_with_slice(
            spec, "raid", judge="gemini", generator="gpt-4o",
        ) == "lt"

    def test_missing_comparator_falls_back_to_per_class(self):
        """comparator_class isn't in the outer slice dict → fall
        back to the per-class entry (which may also miss → spec
        default)."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        # comparator_class='editlens' isn't in either table.
        assert va.resolve_direction_with_slice(
            spec, "editlens", judge="chatgpt", generator="gpt-4o",
        ) == "gt"  # spec default (per-class also misses)

    def test_judge_none_skips_innermost_layer(self):
        """Even if the inner table has an entry, judge=None means
        we can't activate the innermost layer → fall back to
        per-class."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        assert va.resolve_direction_with_slice(
            spec, "raid", judge=None, generator="gpt-4o",
        ) == "lt"  # per-class wins because judge=None

    def test_generator_none_skips_innermost_layer(self):
        """Symmetric: generator=None disqualifies innermost."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        assert va.resolve_direction_with_slice(
            spec, "raid", judge="chatgpt", generator=None,
        ) == "lt"

    def test_all_none_returns_spec_default(self):
        """No comparator, no judge, no generator → spec default."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        assert va.resolve_direction_with_slice(
            spec, None, judge=None, generator=None,
        ) == "gt"

    def test_no_slice_table_falls_through_to_per_class(self):
        """When direction_by_comparator_and_slice is None entirely,
        the helper behaves identically to PR #103's
        resolve_direction -- the per-class table is the only
        active layer."""
        spec = self._spec(by_comparator={"raid": "lt"}, by_slice=None)
        # With judge + generator set, the resolver still finds
        # the per-class entry.
        assert va.resolve_direction_with_slice(
            spec, "raid", judge="chatgpt", generator="gpt-4o",
        ) == "lt"


# ---------- resolve_direction back-compat wrapper ----------------


class TestResolveDirectionBackCompat:
    """``resolve_direction(spec, comparator_class)`` is a thin
    wrapper that calls ``resolve_direction_with_slice(spec,
    comparator_class, judge=None, generator=None)``. Pre-1.100
    callers MUST get exactly the PR #103 fallback chain bit-for-
    bit -- the wrapper exists so the existing test suite + every
    production call site keeps working unchanged."""

    def _spec(self, *, by_comparator=None, by_slice=None):
        return va.ThresholdSpec(
            signal_path="t.x", value=0.5, direction="gt",
            weight=1.0, length_floor=100,
            status="heuristic",
            direction_by_comparator=by_comparator,
            direction_by_comparator_and_slice=by_slice,
        )

    def test_wrapper_returns_per_class_when_class_set(self):
        spec = self._spec(by_comparator={"raid": "lt"})
        assert va.resolve_direction(spec, "raid") == "lt"

    def test_wrapper_returns_default_when_no_class(self):
        spec = self._spec(by_comparator={"raid": "lt"})
        assert va.resolve_direction(spec, None) == "gt"

    def test_wrapper_ignores_inner_slice_table_entirely(self):
        """The wrapper never activates the innermost layer because
        it always passes judge=None, generator=None. Even if a
        spec has a fully-populated slice table, the wrapper only
        consults per-class + default."""
        spec = self._spec(
            by_comparator={"raid": "lt"},
            by_slice={"raid": {"chatgpt": {"gpt-4o": "gt"}}},
        )
        # The inner table says 'gt' for raid+chatgpt+gpt-4o, but
        # the wrapper doesn't know about judge / generator → returns
        # the per-class 'lt'.
        assert va.resolve_direction(spec, "raid") == "lt"


# ---------- classify_compression integration ---------------------


def _minimal_audit_with_tier4(surprisal_sd_value: float) -> dict:
    """Synthetic audit dict (mirror of the PR #103 test fixture).
    n_words=400 clears the length_floor=300 on surprisal_sd."""
    return {
        "summary": {"n_words": 400, "n_sentences": 30},
        "tier1": {
            "sentence_length": {"burstiness_B": 0.0, "sd": 5.0},
            "connective_density": {"per_1000_tokens": 30.0},
            "mattr": {"value": 0.7},
            "mtld": 80.0,
            "yules_k": 60.0,
            "shannon_entropy_bits": 9.0,
            "fkgl": {"sd": 1.5},
        },
        "tier4": {
            "surprisal": {
                "mean": 5.0,
                "sd": surprisal_sd_value,
                "autocorrelation": {"lag_1": 0.0},
            },
        },
    }


class TestClassifyCompressionHonorsJudgeGenerator:
    """Pin the end-to-end behavior change: a populated inner-
    table entry on surprisal_sd flips its verdict under the
    matching (judge, generator) cell.

    We inject a populated slice override directly onto the
    COMPRESSION_HEURISTICS spec instance for the duration of the
    test (monkeypatched) since the framework's shipped table is
    empty in 1.100.0."""

    def test_no_judge_no_generator_uses_pre_1_100_behavior(self):
        """Without judge / generator, classify_compression behaves
        exactly as in PR #103 -- per-class routing only."""
        audit = _minimal_audit_with_tier4(surprisal_sd_value=2.0)
        result_no = va.classify_compression(audit, comparator_class="raid")
        # raid override flips surprisal_sd to 'lt' so value=2.0 > 1.5
        # is NOT compressed under raid.
        assert "surprisal_sd" not in result_no["flagged_signals"]

    def test_judge_generator_innermost_override_flips_back(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """When the inner slice table has an entry for (raid,
        chatgpt, gpt-4o), it overrides the per-class direction
        for that cell only.

        Set up a synthetic override: raid+chatgpt+gpt-4o flips
        back to 'gt'. value=2.0 > 1.5 is then compressed for this
        cell (matching pre-1.95 / un-routed behavior), even though
        the per-class raid direction is 'lt' (NOT compressed).
        Other (judge, generator) tuples still get the per-class
        'lt'.
        """
        original_spec = va.COMPRESSION_HEURISTICS["surprisal_sd"]
        patched_spec = va.ThresholdSpec(
            signal_path=original_spec.signal_path,
            value=original_spec.value,
            direction=original_spec.direction,
            weight=original_spec.weight,
            length_floor=original_spec.length_floor,
            provenance=original_spec.provenance,
            status=original_spec.status,
            direction_by_comparator=original_spec.direction_by_comparator,
            direction_by_comparator_and_slice={
                "raid": {"chatgpt": {"gpt-4o": "gt"}},
            },
        )
        monkeypatch.setitem(
            va.COMPRESSION_HEURISTICS, "surprisal_sd", patched_spec,
        )
        audit = _minimal_audit_with_tier4(surprisal_sd_value=2.0)
        # With matching (judge, generator), the cell flips back to 'gt'
        # so value=2.0 > 1.5 IS compressed.
        result = va.classify_compression(
            audit, comparator_class="raid",
            judge="chatgpt", generator="gpt-4o",
        )
        assert "surprisal_sd" in result["flagged_signals"]
        # With a non-matching (judge, generator), falls back to
        # per-class 'lt' so NOT compressed.
        result_other = va.classify_compression(
            audit, comparator_class="raid",
            judge="gemini", generator="gemini-1.5-pro",
        )
        assert "surprisal_sd" not in result_other["flagged_signals"]


# ---------- thresholds_used JSON surfaces routing inputs ---------


class TestThresholdsUsedSurfacesJudgeGenerator:
    """1.100.0+ adds ``judge`` + ``generator`` to the
    thresholds_used block per signal, plus the full
    ``direction_by_comparator_and_slice`` table from the spec.
    Audit consumers can tell cell-level resolution apart from
    class-level resolution from spec-default."""

    def test_thresholds_used_includes_judge_generator_fields(self):
        """Both keys present per signal."""
        audit = _minimal_audit_with_tier4(surprisal_sd_value=2.0)
        result = va.classify_compression(
            audit, comparator_class="raid",
            judge="chatgpt", generator="gpt-4o",
        )
        sd_block = result["thresholds_used"]["surprisal_sd"]
        assert sd_block["judge"] == "chatgpt"
        assert sd_block["generator"] == "gpt-4o"

    def test_thresholds_used_surfaces_slice_table(self):
        """The full per-(judge × generator) table is surfaced so
        audit consumers see what other cells would have resolved
        to. Currently None on shipped specs (table is empty in
        1.100.0)."""
        audit = _minimal_audit_with_tier4(surprisal_sd_value=2.0)
        result = va.classify_compression(
            audit, comparator_class="raid",
        )
        sd_block = result["thresholds_used"]["surprisal_sd"]
        assert "direction_by_comparator_and_slice" in sd_block
        # The shipped surprisal_sd spec doesn't have any inner
        # entries in 1.100.0 -- only the per-class field is
        # populated.
        assert sd_block["direction_by_comparator_and_slice"] is None

    def test_judge_generator_default_to_none_in_output(self):
        """When the operator doesn't supply judge / generator,
        both fields appear as None in the output."""
        audit = _minimal_audit_with_tier4(surprisal_sd_value=2.0)
        result = va.classify_compression(
            audit, comparator_class="raid",
        )
        sd_block = result["thresholds_used"]["surprisal_sd"]
        assert sd_block["judge"] is None
        assert sd_block["generator"] is None


# ---------- Shipped registry: tables empty in 1.100.0 ------------


class TestShippedRegistryHasNoSliceEntries:
    """The 1.100.0 release ships the infrastructure with the
    inner-table EMPTY across the entire registry. Once operator
    data on the 13 RAID ``comparator_dependent`` cells settles,
    a follow-up PR populates this table -- no plumbing change
    needed at that point, just data."""

    def test_every_spec_has_slice_table_none(self):
        for signal, spec in va.COMPRESSION_HEURISTICS.items():
            assert spec.direction_by_comparator_and_slice is None, (
                f"signal {signal!r} unexpectedly ships with an inner "
                f"slice override in 1.100.0; if intentional, update "
                f"this test docstring + the CHANGELOG."
            )


# ---------- CLI integration --------------------------------------


def test_variance_audit_cli_exposes_judge_and_generator_flags():
    """``--judge`` + ``--generator`` are wired on the CLI and
    appear in --help. Operators discover the deeper routing
    layer from the help output."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "variance_audit.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "--judge" in result.stdout
    assert "--generator" in result.stdout
    # Help text references the deeper-routing fallback so an
    # operator skimming --help sees what the flags do.
    assert "judge" in result.stdout.lower()
    assert "generator" in result.stdout.lower()
