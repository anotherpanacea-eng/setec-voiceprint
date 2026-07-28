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
    "register",
    [
        "professional_letter",
        "teaching",
        "message.imessage",
        "message.facebook_messenger",
        "social_media_twitter",
        "forum_metafilter",
        "social_media_facebook_posts",
        "social_media_facebook_comments",
    ],
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


@pytest.mark.parametrize(
    "register", ["message.imessage", "message.facebook_messenger"]
)
def test_private_dyadic_baseline_use_is_rejected(
    tmp_path: Path, register: str
):
    source = tmp_path / "message.txt"
    source.write_text("A private conversational-register fixture.", encoding="utf-8")
    entry = {
        "id": "message-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["baseline", "voice_profile"],
        "register": register,
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


@pytest.mark.parametrize(
    "register", ["message.imessage", "message.facebook_messenger"]
)
def test_private_dyadic_voice_profile_only_is_accepted(
    tmp_path: Path, register: str
):
    source = tmp_path / "message.txt"
    source.write_text("A private conversational-register fixture.", encoding="utf-8")
    entry = {
        "id": "message-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["voice_profile"],
        "register": register,
        "privacy": "private",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    assert result["n_errors"] == 0
    assert not any(issue["field"] == "register" for issue in result["issues"])


@pytest.mark.parametrize(
    "register",
    [
        "social_media_twitter",
        "forum_metafilter",
        "social_media_facebook_posts",
        "social_media_facebook_comments",
    ],
)
def test_new_public_leaf_is_h2_admissible_and_declares_unknown(
    tmp_path: Path, register: str,
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
        "register": register,
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    data = (json.dumps(row) + "\n").encode("utf-8")
    projection = mv.project_register_sweep_manifest_bytes(
        data, manifest_path=manifest
    )
    assert projection.input_rows == 1
    assert projection.rows[0].register == register
    assert rc.resolve_family(register) == "unknown"
    # And the projected row frames cleanly through the H2 encoder.
    rs.projected_row_binding(
        {
            "ai_status": "pre_ai_human",
            "manifest_ordinal": 0,
            "path": source.name,
            "persona": None,
            "register": register,
            "split": None,
            "use": ["baseline"],
        }
    )


def test_retired_social_media_facebook_is_a_hard_error(tmp_path: Path):
    source = tmp_path / "post.txt"
    source.write_text("A synthetic retired-register fixture.", encoding="utf-8")
    entry = {
        "id": "post-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        "register": "social_media_facebook",
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    issues = [issue for issue in result["issues"] if issue["field"] == "register"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert "Retired register" in issues[0]["message"]

    with pytest.raises(mv.BadInput):
        mv.project_register_sweep_manifest_bytes(
            (json.dumps(entry) + "\n").encode("utf-8"),
            manifest_path=manifest,
        )


@pytest.mark.parametrize(
    "register",
    [
        ["message.imessage"],
        {"leaf": "message.imessage"},
        123,
    ],
)
def test_non_string_register_is_reported_not_raised(tmp_path: Path, register: object):
    """A structurally invalid register must be an issue, never a traceback.

    The profile-only check tests membership in a frozenset, which hashes its
    left operand. An unhashable register reached it unguarded and aborted the
    whole run, losing every other row's issues -- on exactly the malformed
    input the validator exists to report.
    """
    source = tmp_path / "note.txt"
    source.write_text("A synthetic non-string register fixture.", encoding="utf-8")
    entry = {
        "id": "note-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["baseline"],
        "split": "baseline",
        "register": register,
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    issues = [issue for issue in result["issues"] if issue["field"] == "register"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert "must be a string" in issues[0]["message"]


@pytest.mark.parametrize("bad_tag", [["baseline"], {"tag": "baseline"}])
def test_non_string_use_tag_is_reported_not_raised(tmp_path: Path, bad_tag: object):
    """Same class as the register case: membership hashes the element.

    'use' is checked to be a list, but its elements were never type-checked
    before being tested against ALLOWED_USE.
    """
    source = tmp_path / "note.txt"
    source.write_text("A synthetic non-string use-tag fixture.", encoding="utf-8")
    entry = {
        "id": "note-2",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": [bad_tag],
    }
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = mv.validate_manifest(manifest)

    issues = [issue for issue in result["issues"] if issue["field"] == "use"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert "must be a string" in issues[0]["message"]
