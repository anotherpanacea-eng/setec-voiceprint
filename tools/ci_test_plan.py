#!/usr/bin/env python3
"""Deterministic, collection-conserving pytest lane planner (spec 73)."""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
from typing import Any, Iterable, Sequence


SCHEMA = "setec-ci-test-plan/1"
RESULT_SCHEMA = "setec-ci-test-result/1"
SUMMARY_SCHEMA = "setec-ci-test-result-summary/1"
FIXED_TEST_ROOT = "plugins/setec-voiceprint/scripts/tests"
FIXED_UNIT_SHARDS = 2
LANES = ("unit", "serial_subprocess_cli", "integration_contract")
PLAN_KEYS = frozenset({
    "schema",
    "test_root",
    "unit_shards",
    "serial_subprocess_cli",
    "integration_contract",
    "unit_shard_overrides",
})
BASE_REPORT_KEYS = frozenset({
    "schema", "files", "serial_subprocess_cli", "integration_contract",
    "unit_0", "unit_1",
})
COLLECTION_KEYS = frozenset({
    "canonical", "serial_subprocess_cli", "integration_contract",
    "unit_0", "unit_1",
})
RESULT_KEYS = frozenset({
    "schema", "complete", "exitstatus", "warnings", "expected_count",
    "outcomes",
})
RESULT_OUTCOMES = frozenset({
    "passed", "skipped", "xfailed", "xpassed", "failed", "error",
})

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = Path(__file__).with_name("ci_test_plan.json")


class PlanError(ValueError):
    """Controlled validation failure whose details must not cross the CLI."""


def _byte_key(value: str) -> bytes:
    try:
        return value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise PlanError("string is not UTF-8 encodable") from exc


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("ascii")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanError("duplicate JSON object key")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", "strict")
        return json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except PlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError("invalid JSON input") from exc


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_rel_test_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PlanError("test path must be a nonempty string")
    _byte_key(value)
    if "\\" in value or "\x00" in value:
        raise PlanError("test path uses a forbidden character")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PlanError("test path is not a canonical relative POSIX path")
    root_parts = PurePosixPath(FIXED_TEST_ROOT).parts
    if pure.parts[: len(root_parts)] != root_parts or len(pure.parts) <= len(root_parts):
        raise PlanError("test path lies outside the fixed test root")
    if not pure.name.startswith("test_") or pure.suffix != ".py":
        raise PlanError("test path does not name test_*.py")
    return value


def _is_link_or_reparse(path: Path) -> bool:
    """Recognize links and Windows reparse points without following them."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _assert_no_link_components(
    repo_root: Path,
    relative: str,
    *,
    require_file: bool = True,
) -> Path:
    current = repo_root
    try:
        for component in PurePosixPath(relative).parts:
            current = current / component
            if _is_link_or_reparse(current):
                raise PlanError("linked or reparse path is forbidden")
        if require_file and (not current.is_file() or _is_link_or_reparse(current)):
            raise PlanError("test path is not a regular file")
        if not require_file and (not current.is_dir() or _is_link_or_reparse(current)):
            raise PlanError("test directory is missing or linked")
    except (OSError, UnicodeError) as exc:
        raise PlanError("test path cannot be inspected") from exc
    return current


def discover_tests(repo_root: Path = REPO_ROOT) -> list[str]:
    """Discover regular test files without following directory or file links."""
    try:
        root = _assert_no_link_components(
            repo_root,
            FIXED_TEST_ROOT,
            require_file=False,
        )
    except PlanError:
        raise
    except OSError as exc:
        raise PlanError("fixed test root cannot be inspected") from exc

    discovered: list[str] = []

    def walk(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: _byte_key(item.name))
        except OSError as exc:
            raise PlanError("test tree cannot be inspected") from exc
        for entry in entries:
            try:
                entry_path = Path(entry.path)
                if _is_link_or_reparse(entry_path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    walk(entry_path)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and entry.name.startswith("test_")
                    and entry.name.endswith(".py")
                ):
                    discovered.append(entry_path.relative_to(repo_root).as_posix())
            except OSError as exc:
                raise PlanError("test tree entry cannot be inspected") from exc

    walk(root)
    return sorted(discovered, key=_byte_key)


def _dotted_names(node: ast.AST, bindings: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, {node.id})
    if isinstance(node, ast.Attribute):
        return {
            f"{prefix}.{node.attr}"
            for prefix in _dotted_names(node.value, bindings)
        }
    return set()


def _is_process_target(name: str) -> bool:
    if name in {
        "concurrent.futures.ProcessPoolExecutor",
        "concurrent.futures.process.ProcessPoolExecutor",
    }:
        return True
    if name in {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "asyncio.subprocess.create_subprocess_exec",
        "asyncio.subprocess.create_subprocess_shell",
        "os.system",
        "os.popen",
        "os.fork",
        "os.forkpty",
        "pty.spawn",
    }:
        return True
    return name.startswith("os.spawn") or name.startswith("os.posix_spawn")


def has_process_risk(path: Path) -> bool:
    """Apply spec 73's conservative, exact process-risk AST grammar."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.name)
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise PlanError("test source cannot be parsed") from exc

    bindings: dict[str, set[str]] = {}
    risky = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                binding = alias.asname or root
                bindings.setdefault(binding, set()).add(
                    alias.name if alias.asname else root
                )
                if root in {"subprocess", "multiprocessing"}:
                    risky = True
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root in {"subprocess", "multiprocessing"}:
                risky = True
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{module}.{alias.name}" if module else alias.name
                bindings.setdefault(alias.asname or alias.name, set()).add(full)
                if _is_process_target(full):
                    risky = True

    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            if any(_is_process_target(name) for name in _dotted_names(node, bindings)):
                risky = True
    return risky


