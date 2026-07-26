#!/usr/bin/env python3
"""Spec 73 / H2 register-composition sweep: encoder and H1 binding contract.

Covers the increment that supplies the canonical-encoding layer and the H1
binding layer:

  * Acceptance test 10 (digest/codec exactness) for every normative vector in
    the spec's "Digest preimages and checked golden vectors" table. Each
    preimage is constructed from spec literals and public H1 values, its
    canonical bytes and length are asserted where the spec states them, and
    only then is the digest asserted. No test copies an expected digest into
    the function under test.
  * Acceptance test 6 (classifier same-byte seam) for the receipt-bound load
    and the closed public-result validation, including a table of hostile
    results.
  * Acceptance test 8's equality pins for the fixed ``F``/``D``/``R``/``A``
    domains.
  * The receipt schema half of acceptance test 1: every missing, extra,
    wrong-type, and noncanonical-byte receipt refuses.

All fixtures are generated synthetic data. No private corpus, aggregate,
identifier, path, or prose enters the repository.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import manifest_validator as mv  # type: ignore
import register_sweep as rs  # type: ignore
import shingle_dedup_io  # type: ignore


# --------------------------------------------------------------------------
# Shared bindings
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def binding() -> rs.H1Binding:
    """The real receipt-bound H1 namespace."""
    receipt_path, classifier_path = rs.default_h1_paths()
    return rs.load_h1_binding(
        receipt_path=receipt_path, classifier_path=classifier_path
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


# --------------------------------------------------------------------------
# Encoder core
# --------------------------------------------------------------------------


def test_canonical_json_is_sorted_compact_utf8() -> None:
    assert rs.canonical_json({"b": 1, "a": "é"}) == b'{"a":"\xc3\xa9","b":1}'


def test_canonical_json_refuses_non_finite_and_unencodable() -> None:
    with pytest.raises(rs.InternalError):
        rs.canonical_json({"a": float("nan")})
    with pytest.raises(rs.InternalError):
        rs.canonical_json({"a": object()})


def test_framed_sha256_preimage_is_domain_length_payload() -> None:
    domain = b"setec-register-sweep-scope-v2\n"
    payload = b"payload"
    expected = hashlib.sha256(
        domain + struct.pack(">Q", len(payload)) + payload
    ).hexdigest()
    assert rs.framed_sha256(domain, payload) == expected


def test_framed_sha256_requires_ascii_domain_with_terminal_lf() -> None:
    with pytest.raises(rs.InternalError):
        rs.framed_sha256(b"no-terminal-lf", b"")
    with pytest.raises(rs.InternalError):
        rs.framed_sha256("setec-register-sweep-scope-v2\n", b"")  # type: ignore[arg-type]


def test_every_frozen_domain_is_ascii_and_lf_terminated() -> None:
    assert len(rs.FROZEN_DOMAINS) == 12
    assert len(set(rs.FROZEN_DOMAINS)) == 12
    for domain in rs.FROZEN_DOMAINS:
        assert type(domain) is bytes
        assert domain.isascii()
        assert domain.endswith(b"\n")
        assert b"\n" not in domain[:-1]


def test_framing_length_prefix_prevents_payload_concatenation_collision() -> None:
    # uint64_be(len) is what separates "ab" + "c" from "a" + "bc"; without it
    # the two would frame identically under one domain.
    domain = rs.DOMAIN_SCOPE
    assert rs.framed_sha256(domain, b"abc") != rs.framed_sha256(domain, b"ab")


def test_prefixed_requires_64_lowercase_hex() -> None:
    assert rs.prefixed("a" * 64) == "sha256:" + "a" * 64
    for bad in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, "sha256:" + "a" * 64):
        with pytest.raises(rs.InternalError):
            rs.prefixed(bad)


# --------------------------------------------------------------------------
# Normative golden vectors (acceptance test 10)
# --------------------------------------------------------------------------


def test_vector_raw_artifact_bytes() -> None:
    assert (
        rs.raw_sha256(b"{}\n")
        == "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356"
    )


def test_vector_raw_document_content() -> None:
    assert rs.prefixed(rs.raw_sha256(b"hello")) == (
        "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_vector_mapping_payload_length_and_digest(binding: rs.H1Binding) -> None:
    payload, digest = rs.mapping_binding(binding.namespace)
    assert len(payload) == 1_147
    decoded = json.loads(payload)
    assert set(decoded) == {
        "canonical_register_to_family",
        "legacy_register_to_family",
        "register_families",
        "taxonomy",
    }
    assert decoded["register_families"] == list(
        binding.namespace["REGISTER_FAMILIES"]
    )
    assert decoded["taxonomy"] == "register_families/v2"
    assert rs.prefixed(digest) == (
        "sha256:8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef"
    )


def test_vector_refusal_contract_payload_and_digest(binding: rs.H1Binding) -> None:
    payload, digest = rs.refusal_contract_binding(binding.namespace)
    assert len(payload) == 140
    assert payload == (
        b'{"field":"refusal_reason","null_when":"scored_family","reasons":'
        b'["short_text","all_weak","exact_top_tie"],'
        b'"taxonomy":"register_families/v2"}'
    )
    assert rs.prefixed(digest) == (
        "sha256:f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"
    )


SCOPE_DIGEST = "90c35fd6716420e63521971c169aaa8f22ef627f329e4be5d83ad1023368612d"
ROW_DIGEST = "e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc"
PROJECTED_MANIFEST_DIGEST = (
    "f300caefbd833e4358b709450f0fbb2f0b6a69f5b3912a23652adcae330f7c69"
)
SCOPED_ROWS_DIGEST = (
    "80ca784deb07b66477fe2234ca1aab7c829342e95d7e0fc1d38d77a78b7cee25"
)
TARGET_PATH_DIGEST = (
    "2edcbe61a8704538d8b618f3d8027f2d2c480f792e9c8a39a40cf287056bf7ea"
)
POSIX_FINGERPRINT_DIGEST = (
    "463792cd5eb6abf1435147ed9b2d6cd636ef07d5b7a645667b2d3101419045b0"
)
DOCUMENT_PLAN_DIGEST = (
    "c38fdcd97e94e714402559599177ea52d71ebff7d8be0f59bb1c3fa97fd2f204"
)
CHECKPOINT_BINDING_DIGEST = (
    "e6b601945c0c4b497bb06e1773922775c03f66fd9057d910ab9048cb7114e4e8"
)
CHECKPOINT_ROW_DIGEST = (
    "1d212509e4f2d749dd9ea0bae1fd66b7ab4a9bc1c3890896c06ac16bce5f9d72"
)
AGGREGATE_DELTA_DIGEST = (
    "f34b82762a72814fd2968e6c0c8bb38404b71db8c8096c0c13b69c56bd7a820f"
)
SHARD_DIGEST = "c69febb9ce37d9e8dde7318d0e691d77d9023d918af54f0302c349319cf7ade8"

MAPPING_DIGEST = "8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef"
REFUSAL_DIGEST = "f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"


def test_vector_default_scope_payload_and_digest() -> None:
    payload, digest = rs.scope_binding(
        use=None, split=None, ai_status=None, persona=None, min_words=100
    )
    assert payload == (
        b'{"ai_status":null,"min_words":100,"persona":null,'
        b'"privacy_policy":"owner_private_v1","split":null,"use":null}'
    )
    assert digest == SCOPE_DIGEST


def test_scope_binding_commits_raw_persona_only_through_the_digest() -> None:
    _, plain = rs.scope_binding(
        use=None, split=None, ai_status=None, persona=None, min_words=100
    )
    payload, selected = rs.scope_binding(
        use=None, split=None, ai_status=None, persona="alias", min_words=100
    )
    assert selected != plain
    assert b"alias" in payload  # private binding preimage only


def test_scope_binding_rejects_out_of_range_min_words() -> None:
    for bad in (0, -1, 1_000_001, True, 1.0, "100"):
        with pytest.raises(rs.InternalError):
            rs.scope_binding(
                use=None,
                split=None,
                ai_status=None,
                persona=None,
                min_words=bad,  # type: ignore[arg-type]
            )


ONE_ROW = {
    "ai_status": "pre_ai_human",
    "manifest_ordinal": 0,
    "path": "docs/a.txt",
    "persona": None,
    "register": "personal",
    "split": "baseline",
    "use": ["baseline"],
}


def test_vector_projected_row_payload_and_digest() -> None:
    payload, digest = rs.projected_row_binding(ONE_ROW)
    assert payload == (
        b'{"ai_status":"pre_ai_human","manifest_ordinal":0,"path":"docs/a.txt",'
        b'"persona":null,"register":"personal","split":"baseline",'
        b'"use":["baseline"]}'
    )
    assert digest == ROW_DIGEST


def test_vector_projected_manifest_digest() -> None:
    payload, digest = rs.projected_manifest_binding([rs.prefixed(ROW_DIGEST)])
    assert payload == b'{"rows":["sha256:' + ROW_DIGEST.encode("ascii") + b'"]}'
    assert digest == PROJECTED_MANIFEST_DIGEST


def test_vector_scoped_rows_digest() -> None:
    payload, digest = rs.scoped_rows_binding(
        [
            {
                "manifest_ordinal": 0,
                "projected_row_sha256": rs.prefixed(ROW_DIGEST),
                "scoped_ordinal": 0,
            }
        ]
    )
    assert json.loads(payload)["rows"][0]["scoped_ordinal"] == 0
    assert digest == SCOPED_ROWS_DIGEST


def test_empty_scope_frames_the_empty_row_list() -> None:
    payload, _ = rs.scoped_rows_binding([])
    assert payload == b'{"rows":[]}'


def test_vector_target_path_digest() -> None:
    payload, digest = rs.target_path_binding("/repo/docs/a.txt")
    assert payload == b"/repo/docs/a.txt"
    assert len(payload) == 16
    assert digest == TARGET_PATH_DIGEST


def test_vector_posix_fingerprint_digest() -> None:
    payload, digest = rs.posix_fingerprint_binding([1, 2, 3, 4, 5])
    assert payload == b'{"fields":[1,2,3,4,5],"platform":"posix"}'
    assert digest == POSIX_FINGERPRINT_DIGEST


def test_posix_fingerprint_field_order_is_load_bearing() -> None:
    _, forward = rs.posix_fingerprint_binding([1, 2, 3, 4, 5])
    _, swapped = rs.posix_fingerprint_binding([2, 1, 3, 4, 5])
    assert forward != swapped


def test_windows_fingerprint_binds_nine_fields_including_change_time() -> None:
    payload, digest = rs.windows_fingerprint_binding([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert payload == (
        b'{"fields":[1,2,3,4,5,6,7,8,9],"platform":"windows"}'
    )
    # change_time is field index 4; mutating only it must move the digest so a
    # same-size write with a restored LastWriteTime still refuses.
    _, restored = rs.windows_fingerprint_binding([1, 2, 3, 4, 99, 6, 7, 8, 9])
    assert digest != restored
    with pytest.raises(rs.InternalError):
        rs.windows_fingerprint_binding([1, 2, 3, 4, 5])


def test_vector_document_plan_digest() -> None:
    payload, digest = rs.document_plan_binding(
        [
            {
                "candidate_index": 0,
                "file_fingerprint_sha256": rs.prefixed(POSIX_FINGERPRINT_DIGEST),
                "projected_row_sha256": rs.prefixed(ROW_DIGEST),
                "scoped_ordinal": 0,
                "target_path_sha256": rs.prefixed(TARGET_PATH_DIGEST),
            }
        ]
    )
    assert json.loads(payload)["documents"][0]["candidate_index"] == 0
    assert digest == DOCUMENT_PLAN_DIGEST


def test_document_plan_rejects_noncontiguous_and_out_of_range_entries() -> None:
    entry = {
        "candidate_index": 0,
        "file_fingerprint_sha256": rs.prefixed(POSIX_FINGERPRINT_DIGEST),
        "projected_row_sha256": rs.prefixed(ROW_DIGEST),
        "scoped_ordinal": 1,
        "target_path_sha256": rs.prefixed(TARGET_PATH_DIGEST),
    }
    with pytest.raises(rs.InternalError):
        rs.document_plan_binding([entry])  # one-based ordinal
    with pytest.raises(rs.InternalError):
        rs.document_plan_binding([{**entry, "scoped_ordinal": 0, "candidate_index": 3}])
    with pytest.raises(rs.InternalError):
        rs.document_plan_binding([{**entry, "scoped_ordinal": 0, "extra": 1}])


def _binding_kwargs() -> dict[str, str]:
    return {
        "classifier_sha256": rs.prefixed("2" * 64),
        "document_plan_sha256": rs.prefixed(DOCUMENT_PLAN_DIGEST),
        "h1_receipt_sha256": rs.prefixed("3" * 64),
        "mapping_sha256": rs.prefixed(MAPPING_DIGEST),
        "projected_manifest_sha256": rs.prefixed(PROJECTED_MANIFEST_DIGEST),
        "refusal_contract_sha256": rs.prefixed(REFUSAL_DIGEST),
        "scope_sha256": rs.prefixed(SCOPE_DIGEST),
        "scoped_rows_sha256": rs.prefixed(SCOPED_ROWS_DIGEST),
    }


def test_vector_limits_object_bytes() -> None:
    assert _canonical(rs.LIMITS) == (
        b'{"checkpoint_cumulative_bytes":1677721600,'
        b'"classifier_source_bytes":1048576,"document_bytes":16777216,'
        b'"final_shards":400,"h1_receipt_bytes":65536,'
        b'"manifest_bytes":134217728,"reserved_temporary_names":16,'
        b'"scoped_bytes":8589934592,"scoped_documents":100000,'
        b'"shard_bytes":4194304,"shard_rows":250}'
    )


def test_vector_checkpoint_binding_digest() -> None:
    payload, digest = rs.checkpoint_binding(**_binding_kwargs())
    decoded = json.loads(payload)
    assert decoded["limits"] == rs.LIMITS
    assert decoded["privacy_policy"] == "owner_private_v1"
    assert decoded["immutable_shard_contract_version"] == 1
    # No completed ordinal belongs in the binding: progress is sealed by the
    # shard chain, so the binding is a pure function of the run's inputs.
    assert "next_scoped_ordinal" not in decoded
    assert "completed" not in payload.decode("utf-8")
    assert digest == CHECKPOINT_BINDING_DIGEST


def test_checkpoint_binding_requires_prefixed_digests() -> None:
    kwargs = _binding_kwargs()
    kwargs["scope_sha256"] = SCOPE_DIGEST  # unprefixed
    with pytest.raises(rs.InternalError):
        rs.checkpoint_binding(**kwargs)


CANONICAL_ROW = {
    "classified_family": None,
    "content_sha256": "sha256:" + "5" * 64,
    "declared_family": "first_person_essay",
    "document_bytes": 20,
    "manifest_ordinal": 0,
    "projected_row_sha256": "sha256:" + ROW_DIGEST,
    "refusal_reason": "short_text",
    "words": 5,
}


def test_vector_canonical_checkpoint_row_blob() -> None:
    row_json, blob = rs.checkpoint_row_binding(CANONICAL_ROW)
    assert row_json == _canonical(CANONICAL_ROW)
    assert not row_json.endswith(b"\n")
    assert type(blob) is bytes and len(blob) == 32
    assert blob == bytes.fromhex(CHECKPOINT_ROW_DIGEST)
    # Text or hex storage of row_sha256 is invalid.
    assert blob != CHECKPOINT_ROW_DIGEST.encode("ascii")


def test_checkpoint_row_requires_exactly_one_of_family_or_refusal() -> None:
    with pytest.raises(rs.InternalError):
        rs.checkpoint_row_binding({**CANONICAL_ROW, "refusal_reason": None})
    with pytest.raises(rs.InternalError):
        rs.checkpoint_row_binding(
            {**CANONICAL_ROW, "classified_family": "academic"}
        )
    scored = {
        **CANONICAL_ROW,
        "classified_family": "academic",
        "refusal_reason": None,
    }
    rs.checkpoint_row_binding(scored)


def test_checkpoint_row_rejects_extra_and_out_of_range_fields() -> None:
    with pytest.raises(rs.InternalError):
        rs.checkpoint_row_binding({**CANONICAL_ROW, "path": "docs/a.txt"})
    with pytest.raises(rs.InternalError):
        rs.checkpoint_row_binding(
            {**CANONICAL_ROW, "document_bytes": rs.MAX_DOCUMENT_BYTES + 1}
        )
    with pytest.raises(rs.InternalError):
        rs.checkpoint_row_binding({**CANONICAL_ROW, "words": True})


def _coherent_delta(binding: rs.H1Binding) -> dict[str, object]:
    """The spec's coherent one-row delta: 20 bytes, 5 words, short_text."""
    inventories = rs.zero_inventories(
        binding.declared_domain, binding.classified_domain, binding.refusal_domain
    )
    cell = {"documents": 1, "words": 5}
    inventories["declared_family_inventory"]["first_person_essay"] = dict(cell)
    inventories["refusal_inventory"]["short_text"] = dict(cell)
    inventories["match_inventory"]["unresolved"] = dict(cell)
    return {
        "counts": {
            "scoped_documents": 1,
            "scoped_bytes": 20,
            "scoped_words": 5,
            "resolved_declared_documents": 1,
            "resolved_declared_words": 5,
            "unresolved_declared_documents": 0,
            "unresolved_declared_words": 0,
            "classified_documents": 0,
            "classified_words": 0,
            "refused_documents": 1,
            "refused_words": 5,
        },
        **inventories,
    }


