### Changed

**`register_composition_sweep` declares its real required inputs.** The
capability fragment carried only `inputs.target`, so
`tools/gen_calibration_readiness.py` fell back to its canned
"labeled human/AI corpus" phrasing and the generated readiness matrix claimed
H2 needs labels. It does not: the sweep's own posture is that its
same/different/unresolved buckets are counts, "not truth labels". The fragment
now lists the three things an operator actually supplies — an explicitly scoped
corpus JSONL with no truth labels, the committed H1 closeout receipt, and the
receipt-bound classifier source — and `references/calibration-readiness.md` was
regenerated from it. Metadata correction only: no behavior, schema, output, or
CLI change, and the generator is untouched (its canned string is still correct
for `validation_harness`). Recorded alongside the spec-73 status/amendment
reconciliation for the landed H2 implementation (PR #361).
