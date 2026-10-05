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
    # This is an isolated temporary repository, never the workspace root.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", inventory.OWNER], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    owner.write_text((ROOT / inventory.OWNER).read_text())
    fixture = tmp_path / "references/textprims/characterization.json"
    fixture.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "references/textprims/characterization.json", fixture)
    report = inventory.check(tmp_path, "HEAD")
    assert "registered row never imported: split_sentences_punkt" in report["errors"]
    assert "registered row never imported: split_sentences_regex" in report["errors"]
    consumer = owner.parents[2] / "consumer.py"
    consumer.write_text("from setec.core.textprims import split_sentences_punkt, split_sentences_regex\n")
    report = inventory.check(tmp_path, "HEAD")
    assert not report["errors"]
    assert [site["line"] for site in report["registered_import_sites"]] == [1, 1]
