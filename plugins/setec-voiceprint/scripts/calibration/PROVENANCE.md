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

1. Install calibration deps: `pip install -r requirements-calibration.txt` (only required for the HuggingFace fetch path; the GitHub path below is stdlib-only).
2. Fetch the corpus. **Two paths**, equivalent CC BY-NC-SA 4.0 license posture:
   - **GitHub (recommended for first-time runs).** No auth, no license-acceptance UI, no HF token. Stdlib only:
     ```
     python3 scripts/calibration/fetch_pangram_editlens_github.py \
         --split nonnative_english
     # or pin a specific upstream commit for reproducibility:
     python3 scripts/calibration/fetch_pangram_editlens_github.py \
         --split nonnative_english \
         --commit-sha <sha>
     ```
     The GitHub fetcher writes the same `NOTICE.md` license + provenance block as the HuggingFace fetcher and a `.fetch_record.json` containing the pinned commit SHA + per-file SHA-256 hashes for tamper detection.
   - **HuggingFace (license-card check, dataset-revision pin).** Requires `HF_TOKEN` and license acceptance at https://huggingface.co/datasets/pangram/editlens_iclr:
     ```
     python3 scripts/calibration/fetch_pangram_editlens.py \
         --split nonnative_english
     ```
     The HuggingFace path additionally verifies the dataset card declares CC BY-NC-SA 4.0 at fetch time. Use this path when the calibration run's provenance entry should reference an HF dataset revision rather than a GitHub commit.

   Both fetchers write to `ai-prose-baselines-private/editlens/` and both produce CSVs the next step's preset shapes consume. `calibrate_thresholds.py` reads `.fetch_record.json` and writes the corpus pin into the provenance entry's `corpus` field; both fetcher records use a `revision` key (the GitHub fetcher's commit SHA aliases as `revision` for HF-compat) so the downstream calibrator doesn't care which fetcher produced the file.
3. Convert to manifest: `python3 scripts/calibration/editlens_to_manifest.py --source <fetched-csv> --preset editlens_nonnative --out ai-prose-baselines-private/editlens/manifest_nonnative.jsonl --text-dir ai-prose-baselines-private/editlens/nonnative_text`
4. **Survey** every signal in `COMPRESSION_HEURISTICS` (all 11) before picking the first to encode (see "Selection criteria" below — do not assume any specific signal is the first to land). The survey wrapper handles the loop, the aggregation, and the gate evaluation:
   ```
   python3 scripts/calibration/calibration_survey.py \
       --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \
       --fpr-target 0.01 \
       --use validation \
       --out ai-prose-baselines-private/editlens/_survey_2026-XX-XX.json
   ```
   The wrapper runs every signal through `calibrate_thresholds.derive_threshold`, evaluates the four automatable selection-criteria gates (polarity, FPR resolution, TPR ≥ floor, calibrated-vs-heuristic aggressiveness), leaves Gate 2 (AUC/AP not embarrassing) for maintainer judgment, and prints a single ranked markdown table to stdout plus a JSON ledger to `--out`. The survey JSON is private (treat as scratch); only the first signal that earns provenance under the criteria below lands in the committed `thresholds_calibrated.json`.

   Three flags worth knowing about:

   - `--no-tier2` and `--no-tier3` skip the spaCy POS-bigram and SBERT/TF-IDF cohesion features for a faster Tier-1-only sweep. Useful as a cheap first pass while you decide which signals are worth the full compute.
   - `--signal <name>` restricts the survey to one signal. Useful for re-checking a specific calibration after the corpus or the registry changed.
   - `--max-entries N` caps the manifest entries scored per signal. Label-stratified sub-sampling, deterministic via the bootstrap seed (or `--max-entries-seed`). **Use this for pipeline checks before committing to a full calibration run.** A partial run verifies the toolchain works end-to-end (deps, SSL, spaCy model, manifest shape), surfaces any environment friction, and gives you a wall-clock estimate for the full run. Small-N runs will not pass the FPR-resolution and TPR-interpretability gates and the resulting threshold MUST NOT be committed to the ledger — the survey output marks `--max-entries` runs as `is_pipeline_check: true` and the inner `derive_threshold` tags the resulting provenance entry's `notes` with a "PIPELINE CHECK" prefix and a `sub_sample` block. Example partial-run sequence:
     ```
     # 10% pipeline check (~13 essays of the 130-essay ESL slice):
     python3 scripts/calibration/calibration_survey.py \
         --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \
         --fpr-target 0.01 \
         --max-entries 13 \
         --no-tier2 --no-tier3 \
         --out /tmp/_pipeline_check.json
     # If that runs cleanly, commit to the full run:
     python3 scripts/calibration/calibration_survey.py \
         --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \
         --fpr-target 0.01 \
         --out ai-prose-baselines-private/editlens/_survey_2026-XX-XX.json
     ```
5. Pick the first signal whose calibration entry passes the **Selection criteria**.
6. Edit `scripts/variance_audit.py`'s `COMPRESSION_HEURISTICS[<signal>]` to set `provenance=<slug>`, `provisional=False`, and `value=<derived>`.
7. Add a section to this file documenting the calibration run.
8. Commit the first calibrated threshold (small diff: registry + this file + ledger + CHANGELOG/version) + push.

## Selection criteria for a calibration entry

Pre-registered before any data was inspected. A signal earns its first committed `provenance` slug only when all five gates pass. A signal that fails any gate is documented as a calibration *finding* (recorded in the survey, not in the committed ledger), not a threshold to encode.

1. **Expected polarity matches.** Empirical AUC ≥ 0.5 in the registry's declared `direction`. If the registry says a signal is `lt` (compressed when low) but the calibration sweep finds the opposite direction discriminates better, the corpus's polarity inverts the registry's. That's a *finding* about the corpus or about the registry's polarity convention, not a threshold to commit.
2. **AUC / AP not embarrassing.** No fixed cutoff baked into the toolchain — left to maintainer judgment per signal. Low-discrimination signals (AUC ~0.55-0.65) become part of the visible record via the provenance entry rather than something the threshold value alone can hide. The bar a calibrated threshold should clear is "the empirical evidence in the entry would not embarrass a careful reviewer."
3. **Enough negative controls for the requested FPR.** The toolchain's `fpr_resolution = 1 / n_neg` check enforces the lower bound. The softer question this gate adds: *even if the FPR target is reachable, is the resulting TPR statistically interpretable?* Wide bootstrap CIs on TPR at the chosen threshold mean the operating point is noisy; commit anyway only with explicit acknowledgement in the entry's `notes` field.
4. **Interpretable threshold (not "predict almost nothing").** If the highest-TPR threshold within the FPR ceiling fires the signal on 1/130 positives, the threshold is technically valid but operationally meaningless. Look for thresholds with TPR substantially above zero at the chosen FPR target.
5. **ESL slice behaves conservatively.** When calibrating against `nonnative_english.csv` (the ESL slice), the calibrated threshold is implicitly tuned to spare ESL writers from false-positive labeling. If the threshold ends up *more aggressive* than the heuristic on this corpus, that is surprising — investigate before committing. The framework's ethical commitment is that ESL prose is not the failure mode the band classifier should aggressively flag.

## In-sample calibration

The empirical metrics in every committed provenance entry (AUC, AP, TPR / FPR / precision at the chosen threshold, bootstrap CIs) are computed on the same corpus the threshold was derived from. They are not heldout-test performance claims.

A heldout test split is roadmap. Until then, every committed threshold's evidentiary weight is:

> "This value separates the two classes on this fixture under this calibration method."

It is not:

> "This value generalizes to other corpora, registers, or AI-prose generations."

That distinction lives in three places to keep it from drifting:

- The `notes` field of every JSON ledger entry (`thresholds_calibrated.json`).
- The **Notes** bullet in every Markdown ledger entry (this file).
- The CHANGELOG entry's prose for every calibrated-threshold commit, until a heldout split lands.

When the heldout split lands, the seatbelt phrase changes from "in-sample" to "out-of-sample" and prior entries can be re-evaluated against held-out data and either confirmed (provenance gains a `heldout_validation` block) or flagged (entry annotated with the divergence).

## Calibration commit shape

Pre-registered. Each calibration commit should be a small reviewable diff covering exactly four artifacts:

- One `COMPRESSION_HEURISTICS` registry edit (`value` + `provenance` + `provisional` flipped together — the `ThresholdSpec` dataclass enforces the `provisional` / `provenance` mutex in `__post_init__`).
- One new section in this file using the **Template for new entries** below.
- One element appended to `scripts/calibration/thresholds_calibrated.json` (the calibrator does this automatically; review the diff).
- CHANGELOG entry + `plugin.json` version bump. PATCH if the calibration is documentation-shaped (no behavior change because the new value lands close to the old heuristic); MINOR if the band classifier's verdict will shift on borderline documents under realistic inputs.

The 9 corpus-independent regression tests in `scripts/tests/test_calibration_provenance.py` will catch any drift across the four artifacts before the commit can land. The 10th test (corpus-dependent re-derive) will additionally re-run calibration in environments where the private corpus is available.

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
