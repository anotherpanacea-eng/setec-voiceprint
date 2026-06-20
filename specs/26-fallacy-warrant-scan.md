# 26-fallacy-warrant-scan

> ArgScope's two missing argument-domain holes: a **fallacy-pattern scan** (per-type tallies + span
> pointers) and a **warrant probe** (Toulmin critical-question coverage). **Descriptive flags for the
> human, never a "bad argument" / "fallacious" / soundness verdict.**

- **Status:** Ready — adversarially reviewed 2026-06-19 (NEEDS-REWORK → reworked). 3 P1s fixed: (1) **the
  data shape must not adjudicate** — `fallacy_tally`/`fallacy_spans` → `rhetorical_move_flags` /
  `candidate_pattern_tally` (`candidate_type`) + a `not in`-on-keys guard (the operator's hard line);
  (2) **dropped the fabricated `--register` hard-abstain** (no classifier; soft `register_warnings`
  instead); (3) **`prompt_fingerprint_sha256` from `fallacy_judge`'s OWN prompt** (argument_judge's would
  silently break the drift gate). Plus golden counts (capabilities +1/id; label golden +1 once at M1) +
  both scripts declare `TASK_SURFACE` + new `_mock_judge`. M1 (`fallacy_scan`) cleared to build.
- **Tier:** research-grade (judge-tier — mirrors `argument_decision_audit`'s judge wiring; mock judge
  in CI, real LLM judge on the API/box)
