#!/usr/bin/env python3
"""Tests for the output_schema envelope helper.

Pins the schema_version 1.0 contract documented in
``internal/SPEC_output_schema_unification.md``. Downstream consumers
(APODICTIC, ultrareview tooling) read these keys; the tests guard the
shape against accidental drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claim_license import ClaimLicense  # type: ignore
from output_schema import (  # type: ignore
    SCHEMA_VERSION,
    VALID_TASK_SURFACES,
    build_baseline_metadata,
    build_output,
)


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

CLAIM_LICENSE_KEYS = frozenset({
    "task_surface", "licenses", "does_not_license", "comparison_set",
    "length_range_words", "register_match", "language_match",
    "fpr_target", "confidence_interval_95", "additional_caveats",
    "references",
})


def _minimal_license(surface: str = "craft_restoration") -> ClaimLicense:
    return ClaimLicense(
        task_surface=surface,
        licenses="A test license.",
        does_not_license="It does not entitle authorship verdicts.",
    )


class TestEnvelopeShape:
    def test_required_top_level_keys_present(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="test_tool",
            version="0.1.0",
            target_path="draft.md",
            target_words=1000,
            baseline=None,
            results={"patterns": {}},
            claim_license=_minimal_license(),
        )
        assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version_is_pinned(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="t.md", target_words=0,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        assert env["schema_version"] == SCHEMA_VERSION == "1.0"

    def test_target_block_required_keys(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="draft.md",
            target_words=4523,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        assert env["target"] == {"path": "draft.md", "words": 4523}

    def test_target_extra_merges(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md",
            target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
            target_extra={"sentences": 3, "preprocessing": {"stripped": []}},
        )
        assert env["target"]["sentences"] == 3
        assert env["target"]["preprocessing"] == {"stripped": []}

    def test_baseline_is_null_when_not_supplied(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        assert env["baseline"] is None

    def test_claim_license_dict_has_11_keys(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        assert set(env["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_claim_license_rendered_starts_with_header(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        rendered = env["claim_license_rendered"]
        assert rendered.startswith("## What this result licenses")

    def test_warnings_default_to_empty_list(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
        )
        assert env["warnings"] == []

    def test_warnings_list_passes_through(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
            warnings=["text below floor", "spaCy not available"],
        )
        assert env["warnings"] == ["text below floor", "spaCy not available"]

    def test_ai_status_passes_through(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=10,
            baseline=None, results={},
            claim_license=_minimal_license(),
            ai_status="ai_generated_from_outline",
        )
        assert env["ai_status"] == "ai_generated_from_outline"

    def test_available_false_permits_null_claim_license(self):
        env = build_output(
            task_surface="craft_restoration",
            tool="t", version="0", target_path="x.md", target_words=0,
            baseline=None, results={},
            claim_license=None,
            available=False,
            warnings=["text too short"],
        )
        assert env["available"] is False
        assert env["claim_license"] is None
        assert env["claim_license_rendered"] is None


class TestEnvelopeValidation:
    def test_unknown_task_surface_raises(self):
        with pytest.raises(ValueError, match="Unknown task_surface"):
            build_output(
                task_surface="not_a_real_surface",
                tool="t", version="0", target_path="x.md",
                target_words=10, baseline=None, results={},
                claim_license=_minimal_license(),
            )

    def test_available_true_requires_claim_license(self):
        with pytest.raises(ValueError, match="claim_license is required"):
            build_output(
                task_surface="craft_restoration",
                tool="t", version="0", target_path="x.md",
                target_words=10, baseline=None, results={},
                claim_license=None,
                available=True,
            )

    def test_claim_license_surface_mismatch_raises(self):
        lic = _minimal_license(surface="voice_coherence")
        with pytest.raises(ValueError, match="does not match"):
            build_output(
                task_surface="craft_restoration",
                tool="t", version="0", target_path="x.md",
                target_words=10, baseline=None, results={},
                claim_license=lic,
            )

    def test_extra_collision_raises(self):
        with pytest.raises(ValueError, match="collides"):
            build_output(
                task_surface="craft_restoration",
                tool="t", version="0", target_path="x.md",
                target_words=10, baseline=None, results={},
                claim_license=_minimal_license(),
                extra={"task_surface": "stomp"},
            )

    def test_valid_task_surfaces_covers_known_surfaces(self):
        # Mirrors claim_license.TASK_SURFACE_LABELS.
        for s in [
            "smoothing_diagnosis", "voice_coherence",
            "voice_coherence_acquisition", "validation",
            "calibration", "craft_restoration",
            "metric_targeted_restoration",
        ]:
            assert s in VALID_TASK_SURFACES


class TestBaselineMetadata:
    def test_required_keys(self):
        b = build_baseline_metadata(
            n_files=3, words=12000,
            files_loaded=[Path("a.txt"), Path("b.txt")],
            files_skipped=[],
        )
        assert b["n_files"] == 3
        assert b["words"] == 12000
        assert b["files_loaded"] == ["a.txt", "b.txt"]
        assert b["files_skipped"] == []

    def test_minimal_call_omits_optional_keys(self):
        b = build_baseline_metadata(n_files=0, words=0)
        assert b == {"n_files": 0, "words": 0}

    def test_register_and_split_pass_through(self):
        b = build_baseline_metadata(
            n_files=5, words=20000,
            register="literary-fiction", split="train",
        )
        assert b["register"] == "literary-fiction"
        assert b["split"] == "train"

    def test_paths_stringify(self):
        b = build_baseline_metadata(
            n_files=1, words=100,
            files_loaded=[Path("/abs/x.md")],
        )
        assert b["files_loaded"] == ["/abs/x.md"]
