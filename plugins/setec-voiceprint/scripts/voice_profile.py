#!/usr/bin/env python3
"""
voice_profile.py
Produce a private, human-readable stylometric profile from a writer/register
baseline corpus.

The output should be treated as private. A voice profile is useful for
protecting a writer's idiolect during revision, but it is also a voice-cloning
input if shared carelessly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from preprocessing import available_rule_names, strip_non_prose
from stylometry_core import build_profile, load_entries

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore


# See variance_audit.TASK_SURFACE for the contract.
TASK_SURFACE = "voice_coherence"
TOOL_NAME = "voice_profile"
SCRIPT_VERSION = "1.0"


def fmt(value: Any, digits: int = 6) -> str:
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def md_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def build_limits(args: argparse.Namespace) -> dict[str, int]:
    return {
        "function_words": args.function_top,
        # --char-top sets the per-n cap for each char-ngram family
        # separately (3-grams, 4-grams, 5-grams).
        "char_ngrams_3": args.char_top,
        "char_ngrams_4": args.char_top,
        "char_ngrams_5": args.char_top,
        "pos_trigrams": args.pos_top,
        "dependency_ngrams": args.dep_top,
    }


def render_feature_table(items: list[dict[str, Any]], *, value_label: str, top_n: int) -> list[str]:
    lines = []
    lines.append(f"| feature | {value_label} | sd | cv |")
    lines.append("|---|---:|---:|---:|")
    for item in items[:top_n]:
        lines.append(
            f"| `{md_cell(item['feature'])}` | "
            f"{fmt(item['mean'])} | {fmt(item['sd'])} | {fmt(item.get('cv'), 4)} |"
        )
    return lines


def render_report(profile: dict[str, Any], top_n: int) -> str:
    baseline = profile["baseline_summary"]
    lines = []
    lines.append("# Private Voice Profile")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(f"**{profile['privacy']}**")
    lines.append("")
    lines.append(
        "This profile describes the supplied baseline corpus. It is not an authorship "
        "certificate and should not be shared outside the local workspace."
    )
    lines.append("")
    lines.append("## Corpus")
    lines.append("")
    lines.append(f"**Files:** {baseline.get('n_files', 0)}")
    lines.append(f"**Total words:** {baseline.get('total_words', 0)}")
    prep = profile.get("preprocessing") or {}
    if prep:
        if prep.get("opt_out"):
            lines.append("**Preprocessing:** skipped by `--allow-non-prose`")
        else:
            ratio = prep.get("strip_ratio", 0.0)
            ratio_str = f"{ratio:.1%}" if isinstance(ratio, (int, float)) else "n/a"
            lines.append(
                f"**Preprocessing:** stripped {prep.get('tokens_stripped', 0)} "
                f"tokens ({ratio_str}; dominant rule: "
                f"{prep.get('dominant_rule') or 'none'})"
            )
    lines.append(
        f"**Words per file:** mean {baseline.get('mean_words', 0):.0f}, "
        f"range {baseline.get('min_words', 0)}-{baseline.get('max_words', 0)}"
    )
    if profile.get("warnings"):
        lines.append("")
        lines.append("**Warnings:**")
        for warning in profile["warnings"]:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append("## Feature Families")
    lines.append("")
    lines.append("| family | features retained |")
    lines.append("|---|---:|")
    for family, count in sorted(profile["selected_features"].items()):
        lines.append(f"| {family} | {count} |")
    lines.append("")

    for family, info in sorted(profile["families"].items()):
        lines.append(f"## {family}")
        lines.append("")
        lines.append("Most frequent or highest-valued baseline features:")
        lines.append("")
        lines.extend(render_feature_table(info["top_features"], value_label="mean", top_n=top_n))
        lines.append("")
        lines.append("Most stable nonzero baseline features:")
        lines.append("")
        lines.extend(render_feature_table(info["most_stable_features"], value_label="mean", top_n=top_n))
        lines.append("")

    return "\n".join(lines)


def _claim_license(profile: dict[str, Any]) -> ClaimLicense:
    """Structured ClaimLicense for the voice-profile output. Per
    ``internal/SPEC_output_schema_unification.md`` §11, scripts that
    lacked a claim_license gain a basic block as part of migration.

    The voice profile output is voice-cloning-grade input by design.
    The license names the privacy constraint as load-bearing.
    """
    baseline = profile.get("baseline_summary", {}) or {}
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A private stylometric profile of the supplied baseline "
            "corpus: per-family feature inventory (function words, "
            "character n-grams, POS trigrams, dependency n-grams), "
            "with each feature's baseline mean, standard deviation, "
            "and coefficient of variation. The profile is intended "
            "as a within-author reference for downstream voice-"
            "coherence work (voice_distance, idiolect_detector, "
            "mimicry_cosplay_audit)."
        ),
        does_not_license=(
            "An authorship verdict. The profile is descriptive, not "
            "diagnostic. CRITICAL: the profile is voice-cloning-"
            "grade input. Treat it as private to the author / "
            "workspace; do not share outside the author's control. "
            "The script enforces this by refusing public output "
            "paths unless `--allow-public-output` is explicitly "
            "passed (see is_private_output_path)."
        ),
        comparison_set={
            "n_files": baseline.get("n_files"),
            "total_words": baseline.get("total_words"),
            "mean_words": baseline.get("mean_words"),
            "features_retained": dict(
                profile.get("selected_features", {})
            ),
        },
        additional_caveats=[
            "Voice profiles are durable inputs for impersonation. "
            "If the profile is shared, the author's voiceprint is "
            "shared — the same way a fingerprint database is "
            "shared. Default policy: keep under "
            "ai-prose-baselines-private/, sibling to the SETEC "
            "repo, not inside it.",
            "Feature stability (CV) varies sharply across families. "
            "Function-word ratios are usually the most stable; "
            "character-n-gram inventories the least. Read most-"
            "stable feature lists alongside topic-and-register "
            "consistency in the corpus.",
            "POS and dependency families require spaCy + "
            "en_core_web_sm. Profile built with `--no-spacy` "
            "carries fewer feature families.",
        ],
    )


def build_audit_payload(
    profile: dict[str, Any],
    *,
    target_path: Path | str | None,
) -> dict[str, Any]:
    """Wrap the voice-profile dict in the schema_version 1.0
    envelope. The profiled corpus IS the target — voice_profile
    profiles a baseline rather than comparing a target against one.
    ``envelope.baseline`` is therefore None.
    """
    baseline = profile.get("baseline_summary", {}) or {}
    target_words = int(baseline.get("total_words", 0) or 0)
    target_extra: dict[str, Any] = {
        "privacy": profile.get("privacy"),
        "n_files": baseline.get("n_files"),
    }
    if "preprocessing" in profile and profile["preprocessing"]:
        target_extra["preprocessing"] = profile["preprocessing"]

    # Pull metadata-ish keys off the profile dict so they don't
    # collide with the envelope.
    profile_payload = {
        k: v for k, v in profile.items()
        if k not in {
            "task_surface", "preprocessing", "privacy",
        }
    }

    warnings = profile.get("warnings") or []

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=profile_payload,
        claim_license=_claim_license(profile),
        warnings=list(warnings),
        target_extra={
            k: v for k, v in target_extra.items() if v is not None
        } or None,
    )


def is_private_output_path(path: str | None) -> bool:
    if not path:
        return True
    resolved_parts = Path(path).expanduser().resolve().parts
    return "ai-prose-baselines-private" in resolved_parts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a private stylometric profile from a baseline corpus."
    )
    parser.add_argument("--baseline-dir", help="Directory of baseline .txt/.md files.")
    parser.add_argument("--manifest", help="Optional JSONL corpus manifest.")
    parser.add_argument("--use", default="baseline",
                        help="Manifest filter: required use tag (default: baseline).")
    parser.add_argument("--split", help="Manifest filter: split value.")
    parser.add_argument("--register", help="Manifest filter: register value.")
    parser.add_argument("--persona", help="Manifest filter: persona value.")
    parser.add_argument("--ai-status", default="pre_ai_human",
                        help="Manifest filter: ai_status (default: pre_ai_human).")
    parser.add_argument("--function-top", type=int, default=100,
                        help="Top function words from baseline (default 100).")
    parser.add_argument("--char-top", type=int, default=200,
                        help="Top character n-grams per n from baseline "
                             "(default 200). Applies separately to "
                             "3-grams, 4-grams, and 5-grams.")
    parser.add_argument("--pos-top", type=int, default=300,
                        help="Top POS trigrams from baseline (default 300).")
    parser.add_argument("--dep-top", type=int, default=300,
                        help="Top dependency-label n-grams from baseline (default 300).")
    parser.add_argument("--top", type=int, default=20,
                        help="Rows to show per table (default 20).")
    parser.add_argument("--no-spacy", action="store_true",
                        help="Skip POS and dependency feature families.")
    parser.add_argument("--allow-non-prose", action="store_true",
                        help="Skip default corpus-hygiene stripping. Use "
                             "only when intentionally profiling code-heavy "
                             "or markup-heavy text.")
    parser.add_argument("--strip-rules",
                        help="Comma-separated preprocessing rules to enable. "
                             "Default: all conservative rules. Available: "
                             + ", ".join(available_rule_names()) + ".")
    parser.add_argument("--strip-aggressive", action="store_true",
                        help="Also strip URL-only lines, image URLs, link "
                             "wrappers, footnotes, and citations.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write report to file instead of stdout.")
    parser.add_argument(
        "--json-out",
        help="Write the JSON audit envelope to this path (default-private, "
             "like --out but always JSON). Used by the setec_run dispatcher's "
             "file-delivery path; the privacy guard requires a path under "
             "ai-prose-baselines-private/ unless --allow-public-output.",
    )
    parser.add_argument(
        "--allow-public-output",
        action="store_true",
        help="Allow writing a voice profile outside ai-prose-baselines-private/.",
    )
    args = parser.parse_args()

    if not args.baseline_dir and not args.manifest:
        parser.error("Provide either --baseline-dir or --manifest.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))

    entries = load_entries(
        baseline_dir=args.baseline_dir,
        manifest=args.manifest,
        use=args.use,
        split=args.split,
        register=args.register,
        persona=args.persona,
        ai_status=args.ai_status,
    )
    if not entries:
        print("No baseline entries matched the requested filters.", file=sys.stderr)
        return 1

    profile = build_profile(
        entries,
        include_spacy=not args.no_spacy,
        limits=build_limits(args),
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
    )
    profile["task_surface"] = TASK_SURFACE

    # setec_run file-delivery path (R2/R3): --json-out writes the JSON audit
    # envelope to a private file, which the dispatcher reads back and projects
    # to stdout (spec §3). Mirrors pov_voice_profile.py's --json-out so both
    # voice-clone surfaces share one file-delivery contract. Default-private:
    # the path must live under ai-prose-baselines-private/ unless
    # --allow-public-output (same guard --out enforces below).
    if args.json_out:
        payload = build_audit_payload(
            profile,
            target_path=args.baseline_dir or args.manifest,
        )
        if not args.allow_public_output and not is_private_output_path(args.json_out):
            print(
                "Refusing to write a voice profile JSON outside "
                "ai-prose-baselines-private/. Pass --allow-public-output to "
                "override.",
                file=sys.stderr,
            )
            return 2
        json_out_path = Path(args.json_out)
        json_out_path.parent.mkdir(parents=True, exist_ok=True)
        json_out_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8",
        )
        print(f"Written JSON to {args.json_out}", file=sys.stderr)
        return 0

    if args.json:
        payload = build_audit_payload(
            profile,
            target_path=args.baseline_dir or args.manifest,
        )
        output = json.dumps(payload, indent=2, default=str)
    else:
        output = render_report(profile, args.top)

    if args.out and not args.allow_public_output and not is_private_output_path(args.out):
        print(
            "Refusing to write a voice profile outside ai-prose-baselines-private/. "
            "Pass --allow-public-output to override.",
            file=sys.stderr,
        )
        return 2

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        # No --out path means the profile goes to stdout, which the
        # is_private_output_path() guard above cannot see (stdout has
        # no path). Without this gate, a `voice_profile.py --json`
        # call would dump a voice-cloning-grade input to stdout with
        # exit 0 — Codex P2 finding on PR #82. Mirrors the same
        # default-private posture voice_drift_tracker and
        # pov_voice_profile enforce.
        if not args.allow_public_output:
            print(
                "Refusing to write a voice profile to stdout without "
                "--allow-public-output. Voice profile output is a "
                "voice-cloning input; default-private posture requires "
                "either --out into ai-prose-baselines-private/, or "
                "--allow-public-output for non-personal corpora "
                "(e.g., Federalist).",
                file=sys.stderr,
            )
            return 2
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