def test_vector_aggregate_delta_digest(binding: rs.H1Binding) -> None:
    _, digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    assert digest == AGGREGATE_DELTA_DIGEST


def test_aggregate_delta_requires_the_six_closed_keys(
    binding: rs.H1Binding,
) -> None:
    delta = _coherent_delta(binding)
    with pytest.raises(rs.InternalError):
        rs.aggregate_delta_binding({k: v for k, v in delta.items() if k != "counts"})
    with pytest.raises(rs.InternalError):
        rs.aggregate_delta_binding({**delta, "dominant_family": "academic"})


def test_vector_logical_shard_digest(binding: rs.H1Binding) -> None:
    _, delta_digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    _, row_blob = rs.checkpoint_row_binding(CANONICAL_ROW)
    payload, digest = rs.shard_binding(
        aggregate_delta_sha256=rs.prefixed(delta_digest),
        metadata={
            "checkpoint_binding_sha256": rs.prefixed(CHECKPOINT_BINDING_DIGEST),
            "first_scoped_ordinal": 0,
            "kind": "register",
            "next_scoped_ordinal": 1,
            "prior_shard_sha256": None,
            "schema_version": "setec-register-sweep-checkpoint/2",
            "shard_number": 0,
        },
        rows=[
            {
                "row_json_sha256": rs.prefixed(row_blob.hex()),
                "scoped_ordinal": 0,
            }
        ],
    )
    assert json.loads(payload)["metadata"]["prior_shard_sha256"] is None
    assert digest == SHARD_DIGEST


