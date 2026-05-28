# Replication spec: Russell et al. 2026 / StoryScope full pipeline

**Status:** roadmap. v0.1 (2026-05-28). Companion to
`narrative-decision-audit-spec.md` (the single-doc Surface 6 audit).

This document specifies what a full replication of Russell et al. 2026
*StoryScope* (arXiv:2604.03136v4) looks like inside the SETEC framework,
and which stages this repo wires end-to-end versus which it stubs
with the existing judge interface for operator-supplied API access.

The full replication produced 61,608 stories (10,272 prompts × 6
sources), 304 narrative features, and an XGBoost classifier reaching
93.2% macro-F1 (narrative-only) / 96.0% macro-F1 (narrative+style)
at a paper-reported total cost of ~$4,400 USD. Replicating it from
scratch is a multi-week, four-figure-budget engagement; replicating
the *evaluation* (skipping the corpus-construction and
feature-induction stages) against the paper's released artifacts is
much cheaper and is the recommended first pass.

## Three replication levels

| Level | What you replicate | Inputs | Cost | When to choose this |
|---|---|---|---|---|
| **L1 — audit-only** | Apply the paper's released 30 core features to your own prose. Use the SETEC Surface 6 audit; no new corpus, no new training. | A prose target + a judge model. | $0.05–0.30 per doc. | Single-doc spot-checks; integration with the rest of the framework. |
| **L2 — evaluation replication** | Re-run XGBoost + SHAP + LDA + rarity over the paper's released feature manifests (or your own labeled corpus with the paper's 30 features applied). Produces the paper's Tables 2, 4, 5, 6, Figures 2–4. | The paper's `parallel_features.jsonl` (or operator-built equivalent). | $0 if reusing paper artifacts; ~$1.5k if generating a new 6-source × 1k-prompt corpus first. | Sanity-check the paper's numbers on the operator's corpus; per-corpus calibration. |
| **L3 — full induction replication** | Re-run the StoryScope pipeline end-to-end: corpus construction (10k+ stories × 6 sources), templating, comparative analysis, feature discovery + dedup, per-story feature assignment, then the L2 analytics. Produces a brand-new 304-feature taxonomy that may or may not converge to the paper's. | A source corpus of human stories (e.g., Books3 derivatives) + the 5 frontier LLMs + GPT-5.1 for the StoryScope pipeline + Gemini 3 Flash for assignment. | Paper-reported $4.4k. | New research; testing whether the induction is stable across runs / across corpora. |

The SETEC implementation prioritizes L1 + L2 because they're the
operator-actionable forms. L3 is wired as a scaffold with checkpointed
stages; the LLM-driven steps run through the judge interface so
operators bring their own model + budget.

## Pipeline stages

The paper's pipeline maps onto eight stages. Stage prefix letters
match the audit-spec and the orchestrator's `--stage` flag.

```
A1  prompt_extraction      Human stories → writing prompts          (LLM)
A2  story_generation       Prompts × 5 LLMs → mirrored stories      (LLM)
B1  templating             Story → NarraBench JSON template         (LLM)
B2  comparative_analysis   600-story discovery pool → comparisons   (LLM)
B3  feature_discovery      Comparisons → 408 candidate features     (LLM)
B4  feature_dedup          408 → 304 features via embedding cluster (pure Python)
B5  feature_assignment     304 features × 61,608 stories → values   (LLM)
C   analytics              XGBoost + SHAP + LDA + rarity + audits   (pure Python)
```

Stage C is further decomposed in the analytics layer:

```
C1  train_binary           Binary (human vs AI) XGBoost
C2  train_multiclass       6-way (human + 5 LLMs) XGBoost
C3  shap_analysis          SHAP importance + bootstrap stability
C4  lda_projection         6-way LDA + confusion matrix
C5  rarity_analysis        Per-story k-NN rarity percentiles
C6  length_confound        Length-matched test subset audit
C7  memorization_audit     n-gram-overlap contamination audit
C8  polarity_check         Cross-corpus signal-polarity audit
                           (Surface 6 calibration; see Surface 6 spec)
```

Stages A1, A2, B1, B2, B3, B5 are LLM-driven and ship as stubs in
`scripts/replication/stages/`. They call the SETEC judge interface
(`narrative_judge.py`) extended with a `prompt_kind` argument so
operators can route each stage to whichever model + temperature
they prefer.

Stage B4 (embedding-based dedup) is pure Python and wired with the
existing embedding backend (`embedding_backend.py`). Default model
is sentence-transformers' `mxbai-embed-large-v1` (the framework's
canonical embedding); operators wanting the paper's F2LLM-4B can
swap in via `--embedding-model`.

Stage C is fully wired in pure Python and exercised by tests.

