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
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
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
    except (UnicodeError, ValueError, TypeError, RecursionError):
        # RecursionError: deeply nested arrays or objects exhaust the decoder
        # stack; that is a malformed input, not an internal failure.
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


def _anchor(directory: Path) -> Path:
    """Anchor at the directory's real parent chain; the directory stays literal.

    Symlinks above the directory (for example macOS ``/tmp`` or a symlinked
    home) are resolved once here. The directory itself and everything below it
    is still opened component by component without following any symlink.
    """
    directory = Path(os.path.abspath(directory))
    if directory.parent == directory:
        return directory
    try:
        return Path(os.path.realpath(directory.parent)) / directory.name
    except (OSError, ValueError):
        raise Refusal("path_confinement") from None


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


@dataclass(frozen=True)
class BoundInput:
    """A control file bound for confinement but not yet size-checked or read.

    Commands bind every input first so a confinement refusal outranks a size
    or contract refusal on another input (§4.6, first match wins).
    """
    root: Path
    target: Path
    fingerprint: tuple[int, ...]

    def check_size(self, limit: int) -> None:
        if self.fingerprint[2] > limit:
            raise Refusal("size_limit")

    def read(self, limit: int) -> Snapshot:
        return _read_bound(self.target, self.root, limit, self.fingerprint)


def bind_input(path: Path) -> BoundInput:
    """Bind one named file beneath its own directory (``path_confinement`` only)."""
    path = Path(os.path.abspath(path))
    root = _anchor(path.parent)
    target, fingerprint = _bind(root, path.name)
    return BoundInput(root, target, fingerprint)


