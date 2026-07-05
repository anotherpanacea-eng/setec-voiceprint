### Added

**`position_pair_register.py` — same-question passage-pair register (new
`position_pair_register` surface, LLM-judge, `experimental`, uncalibrated).** Given
ONE long nonfiction argument-shaped work and a judge backend, the
`position_pair_register` capability surfaces passage **pairs that address the same
question Q** — each pair carrying a neutral interrogative `Q` and both passages'
verbatim loci (`{doc, start_char, end_char, quote}`), emitted in **document order**.

**It asserts NO relation between the paired passages** — not agreement, not
conflict, not contradiction, not tension, not which passage is right. The model only
points at two passages that share a question; the **human reads both and owns 100%
of the conflict call.** This is the fleet's deliberate NON-step across the
content-verdict wall: the task is split so the model *cannot* assert opposition.

Two mechanical, posture-critical gates carry the firewall:

- **The F4 Q-string gate.** A surfaced `question` must be interrogative in **form**
  (ends with `?`, opens with an interrogative/auxiliary token) AND free of relation
  vocabulary (a case-folded substring scan against a frozen banned set). A Q that
  fails either check has its pair **REFUSED** — dropped, warned, and counted in a
  disclosure (`pairs_refused_q_gate`). The form check is **syntax-only** and gives
  zero protection against loaded/presuppositional questions (the human terminus is
  the guarantee); the substring scan conservatively over-refuses some legitimate
  topics ("counterargument", "incompatibilist", "conflict of interest") by design.
- **The F3 runtime banned-key walk.** Before the envelope is returned, a recursive
  key walk (walk shape from PR #298's
  `test_envelope_carries_no_verdict_keys_recursive`) **raises** on any relation key
  anywhere in the envelope, or any generic verdict key inside `results.pairs`.

Every surfaced locus is **verbatim-bound at validation**: each side's quote is
verified exactly against the document (`text[start:end] == quote`). A quote whose
offsets are wrong is **re-tightened** to the quote's real location; a quote that
appears **nowhere** in the document is a fabrication and its whole pair is
**dropped** with a warning — so an invented quote never reaches the human as
"verbatim evidence". Matching is exact (no punctuation folding — that tolerance is
the consumer's gate). Duplicate quotes bind to the occurrence **nearest the claimed
span** (an occurrence overlapping the claimed start wins over a later duplicate),
and a pair whose two sides resolve to the **same passage** is dropped — a pair must
point at two distinct passages.

Pair caps (default 12/question, 60/work, operator-tunable) are a **disclosure**:
over-cap survivors are the first by document order and the dropped loci are logged
(`pairs_dropped_cap_loci`) — never a tension/confidence ranking. Backends: `mock` /
`manifest` (deterministic, CI-safe) and `anthropic`/`openai`/`gemini`/`agent_host`
(live). Uncalibrated: no run-to-run determinism guarantee for live backends. The
`ClaimLicense` refuses (a) any claim the passages ARE in conflict, (b) which passage
is right, (c) exhaustiveness (absence of a pair is not consistency), and (d) fiction
/ narrator application (v1 register scope: nonfiction-argument only). Anchors:
*ContraDoc* (**arXiv:2311.09182**, contradiction-type taxonomy) and *BeliefShift*
(**arXiv:2603.23848**, position-drift framing).
