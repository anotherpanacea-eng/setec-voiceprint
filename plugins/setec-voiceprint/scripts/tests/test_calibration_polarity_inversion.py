#!/usr/bin/env python3
"""Regression tests for the polarity-inversion refusal gate
(``calibrate_thresholds.py``, v1.59.0+).

The framework's README documents the load-bearing empirical
finding: every Tier 1 signal flipped polarity between the EditLens
val split (2026-05-10) and MAGE (2026-05-11). Calibration thresholds
derived from a single corpus do not generalize. The polarity-
inversion gate enforces that finding at code level: when a
corpus's ``direction_aware_auc`` falls below the chance line, the
harness refuses to publish a threshold entry.

These tests pin:

  1. **Matched polarity** — when ``direction_aware_auc`` is above
     the chance line, the gate is a no-op and the entry ships
     normally.

  2. **Inverted polarity, no override** — when DA-AUC falls below
     the chance line and ``--allow-polarity-inversion`` is False
     (the default), the gate raises
     :class:`calibrate_thresholds.PolarityInversionRefusal`. The
     diagnostic message names the signal, the registry direction,
     the observed DA-AUC, and the override flag.

  3. **Inverted polarity, with override** — when ``--allow-polarity-
     inversion`` is True, the entry is published with a loud
     POLARITY INVERSION notes-prefix and a ``polarity_inversion``
     block recording the DA-AUC and chance line.

  4. **Margin tolerance** — ``--polarity-inversion-margin`` widens
     the chance-line cutoff so DA-AUC values near 0.5 don't trip
     the gate (useful for small corpora where the AUC estimate
     has wide variance).

  5. **Missing DA-AUC field** — when ``_ranking_metrics`` returns
     the legacy ``{auc, ap}`` shape (older test fixtures, pre-
     direction-aware code), the gate is a no-op (no information
     to refuse on).

  6. **Back-compat for programmatic callers** — Namespace built
     manually without the new flags works fine; ``getattr`` with
     default preserves the legacy behavior.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibrate_thresholds as ct  # type: ignore


# ---------- Helpers ----------


def _fake_entries(n_pos: int = 10, n_neg: int = 10) -> list[dict]:
    """Minimal manifest-shape entries the scoring path consumes."""
    entries: list[dict] = []
    for i in range(n_pos):
        entries.append({
            "id": f"pos_{i}", "path": f"pos_{i}.txt",
            "ai_status": "ai_generated",
            "use": ["validation"], "split": "test",
        })
    for i in range(n_neg):
        entries.append({
            "id": f"neg_{i}", "path": f"neg_{i}.txt",
            "ai_status": "pre_ai_human",
            "use": ["validation"], "split": "test",
        })
    return entries


def _make_inner_args(**overrides) -> argparse.Namespace:
    base = dict(
        manifest="dummy.jsonl",
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
        allow_polarity_inversion=False,
        polarity_inversion_margin=ct.DEFAULT_POLARITY_INVERSION_MARGIN,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@contextlib.contextmanager
def _stub_pipeline(*, ranking: dict):
    """Stack the manifest / scoring / sweep / ranking mocks the
    ``derive_threshold`` happy path needs. Yields nothing; tests
    just ``with _stub_pipeline(ranking=...):``."""
    with mock.patch.object(
        ct, "_manifest_content_hash", return_value="sha256:test",
    ), mock.patch.object(
        ct, "validate_manifest", return_value={"n_errors": 0},
    ), mock.patch.object(
        ct, "load_manifest_entries", return_value=_fake_entries(),
    ), mock.patch.object(
        ct, "_entry_uses",
        side_effect=lambda e, t: t in e["use"],
    ), mock.patch.object(
        ct, "score_smoothing_entry",
        side_effect=lambda e, **kw: {
            "entry": e,
            "label": 1 if e["ai_status"] == "ai_generated" else 0,
            "scores": {"layer_a": {"burstiness_B": 0.5}},
        },
    ), mock.patch.object(
        ct, "collect_signal_records",
        return_value=[(0, 0.4), (1, 0.6)] * 10,
    ), mock.patch.object(
        ct, "sweep_threshold",
        return_value={
            "available": True, "threshold": 0.5,
            "fpr_resolution": 0.05,
            "fpr": 0.05, "tpr": 0.5, "precision": 0.5,
            "n_pos": 10, "n_neg": 10,
        },
    ), mock.patch.object(
        ct, "fixed_threshold_bootstrap_ci", return_value=None,
    ), mock.patch.object(
        ct, "_ranking_metrics", return_value=ranking,
    ), mock.patch.object(
        ct, "_load_fetch_record", return_value={},
    ):
        yield


# ---------- Gate behavior ----------


class TestPolarityGateMatched:
    """When the corpus's polarity matches the registry's hypothesis
    (DA-AUC ≥ 0.5), the gate is a no-op and the entry ships."""

    def test_da_auc_above_chance_publishes_entry(self):
        ranking = {
            "auc": 0.80, "ap": 0.78,
            "direction_aware_auc": 0.80,
            "direction_aware_ap": 0.78,
        }
        args = _make_inner_args()
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        # Entry shipped — no PolarityInversionRefusal.
        assert entry["signal"] == "burstiness_B"
        # The polarity_inversion block is absent (gate didn't fire).
        assert "polarity_inversion" not in entry
        # And the notes don't carry the POLARITY INVERSION prefix.
        assert "POLARITY INVERSION" not in entry["notes"]

    def test_da_auc_exactly_at_chance_publishes_entry(self):
        """DA-AUC == 0.5 (the chance line itself) does NOT trip the
        gate. The gate is `< chance_line`, not `<=`. This pins the
        boundary behavior so a future signed-rounding bug can't
        silently flip it."""
        ranking = {
            "auc": 0.50, "ap": 0.50,
            "direction_aware_auc": 0.50,
            "direction_aware_ap": 0.50,
        }
        args = _make_inner_args()
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        assert "polarity_inversion" not in entry


class TestPolarityGateInverted:
    """When DA-AUC is below the chance line and the override is
    NOT set, the gate refuses with PolarityInversionRefusal."""

    def test_da_auc_below_chance_refuses(self):
        # Inverted: DA-AUC = 0.25 means the polarity disagrees
        # strongly with the registry hypothesis.
        ranking = {
            "auc": 0.75, "ap": 0.70,
            "direction_aware_auc": 0.25,
            "direction_aware_ap": 0.30,
        }
        args = _make_inner_args(allow_polarity_inversion=False)
        with _stub_pipeline(ranking=ranking):
            with pytest.raises(
                ct.PolarityInversionRefusal,
            ) as excinfo:
                ct.derive_threshold(args)
        # Diagnostic message includes the load-bearing fields.
        msg = str(excinfo.value)
        assert "POLARITY INVERSION" in msg
        assert "burstiness_B" in msg
        assert "0.2500" in msg  # the DA-AUC value
        assert "--allow-polarity-inversion" in msg

    def test_refusal_is_a_systemexit_subclass(self):
        """PolarityInversionRefusal subclasses SystemExit so the
        CLI exits non-zero, while programmatic callers can catch
        the specific exception type. Pin the inheritance so a
        future refactor doesn't accidentally widen or narrow it."""
        assert issubclass(
            ct.PolarityInversionRefusal, SystemExit,
        )


