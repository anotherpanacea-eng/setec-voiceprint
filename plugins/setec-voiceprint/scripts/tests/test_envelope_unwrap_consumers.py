#!/usr/bin/env python3
"""Consumers accept the producers' schema 1.0 ``--json`` envelope.

Producers nest their payload under ``results``. ``restoration_packet``
(``--bigram-json`` reads ``diffs``), ``confounder_audit``
(``--agency-json`` reads ``densities_per_1k``) and
``evidentiary_conditions_gate`` (reads ``compression`` /
``ranked_confounders``) read those keys at the top level, so feeding
them a producer's envelope silently dropped the evidence. These tests
build the envelope with each producer's own ``build_audit_payload`` and
check the consumer reads the same evidence it reads from the legacy
bare payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import agency_abstraction_audit as agency_mod  # type: ignore
import bigram_diff  # type: ignore
import confounder_audit as ca  # type: ignore
import evidentiary_conditions_gate as ecg  # type: ignore
import paragraph_audit  # type: ignore
import restoration_packet as rp  # type: ignore
import variance_audit  # type: ignore
from output_schema import unwrap_envelope  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = ROOT / "test_data" / "restoration_packet"

# Institutional, agentless prose so the agency densities are nonzero.
_TEXT = (
    "It was determined that the implementation of the policy would be "
    "deferred. The evaluation of the proposal was conducted by the "
    "committee, and the recommendation was approved. Consideration of "
    "the administration's position was given. The organization's "
    "assessment indicated that further documentation was required.\n\n"
    "A review of the regulations was completed. The department noted "
    "that the authorization of the expenditure was postponed. The "
    "institution's management of the allocation was examined, and the "
    "conclusion was reached that the documentation was insufficient. "
    "The stakeholders were informed of the determination.\n\n"
    "The coordination of the response was assigned to the office. An "
    "analysis of the situation was prepared, and the findings were "
    "circulated. The agency's interpretation of the requirement was "
    "accepted. Implementation of the changes was scheduled for the "
    "following quarter by the administration."
)


def _bigram_envelope(bare: dict) -> dict:
    return bigram_diff.build_audit_payload(
        target_path="synthetic_target.txt",
        target_counts={"DET-ADJ-NOUN": bare["target_bigrams"]},
        cluster_loaded=[Path("cluster_a.txt")],
        cluster_skipped=[],
        pooled_diff=bare["diffs"]["pooled"]["rows"],
        mean_diff=None,
        top=len(bare["diffs"]["pooled"]["rows"]),
        alpha=bare["smoothing_alpha"],
        min_count=bare["min_count"],
    )


def _packet_keys(packets) -> list[tuple]:
    return [
        (p.id, p.signal, p.targetability, json.dumps(p.evidence, sort_keys=True))
        for p in packets
    ]


def test_restoration_packet_reads_bigram_diffs_from_envelope():
    bare = json.loads(
        (_FIXTURES / "synthetic_bigram_diff.json").read_text("utf-8")
    )
    env = _bigram_envelope(bare)
    assert "diffs" not in env  # the shape that used to be dropped

    kwargs = dict(
        variance=None, voice=None, idiolect=None, aic=None,
        max_targets=5, targetability_filter=None,
    )
    from_bare = rp.build_packets(bigram=bare, **kwargs)
    from_env = rp.build_packets(bigram=env, **kwargs)
    assert from_bare, "fixture should yield bigram packets"
    assert _packet_keys(from_env) == _packet_keys(from_bare)


def test_confounder_audit_reads_agency_densities_from_envelope():
    audit = agency_mod.audit_agency_abstraction(_TEXT)
    env = agency_mod.build_audit_payload(
        audit, target_path="t.txt",
        baseline_block=None, baseline_comparison=None,
    )
    assert "densities_per_1k" not in env
    assert "densities_per_1k" in env["results"]

    from_bare = ca.extract_observations(agency=audit)
    from_env = ca.extract_observations(agency=env)
    assert from_bare, "agency audit should yield observations"
    assert from_env == from_bare


def test_evidentiary_gate_counts_envelope_surfaces():
    para = paragraph_audit.audit_paragraphs(_TEXT)
    para_env = paragraph_audit.build_audit_payload(
        para, target_path="t.txt",
        baseline_block=None, baseline_comparison=None,
    )
    assert "compression" not in para_env
    conf_report = ca.analyze_confounders(
        agency=agency_mod.audit_agency_abstraction(_TEXT),
    )
    conf_env = ca.build_audit_payload(conf_report, target_path="t.txt")
    assert "ranked_confounders" not in conf_env

    from_bare = ecg.gate(paragraph=para, confounder=conf_report)
    from_env = ecg.gate(paragraph=para_env, confounder=conf_env)
    assert from_bare["indicators"]["n_audit_surfaces"] == 1
    assert from_env["indicators"]["n_audit_surfaces"] == 1
    assert from_env["indicators"]["has_confounder_diagnosis"] is (
        from_bare["indicators"]["has_confounder_diagnosis"]
    )
    assert from_env["indicators"]["has_confounder_diagnosis"] is True
    assert from_env["posture"] == from_bare["posture"]


def test_evidentiary_gate_reads_envelope_target_and_baseline_metadata():
    """The envelope moves preprocessing under ``target`` and baseline
    counts under ``baseline``; the gate's indicators must match the
    legacy bare payload."""
    audit = variance_audit.audit_text(_TEXT, do_tier2=False)
    variance_bare = {
        "audit": audit,
        "compression": variance_audit.classify_compression(audit),
        "preprocessing": {"strip_ratio": 0.25},
        "baseline": {"n_files": 3},
    }
    variance_env = variance_audit.build_audit_payload(
        variance_bare, target_path="t.txt",
    )
    para = paragraph_audit.audit_paragraphs(_TEXT)
    para_bare = dict(para, baseline_block={"n_files": 7})
    para_env = paragraph_audit.build_audit_payload(
        para, target_path="t.txt",
        baseline_block={"n_files": 7, "n_words": 900},
        baseline_comparison=None,
    )
    assert "baseline_block" not in para_env
    assert "preprocessing" not in variance_env

    from_bare = ecg.gate(variance=variance_bare, paragraph=para_bare)
    from_env = ecg.gate(variance=variance_env, paragraph=para_env)
    for key in (
        "target_length", "baseline_size", "strip_ratio",
        "n_audit_surfaces",
    ):
        assert from_env["indicators"][key] == from_bare["indicators"][key], key
    assert from_env["indicators"]["strip_ratio"] == 0.25
    assert from_env["indicators"]["baseline_size"] == 7
    assert from_env["indicators"]["target_length"] > 0
    assert from_env["indicators"]["n_audit_surfaces"] == 2


def test_unavailable_envelope_stays_unusable():
    para = paragraph_audit.audit_paragraphs(_TEXT)
    env = paragraph_audit.build_audit_payload(
        para, target_path="t.txt",
        baseline_block=None, baseline_comparison=None,
    )
    env["available"] = False
    report = ecg.gate(paragraph=env)
    assert report["indicators"]["n_audit_surfaces"] == 0
    assert unwrap_envelope(env)["available"] is False


def test_legacy_bare_payload_passes_through_unchanged():
    bare = json.loads(
        (_FIXTURES / "synthetic_variance.json").read_text("utf-8")
    )
    assert unwrap_envelope(bare) is bare
    assert unwrap_envelope(None) is None
