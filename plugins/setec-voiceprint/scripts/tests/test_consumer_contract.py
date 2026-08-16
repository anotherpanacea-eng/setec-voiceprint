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
import tempfile
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


def test_contract_block_min_setec_version_is_pinned_to_the_first_0_4_0_release():
    """CONTRACT_BLOCK_MIN_SETEC_VERSION is the plugin version of the FIRST
    release that carried manifest 0.4.0 — a historical fact, not "whatever
    plugin.json currently says". Comparing against a live-read
    `plugin_json["version"]` would be a treadmill: it would silently track
    every future version bump instead of catching one, since the constant
    and the read would move together. Pinned as a literal forever; a future
    release bumping plugin.json's version must NOT move this constant."""
    assert cap.CONTRACT_BLOCK_MIN_SETEC_VERSION == "1.129.0"


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
    with pytest.raises(cv.ContractValidationError, match="exact contract"):
        cv.validate_contract_block(c)


@pytest.mark.parametrize("mutate", [
    lambda c: c.__setitem__("output_schema_version", "9.9"),
    lambda c: c.__setitem__("contract_block_min_setec_version", "1.0.0"),
    lambda c: c.__setitem__("reason_categories", c["reason_categories"][:-1]),
    lambda c: c["output_key_policy"].__setitem__(
        "common_required", c["output_key_policy"]["common_required"][:-1]),
    lambda c: c["output_key_policy"].__setitem__(
        "error_required", ["reason_category"]),
    lambda c: c["client"].__setitem__("relative_path", "scripts/decoy.py"),
    lambda c: c["s5_identity"].__setitem__("method", "weighted mean"),
    lambda c: c["s5_identity"]["family_limits"].__setitem__("punctuation", 1),
])
def test_semantic_contract_weakening_is_refused(mutate):
    contract = _valid_contract()
    mutate(contract)
    with pytest.raises(cv.ContractValidationError, match="exact contract"):
        cv.validate_contract_block(contract)


def test_emit_envelope_semantic_types_are_closed():
    envelope = cap.build_emit_envelope(_manifest())
    bad_schema = copy.deepcopy(envelope)
    bad_schema["manifest_schema_version"] = "0.4"
    with pytest.raises(cv.ContractValidationError, match="manifest_schema_version"):
        cv.validate_manifest_emit_envelope(bad_schema)
    bad_entries = copy.deepcopy(envelope)
    bad_entries["entries"] = {}
    with pytest.raises(cv.ContractValidationError, match="entries must be a list"):
        cv.validate_manifest_emit_envelope(bad_entries)


@pytest.mark.parametrize("version", [
    "01.2.3",
    "1.2.3-alpha..1",
    "1.2.3+build..1",
    "1.2.3-.",
])
def test_emit_envelope_reuses_shared_semver_refusals(version):
    envelope = cap.build_emit_envelope(_manifest())
    envelope["setec_version"] = version
    with pytest.raises(cv.ContractValidationError, match="semver"):
        cv.validate_manifest_emit_envelope(envelope)


def test_bad_file_hash_refused():
    c = _valid_contract()
    c["client"]["sha256"] = "not-a-real-hash"
    with pytest.raises(cv.ContractValidationError, match="sha256"):
        cv.validate_contract_block(c)


def test_file_hash_with_trailing_newline_is_refused():
    c = _valid_contract()
    c["client"]["sha256"] = "0" * 64 + "\n"
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
    with pytest.raises(cv.ContractValidationError, match="cannot claim live_emission"):
        cv.validate_live_emission_binding(coverage, emissions=[])


def test_nonempty_producer_emission_fixture_is_refused_until_direct_capture_exists():
    emissions = [{
        "case_id": "forged", "text": "looks plausible",
        "producer_test": "tests/test_hash.py::test_unrelated_passing_call",
    }]
    with pytest.raises(cv.ContractValidationError, match="must remain empty"):
        cv.validate_producer_emissions_bound(emissions, repo_root=REPO_ROOT)


def test_real_committed_fixtures_pass_all_validators():
    """The committed fixtures are, of course, clean against their own
    validators — the positive control for the negative tests above.
    warning_producer_emissions.json is currently an honest EMPTY array (its
    one prior row was a docstring decoy — see test_docstring_decoy_refused);
    an empty live-emission set is acceptable and honest per the spec."""
    coverage = json.loads((FIXTURES_DIR / "warning_classifier_coverage.json").read_text())
    emissions = json.loads((FIXTURES_DIR / "warning_producer_emissions.json").read_text())
    assert emissions == []
    assert all(row["producer_disposition"] == "classifier_only" for row in coverage)
    cv.validate_live_emission_binding(coverage, emissions)
    cv.validate_producer_emissions_bound(emissions, repo_root=REPO_ROOT)
    cv.validate_contract_block(_live_contract())


def test_build_contract_block_does_not_depend_on_tests_tree():
    """F12 regression: a BARE plugin copy (what actually ships to a
    consumer — no `scripts/tests/`, per the packaging spec's zero-install
    contract) must still be able to run `emit`. Simulated by building that
    bare copy under a temp dir. The live `scripts/tests/` must never be
    touched: an earlier version moved the REAL directory aside and restored
    it in `finally`, which raced other pytest-xdist workers — the whole
    tests tree vanished mid-run for any concurrent test reading committed
    fixtures (and pytest rendered their tracebacks as `???`)."""
    import shutil
    import subprocess
    import sys as _sys

    plugin_dir = ROOT.parent

    def _bare_copy_ignore(src, names):
        ignored = {"__pycache__", ".pytest_cache"}
        if Path(src).resolve() == ROOT:
            ignored.add("tests")
        return ignored & set(names)

    with tempfile.TemporaryDirectory() as td:
        bare_plugin = Path(td) / "plugin"
        shutil.copytree(plugin_dir, bare_plugin, ignore=_bare_copy_ignore)
        bare_scripts = bare_plugin / "scripts"
        assert not (bare_scripts / "tests").exists(), (
            "bare consumer copy must ship without scripts/tests/"
        )
        completed = subprocess.run(
            [_sys.executable, "capabilities.py", "emit", "--json"],
            cwd=bare_scripts, capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stderr
        env = json.loads(completed.stdout)
        assert "contract" in env
        assert env["contract"]["s5_identity"]["method"]
