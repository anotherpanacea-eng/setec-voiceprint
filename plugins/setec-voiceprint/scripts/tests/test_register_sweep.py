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
import json
import os
import struct
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import register_sweep as rs  # type: ignore


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


# ---- Increment C: immutable shards, owner-private policy, topology ----
#
# Acceptance tests 7, 11, 12, 13, and 14 for the checkpoint/privacy/topology
# layer.  All fixtures are generated synthetic data; no private corpus,
# aggregate, identifier, path, or prose enters the repository.

import sqlite3
import stat
import types

import shingle_dedup_checkpoint as checkpoint  # type: ignore
import shingle_dedup_io as secure_io  # type: ignore

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="POSIX predicates")


@pytest.fixture(scope="module")
def domains(binding: rs.H1Binding) -> rs.RegisterDomains:
    return rs.RegisterDomains.from_binding(binding).validate()


BINDING_DIGEST = rs.prefixed("a" * 64)


def _row(
    ordinal: int,
    *,
    declared: str,
    classified: str | None = None,
    refusal: str | None = None,
    words: int = 5,
    document_bytes: int = 20,
) -> dict[str, object]:
    return {
        "manifest_ordinal": ordinal,
        "projected_row_sha256": rs.prefixed(f"{ordinal:064x}"),
        "content_sha256": rs.prefixed(f"{ordinal + 1:064x}"),
        "document_bytes": document_bytes,
        "words": words,
        "declared_family": declared,
        "classified_family": classified,
        "refusal_reason": refusal,
    }


def _synthetic_rows(
    count: int, domains: rs.RegisterDomains, *, offset: int = 0
) -> list[dict[str, object]]:
    """Deterministic valid-domain rows covering same/different/unresolved."""
    families = domains.classified
    rows: list[dict[str, object]] = []
    for index in range(offset, offset + count):
        declared = domains.declared[index % len(domains.declared)]
        if index % 4 == 0:
            rows.append(
                _row(index, declared=declared, refusal=domains.refusals[index % len(domains.refusals)])
            )
        elif index % 4 == 1:
            rows.append(_row(index, declared=declared, classified=families[index % len(families)]))
        elif index % 4 == 2:
            rows.append(_row(index, declared=families[index % len(families)],
                             classified=families[index % len(families)]))
        else:
            rows.append(_row(index, declared="unknown",
                             classified=families[(index + 1) % len(families)]))
    return rows


def _fresh_checkpoint(
    path: Path, domains: rs.RegisterDomains
) -> rs.RegisterCheckpoint:
    return rs.RegisterCheckpoint.create(
        path, domains=domains, checkpoint_binding_sha256=BINDING_DIGEST
    )


def _resume_checkpoint(
    path: Path, domains: rs.RegisterDomains
) -> rs.RegisterCheckpoint:
    return rs.RegisterCheckpoint.resume(
        path, domains=domains, checkpoint_binding_sha256=BINDING_DIGEST
    )


def _publish_plan(
    path: Path, domains: rs.RegisterDomains, total: int
) -> rs.RegisterCheckpoint:
    """Publish a whole plan under the frozen shard partition."""
    sizes = rs.shard_partition(total)
    handle = _fresh_checkpoint(path, domains)
    published = 0
    for index, size in enumerate(sizes):
        handle.publish_shard(
            _synthetic_rows(size, domains, offset=published),
            final=index + 1 == len(sizes),
        )
        published += size
    return handle


def _rewrite_shard(raw: bytes, statements: list[tuple[str, tuple[object, ...]]]) -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        for sql, parameters in statements:
            connection.execute(sql, parameters)
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def _replace_shard(path: Path, name: str, raw: bytes) -> None:
    target = path / name
    target.unlink()
    target.write_bytes(raw)
    os.chmod(target, 0o600)


def _reserved_temp(index: int) -> str:
    return ".tmp-" + f"{index:032x}"


def _write_temp(directory: Path, name: str, payload: bytes = b"debris") -> Path:
    target = directory / name
    target.write_bytes(payload)
    os.chmod(target, 0o600)
    return target


# --------------------------------------------------------------------------
# Fixed domains and the frozen shard partition (acceptance test 11)
# --------------------------------------------------------------------------


def test_register_domains_pin_the_fixed_D_F_R_split(
    binding: rs.H1Binding, domains: rs.RegisterDomains
) -> None:
    assert domains.classified == tuple(
        name for name in binding.namespace["REGISTER_FAMILIES"] if name != "unknown"
    )
    assert domains.declared == domains.classified + ("unknown",)
    assert domains.refusals == rs.H1_REFUSAL_REASONS
    assert "unknown" not in domains.classified
    assert rs.MATCH_DOMAIN == ("same", "different", "unresolved")


@pytest.mark.parametrize(
    "total,expected",
    [
        (0, ()),
        (1, (1,)),
        (249, (249,)),
        (250, (250,)),
        (251, (250, 1)),
        (500, (250, 250)),
        (501, (250, 250, 1)),
    ],
)
def test_shard_partition_is_the_one_immutable_partition(
    total: int, expected: tuple[int, ...]
) -> None:
    assert rs.shard_partition(total) == expected
    assert sum(rs.shard_partition(total)) == total


@pytest.mark.parametrize("total,expected", [
    (1, (1,)), (249, (249,)), (250, (250,)), (251, (250, 1)),
    (500, (250, 250)), (501, (250, 250, 1)),
])
@POSIX_ONLY
def test_published_shard_sizes_equality_pin_the_partition(
    tmp_path: Path, domains: rs.RegisterDomains, total: int, expected: tuple[int, ...]
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, total) as handle:
        assert tuple(len(shard.rows) for shard in handle.shards) == expected
        assert handle.next_scoped_ordinal == total
        assert handle.next_shard_number == len(expected)
    names = sorted(item.name for item in state.iterdir())
    assert names == [rs.shard_name(index) for index in range(len(expected))]


