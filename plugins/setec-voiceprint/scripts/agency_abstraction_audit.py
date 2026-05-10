#!/usr/bin/env python3
"""agency_abstraction_audit.py — agency-loss / abstraction-drift detector.

Surfaces Tier-1 build, paired-release schedule Release 4. The
shipped Layer A suite measures distributional compression at the
sentence and token layers (sentence-length variance, lexical
diversity, POS-bigram KL). The paragraph audit (Release 2) added
macro-rhythm. The discourse audit (Release 3) added typed
scaffolding. What's structurally missing — and where AI smoothing
and institutional editing often do their most legible damage — is
**agency loss**: nominalized actions replacing concrete verbs,
agentless passives replacing named actors, light-verb constructions
("make a decision," "provide support") replacing direct verbs,
generic-institutional vocabulary replacing situated detail.

This module surfaces the agency-loss signal directly. Outputs:

  - Nominalization density: derivational-suffix nouns
    (-tion / -sion / -ment / -ity / -ance / -ence / -ness) per 1k.
  - Agentless passive rate: passive-voice constructions where the
    agent is not named (no "by X" phrase).
  - Light-verb constructions: count of "make/take/give/have/do +
    abstract noun" patterns.
  - Generic institutional vocabulary: framework / landscape /
    dynamic / challenge / opportunity / approach / strategy / etc.
  - Entity-to-action ratio: per 1k content words, the ratio of
    proper nouns (human / institution actors) to action verbs.
  - Concrete-detail density: sensory + situated nouns
    (kitchen, sweater, mile, Tuesday, etc., rough heuristic list).

Compression-fraction band call (Lightly / Moderately / Heavily
abstracted) over the six signals. Optional baseline comparison
emits per-signal z-scores. Structured ClaimLicense block.

Per the 1.32.1 narrowed dependency rule: this audit's output also
feeds the confounder audit's signal vocabulary as a strengthening
complement — the agency-loss family sharpens differential diagnosis
across institutional-prose vs. AI-smoothing without being a hard
prerequisite for the confounder audit's first useful version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "agency_abstraction_audit"
SCRIPT_VERSION = "1.0"


# --- Patterns --------------------------------------------------

_WORD_RE = re.compile(r"\b\w+\b")
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:[A-Z][a-z]+)*\b")

# Nominalization: suffixed derivationals indicating action-as-noun.
# Conservative wordlist anchor — the suffix patterns alone miss
# lexicalized exceptions (`station`, `mention` aren't always
# nominalized actions). The 4-character minimum on `(?<=\w)` keeps
# us from matching "tion" inside short stems.
_NOMINALIZATION_RE = re.compile(
    r"\b\w{3,}"
    r"(?:tion|sion|ment|ity|ance|ence|ness|ency|ancy|ization|isation)"
    r"s?\b",
    re.IGNORECASE,
)

# Agentless passive: BE-form + past-participle-shaped word, NOT
# followed by a "by" agent phrase within the next 6 words.
# Conservative — misses non-canonical past participles.
_PASSIVE_BE = re.compile(
    r"\b(?:is|are|was|were|been|being|be|am|will be|has been|have been|"
    r"had been|will have been|"
    r"could be|should be|would be|may be|might be|must be|can be)\s+"
    r"\w+(?:ed|en|wn)\b",
    re.IGNORECASE,
)

# Light-verb construction: verb + (optional article) + nominalized noun.
# We approximate by checking common light verbs followed within
# 1-2 tokens by a nominalization-shape word.
_LIGHT_VERBS = (
    "make", "makes", "made", "making",
    "take", "takes", "took", "taking", "taken",
    "give", "gives", "gave", "giving", "given",
    "have", "has", "had", "having",
    "do", "does", "did", "doing", "done",
    "provide", "provides", "provided", "providing",
    "conduct", "conducts", "conducted", "conducting",
    "perform", "performs", "performed", "performing",
)
_LIGHT_VERB_PATTERN = re.compile(
    r"\b(?:" + "|".join(_LIGHT_VERBS) + r")\s+"
    r"(?:a|an|the|some)?\s*"
    r"\w*"
    r"(?:tion|sion|ment|ance|ence)\b",
    re.IGNORECASE,
)

# Generic institutional vocabulary. Empirical list curated from
# the 2026-05 calibration sessions and corpus run notes — the
# vocabulary AI smoothing and institutional editing reach for when
# concrete actors and verbs would do.
_GENERIC_INSTITUTIONAL = re.compile(
    r"\b(?:framework|landscape|dynamic|paradigm|ecosystem|"
    r"approach|strategy|methodology|insight|"
    r"challenge|challenges|opportunity|opportunities|"
    r"impact|impacts|implication|implications|"
    r"key takeaway|stakeholders?|deliverables?|"
    r"actionable|leverage|leveraging|leveraged|"
    r"holistic|holistically|robust|robustly|"
    r"crucial|crucially|pivotal|pivotally)\b",
    re.IGNORECASE,
)

# Concrete-detail vocabulary anchor. Rough — not exhaustive. The
# audit reports density relative to baseline rather than committing
# to "this list IS the concrete vocabulary"; baseline comparison is
# the right way to read the signal.
_CONCRETE_DETAIL = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"morning|evening|afternoon|midnight|dawn|dusk|"
    r"kitchen|bedroom|hallway|garden|porch|attic|basement|"
    r"window|door|chair|table|cup|glass|mug|spoon|fork|knife|"
    r"sweater|coat|shirt|shoe|jacket|"
    r"mile|miles|inch|inches|foot|feet|yard|yards|"
    r"dog|cat|bird|tree|leaf|stone|rock|sand|"
    r"coffee|tea|bread|wine|water|"
    r"father|mother|brother|sister|grandmother|grandfather|"
    r"uncle|aunt|cousin|niece|nephew)\b",
    re.IGNORECASE,
)

# Common action verbs (rough — the test is "any of these,"
# tracked as a counterweight to nominalization).
_ACTION_VERB = re.compile(
    r"\b(?:walk|walked|walking|run|ran|running|jump|jumped|"
    r"speak|spoke|speaking|tell|told|telling|"
    r"hold|held|holding|grab|grabbed|grabbing|"
    r"open|opened|opening|close|closed|closing|"
    r"throw|threw|thrown|throwing|catch|caught|catching|"
    r"build|built|building|break|broke|breaking|broken|"
    r"climb|climbed|climbing|fall|fell|falling|fallen|"
    r"swim|swam|swimming|drive|drove|driving|driven|"
    r"cook|cooked|cooking|eat|ate|eating|eaten|"
    r"sleep|slept|sleeping|wake|woke|waking|woken|"
    r"laugh|laughed|laughing|cry|cried|crying|"
    r"sing|sang|singing|sung|dance|danced|dancing|"
    r"sit|sat|sitting|stand|stood|standing|"
    r"reach|reached|reaching|hand|handed|handing)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_thousand(count: int, n_words: int) -> float:
    if n_words <= 0:
        return 0.0
    return 1000.0 * count / n_words


def _agentless_passive_count(text: str) -> int:
    """Count passive constructions that don't have a `by ` agent
    phrase within the next 6 words after the past-participle.
    Conservative — misses some genuine passives, but the framing
    is "approximate rate" not "exact count."
    """
    n = 0
    for m in _PASSIVE_BE.finditer(text):
        end = m.end()
        # Look at the next ~30 chars for a "by " phrase.
        tail = text[end:end + 60]
        if re.search(r"\bby\s+\w", tail, re.IGNORECASE):
            continue
        n += 1
    return n


def audit_agency_abstraction(text: str) -> dict[str, Any]:
    """Compute the per-signal agency / abstraction features and a
    band call. Pure function; no I/O.
    """
    n_words = _word_count(text)
    if n_words == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }

    nominalization_count = len(_NOMINALIZATION_RE.findall(text))
    passive_count = _agentless_passive_count(text)
    light_verb_count = len(_LIGHT_VERB_PATTERN.findall(text))
    generic_inst_count = len(_GENERIC_INSTITUTIONAL.findall(text))
    concrete_count = len(_CONCRETE_DETAIL.findall(text))
    action_verb_count = len(_ACTION_VERB.findall(text))
    proper_noun_count = len(_PROPER_NOUN_RE.findall(text))

    densities = {
        "nominalization_per_1k": _per_thousand(nominalization_count, n_words),
        "agentless_passive_per_1k": _per_thousand(passive_count, n_words),
        "light_verb_per_1k": _per_thousand(light_verb_count, n_words),
        "generic_institutional_per_1k": _per_thousand(
            generic_inst_count, n_words,
        ),
        "concrete_detail_per_1k": _per_thousand(concrete_count, n_words),
        "action_verb_per_1k": _per_thousand(action_verb_count, n_words),
        "proper_noun_per_1k": _per_thousand(proper_noun_count, n_words),
    }

    # Entity-to-action ratio: (proper nouns + concrete details) / action verbs
    # at the 1k normalization. Higher = more situated; lower = more
    # abstracted. Floor at 1 to avoid division-by-zero.
    entity_count = proper_noun_count + concrete_count
    if action_verb_count > 0:
        entity_to_action_ratio = entity_count / action_verb_count
    else:
        entity_to_action_ratio = float(entity_count)  # action-floor

    # Compression-fraction band: each sub-signal contributes [0, 1].
    flagged_signals: list[str] = []
    if densities["nominalization_per_1k"] >= 30.0:
        flagged_signals.append("high_nominalization_density")
    if densities["agentless_passive_per_1k"] >= 5.0:
        flagged_signals.append("high_agentless_passive_rate")
    if densities["light_verb_per_1k"] >= 3.0:
        flagged_signals.append("high_light_verb_density")
    if densities["generic_institutional_per_1k"] >= 4.0:
        flagged_signals.append("high_generic_institutional_density")
    if densities["concrete_detail_per_1k"] < 1.5 and n_words >= 500:
        flagged_signals.append("low_concrete_detail_density")
    # Action-verb floor: only fires when both action density is low
    # AND the document is long enough to expect some.
    if (
        densities["action_verb_per_1k"] < 2.0
        and n_words >= 500
    ):
        flagged_signals.append("low_action_verb_density")

    n_signals = 6
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly abstracted"
    elif compression_fraction < 0.50:
        band = "Moderately abstracted"
    else:
        band = "Heavily abstracted"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_words,
        "raw_counts": {
            "nominalization": nominalization_count,
            "agentless_passive": passive_count,
            "light_verb": light_verb_count,
            "generic_institutional": generic_inst_count,
            "concrete_detail": concrete_count,
            "action_verb": action_verb_count,
            "proper_noun": proper_noun_count,
        },
        "densities_per_1k": densities,
        "entity_to_action_ratio": round(entity_to_action_ratio, 3),
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline comparison ---------------------------------------


def audit_baseline_agency(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """1.34.2 hardening: same conventions as paragraph_audit /
    discourse_move_signature — validate dir, surface skipped
    files, exclude target overlap, anonymize filenames by
    default."""
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    paths = [p for p in paths if not p.name.lower().startswith("readme")]

    target_resolved: Path | None = None
    if target_path is not None:
        try:
            target_resolved = Path(target_path).resolve()
        except OSError:
            target_resolved = None

    skipped_files: list[dict[str, str]] = []
    per_file: list[dict[str, Any]] = []
    pooled: dict[str, list[float]] = {}
    next_anon_id = 1
    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from agency baseline "
                        "(matches target path)\n"
                    )
                    continue
            except OSError:
                pass
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{len(skipped_files):03d}",
                "reason": f"unreadable: {exc}",
            })
            continue
        cleaned, _ = strip_non_prose(
            raw, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        a = audit_agency_abstraction(cleaned)
        if not a.get("available"):
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{next_anon_id:03d}",
                "reason": f"audit unavailable: {a.get('reason', 'unknown')}",
            })
            next_anon_id += 1
            continue
        per_file.append({
            "file": (
                p.name if include_filenames
                else f"baseline_{next_anon_id:03d}"
            ),
            "densities_per_1k": a["densities_per_1k"],
            "entity_to_action_ratio": a["entity_to_action_ratio"],
        })
        next_anon_id += 1
        for k, v in a["densities_per_1k"].items():
            pooled.setdefault(k, []).append(v)
        pooled.setdefault("entity_to_action_ratio", []).append(
            a["entity_to_action_ratio"],
        )

    def _mean_sd(vs: list[float]) -> dict[str, float]:
        if not vs:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = sum(vs) / len(vs)
        if len(vs) > 1:
            var = sum((x - m) ** 2 for x in vs) / (len(vs) - 1)
            sd = var ** 0.5
        else:
            sd = 0.0
        return {"mean": m, "sd": sd, "n": len(vs)}

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate": {k: _mean_sd(v) for k, v in pooled.items()},
        "include_filenames": include_filenames,
    }


def compare_to_baseline(
    target: dict[str, Any],
    baseline_block: dict[str, Any],
) -> dict[str, Any]:
    if not target.get("available"):
        return {"available": False, "reason": "target unavailable"}
    if baseline_block.get("n_files", 0) == 0:
        return {"available": False, "reason": "baseline empty"}
    agg = baseline_block["aggregate"]
    z_scores: dict[str, float | None] = {}
    target_dens = target["densities_per_1k"]
    for sig, value in target_dens.items():
        bucket = agg.get(sig, {})
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            z_scores[sig] = None
            continue
        z_scores[sig] = (value - bucket["mean"]) / sd
    e2a_bucket = agg.get("entity_to_action_ratio", {})
    if e2a_bucket.get("sd", 0.0) > 0 and e2a_bucket.get("n", 0) >= 2:
        z_scores["entity_to_action_ratio"] = (
            (target["entity_to_action_ratio"] - e2a_bucket["mean"])
            / e2a_bucket["sd"]
        )
    else:
        z_scores["entity_to_action_ratio"] = None
    return {
        "available": True,
        "z_scores": z_scores,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(audit: dict[str, Any]) -> str:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Agency-loss / abstraction-drift characterization of the "
            "input: nominalization density, agentless-passive rate, "
            "light-verb construction count, generic institutional "
            "vocabulary, concrete-detail density, action-verb "
            "density, entity-to-action ratio. Surfaces *what kind* "
            "of abstraction is happening, not just whether the prose "
            "feels institutional."
        ),
        does_not_license=(
            "An AI-provenance verdict. Heavy abstraction is "
            "characteristic of policy / legal / academic prose, "
            "AI-edited drafts, and writers under deadline pressure "
            "alike. The differential diagnosis of cause is the "
            "confounder audit's job (which now consumes the agency "
            "family as Release-4 evidence). Nor does the audit "
            "license claims about which abstraction patterns are "
            "'good' or 'bad' — institutional prose has legitimate "
            "reasons to use abstract noun forms."
        ),
        comparison_set={
            "n_words": audit.get("n_words"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Pattern matching is regex over English suffixes and "
            "wordlist anchors. Nominalizations like \"station\" or "
            "\"mention\" that aren't action-nouns will register; "
            "concrete-detail vocabulary not in the anchor list (most "
            "domain-specific terminology) won't. Read the densities "
            "as relative to baseline, not as absolutes.",
            "The agentless-passive heuristic is conservative — it "
            "looks for canonical past-participle shapes. Less-"
            "common participles will be missed.",
            "Heuristic thresholds (band call) are calibration-"
            "pending; treat the band as a cue, not a verdict.",
        ],
    )
    return lic.render_block().rstrip()


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            "# Agency / Abstraction audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    densities = audit["densities_per_1k"]
    lines: list[str] = [
        "# Agency / Abstraction audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        f"**Entity-to-action ratio:** "
        f"{audit['entity_to_action_ratio']:.2f}",
        "",
        "## Per-signal densities (per 1,000 words)",
        "",
        "| signal | count | density |",
        "|---|---:|---:|",
    ]
    raw = audit.get("raw_counts", {})
    for label, key in (
        ("nominalization", "nominalization_per_1k"),
        ("agentless passive", "agentless_passive_per_1k"),
        ("light verb construction", "light_verb_per_1k"),
        ("generic institutional", "generic_institutional_per_1k"),
        ("concrete detail", "concrete_detail_per_1k"),
        ("action verb", "action_verb_per_1k"),
        ("proper noun", "proper_noun_per_1k"),
    ):
        raw_key = key.replace("_per_1k", "")
        lines.append(
            f"| {label} | {raw.get(raw_key, 0)} | "
            f"{densities.get(key, 0.0):.2f} |"
        )
    lines.append("")

    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| signal | z-score |")
        lines.append("|---|---:|")
        zs = baseline_comparison["z_scores"]
        for sig, z in zs.items():
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {sig} | {z_str} |")
        lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agency_abstraction_audit.py",
        description=(
            "Agency-loss / abstraction-drift detector. Catches the "
            "smoothing failure mode where concrete actors and verbs "
            "are replaced by nominalized processes and generic "
            "institutional vocabulary."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument("--baseline-dir", help="Optional baseline directory.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--out", help="Write output to this path.")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules", help="Comma-separated strip rules.")
    p.add_argument("--strip-aggressive", action="store_true")
    p.add_argument(
        "--strip-masking",
        help="Optional masking profile (prose_body_only, etc.).",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help=(
            "Include raw baseline filenames in `per_file_summaries` "
            "(privacy default: anonymized as `baseline_001`)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2
    raw = target_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, prep_meta = strip_non_prose(
        raw, args.strip_rules,
        allow_non_prose=args.allow_non_prose,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
    )
    audit = audit_agency_abstraction(cleaned)
    audit["preprocessing"] = prep_meta

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_agency(
                args.baseline_dir,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
                strip_masking=args.strip_masking,
                target_path=target_path,
                include_filenames=args.include_baseline_filenames,
            )
        except FileNotFoundError as exc:
            sys.stderr.write(f"  baseline error: {exc}\n")
            return 2
        audit["baseline_block"] = block
        if block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files; baseline comparison skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, block)
        audit["baseline_comparison"] = baseline_comparison

    out = (
        json.dumps(audit, indent=2, default=str)
        if args.json else render_report(audit, baseline_comparison)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
