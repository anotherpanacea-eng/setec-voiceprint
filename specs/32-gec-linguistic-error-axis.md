# Spec 32: `gec-linguistic-error-axis` — GECScore grammar-error-density signal (M1)

**Capability id:** `gecscore_audit` (tool/script: `gecscore_audit.py`)
**Task surface (NEW):** `gecscore_discrimination`
**Family:** `gecscore`
**Status:** `literature_anchored` (signal *direction* anchored in arXiv:2405.04286); band stays `heuristic` (threshold un-anchored) — two distinct objects.
**arXiv lead:** GECScore, arXiv:2405.04286. The paper's claimed **98.62% avg AUROC** (across XSum + WritingPrompts) is now **confirmed from the primary source** (the abstract) — but it is the paper's own claim, `[UNVERIFIED on SETEC's corpus]` (not independently reproduced here), so it remains a LEAD, never a target. Cited here, in the PR body, and in the `changelog.d/` fragment per the fleet rule.

> **Provenance / adaptation note.** This spec was authored for the Code-PC checkout (`D:\Code-PC\setec-voiceprint`, `py -3.12`). All paths here are adapted to the Code-Mac live checkout (`/Users/anotherpanacea/Documents/Code-Mac/setec-voiceprint`, `python3`). The Code-PC corpus paths in the M2 section (`D:\Code-PC\_litprose_frontier_2026-06-20`, `D:\hf_cache`) are retained verbatim only as M2 build notes — M2 is NOT built here.

> **Review folded (`REVIEW_gec.md`, verdict GO-WITH-CHANGES). Every change-request below is folded into this in-repo copy:**
> - **Change 1 (CRITICAL — structural): `fairness_dialect_guardrails` gating.** The ROADMAP (line 55) gates GECScore behind `fairness_dialect_guardrails` because the surface INVERTS on ESL/dialect prose — a polished non-native author scores near-1.0 (the AI direction). M1 ships a STRUCTURAL gate, not a prose footnote: `gecscore_audit.py` **co-emits** a `fairness_dialect_guardrails` caution block (it calls `fairness_dialect_guardrails.build_caution_report`, passing the target text so code-switching is detected heuristically, plus any operator-`--declare`d background conditions), surfaces the resulting posture cap in `results.fairness_guardrails`, and names the ESL/dialect inversion as a FIRST-CLASS item in the CLAIM LICENSE (`does_not_license`), not a footnote. The capability fragment lists `fairness_dialect_guardrails` under `dependencies.surfaces` as a recommended co-surface.
> - **Change 2 (de-dup completeness): rule out `rewriting_invariance_audit.py`.** Folded into §2: the Raidar surface (`rewriting_invariance`, `edit_distance_ratio` = `1 - difflib.SequenceMatcher.ratio`) is the closest surface by metric implementation but a DIFFERENT phenomenon — it measures how much an *LLM rewrite-to-improve* changes the text; GECScore measures how much a *grammar-error corrector* changes it. Different corrected form, different axis; ruled out.
> - **Change 3 (rig clarity): interpreter.** M1 is pure-stdlib `python3` (no `py -3.12`). The LanguageTool/GECToR backends are the M2 model-CPU seam (LanguageTool needs Java on PATH; GECToR needs torch). M1 needs neither.
> - **Change 4 (soundness): word-count floor.** `LENGTH_FLOOR_WORDS = 50` (matching `rewriting_invariance_audit.py`); below it the normalized edit distance is noisy, so the surface WARNS (does not refuse).
> - **Change 5 (preprint verification): M0 paper status flagged.** The paper's **98.62%** avg AUROC is now confirmed from the primary source (abstract) but is the paper's own claim, `[UNVERIFIED on SETEC's corpus]` — a LEAD, never a target. No M1 acceptance or claim asserts a paper number as a SETEC-measured fact. Which GEC model and which similarity metric produced the 98.62% claim (Change 5's `GEC_M2_notes.md` preflight) is still the open M2 task, noted in §M2.
>
> **Spec slot:** `specs/` already carries multiple `28-*`/`30-*`/`31-*` files; this lands at the next clean integer, `specs/32-gec-linguistic-error-axis.md`.

---

## 1. Framing (one paragraph)

GECScore is a **grammar-error-density** discrimination signal, structurally orthogonal to both of SETEC's existing detection surfaces (the *probability* surface — Binoculars / surprisal / curvature / spectral — and the *distributional* surface — the 13-signal glass-box stylometry). It asks neither "how does the model decode this?" nor "how does the surface distribution compare to a baseline?" but: **how much does a grammar-error corrector change the text?** AI prose, RLHF-polished to near-zero grammar error, is changed little (high similarity → high `gecscore`); human prose retains residual micro-errors (comma splices, subject-verb-distance mismatches, idiomatic fragments) and is changed more (lower `gecscore`). The signal is defined as `gecscore = 1 - normalized_edit_distance(s, GEC(s))` in `[0, 1]`, with a secondary raw count `gec_n_corrections`. The literature direction is `gt` (**higher `gecscore` ⇒ fewer errors ⇒ the paper's "more AI-like" DIRECTION**), pinned as the named constant `GEC_AI_DIRECTION` and asserted in a test — silent sign inversion is the detection family's shared failure mode. In SETEC's posture this is **descriptive only**: VALUES + a PROVISIONAL band over the value's OWN axis + `calibration_status` — never an `is_ai`/`is_human` label or a thresholded verdict.

