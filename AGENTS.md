# Agent workflow

This repo is single-author (`anotherpanacea-eng`) but multi-agent: Claude
sessions and Codex sessions both contribute. This document records the
workflow they should follow.

## The flow

```
spec  →  review  →  write  →  review  →  fix  →  merge
            ▲                    ▲
         codex                 codex
         claude                claude
```

Most cycles are:

1. **Spec.** The user (or an agent on the user's behalf) lays out what
   a release should do. For paired-release-schedule items this is
   already encoded in `ROADMAP.md`; for ad-hoc work, the spec is
   whatever the user types into chat.
2. **Spec review.** Either Claude or Codex (or both) reads the spec
   and surfaces gaps, dependency issues, or scope creep before
   writing starts.
3. **Write.** One agent does the actual implementation. Currently
   Claude does most of the writing; this is not a rule, just a
   pattern.
4. **Code review.** The other agent reads the diff and flags issues.
   Codex emits structured `code-comment` blocks anchored to file +
   line; Claude reviews via `Skill: review` (see `~/.claude/skills/review`).
5. **Fix.** The writing agent applies the fixes, runs tests, commits.
6. **Merge.** Via PR + merge commit. See below.

## Long-running surfaces: belt, suspenders, buttons

SETEC is glass-box stylometry for commodity, local hardware — there is no
large cloud compute to fall back on, and the calibration host is
device-unstable (the WSL2 + ROCm path has documented host hangs). So any
process that runs longer than a few minutes MUST be:

- **Recoverable (belt).** Sharded, so a crash loses at most one shard's
  work — never the whole run.
- **Visible (suspenders).** Emit progress to stdout/disk while running, so
  an operator can see where it is and estimate completion.
- **Continuable (buttons).** Checkpoint partial results to disk and support
  `--resume`, so a killed or hung run picks up where it left off.

This is a standing requirement at the project's current stage, not a
per-feature nicety. The reference implementations are `shard_runner`
(per-shard claim + cache + SIGTERM-safe checkpointing) and the calibration
aggregate (a partial survey flushed after each signal completes). When you
add or touch a surface that loads a full corpus into one process, hold it
to all three before merging.

Worked example — `validation_harness` straddles the line:

- Its *scoring* phase is compliant: progress logging plus an incremental
  scored-records cache with `--resume`.
- Its *metrics/bootstrap* phase honors none of the three — single-threaded
  (not recoverable), silent (not visible), and uncheckpointed (not
  continuable). A host hang loses the entire bootstrap with nothing to
  resume.

**Audit backlog** — full-corpus single-process surfaces to bring into
compliance (tracked in #133):

- `validation_harness` metrics/bootstrap phase — the worked example above.
- single-process `check_corpus` / `corpus_hygiene` at corpus scale.
- the standalone `calibration_survey.py` CLI (the bake-off driver).
- any other surface that loads a full corpus into one process.

## PRs and merges

**Default to PR-per-release with a merge commit.** This makes the
spec→review→write→review→fix structure durable on GitHub and gives
`git log --first-parent main` a clean release timeline.

### Branch naming

- `r<N>-<surface>` for paired-release-schedule items, e.g.
  `r12-semantic-trajectory`.
- `fix/r<N>-p<level>` for reviewer-flagged patch work, e.g.
  `fix/r11-p2-review`.
- `chore/<short-description>` for non-release work like this doc.
- `codex/<short-description>` for Codex-authored proposals (Codex's
  conventional prefix; respected here so authorship is legible).

### Merge mechanics

- **Use `gh pr merge <N> --merge`** (merge commit, not squash). This
  preserves both the original work commits and the review-fix commits
  as distinct nodes on `main`. Squash collapses the spec-review-fix
  structure, which is the most useful audit trail this repo has.
- **Delete the branch on merge** (`--delete-branch`).
- **Tag from `main`** after the merge commit lands, not from the
  branch. Tag names follow the `v1.MAJOR.MINOR` convention enforced
  by `CHANGELOG.md`'s versioning preamble.
- **Auto-merge on dual agreement.** When both reviewing agents (Claude
  and Codex) agree a PR is ready — CI green and review threads resolved —
  merge it (merge commit) without waiting for a further human prompt. The
  maintainer gave standing approval for this case (2026-06-06). If only one
  agent has reviewed, or a review comment is unresolved, hold for the
  second opinion rather than self-merging.

### When to skip the PR

Direct push to `main` is fine for:

- Typo fixes in docs or CHANGELOG entries.
- Regenerating test fixtures whose content is deterministic and
  reproducible.
- Single-line corrections that don't change behavior.

Anything that changes behavior, adds a script, modifies a script's
public CLI, or moves a CHANGELOG-meriting amount of work should land
via PR.

## PR template

`.github/pull_request_template.md` ships the canonical PR shape:
**Summary / Why / Validation**. The Validation section is load-bearing
— it documents the proof-of-correctness the reviewer can read against
the diff. For this repo, "Validation" almost always includes a test
count (`N tests pass + 1 skipped`).

## Conventions in commit messages

- `feat(spine): paired-release schedule R<N> — <surface> (<version>)`
  for new R-releases.
- `fix(reviewer-p2): <short-description> (<version>)` for reviewer-
  flagged patch fixes.
- `chore: <description>` or `docs: <description>` for ancillary work.

Bodies should name what changed and (briefly) why. Reviewer-P2 fix
commits should name the reviewer's reproduction in one or two lines
per issue so the audit trail survives the squash.

## Co-authorship

Commits authored end-to-end by Claude include the trailer:

```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Commits authored end-to-end by Codex use Codex's conventional
trailer. Pair-authored commits (e.g., Claude wrote, Codex reviewed
and Claude fixed) get both.

## Tagging

After a merge commit lands on `main`:

```bash
git checkout main
git pull
git tag v1.<MAJOR>.<MINOR>
git push origin v1.<MAJOR>.<MINOR>
```

Tags are required for the marketplace + plugin install flow to find
the right version.

## When this document is wrong

Update it. It's a working document, not a contract. The goal is for
any future agent (Claude session, Codex session, future maintainer)
to read this file and know what shape the work should take.
