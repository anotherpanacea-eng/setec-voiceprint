#!/usr/bin/env python3
"""known_editor_profile.py — learn an editorial transformation
profile from before/after pairs (paired-release schedule
Release 10, Trustworthiness Tier 3).

The framework's reports can say "this draft has been smoothed"
but cannot say "this draft has been smoothed in the ordinary
way THIS editor smooths THIS writer." For literary and
institutional writing, that distinction is large — a developmental
editor's normal pass produces a recognizable transformation
profile, and treating that profile as evidence of provenance
is exactly the failure mode this module exists to prevent.

The profile is a per-signal delta-distribution learned from
labeled before/after pairs: each pair contributes a per-signal
delta (after − before), and the profile aggregates means and
standard deviations across pairs. A new (before, after) pair
can then be compared against the profile: per-signal z-scores
report whether each signal's movement is within the editor's
typical range.

Two modes:

  ``learn`` — read a pairs manifest, run variance_audit on each
  before/after text, and emit a profile JSON.

  ``match`` — read a learned profile and a new (before, after)
  pair, compute per-signal z-scores, and emit a match report.

The match-mode verdicts:
  - ``matches_profile`` — all signals within ±2 sd of the
    editor's typical delta. The new pair looks like ordinary
    editing by this editor.
  - ``mismatch`` — at least one signal is more than 2 sd away.
    Either the new pair is NOT this editor's normal work, OR
    the profile is too narrow to cover the new case (a
    function of how many pairs trained the profile).
  - ``ambiguous`` — profile sd is too small to test (one-pair
    profiles can't reject anything).

Pairs manifest format (JSON):

    [
      {"before": "drafts/v1.txt", "after": "edited/v1.txt"},
      {"before": "drafts/v2.txt", "after": "edited/v2.txt"}
    ]

A signal is included in the profile only if at least 2 pairs
contributed a measurable value (otherwise sd is undefined).

Usage:

    # Learn an editor's profile from labeled before/after pairs.
    python3 scripts/known_editor_profile.py learn \\
        --pairs-json pairs.json \\
        --out profile.json

    # Match a new pair against the saved profile.
    python3 scripts/known_editor_profile.py match \\
        --before new_v1_before.txt --after new_v1_after.txt \\
        --profile profile.json \\
        --json --out match-report.json

task_surface: validation. The profile and match report do NOT
constitute authorship verdicts. They distinguish "this looks
like normal editing by X" from "this looks like a different
process," and the framework refuses any further commitment.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore

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
TOOL_NAME = "known_editor_profile"
SCRIPT_VERSION = "1.0"


# Signals tracked in the profile. Path-tuples into the
# variance_audit JSON output. A signal is included if its
# extraction succeeds on both members of every pair.
_PROFILE_SIGNALS: dict[str, tuple[str, ...]] = {
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


def _extract_signal(
    audit: dict[str, Any], path: tuple[str, ...],
) -> float | None:
    """Walk a path tuple into the variance_audit output."""
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


def _extract_all_signals(audit: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, path in _PROFILE_SIGNALS.items():
        val = _extract_signal(audit, path)
        if val is not None:
            out[name] = val
    return out


# ---------- Pair processing ----------


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Text file not found: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def measure_pair(
    before_path: Path, after_path: Path,
    *, do_tier2: bool = False,
) -> dict[str, Any]:
    """Run variance_audit on both texts and return the per-signal
    deltas (after − before)."""
    if not HAS_VARIANCE_AUDIT:
        raise RuntimeError(
            "variance_audit unavailable; cannot process pairs."
        )
    before_audit = audit_text(
        _read_text(before_path), do_tier2=do_tier2, do_tier3=False,
    )
    after_audit = audit_text(
        _read_text(after_path), do_tier2=do_tier2, do_tier3=False,
    )
    before_signals = _extract_all_signals(before_audit)
    after_signals = _extract_all_signals(after_audit)
    deltas: dict[str, float] = {}
    for name in _PROFILE_SIGNALS:
        if name in before_signals and name in after_signals:
            deltas[name] = after_signals[name] - before_signals[name]
    return {
        "before_signals": before_signals,
        "after_signals": after_signals,
        "deltas": deltas,
    }


# ---------- Profile learning ----------


def learn_profile(
    *,
    pairs: list[dict[str, str]],
    profile_label: str | None = None,
    do_tier2: bool = False,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Build a per-signal delta-distribution profile from the
    supplied list of pairs. Each pair is ``{"before": PATH,
    "after": PATH}``."""
    n_pairs = len(pairs)
    if n_pairs == 0:
        raise ValueError("learn_profile: pairs list is empty.")

    pair_results: list[dict[str, Any]] = []
    delta_lists: dict[str, list[float]] = {
        name: [] for name in _PROFILE_SIGNALS
    }
    for idx, pair in enumerate(pairs):
        before = Path(pair["before"]).expanduser()
        after = Path(pair["after"]).expanduser()
        result = measure_pair(before, after, do_tier2=do_tier2)
        pair_id = (
            f"pair_{idx + 1:03d}" if not include_filenames
            else f"{before.name}->{after.name}"
        )
        pair_results.append({
            "pair_id": pair_id,
            "deltas": result["deltas"],
        })
        for name, delta in result["deltas"].items():
            delta_lists[name].append(delta)

    profile: dict[str, dict[str, Any]] = {}
    for name, deltas in delta_lists.items():
        if len(deltas) >= 2:
            profile[name] = {
                "n_pairs": len(deltas),
                "mean": statistics.mean(deltas),
                "stdev": statistics.stdev(deltas),
                "min": min(deltas),
                "max": max(deltas),
            }
        elif len(deltas) == 1:
            profile[name] = {
                "n_pairs": 1,
                "mean": deltas[0],
                "stdev": None,
                "min": deltas[0],
                "max": deltas[0],
            }

    return {
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "profile_label": profile_label,
        "n_pairs": n_pairs,
        "do_tier2": do_tier2,
        "signals": profile,
        "pair_results": (
            pair_results if include_filenames else
            [{"pair_id": r["pair_id"]} for r in pair_results]
        ),
    }


