#!/usr/bin/env python3
"""Regression tests for mimicry_cosplay_audit.py (Release 10)."""

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

import mimicry_cosplay_audit as mca  # type: ignore


# ---------- Phrase extraction + survival ----------


class TestExtractPhrases:
    def test_dict_items(self):
        idiolect = {
            "preservation_list": [
                {"phrase": "snowdrift"},
                {"phrase": "kerosene lamp"},
            ],
        }
        phrases = mca._extract_phrases_from_idiolect(idiolect)
        assert phrases == ["snowdrift", "kerosene lamp"]

    def test_string_items(self):
        idiolect = {
            "preservation_list": ["snowdrift", "stone wall"],
        }
        phrases = mca._extract_phrases_from_idiolect(idiolect)
        assert phrases == ["snowdrift", "stone wall"]

    def test_empty_list(self):
        assert mca._extract_phrases_from_idiolect(
            {"preservation_list": []}
        ) == []

    def test_missing_list(self):
        assert mca._extract_phrases_from_idiolect({}) == []


class TestPhraseHits:
    def test_all_match(self):
        n_unique, n_occurrences, matched, missing = mca._phrase_hits(
            "She walked through the snowdrift past the stone wall.",
            ["snowdrift", "stone wall"],
        )
        assert n_unique == 2
        assert n_occurrences == 2
        assert sorted(matched) == ["snowdrift", "stone wall"]
        assert missing == []

    def test_partial_match(self):
        n_unique, n_occurrences, matched, missing = mca._phrase_hits(
            "She walked through the snowdrift.",
            ["snowdrift", "stone wall"],
        )
        assert n_unique == 1
        assert n_occurrences == 1
        assert "snowdrift" in matched
        assert "stone wall" in missing

    def test_case_insensitive(self):
        n_unique, n_occurrences, _, _ = mca._phrase_hits(
            "She walked through the SNOWDRIFT.",
            ["snowdrift"],
        )
        assert n_unique == 1
        assert n_occurrences == 1

    def test_repeated_phrase_counts_occurrences_not_unique(self):
        # Reviewer-reproduced regression: a phrase repeated 20×
        # in the target should contribute 20 to the occurrence
        # count and 1 to the unique count.
        n_unique, n_occurrences, _, _ = mca._phrase_hits(
            ("snowdrift " * 20).strip(),
            ["snowdrift"],
        )
        assert n_unique == 1
        assert n_occurrences == 20


class TestPhraseDensityAnomalyRegression:
    """Reviewer-reproduced regression: pre-1.41.1
    `target_density_per_1k` was based on `n_match` (unique
    phrases counted once), so one signature phrase repeated 20
    times in a 2k-word target reported density 0.5/1k and the
    density-anomaly cosplay shape never fired."""

    def test_repeated_phrase_inflates_occurrence_density(self):
        idiolect = {
            "preservation_list": [{"phrase": "snowdrift"}],
        }
        # Target: signature phrase repeated 20× in a 2k-word doc.
        filler = " filler " * 990  # ~1980 filler words.
        target = "snowdrift " * 20 + filler
        result = mca.compute_idiolect_survival(
            idiolect=idiolect, target_text=target,
        )
        # Survival rate (unique-coverage diagnostic) is 1/1 = 1.0.
        assert result["survival_rate"] == 1.0
        # Occurrence density now reflects the 20 repeats.
        # 20 / ~2000 words * 1000 ≈ 10.0/1k.
        assert result["target_density_per_1k"] >= 9.0
        # Unique-phrase coverage density is much lower, kept for
        # legacy compatibility / coverage reporting.
        assert result["unique_phrase_density_per_1k"] < 1.0
        assert result["n_total_occurrences"] == 20

    def test_audit_cosplay_density_anomaly_fires_on_repetition(self):
        # End-to-end: 20× repetition of a single phrase + high
        # voice_distance Delta should fire the density_anomaly
        # cosplay shape.
        idiolect = {
            "preservation_list": [{"phrase": "snowdrift"}],
        }
        filler = " filler " * 990
        target = "snowdrift " * 20 + filler
        audit = mca.audit_cosplay(
            target_text=target,
            idiolect=idiolect,
            voice_distance={"overall": {"weighted_delta": 2.0}},
            variance=None,
            baseline_density_per_1k=1.0,  # baseline 1/1k
        )
        # Density anomaly fires: target ~10/1k vs. baseline 1/1k
        # at default 2.0× factor.
        assert audit["shapes"]["density_anomaly"]["fired"] is True
        # Verdict is at least mixed (one shape fired); often
        # cosplay_suspected if both shapes also fire.
        assert audit["verdict"] in {"cosplay_suspected", "mixed"}


