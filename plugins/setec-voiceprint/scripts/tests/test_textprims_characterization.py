"""Oracle teeth, exact comparator semantics, and native splitter regression."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("textprims_characterization", ROOT / "tools/run_textprims_characterization.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_json_exact_distinguishes_scalar_types():
    assert runner.encode(1, "json_exact") != runner.encode(True, "json_exact")
    assert runner.encode(1, "json_exact") != runner.encode(1.0, "json_exact")


def test_sequence_comparator_is_order_sensitive():
    assert runner.encode([1, 2], "sequence_exact") != runner.encode([2, 1], "sequence_exact")


def test_set_comparator_ignores_order_and_preserves_membership():
    assert runner.encode({"cat", "42"}, "set_exact") == runner.encode(["42", "cat"], "set_exact")
    assert runner.encode(frozenset(), "set_exact") == runner.encode([], "set_exact")
    assert runner.encode({"Cat"}, "set_exact") != runner.encode({"cat"}, "set_exact")


def test_set_invocation_refuses_a_list_with_the_same_members():
    case = {"args": [], "kwargs": {}, "result_path": []}
    assert runner.invoke(lambda: {"cat"}, case, "set_exact") == ("cat",)
    assert runner.invoke(lambda: frozenset({"cat"}), case, "set_exact") == ("cat",)
    with pytest.raises(ValueError, match="set result required"):
        runner.invoke(lambda: ["cat"], case, "set_exact")


def test_exact_bytes_and_exception_encodings():
    assert runner.encode(b"\x00\xff", "bytes_hex_exact") == {"hex": "00ff"}
    with pytest.raises(ValueError):
        runner.encode({"hex": "FF"}, "bytes_hex_exact")
    with pytest.raises(ValueError):
        runner.encode({"type": "ValueError", "message": "x", "extra": 1}, "exception_exact")


def test_exception_call_does_not_accept_success():
    case = {"args": [], "kwargs": {}, "result_path": []}
    with pytest.raises(ValueError, match="not raised"):
        runner.invoke(lambda: None, case, "exception_exact")


def test_native_rows_and_mutants(tmp_path):
    resource = os.environ["TEXTPRIMS_PUNKT_DATA"]
    fixture = ROOT / "references/textprims/characterization.json"
    runner.run(fixture, resource)
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["rows"][0]["mutant"]["expected"] = document["rows"][0]["expected"]
    altered = tmp_path / "weak.json"
    altered.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="no teeth"):
        runner.run(altered, resource)
    document = json.loads(fixture.read_text(encoding="utf-8"))
    document["rows"][0]["expected"] = ["An incorrect expectation."]
    altered.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="mismatch"):
        runner.run(altered, resource)


def test_provisioning_rejects_unverified_archive(tmp_path):
    spec = importlib.util.spec_from_file_location("punkt_setup", ROOT / "tools/prepare_punkt_characterization.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    destination = tmp_path / "resources"
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        setup.prepare(destination, b"not the official archive")
    assert not destination.exists()


def test_native_resolution_rejects_missing_resources(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.configure_punkt(tmp_path)


def _altered(tmp_path, change):
    doc = json.loads((ROOT / "references/textprims/characterization.json").read_text(encoding="utf-8"))
    change(doc)
    path = tmp_path / "altered.json"
    path.write_text(json.dumps(doc))
    return path


def test_unregistered_callable_is_refused(tmp_path):
    fixture = _altered(tmp_path, lambda doc: doc["rows"][0].update(callable="split_everything"))
    with pytest.raises(ValueError, match="unregistered"):
        runner.run(fixture, os.environ["TEXTPRIMS_PUNKT_DATA"])


def test_every_registered_primitive_needs_a_row(tmp_path):
    fixture = _altered(tmp_path, lambda doc: doc.update(rows=[r for r in doc["rows"] if r["callable"] != "_normws"]))
    with pytest.raises(ValueError, match="lacks characterization: _normws"):
        runner.run(fixture, os.environ["TEXTPRIMS_PUNKT_DATA"])


def test_bytes_arguments_must_be_hex_objects(tmp_path):
    def change(doc):
        row = next(r for r in doc["rows"] if r["callable"] == "_analysis")
        row["args"] = ["plain text"]
    with pytest.raises(ValueError, match="closed hex object"):
        runner.run(_altered(tmp_path, change), os.environ["TEXTPRIMS_PUNKT_DATA"])


@pytest.mark.parametrize("module_name", [
    "agd_move_scan", "argquality_dimension_profile", "argument_decision_audit",
    "enthymeme_gapflag", "fallacy_scan", "warrant_probe",
])
def test_argument_audits_share_blankline_splitter(module_name):
    # The one-owner contract keeps the established public aliases on one object.
    import importlib
    from setec.core import textprims
    legacy = importlib.import_module(module_name)
    packaged = importlib.import_module("setec.surfaces." + module_name)
    assert legacy is packaged
    assert legacy.split_paragraphs is textprims.split_paragraphs_blanklines


@pytest.mark.parametrize("module_name", [
    "crosslingual_voice_distance", "document_layout_audit", "formulaicity_audit",
    "narratorial_distance_audit", "reference_ecology_audit",
    "rewriting_invariance_audit", "sound_texture_audit",
])
def test_unicode_counters_share_function_and_pattern(module_name):
    # Preserve existing public names and the compiled-pattern compatibility alias.
    import importlib
    from setec.core import textprims
    legacy = importlib.import_module(module_name)
    packaged = importlib.import_module("setec.surfaces." + module_name)
    assert legacy is packaged
    assert legacy.count_words is textprims.count_words_unicode_hyphen
    assert legacy._WORD_RE is textprims._WORD_UNICODE_HYPHEN_RE



@pytest.mark.parametrize("module_name, public_name", [
    ("stylometry_core", "word_tokens"),
])
def test_alpha_word_token_consumers_share_function_and_pattern(module_name, public_name):
    import importlib
    from setec.core import textprims
    module = importlib.import_module(module_name)
    assert getattr(module, public_name) is textprims.word_tokens_alpha
    assert module.WORD_RE is textprims.WORD_RE
    # These regex methods are consumed publicly, with original case intact.
    assert module.WORD_RE.findall("AbC café don't") == ["AbC", "caf", "don't"]
    assert module.WORD_RE.sub(lambda m: m.group(0).upper(), "AbC café don't") == "ABC CAFé DON'T"



@pytest.mark.parametrize("module_name, public_name", [
    ("agency_abstraction_audit", "_word_count"),
    ("discourse_move_signature", "_word_count"),
    ("paragraph_audit", "word_count"),
    ("punctuation_cadence_audit", "_word_count"),
    ("stance_modality_audit", "_word_count"),
])
def test_unicode_plain_counters_share_function_and_pattern(module_name, public_name):
    import importlib
    from setec.core import textprims
    module = importlib.import_module(module_name)
    assert getattr(module, public_name) is textprims.count_words_unicode
    assert module._WORD_RE is textprims._WORD_UNICODE_RE



@pytest.mark.parametrize("module_name", ["distinct_diversity_audit", "homogeneity_audit"])
def test_token_count_consumers_preserve_tokenizer_binding(module_name, monkeypatch):
    import importlib
    import re
    from setec.core import textprims
    legacy = importlib.import_module(module_name)
    assert legacy is importlib.import_module("setec.surfaces." + module_name)
    assert legacy._word_count is textprims.count_words_alpha_tokens
    assert legacy.word_tokens is textprims.word_tokens_alpha
    # Preserve the existing tokenizer dependency rather than the similar _WORD_RE counter.
    monkeypatch.setattr(textprims, "WORD_RE", re.compile(r"[0-9]+"))
    assert legacy._word_count("123 abc") == 1
    assert legacy._word_count("123 456") == 2
    assert textprims.count_words_alpha("123 456") == 0
    monkeypatch.setattr(textprims, "word_tokens_alpha", lambda text: ["a", "b", "c"])
    assert legacy._word_count("anything") == 3



@pytest.mark.parametrize("module_name, public_name", [
    ("function_word_grammar_audit", "_sentences"), ("discourse_move_signature", "_split_sentences"),
])
def test_uppercase_sentence_consumers_share_function_and_pattern(module_name, public_name):
    import importlib
    from setec.core import textprims
    module = importlib.import_module(module_name)
    assert getattr(module, public_name) is textprims.split_sentences_uppercase
    assert module._SENTENCE_TERMINATORS is textprims._SENTENCE_TERMINATORS
    if module_name == "function_word_grammar_audit":
        # Run segmentation is deliberately different and remains local.
        assert module._SENT_SPLIT_RE.pattern == r"[.!?]+|\n{2,}"
        assert module.function_word_runs("in the. of the") == [["in", "the"], ["of", "the"]]



@pytest.mark.parametrize("module_name", [
    "specdetect_audit", "structural_shuffle_audit", "binoculars_audit",
    "edit_magnitude_audit", "fast_detect_curvature", "intrinsic_dimension_audit",
])
def test_alpha_lower_counter_consumers_share_function_and_pattern(module_name):
    import importlib
    from setec.core import textprims
    module = importlib.import_module(module_name)
    assert module.count_words is textprims.count_words_alpha_lower
    assert module._WORD_RE is textprims._WORD_RE
    # Lowercasing before the ASCII regex has distinct Unicode behavior.
    assert module.count_words(text="\u212a") == 1
    assert module.count_words(text="\u0130abc") == 2
    assert module.count_words(text="A\u212aB") == 1
    assert module._WORD_RE.findall("A\u212aB") == ["A", "B"]
    with pytest.raises(AttributeError):
        module.count_words(None)
    with pytest.raises(TypeError):
        module.count_words(b"AbC")



@pytest.mark.parametrize("module_name", [
    "agd_move_scan_judge", "argquality_judge", "argument_judge",
    "fallacy_judge", "warrant_judge",
])
def test_numbered_paragraph_consumers_share_primitive(module_name):
    import importlib
    from setec.core import textprims
    module = importlib.import_module(module_name)
    assert module._number_paragraphs is textprims.number_paragraphs
    paragraphs = ["", "second"]
    assert module._number_paragraphs(paragraphs=paragraphs) == "[0] \n\n[1] second"
    assert module._build_user_content("synthetic prompt", paragraphs).endswith("[0] \n\n[1] second")
    assert paragraphs == ["", "second"]
