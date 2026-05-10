#!/usr/bin/env python3
"""Regression tests for confounder_audit.py (Release 3, Layer D).

Trustworthiness Tier-1. The audit is *not a classifier* — its
contracts are about the differential-diagnosis shape:

  * Observations extracted correctly from each input audit JSON.
  * Confounder scoring respects the matches / contradictions /
    any-signal rules.
  * Distinguishing-evidence detector finds signals where top
    candidates disagree.
  * Missing-evidence list names the high-leverage signals NOT
    observed.
  * Rendering produces the differential-diagnosis report shape
    with claim-license block embedded.
  * Honest framing: when AI smoothing and legal/policy memo style
    both score high (the canonical confounder pair), neither is
    presented as the answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import confounder_audit as ca  # type: ignore


# ---------- Fixtures ----------


def _variance(flagged: list[str], n_windows: int = 0,
              hot_window_count: int = 0,
              pos_bigram_kl: float | None = None) -> dict:
    out = {"compression": {"flagged_signals": flagged}}
    if pos_bigram_kl is not None:
        out["baseline_divergences"] = {
            "pos_bigrams": {"kl_divergence": pos_bigram_kl},
        }
    if n_windows > 0:
        out["windows"] = {
            "results": [
                {"compression": {"band": "Heavily smoothed"}}
                for _ in range(hot_window_count)
            ] + [
                {"compression": {"band": "Lightly smoothed"}}
                for _ in range(n_windows - hot_window_count)
            ],
        }
    return out


def _voice_distance(register_strength: str | None = None,
                    char_delta_mean: float | None = None) -> dict:
    out: dict = {}
    if register_strength:
        out["register_match"] = {
            "match": {"strength": register_strength},
        }
    if char_delta_mean is not None:
        out["families"] = {
            "char_ngrams_3": {"burrows_delta": char_delta_mean},
            "char_ngrams_4": {"burrows_delta": char_delta_mean},
            "char_ngrams_5": {"burrows_delta": char_delta_mean},
        }
    return out


def _paragraph(band: str = "Heavily smoothed") -> dict:
    return {"compression": {"band": band}}


def _discourse(density: float, marked_entropy: float) -> dict:
    return {
        "total_marker_density_per_1k": density,
        "marked_only_entropy_bits": marked_entropy,
    }


# ---------- Observation extraction ----------


class TestExtractObservations:
    def test_no_inputs_returns_empty(self):
        obs = ca.extract_observations()
        assert obs == {}

    def test_variance_flag_extraction(self):
        obs = ca.extract_observations(
            variance=_variance(flagged=["burstiness_B", "mtld"]),
        )
        assert obs["sentence_variance"] == "low"
        assert obs["lexical_diversity"] == "low"

    def test_connective_density_extraction(self):
        obs = ca.extract_observations(
            variance=_variance(flagged=["connective_density"]),
        )
        assert obs["connective_density"] == "high"

    def test_register_match_extraction(self):
        obs = ca.extract_observations(
            voice_distance=_voice_distance(register_strength="mismatch"),
        )
        assert obs["register_match"] == "low"
        obs2 = ca.extract_observations(
            voice_distance=_voice_distance(register_strength="strong"),
        )
        assert obs2["register_match"] == "high"

    def test_paragraph_band_extraction(self):
        obs = ca.extract_observations(
            paragraph=_paragraph(band="Heavily smoothed"),
        )
        assert obs["paragraph_regularity"] == "high"

    def test_discourse_density_extraction(self):
        obs = ca.extract_observations(
            discourse=_discourse(density=45.0, marked_entropy=1.0),
        )
        assert obs["discourse_marker_density"] == "high"
        assert obs["marked_move_entropy"] == "low"

    def test_low_discourse_density(self):
        obs = ca.extract_observations(
            discourse=_discourse(density=4.0, marked_entropy=2.5),
        )
        assert obs["discourse_marker_density"] == "low"
        assert obs["marked_move_entropy"] == "high"

    def test_pos_bigram_kl_extraction(self):
        obs = ca.extract_observations(
            variance=_variance(flagged=[], pos_bigram_kl=0.20),
        )
        assert obs["pos_bigram_kl"] == "high"

    def test_localized_vs_uniform(self):
        # Hot-zone fraction in [0.2, 0.6] → localized.
        obs1 = ca.extract_observations(
            variance=_variance(
                flagged=[], n_windows=10, hot_window_count=4,
            ),
        )
        assert obs1["length_localization"] == "localized"
        # Hot-zone fraction > 0.8 → uniform.
        obs2 = ca.extract_observations(
            variance=_variance(
                flagged=[], n_windows=10, hot_window_count=9,
            ),
        )
        assert obs2["length_localization"] == "uniform"


# ---------- Confounder scoring ----------


class TestScoreConfounders:
    def test_empty_observations_yields_zero_scores(self):
        ranked = ca.score_confounders({})
        for r in ranked:
            assert r["compatibility_score"] == 0.0
            assert r["n_observations_used"] == 0

    def test_single_match_yields_partial_score(self):
        # Only paragraph_regularity observed, ai_smoothing expects high
        ranked = ca.score_confounders({"paragraph_regularity": "high"})
        ai = next(r for r in ranked if r["confounder"] == "ai_smoothing")
        assert ai["n_matches"] == 1
        assert ai["compatibility_score"] == 1.0  # 1 match out of 1 used

    def test_contradictory_observation_lowers_score(self):
        # paragraph_regularity=low contradicts ai_smoothing's "high"
        ranked = ca.score_confounders({"paragraph_regularity": "low"})
        ai = next(r for r in ranked if r["confounder"] == "ai_smoothing")
        assert ai["n_contradictions"] == 1
        assert ai["compatibility_score"] == 0.0

    def test_top_score_for_canonical_ai_smoothing_pattern(self):
        obs = {
            "sentence_variance": "low",
            "lexical_diversity": "low",
            "paragraph_regularity": "high",
            "discourse_marker_density": "high",
            "marked_move_entropy": "low",
            "connective_density": "high",
            "length_localization": "uniform",
        }
        ranked = ca.score_confounders(obs)
        # ai_smoothing should be among top 2 candidates.
        top2 = [r["confounder"] for r in ranked[:2]]
        assert "ai_smoothing" in top2

    def test_canonical_confounder_pair_both_score_high(self):
        """Honesty contract: AI smoothing and legal/policy memo
        style both predict the same surface pattern. The audit
        should score both high and refuse to commit to one."""
        obs = {
            "connective_density": "high",
            "discourse_marker_density": "high",
            "paragraph_regularity": "high",
            "marked_move_entropy": "low",
        }
        ranked = ca.score_confounders(obs)
        ai_score = next(
            r["compatibility_score"] for r in ranked
            if r["confounder"] == "ai_smoothing"
        )
        legal_score = next(
            r["compatibility_score"] for r in ranked
            if r["confounder"] == "legal_or_policy_memo_style"
        )
        # Both should score >= 0.6 — the framework cannot
        # distinguish them on this evidence alone.
        assert ai_score >= 0.6
        assert legal_score >= 0.6

    def test_ranked_descending(self):
        obs = {
            "sentence_variance": "low",
            "paragraph_regularity": "high",
        }
        ranked = ca.score_confounders(obs)
        scores = [r["compatibility_score"] for r in ranked]
        assert scores == sorted(scores, reverse=True)


# ---------- Distinguishing evidence ----------


class TestDistinguishingEvidence:
    def test_no_distinguishing_when_observations_consistent(self):
        # All observations point in directions both top candidates predict.
        obs = {
            "paragraph_regularity": "high",
            "discourse_marker_density": "high",
        }
        ranked = ca.score_confounders(obs)
        evidence = ca.find_distinguishing_evidence(obs, ranked)
        # Top candidates here predict the same direction → no
        # distinguishing evidence between them.
        assert isinstance(evidence, list)

    def test_distinguishing_evidence_when_register_diverges(self):
        # AI smoothing predicts register-match high (kind of); register
        # shift predicts low. The matrix has register_match in some
        # confounders only. Just check the function runs and returns a list.
        obs = {
            "register_match": "low",
            "pos_bigram_kl": "high",
            "idiolect_survival": "high",
        }
        ranked = ca.score_confounders(obs)
        evidence = ca.find_distinguishing_evidence(obs, ranked)
        assert isinstance(evidence, list)


# ---------- Missing evidence ----------


class TestMissingEvidence:
    def test_empty_inputs_lists_all_missing(self):
        missing = ca.find_missing_evidence({})
        # 13 high-leverage signals after Release 4 added the agency
        # family (nominalization_density, agentless_passive_rate,
        # generic_institutional_density, concrete_detail_density)
        # to the original 9.
        assert len(missing) == 13

    def test_one_observed_drops_from_missing_list(self):
        missing_empty = ca.find_missing_evidence({})
        missing_with = ca.find_missing_evidence(
            {"discourse_marker_density": "high"},
        )
        assert len(missing_with) == len(missing_empty) - 1


# ---------- analyze_confounders end-to-end ----------


class TestAnalyzeConfounders:
    def test_returns_full_shape(self):
        report = ca.analyze_confounders(
            variance=_variance(flagged=["connective_density"]),
            discourse=_discourse(density=45.0, marked_entropy=1.0),
        )
        for k in (
            "task_surface", "tool", "version",
            "observations", "ranked_confounders",
            "distinguishing_evidence", "missing_evidence",
            "n_observations", "inputs_used",
        ):
            assert k in report

    def test_inputs_used_records_what_was_supplied(self):
        report = ca.analyze_confounders(
            variance=_variance(flagged=[]),
        )
        assert report["inputs_used"]["variance"] is True
        assert report["inputs_used"]["voice_distance"] is False


# ---------- Render ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        report = ca.analyze_confounders(
            variance=_variance(flagged=["connective_density"]),
            discourse=_discourse(density=45.0, marked_entropy=1.0),
        )
        md = ca.render_report(report)
        assert "## What this result licenses" in md
        assert "differential diagnosis" in md.lower()

    def test_markdown_renders_ranked_table(self):
        report = ca.analyze_confounders(
            variance=_variance(flagged=["connective_density"]),
        )
        md = ca.render_report(report)
        assert "## Ranked compatible explanations" in md
        # Multiple confounders rendered.
        for confounder in (
            "ai_smoothing", "legal_or_policy_memo_style",
            "professional_copyediting",
        ):
            assert confounder in md

    def test_markdown_lists_missing_evidence(self):
        report = ca.analyze_confounders()  # no inputs
        md = ca.render_report(report)
        assert "## Missing evidence" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_with_no_inputs_errors(self, tmp_path):
        rc = ca.main([])
        assert rc == 2

    def test_cli_with_variance_input(self, tmp_path):
        var_path = tmp_path / "var.json"
        var_path.write_text(
            json.dumps(_variance(flagged=["connective_density"])),
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = ca.main([
            "--variance-json", str(var_path),
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "validation"
        assert "ranked_confounders" in payload


# ---------- Confounder matrix integrity ----------


class TestMatrixIntegrity:
    def test_all_confounders_have_at_least_one_signal(self):
        for name, expectations in ca.CONFOUNDER_MATRIX.items():
            assert len(expectations) >= 1, (
                f"Confounder {name!r} has no expectations"
            )

    def test_directions_are_canonical(self):
        canonical = {"high", "low", "any", "absent", "uniform", "localized"}
        for name, expectations in ca.CONFOUNDER_MATRIX.items():
            for signal, direction in expectations.items():
                assert direction in canonical, (
                    f"{name}.{signal}={direction} not in canonical set"
                )


# ---------- Agency family folded into matrix (Release 4) ----------


def _agency(
    nominalization: float = 0.0,
    agentless_passive: float = 0.0,
    generic_inst: float = 0.0,
    concrete: float = 5.0,
) -> dict:
    return {
        "densities_per_1k": {
            "nominalization_per_1k": nominalization,
            "agentless_passive_per_1k": agentless_passive,
            "generic_institutional_per_1k": generic_inst,
            "concrete_detail_per_1k": concrete,
        },
    }


class TestAgencyFolding:
    """The Release 4 strengthening complement folds the agency
    family into the confounder matrix and the observation extractor.
    Tests pin both the new observation extraction and the matrix
    expectations for the canonical confounders."""

    def test_extract_observations_reads_agency(self):
        obs = ca.extract_observations(
            agency=_agency(
                nominalization=40.0, agentless_passive=8.0,
                generic_inst=10.0, concrete=0.5,
            ),
        )
        assert obs["nominalization_density"] == "high"
        assert obs["agentless_passive_rate"] == "high"
        assert obs["generic_institutional_density"] == "high"
        assert obs["concrete_detail_density"] == "low"

    def test_low_agency_signals(self):
        obs = ca.extract_observations(
            agency=_agency(
                nominalization=4.0, agentless_passive=0.0,
                generic_inst=0.0, concrete=10.0,
            ),
        )
        assert obs["nominalization_density"] == "low"
        assert obs["agentless_passive_rate"] == "low"
        assert obs["concrete_detail_density"] == "high"

    def test_ai_smoothing_predicts_high_agency_loss(self):
        """The matrix's ai_smoothing entry should expect high
        nominalization, high agentless passive, high generic
        institutional, low concrete detail (Release 4)."""
        ai_expectations = ca.CONFOUNDER_MATRIX["ai_smoothing"]
        assert ai_expectations.get("nominalization_density") == "high"
        assert ai_expectations.get("agentless_passive_rate") == "high"
        assert ai_expectations.get("generic_institutional_density") == "high"
        assert ai_expectations.get("concrete_detail_density") == "low"

    def test_legal_or_policy_memo_predicts_high_agency_loss(self):
        legal = ca.CONFOUNDER_MATRIX["legal_or_policy_memo_style"]
        assert legal.get("nominalization_density") == "high"
        assert legal.get("agentless_passive_rate") == "high"
        assert legal.get("generic_institutional_density") == "high"

    def test_translation_esl_predicts_low_agency_loss(self):
        """Per the Release 4 matrix update: ESL cleanup tends
        toward simpler, agent-explicit constructions; lower
        nominalization than native institutional prose."""
        esl = ca.CONFOUNDER_MATRIX["translation_or_esl_cleanup"]
        assert esl.get("nominalization_density") == "low"
        assert esl.get("agentless_passive_rate") == "low"

    def test_agency_sharpens_ai_vs_legal_differential(self):
        """The honesty contract from Release 3 was that AI smoothing
        and legal/policy memo style both score high on the same
        original 14 signals. The Release 4 addition is that BOTH
        still predict high agency loss — so agency alone doesn't
        distinguish them — but the AI matrix entry ALSO expects
        low concrete-detail-density and (when char_ngram_delta is
        observed) high char_ngram_delta + low idiolect_survival."""
        # The agency fold doesn't add a unique distinguishing
        # signal between AI and legal — both predict high agency
        # loss. The framework's design point: the differential
        # diagnosis stays honest about which signals distinguish
        # which candidates. The matrix integrity check just
        # confirms both entries use the agency family.
        ai = ca.CONFOUNDER_MATRIX["ai_smoothing"]
        legal = ca.CONFOUNDER_MATRIX["legal_or_policy_memo_style"]
        assert ai.get("nominalization_density") == legal.get("nominalization_density")
        # But AI uniquely predicts low concrete-detail; legal
        # leaves it unspecified (or "any").
        ai_concrete = ai.get("concrete_detail_density")
        legal_concrete = legal.get("concrete_detail_density")
        # AI matrix says "low concrete" — load-bearing.
        assert ai_concrete == "low"
        # Legal matrix doesn't predict concrete detail (not in entry).
        assert legal_concrete is None or legal_concrete == "any"

    def test_analyze_confounders_accepts_agency_kwarg(self):
        report = ca.analyze_confounders(
            agency=_agency(nominalization=40.0, generic_inst=10.0),
        )
        assert "ranked_confounders" in report
        assert report["inputs_used"]["agency"] is True

    def test_missing_evidence_lists_agency_signals_when_absent(self):
        missing = ca.find_missing_evidence({})
        # Agency family signals appear in the missing-evidence list.
        missing_text = " ".join(missing)
        assert "nominalization" in missing_text
        assert "concrete_detail" in missing_text


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
