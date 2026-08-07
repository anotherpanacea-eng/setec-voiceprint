#!/usr/bin/env python3
"""check_syspath_ratchet.py — pin the `sys.path` bootstrap ceiling.

Per `specs/svp-packaging-conversion.md` P5 ("... remaining `sys.path`
ratchets in CI"). An earlier audit found roughly 109 of the flat-tree
`sys.path.insert`/`sys.path.append` bootstraps are redundant no-ops (a
script's own directory is already `sys.path[0]` under both direct
execution and `setec_run.py` dispatch). This checker does NOT remove
any of them -- that is a separate, later change. It only PINS today's
count and fails if it goes up, so the flat tree cannot quietly grow
more bootstrap debt while the cleanup is pending.

Scope: every `sys.path.insert(...)` / `sys.path.append(...)` call site,
found by AST (not text grep, so a reformatted or renamed-alias call
site is still counted correctly) anywhere in plugin-runtime PRODUCTION
source under `plugins/setec-voiceprint/scripts/`, excluding
`scripts/tests/` and `__pycache__`. Deliberately independent of
check_packaging_migration.find_runtime_scripts() (which additionally
excludes `setec/paths.py` for an unrelated, anchor-scan-specific
reason) and of check_layering.find_runtime_scripts() (same scope, but
this file avoids a cross-tool import so each of the three P5 checkers
stays independently runnable).

The pinned ceiling lives in this file as `PINNED_CEILING`, not a
separate YAML -- there is no exemption concept here (a specific call
site cannot be "excused"; only the aggregate count is ratcheted), so a
whole second exemptions file would be a schema with one row. Lowering
the ceiling (as the redundant-bootstrap cleanup lands) is a one-line
edit to `PINNED_CEILING` in the SAME commit that removes the sites --
the N10-style "byte-pinned file, re-pin in the same commit" discipline
applies here too.

Exit codes:

    0 — current count <= PINNED_CEILING
    1 — current count > PINNED_CEILING (regression), OR (--strict only)
        current count < PINNED_CEILING (the ceiling is stale and must
        be lowered to match — a silently-loose ceiling is not a ratchet)
    2 — internal error

Usage:

    python3 tools/check_syspath_ratchet.py
    python3 tools/check_syspath_ratchet.py --strict
    python3 tools/check_syspath_ratchet.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import enable_utf8_stdio  # noqa: E402

_EXCLUDED_DIR_PARTS = {"tests", "__pycache__"}

# Measured at origin/main ebd7a2eba9136e5831dd6a82878e7458d31fc520
# (2026-08-06) via `python3 tools/check_syspath_ratchet.py --json`. Lower
# this number IN THE SAME COMMIT that removes call sites; never raise it
# to make a new bootstrap pass -- add the bootstrap without growing the
# count, or get a real review for why the ceiling must move.
PINNED_CEILING = 156


def find_runtime_scripts() -> list[Path]:
    out = []
    for p in SCRIPTS_ROOT.rglob("*.py"):
        rel = p.relative_to(SCRIPTS_ROOT)
        if any(part in _EXCLUDED_DIR_PARTS for part in rel.parts):
            continue
        out.append(p)
    return sorted(out)


@dataclass
class SyspathSite:
    path: str      # repo-relative posix path
    lineno: int
    method: str    # "insert" | "append"


def _is_sys_path_attr(node: ast.AST) -> bool:
    """Whether `node` is the attribute expression `sys.path` (allowing for
    `import sys as _sys`-style aliasing is NOT attempted -- this codebase
    uses a bare `import sys` uniformly; see the planted-violation test for
    the exact pattern this matches)."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def find_sites_in_file(path: Path) -> list[SyspathSite]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    repo_rel = path.relative_to(REPO_ROOT).as_posix()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute) or f.attr not in ("insert", "append"):
            continue
        if not _is_sys_path_attr(f.value):
            continue
        out.append(SyspathSite(path=repo_rel, lineno=node.lineno, method=f.attr))
    return out


def find_all_sites() -> list[SyspathSite]:
    out: list[SyspathSite] = []
    for p in find_runtime_scripts():
        out.extend(find_sites_in_file(p))
    out.sort(key=lambda s: (s.path, s.lineno))
    return out


def cmd_check(args: argparse.Namespace) -> int:
    sites = find_all_sites()
    count = len(sites)

    over = count > PINNED_CEILING
    under = count < PINNED_CEILING
    passed = not over and (not args.strict or not under)

    if args.json:
        print(json.dumps({
            "passed": passed,
            "count": count,
            "pinned_ceiling": PINNED_CEILING,
            "over_by": max(0, count - PINNED_CEILING),
            "under_by": max(0, PINNED_CEILING - count),
            "sites": [
                {"path": s.path, "lineno": s.lineno, "method": s.method}
                for s in sites
            ] if not passed else [],
        }, indent=2))
        return 0 if passed else 1

    print(
        f"Found {count} sys.path.insert/append call site(s) in "
        f"plugin-runtime production code; pinned ceiling is {PINNED_CEILING}."
    )
    if passed:
        print("Ceiling holds. ✔")
        return 0
    if over:
        print(
            f"\nREGRESSION: {count} > {PINNED_CEILING} -- "
            f"{count - PINNED_CEILING} new sys.path bootstrap site(s) "
            "introduced. Either avoid the new bootstrap or, if it's a "
            "deliberate part of the (separate) redundant-bootstrap "
            "cleanup, raise PINNED_CEILING in tools/check_syspath_ratchet.py "
            "in the same commit with a reviewed reason.\n"
        )
    if args.strict and under:
        print(
            f"\n--strict: {count} < {PINNED_CEILING} -- the pinned ceiling "
            f"is stale (some sites were removed without lowering it). "
            "Lower PINNED_CEILING to the current count in the same commit "
            "that removed them.\n"
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Pin the count of sys.path.insert/append call sites in "
            "plugin-runtime production code; fail if it increases."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict", action="store_true",
        help="Also fail if the count has dropped below the pinned ceiling "
             "without the ceiling being lowered to match.",
    )
    args = parser.parse_args(argv)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
