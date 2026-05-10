#!/usr/bin/env python3
"""restoration_packet.py — Surface 4 metric-targeted restoration.

Translates SETEC's diagnostic outputs (Surface 1 variance audit,
Surface 1 per-bigram POS-bigram diff, Surface 2 voice distance,
Surface 2 idiolect detector, Surface 4 AIC pattern audit) into
revision-safe prompt packets.

Targetability classes (see references/metric-targeted-restoration.md):

  direct            Signal maps cleanly to a promptable prose move.
  translated        Raw signal not promptable; contributors translate
                    into a prose-level move (POS bigrams/trigrams,
                    selected dep n-grams, function-word clusters).
  investigate_first Signal says "something is off" but not which
                    revision should happen; emit a diagnostic prompt
                    asking for causes before any rewrite.
  avoid_direct      Signal should not become a prompt target.
                    Optimizing it directly invites metric gaming.

The script reads JSON outputs (any subset; at least one required)
and emits a markdown + JSON packet suitable for a prompt generator
or human reviser. v1 does not rewrite prose — it produces target
packets and prompt text. Actual rewriting is a human- or LLM-in-the-
loop step outside the script.

Usage:

    python3 scripts/restoration_packet.py \\
        --variance-json out/variance.json \\
        --bigram-json out/bigram_diff.json \\
        --idiolect-json out/idiolect.json \\
        --genre essay \\
        --target-scope "paragraphs 4-8" \\
        --out packet.md \\
        --json-out packet.json

See references/metric-targeted-restoration.md for the targetability
taxonomy + translation tables.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


TASK_SURFACE = "craft_restoration"
TOOL_NAME = "restoration_packet"


# --------------- Targetability constants ---------------------


# Signals that map cleanly to direct prompt targets. Keys match the
# heuristic-key names in variance_audit.COMPRESSION_HEURISTICS where
# possible; some ("aic_pattern", "idiolect_preservation") are signals
# from other surfaces.
DIRECT_TARGETS: dict[str, dict[str, str]] = {
    "connective_density": {
        "diagnosis": "Connective density is elevated.",
        "move_over": "Cut explicit discourse markers; let adjacency carry transitions.",
        "move_under": "Add a few connectives to scaffold a long stretch of unmarked transitions.",
    },
    "burstiness_B": {
        "diagnosis": "Sentence-length variance is compressed.",
        "move_over": "Reduce uniformity at the long end of the distribution.",
        "move_under": "Add fragments and a few longer syntactic runs; avoid middle-length uniformity.",
    },
    "fkgl_sd": {
        "diagnosis": "FKGL standard deviation is compressed across sentences.",
        "move_over": "Reduce reading-level uniformity.",
        "move_under": "Let technical/analytical sentences spike and anecdotal sentences drop.",
    },
    "adjacent_cosine_mean": {
        "diagnosis": "Adjacent-sentence cohesion is over-tight.",
        "move_over": "Remove over-explained transitions; permit sharper turns and asides.",
        "move_under": "Add a transition where the reader is currently doing too much work.",
    },
    "adjacent_cosine_sd": {
        "diagnosis": "Cohesion variance is compressed.",
        "move_over": "Loosen cohesion uniformity.",
        "move_under": "Let cohesion vary more across the passage.",
    },
    "repetition_audit_generic": {
        "diagnosis": "Generic vocabulary is over-represented.",
        "move_over": "Replace or cut generic repetition; preserve project anchors and idiolect.",
        "move_under": "(rare) generic vocabulary is under-represented; usually no action.",
    },
    "idiolect_preservation": {
        "diagnosis": "Writer-specific phrases and collocations to preserve.",
        "move_over": "(N/A — this is a preservation list, not a drift signal.)",
        "move_under": "Preserve these words/phrases verbatim during revision.",
    },
    "aic_pattern": {
        "diagnosis": "Named AIC pattern density is elevated.",
        "move_over": "Apply source triage and rhetorical countermoves from references/source-triage.md.",
        "move_under": "(N/A — under-density is not a craft concern.)",
    },
}


# POS bigrams that translate into prose-level moves. The over- and
# under- entries describe what each direction implies; the move is
# the revision instruction the packet emits when the signal fires.
POS_BIGRAM_TRANSLATIONS: dict[str, dict[str, str]] = {
    "DET-ADJ": {
        "over": "Formulaic noun-phrase setup.",
        "under": "Sparse descriptive setup.",
        "move": "Keep only adjectives that change reader inference.",
    },
    "ADJ-NOUN": {
        "over": "Evaluative label clusters.",
        "under": "Thin sensory/conceptual naming.",
        "move": "Replace generic modifiers with concrete nouns/verbs; preserve earned epithets.",
    },
    "NOUN-NOUN": {
        "over": "Institutional or abstract noun stacks.",
        "under": "Less compressed labeling.",
        "move": "Unpack relation with a verb or preposition only when clarity improves.",
    },
    "NOUN-ADP": {
        "over": "'X of/for/in...' scaffolding.",
        "under": "Less relational explanation.",
        "move": "Cut nested abstract relations; make actors and actions visible.",
    },
    "ADP-DET": {
        "over": "Prepositional padding.",
        "under": "Choppier, less scaffolded syntax.",
        "move": "Collapse weak prepositional phrases; vary sentence architecture.",
    },
    "ADP-NOUN": {
        "over": "Topic/register nouns carried by prepositions.",
        "under": "Fewer abstract anchors.",
        "move": "Check for bureaucratic abstraction or topic terms before revising.",
    },
    "PRON-AUX": {
        "over": "Hedged/assistant-like stance or dialogue-heavy mode.",
        "under": "Agent layer may be missing.",
        "move": "Inspect examples; restore or cut depending on voice.",
    },
    "PRON-VERB": {
        "over": "Personal narration or dialogue pressure.",
        "under": "Human actors may be hidden.",
        "move": "Restore named agents where abstractions dominate.",
    },
    "AUX-VERB": {
        "over": "Modal/passive/periphrastic verb frames.",
        "under": "Direct finite verbs dominate.",
        "move": "Prefer direct verbs unless modality is analytically needed.",
    },
    "ADV-ADJ": {
        "over": "Booster language.",
        "under": "Less evaluative smoothing.",
        "move": "Cut intensifiers; replace evaluation with evidence or image.",
    },
    "VERB-DET": {
        "over": "Action-object frames.",
        "under": "Action layer may be thin.",
        "move": "Add concrete actions/objects where the draft only explains.",
    },
    "VERB-ADP": {
        "over": "Phrasal/prepositional verb scaffolding.",
        "under": "More direct verb-object syntax.",
        "move": "Check for repeated 'looked at / worked through / moved toward' drift.",
    },
    "CCONJ-DET": {
        "over": "Listy connective rhythm.",
        "under": "Fewer additive structures.",
        "move": "Break list cadence; vary coordination.",
    },
    # Markup/code contamination — refuse to translate as prose.
    "PUNCT-PUNCT": {
        "over": "Likely markup/code contamination.",
        "under": "(N/A)",
        "move": "Do not revise prose; run scripts/check_corpus.py first.",
    },
    "PUNCT-SYM": {
        "over": "Likely markup/code contamination.",
        "under": "(N/A)",
        "move": "Do not revise prose; run scripts/check_corpus.py first.",
    },
    "SYM-NOUN": {
        "over": "Likely markup/code contamination.",
        "under": "(N/A)",
        "move": "Do not revise prose; run scripts/check_corpus.py first.",
    },
}


POS_TRIGRAM_TRANSLATIONS: dict[str, dict[str, str]] = {
    "DET-ADJ-NOUN": {
        "translation": "Polished descriptor package.",
        "move": "Replace generic descriptor packages with concrete actors, sensory specifics, or a verb phrase.",
    },
    "ADJ-NOUN-NOUN": {
        "translation": "Noun-stack compression.",
        "move": "Unpack institutional labels when they hide agency; preserve domain terms.",
    },
    "NOUN-ADP-DET": {
        "translation": "Abstract relation scaffolding.",
        "move": "Cut 'X of the Y' chains where the relation is obvious.",
    },
    "NOUN-AUX-VERB": {
        "translation": "Predicate mediated by auxiliary.",
        "move": "Check passive/modal drift; use direct verbs where commitment is safe.",
    },
    "PRON-AUX-VERB": {
        "translation": "Personal stance/action mediated by auxiliary.",
        "move": "In dialogue/interiority, decide whether hesitation is character work or assistant hedging.",
    },
    "AUX-VERB-ADP": {
        "translation": "Modal/passive setup into relation.",
        "move": "Replace with direct action when the sentence is explaining rather than showing.",
    },
    "VERB-DET-NOUN": {
        "translation": "Concrete action-object unit.",
        "move": "If under-represented, restore embodied action or concrete policy actors.",
    },
    "ADV-ADJ-NOUN": {
        "translation": "Intensified evaluative package.",
        "move": "Cut booster adverbs and make the noun/adjective carry the meaning.",
    },
    "ADP-DET-ADJ": {
        "translation": "Preposition-led description chain.",
        "move": "Break stacked prepositional modifiers into a cleaner sentence shape.",
    },
    "CCONJ-DET-NOUN": {
        "translation": "List continuation rhythm.",
        "move": "Break mechanical enumeration or make the list formally intentional.",
    },
}


DEP_NGRAM_TRANSLATIONS: dict[str, dict[str, str]] = {
    "amod": {
        "translation": "Modifier load is high.",
        "move": "Test adjectives for work; cut ornamental modifiers.",
    },
    "compound": {
        "translation": "Noun-stack load is high.",
        "move": "Unpack institutional/domain labels only when they obscure agency.",
    },
    "prep": {
        "translation": "Prepositional scaffolding.",
        "move": "Collapse weak relations or vary sentence architecture.",
    },
    "pobj": {
        "translation": "Prepositional scaffolding.",
        "move": "Collapse weak relations or vary sentence architecture.",
    },
    "auxpass": {
        "translation": "Passive/modal mediation.",
        "move": "Restore actors where accountability matters.",
    },
    "advmod": {
        "translation": "Booster/stance adverbs.",
        "move": "Cut intensity words or replace with evidence.",
    },
}


# Investigate-first signals: these emit a diagnostic prompt asking
# the writer to look at causes before rewriting.
INVESTIGATE_FIRST: dict[str, str] = {
    "mattr": "Are repeated words thematic anchors, closed-scene constraints, or synonym poverty?",
    "mtld": "Are repeated words thematic anchors, closed-scene constraints, or synonym poverty?",
    "yules_k": "Which high-frequency words are load-bearing, and which are generic glue?",
    "shannon_entropy": "Is the distribution narrow because the topic is narrow, or because the prose has been normalized?",
    "function_word_cluster": "Is this a legitimate register/persona shift, or assistant-register connective/default-pronoun drift?",
    "dependency_ngram_other": "Which syntactic constructions are repeated locally, and are they earned by genre/argument?",
}


# Avoid-direct signals: never become prompt targets. Mentioned as
# evidence in the packet (when present in the inputs) but never as
# a revision instruction.
AVOID_DIRECT: dict[str, str] = {
    "pos_bigram_kl_total": "Aggregate POS-bigram divergence; optimizing it directly encourages syntactic gaming.",
    "burrows_delta_overall": "Aggregate Burrows Delta; too easy to overfit function words.",
    "cosine_distance_overall": "Aggregate cosine distance; too easy to overfit function words.",
    "char_ngram_distance": "Mostly orthographic/morphological residue; not a craft instruction.",
    "raw_dep_ngram_distance": "Parse-feature abstraction; unreliable as a writer-facing target unless localized and translated.",
    "auc": "Performance metric, not a revision goal.",
    "compression_band": "Summary judgment; the band is for the writer to read, not for the model to optimize.",
}


GUARDRAILS_DEFAULT = (
    "Do not add new facts.",
    "Do not replace writer-specific phrases from the preservation list.",
    "Do not optimize for POS tags or aggregate divergence directly.",
    "Do not flatten idiolect; preserve recurring writer-specific words and collocations.",
    "Do not rewrite outside the named target scope.",
)


# --------------- Packet model -------------------------------


@dataclass
class Packet:
    id: str
    targetability: str  # direct / translated / investigate_first / avoid_direct
    signal: str
    direction: str  # over_represented / under_represented / unspecified
    severity: str  # light / moderate / heavy / unspecified
    evidence: dict[str, Any]
    plain_language_diagnosis: str
    revision_moves: list[str]
    guardrails: list[str]
    post_check: list[str]
    # Revision-risk model (Release 4, paired-release schedule).
    # Per-packet risk label estimating the chance that the
    # intervention will damage a different dimension of the prose:
    # erase idiolect, create metric gaming, restore quirks
    # intentionally edited out, damage genre expectations, etc.
    # Filled by `classify_revision_risk`. Default "unspecified"
    # for backward-compat with packets built before 1.34.0.
    revision_risk: str = "unspecified"  # low / medium / high / unspecified
    revision_risk_rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "targetability": self.targetability,
            "signal": self.signal,
            "direction": self.direction,
            "severity": self.severity,
            "evidence": self.evidence,
            "plain_language_diagnosis": self.plain_language_diagnosis,
            "revision_moves": self.revision_moves,
            "guardrails": self.guardrails,
            "post_check": self.post_check,
            "revision_risk": self.revision_risk,
            "revision_risk_rationale": self.revision_risk_rationale,
        }


# --------------- Revision-risk model (Release 4) -------------
#
# Per-packet risk classification — estimates the chance that the
# intervention will damage a different dimension of the prose
# beyond the targeted signal. Output: ("low"|"medium"|"high",
# rationale). Heuristic; calibration-pending.
#
# Risk axes (named in the Trustworthiness Tier-3 Revision-risk
# model spec):
#   - erase idiolect
#   - create metric gaming
#   - increase generic humanizer artifacts
#   - damage clarity
#   - damage genre expectations
#   - overcorrect into artificial variance
#   - preserve voice but weaken argument
#   - restore quirks intentionally edited out

_RISK_TABLE: dict[tuple[str, str], tuple[str, str]] = {
    # (targetability, signal-substring) → (risk, rationale)
    # Direct targets: variance restoration. The risk is
    # overcorrection into artificial variance.
    ("direct", "sentence_length"): (
        "medium",
        "Restoring sentence-length variance can overcorrect into "
        "artificial variance if the writer randomizes lengths "
        "rather than rebuilding rhetorical-emphasis turns. Aim for "
        "varied lengths driven by changes in argumentative weight, "
        "not by length-target alone.",
    ),
    ("direct", "burstiness"): (
        "medium",
        "Burstiness restoration is a sentence-rhythm move; the "
        "risk is overcorrecting into mannered variation if the "
        "writer chases the metric. Restore emphasis-driven "
        "variance, not metric-driven variance.",
    ),
    ("direct", "connective_density"): (
        "medium",
        "Reducing connective density damages genre expectations in "
        "expository / academic / policy prose where transitions "
        "are conventional. Verify the move fits the genre before "
        "deleting connectives wholesale.",
    ),
    ("direct", "idiolect"): (
        "low",
        "Restoring documented idiolect phrases is low risk when "
        "the idiolect detector's confidence is high. Risk rises "
        "to medium if the detector flags low-confidence phrases — "
        "those may be quirks intentionally edited out, not voice.",
    ),
    ("direct", "aic pattern"): (
        "medium",
        "AIC pattern reduction risks damaging genre expectations "
        "in legal / policy / forensic prose where parallel-template "
        "rhetoric is conventional. Reduce only the named patterns "
        "the AIC audit flagged at high density, not all patterns.",
    ),
    ("direct", "fkgl"): (
        "medium",
        "FKGL spread restoration risks damaging clarity if the "
        "writer increases spread by introducing gratuitously long "
        "or short sentences. Restore via varied syntactic depth, "
        "not via syllable-count manipulation.",
    ),
    ("direct", "mattr"): (
        "medium",
        "MATTR restoration risks creating generic humanizer "
        "artifacts if the writer reaches for synonyms rather than "
        "concrete details. Lexical diversity should rise from "
        "specificity, not from thesaurus substitution.",
    ),

    # Translated targets: POS bigrams / dep n-grams / function-word
    # clusters. Easy to overcorrect into mannered variation.
    ("translated", "pos_bigram"): (
        "medium",
        "POS-bigram triggers translate to syntactic prose moves "
        "(replace generic descriptor packages with concrete "
        "actors). Risk: restoring variation by tag-shape rather "
        "than by craft produces mannered prose.",
    ),
    ("translated", "function_word"): (
        "medium",
        "Function-word cluster shifts translate into "
        "stance / hedging / boosting changes. Risk: restoring a "
        "cluster mechanically can change the writer's epistemic "
        "posture (e.g., turning hedged claims into unhedged ones).",
    ),

    # Investigate-first: cause-first revision is mandatory.
    ("investigate_first", ""): (
        "high",
        "Investigate-first signals (MATTR / MTLD / Yule's K / "
        "Shannon entropy) ask 'what local cause produced the "
        "signal?' before any revision. Treating the diagnostic as "
        "a target is the canonical metric-gaming failure mode the "
        "framework is designed to resist.",
    ),

    # Avoid-direct: aggregate divergence / KL / Delta / cosine.
    ("avoid_direct", ""): (
        "high",
        "Avoid-direct signals (overall KL / Burrows Delta / cosine "
        "distance / char n-gram aggregates) are evidence summaries, "
        "not writing goals. Optimizing them directly is the "
        "framework's structural anti-goal.",
    ),
}


def classify_revision_risk(
    targetability: str, signal: str, severity: str = "moderate",
) -> tuple[str, str]:
    """Map (targetability, signal) → (risk, rationale).

    Lookup by (targetability, signal-substring) — the table's
    second key is matched as a substring of the signal name so
    `tier1.sentence_length.burstiness_B` matches both
    `("direct", "sentence_length")` and `("direct", "burstiness")`
    (whichever is checked first). When multiple keys match, the
    first matching table entry wins; severity 'heavy' bumps low →
    medium and medium → high to reflect the larger-stakes
    intervention.
    """
    sig_lower = (signal or "").lower()
    risk = "unspecified"
    rationale = ""
    # Targetability-only fallbacks (investigate_first / avoid_direct
    # entries with empty signal key) are general for those classes.
    fallback = _RISK_TABLE.get((targetability, ""))
    if fallback:
        risk, rationale = fallback
    # Signal-specific entries (direct / translated) override.
    for (t, sig_sub), (r, msg) in _RISK_TABLE.items():
        if t != targetability:
            continue
        if not sig_sub:
            continue
        if sig_sub in sig_lower:
            risk, rationale = r, msg
            break
    if risk == "unspecified":
        # Default: low for direct/translated, high for the
        # avoid-direct family fallback.
        if targetability in {"direct", "translated"}:
            risk = "low"
            rationale = (
                "No specific risk pattern matched this signal. "
                "Default-low: revise toward the named prose move; "
                "verify the change with the post-check before "
                "committing."
            )
        elif targetability in {"investigate_first", "avoid_direct"}:
            risk = "high"
            rationale = (
                "No specific rationale matched; default-high for "
                "this targetability class because direct revision "
                "against the signal is structurally discouraged."
            )
    if severity == "heavy":
        if risk == "low":
            risk = "medium"
        elif risk == "medium":
            risk = "high"
    return risk, rationale


def apply_revision_risk(packet: Packet) -> Packet:
    """Fill the revision_risk + revision_risk_rationale fields on a
    packet. Returns the same packet for chaining."""
    risk, rationale = classify_revision_risk(
        packet.targetability, packet.signal, packet.severity,
    )
    packet.revision_risk = risk
    packet.revision_risk_rationale = rationale
    return packet


# --------------- Severity classification --------------------


def _kl_severity(value: float) -> str:
    if value >= 0.030:
        return "heavy"
    if value >= 0.015:
        return "moderate"
    return "light"


def _zscore_severity(value: float) -> str:
    a = abs(value)
    if a >= 3.0:
        return "heavy"
    if a >= 2.0:
        return "moderate"
    return "light"


# --------------- Variance audit consumer --------------------


def packets_from_variance(
    variance: dict[str, Any],
) -> list[Packet]:
    """Pull direct + investigate-first signals from variance_audit JSON.

    Reads `flagged_signals`, the per-signal values from tier1/tier2/
    tier3, and the compression band. Investigate-first signals (mattr,
    mtld, yules_k, shannon_entropy) emit diagnostic prompts; direct
    signals (burstiness_B, connective_density, fkgl_sd, adjacent_*)
    emit revision instructions.
    """
    out: list[Packet] = []
    compression = variance.get("compression") or {}
    flagged: list[str] = compression.get("flagged_signals") or []
    band = compression.get("band")
    notes_post_check = [
        "Rerun scripts/variance_audit.py with the same baseline.",
    ]

    for signal in flagged:
        if signal in DIRECT_TARGETS:
            entry = DIRECT_TARGETS[signal]
            value = _extract_signal_value(variance, signal)
            severity = "moderate" if value is None else _zscore_severity(
                _baseline_z(variance, signal) or 0.0
            )
            out.append(Packet(
                id=f"variance_{signal}_direct",
                targetability="direct",
                signal=f"variance_audit signal {signal!r}",
                direction="over_represented",
                severity=severity,
                evidence={
                    "metric": signal,
                    "value": value,
                    "band": band,
                    "baseline_direction": "above writer baseline" if (_baseline_z(variance, signal) or 0) > 0 else "below writer baseline",
                },
                plain_language_diagnosis=entry["diagnosis"],
                revision_moves=[entry["move_over"]],
                guardrails=list(GUARDRAILS_DEFAULT),
                post_check=list(notes_post_check),
            ))
        elif signal in INVESTIGATE_FIRST:
            value = _extract_signal_value(variance, signal)
            out.append(Packet(
                id=f"variance_{signal}_investigate",
                targetability="investigate_first",
                signal=f"variance_audit signal {signal!r}",
                direction="unspecified",
                severity="moderate",
                evidence={"metric": signal, "value": value, "band": band},
                plain_language_diagnosis=(
                    f"The {signal!r} signal moved, but it is not a safe "
                    f"direct revision target. Inspect causes before "
                    f"rewriting."
                ),
                revision_moves=[
                    INVESTIGATE_FIRST[signal],
                    "Return causes, evidence, and candidate moves; do not rewrite yet.",
                ],
                guardrails=list(GUARDRAILS_DEFAULT),
                post_check=list(notes_post_check),
            ))

    # POS-bigram KL is avoid-direct as an aggregate, but its evidence
    # is informative.
    pb_kl = compression.get("pos_bigram_kl") if compression else None
    if isinstance(pb_kl, dict) and pb_kl.get("compressed"):
        out.append(Packet(
            id="variance_pos_bigram_kl_aggregate",
            targetability="avoid_direct",
            signal="POS-bigram KL aggregate against baseline",
            direction="over_represented",
            severity=_kl_severity(pb_kl.get("value") or 0.0),
            evidence={
                "metric": "kl_to_baseline",
                "value": pb_kl.get("value"),
                "threshold": pb_kl.get("threshold"),
                "baseline_direction": "above writer baseline",
            },
            plain_language_diagnosis=AVOID_DIRECT["pos_bigram_kl_total"],
            revision_moves=[
                "Do not optimize aggregate KL directly. Use bigram_diff.py "
                "to find the top contributing bigrams; the packet generator "
                "translates those into prose moves under `translated`.",
            ],
            guardrails=list(GUARDRAILS_DEFAULT),
            post_check=list(notes_post_check),
        ))

    return out


def _extract_signal_value(variance: dict[str, Any], signal: str) -> float | None:
    """Walk known dotted paths to extract a scalar for the named
    signal. Mirrors the heuristic-key-to-dotted-path map in
    variance_audit._BASELINE_PATH_TO_HEURISTIC."""
    paths = {
        "burstiness_B": ("tier1", "sentence_length", "burstiness_B"),
        "connective_density": ("tier1", "connective_density", "per_1000_tokens"),
        "mattr": ("tier1", "mattr", "value"),
        "mtld": ("tier1", "mtld"),
        "yules_k": ("tier1", "yules_k"),
        "shannon_entropy": ("tier1", "shannon_entropy_bits"),
        "fkgl_sd": ("tier1", "fkgl", "sd"),
        "sentence_length_sd": ("tier1", "sentence_length", "sd"),
        "adjacent_cosine_mean": ("tier3", "adjacent_cosine", "mean"),
        "adjacent_cosine_sd": ("tier3", "adjacent_cosine", "sd"),
        "mdd_sd": ("tier2", "mdd", "sd"),
    }
    path = paths.get(signal)
    if not path:
        return None
    audit = variance.get("audit") or variance
    d: Any = audit
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return float(d) if isinstance(d, (int, float)) else None


def _baseline_z(variance: dict[str, Any], signal: str) -> float | None:
    """Pull the baseline-z score for a signal from variance_audit's
    `baseline_comparison` block, when present."""
    comparison = variance.get("baseline_comparison") or {}
    z_scores = comparison.get("z_scores") or {}
    paths = {
        "burstiness_B": "tier1.sentence_length.burstiness_B",
        "connective_density": "tier1.connective_density.per_1000_tokens",
        "mattr": "tier1.mattr.value",
        "mtld": "tier1.mtld",
        "yules_k": "tier1.yules_k",
        "shannon_entropy": "tier1.shannon_entropy_bits",
        "fkgl_sd": "tier1.fkgl.sd",
        "adjacent_cosine_mean": "tier3.adjacent_cosine.mean",
        "adjacent_cosine_sd": "tier3.adjacent_cosine.sd",
        "mdd_sd": "tier2.mdd.sd",
    }
    path = paths.get(signal)
    if not path:
        return None
    entry = z_scores.get(path)
    if isinstance(entry, dict):
        z = entry.get("z_score")
        if isinstance(z, (int, float)) and math.isfinite(z):
            return float(z)
    return None


# --------------- Bigram-diff consumer -----------------------


def packets_from_bigram(
    bigram: dict[str, Any],
    *,
    max_translated_targets: int,
) -> list[Packet]:
    """Translate top bigram-diff contributors into prose-level moves.

    Uses the `pooled` rows when available, falling back to `mean`.
    Filters to bigrams with a translation in `POS_BIGRAM_TRANSLATIONS`;
    everything else falls into the avoid-direct bin (the aggregate KL
    packet, emitted separately by `packets_from_variance`).
    """
    out: list[Packet] = []
    diffs = bigram.get("diffs") or {}
    rows = (diffs.get("pooled") or {}).get("rows") or (
        (diffs.get("mean") or {}).get("rows") or []
    )
    if not rows:
        return out

    seen_bigrams: set[str] = set()
    n_translated = 0
    for row in rows:
        bg = row.get("bigram")
        if not isinstance(bg, str) or bg in seen_bigrams:
            continue
        seen_bigrams.add(bg)
        if bg not in POS_BIGRAM_TRANSLATIONS:
            continue
        if n_translated >= max_translated_targets:
            break
        kl_contrib = row.get("kl_contrib")
        if not isinstance(kl_contrib, (int, float)):
            continue
        direction = "over_represented" if kl_contrib > 0 else "under_represented"
        translation = POS_BIGRAM_TRANSLATIONS[bg]
        diagnosis_dir = translation["over"] if direction == "over_represented" else translation["under"]
        if diagnosis_dir == "(N/A)":
            # Markup contamination case under-represented direction is
            # not a craft concern; skip.
            continue
        out.append(Packet(
            id=f"pos_bigram_{bg.replace('-', '_')}_{direction}",
            targetability="translated",
            signal=f"POS bigram {bg}",
            direction=direction,
            severity=_kl_severity(abs(float(kl_contrib))),
            evidence={
                "metric": "kl_contribution",
                "value": float(kl_contrib),
                "log2_ratio": row.get("log2_ratio"),
                "target_prob": row.get("target_prob"),
                "baseline_prob": row.get("baseline_prob"),
                "target_count": row.get("target_count"),
                "baseline_count": row.get("baseline_count"),
            },
            plain_language_diagnosis=diagnosis_dir,
            revision_moves=[translation["move"]],
            guardrails=list(GUARDRAILS_DEFAULT),
            post_check=[
                "Rerun scripts/bigram_diff.py on the revised passage.",
                "Rerun scripts/variance_audit.py with the same baseline.",
            ],
        ))
        n_translated += 1

    return out


# --------------- Voice-distance consumer --------------------


def packets_from_voice(voice: dict[str, Any]) -> list[Packet]:
    """The aggregate voice-distance score is avoid-direct. Cluster
    contributors with translations are roadmap; v1 surfaces only the
    aggregate as evidence + an avoid-direct prompt.
    """
    out: list[Packet] = []
    score = voice.get("overall_distance") or voice.get("score")
    band = voice.get("band")
    if score is None and band is None:
        return out
    out.append(Packet(
        id="voice_distance_aggregate",
        targetability="avoid_direct",
        signal="voice_distance aggregate",
        direction="unspecified",
        severity="moderate",
        evidence={
            "metric": "overall_distance",
            "value": score,
            "band": band,
        },
        plain_language_diagnosis=AVOID_DIRECT["burrows_delta_overall"],
        revision_moves=[
            "Do not optimize aggregate voice distance. Inspect "
            "function-word clusters and idiolect contributors locally; "
            "translate cluster drift into prose moves only when the "
            "contributor pattern is interpretable.",
        ],
        guardrails=list(GUARDRAILS_DEFAULT),
        post_check=[
            "Rerun scripts/voice_distance.py with the same baseline.",
        ],
    ))
    return out


# --------------- Idiolect consumer --------------------------


def packets_from_idiolect(idiolect: dict[str, Any]) -> list[Packet]:
    """Idiolect output produces a preservation list, not a drift
    signal. The packet emits one direct target instructing the
    revision pass to preserve these phrases."""
    out: list[Packet] = []
    preservation = idiolect.get("preservation_list") or idiolect.get("preserve") or []
    if not preservation:
        return out
    # Limit the preservation list size to avoid prompt-bloat. The full
    # list is kept in the evidence block; the revision_moves contains
    # the top phrases inline for prompt context.
    preview = list(preservation)[:20]
    out.append(Packet(
        id="idiolect_preservation",
        targetability="direct",
        signal="idiolect preservation list",
        direction="unspecified",
        severity="unspecified",
        evidence={
            "metric": "preservation_list",
            "n_phrases": len(preservation),
            "phrases_preview": preview,
        },
        plain_language_diagnosis=DIRECT_TARGETS["idiolect_preservation"]["diagnosis"],
        revision_moves=[
            DIRECT_TARGETS["idiolect_preservation"]["move_under"],
            "Do not substitute synonyms for any phrase in the preservation list.",
        ],
        guardrails=list(GUARDRAILS_DEFAULT),
        post_check=[
            "Rerun scripts/idiolect_detector.py against the revised passage; "
            "verify the preservation list survived.",
        ],
    ))
    return out


# --------------- AIC pattern consumer -----------------------


def packets_from_aic(aic: dict[str, Any]) -> list[Packet]:
    """AIC pattern audit reports per-pattern density. Patterns whose
    density exceeds the writer's baseline (when supplied) are direct
    targets; the revision move is "apply source triage and rhetorical
    countermoves" rather than a specific syntactic fix."""
    out: list[Packet] = []
    patterns = aic.get("patterns") or aic.get("pattern_densities") or []
    if isinstance(patterns, dict):
        patterns = [
            {"name": k, **(v if isinstance(v, dict) else {"density": v})}
            for k, v in patterns.items()
        ]
    for p in patterns:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or p.get("pattern")
        flagged = p.get("flagged") or p.get("over_baseline") or False
        density = p.get("density") or p.get("per_1000_words")
        if not flagged or not name:
            continue
        out.append(Packet(
            id=f"aic_{str(name).lower().replace(' ', '_')}",
            targetability="direct",
            signal=f"AIC pattern {name}",
            direction="over_represented",
            severity="moderate",
            evidence={
                "metric": "density",
                "value": density,
                "baseline_direction": "above writer baseline",
            },
            plain_language_diagnosis=DIRECT_TARGETS["aic_pattern"]["diagnosis"],
            revision_moves=[
                DIRECT_TARGETS["aic_pattern"]["move_over"],
                f"Apply source triage to each {name} instance; decide earned vs. unearned per case.",
            ],
            guardrails=list(GUARDRAILS_DEFAULT),
            post_check=[
                "Rerun scripts/aic_pattern_audit.py with the same baseline.",
            ],
        ))
    return out