def test_shard_binding_rejects_unsorted_or_duplicate_ordinals(
    binding: rs.H1Binding,
) -> None:
    _, delta_digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    metadata = {
        "checkpoint_binding_sha256": rs.prefixed(CHECKPOINT_BINDING_DIGEST),
        "first_scoped_ordinal": 0,
        "kind": "register",
        "next_scoped_ordinal": 2,
        "prior_shard_sha256": None,
        "schema_version": "setec-register-sweep-checkpoint/2",
        "shard_number": 0,
    }
    rows = [
        {"row_json_sha256": rs.prefixed("1" * 64), "scoped_ordinal": 1},
        {"row_json_sha256": rs.prefixed("2" * 64), "scoped_ordinal": 0},
    ]
    with pytest.raises(rs.InternalError):
        rs.shard_binding(
            aggregate_delta_sha256=rs.prefixed(delta_digest),
            metadata=metadata,
            rows=rows,
        )
    duplicate = [
        {"row_json_sha256": rs.prefixed("1" * 64), "scoped_ordinal": 0},
        {"row_json_sha256": rs.prefixed("2" * 64), "scoped_ordinal": 0},
    ]
    with pytest.raises(rs.InternalError):
        rs.shard_binding(
            aggregate_delta_sha256=rs.prefixed(delta_digest),
            metadata=metadata,
            rows=duplicate,
        )


def test_shard_metadata_key_set_is_closed(binding: rs.H1Binding) -> None:
    _, delta_digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    with pytest.raises(rs.InternalError):
        rs.shard_binding(
            aggregate_delta_sha256=rs.prefixed(delta_digest),
            metadata={"shard_number": 0},
            rows=[],
        )


def test_each_domain_produces_a_distinct_digest_for_one_payload() -> None:
    payload = b'{"rows":[]}'
    digests = {
        domain: rs.framed_sha256(domain, payload) for domain in rs.FROZEN_DOMAINS
    }
    assert len(set(digests.values())) == len(rs.FROZEN_DOMAINS)


# --------------------------------------------------------------------------
# Fixed domains (acceptance test 8 pins)
# --------------------------------------------------------------------------


def test_fixed_domains_are_equality_pinned(binding: rs.H1Binding) -> None:
    families = binding.namespace["REGISTER_FAMILIES"]
    assert binding.classified_domain == tuple(
        f for f in families if f != "unknown"
    )
    assert binding.declared_domain == binding.classified_domain + ("unknown",)
    assert binding.declared_domain == tuple(families)
    assert binding.refusal_domain == ("short_text", "all_weak", "exact_top_tie")
    assert rs.MATCH_DOMAIN == ("same", "different", "unresolved")
    assert "unknown" not in binding.classified_domain


def test_zero_inventories_cover_every_fixed_cell(binding: rs.H1Binding) -> None:
    inventories = rs.zero_inventories(
        binding.declared_domain, binding.classified_domain, binding.refusal_domain
    )
    assert set(inventories) == {
        "declared_family_inventory",
        "classified_family_inventory",
        "declared_by_classified_family",
        "refusal_inventory",
        "match_inventory",
    }
    assert set(inventories["declared_family_inventory"]) == set(
        binding.declared_domain
    )
    assert set(inventories["classified_family_inventory"]) == set(
        binding.classified_domain
    )
    assert set(inventories["refusal_inventory"]) == set(binding.refusal_domain)
    assert set(inventories["match_inventory"]) == set(rs.MATCH_DOMAIN)
    crosstab = inventories["declared_by_classified_family"]
    assert set(crosstab) == set(binding.declared_domain)
    for row in crosstab.values():
        assert set(row) == set(binding.classified_domain)
    for cell in inventories["declared_family_inventory"].values():
        assert cell == {"documents": 0, "words": 0}


def test_zero_cell_carries_no_rate_or_share() -> None:
    assert rs.zero_cell() == {"documents": 0, "words": 0}


# --------------------------------------------------------------------------
# H1 receipt (acceptance test 1, receipt schema half)
# --------------------------------------------------------------------------


def test_real_receipt_reads_and_validates() -> None:
    receipt_path, _ = rs.default_h1_paths()
    receipt, data = rs.read_h1_receipt(receipt_path)
    assert rs.raw_sha256(data) == rs.H1_RECEIPT_SHA256
    assert data == rs.canonical_json(receipt) + b"\n"
    assert rs.validate_h1_receipt(receipt) is receipt


def test_pinned_receipt_digest_is_the_landed_receipt() -> None:
    # The pinned constant is the first H2 implementation step; it must equal the
    # raw digest of the receipt committed on this tree.
    receipt_path, _ = rs.default_h1_paths()
    assert rs.raw_sha256(receipt_path.read_bytes()) == rs.H1_RECEIPT_SHA256


def _real_receipt() -> dict[str, object]:
    receipt_path, _ = rs.default_h1_paths()
    receipt, _ = rs.read_h1_receipt(receipt_path)
    return receipt


def test_receipt_rejects_every_missing_top_level_field() -> None:
    receipt = _real_receipt()
    for key in list(receipt):
        broken = {k: v for k, v in receipt.items() if k != key}
        with pytest.raises(rs.PolicyRefused):
            rs.validate_h1_receipt(broken)


def test_receipt_rejects_an_extra_field() -> None:
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**_real_receipt(), "evidence_url": "x"})


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", "setec-h1-landing-receipt/1"),
        ("landed_commit", "0" * 40),
        ("landed_commit", "7ffabd34"),
        ("landed_commit", "7FFABD343066585DE2A80C22B4ABEA25D27D5450"),
        ("spec_sha256", "0" * 64),
        ("refusal_spec_path", "specs/37-register-classifier-repair.md"),
        ("refusal_spec_sha256", "0" * 64),
        ("base_classifier_sha256", "0" * 64),
        ("taxonomy", "register_families/v1"),
        ("classifier_sha256", "sha256:" + "0" * 64),
        ("mapping_sha256", 1),
        ("ci", {}),
        ("spec_review", {"reviewed_head": "a" * 40}),
    ],
)
def test_receipt_rejects_wrong_field_values(key: str, value: object) -> None:
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**_real_receipt(), key: value})


@pytest.mark.parametrize(
    "role",
    [
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
    ],
)
def test_receipt_review_objects_are_closed(role: str) -> None:
    receipt = _real_receipt()
    original = dict(receipt[role])  # type: ignore[arg-type]
    for broken in (
        {"reviewed_head": original["reviewed_head"]},
        {**original, "verdict": "APPROVED"},
        {**original, "evidence_url": "https://example.invalid"},
        {**original, "reviewed_head": "z" * 40},
    ):
        with pytest.raises(rs.PolicyRefused):
            rs.validate_h1_receipt({**receipt, role: broken})


@pytest.mark.parametrize(
    "key,value",
    [
        ("branch", "release"),
        ("event", "pull_request"),
        ("event", "workflow_dispatch"),
        ("event", "schedule"),
        ("result", "SUCCESS"),
        ("workflow_name", "ci"),
        ("workflow_path", ".github/workflows/ci.yml"),
        ("workflow_sha256", "0" * 64),
        ("run_id", 0),
        ("run_id", -1),
        ("run_id", True),
        ("run_id", 1.0),
        ("run_id", "30131248170"),
        ("attempt", 0),
        ("head", "0" * 40),
        ("required_jobs", ["pytest"]),
        ("required_jobs", tuple(rs.H1_REQUIRED_JOBS)),
    ],
)
def test_receipt_ci_object_is_closed(key: str, value: object) -> None:
    receipt = _real_receipt()
    ci = {**receipt["ci"], key: value}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**receipt, "ci": ci})


def test_receipt_ci_required_jobs_order_is_load_bearing() -> None:
    receipt = _real_receipt()
    jobs = list(rs.H1_REQUIRED_JOBS)
    jobs[0], jobs[1] = jobs[1], jobs[0]
    ci = {**receipt["ci"], "required_jobs": jobs}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**receipt, "ci": ci})


def test_receipt_ci_head_must_equal_landed_commit() -> None:
    receipt = _real_receipt()
    ci = {**receipt["ci"], "head": "a" * 40}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**receipt, "ci": ci})


def test_receipt_admits_both_allowlisted_workflow_digests() -> None:
    receipt = _real_receipt()
    for allowed in rs.H1_WORKFLOW_SHA256_ALLOWLIST:
        ci = {**receipt["ci"], "workflow_sha256": allowed}  # type: ignore[dict-item]
        assert rs.validate_h1_receipt({**receipt, "ci": ci})


def _write_receipt(path: Path, receipt: dict[str, object]) -> str:
    data = rs.canonical_json(receipt) + b"\n"
    path.write_bytes(data)
    return rs.raw_sha256(data)