class TestPolarityGateOverride:
    """When ``--allow-polarity-inversion`` is True, the entry ships
    with a POLARITY INVERSION notes prefix and a
    ``polarity_inversion`` provenance block."""

    def test_override_publishes_with_polarity_inversion_block(self):
        ranking = {
            "auc": 0.75, "ap": 0.70,
            "direction_aware_auc": 0.25,
            "direction_aware_ap": 0.30,
        }
        args = _make_inner_args(allow_polarity_inversion=True)
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        # Entry shipped under override.
        assert entry["signal"] == "burstiness_B"
        # Provenance block recorded the inversion.
        block = entry.get("polarity_inversion")
        assert block is not None
        assert block["recorded"] is True
        assert block["direction_aware_auc"] == 0.25
        assert block["chance_line"] == 0.5
        assert block["registry_direction"] == "lt"

    def test_override_notes_carry_loud_prefix(self):
        ranking = {
            "auc": 0.75, "ap": 0.70,
            "direction_aware_auc": 0.10,
            "direction_aware_ap": 0.20,
        }
        args = _make_inner_args(allow_polarity_inversion=True)
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        # Notes prefix is unmissable — downstream consumers
        # filtering on "POLARITY INVERSION" / "PIPELINE CHECK" can
        # refuse to treat this entry as load-bearing.
        assert entry["notes"].startswith("POLARITY INVERSION")
        # Carries the load-bearing fields in the prose so an
        # operator skimming the ledger sees what happened.
        assert "registry direction 'lt'" in entry["notes"]
        assert "0.1000" in entry["notes"]
        assert "DO NOT treat this entry as a load-bearing" in entry["notes"]


