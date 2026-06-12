#!/usr/bin/env python3
"""seed_capabilities.py — one-shot bootstrap for capabilities.yaml.

Walks every Python file under `plugins/setec-voiceprint/scripts/`,
picks out those that declare a `TASK_SURFACE = "..."` module-level
constant (the convention every user-facing script in the framework
follows), and emits a draft `capabilities.yaml` entry for each.

Auto-extracted fields:

  * id — taken from `TOOL_NAME = "..."` if present, else from the
    script's filename stem
  * script_path — repo-relative path to the script
  * surface — the `TASK_SURFACE` value
  * status — set to `todo` for every seeded entry so the linter
    can distinguish auto-seeded from hand-curated
  * purpose — first paragraph of the module docstring, lightly cleaned
  * compute.tier — heuristic from imports (api_llm > surprisal > ocr
    > acquisition > calibration > optional > core)
  * dependencies.python — third-party packages the script imports
  * _seeded_at — ISO date

Hand-curated fields left as TODO sentinels for the operator to fill:

  * family, use_when, do_not_use_when, inputs, outputs, registers,
    examples, references, cost_note, length_floor_words

After seeding, operators promote `status: todo` → `status:
<calibration-status>` by filling in the hand-curated fields. The
drift linter rejects PRs that touch a TODO-status script without
promoting its entry.

Usage:

    python3 tools/seed_capabilities.py \\
        --out plugins/setec-voiceprint/capabilities.yaml
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from r1_bundle import validate_r1_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"

SKIP_FILE_PATTERNS = [
    re.compile(r"^test_"),
    re.compile(r"_test\.py$"),
    re.compile(r"^__init__\.py$"),
]


# Tier inference rules. Order matters: first match wins.
TIER_RULES: list[tuple[str, list[str]]] = [
    ("api_llm", ["anthropic", "openai", "google.genai", "google_genai"]),
    ("surprisal", ["transformers", "surprisal_backend", "tokenizers"]),
    ("ocr", ["pypdf", "ocrmypdf", "fitz", "pdfplumber"]),
    ("acquisition", ["requests", "bs4", "beautifulsoup4"]),
    ("calibration", ["huggingface_hub", "pyarrow"]),
    ("optional", ["sentence_transformers"]),
    # "core" is the default for everything else (spacy, scipy,
    # scikit-learn, statsmodels, xgboost, etc. — all in
    # requirements*.txt by default)
]


@dataclass
class Seed:
    id: str
    script_path: str
    surface: str
    purpose: str
    tier: str
    deps: list[str] = field(default_factory=list)
    has_main: bool = False


def find_scripts() -> list[Path]:
    out: list[Path] = []
    for path in SCRIPTS_ROOT.rglob("*.py"):
        name = path.name
        if any(p.search(name) for p in SKIP_FILE_PATTERNS):
            continue
        if "/tests/" in str(path) or "/__pycache__/" in str(path):
            continue
        out.append(path)
    return sorted(out)


def parse_module(path: Path) -> Seed | None:
    """Return None if the file has no TASK_SURFACE constant."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    surface: str | None = None
    tool_name: str | None = None
    has_main = False
    imports: set[str] = set()

    for node in tree.body:
        # Constants: TASK_SURFACE = "..." / TOOL_NAME = "..."
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    if tgt.id == "TASK_SURFACE":
                        surface = node.value.value
                    elif tgt.id == "TOOL_NAME":
                        tool_name = node.value.value
        # __main__ block
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            has_main = True

    # Walk the whole tree for imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
            # Handle `from google import genai`
            if node.module == "google":
                for alias in node.names:
                    if alias.name == "genai":
                        imports.add("google.genai")

    if surface is None:
        return None

    purpose = _extract_purpose(tree, source)
    tier = _infer_tier(imports)
    deps = sorted(_external_deps(imports))

    # If TOOL_NAME contains a path separator, the script is using a
    # path-style identifier (e.g., "scripts/replication/foo.py").
    # Fall back to the filename stem so the manifest id is a plain
    # identifier.
    if tool_name and ("/" in tool_name or "\\" in tool_name):
        tool_name = Path(tool_name).stem
    return Seed(
        id=tool_name or path.stem,
        script_path=str(path.relative_to(REPO_ROOT)),
        surface=surface,
        purpose=purpose,
        tier=tier,
        deps=deps,
        has_main=has_main,
    )


def _extract_purpose(tree: ast.Module, source: str) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        # Try the first comment block above the imports
        m = re.search(r'^"""(.+?)"""', source, re.DOTALL | re.MULTILINE)
        if m:
            doc = m.group(1).strip()
    if not doc:
        return "(no module docstring)"
    # First paragraph; strip common patterns
    first = doc.split("\n\n")[0].strip()
    # If it's filename — purpose, drop the filename
    first = re.sub(r"^[\w_]+\.py\s+[—-]\s+", "", first)
    first = re.sub(r"\s+", " ", first)
    if len(first) > 300:
        first = first[:297] + "..."
    return first


