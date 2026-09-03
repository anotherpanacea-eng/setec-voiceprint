# 81 — Draft-first integration trains

> Make the repo's existing draft-window practice the default delivery contract:
> independently reviewed draft constituents, one fresh exact-head integration
> train, one complete hosted run, and one merge to `main`.

- **Status:** Implementation review in progress on `codex/draft-first-integration-trains`
- **Tier:** repository process / CI policy
- **GPU required:** no
- **Upstream / prior art:** the repo's existing `AGENTS.md` Actions-conservation
  window; Voicewright's landed draft-first train contract in PR #339;
  Voiceprint train PR #415
- **License decision:** N/A

## Decision

Voiceprint will use unarmed constituent pull requests plus a fresh, frozen
integration-train pull request as its default delivery shape. A constituent
targets `main`, stays draft while work is active, and starts no hosted job. Near
the end of a work window, a `train/<YYYY-MM-DD>[-<slug>]` branch is created from
the exact current `origin/main`; independently cleared constituent heads are
merged into it with `--no-ff` in dependency order. Only that combined tree is
promoted and billed.

The train is not a long-lived staging branch. It is a disposable, auditable
snapshot of one delivery window. Reusing an old branch would blur its base,
constituent inventory, and review receipts; a stale or failed train is therefore
closed and rebuilt from a fresh base.

This is the same operating mode intended across the fleet, implemented here with
Voiceprint's own jobs and history. It does not introduce a shared cross-repo
branch: every repository builds and lands its own train in dependency order.

The policy does not depend on GitHub Pro, a ruleset, a merge queue, or a paid
current-base interlock. The current non-strict, administrator-bypassable classic
`pytest` protection supplies no constituent-merge interlock because an unarmed
job is reported as successful. A no-Pro landing is safe only through the live
read-back and exact-base leased merge protocol below.

## Current evidence and cost problem

At this spec's base, `.github/workflows/tests.yml`:

- subscribes to `opened`, `synchronize`, `reopened`, and `ready_for_review`;
- refuses every job while a PR is draft;
- arms all seven jobs merely by making any PR non-draft; and
- runs those seven jobs again on every push to `main`.

The draft half already conserves Actions minutes, but the last two points make a
normal constituent promotion expensive and duplicate the full suite after a
merge. They also permit an accidental draft-to-ready click to spend the complete
matrix before a combined train exists.

GitHub's documented behavior makes branch protection an incomplete replacement
for this operating contract: a job skipped by a job-level conditional reports
success, even when named as a required check, while a whole workflow skipped by
a HEAD commit instruction remains pending. Accordingly, neither a skipped
constituent job nor a paid ruleset is treated as admission or correctness
evidence. The pre-merge train receipts are the load-bearing evidence.

PR #415 is evidence that this repository can land a combined train, but not a
complete precedent for the new invariant: the new train must preserve every
advertised constituent head as an ancestor, bind every hosted job to the exact
synthetic merge it ran, and avoid a standing post-merge duplicate.

## Terms

- **Constituent:** one independently specified and reviewed PR targeting `main`.
  Its remote head freezes when admitted. Draft status is a cost state, not review
  evidence.
- **Train branch:** a fresh `train/<date>[-<slug>]` branch based on one recorded
  `origin/main` object and containing exact constituent heads.
- **Train PR:** the one PR that receives combined hosted clearance. It remains
  draft until its exact head is frozen, then is made ready once.
- **Armed PR:** a non-draft PR whose head branch starts with `train/` **and whose
  head repository is this repository**, or a non-draft standalone PR carrying
  the exact `ci-ready` label. A fork controls its own branch name and therefore
  cannot arm itself merely by choosing a `train/` prefix.
- **Standalone:** an urgent or genuinely indivisible change for which a
  one-member train adds no integration evidence. It requires explicit owner
  authorization and follows the same freeze, full-run, and landing protocol.
- **Receipt:** one canonical JSON line printed before substantive work by each
  billed job, binding the job and run to the synthetic merge, exact base, and
  exact PR head.

## Constituent admission

A constituent may be listed under **Included** only after all of these hold:

1. Its PR number, title, exact 40-hex remote head, dependency order, and local
   validation are recorded.
2. Non-trivial work has a written contract and independent spec review.
3. Independent generic and fleet-posture reviews of the exact constituent diff
   agree that every P1/P2 is fixed; any fix receives exact-head re-review.
4. Scope-appropriate local tests, repository gates, and `git diff --check` pass.
5. Long-running work satisfies this repository's recoverable, visible,
   continuable requirements; private-data and SETEC-held-out boundaries remain
   intact.
