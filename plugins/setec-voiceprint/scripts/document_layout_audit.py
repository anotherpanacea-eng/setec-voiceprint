#!/usr/bin/env python3
"""document_layout_audit.py — non-voice document structure / layout profile.

A descriptive structural fingerprint of a document: heading cadence,
section-length variance, list / blockquote / code / link / table usage. Useful
for blog / Substack / policy / memo / newsletter workflows where formatting is
part of the working style.

This is deliberately **not** a voice or AI surface. A layout profile is a
*publishing-format* fingerprint — it changes with medium and template, not with
authorial voice — so the claim-license refuses any voice / authorship /
AI-provenance / quality inference, and the audit emits **no band and no verdict**,
only measurements. (ROADMAP: "Tier 5 — Adjacent surfaces → Document Structure /
Layout Audit … ship as its own small audit, not as a voice tool.")

Signals (stdlib regex over raw Markdown/plain structure; code-fence content is
excluded from structural detection):

  - Headings (ATX `#`…`######`): count, per-1k-word rate, level distribution,
    max depth, distinct levels.
  - Sections (spans between headings): count, word-count mean / sd / coefficient
    of variation — the "uniform sections" signal.
  - Lists: unordered (`-`/`*`/`+`) + ordered (`1.`/`1)`) item counts, per-1k-word
    rate, bullet-marker distribution, list-block count.
  - Blockquotes (`>`): line count + rate.
  - Fenced code blocks (``` / ~~~): pair count.
  - Links: Markdown `[..](..)` + bare `http(s)://` URLs; density per 1k words.
  - Thematic breaks (`---` / `***` / `___`).
  - Tables: pipe-table row count.

Usage:

    python3 scripts/document_layout_audit.py INPUT.md
    python3 scripts/document_layout_audit.py INPUT.md --json
    python3 scripts/document_layout_audit.py INPUT.md --out report.md
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "document_layout"
TOOL_NAME = "document_layout_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 300

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_UL_ITEM_RE = re.compile(r"^\s*([-*+])\s+\S")
_OL_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+\S")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_BARE_URL_RE = re.compile(r"(?<!\()\bhttps?://[^\s)]+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_1k(n: int, words: int) -> float:
    return round(n / words * 1000, 2) if words else 0.0


def audit_layout(text: str) -> dict[str, Any]:
    """Compute the descriptive layout profile. Pure function, deterministic."""
    lines = text.splitlines()
    word_count = count_words(text)

    in_fence = False
    fenced_count = 0
    heading_levels: Counter[int] = Counter()
    ul_items = 0
    ol_items = 0
    bullet_markers: Counter[str] = Counter()
    list_blocks = 0
    prev_list = False
    blockquote_lines = 0
    hr_count = 0
    table_rows = 0
    md_links = 0
    bare_urls = 0

    section_word_counts: list[int] = []
    current_section_words = 0

    for line in lines:
        if _FENCE_RE.match(line):
            if in_fence:
                fenced_count += 1  # count complete pairs on close
            in_fence = not in_fence
            prev_list = False
            continue
        if in_fence:
            current_section_words += count_words(line)
            prev_list = False
            continue

        # Links counted on every non-code line (incl. headings/lists).
        md_links += len(_MD_LINK_RE.findall(line))
        bare_urls += len(_BARE_URL_RE.findall(line))

        hm = _ATX_HEADING_RE.match(line)
        if hm:
            section_word_counts.append(current_section_words)
            current_section_words = 0
            heading_levels[len(hm.group(1))] += 1
            prev_list = False
            continue

        current_section_words += count_words(line)

        is_list = False
        um = _UL_ITEM_RE.match(line)
        if um:
            ul_items += 1
            bullet_markers[um.group(1)] += 1
            is_list = True
        elif _OL_ITEM_RE.match(line):
            ol_items += 1
            is_list = True
        if is_list and not prev_list:
            list_blocks += 1
        prev_list = is_list

        if _BLOCKQUOTE_RE.match(line):
            blockquote_lines += 1
        if _HR_RE.match(line):
            hr_count += 1
        if _TABLE_ROW_RE.match(line):
            table_rows += 1

    section_word_counts.append(current_section_words)
    nonempty_sections = [w for w in section_word_counts if w > 0]

    sec_mean: float | None = None
    sec_sd: float | None = None
    sec_cv: float | None = None
    if nonempty_sections:
        sec_mean = round(statistics.fmean(nonempty_sections), 2)
        if len(nonempty_sections) >= 2:
            sec_sd = round(statistics.stdev(nonempty_sections), 2)
            sec_cv = round(sec_sd / sec_mean, 4) if sec_mean else None

    heading_count = int(sum(heading_levels.values()))
    list_item_total = ul_items + ol_items
    link_total = md_links + bare_urls

    return {
        "headings": {
            "count": heading_count,
            "rate_per_1k": _per_1k(heading_count, word_count),
            "level_distribution": {
                f"h{lvl}": heading_levels[lvl] for lvl in sorted(heading_levels)
            },
            "max_depth": max(heading_levels) if heading_levels else 0,
            "distinct_levels": len(heading_levels),
        },
        "sections": {
            "count": len(nonempty_sections),
            "word_count_mean": sec_mean,
            "word_count_sd": sec_sd,
            "coefficient_of_variation": sec_cv,
        },
        "lists": {
            "unordered_items": ul_items,
            "ordered_items": ol_items,
            "item_rate_per_1k": _per_1k(list_item_total, word_count),
            "bullet_markers": dict(sorted(bullet_markers.items())),
            "list_blocks": list_blocks,
        },
        "blockquotes": {
            "lines": blockquote_lines,
            "rate_per_1k": _per_1k(blockquote_lines, word_count),
        },
        "code_blocks": {"fenced_count": fenced_count},
        "links": {
            "markdown": md_links,
            "bare_urls": bare_urls,
            "total": link_total,
            "density_per_1k": _per_1k(link_total, word_count),
        },
        "thematic_breaks": hr_count,
        "tables": {"rows": table_rows},
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "the document's structural / formatting profile: heading cadence, "
            "section-length variance, and list / blockquote / code / link / "
            "table usage."
        ),
        does_not_license=(
            "any inference about authorial voice, authorship identity, "
            "AI provenance, or writing quality. A layout profile is a "
            "publishing-format fingerprint — topic- and medium-bound — not "
            "stylometry."
        ),
        comparison_set={"mode": "single_document_descriptive"},
        additional_caveats=[
            "Operates on raw Markdown/plain structure; format changes with "
            "medium and template, not voice.",
            "Descriptive only — no band, no verdict, no threshold.",
        ],
        references=[
            "specs/07-document-layout-audit.md",
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
        f"# Document layout profile — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Insufficient length — no layout profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    h = r["headings"]
    s = r["sections"]
    li = r["lists"]
    lk = r["links"]
    lines += [
        "## Structure",
        "",
        f"- **Headings:** {h['count']} ({h['rate_per_1k']}/1k words); "
        f"levels {h['level_distribution']}, max depth {h['max_depth']}",
        f"- **Sections:** {s['count']}; mean {s['word_count_mean']} words, "
        f"sd {s['word_count_sd']}, CV {s['coefficient_of_variation']}",
        f"- **Lists:** {li['unordered_items']} unordered + {li['ordered_items']} "
        f"ordered ({li['item_rate_per_1k']}/1k); markers {li['bullet_markers']}; "
        f"{li['list_blocks']} blocks",
        f"- **Blockquotes:** {r['blockquotes']['lines']} lines",
        f"- **Code blocks:** {r['code_blocks']['fenced_count']}",
        f"- **Links:** {lk['total']} ({lk['density_per_1k']}/1k) "
        f"= {lk['markdown']} md + {lk['bare_urls']} bare",
        f"- **Thematic breaks:** {r['thematic_breaks']}",
        f"- **Table rows:** {r['tables']['rows']}",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="Path to .md or .txt target file.")
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
                "floor for a meaningful layout profile."
            ],
        )
    else:
        results = audit_layout(text)
        payload = build_payload(
            results, target_path=target_path, word_count=word_count,
            available=True,
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
