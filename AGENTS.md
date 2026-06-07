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
   already encoded in `ROADMAP.md`; for ad-hoc work, the spec is a
   written brief — chat for trivial changes, a GitHub Issue once the
   work is non-trivial. See "Where work comes from" below.
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

## Where work comes from: roadmap, briefs, and Issues

Every change implements from a **written contract**, never from an
unscoped instruction like "improve the release workflow." Agents are
prone to plausible-adjacent work — reasonable-looking changes nobody
asked for — and this repo's roadmap is rich enough with adjacent ideas
to make that easy. The contract is the leash. It comes from one of
three places, in order of formality:

1. **A `ROADMAP.md` item.** Paired-release and cathedral-upgrade work
   is already specified there. The roadmap entry *is* the brief; no
   Issue is needed. Reference it in the PR.
2. **A GitHub Issue** (`Task brief` template: Goal / Acceptance
   criteria / Out of scope / Constraints). This is the home for
   **non-trivial ad-hoc work** that isn't on the roadmap — exactly the
   case that used to live only in chat and evaporate. The acceptance
   criteria are what the *second* reviewer checks the diff against, so
   write them concretely. The PR closes the Issue (`Closes #N`).
3. **A chat brief**, for trivial changes (typo, one-line fix, fixture
   regen) that also qualify for the direct-push path below.

Roadmap and Issues do different jobs and should not duplicate each
other: the roadmap is strategic and narrative ("where is this going,
what's deferred, what's out of scope"); an Issue is a single bounded
work order with a definition of done. A roadmap item becomes an Issue
only when it's close enough to implement and needs acceptance criteria
the roadmap doesn't carry.

**Constraints belong in the contract, not just in the code.** This is a
forensics framework where an uncalibrated threshold silently shipped as
a default is a real failure mode. "Threshold stays provisional; no
registry default until calibrated" is the kind of line that belongs in
an Issue's acceptance criteria, where the reviewer will enforce it —
not only in a code comment the review might skim past.

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

## Keeping docs current (the docs-freshness step)

Shipping or changing a capability is not done until its paper trail moves with
it. These travel together, and `tools/check_docs_freshness.py` gates the pair of
them in CI:

- **`capabilities.yaml`** — add/update the entry (the drift linter
  `tools/check_capabilities_drift.py` enforces the surface/script match).
- **Task-surface label** — if the work adds a new `TASK_SURFACE`, register it by
  dropping `scripts/claim_license_surfaces/<surface>.txt` (filename = key,
  contents = label). Never edit a shared surface dict/list: `TASK_SURFACE_LABELS`
  and `output_schema.VALID_TASK_SURFACES` both derive from that fragment dir, so a
  fragment is the whole change — and parallel audit PRs can't collide on it (#170).
- **`CHANGELOG.md`** — a line referencing the capability `id` (the freshness gate
  fails CI if a curated capability has no changelog mention).
- **Calibration-readiness matrix** — auto-derived; run
  `python3 tools/gen_calibration_readiness.py` and commit any change (CI runs
  `--check`).
- **`ROADMAP.md`** — update the dated status-reconciliation section when what's
  shipped/left changes.
- **`references/signals-glossary.md`** — if a new signal was added.

Before pushing capability work:

```bash
python3 tools/check_capabilities_drift.py     # manifest ↔ source
python3 tools/gen_calibration_readiness.py     # refresh the matrix, then commit
python3 tools/check_docs_freshness.py          # changelog coverage + matrix freshness
```

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
- **Bump the version at merge, not in the PR.** Open PRs merge in an
  unknown order, so a `plugin.json` version pinned inside a feature branch
  collides or goes stale. A PR's CHANGELOG entry names its bump *class*
  (`feat` → MINOR, `fix`/`docs`/`chore` → PATCH) but does **not** pin the
  number or edit `plugin.json`; the merger sets `plugin.json` + the
  `Plugin version X → Y` line when the merge commit lands, then tags.
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
