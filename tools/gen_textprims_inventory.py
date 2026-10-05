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
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OWNER = "plugins/setec-voiceprint/scripts/setec/core/textprims.py"
TOKENIZER_OWNER = "plugins/setec-voiceprint/scripts/setec/core/passage_tokenizer_v1.py"
TOKENIZER_DATA = "plugins/setec-voiceprint/scripts/passage_tokenizer_data_v1.json"
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


def verify_rows(candidate, baseline, external_bytes=None, baseline_external_bytes=None):
    external_bytes = external_bytes if external_bytes is not None else {path: (ROOT / path).read_bytes() for path in (TOKENIZER_OWNER, TOKENIZER_DATA)}
    baseline_external_bytes = baseline_external_bytes if baseline_external_bytes is not None else external_bytes
    rows, old = registry(candidate), registry(baseline)
    errors = []
    if not {row["id"] for row in old.values()} <= {row["id"] for row in rows.values()}:
        errors.append("merge-base registry obligation removed")
    tree = ast.parse(candidate)
    def definitions_in(tree):
        definitions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Set) and all(isinstance(v, ast.Constant) and type(v.value) is str for v in node.value.elts):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        definitions[target.id] = node
        return definitions
    definitions = definitions_in(tree)
    old_definitions = definitions_in(ast.parse(baseline))
    pattern = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SENT_RE" for t in n.targets))
    pattern_bytes = ast.literal_eval(pattern.value.args[0]).encode()
    old_pattern = next(n for n in ast.parse(baseline).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SENT_RE" for t in n.targets))
    if source(pattern, candidate) != source(old_pattern, baseline):
        errors.append("compiled pattern declaration changed during ownership-only increment")
    protected = set(rows) | {"_SENT_RE"}
    if "tokenize" in rows:
        protected.add("__getattr__")
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
        if symbol == "tokenize" and row["implementation_ref"] == TOKENIZER_OWNER + ":tokenize":
            if row["family"] != "tokenizer" or row["case_policy"] != "lower" or row["unicode_normalization"] != "frozen_table" or row["allowed_backends"] != ():
                errors.append("invalid frozen tokenizer policy")
            for path in (TOKENIZER_OWNER, TOKENIZER_DATA):
                if external_bytes[path] != baseline_external_bytes[path]:
                    errors.append("frozen tokenizer dependency changed: " + path)
            lazy = ast.parse('def __getattr__(name):\n    if name == "tokenize":\n        from setec.core.passage_tokenizer_v1 import tokenize\n        return tokenize\n    raise AttributeError(name)\n').body[0]
            getters = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "__getattr__"]
            if len(getters) != 1 or ast.dump(getters[0]) != ast.dump(lazy):
                errors.append("frozen tokenizer must lazily reexport its native object")
            if row["pattern_sha256"] != hashlib.sha256(external_bytes[TOKENIZER_DATA]).hexdigest():
                errors.append("frozen tokenizer table digest mismatch")
            fields = {k: v for k, v in row.items() if k not in {"id", "behavior_sha256"}}
            fields["allowed_backends"] = list(fields["allowed_backends"])
            digest = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n" + external_bytes[TOKENIZER_OWNER] + b"\n" + external_bytes[TOKENIZER_DATA]).hexdigest()
            if row["behavior_sha256"] != digest or row["id"] != "tokenizer-" + digest[:12] + "-v1":
                errors.append("frozen tokenizer behavior digest mismatch")
            if symbol in old and row != old[symbol]:
                errors.append("registered behavior fields changed: " + symbol)
            continue
        if row["implementation_ref"] != OWNER + ":" + symbol or symbol not in definitions:
            errors.append("unresolved final owner: " + symbol)
            continue
        if row["family"] == "function_words" and not isinstance(definitions[symbol], ast.Assign):
            errors.append("function-word row must name its literal table: " + symbol)
            continue
        if row["family"] != "function_words" and not isinstance(definitions[symbol], ast.FunctionDef):
            errors.append("callable row must name its defining function: " + symbol)
            continue
        defining = source(definitions[symbol], candidate)
        if row["family"] == "function_words" and row["pattern_sha256"] != hashlib.sha256(defining.encode()).hexdigest():
            errors.append("table digest mismatch: " + symbol)
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
    owner = root / OWNER
    registered = registry(owner.read_text(encoding="utf-8")) if owner.exists() else {}
    registered_refs = {row["implementation_ref"] for row in registered.values()}
    for path in sorted((root / "plugins/setec-voiceprint/scripts").rglob("*.py")):
        if "tests" in path.relative_to(root).parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
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
                discoveries.append({"path": path.relative_to(root).as_posix(), "line": node.lineno, "owner": candidate_owner, "operation": candidate_kind, "pattern": None, "outcome": "recognized_primitive" if path.relative_to(root).as_posix() + ":" + candidate_owner in registered_refs else "unresolved", "reason": "registered defining object" if path.relative_to(root).as_posix() + ":" + candidate_owner in registered_refs else "candidate syntax only; semantic role requires independent review"})
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
    return discoveries


