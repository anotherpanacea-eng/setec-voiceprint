#!/usr/bin/env python3
"""Apply a fail-closed owner-corrections JSONL sidecar to a manifest.

This deliberately small, stdlib-only pre-registration step changes only the
reviewed ``register`` and ``era`` classifications.  It never discovers a
sidecar implicitly and it never edits the source manifest unless ``--in-place``
is explicitly selected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from manifest_validator import (
    ALLOWED_ERA,
    ALLOWED_REGISTER,
    resolve_path,
    validate_manifest,
)


CORRECTION_SCHEMA = "setec-owner-correction/1"
RESULT_SCHEMA = "setec-owner-corrections-result/1"
_FAILURE = b"apply_owner_corrections: input, policy, or publication validation failed\n"
_MATCH_KEYS = frozenset({"id", "path", "source_id", "content_hash"})
_REWRITE_KEYS = frozenset({"register", "era"})
_TOP_LEVEL_KEYS = frozenset({"schema", "match", "expect", "rewrite", "note"})


class ControlledFailure(ValueError):
    """A refusal whose public CLI rendering must reveal no input details."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ControlledFailure(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControlledFailure("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise ControlledFailure("non-finite JSON number")


def _decode_utf8(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ControlledFailure("UTF-8 BOM is not permitted")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ControlledFailure("input is not strict UTF-8") from exc


def _physical_lines(text: str) -> Iterable[str]:
    """Split only the three byte-level JSONL record endings after UTF-8 decode.

    ``str.splitlines`` treats U+0085/U+2028/U+2029 as record separators.  Those
    are legal JSON string data, so recognize only LF, CRLF, and lone CR here.
    """
    start = 0
    index = 0
    while index < len(text):
        if text[index] == "\n":
            yield text[start:index]
            index += 1
            start = index
        elif text[index] == "\r":
            yield text[start:index]
            index += 2 if index + 1 < len(text) and text[index + 1] == "\n" else 1
            start = index
        else:
            index += 1
    if start < len(text):
        yield text[start:]


def _load_json_object(line: str) -> dict[str, Any]:
    try:
        value = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ControlledFailure):
            raise
        raise ControlledFailure("invalid JSON") from exc
    if not isinstance(value, dict):
        raise ControlledFailure("JSONL record is not an object")
    _reject_nonfinite_values(value)
    return value


