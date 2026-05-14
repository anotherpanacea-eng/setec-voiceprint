#!/usr/bin/env python3
"""Regression tests for editlens_to_manifest.py.

Targets the v1.49.0+ B.4 behavior: Pangram label ``-1`` now maps to
``ai_status: mixed`` with ``notes.composite_states: ["ai_edited"]``
instead of being silently dropped. Plus a backwards-compat check
that the older label-map without ``-1`` still works (i.e., operators
who do NOT want the new behavior can still get the old "drop -1"
semantics by simply not adding -1 to their label-map).

See ``internal/SPEC_authorship_states.md`` §7.1 + §10 (Phase B.4).
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

import editlens_to_manifest as etm  # type: ignore


# ---------- Helpers ----------


def _write_csv(path: Path, rows: list[dict]) -> Path:
    """Write a CSV with the supplied rows. Returns path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


def _build_args(
    source: Path,
    out: Path,
    text_dir: Path,
    *,
    label_map: str | None = None,
    preset: str | None = None,
    mixed_composite_states: str | None = None,
) -> "argparse.Namespace":  # type: ignore  # noqa: F821
    import argparse
    return argparse.Namespace(
        source=str(source),
        out=str(out),
        text_dir=str(text_dir),
        text_column="text" if not preset else None,
        label_column="label" if not preset else None,
        label_map=label_map,
        register=None,
        language_status=None,
        use="validation",
        notes_columns=None,
        mixed_composite_states=mixed_composite_states,
        preset=preset,
        max_rows=None,
        allow_public_output=True,  # tests write to tmp_path
    )


# ---------- Preset label maps ----------


class TestPresetsIncludeMixed:
    """All three built-in presets should include ``-1=mixed`` after
    B.4, so an operator who picks ``--preset editlens_nonnative``
    (etc.) automatically gets the new behavior."""

    def test_editlens_nonnative_preset_maps_minus_one_to_mixed(self):
        assert etm.PRESETS["editlens_nonnative"]["label_map"]["-1"] == "mixed"

    def test_editlens_test_preset_maps_minus_one_to_mixed(self):
        assert etm.PRESETS["editlens_test"]["label_map"]["-1"] == "mixed"

    def test_editlens_human_detectors_preset_maps_minus_one_to_mixed(self):
        assert etm.PRESETS["editlens_human_detectors"]["label_map"]["-1"] == "mixed"

    def test_existing_label_maps_unchanged(self):
        """B.4 must not retroactively re-classify existing values.
        0 stays pre_ai_human; 1 stays ai_generated."""
        for preset_name in (
            "editlens_nonnative", "editlens_test", "editlens_human_detectors",
        ):
            lm = etm.PRESETS[preset_name]["label_map"]
            assert lm["0"] == "pre_ai_human"
            assert lm["1"] == "ai_generated"


class TestDefaultMixedCompositeStates:
    """Module-level default for composite_states when ai_status=mixed."""

    def test_default_is_ai_edited(self):
        assert etm.DEFAULT_MIXED_COMPOSITE_STATES == ("ai_edited",)


# ---------- End-to-end: label -1 → mixed + composite_states ----------


class TestB4MixedLabelEndToEnd:
    """A row with label=-1 should land with ai_status=mixed AND
    notes.composite_states=['ai_edited'] (the default) so the B.2
    validator soft check is satisfied."""

    def test_label_minus_one_produces_mixed_with_composite_states(
        self, tmp_path: Path,
    ):
        source = tmp_path / "editlens.csv"
        _write_csv(source, [
            {"text": "Pure human prose.", "label": "0"},
            {"text": "Pure AI prose.", "label": "1"},
            {"text": "Edited prose.", "label": "-1"},
        ])
        out = tmp_path / "manifest.jsonl"
        text_dir = tmp_path / "text"
        args = _build_args(
            source, out, text_dir, preset="editlens_test",
        )
        assert etm.convert(args) == 0
        entries = [
            json.loads(line) for line in out.read_text().splitlines() if line
        ]
        assert len(entries) == 3
        statuses = [e["ai_status"] for e in entries]
        assert "pre_ai_human" in statuses
        assert "ai_generated" in statuses
        assert "mixed" in statuses
        # The mixed entry carries composite_states.
        mixed_entry = next(e for e in entries if e["ai_status"] == "mixed")
        notes_raw = mixed_entry["notes"]
        # notes can be a JSON string (legacy) or a dict; handle both.
        if isinstance(notes_raw, str):
            notes = json.loads(notes_raw)
        else:
            notes = notes_raw
        assert notes.get("composite_states") == ["ai_edited"]

    def test_pre_b4_label_map_still_drops_minus_one(
        self, tmp_path: Path,
    ):
        """An operator who explicitly passes the pre-B.4
        label-map (only 0 and 1) gets the old behavior: -1 rows are
        skipped. This is the opt-out path for anyone who prefers
        the v1.48.x behavior."""
        source = tmp_path / "editlens.csv"
        _write_csv(source, [
            {"text": "Human.", "label": "0"},
            {"text": "AI.", "label": "1"},
            {"text": "Edited.", "label": "-1"},
        ])
        out = tmp_path / "manifest.jsonl"
        text_dir = tmp_path / "text"
        args = _build_args(
            source, out, text_dir,
            label_map="0=pre_ai_human,1=ai_generated",
        )
        assert etm.convert(args) == 0
        entries = [
            json.loads(line) for line in out.read_text().splitlines() if line
        ]
        # Only the two non-minus-one rows survive.
        assert len(entries) == 2
        statuses = sorted(e["ai_status"] for e in entries)
        assert statuses == ["ai_generated", "pre_ai_human"]


