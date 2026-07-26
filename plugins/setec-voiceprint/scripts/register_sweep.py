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

This increment supplies the canonical-encoding layer and the H1 binding layer:
every framed digest domain in the spec, the strict H1 receipt read, and the
receipt-bound classifier load plus its closed public-result validation. The
immutable shard checkpoint codec, the owner-private checkpoint directory, and
the joint topology preflight follow in the clearly delimited section at the end
of this module. The manifest projection, runner, and report land in later
increments against these exact encoders.
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
import unicodedata
from pathlib import Path
from typing import Any, Sequence

from shingle_dedup_checkpoint import CheckpointRefusal, ImmutableShardDirectory
from shingle_dedup_io import (
    SecureIOError,
    planned_fingerprint,
    publish_create_new,
    read_bounded_regular,
)

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


def canonical_json(value: Any) -> bytes:
    """Return the exact canonical JSON encoding of a closed-domain value.

    Inputs are composed only of string keys, NFC valid Unicode strings, JSON
    null/Boolean, signed-64-bit non-Boolean integers, and arrays/objects whose
    order and key set the spec fixes. Floats are forbidden in every canonical
    payload; ``allow_nan=False`` additionally refuses non-finite values.
    """
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
    occurs inside this function.
    """
    if type(domain) is not bytes or type(payload) is not bytes:
        raise InternalError()
    if not domain.endswith(b"\n") or not domain.isascii():
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
    ``{"rows":[]}``.
    """
    rows = []
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
        _require_int(entry["manifest_ordinal"], 0, INT64_MAX)
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
    classified = row["classified_family"]
    refusal = row["refusal_reason"]
    if (classified is None) == (refusal is None):
        raise InternalError()
    row_json = canonical_json(row)
    return row_json, hashlib.sha256(row_json).digest()


def aggregate_delta_binding(delta: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the reassembled six-key aggregate delta object."""
    if set(delta) != {
        "counts",
        "declared_family_inventory",
        "classified_family_inventory",
        "declared_by_classified_family",
        "refusal_inventory",
        "match_inventory",
    }:
        raise InternalError()
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
    ordered_rows = []
    previous = -1
    for entry in rows:
        if set(entry) != {"row_json_sha256", "scoped_ordinal"}:
            raise InternalError()
        ordinal = _require_int(
            entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1
        )
        if ordinal <= previous:
            raise InternalError()
        previous = ordinal
        ordered_rows.append(
            {
                "row_json_sha256": _require_prefixed(entry["row_json_sha256"]),
                "scoped_ordinal": ordinal,
            }
        )
    payload = canonical_json(
        {
            "aggregate_delta_sha256": _require_prefixed(aggregate_delta_sha256),
            "metadata": metadata,
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
    if type(value) is not dict or data != canonical_json(value) + b"\n":
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
        families = namespace["REGISTER_FAMILIES"]
        self.families = tuple(families)
        self.classified_domain = tuple(
            name for name in families if name != "unknown"
        )
        self.declared_domain = self.classified_domain + ("unknown",)
        self.refusal_domain = tuple(namespace["REGISTER_REFUSAL_REASONS"])

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
    and ``taxonomy``.
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

    Order is fixed: strict-read the receipt and check its pinned raw digest and
    exact schema; read the expected sibling classifier source once under the
    1 MiB ceiling and check the receipt's post-follow-on ``classifier_sha256``;
    compute and check the public mapping and refusal-contract digests; then
    compile and execute those exact source bytes in a private namespace.
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
    """Return the sibling classifier and plugin receipt paths for this script."""
    scripts = Path(__file__).resolve().parent
    classifier = scripts / CLASSIFIER_FILENAME
    receipt = scripts.parent / "references" / RECEIPT_FILENAME
    return receipt, classifier


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


# --------------------------------------------------------------------------
# Fixed inventory domains
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterDomains:
    """The frozen ``D``/``F``/``R`` domains for one receipt-bound H1 namespace.

    ``declared`` is ``D``, ``classified`` is ``F``, and ``refusals`` is ``R``.
    ``unknown`` is a declared bucket only; it is never a classified family.
    """

    declared: tuple[str, ...]
    classified: tuple[str, ...]
    refusals: tuple[str, ...]

    @classmethod
    def from_binding(cls, binding: "H1Binding") -> "RegisterDomains":
        return cls(
            declared=binding.declared_domain,
            classified=binding.classified_domain,
            refusals=binding.refusal_domain,
        )

    def validate(self) -> "RegisterDomains":
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
            or any(type(item) is not str for item in self.declared)
            or any(type(item) is not str for item in self.refusals)
        ):
            raise InternalError()
        return self


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
    """
    try:
        return planned_fingerprint(path)
    except (SecureIOError, OSError) as exc:
        raise BadInput() from exc


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

        report_handles, report_identities = _open_directory_chain(report.parent)
        checkpoint_handles: list[int] = []
        checkpoint_identities: list[tuple[int, int]] = []
        try:
            checkpoint_handles, checkpoint_identities = _open_directory_chain(
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
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for descriptor in reversed(report_handles):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise

    # -- internals ---------------------------------------------------------

    def _revalidate_chains(self) -> None:
        _revalidate_directory_chain(
            self.report_path.parent, self._report_handles, self._report_identities
        )
        _revalidate_directory_chain(
            self.checkpoint_path.parent,
            self._checkpoint_handles,
            self._checkpoint_identities,
        )

    def _require_disjoint_leaves(self, *, fresh_checkpoint: bool) -> None:
        report_leaf = _leaf_stat(self._report_handles[-1], self.report_path.name)
        if report_leaf is not None:
            # The report name is absent in fresh mode and in resume mode alike,
            # and it is never deleted, chmodded, rewritten, or followed.
            raise PolicyRefused()
        checkpoint_leaf = _leaf_stat(
            self._checkpoint_handles[-1], self.checkpoint_path.name
        )
        if fresh_checkpoint:
            if checkpoint_leaf is not None:
                raise PolicyRefused()
            return
        if checkpoint_leaf is None or not stat.S_ISDIR(checkpoint_leaf.st_mode):
            raise PolicyRefused()
        if getattr(checkpoint_leaf, "st_reparse_tag", 0):
            raise PolicyRefused()
        identity = _identity_of(checkpoint_leaf)
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
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(self._report_handles):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def close(self) -> None:
        self._release()

    def __enter__(self) -> "TopologyPreflight":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()
