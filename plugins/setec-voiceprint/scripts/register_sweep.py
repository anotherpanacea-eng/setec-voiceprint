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
manifest projection, checkpoint codec, runner, and report land in later
increments against these exact encoders.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
import unicodedata
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from manifest_validator import (  # type: ignore
    ALLOWED_AI_STATUS,
    ALLOWED_SPLIT,
    ALLOWED_USE,
)
from output_schema import build_error_output, build_output  # type: ignore

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
    """Build the complete 22-key create-new private report.

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
