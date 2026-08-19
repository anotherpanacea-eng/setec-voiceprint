#!/usr/bin/env python3
"""Tests for tools/gen_calibration_readiness.py.

Pins:

  * The committed calibration-readiness.md is in sync with the manifest
    (--check passes); this is the "keep updated" guarantee.
  * Readiness is derived from `status` (the calibration-maturity field).
  * "Runs without your corpus?" is False exactly when a baseline / manifest
    is required, True otherwise.
  * api_llm-tier entries surface an LLM-key requirement.
  * The generated block carries both section tables and the legend.
  * replace_region round-trips and rejects a doc missing the markers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

pytest.importorskip("yaml")

import gen_calibration_readiness as gcr  # type: ignore  # noqa: E402


def _entry(eid: str) -> dict:
    manifest = gcr.load_manifest()
    for e in manifest["entries"]:
        if e["id"] == eid:
            return e
    raise AssertionError(f"no manifest entry {eid!r}")


def test_committed_doc_is_fresh():
    """The 'keep updated' contract: committed doc matches generator output."""
    assert gcr.main(["--check"]) == 0


def test_readiness_tracks_status():
    assert gcr.derive(_entry("variance_audit"))["readiness"] == "Empirical (provisional)"
    assert gcr.derive(_entry("aic_pattern_audit"))["readiness"] == "Heuristic (uncalibrated)"
    assert gcr.derive(_entry("binoculars_audit"))["readiness"] == "Literature-anchored"


def test_runs_without_corpus_flag():
    # target-only audits run standalone
    assert gcr.derive(_entry("variance_audit"))["runs_without_corpus"] is True
    assert gcr.derive(_entry("binoculars_audit"))["runs_without_corpus"] is True
    # baseline/manifest-required audits do not
    assert gcr.derive(_entry("voice_distance"))["runs_without_corpus"] is False
    assert gcr.derive(_entry("idiolect_detector"))["runs_without_corpus"] is False
    assert gcr.derive(_entry("validation_harness"))["runs_without_corpus"] is False


def test_baseline_required_with_size_hint():
    supplies = gcr.derive(_entry("voice_distance"))["supplies"]
    joined = " ".join(supplies)
    assert "required" in joined
    assert "20K" in joined  # scraped from use_when


def test_api_llm_implies_key():
    supplies = gcr.derive(_entry("narrative_decision_audit"))["supplies"]
    assert any("LLM API access" in s for s in supplies)


def test_validation_harness_wants_labeled_corpus():
    supplies = gcr.derive(_entry("validation_harness"))["supplies"]
    assert any("corpus_manifest.jsonl" in s for s in supplies)


def test_tooling_grouping():
    assert gcr.derive(_entry("validation_harness"))["is_tooling"] is True
    assert gcr.derive(_entry("dependency_check"))["is_tooling"] is True
    assert gcr.derive(_entry("variance_audit"))["is_tooling"] is False


def test_curated_acquisition_rows_show_real_source_and_locality():
    gmail = gcr.derive(_entry("acquire_gmail_sent"))
    assert gmail["readiness"] == "Operational tooling (calibration N/A)"
    assert gmail["is_tooling"] is True
    assert any("Google Takeout" in item for item in gmail["supplies"])
    assert gmail["hardware"] == "CPU + local I/O"

    courtlistener = gcr.derive(_entry("acquire_courtlistener"))
    assert any("CourtListener" in item for item in courtlistener["supplies"])
    assert courtlistener["hardware"] == "CPU + network"

    # The source list is local, but the actual PDFs are fetched over the network.
    assert gcr.derive(_entry("acquire_pdf_urls"))["hardware"] == "CPU + network"

    near_dup = gcr.derive(_entry("near_dup_dedup"))
    assert len(near_dup["supplies"]) == len(set(near_dup["supplies"]))
    assert any("acquisition manifest" in item for item in near_dup["supplies"])
    assert not any("labeled corpus" in item for item in near_dup["supplies"])

    remediation = gcr.derive(_entry("passage_remediation"))
    assert not any("labeled corpus" in item for item in remediation["supplies"])
    assert len(remediation["supplies"]) == 2

    shingle = gcr.derive(_entry("shingle_dedup"))
    assert shingle["supplies"] == [
        "explicit staged-descriptor JSONL or exact-pinned local index, as required by the selected mode (required)",
        "for a compatible immutable checkpoint directory (optional)",
    ]

    pipeline = gcr.derive(_entry("gmail_author_pipeline"))
    assert "--request (required)" in pipeline["supplies"]
    assert pipeline["hardware"] == "CPU + local I/O"
    assert near_dup["hardware"] == "CPU + local I/O"


def test_structured_non_corpus_required_inputs_are_preserved():
    export = gcr.derive(_entry("author_corpus_export"))
    for flag in (
        "--source-manifest", "--register-map", "--allowed-ai-status",
        "--persona", "--hmac-key",
    ):
        assert f"{flag} (required)" in export["supplies"]
    assert "--output-dir (required)" not in export["supplies"]


def test_block_has_both_tables_and_legend():
    block = gcr.render_block(gcr.load_manifest())
    assert "### Evidence surfaces (run on a draft)" in block
    assert "### Runway & calibration tooling" in block
    assert "**Readiness legend.**" in block
    assert "`variance_audit`" in block


def test_replace_region_round_trip():
    doc = f"intro\n\n{gcr.BEGIN_MARKER}\nOLD\n{gcr.END_MARKER}\n\noutro\n"
    out = gcr.replace_region(doc, "NEW BLOCK")
    assert "NEW BLOCK" in out
    assert "OLD" not in out
    assert out.startswith("intro")
    assert out.rstrip().endswith("outro")


def test_replace_region_requires_markers():
    with pytest.raises(ValueError):
        gcr.replace_region("no markers here", "x")