def process_risk_files(
    discovered: Sequence[str], repo_root: Path = REPO_ROOT,
) -> list[str]:
    return [
        relative
        for relative in discovered
        if has_process_risk(repo_root / PurePosixPath(relative))
    ]


def _validate_sorted_unique_strings(value: object, *, path_values: bool) -> list[str]:
    if not isinstance(value, list):
        raise PlanError("plan lane must be a list")
    result: list[str] = []
    for item in value:
        result.append(_validate_rel_test_path(item) if path_values else str(item))
    if any(not isinstance(item, str) for item in value):
        raise PlanError("plan lane values must be strings")
    if result != sorted(result, key=_byte_key) or len(result) != len(set(result)):
        raise PlanError("plan lane must be sorted and duplicate-free")
    return result


def _unit_shard(relative: str, overrides: dict[str, int]) -> int:
    if relative in overrides:
        return overrides[relative]
    digest = hashlib.sha256(relative.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) % FIXED_UNIT_SHARDS


def load_and_verify_plan(
    plan_path: Path = PLAN_PATH,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    raw = _read_json(plan_path)
    if not isinstance(raw, dict) or set(raw) != PLAN_KEYS:
        raise PlanError("plan object has unknown or missing fields")
    if raw["schema"] != SCHEMA or raw["test_root"] != FIXED_TEST_ROOT:
        raise PlanError("plan schema or test root is invalid")
    if not _is_int(raw["unit_shards"]) or raw["unit_shards"] != FIXED_UNIT_SHARDS:
        raise PlanError("schema v1 requires exactly two unit shards")

    serial = _validate_sorted_unique_strings(raw["serial_subprocess_cli"], path_values=True)
    integration = _validate_sorted_unique_strings(raw["integration_contract"], path_values=True)
    if not serial or not integration:
        raise PlanError("required lane is empty")
    if set(serial) & set(integration):
        raise PlanError("explicit lanes overlap")

    overrides_raw = raw["unit_shard_overrides"]
    if not isinstance(overrides_raw, dict):
        raise PlanError("unit shard overrides must be an object")
    override_keys = list(overrides_raw)
    if override_keys != sorted(override_keys, key=_byte_key):
        raise PlanError("unit shard overrides must be sorted")
    overrides: dict[str, int] = {}
    for key, value in overrides_raw.items():
        relative = _validate_rel_test_path(key)
        if not _is_int(value) or value not in {0, 1}:
            raise PlanError("unit shard override is invalid")
        overrides[relative] = value

    discovered = discover_tests(repo_root)
    discovered_set = set(discovered)
    for relative in [*serial, *integration, *overrides]:
        _assert_no_link_components(repo_root, relative)
        if relative not in discovered_set:
            raise PlanError("plan references a missing test file")

    explicit = set(serial) | set(integration)
    unit = [relative for relative in discovered if relative not in explicit]
    if set(overrides) - set(unit):
        raise PlanError("stale unit shard override")

    risky = set(process_risk_files(discovered, repo_root))
    if risky - set(serial):
        raise PlanError("process-risk test is not in the serial lane")

    lanes = {
        "serial_subprocess_cli": serial,
        "integration_contract": integration,
        "unit_0": [p for p in unit if _unit_shard(p, overrides) == 0],
        "unit_1": [p for p in unit if _unit_shard(p, overrides) == 1],
    }
    if not lanes["unit_0"] or not lanes["unit_1"]:
        raise PlanError("unit shard is empty")
    if set().union(*(set(values) for values in lanes.values())) != discovered_set:
        raise PlanError("file plan does not conserve discovery")
    if sum(len(values) for values in lanes.values()) != len(discovered):
        raise PlanError("file plan is not pairwise disjoint")
    return raw, lanes


def _base_report(lanes: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "files": sum(len(values) for values in lanes.values()),
        "serial_subprocess_cli": len(lanes["serial_subprocess_cli"]),
        "integration_contract": len(lanes["integration_contract"]),
        "unit_0": len(lanes["unit_0"]),
        "unit_1": len(lanes["unit_1"]),
    }


def _node_digest(nodeids: Iterable[str]) -> dict[str, Any]:
    ordered = sorted(nodeids, key=_byte_key)
    payload = b"".join(_byte_key(nodeid) + b"\0" for nodeid in ordered)
    return {"count": len(ordered), "sha256": hashlib.sha256(payload).hexdigest()}


def _outcome_digest(outcomes: dict[str, str]) -> dict[str, Any]:
    ordered = sorted(outcomes.items(), key=lambda item: _byte_key(item[0]))
    payload = b"".join(
        _byte_key(nodeid) + b"\0" + outcome.encode("ascii") + b"\0"
        for nodeid, outcome in ordered
    )
    return {"count": len(ordered), "sha256": hashlib.sha256(payload).hexdigest()}


def _bind_nodeid(nodeid: str, file_to_lane: dict[str, str]) -> str:
    if not isinstance(nodeid, str) or not nodeid:
        raise PlanError("invalid collected node ID")
    _byte_key(nodeid)
    prefix = nodeid.split("::", 1)[0]
    try:
        return file_to_lane[prefix]
    except KeyError as exc:
        raise PlanError("collected node ID does not bind to the file plan") from exc


def _collect_nodeids(repo_root: Path) -> list[str]:
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)
    try:
        import pytest  # type: ignore
        from tools.ci_pytest_plugin import CollectionCapture  # type: ignore
    except (ImportError, AttributeError) as exc:
        raise PlanError("pytest collection support is unavailable") from exc

    capture = CollectionCapture()
    old_cwd = Path.cwd()
    try:
        os.chdir(repo_root)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            exit_code = pytest.main(
                [FIXED_TEST_ROOT, "--collect-only", "-q", "-p", "no:cacheprovider"],
                plugins=[capture],
            )
    except (OSError, UnicodeError, SystemExit) as exc:
        raise PlanError("pytest collection failed") from exc
    finally:
        os.chdir(old_cwd)
    if int(exit_code) != 0:
        raise PlanError("pytest collection failed")
    nodeids = list(capture.nodeids)
    if len(nodeids) != len(set(nodeids)):
        raise PlanError("pytest collection returned duplicate node IDs")
    return nodeids


