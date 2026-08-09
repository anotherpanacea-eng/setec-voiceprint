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
    domains, and the value domains every builder enforces against them: the
    closed canonical JSON domain, the shard metadata scalars and their
    contiguous row block, the checkpoint row's family/reason domains, and the
    aggregate delta's key sets and cell domain.
  * The receipt schema half of acceptance test 1: every missing, extra,
    wrong-type, and noncanonical-byte receipt refuses, and the three H1
    identity digests are pinned to the same constants the CI-side gate holds.

Where a refusal site is not obvious from the call under test — a case that
could plausibly refuse at an earlier gate — the test names the site in a
comment, so no test passes green for a reason other than the one it claims.

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


@pytest.mark.parametrize(
    "value",
    [
        # Floats are forbidden everywhere, not only as non-finite values, and
        # not only at the top level.
        1.0,
        {"a": 1.0},
        {"a": 0.0},
        {"a": [1, 2.5]},
        {"a": {"b": [{"c": 3.0}]}},
        # Integers must fit signed 64 bits.
        {"a": 2**63},
        {"a": -(2**63) - 1},
        {"a": [2**63]},
        # Keys must be strings.
        {1: "a"},
        {"a": {2: "b"}},
        # Unsupported leaf types.
        {"a": object()},
        {"a": b"bytes"},
        {"a": (1, 2)},
        {"a": {"b"}},
        # Non-NFC keys and values: the decomposed sequence U+0041 U+030A,
        # which NFC folds to the single code point U+00C5. Written as an
        # escape so no editor can silently normalize the fixture away.
        {"a": "A\u030a"},
        {"A\u030a": "a"},
        {"a": ["A\u030a"]},
    ],
)
def test_canonical_json_walks_a_closed_domain(value: object) -> None:
    with pytest.raises(rs.InternalError):
        rs.canonical_json(value)


def test_canonical_json_admits_the_closed_domain() -> None:
    assert rs.canonical_json(
        {"a": None, "b": True, "c": -1, "d": "é", "e": [], "f": {}}
    ) == b'{"a":null,"b":true,"c":-1,"d":"\xc3\xa9","e":[],"f":{}}'
    assert rs.canonical_json({"a": rs.INT64_MAX}) == (
        b'{"a":9223372036854775807}'
    )
    assert rs.canonical_json({"a": rs.INT64_MIN}) == (
        b'{"a":-9223372036854775808}'
    )


def test_canonical_json_refuses_a_cyclic_value() -> None:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(rs.InternalError):
        rs.canonical_json(cycle)


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


@pytest.mark.parametrize(
    "domain",
    [
        b"setec-register-sweep-scope-v3\n",
        b"setec-register-sweep-scope-v1\n",
        b"setec-register-sweep-shard-v2\n\n",
        b"setec-register-family-mapping-v2\n ",
        b"\n",
    ],
)
def test_framed_sha256_refuses_an_unfrozen_domain(domain: bytes) -> None:
    # An unfrozen domain has no payload schema at all; framing under one would
    # mint an identity the spec never defined.
    with pytest.raises(rs.InternalError):
        rs.framed_sha256(domain, b'{"rows":[]}')


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


def _scoped_entry(manifest_ordinal: int, scoped_ordinal: int) -> dict[str, object]:
    return {
        "manifest_ordinal": manifest_ordinal,
        "projected_row_sha256": rs.prefixed(ROW_DIGEST),
        "scoped_ordinal": scoped_ordinal,
    }


def test_scoped_rows_require_strictly_ascending_manifest_ordinals() -> None:
    # The scoped slice is in manifest order, so a filtered slice skips manifest
    # ordinals but never repeats or reverses one.
    rs.scoped_rows_binding([_scoped_entry(0, 0), _scoped_entry(7, 1)])
    for entries in (
        [_scoped_entry(7, 0), _scoped_entry(0, 1)],
        [_scoped_entry(3, 0), _scoped_entry(3, 1)],
    ):
        with pytest.raises(rs.InternalError):
            rs.scoped_rows_binding(entries)


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


def test_checkpoint_row_pins_the_family_and_reason_domains() -> None:
    scored = {**CANONICAL_ROW, "classified_family": "academic", "refusal_reason": None}
    # ``unknown`` is a declared value only; it is never a classified family.
    rs.checkpoint_row_binding({**CANONICAL_ROW, "declared_family": "unknown"})
    for bad in (
        {**CANONICAL_ROW, "declared_family": "not_a_family"},
        {**CANONICAL_ROW, "declared_family": None},
        {**CANONICAL_ROW, "declared_family": "Academic"},
        {**scored, "classified_family": "unknown"},
        {**scored, "classified_family": "not_a_family"},
        {**scored, "classified_family": True},
        {**CANONICAL_ROW, "refusal_reason": "not_a_reason"},
        {**CANONICAL_ROW, "refusal_reason": "unknown"},
    ):
        with pytest.raises(rs.InternalError):
            rs.checkpoint_row_binding(bad)


def test_checkpoint_row_accepts_every_family_and_reason_in_domain() -> None:
    for declared in rs.H1_DECLARED_FAMILIES:
        rs.checkpoint_row_binding({**CANONICAL_ROW, "declared_family": declared})
    for classified in rs.H1_CLASSIFIED_FAMILIES:
        rs.checkpoint_row_binding(
            {
                **CANONICAL_ROW,
                "classified_family": classified,
                "refusal_reason": None,
            }
        )
    for reason in rs.H1_REFUSAL_REASONS:
        rs.checkpoint_row_binding({**CANONICAL_ROW, "refusal_reason": reason})


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


def test_aggregate_delta_pins_the_counts_key_set(binding: rs.H1Binding) -> None:
    delta = _coherent_delta(binding)
    counts = delta["counts"]
    assert set(counts) == set(rs.SHARD_COUNT_KEYS)  # type: ignore[arg-type]
    assert len(rs.SHARD_COUNT_KEYS) == 11
    for broken in (
        {
            k: v
            for k, v in counts.items()  # type: ignore[union-attr]
            if k != "scoped_words"
        },
        {**counts, "mixture_score": 0},  # type: ignore[dict-item]
    ):
        with pytest.raises(rs.InternalError):
            rs.aggregate_delta_binding({**delta, "counts": broken})


@pytest.mark.parametrize("value", [True, False, -1, 2**63, 1.0, "1", None])
def test_aggregate_delta_refuses_hostile_count_values(
    binding: rs.H1Binding, value: object
) -> None:
    delta = _coherent_delta(binding)
    counts = {**delta["counts"], "scoped_words": value}  # type: ignore[dict-item]
    with pytest.raises(rs.InternalError):
        rs.aggregate_delta_binding({**delta, "counts": counts})


@pytest.mark.parametrize("value", [True, -1, 2**63, 1.0, "1", None])
def test_aggregate_delta_refuses_hostile_cell_values(
    binding: rs.H1Binding, value: object
) -> None:
    delta = _coherent_delta(binding)
    inventory = {
        **delta["refusal_inventory"],  # type: ignore[dict-item]
        "short_text": {"documents": 1, "words": value},
    }
    with pytest.raises(rs.InternalError):
        rs.aggregate_delta_binding({**delta, "refusal_inventory": inventory})


def test_aggregate_delta_refuses_a_non_domain_cell_shape(
    binding: rs.H1Binding,
) -> None:
    delta = _coherent_delta(binding)
    for cell in (
        {"documents": 1},
        {"documents": 1, "words": 5, "share": 1},
        {"documents": 1, "chars": 5},
        [1, 5],
        1,
    ):
        inventory = {
            **delta["match_inventory"],  # type: ignore[dict-item]
            "same": cell,
        }
        with pytest.raises(rs.InternalError):
            rs.aggregate_delta_binding({**delta, "match_inventory": inventory})


def test_aggregate_delta_pins_every_inventory_domain(
    binding: rs.H1Binding,
) -> None:
    delta = _coherent_delta(binding)
    for key, extra_key in (
        ("declared_family_inventory", "not_a_family"),
        ("classified_family_inventory", "unknown"),
        ("refusal_inventory", "not_a_reason"),
        ("match_inventory", "partial"),
    ):
        inventory = delta[key]
        widened = {**inventory, extra_key: rs.zero_cell()}  # type: ignore[dict-item]
        narrowed = {
            k: v
            for k, v in list(inventory.items())[1:]  # type: ignore[union-attr]
        }
        for broken in (widened, narrowed):
            with pytest.raises(rs.InternalError):
                rs.aggregate_delta_binding({**delta, key: broken})


def test_aggregate_delta_pins_the_crosstab_shape(binding: rs.H1Binding) -> None:
    delta = _coherent_delta(binding)
    crosstab = delta["declared_by_classified_family"]
    assert set(crosstab) == set(rs.H1_DECLARED_FAMILIES)  # type: ignore[arg-type]
    for row in crosstab.values():  # type: ignore[union-attr]
        assert set(row) == set(rs.H1_CLASSIFIED_FAMILIES)
    outer_widened = {
        **crosstab,  # type: ignore[dict-item]
        "not_a_family": dict(crosstab["unknown"]),  # type: ignore[index]
    }
    inner_widened = {
        **crosstab,
        "unknown": {
            **crosstab["unknown"],  # type: ignore[index]
            "unknown": rs.zero_cell(),
        },
    }
    for broken in (outer_widened, inner_widened):
        with pytest.raises(rs.InternalError):
            rs.aggregate_delta_binding(
                {**delta, "declared_by_classified_family": broken}
            )


def test_aggregate_delta_records_no_cross_cell_equation(
    binding: rs.H1Binding,
) -> None:
    # This encoder is a structural/domain check only. Equation checking (row
    # sums, per-shard document ceilings, match derivation) belongs to the
    # runner increment, so an internally inconsistent but well-formed delta
    # still frames here.
    delta = _coherent_delta(binding)
    counts = {**delta["counts"], "scoped_documents": 999}  # type: ignore[dict-item]
    _, digest = rs.aggregate_delta_binding({**delta, "counts": counts})
    assert digest != AGGREGATE_DELTA_DIGEST


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


def _shard_metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "checkpoint_binding_sha256": rs.prefixed(CHECKPOINT_BINDING_DIGEST),
        "first_scoped_ordinal": 0,
        "kind": "register",
        "next_scoped_ordinal": 2,
        "prior_shard_sha256": None,
        "schema_version": "setec-register-sweep-checkpoint/2",
        "shard_number": 0,
    }
    metadata.update(overrides)
    return metadata


def _shard_rows(first: int, count: int) -> list[dict[str, object]]:
    return [
        {
            "row_json_sha256": rs.prefixed(f"{index:064x}"),
            "scoped_ordinal": first + index,
        }
        for index in range(count)
    ]


def _frame_shard(
    binding: rs.H1Binding,
    metadata: dict[str, object],
    rows: list[dict[str, object]],
) -> tuple[bytes, str]:
    _, delta_digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    return rs.shard_binding(
        aggregate_delta_sha256=rs.prefixed(delta_digest),
        metadata=metadata,
        rows=rows,
    )


def test_shard_binding_rejects_noncontiguous_unsorted_and_duplicate_ordinals(
    binding: rs.H1Binding,
) -> None:
    # Row k carries scoped ordinal ``first_scoped_ordinal + k``, so a reversed
    # or repeated pair is caught as a contiguity break at the first offending
    # offset rather than only as an ordering break.
    metadata = _shard_metadata()
    for rows in (
        [
            {"row_json_sha256": rs.prefixed("1" * 64), "scoped_ordinal": 1},
            {"row_json_sha256": rs.prefixed("2" * 64), "scoped_ordinal": 0},
        ],
        [
            {"row_json_sha256": rs.prefixed("1" * 64), "scoped_ordinal": 0},
            {"row_json_sha256": rs.prefixed("2" * 64), "scoped_ordinal": 0},
        ],
        [
            {"row_json_sha256": rs.prefixed("1" * 64), "scoped_ordinal": 0},
            {"row_json_sha256": rs.prefixed("2" * 64), "scoped_ordinal": 2},
        ],
    ):
        with pytest.raises(rs.InternalError):
            _frame_shard(binding, metadata, rows)


def test_shard_rows_start_at_the_first_scoped_ordinal(
    binding: rs.H1Binding,
) -> None:
    metadata = _shard_metadata(first_scoped_ordinal=250, next_scoped_ordinal=252)
    _frame_shard(binding, metadata, _shard_rows(250, 2))
    with pytest.raises(rs.InternalError):
        _frame_shard(binding, metadata, _shard_rows(0, 2))


def test_shard_row_count_bounds_and_next_ordinal_agree(
    binding: rs.H1Binding,
) -> None:
    # A full shard is exactly SHARD_ROWS rows; an empty shard is never framed
    # (an empty scope creates no shard at all).
    _frame_shard(
        binding,
        _shard_metadata(next_scoped_ordinal=rs.SHARD_ROWS),
        _shard_rows(0, rs.SHARD_ROWS),
    )
    with pytest.raises(rs.InternalError):
        _frame_shard(binding, _shard_metadata(next_scoped_ordinal=1), [])
    with pytest.raises(rs.InternalError):
        _frame_shard(
            binding,
            _shard_metadata(next_scoped_ordinal=rs.SHARD_ROWS + 1),
            _shard_rows(0, rs.SHARD_ROWS + 1),
        )
    # next_scoped_ordinal must equal first + len(rows), not merely be in range.
    with pytest.raises(rs.InternalError):
        _frame_shard(binding, _shard_metadata(next_scoped_ordinal=3), _shard_rows(0, 2))


@pytest.mark.parametrize(
    "overrides",
    [
        {"checkpoint_binding_sha256": CHECKPOINT_BINDING_DIGEST},  # unprefixed
        {"checkpoint_binding_sha256": None},
        {"prior_shard_sha256": "0" * 64},  # unprefixed
        {"prior_shard_sha256": 0},
        {"shard_number": -1},
        {"shard_number": rs.MAX_FINAL_SHARDS},
        {"shard_number": True},
        {"shard_number": "0"},
        {"first_scoped_ordinal": -1},
        {"first_scoped_ordinal": rs.MAX_SCOPED_DOCUMENTS},
        {"first_scoped_ordinal": True},
        {"next_scoped_ordinal": 0},
        {"next_scoped_ordinal": rs.MAX_SCOPED_DOCUMENTS + 1},
        {"next_scoped_ordinal": True},
        {"first_scoped_ordinal": 0, "next_scoped_ordinal": rs.SHARD_ROWS + 1},
        {"kind": "sweep"},
        {"kind": None},
        {"schema_version": "setec-register-sweep-checkpoint/1"},
        {"schema_version": None},
    ],
)
def test_shard_metadata_values_are_domain_checked(
    binding: rs.H1Binding, overrides: dict[str, object]
) -> None:
    with pytest.raises(rs.InternalError):
        _frame_shard(binding, _shard_metadata(**overrides), _shard_rows(0, 2))


def test_shard_metadata_accepts_a_prefixed_prior_shard_digest(
    binding: rs.H1Binding,
) -> None:
    payload, _ = _frame_shard(
        binding,
        _shard_metadata(
            shard_number=1,
            first_scoped_ordinal=250,
            next_scoped_ordinal=252,
            prior_shard_sha256=rs.prefixed(SHARD_DIGEST),
        ),
        _shard_rows(250, 2),
    )
    assert json.loads(payload)["metadata"]["prior_shard_sha256"] == rs.prefixed(
        SHARD_DIGEST
    )


def test_shard_metadata_key_set_is_closed(binding: rs.H1Binding) -> None:
    # Refusal site: the metadata key-set check, which runs before any row or
    # value domain check, so the empty row list here is not what refuses.
    _, delta_digest = rs.aggregate_delta_binding(_coherent_delta(binding))
    with pytest.raises(rs.InternalError):
        rs.shard_binding(
            aggregate_delta_sha256=rs.prefixed(delta_digest),
            metadata={"shard_number": 0},
            rows=[],
        )
    with pytest.raises(rs.InternalError):
        rs.shard_binding(
            aggregate_delta_sha256=rs.prefixed(delta_digest),
            metadata=_shard_metadata(shard_sha256=rs.prefixed(SHARD_DIGEST)),
            rows=_shard_rows(0, 2),
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
    # The module constants are the same freeze, and the loaded namespace is
    # equality-pinned to them: D == REGISTER_FAMILIES.
    assert tuple(families) == rs.H1_REGISTER_FAMILIES
    assert rs.H1_DECLARED_FAMILIES == rs.H1_REGISTER_FAMILIES
    assert rs.H1_CLASSIFIED_FAMILIES == rs.H1_REGISTER_FAMILIES[:-1]
    assert rs.H1_REGISTER_FAMILIES[-1] == "unknown"
    assert binding.classified_domain is rs.H1_CLASSIFIED_FAMILIES
    assert binding.declared_domain is rs.H1_DECLARED_FAMILIES
    assert binding.refusal_domain is rs.H1_REFUSAL_REASONS


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
    "key,constant",
    [
        ("classifier_sha256", "H1_FINAL_CLASSIFIER_SHA256"),
        ("mapping_sha256", "H1_MAPPING_SHA256"),
        ("refusal_contract_sha256", "H1_REFUSAL_CONTRACT_SHA256"),
    ],
)
def test_receipt_identity_digests_are_pinned_to_the_ci_gate_constants(
    key: str, constant: str
) -> None:
    # Parity with tools/check_register_sweep_h1_gate.py: each of the three H1
    # identity digests is equality-pinned on its own, so dropping any one pin
    # is caught here rather than masked by a sibling pin.
    receipt = _real_receipt()
    assert receipt[key] == getattr(rs, constant)
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**receipt, key: "0" * 64})


def test_receipt_pins_match_the_ci_checker_source() -> None:
    # The checker is frozen; read its constants out of the file rather than
    # restating them, so a future divergence is visible here.
    checker = (
        Path(rs.__file__).resolve().parents[3]
        / "tools"
        / "check_register_sweep_h1_gate.py"
    ).read_text(encoding="utf-8")
    for value in (
        rs.H1_FINAL_CLASSIFIER_SHA256,
        rs.H1_MAPPING_SHA256,
        rs.H1_REFUSAL_CONTRACT_SHA256,
        rs.H1_BASE_CLASSIFIER_SHA256,
        rs.H1_SPEC_SHA256,
        rs.H1_REFUSAL_SPEC_SHA256,
    ):
        assert value in checker


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


def _receipt_of_exactly(total_bytes: int) -> tuple[dict[str, object], bytes]:
    """A canonical receipt whose bytes-with-terminal-LF are exactly n."""
    # b'{"a":"' + padding + b'"}' is 8 bytes of framing; +1 for the LF.
    value = {"a": "x" * (total_bytes - 9)}
    data = rs.canonical_json(value) + b"\n"
    assert len(data) == total_bytes
    return value, data


def test_receipt_read_boundary_is_the_ceiling_byte(tmp_path: Path) -> None:
    # Schema validation is not applied by read_h1_receipt, so this isolates the
    # ceiling itself: exactly MAX_H1_RECEIPT_BYTES reads, one more refuses.
    value, data = _receipt_of_exactly(rs.MAX_H1_RECEIPT_BYTES)
    at_ceiling = tmp_path / "at_ceiling.json"
    at_ceiling.write_bytes(data)
    parsed, raw = rs.read_h1_receipt(at_ceiling)
    assert parsed == value
    assert len(raw) == rs.MAX_H1_RECEIPT_BYTES

    _, over = _receipt_of_exactly(rs.MAX_H1_RECEIPT_BYTES + 1)
    over_ceiling = tmp_path / "over_ceiling.json"
    over_ceiling.write_bytes(over)
    with pytest.raises(rs.PolicyRefused):
        rs.read_h1_receipt(over_ceiling)


def test_receipt_read_refuses_a_value_outside_the_canonical_domain(
    tmp_path: Path,
) -> None:
    # Refusal site: the canonical_json comparison inside read_h1_receipt. A
    # float or an out-of-range integer parses and round-trips as JSON text but
    # is outside H2's closed canonical domain, and that failure belongs to the
    # H1-identity taxonomy (PolicyRefused), not to InternalError.
    for name, data in (
        ("float.json", b'{"a":1.0}\n'),
        ("bigint.json", b'{"a":9223372036854775808}\n'),
    ):
        target = tmp_path / name
        target.write_bytes(data)
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


def _synthetic_namespace(tmp_path: Path, source: bytes) -> dict[str, object]:
    """Write and execute synthetic classifier source in a private namespace.

    A synthetic classifier can no longer reach ``load_h1_binding``: its raw
    digest is not ``H1_FINAL_CLASSIFIER_SHA256``, and a receipt forged to agree
    with it refuses in ``validate_h1_receipt`` against that pinned constant.
    Namespace-level hostility is therefore covered at the inner seams the
    loader runs after ``exec`` — which is where those refusals actually live —
    with the end-to-end digest-mismatch path covered separately below.
    """
    classifier = tmp_path / "register_classifier.py"
    classifier.write_bytes(source)
    return rs._execute_classifier(source, classifier)


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


