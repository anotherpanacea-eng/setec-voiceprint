# 82-verbatim-mosaic-audit

> Measure how SETEC's shipped signals respond to prose assembled mostly from
> verbatim human spans (Frankentext-style composition), and add a descriptive
> profile of a target's verbatim-span mosaic against a named reference pool:
> how many distinct sources its covered spans come from, and how the style
> shifts at the joins between them.

- **Status:** M1 build-contract after review; M2 gated on a separate replay
  protocol and operator authorization (2026-09-23).
- **Tier:** near-term for M1 (stdlib); M2 replay of model-backed signals on the
  local box.
- **GPU required:** no for M1; M2 re-scoring uses the existing model-backed
  surfaces and their existing tiers.
- **Upstream / prior art:** Frankentext, arXiv 2505.18128 (v4, April 2026;
  ACL 2026 long paper), code and outputs at github.com/chtmp223/frankentext; DJ-Search / Creativity
  Index, arXiv 2410.04265 (already clean-roomed here as `originality_audit`);
  PAN 2026 multi-author style analysis, arXiv 2602.09147 (already cited by
  `within_doc_segmentation`). Followups and detector responses are summarized
  in a survey kept in the project's shared folder, outside this repo; the
  papers this spec relies on are cited inline.
- **License decision:** clean-room composition over shipped repository code. No
  Frankentext code, output or Books3-derived text is vendored, used as a
  fixture or used for calibration: the released outputs are built from Books3,
  whose provenance this repo cannot accept. Fixtures are built from
  public-domain text only.

## Motivation

**The attack.** Frankentext hands a reasoning model a writing prompt, about
1,500 randomly sampled human paragraphs and an instruction that 90% of its
tokens be copied verbatim. The model selects, orders and lightly stitches the
paragraphs. Measured copy rates were 75% (Gemini 2.5 Pro) and 82% (GPT-5).
Pangram, a commercial detector, labelled 59% of the Gemini outputs and 79% of
the GPT-5 outputs human-written at 1,500 snippets, and 72% of Gemini's with
5,000 snippets; it flagged up to 37% as "mixed". Detection rises as the copy
rate falls (the survey flags one per-copy-ratio table in the final version
that needs checking against the ACL PDF). Binoculars and Fast-DetectGPT
flagged 0 to 1% as AI. Human annotators flagged about 56% as AI-involved,
citing abrupt tonal shifts (50%), disfluency (43%) and confusing passages
(40%), and longer texts were easier to spot. In the final version, Pangram's
own web plagiarism checker flagged only 10 to 21% of 300 Frankentexts,
because the Books3 sources are mostly absent from the open web. The authors
propose two defenses: n-gram lookup against the source corpus paired with an
AI detector, and fine-grained (token-level) authorship attribution.

**Detector responses so far** (details in the project survey). No published paper
yet builds or tests a detector on Frankentext data, as far as the survey
could establish without the citation graph. Pangram 4 (arXiv 2607.27183)
now labels tokens and reaches 75% tokenwise accuracy when single AI
sentences are interleaved with human ones, but was not tested on short AI
connectives between long human spans. Kernel-smoothed token detectors
(2607.21458) assume long same-author runs, the worst case for Frankentext.
Change-point segmentation over per-sentence scores (2605.03723) and
stylometric segmentation (DAMASHA, 2512.04838, boundary accuracy about 46%)
are the nearest seam finders. StoryScope (2604.03136) separates human from
AI fiction on narrative-structure features alone (93.2% macro-F1) and cites
Frankentext without testing it; it is the strongest untested defense, since
a Frankentext copies surface prose while the plot architecture is the
model's. Multi-document style detection (2505.14608) recovers separation
that single documents lose.

**What SETEC would see today.** Reasoned from the shipped surfaces, and to be
measured by M2, not assumed:

- The **discrimination** surfaces (`fast_detect_curvature`, `binoculars_audit`,
  `specdetect_audit`, `structural_shuffle_audit`, `rank_space_audit`,
  `gecscore_audit`) score token-level likelihood structure. Three quarters of
  a Frankentext's tokens are human tokens in human order, so these should read
  it much as they read human prose. Frankentext reports Binoculars and
  Fast-DetectGPT results in its appendix; the survey records what could be
  verified.
