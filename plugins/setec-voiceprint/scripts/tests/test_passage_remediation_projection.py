from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest


import author_corpus_export as ace  # noqa: E402
import passage_lineage_crosswalk as lineage  # noqa: E402
import passage_remediation_projection as remediation  # noqa: E402


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _source(text: str, digit: str) -> tuple[dict, bytes]:
    payload = text.encode()
    content = ace._sha(payload)
    content_hex = content.removeprefix("sha256:")
    row = {
        "schema": ace.RECORD_SCHEMA,
        "id": "",
        "persona": "owner",
        "register": "personal.letter",
        "role": "author",
        "text_path": (
            f"texts/{content_hex[:2]}/{content_hex[2:4]}/{content_hex}.txt"
        ),
        "source_entry_fingerprint": "src:hmac-sha256:" + digit * 64,
        "source_group": "grp:hmac-sha256:" + digit * 64,
        "conversation_id": None,
        "date": "2026-07-29",
        "unit_kind": "document",
        "unit_index": 0,
        "unit_count": 1,
        "corpus_role": "identity_baseline",
        "use": ["voice_profile"],
        "consent_status": "author_consent",
        "ai_status": "pre_ai_human",
        "source_kind": "document_local",
        "content_sha256": content,
        "normalized_text_sha256": ace._sha(
            ace._normalize_text(text).encode()
        ),
    }
    row["id"] = ace._record_id(row)
    return row, payload


