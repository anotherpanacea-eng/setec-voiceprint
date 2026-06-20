# Agent workflow

This repo is single-author (`anotherpanacea-eng`) but multi-agent: Claude
sessions and Codex sessions both contribute. This document records the
workflow they should follow.

## Fleet / cross-repo context

This repo is one of four maintained together (all `github.com/anotherpanacea-eng`):
`setec-voiceprint` (producer · public · Python — **this repo**), `apodictic`
(consumer + producer · public · Python), `setec-voicewright` (consumer · private ·
Python), `APODICTIC-Gemini` (consumer · private · TS app).

**This repo's role:** PRODUCER of the SETEC normalized-entrypoint contract
(`setec run <surface> --json`, JSON envelope). Consumed by `apodictic` and
`setec-voicewright`, each of which pins a release tag and runs an offline drift
gate against the vendored contract. **Changing a consumed surface ripples to
them** — follow the surface-addition checklist (capabilities.d fragment + golden +
the `_golden_*` count bumps) and keep `references/contract_fixtures/` in sync; the
consumers catch drift on their next weekly pull.

**Shared workflow:** spec → review → build → review → merge. **Both reviews (spec
and build) are subagent passes — iterate until everything is fixed.** Then the fork:
**a docs-only change lands as a direct merge commit; anything more goes up as a PR
for Codex 5.5 review** (don't merge out from under it) — iterate with Codex until
clean + green, then merge. Merge commits, never squash; version + changelog are cut
at release (a PR ships a `changelog.d/` fragment), tagged from `main`. (Full detail
in §The flow below.)

**Fuller cross-repo context** (full backlog, topology, deep lessons) lives in the
maintainer's local `Cowork/repo-fleet/` hub — **not reachable from cloud
containers** (which hold only this one git repo). If you're a cloud session and
need cross-repo context beyond this section, flag it rather than guessing.

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

## Build pre-flight — root out the P1/P2 modes before review

A retrospective over 350 review findings (68 P1 / 115 P2) found the same handful of
mistakes recurring. Codex review is token-gated (≈one round per 5-hour window), so the
goal is **first-pass-clean**: run this before you call a build done, and re-run it on
every fix — *a fix is a build*.

**Root cause (~40% of P1s):** the spec/build *asserts* something about existing source —
an API, a field, a `file:line`, an env-var, a compute tier, a sibling spec, an invariant —
that it never opened the file to verify. Pattern-matching a plausible API from memory is
the single biggest defect source.

1. **API-anchor drift (dominant).** For every symbol / field / `file:line` / env-var /
   compute-tier / CLI-flag / sibling-spec / precedent you cite: **grep or open it. If grep
   finds nothing, it does not exist — do not assert it.** Verify the real signature *and*
   the real return shape (including caps like `.most_common(20)`). Don't describe a
   precedent file you haven't opened this session.
2. **Posture leak.** No bare thresholdable scalar in `results`; a `band` must name the
   **measured property** (`smoothed`/`typical`/`indeterminate`), never the **inference
   target** (`machine_like_spectrum`); fail-direction **closed**. Run the no-verdict
   recursive walk — no `is_ai`/`is_human`/`verdict`/`label`/selection key, and nothing one
   hand-edit from a back door.
3. **M1/M2 overclaim.** "stdlib / model-free" must be import-clean and CI-runnable —
   including `build_output`'s `task_surface` being in `VALID_TASK_SURFACES` (else M1 raises
   and literally doesn't build). spaCy POS/dependency is model-gated; numpy is an allowed
   transitive CI dep. If the core needs a model, it's M2.
4. **Stale registration / golden.** Drop-in only (post-#170): a per-id
   `_golden_capabilities/<id>.json` fragment, **NO `==N` count literal anywhere**, the YAML
   fragment carries `entries:` + `script_path:` (open a real sibling, e.g.
   `dependency_distance_audit.yaml`), `git add` the fragment, the dropin/drift/docs-freshness
   gates pass. There is no `_golden_task_surface_labels` (retired).
5. **Untestable / false-invariant AC.** Every acceptance must run against REAL behavior. No
   "byte-identical" without a frozen-fixture test; no "copied from line X" without opening
   line X. An AC the real API can't satisfy is NEEDS-REWORK, not a test to write.
6. **Math / data-structure.** Bounds hold on saturated / tie / empty input; immutable
   frozensets aren't "added to" from a caller (the module itself must change); once-consumed
   generators are materialized.
7. **Process.** Honor the spec's PR-split (don't bundle N sub-PRs in one commit — it defeats
   the per-PR Codex gate); complete the paper trail (signals-glossary / changelog / ROADMAP)
   even where CI doesn't gate it — Codex reads it.

**Fix loop:** after folding a Codex finding, confirm it fully resolves the finding, self-review
the fix against modes 1–7 (a fix that adds a field can introduce a mode-1/2/4 defect), re-run
the suite, *then* push. Round 2 should be empty because you caught the regression, not Codex.

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
- Its *metrics/bootstrap* phase is recoverable **when `--metrics-cache` is
  passed**: a per-CI `_MetricsCheckpoint` (atomic, default flush-after-every-CI)
  logs each completed CI and resumes from the last one, gated by a
  records-fingerprint. The remaining gap is that it is **opt-in** — without the
  flag the bootstrap runs un-checkpointed, so a host hang loses it; the harness
  now emits a loud stderr warning naming the one-flag fix. (Within-CI resampling
  is still single-pass, but each CI is minutes, so a lost CI is bounded.)

**Audit backlog** — full-corpus single-process surfaces to bring into
compliance (tracked in #133; see that issue for the full 2026-06-14 audit):

- `validation_harness` metrics/bootstrap phase — checkpoint shipped; **opt-in**,
  now with a discoverability warning. Open question: make `--metrics-cache` (and
  the scoring cache) default-on rather than opt-in.
- single-process `check_corpus` / `corpus_hygiene` at corpus scale — sharded
  parity exists via `shard_runner`, but the default path is still uncheckpointed.
- the standalone `calibration_survey.py` CLI (the bake-off driver) — corpus
  scoring is cached, but the survey wrapper has no per-signal checkpoint.
- when the `train_edit_magnitude` fine-tune loop is wired (currently a stub), it
  must ship epoch-checkpoint + `--resume` from the start (GPU, hours).
- any other surface that loads a full corpus into one process.

## Acquisition scripts (`acquire_*.py`)

The impostor-corpus acquirers share `acquisition_core.py` and the
`acquire_corpus_template.py` shape (`discover_items` + `extract_one`; the rest
of the pipeline is shared). The full build/test guide lives in
`references/acquire-corpus-pattern.md`. Two hard-won conventions:

- **A zero-output run must fail.** When a run acquires nothing, exit non-zero
  unless it's a dedupe-only rerun (a `duplicate-hash` skip was seen) or
  `--allow-empty` was passed — a non-empty `skip_log` (everything filtered /
  no-text / below-min-words) must not mask a misconfigured source, filter,
  selector, or `--prefix`. (Codex review of #180.)
- **An API key never enters a stored locator.** Keep the manifest `source_url` /
  `ItemMeta.locator` clean — add a key only at the fetch boundary (`extract_one`),
  or pass it via an auth header, never embed it in a URL that gets persisted.
  (Build-review catch on the keyed acquirers — GovInfo / CORE / CourtListener / PTAB.)

## Keeping docs current (the docs-freshness step)

Shipping or changing a capability is not done until its paper trail moves with
it. These travel together, and `tools/check_docs_freshness.py` gates the pair of
them in CI:

- **`capabilities.d/`** — add/update the `<id>.yaml` fragment (one capability per
  file; the drift linter `tools/check_capabilities_drift.py` enforces the
  surface/script match). Never edit a shared manifest — fragments don't collide (#170).
- **Task-surface label** — if the work adds a new `TASK_SURFACE`, register it by
  dropping `scripts/claim_license_surfaces/<surface>.txt` (filename = key,
  contents = label). Never edit a shared surface dict/list: `TASK_SURFACE_LABELS`
  and `output_schema.VALID_TASK_SURFACES` both derive from that fragment dir, so a
  fragment is the whole change — and parallel audit PRs can't collide on it (#170).
- **`changelog.d/<slug>.md`** — drop a changelog fragment referencing the
  capability `id` (a `### Added/Changed/Fixed` header + prose; never edit a shared
  `## Unreleased` block). The freshness gate counts the `id` across `CHANGELOG.md`
  *and* these fragments; `tools/assemble_changelog.py` cuts them into a version
  section at release (#170).
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
- **Version + changelog are cut at release, not pinned in the PR.** Open PRs
  merge in an unknown order, so a `plugin.json` version pinned in a feature
  branch collides or goes stale. A PR ships a `changelog.d/<slug>.md` fragment
  naming its change (and bump *class*: `feat` → MINOR, `fix`/`docs`/`chore` →
  PATCH) but does **not** pin the number or edit `plugin.json`. At release, run
  `python3 tools/assemble_changelog.py --version X.Y.Z --date YYYY-MM-DD` to cut
  the accumulated fragments into a `## [X.Y.Z]` section, set `plugin.json`, then
  tag from `main`. This is the existing accumulate-then-cut "consolidated
  release" practice (see `## [1.111.0]`), now scripted and conflict-free.
- **Auto-merge on dual agreement.** When both reviewing agents (Claude
  and Codex) agree a PR is ready — CI green and review threads resolved —
  merge it (merge commit) without waiting for a further human prompt. The
  maintainer gave standing approval for this case (2026-06-06). If only one
  agent has reviewed, or a review comment is unresolved, hold for the
  second opinion rather than self-merging.
- **`gh` OAuth workflow-scope merge block (public repo).** A PR that touches
  `.github/workflows/` can't be merged with the `gh` OAuth token (403 "refusing to
  allow an OAuth App to create or update workflow"). The *git* credential keeps the
  scope (it pushed the branch fine), so the fallback is a local
  `git merge --no-ff origin/<branch>` into a `main` worktree →
  `git push origin HEAD:main` (needs explicit OK for the direct-to-main push), or
  merge via the GitHub web UI. PRs that don't touch workflows merge via `gh` fine.

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
the right version. Pushing a `v*` tag also auto-publishes a GitHub
**Release** for it (`.github/workflows/release.yml`) — the consumer
weekly-sync workflows resolve `latest` via that Release object, so a tag
without a Release means their auto-bump silently no-ops.

Full cross-repo release sequence (this tag → apodictic + voicewright
re-pin → apodictic release → APODICTIC-Gemini re-pin), with exact
commands and the known gotchas: `references/fleet-release-runbook.md`.

## When this document is wrong

Update it. It's a working document, not a contract. The goal is for
any future agent (Claude session, Codex session, future maintainer)
to read this file and know what shape the work should take.
