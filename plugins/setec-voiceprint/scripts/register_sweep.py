#!/usr/bin/env python3
"""Aggregate register-composition hygiene inventory (Spec 73 / H2).

This module runs the landed H1 register-family classifier over an explicitly
scoped private manifest slice and emits a deterministic aggregate count
inventory. The inventory answers one hygiene question only: **is this corpus
obviously register-mixed enough to warrant a human check?** It does not answer
that question. It emits no score, threshold, band, flag, or verdict.

The H1 classifier is a confounded heuristic proxy: its family labels vary with
topic, project, date, and document length. Nothing here is a calibrated or
reportable register distribution, classifier accuracy evidence, source or
provenance analysis, or authorization to change, register, activate, train on,
or publish a corpus.

The manifest fields ``source``, ``source_id``, and ``source_family`` are outside
this contract. They are never read, normalized, hashed, inferred from, grouped
by, checkpointed, or emitted.

The first section supplies the canonical-encoding layer and the H1 binding
layer: every framed digest domain in the spec, the strict H1 receipt read, and
the receipt-bound classifier load plus its closed public-result validation. The
manifest projection, aggregation and report, checkpoint codec, topology
preflight, and the CLI/runner each live in one delimited section below, built
against those exact encoders.

The checkpoint's integrity model is OWNER-TRUSTED: its 0700 directory and 0600
immutable shards, the per-shard hash chain, the run-binding equality check, and
the runner's re-association of every sealed row with the current frozen plan
detect an unrelated, stale, truncated, reordered, or carelessly edited
checkpoint, but they are not proof against an adversary running as the same UID,
who can rewrite a shard and recompute every hash it commits.
"""

from __future__ import annotations

from dataclasses import dataclass

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import struct
import sys
import unicodedata
from pathlib import Path
from typing import Any, Sequence

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore
from shingle_dedup_checkpoint import (  # type: ignore
    CheckpointRefusal,
    ImmutableShardDirectory,
)
from shingle_dedup_io import (  # type: ignore
    SecureIOError,
    bind_regular,
    publish_create_new,
    read_bounded_regular,
)

# ``manifest_validator`` imports this module at load time (exception classes
# and shared string-domain constants), so the one back-reference H2 needs —
# the three CLI filter enums — is imported lazily inside each of the two
# functions that read them, ``build_report_scope`` and ``parse_arguments``, to
# keep manifest_validator -> register_sweep the only import-time direction.

# --------------------------------------------------------------------------
# Fixed identities
# --------------------------------------------------------------------------

TOOL = "register_sweep"
VERSION = "2.0.0"
TAXONOMY = "register_families/v2"
REPORT_SCHEMA_VERSION = "setec-register-sweep-report/2"
CHECKPOINT_SCHEMA_VERSION = "setec-register-sweep-checkpoint/2"
CHECKPOINT_KIND = "register"
PRIVACY_POLICY = "owner_private_v1"
IMMUTABLE_SHARD_CONTRACT_VERSION = 1

#: Raw SHA-256 of the immutable H1 closeout receipt bytes. Pinned from the
#: landed receipt in merge commit c6d7cbc72da2a7429fbe986c5cd7df38aad69da3
#: (PR #357). H2 trusts the already-committed receipt bytes and never queries
#: GitHub; ``tools/check_register_sweep_h1_gate.py`` is the CI-side gate that
#: validated the Actions attempt before the receipt was committed.
H1_RECEIPT_SHA256 = "626e32652d476ac88d7d0caf3c78de17dd93c0c81f175405502b83f563922839"

H1_RECEIPT_SCHEMA_VERSION = "setec-h1-landing-receipt/2"
H1_LANDED_COMMIT = "7ffabd343066585de2a80c22b4aeba25d27d5450"
H1_SPEC_PATH = "specs/37-register-classifier-repair.md"
H1_SPEC_SHA256 = "7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1"
H1_REFUSAL_SPEC_PATH = "specs/76-register-classifier-refusal-reasons.md"
H1_REFUSAL_SPEC_SHA256 = (
    "5be5f74d74a8f9243d1cbeef4e24ed49ef1a14c932867ecb80cafcabfc734722"
)
H1_BASE_CLASSIFIER_SHA256 = (
    "740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d"
)
#: The final landed H1 classifier raw digest and the two public identities it
#: must reproduce. These are the same constants the CI-side gate pins in
#: ``tools/check_register_sweep_h1_gate.py``; the committed receipt is the
#: single H1 identity for both sides, so H2 refuses any receipt whose fields
#: disagree with them rather than trusting a receipt that agrees only with
#: itself.
H1_FINAL_CLASSIFIER_SHA256 = (
    "808da9eb369fd3aad725d9e6a799a6151b2f751b0f8f2ca8332dc037fbaaf2d8"
)
H1_MAPPING_SHA256 = (
    "8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef"
)
H1_REFUSAL_CONTRACT_SHA256 = (
    "f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"
)
H1_WORKFLOW_PATH = ".github/workflows/tests.yml"
H1_WORKFLOW_SHA256_ALLOWLIST = (
    "1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd",
    "2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50",
)
H1_REQUIRED_JOBS = (
    "pytest",
    "macos-descriptor-confinement",
    "windows-descriptor-backend",
    "windows-owner-corrections",
    "windows-shingle-dedup",
    "windows-nonprose-sweep",
    "windows-private-writer-guards",
)

RECEIPT_FILENAME = "register-classifier-h1-receipt.json"
CLASSIFIER_FILENAME = "register_classifier.py"

# --------------------------------------------------------------------------
# Frozen framed domains. Every domain is ASCII and includes its terminal LF as
# its final byte. Domain reuse with a different payload schema is forbidden.
# --------------------------------------------------------------------------

DOMAIN_MAPPING = b"setec-register-family-mapping-v2\n"
DOMAIN_REFUSAL_CONTRACT = b"setec-register-classifier-refusal-contract-v1\n"
DOMAIN_SCOPE = b"setec-register-sweep-scope-v2\n"
DOMAIN_PROJECTED_ROW = b"setec-register-sweep-projected-row-v1\n"
DOMAIN_PROJECTED_MANIFEST = b"setec-register-sweep-projected-manifest-v1\n"
DOMAIN_SCOPED_ROWS = b"setec-register-sweep-scoped-rows-v1\n"
DOMAIN_TARGET_PATH = b"setec-register-sweep-target-path-v2\n"
DOMAIN_FILE_FINGERPRINT = b"setec-register-sweep-file-fingerprint-v2\n"
DOMAIN_DOCUMENT_PLAN = b"setec-register-sweep-document-plan-v2\n"
DOMAIN_CHECKPOINT_BINDING = b"setec-register-sweep-checkpoint-binding-v2\n"
DOMAIN_AGGREGATE_DELTA = b"setec-register-sweep-aggregate-delta-v1\n"
DOMAIN_SHARD = b"setec-register-sweep-shard-v2\n"

FROZEN_DOMAINS = (
    DOMAIN_MAPPING,
    DOMAIN_REFUSAL_CONTRACT,
    DOMAIN_SCOPE,
    DOMAIN_PROJECTED_ROW,
    DOMAIN_PROJECTED_MANIFEST,
    DOMAIN_SCOPED_ROWS,
    DOMAIN_TARGET_PATH,
    DOMAIN_FILE_FINGERPRINT,
    DOMAIN_DOCUMENT_PLAN,
    DOMAIN_CHECKPOINT_BINDING,
    DOMAIN_AGGREGATE_DELTA,
    DOMAIN_SHARD,
)

# --------------------------------------------------------------------------
# Hard ceilings
# --------------------------------------------------------------------------

MAX_MANIFEST_BYTES = 134_217_728
MAX_CLASSIFIER_SOURCE_BYTES = 1_048_576
MAX_H1_RECEIPT_BYTES = 65_536
MAX_DOCUMENT_BYTES = 16_777_216
MAX_SCOPED_DOCUMENTS = 100_000
MAX_SCOPED_BYTES = 8_589_934_592
MAX_FINAL_SHARDS = 400
MAX_RESERVED_TEMPORARY_NAMES = 16
MAX_SHARD_BYTES = 4_194_304
MAX_CHECKPOINT_CUMULATIVE_BYTES = 1_677_721_600
SHARD_ROWS = 250

MIN_WORDS_FLOOR = 1
MIN_WORDS_CEILING = 1_000_000
MAX_PERSONA_BYTES = 128
MAX_PATH_BYTES = 4_096
MAX_USE_MEMBERS = 32
MAX_WARNING_BYTES = 4_096
MAX_SECONDARY = 8
INT64_MAX = 2**63 - 1

#: The exact closed report ``limits`` object.
LIMITS: dict[str, int] = {
    "checkpoint_cumulative_bytes": MAX_CHECKPOINT_CUMULATIVE_BYTES,
    "classifier_source_bytes": MAX_CLASSIFIER_SOURCE_BYTES,
    "document_bytes": MAX_DOCUMENT_BYTES,
    "final_shards": MAX_FINAL_SHARDS,
    "h1_receipt_bytes": MAX_H1_RECEIPT_BYTES,
    "manifest_bytes": MAX_MANIFEST_BYTES,
    "reserved_temporary_names": MAX_RESERVED_TEMPORARY_NAMES,
    "scoped_bytes": MAX_SCOPED_BYTES,
    "scoped_documents": MAX_SCOPED_DOCUMENTS,
    "shard_bytes": MAX_SHARD_BYTES,
    "shard_rows": SHARD_ROWS,
}

#: Match-inventory domain. These are count buckets, not accuracy labels.
MATCH_DOMAIN = ("same", "different", "unresolved")

BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮"
    "⁦⁧⁨⁩"
)

ZERO_WORD_EVIDENCE_KEYS = ("n_words", "n_chars")
FULL_EVIDENCE_KEYS = (
    "n_words",
    "n_chars",
    "n_sentences",
    "n_paragraphs",
    "mean_paragraph_words",
    "heading_density_per_1k",
    "first_person_per_1k",
    "second_person_per_1k",
    "dialogue_ratio",
    "question_per_1k",
    "exclamation_per_1k",
    "inline_citation_per_1k",
    "statutory_per_1k",
    "formal_address_per_1k",
    "shall_pursuant_per_1k",
    "attributed_quote_per_1k",
    "imperative_open_per_1k",
    "past_tense_narrative_per_1k",
    "academic_voice_per_1k",
)
EVIDENCE_INTEGER_KEYS = frozenset(
    {"n_words", "n_chars", "n_sentences", "n_paragraphs"}
)

CLASSIFICATION_KEYS = frozenset(
    {
        "primary",
        "confidence",
        "secondary",
        "scores",
        "evidence",
        "warning",
        "taxonomy",
        "refusal_reason",
    }
)

H1_PUBLIC_SYMBOLS = (
    "REGISTER_TAXONOMY",
    "REGISTER_FAMILIES",
    "KNOWN_REGISTERS",
    "REGISTER_REFUSAL_REASONS",
    "CANONICAL_REGISTER_TO_FAMILY",
    "LEGACY_REGISTER_TO_FAMILY",
    "resolve_family",
    "classify_register",
)

H1_REFUSAL_REASONS = ("short_text", "all_weak", "exact_top_tie")

#: The frozen ``REGISTER_FAMILIES`` tuple of the receipt-pinned H1 classifier.
#: The committed receipt byte-pins ``register_classifier.py``, so this module
#: may state the identity as a constant instead of deriving the value domains
#: from whatever namespace happens to be bound. ``H1Binding`` refuses any
#: namespace whose tuple differs, which is what keeps the constant honest.
H1_REGISTER_FAMILIES = (
    "formal_legal_policy",
    "formal_first_person",
    "academic",
    "journalism",
    "narrative_fiction",
    "first_person_essay",
    "promotional",
    "short_social",
    "unknown",
)
#: ``F`` in the spec's freeze: the scored families. ``unknown`` is a refusal
#: sentinel and is never a classified family.
H1_CLASSIFIED_FAMILIES = tuple(
    name for name in H1_REGISTER_FAMILIES if name != "unknown"
)
#: ``D`` in the spec's freeze: ``F + ("unknown",) == REGISTER_FAMILIES``.
H1_DECLARED_FAMILIES = H1_CLASSIFIED_FAMILIES + ("unknown",)

#: The exact per-shard ``counts`` key set.
SHARD_COUNT_KEYS = frozenset(
    {
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
    }
)


# --------------------------------------------------------------------------
# Controlled refusals
# --------------------------------------------------------------------------


class SweepRefusal(Exception):
    """Base controlled refusal.

    Refusals carry no path, digest, commit, filter value, or caught-exception
    text: the CLI maps each subclass to one fixed golden envelope.
    """

    exit_code = 4
    reason_category = "internal_error"
    reason = "register composition sweep unavailable after internal failure"


class BadInput(SweepRefusal):
    """Manifest, document, or CLI input failure."""

    exit_code = 2
    reason_category = "bad_input"
    reason = "register composition sweep refused invalid input"


class PolicyRefused(SweepRefusal):
    """H1 identity/contract, checkpoint/privacy/platform, or create-new failure."""

    exit_code = 3
    reason_category = "policy_refused"
    reason = "register composition sweep refused by policy"


class InternalError(SweepRefusal):
    """Violation in H2's already-validated in-memory construction."""


# --------------------------------------------------------------------------
# Canonical encoding core
# --------------------------------------------------------------------------


INT64_MIN = -(2**63)

#: Nesting ceiling for the closed-domain walk. Every canonical payload in the
#: spec is at most five levels deep; the bound also terminates a cyclic value
#: before it can be walked forever.
MAX_CANONICAL_DEPTH = 64


def _require_nfc(text: str) -> None:
    if unicodedata.normalize("NFC", text) != text:
        raise InternalError()


def _require_canonical_value(value: Any, depth: int) -> None:
    """Refuse anything outside the closed canonical JSON domain.

    Admissible: JSON null, Boolean, signed-64-bit non-Boolean integer, NFC
    string, array, and object with string NFC keys. Floats are forbidden
    everywhere, including inside arrays and nested objects.
    """
    if depth > MAX_CANONICAL_DEPTH:
        raise InternalError()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not (INT64_MIN <= value <= INT64_MAX):
            raise InternalError()
        return
    if type(value) is str:
        _require_nfc(value)
        return
    if type(value) is list:
        for item in value:
            _require_canonical_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise InternalError()
            _require_nfc(key)
            _require_canonical_value(item, depth + 1)
        return
    raise InternalError()


def canonical_json(value: Any) -> bytes:
    """Return the exact canonical JSON encoding of a closed-domain value.

    Inputs are composed only of string keys, NFC valid Unicode strings, JSON
    null/Boolean, signed-64-bit non-Boolean integers, and arrays/objects whose
    order and key set the spec fixes. The whole value is walked against that
    closed domain before it is dumped: floats are forbidden in every canonical
    payload, every key and string must already be NFC, and every integer must
    fit in signed 64 bits. ``allow_nan=False`` additionally refuses non-finite
    values that reach ``json.dumps``.
    """
    _require_canonical_value(value, 0)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InternalError() from exc


def raw_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 over exact bytes, with no prefix."""
    if type(data) is not bytes:
        raise InternalError()
    return hashlib.sha256(data).hexdigest()


def framed_sha256(domain: bytes, payload: bytes) -> str:
    """Return ``SHA256(domain || uint64_be(len(payload)) || payload)`` as hex.

    No NUL separator, extra newline, hex decoding, or Unicode normalization
    occurs inside this function. ``domain`` must be one of the twelve frozen
    domains: domain reuse with a different payload schema is forbidden, and an
    unfrozen domain has no schema at all.
    """
    if type(domain) is not bytes or type(payload) is not bytes:
        raise InternalError()
    if not domain.endswith(b"\n") or not domain.isascii():
        raise InternalError()
    if domain not in FROZEN_DOMAINS:
        raise InternalError()
    return hashlib.sha256(
        domain + struct.pack(">Q", len(payload)) + payload
    ).hexdigest()


def framed_object(domain: bytes, value: Any) -> str:
    """Frame a canonical object payload under ``domain``."""
    return framed_sha256(domain, canonical_json(value))


def prefixed(digest: str) -> str:
    """Return the runtime/report/checkpoint digest string form."""
    if type(digest) is not str or len(digest) != 64:
        raise InternalError()
    if any(char not in "0123456789abcdef" for char in digest):
        raise InternalError()
    return "sha256:" + digest


def _require_prefixed(value: Any) -> str:
    if type(value) is not str or not value.startswith("sha256:"):
        raise InternalError()
    body = value[len("sha256:") :]
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        raise InternalError()
    return value


def _require_int(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not (low <= value <= high):
        raise InternalError()
    return value


# ``_require_count`` and ``_require_cell`` are defined once, in the Increment D1
# section below. Module-level rebinding meant an earlier duplicate pair here was
# already shadowed for every call site, so this section simply uses the single
# later definition.


def _require_inventory(inventory: Any, domain: tuple[str, ...]) -> None:
    """Require an inventory object keyed by exactly ``domain``."""
    if type(inventory) is not dict or set(inventory) != set(domain):
        raise InternalError()
    for cell in inventory.values():
        _require_cell(cell)


# --------------------------------------------------------------------------
# Framed payload builders
#
# Each builder returns ``(payload_bytes, digest_hex)`` so callers and the
# normative vector test can pin the exact preimage as well as the digest.
# --------------------------------------------------------------------------


def projected_row_binding(row: dict[str, Any]) -> tuple[bytes, str]:
    """Frame one projected manifest row's seven owned fields."""
    payload = canonical_json(row)
    return payload, framed_sha256(DOMAIN_PROJECTED_ROW, payload)


def projected_manifest_binding(
    row_digests: tuple[str, ...] | list[str],
) -> tuple[bytes, str]:
    """Frame every projected row digest in ascending manifest ordinal.

    Rows later excluded by scope filters are included here: the projected
    manifest binds the whole parsed input, not the scoped slice.
    """
    rows = [_require_prefixed(item) for item in row_digests]
    payload = canonical_json({"rows": rows})
    return payload, framed_sha256(DOMAIN_PROJECTED_MANIFEST, payload)


def scope_binding(
    *,
    use: str | None,
    split: str | None,
    ai_status: str | None,
    persona: str | None,
    min_words: int,
) -> tuple[bytes, str]:
    """Frame the private scope binding.

    This is the only H2 identity that binds the raw persona filter. The raw
    value never reaches stdout, the report, a checkpoint, an exception, or a
    log; only this digest does.
    """
    _require_int(min_words, MIN_WORDS_FLOOR, MIN_WORDS_CEILING)
    payload = canonical_json(
        {
            "ai_status": ai_status,
            "min_words": min_words,
            "persona": persona,
            "privacy_policy": PRIVACY_POLICY,
            "split": split,
            "use": use,
        }
    )
    return payload, framed_sha256(DOMAIN_SCOPE, payload)


