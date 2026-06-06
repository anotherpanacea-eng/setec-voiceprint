# 02-voice-fingerprint-embedding

> A same-author **style-embedding** verification surface: embed two (or N) passages
> into a learned voice manifold and report the cosine-similarity *distribution* — a
> holistic voice fingerprint that complements SETEC's scalar signals and refuses an
> identity/AI verdict.

- **Status:** Ready
- **Tier:** near-term
- **GPU required:** inference-optional (CPU-feasible for forensic batch sizes; <2 GB VRAM if available)
- **Upstream / prior art:** LUAR ([EMNLP 2021](https://aclanthology.org/2021.emnlp-main.70/), [LLNL/LUAR](https://github.com/LLNL/LUAR), weights `rrivera1849/LUAR-MUD`); Wegmann et al. 2022 "Same Author or Just Same Topic?" ([RepL4NLP](https://aclanthology.org/2022.repl4nlp-1.26/), `AnnaWegmann/Style-Embedding`).
- **License decision:** **wrap existing Apache-2.0 weights** (LUAR code+weights are Apache-2.0 — clean). Wegmann is the secondary cross-check; **pre-build: confirm the `AnnaWegmann/Style-Embedding` weight-card license tag** (brief TODO). If Wegmann's tag is non-permissive, ship LUAR-only and add Wegmann later. No clean-room modeling needed.

## Motivation

SETEC's voice-coherence surface (`voice_distance`, `idiolect_detector`) measures
distance in *interpretable scalar features* (function words, char n-grams, Burrows
Delta). Surface 5 measures *perplexity/surprisal*. Neither captures a **learned
holistic voice manifold** — the axis that authorship-verification SOTA (PAN, LUAR)
actually leans on. A style embedding adds that orthogonal axis: it answers "are these
passages stylistically consistent?" without per-deployment training and without a
threshold, which fits SETEC's no-verdict posture exactly. It also gives the framework
a content-controlled option (Wegmann) that directly addresses its deepest confound —
topic leaking into a "voice" judgment.

This is the brief's **#1 ranked addition** (orthogonal, cleanest license, low effort).

## Method

1. Load a frozen style encoder (LUAR by default; Wegmann optional via `--model`).
2. Window the input(s) into comparable units (paragraph or fixed-token, reusing the
   windowing already in `semantic_trajectory_audit.py`).
3. Embed each window → unit-normalized style vector.
4. Compute the **cosine-similarity distribution**:
   - *Single-document mode:* pairwise cosines across the document's own windows
     (internal voice consistency / drift).
   - *Two-corpus mode:* each target window vs. a baseline corpus's window centroid(s)
     → similarity distribution (same-author-consistency evidence).
   - *N-way mode:* target vs. candidate baseline vs. impostor pool → report where the
     target's similarity falls (pairs with `general_imposters.py`'s framing; **not** a
     GI replacement — this is the embedding analogue).
5. Report distribution statistics (mean, sd, min, quantiles), never a binary call.

Reused, not reimplemented: the encoder weights and the windowing helper. New code:
the surface contract, distance aggregation, and caveat plumbing.

## Contract (the testable interface)

- **task_surface:** new value `authorship_embedding` (register in
  `output_schema.VALID_TASK_SURFACES` and `claim_license.TASK_SURFACE_LABELS`). It is
  a sibling of `voice_coherence`, not a replacement.
- **CLI:**
  `python3 plugins/setec-voiceprint/scripts/voice_fingerprint.py TARGET [--baseline-dir DIR] [--impostor-dir DIR] [--model luar|wegmann] [--window-strategy paragraph|fixed-token] [--window-size N] [--device DEVICE] [--json] [--out PATH]`
- **JSON envelope:** `output_schema.build_output(task_surface="authorship_embedding", ...)`.
  Keys under `results`: `mode` (single|two_corpus|n_way), `model_id`, `n_windows`,
  `cosine_distribution` {mean, sd, min, p10, p50, p90}, `per_window` (optional series),
  and in n-way mode `target_vs_candidate`, `target_vs_impostors`. Carries a
  `ClaimLicense` block.
- **Claim license:** **licenses** "these passages are stylistically consistent /
  divergent at cosine distance D under model M's learned style manifold." **Refuses**
  "same person," "different author," and "AI/human." States the content-control caveat
  (LUAR is Reddit-trained → register skew; Wegmann captures mostly
  punctuation/casing/contraction per its own STEL analysis) and short-text fragility.
- **capabilities.yaml entry:** `id: voice_fingerprint`, `surface: authorship_embedding`,
  `status: empirically_oriented`, `handoff: experimental`,
  `compute: {tier: optional, cost_note: "loads a ~0.5–1.4 GB style encoder; CPU-feasible, GPU optional <2 GB VRAM", length_floor_words: 500}`,
  `dependencies.python: [transformers]` (+ `python_optional: [sentence_transformers]` for Wegmann),
  `use_when`/`do_not_use_when`/`inputs`/`references` filled.
- **Dependencies / footprint:** uses the existing `surprisal`/optional transformer
  stack; add a `style_embedding` note to `dependency_check.py` if the model download is
  distinct. Disk/VRAM line goes into the readiness matrix automatically via the manifest.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_voice_fingerprint.py`)

The build makes these pass (encoder stubbed where possible for determinism):
- `test_envelope_shape` — output validates against `output_schema`; `task_surface == "authorship_embedding"`.
- `test_claim_license_present_and_refuses_verdict` — claim-license block exists; rendered
  text contains no "is AI"/"same person" assertion; contains the content-control caveat.
- `test_cosine_distribution_keys` — `cosine_distribution` carries mean/sd/min/p10/p50/p90.
- `test_identical_text_high_similarity` — a document compared to itself yields cosine ≈ 1
  (stub encoder returns deterministic vectors).
- `test_two_corpus_mode_requires_baseline` — two-corpus mode errors clearly without `--baseline-dir`.
- `test_missing_transformers_graceful` — absent `transformers`, exits with the
  `dependency_check`-style install hint, not a traceback.
- `test_window_strategy_parity` — paragraph vs fixed-token produce the same envelope shape.
- `test_capabilities_entry_present` — `voice_fingerprint` is in `capabilities.yaml` and
  passes the drift linter.

## Calibration posture

Ships PROVISIONAL: cosine distances are absolute measurements; any "consistent/
divergent" banding is illustrative and named so in the claim-license. Calibration
(later) = per-register impostor-pool study giving an empirical same-author cosine
distribution → PROVENANCE entry → promote `status`. Pairs with the fiction impostor
pool already on the roadmap.

## Out of scope / non-goals

- No author identification, no "same person" claim, no AI verdict.
- Not a replacement for `general_imposters.py` (unsupervised GI stays the AV primitive);
  this is the learned-embedding complement.
- No fine-tuning in v1 — wrap frozen weights only.

## Open questions

- Default model: LUAR (clean license, social-media skew) vs. Wegmann (content-controlled,
  license-tag-pending). Recommend LUAR default, Wegmann opt-in once its tag is confirmed.
- Should n-way mode emit a GI-style proportion, or stay descriptive only? (Lean
  descriptive to avoid re-introducing a threshold.)