def test_receipt_read_requires_canonical_bytes(tmp_path: Path) -> None:
    receipt = _real_receipt()
    canonical = tmp_path / "canonical.json"
    _write_receipt(canonical, receipt)
    rs.read_h1_receipt(canonical)

    for name, data in (
        ("no_lf.json", rs.canonical_json(receipt)),
        ("two_lf.json", rs.canonical_json(receipt) + b"\n\n"),
        ("indented.json", json.dumps(receipt, indent=2).encode("utf-8") + b"\n"),
        (
            # Reversed insertion order: the parsed receipt is already sorted,
            # so sort_keys=False alone would reproduce canonical bytes.
            "unsorted.json",
            json.dumps(
                dict(reversed(list(receipt.items()))),
                sort_keys=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n",
        ),
        ("bom.json", "﻿".encode("utf-8") + rs.canonical_json(receipt) + b"\n"),
        ("not_object.json", b"[]\n"),
        ("malformed.json", b"{\n"),
        ("latin1.json", b'{"a":"\xff"}\n'),
        (
            "duplicate.json",
            b'{"taxonomy":"register_families/v2","taxonomy":"x"}\n',
        ),
        ("nan.json", b'{"a":NaN}\n'),
        ("infinity.json", b'{"a":Infinity}\n'),
    ):
        target = tmp_path / name
        target.write_bytes(data)
        with pytest.raises(rs.PolicyRefused):
            rs.read_h1_receipt(target)


def test_receipt_read_refuses_oversize(tmp_path: Path) -> None:
    target = tmp_path / "big.json"
    target.write_bytes(b'{"a":"' + b"x" * rs.MAX_H1_RECEIPT_BYTES + b'"}\n')
    with pytest.raises(rs.PolicyRefused):
        rs.read_h1_receipt(target)


def test_receipt_read_refuses_symlink_and_missing(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    _write_receipt(real, _real_receipt())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(rs.PolicyRefused):
        rs.read_h1_receipt(link)
    with pytest.raises(rs.PolicyRefused):
        rs.read_h1_receipt(tmp_path / "absent.json")
    with pytest.raises(rs.PolicyRefused):
        rs.read_h1_receipt(tmp_path)


# --------------------------------------------------------------------------
# Classifier same-byte seam (acceptance test 6)
# --------------------------------------------------------------------------


def test_load_binds_receipt_classifier_mapping_and_refusal(
    binding: rs.H1Binding,
) -> None:
    assert binding.receipt_sha256 == rs.H1_RECEIPT_SHA256
    assert binding.classifier_sha256 == binding.receipt["classifier_sha256"]
    assert binding.mapping_sha256 == binding.receipt["mapping_sha256"]
    assert (
        binding.refusal_contract_sha256
        == binding.receipt["refusal_contract_sha256"]
    )


def test_load_refuses_a_wrong_expected_receipt_digest() -> None:
    receipt_path, classifier_path = rs.default_h1_paths()
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path,
            classifier_path=classifier_path,
            expected_receipt_sha256="0" * 64,
        )


def test_expected_receipt_default_is_the_pinned_constant() -> None:
    import inspect

    default = inspect.signature(rs.load_h1_binding).parameters[
        "expected_receipt_sha256"
    ].default
    assert default == rs.H1_RECEIPT_SHA256


def test_load_refuses_a_mutated_classifier(tmp_path: Path) -> None:
    receipt_path, classifier_path = rs.default_h1_paths()
    mutated = tmp_path / "register_classifier.py"
    mutated.write_bytes(classifier_path.read_bytes() + b"\n# drift\n")
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path, classifier_path=mutated
        )


def test_load_refuses_an_oversize_classifier(tmp_path: Path) -> None:
    receipt_path, _ = rs.default_h1_paths()
    big = tmp_path / "register_classifier.py"
    big.write_bytes(b"# " + b"x" * rs.MAX_CLASSIFIER_SOURCE_BYTES + b"\n")
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(receipt_path=receipt_path, classifier_path=big)


def _synthetic_pair(
    tmp_path: Path, source: bytes
) -> tuple[Path, Path, str]:
    """Write a synthetic classifier plus a receipt coherent with its digests."""
    classifier = tmp_path / "register_classifier.py"
    classifier.write_bytes(source)
    namespace = rs._execute_classifier(source, classifier)
    receipt = dict(_real_receipt())
    receipt["classifier_sha256"] = rs.raw_sha256(source)
    try:
        receipt["mapping_sha256"] = rs.mapping_binding(namespace)[1]
    except rs.PolicyRefused:
        receipt["mapping_sha256"] = "0" * 64
    try:
        receipt["refusal_contract_sha256"] = rs.refusal_contract_binding(
            namespace
        )[1]
    except rs.PolicyRefused:
        receipt["refusal_contract_sha256"] = "0" * 64
    receipt_path = tmp_path / "receipt.json"
    digest = _write_receipt(receipt_path, receipt)
    return receipt_path, classifier, digest


def _faithful_source() -> bytes:
    """A minimal classifier exporting the real public identities."""
    receipt_path, classifier_path = rs.default_h1_paths()
    namespace = rs._execute_classifier(
        classifier_path.read_bytes(), classifier_path
    )
    return (
        "REGISTER_TAXONOMY = {taxonomy!r}\n"
        "REGISTER_FAMILIES = {families!r}\n"
        "KNOWN_REGISTERS = REGISTER_FAMILIES\n"
        "REGISTER_REFUSAL_REASONS = {reasons!r}\n"
        "CANONICAL_REGISTER_TO_FAMILY = {canonical!r}\n"
        "LEGACY_REGISTER_TO_FAMILY = {legacy!r}\n"
        "def resolve_family(value):\n"
        "    if value is None:\n"
        "        return 'unknown'\n"
        "    return CANONICAL_REGISTER_TO_FAMILY.get(\n"
        "        value, LEGACY_REGISTER_TO_FAMILY.get(value, 'unknown'))\n"
        "def classify_register(text, *, hint=None, min_words=100):\n"
        "    return RESULT\n"
        "RESULT = None\n"
    ).format(
        taxonomy=namespace["REGISTER_TAXONOMY"],
        families=namespace["REGISTER_FAMILIES"],
        reasons=namespace["REGISTER_REFUSAL_REASONS"],
        canonical=namespace["CANONICAL_REGISTER_TO_FAMILY"],
        legacy=namespace["LEGACY_REGISTER_TO_FAMILY"],
    ).encode("utf-8")


def test_synthetic_faithful_classifier_binds(tmp_path: Path) -> None:
    receipt_path, classifier, digest = _synthetic_pair(
        tmp_path, _faithful_source()
    )
    bound = rs.load_h1_binding(
        receipt_path=receipt_path,
        classifier_path=classifier,
        expected_receipt_sha256=digest,
    )
    assert bound.mapping_sha256 == MAPPING_DIGEST
    assert bound.refusal_contract_sha256 == REFUSAL_DIGEST


def test_mapping_drift_refuses_even_when_the_receipt_agrees(
    tmp_path: Path,
) -> None:
    # The receipt is regenerated to match the drifted mapping, so only the
    # pinned-digest comparison against the real H1 identity can catch this.
    drifted = _faithful_source().replace(
        b"'personal': 'first_person_essay'", b"'personal': 'academic'"
    )
    assert drifted != _faithful_source()
    receipt_path, classifier, digest = _synthetic_pair(tmp_path, drifted)
    bound = rs.load_h1_binding(
        receipt_path=receipt_path,
        classifier_path=classifier,
        expected_receipt_sha256=digest,
    )
    assert bound.mapping_sha256 != MAPPING_DIGEST


@pytest.mark.parametrize(
    "mutation",
    [
        "REGISTER_TAXONOMY = 'register_families/v1'",
        "KNOWN_REGISTERS = ()",
        "REGISTER_FAMILIES = ['a', 'b']",
        "REGISTER_REFUSAL_REASONS = ('short_text', 'all_weak')",
        "REGISTER_REFUSAL_REASONS = ('all_weak', 'short_text', 'exact_top_tie')",
        "CANONICAL_REGISTER_TO_FAMILY = {'personal': 'not_a_family'}",
        "LEGACY_REGISTER_TO_FAMILY = {1: 'academic'}",
        "del resolve_family",
        "del classify_register",
        "def classify_register(text, hint=None, min_words=100): return RESULT",
        "def classify_register(text, *, hint=None, min_words=50): return RESULT",
        "def classify_register(text, *, min_words=100): return RESULT",
        "def resolve_family(v): return 'unknown'",
        "def resolve_family(value='x'): return 'unknown'",
        "resolve_family = 3",
    ],
)
def test_hostile_classifier_namespaces_refuse(
    tmp_path: Path, mutation: str
) -> None:
    source = _faithful_source() + mutation.encode("utf-8") + b"\n"
    receipt_path, classifier, digest = _synthetic_pair(tmp_path, source)
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path,
            classifier_path=classifier,
            expected_receipt_sha256=digest,
        )


def test_classifier_source_that_fails_to_execute_refuses(
    tmp_path: Path,
) -> None:
    receipt_path, classifier, digest = _synthetic_pair(
        tmp_path, _faithful_source()
    )
    classifier.write_bytes(b"raise RuntimeError('boom')\n")
    receipt = dict(_real_receipt())
    receipt["classifier_sha256"] = rs.raw_sha256(classifier.read_bytes())
    digest = _write_receipt(receipt_path, receipt)
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path,
            classifier_path=classifier,
            expected_receipt_sha256=digest,
        )


# --------------------------------------------------------------------------
# Closed public-result validation (acceptance test 6, hostile results)
# --------------------------------------------------------------------------


