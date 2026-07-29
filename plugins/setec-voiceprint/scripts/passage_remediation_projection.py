"""Deterministic Spec-80 evidence-to-Spec-75 remediation projection.

This is the derivation core.  It consumes already verified inventory and
lineage objects; artifact admission, authority minting, and publication remain
the responsibility of the future transaction wrapper.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from passage_lineage_crosswalk import LineageError, LineageProjection, _semantic_sha


class RemediationError(ValueError):
    pass


@dataclass(frozen=True)
class RemediationProjection:
    projected_population_rows: list[dict[str, Any]]
    passages: list[dict[str, Any]]
    crosswalk_projection: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    units: list[dict[str, Any]]
    pair_exclusion_projection: list[dict[str, Any]]


class _UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a == b:
            return
        if b.encode("utf-8") < a.encode("utf-8"):
            a, b = b, a
        self.parent[b] = a


def _prefixed(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _normalize_masks(intervals: list[list[int]], length: int) -> list[list[int]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    if any(
        type(item) is not list
        or len(item) != 2
        or any(type(value) is not int for value in item)
        or item[0] < 0
        or item[0] >= item[1]
        or item[1] > length
        for item in ordered
    ):
        raise RemediationError("mask invariant")
    result = [ordered[0][:]]
    for start, end in ordered[1:]:
        if start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return [[0, length]] if result == [[0, length]] else result


_PASSAGE_KEYS = {
    "passage_id", "source_doc_id", "source_manifest", "ordinal",
    "char_start", "char_end", "n_words", "sha256",
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _complete_passage_partition(
    inventory: dict[str, Any],
    stage_a_passages: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return the complete Spec-80 §9 Stage-A partition.

    The complete passage list is a live frozen-verifier output, not something
    the itemized cluster report can reconstruct.  Requiring it explicitly
    prevents count-only noncluster inference.
    """
    if type(stage_a_passages) is not list:
        raise RemediationError("complete passage partition required")
    passages: dict[str, dict[str, Any]] = {}
    for row in stage_a_passages:
        if (
            type(row) is not dict
            or set(row) != _PASSAGE_KEYS
            or type(row["passage_id"]) is not str
            or not row["passage_id"]
            or type(row["source_doc_id"]) is not str
            or not _DIGEST.fullmatch(row["source_doc_id"])
            or type(row["source_manifest"]) is not str
            or type(row["ordinal"]) is not int
            or isinstance(row["ordinal"], bool)
            or row["ordinal"] < 0
            or type(row["char_start"]) is not int
            or isinstance(row["char_start"], bool)
            or type(row["char_end"]) is not int
            or isinstance(row["char_end"], bool)
            or row["char_start"] < 0
            or row["char_start"] >= row["char_end"]
            or type(row["n_words"]) is not int
            or isinstance(row["n_words"], bool)
            or row["n_words"] <= 0
            or type(row["sha256"]) is not str
            or len(row["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in row["sha256"])
            or row["passage_id"] in passages
        ):
            raise RemediationError("complete passage partition invalid")
        passages[row["passage_id"]] = row

    clustered: dict[str, tuple[int, str]] = {}
    clusters = inventory["provenance"]["passage_clusters"]
    for cluster_index, cluster in enumerate(clusters):
        if (
            type(cluster) is not dict
            or set(cluster) != {"representative", "dropped", "passages"}
            or type(cluster["representative"]) is not str
            or type(cluster["dropped"]) is not list
            or type(cluster["passages"]) is not list
        ):
            raise RemediationError("cluster partition invalid")
        member_ids = [row.get("passage_id") for row in cluster["passages"]]
        expected_ids = [cluster["representative"], *cluster["dropped"]]
        if (
            member_ids != expected_ids
            or len(member_ids) != len(set(member_ids))
            or any(type(value) is not str for value in expected_ids)
        ):
            raise RemediationError("cluster partition invalid")
        for member, passage_id in zip(cluster["passages"], member_ids):
            if passage_id not in passages or member != passages[passage_id]:
                raise RemediationError("cluster passage not in complete partition")
            if passage_id in clustered:
                raise RemediationError("duplicate clustered passage")
            disposition = (
                "representative"
                if passage_id == cluster["representative"]
                else "nonrepresentative"
            )
            clustered[passage_id] = (cluster_index, disposition)

    declared_count = inventory.get("n_passages")
    if (
        declared_count is not None
        and (
            type(declared_count) is not int
            or isinstance(declared_count, bool)
            or declared_count != len(passages)
        )
    ):
        raise RemediationError("complete passage partition invalid")

    partition = []
    for passage_id, row in passages.items():
        cluster = clustered.get(passage_id)
        partition.append({
            "source_doc_id": row["source_doc_id"],
            "passage_id": passage_id,
            "char_start": row["char_start"],
            "char_end": row["char_end"],
            "n_words": row["n_words"],
            "raw_text_sha256": _prefixed(row["sha256"]),
            "partition": "cluster_member" if cluster is not None else "noncluster",
            "disposition": cluster[1] if cluster is not None else "not_assessed",
        })
    partition.sort(key=lambda row: (
        row["source_doc_id"], row["char_start"], row["char_end"],
        row["passage_id"],
    ))
    return passages, partition


def _project_interval(
    rows_by_source: dict[str, list[dict[str, Any]]],
    source_doc_id: str,
    char_start: int,
    char_end: int,
) -> tuple[list[str], list[tuple[str, int, int]]]:
    if char_start < 0 or char_start >= char_end:
        raise RemediationError("evidence interval")
    cursor = char_start
    projected: list[tuple[str, int, int]] = []
    for row in rows_by_source.get(source_doc_id, []):
        left = max(char_start, row["source_char_start"])
        right = min(char_end, row["source_char_end"])
        if left >= right:
            continue
        if left != cursor:
            raise RemediationError("evidence projection gap")
        offset = left - row["source_char_start"]
        unit_start = row["unit_char_start"] + offset
        projected.append((
            row["unit_id"],
            unit_start,
            unit_start + (right - left),
        ))
        cursor = right
    if cursor != char_end:
        raise RemediationError("evidence projection gap")
    refs = sorted({item[0] for item in projected}, key=lambda value: value.encode())
    return refs, projected


def _stage_a_id(
    inventory_sha256: str,
    cluster_index: int,
    passage: dict[str, Any],
) -> str:
    return _semantic_sha(
        b"setec-passage-stage-a-evidence-id-v1\n",
        {
            "inventory_sha256": inventory_sha256,
            "cluster_index": cluster_index,
            "passage_id": passage["passage_id"],
            "source_doc_id": passage["source_doc_id"],
            "char_start": passage["char_start"],
            "char_end": passage["char_end"],
            "passage_sha256": _prefixed(passage["sha256"]),
            "disposition": "nonrepresentative",
        },
    )


def _stage_b_id(
    inventory_sha256: str,
    repeated_span_index: int,
    span: dict[str, Any],
    occurrence_index: int,
    occurrence: dict[str, Any],
) -> str:
    return _semantic_sha(
        b"setec-passage-stage-b-evidence-id-v1\n",
        {
            "inventory_sha256": inventory_sha256,
            "repeated_span_index": repeated_span_index,
            "span_sha256": _prefixed(span["span_sha256"]),
            "occurrence_index": occurrence_index,
            "occurrence_span_id": occurrence["span_id"],
            "source_doc_id": occurrence["source_doc_id"],
            "token_start": occurrence["token_start"],
            "token_end": occurrence["token_end"],
            "char_start": occurrence["char_start"],
            "char_end": occurrence["char_end"],
            "n_words": occurrence["n_words"],
            "normalized_span_sha256": _prefixed(span["span_sha256"]),
            "raw_text_sha256": _prefixed(occurrence["sha256"]),
        },
    )


def project_remediation(
    *,
    inventory: dict[str, Any],
    inventory_sha256: str,
    lineage_projection: LineageProjection,
    stage_a_passages: list[dict[str, Any]],
) -> RemediationProjection:
    """Project all itemized Stage-A drops and Stage-B occurrences onto units."""
    try:
        from passage_lineage_crosswalk import validate_crosswalk_shape

        validate_crosswalk_shape(lineage_projection.crosswalk)
    except LineageError as exc:
        raise RemediationError("lineage refused") from exc
    if inventory.get("mode") != "passages":
        raise RemediationError("inventory mode")
    provenance = inventory.get("provenance")
    if type(provenance) is not dict:
        raise RemediationError("inventory provenance")
    clusters = provenance.get("passage_clusters")
    spans = provenance.get("repeated_spans")
    if type(clusters) is not list or type(spans) is not list:
        raise RemediationError("inventory provenance")

    source_rows: dict[str, list[dict[str, Any]]] = {}
    projection_refs: dict[str, list[str]] = {}
    for row in lineage_projection.crosswalk["rows"]:
        source_rows.setdefault(row["source_doc_id"], []).append(row)
        projection_refs.setdefault(row["unit_id"], []).append(row["projection_id"])
    for rows in source_rows.values():
        rows.sort(key=lambda row: row["source_char_start"])

    population = lineage_projection.population_rows
    by_unit = {row["unit_id"]: row for row in population}
    metadata = {
        row["unit_id"]: row
        for row in lineage_projection.snapshot_metadata["units"]
    }
    if set(by_unit) != set(metadata) or set(by_unit) != set(lineage_projection.unit_texts):
        raise RemediationError("unit join")

    new_masks: dict[str, list[list[int]]] = {unit_id: [] for unit_id in by_unit}
    evidence_refs: dict[str, set[str]] = {unit_id: set() for unit_id in by_unit}
    evidence: list[dict[str, Any]] = []
    new_edge_groups: list[list[str]] = []
    passages, passage_partition = _complete_passage_partition(
        inventory, stage_a_passages,
    )

    for cluster_index, cluster in enumerate(clusters):
        cluster_units: set[str] = set()
        for member in cluster["passages"]:
            refs, _ = _project_interval(
                source_rows,
                member["source_doc_id"],
                member["char_start"],
                member["char_end"],
            )
            cluster_units.update(refs)
        new_edge_groups.append(sorted(cluster_units, key=lambda value: value.encode()))
        for passage_id in cluster["dropped"]:
            passage = passages.get(passage_id)
            if passage is None:
                raise RemediationError("missing dropped passage")
            evidence_id = _stage_a_id(inventory_sha256, cluster_index, passage)
            refs, projected = _project_interval(
                source_rows,
                passage["source_doc_id"],
                passage["char_start"],
                passage["char_end"],
            )
            for unit_id, start, end in projected:
                new_masks[unit_id].append([start, end])
                evidence_refs[unit_id].add(evidence_id)
            evidence.append({
                "kind": "stage_a_nonrepresentative",
                "evidence_id": evidence_id,
                "cluster_index": cluster_index,
                "passage_id": passage["passage_id"],
                "source_doc_id": passage["source_doc_id"],
                "char_start": passage["char_start"],
                "char_end": passage["char_end"],
                "n_words": passage["n_words"],
                "raw_text_sha256": _prefixed(passage["sha256"]),
                "disposition": "nonrepresentative",
                "unit_refs": refs,
            })

    for span_index, span in enumerate(spans):
        span_units: set[str] = set()
        for occurrence_index, occurrence in enumerate(span["occurrences"]):
            evidence_id = _stage_b_id(
                inventory_sha256, span_index, span, occurrence_index, occurrence,
            )
            refs, projected = _project_interval(
                source_rows,
                occurrence["source_doc_id"],
                occurrence["char_start"],
                occurrence["char_end"],
            )
            span_units.update(refs)
            for unit_id, start, end in projected:
                new_masks[unit_id].append([start, end])
                evidence_refs[unit_id].add(evidence_id)
            evidence.append({
                "kind": "stage_b_occurrence",
                "evidence_id": evidence_id,
                "repeated_span_index": span_index,
                "occurrence_index": occurrence_index,
                "occurrence_span_id": occurrence["span_id"],
                "source_doc_id": occurrence["source_doc_id"],
                "token_start": occurrence["token_start"],
                "token_end": occurrence["token_end"],
                "char_start": occurrence["char_start"],
                "char_end": occurrence["char_end"],
                "n_words": occurrence["n_words"],
                "normalized_span_sha256": _prefixed(span["span_sha256"]),
                "raw_text_sha256": _prefixed(occurrence["sha256"]),
                "unit_refs": refs,
            })
        new_edge_groups.append(sorted(span_units, key=lambda value: value.encode()))

    if len({row["evidence_id"] for row in evidence}) != len(evidence):
        raise RemediationError("evidence collision")

    union = _UnionFind(list(by_unit))
    by_component: dict[str, list[str]] = {}
    for unit_id, row in by_unit.items():
        by_component.setdefault(row["duplicate_component"], []).append(unit_id)
    for members in by_component.values():
        for member in members[1:]:
            union.union(members[0], member)

    def boundary(unit_id: str) -> tuple[str, str, str]:
        return (
            by_unit[unit_id]["evaluation_partition"],
            metadata[unit_id]["register"],
            metadata[unit_id]["source_kind"],
        )

    changed_members: set[str] = set()
    for group in new_edge_groups:
        if not group:
            raise RemediationError("empty remediation edge")
        if len({boundary(unit_id) for unit_id in group}) != 1:
            raise RemediationError("boundary invariant")
        if len(group) == 1:
            continue
        changed_members.update(group)
        for unit_id in group[1:]:
            union.union(group[0], unit_id)

    final_groups: dict[str, list[str]] = {}
    for unit_id in by_unit:
        final_groups.setdefault(union.find(unit_id), []).append(unit_id)
    changed_roots = {union.find(unit_id) for unit_id in changed_members}
    projected_component: dict[str, str] = {}
    for root, members in final_groups.items():
        if len({boundary(unit_id) for unit_id in members}) != 1:
            raise RemediationError("boundary invariant")
        if root in changed_roots:
            component = _semantic_sha(
                b"setec-passage-duplicate-component-v1\n",
                sorted(members, key=lambda value: value.encode()),
            )
        else:
            components = {by_unit[unit_id]["duplicate_component"] for unit_id in members}
            if len(components) != 1:
                raise RemediationError("input component")
            component = next(iter(components))
        for unit_id in members:
            projected_component[unit_id] = component

    projected_rows: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for input_row in population:
        unit_id = input_row["unit_id"]
        text_length = len(lineage_projection.unit_texts[unit_id])
        masks = _normalize_masks(
            input_row["loss_mask_intervals"] + new_masks[unit_id],
            text_length,
        )
        output_row = dict(input_row)
        output_row["duplicate_component"] = projected_component[unit_id]
        output_row["loss_mask_intervals"] = masks
        projected_rows.append(output_row)
        refs = sorted(evidence_refs[unit_id], key=lambda value: value.encode())
        units.append({
            "unit_id": unit_id,
            "content_sha256": input_row["content_sha256"],
            "evaluation_partition": input_row["evaluation_partition"],
            "register": metadata[unit_id]["register"],
            "source_kind": metadata[unit_id]["source_kind"],
            "source_group": input_row["source_group"],
            "document_family": input_row["document_family"],
            "input_duplicate_component": input_row["duplicate_component"],
            "input_loss_mask_intervals": input_row["loss_mask_intervals"],
            "projected_duplicate_component": projected_component[unit_id],
            "projected_loss_mask_intervals": masks,
            "pairing_excluded": bool(refs),
            "evidence_refs": refs,
            "projection_refs": sorted(
                projection_refs[unit_id], key=lambda value: value.encode(),
            ),
        })
        exclusions.append({
            "unit_id": unit_id,
            "pairing_excluded": bool(refs),
        })

    return RemediationProjection(
        projected_population_rows=projected_rows,
        passages=passage_partition,
        crosswalk_projection=[
            dict(row) for row in lineage_projection.crosswalk["rows"]
        ],
        evidence=sorted(
            evidence,
            key=lambda row: (
                row["kind"],
                row["source_doc_id"],
                row["char_start"],
                row["char_end"],
                row["evidence_id"],
            ),
        ),
        units=sorted(units, key=lambda row: row["unit_id"].encode()),
        pair_exclusion_projection=sorted(
            exclusions, key=lambda row: row["unit_id"].encode(),
        ),
    )
