"""Spec-80 lineage-derived Spec-75 population adapter.

This module builds the exact character-bijection crosswalk and derived unit
identities.  It does not mint consumer authority, inspect passage evidence, or
publish files.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

import author_corpus_export as author_export
import reconstructibility_probe_set as spec75
from passage_source_population_commitment import canonical_frame_v1


CROSSWALK_SCHEMA = "setec-passage-source-lineage-crosswalk/1"
METADATA_SCHEMA = "setec-passage-snapshot-metadata/1"
PROJECTION = "identity_text"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_CROSSWALK_KEYS = {
    "schema", "source_population_commitment_sha256",
    "population_manifest_sha256", "author_corpus_export_receipt_sha256",
    "authorized_by", "attested_at", "rows", "crosswalk_commitment_sha256",
}
_ROW_KEYS = {
    "projection_index", "projection_id", "lineage_slice_id", "source_doc_id",
    "source_entry_fingerprint", "source_content_sha256", "source_char_start",
    "source_char_end", "source_slice_sha256", "unit_id", "unit_char_start",
    "unit_char_end", "projection",
}
_UNIT_SPEC_KEYS = {
    "evaluation_partition", "source_group", "document_family",
    "duplicate_component", "loss_mask_intervals", "slices",
}
_SLICE_SPEC_KEYS = {
    "source_doc_id", "source_char_start", "source_char_end",
}


class LineageError(ValueError):
    pass


@dataclass(frozen=True)
class LineageProjection:
    crosswalk: dict[str, Any]
    population_rows: list[dict[str, Any]]
    snapshot_metadata: dict[str, Any]
    unit_texts: dict[str, str]


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _semantic_sha(domain: bytes, value: Any) -> str:
    return _sha(domain + canonical_frame_v1(value))


def _require_digest(value: Any, label: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise LineageError(label)
    return value


def derive_lineage_slice_id(
    *,
    source_population_commitment_sha256: str,
    source_doc_id: str,
    source_entry_fingerprint: str,
    source_content_sha256: str,
    source_char_start: int,
    source_char_end: int,
    source_slice_sha256: str,
) -> str:
    return _semantic_sha(
        b"setec-passage-lineage-slice-id-v1\n",
        {
            "source_population_commitment_sha256":
                source_population_commitment_sha256,
            "source_doc_id": source_doc_id,
            "source_entry_fingerprint": source_entry_fingerprint,
            "source_content_sha256": source_content_sha256,
            "source_char_start": source_char_start,
            "source_char_end": source_char_end,
            "source_slice_sha256": source_slice_sha256,
        },
    )


def derive_unit_id(
    unit_content_sha256: str, ordered_source_slices: list[dict[str, Any]],
) -> str:
    return _semantic_sha(
        b"setec-passage-lineage-unit-id-v1\n",
        {
            "unit_content_sha256": unit_content_sha256,
            "ordered_source_slices": ordered_source_slices,
        },
    )


def derive_projection_id(
    *,
    source_population_commitment_sha256: str,
    population_manifest_sha256: str,
    projection_index: int,
    lineage_slice_id: str,
    source_entry_fingerprint: str,
    unit_id: str,
    unit_char_start: int,
    unit_char_end: int,
) -> str:
    return _semantic_sha(
        b"setec-passage-projection-id-v1\n",
        {
            "source_population_commitment_sha256":
                source_population_commitment_sha256,
            "population_manifest_sha256": population_manifest_sha256,
            "projection_index": projection_index,
            "lineage_slice_id": lineage_slice_id,
            "source_entry_fingerprint": source_entry_fingerprint,
            "unit_id": unit_id,
            "unit_char_start": unit_char_start,
            "unit_char_end": unit_char_end,
            "projection": PROJECTION,
        },
    )


def _population_manifest_bytes(rows: list[dict[str, Any]]) -> bytes:
    import json

    return b"".join(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        for row in rows
    )


def build_lineage_projection(
    *,
    records: list[dict[str, Any]],
    texts: dict[str, bytes],
    unit_specs: list[dict[str, Any]],
    source_population_commitment_sha256: str,
    author_corpus_export_receipt_sha256: str,
    authorized_by: str,
    attested_at: str,
) -> LineageProjection:
    """Build a complete source-to-unit character bijection and derived IDs."""
    source_commitment = _require_digest(
        source_population_commitment_sha256, "source commitment",
    )
    export_receipt = _require_digest(
        author_corpus_export_receipt_sha256, "author receipt",
    )
    if type(authorized_by) is not str or not authorized_by:
        raise LineageError("authorized_by")
    if type(attested_at) is not str or not _UTC.fullmatch(attested_at):
        raise LineageError("attested_at")
    try:
        author_export._verify_record_population_metadata(records)
        author_export._verify_record_population_texts(records, texts)
    except (TypeError, ValueError) as exc:
        raise LineageError("author population") from exc
    if type(unit_specs) is not list or not unit_specs:
        raise LineageError("unit specs")

    source_by_id = {row["id"]: row for row in records}
    source_text = {
        row["id"]: texts[row["content_sha256"]].decode("utf-8")
        for row in records
    }
    source_intervals: dict[str, list[tuple[int, int]]] = {
        row["id"]: [] for row in records
    }
    built_units: list[dict[str, Any]] = []
    for spec in unit_specs:
        if type(spec) is not dict or set(spec) != _UNIT_SPEC_KEYS:
            raise LineageError("unit spec schema")
        slices = spec["slices"]
        if type(slices) is not list or not slices:
            raise LineageError("unit slices")
        unit_text_parts: list[str] = []
        ordered: list[dict[str, Any]] = []
        unit_cursor = 0
        metadata: set[tuple[str, str]] = set()
        for slice_spec in slices:
            if type(slice_spec) is not dict or set(slice_spec) != _SLICE_SPEC_KEYS:
                raise LineageError("slice spec schema")
            source_id = slice_spec["source_doc_id"]
            start = slice_spec["source_char_start"]
            end = slice_spec["source_char_end"]
            if (
                type(source_id) is not str
                or source_id not in source_by_id
                or type(start) is not int
                or type(end) is not int
                or isinstance(start, bool)
                or isinstance(end, bool)
                or start < 0
                or start >= end
                or end > len(source_text[source_id])
            ):
                raise LineageError("slice coordinates")
            record = source_by_id[source_id]
            piece = source_text[source_id][start:end]
            piece_sha = _sha(piece.encode("utf-8"))
            lineage_id = derive_lineage_slice_id(
                source_population_commitment_sha256=source_commitment,
                source_doc_id=source_id,
                source_entry_fingerprint=record["source_entry_fingerprint"],
                source_content_sha256=record["content_sha256"],
                source_char_start=start,
                source_char_end=end,
                source_slice_sha256=piece_sha,
            )
            unit_end = unit_cursor + len(piece)
            ordered.append({
                "lineage_slice_id": lineage_id,
                "source_doc_id": source_id,
                "source_content_sha256": record["content_sha256"],
                "source_char_start": start,
                "source_char_end": end,
                "source_slice_sha256": piece_sha,
                "unit_char_start": unit_cursor,
                "unit_char_end": unit_end,
            })
            unit_text_parts.append(piece)
            source_intervals[source_id].append((start, end))
            metadata.add((record["register"], record["source_kind"]))
            unit_cursor = unit_end
        if len(metadata) != 1:
            raise LineageError("unit metadata mismatch")
        unit_text = "".join(unit_text_parts)
        content_sha = _sha(unit_text.encode("utf-8"))
        unit_id = derive_unit_id(content_sha, ordered)
        register, source_kind = next(iter(metadata))
        built_units.append({
            "unit_id": unit_id,
            "content_sha256": content_sha,
            "text": unit_text,
            "register": register,
            "source_kind": source_kind,
            "ordered_slices": ordered,
            "evaluation_partition": spec["evaluation_partition"],
            "source_group": spec["source_group"],
            "document_family": spec["document_family"],
            "duplicate_component": spec["duplicate_component"],
            "loss_mask_intervals": spec["loss_mask_intervals"],
        })

    for source_id, intervals in source_intervals.items():
        cursor = 0
        for start, end in sorted(intervals):
            if start != cursor:
                raise LineageError("source projection is not a bijection")
            cursor = end
        if cursor != len(source_text[source_id]):
            raise LineageError("source projection is not a bijection")

    built_units.sort(key=lambda row: row["unit_id"].encode("utf-8"))
    if len({row["unit_id"] for row in built_units}) != len(built_units):
        raise LineageError("duplicate derived unit")
    population_rows = [{
        "schema": spec75.SCHEMA_POPULATION,
        "unit_id": unit["unit_id"],
        "text_path": f"units/{unit['unit_id'][7:]}.txt",
        "content_sha256": unit["content_sha256"],
        "corpus_split": "train",
        "evaluation_partition": unit["evaluation_partition"],
        "source_group": unit["source_group"],
        "document_family": unit["document_family"],
        "duplicate_component": unit["duplicate_component"],
        "loss_mask_intervals": unit["loss_mask_intervals"],
    } for unit in built_units]
    try:
        spec75.validate_population(population_rows)
    except spec75.ProbeSetError as exc:
        raise LineageError(exc.code) from exc
    for row, unit in zip(population_rows, built_units):
        if any(interval[1] > len(unit["text"]) for interval in row["loss_mask_intervals"]):
            raise LineageError("mask out of bounds")

    population_sha = _sha(_population_manifest_bytes(population_rows))
    crosswalk_rows: list[dict[str, Any]] = []
    projection_index = 0
    for unit in built_units:
        for source_slice in unit["ordered_slices"]:
            record = source_by_id[source_slice["source_doc_id"]]
            row = {
                "projection_index": projection_index,
                "projection_id": "",
                "lineage_slice_id": source_slice["lineage_slice_id"],
                "source_doc_id": source_slice["source_doc_id"],
                "source_entry_fingerprint": record["source_entry_fingerprint"],
                "source_content_sha256": source_slice["source_content_sha256"],
                "source_char_start": source_slice["source_char_start"],
                "source_char_end": source_slice["source_char_end"],
                "source_slice_sha256": source_slice["source_slice_sha256"],
                "unit_id": unit["unit_id"],
                "unit_char_start": source_slice["unit_char_start"],
                "unit_char_end": source_slice["unit_char_end"],
                "projection": PROJECTION,
            }
            row["projection_id"] = derive_projection_id(
                source_population_commitment_sha256=source_commitment,
                population_manifest_sha256=population_sha,
                projection_index=projection_index,
                lineage_slice_id=row["lineage_slice_id"],
                source_entry_fingerprint=row["source_entry_fingerprint"],
                unit_id=row["unit_id"],
                unit_char_start=row["unit_char_start"],
                unit_char_end=row["unit_char_end"],
            )
            crosswalk_rows.append(row)
            projection_index += 1

    core = {
        "schema": CROSSWALK_SCHEMA,
        "source_population_commitment_sha256": source_commitment,
        "population_manifest_sha256": population_sha,
        "author_corpus_export_receipt_sha256": export_receipt,
        "authorized_by": authorized_by,
        "attested_at": attested_at,
        "rows": crosswalk_rows,
    }
    crosswalk = dict(core)
    crosswalk["crosswalk_commitment_sha256"] = _semantic_sha(
        b"setec-passage-source-lineage-crosswalk-v1\n", core,
    )
    metadata_rows = [{
        "unit_id": unit["unit_id"],
        "content_sha256": unit["content_sha256"],
        "register": unit["register"],
        "source_kind": unit["source_kind"],
    } for unit in built_units]
    snapshot_metadata = {
        "schema": METADATA_SCHEMA,
        "population_manifest_sha256": population_sha,
        "units": metadata_rows,
    }
    unit_texts = {unit["unit_id"]: unit["text"] for unit in built_units}
    return LineageProjection(
        crosswalk=crosswalk,
        population_rows=population_rows,
        snapshot_metadata=snapshot_metadata,
        unit_texts=unit_texts,
    )


def validate_crosswalk_shape(value: dict[str, Any]) -> None:
    if type(value) is not dict or set(value) != _CROSSWALK_KEYS:
        raise LineageError("crosswalk schema")
    if value["schema"] != CROSSWALK_SCHEMA:
        raise LineageError("crosswalk schema")
    rows = value["rows"]
    if type(rows) is not list or any(
        type(row) is not dict or set(row) != _ROW_KEYS for row in rows
    ):
        raise LineageError("crosswalk row schema")
    if [row["projection_index"] for row in rows] != list(range(len(rows))):
        raise LineageError("projection order")
    core = {key: value[key] for key in value if key != "crosswalk_commitment_sha256"}
    if value["crosswalk_commitment_sha256"] != _semantic_sha(
        b"setec-passage-source-lineage-crosswalk-v1\n", core,
    ):
        raise LineageError("crosswalk commitment")
