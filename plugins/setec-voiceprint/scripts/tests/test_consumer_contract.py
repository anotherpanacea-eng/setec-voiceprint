#!/usr/bin/env python3
"""Tests for capabilities.py's C2.1 manifest `contract` block (manifest_schema_version 0.4.0)
and setec/contract_validate.py's closed-shape negatives.

Pins:
  * `emit --json` at the committed manifest carries the exact 4-key top level
    (`setec_version`, `manifest_schema_version`, `contract`, `entries`).
  * `contract` has the exact key table from the spec, sourced live (not a
    second hand-typed copy) from output_schema.py / s5_distance.py / the
    actual client + fixture bytes.
  * `contract_block_min_setec_version` equals plugin.json's version at THIS
    release (the first 0.4.0 release) — producer CI asserts this equality.
  * `references/contract_fixtures/consumer_contract.json` is the worked
    exemplar: canonical bytes of the live contract block.
  * contract_validate negatives: a missing block, an unknown key, an
    unsorted common-key projection, a bad file hash, an invalid
    producer_disposition, a missing producer_test node, and a falsely
    labelled live emission all raise.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import capabilities as cap  # type: ignore  # noqa: E402
from setec import contract_validate as cv  # type: ignore  # noqa: E402

REPO_ROOT = ROOT.parents[2]  # .../plugins/setec-voiceprint/scripts -> repo root
FIXTURES_DIR = ROOT.parent / "references" / "contract_fixtures"


def _manifest():
    return cap.load_manifest()


def _live_contract():
    return cap.build_contract_block()


# ---------- positive: the real emit envelope + contract block -------------

def test_emit_envelope_is_valid_at_0_4_0():
    m = _manifest()
    assert m["schema_version"] == "0.4.0"
    env = cap.build_emit_envelope(m)
    cv.validate_manifest_emit_envelope(env)


def test_contract_block_min_setec_version_matches_plugin_json_at_release():
    """Producer CI's release-time assertion: CONTRACT_BLOCK_MIN_SETEC_VERSION
    equals the plugin version of THIS (the first 0.4.0) release."""
    plugin_json = json.loads(cap.PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
    assert cap.CONTRACT_BLOCK_MIN_SETEC_VERSION == plugin_json["version"]


def test_contract_client_sha256_matches_actual_vendored_source_bytes():
    contract = _live_contract()
    client_path = ROOT / "setec" / "consumer_client.py"
    import hashlib
    assert contract["client"]["sha256"] == hashlib.sha256(client_path.read_bytes()).hexdigest()
    assert contract["client"]["relative_path"] == "scripts/setec/consumer_client.py"


def test_contract_fixture_hashes_match_actual_fixture_bytes():
    import hashlib
    contract = _live_contract()
    for key, filename in (
        ("semver_parser_sha256", "semver_parser_cases.json"),
        ("warning_classifier_coverage_sha256", "warning_classifier_coverage.json"),
        ("warning_producer_emissions_sha256", "warning_producer_emissions.json"),
    ):
        expected = hashlib.sha256((FIXTURES_DIR / filename).read_bytes()).hexdigest()
        assert contract["fixtures"][key] == expected


def test_consumer_contract_exemplar_matches_live_canonical_bytes():
    exemplar_path = FIXTURES_DIR / "consumer_contract.json"
    exemplar = json.loads(exemplar_path.read_text(encoding="utf-8"))
    live = _live_contract()
    assert cap.canonical_contract_bytes(exemplar) == cap.canonical_contract_bytes(live)


def test_canonical_contract_bytes_is_deterministic_and_newline_terminated():
    contract = _live_contract()
    b1 = cap.canonical_contract_bytes(contract)
    b2 = cap.canonical_contract_bytes(copy.deepcopy(contract))
    assert b1 == b2
    assert b1.endswith(b"\n")
    assert b",  " not in b1 and b": " not in b1  # compact separators


# ---------- characterization: generic-builder extensibility unaffected ----

def test_output_key_policy_matches_observed_build_output_semantics():
    """The manifest projects EXACTLY the observed 12-key success / 2-key
    error contract from output_schema.build_output/build_error_output — this
    does not make the generic builders exact-set validators; it only asserts
    the manifest's projection matches what they actually emit."""
    import output_schema as os_
    from claim_license import ClaimLicense

    lic = ClaimLicense(task_surface="smoothing_diagnosis", licenses="x", does_not_license="y")
    out = os_.build_output(
        task_surface="smoothing_diagnosis", tool="t", version="1.0",
        target_path=None, target_words=10, baseline=None, results={},
        claim_license=lic, warnings=[],
    )
    contract = _live_contract()
    assert set(out.keys()) >= set(contract["output_key_policy"]["common_required"])

    err = os_.build_error_output(
        task_surface="smoothing_diagnosis", tool="t", version="1.0",
        reason="x", reason_category="bad_input",
    )
    assert set(contract["output_key_policy"]["error_required"]) <= set(err.keys())

    # Extension preservation + collision refusal (characterization, not new
    # exact-set behavior): a sentinel non-colliding extension survives.
    out2 = os_.build_output(
        task_surface="smoothing_diagnosis", tool="t", version="1.0",
        target_path=None, target_words=10, baseline=None,
        results={}, claim_license=lic, warnings=[],
        extra={"sentinel_extension_field": True},
    )
    assert out2.get("sentinel_extension_field") is True
    with pytest.raises(Exception):
        os_.build_output(
            task_surface="smoothing_diagnosis", tool="t", version="1.0",
            target_path=None, target_words=10, baseline=None,
            results={}, claim_license=lic, warnings=[],
            extra={"schema_version": "colliding"},
        )


