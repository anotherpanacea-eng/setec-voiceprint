"""Behavioral checks for the fixture-only candidate validation boundary."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from argument_annotation_contract import (
    ANNOTATION_SCHEMA,
    BLOCK_MAP_SCHEMA,
    ValidationError,
    validate_candidate_bundle,
)


FIXTURE = Path(__file__).resolve().parents[1] / "test_data" / "argument_annotation_contract_fixture"


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _field(value, state="assigned", reason=None):
    return {"value": value, "state": state, "reason": reason}


def _bundle():
    source = (FIXTURE / "source.txt").read_bytes()
    text = source.decode("utf-8")
    specs = [
        ("r1", "prose", "measure", "Résumé opens."),
        ("x1", "excluded", "layout_only", "Layout heading"),
        ("r2", "prose", "measure", "Support explains."),
        ("b1", "boundary", "substantive_nonprose", "TABLE: figures"),
        ("r3", "prose", "measure", "Proposal closes."),
    ]
    nodes = []
    for node_id, kind, disposition, body in specs:
        start = text.index(body)
        nodes.append({"id": node_id, "kind": kind, "start": start,
                      "end": start + len(body), "text_sha256": _hash(body.encode()),
                      "disposition": disposition})
    block_map = {"schema": BLOCK_MAP_SCHEMA, "source_sha256": _hash(source), "nodes": nodes}
    entries = [
        {"unit_id": "r1", "role": _field("support"), "mode": _field("argumentation")},
        {"unit_id": "r2", "role": _field(None, "missing_or_malformed", "missing_response_field"),
         "mode": _field("exposition")},
        {"unit_id": "r3", "role": _field("proposal"), "mode": _field("argumentation")},
    ]
    candidate = {"schema": ANNOTATION_SCHEMA, "block_map_sha256": _hash(_bytes(block_map)),
                 "annotator_run_id": "run-1", "entries": entries}
    return source, block_map, candidate


def _run(source=None, block_map=None, candidate=None):
    original_source, original_map, original_candidate = _bundle()
    source = original_source if source is None else source
    block_map = original_map if block_map is None else block_map
    candidate = original_candidate if candidate is None else candidate
    candidate = copy.deepcopy(candidate)
    candidate["block_map_sha256"] = _hash(_bytes(block_map))
    return validate_candidate_bundle(source, _bytes(block_map), _bytes(candidate))


def _fails(reason, *, source=None, block_map=None, candidate=None):
    with pytest.raises(ValidationError) as exc:
        _run(source, block_map, candidate)
    assert exc.value.reason == reason
    assert "Résumé" not in str(exc.value)
    assert "Proposal closes" not in str(exc.value)


def test_valid_bundle_preserves_order_runs_states_and_content_free_summary():
    source, block_map, candidate = _bundle()
    result = _run()
    assert result.candidate_projection.first_prose_unit_id == "r1"
    assert [(unit.unit_id, unit.adjacency_run_id, unit.role_value, unit.role_state,
             unit.mode_value, unit.mode_state) for unit in result.candidate_projection.units] == [
        ("r1", 0, "support", "assigned", "argumentation", "assigned"),
        ("r2", 0, None, "missing_or_malformed", "exposition", "assigned"),
        ("r3", 1, "proposal", "assigned", "argumentation", "assigned"),
    ]
    summary = result.validation_summary
    assert (summary.total_nodes, summary.prose_nodes, summary.boundary_nodes,
            summary.excluded_nodes) == (5, 3, 1, 1)
    assert summary.source_sha256 == _hash(source)
    assert summary.block_map_sha256 == _hash(_bytes(block_map))
    assert summary.candidate_sha256 == _hash(_bytes(candidate))
    assert summary.role_state_counts["missing_or_malformed"] == 1
    assert summary.mode_state_counts["assigned"] == 3
    receipt = repr(summary)
    assert all(part not in receipt for part in ("r1", "Résumé", "support", "run-1"))


def test_leading_nonprose_and_unassigned_prose_do_not_shift_opening_or_join_neighbors():
    source, block_map, candidate = _bundle()
    lead = block_map["nodes"].pop(1)
    block_map["nodes"].insert(0, lead)
    _fails("unmapped_substantive_text", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"][0]["kind"] = "excluded"
    block_map["nodes"][0]["disposition"] = "layout_only"
    candidate["entries"].pop(0)
    result = _run(block_map=block_map, candidate=candidate)
    assert result.candidate_projection.first_prose_unit_id == "r2"
    assert [unit.unit_id for unit in result.candidate_projection.units] == ["r2", "r3"]


def test_role_abstention_independent_of_assigned_mode():
    candidate = _bundle()[2]
    candidate["entries"][0]["role"] = _field(None, "substantive_abstention", "no_supported_argumentative_function")
    result = _run(candidate=candidate)
    assert result.candidate_projection.units[0].role_value is None
    assert result.candidate_projection.units[0].mode_value == "argumentation"
    assert result.validation_summary.role_state_counts["substantive_abstention"] == 1


@pytest.mark.parametrize("edit,reason", [
    (lambda c: c["entries"][0]["mode"].update(_field(None, "substantive_abstention", "no_supported_argumentative_function")), "invalid_field_state"),
    (lambda c: c["entries"][0]["role"].update(_field(None, "unknown", "insufficient_context")), "invalid_field_state"),
    (lambda c: c["entries"][0]["role"].update(_field(None, "adjudication_pending", "provider_failure")), "invalid_field_reason"),
    (lambda c: c["entries"][0]["role"].update(_field("nonsense")), "invalid_field_value"),
    (lambda c: c["entries"][0]["role"].update(_field("support", "provider_failure", "provider_failure")), "invalid_field_value"),
])
def test_field_state_contract(edit, reason):
    candidate = _bundle()[2]
    edit(candidate)
    _fails(reason, candidate=candidate)


@pytest.mark.parametrize("ids,reason", [
    (["r1", "r2"], "missing_entry"),
    (["r1", "r2", "r3", "r4"], "extra_entry"),
    (["r1", "r1", "r3"], "duplicate_entry"),
    (["r2", "r1", "r3"], "reordered_entry"),
    (["r1", 2, "r3"], "invalid_unit_id"),
])
def test_entries_match_exact_prose_ids(ids, reason):
    candidate = _bundle()[2]
    prototype = copy.deepcopy(candidate["entries"][0])
    candidate["entries"] = [dict(copy.deepcopy(prototype), unit_id=unit_id) for unit_id in ids]
    _fails(reason, candidate=candidate)


def test_pending_and_provider_failure_remain_independent_states():
    candidate = _bundle()[2]
    candidate["entries"][0]["role"] = _field(None, "adjudication_pending", "insufficient_context")
    candidate["entries"][0]["mode"] = _field(None, "provider_failure", "provider_failure")
    result = _run(candidate=candidate)
    assert result.candidate_projection.units[0].role_state == "adjudication_pending"
    assert result.candidate_projection.units[0].mode_state == "provider_failure"
    assert result.validation_summary.role_state_counts["adjudication_pending"] == 1
    assert result.validation_summary.mode_state_counts["provider_failure"] == 1
    with pytest.raises(TypeError):
        result.validation_summary.role_state_counts["assigned"] = 99


def test_missing_entry_differs_from_explicit_missing_state():
    candidate = _bundle()[2]
    candidate["entries"].pop(1)
    _fails("missing_entry", candidate=candidate)
    assert _run().validation_summary.role_state_counts["missing_or_malformed"] == 1


def test_strict_json_and_schema_refusals():
    source, block_map, candidate = _bundle()
    for raw, reason in [
        (b'{"schema":1,"schema":2}', "duplicate_json_key"),
        (b'{"schema":NaN}', "nonfinite_json_number"),
        (b'{"schema":1e999}', "nonfinite_json_number"),
        (b'[]', "invalid_root"),
        (b'\xff', "invalid_utf8"),
    ]:
        with pytest.raises(ValidationError) as exc:
            validate_candidate_bundle(source, raw, _bytes(candidate))
        assert exc.value.reason == reason
    block_map["new_field"] = True
    _fails("invalid_keys", block_map=block_map)
    block_map = _bundle()[1]
    block_map["schema"] = "v2"
    _fails("unsupported_schema", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"][0]["disposition"] = []
    _fails("invalid_node_disposition", block_map=block_map)


def test_candidate_extra_key_and_wrong_input_type_refuse():
    source, block_map, candidate = _bundle()
    candidate["entries"][0]["role"]["confidence"] = 1
    _fails("invalid_keys", candidate=candidate)
    with pytest.raises(ValidationError, match="invalid_bytes"):
        validate_candidate_bundle(source, _bytes(block_map), "not bytes")


def test_source_span_and_binding_refusals():
    source, block_map, candidate = _bundle()
    _fails("source_hash_mismatch", source=source + b"x")
    block_map["nodes"][0]["start"] = True
    _fails("invalid_span", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"][1]["start"] = block_map["nodes"][0]["end"] - 1
    _fails("unordered_span", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"][0]["text_sha256"] = "0" * 64
    _fails("text_hash_mismatch", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"].pop(1)
    _fails("unmapped_substantive_text", block_map=block_map)
    block_map = _bundle()[1]
    block_map["nodes"][1]["id"] = "r1"
    _fails("duplicate_node_id", block_map=block_map)
    candidate = _bundle()[2]
    candidate["block_map_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="map_hash_mismatch"):
        validate_candidate_bundle(source, _bytes(_bundle()[1]), _bytes(candidate))


def test_whitespace_only_nodes_and_no_prose_refuse():
    source, block_map, candidate = _bundle()
    block_map["nodes"][0]["start"] = 0
    block_map["nodes"][0]["end"] = 2
    block_map["nodes"][0]["text_sha256"] = _hash(source[:2])
    _fails("empty_measurement_node", block_map=block_map)
    block_map = _bundle()[1]
    boundary = block_map["nodes"][3]
    boundary["start"] = block_map["nodes"][2]["end"]
    boundary["end"] = boundary["start"] + 1
    boundary["text_sha256"] = _hash(b"\n")
    _fails("empty_measurement_node", block_map=block_map)
    block_map = _bundle()[1]
    for node in block_map["nodes"]:
        if node["kind"] == "prose":
            node["kind"], node["disposition"] = "excluded", "layout_only"
    candidate["entries"] = []
    _fails("no_measurement_units", block_map=block_map, candidate=candidate)
    _fails("no_measurement_units", source=b" ", block_map={"schema": BLOCK_MAP_SCHEMA,
        "source_sha256": _hash(b" "), "nodes": []}, candidate={"schema": ANNOTATION_SCHEMA,
        "block_map_sha256": "0" * 64, "annotator_run_id": "r", "entries": []})


def test_exact_byte_bindings_and_codepoint_offsets():
    source, block_map, candidate = _bundle()
    assert block_map["nodes"][0]["end"] == source.decode("utf-8").index("Résumé opens.") + len("Résumé opens.")
    assert block_map["nodes"][0]["end"] != source.index(b"R\xc3\xa9sum\xc3\xa9 opens.") + len("R\xc3\xa9sum\xc3\xa9 opens.")
    assert _run().validation_summary.source_sha256 == _hash(source)
    changed_map = _bytes(block_map) + b" "
    with pytest.raises(ValidationError, match="map_hash_mismatch"):
        validate_candidate_bundle(source, changed_map, _bytes(candidate))