## Stage details

### A1: prompt extraction

**Goal.** Reverse-engineer a writing prompt from each human story
so each source generates from the same starting point.

**Paper's model.** Gemini 2.5 Flash, June 2025.

**Paper's prompt.** Published at
`prompts_display/prompt_generation.md` in the paper's TeX source;
reproduced verbatim in `references/storyscope-prompts/prompt_generation.md`
in this repo for offline replication.

**Inputs.** One human story per call.

**Outputs.** A single writing prompt (≤120 words, third-person
imperative, name-bearing) per story. Recommended JSONL shape:

```json
{"prompt_id": "...", "source_story_id": "...", "prompt_text": "...",
 "judge_identity": {...}}
```

**Cost.** Linear in number of stories. Gemini 2.5 Flash at
June-2025 prices: ~$0.10 per 1k input tokens × ~5k tokens per
story → ~$0.50 per story → ~$5,000 for the full 10,272-story
corpus. Cheaper if the operator chooses a smaller model.

**Wiring.** `scripts/replication/stages/a1_prompt_extraction.py`.
Uses the existing judge interface with `prompt_kind="prompt_extraction"`.

### A2: story generation from 5 LLMs

**Goal.** Generate a parallel story from each of the 5 AI sources
for each prompt.

**Paper's models.** Gemini 3 Flash, GPT-5.4, Claude Sonnet 4.6,
DeepSeek V3.2, Kimi K2.5.

**Paper's prompt.** Each prompt is given to all 6 sources verbatim
with a "approximately N words" suffix; representative example at
`prompts_display/story_generation_example.md`, reproduced at
`references/storyscope-prompts/story_generation_example.md`.

**Inputs.** Prompts manifest from A1.

**Outputs.** JSONL of one story per (prompt × model) pair:

```json
{"prompt_id": "...", "model": "...", "story_text": "...",
 "judge_identity": {...}, "stop_reason": "..."}
```

**Cost.** Paper-reported $2,800 total across all 5 models for
10,272 prompts. Length-aware: the long-context models (Claude,
GPT) cost more per story than the shorter-output models (Gemini
3 Flash, DeepSeek, Kimi).

**Wiring.** `scripts/replication/stages/a2_story_generation.py`.
Supports `--models` for partial multi-class runs (e.g.,
`--models claude_sonnet_4_6,gpt_5_4` for a 3-source replication
that drops the lower-budget AIs).

### B1: NarraBench templating

**Goal.** Per-story structured JSON representation grounded in
NarraBench's 10 dimensions.

**Paper's model.** GPT-5.1, zero-shot, default reasoning.

**Paper's prompt.** Published at `prompts_display/template.md`;
reproduced at `references/storyscope-prompts/template.md`.

**Inputs.** Story manifest from A2 + the human-story manifest.

**Outputs.** JSONL of one structured template per story (the
paper's "story → JSON template" stage). Schema enumerated in the
prompt itself; expect ~5–8k tokens per template.

**Cost.** GPT-5.1 over 61,608 stories at long-context rates: this
is the most expensive stage. Operators should checkpoint per-prompt
batches and resume on failure.

**Wiring.** `scripts/replication/stages/b1_templating.py`.

### B2: comparative analysis on the discovery pool

**Goal.** For each of 100 randomly-selected prompts (= 600 stories
across 6 sources), produce a cross-source structured comparison.

**Paper's model.** GPT-5.1, high reasoning effort.

**Paper's prompt.** Not in `prompts_display/`; described in §2.1
of the paper. The recommended approach is to reuse the paper's
`prompts/comparative.md` from the released `storyscope` repo at
github.com/jenna-russell/storyscope (referenced in the paper's
Code-and-data footnote).

**Inputs.** Discovery-pool subset of B1 templates (600 templates,
batched at mean 3.1 prompts/batch).

**Outputs.** JSONL of comparative-analysis JSON per batch:
per-source dimension notes, cross-source comparisons, executive
summary.

**Cost.** Bounded — only 600 stories × ~30 batches at high
reasoning. Order-of-magnitude estimate: $200–500 depending on
batch token shape.

**Wiring.** `scripts/replication/stages/b2_comparative_analysis.py`.

### B3: feature discovery

**Goal.** From the comparative analyses, propose discriminative
features within each NarraBench dimension. Run 3× and take the
union; expect ~408 candidates.

**Paper's model.** GPT-5.1 with 10 specialized aspect prompts (one
per NarraBench dimension).

**Paper's prompts.** 10 expert prompts. Not in `prompts_display/`;
the paper's released repo carries them. The recommended approach
is to use the paper's `prompts/discovery_*.md` set; for
operators replicating offline, the SETEC repo provides
`references/storyscope-prompts/discovery_template.md` as a
starting harness with per-dimension placeholders for the
operator to fill in from the paper's appendix or released code.