def test_synthetic_faithful_namespace_reproduces_the_pinned_identities(
    tmp_path: Path,
) -> None:
    namespace = _synthetic_namespace(tmp_path, _faithful_source())
    rs._validate_h1_callables(namespace)
    mapping_payload, mapping_digest = rs.mapping_binding(namespace)
    refusal_payload, refusal_digest = rs.refusal_contract_binding(namespace)
    assert len(mapping_payload) == 1_147
    assert len(refusal_payload) == 140
    assert mapping_digest == MAPPING_DIGEST == rs.H1_MAPPING_SHA256
    assert refusal_digest == REFUSAL_DIGEST == rs.H1_REFUSAL_CONTRACT_SHA256


def test_mapping_drift_refuses_against_the_pinned_receipt_constants(
    tmp_path: Path,
) -> None:
    # A length-preserving remap: 'formal_legal_policy' and
    # 'formal_first_person' are both nineteen characters, so the canonical
    # mapping payload is still exactly 1,147 bytes and only the digest moves.
    drifted = _faithful_source().replace(
        b"'policy_brief': 'formal_legal_policy'",
        b"'policy_brief': 'formal_first_person'",
    )
    assert drifted != _faithful_source()
    # The drift is visible at the identity seam itself: the framed mapping no
    # longer reproduces the pinned public digest.
    namespace = _synthetic_namespace(tmp_path, drifted)
    drifted_payload, drifted_digest = rs.mapping_binding(namespace)
    assert len(drifted_payload) == 1_147
    assert drifted_digest != rs.H1_MAPPING_SHA256
    # And a receipt regenerated to agree with the drift buys nothing: the
    # schema pins classifier/mapping/refusal to the module constants, so this
    # refuses inside ``validate_h1_receipt`` rather than at the later
    # namespace-derived comparison.
    receipt = dict(_real_receipt())
    receipt["classifier_sha256"] = rs.raw_sha256(drifted)
    receipt["mapping_sha256"] = drifted_digest
    receipt_path = tmp_path / "receipt.json"
    digest = _write_receipt(receipt_path, receipt)
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path,
            classifier_path=tmp_path / "register_classifier.py",
            expected_receipt_sha256=digest,
        )


def _seam_callables(namespace: dict[str, object]) -> None:
    rs._validate_h1_callables(namespace)


def _seam_mapping(namespace: dict[str, object]) -> None:
    rs.mapping_binding(namespace)


def _seam_refusal(namespace: dict[str, object]) -> None:
    rs.refusal_contract_binding(namespace)


SEAMS = {
    "callables": _seam_callables,
    "mapping": _seam_mapping,
    "refusal": _seam_refusal,
}


@pytest.mark.parametrize(
    "mutation,seam",
    [
        ("REGISTER_TAXONOMY = 'register_families/v1'", "mapping"),
        ("KNOWN_REGISTERS = ()", "mapping"),
        ("REGISTER_FAMILIES = ['a', 'b']", "mapping"),
        ("REGISTER_REFUSAL_REASONS = ('short_text', 'all_weak')", "refusal"),
        (
            "REGISTER_REFUSAL_REASONS = ('all_weak', 'short_text', 'exact_top_tie')",
            "refusal",
        ),
        ("CANONICAL_REGISTER_TO_FAMILY = {'personal': 'not_a_family'}", "mapping"),
        ("LEGACY_REGISTER_TO_FAMILY = {1: 'academic'}", "mapping"),
        ("del resolve_family", "callables"),
        ("del classify_register", "callables"),
        (
            "def classify_register(text, hint=None, min_words=100): return RESULT",
            "callables",
        ),
        (
            "def classify_register(text, *, hint=None, min_words=50): return RESULT",
            "callables",
        ),
        ("def classify_register(text, *, min_words=100): return RESULT", "callables"),
        ("def resolve_family(v): return 'unknown'", "callables"),
        ("def resolve_family(value='x'): return 'unknown'", "callables"),
        ("resolve_family = 3", "callables"),
    ],
)
def test_hostile_classifier_namespaces_refuse_at_their_seam(
    tmp_path: Path, mutation: str, seam: str
) -> None:
    # Each case names the seam that must refuse, so no case can pass by
    # refusing somewhere unrelated.
    namespace = _synthetic_namespace(
        tmp_path, _faithful_source() + mutation.encode("utf-8") + b"\n"
    )
    with pytest.raises(rs.PolicyRefused):
        SEAMS[seam](namespace)


def test_mapping_binding_refuses_an_off_length_payload(tmp_path: Path) -> None:
    # A structurally valid mapping that encodes to a different canonical length
    # is not the landed H1 identity. This is the CI checker's 1,147-byte pin.
    source = _faithful_source() + (
        "CANONICAL_REGISTER_TO_FAMILY = dict(CANONICAL_REGISTER_TO_FAMILY)\n"
        "CANONICAL_REGISTER_TO_FAMILY['zz_extra'] = 'academic'\n"
    ).encode("utf-8")
    namespace = _synthetic_namespace(tmp_path, source)
    with pytest.raises(rs.PolicyRefused):
        rs.mapping_binding(namespace)


def test_h1_binding_pins_the_register_family_tuple(tmp_path: Path) -> None:
    namespace = _synthetic_namespace(tmp_path, _faithful_source())
    families = rs.H1_REGISTER_FAMILIES
    reordered = (families[1], families[0]) + families[2:]
    namespace["REGISTER_FAMILIES"] = reordered
    namespace["KNOWN_REGISTERS"] = reordered
    # A reordered tuple still encodes to 1,147 bytes, so the mapping seam
    # cannot catch it; the binding's own equality pin is what does.
    payload, digest = rs.mapping_binding(namespace)
    assert len(payload) == 1_147
    assert digest != rs.H1_MAPPING_SHA256
    with pytest.raises(rs.PolicyRefused):
        rs.H1Binding(
            namespace=namespace,
            receipt={},
            receipt_sha256=rs.H1_RECEIPT_SHA256,
            classifier_sha256=rs.H1_FINAL_CLASSIFIER_SHA256,
            mapping_sha256=digest,
            refusal_contract_sha256=rs.H1_REFUSAL_CONTRACT_SHA256,
        )
    namespace["REGISTER_REFUSAL_REASONS"] = ("short_text",)
    with pytest.raises(rs.PolicyRefused):
        rs.H1Binding(
            namespace=namespace,
            receipt={},
            receipt_sha256=rs.H1_RECEIPT_SHA256,
            classifier_sha256=rs.H1_FINAL_CLASSIFIER_SHA256,
            mapping_sha256=digest,
            refusal_contract_sha256=rs.H1_REFUSAL_CONTRACT_SHA256,
        )


def test_classifier_source_that_fails_to_execute_refuses(
    tmp_path: Path,
) -> None:
    # Refusal site: ``_execute_classifier``. A forged receipt can no longer
    # carry non-H1 source as far as the loader, so the exec seam is exercised
    # directly; the end-to-end path for this class is the digest-mismatch test
    # below.
    source = b"raise RuntimeError('boom')\n"
    classifier = tmp_path / "register_classifier.py"
    classifier.write_bytes(source)
    with pytest.raises(rs.PolicyRefused):
        rs._execute_classifier(source, classifier)


@pytest.mark.parametrize(
    "label,source",
    [
        ("missing callable", b"del resolve_family\n"),
        ("drifted refusal contract", b"REGISTER_REFUSAL_REASONS = ('short_text',)\n"),
        ("raises on import", None),
    ],
)
def test_hostile_classifier_on_disk_refuses_end_to_end(
    tmp_path: Path, label: str, source: bytes | None
) -> None:
    # Refusal site: the raw ``classifier_sha256`` gate in ``load_h1_binding``,
    # reached with the REAL committed receipt. One end-to-end case per hostile
    # class, so the inner-seam table above is not the only coverage.
    receipt_path, _ = rs.default_h1_paths()
    body = (
        b"raise RuntimeError('boom')\n"
        if source is None
        else _faithful_source() + source
    )
    classifier = tmp_path / "register_classifier.py"
    classifier.write_bytes(body)
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path, classifier_path=classifier
        )


def test_a_digest_mismatched_classifier_is_never_executed(
    tmp_path: Path,
) -> None:
    # The raw-digest gate runs before compile/exec, so module-level code in a
    # drifted classifier must never run.
    receipt_path, _ = rs.default_h1_paths()
    marker = tmp_path / "executed.marker"
    classifier = tmp_path / "register_classifier.py"
    classifier.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    with pytest.raises(rs.PolicyRefused):
        rs.load_h1_binding(
            receipt_path=receipt_path, classifier_path=classifier
        )
    assert not marker.exists()


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


def test_a_full_eight_member_secondary_validates_under_an_unknown_primary(
    binding: rs.H1Binding,
) -> None:
    # The cap is MAX_SECONDARY == 8 and |F| == 8, so with the distinctness and
    # in-domain rules already enforced the length cap is structurally
    # redundant: a list of eight distinct members of F is the largest one that
    # can exist. It is still reachable in real H1 through an exact_top_tie,
    # where the primary is "unknown" and therefore excluded from no member of
    # F, so the positive case is exercised rather than left hypothetical.
    assert rs.MAX_SECONDARY == 8
    assert len(binding.classified_domain) == 8
    result = {
        **_refusal_result(binding),
        "refusal_reason": "exact_top_tie",
        "secondary": list(binding.classified_domain),
        "scores": {name: 0.5 for name in binding.classified_domain},
        "evidence": _good_result(binding)["evidence"],
    }
    validated = binding.validate_classification(result, min_words=100)
    assert len(validated["secondary"]) == rs.MAX_SECONDARY
    with pytest.raises(rs.PolicyRefused):
        binding.validate_classification(
            {**result, "secondary": list(binding.classified_domain) + ["academic"]},
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


def test_importing_the_sweep_opens_no_network_or_subprocess_surface() -> None:
    """The sweep walks private corpus text; importing it must load no
    exfiltration-capable module.  Checked behaviorally (a fresh interpreter's
    sys.modules after import), not by source grep, so a conditional or aliased
    import cannot slip past a substring check."""
    import subprocess

    surfaces = ("socket", "ssl", "urllib", "http", "subprocess", "requests")
    probe = (
        "import json,sys; import register_sweep; "
        f"print(json.dumps([name for name in {surfaces!r} if name in sys.modules]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(rs.__file__).resolve().parent,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


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
        "persona": "alias-a",
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
        "persona": "alias-a",
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
        persona="alias-a",
        ai_status="pre_ai_human",
    )


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
#
# EVERY hostile case in this block points its row at ``doc.txt``, so the
# document MUST exist in ``tmp_path`` before the projection runs. Without it the
# projection refuses on the missing file and the strict-parser hardening under
# test is never reached -- the whole block stays green with the hardenings
# disabled. ``test_strict_parser_positive_control`` is the paired proof that the
# well-formed sibling of these manifests projects.


def _strict_parser_document(tmp_path: Path) -> Path:
    """Create the ``doc.txt`` every row in this block resolves to."""
    document = tmp_path / "doc.txt"
    document.write_text("tiny synthetic document\n", encoding="utf-8")
    return document


def test_strict_parser_positive_control(tmp_path: Path) -> None:
    """The well-formed sibling of this block's hostile manifests PROJECTS.

    This is what makes each refusal below attributable to the hostile bytes
    rather than to a missing document.
    """
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = json.dumps(_h2_row_dict()).encode("utf-8") + b"\n"
    result = mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)
    assert result.input_rows == 1
    assert len(result.rows) == 1
    assert len(result.document_plan) == 1
    assert result.rows[0].path == "doc.txt"


def test_bom_refuses_before_any_row_projects(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = b"\xef\xbb\xbf" + json.dumps(_h2_row_dict()).encode("utf-8") + b"\n"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)
    # Attribution: the BOM refusal is the decode layer's own, not a downstream
    # JSON syntax error that happens to also reject a BOM-prefixed first row.
    with pytest.raises(mv.ManifestParseError):
        mv.decode_manifest_bytes(data)


def test_non_utf8_bytes_refuse(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = json.dumps(_h2_row_dict()).encode("utf-8") + b"\n\xff\xfe"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_top_level_duplicate_key_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    (tmp_path / "other.txt").write_text("other", encoding="utf-8")
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","path":"other.txt","ai_status":"pre_ai_human",'
        b'"use":["baseline"]}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_nested_duplicate_key_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","ai_status":"pre_ai_human","use":["baseline"],'
        b'"notes":{"a":1,"a":2}}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_non_finite_constant_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = (
        b'{"path":"doc.txt","ai_status":"pre_ai_human","use":["baseline"],'
        b'"word_count":NaN}\n'
    )
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_non_object_data_row_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(b"[1,2,3]\n", manifest_path=manifest_path)


def test_malformed_json_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(b"{bad-json\n", manifest_path=manifest_path)


def test_wrong_type_data_refuses(tmp_path: Path) -> None:
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes("not bytes", manifest_path=manifest_path)  # type: ignore[arg-type]


# ---- Row-boundary hardening: str.splitlines() row splitting --------------


