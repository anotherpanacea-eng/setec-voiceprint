"""Inventory must fail ambiguity and changes to registered behavior."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("textprims_inventory", ROOT / "tools/gen_textprims_inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def discover(tmp_path, text):
    p = tmp_path / "plugins/setec-voiceprint/scripts/primitive.py"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    return inventory.discover(tmp_path)


def test_literal_word_extraction_evidence_does_not_replace_provenance(tmp_path):
    rows = discover(tmp_path, 'import re as regex\nWORDS = regex.compile(r"\\b\\w+\\b")\ndef units(text):\n    return WORDS.findall(text)\n')
    extraction = next(row for row in rows if row["operation"].endswith("findall"))
    assert extraction["outcome"] == "unresolved"
    assert "literal unit extraction proved" in extraction["reason"]
    assert extraction["owner"] == "units"


def test_metadata_validation_not_silently_declared_a_tokenizer(tmp_path):
    rows = discover(tmp_path, 'import re\ndef valid(value):\n    return re.fullmatch(r"[0-9a-f]{64}", value)\n')
    assert rows[0]["outcome"] == "unresolved"


def test_dynamic_pattern_not_silently_recognized(tmp_path):
    rows = discover(tmp_path, 'from re import findall as matches\ndef units(text, pattern):\n    return matches(pattern, text)\n')
    assert rows[0]["outcome"] == "unresolved"


def test_regex_flags_cannot_change_in_ownership_only_increment():
    candidate = (ROOT / inventory.OWNER).read_text()
    baseline = subprocess.check_output(["git", "show", "origin/main:" + inventory.OWNER], cwd=ROOT, text=True)
    changed = candidate.replace('re.compile(r"(?<=[.!?])\\s+(?=[A-Z\\\"\'])|\\n{2,}")', 're.compile(r"(?<=[.!?])\\s+(?=[A-Z\\\"\'])|\\n{2,}", re.I)')
    assert changed != candidate
    _, errors = inventory.verify_rows(changed, baseline)
    assert any("declaration changed" in error for error in errors)
    assert any("behavior digest mismatch" in error for error in errors)


def test_rebound_compiled_pattern_remains_unresolved(tmp_path):
    rows = discover(tmp_path, 'import re\nWORDS = re.compile(r"\\w+")\nWORDS = other\ndef units(text):\n    return WORDS.findall(text)\n')
    assert next(row for row in rows if row["operation"].endswith("findall"))["outcome"] == "unresolved"


def test_shadowed_module_alias_remains_unresolved(tmp_path):
    rows = discover(tmp_path, 'import re as regex\ndef units(regex, text):\n    return regex.findall(r"\\w+", text)\n')
    assert rows[0]["outcome"] == "unresolved"


def test_shadowed_function_alias_remains_unresolved(tmp_path):
    rows = discover(tmp_path, 'from re import findall as matches\nmatches = custom\ndef units(text):\n    return matches(r"\\w+", text)\n')
    assert rows[0]["outcome"] == "unresolved"


def test_registered_reexport_cannot_be_rebound(tmp_path):
    path = tmp_path / "plugins/setec-voiceprint/scripts/legacy.py"
    path.parent.mkdir(parents=True)
    path.write_text('from setec.core.textprims import split_sentences_regex as split\nsplit = replacement\n')
    _, errors = inventory.bindings(tmp_path, {"split_sentences_regex"})
    assert any("rebound registered import" in error for error in errors)


def test_duplicate_registered_defining_owner_fails(tmp_path):
    path = tmp_path / "plugins/setec-voiceprint/scripts/legacy.py"
    path.parent.mkdir(parents=True)
    path.write_text('def split_sentences_regex(text):\n    return [text]\n')
    _, errors = inventory.bindings(tmp_path, {"split_sentences_regex"})
    assert any("duplicate registered owner" in error for error in errors)


def test_registered_obligation_cannot_be_deleted():
    baseline = (ROOT / inventory.OWNER).read_text()
    start = baseline.index("    'split_sentences_punkt':")
    end = baseline.index("    'split_sentences_regex':", start)
    candidate = baseline[:start] + baseline[end:]
    _, errors = inventory.verify_rows(candidate, baseline)
    assert any("merge-base registry obligation removed" in error for error in errors)


def test_second_import_cannot_replace_registered_alias(tmp_path):
    path = tmp_path / "plugins/setec-voiceprint/scripts/legacy.py"
    path.parent.mkdir(parents=True)
    path.write_text('from setec.core.textprims import split_sentences_regex as split\nfrom another_module import replacement as split\n')
    _, errors = inventory.bindings(tmp_path, {"split_sentences_regex"})
    assert any("replacement import" in error for error in errors)


def test_owner_module_alias_cannot_be_rebound(tmp_path):
    path = tmp_path / "plugins/setec-voiceprint/scripts/legacy.py"
    path.parent.mkdir(parents=True)
    path.write_text('from setec.core import textprims as primitives\nasync def primitives():\n    pass\n')
    _, errors = inventory.bindings(tmp_path, {"split_sentences_regex"})
    assert any("rebound registered import" in error for error in errors)


def test_registry_factory_and_maps_cannot_be_rebound():
    baseline = (ROOT / inventory.OWNER).read_text()
    for extra in ('\nTOKENIZERS = {}\n', '\n_MappingProxyType = dict\n'):
        with pytest.raises(ValueError, match="rebound"):
            inventory.registry(baseline + extra)


def test_referenced_pattern_rebinding_is_rejected():
    baseline = (ROOT / inventory.OWNER).read_text()
    _, errors = inventory.verify_rows(baseline + '\n_SENT_RE = re.compile("changed")\n', baseline)
    assert any("dependency rebound" in error for error in errors)


def test_invalid_closed_policy_is_rejected():
    baseline = (ROOT / inventory.OWNER).read_text()
    changed = baseline.replace("'case_policy': 'preserve'", "'case_policy': 'arbitrary'", 1)
    _, errors = inventory.verify_rows(changed, baseline)
    assert any("closed behavior policy" in error for error in errors)


def test_bytes_patterns_produce_json_report(tmp_path):
    import json
    rows = discover(tmp_path, 'import re\nWORDS = re.compile(b"[a-z]+")\nWORDS.findall(data)\n')
    encoded = json.dumps(rows)
    assert "bytes_hex" in encoded


def test_new_registered_rows_need_a_production_import(tmp_path):
    import shutil
    owner = tmp_path / inventory.OWNER
    owner.parent.mkdir(parents=True)
    old = subprocess.check_output(["git", "show", "origin/main:" + inventory.OWNER], cwd=ROOT, text=True)
    owner.write_text(old)
    for path in inventory.EXTERNAL:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    # This is an isolated temporary repository, never the workspace root.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", inventory.OWNER, *inventory.EXTERNAL], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    owner.write_text((ROOT / inventory.OWNER).read_text())
    fixture = tmp_path / "references/textprims/characterization.json"
    fixture.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "references/textprims/characterization.json", fixture)
    report = inventory.check(tmp_path, "HEAD")
    assert "registered row never imported: split_sentences_punkt" in report["errors"]
    assert "registered row never imported: split_sentences_regex" in report["errors"]
    # The registry's pinned lazy __getattr__ branch is the preflight analysis row's import site.
    assert "registered row never imported: _analysis" not in report["errors"]
    consumer = owner.parents[2] / "consumer.py"
    consumer.write_text("from setec.core.textprims import " + ", ".join(inventory.registry(owner.read_text())) + "\n")
    report = inventory.check(tmp_path, "HEAD")
    assert not report["errors"]
    assert {site["line"] for site in report["registered_import_sites"] if site["path"].endswith("/consumer.py")} == {1}


def test_registered_tables_keep_exact_defining_bytes():
    baseline = (ROOT / inventory.OWNER).read_text()
    changed = baseline.replace('"above"', '"unlisted_word"', 1)
    _, errors = inventory.verify_rows(changed, baseline)
    assert any("table digest mismatch" in error for error in errors)
    assert any("defining callable changed" in error for error in errors)


def test_compatibility_reexport_chain_resolves_final_table(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "legacy.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\n")
    (scripts / "consumer.py").write_text("from legacy import WORDS as vocabulary\n")
    imports, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert not errors
    assert imports[("plugins/setec-voiceprint/scripts/consumer.py", "vocabulary")] == "FUNCTION_WORDS"
    (scripts / "legacy.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\nWORDS = set()\n")
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert any("rebound registered import" in error for error in errors)


@pytest.mark.parametrize("operation", ['WORDS.add("new")', 'mutator = WORDS.pop', 'getattr(WORDS, "add")("new")', 'alias = WORDS'])
def test_registered_table_mutation_or_escaping_alias_fails(tmp_path, operation):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\n" + operation + "\n")
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert errors


def test_module_qualified_table_mutation_fails(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text('import setec.core.textprims\nsetec.core.textprims.FUNCTION_WORDS.clear()\n')
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert any("table mutation" in error for error in errors)


@pytest.mark.parametrize("operation", [
    'set.add(WORDS, "new")',
    'def change(table):\n    table.add("new")\nchange(WORDS)',
    'consume(table=WORDS)',
    'consume([WORDS])',
    'consume({"table": (WORDS,)})',
    'sorted = change\nsorted(WORDS)',
    'def sorted(table):\n    table.clear()\nsorted(WORDS)',
    'def caller(len):\n    len(WORDS)',
])
def test_registered_table_arguments_cannot_escape(tmp_path, operation):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\n" + operation + "\n")
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert any("table argument" in error for error in errors)


def test_unshadowed_table_read_builtins_remain_allowed(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\nordered = sorted(WORDS)\nsize = len(WORDS)\n")
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert not errors


@pytest.mark.parametrize("operation", ['holder = [WORDS]\nholder[0].clear()', 'holder = {"table": WORDS}', 'def give():\n    return WORDS'])
def test_registered_table_container_or_return_escape_fails(tmp_path, operation):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text("from setec.core.textprims import FUNCTION_WORDS as WORDS\n" + operation + "\n")
    _, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert any("table alias" in error for error in errors)


def _launcher(module):
    return f'import sys\nfrom {module.rsplit(".", 1)[0]} import {module.rsplit(".", 1)[1]} as _mod\nif __name__ == "__main__":\n    sys.exit(_mod.main())\nelse:\n    sys.modules[__name__] = _mod\n'


def test_permanent_module_alias_keeps_registered_reexports(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "canonical.py").write_text("from setec.core.textprims import FUNCTION_WORDS\n")
    (scripts / "legacy.py").write_text(_launcher("package.canonical"))
    (scripts / "package").mkdir()
    (scripts / "package/canonical.py").write_text("from setec.core.textprims import FUNCTION_WORDS\n")
    imports, errors = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert not errors
    assert imports[("plugins/setec-voiceprint/scripts/legacy.py", "FUNCTION_WORDS")] == "FUNCTION_WORDS"


@pytest.mark.parametrize("repair", [
    lambda text: text + "_mod = replacement\n",
    lambda text: text + "sys = replacement\n",
    lambda text: '__name__ = "__main__"\n' + text,
    lambda text: text + "sys.modules[__name__] = other\n",
    lambda text: text.replace('== "__main__"', '!= "__main__"'),
])
def test_ambiguous_permanent_alias_is_not_admitted(repair):
    import ast
    assert inventory.permanent_alias(ast.parse(repair(_launcher("package.canonical")))) is None


def test_wrong_target_and_alias_cycles_do_not_prove_a_binding(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "legacy.py").write_text(_launcher("package.missing"))
    imports, _ = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert ("plugins/setec-voiceprint/scripts/legacy.py", "FUNCTION_WORDS") not in imports
    (scripts / "package").mkdir()
    (scripts / "package/a.py").write_text(_launcher("package.b"))
    (scripts / "package/b.py").write_text(_launcher("package.a"))
    imports, _ = inventory.bindings(tmp_path, {"FUNCTION_WORDS"}, {"FUNCTION_WORDS"})
    assert not imports


@pytest.mark.parametrize("path", [inventory.TOKENIZER_OWNER, inventory.TOKENIZER_DATA])
def test_frozen_tokenizer_dependency_bytes_are_bound(path):
    owner = (ROOT / inventory.OWNER).read_text()
    original = {p: (ROOT / p).read_bytes() for p in (inventory.TOKENIZER_OWNER, inventory.TOKENIZER_DATA)}
    changed = dict(original)
    changed[path] += b"\n"
    _, errors = inventory.verify_rows(owner, owner, changed, original)
    assert any("dependency changed" in error for error in errors)
    assert any("digest mismatch" in error for error in errors)


def test_frozen_tokenizer_cannot_name_the_registry_as_defining_owner():
    owner = (ROOT / inventory.OWNER).read_text()
    changed = owner.replace(inventory.TOKENIZER_OWNER + ":tokenize", inventory.OWNER + ":tokenize")
    _, errors = inventory.verify_rows(changed, owner)
    assert any("unresolved final owner" in error for error in errors)


@pytest.mark.parametrize("operation", ['native.tokenize = replacement', 'native.load_data = replacement', 'native.DATA_FILE = replacement', 'del native.load_data', 'native = replacement', 'alias = native', 'setattr(native, "tokenize", replacement)'])
def test_frozen_tokenizer_module_binding_cannot_be_replaced(tmp_path, operation):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text('from setec.core import passage_tokenizer_v1 as native\n' + operation + '\n')
    _, errors = inventory.bindings(tmp_path, {"tokenize"})
    assert errors


def test_frozen_tokenizer_module_import_is_an_obligation(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    scripts.mkdir(parents=True)
    (scripts / "consumer.py").write_text('from setec.core import passage_tokenizer_v1 as native\nwords = native.tokenize("A")\n')
    imports, errors = inventory.bindings(tmp_path, {"tokenize"})
    assert not errors
    assert imports[("plugins/setec-voiceprint/scripts/consumer.py", "native.tokenize")] == "tokenize"


def test_import_only_native_module_alias_is_recognized():
    import ast
    source = 'import sys\nfrom setec.core import passage_tokenizer_v1 as _mod\nif __name__ != "__main__":\n    sys.modules[__name__] = _mod\n'
    assert inventory.permanent_alias(ast.parse(source)) == ("setec.core.passage_tokenizer_v1", 4)


def test_permanent_alias_preserves_module_qualified_primitive_binding(tmp_path):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    (scripts / "package").mkdir(parents=True)
    (scripts / "legacy.py").write_text(_launcher("package.canonical"))
    (scripts / "package/canonical.py").write_text('from setec.core import passage_tokenizer_v1 as native\n')
    imports, errors = inventory.bindings(tmp_path, {"tokenize"})
    assert not errors
    assert imports[("plugins/setec-voiceprint/scripts/legacy.py", "native.tokenize")] == "tokenize"


@pytest.mark.parametrize("replacement", [
    'return lambda value: tokenize(value)',
    'return replacement',
])
def test_frozen_tokenizer_lazy_export_cannot_wrap_or_replace_native(replacement):
    owner = (ROOT / inventory.OWNER).read_text()
    changed = owner.replace('        return tokenize', '        ' + replacement)
    _, errors = inventory.verify_rows(changed, owner)
    assert any("lazily reexport" in error for error in errors)


def test_frozen_tokenizer_lazy_export_cannot_be_rebound():
    owner = (ROOT / inventory.OWNER).read_text()
    _, errors = inventory.verify_rows(owner + '\n__getattr__ = replacement\n', owner)
    assert any("dependency rebound" in error for error in errors)


def _verbatim_rows(owner_text):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    original = (ROOT / inventory.VERBATIM_OWNER).read_bytes()
    return inventory.verify_rows(registry, registry, {inventory.VERBATIM_OWNER: owner_text.encode("utf-8")}, {inventory.VERBATIM_OWNER: original})


def test_verbatim_cover_rows_verify_against_the_live_owner():
    rows, errors = _verbatim_rows((ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8"))
    assert not errors
    assert rows["_tokens"]["family"] == "tokenizer" and rows["_content_fingerprint"]["family"] == "fingerprint"


@pytest.mark.parametrize("old, new", [
    ('def _tokens(text: str) -> list[str]:\n', 'def _tokens(text: str) -> list[str]:  # changed\n'),
    ('_TOKEN = re.compile(r"[a-z0-9]+")\n', '_TOKEN = re.compile(r"[a-z0-9]+")  # changed\n'),
    ('# ASCII unit separator: a non-token byte', '# changed separator comment'),
    ('return hashlib.sha256(_FP_SEP.join(', 'return hashlib.sha256(_FP_SEP.join( '),
])
def test_verbatim_cover_bound_source_change_is_rejected(old, new):
    text = (ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8")
    assert text.count(old) == 1
    _, errors = _verbatim_rows(text.replace(old, new))
    assert any("bound source changed" in error for error in errors)
    assert any("verbatim-cover behavior digest mismatch" in error for error in errors)


def test_verbatim_cover_digest_ignores_unbound_owner_code():
    text = (ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8")
    old = 'def _bounded(toks: list[str], i: int, length: int) -> str:\n'
    assert text.count(old) == 1
    _, errors = _verbatim_rows(text.replace(old, old + '    # matcher edit outside this cohort\n'))
    assert not errors


@pytest.mark.parametrize("name", ["_TOKEN", "_FP_SEP", "_tokens", "_content_fingerprint"])
def test_verbatim_cover_second_owner_binding_is_rejected(name):
    text = (ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8")
    _, errors = _verbatim_rows(text + "\n" + name + " = replacement\n")
    assert "verbatim-cover owner binding not unique: " + name in errors


def test_verbatim_cover_decorated_callable_is_rejected():
    text = (ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8")
    _, errors = _verbatim_rows(text.replace("\ndef _tokens(", "\n@decorator\ndef _tokens(", 1))
    assert "verbatim-cover callable decorated: _tokens" in errors


@pytest.mark.parametrize("module, extra", [("re", "re = replacement\n"), ("re", "from regex import compile as re\n"), ("hashlib", "def f(hashlib):\n    pass\n")])
def test_verbatim_cover_module_imports_must_be_sole_plain_bindings(module, extra):
    text = (ROOT / inventory.VERBATIM_OWNER).read_text(encoding="utf-8")
    _, errors = _verbatim_rows(text + "\n" + extra)
    assert "verbatim-cover module binding must be one plain import: " + module in errors


@pytest.mark.parametrize("old, new", [
    ("from setec.core.verbatim_cover import _content_fingerprint, _tokens\n", "from setec.core.verbatim_cover import _content_fingerprint\nfrom setec.core.verbatim_cover import _tokens\n"),
    ("from setec.core.verbatim_cover import _content_fingerprint, _tokens\n", "from setec.core.verbatim_cover import _content_fingerprint, _tokens as _native\n_tokens = _native\n"),
])
def test_registry_must_import_verbatim_cover_callables_directly(old, new):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    assert registry.count(old) == 1
    _, errors = inventory.verify_rows(registry.replace(old, new), registry)
    assert "registry must directly import its verbatim-cover callables" in errors


def test_registry_cannot_rebind_a_verbatim_cover_callable():
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    _, errors = inventory.verify_rows(registry + "\ndef _content_fingerprint(text):\n    return text\n", registry)
    assert "registered owner or dependency rebound: _content_fingerprint" in errors


def _scripts(tmp_path, files, rows=inventory.VERBATIM_ROWS):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    for name, text in files.items():
        (scripts / name).parent.mkdir(parents=True, exist_ok=True)
        (scripts / name).write_text(text, encoding="utf-8")
    return inventory.bindings(tmp_path, set(rows))


def test_verbatim_cover_reexport_chain_resolves_to_the_owner(tmp_path):
    imports, errors = _scripts(tmp_path, {
        "setec/surfaces/audit.py": "from setec.core.verbatim_cover import _content_fingerprint, _tokens\n",
        "consumer.py": "from setec.surfaces.audit import _tokens as words\n",
    })
    assert not errors
    assert imports[("plugins/setec-voiceprint/scripts/consumer.py", "words")] == "_tokens"
    assert imports[("plugins/setec-voiceprint/scripts/setec/surfaces/audit.py", "_content_fingerprint")] == "_content_fingerprint"


@pytest.mark.parametrize("operation", ["_tokens = replacement", "def _tokens(text):\n    return []", "from elsewhere import other as _tokens"])
def test_rebinding_an_owner_resolved_callable_fails(tmp_path, operation):
    _, errors = _scripts(tmp_path, {"consumer.py": "from setec.core.verbatim_cover import _tokens\n" + operation + "\n"})
    assert errors


def test_same_named_independent_definitions_are_not_registered_objects(tmp_path):
    imports, errors = _scripts(tmp_path, {
        "independent.py": 'import hashlib\nimport re\n_TOKEN = re.compile(r"[a-z]+")\ndef _tokens(text):\n    return _TOKEN.findall(text.lower())\ndef _content_fingerprint(text):\n    return hashlib.sha256(text.encode()).hexdigest()\n',
        "consumer.py": "from independent import _tokens, _content_fingerprint\n",
        "stream.py": "class Stream:\n    def __init__(self, text):\n        self._tokens = text.split()\n        self._content_fingerprint = None\n",
    })
    assert not errors
    assert not imports


@pytest.mark.parametrize("source", [
    "from setec.core import verbatim_cover as vc\nsetattr(vc, '_tokens', len)\n",
    "import setec.core.verbatim_cover as vc\nvc._content_fingerprint = len\n",
    "import setec.core.verbatim_cover as vc\nowner = vc\n",
    "import setec.surfaces.audit\nsetec.surfaces.audit._tokens = len\n",
])
def test_replacing_through_an_owner_module_object_fails(tmp_path, source):
    _, errors = _scripts(tmp_path, {
        "setec/surfaces/audit.py": "from setec.core.verbatim_cover import _content_fingerprint, _tokens\n",
        "consumer.py": source,
    })
    assert errors


def test_inline_token_pattern_use_stays_an_unresolved_candidate(tmp_path):
    source = "from setec.core.verbatim_cover import _TOKEN, _content_fingerprint\ndef words(text):\n    return len(_TOKEN.findall(text.lower()))\n"
    _, errors = _scripts(tmp_path, {"consumer.py": source})
    assert not errors
    inline = next(row for row in inventory.discover(tmp_path) if row["operation"] == "possible_compiled_pattern.findall")
    assert inline["outcome"] == "unresolved"


@pytest.mark.parametrize("old, new, error", [
    ("'pattern_sha256': '6e4816cd686ec03e0953452b4db122df21c7a5d83a99db3aeb98c0889ca6b9f5'", "'pattern_sha256': None", "verbatim-cover pattern digest mismatch: _tokens"),
    ("'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_content_fingerprint',\n 'pattern_sha256': None", "'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_content_fingerprint',\n 'pattern_sha256': '6e4816cd686ec03e0953452b4db122df21c7a5d83a99db3aeb98c0889ca6b9f5'", "verbatim-cover pattern digest mismatch: _content_fingerprint"),
    ("'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_tokens',\n 'pattern_sha256': '6e4816cd686ec03e0953452b4db122df21c7a5d83a99db3aeb98c0889ca6b9f5',\n 'case_policy': 'lower'", "'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_tokens',\n 'pattern_sha256': '6e4816cd686ec03e0953452b4db122df21c7a5d83a99db3aeb98c0889ca6b9f5',\n 'case_policy': 'casefold'", "invalid verbatim-cover policy: _tokens"),
])
def test_verbatim_cover_row_fields_are_bound(old, new, error):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    assert registry.count(old) == 1
    _, errors = inventory.verify_rows(registry.replace(old, new), registry)
    assert error in errors


def _paragraph_rows(owner_text):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    original = (ROOT / inventory.PARAGRAPH_OWNER).read_bytes()
    return inventory.verify_rows(registry, registry, {inventory.PARAGRAPH_OWNER: owner_text.encode("utf-8")}, {inventory.PARAGRAPH_OWNER: original})


def test_paragraph_parser_rows_verify_against_the_live_owner():
    rows, errors = _paragraph_rows((ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8"))
    assert not errors
    assert rows["split_paragraphs"]["family"] == "paragraph_splitter" and rows["split_sentences"]["family"] == "sentence_splitter"


@pytest.mark.parametrize("old, new", [
    ("def split_paragraphs(text: str) -> list[str]:\n", "def split_paragraphs(text: str) -> list[str]:  # changed\n"),
    ("_PARAGRAPH_SPLIT = re.compile(", "_PARAGRAPH_SPLIT = re.compile( "),
    ("def split_sentences(paragraph: str) -> list[str]:\n", "def split_sentences(paragraph: str) -> list[str]:  # changed\n"),
    ("_SENTENCE_END = re.compile(", "_SENTENCE_END = re.compile( "),
])
def test_paragraph_parser_bound_source_change_is_rejected(old, new):
    text = (ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8")
    assert text.count(old) == 1
    _, errors = _paragraph_rows(text.replace(old, new))
    assert any("paragraph-parser bound source changed" in error for error in errors)
    assert any("paragraph-parser behavior digest mismatch" in error for error in errors)


def test_paragraph_parser_digest_ignores_unbound_owner_code():
    text = (ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8")
    old = "def paragraph_count(text: str) -> int:\n"
    assert text.count(old) == 1
    _, errors = _paragraph_rows(text.replace(old, old + "    # stats edit outside this cohort\n"))
    assert not errors


@pytest.mark.parametrize("name", ["split_paragraphs", "split_sentences", "_PARAGRAPH_SPLIT", "_SENTENCE_END"])
def test_paragraph_parser_second_owner_binding_is_rejected(name):
    text = (ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8")
    _, errors = _paragraph_rows(text + "\n" + name + " = replacement\n")
    assert "paragraph-parser owner binding not unique: " + name in errors


def test_paragraph_parser_decorated_callable_is_rejected():
    text = (ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8")
    _, errors = _paragraph_rows(text.replace("\ndef split_sentences(", "\n@decorator\ndef split_sentences(", 1))
    assert "paragraph-parser callable decorated: split_sentences" in errors


def test_paragraph_parser_re_must_be_the_sole_plain_import():
    text = (ROOT / inventory.PARAGRAPH_OWNER).read_text(encoding="utf-8")
    _, errors = _paragraph_rows(text + "\nfrom regex import compile as re\n")
    assert "paragraph-parser module binding must be one plain import: re" in errors


def test_registry_must_import_paragraph_parser_callables_directly():
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    old = "from setec.core.paragraph_parser import split_paragraphs, split_sentences\n"
    assert registry.count(old) == 1
    _, errors = inventory.verify_rows(registry.replace(old, "from setec.core.paragraph_parser import split_paragraphs\nfrom setec.core.paragraph_parser import split_sentences\n"), registry)
    assert "registry must directly import its paragraph-parser callables" in errors


@pytest.mark.parametrize("old, new, error", [
    ("'pattern_sha256': 'fd111b0036776b9ec1d7bb65d7e38b609b99a8d8d8376013978d755199f743f7'", "'pattern_sha256': '321db9c338b83143e55c62803cc0ab884d70a6892b437dc130cf5d5db4722289'", "paragraph-parser pattern digest mismatch: split_paragraphs"),
    ("'pattern_sha256': '321db9c338b83143e55c62803cc0ab884d70a6892b437dc130cf5d5db4722289',\n 'case_policy': 'preserve'", "'pattern_sha256': '321db9c338b83143e55c62803cc0ab884d70a6892b437dc130cf5d5db4722289',\n 'case_policy': 'lower'", "invalid paragraph-parser policy: split_sentences"),
])
def test_paragraph_parser_row_fields_are_bound(old, new, error):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    assert registry.count(old) == 1
    _, errors = inventory.verify_rows(registry.replace(old, new), registry)
    assert error in errors


def _paragraph_scripts(tmp_path, consumer):
    launcher = (ROOT / "plugins/setec-voiceprint/scripts/paragraph_parser.py").read_text(encoding="utf-8")
    return _scripts(tmp_path, {"paragraph_parser.py": launcher, "consumer.py": consumer}, inventory.PARAGRAPH_ROWS)


def test_registered_reads_through_the_flat_launcher_resolve_to_the_owner(tmp_path):
    imports, errors = _paragraph_scripts(tmp_path, "import paragraph_parser\nfrom setec.core import paragraph_parser as core\ndef run(text):\n    return [paragraph_parser.split_sentences(p) for p in core.split_paragraphs(text)], paragraph_parser.parse_document(text)\n")
    assert not errors
    consumer = "plugins/setec-voiceprint/scripts/consumer.py"
    assert imports[(consumer, "paragraph_parser.split_sentences")] == "split_sentences"
    assert imports[(consumer, "core.split_paragraphs")] == "split_paragraphs"
    assert (consumer, "paragraph_parser.parse_document") not in imports


@pytest.mark.parametrize("source", [
    "import paragraph_parser\nparagraph_parser.split_sentences = len\n",
    "import paragraph_parser\nparagraph_parser.unregistered = None\n",
    "from setec.core import paragraph_parser as core\nsetattr(core, 'split_paragraphs', len)\n",
    "import paragraph_parser\nowner = paragraph_parser\n",
])
def test_replacing_or_aliasing_a_paragraph_parser_module_object_fails(tmp_path, source):
    _, errors = _paragraph_scripts(tmp_path, source)
    assert errors


def test_same_named_independent_splitters_are_not_registered_objects(tmp_path):
    imports, errors = _scripts(tmp_path, {
        "independent.py": "def split_paragraphs(text):\n    return text.split('\\n\\n')\ndef split_sentences(text):\n    return text.split('. ')\n",
        "consumer.py": "from independent import split_paragraphs, split_sentences\n",
    }, inventory.PARAGRAPH_ROWS)
    assert not errors
    assert not imports


def _preflight_rows(owner_text):
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    original = (ROOT / inventory.PREFLIGHT_OWNER).read_bytes()
    return inventory.verify_rows(registry, registry, {inventory.PREFLIGHT_OWNER: owner_text.encode("utf-8")}, {inventory.PREFLIGHT_OWNER: original})


@pytest.mark.parametrize("old", [
    "def _analysis(data: bytes) -> tuple[str, str]:\n",
    "def text_rule_violation(data: bytes) -> str | None:\n",
    "def domain_hash(domain: str, payload: bytes) -> str:\n",
])
def test_preflight_analysis_bound_source_change_is_rejected(old):
    text = (ROOT / inventory.PREFLIGHT_OWNER).read_text(encoding="utf-8")
    assert text.count(old) == 1
    _, errors = _preflight_rows(text.replace(old, old.rstrip("\n") + "  # changed\n"))
    assert any("preflight-analysis bound source changed" in error for error in errors)
    assert any("preflight-analysis behavior digest mismatch" in error for error in errors)


@pytest.mark.parametrize("change, error", [
    (lambda text: text.replace("\ndef domain_hash(", "\n@decorator\ndef domain_hash(", 1), "preflight-analysis callable decorated: domain_hash"),
    (lambda text: text + "\ntext_rule_violation = replacement\n", "preflight-analysis owner binding not unique: text_rule_violation"),
    (lambda text: text + "\nfrom os import path as unicodedata\n", "preflight-analysis module binding must be one plain import: unicodedata"),
])
def test_preflight_analysis_owner_bindings_are_single_undecorated_and_plain(change, error):
    _, errors = _preflight_rows(change((ROOT / inventory.PREFLIGHT_OWNER).read_text(encoding="utf-8")))
    assert error in errors


def test_preflight_analysis_normalization_is_bound():
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    old = "'unicode_normalization': 'NFC'"
    assert registry.count(old) == 1
    _, errors = inventory.verify_rows(registry.replace(old, "'unicode_normalization': 'none'"), registry)
    assert "invalid preflight-analysis policy: _analysis" in errors


def test_registry_must_expose_preflight_analysis_lazily():
    registry = (ROOT / inventory.OWNER).read_text(encoding="utf-8")
    branch = '    if name == "_analysis":\n        from setec.preflight.common import _analysis\n        return _analysis\n'
    assert registry.count(branch) == 1
    eager = registry.replace(branch, "") + "\nfrom setec.preflight.common import _analysis\n"
    _, errors = inventory.verify_rows(eager, registry)
    assert "registry must lazily import its preflight-analysis callables" in errors


def test_relative_preflight_imports_resolve_to_the_owner(tmp_path):
    user = "from .common import _analysis\nfrom . import common\ndef run(data):\n    return _analysis(data), common._analysis(data)\n"
    imports, errors = _scripts(tmp_path, {"setec/preflight/__init__.py": "", "setec/preflight/user.py": user, "consumer.py": "from setec.preflight.user import _analysis as analysis\n"}, inventory.PREFLIGHT_ROWS)
    assert not errors
    path = "plugins/setec-voiceprint/scripts/setec/preflight/user.py"
    assert imports[(path, "_analysis")] == imports[(path, "common._analysis")] == "_analysis"
    assert imports[("plugins/setec-voiceprint/scripts/consumer.py", "analysis")] == "_analysis"


@pytest.mark.parametrize("source", [
    "from . import common\ncommon._analysis = len\n",
    "from . import common\nowner = common\n",
    "from . import common\nprint(common)\n",
])
def test_storing_aliasing_or_passing_a_relative_preflight_owner_module_fails(tmp_path, source):
    _, errors = _scripts(tmp_path, {"setec/preflight/__init__.py": "", "setec/preflight/user.py": source}, inventory.PREFLIGHT_ROWS)
    assert errors