def read_bounded(root: Path, rel: str, limit: int) -> Snapshot:
    """Read an exact regular-file snapshot beneath an anchored directory."""
    if type(limit) is not int or limit < 0:
        raise ValueError("invalid bound")
    root = _anchor(root)
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
    # C0 is U+0000..U+001F and C1 is U+0080..U+009F; U+007F (DEL) is neither.
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise Refusal("input_contract")
    if any(ord(ch) < 32 or 128 <= ord(ch) <= 159 or
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
    if any(128 <= ord(ch) <= 159 for ch in text):
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


_ROW_KEYS = frozenset({"id", "group_id", "stratum", "path", "span"})
_SPAN_KEYS = frozenset({"source_path", "source_bytes_sha256", "start_byte", "end_byte"})
_JSON_WHITESPACE = b" \t\r"


@dataclass(frozen=True)
class ManifestPlan:
    """A read manifest whose named paths are bound, before any contract check.

    ``contract_ok`` records whether every row met §4.2; the refusal is raised
    only after confinement, aliasing, and sizes, which rank earlier in §4.6.
    """
    snapshot: Snapshot
    root: Path
    rows: tuple[dict, ...]
    contract_ok: bool
    bound: Mapping[str, tuple[Path, tuple[int, ...]]]
    candidates: frozenset[str]


def _row_contract(line: bytes, value: object, terminated: bool) -> bool:
    if (not line or len(line) + int(terminated) > 32 * 1024
            or line[:1] in (b" ", b"\t", b"\r") or line[-1:] in (b" ", b"\t", b"\r")):
        return False
    try:
        row = exact_keys(value, _ROW_KEYS, "input_contract")
        for key in ("id", "group_id", "stratum"):
            _label(row[key])
        span = exact_keys(row["span"], _SPAN_KEYS, "input_contract")
        require_hex(span["source_bytes_sha256"], "input_contract")
    except Refusal:
        return False
    return (type(span["start_byte"]) is int and type(span["end_byte"]) is int
            and 0 <= span["start_byte"] < span["end_byte"])


def plan_manifest(source: BoundInput) -> ManifestPlan:
    """Read the manifest and bind every path it names (§4.2, §4.3).

    Raises ``size_limit`` for the manifest file itself, then
    ``path_confinement`` for any named path, then ``path_alias``. Row contract
    failures are recorded, not raised, so they cannot outrank those codes.
    """
    source.check_size(MANIFEST_LIMIT)
    snapshot = source.read(MANIFEST_LIMIT)
    lines = snapshot.data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    contract_ok = 1 <= len(lines) <= MAX_RECORDS
    names: set[str] = set()
    candidates: set[str] = set()
    rows: list[dict] = []
    ids: set[str] = set()
    for index, line in enumerate(lines):
        try:
            value = parse_json(line, "input_contract")
        except Refusal:
            contract_ok = False
            continue
        if type(value) is dict:
            span = value.get("span")
            named = [value["path"]] if "path" in value else []
            if type(span) is dict and "source_path" in span:
                named.append(span["source_path"])
            for name in named:
                _relative_name(name)
                names.add(name)
            if "path" in value:
                candidates.add(value["path"])
        terminated = index < len(lines) - 1 or snapshot.data.endswith(b"\n")
        if not _row_contract(line, value, terminated) or value["id"] in ids:
            contract_ok = False
            continue
        ids.add(value["id"])
        rows.append(value)
    bound: dict[str, tuple[Path, tuple[int, ...]]] = {}
    for name in sorted(names):
        bound[name] = _bind(source.root, name)
    owners: dict[tuple[int, int], str] = {}
    for name, (_, fingerprint) in bound.items():
        identity = (fingerprint[0], fingerprint[1])
        if identity in owners and owners[identity] != name:
            raise Refusal("path_alias")
        owners[identity] = name
    return ManifestPlan(snapshot, source.root, tuple(rows), contract_ok, bound,
                        frozenset(candidates))


def check_candidate_sizes(plan: ManifestPlan, *, combined_limit: int | None = None) -> None:
    """Refuse ``size_limit`` for any candidate or the combined candidate bytes."""
    ceiling = COMBINED_CANDIDATE_LIMIT
    if combined_limit is not None:
        if type(combined_limit) is not int or combined_limit < 0:
            raise Refusal("size_limit")
        ceiling = min(ceiling, combined_limit)
    total = 0
    for name in sorted(plan.candidates):
        size = plan.bound[name][1][2]
        total += size
        if size > CANDIDATE_LIMIT or total > ceiling:
            raise Refusal("size_limit")


def finish_manifest(plan: ManifestPlan, *, calibration: bool = False,
                    expected_sha256: str | None = None) -> tuple[Manifest, dict[str, str], str]:
    """Read candidates and apply the §4.2 contract, text rules, and self-spans."""
    if not plan.contract_ok:
        raise Refusal("input_contract")
    root = plan.root
    cache: dict[str, Snapshot] = {}
    analyses: dict[str, tuple[str, str] | str] = {}
    for name in sorted({row["path"] for row in plan.rows}):
        target, fingerprint = plan.bound[name]
        cache[name] = _read_bound(target, root, CANDIDATE_LIMIT, fingerprint)
        # Text rules and the analysis view run once per distinct candidate
        # file, however many rows share it.
        violation = text_rule_violation(cache[name].data)
        analyses[name] = violation if violation is not None else _analysis(cache[name].data)
    records: list[Record] = []
    violations: dict[str, str] = {}
    content_pairs: list[list[str]] = []
    for row in plan.rows:
        span = row["span"]
        candidate = cache[row["path"]]
        self_span = span["source_path"] == row["path"]
        if self_span and (span["start_byte"] != 0 or span["end_byte"] != len(candidate.data)
                          or span["source_bytes_sha256"] != candidate.sha256):
            raise Refusal("input_contract")
        content_sha = domain_hash("setec-preflight-record-content-v1", canonical_json({
            "candidate_bytes_sha256": candidate.sha256,
            "source_bytes_sha256": span["source_bytes_sha256"],
            "start_byte": span["start_byte"], "end_byte": span["end_byte"],
        }))
        content_pairs.append([row["id"], content_sha])
        analysis = analyses[row["path"]]
        if type(analysis) is str:
            if not calibration:
                raise Refusal("input_contract")
            violations[row["id"]] = analysis
            continue
        view, analysis_sha = analysis
        records.append(Record(row["id"], row["group_id"], row["stratum"], row["path"],
                              span["source_path"], span["source_bytes_sha256"],
                              span["start_byte"], span["end_byte"], candidate,
                              view, analysis_sha, content_sha, self_span))
    if expected_sha256 is not None:
        require_hex(expected_sha256, "calibration_binding")
        if plan.snapshot.sha256 != expected_sha256:
            raise Refusal("calibration_binding")
    manifest = Manifest(plan.snapshot.sha256, root,
                        {name: (fingerprint[0], fingerprint[1])
                         for name, (_, fingerprint) in plan.bound.items()}, tuple(records))
    record_set = domain_hash("setec-preflight-record-set-v1", canonical_json(
        sorted(content_pairs, key=lambda pair: pair[0])))
    return manifest, violations, record_set


def _load_manifest(path: Path, *, combined_limit: int | None = None,
                   calibration: bool = False,
                   expected_sha256: str | None = None) -> tuple[Manifest, dict[str, str], str]:
    plan = plan_manifest(bind_input(path))
    check_candidate_sizes(plan, combined_limit=combined_limit)
    return finish_manifest(plan, calibration=calibration, expected_sha256=expected_sha256)


def load_manifest(path: Path, *, combined_limit: int | None = None) -> Manifest:
    return _load_manifest(path, combined_limit=combined_limit)[0]


def load_manifest_for_calibration(path: Path, *,
                                  expected_sha256: str | None = None) -> tuple[Manifest, dict[str, str], str]:
    return _load_manifest(path, calibration=True, expected_sha256=expected_sha256)


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


def _real_chain(path: Path) -> list[tuple[int, int]]:
    """``(st_dev, st_ino)`` of ``path``'s real location and every real ancestor."""
    real = Path(os.path.realpath(path))
    chain = []
    for item in (real, *real.parents):
        info = os.stat(item)
        chain.append((info.st_dev, info.st_ino))
    return chain


def confine_output_path(manifest_root: Path, dest: Path) -> Path:
    """Refuse ``path_confinement`` when the bundle and manifest directory nest (§4.2).

    Containment compares file identities, not strings, so a case-folding or
    symlinked spelling of the manifest directory cannot slip past. Missing
    tail components of ``dest`` are fine here; availability is checked later.
    """
    dest = Path(os.path.abspath(dest))
    try:
        info = os.stat(manifest_root)
        root = (info.st_dev, info.st_ino)
        existing = dest.parent
        while not os.path.exists(existing) and existing.parent != existing:
            existing = existing.parent
        if root in _real_chain(existing):
            raise Refusal("path_confinement")
        if os.path.exists(dest):
            info = os.stat(dest)
            if (info.st_dev, info.st_ino) in _real_chain(manifest_root):
                raise Refusal("path_confinement")
    except OSError:
        raise Refusal("output_unavailable") from None
    return dest


def validate_output_path(manifest_root: Path, dest: Path) -> Path:
    dest = confine_output_path(manifest_root, dest)
    if os.path.lexists(dest):
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


def verify_overlap_detail(detail: dict, manifest: Manifest) -> None:
    """Bind a strict overlap detail to live packet bytes and its components."""
    code = "detail_contract"
    if (detail["inputs"]["manifest_sha256"] != manifest.manifest_sha256 or
            detail["inputs"]["record_set_sha256"] != record_set_sha256(manifest.records)):
        raise Refusal(code)
    live = {record.id: record for record in manifest.records}
    rows = {row["id"]: row for row in detail["records"]}
    if set(rows) != set(live):
        raise Refusal(code)
    for record_id, record in live.items():
        row = rows[record_id]
        expected = {
            "group_id": record.group_id, "stratum": record.stratum,
            "candidate_bytes_sha256": record.candidate.sha256,
            "analysis_sha256": record.analysis_sha256,
            "content_sha256": record.content_sha256,
            "source_bytes_sha256": record.source_bytes_sha256,
            "start_byte": record.start_byte, "end_byte": record.end_byte,
            "self_span": record.self_span,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise Refusal(code)
    parent = {record_id: record_id for record_id in live}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    actual_exact: set[tuple[str, str]] = set()
    for edge in detail["edges"]:
        left, right = edge["left_id"], edge["right_id"]
        if left not in live or right not in live:
            raise Refusal(code)
        if edge["edge_type"] == "exact":
            if live[left].analysis_sha256 != live[right].analysis_sha256:
                raise Refusal(code)
            actual_exact.add((left, right))
        first, second = find(left), find(right)
        if first != second:
            parent[max(first, second)] = min(first, second)
    by_analysis: dict[str, list[str]] = defaultdict(list)
    for record in manifest.records:
        by_analysis[record.analysis_sha256].append(record.id)
    if sum(len(ids) * (len(ids) - 1) // 2 for ids in by_analysis.values()) > len(actual_exact):
        raise Refusal(code)
    expected_exact = {pair for ids in by_analysis.values()
                      for pair in combinations(sorted(ids), 2)}
    if actual_exact != expected_exact:
        raise Refusal(code)
    components: dict[str, set[str]] = defaultdict(set)
    for record_id in live:
        components[find(record_id)].add(record_id)
    cluster_members = {frozenset(cluster["member_ids"]) for cluster in detail["clusters"]}
    if cluster_members != {frozenset(members) for members in components.values()}:
        raise Refusal(code)
