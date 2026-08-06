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
import subprocess
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


# Hand-reviewed exceptions where the default "P4, will convert to
# setec.paths" disposition would be DISHONEST — the anchor cannot become
# a setec.paths call, ever, for a structural reason named in `reason`.
# `removal_phase: "not-applicable"` says so explicitly instead of
# promising a conversion that will never happen. Both entries below were
# named directly in build-review P1 finding #5 ("hand-review the two rows
# with impossible removal plans ... give each an honest reason and a real
# disposition").
_MANUAL_DISPOSITIONS: dict[tuple[str, str], dict[str, str]] = {
    (
        "plugins/setec-voiceprint/scripts/argument_register_baselines.py",
        "_REPO_ROOT",
    ): {
        "removal_phase": "not-applicable",
        "reason": (
            "_REPO_ROOT = Path(__file__).resolve().parents[3] reaches the "
            "REPOSITORY root specifically to find the shared top-level "
            "baselines/ directory (baselines/argument_register_baselines.yaml), "
            "which lives OUTSIDE plugins/setec-voiceprint/ entirely. "
            "setec.paths.find_plugin_root() resolves only the PLUGIN root by "
            "design and cannot reach a repo-root sibling directory. Not "
            "convertible without relocating baselines/ into the plugin (a "
            "data-ownership decision outside this packaging spec's scope) — "
            "SETEC_BASELINES_DIR is the documented env-var override already "
            "shipped for exactly this case. Standing exception, not migration "
            "debt."
        ),
    },
    (
        "plugins/setec-voiceprint/scripts/argument_register_baselines.py",
        "_DEFAULT_YAML_PATH",
    ): {
        "removal_phase": "not-applicable",
        "reason": (
            "Derived from _REPO_ROOT (repo-root baselines/ path) — same "
            "disposition: not convertible to setec.paths, see _REPO_ROOT's "
            "row."
        ),
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "revision",
    ): {
        "removal_phase": "not-applicable",
        "reason": (
            "Path(__file__) here is passed to "
            "source_commitment.committed_producer_identity(script=Path(__file__)) "
            "as a SELF-REFERENTIAL provenance stamp — it identifies THIS "
            "script's own file for content-commitment hashing (git revision + "
            "blob OID + committed script bytes), not a stay-put data-path "
            "lookup. setec.paths resolves plugin-relative DATA locations; "
            "swapping this anchor for a setec.paths call would change WHAT "
            "gets hashed (the plugin root's identity instead of this script's), "
            "which is a semantic change this packaging spec explicitly forbids "
            "(no production behavior changes). Standing exception."
        ),
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "blob",
    ): {
        "removal_phase": "not-applicable",
        "reason": "Same tuple-unpacking assignment as 'revision' — see that row.",
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "committed_script",
    ): {
        "removal_phase": "not-applicable",
        "reason": "Same tuple-unpacking assignment as 'revision' — see that row.",
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "commitment",
    ): {
        "removal_phase": "not-applicable",
        "reason": (
            "Downstream of 'revision'/'blob'/'committed_script' "
            "(source_commitment.build_commitment(producer_revision=revision, "
            "producer_blob_oid=blob, producer_script_bytes=committed_script, ...)) "
            "— part of the same self-referential content-commitment chain "
            "rooted in Path(__file__) as this script's own identity. Same "
            "disposition, see 'revision'."
        ),
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "commitment_bytes",
    ): {
        "removal_phase": "not-applicable",
        "reason": "Downstream of 'commitment' — same self-referential chain, see 'revision'.",
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "receipt",
    ): {
        "removal_phase": "not-applicable",
        "reason": "Downstream of 'commitment'/'commitment_bytes' — same self-referential chain, see 'revision'.",
    },
    (
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py",
        "payloads",
    ): {
        "removal_phase": "not-applicable",
        "reason": "Downstream of 'commitment_bytes'/'receipt' — same self-referential chain, see 'revision'.",
    },
}


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