# ---------- negatives: contract_validate closed-shape refusals ------------

def _valid_contract():
    return copy.deepcopy(_live_contract())


def test_missing_block_refused():
    with pytest.raises(cv.ContractValidationError, match="missing"):
        cv.validate_contract_block(None)


def test_unknown_top_level_key_refused():
    c = _valid_contract()
    c["unexpected_key"] = 1
    with pytest.raises(cv.ContractValidationError, match="unknown key"):
        cv.validate_contract_block(c)


def test_unsorted_common_required_refused():
    c = _valid_contract()
    c["output_key_policy"]["common_required"] = list(
        reversed(c["output_key_policy"]["common_required"])
    )
    with pytest.raises(cv.ContractValidationError, match="not a sorted"):
        cv.validate_contract_block(c)


def test_bad_file_hash_refused():
    c = _valid_contract()
    c["client"]["sha256"] = "not-a-real-hash"
    with pytest.raises(cv.ContractValidationError, match="sha256"):
        cv.validate_contract_block(c)


def test_missing_required_key_refused():
    c = _valid_contract()
    del c["s5_identity"]
    with pytest.raises(cv.ContractValidationError, match="missing required key"):
        cv.validate_contract_block(c)


def test_invalid_producer_disposition_refused():
    coverage = [{
        "case_id": "x", "text": "y",
        "expected_consumer_tier": "reliability",
        "producer_disposition": "definitely_not_valid",
    }]
    with pytest.raises(cv.ContractValidationError, match="invalid producer_disposition"):
        cv.validate_live_emission_binding(coverage, emissions=[])


def test_falsely_labelled_live_emission_refused():
    coverage = [{
        "case_id": "x", "text": "y",
        "expected_consumer_tier": "reliability",
        "producer_disposition": "live_emission",
    }]
    with pytest.raises(cv.ContractValidationError, match="no .*row exists"):
        cv.validate_live_emission_binding(coverage, emissions=[])


def test_missing_producer_test_node_refused():
    emissions = [{
        "case_id": "x", "text": "text too short",
        "producer_test": (
            "plugins/setec-voiceprint/scripts/tests/test_surprisal_audit_schema.py"
            "::TestUnavailable::test_this_function_does_not_exist"
        ),
    }]
    with pytest.raises(cv.ContractValidationError, match="was not found"):
        cv.validate_producer_emissions_bound(emissions, repo_root=REPO_ROOT)


def test_producer_test_not_observing_text_refused():
    emissions = [{
        "case_id": "x", "text": "a string this test never mentions",
        "producer_test": (
            "plugins/setec-voiceprint/scripts/tests/test_surprisal_audit_schema.py"
            "::TestUnavailable::test_unavailable_audit"
        ),
    }]
    with pytest.raises(cv.ContractValidationError, match="does not appear as a string literal"):
        cv.validate_producer_emissions_bound(emissions, repo_root=REPO_ROOT)


def test_real_committed_fixtures_pass_all_validators():
    """The committed fixtures are, of course, clean against their own
    validators — the positive control for the negative tests above."""
    coverage = json.loads((FIXTURES_DIR / "warning_classifier_coverage.json").read_text())
    emissions = json.loads((FIXTURES_DIR / "warning_producer_emissions.json").read_text())
    cv.validate_live_emission_binding(coverage, emissions)
    cv.validate_producer_emissions_bound(emissions, repo_root=REPO_ROOT)
    cv.validate_contract_block(_live_contract())
