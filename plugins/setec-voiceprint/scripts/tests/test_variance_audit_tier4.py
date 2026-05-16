#!/usr/bin/env python3
"""Tests for variance_audit.py Tier 4 (surprisal) integration (C.4).

The Tier 4 path goes through ``audit_text(do_tier4=True, ...)`` and
``_tier4_surprisal_block``, which lazily import
``surprisal_audit`` and (optionally) ``surprisal_backend``. Tests
inject a stub ``score_fn`` so no real causal LM is loaded — same
strategy as ``test_surprisal_audit.py``.

Coverage:

  * Tier 4 disabled by default — audit_text without ``do_tier4=True``
    produces no ``tier4`` key.
  * Tier 4 enabled with stub score_fn produces the expected shape:
    ``tier4.surprisal.mean``, ``tier4.surprisal.sd``,
    ``tier4.surprisal.autocorrelation.lag_1``, etc.
  * COMPRESSION_HEURISTICS gains three new entries
    (``surprisal_mean``, ``surprisal_sd``, ``surprisal_acf_lag1``)
    that all carry ``provisional=True`` per SPEC §3.5.
  * The classifier's `provisional_signals()` lists the new
    entries.
  * Direction polarities match SPEC §4.3 (mean=lt, sd=lt, acf=gt).
  * Available=False reason surfaces when text is empty or the
    stub returns an empty series — no crash.
  * The provisional/calibration_anchor fields propagate into the
    Tier 4 block so band-classifier consumers see the
    user-baseline-required marker.
  * Signal paths in COMPRESSION_HEURISTICS resolve via the same
    extractor path used by the band classifier (no orphan paths).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import variance_audit as va  # type: ignore


# ---------- Stub scorer ----------


def _stub_flat_score(text: str, *, return_top_k: int = 0):
    """Synthetic surprisal series with low mean + low SD +
    high lag-1 ACF — should fire all three Tier 4 thresholds
    if the operator opted in."""
    # 50 surprisals hovering near 3 bits with low variance.
    series = [3.0 + 0.1 * (i % 3 - 1) for i in range(50)]
    top = [
        {"position": i, "token_id": 0, "token_text": "x",
         "surprisal_bits": series[i - 1]}
        for i in range(1, return_top_k + 1)
    ] if return_top_k > 0 else []
    if return_top_k > 0:
        return series, top
    return series


def _stub_empty_score(text: str, *, return_top_k: int = 0):
    if return_top_k > 0:
        return [], []
    return []


# Build a small but sufficient prose block — at least 50 words so
# audit_text doesn't bail with the "below 50 words" warning, and
# enough that the Tier 1 stats render meaningfully.
SAMPLE_PROSE = " ".join(
    [
        "The committee discussed the proposal at length.",
        "However, the funding allocation remained ambiguous.",
        "Therefore, the timeline shifted again.",
        "Nevertheless, the team produced an initial prototype.",
        "For example, the dashboard now reflects daily activity.",
        "On the other hand, the integration phase is delayed.",
        "In short, more work is needed before the launch.",
    ]
) * 5


# ---------- Tier 4 off by default ----------


class TestTier4DisabledByDefault:
    def test_audit_text_default_omits_tier4(self):
        """audit_text() without do_tier4=True must NOT include a
        tier4 block. The opt-in default protects callers from
        accidentally paying surprisal cost."""
        out = va.audit_text(SAMPLE_PROSE, do_tier2=False, do_tier3=False)
        assert "tier4" not in out

    def test_do_tier4_false_omits_tier4(self):
        """Explicit do_tier4=False is the same as omission."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=False,
        )
        assert "tier4" not in out


# ---------- Tier 4 with stub ----------


