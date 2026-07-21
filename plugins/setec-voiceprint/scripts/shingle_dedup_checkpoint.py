"""Race-resistant immutable checkpoint shards for ``shingle_dedup``.

This module intentionally owns the complete checkpoint filesystem boundary.
Callers never enumerate a checkpoint directory, reopen a shard name, or hand a
named SQLite database to SQLite.  Final shards are read once through retained
directory handles, deserialized into memory, exactly validated, and represented
by :class:`CheckpointSnapshot` objects.  New shards are built in memory and
published create-new through the retained directory handle.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Iterable, Mapping, Sequence


MAX_ENTRIES = 4_056
MAX_FINAL_SHARDS = 4_040
MAX_RESERVED_TEMPS = 16
MAX_SHARD_BYTES = 128 * 1024 * 1024
MAX_CUMULATIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CUMULATIVE_VM_OPCODES = 2_000_000_000
VM_INTERVAL = 1_000
MAX_ITEM_COUNT = 250
MAX_DESCRIPTORS = 5_000
MAX_TOKENS_PER_DOCUMENT = 500_000
MAX_TOTAL_TOKENS = 5_000_000
MAX_POSTINGS = 5_000_000
MAX_POTENTIAL_PAIRS = 1_000_000
MAX_REPORTED_PAIRS = 50_000
MAX_SHINGLES_PER_DOCUMENT = 500_000
CHECKPOINT_APPLICATION_ID = 0x53484331
CHECKPOINT_USER_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = "setec-shingle-checkpoint/1"

_FINAL_RE = re.compile(r"(inventory|build|batch)-(\d{8})\.sqlite\Z")
_TEMP_RE = re.compile(
    r"\.tmp-(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?:-journal|-wal|-shm)?\Z"
)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_UNSIGNED_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\\/]")

_META_KEYS = frozenset({
    "schema_version", "tool", "method_version", "checkpoint_kind", "chunk_number",
    "source_manifest_sha256", "canonical_descriptors_sha256", "index_sha256",
    "logical_index_sha256", "config_sha256", "first_item", "next_item", "item_count",
    "potential_pairs", "unassessed_pairs", "assessed_pairs", "no_overlap_pairs",
    "below_0_35_pairs", "containment_0_35_to_0_60_pairs",
    "containment_at_least_0_60_pairs", "reported_pairs", "checkpoint_sha256",
})
_COUNTERS = (
    "potential_pairs", "unassessed_pairs", "assessed_pairs", "no_overlap_pairs",
    "below_0_35_pairs", "containment_0_35_to_0_60_pairs",
    "containment_at_least_0_60_pairs", "reported_pairs",
)
_PAIR_KEYS = frozenset({
    "pair_kind", "query_id", "reference_id", "draft_id", "query_stage",
    "query_stage_order", "reference_stage", "reference_stage_order", "query_tokens",
    "reference_tokens", "query_shingles", "reference_shingles", "shared_shingles",
    "containment_numerator", "containment_denominator", "containment",
    "reverse_containment_numerator", "reverse_containment_denominator",
    "reverse_containment", "jaccard_numerator", "jaccard_denominator", "jaccard",
    "tier_metric_numerator", "tier_metric_denominator", "tier_metric",
    "pair_containment_direction", "overlap_tier",
})

_META_SQL = "CREATE TABLE checkpoint_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID"
_INVENTORY_SQL = (
    "CREATE TABLE inventory(doc_id TEXT PRIMARY KEY COLLATE BINARY,draft_id TEXT NOT NULL COLLATE BINARY,"
    "stage TEXT NOT NULL COLLATE BINARY,stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL) WITHOUT ROWID"
)
_DOCUMENTS_SQL = (
    "CREATE TABLE documents(doc_id TEXT PRIMARY KEY COLLATE BINARY,draft_id TEXT NOT NULL COLLATE BINARY,"
    "stage TEXT NOT NULL COLLATE BINARY,stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL,"
    "token_count INTEGER NOT NULL,shingle_count INTEGER NOT NULL,status TEXT NOT NULL) WITHOUT ROWID"
)
_POSTINGS_SQL = (
    "CREATE TABLE postings(shingle_sha256 BLOB NOT NULL,doc_id TEXT NOT NULL COLLATE BINARY REFERENCES "
    "documents(doc_id),PRIMARY KEY(shingle_sha256,doc_id)) WITHOUT ROWID"
)
_LOOKUP_SQL = "CREATE INDEX documents_shingle_lookup ON postings(doc_id,shingle_sha256)"
_PAIRS_SQL = "CREATE TABLE pairs(sequence INTEGER PRIMARY KEY,pair_json BLOB NOT NULL,pair_sha256 BLOB NOT NULL)"


class CheckpointRefusal(Exception):
    """Stable non-disclosing checkpoint refusal."""


def _refuse() -> CheckpointRefusal:
    return CheckpointRefusal("checkpoint operation refused")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError):
        raise _refuse() from None


def _strict_json(raw: str) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    return json.loads(raw, parse_constant=reject_constant, object_pairs_hook=reject_duplicates)


def _opaque(value: object) -> bool:
    if type(value) is not str or value != value.strip() or value in {"", ".", ".."}:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return False
    return len(encoded) <= 128 and _CONTROL_OR_SEPARATOR.search(value) is None


def _integer(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**63 - 1


def _minimal_unsigned(value: object) -> int:
    if type(value) is not str or _UNSIGNED_RE.fullmatch(value) is None:
        raise _refuse()
    return int(value)


class _SharedVmBudget:
    def __init__(self) -> None:
        self.callbacks = 0
        self.maximum_callbacks = MAX_CUMULATIVE_VM_OPCODES // VM_INTERVAL

    def install(self, connection: sqlite3.Connection) -> None:
        local_callbacks = 0

        def bounded_callback() -> int:
            nonlocal local_callbacks
            local_callbacks += 1
            self.callbacks += 1
            return int(local_callbacks > 500_000 or self.callbacks > self.maximum_callbacks)

        connection.set_progress_handler(bounded_callback, VM_INTERVAL)
        setlimit = getattr(connection, "setlimit", None)
        if setlimit is not None:
            for name, ceiling in (
                ("SQLITE_LIMIT_LENGTH", 16_777_216), ("SQLITE_LIMIT_SQL_LENGTH", 65_536),
                ("SQLITE_LIMIT_COLUMN", 64), ("SQLITE_LIMIT_EXPR_DEPTH", 32),
                ("SQLITE_LIMIT_COMPOUND_SELECT", 16), ("SQLITE_LIMIT_VARIABLE_NUMBER", 32),
                ("SQLITE_LIMIT_ATTACHED", 0), ("SQLITE_LIMIT_TRIGGER_DEPTH", 0),
            ):
                constant = getattr(sqlite3, name, None)
                if constant is not None:
                    setlimit(constant, ceiling)


@dataclass(frozen=True)
class CheckpointSnapshot:
    name: str
    kind: str
    chunk_number: int
    raw: bytes
    meta: Mapping[str, str]
    inventory_rows: tuple[tuple[Any, ...], ...] = ()
    document_rows: tuple[tuple[Any, ...], ...] = ()
    posting_rows: tuple[tuple[Any, ...], ...] = ()
    pair_rows: tuple[tuple[int, bytes, bytes], ...] = ()


@dataclass(frozen=True)
class CheckpointState:
    mode: str
    snapshots: tuple[CheckpointSnapshot, ...]
    continuations: Mapping[str, str]

    @property
    def inventory(self) -> tuple[CheckpointSnapshot, ...]:
        return tuple(item for item in self.snapshots if item.kind == "inventory")

    @property
    def build(self) -> tuple[CheckpointSnapshot, ...]:
        return tuple(item for item in self.snapshots if item.kind == "build")

    @property
    def batch(self) -> tuple[CheckpointSnapshot, ...]:
        return tuple(item for item in self.snapshots if item.kind == "batch")

    def continuation(self, kind: str) -> str | None:
        return self.continuations.get(kind)


def _absolute(path: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(path)
        if type(raw) is not str or not raw or "\x00" in raw:
            raise _refuse()
        return Path(os.path.abspath(raw))
    except (OSError, TypeError, ValueError):
        raise _refuse() from None


def _optional_flag(name: str) -> int:
    return int(getattr(os, name, 0))


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns),
            int(info.st_ctime_ns), int(info.st_nlink))


def _posix_pin_directory(path: Path) -> tuple[int, list[int]]:
    if not path.is_absolute():
        raise _refuse()
    flags = os.O_RDONLY | _optional_flag("O_DIRECTORY") | _optional_flag("O_CLOEXEC") | _optional_flag("O_NOFOLLOW") | _optional_flag("O_BINARY")
    opened: list[int] = []
    try:
        current = os.open(path.anchor or "/", flags)
        opened.append(current)
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise _refuse()
            named = os.stat(component, dir_fd=current, follow_symlinks=False)
            following = os.open(component, flags, dir_fd=current)
            # Own the descriptor immediately: even an injected/real fstat
            # failure must be covered by the exception cleanup below.
            opened.append(following)
            opened_info = os.fstat(following)
            if not stat.S_ISDIR(named.st_mode) or _identity(named) != _identity(opened_info):
                raise _refuse()
            current = following
        return current, opened
    except (CheckpointRefusal, OSError, TypeError, ValueError):
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _refuse() from None


def _posix_revalidate(path: Path, descriptors: Sequence[int]) -> None:
    if len(descriptors) != len(path.parts):
        raise _refuse()
    try:
        for index, component in enumerate(path.parts[1:]):
            named = os.stat(component, dir_fd=descriptors[index], follow_symlinks=False)
            opened = os.fstat(descriptors[index + 1])
            if not stat.S_ISDIR(named.st_mode) or _identity(named) != _identity(opened):
                raise _refuse()
    except (OSError, TypeError, ValueError):
        raise _refuse() from None


class CheckpointDirectory:
    """A retained, identity-checked checkpoint directory handle."""

    def __init__(self, path: Path, *, posix_handles: list[int] | None = None,
                 windows_handles: tuple[int, ...] | None = None) -> None:
        self.path = path
        self._posix_handles = posix_handles
        self._windows_handles = windows_handles
        self._closed = False
        self._known_names: set[str] | None = None
        self._snapshots: list[CheckpointSnapshot] = []
        self._windows_listing: dict[str, tuple[int, int, int, int, int]] = {}

    @classmethod
    def open_resume(cls, path: os.PathLike[str] | str) -> "CheckpointDirectory":
        target = _absolute(path)
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            try:
                import windows_descriptor_io as winio
                handles = winio.pin_directory_chain(target, writable_final=True)
                return cls(target, windows_handles=handles)
            except (ImportError, OSError, TypeError, ValueError):
                raise _refuse() from None
        _directory, handles = _posix_pin_directory(target)
        return cls(target, posix_handles=handles)

    @classmethod
    def open_new(cls, path: os.PathLike[str] | str) -> "CheckpointDirectory":
        target = _absolute(path)
        if target.name in {"", ".", ".."}:
            raise _refuse()
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            parent_chain: tuple[int, ...] = ()
            created = 0
            try:
                import windows_descriptor_io as winio
                parent_chain = winio.pin_directory_chain(target.parent, writable_final=True)
                winio.revalidate_directory_chain(target.parent, parent_chain)
                created = winio.create_directory(parent_chain[-1], target.name)
                handles = (*parent_chain, created)
                winio.revalidate_directory_chain(target, handles)
                result = cls(target, windows_handles=handles)
                result._known_names = set()
                parent_chain = ()
                created = 0
                return result
            except (ImportError, OSError, TypeError, ValueError):
                raise _refuse() from None
            finally:
                if created:
                    try: winio.close(created)
                    except (NameError, OSError): pass
                for handle in reversed(parent_chain):
                    try: winio.close(handle)
                    except (NameError, OSError): pass
        parent_fd, parents = _posix_pin_directory(target.parent)
        flags = os.O_RDONLY | _optional_flag("O_DIRECTORY") | _optional_flag("O_CLOEXEC") | _optional_flag("O_NOFOLLOW") | _optional_flag("O_BINARY")
        created = -1
        try:
            _posix_revalidate(target.parent, parents)
            os.mkdir(target.name, 0o700, dir_fd=parent_fd)
            created = os.open(target.name, flags, dir_fd=parent_fd)
            named = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode) or _identity(named) != _identity(os.fstat(created)):
                raise _refuse()
            handles = parents + [created]
            _posix_revalidate(target, handles)
            result = cls(target, posix_handles=handles)
            result._known_names = set()
            # Ownership transfers to the returned CheckpointDirectory only
            # after all post-open validation has succeeded.
            parents = []
            created = -1
            return result
        except (OSError, TypeError, ValueError):
            raise _refuse() from None
        finally:
            if created >= 0:
                try:
                    os.close(created)
                except OSError:
                    pass
            for descriptor in reversed(parents):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def __enter__(self) -> "CheckpointDirectory":
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._posix_handles is not None:
            for descriptor in reversed(self._posix_handles):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if self._windows_handles is not None:  # pragma: no cover - Windows CI
            import windows_descriptor_io as winio
            for handle in reversed(self._windows_handles):
                try:
                    winio.close(handle)
                except OSError:
                    pass

    def _require_open(self) -> None:
        if self._closed:
            raise _refuse()

    def _revalidate(self) -> None:
        self._require_open()
        if self._posix_handles is not None:
            _posix_revalidate(self.path, self._posix_handles)
            return
        try:  # pragma: no cover - Windows CI
            import windows_descriptor_io as winio
            assert self._windows_handles is not None
            winio.revalidate_directory_chain(self.path, self._windows_handles)
        except (ImportError, OSError, TypeError, ValueError):
            raise _refuse() from None

    def _list_names(self) -> tuple[str, ...]:
        self._revalidate()
        try:
            if self._posix_handles is not None:
                return tuple(sorted(os.listdir(self._posix_handles[-1]), key=lambda item: item.encode("utf-8")))
            import windows_descriptor_io as winio  # pragma: no cover - Windows CI
            assert self._windows_handles is not None
            entries = winio.list_entries(self._windows_handles[-1])
            self._windows_listing = {
                name: (size, attributes, creation, write_time, change_time)
                for name, size, attributes, creation, write_time, change_time in entries
            }
            return tuple(name for name, *_metadata in entries)
        except (OSError, TypeError, ValueError, UnicodeError):
            raise _refuse() from None

    def _entry_info(self, name: str) -> tuple[int, object]:
        try:
            if self._posix_handles is not None:
                info = os.stat(name, dir_fd=self._posix_handles[-1], follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise _refuse()
                return int(info.st_size), _fingerprint(info)
            import windows_descriptor_io as winio  # pragma: no cover - Windows CI
            assert self._windows_handles is not None
            if _TEMP_RE.fullmatch(name) is not None:
                metadata = self._windows_listing.get(name)
                if metadata is None:
                    raise _refuse()
                size, attributes, creation, write_time, change_time = metadata
                if attributes & (winio.FILE_ATTRIBUTE_DIRECTORY | winio.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise _refuse()
                return size, (size, attributes, creation, write_time, change_time)
            handle = winio.open_file(self._windows_handles[-1], name)
            try:
                info = winio.require_direct(handle, "file")
                return info.size, info.identity
            finally:
                winio.close(handle)
        except (OSError, TypeError, ValueError):
            raise _refuse() from None

    def _read_final(self, name: str) -> bytes:
        if self._posix_handles is not None:
            descriptor = -1
            try:
                parent = self._posix_handles[-1]
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= MAX_SHARD_BYTES:
                    raise _refuse()
                flags = os.O_RDONLY | _optional_flag("O_CLOEXEC") | _optional_flag("O_NOFOLLOW") | _optional_flag("O_BINARY")
                descriptor = os.open(name, flags, dir_fd=parent)
                opened = os.fstat(descriptor)
                if _fingerprint(before) != _fingerprint(opened):
                    raise _refuse()
                parts: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(1024 * 1024, MAX_SHARD_BYTES + 1 - total))
                    if not chunk:
                        break
                    parts.append(chunk); total += len(chunk)
                    if total > MAX_SHARD_BYTES:
                        raise _refuse()
                after = os.fstat(descriptor)
                self._revalidate()
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if _fingerprint(opened) != _fingerprint(after) or _fingerprint(opened) != _fingerprint(named) or total != after.st_size:
                    raise _refuse()
                return b"".join(parts)
            except (OSError, TypeError, ValueError):
                raise _refuse() from None
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
        try:  # pragma: no cover - Windows CI
            import windows_descriptor_io as winio
            assert self._windows_handles is not None
            parent = self._windows_handles[-1]
            handle = winio.open_file(parent, name)
            verify = 0
            try:
                opened = winio.require_direct(handle, "file")
                if not 0 <= opened.size <= MAX_SHARD_BYTES:
                    raise _refuse()
                parts: list[bytes] = []
                total = 0
                while True:
                    chunk = winio.read(handle, min(1024 * 1024, MAX_SHARD_BYTES + 1 - total))
                    if not chunk:
                        break
                    parts.append(chunk); total += len(chunk)
                    if total > MAX_SHARD_BYTES:
                        raise _refuse()
                after = winio.require_direct(handle, "file")
                self._revalidate()
                verify = winio.open_file(parent, name)
                named = winio.require_direct(verify, "file")
                if opened.identity != after.identity or opened.identity != named.identity or total != after.size:
                    raise _refuse()
                return b"".join(parts)
            finally:
                if verify: winio.close(verify)
                winio.close(handle)
        except (ImportError, OSError, TypeError, ValueError):
            raise _refuse() from None

    def load(self, *, mode: str, config_sha256: str,
             source_manifest_sha256: str | None = None,
             canonical_descriptors_sha256: str | None = None,
             index_sha256: str | None = None,
             logical_index_sha256: str | None = None) -> CheckpointState:
        try:
            return self._load(mode=mode, config_sha256=config_sha256,
                              source_manifest_sha256=source_manifest_sha256,
                              canonical_descriptors_sha256=canonical_descriptors_sha256,
                              index_sha256=index_sha256,
                              logical_index_sha256=logical_index_sha256)
        except MemoryError:
            raise _refuse() from None

    def _load(self, *, mode: str, config_sha256: str,
             source_manifest_sha256: str | None = None,
             canonical_descriptors_sha256: str | None = None,
             index_sha256: str | None = None,
             logical_index_sha256: str | None = None) -> CheckpointState:
        """Freeze, snapshot once, and exactly validate all final shards."""
        if mode not in {"build", "batch"} or _HASH_RE.fullmatch(config_sha256) is None:
            raise _refuse()
        names = self._list_names()
        if len(names) > MAX_ENTRIES:
            raise _refuse()
        finals: list[tuple[str, str, int]] = []
        temps = 0
        total_bytes = 0
        fingerprints: dict[str, object] = {}
        for name in names:
            matched = _FINAL_RE.fullmatch(name)
            if matched is None:
                if _TEMP_RE.fullmatch(name) is None:
                    raise _refuse()
                temps += 1
            else:
                finals.append((name, matched.group(1), int(matched.group(2))))
            size, fingerprint = self._entry_info(name)
            if size < 0 or (matched is not None and size > MAX_SHARD_BYTES):
                raise _refuse()
            total_bytes += size
            fingerprints[name] = fingerprint
        if len(finals) > MAX_FINAL_SHARDS or temps > MAX_RESERVED_TEMPS or total_bytes > MAX_CUMULATIVE_BYTES:
            raise _refuse()
        if mode == "build" and any(kind == "batch" for _name, kind, _number in finals):
            raise _refuse()
        if mode == "batch" and any(kind != "batch" for _name, kind, _number in finals):
            raise _refuse()
        budget = _SharedVmBudget()
        snapshots: list[CheckpointSnapshot] = []
        read_total = 0
        for name, kind, number in finals:
            raw = self._read_final(name)
            read_total += len(raw)
            if read_total > MAX_CUMULATIVE_BYTES:
                raise _refuse()
            snapshot = _validate_snapshot(
                name, kind, number, raw, budget, config_sha256=config_sha256,
                source_manifest_sha256=source_manifest_sha256,
                canonical_descriptors_sha256=canonical_descriptors_sha256,
                index_sha256=index_sha256, logical_index_sha256=logical_index_sha256,
            )
            snapshots.append(CheckpointSnapshot(
                snapshot.name, snapshot.kind, snapshot.chunk_number, b"", snapshot.meta,
                snapshot.inventory_rows, snapshot.document_rows, snapshot.posting_rows,
                snapshot.pair_rows,
            ))
        final_names = self._list_names()
        if len(final_names) != len(names) or set(final_names) != set(names):
            raise _refuse()
        for name in names:
            _size, fingerprint = self._entry_info(name)
            if fingerprint != fingerprints[name]:
                raise _refuse()
        ordered, continuations = _validate_sequence(mode, snapshots)
        self._known_names = set(names)
        self._snapshots = list(ordered)
        return CheckpointState(mode, tuple(ordered), continuations)

    def publish(self, *, kind: str, meta: Mapping[str, str],
                inventory_rows: Iterable[Sequence[Any]] = (),
                document_rows: Iterable[Sequence[Any]] = (),
                posting_rows: Iterable[Sequence[Any]] = (),
                pairs: Iterable[Mapping[str, Any]] = ()) -> CheckpointSnapshot:
        try:
            return self._publish(kind=kind, meta=meta, inventory_rows=inventory_rows,
                                 document_rows=document_rows, posting_rows=posting_rows,
                                 pairs=pairs)
        except MemoryError:
            raise _refuse() from None

    def _publish(self, *, kind: str, meta: Mapping[str, str],
                inventory_rows: Iterable[Sequence[Any]] = (),
                document_rows: Iterable[Sequence[Any]] = (),
                posting_rows: Iterable[Sequence[Any]] = (),
                pairs: Iterable[Mapping[str, Any]] = ()) -> CheckpointSnapshot:
        """Build, exact-validate, and create-new publish one immutable shard."""
        if kind not in {"inventory", "build", "batch"}:
            raise _refuse()
        raw, sealed_meta = _encode_checkpoint(kind, meta, inventory_rows=inventory_rows,
                                              document_rows=document_rows, posting_rows=posting_rows,
                                              pairs=pairs)
        number = _minimal_unsigned(sealed_meta["chunk_number"])
        if number > 99_999_999:
            raise _refuse()
        name = f"{kind}-{number:08d}.sqlite"
        snapshot = _validate_snapshot(name, kind, number, raw, _SharedVmBudget(),
                                      config_sha256=sealed_meta["config_sha256"])
        current = self._list_names()
        if self._known_names is None:
            if any(_FINAL_RE.fullmatch(item) is not None for item in current):
                raise _refuse()
            self._known_names = set(current)
        mode = "batch" if kind == "batch" else "build"
        _validate_sequence(mode, (*self._snapshots, snapshot))
        self._publish_raw(name, raw)
        self._snapshots.append(CheckpointSnapshot(
            snapshot.name, snapshot.kind, snapshot.chunk_number, b"", snapshot.meta,
            snapshot.inventory_rows, snapshot.document_rows, snapshot.posting_rows,
            snapshot.pair_rows,
        ))
        return snapshot

    def _publish_raw(self, name: str, raw: bytes) -> None:
        if _FINAL_RE.fullmatch(name) is None or not isinstance(raw, bytes) or len(raw) > MAX_SHARD_BYTES:
            raise _refuse()
        current = self._list_names()
        if self._known_names is not None and set(current) != self._known_names:
            raise _refuse()
        final_count = sum(_FINAL_RE.fullmatch(item) is not None for item in current)
        cumulative_bytes = sum(self._entry_info(item)[0] for item in current)
        if (name in current or len(current) >= MAX_ENTRIES or final_count >= MAX_FINAL_SHARDS
                or cumulative_bytes + len(raw) > MAX_CUMULATIVE_BYTES):
            raise _refuse()
        temp_count = sum(_TEMP_RE.fullmatch(item) is not None for item in current)
        if temp_count >= MAX_RESERVED_TEMPS:
            raise _refuse()
        if self._posix_handles is not None:
            self._posix_publish(name, raw)
        else:
            self._windows_publish(name, raw)
        expected = set(current) | {name}
        actual = self._list_names()
        if len(actual) != len(expected) or set(actual) != expected:
            raise _refuse()
        self._known_names = expected

    def _posix_publish(self, name: str, raw: bytes) -> None:
        assert self._posix_handles is not None
        parent = self._posix_handles[-1]
        temp_name = ".tmp-" + os.urandom(16).hex()
        descriptor = -1
        identity: tuple[int, int] | None = None
        published_identity: tuple[int, int] | None = None
        try:
            self._revalidate()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _optional_flag("O_CLOEXEC") | _optional_flag("O_BINARY")
            descriptor = os.open(temp_name, flags, 0o600, dir_fd=parent)
            identity = _identity(os.fstat(descriptor))
            view = memoryview(raw)
            while view:
                count = os.write(descriptor, view)
                if count <= 0: raise _refuse()
                view = view[count:]
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            os.close(descriptor); descriptor = -1
            named = os.stat(temp_name, dir_fd=parent, follow_symlinks=False)
            if _fingerprint(named) != _fingerprint(written):
                raise _refuse()
            self._revalidate()
            if os.link not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd:
                raise _refuse()
            try:
                os.link(temp_name, name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
            except BaseException:
                if identity is not None:
                    try:
                        linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if _identity(linked) == identity:
                            published_identity = identity
                    except OSError:
                        pass
                raise
            published_identity = identity
            final = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _identity(final) != identity:
                raise _refuse()
            os.unlink(temp_name, dir_fd=parent)
            identity = None
            final_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _identity(final_after) != _identity(final):
                raise _refuse()
            self._revalidate()
            published_identity = None
            try:
                os.fsync(parent)
            except OSError:
                pass
        except (OSError, TypeError, ValueError, MemoryError):
            raise _refuse() from None
        finally:
            if descriptor >= 0:
                if identity is None:
                    try: identity = _identity(os.fstat(descriptor))
                    except (OSError, MemoryError): pass
                try: os.close(descriptor)
                except OSError: pass
            if identity is not None:
                try:
                    named = os.stat(temp_name, dir_fd=parent, follow_symlinks=False)
                    if _identity(named) == identity:
                        os.unlink(temp_name, dir_fd=parent)
                except OSError:
                    pass
            if published_identity is not None:
                try:
                    named_final = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if _identity(named_final) == published_identity:
                        os.unlink(name, dir_fd=parent)
                except OSError:
                    pass

    def _windows_publish(self, name: str, raw: bytes) -> None:  # pragma: no cover - Windows CI
        try:
            import windows_descriptor_io as winio
            assert self._windows_handles is not None
            parent = self._windows_handles[-1]
            temp_name = ".tmp-" + os.urandom(16).hex()
            payload = control = 0
            try:
                self._revalidate()
                payload = winio.create_file(parent, temp_name)
                view = memoryview(raw)
                while view:
                    count = winio.write(payload, view[:1024 * 1024])
                    if count <= 0: raise _refuse()
                    view = view[count:]
                winio.flush(payload); winio.close(payload); payload = 0
                control = winio.open_file(parent, temp_name, delete_access=True,
                                          share_delete=True, share_write=False)
                original = winio.require_direct(control, "file").identity
                self._revalidate()
                winio.rename(control, parent, name, replace=False)
                self._revalidate()
                verify = winio.open_file(parent, name)
                try:
                    if winio.require_direct(verify, "file").identity != original:
                        raise _refuse()
                finally:
                    winio.close(verify)
                winio.close(control); control = 0
            except BaseException:
                if control:
                    try: winio.delete(control)
                    except OSError: pass
                elif payload:
                    try: winio.delete(payload)
                    except OSError: pass
                raise
            finally:
                for handle in (control, payload):
                    if handle:
                        try: winio.close(handle)
                        except OSError: pass
        except (ImportError, OSError, TypeError, ValueError):
            raise _refuse() from None


def _configure_read(connection: sqlite3.Connection, budget: _SharedVmBudget) -> None:
    budget.install(connection)
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA cache_size=-16384")
    if connection.execute("PRAGMA trusted_schema").fetchone() != (0,) or connection.execute("PRAGMA query_only").fetchone() != (1,):
        raise _refuse()


def _expected_objects(kind: str) -> dict[tuple[str, str], str]:
    result = {("table", "checkpoint_meta"): _META_SQL}
    if kind == "inventory": result[("table", "inventory")] = _INVENTORY_SQL
    elif kind == "build":
        result.update({("table", "documents"): _DOCUMENTS_SQL, ("table", "postings"): _POSTINGS_SQL,
                       ("index", "documents_shingle_lookup"): _LOOKUP_SQL})
    else: result[("table", "pairs")] = _PAIRS_SQL
    return result


def _cursor(raw: str, kind: str) -> object:
    try:
        value = _strict_json(raw)
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise _refuse() from None
    if _canonical(value).decode("ascii").strip() != raw:
        raise _refuse()
    if value is None:
        return None
    keys = {"doc_id"} if kind != "batch" else {
        "draft_id", "query_id", "query_stage_order", "reference_id", "reference_stage_order"
    }
    if type(value) is not dict or set(value) != keys:
        raise _refuse()
    if kind != "batch":
        if not _opaque(value["doc_id"]): raise _refuse()
    else:
        if not all(_opaque(value[key]) for key in ("draft_id", "query_id", "reference_id")):
            raise _refuse()
        if not all(type(value[key]) is int and -(2**63) <= value[key] <= 2**63 - 1
                   for key in ("query_stage_order", "reference_stage_order")):
            raise _refuse()
    return value


def _validate_meta(meta: dict[str, str], kind: str, number: int, *, config_sha256: str,
                   source_manifest_sha256: str | None, canonical_descriptors_sha256: str | None,
                   index_sha256: str | None, logical_index_sha256: str | None) -> tuple[dict[str, int], object, object]:
    if set(meta) != _META_KEYS or any(type(value) is not str for value in meta.values()):
        raise _refuse()
    if meta["schema_version"] != CHECKPOINT_SCHEMA_VERSION or meta["tool"] != "shingle_dedup" or meta["method_version"] != "1":
        raise _refuse()
    expected_kind = {"inventory": "build_inventory", "build": "build_index", "batch": "batch_report"}[kind]
    if meta["checkpoint_kind"] != expected_kind or _minimal_unsigned(meta["chunk_number"]) != number:
        raise _refuse()
    if meta["config_sha256"] != config_sha256 or _HASH_RE.fullmatch(meta["checkpoint_sha256"]) is None:
        raise _refuse()
    if kind == "inventory":
        if _HASH_RE.fullmatch(meta["source_manifest_sha256"]) is None or any(meta[key] != "-" for key in ("canonical_descriptors_sha256", "index_sha256", "logical_index_sha256")):
            raise _refuse()
    elif kind == "build":
        if _HASH_RE.fullmatch(meta["source_manifest_sha256"]) is None or _HASH_RE.fullmatch(meta["canonical_descriptors_sha256"]) is None or any(meta[key] != "-" for key in ("index_sha256", "logical_index_sha256")):
            raise _refuse()
    else:
        if any(meta[key] != "-" for key in ("source_manifest_sha256", "canonical_descriptors_sha256")) or _HASH_RE.fullmatch(meta["index_sha256"]) is None or _HASH_RE.fullmatch(meta["logical_index_sha256"]) is None:
            raise _refuse()
    applicable = ((source_manifest_sha256, meta["source_manifest_sha256"]),) if kind == "inventory" else (
        ((source_manifest_sha256, meta["source_manifest_sha256"]),
         (canonical_descriptors_sha256, meta["canonical_descriptors_sha256"])) if kind == "build" else
        ((index_sha256, meta["index_sha256"]),
         (logical_index_sha256, meta["logical_index_sha256"]))
    )
    for expected, actual in applicable:
        if expected is not None and expected != actual:
            raise _refuse()
    counts = {key: _minimal_unsigned(meta[key]) for key in ("item_count", *_COUNTERS)}
    if counts["item_count"] > MAX_ITEM_COUNT:
        raise _refuse()
    if kind == "batch":
        if counts["item_count"] != counts["potential_pairs"] or counts["potential_pairs"] != counts["assessed_pairs"] + counts["unassessed_pairs"]:
            raise _refuse()
        if counts["assessed_pairs"] != sum(counts[key] for key in ("no_overlap_pairs", "below_0_35_pairs", "containment_0_35_to_0_60_pairs", "containment_at_least_0_60_pairs")):
            raise _refuse()
        if counts["reported_pairs"] != counts["containment_0_35_to_0_60_pairs"] + counts["containment_at_least_0_60_pairs"]:
            raise _refuse()
    elif any(counts[key] != 0 for key in _COUNTERS):
        raise _refuse()
    return counts, _cursor(meta["first_item"], kind), _cursor(meta["next_item"], kind)


def _validate_pair(value: object) -> None:
    if type(value) is not dict or set(value) != _PAIR_KEYS or value["pair_kind"] != "draft_stage_pair_candidate":
        raise _refuse()
    if not all(_opaque(value[key]) for key in ("query_id", "reference_id", "draft_id", "query_stage", "reference_stage")):
        raise _refuse()
    integer_keys = _PAIR_KEYS - {"pair_kind", "query_id", "reference_id", "draft_id", "query_stage", "reference_stage",
                                 "containment", "reverse_containment", "jaccard", "tier_metric",
                                 "pair_containment_direction", "overlap_tier", "query_stage_order",
                                 "reference_stage_order"}
    if not all(_integer(value[key]) for key in integer_keys):
        raise _refuse()
    if not all(type(value[key]) is int and -(2**63) <= value[key] <= 2**63 - 1
               for key in ("query_stage_order", "reference_stage_order")):
        raise _refuse()
    if (value["query_id"] == value["reference_id"] or value["query_stage"] == value["reference_stage"]
            or value["query_stage_order"] <= value["reference_stage_order"]):
        raise _refuse()
    for token_key, shingle_key in (("query_tokens", "query_shingles"),
                                   ("reference_tokens", "reference_shingles")):
        if (not 8 <= value[token_key] <= MAX_TOKENS_PER_DOCUMENT
                or not 1 <= value[shingle_key] <= MAX_SHINGLES_PER_DOCUMENT
                or value[shingle_key] > value[token_key] - 7):
            raise _refuse()
    for key in ("containment", "reverse_containment", "jaccard", "tier_metric"):
        if type(value[key]) is not float or not math.isfinite(value[key]) or not 0.0 <= value[key] <= 1.0:
            raise _refuse()
    if value["pair_containment_direction"] not in {"query_in_reference", "reference_in_query", "equal"}:
        raise _refuse()
    if value["overlap_tier"] not in {"containment_0_35_to_0_60", "containment_at_least_0_60"}:
        raise _refuse()
    fractions = (
        ("containment_numerator", "containment_denominator", "containment"),
        ("reverse_containment_numerator", "reverse_containment_denominator", "reverse_containment"),
        ("jaccard_numerator", "jaccard_denominator", "jaccard"),
        ("tier_metric_numerator", "tier_metric_denominator", "tier_metric"),
    )
    for numerator, denominator, rendered in fractions:
        if value[denominator] <= 0 or value[numerator] > value[denominator] or value[rendered] != round(value[numerator] / value[denominator], 6):
            raise _refuse()
    shared = value["shared_shingles"]
    if (value["containment_numerator"] != shared or value["reverse_containment_numerator"] != shared
            or value["jaccard_numerator"] != shared
            or value["containment_denominator"] != value["query_shingles"]
            or value["reverse_containment_denominator"] != value["reference_shingles"]
            or value["jaccard_denominator"] != value["query_shingles"] + value["reference_shingles"] - shared):
        raise _refuse()
    if shared > min(value["query_shingles"], value["reference_shingles"]):
        raise _refuse()
    query_fraction = shared / value["query_shingles"]
    reference_fraction = shared / value["reference_shingles"]
    expected_direction = "equal"
    left = shared * value["reference_shingles"]
    right = shared * value["query_shingles"]
    if left > right:
        expected_direction = "query_in_reference"
    elif right > left:
        expected_direction = "reference_in_query"
    if (value["pair_containment_direction"] != expected_direction
            or value["tier_metric"] != round(max(query_fraction, reference_fraction), 6)):
        raise _refuse()
    expected_denominator = value["query_shingles"] if expected_direction in {"query_in_reference", "equal"} else value["reference_shingles"]
    if value["tier_metric_numerator"] != shared or value["tier_metric_denominator"] != expected_denominator:
        raise _refuse()
    if shared == 0:
        raise _refuse()
    if shared * 100 < 35 * expected_denominator:
        expected_tier = "below_0_35"
    elif shared * 100 < 60 * expected_denominator:
        expected_tier = "containment_0_35_to_0_60"
    else:
        expected_tier = "containment_at_least_0_60"
    if expected_tier == "below_0_35" or value["overlap_tier"] != expected_tier:
        raise _refuse()


def _validate_snapshot(name: str, kind: str, number: int, raw: bytes, budget: _SharedVmBudget, *,
                       config_sha256: str, source_manifest_sha256: str | None = None,
                       canonical_descriptors_sha256: str | None = None, index_sha256: str | None = None,
                       logical_index_sha256: str | None = None) -> CheckpointSnapshot:
    if not hasattr(sqlite3.Connection, "deserialize") or not 0 < len(raw) <= MAX_SHARD_BYTES:
        raise _refuse()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        budget.install(connection)
        connection.deserialize(raw)
        _configure_read(connection, budget)
        if connection.execute("PRAGMA application_id").fetchone() != (CHECKPOINT_APPLICATION_ID,) or connection.execute("PRAGMA user_version").fetchone() != (CHECKPOINT_USER_VERSION,):
            raise _refuse()
        if connection.execute("PRAGMA encoding").fetchone() != ("UTF-8",) or connection.execute("PRAGMA page_size").fetchone() != (4096,):
            raise _refuse()
        if connection.execute("PRAGMA journal_mode").fetchone() != ("memory",):
            raise _refuse()
        page_count = connection.execute("PRAGMA page_count").fetchone()
        if (page_count is None or type(page_count[0]) is not int or page_count[0] < 1
                or page_count[0] * 4096 != len(raw)):
            raise _refuse()
        objects = {(row[0], row[1]): row[2] for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")}
        if objects != _expected_objects(kind):
            raise _refuse()
        meta_rows = tuple(connection.execute("SELECT key,value FROM checkpoint_meta ORDER BY key COLLATE BINARY"))
        if len(meta_rows) != len(_META_KEYS) or any(type(key) is not str or type(value) is not str for key, value in meta_rows):
            raise _refuse()
        meta = dict(meta_rows)
        counts, _first, _next = _validate_meta(meta, kind, number, config_sha256=config_sha256,
                                              source_manifest_sha256=source_manifest_sha256,
                                              canonical_descriptors_sha256=canonical_descriptors_sha256,
                                              index_sha256=index_sha256, logical_index_sha256=logical_index_sha256)
        inventory_rows: tuple[tuple[Any, ...], ...] = ()
        document_rows: tuple[tuple[Any, ...], ...] = ()
        posting_rows: tuple[tuple[Any, ...], ...] = ()
        pair_rows: tuple[tuple[int, bytes, bytes], ...] = ()
        digest = hashlib.sha256()
        header = {key: value for key, value in meta.items() if key != "checkpoint_sha256"}
        digest.update(_canonical({"domain": "setec-shingle-checkpoint-logical-v1", "meta": header, "record": "header"}))
        if kind == "inventory":
            inventory_rows = tuple(connection.execute("SELECT doc_id,draft_id,stage,stage_order,content_sha256 FROM inventory ORDER BY doc_id COLLATE BINARY"))
            if len(inventory_rows) != counts["item_count"]:
                raise _refuse()
            for doc_id, draft_id, stage, stage_order, content_sha in inventory_rows:
                if not all(_opaque(item) for item in (doc_id, draft_id, stage)) or type(stage_order) is not int or not isinstance(content_sha, bytes) or len(content_sha) != 32:
                    raise _refuse()
                digest.update(_canonical({"content_sha256": content_sha.hex(), "doc_id": doc_id, "draft_id": draft_id,
                                          "record": "descriptor", "stage": stage, "stage_order": stage_order}))
        elif kind == "build":
            document_rows = tuple(connection.execute("SELECT doc_id,draft_id,stage,stage_order,content_sha256,token_count,shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"))
            posting_rows = tuple(connection.execute("SELECT shingle_sha256,doc_id FROM postings ORDER BY shingle_sha256,doc_id COLLATE BINARY"))
            if len(document_rows) != counts["item_count"] or len(posting_rows) > MAX_POSTINGS:
                raise _refuse()
            posting_counts: dict[str, int] = {row[0]: 0 for row in document_rows}
            for row in document_rows:
                doc_id, draft_id, stage, stage_order, content_sha, token_count, shingle_count, status = row
                if not all(_opaque(item) for item in (doc_id, draft_id, stage)) or type(stage_order) is not int or not isinstance(content_sha, bytes) or len(content_sha) != 32:
                    raise _refuse()
                if (not _integer(token_count) or token_count > MAX_TOKENS_PER_DOCUMENT
                        or not _integer(shingle_count) or shingle_count > MAX_SHINGLES_PER_DOCUMENT
                        or shingle_count > max(0, token_count - 7)):
                    raise _refuse()
                if (status == "eligible" and (token_count < 8 or shingle_count == 0)) or (status == "too_short_unassessed" and (token_count >= 8 or shingle_count != 0)) or status not in {"eligible", "too_short_unassessed"}:
                    raise _refuse()
                digest.update(_canonical({"content_sha256": content_sha.hex(), "doc_id": doc_id, "draft_id": draft_id,
                                          "record": "document", "shingle_count": shingle_count, "stage": stage,
                                          "stage_order": stage_order, "status": status, "token_count": token_count}))
            for shingle_sha, doc_id in posting_rows:
                if not isinstance(shingle_sha, bytes) or len(shingle_sha) != 32 or doc_id not in posting_counts:
                    raise _refuse()
                posting_counts[doc_id] += 1
                digest.update(_canonical({"doc_id": doc_id, "record": "posting", "shingle_sha256": shingle_sha.hex()}))
            if any(posting_counts[row[0]] != row[6] for row in document_rows):
                raise _refuse()
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise _refuse()
        else:
            pair_rows = tuple(connection.execute("SELECT sequence,pair_json,pair_sha256 FROM pairs ORDER BY sequence"))
            if len(pair_rows) != counts["reported_pairs"] or [row[0] for row in pair_rows] != list(range(len(pair_rows))):
                raise _refuse()
            for sequence, pair_json, pair_sha in pair_rows:
                if not isinstance(pair_json, bytes) or not isinstance(pair_sha, bytes) or len(pair_sha) != 32 or hashlib.sha256(pair_json).digest() != pair_sha:
                    raise _refuse()
                try:
                    pair = _strict_json(pair_json.decode("utf-8"))
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    raise _refuse() from None
                if _canonical(pair) != pair_json:
                    raise _refuse()
                _validate_pair(pair)
                digest.update(_canonical({"pair_json_sha256": pair_sha.hex(), "record": "pair", "sequence": sequence}))
        if digest.hexdigest() != meta["checkpoint_sha256"]:
            raise _refuse()
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise _refuse()
        return CheckpointSnapshot(name, kind, number, raw, dict(meta), inventory_rows,
                                  document_rows, posting_rows, pair_rows)
    except (sqlite3.Error, OSError, TypeError, ValueError, KeyError, UnicodeError):
        raise _refuse() from None
    finally:
        if connection is not None:
            connection.close()


def _validate_sequence(mode: str, snapshots: Sequence[CheckpointSnapshot]) -> tuple[list[CheckpointSnapshot], Mapping[str, str]]:
    ordered: list[CheckpointSnapshot] = []
    continuations: dict[str, str] = {}
    kinds = ("inventory", "build") if mode == "build" else ("batch",)
    for kind in kinds:
        shards = sorted((item for item in snapshots if item.kind == kind), key=lambda item: item.chunk_number)
        if [item.chunk_number for item in shards] != list(range(len(shards))):
            raise _refuse()
        for index, shard in enumerate(shards):
            count = _minimal_unsigned(shard.meta["item_count"])
            first = _cursor(shard.meta["first_item"], kind)
            next_item = _cursor(shard.meta["next_item"], kind)
            terminal = index + 1 == len(shards)
            if not terminal and (count != MAX_ITEM_COUNT or next_item is None or shard.meta["next_item"] != shards[index + 1].meta["first_item"]):
                raise _refuse()
            if terminal and next_item is not None:
                if count != MAX_ITEM_COUNT:
                    raise _refuse()
                continuations[kind] = shard.meta["next_item"]
            if count == 0:
                if kind != "batch" or len(shards) != 1 or first is not None or next_item is not None:
                    raise _refuse()
            elif first is None or not 1 <= count <= MAX_ITEM_COUNT:
                raise _refuse()
            if kind in {"inventory", "build"} and count:
                rows = shard.inventory_rows if kind == "inventory" else shard.document_rows
                actual_first = _canonical({"doc_id": rows[0][0]}).decode("ascii").strip()
                if shard.meta["first_item"] != actual_first:
                    raise _refuse()
        ordered.extend(shards)
    if mode == "build" and snapshots and (not any(item.kind == "inventory" for item in snapshots)):
        raise _refuse()
    if mode == "build":
        inventory_rows = [row for item in ordered for row in item.inventory_rows]
        document_rows = [row for item in ordered for row in item.document_rows]
        if len(inventory_rows) > MAX_DESCRIPTORS or len(document_rows) > MAX_DESCRIPTORS:
            raise _refuse()
        inventory_by_id: dict[str, tuple[Any, ...]] = {}
        seen_inventory_stage: set[tuple[str, str]] = set()
        seen_inventory_order: set[tuple[str, int]] = set()
        for row in inventory_rows:
            if (row[0] in inventory_by_id or (row[1], row[2]) in seen_inventory_stage
                    or (row[1], row[3]) in seen_inventory_order):
                raise _refuse()
            inventory_by_id[row[0]] = row
            seen_inventory_stage.add((row[1], row[2])); seen_inventory_order.add((row[1], row[3]))
        seen_documents: set[str] = set()
        total_tokens = 0
        for row in document_rows:
            inventory = inventory_by_id.get(row[0])
            if row[0] in seen_documents or inventory is None or tuple(row[:5]) != tuple(inventory):
                raise _refuse()
            seen_documents.add(row[0])
            total_tokens += row[5]
            if total_tokens > MAX_TOTAL_TOKENS:
                raise _refuse()
        if sum(len(item.posting_rows) for item in ordered) > MAX_POSTINGS:
            raise _refuse()
        inventory_ids = [row[0] for row in inventory_rows]
        document_ids = [row[0] for row in document_rows]
        if document_ids != inventory_ids[:len(document_ids)]:
            raise _refuse()
        inventory_continuation = continuations.get("inventory")
        build_continuation = continuations.get("build")
        if inventory_continuation is not None and document_rows:
            raise _refuse()
        if document_rows:
            if build_continuation is None and len(document_rows) != len(inventory_rows):
                raise _refuse()
            if build_continuation is not None:
                if len(document_rows) >= len(inventory_rows):
                    raise _refuse()
                expected = _canonical({"doc_id": inventory_rows[len(document_rows)][0]}).decode("ascii").strip()
                if build_continuation != expected:
                    raise _refuse()
    else:
        if sum(_minimal_unsigned(item.meta["potential_pairs"]) for item in ordered) > MAX_POTENTIAL_PAIRS:
            raise _refuse()
        if sum(_minimal_unsigned(item.meta["reported_pairs"]) for item in ordered) > MAX_REPORTED_PAIRS:
            raise _refuse()
    return ordered, continuations


def _encode_checkpoint(kind: str, meta: Mapping[str, str], *,
                       inventory_rows: Iterable[Sequence[Any]], document_rows: Iterable[Sequence[Any]],
                       posting_rows: Iterable[Sequence[Any]], pairs: Iterable[Mapping[str, Any]]) -> tuple[bytes, dict[str, str]]:
    values = dict(meta)
    if set(values) != _META_KEYS - {"checkpoint_sha256"} or any(type(item) is not str for item in values.values()):
        raise _refuse()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        _SharedVmBudget().install(connection)
        connection.execute("PRAGMA encoding='UTF-8'")
        connection.execute("PRAGMA page_size=4096")
        if connection.execute("PRAGMA journal_mode=MEMORY").fetchone() != ("memory",):
            raise _refuse()
        connection.execute("PRAGMA cache_size=-16384")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise _refuse()
        connection.execute(f"PRAGMA application_id={CHECKPOINT_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={CHECKPOINT_USER_VERSION}")
        connection.execute(_META_SQL)
        rows_for_seal: list[bytes] = []
        if kind == "inventory":
            connection.execute(_INVENTORY_SQL)
            rows = [tuple(row) for row in inventory_rows]
            connection.executemany("INSERT INTO inventory VALUES(?,?,?,?,?)", rows)
            for row in sorted(rows, key=lambda item: str(item[0]).encode("utf-8")):
                rows_for_seal.append(_canonical({"content_sha256": bytes(row[4]).hex(), "doc_id": row[0], "draft_id": row[1],
                                                 "record": "descriptor", "stage": row[2], "stage_order": row[3]}))
        elif kind == "build":
            connection.execute(_DOCUMENTS_SQL); connection.execute(_POSTINGS_SQL); connection.execute(_LOOKUP_SQL)
            docs = [tuple(row) for row in document_rows]; posts = [tuple(row) for row in posting_rows]
            connection.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", docs)
            connection.executemany("INSERT INTO postings VALUES(?,?)", posts)
            for row in sorted(docs, key=lambda item: str(item[0]).encode("utf-8")):
                rows_for_seal.append(_canonical({"content_sha256": bytes(row[4]).hex(), "doc_id": row[0], "draft_id": row[1],
                                                 "record": "document", "shingle_count": row[6], "stage": row[2],
                                                 "stage_order": row[3], "status": row[7], "token_count": row[5]}))
            for shingle_sha, doc_id in sorted(posts, key=lambda item: (bytes(item[0]), str(item[1]).encode("utf-8"))):
                rows_for_seal.append(_canonical({"doc_id": doc_id, "record": "posting", "shingle_sha256": bytes(shingle_sha).hex()}))
        else:
            connection.execute(_PAIRS_SQL)
            pair_values = list(pairs)
            for sequence, pair in enumerate(pair_values):
                pair_json = _canonical(pair); pair_sha = hashlib.sha256(pair_json).digest()
                connection.execute("INSERT INTO pairs VALUES(?,?,?)", (sequence, pair_json, pair_sha))
                rows_for_seal.append(_canonical({"pair_json_sha256": pair_sha.hex(), "record": "pair", "sequence": sequence}))
        digest = hashlib.sha256()
        digest.update(_canonical({"domain": "setec-shingle-checkpoint-logical-v1", "meta": values, "record": "header"}))
        for row in rows_for_seal: digest.update(row)
        values["checkpoint_sha256"] = digest.hexdigest()
        connection.executemany("INSERT INTO checkpoint_meta VALUES(?,?)", sorted(values.items()))
        connection.commit()
        raw = connection.serialize()
        if not isinstance(raw, bytes) or len(raw) > MAX_SHARD_BYTES:
            raise _refuse()
        return raw, values
    except (sqlite3.Error, OSError, TypeError, ValueError, KeyError, UnicodeError):
        raise _refuse() from None
    finally:
        if connection is not None: connection.close()


__all__ = ["CheckpointDirectory", "CheckpointRefusal", "CheckpointSnapshot", "CheckpointState"]
