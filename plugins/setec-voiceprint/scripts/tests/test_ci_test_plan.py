#!/usr/bin/env python3
"""Focused contract tests for the deterministic CI test planner (spec 73)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.ci_test_plan as plan  # noqa: E402


def _write_plan(
    root: Path,
    *,
    serial: list[str],
    integration: list[str],
    overrides: dict[str, int] | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    value: dict[str, object] = {
        "schema": plan.SCHEMA,
        "test_root": plan.FIXED_TEST_ROOT,
        "unit_shards": 2,
        "serial_subprocess_cli": sorted(serial, key=str.encode),
        "integration_contract": sorted(integration, key=str.encode),
        "unit_shard_overrides": dict(
            sorted((overrides or {}).items(), key=lambda item: item[0].encode())
        ),
    }
    if extra:
        value.update(extra)
    path = root / "tools" / "ci_test_plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return path


def _make_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "repo"
    tests = root / plan.FIXED_TEST_ROOT
    tests.mkdir(parents=True)
    paths = {
        "serial": f"{plan.FIXED_TEST_ROOT}/test_serial.py",
        "integration": f"{plan.FIXED_TEST_ROOT}/test_integration.py",
        "unit0": f"{plan.FIXED_TEST_ROOT}/test_unit_alpha.py",
        "unit1": f"{plan.FIXED_TEST_ROOT}/test_unit_beta.py",
    }
    (root / paths["serial"]).write_text(
        "import subprocess\ndef test_serial(): assert subprocess.CompletedProcess\n",
        encoding="utf-8",
    )
    for key in ("integration", "unit0", "unit1"):
        (root / paths[key]).write_text("def test_ok(): assert True\n", encoding="utf-8")
    plan_path = _write_plan(
        root,
        serial=[paths["serial"]],
        integration=[paths["integration"]],
        overrides={paths["unit0"]: 0, paths["unit1"]: 1},
    )
    return root, plan_path, paths


def _result_bytes(outcomes: dict[str, str], *, warnings: int = 0, exitstatus: int = 0) -> bytes:
    return plan._canonical_bytes({
        "schema": plan.RESULT_SCHEMA,
        "complete": True,
        "exitstatus": exitstatus,
        "warnings": warnings,
        "expected_count": len(outcomes),
        "outcomes": [
            {"nodeid": nodeid, "outcome": outcomes[nodeid]}
            for nodeid in sorted(outcomes, key=str.encode)
        ],
    })


def _make_junction(link: Path, target: Path) -> None:
    try:
        completed = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        pytest.skip(f"junction creation unavailable: {exc}")
    if completed.returncode != 0:
        pytest.skip(
            "junction creation unavailable: "
            f"rc={completed.returncode} stdout={completed.stdout!r} "
            f"stderr={completed.stderr!r}"
        )


def test_checked_in_plan_is_valid_and_conserves_current_tree() -> None:
    _, lanes = plan.load_and_verify_plan()
    discovered = plan.discover_tests()
    union = [item for values in lanes.values() for item in values]
    assert sorted(union, key=str.encode) == discovered
    assert len(union) == len(set(union))
    assert set(plan.process_risk_files(discovered)) <= set(lanes["serial_subprocess_cli"])
    assert all(lanes[name] for name in lanes)


def test_verify_is_canonical_and_stable(tmp_path: Path) -> None:
    root, plan_path, _ = _make_repo(tmp_path)
    first = plan.verify_report(plan_path=plan_path, repo_root=root)
    second = plan.verify_report(plan_path=plan_path, repo_root=root)
    assert first == second
    assert first.endswith(b"\n") and b"\r" not in first
    decoded = json.loads(first)
    assert decoded == {
        "schema": plan.SCHEMA,
        "files": 4,
        "serial_subprocess_cli": 1,
        "integration_contract": 1,
        "unit_0": 1,
        "unit_1": 1,
    }


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess as sp\n",
        "def f():\n import multiprocessing as mp\n",
        "from subprocess import CompletedProcess as CP\n",
        "from concurrent.futures import ProcessPoolExecutor as PPE\n",
        "import concurrent.futures as cf\nx = cf.ProcessPoolExecutor\n",
        "from concurrent.futures.process import ProcessPoolExecutor as PPE\nx = PPE\n",
        "import concurrent.futures.process as cfp\nx = cfp.ProcessPoolExecutor\n",
        "from asyncio import create_subprocess_exec as launch\nx = launch\n",
        "import asyncio as a\nx = a.create_subprocess_shell\n",
        "from asyncio.subprocess import create_subprocess_exec as launch\nx = launch\n",
        "import asyncio.subprocess as asp\nx = asp.create_subprocess_shell\n",
        "from os import system as invoke\nx = invoke\n",
        "import os as operating\nx = operating.spawnv\n",
        "from os import posix_spawnp as launch\nx = launch\n",
        "import pty as terminal\nx = terminal.spawn\n",
    ],
)
def test_process_risk_ast_positive_alias_and_local_cases(tmp_path: Path, source: str) -> None:
    path = tmp_path / "test_risk.py"
    path.write_text(source, encoding="utf-8")
    assert plan.has_process_risk(path)


@pytest.mark.parametrize(
    "source",
    [
        (
            "def risky():\n"
            " import concurrent.futures as alias\n"
            " return alias.ProcessPoolExecutor()\n"
            "def safe():\n"
            " import json as alias\n"
            " return alias.loads('{}')\n"
        ),
        (
            "def risky():\n"
            " import asyncio as alias\n"
            " return alias.create_subprocess_exec('child')\n"
            "def safe():\n"
            " import json as alias\n"
            " return alias.loads('{}')\n"
        ),
        (
            "def risky():\n"
            " import os as alias\n"
            " return alias.system('child')\n"
            "def safe():\n"
            " import json as alias\n"
            " return alias.loads('{}')\n"
        ),
    ],
)
def test_process_risk_preserves_scope_shadowed_aliases(
    tmp_path: Path,
    source: str,
) -> None:
    path = tmp_path / "test_shadowed_risk.py"
    path.write_text(source, encoding="utf-8")
    assert plan.has_process_risk(path)


def test_process_risk_ignores_comments_and_strings(tmp_path: Path) -> None:
    path = tmp_path / "test_safe.py"
    path.write_text(
        "# import subprocess\nTEXT = 'multiprocessing os.system ProcessPoolExecutor'\n",
        encoding="utf-8",
    )
    assert not plan.has_process_risk(path)


def test_new_ordinary_file_enters_one_stable_unit_shard(tmp_path: Path) -> None:
    root, plan_path, _ = _make_repo(tmp_path)
    new_path = root / plan.FIXED_TEST_ROOT / "test_new_ordinary.py"
    new_path.write_text("def test_new(): assert True\n", encoding="utf-8")
    _, lanes_a = plan.load_and_verify_plan(plan_path, root)
    _, lanes_b = plan.load_and_verify_plan(plan_path, root)
    relative = new_path.relative_to(root).as_posix()
    hits = [lane for lane in ("unit_0", "unit_1") if relative in lanes_a[lane]]
    assert len(hits) == 1
    assert lanes_a == lanes_b


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_field",
        "wrong_shards",
        "overlap",
        "unsorted",
        "stale_override",
        "backslash",
        "absolute",
        "surrogate",
    ],
)
def test_plan_rejects_malformed_and_unsafe_shapes(tmp_path: Path, mutation: str) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    if mutation == "unknown_field":
        value["extra"] = True
    elif mutation == "wrong_shards":
        value["unit_shards"] = 3
    elif mutation == "overlap":
        value["integration_contract"] = [paths["serial"]]
    elif mutation == "unsorted":
        value["serial_subprocess_cli"] = [paths["unit1"], paths["serial"]]
    elif mutation == "stale_override":
        value["unit_shard_overrides"][paths["integration"]] = 0
    elif mutation == "backslash":
        value["serial_subprocess_cli"] = [paths["serial"].replace("/", "\\")]
    elif mutation == "surrogate":
        value["serial_subprocess_cli"] = [
            f"{plan.FIXED_TEST_ROOT}/test_\ud800.py"
        ]
    else:
        value["serial_subprocess_cli"] = ["/tmp/test_escape.py"]
    plan_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(plan.PlanError):
        plan.load_and_verify_plan(plan_path, root)


def test_plan_rejects_linked_test(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    target = root / plan.FIXED_TEST_ROOT / "ordinary.py"
    target.write_text("def test_x(): pass\n", encoding="utf-8")
    serial = root / paths["serial"]
    serial.unlink()
    try:
        serial.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(plan.PlanError):
        plan.load_and_verify_plan(plan_path, root)


def test_reparse_attribute_fallback_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flag = 0x0400
    monkeypatch.setattr(plan.stat, "FILE_ATTRIBUTE_REPARSE_POINT", flag, raising=False)

    class ReparsePath:
        def is_symlink(self) -> bool:
            return False

        def lstat(self) -> SimpleNamespace:
            return SimpleNamespace(st_file_attributes=flag)

    assert plan._is_link_or_reparse(ReparsePath())


def test_plan_rejects_windows_junction_component(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    outside = tmp_path / "outside-tests"
    outside.mkdir()
    (outside / "test_escape.py").write_text(
        "def test_escape(): assert True\n",
        encoding="utf-8",
    )
    link = root / plan.FIXED_TEST_ROOT / "junction"
    _make_junction(link, outside)

    escaped = f"{plan.FIXED_TEST_ROOT}/junction/test_escape.py"
    assert escaped not in plan.discover_tests(root)
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    value["integration_contract"] = sorted(
        [*value["integration_contract"], escaped],
        key=str.encode,
    )
    plan_path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(plan.PlanError, match="linked or reparse"):
        plan.load_and_verify_plan(plan_path, root)


def test_discovery_rejects_windows_junction_test_root(tmp_path: Path) -> None:
    root, _, _ = _make_repo(tmp_path)
    tests = root / plan.FIXED_TEST_ROOT
    target = tmp_path / "real-tests"
    tests.rename(target)
    _make_junction(tests, target)
    with pytest.raises(plan.PlanError, match="linked or reparse"):
        plan.discover_tests(root)


def test_collect_report_conserves_opaque_nodeids_and_writes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    nodeids = [
        f"{paths['serial']}::test_serial",
        f"{paths['integration']}::test_ok[param::\N{SNOWMAN}]",
        f"{paths['unit0']}::test_ok",
        f"{paths['unit1']}::test_ok",
    ]
    monkeypatch.setattr(plan, "_collect_nodeids", lambda _root: list(nodeids))
    destination = tmp_path / "collection.json"
    payload = plan.verify_report(
        collect=True,
        collection_out=destination,
        plan_path=plan_path,
        repo_root=root,
    )
    assert destination.read_bytes() == payload
    report = json.loads(payload)
    assert report["collection"]["canonical"]["count"] == 4
    assert report["collection"]["serial_subprocess_cli"]["count"] == 1
    with pytest.raises(plan.PlanError):
        plan.verify_report(
            collect=True,
            collection_out=destination,
            plan_path=plan_path,
            repo_root=root,
        )


def test_collect_rejects_duplicate_and_unbound_nodeids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    duplicate = f"{paths['serial']}::test_serial"
    monkeypatch.setattr(plan, "_collect_nodeids", lambda _root: [duplicate, duplicate])
    with pytest.raises(plan.PlanError):
        plan.verify_report(collect=True, plan_path=plan_path, repo_root=root)
    monkeypatch.setattr(plan, "_collect_nodeids", lambda _root: ["elsewhere/test_x.py::test_x"])
    with pytest.raises(plan.PlanError):
        plan.verify_report(collect=True, plan_path=plan_path, repo_root=root)
    monkeypatch.setattr(
        plan,
        "_collect_nodeids",
        lambda _root: [f"{paths['serial']}::test_\ud800"],
    )
    with pytest.raises(plan.PlanError):
        plan.verify_report(collect=True, plan_path=plan_path, repo_root=root)


def test_output_path_rejects_lone_surrogate_as_plan_error() -> None:
    with pytest.raises(plan.PlanError):
        plan._write_create_new(Path("collection-\ud800.json"), b"{}\n")


def test_list_lane_validates_shard_combinations(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    assert plan.list_files("unit", 0, plan_path=plan_path, repo_root=root) == [paths["unit0"]]
    assert plan.list_files(
        "serial_subprocess_cli", None, plan_path=plan_path, repo_root=root,
    ) == [paths["serial"]]
    with pytest.raises(plan.PlanError):
        plan.list_files("unit", None, plan_path=plan_path, repo_root=root)
    with pytest.raises(plan.PlanError):
        plan.list_files("integration_contract", 0, plan_path=plan_path, repo_root=root)


def test_run_uses_current_python_without_shell_and_preserves_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(plan.subprocess, "run", fake_run)
    rc = plan.run_lane(
        "unit", 0, ["-q", "--maxfail=1"], plan_path=plan_path, repo_root=root,
    )
    assert rc == 7
    assert observed["argv"] == [
        sys.executable, "-m", "pytest", paths["unit0"], "-q", "--maxfail=1",
    ]
    assert observed["kwargs"] == {"cwd": root, "check": False}


def test_verify_results_conserves_union_and_matches_baseline(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    nodeids = {
        key: f"{relative}::test_ok" for key, relative in paths.items()
    }
    monkey_collection = list(nodeids.values())
    original_collect = plan._collect_nodeids
    plan._collect_nodeids = lambda _root: list(monkey_collection)
    try:
        collection_path = tmp_path / "collection.json"
        plan.verify_report(
            collect=True,
            collection_out=collection_path,
            plan_path=plan_path,
            repo_root=root,
        )
    finally:
        plan._collect_nodeids = original_collect

    first_outcomes = {
        nodeids["serial"]: "passed",
        nodeids["integration"]: "skipped",
    }
    second_outcomes = {
        nodeids["unit0"]: "xfailed",
        nodeids["unit1"]: "passed",
    }
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    baseline = tmp_path / "baseline.json"
    first.write_bytes(_result_bytes(first_outcomes, warnings=2))
    second.write_bytes(_result_bytes(second_outcomes, warnings=0))
    baseline.write_bytes(_result_bytes({**first_outcomes, **second_outcomes}, warnings=1))

    summary = json.loads(plan.verify_results(
        collection_path,
        [first, second],
        baseline,
        plan_path=plan_path,
        repo_root=root,
    ))
    assert summary["collection"]["count"] == 4
    assert summary["outcomes"]["passed"] == 2
    assert summary["outcomes"]["skipped"] == 1
    assert summary["outcomes"]["xfailed"] == 1
    assert summary["warnings_by_report"] == [2, 0]


def test_verify_results_rejects_overlap_partial_unknown_and_drift(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    nodes = [f"{relative}::test_ok" for relative in paths.values()]
    original_collect = plan._collect_nodeids
    plan._collect_nodeids = lambda _root: list(nodes)
    try:
        collection_path = tmp_path / "collection.json"
        plan.verify_report(
            collect=True,
            collection_out=collection_path,
            plan_path=plan_path,
            repo_root=root,
        )
    finally:
        plan._collect_nodeids = original_collect

    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    left.write_bytes(_result_bytes({nodes[0]: "passed", nodes[1]: "passed"}))
    right.write_bytes(_result_bytes({nodes[1]: "passed", nodes[2]: "passed", nodes[3]: "passed"}))
    with pytest.raises(plan.PlanError):
        plan.verify_results(collection_path, [left, right], plan_path=plan_path, repo_root=root)

    bad = json.loads(right.read_text(encoding="utf-8"))
    bad["outcomes"][0]["outcome"] = "unknown"
    right.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(plan.PlanError):
        plan.verify_results(collection_path, [right], plan_path=plan_path, repo_root=root)


def test_result_and_collection_reports_reject_nonclosed_mutations(tmp_path: Path) -> None:
    root, plan_path, paths = _make_repo(tmp_path)
    nodes = [f"{relative}::test_ok" for relative in paths.values()]
    original_collect = plan._collect_nodeids
    plan._collect_nodeids = lambda _root: list(nodes)
    try:
        collection_path = tmp_path / "collection.json"
        plan.verify_report(
            collect=True,
            collection_out=collection_path,
            plan_path=plan_path,
            repo_root=root,
        )
    finally:
        plan._collect_nodeids = original_collect

    invalid_exit = tmp_path / "invalid-exit.json"
    invalid_exit.write_bytes(_result_bytes({nodes[0]: "passed"}, exitstatus=2))
    with pytest.raises(plan.PlanError):
        plan._load_result_report(invalid_exit)

    invalid_node = tmp_path / "invalid-node.json"
    invalid_node.write_text(
        json.dumps(
            {
                "schema": plan.RESULT_SCHEMA,
                "complete": True,
                "exitstatus": 0,
                "warnings": 0,
                "expected_count": 1,
                "outcomes": [{"nodeid": "test_\ud800.py::test_x", "outcome": "passed"}],
            },
            ensure_ascii=True,
        ),
        encoding="ascii",
    )
    with pytest.raises(plan.PlanError):
        plan._load_result_report(invalid_node)

    invalid_outcome = tmp_path / "invalid-outcome.json"
    outcome_document = json.loads(_result_bytes({nodes[0]: "passed"}))
    outcome_document["outcomes"][0]["outcome"] = ["passed"]
    invalid_outcome.write_bytes(plan._canonical_bytes(outcome_document))
    with pytest.raises(plan.PlanError):
        plan._load_result_report(invalid_outcome)

    invalid_collection = tmp_path / "invalid-collection.json"
    collection_document = json.loads(collection_path.read_bytes())
    collection_document["collection"]["unit_0"]["count"] += 1
    invalid_collection.write_bytes(plan._canonical_bytes(collection_document))
    with pytest.raises(plan.PlanError):
        plan._load_collection_report(
            invalid_collection,
            plan_path=plan_path,
            repo_root=root,
        )


def test_real_cli_verify_and_list_are_byte_stable() -> None:
    script = TOOLS / "ci_test_plan.py"
    verified = subprocess.run(
        [sys.executable, str(script), "verify"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0
    assert verified.stderr == b""
    assert verified.stdout.endswith(b"\n") and b"\r" not in verified.stdout
    listed = subprocess.run(
        [sys.executable, str(script), "list", "--lane", "unit", "--shard-index", "0", "--null"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0
    assert listed.stderr == b""
    assert listed.stdout.endswith(b"\0")


def test_checked_in_unit_shard_override_contract() -> None:
    # These are the smallest two reviewed whole-file moves that bring the
    # checked-in topology inside Spec 73's measured 1.25 node-count ratio.
    assert set(plan._read_json(plan.PLAN_PATH)["unit_shard_overrides"]) == {
        f"{plan.FIXED_TEST_ROOT}/test_acquire_blog.py",
        f"{plan.FIXED_TEST_ROOT}/test_acquire_gmail_sent.py",
    }


def test_real_cli_collects_minimal_repo_without_import_path_failure(
    tmp_path: Path,
) -> None:
    root, _, _ = _make_repo(tmp_path)
    for name in ("ci_test_plan.py", "ci_pytest_plugin.py"):
        (root / "tools" / name).write_bytes((TOOLS / name).read_bytes())
    script = root / "tools" / "ci_test_plan.py"
    artifact = tmp_path / "collection.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify",
            "--collect",
            "--collection-out",
            str(artifact),
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == artifact.read_bytes()
    report = json.loads(completed.stdout)
    assert report["collection"]["canonical"]["count"] == 4
    assert {
        lane: report["collection"][lane]["count"]
        for lane in (
            "serial_subprocess_cli",
            "integration_contract",
            "unit_0",
            "unit_1",
        )
    } == {
        "serial_subprocess_cli": 1,
        "integration_contract": 1,
        "unit_0": 1,
        "unit_1": 1,
    }
    assert completed.stderr == b""

    invalid_result = tmp_path / "surrogate-result.json"
    invalid_result.write_text(
        json.dumps(
            {
                "schema": plan.RESULT_SCHEMA,
                "complete": True,
                "exitstatus": 0,
                "warnings": 0,
                "expected_count": 1,
                "outcomes": [{"nodeid": "test_\ud800.py::test_x", "outcome": "passed"}],
            },
            ensure_ascii=True,
        ),
        encoding="ascii",
    )
    rejected = subprocess.run(
        [
            sys.executable,
            str(script),
            "verify-results",
            "--collection-report",
            str(artifact),
            "--result",
            str(invalid_result),
        ],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == b""
    assert rejected.stderr == b"ci_test_plan: validation failed\n"
