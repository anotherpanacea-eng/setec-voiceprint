#!/usr/bin/env python3
"""B.3 integration tests: per-script claim-license state routing.

Tests that the two exemplar audit scripts wired in v1.47.0
(``stance_modality_audit.py`` and ``discourse_move_signature.py``)
emit ClaimLicense blocks whose ``additional_caveats`` carry
state-specific language when the operator passed ``--ai-status``,
and that the pre-B.3 behavior is preserved when the flag is absent.

These tests use the audit scripts' ``main()`` entry points so the
CLI plumbing (argparse → audit dict → claim-license block) is
exercised end-to-end. Other audit scripts that consume
``claim_license`` get wired in follow-up PATCH PRs per SPEC §10
phase B.3.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------- Helpers ----------


def _sample_prose() -> str:
    """A short essay with enough varied stance / discourse markers
    that the audit scripts' classifiers produce meaningful counts.
    Length is intentionally above the audit scripts' minimum-word
    thresholds."""
    return (
        "We might consider whether the proposal is feasible. "
        "However, the budget is fixed; therefore, the constraints "
        "are binding. The committee believes the timeline is "
        "ambitious. Nevertheless, the team is confident. "
        "For example, the prototype was completed in two weeks. "
        "On the other hand, the integration phase has been "
        "delayed. To summarize, the project is partially on "
        "track. It is clear that further review is necessary. "
        "Importantly, the stakeholders should be consulted before "
        "any decision is finalized. In short, more work is needed."
    ) * 8  # repeat so we comfortably exceed length floors


# ---------- stance_modality_audit ----------


class TestStanceModalityB3Routing:
    """The ``stance_modality_audit.py`` CLI gains ``--ai-status``
    in v1.47.0 (B.3). When supplied, the ClaimLicense block in the
    rendered markdown carries the state-specific caveat."""

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        """Backwards compat: omitting ``--ai-status`` produces the
        pre-B.3 markdown — no state-routed caveat."""
        import stance_modality_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = stance_modality_audit.main([
            str(target), "--out", str(out),
        ])
        assert rc == 0
        text = out.read_text()
        # No state-specific phrasing.
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "human seed" not in text.lower()
        # But the basic claim-license header IS there.
        assert "## What this result licenses" in text

    def test_with_outline_ai_status_emits_state_caveat(
        self, tmp_path: Path,
    ):
        """``--ai-status ai_generated_from_outline`` should produce
        a caveat that mentions 'outline' or 'human seed'."""
        import stance_modality_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = stance_modality_audit.main([
            str(target), "--out", str(out),
            "--ai-status", "ai_generated_from_outline",
        ])
        assert rc == 0
        text = out.read_text()
        # Caveat mentioning the outline/seed origin shows up in the
        # block's ### Caveats section.
        assert (
            "outline" in text.lower() or "human seed" in text.lower()
        )

    def test_with_pre_ai_human_ai_status(self, tmp_path: Path):
        """A pre_ai_human target should produce its own caveat."""
        import stance_modality_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = stance_modality_audit.main([
            str(target), "--out", str(out),
            "--ai-status", "pre_ai_human",
        ])
        assert rc == 0
        text = out.read_text()
        # The pre_ai_human caveat mentions pre-AI or pre_ai_human.
        assert "pre-AI" in text or "pre_ai_human" in text


# ---------- discourse_move_signature ----------


class TestDiscourseMoveB3Routing:
    """Same shape as the stance_modality tests, exercising the
    ``discourse_move_signature.py`` CLI's new ``--ai-status``."""

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        import discourse_move_signature  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = discourse_move_signature.main([
            str(target), "--out", str(out),
        ])
        assert rc == 0
        text = out.read_text()
        assert "ai_generated_from_outline" not in text
        assert "human seed" not in text.lower()
        assert "## What this result licenses" in text

    def test_with_ai_assisted_emits_state_caveat(
        self, tmp_path: Path,
    ):
        """An ai_assisted target gets a caveat that mentions
        collaborative editing / per-suggestion adjudication."""
        import discourse_move_signature  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = discourse_move_signature.main([
            str(target), "--out", str(out),
            "--ai-status", "ai_assisted",
        ])
        assert rc == 0
        text = out.read_text().lower()
        # Caveat mentions ai_assisted or its operational definition.
        assert (
            "ai_assisted" in text
            or "collaborative" in text
            or "per-suggestion" in text
        )

    def test_with_mixed_ai_status_mentions_composite_states(
        self, tmp_path: Path,
    ):
        """A mixed target's caveat should point at composite_states."""
        import discourse_move_signature  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.md"
        rc = discourse_move_signature.main([
            str(target), "--out", str(out),
            "--ai-status", "mixed",
        ])
        assert rc == 0
        text = out.read_text().lower()
        assert "composite_states" in text or "composite states" in text


# ---------- Both scripts: JSON output unaffected ----------


class TestB3JsonOutputUnaffected:
    """The B.3 change is rendering-layer (markdown). JSON output
    should not include the rendered caveats; downstream consumers
    of the JSON path see the same shape as v1.45.0."""

    def test_stance_modality_json_unchanged_shape(
        self, tmp_path: Path,
    ):
        import stance_modality_audit  # type: ignore
        target = tmp_path / "essay.txt"
        target.write_text(_sample_prose(), encoding="utf-8")
        out = tmp_path / "out.json"
        rc = stance_modality_audit.main([
            str(target), "--out", str(out), "--json",
            "--ai-status", "ai_generated_from_outline",
        ])
        assert rc == 0
        payload = json.loads(out.read_text())
        # The audit dict gained an `ai_status` field (forward-compat
        # for downstream consumers that want to route on state) but
        # no extra caveats blob.
        assert payload.get("ai_status") == "ai_generated_from_outline"
        # Audit dict still has its core keys.
        assert "available" in payload or "preprocessing" in payload


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
