#!/usr/bin/env python3
"""reference_ecology_audit.py — non-voice reference-ecology profile.

A descriptive profile of how a document *references the world*: citation style
and density, quotation / attribution patterns, and external-link / domain
breadth. Useful for thematic / register profiling of essays, scholarship, and
policy writing.

This is deliberately **not** a voice or AI surface. Reference ecology is
**heavily topic-bound** — a writer changes subject and their citations, quotes,
and link domains change wholesale — so reading it as authorial voice (or as AI
provenance) is a category error. The claim-license refuses those inferences and
names the topic-leakage hazard, and the audit emits **no band and no verdict**,
only measurements. (ROADMAP: "Tier 5 — Adjacent surfaces → Reference Ecology
Audit … better as a thematic / register profile than a voice tool.")

Signals (stdlib regex over raw text):

  - Citations: parenthetical `(Name … YYYY)`, DOI, arXiv, `et al.`.
  - Footnotes: Markdown refs `[^id]` and definitions `[^id]:`.
  - Attribution: "according to X" / "as X" / "X argues/notes/writes/…".
  - Quotation: inline quote-pair count + blockquote lines; density / 1k.
  - Link ecology: markdown + bare URLs, total + density / 1k, distinct-domain
    breadth + top domains.

Usage:

    python3 scripts/reference_ecology_audit.py INPUT.md
    python3 scripts/reference_ecology_audit.py INPUT.md --json
    python3 scripts/reference_ecology_audit.py INPUT.md --out report.md
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

TASK_SURFACE = "reference_ecology"
TOOL_NAME = "reference_ecology_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 300

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)
_PAREN_RE = re.compile(r"\(([^)]*)\)")
_YEAR_RE = re.compile(r"\b(1[6-9]\d\d|20\d\d)\b")
_UPPER_RE = re.compile(r"[A-Z]")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s)]+")
_ARXIV_RE = re.compile(r"arxiv:\s*\d{4}\.\d{4,5}", re.IGNORECASE)
_ETAL_RE = re.compile(r"\bet al\.", re.IGNORECASE)
_FN_REF_RE = re.compile(r"\[\^[^\]\s]+\](?!:)")
_FN_DEF_RE = re.compile(r"^\s*\[\^[^\]\s]+\]:", re.MULTILINE)
_ATTR_A_RE = re.compile(r"\b(?:[Aa]ccording to|[Aa]s)\s+[A-Z][a-zA-Z]+")
_ATTR_B_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+\s+(?:argues|argued|notes|noted|writes|wrote|observes|"
    r"contends|suggests|claims|maintains|puts it)\b"
)
_BLOCKQUOTE_RE = re.compile(r"^\s*>", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_BARE_URL_RE = re.compile(r"(?<!\()\bhttps?://[^\s)]+")
_NETLOC_RE = re.compile(r"https?://([^/\s)]+)", re.IGNORECASE)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_1k(n: int, words: int) -> float:
    return round(n / words * 1000, 2) if words else 0.0


def _norm_domain(netloc: str) -> str:
    d = netloc.lower()
    return d[4:] if d.startswith("www.") else d  # prefix strip, not lstrip()


def audit_references(text: str) -> dict[str, Any]:
    """Compute the descriptive reference-ecology profile. Deterministic."""
    word_count = count_words(text)

    parenthetical = sum(
        1 for m in _PAREN_RE.finditer(text)
        if _YEAR_RE.search(m.group(1)) and _UPPER_RE.search(m.group(1))
    )
    doi = len(_DOI_RE.findall(text))
    arxiv = len(_ARXIV_RE.findall(text))
    et_al = len(_ETAL_RE.findall(text))

    fn_refs = len(_FN_REF_RE.findall(text))
    fn_defs = len(_FN_DEF_RE.findall(text))

    attribution = len(_ATTR_A_RE.findall(text)) + len(_ATTR_B_RE.findall(text))

    inline_pairs = text.count('"') // 2 + min(text.count("“"),
                                              text.count("”"))
    blockquote_lines = len(_BLOCKQUOTE_RE.findall(text))

    md_links = len(_MD_LINK_RE.findall(text))
    bare_urls = len(_BARE_URL_RE.findall(text))
    link_total = md_links + bare_urls
    domains = Counter(
        _norm_domain(m.group(1)) for m in _NETLOC_RE.finditer(text)
    )

    return {
        "citations": {
            "parenthetical": parenthetical,
            "rate_per_1k": _per_1k(parenthetical, word_count),
            "doi": doi,
            "arxiv": arxiv,
            "et_al": et_al,
        },
        "footnotes": {"refs": fn_refs, "definitions": fn_defs},
        "attribution": {
            "phrases": attribution,
            "rate_per_1k": _per_1k(attribution, word_count),
        },
        "quotation": {
            "inline_pairs": inline_pairs,
            "blockquote_lines": blockquote_lines,
            "density_per_1k": _per_1k(inline_pairs + blockquote_lines, word_count),
        },
        "links": {
            "total": link_total,
            "markdown": md_links,
            "bare_urls": bare_urls,
            "distinct_domains": len(domains),
            "top_domains": domains.most_common(5),
            "density_per_1k": _per_1k(link_total, word_count),
        },
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "the document's reference ecology: citation style and density, "
            "quotation / attribution patterns, and external-link / domain "
            "breadth."
        ),
        does_not_license=(
            "any inference about authorial voice, authorship identity, or "
            "AI provenance. Reference ecology is heavily topic-bound — it "
            "shifts with subject matter, not voice — so it must not be read "
            "as voice drift or an authorship signal."
        ),
        comparison_set={"mode": "single_document_descriptive"},
        additional_caveats=[
            "A thematic / register profile, not stylometry: changing topic "
            "changes the reference ecology wholesale.",
            "Attribution and citation detection is regex-heuristic (no NER); "
            "expect some miss/over-count on unusual citation styles.",
            "Descriptive only — no band, no verdict, no threshold.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/08-reference-ecology-audit.md",
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
        f"# Reference-ecology profile — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Insufficient length — no reference-ecology profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    c = r["citations"]
    a = r["attribution"]
    q = r["quotation"]
    lk = r["links"]
    lines += [
        "## Reference ecology",
        "",
        f"- **Citations:** {c['parenthetical']} parenthetical "
        f"({c['rate_per_1k']}/1k); DOI {c['doi']}, arXiv {c['arxiv']}, "
        f"et al. {c['et_al']}",
        f"- **Footnotes:** {r['footnotes']['refs']} refs, "
        f"{r['footnotes']['definitions']} definitions",
        f"- **Attribution phrases:** {a['phrases']} ({a['rate_per_1k']}/1k)",
        f"- **Quotation:** {q['inline_pairs']} inline pairs + "
        f"{q['blockquote_lines']} blockquote lines ({q['density_per_1k']}/1k)",
        f"- **Links:** {lk['total']} ({lk['density_per_1k']}/1k) across "
        f"{lk['distinct_domains']} distinct domains; top {lk['top_domains']}",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
                "floor for a meaningful reference-ecology profile."
            ],
        )
    else:
        payload = build_payload(
            audit_references(text), target_path=target_path,
            word_count=word_count, available=True,
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
