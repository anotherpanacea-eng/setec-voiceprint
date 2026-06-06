#!/usr/bin/env python3
"""formulaicity_audit.py — non-voice phraseological-texture profile.

Measures the density of common **generic / stock** phrases (clichés, filler
transitions, hedge boilerplate, corporate idiom) drawn from a small, illustrative,
user-extensible built-in list.

This is deliberately **not** an AI detector and **not** a quality judgment. The
roadmap is explicit on both failure modes: an "LLM-associated phrase" list drifts
as models change (so shipping it as an AI signal would be a Pangram-style
anti-goal), and many writers use these phrases perfectly legitimately. So the
framing is *phraseological texture* — a descriptive density measurement — and the
claim-license refuses AI-provenance, voice/authorship, and writing-quality
inference. No band, no verdict.

The built-in list is intentionally small and generic; override or extend it with
`--phrases-file` (one phrase per line, optional `group:phrase`).

Usage:

    python3 scripts/formulaicity_audit.py INPUT.md
    python3 scripts/formulaicity_audit.py INPUT.md --json
    python3 scripts/formulaicity_audit.py INPUT.md --phrases-file mylist.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "formulaicity"
TOOL_NAME = "formulaicity_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 300

# Small, generic, illustrative — NOT an "AI words" list. User-extensible.
BUILTIN_PHRASES: dict[str, list[str]] = {
    "stock_transitions": [
        "at the end of the day", "when all is said and done", "needless to say",
        "it goes without saying", "when it comes to", "in today's world",
        "more often than not", "the fact of the matter is", "in this day and age",
        "last but not least", "all things considered",
    ],
    "hedge_boilerplate": [
        "it's important to note", "it is important to note", "it's worth noting",
        "it is worth noting", "it should be noted", "one could argue",
        "it could be argued", "for all intents and purposes",
    ],
    "corporate_cliche": [
        "think outside the box", "low-hanging fruit", "move the needle",
        "circle back", "best practices", "game changer", "paradigm shift",
        "going forward", "touch base", "deep dive",
    ],
    "wordy_filler": [
        "in order to", "due to the fact that", "a number of",
        "the vast majority of", "in the event that", "at this point in time",
        "in spite of the fact that",
    ],
}

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_1k(n: int, words: int) -> float:
    return round(n / words * 1000, 2) if words else 0.0


def _compile(phrase: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)


def load_phrases(path: str | None) -> tuple[dict[str, list[str]], bool]:
    """Return (groups, is_custom). Custom file lines: 'phrase' or 'group:phrase'."""
    if not path:
        return BUILTIN_PHRASES, False
    groups: dict[str, list[str]] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            group, phrase = line.split(":", 1)
            group, phrase = group.strip(), phrase.strip()
        else:
            group, phrase = "custom", line
        if phrase:
            groups.setdefault(group, []).append(phrase)
    return groups, True


def audit_formulaicity(text: str, groups: dict[str, list[str]],
                       *, is_custom: bool) -> dict[str, Any]:
    """Compute the descriptive stock-phrase density profile. Deterministic."""
    word_count = count_words(text)
    by_group: dict[str, int] = {}
    phrase_hits: Counter[str] = Counter()
    list_size = 0
    for group, phrases in groups.items():
        g_total = 0
        for phrase in phrases:
            list_size += 1
            n = len(_compile(phrase).findall(text))
            if n:
                phrase_hits[phrase] += n
                g_total += n
        by_group[group] = g_total
    total_hits = int(sum(phrase_hits.values()))
    return {
        "total_hits": total_hits,
        "density_per_1k": _per_1k(total_hits, word_count),
        "distinct_phrases": len(phrase_hits),
        "by_group": dict(sorted(by_group.items())),
        "top_phrases": phrase_hits.most_common(5),
        "list_size": list_size,
        "custom_list": is_custom,
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "the density of common generic/stock phrases drawn from a small, "
            "illustrative, user-extensible built-in list — a phraseological-"
            "texture measurement."
        ),
        does_not_license=(
            "any inference about AI provenance, authorial voice/identity, or "
            "writing quality. Stock-phrase density is not an AI signal and not "
            "a quality judgment."
        ),
        comparison_set={"mode": "single_document_descriptive"},
        additional_caveats=[
            "The built-in phrase list is illustrative, not exhaustive, and "
            "drifts as language changes — it is NOT a curated 'AI words' list.",
            "Many writers use these phrases legitimately; density is heavily "
            "register-dependent.",
            "Descriptive only — no band, no verdict, no threshold.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/09-formulaicity-audit.md",
        ],
    )


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  word_count: int, available: bool,
                  warnings: list[str] | None = None) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=word_count,
        baseline=None,
        results=results if available else {},
        claim_license=_claim_license() if available else None,
        available=available,
        warnings=warnings,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Formulaicity (phraseological-texture) profile — "
        f"`{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Insufficient length — no formulaicity profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    lines += [
        "## Phraseological texture",
        "",
        f"- **Stock-phrase hits:** {r['total_hits']} "
        f"({r['density_per_1k']}/1k words); {r['distinct_phrases']} distinct "
        f"of {r['list_size']} listed{' (custom list)' if r['custom_list'] else ''}",
        f"- **By group:** {r['by_group']}",
        f"- **Top phrases:** {r['top_phrases']}",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="Path to .md or .txt target file.")
    p.add_argument("--phrases-file",
                   help="Override the built-in list (one phrase/line, optional group:phrase).")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")
    word_count = count_words(text)

    if word_count < LENGTH_FLOOR_WORDS:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count, available=False,
            warnings=[
                f"Target is {word_count} words; below the {LENGTH_FLOOR_WORDS}-word "
                "floor for a meaningful formulaicity profile."
            ],
        )
    else:
        groups, is_custom = load_phrases(args.phrases_file)
        payload = build_payload(
            audit_formulaicity(text, groups, is_custom=is_custom),
            target_path=target_path, word_count=word_count, available=True,
        )

    text_out = (json.dumps(payload, indent=2, default=str)
                if args.json else render_report(payload))
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
