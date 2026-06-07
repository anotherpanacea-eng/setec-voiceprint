# 21-attribution-refusal-lab

> A **refusal-curve laboratory** for the three person-identifying capabilities the
> framework refuses to ship as verdicts — open-set author attribution, demographic
> profiling, and cross-document identity linkage. The deliverable is **not** an
> attribution tool. It is a `validation`-surface harness that maps *where the signal
> exists and where it is bullshit*, and scores an untooled LLM's confident
> attribution against known ground truth. Strength-of-evidence only; never a name or
> a label.

- **Status:** Spec (research-grade; build-gated). The **contract below is dispatchable**; the code build is gated on the §Gating prerequisites — chiefly the strong-foil resourcing decision. No script / `capabilities.d/` entry ships from *this* doc; they are specified here for the build.
- **Tier:** Research (capability-whitespace discussion 2026-06-07; ROADMAP → "Explicit anti-goals", the *no-verdict-about-the-person* cluster).
- **GPU required:** no — the classical foil is CPU (reuses `general_imposters` / `voice_distance`); an optional neural foil reuses `authorship_embedding`; the E3 LLM-contrast leg needs operator-side LLM access (API or local instruct model).
- **License decision:** N/A — clean-room. Method is classical stylometry (Mosteller & Wallace 1963; Koppel/Schler/Argamon; Kestemont et al. 2016 GI). Ground-truth corpora are public shared tasks (PAN author-identification / author-profiling) + the Federalist set + held-out same-author pairs from owned corpora; no restricted artifact is vendored.
- **task_surface:** existing **`validation`** — the lab reports *signal reliability / degradation* and a *scored foil-vs-LLM contrast*, never an identity. It does **not** register a person-identifying surface.

## Motivation

The constructive half of the 2026-06-07 capability-whitespace discussion. Three
capabilities — open-set 1-to-many attribution, demographic profiling, sock-puppet
linkage — are a short step from shipped machinery (verification, GI,
`idiolect_detector`, the impostor pools), and untooled LLMs will perform all three
*confidently and untraceably* on request. The framework's foundational claim ("Why
no verdict": confident output ≠ licensed evidence) was built for *is-this-AI*; this
lab extends it to *who-is-the-author*.

The point is **not** to build the capability cautiously. It is to build the
instrument that measures the capability's **absence**, and to use the LLM's
overconfidence as the contrast that makes the absence legible. The natural and
*encouraged* outcome is a null or weak result. A harness whose headline is "here is
how little this licenses, and here is the untooled model asserting more" is an
antibody, not a weapon; the incentive gradient is inverted relative to a product
that names people.

