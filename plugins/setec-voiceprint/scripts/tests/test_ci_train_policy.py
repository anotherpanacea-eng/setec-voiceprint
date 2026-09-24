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
CLAUDE = ROOT / ".github" / "workflows" / "claude.yml"
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


CLAUDE_EVENTS = {
    "issue_comment": ["created"],
    "pull_request_review_comment": ["created"],
    "pull_request_review": ["submitted"],
    "issues": ["opened"],
}
CLAUDE_TRUSTED = """fromJSON('["OWNER","MEMBER","COLLABORATOR"]')"""
CLAUDE_PERMISSIONS = {
    "contents": "write", "pull-requests": "write", "issues": "write",
    "id-token": "write", "actions": "read",
}
# Pinned to a commit: the step holds the OAuth token and write permissions.
CLAUDE_ACTION = "anthropics/claude-code-action@8cf3482550831fb35a4fc3fbf7ca139cf8028b4c"


# Per event: the payload object whose author is checked, and the text fields
# that may carry the mention.
CLAUDE_GATE_FIELDS = {
    "issue_comment": ("comment", {"body"}),
    "pull_request_review_comment": ("comment", {"body"}),
    "pull_request_review": ("review", {"body"}),
    "issues": ("issue", {"body", "title"}),
}


def _split_top(expr: str, op: str) -> list[str]:
    """Split on `op` outside parentheses and quotes, peeling redundant outer parens."""
    expr = expr.strip()
    while expr.startswith("(") and _closing_paren(expr, 0) == len(expr) - 1:
        expr = expr[1:-1].strip()
    parts, depth, quote, start, i = [], 0, None, 0, 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and expr.startswith(op, i):
            parts.append(expr[start:i].strip())
            start = i + len(op)
            i = start
            continue
        i += 1
    parts.append(expr[start:].strip())
    return parts


