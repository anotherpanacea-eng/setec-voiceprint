#!/usr/bin/env python3
"""Regression tests for surface_disagreement_resolver.py (Release 7)."""

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

import surface_disagreement_resolver as sdr  # type: ignore


# ---------- Surface readings ----------


class TestSmoothingLevelReader:
    def test_heavily_smoothed_high(self):
        r = sdr._read_smoothing_level(
            {"compression": {"band": "Heavily smoothed"}},
        )
        assert r == "high"

    def test_lightly_smoothed_low(self):
        r = sdr._read_smoothing_level(
            {"compression": {"band": "Lightly smoothed"}},
        )
        assert r == "low"

    def test_none_unknown(self):
        assert sdr._read_smoothing_level(None) == "unknown"


class TestVoiceDriftReader:
    def test_far_high(self):
        r = sdr._read_voice_drift_level(
            {"overall": {"band": "far from baseline"}},
        )
        assert r == "high"

    def test_near_low(self):
        r = sdr._read_voice_drift_level(
            {"overall": {"band": "near baseline"}},
        )
        assert r == "low"

    def test_none_unknown(self):
        assert sdr._read_voice_drift_level(None) == "unknown"


class TestGiDecisionReader:
    def test_consistent(self):
        r = sdr._read_gi_decision({"decision": "consistent_with_candidate"})
        assert r == "consistent"

    def test_gray_zone(self):
        r = sdr._read_gi_decision({"decision": "gray_zone_refused"})
        assert r == "gray_zone"


class TestIdiolectSurvival:
    def test_high_survival(self):
        idiolect = {
            "preservation_list": [
                {"phrase": "snowdrift"}, {"phrase": "kerosene lamp"},
                {"phrase": "stone wall"}, {"phrase": "cup of tea"},
                {"phrase": "Tuesday"},
            ],
        }
        target = (
            "She walked through the snowdrift past the stone wall. "
            "The kerosene lamp burned. She sipped a cup of tea on Tuesday."
        )
        r = sdr._read_idiolect_survival(idiolect, target)
        assert r == "high"

    def test_low_survival(self):
        idiolect = {
            "preservation_list": [{"phrase": "snowdrift"}] * 5,
        }
        target = "Generic prose without preservation phrases."
        r = sdr._read_idiolect_survival(idiolect, target)
        assert r == "low"

    def test_unknown_without_target(self):
        idiolect = {"preservation_list": [{"phrase": "x"}]}
        r = sdr._read_idiolect_survival(idiolect, None)
        assert r == "unknown"


# ---------- Pattern matcher ----------


class TestMatchesValue:
    def test_wildcard(self):
        assert sdr._matches_value("high", "*") is True
        assert sdr._matches_value("unknown", "*") is True

    def test_exact_match(self):
        assert sdr._matches_value("high", "high") is True
        assert sdr._matches_value("high", "low") is False

    def test_alternation(self):
        assert sdr._matches_value("low", "(low|moderate)") is True
        assert sdr._matches_value("moderate", "(low|moderate)") is True
        assert sdr._matches_value("high", "(low|moderate)") is False

    def test_unknown_matches_only_wildcard(self):
        assert sdr._matches_value("unknown", "high") is False
        assert sdr._matches_value("unknown", "(low|high)") is False


class TestPatternMatching:
    def test_edited_authorial_voice_pattern(self):
        # high smoothing + low voice drift
        report = sdr.resolve(
            variance={"compression": {"band": "Heavily smoothed"}},
            voice_distance={"overall": {"band": "near baseline"}},
        )
        names = [m["name"] for m in report["matched_interpretations"]]
        assert "edited_authorial_voice" in names

    def test_register_shift_pattern(self):
        report = sdr.resolve(
            variance={"compression": {"band": "Lightly smoothed"}},
            voice_distance={"overall": {"band": "far from baseline"}},
        )
        names = [m["name"] for m in report["matched_interpretations"]]
        assert "register_shift_or_collaboration" in names

    def test_self_conscious_imitation_pattern(self):
        idiolect = {
            "preservation_list": [
                {"phrase": "snowdrift"}, {"phrase": "stone wall"},
                {"phrase": "kerosene lamp"}, {"phrase": "cup of tea"},
                {"phrase": "Tuesday morning"},
            ],
        }
        target = (
            "She walked through the snowdrift past the stone wall. "
            "The kerosene lamp burned. She sipped a cup of tea on "
            "Tuesday morning."
        )
        report = sdr.resolve(
            voice_distance={"overall": {"band": "far from baseline"}},
            idiolect=idiolect,
            target_text=target,
        )
        names = [m["name"] for m in report["matched_interpretations"]]
        assert "self_conscious_imitation" in names

    def test_no_inputs_no_matches(self):
        report = sdr.resolve()
        assert report["matched_interpretations"] == []
        assert report["n_matches"] == 0

    def test_unknown_readings_dont_fire_specific_patterns(self):
        # No surfaces supplied → all readings "unknown" → no
        # patterns match (none have all-wildcard signals).
        report = sdr.resolve()
        assert report["n_known_readings"] == 0


class TestResolveE2E:
    def test_returns_full_shape(self):
        report = sdr.resolve(
            variance={"compression": {"band": "Heavily smoothed"}},
        )
        for k in (
            "task_surface", "tool", "version", "readings",
            "n_known_readings", "matched_interpretations",
            "n_matches", "inputs_used",
        ):
            assert k in report

    def test_inputs_used_records_supplied(self):
        report = sdr.resolve(
            variance={"compression": {"band": "Lightly smoothed"}},
        )
        assert report["inputs_used"]["variance"] is True
        assert report["inputs_used"]["voice_distance"] is False


# ---------- Render ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        report = sdr.resolve(
            variance={"compression": {"band": "Heavily smoothed"}},
            voice_distance={"overall": {"band": "near baseline"}},
        )
        md = sdr.render_report(report)
        assert "## What this result licenses" in md

    def test_markdown_renders_readings_table(self):
        report = sdr.resolve()
        md = sdr.render_report(report)
        assert "## Surface readings" in md
        assert "smoothing" in md
        assert "voice_drift" in md

    def test_markdown_renders_matched_interpretations(self):
        report = sdr.resolve(
            variance={"compression": {"band": "Heavily smoothed"}},
            voice_distance={"overall": {"band": "near baseline"}},
        )
        md = sdr.render_report(report)
        assert "edited_authorial_voice" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_no_inputs_succeeds(self, tmp_path):
        out_path = tmp_path / "out.json"
        rc = sdr.main(["--json", "--out", str(out_path)])
        assert rc == 0
        import json
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert "matched_interpretations" in payload

    def test_cli_missing_input_returns_2(self, tmp_path):
        rc = sdr.main([
            "--variance-json", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_invalid_json_returns_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ malformed", encoding="utf-8")
        rc = sdr.main(["--variance-json", str(bad)])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
