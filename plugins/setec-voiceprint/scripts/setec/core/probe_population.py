"""Spec 75 population core: population validation, strict JSONL and portable paths.

Moved verbatim from ``reconstructibility_probe_set`` (CB-5) so the passage
authority modules can validate a probe population without importing that
builder. ``reconstructibility_probe_set`` re-exports every name here as the same
object. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from typing import Any, Mapping, Sequence


SCHEMA_POPULATION = "setec-reconstructibility-probe-population/1"


PARTITIONS = ("qualification", "sealed_confirmation")


MAX_MANIFEST_LINES = MAX_UNITS = MAX_SCORE_SHARDS = 5_000


MAX_MANIFEST_LINE_BYTES = 65_536


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9_-]|[A-Za-z0-9]\Z")


_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)


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