@pytest.mark.parametrize(
    "breaker",
    ["\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_splitlines_only_row_separators_refuse(
    tmp_path: Path, breaker: str
) -> None:
    """Two objects on ONE physical LF-delimited line refuse at the line level.

    ``str.splitlines()`` breaks on each of these characters, so the single
    physical row below would have been counted as TWO documents -- and the row
    count plus every row's ordinal are baked into this build's frozen digests.
    The refusal fires before any JSON parsing, so an unowned field can't smuggle
    a row split past the owned fields' string domain either.
    """
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    encoded = json.dumps(_h2_row_dict()).encode("utf-8")
    data = encoded + breaker.encode("utf-8") + encoded + b"\n"
    # Exactly one physical row, but two under the old splitlines() rule.
    assert data.count(b"\n") == 1
    text = data.decode("utf-8")
    assert len(text.splitlines()) == 2
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(data, manifest_path=manifest_path)


def test_a_crlf_terminated_manifest_projects_as_one_row(
    tmp_path: Path,
) -> None:
    """A trailing "\r\n" is one Windows line terminator, not an embedded
    break: native-Windows text-mode writers produce it for every manifest, and
    refusing it broke the legacy validator on Windows CI. Bare/interior "\r"
    still refuses (see the CRLF carve-in tests at the end of this file)."""
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    data = json.dumps(_h2_row_dict()).encode("utf-8") + b"\r\n"
    projection = mv.project_register_sweep_manifest_bytes(
        data, manifest_path=manifest_path
    )
    assert projection.input_rows == 1


def test_the_legacy_validator_shares_the_row_boundary_refusal(
    tmp_path: Path,
) -> None:
    """The hardening lives in the SHARED parser, so the legacy full-row
    validator bails out on the same bytes instead of reflowing them."""
    _strict_parser_document(tmp_path)
    manifest_path = tmp_path / "m.jsonl"
    encoded = json.dumps(_h2_row_dict())
    manifest_path.write_text(
        encoded + "\u2028" + encoded + "\n", encoding="utf-8"
    )
    report = mv.validate_manifest(manifest_path)
    assert report["n_entries"] == 0
    assert report["n_errors"] == 1
    assert any(
        "forbidden line-break character" in issue["message"]
        for issue in report["issues"]
    )


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
# ---- Increment D1: aggregation, report, guard, envelopes ----
#
# Acceptance test 8 (fixed domains and equations), acceptance test 9 (minimal
# inventory and report types), acceptance test 17 (mechanical claim posture),
# and the aggregation-layer halves of acceptance tests 15/16 (privacy/errors
# and exact output). Every fixture is synthetic; no private corpus, aggregate,
# identifier, path, or prose appears here.


# --------------------------------------------------------------------------
# Spec literals
#
# These are transcribed from the spec, not read back from the module, so a
# drift in either direction fails.
# --------------------------------------------------------------------------

SPEC_LICENSES = (
    "Aggregate register-family count inventory for a hand-check of the "
    "explicitly scoped manifest slice."
)
SPEC_DOES_NOT_LICENSE = (
    "Multimodality or semantic-mode explanation; calibration, accuracy, or a "
    "reportable distribution; source, source-family, or provenance analysis; "
    "corpus selection, exclusion, disposition, registration, activation, "
    "retagging, publication, or training authorization."
)
SPEC_CAVEAT = (
    "Register family is a confounded heuristic proxy; this inventory can only "
    "prompt a human hand-check."
)
SPEC_RENDERED_LICENSE = (
    "## What this result licenses\n"
    "\n"
    "**Task surface:** validation / labeled-corpus harness\n"
    "\n"
    "**Reports:** " + SPEC_LICENSES + "\n"
    "\n"
    "**Does NOT report:** " + SPEC_DOES_NOT_LICENSE + "\n"
    "\n"
    "### Caveats\n"
    "\n"
    "- " + SPEC_CAVEAT
)
SPEC_ASSUMPTIONS = {
    "purpose": "aggregate_hygiene_inventory_for_hand_check",
    "classifier_posture": "uncalibrated_heuristic",
    "register_role": "confounded_proxy",
    "reporting_status": "not_calibrated_or_reportable",
}
SPEC_DEFAULT_SCOPE_BYTES = (
    b'{"ai_status":null,"min_words":100,"persona_selected":false,'
    b'"scope_sha256":"sha256:90c35fd6716420e63521971c169aaa8f22ef627f'
    b'329e4be5d83ad1023368612d","split":null,"use":null}'
)
SPEC_REPORT_KEYS = (
    "schema_version",
    "tool",
    "version",
    "taxonomy",
    "projected_manifest_sha256",
    "scoped_rows_sha256",
    "document_plan_sha256",
    "h1_receipt_sha256",
    "classifier_sha256",
    "mapping_sha256",
    "refusal_contract_sha256",
    "checkpoint_binding_sha256",
    "scope",
    "limits",
    "counts",
    "declared_family_inventory",
    "classified_family_inventory",
    "declared_by_classified_family",
    "refusal_inventory",
    "match_inventory",
    "assumptions",
    "claim_license",
    "warnings",
)
SPEC_COUNT_KEYS = (
    "input_rows",
    "scoped_documents",
    "scoped_bytes",
    "scoped_words",
    "resolved_declared_documents",
    "resolved_declared_words",
    "unresolved_declared_documents",
    "unresolved_declared_words",
    "classified_documents",
    "classified_words",
    "refused_documents",
    "refused_words",
)

DIGEST_FIXTURES = {
    "projected_manifest_sha256": "sha256:" + "1" * 64,
    "scoped_rows_sha256": "sha256:" + "2" * 64,
    "document_plan_sha256": "sha256:" + "3" * 64,
    "h1_receipt_sha256": "sha256:" + "4" * 64,
    "classifier_sha256": "sha256:" + "5" * 64,
    "mapping_sha256": "sha256:" + "6" * 64,
    "refusal_contract_sha256": "sha256:" + "7" * 64,
    "checkpoint_binding_sha256": "sha256:" + "8" * 64,
}
SCOPE_DIGEST_FIXTURE = "sha256:" + "9" * 64


# The module-scoped ``domains`` fixture is defined once in the D1 section above.


def _scope(**overrides: object) -> dict:
    fields = {
        "use": None,
        "split": None,
        "ai_status": None,
        "min_words": 100,
        "persona_selected": False,
        "scope_sha256": SCOPE_DIGEST_FIXTURE,
    }
    fields.update(overrides)
    return rs.build_report_scope(**fields)  # type: ignore[arg-type]


def _aggregate(domains: rs.RegisterDomains, documents: list) -> dict:
    """Accumulate ``(declared, primary, refusal_reason, words, bytes)`` rows."""
    accumulator = rs.RegisterAggregate(domains)
    for declared, primary, reason, words, size in documents:
        accumulator.add_document(
            declared_family=declared,
            primary=primary,
            refusal_reason=reason,
            n_words=words,
            document_bytes=size,
        )
    return accumulator.snapshot()


def _report(
    domains: rs.RegisterDomains,
    aggregate: dict,
    *,
    input_rows: int | None = None,
    scope: dict | None = None,
) -> dict:
    if input_rows is None:
        input_rows = aggregate["counts"]["scoped_documents"]
    return rs.build_report(
        domains=domains,
        scope=_scope() if scope is None else scope,
        input_rows=input_rows,
        aggregate=aggregate,
        **DIGEST_FIXTURES,
    )


def _mixed(domains: rs.RegisterDomains) -> dict:
    first, second = domains.classified[0], domains.classified[1]
    return _aggregate(
        domains,
        [
            (first, first, None, 100, 600),
            (second, first, None, 200, 700),
            ("unknown", second, None, 300, 800),
            (first, "unknown", domains.refusals[0], 4, 20),
            ("unknown", "unknown", domains.refusals[1], 7, 30),
        ],
    )


# --------------------------------------------------------------------------
# Acceptance test 8 — fixed domains and equations
# --------------------------------------------------------------------------


def test_domains_pin_the_fixed_f_d_r_and_a_sets(
    binding: rs.H1Binding, domains: rs.RegisterDomains
) -> None:
    assert domains.classified == tuple(
        f for f in binding.namespace["REGISTER_FAMILIES"] if f != "unknown"
    )
    assert domains.declared == domains.classified + ("unknown",)
    assert domains.declared == tuple(binding.namespace["REGISTER_FAMILIES"])
    assert domains.refusals == tuple(
        binding.namespace["REGISTER_REFUSAL_REASONS"]
    )
    assert rs.MATCH_DOMAIN == ("same", "different", "unresolved")


@pytest.mark.parametrize(
    "declared, classified",
    [
        (("a", "b"), ("a", "b")),          # missing "unknown"
        (("a", "unknown"), ("a", "unknown")),  # "unknown" as a family
        (("a", "b", "unknown"), ("b", "a")),   # wrong order
        (("a", "a", "unknown"), ("a", "a")),   # duplicate
    ],
)
def test_domain_construction_rejects_a_broken_domain_pair(
    declared: tuple, classified: tuple
) -> None:
    with pytest.raises(rs.InternalError):
        rs.RegisterDomains(declared, classified, ("short_text",))


def test_empty_scope_emits_every_fixed_domain_zero_filled(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _aggregate(domains, [])
    rs.validate_aggregate(aggregate, domains=domains)
    assert set(aggregate) == set(rs.AGGREGATE_KEYS)
    assert set(aggregate["counts"]) == set(rs.AGGREGATE_COUNT_KEYS)
    assert all(value == 0 for value in aggregate["counts"].values())
    assert set(aggregate["declared_family_inventory"]) == set(domains.declared)
    assert set(aggregate["classified_family_inventory"]) == set(
        domains.classified
    )
    assert set(aggregate["refusal_inventory"]) == set(domains.refusals)
    assert set(aggregate["match_inventory"]) == set(rs.MATCH_DOMAIN)
    assert set(aggregate["declared_by_classified_family"]) == set(
        domains.declared
    )
    for row in aggregate["declared_by_classified_family"].values():
        assert set(row) == set(domains.classified)
        assert all(cell == {"documents": 0, "words": 0} for cell in row.values())
    report = _report(domains, aggregate, input_rows=0)
    assert report["counts"]["scoped_documents"] == 0
    assert report["counts"]["input_rows"] == 0


def test_unresolved_declared_family_lands_in_unknown_and_unresolved(
    domains: rs.RegisterDomains,
) -> None:
    family = domains.classified[0]
    aggregate = _aggregate(domains, [("unknown", family, None, 40, 90)])
    counts = aggregate["counts"]
    assert counts["unresolved_declared_documents"] == 1
    assert counts["unresolved_declared_words"] == 40
    assert counts["resolved_declared_documents"] == 0
    assert aggregate["declared_family_inventory"]["unknown"] == {
        "documents": 1,
        "words": 40,
    }
    assert aggregate["match_inventory"]["unresolved"] == {
        "documents": 1,
        "words": 40,
    }
    assert aggregate["classified_family_inventory"][family] == {
        "documents": 1,
        "words": 40,
    }
    assert aggregate["declared_by_classified_family"]["unknown"][family] == {
        "documents": 1,
        "words": 40,
    }
    rs.validate_aggregate(aggregate, domains=domains)


def test_each_refusal_reason_is_recorded_once_and_never_in_a_family_cell(
    domains: rs.RegisterDomains,
) -> None:
    declared = domains.classified[0]
    for reason in domains.refusals:
        aggregate = _aggregate(
            domains, [(declared, "unknown", reason, 3, 15)]
        )
        rs.validate_aggregate(aggregate, domains=domains)
        assert aggregate["refusal_inventory"][reason] == {
            "documents": 1,
            "words": 3,
        }
        assert sum(
            cell["documents"]
            for cell in aggregate["classified_family_inventory"].values()
        ) == 0
        assert sum(
            cell["documents"]
            for row in aggregate["declared_by_classified_family"].values()
            for cell in row.values()
        ) == 0
        assert aggregate["declared_family_inventory"][declared] == {
            "documents": 1,
            "words": 3,
        }
        assert aggregate["match_inventory"]["unresolved"] == {
            "documents": 1,
            "words": 3,
        }
        assert aggregate["counts"]["refused_documents"] == 1
        assert aggregate["counts"]["resolved_declared_documents"] == 1


def test_same_and_different_match_buckets(
    domains: rs.RegisterDomains,
) -> None:
    first, second = domains.classified[0], domains.classified[1]
    same = _aggregate(domains, [(first, first, None, 11, 50)])
    assert same["match_inventory"]["same"] == {"documents": 1, "words": 11}
    assert same["match_inventory"]["different"] == {"documents": 0, "words": 0}
    different = _aggregate(domains, [(first, second, None, 13, 60)])
    assert different["match_inventory"]["different"] == {
        "documents": 1,
        "words": 13,
    }
    assert different["match_inventory"]["same"] == {"documents": 0, "words": 0}
    for aggregate in (same, different):
        rs.validate_aggregate(aggregate, domains=domains)


def test_mixed_run_satisfies_every_marginal_and_conservation_equation(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _mixed(domains)
    rs.validate_aggregate(aggregate, domains=domains)
    counts = aggregate["counts"]
    declared = aggregate["declared_family_inventory"]
    classified = aggregate["classified_family_inventory"]
    crosstab = aggregate["declared_by_classified_family"]
    refusals = aggregate["refusal_inventory"]
    matches = aggregate["match_inventory"]

    for measure in ("documents", "words"):
        total = counts["scoped_" + ("documents" if measure == "documents" else "words")]
        assert sum(cell[measure] for cell in declared.values()) == total
        assert sum(cell[measure] for cell in matches.values()) == total
        classified_total = counts[
            "classified_documents" if measure == "documents" else "classified_words"
        ]
        refused_total = counts[
            "refused_documents" if measure == "documents" else "refused_words"
        ]
        assert sum(cell[measure] for cell in classified.values()) == classified_total
        assert (
            sum(
                cell[measure]
                for row in crosstab.values()
                for cell in row.values()
            )
            == classified_total
        )
        assert sum(cell[measure] for cell in refusals.values()) == refused_total
        assert classified_total + refused_total == total
        # Every column marginal.
        for family in domains.classified:
            assert (
                sum(crosstab[d][family][measure] for d in domains.declared)
                == classified[family][measure]
            )
        # Every row marginal: the crosstab row plus that declared family's
        # refused share is the declared inventory cell.
        for family in domains.declared:
            assert (
                sum(crosstab[family][p][measure] for p in domains.classified)
                <= declared[family][measure]
            )
        assert matches["same"][measure] == sum(
            crosstab[f][f][measure] for f in domains.classified
        )
        assert matches["different"][measure] == sum(
            crosstab[d][p][measure]
            for d in domains.classified
            for p in domains.classified
            if d != p
        )
        assert matches["unresolved"][measure] == (
            sum(crosstab["unknown"][p][measure] for p in domains.classified)
            + sum(cell[measure] for cell in refusals.values())
        )
        unresolved_declared = counts[
            "unresolved_declared_documents"
            if measure == "documents"
            else "unresolved_declared_words"
        ]
        resolved_declared = counts[
            "resolved_declared_documents"
            if measure == "documents"
            else "resolved_declared_words"
        ]
        assert unresolved_declared == declared["unknown"][measure]
        assert resolved_declared == total - declared["unknown"][measure]


def test_word_counts_come_only_from_the_validated_h1_evidence(
    binding: rs.H1Binding, domains: rs.RegisterDomains
) -> None:
    text = "\n\n".join(
        "This is a synthetic paragraph written to exercise the classifier "
        "seam with ordinary sentences. It carries no private prose."
        for _ in range(4)
    )
    result = binding.classify(text, min_words=10)
    accumulator = rs.RegisterAggregate(domains)
    accumulator.add_h1_result(
        declared_family="unknown",
        result=result,
        document_bytes=len(text.encode("utf-8")),
    )
    aggregate = accumulator.snapshot()
    assert aggregate["counts"]["scoped_words"] == result["evidence"]["n_words"]
    rs.validate_aggregate(aggregate, domains=domains)

    # H2 has no competing tokenizer: the count is read from the validated
    # evidence even when it disagrees with every text-derived count.
    hostile = copy.deepcopy(result)
    hostile["evidence"]["n_words"] = 4242
    second = rs.RegisterAggregate(domains)
    second.add_h1_result(
        declared_family="unknown", result=hostile, document_bytes=1
    )
    assert second.snapshot()["counts"]["scoped_words"] == 4242


def test_add_h1_result_refuses_a_result_outside_the_closed_key_set(
    domains: rs.RegisterDomains,
) -> None:
    accumulator = rs.RegisterAggregate(domains)
    for broken in ({}, {"primary": "unknown"}, {"evidence": {}}):
        with pytest.raises(rs.InternalError):
            accumulator.add_h1_result(
                declared_family="unknown", result=broken, document_bytes=1
            )


def test_shard_delta_is_framed_by_the_aggregate_delta_binding(
    domains: rs.RegisterDomains,
) -> None:
    accumulator = rs.RegisterAggregate(domains)
    accumulator.add_document(
        declared_family="first_person_essay",
        primary="unknown",
        refusal_reason="short_text",
        n_words=5,
        document_bytes=20,
    )
    delta = accumulator.shard_delta()
    assert set(delta) == {
        "counts",
        "declared_family_inventory",
        "classified_family_inventory",
        "declared_by_classified_family",
        "refusal_inventory",
        "match_inventory",
    }
    _, digest = rs.aggregate_delta_binding(delta)
    assert rs.prefixed(digest) == (
        "sha256:f34b82762a72814fd2968e6c0c8bb38404b71db8c8096c0c13b69c56"
        "bd7a820f"
    )


def test_shard_delta_refuses_more_than_two_hundred_fifty_documents(
    domains: rs.RegisterDomains,
) -> None:
    accumulator = rs.RegisterAggregate(domains)
    for _ in range(rs.SHARD_ROWS):
        accumulator.add_document(
            declared_family="unknown",
            primary="unknown",
            refusal_reason=domains.refusals[0],
            n_words=1,
            document_bytes=2,
        )
    accumulator.shard_delta()
    accumulator.add_document(
        declared_family="unknown",
        primary="unknown",
        refusal_reason=domains.refusals[0],
        n_words=1,
        document_bytes=2,
    )
    with pytest.raises(rs.InternalError):
        accumulator.shard_delta()


def test_whole_run_reassembly_adds_sealed_deltas_in_shard_order(
    domains: rs.RegisterDomains,
) -> None:
    first, second = domains.classified[0], domains.classified[1]
    shard_a = _aggregate(
        domains,
        [(first, first, None, 100, 600), (second, first, None, 200, 700)],
    )
    shard_b = _aggregate(
        domains,
        [
            ("unknown", second, None, 300, 800),
            (first, "unknown", domains.refusals[0], 4, 20),
            ("unknown", "unknown", domains.refusals[1], 7, 30),
        ],
    )
    whole = rs.reassemble_aggregate([shard_a, shard_b], domains=domains)
    assert whole == _mixed(domains)
    # Addition is over sealed deltas, so a single-shard run agrees too.
    assert rs.reassemble_aggregate([_mixed(domains)], domains=domains) == whole


@pytest.mark.parametrize(
    "target",
    [
        ("counts", "scoped_documents"),
        ("counts", "scoped_words"),
        ("counts", "classified_documents"),
        ("counts", "classified_words"),
        ("counts", "refused_documents"),
        ("counts", "refused_words"),
        ("counts", "resolved_declared_documents"),
        ("counts", "unresolved_declared_words"),
    ],
)
def test_a_mutated_count_breaks_an_equation(
    domains: rs.RegisterDomains, target: tuple
) -> None:
    aggregate = _mixed(domains)
    aggregate["counts"][target[1]] += 1
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


def test_every_inventory_cell_mutation_breaks_a_marginal(
    domains: rs.RegisterDomains,
) -> None:
    first = domains.classified[0]
    mutations = (
        ("declared_family_inventory", (first,)),
        ("declared_family_inventory", ("unknown",)),
        ("classified_family_inventory", (first,)),
        ("declared_by_classified_family", (first, first)),
        ("declared_by_classified_family", ("unknown", first)),
        ("refusal_inventory", (domains.refusals[0],)),
        ("match_inventory", ("same",)),
        ("match_inventory", ("different",)),
        ("match_inventory", ("unresolved",)),
    )
    for name, path in mutations:
        for measure in ("documents", "words"):
            aggregate = _mixed(domains)
            node = aggregate[name]
            for step in path:
                node = node[step]
            node[measure] += 1
            with pytest.raises(rs.InternalError):
                rs.validate_aggregate(aggregate, domains=domains)


def test_recording_a_refusal_in_a_family_cell_fails_before_publication(
    domains: rs.RegisterDomains,
) -> None:
    """A refusal must never reach a family cell or the crosstab."""
    first = domains.classified[0]
    aggregate = _aggregate(domains, [(first, "unknown", domains.refusals[0], 9, 40)])
    aggregate["classified_family_inventory"][first] = {"documents": 1, "words": 9}
    aggregate["declared_by_classified_family"][first][first] = {
        "documents": 1,
        "words": 9,
    }
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


def test_swapping_the_same_and_different_buckets_fails(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _mixed(domains)
    matches = aggregate["match_inventory"]
    matches["same"], matches["different"] = matches["different"], matches["same"]
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.pop("match_inventory"),
        lambda a: a.__setitem__("extra", {}),
        lambda a: a["counts"].pop("refused_words"),
        lambda a: a["counts"].__setitem__("rows", 1),
        lambda a: a["declared_family_inventory"].pop("unknown"),
        lambda a: a["classified_family_inventory"].__setitem__("unknown", {
            "documents": 0, "words": 0
        }),
        lambda a: a["refusal_inventory"].__setitem__("made_up", {
            "documents": 0, "words": 0
        }),
        lambda a: a["match_inventory"].pop("unresolved"),
        lambda a: a["declared_by_classified_family"]["unknown"].pop(
            next(iter(a["declared_by_classified_family"]["unknown"]))
        ),
        lambda a: a["match_inventory"]["same"].__setitem__("share", 0),
    ],
)
def test_aggregate_shape_drift_refuses(
    domains: rs.RegisterDomains, mutate
) -> None:
    aggregate = _mixed(domains)
    mutate(aggregate)
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


@pytest.mark.parametrize(
    "value", [True, False, 1.0, "1", None, -1, rs.INT64_MAX + 1]
)
def test_non_integer_or_out_of_range_cells_refuse(
    domains: rs.RegisterDomains, value: object
) -> None:
    aggregate = _aggregate(domains, [])
    aggregate["match_inventory"]["same"]["documents"] = value
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


@pytest.mark.parametrize(
    "declared, primary, reason",
    [
        ("not_a_family", "unknown", "short_text"),
        ("unknown", "not_a_family", None),
        ("unknown", "unknown", "not_a_reason"),
        ("unknown", "unknown", None),          # refusal biconditional
    ],
)
def test_add_document_refuses_out_of_domain_or_incoherent_rows(
    domains: rs.RegisterDomains, declared: str, primary: str, reason: object
) -> None:
    accumulator = rs.RegisterAggregate(domains)
    with pytest.raises(rs.InternalError):
        accumulator.add_document(
            declared_family=declared,
            primary=primary,
            refusal_reason=reason,  # type: ignore[arg-type]
            n_words=1,
            document_bytes=1,
        )


def test_add_document_refuses_a_classified_row_carrying_a_refusal_reason(
    domains: rs.RegisterDomains,
) -> None:
    accumulator = rs.RegisterAggregate(domains)
    with pytest.raises(rs.InternalError):
        accumulator.add_document(
            declared_family="unknown",
            primary=domains.classified[0],
            refusal_reason=domains.refusals[0],
            n_words=1,
            document_bytes=1,
        )


# --------------------------------------------------------------------------
# Acceptance test 9 — minimal inventory and report types
# --------------------------------------------------------------------------


def test_report_has_exactly_the_twenty_two_spec_keys(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))
    # The spec enumerates the closed report key set explicitly; the list has
    # 23 entries.
    assert len(SPEC_REPORT_KEYS) == 23
    assert tuple(report) == SPEC_REPORT_KEYS
    assert rs.REPORT_KEYS == SPEC_REPORT_KEYS
    assert report["schema_version"] == "setec-register-sweep-report/2"
    assert report["tool"] == "register_sweep"
    assert report["version"] == "2.0.0"
    assert report["taxonomy"] == "register_families/v2"
    assert report["warnings"] == []
    assert report["assumptions"] == SPEC_ASSUMPTIONS
    assert report["limits"] == rs.LIMITS
    assert set(report["counts"]) == set(SPEC_COUNT_KEYS)
    assert len(SPEC_COUNT_KEYS) == 12


def test_report_scope_is_the_exact_default_canonical_object() -> None:
    _, digest = rs.scope_binding(
        use=None, split=None, ai_status=None, persona=None, min_words=100
    )
    scope = rs.build_report_scope(
        use=None,
        split=None,
        ai_status=None,
        min_words=100,
        persona_selected=False,
        scope_sha256=rs.prefixed(digest),
    )
    assert _canonical(scope) == SPEC_DEFAULT_SCOPE_BYTES
    assert tuple(sorted(scope)) == rs.REPORT_SCOPE_KEYS


def test_report_scope_key_order_is_irrelevant_to_canonical_bytes() -> None:
    scope = _scope()
    shuffled = {key: scope[key] for key in reversed(list(scope))}
    assert _canonical(shuffled) == _canonical(scope)
    assert rs.validate_report_scope(shuffled) == scope


@pytest.mark.parametrize("key", rs.REPORT_SCOPE_KEYS)
def test_a_missing_scope_key_refuses(key: str) -> None:
    scope = _scope()
    scope.pop(key)
    with pytest.raises(rs.InternalError):
        rs.validate_report_scope(scope)


def test_an_extra_scope_key_refuses() -> None:
    scope = _scope()
    scope["persona"] = "x"
    with pytest.raises(rs.InternalError):
        rs.validate_report_scope(scope)


@pytest.mark.parametrize(
    "field, value",
    [
        ("use", "not_a_use"),
        ("use", ["baseline"]),
        ("split", "not_a_split"),
        ("ai_status", "not_a_status"),
        ("persona_selected", 0),
        ("persona_selected", 1),
        ("persona_selected", "false"),
        ("min_words", 0),
        ("min_words", 1_000_001),
        ("min_words", True),
        ("min_words", 100.0),
        ("scope_sha256", "90c35fd6" * 8),
        ("scope_sha256", "sha256:" + "A" * 64),
    ],
)
def test_non_domain_scope_values_refuse(field: str, value: object) -> None:
    with pytest.raises(rs.InternalError):
        _scope(**{field: value})


def test_validated_filter_values_appear_only_in_report_scope(
    domains: rs.RegisterDomains,
) -> None:
    scope = _scope(
        use="validation",
        split="holdout",
        ai_status="pre_ai_human",
        persona_selected=True,
    )
    report = _report(domains, _mixed(domains), scope=scope)
    assert report["scope"]["use"] == "validation"
    assert report["scope"]["persona_selected"] is True
    frozen, digest, envelope, envelope_bytes = rs.freeze_publication(
        report=report, domains=domains
    )
    for leaked in (b"holdout", b"pre_ai_human", b"persona"):
        assert leaked not in envelope_bytes
    assert b"holdout" in frozen  # only the private report carries the scope
    assert digest.startswith("sha256:")
    assert envelope["results"]["report_sha256"] == digest


@pytest.mark.parametrize("key", SPEC_REPORT_KEYS)
def test_a_missing_report_key_refuses(
    domains: rs.RegisterDomains, key: str
) -> None:
    report = _report(domains, _mixed(domains))
    report.pop(key)
    with pytest.raises(rs.InternalError):
        rs.validate_report_schema(report, domains=domains)


@pytest.mark.parametrize(
    "key",
    [
        "percentage",
        "ratio",
        "entropy",
        "effective_modes",
        "threshold",
        "band",
        "rank",
        "dominant_family",
        "mixture_flag",
        "rows",
        "family_share",
    ],
)
def test_an_extra_report_key_refuses_and_the_guard_agrees(
    domains: rs.RegisterDomains, key: str
) -> None:
    report = _report(domains, _mixed(domains))
    report[key] = 1
    with pytest.raises(rs.InternalError):
        rs.validate_report_schema(report, domains=domains)
    if rs.claim_key_is_refused(key):
        with pytest.raises(rs.InternalError):
            rs.assert_claim_posture(report)


def test_no_report_key_is_a_forbidden_atom_or_sequence(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                assert not rs.claim_key_is_refused(name), name
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(report)


@pytest.mark.parametrize(
    "value", [True, False, 1.0, "3", None, -1, rs.INT64_MAX + 1]
)
def test_hostile_count_values_refuse(
    domains: rs.RegisterDomains, value: object
) -> None:
    report = _report(domains, _mixed(domains))
    report["counts"]["scoped_bytes"] = value
    with pytest.raises(rs.InternalError):
        rs.validate_report_schema(report, domains=domains)


def test_scoped_documents_may_not_exceed_input_rows(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _mixed(domains)
    with pytest.raises(rs.InternalError):
        _report(domains, aggregate, input_rows=4)
    report = _report(domains, aggregate, input_rows=5)
    assert report["counts"]["input_rows"] == 5


@pytest.mark.parametrize("input_rows", [0, 5, 9_000])
def test_input_rows_equality_pins_the_projection_row_count(
    domains: rs.RegisterDomains, input_rows: int
) -> None:
    aggregate = _aggregate(domains, []) if input_rows == 0 else _mixed(domains)
    report = _report(domains, aggregate, input_rows=input_rows)
    assert report["counts"]["input_rows"] == input_rows


def test_scoped_document_and_byte_ceilings_are_enforced(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _aggregate(domains, [])
    aggregate["counts"]["scoped_bytes"] = rs.MAX_SCOPED_BYTES + 1
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(aggregate, domains=domains)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.__setitem__("limits", {}),
        lambda r: r["limits"].__setitem__("shard_rows", 251),
        lambda r: r.__setitem__("assumptions", {}),
        lambda r: r["assumptions"].__setitem__(
            "reporting_status", "calibrated_and_reportable"
        ),
        lambda r: r.__setitem__("warnings", ["a manifest warning"]),
        lambda r: r.__setitem__("schema_version", "setec-register-sweep-report/1"),
        lambda r: r.__setitem__("taxonomy", "register_families/v1"),
        lambda r: r.__setitem__("version", "2.0.1"),
        lambda r: r.__setitem__("classifier_sha256", "5" * 64),
        lambda r: r["claim_license"].__setitem__("fpr_target", 0.05),
        lambda r: r["claim_license"].__setitem__("additional_caveats", []),
    ],
)
def test_report_field_drift_refuses(
    domains: rs.RegisterDomains, mutate
) -> None:
    report = _report(domains, _mixed(domains))
    mutate(report)
    with pytest.raises(rs.InternalError):
        rs.validate_report_schema(report, domains=domains)


def test_report_pins_the_complete_fixed_claim_license(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))
    assert report["claim_license"] == {
        "task_surface": "validation",
        "licenses": SPEC_LICENSES,
        "does_not_license": SPEC_DOES_NOT_LICENSE,
        "comparison_set": {},
        "length_range_words": None,
        "register_match": [],
        "language_match": [],
        "fpr_target": None,
        "confidence_interval_95": None,
        "additional_caveats": [SPEC_CAVEAT],
        "references": [],
    }


def test_report_bytes_are_canonical_with_one_terminal_newline(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))
    frozen = rs.canonical_report_bytes(report, domains=domains)
    assert frozen.endswith(b"\n")
    assert not frozen.endswith(b"\n\n")
    assert frozen[:-1] == _canonical(report)
    assert b"  " not in frozen
    assert rs.artifact_sha256(frozen) == "sha256:" + hashlib.sha256(
        frozen
    ).hexdigest()
    # Two constructions of the same report are byte-identical.
    again = rs.canonical_report_bytes(_report(domains, _mixed(domains)), domains=domains)
    assert again == frozen