def _infer_tier(imports: set[str]) -> str:
    for tier, signals in TIER_RULES:
        for sig in signals:
            if sig in imports:
                return tier
    return "core"


# Stdlib + repo-local modules that don't count as external deps.
STDLIB_MODULES = {
    "abc", "argparse", "ast", "base64", "collections", "contextlib",
    "copy", "csv", "dataclasses", "datetime", "decimal", "enum",
    "functools", "glob", "gzip", "hashlib", "io", "itertools", "json",
    "logging", "math", "multiprocessing", "operator", "os", "pathlib",
    "pickle", "platform", "pprint", "queue", "random", "re", "secrets",
    "shlex", "shutil", "signal", "socket", "sqlite3", "statistics",
    "string", "struct", "subprocess", "sys", "tempfile", "textwrap",
    "threading", "time", "tomllib", "traceback", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref",
    "xml", "zipfile", "zlib",
    "__future__",
}


def _is_repo_local(name: str) -> bool:
    """Return True if `name` is a sibling module in scripts/."""
    candidates = [
        SCRIPTS_ROOT / f"{name}.py",
        SCRIPTS_ROOT / "calibration" / f"{name}.py",
        SCRIPTS_ROOT / "replication" / f"{name}.py",
        SCRIPTS_ROOT / "replication" / "stages" / f"{name}.py",
        SCRIPTS_ROOT / "external_mirror" / f"{name}.py",
    ]
    return any(p.exists() for p in candidates)


def _external_deps(imports: set[str]) -> set[str]:
    out: set[str] = set()
    for name in imports:
        top = name.split(".")[0]
        if top in STDLIB_MODULES:
            continue
        if _is_repo_local(top):
            continue
        out.add(top)
    return out


# ---------- R1 field-bundle validation ----------------------------


# ---------- YAML rendering ----------------------------------------