6. The branch has no unresolved review thread, known blocker, active owner
   mutation, or uncommitted integration fix.

The train body also carries **Explicitly excluded**. Parked, conflicting,
superseded, owner-active, or uncleared PRs are named there; silence cannot be
mistaken for admission.

## Train construction contract

1. Fetch, require a clean isolated worktree, and record the exact live
   `refs/remotes/origin/main` as `BASE`.
2. Create a new train branch from `BASE`.
3. Merge each admitted remote head with `--no-ff` in the recorded dependency
   order. Never cherry-pick, squash, or reconstruct an advertised constituent.
   Exact ancestry is what lets GitHub recognize an indirectly merged PR and lets
   the reviewer prove that the reviewed head actually landed.
4. Every constituent inventory step records the exact resulting merge commit and
   one of two tree modes. `clean` requires the merge commit's tree to equal Git's
   independently computed automatic merge tree for its exact two parents.
   `conflict-resolution` is permitted only when that automatic merge reports a
   conflict; it records a nonempty resolution description, and the final tree may
   differ from Git's conflicted automatic tree on exactly the paths Git reported
   conflicting—every non-conflict path must remain identical. The exact
   conflict-bearing merge commit and its tree receive local tests plus independent
   review. Git replacement refs and graft files are refused and all plumbing runs
   with replacement views disabled. This closes the otherwise-uninventoried
   merge-tree seam.
   One-parent train-only commits are reserved for separately inventoried
   post-merge integration or release adjustments, not for pretending a conflict
   was resolved outside its merge commit.
5. Validate the frozen train with a standard-library closed-topology checker.
   Its external JSON inventory uses schema `setec-merge-train/1`, records the
   exact base and ordered steps, and permits only:
   - an exact inventoried two-parent merge whose first parent is the prior train
     step, whose second parent is the next exact constituent head, and whose tree
     satisfies its recorded clean/conflict-resolution mode; or
   - an explicitly inventoried train-only direct commit.
6. Refuse an empty train; malformed, duplicate, all-zero, unresolved, or
   base-contained constituent IDs; a moved `origin/main`; missing/reordered
   constituents; extra first-parent commits; unlisted merge parents; octopus
   merges; non-ancestor endpoints; or a train HEAD carrying a GitHub Actions skip
   instruction.
7. Run the full local suite and gates appropriate to the combined diff. Record
   the known-on-base failures separately; a train may not create a new failure.
8. Open the train PR as draft, with Included, Explicitly excluded, exact
   base/head, closed construction inventory, integration fixes, local receipts,
   independent reviews, and the promotion/landing protocol.

Never remove a failed member with a revert: its head remains an ancestor and can
be reported as indirectly merged. Rebuild a new train without it.

## Workflow contract

`.github/workflows/tests.yml` becomes PR-only. The release workflow remains
tag-only and unchanged.

The test workflow subscribes to `opened`, `synchronize`, `reopened`,
`ready_for_review`, `converted_to_draft`, `labeled`, and `unlabeled`. Its
top-level `run-name` encodes only closed, machine-readable event metadata: PR
number, the GitHub-owned activity action, a Boolean same-repository-train class,
and a Boolean “changed label is exactly `ci-ready`” class. It never interpolates
raw branch, title, actor, or label text. The live receipt verifier parses this
exact bounded shape, allowing it to distinguish ignorable all-skipped label
noise from a relevant all-skipped revocation or clearance attempt without a
runner or an unreliable timeline inference.

The workflow's canonical clearance concurrency key is namespaced by workflow and PR number,
with `cancel-in-progress: true`. Concurrency classifies the PR's admission mode:

- every label event on a same-repository `train/` PR receives a unique,
  non-colliding run-ID suffix and cannot cancel or replace its clearance;
- on a non-train standalone, exact `ci-ready` add/remove events use the canonical
  group so removal revokes and cancels an in-flight clearance; and
- unrelated standalone label events receive the unique suffix, while every
  non-label event uses the canonical group.

Every one of the existing seven jobs carries the same job-level arming guard:

```text
non-draft AND (
  (
    same-repository train branch
    AND event is not labeled/unlabeled
  ) OR (
    not a same-repository train branch
    AND current labels contain ci-ready
    AND (
      event is not labeled/unlabeled
      OR event is labeled with exactly ci-ready
    )
  )
)
```

