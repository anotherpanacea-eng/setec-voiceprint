# 18-triage-agreement

> Measures the **agreement between the framework's surfaced candidates and an
> operator's triage judgments** on a labeled item set: confusion matrix, percent
> agreement, Cohen's κ, prevalence-and-bias-adjusted κ (PABAK), and a seeded
> bootstrap CI on κ. Closes the loop the framework keeps open on principle —
> "source triage is judgment work" — by giving that judgment an actual measured
> number, without ever asserting which side is right.

- **Status:** Ready → building (this group).
- **Tier:** Trustworthiness → "Validation upgrades" (ROADMAP → "Capability-whitespace additions (2026-06-07) → W3"). The shipped suite validates against labeled *corpora* (RAID/MAGE/EditLens) but never against *human raters*.
- **GPU required:** no — stdlib only (`json`, `csv`, `random`, `math`, `collections`).
- **License decision:** N/A — local code; Cohen's κ / PABAK are standard published statistics.

## Motivation

The framework's load-bearing epistemic claim is that *most surface flags resolve as
earned on triage, and triage is judgment work, not algorithm.* That claim is
currently untested: nothing records how often the framework's surfaced candidates
match an expert reader's call, and nothing reports the concordance as a number. An
inter-rater-agreement harness turns "the operator decides per instance" into a
per-corpus, auditable statistic — and, crucially, reports **PABAK** alongside κ
because κ is notoriously prevalence-sensitive (a high agreement rate can produce a
low κ when one category dominates, the "κ paradox").

**Orthogonality:** the shipped `validation_harness` / `voice_validation_harness`
compute FPR/TPR/ROC against *ground-truth corpus labels*; this measures
concordance against *human judgments* where there is no ground truth — a different
question. It is the human-in-the-loop validator the trustworthiness layer lacks.

## Method

Stdlib only, deterministic given a seed. Input is a JSONL (default) or CSV file,
one row per triaged item, each carrying a framework decision and a human decision
(any hashable category labels; the common case is binary `flag`/`clear` or
`earned`/`unearned`).

1. Parse rows; drop rows missing either key (counted + warned).
2. Confusion matrix over the union of observed categories (works for k≥2).
3. `percent_agreement` = (#rows where framework == human) / N.
4. `cohens_kappa` = (p_o − p_e) / (1 − p_e), p_e from the product of marginals.
5. `pabak` = (p_o − 1/k) / (1 − 1/k) for k categories (= 2·p_o − 1 when k = 2).
6. Bootstrap CI on κ: resample rows with replacement `--bootstrap` times under a
   fixed `--seed`, recompute κ each time, report the 2.5 / 97.5 percentiles.
   Degenerate resamples (single category) contribute κ = 0.0.

The framework / human label semantics are symmetric — this measures concordance,
not correctness.

## Contract (the testable interface)

- **task_surface:** existing `validation` (no new surface).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/triage_agreement.py LABELS[.jsonl|.csv] [--framework-key framework] [--human-key human] [--format jsonl|csv] [--bootstrap 2000] [--seed 0] [--json] [--out PATH]`.
- **JSON envelope:** `build_output(task_surface="validation", …)`, `target_path` =
  the labels file, `target_words` = 0 (non-prose input; item count lives in results).
  `results` keys: `n_items`, `n_dropped`, `categories`, `confusion`, `marginals`,
  `percent_agreement`, `cohens_kappa`, `pabak`, `bootstrap_n`, `kappa_ci95`
  (`[lo, hi]` or `null` when `--bootstrap 0`). Carries a `ClaimLicense`.
- **Claim license:** *licenses* "the measured concordance between framework-surfaced
  candidates and operator triage judgments on this item set (percent agreement,
  Cohen's κ, PABAK, bootstrap CI)"; *refuses* any inference that the framework or
  the human is correct (κ measures agreement, not ground truth), and any
  generalization beyond this item set. Caveats: κ is prevalence-sensitive (PABAK
  reported for that reason); the item set must be representative; small N → wide CI.
- **capabilities.yaml entry:** `id: triage_agreement`, `surface: validation`,
  `status: heuristic`, `handoff: experimental`, `consumers: []`,
  `family: validation`, `compute: {tier: core}`, `dependencies.python: []`,
  `inputs.target: "JSONL/CSV of per-item framework + human triage labels"`.
- **Availability:** fewer than 10 usable items → `available=False` + warning (κ is
  meaningless at very small N).

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_triage_agreement.py`)

- `test_task_surface_is_validation` — `TASK_SURFACE == "validation"`.
- `test_perfect_agreement_kappa_one` — identical framework/human columns → κ = 1.0, agreement = 1.0.
- `test_chance_agreement_kappa_near_zero` — independent labels → |κ| small.
- `test_kappa_paradox_pabak_reported` — high agreement + skewed prevalence → low κ but PABAK high; both present.
- `test_confusion_and_marginals` — confusion matrix + marginals match a hand-computed fixture.
- `test_bootstrap_ci_brackets_kappa` — CI is `[lo, hi]`, lo ≤ κ ≤ hi, deterministic under seed.
- `test_multicategory` — a 3-category fixture computes without error.
- `test_dropped_rows_counted` — rows missing a key are dropped + counted, not crashed on.
- `test_csv_input` — `--format csv` parses an equivalent CSV fixture.
- `test_too_few_items_unavailable` — < 10 items → `available=False`.
- `test_claim_license_refuses_ground_truth` — `does_not_license` names "correct"/"ground truth".
- `test_deterministic` — same seed → identical CI.

## Calibration posture

Nothing to calibrate — it reports descriptive agreement statistics. What it
*enables* is calibration discipline elsewhere: a low framework-vs-human κ on a
register is direct evidence that the framework's candidate-surfacing needs
operator-side review on that register before any evaluative use.

## Out of scope / non-goals

- Not a ground-truth oracle: it never decides who is right.
- Not multi-rater (Fleiss' κ across 3+ humans) in v1 — pairwise framework-vs-human only.
- Does not ingest audit envelopes directly; the operator supplies the paired labels
  (a future adapter could derive the framework column from a batch of envelopes).

## Open questions

- Whether to add Fleiss' κ / Krippendorff's α for multi-rater human panels later.
- Whether to ship an adapter that builds the `framework` column from a directory of
  `aic_pattern_audit` / `variance_audit` envelopes automatically.