class TestB4MixedCompositeStatesOverride:
    """--mixed-composite-states overrides the default for the
    operator who wants different sub-state granularity."""

    def test_override_to_two_sub_states(self, tmp_path: Path):
        source = tmp_path / "editlens.csv"
        _write_csv(source, [
            {"text": "Edited prose.", "label": "-1"},
        ])
        out = tmp_path / "manifest.jsonl"
        text_dir = tmp_path / "text"
        args = _build_args(
            source, out, text_dir, preset="editlens_test",
            mixed_composite_states="ai_assisted,ai_edited",
        )
        assert etm.convert(args) == 0
        entry = json.loads(out.read_text().strip())
        notes = entry["notes"]
        if isinstance(notes, str):
            notes = json.loads(notes)
        assert notes["composite_states"] == ["ai_assisted", "ai_edited"]

    def test_empty_override_omits_composite_states(self, tmp_path: Path):
        """Passing an empty --mixed-composite-states leaves
        composite_states off the entry. Useful for operators who
        want to surface the B.2 validator warning (e.g., during
        manual review)."""
        source = tmp_path / "editlens.csv"
        _write_csv(source, [
            {"text": "Edited prose.", "label": "-1"},
        ])
        out = tmp_path / "manifest.jsonl"
        text_dir = tmp_path / "text"
        args = _build_args(
            source, out, text_dir, preset="editlens_test",
            mixed_composite_states="",
        )
        assert etm.convert(args) == 0
        entry = json.loads(out.read_text().strip())
        notes_raw = entry.get("notes")
        if isinstance(notes_raw, str):
            notes = json.loads(notes_raw)
        else:
            notes = notes_raw or {}
        assert "composite_states" not in notes


class TestB4ValidatorRoundTrip:
    """End-to-end: the B.4-emitted manifest (label=-1 → mixed +
    composite_states) should pass the B.2 validator cleanly
    (no errors, no warnings about missing composite_states)."""

    def test_mixed_with_composite_states_is_validator_clean(
        self, tmp_path: Path,
    ):
        source = tmp_path / "editlens.csv"
        _write_csv(source, [
            {"text": "x" * 200, "label": "0"},
            {"text": "y" * 200, "label": "1"},
            {"text": "z" * 200, "label": "-1"},
        ])
        out = tmp_path / "manifest.jsonl"
        text_dir = tmp_path / "text"
        args = _build_args(
            source, out, text_dir, preset="editlens_test",
        )
        assert etm.convert(args) == 0
        # Run the validator on the output manifest.
        sys.path.insert(0, str(ROOT))
        import manifest_validator as mv  # type: ignore
        result = mv.validate_manifest(str(out))
        issues = result.get("issues", [])
        # No errors on the mixed entry.
        errors = [
            i for i in issues
            if getattr(i, "level", None) == "error"
            or (isinstance(i, dict) and i.get("severity") == "error")
        ]
        assert not errors, f"validator errors: {errors[:5]}"
        # No "mixed without composite_states" warnings either —
        # B.4's whole point is to satisfy that B.2 soft check.
        warning_msgs = []
        for i in issues:
            msg = (
                getattr(i, "message", None)
                or (isinstance(i, dict) and i.get("message"))
                or str(i)
            )
            warning_msgs.append(msg)
        assert not any(
            "composite_states" in m and "mixed" in m
            for m in warning_msgs
        ), f"got composite_states warning: {warning_msgs[:5]}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