Thus all label edits on a train are unarmed/non-colliding. For a standalone,
only adding `ci-ready` can arm a label event; removing it is unarmed but collides
with the canonical group to revoke/cancel. Other unarmed or unrelated-label
events may create skipped records but start no hosted runner.
There is no billed sentinel, scheduled run, manual-dispatch lane, path/domain
split, or `push: main` test trigger. The seven existing platforms and commands
remain complete; this change consolidates when they run rather than reducing what
the one paid clearance covers.

Before dependency installation, each job runs the same standard-library
`tools/check_pr_merge_binding.py` against explicit environment values for
`github.event.pull_request.base.sha`, `github.event.pull_request.head.sha`, and
`GITHUB_SHA`. The verifier:

- requires a clean checkout and `HEAD == GITHUB_SHA`;
- parses the current commit object's parent headers without requiring shallow
  runners to download either parent object;
- requires exactly two parents in base/head order and exact 40-hex endpoints;
- emits exactly one `setec-pr-merge-binding/1` JSON receipt containing job, run,
  attempt, synthetic merge, base, and head; and
- fails closed on missing or malformed environment, a non-merge, octopus merge,
  wrong parent order, or endpoint disagreement.

The Ubuntu `pytest` checkout keeps its existing full history because the changed-
spec and packaging ratchets need it. The six focused macOS/Windows jobs remain
shallow; the receipt parser must not turn this policy into six unnecessary
history downloads.

A narrow workflow-policy regression suite is justified as a stable negative-
property gate. It parses a closed active workflow topology and rejects:

- any `.yml` or `.yaml` workflow other than the one PR-only test workflow and
  the separately specified tag-only release workflow; the unchanged release
  workflow's normalized content is itself pinned so matrix, permission, runner,
  step, or command growth cannot hide there;
- a `push`/schedule/dispatch test trigger or missing PR activity type;
- missing/malformed activity-class `run-name`, unbounded user-controlled text in
  it, or disagreement between its exact action/train/`ci-ready` Boolean fields
  and the event-derived guard/concurrency state table;
- a guard that omits draft, same-repository train, or explicit-label admission,
  including a simulated fork-owned `train/` head; every label event on a train
  remains unarmed, and only an exact `ci-ready` add can arm a non-train label
  event;
- any hidden/eighth job, deleted protected job, changed runner or increased
  timeout, job-level permission/error-policy addition, or non-PR concurrency;
  every job's allowed top-level keys are exactly `if`, `runs-on`,
  `timeout-minutes`, and `steps`, so `strategy`/matrix, `services`, `container`,
  reusable-workflow `uses`, and every other added job property fail;
- incorrect concurrency classification: train label events and unrelated
  standalone label events use a run-unique non-colliding group, while standalone
  `ci-ready` add/remove and every non-label event use the canonical PR group;
- a missing, conditional, reordered, substituted, or ignored binding step; work
  before binding; or post-binding work that can execute after binding failure;
  the existing `always()` consistency steps must also require the binding step's
  successful outcome; and
- any inserted step, appended/prepended command, removal, reordering, condition
  weakening, or inert `echo` substitution. Each protected job has a closed exact
  active step-header order and each action/run body has a closed normalized
  allowed command sequence; no additional action or command may hide inside one
  of the seven allowed lanes.

The verifier normalizes active YAML rather than snapshotting incidental comments
or whitespace. This maintenance cost protects the one full run on which every
landing depends and prevents either silent coverage loss or silent cost growth.

## Promotion and hosted clearance

Immediately before promotion:

1. Fetch and require live `origin/main == BASE`.
2. Read every constituent PR and the train PR from GitHub; require exact remote
   heads, target `main`, expected draft/disposition, and no unresolved thread.
3. Re-run the closed-topology check and `git diff --check`.
4. Obtain generic and fleet-posture reviews over the exact train base/head diff.
5. Make the frozen train ready once. A later head change invalidates the receipts:
   return to draft, re-review/retest the changed head, and promote again.

All seven jobs must complete successfully and non-skipped on the same synthetic
merge of the unchanged exact base and train head:

- `pytest`
- `macos-descriptor-confinement`
- `windows-descriptor-backend`
- `windows-owner-corrections`
- `windows-shingle-dedup`
- `windows-nonprose-sweep`
- `windows-private-writer-guards`

