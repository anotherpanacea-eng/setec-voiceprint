### Added

**`.github/workflows/claude.yml`: on-demand Claude runs from GitHub.** A
comment, review, or issue from an owner, member, or collaborator that mentions
`@claude` starts one `anthropics/claude-code-action@v1` run. No other event
triggers it, so pushes and PR updates spend no Actions minutes. Needs the
`CLAUDE_CODE_OAUTH_TOKEN` repository secret.
