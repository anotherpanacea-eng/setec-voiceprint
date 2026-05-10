#!/usr/bin/env python3
"""Regression tests for the Release-4 revision-risk model in
restoration_packet.py.

The targetability taxonomy (direct / translated / investigate_first /
avoid_direct) classifies SIGNALS by what kind of prompt instruction
is safe. The revision-risk model classifies the INTERVENTION's
potential damage along orthogonal axes: erase idiolect, create
metric gaming, increase generic humanizer artifacts, damage
clarity, damage genre expectations, overcorrect into artificial
variance, preserve voice but weaken argument, restore quirks
intentionally edited out.

Tests pin:
  * classify_revision_risk maps known (targetability, signal)
    pairs to the expected risk level.
  * Severity 'heavy' bumps risk one notch (low→medium, medium→high).
  * apply_revision_risk fills the packet's revision_risk +
    revision_risk_rationale fields.
  * build_packets applies the risk classifier to every packet.
  * Markdown rendering surfaces the risk + ⚠ marker on medium / high.
  * Backward compat: pre-1.34.0 packet shape (without the new
    fields) still serializes correctly.
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

import restoration_packet as rp  # type: ignore


# ---------- classify_revision_risk ----------


class TestClassifyRevisionRisk:
    def test_direct_sentence_length_medium(self):
        risk, rationale = rp.classify_revision_risk(
            "direct", "tier1.sentence_length.sd",
        )
        assert risk == "medium"
        assert "artificial variance" in rationale.lower() or "rhetorical" in rationale.lower()

    def test_direct_burstiness_medium(self):
        risk, _ = rp.classify_revision_risk(
            "direct", "tier1.sentence_length.burstiness_B",
        )
        assert risk == "medium"

    def test_direct_connective_density_medium(self):
        risk, rationale = rp.classify_revision_risk(
            "direct", "tier1.connective_density",
        )
        assert risk == "medium"
        assert "genre" in rationale.lower()

    def test_direct_idiolect_low(self):
        risk, _ = rp.classify_revision_risk("direct", "idiolect_phrase")
        assert risk == "low"

    def test_direct_aic_pattern_medium(self):
        risk, rationale = rp.classify_revision_risk(
            "direct", "AIC pattern triplet",
        )
        assert risk == "medium"
        assert "genre" in rationale.lower()

    def test_translated_pos_bigram_medium(self):
        risk, rationale = rp.classify_revision_risk(
            "translated", "pos_bigram_diff",
        )
        assert risk == "medium"
        assert "mannered" in rationale.lower() or "tag-shape" in rationale.lower()

    def test_investigate_first_high(self):
        risk, rationale = rp.classify_revision_risk(
            "investigate_first", "mtld",
        )
        assert risk == "high"
        assert "metric-gaming" in rationale.lower() or "cause" in rationale.lower()

    def test_avoid_direct_high(self):
        risk, rationale = rp.classify_revision_risk(
            "avoid_direct", "voice_distance.weighted_delta",
        )
        assert risk == "high"
        assert "evidence" in rationale.lower() or "metric" in rationale.lower()

    def test_severity_heavy_bumps_low_to_medium(self):
        risk_normal, _ = rp.classify_revision_risk(
            "direct", "idiolect_phrase", "moderate",
        )
        risk_heavy, _ = rp.classify_revision_risk(
            "direct", "idiolect_phrase", "heavy",
        )
        assert risk_normal == "low"
        assert risk_heavy == "medium"

    def test_severity_heavy_bumps_medium_to_high(self):
        risk_normal, _ = rp.classify_revision_risk(
            "direct", "tier1.sentence_length.sd", "moderate",
        )
        risk_heavy, _ = rp.classify_revision_risk(
            "direct", "tier1.sentence_length.sd", "heavy",
        )
        assert risk_normal == "medium"
        assert risk_heavy == "high"

    def test_unknown_direct_signal_defaults_to_low(self):
        risk, rationale = rp.classify_revision_risk(
            "direct", "some_unknown_signal_name",
        )
        assert risk == "low"
        assert rationale  # non-empty default rationale

    def test_unknown_targetability_defaults_safely(self):
        risk, _ = rp.classify_revision_risk("unknown_class", "x")
        assert risk == "unspecified"


# ---------- apply_revision_risk ----------


class TestApplyRevisionRisk:
    def test_apply_fills_packet_fields(self):
        p = rp.Packet(
            id="t1", targetability="direct",
            signal="tier1.sentence_length.sd",
            direction="under_represented", severity="moderate",
            evidence={}, plain_language_diagnosis="...",
            revision_moves=[], guardrails=[], post_check=[],
        )
        rp.apply_revision_risk(p)
        assert p.revision_risk == "medium"
        assert p.revision_risk_rationale

    def test_apply_returns_packet_for_chaining(self):
        p = rp.Packet(
            id="t1", targetability="direct", signal="idiolect_phrase",
            direction="under_represented", severity="light",
            evidence={}, plain_language_diagnosis="...",
            revision_moves=[], guardrails=[], post_check=[],
        )
        same = rp.apply_revision_risk(p)
        assert same is p

    def test_to_dict_includes_risk_fields(self):
        p = rp.Packet(
            id="t1", targetability="avoid_direct",
            signal="overall_kl",
            direction="over_represented", severity="moderate",
            evidence={}, plain_language_diagnosis="...",
            revision_moves=[], guardrails=[], post_check=[],
        )
        rp.apply_revision_risk(p)
        d = p.to_dict()
        assert "revision_risk" in d
        assert "revision_risk_rationale" in d
        assert d["revision_risk"] == "high"


# ---------- Backward compat ----------


class TestBackwardCompat:
    def test_packet_default_unspecified(self):
        p = rp.Packet(
            id="t1", targetability="direct", signal="x",
            direction="d", severity="moderate",
            evidence={}, plain_language_diagnosis="",
            revision_moves=[], guardrails=[], post_check=[],
        )
        assert p.revision_risk == "unspecified"
        assert p.revision_risk_rationale == ""

    def test_to_dict_carries_unspecified(self):
        p = rp.Packet(
            id="t1", targetability="direct", signal="x",
            direction="d", severity="moderate",
            evidence={}, plain_language_diagnosis="",
            revision_moves=[], guardrails=[], post_check=[],
        )
        d = p.to_dict()
        assert d["revision_risk"] == "unspecified"


# ---------- build_packets integration ----------


class TestBuildPacketsIntegration:
    """Integration: build_packets aggregates from the per-source
    builders and applies the risk classifier to every output
    packet."""

    def test_packets_carry_risk_fields(self):
        # Synthetic variance audit input that produces at least one
        # direct + one investigate_first packet.
        variance = {
            "compression": {
                "flagged_signals": ["sentence_length_sd", "mtld"],
                "thresholds_used": {
                    "sentence_length_sd": {
                        "direction": "lt",
                    },
                    "mtld": {"direction": "lt"},
                },
            },
            "tier1": {
                "sentence_length": {"sd": 2.0},
                "mtld": 50.0,
            },
        }
        packets = rp.build_packets(
            variance=variance, bigram=None, voice=None,
            idiolect=None, aic=None,
            max_targets=10, targetability_filter=None,
        )
        for p in packets:
            assert p.revision_risk in {"low", "medium", "high"}, (
                f"Packet {p.id} has unset revision_risk={p.revision_risk}"
            )
            assert p.revision_risk_rationale


# ---------- Markdown rendering ----------


class TestMarkdownRendering:
    def test_high_risk_renders_with_warning_marker(self):
        p = rp.Packet(
            id="t1", targetability="avoid_direct",
            signal="overall_kl",
            direction="over_represented", severity="moderate",
            evidence={"kl": 0.04},
            plain_language_diagnosis="Aggregate KL is high.",
            revision_moves=[], guardrails=[], post_check=[],
        )
        rp.apply_revision_risk(p)
        lines = rp._render_packet_md(1, p, brief=False)
        rendered = "\n".join(lines)
        assert "**Revision risk:**" in rendered
        # ⚠ marker on medium / high.
        assert "⚠" in rendered
        assert "high" in rendered

    def test_low_risk_renders_without_warning_marker(self):
        p = rp.Packet(
            id="t1", targetability="direct",
            signal="idiolect_phrase",
            direction="under_represented", severity="light",
            evidence={},
            plain_language_diagnosis="Idiolect phrase under-represented.",
            revision_moves=[], guardrails=[], post_check=[],
        )
        rp.apply_revision_risk(p)
        lines = rp._render_packet_md(1, p, brief=False)
        rendered = "\n".join(lines)
        assert "**Revision risk:**" in rendered
        # No ⚠ marker on low risk.
        assert "low" in rendered


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