The landing read-back downloads each job log and requires one valid binding
receipt from every job. All seven receipts must share the same workflow `run_id`,
the same `run_attempt`, base, head, and synthetic merge. A partial job rerun is
not a complete clearance; after a transient failure the operator reruns the
entire workflow so the latest attempt contains all seven. The selected run must
be the newest **clearance** run for that head (a run with any expected job not
skipped), its attempt must be the latest attempt for that run, and there may be
no later failed, cancelled, or pending clearance occurrence of an expected job
on the head. An all-skipped train-label or unrelated-standalone-label event is
not a clearance run and does not invalidate one only when its API state is a
completed success and all exact seven job records are completed/skipped. Missing
job metadata, pending/cancelled/failed state, or any non-skipped work fails
closed. Current live base SHA/base ref/draft/head-repository/branch/label arming
state is separately mandatory, so converting to draft or removing `ci-ready`
from a standalone candidate still revokes clearance. Mixed-run or stale-attempt
greens are refused. A green head-attached check without its in-job receipt is
insufficient.

`tools/check_train_ci_receipts.py` makes that comparison mechanical. In live mode
it reads the PR, workflow run/attempt, jobs, conclusions, and job logs through the
authenticated GitHub CLI; its pure validation layer also accepts fixture evidence
for offline tests. Evidence selection is bound to the exact GitHub repository,
PR number, exact current `main` base SHA and base ref, `pull_request` event, workflow path
`.github/workflows/tests.yml`, train head SHA, newest run for that identity,
latest run attempt, exact current arming state, and the exact seven job IDs. It
parses the bounded activity class from the run's GitHub API `display_title`,
cross-checks the PR number and same-repository-train class against live PR
identity, and classifies an all-skipped train-label or unrelated-standalone-label
invocation as non-clearance evidence. It ignores that noise without weakening
current-state revocation. A missing, duplicate, impossible, or malformed class
fails closed. It requires one
canonical receipt per job, rejects duplicate/malformed receipt lines, and emits those identity
fields in one aggregate no-prose clearance receipt only after the exact-head run
satisfies the contract. A same-named workflow, different event, different PR, or
display-name collision cannot supply evidence. The tool performs no merge or
write.

## Landing without a paid ruleset

Immediately before landing, repeat the live base/head/constituent read-back and
require the seven exact-head checks and receipts to remain green.

When a reliable strict current-base protected merge button is available, Create
a merge commit may be used with an expected-head guard. Otherwise:

1. Fetch the tested synthetic merge commit from the PR ref.
2. Require its parents to be exact `BASE` then exact train `HEAD`.
3. Construct a local two-parent merge commit with the same tree; require the
   local and tested synthetic tree IDs to match.
4. Re-read live GitHub state once more.
5. Push the local merge to `main` with
   `--force-with-lease=refs/heads/main:<BASE>`.

This is a fast-forward update; the lease supplies compare-and-swap. A moved base
must reject the push and force a rebuild/retest. No ordinary direct push, squash,
rebase, or separate constituent merge is permitted.

After landing, prove that `main` contains the train head and every exact
constituent head. GitHub should mark unchanged constituents indirectly merged;
close only unchanged UI stragglers with a link to the train merge. A constituent
whose remote head moved is new work and remains open. Delete the remote train
branch and clean its worktree.

No standing post-merge hosted test is required. The one-time rollout's landed
tree removes `push: main`, so its landing commit also carries no skip instruction.
After landing, verify that no post-merge test run was created; a surprising run
is investigated rather than normalized with commit-message bypasses.

## Historical compatibility

Spec 73's H1 closeout is immutable historical evidence. Its recorded workflow
bytes, landed-main run, receipt, checker, and implementation digest are not
rewritten to resemble the new policy. The Spec 73 closeout checker must pass
before and after this build by resolving its pinned historical objects rather
than treating today's workflow as yesterday's artifact.

## First rollout

The first train should contain, in dependency-safe order:

1. PR #416 at its independently cleared exact head;
2. PR #417 at its independently cleared exact head; and
3. the draft PR implementing this spec at its independently cleared exact head.

The combined train receives the repository's seven hosted jobs once. The policy
PR changes the workflow that governs its own pull request, so its exact head must
pass the local workflow-policy mutation suite before publication; draft status
alone is not claimed as a hostile-workflow spending boundary.

The rollout also creates the repository label `ci-ready` and reads it back before
the exceptional standalone path is documented as available. Until that label
exists, the automatic same-repository train path is the only arming path.

## Build sequence

1. Reconcile `AGENTS.md`, `.github/pull_request_template.md`, `specs/README.md`,
   and one `changelog.d/` fragment with this policy and remove contradictory
   direct-landing/auto-merge instructions.
2. Add the standard-library closed-topology, shallow-safe merge-binding, and
   live receipt-aggregation tools. Their validation cores remain independently
   testable without GitHub or application imports.