def verify_report(
    *,
    collect: bool = False,
    collection_out: Path | None = None,
    plan_path: Path = PLAN_PATH,
    repo_root: Path = REPO_ROOT,
) -> bytes:
    if collection_out is not None and not collect:
        raise PlanError("collection output requires collection")
    _, lanes = load_and_verify_plan(plan_path, repo_root)
    report = _base_report(lanes)
    if collect:
        nodeids = _collect_nodeids(repo_root)
        file_to_lane = {
            relative: lane
            for lane, paths in lanes.items()
            for relative in paths
        }
        by_lane: dict[str, list[str]] = {key: [] for key in lanes}
        for nodeid in nodeids:
            by_lane[_bind_nodeid(nodeid, file_to_lane)].append(nodeid)
        union = [nodeid for values in by_lane.values() for nodeid in values]
        if len(union) != len(set(union)) or set(union) != set(nodeids):
            raise PlanError("collected node IDs are not conserved")
        report["collection"] = {
            "canonical": _node_digest(nodeids),
            **{lane: _node_digest(values) for lane, values in by_lane.items()},
        }
    encoded = _canonical_bytes(report)
    if collection_out is not None:
        _write_create_new(collection_out, encoded)
    return encoded


def _write_create_new(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            written = stream.write(payload)
            if written != len(payload):
                raise PlanError("short output write")
            stream.flush()
            if hasattr(os, "fsync"):
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
    except FileExistsError as exc:
        raise PlanError("output destination already exists") from exc
    except PlanError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PlanError("output destination cannot be written") from exc


def list_files(
    lane: str,
    shard_index: int | None,
    *,
    plan_path: Path = PLAN_PATH,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    if lane not in LANES:
        raise PlanError("unknown lane")
    _, lanes = load_and_verify_plan(plan_path, repo_root)
    if lane == "unit":
        if shard_index not in {0, 1}:
            raise PlanError("unit lane requires an in-range shard")
        return lanes[f"unit_{shard_index}"]
    if shard_index is not None:
        raise PlanError("unsharded lane rejects a shard index")
    return lanes[lane]


def run_lane(
    lane: str,
    shard_index: int | None,
    pytest_args: Sequence[str],
    *,
    plan_path: Path = PLAN_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    files = list_files(
        lane, shard_index, plan_path=plan_path, repo_root=repo_root,
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *files, *pytest_args],
        cwd=repo_root,
        check=False,
    )
    return int(completed.returncode)


def _validate_digest_object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"count", "sha256"}:
        raise PlanError("invalid digest object")
    count = value.get("count")
    digest = value.get("sha256")
    if not _is_int(count) or count < 0:
        raise PlanError("invalid digest count")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise PlanError("invalid SHA-256 digest")
    return {"count": count, "sha256": digest}


def _load_collection_report(
    path: Path, *, plan_path: Path, repo_root: Path,
) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict) or set(value) != BASE_REPORT_KEYS | {"collection"}:
        raise PlanError("invalid collection report")
    _, lanes = load_and_verify_plan(plan_path, repo_root)
    expected_base = _base_report(lanes)
    if any(value.get(key) != expected for key, expected in expected_base.items()):
        raise PlanError("stale collection report")
    collection = value.get("collection")
    if not isinstance(collection, dict) or set(collection) != COLLECTION_KEYS:
        raise PlanError("invalid collection report")
    validated = {
        key: _validate_digest_object(item) for key, item in collection.items()
    }
    lane_count = sum(
        validated[key]["count"] for key in COLLECTION_KEYS if key != "canonical"
    )
    if lane_count != validated["canonical"]["count"]:
        raise PlanError("collection lane counts do not conserve the canonical count")
    return validated


