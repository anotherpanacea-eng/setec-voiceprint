"""contract_validate.py — closed-shape validators for the C2.1 manifest
`contract` block and the C1.2 warning-firewall fixtures.

Stdlib-only, producer-owned. Used by producer CI (negative-fixture tests) and
importable by a consumer that wants the same refusal semantics rather than
re-typing them. Every check here raises ``ContractValidationError`` with a
specific, single-violation message — no silent pass-through of an unknown key
or a mislabeled emission.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

REQUIRED_CONTRACT_KEYS = frozenset({
    "output_schema_version", "output_key_policy", "reason_categories",
    "contract_block_min_setec_version", "s5_identity", "client", "fixtures",
})
REQUIRED_OUTPUT_KEY_POLICY_KEYS = frozenset({
    "common_required", "success_extensions", "error_required",
    "error_extensions", "reserved_collision_refused",
})
REQUIRED_CLIENT_KEYS = frozenset({"relative_path", "sha256"})
REQUIRED_FIXTURES_KEYS = frozenset({
    "semver_parser_sha256", "warning_classifier_coverage_sha256",
    "warning_producer_emissions_sha256",
})
REQUIRED_S5_IDENTITY_KEYS = frozenset({"method", "family_order", "family_limits"})

VALID_DISPOSITIONS = frozenset({"live_emission", "classifier_only"})

EXPECTED_OUTPUT_SCHEMA_VERSION = "1.0"
EXPECTED_MANIFEST_SCHEMA_VERSION = "0.4.0"
EXPECTED_CONTRACT_FLOOR = "1.129.0"
EXPECTED_COMMON_REQUIRED = [
    "ai_status", "available", "baseline", "claim_license",
    "claim_license_rendered", "results", "schema_version", "target",
    "task_surface", "tool", "version", "warnings",
]
EXPECTED_ERROR_REQUIRED = ["reason", "reason_category"]
EXPECTED_REASON_CATEGORIES = [
    "bad_input", "internal_error", "missing_dependency", "policy_refused",
    "text_too_short", "version_floor",
]
EXPECTED_CLIENT_RELATIVE_PATH = "scripts/setec/consumer_client.py"
EXPECTED_S5_IDENTITY = {
    "method": "unweighted mean of six family Burrows-Delta values",
    "family_order": [
        "char_ngrams_3", "char_ngrams_4", "char_ngrams_5", "pos_trigrams",
        "dependency_ngrams", "punctuation",
    ],
    "family_limits": {
        "char_ngrams_3": 200,
        "char_ngrams_4": 200,
        "char_ngrams_5": 200,
        "pos_trigrams": 300,
        "dependency_ngrams": 300,
        "punctuation": None,
    },
}


class ContractValidationError(ValueError):
    """Raised by every validator in this module on a closed-shape violation."""


def _check_exact_keys(d: Any, required: frozenset, label: str) -> None:
    if not isinstance(d, dict):
        raise ContractValidationError(f"{label}: expected a mapping, got {type(d).__name__}")
    keys = set(d.keys())
    unknown = keys - required
    missing = required - keys
    if unknown:
        raise ContractValidationError(f"{label}: unknown key(s) {sorted(unknown)!r}")
    if missing:
        raise ContractValidationError(f"{label}: missing required key(s) {sorted(missing)!r}")


def _check_sha256(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise ContractValidationError(f"{label}: not a 64-lower-hex sha256 string: {value!r}")


def validate_contract_block(contract: Any) -> None:
    """Raise ContractValidationError on: a missing block (None), an unknown
    top-level or nested key, a missing required key, an unsorted
    common-key/error-key/reason_categories projection, a malformed sha256
    hash, or a mistyped literal (`success_extensions`,
    `reserved_collision_refused`)."""
    if contract is None:
        raise ContractValidationError("contract block is missing")
    _check_exact_keys(contract, REQUIRED_CONTRACT_KEYS, "contract")

    okp = contract["output_key_policy"]
    _check_exact_keys(okp, REQUIRED_OUTPUT_KEY_POLICY_KEYS, "contract.output_key_policy")
    if okp["common_required"] != EXPECTED_COMMON_REQUIRED:
        raise ContractValidationError(
            "contract.output_key_policy.common_required differs from the exact contract"
        )
    if okp["error_required"] != EXPECTED_ERROR_REQUIRED:
        raise ContractValidationError(
            "contract.output_key_policy.error_required differs from the exact contract"
        )
    if okp["success_extensions"] != "surface_specific_allowed":
        raise ContractValidationError(
            "contract.output_key_policy.success_extensions must be "
            "'surface_specific_allowed'"
        )
    if okp["error_extensions"] != "surface_specific_allowed":
        raise ContractValidationError(
            "contract.output_key_policy.error_extensions must be "
            "'surface_specific_allowed'"
        )
    if okp["reserved_collision_refused"] is not True:
        raise ContractValidationError(
            "contract.output_key_policy.reserved_collision_refused must be true"
        )

    if contract["output_schema_version"] != EXPECTED_OUTPUT_SCHEMA_VERSION:
        raise ContractValidationError(
            "contract.output_schema_version differs from the exact contract"
        )
    if contract["contract_block_min_setec_version"] != EXPECTED_CONTRACT_FLOOR:
        raise ContractValidationError(
            "contract.contract_block_min_setec_version differs from the exact contract"
        )
    if contract["reason_categories"] != EXPECTED_REASON_CATEGORIES:
        raise ContractValidationError(
            "contract.reason_categories differs from the exact contract"
        )

    _check_exact_keys(contract["client"], REQUIRED_CLIENT_KEYS, "contract.client")
    if contract["client"]["relative_path"] != EXPECTED_CLIENT_RELATIVE_PATH:
        raise ContractValidationError(
            "contract.client.relative_path differs from the exact contract"
        )
    _check_sha256(contract["client"]["sha256"], "contract.client.sha256")

    _check_exact_keys(contract["fixtures"], REQUIRED_FIXTURES_KEYS, "contract.fixtures")
    for key in REQUIRED_FIXTURES_KEYS:
        _check_sha256(contract["fixtures"][key], f"contract.fixtures.{key}")

    _check_exact_keys(contract["s5_identity"], REQUIRED_S5_IDENTITY_KEYS, "contract.s5_identity")
    if contract["s5_identity"] != EXPECTED_S5_IDENTITY:
        raise ContractValidationError(
            "contract.s5_identity differs from the exact contract"
        )


def validate_manifest_emit_envelope(envelope: dict[str, Any]) -> None:
    """Validate the top-level `emit` envelope at manifest_schema_version
    0.4.0: exact key set `{setec_version, manifest_schema_version, contract,
    entries}`, and a valid `contract` block."""
    required = frozenset({"setec_version", "manifest_schema_version", "contract", "entries"})
    _check_exact_keys(envelope, required, "emit envelope (schema 0.4.0)")
    if envelope["manifest_schema_version"] != EXPECTED_MANIFEST_SCHEMA_VERSION:
        raise ContractValidationError("emit envelope has the wrong manifest_schema_version")
    if not isinstance(envelope["setec_version"], str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
        envelope["setec_version"],
    ):
        raise ContractValidationError("emit envelope setec_version is not a semver string")
    if not isinstance(envelope["entries"], list):
        raise ContractValidationError("emit envelope entries must be a list")
    validate_contract_block(envelope["contract"])


def validate_warning_classifier_coverage_row_shape(row: Any) -> None:
    required = frozenset({
        "case_id", "text", "expected_consumer_tier", "expected_classification",
        "producer_disposition",
    })
    _check_exact_keys(row, required, "warning_classifier_coverage row")
    if row["producer_disposition"] not in VALID_DISPOSITIONS:
        raise ContractValidationError(
            f"coverage row {row.get('case_id')!r}: invalid producer_disposition "
            f"{row['producer_disposition']!r} (must be one of {sorted(VALID_DISPOSITIONS)!r})"
        )


def validate_live_emission_binding(
    coverage: list[dict[str, Any]], emissions: list[dict[str, Any]],
) -> None:
    """The 1.129.0 producer fixture is closed-empty and coverage says so."""
    if emissions:
        raise ContractValidationError(
            "warning_producer_emissions must remain empty for contract 1.129.0; "
            "a later live-emission fixture needs a direct behavioral validator"
        )
    for row in coverage:
        validate_warning_classifier_coverage_row_shape(row)
        if row["producer_disposition"] == "live_emission":
            raise ContractValidationError(
                f"coverage row {row['case_id']!r} cannot claim live_emission "
                "while the 1.129.0 producer fixture is closed-empty"
            )


def validate_producer_emissions_bound(
    emissions: list[dict[str, Any]], *, repo_root: Path, run_pytest: bool = True,
) -> None:
    """P1.129.0 has no live producer-warning rows; enforce that fact."""
    del repo_root, run_pytest
    if emissions:
        raise ContractValidationError(
            "warning_producer_emissions must remain empty for contract 1.129.0"
        )