# --------------- Top-level packet assembly ------------------


def build_packets(
    *,
    variance: dict[str, Any] | None,
    bigram: dict[str, Any] | None,
    voice: dict[str, Any] | None,
    idiolect: dict[str, Any] | None,
    aic: dict[str, Any] | None,
    max_targets: int,
    targetability_filter: set[str] | None,
) -> list[Packet]:
    all_packets: list[Packet] = []
    if variance:
        all_packets.extend(packets_from_variance(variance))
    if bigram:
        all_packets.extend(packets_from_bigram(
            bigram, max_translated_targets=max_targets,
        ))
    if voice:
        all_packets.extend(packets_from_voice(voice))
    if idiolect:
        all_packets.extend(packets_from_idiolect(idiolect))
    if aic:
        all_packets.extend(packets_from_aic(aic))

    if targetability_filter is not None:
        all_packets = [
            p for p in all_packets
            if p.targetability in targetability_filter
        ]

    # Order: direct first, then translated, then investigate_first;
    # avoid_direct goes last (kept as evidence, not as a prompt
    # instruction). Within each class, sort by severity.
    severity_rank = {"heavy": 0, "moderate": 1, "light": 2, "unspecified": 3}
    class_rank = {
        "direct": 0,
        "translated": 1,
        "investigate_first": 2,
        "avoid_direct": 3,
    }
    all_packets.sort(
        key=lambda p: (
            class_rank.get(p.targetability, 9),
            severity_rank.get(p.severity, 9),
        )
    )
    # Enforce the per-prompt cap on actionable targets only (direct +
    # translated). investigate_first and avoid_direct stay as
    # context.
    actionable_kept = 0
    out: list[Packet] = []
    for p in all_packets:
        if p.targetability in ("direct", "translated"):
            if actionable_kept >= max_targets:
                continue
            actionable_kept += 1
        # Revision-risk classification (Release 4): each packet
        # gets a risk + rationale label that surfaces what could
        # go wrong if this revision is followed naively.
        apply_revision_risk(p)
        out.append(p)
    return out


