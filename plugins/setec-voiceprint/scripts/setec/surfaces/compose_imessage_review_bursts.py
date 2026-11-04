#!/usr/bin/env python3
"""Compose validated atomic iMessage rows into private review bursts.

Owner-adjudicated identity exclusions (adjudicated-identity-exclusions.json in
the source run) are rejected from corpus ingestion here: an adjudicated row
never contributes text to any burst, acts as a hard burst boundary like an
acquisition exclusion, and is accounted for under the explicit
adjudicated_excluded_rows / adjudicated_excluded_words conservation category.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows import/CLI jobs
    fcntl = None  # type: ignore[assignment]

import acquire_imessage_sent_atomic as atomic


TOOL_NAME = "compose_imessage_review_bursts"
TOOL_VERSION = "1.1"
DEFAULT_GAP_MINUTES = 30
DEFAULT_TARGET_WORDS = 300
DEFAULT_MIN_REVIEW_WORDS = 20
MAX_STATE_BYTES = 16 * 1024 * 1024
MANIFEST_FILENAME = "burst-manifest.jsonl"
HELD_FILENAME = "held-sources.json"
CHECKPOINT_FILENAME = "checkpoint.json"
CHECKPOINT_NEXT_FILENAME = ".checkpoint.json.next"
RECEIPT_FILENAME = "review-burst-receipt.json"


class ReviewBurstError(ValueError):
    """The source or private review package violated the closed contract."""


@dataclass(frozen=True)
class BurstConfig:
    gap_minutes: int = DEFAULT_GAP_MINUTES
    target_words: int = DEFAULT_TARGET_WORDS
    min_review_words: int = DEFAULT_MIN_REVIEW_WORDS

    def __post_init__(self) -> None:
        for name, value in (
            ("gap minutes", self.gap_minutes),
            ("target words", self.target_words),
            ("minimum review words", self.min_review_words),
        ):
            if type(value) is not int or value <= 0:
                raise ReviewBurstError(f"{name} must be a positive integer")

    def payload(self) -> dict[str, Any]:
        return {
            "schema": "setec-imessage-review-burst-config/1",
            "gap_minutes": self.gap_minutes,
            "target_words": self.target_words,
            "min_review_words": self.min_review_words,
            "separator_hex": "0a0a",
        }


@dataclass(frozen=True)
class RetainedSourceRow:
    source_index: int
    source_ordinal: str
    entry_locator: str
    text_bytes: bytes
    content_sha256: str
    word_count: int
    unix_nanoseconds: int
    local_date: str
    group_status: str
    group_locator: str


@dataclass(frozen=True)
class SourceEvent:
    retained: RetainedSourceRow | None
    exclusion_reason: str | None
    adjudicated: bool = False

    def __post_init__(self) -> None:
        if (self.retained is None) == (self.exclusion_reason is None):
            raise ReviewBurstError("source event must be retained or excluded")
        if type(self.adjudicated) is not bool or (
            self.adjudicated and self.retained is None
        ):
            raise ReviewBurstError(
                "adjudicated source event must bind its retained row"
            )


@dataclass(frozen=True)
class PlannedBurst:
    index: int
    burst_id: str
    members: tuple[RetainedSourceRow, ...]
    text_bytes: bytes
    metadata: dict[str, Any]
    text_filename: str
    metadata_filename: str
    text_sha256: str
    metadata_bytes: bytes
    metadata_sha256: str


def _sha256_tag(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json(payload: object) -> bytes:
    def validate(item: object) -> None:
        if item is None or type(item) in {bool, int, str}:
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ReviewBurstError(
                        "canonical JSON object key is not a string"
                    )
                validate(child)
            return
        raise ReviewBurstError("value is outside the canonical JSON domain")

    try:
        validate(payload)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    except (RecursionError, UnicodeEncodeError) as exc:
        raise ReviewBurstError("value cannot be canonically encoded") from exc


def _canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ReviewBurstError(f"{label} has duplicate keys")
            value[key] = item
        return value

    try:
        payload = json.loads(raw, object_pairs_hook=closed_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReviewBurstError(f"{label} is unreadable") from exc
    try:
        canonical = _canonical_json(payload)
    except (ReviewBurstError, RecursionError) as exc:
        raise ReviewBurstError(f"{label} is unreadable") from exc
    if type(payload) is not dict or canonical != raw:
        raise ReviewBurstError(f"{label} is not canonical")
    return payload


class _ValidatedSourceView:
    """Capture the exact source view approved by the atomic validator."""

    def __init__(self, reader: atomic._PrivateReadOnlyRowIo) -> None:
        self.root = reader.root
        self._reader = reader
        self._bytes: dict[str, tuple[bytes, str]] = {}
        self._root_names: tuple[str, ...] | None = None
        self._exists: dict[str, bool] = {}
        self._directories: dict[str, tuple[str, ...]] = {}
        self._frozen = False

    def root_names(self) -> tuple[str, ...]:
        if self._root_names is None:
            if self._frozen:
                raise ReviewBurstError("post-validation source inventory was not approved")
            self._root_names = self._reader.root_names()
        return self._root_names

    def exists(self, relative: str) -> bool:
        if relative not in self._exists:
            if self._frozen:
                raise ReviewBurstError("post-validation source path was not approved")
            self._exists[relative] = self._reader.exists(relative)
        return self._exists[relative]

    def list_directory(self, relative: str) -> tuple[str, ...]:
        if relative not in self._directories:
            if self._frozen:
                raise ReviewBurstError("post-validation source directory was not approved")
            self._directories[relative] = self._reader.list_directory(relative)
        return self._directories[relative]

    def read_bytes(self, relative: str, label: str) -> bytes:
        if relative not in self._bytes:
            if self._frozen:
                raise ReviewBurstError("post-validation source artifact was not approved")
            self._bytes[relative] = (self._reader.read_bytes(relative, label), label)
        return self._bytes[relative][0]

    def freeze(self) -> None:
        self._frozen = True

    def verify_unchanged(self) -> None:
        """Refuse drift between validator return and the captured composition view."""

        try:
            if (
                self._root_names is not None
                and self._reader.root_names() != self._root_names
            ):
                raise ReviewBurstError("source inventory changed after validation")
            for relative, expected in self._exists.items():
                if self._reader.exists(relative) is not expected:
                    raise ReviewBurstError("source path changed after validation")
            for relative, expected in self._directories.items():
                if self._reader.list_directory(relative) != expected:
                    raise ReviewBurstError("source directory changed after validation")
            for relative, (expected, label) in self._bytes.items():
                if self._reader.read_bytes(relative, label) != expected:
                    raise ReviewBurstError("source artifact changed after validation")
        except ReviewBurstError:
            raise
        except (atomic.AtomicAcquisitionError, OSError) as exc:
            raise ReviewBurstError("source changed after validation") from exc


def _safe_name(value: object, label: str) -> str:
    if type(value) is not str:
        raise ReviewBurstError(f"{label} is invalid")
    try:
        if "\x00" in value:
            raise ValueError("NUL is not a filesystem name character")
        value.encode(sys.getfilesystemencoding(), errors="strict")
        return atomic._bootstrap_basename(value, label)
    except (atomic.AtomicAcquisitionError, UnicodeError, ValueError) as exc:
        raise ReviewBurstError(f"{label} is invalid") from exc


def _read_private_bytes_at(
    parent_fd: int,
    filename: str,
    *,
    expected: bytes | None = None,
    max_bytes: int | None = None,
    label: str,
) -> bytes:
    filename = _safe_name(filename, f"{label} filename")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        named_before = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or atomic._private_node_identity(before)
            != atomic._private_node_identity(named_before)
        ):
            raise ReviewBurstError(f"{label} inode is invalid")
        ceiling = max_bytes if max_bytes is not None else before.st_size
        if ceiling < 0 or before.st_size > ceiling:
            raise ReviewBurstError(f"{label} exceeds its size bound")
        chunks: list[bytes] = []
        remaining = ceiling + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        named_after = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if (
            atomic._private_node_identity(before)
            != atomic._private_node_identity(after)
            or atomic._private_node_identity(after)
            != atomic._private_node_identity(named_after)
        ):
            raise ReviewBurstError(f"{label} changed while being read")
        if expected is not None and raw != expected:
            raise ReviewBurstError(f"{label} bytes drifted")
        if max_bytes is not None and len(raw) > max_bytes:
            raise ReviewBurstError(f"{label} exceeds its size bound")
        return raw
    except ReviewBurstError:
        raise
    except OSError as exc:
        raise ReviewBurstError(f"cannot read {label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _publish_resumable_file_at(
    parent_fd: int,
    filename: str,
    raw: bytes,
    *,
    label: str,
    fault: Callable[[str], None] | None = None,
    copying_fault_boundary: str | None = None,
) -> str:
    """Create one exact file through a deterministic prefix-resumable temporary."""

    filename = _safe_name(filename, f"{label} filename")
    temporary = _safe_name(f".{filename}.copying", f"{label} temporary")
    try:
        final_info = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        final_info = None
    except OSError as exc:
        raise ReviewBurstError(f"cannot inspect {label}") from exc
    try:
        temporary_info = os.stat(
            temporary, dir_fd=parent_fd, follow_symlinks=False
        )
    except FileNotFoundError:
        temporary_info = None
    except OSError as exc:
        raise ReviewBurstError(f"cannot inspect partial {label}") from exc

    if final_info is not None:
        if temporary_info is not None:
            raise ReviewBurstError(f"{label} publication is ambiguous")
        _read_private_bytes_at(parent_fd, filename, expected=raw, label=label)
        return _sha256_tag(raw)

    descriptor: int | None = None
    try:
        if temporary_info is None:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            prefix = b""
        else:
            prefix = _read_private_bytes_at(
                parent_fd,
                temporary,
                max_bytes=len(raw),
                label=f"partial {label}",
            )
            if not raw.startswith(prefix):
                raise ReviewBurstError(f"partial {label} is not an exact prefix")
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_APPEND
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        opened = os.fstat(descriptor)
        named = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or atomic._private_node_identity(opened)
            != atomic._private_node_identity(named)
        ):
            raise ReviewBurstError(f"partial {label} inode is invalid")
        view = memoryview(raw)[len(prefix) :]
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise ReviewBurstError(f"partial {label} write was incomplete")
            view = view[count:]
        os.fsync(descriptor)
    except ReviewBurstError:
        raise
    except OSError as exc:
        raise ReviewBurstError(f"cannot write {label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    _read_private_bytes_at(parent_fd, temporary, expected=raw, label=f"partial {label}")
    if fault is not None and copying_fault_boundary is not None:
        fault(copying_fault_boundary)
    try:
        atomic._macos_rename_exclusive_at(parent_fd, temporary, filename)
        os.fsync(parent_fd)
    except atomic.BootstrapStateError as exc:
        raise ReviewBurstError(f"cannot exclusively publish {label}") from exc
    _read_private_bytes_at(parent_fd, filename, expected=raw, label=label)
    return _sha256_tag(raw)


def _object_validator(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        raise atomic.BootstrapStateError("review-burst state root is invalid")
    return value


def _read_state_at(
    parent_fd: int,
    filename: str,
    label: str,
) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload, _identity, digest, raw = atomic._read_private_canonical_json_at(
            parent_fd,
            filename,
            max_bytes=MAX_STATE_BYTES,
            validator=_object_validator,
            artifact_label=label,
        )
    except (atomic.AtomicAcquisitionError, OSError) as exc:
        raise ReviewBurstError(f"cannot read {label}") from exc
    return payload, raw, digest


def _checkpoint_next_raw(payload: dict[str, Any]) -> bytes:
    raw = _canonical_json(payload)
    _canonical_object(raw, "review-burst checkpoint")
    if len(raw) > MAX_STATE_BYTES:
        raise ReviewBurstError("review-burst checkpoint exceeds its size bound")
    return raw


def _checkpoint_residue_names() -> set[str]:
    return {
        CHECKPOINT_NEXT_FILENAME,
        f".{CHECKPOINT_NEXT_FILENAME}.copying",
    }


def _cleanup_committed_checkpoint_residue_at(
    parent_fd: int,
    checkpoint: dict[str, Any],
) -> None:
    """Discard only a byte-valid predecessor retained by a completed swap."""

    names = set(os.listdir(parent_fd))
    next_copy = f".{CHECKPOINT_NEXT_FILENAME}.copying"
    if CHECKPOINT_NEXT_FILENAME not in names:
        return
    if next_copy in names:
        raise ReviewBurstError("checkpoint transaction residue is ambiguous")
    predecessor, _raw, _digest = _read_state_at(
        parent_fd, CHECKPOINT_NEXT_FILENAME, "checkpoint predecessor"
    )
    fingerprint = checkpoint.get("source_config_fingerprint")
    burst_count = checkpoint.get("burst_count")
    closed = checkpoint.get("closed_bursts")
    if type(fingerprint) is not str or type(burst_count) is not int:
        return
    predecessor_closed: int | None = None
    if checkpoint.get("complete") is False and type(closed) is int and closed > 0:
        predecessor_closed = closed - 1
    elif checkpoint.get("complete") is True and closed == burst_count:
        predecessor_closed = burst_count
    if predecessor_closed is None:
        return
    expected_predecessor = _checkpoint_payload(
        source_config_fingerprint=fingerprint,
        burst_count=burst_count,
        closed_bursts=predecessor_closed,
        complete=False,
    )
    if predecessor != expected_predecessor:
        return
    try:
        os.unlink(CHECKPOINT_NEXT_FILENAME, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        raise ReviewBurstError("cannot clean committed checkpoint predecessor") from exc


def _advance_checkpoint_at(
    parent_fd: int,
    expected: dict[str, Any],
    desired: dict[str, Any],
    *,
    fault: Callable[[str], None] | None = None,
) -> str:
    """Advance through one deterministic swap recoverable at every boundary."""

    current, _current_raw, _current_digest = _read_state_at(
        parent_fd, CHECKPOINT_FILENAME, "review-burst checkpoint"
    )
    desired_raw = _checkpoint_next_raw(desired)
    if current == desired:
        _cleanup_committed_checkpoint_residue_at(parent_fd, current)
        return _sha256_tag(desired_raw)
    if current != expected:
        raise ReviewBurstError("review-burst checkpoint compare-and-swap failed")
    _publish_resumable_file_at(
        parent_fd,
        CHECKPOINT_NEXT_FILENAME,
        desired_raw,
        label="next review-burst checkpoint",
    )
    if fault is not None:
        fault("checkpoint_after_next")
    successor, _successor_raw, _successor_digest = _read_state_at(
        parent_fd, CHECKPOINT_NEXT_FILENAME, "next review-burst checkpoint"
    )
    if successor != desired:
        raise ReviewBurstError("next review-burst checkpoint binding drifted")
    try:
        atomic._macos_swap_names_at(
            parent_fd, CHECKPOINT_NEXT_FILENAME, CHECKPOINT_FILENAME
        )
        os.fsync(parent_fd)
    except atomic.BootstrapStateError as exc:
        raise ReviewBurstError("cannot swap review-burst checkpoint") from exc
    if fault is not None:
        fault("checkpoint_after_swap")
    published, _published_raw, published_digest = _read_state_at(
        parent_fd, CHECKPOINT_FILENAME, "review-burst checkpoint"
    )
    predecessor, _predecessor_raw, _predecessor_digest = _read_state_at(
        parent_fd, CHECKPOINT_NEXT_FILENAME, "checkpoint predecessor"
    )
    if published != desired or predecessor != expected:
        raise ReviewBurstError("review-burst checkpoint swap drifted")
    try:
        os.unlink(CHECKPOINT_NEXT_FILENAME, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        raise ReviewBurstError("cannot retire checkpoint predecessor") from exc
    return published_digest


def _preflight_source(reader: atomic._PrivateReadOnlyRowIo) -> tuple[bytes, bytes]:
    checkpoint_raw = reader.read_bytes(CHECKPOINT_FILENAME, "source checkpoint")
    checkpoint = _canonical_object(checkpoint_raw, "source checkpoint")
    if checkpoint.get("schema") != "setec-imessage-atomic-checkpoint/2" \
            or checkpoint.get("complete") is not True:
        raise ReviewBurstError("source checkpoint is not complete")
    receipt_raw = reader.read_bytes(
        "acquisition-receipt.json", "source acquisition receipt"
    )
    receipt = _canonical_object(receipt_raw, "source acquisition receipt")
    if (
        receipt.get("schema") != "setec-imessage-atomic-acquisition-receipt/2"
        or receipt.get("full_universe_eligibility_closure") is not True
    ):
        raise ReviewBurstError("source receipt lacks full-universe closure")
    return checkpoint_raw, receipt_raw


def _verify_source_aggregate_bindings(
    *,
    checkpoint_raw: bytes,
    receipt_raw: bytes,
    ledger_raw: bytes,
    holds_raw: bytes,
) -> None:
    """Bind captured aggregate bytes to the producer's shipped schemas."""

    checkpoint = _canonical_object(checkpoint_raw, "source checkpoint")
    receipt = _canonical_object(receipt_raw, "source acquisition receipt")
    ledger_digest = _sha256_tag(ledger_raw)
    hold_digest = _sha256_tag(holds_raw)
    if (
        checkpoint.get("ledger_sha256") != ledger_digest
        or receipt.get("ledger_sha256") != ledger_digest
        or checkpoint.get("source_hold_ledger_hash") != hold_digest
        or receipt.get("source_hold_ledger_hash") != hold_digest
    ):
        raise ReviewBurstError("source aggregates are not bound to the approved closure")


