#!/usr/bin/env python3
"""Regression tests for idiolect/keyness extraction."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idiolect_detector import directory_entries, manifest_entries, run_idiolect_detector
from manifest_validator import validate_manifest


FIXTURE_DIR = ROOT / "test_data" / "idiolect_oracle"
TARGET_DIR = FIXTURE_DIR / "target"
REFERENCE_DIR = FIXTURE_DIR / "reference"
MANIFEST = FIXTURE_DIR / "manifest.jsonl"


def test_idiolect_detector_surfaces_preservation_phrases() -> None:
    result = run_idiolect_detector(
        directory_entries(TARGET_DIR),
        directory_entries(REFERENCE_DIR),
        n_values=(1, 2, 3),
    )

    preservation = {row["phrase"] for row in result["preservation_list"]}
    assert "moral weather" in preservation
    assert "quiet calculus" in preservation
    assert result["task_surface"] == "voice_coherence"
    assert "PRIVATE - DO NOT SHARE" in result["privacy"]


def test_zero_reference_phrase_uses_count_smoothing() -> None:
    result = run_idiolect_detector(
        directory_entries(TARGET_DIR),
        directory_entries(REFERENCE_DIR),
        n_values=(2,),
    )

    rows = result["rankings"][2]["idiolectic"]
    moral_weather = next(row for row in rows if row["phrase"] == "moral weather")
    assert moral_weather["reference_count"] == 0
    assert moral_weather["log2_ratio"] > 4.0


def test_manifest_filters_load_target_and_reference_entries() -> None:
    result = run_idiolect_detector(
        manifest_entries(MANIFEST, "use=idiolect,persona=synthetic_voice"),
        manifest_entries(MANIFEST, "use=negative_baseline"),
        n_values=(2,),
    )

    preservation = {row["phrase"] for row in result["preservation_list"]}
    assert "moral weather" in preservation
    assert result["target_summary"]["n_files"] == 2
    assert result["reference_summary"]["n_files"] == 2


def test_small_preservation_top_still_uses_ngram_quotas() -> None:
    result = run_idiolect_detector(
        directory_entries(TARGET_DIR),
        directory_entries(REFERENCE_DIR),
        n_values=(1, 2, 3),
        preservation_top=4,
    )

    n_values = {row["n"] for row in result["preservation_list"]}
    assert 1 in n_values
    assert 2 in n_values


def test_manifest_validator_privacy_ratchet_includes_idiolect(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("Moral weather and quiet calculus.", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "sample",
            "path": str(sample),
            "ai_status": "pre_ai_human",
            "use": ["idiolect"],
            "privacy": "shareable",
        })
        + "\n",
        encoding="utf-8",
    )

    result = validate_manifest(manifest)
    warnings = [
        issue["message"]
        for issue in result["issues"]
        if issue["severity"] == "warning"
    ]
    assert any("Voiceprint sources" in warning for warning in warnings)
