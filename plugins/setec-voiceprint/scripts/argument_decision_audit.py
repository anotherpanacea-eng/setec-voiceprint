#!/usr/bin/env python3
"""argument_decision_audit.py — ArgScope Layer A: the argument-decision audit
surface (the argument-domain sibling of StoryScope's narrative-decision audit).

Scores HOW an argument is built against the collapse-tells reported in Kim,
Chang, Pham & Iyyer 2026, "Argument Collapse: LLMs Flatten Long-Form Public
Debate" (arXiv:2606.01736v3) — its structural arc (paragraph-role transitions,
B1) and discourse-mode mix (B2). It is NOT a provenance detector and NOT a
quality judgment: the paper measures argumentative *diversity*, not quality, and
does not claim human arguments are better. No "human = better."

The judge (`argument_judge`) labels a per-paragraph SEQUENCE (one role∈8 +
mode∈4 per paragraph); this surface computes the paper-anchored signals from
that sequence:
  * B1 support→proposal rate, support→support rate (row-normalized from the
    `support` role), thesis-opening tendency (directional, unanchored);
  * B2 argumentation discourse-mode share.
Each anchored signal's contribution is 1.0 at the paper's human mean and 0.0 at
its LLM mean; the aggregate is the mean contribution. The band is
**unconditionally `uncalibrated`** (the anchors are register-bound to
public-debate forums — directional reference, never thresholds); the consumer
(APODICTIC) maps the target's genre to matched/adjacent/distant and downgrades.

SCOPE (Inc A1): the anchorable B1/B2 judge core (the contributions + aggregate)
PLUS B3/B4 deterministic reuse — abstraction + stance + AGD marker densities for
the target (via `argmove_profile.argmove_vector`), surfaced as descriptive
`reused_signals` (`heuristic`, NO anchor, not in the aggregate; D2/D5). The two
dynamic/arc signals (disappearing-guard hedging-drift; discounting-straw-men)
shipped subsequently as B5 — `heuristic`, directional-only TEXTURE observations
excluded from the aggregate (see `compute_collapse_dynamics` + the B5 derivation
below), so they leave the score and the `uncalibrated` band unchanged. The
envelope is additive (schema 1.0).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argument_feature_schema import (  # type: ignore
    BUNDLE_LABELS,
    DERIVED_SIGNALS,
    DerivedSignal,
)
from argument_judge import (  # type: ignore
    JudgeError,
    build_judge,
    fingerprint_prompt,
    utc_now,
    validate_doc_level,
    validate_labels,
)
from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "argument_decision_audit"
TOOL_NAME = "argument_decision_audit"
SCRIPT_VERSION = "0.2.0"  # C0: --register / --baseline-dir register-baseline plumbing

MIN_ARGUMENT_WORDS = 300       # argument-bearing structure needs length
MIN_PARAGRAPHS = 3             # transition-matrix signals need a multi-paragraph arc
PAPER_OPED_MEAN_WORDS = 352    # NYT Room for Debate mean (Boston Review ~1,150)

DEFAULT_LICENSES = (
    "Reports how the target's argumentative STRUCTURE compares to the human / "
    "LLM group means Kim et al. 2026 (\"Argument Collapse\", arXiv:2606.01736) "
    "reported over public-debate-forum essays (NYT Room for Debate ~352w; Boston "
    "Review ~1,150w): the B1 paragraph-role transition rates (support→proposal, "
    "support→support) and the B2 argumentation discourse-mode share. Each "
    "anchored signal's contribution is 1.0 at the paper's human mean and 0.0 at "
    "its LLM mean; the aggregate is the mean per-signal contribution. Role/mode "
    "labels come from a pluggable LLM judge (read judge.provenance)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license an AI / human authorship verdict — no signal here means "
    "\"written by AI\"; a human arguing thesis-first in an abstract register "
    "scores the same. Does not license a quality judgment: the paper measures "
    "argumentative DIVERSITY, not quality or accuracy, and does not claim human "
    "arguments are better (no \"human = better\", and no \"concrete = better\"). "
    "The anchors are REGISTER-BOUND to public-debate forums; the paper's "
    "Limitations warn they may not transfer to research / legal / policy writing "
    "(the consumer's `distant` tier), so the band is unconditionally "
    "`uncalibrated` and a register mismatch downgrades to structural-signals-only. "
    "Temporal confound: the human/LLM means are a snapshot of the models the "
    "paper studied; the gap will shift as models change, so the anchors are a "
    "dated reference, not a stable threshold. Judge fidelity varies by backend: "
    "`mock` is a test stub (do not infer from it); `manifest` is only as good as "
    "whatever produced the labels (unverifiable by this surface — read "
    "judge.provenance.model); the API backends are a faithful per-paragraph "
    "labeler. Does not run a soundness / warrant / fairness verdict (that is "
    "dialectical-clarity / banister, which this surface may PRE-FLAG but never "
    "adjudicates). B3/B4 abstraction & stance ship as descriptive `reused_signals` "
    "(`heuristic`, NO numeric anchor BY DESIGN — marker density is a different "
    "construct from the paper's judge-rated per-essay stance strength, D5; not in "
    "the aggregate). The two B5 dynamic collapse signals (disappearing-guard, "
    "discounting-straw-men) ship as `heuristic`, directional-only TEXTURE "
    "observations of within-document hedging-drift and decoy-objection patterns "
    "(judge-derived from per-paragraph guard_strength/claim_ref + counterclaim "
    "objection_strength + a doc-level strongest-objection-engaged field): they "
    "carry NO numeric anchor (the paper reports them only qualitatively) and NO "
    "measured discrimination, are EXCLUDED from the aggregate (contribution=null) "
    "and from the verdict band, and return null (never a fabricated False) when "
    "the evidence is absent. They do NOT adjudicate fairness or soundness (that "
    "is banister / dialectical-clarity) and license NO provenance or quality "
    "verdict; a True discounting-straw-men flag at most signals that a "
    "dialectical-clarity OB5 run would be informative — the surface never "
    "adjudicates it."
)


# ---------- paragraph splitting + signal computation ----------------

def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; strip; drop empties. The judge labels exactly
    these paragraphs (aligned by index)."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def compute_arc_signals(labels: list[dict[str, Any]]) -> dict[str, float | None]:
    """Compute the four B1/B2 derived signals from the per-paragraph label
    sequence (``labels`` = [{"role","mode"}, ...] aligned to document order).

    Transition rates are row-normalized FROM the ``support`` role and count only
    transitions whose successor paragraph is also labeled (a None successor
    neither numerator nor denominator). All signals return None when their
    denominator is empty (too few labels) — never a fabricated 0."""
    roles = [l.get("role") for l in labels]
    modes = [l.get("mode") for l in labels]

    support_succ = support_to_proposal = support_to_support = 0
    for cur, nxt in zip(roles, roles[1:]):
        if cur == "support" and nxt is not None:
            support_succ += 1
            if nxt == "proposal":
                support_to_proposal += 1
            elif nxt == "support":
                support_to_support += 1
    sp = support_to_proposal / support_succ if support_succ else None
    ss = support_to_support / support_succ if support_succ else None

    labeled_modes = [m for m in modes if m is not None]
    arg_share = (
        sum(1 for m in labeled_modes if m == "argumentation") / len(labeled_modes)
        if labeled_modes else None
    )

    # Thesis-opening is a property of the FIRST paragraph specifically. If the
    # judge failed to label paragraph 0, the answer is unknown (None), not the
    # role of whatever later paragraph happened to be labeled first — asserting
    # "opens thesis-first" off paragraph 1+ would be a fabricated value.
    r0 = roles[0] if roles else None
    thesis_open = None if r0 is None else (1.0 if r0 == "thesis" else 0.0)

    return {
        "support_to_proposal_rate": sp,
        "support_to_support_rate": ss,
        "thesis_opening_tendency": thesis_open,
        "argumentation_share": arg_share,
    }


# ---- B5: arc-level collapse-dynamics derivation --------------------

# Guard levels the surface treats as "guarded" vs "unguarded" when reading a
# disappearing-guard trajectory. A downward transition is a guarded earlier
# paragraph (strong/moderate) followed by an unguarded later one (weak/none) for
# the SAME claim (matched by claim_ref).
_GUARDED = {"strong", "moderate"}
_UNGUARDED = {"weak", "none"}


def compute_collapse_dynamics(
    labels: list[dict[str, Any]],
    strongest_internal_objection_engaged: bool | None,
) -> dict[str, bool | None]:
    """Derive the two B5 arc-collapse flags from the per-paragraph judge labels
    + the document-level objection field. Returns ``bool | None`` per flag and
    NEVER fabricates a False: when the evidence is absent the flag is None
    (insufficient evidence), distinct from a real False (evidence present, no
    collapse).

    disappearing_guard_flag — group paragraphs by ``claim_ref``; a claim guarded
    (``strong``/``moderate``) in an EARLIER paragraph and unguarded
    (``weak``/``none``) in a LATER paragraph (a downward guard transition across
    >=2 paragraphs) sets True. False when >=1 claim is tracked across >=2
    paragraphs WITH guard data but no downward transition occurs. None when no
    claim_ref spans >=2 paragraphs carrying guard data (nothing to compare).

    discounting_straw_men_flag — True when >=1 counterclaim/rebuttal paragraph is
    labeled ``objection_strength=weak`` AND the doc-level
    ``strongest_internal_objection_engaged`` is False (weak objections engaged,
    the strong one ignored). False when the strongest internal objection IS
    engaged (doc-level True). None when no counterclaim/rebuttal is labeled OR
    the doc-level field is null (unknown — never a fabricated False)."""
    # --- disappearing_guard ---
    by_claim: dict[str, list[tuple[int, str]]] = {}
    for i, l in enumerate(labels):
        cref = l.get("claim_ref")
        gs = l.get("guard_strength")
        if cref is not None and gs is not None:
            by_claim.setdefault(cref, []).append((i, gs))

    trackable = [seq for seq in by_claim.values() if len(seq) >= 2]
    if not trackable:
        disappearing_guard: bool | None = None
    else:
        disappearing_guard = False
        for seq in trackable:
            # seq is in document order (we appended by ascending index).
            for (i_e, g_e), (i_l, g_l) in zip(seq, seq[1:]):
                if g_e in _GUARDED and g_l in _UNGUARDED:
                    disappearing_guard = True
                    break
            if disappearing_guard:
                break

    # --- discounting_straw_men ---
    objection_roles = {"counterclaim", "rebuttal"}
    has_objection_para = any(
        l.get("role") in objection_roles and l.get("objection_strength") is not None
        for l in labels
    )
    weak_engaged = any(
        l.get("role") in objection_roles and l.get("objection_strength") == "weak"
        for l in labels
    )
    if not has_objection_para or strongest_internal_objection_engaged is None:
        # No counterclaim/rebuttal labeled, or the strongest-objection judgment is
        # unknown: insufficient evidence -> None (never a fabricated False).
        discounting_straw_men: bool | None = None
    elif strongest_internal_objection_engaged is False and weak_engaged:
        discounting_straw_men = True
    else:
        # The strongest objection IS engaged (doc-level True), or only strong
        # objections are engaged: not a decoy pattern.
        discounting_straw_men = False

    return {
        "disappearing_guard_flag": disappearing_guard,
        "discounting_straw_men_flag": discounting_straw_men,
    }


# ---------- contributions -------------------------------------------

# Per-signal D2 status is carried on the schema's DerivedSignal.calibration_status
# (B1/B2 `literature_anchored`; B5 arc_flags `heuristic`). The schema tier is the
# FLOOR; a register baseline row may graduate an ANCHORED signal off it (the C0
# path), but an unanchored arc_flag (B5) NEVER reads the register row's status —
# its `heuristic` tier is pinned (no numeric anchor / no measured discrimination
# exists to graduate it, so no op-ed/other register row can push it higher).


@dataclass
class SignalContribution:
    signal_key: str
    label: str
    bundle: str
    leaning: str
    anchored: bool
    calibration_status: str
    paper_human_mean: float | None
    paper_ai_mean: float | None
    observed_value: float | None
    contribution: float | None
    direction: str  # "human" | "ai" | "neutral" | "directional" | "unavailable"
    # C0: register-matched means from argument_register_baselines.yaml, carried
    # ALONGSIDE the paper anchors. None unless `--register` supplied a row for
    # this signal; register_ai_mean is present only at `calibrated`. The row's
    # per-signal status flows into `calibration_status` (above).
    register_human_mean: float | None = None
    register_ai_mean: float | None = None
    register_provenance: str | None = None


def per_signal_contributions(
    observed: dict[str, float | None],
    register: "RegisterBaseline | None" = None,
) -> list[SignalContribution]:
    out: list[SignalContribution] = []
    reg_signals = register.signals if register is not None else {}
    for sig in DERIVED_SIGNALS:
        ov = observed.get(sig.key)
        rb = reg_signals.get(sig.key)
        # Per-signal calibration_status resolution: start from the schema tier
        # (the floor). A register row may graduate an ANCHORED signal off its
        # floor (the C0 path), carrying the register-matched mean(s) beside the
        # paper anchors. An UNANCHORED arc_flag (B5) is pinned at its schema tier
        # (`heuristic`) and NEVER reads the register row's status — there is no
        # numeric anchor / measured discrimination to graduate it, so a register
        # baseline cannot push it above heuristic (honesty ladder, review-binding).
        status = sig.calibration_status
        if rb is not None and sig.anchored:
            status = rb.status
        common = dict(
            signal_key=sig.key, label=sig.label, bundle=sig.bundle,
            leaning=sig.leaning,
            calibration_status=status,
            register_human_mean=(rb.human_mean if rb is not None and sig.anchored else None),
            register_ai_mean=(rb.ai_mean if rb is not None and sig.anchored else None),
            register_provenance=(rb.provenance if rb is not None and sig.anchored else None),
        )
        if not sig.anchored:
            # Directional-only (no numeric anchor): report the observed value,
            # no contribution, no human/ai placement. When the observed value is
            # absent (e.g. a B5 arc_flag that derived to None on insufficient
            # evidence, or thesis-opening with an unlabeled paragraph 0) the
            # direction is `unavailable` — never a fabricated `directional`.
            out.append(SignalContribution(
                **common, anchored=False,
                paper_human_mean=None, paper_ai_mean=None,
                observed_value=ov, contribution=None,
                direction=("directional" if ov is not None else "unavailable"),
            ))
            continue
        denom = sig.human_mean - sig.ai_mean
        if ov is None or denom == 0:
            # No observed value, or a degenerate equal-means anchor (which
            # _self_check already rejects, but guard the 0/0 anyway): the signal
            # is unavailable — never a fabricated contribution.
            out.append(SignalContribution(
                **common, anchored=True,
                paper_human_mean=sig.human_mean, paper_ai_mean=sig.ai_mean,
                observed_value=ov, contribution=None, direction="unavailable",
            ))
            continue
        contribution = (ov - sig.ai_mean) / denom
        midpoint = (sig.human_mean + sig.ai_mean) / 2
        if abs(ov - midpoint) < 1e-9:
            direction = "neutral"
        elif (ov > midpoint) == (sig.human_mean > sig.ai_mean):
            direction = "human"
        else:
            direction = "ai"
        out.append(SignalContribution(
            **common, anchored=True,
            paper_human_mean=sig.human_mean, paper_ai_mean=sig.ai_mean,
            observed_value=ov, contribution=contribution, direction=direction,
        ))
    return out


@dataclass
class BundleAggregate:
    bundle: str
    label: str
    n_signals: int
    n_evaluated: int
    mean_contribution: float | None
    human_leaning_signals: int
    ai_leaning_signals: int


def per_bundle_aggregates(
    contributions: list[SignalContribution],
) -> list[BundleAggregate]:
    by_bundle: dict[str, list[SignalContribution]] = {}
    for c in contributions:
        by_bundle.setdefault(c.bundle, []).append(c)
    out: list[BundleAggregate] = []
    for bundle in BUNDLE_LABELS:
        sigs = by_bundle.get(bundle, [])
        evaluated = [s for s in sigs if s.contribution is not None]
        mean = (
            sum(s.contribution for s in evaluated) / len(evaluated)
            if evaluated else None
        )
        out.append(BundleAggregate(
            bundle=bundle,
            label=BUNDLE_LABELS[bundle],
            n_signals=len(sigs),
            n_evaluated=len(evaluated),
            mean_contribution=mean,
            human_leaning_signals=sum(1 for s in sigs if s.direction == "human"),
            ai_leaning_signals=sum(1 for s in sigs if s.direction == "ai"),
        ))
    return out


def aggregate_score(contributions: list[SignalContribution]) -> dict[str, Any]:
    evaluated = [c for c in contributions if c.contribution is not None]
    if not evaluated:
        return {
            "score": None,
            "n_signals_evaluated": 0,
            "n_signals_total": len(contributions),
            "verdict_band": "unavailable",
        }
    raw = sum(c.contribution for c in evaluated) / len(evaluated)
    return {
        "score": raw,
        "n_signals_evaluated": len(evaluated),
        "n_signals_total": len(contributions),
        "verdict_band": "uncalibrated",
    }


def compute_pre_flag(contributions: list[SignalContribution]) -> dict[str, Any]:
    """D4: a structured pre-flag DATA hint — a texture observation, never a
    reasoning verdict. ``dialectical_clarity_informative`` is True when ≥2 of the
    three anchored arc/mode signals (support→proposal, support→support,
    argumentation_share) actually land on the AI side of the paper's midpoint;
    the consumer OFFERS a dialectical-clarity run on the hint (offer-then-attach).
    The ``basis`` is built from the SIGNALS THAT ACTUALLY CONVERGED — it never
    asserts a direction the same payload's ``contributions[]`` contradicts — and
    the AT3 / DC-rule-2a (uncompared-recommendation) hook is named only when
    ``support_to_proposal_rate`` is itself among the AI-leaning signals."""
    by = {c.signal_key: c for c in contributions}
    arc_keys = ("support_to_proposal_rate", "support_to_support_rate", "argumentation_share")
    ai_leaning = [k for k in arc_keys if by.get(k) and by[k].direction == "ai"]
    informative = len(ai_leaning) >= 2
    if informative:
        parts = ", ".join(ai_leaning)
        basis = (
            f"{len(ai_leaning)} of 3 anchored arc/mode signals lean LLM-typical "
            f"({parts} on the AI side of the paper's midpoint)."
        )
        if "support_to_proposal_rate" in ai_leaning:
            basis += (
                " The AI-side support→proposal rate makes a dialectical-clarity "
                "run informative: it would check whether the proposal-heavy arc "
                "reflects an AT3 uncompared recommendation (DC rule 2a)."
            )
        basis += " This is a texture observation, not a soundness verdict."
    else:
        present = [k for k in arc_keys if by.get(k)]
        basis = (
            "The anchored arc/mode signals do not converge on the paper's "
            "collapse-leaning pattern (fewer than 2 of "
            + ", ".join(present) + " on the AI side)."
        )
    return {"dialectical_clarity_informative": informative, "basis": basis}


def register_warnings_for(n_words: int, n_paragraphs: int) -> list[str]:
    warnings: list[str] = []
    if n_words < MIN_ARGUMENT_WORDS:
        warnings.append(
            f"Target is {n_words} words; ArgScope's home register is "
            f"public-debate-forum essays (NYT mean ~{PAPER_OPED_MEAN_WORDS}). "
            f"Below ~{MIN_ARGUMENT_WORDS} words the structural arc is too short "
            f"to read; treat as out-of-register."
        )
    if n_paragraphs < MIN_PARAGRAPHS:
        warnings.append(
            f"Target has {n_paragraphs} paragraph(s); the B1 transition-matrix "
            f"signals (support→proposal, support→support) need a multi-paragraph "
            f"arc (>= {MIN_PARAGRAPHS}). They report null below that."
        )
    return warnings


# ---------- envelope -------------------------------------------------

def compute_reused_signals(text: str) -> dict[str, Any]:
    """B3 (abstraction) + B4 (stance) + AGD densities for the target, reused from
    the deterministic audits via ``argmove_profile.argmove_vector``. These are
    DESCRIPTIVE / `heuristic` — no anchor, not in the contributions or the
    aggregate (D2/D5); they sit beside the anchored B1/B2 structure as texture
    context. Lazy-imported so the surface module stays cheap; degrades to
    ``available: false`` if a reused audit's schema drifted (ContractError) or an
    optional dep (e.g. the concreteness data file) is unavailable — a missing
    reuse signal is descriptive context, never a hard failure of the audit."""
    try:
        import argmove_profile  # lazy
        vec = dict(argmove_profile.argmove_vector(text))
        n_words = vec.pop("_n_words", None)
        return {
            "available": True,
            "calibration_status": "heuristic",
            "n_words": n_words,
            "signals": vec,
            "note": (
                "B3 abstraction + B4 stance + AGD marker densities (deterministic, "
                "`heuristic` — descriptive only, NO anchor, not in the aggregate). "
                "No numeric anchor by design (D5): marker density is a different "
                "construct from the paper's judge-rated per-essay stance strength."
            ),
        }
    except Exception as exc:  # noqa: BLE001 — reuse is descriptive; degrade, don't crash
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def build_results_payload(
    *,
    target_words: int,
    n_paragraphs: int,
    judge_result: dict[str, Any],
    paragraph_labels: list[dict[str, Any]],
    validation_warnings: list[str],
    observed: dict[str, float | None],
    reused_signals: dict[str, Any],
    contributions: list[SignalContribution],
    bundles: list[BundleAggregate],
    aggregate: dict[str, Any],
    pre_flag: dict[str, Any],
    register_warnings: list[str],
    register: "RegisterBaseline | None" = None,
    strongest_internal_objection_engaged: bool | None = None,
) -> dict[str, Any]:
    return {
        "judge": judge_result,
        "prompt_fingerprint_sha256": fingerprint_prompt(),
        "target": {
            "words": target_words,
            "paragraphs": n_paragraphs,
            "register_match": ["op-ed"],
            "register_warnings": register_warnings,
            # C0: which register baseline (if any) was applied. None when no
            # `--register` was supplied (the surface fell back to D3 paper anchors).
            "register": (
                None if register is None else {
                    "genre": register.genre,
                    "source": register.source_path,
                    "calibrated": register.is_calibrated,
                    "per_signal_status": {
                        k: v.status for k, v in register.signals.items()
                    },
                }
            ),
        },
        "paragraph_labels": paragraph_labels,
        "validation_warnings": validation_warnings,
        "observed_signals": observed,
        "reused_signals": reused_signals,
        "contributions": [
            {
                "signal_key": c.signal_key,
                "label": c.label,
                "bundle": c.bundle,
                "leaning": c.leaning,
                "anchored": c.anchored,
                "calibration_status": c.calibration_status,
                "paper_human_mean": c.paper_human_mean,
                "paper_ai_mean": c.paper_ai_mean,
                "register_human_mean": c.register_human_mean,
                "register_ai_mean": c.register_ai_mean,
                "register_provenance": c.register_provenance,
                "observed_value": c.observed_value,
                "contribution": c.contribution,
                "direction": c.direction,
            }
            for c in contributions
        ],
        "bundles": [
            {
                "bundle": b.bundle,
                "label": b.label,
                "n_signals": b.n_signals,
                "n_evaluated": b.n_evaluated,
                "mean_contribution": b.mean_contribution,
                "human_leaning_signals": b.human_leaning_signals,
                "ai_leaning_signals": b.ai_leaning_signals,
            }
            for b in bundles
        ],
        "pre_flag": pre_flag,
        # B5 (collapse dynamics): the document-level judge field the two B5 flags
        # derive against. The per-signal flag VALUES live in observed_signals /
        # contributions[] like every other derived signal; this block carries the
        # single doc-level scalar (strongest_internal_objection_engaged) that has
        # no per-paragraph home. None = the judge could not tell (the derivation
        # then returns None for discounting_straw_men_flag, never a fabricated
        # False). These signals are `heuristic` directional-only TEXTURE — they do
        # NOT enter the aggregate and do NOT adjudicate fairness/soundness.
        "collapse_dynamics": {
            "strongest_internal_objection_engaged": strongest_internal_objection_engaged,
        },
        "aggregate": {
            **aggregate,
            "thresholds": {"low": None, "high": None},
        },
        "run_timestamp_utc": utc_now(),
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    licenses_text: str,
    does_not_license_text: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    if results["target"].get("register_warnings"):
        caveats.extend(results["target"]["register_warnings"])
    if results.get("validation_warnings"):
        caveats.append(
            f"Judge output had {len(results['validation_warnings'])} validation "
            f"warning(s); see results.validation_warnings."
        )
    judge_kind = results["judge"]["judge_identity"].get("kind")
    if judge_kind == "mock":
        caveats.append(
            "Judge backend is `mock` — a deterministic TEST stub, not a real "
            "labeler. Do not infer anything about the argument from a mock run."
        )
    elif judge_kind == "manifest":
        caveats.append(
            "Judge backend is `manifest` — the role/mode labels (and every B1/B2 "
            "signal derived from them) are only as good as whatever produced the "
            "manifest, which this surface cannot verify. Read judge.judge_identity."
        )
    elif judge_kind == "agent_host":
        caveats.append(
            "Judge backend is `agent_host` — the labels were produced by the HOST "
            "runtime's model (see judge.judge_identity.host), not a pinned API "
            "model@revision. The judgment is NON-DETERMINISTIC and host-version-fluid. "
            "The identity is recorded as agent_host:<host>:<model> so a consumer can "
            "assert it is disjoint from any generator it validates (the consumer's "
            "drift gate must enforce judge model != generator model on holdout/selection "
            "surfaces; see specs/35-host-delegated-judge.md)."
        )
    caveats.append(
        "Verdict band is `uncalibrated` and the anchors are register-bound to "
        "public-debate forums (directional reference, not thresholds). No "
        "human / AI label is emitted."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "literature_anchor": (
                "Kim, Chang, Pham & Iyyer 2026 ('Argument Collapse', "
                "arXiv:2606.01736v3) §4.1-4.2 / Tables 26-27 group means, "
                "public-debate-forum essays (NYT Room for Debate + Boston Review)"
            ),
            "judge_kind": judge_kind,
            "judge_model": (
                results["judge"]["judge_identity"].get("model") or "(unspecified)"
            ),
            # host runtime id for agent_host (the firewall hook: lets a consumer assert
            # judge model != generator model); null for non-delegated backends.
            "judge_host": results["judge"]["judge_identity"].get("host"),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_ARGUMENT_WORDS, 8000),
        register_match=["op-ed"],
        additional_caveats=caveats,
        references=[
            "Kim, Chang, Pham & Iyyer 2026, 'Argument Collapse: LLMs Flatten "
            "Long-Form Public Debate' (arXiv:2606.01736v3)",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    agg = results["aggregate"]
    lines: list[str] = [
        f"# Argument-decision audit: `{target.get('path')}`",
        "",
        f"- **Target:** {target.get('words'):,} words, "
        f"{results['target']['paragraphs']} paragraphs",
        f"- **Judge:** `{results['judge']['judge_identity'].get('kind')}` "
        f"({results['judge']['judge_identity'].get('model') or '—'})",
        f"- **Aggregate score:** "
        f"{('%.3f' % agg['score']) if agg.get('score') is not None else 'n/a'} "
        f"(verdict band: `{agg['verdict_band']}`)",
        f"- **Signals evaluated:** {agg['n_signals_evaluated']}/{agg['n_signals_total']}",
        "",
        "Score is a linear interpolation between the paper's group means "
        "(1.0 = human mean, 0.0 = LLM mean), UNBOUNDED — a value past either "
        "mean extrapolates beyond [0, 1], and one extreme signal can dominate "
        "the mean. Not a z-score (no variance normalization). Anchors are "
        "register-bound to public-debate forums (directional, not thresholds).",
        "",
        "## Signals",
        "",
        "| Signal | Bundle | Observed | H mean | LLM mean | Contribution | Direction |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for c in results["contributions"]:
        ov = c["observed_value"]
        ov_s = f"{ov:.3f}" if ov is not None else "—"
        hm = f"{c['paper_human_mean']:.3f}" if c["paper_human_mean"] is not None else "—"
        am = f"{c['paper_ai_mean']:.3f}" if c["paper_ai_mean"] is not None else "—"
        contrib = c["contribution"]
        contrib_s = f"{contrib:+.3f}" if contrib is not None else "—"
        lines.append(
            f"| {c['label']} | {c['bundle']} | {ov_s} | {hm} | {am} | "
            f"{contrib_s} | {c['direction']} |"
        )
    lines += [
        "",
        f"**Pre-flag (dialectical-clarity informative):** "
        f"{results['pre_flag']['dialectical_clarity_informative']} — "
        f"{results['pre_flag']['basis']}",
        "",
        "## Claim license",
        "",
        envelope["claim_license_rendered"].rstrip(),
        "",
    ]
    return "\n".join(lines)


# ---------- CLI -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ArgScope Layer A: score a public-debate essay's argumentative arc "
            "(B1 paragraph-role transitions) + discourse-mode mix (B2) against "
            "Kim et al. 2026's human/LLM anchors."
        )
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument(
        "--judge",
        choices=("manifest", "mock", "anthropic", "openai", "gemini", "agent_host"),
        default="manifest",
        help="Judge backend for the per-paragraph role/mode labels. `agent_host` "
             "delegates to the host runtime's model (no API key); see "
             "specs/35-host-delegated-judge.md.",
    )
    parser.add_argument("--judge-manifest", type=Path, default=None,
                        help="JSON manifest of pre-computed labels (required for --judge=manifest).")
    parser.add_argument("--judge-model", default=None, help="Model ID for API judges.")
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=4096)
    parser.add_argument("--out", type=Path, default=None,
                        help="JSON output path (default <target>.argument.json).")
    parser.add_argument("--out-md", type=Path, default=None,
                        help="Markdown output path (default <target>.argument.md).")
    parser.add_argument("--json", action="store_true", help="Print the envelope to stdout.")
    parser.add_argument(
        "--register", default=None,
        help="Genre key to look up in argument_register_baselines.yaml (e.g. "
             "op-ed): attaches the register-matched mean(s) + per-signal "
             "calibration_status alongside the paper anchors. Absent → paper "
             "anchors only (Layer A D3).",
    )
    parser.add_argument(
        "--baseline-dir", type=Path, default=None,
        help="Directory holding an operator-local argument_register_baselines.yaml "
             "(else $SETEC_BASELINES_DIR, else the shipped baselines/). Requires --register.",
    )
    parser.add_argument("--licenses", default=DEFAULT_LICENSES)
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    args = parser.parse_args(argv)

    target_path = Path(args.target)
    if not target_path.is_file():
        print(f"error: target file not found at {target_path}", file=sys.stderr)
        return 1
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read target {target_path}: {exc}", file=sys.stderr)
        return 1

    paragraphs = split_paragraphs(text)
    target_words = count_words(text)

    # C0: resolve the register baseline (if requested) BEFORE the expensive judge
    # run, so a malformed / unknown --register fails fast.
    register = None
    register_warnings_extra: list[str] = []
    if args.register or args.baseline_dir is not None:
        if not args.register:
            parser.error("--baseline-dir requires --register <genre>")
        try:
            from argument_register_baselines import (  # type: ignore
                RegisterBaselineError,
                load_register,
            )
        except ImportError as exc:
            parser.error(f"register baseline support unavailable: {exc}")
        try:
            register = load_register(args.register, baseline_dir=args.baseline_dir)
        except RegisterBaselineError as exc:
            # A malformed / dishonest baseline is bad INPUT; route through argparse
            # so setec_run categorizes the exit-2 as bad_input, not policy_refused.
            parser.error(f"register baseline error: {exc}")
        if register is None:
            register_warnings_extra.append(
                f"--register {args.register!r}: no row in argument_register_baselines.yaml; "
                f"falling back to the paper's public-debate anchors (no register-matched "
                f"baseline for this genre yet)."
            )

    try:
        judge = build_judge(
            args.judge, manifest_path=args.judge_manifest, model=args.judge_model,
            temperature=args.judge_temperature, max_tokens=args.judge_max_tokens,
        )
    except JudgeError as exc:
        # Judge construction failures are bad SETUP input (missing manifest /
        # model / API key), not a privacy-policy refusal. Route them through
        # argparse so the emitted "usage:" line lets setec_run categorize the
        # exit-2 as bad_input rather than the policy_refused bucket that a bare
        # exit-2 falls into (the privacy ratchet). See setec_run._wrap_script_failure.
        parser.error(f"judge construction failed: {exc}")
    try:
        judge_result_obj = judge(paragraphs)
    except JudgeError as exc:
        print(f"error: judge execution failed: {exc}", file=sys.stderr)
        return 3

    labels, val_warnings = validate_labels(
        judge_result_obj.values, n_paragraphs=len(paragraphs)
    )
    strongest_obj_engaged, doc_warnings = validate_doc_level(judge_result_obj.values)
    val_warnings = val_warnings + doc_warnings
    observed = compute_arc_signals(labels)
    # B5: derive the two arc-collapse flags + merge into observed_signals so the
    # B5 contributions (unanchored, contribution=null) pick them up. Additive —
    # the B1/B2 observed keys are untouched.
    collapse = compute_collapse_dynamics(labels, strongest_obj_engaged)
    observed.update(collapse)
    contributions = per_signal_contributions(observed, register)
    bundles = per_bundle_aggregates(contributions)
    agg = aggregate_score(contributions)
    pre_flag = compute_pre_flag(contributions)
    reg_warnings = register_warnings_for(target_words, len(paragraphs)) + register_warnings_extra
    reused = compute_reused_signals(text)

    paragraph_labels = [
        {
            "index": i,
            "role": labels[i]["role"],
            "mode": labels[i]["mode"],
            "guard_strength": labels[i].get("guard_strength"),
            "claim_ref": labels[i].get("claim_ref"),
            "objection_strength": labels[i].get("objection_strength"),
        }
        for i in range(len(labels))
    ]
    results = build_results_payload(
        target_words=target_words,
        n_paragraphs=len(paragraphs),
        judge_result=judge_result_obj.to_dict(),
        paragraph_labels=paragraph_labels,
        validation_warnings=val_warnings,
        observed=observed,
        reused_signals=reused,
        contributions=contributions,
        bundles=bundles,
        aggregate=agg,
        pre_flag=pre_flag,
        register_warnings=reg_warnings,
        register=register,
        strongest_internal_objection_engaged=strongest_obj_engaged,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=target_words, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )

    out_json_path = (
        args.out if args.out is not None
        else target_path.with_suffix(target_path.suffix + ".argument.json")
    )
    out_md_path = (
        args.out_md if args.out_md is not None
        else target_path.with_suffix(target_path.suffix + ".argument.md")
    )
    try:
        out_json_path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
        out_md_path.write_text(render_markdown(envelope), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        score = agg.get("score")
        score_s = f"{score:+.3f}" if score is not None else "n/a"
        print(f"JSON written to {out_json_path}")
        print(f"Markdown written to {out_md_path}")
        print(f"Aggregate score: {score_s} (verdict band: {results['aggregate']['verdict_band']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