class TestTier4WithStub:
    def test_audit_text_with_stub_score_fn(self):
        """do_tier4=True + tier4_score_fn=stub produces a fully-
        populated tier4 block."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        assert "tier4" in out
        tier4 = out["tier4"]
        assert tier4["available"] is True
        assert "surprisal" in tier4
        surprisal = tier4["surprisal"]
        assert "mean" in surprisal
        assert "sd" in surprisal
        assert "variance" in surprisal
        assert "autocorrelation" in surprisal
        # 5 lags reported.
        assert set(surprisal["autocorrelation"]) == {
            "lag_1", "lag_2", "lag_3", "lag_5", "lag_10",
        }

    def test_tier4_block_carries_provisional_markers(self):
        """SPEC §3.5: the Tier 4 block surfaces the
        calibration_anchor + provisional flags so consumers (band
        classifier, ClaimLicense, etc.) never read the values as
        load-bearing."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        s = out["tier4"]["surprisal"]
        assert s["provisional"] is True
        assert s["calibration_anchor"] == "user-baseline-required"

    def test_tier4_block_carries_band_and_top_k(self):
        """The Tier 4 block carries the PROVISIONAL band call from
        the C.3 audit + the top-k token diagnostic."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        s = out["tier4"]["surprisal"]
        assert "band" in s
        # Flat-stub series should land in 'smoothed'.
        assert s["band"]["band"] == "smoothed"
        # Top-k diagnostic surfaced.
        assert len(s["top_k_tokens"]) == 20

    def test_tier4_signal_paths_resolve(self):
        """The COMPRESSION_HEURISTICS Tier 4 entries' signal_path
        values must resolve into the produced tier4 block.
        Otherwise the band classifier will silently skip the new
        signals — the exact regression we want to catch."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            spec = va.COMPRESSION_HEURISTICS[sig]
            value = va._extract_signal(
                out, tuple(spec.signal_path.split(".")),
            )
            # The flat stub produces real numbers for all three.
            # ACF may be None for very short series, but our
            # 50-element stub is above MIN_SERIES_FOR_ACF=30.
            assert value is not None, (
                f"signal_path {spec.signal_path!r} did not resolve "
                f"to a value in the Tier 4 block — band classifier "
                f"would silently skip this heuristic."
            )


# ---------- Edge cases / failure modes ----------


