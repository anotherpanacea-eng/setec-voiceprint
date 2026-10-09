#!/usr/bin/env python3
"""Refuse new copies of registered text primitives.

`setec.core.textprims.PRIMITIVES` names the one owner module of each shared
text primitive. A function defined anywhere else in the plugin with the same
code (same body and parameters up to renaming, ignoring its name, annotations
and docstring, and the same
values for the module-level names it reads) is a copy that should import the
registered one instead. Likewise a module-level set literal equal to a
registered word set. Same-named functions with different code are unrelated
and are not reported.

Behavior itself is pinned by `references/textprims/characterization.json`
(see `tools/run_textprims_characterization.py`).

Usage: python3 tools/gen_textprims_inventory.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/setec-voiceprint/scripts"
REGISTRY = SCRIPTS / "setec/core/textprims.py"


def module_path(root: Path, module: str) -> Path:
    return root / "plugins/setec-voiceprint/scripts" / (module.replace(".", "/") + ".py")


def registry(root: Path) -> dict[str, str]:
    """Read PRIMITIVES from the registry source without importing it."""
    tree = ast.parse((root / REGISTRY.relative_to(ROOT)).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PRIMITIVES" for t in node.targets):
            return ast.literal_eval(node.value.args[0])
    raise SystemExit("textprims.PRIMITIVES not found")


def _globals(tree: ast.Module) -> dict[str, str]:
    """Module-level single-name assignments and functions, as AST dumps."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.dump(node.value)
        elif isinstance(node, ast.FunctionDef):
            out[node.name] = ast.dump(ast.Module(body=node.body, type_ignores=[]))
    return out


class _Rename(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]):
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.copy_location(ast.Name(self.names.get(node.id, node.id), node.ctx), node)


def fingerprint(func: ast.FunctionDef, module_globals: dict[str, str]) -> str:
    """Same code up to parameter names, annotations, the function name and docstring."""
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    a = func.args
    params = [*a.posonlyargs, *a.args, *([a.vararg] if a.vararg else []), *a.kwonlyargs, *([a.kwarg] if a.kwarg else [])]
    names = {p.arg: f"_p{i}" for i, p in enumerate(params)}
    body = [_Rename(names).visit(ast.parse(ast.unparse(stmt)).body[0]) for stmt in body]
    reads = sorted({n.id for n in ast.walk(ast.Module(body=body, type_ignores=[])) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in module_globals})
    signature = [len(a.posonlyargs), len(a.args), bool(a.vararg), len(a.kwonlyargs), bool(a.kwarg),
                 [ast.dump(d) for d in a.defaults], [ast.dump(d) if d else None for d in a.kw_defaults]]
    return json.dumps([signature, [ast.dump(s) for s in body], [(r, module_globals[r]) for r in reads]])


def word_set(node: ast.AST) -> frozenset | None:
    if isinstance(node, ast.Set) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return frozenset(e.value for e in node.elts)
    return None


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    functions: dict[str, tuple[str, str, int, int]] = {}   # fingerprint -> (name, owner file, definition line, column)
    sets: dict[frozenset, tuple[str, str, int, int]] = {}
    for name, owner in registry(root).items():
        path = module_path(root, owner)
        if not path.is_file():
            errors.append(f"registered owner missing: {name} -> {owner}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_globals = _globals(tree)
        found = False
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                functions[fingerprint(node, module_globals)] = (name, path.relative_to(root).as_posix(), node.lineno, node.col_offset)
                found = True
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                words = word_set(node.value)
                if words is not None:
                    sets[words] = (name, path.relative_to(root).as_posix(), node.lineno, node.col_offset)
                found = True
        if not found:
            errors.append(f"registered owner does not define {name}: {owner}")
    for path in sorted((root / "plugins/setec-voiceprint/scripts").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if "/tests/" in relative or "__pycache__" in relative:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_globals = _globals(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                hit = functions.get(fingerprint(node, module_globals))
            elif isinstance(node, ast.Assign):
                words = word_set(node.value)
                hit = sets.get(words) if words is not None else None
            else:
                continue
            # Exempt only the actual registered definition, not its whole file.
            if hit and hit[1:] != (relative, node.lineno, node.col_offset):
                errors.append(f"copy of registered primitive {hit[0]} at {relative}:{node.lineno}; import it from setec.core.textprims")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if any copy or broken owner is found")
    parser.add_argument("--base", help="accepted for the existing CI step; unused")
    args = parser.parse_args(argv)
    errors = check()
    for error in errors:
        print(error)
    if not errors:
        print(f"textprims: {len(registry(ROOT))} registered primitives, no copies found")
    return 1 if errors and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