class TestComputeIdiolectSurvival:
    def test_basic_survival(self):
        idiolect = {
            "preservation_list": [
                {"phrase": "snowdrift"},
                {"phrase": "kerosene lamp"},
                {"phrase": "Tuesday morning"},
                {"phrase": "stone wall"},
                {"phrase": "cup of tea"},
            ],
        }
        target = (
            "She walked through the snowdrift past the stone wall. "
            "The kerosene lamp burned. She sipped a cup of tea on "
            "Tuesday morning."
        )
        result = mca.compute_idiolect_survival(
            idiolect=idiolect, target_text=target,
        )
        assert result["n_phrases"] == 5
        assert result["n_matched"] == 5
        assert result["survival_rate"] == 1.0


# ---------- Voice-distance reading ----------


class TestReadWeightedDelta:
    def test_extracts_delta(self):
        vd = {"overall": {"weighted_delta": 1.5}}
        assert mca._read_weighted_delta(vd) == 1.5

    def test_returns_none_for_missing(self):
        assert mca._read_weighted_delta(None) is None
        assert mca._read_weighted_delta({}) is None
        assert mca._read_weighted_delta({"overall": {}}) is None


class TestReadPosBigramKl:
    def test_reads_compression_path(self):
        variance = {
            "compression": {
                "pos_bigram_kl": {
                    "in_band": True, "compressed": True,
                    "value": 0.30,
                },
            },
        }
        block = mca._read_pos_bigram_kl(variance)
        assert block is not None
        assert block["compressed"] is True

    def test_legacy_top_level_fallback(self):
        variance = {
            "pos_bigram_kl": {
                "in_band": True, "compressed": False,
            },
        }
        block = mca._read_pos_bigram_kl(variance)
        assert block is not None

    def test_returns_none_when_absent(self):
        assert mca._read_pos_bigram_kl(None) is None
        assert mca._read_pos_bigram_kl({}) is None


# ---------- Cosplay verdict ----------


class TestCosplayClassification:
    def test_not_cosplay_when_low_survival_and_low_delta(self):
        survival = {
            "survival_rate": 0.2,
            "target_density_per_1k": 1.0,
        }
        result = mca._classify_cosplay(
            survival=survival,
            weighted_delta=0.5,
            pos_bigram_kl=None,
            baseline_density_per_1k=5.0,
            high_survival_threshold=0.6,
            high_delta_threshold=1.25,
            over_preservation_factor=2.0,
        )
        assert result["verdict"] == "not_cosplay"

    def test_cosplay_suspected_when_both_shapes_fire(self):
        # High survival + high delta + over-preservation density.
        survival = {
            "survival_rate": 0.9,
            "target_density_per_1k": 12.0,  # > 5.0 × 2.0
        }
        result = mca._classify_cosplay(
            survival=survival,
            weighted_delta=2.0,
            pos_bigram_kl=None,
            baseline_density_per_1k=5.0,
            high_survival_threshold=0.6,
            high_delta_threshold=1.25,
            over_preservation_factor=2.0,
        )
        assert result["verdict"] == "cosplay_suspected"
        assert (
            result["shapes"]["lexical_without_syntactic"]["fired"]
            is True
        )
        assert result["shapes"]["density_anomaly"]["fired"] is True

    def test_mixed_when_only_one_shape_fires(self):
        # Shape 1 fires but density is normal.
        survival = {
            "survival_rate": 0.9,
            "target_density_per_1k": 4.0,  # below 5.0 × 2.0
        }
        result = mca._classify_cosplay(
            survival=survival,
            weighted_delta=2.0,
            pos_bigram_kl=None,
            baseline_density_per_1k=5.0,
            high_survival_threshold=0.6,
            high_delta_threshold=1.25,
            over_preservation_factor=2.0,
        )
        assert result["verdict"] == "mixed"

    def test_unknown_when_no_evidence(self):
        survival = {
            "survival_rate": None,
            "target_density_per_1k": 0.0,
        }
        result = mca._classify_cosplay(
            survival=survival,
            weighted_delta=None,
            pos_bigram_kl=None,
            baseline_density_per_1k=5.0,
            high_survival_threshold=0.6,
            high_delta_threshold=1.25,
            over_preservation_factor=2.0,
        )
        assert result["verdict"] == "unknown"

    def test_pos_bigram_kl_compressed_substitutes_for_high_delta(self):
        # No weighted_delta, but pos_bigram_kl compressed.
        survival = {
            "survival_rate": 0.9,
            "target_density_per_1k": 3.0,
        }
        result = mca._classify_cosplay(
            survival=survival,
            weighted_delta=None,
            pos_bigram_kl={
                "in_band": True, "compressed": True, "value": 0.3,
            },
            baseline_density_per_1k=5.0,
            high_survival_threshold=0.6,
            high_delta_threshold=1.25,
            over_preservation_factor=2.0,
        )
        assert (
            result["shapes"]["lexical_without_syntactic"]["fired"]
            is True
        )