def permanent_alias(tree):
    """Recognize only the existing import-time whole-module launcher form."""
    executable = ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body)
    import_only = ast.dump(ast.parse('__name__ != "__main__"', mode="eval").body)
    branches = [n for n in tree.body if isinstance(n, ast.If) and ast.dump(n.test) in {executable, import_only}]
    if len(branches) != 1:
        return None
    branch = branches[0]
    is_executable = ast.dump(branch.test) == executable
    if is_executable:
        if len(branch.body) != 1 or len(branch.orelse) != 1:
            return None
        replacement = branch.orelse[0]
    else:
        if len(branch.body) != 1 or branch.orelse:
            return None
        replacement = branch.body[0]
    if not isinstance(replacement, ast.Assign) or len(replacement.targets) != 1 or not isinstance(replacement.value, ast.Name):
        return None
    if ast.dump(replacement.targets[0]) != ast.dump(ast.parse('sys.modules[__name__] = _mod').body[0].targets[0]):
        return None
    local = replacement.value.id
    expected_main = ast.parse(f'sys.exit({local}.main())').body[0]
    if is_executable and ast.dump(branch.body[0]) != ast.dump(expected_main):
        return None
    imported = [n for n in tree.body if isinstance(n, ast.ImportFrom) and not n.level for a in n.names if (a.asname or a.name) == local]
    if len(imported) != 1:
        return None
    binding = imported[0]
    alias = next(a for a in binding.names if (a.asname or a.name) == local)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == '__name__') or (isinstance(node, ast.arg) and node.arg == '__name__') or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == '__name__') or (isinstance(node, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name.split('.')[0]) == '__name__' for a in node.names)):
            return None
    for name in ('sys', local):
        writes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                writes.extend((node, a) for a in node.names if (a.asname or a.name.split('.')[0]) == name)
            elif (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id == name) or (isinstance(node, ast.arg) and node.arg == name) or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name):
                return None
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and isinstance(node.value, ast.Name) and node.value.id == name:
                return None
        if len(writes) != 1:
            return None
        if name == 'sys' and (not isinstance(writes[0][0], ast.Import) or writes[0][1].name != 'sys'):
            return None
    if sum(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id == local for n in ast.walk(tree)) != (2 if is_executable else 1):
        return None
    if sum(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == 'sys' and n.attr == 'modules' for n in ast.walk(tree)) != 1:
        return None
    return binding.module + '.' + alias.name, replacement.lineno