# --------------- Prompt assembly ----------------------------


def build_prompt_block(
    packets: Sequence[Packet],
    *,
    target_scope: str | None,
    genre: str | None,
) -> dict[str, Any]:
    actionable = [p for p in packets if p.targetability in ("direct", "translated")]
    investigate = [p for p in packets if p.targetability == "investigate_first"]

    if not actionable and not investigate:
        return {
            "model_instruction": (
                "No actionable revision targets surfaced. Inspect the "
                "passage manually before any revision pass."
            ),
            "revision_brief": "",
            "post_check_commands": [],
        }

    scope_str = (
        f"\nRevise only this scope: {target_scope}." if target_scope else ""
    )
    genre_str = f" Genre: {genre}." if genre else ""

    if actionable:
        moves = "\n".join(
            f"  - {p.signal}: {' / '.join(p.revision_moves)}"
            for p in actionable
        )
        guardrails = "\n".join(f"  - {g}" for g in GUARDRAILS_DEFAULT)
        revision_brief = (
            f"Targeted revision packet for SETEC-detected drift.{genre_str}"
            f"{scope_str}\n\n"
            f"Targets:\n{moves}\n\n"
            f"Guardrails:\n{guardrails}\n\n"
            "After revision, run the post-check commands listed below "
            "and report whether the targeted signals moved in the "
            "intended direction without degrading neighboring signals "
            "or the idiolect preservation list."
        )
    else:
        revision_brief = ""

    if investigate:
        diag = "\n".join(
            f"  - {p.signal}: {p.plain_language_diagnosis}"
            for p in investigate
        )
        diagnostic_brief = (
            "The diagnostic surfaced signals that are not safe direct "
            "revision targets. Before any rewrite, return:\n"
            "1. Which repeated words / phrase templates / structural "
            "patterns drive each signal?\n"
            "2. Are they thematic / project anchors, closed-scene "
            "constraints, or generic smoothing?\n"
            "3. Which two local edits would address the cause without "
            "optimizing the metric directly?\n\n"
            f"Investigate-first signals:\n{diag}\n"
        )
    else:
        diagnostic_brief = ""

    if revision_brief and diagnostic_brief:
        full_instruction = (
            f"{revision_brief}\n\n--- Investigate-first diagnostic ---\n\n"
            f"{diagnostic_brief}"
        )
    else:
        full_instruction = revision_brief or diagnostic_brief

    post_check_commands: list[str] = []
    seen: set[str] = set()
    for p in actionable + investigate:
        for cmd in p.post_check:
            if cmd not in seen:
                post_check_commands.append(cmd)
                seen.add(cmd)

    return {
        "model_instruction": full_instruction,
        "revision_brief": revision_brief,
        "diagnostic_brief": diagnostic_brief,
        "post_check_commands": post_check_commands,
    }