**Outputs.** JSONL of feature proposals: `{feature_id, name,
question, options, dimension, response_type, justification}`. The
paper reports ~408 raw candidates before dedup.

**Cost.** ~$100–300 for three passes across the comparative-analysis
batches.

**Wiring.** `scripts/replication/stages/b3_feature_discovery.py`.

### B4: feature deduplication (pure Python)

**Goal.** Cluster the ~408 raw candidates by embedding similarity
and keep the cluster centroid as the canonical feature. Target
size: 304.

**Paper's method.** Each feature's `name + question + detection
method` text encoded via F2LLM-4B; single-linkage clustering at
cosine threshold 0.85.

**SETEC method.** Same clustering math; the default embedding
backend is `mxbai-embed-large-v1` (already in the framework via
`embedding_backend.py`). Operators wanting the paper's F2LLM-4B
can pass `--embedding-model f2llm-4b` after installing the model
locally.

**Outputs.** Deduplicated feature manifest with `cluster_id` and
`source_proposals` lineage.

**Cost.** Negligible (offline embedding + clustering).

**Wiring.** `scripts/replication/feature_dedup.py`. Fully wired
and unit-tested.

### B5: per-story feature assignment

**Goal.** For each of 61,608 stories, assign a value to each of
the 304 deduplicated features.

**Paper's model.** Gemini 3 Flash with minimal thinking
(repeatability α = 0.88; human–model κ = 0.84).

**Paper's prompt design.** 10 specialized aspect prompts (one per
NarraBench dimension), each carrying only the features in that
dimension. Aspect-based application achieved 95.4% coverage vs.
68.4% for single-call application; mixed-thinking comparisons in
§2.2 informed the minimal-thinking choice.

**Outputs.** JSONL of per-story feature assignments:

```json
{"story_id": "...", "model": "...", "label": "...",
 "narrative_values": {feature_key: value, ...},
 "judge_identity": {...}}
```

This is the same format the SETEC Surface 6 polarity audit
consumes — making this stage's output directly usable by the
existing `narrative_polarity_audit.py`.

**Cost.** Paper-reported ~$1,600 USD for Gemini 3 Flash over the
full corpus + 304 features.

**Wiring.** `scripts/replication/stages/b5_feature_assignment.py`.
For operators who only want the paper's 30 core features (not the
full 304), the existing single-doc audit (`narrative_decision_audit.py`)
suffices and the B5 stage stub can be skipped.

### C: analytics layer (pure Python, fully wired)

All stage-C scripts consume the feature-assignment manifest format
(same shape as the polarity audit input) and emit standard SETEC
JSON envelopes against the `narrative_decision_audit` task surface
(or `calibration` for the threshold-deriving stages).

**C1 — binary classifier.** `scripts/replication/train_xgboost.py
--task binary`. XGBoost on encoded feature vectors; one-hot for
nominal, multi-hot for multi-select, integer for ordinal/scale.
Paper hyperparams (`n_est=420, depth=8, λ=2.0, pos_weight=5:1`)
are the documented defaults; operator can grid-search via
`--hyperparam-grid`. Splits use prompt-level grouping. Reports
macro-F1, AUPRC, prompt-bootstrap 95% CIs.

**C2 — multiclass classifier.** `scripts/replication/train_xgboost.py
--task multiclass`. Same script, different config: 6-way (human +
5 LLMs), uniform class weights, paper hyperparams `n_est=500,
depth=7, λ=1.0`. Reports macro-F1, per-class F1, accuracy.

**C3 — SHAP analysis.** `scripts/replication/shap_analysis.py`.
Per-feature SHAP importance + B=50 prompt-level bootstrap +
permutation-label null calibration. Assigns each feature to one
of three roles per the paper's criteria:

- **core** — binary-task feature with stable, important SHAP
  contribution, ≥ 0.20 H–AI mean gap, ≤ 0.35 cross-AI spread.
- **fingerprint** — multiclass-task feature with SHAP
  concentrated in a single source class.
- **noise** — neither.

The 30 paper-reported core features are validated against the
schema in `narrative_feature_schema.py`; the script flags any
divergence so operators see when their corpus disagrees with the
paper.

**C4 — LDA projection.** `scripts/replication/lda_projection.py`.
6-way LDA on encoded features → first two discriminant components.
Confusion matrix data for the matplotlib-side plot helpers.

**C5 — rarity analysis.** `scripts/replication/rarity_analysis.py`.
Per-story rarity percentile = mean Euclidean distance to k=25
nearest neighbors in the pooled z-scored encoded space. Reports
Cohen's *d* on the human/AI rarity-percentile distributions, plus
per-tail composition counts (top 1%, 5%, 10%).