def _load_source_events(
    reader: atomic._PrivateReadOnlyRowIo,
) -> tuple[
    tuple[SourceEvent, ...],
    dict[str, Any],
    bytes,
    dict[str, Any],
    bytes,
    bytes | None,
]:
    ledger_raw = reader.read_bytes("source-ledger.json", "source ledger")
    ledger = _canonical_object(ledger_raw, "source ledger")
    holds_raw = reader.read_bytes(
        atomic.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
        "private source hold ledger",
    )
    holds = _canonical_object(holds_raw, "private source hold ledger")
    rows = ledger.get("rows")
    if (
        ledger.get("schema") != "setec-imessage-atomic-source-ledger/2"
        or ledger.get("complete") is not True
        or ledger.get("not_considered_after_bound") != 0
        or type(rows) is not list
    ):
        raise ReviewBurstError("source ledger is not fully closed")

    events: list[SourceEvent] = []
    seen_locators: set[str] = set()
    stem_by_event_index: dict[int, str] = {}
    for index, ledger_row in enumerate(rows):
        if type(ledger_row) is not dict:
            raise ReviewBurstError("source ledger row is invalid")
        disposition = ledger_row.get("disposition")
        locator = ledger_row.get("entry_locator")
        if type(locator) is not str or locator in seen_locators:
            raise ReviewBurstError("source ledger locator is invalid or duplicated")
        seen_locators.add(locator)
        if disposition != "retained":
            if disposition not in atomic.EXCLUSION_REASONS:
                raise ReviewBurstError("source ledger disposition is invalid")
            events.append(SourceEvent(None, disposition))
            continue
        stem = ledger_row.get("row_stem")
        if type(stem) is not str:
            raise ReviewBurstError("retained source row stem is invalid")
        source_ordinal = ledger_row.get("source_ordinal")
        if type(source_ordinal) is not str:
            raise ReviewBurstError("retained source row ordinal is invalid")
        text_raw = reader.read_bytes(
            f"rows/{stem}/{stem}.txt", "retained source text"
        )
        sidecar_raw = reader.read_bytes(
            f"rows/{stem}/{stem}.meta.json", "retained source sidecar"
        )
        sidecar = _canonical_object(sidecar_raw, "retained source sidecar")
        try:
            text = text_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewBurstError("retained source text is not UTF-8") from exc
        word_count = len(text.split())
        group_locator = sidecar.get("author_corpus_group_locator")
        if (
            ledger_row.get("content_sha256") != _sha256_tag(text_raw)
            or ledger_row.get("word_count") != word_count
            or sidecar.get("content_hash") != _sha256_tag(text_raw)
            or sidecar.get("word_count") != word_count
            or sidecar.get("author_corpus_entry_locator") != locator
            or type(group_locator) is not str
            or type(sidecar.get("unix_nanoseconds")) is not int
            or type(sidecar.get("local_date")) is not str
            or sidecar.get("group_status") not in {
                atomic.GROUP_STATUS_DIRECT,
                atomic.GROUP_STATUS_GROUP,
            }
        ):
            raise ReviewBurstError("retained source row binding drifted")
        stem_by_event_index[len(events)] = stem
        events.append(SourceEvent(RetainedSourceRow(
            source_index=index,
            source_ordinal=source_ordinal,
            entry_locator=locator,
            text_bytes=text_raw,
            content_sha256=_sha256_tag(text_raw),
            word_count=word_count,
            unix_nanoseconds=sidecar["unix_nanoseconds"],
            local_date=sidecar["local_date"],
            group_status=sidecar["group_status"],
            group_locator=group_locator,
        ), None))
    if ledger.get("retained_rows") != sum(event.retained is not None for event in events):
        raise ReviewBurstError("retained source row count drifted")
    adjudicated_raw: bytes | None = None
    if atomic.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME in reader.root_names():
        try:
            adjudicated, adjudicated_raw = atomic._read_io_object(
                reader,
                atomic.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME,
                "adjudicated identity exclusions",
                max_bytes=atomic.MAX_ADJUDICATED_IDENTITY_EXCLUSIONS_BYTES,
            )
            adjudicated_stems = atomic._validated_adjudicated_identity_exclusions(
                adjudicated,
                retained_row_stems=set(stem_by_event_index.values()),
            )
        except atomic.AtomicAcquisitionError as exc:
            raise ReviewBurstError(
                "source adjudicated identity exclusions are invalid"
            ) from exc
        for index, stem in stem_by_event_index.items():
            if stem in adjudicated_stems:
                events[index] = SourceEvent(
                    events[index].retained, None, adjudicated=True
                )
    return tuple(events), ledger, ledger_raw, holds, holds_raw, adjudicated_raw