def _closing_paren(expr: str, open_at: int) -> int:
    depth, quote = 0, None
    for i in range(open_at, len(expr)):
        ch = expr[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _claude_gate_violations(condition: str) -> list[str]:
    """Each `||` branch must be one event, a trusted author, and a mention."""
    problems: list[str] = []
    seen: list[str] = []
    for branch in _split_top(" ".join(condition.split()), "||"):
        terms = _split_top(branch, "&&")
        events = [
            event for event in CLAUDE_GATE_FIELDS
            if f"github.event_name == '{event}'" in terms
        ]
        if len(events) != 1 or len(terms) != 3:
            problems.append(f"job gate branch not event-trusted-mention: {branch!r}")
            continue
        event = events[0]
        obj, fields = CLAUDE_GATE_FIELDS[event]
        trusted = f"contains({CLAUDE_TRUSTED}, github.event.{obj}.author_association)"
        mention = [term for term in terms if term not in {f"github.event_name == '{event}'", trusted}]
        allowed = {f"contains(github.event.{obj}.{field}, '@claude')" for field in fields}
        if trusted not in terms or len(mention) != 1:
            problems.append(f"{event}: gate lacks the trusted-author check")
            continue
        mentions = set(_split_top(mention[0], "||"))
        if not mentions or not mentions <= allowed:
            problems.append(f"{event}: gate lacks an @claude mention on its own payload")
        seen.append(event)
    if sorted(seen) != sorted(CLAUDE_GATE_FIELDS):
        problems.append(f"job gate events: {sorted(seen)}")
    return problems


def _claude_violations(text: str) -> list[str]:
    """Policy for the on-demand @claude workflow (public repo, billed runner).

    Only a trusted author's mention may start a run, on one bounded job,
    with no widened actor allowlist and no extra commands.
    """
    problems: list[str] = []
    try:
        workflow = _load(text)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a violation
        return [f"unparseable: {exc}"]
    on = workflow.get("on") or {}
    if set(on) != set(CLAUDE_EVENTS):
        problems.append(f"triggers: {sorted(on)}")
    for event, types in CLAUDE_EVENTS.items():
        if (on.get(event) or {}).get("types") != types:
            problems.append(f"{event}: types")
    if "permissions" in workflow or "concurrency" in workflow:
        problems.append("workflow-level permissions or concurrency")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != {"claude"}:
        return problems + [f"jobs: {sorted(jobs)}"]
    job = jobs["claude"]
    if set(job) != {"if", "runs-on", "timeout-minutes", "permissions", "steps"}:
        problems.append(f"job keys: {sorted(job)}")
    problems.extend(_claude_gate_violations(job.get("if", "")))
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("runner")
    timeout = job.get("timeout-minutes", "")
    if not timeout.isdigit() or int(timeout) > 30:
        problems.append("timeout")
    if job.get("permissions") != CLAUDE_PERMISSIONS:
        problems.append("job permissions")
    steps = job.get("steps") or []
    if [step.get("uses") for step in steps] != ["actions/checkout@v4", CLAUDE_ACTION]:
        problems.append("steps")
    for step in steps:
        if "run" in step or "continue-on-error" in step:
            problems.append(f"{step.get('uses')}: run or continue-on-error")
    inputs = (steps[-1].get("with") or {}) if steps else {}
    if set(inputs) - {"claude_code_oauth_token", "additional_permissions"}:
        problems.append(f"action inputs: {sorted(inputs)}")
    if inputs.get("claude_code_oauth_token") != "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}":
        problems.append("token source")
    return problems


def test_current_workflow_holds_closed_train_policy():
    workflow_paths = _workflow_names(ROOT / ".github" / "workflows")
    assert workflow_paths == {
        "claude.yml", "release.yml", "tests.yml",
    }
    assert _violations(WORKFLOW.read_text(encoding="utf-8")) == []
    release_text = RELEASE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _release_digest(release_text) == RELEASE_SHA256
    release = _load(release_text)
    assert release["on"] == {"push": {"tags": ["v*"]}}
    assert set(release["jobs"]) == {"publish"}
    assert _claude_violations(CLAUDE.read_text(encoding="utf-8")) == []


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


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  issue_comment:\n", "  pull_request_target:\n  issue_comment:\n"),
        ("    types: [opened]", "    types: [opened, assigned]"),
        ("contains(github.event.comment.body, '@claude') &&", "("),
        ('["OWNER","MEMBER","COLLABORATOR"]\'), github.event.review', '["OWNER","MEMBER","COLLABORATOR","NONE"]\'), github.event.review'),
        ("contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), github.event.issue.author_association)", "true"),
        ("(github.event_name == 'pull_request_review' &&", "(github.event_name == 'pull_request_review' || github.event_name == 'issues' &&"),
        ("claude-code-action@8cf3482550831fb35a4fc3fbf7ca139cf8028b4c", "claude-code-action@v1"),
        ("    timeout-minutes: 30", "    timeout-minutes: 300"),
        ("    runs-on: ubuntu-latest", "    strategy:\n      matrix:\n        copy: [1, 2]\n    runs-on: ubuntu-latest"),
        ("      contents: write", "      contents: write\n      packages: write"),
        ("      - uses: actions/checkout@v4", "      - run: curl https://example.invalid\n      - uses: actions/checkout@v4"),
        ("          claude_code_oauth_token:", "          allowed_non_write_users: '*'\n          claude_code_oauth_token:"),
        ("          claude_code_oauth_token:", "          allowed_bots: '*'\n          claude_code_oauth_token:"),
    ],
)
def test_claude_workflow_policy_mutations_fail_closed(old: str, new: str):
    text = CLAUDE.read_text(encoding="utf-8")
    assert old in text
    assert _claude_violations(text.replace(old, new, 1)), f"mutation escaped: {old!r}"


def test_claude_workflow_comment_edits_stay_green():
    text = CLAUDE.read_text(encoding="utf-8")
    edited = "# reworded header\n" + text.replace("# On-demand", "# On demand", 1)
    assert _claude_violations(edited) == []


def test_claude_gate_rejects_count_preserving_unguarded_branch():
    # Codex round 2: drop the issues branch's trust check but repeat one in
    # issue_comment, so substring counts stay the same.
    text = CLAUDE.read_text(encoding="utf-8")
    trusted_issue = (
        "contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), "
        "github.event.issue.author_association)"
    )
    trusted_comment = trusted_issue.replace(".issue.", ".comment.")
    assert trusted_issue in text
    mutated = text.replace(trusted_issue, "true", 1).replace(
        trusted_comment, f"{trusted_comment} && {trusted_issue}", 1,
    )
    assert mutated.count(CLAUDE_TRUSTED) == text.count(CLAUDE_TRUSTED)
    assert _claude_violations(mutated)
