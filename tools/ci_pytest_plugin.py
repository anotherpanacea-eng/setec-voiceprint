"""Pytest collection/result support for the Spec 73 CI topology.

The module has two deliberately separate surfaces:

* :class:`CollectionCapture` and :func:`collect_nodeids` let the planner obtain
  node IDs directly from pytest without parsing human-oriented output.
* Loading the module as ``-p tools.ci_pytest_plugin`` enables the
  ``--ci-result-out`` result artifact.  Only the pytest controller publishes;
  xdist workers merely forward their ordinary reports.

The result file is intentionally create-new.  An incomplete session, worker
failure, collection mismatch, or pre-existing destination never produces a
valid replacement artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Sequence

import pytest


RESULT_SCHEMA = "setec-ci-test-result/1"
_CLOSED_OUTCOMES = frozenset(
    {"passed", "skipped", "xfailed", "xpassed", "failed", "error"}
)


class CollectionCapture:
    """In-memory capture passed to ``pytest.main(..., plugins=[capture])``."""

    def __init__(self) -> None:
        self.nodeids: list[str] = []

    @pytest.hookimpl(trylast=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.nodeids = [str(item.nodeid) for item in session.items]


def collect_nodeids(pytest_args: Sequence[str]) -> tuple[int, list[str]]:
    """Collect node IDs in memory and discard partial results on failure."""

    capture = CollectionCapture()
    args = [str(arg) for arg in pytest_args]
    if "--collect-only" not in args and "--co" not in args:
        args.append("--collect-only")
    status = int(pytest.main(args, plugins=[capture]))
    return status, list(capture.nodeids) if status == 0 else []


@dataclass
class _RunState:
    is_worker: bool
    is_xdist_controller: bool
    expected: set[str] | None = None
    worker_collections: dict[str, set[str]] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: int = 0
    invalid: bool = False


_STATE_KEY: pytest.StashKey[_RunState] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("setec-ci-result")
    group.addoption(
        "--ci-result-out",
        metavar="PATH",
        default=None,
        help="publish one canonical create-new SETEC CI result report",
    )


def pytest_configure(config: pytest.Config) -> None:
    is_worker = hasattr(config, "workerinput")
    numprocesses = getattr(config.option, "numprocesses", None)
    is_xdist_controller = not is_worker and numprocesses not in (None, 0, "0")
    config.stash[_STATE_KEY] = _RunState(
        is_worker=is_worker,
        is_xdist_controller=is_xdist_controller,
    )


def _state(config: pytest.Config) -> _RunState:
    return config.stash[_STATE_KEY]


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    state = _state(session.config)
    if state.is_worker or state.is_xdist_controller:
        return
    state.expected = {str(item.nodeid) for item in session.items}


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: Any, ids: Sequence[str]) -> None:
    state = _state(node.config)
    if state.is_worker:
        return
    worker_id = str(getattr(getattr(node, "gateway", None), "id", id(node)))
    collection = {str(nodeid) for nodeid in ids}
    if len(collection) != len(ids):
        state.invalid = True
    state.worker_collections[worker_id] = collection
    if state.expected is None:
        state.expected = collection
    elif state.expected != collection:
        state.invalid = True


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object | None) -> None:
    state = _state(node.config)
    if not state.is_worker and error is not None:
        state.invalid = True


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Local reports do not carry config; pytest exposes the active module state
    # through the hook caller only indirectly.  Store the active controller
    # config at configure time, scoped to the current process.
    state = _ACTIVE_STATE
    if state is None or state.is_worker:
        return
    nodeid = str(report.nodeid)
    phase = str(report.when)
    if phase not in {"setup", "call", "teardown"}:
        state.invalid = True
        return
    phases = state.reports.setdefault(nodeid, {})
    if phase in phases:
        state.invalid = True
        return
    phases[phase] = report


def pytest_warning_recorded(
    warning_message: Warning,
    when: str,
    nodeid: str,
    location: tuple[str, int, str] | None,
) -> None:
    del warning_message, when, nodeid, location
    state = _ACTIVE_STATE
    if state is not None and not state.is_worker:
        state.warnings += 1


_ACTIVE_STATE: _RunState | None = None


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    global _ACTIVE_STATE
    _ACTIVE_STATE = _state(session.config)


def _has_wasxfail(report: Any) -> bool:
    return getattr(report, "wasxfail", None) is not None


def _is_strict_xpass(report: Any) -> bool:
    # Pytest deliberately does not attach ``wasxfail`` to a strict XPASS.  Its
    # stable TestReport representation is a failed call whose longrepr begins
    # with this marker (the same marker used by pytest's terminal reporter).
    return (
        getattr(report, "when", None) == "call"
        and getattr(report, "outcome", None) == "failed"
        and str(getattr(report, "longrepr", "")).startswith("[XPASS(strict)]")
    )


def _reduce_outcome(phases: dict[str, Any]) -> str | None:
    setup = phases.get("setup")
    call = phases.get("call")
    teardown = phases.get("teardown")

    if setup is not None and setup.outcome == "failed":
        return "error"
    if teardown is not None and teardown.outcome == "failed":
        return "error"

    if call is not None and _is_strict_xpass(call):
        return "xpassed"

    for report in (setup, call, teardown):
        if report is None or not _has_wasxfail(report):
            continue
        if report.outcome == "skipped":
            return "xfailed"
        if report.outcome in {"passed", "failed"}:
            return "xpassed"
        return None

    if setup is not None and setup.outcome == "skipped":
        return "skipped" if teardown is not None and teardown.outcome == "passed" else None
    if call is not None and call.outcome == "skipped":
        return "skipped" if teardown is not None and teardown.outcome == "passed" else None
    if call is not None and call.outcome == "failed":
        return "failed" if teardown is not None and teardown.outcome == "passed" else None
    if (
        setup is not None
        and setup.outcome == "passed"
        and call is not None
        and call.outcome == "passed"
        and teardown is not None
        and teardown.outcome == "passed"
    ):
        return "passed"
    return None


def _canonical_result(
    *, exitstatus: int, warnings: int, expected: set[str], reports: dict[str, dict[str, Any]]
) -> bytes | None:
    if set(reports) != expected:
        return None
    outcomes: list[dict[str, str]] = []
    for nodeid in sorted(expected, key=lambda value: value.encode("utf-8")):
        outcome = _reduce_outcome(reports[nodeid])
        if outcome not in _CLOSED_OUTCOMES:
            return None
        outcomes.append({"nodeid": nodeid, "outcome": outcome})
    payload = {
        "schema": RESULT_SCHEMA,
        "complete": True,
        "exitstatus": int(exitstatus),
        "warnings": int(warnings),
        "expected_count": len(expected),
        "outcomes": outcomes,
    }
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _set_internal_error(session: pytest.Session) -> None:
    session.exitstatus = pytest.ExitCode.INTERNAL_ERROR


def _publish_create_new(path: Path, payload: bytes) -> bool:
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            written = stream.write(payload)
            if written != len(payload):
                raise OSError("short result write")
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except (FileExistsError, OSError):
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        return False


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _ACTIVE_STATE
    state = _state(session.config)
    try:
        output = session.config.getoption("ci_result_out")
        if state.is_worker or output is None:
            return
        if int(exitstatus) not in {int(pytest.ExitCode.OK), int(pytest.ExitCode.TESTS_FAILED)}:
            return
        if state.invalid or state.expected is None:
            _set_internal_error(session)
            return
        payload = _canonical_result(
            exitstatus=int(exitstatus),
            warnings=state.warnings,
            expected=state.expected,
            reports=state.reports,
        )
        if payload is None or not _publish_create_new(Path(output), payload):
            _set_internal_error(session)
    finally:
        if not state.is_worker:
            _ACTIVE_STATE = None


__all__ = ["CollectionCapture", "RESULT_SCHEMA", "collect_nodeids"]
