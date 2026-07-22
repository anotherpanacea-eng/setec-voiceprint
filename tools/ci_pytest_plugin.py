"""Pytest collection/result support for the Spec 73 CI topology.

The module has two deliberately separate surfaces:

* :class:`CollectionCapture` and :func:`collect_nodeids` let the planner obtain
  node IDs directly from pytest without parsing human-oriented output.
* Loading the module as ``-p tools.ci_pytest_plugin`` enables the internal
  ``--ci-result-candidate-out`` candidate artifact.  Only the pytest controller
  publishes; xdist workers merely forward their ordinary reports.  An external
  runner owns final merge-gate publication after :func:`pytest.main` returns.

The candidate file is intentionally create-new.  An incomplete session, worker
failure, collection mismatch, or pre-existing destination never produces a
valid replacement candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import warnings
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
    finished: bool = False


_STATE_KEY: pytest.StashKey[_RunState] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("setec-ci-result-candidate")
    group.addoption(
        "--ci-result-candidate-out",
        metavar="PATH",
        default=None,
        help="write one internal canonical candidate for external finalization",
    )


def _activate_state(config: pytest.Config) -> _RunState:
    global _ACTIVE_STATE, _PENDING_WARNINGS
    is_worker = hasattr(config, "workerinput")
    numprocesses = getattr(config.option, "numprocesses", None)
    is_xdist_controller = not is_worker and numprocesses not in (None, 0, "0")
    state = _RunState(
        is_worker=is_worker,
        is_xdist_controller=is_xdist_controller,
        warnings=0 if is_worker else _PENDING_WARNINGS,
    )
    _PENDING_WARNINGS = 0
    config.stash[_STATE_KEY] = state
    # Configuration warnings can be emitted before pytest_sessionstart, and
    # xdist workers can forward their configure-time warnings while the
    # controller is still inside its own sessionstart hooks.  Activate the
    # state before pytest invokes the historic pytest_configure hook.
    _ACTIVE_STATE = state
    return state


def _monitor_configure_warnings(
    config: pytest.Config, state: _RunState,
) -> tuple[Any, Any]:
    """Count warnings across the complete historic configure hook call."""
    original_showwarning = warnings.showwarning
    installed = False

    def showwarning(message, category, filename, lineno, file=None, line=None):
        if state.is_worker:
            # xdist's worker transport is itself a pytest_warning_recorded
            # implementation.  Replay once through the hook instead of also
            # printing locally, so the controller is the sole counter/reporter.
            config.hook.pytest_warning_recorded.call_historic(
                kwargs={
                    "warning_message": warnings.WarningMessage(
                        message, category, filename, lineno, file, line,
                    ),
                    "when": "config",
                    "nodeid": "",
                    "location": None,
                }
            )
        else:
            state.warnings += 1
            original_showwarning(message, category, filename, lineno, file, line)

    def restore() -> None:
        nonlocal installed
        if installed and warnings.showwarning is showwarning:
            warnings.showwarning = original_showwarning
        installed = False

    def before(hook_name, hook_impls, kwargs) -> None:
        del hook_impls, kwargs
        nonlocal installed
        if hook_name == "pytest_configure":
            warnings.showwarning = showwarning
            installed = True

    def after(outcome, hook_name, hook_impls, kwargs) -> None:
        del outcome, hook_impls, kwargs
        if hook_name == "pytest_configure":
            restore()

    undo = config.pluginmanager.add_hookcall_monitoring(before, after)
    return undo, restore


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
    if state is None:
        global _PENDING_WARNINGS
        _PENDING_WARNINGS += 1
    elif not state.is_worker:
        state.warnings += 1


_ACTIVE_STATE: _RunState | None = None
_PENDING_WARNINGS = 0


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
    del exitstatus
    state = _state(session.config)
    state.finished = True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_cmdline_main(config: pytest.Config):
    """Write a candidate after this wrapper's inner lifecycle returns."""
    global _ACTIVE_STATE, _PENDING_WARNINGS
    state = _activate_state(config)
    undo_monitor, restore_showwarning = _monitor_configure_warnings(config, state)
    try:
        result = yield
    except BaseException:
        # No candidate has been created yet.  Preserve pytest's late failure.
        raise
    else:
        output = config.getoption("ci_result_candidate_out")
        if state.is_worker or output is None:
            return result
        if int(result) not in {int(pytest.ExitCode.OK), int(pytest.ExitCode.TESTS_FAILED)}:
            return result
        if not state.finished or state.invalid or state.expected is None:
            return pytest.ExitCode.INTERNAL_ERROR
        payload = _canonical_result(
            exitstatus=int(result),
            warnings=state.warnings,
            expected=state.expected,
            reports=state.reports,
        )
        if payload is None or not _publish_create_new(Path(output), payload):
            return pytest.ExitCode.INTERNAL_ERROR
        return result
    finally:
        restore_showwarning()
        undo_monitor()
        _ACTIVE_STATE = None
        _PENDING_WARNINGS = 0


__all__ = ["CollectionCapture", "RESULT_SCHEMA", "collect_nodeids"]
