### Added

**Comment-triggered `@claude` workflow.** A `.github/workflows/claude.yml` job
runs claude-code-action when an owner, member, or collaborator mentions
`@claude` in an issue, PR comment, or review. It has no `pull_request` trigger,
refuses fork PRs before checkout, pins both actions to commit SHAs, and keeps
the workflow token read-only apart from `id-token: write`. `test_claude_workflow.py`
guards those boundaries, and the closed workflow inventory now lists the file.

The closed workflow policy refuses fork-guard overrides and untrusted checkout
inputs while accepting redundant gate parentheses; Spec 81 records this bounded
on-demand exception separately from the seven train-clearance lanes.
