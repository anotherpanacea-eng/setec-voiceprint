#!/usr/bin/env python3
"""draft_history_analysis.py — version-aware stylometric trajectory
across multiple drafts (paired-release schedule Release 11,
Trustworthiness Tier 3).

Single-snapshot stylometric audits answer the question *what does
this draft look like?* The framework's `before_after_restoration`
audit answers *what changed in this revision pass?* Draft-history
analysis asks the version-aware question: *given a sequence of
drafts (v1, v2, … vN), where in the revision arc did the
smoothing enter, when did idiolect disappear, was the change
gradual or sudden, did later edits restore or further flatten
the voice?*

The output shape mirrors the canonical use case the ROADMAP names:

  > "Major distributional compression appears between v3 and v4,
  > concentrated in sections 1, 4, and 6. Later edits restore
  > lexical idiolect but not sentence-architecture variance."

Inputs:

  --versions-json — JSON list of `{label, path}` entries, ordered
                    chronologically. Each entry's `path` points at
                    a draft version's text. The label is the
                    version's display name (`v1`, `pre_edit`,
                    `post_developmental`, etc.).

Output (per-version per-signal trajectory):

  - **Per-version metrics**: variance-audit signals run on each
    draft. The same eight tier-1 signals `known_editor_profile`
    tracks (burstiness, sentence_length_sd, mtld, mattr, shannon
    entropy, yules_k, fkgl_sd, connective density).
  - **Pair deltas**: per-signal `(v[i+1] − v[i])` for adjacent
    versions. Surfaces where each individual revision pass moved
    each signal.
  - **Inflection points**: per-signal, the version-pair index
    where the largest absolute delta occurred. Names where the
    signal moved most sharply.
  - **Per-signal narrative**: a heuristic verdict per signal
    (``stable_throughout`` / ``gradual_drift`` / ``sudden_shift``
    / ``restored_after_drift``), based on the trajectory shape.

Pairs naturally with `known_editor_profile` (which compares a
single new pair against a learned editor profile). Draft-history
analysis is the multi-version generalization that doesn't try
to learn a profile — just traces trajectories and names
inflection points.

Usage:

    python3 scripts/draft_history_analysis.py \\
        --versions-json drafts.json \\
        --json --out trajectory.json

    # drafts.json:
    # [
    #   {"label": "v1",  "path": "drafts/v1.txt"},
    #   {"label": "v2",  "path": "drafts/v2.txt"},
    #   {"label": "v3",  "path": "drafts/v3.txt"},
    #   {"label": "v4",  "path": "drafts/v4_post_edit.txt"}
    # ]

task_surface: validation. The output is a trajectory report,
not a verdict. The framework refuses to commit to provenance
claims at any version; the report names where signals moved,
not what caused the movement.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

try:
    from variance_audit import audit_text  # type: ignore
    HAS_VARIANCE_AUDIT = True
except ImportError:
    HAS_VARIANCE_AUDIT = False

    def audit_text(*args, **kwargs):  # type: ignore
        raise RuntimeError(
            "variance_audit unavailable; cannot compute audits."
        )


TASK_SURFACE = "validation"
TOOL_NAME = "draft_history_analysis"
SCRIPT_VERSION = "1.0"


# Tier-1 signals tracked across versions. Keep aligned with
# `known_editor_profile._PROFILE_SIGNALS` so the two surfaces
# can compose: a draft history can supply pair-deltas to the
# editor-profile match step, and vice versa.
_TRAJECTORY_SIGNALS: dict[str, tuple[str, ...]] = {
    "burstiness_B": (
        "tier1", "sentence_length", "burstiness_B",
    ),
    "sentence_length_sd": (
        "tier1", "sentence_length", "sd",
    ),
    "mtld": ("tier1", "mtld"),
    "mattr": ("tier1", "mattr", "value"),
    "shannon_entropy": ("tier1", "shannon_entropy_bits"),
    "yules_k": ("tier1", "yules_k"),
    "fkgl_sd": ("tier1", "fkgl", "sd"),
    "connective_density": (
        "tier1", "connective_density", "per_1000_tokens",
    ),
}


# Per-signal noise floors. A delta within the floor counts as
# `stable`; outside is `notable`. Keep aligned with
# `before_after_restoration.NOISE_THRESHOLDS` where overlapping.
_NOISE_FLOORS: dict[str, float] = {
    "burstiness_B": 0.05,
    "sentence_length_sd": 0.50,
    "mtld": 5.0,
    "mattr": 0.02,
    "shannon_entropy": 0.10,
    "yules_k": 10.0,
    "fkgl_sd": 0.20,
    "connective_density": 1.0,
}


# ---------- Per-version measurement ----------


def _extract_signal(
    audit: dict[str, Any], path: tuple[str, ...],
) -> float | None:
    cur: Any = audit
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    if isinstance(cur, (int, float)) and not isinstance(cur, bool):
        return float(cur)
    return None


def _extract_all_signals(
    audit: dict[str, Any],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, path in _TRAJECTORY_SIGNALS.items():
        val = _extract_signal(audit, path)
        if val is not None:
            out[name] = val
    return out


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Version file not found: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def measure_version(
    text: str, *, do_tier2: bool = False,
) -> dict[str, Any]:
    """Run variance_audit on a single version's text and extract
    the tier-1 trajectory signals."""
    if not HAS_VARIANCE_AUDIT:
        raise RuntimeError(
            "variance_audit unavailable; cannot measure versions."
        )
    audit = audit_text(text, do_tier2=do_tier2, do_tier3=False)
    summary = audit.get("summary", {})
    return {
        "n_words": summary.get("n_words"),
        "n_sentences": summary.get("n_sentences"),
        "signals": _extract_all_signals(audit),
    }


# ---------- Trajectory + inflection points ----------


@dataclass
class SignalTrajectory:
    name: str
    values: list[float | None]  # one per version
    deltas: list[float | None]  # one per pair (v[i+1] − v[i])
    inflection_pair_index: int | None
    inflection_delta: float | None
    verdict: str
    notes: list[str]


def _classify_trajectory_verdict(
    *,
    deltas: list[float | None],
    noise_floor: float,
) -> tuple[str, list[str]]:
    """Map a sequence of deltas to a verdict.

    Verdicts:
      - ``stable_throughout`` — every delta within ±noise_floor.
      - ``gradual_drift`` — deltas of consistent sign with
        cumulative absolute movement above the floor, AND no
        single delta dominating (>= 2× the average notable delta).
      - ``sudden_shift`` — one delta dominates (>= 2× the average
        absolute delta), and that delta is outside the floor.
      - ``restored_after_drift`` — net cumulative change within
        the floor BUT individual deltas exceed the floor with
        sign reversal (drift forward, restoration back).
      - ``unknown`` — fewer than 2 deltas usable.
    """
    notable = [d for d in deltas if d is not None]
    if len(notable) == 0:
        return "unknown", []
    if len(notable) == 1:
        # Single pair: stable / notable based on the floor.
        d = notable[0]
        if abs(d) <= noise_floor:
            return "stable_throughout", []
        return "sudden_shift", [
            f"single-pair trajectory with |Δ|={abs(d):.4f} "
            f"above floor {noise_floor:.4f}.",
        ]

    abs_deltas = [abs(d) for d in notable]
    n_above_floor = sum(
        1 for ad in abs_deltas if ad > noise_floor
    )
    if n_above_floor == 0:
        return "stable_throughout", []

    cumulative = sum(notable)
    cumulative_above_floor = abs(cumulative) > noise_floor

    # Sign pattern.
    signs = [1 if d > 0 else (-1 if d < 0 else 0) for d in notable]
    sign_reversals = sum(
        1 for a, b in zip(signs, signs[1:])
        if a != 0 and b != 0 and a != b
    )

    # Identify dominant delta.
    max_abs = max(abs_deltas)
    mean_abs = statistics.mean(abs_deltas)
    dominant = (
        max_abs >= 2.0 * mean_abs
        and max_abs > noise_floor
    )

    notes: list[str] = []
    if (
        not cumulative_above_floor
        and sign_reversals >= 1
        and n_above_floor >= 2
    ):
        notes.append(
            f"cumulative change |{cumulative:.4f}| within floor "
            f"{noise_floor:.4f} but {sign_reversals} sign reversal(s) "
            f"with {n_above_floor} delta(s) above floor."
        )
        return "restored_after_drift", notes
    if dominant:
        notes.append(
            f"max |Δ|={max_abs:.4f} is {max_abs / mean_abs:.2f}× the "
            f"mean |Δ|={mean_abs:.4f}; dominant pair shifts the trajectory."
        )
        return "sudden_shift", notes
    return "gradual_drift", []


def build_trajectory(
    *,
    versions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build per-signal trajectories from a list of version
    measurements. Each version is shaped like
    ``{"label": "v1", "n_words": ..., "signals": {...}}``."""
    n_versions = len(versions)
    if n_versions < 2:
        raise ValueError(
            "Draft history requires at least two versions to "
            "compute deltas; got "
            f"{n_versions}."
        )

    pair_labels = [
        f"{versions[i]['label']}→{versions[i + 1]['label']}"
        for i in range(n_versions - 1)
    ]

    trajectories: dict[str, dict[str, Any]] = {}
    for sig_name in _TRAJECTORY_SIGNALS:
        values: list[float | None] = []
        for v in versions:
            sigs = v.get("signals", {})
            values.append(sigs.get(sig_name))
        deltas: list[float | None] = []
        for i in range(n_versions - 1):
            a = values[i]
            b = values[i + 1]
            if a is None or b is None:
                deltas.append(None)
            else:
                deltas.append(b - a)

        # Inflection pair: index of the largest absolute delta.
        inflection_idx: int | None = None
        inflection_val: float | None = None
        for i, d in enumerate(deltas):
            if d is None:
                continue
            if (
                inflection_val is None
                or abs(d) > abs(inflection_val)
            ):
                inflection_idx = i
                inflection_val = d

        verdict, notes = _classify_trajectory_verdict(
            deltas=deltas,
            noise_floor=_NOISE_FLOORS.get(sig_name, 0.0),
        )

        trajectories[sig_name] = {
            "values": values,
            "deltas": deltas,
            "inflection_pair_index": inflection_idx,
            "inflection_pair_label": (
                pair_labels[inflection_idx]
                if inflection_idx is not None
                and inflection_idx < len(pair_labels)
                else None
            ),
            "inflection_delta": inflection_val,
            "verdict": verdict,
            "notes": notes,
            "noise_floor": _NOISE_FLOORS.get(sig_name, 0.0),
        }

    summary = _summarize_trajectory(
        trajectories=trajectories,
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "n_versions": n_versions,
        "version_labels": [v["label"] for v in versions],
        "pair_labels": pair_labels,
        "per_version": [
            {
                "label": v["label"],
                "n_words": v.get("n_words"),
                "n_sentences": v.get("n_sentences"),
                "signals": v.get("signals", {}),
            }
            for v in versions
        ],
        "trajectories": trajectories,
        "summary": summary,
        "claim_license": _claim_license_dict(
            n_versions=n_versions,
            summary=summary,
        ),
    }


