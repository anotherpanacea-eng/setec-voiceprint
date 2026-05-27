# Binoculars Audit (Perplexity Ratio v1)

- **Target:** `Writing/stylometry sequence/magnifica-humanitas/texts/laudate-deum-en.txt` (6974 words)
- **Scorer:** `openai-community/gpt2` (rev `None`)
- **Observer:** `distilgpt2` (rev `None`)
- **Score version:** `perplexity_ratio_v1`

## Score

| | Log-perplexity (bits) | Series length |
|---|---:|---:|
| Scorer | 4.8063 | 8734 |
| Observer | 5.1603 | 8734 |

**Perplexity ratio (scorer/observer):** 0.9314
**Verdict band:** `uncalibrated` (thresholds: low=None, high=None)

## Caveats

- no_calibrated_thresholds_supplied

## Claim license

## What this result licenses

**Task surface:** Binoculars-style perplexity-ratio discrimination

**Reports:** Reports the perplexity ratio of the target text under a scorer language model relative to an observer language model. Lower scores indicate the target is more predictable under the scorer than the observer — a known signal for AI-generated text in the Hans et al. 2024 framework. The score is a numeric measurement against the chosen model pair; it is not a verdict.

**Does NOT report:** Does not license a binary AI/human authorship verdict. The score is one measurement against one model pair; operator judgment remains the load-bearing decision step. v1 ships without framework-calibrated thresholds: by default the verdict band is 'uncalibrated' and the audit reports the raw ratio only. Operators who supply --threshold-low / --threshold-high explicitly take responsibility for those thresholds being appropriate for their model pair, corpus, and register. Does not control for memorization (if the target is in the training set of either model, the score will be biased). Does not generalize across genres without operator-validated calibration. Is an approximation of the Hans et al. 2024 Binoculars score (this v1 uses the perplexity ratio baseline; v2 will upgrade to true cross-perplexity Binoculars once surprisal_backend supports distribution extraction). Does not substitute for stylometric, embedding-based, or other framework audits — it complements them.

### Comparison context

- **observer model:** distilgpt2
- **score version:** perplexity_ratio_v1
- **scorer model:** openai-community/gpt2
- **threshold high:** None
- **threshold low:** None

### Caveats

- no_calibrated_thresholds_supplied

### References

- Hans et al. 2024, 'Spotting LLMs With Binoculars: Zero-Shot Detection of Machine-Generated Text'

## Provenance

- **Tool:** `binoculars_audit` v0.1.0
- **Scorer identifier_block:** `{"id": "openai-community/gpt2", "revision": null, "alias": "gpt2", "deterministic_mode": true, "method": "transformers-causal-lm", "dtype_requested": "auto", "dtype_loaded": "fp32"}`
- **Observer identifier_block:** `{"id": "distilgpt2", "revision": null, "alias": null, "deterministic_mode": true, "method": "transformers-causal-lm", "dtype_requested": "auto", "dtype_loaded": "fp32"}`