def _reject_nonfinite_values(value: Any) -> None:
    """Reject exponent-overflow floats that JSON's parse_constant cannot see."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ControlledFailure("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite_values(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nonfinite_values(item)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_string_map(value: Any, *, keys: frozenset[str], allow_empty: bool) -> dict[str, str]:
    if not isinstance(value, dict) or (not allow_empty and not value):
        raise ControlledFailure("invalid correction object")
    result: dict[str, str] = {}
    for key, item in value.items():
        if key not in keys or not _is_nonempty_string(item):
            raise ControlledFailure("invalid correction field")
        result[key] = item
    return result


def _parse_manifest(raw: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for physical_line in _physical_lines(_decode_utf8(raw)):
        candidate = physical_line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        rows.append(_load_json_object(candidate))

    seen_ids: set[str] = set()
    for row in rows:
        if not _is_nonempty_string(row.get("id")) or not _is_nonempty_string(row.get("path")):
            raise ControlledFailure("manifest identity is malformed")
        row_id = row["id"]
        if row_id in seen_ids:
            raise ControlledFailure("manifest IDs are not unique")
        seen_ids.add(row_id)
    return rows


def _parse_corrections(raw: bytes) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for physical_line in _physical_lines(_decode_utf8(raw)):
        if not physical_line.strip():
            continue
        rule = _load_json_object(physical_line.strip())
        rule_keys = set(rule)
        if rule_keys - _TOP_LEVEL_KEYS:
            raise ControlledFailure("unknown correction key")
        if rule_keys not in (
            {"schema", "match", "rewrite", "note"},
            {"schema", "match", "expect", "rewrite", "note"},
        ):
            raise ControlledFailure("missing correction key")
        if rule["schema"] != CORRECTION_SCHEMA:
            raise ControlledFailure("unknown correction schema")
        match = _require_string_map(rule["match"], keys=_MATCH_KEYS, allow_empty=False)
        expect = _require_string_map(rule.get("expect", {}), keys=_REWRITE_KEYS, allow_empty=True)
        rewrite = _require_string_map(rule["rewrite"], keys=_REWRITE_KEYS, allow_empty=False)
        if not _is_nonempty_string(rule["note"]):
            raise ControlledFailure("invalid correction note")
        for key, value in rewrite.items():
            allowed = ALLOWED_REGISTER if key == "register" else ALLOWED_ERA
            if value not in allowed:
                raise ControlledFailure("unsupported correction value")
        rules.append({"match": match, "expect": expect, "rewrite": rewrite})
    return rules


def _canonical_jsonl(rows: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(
            row,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        for row in rows
    )


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def prepare_corrected_manifest(manifest_bytes: bytes, corrections_bytes: bytes) -> tuple[bytes, dict[str, int]]:
    """Return canonical corrected bytes and aggregate counts without publishing.

    This importable entry point performs all schema, match, conflict, and
    idempotence checks.  Filesystem validation and publication remain CLI
    concerns because they need the source manifest's parent directory.
    """
    rows = _parse_manifest(manifest_bytes)
    rules = _parse_corrections(corrections_bytes)
    matched_rules: dict[int, dict[str, Any]] = {}

    for rule in rules:
        matching_indexes = [
            index for index, row in enumerate(rows)
            if all(row.get(key) == value for key, value in rule["match"].items())
        ]
        if len(matching_indexes) != 1:
            raise ControlledFailure("correction did not select exactly one row")
        index = matching_indexes[0]
        if index in matched_rules:
            raise ControlledFailure("multiple corrections select one row")
        matched_rules[index] = rule

    corrected = [dict(row) for row in rows]
    applied = 0
    already_applied = 0
    for index, rule in matched_rules.items():
        original = rows[index]
        rewrite: dict[str, str] = rule["rewrite"]
        if all(original.get(key) == value for key, value in rewrite.items()):
            already_applied += 1
            continue
        expect: dict[str, str] = rule["expect"]
        if any(key not in original or original.get(key) != value for key, value in expect.items()):
            raise ControlledFailure("stale correction")
        corrected[index].update(rewrite)
        applied += 1

    return _canonical_jsonl(corrected), {
        "manifest_rows": len(rows),
        "corrections": len(rules),
        "applied": applied,
        "already_applied": already_applied,
    }


def _same_path_identity(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except (OSError, ValueError):
        pass
    return os.path.normcase(os.path.abspath(os.path.realpath(left))) == os.path.normcase(
        os.path.abspath(os.path.realpath(right))
    )


def _same_parent_identity(manifest: Path, output: Path) -> bool:
    try:
        if os.path.samefile(manifest.parent, output.parent):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(os.path.realpath(manifest.parent))) == os.path.normcase(
        os.path.abspath(os.path.realpath(output.parent))
    )


def _refuse_manifest_data_alias(
    manifest: Path, manifest_bytes: bytes, destination: Path,
) -> None:
    """Keep publication away from every source file named by the manifest."""
    for row in _parse_manifest(manifest_bytes):
        try:
            source_path = resolve_path(manifest, row["path"])
            aliases_destination = _same_path_identity(destination, source_path)
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise ControlledFailure("manifest source path is invalid") from exc
        if aliases_destination:
            raise ControlledFailure("output aliases manifest source data")


def _validate_candidate(manifest_parent: Path, corrected: bytes) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(
        dir=manifest_parent,
        prefix=".apply_owner_corrections.",
        suffix=".jsonl",
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(corrected)
            handle.flush()
            os.fsync(handle.fileno())
        result = validate_manifest(temporary, progress_every=0)
        if result["n_errors"]:
            raise ControlledFailure("corrected manifest failed validation")
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise ControlledFailure("candidate validation failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _publish(destination: Path, content: bytes, *, replace: bool) -> None:
    descriptor, raw_temporary = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, destination)
        else:
            # link(2) is create-new: an intervening destination wins and is
            # never overwritten.  This works on Windows without POSIX modes.
            os.link(temporary, destination)
            temporary.unlink()
    except (OSError, ValueError) as exc:
        raise ControlledFailure("destination publication failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_bytes(stream: Any, data: bytes) -> None:
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(data)
        binary.flush()
    else:
        stream.write(data.decode("utf-8"))
        flush = getattr(stream, "flush", None)
        if flush is not None:
            flush()


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(description="Apply a deterministic owner-corrections JSONL sidecar.")
    parser.add_argument("manifest")
    parser.add_argument("corrections")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--out")
    target.add_argument("--in-place", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.in_place and args.replace:
        raise ControlledFailure("--replace requires --out")
    return args


def _run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = Path(args.manifest)
    corrections = Path(args.corrections)
    if not manifest.parent.is_dir():
        raise ControlledFailure("manifest parent is invalid")
    if _same_path_identity(manifest, corrections):
        raise ControlledFailure("manifest and corrections must differ")

    destination = manifest if args.in_place else Path(args.out)
    if not args.in_place:
        if not destination.parent.is_dir() or not _same_parent_identity(manifest, destination):
            raise ControlledFailure("output parent differs from manifest parent")
        if _same_path_identity(destination, manifest) or _same_path_identity(destination, corrections):
            raise ControlledFailure("output aliases an input")
        if destination.exists() and not args.replace:
            raise ControlledFailure("output already exists")
    elif _same_path_identity(destination, corrections):
        raise ControlledFailure("in-place target aliases corrections")

    try:
        manifest_bytes = manifest.read_bytes()
        corrections_bytes = corrections.read_bytes()
    except OSError as exc:
        raise ControlledFailure("input could not be read") from exc
    _refuse_manifest_data_alias(manifest, manifest_bytes, destination)
    corrected, counts = prepare_corrected_manifest(manifest_bytes, corrections_bytes)
    _validate_candidate(manifest.parent, corrected)

    if not args.dry_run:
        _publish(destination, corrected, replace=bool(args.replace or args.in_place))

    return {
        "schema": RESULT_SCHEMA,
        **counts,
        "input_manifest_sha256": _sha256(manifest_bytes),
        "corrections_sha256": _sha256(corrections_bytes),
        "output_sha256": _sha256(corrected),
        "dry_run": bool(args.dry_run),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    """Run the CLI and return its documented process exit code."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        args = _arguments(argv)
        result = _run(args)
        _write_bytes(
            stdout,
            json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(",", ":")).encode("utf-8") + b"\n",
        )
        return 0
    except ControlledFailure:
        _write_bytes(stderr, _FAILURE)
        return 2
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