def build_bursts(
    events: Sequence[SourceEvent],
    config: BurstConfig,
) -> tuple[PlannedBurst, ...]:
    """Build deterministic bursts while treating exclusions as hard boundaries.

    Owner-adjudicated rows are rejected from the corpus here: their text never
    enters a burst, and — like acquisition exclusions — they close the current
    burst so rows on either side are not misrepresented as contiguous.
    """

    if type(config) is not BurstConfig:
        raise ReviewBurstError("burst config is invalid")
    groups: list[tuple[RetainedSourceRow, ...]] = []
    current: list[RetainedSourceRow] = []
    current_words = 0
    gap_ns = config.gap_minutes * 60 * 1_000_000_000

    def close_current() -> None:
        nonlocal current, current_words
        if current:
            groups.append(tuple(current))
            current = []
            current_words = 0

    previous_source_index = -1
    previous_retained: RetainedSourceRow | None = None
    for event in events:
        if event.retained is None:
            close_current()
            previous_source_index += 1
            continue
        row = event.retained
        if row.source_index <= previous_source_index:
            raise ReviewBurstError("source event order is not strictly increasing")
        previous_source_index = row.source_index
        if (
            previous_retained is not None
            and row.unix_nanoseconds < previous_retained.unix_nanoseconds
        ):
            raise ReviewBurstError("source event timestamp precedes its predecessor")
        previous_retained = row
        if event.adjudicated:
            close_current()
            continue
        if current:
            previous = current[-1]
            gap = row.unix_nanoseconds - previous.unix_nanoseconds
            compatible = (
                gap <= gap_ns
                and row.group_locator == previous.group_locator
                and row.group_status == previous.group_status
                and row.local_date == previous.local_date
                and current_words + row.word_count <= config.target_words
            )
            if not compatible:
                close_current()
        current.append(row)
        current_words += row.word_count
    close_current()

    planned: list[PlannedBurst] = []
    for index, members in enumerate(groups, start=1):
        burst_id = f"burst-{index:06d}"
        text_raw = b"\n\n".join(member.text_bytes for member in members)
        word_count = sum(member.word_count for member in members)
        separator_bytes = 2 * (len(members) - 1)
        try:
            joined_words = len(text_raw.decode("utf-8").split())
        except UnicodeDecodeError as exc:
            raise ReviewBurstError("planned burst text is not UTF-8") from exc
        if joined_words != word_count:
            raise ReviewBurstError("planned burst word conservation drifted")
        metadata = {
            "schema": "setec-imessage-review-burst-meta/1",
            "burst_id": burst_id,
            "content_sha256": _sha256_tag(text_raw),
            "byte_size": len(text_raw),
            "word_count": word_count,
            "member_count": len(members),
            "separator": {"hex": "0a0a", "inserted_bytes": separator_bytes},
            "first_unix_nanoseconds": members[0].unix_nanoseconds,
            "last_unix_nanoseconds": members[-1].unix_nanoseconds,
            "local_date": members[0].local_date,
            "group_status": members[0].group_status,
            "group_locator": members[0].group_locator,
            "too_short_review": word_count < config.min_review_words,
            "oversized_singleton": (
                len(members) == 1 and word_count > config.target_words
            ),
            "members": [
                {
                    "source_index": member.source_index,
                    "source_ordinal": member.source_ordinal,
                    "entry_locator": member.entry_locator,
                    "content_sha256": member.content_sha256,
                    "byte_size": len(member.text_bytes),
                    "word_count": member.word_count,
                    "unix_nanoseconds": member.unix_nanoseconds,
                }
                for member in members
            ],
        }
        metadata_raw = _canonical_json(metadata)
        planned.append(PlannedBurst(
            index=index,
            burst_id=burst_id,
            members=members,
            text_bytes=text_raw,
            metadata=metadata,
            text_filename=f"{burst_id}.txt",
            metadata_filename=f"{burst_id}.meta.json",
            text_sha256=_sha256_tag(text_raw),
            metadata_bytes=metadata_raw,
            metadata_sha256=_sha256_tag(metadata_raw),
        ))
    return tuple(planned)


