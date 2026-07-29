"""Frozen, table-driven tokenizer for the Spec-80 producer contract.

This module deliberately performs no Unicode classification, normalization, or
case conversion through the host runtime.  Its complete behavior is determined
by the adjacent, committed JSON table.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).with_name("passage_tokenizer_data_v1.json")
DATA_SCHEMA = "setec-frozen-unicode-word-lower-data/1"
_HEX = set("0123456789abcdef")


class TokenizerDataError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8") + b"\n"


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _frame(value: Any) -> bytes:
    if type(value) is int:
        tag, payload = b"i", str(value).encode("ascii")
    elif type(value) is str:
        tag, payload = b"s", value.encode("utf-8")
    elif type(value) is list:
        tag, payload = b"l", b"".join(_frame(item) for item in value)
    elif type(value) is dict:
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        tag, payload = b"o", b"".join(_frame(key) + _frame(value[key]) for key in keys)
    else:
        raise TokenizerDataError("unsupported frame value")
    return tag + len(payload).to_bytes(8, "big") + payload


def _strict_int(value: Any) -> int:
    if type(value) is not int or not 0 <= value < 1_114_112:
        raise TokenizerDataError("invalid scalar")
    if 0xD800 <= value <= 0xDFFF:
        raise TokenizerDataError("surrogate scalar")
    return value


def load_data(path: Path = DATA_FILE) -> tuple[list[tuple[int, int]], dict[int, tuple[int, ...]], dict[str, Any]]:
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise TokenizerDataError("data must end with one LF")
    try:
        data = json.loads(raw.decode("utf-8"))
        canonical = _canonical(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TokenizerDataError("invalid data encoding") from exc
    if raw != canonical:
        raise TokenizerDataError("data must use strict canonical JSON")
    if type(data) is not dict or set(data) != {"schema", "scalar_limit", "word_ranges", "lower_mappings", "data_commitment_sha256"}:
        raise TokenizerDataError("data schema")
    if data["schema"] != DATA_SCHEMA or data["scalar_limit"] != 1_114_112:
        raise TokenizerDataError("data constants")
    commitment = data["data_commitment_sha256"]
    if not (type(commitment) is str and commitment.startswith("sha256:") and len(commitment) == 71 and set(commitment[7:]) <= _HEX):
        raise TokenizerDataError("data commitment")
    core = dict(data)
    del core["data_commitment_sha256"]
    if _digest(b"setec-passage-tokenizer-data-v1\n" + _frame(core)) != commitment:
        raise TokenizerDataError("data commitment mismatch")
    ranges: list[tuple[int, int]] = []
    previous = -1
    for row in data["word_ranges"]:
        if type(row) is not dict or set(row) != {"start", "end"}:
            raise TokenizerDataError("range schema")
        start, end = _strict_int(row["start"]), _strict_int(row["end"])
        if start > end or start <= previous:
            raise TokenizerDataError("range order")
        ranges.append((start, end)); previous = end
    mappings: dict[int, tuple[int, ...]] = {}
    previous = -1
    for row in data["lower_mappings"]:
        if type(row) is not dict or set(row) != {"source", "output"}:
            raise TokenizerDataError("mapping schema")
        source = _strict_int(row["source"])
        output = row["output"]
        if source <= previous or type(output) is not list or not output:
            raise TokenizerDataError("mapping order")
        mappings[source] = tuple(_strict_int(item) for item in output); previous = source
    return ranges, mappings, data


def tokenize(text: str, *, data_path: Path = DATA_FILE) -> list[dict[str, Any]]:
    if type(text) is not str:
        raise TypeError("text must be str")
    ranges, mappings, _data = load_data(data_path)
    def is_word(scalar: int) -> bool:
        return any(start <= scalar <= end for start, end in ranges)
    out: list[dict[str, Any]] = []
    start: int | None = None
    normalized: list[str] = []
    for index, char in enumerate(text):
        scalar = ord(char)
        if is_word(scalar):
            if start is None:
                start = index
            normalized.extend(chr(mapped) for mapped in mappings.get(scalar, (scalar,)))
        elif start is not None:
            out.append({"char_start": start, "char_end": index, "normalized_token": "".join(normalized)})
            start, normalized = None, []
    if start is not None:
        out.append({"char_start": start, "char_end": len(text), "normalized_token": "".join(normalized)})
    return out
