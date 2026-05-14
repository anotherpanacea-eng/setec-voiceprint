#!/usr/bin/env python3
"""Regression tests for SPEC_authorship_states.md phase B.2.

Phase B.2 of the authorship-states refinement adds:

  1. ``ai_generated_from_outline`` to ``ALLOWED_AI_STATUS``. Schema-
     additive; existing manifests with `ai_generated` still validate.
  2. Soft consistency check on ``ai_status: mixed``. Entries with
     `mixed` should carry a `notes.composite_states` array listing
     the authorship states present across sections; absence
     produces a warning (not an error, so legacy `mixed` entries
     don't break).

These tests pin both behaviors against the validator's contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from manifest_validator import (  # type: ignore
    ALLOWED_AI_STATUS,
    validate_entry,
    validate_manifest,
)


# --------------- Vocabulary additions ---------------------------


def test_ai_generated_from_outline_in_allowed_set():
    """The new value must be in the vocabulary so manifests using
    it validate cleanly."""
    assert "ai_generated_from_outline" in ALLOWED_AI_STATUS


def test_existing_ai_status_values_still_present():
    """Schema-additive: previously-allowed values are unchanged."""
    expected_pre_b2 = {
        "pre_ai_human", "ai_generated", "ai_assisted",
        "ai_edited", "mixed", "unknown",
    }
    assert expected_pre_b2.issubset(ALLOWED_AI_STATUS), (
        f"Existing ai_status values must remain valid; "
        f"missing: {expected_pre_b2 - ALLOWED_AI_STATUS}"
    )


def _base_entry(**overrides) -> dict:
    """Minimal valid manifest entry for testing single fields."""
    base = {
        "id": "test_entry",
        "path": "test_data/sample.txt",
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        "register": "blog_essay",
    }
    base.update(overrides)
    return base


# --------------- ai_generated_from_outline validates -----------


def test_ai_generated_from_outline_entry_validates(tmp_path: Path):
    """An entry with the new ai_status value should produce no
    vocabulary warnings."""
    entry = _base_entry(ai_status="ai_generated_from_outline")
    # path must exist for the validator to be happy.
    text_path = tmp_path / "test_data" / "sample.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("dummy content")
    entry["path"] = str(text_path)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n")
    result = validate_manifest(manifest)
    # No warnings or errors flagging the ai_status value as unknown.
    ai_status_issues = [
        i for i in result["issues"]
        if i.get("field") == "ai_status" and "Unknown" in i.get("message", "")
    ]
    assert ai_status_issues == [], (
        f"ai_generated_from_outline should be a known value; "
        f"got: {ai_status_issues}"
    )


def test_legacy_ai_generated_still_validates(tmp_path: Path):
    """Backwards compat: the bare `ai_generated` value (the
    backwards-compat catch-all) still validates without complaint."""
    entry = _base_entry(ai_status="ai_generated")
    text_path = tmp_path / "test_data" / "sample.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("dummy content")
    entry["path"] = str(text_path)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n")
    result = validate_manifest(manifest)
    ai_status_issues = [
        i for i in result["issues"]
        if i.get("field") == "ai_status" and "Unknown" in i.get("message", "")
    ]
    assert ai_status_issues == []


# --------------- mixed consistency check ------------------------


def test_mixed_without_composite_states_warns():
    """Direct test against `validate_entry`: an `ai_status: mixed`
    entry without `notes.composite_states` should produce a
    warning (not an error)."""
    entry = _base_entry(ai_status="mixed")
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    mixed_warnings = [
        i for i in issues
        if i.severity == "warning"
        and i.field == "ai_status"
        and "composite_states" in i.message
    ]
    assert len(mixed_warnings) == 1, (
        f"Expected exactly one mixed/composite_states warning; "
        f"got {len(mixed_warnings)}: {[i.to_dict() for i in issues]}"
    )


def test_mixed_with_composite_states_array_is_clean():
    """An `ai_status: mixed` entry with a proper composite_states
    array produces no warning."""
    entry = _base_entry(
        ai_status="mixed",
        notes={
            "composite_states": ["ai_assisted", "ai_generated_from_outline"],
            "composite_note": "Sections 1-3 ai_assisted; 4-7 from outline",
        },
    )
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    mixed_warnings = [
        i for i in issues
        if "composite_states" in i.message
    ]
    assert mixed_warnings == [], (
        f"Properly-annotated mixed entry should warn nothing about "
        f"composite_states; got: {[i.to_dict() for i in mixed_warnings]}"
    )


def test_mixed_with_empty_composite_states_array_warns():
    """An empty composite_states array does not satisfy the soft
    requirement — the value must list at least one state."""
    entry = _base_entry(
        ai_status="mixed",
        notes={"composite_states": []},
    )
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    mixed_warnings = [
        i for i in issues
        if "composite_states" in i.message
    ]
    assert len(mixed_warnings) == 1


def test_mixed_with_non_list_composite_states_warns():
    """A non-list `composite_states` value (e.g., a string) does
    not satisfy the soft requirement either — the value must be a
    list type."""
    entry = _base_entry(
        ai_status="mixed",
        notes={"composite_states": "ai_assisted, ai_generated_from_outline"},
    )
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    mixed_warnings = [
        i for i in issues
        if "composite_states" in i.message
    ]
    assert len(mixed_warnings) == 1


def test_non_mixed_entry_without_composite_states_is_clean():
    """A non-`mixed` entry doesn't trigger the composite_states
    warning regardless of what's in (or missing from) `notes`."""
    entry = _base_entry(ai_status="pre_ai_human")
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    mixed_warnings = [
        i for i in issues
        if "composite_states" in i.message
    ]
    assert mixed_warnings == []


def test_mixed_warning_is_warning_not_error():
    """The soft-requirement contract: `mixed` without composite_states
    is a WARNING, not an ERROR. Legacy `mixed` entries from before
    this check existed must continue to validate (with warnings)."""
    entry = _base_entry(ai_status="mixed")
    issues = validate_entry(
        entry, lineno=1, manifest_path=Path("/tmp/m.jsonl"),
        seen_ids=set(), seen_paths={},
    )
    errors = [i for i in issues if i.severity == "error"]
    # No error from the mixed/composite_states check itself; only
    # other errors (which there shouldn't be for this minimal valid
    # entry).
    composite_errors = [
        e for e in errors if "composite_states" in e.message
    ]
    assert composite_errors == []
