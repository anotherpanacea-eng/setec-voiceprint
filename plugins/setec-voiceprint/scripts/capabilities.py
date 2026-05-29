#!/usr/bin/env python3
"""capabilities.py — query SETEC's capabilities manifest.

The manifest at `plugins/setec-voiceprint/capabilities.yaml` is the
single source of truth for what every user-facing script does, when
to use it, when not to use it, and what compute it needs. This CLI
queries the manifest from the command line and from the `/setec`
skill.

Subcommands
-----------

  list      — list entries, with filters
  show ID   — print one entry in full
  recommend — given a free-text situation, return matching audits

Filters supported by `list`:

  --surface NAME            — task surface
  --family NAME             — finer family inside the surface
  --status NAME             — calibration status
  --tier NAME               — compute tier (core / spacy / surprisal / api_llm / ocr / acquisition / optional)
  --register NAME           — register match
  --length-floor N          — show only entries whose length floor is ≤ N
  --available               — show only entries whose deps are installed
  --include-todo            — include entries that are still TODO (default: hide)
  --format table|json|md    — output format

The manifest is read with `yaml.safe_load`. Fields are documented
inline in the manifest's header comments.

Usage examples
--------------

    # everything
    python3 capabilities.py list

    # what's runnable on stdlib alone
    python3 capabilities.py list --tier core

    # what's available given installed deps
    python3 capabilities.py list --available

    # one entry, in full
    python3 capabilities.py show variance_audit

    # recommend a pipeline for a situation
    python3 capabilities.py recommend \\
        --situation "I have a 5000-word short story and I want to know if it was AI-edited"

    # machine-readable
    python3 capabilities.py list --format json --available > my_kit.json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PLUGIN_ROOT / "capabilities.yaml"


def _load_yaml():
    """Lazy PyYAML import. Module-load doesn't require yaml so test
    harnesses without PyYAML can still import this for unit
    testing of the non-loader functions; only callers that actually
    parse the manifest see the dep error."""
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise ImportError(
            "capabilities.py requires PyYAML to parse the manifest "
            "(`pip install pyyaml`); the parse-free helpers can run "
            "without it."
        ) from exc


# ---------- loading -------------------------------------------------

def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"capabilities manifest missing at {path}; "
            f"run `python3 tools/seed_capabilities.py --out {path}` "
            f"to bootstrap"
        )
    yaml = _load_yaml()
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(
            f"{path}: manifest missing top-level `entries` key"
        )
    return data


def entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return list(manifest.get("entries") or [])


# ---------- availability check -------------------------------------

def is_installed(module_name: str) -> bool:
    """Lightweight check: can importlib find a spec for the module?

    Doesn't actually import (which would side-effect-load model
    weights for sentence-transformers etc.).
    """
    name = module_name.replace("-", "_")
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def entry_available(
    entry: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Return ``(available, missing_required, missing_optional)``.

    *Available* means every dep in ``dependencies.python`` (required)
    resolves via importlib. The audit may not exercise every feature
    (some Tier 2/3 paths may degrade) but its primary use case runs.

    Optional deps live in ``dependencies.python_optional`` —
    graceful-degradation imports the script falls back from
    (sentence_transformers → TF-IDF, textstat → stdlib FKGL
    approximation, NLTK → regex tokenization, etc.). Missing
    optional deps do NOT block availability but are reported so
    operators see what they're missing.

    ``dependencies.sdks_optional`` (third-party SDKs like anthropic,
    openai, google-genai) is informational only; see entries that
    use those (e.g., narrative_decision_audit) for the per-audit
    discipline.
    """
    deps_block = entry.get("dependencies") or {}
    required = deps_block.get("python") or []
    optional = deps_block.get("python_optional") or []
    missing_required = [d for d in required if not is_installed(d)]
    missing_optional = [d for d in optional if not is_installed(d)]
    return (len(missing_required) == 0, missing_required, missing_optional)


# ---------- filtering ----------------------------------------------