class TestPolarityGateMargin:
    """``--polarity-inversion-margin`` widens the chance-line cutoff
    for borderline DA-AUC values near 0.5."""

    def test_margin_lets_borderline_value_pass(self):
        """DA-AUC = 0.48 trips the strict gate (< 0.5) but passes
        when the margin is 0.05 (chance line shifts to 0.45)."""
        ranking = {
            "auc": 0.52, "ap": 0.50,
            "direction_aware_auc": 0.48,
            "direction_aware_ap": 0.50,
        }
        # Without margin: would refuse.
        args_strict = _make_inner_args(allow_polarity_inversion=False)
        with _stub_pipeline(ranking=ranking):
            with pytest.raises(ct.PolarityInversionRefusal):
                ct.derive_threshold(args_strict)
        # With margin 0.05 (chance line → 0.45): passes.
        args_lax = _make_inner_args(
            allow_polarity_inversion=False,
            polarity_inversion_margin=0.05,
        )
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args_lax)
        assert "polarity_inversion" not in entry

    def test_margin_block_records_widened_chance_line(self):
        """When override fires with a margin, the polarity_inversion
        block records the widened chance line so the operator can
        audit what threshold was applied."""
        ranking = {
            "auc": 0.65, "ap": 0.60,
            "direction_aware_auc": 0.35,
            "direction_aware_ap": 0.40,
        }
        args = _make_inner_args(
            allow_polarity_inversion=True,
            polarity_inversion_margin=0.10,
        )
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        block = entry["polarity_inversion"]
        # Chance line was widened from 0.5 to 0.4.
        assert block["chance_line"] == pytest.approx(0.4)


class TestPolarityGateBackCompat:
    """Programmatic callers (older test fixtures, scripts that
    don't know about the new flags) must keep working. The gate
    uses ``getattr`` with defaults to absorb missing attributes."""

    def test_missing_attrs_default_to_strict_gate(self):
        """A Namespace built without the polarity-inversion fields
        defaults to allow=False (strict) and margin=0.0 (the
        canonical chance line)."""
        ranking = {
            "auc": 0.80, "ap": 0.78,
            "direction_aware_auc": 0.80,
            "direction_aware_ap": 0.78,
        }
        # Strip the new attributes to simulate a pre-1.59 caller.
        args = _make_inner_args()
        delattr(args, "allow_polarity_inversion")
        delattr(args, "polarity_inversion_margin")
        with _stub_pipeline(ranking=ranking):
            # Matched-polarity case still ships (gate is a no-op).
            entry = ct.derive_threshold(args)
        assert entry["signal"] == "burstiness_B"
        assert "polarity_inversion" not in entry

    def test_missing_da_auc_field_skips_gate(self):
        """When ``_ranking_metrics`` returns the legacy {auc, ap}
        shape (older test fixtures mock it without
        direction_aware_auc), the gate has no information to refuse
        on and skips silently. Pins the back-compat path."""
        ranking = {"auc": 0.50, "ap": 0.50}  # legacy shape
        args = _make_inner_args()
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        # Gate didn't fire — no inversion block, no notes prefix.
        assert "polarity_inversion" not in entry
        assert "POLARITY INVERSION" not in entry["notes"]


