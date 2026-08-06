#!/usr/bin/env python3
"""check_claim_license_guard.py — the no-change claim-license deficit lock.

Per `specs/svp-packaging-conversion.md` §3. This packaging repo is
allowed to MOVE claim-license code but never to change what it *says*
or *means*. This gate compares the candidate tree against the fetched
merge base (Git objects only — `git merge-base` + `git show`, never a
baseline regenerated inside the PR) and fails on any semantic delta in
the "protected set": every module that constructs a `ClaimLicense` or
defines a `_claim_license*`-style helper, plus the plugin-local modules
that supply names those call sites reference.

**No semantic claim-license change is authorized by this packaging
spec.** A relocation is fine (see `packaging_move_map.json`'s `moves` /
`path_rewrites`); a changed license string, a changed `model_id`, a
changed comparison set — anything the normalized AST doesn't already
attribute to a declared move — fails CI with NO override.

Protected-set construction (spec §3):

  1. Seed: every plugin-runtime module whose AST (a) defines a
     function or assigns a name matching the spec's literal
     `_claim_license*` glob — a PREFIX match, case-sensitive:
     `_claim_license`, `_claim_license_dict`, `_claim_license_block`,
     ... — OR (b) directly calls `ClaimLicense(...)`. A name that
     merely CONTAINS "claim_license" without STARTING with it (e.g.
     `_structured_claim_license`, or setec.paths'
     `claim_license_surfaces_dir`) does not satisfy (a) alone — a bare
     substring search here previously produced a false-positive seed
     (see the regression test pinning that exact name). Such a module
     is still correctly seeded whenever it ALSO satisfies (b), which
     `_structured_claim_license` in general_imposters.py does (it
     calls `ClaimLicense(...)` directly).
  2. Supplier closure: within each seed module's `_claim_license*`
     function bodies and `ClaimLicense(...)` call sites (args +
     keywords), resolve every `Name` load. A name bound by a
     plugin-local `from X import name` / `import X as name` pulls the
     WHOLE module `X` into the protected set (stdlib/builtin values and
     the `ClaimLicense` class/`claim_license` module import itself are
     terminals — importing the class doesn't recursively protect its
     defining module beyond what already qualifies it as a seed on its
     own merits). Newly added supplier modules are then scanned the
     same way (their own `_claim_license*`/`ClaimLicense(...)` sites,
     if any, plus one more hop of `Name`-load resolution over their
     WHOLE module body) until the set closes. A star import or an
     unresolved/ambiguous alias inside a protected subtree can't be
     traced to a unique source module — the checker fails closed
     (adds nothing silently; the whole run fails so a human resolves
     it) rather than guessing.

Comparison (spec §3): for every protected module, `ast.dump(...,
include_attributes=False)` the merge-base version and the mapped
candidate version. This keeps docstrings, literals, operators, calls,
keyword names/values, collection order, defaults, and control flow —
so even an unchanged `_claim_license` function whose CALLER passes a
different `model_id` is caught. Normalization permits only the exact
relocation declared in `packaging_move_map.json`'s `moves` — a
STRUCTURAL `old_symbol -> new_symbol` AST rename (Name/alias/
FunctionDef/ClassDef/Attribute.attr/ImportFrom.module nodes only,
never a string Constant — see `_StructuralRename`) — and the exact
verified data-anchor substitution declared in `path_rewrites`.

Exit codes:

    0 — no semantic claim-license delta
    1 — a protected-module delta is not explained by the move map, or
        the move map itself is malformed / not one-to-one / silent on
        a deletion or second destination
    2 — internal error: missing merge base, unresolved/ambiguous
        provenance inside a protected subtree (fail closed), or the
        move map / a protected merge-base object is unreadable

Usage:

    python3 tools/check_claim_license_guard.py [--base-ref <ref>]
    python3 tools/check_claim_license_guard.py --json
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
MOVE_MAP_PATH = PLUGIN_ROOT / "packaging_move_map.json"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from _console import enable_utf8_stdio  # noqa: E402

_EXCLUDED_DIR_PARTS = {"tests", "__pycache__"}
# Anchored prefix match for the spec's literal glob "_claim_license*" — a
# leading underscore, then exactly "claim_license". This is deliberately
# NOT a bare substring search: scripts/setec/paths.py exposes a PUBLIC
# `claim_license_surfaces_dir()` accessor (named after the
# claim_license_surfaces/ directory, nothing to do with constructing a
# ClaimLicense license block) that a substring match would wrongly seed.
_CLAIM_LICENSE_NAME_RE = re.compile(r"^_claim_license")


class GuardError(RuntimeError):
    """Raised for a fail-closed condition: missing merge base, an
    unresolved/ambiguous name inside a protected subtree, or a
    malformed move map. The CLI turns this into exit code 2."""


# ---------- git plumbing ---------------------------------------------


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GuardError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def merge_base(base_ref: str) -> str:
    try:
        return _run_git(["merge-base", "HEAD", base_ref]).strip()
    except GuardError as exc:
        raise GuardError(
            f"could not resolve a merge base against {base_ref!r}: {exc}"
        ) from exc


def show_file_at(sha: str, rel_path: str) -> str | None:
    """`git show <sha>:<rel_path>`, or None if the path doesn't exist at
    that commit."""
    proc = subprocess.run(
        ["git", "show", f"{sha}:{rel_path}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_tree_py_files(sha: str, rel_dir: str) -> set[str]:
    """Every `.py` path under `rel_dir` at `sha`, repo-relative posix."""
    out = _run_git(["ls-tree", "-r", "--name-only", sha, "--", rel_dir])
    return {
        line for line in out.splitlines()
        if line.endswith(".py")
    }


# ---------- protected-set construction (candidate tree) --------------


@dataclass
class ProtectedModule:
    path: str  # repo-relative posix
    reason: str


def _module_path_for_import(
    importing_file: Path, module_dotted: str, level: int, symbol: str | None = None,
) -> Path | None:
    """Best-effort resolution of a plugin-local import target to an
    on-disk `.py` file under SCRIPTS_ROOT. Returns None for anything
    that isn't a plain plugin-local module (stdlib, third-party,
    package-relative we can't resolve, etc.) — the caller treats an
    unresolved-but-suspicious import conservatively.

    Handles the `scripts/setec/` PACKAGE (a directory with
    `__init__.py`, not a flat `.py` file) — the resolution hole build-
    review flagged: `from setec import paths` (module_dotted="setec",
    symbol="paths") used to resolve to the nonexistent `setec.py` and
    silently fail to resolve at all, missing `setec/paths.py` as a
    supplier. Resolution order for a dotted path with no flat `.py`
    match: (1) `<base>.py`; (2) if `<base>` is a package
    (`<base>/__init__.py` exists) AND `symbol` names a submodule file
    (`<base>/<symbol>.py`), that submodule — this is the real target
    of Python's own `from package import submodule` idiom, not
    `__init__.py`; (3) `<base>/__init__.py` itself (the symbol is
    presumably defined there directly)."""
    if level > 0:
        # Relative import (`from . import x` / `from .sub import x`).
        # scripts/ is not a real package (no __init__.py at its root),
        # so a relative import inside plugin-runtime code is unexpected;
        # resolve relative to the importing file's directory as a
        # best-effort, still verified against SCRIPTS_ROOT below.
        base = importing_file.parent
        for _ in range(level - 1):
            base = base.parent
        parts = module_dotted.split(".") if module_dotted else []
        base_dir = base.joinpath(*parts)
    else:
        parts = module_dotted.split(".")
        base_dir = SCRIPTS_ROOT.joinpath(*parts)

    flat = base_dir.with_suffix(".py")
    try:
        flat.relative_to(SCRIPTS_ROOT)
    except ValueError:
        return None
    if flat.is_file():
        return flat

    package_init = base_dir / "__init__.py"
    if package_init.is_file():
        if symbol is not None:
            submodule = base_dir / f"{symbol}.py"
            if submodule.is_file():
                return submodule
        return package_init
    return None


def _claim_license_relevant_subtrees(tree: ast.AST) -> list[ast.AST]:
    """Every AST subtree that is either a `_claim_license*`-named
    function body or a `ClaimLicense(...)` call — the exact places
    the spec says to resolve names/args "at those call sites only"."""
    subtrees: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _CLAIM_LICENSE_NAME_RE.search(node.name):
                subtrees.append(node)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and _CLAIM_LICENSE_NAME_RE.search(tgt.id):
                    subtrees.append(node.value)
        elif isinstance(node, ast.Call):
            func = node.func
            called_name = None
            if isinstance(func, ast.Name):
                called_name = func.id
            elif isinstance(func, ast.Attribute):
                called_name = func.attr
            if called_name == "ClaimLicense":
                subtrees.append(node)
    return subtrees


def _module_matches_seed(tree: ast.AST) -> bool:
    return bool(_claim_license_relevant_subtrees(tree))


@dataclass
class ImportMap:
    # name-as-bound -> (module_dotted, imported_symbol_or_None, level)
    bindings: dict[str, tuple[str, str | None, int]] = field(default_factory=dict)
    has_star_import: bool = False


def _build_import_map(tree: ast.AST) -> ImportMap:
    m = ImportMap()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    m.has_star_import = True
                    continue
                bound = alias.asname or alias.name
                m.bindings[bound] = (module, alias.name, node.level)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                m.bindings[bound] = (alias.name, None, 0)
    return m


def build_protected_set(
    scripts_root: Path | None = None,
) -> list[ProtectedModule]:
    """Spec-literal closure: a seed module's initial resolution is
    scoped to its OWN `_claim_license*`/`ClaimLicense(...)` call sites
    ("resolve the names and arguments at those call sites only" — spec
    §3); every module pulled in as a SUPPLIER is then protected "in
    full" and its WHOLE MODULE BODY is scanned for one more hop of
    Name-load resolution ("repeat over newly added suppliers until the
    module set closes"). This is intentionally broader than tracing
    only the specific referenced symbol — a prior, narrower revision of
    this function scoped supplier hops to just the referenced symbol's
    own definition, which is MORE precise but not what the spec
    describes, and it silently excluded a real supplier
    (`judge_backends.py`, reached only through an indirect reference
    inside another supplier's body) from the protected set. Reverted
    per build-review P1 finding #4 — the whole-module rule is the
    conservative, fail-closed reading the spec calls for, and with
    hermetic backend mode removed from this PR there is no longer any
    P1 deliverable whose OWN edits collide with that breadth.

    "Memoized rescan": each module's WHOLE-BODY scan happens at most
    once (the `processed` set below) — reaching the same module through
    a second call site later re-adds nothing to the scan queue, it is
    already covered.
    """
    # A live module-global lookup (not a def-time-bound default), so tests
    # that monkeypatch REPO_ROOT/SCRIPTS_ROOT (module globals referenced
    # below via REPO_ROOT for relative-path math) are honored.
    if scripts_root is None:
        scripts_root = SCRIPTS_ROOT
    protected: dict[str, ProtectedModule] = {}
    file_trees: dict[str, ast.Module] = {}
    all_files = sorted(
        p for p in scripts_root.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_PARTS for part in p.relative_to(scripts_root).parts)
    )

    def _parse(path: Path) -> ast.Module | None:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in file_trees:
            return file_trees[rel]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (UnicodeDecodeError, SyntaxError):
            return None
        file_trees[rel] = tree
        return tree

    queue: list[str] = []

    # Seed.
    for path in all_files:
        tree = _parse(path)
        if tree is None:
            continue
        if _module_matches_seed(tree):
            rel = path.relative_to(REPO_ROOT).as_posix()
            protected[rel] = ProtectedModule(
                path=rel,
                reason="seed: defines/calls a _claim_license*/ClaimLicense(...) site",
            )
            queue.append(rel)

    # Supplier closure — memoized: a module's whole-body scan runs once.
    processed: set[str] = set()
    while queue:
        rel = queue.pop()
        if rel in processed:
            continue
        processed.add(rel)
        path = REPO_ROOT / rel
        tree = _parse(path)
        if tree is None:
            continue
        import_map = _build_import_map(tree)

        # Seeds resolve only their own call-site subtrees on this (their
        # first and only) pass; a module reached purely as a supplier
        # scans its whole body, per the spec's "repeat ... until the
        # module set closes."
        is_seed = _module_matches_seed(tree)
        subtrees: list[ast.AST] = (
            _claim_license_relevant_subtrees(tree) if is_seed else [tree]
        )

        if import_map.has_star_import and any(
            n.id not in import_map.bindings
            for sub in subtrees
            for n in ast.walk(sub)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        ):
            raise GuardError(
                f"{rel}: a star import makes name provenance unresolvable "
                f"inside a protected claim-license subtree — failing closed "
                f"per spec §3 rather than guessing which module supplies "
                f"the name."
            )

        for sub in subtrees:
            for node in ast.walk(sub):
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
                    continue
                name = node.id
                binding = import_map.bindings.get(name)
                if binding is None:
                    continue  # local/builtin/stdlib name — not plugin-local
                module_dotted, symbol, level = binding
                # Terminal: the ClaimLicense class/module itself does not
                # recursively drag in claim_license.py as a "supplier" —
                # claim_license.py is protected on its own merits (it
                # defines ClaimLicense and calls it in with_state_caveats),
                # never merely because something imports the class.
                if symbol == "ClaimLicense" or module_dotted == "claim_license":
                    continue
                target = _module_path_for_import(path, module_dotted, level, symbol)
                if target is None:
                    continue  # not resolvable to a plugin-local file (stdlib/3rd-party)
                target_rel = target.relative_to(REPO_ROOT).as_posix()
                if target_rel not in protected:
                    protected[target_rel] = ProtectedModule(
                        path=target_rel,
                        reason=(
                            f"supplier: {rel} imports {name!r} "
                            f"(symbol {symbol!r}) from here"
                        ),
                    )
                if target_rel not in processed:
                    queue.append(target_rel)

    return sorted(protected.values(), key=lambda p: p.path)


# ---------- move map ----------------------------------------------------


REQUIRED_MOVE_MAP_KEYS = {"schema", "moves", "path_rewrites"}
REQUIRED_MOVE_ROW_KEYS = {"old_path", "new_path", "old_symbol", "new_symbol", "phase"}
REQUIRED_REWRITE_ROW_KEYS = {
    "old_path", "old_ast", "new_path", "new_ast", "plugin_relative_target",
}


def load_move_map(path: Path | None = None) -> dict[str, Any]:
    # Live global lookup at call time (see build_protected_set's comment) —
    # not a def-time-bound default.
    if path is None:
        path = MOVE_MAP_PATH
    if not path.exists():
        raise GuardError(
            f"{path} is missing. The candidate must commit "
            f"packaging_move_map.json with {{schema,moves,path_rewrites}} "
            f"even when it is empty (P1 lands no production moves: "
            f'{{"schema": 1, "moves": [], "path_rewrites": []}}).'
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data.keys()) != REQUIRED_MOVE_MAP_KEYS:
        raise GuardError(
            f"{path}: top-level keys must be exactly "
            f"{sorted(REQUIRED_MOVE_MAP_KEYS)}; got {sorted(data.keys())}"
        )
    moves = data["moves"]
    rewrites = data["path_rewrites"]
    if not isinstance(moves, list) or not isinstance(rewrites, list):
        raise GuardError(f"{path}: `moves` and `path_rewrites` must be lists")
    for i, row in enumerate(moves):
        if not isinstance(row, dict) or set(row.keys()) != REQUIRED_MOVE_ROW_KEYS:
            raise GuardError(
                f"{path}: moves[{i}] must have exactly keys "
                f"{sorted(REQUIRED_MOVE_ROW_KEYS)}"
            )
        # `old_symbol`/`new_symbol` drive a STRUCTURAL AST rename (see
        # _normalized_dump) — restricting them to legal Python identifiers
        # is a cheap first fail-closed gate (a non-identifier can never
        # match a Name/alias/attr node, so it would silently no-op the
        # row instead of documenting a real rename; reject it instead of
        # accepting dead data).
        for key in ("old_symbol", "new_symbol"):
            value = row.get(key)
            if not isinstance(value, str) or not value.isidentifier():
                raise GuardError(
                    f"{path}: moves[{i}].{key} = {value!r} is not a legal "
                    f"Python identifier"
                )
    for i, row in enumerate(rewrites):
        if not isinstance(row, dict) or set(row.keys()) != REQUIRED_REWRITE_ROW_KEYS:
            raise GuardError(
                f"{path}: path_rewrites[{i}] must have exactly keys "
                f"{sorted(REQUIRED_REWRITE_ROW_KEYS)}"
            )
    # One-to-one: no old_path claimed twice, no new_path claimed twice.
    old_paths = [r["old_path"] for r in moves]
    new_paths = [r["new_path"] for r in moves]
    dupes_old = {p for p in old_paths if old_paths.count(p) > 1}
    dupes_new = {p for p in new_paths if new_paths.count(p) > 1}
    if dupes_old:
        raise GuardError(
            f"{path}: moves is not one-to-one — old_path claimed more than "
            f"once: {sorted(dupes_old)}"
        )
    if dupes_new:
        raise GuardError(
            f"{path}: moves is not one-to-one — new_path claimed more than "
            f"once (an unlisted second destination): {sorted(dupes_new)}"
        )
    return data


def validate_move_map_paths(move_map: dict[str, Any], base_sha: str) -> None:
    """Both endpoints of every `moves` row must resolve as real git
    objects — `old_path` at the merge base, `new_path` in the candidate
    — never a path that merely LOOKS plausible. Without this, a row
    could name a path that never existed anywhere, and its
    old_symbol/new_symbol rename would still get applied everywhere
    that symbol appears across the WHOLE protected set (not just the
    claimed file), which is its own laundering surface even after the
    rename is made structural."""
    for i, row in enumerate(move_map["moves"]):
        old_path = row["old_path"]
        if show_file_at(base_sha, old_path) is None:
            raise GuardError(
                f"moves[{i}].old_path {old_path!r} does not resolve as a "
                f"real git object at the merge base {base_sha} — refusing "
                f"to trust the row"
            )
        new_path = row["new_path"]
        if not (REPO_ROOT / new_path).is_file():
            raise GuardError(
                f"moves[{i}].new_path {new_path!r} does not resolve to a "
                f"real file in the candidate tree — refusing to trust the "
                f"row"
            )


# ---------- AST normalization + comparison ---------------------------


class _StructuralRename(ast.NodeTransformer):
    """Rename ONLY identifier-bearing AST nodes — `ast.Name`, `ast.arg`,
    `ast.alias` (`import X` / `import X as Y` / `from M import X as Y`),
    `FunctionDef`/`AsyncFunctionDef`/`ClassDef.name`, `Attribute.attr`,
    and `ImportFrom.module` (a dotted module-path string, per the spec's
    "module-path nodes"). `ast.Constant` — where license PROSE lives —
    is never visited by name here (NodeTransformer's generic_visit still
    walks into it, but there is no visit_Constant override, so it is
    returned unchanged). This is the fix for a real laundering channel:
    the previous implementation did a raw `str.replace` on the whole
    `ast.dump()` TEXT, so a `moves` row with `old_symbol="REPORTS"` /
    `new_symbol="REFUSES"` silently rewrote a `Constant` string
    containing the substring "REPORTS" too — inverting a refusal
    sentence while the guard reported a clean structural relocation.
    Confirmed via a synthetic repro before this fix; regression-tested
    after it (see test_check_claim_license_guard.py)."""

    def __init__(self, renames: dict[str, str]):
        self.renames = renames

    def _rename(self, name: str) -> str:
        return self.renames.get(name, name)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        self.generic_visit(node)
        node.id = self._rename(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        self.generic_visit(node)
        node.arg = self._rename(node.arg)
        return node

    def visit_alias(self, node: ast.alias) -> ast.AST:
        node.name = self._rename(node.name)
        if node.asname is not None:
            node.asname = self._rename(node.asname)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.name = self._rename(node.name)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.generic_visit(node)
        node.name = self._rename(node.name)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        node.attr = self._rename(node.attr)
        return node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        self.generic_visit(node)
        if node.module is not None:
            # Rename a whole-module dotted path only on an exact match —
            # a partial/segment rename would require splitting on "."
            # and is not a shape the spec's `moves` rows describe (a
            # `moves` row relocates one file/symbol, not a package
            # segment).
            node.module = self._rename(node.module)
        return node


def _normalized_dump(
    tree: ast.AST, moves: list[dict[str, Any]], rewrites: list[dict[str, Any]]
) -> str:
    """`ast.dump(..., include_attributes=False)` of a STRUCTURALLY
    renamed copy of `tree` — every `moves` row's `old_symbol ->
    new_symbol` is applied by renaming AST identifier nodes (never by
    substring-replacing the dumped text; see `_StructuralRename`), so a
    rename can never reach into a string literal's contents no matter
    what symbol names a row picks. `path_rewrites` keeps its existing,
    narrower mechanism (an exact declared `old_ast -> new_ast`
    expression substitution, verified in scratch copies per spec §3) —
    that one isn't the laundering channel this fix addresses, since its
    rows are exact multi-token expressions, not short bare symbols."""
    if moves:
        tree = copy.deepcopy(tree)
        renames = {row["old_symbol"]: row["new_symbol"] for row in moves}
        tree = _StructuralRename(renames).visit(tree)
        ast.fix_missing_locations(tree)
    dumped = ast.dump(tree, include_attributes=False)
    for row in rewrites:
        dumped = dumped.replace(row["old_ast"], row["new_ast"])
    return dumped


@dataclass
class ModuleDelta:
    path: str
    detail: str


def compare_protected_modules(
    base_sha: str,
    protected: list[ProtectedModule],
    move_map: dict[str, Any],
) -> list[ModuleDelta]:
    deltas: list[ModuleDelta] = []
    moves = move_map["moves"]
    rewrites = move_map["path_rewrites"]
    moved_new_paths = {r["new_path"] for r in moves}
    moved_old_paths = {r["old_path"] for r in moves}

    # Every protected object must exist at the merge base (unless it is
    # the declared NEW path of a move) and in the candidate (unless it is
    # the declared OLD path of a move that deleted it).
    for pm in protected:
        candidate_path = REPO_ROOT / pm.path
        base_content = show_file_at(base_sha, pm.path)

        if base_content is None and pm.path not in moved_new_paths:
            deltas.append(ModuleDelta(
                pm.path,
                "protected module does not exist at the merge base and is "
                "not a declared move destination (packaging_move_map.json "
                "moves[].new_path) — an unlisted new protected file",
            ))
            continue
        if not candidate_path.is_file() and pm.path not in moved_old_paths:
            deltas.append(ModuleDelta(
                pm.path,
                "protected module is missing from the candidate and is not "
                "a declared move source (packaging_move_map.json "
                "moves[].old_path) — an unlisted deletion",
            ))
            continue
        if base_content is None:
            continue  # legitimate move destination; the OLD path's row covers it

        try:
            base_tree = ast.parse(base_content, filename=f"{base_sha}:{pm.path}")
        except SyntaxError as exc:
            deltas.append(ModuleDelta(pm.path, f"merge-base copy fails to parse: {exc}"))
            continue
        if not candidate_path.is_file():
            continue
        try:
            candidate_tree = ast.parse(
                candidate_path.read_text(encoding="utf-8"), filename=str(candidate_path)
            )
        except SyntaxError as exc:
            deltas.append(ModuleDelta(pm.path, f"candidate copy fails to parse: {exc}"))
            continue

        base_dump = _normalized_dump(base_tree, moves, rewrites)
        cand_dump = _normalized_dump(candidate_tree, moves, rewrites)
        if base_dump != cand_dump:
            deltas.append(ModuleDelta(
                pm.path,
                "normalized whole-module AST differs from the merge-base "
                "version and the delta is not explained by a declared "
                "move/path_rewrite — a semantic claim-license change",
            ))
    return deltas


# ---------- CLI --------------------------------------------------------


def run(base_ref: str) -> tuple[bool, dict[str, Any]]:
    base_sha = merge_base(base_ref)
    move_map = load_move_map()
    validate_move_map_paths(move_map, base_sha)
    protected = build_protected_set()
    deltas = compare_protected_modules(base_sha, protected, move_map)
    passed = not deltas
    report = {
        "passed": passed,
        "base_sha": base_sha,
        "protected_module_count": len(protected),
        "protected_modules": [p.path for p in protected],
        "deltas": [{"path": d.path, "detail": d.detail} for d in deltas],
    }
    return passed, report


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="No-change claim-license guard (spec §3 deficit lock).",
    )
    parser.add_argument(
        "--base-ref", default="origin/main",
        help="Ref to compute the merge base against (default origin/main).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        passed, report = run(args.base_ref)
    except GuardError as exc:
        if args.json:
            print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if passed else 1

    print(
        f"Merge base: {report['base_sha']}. "
        f"Protected claim-license module(s): {report['protected_module_count']}."
    )
    if passed:
        print("No semantic claim-license delta. ✔")
        return 0
    print(f"\n{len(report['deltas'])} claim-license delta(s):\n")
    for d in report["deltas"]:
        print(f"  {d['path']}: {d['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
