#!/usr/bin/env python3
"""before_after_restoration.py — Cathedral upgrade #7 v2.

Automates the post-check loop that `restoration_packet.py` (v1)
required users to run manually. Reads "before" and "after" diagnostic
JSONs (variance audit, POS-bigram diff, voice distance, idiolect
detector, AIC pattern audit) and the original restoration packet,
and reports per-target verdicts:

  improved   Signal moved in the intended direction by more than the
             noise threshold.
  no_change  Signal moved within the noise threshold either way.
  degraded   Signal moved opposite to the intended direction.
  gamed      Signal improved AND a related avoid_direct aggregate
             moved against improvement -- a sign that the revision
             optimized the local target without addressing the
             underlying drift, which is the metric-gaming failure
             mode the framework's targetability taxonomy is designed
             to resist.

Two modes:

  Packet-driven (--packet-json supplied):
    Evaluates each target in the original packet against its
    before/after values, applies direction-aware improvement logic
    (looking up registry direction for variance signals and
    |kl_contrib| reduction for bigram signals), runs the metric-
    gaming heuristic, and reports per-target verdicts.

  Diff-only (no --packet-json):
    Reports raw deltas across all measurable signals. Useful for
    general "what changed" inspection without committing to a
    pre-registered set of targets.

If --original-text and --revised-text are supplied alongside an
idiolect packet, the script also checks preservation-list survival
in the revised text (case-insensitive substring search).

Usage:

    python3 scripts/before_after_restoration.py \\
        --packet-json packet.json \\
        --before-variance-json before/variance.json \\
        --after-variance-json after/variance.json \\
        --before-bigram-json before/bigram.json \\
        --after-bigram-json after/bigram.json \\
        --original-text original.txt \\
        --revised-text revised.txt \\
        --out report.md \\
        --json-out report.json

task_surface: craft_restoration. Mirrors restoration_packet.py's
posture: never claims AI provenance, never claims the revision is
"better" -- only that it moved (or didn't) the targeted signals
in their intended directions, and whether the move came at the
cost of neighboring signals or the idiolect preservation list.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from variance_audit import COMPRESSION_HEURISTICS  # type: ignore


TASK_SURFACE = "craft_restoration"
TOOL_NAME = "before_after_restoration"


# Per-signal noise thresholds. A delta within ± threshold counts as
# "no change" rather than "improved" or "degraded." Conservative
# defaults; calibration roadmap.
NOISE_THRESHOLDS: dict[str, float] = {
    "burstiness_B": 0.05,
    "connective_density": 1.0,
    "mattr": 0.02,
    "mtld": 5.0,
    "yules_k": 10.0,
    "shannon_entropy": 0.10,
    "fkgl_sd": 0.20,
    "sentence_length_sd": 0.50,
    "adjacent_cosine_mean": 0.03,
    "adjacent_cosine_sd": 0.02,
    "mdd_sd": 0.05,
    # POS-bigram per-bigram kl_contrib (sub-divergence units)
    "kl_contribution": 0.005,
    # Aggregate POS-bigram KL
    "pos_bigram_kl_total": 0.02,
    # Aggregate voice distance
    "voice_distance_overall": 0.10,
}

# When a direct/translated target improves AND one of these aggregates
# moves against improvement by more than its noise threshold, flag
# the verdict as "gamed."
GAMING_AGGREGATES = (
    "pos_bigram_kl_total",
    "voice_distance_overall",
)


# --------------- Signal extractors --------------------------


def _extract_variance_value(audit: dict[str, Any], signal_key: str) -> float | None:
    """Pull a scalar from a variance_audit JSON for the named signal
    key. Mirrors restoration_packet._extract_signal_value."""
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
    path = paths.get(signal_key)
    if not path:
        return None
    d: Any = audit.get("audit") or audit
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    return float(d) if isinstance(d, (int, float)) else None


def _extract_pos_bigram_kl_total(audit: dict[str, Any]) -> float | None:
    """Aggregate POS-bigram KL from variance_audit JSON."""
    compression = audit.get("compression") or {}
    pb_kl = compression.get("pos_bigram_kl") or {}
    v = pb_kl.get("value")
    return float(v) if isinstance(v, (int, float)) else None


def _extract_band(audit: dict[str, Any]) -> str | None:
    compression = audit.get("compression") or {}
    return compression.get("band")


def _extract_compression_fraction(audit: dict[str, Any]) -> float | None:
    compression = audit.get("compression") or {}
    f = compression.get("compression_fraction")
    return float(f) if isinstance(f, (int, float)) else None


def _extract_voice_distance(voice: dict[str, Any]) -> float | None:
    v = voice.get("overall_distance") or voice.get("score")
    return float(v) if isinstance(v, (int, float)) else None


def _bigram_kl_contribs(bigram: dict[str, Any]) -> dict[str, float]:
    """Return {bigram_key: kl_contrib} from a bigram_diff JSON.
    Prefers pooled rows; falls back to mean rows."""
    diffs = bigram.get("diffs") or {}
    rows = (diffs.get("pooled") or {}).get("rows") or (
        (diffs.get("mean") or {}).get("rows") or []
    )
    out: dict[str, float] = {}
    for r in rows:
        bg = r.get("bigram")
        kl = r.get("kl_contrib")
        if isinstance(bg, str) and isinstance(kl, (int, float)):
            out[bg] = float(kl)
    return out


def _bigram_kl_total(bigram: dict[str, Any]) -> float | None:
    """Sum |kl_contrib| across all bigrams in the diff (a proxy for
    aggregate divergence; the diff JSON also reports a `kl_total`
    field at the diff level)."""
    diffs = bigram.get("diffs") or {}
    pooled = diffs.get("pooled") or {}
    if "kl_total" in pooled and isinstance(pooled["kl_total"], (int, float)):
        return float(pooled["kl_total"])
    rows = pooled.get("rows") or []
    if not rows:
        rows = (diffs.get("mean") or {}).get("rows") or []
    if not rows:
        return None
    return float(sum(r.get("kl_contrib", 0.0) for r in rows))


# --------------- Verdict logic ------------------------------


@dataclass
class TargetVerdict:
    target_id: str
    signal: str
    targetability: str
    direction: str
    before: float | None
    after: float | None
    delta: float | None
    signed_improvement: float | None  # > 0 = moved in intended direction
    noise_threshold: float | None
    verdict: str  # improved / no_change / degraded / gamed / not_measurable
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "signal": self.signal,
            "targetability": self.targetability,
            "direction": self.direction,
            "before": self.before,
            "after": self.after,
            "delta": self.delta,
            "signed_improvement": self.signed_improvement,
            "noise_threshold": self.noise_threshold,
            "verdict": self.verdict,
            "notes": self.notes,
        }


def _signed_improvement_variance(
    signal_key: str, before: float, after: float,
) -> tuple[float, float, float | None]:
    """For a variance signal, return (delta, signed_improvement,
    noise). signed_improvement > 0 means the signal moved in the
    direction that counts as improvement (away from the heuristic's
    fired-threshold side)."""
    delta = after - before
    spec = COMPRESSION_HEURISTICS.get(signal_key)
    noise = NOISE_THRESHOLDS.get(signal_key)
    if spec is None:
        return delta, 0.0, noise
    if spec.direction == "lt":
        # Heuristic fires when value < threshold; improvement = value goes up
        return delta, delta, noise
    else:  # "gt"
        # Heuristic fires when value > threshold; improvement = value goes down
        return delta, -delta, noise


def _signed_improvement_bigram(
    before_kl: float, after_kl: float,
) -> tuple[float, float, float]:
    """For a bigram packet, improvement = |kl_contrib| decreases.
    Returns (delta, signed_improvement, noise)."""
    delta = after_kl - before_kl
    signed_improvement = abs(before_kl) - abs(after_kl)
    noise = NOISE_THRESHOLDS["kl_contribution"]
    return delta, signed_improvement, noise


def _classify_verdict(
    signed_improvement: float | None, noise: float | None,
) -> str:
    if signed_improvement is None or noise is None:
        return "not_measurable"
    if signed_improvement > noise:
        return "improved"
    if signed_improvement < -noise:
        return "degraded"
    return "no_change"


# --------------- Per-target evaluation ----------------------


def evaluate_packet(
    packet: dict[str, Any],
    *,
    before_variance: dict[str, Any] | None,
    after_variance: dict[str, Any] | None,
    before_bigram: dict[str, Any] | None,
    after_bigram: dict[str, Any] | None,
    before_voice: dict[str, Any] | None,
    after_voice: dict[str, Any] | None,
    before_idiolect: dict[str, Any] | None,
    after_idiolect: dict[str, Any] | None,
) -> TargetVerdict:
    """Compute a verdict for one packet from the original restoration
    packet. Dispatches on the packet's `id` prefix and reads
    before/after values from the corresponding JSON pair."""
    pid = packet.get("id", "")
    signal = packet.get("signal", "")
    targetability = packet.get("targetability", "unspecified")
    direction = packet.get("direction", "unspecified")
    notes: list[str] = []

    # Variance-audit packets: id starts with "variance_<signal_key>_..."
    if pid.startswith("variance_") and pid != "variance_pos_bigram_kl_aggregate":
        # Strip "variance_" prefix and "_direct" / "_investigate" suffix
        signal_key = pid[len("variance_"):]
        for suffix in ("_direct", "_investigate"):
            if signal_key.endswith(suffix):
                signal_key = signal_key[:-len(suffix)]
                break
        before = _extract_variance_value(before_variance or {}, signal_key)
        after = _extract_variance_value(after_variance or {}, signal_key)
        if before is None or after is None:
            return TargetVerdict(
                target_id=pid, signal=signal, targetability=targetability,
                direction=direction, before=before, after=after,
                delta=None, signed_improvement=None,
                noise_threshold=NOISE_THRESHOLDS.get(signal_key),
                verdict="not_measurable",
                notes=["Could not extract before or after value from variance_audit JSON."],
            )
        delta, signed_improvement, noise = _signed_improvement_variance(
            signal_key, before, after,
        )
        if targetability == "investigate_first":
            # We can't claim improvement; just report the delta.
            return TargetVerdict(
                target_id=pid, signal=signal, targetability=targetability,
                direction=direction, before=before, after=after,
                delta=delta, signed_improvement=None,
                noise_threshold=noise, verdict="not_measurable",
                notes=[
                    "investigate_first signal: delta reported but no "
                    "improvement claim made (the signal needs local "
                    "diagnostic interpretation, not a metric verdict)."
                ],
            )
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=before, after=after,
            delta=delta, signed_improvement=signed_improvement,
            noise_threshold=noise,
            verdict=_classify_verdict(signed_improvement, noise),
            notes=notes,
        )

    # Aggregate POS-bigram KL packet (avoid_direct from variance_audit)
    if pid == "variance_pos_bigram_kl_aggregate":
        before = _extract_pos_bigram_kl_total(before_variance or {})
        after = _extract_pos_bigram_kl_total(after_variance or {})
        if before is None or after is None:
            return TargetVerdict(
                target_id=pid, signal=signal, targetability=targetability,
                direction=direction, before=before, after=after,
                delta=None, signed_improvement=None,
                noise_threshold=NOISE_THRESHOLDS["pos_bigram_kl_total"],
                verdict="not_measurable",
                notes=["Could not extract aggregate POS-bigram KL from variance_audit JSON."],
            )
        # avoid_direct: improvement = aggregate KL goes down. But we
        # don't claim "improved" on an avoid_direct target -- we
        # report the delta as evidence for the metric-gaming check.
        delta = after - before
        signed_improvement = -delta  # going down is good
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=before, after=after,
            delta=delta, signed_improvement=signed_improvement,
            noise_threshold=NOISE_THRESHOLDS["pos_bigram_kl_total"],
            verdict="not_measurable",  # avoid_direct: never claim improvement
            notes=[
                "avoid_direct aggregate: delta reported as evidence "
                "for metric-gaming detection, not as a claim that the "
                "revision improved this metric."
            ],
        )

    # Bigram packets: id like "pos_bigram_<KEY>_<direction>"
    if pid.startswith("pos_bigram_"):
        # Extract bigram key from the id
        # id format: pos_bigram_DET_ADJ_over_represented or _under_represented
        body = pid[len("pos_bigram_"):]
        for direction_suffix in ("_over_represented", "_under_represented"):
            if body.endswith(direction_suffix):
                body = body[:-len(direction_suffix)]
                break
        # Convert underscores back to dashes for the bigram key
        bigram_key = body.replace("_", "-")
        before_kls = _bigram_kl_contribs(before_bigram or {})
        after_kls = _bigram_kl_contribs(after_bigram or {})
        before = before_kls.get(bigram_key)
        after = after_kls.get(bigram_key, 0.0)  # absent in after = 0 contribution
        if before is None:
            return TargetVerdict(
                target_id=pid, signal=signal, targetability=targetability,
                direction=direction, before=None, after=after,
                delta=None, signed_improvement=None,
                noise_threshold=NOISE_THRESHOLDS["kl_contribution"],
                verdict="not_measurable",
                notes=["Could not find bigram in before bigram_diff JSON."],
            )
        delta, signed_improvement, noise = _signed_improvement_bigram(before, after)
        if not after_bigram:
            notes.append("Bigram absent from after_bigram (treated as kl_contrib=0).")
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=before, after=after,
            delta=delta, signed_improvement=signed_improvement,
            noise_threshold=noise,
            verdict=_classify_verdict(signed_improvement, noise),
            notes=notes,
        )

    # Voice-distance aggregate (avoid_direct)
    if pid == "voice_distance_aggregate":
        before = _extract_voice_distance(before_voice or {})
        after = _extract_voice_distance(after_voice or {})
        if before is None or after is None:
            return TargetVerdict(
                target_id=pid, signal=signal, targetability=targetability,
                direction=direction, before=before, after=after,
                delta=None, signed_improvement=None,
                noise_threshold=NOISE_THRESHOLDS["voice_distance_overall"],
                verdict="not_measurable",
                notes=["Could not extract voice distance from before/after JSON."],
            )
        delta = after - before
        signed_improvement = -delta  # going down = closer to baseline
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=before, after=after,
            delta=delta, signed_improvement=signed_improvement,
            noise_threshold=NOISE_THRESHOLDS["voice_distance_overall"],
            verdict="not_measurable",  # avoid_direct
            notes=[
                "avoid_direct aggregate: delta reported as evidence "
                "for metric-gaming detection, not as a claim that the "
                "revision improved this metric."
            ],
        )

    # Idiolect preservation packet (handled separately by check_preservation)
    if pid == "idiolect_preservation":
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=None, after=None,
            delta=None, signed_improvement=None, noise_threshold=None,
            verdict="not_measurable",
            notes=[
                "Preservation list survival is checked separately via "
                "--original-text and --revised-text. See the "
                "preservation_check block in the JSON output."
            ],
        )

    # AIC pattern packets
    if pid.startswith("aic_"):
        # AIC density values aren't extracted from variance_audit;
        # they require an aic_pattern_audit JSON pair.
        # v1 reports the pattern name + delta if both fixtures are
        # supplied; we compare densities by the pattern's `name`
        # field within each AIC JSON.
        pattern_name = pid[len("aic_"):].replace("_", " ")
        before = _aic_density(before_idiolect or {}, pattern_name) if False else None
        # NOTE: AIC fixtures aren't a standard input here; the v1
        # fallback is to mark not_measurable. v2 should accept
        # --before-aic-json / --after-aic-json explicitly.
        return TargetVerdict(
            target_id=pid, signal=signal, targetability=targetability,
            direction=direction, before=None, after=None,
            delta=None, signed_improvement=None, noise_threshold=None,
            verdict="not_measurable",
            notes=[
                "AIC pattern verdict requires before/after AIC JSON; "
                "v1 of this script handles that via diff-only mode."
            ],
        )

    # Unknown packet shape
    return TargetVerdict(
        target_id=pid, signal=signal, targetability=targetability,
        direction=direction, before=None, after=None,
        delta=None, signed_improvement=None, noise_threshold=None,
        verdict="not_measurable",
        notes=[f"Unknown packet id shape: {pid!r}"],
    )


def _aic_density(aic: dict[str, Any], pattern_name: str) -> float | None:
    """Pull density for the named AIC pattern from an aic_pattern_audit
    JSON. Best-effort; the AIC schema accepts a list of dicts or a
    flat dict of name->density."""
    patterns = aic.get("patterns") or aic.get("pattern_densities") or []
    if isinstance(patterns, dict):
        v = patterns.get(pattern_name)
        if isinstance(v, dict):
            v = v.get("density")
        return float(v) if isinstance(v, (int, float)) else None
    if isinstance(patterns, list):
        for p in patterns:
            if isinstance(p, dict) and (p.get("name") or p.get("pattern")) == pattern_name:
                v = p.get("density") or p.get("per_1000_words")
                return float(v) if isinstance(v, (int, float)) else None
    return None


# --------------- Metric-gaming detection --------------------


def detect_gaming(
    verdicts: Sequence[TargetVerdict],
    aggregate_deltas: dict[str, float | None],
) -> list[TargetVerdict]:
    """If any actionable target improved AND a gaming-aggregate moved
    against improvement by more than its noise threshold, flip the
    target's verdict from 'improved' to 'gamed' and add a note.

    Returns a new list of verdicts (the originals are not mutated)."""
    flagged_aggregates: list[str] = []
    for agg_key in GAMING_AGGREGATES:
        delta = aggregate_deltas.get(agg_key)
        noise = NOISE_THRESHOLDS.get(agg_key, 0.0)
        if delta is not None and delta > noise:
            # Aggregate increased (got worse) by more than noise
            flagged_aggregates.append(agg_key)
    if not flagged_aggregates:
        return list(verdicts)
    out: list[TargetVerdict] = []
    for v in verdicts:
        if (
            v.verdict == "improved"
            and v.targetability in ("direct", "translated")
        ):
            new_notes = list(v.notes) + [
                f"Metric-gaming flag: targeted signal improved, but "
                f"avoid_direct aggregate(s) {flagged_aggregates} "
                f"moved against improvement by more than the noise "
                f"threshold. The revision may have optimized the "
                f"local target without addressing the underlying drift."
            ]
            out.append(TargetVerdict(
                target_id=v.target_id, signal=v.signal,
                targetability=v.targetability, direction=v.direction,
                before=v.before, after=v.after, delta=v.delta,
                signed_improvement=v.signed_improvement,
                noise_threshold=v.noise_threshold,
                verdict="gamed",
                notes=new_notes,
            ))
        else:
            out.append(v)
    return out


# --------------- Preservation-list survival -----------------


def check_preservation(
    revised_text: str, preservation_list: Sequence[str],
) -> dict[str, Any]:
    """Case-insensitive substring search for each preservation phrase
    in the revised text. Returns a dict reporting survival rate +
    missing phrases. Caps the missing-phrase list at 30 entries to
    keep the report bounded."""
    if not preservation_list:
        return {"checked": False, "reason": "no preservation list supplied"}
    revised_lower = revised_text.lower()
    survived: list[str] = []
    missing: list[str] = []
    for phrase in preservation_list:
        if not isinstance(phrase, str):
            continue
        if phrase.lower() in revised_lower:
            survived.append(phrase)
        else:
            missing.append(phrase)
    n_total = len(survived) + len(missing)
    return {
        "checked": True,
        "n_total": n_total,
        "n_survived": len(survived),
        "n_missing": len(missing),
        "survival_rate": (len(survived) / n_total) if n_total else 1.0,
        "missing_phrases": missing[:30],
    }


# --------------- Diff-only mode -----------------------------


def diff_all_signals(
    *,
    before_variance: dict[str, Any] | None,
    after_variance: dict[str, Any] | None,
    before_bigram: dict[str, Any] | None,
    after_bigram: dict[str, Any] | None,
    before_voice: dict[str, Any] | None,
    after_voice: dict[str, Any] | None,
) -> dict[str, Any]:
    """Report raw before/after deltas across all measurable signals
    without committing to a target list. Used in diff-only mode."""
    out: dict[str, Any] = {"variance": {}, "bigram": {}, "voice": {}}
    if before_variance and after_variance:
        for key in COMPRESSION_HEURISTICS.keys():
            b = _extract_variance_value(before_variance, key)
            a = _extract_variance_value(after_variance, key)
            if b is None or a is None:
                continue
            out["variance"][key] = {
                "before": b, "after": a, "delta": a - b,
                "noise_threshold": NOISE_THRESHOLDS.get(key),
            }
        # Aggregates
        b = _extract_pos_bigram_kl_total(before_variance)
        a = _extract_pos_bigram_kl_total(after_variance)
        if b is not None and a is not None:
            out["variance"]["pos_bigram_kl_total"] = {
                "before": b, "after": a, "delta": a - b,
                "noise_threshold": NOISE_THRESHOLDS["pos_bigram_kl_total"],
            }
        # Band shift
        b_band = _extract_band(before_variance)
        a_band = _extract_band(after_variance)
        if b_band or a_band:
            out["variance"]["band"] = {"before": b_band, "after": a_band}
        # Compression fraction
        b_cf = _extract_compression_fraction(before_variance)
        a_cf = _extract_compression_fraction(after_variance)
        if b_cf is not None and a_cf is not None:
            out["variance"]["compression_fraction"] = {
                "before": b_cf, "after": a_cf, "delta": a_cf - b_cf,
            }
    if before_bigram and after_bigram:
        b_total = _bigram_kl_total(before_bigram)
        a_total = _bigram_kl_total(after_bigram)
        if b_total is not None and a_total is not None:
            out["bigram"]["kl_total"] = {
                "before": b_total, "after": a_total, "delta": a_total - b_total,
            }
        before_kls = _bigram_kl_contribs(before_bigram)
        after_kls = _bigram_kl_contribs(after_bigram)
        per_bigram: dict[str, dict[str, float]] = {}
        for bg in set(before_kls) | set(after_kls):
            b = before_kls.get(bg, 0.0)
            a = after_kls.get(bg, 0.0)
            per_bigram[bg] = {"before": b, "after": a, "delta": a - b}
        out["bigram"]["per_bigram"] = per_bigram
    if before_voice and after_voice:
        b_v = _extract_voice_distance(before_voice)
        a_v = _extract_voice_distance(after_voice)
        if b_v is not None and a_v is not None:
            out["voice"]["overall_distance"] = {
                "before": b_v, "after": a_v, "delta": a_v - b_v,
                "noise_threshold": NOISE_THRESHOLDS["voice_distance_overall"],
            }
    return out


# --------------- Output rendering ---------------------------


CLAIM_LICENSE = {
    "licenses": (
        "Per-target verdict reporting whether each restoration packet's "
        "targeted signal moved in the intended direction by more than "
        "the per-signal noise threshold, plus side-effect deltas on "
        "non-target signals and preservation-list survival when the "
        "original and revised text are supplied."
    ),
    "does_not_license": (
        "AI provenance, authorship attribution, or proof the revision "
        "is 'better.' A verdict of 'improved' means the metric moved; "
        "the writer's local read decides whether the prose itself "
        "improved. The 'gamed' verdict is a soft heuristic, not a "
        "proof of metric gaming -- the writer still has to inspect "
        "the actual prose to judge."
    ),
}


def render_json(
    *,
    verdicts: Sequence[TargetVerdict],
    aggregate_deltas: dict[str, float | None],
    preservation_check: dict[str, Any] | None,
    diff_only: dict[str, Any] | None,
    inputs: dict[str, str | None],
    packet_summary: dict[str, Any] | None,
) -> str:
    summary = _summarize_verdicts(verdicts)
    out = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "inputs": inputs,
        "claim_license": CLAIM_LICENSE,
        "packet_summary": packet_summary,
        "verdict_summary": summary,
        "verdicts": [v.to_dict() for v in verdicts],
        "aggregate_deltas": aggregate_deltas,
        "preservation_check": preservation_check,
        "diff_only": diff_only,
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def _summarize_verdicts(verdicts: Sequence[TargetVerdict]) -> dict[str, int]:
    summary = {
        "improved": 0, "no_change": 0, "degraded": 0, "gamed": 0,
        "not_measurable": 0,
    }
    for v in verdicts:
        if v.verdict in summary:
            summary[v.verdict] += 1
    return summary


def render_markdown(
    *,
    verdicts: Sequence[TargetVerdict],
    aggregate_deltas: dict[str, float | None],
    preservation_check: dict[str, Any] | None,
    diff_only: dict[str, Any] | None,
    packet_summary: dict[str, Any] | None,
) -> str:
    lines: list[str] = [
        "# Before/After Restoration Report",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        "",
        f"**Reports:** {CLAIM_LICENSE['licenses']}",
        "",
        f"**Does NOT report:** {CLAIM_LICENSE['does_not_license']}",
        "",
    ]

    summary = _summarize_verdicts(verdicts)
    lines.append("## Verdict summary")
    lines.append("")
    lines.append(
        f"- improved: {summary['improved']}  "
        f"no_change: {summary['no_change']}  "
        f"degraded: {summary['degraded']}  "
        f"gamed: {summary['gamed']}  "
        f"not_measurable: {summary['not_measurable']}"
    )
    lines.append("")

    if verdicts:
        lines.append("## Per-target verdicts")
        lines.append("")
        lines.append("| Target | Class | Direction | Before | After | Δ | Verdict |")
        lines.append("|---|---|---|---:|---:|---:|---|")
        for v in verdicts:
            lines.append(
                f"| `{v.target_id}` | {v.targetability} | {v.direction} | "
                f"{_fmt(v.before)} | {_fmt(v.after)} | "
                f"{_fmt(v.delta)} | **{v.verdict}** |"
            )
        lines.append("")
        # Surface notes for any verdict with non-empty notes (especially
        # "gamed" verdicts -- the explanation matters).
        for v in verdicts:
            if v.notes:
                lines.append(f"### {v.target_id} notes")
                for n in v.notes:
                    lines.append(f"- {n}")
                lines.append("")

    if aggregate_deltas:
        lines.append("## Aggregate deltas (avoid_direct evidence)")
        lines.append("")
        for key, delta in aggregate_deltas.items():
            noise = NOISE_THRESHOLDS.get(key)
            lines.append(
                f"- `{key}`: Δ = {_fmt(delta)} "
                f"(noise threshold ±{_fmt(noise)})"
            )
        lines.append("")

    if preservation_check and preservation_check.get("checked"):
        lines.append("## Idiolect preservation list")
        lines.append("")
        rate = preservation_check["survival_rate"]
        lines.append(
            f"- Survival rate: {rate:.1%} "
            f"({preservation_check['n_survived']} / "
            f"{preservation_check['n_total']})"
        )
        if preservation_check["n_missing"] > 0:
            lines.append("- Missing phrases (revise to restore):")
            for phrase in preservation_check["missing_phrases"]:
                lines.append(f"  - {phrase!r}")
        lines.append("")

    if diff_only:
        lines.append("## All-signal diff (informational)")
        lines.append("")
        var = diff_only.get("variance") or {}
        if var:
            lines.append("### Variance signals")
            lines.append("")
            lines.append("| Signal | Before | After | Δ | Noise |")
            lines.append("|---|---:|---:|---:|---:|")
            for k, d in sorted(var.items()):
                if not isinstance(d, dict) or "delta" not in d:
                    continue
                lines.append(
                    f"| `{k}` | {_fmt(d.get('before'))} | "
                    f"{_fmt(d.get('after'))} | {_fmt(d.get('delta'))} | "
                    f"±{_fmt(d.get('noise_threshold'))} |"
                )
            band = var.get("band")
            if isinstance(band, dict):
                lines.append("")
                lines.append(
                    f"**Band:** `{band.get('before')}` → "
                    f"`{band.get('after')}`"
                )
            lines.append("")

    return "\n".join(lines) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "—"
        return f"{v:.4f}"
    return str(v)


# --------------- CLI ---------------------------------------


def _load_json_arg(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        sys.stderr.write(f"Input not found: {p}\n")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


def _load_text_arg(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        sys.stderr.write(f"Text file not found: {p}\n")
        sys.exit(1)
    return p.read_text(encoding="utf-8", errors="ignore")


def run(args: argparse.Namespace) -> dict[str, Any]:
    """In-process entry point. Returns the structured comparison
    dict; the CLI wraps this for JSON / markdown output."""
    packet = _load_json_arg(args.packet_json)
    before_variance = _load_json_arg(args.before_variance_json)
    after_variance = _load_json_arg(args.after_variance_json)
    before_bigram = _load_json_arg(args.before_bigram_json)
    after_bigram = _load_json_arg(args.after_bigram_json)
    before_voice = _load_json_arg(args.before_voice_json)
    after_voice = _load_json_arg(args.after_voice_json)
    before_idiolect = _load_json_arg(args.before_idiolect_json)
    after_idiolect = _load_json_arg(args.after_idiolect_json)
    revised_text = _load_text_arg(args.revised_text)

    verdicts: list[TargetVerdict] = []
    aggregate_deltas: dict[str, float | None] = {}
    packet_summary: dict[str, Any] | None = None

    if packet:
        packet_summary = {
            "n_packets": packet.get("n_packets"),
            "task_surface": packet.get("task_surface"),
            "target_scope": packet.get("target_scope"),
            "genre": packet.get("genre"),
        }
        for p in packet.get("packets", []):
            v = evaluate_packet(
                p,
                before_variance=before_variance,
                after_variance=after_variance,
                before_bigram=before_bigram,
                after_bigram=after_bigram,
                before_voice=before_voice,
                after_voice=after_voice,
                before_idiolect=before_idiolect,
                after_idiolect=after_idiolect,
            )
            verdicts.append(v)

    # Compute aggregate deltas for the gaming detector (and for
    # display in the report regardless of packet presence).
    if before_variance and after_variance:
        b = _extract_pos_bigram_kl_total(before_variance)
        a = _extract_pos_bigram_kl_total(after_variance)
        aggregate_deltas["pos_bigram_kl_total"] = (
            (a - b) if (b is not None and a is not None) else None
        )
    if before_voice and after_voice:
        b = _extract_voice_distance(before_voice)
        a = _extract_voice_distance(after_voice)
        aggregate_deltas["voice_distance_overall"] = (
            (a - b) if (b is not None and a is not None) else None
        )
    if before_bigram and after_bigram:
        b = _bigram_kl_total(before_bigram)
        a = _bigram_kl_total(after_bigram)
        aggregate_deltas["bigram_kl_total"] = (
            (a - b) if (b is not None and a is not None) else None
        )

    if verdicts:
        verdicts = detect_gaming(verdicts, aggregate_deltas)

    # Preservation-list survival check
    preservation_check: dict[str, Any] | None = None
    preservation_list: list[str] = []
    # Pull the list from the packet (idiolect_preservation entry's
    # phrases_preview is the nearest source, but the full list lives
    # in the original idiolect JSON). Prefer the original idiolect
    # JSON when supplied; fall back to the packet's preview.
    if before_idiolect:
        pl = before_idiolect.get("preservation_list") or before_idiolect.get("preserve") or []
        if isinstance(pl, list):
            preservation_list = [s for s in pl if isinstance(s, str)]
    elif packet:
        for p in packet.get("packets", []):
            if p.get("id") == "idiolect_preservation":
                ev = p.get("evidence") or {}
                preview = ev.get("phrases_preview") or []
                if isinstance(preview, list):
                    preservation_list = [s for s in preview if isinstance(s, str)]
                break
    if revised_text and preservation_list:
        preservation_check = check_preservation(revised_text, preservation_list)

    # Diff-only mode (always computed; surfaced in the report when the
    # packet is absent OR --include-diff is set).
    diff_only_block = None
    if args.diff_only or not packet:
        diff_only_block = diff_all_signals(
            before_variance=before_variance, after_variance=after_variance,
            before_bigram=before_bigram, after_bigram=after_bigram,
            before_voice=before_voice, after_voice=after_voice,
        )

    inputs = {
        "packet_json": args.packet_json,
        "before_variance_json": args.before_variance_json,
        "after_variance_json": args.after_variance_json,
        "before_bigram_json": args.before_bigram_json,
        "after_bigram_json": args.after_bigram_json,
        "before_voice_json": args.before_voice_json,
        "after_voice_json": args.after_voice_json,
        "before_idiolect_json": args.before_idiolect_json,
        "after_idiolect_json": args.after_idiolect_json,
        "original_text": args.original_text,
        "revised_text": args.revised_text,
    }

    return {
        "verdicts": verdicts,
        "aggregate_deltas": aggregate_deltas,
        "preservation_check": preservation_check,
        "diff_only": diff_only_block,
        "inputs": inputs,
        "packet_summary": packet_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare before/after SETEC diagnostics against an "
            "original restoration packet; report per-target verdicts."
        )
    )
    parser.add_argument("--packet-json", help="Original restoration_packet.py JSON output.")
    parser.add_argument("--before-variance-json")
    parser.add_argument("--after-variance-json")
    parser.add_argument("--before-bigram-json")
    parser.add_argument("--after-bigram-json")
    parser.add_argument("--before-voice-json")
    parser.add_argument("--after-voice-json")
    parser.add_argument("--before-idiolect-json")
    parser.add_argument("--after-idiolect-json")
    parser.add_argument(
        "--original-text",
        help="Original target text (for preservation-list survival check).",
    )
    parser.add_argument(
        "--revised-text",
        help="Revised text (for preservation-list survival check).",
    )
    parser.add_argument(
        "--diff-only", action="store_true",
        help="Force diff-only mode even when --packet-json is supplied.",
    )
    parser.add_argument("--out", help="Markdown output path.")
    parser.add_argument("--json-out", help="JSON output path.")
    args = parser.parse_args(argv)

    inputs_present = any([
        args.packet_json,
        args.before_variance_json or args.after_variance_json,
        args.before_bigram_json or args.after_bigram_json,
        args.before_voice_json or args.after_voice_json,
    ])
    if not inputs_present:
        sys.stderr.write(
            "At least one of --packet-json or a before/after JSON pair "
            "is required.\n"
        )
        return 1

    result = run(args)
    json_out = render_json(
        verdicts=result["verdicts"],
        aggregate_deltas=result["aggregate_deltas"],
        preservation_check=result["preservation_check"],
        diff_only=result["diff_only"],
        inputs=result["inputs"],
        packet_summary=result["packet_summary"],
    )
    md_out = render_markdown(
        verdicts=result["verdicts"],
        aggregate_deltas=result["aggregate_deltas"],
        preservation_check=result["preservation_check"],
        diff_only=result["diff_only"],
        packet_summary=result["packet_summary"],
    )

    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json_out, encoding="utf-8")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md_out, encoding="utf-8")
    if not args.json_out and not args.out:
        sys.stdout.write(md_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