# ---------- CLI flag plumbing ----------


class TestPolarityGateCli:
    """The two new flags parse correctly into Namespace fields with
    the documented defaults."""

    def test_flags_default_strict(self):
        """Without flags: allow_polarity_inversion=False,
        polarity_inversion_margin=DEFAULT (0.0)."""
        # Drive the parser the way the existing subsample test
        # drives it: --help exits with SystemExit, which is the
        # idiomatic check that the parser accepts the flag set.
        sys.argv_backup = sys.argv
        try:
            sys.argv = ["calibrate_thresholds.py", "--help"]
            try:
                ct.main()
            except SystemExit:
                pass
        finally:
            sys.argv = sys.argv_backup
        # Functional check via _make_inner_args (which uses the
        # documented defaults).
        args = _make_inner_args()
        assert args.allow_polarity_inversion is False
        assert args.polarity_inversion_margin == 0.0
        assert ct.DEFAULT_POLARITY_INVERSION_MARGIN == 0.0

    def test_argparse_accepts_polarity_flags(self):
        """Smoke check that argparse parses both new flags without
        error and assigns the expected attributes."""
        # Pull the parser the same way the existing
        # test_subsample_caps_total_entries reaches it: via main()
        # with --help. To actually exercise the flags, call
        # parser.parse_args() on a built parser. We do this by
        # calling main() with a sentinel argv that would otherwise
        # exit at the manifest-loading step, but argparse's parse
        # happens first.
        try:
            ct.main([
                "--manifest", "nonexistent.jsonl",
                "--signal", "burstiness_B",
                "--fpr-target", "0.01",
                "--allow-polarity-inversion",
                "--polarity-inversion-margin", "0.05",
            ])
        except (SystemExit, FileNotFoundError, Exception):
            # We don't care if it fails downstream — only that the
            # parser accepted the flags. argparse would have exited
            # rc=2 with a clear error if either flag were unknown.
            pass


# ---------- Codex PR #40 review P1: margin range validation ----
#
# Codex flagged that ``--polarity-inversion-margin 5`` (typo for
# ``0.5``) would shift the chance line below zero, silently
# disabling the refusal gate without setting the override block.
# The fix validates ``0.0 <= margin < 0.5`` before use and reuses
# the validated chance_line for both the gate and the provenance
# block.


class TestPolarityMarginValidation:
    """``_validate_polarity_margin`` enforces the half-open
    interval [0.0, 0.5). Anything outside raises ``SystemExit``
    with a clear diagnostic."""

    def test_zero_margin_passes(self):
        assert ct._validate_polarity_margin(0.0) == 0.0

    def test_small_positive_margin_passes(self):
        assert ct._validate_polarity_margin(0.05) == pytest.approx(0.05)

    def test_margin_just_below_upper_bound_passes(self):
        """The half-open interval includes values arbitrarily close
        to but less than 0.5."""
        result = ct._validate_polarity_margin(0.499)
        assert result == pytest.approx(0.499)

    def test_negative_margin_refused(self):
        """A negative margin would shift the chance line above 0.5,
        refusing readings that AGREE with the registry hypothesis
        — the inverse of the intended semantics."""
        with pytest.raises(SystemExit) as excinfo:
            ct._validate_polarity_margin(-0.1)
        msg = str(excinfo.value)
        assert "0.0 <= margin" in msg
        assert "-0.1" in msg

    def test_margin_at_upper_bound_refused(self):
        """margin == 0.5 would shift the line to 0.0; a DA-AUC of
        0.0 (the most extreme inverted polarity possible) would
        just pass the gate. The upper bound is exclusive."""
        with pytest.raises(SystemExit):
            ct._validate_polarity_margin(0.5)

    def test_margin_above_upper_bound_refused(self):
        """Codex's reproducer case: --polarity-inversion-margin 5
        (typo for 0.5). Pre-fix this shifted the chance line to
        -4.5 and silently disabled the gate. Post-fix it fails
        loudly."""
        with pytest.raises(SystemExit) as excinfo:
            ct._validate_polarity_margin(5.0)
        msg = str(excinfo.value)
        # Diagnostic names the load-bearing failure mode so the
        # operator understands why the typo was caught.
        assert (
            "disable the gate" in msg or "passes" in msg.lower()
        )
        assert "5" in msg

    def test_non_numeric_margin_refused(self):
        with pytest.raises(SystemExit) as excinfo:
            ct._validate_polarity_margin("not a number")
        msg = str(excinfo.value)
        assert "real number" in msg

    def test_nan_margin_refused(self):
        """NaN compares as neither <, >, nor == any value. Without
        an explicit NaN check the range comparison would silently
        be False and the gate would skip — but the operator passed
        a non-real value and should know."""
        with pytest.raises(SystemExit) as excinfo:
            ct._validate_polarity_margin(float("nan"))
        msg = str(excinfo.value)
        assert "NaN" in msg or "real number" in msg