The GEC corrector is the **only** load-bearing model/compute dependency, so it is the M1/M2 seam.

---

## 2. De-duplication and reuse

**De-dup check performed against the live Code-Mac codebase.**

- `stylometry_core.py` / `variance_audit.py` — no grammar-error-rate, GEC-similarity, edit-distance, or spell-correction signal anywhere in the 13-signal suite. No overlap.
- `function_word_grammar_audit.py` — function-word *sequence grammar* (bigram/trigram patterns, preposition profiles), NOT grammar-error correction. No overlap.
- **`rewriting_invariance_audit.py` (`rewriting_invariance` surface) — folded de-dup (Change 2).** This is the closest surface by metric *implementation*: it uses `edit_distance_ratio(original, rewrite) = 1 - difflib.SequenceMatcher.ratio` (character-level). But it measures a DIFFERENT phenomenon — how much an *LLM rewrite-to-improve* prompt changes the text (the Raidar observation: the model edits its own AI-like prose less). GECScore measures how much a *grammar-error corrector* changes the text. The distance is computed against a different corrected form (LLM rewrite vs. GEC correction) and tracks a different axis (AI prose is rewrite-invariant under an LLM; AI prose is GEC-invariant because it already has ~zero errors). Structurally orthogonal; ruled out.
- `edit_magnitude_audit.py` (`edit_magnitude`) — a RoBERTa regressor over pre/post-edit manuscript pairs (a manuscript-editing magnitude estimator), not a grammar-error-corrector similarity. No overlap.
- `tocsin_audit` / `specdetect_audit` / `binoculars_audit` / `surprisal_audit` / `fast_detect_curvature` — probability/perturbation surface seams. No grammar-error contact. No overlap.

**Conclusion: GECScore is a genuinely new feature column on a new `gecscore_discrimination` surface.** It reuses the load-bearing plumbing: `output_schema.build_output` / `build_error_output`, `claim_license.ClaimLicense`, `stylometry_core.word_tokens` for the word floor, and `fairness_dialect_guardrails.build_caution_report` for the co-emitted ESL/dialect gate.

---

## 3. Design

### Score definition

```
gec_sim(s) = SequenceMatcher(None, s, GEC_correct(s), autojunk=False).ratio()
           = 2*M / (len(s) + len(GEC_correct(s)))   # M = matched chars
           = 1 - sequence_dissimilarity(s, GEC_correct(s))
```

