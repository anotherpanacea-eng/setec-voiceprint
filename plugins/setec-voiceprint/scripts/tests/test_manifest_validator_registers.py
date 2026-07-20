#!/usr/bin/env python3
"""Regression coverage for the manifest register vocabulary."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


def test_professional_letter_is_a_known_register_without_warning(tmp_path: Path):
    source = tmp_path / "letter.txt"
    source.write_text("Dear colleague, thank you for your thoughtful letter.", encoding="utf-8")
    entry = {
        "id": "letter-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        "register": "professional_letter",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    register_issues = [issue for issue in result["issues"] if issue["field"] == "register"]
    assert register_issues == []
    assert result["summary"]["by_register"] == {"professional_letter": 1}
