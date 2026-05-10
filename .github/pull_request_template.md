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

<!--
See AGENTS.md for the full workflow and conventions.
-->
