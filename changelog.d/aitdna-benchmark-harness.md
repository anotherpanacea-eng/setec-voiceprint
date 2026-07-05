### Added

**AITDNA external-validation benchmark harness (`aitdna_benchmark.py` +
`aitdna_to_manifest.py` + `fetch_aitdna.py`).** A held-out
external-validation eval harness — a sibling of `pan_voight_kampff_benchmark.py`
— that scores voiceprint's **existing** detectors against **AITDNA**
(*'Your AI Text is not Mine': Redefining and Evaluating AI-generated Text
Detection under Realistic Assumptions*; Dycke, Sakharova, Daheim, Gurevych —
**arXiv:2606.04906**; HF `datasets/UKPLab/AITDNA`, **CC-BY-SA-4.0**), a public
benchmark of realistic human-AI **co-written** text (the case generic
document-level detectors handle worst). Like the PAN harness this is NOT a new
detection surface, ships NO `capabilities.d/` entry / `_golden_capabilities/`
fragment / `claim_license_surfaces/` file, and **writes only a report** —
external validation, never a tuning/calibration/selection target.

The adapter computes the per-notion GOLD label from AITDNA's own genesis
annotations (per-token `User`/`Bot` provenance) under **declared, fixed,
never-swept** constants (document-level τ=0.5; co-written = ≥1 human AND ≥1 AI
token; membership n=4-gram / p=5th-percentile) — labels flow one way. The
report carries the honest-gap `notion_coverage` block (per notion:
addressed / partial / not_applicable — no fabricated per-notion F1 on the
boundary/sentence/intent/content notions voiceprint never claimed), a
first-class **co-written-human FPR** cell (the AITDNA-foregrounded hard case),
the fixed `reference_provenance` block (AITDNA's own published human-only
subset, pre-specified so it can't be retro-tuned), and an `anti_goodhart`
block. M1 wires three CPU-clean detectors — `membership_novelty`
(originality_audit DJ-Search coverage vs the human-only reference, the
membership-based notion), `binoculars_audit`, and `length_ratio_standin`; the
model-tier discrimination surfaces and the `voice_verifier` authorship-ID
surface are a named out-of-M1 seam. Guarded by `test_notion_parameters_fixed`
(peer of PAN's `test_no_aggregate_score`). Ships a synthetic fixture (M1); the
real-dataset run over the fetched AITDNA release is M2/deferred.
