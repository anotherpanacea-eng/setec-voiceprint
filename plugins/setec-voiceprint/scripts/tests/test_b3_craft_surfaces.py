#!/usr/bin/env python3
"""B.3 wave-3 integration tests: craft-surface claim-license routing.

The B.3 rollout wires per-state caveats into every audit script
that emits a ``ClaimLicense`` block. Wave 1 (PR #29 / v1.49.0)
shipped the helper plus two exemplar scripts; wave 2 (PR #37 /
v1.56.0) wired the validation-surface scripts.

This file pins wave 3 — the craft-surface scripts:

  * ``construction_signature_audit`` — per-construction syntactic density
  * ``punctuation_cadence_audit`` — punctuation rhythm + interruption grammar
  * ``mimicry_cosplay_audit`` — lexical/syntactic dissociation detector

For each script the tests confirm:

  1. Pre-B.3 callers (no ``--ai-status``) see the same markdown as
     v1.49.0–1.56.0 — backwards compat preserved.
  2. ``--ai-status ai_generated_from_outline`` adds the outline/seed
     caveat to the rendered claim-license block.
  3. ``--ai-status pre_ai_human`` adds the pre-AI baseline caveat.
  4. The audit dict's ``ai_status`` field is populated in JSON
     output so downstream consumers can route on state without
     re-passing the flag.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------- Fixture helpers ----------


def _sample_prose() -> str:
    """Sample with enough sentence variety + constructions for the
    craft audits to produce meaningful output. Length is well
    above the audits' minimums."""
    return (
        "The committee discussed the proposal at length. "
        "However, the budget remained ambiguous; therefore, the "
        "timeline shifted. What matters is that the team produced "
        "a working prototype. It is clear that more work is "
        "needed, although the trajectory is favorable. "
        "Nevertheless, the integration phase has been delayed. "
        "For example, the dashboard now reflects daily activity "
        "across regions. Considering the constraints, the team "
        "is confident the launch is achievable. There is, "
        "however, room for additional review before any decision "
        "is finalized. In short, the project is partially on "
        "track."
    ) * 5


def _idiolect_json() -> dict:
    """Minimal idiolect_detector output for mimicry_cosplay_audit."""
    return {
        "preservation_list": [
            {"phrase": "the committee", "score": 1.2},
            {"phrase": "more work is needed", "score": 1.1},
            {"phrase": "in short", "score": 0.9},
        ],
    }


def _voice_distance_json(weighted_delta: float = 1.5) -> dict:
    return {
        "overall": {
            "weighted_delta": weighted_delta,
            "band": "Strong drift (weighted_delta=1.50)",
        },
    }


def _variance_with_kl(kl: float = 0.20) -> dict:
    return {
        "compression": {
            "pos_bigram_kl": {
                "in_band": True,
                "compressed": True,
                "value": kl,
                "threshold": 0.15,
            },
        },
    }


# ---------- construction_signature_audit ----------


class TestConstructionSignatureB3Routing:
    """``construction_signature_audit.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import construction_signature_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [str(target), "--out", str(out_path)]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = construction_signature_audit.main(argv)
        assert rc == 0
        return out_path

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        out = self._run(tmp_path)
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "## What this result licenses" in text

    def test_outline_status_emits_seed_caveat(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="ai_generated_from_outline",
        )
        text = out.read_text().lower()
        assert "outline" in text or "human seed" in text

    def test_pre_ai_human_status_emits_baseline_caveat(
        self, tmp_path: Path,
    ):
        out = self._run(tmp_path, ai_status="pre_ai_human")
        text = out.read_text()
        assert "pre-AI" in text or "pre_ai_human" in text

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="ai_edited", json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_edited"
        # Core report shape unchanged. construction_signature_audit
        # is tagged voice_coherence per its TASK_SURFACE constant.
        assert "constructions" in payload
        assert payload.get("task_surface") == "voice_coherence"


# ---------- punctuation_cadence_audit ----------


class TestPunctuationCadenceB3Routing:
    """``punctuation_cadence_audit.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import punctuation_cadence_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [str(target), "--out", str(out_path)]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = punctuation_cadence_audit.main(argv)
        assert rc == 0
        return out_path

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        out = self._run(tmp_path)
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "## What this result licenses" in text

    def test_outline_status_emits_seed_caveat(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="ai_generated_from_outline",
        )
        text = out.read_text().lower()
        assert "outline" in text or "human seed" in text

    def test_mixed_status_mentions_composite_states(
        self, tmp_path: Path,
    ):
        out = self._run(tmp_path, ai_status="mixed")
        text = out.read_text().lower()
        assert "composite_states" in text or "composite states" in text

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="ai_assisted", json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_assisted"
        # Core audit shape unchanged.
        assert "preprocessing" in payload


# ---------- mimicry_cosplay_audit ----------


class TestMimicryCosplayB3Routing:
    """``mimicry_cosplay_audit.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import mimicry_cosplay_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        idi_path = tmp_path / "idi.json"
        idi_path.write_text(
            json.dumps(_idiolect_json()), encoding="utf-8",
        )
        vd_path = tmp_path / "vd.json"
        vd_path.write_text(
            json.dumps(_voice_distance_json(1.5)), encoding="utf-8",
        )
        var_path = tmp_path / "var.json"
        var_path.write_text(
            json.dumps(_variance_with_kl(0.20)), encoding="utf-8",
        )
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [
            "--target", str(target),
            "--idiolect-json", str(idi_path),
            "--voice-distance-json", str(vd_path),
            "--variance-json", str(var_path),
            "--out", str(out_path),
        ]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = mimicry_cosplay_audit.main(argv)
        assert rc == 0
        return out_path

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        out = self._run(tmp_path)
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "## What this result licenses" in text

    def test_outline_status_emits_seed_caveat(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="ai_generated_from_outline",
        )
        text = out.read_text().lower()
        assert "outline" in text or "human seed" in text

    def test_ai_edited_status_emits_edited_caveat(
        self, tmp_path: Path,
    ):
        out = self._run(tmp_path, ai_status="ai_edited")
        text = out.read_text().lower()
        # The ai_edited caveat template names low-touch LLM editing
        # / "suggestions accepted in bulk" — match the actual
        # template language.
        assert (
            "editing" in text
            or "accepted in bulk" in text
            or "low-touch" in text
        )

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        out = self._run(
            tmp_path,
            ai_status="ai_generated_from_outline",
            json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_generated_from_outline"
        # Core audit shape unchanged.
        assert "verdict" in payload
        assert "shapes" in payload


# ---------- JSON-shape contract: caveats live in markdown only ----


class TestB3CraftJsonOutputUnaffected:
    """JSON output for all three craft scripts must not include the
    rendered caveats; downstream consumers see the same JSON shape
    as v1.56.0 plus an ``ai_status`` field when supplied."""

    def test_punctuation_cadence_json_has_no_caveat_blob(
        self, tmp_path: Path,
    ):
        import punctuation_cadence_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = punctuation_cadence_audit.main([
            str(target), "--out", str(out_path), "--json",
            "--ai-status", "ai_generated_from_outline",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        text = json.dumps(payload).lower()
        # No rendered caveats embedded in JSON payload.
        assert "outline-seeded" not in text
        assert "human seed" not in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
