#!/usr/bin/env python3
"""B.3 wave-4 integration tests: voice-surface claim-license routing.

Final wave of the B.3 rollout. Wires per-state caveats into the
two voice-surface scripts that emit a ``ClaimLicense`` block:

  * ``general_imposters`` — General Imposters attribution harness
  * ``semantic_preservation_check`` — before/after preservation guardrail

The B.3 helper shipped in 1.49.0 (PR #29); waves 2 (PR #37 / 1.56.0)
and 3 (PR #38 / 1.57.0) covered the validation- and craft-surface
scripts respectively. This file closes out the rollout.

For each script the tests confirm:

  1. Pre-B.3 callers (no ``--ai-status``) see the same markdown as
     v1.49.0–1.57.0 — backwards compat preserved.
  2. ``--ai-status ai_generated_from_outline`` adds the outline/seed
     caveat to the rendered claim-license block.
  3. ``--ai-status pre_ai_human`` adds the pre-AI baseline caveat.
  4. The output dict's ``ai_status`` field is populated in JSON
     output so downstream consumers can route on state without
     re-passing the flag.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------- Fixtures ----------


def _gi_manifest(tmp_path: Path) -> Path:
    """Minimal on-disk manifest for general_imposters: one
    candidate persona with 4 baseline docs + 6 impostor personas.
    Same shape as ``test_general_imposters._write_manifest``."""
    text_dir = tmp_path / "texts"
    text_dir.mkdir()
    rows: list[dict] = []
    base = (
        "The discipline of attention is older than the "
        "disciplines that depend on it. "
    ) * 30
    for i in range(4):
        p = text_dir / f"alice_{i}.txt"
        p.write_text(base + f" Document {i}.", encoding="utf-8")
        rows.append({
            "id": f"alice_{i}", "path": str(p),
            "persona": "alice", "register": "blog_essay",
            "author": "Alice", "corpus_role": "identity_baseline",
            "ai_status": "pre_ai_human", "use": ["voice_profile"],
            "split": "baseline", "privacy": "private",
        })
    for i in range(6):
        p = text_dir / f"impostor_{i}.txt"
        p.write_text(
            f"Most contemporary fiction declines structural ambition. "
            f"Impostor {i}. " * 30,
            encoding="utf-8",
        )
        rows.append({
            "id": f"impostor_{i}", "path": str(p),
            "persona": f"impostor_{i}", "register": "blog_essay",
            "author": f"Impostor {i}", "corpus_role": "impostor",
            "impostor_for": ["alice"],
            "register_match": "high", "topic_match": "medium",
            "consent_status": "fair_use_research",
            "era": "pre_chatgpt",
            "acquired_via": "test_fixture",
            "ai_status": "pre_ai_human", "use": ["voice_impostor"],
            "split": "baseline", "privacy": "private",
        })
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return manifest


def _gi_target(tmp_path: Path) -> Path:
    """A target text that should land in the consistent region for
    the alice persona in the fixture above."""
    target = tmp_path / "target.txt"
    target.write_text(
        (
            "The discipline of attention is older than the "
            "disciplines that depend on it. "
        ) * 30 + " Document 0.",
        encoding="utf-8",
    )
    return target


# ---------- general_imposters ----------


class TestGeneralImpostersB3Routing:
    """``general_imposters.py`` gains ``--ai-status`` in v1.58.0."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
    ) -> tuple[Path, Path]:
        import general_imposters as gi  # type: ignore
        manifest = _gi_manifest(tmp_path)
        target = _gi_target(tmp_path)
        out_md = tmp_path / "ai-prose-baselines-private" / "gi.md"
        out_json = tmp_path / "ai-prose-baselines-private" / "gi.json"
        # The script uses an argparse.Namespace directly via run().
        # We mirror the test_general_imposters.test_run_end_to_end
        # convention but add the new ai_status field.
        args = argparse.Namespace(
            target=str(target), target_id=None,
            manifest=str(manifest),
            candidate_persona="alice", register="blog_essay",
            iterations=20, feature_fraction=0.5,
            top_n_features=200, seed=42,
            out=str(out_md), json_out=str(out_json),
            allow_public_output=False,
            ai_status=ai_status,
        )
        rc = gi.run(args)
        assert rc == 0
        return out_md, out_json

    def test_without_ai_status_no_state_caveat(self, tmp_path: Path):
        md_path, _ = self._run(tmp_path)
        text = md_path.read_text()
        assert "ai_generated_from_outline" not in text
        assert "outline-seeded" not in text.lower()
        assert "human seed" not in text.lower()
        assert "## What this result licenses" in text

    def test_outline_status_emits_seed_caveat(self, tmp_path: Path):
        md_path, _ = self._run(
            tmp_path, ai_status="ai_generated_from_outline",
        )
        text = md_path.read_text().lower()
        assert "outline" in text or "human seed" in text

    def test_pre_ai_human_status_emits_baseline_caveat(
        self, tmp_path: Path,
    ):
        md_path, _ = self._run(tmp_path, ai_status="pre_ai_human")
        text = md_path.read_text()
        assert "pre-AI" in text or "pre_ai_human" in text

    def test_json_output_carries_ai_status_field(self, tmp_path: Path):
        _, json_path = self._run(tmp_path, ai_status="ai_assisted")
        payload = json.loads(json_path.read_text())
        assert payload.get("ai_status") == "ai_assisted"
        # Core report shape unchanged.
        assert "decision" in payload
        assert "proportion" in payload

    def test_omitting_ai_status_keeps_json_shape(self, tmp_path: Path):
        """Backwards-compat for JSON consumers: when --ai-status is
        not supplied, the new ai_status key does NOT appear in the
        payload (to_dict only emits it when set). Legacy parsers
        that don't know about the field see the v1.49.0–1.57.0
        shape."""
        _, json_path = self._run(tmp_path, ai_status=None)
        payload = json.loads(json_path.read_text())
        assert "ai_status" not in payload, (
            "When --ai-status is omitted, the JSON payload must "
            "not gain an ai_status key (back-compat)."
        )