def _manifest_bytes(bursts: Sequence[PlannedBurst]) -> bytes:
    return b"".join(_canonical_json({
        "schema": "setec-imessage-review-burst-manifest-entry/1",
        "burst_id": burst.burst_id,
        "text_path": burst.text_filename,
        "metadata_path": burst.metadata_filename,
        "content_sha256": burst.text_sha256,
        "metadata_sha256": burst.metadata_sha256,
        "word_count": burst.metadata["word_count"],
        "member_count": burst.metadata["member_count"],
        "too_short_review": burst.metadata["too_short_review"],
    }) for burst in bursts)


def _held_output_payload(source_holds: dict[str, Any]) -> dict[str, Any]:
    holds = source_holds.get("holds")
    if type(holds) is not list:
        raise ReviewBurstError("source hold ledger is invalid")
    return {
        "schema": "setec-imessage-review-burst-held-sources/1",
        "held_missing_chat_join_rows": source_holds.get(
            "held_missing_chat_join_rows"
        ),
        "selected_held_missing_chat_join_rows": source_holds.get(
            "selected_held_missing_chat_join_rows"
        ),
        "holds": holds,
    }


def _conservation_payload(
    events: Sequence[SourceEvent],
    bursts: Sequence[PlannedBurst],
    source_holds: dict[str, Any],
) -> dict[str, Any]:
    retained = [event.retained for event in events if event.retained is not None]
    adjudicated = [
        event.retained
        for event in events
        if event.retained is not None and event.adjudicated
    ]
    composed = [
        event.retained
        for event in events
        if event.retained is not None and not event.adjudicated
    ]
    members = [member for burst in bursts for member in burst.members]
    composed_locators = [row.entry_locator for row in composed]
    member_locators = [row.entry_locator for row in members]
    source_words = sum(row.word_count for row in retained)
    adjudicated_words = sum(row.word_count for row in adjudicated)
    burst_words = sum(int(burst.metadata["word_count"]) for burst in bursts)
    excluded = sum(event.exclusion_reason is not None for event in events)
    inserted = sum(int(burst.metadata["separator"]["inserted_bytes"]) for burst in bursts)
    holds = source_holds.get("holds")
    if (
        composed_locators != member_locators
        or len(member_locators) != len(set(member_locators))
        or source_words != burst_words + adjudicated_words
        or type(holds) is not list
    ):
        raise ReviewBurstError("review-burst conservation failed")
    return {
        "source_retained_rows": len(retained),
        "burst_member_rows": len(members),
        "source_retained_words": source_words,
        "burst_member_words": burst_words,
        "adjudicated_excluded_rows": len(adjudicated),
        "adjudicated_excluded_words": adjudicated_words,
        "excluded_selected_eligible_rows": excluded,
        "held_rows": len(holds),
        "inserted_separator_bytes": inserted,
        "unique_member_locators": len(set(member_locators)),
    }