# --------------------------------------------------------------------------
# Acceptance test 17 — mechanical claim posture
# --------------------------------------------------------------------------


#: The guard's 20 forbidden key atoms, written out LITERALLY rather than splatted
#: from ``rs.FORBIDDEN_KEY_ATOMS``. Splatting the module's own table made the
#: parametrization self-referential: deleting an atom from the guard also deleted
#: its case, so the suite stayed green. ``test_the_literal_atom_table_mirrors_the
#: _guard`` is the paired equality check, so adding or removing an atom is a
#: deliberate two-sided edit.
LITERAL_FORBIDDEN_KEY_ATOMS = [
    "accuracy",
    "authorship",
    "band",
    "correctness",
    "dominant",
    "homogeneity",
    "label",
    "percent",
    "percentage",
    "probability",
    "proportion",
    "quality",
    "rank",
    "rate",
    "ratio",
    "score",
    "share",
    "threshold",
    "unimodality",
    "verdict",
]


def test_the_literal_atom_table_mirrors_the_guard() -> None:
    """The literal table above and the guard's own table must agree exactly."""
    assert len(LITERAL_FORBIDDEN_KEY_ATOMS) == 20
    assert len(set(LITERAL_FORBIDDEN_KEY_ATOMS)) == 20
    assert set(LITERAL_FORBIDDEN_KEY_ATOMS) == set(rs.FORBIDDEN_KEY_ATOMS)


def test_claim_posture_refuses_every_node_type_it_cannot_scan() -> None:
    """The walker dispatches on EXACT type (``type(x) is``), so a ``str``,
    ``dict``, or ``list`` subclass -- or any other object -- would otherwise
    fall through unscanned and smuggle refused claim text past the guard. The
    terminal branch refuses instead of silently skipping."""

    class SmuggledStr(str):
        pass

    class SmuggledDict(dict):
        pass

    class SmuggledList(list):
        pass

    class Opaque:
        def __str__(self) -> str:  # pragma: no cover - never reached
            return "verdict"

    for node in (
        SmuggledStr("register verdict: mixed"),
        SmuggledDict({"verdict": 1}),
        SmuggledList(["verdict"]),
        Opaque(),
        {1, 2},
        b"verdict",
    ):
        for artifact in (
            {"warnings": node},
            {"outer": {"inner": node}},
            {"outer": [{"inner": [node]}]},
        ):
            with pytest.raises(rs.InternalError):
                rs.assert_claim_posture(artifact)

    # The exact scannable types still pass.
    rs.assert_claim_posture(
        {"outer": ["ok", 1, 1.5, True, None, {"inner": ["ok"]}]}
    )


FORBIDDEN_KEY_CASES = [
    *LITERAL_FORBIDDEN_KEY_ATOMS,
    "final_verdict",
    "Verdict",
    "VERDICT",
    "family-score",
    "family.score",
    "family score",
    "  rank  ",
    "__band__",
    "ＳＣＯＲＥ",  # fullwidth SCORE, NFKC-folds to "score"
    "selection_decision",
    "row_disposition",
    "activation_decision",
    "training_decision",
    "is_ai",
    "is-ai",
    "is_ai_flag",
    "is_human",
    "source_group",
    "source_id",
    "source-id",
    "source_family",
    "declared_source_family_cell",
    "semantic_mode",
    "multimodality",
    "mixture_flag",
]


@pytest.mark.parametrize("key", FORBIDDEN_KEY_CASES)
def test_a_forbidden_key_is_refused_at_root_nested_and_list_depth(
    key: str,
) -> None:
    assert rs.claim_key_is_refused(key), key
    for artifact in (
        {key: 0},
        {"outer": {key: 0}},
        {"outer": [{"inner": [{key: 0}]}]},
    ):
        with pytest.raises(rs.InternalError):
            rs.assert_claim_posture(artifact)


@pytest.mark.parametrize(
    "key",
    [
        "scoped",
        "scoped_documents",
        "scoped_bytes",
        "scoreboard",
        "bandwidth",
        "ranking",
        "labelling",
        "declared_family_inventory",
        "declared_by_classified_family",
        "classifier_sha256",
        "claim_license",
        "claim_license_rendered",
        "refusal_inventory",
        "match_inventory",
        "sources",
        "source_bytes",
        "persona_selected",
        "ai_status",
    ],
)
def test_a_near_miss_key_passes(key: str) -> None:
    assert not rs.claim_key_is_refused(key), key
    rs.assert_claim_posture({key: 0})


def test_key_normalization_is_component_based() -> None:
    assert rs.normalize_claim_key("Final--Verdict!!") == ("final", "verdict")
    assert rs.normalize_claim_key("__scoped__") == ("scoped",)
    assert rs.normalize_claim_key("ＳＣＯＲＥ") == ("score",)
    assert rs.normalize_claim_key("") == ()
    assert rs.normalize_claim_key("!!!") == ()
    assert rs.normalize_claim_key("café_score") == ("caf", "score")


@pytest.mark.parametrize(
    "text",
    [
        "the dominant family",
        "register-family verdict",
        "a quality label",
        "source family",
        "source-family",
        "source_family",
        "source id",
        "source group",
        "semantic mode",
        "semantic-mode",
        "mixture flag",
        "is ai",
        "is-human",
        "selection decision",
        "activation-decision",
        "training_decision",
        "this corpus is human",
        "the inventory shows mixed prose",
        "this result proves the corpus safe",
        "it indicates the rows were selected",
        "exclude this corpus",
        "register the document",
        "train on data",
        "publish the row",
    ],
)
def test_forbidden_value_prose_is_refused(text: str) -> None:
    assert rs.claim_text_is_refused(text), text
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture({"note": text})
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture({"outer": {"warnings": [text]}})


@pytest.mark.parametrize(
    "text",
    [
        "scoped rows",
        "register family count inventory",
        "aggregate hygiene inventory for hand check",
        "uncalibrated heuristic",
        "confounded proxy",
        "not calibrated or reportable",
        rs.CLAIM_LICENSES,
        "register sweep refused invalid input",
        "register sweep refused by policy",
        "register sweep unavailable after internal failure",
    ],
)
def test_permitted_value_prose_passes(text: str) -> None:
    assert not rs.claim_text_is_refused(text), text


def test_the_dot_in_a_value_regex_does_not_cross_a_newline() -> None:
    # "is" and "human" are 40 characters apart but on different lines, so the
    # bounded-gap regex must not join them.
    assert not rs.claim_text_is_refused("this is a proxy\nfor a human check")
    assert rs.claim_text_is_refused("this is a proxy for a human check")


def test_the_three_exemptions_apply_only_to_the_frozen_license(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))
    rs.assert_claim_posture(report)
    _, _, envelope, _ = rs.freeze_publication(report=report, domains=domains)
    rs.assert_claim_posture(envelope)
    assert envelope["claim_license_rendered"] == SPEC_RENDERED_LICENSE
    assert rs.CLAIM_EXEMPT_LICENSE_PATHS == (
        ("claim_license", "does_not_license"),
        ("claim_license", "additional_caveats", 0),
    )
    assert rs.CLAIM_EXEMPT_RENDER_PATH == ("claim_license_rendered",)
    # The exempt leaves really are prose the guard would otherwise refuse.
    assert rs.claim_text_is_refused(SPEC_DOES_NOT_LICENSE)
    assert rs.claim_text_is_refused(SPEC_CAVEAT)
    # ...and the positive leaf is NOT exempt; it passes on its own merits.
    assert not rs.claim_text_is_refused(SPEC_LICENSES)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e["claim_license"].__setitem__(
            "licenses", rs.CLAIM_LICENSES + " "
        ),
        lambda e: e["claim_license"].__setitem__(
            "does_not_license", rs.CLAIM_DOES_NOT_LICENSE + "."
        ),
        lambda e: e["claim_license"].__setitem__("references", ["x"]),
        lambda e: e["claim_license"].__setitem__("task_surface", "validation "),
        lambda e: e["claim_license"].__setitem__(
            "additional_caveats", [rs.CLAIM_CAVEAT, rs.CLAIM_CAVEAT]
        ),
    ],
)
def test_one_byte_off_revokes_the_license_exemptions(mutate) -> None:
    envelope = rs.build_success_envelope(
        report_sha256="sha256:" + "a" * 64,
        counts={key: 0 for key in rs.REPORT_COUNT_KEYS},
    )
    rs.assert_claim_posture(envelope)
    mutate(envelope)
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture(envelope)


def test_one_byte_off_revokes_the_rendered_exemption() -> None:
    envelope = rs.build_success_envelope(
        report_sha256="sha256:" + "a" * 64,
        counts={key: 0 for key in rs.REPORT_COUNT_KEYS},
    )
    envelope["claim_license_rendered"] = SPEC_RENDERED_LICENSE + "\n"
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture(envelope)


def test_no_other_leaf_inherits_a_license_exemption() -> None:
    envelope = rs.build_success_envelope(
        report_sha256="sha256:" + "a" * 64,
        counts={key: 0 for key in rs.REPORT_COUNT_KEYS},
    )
    for path, value in (
        ("warnings", [SPEC_DOES_NOT_LICENSE]),
        ("ai_status", SPEC_CAVEAT),
    ):
        hostile = json.loads(json.dumps(envelope))
        hostile[path] = value
        with pytest.raises(rs.InternalError):
            rs.assert_claim_posture(hostile)
    nested = json.loads(json.dumps(envelope))
    nested["results"]["note"] = SPEC_RENDERED_LICENSE
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture(nested)
    moved = json.loads(json.dumps(envelope))
    moved["claim_license"]["comparison_set"] = {
        "copy": moved["claim_license"]["does_not_license"]
    }
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture(moved)


def test_the_guard_refuses_a_non_string_mapping_key() -> None:
    with pytest.raises(rs.InternalError):
        rs.assert_claim_posture({1: "ok"})


# --------------------------------------------------------------------------
# Acceptance tests 15/16 — canonical goldens, errors, progress, and the sink
# --------------------------------------------------------------------------


def test_success_envelope_matches_its_canonical_golden(
    domains: rs.RegisterDomains,
) -> None:
    report = _report(domains, _mixed(domains))
    frozen, digest, envelope, envelope_bytes = rs.freeze_publication(
        report=report, domains=domains
    )
    expected = {
        "schema_version": "1.0",
        "task_surface": "validation",
        "tool": "register_sweep",
        "version": "2.0.0",
        "available": True,
        "target": {"path": None, "words": report["counts"]["scoped_words"]},
        "baseline": None,
        "results": {
            "report_sha256": digest,
            "report_schema_version": "setec-register-sweep-report/2",
            "taxonomy": "register_families/v2",
            "counts": report["counts"],
        },
        "claim_license": {
            "task_surface": "validation",
            "licenses": SPEC_LICENSES,
            "does_not_license": SPEC_DOES_NOT_LICENSE,
            "comparison_set": {},
            "length_range_words": None,
            "register_match": [],
            "language_match": [],
            "fpr_target": None,
            "confidence_interval_95": None,
            "additional_caveats": [SPEC_CAVEAT],
            "references": [],
        },
        "claim_license_rendered": SPEC_RENDERED_LICENSE,
        "warnings": [],
        "ai_status": None,
    }
    assert envelope == expected
    assert envelope_bytes == _canonical(expected) + b"\n"
    assert envelope_bytes.count(b"\n") == 1
    assert envelope["target"]["words"] == envelope["results"]["counts"][
        "scoped_words"
    ]
    assert digest == rs.artifact_sha256(frozen)
    # No family cell, path, or free-text metadata reaches the envelope.
    for family in domains.classified:
        assert family.encode("utf-8") not in envelope_bytes
    assert b"declared_family_inventory" not in envelope_bytes


@pytest.mark.parametrize(
    "failure, exit_code, category, reason",
    [
        (rs.BadInput, 2, "bad_input", "register composition sweep refused invalid input"),
        (rs.PolicyRefused, 3, "policy_refused", "register composition sweep refused by policy"),
        (
            rs.InternalError,
            4,
            "internal_error",
            "register composition sweep unavailable after internal failure",
        ),
    ],
)
def test_each_controlled_error_envelope_matches_its_golden(
    failure: type, exit_code: int, category: str, reason: str
) -> None:
    frozen, code = rs.freeze_controlled_error(failure)
    assert code == exit_code
    expected = (
        b'{"ai_status":null,"available":false,"baseline":null,'
        b'"claim_license":null,"claim_license_rendered":null,'
        b'"reason":"' + reason.encode("ascii") + b'",'
        b'"reason_category":"' + category.encode("ascii") + b'",'
        b'"results":{},"schema_version":"1.0",'
        b'"target":{"path":null,"words":0},"task_surface":"validation",'
        b'"tool":"register_sweep","version":"2.0.0","warnings":[]}\n'
    )
    assert frozen == expected
    # An instance maps to exactly the same golden as its class.
    assert rs.freeze_controlled_error(failure()) == (frozen, exit_code)


def test_controlled_failure_mapping_never_converts_a_base_exception() -> None:
    assert rs.controlled_failure_class(rs.BadInput()) is rs.BadInput
    assert rs.controlled_failure_class(rs.PolicyRefused()) is rs.PolicyRefused
    assert rs.controlled_failure_class(rs.InternalError()) is rs.InternalError
    assert rs.controlled_failure_class(ValueError("x")) is rs.InternalError
    assert rs.controlled_failure_class(rs.SweepRefusal()) is rs.InternalError
    with pytest.raises(KeyboardInterrupt):
        rs.controlled_failure_class(KeyboardInterrupt())
    with pytest.raises(SystemExit):
        rs.controlled_failure_class(SystemExit(1))


def test_error_envelopes_disclose_no_caught_exception_text() -> None:
    frozen, _ = rs.freeze_controlled_error(rs.BadInput("/private/path/secret.jsonl"))
    assert b"secret" not in frozen
    assert b"/private" not in frozen


@pytest.mark.parametrize(
    "total, expected",
    [
        (0, ()),
        (1, ()),
        (99, ()),
        (100, ()),
        (101, (100,)),
        (200, (100,)),
    ],
)
def test_progress_cadence_goldens(total: int, expected: tuple) -> None:
    assert rs.progress_ordinals(total) == expected
    for completed in expected:
        assert rs.progress_is_eligible(completed, total)
        assert rs.progress_line(completed, total) == (
            "register sweep progress: completed=%d total=%d\n" % (completed, total)
        )
    assert not rs.progress_is_eligible(total, total)  # never at K == N
    assert rs.processing_complete_line(total) == (
        "register sweep processing-complete: completed=%d total=%d "
        "report_commit=pending\n" % (total, total)
    )


@pytest.mark.parametrize(
    "resume_from, expected",
    [
        (0, (100, 200, 300, 400, 500)),
        (50, (100, 200, 300, 400, 500)),
        (100, (200, 300, 400, 500)),
        (150, (200, 300, 400, 500)),
    ],
)
def test_resume_replays_no_earlier_progress(
    resume_from: int, expected: tuple
) -> None:
    assert rs.progress_ordinals(501, resume_from=resume_from) == expected
    assert not rs.progress_is_eligible(100, 501, resume_from=100)
    for completed in expected:
        assert rs.progress_is_eligible(completed, 501, resume_from=resume_from)


def test_progress_lines_are_ascii_with_no_extra_metadata() -> None:
    line = rs.progress_line(300, 1000)
    assert line.isascii() and line.endswith("\n") and line.count("\n") == 1
    assert "  " not in line and "+" not in line and "0300" not in line
    complete = rs.processing_complete_line(0)
    assert complete == (
        "register sweep processing-complete: completed=0 total=0 "
        "report_commit=pending\n"
    )


@pytest.mark.parametrize("completed, total", [(0, 10), (100, 100), (250, 100)])
def test_progress_line_refuses_an_ineligible_ordinal(
    completed: int, total: int
) -> None:
    with pytest.raises(rs.InternalError):
        rs.progress_line(completed, total)