- The **smoothing_diagnosis** surfaces measure variance compression. A mosaic
  of 1,500 unrelated human paragraphs is, if anything, more variable than one
  human author, so a smoothing audit should read it as unsmoothed.
- `originality_audit` measures DJ-Search coverage against a pool the operator
  holds. It sees a Frankentext exactly when the pool contains the sources, and
  otherwise not at all. It also reports only the five longest spans' sources
  (`attribution`, `originality_audit.py` lines 191-201), so it cannot say that
  forty covered spans came from forty different books.
- `within_doc_segmentation` locates register discontinuities with
  five-sentence windows by default. Frankentext joins fall inside and between
  sentences, often every one to three sentences, which that window size will
  mostly average away.
- The **creativity-index direction inverts.** SETEC's DJ-Search port treats
  low reconstructibility as the ordinary human case. Against a pool that
  holds the sources, a Frankentext is near-totally reconstructible, far above
  ordinary human prose; against a pool that lacks them, it looks original.

**Orthogonality.** No shipped surface reports **source multiplicity** (distinct
sources per hundred covered tokens) or **junction discontinuity** (style
distance across a join between spans from different sources, relative to the
distance inside spans). A person quoting draws long spans from few sources. A
Frankentext draws paragraph-length spans from nearly as many sources as it has
spans (1,500 snippets came from 1,497 books). That structure is measurable
without any claim about who or what assembled it.

**Why it matters to the fleet.** Voicewright consumes SETEC as its held-out
audit. AGENTS.md there already warns that "a memorized passage spuriously
passes the voice validators". Voicewright's companion draft
(the setec-voicewright Victorian phrase-grounding draft) proposes asking a reviser to
build its connective phrasing from short Victorian n-grams. Any SETEC voice or
register reading of such output is only honest if SETEC can also report how
much of the text is verbatim mosaic and from how many sources.

**Answer to "should SETEC catch this?"** SETEC's posture forbids an AI/human
verdict, so "catch" means two things here: document the blind spot with
numbers (M2), and offer the one measurement that is decisive when the source
pool is available, the mosaic profile (M1), with no verdict attached.

## Method

### M1a — mosaic profile (stdlib, deterministic)

Given a target and a reference pool (directory or manifest, same inputs as
`originality_audit`):

1. Run the shipped greedy DJ-Search cover (`audit_originality`,
   `min_ngram` default 8) and keep **every** counted span with its start,
   length and the reference document it matches. Per-span source lookup is
   the same first-containing-document rule `_source_of` already applies to the
   top five. This is a canonical assignment, not proof of the actual source:
   duplicate/shared passages may occur in several documents, so multiplicity
   is a pool-order-dependent diagnostic, not an origin count. Preserve
   `originality_audit._run`'s path **or canonical token-fingerprint**
   self-exclusion before matching; a target equal to its sole reference must
   refuse as bad input. This needs either an additive option on `audit_originality`
   (default off, existing envelope unchanged) or an import of its matcher, as
   spec 75 imports `_tokens`; the build chooses and the review checks that the
   existing capability's output is byte-identical with the option off. Carry
   `--max-span` (default 256) and report `max_span_cap` and
   `longest_match_capped`; cap-induced splits are not source joins and must
   not inflate junction counts. A capped span length is a lower bound.
   Keep this bounded core run below the repo's long-job checkpoint trigger:
   hard ceilings are 60,000 target tokens, 100,000 post-exclusion reference
   tokens, 5,000 reference documents, and `max_span <= 1024`. Callers may
   lower these ceilings with CLI flags but cannot raise them. Exceeding one
   returns `bad_input` naming the actual count and limit. The build PR records
   local runtime benchmarks for these ceilings. Revisit the ceilings or add
   checkpoint/resume before admitting materially larger pools.
2. **Source multiplicity:** distinct canonically assigned source IDs over
   counted spans; sources per 100 covered tokens; the share of covered tokens
   assigned to the single largest source. With zero counted spans, report zero
   distinct sources and `null` for both ratios, without a division by zero.