def scoped_rows_binding(
    entries: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the scoped-row slice in manifest order.

    ``scoped_ordinal`` is zero-based and contiguous; an empty scope frames
    ``{"rows":[]}``. Entries are in manifest order, so ``manifest_ordinal`` is
    strictly ascending across the slice.
    """
    rows = []
    previous_manifest = -1
    for expected, entry in enumerate(entries):
        if set(entry) != {
            "manifest_ordinal",
            "projected_row_sha256",
            "scoped_ordinal",
        }:
            raise InternalError()
        if entry["scoped_ordinal"] != expected:
            raise InternalError()
        _require_int(entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1)
        manifest_ordinal = _require_int(entry["manifest_ordinal"], 0, INT64_MAX)
        if manifest_ordinal <= previous_manifest:
            raise InternalError()
        previous_manifest = manifest_ordinal
        _require_prefixed(entry["projected_row_sha256"])
        rows.append(
            {
                "manifest_ordinal": entry["manifest_ordinal"],
                "projected_row_sha256": entry["projected_row_sha256"],
                "scoped_ordinal": entry["scoped_ordinal"],
            }
        )
    payload = canonical_json({"rows": rows})
    return payload, framed_sha256(DOMAIN_SCOPED_ROWS, payload)


def target_path_binding(absolute_path: os.PathLike[str] | str) -> tuple[bytes, str]:
    """Frame the raw ``os.fsencode`` bytes of a frozen absolute path."""
    payload = os.fsencode(absolute_path)
    return payload, framed_sha256(DOMAIN_TARGET_PATH, payload)


def posix_fingerprint_binding(
    fields: tuple[int, ...] | list[int],
) -> tuple[bytes, str]:
    """Frame a POSIX identity/mutation fingerprint.

    Field order is exactly ``[dev, ino, size, mtime_ns, ctime_ns]``.
    """
    values = [_require_int(item, 0, INT64_MAX) for item in fields]
    if len(values) != 5:
        raise InternalError()
    payload = canonical_json({"fields": values, "platform": "posix"})
    return payload, framed_sha256(DOMAIN_FILE_FINGERPRINT, payload)


def windows_fingerprint_binding(
    fields: tuple[int, ...] | list[int],
) -> tuple[bytes, str]:
    """Frame a native-Windows identity/mutation fingerprint.

    Field order is exactly ``[volume_serial, file_id, size, write_time,
    change_time, creation_time, mode, links, attributes]``. ``change_time`` is
    present so same-size content mutation with a restored LastWriteTime still
    refuses.
    """
    values = [_require_int(item, 0, INT64_MAX) for item in fields]
    if len(values) != 9:
        raise InternalError()
    payload = canonical_json({"fields": values, "platform": "windows"})
    return payload, framed_sha256(DOMAIN_FILE_FINGERPRINT, payload)


def document_plan_binding(
    entries: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the frozen document plan in ascending scoped ordinal."""
    documents = []
    for expected, entry in enumerate(entries):
        if set(entry) != {
            "candidate_index",
            "file_fingerprint_sha256",
            "projected_row_sha256",
            "scoped_ordinal",
            "target_path_sha256",
        }:
            raise InternalError()
        if entry["scoped_ordinal"] != expected:
            raise InternalError()
        _require_int(entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1)
        _require_int(entry["candidate_index"], 0, 2)
        _require_prefixed(entry["file_fingerprint_sha256"])
        _require_prefixed(entry["projected_row_sha256"])
        _require_prefixed(entry["target_path_sha256"])
        documents.append(
            {
                "candidate_index": entry["candidate_index"],
                "file_fingerprint_sha256": entry["file_fingerprint_sha256"],
                "projected_row_sha256": entry["projected_row_sha256"],
                "scoped_ordinal": entry["scoped_ordinal"],
                "target_path_sha256": entry["target_path_sha256"],
            }
        )
    payload = canonical_json({"documents": documents})
    return payload, framed_sha256(DOMAIN_DOCUMENT_PLAN, payload)


def checkpoint_binding(
    *,
    classifier_sha256: str,
    document_plan_sha256: str,
    h1_receipt_sha256: str,
    mapping_sha256: str,
    projected_manifest_sha256: str,
    refusal_contract_sha256: str,
    scope_sha256: str,
    scoped_rows_sha256: str,
) -> tuple[bytes, str]:
    """Frame the checkpoint binding.

    No completed ordinal belongs in the binding: progress is sealed by the
    shard hash chain, so the binding is a pure function of the run's inputs.
    """
    payload = canonical_json(
        {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "classifier_sha256": _require_prefixed(classifier_sha256),
            "document_plan_sha256": _require_prefixed(document_plan_sha256),
            "h1_receipt_sha256": _require_prefixed(h1_receipt_sha256),
            "immutable_shard_contract_version": IMMUTABLE_SHARD_CONTRACT_VERSION,
            "limits": dict(LIMITS),
            "mapping_sha256": _require_prefixed(mapping_sha256),
            "privacy_policy": PRIVACY_POLICY,
            "projected_manifest_sha256": _require_prefixed(
                projected_manifest_sha256
            ),
            "refusal_contract_sha256": _require_prefixed(refusal_contract_sha256),
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "scope_sha256": _require_prefixed(scope_sha256),
            "scoped_rows_sha256": _require_prefixed(scoped_rows_sha256),
            "taxonomy": TAXONOMY,
            "tool": TOOL,
            "version": VERSION,
        }
    )
    return payload, framed_sha256(DOMAIN_CHECKPOINT_BINDING, payload)


def checkpoint_row_binding(row: dict[str, Any]) -> tuple[bytes, bytes]:
    """Return ``(row_json, row_sha256_blob)`` for one completed checkpoint row.

    ``row_sha256`` is exactly the 32 raw digest bytes; text or hex storage is
    invalid. No raw register, row id, path, fingerprint, prose, persona,
    evidence vector, warning, or free-text metadata is stored.

    ``declared_family`` is in ``D``, ``classified_family`` is in ``F`` or null,
    and ``refusal_reason`` is in ``R`` or null, with exactly one of the latter
    two non-null. ``unknown`` is a declared value only: it is never a
    classified family.
    """
    if set(row) != {
        "manifest_ordinal",
        "projected_row_sha256",
        "content_sha256",
        "document_bytes",
        "words",
        "declared_family",
        "classified_family",
        "refusal_reason",
    }:
        raise InternalError()
    _require_int(row["manifest_ordinal"], 0, INT64_MAX)
    _require_prefixed(row["projected_row_sha256"])
    _require_prefixed(row["content_sha256"])
    _require_int(row["document_bytes"], 0, MAX_DOCUMENT_BYTES)
    _require_int(row["words"], 0, INT64_MAX)
    if row["declared_family"] not in H1_DECLARED_FAMILIES:
        raise InternalError()
    classified = row["classified_family"]
    refusal = row["refusal_reason"]
    if classified is not None and classified not in H1_CLASSIFIED_FAMILIES:
        raise InternalError()
    if refusal is not None and refusal not in H1_REFUSAL_REASONS:
        raise InternalError()
    if (classified is None) == (refusal is None):
        raise InternalError()
    row_json = canonical_json(row)
    return row_json, hashlib.sha256(row_json).digest()


def aggregate_delta_binding(delta: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the reassembled six-key aggregate delta object.

    This is a structural and value-domain check only: the exact key sets of
    ``counts`` and of the five fixed inventories, and the closed cell shape and
    unsigned-64-bit integer domain of every leaf. The cross-cell equations
    (row sums, per-shard document ceilings, match-bucket derivation) belong to
    the runner increment that produces the deltas, not to this encoder.
    """
    if set(delta) != {
        "counts",
        "declared_family_inventory",
        "classified_family_inventory",
        "declared_by_classified_family",
        "refusal_inventory",
        "match_inventory",
    }:
        raise InternalError()
    counts = delta["counts"]
    if type(counts) is not dict or set(counts) != SHARD_COUNT_KEYS:
        raise InternalError()
    for value in counts.values():
        _require_count(value)
    _require_inventory(delta["declared_family_inventory"], H1_DECLARED_FAMILIES)
    _require_inventory(
        delta["classified_family_inventory"], H1_CLASSIFIED_FAMILIES
    )
    crosstab = delta["declared_by_classified_family"]
    if type(crosstab) is not dict or set(crosstab) != set(H1_DECLARED_FAMILIES):
        raise InternalError()
    for inner in crosstab.values():
        _require_inventory(inner, H1_CLASSIFIED_FAMILIES)
    _require_inventory(delta["refusal_inventory"], H1_REFUSAL_REASONS)
    _require_inventory(delta["match_inventory"], MATCH_DOMAIN)
    payload = canonical_json(delta)
    return payload, framed_sha256(DOMAIN_AGGREGATE_DELTA, payload)


def shard_binding(
    *,
    aggregate_delta_sha256: str,
    metadata: dict[str, Any],
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the logical shard payload.

    The hash binds logical content and makes no claim about cross-runtime
    SQLite byte determinism.

    Every metadata value is domain-checked, not merely present: the binding
    digest is prefixed, ``prior_shard_sha256`` is null or prefixed,
    ``shard_number`` is in ``[0, 399]``, ``first_scoped_ordinal`` is in
    ``[0, 99_999]``, ``next_scoped_ordinal`` is in ``[1, 100_000]`` and exceeds
    the first ordinal by ``[1, 250]``, and ``kind``/``schema_version`` are the
    frozen literals. The row list carries ``[1, 250]`` rows whose scoped
    ordinals are contiguous from ``first_scoped_ordinal``, and
    ``next_scoped_ordinal`` is exactly one past the last of them; a shard with
    no rows, a gap, or a next ordinal that disagrees with the row count cannot
    be framed. An empty scope creates no shard at all.
    """
    if set(metadata) != {
        "checkpoint_binding_sha256",
        "first_scoped_ordinal",
        "kind",
        "next_scoped_ordinal",
        "prior_shard_sha256",
        "schema_version",
        "shard_number",
    }:
        raise InternalError()
    binding_digest = _require_prefixed(metadata["checkpoint_binding_sha256"])
    prior = metadata["prior_shard_sha256"]
    if prior is not None:
        _require_prefixed(prior)
    shard_number = _require_int(
        metadata["shard_number"], 0, MAX_FINAL_SHARDS - 1
    )
    first = _require_int(
        metadata["first_scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1
    )
    following = _require_int(
        metadata["next_scoped_ordinal"], 1, MAX_SCOPED_DOCUMENTS
    )
    # This bound, the row-count bound, and the ``following == first + len``
    # equality below are each a separate spec clause (metadata domain, shard
    # size, and shard/row agreement), and any two of them imply the third.
    # They are kept separate so each clause has its own refusal site.
    if not (1 <= following - first <= SHARD_ROWS):
        raise InternalError()
    if metadata["kind"] != CHECKPOINT_KIND:
        raise InternalError()
    if metadata["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise InternalError()

    ordered_rows = []
    previous = -1
    for offset, entry in enumerate(rows):
        if set(entry) != {"row_json_sha256", "scoped_ordinal"}:
            raise InternalError()
        ordinal = _require_int(
            entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1
        )
        if ordinal != first + offset or ordinal <= previous:
            raise InternalError()
        previous = ordinal
        ordered_rows.append(
            {
                "row_json_sha256": _require_prefixed(entry["row_json_sha256"]),
                "scoped_ordinal": ordinal,
            }
        )
    if not (1 <= len(ordered_rows) <= SHARD_ROWS):
        raise InternalError()
    if following != first + len(ordered_rows):
        raise InternalError()

    payload = canonical_json(
        {
            "aggregate_delta_sha256": _require_prefixed(aggregate_delta_sha256),
            "metadata": {
                "checkpoint_binding_sha256": binding_digest,
                "first_scoped_ordinal": first,
                "kind": CHECKPOINT_KIND,
                "next_scoped_ordinal": following,
                "prior_shard_sha256": prior,
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "shard_number": shard_number,
            },
            "rows": ordered_rows,
        }
    )
    return payload, framed_sha256(DOMAIN_SHARD, payload)


# --------------------------------------------------------------------------
# Zero-filled fixed inventory domains
# --------------------------------------------------------------------------


def zero_cell() -> dict[str, int]:
    """Return the fixed zero cell. Every inventory cell has exactly this shape."""
    return {"documents": 0, "words": 0}


def zero_inventories(
    declared: tuple[str, ...],
    classified: tuple[str, ...],
    refusals: tuple[str, ...],
) -> dict[str, Any]:
    """Return the five zero-filled fixed inventory domains.

    There are no percentages, rates, shares, entropies, effective-mode counts,
    thresholds, bands, ranks, dominant-family labels, or mixture flags: these
    are count buckets only.
    """
    return {
        "declared_family_inventory": {name: zero_cell() for name in declared},
        "classified_family_inventory": {name: zero_cell() for name in classified},
        "declared_by_classified_family": {
            outer: {inner: zero_cell() for inner in classified}
            for outer in declared
        },
        "refusal_inventory": {name: zero_cell() for name in refusals},
        "match_inventory": {name: zero_cell() for name in MATCH_DOMAIN},
    }


# --------------------------------------------------------------------------
# Strict H1 receipt read
# --------------------------------------------------------------------------


def _decode_strict_json(data: bytes) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PolicyRefused()
            out[key] = value
        return out

    def reject_constant(_: str) -> None:
        raise PolicyRefused()

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PolicyRefused() from exc
    if text.startswith("﻿"):
        raise PolicyRefused()
    try:
        return json.loads(
            text, object_pairs_hook=pairs_hook, parse_constant=reject_constant
        )
    except (json.JSONDecodeError, RecursionError, ValueError, TypeError) as exc:
        raise PolicyRefused() from exc


def _valid_receipt_string(value: Any) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise PolicyRefused()
    if len(value.encode("utf-8")) > 131_072:
        raise PolicyRefused()
    for char in value:
        code = ord(char)
        if (
            code == 0
            or code < 0x20
            or 0x7F <= code <= 0x9F
            or 0xD800 <= code <= 0xDFFF
            or char in BIDI_CONTROLS
        ):
            raise PolicyRefused()
    return value


def _guard_receipt_tree(root: Any) -> None:
    stack: list[tuple[Any, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise PolicyRefused()
        if type(value) is str:
            _valid_receipt_string(value)
        elif type(value) is dict:
            for key, child in value.items():
                _valid_receipt_string(key)
                nodes += 1
                if nodes > 100_000:
                    raise PolicyRefused()
                stack.append((child, depth + 1))
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        elif value is None or type(value) in (bool, int, float):
            if type(value) is float and not math.isfinite(value):
                raise PolicyRefused()
        else:
            raise PolicyRefused()


def read_h1_receipt(path: os.PathLike[str] | str) -> tuple[dict[str, Any], bytes]:
    """Read and canonically verify the H1 closeout receipt exactly once.

    Reads at most :data:`MAX_H1_RECEIPT_BYTES` from a direct non-symlink
    regular file, decodes strict UTF-8 with no BOM, parses with duplicate-key
    and non-finite rejection, and requires the input bytes to equal the
    canonical re-encoding plus one terminal LF.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name == "posix" and not nofollow:
        raise PolicyRefused()
    descriptor = -1
    try:
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or getattr(named, "st_reparse_tag", 0):
            raise PolicyRefused()
        descriptor = os.open(path, flags | nofollow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            int(named.st_dev),
            int(named.st_ino),
        ) != (int(info.st_dev), int(info.st_ino)):
            raise PolicyRefused()
        data = os.read(descriptor, MAX_H1_RECEIPT_BYTES + 1)
        if len(data) > MAX_H1_RECEIPT_BYTES or os.read(descriptor, 1):
            raise PolicyRefused()
    except (OSError, ValueError, MemoryError) as exc:
        raise PolicyRefused() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    value = _decode_strict_json(data)
    _guard_receipt_tree(value)
    if type(value) is not dict:
        raise PolicyRefused()
    # A receipt that cannot even be canonically encoded (a float, an
    # out-of-range integer, a non-NFC string) is an H1-identity failure, not an
    # H2 internal failure: re-raise it into the identity taxonomy.
    try:
        canonical = canonical_json(value)
    except InternalError as exc:
        raise PolicyRefused() from exc
    if data != canonical + b"\n":
        raise PolicyRefused()
    return value, data


_RECEIPT_TOP_KEYS = frozenset(
    {
        "schema_version",
        "landed_commit",
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
        "ci",
        "spec_sha256",
        "refusal_spec_path",
        "refusal_spec_sha256",
        "base_classifier_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
        "taxonomy",
    }
)
_RECEIPT_REVIEW_KEYS = frozenset({"reviewed_head", "verdict"})
_RECEIPT_CI_KEYS = frozenset(
    {
        "attempt",
        "branch",
        "event",
        "head",
        "required_jobs",
        "result",
        "run_id",
        "workflow_name",
        "workflow_path",
        "workflow_sha256",
    }
)
_RECEIPT_HEX_FIELDS = (
    "spec_sha256",
    "refusal_spec_sha256",
    "base_classifier_sha256",
    "classifier_sha256",
    "mapping_sha256",
    "refusal_contract_sha256",
)


def _hex_field(value: Any, length: int) -> str:
    if type(value) is not str or len(value) != length:
        raise PolicyRefused()
    if any(char not in "0123456789abcdef" for char in value):
        raise PolicyRefused()
    return value


def validate_h1_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete ``setec-h1-landing-receipt/2`` schema.

    Every absent, extra, or wrong-type field refuses. H2 does not infer review
    status from Git history or runtime behavior: ``verdict`` is a human
    repository-governance attestation whose exact artifact bytes the CI-side
    checker already verified.
    """
    if set(receipt) != _RECEIPT_TOP_KEYS:
        raise PolicyRefused()
    if receipt["schema_version"] != H1_RECEIPT_SCHEMA_VERSION:
        raise PolicyRefused()
    _hex_field(receipt["landed_commit"], 40)
    for key in _RECEIPT_HEX_FIELDS:
        _hex_field(receipt[key], 64)
    if receipt["landed_commit"] != H1_LANDED_COMMIT:
        raise PolicyRefused()
    if receipt["spec_sha256"] != H1_SPEC_SHA256:
        raise PolicyRefused()
    if receipt["refusal_spec_path"] != H1_REFUSAL_SPEC_PATH:
        raise PolicyRefused()
    if receipt["refusal_spec_sha256"] != H1_REFUSAL_SPEC_SHA256:
        raise PolicyRefused()
    if receipt["base_classifier_sha256"] != H1_BASE_CLASSIFIER_SHA256:
        raise PolicyRefused()
    # Parity with the CI-side gate: the receipt's three H1 identity digests are
    # pinned to the same constants that checker holds, so a receipt that agrees
    # only with itself (regenerated around a drifted classifier) refuses here.
    if receipt["classifier_sha256"] != H1_FINAL_CLASSIFIER_SHA256:
        raise PolicyRefused()
    if receipt["mapping_sha256"] != H1_MAPPING_SHA256:
        raise PolicyRefused()
    if receipt["refusal_contract_sha256"] != H1_REFUSAL_CONTRACT_SHA256:
        raise PolicyRefused()
    if receipt["taxonomy"] != TAXONOMY:
        raise PolicyRefused()

    for key in (
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
    ):
        review = receipt[key]
        if type(review) is not dict or set(review) != _RECEIPT_REVIEW_KEYS:
            raise PolicyRefused()
        _hex_field(review["reviewed_head"], 40)
        if review["verdict"] != "READY":
            raise PolicyRefused()

    ci = receipt["ci"]
    if type(ci) is not dict or set(ci) != _RECEIPT_CI_KEYS:
        raise PolicyRefused()
    for key in ("run_id", "attempt"):
        if type(ci[key]) is not int or not (1 <= ci[key] <= INT64_MAX):
            raise PolicyRefused()
    _hex_field(ci["head"], 40)
    _hex_field(ci["workflow_sha256"], 64)
    if type(ci["required_jobs"]) is not list:
        raise PolicyRefused()
    if (
        ci["branch"] != "main"
        or ci["event"] != "push"
        or ci["result"] != "PASS"
        or ci["workflow_name"] != "tests"
        or ci["workflow_path"] != H1_WORKFLOW_PATH
        or tuple(ci["required_jobs"]) != H1_REQUIRED_JOBS
        or ci["workflow_sha256"] not in H1_WORKFLOW_SHA256_ALLOWLIST
        or ci["head"] != receipt["landed_commit"]
    ):
        raise PolicyRefused()
    return receipt


# --------------------------------------------------------------------------
# Receipt-bound H1 classifier
# --------------------------------------------------------------------------


class H1Binding:
    """The receipt-bound H1 namespace and its verified public identities.

    The classifier's exact source bytes are compiled and executed in a private
    module namespace. H2 consumes only the receipt-bound public symbols and
    never inspects ``_SCORERS``, decodes warning prose, or reconstructs H1
    behavior.
    """

    __slots__ = (
        "namespace",
        "receipt",
        "receipt_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
        "families",
        "declared_domain",
        "classified_domain",
        "refusal_domain",
    )

    def __init__(
        self,
        *,
        namespace: dict[str, Any],
        receipt: dict[str, Any],
        receipt_sha256: str,
        classifier_sha256: str,
        mapping_sha256: str,
        refusal_contract_sha256: str,
    ) -> None:
        self.namespace = namespace
        self.receipt = receipt
        self.receipt_sha256 = receipt_sha256
        self.classifier_sha256 = classifier_sha256
        self.mapping_sha256 = mapping_sha256
        self.refusal_contract_sha256 = refusal_contract_sha256
        # The value domains are the receipt-pinned H1 identity, not whatever
        # the bound namespace happens to export. Deriving ``D`` from the
        # namespace and then validating against that same namespace would be
        # circular, so the tuples are equality-pinned to the module constants
        # first; this is also the spec's ``D == REGISTER_FAMILIES`` freeze.
        families = namespace.get("REGISTER_FAMILIES")
        if type(families) is not tuple or families != H1_REGISTER_FAMILIES:
            raise PolicyRefused()
        refusals = namespace.get("REGISTER_REFUSAL_REASONS")
        if type(refusals) is not tuple or refusals != H1_REFUSAL_REASONS:
            raise PolicyRefused()
        self.families = H1_REGISTER_FAMILIES
        self.classified_domain = H1_CLASSIFIED_FAMILIES
        self.declared_domain = H1_DECLARED_FAMILIES
        self.refusal_domain = H1_REFUSAL_REASONS

    def resolve_family(self, value: str | None) -> str:
        """Resolve declared manifest metadata through H1's public mapping."""
        try:
            resolved = self.namespace["resolve_family"](value)
        except Exception as exc:  # noqa: BLE001 - closed controlled refusal
            raise PolicyRefused() from exc
        if type(resolved) is not str or resolved not in self.declared_domain:
            raise PolicyRefused()
        return resolved

    def classify(self, text: str, *, min_words: int) -> dict[str, Any]:
        """Call H1 with no hint and validate its complete public result."""
        try:
            result = self.namespace["classify_register"](
                text, min_words=min_words
            )
        except Exception as exc:  # noqa: BLE001 - closed controlled refusal
            raise PolicyRefused() from exc
        return self.validate_classification(result, min_words=min_words)

    def validate_classification(
        self, result: Any, *, min_words: int
    ) -> dict[str, Any]:
        """Validate the closed eight-key H1 result and refusal biconditional.

        H2 validates the complete public result rather than silently ignoring
        fields, but recomputes no score, rank, threshold, tie, or secondary
        band, and never branches on ``warning`` prose.
        """
        classified = self.classified_domain
        refusals = self.refusal_domain
        if type(result) is not dict or set(result) != CLASSIFICATION_KEYS:
            raise PolicyRefused()
        if result["taxonomy"] != TAXONOMY:
            raise PolicyRefused()

        primary = result["primary"]
        if type(primary) is not str or primary not in (
            *classified,
            "unknown",
        ):
            raise PolicyRefused()

        reason = result["refusal_reason"]
        if reason is not None and (
            type(reason) is not str or reason not in refusals
        ):
            raise PolicyRefused()
        if (primary == "unknown") != (reason is not None):
            raise PolicyRefused()

        confidence = result["confidence"]
        if (
            type(confidence) is not float
            or not math.isfinite(confidence)
            or not (0.0 <= confidence <= 1.0)
        ):
            raise PolicyRefused()

        secondary = result["secondary"]
        if type(secondary) is not list or len(secondary) > MAX_SECONDARY:
            raise PolicyRefused()
        seen: set[str] = set()
        for item in secondary:
            if type(item) is not str or item not in classified or item in seen:
                raise PolicyRefused()
            seen.add(item)
        if primary in classified and primary in seen:
            raise PolicyRefused()

        warning = result["warning"]
        if warning is not None:
            if type(warning) is not str:
                raise PolicyRefused()
            try:
                encoded = warning.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise PolicyRefused() from exc
            if len(encoded) > MAX_WARNING_BYTES:
                raise PolicyRefused()

        evidence = self._validate_evidence(result["evidence"])
        self._validate_scores(
            result["scores"],
            reason=reason,
            n_words=evidence["n_words"],
            min_words=min_words,
        )
        return result

    def _validate_evidence(self, evidence: Any) -> dict[str, Any]:
        if type(evidence) is not dict:
            raise PolicyRefused()
        keys = set(evidence)
        if keys == set(ZERO_WORD_EVIDENCE_KEYS):
            expected_zero_word = True
        elif keys == set(FULL_EVIDENCE_KEYS):
            expected_zero_word = False
        else:
            raise PolicyRefused()

        for key in EVIDENCE_INTEGER_KEYS & keys:
            value = evidence[key]
            if type(value) is not int or not (0 <= value <= INT64_MAX):
                raise PolicyRefused()
        for key in keys - EVIDENCE_INTEGER_KEYS:
            value = evidence[key]
            if (
                type(value) is not float
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise PolicyRefused()
        if "dialogue_ratio" in keys and evidence["dialogue_ratio"] > 1.0:
            raise PolicyRefused()

        if expected_zero_word:
            # Only ``n_words`` is pinned here. Whitespace- or punctuation-only
            # input legitimately reports zero words with a nonzero character
            # count, so ``n_chars`` is bounded but not forced to zero; the
            # spec's "n_chars may be zero only in the zero-word shape" clause
            # constrains the full shape below, not this one.
            if evidence["n_words"] != 0:
                raise PolicyRefused()
        else:
            if (
                evidence["n_words"] < 1
                or evidence["n_sentences"] < 1
                or evidence["n_paragraphs"] < 1
                or evidence["n_chars"] < 1
            ):
                raise PolicyRefused()
        return evidence

    def _validate_scores(
        self,
        scores: Any,
        *,
        reason: str | None,
        n_words: int,
        min_words: int,
    ) -> None:
        if type(scores) is not dict:
            raise PolicyRefused()
        if reason == "short_text":
            if scores != {}:
                raise PolicyRefused()
            if n_words >= min_words:
                raise PolicyRefused()
            return
        if set(scores) != set(self.classified_domain):
            raise PolicyRefused()
        if n_words < min_words:
            raise PolicyRefused()
        for value in scores.values():
            if (
                type(value) is not float
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                raise PolicyRefused()


def mapping_binding(namespace: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the public register-family mapping from the H1 namespace.

    The payload has exactly ``canonical_register_to_family``,
    ``legacy_register_to_family``, ``register_families`` in public tuple order,
    and ``taxonomy``. At the landed H1 identity its canonical length is exactly
    1,147 bytes; the CI-side gate pins the same length, so any mapping that
    encodes to a different size refuses before it is framed.
    """
    families = namespace.get("REGISTER_FAMILIES")
    canonical = namespace.get("CANONICAL_REGISTER_TO_FAMILY")
    legacy = namespace.get("LEGACY_REGISTER_TO_FAMILY")
    if (
        namespace.get("REGISTER_TAXONOMY") != TAXONOMY
        or type(families) is not tuple
        or type(canonical) is not dict
        or type(legacy) is not dict
        or namespace.get("KNOWN_REGISTERS") != families
        or any(type(item) is not str for item in families)
        or len(set(families)) != len(families)
        or "unknown" not in families
        or any(
            type(key) is not str or type(val) is not str
            for key, val in canonical.items()
        )
        or any(
            type(key) is not str or type(val) is not str
            for key, val in legacy.items()
        )
        or any(
            val not in families
            for val in (*canonical.values(), *legacy.values())
        )
    ):
        raise PolicyRefused()
    payload = canonical_json(
        {
            "canonical_register_to_family": canonical,
            "legacy_register_to_family": legacy,
            "register_families": list(families),
            "taxonomy": TAXONOMY,
        }
    )
    if len(payload) != 1_147:
        raise PolicyRefused()
    return payload, framed_sha256(DOMAIN_MAPPING, payload)


REFUSAL_CONTRACT_PAYLOAD = (
    b'{"field":"refusal_reason","null_when":"scored_family","reasons":'
    b'["short_text","all_weak","exact_top_tie"],'
    b'"taxonomy":"register_families/v2"}'
)


def refusal_contract_binding(namespace: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the public refusal contract from the H1 namespace.

    The payload is constructed from the exported tuple, field/null rule, and
    taxonomy, then equality-checked against the exact 140 spec bytes before
    hashing. Copying the expected digest without constructing the payload is
    invalid.
    """
    reasons = namespace.get("REGISTER_REFUSAL_REASONS")
    if reasons != H1_REFUSAL_REASONS:
        raise PolicyRefused()
    payload = canonical_json(
        {
            "field": "refusal_reason",
            "null_when": "scored_family",
            "reasons": list(reasons),
            "taxonomy": TAXONOMY,
        }
    )
    if payload != REFUSAL_CONTRACT_PAYLOAD or len(payload) != 140:
        raise PolicyRefused()
    return payload, framed_sha256(DOMAIN_REFUSAL_CONTRACT, payload)


def _validate_h1_callables(namespace: dict[str, Any]) -> None:
    """Exact-validate the receipt-bound callable names, kinds, and defaults."""
    import inspect

    for name in H1_PUBLIC_SYMBOLS:
        if name not in namespace:
            raise PolicyRefused()

    resolve = namespace["resolve_family"]
    classify = namespace["classify_register"]
    if not callable(resolve) or not callable(classify):
        raise PolicyRefused()
    try:
        resolve_params = list(inspect.signature(resolve).parameters.values())
        classify_params = list(inspect.signature(classify).parameters.values())
    except (TypeError, ValueError) as exc:
        raise PolicyRefused() from exc

    if len(resolve_params) != 1:
        raise PolicyRefused()
    only = resolve_params[0]
    if (
        only.name != "value"
        or only.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
        or only.default is not inspect.Parameter.empty
    ):
        raise PolicyRefused()

    expected = (
        ("text", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("hint", inspect.Parameter.KEYWORD_ONLY, None),
        ("min_words", inspect.Parameter.KEYWORD_ONLY, 100),
    )
    if len(classify_params) != len(expected):
        raise PolicyRefused()
    for param, (name, kind, default) in zip(classify_params, expected):
        if param.name != name or param.kind is not kind:
            raise PolicyRefused()
        if default is inspect.Parameter.empty:
            if param.default is not inspect.Parameter.empty:
                raise PolicyRefused()
        elif type(param.default) is not type(default) or param.default != default:
            raise PolicyRefused()


def load_h1_binding(
    *,
    receipt_path: os.PathLike[str] | str,
    classifier_path: os.PathLike[str] | str,
    expected_receipt_sha256: str = H1_RECEIPT_SHA256,
) -> H1Binding:
    """Bind the H1 classifier to the committed closeout receipt.

    Order is fixed:

    1. strict-read the receipt and check its pinned raw digest and its exact
       schema, which equality-pins ``classifier_sha256``, ``mapping_sha256``,
       and ``refusal_contract_sha256`` to the frozen module constants;
    2. read the expected sibling classifier source once under the 1 MiB ceiling
       and check its raw digest against the receipt's post-follow-on
       ``classifier_sha256``. That raw-digest check is the only gate on
       execution: nothing is compiled or executed until it passes;
    3. compile and execute those exact source bytes in a private namespace and
       validate the receipt-bound callables; and
    4. immediately after execution and before any classifier call, derive the
       public mapping and refusal-contract digests *from* the executed
       namespace and check them against the receipt.

    The mapping and refusal-contract digests cannot gate the execution, because
    they are computed from the executed namespace. What they gate is every
    later use of the binding.
    """
    receipt, receipt_bytes = read_h1_receipt(receipt_path)
    receipt_digest = raw_sha256(receipt_bytes)
    if receipt_digest != expected_receipt_sha256:
        raise PolicyRefused()
    validate_h1_receipt(receipt)

    source = _read_classifier_source(classifier_path)
    classifier_digest = raw_sha256(source)
    if classifier_digest != receipt["classifier_sha256"]:
        raise PolicyRefused()

    namespace = _execute_classifier(source, classifier_path)
    _validate_h1_callables(namespace)

    _, mapping_digest = mapping_binding(namespace)
    if mapping_digest != receipt["mapping_sha256"]:
        raise PolicyRefused()
    _, refusal_digest = refusal_contract_binding(namespace)
    if refusal_digest != receipt["refusal_contract_sha256"]:
        raise PolicyRefused()

    return H1Binding(
        namespace=namespace,
        receipt=receipt,
        receipt_sha256=receipt_digest,
        classifier_sha256=classifier_digest,
        mapping_sha256=mapping_digest,
        refusal_contract_sha256=refusal_digest,
    )


def _read_classifier_source(path: os.PathLike[str] | str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name == "posix" and not nofollow:
        raise PolicyRefused()
    descriptor = -1
    try:
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or getattr(named, "st_reparse_tag", 0):
            raise PolicyRefused()
        descriptor = os.open(path, flags | nofollow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            int(named.st_dev),
            int(named.st_ino),
        ) != (int(info.st_dev), int(info.st_ino)):
            raise PolicyRefused()
        data = os.read(descriptor, MAX_CLASSIFIER_SOURCE_BYTES + 1)
        if len(data) > MAX_CLASSIFIER_SOURCE_BYTES or os.read(descriptor, 1):
            raise PolicyRefused()
    except (OSError, ValueError, MemoryError) as exc:
        raise PolicyRefused() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return data


def _execute_classifier(
    source: bytes, path: os.PathLike[str] | str
) -> dict[str, Any]:
    try:
        text = source.decode("utf-8", errors="strict")
        namespace: dict[str, Any] = {"__name__": "_h2_bound_register_classifier"}
        exec(compile(text, os.fspath(path), "exec"), namespace, namespace)
    except Exception as exc:  # noqa: BLE001 - closed controlled refusal
        raise PolicyRefused() from exc
    return namespace


def default_h1_paths() -> tuple[Path, Path]:
    """Return ``(receipt_path, classifier_path)`` for this script's plugin.

    The receipt is the plugin's ``references/`` copy; the classifier is the
    sibling module in ``scripts/``. The return order matches the keyword order
    of :func:`load_h1_binding`.
    """
    scripts = Path(__file__).resolve().parent
    classifier = scripts / CLASSIFIER_FILENAME
    receipt = scripts.parent / "references" / RECEIPT_FILENAME
    return receipt, classifier


# --------------------------------------------------------------------------
# Increment D1: aggregation, report, claim posture guard, and output envelopes
#
# Everything below is pure in-memory construction over the encoders and the
# receipt-bound H1 domains above. It reads no file, opens no checkpoint, and
# performs no I/O apart from the one terminal committed-success stdout sink at
# the end of the section.
# --------------------------------------------------------------------------

#: The additive per-shard/whole-run count keys. ``input_rows`` is deliberately
#: absent: it is the whole-run prefilter projection object-row count, not a
#: per-shard quantity, and summing it over shards would double-count. The
#: spec's normative one-row aggregate-delta vector pins this exact key set.
AGGREGATE_COUNT_KEYS = (
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

#: The exact closed report ``counts`` key set.
REPORT_COUNT_KEYS = ("input_rows",) + AGGREGATE_COUNT_KEYS

#: The five fixed inventory names, in report order.
INVENTORY_KEYS = (
    "declared_family_inventory",
    "classified_family_inventory",
    "declared_by_classified_family",
    "refusal_inventory",
    "match_inventory",
)

#: The six-key aggregate object ``aggregate_delta_binding`` frames.
AGGREGATE_KEYS = ("counts",) + INVENTORY_KEYS

#: The exact closed report key set, in the order the spec enumerates it.
#: The spec's enumerated block has 23 entries.
REPORT_KEYS = (
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
    *INVENTORY_KEYS,
    "assumptions",
    "claim_license",
    "warnings",
)

#: The eight report digest fields, in report order.
REPORT_DIGEST_KEYS = (
    "projected_manifest_sha256",
    "scoped_rows_sha256",
    "document_plan_sha256",
    "h1_receipt_sha256",
    "classifier_sha256",
    "mapping_sha256",
    "refusal_contract_sha256",
    "checkpoint_binding_sha256",
)

#: The exact closed report ``scope`` key set. It never carries a path, corpus
#: identifier, raw persona, or free-text metadata.
REPORT_SCOPE_KEYS = (
    "ai_status",
    "min_words",
    "persona_selected",
    "scope_sha256",
    "split",
    "use",
)

#: The exact fixed ``assumptions`` object. These four values are frozen text,
#: not run-dependent commentary.
REPORT_ASSUMPTIONS: dict[str, str] = {
    "purpose": "aggregate_hygiene_inventory_for_hand_check",
    "classifier_posture": "uncalibrated_heuristic",
    "register_role": "confounded_proxy",
    "reporting_status": "not_calibrated_or_reportable",
}

CLAIM_TASK_SURFACE = "validation"
CLAIM_LICENSES = (
    "Aggregate register-family count inventory for a hand-check of the "
    "explicitly scoped manifest slice."
)
CLAIM_DOES_NOT_LICENSE = (
    "Multimodality or semantic-mode explanation; calibration, accuracy, or a "
    "reportable distribution; source, source-family, or provenance analysis; "
    "corpus selection, exclusion, disposition, registration, activation, "
    "retagging, publication, or training authorization."
)
CLAIM_CAVEAT = (
    "Register family is a confounded heuristic proxy; this inventory can only "
    "prompt a human hand-check."
)


def fixed_claim_license() -> ClaimLicense:
    """Return the one frozen success ClaimLicense.

    Every field other than the three fixed strings and the single caveat keeps
    its exact empty/null dataclass default; the report pins the complete
    ``to_dict()`` result.
    """
    return ClaimLicense(
        task_surface=CLAIM_TASK_SURFACE,
        licenses=CLAIM_LICENSES,
        does_not_license=CLAIM_DOES_NOT_LICENSE,
        additional_caveats=[CLAIM_CAVEAT],
    )


# --------------------------------------------------------------------------
# Fixed aggregate domains
# --------------------------------------------------------------------------


class RegisterDomains:
    """The frozen ``D``/``F``/``R`` domains of one receipt-bound H1 binding.

    ``declared`` is ``F + ("unknown",)``; ``unknown`` is never a classified
    family and never a crosstab column.
    """

    __slots__ = ("declared", "classified", "refusals")

    def __init__(
        self,
        declared: tuple[str, ...],
        classified: tuple[str, ...],
        refusals: tuple[str, ...],
    ) -> None:
        declared = tuple(declared)
        classified = tuple(classified)
        refusals = tuple(refusals)
        for domain in (declared, classified, refusals):
            if not domain or len(set(domain)) != len(domain):
                raise InternalError()
            if any(type(name) is not str or not name for name in domain):
                raise InternalError()
        if declared != classified + ("unknown",):
            raise InternalError()
        if "unknown" in classified:
            raise InternalError()
        self.declared = declared
        self.classified = classified
        self.refusals = refusals

    @classmethod
    def from_binding(cls, binding: H1Binding) -> RegisterDomains:
        """Build the domains from a loaded, receipt-bound H1 namespace."""
        return cls(
            binding.declared_domain,
            binding.classified_domain,
            binding.refusal_domain,
        )

    def validate(self) -> RegisterDomains:
        """Re-assert the eager ``__init__`` invariants and return ``self``.

        The checkpoint layer calls this at create/resume/decode boundaries as
        a cheap defense-in-depth re-check on a value that may have crossed a
        codec seam.
        """
        if (
            type(self.declared) is not tuple
            or type(self.classified) is not tuple
            or type(self.refusals) is not tuple
            or not self.classified
            or not self.refusals
            or self.declared != self.classified + ("unknown",)
            or "unknown" in self.classified
            or len(set(self.declared)) != len(self.declared)
            or len(set(self.refusals)) != len(self.refusals)
            or any(type(item) is not str or not item for item in self.declared)
            or any(type(item) is not str or not item for item in self.refusals)
        ):
            raise InternalError()
        return self

    def zero(self) -> dict[str, Any]:
        """Return the five zero-filled fixed inventories."""
        return zero_inventories(self.declared, self.classified, self.refusals)


def _zero_counts() -> dict[str, int]:
    return {key: 0 for key in AGGREGATE_COUNT_KEYS}


def _require_count(value: Any) -> int:
    """Accept only a non-Boolean, non-negative, signed-64-bit JSON integer."""
    if type(value) is not int or not (0 <= value <= INT64_MAX):
        raise InternalError()
    return value


def _require_cell(cell: Any) -> tuple[int, int]:
    if type(cell) is not dict or set(cell) != {"documents", "words"}:
        raise InternalError()
    return _require_count(cell["documents"]), _require_count(cell["words"])


def _bump(cell: dict[str, int], documents: int, words: int) -> None:
    cell["documents"] = _require_count(cell["documents"] + documents)
    cell["words"] = _require_count(cell["words"] + words)


# --------------------------------------------------------------------------
# Aggregate accumulator
# --------------------------------------------------------------------------


class RegisterAggregate:
    """Fixed-domain count accumulator for one shard or one whole run.

    The accumulator holds only counts. It never stores document text, a path,
    a corpus identifier, a per-row result, a percentage, a rate, an entropy, a
    threshold, a band, a rank, or a mixture flag. Word counts arrive only from
    an already-validated H1 ``evidence.n_words``; H2 has no tokenizer.
    """

    __slots__ = ("domains", "counts", "inventories")

    def __init__(self, domains: RegisterDomains) -> None:
        if type(domains) is not RegisterDomains:
            raise InternalError()
        self.domains = domains
        self.counts = _zero_counts()
        self.inventories = domains.zero()

    # -- per-document accumulation -------------------------------------

    def add_document(
        self,
        *,
        declared_family: str,
        primary: str,
        refusal_reason: str | None,
        n_words: int,
        document_bytes: int,
    ) -> None:
        """Apply one completed scoped document to the fixed cells.

        ``declared_family`` is the H1 ``resolve_family`` result (``unknown``
        when the admissible declared metadata is absent/null or unresolvable).
        ``primary``/``refusal_reason`` come from an already-validated H1
        result. A refusal is recorded once in the refusal inventory and never
        in a family cell or the crosstab.
        """
        domains = self.domains
        if declared_family not in domains.declared:
            raise InternalError()
        if primary != "unknown" and primary not in domains.classified:
            raise InternalError()
        if refusal_reason is not None and refusal_reason not in domains.refusals:
            raise InternalError()
        if (primary == "unknown") != (refusal_reason is not None):
            raise InternalError()
        words = _require_count(n_words)
        size = _require_count(document_bytes)
        if size > MAX_DOCUMENT_BYTES:
            raise InternalError()

        counts = self.counts
        counts["scoped_documents"] = _require_count(counts["scoped_documents"] + 1)
        if counts["scoped_documents"] > MAX_SCOPED_DOCUMENTS:
            raise InternalError()
        counts["scoped_bytes"] = _require_count(counts["scoped_bytes"] + size)
        if counts["scoped_bytes"] > MAX_SCOPED_BYTES:
            raise InternalError()
        counts["scoped_words"] = _require_count(counts["scoped_words"] + words)

        inventories = self.inventories
        _bump(inventories["declared_family_inventory"][declared_family], 1, words)
        if declared_family == "unknown":
            counts["unresolved_declared_documents"] += 1
            counts["unresolved_declared_words"] += words
        else:
            counts["resolved_declared_documents"] += 1
            counts["resolved_declared_words"] += words

        if refusal_reason is not None:
            _bump(inventories["refusal_inventory"][refusal_reason], 1, words)
            _bump(inventories["match_inventory"]["unresolved"], 1, words)
            counts["refused_documents"] += 1
            counts["refused_words"] += words
            return

        _bump(inventories["classified_family_inventory"][primary], 1, words)
        _bump(
            inventories["declared_by_classified_family"][declared_family][primary],
            1,
            words,
        )
        counts["classified_documents"] += 1
        counts["classified_words"] += words
        if declared_family == "unknown":
            bucket = "unresolved"
        elif declared_family == primary:
            bucket = "same"
        else:
            bucket = "different"
        _bump(inventories["match_inventory"][bucket], 1, words)

    def add_h1_result(
        self,
        *,
        declared_family: str,
        result: dict[str, Any],
        document_bytes: int,
    ) -> None:
        """Apply one already-validated H1 result and its planned byte size."""
        if type(result) is not dict or set(result) != CLASSIFICATION_KEYS:
            raise InternalError()
        evidence = result["evidence"]
        if type(evidence) is not dict or "n_words" not in evidence:
            raise InternalError()
        self.add_document(
            declared_family=declared_family,
            primary=result["primary"],
            refusal_reason=result["refusal_reason"],
            n_words=evidence["n_words"],
            document_bytes=document_bytes,
        )

    # -- delta extraction and reassembly --------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return an independent copy of the six-key aggregate object."""
        return {
            "counts": dict(self.counts),
            "declared_family_inventory": {
                name: dict(cell)
                for name, cell in self.inventories[
                    "declared_family_inventory"
                ].items()
            },
            "classified_family_inventory": {
                name: dict(cell)
                for name, cell in self.inventories[
                    "classified_family_inventory"
                ].items()
            },
            "declared_by_classified_family": {
                outer: {inner: dict(cell) for inner, cell in row.items()}
                for outer, row in self.inventories[
                    "declared_by_classified_family"
                ].items()
            },
            "refusal_inventory": {
                name: dict(cell)
                for name, cell in self.inventories["refusal_inventory"].items()
            },
            "match_inventory": {
                name: dict(cell)
                for name, cell in self.inventories["match_inventory"].items()
            },
        }

    def shard_delta(self) -> dict[str, Any]:
        """Return the per-shard six-key delta.

        A shard seals at most ``SHARD_ROWS`` documents, so a delta claiming
        more is an in-memory construction violation.
        """
        if self.counts["scoped_documents"] > SHARD_ROWS:
            raise InternalError()
        delta = self.snapshot()
        validate_aggregate(delta, domains=self.domains)
        return delta

    def add_delta(self, delta: dict[str, Any]) -> None:
        """Add one sealed shard delta into this accumulator."""
        validate_aggregate(delta, domains=self.domains)
        for key in AGGREGATE_COUNT_KEYS:
            self.counts[key] = _require_count(
                self.counts[key] + delta["counts"][key]
            )
        for name in (
            "declared_family_inventory",
            "classified_family_inventory",
            "refusal_inventory",
            "match_inventory",
        ):
            for member, entry in delta[name].items():
                documents, words = _require_cell(entry)
                _bump(self.inventories[name][member], documents, words)
        for outer, row in delta["declared_by_classified_family"].items():
            for inner, entry in row.items():
                documents, words = _require_cell(entry)
                _bump(
                    self.inventories["declared_by_classified_family"][outer][inner],
                    documents,
                    words,
                )
        if self.counts["scoped_documents"] > MAX_SCOPED_DOCUMENTS:
            raise InternalError()
        if self.counts["scoped_bytes"] > MAX_SCOPED_BYTES:
            raise InternalError()


def reassemble_aggregate(
    deltas: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    domains: RegisterDomains,
) -> dict[str, Any]:
    """Add the sealed shard deltas in shard order and validate once.

    Report construction consumes this result. Addition is commutative, but the
    caller still supplies shards in ascending shard order so that a hole,
    reordering, or duplicate is a chain failure upstream rather than a silent
    arithmetic success here.
    """
    total = RegisterAggregate(domains)
    for delta in deltas:
        total.add_delta(delta)
    aggregate = total.snapshot()
    validate_aggregate(aggregate, domains=domains)
    return aggregate


# --------------------------------------------------------------------------
# Equation validation
# --------------------------------------------------------------------------


def validate_aggregate(
    aggregate: Any, *, domains: RegisterDomains
) -> dict[str, Any]:
    """Validate the six-key aggregate object's shape, domains, and equations.

    Every marginal and conservation equation in the spec's aggregate hygiene
    inventory is checked. Any disagreement is a violation of H2's own
    already-validated in-memory construction and refuses as ``InternalError``.
    """
    if type(aggregate) is not dict or set(aggregate) != set(AGGREGATE_KEYS):
        raise InternalError()
    counts = aggregate["counts"]
    if type(counts) is not dict or set(counts) != set(AGGREGATE_COUNT_KEYS):
        raise InternalError()
    for key in AGGREGATE_COUNT_KEYS:
        _require_count(counts[key])

    declared = aggregate["declared_family_inventory"]
    classified = aggregate["classified_family_inventory"]
    crosstab = aggregate["declared_by_classified_family"]
    refusals = aggregate["refusal_inventory"]
    matches = aggregate["match_inventory"]
    if type(declared) is not dict or tuple(sorted(declared)) != tuple(
        sorted(domains.declared)
    ):
        raise InternalError()
    if type(classified) is not dict or tuple(sorted(classified)) != tuple(
        sorted(domains.classified)
    ):
        raise InternalError()
    if type(refusals) is not dict or tuple(sorted(refusals)) != tuple(
        sorted(domains.refusals)
    ):
        raise InternalError()
    if type(matches) is not dict or tuple(sorted(matches)) != tuple(
        sorted(MATCH_DOMAIN)
    ):
        raise InternalError()
    if type(crosstab) is not dict or tuple(sorted(crosstab)) != tuple(
        sorted(domains.declared)
    ):
        raise InternalError()
    for row in crosstab.values():
        if type(row) is not dict or tuple(sorted(row)) != tuple(
            sorted(domains.classified)
        ):
            raise InternalError()

    def cell(mapping: dict[str, Any], name: str) -> tuple[int, int]:
        return _require_cell(mapping[name])

    def total(mapping: dict[str, Any], names: tuple[str, ...]) -> tuple[int, int]:
        documents = 0
        words = 0
        for name in names:
            one_documents, one_words = cell(mapping, name)
            documents += one_documents
            words += one_words
        return documents, words

    scoped = (counts["scoped_documents"], counts["scoped_words"])
    if counts["scoped_documents"] > MAX_SCOPED_DOCUMENTS:
        raise InternalError()
    if counts["scoped_bytes"] > MAX_SCOPED_BYTES:
        raise InternalError()

    # sum(D declared_family_inventory[*].m) = T_m
    if total(declared, domains.declared) != scoped:
        raise InternalError()
    # sum(A match_inventory[*].m) = T_m
    if total(matches, MATCH_DOMAIN) != scoped:
        raise InternalError()
    # sum(F classified_family_inventory[*].m) = counts.classified_m
    classified_total = total(classified, domains.classified)
    if classified_total != (
        counts["classified_documents"],
        counts["classified_words"],
    ):
        raise InternalError()
    # sum(R refusal_inventory[*].m) = counts.refused_m
    if total(refusals, domains.refusals) != (
        counts["refused_documents"],
        counts["refused_words"],
    ):
        raise InternalError()

    # Crosstab marginals. Every column marginal equals its classified family
    # cell; the grand total equals the classified totals; and no declared row
    # may carry more than that declared family's whole-inventory cell (the
    # remainder is exactly that family's refused share, which by construction
    # lives only in the refusal inventory).
    crosstab_documents = 0
    crosstab_words = 0
    for declared_name in domains.declared:
        row_documents = 0
        row_words = 0
        for classified_name in domains.classified:
            one_documents, one_words = cell(
                crosstab[declared_name], classified_name
            )
            row_documents += one_documents
            row_words += one_words
        declared_documents, declared_words = cell(declared, declared_name)
        if row_documents > declared_documents or row_words > declared_words:
            raise InternalError()
        crosstab_documents += row_documents
        crosstab_words += row_words
    if (crosstab_documents, crosstab_words) != classified_total:
        raise InternalError()
    for classified_name in domains.classified:
        column_documents = 0
        column_words = 0
        for declared_name in domains.declared:
            one_documents, one_words = cell(
                crosstab[declared_name], classified_name
            )
            column_documents += one_documents
            column_words += one_words
        if (column_documents, column_words) != cell(classified, classified_name):
            raise InternalError()

    # match_inventory["same"/"different"/"unresolved"]
    same_documents = 0
    same_words = 0
    different_documents = 0
    different_words = 0
    for declared_name in domains.classified:
        for classified_name in domains.classified:
            one_documents, one_words = cell(
                crosstab[declared_name], classified_name
            )
            if declared_name == classified_name:
                same_documents += one_documents
                same_words += one_words
            else:
                different_documents += one_documents
                different_words += one_words
    if (same_documents, same_words) != cell(matches, "same"):
        raise InternalError()
    if (different_documents, different_words) != cell(matches, "different"):
        raise InternalError()
    unresolved_documents = 0
    unresolved_words = 0
    for classified_name in domains.classified:
        one_documents, one_words = cell(crosstab["unknown"], classified_name)
        unresolved_documents += one_documents
        unresolved_words += one_words
    unresolved_documents += counts["refused_documents"]
    unresolved_words += counts["refused_words"]
    if (unresolved_documents, unresolved_words) != cell(matches, "unresolved"):
        raise InternalError()

    # Declared resolution and the count conservation equations.
    unknown_documents, unknown_words = cell(declared, "unknown")
    if (
        counts["unresolved_declared_documents"],
        counts["unresolved_declared_words"],
    ) != (unknown_documents, unknown_words):
        raise InternalError()
    if (
        counts["resolved_declared_documents"]
        + counts["unresolved_declared_documents"]
        != counts["scoped_documents"]
    ):
        raise InternalError()
    if (
        counts["resolved_declared_words"] + counts["unresolved_declared_words"]
        != counts["scoped_words"]
    ):
        raise InternalError()
    if (
        counts["classified_documents"] + counts["refused_documents"]
        != counts["scoped_documents"]
    ):
        raise InternalError()
    if (
        counts["classified_words"] + counts["refused_words"]
        != counts["scoped_words"]
    ):
        raise InternalError()
    for key in AGGREGATE_COUNT_KEYS:
        if key.endswith("_documents") and counts[key] > counts["scoped_documents"]:
            raise InternalError()
        if key.endswith("_words") and counts[key] > counts["scoped_words"]:
            raise InternalError()
    return aggregate


# --------------------------------------------------------------------------
# Report construction
# --------------------------------------------------------------------------


def build_report_scope(
    *,
    use: str | None,
    split: str | None,
    ai_status: str | None,
    min_words: int,
    persona_selected: bool,
    scope_sha256: str,
) -> dict[str, Any]:
    """Build the closed six-key report ``scope`` object.

    The raw persona filter never enters this object; only the separate private
    scope-binding digest, which itself commits the raw persona, does.
    """
    # Deferred import: see the module-header note on the one-way import rule.
    from manifest_validator import (  # type: ignore
        ALLOWED_AI_STATUS,
        ALLOWED_SPLIT,
        ALLOWED_USE,
    )

    if use is not None and (type(use) is not str or use not in ALLOWED_USE):
        raise InternalError()
    if split is not None and (type(split) is not str or split not in ALLOWED_SPLIT):
        raise InternalError()
    if ai_status is not None and (
        type(ai_status) is not str or ai_status not in ALLOWED_AI_STATUS
    ):
        raise InternalError()
    if type(persona_selected) is not bool:
        raise InternalError()
    return {
        "ai_status": ai_status,
        "min_words": _require_int(min_words, MIN_WORDS_FLOOR, MIN_WORDS_CEILING),
        "persona_selected": persona_selected,
        "scope_sha256": _require_prefixed(scope_sha256),
        "split": split,
        "use": use,
    }


def validate_report_scope(scope: Any) -> dict[str, Any]:
    """Validate an already-built report ``scope`` object."""
    if type(scope) is not dict or set(scope) != set(REPORT_SCOPE_KEYS):
        raise InternalError()
    return build_report_scope(
        use=scope["use"],
        split=scope["split"],
        ai_status=scope["ai_status"],
        min_words=scope["min_words"],
        persona_selected=scope["persona_selected"],
        scope_sha256=scope["scope_sha256"],
    )


def build_report(
    *,
    domains: RegisterDomains,
    projected_manifest_sha256: str,
    scoped_rows_sha256: str,
    document_plan_sha256: str,
    h1_receipt_sha256: str,
    classifier_sha256: str,
    mapping_sha256: str,
    refusal_contract_sha256: str,
    checkpoint_binding_sha256: str,
    scope: dict[str, Any],
    input_rows: int,
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    """Build the complete 23-key create-new private report.

    The report is a count inventory and nothing else: no percentage, rate,
    share, entropy, effective-mode count, threshold, band, rank, dominant
    family, mixture flag, or row-level result exists in it, and no aggregate
    in it is calibrated or reportable.
    """
    validate_aggregate(aggregate, domains=domains)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL,
        "version": VERSION,
        "taxonomy": TAXONOMY,
        "projected_manifest_sha256": projected_manifest_sha256,
        "scoped_rows_sha256": scoped_rows_sha256,
        "document_plan_sha256": document_plan_sha256,
        "h1_receipt_sha256": h1_receipt_sha256,
        "classifier_sha256": classifier_sha256,
        "mapping_sha256": mapping_sha256,
        "refusal_contract_sha256": refusal_contract_sha256,
        "checkpoint_binding_sha256": checkpoint_binding_sha256,
        "scope": validate_report_scope(scope),
        "limits": dict(LIMITS),
        "counts": {
            "input_rows": _require_count(input_rows),
            **{key: aggregate["counts"][key] for key in AGGREGATE_COUNT_KEYS},
        },
        "declared_family_inventory": aggregate["declared_family_inventory"],
        "classified_family_inventory": aggregate["classified_family_inventory"],
        "declared_by_classified_family": aggregate[
            "declared_by_classified_family"
        ],
        "refusal_inventory": aggregate["refusal_inventory"],
        "match_inventory": aggregate["match_inventory"],
        "assumptions": dict(REPORT_ASSUMPTIONS),
        "claim_license": fixed_claim_license().to_dict(),
        "warnings": [],
    }
    validate_report_schema(report, domains=domains)
    return report


def validate_report_schema(
    report: Any, *, domains: RegisterDomains
) -> dict[str, Any]:
    """Validate the complete report shape, domains, bounds, and equations."""
    if type(report) is not dict or set(report) != set(REPORT_KEYS):
        raise InternalError()
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise InternalError()
    if report["tool"] != TOOL or report["version"] != VERSION:
        raise InternalError()
    if report["taxonomy"] != TAXONOMY:
        raise InternalError()
    for key in REPORT_DIGEST_KEYS:
        _require_prefixed(report[key])
    validate_report_scope(report["scope"])
    if type(report["limits"]) is not dict or report["limits"] != LIMITS:
        raise InternalError()
    if type(report["assumptions"]) is not dict or (
        report["assumptions"] != REPORT_ASSUMPTIONS
    ):
        raise InternalError()
    if report["claim_license"] != fixed_claim_license().to_dict():
        raise InternalError()
    if type(report["warnings"]) is not list or report["warnings"] != []:
        raise InternalError()

    counts = report["counts"]
    if type(counts) is not dict or set(counts) != set(REPORT_COUNT_KEYS):
        raise InternalError()
    input_rows = _require_count(counts["input_rows"])
    aggregate = {
        "counts": {key: counts[key] for key in AGGREGATE_COUNT_KEYS},
        **{name: report[name] for name in INVENTORY_KEYS},
    }
    validate_aggregate(aggregate, domains=domains)
    if counts["scoped_documents"] > input_rows:
        raise InternalError()
    return report


def canonical_report_bytes(
    report: dict[str, Any], *, domains: RegisterDomains
) -> bytes:
    """Return the frozen canonical report bytes: sorted keys, compact
    separators, UTF-8, ``allow_nan=False``, exactly one terminal LF, and no
    timestamp, random identifier, or local path."""
    validate_report_schema(report, domains=domains)
    return canonical_json(report) + b"\n"


def artifact_sha256(frozen_bytes: bytes) -> str:
    """Return the prefixed raw digest of a frozen artifact's exact bytes."""
    return prefixed(raw_sha256(frozen_bytes))


# --------------------------------------------------------------------------
# Mechanical recursive claim-posture guard
#
# The atom and sequence tables below are the guard's own closed vocabularies.
# They are the only place in this module where those words appear as data, and
# no H2 key or value is allowed to contain them.
# --------------------------------------------------------------------------

_FORBIDDEN_KEY_ATOM_SPEC = (
    "verdict label score probability rate ratio share proportion percentage "
    "percent threshold band rank dominant homogeneity unimodality accuracy "
    "quality correctness authorship"
)
FORBIDDEN_KEY_ATOMS = frozenset(_FORBIDDEN_KEY_ATOM_SPEC.split())

_FORBIDDEN_KEY_SEQUENCE_SPEC = (
    "selection_decision disposition activation_decision training_decision "
    "is_ai is_human source_group source_id source_family semantic_mode "
    "multimodality mixture_flag"
)
FORBIDDEN_KEY_SEQUENCES = tuple(
    tuple(entry.split("_")) for entry in _FORBIDDEN_KEY_SEQUENCE_SPEC.split()
)

_KEY_SEPARATOR_RUN = re.compile(r"[^0-9a-z]+")

FORBIDDEN_VALUE_PATTERNS = (
    re.compile(
        r"\b(?:verdict|label|score|probability|threshold|band|rank|dominant"
        r"|homogeneous|unimodal|multimodal|accuracy|quality|correctness"
        r"|authorship|is[_ -]?ai|is[_ -]?human|selection[_ -]?decision"
        r"|activation[_ -]?decision|training[_ -]?decision|mixture[_ -]?flag"
        r"|semantic[_ -]?mode|source[_ -]?(?:group|id|family))\b",
        re.ASCII | re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:is|are|was|were|shows?|proves?|explains?|indicates?|means?"
        r"|licenses?|authorizes?|recommends?)\b.{0,64}"
        r"\b(?:ai|human|accurate|correct|homogeneous|unimodal|multimodal"
        r"|mixed|selected|excluded|registered|activated|approved|safe)\b",
        re.ASCII | re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:select|exclude|discard|keep|register|activate|train|publish)\b"
        r".{0,32}\b(?:this|the)?\s*(?:corpus|row|document|data)\b",
        re.ASCII | re.IGNORECASE,
    ),
)

#: The three context exceptions, each valid only after the artifact's
#: ``claim_license`` is byte-equal to the frozen object.
CLAIM_EXEMPT_LICENSE_PATHS = (
    ("claim_license", "does_not_license"),
    ("claim_license", "additional_caveats", 0),
)
CLAIM_EXEMPT_RENDER_PATH = ("claim_license_rendered",)


def normalize_claim_key(key: str) -> tuple[str, ...]:
    """Return a mapping key's normalized components.

    NFKC + casefold, each maximal non-ASCII-alphanumeric run collapsed to
    ``_``, edge underscores stripped, then split on ``_``. The check is
    component-based, so ``scoped`` does not match the ``score`` atom while
    ``final_verdict`` does match ``verdict``.
    """
    if type(key) is not str:
        raise InternalError()
    folded = unicodedata.normalize("NFKC", key).casefold()
    collapsed = _KEY_SEPARATOR_RUN.sub("_", folded).strip("_")
    if not collapsed:
        return ()
    return tuple(part for part in collapsed.split("_") if part)


def claim_key_is_refused(key: str) -> bool:
    """Return whether a mapping key hits the closed atom or sequence tables."""
    components = normalize_claim_key(key)
    if any(component in FORBIDDEN_KEY_ATOMS for component in components):
        return True
    for sequence in FORBIDDEN_KEY_SEQUENCES:
        span = len(sequence)
        for start in range(len(components) - span + 1):
            if components[start : start + span] == sequence:
                return True
    return False


def claim_text_is_refused(text: str) -> bool:
    """Return whether a string leaf matches a forbidden value pattern."""
    if type(text) is not str:
        raise InternalError()
    folded = unicodedata.normalize("NFKC", text).casefold()
    return any(pattern.search(folded) for pattern in FORBIDDEN_VALUE_PATTERNS)


def assert_claim_posture(artifact: Any) -> None:
    """Recursively enforce the mechanical claim posture on a complete artifact.

    Every mapping key and every string leaf is checked. Exactly three context
    exceptions exist, and only after the artifact's ``claim_license`` is
    byte-equal to the frozen success object: the negative
    ``does_not_license`` leaf, the single negative caveat, and a top-level
    ``claim_license_rendered`` byte-equal to that same object's rendered
    block. The positive ``licenses`` leaf, every warning, and every assumption
    stay checked.
    """
    frozen = fixed_claim_license()
    frozen_bytes = canonical_json(frozen.to_dict())
    exempt: set[tuple[Any, ...]] = set()
    if type(artifact) is dict:
        license_object = artifact.get("claim_license")
        if type(license_object) is dict:
            try:
                same = canonical_json(license_object) == frozen_bytes
            except InternalError:
                same = False
            if same:
                exempt.update(CLAIM_EXEMPT_LICENSE_PATHS)
                rendered = artifact.get("claim_license_rendered")
                if (
                    type(rendered) is str
                    and rendered == frozen.render_block().rstrip()
                ):
                    exempt.add(CLAIM_EXEMPT_RENDER_PATH)
    _walk_claim_posture(artifact, (), exempt)


def _walk_claim_posture(
    node: Any, path: tuple[Any, ...], exempt: set[tuple[Any, ...]]
) -> None:
    if type(node) is dict:
        for key, value in node.items():
            if claim_key_is_refused(key):
                raise InternalError()
            _walk_claim_posture(value, path + (key,), exempt)
        return
    if type(node) in (list, tuple):
        for index, item in enumerate(node):
            _walk_claim_posture(item, path + (index,), exempt)
        return
    if type(node) is str:
        if path in exempt:
            return
        if claim_text_is_refused(node):
            raise InternalError()
        return
    # Terminal: the dispatch above is exact-type (``type(x) is``), so a str,
    # dict, or list SUBCLASS -- or any other node type -- would otherwise fall
    # through the walk unscanned and smuggle refused claim text past the guard.
    if node is None or type(node) in (int, float, bool):
        return
    raise InternalError()


# --------------------------------------------------------------------------
# Normalized success and controlled error envelopes
# --------------------------------------------------------------------------


def build_success_envelope(
    *, report_sha256: str, counts: dict[str, Any]
) -> dict[str, Any]:
    """Build the normalized success envelope with every argument explicit.

    The envelope carries no family cell, plaintext filter value, path, corpus
    identifier, or free-text metadata; ``report_sha256`` indirectly commits
    the private report's closed validated scope object.
    """
    _require_prefixed(report_sha256)
    if type(counts) is not dict or set(counts) != set(REPORT_COUNT_KEYS):
        raise InternalError()
    for key in REPORT_COUNT_KEYS:
        _require_count(counts[key])
    try:
        return build_output(
            task_surface=CLAIM_TASK_SURFACE,
            tool=TOOL,
            version=VERSION,
            target_path=None,
            target_words=counts["scoped_words"],
            baseline=None,
            results={
                "report_sha256": report_sha256,
                "report_schema_version": REPORT_SCHEMA_VERSION,
                "taxonomy": TAXONOMY,
                "counts": dict(counts),
            },
            claim_license=fixed_claim_license(),
            available=True,
            warnings=[],
            ai_status=None,
            target_extra=None,
            extra=None,
            validate_bounds=True,
        )
    except Exception as exc:  # noqa: BLE001 - closed controlled refusal
        raise InternalError() from exc


def validate_success_envelope(envelope: Any) -> dict[str, Any]:
    """Validate the normalized success envelope before the guard runs."""
    if type(envelope) is not dict:
        raise InternalError()
    if set(envelope) != {
        "schema_version",
        "task_surface",
        "tool",
        "version",
        "available",
        "target",
        "baseline",
        "results",
        "claim_license",
        "claim_license_rendered",
        "warnings",
        "ai_status",
    }:
        raise InternalError()
    if envelope["task_surface"] != CLAIM_TASK_SURFACE:
        raise InternalError()
    if envelope["tool"] != TOOL or envelope["version"] != VERSION:
        raise InternalError()
    if envelope["available"] is not True:
        raise InternalError()
    if envelope["baseline"] is not None or envelope["ai_status"] is not None:
        raise InternalError()
    if envelope["warnings"] != []:
        raise InternalError()
    target = envelope["target"]
    if type(target) is not dict or set(target) != {"path", "words"}:
        raise InternalError()
    if target["path"] is not None:
        raise InternalError()
    results = envelope["results"]
    if type(results) is not dict or set(results) != {
        "report_sha256",
        "report_schema_version",
        "taxonomy",
        "counts",
    }:
        raise InternalError()
    _require_prefixed(results["report_sha256"])
    if results["report_schema_version"] != REPORT_SCHEMA_VERSION:
        raise InternalError()
    if results["taxonomy"] != TAXONOMY:
        raise InternalError()
    counts = results["counts"]
    if type(counts) is not dict or set(counts) != set(REPORT_COUNT_KEYS):
        raise InternalError()
    for key in REPORT_COUNT_KEYS:
        _require_count(counts[key])
    if _require_count(target["words"]) != counts["scoped_words"]:
        raise InternalError()
    frozen = fixed_claim_license()
    if envelope["claim_license"] != frozen.to_dict():
        raise InternalError()
    if envelope["claim_license_rendered"] != frozen.render_block().rstrip():
        raise InternalError()
    return envelope


def canonical_envelope_bytes(envelope: dict[str, Any]) -> bytes:
    """Freeze an envelope's canonical bytes with exactly one terminal LF."""
    return canonical_json(envelope) + b"\n"


def freeze_publication(
    *, report: dict[str, Any], domains: RegisterDomains
) -> tuple[bytes, str, dict[str, Any], bytes]:
    """Freeze the complete publication artifacts before the terminal commit.

    Schema equality runs first; the guard then runs on the report and the
    envelope independently. Returns the frozen report bytes, the prefixed
    report digest, the success envelope, and its frozen bytes. The caller
    publishes the report bytes and only afterwards hands the envelope bytes to
    :func:`emit_committed_success`.
    """
    validate_report_schema(report, domains=domains)
    frozen_report = canonical_report_bytes(report, domains=domains)
    report_sha256 = artifact_sha256(frozen_report)
    assert_claim_posture(report)
    envelope = build_success_envelope(
        report_sha256=report_sha256, counts=report["counts"]
    )
    validate_success_envelope(envelope)
    assert_claim_posture(envelope)
    return frozen_report, report_sha256, envelope, canonical_envelope_bytes(envelope)


def controlled_failure_class(exc: BaseException) -> type[SweepRefusal]:
    """Map a caught exception to one of the three controlled failure rows.

    ``KeyboardInterrupt`` and every other non-``Exception`` ``BaseException``
    is re-raised unchanged: it is never converted into an output artifact.
    """
    if isinstance(exc, BadInput):
        return BadInput
    if isinstance(exc, PolicyRefused):
        return PolicyRefused
    if isinstance(exc, Exception):
        return InternalError
    raise exc


def build_controlled_error_envelope(
    failure: type[SweepRefusal] | SweepRefusal,
) -> dict[str, Any]:
    """Build the one controlled error envelope for a failure row.

    Parameters are fixed and path-free: no path, digest, filter value,
    validator text, or caught-exception text reaches the envelope.
    """
    cls = failure if isinstance(failure, type) else type(failure)
    if cls not in (BadInput, PolicyRefused, InternalError):
        raise InternalError()
    try:
        return build_error_output(
            task_surface=CLAIM_TASK_SURFACE,
            tool=TOOL,
            version=VERSION,
            reason=cls.reason,
            reason_category=cls.reason_category,
            target_path=None,
            target_words=0,
            warnings=[],
            extra=None,
        )
    except Exception as exc:  # noqa: BLE001 - closed controlled refusal
        raise InternalError() from exc


def freeze_controlled_error(
    failure: type[SweepRefusal] | SweepRefusal,
) -> tuple[bytes, int]:
    """Return one canonical golden error envelope's bytes and its exit code."""
    cls = failure if isinstance(failure, type) else type(failure)
    envelope = build_controlled_error_envelope(cls)
    assert_claim_posture(envelope)
    return canonical_envelope_bytes(envelope), cls.exit_code


# --------------------------------------------------------------------------
# Progress lines
# --------------------------------------------------------------------------

PROGRESS_INTERVAL = 100
PROGRESS_TEMPLATE = "register sweep progress: completed={completed} total={total}\n"
PROGRESS_COMPLETE_TEMPLATE = (
    "register sweep processing-complete: completed={total} total={total} "
    "report_commit=pending\n"
)


def _require_ordinal(value: Any) -> int:
    if type(value) is not int or not (0 <= value <= MAX_SCOPED_DOCUMENTS):
        raise InternalError()
    return value


def progress_is_eligible(completed: int, total: int, *, resume_from: int = 0) -> bool:
    """Return whether the cadence line is emitted after ordinal ``completed``.

    ``completed`` must be a positive multiple of ``PROGRESS_INTERVAL`` and
    strictly less than ``total``; the cadence line is never emitted at
    ``completed == total``. On resume from ``resume_from`` sealed rows no
    earlier line is replayed.
    """
    _require_ordinal(completed)
    _require_ordinal(total)
    _require_ordinal(resume_from)
    if resume_from > total:
        raise InternalError()
    if completed <= 0 or completed % PROGRESS_INTERVAL != 0:
        return False
    if completed >= total:
        return False
    return completed > resume_from


def progress_ordinals(total: int, *, resume_from: int = 0) -> tuple[int, ...]:
    """Return every eligible cadence ordinal for a run, in emission order.

    The first eligible line is the smallest multiple of ``PROGRESS_INTERVAL``
    strictly greater than ``resume_from`` and strictly less than ``total``.
    """
    _require_ordinal(total)
    _require_ordinal(resume_from)
    if resume_from > total:
        raise InternalError()
    first = (resume_from // PROGRESS_INTERVAL + 1) * PROGRESS_INTERVAL
    return tuple(range(first, total, PROGRESS_INTERVAL))


def progress_line(completed: int, total: int) -> str:
    """Return the exact ASCII cadence line for ``completed`` of ``total``."""
    _require_ordinal(completed)
    _require_ordinal(total)
    if completed <= 0 or completed >= total:
        raise InternalError()
    return PROGRESS_TEMPLATE.format(completed=completed, total=total)


def processing_complete_line(total: int) -> str:
    """Return the exact ASCII pending-completion line, emitted exactly once.

    ``report_commit=pending`` is literal: document work and aggregate
    reassembly finished, and committed success has not happened yet.
    """
    _require_ordinal(total)
    return PROGRESS_COMPLETE_TEMPLATE.format(total=total)


# --------------------------------------------------------------------------
# Terminal committed-success sink
# --------------------------------------------------------------------------


def emit_committed_success(frozen_bytes: bytes, stream: Any = None) -> bool:
    """Best-effort deliver already-frozen success bytes after the commit.

    This is total. Once the report is published the run may not fail, so a
    closed, broken, or exhausted consumer is absorbed without stderr, without
    rollback, and without changing exit 0. It never raises and always reports
    success.
    """
    try:
        sink = sys.stdout.buffer if stream is None else stream
        view = memoryview(frozen_bytes)
        while view:
            written = sink.write(view)
            if type(written) is not int or written <= 0:
                # Partial-write exhaustion: stop rather than spin. The report
                # is already authoritative; only the convenience envelope is
                # lost.
                break
            view = view[written:]
        flush = getattr(sink, "flush", None)
        if flush is not None:
            flush()
    except Exception:  # noqa: BLE001 - the sink absorbs every delivery failure
        pass
    return True


# ==========================================================================
# Increment C: immutable shard checkpoint, owner-private policy, topology
#
# Everything below this banner implements the spec's "Checkpoint, privacy,
# platform, and resume" section against the encoders above. It adds no
# classifier behaviour, no report construction, and no runner.
# ==========================================================================

#: SQLite application id ``RSW1`` and user version for an H2 shard.
SHARD_APPLICATION_ID = 0x52535731
SHARD_USER_VERSION = 1
SHARD_PAGE_SIZE = 4096

SHARD_NAME_TEMPLATE = "register-{:08d}.sqlite"
SHARD_NAME_RE = re.compile(r"register-(\d{8})\.sqlite\Z")
#: The only tolerated non-shard names in a resume directory. Their bytes are
#: inert crash debris: they are never opened, deserialized, hashed as progress,
#: deleted, or overwritten.
RESERVED_TEMP_RE = re.compile(
    r"\.tmp-(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?:-journal|-wal|-shm)?\Z"
)

SHARD_META_SQL = (
    "CREATE TABLE checkpoint_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
    " WITHOUT ROWID"
)
SHARD_ROWS_SQL = (
    "CREATE TABLE rows(scoped_ordinal INTEGER PRIMARY KEY,row_json BLOB NOT NULL,"
    "row_sha256 BLOB NOT NULL)"
)
SHARD_DELTA_SQL = (
    "CREATE TABLE aggregate_delta(key TEXT PRIMARY KEY,value_json BLOB NOT NULL)"
    " WITHOUT ROWID"
)
SHARD_OBJECTS = {
    ("table", "aggregate_delta"): SHARD_DELTA_SQL,
    ("table", "checkpoint_meta"): SHARD_META_SQL,
    ("table", "rows"): SHARD_ROWS_SQL,
}

SHARD_META_KEYS = (
    "checkpoint_binding_sha256",
    "first_scoped_ordinal",
    "kind",
    "next_scoped_ordinal",
    "prior_shard_sha256",
    "schema_version",
    "shard_number",
    "shard_sha256",
)
#: The seven metadata keys that enter the logical shard payload. ``shard_sha256``
#: is the digest of that payload and therefore cannot be inside it.
SHARD_PAYLOAD_META_KEYS = tuple(
    key for key in SHARD_META_KEYS if key != "shard_sha256"
)

DELTA_KEYS = (
    "counts",
    "declared_family_inventory",
    "classified_family_inventory",
    "declared_by_classified_family",
    "refusal_inventory",
    "match_inventory",
)
DELTA_COUNT_KEYS = (
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
CHECKPOINT_ROW_KEYS = (
    "manifest_ordinal",
    "projected_row_sha256",
    "content_sha256",
    "document_bytes",
    "words",
    "declared_family",
    "classified_family",
    "refusal_reason",
)


# ``RegisterDomains`` is defined once, in the Increment D1 section above;
# the checkpoint layer below consumes that single definition.


def _add_cell(cell: dict[str, int], words: int) -> None:
    cell["documents"] += 1
    cell["words"] += words


def compute_aggregate_delta(
    rows: Sequence[dict[str, Any]], domains: RegisterDomains
) -> dict[str, Any]:
    """Return the six-key aggregate delta for one shard's completed rows.

    These are count buckets only. ``same``/``different``/``unresolved`` are not
    accuracy or truth labels, and no percentage, rate, share, entropy,
    threshold, band, or dominant-family value exists here or anywhere else.
    """
    domains.validate()
    if len(rows) > SHARD_ROWS:
        raise InternalError()
    delta = zero_inventories(
        domains.declared, domains.classified, domains.refusals
    )
    counts = {key: 0 for key in DELTA_COUNT_KEYS}
    for row in rows:
        if set(row) != set(CHECKPOINT_ROW_KEYS):
            raise InternalError()
        declared = row["declared_family"]
        classified = row["classified_family"]
        refusal = row["refusal_reason"]
        words = _require_int(row["words"], 0, INT64_MAX)
        document_bytes = _require_int(row["document_bytes"], 0, MAX_DOCUMENT_BYTES)
        if declared not in domains.declared:
            raise InternalError()
        if (classified is None) == (refusal is None):
            raise InternalError()
        if classified is not None and classified not in domains.classified:
            raise InternalError()
        if refusal is not None and refusal not in domains.refusals:
            raise InternalError()

        counts["scoped_documents"] += 1
        counts["scoped_bytes"] += document_bytes
        counts["scoped_words"] += words
        _add_cell(delta["declared_family_inventory"][declared], words)
        if declared == "unknown":
            counts["unresolved_declared_documents"] += 1
            counts["unresolved_declared_words"] += words
        else:
            counts["resolved_declared_documents"] += 1
            counts["resolved_declared_words"] += words

        if classified is not None:
            counts["classified_documents"] += 1
            counts["classified_words"] += words
            _add_cell(delta["classified_family_inventory"][classified], words)
            _add_cell(
                delta["declared_by_classified_family"][declared][classified], words
            )
            if declared == "unknown":
                bucket = "unresolved"
            elif declared == classified:
                bucket = "same"
            else:
                bucket = "different"
        else:
            counts["refused_documents"] += 1
            counts["refused_words"] += words
            _add_cell(delta["refusal_inventory"][refusal], words)
            bucket = "unresolved"
        _add_cell(delta["match_inventory"][bucket], words)

    for key in DELTA_COUNT_KEYS:
        _require_int(counts[key], 0, INT64_MAX)
    if counts["scoped_documents"] > SHARD_ROWS:
        raise InternalError()
    delta["counts"] = counts
    return {key: delta[key] for key in DELTA_KEYS}


def validate_aggregate_delta(
    delta: Any, rows: Sequence[dict[str, Any]], domains: RegisterDomains
) -> dict[str, Any]:
    """Require ``delta`` to be exactly the delta implied by ``rows``.

    Every document count is at most 250 and every row sum must agree, so a
    tampered delta cannot survive alongside honest rows or the reverse.
    """
    if type(delta) is not dict or tuple(sorted(delta)) != tuple(sorted(DELTA_KEYS)):
        raise PolicyRefused()
    expected = compute_aggregate_delta(rows, domains)
    if canonical_json(delta) != canonical_json(expected):
        raise PolicyRefused()
    counts = delta["counts"]
    for key in DELTA_COUNT_KEYS:
        value = counts[key]
        if type(value) is not int or isinstance(value, bool) or not (
            0 <= value <= INT64_MAX
        ):
            raise PolicyRefused()
    if counts["scoped_documents"] > SHARD_ROWS:
        raise PolicyRefused()
    if (
        counts["resolved_declared_documents"]
        + counts["unresolved_declared_documents"]
        != counts["scoped_documents"]
        or counts["classified_documents"] + counts["refused_documents"]
        != counts["scoped_documents"]
        or counts["resolved_declared_words"] + counts["unresolved_declared_words"]
        != counts["scoped_words"]
        or counts["classified_words"] + counts["refused_words"]
        != counts["scoped_words"]
    ):
        raise PolicyRefused()
    return delta


def add_aggregate_deltas(
    total: dict[str, Any], delta: dict[str, Any]
) -> dict[str, Any]:
    """Add one sealed shard delta into a running total, in shard order."""

    def merge(left: Any, right: Any) -> Any:
        if type(left) is not type(right):
            raise InternalError()
        if type(left) is int:
            return _require_int(left + right, 0, INT64_MAX)
        if type(left) is not dict or set(left) != set(right):
            raise InternalError()
        return {key: merge(left[key], right[key]) for key in left}

    if set(total) != set(DELTA_KEYS) or set(delta) != set(DELTA_KEYS):
        raise InternalError()
    return {key: merge(total[key], delta[key]) for key in DELTA_KEYS}


def zero_aggregate_delta(domains: RegisterDomains) -> dict[str, Any]:
    """The all-zero six-key delta: an empty scope's sealed progress."""
    return compute_aggregate_delta((), domains)


def shard_partition(total_rows: int) -> tuple[int, ...]:
    """Return the one immutable shard partition of a plan of ``total_rows``.

    Every non-final shard holds exactly 250 contiguous completed rows and the
    final shard holds the remaining 1-250. An empty plan creates no shard, so a
    fixed plan cannot produce alternate chains such as 200/200/101.
    """
    _require_int(total_rows, 0, MAX_SCOPED_DOCUMENTS)
    full, remainder = divmod(total_rows, SHARD_ROWS)
    sizes = [SHARD_ROWS] * full
    if remainder:
        sizes.append(remainder)
    return tuple(sizes)


# --------------------------------------------------------------------------
# Shard codec
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterShard:
    """One fully validated immutable shard."""

    name: str
    shard_number: int
    first_scoped_ordinal: int
    next_scoped_ordinal: int
    checkpoint_binding_sha256: str
    prior_shard_sha256: str | None
    shard_sha256: str
    aggregate_delta_sha256: str
    rows: tuple[dict[str, Any], ...]
    row_digests: tuple[str, ...]
    delta: dict[str, Any]


def shard_name(shard_number: int) -> str:
    """Return ``register-NNNNNNNN.sqlite`` for a zero-based shard number."""
    _require_int(shard_number, 0, MAX_FINAL_SHARDS - 1)
    return SHARD_NAME_TEMPLATE.format(shard_number)


def _meta_text(value: Any) -> str:
    """``canonical_json(logical_scalar)`` decoded as text, with no terminal LF."""
    return canonical_json(value).decode("utf-8")


def _meta_scalar(text: Any) -> Any:
    """Invert :func:`_meta_text` under strict, canonical-only decoding."""
    if type(text) is not str:
        raise PolicyRefused()
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise PolicyRefused() from exc
    if type(value) not in (str, int, type(None)) or isinstance(value, bool):
        raise PolicyRefused()
    if _meta_text(value) != text:
        raise PolicyRefused()
    return value


def _shard_metadata(
    *,
    shard_number: int,
    first_scoped_ordinal: int,
    next_scoped_ordinal: int,
    checkpoint_binding_sha256: str,
    prior_shard_sha256: str | None,
) -> dict[str, Any]:
    _require_int(shard_number, 0, MAX_FINAL_SHARDS - 1)
    _require_int(first_scoped_ordinal, 0, MAX_SCOPED_DOCUMENTS - 1)
    _require_int(next_scoped_ordinal, 1, MAX_SCOPED_DOCUMENTS)
    if not 1 <= next_scoped_ordinal - first_scoped_ordinal <= SHARD_ROWS:
        raise InternalError()
    if (shard_number == 0) != (prior_shard_sha256 is None):
        raise InternalError()
    if shard_number == 0 and first_scoped_ordinal != 0:
        raise InternalError()
    if prior_shard_sha256 is not None:
        _require_prefixed(prior_shard_sha256)
    return {
        "checkpoint_binding_sha256": _require_prefixed(checkpoint_binding_sha256),
        "first_scoped_ordinal": first_scoped_ordinal,
        "kind": CHECKPOINT_KIND,
        "next_scoped_ordinal": next_scoped_ordinal,
        "prior_shard_sha256": prior_shard_sha256,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "shard_number": shard_number,
    }


def _configure_shard_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA encoding='UTF-8'")
    connection.execute(f"PRAGMA page_size={SHARD_PAGE_SIZE}")
    if connection.execute("PRAGMA journal_mode=MEMORY").fetchone() != ("memory",):
        raise InternalError()
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA cache_size=-16384")
    connection.execute(f"PRAGMA application_id={SHARD_APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version={SHARD_USER_VERSION}")


def encode_register_shard(
    *,
    shard_number: int,
    first_scoped_ordinal: int,
    checkpoint_binding_sha256: str,
    prior_shard_sha256: str | None,
    rows: Sequence[dict[str, Any]],
    domains: RegisterDomains,
) -> tuple[bytes, RegisterShard]:
    """Build one create-new SQLite shard artifact in memory.

    Returns the exact serialized bytes and the shard those bytes decode to. The
    caller performs the read-only verification pass before publication.
    """
    if not 1 <= len(rows) <= SHARD_ROWS:
        raise InternalError()
    metadata = _shard_metadata(
        shard_number=shard_number,
        first_scoped_ordinal=first_scoped_ordinal,
        next_scoped_ordinal=first_scoped_ordinal + len(rows),
        checkpoint_binding_sha256=checkpoint_binding_sha256,
        prior_shard_sha256=prior_shard_sha256,
    )
    encoded_rows: list[tuple[int, bytes, bytes]] = []
    payload_rows: list[dict[str, Any]] = []
    for offset, row in enumerate(rows):
        ordinal = first_scoped_ordinal + offset
        _require_int(ordinal, 0, MAX_SCOPED_DOCUMENTS - 1)
        row_json, row_digest = checkpoint_row_binding(row)
        encoded_rows.append((ordinal, row_json, row_digest))
        payload_rows.append(
            {
                "row_json_sha256": prefixed(row_digest.hex()),
                "scoped_ordinal": ordinal,
            }
        )
    delta = compute_aggregate_delta(rows, domains)
    _, delta_digest = aggregate_delta_binding(delta)
    _, shard_digest = shard_binding(
        aggregate_delta_sha256=prefixed(delta_digest),
        metadata=metadata,
        rows=payload_rows,
    )
    sealed = dict(metadata)
    sealed["shard_sha256"] = prefixed(shard_digest)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        _configure_shard_connection(connection)
        connection.execute(SHARD_META_SQL)
        connection.execute(SHARD_ROWS_SQL)
        connection.execute(SHARD_DELTA_SQL)
        connection.executemany(
            "INSERT INTO checkpoint_meta VALUES(?,?)",
            sorted((key, _meta_text(sealed[key])) for key in SHARD_META_KEYS),
        )
        connection.executemany("INSERT INTO rows VALUES(?,?,?)", encoded_rows)
        connection.executemany(
            "INSERT INTO aggregate_delta VALUES(?,?)",
            sorted((key, canonical_json(delta[key])) for key in DELTA_KEYS),
        )
        connection.commit()
        raw = connection.serialize()
    except (sqlite3.Error, OSError, TypeError, ValueError, MemoryError) as exc:
        raise InternalError() from exc
    finally:
        if connection is not None:
            connection.close()
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_SHARD_BYTES:
        raise InternalError()
    return raw, decode_register_shard(
        raw,
        name=shard_name(shard_number),
        domains=domains,
        checkpoint_binding_sha256=checkpoint_binding_sha256,
    )


def decode_register_shard(
    raw: bytes,
    *,
    name: str,
    domains: RegisterDomains,
    checkpoint_binding_sha256: str,
) -> RegisterShard:
    """Read-only in-memory validation of one shard's exact bytes.

    Every schema object, PRAGMA, metadata key/domain, filename ordinal, row
    digest, delta equation, and hash-chain link is revalidated here. Holes,
    extras, mutation, replacement, and corruption refuse.
    """
    domains.validate()
    matched = SHARD_NAME_RE.fullmatch(name)
    if matched is None:
        raise PolicyRefused()
    expected_number = int(matched.group(1))
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_SHARD_BYTES:
        raise PolicyRefused()
    if not hasattr(sqlite3.Connection, "deserialize"):
        raise PolicyRefused()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.deserialize(raw)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA cache_size=-16384")
        if connection.execute("PRAGMA trusted_schema").fetchone() != (0,):
            raise PolicyRefused()
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise PolicyRefused()
        for pragma, expected in (
            ("application_id", (SHARD_APPLICATION_ID,)),
            ("user_version", (SHARD_USER_VERSION,)),
            ("encoding", ("UTF-8",)),
            ("page_size", (SHARD_PAGE_SIZE,)),
            ("journal_mode", ("memory",)),
        ):
            if connection.execute(f"PRAGMA {pragma}").fetchone() != expected:
                raise PolicyRefused()
        page_count = connection.execute("PRAGMA page_count").fetchone()
        if (
            page_count is None
            or type(page_count[0]) is not int
            or page_count[0] < 1
            or page_count[0] * SHARD_PAGE_SIZE != len(raw)
        ):
            raise PolicyRefused()
        objects = {
            (row[0], row[1]): row[2]
            for row in connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            )
        }
        if objects != SHARD_OBJECTS:
            raise PolicyRefused()

        meta_rows = tuple(
            connection.execute(
                "SELECT key,value FROM checkpoint_meta ORDER BY key COLLATE BINARY"
            )
        )
        if len(meta_rows) != len(SHARD_META_KEYS) or any(
            type(key) is not str or type(value) is not str
            for key, value in meta_rows
        ):
            raise PolicyRefused()
        meta = {key: _meta_scalar(value) for key, value in meta_rows}
        if tuple(sorted(meta)) != SHARD_META_KEYS:
            raise PolicyRefused()
        if (
            meta["schema_version"] != CHECKPOINT_SCHEMA_VERSION
            or meta["kind"] != CHECKPOINT_KIND
        ):
            raise PolicyRefused()
        shard_number = meta["shard_number"]
        first = meta["first_scoped_ordinal"]
        following = meta["next_scoped_ordinal"]
        if (
            type(shard_number) is not int
            or not 0 <= shard_number <= MAX_FINAL_SHARDS - 1
            or shard_number != expected_number
            or type(first) is not int
            or not 0 <= first <= MAX_SCOPED_DOCUMENTS - 1
            or type(following) is not int
            or not 1 <= following <= MAX_SCOPED_DOCUMENTS
            or not 1 <= following - first <= SHARD_ROWS
        ):
            raise PolicyRefused()
        if shard_number == 0 and (first != 0 or meta["prior_shard_sha256"] is not None):
            raise PolicyRefused()
        if shard_number != 0 and meta["prior_shard_sha256"] is None:
            raise PolicyRefused()
        for key in ("checkpoint_binding_sha256", "shard_sha256"):
            if type(meta[key]) is not str:
                raise PolicyRefused()
            try:
                _require_prefixed(meta[key])
            except InternalError as exc:
                raise PolicyRefused() from exc
        if meta["prior_shard_sha256"] is not None:
            try:
                _require_prefixed(meta["prior_shard_sha256"])
            except InternalError as exc:
                raise PolicyRefused() from exc
        if meta["checkpoint_binding_sha256"] != _require_prefixed(
            checkpoint_binding_sha256
        ):
            raise PolicyRefused()

        row_records = tuple(
            connection.execute(
                "SELECT scoped_ordinal,row_json,row_sha256 FROM rows"
                " ORDER BY scoped_ordinal"
            )
        )
        if [record[0] for record in row_records] != list(range(first, following)):
            raise PolicyRefused()
        rows: list[dict[str, Any]] = []
        digests: list[str] = []
        payload_rows: list[dict[str, Any]] = []
        for ordinal, row_json, row_digest in row_records:
            if type(row_json) is not bytes or type(row_digest) is not bytes:
                raise PolicyRefused()
            if len(row_digest) != 32:
                raise PolicyRefused()
            if hashlib.sha256(row_json).digest() != row_digest:
                raise PolicyRefused()
            try:
                row = json.loads(row_json.decode("utf-8", errors="strict"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise PolicyRefused() from exc
            if type(row) is not dict or set(row) != set(CHECKPOINT_ROW_KEYS):
                raise PolicyRefused()
            try:
                encoded, recomputed = checkpoint_row_binding(row)
            except InternalError as exc:
                raise PolicyRefused() from exc
            if encoded != row_json or recomputed != row_digest:
                raise PolicyRefused()
            # The codec revalidates the closed value domains itself rather than
            # relying on the shared builders as its only line of defence.
            if (
                row["declared_family"] not in domains.declared
                or (
                    row["classified_family"] is not None
                    and row["classified_family"] not in domains.classified
                )
                or (
                    row["refusal_reason"] is not None
                    and row["refusal_reason"] not in domains.refusals
                )
                or (row["classified_family"] is None)
                == (row["refusal_reason"] is None)
            ):
                raise PolicyRefused()
            rows.append(row)
            digests.append(prefixed(row_digest.hex()))
            payload_rows.append(
                {
                    "row_json_sha256": prefixed(row_digest.hex()),
                    "scoped_ordinal": ordinal,
                }
            )

        delta_records = tuple(
            connection.execute(
                "SELECT key,value_json FROM aggregate_delta ORDER BY key COLLATE BINARY"
            )
        )
        if len(delta_records) != len(DELTA_KEYS):
            raise PolicyRefused()
        delta: dict[str, Any] = {}
        for key, value_json in delta_records:
            if type(key) is not str or type(value_json) is not bytes:
                raise PolicyRefused()
            if key not in DELTA_KEYS or key in delta:
                raise PolicyRefused()
            try:
                value = json.loads(value_json.decode("utf-8", errors="strict"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                raise PolicyRefused() from exc
            if canonical_json(value) != value_json:
                raise PolicyRefused()
            delta[key] = value
        try:
            validate_aggregate_delta(delta, rows, domains)
            _, delta_digest = aggregate_delta_binding(delta)
            _, shard_digest = shard_binding(
                aggregate_delta_sha256=prefixed(delta_digest),
                metadata={key: meta[key] for key in SHARD_PAYLOAD_META_KEYS},
                rows=payload_rows,
            )
        except InternalError as exc:
            # A corrupt artifact is a policy refusal, never an internal error.
            raise PolicyRefused() from exc
        if prefixed(shard_digest) != meta["shard_sha256"]:
            raise PolicyRefused()
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise PolicyRefused()
    except (sqlite3.Error, OSError, TypeError, ValueError, KeyError, MemoryError) as exc:
        raise PolicyRefused() from exc
    finally:
        if connection is not None:
            connection.close()
    return RegisterShard(
        name=name,
        shard_number=shard_number,
        first_scoped_ordinal=first,
        next_scoped_ordinal=following,
        checkpoint_binding_sha256=meta["checkpoint_binding_sha256"],
        prior_shard_sha256=meta["prior_shard_sha256"],
        shard_sha256=meta["shard_sha256"],
        aggregate_delta_sha256=prefixed(delta_digest),
        rows=tuple(rows),
        row_digests=tuple(digests),
        delta=delta,
    )


# --------------------------------------------------------------------------
# Owner-private immutable shard directory and resume
# --------------------------------------------------------------------------


class RegisterShardDirectory(ImmutableShardDirectory):
    """The H2 checkpoint directory: owner-private, register shard grammar."""

    policy = PRIVACY_POLICY
    final_pattern = SHARD_NAME_RE
    temp_pattern = RESERVED_TEMP_RE

    @classmethod
    def _limits(cls) -> tuple[int, int, int, int, int]:
        return (
            MAX_FINAL_SHARDS + MAX_RESERVED_TEMPORARY_NAMES,
            MAX_FINAL_SHARDS,
            MAX_RESERVED_TEMPORARY_NAMES,
            MAX_SHARD_BYTES,
            MAX_CHECKPOINT_CUMULATIVE_BYTES,
        )


class RegisterCheckpoint:
    """The sealed shard chain behind ``--checkpoint-dir``.

    Fresh mode requires the directory absent; resume requires it present. No
    named SQLite database is ever opened, no published shard is ever mutated,
    and no second directory or race protocol is invented.
    """

    __slots__ = (
        "_directory",
        "_domains",
        "_binding_sha256",
        "_shards",
        "_delta",
        "_closed",
        "_sealed_final",
    )

    def __init__(
        self,
        directory: RegisterShardDirectory,
        *,
        domains: RegisterDomains,
        checkpoint_binding_sha256: str,
        shards: tuple[RegisterShard, ...],
    ) -> None:
        self._directory = directory
        self._domains = domains
        self._binding_sha256 = checkpoint_binding_sha256
        self._shards = list(shards)
        self._closed = False
        self._sealed_final = bool(shards) and len(shards[-1].rows) < SHARD_ROWS
        total = zero_aggregate_delta(domains)
        for shard in shards:
            total = add_aggregate_deltas(total, shard.delta)
        self._delta = total

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: os.PathLike[str] | str,
        *,
        domains: RegisterDomains,
        checkpoint_binding_sha256: str,
    ) -> "RegisterCheckpoint":
        """Create the owner-private checkpoint directory. It must be absent."""
        domains.validate()
        _require_prefixed(checkpoint_binding_sha256)
        try:
            directory = RegisterShardDirectory.open_new(path)
        except CheckpointRefusal as exc:
            raise PolicyRefused() from exc
        try:
            finals, temporaries, _total = _frozen_listing(directory)
            if finals or temporaries:
                raise PolicyRefused()
        except BaseException:
            directory.close()
            raise
        return cls(
            directory,
            domains=domains,
            checkpoint_binding_sha256=checkpoint_binding_sha256,
            shards=(),
        )

    @classmethod
    def resume(
        cls,
        path: os.PathLike[str] | str,
        *,
        domains: RegisterDomains,
        checkpoint_binding_sha256: str,
    ) -> "RegisterCheckpoint":
        """Reopen and fully revalidate an existing checkpoint directory.

        An empty or reserved-temporary-only directory resumes at exactly zero
        progress: zero sealed rows, a zero aggregate delta, a null prior-shard
        digest, next shard number 0, and next scoped ordinal 0. Reserved
        temporaries are inert crash debris and grant no continuation, so a
        temporary holding valid-looking SQLite bytes is never accepted as a row,
        delta, or prior hash.
        """
        domains.validate()
        _require_prefixed(checkpoint_binding_sha256)
        try:
            directory = RegisterShardDirectory.open_resume(path)
        except CheckpointRefusal as exc:
            raise PolicyRefused() from exc
        try:
            finals, temporaries, _total = _frozen_listing(directory)
            shards = _snapshot_chain(
                directory,
                finals,
                domains=domains,
                checkpoint_binding_sha256=checkpoint_binding_sha256,
            )
            try:
                directory.require_unchanged((*finals, *temporaries))
            except CheckpointRefusal as exc:
                raise PolicyRefused() from exc
        except BaseException:
            directory.close()
            raise
        return cls(
            directory,
            domains=domains,
            checkpoint_binding_sha256=checkpoint_binding_sha256,
            shards=shards,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._directory.close()

    def __enter__(self) -> "RegisterCheckpoint":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    # -- sealed progress ---------------------------------------------------

    @property
    def path(self) -> Path:
        return self._directory.path

    @property
    def shards(self) -> tuple[RegisterShard, ...]:
        return tuple(self._shards)

    @property
    def next_shard_number(self) -> int:
        return len(self._shards)

    @property
    def next_scoped_ordinal(self) -> int:
        """The first ordinal a resumed run must reprocess."""
        return self._shards[-1].next_scoped_ordinal if self._shards else 0

    @property
    def prior_shard_sha256(self) -> str | None:
        return self._shards[-1].shard_sha256 if self._shards else None

    @property
    def sealed_delta(self) -> dict[str, Any]:
        """The six sealed deltas added in shard order."""
        return json.loads(canonical_json(self._delta).decode("utf-8"))

    @property
    def checkpoint_binding_sha256(self) -> str:
        return self._binding_sha256

    # -- publication -------------------------------------------------------

    def publish_shard(
        self, rows: Sequence[dict[str, Any]], *, final: bool
    ) -> RegisterShard:
        """Seal and publish one immutable shard create-new.

        A non-final shard must carry exactly 250 contiguous completed rows; the
        final shard carries 1-250. Interruption therefore loses the current
        unpublished rows rather than sealing a short non-final shard, and a
        fixed plan has exactly one immutable shard partition.
        """
        if self._closed:
            raise InternalError()
        if type(final) is not bool:
            raise InternalError()
        if self._sealed_final:
            raise PolicyRefused()
        rows = list(rows)
        if final:
            if not 1 <= len(rows) <= SHARD_ROWS:
                raise InternalError()
        elif len(rows) != SHARD_ROWS:
            raise InternalError()
        number = self.next_shard_number
        if number >= MAX_FINAL_SHARDS:
            raise PolicyRefused()
        raw, shard = encode_register_shard(
            shard_number=number,
            first_scoped_ordinal=self.next_scoped_ordinal,
            checkpoint_binding_sha256=self._binding_sha256,
            prior_shard_sha256=self.prior_shard_sha256,
            rows=rows,
            domains=self._domains,
        )
        # Read-only in-memory verification pass before publication: the exact
        # bytes about to be published are decoded and revalidated from scratch.
        verified = decode_register_shard(
            raw,
            name=shard.name,
            domains=self._domains,
            checkpoint_binding_sha256=self._binding_sha256,
        )
        if canonical_json(verified.delta) != canonical_json(shard.delta):
            raise InternalError()
        if verified.shard_sha256 != shard.shard_sha256:
            raise InternalError()
        try:
            self._directory.publish_entry(shard.name, raw)
        except CheckpointRefusal as exc:
            raise PolicyRefused() from exc
        self._shards.append(verified)
        self._delta = add_aggregate_deltas(self._delta, verified.delta)
        self._sealed_final = final
        return verified


def _frozen_listing(
    directory: RegisterShardDirectory,
) -> tuple[tuple[tuple[str, int, object], ...], tuple[tuple[str, int, object], ...], int]:
    try:
        return directory.freeze_listing()
    except CheckpointRefusal as exc:
        raise PolicyRefused() from exc


def _snapshot_chain(
    directory: RegisterShardDirectory,
    finals: Sequence[tuple[str, int, object]],
    *,
    domains: RegisterDomains,
    checkpoint_binding_sha256: str,
) -> tuple[RegisterShard, ...]:
    """Validate the complete bound shard chain, or refuse.

    No row, aggregate delta, completed ordinal, or prior hash is accepted
    unless it is contained in a fully validated final shard and its complete
    bound chain.
    """
    ordered = sorted(name for name, _size, _fingerprint in finals)
    if ordered != [shard_name(index) for index in range(len(ordered))]:
        raise PolicyRefused()
    shards: list[RegisterShard] = []
    for index, name in enumerate(ordered):
        try:
            raw = directory.snapshot_entry(name)
        except CheckpointRefusal as exc:
            raise PolicyRefused() from exc
        shard = decode_register_shard(
            raw,
            name=name,
            domains=domains,
            checkpoint_binding_sha256=checkpoint_binding_sha256,
        )
        expected_first = shards[-1].next_scoped_ordinal if shards else 0
        expected_prior = shards[-1].shard_sha256 if shards else None
        if (
            shard.shard_number != index
            or shard.first_scoped_ordinal != expected_first
            or shard.prior_shard_sha256 != expected_prior
        ):
            raise PolicyRefused()
        if index + 1 < len(ordered) and len(shard.rows) != SHARD_ROWS:
            raise PolicyRefused()
        shards.append(shard)
    return tuple(shards)


# --------------------------------------------------------------------------
# Expected-fingerprint document read
# --------------------------------------------------------------------------


def plan_document_fingerprint(path: os.PathLike[str] | str) -> tuple[int, ...]:
    """Freeze one planned document's identity/mutation fingerprint.

    POSIX binds device, inode, size, ``mtime_ns``, and ``ctime_ns``. Native
    Windows binds the scoped handle fingerprint, which adds ``change_time`` so a
    same-size content mutation with a restored LastWriteTime still refuses.

    Derived through ``bind_regular`` -- the same producer the frozen document
    plan uses -- so there is exactly ONE fingerprint producer in the build and
    a plan entry and a replan of the same path cannot diverge.
    """
    try:
        _absolute, _candidate_index, fingerprint = bind_regular([os.fspath(path)])
    except (SecureIOError, OSError, TypeError, ValueError) as exc:
        raise BadInput() from exc
    return fingerprint


def read_planned_document(
    path: os.PathLike[str] | str, expected_fingerprint: tuple[int, ...]
) -> bytes:
    """Read one planned document exactly once under the 16 MiB ceiling.

    The pre-open, open-handle, post-read, and rebound-name fingerprints must all
    agree with the frozen plan's expected fingerprint. Content digest, UTF-8
    text, and classifier input all derive from this one byte string; the
    document is never reopened.
    """
    if type(expected_fingerprint) is not tuple or not expected_fingerprint:
        raise InternalError()
    try:
        return read_bounded_regular(
            path, MAX_DOCUMENT_BYTES, expected_fingerprint=expected_fingerprint
        )
    except (SecureIOError, OSError) as exc:
        raise BadInput() from exc


# --------------------------------------------------------------------------
# Joint topology preflight
# --------------------------------------------------------------------------


def portable_component_key(component: str) -> str:
    """The portable comparison key for one path component."""
    if type(component) is not str:
        raise InternalError()
    return unicodedata.normalize("NFC", component).casefold().rstrip(" .")


def _identity_of(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _absolute_target(path: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(path)
    except (TypeError, ValueError) as exc:
        raise BadInput() from exc
    if type(raw) is not str or not raw or "\x00" in raw:
        raise BadInput()
    absolute = Path(os.path.abspath(raw))
    if not absolute.is_absolute() or absolute.name in {"", ".", ".."}:
        raise BadInput()
    if any(part in {"", ".", ".."} for part in absolute.parts[1:]):
        raise BadInput()
    return absolute


def _open_directory_chain(path: Path) -> tuple[list[int], list[tuple[int, int]]]:
    """Pin every directory component of ``path`` through descriptor handles.

    ``O_NOFOLLOW | O_DIRECTORY`` refuses a symlink or non-directory component,
    and each opened handle is required to be the exact node the name resolved
    to, so a component swapped between the lookup and the open refuses.
    """
    flags = (
        os.O_RDONLY
        | int(getattr(os, "O_DIRECTORY", 0))
        | int(getattr(os, "O_CLOEXEC", 0))
        | int(getattr(os, "O_NOFOLLOW", 0))
        | int(getattr(os, "O_BINARY", 0))
    )
    opened: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        current = os.open(path.anchor or "/", flags)
        opened.append(current)
        anchor = os.fstat(current)
        if not stat.S_ISDIR(anchor.st_mode):
            raise PolicyRefused()
        identities.append(_identity_of(anchor))
        for component in path.parts[1:]:
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            following = os.open(component, flags, dir_fd=current)
            opened.append(following)
            info = os.fstat(following)
            if (
                not stat.S_ISDIR(named.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or _identity_of(named) != _identity_of(info)
            ):
                raise PolicyRefused()
            identities.append(_identity_of(info))
            current = following
        return opened, identities
    except (PolicyRefused, OSError, TypeError, ValueError):
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise PolicyRefused() from None


def _revalidate_directory_chain(path: Path, descriptors: Sequence[int],
                                identities: Sequence[tuple[int, int]]) -> None:
    if len(descriptors) != len(path.parts) or len(identities) != len(descriptors):
        raise PolicyRefused()
    try:
        if _identity_of(os.fstat(descriptors[0])) != identities[0]:
            raise PolicyRefused()
        for index, component in enumerate(path.parts[1:]):
            named = os.stat(component, dir_fd=descriptors[index], follow_symlinks=False)
            opened = os.fstat(descriptors[index + 1])
            if (
                not stat.S_ISDIR(named.st_mode)
                or _identity_of(named) != _identity_of(opened)
                or _identity_of(opened) != identities[index + 1]
            ):
                raise PolicyRefused()
    except (PolicyRefused, OSError, TypeError, ValueError):
        raise PolicyRefused() from None


def _leaf_stat(parent: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PolicyRefused() from exc


# --------------------------------------------------------------------------
# Native-Windows arm of the topology preflight
#
# ``os.open`` on a directory fails on native Windows and ``dir_fd`` is not
# supported there at all, so the POSIX descriptor-chain helpers above cannot
# run under ``os.name == "nt"``. Spec 73 requires the same control shape on
# Windows -- retained descriptor-based directory handles for the report parent
# and the checkpoint parent, native ``(volume_serial, file_id)`` identity
# wherever POSIX uses ``(st_dev, st_ino)``, and identity revalidation through
# those retained handles -- so the preflight dispatches to
# ``windows_descriptor_io`` instead of degrading to path-string calls.
#
# Only the platform-specific *mechanics* are dispatched. The portable-component
# key collision logic, the ancestor/equality refusals, the leaf disjointness
# rules, and the commit-point discipline stay in exactly one place and run
# identically on both platforms.
# --------------------------------------------------------------------------


def _native_windows() -> bool:
    """The single platform seam the topology preflight dispatches on.

    One named predicate rather than an inline ``os.name`` test at each seam, so
    the dispatch layer can be exercised on a simulated native platform without
    mutating ``os.name`` itself -- which would also re-flavour every
    ``pathlib.Path`` constructed during the test and prove nothing about this
    module.
    """
    return os.name == "nt"


def _windows_backend() -> Any:
    """Import the native descriptor backend, or refuse.

    Imported lazily and only from the native arm: a POSIX run never reaches
    this, and ``windows_descriptor_io`` is not importable there at all.
    """
    try:
        import windows_descriptor_io as winio  # type: ignore
    except ImportError:
        raise PolicyRefused() from None
    return winio


def _windows_helper(winio: Any, name: str) -> Any:
    """Fetch one required native entry point, or refuse.

    A backend that does not expose the seam the preflight needs is a controlled
    refusal, never a silent fallback to the POSIX ``dir_fd`` calls.
    """
    helper = getattr(winio, name, None)
    if helper is None or not callable(helper):
        raise PolicyRefused()
    return helper


def _windows_constant(winio: Any, name: str) -> int:
    """Fetch one required native constant, or refuse."""
    value = getattr(winio, name, None)
    if type(value) is not int:
        raise PolicyRefused()
    return value


def _windows_identity_of(node: Any) -> tuple[int, int]:
    """The native counterpart of ``(st_dev, st_ino)``."""
    try:
        return int(node.volume_serial), int(node.file_id)
    except (AttributeError, TypeError, ValueError):
        raise PolicyRefused() from None


def _windows_close_handle(handle: int) -> None:
    try:
        _windows_helper(_windows_backend(), "close")(handle)
    except (PolicyRefused, OSError, TypeError, ValueError):
        pass


def _windows_open_directory_chain(path: Path) -> tuple[list[int], list[tuple[int, int]]]:
    """Retain a native handle for every directory component of ``path``.

    ``pin_directory_chain`` opens the volume root and then each component
    relative to the retained parent handle with ``FILE_OPEN_REPARSE_POINT`` and
    ``FILE_DIRECTORY_FILE``, so an indirected or non-directory component refuses
    exactly as ``O_NOFOLLOW | O_DIRECTORY`` does on POSIX. The handles are
    opened read-only: the preflight creates nothing.
    """
    winio = _windows_backend()
    pin = _windows_helper(winio, "pin_directory_chain")
    require_direct = _windows_helper(winio, "require_direct")
    opened: list[Any] = []
    try:
        # Collected before anything can fail, so no partial chain ever leaks.
        opened.extend(pin(path, writable_final=False))
        identities = [
            _windows_identity_of(require_direct(handle, "directory")) for handle in opened
        ]
        retained = [int(handle) for handle in opened]
        opened = []
        return retained, identities
    except (PolicyRefused, OSError, TypeError, ValueError):
        raise PolicyRefused() from None
    finally:
        for handle in reversed(opened):
            _windows_close_handle(handle)


def _windows_revalidate_directory_chain(
    path: Path, handles: Sequence[int], identities: Sequence[tuple[int, int]]
) -> None:
    """Re-resolve every named component and require the retained handles.

    ``revalidate_directory_chain`` walks the names again and compares each
    freshly opened component against the corresponding retained handle; the
    frozen ``(volume_serial, file_id)`` pairs are then re-checked through those
    same retained handles, so a component swapped underneath the preflight
    refuses either way.
    """
    if len(handles) != len(path.parts) or len(identities) != len(handles):
        raise PolicyRefused()
    winio = _windows_backend()
    revalidate = _windows_helper(winio, "revalidate_directory_chain")
    require_direct = _windows_helper(winio, "require_direct")
    try:
        revalidate(path, tuple(handles))
        for handle, identity in zip(handles, identities):
            if _windows_identity_of(require_direct(handle, "directory")) != identity:
                raise PolicyRefused()
    except (PolicyRefused, OSError, TypeError, ValueError):
        raise PolicyRefused() from None


@dataclass(frozen=True)
class _TopologyLeaf:
    """One present leaf, in the platform-neutral terms the preflight uses."""

    is_directory: bool
    is_indirect: bool
    identity: tuple[int, int]


def _windows_leaf_node(parent: int, name: str) -> _TopologyLeaf | None:
    winio = _windows_backend()
    probe = _windows_helper(winio, "probe_leaf_node")
    reparse = _windows_constant(winio, "FILE_ATTRIBUTE_REPARSE_POINT")
    try:
        node = probe(parent, name)
        if node is None:
            return None
        return _TopologyLeaf(
            is_directory=getattr(node, "kind", None) == "directory",
            is_indirect=bool(int(getattr(node, "attributes", 0)) & reparse),
            identity=_windows_identity_of(node),
        )
    except (PolicyRefused, OSError, TypeError, ValueError):
        raise PolicyRefused() from None


# -- the platform seams the preflight actually calls ------------------------


def _topology_open_chain(path: Path) -> tuple[list[int], list[tuple[int, int]]]:
    if _native_windows():
        return _windows_open_directory_chain(path)
    return _open_directory_chain(path)


def _topology_revalidate_chain(
    path: Path, handles: Sequence[int], identities: Sequence[tuple[int, int]]
) -> None:
    if _native_windows():
        _windows_revalidate_directory_chain(path, handles, identities)
        return
    _revalidate_directory_chain(path, handles, identities)


def _topology_leaf(parent: int, name: str) -> _TopologyLeaf | None:
    if _native_windows():
        return _windows_leaf_node(parent, name)
    info = _leaf_stat(parent, name)
    if info is None:
        return None
    return _TopologyLeaf(
        is_directory=stat.S_ISDIR(info.st_mode),
        is_indirect=bool(getattr(info, "st_reparse_tag", 0)),
        identity=_identity_of(info),
    )


def _topology_close(handle: int) -> None:
    if _native_windows():
        _windows_close_handle(handle)
        return
    try:
        os.close(handle)
    except OSError:
        pass


class TopologyPreflight:
    """One joint topology preflight over the report file and checkpoint directory.

    The two targets must be disjoint under native identity and portable
    component comparison. Nothing is created, deleted, chmodded, rewritten, or
    followed here, and no failure path creates the second target after the first
    target fails. Report publication is the terminal commit point: there is no
    post-publication revalidation.
    """

    __slots__ = (
        "report_path",
        "checkpoint_path",
        "resume",
        "_report_handles",
        "_report_identities",
        "_checkpoint_handles",
        "_checkpoint_identities",
        "_closed",
        "_committed",
    )

    def __init__(
        self,
        *,
        report_path: Path,
        checkpoint_path: Path,
        resume: bool,
        report_handles: list[int],
        report_identities: list[tuple[int, int]],
        checkpoint_handles: list[int],
        checkpoint_identities: list[tuple[int, int]],
    ) -> None:
        self.report_path = report_path
        self.checkpoint_path = checkpoint_path
        self.resume = resume
        self._report_handles = report_handles
        self._report_identities = report_identities
        self._checkpoint_handles = checkpoint_handles
        self._checkpoint_identities = checkpoint_identities
        self._closed = False
        self._committed = False

    @classmethod
    def check(
        cls,
        *,
        report_path: os.PathLike[str] | str,
        checkpoint_path: os.PathLike[str] | str,
        resume: bool,
    ) -> "TopologyPreflight":
        """Resolve, retain, and jointly validate both targets before creation."""
        if type(resume) is not bool:
            raise InternalError()
        report = _absolute_target(report_path)
        checkpoint = _absolute_target(checkpoint_path)
        if report == checkpoint or report.parts == checkpoint.parts:
            raise PolicyRefused()
        shorter, longer = sorted((report.parts, checkpoint.parts), key=len)
        if longer[: len(shorter)] == shorter:
            raise PolicyRefused()
        # Corresponding existing ancestor components that collide portably but
        # are spelled differently natively are refused at every shared depth,
        # including the two leaves when they sit at the same depth.
        for left, right in zip(report.parts, checkpoint.parts):
            if left != right and portable_component_key(left) == portable_component_key(right):
                raise PolicyRefused()

        report_handles, report_identities = _topology_open_chain(report.parent)
        checkpoint_handles: list[int] = []
        checkpoint_identities: list[tuple[int, int]] = []
        try:
            checkpoint_handles, checkpoint_identities = _topology_open_chain(
                checkpoint.parent
            )
            if report_identities[-1] == checkpoint_identities[-1] and (
                portable_component_key(report.name)
                == portable_component_key(checkpoint.name)
            ):
                raise PolicyRefused()
            preflight = cls(
                report_path=report,
                checkpoint_path=checkpoint,
                resume=resume,
                report_handles=report_handles,
                report_identities=report_identities,
                checkpoint_handles=checkpoint_handles,
                checkpoint_identities=checkpoint_identities,
            )
            preflight._require_disjoint_leaves(fresh_checkpoint=not resume)
            preflight._revalidate_chains()
            return preflight
        except BaseException:
            for descriptor in reversed(checkpoint_handles):
                _topology_close(descriptor)
            for descriptor in reversed(report_handles):
                _topology_close(descriptor)
            raise

    # -- internals ---------------------------------------------------------

    def _revalidate_chains(self) -> None:
        _topology_revalidate_chain(
            self.report_path.parent, self._report_handles, self._report_identities
        )
        _topology_revalidate_chain(
            self.checkpoint_path.parent,
            self._checkpoint_handles,
            self._checkpoint_identities,
        )

    def _require_disjoint_leaves(self, *, fresh_checkpoint: bool) -> None:
        report_leaf = _topology_leaf(self._report_handles[-1], self.report_path.name)
        if report_leaf is not None:
            # The report name is absent in fresh mode and in resume mode alike,
            # and it is never deleted, chmodded, rewritten, or followed.
            raise PolicyRefused()
        checkpoint_leaf = _topology_leaf(
            self._checkpoint_handles[-1], self.checkpoint_path.name
        )
        if fresh_checkpoint:
            if checkpoint_leaf is not None:
                raise PolicyRefused()
            return
        if checkpoint_leaf is None or not checkpoint_leaf.is_directory:
            raise PolicyRefused()
        if checkpoint_leaf.is_indirect:
            raise PolicyRefused()
        identity = checkpoint_leaf.identity
        # An existing identity alias may not make the checkpoint directory an
        # ancestor of the report, however it is lexically spelled.
        if identity in set(self._report_identities):
            raise PolicyRefused()

    # -- public seams ------------------------------------------------------

    def revalidate(self) -> None:
        """Revalidate after checkpoint open/create and before report publication.

        There is deliberately no post-publication revalidation: once the report
        is published this refuses rather than reopening the terminal commit.
        """
        if self._committed:
            raise InternalError()
        if self._closed:
            raise PolicyRefused()
        self._revalidate_chains()
        self._require_disjoint_leaves(fresh_checkpoint=False)
        self._revalidate_chains()

    def publish_report(self, payload: bytes) -> None:
        """Publish the report create-new. This is the terminal commit point."""
        if self._committed:
            raise InternalError()
        if self._closed or type(payload) is not bytes:
            raise PolicyRefused()
        self.revalidate()
        try:
            publish_create_new(
                self.report_path, payload, privacy_policy=PRIVACY_POLICY
            )
        except (SecureIOError, OSError) as exc:
            raise PolicyRefused() from exc
        self._committed = True
        self._release()

    @property
    def committed(self) -> bool:
        return self._committed

    def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in reversed(self._checkpoint_handles):
            _topology_close(descriptor)
        for descriptor in reversed(self._report_handles):
            _topology_close(descriptor)

    def close(self) -> None:
        self._release()

    def __enter__(self) -> "TopologyPreflight":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()


# ==========================================================================
# Increment D2: CLI, runner, and capability registration
#
# Everything below this banner is wiring. It adds no encoder, no classifier
# behaviour, no aggregate rule, and no checkpoint format: it composes the
# increment A/B/C/D1 seams in the spec's exact run order, emits the spec's
# exact progress lines, and hands the frozen report bytes to the terminal
# commit point.
# ==========================================================================

#: Capability task surface. ``tools/check_capabilities_drift.py`` reads this
#: module-level constant and requires a matching ``capabilities.d/`` fragment
#: whose ``surface`` equals it.
TASK_SURFACE = "validation"

#: The closed CLI grammar. There is no source-related option, no abbreviation,
#: no ``--option=value`` spelling, no single-dash alias, no positional, and no
#: usage text: an unknown, repeated, or malformed option refuses before any
#: output is created. Former grouping spellings are simply unknown options.
CLI_FLAG_OPTIONS = ("--resume",)
CLI_VALUE_OPTIONS = (
    "--manifest",
    "--report-out",
    "--checkpoint-dir",
    "--use",
    "--split",
    "--persona",
    "--ai-status",
    "--min-words",
)
CLI_REQUIRED_OPTIONS = ("--manifest", "--report-out", "--checkpoint-dir")
CLI_OPTIONS = CLI_FLAG_OPTIONS + CLI_VALUE_OPTIONS

#: ``--min-words`` default. The value is passed straight to
#: ``classify_register`` and is never a row filter.
MIN_WORDS_DEFAULT = 100

#: Canonical base-10 spelling of a positive integer: no sign, no whitespace,
#: no underscore, no leading zero, no non-ASCII digit.
_CANONICAL_INTEGER_RE = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def valid_h2_string(value: Any, *, max_bytes: int) -> bool:
    """The shared H2 string domain: NFC, nonblank, no edge whitespace, 1..N
    UTF-8 bytes, and no NUL, C0/C1, unpaired surrogate, or bidi control.

    The CLI ``--persona`` filter and the projected ``persona`` field share
    exactly this domain; ``path`` differs only in its byte ceiling. The
    projection seam owns the identical predicate on its side of the import
    boundary, and a test pins the two against one table of hostile values so
    they cannot drift apart.
    """
    if type(value) is not str:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if value != value.strip():
        return False
    try:
        encoded_len = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    if not (1 <= encoded_len <= max_bytes):
        return False
    for char in value:
        code = ord(char)
        if code == 0 or code < 0x20 or 0x7F <= code <= 0x9F:
            return False
        if 0xD800 <= code <= 0xDFFF:
            return False
        if char in BIDI_CONTROLS:
            return False
    return True


@dataclass(frozen=True, repr=False)
class RunOptions:
    """One validated invocation of the runner.

    ``persona`` holds the raw validated filter. It reaches only
    :func:`scope_binding`; the fixed ``__repr__`` keeps it out of any
    accidental diagnostic rendering, and the report records only the Boolean
    ``persona_selected``.
    """

    manifest: str
    report_out: str
    checkpoint_dir: str
    resume: bool
    use: str | None
    split: str | None
    persona: str | None
    ai_status: str | None
    min_words: int

    def __repr__(self) -> str:
        return "<register sweep options>"

    @property
    def persona_selected(self) -> bool:
        return self.persona is not None


def _require_option_value(value: Any) -> str:
    """A value token: a nonempty string that is not itself an option spelling."""
    if type(value) is not str or not value or value.startswith("-"):
        raise BadInput()
    if "\x00" in value:
        raise BadInput()
    return value


def parse_arguments(argv: Sequence[str] | None = None) -> RunOptions:
    """Parse and validate the closed CLI grammar.

    Refuses with :class:`BadInput` — the exit-2 ``bad_input`` envelope — on an
    unknown option, a repeated option, a missing required option, a missing or
    option-shaped value, or any out-of-domain value. No usage text, option
    suggestion, echoed token, or rejected value ever reaches an output stream.
    """
    tokens = list(sys.argv[1:]) if argv is None else list(argv)
    seen: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if type(token) is not str:
            raise BadInput()
        if token in seen:
            raise BadInput()
        if token in CLI_FLAG_OPTIONS:
            seen[token] = True
            index += 1
            continue
        if token in CLI_VALUE_OPTIONS:
            if index + 1 >= len(tokens):
                raise BadInput()
            seen[token] = _require_option_value(tokens[index + 1])
            index += 2
            continue
        raise BadInput()

    for required in CLI_REQUIRED_OPTIONS:
        if required not in seen:
            raise BadInput()

    # Deferred import: see the module-header note on the one-way import rule.
    from manifest_validator import (  # type: ignore
        ALLOWED_AI_STATUS,
        ALLOWED_SPLIT,
        ALLOWED_USE,
    )

    use = _validated_enum(seen.get("--use"), ALLOWED_USE)
    split = _validated_enum(seen.get("--split"), ALLOWED_SPLIT)
    ai_status = _validated_enum(seen.get("--ai-status"), ALLOWED_AI_STATUS)

    persona = seen.get("--persona")
    if persona is not None and not valid_h2_string(persona, max_bytes=MAX_PERSONA_BYTES):
        raise BadInput()

    raw_min_words = seen.get("--min-words")
    if raw_min_words is None:
        min_words = MIN_WORDS_DEFAULT
    else:
        if _CANONICAL_INTEGER_RE.fullmatch(raw_min_words) is None:
            raise BadInput()
        min_words = int(raw_min_words)
        if not (MIN_WORDS_FLOOR <= min_words <= MIN_WORDS_CEILING):
            raise BadInput()

    return RunOptions(
        manifest=seen["--manifest"],
        report_out=seen["--report-out"],
        checkpoint_dir=seen["--checkpoint-dir"],
        resume=bool(seen.get("--resume", False)),
        use=use,
        split=split,
        persona=persona,
        ai_status=ai_status,
        min_words=min_words,
    )


def _validated_enum(value: Any, allowed: Any) -> str | None:
    """Exact membership in one closed ``manifest_validator`` enum, or null.

    Deliberately stricter than the warning-tolerant general validator: an
    unknown value refuses instead of producing a warning-only acceptance.
    """
    if value is None:
        return None
    if type(value) is not str or value not in allowed:
        raise BadInput()
    return value


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


def _resolve_stream(stream: Any, fallback: Any) -> Any:
    if stream is not None:
        return stream
    return getattr(fallback, "buffer", fallback)


def _emit_bytes(stream: Any, payload: bytes) -> None:
    """Write exact bytes to a pre-commit stream. Failures are controlled."""
    try:
        view = memoryview(payload)
        while view:
            written = stream.write(view)
            if type(written) is not int or written <= 0:
                raise InternalError()
            view = view[written:]
        flush = getattr(stream, "flush", None)
        if flush is not None:
            flush()
    except SweepRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 - closed controlled refusal
        raise InternalError() from exc


def _emit_progress(stream: Any, line: str) -> None:
    """Privacy-check one stderr progress leaf, then emit its exact bytes."""
    if type(line) is not str or claim_text_is_refused(line):
        raise InternalError()
    _emit_bytes(stream, line.encode("ascii", errors="strict"))


def _read_manifest(path: str) -> bytes:
    """One bounded 128 MiB read of a non-symlink regular manifest.

    Digest, validation, parsing, filtering, and planning all consume this one
    immutable byte string; the manifest is never reopened.
    """
    try:
        return read_bounded_regular(path, MAX_MANIFEST_BYTES)
    except (SecureIOError, OSError, ValueError) as exc:
        raise BadInput() from exc


def _projected_row_object(row: Any) -> dict[str, Any]:
    """The canonical seven-field projected-row object for one projected row."""
    return {
        "ai_status": row.ai_status,
        "manifest_ordinal": row.manifest_ordinal,
        "path": row.path,
        "persona": row.persona,
        "register": row.register,
        "split": row.split,
        "use": list(row.use),
    }


def _fingerprint_digest(fields: tuple[int, ...]) -> str:
    """Frame the platform fingerprint the planner already froze."""
    if os.name == "nt":  # pragma: no cover - native Windows
        return prefixed(windows_fingerprint_binding(fields)[1])
    return prefixed(posix_fingerprint_binding(fields)[1])


def _row_in_scope(row: Any, options: RunOptions) -> bool:
    """Apply the four scope filters to one H2-admissible projected row.

    ``use`` is membership in the row's validated list; ``split``, ``ai_status``,
    and ``persona`` are exact equality. An omitted filter includes every
    H2-admissible row; there are no implicit corpus-role defaults.
    """
    if options.use is not None and options.use not in row.use:
        return False
    if options.split is not None and row.split != options.split:
        return False
    if options.ai_status is not None and row.ai_status != options.ai_status:
        return False
    if options.persona is not None and row.persona != options.persona:
        return False
    return True


def _run(options: RunOptions, *, stdout: Any, stderr: Any) -> int:
    """Execute the spec's fixed run order and return the process exit code."""
    # Deferred import: see the module-header note on the one-way import rule.
    from manifest_validator import (  # type: ignore
        check_document_plan_collisions,
        project_register_sweep_manifest_bytes,
    )

    manifest_bytes = _read_manifest(options.manifest)
    projection = project_register_sweep_manifest_bytes(
        manifest_bytes, manifest_path=options.manifest
    )
    # The parser, the projected identities, and the planner have now all
    # consumed that one immutable byte string, and the manifest is never
    # reopened. From here the runner holds only bounded metadata plus one
    # document's text at a time.
    del manifest_bytes

    # -- scope selection over the already-frozen projection ----------------
    row_digests = [
        prefixed(projected_row_binding(_projected_row_object(row))[1])
        for row in projection.rows
    ]
    plan_by_ordinal = {
        entry.manifest_ordinal: entry for entry in projection.document_plan
    }
    if len(plan_by_ordinal) != len(projection.document_plan):
        raise InternalError()

    scoped_rows: list[Any] = []
    scoped_plan: list[Any] = []
    scoped_row_entries: list[dict[str, Any]] = []
    plan_entries: list[dict[str, Any]] = []
    for row in projection.rows:
        if not _row_in_scope(row, options):
            continue
        scoped_ordinal = len(scoped_rows)
        if scoped_ordinal >= MAX_SCOPED_DOCUMENTS:
            raise BadInput()
        entry = plan_by_ordinal.get(row.manifest_ordinal)
        if entry is None:
            raise InternalError()
        scoped_rows.append(row)
        scoped_plan.append(entry)
        scoped_row_entries.append(
            {
                "manifest_ordinal": row.manifest_ordinal,
                "projected_row_sha256": row_digests[row.manifest_ordinal],
                "scoped_ordinal": scoped_ordinal,
            }
        )
        plan_entries.append(
            {
                "candidate_index": entry.candidate_index,
                "file_fingerprint_sha256": _fingerprint_digest(entry.fingerprint),
                "projected_row_sha256": row_digests[row.manifest_ordinal],
                "scoped_ordinal": scoped_ordinal,
                "target_path_sha256": prefixed(
                    target_path_binding(entry.absolute_path)[1]
                ),
            }
        )

    # No two scoped rows may select the same normalized absolute path or the
    # same retained file identity. This runs on the scoped subset, before the
    # first document body is read.
    check_document_plan_collisions(tuple(scoped_plan))

    total = len(scoped_rows)

    # -- frozen identities -------------------------------------------------
    projected_manifest_sha256 = prefixed(
        projected_manifest_binding(row_digests)[1]
    )
    scope_sha256 = prefixed(
        scope_binding(
            use=options.use,
            split=options.split,
            ai_status=options.ai_status,
            persona=options.persona,
            min_words=options.min_words,
        )[1]
    )
    scoped_rows_sha256 = prefixed(scoped_rows_binding(scoped_row_entries)[1])
    document_plan_sha256 = prefixed(document_plan_binding(plan_entries)[1])

    # -- receipt-bound H1 identity ----------------------------------------
    receipt_path, classifier_path = default_h1_paths()
    binding = load_h1_binding(
        receipt_path=receipt_path, classifier_path=classifier_path
    )
    domains = RegisterDomains.from_binding(binding).validate()

    checkpoint_binding_sha256 = prefixed(
        checkpoint_binding(
            classifier_sha256=prefixed(binding.classifier_sha256),
            document_plan_sha256=document_plan_sha256,
            h1_receipt_sha256=prefixed(binding.receipt_sha256),
            mapping_sha256=prefixed(binding.mapping_sha256),
            projected_manifest_sha256=projected_manifest_sha256,
            refusal_contract_sha256=prefixed(binding.refusal_contract_sha256),
            scope_sha256=scope_sha256,
            scoped_rows_sha256=scoped_rows_sha256,
        )[1]
    )

    # -- joint topology preflight, then the checkpoint ---------------------
    preflight = TopologyPreflight.check(
        report_path=options.report_out,
        checkpoint_path=options.checkpoint_dir,
        resume=options.resume,
    )
    try:
        if options.resume:
            checkpoint = RegisterCheckpoint.resume(
                options.checkpoint_dir,
                domains=domains,
                checkpoint_binding_sha256=checkpoint_binding_sha256,
            )
        else:
            checkpoint = RegisterCheckpoint.create(
                options.checkpoint_dir,
                domains=domains,
                checkpoint_binding_sha256=checkpoint_binding_sha256,
            )
        try:
            preflight.revalidate()
            aggregate = _process_documents(
                options,
                binding=binding,
                domains=domains,
                checkpoint=checkpoint,
                scoped_rows=scoped_rows,
                scoped_plan=scoped_plan,
                row_digests=row_digests,
                total=total,
                stderr=stderr,
            )
        finally:
            checkpoint.close()

        _emit_progress(stderr, processing_complete_line(total))

        report = build_report(
            domains=domains,
            projected_manifest_sha256=projected_manifest_sha256,
            scoped_rows_sha256=scoped_rows_sha256,
            document_plan_sha256=document_plan_sha256,
            h1_receipt_sha256=prefixed(binding.receipt_sha256),
            classifier_sha256=prefixed(binding.classifier_sha256),
            mapping_sha256=prefixed(binding.mapping_sha256),
            refusal_contract_sha256=prefixed(binding.refusal_contract_sha256),
            checkpoint_binding_sha256=checkpoint_binding_sha256,
            scope=build_report_scope(
                use=options.use,
                split=options.split,
                ai_status=options.ai_status,
                min_words=options.min_words,
                persona_selected=options.persona_selected,
                scope_sha256=scope_sha256,
            ),
            input_rows=projection.input_rows,
            aggregate=aggregate,
        )
        frozen_report, _report_sha256, _envelope, envelope_bytes = freeze_publication(
            report=report, domains=domains
        )
        preflight.revalidate()
        # ---- terminal commit point ----
        preflight.publish_report(frozen_report)
    except BaseException:
        preflight.close()
        raise
    # Nothing after the commit may fail, inspect the checkpoint, emit stderr,
    # serialize data, or map a later condition to a controlled failure. The
    # total sink absorbs a closed or broken stdout and still returns success.
    emit_committed_success(envelope_bytes, stdout)
    return 0


def _process_documents(
    options: RunOptions,
    *,
    binding: H1Binding,
    domains: RegisterDomains,
    checkpoint: RegisterCheckpoint,
    scoped_rows: Sequence[Any],
    scoped_plan: Sequence[Any],
    row_digests: Sequence[str],
    total: int,
    stderr: Any,
) -> dict[str, Any]:
    """Process the scoped plan in manifest order and reassemble the aggregate.

    Resume reprocesses from the checkpoint's next sealed ordinal; every
    identity was recomputed from inputs above and the checkpoint layer has
    already validated binding equality for the sealed chain.
    """
    sizes = shard_partition(total)
    start = checkpoint.next_scoped_ordinal
    published = len(checkpoint.shards)
    if published > len(sizes) or start != sum(sizes[:published]):
        raise PolicyRefused()

    # Sealed rows are re-associated with THIS run's frozen plan before any of
    # them is trusted. The shard codec validates each sealed row against itself
    # and its own recomputed inner hashes, which says nothing about whether the
    # row still describes the document the current plan assigns to that scoped
    # ordinal: a checkpoint rewritten by a same-UID writer (inner hashes
    # recomputed) would otherwise resume and publish a report that differs from
    # a fresh run over the same manifest. Each sealed ordinal is therefore bound
    # back to the scoped row, the projected-row digest, the planned file size,
    # and the declared family this run derived from the manifest itself.
    sealed_rows: list[dict[str, Any]] = []
    for sealed in checkpoint.shards:
        sealed_rows.extend(sealed.rows)
    if len(sealed_rows) != start:
        raise PolicyRefused()
    for ordinal, sealed_row in enumerate(sealed_rows):
        row = scoped_rows[ordinal]
        entry = scoped_plan[ordinal]
        if sealed_row["manifest_ordinal"] != row.manifest_ordinal:
            raise PolicyRefused()
        if sealed_row["projected_row_sha256"] != row_digests[row.manifest_ordinal]:
            raise PolicyRefused()
        # Index 2 is the size field of both the POSIX 5-tuple and the native
        # Windows 9-tuple planned fingerprint.
        if sealed_row["document_bytes"] != entry.fingerprint[2]:
            raise PolicyRefused()
        if sealed_row["declared_family"] != binding.resolve_family(row.register):
            raise PolicyRefused()

    # Running scoped-byte total across the whole plan, sealed prefix included.
    # The total body size of the scoped documents is INPUT, so a breach of
    # ``MAX_SCOPED_BYTES`` is a ``bad_input`` (exit 2) row of the spec's failure
    # map, not an internal error. This pre-add check is the routing site: it
    # fires on the first document that would cross the ceiling, before the
    # aggregate builder's own post-add ``InternalError`` guard (which stays as
    # defense in depth over H2's already-validated in-memory construction) can
    # be reached from real input.
    scoped_bytes = 0
    for sealed in checkpoint.shards:
        scoped_bytes = _require_count(
            scoped_bytes + sealed.delta["counts"]["scoped_bytes"]
        )
    if scoped_bytes > MAX_SCOPED_BYTES:
        raise BadInput()

    pending = RegisterAggregate(domains)
    buffered: list[dict[str, Any]] = []
    shard_index = published
    for scoped_ordinal in range(start, total):
        row = scoped_rows[scoped_ordinal]
        entry = scoped_plan[scoped_ordinal]
        data = read_planned_document(entry.absolute_path, entry.fingerprint)
        scoped_bytes = _require_count(scoped_bytes + len(data))
        if scoped_bytes > MAX_SCOPED_BYTES:
            raise BadInput()
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BadInput() from exc
        declared_family = binding.resolve_family(row.register)
        result = binding.classify(text, min_words=options.min_words)
        pending.add_h1_result(
            declared_family=declared_family,
            result=result,
            document_bytes=len(data),
        )
        refusal_reason = result["refusal_reason"]
        buffered.append(
            {
                "manifest_ordinal": row.manifest_ordinal,
                "projected_row_sha256": row_digests[row.manifest_ordinal],
                "content_sha256": prefixed(raw_sha256(data)),
                "document_bytes": len(data),
                "words": result["evidence"]["n_words"],
                "declared_family": declared_family,
                "classified_family": (
                    None if refusal_reason is not None else result["primary"]
                ),
                "refusal_reason": refusal_reason,
            }
        )
        if len(buffered) == sizes[shard_index]:
            shard = checkpoint.publish_shard(
                buffered, final=shard_index + 1 == len(sizes)
            )
            if canonical_json(shard.delta) != canonical_json(pending.shard_delta()):
                raise InternalError()
            buffered = []
            pending = RegisterAggregate(domains)
            shard_index += 1
        completed = scoped_ordinal + 1
        if progress_is_eligible(completed, total, resume_from=start):
            _emit_progress(stderr, progress_line(completed, total))

    if buffered or shard_index != len(sizes):
        raise InternalError()

    aggregate = reassemble_aggregate(
        tuple(shard.delta for shard in checkpoint.shards), domains=domains
    )
    if aggregate["counts"]["scoped_documents"] != total:
        raise InternalError()
    return aggregate


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    """Run one register-composition sweep and return its exit code.

    Every controlled failure before the terminal report commit prints exactly
    one canonical golden error envelope on stdout, publishes no report, and
    returns 2, 3, or 4. ``KeyboardInterrupt`` and every other non-``Exception``
    ``BaseException`` propagate unconverted. Nothing after the commit can fail.
    """
    out = _resolve_stream(stdout, sys.stdout)
    err = _resolve_stream(stderr, sys.stderr)
    try:
        return _run(parse_arguments(argv), stdout=out, stderr=err)
    except BaseException as exc:
        # Re-raises ``KeyboardInterrupt`` and every other non-``Exception``.
        frozen, exit_code = freeze_controlled_error(controlled_failure_class(exc))
    try:
        _emit_bytes(out, frozen)
    except Exception:  # noqa: BLE001 - a closed consumer cannot add an artifact
        pass
    return exit_code


if __name__ == "__main__":
    # Second-copy class-identity hazard. Run as a script this module is loaded
    # under the name ``__main__``; ``manifest_validator``'s import-time
    # ``from register_sweep import BadInput, ...`` would then load a SECOND,
    # independent copy of this file under the name ``register_sweep``, with its
    # own distinct ``BadInput``/``PolicyRefused``/``InternalError`` classes.
    # A refusal raised from the manifest_validator seam would be an instance of
    # the second copy's classes, so ``controlled_failure_class``'s ``isinstance``
    # checks against *this* copy's classes would all miss and every seam-raised
    # ``BadInput`` would ship as exit 4 / ``internal_error`` instead of
    # exit 2 / ``bad_input``. Aliasing the running module under its import name
    # before ``main()`` makes the seam import resolve to this very module, so
    # there is exactly one set of refusal classes at the real entry point.
    sys.modules.setdefault("register_sweep", sys.modules[__name__])
    sys.exit(main())