@POSIX_ONLY
def test_short_non_final_shard_is_never_publishable(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    with _fresh_checkpoint(tmp_path / "cp", domains) as handle:
        with pytest.raises(rs.InternalError):
            handle.publish_shard(_synthetic_rows(249, domains), final=False)
        with pytest.raises(rs.InternalError):
            handle.publish_shard(_synthetic_rows(251, domains), final=True)
        with pytest.raises(rs.InternalError):
            handle.publish_shard((), final=True)
    assert not list((tmp_path / "cp").iterdir())


@POSIX_ONLY
def test_publication_after_a_short_final_shard_refuses(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    with _fresh_checkpoint(tmp_path / "cp", domains) as handle:
        handle.publish_shard(_synthetic_rows(3, domains), final=True)
        with pytest.raises(rs.PolicyRefused):
            handle.publish_shard(_synthetic_rows(1, domains, offset=3), final=True)


# --------------------------------------------------------------------------
# Immutable resume (acceptance test 11)
# --------------------------------------------------------------------------


@POSIX_ONLY
def test_interruption_before_publication_loses_only_the_unpublished_shard(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    handle = _fresh_checkpoint(state, domains)
    handle.publish_shard(_synthetic_rows(250, domains), final=False)
    # The next 250 rows are processed but the process dies before publication.
    handle.close()

    resumed = _resume_checkpoint(state, domains)
    assert resumed.next_scoped_ordinal == 250
    assert resumed.next_shard_number == 1
    resumed.publish_shard(_synthetic_rows(250, domains, offset=250), final=False)
    resumed.publish_shard(_synthetic_rows(1, domains, offset=500), final=True)
    resumed.close()

    fresh = _publish_plan(tmp_path / "fresh", domains, 501)
    replayed = _resume_checkpoint(state, domains)
    try:
        assert rs.canonical_json(replayed.sealed_delta) == rs.canonical_json(
            fresh.sealed_delta
        )
        assert replayed.next_scoped_ordinal == fresh.next_scoped_ordinal
        assert [shard.shard_sha256 for shard in replayed.shards] == [
            shard.shard_sha256 for shard in fresh.shards
        ]
        assert [shard.row_digests for shard in replayed.shards] == [
            shard.row_digests for shard in fresh.shards
        ]
    finally:
        replayed.close()
        fresh.close()
    assert (state / rs.shard_name(0)).read_bytes() == (
        tmp_path / "fresh" / rs.shard_name(0)
    ).read_bytes()


@POSIX_ONLY
def test_interruption_after_publication_resumes_at_the_next_ordinal(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    handle = _fresh_checkpoint(state, domains)
    first = handle.publish_shard(_synthetic_rows(250, domains), final=False)
    handle.close()
    with _resume_checkpoint(state, domains) as resumed:
        assert resumed.next_scoped_ordinal == first.next_scoped_ordinal == 250
        assert resumed.prior_shard_sha256 == first.shard_sha256


@POSIX_ONLY
def test_empty_and_reserved_temp_only_directories_resume_at_zero_progress(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    for temps in (0, 1, 15, 16):
        state = tmp_path / f"cp{temps}"
        _fresh_checkpoint(state, domains).close()
        for index in range(temps):
            _write_temp(state, _reserved_temp(index))
        with _resume_checkpoint(state, domains) as handle:
            assert handle.next_scoped_ordinal == 0
            assert handle.next_shard_number == 0
            assert handle.prior_shard_sha256 is None
            assert handle.shards == ()
            assert rs.canonical_json(handle.sealed_delta) == rs.canonical_json(
                rs.zero_aggregate_delta(domains)
            )
            if temps == rs.MAX_RESERVED_TEMPORARY_NAMES:
                # A directory already holding the whole reserved allowance
                # refuses publication rather than reusing one of those names.
                with pytest.raises(rs.PolicyRefused):
                    handle.publish_shard(_synthetic_rows(1, domains), final=True)
                assert not any(
                    rs.SHARD_NAME_RE.fullmatch(item.name) for item in state.iterdir()
                )
                continue
            # The first published final must be shard 0 bound to the current
            # binding, regardless of how much crash debris is present.
            published = handle.publish_shard(_synthetic_rows(1, domains), final=True)
        assert published.name == "register-00000000.sqlite"
        assert published.prior_shard_sha256 is None
        assert published.checkpoint_binding_sha256 == BINDING_DIGEST
        assert published.first_scoped_ordinal == 0


@POSIX_ONLY
def test_seventeen_reserved_temps_refuse_rather_than_reusing_one(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    _fresh_checkpoint(state, domains).close()
    for index in range(17):
        _write_temp(state, _reserved_temp(index))
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    (state / _reserved_temp(16)).unlink()
    with _resume_checkpoint(state, domains) as handle:
        assert handle.next_scoped_ordinal == 0
    # A published final plus the full reserved allowance still resumes, and the
    # seventeenth reserved name refuses again.
    (state / _reserved_temp(15)).unlink()
    with _resume_checkpoint(state, domains) as handle:
        handle.publish_shard(_synthetic_rows(1, domains), final=True)
    with _resume_checkpoint(state, domains) as handle:
        assert handle.next_scoped_ordinal == 1
    _write_temp(state, _reserved_temp(15))
    _write_temp(state, _reserved_temp(16))
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_publication_refuses_when_the_reserved_temp_allowance_is_full(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    handle = _fresh_checkpoint(state, domains)
    for index in range(16):
        _write_temp(state, _reserved_temp(index))
    handle._directory.freeze_listing()
    with pytest.raises(rs.PolicyRefused):
        handle.publish_shard(_synthetic_rows(1, domains), final=True)
    handle.close()
    assert not any(rs.SHARD_NAME_RE.fullmatch(item.name) for item in state.iterdir())


@POSIX_ONLY
def test_valid_looking_sqlite_inside_a_reserved_temp_is_inert(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    donor = tmp_path / "donor"
    with _publish_plan(donor, domains, 3) as source:
        payload = (donor / source.shards[0].name).read_bytes()
    state = tmp_path / "cp"
    _fresh_checkpoint(state, domains).close()
    _write_temp(state, _reserved_temp(0), payload)
    with _resume_checkpoint(state, domains) as handle:
        assert handle.next_scoped_ordinal == 0
        assert handle.shards == ()
        assert rs.canonical_json(handle.sealed_delta) == rs.canonical_json(
            rs.zero_aggregate_delta(domains)
        )


@pytest.mark.parametrize("name", [
    "register-0000000.sqlite",
    "register-000000000.sqlite",
    "register-00000000.sqlite-journal",
    "register-00000000.sqlite-wal",
    "register-00000000.sqlite-shm",
    "register-0000000A.sqlite",
    ".tmp-" + "1" * 31,
    ".tmp-" + "1" * 33,
    ".tmp-" + "G" * 32,
    ".tmp-" + "1" * 32 + "-log",
    "notes.txt",
    ".tmp-",
])
@POSIX_ONLY
def test_non_reserved_names_refuse_resume(
    tmp_path: Path, domains: rs.RegisterDomains, name: str
) -> None:
    state = tmp_path / "cp"
    _fresh_checkpoint(state, domains).close()
    _write_temp(state, name)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_oversized_and_indirect_reserved_temps_refuse(
    tmp_path: Path, domains: rs.RegisterDomains, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "cp"
    _fresh_checkpoint(state, domains).close()
    oversized = _write_temp(state, _reserved_temp(0), b"x" * 64)
    monkeypatch.setattr(rs, "MAX_SHARD_BYTES", 32)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    monkeypatch.undo()
    oversized.unlink()

    (state / _reserved_temp(1)).symlink_to(tmp_path / "absent")
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    (state / _reserved_temp(1)).unlink()

    (state / _reserved_temp(2)).mkdir()
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    (state / _reserved_temp(2)).rmdir()

    original = _write_temp(state, _reserved_temp(3))
    os.link(original, state / _reserved_temp(4))
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_hole_extra_and_ordinal_drift_in_the_shard_chain_refuse(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 501) as handle:
        names = [shard.name for shard in handle.shards]
    assert names == [rs.shard_name(index) for index in range(3)]

    hole = (state / names[1]).read_bytes()
    (state / names[1]).unlink()
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    (state / names[1]).write_bytes(hole)
    os.chmod(state / names[1], 0o600)
    _resume_checkpoint(state, domains).close()

    # An extra final beyond the contiguous chain refuses.
    (state / rs.shard_name(4)).write_bytes(hole)
    os.chmod(state / rs.shard_name(4), 0o600)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_chain_and_binding_drift_refuse(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 251) as handle:
        pass
    with pytest.raises(rs.PolicyRefused):
        rs.RegisterCheckpoint.resume(
            state, domains=domains, checkpoint_binding_sha256=rs.prefixed("b" * 64)
        )
    # Swapping the two shards' bytes breaks both the ordinal and the chain.
    first = (state / rs.shard_name(0)).read_bytes()
    second = (state / rs.shard_name(1)).read_bytes()
    _replace_shard(state, rs.shard_name(0), second)
    _replace_shard(state, rs.shard_name(1), first)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_non_final_shard_shorter_than_250_rows_refuses_resume(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _fresh_checkpoint(state, domains) as handle:
        short = handle.publish_shard(_synthetic_rows(3, domains), final=True)
    # Forge a second shard behind the short one: the chain now claims a
    # non-final shard with fewer than 250 rows.
    raw, _forged = rs.encode_register_shard(
        shard_number=1,
        first_scoped_ordinal=short.next_scoped_ordinal,
        checkpoint_binding_sha256=BINDING_DIGEST,
        prior_shard_sha256=short.shard_sha256,
        rows=_synthetic_rows(2, domains, offset=short.next_scoped_ordinal),
        domains=domains,
    )
    (state / rs.shard_name(1)).write_bytes(raw)
    os.chmod(state / rs.shard_name(1), 0o600)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@pytest.mark.parametrize("ceiling,boundary,refused", [
    ("MAX_FINAL_SHARDS", 3, 2),
    ("MAX_CHECKPOINT_CUMULATIVE_BYTES", None, None),
    ("MAX_RESERVED_TEMPORARY_NAMES", 1, 0),
])
@POSIX_ONLY
def test_checkpoint_ceilings_accept_the_control_and_refuse_a_lowered_bound(
    tmp_path: Path, domains: rs.RegisterDomains, monkeypatch: pytest.MonkeyPatch,
    ceiling: str, boundary: int | None, refused: int | None,
) -> None:
    state = tmp_path / ceiling
    with _publish_plan(state, domains, 501) as handle:
        published = sum((state / shard.name).stat().st_size for shard in handle.shards)
    if ceiling == "MAX_CHECKPOINT_CUMULATIVE_BYTES":
        boundary, refused = published, published - 1
    if ceiling == "MAX_RESERVED_TEMPORARY_NAMES":
        _write_temp(state, _reserved_temp(0))
    monkeypatch.setattr(rs, ceiling, boundary)
    _resume_checkpoint(state, domains).close()
    monkeypatch.setattr(rs, ceiling, refused)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


@POSIX_ONLY
def test_final_shard_ceiling_refuses_publication_rather_than_overflowing(
    tmp_path: Path, domains: rs.RegisterDomains, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "cp"
    handle = _fresh_checkpoint(state, domains)
    handle.publish_shard(_synthetic_rows(250, domains), final=False)
    monkeypatch.setattr(rs, "MAX_FINAL_SHARDS", 1)
    with pytest.raises(rs.PolicyRefused):
        handle.publish_shard(_synthetic_rows(250, domains, offset=250), final=False)
    handle.close()
    assert sorted(item.name for item in state.iterdir()) == [rs.shard_name(0)]


# --------------------------------------------------------------------------
# Codec exactness: every metadata/schema/PRAGMA/digest/delta mutation refuses
# --------------------------------------------------------------------------


@pytest.fixture()
def sealed_shard(domains: rs.RegisterDomains) -> tuple[bytes, rs.RegisterShard]:
    return rs.encode_register_shard(
        shard_number=0,
        first_scoped_ordinal=0,
        checkpoint_binding_sha256=BINDING_DIGEST,
        prior_shard_sha256=None,
        rows=_synthetic_rows(4, domains),
        domains=domains,
    )


def test_sealed_shard_pins_the_frozen_codec_shape(
    sealed_shard: tuple[bytes, rs.RegisterShard], domains: rs.RegisterDomains
) -> None:
    raw, shard = sealed_shard
    assert raw.startswith(b"SQLite format 3\x00")
    assert len(raw) % rs.SHARD_PAGE_SIZE == 0
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        assert connection.execute("PRAGMA application_id").fetchone() == (0x52535731,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA encoding").fetchone() == ("UTF-8",)
        assert connection.execute("PRAGMA page_size").fetchone() == (4096,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("memory",)
        objects = {
            (row[0], row[1]): row[2]
            for row in connection.execute("SELECT type,name,sql FROM sqlite_master")
        }
        assert objects == {
            ("table", "checkpoint_meta"): (
                "CREATE TABLE checkpoint_meta(key TEXT PRIMARY KEY,"
                "value TEXT NOT NULL) WITHOUT ROWID"
            ),
            ("table", "rows"): (
                "CREATE TABLE rows(scoped_ordinal INTEGER PRIMARY KEY,"
                "row_json BLOB NOT NULL,row_sha256 BLOB NOT NULL)"
            ),
            ("table", "aggregate_delta"): (
                "CREATE TABLE aggregate_delta(key TEXT PRIMARY KEY,"
                "value_json BLOB NOT NULL) WITHOUT ROWID"
            ),
        }
        meta = dict(connection.execute("SELECT key,value FROM checkpoint_meta"))
        assert tuple(sorted(meta)) == rs.SHARD_META_KEYS
        assert meta["schema_version"] == '"setec-register-sweep-checkpoint/2"'
        assert meta["kind"] == '"register"'
        assert meta["shard_number"] == "0"
        assert meta["first_scoped_ordinal"] == "0"
        assert meta["next_scoped_ordinal"] == "4"
        assert meta["prior_shard_sha256"] == "null"
        assert not any(value.endswith("\n") for value in meta.values())
        for key, value in meta.items():
            assert json.dumps(
                json.loads(value), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ) == value, key
        rows = list(
            connection.execute(
                "SELECT scoped_ordinal,row_json,row_sha256 FROM rows ORDER BY scoped_ordinal"
            )
        )
        assert [row[0] for row in rows] == [0, 1, 2, 3]
        for _ordinal, row_json, row_digest in rows:
            assert type(row_json) is bytes and type(row_digest) is bytes
            assert len(row_digest) == 32
            assert hashlib.sha256(row_json).digest() == row_digest
            assert not row_json.endswith(b"\n")
        delta = dict(
            connection.execute("SELECT key,value_json FROM aggregate_delta")
        )
        assert tuple(sorted(delta)) == tuple(sorted(rs.DELTA_KEYS))
        assert all(type(value) is bytes for value in delta.values())
    finally:
        connection.close()
    assert shard.delta["counts"]["scoped_documents"] == 4
    assert shard.next_scoped_ordinal == 4
    assert shard.prior_shard_sha256 is None


def _meta_update(key: str, value: str) -> list[tuple[str, tuple[object, ...]]]:
    return [("UPDATE checkpoint_meta SET value=? WHERE key=?", (value, key))]


CODEC_MUTATIONS: dict[str, list[tuple[str, tuple[object, ...]]]] = {
    "application_id": [("PRAGMA application_id=1", ())],
    "user_version": [("PRAGMA user_version=2", ())],
    "extra_table": [("CREATE TABLE extra(a INTEGER)", ())],
    "schema_version": _meta_update("schema_version", '"setec-register-sweep-checkpoint/1"'),
    "kind": _meta_update("kind", '"shingle"'),
    "shard_number": _meta_update("shard_number", "1"),
    "first_ordinal": _meta_update("first_scoped_ordinal", "1"),
    "next_ordinal": _meta_update("next_scoped_ordinal", "5"),
    "prior_hash_on_shard_zero": _meta_update(
        "prior_shard_sha256", '"sha256:' + "b" * 64 + '"'
    ),
    "uppercase_shard_hash": _meta_update("shard_sha256", '"sha256:' + "B" * 64 + '"'),
    "unprefixed_binding": _meta_update("checkpoint_binding_sha256", '"' + "a" * 64 + '"'),
    "noncanonical_meta_text": _meta_update("shard_number", " 0"),
    "meta_terminal_lf": _meta_update("shard_number", "0\n"),
    "extra_meta_key": [
        ("INSERT INTO checkpoint_meta VALUES('extra','0')", ())
    ],
    "missing_meta_key": [("DELETE FROM checkpoint_meta WHERE key='kind'", ())],
    "row_hole": [("DELETE FROM rows WHERE scoped_ordinal=2", ())],
    "row_ordinal_shift": [
        ("UPDATE rows SET scoped_ordinal=9 WHERE scoped_ordinal=3", ())
    ],
    "hex_row_digest": [
        (
            "UPDATE rows SET row_sha256=(SELECT hex(row_sha256) FROM rows"
            " WHERE scoped_ordinal=0) WHERE scoped_ordinal=0",
            (),
        )
    ],
    "delta_counts": [
        (
            "UPDATE aggregate_delta SET value_json=? WHERE key='counts'",
            (b'{"scoped_documents":99}',),
        )
    ],
    "delta_key": [
        ("UPDATE aggregate_delta SET key='counts_extra' WHERE key='counts'", ())
    ],
    "shard_hash": _meta_update("shard_sha256", '"sha256:' + "c" * 64 + '"'),
}


@pytest.mark.parametrize("mutation", sorted(CODEC_MUTATIONS))
def test_every_codec_mutation_refuses(
    sealed_shard: tuple[bytes, rs.RegisterShard],
    domains: rs.RegisterDomains,
    mutation: str,
) -> None:
    raw, _shard = sealed_shard
    corrupt = _rewrite_shard(raw, CODEC_MUTATIONS[mutation])
    with pytest.raises(rs.PolicyRefused):
        rs.decode_register_shard(
            corrupt,
            name="register-00000000.sqlite",
            domains=domains,
            checkpoint_binding_sha256=BINDING_DIGEST,
        )


def test_row_rehashed_after_content_change_still_refuses_via_the_delta(
    sealed_shard: tuple[bytes, rs.RegisterShard], domains: rs.RegisterDomains
) -> None:
    raw, _shard = sealed_shard
    replacement = _row(0, declared=domains.classified[0], classified=domains.classified[0], words=999)
    row_json, row_digest = rs.checkpoint_row_binding(replacement)
    corrupt = _rewrite_shard(
        raw,
        [(
            "UPDATE rows SET row_json=?,row_sha256=? WHERE scoped_ordinal=0",
            (row_json, row_digest),
        )],
    )
    with pytest.raises(rs.PolicyRefused):
        rs.decode_register_shard(
            corrupt,
            name="register-00000000.sqlite",
            domains=domains,
            checkpoint_binding_sha256=BINDING_DIGEST,
        )


def test_shard_name_must_match_the_recorded_ordinal(
    sealed_shard: tuple[bytes, rs.RegisterShard], domains: rs.RegisterDomains
) -> None:
    raw, _shard = sealed_shard
    for name in ("register-00000001.sqlite", "register-0000000.sqlite", "other.sqlite"):
        with pytest.raises(rs.PolicyRefused):
            rs.decode_register_shard(
                raw, name=name, domains=domains,
                checkpoint_binding_sha256=BINDING_DIGEST,
            )


@POSIX_ONLY
def test_a_mutated_published_shard_refuses_resume(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 2) as handle:
        name = handle.shards[0].name
    raw = (state / name).read_bytes()
    _replace_shard(state, name, _rewrite_shard(raw, CODEC_MUTATIONS["shard_hash"]))
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


def test_aggregate_delta_equations_and_marginals_agree(
    domains: rs.RegisterDomains
) -> None:
    rows = _synthetic_rows(64, domains)
    delta = rs.compute_aggregate_delta(rows, domains)
    counts = delta["counts"]
    assert tuple(sorted(delta)) == tuple(sorted(rs.DELTA_KEYS))
    assert set(delta["declared_family_inventory"]) == set(domains.declared)
    assert set(delta["classified_family_inventory"]) == set(domains.classified)
    assert set(delta["refusal_inventory"]) == set(domains.refusals)
    assert set(delta["match_inventory"]) == set(rs.MATCH_DOMAIN)
    for measure in ("documents", "words"):
        total = sum(
            cell[measure] for cell in delta["declared_family_inventory"].values()
        )
        crosstab = sum(
            inner[measure]
            for outer in delta["declared_by_classified_family"].values()
            for inner in outer.values()
        )
        classified = sum(
            cell[measure] for cell in delta["classified_family_inventory"].values()
        )
        refused = sum(cell[measure] for cell in delta["refusal_inventory"].values())
        matched = sum(cell[measure] for cell in delta["match_inventory"].values())
        assert classified == crosstab == counts[f"classified_{measure}"]
        assert refused == counts[f"refused_{measure}"]
        assert total == counts[f"scoped_{measure}"] == classified + refused == matched
        assert delta["match_inventory"]["same"][measure] == sum(
            delta["declared_by_classified_family"][family][family][measure]
            for family in domains.classified
        )
        assert delta["match_inventory"]["different"][measure] == sum(
            delta["declared_by_classified_family"][declared][classified][measure]
            for declared in domains.classified
            for classified in domains.classified
            if declared != classified
        )
        assert delta["match_inventory"]["unresolved"][measure] == (
            sum(
                delta["declared_by_classified_family"]["unknown"][family][measure]
                for family in domains.classified
            )
            + refused
        )
        assert counts[f"resolved_declared_{measure}"] == (
            counts[f"scoped_{measure}"]
            - delta["declared_family_inventory"]["unknown"][measure]
        )
        assert counts[f"unresolved_declared_{measure}"] == (
            delta["declared_family_inventory"]["unknown"][measure]
        )


def test_empty_scope_delta_is_the_fully_zero_filled_fixed_domain(
    domains: rs.RegisterDomains
) -> None:
    zero = rs.zero_aggregate_delta(domains)
    assert zero["counts"] == {key: 0 for key in rs.DELTA_COUNT_KEYS}
    assert all(
        cell == {"documents": 0, "words": 0}
        for cell in zero["declared_family_inventory"].values()
    )
    assert rs.canonical_json(
        rs.add_aggregate_deltas(zero, zero)
    ) == rs.canonical_json(zero)


def test_delta_rejects_out_of_domain_rows(domains: rs.RegisterDomains) -> None:
    for bad in (
        _row(0, declared="not_a_family", classified=domains.classified[0]),
        _row(0, declared=domains.classified[0], classified="unknown"),
        _row(0, declared=domains.classified[0], classified=domains.classified[0],
             refusal=domains.refusals[0]),
        _row(0, declared=domains.classified[0]),
    ):
        with pytest.raises(rs.InternalError):
            rs.compute_aggregate_delta([bad], domains)
    with pytest.raises(rs.InternalError):
        rs.compute_aggregate_delta(_synthetic_rows(251, domains), domains)


# --------------------------------------------------------------------------
# Owner-private platform contract (acceptance test 12)
# --------------------------------------------------------------------------


def _offset_read(source: bytes):
    """A fake backend read that advances per handle, like the real one."""
    offsets: dict[int, int] = {}

    def read(handle: int, maximum: int) -> bytes:
        start = offsets.get(handle, 0)
        chunk = source[start:start + maximum]
        offsets[handle] = start + len(chunk)
        return chunk

    return read


def test_policies_are_the_two_named_contracts() -> None:
    assert checkpoint.LEGACY_SHINGLE_POLICY == "legacy_shingle_v1"
    assert checkpoint.OWNER_PRIVATE_POLICY == "owner_private_v1"
    assert checkpoint.ImmutableShardDirectory.policy == "legacy_shingle_v1"
    assert checkpoint.CheckpointDirectory.policy == "legacy_shingle_v1"
    assert issubclass(
        checkpoint.CheckpointDirectory, checkpoint.ImmutableShardDirectory
    )
    assert rs.RegisterShardDirectory.policy == "owner_private_v1"
    assert rs.PRIVACY_POLICY == "owner_private_v1"
    assert secure_io.PRIVACY_POLICIES == ("legacy_shingle_v1", "owner_private_v1")
    assert rs.RegisterShardDirectory._limits() == (416, 400, 16, 4194304, 1677721600)


@POSIX_ONLY
def test_owner_private_create_publish_and_resume_predicates(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 2) as handle:
        name = handle.shards[0].name
    directory_info = os.stat(state)
    assert stat.S_ISDIR(directory_info.st_mode)
    assert stat.S_IMODE(directory_info.st_mode) == 0o700
    assert directory_info.st_uid == os.geteuid()
    shard_info = os.stat(state / name)
    assert stat.S_ISREG(shard_info.st_mode)
    assert stat.S_IMODE(shard_info.st_mode) == 0o600
    assert shard_info.st_uid == os.geteuid()
    assert shard_info.st_nlink == 1
    _resume_checkpoint(state, domains).close()


@POSIX_ONLY
def test_hostile_umask_cannot_widen_the_directory_or_the_shard(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    previous = os.umask(0o777)
    try:
        with _fresh_checkpoint(state, domains) as handle:
            shard = handle.publish_shard(_synthetic_rows(1, domains), final=True)
        report = tmp_path / "report.json"
        secure_io.publish_create_new(
            report, b"{}\n", privacy_policy="owner_private_v1"
        )
    finally:
        os.umask(previous)
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(state / shard.name).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    assert os.stat(report).st_nlink == 1


@POSIX_ONLY
def test_resume_never_chmods_or_repairs_a_widened_directory(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    _publish_plan(state, domains, 1).close()
    os.chmod(state, 0o750)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o750
    os.chmod(state, 0o700)
    _resume_checkpoint(state, domains).close()


@POSIX_ONLY
def test_resume_never_chmods_or_repairs_a_widened_shard(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 1) as handle:
        name = handle.shards[0].name
    os.chmod(state / name, 0o644)
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    assert stat.S_IMODE(os.stat(state / name).st_mode) == 0o644
    os.chmod(state / name, 0o600)
    _resume_checkpoint(state, domains).close()


@POSIX_ONLY
def test_multiply_linked_or_indirect_shard_refuses(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "cp"
    with _publish_plan(state, domains, 1) as handle:
        name = handle.shards[0].name
    os.link(state / name, tmp_path / "alias.sqlite")
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)
    (tmp_path / "alias.sqlite").unlink()
    _resume_checkpoint(state, domains).close()


@POSIX_ONLY
def test_parent_permissions_are_never_proof_and_never_changed(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    parent = tmp_path / "open-parent"
    parent.mkdir(mode=0o755)
    state = parent / "cp"
    _publish_plan(state, domains, 1).close()
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o755
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    _resume_checkpoint(state, domains).close()
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o755


@POSIX_ONLY
def test_legacy_policy_never_chmods_and_keeps_byte_identical_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identical ``legacy_shingle_v1`` regression against pinned goldens."""
    inventory_row = ("doc", "draft", "stage", 1, hashlib.sha256(b"doc").digest())
    meta = {
        "schema_version": "setec-shingle-checkpoint/1", "tool": "shingle_dedup",
        "method_version": "1", "checkpoint_kind": "build_inventory",
        "chunk_number": "0", "source_manifest_sha256": "a" * 64,
        "canonical_descriptors_sha256": "-", "index_sha256": "-",
        "logical_index_sha256": "-", "config_sha256": "c" * 64,
        "first_item": '{"doc_id":"doc"}', "next_item": "null", "item_count": "1",
        "potential_pairs": "0", "unassessed_pairs": "0", "assessed_pairs": "0",
        "no_overlap_pairs": "0", "below_0_35_pairs": "0",
        "containment_0_35_to_0_60_pairs": "0",
        "containment_at_least_0_60_pairs": "0", "reported_pairs": "0",
    }
    expected_raw, sealed = checkpoint._encode_checkpoint(
        "inventory", meta, inventory_rows=[inventory_row], document_rows=(),
        posting_rows=(), pairs=(),
    )
    # The pre-factoring logical seal, pinned by the existing shingle suite.
    assert sealed["checkpoint_sha256"] == (
        "bd0c1cffa3177fa5db0051791a14c0538fdd3d5e261e472ba30f9da8e5e8b4cd"
    )

    def forbidden_fchmod(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy_shingle_v1 must never chmod")

    monkeypatch.setattr(checkpoint.os, "fchmod", forbidden_fchmod)
    monkeypatch.setattr(secure_io.os, "fchmod", forbidden_fchmod)
    state = tmp_path / "legacy"
    with checkpoint.CheckpointDirectory.open_new(state) as directory:
        snapshot = directory.publish(
            kind="inventory", meta=meta, inventory_rows=[inventory_row]
        )
    assert snapshot.raw == expected_raw
    assert (state / "inventory-00000000.sqlite").read_bytes() == expected_raw
    with checkpoint.CheckpointDirectory.open_resume(state) as directory:
        state_value = directory.load(mode="build", config_sha256="c" * 64,
                                     source_manifest_sha256="a" * 64)
    assert state_value.inventory[0].inventory_rows == (inventory_row,)
    secure_io.publish_create_new(tmp_path / "legacy.bin", b"payload")
    assert (tmp_path / "legacy.bin").read_bytes() == b"payload"


@POSIX_ONLY
def test_legacy_directory_tolerates_a_widened_mode_that_h2_refuses(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    state = tmp_path / "legacy"
    checkpoint.CheckpointDirectory.open_new(state).close()
    os.chmod(state, 0o755)
    # Legacy has no owner-private predicate and still opens.
    checkpoint.CheckpointDirectory.open_resume(state).close()
    with pytest.raises(rs.PolicyRefused):
        _resume_checkpoint(state, domains)


def test_windows_owner_private_publish_uses_the_scoped_native_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake-backend proof that ``owner_private_v1`` reaches the native seams."""
    events: list[tuple[object, ...]] = []

    class Direct:
        def __init__(self, identity: tuple[int, ...], size: int = 7) -> None:
            self.identity = identity
            self.size = size

    def create_owner_private_file(parent: int, name: str) -> int:
        events.append(("create_owner_private", parent, name))
        return 10

    def create_file(parent: int, name: str) -> int:  # pragma: no cover - guard
        raise AssertionError("owner_private_v1 must not use the legacy creator")

    def require_owner_private(handle: int, kind: str) -> Direct:
        events.append(("require_owner_private", handle, kind))
        return Direct((1, 2, 7, 100))

    strict_opens = 0

    def open_file(parent: int, name: str, **kwargs: object) -> int:
        nonlocal strict_opens
        if not name.startswith(".tmp-"):
            handle = 30
        elif kwargs.get("share_write") is True:
            handle = 15
        else:
            strict_opens += 1
            handle = 18 if strict_opens == 1 else 20
        events.append(("open", name, handle))
        return handle

    fake = types.SimpleNamespace(
        create_file=create_file,
        create_owner_private_file=create_owner_private_file,
        require_owner_private=require_owner_private,
        write=lambda handle, view: len(view),
        flush=lambda handle: None,
        close=lambda handle: events.append(("close", handle)),
        open_file=open_file,
        require_direct=lambda handle, kind: Direct((1, 2, 7, 100)),
        read=_offset_read(b"payload"),
        rename=lambda handle, parent, name, *, replace: events.append(("rename", name)),
        delete=lambda handle: events.append(("delete", handle)),
    )
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", fake)
    directory = rs.RegisterShardDirectory(tmp_path, windows_handles=(99,))
    monkeypatch.setattr(directory, "_revalidate", lambda: None)
    directory._windows_publish("register-00000000.sqlite", b"payload")
    assert ("create_owner_private", 99, next(
        event[2] for event in events if event[0] == "create_owner_private"
    )) in events
    assert ("require_owner_private", 30, "file") in events
    assert ("rename", "register-00000000.sqlite") in events


def test_missing_native_owner_private_support_is_a_controlled_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = types.SimpleNamespace(
        create_file=lambda parent, name: 10,
        require_direct=lambda handle, kind: types.SimpleNamespace(
            identity=(1, 2, 7, 100), size=7
        ),
        close=lambda handle: None,
        write=lambda handle, view: len(view),
        flush=lambda handle: None,
        open_file=lambda parent, name, **kwargs: 11,
        read=lambda handle, maximum: b"",
        rename=lambda *args, **kwargs: None,
        delete=lambda handle: None,
    )
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", fake)
    directory = rs.RegisterShardDirectory(tmp_path, windows_handles=(99,))
    monkeypatch.setattr(directory, "_revalidate", lambda: None)
    with pytest.raises(checkpoint.CheckpointRefusal):
        directory._windows_publish("register-00000000.sqlite", b"payload")


def test_legacy_windows_publish_never_reaches_the_owner_private_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Direct:
        def __init__(self) -> None:
            self.identity = (1, 2, 7, 100)
            self.size = 7

    def forbidden(*_args: object, **_kwargs: object) -> int:  # pragma: no cover
        raise AssertionError("legacy_shingle_v1 must not use owner-private creation")

    fake = types.SimpleNamespace(
        create_file=lambda parent, name: 10,
        create_owner_private_file=forbidden,
        require_owner_private=forbidden,
        require_direct=lambda handle, kind: Direct(),
        close=lambda handle: None,
        write=lambda handle, view: len(view),
        flush=lambda handle: None,
        open_file=lambda parent, name, **kwargs: 20,
        read=_offset_read(b"payload"),
        rename=lambda *args, **kwargs: None,
        delete=lambda handle: None,
    )
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", fake)
    directory = checkpoint.CheckpointDirectory(tmp_path, windows_handles=(99,))
    monkeypatch.setattr(directory, "_revalidate", lambda: None)
    directory._windows_publish("inventory-00000000.sqlite", b"payload")


# --------------------------------------------------------------------------
# Topology before creation (acceptance test 13)
# --------------------------------------------------------------------------


def _preflight(report: Path, state: Path, *, resume: bool = False) -> rs.TopologyPreflight:
    return rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=resume
    )


def test_portable_component_key_is_the_frozen_normalisation() -> None:
    assert rs.portable_component_key("Report.JSON") == "report.json"
    assert rs.portable_component_key("report.json. ") == "report.json"
    assert rs.portable_component_key("report.json ..") == "report.json"
    assert rs.portable_component_key("café") == rs.portable_component_key("café")
    assert rs.portable_component_key("STRASSE") == rs.portable_component_key("strasse")
    assert rs.portable_component_key("scoped") != rs.portable_component_key("score")


@POSIX_ONLY
def test_disjoint_fresh_topology_passes_and_creates_nothing(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    with _preflight(report, state) as preflight:
        assert preflight.report_path == report
        assert preflight.checkpoint_path == state
        assert not preflight.committed
    assert sorted(item.name for item in tmp_path.iterdir()) == []


@POSIX_ONLY
def test_lexical_equality_and_both_ancestor_directions_refuse(tmp_path: Path) -> None:
    same = tmp_path / "same"
    with pytest.raises(rs.PolicyRefused):
        _preflight(same, same)
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / "cp" / "report.json", tmp_path / "cp")
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / "cp", tmp_path / "cp" / "inner")
    assert sorted(item.name for item in tmp_path.iterdir()) == []


@pytest.mark.parametrize("report_name,state_name", [
    ("report.json", "REPORT.JSON"),
    ("report.json", "report.json. "),
    ("report.json", "report.json."),
    ("café", "café"),
    ("Report.Json", "report.json"),
])
@POSIX_ONLY
def test_same_parent_portable_key_collisions_refuse(
    tmp_path: Path, report_name: str, state_name: str
) -> None:
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / report_name, tmp_path / state_name)


@POSIX_ONLY
def test_portable_colliding_ancestor_spellings_refuse(tmp_path: Path) -> None:
    (tmp_path / "Runs").mkdir()
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / "Runs" / "report.json", tmp_path / "runs" / "cp")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "Alpha").mkdir()
    with pytest.raises(rs.PolicyRefused):
        _preflight(
            tmp_path / "deep" / "Alpha" / "report.json",
            tmp_path / "deep" / "alpha " / "cp",
        )


@POSIX_ONLY
def test_symlinked_components_refuse_in_either_position(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / "link" / "report.json", tmp_path / "cp")
    with pytest.raises(rs.PolicyRefused):
        _preflight(tmp_path / "report.json", tmp_path / "link" / "cp")


@POSIX_ONLY
def test_existing_report_name_including_a_hardlink_alias_refuses(
    tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    report.write_bytes(b"{}\n")
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, tmp_path / "cp")
    report.unlink()
    (tmp_path / "other.json").write_bytes(b"{}\n")
    os.link(tmp_path / "other.json", report)
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, tmp_path / "cp")
    report.unlink()
    report.symlink_to(tmp_path / "other.json")
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, tmp_path / "cp")


@POSIX_ONLY
def test_fresh_requires_both_absent_and_resume_requires_the_directory(
    tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, state, resume=True)
    state.mkdir(mode=0o700)
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, state, resume=False)
    _preflight(report, state, resume=True).close()
    # A non-directory at the checkpoint name refuses resume.
    state.rmdir()
    state.write_bytes(b"")
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, state, resume=True)


@POSIX_ONLY
def test_revalidation_catches_a_race_after_checkpoint_creation(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    preflight = _preflight(report, state)
    handle = _fresh_checkpoint(state, domains)
    preflight.revalidate()
    handle.publish_shard(_synthetic_rows(1, domains), final=True)
    preflight.revalidate()
    # A racing winner at the report name is never deleted, chmodded, or
    # overwritten; publication simply refuses.
    report.write_bytes(b"winner\n")
    with pytest.raises(rs.PolicyRefused):
        preflight.revalidate()
    with pytest.raises(rs.PolicyRefused):
        preflight.publish_report(b"{}\n")
    assert report.read_bytes() == b"winner\n"
    handle.close()
    preflight.close()


@POSIX_ONLY
def test_parent_swap_between_preflight_and_revalidation_refuses(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    report = parent / "report.json"
    state = tmp_path / "cp"
    preflight = _preflight(report, state)
    _fresh_checkpoint(state, domains).close()
    preflight.revalidate()
    replacement = tmp_path / "runs-2"
    replacement.mkdir()
    parent.rmdir()
    replacement.rename(parent)
    with pytest.raises(rs.PolicyRefused):
        preflight.revalidate()
    preflight.close()


@POSIX_ONLY
def test_identity_alias_makes_the_checkpoint_an_ancestor_of_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identity alias is refused even when no lexical prefix exists."""
    parent = tmp_path / "runs"
    parent.mkdir(mode=0o700)
    report = parent / "report.json"
    state = tmp_path / "cp"
    state.mkdir(mode=0o700)
    real_leaf_stat = rs._leaf_stat

    def aliased(handle: int, name: str) -> object:
        if name == state.name:
            # The checkpoint name resolves to the report's own parent inode.
            return os.stat(parent)
        return real_leaf_stat(handle, name)

    monkeypatch.setattr(rs, "_leaf_stat", aliased)
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, state, resume=True)


@POSIX_ONLY
def test_publication_is_the_terminal_commit_point(
    tmp_path: Path, domains: rs.RegisterDomains
) -> None:
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    preflight = _preflight(report, state)
    handle = _fresh_checkpoint(state, domains)
    handle.publish_shard(_synthetic_rows(1, domains), final=True)
    handle.close()
    preflight.revalidate()
    preflight.publish_report(b'{"schema_version":"x"}\n')
    assert report.read_bytes() == b'{"schema_version":"x"}\n'
    assert stat.S_IMODE(os.stat(report).st_mode) == 0o600
    assert preflight.committed
    # No post-publication revalidation, republication, or topology check exists.
    for attempt in (
        lambda: preflight.revalidate(),
        lambda: preflight.publish_report(b"{}\n"),
    ):
        with pytest.raises(rs.InternalError):
            attempt()
    assert report.read_bytes() == b'{"schema_version":"x"}\n'


@POSIX_ONLY
def test_no_failure_path_creates_the_second_target(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    with pytest.raises(rs.PolicyRefused):
        _preflight(report, tmp_path / "REPORT.JSON")
    assert sorted(item.name for item in tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# Expected-fingerprint document read (acceptance test 7)
# --------------------------------------------------------------------------


@POSIX_ONLY
def test_expected_fingerprint_read_agrees_with_the_frozen_plan(
    tmp_path: Path
) -> None:
    document = tmp_path / "doc.txt"
    document.write_bytes(b"hello world")
    fingerprint = rs.plan_document_fingerprint(document)
    assert len(fingerprint) == 5
    info = os.stat(document)
    assert fingerprint == (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    )
    assert rs.read_planned_document(document, fingerprint) == b"hello world"
    # The bounded helper still works without a plan, exactly as before.
    assert secure_io.read_bounded_regular(document, 1024) == b"hello world"


@POSIX_ONLY
def test_replaced_mutated_and_relinked_documents_refuse(tmp_path: Path) -> None:
    document = tmp_path / "doc.txt"
    document.write_bytes(b"hello world")
    fingerprint = rs.plan_document_fingerprint(document)

    # Same-size in-place mutation.
    with open(document, "r+b") as handle:
        handle.write(b"HELLO")
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(document, fingerprint)

    # Same-size mutation with a restored mtime still refuses on ctime_ns.
    os.utime(document, ns=(fingerprint[3], fingerprint[3]))
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(document, fingerprint)

    # Replacement by a fresh inode at the same name.
    document.unlink()
    document.write_bytes(b"hello world")
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(document, fingerprint)

    # Rebound name: the plan's inode still exists but the name now resolves
    # elsewhere.
    other = tmp_path / "other.txt"
    other.write_bytes(b"hello world")
    replanned = rs.plan_document_fingerprint(other)
    document.unlink()
    os.link(other, document)
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(document, fingerprint)
    # ...and the relinked node fails its own single-plan fingerprint too,
    # because the extra link advanced ctime_ns.
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(other, replanned)


@POSIX_ONLY
def test_expected_fingerprint_rejects_a_symlink_directory_or_oversized_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "doc.txt"
    document.write_bytes(b"x" * 32)
    fingerprint = rs.plan_document_fingerprint(document)
    monkeypatch.setattr(rs, "MAX_DOCUMENT_BYTES", 8)
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(document, fingerprint)
    monkeypatch.undo()

    link = tmp_path / "link.txt"
    link.symlink_to(document)
    with pytest.raises(rs.BadInput):
        rs.plan_document_fingerprint(link)
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(link, fingerprint)
    with pytest.raises(rs.BadInput):
        rs.plan_document_fingerprint(tmp_path)
    with pytest.raises(rs.BadInput):
        rs.read_planned_document(tmp_path / "absent.txt", fingerprint)


@POSIX_ONLY
def test_expected_fingerprint_argument_domain_is_closed(tmp_path: Path) -> None:
    document = tmp_path / "doc.txt"
    document.write_bytes(b"x")
    fingerprint = rs.plan_document_fingerprint(document)
    for bad in ((), [1, 2, 3, 4, 5], (True, 2, 3, 4, 5), ("a", 2, 3, 4, 5)):
        with pytest.raises(secure_io.SecureIOError):
            secure_io.read_bounded_regular(document, 1024, expected_fingerprint=bad)
    for bad in ((), "abc", None):
        with pytest.raises(rs.InternalError):
            rs.read_planned_document(document, bad)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.read_bounded_regular(
            document, 1024, expected_fingerprint=fingerprint[:-1] + (0,)
        )


def test_windows_scoped_fingerprint_adds_change_time(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scoped seam binds ``change_time``; ``NodeInfo.identity`` does not."""
    calls: list[int] = []

    def scoped_fingerprint(handle: int) -> tuple[int, ...]:
        calls.append(handle)
        return (1, 2, 3, 4, 5, 6, 0o100600, 1, 0x80)

    fake = types.SimpleNamespace(scoped_fingerprint=scoped_fingerprint)
    assert secure_io._windows_scoped_fingerprint(fake, 7) == (
        1, 2, 3, 4, 5, 6, 0o100600, 1, 0x80
    )
    assert calls == [7]
    # A backend without the scoped helper is a controlled refusal.
    with pytest.raises(secure_io.SecureIOError):
        secure_io._windows_scoped_fingerprint(types.SimpleNamespace(), 7)


def test_out_of_domain_row_values_inside_a_shard_are_a_policy_refusal(
    sealed_shard: tuple[bytes, rs.RegisterShard], domains: rs.RegisterDomains
) -> None:
    """A corrupt artifact refuses by policy; it never surfaces as internal."""
    raw, _shard = sealed_shard
    for replacement in (
        {"declared_family": "not_a_family"},
        {"classified_family": "unknown", "refusal_reason": None},
        {"refusal_reason": "not_a_reason", "classified_family": None},
        {"classified_family": domains.classified[0], "refusal_reason": domains.refusals[0]},
        {"classified_family": None, "refusal_reason": None},
    ):
        row = _row(0, declared=domains.classified[0], classified=domains.classified[0])
        row.update(replacement)
        row_json = json.dumps(
            row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        corrupt = _rewrite_shard(
            raw,
            [(
                "UPDATE rows SET row_json=?,row_sha256=? WHERE scoped_ordinal=0",
                (row_json, hashlib.sha256(row_json).digest()),
            )],
        )
        with pytest.raises(rs.PolicyRefused):
            rs.decode_register_shard(
                corrupt, name="register-00000000.sqlite", domains=domains,
                checkpoint_binding_sha256=BINDING_DIGEST,
            )
