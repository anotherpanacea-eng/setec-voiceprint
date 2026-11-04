#!/usr/bin/env python3
"""evidence_pack.py — bundle multiple SETEC audit envelopes into one report.

A SETEC run usually produces several JSON outputs for one draft (variance,
voice-distance, AIC, document-layout, …). Each is a `output_schema.build_output`
envelope. This tool reads any number of those envelopes and renders **one
combined evidence pack** — Markdown (default) or a self-contained HTML page —
grouped by target document, with each audit's key results and its
claim-license "Reports / Does NOT report" lines, plus an aggregated warnings
section.

It is a *reporting tool*, not an audit: it computes nothing and asserts no
verdict. It only collates what the audits already licensed. Non-SETEC or
malformed JSON files are skipped with a warning rather than aborting the pack.

Usage:

    python3 scripts/evidence_pack.py a.variance.json b.voice.json --out pack.md
    python3 scripts/evidence_pack.py *.json --format html --out pack.html
    python3 scripts/evidence_pack.py run1.json --title "Draft 3 evidence pack"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

TOOL_NAME = "evidence_pack"
SCRIPT_VERSION = "1.0"


def is_setec_envelope(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and "schema_version" in obj
        and "task_surface" in obj
        and "tool" in obj
    )


def load_envelopes(paths: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (envelopes, warnings). Malformed / non-SETEC files are skipped."""
    envelopes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for p in paths:
        path = Path(p).expanduser()
        if not path.is_file():
            warnings.append(f"skipped {p}: not a file")
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            warnings.append(f"skipped {p}: unreadable JSON ({exc.__class__.__name__})")
            continue
        if not is_setec_envelope(obj):
            warnings.append(f"skipped {p}: not a SETEC audit envelope")
            continue
        obj["_source_file"] = str(path)
        envelopes.append(obj)
    return envelopes, warnings


def _target_key(env: dict[str, Any]) -> str:
    tgt = env.get("target") or {}
    return tgt.get("path") or "(no target path)"


def _summarize_results(results: dict[str, Any]) -> list[str]:
    """One compact line per top-level results key (scalars inline, nested summarized)."""
    out: list[str] = []
    for k, v in results.items():
        if isinstance(v, dict):
            out.append(f"`{k}`: {{{len(v)} fields}}")
        elif isinstance(v, list):
            out.append(f"`{k}`: [{len(v)} items]")
        elif isinstance(v, bool) or v is None or isinstance(v, (int, float, str)):
            out.append(f"`{k}`: {v}")
        else:
            out.append(f"`{k}`: …")
    return out


def render_pack(envelopes: list[dict[str, Any]], *, title: str,
                load_warnings: list[str] | None = None) -> str:
    """Render the combined pack as Markdown. Deterministic (no timestamp)."""
    lines: list[str] = [f"# {title}", ""]
    if not envelopes:
        lines.append("_No SETEC audit envelopes were supplied._")
        for w in load_warnings or []:
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    lines.append(
        f"Bundled **{len(envelopes)}** audit envelope(s) across "
        f"**{len(set(_target_key(e) for e in envelopes))}** target(s). "
        "This pack collates measurements the audits already produced; it adds "
        "no new analysis and asserts no verdict."
    )
    lines.append("")

    grouped: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for env in envelopes:
        grouped.setdefault(_target_key(env), []).append(env)

    all_warnings: list[str] = list(load_warnings or [])

    for target, envs in grouped.items():
        words = (envs[0].get("target") or {}).get("words")
        wc = f" · {words} words" if isinstance(words, int) else ""
        lines += [f"## Target: `{target}`{wc}", ""]
        for env in sorted(envs, key=lambda e: str(e.get("tool", ""))):
            tool = env.get("tool", "?")
            surface = env.get("task_surface", "?")
            avail = env.get("available", True)
            ver = env.get("version", "?")
            lines.append(f"### `{tool}` v{ver} — surface `{surface}`"
                         + ("" if avail else " — _unavailable_"))
            lines.append("")
            if avail and isinstance(env.get("results"), dict) and env["results"]:
                for s in _summarize_results(env["results"]):
                    lines.append(f"- {s}")
            cl = env.get("claim_license")
            if isinstance(cl, dict):
                if cl.get("licenses"):
                    lines.append(f"- **Reports:** {cl['licenses']}")
                if cl.get("does_not_license"):
                    lines.append(f"- **Does NOT report:** {cl['does_not_license']}")
            for w in env.get("warnings") or []:
                all_warnings.append(f"[{tool}] {w}")
            lines.append("")

    if all_warnings:
        lines += ["## Warnings", ""]
        for w in all_warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines += [
        "## What this pack licenses",
        "",
        "This is a collation of independent audit outputs. Each audit's own "
        "`Reports` / `Does NOT report` lines above are authoritative for that "
        "measurement. Bundling them together does **not** combine them into a "
        "single score or verdict — SETEC refuses that by design.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_INLINE_CODE = re.compile(r"`([^`]+)`")


def _inline_html(text: str) -> str:
    esc = html.escape(text)
    esc = _INLINE_BOLD.sub(r"<strong>\1</strong>", esc)
    esc = _INLINE_CODE.sub(r"<code>\1</code>", esc)
    return esc


def markdown_to_html(md: str, *, title: str) -> str:
    """Minimal converter for the controlled Markdown subset render_pack emits."""
    body: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            close_list()
            continue
        if line.startswith("### "):
            close_list(); body.append(f"<h3>{_inline_html(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list(); body.append(f"<h2>{_inline_html(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list(); body.append(f"<h1>{_inline_html(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>"); in_list = True
            body.append(f"<li>{_inline_html(line[2:])}</li>")
        else:
            close_list(); body.append(f"<p>{_inline_html(line)}</p>")
    close_list()
    style = ("body{font-family:system-ui,sans-serif;max-width:48rem;margin:2rem "
             "auto;padding:0 1rem;line-height:1.5}code{background:#f0f0f0;"
             "padding:.1em .3em;border-radius:3px}h1,h2,h3{line-height:1.2}")
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{style}</style></head>\n"
        "<body>\n" + "\n".join(body) + "\n</body></html>\n"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", help="One or more SETEC audit JSON envelopes.")
    p.add_argument("--format", choices=["markdown", "html"], default="markdown")
    p.add_argument("--title", default="SETEC evidence pack")
    p.add_argument("--out", help="Write to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    envelopes, warnings = load_envelopes(args.inputs)
    if not envelopes:
        sys.stderr.write(
            "No SETEC audit envelopes found in the supplied inputs.\n")
        for w in warnings:
            sys.stderr.write(f"  - {w}\n")
        return 2

    md = render_pack(envelopes, title=args.title, load_warnings=warnings)
    if args.format == "html":
        out_text = markdown_to_html(md, title=args.title)
    else:
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        out_text = md + f"\n_Generated {stamp} by {TOOL_NAME} v{SCRIPT_VERSION}._\n"

    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        sys.stderr.write(f"Wrote evidence pack to {args.out}\n")
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
