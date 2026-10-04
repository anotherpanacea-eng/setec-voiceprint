#!/usr/bin/env python3
"""Conservative source-only registry validation and complete-source discovery.

Registered-cohort violations fail; remaining sites require independent review.
This first implementation deliberately does not claim
semantic classification merely because a function has a familiar name.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OWNER = "plugins/setec-voiceprint/scripts/setec/core/textprims.py"
MAPS = {"TOKENIZERS", "SENTENCE_SPLITTERS", "PARAGRAPH_SPLITTERS", "FUNCTION_WORD_SETS", "QUANTILES", "FINGERPRINTS", "PREPROCESSORS"}
FIELDS = {"id", "family", "implementation_ref", "pattern_sha256", "case_policy", "unicode_normalization", "allowed_backends", "behavior_sha256"}
REGEX_CALLS = {"compile", "split", "findall", "finditer", "sub", "subn", "search", "match", "fullmatch"}


def source(node, text):
    return "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def registry(text):
    tree = ast.parse(text)
    result, assigned = {}, set()
    factories = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "types" for a in n.names if a.name == "MappingProxyType" and a.asname == "_MappingProxyType"]
    map_nodes = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in MAPS for t in n.targets)]
    if not map_nodes:
        return result
    if len(factories) != 1:
        raise ValueError("immutable factory must have one direct types binding")
    stores = {}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names = [node.id]
        elif isinstance(node, ast.arg):
            names = [node.arg]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.asname or a.name.split(".")[0] for a in node.names]
        for name in names:
            if name in MAPS or name == "_MappingProxyType":
                stores[name] = stores.get(name, 0) + 1
    if stores.get("_MappingProxyType") != 1 or any(stores.get(name) != 1 for name in MAPS):
        raise ValueError("registry map or immutable factory rebound")
    families = dict(zip(("TOKENIZERS", "SENTENCE_SPLITTERS", "PARAGRAPH_SPLITTERS", "FUNCTION_WORD_SETS", "QUANTILES", "FINGERPRINTS", "PREPROCESSORS"), ("tokenizer", "sentence_splitter", "paragraph_splitter", "function_words", "quantile", "fingerprint", "preprocessor")))
    for node in map_nodes:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise ValueError("closed registry assignment required")
        name = node.targets[0].id
        if name in assigned:
            raise ValueError("repeated registry map")
        assigned.add(name)
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "_MappingProxyType" or len(value.args) != 1 or value.keywords or not isinstance(value.args[0], ast.Dict):
            raise ValueError("registry must be a closed immutable literal map")
        for key, row in zip(value.args[0].keys, value.args[0].values):
            if not isinstance(row, ast.Call) or not isinstance(row.func, ast.Name) or row.func.id != "_MappingProxyType" or len(row.args) != 1 or row.keywords or not isinstance(row.args[0], ast.Dict):
                raise ValueError("registry rows must be immutable literals")
            field_names = [ast.literal_eval(k) for k in row.args[0].keys]
            if len(set(field_names)) != len(field_names):
                raise ValueError("duplicate row field")
            entry = ast.literal_eval(row.args[0])
            symbol = ast.literal_eval(key)
            if type(symbol) is not str or symbol in result:
                raise ValueError("invalid or duplicate registry symbol")
            if entry.get("family") != families[name]:
                raise ValueError("registry family does not match its closed map")
            result[symbol] = entry
    return result


def verify_rows(candidate, baseline):
    rows, old = registry(candidate), registry(baseline)
    errors = []
    if not {row["id"] for row in old.values()} <= {row["id"] for row in rows.values()}:
        errors.append("merge-base registry obligation removed")
    tree = ast.parse(candidate)
    definitions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    old_definitions = {n.name: n for n in ast.parse(baseline).body if isinstance(n, ast.FunctionDef)}
    pattern = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SENT_RE" for t in n.targets))
    pattern_bytes = ast.literal_eval(pattern.value.args[0]).encode()
    old_pattern = next(n for n in ast.parse(baseline).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SENT_RE" for t in n.targets))
    if source(pattern, candidate) != source(old_pattern, baseline):
        errors.append("compiled pattern declaration changed during ownership-only increment")
    protected = set(rows) | {"_SENT_RE"}
    for name in protected:
        writes = [n for n in ast.walk(tree) if (isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.id == name) or (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name) or (isinstance(n, ast.arg) and n.arg == name)]
        writes.extend(n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name.split(".")[0]) == name for a in n.names))
        if len(writes) != 1:
            errors.append("registered owner or dependency rebound: " + name)
    seen = set()
    for symbol, row in rows.items():
        if set(row) != FIELDS or row["id"] in seen:
            errors.append("invalid or duplicate row: " + symbol)
            continue
        if row["case_policy"] not in {"preserve", "lower", "casefold", "not_applicable"} or row["unicode_normalization"] not in {"none", "NFC", "NFKC", "frozen_table", "not_applicable"} or type(row["allowed_backends"]) is not tuple or row["allowed_backends"] not in ((), ("nltk",)):
            errors.append("invalid closed behavior policy: " + symbol)
            continue
        seen.add(row["id"])
        if row["implementation_ref"] != OWNER + ":" + symbol or symbol not in definitions:
            errors.append("unresolved final owner: " + symbol)
            continue
        defining = source(definitions[symbol], candidate)
        if symbol not in old_definitions or defining != source(old_definitions[symbol], baseline):
            errors.append("defining callable changed during ownership-only increment: " + symbol)
        fields = {k: v for k, v in row.items() if k not in {"id", "behavior_sha256"}}
        fields["allowed_backends"] = list(fields["allowed_backends"])
        payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n" + defining.encode()
        if symbol == "split_sentences_regex":
            if row["pattern_sha256"] != hashlib.sha256(pattern_bytes).hexdigest():
                errors.append("pattern digest mismatch")
            payload += b"\n" + source(pattern, candidate).encode()
        digest = hashlib.sha256(payload).hexdigest()
        if row["behavior_sha256"] != digest or row["id"] != row["family"] + "-" + digest[:12] + "-v1":
            errors.append("behavior digest mismatch: " + symbol)
        if symbol in old and row != old[symbol]:
            errors.append("registered behavior fields changed: " + symbol)
    return rows, errors


def discover(root):
    discoveries = []
    for path in sorted((root / "plugins/setec-voiceprint/scripts").rglob("*.py")):
        if "tests" in path.relative_to(root).parts or "__pycache__" in path.parts:
            continue
        text = path.read_text()
        tree = ast.parse(text)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        re_modules, re_functions = set(), {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                re_modules.update(a.asname or a.name for a in node.names if a.name == "re")
            if isinstance(node, ast.ImportFrom) and node.module == "re":
                re_functions.update({a.asname or a.name: a.name for a in node.names})
        stores = {}
        for binding in ast.walk(tree):
            if isinstance(binding, ast.Name) and isinstance(binding.ctx, ast.Store):
                stores[binding.id] = stores.get(binding.id, 0) + 1
            elif isinstance(binding, ast.arg):
                stores[binding.arg] = stores.get(binding.arg, 0) + 1
            elif isinstance(binding, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stores[binding.name] = stores.get(binding.name, 0) + 1
        shadowed_imports = {name for name in re_modules | set(re_functions) if stores.get(name, 0)}
        compiled = {}
        for binding in ast.walk(tree):
            if isinstance(binding, ast.Assign) and isinstance(binding.value, ast.Call):
                call = binding.value
                is_compile = (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in re_modules and call.func.attr == "compile") or (isinstance(call.func, ast.Name) and re_functions.get(call.func.id) == "compile")
                if is_compile and call.args and not (isinstance(call.func, ast.Name) and call.func.id in shadowed_imports) and not (isinstance(call.func, ast.Attribute) and call.func.value.id in shadowed_imports):
                    try:
                        literal = ast.literal_eval(call.args[0])
                    except (ValueError, TypeError):
                        continue
                    for target in binding.targets:
                        if isinstance(target, ast.Name) and stores.get(target.id) == 1:
                            compiled[target.id] = literal
        extraction_patterns = {r"\b\w+\b", r"\w+", r"\S+", r"[A-Za-z']+", r"[a-zA-Z]+", r"[a-z]+", r"[A-Za-z]+"}
        for node in ast.walk(tree):
            candidate_kind = None
            candidate_owner = "<module>"
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(term in node.name.lower() for term in ("token", "split", "quantile", "percentile", "fingerprint", "preprocess", "function_word")):
                candidate_kind = "named_primitive_candidate"
                candidate_owner = node.name
            elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Set, ast.List, ast.Tuple)) and node.value.elts and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.value.elts):
                candidate_kind = "literal_string_table_candidate"
                candidate_owner = ",".join(ast.unparse(t) for t in node.targets)
            if candidate_kind:
                discoveries.append({"path": path.relative_to(root).as_posix(), "line": node.lineno, "owner": candidate_owner, "operation": candidate_kind, "pattern": None, "outcome": "unresolved", "reason": "candidate syntax only; semantic role requires independent review"})
            operation = None
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in re_modules and node.func.attr in REGEX_CALLS:
                    operation = "regex." + node.func.attr
                elif isinstance(node.func, ast.Name) and node.func.id in re_functions:
                    operation = "regex." + re_functions[node.func.id]
                elif isinstance(node.func, ast.Attribute) and node.func.attr in {"split", "findall", "finditer", "sub", "subn"}:
                    operation = "possible_compiled_pattern." + node.func.attr
                elif isinstance(node.func, ast.Attribute) and node.func.attr in {"quantile", "quantiles", "percentile", "sha256", "sha1", "digest", "hexdigest", "normalize", "strip", "lstrip", "rstrip", "lower", "casefold", "replace"}:
                    operation = "possible_primitive." + node.func.attr
                elif isinstance(node.func, ast.Name) and any(term in node.func.id.lower() for term in ("quantile", "percentile", "fingerprint", "preprocess", "strip_non_prose")):
                    operation = "possible_primitive." + node.func.id
            if not operation:
                continue
            parent = node
            while parent in parents and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents[parent]
            owner = parent.name if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            relative = path.relative_to(root).as_posix()
            recognized = relative == OWNER and (owner == "split_sentences_regex" or (operation == "regex.compile" and source(node, text).strip().startswith("_SENT_RE =")))
            reason = "existing sentence-splitter owner" if recognized else "input/output provenance and static binding not yet proved"
            pattern = None
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in compiled:
                pattern = compiled[node.func.value.id]
            elif operation.startswith("regex.") and node.args:
                try:
                    pattern = ast.literal_eval(node.args[0])
                except (ValueError, TypeError):
                    pass
            binding_shadowed = (isinstance(node.func, ast.Name) and node.func.id in shadowed_imports) or (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in shadowed_imports)
            if operation.endswith((".findall", ".finditer")) and type(pattern) is str and pattern in extraction_patterns and not binding_shadowed:
                # Extraction syntax alone does not prove prose provenance;
                # metadata strings can be subjected to the same operation.
                reason = "literal unit extraction proved; input and consumer provenance unresolved"
            if binding_shadowed:
                recognized = False
                reason = "regex import may be rebound or shadowed"
            discoveries.append({"path": relative, "line": node.lineno, "owner": owner, "operation": operation, "pattern": ({"bytes_hex": pattern.hex()} if isinstance(pattern, bytes) else pattern), "outcome": "recognized_primitive" if recognized else "unresolved", "reason": reason})
    # Attach source-derived construction and direct consumer locations. These
    # are inspection evidence, not a claim of semantic provenance inference.
    by_path = {}
    for row in discoveries:
        by_path.setdefault(row["path"], []).append(row)
    for relative, rows in by_path.items():
        text = (root / relative).read_text()
        tree = ast.parse(text)
        nodes = list(ast.walk(tree))
        parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
        for row in rows:
            matching = [n for n in nodes if getattr(n, "lineno", None) == row["line"] and isinstance(n, (ast.Call, ast.Assign, ast.FunctionDef, ast.AsyncFunctionDef))]
            node = next((n for n in matching if isinstance(n, ast.Call)), matching[0] if matching else None)
            if node is None:
                row["construction"] = "unresolved"
                row["direct_consumers"] = []
                continue
            row["construction"] = ast.unparse(node)[:500] if isinstance(node, (ast.Call, ast.Assign)) else "definition " + node.name
            consumers = []
            parent = parents.get(node)
            if parent is not None:
                consumers.append({"line": getattr(parent, "lineno", row["line"]), "kind": type(parent).__name__})
            binding = node if isinstance(node, ast.Assign) else parent
            names = {t.id for t in binding.targets if isinstance(t, ast.Name)} if isinstance(binding, ast.Assign) else ({node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set())
            for use in nodes:
                if isinstance(use, ast.Name) and isinstance(use.ctx, ast.Load) and use.id in names:
                    context = parents.get(use)
                    consumers.append({"line": use.lineno, "kind": type(context).__name__, "binding": use.id})
            row["direct_consumers"] = consumers
    return discoveries


def bindings(root, symbols):
    """Validate static owner imports and reject replacement/shadow bindings."""
    imports, errors = {}, []
    for path in sorted((root / "plugins/setec-voiceprint/scripts").rglob("*.py")):
        if "tests" in path.relative_to(root).parts or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text())
        imported, modules, all_imports = {}, {}, []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    all_imports.append((local, node))
                    if isinstance(node, ast.ImportFrom) and alias.name in symbols:
                        if node.module != "setec.core.textprims" or node.level:
                            errors.append(f"unresolved registered import: {relative}:{node.lineno}")
                        imported[local] = alias.name
                        imports[(relative, local)] = alias.name
                    if (isinstance(node, ast.Import) and alias.name == "setec.core.textprims") or (isinstance(node, ast.ImportFrom) and node.module == "setec.core" and alias.name == "textprims" and not node.level):
                        modules[local] = node.lineno
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in symbols and relative != OWNER:
                errors.append(f"duplicate registered owner: {relative}:{node.lineno}")
        tracked = set(imported) | set(modules)
        for local in tracked:
            if sum(name == local for name, _ in all_imports) != 1 or any(name == "*" for name, _ in all_imports):
                errors.append(f"replacement import may rebind registered binding: {relative}:{local}")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in tracked) or (isinstance(node, ast.arg) and node.arg in tracked) or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in tracked):
                errors.append(f"rebound registered import: {relative}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in symbols and isinstance(node.ctx, (ast.Store, ast.Del)):
                errors.append(f"registered attribute replacement: {relative}:{node.lineno}")
    return imports, errors


def check(root, base):
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], cwd=root, text=True).strip()
    baseline = subprocess.check_output(["git", "show", merge_base + ":" + OWNER], cwd=root, text=True)
    rows, errors = verify_rows((root / OWNER).read_text(), baseline)
    imports, binding_errors = bindings(root, set(rows))
    errors.extend(binding_errors)
    for symbol in sorted(set(rows) - set(imports.values())):
        errors.append("registered row never imported: " + symbol)
    fixture = json.loads((root / "references/textprims/characterization.json").read_text())
    fixture_ids = {row["registry_id"] for row in fixture["rows"]}
    if fixture_ids != {row["id"] for row in rows.values()}:
        errors.append("fixture and cumulative registry coverage differ")
    # Existing compatibility imports are baseline obligations even when the
    # candidate deletes their containing file or renames the imported binding.
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", merge_base, "plugins/setec-voiceprint/scripts"], cwd=root, text=True).splitlines()
    for path in paths:
        if not path.endswith(".py") or "tests" in Path(path).parts:
            continue
        old_text = subprocess.check_output(["git", "show", merge_base + ":" + path], cwd=root, text=True)
        if not any(symbol in old_text for symbol in rows):
            continue
        for node in ast.walk(ast.parse(old_text)):
            if isinstance(node, ast.ImportFrom) and node.module == "setec.core.textprims":
                for alias in node.names:
                    if alias.name in rows and imports.get((path, alias.asname or alias.name)) != alias.name:
                        errors.append("merge-base compatibility binding removed: " + path + ":" + alias.name)
    import_sites = []
    for (path, local), symbol in sorted(imports.items()):
        tree = ast.parse((root / path).read_text())
        line = next(n.lineno for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and any(a.name == symbol and (a.asname or a.name) == local for a in n.names))
        import_sites.append({"path": path, "line": line, "local_binding": local, "registered_symbol": symbol})
    discoveries = discover(root)
    unresolved = [d for d in discoveries if d["outcome"] == "unresolved"]
    return {"registered_rows": len(rows), "enforced_imports": len(imports), "registered_import_sites": import_sites, "discoveries": discoveries, "unresolved_count": len(unresolved), "errors": errors, "complete": False, "scope": "cumulative registered cohort checks only; independent remaining-site review and full reconciliation required"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", required=True)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    result = check(ROOT, args.base)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(1 if result["errors"] else 0)
