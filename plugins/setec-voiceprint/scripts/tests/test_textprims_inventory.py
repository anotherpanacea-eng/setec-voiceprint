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
    for path in (inventory.TOKENIZER_OWNER, inventory.TOKENIZER_DATA):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, target)
    # This is an isolated temporary repository, never the workspace root.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", inventory.OWNER, inventory.TOKENIZER_OWNER, inventory.TOKENIZER_DATA], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    owner.write_text((ROOT / inventory.OWNER).read_text())
    fixture = tmp_path / "references/textprims/characterization.json"
    fixture.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "references/textprims/characterization.json", fixture)
    report = inventory.check(tmp_path, "HEAD")
    assert "registered row never imported: split_sentences_punkt" in report["errors"]
    assert "registered row never imported: split_sentences_regex" in report["errors"]
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
