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


def test_order_and_set_comparators_have_distinct_contracts():
    assert runner.encode([1, 2], "sequence_exact") != runner.encode([2, 1], "sequence_exact")
    assert runner.encode([1, 2], "set_exact") == runner.encode([2, 1], "set_exact")
    with pytest.raises(ValueError):
        runner.encode([1, 1], "set_exact")


def test_exact_bytes_float_and_exception_encodings():
    assert runner.encode(b"\x00\xff", "bytes_hex_exact") == {"hex": "00ff"}
    assert runner.encode(-0.0, "float_hex_exact") != runner.encode(0.0, "float_hex_exact")
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
    document = json.loads(fixture.read_text())
    document["rows"][0]["mutant"]["expected"] = document["rows"][0]["expected"]
    altered = tmp_path / "weak.json"
    altered.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="no teeth"):
        runner.run(altered, resource)
    document = json.loads(fixture.read_text())
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


def test_wrong_table_cannot_pass_shared_membership_cases(tmp_path):
    fixture = ROOT / "references/textprims/characterization.json"
    doc = json.loads(fixture.read_text())
    row = next(row for row in doc["rows"] if row["family"] == "function_words")
    row["legacy_callable"] = row["legacy_callable"].replace(":FUNCTION_WORDS.", ":DIALOGUE_FUNCTION_WORDS.")
    altered = tmp_path / "substitute-table.json"
    altered.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="ownership identity changed"):
        runner.run(altered, os.environ["TEXTPRIMS_PUNKT_DATA"])


def test_frozen_tokenizer_registry_wrapper_is_refused(monkeypatch):
    from setec.core import textprims
    native = textprims.tokenize
    monkeypatch.setattr(textprims, "tokenize", lambda *args, **kwargs: native(*args, **kwargs))
    with pytest.raises(ValueError, match="registry identity changed"):
        runner.run(ROOT / "references/textprims/characterization.json", os.environ["TEXTPRIMS_PUNKT_DATA"])


def test_frozen_tokenizer_custom_table_argument_is_refused(tmp_path):
    doc = json.loads((ROOT / "references/textprims/characterization.json").read_text())
    row = next(row for row in doc["rows"] if row["family"] == "tokenizer")
    row["kwargs"]["data_path"] = "alternate.json"
    fixture = tmp_path / "custom-table.json"
    fixture.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="custom frozen tokenizer table"):
        runner.run(fixture, os.environ["TEXTPRIMS_PUNKT_DATA"])