def _good_result(binding: rs.H1Binding) -> dict[str, object]:
    families = binding.classified_domain
    return {
        "primary": "academic",
        "confidence": 0.5,
        "secondary": ["journalism"],
        "scores": {name: 0.25 for name in families},
        "evidence": {
            "n_words": 150,
            "n_chars": 900,
            "n_sentences": 10,
            "n_paragraphs": 3,
            "mean_paragraph_words": 50.0,
            "heading_density_per_1k": 0.0,
            "first_person_per_1k": 1.0,
            "second_person_per_1k": 0.0,
            "dialogue_ratio": 0.0,
            "question_per_1k": 0.0,
            "exclamation_per_1k": 0.0,
            "inline_citation_per_1k": 2.0,
            "statutory_per_1k": 0.0,
            "formal_address_per_1k": 0.0,
            "shall_pursuant_per_1k": 0.0,
            "attributed_quote_per_1k": 0.0,
            "imperative_open_per_1k": 0.0,
            "past_tense_narrative_per_1k": 0.0,
            "academic_voice_per_1k": 4.0,
        },
        "warning": None,
        "taxonomy": "register_families/v2",
        "refusal_reason": None,
    }


def _refusal_result(binding: rs.H1Binding) -> dict[str, object]:
    return {
        "primary": "unknown",
        "confidence": 0.0,
        "secondary": [],
        "scores": {},
        # The two-key evidence shape is the zero-word shape: real H1 emits the
        # full nineteen keys as soon as n_words >= 1.
        "evidence": {"n_words": 0, "n_chars": 20},
        "warning": "short input",
        "taxonomy": "register_families/v2",
        "refusal_reason": "short_text",
    }


def test_good_and_refusal_results_validate(binding: rs.H1Binding) -> None:
    binding.validate_classification(_good_result(binding), min_words=100)
    binding.validate_classification(_refusal_result(binding), min_words=100)


def test_real_classifier_results_validate(binding: rs.H1Binding) -> None:
    for text, expect_refusal in (
        ("word " * 400, False),
        ("short", True),
        ("", True),
        ("   ", True),
        ("...", True),
    ):
        result = binding.classify(text, min_words=100)
        assert (result["refusal_reason"] is not None) is expect_refusal
        assert (result["primary"] == "unknown") is expect_refusal


def test_full_evidence_shape_has_nineteen_keys() -> None:
    assert len(rs.FULL_EVIDENCE_KEYS) == 19
    assert len(set(rs.FULL_EVIDENCE_KEYS)) == 19
    assert set(rs.ZERO_WORD_EVIDENCE_KEYS) < set(rs.FULL_EVIDENCE_KEYS)


def test_every_missing_top_level_result_key_refuses(
    binding: rs.H1Binding,
) -> None:
    good = _good_result(binding)
    for key in list(good):
        broken = {k: v for k, v in good.items() if k != key}
        with pytest.raises(rs.PolicyRefused):
            binding.validate_classification(broken, min_words=100)


def test_an_extra_top_level_result_key_refuses(binding: rs.H1Binding) -> None:
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**_good_result(binding), "dominant": "academic"}, min_words=100
        )


def test_every_missing_evidence_key_refuses(binding: rs.H1Binding) -> None:
    good = _good_result(binding)
    for key in list(good["evidence"]):  # type: ignore[arg-type]
        evidence = {
            k: v
            for k, v in good["evidence"].items()  # type: ignore[union-attr]
            if k != key
        }
        with pytest.raises(rs.PolicyRefused):
            binding.validate_classification(
                {**good, "evidence": evidence}, min_words=100
            )


def test_an_extra_evidence_key_refuses(binding: rs.H1Binding) -> None:
    good = _good_result(binding)
    evidence = {**good["evidence"], "novel_metric": 1.0}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**good, "evidence": evidence}, min_words=100
        )


@pytest.mark.parametrize(
    "key,value",
    [
        ("primary", "unknown"),
        ("primary", "not_a_family"),
        ("primary", 1),
        ("primary", None),
        ("confidence", 1.5),
        ("confidence", -0.1),
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("confidence", 1),
        ("confidence", True),
        ("confidence", "0.5"),
        ("secondary", ["academic"]),
        ("secondary", ["journalism", "journalism"]),
        ("secondary", ["unknown"]),
        ("secondary", ["not_a_family"]),
        ("secondary", ("journalism",)),
        ("secondary", [1]),
        ("taxonomy", "register_families/v1"),
        ("taxonomy", None),
        ("warning", 1),
        ("warning", "x" * 4097),
        ("refusal_reason", "short_text"),
        ("refusal_reason", "not_a_reason"),
        ("refusal_reason", 1),
    ],
)
def test_hostile_scored_result_fields_refuse(
    binding: rs.H1Binding, key: str, value: object
) -> None:
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**_good_result(binding), key: value}, min_words=100
        )


def test_secondary_may_hold_up_to_eight_distinct_families(
    binding: rs.H1Binding,
) -> None:
    others = [f for f in binding.classified_domain if f != "academic"]
    good = _good_result(binding)
    binding.validate_classification(
        {**good, "secondary": others[:7]}, min_words=100
    )
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**good, "secondary": list(binding.classified_domain) + ["academic"]},
            min_words=100,
        )


def test_warning_prose_cannot_change_the_refusal_category(
    binding: rs.H1Binding,
) -> None:
    good = _good_result(binding)
    for prose in ("short_text", "all_weak", "unknown", "verdict: mixed", "x" * 4096):
        validated = binding.validate_classification(
            {**good, "warning": prose}, min_words=100
        )
        assert validated["refusal_reason"] is None
        assert validated["primary"] == "academic"


@pytest.mark.parametrize(
    "key,value",
    [
        ("n_words", True),
        ("n_words", -1),
        ("n_words", 1.0),
        ("n_words", 2**63),
        ("n_words", "150"),
        ("n_chars", 0),
        ("n_sentences", 0),
        ("n_paragraphs", 0),
        ("dialogue_ratio", 1.5),
        ("dialogue_ratio", -0.1),
        ("mean_paragraph_words", -1.0),
        ("mean_paragraph_words", float("inf")),
        ("mean_paragraph_words", 50),
        ("academic_voice_per_1k", True),
        ("academic_voice_per_1k", "4.0"),
    ],
)
def test_hostile_evidence_values_refuse(
    binding: rs.H1Binding, key: str, value: object
) -> None:
    good = _good_result(binding)
    evidence = {**good["evidence"], key: value}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**good, "evidence": evidence}, min_words=100
        )


def test_zero_word_evidence_allows_nonzero_chars(binding: rs.H1Binding) -> None:
    # Whitespace- or punctuation-only input reports zero words with a nonzero
    # character count; that is the real H1 behavior and must validate.
    refusal = _refusal_result(binding)
    binding.validate_classification(
        {**refusal, "evidence": {"n_words": 0, "n_chars": 3}}, min_words=100
    )
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**refusal, "evidence": {"n_words": 1, "n_chars": 3}}, min_words=100
        )


def test_score_domain_must_match_word_count_relative_to_min_words(
    binding: rs.H1Binding,
) -> None:
    good = _good_result(binding)
    # Full score domain requires n_words >= min_words.
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(good, min_words=1_000)
    # Empty scores require n_words < min_words. A short_text refusal can carry
    # the full evidence shape (real H1 does so from n_words == 1), so this is
    # reachable rather than vacuous.
    refusal = _refusal_result(binding)
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**refusal, "evidence": good["evidence"]}, min_words=100
        )


@pytest.mark.parametrize(
    "scores",
    [
        {},
        {"academic": 0.5},
        {"not_a_family": 0.5},
        {"unknown": 0.5},
    ],
)
def test_hostile_score_domains_refuse(
    binding: rs.H1Binding, scores: object
) -> None:
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**_good_result(binding), "scores": scores}, min_words=100
        )


@pytest.mark.parametrize("value", [1.5, -0.1, float("nan"), 1, True, "0.5"])
def test_hostile_score_values_refuse(
    binding: rs.H1Binding, value: object
) -> None:
    good = _good_result(binding)
    scores = {**good["scores"], "academic": value}  # type: ignore[dict-item]
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**good, "scores": scores}, min_words=100
        )


def test_refusal_biconditional_is_enforced_in_both_directions(
    binding: rs.H1Binding,
) -> None:
    good = _good_result(binding)
    refusal = _refusal_result(binding)
    # unknown primary without a reason
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**refusal, "refusal_reason": None}, min_words=100
        )
    # scored primary with a reason
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**good, "refusal_reason": "all_weak"}, min_words=100
        )


def test_each_refusal_reason_validates(binding: rs.H1Binding) -> None:
    refusal = _refusal_result(binding)
    for reason in binding.refusal_domain:
        if reason == "short_text":
            binding.validate_classification(
                {**refusal, "refusal_reason": reason}, min_words=100
            )
            continue
        # all_weak and exact_top_tie carry the full score domain.
        scored_refusal = {
            **refusal,
            "refusal_reason": reason,
            "scores": {name: 0.1 for name in binding.classified_domain},
            "evidence": _good_result(binding)["evidence"],
        }
        binding.validate_classification(scored_refusal, min_words=100)


def test_result_must_be_a_direct_dict(binding: rs.H1Binding) -> None:
    class Sneaky(dict):
        pass

    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            Sneaky(_good_result(binding)), min_words=100
        )
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(None, min_words=100)


def test_resolve_family_rejects_an_out_of_domain_return(
    binding: rs.H1Binding,
) -> None:
    assert binding.resolve_family(None) == "unknown"
    assert binding.resolve_family("personal") in binding.classified_domain
    original = binding.namespace["resolve_family"]
    binding.namespace["resolve_family"] = lambda value: "not_a_family"
    try:
        with pytest.raises(rs.PolicyRefused):
            binding.resolve_family("personal")
    finally:
        binding.namespace["resolve_family"] = original