# --------------- Output rendering ---------------------------


CLAIM_LICENSE = {
    "licenses": (
        "Revision targets for measured drift, with targetability "
        "classification, plain-language translation of distributional "
        "signals, and required post-check commands."
    ),
    "does_not_license": (
        "AI provenance, authorship attribution, or proof the revision "
        "is better. Optimizing metrics directly without inspecting "
        "local examples is not a use of this packet; the framework's "
        "metric-gaming resistance lives in the targetability taxonomy."
    ),
}


def render_json(
    packets: Sequence[Packet],
    prompt_block: dict[str, Any],
    *,
    inputs: dict[str, str | None],
    target_scope: str | None,
    genre: str | None,
    private: bool,
) -> str:
    out = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "inputs": inputs,
        "target_scope": target_scope,
        "genre": genre,
        "private": private,
        "claim_license": CLAIM_LICENSE,
        "n_packets": len(packets),
        "packets": [p.to_dict() for p in packets],
        "prompt": prompt_block,
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def render_markdown(
    packets: Sequence[Packet],
    prompt_block: dict[str, Any],
    *,
    target_scope: str | None,
    genre: str | None,
    private: bool,
    show_poor_targets: bool,
) -> str:
    lines: list[str] = [
        "# Metric-Targeted Restoration Packet",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Genre:** {genre or 'unspecified'}",
        f"**Target scope:** {target_scope or 'whole document'}",
        f"**Private:** {private}" if private else "",
        "",
        f"**Claim:** {CLAIM_LICENSE['licenses']}",
        "",
        f"**Does NOT claim:** {CLAIM_LICENSE['does_not_license']}",
        "",
    ]

    actionable = [p for p in packets if p.targetability in ("direct", "translated")]
    investigate = [p for p in packets if p.targetability == "investigate_first"]
    avoid = [p for p in packets if p.targetability == "avoid_direct"]

    if actionable:
        lines.append("## Targets (actionable)")
        lines.append("")
        for i, p in enumerate(actionable, start=1):
            lines.extend(_render_packet_md(i, p))
            lines.append("")

    if investigate:
        lines.append("## Investigate first")
        lines.append("")
        lines.append(
            "These signals moved but are not safe direct revision "
            "targets. Inspect causes before any rewrite."
        )
        lines.append("")
        for i, p in enumerate(investigate, start=1):
            lines.extend(_render_packet_md(i, p))
            lines.append("")

    if avoid and show_poor_targets:
        lines.append("## Evidence (do not target directly)")
        lines.append("")
        lines.append(
            "These signals are summary measurements, not revision "
            "goals. They appear here as context only; optimizing them "
            "directly invites metric gaming."
        )
        lines.append("")
        for i, p in enumerate(avoid, start=1):
            lines.extend(_render_packet_md(i, p, brief=True))
            lines.append("")

    if prompt_block.get("model_instruction"):
        lines.append("## Prompt for model or human reviser")
        lines.append("")
        lines.append("```")
        lines.append(prompt_block["model_instruction"])
        lines.append("```")
        lines.append("")

    if prompt_block.get("post_check_commands"):
        lines.append("## Post-check commands")
        lines.append("")
        for cmd in prompt_block["post_check_commands"]:
            lines.append(f"- `{cmd}`")
        lines.append("")

    # Filter empty lines from the leading metadata block (the
    # `**Private:** {private}` if not private).
    return "\n".join(line for line in lines if line is not None) + "\n"