Value in `[0, 1]`. `gec_sim = 1.0` ⇒ the corrector changed nothing (zero errors detected). The similarity is the **character-level** stdlib `difflib.SequenceMatcher` **Gestalt-pattern ratio** (`autojunk=False`, deterministic) — `ratio() = 2*M / (len(s) + len(corrected))`, normalized by the **SUM** of the two lengths, **NOT** a Levenshtein edit distance and **NOT** normalized by `max(len)` (an earlier draft mislabeled it as a max(len)-normalized edit distance; the implementation has always been `ratio()`). `autojunk=False` is passed so difflib's length-triggered "popular character" heuristic — which perturbs `ratio()` on prose >200 chars and can flip the band — never fires. The feature column is `gecscore`; the detection direction is `GEC_AI_DIRECTION = "gt"` (high `gecscore` is the paper's AI-like direction). Secondary feature: `gec_n_corrections` — the number of distinct correction spans the corrector applied (a raw count, complementing the similarity for short passages).

### The injectable GEC backend (the M1/M2 seam)

`GecBackend` is a tiny protocol with one method, `correct(text: str) -> str`. M1 ships:

- `StubGecBackend(corrections: dict[str, str] | None)` — returns its input unchanged by default (zero errors → `gec_sim = 1.0`), or a canned correction for a fixture input. **This is the M1 default and the CI path: model-free, over INJECTED scores.**
- `LanguageToolBackend` / `GecTorBackend` — the M2 real backends, lazily constructed (LanguageTool needs `java` on PATH; GECToR needs `torch`). **Not built in M1** — referenced as the M2 seam only.

`audit_gecscore(text, *, backend=None, ...)` defaults to `StubGecBackend()`. No model is imported at module load or touched in any test.

### Provisional band (descriptive, over the value's OWN axis)

