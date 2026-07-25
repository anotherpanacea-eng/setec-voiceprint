#!/usr/bin/env python3
"""Build a private, model-free reconstructibility-targeted probe set.

M1 is a local CPU/stdlib builder.  It reads an owner-attested exact training
population, scores document leave-one-out DJ-Search coverage, and creates a
private probe-selection package.  A successful package is an evaluation input
receipt, not a memorization result.  It does not run or authorize a model,
tokenizer, trainer, GPU job, corpus activation, or operator evaluation.

Mutating M1 is intentionally Darwin-only.  Windows and Linux fail before
opening the private root; helper-level path and deterministic-core contracts
remain portable and testable.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from originality_audit import (  # noqa: E402
    DEFAULT_MIN_NGRAM,
    _MAX_SPAN,
    _TOKEN,
    _tokens,
    audit_originality,
)

SCHEMA_POPULATION = "setec-reconstructibility-probe-population/1"
SCHEMA_ATTESTATION = "setec-reconstructibility-population-attestation/1"
SCHEMA_PLAN = "setec-reconstructibility-probe-plan/1"
SCHEMA_BINDING = "setec-reconstructibility-checkpoint-binding/1"
SCHEMA_SHARD = "setec-reconstructibility-score-shard/1"
SCHEMA_PROBE = "setec-reconstructibility-probe/1"
SCHEMA_INDEX = "setec-reconstructibility-probe-index/1"
SCHEMA_RECEIPT = "setec-reconstructibility-probe-receipt/1"
POLICY = "document-loo-djsearch-tail-v1"
PUBLICATION_PROTOCOL = "setec-committed-directory/1"
PARTITIONS = ("qualification", "sealed_confirmation")

MAX_MANIFEST_LINES = MAX_UNITS = MAX_SCORE_SHARDS = 5_000
MAX_MANIFEST_LINE_BYTES = 65_536
MAX_MANIFEST_BYTES = 67_108_864
MAX_ATTESTATION_BYTES = MAX_PLAN_BYTES = 65_536
MAX_DOCUMENT_BYTES = 8_388_608
MAX_TOTAL_DOCUMENT_BYTES = 536_870_912
MAX_DOCUMENT_LOWERED_CODEPOINTS = 16_777_216
MAX_TOTAL_LOWERED_CODEPOINTS = 1_073_741_824
MAX_DOCUMENT_LOWER_MAP_OPERATIONS_PER_PASS = 101_163_296
MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_PASS = 6_446_450_944
LOWER_MAP_PASSES_PER_RUN = 3
MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_RUN = 19_339_352_832
MAX_DOCUMENT_TOKENS = 250_000
MAX_TOTAL_TOKENS = 2_000_000
MAX_LOO_DOCUMENT_PAIR_OPERATIONS = 25_000_000
MAX_LOO_TOKEN_PAIR_OPERATIONS = 4_000_000_000_000
MAX_BINDING_BYTES = 16_384
MAX_SCORE_SHARD_BYTES = 4_096
MAX_CHECKPOINT_INTENT_BYTES = 1_024
MAX_CHECKPOINT_RESERVED_BYTES = 20_497_408
OUTPUT_FIXED_BYTES_PER_PROBE = 16_384
MAX_RECEIPT_BYTES = 65_536
MAX_OUTPUT_INTENT_BYTES = 1_024
MAX_OUTPUT_RESERVED_BYTES = 2_147_483_648

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9_-]|[A-Za-z0-9]\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_FAULT_HOOK: Any = None


def _fault_point(label: str) -> None:
    hook = _FAULT_HOOK
    if hook is not None:
        hook(label)


class ProbeSetError(Exception):
    """Closed, prose-free operational failure."""

    def __init__(self, code: str, exit_code: int = 3):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


class _Pairs(list[tuple[str, Any]]):
    pass


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite")


def _walk_scalar_tree(root: Any) -> None:
    stack: list[tuple[Any, int]] = [(root, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > 100_000 or depth > 16:
            raise ProbeSetError("json_tree_limit_refused")
        if isinstance(value, str):
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
                raise ProbeSetError("json_unicode_scalar_refused")
        elif isinstance(value, float):
            if not math.isfinite(value) or (
                value == 0.0 and math.copysign(1.0, value) < 0
            ):
                raise ProbeSetError("json_canonicalization_refused")
        elif isinstance(value, _Pairs):
            for key, child in reversed(value):
                stack.append((child, depth + 1))
                stack.append((key, depth + 1))
        elif isinstance(value, list):
            for child in reversed(value):
                stack.append((child, depth + 1))


def _pairs_to_value(value: Any) -> Any:
    if isinstance(value, _Pairs):
        out: dict[str, Any] = {}
        for key, child in value:
            if key in out:
                raise ProbeSetError("json_duplicate_key_refused")
            out[key] = _pairs_to_value(child)
        return out
    if isinstance(value, list):
        return [_pairs_to_value(child) for child in value]
    return value


def canonical_json_line_v1(value: Any) -> bytes:
    _walk_scalar_tree(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            check_circular=True,
            skipkeys=False,
            sort_keys=True,
            separators=(",", ":"),
            indent=None,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProbeSetError("json_canonicalization_refused", 5) from exc
    return (text + "\n").encode("utf-8")


def strict_json_line_v1(raw: bytes, *, plan: bool = False) -> dict[str, Any]:
    code = 2 if plan else 3
    try:
        text = raw.decode("utf-8", "strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_Pairs,
            parse_constant=_reject_constant,
        )
    except ProbeSetError as exc:
        exc.exit_code = code
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProbeSetError("json_syntax_refused", code) from exc
    try:
        _walk_scalar_tree(parsed)
        value = _pairs_to_value(parsed)
    except ProbeSetError as exc:
        exc.exit_code = code
        raise
    if not isinstance(value, dict):
        raise ProbeSetError("json_schema_refused", code)
    if canonical_json_line_v1(value) != raw:
        raise ProbeSetError("json_canonicalization_refused", code)
    return value


def strict_jsonl_v1(raw: bytes) -> list[dict[str, Any]]:
    if not raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ProbeSetError("manifest_format_refused")
    rows: list[dict[str, Any]] = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        offset += len(line)
        if len(line) > MAX_MANIFEST_LINE_BYTES:
            raise ProbeSetError("manifest_resource_limit_refused")
        rows.append(strict_json_line_v1(line))
        if len(rows) > MAX_MANIFEST_LINES:
            raise ProbeSetError("manifest_resource_limit_refused")
    if offset != len(raw):
        raise ProbeSetError("manifest_format_refused")
    return rows


def _frame(value: Any) -> bytes:
    if value is None:
        tag, payload = b"n", b""
    elif isinstance(value, bool):
        tag, payload = b"b", b"\x01" if value else b"\x00"
    elif isinstance(value, int):
        tag, payload = b"i", str(value).encode("ascii")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ProbeSetError("canonical_frame_refused")
        tag, payload = b"f", struct.pack(">d", value)
    elif isinstance(value, str):
        _walk_scalar_tree(value)
        tag, payload = b"s", value.encode("utf-8")
    elif isinstance(value, bytes):
        tag, payload = b"y", value
    elif isinstance(value, (list, tuple)):
        tag, payload = b"l", b"".join(_frame(v) for v in value)
    elif isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise ProbeSetError("canonical_frame_refused")
        keys = sorted(value, key=lambda k: k.encode("utf-8"))
        if len(keys) != len(set(keys)):
            raise ProbeSetError("canonical_frame_refused")
        tag = b"o"
        payload = b"".join(_frame(k) + _frame(value[k]) for k in keys)
    else:
        raise ProbeSetError("canonical_frame_refused")
    return tag + len(payload).to_bytes(8, "big") + payload


def semantic_sha256(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + _frame(value)).hexdigest()


def plain_sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def portable_private_relative_path_v1(value: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not (1 <= len(value) <= 4_096)
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
    ):
        raise ProbeSetError("portable_private_path_refused")
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ProbeSetError("portable_private_path_refused") from exc
    if not (1 <= len(raw) <= 4_096):
        raise ProbeSetError("portable_private_path_refused")
    parts = tuple(value.split("/"))
    if not (1 <= len(parts) <= 64):
        raise ProbeSetError("portable_private_path_refused")
    for part in parts:
        if not (1 <= len(part) <= 128) or not _COMPONENT.fullmatch(part):
            raise ProbeSetError("portable_private_path_refused")
        stem = part.split(".", 1)[0]
        if _DEVICE.fullmatch(stem):
            raise ProbeSetError("portable_private_path_refused")
    return parts


def portable_collision_key(parts: Sequence[str]) -> tuple[str, ...]:
    return tuple(part.lower() for part in parts)


def _closed(value: Mapping[str, Any], keys: set[str], schema: str) -> None:
    if set(value) != keys or value.get("schema") != schema:
        raise ProbeSetError("json_schema_refused")


def _is_int(value: Any, *, positive: bool = False) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (not positive or value > 0)


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


POPULATION_KEYS = {
    "schema", "unit_id", "text_path", "content_sha256", "corpus_split",
    "evaluation_partition", "source_group", "document_family",
    "duplicate_component", "loss_mask_intervals",
}
ATTESTATION_KEYS = {
    "schema", "authoritative_training_snapshot", "training_run_receipt_sha256",
    "population_manifest_sha256", "membership_projection_sha256",
    "grouping_projection_sha256", "document_dedup_receipt_sha256",
    "passage_remediation_receipt_sha256", "source_group_method",
    "document_family_method", "duplicate_component_method", "authorized_by",
    "basis", "attested_at",
}
PLAN_KEYS = {
    "schema", "policy", "seed", "min_ngram", "max_span",
    "population_token_projection_sha256", "tail_count_by_partition",
    "probe_count_by_partition", "prompt_words", "minimum_suffix_words",
    "max_probes_per_duplicate_component", "max_probes_per_source_group",
    "max_probes_per_document_family", "mask_policy",
    "selection_frozen_before", "purpose",
}


def validate_population(rows: list[dict[str, Any]]) -> None:
    if not (1 <= len(rows) <= MAX_UNITS):
        raise ProbeSetError("manifest_resource_limit_refused")
    ids: set[str] = set()
    exact_paths: set[tuple[str, ...]] = set()
    path_keys: set[tuple[str, ...]] = set()
    partition_by_group: dict[tuple[str, str], str] = {}
    for row in rows:
        _closed(row, POPULATION_KEYS, SCHEMA_POPULATION)
        if (
            not all(_digest(row[k]) for k in (
                "unit_id", "content_sha256", "source_group",
                "document_family", "duplicate_component",
            ))
            or row["corpus_split"] != "train"
            or row["evaluation_partition"] not in PARTITIONS
        ):
            raise ProbeSetError("population_schema_refused")
        if row["unit_id"] in ids:
            raise ProbeSetError("population_duplicate_refused")
        ids.add(row["unit_id"])
        parts = portable_private_relative_path_v1(row["text_path"])
        key = portable_collision_key(parts)
        if parts in exact_paths or key in path_keys:
            raise ProbeSetError("portable_private_path_refused")
        exact_paths.add(parts)
        path_keys.add(key)
        intervals = row["loss_mask_intervals"]
        if not isinstance(intervals, list):
            raise ProbeSetError("population_schema_refused")
        previous_end = -1
        for interval in intervals:
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or not all(_is_int(x) for x in interval)
                or interval[0] < 0
                or interval[0] >= interval[1]
                or interval[0] <= previous_end
            ):
                raise ProbeSetError("population_schema_refused")
            previous_end = interval[1]
        for field in ("source_group", "document_family", "duplicate_component"):
            group = (field, row[field])
            old = partition_by_group.setdefault(group, row["evaluation_partition"])
            if old != row["evaluation_partition"]:
                raise ProbeSetError("cross_partition_grouping_refused")


def validate_attestation(value: dict[str, Any]) -> None:
    _closed(value, ATTESTATION_KEYS, SCHEMA_ATTESTATION)
    digest_fields = {
        "authoritative_training_snapshot", "training_run_receipt_sha256",
        "population_manifest_sha256", "membership_projection_sha256",
        "grouping_projection_sha256", "document_dedup_receipt_sha256",
    }
    if not all(_digest(value[k]) for k in digest_fields):
        raise ProbeSetError("attestation_schema_refused")
    passage = value["passage_remediation_receipt_sha256"]
    if passage is not None and not _digest(passage):
        raise ProbeSetError("attestation_schema_refused")
    for field in (
        "source_group_method", "document_family_method",
        "duplicate_component_method", "authorized_by", "basis",
    ):
        if not isinstance(value[field], str) or not value[field]:
            raise ProbeSetError("attestation_schema_refused")
    if not isinstance(value["attested_at"], str) or not _UTC.fullmatch(value["attested_at"]):
        raise ProbeSetError("attestation_schema_refused")


def validate_plan(value: dict[str, Any]) -> None:
    try:
        _closed(value, PLAN_KEYS, SCHEMA_PLAN)
    except ProbeSetError as exc:
        exc.exit_code = 2
        raise
    if (
        value["policy"] != POLICY
        or value["purpose"] != "matched_memorization_safety_evaluation"
        or value["mask_policy"] != "exclude_prompt_or_continuation_intersection"
        or value["min_ngram"] != DEFAULT_MIN_NGRAM
        or value["max_span"] != _MAX_SPAN
        or value["max_probes_per_duplicate_component"] != 1
        or not _digest(value["seed"])
        or not _digest(value["population_token_projection_sha256"])
        or not isinstance(value["selection_frozen_before"], str)
        or not _UTC.fullmatch(value["selection_frozen_before"])
    ):
        raise ProbeSetError("plan_schema_refused", 2)
    for field in ("tail_count_by_partition", "probe_count_by_partition"):
        counts = value[field]
        if (
            not isinstance(counts, dict)
            or set(counts) != set(PARTITIONS)
            or not all(_is_int(counts[p], positive=True) for p in PARTITIONS)
        ):
            raise ProbeSetError("plan_schema_refused", 2)
    for p in PARTITIONS:
        if value["tail_count_by_partition"][p] < value["probe_count_by_partition"][p]:
            raise ProbeSetError("plan_schema_refused", 2)
    for field in ("prompt_words", "minimum_suffix_words"):
        if not _is_int(value[field], positive=True):
            raise ProbeSetError("plan_schema_refused", 2)
    for field in ("max_probes_per_source_group", "max_probes_per_document_family"):
        if value[field] is not None and not _is_int(value[field], positive=True):
            raise ProbeSetError("plan_schema_refused", 2)


def membership_projection(rows: Sequence[Mapping[str, Any]]) -> str:
    members = [
        {"unit_id": row["unit_id"], "content_sha256": row["content_sha256"]}
        for row in sorted(rows, key=lambda r: r["unit_id"].encode("utf-8"))
    ]
    return semantic_sha256(b"setec-reconstructibility-membership-projection-v1\n", members)


def grouping_projection(rows: Sequence[Mapping[str, Any]]) -> str:
    members = [
        {
            "unit_id": row["unit_id"],
            "evaluation_partition": row["evaluation_partition"],
            "source_group": row["source_group"],
            "document_family": row["document_family"],
            "duplicate_component": row["duplicate_component"],
        }
        for row in sorted(rows, key=lambda r: r["unit_id"].encode("utf-8"))
    ]
    return semantic_sha256(b"setec-reconstructibility-grouping-projection-v1\n", members)


def population_token_projection(
    rows: Sequence[Mapping[str, Any]], texts: Mapping[str, str]
) -> str:
    members = [
        {"unit_id": row["unit_id"], "tokens": list(_tokens(texts[row["unit_id"]]))}
        for row in sorted(rows, key=lambda r: r["unit_id"].encode("utf-8"))
    ]
    return semantic_sha256(
        b"setec-reconstructibility-population-token-projection-v1\n", members
    )


@dataclass(frozen=True)
class TokenCoordinate:
    value: str
    lowered_start: int
    lowered_end: int
    source_start: int
    source_end: int


def lower_to_source_matches(text: str) -> tuple[str, list[TokenCoordinate]]:
    lowered = text.lower()
    matches = list(_TOKEN.finditer(lowered))
    expected = list(_tokens(text))
    starts: list[int | None] = [None] * len(matches)
    ends: list[int | None] = [None] * len(matches)
    start_cursor = end_cursor = 0
    q = 0
    for i, ch in enumerate(text):
        piece = ch.lower()
        width = len(piece)
        if width < 1:
            raise ProbeSetError("token_semantics_refused")
        r = q + width
        if r > len(lowered):
            raise ProbeSetError("token_semantics_refused")
        chunk = lowered[q:r]
        sigma = ch == "\u03a3" and width == 1 and chunk in ("\u03c3", "\u03c2")
        if chunk != piece and not sigma:
            raise ProbeSetError("token_semantics_refused")
        while start_cursor < len(matches) and q <= matches[start_cursor].start() < r:
            starts[start_cursor] = i
            start_cursor += 1
        while end_cursor < len(matches) and q < matches[end_cursor].end() <= r:
            ends[end_cursor] = i + 1
            end_cursor += 1
        q = r
    if q != len(lowered) or any(v is None for v in starts + ends):
        raise ProbeSetError("token_semantics_refused")
    result = [
        TokenCoordinate(
            match.group(0), match.start(), match.end(),
            int(starts[i]), int(ends[i]),
        )
        for i, match in enumerate(matches)
    ]
    if [m.value for m in result] != expected:
        raise ProbeSetError("token_semantics_refused")
    if any(
        not (0 <= m.source_start < m.source_end <= len(text))
        for m in result
    ) or any(a.source_start >= b.source_start for a, b in zip(result, result[1:])):
        raise ProbeSetError("token_semantics_refused")
    return lowered, result


def _interval_intersects(start: int, end: int, masks: Sequence[Sequence[int]]) -> bool:
    return any(start < mask_end and mask_start < end for mask_start, mask_end in masks)


def valid_anchors(
    row: Mapping[str, Any],
    text: str,
    plan: Mapping[str, Any],
    *,
    plan_sha256: str,
) -> list[dict[str, Any]]:
    del plan_sha256  # anchor identity binds the plan seed; probe identity binds its byte hash.
    _, matches = lower_to_source_matches(text)
    prompt_words = plan["prompt_words"]
    suffix_words = plan["minimum_suffix_words"]
    limit = len(matches) - prompt_words - suffix_words + 1
    anchors: list[dict[str, Any]] = []
    for start_token in range(max(0, limit)):
        prompt_start = matches[start_token].source_start
        prompt_end = matches[start_token + prompt_words].source_start
        continuation_end = matches[
            start_token + prompt_words + suffix_words - 1
        ].source_end
        masks = row["loss_mask_intervals"]
        if _interval_intersects(prompt_start, prompt_end, masks) or _interval_intersects(
            prompt_end, continuation_end, masks
        ):
            continue
        prompt = text[prompt_start:prompt_end]
        continuation = text[prompt_end:continuation_end]
        tokens = _tokens(text)
        if list(_tokens(prompt)) != tokens[start_token:start_token + prompt_words]:
            raise ProbeSetError("token_semantics_refused")
        if list(_tokens(continuation)) != tokens[
            start_token + prompt_words:start_token + prompt_words + suffix_words
        ]:
            raise ProbeSetError("token_semantics_refused")
        digest = semantic_sha256(
            b"setec-reconstructibility-probe-anchor-v1\n",
            {
                "seed": plan["seed"],
                "unit_id": row["unit_id"],
                "content_sha256": row["content_sha256"],
                "start_token": start_token,
            },
        )
        anchors.append({
            "anchor_sha256": digest,
            "start_token": start_token,
            "prompt_char_start": prompt_start,
            "prompt_char_end": prompt_end,
            "minimum_continuation_char_start": prompt_end,
            "minimum_continuation_char_end": continuation_end,
            "prompt_text": prompt,
            "minimum_continuation_text": continuation,
        })
    return anchors


def _score_projection(
    result: Mapping[str, Any], *, ordinal: int, row: Mapping[str, Any],
    binding_sha256: str, token_count: int,
) -> dict[str, Any]:
    histogram = result.get("matched_token_histogram")
    if not isinstance(histogram, dict):
        raise ProbeSetError("scorer_projection_refused")
    covered = 0
    n_spans = 0
    for key, count in histogram.items():
        if (
            not isinstance(key, str) or not re.fullmatch(r"0|[1-9][0-9]*", key)
            or not _is_int(count, positive=True)
        ):
            raise ProbeSetError("scorer_projection_refused")
        length = int(key)
        if not (DEFAULT_MIN_NGRAM <= length <= _MAX_SPAN):
            raise ProbeSetError("scorer_projection_refused")
        covered += length * count
        n_spans += count
    longest = result.get("longest_match_tokens")
    coverage_value = result.get("coverage")
    originality_value = result.get("originality")
    if (
        n_spans != result.get("n_matched_spans")
        or not _is_int(longest)
        or not isinstance(coverage_value, float)
        or not isinstance(originality_value, float)
        or not math.isfinite(coverage_value)
        or not math.isfinite(originality_value)
        or (coverage_value == 0.0 and math.copysign(1.0, coverage_value) < 0)
        or (originality_value == 0.0 and math.copysign(1.0, originality_value) < 0)
        or result.get("target_tokens") != token_count
        or result.get("min_ngram") != DEFAULT_MIN_NGRAM
        or result.get("max_span_cap") != _MAX_SPAN
        or result.get("longest_match_capped") is not (longest == _MAX_SPAN)
        or covered > token_count
        or not (0 <= longest <= min(_MAX_SPAN, token_count))
        or (bool(histogram) and max(map(int, histogram)) != longest)
        or ((covered == 0) != (longest < DEFAULT_MIN_NGRAM))
        or (covered > 0 and not (DEFAULT_MIN_NGRAM <= longest <= covered))
    ):
        raise ProbeSetError("scorer_projection_refused")
    coverage = round(covered / token_count, 6)
    originality = round(1.0 - covered / token_count, 6)
    if result.get("coverage") != coverage or result.get("originality") != originality:
        raise ProbeSetError("scorer_projection_refused")
    shard = {
        "schema": SCHEMA_SHARD,
        "binding_sha256": binding_sha256,
        "ordinal": ordinal,
        "unit_id": row["unit_id"],
        "coverage": coverage,
        "originality": originality,
        "covered_tokens": covered,
        "longest_match_tokens": longest,
        "longest_match_capped": longest == _MAX_SPAN,
        "min_ngram": DEFAULT_MIN_NGRAM,
        "max_span_cap": _MAX_SPAN,
        "target_tokens": token_count,
    }
    shard["shard_sha256"] = semantic_sha256(
        b"setec-reconstructibility-score-shard-v1\n", shard
    )
    return shard


def score_population(
    rows: Sequence[Mapping[str, Any]], texts: Mapping[str, str],
    binding_sha256: str,
) -> list[dict[str, Any]]:
    by_id = {row["unit_id"]: row for row in rows}
    order = (
        sorted((r["unit_id"] for r in rows if r["evaluation_partition"] == PARTITIONS[0]),
               key=lambda s: s.encode("utf-8"))
        + sorted((r["unit_id"] for r in rows if r["evaluation_partition"] == PARTITIONS[1]),
                 key=lambda s: s.encode("utf-8"))
    )
    out: list[dict[str, Any]] = []
    for ordinal, unit_id in enumerate(order):
        refs = [(other, texts[other]) for other in order if other != unit_id]
        result = audit_originality(
            texts[unit_id], refs,
            min_ngram=DEFAULT_MIN_NGRAM, max_span=_MAX_SPAN,
        )
        out.append(_score_projection(
            result, ordinal=ordinal, row=by_id[unit_id],
            binding_sha256=binding_sha256, token_count=len(_tokens(texts[unit_id])),
        ))
    return out


SHARD_KEYS = {
    "schema", "binding_sha256", "ordinal", "unit_id", "coverage",
    "originality", "covered_tokens", "longest_match_tokens",
    "longest_match_capped", "min_ngram", "max_span_cap",
    "target_tokens", "shard_sha256",
}


def admit_score_shard(
    raw: bytes, *, ordinal: int, unit_id: str, binding_sha256: str,
    token_count: int,
) -> dict[str, Any]:
    shard = strict_json_line_v1(raw)
    _closed(shard, SHARD_KEYS, SCHEMA_SHARD)
    stored_hash = shard["shard_sha256"]
    without_hash = dict(shard)
    del without_hash["shard_sha256"]
    expected_hash = semantic_sha256(
        b"setec-reconstructibility-score-shard-v1\n", without_hash
    )
    coverage = shard["coverage"]
    originality = shard["originality"]
    covered = shard["covered_tokens"]
    longest = shard["longest_match_tokens"]
    if (
        stored_hash != expected_hash
        or shard["binding_sha256"] != binding_sha256
        or shard["ordinal"] != ordinal
        or shard["unit_id"] != unit_id
        or not _is_int(covered)
        or not _is_int(longest)
        or not isinstance(coverage, float)
        or not isinstance(originality, float)
        or not math.isfinite(coverage)
        or not math.isfinite(originality)
        or shard["longest_match_capped"] is not (longest == _MAX_SPAN)
        or shard["min_ngram"] != DEFAULT_MIN_NGRAM
        or shard["max_span_cap"] != _MAX_SPAN
        or shard["target_tokens"] != token_count
        or not (0 <= covered <= token_count)
        or not (0 <= longest <= min(_MAX_SPAN, token_count))
        or coverage != round(covered / token_count, 6)
        or originality != round(1.0 - covered / token_count, 6)
        or ((covered == 0) != (longest < DEFAULT_MIN_NGRAM))
        or (covered > 0 and not (DEFAULT_MIN_NGRAM <= longest <= covered))
    ):
        raise ProbeSetError("checkpoint_shard_refused")
    return shard


def select_probes(
    rows: Sequence[Mapping[str, Any]], texts: Mapping[str, str],
    shards: Sequence[Mapping[str, Any]], plan: Mapping[str, Any],
    plan_sha256: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    by_id = {row["unit_id"]: row for row in rows}
    scores = {shard["unit_id"]: shard for shard in shards}
    selected: dict[str, list[dict[str, Any]]] = {p: [] for p in PARTITIONS}
    rejected = {
        "rejected_duplicate_component_cap": 0,
        "rejected_source_group_cap": 0,
        "rejected_document_family_cap": 0,
        "rejected_no_valid_anchor": 0,
    }
    for partition in PARTITIONS:
        candidates = [r for r in rows if r["evaluation_partition"] == partition]
        candidates.sort(key=lambda r: (
            -scores[r["unit_id"]]["coverage"],
            -scores[r["unit_id"]]["longest_match_tokens"],
            r["content_sha256"].encode("utf-8"),
            r["unit_id"].encode("utf-8"),
        ))
        tail = candidates[:plan["tail_count_by_partition"][partition]]
        duplicate_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        family_counts: Counter[str] = Counter()
        for tail_rank, row in enumerate(tail, 1):
            if len(selected[partition]) == plan["probe_count_by_partition"][partition]:
                break
            if duplicate_counts[row["duplicate_component"]] >= 1:
                rejected["rejected_duplicate_component_cap"] += 1
                continue
            source_cap = plan["max_probes_per_source_group"]
            if source_cap is not None and source_counts[row["source_group"]] >= source_cap:
                rejected["rejected_source_group_cap"] += 1
                continue
            family_cap = plan["max_probes_per_document_family"]
            if family_cap is not None and family_counts[row["document_family"]] >= family_cap:
                rejected["rejected_document_family_cap"] += 1
                continue
            anchors = valid_anchors(row, texts[row["unit_id"]], plan, plan_sha256=plan_sha256)
            if not anchors:
                rejected["rejected_no_valid_anchor"] += 1
                continue
            anchor = min(anchors, key=lambda a: (
                a["anchor_sha256"].encode("ascii"), a["start_token"]
            ))
            prompt_raw = anchor["prompt_text"].encode("utf-8")
            continuation_raw = anchor["minimum_continuation_text"].encode("utf-8")
            prompt_hash = plain_sha256(prompt_raw)
            continuation_hash = plain_sha256(continuation_raw)
            probe_preimage = {
                "plan_sha256": plan_sha256,
                "unit_id": row["unit_id"],
                "content_sha256": row["content_sha256"],
                "prompt_char_start": anchor["prompt_char_start"],
                "prompt_char_end": anchor["prompt_char_end"],
                "minimum_continuation_char_start": anchor["minimum_continuation_char_start"],
                "minimum_continuation_char_end": anchor["minimum_continuation_char_end"],
                "minimum_continuation_utf8_sha256": continuation_hash,
            }
            probe_id = semantic_sha256(
                b"setec-reconstructibility-probe-v1\n", probe_preimage
            )
            score = scores[row["unit_id"]]
            selected[partition].append({
                "probe": {
                    "schema": SCHEMA_PROBE,
                    "probe_id": probe_id,
                    "evaluation_partition": partition,
                    "prompt_text": anchor["prompt_text"],
                    "prompt_utf8_sha256": prompt_hash,
                    "minimum_continuation_text": anchor["minimum_continuation_text"],
                    "minimum_continuation_utf8_sha256": continuation_hash,
                },
                "index": {
                    "schema": SCHEMA_INDEX,
                    "probe_id": probe_id,
                    "unit_id": row["unit_id"],
                    "content_sha256": row["content_sha256"],
                    "evaluation_partition": partition,
                    "source_group": row["source_group"],
                    "document_family": row["document_family"],
                    "duplicate_component": row["duplicate_component"],
                    "coverage": score["coverage"],
                    "originality": score["originality"],
                    "longest_match_tokens": score["longest_match_tokens"],
                    "longest_match_capped": score["longest_match_capped"],
                    "tail_rank": tail_rank,
                    "start_token": anchor["start_token"],
                    "prompt_char_start": anchor["prompt_char_start"],
                    "prompt_char_end": anchor["prompt_char_end"],
                    "minimum_continuation_char_start": anchor[
                        "minimum_continuation_char_start"
                    ],
                    "minimum_continuation_char_end": anchor[
                        "minimum_continuation_char_end"
                    ],
                    "prompt_words": plan["prompt_words"],
                    "minimum_suffix_words": plan["minimum_suffix_words"],
                    "prompt_utf8_sha256": prompt_hash,
                    "minimum_continuation_utf8_sha256": continuation_hash,
                },
            })
            duplicate_counts[row["duplicate_component"]] += 1
            source_counts[row["source_group"]] += 1
            family_counts[row["document_family"]] += 1
        if len(selected[partition]) != plan["probe_count_by_partition"][partition]:
            raise ProbeSetError("selection_quota_refused")
    return selected, rejected


CLAIM_LICENSE = {
    "licenses": (
        "The named code revision deterministically selected and packaged the declared "
        "number of exact-text prompts and minimum continuations from the high "
        "document-level leave-one-out DJ-Search coverage tail of the owner-attested, "
        "hash-bound training population, under the named grouping, caps, partitions, "
        "and plan, before consuming generation or model output. It mechanically proves "
        "agreement with the attested membership and grouping projections, not the "
        "historical truth of the owner's training-run attestation."
    ),
    "does_not_license": (
        "Does not license that any prompt, document, component, source, or corpus is "
        "memorized, unsafe, contaminated, duplicated, clean, or suitable for training; "
        "an absolute memorization rate; a causal relation between reconstructibility "
        "and reproduction; an AI/human, authorship, plagiarism, copyright, quality, or "
        "provenance verdict; checkpoint or hyperparameter selection; corpus activation; "
        "training; deployment; adapter promotion; continuation of the stopped rung-3 "
        "frontier; or comparison with an arm evaluated on a different probe set, "
        "tokenizer, seed, decoding policy, or harness."
    ),
}


def _git_identity() -> tuple[str, str]:
    git = shutil.which("git")
    if not git or not os.path.isabs(git) or not Path(git).is_file():
        raise ProbeSetError("producer_identity_refused")
    source = Path(__file__).resolve(strict=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})

    def run(*args: str, ok=(0,)) -> str:
        completed = subprocess.run(
            [git, "-C", str(source.parent), *args],
            env=env, shell=False, capture_output=True, check=False,
        )
        if completed.returncode not in ok or completed.stderr:
            raise ProbeSetError("producer_identity_refused")
        try:
            return completed.stdout.decode("utf-8", "strict").strip()
        except UnicodeError as exc:
            raise ProbeSetError("producer_identity_refused") from exc

    root = Path(run("rev-parse", "--path-format=absolute", "--show-toplevel"))
    if run("rev-parse", "--show-object-format") != "sha1":
        raise ProbeSetError("producer_identity_refused")
    relative = source.relative_to(root).as_posix()
    if not run("ls-files", "--error-unmatch", "--", relative):
        raise ProbeSetError("producer_identity_refused")
    head1 = run("rev-parse", "--verify", "HEAD^{commit}")
    status = subprocess.run(
        [git, "-C", str(root), "status", "--porcelain=v1", "-z",
         "--untracked-files=all", "--ignore-submodules=none"],
        env=env, shell=False, capture_output=True, check=False,
    )
    head2 = run("rev-parse", "--verify", "HEAD^{commit}")
    if (
        status.returncode != 0 or status.stdout or status.stderr
        or head1 != head2 or not _HEX40.fullmatch(head1)
    ):
        raise ProbeSetError("producer_identity_refused")
    return head1, plain_sha256(source.read_bytes())


def preflight_resources(
    rows: Sequence[Mapping[str, Any]], texts: Mapping[str, str],
    raw_sizes: Mapping[str, int], plan: Mapping[str, Any],
) -> None:
    n = len(rows)
    tokens = {row["unit_id"]: len(_tokens(texts[row["unit_id"]])) for row in rows}
    chars = {row["unit_id"]: len(texts[row["unit_id"]]) for row in rows}
    lowered = {row["unit_id"]: len(texts[row["unit_id"]].lower()) for row in rows}
    operations = {
        unit: 2 * chars[unit] + 5 * lowered[unit] + 2 * tokens[unit]
        for unit in tokens
    }
    if (
        any(not (1 <= raw_sizes[u] <= MAX_DOCUMENT_BYTES) for u in tokens)
        or sum(raw_sizes.values()) > MAX_TOTAL_DOCUMENT_BYTES
        or any(not (1 <= lowered[u] <= MAX_DOCUMENT_LOWERED_CODEPOINTS) for u in tokens)
        or sum(lowered.values()) > MAX_TOTAL_LOWERED_CODEPOINTS
        or any(not (1 <= tokens[u] <= MAX_DOCUMENT_TOKENS) for u in tokens)
        or sum(tokens.values()) > MAX_TOTAL_TOKENS
        or any(v > MAX_DOCUMENT_LOWER_MAP_OPERATIONS_PER_PASS for v in operations.values())
        or sum(operations.values()) > MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_PASS
        or LOWER_MAP_PASSES_PER_RUN * sum(operations.values())
            > MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_RUN
        or n * (n - 1) > MAX_LOO_DOCUMENT_PAIR_OPERATIONS
        or sum(tokens[u] * (sum(tokens.values()) - tokens[u]) for u in tokens)
            > MAX_LOO_TOKEN_PAIR_OPERATIONS
        or MAX_BINDING_BYTES + n * MAX_SCORE_SHARD_BYTES + MAX_CHECKPOINT_INTENT_BYTES
            > MAX_CHECKPOINT_RESERVED_BYTES
    ):
        raise ProbeSetError("population_resource_limit_refused")
    for p in PARTITIONS:
        count = sum(row["evaluation_partition"] == p for row in rows)
        if count < 3 or plan["tail_count_by_partition"][p] > count:
            raise ProbeSetError("plan_population_refused", 2)
    if any(row["loss_mask_intervals"] for row in rows) and plan[
        "mask_policy"
    ] != "exclude_prompt_or_continuation_intersection":
        raise ProbeSetError("mask_authority_refused")


def _platform_refusal() -> ProbeSetError | None:
    if sys.platform == "darwin":
        return None
    if sys.platform.startswith("linux"):
        return ProbeSetError("linux_acl_backend_unsupported", 4)
    if os.name == "nt":
        return ProbeSetError("windows_publication_unsupported", 4)
    return ProbeSetError("platform_unsupported", 4)


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    mode: int
    links: int
    size: int


def _identity(info: os.stat_result) -> _Identity:
    return _Identity(
        int(info.st_dev), int(info.st_ino), int(info.st_mode),
        int(info.st_nlink), int(info.st_size),
    )


class _DarwinPrivateTree:
    """Descriptor-relative private tree access for the supported M1 host."""

    ACL_TYPE_EXTENDED = 0x00000100
    ACL_FIRST_ENTRY = 0
    ACL_NEXT_ENTRY = -1
    ACL_EXTENDED_ALLOW = 1
    ACL_EXTENDED_DENY = 2
    ACL_MAX_ENTRIES = 128

    def __init__(self, root: str):
        if sys.platform != "darwin":
            raise ProbeSetError("darwin_publication_backend_unavailable", 4)
        if (
            not isinstance(root, str) or not root.startswith("/")
            or root == "/" or root.endswith("/") or "//" in root
            or "\\" in root or "\x00" in root
        ):
            raise ProbeSetError("portable_private_path_refused")
        components = root[1:].split("/")
        if any(part in ("", ".", "..") for part in components):
            raise ProbeSetError("portable_private_path_refused")
        self._fds: list[int] = []
        self._entry_count = 0
        self._name_bytes = 0
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._fds.append(fd)
            self._validate_directory(fd, inside=False)
            for index, component in enumerate(components):
                self._exact_entry(fd, component)
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
                self._assert_named_identity(fd, component, child)
                self._fds.append(child)
                self._validate_directory(child, inside=index == len(components) - 1)
                fd = child
            self.root_fd = fd
            self.root_identity = _identity(os.fstat(fd))
        except ProbeSetError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise ProbeSetError("private_root_refused") from exc

    def close(self) -> None:
        for fd in reversed(getattr(self, "_fds", [])):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []

    def __enter__(self) -> "_DarwinPrivateTree":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _mode_private(info: os.stat_result, *, directory: bool) -> bool:
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        return (
            expected and info.st_uid == os.getuid()
            and not (stat.S_IMODE(info.st_mode) & 0o077)
            and (directory or info.st_nlink == 1)
        )

    @classmethod
    def _acl_check(cls, fd: int, *, inside: bool) -> None:
        path = "/usr/lib/libSystem.B.dylib"
        try:
            lib = ctypes.CDLL(path, use_errno=True)
            lib.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
            lib.acl_get_fd_np.restype = ctypes.c_void_p
            lib.acl_valid_fd_np.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            lib.acl_valid_fd_np.restype = ctypes.c_int
            lib.acl_get_entry.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
            ]
            lib.acl_get_entry.restype = ctypes.c_int
            lib.acl_get_tag_type.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
            ]
            lib.acl_get_tag_type.restype = ctypes.c_int
            lib.acl_get_flagset_np.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            ]
            lib.acl_get_flagset_np.restype = ctypes.c_int
            lib.acl_get_flag_np.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.acl_get_flag_np.restype = ctypes.c_int
            lib.acl_free.argtypes = [ctypes.c_void_p]
            lib.acl_free.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise ProbeSetError("private_acl_inspection_unavailable", 4) from exc
        ctypes.set_errno(0)
        acl = lib.acl_get_fd_np(fd, cls.ACL_TYPE_EXTENDED)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:
                return
            raise ProbeSetError("private_acl_inspection_unavailable", 4)
        failure: ProbeSetError | None = None
        try:
            validation_failed = (
                lib.acl_valid_fd_np(fd, cls.ACL_TYPE_EXTENDED, acl) != 0
            )
            processed = 0
            which = cls.ACL_FIRST_ENTRY
            while True:
                entry = ctypes.c_void_p()
                ctypes.set_errno(0)
                result = lib.acl_get_entry(acl, which, ctypes.byref(entry))
                if result == -1:
                    if processed and ctypes.get_errno() == errno.EINVAL:
                        break
                    raise ProbeSetError("private_acl_inspection_unavailable", 4)
                if result != 0 or not entry.value or processed >= cls.ACL_MAX_ENTRIES:
                    raise ProbeSetError("private_acl_inspection_unavailable", 4)
                tag = ctypes.c_int()
                if lib.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                    raise ProbeSetError("private_acl_inspection_unavailable", 4)
                if tag.value not in (cls.ACL_EXTENDED_ALLOW, cls.ACL_EXTENDED_DENY):
                    raise ProbeSetError("private_acl_inspection_unavailable", 4)
                if tag.value == cls.ACL_EXTENDED_ALLOW:
                    if validation_failed:
                        raise ProbeSetError("private_acl_refused")
                    if inside:
                        raise ProbeSetError("private_acl_refused")
                    flags = ctypes.c_void_p()
                    if lib.acl_get_flagset_np(entry, ctypes.byref(flags)) != 0:
                        raise ProbeSetError("private_acl_inspection_unavailable", 4)
                    for inherit_flag in (1 << 5, 1 << 6, 1 << 8):
                        present = lib.acl_get_flag_np(flags, inherit_flag)
                        if present not in (0, 1):
                            raise ProbeSetError(
                                "private_acl_inspection_unavailable", 4
                            )
                        if present:
                            raise ProbeSetError("private_acl_refused")
                processed += 1
                which = cls.ACL_NEXT_ENTRY
            if validation_failed:
                raise ProbeSetError("private_acl_inspection_unavailable", 4)
        except ProbeSetError as exc:
            failure = exc
        finally:
            if lib.acl_free(acl) != 0 and failure is None:
                failure = ProbeSetError("private_acl_inspection_unavailable", 4)
        if failure is not None:
            raise failure

    def _validate_directory(self, fd: int, *, inside: bool = True) -> _Identity:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ProbeSetError("private_directory_refused")
        if inside and not self._mode_private(info, directory=True):
            raise ProbeSetError("private_directory_refused")
        self._acl_check(fd, inside=inside)
        return _identity(info)

    def _validate_file(self, fd: int) -> _Identity:
        info = os.fstat(fd)
        if not self._mode_private(info, directory=False):
            raise ProbeSetError("private_file_refused")
        self._acl_check(fd, inside=True)
        return _identity(info)

    def _exact_entry(self, parent_fd: int, requested: str, *, absent: bool = False) -> bool:
        requested_raw = os.fsencode(requested)
        requested_key = requested.lower()
        requested_normalized = unicodedata.normalize("NFC", requested).casefold()
        found = False
        collision = False
        per_entries = 0
        per_bytes = 0
        try:
            iterator = os.scandir(parent_fd)
            with iterator:
                for entry in iterator:
                    raw = os.fsencode(entry.name)
                    per_entries += 1
                    per_bytes += len(raw)
                    self._entry_count += 1
                    self._name_bytes += len(raw)
                    if (
                        len(raw) > 4_096 or per_entries > MAX_UNITS
                        or per_bytes > MAX_MANIFEST_BYTES
                        or self._entry_count > MAX_LOO_DOCUMENT_PAIR_OPERATIONS
                        or self._name_bytes > MAX_TOTAL_DOCUMENT_BYTES
                    ):
                        raise ProbeSetError(
                            "private_directory_enumeration_limit_refused"
                        )
                    if raw == requested_raw:
                        found = True
                    elif (
                        entry.name.lower() == requested_key
                        or unicodedata.normalize("NFC", entry.name).casefold()
                            == requested_normalized
                    ):
                        collision = True
        except ProbeSetError:
            raise
        except OSError as exc:
            raise ProbeSetError("private_directory_enumeration_refused") from exc
        if collision or (absent and found) or (not absent and not found):
            raise ProbeSetError("portable_private_path_refused")
        return found

    @staticmethod
    def _assert_named_identity(parent_fd: int, name: str, child_fd: int) -> None:
        try:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            held = os.fstat(child_fd)
        except OSError as exc:
            raise ProbeSetError("private_file_identity_refused") from exc
        if (
            int(named.st_dev), int(named.st_ino)
        ) != (
            int(held.st_dev), int(held.st_ino)
        ):
            raise ProbeSetError("private_file_identity_refused")

    def open_dir(self, parts: Sequence[str]) -> tuple[int, _Identity]:
        fd = os.dup(self.root_fd)
        try:
            for part in parts:
                self._exact_entry(fd, part)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
                self._assert_named_identity(fd, part, child)
                os.close(fd)
                fd = child
                self._validate_directory(fd)
            return fd, _identity(os.fstat(fd))
        except Exception:
            os.close(fd)
            raise

    def open_parent(self, parts: Sequence[str]) -> tuple[int, str]:
        if not parts:
            raise ProbeSetError("portable_private_path_refused")
        fd, _ = self.open_dir(parts[:-1])
        return fd, parts[-1]

    @staticmethod
    def _refuse_git_marker(fd: int) -> None:
        try:
            info = os.stat(".git", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProbeSetError("git_worktree_detection_failed") from exc
        if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
            raise ProbeSetError("git_worktree_refused")
        raise ProbeSetError("git_worktree_detection_failed")

    def assert_no_git_authority_chain(self, parts: Sequence[str]) -> None:
        # Root and every descendant parent.
        self._refuse_git_marker(self.root_fd)
        fd = os.dup(self.root_fd)
        try:
            for component in parts:
                self._exact_entry(fd, component)
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=fd,
                )
                self._assert_named_identity(fd, component, child)
                os.close(fd)
                fd = child
                self._validate_directory(fd)
                self._refuse_git_marker(fd)
        finally:
            os.close(fd)
        # Root upward to a proved filesystem root.
        current = os.dup(self.root_fd)
        seen: set[tuple[int, int]] = set()
        try:
            while True:
                info = os.fstat(current)
                key = (int(info.st_dev), int(info.st_ino))
                if key in seen:
                    raise ProbeSetError("git_worktree_detection_failed")
                seen.add(key)
                self._refuse_git_marker(current)
                parent = os.open(
                    "..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                parent_info = os.fstat(parent)
                if (
                    int(parent_info.st_dev), int(parent_info.st_ino)
                ) == key:
                    os.close(parent)
                    break
                os.close(current)
                current = parent
        except ProbeSetError:
            raise
        except OSError as exc:
            raise ProbeSetError("git_worktree_detection_failed") from exc
        finally:
            os.close(current)

    def read_file(self, parts: Sequence[str], cap: int) -> tuple[bytes, _Identity]:
        parent, name = self.open_parent(parts)
        fd: int | None = None
        try:
            self._exact_entry(parent, name)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            self._assert_named_identity(parent, name, fd)
            before = self._validate_file(fd)
            if before.size > cap:
                raise ProbeSetError("input_resource_limit_refused")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(65_536, cap + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise ProbeSetError("input_resource_limit_refused")
                chunks.append(chunk)
            after = _identity(os.fstat(fd))
            if after != before:
                raise ProbeSetError("private_file_identity_refused")
            return b"".join(chunks), before
        except OSError as exc:
            raise ProbeSetError("private_file_refused") from exc
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def mkdir_exclusive(self, parent_fd: int, name: str) -> int:
        self._exact_entry(parent_fd, name, absent=True)
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.fchmod(fd, 0o700)
            self._validate_directory(fd)
            os.fsync(parent_fd)
            return fd
        except OSError as exc:
            raise ProbeSetError("publication_create_refused", 4) from exc

    def write_exclusive(
        self, parent_fd: int, name: str, raw: bytes, *, flush_parent: bool = True
    ) -> _Identity:
        fd: int | None = None
        try:
            self._exact_entry(parent_fd, name, absent=True)
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=parent_fd,
            )
            os.fchmod(fd, 0o600)
            created = self._validate_file(fd)
            view = memoryview(raw)
            written = 0
            while written < len(view):
                n = os.write(fd, view[written:])
                if n <= 0:
                    raise ProbeSetError("publication_write_failed", 4)
                written += n
            os.fsync(fd)
            final = self._validate_file(fd)
            if final.device != created.device or final.inode != created.inode:
                raise ProbeSetError("publication_source_swap_detected", 4)
            if flush_parent:
                os.fsync(parent_fd)
            return final
        except ProbeSetError:
            raise
        except OSError as exc:
            raise ProbeSetError("publication_write_failed", 4) from exc
        finally:
            if fd is not None:
                os.close(fd)

    def read_named_file(
        self, parent_fd: int, name: str, cap: int
    ) -> tuple[bytes, _Identity]:
        fd: int | None = None
        try:
            self._exact_entry(parent_fd, name)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            self._assert_named_identity(parent_fd, name, fd)
            before = self._validate_file(fd)
            if before.size > cap:
                raise ProbeSetError("input_resource_limit_refused")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(65_536, cap + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise ProbeSetError("input_resource_limit_refused")
                chunks.append(chunk)
            after = _identity(os.fstat(fd))
            if after != before:
                raise ProbeSetError("private_file_identity_refused")
            return b"".join(chunks), before
        finally:
            if fd is not None:
                os.close(fd)

    def checkpoint_prefix_length(self, checkpoint_fd: int, n_units: int) -> int:
        count = 0
        minimum: int | None = None
        maximum: int | None = None
        name_total = 0
        try:
            iterator = os.scandir(checkpoint_fd)
            with iterator:
                for entry in iterator:
                    raw = os.fsencode(entry.name)
                    name_total += len(raw)
                    if len(raw) > 4_096 or name_total > MAX_MANIFEST_BYTES:
                        raise ProbeSetError(
                            "private_directory_enumeration_limit_refused"
                        )
                    if entry.name == "binding.json":
                        continue
                    match = re.fullmatch(r"score-([0-9]{8})\.json", entry.name)
                    if match is None:
                        raise ProbeSetError("checkpoint_member_refused")
                    ordinal = int(match.group(1))
                    if ordinal >= n_units:
                        raise ProbeSetError("checkpoint_member_refused")
                    count += 1
                    minimum = ordinal if minimum is None else min(minimum, ordinal)
                    maximum = ordinal if maximum is None else max(maximum, ordinal)
        except ProbeSetError:
            raise
        except OSError as exc:
            raise ProbeSetError("checkpoint_member_refused") from exc
        if count == 0:
            return 0
        if minimum != 0 or maximum != count - 1:
            raise ProbeSetError("checkpoint_hole_refused")
        return count

    @staticmethod
    def require_empty_directory(fd: int) -> None:
        with os.scandir(fd) as iterator:
            try:
                next(iterator)
            except StopIteration:
                return
        raise ProbeSetError("checkpoint_recovery_required")

    def name_present(self, parent_fd: int, name: str) -> bool:
        try:
            self._exact_entry(parent_fd, name, absent=True)
        except ProbeSetError as exc:
            if exc.code != "portable_private_path_refused":
                raise
            self._exact_entry(parent_fd, name)
            return True
        return False

    @staticmethod
    def _checkpoint_intent(
        target_name: str, stage_identity: _Identity, raw: bytes
    ) -> tuple[dict[str, Any], bytes]:
        intent = {
            "schema": "setec-reconstructibility-checkpoint-publish-intent/1",
            "target_basename": target_name,
            "source_st_dev": stage_identity.device,
            "source_st_ino": stage_identity.inode,
            "target_byte_length": len(raw),
            "target_bytes_sha256": plain_sha256(raw),
        }
        intent["intent_sha256"] = semantic_sha256(
            b"setec-reconstructibility-checkpoint-publish-intent-v1\n", intent
        )
        return intent, canonical_json_line_v1(intent)

    def _finish_checkpoint_publish(
        self,
        staging_fd: int,
        checkpoint_fd: int,
        target_name: str,
        stage_name: str,
        raw: bytes,
        stage_identity: _Identity,
        cap: int,
        *,
        intent_exists: bool,
    ) -> None:
        _, intent_raw = self._checkpoint_intent(target_name, stage_identity, raw)
        intent_name = "publication-intent.json"
        if intent_exists:
            stored, _ = self.read_named_file(
                staging_fd, intent_name, MAX_CHECKPOINT_INTENT_BYTES
            )
            if stored != intent_raw:
                if intent_raw.startswith(stored) and not self.name_present(
                    checkpoint_fd, target_name
                ):
                    os.unlink(intent_name, dir_fd=staging_fd)
                    os.fsync(staging_fd)
                    self.write_exclusive(
                        staging_fd, intent_name, intent_raw, flush_parent=False
                    )
                else:
                    raise ProbeSetError("checkpoint_intent_refused")
        else:
            self.write_exclusive(
                staging_fd, intent_name, intent_raw, flush_parent=False
            )
        _fault_point("checkpoint_intent_file_flushed")
        os.fsync(staging_fd)
        _fault_point("checkpoint_intent_parent_flushed")
        replay, replay_identity = self.read_named_file(staging_fd, stage_name, cap)
        if replay != raw or replay_identity != stage_identity:
            raise ProbeSetError("publication_source_swap_detected", 4)
        self._exact_entry(checkpoint_fd, target_name, absent=True)
        try:
            self.rename_exclusive(staging_fd, stage_name, checkpoint_fd, target_name)
        except ProbeSetError as exc:
            if exc.code != "publication_primitive_unavailable":
                raise
            try:
                os.link(
                    stage_name, target_name,
                    src_dir_fd=staging_fd, dst_dir_fd=checkpoint_fd,
                    follow_symlinks=False,
                )
                os.fsync(checkpoint_fd)
                _fault_point("checkpoint_link_target_parent_flushed")
                os.unlink(stage_name, dir_fd=staging_fd)
                os.fsync(staging_fd)
                _fault_point("checkpoint_link_stage_parent_flushed")
            except OSError as link_exc:
                raise ProbeSetError("publication_primitive_unavailable", 4) from link_exc
        os.fsync(checkpoint_fd)
        _fault_point("checkpoint_target_parent_flushed")
        os.fsync(staging_fd)
        _fault_point("checkpoint_stage_parent_flushed")
        committed, committed_identity = self.read_named_file(
            checkpoint_fd, target_name, cap
        )
        if committed != raw or committed_identity != stage_identity:
            raise ProbeSetError("publication_source_swap_detected", 4)
        os.unlink(intent_name, dir_fd=staging_fd)
        os.fsync(staging_fd)
        _fault_point("checkpoint_intent_removed_flushed")

    def publish_checkpoint_member(
        self,
        staging_fd: int,
        checkpoint_fd: int,
        target_name: str,
        raw: bytes,
        cap: int,
    ) -> None:
        stage_name = f"{target_name}.stage"
        self._exact_entry(checkpoint_fd, target_name, absent=True)
        stage_identity = self.write_exclusive(staging_fd, stage_name, raw)
        os.fsync(staging_fd)
        self._finish_checkpoint_publish(
            staging_fd, checkpoint_fd, target_name, stage_name,
            raw, stage_identity, cap, intent_exists=False,
        )

    def recover_checkpoint_member(
        self,
        staging_fd: int,
        checkpoint_fd: int,
        target_name: str,
        raw: bytes,
        cap: int,
    ) -> bool:
        stage_name = f"{target_name}.stage"
        intent_name = "publication-intent.json"
        stage = self.name_present(staging_fd, stage_name)
        intent = self.name_present(staging_fd, intent_name)
        target = self.name_present(checkpoint_fd, target_name)
        if not stage and not intent:
            if not target:
                return False
            committed, _ = self.read_named_file(checkpoint_fd, target_name, cap)
            if committed != raw:
                raise ProbeSetError("checkpoint_member_refused")
            return True
        if stage and not intent and not target:
            staged, identity = self.read_named_file(staging_fd, stage_name, cap)
            if staged != raw:
                if raw.startswith(staged):
                    os.unlink(stage_name, dir_fd=staging_fd)
                    os.fsync(staging_fd)
                    return False
                raise ProbeSetError("checkpoint_member_refused")
            self._finish_checkpoint_publish(
                staging_fd, checkpoint_fd, target_name, stage_name,
                raw, identity, cap, intent_exists=False,
            )
            return True
        if stage and intent and not target:
            staged, identity = self.read_named_file(staging_fd, stage_name, cap)
            if staged != raw:
                raise ProbeSetError("checkpoint_member_refused")
            self._finish_checkpoint_publish(
                staging_fd, checkpoint_fd, target_name, stage_name,
                raw, identity, cap, intent_exists=True,
            )
            return True
        if target and intent and not stage:
            committed, identity = self.read_named_file(checkpoint_fd, target_name, cap)
            _, expected_intent = self._checkpoint_intent(
                target_name, identity, committed
            )
            stored, _ = self.read_named_file(
                staging_fd, intent_name, MAX_CHECKPOINT_INTENT_BYTES
            )
            if committed != raw or stored != expected_intent:
                raise ProbeSetError("publication_source_swap_detected", 4)
            os.fsync(checkpoint_fd)
            os.fsync(staging_fd)
            os.unlink(intent_name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return True
        if stage and target and intent:
            staged, stage_identity = self.read_named_file(staging_fd, stage_name, cap)
            committed, target_identity = self.read_named_file(
                checkpoint_fd, target_name, cap
            )
            _, expected_intent = self._checkpoint_intent(
                target_name, stage_identity, staged
            )
            stored, _ = self.read_named_file(
                staging_fd, intent_name, MAX_CHECKPOINT_INTENT_BYTES
            )
            if (
                staged != raw or committed != raw or stored != expected_intent
                or stage_identity.device != target_identity.device
                or stage_identity.inode != target_identity.inode
                or stage_identity.links != 2 or target_identity.links != 2
            ):
                raise ProbeSetError("publication_source_swap_detected", 4)
            os.unlink(stage_name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            _, after = self.read_named_file(checkpoint_fd, target_name, cap)
            if after.links != 1:
                raise ProbeSetError("publication_source_swap_detected", 4)
            os.fsync(checkpoint_fd)
            os.unlink(intent_name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return True
        raise ProbeSetError("checkpoint_recovery_refused")

    def checkpoint_pending_target(self, staging_fd: int) -> str | None:
        stage_target: str | None = None
        intent_target: str | None = None
        with os.scandir(staging_fd) as iterator:
            for entry in iterator:
                if entry.name == "publication-intent.json":
                    raw, _ = self.read_named_file(
                        staging_fd, entry.name, MAX_CHECKPOINT_INTENT_BYTES
                    )
                    value = strict_json_line_v1(raw)
                    if (
                        value.get("schema")
                        != "setec-reconstructibility-checkpoint-publish-intent/1"
                        or not isinstance(value.get("target_basename"), str)
                    ):
                        raise ProbeSetError("checkpoint_intent_refused")
                    intent_target = value["target_basename"]
                elif entry.name == "binding.json.stage":
                    stage_target = "binding.json"
                else:
                    match = re.fullmatch(r"(score-[0-9]{8}\.json)\.stage", entry.name)
                    if match is None or stage_target is not None:
                        raise ProbeSetError("checkpoint_recovery_refused")
                    stage_target = match.group(1)
        if (
            stage_target is not None and intent_target is not None
            and stage_target != intent_target
        ):
            raise ProbeSetError("checkpoint_recovery_refused")
        return stage_target or intent_target

    def replay_output_tree(
        self, root_fd: int, artifacts: Mapping[str, bytes]
    ) -> None:
        def require_names(fd: int, expected: set[str]) -> None:
            found: set[str] = set()
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    if entry.name not in expected or entry.name in found:
                        raise ProbeSetError("output_replay_refused")
                    found.add(entry.name)
            if found != expected:
                raise ProbeSetError("output_replay_refused")

        expected_root = {
            "qualification", "sealed_confirmation",
            "probe_receipt.json", ".setec-committed-v1",
        }
        require_names(root_fd, expected_root)
        for partition in PARTITIONS:
            self._exact_entry(root_fd, partition)
            directory = os.open(
                partition, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root_fd,
            )
            try:
                self._assert_named_identity(root_fd, partition, directory)
                self._validate_directory(directory)
                require_names(directory, {"probes.jsonl", "probe_index.jsonl"})
                for basename in ("probes.jsonl", "probe_index.jsonl"):
                    raw, _ = self.read_named_file(
                        directory, basename, MAX_OUTPUT_RESERVED_BYTES
                    )
                    if raw != artifacts[f"{partition}/{basename}"]:
                        raise ProbeSetError("output_replay_refused")
                os.fsync(directory)
            finally:
                os.close(directory)
        receipt, _ = self.read_named_file(
            root_fd, "probe_receipt.json", MAX_RECEIPT_BYTES
        )
        marker, _ = self.read_named_file(root_fd, ".setec-committed-v1", 0)
        if receipt != artifacts["probe_receipt.json"] or marker:
            raise ProbeSetError("output_replay_refused")
        os.fsync(root_fd)

    def complete_output_stage(
        self, stage_fd: int, artifacts: Mapping[str, bytes]
    ) -> None:
        root_names: set[str] = set()
        with os.scandir(stage_fd) as iterator:
            for entry in iterator:
                if entry.name not in {
                    "qualification", "sealed_confirmation",
                    "probe_receipt.json", ".setec-committed-v1",
                }:
                    raise ProbeSetError("output_replay_refused")
                root_names.add(entry.name)
        def existing_partition_complete(partition: str) -> bool:
            if partition not in root_names:
                return False
            fd = os.open(
                partition, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=stage_fd,
            )
            try:
                self._validate_directory(fd)
                names = {entry.name for entry in os.scandir(fd)}
                if names != {"probes.jsonl", "probe_index.jsonl"}:
                    return False
                for basename in names:
                    stored, _ = self.read_named_file(
                        fd, basename, MAX_OUTPUT_RESERVED_BYTES
                    )
                    if stored != artifacts[f"{partition}/{basename}"]:
                        return False
                return True
            finally:
                os.close(fd)
        if "sealed_confirmation" in root_names and not existing_partition_complete(
            "qualification"
        ):
            raise ProbeSetError("output_replay_refused")
        if "probe_receipt.json" in root_names and not all(
            existing_partition_complete(partition) for partition in PARTITIONS
        ):
            raise ProbeSetError("output_replay_refused")
        if (
            "sealed_confirmation" in root_names
            and "qualification" not in root_names
        ) or (
            "probe_receipt.json" in root_names
            and not {"qualification", "sealed_confirmation"} <= root_names
        ) or (
            ".setec-committed-v1" in root_names
            and "probe_receipt.json" not in root_names
        ):
            raise ProbeSetError("output_replay_refused")
        dirs: dict[str, int] = {}
        try:
            for partition in PARTITIONS:
                if partition in root_names:
                    fd = os.open(
                        partition,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=stage_fd,
                    )
                    self._assert_named_identity(stage_fd, partition, fd)
                    self._validate_directory(fd)
                else:
                    if root_names - set(PARTITIONS[:PARTITIONS.index(partition)]):
                        raise ProbeSetError("output_replay_refused")
                    fd = self.mkdir_exclusive(stage_fd, partition)
                    root_names.add(partition)
                dirs[partition] = fd
                present: set[str] = set()
                with os.scandir(fd) as iterator:
                    for entry in iterator:
                        if entry.name not in {"probes.jsonl", "probe_index.jsonl"}:
                            raise ProbeSetError("output_replay_refused")
                        present.add(entry.name)
                if "probe_index.jsonl" in present and "probes.jsonl" not in present:
                    raise ProbeSetError("output_replay_refused")
                for basename in ("probes.jsonl", "probe_index.jsonl"):
                    expected = artifacts[f"{partition}/{basename}"]
                    if basename in present:
                        stored, _ = self.read_named_file(
                            fd, basename, MAX_OUTPUT_RESERVED_BYTES
                        )
                        if stored != expected:
                            if expected.startswith(stored) and (
                                basename == "probe_index.jsonl"
                                or "probe_index.jsonl" not in present
                            ):
                                os.unlink(basename, dir_fd=fd)
                                os.fsync(fd)
                                self.write_exclusive(fd, basename, expected)
                            else:
                                raise ProbeSetError("output_replay_refused")
                    else:
                        self.write_exclusive(fd, basename, expected)
                os.fsync(fd)
            if "probe_receipt.json" in root_names:
                stored, _ = self.read_named_file(
                    stage_fd, "probe_receipt.json", MAX_RECEIPT_BYTES
                )
                if stored != artifacts["probe_receipt.json"]:
                    if artifacts["probe_receipt.json"].startswith(stored):
                        os.unlink("probe_receipt.json", dir_fd=stage_fd)
                        os.fsync(stage_fd)
                        self.write_exclusive(
                            stage_fd, "probe_receipt.json",
                            artifacts["probe_receipt.json"],
                        )
                    else:
                        raise ProbeSetError("output_replay_refused")
            else:
                self.write_exclusive(
                    stage_fd, "probe_receipt.json",
                    artifacts["probe_receipt.json"],
                )
            if ".setec-committed-v1" in root_names:
                marker, _ = self.read_named_file(
                    stage_fd, ".setec-committed-v1", 0
                )
                if marker:
                    raise ProbeSetError("output_replay_refused")
            else:
                self.write_exclusive(stage_fd, ".setec-committed-v1", b"")
            self.replay_output_tree(stage_fd, artifacts)
        finally:
            for fd in dirs.values():
                os.close(fd)

    @staticmethod
    def _output_intent(
        output_name: str, stage_identity: _Identity, receipt_sha256: str
    ) -> bytes:
        intent = {
            "schema": "setec-reconstructibility-output-publish-intent/1",
            "target_basename": output_name,
            "source_st_dev": stage_identity.device,
            "source_st_ino": stage_identity.inode,
            "probe_receipt_sha256": receipt_sha256,
        }
        intent["intent_sha256"] = semantic_sha256(
            b"setec-reconstructibility-output-publish-intent-v1\n", intent
        )
        return canonical_json_line_v1(intent)

    def _finish_output_publish(
        self,
        parent_fd: int,
        output_name: str,
        stage_name: str,
        intent_name: str,
        stage_fd: int,
        artifacts: Mapping[str, bytes],
        receipt_sha256: str,
        *,
        intent_exists: bool,
    ) -> None:
        self.replay_output_tree(stage_fd, artifacts)
        stage_identity = _identity(os.fstat(stage_fd))
        intent_raw = self._output_intent(
            output_name, stage_identity, receipt_sha256
        )
        if len(intent_raw) > MAX_OUTPUT_INTENT_BYTES:
            raise ProbeSetError("output_resource_limit_refused")
        if intent_exists:
            stored, _ = self.read_named_file(
                parent_fd, intent_name, MAX_OUTPUT_INTENT_BYTES
            )
            if stored != intent_raw:
                if intent_raw.startswith(stored) and not self.name_present(
                    parent_fd, output_name
                ):
                    os.unlink(intent_name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                    self.write_exclusive(
                        parent_fd, intent_name, intent_raw, flush_parent=False
                    )
                else:
                    raise ProbeSetError("output_intent_refused")
        else:
            self.write_exclusive(
                parent_fd, intent_name, intent_raw, flush_parent=False
            )
        replayed_intent, _ = self.read_named_file(
            parent_fd, intent_name, MAX_OUTPUT_INTENT_BYTES
        )
        if replayed_intent != intent_raw:
            raise ProbeSetError("output_intent_refused")
        _fault_point("output_intent_file_flushed")
        os.fsync(parent_fd)
        _fault_point("output_intent_parent_flushed")
        reopened_stage = os.open(
            stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            self._assert_named_identity(parent_fd, stage_name, reopened_stage)
            if _identity(os.fstat(reopened_stage)) != stage_identity:
                raise ProbeSetError("publication_source_swap_detected", 4)
            self.replay_output_tree(reopened_stage, artifacts)
        finally:
            os.close(reopened_stage)
        self._exact_entry(parent_fd, output_name, absent=True)
        self.rename_exclusive(parent_fd, stage_name, parent_fd, output_name)
        _fault_point("output_renamed")
        final_fd = os.open(
            output_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            self._validate_directory(final_fd)
            if _identity(os.fstat(final_fd)) != stage_identity:
                raise ProbeSetError("publication_source_swap_detected", 4)
            self.replay_output_tree(final_fd, artifacts)
            os.fsync(final_fd)
            os.fsync(parent_fd)
            _fault_point("output_target_parent_flushed")
            os.unlink(intent_name, dir_fd=parent_fd)
            _fault_point("output_intent_unlinked")
            os.fsync(parent_fd)
            _fault_point("output_intent_unlink_parent_flushed")
        finally:
            os.close(final_fd)

    def recover_or_publish_output(
        self,
        parent_fd: int,
        output_name: str,
        stage_name: str,
        intent_name: str,
        artifacts: Mapping[str, bytes],
        receipt_sha256: str,
        *,
        resume: bool,
    ) -> None:
        if resume:
            os.fsync(parent_fd)
        stage = self.name_present(parent_fd, stage_name)
        target = self.name_present(parent_fd, output_name)
        intent = self.name_present(parent_fd, intent_name)
        if resume:
            os.fsync(parent_fd)
        if stage and target:
            raise ProbeSetError("output_recovery_refused")
        if target and not intent:
            raise ProbeSetError("output_existing_refused")
        if intent and not stage and not target:
            raise ProbeSetError("output_recovery_refused")
        if target and intent and not stage:
            final_fd = os.open(
                output_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            try:
                self._validate_directory(final_fd)
                identity = _identity(os.fstat(final_fd))
                self.replay_output_tree(final_fd, artifacts)
                expected = self._output_intent(
                    output_name, identity, receipt_sha256
                )
                stored, _ = self.read_named_file(
                    parent_fd, intent_name, MAX_OUTPUT_INTENT_BYTES
                )
                if stored != expected:
                    raise ProbeSetError("publication_source_swap_detected", 4)
                os.fsync(final_fd)
                os.fsync(parent_fd)
                os.unlink(intent_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
                return
            finally:
                os.close(final_fd)
        if stage:
            stage_fd = os.open(
                stage_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            try:
                self._assert_named_identity(parent_fd, stage_name, stage_fd)
                self._validate_directory(stage_fd)
                os.fsync(parent_fd)
                if intent:
                    # A durable output intent is legal only after the complete
                    # marker-bearing tree existed; never mutate a partial tree
                    # under an intent.
                    self.replay_output_tree(stage_fd, artifacts)
                else:
                    self.complete_output_stage(stage_fd, artifacts)
                self._finish_output_publish(
                    parent_fd, output_name, stage_name, intent_name,
                    stage_fd, artifacts, receipt_sha256,
                    intent_exists=intent,
                )
                return
            finally:
                os.close(stage_fd)
        stage_fd = self.mkdir_exclusive(parent_fd, stage_name)
        try:
            self.complete_output_stage(stage_fd, artifacts)
            self._finish_output_publish(
                parent_fd, output_name, stage_name, intent_name,
                stage_fd, artifacts, receipt_sha256,
                intent_exists=False,
            )
        finally:
            os.close(stage_fd)

    @staticmethod
    def rename_exclusive(
        source_parent_fd: int, source: str, target_parent_fd: int, target: str
    ) -> None:
        try:
            lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            fn = lib.renameatx_np
            fn.argtypes = [
                ctypes.c_int, ctypes.c_char_p,
                ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
            ]
            fn.restype = ctypes.c_int
        except (OSError, AttributeError) as exc:
            raise ProbeSetError("publication_primitive_unavailable", 4) from exc
        if fn(
            source_parent_fd, os.fsencode(source),
            target_parent_fd, os.fsencode(target), 0x00000004,
        ) != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise ProbeSetError("publication_collision_refused")
            raise ProbeSetError("publication_rename_failed", 4)
        os.fsync(source_parent_fd)
        if source_parent_fd != target_parent_fd:
            os.fsync(target_parent_fd)


@dataclass(frozen=True)
class WindowsPrivateNodeProof:
    """Read-only proof projected from a retained native Windows handle.

    This helper is deliberately not a publication backend.  A future Windows
    milestone may populate the proof from CreateFileW/NtCreateFile and an
    owner-resolved DACL inspection; M1 still refuses before private access.
    """

    volume_serial: int
    file_id: int
    reparse_tag: int
    owner: str
    allow_principals: tuple[str, ...]


def validate_windows_private_node_proof(
    proof: WindowsPrivateNodeProof,
    *,
    expected_owner: str,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    if (
        not _is_int(proof.volume_serial)
        or not _is_int(proof.file_id)
        or proof.volume_serial < 0
        or proof.file_id < 0
        or not _is_int(proof.reparse_tag)
        or proof.reparse_tag != 0
        or not isinstance(expected_owner, str)
        or not expected_owner
        or proof.owner != expected_owner
        or not isinstance(proof.allow_principals, tuple)
        or not all(isinstance(value, str) for value in proof.allow_principals)
    ):
        raise ProbeSetError("windows_private_handle_refused")
    allowed = {
        expected_owner,
        r"NT AUTHORITY\SYSTEM",
        r"BUILTIN\Administrators",
    }
    if any(principal not in allowed for principal in proof.allow_principals):
        raise ProbeSetError("windows_private_dacl_refused")
    identity = (proof.volume_serial, proof.file_id)
    if expected_identity is not None and identity != expected_identity:
        raise ProbeSetError("windows_private_handle_refused")
    return identity


def _runtime_binding_fields() -> dict[str, str]:
    executable = Path(sys.executable).resolve(strict=True)
    executable_info_before = executable.stat()
    executable_raw = executable.read_bytes()
    if executable.stat() != executable_info_before:
        raise ProbeSetError("runtime_identity_refused")
    spec = importlib.util.find_spec("unicodedata")
    if spec is None or not spec.origin or spec.origin in ("built-in", "frozen"):
        raise ProbeSetError("runtime_identity_refused")
    module = Path(spec.origin).resolve(strict=True)
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "python_executable_sha256": plain_sha256(executable_raw),
        "unicode_data_version": unicodedata.unidata_version,
        "unicodedata_module_sha256": plain_sha256(module.read_bytes()),
    }


def _publish_after_identity_gate(
    expected: tuple[str, str], publisher: Any
) -> None:
    if _git_identity() != expected:
        raise ProbeSetError("producer_identity_refused")
    publisher()


def _unit_order(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return (
        sorted(
            (r["unit_id"] for r in rows if r["evaluation_partition"] == PARTITIONS[0]),
            key=lambda value: value.encode("utf-8"),
        )
        + sorted(
            (r["unit_id"] for r in rows if r["evaluation_partition"] == PARTITIONS[1]),
            key=lambda value: value.encode("utf-8"),
        )
    )


def _jsonl(values: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_line_v1(value) for value in values)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population-manifest", required=True)
    parser.add_argument("--population-attestation", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    refusal = _platform_refusal()
    if refusal is not None:
        raise refusal
    producer_revision, builder_source_sha256 = _git_identity()
    relative_values = {
        name: portable_private_relative_path_v1(getattr(args, name))
        for name in (
            "population_manifest", "population_attestation", "plan",
            "checkpoint_dir", "output_dir",
        )
    }
    exact = list(relative_values.values())
    collision = [portable_collision_key(parts) for parts in exact]
    if len(set(exact)) != len(exact) or len(set(collision)) != len(collision):
        raise ProbeSetError("portable_private_path_refused")
    checkpoint_parts = relative_values["checkpoint_dir"]
    output_parts = relative_values["output_dir"]
    if (
        checkpoint_parts[:len(output_parts)] == output_parts
        or output_parts[:len(checkpoint_parts)] == checkpoint_parts
        or portable_collision_key(checkpoint_parts)[:len(output_parts)]
            == portable_collision_key(output_parts)
        or portable_collision_key(output_parts)[:len(checkpoint_parts)]
            == portable_collision_key(checkpoint_parts)
    ):
        raise ProbeSetError("portable_private_path_refused")

    with _DarwinPrivateTree(args.private_root) as tree:
        plan_raw, _ = tree.read_file(relative_values["plan"], MAX_PLAN_BYTES)
        plan = strict_json_line_v1(plan_raw, plan=True)
        validate_plan(plan)
        manifest_raw, _ = tree.read_file(
            relative_values["population_manifest"], MAX_MANIFEST_BYTES
        )
        attestation_raw, _ = tree.read_file(
            relative_values["population_attestation"], MAX_ATTESTATION_BYTES
        )
        rows = strict_jsonl_v1(manifest_raw)
        attestation = strict_json_line_v1(attestation_raw)
        validate_population(rows)
        validate_attestation(attestation)
        manifest_sha = plain_sha256(manifest_raw)
        attestation_sha = plain_sha256(attestation_raw)
        plan_sha = plain_sha256(plan_raw)
        if (
            attestation["population_manifest_sha256"] != manifest_sha
            or attestation["membership_projection_sha256"] != membership_projection(rows)
            or attestation["grouping_projection_sha256"] != grouping_projection(rows)
            or attestation["attested_at"] > plan["selection_frozen_before"]
        ):
            raise ProbeSetError("population_attestation_refused")
        if (
            any(row["loss_mask_intervals"] for row in rows)
            and attestation["passage_remediation_receipt_sha256"] is None
        ):
            raise ProbeSetError("mask_authority_refused")

        texts: dict[str, str] = {}
        raw_sizes: dict[str, int] = {}
        identities: dict[str, _Identity] = {}
        metadata_paths = {
            relative_values["population_manifest"],
            relative_values["population_attestation"],
            relative_values["plan"],
        }
        metadata_keys = {portable_collision_key(p) for p in metadata_paths}
        for row in rows:
            parts = portable_private_relative_path_v1(row["text_path"])
            if parts in metadata_paths or portable_collision_key(parts) in metadata_keys:
                raise ProbeSetError("portable_private_path_refused")
            for owned in (checkpoint_parts, output_parts):
                parts_key = portable_collision_key(parts)
                owned_key = portable_collision_key(owned)
                if (
                    parts[:len(owned)] == owned
                    or owned[:len(parts)] == parts
                    or parts_key[:len(owned_key)] == owned_key
                    or owned_key[:len(parts_key)] == parts_key
                ):
                    raise ProbeSetError("portable_private_path_refused")
            raw, identity = tree.read_file(parts, MAX_DOCUMENT_BYTES)
            if plain_sha256(raw) != row["content_sha256"]:
                raise ProbeSetError("source_hash_refused")
            try:
                text = raw.decode("utf-8", "strict")
            except UnicodeError as exc:
                raise ProbeSetError("source_utf8_refused") from exc
            for start, end in row["loss_mask_intervals"]:
                if end > len(text):
                    raise ProbeSetError("population_schema_refused")
            texts[row["unit_id"]] = text
            raw_sizes[row["unit_id"]] = len(raw)
            identities[row["unit_id"]] = identity
        preflight_resources(rows, texts, raw_sizes, plan)
        token_projection = population_token_projection(rows, texts)
        if token_projection != plan["population_token_projection_sha256"]:
            raise ProbeSetError("population_token_projection_refused")
        source_snapshot = semantic_sha256(
            b"setec-reconstructibility-source-snapshot-v1\n",
            {
                "membership_projection_sha256": membership_projection(rows),
                "population_token_projection_sha256": token_projection,
            },
        )
        order = _unit_order(rows)
        runtime = _runtime_binding_fields()
        originality_source = Path(audit_originality.__code__.co_filename).resolve(strict=True)
        originality_source_sha = plain_sha256(originality_source.read_bytes())
        token_semantics = semantic_sha256(
            b"setec-reconstructibility-token-semantics-v1\n",
            [
                {
                    "source": source,
                    "lowered": lower_to_source_matches(source)[0],
                    "matches": [
                        [
                            m.value, m.lowered_start, m.lowered_end,
                            m.source_start, m.source_end,
                        ]
                        for m in lower_to_source_matches(source)[1]
                    ],
                }
                for source in (
                    "\u0130A", "A\u0130B", "A\u03A3", "A\u03A3B",
                    "A\u03A3\u0301", "A\u03A3\u0301B", "\u0130\u0130A",
                )
            ],
        )
        binding = {
            "schema": SCHEMA_BINDING,
            "policy": POLICY,
            "producer_revision": producer_revision,
            "population_manifest_sha256": manifest_sha,
            "population_attestation_sha256": attestation_sha,
            "population_token_projection_sha256": token_projection,
            "source_snapshot_sha256": source_snapshot,
            "plan_sha256": plan_sha,
            "builder_source_sha256": builder_source_sha256,
            "originality_source_sha256": originality_source_sha,
            **runtime,
            "token_semantics_sha256": token_semantics,
            "unit_order_sha256": semantic_sha256(
                b"setec-reconstructibility-unit-order-v1\n", order
            ),
            "n_units": len(order),
        }
        binding["binding_sha256"] = semantic_sha256(
            b"setec-reconstructibility-checkpoint-binding-v1\n", binding
        )
        binding_raw = canonical_json_line_v1(binding)
        if len(binding_raw) > MAX_BINDING_BYTES:
            raise ProbeSetError("checkpoint_resource_limit_refused")

        checkpoint_parent, checkpoint_name = tree.open_parent(checkpoint_parts)
        output_parent, output_name = tree.open_parent(output_parts)
        checkpoint_stage = f".{checkpoint_name}.setec-checkpoint-stage-v1"
        output_stage = f".{output_name}.setec-output-stage-v1"
        output_intent = f".{output_name}.setec-output-intent-v1"
        try:
            tree.assert_no_git_authority_chain(checkpoint_parts[:-1])
            tree.assert_no_git_authority_chain(output_parts[:-1])
            # Output create-new/refusal is established before checkpoint
            # creation or recovery; checkpoint progress cannot be created for
            # a name already unavailable to final publication.
            if not args.resume:
                for name in (output_name, output_stage, output_intent):
                    tree._exact_entry(output_parent, name, absent=True)
            if args.resume:
                # The conservative v1 resume path only admits the exact final
                # checkpoint.  Staged or intent-bearing crash states remain
                # fail-closed for explicit recovery rather than guessed.
                tree._exact_entry(checkpoint_parent, checkpoint_name)
                checkpoint_fd = os.open(
                    checkpoint_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=checkpoint_parent,
                )
                tree._validate_directory(checkpoint_fd)
                os.fsync(checkpoint_parent)
                try:
                    tree._exact_entry(checkpoint_parent, checkpoint_stage)
                except ProbeSetError as exc:
                    if exc.code != "portable_private_path_refused":
                        raise
                    checkpoint_stage_fd = tree.mkdir_exclusive(
                        checkpoint_parent, checkpoint_stage
                    )
                else:
                    checkpoint_stage_fd = os.open(
                        checkpoint_stage,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=checkpoint_parent,
                    )
                    tree._validate_directory(checkpoint_stage_fd)
                    os.fsync(checkpoint_parent)
                pending = tree.checkpoint_pending_target(checkpoint_stage_fd)
                if pending not in (None, "binding.json"):
                    # A shard cannot precede a durably admitted binding.
                    raise ProbeSetError("checkpoint_recovery_refused")
                recovered_binding = (
                    tree.recover_checkpoint_member(
                        checkpoint_stage_fd, checkpoint_fd,
                        "binding.json", binding_raw, MAX_BINDING_BYTES,
                    )
                    if pending == "binding.json"
                    else tree.name_present(checkpoint_fd, "binding.json")
                )
                if not recovered_binding:
                    tree.publish_checkpoint_member(
                        checkpoint_stage_fd, checkpoint_fd,
                        "binding.json", binding_raw, MAX_BINDING_BYTES,
                    )
                stored_binding, _ = tree.read_named_file(
                    checkpoint_fd, "binding.json", MAX_BINDING_BYTES
                )
                if stored_binding != binding_raw:
                    raise ProbeSetError("checkpoint_binding_refused")
            else:
                for name in (checkpoint_name, checkpoint_stage):
                    tree._exact_entry(checkpoint_parent, name, absent=True)
                checkpoint_fd = tree.mkdir_exclusive(
                    checkpoint_parent, checkpoint_name
                )
                checkpoint_stage_fd = tree.mkdir_exclusive(
                    checkpoint_parent, checkpoint_stage
                )
                tree.publish_checkpoint_member(
                    checkpoint_stage_fd, checkpoint_fd,
                    "binding.json", binding_raw, MAX_BINDING_BYTES,
                )

            prefix = tree.checkpoint_prefix_length(checkpoint_fd, len(order))
            by_id = {row["unit_id"]: row for row in rows}
            shards: list[dict[str, Any]] = []
            for ordinal, unit_id in enumerate(order):
                name = f"score-{ordinal:08d}.json"
                token_count = len(_tokens(texts[unit_id]))
                if ordinal < prefix:
                    existing, _ = tree.read_file(
                        tuple(checkpoint_parts) + (name,), MAX_SCORE_SHARD_BYTES
                    )
                    shard = admit_score_shard(
                        existing, ordinal=ordinal, unit_id=unit_id,
                        binding_sha256=binding["binding_sha256"],
                        token_count=token_count,
                    )
                    pending = tree.checkpoint_pending_target(checkpoint_stage_fd)
                    if pending == name:
                        tree.recover_checkpoint_member(
                            checkpoint_stage_fd, checkpoint_fd,
                            name, existing, MAX_SCORE_SHARD_BYTES,
                        )
                else:
                    refs = [
                        (other, texts[other]) for other in order if other != unit_id
                    ]
                    result = audit_originality(
                        texts[unit_id], refs,
                        min_ngram=DEFAULT_MIN_NGRAM, max_span=_MAX_SPAN,
                    )
                    shard = _score_projection(
                        result, ordinal=ordinal, row=by_id[unit_id],
                        binding_sha256=binding["binding_sha256"],
                        token_count=token_count,
                    )
                    raw = canonical_json_line_v1(shard)
                    if len(raw) > MAX_SCORE_SHARD_BYTES:
                        raise ProbeSetError("checkpoint_resource_limit_refused")
                    pending = tree.checkpoint_pending_target(checkpoint_stage_fd)
                    if pending not in (None, name):
                        raise ProbeSetError("checkpoint_recovery_refused")
                    recovered = (
                        tree.recover_checkpoint_member(
                            checkpoint_stage_fd, checkpoint_fd,
                            name, raw, MAX_SCORE_SHARD_BYTES,
                        )
                        if pending == name
                        else False
                    )
                    if not recovered:
                        tree.publish_checkpoint_member(
                            checkpoint_stage_fd, checkpoint_fd,
                            name, raw, MAX_SCORE_SHARD_BYTES,
                        )
                shards.append(shard)
                sys.stderr.write(f"scored {ordinal + 1}/{len(order)}\n")

            selections, rejected = select_probes(
                rows, texts, shards, plan, plan_sha
            )
            artifacts: dict[str, bytes] = {}
            for partition in PARTITIONS:
                values = sorted(
                    selections[partition],
                    key=lambda value: (
                        value["index"]["tail_rank"], value["probe"]["probe_id"]
                    ),
                )
                artifacts[f"{partition}/probes.jsonl"] = _jsonl(
                    item["probe"] for item in values
                )
                artifacts[f"{partition}/probe_index.jsonl"] = _jsonl(
                    item["index"] for item in values
                )
            counts = {
                "population_total": len(rows),
                "qualification_population": sum(
                    r["evaluation_partition"] == PARTITIONS[0] for r in rows
                ),
                "sealed_confirmation_population": sum(
                    r["evaluation_partition"] == PARTITIONS[1] for r in rows
                ),
                "qualification_tail": plan["tail_count_by_partition"][PARTITIONS[0]],
                "sealed_confirmation_tail": plan["tail_count_by_partition"][PARTITIONS[1]],
                "qualification_probes": plan["probe_count_by_partition"][PARTITIONS[0]],
                "sealed_confirmation_probes": plan["probe_count_by_partition"][PARTITIONS[1]],
                "capped_longest_matches_selected": sum(
                    item["index"]["longest_match_capped"]
                    for values in selections.values() for item in values
                ),
                **rejected,
            }
            receipt = {
                "schema": SCHEMA_RECEIPT,
                "policy": POLICY,
                "producer_revision": producer_revision,
                "population_manifest_sha256": manifest_sha,
                "population_attestation_sha256": attestation_sha,
                "source_snapshot_sha256": source_snapshot,
                "plan_sha256": plan_sha,
                "originality_source_sha256": originality_source_sha,
                "qualification_probes_sha256": plain_sha256(
                    artifacts["qualification/probes.jsonl"]
                ),
                "qualification_probe_index_sha256": plain_sha256(
                    artifacts["qualification/probe_index.jsonl"]
                ),
                "sealed_confirmation_probes_sha256": plain_sha256(
                    artifacts["sealed_confirmation/probes.jsonl"]
                ),
                "sealed_confirmation_probe_index_sha256": plain_sha256(
                    artifacts["sealed_confirmation/probe_index.jsonl"]
                ),
                "publication_protocol": PUBLICATION_PROTOCOL,
                "counts": counts,
                "evaluation_independence": {
                    "generation_consumed": False,
                    "model_or_tokenizer_consumed": False,
                    "cross_partition_grouping": False,
                    "builder_creation_accesses": 1,
                    "sealed_consumer_accesses": 0,
                    "sealed_reveal_events": 0,
                },
                "grouping_authority": "owner_attested",
                "passage_remediation_bound": (
                    attestation["passage_remediation_receipt_sha256"] is not None
                ),
                "claim_license_id": "reconstructibility-probe-sampling-v1",
                "claim_license_sha256": semantic_sha256(
                    b"setec-reconstructibility-claim-license-v1\n", CLAIM_LICENSE
                ),
                "activation_status": "frozen_non_activating_evaluation_input",
            }
            receipt["receipt_sha256"] = semantic_sha256(
                b"setec-reconstructibility-probe-receipt-v1\n", receipt
            )
            receipt_raw = canonical_json_line_v1(receipt)
            if len(receipt_raw) > MAX_RECEIPT_BYTES:
                raise ProbeSetError("output_resource_limit_refused")
            artifacts["probe_receipt.json"] = receipt_raw
            reserved = (
                sum(len(raw) for raw in artifacts.values())
                + sum(counts[f"{p}_probes"] for p in PARTITIONS)
                  * OUTPUT_FIXED_BYTES_PER_PROBE
                + MAX_OUTPUT_INTENT_BYTES
            )
            if reserved > MAX_OUTPUT_RESERVED_BYTES:
                raise ProbeSetError("output_resource_limit_refused")

            output_published = False
            try:
                # The producer identity is a pre-publication gate, not merely
                # a post-hoc receipt check.  Drift must precede every output
                # stage/intent/target recovery or mutation.
                _publish_after_identity_gate(
                    (producer_revision, builder_source_sha256),
                    lambda: tree.recover_or_publish_output(
                        output_parent, output_name, output_stage, output_intent,
                        artifacts, receipt["receipt_sha256"], resume=args.resume,
                    ),
                )
                output_published = True
            finally:
                os.close(checkpoint_stage_fd)
                os.close(checkpoint_fd)
            if output_published:
                tree._exact_entry(checkpoint_parent, checkpoint_stage)
                os.rmdir(checkpoint_stage, dir_fd=checkpoint_parent)
                os.fsync(checkpoint_parent)
            # Recheck code identity only after durable publication; a dirty or
            # moved producer makes the visible target unreported.
            if _git_identity() != (producer_revision, builder_source_sha256):
                raise ProbeSetError("producer_identity_refused")
            return receipt
        finally:
            os.close(checkpoint_parent)
            os.close(output_parent)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        receipt = run(args)
    except ProbeSetError as exc:
        sys.stderr.write(exc.code + "\n")
        return exc.exit_code
    if args.json:
        sys.stdout.buffer.write(canonical_json_line_v1(receipt))
    else:
        counts = receipt["counts"]
        print(
            f"receipt_sha256={receipt['receipt_sha256']} "
            f"qualification_probes={counts['qualification_probes']} "
            f"sealed_confirmation_probes={counts['sealed_confirmation_probes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