def _render_packet_md(
    index: int, p: Packet, *, brief: bool = False,
) -> list[str]:
    lines = [f"### Target {index}: {p.signal}"]
    lines.append("")
    lines.append(f"- **Targetability:** `{p.targetability}`")
    lines.append(f"- **Direction:** `{p.direction}`")
    lines.append(f"- **Severity:** `{p.severity}`")
    # Revision risk (Release 4): per-packet risk label estimating
    # what can go wrong if this revision is followed naively. ⚠
    # marker on medium / high so the reader sees the caveat
    # alongside the targetability and severity.
    risk_marker = (
        "⚠ "
        if p.revision_risk in {"medium", "high"} else ""
    )
    if p.revision_risk and p.revision_risk != "unspecified":
        lines.append(
            f"- {risk_marker}**Revision risk:** `{p.revision_risk}`"
        )
        if p.revision_risk_rationale:
            lines.append(
                f"  - Rationale: {p.revision_risk_rationale}"
            )
    if p.evidence:
        ev_lines = []
        for k, v in p.evidence.items():
            if isinstance(v, list) and len(v) > 5:
                ev_lines.append(f"  - {k}: {v[:5]} ... ({len(v)} total)")
            else:
                ev_lines.append(f"  - {k}: {v}")
        lines.append("- **Evidence:**")
        lines.extend(ev_lines)
    lines.append(f"- **Diagnosis:** {p.plain_language_diagnosis}")
    if brief:
        return lines
    if p.revision_moves:
        lines.append("- **Revision moves:**")
        for m in p.revision_moves:
            lines.append(f"  - {m}")
    if p.guardrails:
        lines.append("- **Guardrails:**")
        for g in p.guardrails:
            lines.append(f"  - {g}")
    if p.post_check:
        lines.append("- **Post-check:**")
        for c in p.post_check:
            lines.append(f"  - `{c}`")
    return lines


