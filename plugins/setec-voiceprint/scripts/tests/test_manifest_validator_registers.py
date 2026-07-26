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
    "register", ["professional_letter", "teaching", "message.imessage"]
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


def test_message_imessage_baseline_use_is_rejected(tmp_path: Path):
    source = tmp_path / "message.txt"
    source.write_text("A private conversational-register fixture.", encoding="utf-8")
    entry = {
        "id": "message-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["baseline", "voice_profile"],
        "register": "message.imessage",
        "privacy": "private",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    assert any(
        issue["severity"] == "error"
        and issue["field"] == "use"
        and "profile-only" in issue["message"]
        for issue in result["issues"]
    )


def test_message_imessage_voice_profile_only_is_accepted(tmp_path: Path):
    source = tmp_path / "message.txt"
    source.write_text("A private conversational-register fixture.", encoding="utf-8")
    entry = {
        "id": "message-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["voice_profile"],
        "register": "message.imessage",
        "privacy": "private",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    assert result["n_errors"] == 0
    assert not any(issue["field"] == "register" for issue in result["issues"])
