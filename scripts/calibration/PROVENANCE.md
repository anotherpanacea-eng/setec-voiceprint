# SETEC threshold calibration provenance

This ledger records every empirically-calibrated per-signal
threshold currently encoded in SETEC, with full provenance. Derived
values are abstract aggregate measurements (CC-NC corpora used for
derivation are not redistributed).

v1 covers per-signal thresholds only; band thresholds, directional-
cluster consistency, and POS-bigram smoothing α stay heuristic. See
`internal/SPEC_calibration_toolchain.md` (gitignored) for the
toolchain design and the calibration-vs-evaluation split policy.

Format: one section per threshold, in chronological order. New
entries land via PR. Slugs match the keys in
`scripts/calibration/thresholds_calibrated.json`.

## Status

**No thresholds calibrated yet** as of the toolchain release. All
11 signal thresholds in `COMPRESSION_HEURISTICS` carry
`provisional=True` and `provenance=None`. Variance audits report
"0 of 11 signal thresholds carry calibration provenance" in the
Calibration status footer.

To populate this ledger:

1. Install calibration deps: `pip install -r requirements-calibration.txt`
2. Fetch the corpus: `python3 scripts/calibration/fetch_pangram_editlens.py --split nonnative_english`
3. Convert to manifest: `python3 scripts/calibration/editlens_to_manifest.py --source <fetched-parquet> --preset editlens_nonnative --out ai-prose-baselines-private/editlens/manifest_nonnative.jsonl --text-dir ai-prose-baselines-private/editlens/nonnative_text`
4. Calibrate one signal: `python3 scripts/calibration/calibrate_thresholds.py --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl --use validation --signal burstiness_B --fpr-target 0.01`
5. Edit `scripts/variance_audit.py`'s `COMPRESSION_HEURISTICS["burstiness_B"]` to set `provenance=<slug>`, `provisional=False`, and `value=<derived>`.
6. Add a section to this file documenting the calibration run.
7. Commit + push as a new PATCH release.

## Calibrated thresholds

_(empty)_

## Template for new entries

When you populate a calibration run, format the entry like this:

```markdown
## <slug>

- **Signal:** `<heuristic_key>` (direction `<gt|lt>`)
- **Derived value:** `<float>`
- **Corpus:** `<corpus_name>` (HF revision `<sha>`)
- **License:** `<license>` (local-only use; not redistributed)
- **Calibration:** direction-aware FPR-target sweep at FPR ≤ `<target>`
- **Split role:** calibration_only (in-sample; heldout test split is roadmap)
- **FPR resolution:** `1/n_neg = <value>` (`<n_neg>` negatives)
- **Empirical:** AUC `<auc>`, AP `<ap>`, TPR `<tpr>` `[<lo>, <hi>]` at FPR `<fpr>`
- **CI method:** fixed-threshold paired bootstrap (`<resamples>` resamples, seed `<seed>`)
- **SETEC commit:** `<sha>`
- **Date:** `<iso>`
- **Notes:** `<context>`
```

Slugs follow the convention `<corpus>_<signal>_fpr<target>_<iso-date>`,
e.g. `editlens_nonnative_burstiness_B_fpr0.01_2026-05-08`. The
slug is the foreign key into
`scripts/calibration/thresholds_calibrated.json`; matching slugs
across the registry, the JSON ledger, and this Markdown ledger are
what `scripts/tests/test_calibration_provenance.py` enforces.

## Reading this ledger

- Every entry references the corpus by name + URL + revision SHA;
  no entry quotes corpus content or per-row reference-detector
  scores.
- The `derived_value` is the floating-point threshold encoded in
  `scripts/variance_audit.py`'s `COMPRESSION_HEURISTICS` registry.
- The empirical metrics (AUC, AP, TPR, FPR, precision, CIs) are
  in-sample on the corpus the threshold was derived from. Heldout
  performance is roadmap.
- The CI is fixed-threshold paired bootstrap on rate uncertainty;
  it does not capture the uncertainty in selecting the threshold
  itself (selection uncertainty is a nested bootstrap, roadmap).
- A threshold without a section here means it's still heuristic /
  provisional. Audit the variance audit JSON's `calibration_status`
  block to see which signals are which.