def _lineage(
    items: list[tuple[str, str]],
    *,
    split_first: bool = False,
) -> tuple[lineage.LineageProjection, dict[str, dict]]:
    pairs = [_source(text, digit) for text, digit in items]
    records = sorted((row for row, _ in pairs), key=lambda row: row["id"])
    texts = {row["content_sha256"]: payload for row, payload in pairs}
    specs = []
    for row in records:
        text = texts[row["content_sha256"]].decode()
        boundaries = (
            [(0, len(text) // 2), (len(text) // 2, len(text))]
            if split_first and row == records[0]
            else [(0, len(text))]
        )
        for index, (start, end) in enumerate(boundaries):
            specs.append({
                "evaluation_partition": "qualification",
                "source_group": _digest(f"group-{row['id']}-{index}"),
                "document_family": _digest(f"family-{row['id']}-{index}"),
                "duplicate_component": _digest(
                    f"duplicate-{row['id']}-{index}"
                ),
                "loss_mask_intervals": [],
                "slices": [{
                    "source_doc_id": row["id"],
                    "source_char_start": start,
                    "source_char_end": end,
                }],
            })
    result = lineage.build_lineage_projection(
        records=records,
        texts=texts,
        unit_specs=specs,
        source_population_commitment_sha256=_digest("source"),
        author_corpus_export_receipt_sha256=_digest("receipt"),
        authorized_by="owner",
        attested_at="2026-07-29T12:00:00Z",
    )
    by_text = {
        texts[row["content_sha256"]].decode(): row
        for row in records
    }
    return result, by_text


def _passage(row: dict, start: int, end: int, passage_id: str) -> dict:
    return {
        "char_end": end,
        "char_start": start,
        "n_words": 1,
        "ordinal": 0,
        "passage_id": passage_id,
        "sha256": hashlib.sha256(passage_id.encode()).hexdigest(),
        "source_doc_id": row["id"],
        "source_manifest": "records.jsonl",
    }


def _occurrence(row: dict, start: int, end: int, span_id: str) -> dict:
    return {
        "char_end": end,
        "char_start": start,
        "n_words": 1,
        "sha256": hashlib.sha256(span_id.encode()).hexdigest(),
        "source_doc_id": row["id"],
        "source_manifest": "records.jsonl",
        "span_id": span_id,
        "token_end": 0,
        "token_start": 0,
    }


def _inventory(clusters: list[dict], spans: list[dict]) -> dict:
    return {
        "mode": "passages",
        "provenance": {
            "duplicated_regions": [],
            "passage_clusters": clusters,
            "repeated_spans": spans,
        },
    }


def _project(
    inventory: dict,
    projection: lineage.LineageProjection,
    *,
    stage_a_passages: list[dict] | None = None,
):
    if stage_a_passages is None:
        stage_a_passages = [
            passage
            for cluster in inventory["provenance"]["passage_clusters"]
            for passage in cluster["passages"]
        ]
    return remediation.project_remediation(
        inventory=inventory,
        inventory_sha256=_digest("inventory"),
        lineage_projection=projection,
        stage_a_passages=stage_a_passages,
    )


def test_stage_a_and_b_project_masks_components_and_pair_exclusions():
    projection, source = _lineage([("abcdefghij", "a"), ("klmnopqrst", "b")])
    left = _passage(source["abcdefghij"], 0, 5, "left")
    dropped = _passage(source["klmnopqrst"], 0, 5, "dropped")
    span = {
        "n_occurrences": 2,
        "n_words": 1,
        "occurrences": [
            _occurrence(source["abcdefghij"], 5, 10, "span-left"),
            _occurrence(source["klmnopqrst"], 5, 10, "span-right"),
        ],
        "span_sha256": hashlib.sha256(b"span").hexdigest(),
    }
    result = _project(
        _inventory([{
            "dropped": ["dropped"],
            "passages": [left, dropped],
            "representative": "left",
        }], [span]),
        projection,
    )

    masks_by_text = {
        projection.unit_texts[row["unit_id"]]: row["loss_mask_intervals"]
        for row in result.projected_population_rows
    }
    assert masks_by_text == {
        "abcdefghij": [[5, 10]],
        "klmnopqrst": [[0, 10]],
    }
    assert len(result.evidence) == 3
    assert all(row["pairing_excluded"] for row in result.pair_exclusion_projection)
    assert len({
        row["duplicate_component"]
        for row in result.projected_population_rows
    }) == 1


def test_stage_a_representative_is_grouped_but_not_masked():
    projection, source = _lineage([("abcdefghij", "a"), ("klmnopqrst", "b")])
    representative = _passage(source["abcdefghij"], 0, 5, "representative")
    dropped = _passage(source["klmnopqrst"], 0, 5, "dropped")
    result = _project(
        _inventory([{
            "dropped": ["dropped"],
            "passages": [representative, dropped],
            "representative": "representative",
        }], []),
        projection,
    )

    exclusions = {
        projection.unit_texts[row["unit_id"]]: row["pairing_excluded"]
        for row in result.pair_exclusion_projection
    }
    assert exclusions == {"abcdefghij": False, "klmnopqrst": True}


def test_complete_stage_a_partition_itemizes_noncluster_and_inverse_refs():
    projection, source = _lineage([("abcdefghij", "a"), ("klmnopqrst", "b")])
    representative = _passage(source["abcdefghij"], 0, 3, "representative")
    dropped = _passage(source["klmnopqrst"], 0, 3, "dropped")
    noncluster = _passage(source["abcdefghij"], 4, 8, "noncluster")
    inventory = _inventory([{
        "dropped": ["dropped"],
        "passages": [representative, dropped],
        "representative": "representative",
    }], [])
    inventory["n_passages"] = 3

    result = _project(
        inventory,
        projection,
        stage_a_passages=[noncluster, dropped, representative],
    )

    assert result.crosswalk_projection == projection.crosswalk["rows"]
    assert {
        row["passage_id"]: (row["partition"], row["disposition"])
        for row in result.passages
    } == {
        "representative": ("cluster_member", "representative"),
        "dropped": ("cluster_member", "nonrepresentative"),
        "noncluster": ("noncluster", "not_assessed"),
    }
    evidence_to_units = {
        row["evidence_id"]: set(row["unit_refs"]) for row in result.evidence
    }
    unit_to_evidence = {
        row["unit_id"]: set(row["evidence_refs"]) for row in result.units
    }
    assert all(
        (unit_id in refs) == (evidence_id in unit_to_evidence[unit_id])
        for evidence_id, refs in evidence_to_units.items()
        for unit_id in unit_to_evidence
    )


def test_complete_stage_a_partition_refuses_missing_noncluster():
    projection, source = _lineage([("abcdefghij", "a")])
    clustered = _passage(source["abcdefghij"], 0, 3, "clustered")
    inventory = _inventory([{
        "dropped": [],
        "passages": [clustered],
        "representative": "clustered",
    }], [])
    inventory["n_passages"] = 2

    with pytest.raises(
        remediation.RemediationError,
        match="complete passage partition invalid",
    ):
        _project(
            inventory,
            projection,
            stage_a_passages=[clustered],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_doc_id", "not-a-digest"),
        ("n_words", 0),
    ],
)
def test_complete_stage_a_partition_refuses_invalid_scalars(
    field: str,
    value: object,
):
    projection, source = _lineage([("abcdefghij", "a")])
    passage = _passage(source["abcdefghij"], 0, 3, "passage")
    passage[field] = value
    inventory = _inventory([{
        "dropped": [],
        "passages": [passage],
        "representative": "passage",
    }], [])

    with pytest.raises(
        remediation.RemediationError,
        match="complete passage partition invalid",
    ):
        _project(inventory, projection, stage_a_passages=[passage])


def test_single_unit_repetition_preserves_component_but_adds_mask():
    projection, source = _lineage([("abcdefghij", "a")])
    before = projection.population_rows[0]["duplicate_component"]
    span = {
        "n_occurrences": 2,
        "n_words": 1,
        "occurrences": [
            _occurrence(source["abcdefghij"], 0, 2, "one"),
            _occurrence(source["abcdefghij"], 4, 6, "two"),
        ],
        "span_sha256": hashlib.sha256(b"span").hexdigest(),
    }

    result = _project(_inventory([], [span]), projection)

    assert result.projected_population_rows[0]["duplicate_component"] == before
    assert result.projected_population_rows[0]["loss_mask_intervals"] == [
        [0, 2],
        [4, 6],
    ]


def test_occurrence_crossing_unit_boundary_projects_without_clipping():
    projection, source = _lineage([("abcdefghij", "a")], split_first=True)
    span = {
        "n_occurrences": 1,
        "n_words": 1,
        "occurrences": [
            _occurrence(source["abcdefghij"], 3, 7, "crossing"),
        ],
        "span_sha256": hashlib.sha256(b"span").hexdigest(),
    }

    result = _project(_inventory([], [span]), projection)

    assert len(result.evidence[0]["unit_refs"]) == 2
    assert sorted(
        row["loss_mask_intervals"]
        for row in result.projected_population_rows
    ) == [[[0, 2]], [[3, 5]]]


def test_new_edge_crossing_register_boundary_is_refused():
    projection, source = _lineage([("abcdefghij", "a"), ("klmnopqrst", "b")])
    changed_metadata = copy.deepcopy(projection.snapshot_metadata)
    changed_metadata["units"][1]["register"] = "professional.email"
    changed_projection = lineage.LineageProjection(
        crosswalk=projection.crosswalk,
        population_rows=projection.population_rows,
        snapshot_metadata=changed_metadata,
        unit_texts=projection.unit_texts,
    )
    one = _passage(source["abcdefghij"], 0, 5, "one")
    two = _passage(source["klmnopqrst"], 0, 5, "two")

    with pytest.raises(remediation.RemediationError, match="boundary invariant"):
        _project(
            _inventory([{
                "dropped": ["two"],
                "passages": [one, two],
                "representative": "one",
            }], []),
            changed_projection,
        )
