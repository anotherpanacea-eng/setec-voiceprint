"""contract_validate.py — closed-shape validators for the C2.1 manifest
`contract` block and the C1.2 warning-firewall fixtures.

Stdlib-only, producer-owned. Used by producer CI (negative-fixture tests) and
importable by a consumer that wants the same refusal semantics rather than
re-typing them. Every check here raises ``ContractValidationError`` with a
specific, single-violation message — no silent pass-through of an unknown key
or a mislabeled emission.
"""

from __future__ import annotations

import ast
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
    if list(okp["common_required"]) != sorted(okp["common_required"]):
        raise ContractValidationError(
            "contract.output_key_policy.common_required is not a sorted projection"
        )
    if list(okp["error_required"]) != sorted(okp["error_required"]):
        raise ContractValidationError(
            "contract.output_key_policy.error_required is not a sorted projection"
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

    reason_categories = contract["reason_categories"]
    if not isinstance(reason_categories, list) or list(reason_categories) != sorted(reason_categories):
        raise ContractValidationError(
            "contract.reason_categories is not a sorted list projection"
        )

    _check_exact_keys(contract["client"], REQUIRED_CLIENT_KEYS, "contract.client")
    _check_sha256(contract["client"]["sha256"], "contract.client.sha256")

    _check_exact_keys(contract["fixtures"], REQUIRED_FIXTURES_KEYS, "contract.fixtures")
    for key in REQUIRED_FIXTURES_KEYS:
        _check_sha256(contract["fixtures"][key], f"contract.fixtures.{key}")

    _check_exact_keys(contract["s5_identity"], REQUIRED_S5_IDENTITY_KEYS, "contract.s5_identity")


def validate_manifest_emit_envelope(envelope: dict[str, Any]) -> None:
    """Validate the top-level `emit` envelope at manifest_schema_version
    0.4.0: exact key set `{setec_version, manifest_schema_version, contract,
    entries}`, and a valid `contract` block."""
    required = frozenset({"setec_version", "manifest_schema_version", "contract", "entries"})
    _check_exact_keys(envelope, required, "emit envelope (schema 0.4.0)")
    validate_contract_block(envelope["contract"])


def validate_warning_classifier_coverage_row_shape(row: Any) -> None:
    required = frozenset({"case_id", "text", "expected_consumer_tier", "producer_disposition"})
    _check_exact_keys(row, required, "warning_classifier_coverage row")
    if row["producer_disposition"] not in VALID_DISPOSITIONS:
        raise ContractValidationError(
            f"coverage row {row.get('case_id')!r}: invalid producer_disposition "
            f"{row['producer_disposition']!r} (must be one of {sorted(VALID_DISPOSITIONS)!r})"
        )


def validate_warning_producer_emissions_row_shape(row: Any) -> None:
    required = frozenset({"case_id", "text", "producer_test"})
    _check_exact_keys(row, required, "warning_producer_emissions row")


def validate_live_emission_binding(
    coverage: list[dict[str, Any]], emissions: list[dict[str, Any]],
) -> None:
    """Every `live_emission`-disposition coverage row must have a matching
    `{case_id, text}` pair in `emissions` — refuses a falsely labeled live
    emission (a row claiming `live_emission` with no bound producer test)."""
    emission_keys = {(row["case_id"], row["text"]) for row in emissions}
    for row in coverage:
        validate_warning_classifier_coverage_row_shape(row)
        if row["producer_disposition"] == "live_emission":
            key = (row["case_id"], row["text"])
            if key not in emission_keys:
                raise ContractValidationError(
                    f"coverage row {row['case_id']!r} is disposition=live_emission "
                    f"but no {{case_id,text}}={key!r} row exists in "
                    f"warning_producer_emissions.json"
                )


def _parse_node_id(producer_test: str) -> tuple[str, list[str]]:
    """Split a pytest node id 'path/to/file.py::Class::method' into
    (path, [qualname parts])."""
    if "::" not in producer_test:
        raise ContractValidationError(
            f"producer_test {producer_test!r} is not a pytest node id "
            f"('path::name[::name...]')"
        )
    path, *parts = producer_test.split("::")
    if not parts:
        raise ContractValidationError(
            f"producer_test {producer_test!r} names a path but no test node"
        )
    return path, parts


def _find_def(tree: ast.Module, parts: list[str]) -> "ast.AST | None":
    """Walk dotted `Class::method` / bare `function` parts to the AST def
    node, or None if any segment is missing."""
    scope: Any = tree
    node = None
    for part in parts:
        node = None
        body = getattr(scope, "body", [])
        for item in body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part:
                node = item
                break
        if node is None:
            return None
        scope = node
    return node


def validate_producer_emissions_bound(
    emissions: list[dict[str, Any]], *, repo_root: Path,
) -> None:
    """For every emissions row: the `producer_test` node's FILE and DEF must
    exist (refuses "a missing producer_test node"), and the row's `text`
    must appear as a literal string constant somewhere in that def's body
    (refuses a producer_test that does not actually observe the text)."""
    for row in emissions:
        validate_warning_producer_emissions_row_shape(row)
        path_str, parts = _parse_node_id(row["producer_test"])
        test_path = repo_root / path_str
        if not test_path.is_file():
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: producer_test file "
                f"{path_str!r} does not exist"
            )
        tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
        def_node = _find_def(tree, parts)
        if def_node is None:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: producer_test node "
                f"{row['producer_test']!r} was not found in {path_str}"
            )
        literals = {
            n.value for n in ast.walk(def_node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        if row["text"] not in literals:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: text {row['text']!r} does not "
                f"appear as a string literal in {row['producer_test']!r} — the "
                f"test does not observably bind this exact string"
            )