# ---------- semantic_preservation_check ----------


class TestSemanticPreservationB3Routing:
    """``semantic_preservation_check.py`` gains ``--ai-status``."""

    def _run(
        self,
        tmp_path: Path,
        *,
        ai_status: str | None = None,
        json_out: bool = False,
    ) -> Path:
        import semantic_preservation_check  # type: ignore
        before = tmp_path / "before.txt"
        before.write_text(
            "The committee discussed the proposal. "
            "However, the budget is fixed. "
            "Therefore, the timeline shifted. "
            "It might be that more work is needed. ",
            encoding="utf-8",
        )
        after = tmp_path / "after.txt"
        after.write_text(
            "The committee considered the proposal. "
            "The budget remained constrained. "
            "The timeline therefore shifted. "
            "More work is required. ",
            encoding="utf-8",
        )
        out_path = tmp_path / ("out.json" if json_out else "out.md")
        argv = [
            "--before", str(before),
            "--after", str(after),
            "--out", str(out_path),
        ]
        if json_out:
            argv.append("--json")
        if ai_status is not None:
            argv += ["--ai-status", ai_status]
        rc = semantic_preservation_check.main(argv)
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
            tmp_path,
            ai_status="ai_generated_from_outline",
            json_out=True,
        )
        payload = json.loads(out.read_text())
        assert payload.get("ai_status") == "ai_generated_from_outline"
        # Core report shape unchanged.
        assert "overall_verdict" in payload
        assert "categories" in payload


# ---------- JSON-shape contract: caveats live in markdown only ----


class TestB3VoiceJsonOutputUnaffected:
    """JSON output for both voice scripts must not include the
    rendered caveats; downstream consumers see the same JSON shape
    as v1.57.0 plus an ``ai_status`` field when supplied."""

    def test_semantic_preservation_json_has_no_caveat_blob(
        self, tmp_path: Path,
    ):
        import semantic_preservation_check  # type: ignore
        before = tmp_path / "b.txt"
        before.write_text(
            "The team produced a prototype. It worked. " * 10,
            encoding="utf-8",
        )
        after = tmp_path / "a.txt"
        after.write_text(
            "The team built a prototype. The result was good. " * 10,
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = semantic_preservation_check.main([
            "--before", str(before),
            "--after", str(after),
            "--out", str(out_path), "--json",
            "--ai-status", "ai_generated_from_outline",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        # ai_status is at top-level; the rendered caveat text lives
        # only inside claim_license.rendered (the structured block).
        # The top-level fields should NOT contain outline-seeded
        # language outside that block.
        cl = payload.get("claim_license", {})
        rendered = (cl.get("rendered") or "").lower()
        # The rendered block IS allowed to contain the caveat (it's
        # the rendered markdown after all). What we're checking is
        # that the rest of the JSON doesn't accidentally contain
        # it.
        payload_no_rendered = dict(payload)
        if "claim_license" in payload_no_rendered:
            payload_no_rendered["claim_license"] = {
                k: v for k, v in cl.items() if k != "rendered"
            }
        text_no_rendered = json.dumps(payload_no_rendered).lower()
        assert "outline-seeded" not in text_no_rendered, (
            "Rendered caveats should live ONLY inside "
            "claim_license.rendered, not leak into the structured "
            "JSON payload."
        )
        # Sanity: rendered block does carry the caveat.
        assert "outline" in rendered or "human seed" in rendered


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