- **GPU required:** no (LLM judge via `judge_backends`; `mock` for tests)
- **Upstream / prior art:**
  - **Logic / LogicClimate** — *Logical Fallacy Detection* ([arXiv:2202.13758](https://arxiv.org/abs/2202.13758)):
    the canonical 13-type fallacy benchmark (structure-aware, public data + code).
  - **Flee the Flaw** ([arXiv:2406.12402](https://arxiv.org/abs/2406.12402)): template/slot-fill
    reconstruction of a fallacy's implicit logic (the human-readable explanation scaffold).
  - **Critical-Questions-of-Thought** ([arXiv:2412.15177](https://arxiv.org/abs/2412.15177)): a
    Toulmin critical-question bank (warrant / backing / rebuttal) probing the missing support.
- **License decision:** **clean-room the methods** (a fallacy taxonomy + a critical-question bank + a
  judge prompt). No weights; the LLM judge is the existing pluggable `judge_backends` seam.

## Motivation

SETEC's ArgScope today reads argument *structure* (`argument_decision_audit`: the B1 paragraph-role arc;
`argmove_profile`: AGD stance/abstraction). It has **no** signal for two things a human argument-reviewer
most wants flagged: **(1)** *where a fallacy pattern may be operating* (and a reconstruction of its
implicit logic), and **(2)** *which Toulmin critical questions a claim leaves unanswered* (warrant /
backing / rebuttal gaps). These are the framework's three named argument-domain holes (fallacy detection,
warrant analysis, claim support). This adds them — **descriptively**.

**Orthogonality.** `argument_decision_audit` scores the paragraph-role *arc* against human/LLM anchors;
`argmove_profile` counts AGD markers; `stance_modality_audit` types epistemic posture. None identify
*fallacy patterns* or probe *warrant coverage*. New axis (argument integrity *flags*, not structure
inventory).

## Posture — load-bearing, non-negotiable

**This surface flags candidate patterns for a human; it NEVER adjudicates.** Specifically:

- **No "bad argument" / "fallacious" / soundness / quality label or score — enforced in the DATA SHAPE,
  not just prose.** Output is **candidate rhetorical-move flags** (`candidate_type` + `span_text` +
  `reconstruction`) + a `candidate_pattern_tally` (M2: per-claim critical-question coverage) — every field
  name carries "candidate"/"flag." No span asserts "this IS a fallacy"; **no aggregate** (no "count too
  high", no "weak argument", no numeric soundness). The bare key `fallacy_*` is forbidden in results.
- **A flagged move is frequently legitimate in context.** The judge surfaces *candidate* moves with that
  framing (an appeal to authority is often valid; a slippery-slope may be a sound causal chain). The
  operator adjudicates; the surface supplies evidence, not a ruling.
- **Unconditionally `uncalibrated`** (the `argument_decision_audit` posture): no shipped thresholds, no
  band. The judge's flags are priors for a human, never a standalone detector.
- The claim license refuses all of the above explicitly. **Register handling mirrors the real precedent**
  (`argument_decision_audit`/`narrative_decision_audit`): a soft `register_warnings` caveat + a length
  floor — NOT a hard register-classifier abstain (there is no register classifier, and the existing
  `--register` is a baseline-genre lookup, not a type gate; do not overload it).

## Method

Two judge-derived scripts under one new surface `argument_pattern_scan` (M1 ships the fallacy scan; M2
the warrant probe). A new **`fallacy_judge.py`** *mirrors* `argument_judge.py` but is its own module:

- **Reused** (the plumbing, not the content): `judge_backends.make_api_judge` (provider call/parse), the
  `build_judge(kind=manifest|mock|anthropic|openai|gemini)` factory *shape*, and the `JudgeError` /
  `JudgeResult` pattern — so a real model never loads in CI.
- **New (must not be imported from `argument_judge`)**: its OWN `_SYSTEM_PREAMBLE` / `render_prompt` /
  **`fingerprint_prompt`** — `argument_judge.fingerprint_prompt()` hashes the role/mode prompt, so calling
  it would fingerprint the wrong prompt and silently defeat the drift gate. The provenance
  `prompt_fingerprint_sha256` MUST come from `fallacy_judge`'s own prompt; any (none ship) operator band
  binds to *that* hash. Its OWN `_mock_judge` too — returning a fixed **candidate-flag** set (the
  `argument_judge` mock returns role/mode paragraph labels, the wrong shape).

### M1 — `fallacy_scan` (candidate rhetorical-move flags)

**The data shape itself must not adjudicate** (P1): naming a span `type: ad_hominem` and counting it in a
`fallacy_tally` IS a verdict ("counting fallacies implies they ARE fallacies") — the operator's forbidden
line. So the construct is **candidate rhetorical-move flags**, with "candidate"/"flag" carried in *every*
field name, not just a prose disclaimer:

- `rhetorical_move_flags`: a list of `{candidate_type, span_text, paragraph_index, reconstruction}` —
  `candidate_type` ∈ the **Logic taxonomy** (faulty generalization, ad hominem, ad populum, false
  causality, circular reasoning, appeal to emotion, fallacy of relevance, deductive fallacy, intentional,
  false dilemma, equivocation, fallacy of extension, fallacy of credibility); `reconstruction` is the
  Flee-the-Flaw slot-filled implicit-logic scaffold (the human-readable explanation of *why the judge
  flagged it*, not an assertion it is fallacious); `span_text` is the verbatim flagged span,
  paragraph-anchored (NOT character offsets — judge-fragile; pinned in a test).
- `candidate_pattern_tally`: per-`candidate_type` **candidate** counts (a convenience rollup of the
  flags — never a "fallacy count").

The judge prompt **frames each as a CANDIDATE rhetorical move a human should examine** — explicitly that
a flagged move is frequently legitimate in context (an appeal to authority is often valid; a
slippery-slope may be a sound causal chain). No span carries a "this IS a fallacy" assertion; there is no
aggregate score. **The results dict must not contain the bare key `fallacy_*`** (a test guards this).

### M2 — `warrant_probe` (Toulmin critical-question coverage)

For each major claim the judge identifies, run the Critical-Questions-of-Thought bank — is the
**warrant** stated? the **backing**? is a **rebuttal/counterargument** addressed? — returning per-claim
`{claim_span, critical_questions: {warrant: present|absent|partial, backing: …, rebuttal: …}}` and a
`warrant_coverage` summary (how many questions answered vs left open). **Descriptive coverage, never
"unsound".**

## Contract (the testable interface)

- **task_surface:** **new — `argument_pattern_scan`** (BOTH `fallacy_scan.py` and `warrant_probe.py`
  declare `TASK_SURFACE = "argument_pattern_scan"`; precedented — `voice_distance`/`voice_profile` share
  `voice_coherence`). Register `claim_license_surfaces/argument_pattern_scan.txt` (label: "Argument-
  pattern scan: candidate rhetorical-move flags + warrant-coverage flags for human review — descriptive,
  no soundness/quality verdict"). **Golden bumps (corrected):** the per-script `_golden_capabilities.json`
  bumps **+1 per id** (M1 → 91, M2 → 92; pin the count each PR). The per-surface
  `_golden_task_surface_labels.json` + `test_claim_license_surfaces` count bump **once, at M1 only**
  (23 → 24); **M2 does NOT touch the label golden** (one surface = one label). See [[voiceprint-capability-golden-bump]].
- **CLI:** `python3 .../fallacy_scan.py TARGET --judge {mock,anthropic,openai,gemini,manifest}
  [--judge-manifest M] [--json] [--out F]` (M2: `warrant_probe.py`, same shape). No `--register` *gate*
  (that flag elsewhere is a baseline-genre lookup, not a register-type gate; not reused here).
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` = **`rhetorical_move_flags`**,
  **`candidate_pattern_tally`**, `n_paragraphs`, `register_warnings` (soft caveats),
  `calibration_status: "uncalibrated"`, the **judge provenance** (`judge.provenance` +
  `prompt_fingerprint_sha256` from `fallacy_judge`'s own prompt). **No** aggregate / score / `fallacy_*` key.
- **Claim license — licenses:** "candidate rhetorical-move flags (named against the Logic 13-type
  taxonomy) with span pointers + implicit-logic reconstructions, as judge-derived flags for human
  review." **Refuses:** any "this is a fallacy / the argument is fallacious / unsound / weak / bad"
  determination; any soundness or quality score; any aggregate. A flagged move may be legitimate in
  context. `uncalibrated`; the LLM judge is a prior, not a detector (read `judge.provenance`).
- **Abstention / caveats** (mirror the REAL `argument_decision_audit`/`narrative_decision_audit`
  precedent — soft, not a hard register classifier): (a) **soft `register_warnings`** — if the text reads
  as non-argument/fiction the surface flags a caveat (it does NOT hard-abstain; there is no register
  classifier to do so); (b) **length floor**; (c) **judge-fingerprint drift** — any operator band (none
  ship) binds to `fallacy_judge`'s `prompt_fingerprint_sha256`; a mismatch abstains; (d) a real judge with
  no SDK/key (`mock`/`manifest` aside) → `missing_dependency` fail-loud.
- **Paper trail:** two `capabilities.d` fragments (M1 + M2, one per script) + the
  `claim_license_surfaces` label (M1) + `changelog.d` (citing all three arXiv ids) + a
  `references/signals-glossary.md` entry + the golden bumps above + `gen_calibration_readiness`. Run drift
  / docs-freshness / `pytest test_capabilities_dropin test_claim_license_surfaces` before push.

## Test contract (mock judge; torch-free)

`tests/test_fallacy_scan.py` (M1): a `mock` judge returning a fixed candidate-flag set →
- deterministic `rhetorical_move_flags` + `candidate_pattern_tally` shape (each flag has
  `candidate_type`/`span_text` (paragraph-anchored verbatim)/`paragraph_index`/`reconstruction`).
- **no-verdict DATA-SHAPE guard** — `not in` over results KEYS for `fallacy_tally`, `fallacy_spans`,
  `soundness`, `verdict`, `is_bad`, `score`, and any key matching `fallacy*` / `*soundness*` (the
  operator's hard line — the shape itself must not adjudicate).
- **claim-license refuses-verdict** — `does_not_license` contains "may be legitimate in context" and
  refuses "fallacious/unsound/weak/bad"; `calibration_status == "uncalibrated"`.
- **caveats/abstention** — non-argument-looking text yields a soft `register_warnings` entry (NOT
  `available:false`); judge-fingerprint mismatch abstains; a real judge with no SDK → `missing_dependency`.
- **provenance (own fingerprint)** — `prompt_fingerprint_sha256` equals `fallacy_judge.fingerprint_prompt()`
  (NOT `argument_judge.fingerprint_prompt()` — assert they differ); `judge.provenance` present; `mock`
  labelled a stub.
`tests/test_warrant_probe.py` (M2): analogous over `warrant_coverage` (no "unsound" key/label).

## Calibration posture

Ships **`uncalibrated`** and **never graduates to a verdict** — even a labeled fallacy corpus would only
calibrate *agreement* (kappa vs human flaggers, via `triage_agreement`), reported as a provenance note,
never a shipped soundness threshold. The default is, and stays, descriptive flags.

## Out of scope / non-goals

- No soundness/quality/"bad argument" verdict or score, ever (the whole posture). No auto-rewrite. Not a
  structure signal (that's `argument_decision_audit`). M2 (warrant probe) lands as its own PR after M1.
  The mock judge is a CI stub, never an inference source.

## Open questions

1. ~~**One surface, two scripts**~~ **Resolved: new surface** `argument_pattern_scan`, two ids
   (`fallacy_scan` M1, `warrant_probe` M2) — distinct judge task from `argument_decision_audit`.
2. ~~**Span granularity**~~ **Resolved: paragraph-anchored + verbatim `span_text`** (character offsets are
   judge-fragile and break the deterministic mock test); pinned in the M1 test.
3. **Taxonomy** — ship the Logic 13 as-is (default), or a curated op-ed/policy subset later. Maintainer call.

*Orthogonality note:* `argument_decision_audit`'s B5 `discounting_straw_men_flag` already gestures at
decoy objections — frame `warrant_probe`'s rebuttal critical-question as **complementary** (per-claim
coverage), not duplicative.
