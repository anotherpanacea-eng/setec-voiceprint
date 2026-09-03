"""Closed negative-policy checks for train admission, coverage, and CI cost."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_SHA256 = "2d5385b0793ad82dcb28e9d2ecf7feb95a5da1f01c2afdab672a20e4c49d5b05"
EVENTS = [
    "opened", "synchronize", "reopened", "ready_for_review",
    "converted_to_draft", "labeled", "unlabeled",
]
RUN_NAME = (
    "setec-tests pr=${{ github.event.pull_request.number }} "
    "action=${{ github.event.action }} "
    "train=${{ github.event.pull_request.head.repo.full_name == github.repository "
    "&& startsWith(github.head_ref, 'train/') }} "
    "ci-ready-event=${{ github.event.label.name == 'ci-ready' }}"
)
CONCURRENCY = (
    "tests-${{ github.workflow }}-${{ github.event.pull_request.number }}-${{ "
    "(contains(fromJSON('[\"labeled\",\"unlabeled\"]'), github.event.action) && "
    "((github.event.pull_request.head.repo.full_name == github.repository && "
    "startsWith(github.head_ref, 'train/')) || github.event.label.name != "
    "'ci-ready') && github.run_id) || 'clearance' }}"
)
ARM = (
    "github.event.pull_request.draft == false && "
    "(((github.event.pull_request.head.repo.full_name == github.repository && "
    "startsWith(github.head_ref, 'train/')) && github.event.action != 'labeled' && "
    "github.event.action != 'unlabeled') || "
    "((github.event.pull_request.head.repo.full_name != github.repository || "
    "startsWith(github.head_ref, 'train/') == false) && "
    "contains(github.event.pull_request.labels.*.name, 'ci-ready') && "
    "((github.event.action != 'labeled' && github.event.action != 'unlabeled') || "
    "(github.event.action == 'labeled' && github.event.label.name == 'ci-ready'))))"
)
JOBS = {
    "pytest": ("ubuntu-latest", "30"),
    "macos-descriptor-confinement": ("macos-latest", "60"),
    "windows-descriptor-backend": ("windows-latest", "60"),
    "windows-owner-corrections": ("windows-latest", "10"),
    "windows-shingle-dedup": ("windows-latest", "10"),
    "windows-nonprose-sweep": ("windows-latest", "15"),
    "windows-private-writer-guards": ("windows-latest", "10"),
}
BINDING_ENV = {
    "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
    "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    "MERGE_SHA": "${{ github.sha }}",
    "JOB_NAME": "${{ github.job }}",
    "RUN_ID": "${{ github.run_id }}",
    "RUN_ATTEMPT": "${{ github.run_attempt }}",
}
WINDOWS_BINDING = (
    'python tools/check_pr_merge_binding.py --base "$env:BASE_SHA" '
    '--head "$env:HEAD_SHA" --github-sha "$env:MERGE_SHA" '
    '--job "$env:JOB_NAME" --run-id "$env:RUN_ID" --run-attempt "$env:RUN_ATTEMPT"'
)
UNIX_BINDING = (
    'python3 tools/check_pr_merge_binding.py --base "$BASE_SHA" '
    '--head "$HEAD_SHA" --github-sha "$MERGE_SHA" --job "$JOB_NAME" '
    '--run-id "$RUN_ID" --run-attempt "$RUN_ATTEMPT"'
)
EXPECTED_STEPS = {
    "pytest": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install core dependencies", "Run test suite",
        "Consistency gates", "Packaging P1 gates (migration checker, zero-install)",
        "Packaging P5 gates (layering, sys.path ratchet, flat-module freeze)",
        "Spec anchor lint (changed specs)",
    ],
    "macos-descriptor-confinement": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install test dependency",
        "Run descriptor-confinement and shared atomic-publish suites",
    ],
    "windows-descriptor-backend": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install focused test dependency",
        "Run Windows handle-relative writer, guard, and conflict-copy tests",
    ],
    "windows-owner-corrections": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install focused test dependency",
        "Run owner-corrections Windows contract tests",
    ],
    "windows-shingle-dedup": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install focused test dependency",
        "Run shingle-dedup Windows contract tests",
    ],
    "windows-nonprose-sweep": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install focused test dependency",
        "Run non-prose sweep Windows contract tests",
    ],
    "windows-private-writer-guards": [
        "actions/checkout@v4", "Bind billed job to the exact pull-request merge",
        "actions/setup-python@v5", "Install focused test dependency",
        "Run private-writer mode-guard and export-seam tests on native Windows",
    ],
}
EXPECTED_COMMANDS = {
    "pytest": {
        "Install core dependencies": (
            "python -m pip install --upgrade pip",
            "pip install -r plugins/setec-voiceprint/requirements.txt",
            "pip install -r plugins/setec-voiceprint/requirements-acquisition.txt",
            "pip install pypdf pytest pytest-xdist click",
            "python -m spacy download en_core_web_sm",
        ),
        "Run test suite": ("pytest plugins/setec-voiceprint/scripts/tests -n auto -q -rs",),
        "Consistency gates": (
            "python3 tools/check_capabilities_drift.py",
            "python3 tools/check_docs_freshness.py",
            "python3 tools/gen_calibration_readiness.py --check",
        ),
        "Packaging P1 gates (migration checker, zero-install)": (
            "python3 tools/check_packaging_migration.py --strict",
            "python3 tools/check_zero_install.py",
        ),
        "Packaging P5 gates (layering, sys.path ratchet, flat-module freeze)": (
            "python3 tools/check_layering.py --strict",
            "python3 tools/check_syspath_ratchet.py --strict",
            "python3 tools/check_no_new_flat_modules.py --strict",
        ),
        "Spec anchor lint (changed specs)": (
            "set -euo pipefail", 'base="origin/${{ github.base_ref }}"',
            'python3 tools/check_changed_spec_anchors.py --repo . --base "$base"',
        ),
    },
    "macos-descriptor-confinement": {
        "Install test dependency": ("python -m pip install --upgrade pip pytest",),
        "Run descriptor-confinement and shared atomic-publish suites": (
            "python -m pytest \\",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py \\",
            "plugins/setec-voiceprint/scripts/tests/test_atomic_publish.py \\",
            "plugins/setec-voiceprint/scripts/tests/test_reconstructibility_probe_set.py \\",
            "-q -rs",
        ),
    },
    "windows-descriptor-backend": {
        "Install focused test dependency": ("python -m pip install pytest tzdata",),
        "Run Windows handle-relative writer, guard, and conflict-copy tests": (
            "python -m pytest `",
            "plugins/setec-voiceprint/scripts/tests/test_manifest_validator_conflict_copies.py `",
            "plugins/setec-voiceprint/scripts/tests/test_windows_descriptor_backend.py `",
            "plugins/setec-voiceprint/scripts/tests/test_reconstructibility_probe_set.py `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_approved_refuses_output_outside_private_root_without_writes `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_bootstrap_resumes_interrupted_approved_snapshot_copy `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_bootstrap_refuses_foreign_staging_artifact `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_bootstrap_resumes_zero_byte_snapshot_prefix `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_bootstrap_refuses_raced_empty_final_destination `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_full_run_preserves_approved_policy_and_resumes_rows `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_run_resumes_after_k_row_publications `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_run_refuses_foreign_root_artifact_on_resume `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_offline_cli_loads_approved_portable_key_and_runs `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_direct_script_invocation_help_avoids_circular_import `",
            "plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py::test_direct_script_invocation_validate_run_fails_controlled_not_via_import_error `",
            "-q `", '--basetemp "${{ runner.temp }}\\\\setec-win-descriptor"',
        ),
    },
    "windows-owner-corrections": {
        "Install focused test dependency": ("python -m pip install pytest",),
        "Run owner-corrections Windows contract tests": (
            "python -m pytest `",
            "plugins/setec-voiceprint/scripts/tests/test_apply_owner_corrections.py `",
            "-q `", '--basetemp "${{ runner.temp }}\\\\setec-win-owner-corrections"',
        ),
    },
    "windows-shingle-dedup": {
        "Install focused test dependency": ("python -m pip install pytest",),
        "Run shingle-dedup Windows contract tests": (
            "python -m pytest `",
            "plugins/setec-voiceprint/scripts/tests/test_shingle_dedup.py `",
            "plugins/setec-voiceprint/scripts/tests/test_shingle_dedup_checkpoint.py `",
            "plugins/setec-voiceprint/scripts/tests/test_shingle_dedup_io_faults.py `",
            "plugins/setec-voiceprint/scripts/tests/test_shingle_dedup_validate.py `",
            "plugins/setec-voiceprint/scripts/tests/test_shingle_dedup_windows.py `",
            "-q `", '--basetemp "${{ runner.temp }}\\\\setec-win-shingle-dedup"',
        ),
    },
    "windows-nonprose-sweep": {
        "Install focused test dependency": ("python -m pip install pytest",),
        "Run non-prose sweep Windows contract tests": (
            "python -m pytest `",
            "plugins/setec-voiceprint/scripts/tests/test_nonprose_sweep.py `",
            "plugins/setec-voiceprint/scripts/tests/test_windows_descriptor_backend.py `",
            "-q -rs `", '--basetemp "${{ runner.temp }}\\\\setec-win-nonprose"',
        ),
    },
    "windows-private-writer-guards": {
        "Install focused test dependency": ("python -m pip install pytest tzdata pyyaml",),
        "Run private-writer mode-guard and export-seam tests on native Windows": (
            "python -m pytest `",
            "plugins/setec-voiceprint/scripts/tests/test_atomic_publish.py `",
            "plugins/setec-voiceprint/scripts/tests/test_normalize_author_registry.py `",
            "plugins/setec-voiceprint/scripts/tests/test_author_corpus_export.py `",
            "plugins/setec-voiceprint/scripts/tests/test_prepare_author_document_adapter.py `",
            '--deselect "plugins/setec-voiceprint/scripts/tests/test_prepare_author_document_adapter.py::test_adapter_refuses_declared_nonbaseline_material" `',
            "-q `", '--basetemp "${{ runner.temp }}\\\\setec-win-private-writers"',
        ),
    },
}


class UniqueBaseLoader(yaml.BaseLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueBaseLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping,
)


def _load(text: str):
    return yaml.load(text, Loader=UniqueBaseLoader)


def _commands(run: str) -> tuple[str, ...]:
    return tuple(
        line.strip() for line in run.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _step_id(step: dict) -> str:
    return step.get("name") or step.get("uses") or ""


def _workflow_names(directory: Path) -> set[str]:
    return {
        path.name for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    }


def _release_digest(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _violations(text: str) -> list[str]:
    problems = []
    try:
        workflow = _load(text)
    except Exception as exc:
        return [f"invalid YAML: {exc}"]
    if set(workflow) != {"name", "run-name", "on", "permissions", "concurrency", "jobs"}:
        problems.append("closed workflow keys")
    if workflow.get("name") != "tests" or workflow.get("run-name") != RUN_NAME:
        problems.append("bounded activity run-name")
    if workflow.get("on") != {"pull_request": {"types": EVENTS}}:
        problems.append("exact PR-only events")
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("read-only permissions")
    if workflow.get("concurrency") != {
        "group": CONCURRENCY, "cancel-in-progress": "true",
    }:
        problems.append("label-safe PR concurrency")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != set(JOBS):
        return problems + ["closed seven-job set"]
    for job_name, (runner, timeout) in JOBS.items():
        job = jobs[job_name]
        if set(job) != {"if", "runs-on", "timeout-minutes", "steps"}:
            problems.append(f"{job_name}: closed job properties")
            continue
        if job["if"] != ARM or job["runs-on"] != runner or job["timeout-minutes"] != timeout:
            problems.append(f"{job_name}: guard/runner/timeout")
        steps = job["steps"]
        if not isinstance(steps, list) or [_step_id(step) for step in steps] != EXPECTED_STEPS[job_name]:
            problems.append(f"{job_name}: closed step set/order")
            continue
        for index, step in enumerate(steps):
            step_name = _step_id(step)
            if step_name == "actions/checkout@v4":
                expected = {"uses": "actions/checkout@v4"}
                if job_name == "pytest":
                    expected["with"] = {"fetch-depth": "0"}
                if step != expected:
                    problems.append(f"{job_name}: exact synthetic checkout")
            elif step_name == "actions/setup-python@v5":
                expected_with = {"python-version": "3.12"}
                if job_name in {"pytest", "macos-descriptor-confinement"}:
                    expected_with["cache"] = "pip"
                if step != {"uses": "actions/setup-python@v5", "with": expected_with}:
                    problems.append(f"{job_name}: exact Python setup")
            elif step_name == "Bind billed job to the exact pull-request merge":
                command = UNIX_BINDING if not job_name.startswith("windows-") else WINDOWS_BINDING
                if step != {
                    "name": step_name, "id": "merge_binding", "env": BINDING_ENV,
                    "run": command,
                }:
                    problems.append(f"{job_name}: load-bearing binding")
                if index != 1:
                    problems.append(f"{job_name}: binding order")
            else:
                allowed_keys = {"name", "run"}
                if job_name.startswith("windows-") and step_name.startswith("Run "):
                    allowed_keys.add("shell")
                    if step.get("shell") != "pwsh":
                        problems.append(f"{job_name}/{step_name}: exact shell")
                if step_name in {
                    "Consistency gates",
                    "Packaging P1 gates (migration checker, zero-install)",
                    "Packaging P5 gates (layering, sys.path ratchet, flat-module freeze)",
                    "Spec anchor lint (changed specs)",
                }:
                    allowed_keys.add("if")
                    if step.get("if") != "always() && steps.merge_binding.outcome == 'success'":
                        problems.append(f"{job_name}/{step_name}: binding-success condition")
                if set(step) != allowed_keys:
                    problems.append(f"{job_name}/{step_name}: closed step properties")
                expected_commands = EXPECTED_COMMANDS[job_name][step_name]
                if _commands(step.get("run", "")) != expected_commands:
                    problems.append(f"{job_name}/{step_name}: closed command body")
    return problems


def test_current_workflow_holds_closed_train_policy():
    workflow_paths = _workflow_names(ROOT / ".github" / "workflows")
    assert workflow_paths == {
        "release.yml", "tests.yml",
    }
    assert _violations(WORKFLOW.read_text(encoding="utf-8")) == []
    release_text = RELEASE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _release_digest(release_text) == RELEASE_SHA256
    release = _load(release_text)
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert set(release["jobs"]) == {"publish"}


def test_workflow_inventory_includes_yaml_extension(tmp_path: Path):
    (tmp_path / "tests.yml").write_text("name: tests\n", encoding="utf-8")
    (tmp_path / "hidden.yaml").write_text("name: hidden\n", encoding="utf-8")
    assert _workflow_names(tmp_path) == {"tests.yml", "hidden.yaml"}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "    runs-on: ubuntu-latest",
            "    strategy:\n      matrix:\n        copy: [1, 2]\n    runs-on: ubuntu-latest",
        ),
        (
            "          set -euo pipefail",
            "          set -euo pipefail\n          curl https://example.invalid",
        ),
    ],
)
def test_release_workflow_cost_or_command_mutation_fails_closed(old: str, new: str):
    text = RELEASE.read_text(encoding="utf-8")
    assert old in text
    assert _release_digest(text.replace(old, new, 1)) != RELEASE_SHA256


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("run-name: >-", "run-name: raw-${{ github.event.label.name }}"),
        ("  pull_request:\n", "  push:\n    branches: [main]\n  pull_request:\n"),
        ("  contents: read", "  contents: write"),
        (" && github.run_id) || 'clearance'", ") || 'clearance'"),
        ("head.repo.full_name == github.repository", "head.repo.full_name != github.repository"),
        ("github.event.action != 'unlabeled'", "github.event.action == 'unlabeled'"),
        ("    runs-on: ubuntu-latest", "    strategy:\n      matrix:\n        copy: [1, 2]\n    runs-on: ubuntu-latest"),
        ("    timeout-minutes: 30", "    timeout-minutes: 300"),
        ("      - uses: actions/setup-python@v5", "      - run: sleep 600\n      - uses: actions/setup-python@v5"),
        ("python3 tools/check_capabilities_drift.py", "python3 tools/check_capabilities_drift.py\ncurl https://example.invalid"),
        ("python3 tools/check_pr_merge_binding.py", "echo python3 tools/check_pr_merge_binding.py"),
        ("        id: merge_binding", "        id: merge_binding\n        continue-on-error: true"),
        ("always() && steps.merge_binding.outcome == 'success'", "always()"),
        ("  macos-descriptor-confinement:", "  hidden-runner:\n    runs-on: ubuntu-latest\n    steps:\n      - run: sleep 600\n\n  macos-descriptor-confinement:"),
    ],
)
def test_policy_mutations_fail_closed(old: str, new: str):
    text = WORKFLOW.read_text(encoding="utf-8")
    assert old in text
    mutated = text.replace(old, new, 1)
    assert _violations(mutated), f"mutation escaped: {old!r}"