# ---------- Match mode ----------


def match_pair(
    *,
    profile: dict[str, Any],
    before_path: Path,
    after_path: Path,
    do_tier2: bool = False,
    z_threshold: float = 2.0,
) -> dict[str, Any]:
    """Compare a new (before, after) pair against a learned
    profile. Returns per-signal z-scores + an overall verdict."""
    measurement = measure_pair(
        before_path, after_path, do_tier2=do_tier2,
    )
    new_deltas = measurement["deltas"]
    profile_signals = profile.get("signals", {})

    per_signal: dict[str, dict[str, Any]] = {}
    z_scores: list[float] = []
    n_outside = 0
    n_inside = 0
    n_ambiguous = 0
    for name, delta in new_deltas.items():
        prof = profile_signals.get(name)
        if prof is None:
            per_signal[name] = {
                "delta": delta,
                "verdict": "no_profile_data",
            }
            continue
        mean = prof.get("mean", 0.0)
        sd = prof.get("stdev")
        if sd is None or sd == 0:
            # Single-pair profile or zero-sd profile: can't z-score.
            per_signal[name] = {
                "delta": delta,
                "profile_mean": mean,
                "profile_stdev": sd,
                "verdict": "ambiguous",
                "note": (
                    "profile sd is undefined or zero; cannot "
                    "z-score (need ≥2 pairs with non-identical "
                    "deltas)."
                ),
            }
            n_ambiguous += 1
            continue
        z = (delta - mean) / sd
        z_scores.append(z)
        verdict = (
            "outside_profile" if abs(z) > z_threshold
            else "inside_profile"
        )
        per_signal[name] = {
            "delta": delta,
            "profile_mean": mean,
            "profile_stdev": sd,
            "z_score": z,
            "verdict": verdict,
        }
        if abs(z) > z_threshold:
            n_outside += 1
        else:
            n_inside += 1

    if n_outside > 0:
        overall = "mismatch"
    elif n_inside == 0:
        overall = "ambiguous"
    elif n_inside >= 2:
        overall = "matches_profile"
    else:
        overall = "ambiguous"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "profile_label": profile.get("profile_label"),
        "profile_n_pairs": profile.get("n_pairs"),
        "z_threshold": z_threshold,
        "per_signal": per_signal,
        "n_signals_inside": n_inside,
        "n_signals_outside": n_outside,
        "n_signals_ambiguous": n_ambiguous,
        "verdict": overall,
        "claim_license": _claim_license_dict(
            profile=profile,
            verdict=overall,
            n_outside=n_outside,
        ),
    }