class _BrokenStream:
    """A stdout stand-in that fails every delivery attempt."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.attempts = 0

    def write(self, _data: object) -> int:
        self.attempts += 1
        raise self.error

    def flush(self) -> None:
        raise self.error


class _ExhaustedStream:
    """A stdout stand-in whose writes stop making progress."""

    def __init__(self) -> None:
        self.written = b""

    def write(self, data: memoryview) -> int:
        if not self.written:
            self.written = bytes(data[:1])
            return 1
        return 0

    def flush(self) -> None:
        return None


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(32, "broken pipe"), OSError(5, "io error"), ValueError("closed")],
)
def test_the_committed_success_sink_absorbs_a_broken_stdout(
    error: BaseException, capsys
) -> None:
    stream = _BrokenStream(error)
    assert rs.emit_committed_success(b'{"ok":true}\n', stream=stream) is True
    assert stream.attempts == 1
    captured = capsys.readouterr()
    assert captured.err == ""


def test_the_committed_success_sink_absorbs_partial_write_exhaustion(
    capsys,
) -> None:
    stream = _ExhaustedStream()
    assert rs.emit_committed_success(b'{"ok":true}\n', stream=stream) is True
    assert stream.written == b"{"
    assert capsys.readouterr().err == ""


def test_the_committed_success_sink_delivers_the_frozen_bytes(
    domains: rs.RegisterDomains, capsysbinary
) -> None:
    report = _report(domains, _mixed(domains))
    _, _, _, envelope_bytes = rs.freeze_publication(report=report, domains=domains)

    class _Sink:
        def __init__(self) -> None:
            self.buffer = bytearray()

        def write(self, data: memoryview) -> int:
            chunk = bytes(data)[:7]
            self.buffer.extend(chunk)
            return len(chunk)

        def flush(self) -> None:
            return None

    sink = _Sink()
    assert rs.emit_committed_success(envelope_bytes, stream=sink) is True
    assert bytes(sink.buffer) == envelope_bytes
    assert capsysbinary.readouterr().err == b""


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


def test_compute_aggregate_delta_enforces_its_own_shard_row_ceiling(
    domains: rs.RegisterDomains,
) -> None:
    """``compute_aggregate_delta``'s own ``> SHARD_ROWS`` guard, pinned at the
    boundary and attributed to the ceiling rather than to row contents.

    Exactly ``SHARD_ROWS`` valid rows must build a delta; one more must refuse.
    The function is exercised DIRECTLY here -- not through ``publish_shard`` --
    because it is a public seam an independent accumulator also calls.
    """
    at_ceiling = _synthetic_rows(rs.SHARD_ROWS, domains)
    assert len(at_ceiling) == 250
    delta = rs.compute_aggregate_delta(at_ceiling, domains)
    assert delta["counts"]["scoped_documents"] == 250

    over_ceiling = _synthetic_rows(rs.SHARD_ROWS + 1, domains)
    assert len(over_ceiling) == 251
    # Every row is individually valid, so the ceiling is the only refusal site.
    for row in over_ceiling:
        rs.compute_aggregate_delta([row], domains)
    with pytest.raises(rs.InternalError):
        rs.compute_aggregate_delta(over_ceiling, domains)


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
    # ONE fingerprint producer: the replan seam derives through the same
    # ``bind_regular`` the frozen document plan uses, and the retired second
    # producer is gone from the io module's surface.
    assert secure_io.bind_regular([document])[2] == fingerprint
    assert not hasattr(secure_io, "planned_fingerprint")
    assert "planned_fingerprint" not in secure_io.__all__


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


# ---- Increment D2: CLI, runner, registration ----
#
# Acceptance tests 3 (CLI), 15 (privacy and errors, through the CLI), 16 (exact
# output, progress goldens, fresh-vs-resume byte identity), the runtime half of
# 1 (receipt/classifier identity refuses before any document read; no network),
# and 18 (registration and gates).
#
# All fixtures are generated synthetic data. No private corpus, aggregate,
# identifier, path, or prose enters the repository.

import io
import shutil
import subprocess

REPO_ROOT = SCRIPTS.parents[2]

#: The three canonical golden error envelopes, byte-for-byte. These are the
#: complete stdout of a controlled refusal: one object, no usage text, no
#: traceback, no path, no digest, no filter value, no validator or caught
#: exception text.
GOLDEN_BAD_INPUT = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep refused'
    b' invalid input","reason_category":"bad_input","results":{},'
    b'"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)
GOLDEN_POLICY_REFUSED = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep refused'
    b' by policy","reason_category":"policy_refused","results":{},'
    b'"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)
GOLDEN_INTERNAL_ERROR = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep '
    b'unavailable after internal failure","reason_category":"internal_error",'
    b'"results":{},"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)

#: The frozen success ClaimLicense, written out here rather than read from the
#: module, so a one-byte change to the licensing posture fails this test.
GOLDEN_CLAIM_LICENSE = {
    "additional_caveats": [
        "Register family is a confounded heuristic proxy; this inventory can "
        "only prompt a human hand-check."
    ],
    "comparison_set": {},
    "confidence_interval_95": None,
    "does_not_license": (
        "Multimodality or semantic-mode explanation; calibration, accuracy, or "
        "a reportable distribution; source, source-family, or provenance "
        "analysis; corpus selection, exclusion, disposition, registration, "
        "activation, retagging, publication, or training authorization."
    ),
    "fpr_target": None,
    "language_match": [],
    "length_range_words": None,
    "licenses": (
        "Aggregate register-family count inventory for a hand-check of the "
        "explicitly scoped manifest slice."
    ),
    "references": [],
    "register_match": [],
    "task_surface": "validation",
}


class _D2Sink:
    """A minimal binary sink that records exactly what the runner wrote."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, data: Any) -> int:
        return self.buffer.write(data)

    def flush(self) -> None:
        return None

    @property
    def bytes(self) -> bytes:
        return self.buffer.getvalue()


class _D2BrokenSink:
    """A consumer that is already gone: every write raises."""

    def __init__(self) -> None:
        self.attempts = 0

    def write(self, data: Any) -> int:
        self.attempts += 1
        raise BrokenPipeError("closed consumer")

    def flush(self) -> None:
        raise BrokenPipeError("closed consumer")


def _d2_corpus(
    root: Path,
    count: int,
    *,
    body: str = "tiny synthetic document",
    use: list[str] | None = None,
    ai_status: str = "pre_ai_human",
    register: str | None = None,
    split: str | None = None,
    persona: str | None = None,
    extra_rows: list[dict[str, Any]] | None = None,
    corpus_name: str = "corpus",
    manifest_name: str = "manifest.jsonl",
) -> Path:
    """Write ``count`` tiny synthetic documents plus their manifest."""
    corpus = root / corpus_name
    corpus.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        name = f"doc{index:05d}.txt"
        (corpus / name).write_text(f"{body} {index}\n", encoding="utf-8")
        row: dict[str, Any] = {
            "path": f"{corpus_name}/{name}",
            "use": list(use) if use is not None else ["baseline"],
            "ai_status": ai_status,
        }
        if register is not None:
            row["register"] = register
        if split is not None:
            row["split"] = split
        if persona is not None:
            row["persona"] = persona
        rows.append(row)
    rows.extend(extra_rows or [])
    manifest = root / manifest_name
    manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return manifest


def _d2_args(root: Path, manifest: Path, *extra: str) -> list[str]:
    return [
        "--manifest", str(manifest),
        "--report-out", str(root / "report.json"),
        "--checkpoint-dir", str(root / "state"),
        *extra,
    ]


def _d2_out_args(manifest: Path, out_dir: Path, *extra: str) -> list[str]:
    """Args for a run whose outputs live under ``out_dir`` but whose corpus
    identity (and therefore every H2 digest) is the shared ``manifest``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        "--manifest", str(manifest),
        "--report-out", str(out_dir / "report.json"),
        "--checkpoint-dir", str(out_dir / "state"),
        *extra,
    ]


def _d2_run(argv: list[str]) -> tuple[int, bytes, bytes]:
    """Drive ``main()`` with recording binary streams."""
    out = _D2Sink()
    err = _D2Sink()
    code = rs.main(argv, stdout=out, stderr=err)
    return code, out.bytes, err.bytes


def _d2_sweep(root: Path, count: int, *extra: str, **corpus: Any) -> tuple[int, bytes, bytes]:
    manifest = _d2_corpus(root, count, **corpus)
    return _d2_run(_d2_args(root, manifest, *extra))


# --------------------------------------------------------------------------
# Acceptance test 3: the closed CLI grammar
# --------------------------------------------------------------------------


def test_cli_grammar_is_the_closed_option_set() -> None:
    assert rs.CLI_REQUIRED_OPTIONS == (
        "--manifest", "--report-out", "--checkpoint-dir",
    )
    assert rs.CLI_FLAG_OPTIONS == ("--resume",)
    assert rs.CLI_VALUE_OPTIONS == (
        "--manifest", "--report-out", "--checkpoint-dir",
        "--use", "--split", "--persona", "--ai-status", "--min-words",
    )
    # There is no source-related option anywhere in the grammar.
    for option in rs.CLI_OPTIONS:
        assert "source" not in option and "group" not in option
    assert rs.MIN_WORDS_DEFAULT == 100


def test_cli_accepts_every_allowed_filter_value(tmp_path: Path) -> None:
    """Acceptance 3: every allowed `--use`/`--split`/`--ai-status` member passes
    exact-membership validation."""
    base = ["--manifest", "m", "--report-out", "r", "--checkpoint-dir", "s"]
    for value in sorted(mv.ALLOWED_USE):
        assert rs.parse_arguments(base + ["--use", value]).use == value
    for value in sorted(mv.ALLOWED_SPLIT):
        assert rs.parse_arguments(base + ["--split", value]).split == value
    for value in sorted(mv.ALLOWED_AI_STATUS):
        assert rs.parse_arguments(base + ["--ai-status", value]).ai_status == value


def test_cli_every_allowed_use_value_runs_end_to_end(tmp_path: Path) -> None:
    """Acceptance 3, through `main()`: a run under each allowed `--use` value
    commits a report whose scope records exactly that value."""
    for index, value in enumerate(sorted(mv.ALLOWED_USE)):
        root = tmp_path / f"use{index}"
        root.mkdir()
        code, out, _err = _d2_sweep(root, 2, "--use", value, use=[value])
        assert code == 0, value
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        assert report["scope"]["use"] == value
        assert report["counts"]["scoped_documents"] == 2
        assert out.endswith(b"}\n")


def test_cli_every_allowed_split_and_ai_status_value_runs_end_to_end(
    tmp_path: Path,
) -> None:
    for index, value in enumerate(sorted(mv.ALLOWED_SPLIT)):
        root = tmp_path / f"split{index}"
        root.mkdir()
        code, _out, _err = _d2_sweep(root, 1, "--split", value, split=value)
        assert code == 0, value
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        assert report["scope"]["split"] == value and report["scope"]["use"] is None
    for index, value in enumerate(sorted(mv.ALLOWED_AI_STATUS)):
        root = tmp_path / f"status{index}"
        root.mkdir()
        code, _out, _err = _d2_sweep(root, 1, "--ai-status", value, ai_status=value)
        assert code == 0, value
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        assert report["scope"]["ai_status"] == value


@pytest.mark.parametrize(
    "extra",
    [
        # repeated options (including a repeated required option)
        ["--use", "baseline", "--use", "baseline"],
        ["--use", "baseline", "--use", "train"],
        ["--resume", "--resume"],
        ["--min-words", "100", "--min-words", "200"],
        # unknown options, including every former grouping spelling
        ["--group-by-source"],
        ["--group-by-source-family"],
        ["--source-family", "x"],
        ["--source-id", "x"],
        ["--source", "x"],
        ["--by-source"],
        ["--group", "x"],
        ["--verbose"],
        ["--help"],
        ["-h"],
        # malformed spellings: `=` form, single dash, bare double dash,
        # positional, abbreviation, missing value, option-shaped value
        ["--use=baseline"],
        ["-u", "baseline"],
        ["--"],
        ["extra-positional"],
        ["--us", "baseline"],
        ["--use"],
        ["--use", "--split"],
        # out-of-domain enum values
        ["--use", "not_a_use"],
        ["--use", "Baseline"],
        ["--use", ""],
        ["--split", "not_a_split"],
        ["--ai-status", "not_a_status"],
        # out-of-range / non-canonical integers
        ["--min-words", "0"],
        ["--min-words", "1000001"],
        ["--min-words", "-1"],
        ["--min-words", "+5"],
        ["--min-words", "0100"],
        ["--min-words", "1_0"],
        ["--min-words", "1.0"],
        ["--min-words", "true"],
        ["--min-words", " 100"],
        ["--min-words", "100 "],
        ["--min-words", "１００"],
        ["--min-words", ""],
        # persona domain
        ["--persona", " josh"],
        ["--persona", "josh "],
        ["--persona", "a" * 129],
        ["--persona", "jósh"],
        ["--persona", "jo‮sh"],
        ["--persona", "jo\tsh"],
        ["--persona", ""],
    ],
)
def test_cli_refuses_before_any_output_creation(
    tmp_path: Path, extra: list[str]
) -> None:
    """Acceptance 3: a bad option produces exactly the one `bad_input` golden,
    exit 2, and neither output path exists afterwards."""
    manifest = _d2_corpus(tmp_path, 1)
    report = tmp_path / "report.json"
    state = tmp_path / "state"
    code, out, err = _d2_run(_d2_args(tmp_path, manifest, *extra))
    assert code == 2
    assert out == GOLDEN_BAD_INPUT
    assert err == b""
    assert not report.exists()
    assert not state.exists()


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--manifest", "m"],
        ["--manifest", "m", "--report-out", "r"],
        ["--report-out", "r", "--checkpoint-dir", "s"],
        ["--manifest", "m", "--checkpoint-dir", "s"],
    ],
)
def test_cli_missing_required_option_refuses(argv: list[str]) -> None:
    code, out, err = _d2_run(argv)
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")


def test_cli_min_words_default_and_bounds() -> None:
    base = ["--manifest", "m", "--report-out", "r", "--checkpoint-dir", "s"]
    assert rs.parse_arguments(base).min_words == 100
    assert rs.parse_arguments(base + ["--min-words", "1"]).min_words == 1
    assert rs.parse_arguments(base + ["--min-words", "1000000"]).min_words == 1000000
    assert rs.parse_arguments(base).persona is None
    assert rs.parse_arguments(base).resume is False
    assert rs.parse_arguments(base + ["--resume"]).resume is True


def test_cli_persona_domain_matches_the_projection_seam() -> None:
    """The CLI persona filter and the projected `persona` field share one
    domain; the two predicates live on opposite sides of the one-way import
    boundary, so they are pinned against one table of hostile values."""
    table = [
        "josh", "j", "a" * 128, "josh smith", "josé", "中文",
        "", " ", " josh", "josh ", "a" * 129, "jósh", "jo\x00sh",
        "jo\x01sh", "jo\x7fsh", "jo\x9fsh", "jo‮sh", "jo‏sh",
        "jo\nsh", "jo\tsh",
    ]
    for value in table:
        assert rs.valid_h2_string(value, max_bytes=rs.MAX_PERSONA_BYTES) == (
            mv._valid_h2_string(value, max_bytes=rs.MAX_PERSONA_BYTES)
        ), value
    for value in (None, 1, True, b"josh", ["josh"]):
        assert rs.valid_h2_string(value, max_bytes=rs.MAX_PERSONA_BYTES) is False


def test_cli_options_never_render_the_raw_persona() -> None:
    """The validated raw persona reaches only the private scope binding; the
    options object refuses to render it."""
    options = rs.parse_arguments(
        ["--manifest", "m", "--report-out", "r", "--checkpoint-dir", "s",
         "--persona", "sentinelpersonavalue"]
    )
    assert options.persona == "sentinelpersonavalue"
    assert options.persona_selected is True
    assert "sentinelpersonavalue" not in repr(options)
    assert repr(options) == "<register sweep options>"


def test_omitted_filters_include_every_admissible_row(tmp_path: Path) -> None:
    """Acceptance 3: omitted filters include every H2-admissible row; there are
    no implicit corpus-role defaults."""
    manifest = _d2_corpus(
        tmp_path, 3, use=["baseline", "idiolect"], split="holdout",
        register="personal", persona="alias-a", ai_status="ai_edited",
    )
    code, _out, _err = _d2_run(_d2_args(tmp_path, manifest))
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["input_rows"] == 3
    assert report["counts"]["scoped_documents"] == 3
    assert report["scope"] == {
        "ai_status": None,
        "min_words": 100,
        "persona_selected": False,
        "scope_sha256": report["scope"]["scope_sha256"],
        "split": None,
        "use": None,
    }


def test_filters_select_the_expected_subset(tmp_path: Path) -> None:
    """`use` is list membership; `split`, `ai_status`, and `persona` are exact
    equality."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rows = []
    spec = [
        (["baseline", "idiolect"], "holdout", "ai_edited", "alpha"),
        (["baseline"], "train", "ai_edited", "alpha"),
        (["idiolect"], "holdout", "pre_ai_human", "beta"),
        (["idiolect"], "holdout", "ai_edited", None),
    ]
    for index, (use, split, ai_status, persona) in enumerate(spec):
        (corpus / f"doc{index}.txt").write_text(f"body {index}\n", encoding="utf-8")
        row: dict[str, Any] = {
            "path": f"corpus/doc{index}.txt", "use": use,
            "ai_status": ai_status, "split": split,
        }
        if persona is not None:
            row["persona"] = persona
        rows.append(row)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )

    cases = [
        (["--use", "idiolect"], 3),
        (["--use", "baseline"], 2),
        (["--split", "holdout"], 3),
        (["--ai-status", "ai_edited"], 3),
        (["--persona", "alpha"], 2),
        (["--use", "idiolect", "--split", "holdout", "--ai-status", "ai_edited"], 2),
        (["--persona", "gamma"], 0),
    ]
    for index, (extra, expected) in enumerate(cases):
        run_root = tmp_path / f"run{index}"
        run_root.mkdir()
        code, _out, _err = _d2_run(
            ["--manifest", str(manifest),
             "--report-out", str(run_root / "report.json"),
             "--checkpoint-dir", str(run_root / "state"), *extra]
        )
        assert code == 0, extra
        report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
        assert report["counts"]["input_rows"] == 4
        assert report["counts"]["scoped_documents"] == expected, extra


def test_empty_scope_commits_a_zero_inventory_and_creates_no_shard(
    tmp_path: Path,
) -> None:
    manifest = _d2_corpus(tmp_path, 3)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest, "--use", "exclude"))
    assert code == 0
    assert err == b"register sweep processing-complete: completed=0 total=0 report_commit=pending\n"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["input_rows"] == 3
    assert report["counts"]["scoped_documents"] == 0
    assert report["counts"]["scoped_words"] == 0
    assert list((tmp_path / "state").iterdir()) == []
    for name in rs.INVENTORY_KEYS:
        for cell in _d2_cells(report[name]):
            assert cell == {"documents": 0, "words": 0}
    assert json.loads(out.decode("utf-8"))["results"]["counts"] == report["counts"]


def _d2_cells(inventory: Any) -> list[dict[str, int]]:
    cells: list[dict[str, int]] = []
    for value in inventory.values():
        if set(value) == {"documents", "words"}:
            cells.append(value)
        else:
            cells.extend(value.values())
    return cells


# --------------------------------------------------------------------------
# Acceptance test 16: exact output, determinism, and progress goldens
# --------------------------------------------------------------------------


def test_two_fresh_runs_are_byte_identical(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 7, register="personal", split="baseline")
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    first_root.mkdir()
    second_root.mkdir()
    results = []
    for root in (first_root, second_root):
        code, out, err = _d2_run(
            ["--manifest", str(manifest),
             "--report-out", str(root / "report.json"),
             "--checkpoint-dir", str(root / "state")]
        )
        assert code == 0
        results.append(((root / "report.json").read_bytes(), out, err))
    assert results[0][0] == results[1][0]
    assert results[0][1] == results[1][1]
    assert results[0][2] == results[1][2]
    digest = hashlib.sha256(results[0][0]).hexdigest()
    envelope = json.loads(results[0][1].decode("utf-8"))
    assert envelope["results"]["report_sha256"] == f"sha256:{digest}"


def test_success_envelope_is_the_canonical_golden(tmp_path: Path) -> None:
    """Acceptance 16: the full canonical success envelope, with explicit null
    and empty defaults and the complete frozen ClaimLicense rendering."""
    manifest = _d2_corpus(tmp_path, 2, register="personal")
    code, out, _err = _d2_run(_d2_args(tmp_path, manifest, "--persona", "unused"))
    assert code == 0
    report_bytes = (tmp_path / "report.json").read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    expected = {
        "ai_status": None,
        "available": True,
        "baseline": None,
        "claim_license": GOLDEN_CLAIM_LICENSE,
        "claim_license_rendered": (
            "## What this result licenses\n\n"
            "**Task surface:** validation / labeled-corpus harness\n\n"
            "**Reports:** " + GOLDEN_CLAIM_LICENSE["licenses"] + "\n\n"
            "**Does NOT report:** " + GOLDEN_CLAIM_LICENSE["does_not_license"]
            + "\n\n### Caveats\n\n- "
            + GOLDEN_CLAIM_LICENSE["additional_caveats"][0]
        ),
        "results": {
            "counts": report["counts"],
            "report_schema_version": "setec-register-sweep-report/2",
            "report_sha256": "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
            "taxonomy": "register_families/v2",
        },
        "schema_version": "1.0",
        "target": {"path": None, "words": report["counts"]["scoped_words"]},
        "task_surface": "validation",
        "tool": "register_sweep",
        "version": "2.0.0",
        "warnings": [],
    }
    assert out == _canonical(expected) + b"\n"
    # The envelope carries no family cell, no plaintext filter value, no path,
    # and no corpus identifier.
    assert b"persona" not in out
    assert b"unused" not in out


@pytest.mark.parametrize("total", [0, 1, 99, 100, 101, 200])
def test_progress_goldens_by_total(tmp_path: Path, total: int) -> None:
    """Acceptance 16: exact progress bytes at each pinned total. No cadence line
    at the final `K == N`, and exactly one pending-completion line."""
    root = tmp_path / f"t{total}"
    root.mkdir()
    manifest = _d2_corpus(root, total)
    code, _out, err = _d2_run(_d2_args(root, manifest))
    assert code == 0
    expected = "".join(
        f"register sweep progress: completed={k} total={total}\n"
        for k in range(100, total, 100)
    ) + (
        f"register sweep processing-complete: completed={total} total={total} "
        f"report_commit=pending\n"
    )
    assert err.decode("ascii") == expected
    assert err.count(b"processing-complete") == 1
    assert f"completed={total} total={total}\n".encode() not in err


@pytest.mark.parametrize("resume_from", [0, 50, 100, 150])
@pytest.mark.parametrize("total", [0, 1, 99, 100, 101, 200])
def test_progress_decision_goldens_on_resume(total: int, resume_from: int) -> None:
    """The pure cadence decision functions, pinned at every spec resume start.
    A shard seals every 250 rows, so starts of 50/100/150 are reachable only
    through the decision functions, not through a real sealed chain."""
    if resume_from > total:
        with pytest.raises(rs.InternalError):
            rs.progress_ordinals(total, resume_from=resume_from)
        return
    expected = tuple(
        k for k in range(100, total, 100) if k > resume_from
    )
    assert rs.progress_ordinals(total, resume_from=resume_from) == expected
    for k in range(0, total + 1):
        assert rs.progress_is_eligible(k, total, resume_from=resume_from) == (
            k in expected
        )
    assert rs.progress_is_eligible(total, total, resume_from=resume_from) is False
    assert rs.processing_complete_line(total) == (
        f"register sweep processing-complete: completed={total} total={total} "
        f"report_commit=pending\n"
    )


