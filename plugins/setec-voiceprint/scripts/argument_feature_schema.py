#!/usr/bin/env python3
"""argument_feature_schema.py — ArgScope Layer A: the B1/B2 labeling taxonomies
plus the anchored derived signals.

Source of truth: Kim, Chang, Pham & Iyyer 2026, "Argument Collapse: LLMs
Flatten Long-Form Public Debate" (arXiv:2606.01736v3), §4.1–4.2 + Tables 26/27.
ArgScope Layer A is the argument-domain sibling of StoryScope's
narrative-decision audit: it scores *how an argument is built* (structural arc,
discourse-mode mix), not how its sentences are phrased.

Unlike StoryScope's whole-document feature judgments, ArgScope's two net-new
bundles are computed from a PER-PARAGRAPH label sequence:

  * **B1 — structural arc.** Each paragraph gets one of 8 argumentative ROLES;
    the anchored signals are transition-matrix rates over the role sequence
    (``support→proposal``, ``support→support``) plus a thesis-opening tendency.
  * **B2 — discourse mode.** Each paragraph gets one of 4 discourse MODES; the
    anchored signal is the ``argumentation`` share.

So the judge (``argument_judge``) labels a *sequence* of paragraphs; this module
defines the label space (for the judge prompt) and the derived signals (with the
paper's human/LLM anchors) that the surface computes from that sequence.

Load-bearing framing notes (carried into the consumer claim-license):

1. **Not provenance, not quality.** The paper measures argumentative
   *diversity*, not quality or accuracy, and does not claim human arguments are
   better. No "human = better." A human who argues thesis-first in an abstract
   register scores the same and is not thereby worse.

2. **Register-bound anchors.** The means are public-debate-forum numbers (NYT
   *Room for Debate* ~352w; *Boston Review* ~1,150w). The paper's Limitations
   warn they may NOT transfer to research/legal/policy writing — exactly the
   high-stakes genres a consumer cares about. The anchors are **directional
   reference, NOT thresholds**; the surface ships an unconditionally
   ``uncalibrated`` band and a register-match list keyed to ``op-ed``.

3. **B1/B2 only are anchored.** B3 (abstraction) and B4 (stance) are deterministic
   reuse signals (``argmove_profile`` / ``stance_modality_audit`` /
   ``agency_abstraction_audit``), ``heuristic`` with no anchor; they live in the
   surface, not this schema. The two net-new dynamic/arc signals
   (disappearing-guard, discounting-straw-men) are deferred to a follow-up.

4. **D1 — the labelers are an LLM judge, not regexes.** Argumentative role and
   discourse mode are genuine classification tasks the paper itself ran with an
   LLM judge (``gemini-3-flash``); marker lexicons are at most few-shot priors,
   never a standalone detector (the AGD substitution-test caveat). The
   ``mock`` judge keeps CI deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ROLE_OPTIONS",
    "ROLE_DESCRIPTIONS",
    "MODE_OPTIONS",
    "MODE_DESCRIPTIONS",
    "DerivedSignal",
    "DERIVED_SIGNALS",
    "BUNDLE_LABELS",
    "iter_anchored_signals",
]

Leaning = Literal["ai", "human"]
SignalKind = Literal["transition_rate", "mode_share", "opening_tendency"]

# ---- B1: the 8-way argumentative paragraph-role taxonomy (paper §4.1) -------
# The role each paragraph plays in the argument's construction. Cross-walked
# against Toulmin / Walton / stasis / the classical oration (calibration spec
# §3.5); the descriptions double as the judge's per-role labeling guidance.
ROLE_OPTIONS: tuple[str, ...] = (
    "thesis",
    "support",
    "counterclaim",
    "rebuttal",
    "concession",
    "reframing",
    "implication",
    "proposal",
)

ROLE_DESCRIPTIONS: dict[str, str] = {
    "thesis": "States or restates the central claim/position the essay argues for.",
    "support": "Offers evidence, reasons, or examples advancing the thesis.",
    "counterclaim": "Raises an opposing position or objection to the thesis.",
    "rebuttal": "Answers/refutes a counterclaim or objection.",
    "concession": "Grants a point to the other side without abandoning the thesis.",
    "reframing": "Recasts the question, terms, or stakes of the debate.",
    "implication": "Draws out consequences or significance of the argument so far.",
    "proposal": "Recommends an action, policy, or solution.",
}

# ---- B2: the 4-way discourse-mode taxonomy (paper §4.1) ---------------------
MODE_OPTIONS: tuple[str, ...] = (
    "argumentation",
    "exposition",
    "narration",
    "description",
)

MODE_DESCRIPTIONS: dict[str, str] = {
    "argumentation": "Advances claims with reasons/evidence; the paragraph argues.",
    "exposition": "Explains or informs (background, definitions) without arguing.",
    "narration": "Recounts events or a story in sequence.",
    "description": "Depicts a scene, object, or state; sensory or static detail.",
}

BUNDLE_LABELS: dict[str, str] = {
    "B1_structural_arc": "B1 — Structural arc (paragraph-role transitions)",
    "B2_discourse_mode": "B2 — Discourse-mode mix",
}


@dataclass(frozen=True)
class DerivedSignal:
    """One anchored signal computed from the per-paragraph label sequence.

    ``human_mean`` / ``ai_mean`` are the paper's reported group means, stored as
    proportions in [0, 1] (the paper reports percentages). They are **literature
    anchors, register-bound to public-debate forums** — directional reference,
    never thresholds. ``anchored=False`` marks a signal the paper reports only
    directionally (no clean human/LLM pair): ``human_mean``/``ai_mean`` are None
    and only ``leaning`` (the reported direction) is carried.

    ``anchor_register`` records which corpus the means come from (the
    register-binding is the point); ``notes`` carries secondary-register figures
    and range caveats so nothing anchored is silently single-valued.
    """

    key: str
    label: str
    bundle: str
    kind: SignalKind
    leaning: Leaning
    anchored: bool
    human_mean: float | None
    ai_mean: float | None
    anchor_register: str
    notes: str

    @property
    def gap(self) -> float | None:
        """human_mean − ai_mean (sign matches ``leaning``); None when unanchored.
        Negative = LLM-elevated; positive = human-elevated."""
        if self.human_mean is None or self.ai_mean is None:
            return None
        return self.human_mean - self.ai_mean


# ---- the anchored derived signals (paper §4.1–4.2, Tables 26/27) -----------
# Transcribed to the paper's reported proportions. support→proposal and
# support→support are role-transition rates; argumentation_share is a mode
# share. Primary anchor = NYT Room for Debate; Boston Review figures and the
# reported ranges are carried in `notes` (never dropped).
DERIVED_SIGNALS: tuple[DerivedSignal, ...] = (
    DerivedSignal(
        key="support_to_proposal_rate",
        label="support→proposal transition rate",
        bundle="B1_structural_arc",
        kind="transition_rate",
        leaning="ai",  # LLM-elevated: 29.4% vs 12.3% human (NYT)
        anchored=True,
        human_mean=0.123,
        ai_mean=0.294,
        anchor_register="NYT Room for Debate",
        notes=(
            "LLM essays pivot support→proposal far more often. Boston Review "
            "(secondary): 7.2% human / 17.7% LLM — same direction, lower base "
            "rate at longer length. Register-bound to public-debate forums."
        ),
    ),
    DerivedSignal(
        key="support_to_support_rate",
        label="support→support transition rate",
        bundle="B1_structural_arc",
        kind="transition_rate",
        leaning="human",  # human-elevated: humans sustain support chains
        anchored=True,
        human_mean=0.525,  # midpoint of the reported 50.5–54.5% human range
        ai_mean=0.329,     # midpoint of the reported 29.7–36.0% LLM range
        anchor_register="NYT Room for Debate + Boston Review",
        notes=(
            "Humans sustain longer support→support chains (50.5–54.5% human vs "
            "29.7–36.0% LLM across NYT/BR); means stored are the range midpoints. "
            "Directional reference, not a threshold."
        ),
    ),
    DerivedSignal(
        key="thesis_opening_tendency",
        label="thesis-first opening tendency",
        bundle="B1_structural_arc",
        kind="opening_tendency",
        leaning="ai",  # LLMs open thesis-first more often (directional only)
        anchored=False,
        human_mean=None,
        ai_mean=None,
        anchor_register="NYT Room for Debate (directional)",
        notes=(
            "The paper reports a thesis-opening tendency only directionally "
            "(LLM essays more often open by stating the thesis); no clean "
            "human/LLM mean pair is transcribable, so this is directional with "
            "no numeric anchor."
        ),
    ),
    DerivedSignal(
        key="argumentation_share",
        label="argumentation discourse-mode share",
        bundle="B2_discourse_mode",
        kind="mode_share",
        leaning="ai",  # LLM-elevated: 89.7% vs 71.5% human
        anchored=True,
        human_mean=0.715,
        ai_mean=0.897,
        anchor_register="NYT Room for Debate + Boston Review",
        notes=(
            "LLM essays are almost entirely argumentation (89.7% diversified, "
            "89.1% position-guided) vs 71.5% human — humans interleave more "
            "exposition/narration/description. Stored ai_mean = the diversified "
            "condition (0.897)."
        ),
    ),
)


def iter_anchored_signals():
    """Yield each DerivedSignal that carries a numeric anchor (anchored=True)."""
    for s in DERIVED_SIGNALS:
        if s.anchored:
            yield s


# ---- import-time self-check (catch transcription mistakes early) -----------
def _self_check() -> None:
    if len(ROLE_OPTIONS) != 8:
        raise RuntimeError(f"B1 role taxonomy must be 8-way; got {len(ROLE_OPTIONS)}")
    if len(MODE_OPTIONS) != 4:
        raise RuntimeError(f"B2 mode taxonomy must be 4-way; got {len(MODE_OPTIONS)}")
    if set(ROLE_OPTIONS) != set(ROLE_DESCRIPTIONS):
        raise RuntimeError("ROLE_DESCRIPTIONS must cover exactly ROLE_OPTIONS")
    if set(MODE_OPTIONS) != set(MODE_DESCRIPTIONS):
        raise RuntimeError("MODE_DESCRIPTIONS must cover exactly MODE_OPTIONS")
    keys = {s.key for s in DERIVED_SIGNALS}
    if len(keys) != len(DERIVED_SIGNALS):
        raise RuntimeError("DERIVED_SIGNALS contains duplicate keys")
    for s in DERIVED_SIGNALS:
        if s.bundle not in BUNDLE_LABELS:
            raise RuntimeError(f"signal {s.key}: unknown bundle {s.bundle!r}")
        if s.anchored:
            if s.human_mean is None or s.ai_mean is None:
                raise RuntimeError(f"signal {s.key}: anchored but missing a mean")
            for m in (s.human_mean, s.ai_mean):
                if not (0.0 <= m <= 1.0):
                    raise RuntimeError(
                        f"signal {s.key}: proportion {m} out of [0, 1]"
                    )
            gap = s.gap
            if gap == 0:
                # Equal human/AI means → no signal, and the surface's
                # contribution would be 0/0. Reject so a degenerate anchor
                # can't reach the (denom==0 → 0.0) path with a fabricated value.
                raise RuntimeError(
                    f"signal {s.key}: anchored with equal human/AI means "
                    f"({s.human_mean}); that carries no direction"
                )
            gap_sign = "human" if gap > 0 else "ai"
            if gap_sign != s.leaning:
                raise RuntimeError(
                    f"signal {s.key}: leaning {s.leaning!r} inconsistent with "
                    f"gap sign {gap:+.3f}"
                )
        else:
            if s.human_mean is not None or s.ai_mean is not None:
                raise RuntimeError(
                    f"signal {s.key}: unanchored but carries a mean"
                )


_self_check()
