# 22-set-level-diversity

> Set-level diagnostics that no per-document surface can see: how much a target reuses
> material already in a reference pool (**originality**), and how tightly a *set* of texts
> clusters together (**homogeneity** — the "AI hivemind" / mode-collapse axis).

- **Status:** Ready — adversarially reviewed 2026-06-19 (P1 fixes applied: LUAR surface/privacy-gate
  naming, reconstructibility-not-"human" orientation; + status/test/paper-trail fixes). **M1 cleared to
  build; M2 POC-gated** (`BRAINSTORM.md` #3).
- **Tier:** near-term (M1 originality, stdlib) · research-grade (M2 homogeneity, embeddings + POC-gated)
- **GPU required:** no (M1 stdlib; M2 reuses the existing `voice_fingerprint` LUAR lens on CPU; an
  optional semantic lens is API, not local)
- **Upstream / prior art:**
  - Creativity Index / **DJ Search** — *AI as Humanity's Salieri* ([arXiv:2410.04265](https://arxiv.org/abs/2410.04265)).
  - **QUDsim** — discourse-structure reuse across LLM text ([arXiv:2504.09373](https://arxiv.org/abs/2504.09373)).
  - **Artificial Hivemind** — set-level LM homogeneity, avg-pairwise-cosine metric
    ([arXiv:2510.22954](https://arxiv.org/abs/2510.22954)); the fleet `BRAINSTORM.md` item #3.
- **License decision:** **clean-room the methods.** DJ-Search longest-n-gram matching and the
  avg-pairwise-cosine homogeneity metric are reimplemented from the papers (no weights). The local
  embedding lens **reuses SETEC's existing LUAR encoder** (`voice_fingerprint.py`, task surface
  `authorship_embedding` — already vendored, already license-cleared); the optional semantic lens is a
  remote API (`text-embedding-3-small`), off by default. **Privacy gate (inherited):** per-text LUAR
  embeddings are voiceprint-shaped, so M2's local lens **inherits the `authorship_embedding` privacy
  gate** — outputs default under the private baselines dir and refuse a public path without
  `--allow-public-output`, exactly like `voice_fingerprint` / `general_imposters`.

## Motivation

Every current SETEC surface scores **one document** against a baseline. Two questions it
structurally cannot answer:

1. **Originality (target vs a reference pool).** How much of a target is *reconstructible* from
   material the model has plausibly seen — i.e. recombined rather than novel? DJ-Search measures
   the fraction of a text covered by long verbatim/near-verbatim spans drawn from a reference
   corpus. **SETEC already owns the reference corpus** (the impostor pool / acquisition stack), so
   this runs stdlib against assets we have.
2. **Homogeneity (within a *set*).** LLM outputs cluster: a pool of responses collapses toward a
   shared centroid (the Hivemind finding: 79% of prompts average pairwise cosine > 0.8). Every
   per-doc surface is blind to this — it is a property of the **distribution**, not any one text.

**Orthogonality.** `variance_audit` measures *within-text* distributional spread; `voice_distance`
measures *one target vs one baseline*; `general_imposters` asks *which of K authors is closest*.
None measure **external reconstructibility** or **intra-set clustering**. This is a new axis (the
unit of analysis is a *set* / a *target-vs-pool*, not a doc-vs-baseline). Cite the brief: this is
the "set-level" gap `BRAINSTORM.md` #3 names.

Two near-neighbours to disambiguate explicitly (a reviewer will ask): `general_imposters` consumes
the **same** impostor pool, but answers *which author is nearest* (attribution) — M1 measures
*verbatim-span reconstructibility* over that asset, a different question. `formulaicity_audit` scores
generic-phrase density from a small built-in list — M1 is target-specific coverage by the operator's
**actual** reference corpus, not a cliché list.

## Method

Two scripts under one task surface, shipped as milestones.

### M1 — `originality_audit` (DJ-Search reconstructibility; stdlib)

Given a `--target` text and a `--reference-dir`/`--manifest` (default: the operator's impostor
pool), greedily cover the target with the **longest left-to-right n-gram matches** found anywhere
in the reference corpus (DJ Search). Report, at the value level:

- `coverage` (= `reconstructibility`) — fraction of target tokens inside a matched span of length ≥
  `--min-ngram` (default 8).
- `originality = 1 − coverage` — the headline scalar, oriented **gt** = *less reconstructible from the
  named pool*. **NOT "more human"**: a thin/narrow reference pool inflates apparent originality, and
  quotation / shared sources / genre formula deflate it. The orientation is reconstructibility, full
  stop — the human/AI axis is never asserted.
- `longest_match_tokens`, `n_matched_spans`, `matched_token_histogram` (span-length distribution).
- `attribution` — for the longest spans, which reference source they came from (auditable).

Pure stdlib: word tokenize + a suffix-automaton / rolling-hash index over the reference corpus
(built once, reused). Deterministic. No model. The reference corpus is the same asset
`general_imposters` / `voice_profile` already consume.

### M2 — `homogeneity_audit` (set-level clustering; embeddings, **POC-gated**)

Given **N texts** (`--manifest` of a response pool, or a `--dir`), embed each with a lens and
report the **distribution of pairwise cosine similarities** + an **effective number of modes**
(e.g. participation ratio of the Gram-matrix eigenvalues, or `exp(H)` over a similarity-graph
clustering). Also a single-doc mode: **centroid-proximity** = cosine of one target to a supplied
AI-typical centroid (distance-to-the-AI-centroid), oriented so *closer = more hivemind-like*.

- **Embedding lens (design call → default local).** `--lens local` uses the existing LUAR encoder
  (`voice_fingerprint.py`, surface `authorship_embedding`) — glass-box, on-box, no API,
  paper-non-comparable, and **subject to the inherited privacy gate** (private dir default; public
  path needs `--allow-public-output`); `--lens semantic` uses `text-embedding-3-small`
  (paper-comparable, remote/API, opt-in). Offer both; **default `local`** for the on-box/no-egress posture.
- **Single-doc centroid-proximity needs an operator-supplied centroid.** There is **no bundled
  AI-typical centroid** (a shipped default would smuggle in an implied verdict); `--centroid C` is
  operator-supplied, and absent it that mode is simply unavailable, not defaulted.
- Reuses `voice_fingerprint`'s embedding path; the metric (pairwise cosine, effective modes) is
  clean-room arithmetic on top.

**POC gate (per `BRAINSTORM.md` #3 — do not skip).** Before M2 is promoted past `heuristic`, a
Code-PC POC must show the **local/stylometric** pairwise cosine (a) reproduces the ~0.8 AI regime
and (b) separates a human response pool from an AI pool. If the local lens does not separate, M2
ships `--lens semantic`-only or stays `heuristic` with the null result logged — it does not get a
confident band on an unvalidated lens.

## Contract (the testable interface)

- **task_surface:** **new** — `set_level_diversity`. Register by dropping
  `scripts/claim_license_surfaces/set_level_diversity.txt` (label: "Set-level diversity &
  originality diagnostics (originality vs a reference pool; within-set homogeneity)"). Do **not**
  edit `VALID_TASK_SURFACES` / `TASK_SURFACE_LABELS` directly — they derive from the fragment dir.
- **CLI:**
  - `python3 plugins/setec-voiceprint/scripts/originality_audit.py --target T [--reference-dir D | --manifest M] [--min-ngram 8] [--json] [--out F]`
  - `python3 plugins/setec-voiceprint/scripts/homogeneity_audit.py [--manifest M | --dir D] [--lens local|semantic] [--target T --centroid C] [--allow-public-output] [--json] [--out F]`
- **JSON envelope:** via `output_schema.build_output()`; one `ClaimLicense` block. `results` keys
  enumerated per script above. M2 carries `lens`, `n_texts`, and (single-doc) `centroid_proximity`.
- **Claim license — licenses:** "reports the fraction of the target reconstructible from the named
  reference pool" (M1) / "reports the pairwise-similarity distribution + effective modes of the
  supplied set under the named lens" (M2). **Refuses:** any AI/human verdict; any "this is
  plagiarized/derivative" claim; any band that isn't operator-supplied/PROVISIONAL. Low originality
  ≠ AI (quotation, genre formula, shared sources); high homogeneity ≠ AI (a tight topical prompt).
- **capabilities.d fragments:** `originality_audit.yaml`, `homogeneity_audit.yaml` — `surface:
  set_level_diversity` (**one surface, two ids** — well-precedented; e.g. `voice_coherence` already
  backs many scripts, and the drift linter keys on `script_path`, not surface-uniqueness); `status:
  heuristic` for **both** (promotion to `empirically_oriented` requires the PROVENANCE calibration
  entry — never shipped oriented without it); `compute.tier: core` (M1) / `embedding` (M2);
  `length_floor_words` (M1 ≥ ~`min_ngram`×3; M2 per-text floor + a **set floor** of ≥ ~10 texts for a
  stable distribution); `dependencies.python_optional` for the semantic lens; `use_when` /
  `do_not_use_when` (M2 do-not-use: < the set floor; a single-source topical pool).
- **Dependencies / footprint:** M1 none (stdlib). M2 reuses the `voice_fingerprint` embedding tier
  (no new local dep); the semantic lens needs an API key + `openai` (python_optional, a
  `dependency_check.py` tier).
- **Surface-addition paper trail (AGENTS.md — travels with the build):** the two `capabilities.d/`
  fragments + `scripts/claim_license_surfaces/set_level_diversity.txt` + a `changelog.d/<slug>.md`
  fragment (referencing each `id`) + a `references/signals-glossary.md` entry + the dated `ROADMAP.md`
  status line + `tools/gen_calibration_readiness.py` refresh. Run `check_capabilities_drift.py` /
  `gen_calibration_readiness.py` / `check_docs_freshness.py` before push (CI gates them).

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_originality_audit.py` and `test_homogeneity_audit.py`:

- **deterministic-output** — same target + reference → identical `results`.
- **envelope-shape** — `build_output()` keys present; `results` carries the enumerated keys.
- **claim-license-present** + **refuses-verdict** — the `ClaimLicense` block is present and emits no
  AI/human label; a `--verdict`-style flag does not exist.
- **graceful-degradation** — M2 with the semantic lens but no API key/`openai` → `available:false`
  with `reason_category: missing_dependency` (fail loud, never a silent local fallback that changes
  the meaning); M1 with an empty reference pool → `available:false` `bad_input` (no division by zero).
- **set-floor abstention** — M2 with fewer than the set floor (~10) texts → `available:false`
  (`bad_input`): no pairwise-cosine distribution is shipped on too small a set.
- **privacy-gate** — M2 `--lens local` writing to a public path without `--allow-public-output` →
  refuses, mirroring `voice_fingerprint` / `general_imposters` (per-text embeddings are voiceprint-shaped).
- **numeric pins:** M1 — a target that is a verbatim copy of a reference doc → `coverage` ≈ 1.0,
  `originality` ≈ 0.0; a target sharing no ≥`min_ngram` span → `originality` ≈ 1.0. M2 — a set of
  near-identical texts → high mean pairwise cosine + ~1 effective mode; a set of unrelated texts →
  lower mean + more modes; `centroid_proximity` monotone in cosine.
- **ESL/dialect caveat surfaced** — M1 `assumptions`/claim-license notes that reconstructibility is
  corpus- and register-dependent (a thin/narrow reference pool inflates apparent originality), per
  the fairness posture.

## Calibration posture

Both ship **PROVISIONAL / uncalibrated** (no verdict, operator-side bands). M1 originality coverage
is a *measurement*; what calibrates it is a labeled human-vs-AI corpus over a fixed reference pool →
`empirically_oriented`. M2's AI-regime threshold (~0.8) and human/AI separation need **human response
pools** (Reddit/StackExchange/Quora threads via the acquisition stack; Infinity-Chat — 26K prompts +
model pools, public — to replicate the paper's centroid) → that POC + corpus is the path off
`heuristic`, recorded as a PROVENANCE entry. The default must not be a verdict.

## Out of scope / non-goals

- No plagiarism/derivative-work *determination* (originality is a measurement, not a legal claim).
- No covert watermark / reverse lookup (a different, gated axis).
- M2 does **not** ship a confident human/AI band on the local lens until the POC validates separation
  (gate above). The richer **QUDsim discourse-unit** homogeneity is a later refinement (M3), not M1/M2.
- Not a generation-side guard — the voicewright "pool-collapse guard / anti-centroid held-out term"
  is a *separate* consumer-side item (`BRAINSTORM.md` #3, voicewright), not this producer surface.

## Open questions

1. **Effective-modes estimator** — participation ratio (Gram eigenvalues) vs `exp(entropy)` over a
   similarity-graph clustering. Pick the one that is most stable at the ~10-text set floor.
2. ~~**One surface or two?**~~ **Resolved: one** (`set_level_diversity`, two ids) — precedented
   (`voice_coherence` backs many scripts; the drift linter keys on `script_path`, not surface-uniqueness).
3. **Default reference pool for M1** — the impostor pool as-is, or a register-matched subset? A
   register mismatch inflates originality; document the default and the `--reference-dir` override.
4. **Set floor for M2** — ~10 texts is a guess; the POC should report the minimum N for a stable
   pairwise-cosine distribution.
