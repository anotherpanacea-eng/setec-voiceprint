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
            "schema_version": "0.2.0",
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


def test_duplicate_script_path_detected():
    """Regression for PR #129 review: two entries with different ids
    but the same script_path used to silently collide in the
    by_script_path index, dropping the first entry from every
    downstream check. The manifest is one-source-of-truth per
    script, so duplicate script_paths are themselves drift to
    surface."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.2.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "compute": {"tier": "api_llm"},
                },
                {
                    "id": "narrative_decision_audit_v2",
                    "script_path": real_script,  # same path, different id
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "duplicate_script_path" in kinds, (
            f"expected duplicate_script_path; got {kinds}"
        )
        # The original duplicate_id check should NOT fire here — the
        # ids are distinct. This pins that the two checks are
        # independent and a duplicate script_path can land alone.
        assert "duplicate_id" not in kinds


def test_stable_handoff_requires_references():
    """v0.3.0: handoff: stable entries must carry a non-empty
    `references` list so consumers can find the integration spec.
    A stable entry with empty references trips
    stable_without_references."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",
                    "family": "narrative-decision",
                    "use_when": ["short story"],
                    "do_not_use_when": ["essay"],
                    "handoff": "stable",
                    "consumers": ["apodictic"],
                    "references": [],  # empty — should trip
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_without_references" in kinds, (
            f"expected stable_without_references; got {kinds}"
        )


def test_stable_handoff_with_references_passes():
    """v0.3.0: a stable entry WITH references should not trip
    stable_without_references (it may trip other checks, but not
    this one)."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",
                    "family": "narrative-decision",
                    "use_when": ["short story"],
                    "do_not_use_when": ["essay"],
                    "handoff": "stable",
                    "consumers": ["apodictic"],
                    "references": ["plugins/setec-voiceprint/references/narrative-decision-audit-spec.md"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_without_references" not in kinds


def test_handoff_typo_detected():
    """Regression for PR #130 review: `handoff: stabel` (or any
    other typo) used to pass the linter silently because the
    stable_without_references check only inspected entries whose
    handoff was literally "stable" — a typo fell through. The
    downstream consequence was `capabilities.py list --handoff
    stable` silently dropping the entry from APODICTIC's pinned
    surface. The new invalid_handoff check pins this case."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "handoff": "stabel",  # typo
                    "consumers": ["apodictic"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_handoff" in kinds, (
            f"expected invalid_handoff for typo; got {kinds}"
        )
        # And stable_without_references must NOT fire for the typo —
        # the entry isn't actually "stable", it's something else.
        assert "stable_without_references" not in kinds


def test_missing_handoff_field_detected():
    """v0.3.0 entries without a handoff field trip missing_handoff
    so pre-v0.3 manifests get caught."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    # no handoff field
                    "consumers": [],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "missing_handoff" in kinds


def test_consumers_must_be_list_not_scalar():
    """A bare-string consumers value (e.g., `consumers: apodictic`
    instead of `consumers: [apodictic]`) silently dropped the
    entry from --consumer X filters because the filter does an
    `in` check against the value. New invalid_consumers_type
    check catches this."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "handoff": "experimental",
                    "consumers": "apodictic",  # scalar, not list
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_consumers_type" in kinds


def test_experimental_handoff_does_not_require_references():
    """The stable_without_references check only applies to
    handoff: stable. Experimental entries are allowed empty
    references (interface may evolve before stabilization)."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        real_script = (
            "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
        )
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": real_script,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",
                    "family": "narrative-decision",
                    "use_when": ["short story"],
                    "do_not_use_when": ["essay"],
                    "handoff": "experimental",  # not stable
                    "consumers": [],
                    "references": [],  # empty is OK for experimental
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_without_references" not in kinds


# ---------- Check 8: handoff: stable must not be status: todo ------


def test_stable_todo_entry_detected():
    """R1 build-review follow-up: a handoff: stable entry that is
    still status: todo trips stable_is_todo. This is the exact
    incoherence the 5 promoted consumer surfaces had — `emit`
    advertised them as stable while `list --handoff stable` hid
    them as todo."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": _REAL_SCRIPT,
                    "surface": "narrative_decision_audit",
                    "status": "todo",  # incoherent with handoff: stable
                    "family": "TODO",
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "handoff": "stable",
                    "consumers": ["apodictic"],
                    "references": ["plugins/setec-voiceprint/references/x.md"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_is_todo" in kinds, (
            f"expected stable_is_todo; got {kinds}"
        )