**Orthogonality / relation to shipped surfaces.** Closed-set, register-matched,
*known*-candidate attribution with a gray-zone refusal already ships as
`general_imposters` — that stays. This lab studies what happens when the constraints
that make GI honest are *removed* (unknown/large/unmatched candidate pool;
group-distribution reference instead of an individual's prose), and reports the
degradation, not an identity.

## Method — three experiments

All three emit **strength-of-evidence** (likelihood ratio / posterior odds /
proximity-to-distribution with explicit uncertainty), never an identity. Ground
truth is *known* in every experiment, so "the LLM is bullshitting" is a scored
claim, not a vibe.

### E1 — Evidence-strength vs. candidate-pool size & topic drift (the Federalist dissolution)

Take a Federalist-grade closed-set result (Mosteller-Wallace function-word odds, or
SETEC's GI proportion) where the signal is genuinely strong, then **inflate the
candidate pool and drift the topic**, plotting evidence-strength as both grow.
Expected: the signal that cleanly separates Hamilton/Madison on matched 18th-c.
political essays dissolves into noise as the pool reaches population scale and the
register/topic stops matching — i.e., the regime the LLM operates in (open
population, cross-topic, short text) is exactly the regime where the signal is gone.
The **curve is the debunk, drawn.** Reuses `general_imposters`, `voice_distance`,
and the impostor pools; no new detector.

### E2 — The demographic confound-control (profiling evaporates under matching)

Build a group reference (e.g. an L1-Russian vs L1-English corpus) and report the
target's **proximity to the group distribution** — *then* re-run against topic-,
formality-, and era-matched controls. Expected honest result: the apparent L1 signal
collapses into `{topic, formality, education, era}` once controlled, consistent with
the cross-corpus polarity-volatility finding (a different reference corpus gives a
different "distribution"). The debunk here is not "the tool is weak" but "**the
construct doesn't survive controls**" — while the untooled LLM still hands you a
confident L1/age/gender guess. The weakest of the three as a measurement, the
strongest as a debunk; go in expecting the null.

### E3 — The paired LLM contrast (the deliverable)

On inputs with **known ground truth**, run the same target through (1) the
**calibrated foil** (strength-of-evidence; abstains / LR ≈ 1 / wide gray zone when
the regime is hostile), (2) the **untooled LLM** (one or more; prompted exactly as a
naive user would — "who wrote this? / profile this author"), and (3) **ground
truth** (which, in the hostile regime, says the foil was *right* to abstain and the
LLM was confidently wrong). The aggregate three-way table is the publishable
artifact: it shows the LLM's confidence is *uncorrelated with* (or anti-correlated
with) the licensed evidence exactly where it matters. Repeat across the regimes
E1/E2 map (pool size, topic drift, text length) to show *where* the LLM's bullshit
is worst.

**E3 non-leak rule (safety boundary — testable; a precondition for E3 ever running).**
E3 must *prompt* the foil/LLM to produce the very thing the framework refuses — a
name, a demographic label, a same-person judgment. Those raw outputs are evidence to
be **scored**, not artifacts to be kept. As builder invariants (enforced in code; see
the Contract's redaction gate):

1. **Raw person-identifying outputs are private/redacted.** Every raw foil/LLM
   identity/profile output is written only to an access-controlled private store,
   never to any committed or published artifact (including the envelope).
2. **Public artifacts carry only aggregate categories** — correct / incorrect /
   abstain counts, calibration bins, LR / odds *distributions* — and never a name, a
   demographic label, or a same-person verdict for any individual.
3. **A redaction scan gates publication (go/no-go).** Before any artifact leaves the
   lab, an automated scan asserts zero person-identifying tokens; a hit **fails the
   build**.
4. **Only the score leaves.** What the foil and the LLM *said* about a person stays
   private; what leaves is the scored outcome against ground truth.

## Contract (the testable interface)

- **task_surface:** existing **`validation`** (no new surface; no person-identifying
  surface).
- **Entrypoint / CLI (the harness, not an audit):**
  `python3 scripts/attribution_refusal_lab.py --experiment {e1,e2,e3} --manifest LAB_MANIFEST --foil {gi,embedding} [--llm MODEL] [--private-store DIR] [--seed N] [--json] [--out PATH]`.
  `--llm` is required for `e3`; `--private-store` (an access-controlled dir) is
  required whenever raw identity outputs are produced (e3); `--redaction-gate` is
  **on by default and cannot be disabled** for any non-private write.
- **Lab-manifest schema** (the data manifests). JSONL, one row per corpus role,
  validated by a `validate_lab_manifest()` (mirroring `manifest_validator`):
  - `path`, `role` ∈ {`target`, `candidate_pool`, `group_reference`, `control`,
    `ground_truth`}, `ground_truth` (the known answer used for scoring),
    `register`, `topic`, `era`, `lang`, `privacy: private`, and
  - **`consent` ∈ {`consented`, `held_out`, `synthetic`}** — any other value
    (notably `live_unconsented`) makes the harness **refuse to run** (the consent
    gate). This is the bright line between "Federalist lab" and "live de-anonymizer."
- **JSON envelope:** `output_schema.build_output(task_surface="validation",
  tool="attribution_refusal_lab", …)`. `results` is **aggregate-only**, per
  experiment:
  - **E1:** `evidence_strength_curve` = `[{pool_size, topic_match, metric_name,
    metric, ci95}]`, `dissolution_point` (pool size where the metric crosses the
    gray zone), `foil`.
  - **E2:** `group_proximity`, `control_design`, `residual_after_controls`,
    `confound_share` (`{topic, formality, era, education}`).
  - **E3:** `contrast` = `{foil:{correct,incorrect,abstain,n}, llm:{model,
    correct,incorrect,abstain,n}}`, `calibration_bins`, `llm_overconfidence_index`,
    `foil_strength_gate:{passed,floor,observed}`, `run_status` ∈ {`OK`,
    `INCONCLUSIVE_WEAK_FOIL`}. **No** per-item identity keys.
  - Carries a `ClaimLicense`.
- **Claim license:** *licenses* "the measured reliability / degradation of an
  attribution or profiling signal across candidate-pool size, topic drift, and
  confound controls, and the scored contrast between a calibrated foil and an
  untooled LLM against KNOWN ground truth — reported as aggregate error / confidence
  categories." *Refuses* any per-individual identity, demographic label, or
  same-person verdict; any use against a live / unconsented population; any
  generalization beyond the held-out / consented reference; and — when the
  foil-strength gate fails — any "debunk" reading of the null. *Caveats:* held-out
  ground truth; the result is conditioned on the foil's strength (see the gate); the
  redaction gate governs every public artifact.
- **Capabilities posture (for the build):** a `capabilities.d/attribution_refusal_lab.yaml`
  fragment — `surface: validation`, `status: heuristic`, `handoff: none` (research
  scaffold), `family: validation`, `compute: {tier: core}` for E1/E2 and `{tier:
  api_llm}` for E3, `dependencies.python: []` (+ the operator's LLM SDK under
  `sdks_optional` for E3). **Not created by this doc** — created by the build PR, so
  the drift linter stays green here.
- **Dependencies / footprint:** stdlib + the existing surfaces for E1/E2; E3 adds
  operator-side LLM access (network + key + per-call cost) and the private store.

## Acceptance metrics & go/no-go gates

Three gates, each a hard refusal (not a warning), plus per-experiment acceptance:

- **Consent gate.** Every manifest corpus `consent ∈ {consented, held_out,
  synthetic}` or the harness refuses (`available=False`, nonzero exit). No live /
  unconsented population, ever.
- **Redaction gate.** Zero person-identifying tokens (candidate-set surface forms,
  demographic labels, same-person verdict strings) in any public artifact or the run
  fails. Runs before every non-private write and in CI.
- **Foil-strength gate (E3 publication).** The foil must clear a strength **floor**
  on an in-distribution validation set (e.g. GI accuracy ≥ floor on matched
  closed-set pairs, or PAN AUC ≥ floor) *before* an E3 contrast may be read as a
  debunk. Below the floor, `run_status = INCONCLUSIVE_WEAK_FOIL` and the contrast is
  **not** publishable as evidence the LLM is bullshitting (a weak null proves "weak
  tool," not "intrinsic limit").
- **Per-experiment acceptance.** E1: a curve with CI95 + a `dissolution_point`. E2: a
  recorded `control_design` + `residual_after_controls`. E3: the aggregate contrast +
  `calibration_bins` + a *passed* foil-strength gate.

## Test contract (`scripts/tests/test_attribution_refusal_lab.py`)

- `test_task_surface_is_validation` — `TASK_SURFACE == "validation"`.
- `test_consent_gate_refuses_live` — a manifest row with `consent: live_unconsented`
  → harness refuses (`available=False` / nonzero), no scoring runs.
- `test_consent_gate_allows_held_out` — `held_out` / `synthetic` corpora run.
- `test_redaction_gate_blocks_identifying_token` — a public artifact containing a
  candidate name / demographic label → gate raises / build fails.
- `test_e3_public_envelope_is_aggregate_only` — the E3 `results` contains only
  aggregate keys; **no** per-item identity field anywhere in the envelope.
- `test_foil_strength_gate_marks_weak_foil_inconclusive` — a foil below the floor →
  `run_status == INCONCLUSIVE_WEAK_FOIL`, and the claim-license/report refuse the
  debunk reading.
- `test_e1_curve_decreases_on_synthetic` — a synthetic fixture where the signal
  provably dissolves as the pool grows → monotone-ish decreasing curve with CIs.
- `test_e2_pure_topic_residual_near_zero` — a synthetic where the group difference is
  pure topic → `residual_after_controls ≈ 0`.
- `test_e3_contrast_with_stub_llm` — deterministic stub LLM + stub foil; **no** real
  LLM call; aggregate contrast computed.
- `test_private_store_required` — producing raw identity outputs without a
  `--private-store` (or to a public path) is refused.
- `test_claim_license_refuses_identity_and_live` — `does_not_license` names identity /
  label and live / unconsented population.
- `test_envelope_shape_validates`; `test_deterministic` (E1/E2 seeded; E3 stubbed).

## Calibration posture

PROVISIONAL by design — the *foil's* operating point and the *gray zone* are
operator-supplied (this is a `validation`-surface study, not a shipped detector). The
load-bearing number is the **foil-strength floor**; it is set per ground-truth corpus
and recorded in a PROVENANCE entry alongside the run. Nothing here ships a threshold
that names a person.

## Gating prerequisites (before build)

1. **The strong-foil resourcing decision.** A near-SOTA foil (classical GI stack, or
   a neural `authorship_embedding` foil) that clears the strength floor — *the*
   load-bearing prerequisite, because a weak foil manufactures a false null that
   helps the bullshitters. If it can't be done properly, don't build the lab.
2. **Consented / held-out ground-truth corpora** with the lab-manifest `consent`
   tags (PAN author-id/profiling; Federalist; owned held-out same-author pairs).
3. **E3 LLM access** (operator-side) with a pinned prompt + model version so the
   "bullshit" measurement is replayable as models drift.
4. **The redaction-gate scanner** + the access-controlled private store.

## Out of scope / non-goals

- **No identity output.** No name, no demographic label, no "same person" verdict —
  only scored evidence strength + aggregate categories.
- **No live / unconsented population scan**, and no shipped harness capable of one.
- **No retention or publication of raw person-identifying outputs** (the E3 non-leak
  rule); only scored outcomes and aggregate categories leave the lab.
- **No demographic labels** even as an intermediate public artifact; E2 reports
  proximity-to-a-named-reference-distribution and its confound decomposition, not a
  per-author attribute.
- **Not a replacement** for `general_imposters` (closed-set, in-scope) or the planned
  within-document multi-author segmentation (discontinuity, not "different authors").

## Open questions

- Which LLMs to include as the E3 contrast, and the exact prompt/version freeze.
- Whether the strong foil is the classical GI stack alone or also the neural
  `authorship_embedding` foil (stronger, heavier).
- The consent/authorization record shape for E2/E3 owned corpora (a v2 of the
  `voice_profile` privacy ratchet) and the exact person-identifying-token scanner the
  redaction gate uses.
- (Decided 2026-06-07: a user-facing README "What it isn't" line is **not** added for
  now — the refusal lives in ROADMAP + this spec.)