3. **Junctions:** every boundary between two consecutive counted spans whose
   sources differ, plus every boundary between a counted span and uncovered
   text. For each, compute the stylometric distance between the 1 to 2
   sentences on either side using the `within_doc_segmentation` feature
   families and standardization/distance math. Extract an import-clean,
   shared pure lens if needed: importing `within_doc_segmentation` currently
   reaches optional NLTK/download paths through `variance_audit`, so the M1
   CLI must use a fixed stdlib sentence split and make no download or model
   import. Map word-token offsets to character offsets and clip windows at
   the join so a mid-sentence join never compares a sentence with itself.
   Fix one document-wide feature-name vocabulary: all function-word and
   sentence-shape features, plus the top 256 character n-grams by summed raw
   per-window frequency across the target (lexicographic tie break). Then
   z-score over the complete document-wide window
   population and apply that same basis to junction and interior distances;
   never fit a separate z-score basis per pair. Use the sibling lens's
   zero-variance and zero-norm handling. If a side lacks usable text, emit
   `null` distance with a reason. Emit every sentence's offsets and sparse
   nonzero numeric features under a fixed `feature_vocabulary` (absent keys
   mean zero; no copied prose); bound the feature vocabulary, not the number
   of sentences. Never choose change points on this surface.
4. **Junction discontinuity:** compare source-to-source joins and
   covered-to-uncovered joins as separate typed distributions against
   distances between adjacent same-size, nonoverlapping windows wholly
   inside one maximal contiguous same-assigned-source run (including spans
   split only by `max_span`). Short runs may have no such windows; use an empty
   distribution and `null` quantiles in that case. Report quantiles, never a
   ratio promoted to a headline.
5. Uncovered text is reported as a share and as a run-length distribution
   (connective runs in a Frankentext are short).

### M1b — mechanical mosaic fixtures (stdlib, CI-runnable)

A deterministic, model-free generator that builds labeled mosaics from a
public-domain pool: sample paragraphs by seeded hash, truncate each to a
sentence boundary, join them with connective sentences drawn from a small
fixed list, and emit token-level copied/connective labels. Fixture assertions
must use source excerpts with unique >=`min_ngram` token runs, no cross-boundary
match or connective match, distinct source IDs and cap-aware lengths (the
matcher caps one span at 256 tokens by default). Compare token-level coverage
and canonical source assignments, not paragraph boundaries that the greedy
matcher may legitimately merge or split. This is a weak
proxy for an LLM-composed Frankentext (no selection for relevance, no
smoothing edits), and the spec says so; it exists so M1a has exact ground
truth in CI.

### M2 — replay (local box, operator-authorized)

1. Compose LLM Frankentexts locally from a **public-domain** pool (Project
   Gutenberg or the admitted public Victorian corpus) with the paper's
   published prompts, at copy targets 25/50/75/90%, plus vanilla generations
   on the same prompts and the human snippets alone. Human, vanilla and mosaic
   class labels come from generation provenance, independently of M1a. Its
   measured coverage is a covariate, never a ground-truth class label.
2. Re-score every shipped discrimination and smoothing signal on the three
   classes (human, vanilla machine, mosaic). Report per signal and per copy
   target the oriented AUC and TPR at FPR {0.05, 0.10}, reusing
   `plugins/setec-voiceprint/scripts/calibration/paraphrase_robustness.py`'s `oriented_auc` and
   `tpr_at_fpr_budgets` where registered. Before replay, publish a per-signal
   scalar, meaning and fixed orientation registry for every named surface;
   the existing `DETECTOR_DIRECTION` covers only a subset and its helpers
   refuse unknown names. Any surface without a defensible scalar/orientation
   is reported as unscorable with a reason, never silently omitted or assigned
   a direction from the replay data.
3. Run M1a on the mosaics against (a) the true source pool, (b) a disjoint
   pool of the same register, and (c) the operator's default impostor pool, to
   show how the profile degrades when the source is not held.
4. If the StoryScope extensions (specs 78 and 79) are available, add its
   narrative-feature readings to the replay as a column: it is the one
   defense that does not depend on holding the sources.

The M2 output is a blind-spot table for `signals-glossary.md`, never a
threshold or a detector.

## Contract (the testable interface)

- **task_surface:** `set_level_diversity` (existing; `originality_audit` and
  `corpus_novelty_audit` live there). The claim-license drop-in
  `plugins/setec-voiceprint/scripts/claim_license_surfaces/set_level_diversity.txt` already exists.