def test_fresh_and_resumed_reports_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance 11/16: interruption after a sealed shard loses only the
    unpublished rows; the resumed report, hash, and stdout envelope are
    byte-identical to a fresh run, and no cadence line is replayed."""
    total = rs.SHARD_ROWS + 1
    manifest = _d2_corpus(tmp_path, total)
    fresh_root = tmp_path / "fresh"
    code, fresh_out, fresh_err = _d2_run(_d2_out_args(manifest, fresh_root))
    assert code == 0
    fresh_report = (fresh_root / "report.json").read_bytes()
    assert sorted(p.name for p in (fresh_root / "state").iterdir()) == [
        "register-00000000.sqlite", "register-00000001.sqlite",
    ]

    resume_root = tmp_path / "resumed"
    real_read = rs.read_planned_document
    calls = {"n": 0}

    def interrupting_read(path: Any, expected_fingerprint: Any) -> bytes:
        calls["n"] += 1
        if calls["n"] > rs.SHARD_ROWS:
            raise KeyboardInterrupt("synthetic interruption")
        return real_read(path, expected_fingerprint)

    monkeypatch.setattr(rs, "read_planned_document", interrupting_read)
    with pytest.raises(KeyboardInterrupt):
        _d2_run(_d2_out_args(manifest, resume_root))
    monkeypatch.setattr(rs, "read_planned_document", real_read)
    assert not (resume_root / "report.json").exists()
    assert sorted(p.name for p in (resume_root / "state").iterdir()) == [
        "register-00000000.sqlite",
    ]

    code, resumed_out, resumed_err = _d2_run(
        _d2_out_args(manifest, resume_root, "--resume")
    )
    assert code == 0
    assert (resume_root / "report.json").read_bytes() == fresh_report
    assert resumed_out == fresh_out
    # No replay: the fresh run emitted cadence lines at 100 and 200, the
    # resumed run starts at 250 and emits none.
    assert fresh_err.count(b"register sweep progress:") == 2
    assert resumed_err == (
        b"register sweep processing-complete: completed=251 total=251 "
        b"report_commit=pending\n"
    )


# ---- Sealed rows are re-associated with the current plan ----------------
#
# ``decode_register_shard`` checks a sealed shard against ITSELF: schema,
# metadata domains, row digests, the delta equation, and the hash chain. None of
# that says the sealed rows still describe the documents the CURRENT frozen plan
# assigns to those scoped ordinals. A same-UID writer can rewrite a shard and
# recompute every hash it commits -- the fixtures below do exactly that, by
# calling the module's OWN encoder -- so the runner rebinds each sealed ordinal
# to the manifest before trusting it. The checkpoint's integrity model is
# owner-trusted (0700/0600), not adversary-proof; see the module docstring.


def _interrupt_after_first_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: Path, root: Path
) -> Path:
    """Run until exactly one shard is sealed, then interrupt. Returns the
    checkpoint directory."""
    real_read = rs.read_planned_document
    calls = {"n": 0}

    def interrupting_read(path: Any, expected_fingerprint: Any) -> bytes:
        calls["n"] += 1
        if calls["n"] > rs.SHARD_ROWS:
            raise KeyboardInterrupt("synthetic interruption")
        return real_read(path, expected_fingerprint)

    monkeypatch.setattr(rs, "read_planned_document", interrupting_read)
    with pytest.raises(KeyboardInterrupt):
        _d2_run(_d2_out_args(manifest, root))
    monkeypatch.setattr(rs, "read_planned_document", real_read)
    state = root / "state"
    assert sorted(p.name for p in state.iterdir()) == ["register-00000000.sqlite"]
    return state


def _forge_first_shard(
    state: Path, domains: rs.RegisterDomains, rewrite: Any
) -> list[dict[str, Any]]:
    """Rewrite the sealed shard's rows through ``rewrite`` and re-seal it with
    the module's own encoder, so every inner hash is honest again."""
    name = "register-00000000.sqlite"
    raw_sealed = (state / name).read_bytes()
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw_sealed)
        meta = dict(
            connection.execute("SELECT key, value FROM checkpoint_meta").fetchall()
        )
    finally:
        connection.close()
    binding_sha256 = json.loads(meta["checkpoint_binding_sha256"])
    sealed = rs.decode_register_shard(
        raw_sealed,
        name=name,
        domains=domains,
        checkpoint_binding_sha256=binding_sha256,
    )
    forged_rows = [rewrite(index, dict(row)) for index, row in enumerate(sealed.rows)]
    raw, _shard = rs.encode_register_shard(
        shard_number=sealed.shard_number,
        first_scoped_ordinal=sealed.first_scoped_ordinal,
        checkpoint_binding_sha256=sealed.checkpoint_binding_sha256,
        prior_shard_sha256=sealed.prior_shard_sha256,
        rows=forged_rows,
        domains=domains,
    )
    _replace_shard(state, name, raw)
    return forged_rows


def test_a_sloppily_forged_sealed_prefix_refuses_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domains: rs.RegisterDomains
) -> None:
    """Fixture (a): sealed rows rewritten to alien ordinals, alien projected-row
    digests, and ``document_bytes=1``, with every inner hash recomputed. The
    shard is internally perfect and still must refuse: it does not describe this
    run's plan."""
    total = rs.SHARD_ROWS + 1
    manifest = _d2_corpus(tmp_path, total)
    root = tmp_path / "forged"
    state = _interrupt_after_first_shard(tmp_path, monkeypatch, manifest, root)

    def sloppy(index: int, row: dict[str, Any]) -> dict[str, Any]:
        row["manifest_ordinal"] = 9_000_000 + index
        row["projected_row_sha256"] = rs.prefixed(f"{index + 1:064x}")
        row["document_bytes"] = 1
        return row

    _forge_first_shard(state, domains, sloppy)
    code, out, err = _d2_run(_d2_out_args(manifest, root, "--resume"))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert not (root / "report.json").exists()


@pytest.mark.parametrize("field", ["manifest_ordinal", "projected_row_sha256",
                                   "document_bytes", "declared_family"])
def test_each_re_association_field_is_checked_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domains: rs.RegisterDomains,
    field: str,
) -> None:
    """One forged field at a time: each of the four bound fields refuses on its
    own, so no single check is carrying the whole refusal."""
    total = rs.SHARD_ROWS + 1
    manifest = _d2_corpus(tmp_path, total, register="personal")
    root = tmp_path / f"one-{field}"
    state = _interrupt_after_first_shard(tmp_path, monkeypatch, manifest, root)

    def one_field(index: int, row: dict[str, Any]) -> dict[str, Any]:
        if index == 0:
            if field == "manifest_ordinal":
                row["manifest_ordinal"] = 9_000_000
            elif field == "projected_row_sha256":
                row["projected_row_sha256"] = rs.prefixed("b" * 64)
            elif field == "document_bytes":
                row["document_bytes"] = row["document_bytes"] + 1
            else:
                other = [
                    value for value in domains.declared
                    if value != row["declared_family"]
                ][0]
                row["declared_family"] = other
        return row

    _forge_first_shard(state, domains, one_field)
    code, out, err = _d2_run(_d2_out_args(manifest, root, "--resume"))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert not (root / "report.json").exists()


def test_a_sophisticated_classified_words_forgery_is_outside_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domains: rs.RegisterDomains
) -> None:
    """Fixture (b), the honest boundary: flipping only ``classified_family`` and
    ``words`` -- fields the manifest does not determine -- cannot be caught by
    the ordinal/digest/bytes/declared-family re-association, BY CONSTRUCTION.

    What IS asserted is the scope of the model: the four bound fields still
    match, the forged run still commits, and its report differs from a fresh
    one. This is the owner-trusted boundary stated in the module docstring, not
    a defect the added checks were expected to close."""
    total = rs.SHARD_ROWS + 1
    manifest = _d2_corpus(tmp_path, total)
    control_root = tmp_path / "control"
    assert _d2_run(_d2_out_args(manifest, control_root))[0] == 0
    control_report = (control_root / "report.json").read_bytes()

    root = tmp_path / "sophisticated"
    state = _interrupt_after_first_shard(tmp_path, monkeypatch, manifest, root)

    def sophisticated(index: int, row: dict[str, Any]) -> dict[str, Any]:
        if row["classified_family"] is not None:
            other = [
                value for value in domains.classified
                if value != row["classified_family"]
            ][0]
            row["classified_family"] = other
        row["words"] = row["words"] + 1
        return row

    forged_rows = _forge_first_shard(state, domains, sophisticated)
    # The four re-associated fields are untouched, which is exactly why the
    # forgery survives.
    assert all(row["document_bytes"] > 1 for row in forged_rows)
    code, _out, _err = _d2_run(_d2_out_args(manifest, root, "--resume"))
    assert code == 0
    assert (root / "report.json").read_bytes() != control_report


def test_resume_of_a_complete_sealed_chain_reprocesses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sealed chain that already covers the whole plan resumes with zero
    document reads and still commits the identical report."""
    total = rs.SHARD_ROWS
    manifest = _d2_corpus(tmp_path, total)
    control_root = tmp_path / "control"
    assert _d2_run(_d2_out_args(manifest, control_root))[0] == 0
    control_report = (control_root / "report.json").read_bytes()

    root = tmp_path / "run"
    real_publish = rs.TopologyPreflight.publish_report

    def refusing_publish(self: Any, payload: bytes) -> None:
        raise KeyboardInterrupt("synthetic interruption at the commit point")

    monkeypatch.setattr(rs.TopologyPreflight, "publish_report", refusing_publish)
    with pytest.raises(KeyboardInterrupt):
        _d2_run(_d2_out_args(manifest, root))
    monkeypatch.setattr(rs.TopologyPreflight, "publish_report", real_publish)
    assert not (root / "report.json").exists()

    reads: list[Any] = []
    real_read = rs.read_planned_document

    def counting_read(path: Any, expected_fingerprint: Any) -> bytes:
        reads.append(path)
        return real_read(path, expected_fingerprint)

    monkeypatch.setattr(rs, "read_planned_document", counting_read)
    code, _out, err = _d2_run(_d2_out_args(manifest, root, "--resume"))
    assert code == 0
    assert reads == []
    assert (root / "report.json").read_bytes() == control_report
    assert err == (
        b"register sweep processing-complete: completed=250 total=250 "
        b"report_commit=pending\n"
    )


def test_shards_seal_exactly_at_the_frozen_partition(tmp_path: Path) -> None:
    """Acceptance 11 through the runner: the plan's one immutable partition."""
    for total, names in (
        (1, ["register-00000000.sqlite"]),
        (rs.SHARD_ROWS, ["register-00000000.sqlite"]),
        (rs.SHARD_ROWS + 1,
         ["register-00000000.sqlite", "register-00000001.sqlite"]),
    ):
        root = tmp_path / f"n{total}"
        root.mkdir()
        manifest = _d2_corpus(root, total)
        assert _d2_run(_d2_args(root, manifest))[0] == 0
        assert sorted(p.name for p in (root / "state").iterdir()) == names


# --------------------------------------------------------------------------
# Acceptance test 15: privacy, errors, and the terminal commit
# --------------------------------------------------------------------------


SENTINEL_PERSONA = "zqsentinelpersonazq"
SENTINEL_PATH = "zqsentineldirzq"
SENTINEL_PROSE = "zqsentinelprosezq"


def test_sentinel_values_never_reach_any_output_stream(tmp_path: Path) -> None:
    """Acceptance 15: sentinel prose, path components, and the raw persona
    filter never reach stdout, stderr, the report, or the checkpoint."""
    root = tmp_path / SENTINEL_PATH
    root.mkdir()
    manifest = _d2_corpus(
        root, 3,
        body=SENTINEL_PROSE,
        corpus_name=SENTINEL_PATH + "-corpus",
        manifest_name=SENTINEL_PATH + "-manifest.jsonl",
        use=["idiolect"], split="holdout", ai_status="ai_edited",
        persona=SENTINEL_PERSONA,
    )
    code, out, err = _d2_run(
        _d2_args(root, manifest,
                 "--persona", SENTINEL_PERSONA,
                 "--use", "idiolect", "--split", "holdout",
                 "--ai-status", "ai_edited")
    )
    assert code == 0
    report_bytes = (root / "report.json").read_bytes()
    checkpoint_bytes = b"".join(
        p.read_bytes() for p in sorted((root / "state").iterdir())
    )
    for sentinel in (SENTINEL_PERSONA, SENTINEL_PATH, SENTINEL_PROSE):
        encoded = sentinel.encode("utf-8")
        assert encoded not in out
        assert encoded not in err
        assert encoded not in report_bytes
        assert encoded not in checkpoint_bytes
    # The three validated categorical selectors appear ONLY in the report's
    # closed `scope` object -- never in stdout, stderr, or the checkpoint.
    report = json.loads(report_bytes.decode("utf-8"))
    assert report["scope"]["use"] == "idiolect"
    assert report["scope"]["split"] == "holdout"
    assert report["scope"]["ai_status"] == "ai_edited"
    assert report["scope"]["persona_selected"] is True
    for selector in (b"idiolect", b"holdout", b"ai_edited"):
        assert selector not in out
        assert selector not in err
        assert selector not in checkpoint_bytes
        assert report_bytes.count(selector) == 1


def test_persona_selected_toggles_only_with_a_valid_persona_filter(
    tmp_path: Path,
) -> None:
    for index, (extra, expected) in enumerate(
        [([], False), (["--persona", "alpha"], True)]
    ):
        root = tmp_path / f"p{index}"
        root.mkdir()
        manifest = _d2_corpus(root, 2, persona="alpha")
        assert _d2_run(_d2_args(root, manifest, *extra))[0] == 0
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        assert report["scope"]["persona_selected"] is expected


def test_broken_stdout_after_the_commit_still_exits_zero(tmp_path: Path) -> None:
    """Acceptance 15: a closed consumer after the terminal report commit is
    absorbed by the total sink -- exit 0, no stderr, report byte-identical."""
    manifest = _d2_corpus(tmp_path, 3)
    control_root = tmp_path / "control"
    assert _d2_run(_d2_out_args(manifest, control_root))[0] == 0
    control_report = (control_root / "report.json").read_bytes()

    root = tmp_path / "broken"
    out = _D2BrokenSink()
    err = _D2Sink()
    code = rs.main(_d2_out_args(manifest, root), stdout=out, stderr=err)
    assert code == 0
    assert out.attempts == 1
    assert (root / "report.json").read_bytes() == control_report
    assert err.bytes == (
        b"register sweep processing-complete: completed=3 total=3 "
        b"report_commit=pending\n"
    )
    assert b"reason_category" not in err.bytes


def test_no_stderr_or_error_envelope_follows_the_commit(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 2)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert code == 0
    assert err.endswith(b"report_commit=pending\n")
    assert err.count(b"report_commit=pending") == 1
    assert b"reason_category" not in err
    assert json.loads(out.decode("utf-8"))["available"] is True


def test_the_scoped_bytes_ceiling_is_bad_input_not_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scoped-byte ceiling is an INPUT bound, so breaching it is the spec's
    ``bad_input`` row (exit 2), not ``internal_error`` (exit 4).

    The running total is what breaches: the first document fits under the
    patched ceiling and the second crosses it, so this pins the pre-add check in
    the document loop rather than a single oversized document.
    """
    manifest = _d2_corpus(tmp_path, 3)
    one_document = len("tiny synthetic document 0\n")
    monkeypatch.setattr(rs, "MAX_SCOPED_BYTES", one_document + 1)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (2, GOLDEN_BAD_INPUT)
    assert json.loads(out.decode("utf-8"))["reason_category"] == "bad_input"
    assert err == b""
    assert not (tmp_path / "report.json").exists()


def test_a_scoped_bytes_ceiling_above_the_plan_still_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the pre-add check: a ceiling exactly at the plan's
    total scoped bytes must NOT refuse."""
    manifest = _d2_corpus(tmp_path, 3)
    monkeypatch.setattr(rs, "MAX_SCOPED_BYTES", 3 * len("tiny synthetic document 0\n"))
    assert _d2_run(_d2_args(tmp_path, manifest))[0] == 0


def test_missing_document_refuses_with_the_bad_input_golden(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 2)
    (tmp_path / "corpus" / "doc00001.txt").unlink()
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (2, GOLDEN_BAD_INPUT)
    assert err == b""
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "state").exists()


def test_non_utf8_document_refuses_with_the_bad_input_golden(
    tmp_path: Path,
) -> None:
    manifest = _d2_corpus(tmp_path, 2)
    (tmp_path / "corpus" / "doc00001.txt").write_bytes(b"\xff\xfe not utf-8")
    code, out, _err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (2, GOLDEN_BAD_INPUT)
    assert not (tmp_path / "report.json").exists()


def test_report_and_checkpoint_topology_collision_refuses(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 1)
    code, out, _err = _d2_run(
        ["--manifest", str(manifest),
         "--report-out", str(tmp_path / "same"),
         "--checkpoint-dir", str(tmp_path / "same")]
    )
    assert (code, out) == (3, GOLDEN_POLICY_REFUSED)
    assert not (tmp_path / "same").exists()


def test_existing_report_refuses_before_creating_the_checkpoint(
    tmp_path: Path,
) -> None:
    manifest = _d2_corpus(tmp_path, 1)
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    code, out, _err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (3, GOLDEN_POLICY_REFUSED)
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == "{}"
    assert not (tmp_path / "state").exists()


def test_resume_without_a_checkpoint_directory_refuses(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 1)
    code, out, _err = _d2_run(_d2_args(tmp_path, manifest, "--resume"))
    assert (code, out) == (3, GOLDEN_POLICY_REFUSED)
    assert not (tmp_path / "report.json").exists()


def test_internal_error_after_processing_complete_publishes_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance 16: a failure after `processing-complete` but before
    publication emits the one controlled envelope and commits nothing."""
    manifest = _d2_corpus(tmp_path, 2)

    def refusing_freeze(**_kwargs: Any) -> tuple[bytes, str, dict, bytes]:
        raise rs.InternalError()

    monkeypatch.setattr(rs, "freeze_publication", refusing_freeze)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (4, GOLDEN_INTERNAL_ERROR)
    assert err == (
        b"register sweep processing-complete: completed=2 total=2 "
        b"report_commit=pending\n"
    )
    assert not (tmp_path / "report.json").exists()
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == [
        "register-00000000.sqlite",
    ]


def test_keyboard_interrupt_is_never_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _d2_corpus(tmp_path, 1)

    def interrupting(*_args: Any, **_kwargs: Any) -> bytes:
        raise KeyboardInterrupt("synthetic")

    monkeypatch.setattr(rs, "read_planned_document", interrupting)
    with pytest.raises(KeyboardInterrupt):
        _d2_run(_d2_args(tmp_path, manifest))
    assert not (tmp_path / "report.json").exists()


# --------------------------------------------------------------------------
# Acceptance test 1, runtime side: H1 identity and no network
# --------------------------------------------------------------------------


def _d2_h1_copy(tmp_path: Path) -> tuple[Path, Path]:
    """Copy the real receipt and classifier into a writable fixture tree."""
    receipt_path, classifier_path = rs.default_h1_paths()
    references = tmp_path / "h1" / "references"
    scripts = tmp_path / "h1" / "scripts"
    references.mkdir(parents=True)
    scripts.mkdir(parents=True)
    receipt_copy = references / receipt_path.name
    classifier_copy = scripts / classifier_path.name
    shutil.copyfile(receipt_path, receipt_copy)
    shutil.copyfile(classifier_path, classifier_copy)
    return receipt_copy, classifier_copy


@pytest.mark.parametrize("target", ["receipt", "classifier"])
def test_tampered_h1_artifact_refuses_before_any_document_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """Acceptance 1, runtime: a tampered receipt or classifier refuses with
    `policy_refused` before the first document body is read and before either
    output is created."""
    receipt_copy, classifier_copy = _d2_h1_copy(tmp_path)
    if target == "receipt":
        receipt_copy.write_bytes(receipt_copy.read_bytes().replace(b"{", b"{ ", 1))
    else:
        classifier_copy.write_bytes(
            classifier_copy.read_bytes() + b"\n# tampered\n"
        )
    monkeypatch.setattr(
        rs, "default_h1_paths", lambda: (receipt_copy, classifier_copy)
    )
    reads: list[Any] = []
    monkeypatch.setattr(
        rs, "read_planned_document",
        lambda path, fingerprint: reads.append(path) or b"",
    )
    manifest = _d2_corpus(tmp_path, 2)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert reads == []
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "state").exists()


def test_runtime_makes_no_network_call_with_sockets_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket-blocking harness: every socket constructor raises for the whole
    run, and the sweep still commits."""
    import socket as socket_module

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("H2 runtime attempted a network call")

    for name in (
        "socket", "socketpair", "create_connection", "getaddrinfo",
        "gethostbyname",
    ):
        monkeypatch.setattr(socket_module, name, refuse, raising=False)
    manifest = _d2_corpus(tmp_path, 3)
    assert _d2_run(_d2_args(tmp_path, manifest))[0] == 0