def _checkpoint_payload(
    *,
    source_config_fingerprint: str,
    burst_count: int,
    closed_bursts: int,
    complete: bool,
    conservation: dict[str, Any] | None = None,
    manifest_sha256: str | None = None,
    held_sources_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": "setec-imessage-review-burst-checkpoint/1",
        "source_config_fingerprint": source_config_fingerprint,
        "burst_count": burst_count,
        "closed_bursts": closed_bursts,
        "complete": complete,
        "conservation": conservation,
        "manifest_sha256": manifest_sha256,
        "held_sources_sha256": held_sources_sha256,
    }


def _journal_payload(
    *,
    source_config_fingerprint: str,
    staging_name: str,
    final_name: str,
    config: BurstConfig,
) -> dict[str, Any]:
    return {
        "schema": "setec-imessage-review-burst-journal/1",
        "source_config_fingerprint": source_config_fingerprint,
        "staging_name": staging_name,
        "final_name": final_name,
        "config": config.payload(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }


def _receipt_payload(
    *,
    source_config_fingerprint: str,
    config: BurstConfig,
    bursts: Sequence[PlannedBurst],
    conservation: dict[str, Any],
    manifest_raw: bytes,
    held_raw: bytes,
    checkpoint_raw: bytes,
) -> dict[str, Any]:
    tree_entries = []
    for burst in bursts:
        tree_entries.extend((
            {
                "path": burst.text_filename,
                "sha256": burst.text_sha256,
                "byte_size": len(burst.text_bytes),
            },
            {
                "path": burst.metadata_filename,
                "sha256": burst.metadata_sha256,
                "byte_size": len(burst.metadata_bytes),
            },
        ))
    for path, raw in (
        (MANIFEST_FILENAME, manifest_raw),
        (HELD_FILENAME, held_raw),
        (CHECKPOINT_FILENAME, checkpoint_raw),
    ):
        tree_entries.append({
            "path": path,
            "sha256": _sha256_tag(raw),
            "byte_size": len(raw),
        })
    tree_raw = _canonical_json({
        "schema": "setec-imessage-review-burst-tree/1",
        "entries": sorted(tree_entries, key=lambda item: item["path"]),
    })
    counts = {
        "bursts": len(bursts),
        "too_short_review": sum(
            burst.metadata["too_short_review"] is True for burst in bursts
        ),
        "oversized_singletons": sum(
            burst.metadata["oversized_singleton"] is True for burst in bursts
        ),
        **conservation,
    }
    return {
        "schema": "setec-imessage-review-burst-receipt/1",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "source_config_fingerprint": source_config_fingerprint,
        "config": config.payload(),
        "counts": counts,
        "manifest_sha256": _sha256_tag(manifest_raw),
        "held_sources_sha256": _sha256_tag(held_raw),
        "checkpoint_sha256": _sha256_tag(checkpoint_raw),
        "package_tree_sha256": _sha256_tag(tree_raw),
        "privacy": {
            "contains_member_locators": False,
            "contains_source_paths": False,
            "contains_source_prose": False,
        },
    }


def _assert_outward_privacy(payload: dict[str, Any]) -> None:
    forbidden = {
        "entry_locator", "group_locator", "source_ordinal", "members", "row_stem",
    }

    def visit(value: object) -> None:
        if type(value) is dict:
            if forbidden & set(value):
                raise ReviewBurstError("outward receipt contains private member identity")
            for item in value.values():
                visit(item)
        elif type(value) is list:
            for item in value:
                visit(item)

    visit(payload)


def _source_config_fingerprint(
    *,
    checkpoint_raw: bytes,
    receipt_raw: bytes,
    ledger_raw: bytes,
    holds_raw: bytes,
    adjudicated_raw: bytes | None,
    config: BurstConfig,
) -> str:
    payload = {
        "schema": "setec-imessage-review-burst-source-config/2",
        "source_checkpoint_sha256": _sha256_tag(checkpoint_raw),
        "source_receipt_sha256": _sha256_tag(receipt_raw),
        "source_ledger_sha256": _sha256_tag(ledger_raw),
        "source_hold_ledger_sha256": _sha256_tag(holds_raw),
        "source_adjudicated_exclusions_sha256": (
            _sha256_tag(adjudicated_raw) if adjudicated_raw is not None else None
        ),
        "config": config.payload(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }
    return _sha256_tag(_canonical_json(payload))


def _package_expected_files(
    bursts: Sequence[PlannedBurst],
    manifest_raw: bytes,
    held_raw: bytes,
    checkpoint_raw: bytes,
    receipt_raw: bytes,
) -> dict[str, bytes]:
    expected: dict[str, bytes] = {}
    for burst in bursts:
        expected[burst.text_filename] = burst.text_bytes
        expected[burst.metadata_filename] = burst.metadata_bytes
    expected.update({
        MANIFEST_FILENAME: manifest_raw,
        HELD_FILENAME: held_raw,
        CHECKPOINT_FILENAME: checkpoint_raw,
        RECEIPT_FILENAME: receipt_raw,
    })
    return expected


def _validate_complete_package_at(
    directory_fd: int,
    expected: dict[str, bytes],
) -> None:
    names, _identity = atomic._stable_private_directory_names(
        directory_fd,
        owner_uid=os.getuid(),
        ops=atomic._PrivateTreeOsOps(),
        label="review-burst package",
    )
    if names != tuple(sorted(expected, key=os.fsencode)):
        raise ReviewBurstError("review-burst package inventory drifted")
    for name, raw in expected.items():
        _read_private_bytes_at(
            directory_fd,
            name,
            expected=raw,
            label="review-burst artifact",
        )


def _acquire_package_lock(output_fd: int, lock_name: str) -> int:
    if fcntl is None:
        raise ReviewBurstError("review-burst locking requires macOS")
    lock_name = _safe_name(lock_name, "review-burst lock")
    flags = (
        os.O_RDWR
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=output_fd
            )
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            os.fsync(output_fd)
        except FileExistsError:
            descriptor = os.open(lock_name, flags, dir_fd=output_fd)
        opened = os.fstat(descriptor)
        named = os.stat(lock_name, dir_fd=output_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or atomic._private_node_identity(opened)
            != atomic._private_node_identity(named)
        ):
            raise ReviewBurstError("review-burst lock inode is invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ReviewBurstError("review-burst package is active elsewhere") from exc
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _verify_named_private_directory(
    parent_fd: int,
    name: str,
    directory_fd: int,
    *,
    label: str,
) -> None:
    try:
        opened = os.fstat(directory_fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise ReviewBurstError(f"cannot verify {label}") from exc
    for info in (opened, named):
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ReviewBurstError(f"{label} inode is invalid")
    if atomic._private_node_identity(opened)[:2] != atomic._private_node_identity(
        named
    )[:2]:
        raise ReviewBurstError(f"{label} pathname identity drifted")


def compose_review_bursts(
    input_run: Path,
    output_root: Path,
    package_id: str,
    *,
    config: BurstConfig = BurstConfig(),
    resume: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
    fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Validate one source run and durably publish its review-burst package."""

    if sys.platform != "darwin" or os.name == "nt":
        raise ReviewBurstError("private review-burst production requires macOS")
    if type(resume) is not bool or type(config) is not BurstConfig:
        raise ReviewBurstError("review-burst invocation is invalid")
    final_name = _safe_name(package_id, "package ID")
    staging_name = _safe_name(
        f".{final_name}.review-burst-staging", "staging name"
    )
    journal_name = _safe_name(
        f".{final_name}.review-burst-journal.json", "journal name"
    )
    journal_copying_name = _safe_name(
        f".{journal_name}.copying", "journal copying name"
    )
    lock_name = _safe_name(f".{final_name}.review-burst.lock", "lock name")
    source_root = Path(input_run).expanduser().absolute()
    destination_root = Path(output_root).expanduser().absolute()
    try:
        source_resolved = source_root.resolve(strict=True)
        destination_resolved = destination_root.resolve(strict=True)
        destination_resolved.relative_to(source_resolved)
    except ValueError:
        pass
    except OSError as exc:
        raise ReviewBurstError("cannot resolve source or output root") from exc
    else:
        raise ReviewBurstError("review-burst output root must remain outside the source run")

    reader = atomic._PrivateReadOnlyRowIo(source_root)
    source_view = _ValidatedSourceView(reader)
    output_parent_fd: int | None = None
    output_fd: int | None = None
    staging_fd: int | None = None
    lock_fd: int | None = None
    try:
        checkpoint_source_raw, receipt_source_raw = _preflight_source(source_view)
        try:
            source_summary = atomic.validate_atomic_run(
                source_root, io=source_view
            )
        except atomic.AtomicAcquisitionError as exc:
            raise ReviewBurstError("source atomic run validation failed") from exc
        source_view.freeze()
        events, ledger, ledger_raw, source_holds, source_holds_raw, adjudicated_raw = (
            _load_source_events(source_view)
        )
        retained_rows = sum(event.retained is not None for event in events)
        adjudicated_rows = sum(event.adjudicated for event in events)
        if source_summary.get("identity_scan") != {
            "scanned_txt_rows": retained_rows - adjudicated_rows,
            "adjudicated_excluded_txt_rows": adjudicated_rows,
            "adjudicated_exclusions_sha256": (
                _sha256_tag(adjudicated_raw)
                if adjudicated_raw is not None
                else None
            ),
        }:
            raise ReviewBurstError("source adjudication binding drifted")
        _verify_source_aggregate_bindings(
            checkpoint_raw=checkpoint_source_raw,
            receipt_raw=receipt_source_raw,
            ledger_raw=ledger_raw,
            holds_raw=source_holds_raw,
        )
        source_view.verify_unchanged()
        bursts = build_bursts(events, config)
        conservation = _conservation_payload(events, bursts, source_holds)
        manifest_raw = _manifest_bytes(bursts)
        held_raw = _canonical_json(_held_output_payload(source_holds))
        fingerprint = _source_config_fingerprint(
            checkpoint_raw=checkpoint_source_raw,
            receipt_raw=receipt_source_raw,
            ledger_raw=ledger_raw,
            holds_raw=source_holds_raw,
            adjudicated_raw=adjudicated_raw,
            config=config,
        )
        complete_checkpoint = _checkpoint_payload(
            source_config_fingerprint=fingerprint,
            burst_count=len(bursts),
            closed_bursts=len(bursts),
            complete=True,
            conservation=conservation,
            manifest_sha256=_sha256_tag(manifest_raw),
            held_sources_sha256=_sha256_tag(held_raw),
        )
        complete_checkpoint_raw = _canonical_json(complete_checkpoint)
        receipt = _receipt_payload(
            source_config_fingerprint=fingerprint,
            config=config,
            bursts=bursts,
            conservation=conservation,
            manifest_raw=manifest_raw,
            held_raw=held_raw,
            checkpoint_raw=complete_checkpoint_raw,
        )
        _assert_outward_privacy(receipt)
        receipt_raw = _canonical_json(receipt)
        expected_complete = _package_expected_files(
            bursts,
            manifest_raw,
            held_raw,
            complete_checkpoint_raw,
            receipt_raw,
        )

        output_parent_fd, output_name = atomic._open_private_parent_dirfd(
            destination_root
        )
        output_fd, _output_identity = atomic._open_private_tree_node_at(
            output_parent_fd,
            output_name,
            kind="directory",
            owner_uid=os.getuid(),
            ops=atomic._PrivateTreeOsOps(),
            label="review-burst output root",
        )
        _verify_named_private_directory(
            output_parent_fd,
            output_name,
            output_fd,
            label="review-burst output root",
        )
        lock_fd = _acquire_package_lock(output_fd, lock_name)
        root_names = set(os.listdir(output_fd))
        final_exists = final_name in root_names
        staging_exists = staging_name in root_names
        journal_exists = journal_name in root_names
        journal_copying_exists = journal_copying_name in root_names
        journal_expected = _journal_payload(
            source_config_fingerprint=fingerprint,
            staging_name=staging_name,
            final_name=final_name,
            config=config,
        )
        journal_expected_raw = _canonical_json(journal_expected)

        if final_exists:
            if (
                not resume
                or staging_exists
                or not journal_exists
                or journal_copying_exists
            ):
                raise ReviewBurstError("completed review-burst package requires exact resume")
            journal, _journal_raw, _journal_digest = _read_state_at(
                output_fd, journal_name, "review-burst journal"
            )
            if journal != journal_expected:
                raise ReviewBurstError("review-burst journal binding drifted")
            final_fd, _final_identity = atomic._open_private_tree_node_at(
                output_fd,
                final_name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=atomic._PrivateTreeOsOps(),
                label="completed review-burst package",
            )
            try:
                _validate_complete_package_at(final_fd, expected_complete)
            finally:
                os.close(final_fd)
            return receipt

        existing_state = staging_exists or journal_exists or journal_copying_exists
        if existing_state and not resume:
            raise ReviewBurstError("existing review-burst state requires --resume")
        if staging_exists and journal_exists and not journal_copying_exists:
            journal, _journal_raw, _journal_digest = _read_state_at(
                output_fd, journal_name, "review-burst journal"
            )
            if journal != journal_expected:
                raise ReviewBurstError("review-burst journal binding drifted")
            staging_fd, _staging_identity = atomic._open_private_tree_node_at(
                output_fd,
                staging_name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=atomic._PrivateTreeOsOps(),
                label="review-burst staging",
            )
        elif journal_exists and not staging_exists and not journal_copying_exists:
            journal, _journal_raw, _journal_digest = _read_state_at(
                output_fd, journal_name, "review-burst journal"
            )
            if journal != journal_expected:
                raise ReviewBurstError("review-burst journal binding drifted")
            if fault is not None:
                fault("after_journal")
            staging_fd, _staging_identity = atomic._create_private_staging_at(
                output_fd, staging_name
            )
        elif journal_copying_exists and not journal_exists and not staging_exists:
            journal_copying_raw = _read_private_bytes_at(
                output_fd,
                journal_copying_name,
                max_bytes=len(journal_expected_raw),
                label="partial review-burst journal",
            )
            if journal_copying_raw != journal_expected_raw:
                raise ReviewBurstError(
                    "partial review-burst journal is incomplete or binding drifted"
                )
            _publish_resumable_file_at(
                output_fd,
                journal_name,
                journal_expected_raw,
                label="review-burst journal",
                fault=fault,
                copying_fault_boundary="journal_copying",
            )
            journal, _journal_raw, _journal_digest = _read_state_at(
                output_fd, journal_name, "review-burst journal"
            )
            if journal != journal_expected:
                raise ReviewBurstError("review-burst journal binding drifted")
            if fault is not None:
                fault("after_journal")
            staging_fd, _staging_identity = atomic._create_private_staging_at(
                output_fd, staging_name
            )
        elif not existing_state:
            _publish_resumable_file_at(
                output_fd,
                journal_name,
                journal_expected_raw,
                label="review-burst journal",
                fault=fault,
                copying_fault_boundary="journal_copying",
            )
            if fault is not None:
                fault("after_journal")
            staging_fd, _staging_identity = atomic._create_private_staging_at(
                output_fd, staging_name
            )
        else:
            raise ReviewBurstError("review-burst staging and journal state is ambiguous")
        _verify_named_private_directory(
            output_fd,
            staging_name,
            staging_fd,
            label="review-burst staging",
        )

        checkpoint_digest: str | None
        staging_names = set(os.listdir(staging_fd))
        if CHECKPOINT_FILENAME in staging_names:
            checkpoint, _checkpoint_raw, _checkpoint_digest = _read_state_at(
                staging_fd, CHECKPOINT_FILENAME, "review-burst checkpoint"
            )
            if (
                checkpoint.get("schema")
                != "setec-imessage-review-burst-checkpoint/1"
                or checkpoint.get("source_config_fingerprint") != fingerprint
                or checkpoint.get("burst_count") != len(bursts)
                or type(checkpoint.get("closed_bursts")) is not int
                or not 0 <= checkpoint["closed_bursts"] <= len(bursts)
            ):
                raise ReviewBurstError("review-burst checkpoint binding drifted")
            _cleanup_committed_checkpoint_residue_at(staging_fd, checkpoint)
        else:
            initial = _checkpoint_payload(
                source_config_fingerprint=fingerprint,
                burst_count=len(bursts),
                closed_bursts=0,
                complete=False,
            )
            _publish_resumable_file_at(
                staging_fd,
                CHECKPOINT_FILENAME,
                _checkpoint_next_raw(initial),
                label="review-burst checkpoint",
            )
            checkpoint = initial
        if checkpoint.get("complete") is True:
            if checkpoint != complete_checkpoint:
                raise ReviewBurstError("completed review-burst checkpoint drifted")
            closed = len(bursts)
        elif (
            checkpoint.get("complete") is not False
            or any(
                checkpoint.get(name) is not None
                for name in ("conservation", "manifest_sha256", "held_sources_sha256")
            )
        ):
            raise ReviewBurstError("incomplete review-burst checkpoint drifted")
        else:
            closed = checkpoint["closed_bursts"]

        allowed_names = {CHECKPOINT_FILENAME, *_checkpoint_residue_names()}
        for burst in bursts[:closed]:
            allowed_names.update((burst.text_filename, burst.metadata_filename))
        next_names: set[str] = set()
        if closed < len(bursts):
            next_burst = bursts[closed]
            next_names.update((
                next_burst.text_filename,
                f".{next_burst.text_filename}.copying",
                next_burst.metadata_filename,
                f".{next_burst.metadata_filename}.copying",
            ))
        final_names = {
            MANIFEST_FILENAME,
            f".{MANIFEST_FILENAME}.copying",
            HELD_FILENAME,
            f".{HELD_FILENAME}.copying",
            RECEIPT_FILENAME,
            f".{RECEIPT_FILENAME}.copying",
        }
        unexpected = set(os.listdir(staging_fd)) - allowed_names - next_names - final_names
        if unexpected:
            raise ReviewBurstError("review-burst staging contains foreign residue")

        for burst in bursts[closed:]:
            _verify_named_private_directory(
                output_fd,
                staging_name,
                staging_fd,
                label="review-burst staging",
            )
            _publish_resumable_file_at(
                staging_fd,
                burst.text_filename,
                burst.text_bytes,
                label="review-burst text",
            )
            if fault is not None:
                fault("after_text")
            _publish_resumable_file_at(
                staging_fd,
                burst.metadata_filename,
                burst.metadata_bytes,
                label="review-burst metadata",
            )
            if fault is not None:
                fault("after_metadata")
            next_checkpoint = _checkpoint_payload(
                source_config_fingerprint=fingerprint,
                burst_count=len(bursts),
                closed_bursts=burst.index,
                complete=False,
            )
            _advance_checkpoint_at(
                staging_fd,
                checkpoint,
                next_checkpoint,
                fault=fault,
            )
            checkpoint = next_checkpoint
            if progress is not None:
                progress({
                    "event": "burst_closed",
                    "closed_bursts": burst.index,
                    "burst_count": len(bursts),
                })
            if fault is not None:
                fault("after_checkpoint")

        _verify_named_private_directory(
            output_fd,
            staging_name,
            staging_fd,
            label="review-burst staging",
        )
        _publish_resumable_file_at(
            staging_fd, MANIFEST_FILENAME, manifest_raw, label="burst manifest"
        )
        _publish_resumable_file_at(
            staging_fd, HELD_FILENAME, held_raw, label="held-source ledger"
        )
        current_checkpoint, _raw, _current_digest = _read_state_at(
            staging_fd, CHECKPOINT_FILENAME, "review-burst checkpoint"
        )
        if current_checkpoint != complete_checkpoint:
            if (
                current_checkpoint.get("complete") is not False
                or current_checkpoint.get("closed_bursts") != len(bursts)
            ):
                raise ReviewBurstError("review-burst finalization checkpoint drifted")
            _advance_checkpoint_at(
                staging_fd,
                current_checkpoint,
                complete_checkpoint,
                fault=fault,
            )
        _publish_resumable_file_at(
            staging_fd, RECEIPT_FILENAME, receipt_raw, label="review-burst receipt"
        )
        _validate_complete_package_at(staging_fd, expected_complete)
        if fault is not None:
            fault("before_promote")
        _verify_named_private_directory(
            output_fd,
            staging_name,
            staging_fd,
            label="review-burst staging",
        )
        try:
            atomic._macos_rename_exclusive_at(output_fd, staging_name, final_name)
            os.fsync(output_fd)
        except atomic.BootstrapStateError as exc:
            raise ReviewBurstError("cannot exclusively promote review-burst package") from exc
        final_info = os.stat(final_name, dir_fd=output_fd, follow_symlinks=False)
        if atomic._private_node_identity(final_info)[:2] != atomic._private_node_identity(
            os.fstat(staging_fd)
        )[:2]:
            raise ReviewBurstError("promoted review-burst package identity drifted")
        _validate_complete_package_at(staging_fd, expected_complete)
        return receipt
    finally:
        reader.close()
        if staging_fd is not None:
            os.close(staging_fd)
        if lock_fd is not None:
            try:
                assert fcntl is not None
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if output_fd is not None:
            os.close(output_fd)
        if output_parent_fd is not None:
            os.close(output_parent_fd)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compose one validated atomic iMessage run into review bursts."
    )
    parser.add_argument("--input-run", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--gap-minutes", type=int, default=DEFAULT_GAP_MINUTES)
    parser.add_argument("--target-words", type=int, default=DEFAULT_TARGET_WORDS)
    parser.add_argument(
        "--min-review-words", type=int, default=DEFAULT_MIN_REVIEW_WORDS
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        config = BurstConfig(
            gap_minutes=args.gap_minutes,
            target_words=args.target_words,
            min_review_words=args.min_review_words,
        )

        def progress(payload: dict[str, Any]) -> None:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))

        receipt = compose_review_bursts(
            args.input_run,
            args.output_root,
            args.package_id,
            config=config,
            resume=args.resume,
            progress=progress,
        )
    except (ReviewBurstError, atomic.AtomicAcquisitionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