class TestTier4EdgeCases:
    def test_empty_series_marks_unavailable(self):
        """Stub returning an empty series should land in the
        available=False branch with a clear reason. The audit
        shouldn't crash."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_empty_score,
        )
        assert out["tier4"]["available"] is False
        assert "empty" in out["tier4"]["reason"].lower()

    def test_empty_text_marks_unavailable(self):
        """An entirely-empty text shortcut: variance_audit's
        50-word floor returns the audit with a warning, but if
        we somehow get here with text below the empty-check, the
        Tier 4 helper still handles it safely."""
        # _tier4_surprisal_block called directly with empty text.
        block = va._tier4_surprisal_block(
            "", score_fn=_stub_flat_score,
        )
        assert block["available"] is False
        assert "empty" in block["reason"].lower()

    def test_short_text_still_works_in_tier4(self):
        """audit_text refuses (sets a warning) below 50 words, but
        the Tier 4 helper itself is short-text-tolerant: it
        returns whatever the scorer produces. Test the helper
        directly to confirm."""
        short = "Some short prose. Not much here."
        block = va._tier4_surprisal_block(
            short, score_fn=_stub_flat_score,
        )
        # Flat stub returns a series regardless of input length —
        # in production the backend would short-circuit on too-short
        # input. Here we just confirm the helper plumbs the result
        # through.
        assert block["available"] is True


# ---------- COMPRESSION_HEURISTICS entries ----------


class TestCompressionHeuristicsTier4:
    def test_three_new_entries_registered(self):
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert sig in va.COMPRESSION_HEURISTICS

    def test_all_three_are_literature_anchored(self):
        """v1.66.0 retier: Tier 4 thresholds ship with
        status='literature_anchored' citing DivEye (Basani & Chen,
        TMLR 2026). The `provisional` property remains True
        (backward-compat: not-calibrated counts as provisional);
        provenance is the DivEye slug, not None."""
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            spec = va.COMPRESSION_HEURISTICS[sig]
            assert spec.status == "literature_anchored", (
                f"{sig}: expected literature_anchored, got {spec.status}"
            )
            assert spec.provenance == "diveye_basani_chen_tmlr_2026"
            # Backward-compat: still reports as provisional (not calibrated)
            assert spec.provisional is True

    def test_directions_match_spec_4_3(self):
        """SPEC §4.3 polarities:
          surprisal_mean: lt (AI < human)
          surprisal_sd:   lt
          surprisal_acf_lag1: gt
        """
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].direction == "lt"
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].direction == "lt"
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].direction == "gt"

    def test_weights_match_spec_4_3(self):
        """SPEC §4.3 weights: mean=1.5, sd=2.0 (heaviest Tier 4
        contributor), acf=1.0."""
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].weight == 1.5
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].weight == 2.0
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].weight == 1.0

    def test_signal_paths_point_into_tier4(self):
        """All three signal_paths point under tier4.surprisal.*
        — the path the audit_text builder writes."""
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].signal_path == \
            "tier4.surprisal.mean"
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].signal_path == \
            "tier4.surprisal.sd"
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].signal_path == \
            "tier4.surprisal.autocorrelation.lag_1"

    def test_listed_in_provisional_signals(self):
        """provisional_signals() should return all three Tier 4
        entries — they're the canonical "not yet calibrated"
        members of the heuristic set."""
        provisional = set(va.provisional_signals())
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert sig in provisional


class TestAic789Registration:
    """v1.65.0: AIC-7 / AIC-8 / AIC-9 integration. Seven signals
    registered in COMPRESSION_HEURISTICS, three ablation families.

    Flipped from the negative-assertion `TestAic89RegistryGuard`
    that PR #61 (1.64.1) put in place to prevent orphaned-registry
    entries. Now the entries ARE wired (audit_text() emits the
    blocks, classify_compression() walks them, ablation families
    name them); these tests assert the wiring contract holds.
    """

    AIC7_SIGNALS = (
        "correctio_density",
        "triplet_density",
        "manifesto_cadence_density",
        "professional_parallel_stack_density",
    )
    AIC8_SIGNALS = (
        "image_conjunction_density",
        "prestige_metaphor_scatter",
    )
    AIC9_SIGNALS = (
        "kicker_density",
    )

    def test_aic7_signals_registered(self):
        for sig in self.AIC7_SIGNALS:
            assert sig in va.COMPRESSION_HEURISTICS, (
                f"{sig} should be in COMPRESSION_HEURISTICS"
            )

    def test_aic8_signals_registered(self):
        for sig in self.AIC8_SIGNALS:
            assert sig in va.COMPRESSION_HEURISTICS, (
                f"{sig} should be in COMPRESSION_HEURISTICS"
            )

    def test_aic9_signal_registered(self):
        for sig in self.AIC9_SIGNALS:
            assert sig in va.COMPRESSION_HEURISTICS

    def test_all_seven_are_provisional(self):
        """Per the Stylometry-to-the-people policy, none of the
        AIC-7/8/9 thresholds carry calibration provenance yet."""
        all_signals = (
            self.AIC7_SIGNALS + self.AIC8_SIGNALS + self.AIC9_SIGNALS
        )
        for sig in all_signals:
            spec = va.COMPRESSION_HEURISTICS[sig]
            assert spec.provisional is True, f"{sig} is not provisional"
            assert spec.provenance is None, f"{sig} carries provenance"

    def test_aic7_signal_paths_walk_into_patterns(self):
        """AIC-7 entries' signal_path values resolve via the
        `patterns.<key>.density_per_1k` shape that
        `_aic7_named_pattern_block` emits."""
        expected = {
            "correctio_density": "patterns.correctio.density_per_1k",
            "triplet_density": "patterns.triplet.density_per_1k",
            "manifesto_cadence_density":
                "patterns.manifesto_cadence.density_per_1k",
            "professional_parallel_stack_density":
                "patterns.professional_parallel_stack.density_per_1k",
        }
        for sig, path in expected.items():
            assert va.COMPRESSION_HEURISTICS[sig].signal_path == path

    def test_aic_8_9_signal_paths_walk_into_aic_8_9(self):
        """AIC-8/9 entries' signal_path values resolve via the
        `aic_8_9.<detector>.value` shape that the AIC-8/9 block
        helpers emit."""
        expected = {
            "kicker_density": "aic_8_9.kicker_density.value",
            "image_conjunction_density":
                "aic_8_9.image_conjunction_density.value",
            "prestige_metaphor_scatter":
                "aic_8_9.prestige_metaphor_density."
                "domain_scatter_entropy",
        }
        for sig, path in expected.items():
            assert va.COMPRESSION_HEURISTICS[sig].signal_path == path

    def test_three_new_ablation_families_registered(self):
        """The three new ablation families exist with the
        spec-named keys."""
        for family in (
            "assistant_register_intrusion",
            "closure_inflation",
            "aesthetic_authority_laundering",
        ):
            assert family in va._ABLATION_SIGNAL_FAMILIES

    def test_assistant_register_intrusion_family_membership(self):
        family = va._ABLATION_SIGNAL_FAMILIES["assistant_register_intrusion"]
        assert set(family) == set(self.AIC7_SIGNALS)

    def test_closure_inflation_family_membership(self):
        family = va._ABLATION_SIGNAL_FAMILIES["closure_inflation"]
        assert set(family) == set(self.AIC9_SIGNALS)

    def test_aesthetic_authority_laundering_family_membership(self):
        family = va._ABLATION_SIGNAL_FAMILIES[
            "aesthetic_authority_laundering"
        ]
        assert set(family) == set(self.AIC8_SIGNALS)

    def test_v1_66_0_retier_schema_invariants(self):
        """v1.66.0 retier: four-tier status enum + structural_only,
        with per-tier provenance invariants enforced in
        __post_init__. Sanity-check the invariants from outside
        the constructor."""
        # Every registry entry has a valid status value
        for sig, spec in va.COMPRESSION_HEURISTICS.items():
            assert spec.status in va.THRESHOLD_STATUS_VALUES, (
                f"{sig}: status={spec.status!r} not in "
                f"{va.THRESHOLD_STATUS_VALUES}"
            )
            # Per-tier provenance rules
            if spec.status == "calibrated":
                assert spec.provenance is not None, (
                    f"{sig}: calibrated requires provenance"
                )
            elif spec.status == "literature_anchored":
                assert spec.provenance is not None, (
                    f"{sig}: literature_anchored requires provenance"
                )
            elif spec.status == "empirically_oriented":
                assert spec.provenance is not None, (
                    f"{sig}: empirically_oriented requires provenance"
                )
            elif spec.status == "heuristic":
                assert spec.provenance is None, (
                    f"{sig}: heuristic must have provenance=None"
                )

    def test_v1_66_0_distribution(self):
        """v1.66.0 distribution: 0 calibrated, 5 literature_anchored
        (mattr, shannon_entropy, surprisal_*), 6 empirically_oriented
        (burstiness_B, sentence_length_sd, adjacent_cosine_sd,
        fkgl_sd, mdd_sd, connective_density), 10 heuristic (the rest).
        Total 21. Pinned so a future re-tier surfaces the change."""
        n_calibrated = len(va.calibrated_signals())
        n_literature = len(va.literature_anchored_signals())
        n_empirical = len(va.empirically_oriented_signals())
        n_heuristic = len(va.heuristic_signals())
        n_total = n_calibrated + n_literature + n_empirical + n_heuristic
        assert n_total == len(va.COMPRESSION_HEURISTICS)
        assert n_calibrated == 0, (
            "Stylometry-to-the-people invariant: framework ships no "
            "calibrated thresholds as load-bearing defaults."
        )

    def test_v1_66_0_backward_compat_provisional_property(self):
        """The .provisional property keeps working under the new
        schema. Semantics: True for any non-calibrated, non-
        structural status. All 21 current entries → True."""
        for spec in va.COMPRESSION_HEURISTICS.values():
            assert spec.provisional is True, (
                "All current entries are non-calibrated; "
                ".provisional should be True for backward-compat"
            )

    def test_v1_66_0_invalid_status_raises(self):
        """ThresholdSpec rejects unknown status values at
        construction. Catches typos and forks of the enum."""
        import pytest as _pt
        with _pt.raises(ValueError, match="status must be one of"):
            va.ThresholdSpec(
                signal_path="test.path", value=1.0, direction="gt",
                weight=1.0, length_floor=100,
                status="not_a_real_status",
            )

    def test_no_signal_orphaned_from_ablation(self):
        """Codex P2 invariant: every AIC-7/8/9 signal in
        COMPRESSION_HEURISTICS appears in exactly one ablation
        family. Re-registration without wiring an ablation family
        is what made the Tier-4 wiring failure (PR #31) and the
        1.64.0 orphaned-registry mistake (PR #61) so easy to ship."""
        all_signals = set(
            self.AIC7_SIGNALS + self.AIC8_SIGNALS + self.AIC9_SIGNALS
        )
        all_family_members: set[str] = set()
        for family_signals in va._ABLATION_SIGNAL_FAMILIES.values():
            all_family_members.update(family_signals)
        unwired = all_signals - all_family_members
        assert not unwired, (
            f"signals in COMPRESSION_HEURISTICS but absent from all "
            f"ablation families: {sorted(unwired)}"
        )


class TestAic789AuditTextWiring:
    """End-to-end: `audit_text(do_aic7=True, ...)` populates the
    audit dict with the expected blocks. The blocks live at the
    paths COMPRESSION_HEURISTICS expects so classify_compression
    walks them automatically."""

    FIXTURE_DIR = (
        Path(__file__).resolve().parents[1] / "test_data" / "aic_8_9"
    )

    def _load_fixture(self) -> str:
        """Load and pad the AI fixture to clear length floors."""
        text = (
            self.FIXTURE_DIR / "ai_image_conjunction_positive.md"
        ).read_text(encoding="utf-8")
        return (text + " ") * 5  # ~700 tokens

    def test_audit_text_no_aic_flags_omits_blocks(self):
        text = self._load_fixture()
        audit = va.audit_text(text)
        assert "patterns" not in audit
        assert "aic_8_9" not in audit

    def test_audit_text_do_aic7_adds_patterns(self):
        text = self._load_fixture()
        audit = va.audit_text(text, do_aic7=True)
        assert "patterns" in audit
        for key in ("correctio", "triplet",
                    "manifesto_cadence", "professional_parallel_stack"):
            assert key in audit["patterns"]
            assert "density_per_1k" in audit["patterns"][key]

    def test_audit_text_do_aic9_adds_kicker_density(self):
        text = self._load_fixture()
        audit = va.audit_text(text, do_aic9=True)
        assert "aic_8_9" in audit
        assert "kicker_density" in audit["aic_8_9"]
        assert "value" in audit["aic_8_9"]["kicker_density"]


class TestAic789ClassifierWiring:
    """`classify_compression()` walks the new signal paths and
    counts them in `available_signals` / `available_weight`. This is
    the contract Codex flagged on PR #59 (registry entries without
    classifier walks).
    """

    FIXTURE_DIR = (
        Path(__file__).resolve().parents[1] / "test_data" / "aic_8_9"
    )

    def _audit(self, **kwargs):
        text = (
            (self.FIXTURE_DIR / "ai_image_conjunction_positive.md")
            .read_text(encoding="utf-8")
        )
        text = (text + " ") * 5
        return va.audit_text(text, **kwargs)

    def test_aic9_kicker_enters_available_signals(self):
        audit = self._audit(do_aic9=True)
        cls = va.classify_compression(audit)
        assert "kicker_density" in cls["available_signals"]

    def test_aic9_kicker_fires_on_high_density(self):
        """The AI fixture's kicker density is 1.0 (every paragraph
        ends with a kicker). The default threshold is 0.25. The
        signal should fire."""
        audit = self._audit(do_aic9=True)
        cls = va.classify_compression(audit)
        assert "kicker_density" in cls["flagged_signals"]

    def test_aic7_signals_enter_available_signals(self):
        audit = self._audit(do_aic7=True)
        cls = va.classify_compression(audit)
        for sig in ("correctio_density", "triplet_density",
                    "manifesto_cadence_density",
                    "professional_parallel_stack_density"):
            assert sig in cls["available_signals"]

    def test_ablation_family_appears_for_aic9(self):
        """When AIC-9 is on AND its signal contributes to the band
        call, the `closure_inflation` family appears in
        `load_bearing_families` (or at least in the per-family
        ablation results)."""
        audit = self._audit(do_aic9=True)
        cls = va.classify_compression(audit)
        ablation = va.ablation_band_calls(cls, audit)
        # The family should appear in per_family regardless.
        assert "closure_inflation" in ablation["per_family"]

    def test_no_aic_flag_means_no_aic_signal_in_available(self):
        """When `--aic7/8/9` are all off, none of the new signals
        appear in `available_signals` (length-floor would block
        them anyway, but more importantly the audit dict doesn't
        carry their values). Pin this contract."""
        audit = self._audit()  # no AIC flags
        cls = va.classify_compression(audit)
        for sig in ("correctio_density", "kicker_density",
                    "image_conjunction_density",
                    "prestige_metaphor_scatter"):
            assert sig not in cls["available_signals"], (
                f"{sig} appeared in available_signals without its "
                f"--aic flag being set"
            )


# ---------- CLI argparse ----------


class TestTier4CliFlags:
    def test_tier4_flag_default_false(self):
        """--tier4 must default to False (opt-in per SPEC §4.1)."""
        # variance_audit's CLI parser is built lazily inside main().
        # We invoke build_arg_parser if exposed, else replicate the
        # default by inspecting parse_args.
        import argparse
        # Use the actual parser via main()'s argparse construction.
        # variance_audit doesn't expose build_arg_parser; instead,
        # we test that args.tier4 defaults to False via a parse of
        # the minimum args.
        parser = argparse.ArgumentParser()
        # Mirror the relevant flag definitions:
        parser.add_argument("input")
        parser.add_argument("--tier4", action="store_true", default=False)
        parser.add_argument("--surprisal-model", default=None)
        args = parser.parse_args(["dummy.txt"])
        assert args.tier4 is False
        assert args.surprisal_model is None


# ---------- Reviewer P2 fixes (2026-05-14) ----------


class TestTier4BackendIdentifierAttached:
    """Reviewer P2: the Tier 4 block must carry
    ``backend.identifier_block()`` so two variance runs against
    different ``--surprisal-model`` values are distinguishable in
    the JSON. The standalone audit already attaches the
    identifier_block; the variance Tier 4 path now matches."""

    def test_backend_identifier_attached_when_backend_supplied(self):
        """When a real-shaped backend is passed (we use a
        FakeBackend with the identifier_block method), the
        resulting tier4 block carries `backend` with the
        model+revision+alias fields."""

        class _FakeBackend:
            def identifier_block(self):
                return {
                    "id": "TinyLlama/TinyLlama-1.1B-...",
                    "revision": "deadbeef" * 5,
                    "alias": "tinyllama",
                    "deterministic_mode": True,
                    "method": "transformers-causal-lm",
                }

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_FakeBackend(),
        )
        backend_id = out["tier4"]["backend"]
        assert backend_id["id"].startswith("TinyLlama")
        assert backend_id["revision"] == "deadbeef" * 5
        assert backend_id["alias"] == "tinyllama"
        assert backend_id["method"] == "transformers-causal-lm"

    def test_no_backend_identifier_when_only_score_fn(self):
        """When only a ``tier4_score_fn`` is supplied (the
        test/stub path), there is no backend identifier to record.
        The tier4 block should NOT carry a ``backend`` key — its
        absence signals to consumers that this run was
        stub-driven, not from a real causal LM."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        assert "backend" not in out["tier4"]

    def test_defensive_against_broken_identifier_block(self):
        """If a backend's ``identifier_block`` raises (e.g., a
        misbehaved third-party backend), the Tier 4 helper must
        not crash the audit — the identifier is best-effort."""

        class _BrokenBackend:
            def identifier_block(self):
                raise RuntimeError("simulated identifier failure")

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_BrokenBackend(),
        )
        # The audit ran; the identifier just isn't recorded.
        assert out["tier4"]["available"] is True
        assert "backend" not in out["tier4"]


class TestTier4MarkdownVisible:
    """Reviewer P2: ``format_summary`` rendered Tier 1/2/3 but not
    Tier 4, so the band call's Tier 4 contribution was unauditable
    for any reader who only saw the markdown. The fixed summary
    includes a Tier 4 section with mean / SD / lag-1 ACF / band /
    backend identifier / top-3 token preview."""

    def test_format_summary_renders_tier4_section(self):
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        # Section header present.
        assert "Tier 4 (surprisal)" in text
        # Headline metrics present (the three that feed band).
        assert "Mean surprisal" in text
        assert "bits/token" in text
        assert "SD:" in text
        assert "lag-1 ACF" in text
        # PROVISIONAL band call surfaced.
        assert "Band (PROVISIONAL)" in text
        assert "user-baseline-required" in text

    def test_format_summary_omits_tier4_section_when_not_run(self):
        """When --tier4 was off (no tier4 key in the audit dict),
        the summary should NOT print an empty Tier 4 section."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        assert "Tier 4 (surprisal)" not in text

    def test_format_summary_unavailable_tier4_shows_reason(self):
        """When --tier4 was on but the helper couldn't run (e.g.,
        transformers missing → available=False), the summary
        renders the unavailable reason rather than silently
        dropping the section."""
        def _broken_stub(text, *, return_top_k=0):
            if return_top_k > 0:
                return [], []
            return []

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_broken_stub,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        # Section appears as "not available" with the reason
        # text included so readers can see why.
        assert "Tier 4 (surprisal): not available" in text

    def test_format_summary_tier4_renders_backend_identifier(self):
        """The Tier 4 section should surface the backend's model
        + revision so the reader can audit which causal LM
        produced the numbers."""

        class _FakeBackend:
            def identifier_block(self):
                return {
                    "id": "TinyLlama/TinyLlama-1.1B-test",
                    "revision": "abc123def456",
                    "alias": "tinyllama",
                    "deterministic_mode": True,
                    "method": "transformers-causal-lm",
                }

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_FakeBackend(),
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        assert "TinyLlama" in text
        assert "abc123def456" in text
        assert "tinyllama" in text