# --------------------------------------------------------------------------
# The REAL entry point: ``python3 register_sweep.py ...``
#
# ``rs.main(argv)`` called in-process is not where users meet this tool. Run as
# a script the module loads under the name ``__main__``; ``manifest_validator``
# then executes ``from register_sweep import BadInput, ...`` at import time,
# which -- without the alias installed in the ``__main__`` guard -- loads a
# SECOND, independent copy of this file under the name ``register_sweep``, with
# its own distinct ``BadInput``/``PolicyRefused``/``InternalError`` classes.
# A refusal raised from the manifest_validator seam is then an instance of the
# second copy's class, misses every ``isinstance`` check in
# ``controlled_failure_class``, and ships as exit 4 / ``internal_error``
# instead of exit 2 / ``bad_input`` -- nine rows of the spec's failure map.
#
# Every case below is therefore driven through ``subprocess`` at the real entry
# point, and each pins the exit code AND the ``reason_category`` parsed out of
# the golden stdout envelope.
# --------------------------------------------------------------------------


REGISTER_SWEEP_SCRIPT = SCRIPTS / "register_sweep.py"


def _cli_subprocess(script: Path, argv: list[str]) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        [sys.executable, str(script), *argv], capture_output=True
    )
    return completed.returncode, completed.stdout, completed.stderr


#: One row per category of the spec's failure map that is raised from the
#: manifest_validator seam -- the exact set the double-import hazard misroutes.
CLI_BAD_INPUT_CASES = (
    "bad_enum_value",
    "missing_document",
    "duplicate_row_collision",
    "malformed_json",
    "bom",
    "duplicate_key",
    "non_object_row",
)


def _cli_bad_input_corpus(root: Path, case: str) -> Path:
    """Write one hostile manifest (and its corpus) for a failure-map row."""
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    document = corpus / "doc00000.txt"
    document.write_text("tiny synthetic document 0\n", encoding="utf-8")
    good = {
        "path": "corpus/doc00000.txt",
        "use": ["baseline"],
        "ai_status": "pre_ai_human",
    }
    encoded = json.dumps(good).encode("utf-8")
    manifest = root / "manifest.jsonl"
    if case == "bad_enum_value":
        manifest.write_bytes(
            json.dumps(dict(good, ai_status="not_a_real_ai_status")).encode("utf-8")
            + b"\n"
        )
    elif case == "missing_document":
        manifest.write_bytes(encoded + b"\n")
        document.unlink()
    elif case == "duplicate_row_collision":
        manifest.write_bytes(encoded + b"\n" + encoded + b"\n")
    elif case == "malformed_json":
        manifest.write_bytes(b"{bad-json\n")
    elif case == "bom":
        manifest.write_bytes(b"\xef\xbb\xbf" + encoded + b"\n")
    elif case == "duplicate_key":
        manifest.write_bytes(
            b'{"path":"corpus/doc00000.txt","path":"corpus/doc00000.txt",'
            b'"use":["baseline"],"ai_status":"pre_ai_human"}\n'
        )
    elif case == "non_object_row":
        manifest.write_bytes(b"[1,2,3]\n")
    else:  # pragma: no cover - the parametrization is closed
        raise AssertionError(case)
    return manifest


@pytest.mark.parametrize("case", CLI_BAD_INPUT_CASES)
def test_cli_entry_point_ships_seam_refusals_as_bad_input(
    tmp_path: Path, case: str
) -> None:
    """Every seam-raised failure-map row is exit 2 / ``bad_input`` AT THE REAL
    ENTRY POINT, not exit 4 / ``internal_error``."""
    root = tmp_path / case
    root.mkdir()
    manifest = _cli_bad_input_corpus(root, case)
    code, out, err = _cli_subprocess(
        REGISTER_SWEEP_SCRIPT,
        [
            "--manifest", str(manifest),
            "--report-out", str(root / "report.json"),
            "--checkpoint-dir", str(root / "state"),
        ],
    )
    assert code == 2, (case, out, err)
    assert out == GOLDEN_BAD_INPUT, case
    assert json.loads(out.decode("utf-8"))["reason_category"] == "bad_input"
    assert err == b""
    assert not (root / "report.json").exists()
    assert not (root / "state").exists()


def _cli_tampered_receipt_script(tmp_path: Path) -> Path:
    """A second ``register_sweep.py`` whose plugin tree holds a tampered H1
    receipt.

    Only ``register_sweep.py`` is a real copy -- ``default_h1_paths`` derives
    the reference tree from ``Path(__file__).resolve().parent``, so a symlink
    would resolve straight back to the real plugin. Every sibling module is
    symlinked, so this costs one file rather than a tree copy.
    """
    plugin = tmp_path / "plugin"
    fake_scripts = plugin / "scripts"
    references = plugin / "references"
    fake_scripts.mkdir(parents=True)
    references.mkdir(parents=True)
    for item in SCRIPTS.iterdir():
        if item.name in ("__pycache__", "tests"):
            continue
        if item.name == "register_sweep.py":
            shutil.copyfile(item, fake_scripts / item.name)
        else:
            (fake_scripts / item.name).symlink_to(item)
    receipt_path, _classifier_path = rs.default_h1_paths()
    (references / receipt_path.name).write_bytes(
        receipt_path.read_bytes().replace(b"{", b"{ ", 1)
    )
    return fake_scripts / "register_sweep.py"


def test_cli_entry_point_ships_a_tampered_receipt_as_policy_refused(
    tmp_path: Path,
) -> None:
    """The other side of the taxonomy at the real entry point: a policy row is
    exit 3 / ``policy_refused``, and it still is with the alias installed."""
    script = _cli_tampered_receipt_script(tmp_path)
    root = tmp_path / "run"
    root.mkdir()
    manifest = _d2_corpus(root, 2)
    code, out, err = _cli_subprocess(
        script,
        [
            "--manifest", str(manifest),
            "--report-out", str(root / "report.json"),
            "--checkpoint-dir", str(root / "state"),
        ],
    )
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert json.loads(out.decode("utf-8"))["reason_category"] == "policy_refused"
    assert not (root / "report.json").exists()
    assert not (root / "state").exists()


def test_cli_entry_point_commits_the_success_envelope(tmp_path: Path) -> None:
    """The success case reconfirmed at the real entry point: exit 0, the one
    committed success envelope on stdout, the cadence line on stderr, and the
    report published."""
    root = tmp_path / "run"
    root.mkdir()
    manifest = _d2_corpus(root, 3)
    code, out, err = _cli_subprocess(
        REGISTER_SWEEP_SCRIPT,
        [
            "--manifest", str(manifest),
            "--report-out", str(root / "report.json"),
            "--checkpoint-dir", str(root / "state"),
        ],
    )
    assert code == 0
    envelope = json.loads(out.decode("utf-8"))
    assert envelope["available"] is True
    assert "reason_category" not in envelope
    assert err == (
        b"register sweep processing-complete: completed=3 total=3 "
        b"report_commit=pending\n"
    )
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["scoped_documents"] == 3


def test_runtime_import_graph_reaches_no_network_module(tmp_path: Path) -> None:
    """The module import graph -- including the exec'd receipt-bound classifier
    -- pulls in no network module. Run in a child interpreter so the enclosing
    test session's own imports cannot mask the answer."""
    root = tmp_path / "probe"
    root.mkdir()
    manifest = _d2_corpus(root, 2)
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import io\n"
        "import register_sweep as rs\n"
        "out = io.BytesIO(); err = io.BytesIO()\n"
        "code = rs.main(["
        f"'--manifest', {str(manifest)!r},"
        f"'--report-out', {str(root / 'report.json')!r},"
        f"'--checkpoint-dir', {str(root / 'state')!r}"
        "], stdout=out, stderr=err)\n"
        # Transport-capable modules by top-level name, plus the transport
        # submodules of urllib/http by exact name. Deliberately NOT the bare
        # 'urllib'/'http' top-levels: 'urllib.parse' is pure string parsing
        # with no socket, and Python 3.12's pathlib imports it at module
        # scope (3.13 made that lazy) -- forbidding it would pin the stdlib's
        # internals, not this module's network posture.
        "network = sorted(\n"
        "    name for name in sys.modules\n"
        "    if name.split('.')[0] in {\n"
        "        'socket', '_socket', 'ssl', '_ssl',\n"
        "        'ftplib', 'smtplib', 'telnetlib', 'asyncio', 'selectors',\n"
        "        'requests', 'httpx', 'subprocess',\n"
        "    }\n"
        "    or name in {'urllib.request', 'urllib.error', 'http.client',\n"
        "                'http.server', 'xmlrpc'}\n"
        ")\n"
        "print(code, network)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, check=True,
    )
    assert completed.stdout.strip() == "0 []", completed.stdout + completed.stderr


# --------------------------------------------------------------------------
# Fail-before wiring checks
# --------------------------------------------------------------------------


def test_scoped_plan_collision_refuses_before_any_document_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the scoped-subset collision check would let a repeated row
    inflate the inventory; the runner must refuse before the first body read."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.txt").write_text("only document\n", encoding="utf-8")
    rows = [
        {"path": "corpus/doc.txt", "use": ["baseline"], "ai_status": "pre_ai_human"},
        {"path": "corpus/doc.txt", "use": ["baseline"], "ai_status": "pre_ai_human"},
    ]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    reads: list[Any] = []
    real_read = rs.read_planned_document
    monkeypatch.setattr(
        rs, "read_planned_document",
        lambda path, fingerprint: reads.append(path) or real_read(path, fingerprint),
    )
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert reads == []
    assert not (tmp_path / "state").exists()


def test_a_filtered_out_collision_does_not_refuse(tmp_path: Path) -> None:
    """The collision check runs on the SCOPED subset: a duplicate row that the
    filters exclude is not a collision."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc.txt").write_text("only document\n", encoding="utf-8")
    rows = [
        {"path": "corpus/doc.txt", "use": ["baseline"], "ai_status": "pre_ai_human"},
        {"path": "corpus/doc.txt", "use": ["exclude"], "ai_status": "pre_ai_human"},
    ]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    code, _out, _err = _d2_run(_d2_args(tmp_path, manifest, "--use", "baseline"))
    assert code == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["input_rows"] == 2
    assert report["counts"]["scoped_documents"] == 1


def test_topology_is_revalidated_before_the_terminal_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the explicit pre-publication revalidate leaves only the
    post-create call and the one inside `publish_report`."""
    manifest = _d2_corpus(tmp_path, 1)
    real_revalidate = rs.TopologyPreflight.revalidate
    calls = {"n": 0}

    def counting(self: Any) -> None:
        calls["n"] += 1
        real_revalidate(self)

    monkeypatch.setattr(rs.TopologyPreflight, "revalidate", counting)
    assert _d2_run(_d2_args(tmp_path, manifest))[0] == 0
    # 1: after checkpoint create. 2: the explicit pre-publication revalidate.
    # 3: the one publish_report performs itself.
    assert calls["n"] == 3


def test_processing_complete_follows_the_aggregate_reassembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pending-completion line is emitted only after every shard/aggregate
    reassembly check succeeds, so a reassembly failure never fabricates it."""
    manifest = _d2_corpus(tmp_path, 2)

    def refusing_reassemble(*_args: Any, **_kwargs: Any) -> dict:
        raise rs.InternalError()

    monkeypatch.setattr(rs, "reassemble_aggregate", refusing_reassemble)
    code, out, err = _d2_run(_d2_args(tmp_path, manifest))
    assert (code, out) == (4, GOLDEN_INTERNAL_ERROR)
    assert err == b""
    assert not (tmp_path / "report.json").exists()


def test_scope_binding_commits_the_raw_persona(tmp_path: Path) -> None:
    """The report's `scope_sha256` is the digest of the private scope payload,
    which includes the raw persona; two runs differing only in persona commit
    different scope digests while both report `persona_selected: true`."""
    digests = []
    for index, persona in enumerate(("alpha", "beta")):
        root = tmp_path / f"s{index}"
        root.mkdir()
        manifest = _d2_corpus(root, 1, persona="alpha")
        code, _out, _err = _d2_run(
            _d2_args(root, manifest, "--persona", persona)
        )
        assert code == 0
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        assert report["scope"]["persona_selected"] is True
        digests.append(report["scope"]["scope_sha256"])
        expected = rs.prefixed(
            rs.scope_binding(
                use=None, split=None, ai_status=None,
                persona=persona, min_words=100,
            )[1]
        )
        assert report["scope"]["scope_sha256"] == expected
    assert digests[0] != digests[1]


def test_min_words_reaches_the_classifier_and_is_not_a_row_filter(
    tmp_path: Path,
) -> None:
    """`--min-words` is passed straight to `classify_register`: at the default
    every tiny document refuses `short_text`, and at 1 every one classifies.
    The scoped row count is identical either way."""
    counts = []
    for index, extra in enumerate(([], ["--min-words", "1"])):
        root = tmp_path / f"m{index}"
        root.mkdir()
        manifest = _d2_corpus(root, 3, register="personal")
        assert _d2_run(_d2_args(root, manifest, *extra))[0] == 0
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        counts.append(report["counts"])
        assert report["counts"]["scoped_documents"] == 3
    assert counts[0]["refused_documents"] == 3
    assert counts[0]["classified_documents"] == 0
    assert counts[1]["refused_documents"] == 0
    assert counts[1]["classified_documents"] == 3


def test_report_counts_conserve_the_scoped_totals(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 6, register="personal")
    assert _d2_run(_d2_args(tmp_path, manifest, "--min-words", "1"))[0] == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    counts = report["counts"]
    assert (
        counts["resolved_declared_documents"]
        + counts["unresolved_declared_documents"]
        == counts["scoped_documents"]
    )
    assert (
        counts["classified_documents"] + counts["refused_documents"]
        == counts["scoped_documents"]
    )
    assert (
        counts["classified_words"] + counts["refused_words"]
        == counts["scoped_words"]
    )
    for measure in ("documents", "words"):
        assert sum(
            cell[measure]
            for cell in report["declared_family_inventory"].values()
        ) == counts[f"scoped_{measure}"]
        assert sum(
            cell[measure] for cell in report["match_inventory"].values()
        ) == counts[f"scoped_{measure}"]


# --------------------------------------------------------------------------
# Acceptance test 18: registration and gates
# --------------------------------------------------------------------------


def test_module_declares_the_capability_task_surface() -> None:
    assert rs.TASK_SURFACE == "validation"


def test_capability_fragment_and_golden_agree() -> None:
    import capabilities as _capabilities  # type: ignore

    manifest = _capabilities.load_manifest(SCRIPTS.parent / "capabilities.d")
    entry = {e["id"]: e for e in manifest["entries"]}["register_composition_sweep"]
    golden = json.loads(
        (
            Path(__file__).resolve().parent
            / "_golden_capabilities"
            / "register_composition_sweep.json"
        ).read_text(encoding="utf-8")
    )
    assert entry == golden
    assert entry["surface"] == rs.TASK_SURFACE == "validation"
    assert entry["status"] == "heuristic"
    # `manifest_validator` imports this surface; the registry records that.
    assert entry["consumers"] == ["manifest_validator"]
    assert entry["script_path"] == (
        "plugins/setec-voiceprint/scripts/register_sweep.py"
    )
    assert entry["dependencies"]["python"] == []


def test_no_orphan_script_or_surface_drift_for_this_capability() -> None:
    """The drift linter's Check 1/Check 3 conditions, asserted directly.

    The linter itself is a CI gate (it imports the plugin's optional-dependency
    fixture generators); what this pins is the property it checks: the script
    declares a module-level ``TASK_SURFACE`` constant, and the fragment's
    ``surface`` equals it.
    """
    import ast as _ast

    tree = _ast.parse(
        (SCRIPTS / "register_sweep.py").read_text(encoding="utf-8")
    )
    declared = [
        node.value.value
        for node in tree.body
        if isinstance(node, _ast.Assign)
        for target in node.targets
        if isinstance(target, _ast.Name)
        and target.id == "TASK_SURFACE"
        and isinstance(node.value, _ast.Constant)
    ]
    assert declared == ["validation"]
    fragment = (
        SCRIPTS.parent / "capabilities.d" / "register_composition_sweep.yaml"
    ).read_text(encoding="utf-8")
    assert "surface: validation" in fragment
    assert "- manifest_validator" in fragment
    assert "status: heuristic" in fragment


def test_docs_freshness_is_green() -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "check_docs_freshness.py")],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_changelog_fragment_names_the_capability_id_verbatim() -> None:
    """The shipped changelog record names the capability id verbatim.

    A `changelog.d/` fragment is transient by design: a release folds it into
    `CHANGELOG.md` and deletes it. Pinning only the fragment path therefore
    fails on the first release cut after this test lands, which is what
    happened at v1.127.0. Follow the record to wherever it currently lives.
    """
    fragment = (
        REPO_ROOT / "changelog.d" / "feat-register-composition-sweep-encoders.md"
    )
    record = fragment if fragment.exists() else REPO_ROOT / "CHANGELOG.md"
    assert "register_composition_sweep" in record.read_text(encoding="utf-8")


def test_script_is_executable_as_a_module_entry_point() -> None:
    """`__main__` wiring: an argument-less invocation refuses with the one
    `bad_input` golden on stdout, exit 2, and no usage text."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "register_sweep.py")],
        capture_output=True, cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 2
    assert completed.stdout == GOLDEN_BAD_INPUT
    assert completed.stderr == b""


