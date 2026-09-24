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


def test_every_trigger_requires_a_trusted_author():
    events = re.findall(r"github\.event_name == '(\w+)'", WORKFLOW)
    assert sorted(events) == [
        "issue_comment", "issues", "pull_request_review", "pull_request_review_comment",
    ]
    assert WORKFLOW.count(TRUSTED) == len(events)


def test_fork_guard_runs_before_checkout():
    guard = WORKFLOW.find("isCrossRepository")
    checkout = WORKFLOW.find("uses: actions/checkout@")
    assert guard != -1
    assert guard < checkout


def test_workflow_token_has_no_write_scope_but_oidc():
    block = re.search(r"^    permissions:\n((?:      .*\n)+)", WORKFLOW, re.MULTILINE)
    assert block is not None
    scopes = dict(re.findall(r"^\s+([\w-]+):\s*(\w+)", block.group(1), re.MULTILINE))
    writes = {k for k, v in scopes.items() if v == "write"}
    assert writes == {"id-token"}
