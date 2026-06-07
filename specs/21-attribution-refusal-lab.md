# 21-attribution-refusal-lab

> A **refusal-curve laboratory** for the three person-identifying capabilities the
> framework refuses to ship as verdicts — open-set author attribution, demographic
> profiling, and cross-document identity linkage. The deliverable is **not** an
> attribution tool. It is an instrument that maps *where the signal exists and where
> it is bullshit*, and plots an untooled LLM's confident attribution against that
> curve. Strength-of-evidence only; never a name or a label.

- **Status:** Spec (research). **Gated** on the strong-foil resourcing decision below — no surface ships from this brief.
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

## Output discipline & the two load-bearing constraints

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

**Nothing executable, in v1.** This brief registers **no** task surface, ships
**no** script, and adds **no** `capabilities.d/` entry. Its outputs are: (1) the
named anti-goal in ROADMAP, (2) this protocol, (3) — when resourced — a research
write-up + reference-corpus manifests + a refusal-curve harness built from existing
surfaces. The "build" step of the spec→review→build→review workflow is the lab
itself and is deliberately deferred behind the strong-foil decision.

## Out of scope / non-goals

- **No identity output.** No name, no demographic label, no "same person" verdict —
  only scored evidence strength with its limits.
- **No live / unconsented population scan**, and no shipped harness capable of one.
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
- Whether a README "What it isn't" line ("not an author de-anonymizer / demographic
  profiler") should accompany the ROADMAP anti-goal, given the existing anti-goals'
  "name the refusal where users see it" logic.
- The consent/authorization record shape for E2/E3 owned corpora (a v2 of the
  `voice_profile` privacy ratchet).
