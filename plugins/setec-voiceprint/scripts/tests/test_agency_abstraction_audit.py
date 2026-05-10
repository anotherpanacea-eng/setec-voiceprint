#!/usr/bin/env python3
"""Regression tests for agency_abstraction_audit.py (Release 4).

Surfaces Tier-1. The audit catches agency loss / abstraction
drift that the existing Layer A signals don't see — institutional
smoothing replaces concrete actors with nominalized processes.
Tests pin the per-signal patterns and the band-call shape on
clear concrete-vs-abstract fixtures.
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

import agency_abstraction_audit as aa  # type: ignore


# ---------- Fixtures ----------


_CONCRETE_PROSE = (
    "Sarah walked into the kitchen on Tuesday morning. "
    "Her grandmother was at the table, holding a cup of tea. "
    "The garden was still wet from the night rain. "
    "Sarah sat down. She had walked four miles already, and her "
    "shoes were muddy. Her grandmother handed her a sweater. They "
    "watched the dog stretch on the porch."
) * 4

_ABSTRACT_PROSE = (
    "The implementation of the framework requires consideration "
    "of multiple dimensions. Actionable insights are provided "
    "through holistic analysis. The challenges and opportunities "
    "of stakeholder engagement must be addressed in a robust "
    "manner. Decisions are made about the strategy, and "
    "recommendations are provided. The methodology leverages key "
    "takeaways from the literature."
) * 4


# ---------- Per-signal contracts ----------


class TestAuditAgency:
    def test_empty_text_unavailable(self):
        a = aa.audit_agency_abstraction("")
        assert a["available"] is False

    def test_concrete_prose_low_nominalization(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert a["densities_per_1k"]["nominalization_per_1k"] < 10.0

    def test_abstract_prose_high_nominalization(self):
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        assert a["densities_per_1k"]["nominalization_per_1k"] >= 30.0

    def test_concrete_prose_high_concrete_detail(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert a["densities_per_1k"]["concrete_detail_per_1k"] >= 5.0

    def test_abstract_prose_low_concrete_detail(self):
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        assert a["densities_per_1k"]["concrete_detail_per_1k"] < 1.5

    def test_abstract_prose_high_generic_institutional(self):
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        assert a["densities_per_1k"]["generic_institutional_per_1k"] >= 10.0

    def test_concrete_prose_high_action_verbs(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert a["densities_per_1k"]["action_verb_per_1k"] >= 10.0

    def test_passive_detection_skips_with_by(self):
        # "X was carried by Y" should NOT count as agentless.
        text = "The decision was made by the committee. " * 50
        a = aa.audit_agency_abstraction(text)
        # Most "was made by" instances skip the agentless count.
        assert a["raw_counts"]["agentless_passive"] < 10

    def test_passive_detection_counts_agentless(self):
        # "X was made" without a by-phrase IS agentless.
        text = "The decision was made. The conclusion was reached. " * 50
        a = aa.audit_agency_abstraction(text)
        assert a["raw_counts"]["agentless_passive"] >= 50


# ---------- Band call ----------


class TestBandCall:
    def test_concrete_prose_lightly_abstracted(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert a["compression"]["band"] == "Lightly abstracted"

    def test_abstract_prose_heavily_abstracted(self):
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        assert a["compression"]["band"] in {
            "Moderately abstracted", "Heavily abstracted",
        }

    def test_abstract_prose_flags_multiple_signals(self):
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        flagged = set(a["compression"]["flagged_signals"])
        # At least nominalization and generic-institutional should fire.
        assert "high_nominalization_density" in flagged
        assert "high_generic_institutional_density" in flagged

    def test_concrete_prose_flags_no_abstraction(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert a["compression"]["n_flagged"] == 0


# ---------- Entity-to-action ratio ----------


class TestEntityToActionRatio:
    def test_concrete_prose_has_finite_ratio(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        assert isinstance(a["entity_to_action_ratio"], float)
        assert a["entity_to_action_ratio"] >= 0.0

    def test_abstract_prose_high_ratio(self):
        # Abstract prose has many entities/concrete=0 but also no
        # action verbs, so e2a saturates to entity count.
        a = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        assert a["entity_to_action_ratio"] >= 1.0


# ---------- Baseline comparison ----------


class TestBaselineComparison:
    def test_baseline_aggregate(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        for i in range(3):
            (base / f"f{i}.txt").write_text(_CONCRETE_PROSE, encoding="utf-8")
        block = aa.audit_baseline_agency(str(base))
        assert block["n_files"] == 3
        assert "aggregate" in block

    def test_compare_to_baseline_returns_z_scores(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        for i in range(4):
            (base / f"f{i}.txt").write_text(_CONCRETE_PROSE, encoding="utf-8")
        block = aa.audit_baseline_agency(str(base))
        target = aa.audit_agency_abstraction(_ABSTRACT_PROSE)
        cmp = aa.compare_to_baseline(target, block)
        assert cmp["available"] is True
        assert "z_scores" in cmp
        # Abstract target vs. concrete baseline → high z-score on
        # nominalization density.
        z_nom = cmp["z_scores"]["nominalization_per_1k"]
        if z_nom is not None:
            assert z_nom > 1.0


# ---------- Render + claim license ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        md = aa.render_report(a)
        assert "## What this result licenses" in md
        assert "Agency-loss" in md or "agency" in md.lower()

    def test_markdown_renders_band(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        md = aa.render_report(a)
        assert "**Band:**" in md

    def test_markdown_renders_per_signal_table(self):
        a = aa.audit_agency_abstraction(_CONCRETE_PROSE)
        md = aa.render_report(a)
        assert "## Per-signal densities" in md
        assert "nominalization" in md
        assert "agentless passive" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_CONCRETE_PROSE, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = aa.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "smoothing_diagnosis"
        assert "compression" in payload

    def test_cli_handles_missing_input(self, tmp_path):
        rc = aa.main([str(tmp_path / "missing.txt")])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
