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

  1. Seed: every plugin-runtime module whose AST defines a function or
     assigns a name matching `_claim_license*` (substring match,
     case-sensitive — covers `_claim_license`, `_claim_license_dict`,
     `_claim_license_block`, `_structured_claim_license`, ...), OR
     directly calls `ClaimLicense(...)`.
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
relocation declared in `packaging_move_map.json`'s `moves` (a straight
substitution of `old_path`/`old_symbol` -> `new_path`/`new_symbol`
throughout the dumped tree) and the exact verified data-anchor
substitution declared in `path_rewrites`.

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
    importing_file: Path, module_dotted: str, level: int
) -> Path | None:
    """Best-effort resolution of a plugin-local import target to an
    on-disk `.py` file under SCRIPTS_ROOT. Returns None for anything
    that isn't a plain plugin-local module (stdlib, third-party,
    package-relative we can't resolve, etc.) — the caller treats an
    unresolved-but-suspicious import conservatively (see
    `_is_plugin_local_candidate`)."""
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
        candidate = base.joinpath(*parts).with_suffix(".py")
    else:
        parts = module_dotted.split(".")
        candidate = SCRIPTS_ROOT.joinpath(*parts).with_suffix(".py")
    try:
        candidate.relative_to(SCRIPTS_ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


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


def _resolve_symbol_definition(tree: ast.Module, symbol: str) -> ast.AST | None:
    """The top-level FunctionDef/AsyncFunctionDef/ClassDef/Assign-target
    named `symbol` in `tree`'s body, or None if not found there (e.g. it
    is itself re-exported from elsewhere — the caller falls back to
    whole-module scanning in that case)."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                return node
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == symbol:
                    return node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == symbol:
                return node
    return None


def build_protected_set(
    scripts_root: Path | None = None,
) -> list[ProtectedModule]:
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

    # scan_subtrees[rel]: AST subtrees still needing Name-load resolution
    # for `rel`. A module can be added to `protected` (whole-file, "in
    # full") once, but revisited here MULTIPLE times as different call
    # sites reference different symbols from it — each addition only
    # queues the SPECIFIC referenced symbol's own definition, not the
    # whole file, so an unrelated function elsewhere in a supplier module
    # never drags in ITS OWN unrelated imports as false-positive further
    # suppliers (e.g. narrative_judge.py is a genuine supplier via
    # `fingerprint_prompt()`, referenced from a ClaimLicense call site's
    # comparison_set — but narrative_judge.py's UNRELATED `build_judge()`
    # helper, which happens to import judge_backends, must not itself
    # drag judge_backends.py into the protected set).
    scan_subtrees: dict[str, list[ast.AST]] = {}
    queue: list[str] = []

    def _enqueue(rel: str, subtree: ast.AST, reason: str) -> None:
        if rel not in protected:
            protected[rel] = ProtectedModule(path=rel, reason=reason)
        scan_subtrees.setdefault(rel, []).append(subtree)
        if rel not in queue:
            queue.append(rel)

    # Seed.
    for path in all_files:
        tree = _parse(path)
        if tree is None:
            continue
        subtrees = _claim_license_relevant_subtrees(tree)
        if subtrees:
            rel = path.relative_to(REPO_ROOT).as_posix()
            for sub in subtrees:
                _enqueue(
                    rel, sub,
                    "seed: defines/calls a _claim_license*/ClaimLicense(...) site",
                )

    while queue:
        rel = queue.pop()
        pending = scan_subtrees.pop(rel, [])
        if not pending:
            continue
        path = REPO_ROOT / rel
        tree = _parse(path)
        if tree is None:
            continue
        import_map = _build_import_map(tree)

        if import_map.has_star_import and any(
            n.id not in import_map.bindings
            for sub in pending
            for n in ast.walk(sub)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        ):
            raise GuardError(
                f"{rel}: a star import makes name provenance unresolvable "
                f"inside a protected claim-license subtree — failing closed "
                f"per spec §3 rather than guessing which module supplies "
                f"the name."
            )

        for sub in pending:
            # Bare-module attribute access (`import X` then `X.attr`):
            # resolve to the SPECIFIC attribute, not the whole module —
            # otherwise every attribute access anywhere in a large
            # utility module's usage would look identical to "the whole
            # module matters here."
            attr_by_name: dict[str, set[str]] = {}
            for node in ast.walk(sub):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                ):
                    attr_by_name.setdefault(node.value.id, set()).add(node.attr)

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
                target = _module_path_for_import(path, module_dotted, level)
                if target is None:
                    continue  # not resolvable to a plugin-local file (stdlib/3rd-party)
                target_tree = _parse(target)
                if target_tree is None:
                    continue
                target_rel = target.relative_to(REPO_ROOT).as_posix()

                # `from X import symbol` gives a concrete symbol directly;
                # `import X as name` (symbol is None) needs the attribute
                # access(es) on `name` found above, or — if `name` is used
                # bare with no attribute access at all — there is no
                # single symbol to scope to, so fall back to the whole
                # module (conservative; matches "fails closed" instead of
                # silently narrowing to nothing).
                wanted_symbols = (
                    {symbol} if symbol is not None
                    else (attr_by_name.get(name) or set())
                )
                if not wanted_symbols:
                    _enqueue(
                        target_rel, target_tree,
                        f"supplier: {rel} imports {name!r} from here "
                        f"(used bare, no specific symbol resolvable — "
                        f"whole module scanned)",
                    )
                    continue
                for wanted in wanted_symbols:
                    definition = _resolve_symbol_definition(target_tree, wanted)
                    if definition is None:
                        _enqueue(
                            target_rel, target_tree,
                            f"supplier: {rel} references {name}.{wanted} "
                            f"from here (symbol not found as a top-level "
                            f"def/assign — whole module scanned)",
                        )
                    else:
                        _enqueue(
                            target_rel, definition,
                            f"supplier: {rel} references {name}.{wanted} "
                            f"({wanted!r}) from here",
                        )

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


# ---------- AST normalization + comparison ---------------------------


def _normalized_dump(tree: ast.AST, moves: list[dict[str, Any]], rewrites: list[dict[str, Any]]) -> str:
    """`ast.dump(..., include_attributes=False)`, then apply ONLY the
    substitutions the move map declares: an old_symbol -> new_symbol
    (and old_path -> new_path, for any embedded path-string literal)
    rewrite per `moves` row, and the exact verified `old_ast` ->
    `new_ast` substitution per `path_rewrites` row. P1 ships an empty
    move map, so this is the identity transform today; the substitution
    logic is exercised (and tested) via the module's own unit tests
    using a synthetic non-empty map."""
    dumped = ast.dump(tree, include_attributes=False)
    for row in moves:
        dumped = dumped.replace(row["old_symbol"], row["new_symbol"])
        dumped = dumped.replace(row["old_path"], row["new_path"])
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
