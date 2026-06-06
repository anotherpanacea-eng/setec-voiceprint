#!/usr/bin/env python3
"""gen_calibration_readiness.py — derive the user-facing calibration/readiness
matrix from the capabilities manifest.

The manifest at `plugins/setec-voiceprint/capabilities.yaml` already carries,
per curated entry, everything needed to answer the question a new user (one
*without* the maintainer's private baseline corpora) actually has: "for each
capability, what does it need to run, what corpus do I have to bring myself,
and how far can I trust the output before I calibrate?"

This tool reads those existing fields — it does **not** add a schema field —
and renders a readiness matrix into the generated region of
`plugins/setec-voiceprint/references/calibration-readiness.md`. The narrative
prose around the generated region is hand-maintained; only the block between
the BEGIN/END markers is owned by this script.

Derivation (all from existing manifest fields):

  * **Readiness** — from `status` (heuristic → empirically_oriented →
    literature_anchored → calibrated). Maps to what the output licenses
    before the user supplies their own calibration data.
  * **Runs without your corpus?** — from `inputs.required` / `inputs.target`:
    an entry that requires a baseline / reference / manifest cannot produce a
    result until the user supplies that corpus.
  * **What you supply** — the "runway": derived from `inputs.required`,
    `inputs.optional`, `inputs.target`, and the `compute.tier` (api_llm ⇒ an
    LLM key). Baseline-size hints are scraped from `use_when` text.
  * **Packages** — `dependencies.python` (required) + `python_optional`.
  * **Hardware** — mapped from `compute.tier`.
  * **Length floor** — `compute.length_floor_words`.

Only curated entries (status != todo) are rendered; the auto-seeded research
scaffolds are excluded.

Modes (mirrors the exit-code contract of tools/check_capabilities_drift.py):

    python3 tools/gen_calibration_readiness.py            # --write (default)
    python3 tools/gen_calibration_readiness.py --check    # 0 fresh / 1 stale
    python3 tools/gen_calibration_readiness.py --stdout    # print the md block
    python3 tools/gen_calibration_readiness.py --json      # print derived data

Exit codes: 0 ok / 1 drift (--check only) / 2 internal error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "plugins" / "setec-voiceprint" / "capabilities.yaml"
DEFAULT_DOC = (
    REPO_ROOT
    / "plugins"
    / "setec-voiceprint"
    / "references"
    / "calibration-readiness.md"
)

BEGIN_MARKER = "<!-- BEGIN GENERATED: tools/gen_calibration_readiness.py — do not edit by hand -->"
END_MARKER = "<!-- END GENERATED -->"

# status → (short label, what it licenses). Ordered weakest → strongest.
STATUS_READINESS: dict[str, tuple[str, str]] = {
    "heuristic": (
        "Heuristic (uncalibrated)",
        "Shipped, not yet calibrated. Treat output as candidate-surfacing, not a score.",
    ),
    "empirically_oriented": (
        "Empirical (provisional)",
        "Runs immediately, but bands/thresholds are local-experimentation grade — "
        "PROVISIONAL until you calibrate against your own labeled corpus.",
    ),
    "literature_anchored": (
        "Literature-anchored",
        "Usable as evidence out of the box (close to a published condition); the "
        "operating point for *your* corpus is still uncalibrated.",
    ),
    "calibrated": (
        "Calibrated",
        "Ships with corpus-tested FPR/TPR at a stated operating point.",
    ),
}

# compute.tier → one-line hardware hint.
TIER_HARDWARE: dict[str, str] = {
    "core": "CPU / stdlib (+ optional spaCy model)",
    "spacy": "CPU + spaCy model",
    "surprisal": "CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk",
    "api_llm": "No local GPU; LLM API access (network + key + per-call cost)",
    "ocr": "CPU + OCR engine (Tesseract)",
    "acquisition": "CPU + network",
    "optional": "CPU (+ optional power-ups)",
}

# Tooling surfaces render in a second table (they help build/validate the
# runway rather than produce evidence about a draft).
TOOLING_SURFACES = {"validation", "setup"}

# Deterministic display order; unknown ids sort after, alphabetically.
DISPLAY_ORDER = [
    "variance_audit",
    "voice_distance",
    "idiolect_detector",
    "aic_pattern_audit",
    "restoration_packet",
    "binoculars_audit",
    "narrative_decision_audit",
    "validation_harness",
    "manifest_validator",
    "dependency_check",
]


def _load_yaml():
    """Lazy PyYAML import (see capabilities.py / check_capabilities_drift.py)."""
    try:
        import yaml  # type: ignore

        return yaml
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ImportError(
            "gen_calibration_readiness requires PyYAML to parse the manifest "
            "(`pip install pyyaml`)"
        ) from exc


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    yaml = _load_yaml()
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _friendly_input(raw: str) -> str:
    """Map a manifest input string to a user-facing 'what you supply' label."""
    low = raw.lower()
    if "baseline" in low and ("reference-manifest" in low or "reference manifest" in low):
        return "register-matched personal baseline (or a reference manifest)"
    if "baseline" in low:
        return "register-matched personal baseline corpus"
    if "reference-manifest" in low or "reference manifest" in low:
        return "reference corpus manifest"
    if "judge-manifest" in low or "judge manifest" in low:
        return "pre-computed judge feature manifest"
    if "diagnostic" in low:
        return "diagnostic JSON from a prior Surface 1/2 run"
    if "manifest" in low:
        return "labeled corpus + valid `corpus_manifest.jsonl`"
    # strip leading CLI flag noise and trailing parentheticals for prose use
    cleaned = re.sub(r"^-+\S+\s*", "", raw).strip()
    return cleaned or raw.strip()


def _baseline_size_hint(use_when: list[str]) -> str | None:
    """Scrape a baseline word-count target from use_when text, if present."""
    best = None
    for item in use_when:
        low = item.lower()
        if "baseline" not in low and "prior prose" not in low and "prior work" not in low:
            continue
        for m in re.finditer(r"(≥\s*)?([0-9][0-9,]*)\s*(k)?\s*words", low):
            num = m.group(2).replace(",", "")
            k = m.group(3)
            words = int(num) * (1000 if k else 1)
            label = f"≥{num}K words" if k else f"≥{num} words"
            if best is None or words > best[0]:
                best = (words, label)
    return best[1] if best else None


def derive(entry: dict[str, Any]) -> dict[str, Any]:
    """Derive the readiness row for one curated manifest entry."""
    eid = entry["id"]
    status = entry.get("status", "")
    surface = entry.get("surface", "")
    compute = entry.get("compute") or {}
    tier = compute.get("tier", "core")
    length_floor = compute.get("length_floor_words")
    deps = entry.get("dependencies") or {}
    req_pkgs = deps.get("python") or []
    opt_pkgs = deps.get("python_optional") or []
    inputs = entry.get("inputs") or {}
    required = inputs.get("required") or []
    optional = inputs.get("optional") or []
    target = inputs.get("target") or ""
    use_when = entry.get("use_when") or []

    # Does the entry require a user-supplied corpus to produce any result?
    corpus_text = (" ".join(required) + " " + target).lower()
    needs_corpus = any(
        kw in corpus_text
        for kw in ("baseline", "manifest", "reference", "labeled corpus")
    )

    # What the user supplies (the runway).
    supplies: list[str] = []
    size_hint = _baseline_size_hint(use_when)
    for raw in required:
        label = _friendly_input(raw)
        if "baseline" in label and size_hint:
            label += f" ({size_hint})"
        supplies.append(f"{label} (required)")
    # Target-as-corpus cases (no explicit `required` list, requirement is the target).
    if not required and needs_corpus:
        if "corpus manifest" in target.lower() or "corpus_manifest" in target.lower():
            supplies.append("labeled human/AI corpus + `corpus_manifest.jsonl` (required)")
        elif "manifest" in target.lower():
            supplies.append("a `corpus_manifest.jsonl` to validate (required)")
    for raw in optional:
        label = _friendly_input(raw)
        if "baseline" in label and size_hint:
            label += f" ({size_hint})"
        supplies.append(f"{label} (optional)")
    # Diagnostic-JSON workflow inputs (restoration_packet).
    if not supplies and "diagnostic" in target.lower():
        supplies.append("diagnostic JSON from prior Surface 1/2 runs (required)")
    # api_llm tier always implies an LLM key.
    if tier == "api_llm":
        supplies.append("LLM API access (key + per-call cost) (required)")
    # Env-introspection tooling.
    if not supplies and target.lower().startswith("none"):
        supplies.append("nothing (introspects your local environment)")
    if not supplies:
        supplies.append(
            "nothing required to run; add a baseline / labeled corpus to calibrate"
        )

    # Packages cell.
    pkg_parts: list[str] = []
    pkg_parts.append("req: " + ", ".join(req_pkgs) if req_pkgs else "stdlib")
    if opt_pkgs:
        pkg_parts.append("opt: " + ", ".join(opt_pkgs))
    packages = "; ".join(pkg_parts)

    readiness_label = STATUS_READINESS.get(status, (status or "—", ""))[0]

    return {
        "id": eid,
        "status": status,
        "surface": surface,
        "is_tooling": surface in TOOLING_SURFACES,
        "readiness": readiness_label,
        "runs_without_corpus": not needs_corpus,
        "supplies": supplies,
        "packages": packages,
        "hardware": TIER_HARDWARE.get(tier, tier),
        "length_floor": length_floor,
    }


def _order_key(row: dict[str, Any]) -> tuple[int, str]:
    eid = row["id"]
    idx = DISPLAY_ORDER.index(eid) if eid in DISPLAY_ORDER else len(DISPLAY_ORDER)
    return (idx, eid)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _render_table(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Capability | Readiness | Runs without your corpus? | "
        "What you supply | Packages | Hardware | Length floor |\n"
        "|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for r in rows:
        floor = str(r["length_floor"]) if r["length_floor"] is not None else "—"
        runs = "Yes" if r["runs_without_corpus"] else "No"
        supplies = "; ".join(r["supplies"])
        lines.append(
            "| "
            + " | ".join(
                _md_cell(c)
                for c in (
                    f"`{r['id']}`",
                    r["readiness"],
                    runs,
                    supplies,
                    r["packages"],
                    r["hardware"],
                    floor,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def render_block(manifest: dict[str, Any]) -> str:
    rows = [
        derive(e)
        for e in manifest.get("entries", [])
        if e.get("status") != "todo"
    ]
    rows.sort(key=_order_key)
    evidence = [r for r in rows if not r["is_tooling"]]
    tooling = [r for r in rows if r["is_tooling"]]

    parts: list[str] = []
    parts.append(
        f"_Generated from `capabilities.yaml` (schema "
        f"{manifest.get('schema_version', '?')}) by "
        f"`tools/gen_calibration_readiness.py`. Do not edit this region by hand._"
    )
    parts.append("")
    parts.append("### Evidence surfaces (run on a draft)")
    parts.append("")
    parts.append(_render_table(evidence))
    parts.append("")
    parts.append("### Runway & calibration tooling")
    parts.append("")
    parts.append(_render_table(tooling))
    parts.append("")
    parts.append("**Readiness legend.**")
    for status in ("heuristic", "empirically_oriented", "literature_anchored", "calibrated"):
        label, licenses = STATUS_READINESS[status]
        parts.append(f"- **{label}** — {licenses}")
    return "\n".join(parts)


def replace_region(doc_text: str, block: str) -> str:
    if BEGIN_MARKER not in doc_text or END_MARKER not in doc_text:
        raise ValueError(
            f"doc is missing the generated-region markers "
            f"({BEGIN_MARKER!r} / {END_MARKER!r})"
        )
    pre = doc_text.split(BEGIN_MARKER, 1)[0]
    post = doc_text.split(END_MARKER, 1)[1]
    return f"{pre}{BEGIN_MARKER}\n\n{block}\n\n{END_MARKER}{post}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    ap.add_argument("--check", action="store_true", help="exit 1 if the doc is stale")
    ap.add_argument("--stdout", action="store_true", help="print the generated block")
    ap.add_argument("--json", action="store_true", help="print derived rows as JSON")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not load manifest: {exc}", file=sys.stderr)
        return 2

    if args.json:
        rows = [
            derive(e)
            for e in manifest.get("entries", [])
            if e.get("status") != "todo"
        ]
        rows.sort(key=_order_key)
        print(json.dumps(rows, indent=2))
        return 0

    block = render_block(manifest)

    if args.stdout:
        print(block)
        return 0

    try:
        doc_text = args.doc.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: doc not found: {args.doc}", file=sys.stderr)
        return 2

    try:
        updated = replace_region(doc_text, block)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if updated != doc_text:
            print(
                "calibration-readiness.md is STALE — run "
                "`python3 tools/gen_calibration_readiness.py` to regenerate.",
                file=sys.stderr,
            )
            return 1
        print("calibration-readiness.md is up to date.")
        return 0

    if updated != doc_text:
        args.doc.write_text(updated, encoding="utf-8")
        print(f"updated {args.doc.relative_to(REPO_ROOT)}")
    else:
        print(f"{args.doc.relative_to(REPO_ROOT)} already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