def _summarize_trajectory(
    *,
    trajectories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    n_signals = len(trajectories)
    n_stable = sum(
        1 for t in trajectories.values()
        if t["verdict"] == "stable_throughout"
    )
    n_gradual = sum(
        1 for t in trajectories.values()
        if t["verdict"] == "gradual_drift"
    )
    n_sudden = sum(
        1 for t in trajectories.values()
        if t["verdict"] == "sudden_shift"
    )
    n_restored = sum(
        1 for t in trajectories.values()
        if t["verdict"] == "restored_after_drift"
    )
    n_unknown = sum(
        1 for t in trajectories.values()
        if t["verdict"] == "unknown"
    )

    # The dominant inflection pair across signals — which version-
    # pair shows up most often as the inflection point.
    inflection_pair_counts: dict[str, int] = {}
    for t in trajectories.values():
        label = t.get("inflection_pair_label")
        if label is None:
            continue
        if t["verdict"] in {"sudden_shift", "gradual_drift", "restored_after_drift"}:
            inflection_pair_counts[label] = (
                inflection_pair_counts.get(label, 0) + 1
            )
    if inflection_pair_counts:
        dominant_pair = max(
            inflection_pair_counts.items(),
            key=lambda kv: kv[1],
        )
    else:
        dominant_pair = (None, 0)

    return {
        "n_signals": n_signals,
        "n_stable_throughout": n_stable,
        "n_gradual_drift": n_gradual,
        "n_sudden_shift": n_sudden,
        "n_restored_after_drift": n_restored,
        "n_unknown": n_unknown,
        "dominant_inflection_pair": dominant_pair[0],
        "dominant_inflection_pair_signal_count": dominant_pair[1],
        "inflection_pair_counts": inflection_pair_counts,
    }


def _claim_license_dict(
    *,
    n_versions: int,
    summary: dict[str, Any],
) -> dict[str, Any]:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A version-aware stylometric trajectory across N "
            "drafts. For each tier-1 signal, the report shows "
            "the per-version values, the per-pair deltas, the "
            "version pair where the largest absolute delta "
            "occurred (the inflection point), and a per-signal "
            "verdict (stable_throughout / gradual_drift / "
            "sudden_shift / restored_after_drift / unknown)."
        ),
        does_not_license=(
            "An authorship verdict at any version. The "
            "trajectory says WHEN signals moved, not WHAT "
            "caused the movement. A `sudden_shift` between v3 "
            "and v4 might reflect a global rewrite, an editor "
            "pass, an AI-smoothing pass, an authorial pivot, "
            "or a structural reorganization — the report names "
            "the inflection point and refuses to choose. Pair "
            "with the confounder audit, the known-editor "
            "profile, and the evidentiary-conditions gate "
            "before drawing conclusions."
        ),
        comparison_set={
            "n_versions": n_versions,
            "n_signals_tracked": summary.get("n_signals", 0),
            "dominant_inflection_pair": summary.get(
                "dominant_inflection_pair"
            ),
            "n_signals_at_dominant_pair": summary.get(
                "dominant_inflection_pair_signal_count", 0
            ),
        },
        additional_caveats=[
            "The trajectory operates on tier-1 variance signals "
            "only. POS-bigram KL, voice distance, and the "
            "construction-signature surfaces are not currently "
            "trajectory-tracked; that's roadmap.",
            "The verdict thresholds (per-signal noise floors) "
            "are heuristic; they match `before_after_restoration`'s "
            "noise band where signals overlap. Calibration-"
            "pending against labeled draft-history corpora.",
            "A `sudden_shift` verdict requires the dominant "
            "delta to be ≥ 2× the mean absolute delta. With "
            "two versions (one pair), every notable change "
            "reads as `sudden_shift` by definition; the "
            "verdict's interpretive value scales with the "
            "number of versions.",
            "Inflection pairs are reported per-signal. The "
            "summary's `dominant_inflection_pair` aggregates "
            "across signals — which pair shows up most often "
            "as the inflection point — and is informative when "
            "multiple signals concur.",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


_VERDICT_GLYPH = {
    "stable_throughout": "✓",
    "gradual_drift": "·",
    "sudden_shift": "✗",
    "restored_after_drift": "↺",
    "unknown": "—",
}


def render_report(report: dict[str, Any]) -> str:
    trajectories = report.get("trajectories", {})
    per_version = report.get("per_version", [])
    summary = report.get("summary", {})

    lines: list[str] = [
        "# Draft history analysis",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Versions:** {report.get('n_versions', 0)} "
        f"({', '.join(report.get('version_labels', []))})",
        f"**Dominant inflection pair:** "
        f"{summary.get('dominant_inflection_pair') or '(none)'} "
        f"({summary.get('dominant_inflection_pair_signal_count', 0)} "
        f"signals concur)",
        f"**Per-signal verdict counts:** "
        f"stable_throughout={summary.get('n_stable_throughout', 0)}, "
        f"gradual_drift={summary.get('n_gradual_drift', 0)}, "
        f"sudden_shift={summary.get('n_sudden_shift', 0)}, "
        f"restored_after_drift={summary.get('n_restored_after_drift', 0)}",
        "",
        "## Per-version word counts",
        "",
        "| version | words | sentences |",
        "|---|---|---|",
    ]
    for v in per_version:
        lines.append(
            f"| {v.get('label')} | {v.get('n_words')} | "
            f"{v.get('n_sentences')} |"
        )
    lines.append("")

    lines.append("## Per-signal trajectory")
    lines.append("")
    lines.append(
        "Glyph legend: ✓ stable_throughout, · gradual_drift, "
        "✗ sudden_shift, ↺ restored_after_drift, — unknown."
    )
    lines.append("")
    headers = ["signal"]
    for v in per_version:
        headers.append(v.get("label", ""))
    headers += ["inflection", "verdict"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append(
        "|" + "|".join(["---"] * len(headers)) + "|"
    )
    for sig_name, info in trajectories.items():
        verdict = info.get("verdict", "unknown")
        glyph = _VERDICT_GLYPH.get(verdict, "?")
        row = [sig_name]
        for v in info.get("values", []):
            row.append(
                f"{v:.4f}" if isinstance(v, (int, float)) else "—"
            )
        infl = info.get("inflection_pair_label") or "—"
        row.append(infl)
        row.append(f"`{verdict}` {glyph}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Per-signal narrative for non-stable signals.
    interesting = [
        (k, v) for k, v in trajectories.items()
        if v.get("verdict") not in {"stable_throughout", "unknown"}
    ]
    if interesting:
        lines.append("## Notable signals")
        lines.append("")
        for sig_name, info in interesting:
            lines.append(
                f"### `{sig_name}` — {info.get('verdict')}"
            )
            lines.append("")
            deltas = info.get("deltas", [])
            pair_labels = report.get("pair_labels", [])
            for i, d in enumerate(deltas):
                if d is None:
                    continue
                pair = (
                    pair_labels[i] if i < len(pair_labels)
                    else f"pair_{i}"
                )
                lines.append(f"- `{pair}`: Δ = {d:+.4f}")
            for note in info.get("notes", []):
                lines.append(f"_Note: {note}_")
            lines.append("")

    license_block = report.get("claim_license", {}).get(
        "rendered", "",
    )
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_versions_manifest(
    path_str: str,
) -> list[dict[str, str]]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--versions-json not found: {path_str}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--versions-json malformed: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            "--versions-json must be a JSON list of "
            "{label, path} objects."
        )
    out: list[dict[str, str]] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"--versions-json entry {i} is not an object."
            )
        if "label" not in entry or "path" not in entry:
            raise ValueError(
                f"--versions-json entry {i} missing required "
                "keys 'label' and 'path'."
            )
        out.append({
            "label": str(entry["label"]),
            "path": str(entry["path"]),
        })
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="draft_history_analysis.py",
        description=(
            "Version-aware stylometric trajectory across N "
            "drafts. Reports per-signal trajectory, inflection "
            "points, and per-signal verdicts (stable / gradual / "
            "sudden / restored)."
        ),
    )
    p.add_argument(
        "--versions-json", required=True,
        help="JSON list of {label, path} entries, ordered "
             "chronologically.",
    )
    p.add_argument(
        "--tier2", action="store_true",
        help="Include tier-2 (spaCy) signals; default is tier-1 only.",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not HAS_VARIANCE_AUDIT:
        sys.stderr.write(
            "variance_audit unavailable; cannot run "
            "draft_history_analysis.\n"
        )
        return 2

    try:
        manifest = _read_versions_manifest(args.versions_json)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"--versions-json: {exc}\n")
        return 2

    if len(manifest) < 2:
        sys.stderr.write(
            "--versions-json: requires at least two entries to "
            "compute deltas; got "
            f"{len(manifest)}.\n"
        )
        return 2

    versions: list[dict[str, Any]] = []
    for entry in manifest:
        path = Path(entry["path"]).expanduser()
        try:
            text = _read_text(path)
        except FileNotFoundError as exc:
            sys.stderr.write(f"version {entry['label']}: {exc}\n")
            return 2
        if not text.strip():
            sys.stderr.write(
                f"version {entry['label']}: file is empty: "
                f"{entry['path']}\n"
            )
            return 2
        try:
            measurement = measure_version(
                text, do_tier2=args.tier2,
            )
        except RuntimeError as exc:
            sys.stderr.write(
                f"version {entry['label']}: {exc}\n"
            )
            return 2
        versions.append({
            "label": entry["label"],
            **measurement,
        })

    try:
        report = build_trajectory(versions=versions)
    except ValueError as exc:
        sys.stderr.write(f"build_trajectory: {exc}\n")
        return 2

    if args.json:
        payload = build_audit_payload(report, target_path=None)
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