# --------------- Privacy guard ------------------------------


def _is_private(
    *, idiolect_path: str | None, voice_path: str | None,
) -> bool:
    return bool(idiolect_path) or bool(voice_path)


def _check_output_privacy(
    out_path: Path | None, json_path: Path | None,
    *, private: bool, allow_public: bool,
) -> None:
    """If private signals are present and the output path is outside
    ai-prose-baselines-private/, refuse unless --allow-public-output."""
    if not private or allow_public:
        return
    repo_root = Path(__file__).resolve().parent.parent
    private_dir = repo_root / "ai-prose-baselines-private"
    for p in (out_path, json_path):
        if p is None:
            continue
        try:
            p.resolve().relative_to(private_dir.resolve())
        except ValueError:
            sys.stderr.write(
                f"Refusing to write {p} outside {private_dir} when "
                f"private inputs (idiolect/voice) are present. Pass "
                f"--allow-public-output to override.\n"
            )
            sys.exit(2)


# --------------- CLI ---------------------------------------


def _load_json_arg(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        sys.stderr.write(f"Input not found: {p}\n")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Translate SETEC diagnostic JSON outputs into a metric-"
            "targeted restoration packet."
        )
    )
    parser.add_argument("--variance-json", help="JSON output from variance_audit.py")
    parser.add_argument("--bigram-json", help="JSON output from bigram_diff.py / manuscript_bigram_diff.py")
    parser.add_argument("--voice-json", help="JSON output from voice_distance.py")
    parser.add_argument("--idiolect-json", help="JSON output from idiolect_detector.py")
    parser.add_argument("--aic-json", help="JSON output from aic_pattern_audit.py")
    parser.add_argument("--genre", help="Genre tag for the prompt context.")
    parser.add_argument("--target-scope", help="Locality of the revision (e.g., 'paragraphs 4-8').")
    parser.add_argument("--max-targets", type=int, default=3,
                        help="Maximum direct + translated targets per packet (default 3).")
    parser.add_argument(
        "--targetability",
        default="all",
        choices=("all", "direct", "translated", "investigate_first", "actionable"),
        help=(
            "Filter packets by targetability class. 'actionable' = "
            "direct + translated only."
        ),
    )
    parser.add_argument("--out", help="Markdown output path.")
    parser.add_argument("--json-out", help="JSON output path.")
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="Suppress the prompt block; emit the targets only.",
    )
    parser.add_argument(
        "--show-poor-targets", action="store_true", default=True,
        help="Include avoid_direct evidence in markdown output (default on).",
    )
    parser.add_argument(
        "--no-show-poor-targets", dest="show_poor_targets", action="store_false",
    )
    parser.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Allow markdown/JSON output outside ai-prose-baselines-"
            "private/ when private inputs (idiolect/voice) are used."
        ),
    )
    args = parser.parse_args(argv)

    inputs_present = any([
        args.variance_json, args.bigram_json, args.voice_json,
        args.idiolect_json, args.aic_json,
    ])
    if not inputs_present:
        sys.stderr.write(
            "At least one of --variance-json, --bigram-json, "
            "--voice-json, --idiolect-json, --aic-json is required.\n"
        )
        return 1

    variance = _load_json_arg(args.variance_json)
    bigram = _load_json_arg(args.bigram_json)
    voice = _load_json_arg(args.voice_json)
    idiolect = _load_json_arg(args.idiolect_json)
    aic = _load_json_arg(args.aic_json)

    private = _is_private(
        idiolect_path=args.idiolect_json,
        voice_path=args.voice_json,
    )
    if private:
        sys.stderr.write(
            "Note: idiolect or voice-distance inputs supplied; packet "
            "is marked private. Output is restricted to "
            "ai-prose-baselines-private/ unless --allow-public-output.\n"
        )

    out_path = Path(args.out) if args.out else None
    json_path = Path(args.json_out) if args.json_out else None
    _check_output_privacy(
        out_path, json_path, private=private,
        allow_public=args.allow_public_output,
    )

    targetability_filter: set[str] | None
    if args.targetability == "all":
        targetability_filter = None
    elif args.targetability == "actionable":
        targetability_filter = {"direct", "translated"}
    else:
        targetability_filter = {args.targetability}

    packets = build_packets(
        variance=variance, bigram=bigram, voice=voice,
        idiolect=idiolect, aic=aic,
        max_targets=args.max_targets,
        targetability_filter=targetability_filter,
    )

    if args.no_prompt:
        prompt_block = {
            "model_instruction": "",
            "revision_brief": "",
            "post_check_commands": [],
        }
    else:
        prompt_block = build_prompt_block(
            packets, target_scope=args.target_scope, genre=args.genre,
        )

    inputs_dict = {
        "variance_json": args.variance_json,
        "bigram_json": args.bigram_json,
        "voice_json": args.voice_json,
        "idiolect_json": args.idiolect_json,
        "aic_json": args.aic_json,
    }

    json_out = render_json(
        packets, prompt_block, inputs=inputs_dict,
        target_scope=args.target_scope, genre=args.genre, private=private,
    )
    md_out = render_markdown(
        packets, prompt_block,
        target_scope=args.target_scope, genre=args.genre, private=private,
        show_poor_targets=args.show_poor_targets,
    )

    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_out, encoding="utf-8")
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md_out, encoding="utf-8")
    if not json_path and not out_path:
        sys.stdout.write(md_out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
