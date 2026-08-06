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
spec.** P1 authorizes no relocation row at all; a changed license string,
a changed `model_id`, a changed comparison set, an addition, or a deletion
fails CI with NO override. P2 must add narrowly scoped relocation support
alongside its first real move.

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

Comparison (spec §3): derive the protected closure independently from the
merge base and candidate. For every merge-base protected module, `ast.dump(...,
include_attributes=False)` the merge-base version and the mapped
candidate version. This keeps docstrings, literals, operators, calls,
keyword names/values, collection order, defaults, and control flow —
so even an unchanged `_claim_license` function whose CALLER passes a
different `model_id` is caught. Candidate-only protected modules and
base-only protected modules fail too. The P1 move map must remain empty.

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
import tempfile
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
    scripts_root: Path | None = None,
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
    if scripts_root is None:
        scripts_root = SCRIPTS_ROOT
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
        base_dir = scripts_root.joinpath(*parts)

    flat = base_dir.with_suffix(".py")
    try:
        flat.relative_to(scripts_root)
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
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.value is not None
                and _CLAIM_LICENSE_NAME_RE.search(node.target.id)
            ):
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
    repo_root: Path | None = None,
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
    if repo_root is None:
        repo_root = REPO_ROOT
    protected: dict[str, ProtectedModule] = {}
    file_trees: dict[str, ast.Module] = {}
    all_files = sorted(
        p for p in scripts_root.rglob("*.py")
        if not any(part in _EXCLUDED_DIR_PARTS for part in p.relative_to(scripts_root).parts)
    )

    def _parse(path: Path) -> ast.Module | None:
        rel = path.relative_to(repo_root).as_posix()
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
            rel = path.relative_to(repo_root).as_posix()
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
        path = repo_root / rel
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
                target = _module_path_for_import(
                    path, module_dotted, level, symbol, scripts_root=scripts_root)
                if target is None:
                    continue  # not resolvable to a plugin-local file (stdlib/3rd-party)
                target_rel = target.relative_to(repo_root).as_posix()
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
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise GuardError(f"{path}: `schema` must be the integer 1")
    moves = data["moves"]
    rewrites = data["path_rewrites"]
    if not isinstance(moves, list) or not isinstance(rewrites, list):
        raise GuardError(f"{path}: `moves` and `path_rewrites` must be lists")
    # P1 has no production moves. Rejecting future authorization rows is
    # intentionally smaller and safer than shipping a generic normalizer
    # before there is a real relocation to test it against. P2 must extend
    # this checker in the same PR as its first concrete move.
    if moves or rewrites:
        raise GuardError(
            f"{path}: P1 requires empty moves/path_rewrites; add narrowly "
            "scoped normalization with the first P2 relocation"
        )
    return data


@dataclass
class ModuleDelta:
    path: str
    detail: str


def compare_protected_modules(
    base_sha: str,
    base_protected: list[ProtectedModule],
    candidate_protected: list[ProtectedModule],
    move_map: dict[str, Any],
) -> list[ModuleDelta]:
    deltas: list[ModuleDelta] = []
    if move_map["moves"] or move_map["path_rewrites"]:
        raise GuardError("P1 comparison only accepts an empty move map")
    base_paths = {pm.path for pm in base_protected}
    candidate_paths = {pm.path for pm in candidate_protected}

    # Candidate-only protected modules are additive claim-license surface,
    # not a harmless omission from a candidate-derived census.
    for path in sorted(candidate_paths - base_paths):
        deltas.append(ModuleDelta(
            path,
            "candidate adds a protected claim-license module that does not "
            "exist in the merge-base protected set",
        ))

    for pm in base_protected:
        candidate_path = REPO_ROOT / pm.path
        base_content = show_file_at(base_sha, pm.path)
        if base_content is None:
            deltas.append(ModuleDelta(
                pm.path,
                "protected merge-base module cannot be read",
            ))
            continue
        if not candidate_path.is_file():
            deltas.append(ModuleDelta(
                pm.path,
                "protected merge-base module is missing from the candidate",
            ))
            continue

        try:
            base_tree = ast.parse(base_content, filename=f"{base_sha}:{pm.path}")
        except SyntaxError as exc:
            deltas.append(ModuleDelta(pm.path, f"merge-base copy fails to parse: {exc}"))
            continue
        try:
            candidate_tree = ast.parse(
                candidate_path.read_text(encoding="utf-8"), filename=str(candidate_path)
            )
        except SyntaxError as exc:
            deltas.append(ModuleDelta(pm.path, f"candidate copy fails to parse: {exc}"))
            continue

        base_dump = ast.dump(base_tree, include_attributes=False)
        cand_dump = ast.dump(candidate_tree, include_attributes=False)
        if base_dump != cand_dump:
            deltas.append(ModuleDelta(
                pm.path,
                "whole-module AST differs from the merge-base version — "
                "P1 authorizes no claim-license or relocation delta",
            ))
    return deltas


def build_protected_set_at_revision(sha: str) -> list[ProtectedModule]:
    """Materialize the merge-base script tree and derive its own closure."""
    script_rel = "plugins/setec-voiceprint/scripts"
    with tempfile.TemporaryDirectory(prefix="setec_claim_guard_base_") as raw:
        root = Path(raw)
        for rel in sorted(list_tree_py_files(sha, script_rel)):
            content = show_file_at(sha, rel)
            if content is None:
                raise GuardError(f"cannot read {sha}:{rel}")
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return build_protected_set(
            root / script_rel,
            repo_root=root,
        )


# ---------- CLI --------------------------------------------------------


def run(base_ref: str) -> tuple[bool, dict[str, Any]]:
    base_sha = merge_base(base_ref)
    move_map = load_move_map()
    base_protected = build_protected_set_at_revision(base_sha)
    candidate_protected = build_protected_set()
    deltas = compare_protected_modules(
        base_sha, base_protected, candidate_protected, move_map)
    passed = not deltas
    report = {
        "passed": passed,
        "base_sha": base_sha,
        "protected_module_count": len(base_protected),
        "protected_modules": [p.path for p in base_protected],
        "candidate_protected_modules": [p.path for p in candidate_protected],
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