def build_audit_payload(
    report: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap draft_history_analysis report in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.

    The legacy claim_license stays as a rendered-only dict inside
    the report dict (consumed by render_report). The envelope's
    top-level claim_license is built from the same content as a
    structured ClaimLicense.
    """
    # Reconstruct structured claim_license. The legacy dict in
    # report["claim_license"] is `{"rendered": "..."}` markdown;
    # we want the structured 11-key form at envelope.claim_license.
    n_versions = int(report.get("n_versions", 0) or 0)
    summary = report.get("summary") or {}
    structured = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Multi-version draft trajectory analysis: per-version "
            "stylometric signals plus across-version trajectory "
            "summary (drift direction, monotonicity, signal "
            "stability)."
        ),
        does_not_license=(
            "An authorship verdict on any version. Drift between "
            "versions can come from voice maturation, register "
            "shift, AI assistance, professional editing, or "
            "intentional rewriting. The audit reports the "
            "trajectory; the writer adjudicates."
        ),
        comparison_set={
            "n_versions": n_versions,
            "pair_labels": report.get("pair_labels"),
            "summary_keys": list(summary.keys()) if isinstance(summary, dict) else [],
        },
        additional_caveats=[
            "Per-version signal extraction uses variance_audit's "
            "audit_text() shape. Versions below the 200-word floor "
            "produce noisier readings; the per-version block "
            "preserves the original word counts so consumers can "
            "filter.",
            "Trajectory summary is conservative — it reports drift "
            "direction and monotonicity, not magnitude calibrated "
            "against a labeled corpus.",
        ],
    )

    metadata_keys = {"task_surface", "tool", "version"}
    results_payload = {
        k: v for k, v in report.items() if k not in metadata_keys
    }
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results_payload,
        claim_license=structured,
    )


if __name__ == "__main__":
    sys.exit(main())