_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _flatten_scope_statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement reachable from `body` WITHOUT crossing into a
    nested function/class scope — expands `if`/`for`/`while`/`with`/
    `try` (which do NOT introduce a new Python scope) but returns a
    nested `FunctionDef`/`AsyncFunctionDef`/`ClassDef` as a leaf,
    unexpanded (it is a separate scope, handled on its own pass)."""
    out: list[ast.stmt] = []
    for stmt in body:
        out.append(stmt)
        if isinstance(stmt, _SCOPE_BOUNDARY):
            continue
        if isinstance(stmt, ast.If):
            out.extend(_flatten_scope_statements(stmt.body))
            out.extend(_flatten_scope_statements(stmt.orelse))
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            out.extend(_flatten_scope_statements(stmt.body))
            out.extend(_flatten_scope_statements(stmt.orelse))
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            out.extend(_flatten_scope_statements(stmt.body))
        elif isinstance(stmt, ast.Try):
            out.extend(_flatten_scope_statements(stmt.body))
            for handler in stmt.handlers:
                out.extend(_flatten_scope_statements(handler.body))
            out.extend(_flatten_scope_statements(stmt.orelse))
            out.extend(_flatten_scope_statements(stmt.finalbody))
    return out


def _assign_targets(stmt: ast.stmt) -> tuple[list[str], ast.AST] | None:
    """`(target_names, value_node)` for a statement that binds one or
    more names to a single RHS expression — `ast.Assign` (including
    chained `a = b = value` and tuple/list-unpacking targets
    `a, b = value`) and `ast.AnnAssign` (`a: Path = value`, skipped
    when it's annotation-only with no `value`). Returns None for
    anything else."""
    if isinstance(stmt, ast.Assign):
        names: list[str] = []
        for tgt in stmt.targets:
            names.extend(_names_in_target(tgt))
        if names:
            return names, stmt.value
        return None
    if isinstance(stmt, ast.AnnAssign):
        if stmt.value is None:
            return None
        names = _names_in_target(stmt.target)
        if names:
            return names, stmt.value
        return None
    return None


def _names_in_target(target: ast.expr) -> list[str]:
    """Every bound name in an assignment target, including tuple/list
    unpacking (`a, (b, c) = value`) — each unpacked name is treated as
    a potential anchor when the shared RHS contains `__file__`; this is
    conservative (a real per-element split isn't attempted) but tuple-
    unpacked `__file__` anchors are not a pattern this codebase uses,
    so the conservative-inclusion cost is theoretical."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_names_in_target(elt))
        return out
    return []


def _scope_anchors(
    stmts: list[ast.stmt], visible_names: set[str],
) -> dict[str, ast.stmt]:
    """Anchors found within ONE scope's own flattened statement list.
    `visible_names` are names already known-anchor at an ENCLOSING
    (module) scope — legitimately visible to a function body per
    Python's scoping rules, so a local alias that reads one (`def f():
    here = SCRIPT_DIR / "x"`) is still correctly recognized — WITHOUT
    conflating a same-named but semantically unrelated local in a
    DIFFERENT function (the scope-blindness bug: the previous
    implementation ran this closure over an ast.walk() of the WHOLE
    file, so a coincidental name match between two unrelated local
    variables in two different functions was treated as a derivation
    — the exact defect build-review's "86 of 371 rows are non-anchors
    from function-local name collisions" finding named)."""
    local_assigns = [
        (names, value, stmt)
        for stmt in stmts
        for result in [_assign_targets(stmt)]
        if result is not None
        for names, value in [result]
    ]

    known: dict[str, ast.stmt] = {}
    for names, value, stmt in local_assigns:
        if _contains_file_dunder(value):
            for name in names:
                known[name] = stmt

    changed = True
    while changed:
        changed = False
        for names, value, stmt in local_assigns:
            if all(n in known for n in names):
                continue
            loaded = {
                n.id for n in ast.walk(value)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
            if loaded & (known.keys() | visible_names):
                for name in names:
                    known[name] = stmt
                changed = True
    return known


def find_anchors_in_file(path: Path) -> list[Anchor]:
    """Every `__file__`-relative anchor in `path`: a module- or
    function-scope assignment (`Assign` or `AnnAssign`, including
    tuple-unpacking and chained targets) whose RHS contains `__file__`
    (directly, or via a chain of `.parent`/`.parents[...]`/`.with_name`/
    calls), PLUS the closure of "helper alias" assignments derived from
    an already-known anchor symbol (`Y = X.parent`, `Y = X.with_name(...)`,
    etc.) — matching the spec's "chained `with_name`, and helper aliases"
    language. The closure is SCOPE-AWARE: it runs once for the module
    body and once per function/class body, never conflating two
    same-named but unrelated local variables in different functions. A
    `__file__` reference that appears outside any simple assignment
    (used inline in a call/expression) is recorded once at its
    enclosing statement, symbol `"<inline>"`.
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

    module_stmts = _flatten_scope_statements(tree.body)
    module_known = _scope_anchors(module_stmts, visible_names=set())

    all_known: dict[str, ast.stmt] = dict(module_known)
    covered_nodes: list[ast.stmt] = list(module_known.values())

    for scope_node in ast.walk(tree):
        if not isinstance(scope_node, _SCOPE_BOUNDARY):
            continue
        scope_stmts = _flatten_scope_statements(scope_node.body)
        local_known = _scope_anchors(
            scope_stmts, visible_names=set(module_known.keys()),
        )
        for name, stmt in local_known.items():
            # A local name that merely re-reads a module-level anchor
            # (no NEW local binding was actually found — `visible_names`
            # only makes it usable, `_scope_anchors` only returns names
            # it bound WITHIN this scope) is naturally excluded already;
            # this loop only sees genuinely NEW local anchors. If a
            # DIFFERENT function coincidentally binds the same bare name
            # as an existing module-level (or another function's) anchor,
            # the first one found wins the (path, symbol) exemption slot
            # — a documented simplification of the (path, symbol)
            # exemption-key granularity, not a scope-blindness bug: both
            # are still genuine anchors, and the migration checker's key
            # scheme was never symbol-per-scope to begin with.
            if name not in all_known:
                all_known[name] = stmt
            covered_nodes.append(stmt)

    anchors = [
        Anchor(
            path=rel,
            symbol=name,
            lineno=stmt.lineno,
            expr=ast.unparse(stmt),
        )
        for name, stmt in all_known.items()
    ]

    # Inline (non-assignment) __file__ uses: any Name('__file__') not
    # covered by a line already claimed by a known anchor statement.
    covered_lines: set[int] = set()
    for stmt in covered_nodes:
        covered_lines |= set(_line_range(stmt))

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
    # "not-applicable" is a real, honest disposition — a hand-reviewed
    # anchor that structurally CANNOT become a setec.paths call (see
    # _MANUAL_DISPOSITIONS) — never a default; every "not-applicable" row
    # must justify itself in `reason`, and this validator doesn't relax
    # that requirement.
    legal_phases = {"P2", "P3", "P4", "P5", "not-applicable"}
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
        override = _MANUAL_DISPOSITIONS.get((path, symbol))
        if override is not None:
            reason = override["reason"]
            removal_phase = override["removal_phase"]
        else:
            reason = (
                f"pending relocation ({_phase_for(path)}); anchor "
                f"`{symbol}` at line{plural} {linenos} not yet converted to "
                f"setec.paths (P1 lands no production moves)"
            )
            removal_phase = _phase_for(path)
        rows.append({
            "path": path,
            "symbol": symbol,
            "reason": reason,
            "owner": "packaging",
            "introduced_sha": base_sha,
            "removal_phase": removal_phase,
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


class MigrationCheckError(RuntimeError):
    """Internal error (exit 2) — e.g. the merge base is unavailable."""


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MigrationCheckError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def _merge_base(base_ref: str) -> str:
    try:
        return _run_git(["merge-base", "HEAD", base_ref]).strip()
    except MigrationCheckError as exc:
        raise MigrationCheckError(
            f"could not resolve a merge base against {base_ref!r}: {exc}"
        ) from exc


def _exemptions_file_at(sha: str) -> list[dict[str, Any]] | None:
    """The exemptions file's rows as committed at `sha`, or None if the
    file didn't exist there yet (this PR's own situation — nothing to
    ratchet against)."""
    rel = EXEMPTIONS_PATH.relative_to(REPO_ROOT).as_posix()
    proc = subprocess.run(
        ["git", "show", f"{sha}:{rel}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    yaml = _load_yaml()
    doc = yaml.safe_load(proc.stdout) or {}
    rows = doc.get("exemptions")
    return rows if isinstance(rows, list) else []


def check_ghost_rows(
    rows: list[dict[str, Any]], anchors: list[Anchor],
) -> list[str]:
    """`--strict` only: every committed exemption row must correspond to
    a REAL, currently-found anchor. A "ghost" row — one that names a
    path/symbol the scanner doesn't (or no longer) find — is either
    stale cruft from a conversion that forgot to delete its row, or a
    pre-authorization for a change that was never actually made either
    way, not something the file should silently carry forever."""
    anchor_keys = {(a.path, a.symbol) for a in anchors}
    problems = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _exemption_key(row)
        if key not in anchor_keys:
            problems.append(
                f"{row.get('path')}: symbol {row.get('symbol')!r} has a "
                f"committed exemption row but no matching anchor was found "
                f"— a ghost/expired row. Delete it (the anchor was "
                f"converted or the file/symbol no longer exists) or rerun "
                f"--seed to reconcile."
            )
    return problems


def check_ratchet(base_sha: str) -> list[str]:
    """`--strict` only: exemptions may only SHRINK once committed — the
    same "existing exceptional edges may only disappear; new edges are
    errors" ratchet spec §4 describes for the layering allowlist. A row
    present in the CANDIDATE file but absent from the file as committed
    at the merge base is a new addition; that is only legitimate for a
    brand-new file (this file didn't exist there at all — nothing to
    ratchet against, matching THIS spec's own P1 commit). Once the file
    exists at a merge base, its row set at HEAD must be a SUBSET of the
    merge-base row set."""
    old_rows = _exemptions_file_at(base_sha)
    if old_rows is None:
        return []
    old_keys = {
        (r.get("path"), r.get("symbol")) for r in old_rows if isinstance(r, dict)
    }
    try:
        new_rows = load_exemptions()
    except (ValueError, ImportError):
        return []  # already reported by the caller's own load_exemptions() call
    new_keys = {
        (r.get("path"), r.get("symbol")) for r in new_rows if isinstance(r, dict)
    }
    added = sorted(new_keys - old_keys)
    if not added:
        return []
    return [
        f"{path}: symbol {symbol!r} is a NEW exemption row not present in "
        f"{EXEMPTIONS_PATH.name} at the merge base {base_sha} — exemptions "
        f"only shrink once committed; a genuinely new __file__ anchor in "
        f"new code should be written against setec.paths from the start, "
        f"never added to this file"
        for path, symbol in added
    ]


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

    strict_problems: list[str] = []
    if args.strict:
        strict_problems.extend(check_ghost_rows(rows, anchors))
        try:
            base_sha = _merge_base(args.base_ref)
        except MigrationCheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        strict_problems.extend(check_ratchet(base_sha))

    passed = (
        not shape_problems and not unexempted and not tool_violations
        and not strict_problems
    )

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
            "strict_problems": strict_problems,
        }, indent=2))
        return 0 if passed else 1

    print(
        f"Scanned {len(anchors)} plugin-runtime anchor(s); "
        f"{len(rows)} exemption row(s) on file."
        + (" (--strict)" if args.strict else "")
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
    if strict_problems:
        print(f"\n{len(strict_problems)} --strict violation(s):\n")
        for p in strict_problems:
            print(f"  {p}")
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
    parser.add_argument(
        "--strict", action="store_true",
        help=(
            "Also reject a ghost/expired exemption row (no matching "
            "current anchor) and enforce the merge-base ratchet "
            "(exemptions may only shrink once committed)."
        ),
    )
    parser.add_argument(
        "--base-ref", default="origin/main",
        help="Ref to compute the merge base against (--strict only).",
    )
    args = parser.parse_args(argv)

    if args.seed:
        return cmd_seed(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
