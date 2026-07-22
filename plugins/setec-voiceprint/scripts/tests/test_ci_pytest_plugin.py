"""Focused contract tests for the Spec 73 pytest result plugin."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
PLANNER = REPO_ROOT / "tools" / "ci_test_plan.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import ci_pytest_plugin as plugin  # noqa: E402


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(REPO_ROOT)
        if not current
        else os.pathsep.join((str(REPO_ROOT), current))
    )
    return env


def _run(
    root: Path,
    *pytest_args: str,
    result: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        sys.executable,
        str(PLANNER),
        "run-pytest",
    ]
    if result is not None:
        command.extend(("--ci-result-out", str(result)))
    command.extend(("--", "-p", "no:cacheprovider", *pytest_args))
    return subprocess.run(
        command,
        cwd=root,
        env=_environment(),
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _write(root: Path, name: str, source: str) -> Path:
    path = root / name
    path.write_text(source, encoding="utf-8", newline="\n")
    return path


def _load(path: Path) -> tuple[bytes, dict[str, object]]:
    raw = path.read_bytes()
    return raw, json.loads(raw)


def _outcomes(document: dict[str, object]) -> dict[str, str]:
    values = document["outcomes"]
    assert isinstance(values, list)
    return {str(item["nodeid"]): str(item["outcome"]) for item in values}


def test_collection_capture_and_helper_return_nodeids_without_stdout_parsing(
    tmp_path: Path,
) -> None:
    test_file = _write(
        tmp_path,
        "test_collect.py",
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['plain', 'café'])\n"
        "def test_value(value):\n"
        "    assert value\n",
    )

    capture = plugin.CollectionCapture()
    status = int(
        pytest.main(
            [str(test_file), "--collect-only", "-q", "-p", "no:cacheprovider"],
            plugins=[capture],
        )
    )
    assert status == 0
    assert len(capture.nodeids) == 2
    assert any("caf\\xe9" in nodeid for nodeid in capture.nodeids)

    helper_status, helper_ids = plugin.collect_nodeids(
        [str(test_file), "-q", "-p", "no:cacheprovider"]
    )
    assert helper_status == 0
    assert helper_ids == capture.nodeids


def test_canonical_report_records_closed_outcomes_and_ascii_lf(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_outcomes.py",
        "import pytest\n"
        "def test_pass(): pass\n"
        "def test_skip(): pytest.skip('skip')\n"
        "@pytest.mark.xfail(reason='expected')\n"
        "def test_xfail(): assert False\n"
        "@pytest.mark.xfail(reason='unexpected')\n"
        "def test_xpass(): pass\n"
        "def test_unicode_é(): pass\n",
    )
    result = tmp_path / "result # é.json"
    completed = _run(tmp_path, "test_outcomes.py", "-q", result=result)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")

    raw, document = _load(result)
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    raw.decode("ascii")
    assert raw == (
        json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    assert document["schema"] == plugin.RESULT_SCHEMA
    assert document["complete"] is True
    assert document["exitstatus"] == 0
    assert document["expected_count"] == 5
    assert set(_outcomes(document).values()) == {
        "passed",
        "skipped",
        "xfailed",
        "xpassed",
    }


@pytest.mark.parametrize(
    ("source", "expected", "returncode"),
    [
        ("def test_failure(): assert False\n", "failed", 1),
        (
            "import pytest\n"
            "@pytest.fixture\n"
            "def broken(): raise RuntimeError('setup')\n"
            "def test_setup_error(broken): pass\n",
            "error",
            1,
        ),
        (
            "import pytest\n"
            "@pytest.fixture\n"
            "def unavailable(): pytest.skip('setup skip')\n"
            "def test_setup_skip(unavailable): pass\n",
            "skipped",
            0,
        ),
        (
            "import pytest\n"
            "@pytest.fixture\n"
            "def broken():\n"
            "    yield\n"
            "    raise RuntimeError('teardown')\n"
            "def test_teardown_error(broken): pass\n",
            "error",
            1,
        ),
        (
            "import pytest\n"
            "@pytest.mark.xfail(reason='strict', strict=True)\n"
            "def test_strict_xpass(): pass\n",
            "xpassed",
            1,
        ),
    ],
)
def test_failure_phase_reduction_publishes_complete_failure_report(
    tmp_path: Path, source: str, expected: str, returncode: int
) -> None:
    _write(tmp_path, "test_phase.py", source)
    result = tmp_path / "phase.json"
    completed = _run(tmp_path, "test_phase.py", "-q", result=result)
    assert completed.returncode == returncode
    _, document = _load(result)
    assert document["exitstatus"] == returncode
    assert list(_outcomes(document).values()) == [expected]


@pytest.mark.parametrize(
    "source",
    [
        "raise RuntimeError('collection')\n",
        "def test_interrupt(): raise KeyboardInterrupt()\n",
    ],
)
def test_collection_error_or_interrupt_produces_no_report(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path, "test_incomplete.py", source)
    result = tmp_path / "incomplete.json"
    completed = _run(tmp_path, "test_incomplete.py", "-q", result=result)
    assert completed.returncode not in {0, 1}
    assert not result.exists()


def test_existing_destination_is_refused_without_replacement(tmp_path: Path) -> None:
    _write(tmp_path, "test_ok.py", "def test_ok(): pass\n")
    result = tmp_path / "existing.json"
    original = b"do-not-replace\n"
    result.write_bytes(original)
    completed = _run(tmp_path, "test_ok.py", "-q", result=result)
    assert completed.returncode == int(pytest.ExitCode.INTERNAL_ERROR)
    assert result.read_bytes() == original


def test_failed_publish_removes_only_its_new_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = tmp_path / "partial.json"

    def fail_fsync(fd: int) -> None:
        del fd
        raise OSError("injected fsync failure")

    monkeypatch.setattr(plugin.os, "fsync", fail_fsync)
    assert not plugin._publish_create_new(result, b'pure-ascii\n')
    assert not result.exists()


def test_serial_warning_count_is_neither_lost_nor_duplicated(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_warnings.py",
        "import warnings\n"
        "def test_warning_one(): warnings.warn('serial-one', UserWarning)\n"
        "def test_warning_two(): warnings.warn('serial-two', UserWarning)\n"
        "def test_warning_three(): warnings.warn('serial-three', UserWarning)\n",
    )
    result = tmp_path / "warnings.json"
    completed = _run(tmp_path, "test_warnings.py", "-q", result=result)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    _, document = _load(result)
    assert document["warnings"] == 3


@pytest.mark.parametrize(("pytest_args", "expected_warnings"), [([], 1), (["-n", "2"], 3)])
def test_configure_warnings_are_counted_once_in_serial_and_xdist(
    tmp_path: Path, pytest_args: list[str], expected_warnings: int
) -> None:
    if pytest_args:
        pytest.importorskip("xdist")
    _write(
        tmp_path,
        "conftest.py",
        "import warnings\n"
        "def pytest_configure(config):\n"
        "    warnings.warn('configure-warning', UserWarning)\n",
    )
    _write(tmp_path, "test_ok.py", "def test_ok(): pass\n")
    result = tmp_path / "configure-warnings.json"
    completed = _run(
        tmp_path,
        "test_ok.py",
        *pytest_args,
        "-q",
        result=result,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    _, document = _load(result)
    assert document["warnings"] == expected_warnings


@pytest.mark.parametrize(
    "conftest_source",
    [
        (
            "import pytest\n"
            "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    yield\n"
            "    raise RuntimeError('late session-finish error')\n"
        ),
        (
            "def pytest_unconfigure(config):\n"
            "    raise RuntimeError('late unconfigure error')\n"
        ),
    ],
)
def test_late_lifecycle_failure_never_leaves_complete_report(
    tmp_path: Path, conftest_source: str
) -> None:
    _write(tmp_path, "conftest.py", conftest_source)
    _write(tmp_path, "test_ok.py", "def test_ok(): pass\n")
    result = tmp_path / "must-not-exist.json"
    completed = _run(tmp_path, "test_ok.py", "-q", result=result)
    assert completed.returncode == int(pytest.ExitCode.INTERNAL_ERROR)
    assert not result.exists()


@pytest.mark.parametrize(
    ("test_source", "candidate_exitstatus"),
    [
        ("def test_outcome(): pass\n", 0),
        ("def test_outcome(): assert False\n", 1),
    ],
)
def test_same_priority_outer_cmdline_failure_blocks_final_for_any_test_status(
    tmp_path: Path, test_source: str, candidate_exitstatus: int
) -> None:
    marker = tmp_path / "outer-observed-candidate.txt"
    _write(
        tmp_path,
        "conftest.py",
        "import json\n"
        "import pytest\n"
        "from pathlib import Path\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_cmdline_main(config):\n"
        "    yield\n"
        "    candidate = Path(config.getoption('ci_result_candidate_out'))\n"
        f"    Path({str(marker)!r}).write_text(\n"
        "        str(json.loads(candidate.read_bytes())['exitstatus'])\n"
        "        if candidate.is_file() else 'missing',\n"
        "        encoding='utf-8',\n"
        "    )\n"
        "    raise RuntimeError('outer cmdline failure after candidate')\n",
    )
    _write(tmp_path, "test_outcome.py", test_source)
    result = tmp_path / "must-not-finalize.json"
    completed = _run(tmp_path, "test_outcome.py", "-q", result=result)
    assert completed.returncode == int(pytest.ExitCode.INTERNAL_ERROR)
    assert marker.read_text(encoding="utf-8") == str(candidate_exitstatus)
    assert not result.exists()


def test_real_xdist_n2_publishes_one_controller_union(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    _write(
        tmp_path,
        "test_parallel.py",
        "import pytest\n"
        "import warnings\n"
        "@pytest.mark.parametrize('value', range(12))\n"
        "def test_parallel(value):\n"
        "    warnings.warn(f'parallel-{value}', UserWarning)\n"
        "    assert value >= 0\n",
    )
    result = tmp_path / "parallel.json"
    completed = _run(
        tmp_path,
        "test_parallel.py",
        "-n",
        "2",
        "--dist",
        "loadfile",
        "-q",
        result=result,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    _, document = _load(result)
    assert document["expected_count"] == 12
    assert len(_outcomes(document)) == 12
    assert set(_outcomes(document).values()) == {"passed"}
    assert document["warnings"] == 12


def test_xdist_collection_mismatch_produces_no_report(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    _write(
        tmp_path,
        "conftest.py",
        "import os\n"
        "def pytest_collection_modifyitems(items):\n"
        "    if os.environ.get('PYTEST_XDIST_WORKER') == 'gw1':\n"
        "        items.pop()\n",
    )
    _write(
        tmp_path,
        "test_mismatch.py",
        "def test_one(): pass\ndef test_two(): pass\n",
    )
    result = tmp_path / "mismatch.json"
    completed = _run(tmp_path, "test_mismatch.py", "-n", "2", "-q", result=result)
    assert completed.returncode != 0
    assert not result.exists()


@pytest.mark.skipif(os.name == "nt", reason="os._exit worker-crash probe is POSIX CI only")
def test_xdist_worker_crash_produces_no_report(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    _write(
        tmp_path,
        "test_crash.py",
        "import os\n"
        "def test_crash(): os._exit(17)\n"
        "def test_other(): pass\n",
    )
    result = tmp_path / "crash.json"
    completed = _run(tmp_path, "test_crash.py", "-n", "2", "-q", result=result)
    assert completed.returncode != 0
    assert not result.exists()


@dataclass
class _Report:
    outcome: str
    wasxfail: str | None = None


def test_reduction_rejects_incomplete_or_unknown_phase_shapes() -> None:
    assert plugin._reduce_outcome({"setup": _Report("passed")}) is None
    assert (
        plugin._reduce_outcome(
            {
                "setup": _Report("passed"),
                "call": _Report("passed"),
                "teardown": _Report("skipped"),
            }
        )
        is None
    )
