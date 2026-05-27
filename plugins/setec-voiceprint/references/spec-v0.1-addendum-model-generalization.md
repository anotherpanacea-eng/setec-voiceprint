# SPEC v0.1 addendum: model generalization and operator interface

**Filed:** 27 May 2026
**For:** Future revision of `SPEC_external_mirror_discrimination.md` to v0.2
**Triggered by:** *Magnifica Humanitas* GPT-5 kit refinement, May 2026

---

## Summary

SPEC v0.1's Design 2 protocol currently describes the mirror model in brand-specific terms (e.g., "Claude Opus 4.7," "GPT-5"). The protocol's actual methodological requirement is brand-agnostic. Three refinements should be folded into v0.2.

## Refinement 1: Training cutoff requirement

The load-bearing constraint on the mirror model concerns the model's training cutoff, not the lab that built it. Specifically, the cutoff date must precede the target document's publication date.

**Why it matters.** If the target document is in the model's training data, the model may "predict" by recall rather than by distributional inference. The mirror test then measures memorization, not distributional placement.

**Correct phrasing for v0.2.** "The mirror model's published training cutoff date MUST precede the target document's publication date. If a human-control document is also used, the cutoff should also precede the control's publication date; otherwise the control over-estimates the model's predictive accuracy on verified-human prose, and the discrimination gap becomes a conservative estimate."

**Practical guidance.** Frontier models typically publish cutoffs 6-12 months before the model's own public release date, but the two are distinct: only the cutoff is load-bearing. Operators should consult the model card or the lab's documentation for the specific cutoff, not rely on release-date heuristics.

**Edge case.** For targets published within weeks of the model's release (the *Magnifica Humanitas* case: encyclical released 25 May 2026; Claude Opus 4.7's training cutoff was January 2026), the cutoff comfortably precedes the target. Verify the specific dates from the model card.

## Refinement 2: Operator interface generalization

SPEC v0.1's Design 2 step 5 and Template T4 implicitly assume manual operator interaction with the mirror model (one chat session per window). In May 2026, multiple frontier-model interfaces support agent orchestration:

- **GPT Codex** (OpenAI): serial subagent spawning
- **Claude Code** (Anthropic): parallel and serial subagent spawning via the Task tool
- **Cline / other agentic IDE plugins**: various

The methodological requirement is per-prompt context isolation, not manual operation. Any interface that provides genuine per-context isolation (no shared state between subagents, no leaking of one prompt's context into another's evaluation) is acceptable.

**Correct phrasing for v0.2.** "The K windows MUST be evaluated by the mirror model in isolated contexts: no shared conversational state, no access to other windows' prefixes or targets, no access to the diagnostic-features list or the operator's hypothesis-bearing project documents. Acceptable interfaces include: manual fresh-chat sessions per window; serial agent-spawning frameworks (e.g., GPT Codex); parallel agent-spawning frameworks (e.g., Claude Code Task tool). Operators should document which interface and which model version was used."

**Caveat on agent orchestration.** If the agentic interface itself reads the kit's hypothesis-bearing files (README, run-log-after, SPEC) as part of its task orchestration, the resulting run is no longer fully blind at the agent-routing layer. Document the level of orchestration-layer access in the run log. The blinding standard is two-layered: model context isolation AND orchestration-layer isolation, where the latter is harder to guarantee.

## Refinement 3 (extends Refinement 1 to controls): Post-cutoff baseline document for future rounds

The *Magnifica Humanitas* run used *Dilexit Nos* (October 2024) as the Francis-baseline control. Several frontier models have cutoffs after October 2024, making *Dilexit Nos* plausibly in-training. The discrimination gap reported (Magnifica-Dilexit) is therefore conservative.

A future iteration should add a TRULY post-cutoff baseline: a verified-human document published after all relevant model cutoffs, in matched register. Candidates: a 2026-published academic essay or magazine essay in formal Catholic-doctrinal-adjacent register, by a verified human author with documented online footprint pre-2024.

This refinement is not blocking the current Magnifica analysis but should be standard practice in future runs.

---

## Status

These refinements are advisory for v0.2. The *Magnifica Humanitas* kits ship with the refinements documented in their READMEs and the Codex workflow doc. Operators running the kits as currently shipped get the v0.2 standard even before the formal SPEC revision lands.

Next SPEC revision (v0.2) should incorporate all three refinements plus any others surfaced by the GPT-5 and adversarial runs once they complete.
