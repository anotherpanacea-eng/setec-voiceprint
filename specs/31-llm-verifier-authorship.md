# voice_verifier — LLM-as-verifier: one advisory authorship signal (never a verdict)

> **Spec number is provisional.** Open PRs collide on spec-NN (28, 30 are
> claimed in flight); this is assigned **31** to sit above every open-PR claim
> and above main's current max (29), pending the gate-pass global renumber.
>
> **Review findings folded (`llm-verifier-authorship-findings.md`, GO-WITH-CHANGES).**
> The corrections to the original draft are inline and flagged `[folded Pn]`:
> - **[folded P1]** compute tier is the REAL `api_llm`, not the invented
>   `compute.tier: judge`; mock-vs-live is encoded via `dependencies` (empty
>   required `python`, lazy `sdks_optional`).
> - **[folded P1]** the capability fragment ships **`json_delivery: stdout`** —
>   `setec_run` only runs entries that carry `json_delivery`, and the surface
>   prints its schema-1.0 envelope to stdout under `--json`. Omitting it would
>   make `setec run voice_verifier` refuse the surface.
> - **[folded P2]** `build_result` is the **4-arg** adapter
>   `(payload, raw_text, identity, judge_input)` — the real
>   `judge_backends.make_api_judge` invokes it with four args; the AV pair is
>   `judge_input`, used for span re-validation.
> - **[folded P2]** `claim_license_surfaces/voice_verifier.txt` is the **single
>   source** the golden mirrors (`build_output` resolves the label from it);
>   ship the txt and the golden mirrors it byte-for-byte.
> - **[folded P2]** the `available: false` acceptance is no longer circular: it
>   is triggered by a `manifest` missing the `band` key raising `VerifierError`
>   into `build_error_output`.
> - **[folded P2 / golden]** the golden is **drop-in**: a per-id
>   `scripts/tests/_golden_capabilities/voice_verifier.json` fragment
>   (`json.dumps(entry, indent=2)` + trailing newline). There is **no `==N`
>   count bump** — the `#170` drop-in refactor removed the count literal (the
>   "golden count 95 → 96" in the original draft is stale). `git add` the
>   fragment explicitly.
> - **[folded P3]** the no-new-`SignalSpec` boundary is grounded against
>   voicewright's `AGENTS.md` + `surfaces.py CONSUMED_SURFACES`, not a
>   nonexistent voicewright "spec 26".
> - **[folded P3]** the M2 model-confidence float stays internal; a guard
>   asserts `to_dict()` exposes no `confidence` / `score` / `p_*` key.

Builds on `judge_backends.py` (the shared lazy-SDK provider plumbing —
`make_api_judge`, `PROVIDERS = ("anthropic", "openai", "gemini")`), the
`narrative_judge.py` / `argument_judge.py` **judge family** pattern (a per-family
`JudgeResult` + `build_judge(kind, …)` factory whose kinds include a `manifest`
default, a deterministic `mock` for CI, and the three API adapters; the
`render_prompt` / `validate_values` / `fingerprint_prompt` discipline), the
`claim_license.py` `ClaimLicense` (`task_surface` / `licenses` /
`does_not_license`) + `TASK_SURFACE_LABELS` drop-in register, and the
`output_schema.py` envelope (`build_output` / `build_error_output`, the
`available: false` + `reason` / `reason_category` R-code abstention path). It sits
**beside** `general_imposters.py` (the stylometric same-author harness) as an
**independent LLM second opinion** under the *same* refusal posture — and, exactly
like every detector-flavored surface, it is **never** a held-out validator, a
selection signal, or a same-author/AI verdict.

Roots (cite all three in the PR body **and** the `changelog.d/` fragment, per the
fleet "cite arXiv in PR + changelog" rule):