def test_classify_wraps_a_raising_h1_callable(binding: rs.H1Binding) -> None:
    original = binding.namespace["classify_register"]

    def boom(text, *, hint=None, min_words=100):
        raise RuntimeError("secret path /private/corpus")

    binding.namespace["classify_register"] = boom
    try:
        with pytest.raises(rs.PolicyRefused) as caught:
            binding.classify("text", min_words=100)
        assert "secret path" not in str(caught.value)
    finally:
        binding.namespace["classify_register"] = original


def test_classify_passes_no_hint(binding: rs.H1Binding) -> None:
    seen: dict[str, object] = {}
    original = binding.namespace["classify_register"]
    good = _good_result(binding)

    def record(text, *, hint=None, min_words=100):
        seen["hint"] = hint
        seen["min_words"] = min_words
        return good

    binding.namespace["classify_register"] = record
    try:
        binding.classify("text", min_words=150)
    finally:
        binding.namespace["classify_register"] = original
    assert seen == {"hint": None, "min_words": 150}


# --------------------------------------------------------------------------
# Claim posture of this increment
# --------------------------------------------------------------------------


def test_module_exposes_no_verdict_or_score_surface() -> None:
    forbidden = {
        "verdict",
        "dominant",
        "homogeneity",
        "unimodality",
        "accuracy",
        "mixture_flag",
        "semantic_mode",
        "source_group",
        "source_family",
        "source_id",
    }
    names = {name.lower() for name in dir(rs)}
    for name in names:
        parts = set(name.split("_"))
        assert not (parts & {"verdict", "dominant", "accuracy"}), name
    assert not (names & forbidden)


def test_error_categories_are_the_three_fixed_envelopes() -> None:
    assert (rs.BadInput.exit_code, rs.BadInput.reason_category) == (
        2,
        "bad_input",
    )
    assert rs.BadInput.reason == "register composition sweep refused invalid input"
    assert (rs.PolicyRefused.exit_code, rs.PolicyRefused.reason_category) == (
        3,
        "policy_refused",
    )
    assert rs.PolicyRefused.reason == "register composition sweep refused by policy"
    assert (rs.InternalError.exit_code, rs.InternalError.reason_category) == (
        4,
        "internal_error",
    )
    assert rs.InternalError.reason == (
        "register composition sweep unavailable after internal failure"
    )
    for cls in (rs.BadInput, rs.PolicyRefused, rs.InternalError):
        assert issubclass(cls, rs.SweepRefusal)


def test_no_source_field_appears_in_any_frozen_payload_builder() -> None:
    source = Path(rs.__file__).read_text(encoding="utf-8")
    for forbidden in ('"source"', '"source_id"', '"source_family"'):
        assert forbidden not in source


def test_module_imports_no_network_or_subprocess_surface() -> None:
    source = Path(rs.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import socket",
        "import ssl",
        "import urllib",
        "import http",
        "import subprocess",
        "import requests",
    ):
        assert forbidden not in source


# ---- Increment B: manifest projection seam ----
#
# Covers the H2 manifest-projection seam:
# ``manifest_validator.project_register_sweep_manifest_bytes``, its shared
# strict byte/row parser, the closed source-blind per-row projector
# (``_project_h2_row``), the frozen document plan (``shingle_dedup_io.
# bind_regular``), and the cross-row collision helper
# (``check_document_plan_collisions``). Acceptance tests 4 and 5. All
# fixtures are synthetic bytes written to ``tmp_path``; no private corpus,
# aggregate, identifier, path, or prose enters the repository.


def _h2_row_dict(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": "doc.txt",
        "ai_status": "pre_ai_human",
        "use": ["baseline"],
        "register": "personal",
        "split": "baseline",
        "persona": "josh",
    }
    row.update(overrides)
    return row


def _row_canonical(row: "mv.H2ProjectedRow") -> dict[str, Any]:
    return {
        "ai_status": row.ai_status,
        "manifest_ordinal": row.manifest_ordinal,
        "path": row.path,
        "persona": row.persona,
        "register": row.register,
        "split": row.split,
        "use": list(row.use),
    }


def _projected_row_digests(projection: "mv.RegisterSweepManifestProjection") -> list[str]:
    return [rs.projected_row_binding(_row_canonical(row))[1] for row in projection.rows]


def _projected_manifest_digest(projection: "mv.RegisterSweepManifestProjection") -> str:
    digests = _projected_row_digests(projection)
    return rs.projected_manifest_binding([rs.prefixed(d) for d in digests])[1]


def _scoped_rows_digest(projection: "mv.RegisterSweepManifestProjection") -> str:
    # Increment B performs no CLI scope filtering; treating "every row" as
    # the scope here (identity selection) still exercises the shared builder
    # and proves it is a pure function of the projected rows.
    digests = _projected_row_digests(projection)
    entries = [
        {
            "manifest_ordinal": row.manifest_ordinal,
            "projected_row_sha256": rs.prefixed(digest),
            "scoped_ordinal": i,
        }
        for i, (row, digest) in enumerate(zip(projection.rows, digests))
    ]
    return rs.scoped_rows_binding(entries)[1]


def _document_plan_digest(projection: "mv.RegisterSweepManifestProjection") -> str:
    digests = _projected_row_digests(projection)
    entries = []
    for i, doc in enumerate(projection.document_plan):
        _, target_digest = rs.target_path_binding(doc.absolute_path)
        _, fingerprint_digest = rs.posix_fingerprint_binding(doc.fingerprint)
        entries.append({
            "candidate_index": doc.candidate_index,
            "file_fingerprint_sha256": rs.prefixed(fingerprint_digest),
            "projected_row_sha256": rs.prefixed(digests[i]),
            "scoped_ordinal": i,
            "target_path_sha256": rs.prefixed(target_digest),
        })
    return rs.document_plan_binding(entries)[1]


# -------------------- Canonical shape (acceptance test 10 tie-in) --------


def test_canonical_projected_row_payload_matches_spec_literal() -> None:
    row = mv.H2ProjectedRow(
        manifest_ordinal=0, path="docs/a.txt", register="personal",
        use=("baseline",), split="baseline", persona=None, ai_status="pre_ai_human",
    )
    payload, _digest = rs.projected_row_binding(_row_canonical(row))
    assert payload == (
        b'{"ai_status":"pre_ai_human","manifest_ordinal":0,"path":"docs/a.txt",'
        b'"persona":null,"register":"personal","split":"baseline","use":["baseline"]}'
    )


# -------------------- Source metamorphic invariant (acceptance test 4) ---


class _HostileRow:
    """A row mapping that raises on every access path to the three unowned
    source fields, and on every non-``__getitem__`` access path entirely.

    Deliberately does not derive from ``dict`` or ``collections.abc.Mapping``:
    only ``__getitem__`` is implemented functionally, so a real projector
    that ever fell back to ``.get()``, iteration, or containment testing
    would raise ``AttributeError``/``AssertionError`` instead of silently
    working.
    """

    _FORBIDDEN = ("source", "source_id", "source_family")
    _DATA = {
        "path": "doc.txt",
        "ai_status": "pre_ai_human",
        "use": ["baseline", "voice_profile"],
        "register": "personal",
        "split": "baseline",
        "persona": "josh",
    }

    def __getitem__(self, key: str) -> Any:
        if key in self._FORBIDDEN:
            raise AssertionError(f"forbidden __getitem__({key!r})")
        return self._DATA[key]

    def __iter__(self) -> Any:
        raise AssertionError("forbidden __iter__")

    def keys(self) -> Any:
        raise AssertionError("forbidden keys()")

    def items(self) -> Any:
        raise AssertionError("forbidden items()")

    def values(self) -> Any:
        raise AssertionError("forbidden values()")

    def __contains__(self, key: str) -> Any:
        raise AssertionError("forbidden __contains__")


def test_instrumented_mapping_projects_via_direct_lookup_only() -> None:
    row = mv._project_h2_row(_HostileRow(), manifest_ordinal=7)
    assert row == mv.H2ProjectedRow(
        manifest_ordinal=7,
        path="doc.txt",
        register="personal",
        use=("baseline", "voice_profile"),
        split="baseline",
        persona="josh",
        ai_status="pre_ai_human",
    )


def test_row_projection_source_never_names_unowned_fields() -> None:
    source = inspect.getsource(mv._project_h2_row) + inspect.getsource(mv._row_item)
    for forbidden in ("source_family", "source_id", '"source"', "'source'"):
        assert forbidden not in source


_SOURCE_FIELD_VARIANTS: list[dict[str, Any]] = [
    {},
    {"source": ""},
    {"source_id": "abc-123"},
    {"source_family": "unclassified"},
    {"source": "caf" + "é"},                    # NFC composed
    {"source": "café"},                         # NFD decomposed, same grapheme
    {"source": "‮reversed‬"},                # bidi-looking valid Unicode
    {"source": "a" * 20000},                           # far past any owned-field bound
    {"source": 12345},
    {"source_id": None},
    {"source_family": ["a", "b"]},
    {"source": {"nested": True}},
    {"source_id": True},
    {"source": "x", "source_id": "y", "source_family": "unclassified"},
]