- **CLI:** `python3 plugins/setec-voiceprint/scripts/verbatim_mosaic_audit.py
  --target T (--reference-dir D | --manifest M) [--min-ngram 8]
  [--max-span 256] [--junction-sentences 2]
  [--max-target-tokens 60000] [--max-reference-tokens 100000]
  [--max-reference-docs 5000] [--json] [--out PATH]`.
- **JSON envelope:** via `output_schema.build_output()`. `results` keys:
  `coverage`, `n_counted_spans`, `span_length_quantiles`, `n_distinct_sources`,
  `sources_per_100_covered_tokens`, `largest_source_share`,
  `uncovered_run_quantiles`, `junctions` (token offsets, lengths, canonical
  source ids, boundary type, distance or null and reason; no prose beyond what
  `originality_audit` already emits for its top spans), `feature_vocabulary`,
  `sentence_features` (all sentence token/character offsets plus sparse
  nonzero numeric values keyed by that fixed bounded vocabulary, no prose),
  `source_join_distance_quantiles`, `coverage_join_distance_quantiles`,
  `within_span_distance_quantiles` (null when no samples),
  `min_ngram`, `max_span_cap`, `longest_match_capped`,
  `n_reference_docs`, `assumptions` (including `input_limits` and actual
  post-self-exclusion `reference_tokens`).
- **Claim license:** licenses "this much of the target is covered by verbatim
  spans of at least `min_ngram` tokens from this pool, canonically assigned to
  this many source IDs under the first-containing rule, with these style
  distances at the joins". The assigned IDs need not be the actual origins
  when passages occur in multiple sources. Refuses: AI/human,
  authorship, plagiarism, copyright, intent, and any statement about text
  whose sources are not in the pool. A low coverage against a pool that lacks
  the sources says nothing.
- **capabilities.d entry:** `verbatim_mosaic_audit.yaml` plus a drop-in
  `_golden_capabilities/verbatim_mosaic_audit.json` fragment (no `==N` count
  literal, per post-#170 practice), `status: heuristic`, `compute.tier: core`,
  `length_floor_words: 24`, `consumers: []`.
- **Dependencies:** none beyond stdlib for M1. M2 inherits existing model tiers.

## Test contract

File (planned): *plugins/setec-voiceprint/scripts/tests/test_verbatim_mosaic_audit.py*.

- Deterministic output for fixed inputs.
- Envelope shape and claim license present; recursive no-verdict walk finds no
  `is_ai`, `is_human`, `verdict`, `label` or selection key.
- On an M1b fixture satisfying the uniqueness/cap constraints above,
  token-level coverage and canonical source assignments match the generator's
  labels exactly (the method-specific pin).
- A strict excerpt of a single-source long quotation yields
  `n_distinct_sources == 1`; an M1b
  mosaic of k paragraphs from k documents yields k.
- With the option off, `originality_audit` output is unchanged (regression
  guard for the additive change, as a behavior test, not a source-shape test).
- Empty target or empty pool returns the existing bad-input refusal.
- A nonempty target and pool with no matching spans returns null ratios and
  null/empty distance summaries without crashing; an identical sole pool
  document is self-excluded and refuses bad input.

## Calibration posture

Ships PROVISIONAL with no bands. M2's replay table is descriptive evidence
about other signals, not calibration of this one. A later labeled corpus of
real co-written or quoted prose would be needed before any band could name a
property, and it would name the measured property ("dense multi-source
mosaic"), never the inference target.

## Out of scope / non-goals

- Source-free detection (token-level machine/human attribution). Frankentext's
  second defense needs a trained token classifier (LLMTrace, 2509.21269, and
  2504.11952 are the open starting points); SETEC's posture and the
  no-verdict rule make that a separate research spec, if ever.
- Web-scale or Books3-scale source matching. The in-memory substring search
  suits operator pools, not trillion-token indexes; an infini-gram style
  suffix-array backend is a possible later seam.
- Any use of M2 results to tune a generator. Voicewright may not read this
  audit's output as an objective (its anti-Goodhart note, "construct reuse").

## Open questions

1. Additive option on `audit_originality` or a separate matcher import?
2. Junction window: one sentence either side, two, or a fixed token count?
   Frankentext joins can fall mid-sentence.
3. Which public-domain pool for M2 composition, and does the owner want the
   M2 replay at all, given it requires generating evasion-style fixtures
   locally?
4. Should `corpus_novelty_audit` gain the same per-span sourcing for its
   ordered-pair census, or stay as is?