def test_curated_stable_entry_passes_check8():
    """A handoff: stable entry with a real status and fully-filled
    family/purpose/use_when does not trip stable_is_todo."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": _REAL_SCRIPT,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",
                    "family": "narrative-decision",
                    "purpose": "A real, curated purpose.",
                    "use_when": ["short story"],
                    "do_not_use_when": ["essay"],
                    "handoff": "stable",
                    "consumers": ["apodictic"],
                    "references": ["plugins/setec-voiceprint/references/x.md"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_is_todo" not in kinds


def test_stable_entry_with_placeholder_content_detected():
    """A handoff: stable entry that left a real (non-todo) status but
    kept TODO family/purpose/use_when placeholders also trips
    stable_is_todo — a stable contract must never be placeholders."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": _REAL_SCRIPT,
                    "surface": "narrative_decision_audit",
                    "status": "heuristic",  # non-todo, but...
                    "family": "TODO",  # ...placeholder content remains
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "handoff": "stable",
                    "consumers": ["apodictic"],
                    "references": ["plugins/setec-voiceprint/references/x.md"],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_is_todo" in kinds, (
            f"expected stable_is_todo for placeholder content; got {kinds}"
        )


def test_todo_status_on_non_stable_entry_does_not_trip_check8():
    """status: todo is still fine for a non-stable entry (handoff:
    none/experimental). Check 8 only fires on stable entries."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0",
            "entries": [
                {
                    "id": "narrative_decision_audit",
                    "script_path": _REAL_SCRIPT,
                    "surface": "narrative_decision_audit",
                    "status": "todo",
                    "family": "TODO",
                    "use_when": ["TODO"],
                    "do_not_use_when": ["TODO"],
                    "handoff": "none",  # not stable
                    "consumers": [],
                    "compute": {"tier": "api_llm"},
                },
            ],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "stable_is_todo" not in kinds


# ---------- R1 field-bundle linting -------------------------------

_REAL_SCRIPT = (
    "plugins/setec-voiceprint/scripts/narrative_decision_audit.py"
)


def _entry_with_bundle(**overrides) -> dict:
    """A minimal curated entry carrying the R1 bundle. Overrides let each
    test mutate exactly one bundle field to isolate the failure mode."""
    entry = {
        "id": "narrative_decision_audit",
        "script_path": _REAL_SCRIPT,
        "surface": "narrative_decision_audit",
        "status": "todo",
        "handoff": "experimental",
        "consumers": ["apodictic"],
        "compute": {"tier": "api_llm"},
        "min_setec_version": "1.86.0",
        "json_delivery": "stdout",
        "inputs": [
            {"flag": "target", "type": "path", "required": True},
        ],
    }
    entry.update(overrides)
    return entry


def test_r1_bundle_missing_json_delivery_fails():
    """(c) a fragment carrying min_setec_version but missing json_delivery
    trips invalid_r1_bundle."""
    if yaml is None:
        return
    entry = _entry_with_bundle()
    del entry["json_delivery"]
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" in kinds, (
            f"expected invalid_r1_bundle; got {kinds}"
        )


def test_r1_bundle_missing_inputs_fails():
    """(c) a fragment carrying min_setec_version but missing inputs trips
    invalid_r1_bundle."""
    if yaml is None:
        return
    entry = _entry_with_bundle()
    del entry["inputs"]
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" in kinds


def test_r1_bundle_bad_semver_fails():
    """min_setec_version must be a valid semver; '1.86' (two components)
    trips the bundle check."""
    if yaml is None:
        return
    entry = _entry_with_bundle(min_setec_version="1.86")
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" in kinds


def test_r1_bundle_enum_requires_values():
    """An inputs[] entry of type 'enum' must carry a non-empty values list."""
    if yaml is None:
        return
    entry = _entry_with_bundle(inputs=[
        {"flag": "--judge", "type": "enum", "required": False},  # no values
    ])
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" in kinds


def test_r1_bundle_complete_passes():
    """A complete, valid bundle does NOT trip invalid_r1_bundle (it may trip
    other checks, but not this one)."""
    if yaml is None:
        return
    entry = _entry_with_bundle()
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" not in kinds


def test_fragment_without_marker_is_bundle_exempt():
    """(d) a fragment WITHOUT min_setec_version is not required to carry the
    bundle — no invalid_r1_bundle violation even with no json_delivery /
    inputs. This is what keeps the ~73 reference-tagged / internal entries
    untouched."""
    if yaml is None:
        return
    entry = {
        "id": "narrative_decision_audit",
        "script_path": _REAL_SCRIPT,
        "surface": "narrative_decision_audit",
        "status": "todo",
        "handoff": "none",
        "consumers": [],
        "compute": {"tier": "api_llm"},
        # no min_setec_version, no json_delivery, no inputs
    }
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "capabilities.yaml"
        _write_yaml(manifest, {
            "schema_version": "0.3.0", "entries": [entry],
        })
        report = ccd.check_drift(manifest)
        kinds = {v.kind for v in report.violations}
        assert "invalid_r1_bundle" not in kinds


def test_validate_r1_bundle_exempts_unmarked_entry():
    """The validator unit returns [] for an entry with no marker."""
    assert ccd.validate_r1_bundle({"id": "x"}) == []


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
