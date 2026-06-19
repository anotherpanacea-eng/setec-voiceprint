# 26-fallacy-warrant-scan

> ArgScope's two missing argument-domain holes: a **fallacy-pattern scan** (per-type tallies + span
> pointers) and a **warrant probe** (Toulmin critical-question coverage). **Descriptive flags for the
> human, never a "bad argument" / "fallacious" / soundness verdict.**

- **Status:** Draft
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

- **No "bad argument" / "fallacious" / soundness / quality label or score.** There is **no aggregate**
  — no "fallacy count too high", no "weak argument" verdict, no numeric soundness. Output is per-type
  **tallies** + **span pointers** + (M2) per-claim **critical-question coverage**, full stop.
- **A flagged pattern may be entirely legitimate in context.** The judge is instructed to surface
  *candidate* patterns with that framing (e.g. an appeal to authority is often valid; a slippery-slope
  may be a sound causal chain). The operator adjudicates; the surface supplies evidence, not a ruling.
- **Unconditionally `uncalibrated`** (the `argument_decision_audit` posture): no shipped thresholds, no
  band. The judge's labels are priors for a human, never a standalone detector.
- The claim license refuses all of the above explicitly; a `--register` gate confines it to
  argument-shaped nonfiction and abstains elsewhere.

## Method

Two judge-derived scripts under one new surface `argument_pattern_scan` (M1 ships the fallacy scan; M2
the warrant probe). Both reuse the **`argument_judge`/`judge_backends` plumbing** (`build_judge` with
`manifest|mock|anthropic|openai|gemini`; `fingerprint_prompt` provenance; `JudgeResult` identity) via a
new `fallacy_judge.py` (mirrors `argument_judge.py`), so a real model never loads in CI (`mock`).

### M1 — `fallacy_scan` (Logic 13-type tally + spans)

The judge reads the (paragraph-segmented) argument text and, per the **Logic taxonomy** (faulty
generalization, ad hominem, ad populum, false causality, circular reasoning, appeal to emotion, fallacy
of relevance, deductive fallacy, intentional, false dilemma, equivocation, fallacy of extension, fallacy
of credibility), returns for each **candidate** pattern: `{type, span_text, paragraph_index,
template_reconstruction}` — the Flee-the-Flaw slot-filled implicit-logic reconstruction. Aggregate to a
`fallacy_tally` (per-type counts) + the flat `fallacy_spans` list. The judge prompt **explicitly frames
each as a candidate that may be legitimate in context**, to be confirmed by a human.

### M2 — `warrant_probe` (Toulmin critical-question coverage)

For each major claim the judge identifies, run the Critical-Questions-of-Thought bank — is the
**warrant** stated? the **backing**? is a **rebuttal/counterargument** addressed? — returning per-claim
`{claim_span, critical_questions: {warrant: present|absent|partial, backing: …, rebuttal: …}}` and a
`warrant_coverage` summary (how many questions answered vs left open). **Descriptive coverage, never
"unsound".**

## Contract (the testable interface)

- **task_surface:** **new — `argument_pattern_scan`.** Register `claim_license_surfaces/
  argument_pattern_scan.txt` (label: "Argument-pattern scan: candidate fallacy patterns + warrant-
  coverage flags for human review — descriptive, no soundness/quality verdict"). → **both goldens
  bump** (`_golden_capabilities.json` +1 per script id; `_golden_task_surface_labels.json` +1 label +
  the `test_claim_license_surfaces` count) — the [[voiceprint-capability-golden-bump]] two-golden rule.
- **CLI:** `python3 .../fallacy_scan.py TARGET --judge {mock,anthropic,openai,gemini,manifest}
  [--register op_ed|policy_brief|testimony] [--judge-manifest M] [--json] [--out F]` (M2:
  `warrant_probe.py`, same shape).
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` = `fallacy_tally`, `fallacy_spans`,
  `n_paragraphs`, `register`, `calibration_status: "uncalibrated"`, the **judge provenance**
  (`judge.provenance` + `prompt_fingerprint_sha256`). **No** aggregate score key.
- **Claim license — licenses:** "candidate fallacy patterns (per the Logic 13-type taxonomy) with span
  pointers + implicit-logic reconstructions, as judge-derived flags for human review." **Refuses:** any
  "this is a fallacy / the argument is fallacious / unsound / weak / bad" determination; any soundness or
  quality score; any aggregate. A flagged pattern may be legitimate in context. `uncalibrated`; the LLM
  judge is a prior, not a detector (read `judge.provenance`).
- **Abstention gates** (mirror `argument_decision_audit` / `narrative_decision_audit`): (a) **register**
  — argument-shaped nonfiction only; a non-argument/fiction register abstains (`available:false`,
  `bad_input` or a structural-only downgrade); (b) **length floor**; (c) **judge-fingerprint drift** —
  any operator band (none ship) is bound to the `prompt_fingerprint_sha256`; a mismatch abstains; (d)
  judge unavailable (`mock`/`manifest` aside) → `missing_dependency` fail-loud.
- **Paper trail:** two `capabilities.d` fragments (M1 + M2) + the `claim_license_surfaces` label +
  `changelog.d` (citing all three arXiv ids) + a `references/signals-glossary.md` entry +
  **both golden bumps** + `gen_calibration_readiness`. Run drift / docs-freshness / `pytest
  test_capabilities_dropin test_claim_license_surfaces` before push.

## Test contract (mock judge; torch-free)

`tests/test_fallacy_scan.py` (M1): a `mock` judge returning a fixed labelled set →
- deterministic `fallacy_tally` + `fallacy_spans` shape (each span has type/span_text/paragraph/
  template); **no aggregate/score/verdict key** (`not in` guard on `soundness`/`verdict`/`is_bad`/`score`).
- **claim-license refuses-verdict** — `does_not_license` contains "may be legitimate in context" and
  refuses "fallacious/unsound/weak"; `calibration_status == "uncalibrated"`.
- **abstention** — non-argument `--register` abstains; judge-fingerprint mismatch abstains; a real judge
  with no SDK → `missing_dependency`.
- **provenance** — `judge.provenance` + `prompt_fingerprint_sha256` present; `mock` is labelled a stub.
`tests/test_warrant_probe.py` (M2): analogous over `warrant_coverage`.

## Calibration posture

Ships **`uncalibrated`** and **never graduates to a verdict** — even a labeled fallacy corpus would only
calibrate *agreement* (kappa vs human flaggers, via `triage_agreement`), reported as a provenance note,
never a shipped soundness threshold. The default is, and stays, descriptive flags.

## Out of scope / non-goals

- No soundness/quality/"bad argument" verdict or score, ever (the whole posture). No auto-rewrite. Not a
  structure signal (that's `argument_decision_audit`). M2 (warrant probe) lands as its own PR after M1.
  The mock judge is a CI stub, never an inference source.

## Open questions

1. **One surface, two scripts** (`fallacy_scan` + `warrant_probe` under `argument_pattern_scan`) vs.
   folding into `argument_decision_audit` — default: new surface (distinct judge task). Confirm.
2. **Span granularity** — paragraph-anchored spans (robust) vs. character offsets (precise but
   judge-fragile); default paragraph + quoted span_text. Pin in a test.
3. **Taxonomy** — ship the Logic 13 as-is, or a curated subset for op-ed/policy register? Default: the 13.
