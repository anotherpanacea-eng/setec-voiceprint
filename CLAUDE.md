# CLAUDE.md

This repo's agent workflow, conventions, and hard-won lessons live in **[`AGENTS.md`](AGENTS.md)** — the canonical, tool-agnostic source (Claude Code, Codex, and others all read from it). This file exists only so Claude Code's auto-load points you there.

Read `AGENTS.md` first. In particular:

- **`AGENTS.md` § Keeping docs current (the docs-freshness step)** — `tools/check_docs_freshness.py` gates changelog coverage + capability-matrix freshness (CI runs it). Any change that ships behavior drops a **`changelog.d/<slug>.md`** fragment — never edit `CHANGELOG.md` directly.
- **`AGENTS.md` § PRs and merges → Merge mechanics / § Tagging** — **merge commit, never squash**. **Version + changelog are cut at release, not pinned in the PR**: a PR ships a `changelog.d/` fragment; at release you run `tools/assemble_changelog.py --version X.Y.Z --date …` and **tag from `main`** after the merge lands.
- **`AGENTS.md` § Fleet / cross-repo context** — this is the **producer** of the SETEC normalized-entrypoint contract (`setec run <surface> --json`) that apodictic and setec-voicewright consume under a pinned tag + drift gate. **Changing a consumed surface ripples downstream** — follow the surface-addition checklist (`capabilities.d` fragment + a per-id **drop-in** `_golden_capabilities/<id>.json` fragment — **no `==N` count bumps** post-#170; keep `references/contract_fixtures/` in sync).

Update `AGENTS.md`, not this file, when the workflow changes.
