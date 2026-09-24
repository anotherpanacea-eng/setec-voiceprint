"""Safety boundaries of the comment-triggered @claude workflow.

This job hands a write-capable App token and the Claude OAuth token to a
model acting on comment text, so a few properties must not drift in a later
edit: every action is pinned to an immutable commit, only owners, members,
and collaborators can start a run, fork PRs are refused before checkout,
and the workflow's own GITHUB_TOKEN carries no write scope.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = (ROOT / ".github" / "workflows" / "claude.yml").read_text(encoding="utf-8")

TRUSTED = """contains(fromJSON('["OWNER","MEMBER","COLLABORATOR"]'),"""


def test_every_action_is_pinned_to_a_commit():
    refs = re.findall(r"^\s*-?\s*uses:\s*(\S+)", WORKFLOW, re.MULTILINE)
    assert refs
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"{ref} is not pinned to a commit SHA"


def test_every_trigger_requires_a_mention_and_a_trusted_author():
    # Split the job condition into one clause per event, so a guard repeated
    # in one clause cannot hide an event whose own clause lacks it.
    gate = WORKFLOW[WORKFLOW.index("    if: >-"):WORKFLOW.index("    runs-on:")]
    clauses = re.split(r"\)\) \|\|", gate)
    events = {}
    for clause in clauses:
        names = re.findall(r"github\.event_name == '(\w+)'", clause)
        assert len(names) == 1, clause
        events[names[0]] = clause
    assert sorted(events) == [
        "issue_comment", "issues", "pull_request_review", "pull_request_review_comment",
    ]
    for name, clause in events.items():
        assert "'@claude')" in clause, f"{name} does not require the @claude mention"
        assert TRUSTED in clause, f"{name} does not require a trusted author"


def test_fork_guard_fails_closed_before_checkout():
    start = WORKFLOW.index("- name: Refuse pull requests from forks")
    checkout = WORKFLOW.index("uses: actions/checkout@")
    assert start < checkout
    step = WORKFLOW[start:checkout]
    assert "if: github.event.issue.pull_request || github.event.pull_request" in step
    assert "--json isCrossRepository" in step
    # Anything other than an explicit "false", including a failed lookup,
    # must stop the job.
    assert '[ "$cross" != "false" ]' in step
    assert "exit 1" in step
    assert "continue-on-error" not in step


def test_workflow_token_has_no_write_scope_but_oidc():
    block = re.search(r"^    permissions:\n((?:      .*\n)+)", WORKFLOW, re.MULTILINE)
    assert block is not None
    scopes = dict(re.findall(r"^\s+([\w-]+):\s*(\w+)", block.group(1), re.MULTILINE))
    writes = {k for k, v in scopes.items() if v == "write"}
    assert writes == {"id-token"}