`band ∈ {indeterminate, low_error_density, high_error_density}` — named after the MEASURED property (grammar-error density), NEVER the inference target (authorship). `high_error_density` = many corrections / low `gecscore` (the human-leaning direction); `low_error_density` = near-zero corrections / high `gecscore` (the paper's AI-like direction). The band carries `calibration_status: heuristic`, `calibration_anchor: user-baseline-required`, the `thresholds_used`, and the orientation string. There is NO `is_ai`/`is_human`/`label`/`verdict`/`decision` key anywhere.

### Co-emitted fairness/dialect gate (Change 1, CRITICAL)

`results.fairness_guardrails` is the `fairness_dialect_guardrails.build_caution_report(...)` output, run on the target text (code-switching detected heuristically) plus any `--declare` background conditions. When a condition is present and the validation baseline does not cover it, the guardrail's posture cap (`revision_only`, `refuses_evaluative_use: true`) is surfaced and echoed into a `gecscore_audit` caveat — so the ESL/dialect inversion is visible at report level, structurally, not as a footnote.

---

## 4. Milestones

### M1 — model-free orchestration over an injected corrector (THIS PR; CI-safe)

**What ships:** the full `gecscore_audit.py` pipeline over a `StubGecBackend`. The `gec_sim` + `gec_n_corrections` math, the `output_schema.build_output` wiring, the `ClaimLicense` (with the ESL/dialect inversion named first-class), the PROVISIONAL band + `calibration_status`, the pinned `GEC_AI_DIRECTION`, the co-emitted `fairness_dialect_guardrails` block, `--batch` mode (one row per manifest passage), the structural posture guards, and the registered drop-in capability.

**Acceptance (all M1, no real GEC model):**
- **AC-1 (math).** `gec_sim(s, s) == 1.0`; `gec_sim(s, "")` handled as the empty-correction edge (max distance → `gec_sim == 0.0`); a known-error fixture pair → `gec_sim` and `gec_n_corrections` match hand-computed values.
- **AC-2 (CLI).** Happy path exits 0 with a schema-1.0 envelope (`gecscore`, `gec_n_corrections`, `band`, `claim_license`, `fairness_guardrails`); missing/unreadable target → `bad_input` exit 3; empty target → `text_too_short` exit 3; below the 50-word floor → exit 0 + a `floor` warning.
- **AC-3 (no-verdict).** A recursive key + categorical-value walk over the FULL envelope finds no `is_ai`/`is_human`/`verdict`/`label`/`decision`; the only categorical leaf is `band.band` in the allowed set; the band names a property, never a class.
- **AC-4 (sign pinned).** `GEC_AI_DIRECTION == "gt"`, asserted; flipping it would flip the band, so it is a fixed linguistic prior, not a tuned parameter.
- **AC-5 (separation guard).** A comment-/string-stripped source scan finds none of the forbidden selection/scoring imports (`fitness`, `setec_signals`, `loop`, `cosplay`, `splits`, `provenance`, `qlora`, `reviser`) — `gecscore` is an evidence column, never a selection signal.
- **AC-6 (fairness gate wired — Change 1).** A target that the guardrail flags (declared `nonnative_english`, or detected code-switching) produces a non-empty `results.fairness_guardrails` with the posture cap surfaced, and a `gecscore_audit` caveat echoing the refusal; the ESL/dialect inversion is named in the claim license.
- **AC-7 (batch).** `--batch` over a 3-entry manifest emits 3 rows, each with the right id + `gecscore` + `gec_n_corrections`.
- **AC-8 (calibration honesty).** Manifest `status: literature_anchored` ≠ `band.calibration_status: heuristic` (two objects); fragment `tier: core`, `dependencies.python: []`, `length_floor_words: 50`, `surface: gecscore_discrimination`.
- **AC-9 (model-free import).** Importing `gecscore_audit` pulls no `language_tool_python`/`transformers`/`torch`; the default audit path runs stdlib-only.
- **AC-10 (bounds).** Empty/whitespace, tie, saturated (`gec_sim == 1.0`), and a NaN-injecting stub backend → handled (the NaN reaches the R4 `OutputValidityError` gate, never a silent number).
- **AC-11 (registration).** The `gecscore_discrimination` surface is in `VALID_TASK_SURFACES` (else `build_output` raises); the capability fragment + per-id golden are present and consistent (drift passes).

**M1 is CI-safe.** All tests run with a stub backend; no network, no model download, no Java, no torch.

### M2 — real GEC backend, corpus experiment, AUC (NOT built here)

Wire `LanguageToolBackend` (default; `shutil.which("java")` preflight, exit 2 if absent) and `GecTorBackend` (`--gec-backend gector`, torch CPU). Run `--batch` over the 212-window lit-horror frontier corpus (`D:\Code-PC\_litprose_frontier_2026-06-20`, Code-PC) and a RAID Books subset; report per-class/per-generator `gec_sim`, standalone AUC, Spearman vs. Binoculars, paraphrase-robustness, and the polished-human null. **Preflight (Change 5):** before the run, verify from arXiv:2405.04286 which GEC model and which similarity metric produced the (now primary-source-confirmed) 98.62% claim, recorded in a `GEC_M2_notes.md`. A null result (AUC ≈ 0.5 on lit-horror) is reported honestly and does NOT block the M1 plumbing. The model/GPU/Java seam is the M2 boundary; M1 ships none of it.

---

## 5. Anti-Goodhart posture (load-bearing)

`gecscore` is a **read-only evidence column**. It does not enter `SetecFitness`, does not feed `rank()`/`validate()`, is never auto-tuned, and is never the held-out SETEC fitness signal (AC-5 pins this structurally). The band thresholds are PROVISIONAL and disjoint from any held-out validation corpus; promotion past `heuristic` goes only through `scripts/calibration/` against a labeled corpus, never by tuning here. The ESL/dialect inversion is the surface's primary false-positive failure mode and is surfaced structurally (the co-emitted guardrail, Change 1) and in the claim license — not tuned away. The deliberate-error-injection adversarial path (typos move `gec_sim` toward the human range) is the named Achilles heel. Keep-the-human: the operator adjudicates; the surface emits no verdict.

---

## 6. Honest framing

**Status: literature_anchored** for the *direction* (the GEC-similarity axis and its sign are grounded in arXiv:2405.04286); the *threshold* is `heuristic` (un-anchored). The paper's 98.62% avg AUROC is now confirmed from the primary source (abstract) but is the paper's own claim, `[UNVERIFIED on SETEC's corpus]` — a lead, not a prior; no SETEC result asserts it as a SETEC-measured number, and M2's first-party numbers supersede it on SETEC's corpus. **Does not license** a binary AI/human verdict, a claim that near-zero error density proves AI authorship (polished/copy-edited/professional-non-native prose also scores near 1.0), or a robustness claim against adversarial error injection.
