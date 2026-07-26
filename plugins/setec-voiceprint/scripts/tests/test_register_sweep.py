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
