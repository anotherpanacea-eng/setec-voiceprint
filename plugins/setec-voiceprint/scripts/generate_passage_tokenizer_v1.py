#!/usr/bin/env python3
"""Generate the frozen Spec-80 tokenizer table from an offline UCD archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


UCD_BYTES = 8_248_819
UCD_SHA256 = "cb1c663d053926500cd501229736045752713a066bd75802098598b7a7056177"
UNICODE_DATA_SHA256 = "2fc713e6a31a87c4850a37fe2caffa4218180fadb5de86b43a143ddb4581fb86"
SPECIAL_CASING_SHA256 = "55a477efd933a52cd27e6a9bf70265bb2d8814af31aab07767abc8eb421f27ef"
SCALAR_LIMIT = 1_114_112
SURROGATE_START = 0xD800
SURROGATE_END = 0xDFFF


class GenerationError(ValueError):
    pass


def _frame(value: Any) -> bytes:
    if type(value) is int:
        tag, payload = b"i", str(value).encode("ascii")
    elif type(value) is str:
        tag, payload = b"s", value.encode("utf-8")
    elif type(value) is list:
        tag, payload = b"l", b"".join(_frame(item) for item in value)
    elif type(value) is dict:
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        tag = b"o"
        payload = b"".join(_frame(key) + _frame(value[key]) for key in keys)
    else:
        raise GenerationError("unsupported commitment value")
    return tag + len(payload).to_bytes(8, "big") + payload


def _strict_text(raw: bytes, label: str) -> str:
    if raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw or b"\r" in raw:
        raise GenerationError(f"{label}: forbidden encoding marker")
    if raw and not raw.endswith(b"\n"):
        raise GenerationError(f"{label}: missing final LF")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenerationError(f"{label}: invalid UTF-8") from exc


def _scalar(text: str, label: str) -> int:
    if not text or any(char not in "0123456789ABCDEF" for char in text):
        raise GenerationError(f"{label}: invalid scalar")
    value = int(text, 16)
    if not 0 <= value < SCALAR_LIMIT:
        raise GenerationError(f"{label}: scalar out of range")
    return value


def _sequence(text: str, label: str, *, empty_ok: bool = False) -> tuple[int, ...]:
    if not text:
        if empty_ok:
            return ()
        raise GenerationError(f"{label}: empty mapping")
    parts = text.split()
    if " ".join(parts) != text:
        raise GenerationError(f"{label}: noncanonical spacing")
    values = tuple(_scalar(part, label) for part in parts)
    if any(SURROGATE_START <= value <= SURROGATE_END for value in values):
        raise GenerationError(f"{label}: surrogate mapping")
    return values


def _read_members(archive: Path) -> tuple[bytes, bytes]:
    raw = archive.read_bytes()
    if len(raw) != UCD_BYTES or hashlib.sha256(raw).hexdigest() != UCD_SHA256:
        raise GenerationError("archive identity mismatch")
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        for wanted in ("UnicodeData.txt", "SpecialCasing.txt"):
            if names.count(wanted) != 1:
                raise GenerationError(f"archive member mismatch: {wanted}")
            info = zf.getinfo(wanted)
            if info.is_dir() or info.flag_bits & 1:
                raise GenerationError(f"unsafe archive member: {wanted}")
        unicode_data = zf.read("UnicodeData.txt")
        special_casing = zf.read("SpecialCasing.txt")
    if hashlib.sha256(unicode_data).hexdigest() != UNICODE_DATA_SHA256:
        raise GenerationError("UnicodeData.txt identity mismatch")
    if hashlib.sha256(special_casing).hexdigest() != SPECIAL_CASING_SHA256:
        raise GenerationError("SpecialCasing.txt identity mismatch")
    return unicode_data, special_casing


def _derive(unicode_raw: bytes, special_raw: bytes) -> dict[str, Any]:
    word = bytearray(SCALAR_LIMIT)
    lower: dict[int, tuple[int, ...]] = {}

    def apply(first: int, last: int, fields: list[str]) -> None:
        is_word = fields[2].startswith("L") or any(fields[index] for index in (6, 7, 8))
        simple_lower = (
            (_scalar(fields[13], "UnicodeData lowercase"),)
            if fields[13]
            else None
        )
        for codepoint in range(first, last + 1):
            if SURROGATE_START <= codepoint <= SURROGATE_END:
                continue
            if is_word or codepoint == 0x005F:
                word[codepoint] = 1
            if simple_lower is not None:
                lower[codepoint] = simple_lower

    pending: tuple[int, list[str]] | None = None
    rows = 0
    for line_number, line in enumerate(
        _strict_text(unicode_raw, "UnicodeData.txt").splitlines(), 1
    ):
        fields = line.split(";")
        if len(fields) != 15:
            raise GenerationError(f"UnicodeData.txt:{line_number}: field count")
        codepoint = _scalar(fields[0], "UnicodeData codepoint")
        rows += 1
        if fields[1].endswith(", First>"):
            if pending is not None:
                raise GenerationError("nested UnicodeData range")
            pending = (codepoint, fields)
            continue
        if fields[1].endswith(", Last>"):
            if pending is None or codepoint < pending[0]:
                raise GenerationError("unpaired UnicodeData range")
            start, first_fields = pending
            if fields[2:] != first_fields[2:]:
                raise GenerationError("UnicodeData range field drift")
            apply(start, codepoint, first_fields)
            pending = None
            continue
        if pending is not None:
            raise GenerationError("unterminated UnicodeData range")
        apply(codepoint, codepoint, fields)
    if pending is not None or rows != 34_931:
        raise GenerationError("UnicodeData row/range mismatch")

    unconditional = 0
    conditional = 0
    for line_number, line in enumerate(
        _strict_text(special_raw, "SpecialCasing.txt").splitlines(), 1
    ):
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        parts = [part.strip() for part in body.split(";")]
        if len(parts) == 5 and parts[4] == "":
            codepoint = _scalar(parts[0], "SpecialCasing codepoint")
            mapping = _sequence(parts[1], "SpecialCasing lower")
            _sequence(parts[2], "SpecialCasing title")
            _sequence(parts[3], "SpecialCasing upper")
            if not SURROGATE_START <= codepoint <= SURROGATE_END:
                lower[codepoint] = mapping
            unconditional += 1
        elif len(parts) == 6 and parts[4] and parts[5] == "":
            _scalar(parts[0], "conditional SpecialCasing codepoint")
            _sequence(parts[1], "conditional SpecialCasing lower", empty_ok=True)
            _sequence(parts[2], "conditional SpecialCasing title", empty_ok=True)
            _sequence(parts[3], "conditional SpecialCasing upper", empty_ok=True)
            conditional += 1
        else:
            raise GenerationError(f"SpecialCasing.txt:{line_number}: grammar")
    if (unconditional, conditional) != (103, 16):
        raise GenerationError("SpecialCasing row count mismatch")

    ranges: list[dict[str, int]] = []
    start: int | None = None
    previous = -1
    for codepoint, present in enumerate(word):
        if present and start is None:
            start = codepoint
        if not present and start is not None:
            ranges.append({"start": start, "end": codepoint - 1})
            start = None
        if present:
            previous = codepoint
    if start is not None:
        ranges.append({"start": start, "end": previous})

    mappings = [
        {"source": source, "output": list(output)}
        for source, output in sorted(lower.items())
        if output != (source,)
    ]
    core = {
        "schema": "setec-frozen-unicode-word-lower-data/1",
        "scalar_limit": SCALAR_LIMIT,
        "word_ranges": ranges,
        "lower_mappings": mappings,
    }
    commitment = hashlib.sha256(
        b"setec-passage-tokenizer-data-v1\n" + _frame(core)
    ).hexdigest()
    return {**core, "data_commitment_sha256": f"sha256:{commitment}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    unicode_data, special_casing = _read_members(args.archive)
    data = _derive(unicode_data, special_casing)
    payload = (
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    args.output.write_bytes(payload)
    print(
        json.dumps(
            {
                "bytes": len(payload),
                "commitment": data["data_commitment_sha256"],
                "mappings": len(data["lower_mappings"]),
                "ranges": len(data["word_ranges"]),
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
