## Summary

<!--
Briefly name what changed. For paired-release work, cite the R-number
and the new tools/guardrails. For reviewer-P2 patches, name the
reviewer's findings being addressed.
-->

## Why

<!--
What problem this solves, what failure mode it prevents, or what
roadmap item it advances. For R-releases, reference the ROADMAP.md
paired-release rationale. For fixes, name the reviewer's reproduction
or the user-visible bug.
-->

## Validation

<!--
Proof of correctness a reviewer can read against the diff:
- `python3 -m pytest plugins/setec-voiceprint/scripts/tests/ -q` →
  N passed + 1 skipped
- `py_compile` clean on new scripts (if any)
- `git diff --check` clean
- Any reviewer-reproduction tests added under
  TestNameRegression / TestNameReviewerReproduction
-->

## Delivery state

<!--
Default: keep this constituent draft and CI-unarmed after local validation and
exact-head independent review. Record its exact remote head when admitted to a
fresh train/<date> branch. Do not make a constituent ready or merge it separately.

For the train PR, replace this comment with: Included / Explicitly excluded /
exact base + head / closed setec-merge-train/1 inventory / integration resolutions /
local and independent-review receipts / promotion and exact-base landing plan.

Only an explicitly authorized urgent standalone may be made ready with the exact
ci-ready label. Before any workflow-policy push, run the local policy mutation
suite; draft status is not a hostile-workflow security boundary.
-->

## Docs freshness

<!--
If this PR adds or changes a capability, confirm the paper trail moved with it
(see AGENTS.md → "Keeping docs current"). Delete this section for non-capability PRs.
-->

- [ ] `capabilities.d/<id>.yaml` fragment added/updated (one capability per file; drift linter clean)
- [ ] New task surface → `scripts/claim_license_surfaces/<surface>.txt` fragment added (never edit a shared surface dict/list)
- [ ] `changelog.d/<slug>.md` fragment added referencing the capability `id` (never edit a shared `## Unreleased` block)
- [ ] `tools/gen_calibration_readiness.py` re-run + committed (matrix fresh)
- [ ] `ROADMAP.md` status-reconciliation updated if shipped/left changed
- [ ] `tools/check_docs_freshness.py` passes

<!--
See AGENTS.md for the full workflow and conventions.
-->
