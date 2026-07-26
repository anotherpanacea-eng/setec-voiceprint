#!/usr/bin/env python3
"""Regression coverage for the manifest register vocabulary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


@pytest.mark.parametrize(
    "register", ["professional_letter", "teaching", "social_media_facebook"]
)
def test_owner_approved_register_is_known_without_warning(tmp_path: Path, register: str):
    source = tmp_path / "letter.txt"
    source.write_text("Dear colleague, thank you for your thoughtful letter.", encoding="utf-8")
    entry = {
        "id": "letter-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        "register": register,
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    register_issues = [issue for issue in result["issues"] if issue["field"] == "register"]
    assert register_issues == []
    assert result["summary"]["by_register"] == {register: 1}


def test_social_media_facebook_is_h2_admissible_and_declares_unknown(
    tmp_path: Path,
) -> None:
    """The Spec 73 projection admits the register, and H1's receipt-bound
    mapping resolves it to the "unknown" declared family (it is not in
    CANONICAL_REGISTER_TO_FAMILY), so sweep inventories bucket these rows as
    declared-unknown rather than refusing the corpus."""
    import register_classifier as rc
    import register_sweep as rs

    source = tmp_path / "post.txt"
    source.write_text("word " * 150, encoding="utf-8")
    row = {
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["baseline"],
        "register": "social_media_facebook",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    data = (json.dumps(row) + "\n").encode("utf-8")
    projection = mv.project_register_sweep_manifest_bytes(
        data, manifest_path=manifest
    )
    assert projection.input_rows == 1
    assert projection.rows[0].register == "social_media_facebook"
    assert rc.resolve_family("social_media_facebook") == "unknown"
    # And the projected row frames cleanly through the H2 encoder.
    rs.projected_row_binding(
        {
            "ai_status": "pre_ai_human",
            "manifest_ordinal": 0,
            "path": source.name,
            "persona": None,
            "register": "social_media_facebook",
            "split": None,
            "use": ["baseline"],
        }
    )
