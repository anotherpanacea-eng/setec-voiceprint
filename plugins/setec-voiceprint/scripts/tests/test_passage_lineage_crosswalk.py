from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest


import author_corpus_export as ace  # noqa: E402
import passage_lineage_crosswalk as lineage  # noqa: E402
import reconstructibility_probe_set as spec75  # noqa: E402
from conftest import _digest  # noqa: E402


SOURCE_COMMITMENT = "sha256:" + "1" * 64
EXPORT_RECEIPT = "sha256:" + "2" * 64


def _record(text: str, digit: str) -> tuple[dict[str, object], bytes]:
    payload = text.encode()
    content_sha = ace._sha(payload)
    normalized_sha = ace._sha(
        ace._normalize_text(text).encode("utf-8")
    )
    content_hex = content_sha.removeprefix("sha256:")
    row: dict[str, object] = {
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
        "content_sha256": content_sha,
        "normalized_text_sha256": normalized_sha,
    }
    row["id"] = ace._record_id(row)
    return row, payload


def _population(*items: tuple[str, str]) -> tuple[list[dict], dict[str, bytes]]:
    pairs = [_record(text, digit) for text, digit in items]
    rows = sorted((row for row, _ in pairs), key=lambda row: row["id"])
    texts = {
        row["content_sha256"]: payload
        for row, payload in pairs
    }
    return rows, texts


def _unit(slices: list[dict], *, suffix: str = "a") -> dict:
    return {
        "evaluation_partition": "qualification",
        "source_group": _digest(f"group-{suffix}"),
        "document_family": _digest(f"family-{suffix}"),
        "duplicate_component": _digest(f"duplicate-{suffix}"),
        "loss_mask_intervals": [],
        "slices": slices,
    }


def _slice(row: dict, start: int, end: int) -> dict:
    return {
        "source_doc_id": row["id"],
        "source_char_start": start,
        "source_char_end": end,
    }


def _build(records: list[dict], texts: dict[str, bytes], specs: list[dict]):
    return lineage.build_lineage_projection(
        records=records,
        texts=texts,
        unit_specs=specs,
        source_population_commitment_sha256=SOURCE_COMMITMENT,
        author_corpus_export_receipt_sha256=EXPORT_RECEIPT,
        authorized_by="owner",
        attested_at="2026-07-29T12:00:00Z",
    )


def test_single_source_projection_is_valid_and_bound():
    records, texts = _population(("Alpha beta.", "a"))
    result = _build(
        records, texts, [_unit([_slice(records[0], 0, len("Alpha beta."))])],
    )

    spec75.validate_population(result.population_rows)
    lineage.validate_crosswalk_shape(result.crosswalk)
    assert list(result.unit_texts.values()) == ["Alpha beta."]
    assert result.crosswalk["rows"][0]["source_doc_id"] == records[0]["id"]
    assert (
        result.crosswalk["population_manifest_sha256"]
        == result.snapshot_metadata["population_manifest_sha256"]
    )


def test_split_and_concatenated_sources_preserve_exact_character_bijection():
    records, texts = _population(("Alpha beta.", "a"), ("Gamma delta.", "b"))
    by_text = {
        texts[row["content_sha256"]].decode(): row
        for row in records
    }
    alpha = by_text["Alpha beta."]
    gamma = by_text["Gamma delta."]
    specs = [
        _unit([_slice(alpha, 0, 6)], suffix="first"),
        _unit(
            [
                _slice(alpha, 6, len("Alpha beta.")),
                _slice(gamma, 0, len("Gamma delta.")),
            ],
            suffix="second",
        ),
    ]

    result = _build(records, texts, specs)

    assert sorted(result.unit_texts.values()) == [
        "Alpha ",
        "beta.Gamma delta.",
    ]
    assert len(result.crosswalk["rows"]) == 3
    lineage.validate_crosswalk_shape(result.crosswalk)


@pytest.mark.parametrize(
    "slices",
    [
        [(0, 5), (6, 11)],
        [(0, 7), (6, 11)],
    ],
)
def test_gap_or_overlap_is_refused(slices: list[tuple[int, int]]):
    records, texts = _population(("abcdefghijk", "a"))
    specs = [
        _unit([_slice(records[0], start, end)], suffix=str(index))
        for index, (start, end) in enumerate(slices)
    ]

    with pytest.raises(
        lineage.LineageError, match="source projection is not a bijection"
    ):
        _build(records, texts, specs)


def test_concatenated_source_metadata_mismatch_is_refused():
    records, texts = _population(("Alpha.", "a"), ("Beta.", "b"))
    changed = copy.deepcopy(records)
    changed[1]["register"] = "professional.email"
    changed[1]["id"] = ace._record_id(changed[1])
    changed.sort(key=lambda row: row["id"])

    with pytest.raises(lineage.LineageError, match="unit metadata mismatch"):
        _build(
            changed,
            texts,
            [_unit([
                _slice(changed[0], 0, len(texts[changed[0]["content_sha256"]].decode())),
                _slice(changed[1], 0, len(texts[changed[1]["content_sha256"]].decode())),
            ])],
        )


def test_unit_spec_order_does_not_change_projection():
    records, texts = _population(("abcdefghij", "a"))
    first = _unit([_slice(records[0], 0, 5)], suffix="first")
    second = _unit([_slice(records[0], 5, 10)], suffix="second")

    forward = _build(records, texts, [first, second])
    reverse = _build(records, texts, [second, first])

    assert forward.population_rows == reverse.population_rows
    assert forward.crosswalk == reverse.crosswalk


def test_source_identity_changes_derived_unit_identity():
    records_a, texts_a = _population(("same text", "a"))
    records_b, texts_b = _population(("same text", "b"))
    result_a = _build(
        records_a, texts_a, [_unit([_slice(records_a[0], 0, 9)])],
    )
    result_b = _build(
        records_b, texts_b, [_unit([_slice(records_b[0], 0, 9)])],
    )

    assert (
        result_a.population_rows[0]["content_sha256"]
        == result_b.population_rows[0]["content_sha256"]
    )
    assert (
        result_a.population_rows[0]["unit_id"]
        != result_b.population_rows[0]["unit_id"]
    )


def test_out_of_bounds_loss_mask_is_refused():
    records, texts = _population(("short", "a"))
    spec = _unit([_slice(records[0], 0, 5)])
    spec["loss_mask_intervals"] = [[0, 6]]

    with pytest.raises(lineage.LineageError, match="mask out of bounds"):
        _build(records, texts, [spec])


def test_crosswalk_commitment_mutation_is_refused():
    records, texts = _population(("Alpha beta.", "a"))
    result = _build(
        records, texts, [_unit([_slice(records[0], 0, len("Alpha beta."))])],
    )
    mutated = copy.deepcopy(result.crosswalk)
    mutated["rows"][0]["source_char_end"] -= 1

    with pytest.raises(lineage.LineageError, match="crosswalk commitment"):
        lineage.validate_crosswalk_shape(mutated)