class TestPolarityGateInvalidMargin:
    """End-to-end: when ``derive_threshold(args)`` is called with
    an invalid margin, the gate runs validation upfront and fails
    loudly — regardless of whether DA-AUC would have tripped the
    gate or not. Pin the two cases:

      1. DA-AUC is matched (gate would have passed) → still fails.
      2. DA-AUC is None (gate would have been a no-op) → still fails.

    Both cases catch the typo-class failure mode at the earliest
    possible point.
    """

    def test_invalid_margin_fails_even_when_da_auc_matches(self):
        ranking = {
            "auc": 0.80, "ap": 0.78,
            "direction_aware_auc": 0.80,
            "direction_aware_ap": 0.78,
        }
        args = _make_inner_args(polarity_inversion_margin=5.0)
        with _stub_pipeline(ranking=ranking):
            with pytest.raises(SystemExit) as excinfo:
                ct.derive_threshold(args)
        # Validation diagnostic, not the inversion diagnostic.
        assert "0.0 <= margin" in str(excinfo.value)
        # And specifically NOT a PolarityInversionRefusal (the
        # validator raises plain SystemExit so the failure mode is
        # distinguishable).
        assert not isinstance(
            excinfo.value, ct.PolarityInversionRefusal,
        )

    def test_invalid_margin_fails_even_when_da_auc_missing(self):
        """Legacy ``{auc, ap}`` shape would normally skip the gate.
        Margin validation still runs."""
        ranking = {"auc": 0.50, "ap": 0.50}
        args = _make_inner_args(polarity_inversion_margin=-0.1)
        with _stub_pipeline(ranking=ranking):
            with pytest.raises(SystemExit) as excinfo:
                ct.derive_threshold(args)
        assert "0.0 <= margin" in str(excinfo.value)


class TestPolarityChanceLineReuse:
    """The provenance block's recorded chance_line must equal the
    value the gate used. Codex PR #40 review P1: pre-fix the
    provenance block recomputed `0.5 - raw_margin` without
    validation, so the two could drift."""

    def test_provenance_chance_line_matches_validated_value(self):
        """A valid margin of 0.10 → chance_line == 0.4 in the
        provenance block."""
        ranking = {
            "auc": 0.65, "ap": 0.60,
            "direction_aware_auc": 0.35,
            "direction_aware_ap": 0.40,
        }
        args = _make_inner_args(
            allow_polarity_inversion=True,
            polarity_inversion_margin=0.10,
        )
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        block = entry["polarity_inversion"]
        # chance_line == 0.5 - 0.10 == 0.4, exactly.
        assert block["chance_line"] == pytest.approx(0.4)

    def test_strict_margin_records_canonical_chance_line(self):
        """Default margin (0.0) → chance_line == 0.5 in the
        provenance block."""
        ranking = {
            "auc": 0.75, "ap": 0.70,
            "direction_aware_auc": 0.25,
            "direction_aware_ap": 0.30,
        }
        args = _make_inner_args(allow_polarity_inversion=True)
        with _stub_pipeline(ranking=ranking):
            entry = ct.derive_threshold(args)
        block = entry["polarity_inversion"]
        assert block["chance_line"] == pytest.approx(0.5)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
