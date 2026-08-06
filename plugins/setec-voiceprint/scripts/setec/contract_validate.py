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


def _collect_module_import_names(tree: ast.Module) -> set[str]:
    """Top-level names bound by `import X` / `import X as Y` / `from M import
    X [as Y]` — i.e. names that refer to an IMPORTED module or symbol, as
    opposed to a name defined locally in the test file. Used to recognize a
    "call into a production symbol" (an attribute/call on one of these
    names) versus a purely test-local helper call."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _run_pytest_node(node_id: str, *, repo_root: Path) -> None:
    """Actually EXECUTE the bound pytest node and require it passes. A
    statically-plausible binding that is currently red (or collection-
    broken) is not a real producer emission."""
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", node_id, "-q"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise ContractValidationError(
            f"producer_test {node_id!r} does not currently pass "
            f"(pytest exit {completed.returncode}): "
            f"{completed.stdout[-800:]}{completed.stderr[-400:]}"
        )


def validate_producer_emissions_bound(
    emissions: list[dict[str, Any]], *, repo_root: Path, run_pytest: bool = True,
) -> None:
    """For every emissions row, refuse:

      * a missing producer_test FILE or DEF node;
      * a "docstring decoy" — the row's `text` appearing as an ARGUMENT to
        any call inside the test body. That pattern means the test
        constructs its OWN input containing the expected output (e.g.
        ``audit = {"reason": "text too short"}; fn(audit)``) rather than
        observing production code EMIT it — the exact gap a prior review
        caught: a bound row whose "production path" was really the test
        feeding itself the string it later asserted on;
      * no call into an IMPORTED (production) symbol at all — a test that
        never calls anything from outside itself cannot be observing a real
        emission;
      * the text never appearing inside an `assert` in the test body — it
        must be genuinely CHECKED, not merely present;
      * (when `run_pytest`, the default) the bound node not currently
        passing when actually executed.

    A row that survives all of these actually calls a production function
    and asserts on ITS output containing `text` — not on a copy of `text`
    the test typed in itself."""
    for row in emissions:
        validate_warning_producer_emissions_row_shape(row)
        path_str, parts = _parse_node_id(row["producer_test"])
        test_path = repo_root / path_str
        if not test_path.is_file():
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: producer_test file "
                f"{path_str!r} does not exist"
            )
        source = test_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(test_path))
        def_node = _find_def(tree, parts)
        if def_node is None:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: producer_test node "
                f"{row['producer_test']!r} was not found in {path_str}"
            )
        text = row["text"]

        calls = [n for n in ast.walk(def_node) if isinstance(n, ast.Call)]
        if not calls:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: {row['producer_test']!r} "
                f"calls no function at all — cannot observe a real emission"
            )

        # Decoy check: the literal must never appear ANYWHERE in the test
        # body OUTSIDE of an assert statement — not as a direct call
        # argument, and not one level removed via an intermediate variable
        # (`audit = {"reason": "text too short"}; fn(audit)` — the exact
        # shape a prior review caught: the literal never appears in fn()'s
        # own call-argument AST node, only inside the dict literal that
        # feeds it). Collecting every Constant OUTSIDE any Assert, over the
        # whole function body, catches both shapes uniformly.
        assert_nodes = [n for n in ast.walk(def_node) if isinstance(n, ast.Assert)]
        assert_node_ids = {id(n) for n in assert_nodes}

        def _inside_any_assert(target: ast.AST) -> bool:
            # ast.walk has no parent pointers; walk each assert's own
            # subtree instead and check identity membership.
            return any(
                any(sub is target for sub in ast.walk(a)) for a in assert_nodes
            )

        for node in ast.walk(def_node):
            if isinstance(node, ast.Constant) and node.value == text:
                if not _inside_any_assert(node):
                    raise ContractValidationError(
                        f"emissions row {row['case_id']!r}: text {text!r} "
                        f"appears OUTSIDE any assert in "
                        f"{row['producer_test']!r} — the test constructs "
                        f"its own input containing the expected output "
                        f"(a decoy binding, whether fed directly as a call "
                        f"argument or via an intermediate variable), rather "
                        f"than observing production code emit it"
                    )

        # Must call into an imported (production) symbol.
        imported_names = _collect_module_import_names(tree)
        calls_production = False
        for call in calls:
            func = call.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in imported_names:
                    calls_production = True
                    break
            elif isinstance(func, ast.Name) and func.id in imported_names:
                calls_production = True
                break
        if not calls_production:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: {row['producer_test']!r} "
                f"never calls an imported (production) symbol — cannot "
                f"observe a real emission from test-local code alone"
            )

        # The text must actually be asserted on.
        asserts = [n for n in ast.walk(def_node) if isinstance(n, ast.Assert)]
        text_asserted = any(
            isinstance(sub, ast.Constant) and sub.value == text
            for a in asserts for sub in ast.walk(a)
        )
        if not text_asserted:
            raise ContractValidationError(
                f"emissions row {row['case_id']!r}: text {text!r} is never "
                f"asserted on in {row['producer_test']!r}"
            )

        if run_pytest:
            _run_pytest_node(row["producer_test"], repo_root=repo_root)