- **Can LLMs Identify Authorship?** ([arXiv:2403.08213](https://arxiv.org/abs/2403.08213))
  — the LLM-as-verifier framing: prompt an LLM to compare two texts and emit a
  linguistically-grounded *rationale*, not a bare yes/no.
- **InstructAV** ([arXiv:2407.12882](https://arxiv.org/abs/2407.12882)) — an
  instruction-tuned authorship-verification model that emits a **decision + an
  explanation**; the offline/local-weights variant is the model seam we mirror.
- **CAVE** ([arXiv:2406.16672](https://arxiv.org/abs/2406.16672)) — Controllable
  Authorship Verification Explanations: structured, **decomposed** rationales
  (per-linguistic-feature sub-judgements) with a consistency check between the
  rationale and the conclusion.

- **Status:** Shipped (M1) — `voice_verifier.py` surface merged (released in v1.118.0);
  mock/manifest M1 core, the real `api_llm` path is M2/gated. NEW surface; targets the
  LONG-LIST `voice_verifier (advisory)` row that all three roots map to, and the SHORT-LIST
  "LLM-as-verifier → descriptive/advisory, never a label" posture caution.

## Goal

SETEC already has a **stylometric** same-author check: `general_imposters.py`,
which under bootstrap resampling reports how often a target falls closer to a
candidate's identity baseline than to register-matched impostors, and which
*designs in a refusal* (the gray zone refuses an attribution claim). It licenses
"stylometrically consistent / inconsistent with", never "is by". What SETEC does
**not** have is a **second, model-based opinion** on the same question — the
InstructAV / CAVE / "Can LLMs Identify Authorship?" line — that reads the two texts
*as prose* and produces a **linguistically-grounded rationale** a human can weigh
against the stylometric harness.

The temptation, and the trap, is to let an LLM answer "same author: yes/no". This
spec adds the LLM second opinion **without** that. `voice_verifier` emits a
**calibrated advisory band** over a small ordinal scale (the analogue of
`general_imposters`' trustworthy-extremes-vs-gray-zone, and of
`voice_distance_band`'s drift bands), **plus a decomposed rationale** (the CAVE
shape) with **span pointers** into the two texts, **plus** an explicit
`uncalibrated` posture and the judge+prompt `prompt_fingerprint_sha256` drift
record — and **never** a `same_author` boolean, an AI-vs-human label, or any
aggregate that reads as a verdict. The human reads the band, the rationale, and
the stylometric harness together and decides.

The honest split, stated up front so the milestones don't oversell M1:

- **The advisory band's *vocabulary, scoring, validation, envelope, refusal path,
  CLI, and posture guards* are a property of the framework, not of any model** —
  they are exercised end-to-end through the `mock` judge (the `narrative_judge`
  `_mock_judge` precedent) and are **model-free, stdlib, CI-runnable** (**M1**).
- **Reading the two texts as prose to *produce* the band + rationale is a model
  job** — the InstructAV/CAVE extraction. That is the **gated** offline/local-or-API
  judge seam (**M2**), `skipif`-guarded in CI exactly like the existing judge
  families' live path.

## Honest framing (limits, surfaced not hidden)

- **This is one advisory signal among many — and it is the *weaker* evidence, by
  design.** "Can LLMs Identify Authorship?" reports LLMs are usable but noisy
  zero-shot authorship verifiers; the band is therefore positioned as a **second
  opinion that corroborates or dissents from** the stylometric harness
  (`general_imposters` / `voice_distance`), **not** as a stand-alone answer. The
  surface's `do_not_use_when` says so out loud.
- **A band is not a verdict, and absence is not evidence.** The most-confident band
  is "consistent" / "inconsistent", **not** "same author" / "different author".
  An `inconsistent` band is **not** evidence the text is AI-generated, an impostor,
  a register shift, a co-author, or an editor's pass — the same differential-
  diagnosis-not-verdict discipline `mimicry_cosplay_audit` carries. A
  `cannot_determine` band is the *designed* middle outcome (the `general_imposters`
  gray zone), never an error.
- **Uncalibrated by default.** Like `edit_magnitude_audit` ("uncalibrated by
  default; NOT an absolute % AI"), `rewriting_invariance_audit`, and
  `intrinsic_dimension_audit`, the band ships **uncalibrated**: it is a model's
  ordinal judgement, not a probability, and there is no fixed mapping from band to
  a same-author likelihood without an operator's labeled calibration run. The
  envelope carries `calibration_status: "uncalibrated"` and the claim-license says
  the band is judge+prompt-relative.
- **The rationale is the deliverable; the band is the index.** Per CAVE, the value
  is the **decomposed per-feature rationale with span pointers** (which shared
  habits, which divergences), so the human can audit *why* the model leaned a way —
  not the single band token.
- **Judge-fingerprint drift.** The band + rationale are bound to a specific judge
  model + prompt; the report records `prompt_fingerprint_sha256` (the
  `fingerprint_prompt` precedent shared by `argument_judge` / `narrative_judge` /
  `warrant_judge`) so a band produced under one judge/prompt is flagged
  non-transferable against another, never silently pooled.

## The load-bearing design question (and its answer)

**Why is an LLM authorship verifier not just a `same_author` boolean (or a
probability) — and why is it never a held-out validator?**

1. **The verdict axis (no-verdict discipline).** A `same_author: true/false` (or a
   `p_same_author: 0.83`) reads as an authorship *adjudication* the framework
   refuses to make. So `voice_verifier` emits a **descriptive ordinal band + a
   rationale with span pointers**, and:
   - **The band vocabulary contains no verdict token.** `VERIFIER_BANDS =
     ("consistent", "leans_consistent", "cannot_determine", "leans_inconsistent",
     "inconsistent")` — *stylometric-consistency* language, never `same_author` /
     `different_author` / `ai` / `human`. (Guard: a token-blocklist test asserts the
     band set and the rationale schema name none of `{same_author, different_author,
     ai, human, forgery, plagiar*}` as an emitted value or key.)
   - **No probability, no aggregate score.** The envelope exposes the band
     (categorical), the per-feature sub-judgements (each itself a band, the CAVE
     decomposition), and `calibration_status` — but **no** `p_same_author` /
     `confidence_score` / `verdict` float that invites a `p > k` auto-gate.
     **[folded P3]** even the M2 model-confidence used to demote to
     `cannot_determine` is consumed internally and **never** surfaced as a key —
     a guard asserts `to_dict()` has no `confidence` / `score` / `p_*` key.

2. **The held-out / selection axis (anti-Goodhart, the voicewright contract).**
   `voice_verifier` is a **setec-voiceprint analytic surface**. It is **never**
   added to voicewright's selection or held-out `SignalSpec` sets, never a
   `SetecFitness` term, a reward, or a training target. An LLM authorship signal
   optimised against would Goodhart instantly (the generator learns prose the judge
   rates `consistent` — bland mimicry that games the verifier). It is a **read-only
   diagnostic routed to the human**. **[folded P3]** the no-new-`SignalSpec`
   boundary is the voicewright `AGENTS.md` posture + `surfaces.py
   CONSUMED_SURFACES` set; this PR adds nothing to either (it is voiceprint-side
   only; any voicewright adoption would be advisory operator context, out of scope
   here).

The answer to both: **the LLM second opinion is plumbed exactly like the existing
judge families** (`build_judge` factory, `mock` for CI, `manifest`/API for
production, `ClaimLicense`, `available: false` refusals, `prompt_fingerprint`), so
it inherits their refusals *structurally* rather than re-deciding them.

## Design (model-free band/rationale machinery stdlib; the InstructAV/CAVE judge is the model seam)

A new `voice_verifier.py` (M1 stdlib at import: no model, no SETEC distance
machinery, lazy SDK like every judge family). It is a **judge family** in the
`narrative_judge` / `argument_judge` mould, specialised to authorship verification:
it takes **two text inputs** (a `query` text and a `reference` text, the AV pair),
not one document.

### M1 — model-free core (stdlib; the "build first" piece, no model, deterministic `mock` judge)

- **`VERIFIER_BANDS`** — the ordered ordinal vocabulary
  `("consistent", "leans_consistent", "cannot_determine", "leans_inconsistent",
  "inconsistent")`. `cannot_determine` is the **center / designed-refusal** band.
- **`RATIONALE_FEATURES`** — a small fixed register of the linguistic dimensions
  the decomposed rationale reasons over (the CAVE decomposition):
  `("lexical_habits", "syntactic_constructions", "punctuation_cadence",
  "discourse_moves", "register_and_tone")`. Each appears in the rationale as its own
  sub-judgement (a `VERIFIER_BANDS` token + a `note` + `spans`), schema-pinned.
- **`Span`** — `{"side": "query"|"reference", "start": int, "end": int,
  "quote": str}`, a pointer into one of the two texts. Carries **no**
  `p_same_author` / `score` / `verdict` field.
- **`VerifierResult`** (`@dataclass`, `to_dict` like `JudgeResult`):
  `band: str`, `feature_judgements: dict[str, dict]` (per-feature
  `{"band", "note", "spans"}`), `rationale: str`, `judge_identity: dict`,
  `raw_response: str | None` (truncated in `to_dict`). No score field.
- **`validate_result(result, *, query, reference) -> tuple[VerifierResult, list[str]]`** —
  the `validate_values` analogue: out-of-vocabulary `band` is nulled to
  `cannot_determine` with a warning; each per-feature band likewise; spans whose
  `(start, end)` don't index the named side (against `query` / `reference`) are
  dropped with a warning. **Consistency check (CAVE):** if every per-feature band
  is at one extreme but the top-level `band` is the opposite extreme, append a
  `rationale_band_mismatch` warning — surfaced, **never** auto-corrected.
- **`render_prompt(features=RATIONALE_FEATURES) -> str`** + **`_SYSTEM_PREAMBLE`** —
  instructs the judge to compare on each feature, emit a per-feature band + note +
  verbatim spans, **refuse** to name an author or assert "same person", and return
  only the pinned JSON keys. The refusal is in the prompt **and** structurally
  enforced by `validate_result`.
- **`build_verifier(kind, *, manifest_path=None, model=None, temperature=0.0,
  max_tokens=…, mock_band="cannot_determine") -> VerifierBackend`** — the
  `build_judge` factory, kinds:
  - **`manifest`** (production default): read a pre-computed `VerifierResult` from
    an operator JSON. Malformed / missing `band` → `VerifierError`.
  - **`mock`** (CI): deterministic — emits `mock_band`, a per-feature decomposition
    all set to `mock_band`, and one synthetic span per side, with **no model**.
  - **`anthropic` / `openai` / `gemini`** (M2 API adapters): built through the
    shared `judge_backends.make_api_judge` with this family's `build_user_content`
    (packs both texts + the feature schema), the **4-arg** `build_result`
    `(payload, raw_text, identity, judge_input)` (**[folded P2]**; `judge_input`
    is the AV pair, used for span re-validation), `VerifierError`, `extract_json`.
    Lazy SDK; credentials from env.
- **`build_user_content(user_prompt, pair)`** — packs the AV **pair**:
  `"{user_prompt}\n\n# Query text\n\n{query}\n\n# Reference text\n\n{reference}"`.
- **`fingerprint_prompt(prompt_text="") -> str`** — SHA-256 over
  `_SYSTEM_PREAMBLE + render_prompt()`, recorded as `prompt_fingerprint_sha256`.
- **`build_claim_license(result) -> ClaimLicense`** — `task_surface =
  "voice_verifier"`. `does_not_license` names a same-author/different-author
  verdict, an AI-vs-human determination, and a probability/score.
- **Envelope (`output_schema.build_output`):** schema-1.0, `available: true`,
  `results` carrying `band` / `feature_judgements` / `rationale` /
  `calibration_status: "uncalibrated"` / `judge_identity` /
  `prompt_fingerprint_sha256`, plus the `ClaimLicense` block. **Refusal path
  (`available: false`):** a judge backend that fails surfaces through
  `VerifierError` → `build_error_output(reason=…, reason_category=…)`.
- **`voice_verifier` CLI / `setec run voice_verifier --json` entrypoint** — takes
  `--query <path>` `--reference <path>` `--judge {manifest,mock,anthropic,openai,
  gemini}` `[--manifest <path>] [--judge-model <id>] [--json]`. **[folded P1]**
  prints the schema-1.0 envelope to **stdout** under `--json`, so the fragment's
  `json_delivery: stdout` lets `setec_run` run it.
- **`import voice_verifier` stays stdlib** — the SDKs are lazy inside
  `judge_backends._provider_setup`; no torch/transformers at import.

### M2 — InstructAV / CAVE extraction over the two texts (model seam; gated, `skipif` in CI)

- The API/local-weights judge actually reading the prose, gated like the existing
  judge families' live path. **No model loads in CI; the `mock`/`manifest` kinds
  cover M1.**
- **Extraction (InstructAV + CAVE).** The judge emits the decomposed per-feature
  judgement + synthesised band + verbatim spans. An InstructAV `same/different`
  decision is demoted to `leans_consistent` / `leans_inconsistent`, and **low model
  confidence collapses to `cannot_determine`** — **[folded P3]** the confidence
  float is consumed internally and never emitted as a key.
- **Span grounding is validated, not trusted** (M1 `validate_result`).
- **Judge-fingerprint drift gate** + **strictly advisory + read-only**.

## Registry / golden discipline (NEW surface — drop-in)

- **`capabilities.d/voice_verifier.yaml`** — a new drop-in fragment (the
  `argument_decision_audit.yaml` shape): `id: voice_verifier`, `surface:
  voice_coherence`, `status: literature_anchored`, `family: llm-verifier`,
  **`json_delivery: stdout`** (**[folded P1]**), `min_setec_version: "1.117.0"`,
  the `purpose` / `use_when` / `do_not_use_when`, `inputs`, `outputs` (schema-1.0),
  **`compute.tier: api_llm`** (**[folded P1]**; `mock`/`manifest` are stdlib),
  `dependencies` (empty required `python`; lazy `sdks_optional`), `references`
  citing all three arXiv roots.
- **`scripts/tests/_golden_capabilities/voice_verifier.json`** — the per-id golden
  fragment (`json.dumps(entry, indent=2)` + trailing newline), `git add`-ed
  explicitly. **No `==N` count bump** (**[folded P2]**; the `#170` drop-in golden
  derives the count from the fragments).
- **`scripts/claim_license_surfaces/voice_verifier.txt`** — the single source the
  golden + envelope label mirror (**[folded P2]**).
- **`changelog.d/<slug>.md`** — cites the three arXiv roots (fleet rule).

## Considered & rejected (posture)

- A `same_author` boolean / `p_same_author` / any verdict token. Blocklist-guarded.
- Replacing `general_imposters`. This is an **independent second opinion**.
- Wiring the band into voicewright selection / held-out / `SetecFitness`. No new
  `SignalSpec` ships.
- A calibrated probability by default. Ships `uncalibrated`.
- A free-text-only rationale. Schema-pinned per-feature decomposition + CAVE
  consistency check.
- Trusting model-returned spans. Every span re-validated against offsets.
- A single-document mode. Pairwise (`--query` + `--reference`) required.

## Non-goals

- Authorship *attribution* (1-of-N), a legal/forensic opinion, or an AI-vs-human
  classifier. Pairwise advisory consistency only.
- Any change to `general_imposters.py`, `voice_distance`, `voice_fingerprint`, or
  the voicewright selection/held-out `SignalSpec` contract.
- A bundled fine-tuned InstructAV checkpoint or a hosted judge.
- Calibrating the band to a same-author probability as a shipped default.

## Anti-Goodhart / posture guardrails (must hold)

`voice_verifier` is an **advisory read-only analytic diagnostic** — one signal
among many, **never** a held-out validator, a voicewright selection/`SetecFitness`
signal, a reward, or a training target (no new `SignalSpec` ships) · every output
is a **descriptive ordinal band + a decomposed rationale with verbatim span
pointers**, with **no** `same_author` / `different_author` / `ai` / `human` token
in the band set or rationale schema (token-blocklist guard), and **no**
`p_same_author` / score / verdict / confidence numeric (shape guard, **[folded
P3]**) · the band ships **`calibration_status: "uncalibrated"`** · **absence is
not evidence** — an `inconsistent` band is not a forgery / AI / different-person
verdict, and `cannot_determine` is the *designed* refusal · the refusal path is the
live `available: false` + `reason_category` envelope (`build_error_output`) · the
report records `prompt_fingerprint_sha256` · model-returned spans are
**re-validated against text offsets** · `import voice_verifier` stays stdlib · it
**dedups against** `general_imposters` as an independent second opinion · the NEW
surface ships the drop-in `capabilities.d/` fragment + the per-id golden fragment +
a `claim_license_surfaces/` txt + a `changelog.d/` fragment citing all three roots
(**no count bump** — drop-in golden).

## Acceptance (stdlib-only where a model isn't required)

1. **Band vocabulary + no-verdict blocklist (M1):** `VERIFIER_BANDS` is the 5-token
   ordinal with `cannot_determine` at center; a guard asserts **none** of
   `{same_author, different_author, ai, human, forgery, plagiar*}` appears as a band
   token, a `RATIONALE_FEATURES` key, a `VerifierResult` field name, or an emitted
   value.
2. **Result shape + no score field (M1):** `VerifierResult.to_dict` exposes `band`
   / `feature_judgements` / `rationale` / `judge_identity` (+ truncated
   `raw_response`) and **no** `p_same_author` / `confidence_score` / `confidence` /
   `score` / `verdict` field; each `feature_judgements` entry carries a
   `VERIFIER_BANDS` band + `note` + `spans`.
3. **`validate_result` (M1):** a valid result round-trips; an out-of-vocabulary
   `band` is nulled to `cannot_determine` with a warning; a per-feature
   out-of-vocabulary band is likewise nulled+warned; a `span` whose `(start, end)`
   does not index its named side is **dropped with a warning**; an all-extreme
   per-feature decomposition under an opposite top-level band appends a
   `rationale_band_mismatch` warning **without** auto-correcting.
4. **`mock` judge end-to-end (M1, no model):** `build_verifier("mock",
   mock_band=…)` produces a `VerifierResult` with the chosen band, a full
   `RATIONALE_FEATURES` decomposition, and one synthetic span per side; it drives
   the envelope + CLI with **no SDK import**.
5. **`manifest` judge (M1):** `build_verifier("manifest", manifest_path=…)` reads a
   pre-computed result; a missing/malformed manifest raises `VerifierError`.
6. **Pairwise input is required (M1):** the entrypoint requires both `--query` and
   `--reference`; invoking with one text exits non-zero with a friendly message.
7. **Envelope + uncalibrated + fingerprint (M1):** `setec run voice_verifier`
   (mock) emits a schema-1.0 envelope with `available: true`, `results.band`,
   `results.calibration_status == "uncalibrated"`, the decomposed
   `feature_judgements`, `prompt_fingerprint_sha256` (= `fingerprint_prompt()`), and
   a `ClaimLicense` whose `does_not_license` names "same-author/different-author
   verdict", "AI-vs-human", and "probability/score".
8. **Refusal path is `available: false` (M1) — [folded P2, not circular]:** a
   `manifest` whose JSON is missing the `band` key raises `VerifierError`, which the
   entrypoint routes into `build_error_output` with `available: false` +
   `reason_category`, **not** a fabricated `cannot_determine` band in an
   `available: true` envelope (control asserted both ways).
9. **Stdlib import (M1):** `import voice_verifier` pulls no model/SDK dependency;
   a guard asserts no torch/transformers/provider-SDK at module import.
10. **Drop-in golden (M1, the registry gate) — [folded P2]:** `capabilities.d/
    voice_verifier.yaml` parses; `scripts/tests/_golden_capabilities/
    voice_verifier.json` mirrors the aggregated entry (the `test_capabilities_dropin`
    by-id golden passes, count derived from fragments — **no `==N` literal**);
    `claim_license_surfaces/voice_verifier.txt` is the label source; the
    docs-freshness gate (`tools/check_docs_freshness.py`) passes with the new
    `changelog.d/` fragment; `tools/check_capabilities_drift.py` passes.
11. **InstructAV/CAVE judge (M2, gated — `skipif`, no model in CI):** with an
    injected/stub or live API judge, the AV pair yields a decomposed
    `feature_judgements`, a band mapped from the model decision (InstructAV
    `same/different` demoted to `leans_*`, low confidence → `cannot_determine` with
    the confidence kept internal), verbatim spans **re-validated against offsets**,
    and the recorded `prompt_fingerprint_sha256`. The M2 entrypoint returns **only**
    the envelope.

## Milestones

1. ⏳ **M1 (model-free, stdlib):** `voice_verifier.py` — `VERIFIER_BANDS` +
   `RATIONALE_FEATURES` + `VerifierResult` / `Span` + `validate_result` +
   `render_prompt` / `_SYSTEM_PREAMBLE` / `fingerprint_prompt` + `build_verifier`
   (`mock` + `manifest` kinds, lazy API adapters) + `build_claim_license` + the
   `setec run voice_verifier` entrypoint (pairwise `--query`/`--reference`,
   stdout `--json`) + the `available: false` refusal path + the
   verdict-blocklist / no-score / stdlib-import guards + the drop-in
   `capabilities.d/` fragment + the per-id golden fragment +
   `claim_license_surfaces/` txt + `changelog.d/`. No model.
2. ⏳ **M2 (InstructAV/CAVE judge seam; gated, `skipif` in CI):** the
   `anthropic`/`openai`/`gemini` API adapters + a lazy local InstructAV-style
   backend, the decomposed-rationale extraction + the decision→band demotion +
   span re-validation + the judge-fingerprint record, strictly advisory.

M1 is the stdlib core + posture surface and is independently useful as the
**advisory-signal contract** before any judge is wired; M2 is the model seam.
Version + changelog are cut **at release, not in the PR** (a PR ships the
`changelog.d/` fragment).
