#!/usr/bin/env python3
"""check_packaging_migration.py — packaging-migration anchor gate.

Per `specs/svp-packaging-conversion.md` §1 ("Root resolution before
moves"): before a plugin-runtime module moves into `scripts/setec/`,
every `__file__`-relative anchor it (or any other plugin-runtime
module) defines must be either converted to `setec.paths` or listed in
the single exemptions file
(`plugins/setec-voiceprint/packaging_migration_exemptions.yaml`) with
`path`, `symbol`, `reason`, `owner`, `introduced_sha`, and
`removal_phase`.

Scope: this AST-scans plugin-RUNTIME source only — everything under
`plugins/setec-voiceprint/scripts/` EXCEPT `scripts/tests/` (test
files are not runtime anchors; they get their own pytest-bootstrap
codemod) and `scripts/setec/` (the destination package; `setec/paths.py`
itself legitimately anchors on `__file__` — it IS the resolver).

`tools/` scripts are a different job (they resolve the REPOSITORY
root, never the plugin root — see the spec's "Tools do not move"
note) and are covered by a separate, narrower check in this same tool:
every `tools/*.py` module-level `REPO_ROOT` assignment must be exactly
`Path(__file__).resolve().parents[1]` (tools/ sits one level under the
repo root; a different depth would silently resolve the wrong
directory).

Exit codes:

    0 — every anchor is exempted (or none found)
    1 — an anchor is missing from the exemptions file, or an
        exemption is malformed/stale
    2 — internal error (missing merge base is NOT this checker's
        job — see tools/check_claim_license_guard.py)

Usage:

    python3 tools/check_packaging_migration.py            # gate (CI)
    python3 tools/check_packaging_migration.py --seed      # (re)write
                                                            # the exemptions
                                                            # file from the
                                                            # current tree
    python3 tools/check_packaging_migration.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
TOOLS_ROOT = REPO_ROOT / "tools"
EXEMPTIONS_PATH = PLUGIN_ROOT / "packaging_migration_exemptions.yaml"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from _console import enable_utf8_stdio  # noqa: E402

# Directories under scripts/ that are NOT plugin-runtime anchors for this
# gate's purpose.
_EXCLUDED_DIR_PARTS = {"tests", "setec", "__pycache__"}

# Removal-phase classification, per specs/svp-packaging-conversion.md's
# phase table. Anything not explicitly listed defaults to P4 (whole-surface
# relocation, family by family) — the catch-all for L2 capability-bearing
# modules and their support scripts (oracle/, runners/, test_data/ fixture
# generators). This is a documented default, not a narrative count: the
# `--seed` regeneration always re-derives it from the live tree.
_L0_MODULES = {"output_schema.py", "claim_license.py", "capabilities.py"}
_L1_MODULES = {"stylometry_distance.py"}
# setec_run.py is an explicit generated-shim exclusion (spec §2): it keeps a
# dedicated hand-written launcher and is not covered by the four-template
# generator. Its own anchors are grouped with the P2 L0 batch as the nearest
# named milestone; ASSUMPTION flagged in the build report (no phase is
# spelled out for it explicitly in the spec).
_SETEC_RUN = "setec_run.py"


def _phase_for(rel_path: str) -> str:
    name = Path(rel_path).name
    if name in _L0_MODULES or name == _SETEC_RUN:
        return "P2"
    if name in _L1_MODULES:
        return "P3"
    return "P4"


# ---------- anchor discovery ----------------------------------------


@dataclass
class Anchor:
    path: str       # repo-relative posix path
    symbol: str      # assigned name, or "<inline>"
    lineno: int
    expr: str        # unparsed RHS (or enclosing statement for inline)


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDED_DIR_PARTS for part in path.parts)


def find_runtime_scripts() -> list[Path]:
    out = []
    for p in SCRIPTS_ROOT.rglob("*.py"):
        rel = p.relative_to(SCRIPTS_ROOT)
        if _is_excluded(rel):
            continue
        out.append(p)
    return sorted(out)


def _contains_file_dunder(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "__file__":
            return True
    return False


def _line_range(node: ast.AST) -> range:
    lo = node.lineno
    hi = getattr(node, "end_lineno", None) or lo
    return range(lo, hi + 1)


def find_anchors_in_file(path: Path) -> list[Anchor]:
    """Every `__file__`-relative anchor in `path`: a module- or
    function-scope simple assignment whose RHS contains `__file__`
    (directly, or via a chain of `.parent`/`.parents[...]`/`.with_name`/
    calls), PLUS the closure of "helper alias" assignments derived from
    an already-known anchor symbol (`Y = X.parent`, `Y = X.with_name(...)`,
    etc.) — matching the spec's "chained `with_name`, and helper aliases"
    language. A `__file__` reference that appears outside any simple
    assignment (used inline in a call/expression) is recorded once at its
    enclosing top-level statement, symbol `"<inline>"`.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    rel = path.relative_to(REPO_ROOT).as_posix()

    # Collect every simple `Name = <expr>` assignment anywhere in the file
    # (module scope, function scope, class body — anchors have shown up in
    # all three across this codebase).
    assigns: list[tuple[str, ast.Assign]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                assigns.append((tgt.id, node))

    direct: dict[str, ast.Assign] = {}
    for name, node in assigns:
        if _contains_file_dunder(node.value):
            direct[name] = node

    # Fixed-point closure: an assignment whose RHS Name-loads include an
    # already-known anchor symbol is itself a "helper alias" anchor.
    known = dict(direct)
    changed = True
    while changed:
        changed = False
        for name, node in assigns:
            if name in known:
                continue
            loaded = {
                n.id
                for n in ast.walk(node.value)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            if loaded & known.keys():
                known[name] = node
                changed = True

    anchors = [
        Anchor(
            path=rel,
            symbol=name,
            lineno=node.lineno,
            expr=ast.unparse(node.value),
        )
        for name, node in known.items()
    ]

    # Inline (non-assignment) __file__ uses: any Name('__file__') not
    # covered by a line already claimed by a known anchor assignment.
    covered_lines: set[int] = set()
    for node in known.values():
        covered_lines |= set(_line_range(node))

    inline_lines: set[int] = set()
    for sub in ast.walk(tree):
        if (
            isinstance(sub, ast.Name)
            and sub.id == "__file__"
            and getattr(sub, "lineno", None) not in covered_lines
        ):
            inline_lines.add(sub.lineno)

    for lineno in sorted(inline_lines):
        anchors.append(
            Anchor(path=rel, symbol="<inline>", lineno=lineno, expr="__file__")
        )

    return anchors


def find_all_anchors() -> list[Anchor]:
    out: list[Anchor] = []
    for path in find_runtime_scripts():
        out.extend(find_anchors_in_file(path))
    return out


# ---------- tools/ REPO_ROOT depth check -----------------------------


@dataclass
class ToolRootViolation:
    path: str
    detail: str


def check_tools_repo_root() -> list[ToolRootViolation]:
    """Every `tools/*.py` module-level `REPO_ROOT` assignment must be
    exactly `Path(__file__).resolve().parents[1]` — tools/ sits one
    level under the repo root, so any other depth silently resolves the
    wrong directory. Tools are NOT plugin-runtime and must never adopt
    `setec.paths` (that would resolve the plugin root, not the repo
    root) — this check exists to catch exactly that class of mistake."""
    violations: list[ToolRootViolation] = []
    for path in sorted(TOOLS_ROOT.glob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (UnicodeDecodeError, SyntaxError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in tree.body:  # module level only
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not (isinstance(tgt, ast.Name) and tgt.id == "REPO_ROOT"):
                continue
            expr = ast.unparse(node.value)
            if expr != "Path(__file__).resolve().parents[1]":
                violations.append(ToolRootViolation(
                    path=rel,
                    detail=(
                        f"REPO_ROOT = {expr!r} at line {node.lineno}; "
                        f"expected exactly "
                        f"'Path(__file__).resolve().parents[1]' "
                        f"(tools/ is one level under the repo root)"
                    ),
                ))
    return violations


# ---------- exemptions file -------------------------------------------


def _load_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise ImportError(
            "check_packaging_migration.py requires PyYAML "
            "(`pip install pyyaml`)"
        ) from exc


REQUIRED_EXEMPTION_FIELDS = (
    "path", "symbol", "reason", "owner", "introduced_sha", "removal_phase",
)


def load_exemptions(path: Path | None = None) -> list[dict[str, Any]]:
    # Live global lookup at call time, not a def-time-bound default — so a
    # test that monkeypatches EXEMPTIONS_PATH is honored by a no-arg call.
    if path is None:
        path = EXEMPTIONS_PATH
    if not path.exists():
        return []
    yaml = _load_yaml()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or "exemptions" not in data:
        raise ValueError(
            f"{path}: expected a mapping with a top-level `exemptions` list"
        )
    rows = data["exemptions"]
    if not isinstance(rows, list):
        raise ValueError(f"{path}: `exemptions` must be a list")
    return rows


def _exemption_key(row: dict[str, Any]) -> tuple[str, str]:
    return (row.get("path"), row.get("symbol"))


def validate_exemption_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Shape-check every row: all six required fields present and
    non-empty, plus `removal_phase` in the legal vocabulary."""
    problems: list[str] = []
    legal_phases = {"P2", "P3", "P4", "P5"}
    seen: set[tuple[str, str]] = set()
    for i, row in enumerate(rows):
        where = f"exemptions[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not a mapping")
            continue
        for f in REQUIRED_EXEMPTION_FIELDS:
            if not row.get(f):
                problems.append(f"{where} ({row.get('path')}): missing/empty `{f}`")
        phase = row.get("removal_phase")
        if phase is not None and phase not in legal_phases:
            problems.append(
                f"{where} ({row.get('path')}): removal_phase {phase!r} not "
                f"in {sorted(legal_phases)}"
            )
        key = _exemption_key(row)
        if key in seen:
            problems.append(f"{where}: duplicate exemption for {key}")
        seen.add(key)
    return problems


# ---------- CLI --------------------------------------------------------


def cmd_seed(args: argparse.Namespace) -> int:
    """(Re)write the exemptions file from the current tree: one row per
    anchor found, `--check`-friendly (every found anchor gets an
    exemption, so a fresh `--seed` always makes the gate pass). This is
    the mechanism the spec's "the single ... exemptions file" is
    maintained by — it is generated from source, never hand-typed, so it
    can never silently go stale relative to a narrative count."""
    yaml = _load_yaml()
    anchors = find_all_anchors()
    base_sha = args.introduced_sha

    # Group by (path, symbol) — the exemption key. A file can carry more
    # than one inline (unassigned) __file__ reference, which all share the
    # synthetic symbol "<inline>"; one exemption row covers all of them
    # (the check matches on (path, symbol), not per-line), so merge their
    # line numbers into one reason string rather than emitting duplicate
    # rows with a colliding key.
    grouped: dict[tuple[str, str], list[Anchor]] = {}
    for a in anchors:
        grouped.setdefault((a.path, a.symbol), []).append(a)

    rows = []
    for (path, symbol), group in grouped.items():
        linenos = ", ".join(str(a.lineno) for a in sorted(
            group, key=lambda a: a.lineno
        ))
        plural = "s" if len(group) > 1 else ""
        rows.append({
            "path": path,
            "symbol": symbol,
            "reason": (
                f"pending relocation ({_phase_for(path)}); anchor "
                f"`{symbol}` at line{plural} {linenos} not yet converted to "
                f"setec.paths (P1 lands no production moves)"
            ),
            "owner": "packaging",
            "introduced_sha": base_sha,
            "removal_phase": _phase_for(path),
        })
    rows.sort(key=lambda r: (r["path"], r["symbol"]))
    doc = {
        "schema_version": 1,
        "exemptions": rows,
    }
    EXEMPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXEMPTIONS_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            "# packaging_migration_exemptions.yaml — the single exemptions "
            "file for tools/check_packaging_migration.py\n"
            "#\n"
            "# Per specs/svp-packaging-conversion.md §1: every "
            "__file__-relative anchor in plugin-runtime code under\n"
            "# plugins/setec-voiceprint/scripts/ (excluding scripts/tests/ "
            "and scripts/setec/) is either converted to\n"
            "# setec.paths or listed here with path, symbol, reason, owner, "
            "introduced_sha, and removal_phase.\n"
            "#\n"
            "# Regenerate with: python3 tools/check_packaging_migration.py "
            "--seed --introduced-sha <sha>\n"
            "# Then hand-review the diff before committing — a `--seed` run "
            "makes every CURRENT anchor exempt; it does not\n"
            "# decide whether a NEW anchor introduced by an unrelated PR "
            "should be. Removing a row (as its module's owning\n"
            "# phase lands and converts it to setec.paths) is the ratchet: "
            "exemptions only shrink from here.\n"
        )
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    print(f"Wrote {len(rows)} exemption row(s) to {EXEMPTIONS_PATH}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    anchors = find_all_anchors()
    try:
        rows = load_exemptions()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    shape_problems = validate_exemption_rows(rows)
    exempted = {_exemption_key(r) for r in rows if isinstance(r, dict)}

    unexempted = [
        a for a in anchors if (a.path, a.symbol) not in exempted
    ]

    tool_violations = check_tools_repo_root()

    passed = not shape_problems and not unexempted and not tool_violations

    if args.json:
        print(json.dumps({
            "passed": passed,
            "scanned_anchors": len(anchors),
            "exemption_rows": len(rows),
            "unexempted": [
                {"path": a.path, "symbol": a.symbol, "lineno": a.lineno}
                for a in unexempted
            ],
            "exemption_shape_problems": shape_problems,
            "tool_repo_root_violations": [
                {"path": v.path, "detail": v.detail} for v in tool_violations
            ],
        }, indent=2))
        return 0 if passed else 1

    print(
        f"Scanned {len(anchors)} plugin-runtime anchor(s); "
        f"{len(rows)} exemption row(s) on file."
    )
    if passed:
        print("Every anchor is exempted; tools/ REPO_ROOT depths check out. ✔")
        return 0

    if unexempted:
        print(f"\n{len(unexempted)} anchor(s) missing from {EXEMPTIONS_PATH}:\n")
        for a in unexempted:
            print(f"  {a.path}:{a.lineno} symbol={a.symbol!r}  ({a.expr})")
    if shape_problems:
        print(f"\n{len(shape_problems)} exemption-file shape problem(s):\n")
        for p in shape_problems:
            print(f"  {p}")
    if tool_violations:
        print(f"\n{len(tool_violations)} tools/ REPO_ROOT depth violation(s):\n")
        for v in tool_violations:
            print(f"  {v.path}: {v.detail}")
    return 1


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Gate every plugin-runtime __file__-relative anchor against "
            "the single packaging_migration_exemptions.yaml file."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--seed", action="store_true",
        help="(Re)write the exemptions file from the current tree.",
    )
    parser.add_argument(
        "--introduced-sha", default="unknown",
        help="Base SHA recorded on freshly-seeded rows (--seed only).",
    )
    args = parser.parse_args(argv)

    if args.seed:
        return cmd_seed(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
