#!/usr/bin/env python3
"""B.3 wave-2 integration tests: validation-surface claim-license routing.

The B.3 rollout wires per-state caveats into every audit script
that emits a ``ClaimLicense`` block. Wave 1 (PR #29 / v1.49.0)
shipped the helper plus two exemplar scripts
(``stance_modality_audit``, ``discourse_move_signature``).

This file pins wave 2 — the validation-surface scripts:

  * ``confounder_audit`` — differential diagnosis Layer D
  * ``surface_disagreement_resolver`` — cross-surface meta-layer
  * ``adversarial_robustness_card`` — per-signal robustness card

For each script the tests confirm:

  1. Pre-B.3 callers (no ``--ai-status``) see the same markdown as
     v1.49.0 — backwards compat preserved.
  2. ``--ai-status ai_generated_from_outline`` adds the outline/seed
     caveat to the rendered claim-license block.
  3. ``--ai-status pre_ai_human`` adds the pre-AI baseline caveat.
  4. The audit dict's ``ai_status`` field is populated in JSON
     output so downstream consumers can route on state without
     re-passing the flag.

The scripts are exercised through their ``main()`` entry points so
the CLI plumbing (argparse → audit/report dict → claim-license
block) is covered end-to-end.
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


def _variance_json(flagged: list[str]) -> dict:
    """Minimal variance_audit-shaped JSON: just the keys the
    downstream resolvers/auditors actually read."""
    return {
        "task_surface": "smoothing_diagnosis",
        "summary": {"n_words": 600},
        "compression": {
            "band": "Heavily smoothed",
            "compression_fraction": 0.45,
            "flagged_signals": list(flagged),
            "available_signals": list(flagged),
        },
        "baseline_divergences": {
            "pos_bigrams": {"kl_divergence": 0.18},
        },
    }


def _audit_json_for_rc(burstiness: float = -0.30) -> dict:
    """Minimal variance_audit JSON for adversarial_robustness_card.
    The card reads ``tier1.sentence_length.burstiness_B`` and
    similar paths plus ``compression.compression_fraction``."""
    return {
        "task_surface": "smoothing_diagnosis",
        "summary": {"n_words": 500},
        "tier1": {
            "sentence_length": {
                "burstiness_B": burstiness,
                "sd": 4.0,
            },
            "connective_density": {"per_1000_tokens": 28.0},
            "mattr": {"value": 0.72},
            "mtld": 80.0,
            "yules_k": 110.0,
            "shannon_entropy_bits": 10.0,
            "fkgl": {"sd": 1.5},
        },
        "compression": {
            "band": "Heavily smoothed",
            "compression_fraction": 0.50,
            "flagged_signals": ["burstiness_B"],
            "available_signals": ["burstiness_B"],
        },
    }


# ---------- confounder_audit ----------


class TestConfounderAuditB3Routing:
    """``confounder_audit.py`` gains ``--ai-status`` in v1.53.0."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import confounder_audit  # type: ignore
        var_path = tmp_path / "var.json"
        var_path.write_text(
            json.dumps(_variance_json(["burstiness_B", "connective_density"])),
            encoding="utf-8",
        )
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [
            "--variance-json", str(var_path),
            "--out", str(out_path),
        ]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = confounder_audit.main(argv)
        assert rc == 0
        return out_path

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        out = self._run(tmp_path)
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "human seed" not in text.lower()
        # Basic license header still present (backwards compat).
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
            tmp_path, ai_status="ai_assisted", json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_assisted"
        # Envelope shape: task_surface at top level, report payload
        # under results.
        assert payload.get("task_surface") == "validation"
        assert "ranked_confounders" in payload["results"]


# ---------- surface_disagreement_resolver ----------


class TestSurfaceDisagreementB3Routing:
    """``surface_disagreement_resolver.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import surface_disagreement_resolver  # type: ignore
        var_path = tmp_path / "var.json"
        var_path.write_text(
            json.dumps(_variance_json(["burstiness_B"])),
            encoding="utf-8",
        )
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [
            "--variance-json", str(var_path),
            "--out", str(out_path),
        ]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = surface_disagreement_resolver.main(argv)
        assert rc == 0
        return out_path

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        out = self._run(tmp_path)
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "## What this result licenses" in text

    def test_mixed_status_mentions_composite_states(
        self, tmp_path: Path,
    ):
        out = self._run(tmp_path, ai_status="mixed")
        text = out.read_text().lower()
        assert "composite_states" in text or "composite states" in text

    def test_ai_edited_status_emits_edited_caveat(
        self, tmp_path: Path,
    ):
        out = self._run(tmp_path, ai_status="ai_edited")
        text = out.read_text().lower()
        assert (
            "ai_edited" in text
            or "bulk-accepted" in text
            or "edited" in text
        )

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        out = self._run(
            tmp_path,
            ai_status="ai_generated_from_outline",
            json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_generated_from_outline"
        assert "task_surface" in payload


# ---------- adversarial_robustness_card ----------


class TestAdversarialRobustnessCardB3Routing:
    """``adversarial_robustness_card.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import adversarial_robustness_card  # type: ignore
        base_path = tmp_path / "base.json"
        base_path.write_text(
            json.dumps(_audit_json_for_rc(burstiness=-0.50)),
            encoding="utf-8",
        )
        fix_path = tmp_path / "paraphrase.json"
        fix_path.write_text(
            json.dumps(_audit_json_for_rc(burstiness=0.10)),
            encoding="utf-8",
        )
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [
            "--base", str(base_path),
            "--fixture", f"paraphrase:{fix_path}",
            "--out", str(out_path),
        ]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = adversarial_robustness_card.main(argv)
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

    def test_unknown_status_emits_unknown_caveat(self, tmp_path: Path):
        """``unknown`` is a valid value in the B.2 vocabulary; the
        caveat should make clear the audit can't route on state."""
        out = self._run(tmp_path, ai_status="unknown")
        text = out.read_text().lower()
        # The unknown caveat language should surface the lack of
        # state information in some form.
        assert (
            "unknown" in text
            or "no authorship-state" in text
            or "unspecified" in text
        )

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        out = self._run(
            tmp_path, ai_status="pre_ai_human", json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "pre_ai_human"
        assert "task_surface" in payload


# ---------- JSON-shape contract: caveats live in markdown only ----


class TestB3JsonOutputUnaffected:
    """After schema_version 1.0 migration (wave 8), B.3 state-routed
    caveats are intentionally surfaced in BOTH
    ``claim_license.additional_caveats`` AND
    ``claim_license_rendered`` per SPEC §4 — same contract as the
    wave 3 / 5 / 7 voice + craft surfaces. The report payload itself
    (under ``results``) must still not carry rendered caveat text.
    """

    def test_confounder_audit_report_payload_has_no_caveat_blob(
        self, tmp_path: Path,
    ):
        import confounder_audit  # type: ignore
        var_path = tmp_path / "v.json"
        var_path.write_text(
            json.dumps(_variance_json(["burstiness_B"])),
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = confounder_audit.main([
            "--variance-json", str(var_path),
            "--json", "--out", str(out_path),
            "--ai-status", "ai_generated_from_outline",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        # Caveats must NOT bleed into the report payload (results).
        results_blob = json.dumps(payload["results"]).lower()
        assert "outline-seeded" not in results_blob
        assert "human seed" not in results_blob
        # But they ARE expected in claim_license per SPEC §4.
        caveats = payload["claim_license"]["additional_caveats"]
        assert any(
            "outline" in c.lower() or "seed" in c.lower()
            for c in caveats
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