def filter_entries(
    entries_list: list[dict[str, Any]],
    *,
    surface: str | None = None,
    family: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    register: str | None = None,
    length_floor_max: int | None = None,
    available_only: bool = False,
    include_todo: bool = False,
    handoff: str | None = None,
    consumer: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in entries_list:
        s = e.get("status")
        if not include_todo and s == "todo":
            continue
        if surface is not None and e.get("surface") != surface:
            continue
        if family is not None and e.get("family") != family:
            continue
        if status is not None and s != status:
            continue
        if handoff is not None and e.get("handoff") != handoff:
            continue
        if consumer is not None:
            consumers = e.get("consumers") or []
            if consumer not in consumers:
                continue
        if tier is not None and (
            (e.get("compute") or {}).get("tier") != tier
        ):
            continue
        if register is not None and register not in (
            e.get("registers") or []
        ):
            continue
        if length_floor_max is not None:
            lf = (e.get("compute") or {}).get("length_floor_words")
            if lf is not None and lf > length_floor_max:
                continue
        if available_only:
            ok, _, _ = entry_available(e)
            if not ok:
                continue
        out.append(e)
    return out


# ---------- output formats -----------------------------------------

def render_table(entries_list: list[dict[str, Any]]) -> str:
    if not entries_list:
        return "(no entries match)\n"
    rows = []
    for e in entries_list:
        rows.append((
            e.get("id") or "",
            e.get("surface") or "",
            e.get("family") or "",
            e.get("status") or "",
            (e.get("compute") or {}).get("tier") or "",
        ))
    cols = ["id", "surface", "family", "status", "tier"]
    widths = [
        max(len(c), *(len(r[i]) for r in rows))
        for i, c in enumerate(cols)
    ]
    sep = "  "
    out = [
        sep.join(c.ljust(w) for c, w in zip(cols, widths)),
        sep.join("-" * w for w in widths),
    ]
    for r in rows:
        out.append(sep.join(c.ljust(w) for c, w in zip(r, widths)))
    return "\n".join(out) + "\n"


def render_markdown(entries_list: list[dict[str, Any]]) -> str:
    if not entries_list:
        return "_(no entries match)_\n"
    lines = [
        "| id | surface | family | status | tier | length floor |",
        "|---|---|---|---|---|---:|",
    ]
    for e in entries_list:
        compute = e.get("compute") or {}
        lf = compute.get("length_floor_words")
        lf_s = str(lf) if lf is not None else "—"
        lines.append(
            "| `{id}` | `{surface}` | {family} | {status} | {tier} | {lf} |".format(
                id=e.get("id") or "",
                surface=e.get("surface") or "",
                family=e.get("family") or "",
                status=e.get("status") or "",
                tier=compute.get("tier") or "",
                lf=lf_s,
            )
        )
    return "\n".join(lines) + "\n"


def render_json(entries_list: list[dict[str, Any]]) -> str:
    return json.dumps(entries_list, indent=2, default=str) + "\n"


def render_show(entry: dict[str, Any]) -> str:
    parts = [f"# {entry.get('id')}"]
    parts.append("")
    parts.append(f"- **surface:** `{entry.get('surface')}`")
    parts.append(f"- **family:** {entry.get('family')}")
    parts.append(f"- **status:** {entry.get('status')}")
    handoff = entry.get("handoff") or "none"
    parts.append(f"- **handoff posture:** {handoff}")
    consumers = entry.get("consumers") or []
    if consumers:
        parts.append(
            f"- **named consumers:** {', '.join(consumers)}"
        )
    compute = entry.get("compute") or {}
    parts.append(f"- **compute tier:** {compute.get('tier')}")
    if compute.get("cost_note"):
        parts.append(f"- **cost:** {compute.get('cost_note')}")
    if compute.get("length_floor_words") is not None:
        parts.append(
            f"- **length floor:** "
            f"{compute['length_floor_words']:,} words"
        )
    parts.append(f"- **script:** `{entry.get('script_path')}`")
    ok, missing_req, missing_opt = entry_available(entry)
    parts.append(f"- **available locally:** {ok}")
    if missing_req:
        parts.append(f"  - **missing required deps:** {missing_req}")
    if missing_opt:
        parts.append(
            f"  - **missing optional deps (graceful degradation):** "
            f"{missing_opt}"
        )
    parts.append("")
    purpose = entry.get("purpose") or ""
    parts.append("## Purpose")
    parts.append("")
    parts.append(purpose.strip())
    parts.append("")
    if entry.get("use_when"):
        parts.append("## Use when")
        parts.append("")
        for u in entry["use_when"]:
            parts.append(f"- {u}")
        parts.append("")
    if entry.get("do_not_use_when"):
        parts.append("## Do not use when")
        parts.append("")
        for d in entry["do_not_use_when"]:
            parts.append(f"- {d}")
        parts.append("")
    if entry.get("examples"):
        parts.append("## Examples")
        parts.append("")
        for ex in entry["examples"]:
            parts.append(f"**{ex.get('description', '')}**")
            parts.append("")
            parts.append("```bash")
            parts.append(ex.get("cmd", "").strip())
            parts.append("```")
            parts.append("")
    if entry.get("references"):
        parts.append("## References")
        parts.append("")
        for r in entry["references"]:
            parts.append(f"- {r}")
        parts.append("")
    return "\n".join(parts)


# ---------- recommend ----------------------------------------------

# Simple keyword router. Maps a situation phrase to a list of
# candidate entry ids in recommended order. The recommend subcommand
# also does a fallback keyword-match against each entry's purpose +
# use_when text, but the curated routes here are higher-confidence.

CURATED_ROUTES: list[tuple[list[str], list[str]]] = [
    (
        [
            "essay", "op-ed", "opinion", "blog post", "blog",
            "short essay", "personal essay",
        ],
        [
            "variance_audit", "aic_pattern_audit",
            "binoculars_audit", "validation_harness",
        ],
    ),
    (
        [
            "short story", "novella", "novel", "fiction",
            "literary fiction", "5000 words",
        ],
        [
            "variance_audit", "voice_distance",
            "narrative_decision_audit", "aic_pattern_audit",
            "binoculars_audit",
        ],
    ),
    (
        [
            "revision", "editing", "draft", "rewrite",
            "preserve voice", "voice preservation",
        ],
        [
            "voice_distance", "idiolect_detector",
            "restoration_packet", "aic_pattern_audit",
        ],
    ),
    (
        [
            "calibration", "calibrate", "labeled corpus",
            "validation corpus", "threshold",
        ],
        [
            "manifest_validator", "validation_harness",
        ],
    ),
    (
        [
            "first run", "install", "set up", "setup",
            "dependencies", "ImportError",
        ],
        [
            "dependency_check",
        ],
    ),
    (
        [
            "narrative", "plot", "discourse", "story structure",
            "thematic",
        ],
        [
            "narrative_decision_audit",
        ],
    ),
    (
        [
            "ESL", "non-native", "second language", "TOEFL",
        ],
        [
            "variance_audit",
        ],
    ),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def recommend(
    situation: str,
    *,
    manifest: dict[str, Any],
    available_only: bool = False,
) -> list[tuple[str, dict[str, Any], list[str]]]:
    """Return a list of (id, entry, matched_keywords) tuples ranked
    by curated-route match strength, then by free-text match against
    use_when text."""
    normalized = _normalize(situation)
    all_entries = {e.get("id"): e for e in entries(manifest)}
    matched: dict[str, list[str]] = {}

    # Curated routes first.
    for keywords, ids in CURATED_ROUTES:
        hits = [k for k in keywords if k in normalized]
        if not hits:
            continue
        for entry_id in ids:
            if entry_id not in all_entries:
                continue
            matched.setdefault(entry_id, []).extend(hits)

    # Free-text fallback: per-entry use_when keyword presence.
    for entry_id, entry in all_entries.items():
        if entry.get("status") == "todo":
            continue
        use_when = " ".join(entry.get("use_when") or [])
        purpose = entry.get("purpose") or ""
        haystack = _normalize(use_when + " " + purpose)
        # Pick out tokens of length ≥ 5 from the situation
        tokens = re.findall(r"\b[a-z]{5,}\b", normalized)
        hits = [t for t in tokens if t in haystack]
        if hits:
            matched.setdefault(entry_id, []).extend(hits)

    # Rank and assemble.
    results = []
    for entry_id, keywords in matched.items():
        entry = all_entries[entry_id]
        if entry.get("status") == "todo":
            continue
        if available_only:
            ok, _, _ = entry_available(entry)
            if not ok:
                continue
        score = len(keywords)
        results.append((score, entry_id, entry, sorted(set(keywords))))
    results.sort(key=lambda r: (-r[0], r[1]))
    return [(eid, e, kw) for _, eid, e, kw in results]


def render_recommend(
    results: list[tuple[str, dict[str, Any], list[str]]],
    situation: str,
) -> str:
    if not results:
        return (
            f"# Recommendation\n\n"
            f"**Situation:** {situation}\n\n"
            "No audit clearly matches. Try `capabilities.py list` "
            "to browse what's available, or rephrase the situation."
            "\n"
        )
    lines = [f"# Recommendation", "", f"**Situation:** {situation}", ""]
    lines.append(
        f"Found {len(results)} matching audit"
        f"{'s' if len(results) != 1 else ''}, ordered by relevance:"
    )
    lines.append("")
    for i, (entry_id, entry, keywords) in enumerate(results, 1):
        compute = entry.get("compute") or {}
        ok, missing_req, missing_opt = entry_available(entry)
        if ok and not missing_opt:
            avail = "✔ available"
        elif ok:
            avail = (
                f"✔ available (optional deps absent: "
                f"{', '.join(missing_opt)})"
            )
        else:
            avail = (
                f"⚠ missing required: {', '.join(missing_req)}"
            )
        lines.append(f"## {i}. `{entry_id}` — {avail}")
        lines.append("")
        purpose = (entry.get("purpose") or "").strip()
        if purpose:
            lines.append(purpose)
            lines.append("")
        lines.append(
            f"- **surface:** `{entry.get('surface')}` | "
            f"**tier:** {compute.get('tier')} | "
            f"**status:** {entry.get('status')}"
        )
        if compute.get("cost_note"):
            lines.append(f"- **cost:** {compute.get('cost_note')}")
        if entry.get("do_not_use_when"):
            lines.append("- **do NOT use when:**")
            for d in entry["do_not_use_when"]:
                lines.append(f"    - {d}")
        if entry.get("examples"):
            lines.append("- **example:**")
            ex = entry["examples"][0]
            lines.append("    ```bash")
            lines.append("    " + ex.get("cmd", "").strip())
            lines.append("    ```")
        lines.append("")
        lines.append(
            f"_(matched keywords: {', '.join(keywords)})_"
        )
        lines.append("")
    return "\n".join(lines)


# ---------- CLI ----------------------------------------------------

def cmd_list(args) -> int:
    manifest = load_manifest(args.manifest)
    filtered = filter_entries(
        entries(manifest),
        surface=args.surface,
        family=args.family,
        status=args.status,
        tier=args.tier,
        register=args.register,
        length_floor_max=args.length_floor_max,
        available_only=args.available,
        include_todo=args.include_todo,
        handoff=args.handoff,
        consumer=args.consumer,
    )
    fmt = args.format
    if fmt == "table":
        print(render_table(filtered), end="")
    elif fmt == "md":
        print(render_markdown(filtered), end="")
    elif fmt == "json":
        print(render_json(filtered), end="")
    elif fmt == "ids":
        for e in filtered:
            print(e.get("id"))
    else:
        raise ValueError(f"unknown format {fmt!r}")
    return 0


def cmd_show(args) -> int:
    manifest = load_manifest(args.manifest)
    for e in entries(manifest):
        if e.get("id") == args.id:
            if args.format == "json":
                print(json.dumps(e, indent=2, default=str))
            else:
                print(render_show(e))
            return 0
    print(f"error: no entry {args.id!r}", file=sys.stderr)
    return 1


def cmd_recommend(args) -> int:
    manifest = load_manifest(args.manifest)
    results = recommend(
        args.situation,
        manifest=manifest,
        available_only=args.available,
    )
    if args.format == "json":
        out = []
        for entry_id, entry, keywords in results:
            out.append({
                "id": entry_id,
                "matched_keywords": keywords,
                "entry": entry,
            })
        print(json.dumps(out, indent=2, default=str))
    else:
        print(render_recommend(results, args.situation))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Query the SETEC capabilities manifest.",
    )
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST_PATH,
        help=f"Manifest path (default {MANIFEST_PATH}).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List entries (with filters)")
    p_list.add_argument("--surface", default=None)
    p_list.add_argument("--family", default=None)
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--tier", default=None)
    p_list.add_argument("--register", default=None)
    p_list.add_argument(
        "--length-floor-max", type=int, default=None,
        help="Hide entries whose length floor exceeds N words.",
    )
    p_list.add_argument(
        "--available", action="store_true",
        help="Only entries whose deps are installed.",
    )
    p_list.add_argument(
        "--include-todo", action="store_true",
        help="Include entries that are still TODO.",
    )
    p_list.add_argument(
        "--handoff", default=None,
        choices=("stable", "experimental", "internal", "none"),
        help=(
            "Filter by downstream-handoff posture. `stable` = "
            "pin against; `experimental` = consumer surface but "
            "contract may evolve; `internal` = operator-side, not "
            "for consumers; `none` = not a consumer surface."
        ),
    )
    p_list.add_argument(
        "--consumer", default=None,
        help=(
            "Filter to entries explicitly named in their "
            "`consumers` list (e.g., `apodictic`, `ultrareview`). "
            "Free-form: any consumer name in the manifest matches."
        ),
    )
    p_list.add_argument(
        "--format",
        choices=("table", "json", "md", "ids"),
        default="table",
    )
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Print one entry in full")
    p_show.add_argument("id")
    p_show.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
    )
    p_show.set_defaults(func=cmd_show)

    p_rec = sub.add_parser(
        "recommend",
        help="Recommend audits given a free-text situation",
    )
    p_rec.add_argument("--situation", required=True)
    p_rec.add_argument(
        "--available", action="store_true",
        help="Only recommend audits with installed deps.",
    )
    p_rec.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
    )
    p_rec.set_defaults(func=cmd_recommend)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