def bindings(root, symbols, table_symbols=(), source_texts=None):
    """Resolve direct compatibility re-exports; reject replacement bindings."""
    imports, errors = {}, []
    scripts = root / "plugins/setec-voiceprint/scripts"
    modules_by_name = {}
    if source_texts is None:
        source_texts = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(scripts.rglob("*.py")) if "tests" not in path.relative_to(root).parts and "__pycache__" not in path.parts}
    for relative, text in source_texts.items():
        module = Path(relative).relative_to("plugins/setec-voiceprint/scripts").with_suffix("").as_posix().replace("/", ".")
        modules_by_name[module] = (relative, ast.parse(text))

    def resolve(module, name, seen=()):
        if module == "setec.core.passage_tokenizer_v1" and name == "tokenize" and name in symbols:
            return name
        if module == "setec.core.textprims" and name in symbols:
            return name
        if module not in modules_by_name or (module, name) in seen:
            return None
        tree = modules_by_name[module][1]
        alias = permanent_alias(tree)
        if alias is not None:
            return resolve(alias[0], name, (*seen, (module, name)))
        matches = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and not node.level:
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        matches.append(resolve(node.module, alias.name, (*seen, (module, name))))
        return matches[0] if len(matches) == 1 else None

    for module, (relative, tree) in modules_by_name.items():
        if permanent_alias(tree) is not None:
            for symbol in symbols:
                if resolve(module, symbol) == symbol:
                    imports[(relative, symbol)] = symbol

    mutators = {"add", "clear", "discard", "pop", "remove", "update", "difference_update", "intersection_update", "symmetric_difference_update"}
    for relative, tree in modules_by_name.values():
        imported, owner_modules, external_modules, all_imports = {}, {}, {}, []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    all_imports.append((local, node))
                    if isinstance(node, ast.ImportFrom):
                        symbol = None if node.level else resolve(node.module, alias.name)
                        if symbol is not None or (alias.name in symbols and alias.name != "tokenize"):
                            if symbol is None:
                                errors.append(f"unresolved registered import: {relative}:{node.lineno}")
                                symbol = alias.name
                            imported[local] = symbol
                            imports[(relative, local)] = symbol
                    if "tokenize" in symbols and ((isinstance(node, ast.Import) and alias.name == "setec.core.passage_tokenizer_v1") or (isinstance(node, ast.ImportFrom) and not node.level and node.module == "setec.core" and alias.name == "passage_tokenizer_v1")):
                        prefix = (alias.asname or alias.name) if isinstance(node, ast.Import) else local
                        external_modules[local] = prefix
                        imports[(relative, prefix + ".tokenize")] = "tokenize"
                    if (isinstance(node, ast.Import) and alias.name == "setec.core.textprims") or (isinstance(node, ast.ImportFrom) and node.module == "setec.core" and alias.name == "textprims" and not node.level):
                        owner_modules[local] = (alias.asname or alias.name) if isinstance(node, ast.Import) else local
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in symbols - {"tokenize"} and relative != OWNER:
                errors.append(f"duplicate registered owner: {relative}:{node.lineno}")
            elif isinstance(node, ast.Assign) and relative != OWNER and any(isinstance(t, ast.Name) and t.id in table_symbols for t in node.targets):
                errors.append(f"duplicate registered table owner: {relative}:{node.lineno}")
        tracked = set(imported) | set(owner_modules) | set(external_modules)
        for local in tracked:
            if sum(name == local for name, _ in all_imports) != 1 or any(name == "*" for name, _ in all_imports):
                errors.append(f"replacement import may rebind registered binding: {relative}:{local}")
        table_locals = {local for local, symbol in imported.items() if symbol in table_symbols}
        if relative == OWNER:
            table_locals.update(table_symbols)
        def is_table(receiver):
            return (isinstance(receiver, ast.Name) and receiver.id in table_locals) or (isinstance(receiver, ast.Attribute) and receiver.attr in table_symbols and ast.unparse(receiver.value) in owner_modules.values())

        def escapes_table(value):
            if is_table(value):
                return True
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                return any(escapes_table(item) for item in value.elts)
            if isinstance(value, ast.Dict):
                return any(escapes_table(item) for item in [*value.keys, *value.values] if item is not None)
            if isinstance(value, ast.Starred):
                return escapes_table(value.value)
            return False

        # Only the existing read-only builtins may receive a table directly.
        # Any binding of their names makes that call ambiguous, even in a nested scope.
        shadowed_reads = {local for local, _ in all_imports if local in {"len", "sorted"}}
        for binding in ast.walk(tree):
            if isinstance(binding, ast.Name) and isinstance(binding.ctx, (ast.Store, ast.Del)) and binding.id in {"len", "sorted"}:
                shadowed_reads.add(binding.id)
            elif isinstance(binding, ast.arg) and binding.arg in {"len", "sorted"}:
                shadowed_reads.add(binding.arg)
            elif isinstance(binding, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and binding.name in {"len", "sorted"}:
                shadowed_reads.add(binding.name)
        def is_external_module(value):
            return value is not None and ast.unparse(value) in external_modules.values()

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and is_external_module(node.value):
                errors.append(f"registered external callable replacement: {relative}:{node.lineno}")
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and is_external_module(node.value) and not (permanent_alias(tree) is not None and node.lineno == permanent_alias(tree)[1]):
                errors.append(f"unresolved registered module alias: {relative}:{node.lineno}")
            if isinstance(node, ast.Call) and any(is_external_module(arg) for arg in [*node.args, *(keyword.value for keyword in node.keywords)]):
                errors.append(f"unresolved registered module argument: {relative}:{node.lineno}")
            if isinstance(node, ast.Call) and any(escapes_table(arg) for arg in [*node.args, *(keyword.value for keyword in node.keywords)]):
                safe_read = isinstance(node.func, ast.Name) and node.func.id in {"len", "sorted"} - shadowed_reads and len(node.args) == 1 and is_table(node.args[0]) and not node.keywords
                if not safe_read:
                    errors.append(f"unresolved registered table argument: {relative}:{node.lineno}")
            if (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in tracked) or (isinstance(node, ast.arg) and node.arg in tracked) or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in tracked):
                errors.append(f"rebound registered import: {relative}:{node.lineno}")
            if isinstance(node, ast.Attribute) and node.attr in symbols and isinstance(node.ctx, (ast.Store, ast.Del)):
                errors.append(f"registered attribute replacement: {relative}:{node.lineno}")
            if isinstance(node, ast.Attribute) and is_table(node.value) and node.attr in mutators:
                errors.append(f"registered table mutation: {relative}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args and is_table(node.args[0]):
                errors.append(f"unresolved registered table method: {relative}:{node.lineno}")
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return, ast.Yield, ast.YieldFrom)) and escapes_table(node.value):
                errors.append(f"unresolved registered table alias: {relative}:{node.lineno}")
    # Existing whole-module aliases retain qualified public module bindings too.
    def target_imports(module, seen=()):
        if module not in modules_by_name or module in seen:
            return {}
        relative, tree = modules_by_name[module]
        alias = permanent_alias(tree)
        if alias is not None:
            return target_imports(alias[0], (*seen, module))
        return {local: symbol for (path, local), symbol in imports.items() if path == relative}
    for relative, tree in modules_by_name.values():
        alias = permanent_alias(tree)
        if alias is not None:
            for local, symbol in target_imports(alias[0]).items():
                imports[(relative, local)] = symbol
    return imports, errors