# ---------- audit_cosplay integration ----------


class TestAuditCosplay:
    def test_no_idiolect_produces_unknown_or_partial(self):
        target = "Some target text."
        audit = mca.audit_cosplay(
            target_text=target,
            idiolect=None,
            voice_distance=None,
            variance=None,
        )
        # No evidence at all → unknown.
        assert audit["verdict"] == "unknown"

    def test_full_cosplay_signature(self):
        idiolect = {
            "preservation_list": [
                {"phrase": "snowdrift"},
                {"phrase": "kerosene lamp"},
                {"phrase": "stone wall"},
                {"phrase": "cup of tea"},
                {"phrase": "Tuesday morning"},
            ],
        }
        # Target: contains every phrase, repeated to inflate density.
        target = (
            "She walked through the snowdrift past the stone wall. "
            "She walked through the snowdrift past the stone wall. "
            "The kerosene lamp burned. The kerosene lamp burned. "
            "The kerosene lamp burned. She sipped a cup of tea on "
            "Tuesday morning. She sipped a cup of tea on Tuesday "
            "morning."
        )
        voice_distance = {
            "overall": {"weighted_delta": 2.5},
        }
        variance = {
            "compression": {
                "pos_bigram_kl": {
                    "in_band": True, "compressed": True,
                    "value": 0.40,
                },
            },
        }
        audit = mca.audit_cosplay(
            target_text=target,
            idiolect=idiolect,
            voice_distance=voice_distance,
            variance=variance,
            baseline_density_per_1k=5.0,
        )
        # All five phrases in the target → 100% survival → high.
        # weighted_delta 2.5 → high.
        # pos_bigram_kl compressed.
        # Density depends on word count vs. matches.
        assert audit["verdict"] in {"cosplay_suspected", "mixed"}
        assert audit["idiolect_survival"]["survival_rate"] == 1.0


# ---------- Render ----------


class TestRender:
    def test_includes_claim_license(self):
        target = "Test text."
        audit = mca.audit_cosplay(
            target_text=target,
            idiolect=None,
            voice_distance=None,
            variance=None,
        )
        md = mca.render_report(audit)
        assert "## What this result licenses" in md

    def test_includes_per_axis_evidence(self):
        target = "She walked. He ran."
        audit = mca.audit_cosplay(
            target_text=target,
            idiolect={"preservation_list": [{"phrase": "She walked"}]},
            voice_distance={"overall": {"weighted_delta": 0.5}},
            variance=None,
        )
        md = mca.render_report(audit)
        assert "Per-axis evidence" in md
        assert "Idiolect-phrase survival" in md
        assert "weighted_delta" in md

    def test_renders_cosplay_shapes(self):
        target = "Test text."
        audit = mca.audit_cosplay(
            target_text=target, idiolect=None,
            voice_distance=None, variance=None,
        )
        md = mca.render_report(audit)
        assert "## Cosplay shapes" in md
        assert "Lexical-without-syntactic" in md
        assert "Density anomaly" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        target_path = tmp_path / "target.md"
        target_path.write_text(
            "She walked through the snowdrift past the stone wall.",
            encoding="utf-8",
        )
        idiolect_path = tmp_path / "idiolect.json"
        idiolect_path.write_text(
            json.dumps({
                "preservation_list": [
                    {"phrase": "snowdrift"},
                    {"phrase": "stone wall"},
                ],
            }),
            encoding="utf-8",
        )
        out_path = tmp_path / "audit.json"
        rc = mca.main([
            "--target", str(target_path),
            "--idiolect-json", str(idiolect_path),
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        # schema_version 1.0 envelope: per-script payload lives under
        # results, per SPEC_output_schema_unification.md.
        assert payload["schema_version"] == "1.0"
        assert payload["task_surface"] == "voice_coherence"
        assert payload["tool"] == "mimicry_cosplay_audit"
        assert "verdict" in payload["results"]
        assert payload["results"]["idiolect_survival"]["n_matched"] == 2

    def test_cli_missing_target_returns_2(self, tmp_path):
        rc = mca.main([
            "--target", str(tmp_path / "missing.md"),
        ])
        assert rc == 2

    def test_cli_empty_target_returns_2(self, tmp_path):
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        rc = mca.main([
            "--target", str(empty),
        ])
        assert rc == 2

    def test_cli_missing_idiolect_json_returns_2(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text("Some text.", encoding="utf-8")
        rc = mca.main([
            "--target", str(target),
            "--idiolect-json", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_invalid_json_returns_2(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text("Some text.", encoding="utf-8")
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid", encoding="utf-8")
        rc = mca.main([
            "--target", str(target),
            "--idiolect-json", str(bad),
        ])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