def test_source_metamorphic_invariant_across_manifest_variants(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("hello world", encoding="utf-8")
    manifest_path = tmp_path / "corpus_manifest.jsonl"

    def _project(source_fields: dict[str, Any]) -> "mv.RegisterSweepManifestProjection":
        row = _h2_row_dict(path="doc.txt", **source_fields)
        manifest_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return mv.project_register_sweep_manifest_bytes(
            manifest_path.read_bytes(), manifest_path=manifest_path,
        )

    baseline = _project({})
    baseline_digests = (
        _projected_manifest_digest(baseline),
        _scoped_rows_digest(baseline),
        _document_plan_digest(baseline),
    )

    for variant in _SOURCE_FIELD_VARIANTS:
        result = _project(variant)
        assert result.rows == baseline.rows
        assert result.input_rows == baseline.input_rows
        assert result.document_plan == baseline.document_plan
        assert (
            _projected_manifest_digest(result),
            _scoped_rows_digest(result),
            _document_plan_digest(result),
        ) == baseline_digests


# -------------------- Shared strict byte/row parser (acceptance test 5) --


def test_bom_refuses_before_any_row_projects(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    data = b"\xef\xbb\xbf" + json.dumps(_h2_row_dict()).encode("utf-8") + b"\n"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_non_utf8_bytes_refuse(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    data = json.dumps(_h2_row_dict()).encode("utf-8") + b"\n\xff\xfe"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_top_level_duplicate_key_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","path":"other.txt","ai_status":"pre_ai_human",'
        b'"use":["baseline"]}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_nested_duplicate_key_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","ai_status":"pre_ai_human","use":["baseline"],'
        b'"notes":{"a":1,"a":2}}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_non_finite_constant_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","ai_status":"pre_ai_human","use":["baseline"],'
        b'"word_count":NaN}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_non_object_data_row_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(b"[1,2,3]\n", manifest_path=manifest_path)


def test_malformed_json_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(b"{bad-json\n", manifest_path=manifest_path)


def test_wrong_type_data_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes("not bytes", manifest_path=manifest_path)  # type: ignore[arg-type]


# -------------------- input_rows counting -------------------------------


def test_input_rows_empty_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    result = mv.project_register_sweep_manifest_bytes(b"", manifest_path=manifest_path)
    assert result.input_rows == 0
    assert result.rows == ()
    assert result.document_plan == ()


def test_input_rows_excludes_blank_and_comment_lines(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    lines = [
        "# a comment",
        "",
        json.dumps(_h2_row_dict(path="doc.txt")),
        "   ",
        "# another comment",
    ]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    result = mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)
    assert result.input_rows == 1


def test_input_rows_full_manifest_counts_every_row(tmp_path: Path) -> None:
    doc_a = tmp_path / "a.txt"
    doc_a.write_text("x", encoding="utf-8")
    doc_b = tmp_path / "b.txt"
    doc_b.write_text("y", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    rows = [_h2_row_dict(path="a.txt"), _h2_row_dict(path="b.txt")]
    data = ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")
    result = mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)
    assert result.input_rows == 2
    assert [row.manifest_ordinal for row in result.rows] == [0, 1]
    assert [doc.manifest_ordinal for doc in result.document_plan] == [0, 1]


# -------------------- Row-level owned-field admissibility ----------------


@pytest.mark.parametrize("missing_field", ["path", "use", "ai_status"])
def test_missing_required_owned_field_refuses(missing_field: str) -> None:
    row = _h2_row_dict()
    del row[missing_field]
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


@pytest.mark.parametrize("field,bad_value", [
    ("path", 123),
    ("path", None),
    ("path", ["doc.txt"]),
    ("use", "baseline"),
    ("use", {"baseline": True}),
    ("use", [1, 2]),
    ("use", [None]),
    ("ai_status", 5),
    ("ai_status", None),
    ("ai_status", ["pre_ai_human"]),
    ("register", 5),
    ("register", []),
    ("split", 5),
    ("split", True),
    ("persona", 5),
    ("persona", []),
])
def test_owned_field_wrong_type_refuses(field: str, bad_value: Any) -> None:
    row = _h2_row_dict()
    row[field] = bad_value
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


def test_use_duplicate_member_refuses() -> None:
    # Otherwise-perfectly-valid row: only the duplicate makes this refuse.
    row = _h2_row_dict(use=["baseline", "baseline"])
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


def test_use_empty_list_refuses() -> None:
    row = _h2_row_dict(use=[])
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


def test_use_oversized_list_refuses() -> None:
    row = _h2_row_dict(use=["baseline"] * 33)
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


def test_use_preserves_input_order_not_sorted() -> None:
    ordered = ["voice_profile", "baseline", "idiolect"]
    row = _h2_row_dict(use=ordered)
    projected = mv._project_h2_row(row, manifest_ordinal=0)
    assert projected.use == tuple(ordered)


def test_optional_fields_absent_and_explicit_null_both_project_none() -> None:
    absent = {"path": "doc.txt", "ai_status": "pre_ai_human", "use": ["baseline"]}
    explicit_null = dict(absent, register=None, split=None, persona=None)
    for row in (absent, explicit_null):
        projected = mv._project_h2_row(row, manifest_ordinal=0)
        assert projected.register is None
        assert projected.split is None
        assert projected.persona is None


def test_every_allowed_register_split_ai_status_use_value_passes() -> None:
    for value in sorted(mv.ALLOWED_REGISTER):
        row = _h2_row_dict(register=value)
        assert mv._project_h2_row(row, manifest_ordinal=0).register == value
    for value in sorted(mv.ALLOWED_SPLIT):
        row = _h2_row_dict(split=value)
        assert mv._project_h2_row(row, manifest_ordinal=0).split == value
    for value in sorted(mv.ALLOWED_AI_STATUS):
        row = _h2_row_dict(ai_status=value)
        assert mv._project_h2_row(row, manifest_ordinal=0).ai_status == value
    for value in sorted(mv.ALLOWED_USE):
        row = _h2_row_dict(use=[value])
        assert mv._project_h2_row(row, manifest_ordinal=0).use == (value,)


# path/persona share one string domain (Spec 73 lines ~685-686, ~542-543).
_STRING_DOMAIN_VIOLATIONS = {
    "leading_whitespace": " doc.txt",
    "trailing_whitespace": "doc.txt ",
    "nul": "doc\x00.txt",
    "c0_control": "doc\x01.txt",
    "del_control": "doc\x7f.txt",
    "c1_control": "doc.txt",
    "bidi_control": "doc‮txt",
    "unpaired_surrogate": "doc\ud800.txt",
    "nfd_decomposed": "café.txt",
    "blank": "   ",
    "empty": "",
}


@pytest.mark.parametrize(
    "value", _STRING_DOMAIN_VIOLATIONS.values(), ids=_STRING_DOMAIN_VIOLATIONS.keys(),
)
def test_path_domain_violation_refuses(value: str) -> None:
    row = _h2_row_dict(path=value)
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


@pytest.mark.parametrize(
    "value", _STRING_DOMAIN_VIOLATIONS.values(), ids=_STRING_DOMAIN_VIOLATIONS.keys(),
)
def test_persona_domain_violation_refuses(value: str) -> None:
    row = _h2_row_dict(persona=value)
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)


def test_nfc_composed_path_and_persona_pass() -> None:
    composed = "caf" + "é" + ".txt"
    row = _h2_row_dict(path=composed, persona="j" + "é" + "sh")
    projected = mv._project_h2_row(row, manifest_ordinal=0)
    assert projected.path == composed
    assert projected.persona == "jésh"


def test_path_length_boundary_via_row_projection() -> None:
    ok_path = "a" * rs.MAX_PATH_BYTES
    projected = mv._project_h2_row(_h2_row_dict(path=ok_path), manifest_ordinal=0)
    assert projected.path == ok_path

    too_long = "a" * (rs.MAX_PATH_BYTES + 1)
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(_h2_row_dict(path=too_long), manifest_ordinal=0)


def test_persona_length_boundary_via_row_projection() -> None:
    ok_persona = "p" * rs.MAX_PERSONA_BYTES
    projected = mv._project_h2_row(_h2_row_dict(persona=ok_persona), manifest_ordinal=0)
    assert projected.persona == ok_persona

    too_long = "p" * (rs.MAX_PERSONA_BYTES + 1)
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(_h2_row_dict(persona=too_long), manifest_ordinal=0)


def test_general_validator_warns_but_h2_refuses_unknown_enum_values(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    base = {"id": "row-1", "path": "doc.txt", "ai_status": "pre_ai_human", "use": ["baseline"]}

    for field, bad_value in (
        ("register", "not_a_real_register"),
        ("split", "not_a_real_split"),
        ("ai_status", "not_a_real_status"),
    ):
        entry = dict(base)
        entry[field] = bad_value

        issues = mv.validate_entry(entry, 1, manifest_path, set(), {})
        field_issues = [i for i in issues if i.field == field]
        assert field_issues and all(i.severity == "warning" for i in field_issues)

        manifest_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        with pytest.raises(rs.BadInput):
            mv.project_register_sweep_manifest_bytes(
                manifest_path.read_bytes(), manifest_path=manifest_path,
            )

    use_entry = dict(base, use=["not_a_real_use"])
    use_issues = mv.validate_entry(use_entry, 1, manifest_path, set(), {})
    assert any(i.field == "use" and i.severity == "warning" for i in use_issues)
    manifest_path.write_text(json.dumps(use_entry) + "\n", encoding="utf-8")
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(
            manifest_path.read_bytes(), manifest_path=manifest_path,
        )


# -------------------- Mutation-style "load-bearing" checks ---------------


def test_register_enum_membership_check_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _h2_row_dict(register="not_a_real_register")
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)

    monkeypatch.setattr(mv, "ALLOWED_REGISTER", mv.ALLOWED_REGISTER | {"not_a_real_register"})
    assert mv._project_h2_row(row, manifest_ordinal=0).register == "not_a_real_register"


def test_use_enum_membership_check_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _h2_row_dict(use=["not_a_real_use"])
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)

    monkeypatch.setattr(mv, "ALLOWED_USE", mv.ALLOWED_USE | {"not_a_real_use"})
    assert mv._project_h2_row(row, manifest_ordinal=0).use == ("not_a_real_use",)


def test_ai_status_enum_membership_check_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _h2_row_dict(ai_status="not_a_real_status")
    with pytest.raises(rs.BadInput):
        mv._project_h2_row(row, manifest_ordinal=0)

    monkeypatch.setattr(mv, "ALLOWED_AI_STATUS", mv.ALLOWED_AI_STATUS | {"not_a_real_status"})
    assert mv._project_h2_row(row, manifest_ordinal=0).ai_status == "not_a_real_status"


# -------------------- Frozen document-plan candidate order ---------------


def test_absolute_path_row_uses_candidate_index_zero(tmp_path: Path) -> None:
    doc = tmp_path / "abs.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    manifest_path.write_text(json.dumps(_h2_row_dict(path=str(doc))) + "\n", encoding="utf-8")

    result = mv.project_register_sweep_manifest_bytes(
        manifest_path.read_bytes(), manifest_path=manifest_path,
    )
    assert result.document_plan[0].candidate_index == 0
    assert result.document_plan[0].absolute_path == Path(os.path.abspath(doc))


def test_relative_path_prefers_manifest_parent_first(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    manifest_path = corpus_dir / "m.jsonl"
    doc = corpus_dir / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path.write_text(json.dumps(_h2_row_dict(path="doc.txt")) + "\n", encoding="utf-8")

    result = mv.project_register_sweep_manifest_bytes(
        manifest_path.read_bytes(), manifest_path=manifest_path,
    )
    assert result.document_plan[0].candidate_index == 0
    assert result.document_plan[0].absolute_path == doc


def test_relative_path_falls_through_to_manifest_grandparent(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "outer" / "inner"
    corpus_dir.mkdir(parents=True)
    manifest_path = corpus_dir / "m.jsonl"
    doc = tmp_path / "outer" / "doc.txt"  # manifest.parent.parent / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path.write_text(json.dumps(_h2_row_dict(path="doc.txt")) + "\n", encoding="utf-8")

    result = mv.project_register_sweep_manifest_bytes(
        manifest_path.read_bytes(), manifest_path=manifest_path,
    )
    assert result.document_plan[0].candidate_index == 1
    assert result.document_plan[0].absolute_path == doc


def test_relative_path_falls_through_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_dir = tmp_path / "elsewhere"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "m.jsonl"
    cwd_dir = tmp_path / "cwdroot"
    cwd_dir.mkdir()
    doc = cwd_dir / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    monkeypatch.chdir(cwd_dir)
    manifest_path.write_text(json.dumps(_h2_row_dict(path="doc.txt")) + "\n", encoding="utf-8")

    result = mv.project_register_sweep_manifest_bytes(
        manifest_path.read_bytes(), manifest_path=manifest_path,
    )
    assert result.document_plan[0].candidate_index == 2
    assert result.document_plan[0].absolute_path == doc


def test_unsafe_first_candidate_refuses_without_falling_through(tmp_path: Path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    manifest_path = corpus_dir / "m.jsonl"
    real_target = tmp_path / "real_target.txt"
    real_target.write_text("real", encoding="utf-8")
    (corpus_dir / "doc.txt").symlink_to(real_target)  # candidate index 0: unsafe
    (tmp_path / "doc.txt").write_text("fallback", encoding="utf-8")  # candidate index 1: safe
    manifest_path.write_text(json.dumps(_h2_row_dict(path="doc.txt")) + "\n", encoding="utf-8")

    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(
            manifest_path.read_bytes(), manifest_path=manifest_path,
        )


def test_no_candidate_present_refuses(tmp_path: Path) -> None:
    manifest_path = tmp_path / "m.jsonl"
    manifest_path.write_text(
        json.dumps(_h2_row_dict(path="does-not-exist.txt")) + "\n", encoding="utf-8",
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(
            manifest_path.read_bytes(), manifest_path=manifest_path,
        )


# -------------------- shingle_dedup_io.bind_regular -----------------------


def test_bind_regular_rejects_bad_candidate_shapes(tmp_path: Path) -> None:
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular([])
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular("not-a-list-or-tuple")  # type: ignore[arg-type]
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular(
            [tmp_path / "a", tmp_path / "b", tmp_path / "c", tmp_path / "d"]
        )


def test_bind_regular_skips_absent_candidates_and_uses_first_present(tmp_path: Path) -> None:
    doc = tmp_path / "second.txt"
    doc.write_text("hi", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    absolute, index, fingerprint = shingle_dedup_io.bind_regular([missing, doc])

    assert index == 1
    assert absolute == doc
    assert len(fingerprint) == 5
    info = os.stat(doc)
    assert fingerprint == (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def test_bind_regular_refuses_present_symlink_without_falling_through(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hi", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    fallback = tmp_path / "fallback.txt"
    fallback.write_text("fallback", encoding="utf-8")

    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular([link, fallback])


def test_bind_regular_refuses_present_non_regular_without_falling_through(tmp_path: Path) -> None:
    directory_candidate = tmp_path / "adir"
    directory_candidate.mkdir()
    fallback = tmp_path / "fallback.txt"
    fallback.write_text("fallback", encoding="utf-8")

    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular([directory_candidate, fallback])


def test_bind_regular_refuses_when_no_candidate_present(tmp_path: Path) -> None:
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular([tmp_path / "a", tmp_path / "b", tmp_path / "c"])


# -------------------- Collision helper ------------------------------------


def test_collision_key_nfc_normalizes_composed_vs_decomposed() -> None:
    nfc_name = "caf" + "é" + ".txt"   # single composed code point
    nfd_name = "café.txt"             # e + combining acute accent
    assert nfc_name != nfd_name
    assert mv._collision_key(Path("/x") / nfc_name) == mv._collision_key(Path("/x") / nfd_name)


def _fingerprint_of(path: Path) -> tuple[int, int, int, int, int]:
    info = os.stat(path)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def test_collision_helper_refuses_repeated_absolute_path(tmp_path: Path) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    fingerprint = _fingerprint_of(doc)
    entries = [
        mv.H2PlannedDocument(0, 0, doc, fingerprint),
        mv.H2PlannedDocument(1, 0, doc, fingerprint),
    ]
    with pytest.raises(rs.BadInput):
        mv.check_document_plan_collisions(entries)


def test_collision_helper_refuses_hardlink_alias(tmp_path: Path) -> None:
    doc_a = tmp_path / "a.txt"
    doc_a.write_text("shared", encoding="utf-8")
    doc_b = tmp_path / "b.txt"
    os.link(doc_a, doc_b)
    fingerprint_a = _fingerprint_of(doc_a)
    fingerprint_b = _fingerprint_of(doc_b)
    assert fingerprint_a == fingerprint_b  # same inode: every fingerprint field matches

    entries = [
        mv.H2PlannedDocument(0, 0, doc_a, fingerprint_a),
        mv.H2PlannedDocument(1, 0, doc_b, fingerprint_b),
    ]
    with pytest.raises(rs.BadInput):
        mv.check_document_plan_collisions(entries)


def test_collision_helper_passes_for_distinct_files(tmp_path: Path) -> None:
    doc_a = tmp_path / "a.txt"
    doc_a.write_text("x", encoding="utf-8")
    doc_b = tmp_path / "b.txt"
    doc_b.write_text("y", encoding="utf-8")
    entries = [
        mv.H2PlannedDocument(0, 0, doc_a, _fingerprint_of(doc_a)),
        mv.H2PlannedDocument(1, 0, doc_b, _fingerprint_of(doc_b)),
    ]
    mv.check_document_plan_collisions(entries)  # must not raise


def test_repeated_row_document_plan_collision_detected_downstream(tmp_path: Path) -> None:
    # Increment B's seam is scope-agnostic and does not dedupe by design (the
    # spec scopes collision checking to the runner's *scoped* subset); this
    # proves the wiring boundary: the seam still plans both rows, and the
    # separate helper is what a later-increment runner must call before it
    # reads a first document body.
    doc = tmp_path / "doc.txt"
    doc.write_text("x", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    row = _h2_row_dict(path="doc.txt")
    manifest_path.write_text(
        "\n".join([json.dumps(row), json.dumps(row)]) + "\n", encoding="utf-8",
    )

    result = mv.project_register_sweep_manifest_bytes(
        manifest_path.read_bytes(), manifest_path=manifest_path,
    )
    assert result.input_rows == 2
    assert len(result.document_plan) == 2

    with pytest.raises(rs.BadInput):
        mv.check_document_plan_collisions(result.document_plan)


def test_collision_helper_rejects_malformed_entry_types() -> None:
    with pytest.raises(rs.InternalError):
        mv.check_document_plan_collisions("not-a-list-or-tuple")  # type: ignore[arg-type]
    with pytest.raises(rs.InternalError):
        mv.check_document_plan_collisions([{"not": "a H2PlannedDocument"}])  # type: ignore[list-item]
