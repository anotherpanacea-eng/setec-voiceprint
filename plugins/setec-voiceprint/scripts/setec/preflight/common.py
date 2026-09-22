"""Bounded input and private-output primitives for packet preflight."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

import atomic_publish
import shingle_dedup_io

if os.name == "nt":
    import windows_descriptor_io as winio


MANIFEST_LIMIT = 8 * 1024 * 1024
POLICY_LIMIT = 64 * 1024
SPLIT_LIMIT = 8 * 1024 * 1024
CANDIDATE_LIMIT = 4 * 1024 * 1024
COMBINED_CANDIDATE_LIMIT = 128 * 1024 * 1024
OVERLAP_DETAIL_LIMIT = 256 * 1024 * 1024
RECEIPT_LIMIT = 64 * 1024
MAX_RECORDS = 5000
STAGE_STATUSES = frozenset({"passed", "failed", "needs_human_review", "not_run"})
REFUSAL_CODES = frozenset({
    "internal_refusal", "input_changed", "path_confinement", "path_alias",
    "size_limit", "input_contract", "policy_contract", "split_contract",
    "work_limit", "detail_contract", "admission_contract", "holdout_contract",
    "labels_contract", "calibration_binding", "intake_binding",
    "receipt_contract", "receipt_binding", "output_collision",
    "output_unavailable",
})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_STRATUM = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class Refusal(Exception):
    """A public, path-free refusal code."""

    def __init__(self, code: str) -> None:
        if code not in REFUSAL_CODES:
            raise ValueError("unknown refusal code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    sha256: str
    identity: tuple[int, int]


@dataclass(frozen=True)
class Record:
    id: str
    group_id: str
    stratum: str
    path: str
    source_path: str
    source_bytes_sha256: str
    start_byte: int
    end_byte: int
    candidate: Snapshot
    analysis_text: str
    analysis_sha256: str
    content_sha256: str
    self_span: bool


@dataclass(frozen=True)
class Manifest:
    manifest_sha256: str
    root: Path
    path_identities: Mapping[str, tuple[int, int]]
    records: tuple[Record, ...]


def canonical_json(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def domain_hash(domain: str, payload: bytes) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + payload).hexdigest()


def plain_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _nonfinite(value: str) -> None:
    raise ValueError("non-finite number")


def _reject_float(value: str) -> None:
    raise ValueError("floating-point number")


def parse_json(data: bytes, code: str) -> object:
    try:
        return json.loads(data.decode("utf-8", errors="strict"),
                          object_pairs_hook=_no_duplicates,
                          parse_constant=_nonfinite, parse_float=_reject_float)
    except (UnicodeError, ValueError, TypeError):
        raise Refusal(code) from None


def exact_keys(value: object, keys: set[str] | frozenset[str], code: str) -> dict:
    if type(value) is not dict or set(value) != set(keys):
        raise Refusal(code)
    return value


def require_hex(value: object, code: str) -> str:
    if type(value) is not str or _HEX.fullmatch(value) is None:
        raise Refusal(code)
    return value


def _relative_name(value: str) -> tuple[str, ...]:
    if type(value) is not str or not value:
        raise Refusal("path_confinement")
    try:
        if len(value.encode("utf-8")) > 1024:
            raise Refusal("path_confinement")
    except UnicodeError:
        raise Refusal("path_confinement") from None
    if ("\0" in value or "\\" in value or ":" in value or
            value.startswith("/") or PureWindowsPath(value).drive):
        raise Refusal("path_confinement")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Refusal("path_confinement")
    return tuple(parts)


def _bind(root: Path, rel: str) -> tuple[Path, tuple[int, ...]]:
    parts = _relative_name(rel)
    target = root.joinpath(*parts)
    try:
        bound, index, fingerprint = shingle_dedup_io.bind_regular([target])
        if index != 0:
            raise Refusal("path_confinement")
        return bound, fingerprint
    except shingle_dedup_io.SecureIOError:
        raise Refusal("path_confinement") from None


def _read_bound(target: Path, root: Path, limit: int,
                fingerprint: tuple[int, ...]) -> Snapshot:
    if fingerprint[2] > limit:
        raise Refusal("size_limit")
    try:
        data = shingle_dedup_io.read_bounded_regular(
            target, limit, root=root, expected_fingerprint=fingerprint)
    except shingle_dedup_io.SecureIOError:
        raise Refusal("input_changed") from None
    return Snapshot(data, plain_hash(data), (fingerprint[0], fingerprint[1]))


def read_bounded(root: Path, rel: str, limit: int) -> Snapshot:
    """Read an exact regular-file snapshot beneath an anchored directory."""
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid bound")
    root = Path(os.path.abspath(root))
    target, fingerprint = _bind(root, rel)
    return _read_bound(target, root, limit, fingerprint)


def load_strict_json(path: Path, limit: int, schema: str,
                     keys: frozenset[str], code: str) -> dict:
    path = Path(os.path.abspath(path))
    data = read_bounded(path.parent, path.name, limit).data
    value = exact_keys(parse_json(data, code), keys, code)
    if value.get("schema") != schema:
        raise Refusal(code)
    return value


def _label(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise Refusal("input_contract")
    if any(ord(ch) < 32 or 127 <= ord(ch) <= 159 or
           ord(ch) in {0x2028, 0x2029} or 0xD800 <= ord(ch) <= 0xDFFF
           for ch in value):
        raise Refusal("input_contract")
    return value


def text_rule_violation(data: bytes) -> str | None:
    if data.startswith(b"\xef\xbb\xbf"):
        return "bom"
    if b"\0" in data:
        return "nul"
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError:
        return "invalid_utf8"
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        return "c0_control"
    if any(127 <= ord(ch) <= 159 for ch in text):
        return "c1_control"
    if not text.strip():
        return "empty"
    return None


def _analysis(data: bytes) -> tuple[str, str]:
    if text_rule_violation(data) is not None:
        raise Refusal("input_contract")
    text = data.decode("utf-8")
    view = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return view, domain_hash("setec-preflight-analysis-v1", view.encode("utf-8"))


def record_set_sha256(records: Sequence[Record]) -> str:
    pairs = sorted(([r.id, r.content_sha256] for r in records), key=lambda pair: pair[0])
    return domain_hash("setec-preflight-record-set-v1", canonical_json(pairs))


def load_manifest(path: Path, *, combined_limit: int | None = None) -> Manifest:
    path = Path(os.path.abspath(path))
    root = path.parent
    snapshot = read_bounded(root, path.name, MANIFEST_LIMIT)
    rows = snapshot.data.split(b"\n")
    if rows and rows[-1] == b"":
        rows.pop()
    if not 1 <= len(rows) <= MAX_RECORDS:
        raise Refusal("input_contract")
    ceiling = COMBINED_CANDIDATE_LIMIT
    if combined_limit is not None:
        if type(combined_limit) is not int or combined_limit < 0:
            raise Refusal("size_limit")
        ceiling = min(ceiling, combined_limit)
    parsed: list[dict] = []
    ids: set[str] = set()
    names: set[str] = set()
    for index, line in enumerate(rows):
        terminator = int(index < len(rows) - 1 or snapshot.data.endswith(b"\n"))
        if not line or len(line) + terminator > 32 * 1024:
            raise Refusal("input_contract")
        row = exact_keys(parse_json(line, "input_contract"),
                         {"id", "group_id", "stratum", "path", "span"}, "input_contract")
        for key in ("id", "group_id", "stratum"):
            _label(row[key])
        if row["id"] in ids:
            raise Refusal("input_contract")
        ids.add(row["id"])
        span = exact_keys(row["span"],
                          {"source_path", "source_bytes_sha256", "start_byte", "end_byte"},
                          "input_contract")
        for key in ("path", "source_path"):
            name = row[key] if key == "path" else span[key]
            _relative_name(name)
            names.add(name)
        require_hex(span["source_bytes_sha256"], "input_contract")
        if (type(span["start_byte"]) is not int or type(span["end_byte"]) is not int
                or not 0 <= span["start_byte"] < span["end_byte"]):
            raise Refusal("input_contract")
        parsed.append(row)
    bound: dict[str, tuple[Path, tuple[int, ...]]] = {}
    owners: dict[tuple[int, int], str] = {}
    for name in sorted(names):
        target, fingerprint = _bind(root, name)
        identity = (fingerprint[0], fingerprint[1])
        if identity in owners and owners[identity] != name:
            raise Refusal("path_alias")
        owners[identity] = name
        bound[name] = (target, fingerprint)
    cache: dict[str, Snapshot] = {}
    total = 0
    for name in sorted({row["path"] for row in parsed}):
        target, fingerprint = bound[name]
        if fingerprint[2] > CANDIDATE_LIMIT or total + fingerprint[2] > ceiling:
            raise Refusal("size_limit")
        cache[name] = _read_bound(target, root, CANDIDATE_LIMIT, fingerprint)
        total += len(cache[name].data)
    records: list[Record] = []
    for row in parsed:
        span = row["span"]
        candidate = cache[row["path"]]
        view, analysis_sha = _analysis(candidate.data)
        self_span = span["source_path"] == row["path"]
        if self_span and (span["start_byte"] != 0 or span["end_byte"] != len(candidate.data)
                          or span["source_bytes_sha256"] != candidate.sha256):
            raise Refusal("input_contract")
        content_sha = domain_hash("setec-preflight-record-content-v1", canonical_json({
            "candidate_bytes_sha256": candidate.sha256,
            "source_bytes_sha256": span["source_bytes_sha256"],
            "start_byte": span["start_byte"], "end_byte": span["end_byte"],
        }))
        records.append(Record(row["id"], row["group_id"], row["stratum"], row["path"],
                              span["source_path"], span["source_bytes_sha256"],
                              span["start_byte"], span["end_byte"], candidate,
                              view, analysis_sha, content_sha, self_span))
    return Manifest(snapshot.sha256, root,
                    {name: (fingerprint[0], fingerprint[1])
                     for name, (_, fingerprint) in bound.items()}, tuple(records))


def coordination_label(label: str, allowed: tuple[str, ...]) -> str:
    return label if label in allowed else "_other"


def collapse_strata(counts: dict[str, int], allowed: tuple[str, ...]) -> dict[str, int] | None:
    collapsed: dict[str, int] = {}
    for label, count in counts.items():
        key = coordination_label(label, allowed)
        collapsed[key] = collapsed.get(key, 0) + count
    return collapsed if all(value == 0 or value >= 5 for value in collapsed.values()) else None


class WorkBudget:
    def __init__(self, ceilings: Mapping[str, int]) -> None:
        self.ceilings = dict(ceilings)
        self.used = {name: 0 for name in ceilings}

    def charge(self, name: str, n: int = 1) -> None:
        if type(n) is not int or n < 0 or name not in self.ceilings:
            raise ValueError("invalid work charge")
        if self.used[name] + n > self.ceilings[name]:
            raise Refusal("work_limit")
        self.used[name] += n


def validate_output_path(manifest_root: Path, dest: Path) -> Path:
    dest = Path(os.path.abspath(dest))
    try:
        root = manifest_root.resolve(strict=True)
        parent = dest.parent.resolve(strict=True)
    except OSError:
        raise Refusal("output_unavailable") from None
    resolved_dest = parent / dest.name
    if parent == root or root in parent.parents or resolved_dest == root or resolved_dest in root.parents:
        raise Refusal("path_confinement")
    if dest.exists() or dest.is_symlink():
        raise Refusal("output_collision")
    if not dest.parent.is_dir() or dest.parent.is_symlink():
        raise Refusal("output_unavailable")
    return dest


def publish_bundle(dest: Path, files: dict[str, bytes]) -> None:
    dest = Path(os.path.abspath(dest))
    if dest.exists() or dest.is_symlink():
        raise Refusal("output_collision")
    if not dest.parent.is_dir():
        raise Refusal("output_unavailable")
    staging: Path | None = None
    try:
        if os.name == "nt":
            parent_chain = winio.pin_directory_chain(dest.parent)
            try:
                for _ in range(8):
                    name = ".preflight-" + secrets.token_hex(16)
                    try:
                        stage_handle = winio.create_owner_private_directory(parent_chain[-1], name)
                        staging = dest.parent / name
                        break
                    except FileExistsError:
                        continue
                else:
                    raise Refusal("output_collision")
            finally:
                for handle in reversed(parent_chain):
                    winio.close(handle)
        else:
            staging = Path(tempfile.mkdtemp(prefix=".preflight-", dir=dest.parent))
            os.chmod(staging, 0o700)
        for name, payload in files.items():
            if name not in {"detail.json", "receipt.json", "conflicts.json"}:
                raise ValueError("unexpected bundle file")
            if os.name == "nt":
                file_handle = winio.create_owner_private_file(stage_handle, name)
                try:
                    for start in range(0, len(payload), 1024 * 1024):
                        chunk = payload[start:start + 1024 * 1024]
                        if winio.write(file_handle, chunk) != len(chunk):
                            raise OSError("short private output write")
                    winio.flush(file_handle)
                finally:
                    winio.close(file_handle)
            else:
                descriptor = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(descriptor, "wb", closefd=False) as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    os.close(descriptor)
                os.chmod(staging / name, 0o600)
        if os.name == "posix":
            directory = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            winio.require_owner_private(stage_handle, "directory")
            winio.close(stage_handle)
            stage_handle = 0
        atomic_publish.publish_directory_noreplace(staging, dest)
        staging = None
    except FileExistsError:
        raise Refusal("output_collision") from None
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("output_unavailable") from None
    finally:
        if os.name == "nt" and "stage_handle" in locals() and stage_handle:
            winio.close(stage_handle)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def validate_coordination_strata(value: object) -> tuple[str, ...]:
    if (type(value) is not list or not 1 <= len(value) <= 32
            or any(type(item) is not str or len(item) > 64 or
                   _STRATUM.fullmatch(item) is None for item in value)
            or len(set(value)) != len(value)):
        raise Refusal("policy_contract")
    return tuple(value)