**C6 — length-confound audit.** `scripts/replication/length_confound.py`.
Length-matched decile-stratified test subset; frozen-classifier
re-evaluation; length-only logistic baseline.

**C7 — memorization-risk audit.** `scripts/replication/memorization_audit.py`.
Per (human-story, AI-story) pair: exact 13-gram overlap + paired
8-gram coverage (paper's near-verbatim criterion). Filters
high-risk prompts so the operator can re-run the analytics on the
clean subset.

**C8 — polarity check.** Implemented in
`scripts/calibration/narrative_polarity_audit.py` (delivered with
the Surface 6 audit). Consumes the same B5-shaped manifest.

## Orchestration

`scripts/replication/pipeline.py` chains the stages with
operator-controlled granularity:

```bash
# Level 2 from an existing B5 manifest:
python3 pipeline.py \
    --feature-manifest path/to/features.jsonl \
    --feature-schema path/to/schema.json \
    --output-dir ./run-2026-05-28 \
    --stages C1,C2,C3,C4,C5,C6,C7

# Level 3 starting from human stories:
python3 pipeline.py \
    --human-corpus path/to/human-stories.jsonl \
    --judge-config path/to/judge-config.yaml \
    --output-dir ./run-2026-06-01 \
    --stages A1,A2,B1,B2,B3,B4,B5,C1,C2,C3,C4,C5,C6,C7
```

The orchestrator writes per-stage checkpoints under
`--output-dir/stage_<id>/` so failed runs resume from the last
completed stage. Each stage's manifest carries the SHA-256 of the
prompt + judge identity, so a checkpointed run knows when an
operator has changed the prompt or model and re-runs from that
stage forward.

## Checkpoint manifest format

Every stage emits a JSON sidecar `manifest.json` alongside its
output JSONL:

```json
{
  "stage": "B5",
  "tool": "scripts/replication/stages/b5_feature_assignment.py",
  "version": "0.1.0",
  "prompt_fingerprint_sha256": "...",
  "judge_identity": {
    "kind": "anthropic",
    "model": "claude-sonnet-4-6",
    "model_revision": null
  },
  "input_manifest_sha256": "...",
  "row_count": 61608,
  "completed_at_utc": "2026-06-01T18:42:11+00:00",
  "row_status": {
    "ok": 61584,
    "judge_error": 24,
    "validation_dropped": 0
  }
}
```

When the orchestrator runs a downstream stage, it reads the
upstream `manifest.json`, verifies `input_manifest_sha256`, and
fails fast if the input has changed. This makes the pipeline
behavior identical to the paper's: every analytic result is
deterministically traceable to a specific set of judge calls.

## What this spec does not commit to shipping in v0.1

- **Stage A1–A3 / B1–B3 / B5 prompt strings beyond what the
  paper publishes in `prompts_display/`.** The paper's comparative
  -analysis and feature-discovery prompts live in the released
  StoryScope GitHub repository. Operators replicating L3 should
  use the canonical prompts there; the SETEC repo provides
  placeholder harnesses so the stage scripts run end-to-end but
  does not redistribute the paper's full prompt set.
- **A copy of the paper's feature manifest.** L2 replication
  presumes the operator has obtained the paper's released
  manifests via the link in the paper's footnote. The SETEC repo
  does not vendor them.
- **A copy of any human or AI-generated story corpus.** Per the
  paper's footnote, the human stories are not redistributed for
  copyright reasons; the AI stories are released by the paper's
  authors. SETEC follows the same posture: no corpora vendored.

## Recommended L2 quick-start

The minimum-friction replication path:

1. Obtain the paper's released feature-values manifest (see paper
   footnote / their GitHub repo).
2. Convert to the SETEC manifest format (one JSONL row per
   story with `text_id`, `label`, `model_source`, and
   `narrative_values`).
3. Run `scripts/replication/pipeline.py --stages
   C1,C2,C3,C4,C5,C6,C7 --feature-manifest <path>`.
4. Compare the output JSON against the paper's reported numbers
   (Tables 2, 4, 5 + Figures 2, 3, 4). Divergences > 2 macro-F1
   points warrant investigation; either the manifest is differently
   filtered (e.g., post-memorization-removal vs. raw) or the
   XGBoost hyperparams differ.

The expected outcome on the paper's released manifest is binary
macro-F1 ≈ 93.2% on the narrative-only feature set; if the
operator gets within 1 point, the analytics layer reproduces the
paper. If not, the C3 SHAP analysis surfaces which features the
local XGBoost weighted differently, which is itself an interesting
diagnostic.