def _load_result_report(path: Path) -> tuple[dict[str, str], int]:
    value = _read_json(path)
    if not isinstance(value, dict) or set(value) != RESULT_KEYS:
        raise PlanError("invalid result report")
    if value.get("schema") != RESULT_SCHEMA or value.get("complete") is not True:
        raise PlanError("incomplete result report")
    if not _is_int(value.get("exitstatus")) or value["exitstatus"] not in {0, 1}:
        raise PlanError("invalid complete result exit status")
    for key in ("warnings", "expected_count"):
        if not _is_int(value.get(key)) or value[key] < 0:
            raise PlanError("invalid result report count")
    outcomes_raw = value.get("outcomes")
    if not isinstance(outcomes_raw, list):
        raise PlanError("invalid result outcomes")
    outcomes: dict[str, str] = {}
    order: list[str] = []
    for item in outcomes_raw:
        if not isinstance(item, dict) or set(item) != {"nodeid", "outcome"}:
            raise PlanError("invalid result outcome")
        nodeid = item.get("nodeid")
        outcome = item.get("outcome")
        if (
            not isinstance(nodeid, str)
            or not nodeid
            or not isinstance(outcome, str)
            or outcome not in RESULT_OUTCOMES
        ):
            raise PlanError("invalid result outcome")
        _byte_key(nodeid)
        if nodeid in outcomes:
            raise PlanError("duplicate result node ID")
        outcomes[nodeid] = outcome
        order.append(nodeid)
    if order != sorted(order, key=_byte_key):
        raise PlanError("result outcomes are not sorted")
    if len(outcomes) != value["expected_count"]:
        raise PlanError("result report is incomplete")
    return outcomes, value["warnings"]


