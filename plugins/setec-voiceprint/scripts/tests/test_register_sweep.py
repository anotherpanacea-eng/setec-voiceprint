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


@pytest.fixture(scope="module")
def domains(binding: rs.H1Binding) -> rs.RegisterDomains:
    return rs.RegisterDomains.from_binding(binding)


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


FORBIDDEN_KEY_CASES = [
    *sorted(rs.FORBIDDEN_KEY_ATOMS),
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
