#!/usr/bin/env python3
"""Bounded deterministic transcript/non-prose staging screen (Spec 72)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Iterable, Sequence
import uuid

from claim_license import ClaimLicense
from output_schema import build_output


TASK_SURFACE = "validation"
TOOL_NAME = "nonprose_sweep"
SCRIPT_VERSION = "1.0"
METHOD_VERSION = "setec-nonprose-method/1"
REPORT_SCHEMA = "setec-nonprose-sweep-report/1"
CALIBRATION_STATUS = "operational_uncalibrated"

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 10_000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_DOCUMENT_BYTES = 256 * 1024 * 1024
MAX_LINES_PER_DOCUMENT = 200_000
MAX_LINE_BYTES = 1024 * 1024
MAX_WORDS_PER_DOCUMENT = 2_000_000
MAX_REPORT_BYTES = 16 * 1024 * 1024

_WORD_RE = re.compile(r"[^\W_]+(?:['’\-‐‑][^\W_]+)*", re.UNICODE)
_VTT_TAG_RE = re.compile(r"<[^>\r\n]{1,128}>")
_VTT_TIMESTAMP = r"(?:[0-9]{2,}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"
_VTT_SETTING = r"(?:vertical|line|position|size|align|region):[!-~]+"
_VTT_TIMING_RE = re.compile(
    rf"{_VTT_TIMESTAMP}[ \t]+-->[ \t]+{_VTT_TIMESTAMP}"
    rf"(?:[ \t]+{_VTT_SETTING})*",
    re.ASCII,
)
_JOINERS = frozenset("'’-‐‑")
_DISFLUENCIES = frozenset(
    {"um", "umm", "uh", "uhh", "erm", "er", "hmm", "mm-hmm", "uh-huh"}
)
_EXPLICIT_ROLES = frozenset(
    {
        "interviewer",
        "interviewee",
        "host",
        "guest",
        "moderator",
        "audience",
        "audience member",
        "unknown",
        "q",
        "a",
    }
)
_FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "disposition",
        "verdict",
        "label",
        "selection",
        "authorship",
        "provenance",
        "quality",
        "is_ai",
        "is_human",
    }
)
_THRESHOLDS = {
    "disfluencies_per_1000_strictly_greater_than": 6,
    "short_line_max_words": 5,
    "short_line_min_nonempty_lines_exclusive": 15,
    "short_line_percent_strictly_greater_than": 55,
    "speaker_label_percent_strictly_greater_than": 15,
    "vtt_any_hit": True,
}

_FAULT_HOOK: Callable[[str], None] = lambda _stage: None


class ControlledFailure(Exception):
    """A path-free expected refusal."""


class UsageFailure(ControlledFailure):
    """A sanitized command-line usage refusal."""


class _ClosedParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise UsageFailure("invalid arguments")


def _fault(stage: str) -> None:
    _FAULT_HOOK(stage)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _bounded_canonical_bytes(value: Any, limit: int) -> bytes:
    encoder = json.JSONEncoder(
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    chunks: list[bytes] = []
    size = 0
    for chunk in encoder.iterencode(value):
        raw = chunk.encode("utf-8")
        size += len(raw)
        if size + 1 > limit:
            raise ControlledFailure("report byte ceiling")
        chunks.append(raw)
    chunks.append(b"\n")
    return b"".join(chunks)


def _sha256_tag(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_bytes(stream: Any, raw: bytes) -> None:
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        offset = 0
        while offset < len(raw):
            written = binary.write(raw[offset:])
            if type(written) is not int or written <= 0 or written > len(raw) - offset:
                raise OSError("short console write")
            offset += written
        binary.flush()
        return
    text = raw.decode("utf-8")
    offset = 0
    while offset < len(text):
        written = stream.write(text[offset:])
        if type(written) is not int or written <= 0 or written > len(text) - offset:
            raise OSError("short console write")
        offset += written
    flush = getattr(stream, "flush", None)
    if flush is not None:
        flush()


def _emit_terminal(stream: Any, raw: bytes) -> None:
    try:
        _write_bytes(stream, raw)
    except BaseException:
        pass


def _physical_lines(text: str) -> list[str]:
    if not text:
        return []
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "" and text.endswith(("\r", "\n")):
        lines.pop()
    return lines


def _physical_byte_lines(raw: bytes) -> list[bytes]:
    if not raw:
        return []
    lines = re.split(rb"\r\n|\r|\n", raw)
    if lines and lines[-1] == b"" and raw.endswith((b"\r", b"\n")):
        lines.pop()
    return lines


def _nonempty(line: str) -> bool:
    return any(character not in " \t" for character in line)


def _is_vtt_timing(line: str) -> bool:
    return _VTT_TIMING_RE.fullmatch(line.strip(" \t")) is not None


def _valid_name_token(token: str) -> bool:
    segments: list[str] = []
    current: list[str] = []
    for character in token:
        if character in _JOINERS:
            if not current:
                return False
            segments.append("".join(current))
            current = []
        else:
            current.append(character)
    if not current:
        return False
    segments.append("".join(current))
    letters = "".join(segments)
    if len(letters) < 2 or not all(character.isalpha() for character in letters):
        return False
    cased = [character for character in letters if character.lower() != character.upper()]
    return bool(cased) and all(character == character.upper() for character in cased)


def _is_speaker_label(label: str) -> bool:
    if not label or len(label.encode("utf-8")) > 48:
        return False
    folded = label.casefold()
    if folded in _EXPLICIT_ROLES:
        return True
    for stem in ("speaker", "participant"):
        if folded == stem:
            return True
        prefix = stem + " "
        if folded.startswith(prefix):
            suffix = folded[len(prefix) :]
            if 1 <= len(suffix) <= 3 and suffix.isascii() and suffix.isdigit():
                return True
    tokens = label.split(" ")
    return 2 <= len(tokens) <= 4 and all(_valid_name_token(token) for token in tokens)


def _speaker_payload(line: str) -> str | None:
    colon = line.find(":")
    if colon < 0:
        return None
    if colon + 1 < len(line) and line[colon + 1] not in " \t":
        return None
    label = line[:colon].strip(" \t")
    if not _is_speaker_label(label):
        return None
    return line[colon + 1 :].lstrip(" \t")


def _fraction(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def analyze_document(text: str) -> dict[str, Any]:
    """Analyze already verified strict-UTF-8 text under Spec 72."""
    if "\x00" in text or text.startswith("\ufeff"):
        raise ControlledFailure("invalid document text")
    lines = _physical_lines(text)
    if len(lines) > MAX_LINES_PER_DOCUMENT:
        raise ControlledFailure("line ceiling")

    nonempty = [_nonempty(line) for line in lines]
    first_nonempty = next((i for i, value in enumerate(nonempty) if value), None)
    headers = [
        index == first_nonempty and line.strip(" \t") == "WEBVTT"
        for index, line in enumerate(lines)
    ]
    timings = [_is_vtt_timing(line) for line in lines]
    cue_ids = [False] * len(lines)
    for index, is_timing in enumerate(timings):
        candidate = index - 1
        boundary = index - 2
        if (
            is_timing
            and candidate >= 0
            and nonempty[candidate]
            and not timings[candidate]
            and not headers[candidate]
            and (boundary < 0 or not nonempty[boundary] or headers[boundary])
        ):
            cue_ids[candidate] = True

    payloads = [False] * len(lines)
    in_payload = False
    for index in range(len(lines)):
        if timings[index]:
            in_payload = True
            continue
        if headers[index] or not nonempty[index]:
            in_payload = False
            continue
        if in_payload and not cue_ids[index]:
            payloads[index] = True

    nonempty_lines = sum(nonempty)
    vtt_structural_hits = sum(headers) + sum(timings)
    speaker_label_lines = 0
    disfluency_count = 0
    short_lines = 0
    total_words = 0
    authored_words = 0
    transcript_words = 0
    speaker_active = False

    for index, line in enumerate(lines):
        structural = headers[index] or timings[index] or cue_ids[index]
        speaker = None if structural else _speaker_payload(line)
        if not nonempty[index] or structural:
            speaker_active = False
        if speaker is not None:
            speaker_label_lines += 1
            speaker_active = True
            content = speaker
            is_transcript = True
        elif structural:
            content = ""
            is_transcript = False
        elif payloads[index]:
            content = line
            is_transcript = True
        elif speaker_active:
            content = line
            is_transcript = True
        else:
            content = line
            is_transcript = False

        if payloads[index]:
            content = _VTT_TAG_RE.sub("", content)
        count = 0
        line_disfluencies = 0
        for match in _WORD_RE.finditer(content):
            count += 1
            if count > MAX_WORDS_PER_DOCUMENT - total_words:
                raise ControlledFailure("word ceiling")
            if match.group(0).casefold() in _DISFLUENCIES:
                line_disfluencies += 1
        total_words += count
        if is_transcript:
            transcript_words += count
        else:
            authored_words += count
        disfluency_count += line_disfluencies
        if nonempty[index] and 1 <= count <= _THRESHOLDS["short_line_max_words"]:
            short_lines += 1

    if total_words != authored_words + transcript_words:
        raise RuntimeError("word partition invariant")

    hits = {
        "vtt_any": vtt_structural_hits > 0,
        "speaker_labels": (
            nonempty_lines > 0
            and speaker_label_lines * 100
            > nonempty_lines * _THRESHOLDS["speaker_label_percent_strictly_greater_than"]
        ),
        "disfluencies": (
            total_words > 0
            and disfluency_count * 1000
            > total_words * _THRESHOLDS["disfluencies_per_1000_strictly_greater_than"]
        ),
        "short_lines": (
            nonempty_lines > _THRESHOLDS["short_line_min_nonempty_lines_exclusive"]
            and short_lines * 100
            > nonempty_lines * _THRESHOLDS["short_line_percent_strictly_greater_than"]
        ),
    }
    return {
        "nonempty_lines": nonempty_lines,
        "total_analyzable_words": total_words,
        "authored_residual_words": authored_words,
        "transcript_words": transcript_words,
        "authored_residual_fraction": _fraction(authored_words, total_words),
        "transcript_fraction": _fraction(transcript_words, total_words),
        "vtt_structural_hits": vtt_structural_hits,
        "speaker_label_lines": speaker_label_lines,
        "disfluency_count": disfluency_count,
        "short_lines": short_lines,
        "screen_hits": hits,
    }


def _reject_constant(_value: str) -> None:
    raise ControlledFailure("nonfinite JSON")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ControlledFailure("nonfinite JSON")
    return number


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlledFailure("duplicate JSON key")
        result[key] = value
    return result


def _decode_utf8(raw: bytes, *, allow_bom: bool = False) -> str:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ControlledFailure("invalid UTF-8") from exc
    if (not allow_bom and text.startswith("\ufeff")) or "\x00" in text:
        raise ControlledFailure("forbidden text marker")
    return text


def _valid_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ControlledFailure("invalid id")
    for character in value:
        number = ord(character)
        if number <= 31 or 127 <= number <= 159 or 0xD800 <= number <= 0xDFFF:
            raise ControlledFailure("invalid id")
        if number in {0x2028, 0x2029}:
            raise ControlledFailure("invalid id")
    if len(value.encode("utf-8")) > 256:
        raise ControlledFailure("invalid id")
    return value


def _valid_descriptor_path(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ControlledFailure("invalid path")
    if value.startswith("/"):
        raise ControlledFailure("absolute path")
    parts = value.split("/")
    if any(not part or part in {".", ".."} or ":" in part for part in parts):
        raise ControlledFailure("invalid path component")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ControlledFailure("invalid path component")
    return tuple(parts)


def parse_manifest(raw: bytes) -> list[dict[str, Any]]:
    if any(len(line) > MAX_LINE_BYTES for line in _physical_byte_lines(raw)):
        raise ControlledFailure("line byte ceiling")
    text = _decode_utf8(raw)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_line in _physical_lines(text):
        line = raw_line.strip(" \t")
        if not line or line.startswith("#"):
            continue
        try:
            value = json.loads(
                line,
                object_pairs_hook=_object_pairs,
                parse_constant=_reject_constant,
                parse_float=_finite_float,
            )
        except ControlledFailure:
            raise
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
            raise ControlledFailure("invalid manifest JSON") from exc
        if not isinstance(value, dict):
            raise ControlledFailure("manifest row is not object")
        identifier = _valid_id(value.get("id"))
        if identifier in seen:
            raise ControlledFailure("duplicate id")
        seen.add(identifier)
        rows.append({"id": identifier, "parts": _valid_descriptor_path(value.get("path"))})
        if len(rows) > MAX_DOCUMENTS:
            raise ControlledFailure("document ceiling")
    if not rows:
        raise ControlledFailure("empty manifest")
    return rows


def _posix_require_capabilities() -> None:
    if os.name != "posix":
        return
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ControlledFailure("missing POSIX descriptor capability")
    required = (os.open, os.stat)
    if any(function not in os.supports_dir_fd for function in required):
        raise ControlledFailure("missing POSIX dir-fd capability")
    follow_required = (os.stat,)
    if any(function not in os.supports_follow_symlinks for function in follow_required):
        raise ControlledFailure("missing POSIX no-follow capability")
    _posix_exclusive_rename_function()


def _posix_exclusive_rename_function() -> tuple[Any, Any, int]:
    """Return a native sibling rename that atomically refuses replacement."""

    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            function = getattr(libc, "renameatx_np", None)
            flag = 0x00000004  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            function = getattr(libc, "renameat2", None)
            flag = 1  # RENAME_NOREPLACE
        else:
            function = None
            flag = 0
        if function is None:
            raise ControlledFailure("missing POSIX exclusive-rename capability")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        return ctypes, function, flag
    except ControlledFailure:
        raise
    except (AttributeError, MemoryError, OSError, TypeError, ValueError) as exc:
        raise ControlledFailure("missing POSIX exclusive-rename capability") from exc


def _posix_rename_exclusive_at(
    parent_fd: int, source: str, destination: str
) -> None:
    """Atomically consume one sibling name without replacing a winner."""

    ctypes, function, flag = _posix_exclusive_rename_function()
    try:
        result = function(
            parent_fd,
            os.fsencode(source),
            parent_fd,
            os.fsencode(destination),
            flag,
        )
    except (MemoryError, OSError, TypeError, ValueError) as exc:
        raise ControlledFailure("output publication failed") from exc
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _posix_directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_DIRECTORY", 0)
    )


def _posix_file_flags(*, writable: bool = False, create: bool = False) -> int:
    flags = os.O_RDWR if writable else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if not writable:
        flags |= getattr(os, "O_NONBLOCK", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    return flags


def _posix_pin_directory(path: Path) -> int:
    _posix_require_capabilities()
    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if not parts or parts[0] != os.sep:
        raise ControlledFailure("invalid absolute directory")
    current = os.open(os.sep, _posix_directory_flags())
    try:
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise ControlledFailure("root is not directory")
        for component in parts[1:]:
            if not component or component in {".", ".."}:
                raise ControlledFailure("invalid directory component")
            following = os.open(component, _posix_directory_flags(), dir_fd=current)
            try:
                if not stat.S_ISDIR(os.fstat(following).st_mode):
                    raise ControlledFailure("component is not directory")
            except BaseException:
                os.close(following)
                raise
            previous = current
            current = following
            os.close(previous)
        return current
    except BaseException:
        os.close(current)
        raise


def _posix_relative_parent(root_fd: int, parts: tuple[str, ...]) -> tuple[int, str]:
    if not parts:
        raise ControlledFailure("empty descriptor path")
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            following = os.open(component, _posix_directory_flags(), dir_fd=current)
            try:
                if not stat.S_ISDIR(os.fstat(following).st_mode):
                    raise ControlledFailure("component is not directory")
            except BaseException:
                os.close(following)
                raise
            previous = current
            current = following
            os.close(previous)
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def _posix_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _posix_full_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
    )


def _posix_read_relative(
    root_fd: int,
    parts: tuple[str, ...],
    *,
    limit: int,
    allow_multiple_links: bool,
) -> tuple[bytes, tuple[int, int]]:
    try:
        parent_fd, name = _posix_relative_parent(root_fd, parts)
    except (OSError, MemoryError) as exc:
        raise ControlledFailure("source traversal failed") from exc
    descriptor = -1
    try:
        descriptor = os.open(name, _posix_file_flags(), dir_fd=parent_fd)
        _fault("source_opened")
        before = os.fstat(descriptor)
        named_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named_before.st_mode):
            raise ControlledFailure("source is not regular")
        if _posix_identity(before) != _posix_identity(named_before):
            raise ControlledFailure("source identity mismatch")
        if not allow_multiple_links and before.st_nlink != 1:
            raise ControlledFailure("control has multiple links")
        if before.st_size < 0 or before.st_size > limit:
            raise ControlledFailure("source byte ceiling")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ControlledFailure("short source read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) != b"":
            raise ControlledFailure("source grew during read")
        _fault("source_read")
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _posix_full_identity(before) != _posix_full_identity(after)
            or _posix_full_identity(after) != _posix_full_identity(named_after)
        ):
            raise ControlledFailure("source changed during read")
        return b"".join(chunks), _posix_identity(before)
    except (OSError, MemoryError) as exc:
        raise ControlledFailure("source read failed") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failed = False
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except (OSError, MemoryError):
                close_failed = True
        try:
            os.close(parent_fd)
        except (OSError, MemoryError):
            close_failed = True
        if close_failed and not active_exception:
            raise ControlledFailure("source close failed")


def _windows_module() -> Any:
    try:
        import windows_descriptor_io as windows_io
    except (ImportError, OSError) as exc:
        raise ControlledFailure("Windows descriptor backend unavailable") from exc
    return windows_io


def _windows_pin_directory(path: Path) -> tuple[Any, int]:
    absolute = Path(os.path.abspath(path))
    anchor = absolute.anchor.replace("/", "\\").casefold()
    if anchor.startswith("\\\\?\\unc\\") or (
        anchor.startswith("\\\\") and not anchor.startswith("\\\\?\\")
    ):
        raise ControlledFailure("network input root is not supported")
    windows_io = _windows_module()
    parent = 0
    directory = 0
    try:
        parent, directory, _name = windows_io.pin_directory(
            absolute, writable_final=False
        )
        windows_io.close(parent)
        parent = 0
        return windows_io, directory
    except (OSError, MemoryError, ValueError) as exc:
        if parent:
            try:
                windows_io.close(parent)
            except (OSError, MemoryError):
                pass
        if directory:
            try:
                windows_io.close(directory)
            except (OSError, MemoryError):
                pass
        raise ControlledFailure("directory pin failed") from exc


def _windows_relative_parent(
    windows_io: Any, root: int, parts: tuple[str, ...]
) -> tuple[int, str]:
    if not parts:
        raise ControlledFailure("empty descriptor path")
    current = windows_io.duplicate(root)
    try:
        for component in parts[:-1]:
            following = windows_io.open_directory(current, component)
            previous = current
            current = following
            windows_io.close(previous)
        return current, parts[-1]
    except BaseException:
        try:
            windows_io.close(current)
        except (OSError, MemoryError):
            pass
        raise


def _windows_read_relative(
    windows_io: Any,
    root: int,
    parts: tuple[str, ...],
    *,
    limit: int,
    allow_multiple_links: bool,
) -> tuple[bytes, tuple[int, int]]:
    parent = 0
    handle = 0
    rebound = 0
    try:
        parent, name = _windows_relative_parent(windows_io, root, parts)
        handle = windows_io.open_file(
            parent,
            name,
            share_write=False,
            share_delete=False,
            allow_multiple_links=allow_multiple_links,
        )
        _fault("source_opened")
        before = windows_io.require_direct(
            handle, "file", allow_multiple_links=allow_multiple_links
        )
        if before.size < 0 or before.size > limit:
            raise ControlledFailure("source byte ceiling")
        chunks: list[bytes] = []
        remaining = before.size
        while remaining:
            chunk = windows_io.read(handle, min(1024 * 1024, remaining))
            if not chunk:
                raise ControlledFailure("short source read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if windows_io.read(handle, 1) != b"":
            raise ControlledFailure("source grew during read")
        _fault("source_read")
        after = windows_io.require_direct(
            handle, "file", allow_multiple_links=allow_multiple_links
        )
        rebound = windows_io.open_file(
            parent,
            name,
            share_write=False,
            share_delete=False,
            allow_multiple_links=allow_multiple_links,
        )
        named = windows_io.require_direct(
            rebound, "file", allow_multiple_links=allow_multiple_links
        )
        if before.identity != after.identity or after.identity != named.identity:
            raise ControlledFailure("source changed during read")
        return b"".join(chunks), (before.volume_serial, before.file_id)
    except ControlledFailure:
        raise
    except (OSError, MemoryError, ValueError) as exc:
        raise ControlledFailure("source read failed") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failed = False
        for candidate in (rebound, handle, parent):
            if candidate:
                try:
                    windows_io.close(candidate)
                except (OSError, MemoryError):
                    close_failed = True
        if close_failed and not active_exception:
            raise ControlledFailure("source close failed")


def _posix_stat_owned(parent_fd: int, name: str, identity: tuple[int, int]) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode) and _posix_identity(current) == identity


def _valid_output_name(destination: Path) -> str:
    name = destination.name
    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise ControlledFailure("invalid output name")
    if os.name == "nt" and ("\\" in name or ":" in name):
        raise ControlledFailure("invalid output name")
    return name


def _temporary_output_name(_destination: Path) -> str:
    try:
        return f".setec-nonprose-{uuid.uuid4().hex}.tmp"
    except (OSError, MemoryError) as exc:
        raise ControlledFailure("temporary name allocation failed") from exc


def _preflight_output_absent(destination: Path) -> None:
    name = _valid_output_name(destination)
    if os.name == "posix":
        try:
            parent = _posix_pin_directory(destination.parent)
        except (OSError, MemoryError) as exc:
            raise ControlledFailure("output directory pin failed") from exc
        active_exception = False
        try:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            except (OSError, MemoryError) as exc:
                raise ControlledFailure("output preflight failed") from exc
            raise ControlledFailure("output exists")
        except BaseException:
            active_exception = True
            raise
        finally:
            try:
                os.close(parent)
            except (OSError, MemoryError) as exc:
                if not active_exception:
                    raise ControlledFailure("output preflight close failed") from exc
    elif os.name == "nt":
        windows_io, parent = _windows_pin_output(destination.parent)
        handle = 0
        active_exception = False
        try:
            try:
                handle = windows_io.open_node(parent, name)
            except (OSError, MemoryError, ValueError) as exc:
                if getattr(exc, "winerror", None) in {2, 3}:
                    return
                raise ControlledFailure("output preflight failed") from exc
            raise ControlledFailure("output exists")
        except BaseException:
            active_exception = True
            raise
        finally:
            close_failed = False
            for candidate in (handle, parent):
                if candidate:
                    try:
                        windows_io.close(candidate)
                    except (OSError, MemoryError):
                        close_failed = True
            if close_failed and not active_exception:
                raise ControlledFailure("output preflight close failed")
    else:
        raise ControlledFailure("unsupported platform")


def _read_fd_exact(descriptor: int, expected: bytes) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = len(expected)
    while remaining:
        chunk = os.read(descriptor, min(1024 * 1024, remaining))
        if not chunk:
            raise ControlledFailure("short payload verification")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1) != b"" or b"".join(chunks) != expected:
        raise ControlledFailure("payload verification failed")


def _posix_publish_create_new(destination: Path, payload: bytes) -> None:
    _valid_output_name(destination)
    temporary = _temporary_output_name(destination)
    try:
        parent_fd = _posix_pin_directory(destination.parent)
    except (OSError, MemoryError) as exc:
        raise ControlledFailure("output directory pin failed") from exc
    descriptor = -1
    verifier = -1
    owned_identity: tuple[int, int] | None = None
    try:
        try:
            os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ControlledFailure("output exists")
        descriptor = os.open(
            temporary,
            _posix_file_flags(writable=True, create=True),
            0o600,
            dir_fd=parent_fd,
        )
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
            raise ControlledFailure("invalid output temporary")
        owned_identity = _posix_identity(initial)
        _fault("temp_created")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ControlledFailure("short output write")
            offset += written
        _fault("write")
        os.fsync(descriptor)
        _fault("flush")
        _read_fd_exact(descriptor, payload)
        stable = os.fstat(descriptor)
        if not stat.S_ISREG(stable.st_mode) or stable.st_size != len(payload):
            raise ControlledFailure("invalid output payload")
        if _posix_identity(stable) != owned_identity:
            raise ControlledFailure("temporary identity changed")
        named = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if _posix_identity(named) != owned_identity:
            raise ControlledFailure("temporary identity changed")
        _fault("payload_verified")
        _fault("publish_before")
        _posix_rename_exclusive_at(parent_fd, temporary, destination.name)
        _fault("publish_after_effect")
        verifier = os.open(destination.name, _posix_file_flags(), dir_fd=parent_fd)
        final_info = os.fstat(verifier)
        if _posix_identity(final_info) != owned_identity:
            raise ControlledFailure("published identity changed")
        _read_fd_exact(verifier, payload)
        _fault("final_verified")
        if not _posix_stat_owned(parent_fd, destination.name, owned_identity):
            raise ControlledFailure("published name changed")
        os.fsync(parent_fd)
        if not _posix_stat_owned(parent_fd, destination.name, owned_identity):
            raise ControlledFailure("published name changed")
    except ControlledFailure:
        raise
    except (OSError, MemoryError) as exc:
        raise ControlledFailure("output publication failed") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failed = False
        for candidate in (verifier, descriptor):
            if candidate >= 0:
                try:
                    os.close(candidate)
                except (OSError, MemoryError):
                    close_failed = True
        try:
            os.close(parent_fd)
        except (OSError, MemoryError):
            close_failed = True
        if close_failed and not active_exception:
            raise ControlledFailure("output close failed")


def _windows_pin_output(path: Path) -> tuple[Any, int]:
    windows_io = _windows_module()
    parent = 0
    directory = 0
    try:
        parent, directory, _name = windows_io.pin_directory(
            Path(os.path.abspath(path)), writable_final=True
        )
        windows_io.close(parent)
        parent = 0
        return windows_io, directory
    except (OSError, MemoryError, ValueError) as exc:
        if parent:
            try:
                windows_io.close(parent)
            except (OSError, MemoryError):
                pass
        if directory:
            try:
                windows_io.close(directory)
            except (OSError, MemoryError):
                pass
        raise ControlledFailure("output directory pin failed") from exc


def _windows_read_handle_exact(windows_io: Any, handle: int, payload: bytes) -> None:
    windows_io.seek(handle, 0)
    chunks: list[bytes] = []
    remaining = len(payload)
    while remaining:
        chunk = windows_io.read(handle, min(1024 * 1024, remaining))
        if not chunk:
            raise ControlledFailure("short payload verification")
        chunks.append(chunk)
        remaining -= len(chunk)
    if windows_io.read(handle, 1) != b"" or b"".join(chunks) != payload:
        raise ControlledFailure("payload verification failed")


def _windows_publish_create_new(destination: Path, payload: bytes) -> None:
    _valid_output_name(destination)
    temporary = _temporary_output_name(destination)
    windows_io, parent = _windows_pin_output(destination.parent)
    writer = 0
    rebound = 0
    final_rebound = 0
    owned = None
    try:
        writer = windows_io.create_file(
            parent,
            temporary,
            share_write=False,
            share_delete=False,
        )
        owned = windows_io.require_direct(writer, "file")
        _fault("temp_created")
        offset = 0
        while offset < len(payload):
            written = windows_io.write(writer, payload[offset:])
            if written <= 0:
                raise ControlledFailure("short output write")
            offset += written
        _fault("write")
        windows_io.flush(writer)
        _fault("flush")
        _windows_read_handle_exact(windows_io, writer, payload)
        stable = windows_io.require_direct(writer, "file")
        if stable.identity[:2] != owned.identity[:2] or stable.size != len(payload):
            raise ControlledFailure("invalid output payload")
        owned = stable
        rebound = windows_io.open_file(
            parent, temporary, share_write=True, share_delete=True
        )
        if windows_io.require_direct(rebound, "file").identity != owned.identity:
            raise ControlledFailure("temporary identity changed")
        windows_io.close(rebound)
        rebound = 0
        _fault("payload_verified")
        _fault("publish_before")
        windows_io.rename(writer, parent, destination.name, replace=False)
        _fault("publish_after_effect")
        final_rebound = windows_io.open_file(
            parent, destination.name, share_write=True, share_delete=True
        )
        if windows_io.require_direct(final_rebound, "file").identity != windows_io.require_direct(
            writer, "file"
        ).identity:
            raise ControlledFailure("published identity changed")
        _windows_read_handle_exact(windows_io, final_rebound, payload)
        _fault("final_verified")
    except ControlledFailure:
        if writer:
            try:
                windows_io.delete(writer)
            except BaseException:
                pass
        raise
    except (OSError, MemoryError, ValueError) as exc:
        if writer:
            try:
                windows_io.delete(writer)
            except BaseException:
                pass
        raise ControlledFailure("output publication failed") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failed = False
        for candidate in (final_rebound, rebound):
            if candidate:
                try:
                    windows_io.close(candidate)
                except (OSError, MemoryError):
                    close_failed = True
        if close_failed and writer:
            try:
                windows_io.delete(writer)
            except BaseException:
                pass
        if parent:
            try:
                windows_io.close(parent)
            except (OSError, MemoryError):
                close_failed = True
                if writer:
                    try:
                        windows_io.delete(writer)
                    except BaseException:
                        pass
        if writer:
            try:
                windows_io.close(writer)
            except (OSError, MemoryError):
                close_failed = True
                try:
                    windows_io.delete(writer)
                except BaseException:
                    pass
        if close_failed and not active_exception:
            raise ControlledFailure("output close failed")


def _publish_create_new(destination: Path, payload: bytes) -> None:
    if os.name == "nt":
        _windows_publish_create_new(destination, payload)
    elif os.name == "posix":
        _posix_publish_create_new(destination, payload)
    else:
        raise ControlledFailure("unsupported platform")


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_RESULT_KEYS:
                return True
            if _has_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _totals(documents: list[dict[str, Any]]) -> dict[str, Any]:
    integer_keys = (
        "nonempty_lines",
        "total_analyzable_words",
        "authored_residual_words",
        "transcript_words",
        "vtt_structural_hits",
        "speaker_label_lines",
        "disfluency_count",
        "short_lines",
    )
    totals: dict[str, Any] = {
        "documents": len(documents),
        "documents_with_any_screen": sum(any(row["screen_hits"].values()) for row in documents),
        **{key: sum(int(row[key]) for row in documents) for key in integer_keys},
        "screen_counts": {
            name: sum(bool(row["screen_hits"][name]) for row in documents)
            for name in ("vtt_any", "speaker_labels", "disfluencies", "short_lines")
        },
    }
    if totals["total_analyzable_words"] != (
        totals["authored_residual_words"] + totals["transcript_words"]
    ):
        raise RuntimeError("aggregate partition invariant")
    return totals


def _claim_license(totals: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A bounded structural corpus-hygiene screen reporting VTT structure, "
            "speaker-label density, disfluency density, short-line density, and a "
            "deterministic authored-residual/transcript word partition."
        ),
        does_not_license=(
            "Corpus disposition, authorship, provenance, quality, genre, fiction or "
            "nonfiction classification, AI/human inference, or training eligibility."
        ),
        comparison_set={
            "documents": totals["documents"],
            "documents_with_any_screen": totals["documents_with_any_screen"],
            "method": METHOD_VERSION,
        },
        additional_caveats=[
            "Thresholds are operationally uncalibrated and queue documents only for review.",
            "authored_residual_words is a structural residual, not an authorship inference.",
        ],
        references=["Spec 72"],
    )


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _ClosedParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-out", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> bytes:
    manifest_path = Path(args.manifest)
    report_path = Path(args.report_out)
    _preflight_output_absent(report_path)
    root: Any = None
    windows_io: Any = None
    try:
        if os.name == "nt":
            windows_io, root = _windows_pin_directory(manifest_path.parent)
            manifest_raw, manifest_identity = _windows_read_relative(
                windows_io,
                root,
                (manifest_path.name,),
                limit=MAX_MANIFEST_BYTES,
                allow_multiple_links=False,
            )
        elif os.name == "posix":
            try:
                root = _posix_pin_directory(manifest_path.parent)
            except (OSError, MemoryError) as exc:
                raise ControlledFailure("manifest directory pin failed") from exc
            manifest_raw, manifest_identity = _posix_read_relative(
                root,
                (manifest_path.name,),
                limit=MAX_MANIFEST_BYTES,
                allow_multiple_links=False,
            )
        else:
            raise ControlledFailure("unsupported platform")
        _fault("manifest_opened")
        descriptors = parse_manifest(manifest_raw)
        source_identities: set[tuple[int, int]] = set()
        source_seal_rows: list[dict[str, str]] = []
        documents: list[dict[str, Any]] = []
        cumulative = 0
        for descriptor in descriptors:
            remaining = MAX_TOTAL_DOCUMENT_BYTES - cumulative
            if remaining < 0:
                raise ControlledFailure("cumulative byte ceiling")
            limit = min(MAX_DOCUMENT_BYTES, remaining)
            if os.name == "nt":
                raw, identity = _windows_read_relative(
                    windows_io,
                    root,
                    descriptor["parts"],
                    limit=limit,
                    allow_multiple_links=True,
                )
            else:
                raw, identity = _posix_read_relative(
                    root,
                    descriptor["parts"],
                    limit=limit,
                    allow_multiple_links=True,
                )
            if identity == manifest_identity or identity in source_identities:
                raise ControlledFailure("duplicate source identity")
            source_identities.add(identity)
            cumulative += len(raw)
            raw_lines = _physical_byte_lines(raw)
            if len(raw_lines) > MAX_LINES_PER_DOCUMENT:
                raise ControlledFailure("line ceiling")
            if any(len(line) > MAX_LINE_BYTES for line in raw_lines):
                raise ControlledFailure("line byte ceiling")
            text = _decode_utf8(raw)
            metrics = analyze_document(text)
            documents.append({"id": descriptor["id"], **metrics})
            source_seal_rows.append(
                {"id": descriptor["id"], "content_sha256": hashlib.sha256(raw).hexdigest()}
            )
    finally:
        if root is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                if os.name == "nt" and windows_io is not None:
                    windows_io.close(root)
                else:
                    os.close(root)
            except (OSError, MemoryError) as exc:
                if not active_exception:
                    raise ControlledFailure("input root close failed") from exc

    documents.sort(key=lambda row: row["id"].encode("utf-8"))
    source_seal_rows.sort(key=lambda row: row["id"].encode("utf-8"))
    source_preimage = b"".join(_canonical_bytes(row) for row in source_seal_rows)
    totals = _totals(documents)
    report = {
        "schema": REPORT_SCHEMA,
        "method": METHOD_VERSION,
        "calibration_status": CALIBRATION_STATUS,
        "manifest_sha256": _sha256_tag(manifest_raw),
        "source_set_sha256": _sha256_tag(source_preimage),
        "thresholds": dict(_THRESHOLDS),
        "totals": totals,
        "documents": documents,
    }
    if _has_forbidden_key(report):
        raise RuntimeError("forbidden report key")
    report_bytes = _bounded_canonical_bytes(report, MAX_REPORT_BYTES)
    report_sha = _sha256_tag(report_bytes)
    results = {
        "method": METHOD_VERSION,
        "calibration_status": CALIBRATION_STATUS,
        "thresholds": dict(_THRESHOLDS),
        "totals": totals,
        "manifest_sha256": _sha256_tag(manifest_raw),
        "source_set_sha256": _sha256_tag(source_preimage),
        "report_sha256": report_sha,
    }
    if _has_forbidden_key(results):
        raise RuntimeError("forbidden result key")
    envelope = build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=None,
        target_words=totals["total_analyzable_words"],
        baseline=None,
        results=results,
        claim_license=_claim_license(totals),
        warnings=(
            ["Operational structural screen hits require operator review."]
            if totals["documents_with_any_screen"]
            else []
        ),
        ai_status=None,
    )
    stdout_bytes = _canonical_bytes(envelope)
    _publish_create_new(report_path, report_bytes)
    return stdout_bytes


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = _arguments(argv)
        output = _run(args)
    except UsageFailure:
        _emit_terminal(stderr, b"nonprose_sweep: invalid arguments\n")
        return 2
    except ControlledFailure:
        _emit_terminal(
            stderr,
            b"nonprose_sweep: input, resource, or publication validation failed\n",
        )
        return 3
    except BaseException:
        _emit_terminal(stderr, b"nonprose_sweep: internal failure\n")
        return 1
    try:
        _write_bytes(stdout, output)
    except BaseException:
        _emit_terminal(stderr, b"nonprose_sweep: internal failure\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