3. Convert `tests.yml` to the fork-safe PR-only admission guard, namespaced
   cancellation, load-bearing binding steps, and unchanged seven-lane substantive
   matrix. Leave tag-only `release.yml` unchanged.
4. Add hostile-repository tests for topology/tree/lease behavior, merge-binding
   tests including a truly shallow object store, mixed-run/stale-attempt receipt
   tests, and closed workflow-policy mutations.
5. Run the unchanged historical Spec 73 gate without editing its spec, receipt,
   checker, constant, or allowlists; reproduce any base failure on the exact
   base. Run focused policy tests, the relevant repository gates, full feasible
   local suite, and `git diff --check`.
6. Create and read back the `ci-ready` label while every constituent remains
   draft/unarmed, then open this implementation PR as another draft constituent.

## Acceptance

1. Independent spec review verifies every cited workflow/job/history anchor and
   agrees the policy is ruleset-independent and preserves the complete matrix.
2. Closed-topology tests construct real repositories and accept exact no-ff
   constituent merges plus inventoried integration commits. They require clean
   automatic merge-tree equality or a genuinely conflicting, explicitly
   described and exact-commit conflict resolution whose changed paths equal the
   reported conflict paths; correctly parented commits with arbitrary extra tree
   edits fail. Replacement refs and graft views also fail. They reject every malformed,
   duplicated, reordered, missing, base-contained, moved-base, unlisted-step,
   unlisted-parent, and skip-instruction case above.
3. Merge-binding tests construct real repositories and reject dirty, non-merge,
   octopus, wrong-order, wrong-base/head, wrong-`GITHUB_SHA`, and malformed-env
   cases while succeeding from a shallow checkout that lacks parent objects.
4. Workflow mutation tests enforce the closed job and step sets, fork-safe
   admission, bounded runners/timeouts, load-bearing binding, exact substantive
   action/run bodies, and other stable negative properties in the Workflow
   contract without pinning comments or unrelated shell formatting. Inserted
   steps and commands appended/prepended to an allowed body fail. An unrelated
   standalone label event and every train label event neither arm a job nor
   collide with/cancel the canonical group. Exact standalone `ci-ready`
   add/remove events do use that group; only the add can arm. A matrix/job-key
   addition fails. Both `.yml` and `.yaml` workflow files are inventoried, and
   release-workflow matrix/step/command mutations fail. Activity-class run
   metadata is exact and excludes raw untrusted event text.
5. Receipt-aggregation tests reject mixed workflow runs, mixed or stale attempts,
   missing/duplicate jobs, later non-success occurrences, malformed/duplicate
   receipt lines, base/head/synthetic-merge disagreement, and wrong repository,
   PR, live base SHA/ref, event, workflow path, head identity, or activity class.
   They prove the class distinguishes verified completed/all-skipped
   train/unrelated label noise from failed, pending, or unproven label runs and
   relevant all-skipped clearance/revocation attempts.
6. A local landing test proves an unchanged base accepts an exact two-parent,
   tree-identical fast-forward and a concurrent fast-forward movement rejects the
   same update specifically because of the exact-base lease.
7. Spec 73's historical closeout checker is green without modifying its receipt,
   hash allowlist, checker, or historical workflow object.
8. Relevant focused tests, the full local suite/gates feasible on this host, and
   `git diff --check` pass; pre-existing failures are reproduced on the exact base.
9. `AGENTS.md`, the PR template, the specs index/status, and a changelog fragment
   reconcile the new default. Docs-only and tiny-fix bypasses are narrowed to
   draft constituents; auto-merge applies only to a frozen cleared train or an
   explicitly authorized standalone. Ordinary direct pushes to `main` are no
   longer authorized.
10. The `ci-ready` label is created and read back without arming a constituent.
11. Independent generic and fleet-posture implementation reviews approve the
   exact policy head and then the exact combined train head.
12. The first rollout train receives seven same-run, same-latest-attempt in-job
   receipts and seven
   successful non-skipped jobs once, lands with exact ancestry/tree/base
   guarantees, and creates no billed post-merge duplicate.

## Out of scope

- Purchasing or retaining GitHub Pro; a ruleset, merge queue, or organization
  policy.
- A permanent staging branch or one cross-repo mega-train.
- Automatically deciding which open PRs deserve admission.
- Reducing the seven-job Voiceprint matrix, adding domain ownership maps, or
  treating local tests as a substitute for the combined hosted run.
- Adding a new leak scanner in this public repo; private-data boundaries remain
  review/admission obligations and existing repository gates are preserved.
- Changing the tag-only release workflow or Spec 73's historical evidence.