def _claim_license_dict(
    *,
    profile: dict[str, Any],
    verdict: str,
    n_outside: int,
) -> dict[str, Any]:
    n_pairs = profile.get("n_pairs", 0)
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "An editorial-transformation match report. For each "
            "signal in the editor's profile, the report tells "
            "the reader whether the new pair's delta is within "
            "the editor's typical range (z ≤ 2.0) or outside "
            "it. The overall verdict aggregates per-signal "
            "results: matches_profile (all signals inside), "
            "mismatch (any signal outside), or ambiguous "
            "(profile too narrow to test)."
        ),
        does_not_license=(
            "An authorship verdict. A `mismatch` verdict means "
            "the new pair does NOT look like ordinary editing "
            "by this editor; it does NOT commit to whether the "
            "edits were AI-generated, by a different human "
            "editor, by the same editor on a different kind of "
            "draft, or by no editor at all (the writer's own "
            "self-revision). The framework's discipline of "
            "\"differential diagnosis, not verdict\" applies in "
            "full. Likewise, `matches_profile` means the new "
            "pair's deltas fall within the editor's typical "
            "range; it does NOT prove this editor made the "
            "edits."
        ),
        comparison_set={
            "profile_n_pairs": n_pairs,
            "verdict": verdict,
            "n_signals_outside": n_outside,
            "z_threshold": 2.0,
        },
        additional_caveats=[
            "The profile's interpretive value scales with the "
            "number of pairs it was trained on. A 2-pair "
            "profile is testable but very narrow; a 10-pair "
            "profile is more robust. Match verdicts on "
            "single-pair profiles cannot reject anything and "
            "fall to `ambiguous`.",
            "Signal selection is conservative: only signals "
            "extractable from variance_audit's tier-1 surface "
            "are tracked. Tier-2 (spaCy POS) signals are "
            "available with `--tier2` but not on by default; "
            "they introduce more variance and require more "
            "pairs to stabilize.",
            "Z-score thresholds are heuristic (default 2.0). "
            "Calibration-pending against labeled editorial-"
            "transformation corpora.",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


_VERDICT_GLYPH = {
    "matches_profile": "✓",
    "mismatch": "✗",
    "ambiguous": "·",
}


def render_match_report(report: dict[str, Any]) -> str:
    verdict = report.get("verdict", "ambiguous")
    glyph = _VERDICT_GLYPH.get(verdict, "?")
    per_signal = report.get("per_signal", {})

    lines: list[str] = [
        "# Known-editor profile match",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Profile label:** "
        f"{report.get('profile_label') or '(unlabeled)'}",
        f"**Profile pairs:** {report.get('profile_n_pairs', 0)}",
        f"**Verdict:** `{verdict}` {glyph}",
        f"**Signals inside / outside / ambiguous:** "
        f"{report.get('n_signals_inside', 0)} / "
        f"{report.get('n_signals_outside', 0)} / "
        f"{report.get('n_signals_ambiguous', 0)}",
        "",
        "## Per-signal match",
        "",
        "| signal | new Δ | profile μ | profile σ | "
        "z-score | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for name, info in per_signal.items():
        delta = info.get("delta")
        mean = info.get("profile_mean")
        sd = info.get("profile_stdev")
        z = info.get("z_score")
        verd = info.get("verdict", "?")
        lines.append(
            f"| {name} | "
            f"{delta:+.4f} | "
            f"{mean if mean is None else f'{mean:+.4f}'} | "
            f"{sd if sd is None else f'{sd:.4f}'} | "
            f"{z if z is None else f'{z:+.2f}'} | "
            f"`{verd}` |"
        )
    lines.append("")

    license_block = report.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_pairs(path_str: str) -> list[dict[str, str]]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--pairs-json not found: {path_str}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--pairs-json malformed: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ValueError(
            "--pairs-json must be a JSON list of {before, after} objects."
        )
    out: list[dict[str, str]] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(
                f"--pairs-json entry {i} is not an object."
            )
        if "before" not in entry or "after" not in entry:
            raise ValueError(
                f"--pairs-json entry {i} missing required keys "
                "'before' and 'after'."
            )
        out.append({
            "before": str(entry["before"]),
            "after": str(entry["after"]),
        })
    return out


def _read_profile(path_str: str) -> dict[str, Any]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--profile not found: {path_str}"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--profile malformed: {exc}"
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="known_editor_profile.py",
        description=(
            "Learn an editorial-transformation profile from "
            "before/after pairs and match new pairs against it. "
            "Distinguishes 'this was smoothed' from 'this was "
            "smoothed in the ordinary way this editor smooths "
            "this writer.'"
        ),
    )
    sub = p.add_subparsers(dest="mode", required=True)

    learn = sub.add_parser(
        "learn",
        help="Learn a profile from a pairs manifest.",
    )
    learn.add_argument(
        "--pairs-json", required=True,
        help="JSON list of {before, after} pairs.",
    )
    learn.add_argument(
        "--out", required=True,
        help="Output path for the profile JSON.",
    )
    learn.add_argument(
        "--profile-label",
        help="Optional label for the profile (e.g., editor name).",
    )
    learn.add_argument(
        "--tier2", action="store_true",
        help="Include tier-2 (spaCy) signals; default is tier-1 only.",
    )
    learn.add_argument(
        "--include-filenames", action="store_true",
        help="Include filenames in the profile output "
             "(default: anonymize).",
    )

    match = sub.add_parser(
        "match",
        help="Compare a new pair against a saved profile.",
    )
    match.add_argument(
        "--before", required=True,
        help="Path to the new before-text.",
    )
    match.add_argument(
        "--after", required=True,
        help="Path to the new after-text.",
    )
    match.add_argument(
        "--profile", required=True,
        help="Path to the learned profile JSON.",
    )
    match.add_argument(
        "--z-threshold", type=float, default=2.0,
        help="Per-signal z-score threshold (default 2.0).",
    )
    match.add_argument(
        "--tier2", action="store_true",
        help="Include tier-2 (spaCy) signals; default is tier-1 only.",
    )
    match.add_argument("--json", action="store_true")
    match.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not HAS_VARIANCE_AUDIT:
        sys.stderr.write(
            "variance_audit unavailable; cannot run known-editor-profile.\n"
        )
        return 2

    if args.mode == "learn":
        try:
            pairs = _read_pairs(args.pairs_json)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"--pairs-json: {exc}\n")
            return 2
        if not pairs:
            sys.stderr.write(
                "--pairs-json: empty pairs list.\n"
            )
            return 2
        try:
            profile = learn_profile(
                pairs=pairs,
                profile_label=args.profile_label,
                do_tier2=args.tier2,
                include_filenames=args.include_filenames,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            sys.stderr.write(f"learn: {exc}\n")
            return 2
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(profile, indent=2, default=str),
            encoding="utf-8",
        )
        sys.stderr.write(
            f"Wrote profile ({profile['n_pairs']} pairs, "
            f"{len(profile['signals'])} signals) to {args.out}\n"
        )
        return 0

    if args.mode == "match":
        try:
            profile = _read_profile(args.profile)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"--profile: {exc}\n")
            return 2
        try:
            report = match_pair(
                profile=profile,
                before_path=Path(args.before).expanduser(),
                after_path=Path(args.after).expanduser(),
                do_tier2=args.tier2,
                z_threshold=args.z_threshold,
            )
        except (FileNotFoundError, RuntimeError) as exc:
            sys.stderr.write(f"match: {exc}\n")
            return 2

        out = (
            json.dumps(report, indent=2, default=str)
            if args.json else render_match_report(report)
        )
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
            sys.stderr.write(f"Wrote match report to {args.out}\n")
        else:
            sys.stdout.write(out)
        return 0

    sys.stderr.write(f"Unknown mode: {args.mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
