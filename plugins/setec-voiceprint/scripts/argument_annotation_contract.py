"""Strict, fixture-only validation of candidate argument annotations.

This module checks a cleaned source, its block map, and a structured candidate
artifact. It makes no source-admission, label-acceptance, or measurement claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping

from argument_feature_schema import MODE_OPTIONS, ROLE_OPTIONS


BLOCK_MAP_SCHEMA = "setec.argument_block_map.v1"
ANNOTATION_SCHEMA = "setec.argument_annotation_candidate.v1"

_STATES = (
    "assigned", "substantive_abstention", "adjudication_pending",
    "missing_or_malformed", "provider_failure",
)
_REASONS = {
    "adjudication_pending": {"insufficient_context", "unresolved_interpretation"},
    "missing_or_malformed": {"missing_response_field", "unsupported_response_value"},
    "provider_failure": {"provider_failure"},
}


class ValidationError(ValueError):
    """Invalid bundle; the message contains only a stable code and ordinal."""

    def __init__(self, reason: str, ordinal: int | None = None):
        self.reason = reason
        self.ordinal = ordinal
        super().__init__(reason if ordinal is None else f"{reason} at ordinal {ordinal}")


@dataclass(frozen=True)
class ProjectedUnit:
    unit_id: str
    adjacency_run_id: int
    role_value: str | None
    role_state: str
    mode_value: str | None
    mode_state: str


@dataclass(frozen=True)
class CandidateProjection:
    first_prose_unit_id: str
    units: tuple[ProjectedUnit, ...]


@dataclass(frozen=True)
class ValidationSummary:
    source_schema: str
    block_map_schema: str
    candidate_schema: str
    source_sha256: str
    block_map_sha256: str
    candidate_sha256: str
    total_nodes: int
    prose_nodes: int
    boundary_nodes: int
    excluded_nodes: int
    role_state_counts: Mapping[str, int]
    mode_state_counts: Mapping[str, int]


@dataclass(frozen=True)
class ValidationResult:
    candidate_projection: CandidateProjection
    validation_summary: ValidationSummary


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValidationError("nonfinite_json_number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError("nonfinite_json_number")
    return result


def _decode_utf8(data: bytes) -> str:
    if type(data) is not bytes:
        raise ValidationError("invalid_bytes")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValidationError("invalid_utf8") from None


def _json_object(data: bytes) -> dict[str, Any]:
    text = _decode_utf8(data)
    try:
        value = json.loads(text, object_pairs_hook=_object_pairs,
                           parse_constant=_reject_constant, parse_float=_finite_float)
    except ValidationError:
        raise
    except RecursionError:
        raise ValidationError("invalid_json_depth") from None
    except (json.JSONDecodeError, ValueError):
        raise ValidationError("invalid_json") from None
    if type(value) is not dict:
        raise ValidationError("invalid_root")
    return value


def _keys(value: Any, expected: set[str], ordinal: int | None = None) -> None:
    if type(value) is not dict or set(value) != expected:
        raise ValidationError("invalid_keys", ordinal)


def _nonempty(value: Any) -> bool:
    return type(value) is str and bool(value)


def _hash_field(value: Any, ordinal: int | None = None) -> str:
    if (type(value) is not str or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        raise ValidationError("invalid_hash", ordinal)
    return value


def _parse_map(source: str, source_bytes: bytes, map_data: dict[str, Any]) -> tuple[list[str], list[int], dict[str, int]]:
    _keys(map_data, {"schema", "source_sha256", "nodes"})
    if map_data["schema"] != BLOCK_MAP_SCHEMA:
        raise ValidationError("unsupported_schema")
    if _hash_field(map_data["source_sha256"]) != _digest(source_bytes):
        raise ValidationError("source_hash_mismatch")
    nodes = map_data["nodes"]
    if type(nodes) is not list:
        raise ValidationError("invalid_nodes")
    if not source.strip():
        raise ValidationError("no_measurement_units")

    seen: set[str] = set()
    prose_ids: list[str] = []
    run_ids: list[int] = []
    counts = {"prose": 0, "boundary": 0, "excluded": 0}
    last_end = 0
    run = 0
    prose_in_run = False
    for ordinal, node in enumerate(nodes):
        _keys(node, {"id", "kind", "start", "end", "text_sha256", "disposition"}, ordinal)
        node_id = node["id"]
        if not _nonempty(node_id):
            raise ValidationError("invalid_node_id", ordinal)
        if node_id in seen:
            raise ValidationError("duplicate_node_id", ordinal)
        seen.add(node_id)
        kind = node["kind"]
        if type(kind) is not str or kind not in counts:
            raise ValidationError("invalid_node_kind", ordinal)
        if type(node["disposition"]) is not str or (kind, node["disposition"]) not in {
            ("prose", "measure"),
            ("boundary", "substantive_nonprose"),
            ("excluded", "layout_only"),
        }:
            raise ValidationError("invalid_node_disposition", ordinal)
        start, end = node["start"], node["end"]
        if (type(start) is not int or type(end) is not int
                or start < 0 or start >= end or end > len(source)):
            raise ValidationError("invalid_span", ordinal)
        if start < last_end:
            raise ValidationError("unordered_span", ordinal)
        if source[last_end:start].strip():
            raise ValidationError("unmapped_substantive_text", ordinal)
        slice_text = source[start:end]
        if kind != "excluded" and not slice_text.strip():
            raise ValidationError("empty_measurement_node", ordinal)
        if _hash_field(node["text_sha256"], ordinal) != _digest(slice_text.encode("utf-8")):
            raise ValidationError("text_hash_mismatch", ordinal)
        counts[kind] += 1
        if kind == "boundary":
            if prose_in_run:
                run += 1
                prose_in_run = False
        elif kind == "prose":
            prose_ids.append(node_id)
            run_ids.append(run)
            prose_in_run = True
        last_end = end
    if source[last_end:].strip():
        raise ValidationError("unmapped_substantive_text", len(nodes))
    if not prose_ids:
        raise ValidationError("no_measurement_units")
    return prose_ids, run_ids, counts


def _field(value: Any, taxonomy: tuple[str, ...], *, is_role: bool, ordinal: int) -> tuple[str | None, str]:
    _keys(value, {"value", "state", "reason"}, ordinal)
    state, reason, label = value["state"], value["reason"], value["value"]
    if type(state) is not str or state not in _STATES or (state == "substantive_abstention" and not is_role):
        raise ValidationError("invalid_field_state", ordinal)
    if state == "assigned":
        if reason is not None:
            raise ValidationError("invalid_field_reason", ordinal)
        if type(label) is not str or label not in taxonomy:
            raise ValidationError("invalid_field_value", ordinal)
    else:
        if label is not None:
            raise ValidationError("invalid_field_value", ordinal)
        permitted = ({"no_supported_argumentative_function"} if state == "substantive_abstention"
                     else _REASONS[state])
        if type(reason) is not str or reason not in permitted:
            raise ValidationError("invalid_field_reason", ordinal)
    return label, state


def validate_candidate_bundle(source_bytes: bytes, block_map_bytes: bytes,
                              candidate_bytes: bytes) -> ValidationResult:
    """Validate exact UTF-8 artifacts and return a private candidate projection.

    Raises ValidationError(reason, ordinal) on any refusal; does no I/O and never
    returns a partial projection. The summary has only hashes and aggregate counts.
    """
    source = _decode_utf8(source_bytes)
    map_data = _json_object(block_map_bytes)
    prose_ids, run_ids, counts = _parse_map(source, source_bytes, map_data)
    candidate = _json_object(candidate_bytes)
    _keys(candidate, {"schema", "block_map_sha256", "annotator_run_id", "entries"})
    if candidate["schema"] != ANNOTATION_SCHEMA:
        raise ValidationError("unsupported_schema")
    if _hash_field(candidate["block_map_sha256"]) != _digest(block_map_bytes):
        raise ValidationError("map_hash_mismatch")
    if not _nonempty(candidate["annotator_run_id"]):
        raise ValidationError("invalid_annotator_run_id")
    entries = candidate["entries"]
    if type(entries) is not list:
        raise ValidationError("invalid_entries")
    observed: list[str] = []
    for ordinal, entry in enumerate(entries):
        _keys(entry, {"unit_id", "role", "mode"}, ordinal)
        unit_id = entry["unit_id"]
        if not _nonempty(unit_id):
            raise ValidationError("invalid_unit_id", ordinal)
        if unit_id in observed:
            raise ValidationError("duplicate_entry", ordinal)
        observed.append(unit_id)
    if len(entries) < len(prose_ids):
        raise ValidationError("missing_entry")
    if len(entries) > len(prose_ids):
        raise ValidationError("extra_entry")
    if observed != prose_ids:
        raise ValidationError("reordered_entry" if set(observed) == set(prose_ids) else "unit_id_mismatch")

    projected: list[ProjectedUnit] = []
    role_counts = dict.fromkeys(_STATES, 0)
    mode_counts = dict.fromkeys(_STATES, 0)
    for ordinal, (entry, run_id) in enumerate(zip(entries, run_ids)):
        role_value, role_state = _field(entry["role"], ROLE_OPTIONS, is_role=True, ordinal=ordinal)
        mode_value, mode_state = _field(entry["mode"], MODE_OPTIONS, is_role=False, ordinal=ordinal)
        role_counts[role_state] += 1
        mode_counts[mode_state] += 1
        projected.append(ProjectedUnit(entry["unit_id"], run_id, role_value, role_state,
                                       mode_value, mode_state))
    return ValidationResult(
        CandidateProjection(prose_ids[0], tuple(projected)),
        ValidationSummary(
            "utf8_cleaned_source.v1", BLOCK_MAP_SCHEMA, ANNOTATION_SCHEMA,
            _digest(source_bytes), _digest(block_map_bytes), _digest(candidate_bytes),
            len(map_data["nodes"]), counts["prose"], counts["boundary"], counts["excluded"],
            MappingProxyType(role_counts), MappingProxyType(mode_counts),
        ),
    )
