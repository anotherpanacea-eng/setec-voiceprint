# CX5 deferred-spec inventory — 2026-09-26

Source pin: `780cab5650309b21fe999b5d5ea4d51a80c4f199` (fresh main).
Every file:line below is relative to this immutable tree.
`S` expands to `plugins/setec-voiceprint/scripts/`; `T` to `S/tests/`.
[Browse the pinned specs](https://github.com/anotherpanacea-eng/setec-voiceprint/tree/780cab5650309b21fe999b5d5ea4d51a80c4f199/specs).
The companion Mirrulations report supplies the separate abstract-method audit.

This inventory classifies source evidence, not scientific validity or permission
to execute a study. No implementation, corpus/model run, judge dispatch, private
consumer review, training, provider integration or hosted CI was performed.
A historical spec statement does not establish a present defect. “Already
delivered” below means the named implementation exists on the pinned main;
it does not certify every acceptance criterion or a private experiment.

## Exact spec inventory and version stance

| Requested number | Actual file(s) inspected | Version/status at the pin |
|---|---|---|
| 78 | `specs/78-storyscope-polarity-extension.md` | Draft v6 at :50; explicit M1/M2 split at :1957. Current implementation supersedes the implication that all M1 work is still awaiting a builder. |
| 75 | `specs/75-reconstructibility-targeted-probe-set.md` | M1 implementation complete, native exact-head CI pending at :8. No numbered revision asserted; the source SHA is the version. |
| 37 | `specs/37-register-classifier-repair.md` | H1-only resolution draft at :12. Read with later main implementations and ROADMAP reconciliation, not as a fresh defect list. |
| 32 | Seven files: `32-deepa2-enthymeme-gapflag.md`, `32-diveye-surprisal-diversity.md`, `32-function-word-adjacency.md`, `32-gec-linguistic-error-axis.md`, `32-lambdag.md`, `32-rank-space-detectllm.md`, `32-structural-shuffle-perplexity.md`, all under `specs/` | Number 32 is not unique. All seven were included; no invented canonical “spec32” or common revision. Their M1/M2 paragraphs are cited individually below. |
| 25 | `specs/25-tdetect-tail-normalization.md` | Shipped with PR228 reversal recorded at :7–15; later open question at :137 supersedes original p-value plan. |
| 06 | `specs/06-voice-matching-companion.md` | Draft at :10; explicit companion-exists update at :31. Producer-side historical contract, not current companion implementation inventory. |

## 78: Storyscope polarity

| Item | Classification and current evidence | Exact prerequisites for future work |
|---|---|---|
| Judge-free M1 receipt/extension implementation | Already delivered | `S/calibration/narrative_polarity_extension.py:564–565` emits per-signal and root receipts. `T/test_narrative_polarity_extension.py:128` exercises a real synthetic receipt; :250 checks tamper re-derivation. Do not rebuild M1 from the draft header. This audit does not certify all M1 or real-corpus acceptance. |
| Sign stability | Intentional deferral | Spec :79–80 and :1976–1980; implementation :564 retains null. Requires replicate dimension, registered count bound into thresholds hash, mismatch refusal, named statistic, floor predicate and named producer. Acceptance: count-mismatch refusal and hand-computed replicated fixtures; null/reason consistency until the full contract lands. |
| Multiplicity/BH | Intentional deferral | Spec :102–108 and :1981–1989; implementation :565 emits null method/alpha/family and both deferral reasons. Requires estimator-specific null matching the registered effect threshold (not an automatic zero null), sidedness, stdlib normal derivation, complete BH family/alpha, hand-computed fixture and per-signal `verdict_qualifier`. Owner must decide precedence step 7; no silent M1 activation. |
| Real judged Arm A then Arm B | Intentional deferral / separately authorized evaluation | Spec :1968–1975 and :2007–2013. Freeze source populations, at least two generator families, floors/bands and truncation draws, and the control-author relationship. Arm A requires over-ceiling whole-work bridges as well as segments; Arm B requires truncation controls. A local code pass cannot supply this evidence. |
| Novel-scale joint claim | Remaining actionable integration/authorization gap if pursued, not a new detector | Spec :2022–2025 explicitly says joint consumption is stated, not mechanized here. Requires a reviewed consumer that binds both Spec79 stability coverage and Arm A polarity, plus Arm B for sub-floor claims, and enforces cross-spec suppression precedence. Acceptance must reject missing or incompatible receipts and confounded bridge results; never promote a positive claim from either receipt alone. This report has not audited downstream consumers. |

## 75: reconstructibility probe package

| Item | Classification and evidence | Exact prerequisites / acceptance |
|---|---|---|
| M1 private probe-selection builder | Already delivered | `S/reconstructibility_probe_set.py:2152` is the driver; :2484–2504 binds partition payload hashes and records zero consumer access/reveal events. A package is not a memorization result or training permission (spec :2479–2484). Existing native CI status in the header was not independently resolved here; do not infer missing code from it. |
| Windows read-only validation and publication | Intentional platform boundary, partly delivered helper | Spec :2366–2388 describes read-only handle/DACL validation but expressly keeps it behind native M1 refusal. `S/reconstructibility_probe_set.py:2060` contains the proof validator; :942–949 refuses unsupported Linux/Windows publication; :2153 refuses before identity/input processing. A native backend needs reviewed handle-relative confinement, reparse refusal, stable file identities, owner/SYSTEM/Administrators DACL checks and publication durability. Acceptance needs native alias/ACL/crash vectors, not POSIX mode-bit emulation or removal of the early refusal. |
| Sealed-access consumer and tokenizer/generation/overlap evaluation | Intentional deferral | Spec :2457–2477 requires create-new access receipt before sealed read, immutable tokenizer, exact prompt-token binding with no truncation/template drift, trustworthy loss-bearing offsets, matched base/comparand/candidate generation and token-defined overlap aggregates. Requires separate private/model authorization and synthetic end-to-end proof before private data; no M2 placeholder is owed by M1. |
| Real selection/evaluation run | Intentional owner gate | Spec :2900–2914 requires frozen tail/probe counts, prompt/minimum suffix lengths, optional grouping caps, seed/time, population membership/grouping attestations and ordered-token projection. Unknown grouping or population relation means escalation, not inferred groups. Tests must keep qualification/sealed partitions distinct and refuse identity mismatch before generation. |
| Native exact-head clearance wording | Remaining verification gap, not established failed CI | Spec :2849–2862 requires both native jobs and :8 still says pending. This audit did not inspect historical hosted run receipts and did not trigger jobs. Reconcile the relevant immutable implementation/train receipts under current integration policy before claiming that gate is satisfied. |

## 37: register repair and sweep

| Item | Classification and evidence | Exact prerequisites / acceptance |
|---|---|---|
| H1 classifier coherence repair | Already delivered | `S/register_classifier.py:416` and :505 implement classification/matching; `ROADMAP.md:347` records H1 land. This is not a fresh instruction to replay the historical D1–D6 inventory. |
| Deferred H2 register-composition sweep | Superseded by delivered, narrower Spec73 | Old deferral is at spec :42 and :603. `ROADMAP.md:330` and :375 identify the landed source-free aggregate hygiene screen; `S/register_sweep.py:10–16` states the confounded proxy and excluded source fields; :89 pins the H1 receipt. Do not recreate the obsolete distribution/semantic-mode interpretation. Private execution still requires authorization. |
| Accuracy calibration and better scorers | Intentional deferral | Spec :587–605 distinguishes coherence from accuracy: independently adjudicated register labels and per-family precision, disjoint calibration evidence and PROVENANCE are prerequisites to promotion. H2 manifest labels are comparison metadata, not ground truth. Acceptance must prevent a composition sweep from upgrading classifier status. |
| Source-family mixture analysis | Intentional separate follow-on | `ROADMAP.md:388` separates it from H2. Requires a reviewed causal/measurement question and authorized categorical metadata; register is confounded with topic/project/date/source/length. Acceptance must not infer semantic modes or publish per-document diagnostics from a hygiene screen. |

The separate register-diagnostics claim owns `voice_distance.py` and ROADMAP.
This report does not take that scope or infer that the old claim's stated PR
status is current. Current main, source and the live claim serve different roles.

## 32: seven distinct spec lineages

| Spec / concrete item | Classification and current source | Prerequisites and meaningful acceptance |
|---|---|---|
| DeepA2 location detection | Already delivered: `S/enthymeme_gapflag.py:209` and :296–297 detect locations and explicitly omit authored premises. | Preserve document order, structural evidence and no soundness verdict. No new M1 work justified. |
| DeepA2 reconstruction | Intentional deferral: `specs/32-deepa2-enthymeme-gapflag.md:83` reserves a separate model-backed reconstruction path. | Reviewed human-review-only output contract, explicit model authorization, lazy dependency path, separation from M1 results; tests must prove M1 never emits generated premises and unavailable backend cannot fabricate one. |
| DivEye aggregate helpers | Already delivered: `S/diveye_signals.py:271`; spec :8–10 says M1 shipped. | No replacement helper required; measurements over injected series do not prove detection efficacy. |
| DivEye registered surface/classifier and empirical gate | Intentional deferral: `specs/32-diveye-surprisal-diversity.md:227–241`; no `diveye_audit.py` in pinned tree. | Resolve direction-transfer and leave-one-generator-out stability, verify paper/method before relying on it, freeze disjoint calibration and scope before registration. Test sign, degenerate series, generator inversion and absence of verdict/selection coupling. If classifier fails transfer, the spec proposes standalone direction-stable columns rather than a silently promoted classifier. |
| FWAN lexical network | Already delivered: `S/function_word_adjacency_audit.py:234`; the draft header is stale evidence for “not built.” | Retain lexical-set semantics and no verdict. |
| FWAN optional POS refinement, raw stationary distribution, normalized density, calibrated cut points | Intentional deferrals/options: `specs/32-function-word-adjacency.md:315–336`, :504–512. Source caveat at `S/function_word_adjacency_audit.py:451` retains refinement as future. | POS option requires parser identity/dependency refusal while lexical default stays available; numerical variants require explicit semantics and dangling/tie/length fixtures. Threshold promotion needs independent disjoint calibration. No logprob/GPU seam belongs in FWAN. These are optional designs, not four missing required features. |
| GEC injected-backend plumbing | Already delivered: the injectable helper at `S/gecscore_audit.py:376` supports stub scoring; the production CLI refuses absent/stub correction backends at :839–844 and :901–906 rather than treating identity correction as a real backend result. | Keep fairness guardrails and heuristic calibration posture. |
| GEC real LanguageTool/GECToR and experiment | Intentional deferral: `specs/32-gec-linguistic-error-axis.md:98–100`; source :47 and :388 name the later seam. | Pin correction backend/model and similarity definition; verify paper's actual configuration; Java availability handling for LanguageTool and separate torch path for GECToR. Test missing backend, identity refusal, polished-human/ESL caveats and error propagation with fakes. Corpus AUC is separately authorized evidence, not supplied by software tests. |
| LambdaG count model | Already delivered, with a model-gated parsing caveat: `S/lambdag_audit.py:27–32` explicitly says fixture-POS arithmetic is stdlib but real POS parsing needs spaCy and otherwise abstains. | Do not call the whole raw-text surface model-free or add a fake parse-free fallback. |
| LambdaG richer POS alphabet / KN smoothing | Intentional deferral: `specs/32-lambdag.md:85–87`; source :310 retains KN as an option. | Choose exact tag alphabet/smoothing/backend and schema compatibility; synthetic POS-stream arithmetic, disjoint corpora and absent-parser refusal tests first. Learned backend adds an independent model gate. |
| DetectLLM LRR versus NPR | LRR delivered in `S/rank_space_signals.py:214`; NPR intentional deferral at :268–293, a genuine explicit fail-loud M2 stub unlike the Mirrulations adapter methods. Spec `32-rank-space-detectllm.md:55–65`. | NPR needs positive LRR empirical signal first, chosen T5 checkpoint and mask-fill/perturbation policy, then separate model/corpus authorization. Synthetic perturbation-count/rank aggregation tests and context-ceiling refusal precede any empirical claim. Calibration stays disjoint from development. |
| Structural-shuffle M1 and paper-verification note | M1 delivered; old “unverified paper” premise is superseded by the source's recorded confirmation at `S/structural_shuffle_audit.py:6–17`. This audit did not independently verify the paper or experimental claim. | Do not treat the stale spec header as proof the paper is nonexistent; exact five features are explicitly SETEC's scalarization, not a copied paper feature set. |
| Structural-shuffle planned scorer/real experiment | Intentional deferral: `specs/32-structural-shuffle-perplexity.md:179–184`; `S/structural_shuffle_audit.py:70–72` records missing planned alias and required explicit scorer. | Pin and verify scorer identity and method, CPU-first/model authorization before GPU, frozen study and disjoint calibration. Use injected perplexities to test deterministic shuffle/features and no-verdict separation; real evaluation remains separate. Do not invent the planned alias or a default model. |

## 25 and 06: owner decisions, not stale-code defects

| Item | Classification and evidence | Prerequisites / acceptance |
|---|---|---|
| T-Detect Student-t score | Already delivered: `S/fast_detect_curvature.py:429–459`; `T/test_tdetect_normalization.py:44–61` tests formula and no p-value. | Preserve default Gaussian output, degree-of-freedom refusal and no-probability caveat. |
| T-Detect tail comparison / `p_value_t` | Original p-value plan superseded; optional heuristic coordinate intentionally deferred. `specs/25-tdetect-tail-normalization.md:137–145` explicitly forbids re-adding unsupported `p_value_t`; source :131–137 calls current value a constant rescale. | Maintainer must decide whether to expose any named non-probability coordinate and define its statistical meaning. Hand-computed transform fixtures, constant-rescale ranking check and no P(AI) claim required; disjoint empirical calibration before any operating-point promotion. No automatic “fix” for inert ranking. |
| Companion repository creation/placement | Already delivered/superseded as a producer prerequisite: `specs/06-voice-matching-companion.md:31–36` records existing companion and consumer-owned formal dependency spec. | Do not implement a voice generator inside Voiceprint. Current companion completeness is outside this source audit. |
| Signed authorization / C2PA or SynthID provenance | Intentional v2 deferral in producer design at :110–124. This is not a claim that current private companion lacks every related feature. | Companion owner must reconcile its fresh main, choose trust/signature/revocation and provenance contracts, and claim that separate repo. Acceptance must cover refusal on absent/invalid authority, private defaults and truthful generated-artifact metadata without exposing held-out diagnostics. |
| Initial RAG-versus-LoRA choice and fresh GI pool | Historical/open owner choices at :133–145, not verified present companion gaps. | Reconcile current companion roadmap before scheduling; fresh-pool independence requires frozen partition/custody policy. Keep selection distinct from held-out acceptance (:85–108), human gate and no dense stylometric reward. Licensing and compute assumptions must be verified for any chosen model, not copied from this old design. |

## Ordered next queue and ownership

1. **Small docs reconciliation, separate claim.** Update only the most misleading
   historical status notes (H2 delivered; structural-shuffle paper note; M1 headers)
   against fresh main. Acceptance: every replacement links actual source/history,
   preserves remaining deferrals and asserts no new calibration. Shared ROADMAP,
   Spec37-adjacent diagnostics and homogeneity design are already owned; coordinate
   first. This task owns only these new reports and its changelog.
2. **Owner design choice: T-Detect follow-up or explicit keep-as-is.** One bounded
   decision record with formula, meaning and synthetic acceptance, no new p-value
   or threshold. Implementation only after a new claim and both review gates.
3. **Owner prioritization: choose one optional M2 contract, not a batch.** FWAN
   refinement is a bounded example; NPR/DivEye/GEC need empirical and/or backend
   prerequisites. Check all live claims, required model/source licenses and
   held-out boundaries before commissioning. No corpus/GPU/provider execution is
   authorized by this queue.
4. **Separate private/evaluation lane.** Spec75 consumer/platform and Spec78
   joint consumer/evaluation need explicit owners, immutable inputs, budgets and
   receipt semantics. Reconcile existing native CI evidence before claiming a
   missing implementation. Native platform acceptance cannot be replaced by
   emulated Windows/macOS checks. Spec06 belongs to the companion owner.

No fresh production bug is established merely by these deferred paragraphs.
The actionable gaps above are bounded verification/integration questions with
explicit prerequisites; they are not license to bypass intentional refusals,
calibrate on development data, expose held-out values to selection or revive a
superseded plan. No Grok follow-up sweep was dispatched or used as evidence.

## Executed validation

Existing synthetic/injected-backend suites on Python 3.12 at the source pin:

```text
py -3.12 -m pytest plugins/setec-voiceprint/scripts/tests/test_tdetect_normalization.py plugins/setec-voiceprint/scripts/tests/test_diveye_signals.py plugins/setec-voiceprint/scripts/tests/test_narrative_polarity_extension.py -q -p no:cacheprovider --basetemp <fresh-local-temp>
63 passed
```

Mirrulations: 95 passed, one real-client-construction test deselected (companion
report explains the boundary). Counts are separate suites, not a full-repository
pass. No failure occurred in these focused runs. Other rows are source inspection,
not executed runtime or scientific validation. Native Spec75 acceptance, private
consumer behavior, current paper claims and real backend performance remain
unverified by this audit. Draft/unarmed delivery is not hosted-CI, merge, release
or empirical clearance. Claude counter-review remains required for any later
new build; independent Codex report reviews cannot substitute for that gate.
