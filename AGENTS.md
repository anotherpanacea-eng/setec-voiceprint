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