def check(root, base):
    merge_base = subprocess.check_output(["git", "merge-base", base, "HEAD"], cwd=root, encoding="utf-8").strip()
    baseline = subprocess.check_output(["git", "show", merge_base + ":" + OWNER], cwd=root, encoding="utf-8")
    external_bytes = {path: (root / path).read_bytes() for path in (TOKENIZER_OWNER, TOKENIZER_DATA)}
    baseline_external_bytes = {path: subprocess.check_output(["git", "show", merge_base + ":" + path], cwd=root) for path in (TOKENIZER_OWNER, TOKENIZER_DATA)}
    rows, errors = verify_rows((root / OWNER).read_text(encoding="utf-8"), baseline, external_bytes, baseline_external_bytes)
    imports, binding_errors = bindings(root, set(rows), {symbol for symbol, row in rows.items() if row["family"] == "function_words"})
    errors.extend(binding_errors)
    for symbol in sorted(set(rows) - {symbol for (path, _local), symbol in imports.items() if path != OWNER}):
        errors.append("registered row never imported: " + symbol)
    fixture = json.loads((root / "references/textprims/characterization.json").read_text(encoding="utf-8"))
    fixture_ids = {row["registry_id"] for row in fixture["rows"]}
    if fixture_ids != {row["id"] for row in rows.values()}:
        errors.append("fixture and cumulative registry coverage differ")
    # Existing compatibility imports are baseline obligations even when the
    # candidate deletes their containing file or renames the imported binding.
    archive = subprocess.check_output(["git", "archive", merge_base, "plugins/setec-voiceprint/scripts"], cwd=root)
    baseline_sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle:
            if member.isfile() and member.name.endswith(".py") and "tests" not in Path(member.name).parts and "__pycache__" not in Path(member.name).parts:
                baseline_sources[member.name] = bundle.extractfile(member).read().decode("utf-8")
    baseline_imports, baseline_errors = bindings(root, set(rows), {symbol for symbol, row in rows.items() if row["family"] == "function_words"}, baseline_sources)
    errors.extend("merge-base " + error for error in baseline_errors)
    for (path, local), symbol in baseline_imports.items():
        if imports.get((path, local)) != symbol:
            errors.append("merge-base compatibility binding removed: " + path + ":" + local)
    import_sites = []
    for (path, local), symbol in sorted(imports.items()):
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        alias = permanent_alias(tree)
        if alias is not None:
            line = alias[1]
        elif local.endswith(".tokenize") and symbol == "tokenize":
            line = next(n.lineno for n in ast.walk(tree) if (isinstance(n, ast.Import) and any(a.name == "setec.core.passage_tokenizer_v1" for a in n.names)) or (isinstance(n, ast.ImportFrom) and n.module == "setec.core" and any(a.name == "passage_tokenizer_v1" for a in n.names)))
        else:
            line = alias[1] if alias is not None else next(n.lineno for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and any((a.asname or a.name) == local for a in n.names))
        site = {"path": path, "line": line, "local_binding": local, "registered_symbol": symbol}
        if alias is not None:
            site.update(kind="alias_reexport", canonical_module=alias[0])
        import_sites.append(site)
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