def test_end_to_end_through_the_real_process_entry_point(tmp_path: Path) -> None:
    manifest = _d2_corpus(tmp_path, 2)
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "register_sweep.py"),
         *_d2_args(tmp_path, manifest)],
        capture_output=True, cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == (
        b"register sweep processing-complete: completed=2 total=2 "
        b"report_commit=pending\n"
    )
    report_bytes = (tmp_path / "report.json").read_bytes()
    envelope = json.loads(completed.stdout.decode("utf-8"))
    assert envelope["results"]["report_sha256"] == (
        "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    )


# --------------------------------------------------------------------------
# Native-Windows document-plan binding (fake backend)
# --------------------------------------------------------------------------


def test_windows_bind_returns_the_scoped_nine_field_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_windows_bind` is no longer a refuse-always stub: it returns the frozen
    9-field scoped handle fingerprint, which includes `change_time`."""
    fingerprint = (11, 22, 33, 44, 55, 66, 77, 88, 99)
    closed: list[int] = []

    class Direct:
        def __init__(self, identity: tuple[object, ...]) -> None:
            self.identity = identity

    class FakeWindowsIo:
        next_handle = 100

        def pin_directory(self, _path: Path, *, writable_final: bool) -> tuple[int, int, str]:
            assert not writable_final
            self.next_handle += 2
            return self.next_handle - 1, self.next_handle, "parent"

        def open_file(self, _parent: int, _name: str, *, allow_multiple_links: bool = False) -> int:
            self.next_handle += 1
            return self.next_handle

        def require_direct(self, _handle: int, kind: str, *, allow_multiple_links: bool = False) -> Direct:
            return Direct(("directory",) if kind == "directory" else ("file", 7))

        def scoped_fingerprint(self, _handle: int) -> tuple[int, ...]:
            return fingerprint

        def close(self, handle: int) -> None:
            closed.append(handle)

    monkeypatch.setattr(
        shingle_dedup_io, "_windows_module", lambda: FakeWindowsIo()
    )
    assert shingle_dedup_io._windows_bind(tmp_path / "doc.txt") == fingerprint
    assert closed
    payload, digest = rs.windows_fingerprint_binding(fingerprint)
    assert payload == _canonical(
        {"fields": list(fingerprint), "platform": "windows"}
    )
    assert digest == hashlib.sha256(
        b"setec-register-sweep-file-fingerprint-v2\n"
        + struct.pack(">Q", len(payload))
        + payload
    ).hexdigest()


def test_windows_bind_refuses_when_the_name_rebinds_to_another_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Direct:
        def __init__(self, identity: tuple[object, ...]) -> None:
            self.identity = identity

    class SwappingWindowsIo:
        next_handle = 200
        file_opens = 0

        def pin_directory(self, _path: Path, *, writable_final: bool) -> tuple[int, int, str]:
            self.next_handle += 2
            return self.next_handle - 1, self.next_handle, "parent"

        def open_file(self, _parent: int, _name: str, *, allow_multiple_links: bool = False) -> int:
            self.next_handle += 1
            return self.next_handle

        def require_direct(self, _handle: int, kind: str, *, allow_multiple_links: bool = False) -> Direct:
            if kind == "directory":
                return Direct(("directory",))
            self.file_opens += 1
            return Direct(("file", self.file_opens))

        def scoped_fingerprint(self, _handle: int) -> tuple[int, ...]:
            return (1, 2, 3, 4, 5, 6, 7, 8, 9)

        def close(self, _handle: int) -> None:
            return None

    monkeypatch.setattr(
        shingle_dedup_io, "_windows_module", lambda: SwappingWindowsIo()
    )
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io._windows_bind(tmp_path / "doc.txt")


# ---- Codex round: windows topology dispatch ----
#
# Two Codex-review findings on PR #361.
#
# X1: ``TopologyPreflight`` opened its retained directory chains with POSIX
# ``os.open`` + ``dir_fd``-relative ``os.stat``. Neither exists on native
# Windows -- ``os.open`` cannot open a directory there and ``dir_fd`` is
# unsupported -- so every native-Windows sweep refused at the preflight,
# contradicting the spec's native-Windows support. The preflight now dispatches
# its platform mechanics to ``windows_descriptor_io`` while the portable
# collision/ancestor/leaf rules stay in one platform-neutral place. These are
# fake-backend tests: they prove the dispatch layer reaches the native entry
# points, that a backend missing a seam is a controlled refusal rather than a
# POSIX fallback, and that a POSIX run never touches the native arm at all.
#
# X2: ``shingle_dedup_io.bind_regular``'s candidate-presence probe treated any
# ``OSError`` as absence, so ``EACCES``/``ELOOP``/``EIO`` on a higher-priority
# candidate silently selected a lower-priority file -- the sweep could classify
# a different document than the manifest names. Only ``ENOENT``/``ENOTDIR``
# advance now.

import errno as _errno


class _FakeWinNode:
    """The ``NodeInfo``-shaped record the fake native backend returns."""

    def __init__(self, kind: str, file_id: int, attributes: int = 0) -> None:
        self.kind = kind
        self.volume_serial = 4711
        self.file_id = file_id
        self.attributes = attributes


class _FakeWinBackend:
    """A fake ``windows_descriptor_io`` answering from an in-memory tree.

    Only the seams the topology preflight is required to reach are implemented,
    so a dispatch that quietly fell back to the POSIX ``dir_fd`` calls, or that
    reached a seam it should not, is visible in ``events``.
    """

    FILE_ATTRIBUTE_REPARSE_POINT = 0x400

    def __init__(
        self,
        directories: Any,
        leaves: dict[Path, _FakeWinNode] | None = None,
    ) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.open_handles: set[int] = set()
        self.directories = {Path(item) for item in directories}
        self.leaves = dict(leaves or {})
        self._ids: dict[str, int] = {}
        self._by_handle: dict[int, Path] = {}
        self._next = 100

    # -- fixture control ---------------------------------------------------

    def identity_of(self, path: Path) -> int:
        key = str(path)
        if key not in self._ids:
            self._next += 1
            self._ids[key] = self._next
        return self._ids[key]

    def drift(self, path: Path) -> None:
        """Swap one component's native file id, as a live rebind would."""
        self.identity_of(path)
        self._next += 1
        self._ids[str(path)] = self._next

    def kinds(self) -> list[Any]:
        return [event[0] for event in self.events]

    # -- the native seams --------------------------------------------------

    def _open(self, path: Path) -> int:
        self._next += 1
        handle = self._next
        self._by_handle[handle] = path
        self.open_handles.add(handle)
        return handle

    def pin_directory_chain(self, path: Any, *, writable_final: bool = True) -> tuple[int, ...]:
        target = Path(path)
        self.events.append(("pin_directory_chain", str(target), writable_final))
        handles: list[int] = []
        for index in range(len(target.parts)):
            prefix = Path(*target.parts[: index + 1])
            if prefix not in self.directories:
                for handle in reversed(handles):
                    self.close(handle)
                raise FileNotFoundError(_errno.ENOENT, "no such directory")
            handles.append(self._open(prefix))
        return tuple(handles)

    def revalidate_directory_chain(self, path: Any, retained: tuple[int, ...]) -> None:
        target = Path(path)
        self.events.append(("revalidate_directory_chain", str(target), tuple(retained)))
        if len(retained) != len(target.parts):
            raise OSError("directory chain length changed")
        for handle, index in zip(retained, range(len(target.parts))):
            if self._by_handle.get(handle) != Path(*target.parts[: index + 1]):
                raise OSError("directory chain identity changed")

    def require_direct(self, handle: int, kind: str, *, allow_multiple_links: bool = False) -> _FakeWinNode:
        path = self._by_handle.get(handle)
        if path is None or handle not in self.open_handles:
            raise OSError("stale native handle")
        self.events.append(("require_direct", handle, kind))
        if kind == "directory" and path not in self.directories:
            raise OSError("native node is not a directory")
        return _FakeWinNode("directory", self.identity_of(path))

    def probe_leaf_node(self, parent: int, name: str) -> _FakeWinNode | None:
        base = self._by_handle.get(parent)
        if base is None or parent not in self.open_handles:
            raise OSError("stale native handle")
        target = base / name
        self.events.append(("probe_leaf_node", str(target)))
        if target in self.directories:
            return _FakeWinNode("directory", self.identity_of(target))
        return self.leaves.get(target)

    def close(self, handle: int) -> None:
        self.events.append(("close", handle))
        self.open_handles.discard(handle)


def _win_ancestors(*paths: Path) -> list[Path]:
    """Every directory component of every given path, as the fake volume."""
    seen: list[Path] = []
    for path in paths:
        for index in range(len(path.parts)):
            prefix = Path(*path.parts[: index + 1])
            if prefix not in seen:
                seen.append(prefix)
    return seen


def _forbid_posix_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("the native arm must never reach the POSIX seams")

    monkeypatch.setattr(rs, "_open_directory_chain", forbidden)
    monkeypatch.setattr(rs, "_revalidate_directory_chain", forbidden)
    monkeypatch.setattr(rs, "_leaf_stat", forbidden)


def _simulate_nt(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    """Simulate a native-Windows platform through the module's own seam.

    ``os.name`` itself is deliberately left alone: mutating it would re-flavour
    every ``pathlib.Path`` built during the test into a ``WindowsPath`` over a
    POSIX temporary directory, which proves nothing about this module's
    dispatch. ``_native_windows`` is the one predicate the preflight consults.
    """
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", backend)
    monkeypatch.setattr(rs, "_native_windows", lambda: True)


def test_the_native_platform_seam_is_bound_to_os_name(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: the simulated-``nt`` tests below are only meaningful if the seam they
    drive is the real platform test. No ``Path`` is built here, so mutating
    ``os.name`` for one assertion is safe."""
    assert rs._native_windows() is (os.name == "nt")
    monkeypatch.setattr(rs.os, "name", "nt")
    assert rs._native_windows() is True
    monkeypatch.setattr(rs.os, "name", "posix")
    assert rs._native_windows() is False


def test_nt_topology_preflight_reaches_the_native_descriptor_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: on a simulated ``nt`` platform ``check`` uses the native entry
    points -- retained chains for both parents, native identity, and the same
    leaf-absence rules -- and still creates nothing."""
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    backend = _FakeWinBackend(_win_ancestors(tmp_path))
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)

    with rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=False
    ) as preflight:
        assert preflight.report_path == report
        assert preflight.checkpoint_path == state
        assert not preflight.committed

    kinds = backend.kinds()
    # One retained chain per target parent, revalidated through the handles.
    assert kinds.count("pin_directory_chain") == 2
    assert kinds.count("revalidate_directory_chain") >= 2
    assert kinds.count("require_direct") > 0
    # Both leaves probed natively, neither present, nothing created.
    assert ("probe_leaf_node", str(report)) in backend.events
    assert ("probe_leaf_node", str(state)) in backend.events
    assert backend.open_handles == set()
    assert sorted(item.name for item in tmp_path.iterdir()) == []


def test_nt_topology_preflight_binds_native_volume_serial_and_file_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: identity is ``(volume_serial, file_id)`` on the native arm, and it
    is revalidated through the retained handles -- a component that rebinds to
    another native id refuses."""
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    backend = _FakeWinBackend(_win_ancestors(tmp_path, state))
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)

    preflight = rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=True
    )
    try:
        assert preflight._report_identities[-1] == (4711, backend.identity_of(tmp_path))
        preflight.revalidate()
        backend.drift(tmp_path)
        with pytest.raises(rs.PolicyRefused):
            preflight.revalidate()
    finally:
        preflight.close()
    assert backend.open_handles == set()


def test_nt_topology_publish_routes_through_the_native_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: ``publish_report`` revalidates through the retained native handles
    before the terminal create-new commit."""
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    backend = _FakeWinBackend(_win_ancestors(tmp_path, state))
    published: list[tuple[Any, ...]] = []
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)
    monkeypatch.setattr(
        rs, "publish_create_new",
        lambda path, payload, *, privacy_policy: published.append(
            (path, payload, privacy_policy)
        ),
    )

    preflight = rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=True
    )
    before = backend.kinds().count("revalidate_directory_chain")
    preflight.publish_report(b'{"ok":true}\n')

    assert preflight.committed
    assert published == [(report, b'{"ok":true}\n', rs.PRIVACY_POLICY)]
    assert backend.kinds().count("revalidate_directory_chain") > before
    # The terminal commit releases every retained native handle.
    assert backend.open_handles == set()
    with pytest.raises(rs.InternalError):
        preflight.publish_report(b'{"ok":true}\n')


@pytest.mark.parametrize("resume,leaves,reason", [
    (False, {"report.json": _FakeWinNode("file", 55)}, "an existing report name"),
    (False, {"cp": _FakeWinNode("directory", 56)}, "a fresh checkpoint that exists"),
    (True, {}, "a resume checkpoint that is absent"),
    (True, {"cp": _FakeWinNode("file", 57)}, "a resume checkpoint that is a file"),
    (True, {"cp": _FakeWinNode("directory", 58, attributes=0x400)},
     "a resume checkpoint that is a reparse point"),
])
def test_nt_topology_leaf_rules_match_the_posix_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    resume: bool, leaves: dict[str, _FakeWinNode], reason: str
) -> None:
    """X1: the native arm reproduces the POSIX leaf refusals exactly."""
    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    backend = _FakeWinBackend(
        _win_ancestors(tmp_path),
        {tmp_path / name: node for name, node in leaves.items()},
    )
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)
    with pytest.raises(rs.PolicyRefused):
        rs.TopologyPreflight.check(
            report_path=report, checkpoint_path=state, resume=resume
        )
    # Refusing never leaks a retained native handle.
    assert backend.open_handles == set()


def test_nt_portable_key_collisions_refuse_before_any_native_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: the portable-component-key collision logic is platform-neutral -- it
    is not duplicated into the native arm and runs before any handle opens."""
    backend = _FakeWinBackend(_win_ancestors(tmp_path))
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)
    with pytest.raises(rs.PolicyRefused):
        rs.TopologyPreflight.check(
            report_path=tmp_path / "report.json",
            checkpoint_path=tmp_path / "REPORT.JSON",
            resume=False,
        )
    assert backend.events == []


def test_nt_backend_without_the_leaf_probe_refuses_instead_of_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: a native backend that lacks a required seam is a controlled
    ``PolicyRefused``, never a silent fallback to the POSIX ``dir_fd`` calls."""
    class WithoutLeafProbe(_FakeWinBackend):
        probe_leaf_node = None  # type: ignore[assignment]

    backend = WithoutLeafProbe(_win_ancestors(tmp_path))
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)
    with pytest.raises(rs.PolicyRefused):
        rs.TopologyPreflight.check(
            report_path=tmp_path / "report.json",
            checkpoint_path=tmp_path / "cp",
            resume=False,
        )
    # The chain arm was reached natively; only the missing seam refused.
    assert "pin_directory_chain" in backend.kinds()
    assert backend.open_handles == set()


@pytest.mark.parametrize("missing", [
    "pin_directory_chain", "revalidate_directory_chain", "require_direct",
    "FILE_ATTRIBUTE_REPARSE_POINT",
])
def test_nt_backend_missing_any_required_seam_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    backend = _FakeWinBackend(_win_ancestors(tmp_path))
    monkeypatch.delattr(type(backend), missing, raising=True)
    _forbid_posix_topology(monkeypatch)
    _simulate_nt(monkeypatch, backend)
    with pytest.raises(rs.PolicyRefused):
        rs.TopologyPreflight.check(
            report_path=tmp_path / "report.json",
            checkpoint_path=tmp_path / "cp",
            resume=False,
        )


def test_nt_topology_refuses_when_the_native_backend_cannot_be_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: no native backend at all is still a refusal, not a POSIX fallback."""
    _forbid_posix_topology(monkeypatch)
    # ``sys.modules[name] is None`` makes ``import name`` raise ImportError.
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", None)
    monkeypatch.setattr(rs, "_native_windows", lambda: True)
    with pytest.raises(rs.PolicyRefused):
        rs.TopologyPreflight.check(
            report_path=tmp_path / "report.json",
            checkpoint_path=tmp_path / "cp",
            resume=False,
        )


@POSIX_ONLY
def test_posix_topology_preflight_never_reaches_the_native_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X1: a POSIX run touches none of the Windows entry points, across all
    three public seams (check, revalidate, publish)."""
    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a POSIX run must not reach the native backend")

    for name in (
        "_windows_backend", "_windows_open_directory_chain",
        "_windows_revalidate_directory_chain", "_windows_leaf_node",
        "_windows_close_handle", "_windows_helper", "_windows_constant",
    ):
        monkeypatch.setattr(rs, name, forbidden)

    report = tmp_path / "report.json"
    state = tmp_path / "cp"
    with rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=False
    ) as fresh:
        assert not fresh.committed
    state.mkdir()
    with rs.TopologyPreflight.check(
        report_path=report, checkpoint_path=state, resume=True
    ) as preflight:
        preflight.revalidate()
        preflight.publish_report(b'{"ok":true}\n')
        assert preflight.committed
    assert report.read_bytes() == b'{"ok":true}\n'


# ---- Codex round: fail-closed candidate probing ----


def _blocking_lstat(
    monkeypatch: pytest.MonkeyPatch, blocked: Path, error: OSError
) -> None:
    """Raise ``error`` for exactly one candidate path, delegate the rest."""
    real = os.lstat

    def probe(path: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            same = os.fspath(path) == str(blocked)
        except TypeError:
            same = False
        if same:
            raise error
        return real(path, *args, **kwargs)

    monkeypatch.setattr(shingle_dedup_io.os, "lstat", probe)


@pytest.mark.parametrize("error", [
    PermissionError(_errno.EACCES, "permission denied"),
    OSError(_errno.ELOOP, "too many levels of symbolic links"),
    OSError(_errno.EIO, "input/output error"),
    OSError(),  # errno is None: still not a confirmed absence
])
def test_bind_regular_refuses_a_candidate_probe_that_is_not_an_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    """X2: an unanswerable probe on a higher-priority candidate must never let
    a lower-priority candidate be bound in its place."""
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    second.write_text("second\n", encoding="utf-8")
    _blocking_lstat(monkeypatch, first, error)
    with pytest.raises(shingle_dedup_io.SecureIOError):
        shingle_dedup_io.bind_regular([first, second])


@pytest.mark.parametrize("error", [
    FileNotFoundError(_errno.ENOENT, "no such file or directory"),
    NotADirectoryError(_errno.ENOTDIR, "not a directory"),
])
def test_bind_regular_still_advances_on_a_confirmed_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    """X2: ENOENT and a missing intermediate directory (ENOTDIR) remain
    absences, so the frozen candidate order is unchanged."""
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    second.write_text("second\n", encoding="utf-8")
    _blocking_lstat(monkeypatch, first, error)
    absolute, index, fingerprint = shingle_dedup_io.bind_regular([first, second])
    assert (absolute, index) == (second, 1)
    assert len(fingerprint) == 5


@POSIX_ONLY
def test_bind_regular_refuses_a_real_permission_blocked_candidate(
    tmp_path: Path
) -> None:
    """X2: the same rule on a real filesystem -- candidate 0 sits under an
    unsearchable parent while candidate 1 is a perfectly good file."""
    blocked_parent = tmp_path / "blocked"
    blocked_parent.mkdir()
    first = blocked_parent / "doc.txt"
    first.write_text("first\n", encoding="utf-8")
    second = tmp_path / "doc.txt"
    second.write_text("second\n", encoding="utf-8")
    os.chmod(blocked_parent, 0o000)
    try:
        # Pre-condition: the probe really is blocked for this process.
        with pytest.raises(PermissionError):
            os.lstat(first)
        with pytest.raises(shingle_dedup_io.SecureIOError):
            shingle_dedup_io.bind_regular([first, second])
    finally:
        os.chmod(blocked_parent, 0o700)


@POSIX_ONLY
def test_missing_intermediate_directory_is_an_absence_on_a_real_filesystem(
    tmp_path: Path
) -> None:
    """X2: the ENOTDIR arm, unmocked -- a file standing where an intermediate
    directory should be is an absence, not an unanswerable probe."""
    (tmp_path / "notadir").write_text("i am a file\n", encoding="utf-8")
    first = tmp_path / "notadir" / "doc.txt"
    second = tmp_path / "doc.txt"
    second.write_text("second\n", encoding="utf-8")
    absolute, index, _fingerprint = shingle_dedup_io.bind_regular([first, second])
    assert (absolute, index) == (second, 1)


@POSIX_ONLY
def test_permission_blocked_candidate_ships_the_bad_input_golden(
    tmp_path: Path
) -> None:
    """X2 end to end: an unreadable candidate-0 document is an *input* failure.

    The spec's failure map puts manifest/document/input failures in
    ``bad_input`` (exit 2), and the projection seam already converts
    ``shingle_dedup_io.SecureIOError`` from the document planner into
    ``register_sweep.BadInput``. Candidate 1 here is a valid document, so
    before the fail-closed fix this run bound the wrong file and exited 0.
    """
    manifest = _d2_corpus(tmp_path, 1)
    inner = tmp_path / "inner"
    inner.mkdir()
    shadow = inner / "corpus"
    shadow.mkdir()
    (shadow / "doc00000.txt").write_text("shadowed document 0\n", encoding="utf-8")
    inner_manifest = inner / "manifest.jsonl"
    inner_manifest.write_bytes(manifest.read_bytes())
    os.chmod(shadow, 0o000)
    try:
        code, out, err = _d2_run(_d2_args(tmp_path, inner_manifest))
    finally:
        os.chmod(shadow, 0o700)
    assert (code, out) == (2, GOLDEN_BAD_INPUT)
    assert err == b""
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "state").exists()


# ---- CI round: CRLF row-terminator carve-in ----
#
# The C7 row-boundary hardening refused every CRLF manifest, which broke the
# legacy validator on native Windows (text-mode writers translate "\n" to
# "\r\n"). Exactly one trailing "\r" per line is a Windows line terminator;
# everything else in FORBIDDEN_ROW_BREAKS still refuses.


def _crlf_pair(tmp_path: Path) -> tuple[bytes, bytes, Path]:
    doc = tmp_path / "doc.txt"
    doc.write_text("word " * 150, encoding="utf-8")
    row = (
        '{"path": "doc.txt", "use": ["baseline"], "ai_status": "pre_ai_human",'
        ' "register": "personal"}'
    )
    lf = (row + "\n").encode("utf-8")
    crlf = (row + "\r\n").encode("utf-8")
    return lf, crlf, tmp_path / "manifest.jsonl"


def test_crlf_manifest_projects_identically_to_lf(tmp_path: Path) -> None:
    lf, crlf, manifest = _crlf_pair(tmp_path)
    projections = []
    for data in (lf, crlf):
        projection = mv.project_register_sweep_manifest_bytes(
            data, manifest_path=manifest
        )
        assert projection.input_rows == 1
        projections.append(projection)
    # The raw manifest bytes are deliberately unhashed, so the CRLF and LF
    # spellings yield byte-identical projected-row and manifest digests.
    lf_rows = [rs.projected_row_binding(_row_object(r))[1] for r in projections[0].rows]
    crlf_rows = [rs.projected_row_binding(_row_object(r))[1] for r in projections[1].rows]
    assert lf_rows == crlf_rows


def _row_object(row: object) -> dict:
    return {
        "ai_status": row.ai_status,
        "manifest_ordinal": row.manifest_ordinal,
        "path": row.path,
        "persona": row.persona,
        "register": row.register,
        "split": row.split,
        "use": list(row.use),
    }


def test_crlf_manifest_passes_the_legacy_validator(tmp_path: Path) -> None:
    lf, crlf, manifest = _crlf_pair(tmp_path)
    manifest.write_bytes(crlf)
    result = mv.validate_manifest(str(manifest))
    # The property under test: CRLF is a line terminator, not a row-boundary
    # refusal. Unrelated legacy field diagnostics are out of scope here.
    assert result["n_entries"] == 1
    assert not any(
        "forbidden line-break character" in issue["message"]
        for issue in result["issues"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        b'{"path": "doc.txt"}\r',            # bare trailing CR, no LF terminator
        b'{"path": "doc\rname.txt"}\n',      # interior CR inside the line
        b'{"path": "doc.txt"}\r\r\n',        # double CR: only one is a terminator
        "{} {}\n".encode("utf-8"),      # the original hardening still bites
    ],
)
def test_non_terminator_carriage_returns_still_refuse(
    tmp_path: Path, payload: bytes
) -> None:
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(
            payload, manifest_path=tmp_path / "manifest.jsonl"
        )
