# 21-attribution-refusal-lab — research brief / anti-goal note (NOT a dispatchable spec)

> A **refusal-curve laboratory** for the three person-identifying capabilities the
> framework refuses to ship as verdicts — open-set author attribution, demographic
> profiling, and cross-document identity linkage. The deliverable is **not** an
> attribution tool. It is an instrument that maps *where the signal exists and where
> it is bullshit*, and plots an untooled LLM's confident attribution against that
> curve. Strength-of-evidence only; never a name or a label.

- **Type:** **Research brief / anti-goal note — NOT a dispatchable spec.** By design it carries no Contract / Test-contract (no task surface, CLI / harness entrypoint, JSON shape, claim-license, or `capabilities.d/` entry), so the `specs/01` spec→build→review loop does not apply to it as written. It records doctrine + a gated research agenda. It becomes builder-dispatchable only if **promoted** to a full spec carrying the contract the #177 review enumerates: reference / ground-truth data manifests, the public/private artifact boundary, acceptance metrics, the refusal/redaction tests (E3 non-leak rule below), and explicit go/no-go gates.
- **Status:** Research brief — gated on the strong-foil resourcing decision below; nothing ships until promotion.
- **Tier:** research-grade, multi-session. Mostly stdlib + the framework's existing Burrows-Delta / General-Imposters machinery; the LLM-contrast leg needs operator-side LLM access.
- **GPU required:** no (the classical foil is CPU; an optional neural-embedding foil reuses the `authorship_embedding` surface's posture).
- **License decision:** N/A — clean-room. Method is classical stylometry (Mosteller & Wallace 1963; Koppel/Schler/Argamon; Kestemont et al. 2016 GI). Ground-truth corpora are public shared tasks (PAN author-identification / author-profiling) + the Federalist set + held-out same-author pairs from owned corpora; no restricted artifact is vendored.

## Motivation

This spec is the constructive half of the 2026-06-07 capability-whitespace
discussion (ROADMAP → "Explicit anti-goals", the *no-verdict-about-the-person*
cluster). Three capabilities — open-set 1-to-many attribution, demographic
profiling, sock-puppet linkage — are a short step from shipped machinery
(verification, GI, `idiolect_detector`, the impostor pools), and untooled LLMs
will perform all three *confidently and untraceably* on request. The framework's
foundational claim ("Why no verdict": confident output ≠ licensed evidence) was
built for *is-this-AI*; this lab extends it to *who-is-the-author*.

The point is **not** to build the capability cautiously. It is to build the
instrument that measures the capability's **absence** — and to use the LLM's
overconfidence as the contrast that makes the absence legible. The natural and
*encouraged* outcome is a null or weak result. A tool whose headline is "here is
how little this licenses, and here is the untooled model asserting more" is an
antibody, not a weapon; the incentive gradient is inverted relative to a product
that names people.

**Orthogonality / relation to shipped surfaces.** Closed-set, register-matched,
*known*-candidate attribution with a gray-zone refusal already ships as
`general_imposters` — that stays. This lab studies what happens when the
constraints that make GI honest are *removed* (unknown/large/unmatched candidate
pool; group-distribution reference instead of an individual's prose), and reports
the degradation, not an identity.

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
population, cross-topic, short text) is exactly the regime where the signal is
gone. The **curve is the debunk, drawn.** Uses `general_imposters`,
`voice_distance`, and the impostor pools directly; no new detector.

### E2 — The demographic confound-control (profiling evaporates under matching)

Build a group reference (e.g. an L1-Russian vs L1-English corpus) and report the
target's **proximity to the group distribution** — *then* re-run against
topic-, formality-, and era-matched controls. Expected honest result: the apparent
L1 signal collapses into `{topic, formality, education, era}` once controlled,
consistent with the cross-corpus polarity-volatility finding (a different reference
corpus gives a different "distribution"). The debunk here is not "the tool is weak"
but "**the construct doesn't survive controls**" — while the untooled LLM still
hands you a confident L1/age/gender guess. This is the weakest of the three as a
measurement and the strongest as a debunk; go in expecting the null.

### E3 — The paired LLM contrast (the deliverable)

On inputs with **known ground truth**, run the same target through:

1. the **calibrated foil** (strength-of-evidence; abstains / LR ≈ 1 / wide gray zone
   when the regime is hostile),
2. the **untooled LLM** (one or more; prompted exactly as a naive user would —
   "who wrote this? / profile this author"),
3. **ground truth** (which, in the hostile regime, says the foil was *right* to
   abstain and the LLM was confidently wrong).

The three-way table is the publishable artifact: it shows the LLM's confidence is
*uncorrelated with* (or anti-correlated with) the licensed evidence exactly where
it matters. Repeat across the regimes E1/E2 map (pool size, topic drift, text
length) to show *where* the LLM's bullshit is worst.

**E3 non-leak rule (safety boundary — testable; a precondition for E3 ever running).**
E3 is the one experiment that must *prompt* the foil LLM to produce the very thing
the framework refuses — a name, a demographic label, a same-person judgment. Those
raw outputs are evidence to be **scored**, not artifacts to be kept. Without an
explicit rule, a future lab could log or publish person-identifying material while
still claiming the final SETEC output is strength-of-evidence only. The rule, stated
as builder invariants:

1. **Raw person-identifying outputs are private/redacted.** Every raw LLM (and foil)
   identity/profile output — candidate name, demographic label, "same author as X" —
   is written only to an access-controlled private store, never to any committed or
   published artifact.
2. **Public artifacts carry only aggregate categories.** Tables, figures, and
   write-ups contain only aggregate error/confidence categories — correct / incorrect
   / abstain counts, calibration bins, LR / odds *distributions* — and never a name,
   a demographic label, or a same-person verdict for any individual.
3. **A redaction test gates publication (go/no-go).** Before any artifact leaves the
   lab, an automated scan asserts zero person-identifying tokens (candidate-set names,
   demographic labels, "same author as …") in the public set; a hit **fails the
   build**. This is the E3 analogue of the live-scan refusal, and it is a gate, not a
   guideline.
4. **Only the score leaves.** What the foil and the LLM *said* about a person stays
   private; what leaves the lab is the scored outcome (right / wrong / abstain)
   against ground truth.

## Output discipline & load-bearing constraints

- **Strength-of-evidence, never a name or a label.** LR / posterior odds /
  proximity-to-distribution with explicit uncertainty. Mosteller-Wallace, not
  "Madison did it." If any measurement-only artifact ever ships from this lab, it
  ships on an *existing* surface (`voice_coherence` for attribution-as-LR;
  `validation` for the refusal-curve harness) with a claim-license that refuses the
  identity/label leap — it does **not** register a new person-identifying surface.
- **Consented or held-out reference only — and no live open-set mode, ever.** This
  is the bright line between "Federalist lab" and "Reddit de-anonymizer." Open-set
  is studied exclusively against synthetic / held-out populations where the lab owns
  the ground truth; the apparatus refuses to run against an unconsented live corpus
  (same ratchet shape as `voice_profile` refusing public output paths). Publish the
  *finding* and the *method*; do **not** ship the live-population-scan harness, even
  when it has been measured to be weak — the rig is reusable with the abstention
  turned off.
- **The foil must be strong on purpose.** This is the one place the framework's
  ship-uncalibrated-and-weak default is *inverted*. A null result only debunks the
  LLM if it means "even a competent, near-SOTA attempt fails," not "my
  implementation was weak." A half-built foil manufactures a false null that *helps*
  the bullshitters. Treat the foil's strength as the load-bearing resourcing
  decision; if it can't be done properly, don't ship the lab.

## What ships from this brief

**Nothing executable.** This brief registers **no** task surface, ships **no**
script, and adds **no** `capabilities.d/` entry. Because it is a research brief and
not a dispatchable spec, the `specs/01` spec→build→review loop does not apply to it
as written. Its outputs are: (1) the named anti-goal in ROADMAP, (2) this protocol +
the E3 non-leak rule. A future lab "build" requires **promotion to a full spec**
first — one that adds the Contract / Test-contract the #177 review enumerated
(reference / ground-truth data manifests, the public/private artifact boundary, the
redaction test as a CI gate, acceptance metrics, go/no-go gates) — and is then still
gated behind the strong-foil resourcing decision.

## Out of scope / non-goals

- **No identity output.** No name, no demographic label, no "same person" verdict —
  only scored evidence strength with its limits.
- **No live / unconsented population scan**, and no shipped harness capable of one.
- **No retention or publication of raw person-identifying LLM/foil outputs** (the E3
  non-leak rule); only scored outcomes and aggregate categories leave the lab.
- **No demographic labels** even as an intermediate; E2 reports proximity-to-a-named-
  reference-distribution and its confound decomposition, not a per-author attribute.
- **Not a replacement** for `general_imposters` (closed-set, in-scope) or the planned
  within-document multi-author segmentation (discontinuity, not "different authors").

## Open questions

- Which LLMs to include as the E3 contrast, and how to freeze prompts/versions so the
  "bullshit" measurement is replayable as the models drift.
- Whether the strong foil is the classical GI stack alone or also a neural-embedding
  foil (reusing `authorship_embedding`); the latter is a stronger foil and a heavier
  build.
- The consent/authorization record shape for E2/E3 owned corpora (a v2 of the
  `voice_profile` privacy ratchet), and the exact form of the redaction-test scanner
  that enforces the E3 non-leak rule.
- (Decided 2026-06-07: a user-facing README "What it isn't" line is **not** added for
  now — the refusal lives in ROADMAP + this brief.)
