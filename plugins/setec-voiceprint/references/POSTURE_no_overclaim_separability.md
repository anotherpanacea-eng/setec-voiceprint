# Posture — no over-claim of AI/human separability

**Root:** Sadasivan et al. 2023, "Can AI-Generated Text be Reliably Detected?"
([arXiv:2303.11156](https://arxiv.org/abs/2303.11156)).

This is a **standing posture doc**, not a feature. It states what the SETEC
validation spine and the `adversarial_robustness_card` will **not** claim. It
is paired with a structural absence test
(`test_no_overclaim_separability_strings` in
`scripts/tests/test_no_overclaim_separability.py`) that makes the absence
**checkable, not promised**.

## The posture

No AUC, conformal prediction set, conformal FPR-bound threshold, PHD scalar,
robustness card, or any other SETEC output licenses the claim that:

- "this text **is AI**" (or is human),
- a detector **reliably detects AI** / **reliably separates AI from human**, or
- any text is **AI-detectable** as a settled property.

The grounding result (arXiv:2303.11156) is that a **determined
paraphraser/humanizer can collapse any of these signals** — the gap between AI
and human prose can be driven arbitrarily small, so no detector is reliable
against an adaptive adversary. SETEC's outputs are therefore **evidence under
stated conditions** (a named corpus, a fixed embedding model, an operator-
supplied calibration set, an exchangeability assumption), **never a reliability
claim** about AI/human separability in general.

This is the same spine every SETEC surface already carries in its
`does_not_license` clause; this doc names it once, centrally, for the four
reporting surfaces the test covers:

- `validation_harness` — reports AUC/PR with bootstrap CIs, an FPR-target
  operating point, and the eval-discipline decompositions (topic leakage,
  Simpson inversion). The decompositions **lower** a leaked number; they never
  assert separability.
- `conformal_gate` — emits a p-value / prediction set / FPR-bound threshold.
  An empty *or* full prediction set is a **licensed abstention**. The
  FPR-bound is a reference-class false-positive **ceiling**, not P(AI).
- `intrinsic_dimension_audit` — a PHD scalar, **uncalibrated**: no band, no
  threshold, no verdict. Short-PHD widens the estimate; it does not license one.
- `adversarial_robustness_card` — fixture-specific stability labels. A signal
  "stable" under one fixture says nothing about a different paraphraser.

## Why a doc + a test, not a banner

A repeated over-claim banner in every report trains readers to ignore it
(alert fatigue — see the spec's "considered & rejected"). Instead the posture
is enforced by **what the outputs do not say**: the structural test scans the
rendered output of all four surfaces for a small closed list of
**verdict-phrase** constructions and fails if any appears outside an explicit
refusal/caveat context. The denylist is phrase-level (e.g. "this text is AI",
"reliably detects ai", "reliably separates ai from human", "ai-detectable"),
**not** bare words like "reliable" or "separable" — those appear in legitimate
caveats (e.g. "too few embedding units for a reliable log-log scaling fit") and
are not verdicts.