def _yaml_escape(value: str) -> str:
    """Conservative YAML scalar escaping. Always quotes; never ambiguous."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_yaml(seeds: list[Seed]) -> str:
    today = _dt.date.today().isoformat()
    out: list[str] = []
    out.append("# SETEC Voiceprint capabilities manifest")
    out.append("# Single source of truth for every user-facing script in")
    out.append("# the framework. Hand-edited; do not let it drift from the")
    out.append("# linter at tools/check_capabilities_drift.py.")
    out.append("#")
    out.append("# Status vocabulary:")
    out.append("#   - todo: auto-seeded; hand-curated fields missing")
    out.append("#   - heuristic: shipped, not yet calibrated")
    out.append("#   - empirically_oriented: local experimentation")
    out.append("#   - literature_anchored: peer-reviewed anchor")
    out.append("#   - calibrated: corpus-tested with FPR/TPR metrics")
    out.append("#   - structural_only: feeds downstream signals, not user-facing")
    out.append("#")
    out.append("# dependencies vocabulary (v0.2.0):")
    out.append("#   - python:          required Python packages. Their absence means")
    out.append("#                      the audit's PRIMARY use case won't run. The")
    out.append("#                      --available filter gates on this list.")
    out.append("#   - python_optional: graceful-degradation packages. Their absence")
    out.append("#                      means a non-primary feature falls back. The")
    out.append("#                      seeder cannot tell which auto-extracted deps")
    out.append("#                      have fallbacks (the script's try/except guards")
    out.append("#                      live behind ast.walk), so EVERY auto-extracted")
    out.append("#                      dep lands in `python` (the strict default).")
    out.append("#                      During hand-curation, demote any dep whose")
    out.append("#                      absence the script handles gracefully.")
    out.append("#   - sdks_optional:   third-party API SDKs (anthropic, openai,")
    out.append("#                      google-genai). Informational only.")
    out.append("#")
    out.append("# handoff posture vocabulary (v0.3.0):")
    out.append("#   - stable:       pin against this. SETEC's schema_version +")
    out.append("#                   semver discipline the contract. Breaking changes")
    out.append("#                   bump to 2.0.0.")
    out.append("#   - experimental: designed as a consumer surface but contract")
    out.append("#                   may evolve before 2.0.0. Consumers welcome to")
    out.append("#                   pin, with the understanding that the envelope")
    out.append("#                   shape may shift.")
    out.append("#   - internal:     emits the standard envelope but is operator-side")
    out.append("#                   tooling (dependency_check, manifest_validator);")
    out.append("#                   downstream consumers shouldn't depend on it.")
    out.append("#   - none:         not a consumer surface (research scaffolds,")
    out.append("#                   helper modules, replication stages).")
    out.append("#")
    out.append("# consumers (v0.3.0):")
    out.append("#   Free-list of named downstream integrations that pin against")
    out.append("#   this entry. Known values today: `apodictic`, `ultrareview`,")
    out.append("#   `external_integrations`. New consumers can be added without")
    out.append("#   schema change.")
    out.append("#")
    out.append(f"# Schema version: 0.3.0  (seeded {today})")
    out.append("schema_version: \"0.3.0\"")
    out.append("entries:")
    for seed in sorted(seeds, key=lambda s: s.id):
        out.append(f"  - id: {seed.id}")
        out.append(f"    script_path: {seed.script_path}")
        out.append(f"    surface: {seed.surface}")
        out.append("    status: todo")
        out.append("    family: TODO")
        out.append("    purpose: " + _yaml_escape(seed.purpose))
        out.append("    use_when:")
        out.append("      - TODO")
        out.append("    do_not_use_when:")
        out.append("      - TODO")
        out.append("    inputs:")
        out.append("      target: TODO")
        out.append("    outputs:")
        out.append("      schema_version: \"1.0\"")
        out.append("      artifacts:")
        out.append("        - TODO")
        out.append("    compute:")
        out.append(f"      tier: {seed.tier}")
        out.append("      cost_note: TODO")
        out.append("      length_floor_words: null")
        out.append("    registers:")
        out.append("      - TODO")
        out.append("    dependencies:")
        if seed.deps:
            out.append("      python:")
            for dep in seed.deps:
                out.append(f"        - {dep}")
        else:
            out.append("      python: []")
        # v0.2.0: emit python_optional as an empty list so the schema
        # shape is consistent across seeded and hand-curated entries.
        # Operators populate it during curation by demoting any
        # auto-extracted dep whose absence the script handles
        # gracefully (try/except ImportError → HAS_X flag → fallback).
        out.append("      python_optional: []")
        # v0.3.0: handoff defaults to `none` for auto-seeded entries.
        # Operators promote during curation by setting `stable` /
        # `experimental` / `internal` and populating `consumers` with
        # the named downstream integrations that pin against the entry.
        out.append("    handoff: none")
        out.append("    consumers: []")
        out.append("    examples: []")
        out.append("    references: []")
        out.append(f"    _seeded_at: \"{today}\"")
        out.append("")
    return "\n".join(out) + "\n"


# ---------- main ----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap plugins/setec-voiceprint/capabilities.yaml from TASK_SURFACE-bearing scripts.",
    )
    parser.add_argument(
        "--out", type=Path, required=True,
        help="Output YAML path.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing file (default: refuse).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print seed count and exit without writing.",
    )
    args = parser.parse_args(argv)

    scripts = find_scripts()
    seeds: list[Seed] = []
    for path in scripts:
        s = parse_module(path)
        if s is not None:
            seeds.append(s)

    print(
        f"Scanned {len(scripts)} files; {len(seeds)} carry "
        f"TASK_SURFACE → seed entries.",
        file=sys.stderr,
    )

    if args.dry_run:
        for s in sorted(seeds, key=lambda s: s.id):
            print(f"  {s.id:50s} surface={s.surface:30s} tier={s.tier}")
        return 0

    if args.out.exists() and not args.overwrite:
        print(
            f"error: {args.out} exists; pass --overwrite to replace.",
            file=sys.stderr,
        )
        return 2

    yaml_text = render_yaml(seeds)

    # R1 guard: refuse to write a manifest whose emitted entries carry a
    # malformed field bundle. Today the seeder emits no `min_setec_version`
    # (the bundle is hand-curated), so every entry is exempt and this is a
    # no-op — but it pins the contract in the bootstrap tool too, so if the
    # seeder is ever taught to emit the bundle, an invalid one can't ship.
    # Parsing the rendered text (rather than the Seed dataclasses) validates
    # exactly what gets written.
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    if yaml is not None:
        parsed = yaml.safe_load(yaml_text) or {}
        bundle_problems: list[str] = []
        for entry in parsed.get("entries") or []:
            eid = entry.get("id", "(no id)")
            for problem in validate_r1_bundle(entry):
                bundle_problems.append(f"  {eid}: {problem}")
        if bundle_problems:
            print(
                "error: emitted manifest has invalid R1 field bundle(s):\n"
                + "\n".join(bundle_problems),
                file=sys.stderr,
            )
            return 2

    args.out.write_text(yaml_text, encoding="utf-8")
    print(f"Wrote {args.out} ({len(seeds)} entries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
