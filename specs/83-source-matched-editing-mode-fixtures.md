# 83 — English source-matched editing-mode fixtures

Status: draft
Prepared: 2026-10-02 against main `7537b9f64af8181e1b59250ce66bdfc150d5ac06`. No fixtures generated.

Tier: research-grade; builder tier: COMPLEX. Builder: unassigned pending implementation scope; paired artifacts and holdout isolation require independent review. GPU: later model-backed scoring only, separately authorized. License decision: reuse existing surfaces; no upstream code/data import. Source editions and selected model revisions need their own rights/terms check.

This is a draft fixture protocol, not a new registered capability, CLI, or executable build contract. Existing audit envelopes and claim licenses remain unchanged; no new task-surface identifier is allocated.

## Question and sources

How do existing descriptive surfaces change when a fixed English human passage is edited in different ways? [C-HAT-Bench](https://arxiv.org/html/2609.32770v1) motivates the mode axis; its Chinese detector numbers are not English targets. [Shan, Lee and Hao v2](https://arxiv.org/html/2608.27855v2) motivates reporting editing separately from generation. [Graphite's primary study](https://graphite.io/five-percent/research/ai-tells) motivates matched topics and model-specific reporting. Our stricter same-source editing design is a local proposal; Graphite is not a literary revision benchmark. C-HAT's reported 12.0% mean and 44.4% maximum AUROC losses are relative decreases on Chinese text. Graphite reports 12,877 words, phrases and frames after frequency filters, using summary-mediated topic matching rather than direct rewrites. Neither supplies an English literary threshold. The [Opus update](https://graphite.io/five-percent/research/ai-tells-opus-5-5-update) further motivates dated family-specific reporting; its tell list is not imported.

This is a fixture/report design, with no detector training, threshold calibration, reviser selection or quality ranking. Poor separation is a reportable result. No claim about an unknown writer's provenance follows from it.

## Proposed bounded pilot, subject to corpus/model choice

Use 24 public-domain English passages, 12 fiction and 12 nonfiction, 250–500 words, each from a different work; cap at two works per author. Six additional work-disjoint passages are reserved for prompt/format feasibility only and never enter reported estimates. Establish source editions, public-domain jurisdiction and reuse rights before collecting text. Prefer already approved repository source inventories; do not substitute private corpora or protected evaluation works. If supply does not meet the proposed balance, revise before generation rather than silently relax it.

Freeze the same 24 inputs, one distinct-work reference per input (rights checked, matched genre, outside all evaluation sources), human-written content plans, prompts and decode settings for two owner-chosen subscription model snapshots from different families. Neither plan nor reference is generated during the pilot. Store exact identity/date if an immutable model snapshot is unavailable; alias drift creates a new cohort, never a pooled row.

| Mode | Operational treatment |
|---|---|
| none | Untouched human passage, scored once and shared across model comparisons |
| dispersed | Replace approximately 20% of source sentences, deterministically selected nonadjacent positions; retain other sentence bytes; rebuild locally from returned replacement slots |
| light | Preserve propositions and sentence order; revise wording and local syntax, request at most 20% changed whitespace-token positions |
| deep | Rewrite throughout while preserving propositions, participants and event order; allow sentence restructuring, request at least 50% changed whitespace-token positions |
| plan + reference | Generate from frozen content plan and independent human reference; original wording withheld from generator |
| continuation reference | Supply first 25% of source words; generate the rest to match original total length; report copied-prefix coverage separately |

The continuation row is an explicit addition to the tickler's five-mode ladder, necessary to contrast editing with a generation reference. It is not unconstrained generation. All model-produced modes use the same declared generic Victorian-register instruction; no named-author emulation. None is an immutable anchor, not a register-matched untreated counterfactual. Report source register and treat register change as part of the intervention. This pilot does not by itself establish effects on modern neutral-to-Victorian prose; a rights-cleared modern-human stratum would require a separate addition.

Freeze sentence splitter and whitespace-token diff implementation/version before generation. Define changed-token fraction as `(insertions + deletions + substitutions) / max(source_token_count, output_token_count)` using unit-cost token Levenshtein distance; report it continuously. Prompt intensity targets describe intent, not achieved labels. Record noncompliance, actual edit fraction, length and separate semantic-assessment status; do not regenerate to meet a detector score or retrospectively relabel deep as light. For dispersed mode, require at least five sentences and select floor(0.2 × sentence count), minimum one; if nonadjacent slots cannot be chosen, mark ineligible before the freeze. Replacement slots and copy boundaries record editing operations, not inferred semantic authorship.

Semantic preservation is not mechanically established by token edits. Before scoring, a blinded human reviewer compares each output to the frozen content plan and source: `preserved`, `changed` (omitted/added proposition, changed participant, polarity or event order), `uncertain`, or `not_assessed`. Record coverage and reasons locally; do not remove or regenerate cells on this judgment. No model judge or extra provider call is implied. The rubric and reviewer burden must be accepted in the later run binding.

One retained output per source/mode/model; no best-of-N. At most one retry for transport failure only, same request; log both attempts, never retry a returned refusal or poor prose. Gross truncation, empty output, refusal and malformed replacement slots remain failed cells in the full denominator. A source shortage, changed model identity or reached cap stops the run.

## Existing measurement seams

- `plugins/setec-voiceprint/scripts/binoculars_audit.py`: retain model IDs/revisions, tokenizer compatibility, `score_version`, warnings and missing states. The code supports cross-perplexity v2 and perplexity-ratio v1 fallback; never combine their scales or call v1 true Binoculars. Freeze a compatible pair and mode; unexpected fallback is an unavailable cell, not a substituted score. Model-backed scoring needs separate local execution permission. No new threshold or AI/human verdict.
- `plugins/setec-voiceprint/scripts/verbatim_mosaic_audit.py`, Spec 82 M1: use a fixture-only caller of the existing `audit_mosaic(target_text, reference, ...)` with the identical frozen ordered reference list including source passages and supplied references. Intentional self-matches are retained for all modes. The CLI `_run` excludes references matching target path/content and therefore cannot supply this comparison unchanged; preserve its normal default. In the fixture report explicitly record this intentional self-match policy; report coverage and source multiplicity. One-source copying does not imply human authorship, and nonmatching paraphrase does not imply generation. This is distinct from Spec 82 M2's mosaic replay; no new segmentation algorithm.
- `plugins/setec-voiceprint/scripts/aic_pattern_audit.py`: per-pattern counts and densities with quote handling pinned; preserve parser/version and current regex limitations. Report paired change from the matching human source per model family/version/date. Do not update patterns or import Graphite's tell list during this fixture run. Any later comparison of Graphite frames to disguised-correctio coverage is a separate descriptive coverage table, not automatic detector expansion.

Do not modify `register_typical` baselines. Do not fit model-family baselines here: use stratified observations against the same human sources; calibration would be another protocol. Preserve the existing held-out surface contract, including `binoculars_audit`. Protected-surface outputs can be read only after prompts, variants, surface configuration and identities are frozen. They cannot revise corpus choice, prompts, model choice, feature definitions or thresholds. These fixtures are then consumed audit material, not fresh confirmation data.

## Report and artifact contract

Use ordinary JSONL plus a Markdown report, not a new framework. A private/local row records source/work grouping, source hash, mode, source register, generator identity/date, prompt/settings hash, output hash, attempt count/status, actual edit fraction, length, structural compliance and surface outputs including score version and warnings. Known production mode is fixture provenance; never emit a predicted `is_ai`, `is_human`, combined verdict or automatic winner. Exact prose and per-passage source locators stay in the approved run location; cloud/Git receives only aggregate counts, protocol/whole-artifact hashes and the report.

Report all planned cells and failures. Primary summaries are source-paired changes and distributions per surface, mode, family/version and fiction/nonfiction stratum. Use a work-clustered paired bootstrap (10,000 draws, seed fixed before scoring) for intervals, retaining all variants of a work together. At 12 works per genre this is descriptive, not a powered ranking. Show missing/failed score counts; estimate paired effects on jointly available cells while disclosing coverage and possible missingness bias. No imputation of detector failures as favorable values.

Optional descriptive AUROC applies only to a surface with a predeclared scalar direction (Binoculars: lower ratio first), comparing none to each production mode separately. Fix orientation before scores; never flip to maximize AUROC. No classifier fitting, operating-point tuning, cross-mode transfer claim or composite AIC/mosaic detector. Human anchors duplicated across comparisons are correlated, not extra independent samples.

Before future implementation is cleared, verify using synthetic, non-model fixtures: grouping keeps all variants together; none is byte-identical; mosaic anchor and verbatim returned output both receive full source coverage under the fixed reference list and partial edits retain unchanged-span coverage; dispersed reconstruction preserves untouched slots; missing cells remain counted; unexpected score versions fail closed; report cannot modify baseline files or feed selection/training. Such checks are proposed acceptance criteria, not tests executed here.

## Cost and open decisions

24 × 2 × 5 model-produced modes = **240 retained outputs**, plus 24 human anchors. Six feasibility sources add at most 60 outputs if every mode/family is exercised. With one transport retry per request the absolute request cap is 600, including feasibility. Model-backed audit invocations and token volume are separate costs; repeats of deterministic human scores can be cached under exact identities. This is a proposed count, not an approved cap, measured runtime or dollar estimate.

Owner decisions needed before execution: source inventory/jurisdiction and modern-source scope, two model versions and provider custody/terms, exact frozen prompts/plans/references, request/token/dollar/time caps and local scorer availability. Subscription access does not imply that automation is allowed or free. No provider calls, downloads or corpus acquisition are authorized here.

Limits: known intervention history is not an authorship oracle; public classics may be memorized; short length, formatting, translation and regional register can shift surfaces and are outside this pilot's coverage. [Le Monde](https://www.lemonde.fr/en/pixels/article/2026/09/24/how-does-the-pangram-ai-generated-text-detector-work-and-how-reliable-is-it_6757911_13.html) discusses short-text failures and attributes translation/editing effects, while presenting unusual-format and regional-pattern risks as hypotheses. Its underlying studies were not independently rechecked. Extend these strata only under a later frozen protocol. H and I remain deferred; G does not implement LA-CPD.

## Integration and discovery

Related: [Spec 82](82-verbatim-mosaic-audit.md), existing M1 mosaic measurement. Its M2 replay contract is separate. This draft supplies editing-mode fixtures, not calibration, a segmentation algorithm, or a reviser objective.

`specs/README.md` is held by CX5 source-status corrections and `ROADMAP.md` by register diagnostics. They remain untouched. Suggested entries for those owners:

- Spec index: `83-source-matched-editing-mode-fixtures.md` — Draft research fixture protocol: English same-source editing/generation modes; descriptive Binoculars, mosaic and AIC reports; no generated fixtures or execution authority.
- Roadmap: 2026-10-02 — Spec 83 is proposed; corpus, model versions, human-review burden and resource limits remain open. LA-CPD localization stays deferred until these fixtures establish a need.

No capability, glossary, baseline or calibration-readiness registration changes are appropriate for this spec-only proposal. Source-linked numbers above are paper/report claims, not reproduced results.
