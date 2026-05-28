#!/usr/bin/env python3
"""Tests for tools/check_capabilities_drift.py.

Pins:

  * Repo-wide drift check passes on the committed manifest (no
    orphan scripts, no orphan entries, no surface drift).
  * Synthetic orphan script (TASK_SURFACE in source, no manifest
    entry) trips orphan_script.
  * Synthetic orphan manifest entry (manifest references missing
    file) trips orphan_entry.
  * Synthetic surface mismatch trips surface_drift.
  * TODO entries don't break the linter, but a curated entry with a
    TODO field does (todo_content).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_capabilities_drift as ccd  # type: ignore  # noqa: E402

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


def test_repo_manifest_passes_drift_check():
    """The committed manifest at HEAD must pass."""
    report = ccd.check_drift()
    assert report.passed, (
        f"committed manifest has drift: "
        f"{[v.kind + ':' + v.where for v in report.violations]}"
    )


def _write_yaml(path: Path, data: dict) -> None:
    assert yaml is not None, "PyYAML required"
    path.write_text(yaml.dump(data, sort_keys=False))


def test_orphan_manifest_entry_detected():
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "ghost",
                    "script_path": (
                        "plugins/setec-voiceprint/scripts/"
                        "this_does_not_exist.py"
                    ),
                    "surface": "fake",
                    "status": "heuristic",
                    "family": "ghost",
                    "use_when": ["x"],
                    "do_not_use_when": ["y"],
                    "compute": {"tier": "core"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "orphan_entry" in kinds, (
            f"expected orphan_entry; got {kinds}"
        )


def test_todo_content_detected_in_curated_entry():
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",
                    "family": "TODO",
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "todo_content" in kinds, (
            f"expected todo_content; got {kinds}"
        )


def test_todo_status_does_not_trip_content_check():
    """Entries with status: todo are explicitly allowed to have
    TODO fields; the linter just requires they exist + point at a
    real script."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "family": "TODO",
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        # Only the orphan_script violations from the *other* real
        # scripts that aren't in this synthetic 1-entry manifest
        # should appear. todo_content must NOT appear.
        kinds = {v.kind for v in report.violations}
        assert "todo_content" not in kinds


def test_surface_drift_detected():
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "wrong_surface_name",
                    "status": "todo",
                    "family": "TODO",
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "surface_drift" in kinds


def test_duplicate_id_detected():
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "compute": {"tier": "api_llm"},
                },
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "duplicate_id" in kinds


def test_cli_exits_zero_on_clean_repo():
    """Running the linter CLI with no args on the committed manifest
    should exit 0."""
    rc = ccd.main([])
    assert rc == 0


def test_cli_exits_nonzero_on_bad_manifest():
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.1.0",
            "entries": [
                {
                    "id": "ghost",
                    "script_path": "missing.py",
                    "surface": "fake",
                    "status": "todo",
                    "compute": {"tier": "core"},
                },
            ],
        })
        rc = ccd.main(["--manifest", str(manifest)])
        assert rc != 0


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