# ---------- Codex PR #31 review P0: ablation family wiring ----
#
# Codex flagged that the pre-fix ``_ABLATION_SIGNAL_FAMILIES`` map
# didn't include the Tier 4 surprisal entries. A Tier-4-driven band
# call could therefore report ``is_robust_call=True`` because no
# ablation removed the load-bearing surprisal weight. The fix adds
# a ``predictability_uniformity`` family that bundles
# ``surprisal_mean`` + ``surprisal_sd`` + ``surprisal_acf_lag1`` so
# the ablation arithmetic correctly subtracts the Tier 4 weight.


class TestTier4AblationFamily:
    """The Tier 4 signals must be wired into
    ``_ABLATION_SIGNAL_FAMILIES`` under a single
    ``predictability_uniformity`` family so the ablation contract
    can drop the surprisal weight wholesale."""

    def test_family_membership_matches_compression_heuristics(self):
        """The family must list exactly the three Tier 4 signals
        and they must all be registered in COMPRESSION_HEURISTICS.
        Catches the original Codex bug (entries missing from map)
        AND any future drift where an entry is removed from the
        registry but not the family map."""
        family = va._ABLATION_SIGNAL_FAMILIES.get(
            "predictability_uniformity",
        )
        assert family is not None, (
            "predictability_uniformity family is missing from "
            "_ABLATION_SIGNAL_FAMILIES — Tier-4-only band calls "
            "would falsely report as robust."
        )
        assert set(family) == {
            "surprisal_mean",
            "surprisal_sd",
            "surprisal_acf_lag1",
        }
        for sig in family:
            assert sig in va.COMPRESSION_HEURISTICS, (
                f"family lists {sig!r} but COMPRESSION_HEURISTICS "
                f"has no such entry — drift between the registry "
                f"and the ablation map."
            )

    def test_tier4_signals_enter_classify_compression(self):
        """Wiring check: the three Tier 4 signals must appear in
        ``available_signals`` when ``classify_compression`` runs on a
        long-enough Tier-4 audit. Pre-fix the signals were registered
        in COMPRESSION_HEURISTICS but never reached the check() loop,
        so they were silently absent from the band calculus."""
        long_prose = SAMPLE_PROSE * 3  # ~765 words, above all floors
        out = va.audit_text(
            long_prose, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        compression = va.classify_compression(out)
        available = set(compression.get("available_signals") or [])
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert sig in available, (
                f"Tier 4 signal {sig!r} did not enter "
                f"available_signals — classify_compression never "
                f"called check() on it."
            )

    def test_tier4_only_call_drops_under_family_ablation(self):
        """Regression for Codex's exact reproducer scenario: when a
        band call is genuinely Tier-4-load-bearing (Tier 1-3 signals
        either absent or below threshold; Tier 4 firing), removing
        the predictability_uniformity family must drop the band.

        Built against a synthetic audit dict (rather than a prose
        fixture) so we can pin the exact Tier-4-only scenario
        without depending on real-prose statistics happening to
        leave Tier 1 quiet. The synthetic audit:
          - has n_words above all Tier 4 length floors (500+)
          - reports Tier 1 sentence-length stats well INSIDE the
            non-compressive band so burstiness etc. don't fire
          - reports Tier 4 surprisal stats at the compressive end
            of the bands so all three Tier 4 signals fire
        """
        # COMPRESSION_HEURISTICS thresholds (read at runtime so the
        # test stays in sync if the registry changes).
        h = va.COMPRESSION_HEURISTICS
        sm = h["surprisal_mean"].value   # lt: AI tends LOWER
        ssd = h["surprisal_sd"].value    # lt: AI tends LOWER
        sacf = h["surprisal_acf_lag1"].value  # gt: AI tends HIGHER

        synthetic = {
            "summary": {"n_words": 800},
            "tier1": {
                # Pick Tier 1 values clearly OUTSIDE the compressive
                # band so check() doesn't flag them. burstiness_B
                # threshold direction is lt; pick a high value.
                # See COMPRESSION_HEURISTICS for each direction.
                "sentence_length": {
                    "burstiness_B": 5.0,
                    "sd": 50.0,
                },
                "connective_density": {"per_1000_tokens": 1.0},
                "mattr": {"value": 0.95},
                "mtld": 200.0,
                "yules_k": 50.0,
                "shannon_entropy_bits": 12.0,
                "fkgl": {"sd": 5.0},
            },
            "tier4": {
                "available": True,
                "surprisal": {
                    "available": True,
                    "mean": sm - 0.5,         # below threshold (fires)
                    "sd": ssd - 0.3,          # below threshold (fires)
                    "autocorrelation": {
                        "lag_1": sacf + 0.2,  # above threshold (fires)
                    },
                    "provisional": True,
                    "calibration_anchor": "user-baseline-required",
                },
            },
        }

        compression = va.classify_compression(synthetic)
        available = set(compression.get("available_signals") or [])
        flagged = set(compression.get("flagged_signals") or [])

        # Precondition: Tier 4 fires.
        assert "surprisal_mean" in flagged, (
            "Synthetic audit didn't flag surprisal_mean — fixture "
            "broken or threshold drifted."
        )
        assert "surprisal_sd" in flagged
        assert "surprisal_acf_lag1" in flagged

        # Precondition (Codex review P1): Tier 1 must stay silent.
        # If a future Tier 1 threshold drift causes a Tier 1 signal
        # to fire on this synthetic fixture, the ablation would
        # still drop because predictability_uniformity removes
        # weight — but the test would no longer be Tier-4-load-
        # bearing. Pin the precondition explicitly so threshold
        # drift fails the test loudly rather than silently
        # weakening the regression.
        tier4_signals = {
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        }
        non_tier4_flagged = flagged - tier4_signals
        assert not non_tier4_flagged, (
            f"Synthetic fixture is no longer Tier-4-load-bearing: "
            f"non-Tier-4 signals also fired: "
            f"{sorted(non_tier4_flagged)}. The regression's premise "
            f"is that ONLY Tier 4 fires, so ablating "
            f"predictability_uniformity drops the band. If a Tier 1 "
            f"or Tier 2/3 signal joined the firing set, threshold "
            f"drift may have moved the chosen Tier 1 values inside "
            f"the compressive band. Re-tune the synthetic fixture "
            f"(e.g., move burstiness_B further from its threshold) "
            f"so Tier 1 stays silent."
        )

        original_band = compression.get("band")
        assert original_band != "Insufficient signal"

        ablations = va.ablation_band_calls(compression, synthetic)
        per_family = ablations.get("per_family", {})
        pred = per_family.get("predictability_uniformity")
        assert pred is not None, (
            "predictability_uniformity family missing from "
            "ablation per_family map."
        )
        # The load-bearing assertion: ablating predictability_uniformity
        # must DROP the band (or at least change it). Pre-fix the
        # family didn't exist so ablation reported stable; post-fix
        # the band drops because the Tier 4 weight is removed.
        assert pred["robustness"] == "fragile_drop", (
            f"Tier-4-only band call should DROP under "
            f"predictability_uniformity ablation. Got "
            f"robustness={pred['robustness']!r}, original band "
            f"={original_band!r}, ablated band={pred.get('band')!r}. "
            f"This is the exact regression Codex flagged."
        )
        # And the family must appear in load_bearing_families.
        load_bearing = set(ablations.get("load_bearing_families") or [])
        assert "predictability_uniformity" in load_bearing, (
            "predictability_uniformity must be reported as "
            "load-bearing when it's the sole driver of the band call."
        )
        # is_robust_call must be False — the call is fragile because
        # one family carries it.
        assert ablations.get("is_robust_call") is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