def verify_results(
    collection_report: Path,
    result_paths: Sequence[Path],
    baseline_result: Path | None = None,
    *,
    plan_path: Path = PLAN_PATH,
    repo_root: Path = REPO_ROOT,
) -> bytes:
    if not result_paths:
        raise PlanError("at least one result report is required")
    collection = _load_collection_report(
        collection_report, plan_path=plan_path, repo_root=repo_root,
    )
    combined: dict[str, str] = {}
    warning_counts: list[int] = []
    for path in result_paths:
        outcomes, warnings = _load_result_report(path)
        if set(combined) & set(outcomes):
            raise PlanError("result reports overlap")
        combined.update(outcomes)
        warning_counts.append(warnings)
    union_digest = _node_digest(combined)
    if union_digest != collection["canonical"]:
        raise PlanError("result union does not conserve collection")

    outcome_counts = {name: 0 for name in sorted(RESULT_OUTCOMES)}
    for outcome in combined.values():
        outcome_counts[outcome] += 1
    skipped = {nodeid for nodeid, outcome in combined.items() if outcome == "skipped"}
    outcome_map = _outcome_digest(combined)
    skipped_digest = _node_digest(skipped)

    if baseline_result is not None:
        baseline, _ = _load_result_report(baseline_result)
        if _node_digest(baseline) != collection["canonical"]:
            raise PlanError("baseline result does not match collection")
        baseline_skipped = {
            nodeid for nodeid, outcome in baseline.items() if outcome == "skipped"
        }
        if _node_digest(baseline_skipped) != skipped_digest:
            raise PlanError("candidate skipped nodes differ from baseline")
        if _outcome_digest(baseline) != outcome_map:
            raise PlanError("candidate outcomes differ from baseline")

    summary = {
        "schema": SUMMARY_SCHEMA,
        "collection": collection["canonical"],
        "outcome_map": outcome_map,
        "outcomes": outcome_counts,
        "reports": len(result_paths),
        "skipped": skipped_digest,
        "warnings_by_report": warning_counts,
    }
    return _canonical_bytes(summary)


def _write_stdout(payload: bytes) -> None:
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(payload)
        stream.flush()
    else:  # pragma: no cover - StringIO test harnesses
        sys.stdout.write(payload.decode("ascii"))
        sys.stdout.flush()


def _write_stderr_line() -> None:
    payload = b"ci_test_plan: validation failed\n"
    stream = getattr(sys.stderr, "buffer", None)
    if stream is not None:
        stream.write(payload)
        stream.flush()
    else:  # pragma: no cover - StringIO test harnesses
        sys.stderr.write(payload.decode("ascii"))
        sys.stderr.flush()


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PlanError("invalid command line")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="ci_test_plan.py", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify", allow_abbrev=False)
    verify_parser.add_argument("--collect", action="store_true")
    verify_parser.add_argument("--collection-out", type=Path)

    list_parser = subparsers.add_parser("list", allow_abbrev=False)
    list_parser.add_argument("--lane", choices=LANES, required=True)
    list_parser.add_argument("--shard-index", type=int)
    list_parser.add_argument("--null", action="store_true", dest="null_output")

    run_parser = subparsers.add_parser("run", allow_abbrev=False)
    run_parser.add_argument("--lane", choices=LANES, required=True)
    run_parser.add_argument("--shard-index", type=int)

    result_parser = subparsers.add_parser("verify-results", allow_abbrev=False)
    result_parser.add_argument("--collection-report", type=Path, required=True)
    result_parser.add_argument("--result", type=Path, nargs="+", action="append", required=True)
    result_parser.add_argument("--baseline-result", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    pytest_args: list[str] = []
    if args_list and args_list[0] == "run":
        if "--" not in args_list:
            _write_stderr_line()
            return 2
        separator = args_list.index("--")
        pytest_args = args_list[separator + 1 :]
        args_list = args_list[:separator]
    try:
        args = _parser().parse_args(args_list)
        if args.command == "verify":
            payload = verify_report(
                collect=args.collect,
                collection_out=args.collection_out,
            )
            _write_stdout(payload)
            return 0
        if args.command == "list":
            files = list_files(args.lane, args.shard_index)
            delimiter = b"\0" if args.null_output else b"\n"
            _write_stdout(b"".join(path.encode("utf-8") + delimiter for path in files))
            return 0
        if args.command == "run":
            return run_lane(args.lane, args.shard_index, pytest_args)
        result_paths = [path for group in args.result for path in group]
        _write_stdout(
            verify_results(
                args.collection_report,
                result_paths,
                args.baseline_result,
            )
        )
        return 0
    except PlanError:
        _write_stderr_line()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
