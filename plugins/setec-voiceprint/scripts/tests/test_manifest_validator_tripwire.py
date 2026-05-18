#!/usr/bin/env python3
"""Tripwire for Issue #6 (jsonschema migration for the manifest).

The handcrafted validator stays in place until the manifest shape
outgrows it. Three triggers — nested per-entry objects, an explicit
schema/manifest version field, or per-entry breadth above
TRIPWIRE_BROAD_FIELD_THRESHOLD — record an advisory entry under the
``tripwires`` key of the result dict. Closing Issue #6 left this
tripwire planted so future readers know when to reconsider.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


def _write_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    target = tmp_path / "source.txt"
    target.write_text("hello world", encoding="utf-8")
    for e in entries:
        e.setdefault("path", target.name)
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )
    return manifest


def _flat_entry(idx: int) -> dict:
    return {
        "id": f"entry_{idx}",
        "ai_status": "pre_ai_human",
        "use": ["baseline"],
    }


class TestTripwireDormant:
    def test_flat_manifest_no_tripwire(self, tmp_path: Path):
        manifest = _write_manifest(tmp_path, [_flat_entry(i) for i in range(3)])
        result = mv.validate_manifest(manifest)
        assert result["tripwires"] == []


class TestNestedTripwire:
    def test_unfamiliar_nested_dict_field_fires(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["provenance"] = {"source": "test", "collected_at": "2026-05-18"}
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        nested = [t for t in result["tripwires"] if t["category"] == "nested"]
        assert len(nested) == 1
        assert nested[0]["field"] == "provenance"
        assert "issue #6" in nested[0]["message"].lower()

    def test_only_first_nested_entry_records(self, tmp_path: Path):
        entries = [_flat_entry(i) for i in range(3)]
        entries[0]["meta_a"] = {"k": 1}
        entries[1]["meta_b"] = {"k": 2}
        manifest = _write_manifest(tmp_path, entries)
        result = mv.validate_manifest(manifest)
        nested = [t for t in result["tripwires"] if t["category"] == "nested"]
        assert len(nested) == 1

    def test_documented_notes_nesting_does_not_fire(self, tmp_path: Path):
        """`notes.composite_states` is the documented nesting path for
        the `ai_status: mixed` case (references/manifest-schema.md
        §16). The handcrafted validator already covers it, so the
        nested-trigger must whitelist `notes`."""
        entry = _flat_entry(0)
        entry["ai_status"] = "mixed"
        entry["notes"] = {"composite_states": ["ai_edited", "pre_ai_human"]}
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        nested = [t for t in result["tripwires"] if t["category"] == "nested"]
        assert nested == []


class TestVersionedTripwire:
    def test_schema_version_field_fires(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["schema_version"] = "2.0"
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        versioned = [t for t in result["tripwires"] if t["category"] == "versioned"]
        assert len(versioned) == 1
        assert versioned[0]["field"] == "schema_version"

    def test_manifest_version_field_fires(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["manifest_version"] = "3"
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        versioned = [t for t in result["tripwires"] if t["category"] == "versioned"]
        assert len(versioned) == 1
        assert versioned[0]["field"] == "manifest_version"


class TestBroadTripwire:
    def test_field_count_above_threshold_fires(self, tmp_path: Path):
        entry = _flat_entry(0)
        for k in range(mv.TRIPWIRE_BROAD_FIELD_THRESHOLD + 5):
            entry[f"extension_field_{k}"] = "value"
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        broad = [t for t in result["tripwires"] if t["category"] == "broad"]
        assert len(broad) == 1


class TestTripwireDoesNotBlockValidation:
    def test_nested_dict_still_produces_normal_issues(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["provenance"] = {"source": "test"}
        # Strip a required field to force a validation error.
        del entry["ai_status"]
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        assert result["n_errors"] >= 1
        assert any(t["category"] == "nested" for t in result["tripwires"])


class TestEnvelopeCarriesTripwires:
    """``--json`` consumers (the schema_version 1.0 envelope) must
    surface tripwires under ``results.tripwires`` and add a
    top-level warning when at least one fires. Otherwise the
    advisory is silently dropped at the CLI/JSON boundary."""

    def test_envelope_carries_tripwires(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["provenance"] = {"source": "test"}
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        envelope = mv.build_audit_payload(
            result, target_path=str(manifest),
        )
        assert "tripwires" in envelope["results"]
        assert len(envelope["results"]["tripwires"]) == 1
        assert envelope["results"]["tripwires"][0]["category"] == "nested"

    def test_envelope_warning_surfaces_tripwire_categories(
        self, tmp_path: Path,
    ):
        entry = _flat_entry(0)
        entry["provenance"] = {"source": "test"}
        entry["schema_version"] = "2.0"
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        envelope = mv.build_audit_payload(
            result, target_path=str(manifest),
        )
        warnings_text = " ".join(envelope["warnings"]).lower()
        assert "tripwire" in warnings_text
        assert "issue #6" in warnings_text
        assert "nested" in warnings_text
        assert "versioned" in warnings_text

    def test_envelope_has_no_tripwire_warning_when_dormant(
        self, tmp_path: Path,
    ):
        manifest = _write_manifest(tmp_path, [_flat_entry(0)])
        result = mv.validate_manifest(manifest)
        envelope = mv.build_audit_payload(
            result, target_path=str(manifest),
        )
        warnings_text = " ".join(envelope["warnings"]).lower()
        assert "tripwire" not in warnings_text


class TestMarkdownRendersTripwireSection:
    def test_section_present_when_tripped(self, tmp_path: Path):
        entry = _flat_entry(0)
        entry["provenance"] = {"k": 1}
        manifest = _write_manifest(tmp_path, [entry])
        result = mv.validate_manifest(manifest)
        rendered = mv.render_report(result)
        assert "Schema-migration tripwire (Issue #6)" in rendered
        assert "[nested]" in rendered

    def test_section_absent_when_dormant(self, tmp_path: Path):
        manifest = _write_manifest(tmp_path, [_flat_entry(0)])
        result = mv.validate_manifest(manifest)
        rendered = mv.render_report(result)
        assert "Schema-migration tripwire" not in rendered


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
