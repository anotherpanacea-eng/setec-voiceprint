#!/usr/bin/env python3
"""Transactional, private acquisition of one authorship row per sent message."""

from __future__ import annotations

import argparse
from collections import Counter
import datetime as _dt
from dataclasses import asdict, dataclass, replace
import errno
import hashlib
import hmac
import json
import os
import secrets
import unicodedata
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TOOL_NAME = "acquire_imessage_sent_atomic"
TOOL_VERSION = "1.0"
CAPABILITY_ID = "imessage_sent_atomic"
TASK_SURFACE = "voice_coherence_acquisition"
APPLE_UNIX_EPOCH_SECONDS = 978_307_200
NANOSECONDS_PER_SECOND = 1_000_000_000
AI_BOUNDARY_DATE = _dt.date(2024, 7, 1)
AI_BOUNDARY_VERSION = "imessage-ai-boundary-v1"

GROUP_STATUS_GROUP = "group"
GROUP_STATUS_DIRECT = "direct"
GROUP_STATUS_UNKNOWN = "unknown"

_KEY_ID_DOMAIN = b"setec-author-corpus-hmac-key-id-v1"
_GROUP_LOCATOR_DOMAIN = b"setec-imessage-atomic-chat-v1"
_ENTRY_LOCATOR_DOMAIN = b"setec-imessage-atomic-entry-v1"
_UNIX_EPOCH_UTC = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
PRIVATE_ROOT_COMPONENT = "ai-prose-baselines-private"
SNAPSHOT_FILENAME = "source-snapshot.db"
SNAPSHOT_PARTIAL_FILENAMES = (
    SNAPSHOT_FILENAME,
    SNAPSHOT_FILENAME + "-journal",
    SNAPSHOT_FILENAME + "-shm",
    SNAPSHOT_FILENAME + "-wal",
)
MAX_HMAC_KEY_BYTES = 1024 * 1024
MAX_BOOTSTRAP_JOURNAL_BYTES = 1024 * 1024
MAX_SEMANTIC_OPTIONS_BYTES = 64 * 1024
MAX_RUN_CONTROLS_BYTES = 16 * 1024
MAX_SMOKE_POLICY_BYTES = 256 * 1024
MAX_PRIVATE_CONTACT_MAP_BYTES = 256 * 1024 * 1024
MAX_PRIVATE_SOURCE_IDENTITY_MAP_BYTES = 512 * 1024 * 1024
MAX_PRIVATE_SOURCE_HOLD_LEDGER_BYTES = 512 * 1024 * 1024
MAX_RUN_OWNER_BYTES = 64 * 1024
MAX_ROW_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_ROW_STATE_BYTES = 1024 * 1024 * 1024
MAX_LIVE_SMOKE_RECEIPT_BYTES = 64 * 1024
MAX_OFFLINE_APPROVAL_JOURNAL_BYTES = 64 * 1024
MAX_OFFLINE_APPROVED_EVIDENCE_BYTES = 64 * 1024
MAX_PRIVATE_TREE_DEPTH = 64
MAX_PRIVATE_TREE_NODES = 1_000_000
BOOTSTRAP_JOURNAL_FILENAME = "bootstrap-journal.json"
SEMANTIC_OPTIONS_FILENAME = "semantic-options.json"
RUN_CONTROLS_FILENAME = "run-controls.json"
SMOKE_POLICY_FILENAME = "smoke-policy.json"
PRIVATE_CONTACT_MAP_FILENAME = "private-contact-map.json"
PRIVATE_SOURCE_IDENTITY_MAP_FILENAME = "private-source-identity-map.json"
PRIVATE_SOURCE_HOLD_LEDGER_FILENAME = "private-source-hold-ledger.json"
RUN_OWNER_FILENAME = "run-owner.json"
OFFLINE_APPROVED_EVIDENCE_FILENAME = 'offline-approved-evidence.json'
CHAT_JOIN_POLICY_VERSION = "imessage-chat-join-policy-v2"
ROW_JOURNAL_FILENAME = ".row-transaction.json"
ROW_STAGING_DIRNAME = ".row-staging"
ROWS_DIRNAME = "rows"
ROW_TRANSACTION_STATES = (
    "prepared",
    "staged",
    "committed_unledgered",
    "ledger_closed",
    "checkpoint_closed",
)
INITIALIZATION_DEPENDENCY_FILENAMES = (
    SEMANTIC_OPTIONS_FILENAME,
    RUN_CONTROLS_FILENAME,
    SMOKE_POLICY_FILENAME,
    PRIVATE_CONTACT_MAP_FILENAME,
    PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
    PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
)
INITIALIZATION_ARTIFACT_FILENAMES = (
    *INITIALIZATION_DEPENDENCY_FILENAMES,
    RUN_OWNER_FILENAME,
)
BOOTSTRAP_STATES = (
    "reserved",
    "staging_created",
    "snapshot_in_progress",
    "snapshot_closed",
    "universe_closed",
    "options_maps_closed",
    "owner_closed",
    "ready_to_promote",
    "promoted",
)
REPLY_LINK_COLUMN_VARIANTS = (
    "thread_originator_guid",
    "reply_to_guid",
    "associated_message_guid",
)

REQUIRED_SCHEMA_AFFINITIES = {
    "message": {
        "guid": "TEXT",
        "text": "TEXT",
        "attributedBody": "BLOB",
        "is_from_me": "INTEGER",
        "date": "INTEGER",
        "associated_message_type": "INTEGER",
        "item_type": "INTEGER",
    },
    "chat": {
        "guid": "TEXT",
        "chat_identifier": "TEXT",
        "room_name": "TEXT",
        "style": "INTEGER",
    },
    "chat_message_join": {"chat_id": "INTEGER", "message_id": "INTEGER"},
    "message_attachment_join": {
        "message_id": "INTEGER",
        "attachment_id": "INTEGER",
    },
}
OBJECT_REPLACEMENT = "\ufffc"
AUTOMATED_SYSTEM_TEMPLATES = frozenset(
    {
        "missed call",
        "call ended",
        "no answer",
        "facetime call",
        "facetime audio call",
    }
)
EXCLUSION_REASONS = (
    "unknown_group_status",
    "group_chat_excluded",
    "reaction",
    "group_action",
    "automated_system",
    "attachment_only",
    "unresolved_attributed_body",
    "missing_text",
    "empty_after_preprocess",
)


class AtomicAcquisitionError(ValueError):
    """Base error for a closed atomic-acquisition contract failure."""


class ExactTimestampError(AtomicAcquisitionError):
    """An exact integer timestamp could not be validated or represented."""


class ExplicitTimezoneError(AtomicAcquisitionError):
    """An explicit IANA timezone was missing or invalid."""


class StableGuidError(AtomicAcquisitionError):
    """A stable message or chat GUID failed validation."""


class HmacKeyError(AtomicAcquisitionError):
    """HMAC key bytes failed the acquisition key contract."""


class GroupClassificationError(AtomicAcquisitionError):
    """Group-classification inputs violated their runtime type contract."""


class SnapshotError(AtomicAcquisitionError):
    """The immutable SQLite snapshot could not be created or verified."""


class SchemaPreflightError(AtomicAcquisitionError):
    """The snapshot schema does not satisfy the closed atomic contract."""


class BootstrapStateError(AtomicAcquisitionError):
    """Bootstrap journal, durability, or transition state failed closed."""


class BootstrapRecoveryRequired(BootstrapStateError):
    """A published/ambiguous bootstrap update must retain its recovery lock."""


@dataclass(frozen=True)
class ExpectedPrivateFile:
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class ExpectedPrivateDirectory:
    children: Mapping[str, "ExpectedPrivateFile | ExpectedPrivateDirectory"]


@dataclass(frozen=True)
class PrivateNodeSeal:
    relative_path: tuple[str, ...]
    kind: str
    identity: tuple[int, int, int, int, int, int, int, int]
    byte_size: int | None
    sha256: str | None


@dataclass(frozen=True)
class PrivateTreeSeal:
    root_identity: tuple[int, int, int, int, int, int, int, int]
    nodes: tuple[PrivateNodeSeal, ...]


@dataclass(frozen=True)
class ClosedPrivateJson:
    filename: str
    label: str
    max_bytes: int
    payload: dict[str, Any]
    raw: bytes
    digest: str


@dataclass(frozen=True)
class InitializationClosure:
    artifacts: tuple[ClosedPrivateJson, ...]
    expected_tree: ExpectedPrivateDirectory
    universe_binding: dict[str, Any]

    def artifact(self, filename: str) -> ClosedPrivateJson:
        matches = [item for item in self.artifacts if item.filename == filename]
        if len(matches) != 1:
            raise BootstrapStateError("initialization artifact lookup is invalid")
        return matches[0]


@dataclass(frozen=True)
class SnapshotMetadata:
    schema: str
    file_sha256: str
    byte_size: int
    page_size: int
    page_count: int
    schema_fingerprint: str
    sqlite_user_version: int
    sqlite_application_id: int
    sqlite_library_version: str


@dataclass(frozen=True)
class ClosedSnapshotEvidence:
    metadata: SnapshotMetadata
    snapshot_identity: tuple[int, int, int, int, int, int, int, int]
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    snapshot_device_inode: tuple[int, int]
    staging_device_inode: tuple[int, int]
    inventory: tuple[str, ...]


@dataclass(frozen=True)
class PreparedSnapshotInProgress:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    staging_device_inode: tuple[int, int]


@dataclass(frozen=True)
class PreparedSnapshotClosed:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: ClosedSnapshotEvidence


@dataclass(frozen=True)
class UniverseClosedEvidence:
    snapshot_evidence: ClosedSnapshotEvidence
    schema_info: AtomicSchemaInfo
    universe: AtomicCandidateUniverse
    initialization: InitializationClosure


@dataclass(frozen=True)
class PreparedUniverseClosed:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence


@dataclass(frozen=True)
class PreparedOptionsMapsClosed:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence
    dependency_evidence: dict[str, tuple[str, bytes]]


@dataclass(frozen=True)
class PreparedOwnerClosed:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence
    initialization_evidence: dict[str, tuple[str, bytes]]


@dataclass(frozen=True)
class PreparedReadyToPromote:
    journal: dict[str, Any]
    journal_digest: str
    staging_fd: int
    staging_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence
    initialization_evidence: dict[str, tuple[str, bytes]]


@dataclass(frozen=True)
class PreparedPromoted:
    journal: dict[str, Any]
    journal_digest: str
    final_fd: int
    final_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence
    initialization_evidence: dict[str, tuple[str, bytes]]


@dataclass(frozen=True)
class VerifiedBootstrapFinalTree:
    journal: dict[str, Any]
    journal_digest: str
    final_fd: int
    final_identity: tuple[int, int, int, int, int, int, int, int]
    evidence: UniverseClosedEvidence
    initialization_evidence: dict[str, tuple[str, bytes]]


@dataclass(frozen=True)
class AtomicSchemaInfo:
    schema: str
    schema_fingerprint: str
    reply_column: str | None


@dataclass(frozen=True)
class AtomicCandidate:
    snapshot_rowid: int
    message_guid: str
    chat_guid: str
    chat_identifier: str | None
    room_name: str | None
    style: int
    group_status: str
    unix_nanoseconds: int
    local_date: _dt.date
    text: str | None
    attributed_body: bytes | None
    associated_message_type: int | None
    item_type: int | None
    reply_link: str | None
    attachment_ids: tuple[int, ...]


@dataclass(frozen=True)
class AtomicHeldSourceRow:
    """One outgoing source row withheld before prose processing."""

    snapshot_rowid: int
    message_guid: str
    unix_nanoseconds: int
    local_date: _dt.date
    reason: str


@dataclass(frozen=True)
class AtomicCandidateUniverse:
    schema: str
    candidate_outgoing_rows: int
    candidate_eligible_rows: int
    held_missing_chat_join_rows: int
    ambiguous_multi_chat_rows: int
    selected_outgoing_rows: int
    selected_eligible_rows: int
    selected_held_missing_chat_join_rows: int
    selected_ambiguous_multi_chat_rows: int
    candidates: tuple[AtomicCandidate, ...]
    selected: tuple[AtomicCandidate, ...]
    held: tuple[AtomicHeldSourceRow, ...]
    selected_held: tuple[AtomicHeldSourceRow, ...]


@dataclass(frozen=True)
class AtomicProcessedRow:
    candidate: AtomicCandidate
    disposition: str
    cleaned_text: str | None
    preprocessing_metadata: dict[str, Any] | None


@dataclass(frozen=True)
class AtomicProcessingResult:
    schema: str
    selected_outgoing_rows: int
    considered_rows: int
    not_considered_after_bound: int
    retained_rows: int
    excluded_considered_by_final_reason: dict[str, int]
    rows: tuple[AtomicProcessedRow, ...]


@dataclass(frozen=True)
class PlannedAtomicRow:
    """One deterministic retained or excluded durable-row transaction."""

    source_ordinal: str
    entry_locator: str
    disposition: str
    row_stem: str | None
    text_bytes: bytes | None
    sidecar: dict[str, Any] | None
    fragment: dict[str, Any] | None
    ledger_row: dict[str, Any]


@dataclass(frozen=True)
class AtomicRunConfig:
    source_db: Path
    output_root: Path
    run_id: str
    persona: str
    author: str
    register: str
    since: _dt.date | None
    until: _dt.date | None
    include_group_chats: bool
    apple_date_unit: str
    timezone_name: str
    max_messages: int = 250_000
    max_retained: int | None = None
    allow_empty: bool = False
    progress_interval: int = 100
    live_smoke_receipt: Path | None = None


@dataclass(frozen=True)
class OfflineApprovedImport:
    """Explicit authority for one portable, non-activating archive import."""

    approved_smoke_run: Path
    live_smoke_receipt: Path
    archive_equivalence_db: Path


@dataclass(frozen=True)
class _OfflineApprovedContext:
    approved_snapshot: Path
    approved_receipt_sha256: str
    snapshot_metadata: SnapshotMetadata
    schema_info: AtomicSchemaInfo
    universe: AtomicCandidateUniverse
    semantic_options: dict[str, Any]
    run_controls: dict[str, Any]
    initialization: InitializationClosure
    offline_evidence: ClosedPrivateJson


def apple_date_to_unix_ns(raw: int, unit: str) -> int:
    """Convert an Apple-epoch integer to integer Unix nanoseconds.

    The calculation is intentionally integer-only.  ``bool`` is rejected even
    though it is an ``int`` subclass because SQLite runtime type validation is
    expected to supply an actual integer value.
    """

    if type(raw) is not int:
        raise ExactTimestampError("message date must be an exact integer")
    if unit == "seconds":
        return (raw + APPLE_UNIX_EPOCH_SECONDS) * NANOSECONDS_PER_SECOND
    if unit == "nanoseconds":
        return raw + APPLE_UNIX_EPOCH_SECONDS * NANOSECONDS_PER_SECOND
    raise ExactTimestampError("apple date unit must be seconds or nanoseconds")


def _load_explicit_zone(timezone_name: str) -> ZoneInfo:
    if (
        type(timezone_name) is not str
        or not timezone_name
        or timezone_name != timezone_name.strip()
        or any(unicodedata.category(char) == "Cc" for char in timezone_name)
    ):
        raise ExplicitTimezoneError("an explicit IANA timezone is required")
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ExplicitTimezoneError("the explicit IANA timezone is invalid") from exc


def unix_ns_to_local_date(unix_nanoseconds: int, timezone_name: str) -> _dt.date:
    """Derive local date using an explicit IANA zone and historical rules."""

    if type(unix_nanoseconds) is not int:
        raise ExactTimestampError("Unix nanoseconds must be an exact integer")
    zone = _load_explicit_zone(timezone_name)
    seconds, nanosecond_remainder = divmod(
        unix_nanoseconds, NANOSECONDS_PER_SECOND
    )
    try:
        # datetime has microsecond precision, but discarding only the
        # sub-microsecond remainder cannot change the containing integer second
        # or its local calendar date.
        instant_utc = _UNIX_EPOCH_UTC + _dt.timedelta(
            seconds=seconds, microseconds=nanosecond_remainder // 1_000
        )
        return instant_utc.astimezone(zone).date()
    except (OverflowError, ValueError) as exc:
        raise ExactTimestampError(
            "Unix nanoseconds are outside the supported calendar range"
        ) from exc


def apple_date_to_local_date(
    raw: int, unit: str, timezone_name: str
) -> _dt.date:
    """Convert an exact Apple timestamp directly to its explicit local date."""

    return unix_ns_to_local_date(
        apple_date_to_unix_ns(raw, unit), timezone_name
    )


def validate_stable_guid(value: object, *, identity: str) -> str:
    """Return an exact stable GUID or fail without echoing its raw value."""

    if identity not in {"message", "chat"}:
        raise ValueError("identity must be message or chat")
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise StableGuidError(f"invalid stable {identity} GUID")
    return value


def _validate_hmac_key(key_bytes: bytes) -> bytes:
    if type(key_bytes) is not bytes or len(key_bytes) < 32:
        raise HmacKeyError("HMAC key must contain at least 32 bytes")
    return key_bytes


def load_hmac_key(path: Path) -> bytes:
    """Load an existing bounded owner-only key without exposing its path."""

    if os.name == "nt":
        raise HmacKeyError(
            "secure atomic HMAC key loading is available only on the macOS/POSIX host"
        )
    key_path = path.expanduser().absolute()
    if ".." in key_path.parts or PRIVATE_ROOT_COMPONENT not in key_path.parts:
        raise HmacKeyError("HMAC key private path is invalid")
    private_index = max(
        index
        for index, part in enumerate(key_path.parts)
        if part == PRIVATE_ROOT_COMPONENT
    )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    # Do not compare the current callable identity against
    # `os.supports_dir_fd`: race tests intentionally wrap `os.open` while
    # preserving its dir_fd behavior.  Missing dir_fd support still fails
    # closed at the first descriptor-relative open.
    if not nofollow or not directory_flag:
        raise HmacKeyError("host lacks secure descriptor-relative key loading")
    common_flags = nofollow | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NONBLOCK", 0
    )
    directory_descriptor: int | None = None
    key_descriptor: int | None = None
    try:
        directory_descriptor = os.open(
            key_path.parts[0], os.O_RDONLY | directory_flag | common_flags
        )
        for index, component in enumerate(key_path.parts[1:-1], start=1):
            next_descriptor = os.open(
                component,
                os.O_RDONLY | directory_flag | common_flags,
                dir_fd=directory_descriptor,
            )
            info = os.fstat(next_descriptor)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_descriptor)
                raise HmacKeyError("HMAC key path component is not a directory")
            if index >= private_index and (
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                os.close(next_descriptor)
                raise HmacKeyError("private key directory permissions are invalid")
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        filename = key_path.parts[-1]
        key_descriptor = os.open(
            filename,
            os.O_RDONLY | common_flags,
            dir_fd=directory_descriptor,
        )
        opened_before = os.fstat(key_descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise HmacKeyError("opened HMAC key is not a regular file")
        if opened_before.st_size < 32 or opened_before.st_size > MAX_HMAC_KEY_BYTES:
            raise HmacKeyError("HMAC key size is outside the allowed range")
        if os.name != "nt" and (
            opened_before.st_uid != os.getuid()
            or stat.S_IMODE(opened_before.st_mode) & 0o077
        ):
            raise HmacKeyError("HMAC key permissions or ownership are invalid")
        chunks: list[bytes] = []
        remaining = MAX_HMAC_KEY_BYTES + 1
        while remaining:
            chunk = os.read(key_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        key = b"".join(chunks)
        opened_after = os.fstat(key_descriptor)
        path_after = os.stat(
            filename, dir_fd=directory_descriptor, follow_symlinks=False
        )
    except HmacKeyError:
        raise
    except OSError as exc:
        raise HmacKeyError("cannot securely open or read HMAC key") from exc
    finally:
        if key_descriptor is not None:
            os.close(key_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)

    def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    if (
        not stat.S_ISREG(path_after.st_mode)
        or identity(opened_before) != identity(opened_after)
        or identity(opened_after) != identity(path_after)
    ):
        raise HmacKeyError("HMAC key changed while being read")
    if len(key) < 32 or len(key) > MAX_HMAC_KEY_BYTES:
        raise HmacKeyError("HMAC key size is outside the allowed range")
    return _validate_hmac_key(key)


def hmac_key_id(key_bytes: bytes) -> str:
    """Return the nonsecret fixed-formula HMAC key identifier."""

    key = _validate_hmac_key(key_bytes)
    digest = hashlib.sha256(_KEY_ID_DOMAIN + b"\x00" + key).hexdigest()
    return f"sha256:{digest}"


def load_offline_approved_hmac_key(
    path: Path,
    authorization: OfflineApprovedImport,
) -> bytes:
    """Load a stable bounded key on Windows only for an approved offline run."""

    if type(authorization) is not OfflineApprovedImport:
        raise HmacKeyError('offline HMAC authorization is invalid')
    key_path = Path(path).expanduser().absolute()
    if '..' in Path(path).expanduser().parts:
        raise HmacKeyError('offline HMAC key path is invalid')
    artifact_paths = (
        authorization.approved_smoke_run,
        authorization.live_smoke_receipt,
        authorization.archive_equivalence_db,
    )
    try:
        roots = {_private_root_path(Path(item)) for item in artifact_paths}
        key_root = _private_root_path(key_path)
    except AtomicAcquisitionError as exc:
        raise HmacKeyError('offline HMAC key private-root binding is invalid') from exc
    if len(roots) != 1 or key_root not in roots:
        raise HmacKeyError('offline HMAC key does not share the approved private root')
    try:
        resolved_root = key_root.resolve(strict=True)
        key_path.resolve(strict=True).relative_to(resolved_root)
        before = key_path.lstat()
    except (OSError, ValueError) as exc:
        raise HmacKeyError('cannot inspect offline HMAC key') from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or _is_reparse_or_symlink(key_path)
        or before.st_size < 32
        or before.st_size > MAX_HMAC_KEY_BYTES
        or (
            os.name != 'nt'
            and (before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) & 0o077)
        )
    ):
        raise HmacKeyError('offline HMAC key is not a bounded direct regular file')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(key_path, flags)
        opened_before = os.fstat(descriptor)
        chunks: list[bytes] = []
        remaining = MAX_HMAC_KEY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        opened_after = os.fstat(descriptor)
    except OSError as exc:
        raise HmacKeyError('cannot read offline HMAC key') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = key_path.lstat()
    except OSError as exc:
        raise HmacKeyError('cannot re-inspect offline HMAC key') from exc
    # Windows reports a different descriptor ``st_ctime_ns`` from the named
    # path for an unchanged file (the descriptor value can reflect access-time
    # bookkeeping). Device/inode/size/mtime are stable across lstat/fstat and
    # still prove that the same bounded bytes were read; the two path lstat
    # samples additionally catch a replacement during the read.
    identity_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    identities = (before, opened_before, opened_after, after)
    if any(
        getattr(identities[0], name) != getattr(item, name)
        for item in identities[1:]
        for name in identity_fields
    ):
        raise HmacKeyError('offline HMAC key changed while being read')
    if os.name != 'nt' and any(
        item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) & 0o077
        for item in identities
    ):
        raise HmacKeyError('offline HMAC key permissions or ownership are invalid')
    key = _validate_hmac_key(b''.join(chunks))
    approved_run = _portable_directory(
        authorization.approved_smoke_run,
        'approved smoke run',
    )
    smoke, _ = _read_io_object(
        _SyntheticFixtureRowIo(approved_run),
        SMOKE_POLICY_FILENAME,
        'approved smoke policy',
        validator=_validated_smoke_policy,
        max_bytes=MAX_SMOKE_POLICY_BYTES,
    )
    if smoke['hmac']['key_id'] != hmac_key_id(key):
        raise HmacKeyError('offline HMAC key does not match the approved run')
    return key


def group_locator(key_bytes: bytes, chat_guid: object) -> str:
    """Return the fixed-formula stable private group locator."""

    key = _validate_hmac_key(key_bytes)
    guid = validate_stable_guid(chat_guid, identity="chat")
    message = _GROUP_LOCATOR_DOMAIN + b"\x00" + guid.encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def entry_locator(key_bytes: bytes, message_guid: object) -> str:
    """Return the fixed-formula stable private message-entry locator."""

    key = _validate_hmac_key(key_bytes)
    guid = validate_stable_guid(message_guid, identity="message")
    message = _ENTRY_LOCATOR_DOMAIN + b"\x00" + guid.encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def classify_group_status(room_name: object, style: object) -> str:
    """Apply the closed group/direct/unknown precedence table.

    A nonblank TEXT room name wins regardless of the *value* of a valid integer
    style.  Otherwise only styles 43 and 45 are recognized, and every other
    integer value is unknown.  Missing or retyped fields fail closed.
    """

    if room_name is not None and type(room_name) is not str:
        raise GroupClassificationError("room_name must be TEXT or NULL")
    if type(style) is not int:
        raise GroupClassificationError("chat style must be an exact integer")
    if room_name is not None and room_name.strip():
        return GROUP_STATUS_GROUP
    if style == 43:
        return GROUP_STATUS_GROUP
    if style == 45:
        return GROUP_STATUS_DIRECT
    return GROUP_STATUS_UNKNOWN


def ai_status_for_local_date(local_date: _dt.date) -> str:
    """Apply the frozen acquisition-only AI-date posture."""

    if type(local_date) is not _dt.date:
        raise AtomicAcquisitionError("AI posture requires a local calendar date")
    if local_date < AI_BOUNDARY_DATE:
        return "pre_ai_human"
    return "unknown"


def era_for_local_date(local_date: _dt.date) -> str:
    """Map a local date to the exporter's frozen era vocabulary."""

    if type(local_date) is not _dt.date:
        raise AtomicAcquisitionError("era requires a local calendar date")
    if local_date < _dt.date(2022, 11, 1):
        return "pre_chatgpt"
    if local_date < AI_BOUNDARY_DATE:
        return "pre_ai_widespread"
    return "post_ai_widespread"


def _sha256_tag(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
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
                    raise AtomicAcquisitionError(
                        "canonical JSON object key is not a string"
                    )
                validate(child)
            return
        raise AtomicAcquisitionError("value is outside the canonical JSON domain")

    validate(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def canonical_payload_digest(value: object) -> str:
    """Hash the exact canonical JSON bytes used by semantic artifacts."""

    return _sha256_tag(_canonical_json_bytes(value))


def _canonical_preprocessing_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy preprocessing's redundant float ratio to an exact rational."""

    if type(metadata) is not dict:
        raise AtomicAcquisitionError("preprocessing metadata is not an object")
    normalized = dict(metadata)
    if "strip_ratio" in normalized:
        ratio = normalized["strip_ratio"]
        before = normalized.get("input_tokens_before")
        after = normalized.get("input_tokens_after")
        stripped = normalized.get("tokens_stripped")
        if (
            type(ratio) is not float
            or type(before) is not int
            or type(after) is not int
            or type(stripped) is not int
            or before < 0
            or after < 0
            or after > before
            or stripped != before - after
            or ratio != (stripped / before if before else 0.0)
        ):
            raise AtomicAcquisitionError(
                "preprocessing strip ratio is not bound to token counts"
            )
        normalized["strip_ratio"] = {
            "numerator": stripped,
            "denominator": before if before else 1,
        }
    return json.loads(_canonical_json_bytes(normalized))


def _is_sha256_tag(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def _binding_text(name: str, value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)
    ):
        raise AtomicAcquisitionError(f"{name} binding is invalid")
    return value


def semantic_options_payload(
    *,
    since: _dt.date | None,
    until: _dt.date | None,
    include_group_chats: bool,
    apple_date_unit: str,
    timezone_name: str,
    preprocessing_version: str,
    preprocessing_rules_id: str,
    persona: str,
    author: str,
    register: str,
) -> dict[str, Any]:
    """Build the closed semantic-options payload used by every row."""

    if since is not None and type(since) is not _dt.date:
        raise AtomicAcquisitionError("semantic since date is invalid")
    if until is not None and type(until) is not _dt.date:
        raise AtomicAcquisitionError("semantic until date is invalid")
    if since is not None and until is not None and since > until:
        raise AtomicAcquisitionError("semantic date window is reversed")
    if type(include_group_chats) is not bool:
        raise AtomicAcquisitionError("semantic group policy is invalid")
    if apple_date_unit not in {"seconds", "nanoseconds"}:
        raise AtomicAcquisitionError("semantic Apple date unit is invalid")
    _load_explicit_zone(timezone_name)
    return {
        "schema": "setec-imessage-atomic-semantic-options/1",
        "local_date_window": {
            "since": since.isoformat() if since is not None else None,
            "until": until.isoformat() if until is not None else None,
        },
        "group_policy": (
            "include_group_chats" if include_group_chats else "exclude_group_chats"
        ),
        "apple_date_unit": apple_date_unit,
        "timezone": timezone_name,
        "preprocessing": {
            "version": _binding_text(
                "preprocessing version", preprocessing_version
            ),
            "rules_id": _binding_text(
                "preprocessing rules", preprocessing_rules_id
            ),
        },
        "ai_boundary_version": AI_BOUNDARY_VERSION,
        "persona": _binding_text("persona", persona),
        "author": _binding_text("author", author),
        "register": _binding_text("register", register),
    }


def run_controls_payload(
    *,
    max_messages: int,
    max_retained: int | None,
    allow_empty: bool,
    checkpoint_schema: str,
    checkpoint_interval: int = 1,
) -> dict[str, Any]:
    """Build initialization-stable behavioral controls, excluding invocation state."""

    if type(max_messages) is not int or max_messages < 1:
        raise AtomicAcquisitionError("max_messages control is invalid")
    if max_retained is not None and (
        type(max_retained) is not int or max_retained < 1
    ):
        raise AtomicAcquisitionError("max_retained control is invalid")
    if type(allow_empty) is not bool:
        raise AtomicAcquisitionError("allow_empty control is invalid")
    if type(checkpoint_interval) is not int or checkpoint_interval != 1:
        raise AtomicAcquisitionError("checkpoint interval is frozen at exactly one row")
    return {
        "schema": "setec-imessage-atomic-run-controls/1",
        "max_messages": max_messages,
        "max_retained": max_retained,
        "allow_empty": allow_empty,
        "checkpoint_schema": _binding_text(
            "checkpoint schema", checkpoint_schema
        ),
        "checkpoint_interval": checkpoint_interval,
    }


def _validated_run_controls(payload: object) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != {
        "schema",
        "max_messages",
        "max_retained",
        "allow_empty",
        "checkpoint_schema",
        "checkpoint_interval",
    }:
        raise AtomicAcquisitionError("run controls are invalid")
    try:
        rebuilt = run_controls_payload(
            max_messages=payload.get("max_messages"),
            max_retained=payload.get("max_retained"),
            allow_empty=payload.get("allow_empty"),
            checkpoint_schema=payload.get("checkpoint_schema"),
            checkpoint_interval=payload.get("checkpoint_interval"),
        )
    except AtomicAcquisitionError as exc:
        raise AtomicAcquisitionError("run controls are invalid") from exc
    if rebuilt != payload:
        raise AtomicAcquisitionError("run controls drifted")
    return rebuilt


def _validated_semantic_options(payload: object) -> dict[str, Any]:
    if type(payload) is not dict or set(payload) != {
        "schema",
        "local_date_window",
        "group_policy",
        "apple_date_unit",
        "timezone",
        "preprocessing",
        "ai_boundary_version",
        "persona",
        "author",
        "register",
    }:
        raise AtomicAcquisitionError("smoke semantic options are invalid")
    window = payload.get("local_date_window")
    preprocessing = payload.get("preprocessing")
    if type(window) is not dict or set(window) != {"since", "until"}:
        raise AtomicAcquisitionError("smoke semantic date window is invalid")
    if type(preprocessing) is not dict or set(preprocessing) != {
        "version",
        "rules_id",
    }:
        raise AtomicAcquisitionError("smoke preprocessing binding is invalid")

    def parse_date(name: str) -> _dt.date | None:
        value = window[name]
        if value is None:
            return None
        if type(value) is not str:
            raise AtomicAcquisitionError("smoke semantic date is invalid")
        try:
            parsed = _dt.date.fromisoformat(value)
        except ValueError as exc:
            raise AtomicAcquisitionError("smoke semantic date is invalid") from exc
        if parsed.isoformat() != value:
            raise AtomicAcquisitionError("smoke semantic date is noncanonical")
        return parsed

    group_policy = payload.get("group_policy")
    if group_policy not in {"include_group_chats", "exclude_group_chats"}:
        raise AtomicAcquisitionError("smoke group policy is invalid")
    rebuilt = semantic_options_payload(
        since=parse_date("since"),
        until=parse_date("until"),
        include_group_chats=group_policy == "include_group_chats",
        apple_date_unit=payload.get("apple_date_unit"),
        timezone_name=payload.get("timezone"),
        preprocessing_version=preprocessing.get("version"),
        preprocessing_rules_id=preprocessing.get("rules_id"),
        persona=payload.get("persona"),
        author=payload.get("author"),
        register=payload.get("register"),
    )
    if rebuilt != payload or payload.get("ai_boundary_version") != AI_BOUNDARY_VERSION:
        raise AtomicAcquisitionError("smoke semantic options drifted")
    return rebuilt


def smoke_policy_payload(
    *,
    semantic_options: dict[str, Any],
    snapshot_metadata: SnapshotMetadata,
    schema_info: AtomicSchemaInfo,
    hmac_key_id_value: str,
) -> dict[str, Any]:
    """Bind semantic policy and immutable source, intentionally excluding controls."""

    semantic_validated = _validated_semantic_options(semantic_options)
    if type(snapshot_metadata) is not SnapshotMetadata:
        raise AtomicAcquisitionError("smoke snapshot metadata is invalid")
    if type(schema_info) is not AtomicSchemaInfo:
        raise AtomicAcquisitionError("smoke schema binding is invalid")

    if (
        snapshot_metadata.schema
        != "setec-imessage-atomic-snapshot-metadata/1"
        or not _is_sha256_tag(snapshot_metadata.file_sha256)
        or not _is_sha256_tag(snapshot_metadata.schema_fingerprint)
        or any(
            type(value) is not int or value < minimum
            for value, minimum in (
                (snapshot_metadata.byte_size, 1),
                (snapshot_metadata.page_size, 1),
                (snapshot_metadata.page_count, 1),
                (snapshot_metadata.sqlite_user_version, 0),
                (snapshot_metadata.sqlite_application_id, 0),
            )
        )
        or snapshot_metadata.byte_size
        != snapshot_metadata.page_size * snapshot_metadata.page_count
    ):
        raise AtomicAcquisitionError("smoke snapshot metadata fields are invalid")
    try:
        _binding_text(
            "SQLite library version", snapshot_metadata.sqlite_library_version
        )
    except AtomicAcquisitionError as exc:
        raise AtomicAcquisitionError(
            "smoke snapshot metadata fields are invalid"
        ) from exc
    if (
        schema_info.schema != "setec-imessage-atomic-schema-info/1"
        or not _is_sha256_tag(schema_info.schema_fingerprint)
        or schema_info.schema_fingerprint != snapshot_metadata.schema_fingerprint
        or (
            schema_info.reply_column is not None
            and schema_info.reply_column not in REPLY_LINK_COLUMN_VARIANTS
        )
    ):
        raise AtomicAcquisitionError("smoke schema fields or binding are invalid")
    if (
        not _is_sha256_tag(hmac_key_id_value)
    ):
        raise AtomicAcquisitionError("smoke HMAC key ID is invalid")
    # Round-trip through canonical JSON so later caller mutation cannot alias the
    # nested policy embedded in this returned payload.
    semantic_copy = json.loads(_canonical_json_bytes(semantic_validated))
    return {
        "schema": "setec-imessage-atomic-smoke-policy/2",
        "chat_join_policy_version": CHAT_JOIN_POLICY_VERSION,
        "semantic_options": semantic_copy,
        "snapshot_metadata": asdict(snapshot_metadata),
        "atomic_schema": asdict(schema_info),
        "tool": {
            "capability_id": CAPABILITY_ID,
            "name": TOOL_NAME,
            "version": TOOL_VERSION,
        },
        "hmac": {
            "algorithm": "HMAC-SHA256",
            "key_id": hmac_key_id_value,
        },
    }


def _validated_smoke_policy(payload: object) -> dict[str, Any]:
    try:
        if type(payload) is not dict or set(payload) != {
            "schema",
            "chat_join_policy_version",
            "semantic_options",
            "snapshot_metadata",
            "atomic_schema",
            "tool",
            "hmac",
        }:
            raise BootstrapStateError("smoke policy key set is invalid")
        semantic = _validated_semantic_options(payload["semantic_options"])
        snapshot_payload = _validated_bootstrap_snapshot(
            payload["snapshot_metadata"]
        )
        snapshot_metadata = SnapshotMetadata(**snapshot_payload)
        atomic_schema = payload["atomic_schema"]
        if type(atomic_schema) is not dict or set(atomic_schema) != {
            "schema",
            "schema_fingerprint",
            "reply_column",
        }:
            raise BootstrapStateError("smoke policy schema binding is invalid")
        schema_info = AtomicSchemaInfo(**atomic_schema)
        hmac_binding = payload["hmac"]
        if type(hmac_binding) is not dict or set(hmac_binding) != {
            "algorithm",
            "key_id",
        }:
            raise BootstrapStateError("smoke policy HMAC binding is invalid")
        expected = smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=snapshot_metadata,
            schema_info=schema_info,
            hmac_key_id_value=hmac_binding.get("key_id"),
        )
        if expected != payload:
            raise BootstrapStateError("smoke policy binding drifted")
        return expected
    except BootstrapStateError:
        raise
    except Exception as exc:
        raise BootstrapStateError("smoke policy schema is invalid") from exc


def run_owner_payload(
    *,
    snapshot_metadata: SnapshotMetadata,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    smoke_policy: dict[str, Any],
    hmac_key_id_value: str,
    contact_map_hash: str,
    source_identity_map_hash: str,
    source_hold_ledger_hash: str,
) -> dict[str, Any]:
    """Build the fully bound, path-free owner marker written before prose."""

    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    if type(snapshot_metadata) is not SnapshotMetadata:
        raise AtomicAcquisitionError("owner snapshot metadata is invalid")
    if type(smoke_policy) is not dict or set(smoke_policy) != {
        "schema", "chat_join_policy_version", "semantic_options", "snapshot_metadata", "atomic_schema", "tool", "hmac"
    }:
        raise AtomicAcquisitionError("owner smoke policy is invalid")
    atomic_schema = smoke_policy.get("atomic_schema")
    if type(atomic_schema) is not dict or set(atomic_schema) != {
        "schema", "schema_fingerprint", "reply_column"
    }:
        raise AtomicAcquisitionError("owner atomic schema is invalid")
    try:
        schema_info = AtomicSchemaInfo(**atomic_schema)
    except TypeError as exc:
        raise AtomicAcquisitionError("owner atomic schema is invalid") from exc
    expected_smoke = smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=snapshot_metadata,
        schema_info=schema_info,
        hmac_key_id_value=hmac_key_id_value,
    )
    if expected_smoke != smoke_policy:
        raise AtomicAcquisitionError("owner smoke policy binding drifted")
    for name, value in (
        ("owner HMAC key ID", hmac_key_id_value),
        ("owner contact map hash", contact_map_hash),
        ("owner source identity map hash", source_identity_map_hash),
        ("owner source hold ledger hash", source_hold_ledger_hash),
    ):
        if not _is_sha256_tag(value):
            raise AtomicAcquisitionError(f"{name} is invalid")
    return {
        "schema": "setec-imessage-atomic-run-owner/2",
        "capability_id": CAPABILITY_ID,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "snapshot_file_sha256": snapshot_metadata.file_sha256,
        "semantic_options_digest": canonical_payload_digest(semantic),
        "run_controls_digest": canonical_payload_digest(controls),
        "smoke_policy_digest": canonical_payload_digest(smoke_policy),
        "timezone": semantic["timezone"],
        "hmac": {"algorithm": "HMAC-SHA256", "key_id": hmac_key_id_value},
        "preprocessing": dict(semantic["preprocessing"]),
        "group_policy": semantic["group_policy"],
        "ai_boundary_version": semantic["ai_boundary_version"],
        "chat_join_policy_version": CHAT_JOIN_POLICY_VERSION,
        "contact_map_hash": contact_map_hash,
        "source_identity_map_hash": source_identity_map_hash,
        "source_hold_ledger_hash": source_hold_ledger_hash,
    }


def _bootstrap_basename(name: str, field: str) -> str:
    value = _binding_text(field, name)
    if value in {".", ".."} or Path(value).name != value or any(
        separator in value for separator in ("/", "\\")
    ):
        raise BootstrapStateError(f"bootstrap {field} is not a basename")
    return value


def bootstrap_staging_name(final_name: str) -> str:
    """Return the one run-specific sibling staging basename."""

    final = _bootstrap_basename(final_name, "final name")
    return f".{final}.bootstrap-staging"


def bootstrap_journal_name(final_name: str) -> str:
    """Return the one run-specific external journal basename."""

    final = _bootstrap_basename(final_name, "final name")
    return f".{final}.bootstrap-journal.json"


def _validated_bootstrap_snapshot(value: object) -> dict[str, Any]:
    expected = {
        "schema",
        "file_sha256",
        "byte_size",
        "page_size",
        "page_count",
        "schema_fingerprint",
        "sqlite_user_version",
        "sqlite_application_id",
        "sqlite_library_version",
    }
    if type(value) is not dict or set(value) != expected:
        raise BootstrapStateError("bootstrap snapshot binding is invalid")
    if (
        value["schema"] != "setec-imessage-atomic-snapshot-metadata/1"
        or not _is_sha256_tag(value["file_sha256"])
        or not _is_sha256_tag(value["schema_fingerprint"])
        or any(
            type(item) is not int or item < minimum
            for item, minimum in (
                (value["byte_size"], 1),
                (value["page_size"], 1),
                (value["page_count"], 1),
                (value["sqlite_user_version"], 0),
                (value["sqlite_application_id"], 0),
            )
        )
        or value["byte_size"] != value["page_size"] * value["page_count"]
    ):
        raise BootstrapStateError("bootstrap snapshot fields are invalid")
    _binding_text("bootstrap SQLite version", value["sqlite_library_version"])
    return json.loads(_canonical_json_bytes(value))


def _validated_universe_binding(value: object) -> dict[str, Any]:
    expected = {
        "candidate_outgoing_rows",
        "candidate_eligible_rows",
        "held_missing_chat_join_rows",
        "ambiguous_multi_chat_rows",
        "selected_outgoing_rows",
        "selected_eligible_rows",
        "selected_held_missing_chat_join_rows",
        "selected_ambiguous_multi_chat_rows",
        "candidate_locator_universe_hash",
        "selected_locator_universe_hash",
    }
    if type(value) is not dict or set(value) != expected:
        raise BootstrapStateError("bootstrap universe binding is invalid")
    candidate_count = value["candidate_outgoing_rows"]
    selected_count = value["selected_outgoing_rows"]
    candidate_eligible = value["candidate_eligible_rows"]
    candidate_held = value["held_missing_chat_join_rows"]
    candidate_ambiguous = value["ambiguous_multi_chat_rows"]
    selected_eligible = value["selected_eligible_rows"]
    selected_held = value["selected_held_missing_chat_join_rows"]
    selected_ambiguous = value["selected_ambiguous_multi_chat_rows"]
    if (
        type(candidate_count) is not int
        or candidate_count < 0
        or type(selected_count) is not int
        or selected_count < 0
        or selected_count > candidate_count
        or any(
            type(count) is not int or count < 0
            for count in (
                candidate_eligible, candidate_held, candidate_ambiguous,
                selected_eligible, selected_held, selected_ambiguous,
            )
        )
        or candidate_count != candidate_eligible + candidate_held + candidate_ambiguous
        or selected_count != selected_eligible + selected_held + selected_ambiguous
        or selected_eligible > candidate_eligible
        or selected_held > candidate_held
        or selected_ambiguous > candidate_ambiguous
        or candidate_ambiguous != 0
        or selected_ambiguous != 0
        or not _is_sha256_tag(value["candidate_locator_universe_hash"])
        or not _is_sha256_tag(value["selected_locator_universe_hash"])
    ):
        raise BootstrapStateError("bootstrap universe fields are invalid")
    return json.loads(_canonical_json_bytes(value))


def bootstrap_journal_payload(
    *,
    state: str,
    previous_journal_digest: str | None,
    staging_name: str,
    final_name: str,
    semantic_options_digest: str,
    run_controls_digest: str,
    smoke_policy_digest: str | None,
    hmac_key_id_value: str,
    snapshot_metadata: dict[str, Any] | None,
    universe_binding: dict[str, Any] | None,
    completed_artifacts: dict[str, str],
) -> dict[str, Any]:
    """Construct one exact external bootstrap-journal state."""

    if state not in BOOTSTRAP_STATES:
        raise BootstrapStateError("bootstrap state is unknown")
    index = BOOTSTRAP_STATES.index(state)
    if (index == 0) != (previous_journal_digest is None):
        raise BootstrapStateError("bootstrap previous-state binding is invalid")
    if previous_journal_digest is not None and not _is_sha256_tag(
        previous_journal_digest
    ):
        raise BootstrapStateError("bootstrap previous-state digest is invalid")
    staging = _bootstrap_basename(staging_name, "staging name")
    final = _bootstrap_basename(final_name, "final name")
    if staging == final:
        raise BootstrapStateError("bootstrap staging and final names collide")
    if not _is_sha256_tag(semantic_options_digest) or not _is_sha256_tag(
        run_controls_digest
    ):
        raise BootstrapStateError("bootstrap option digest is invalid")
    if not _is_sha256_tag(hmac_key_id_value):
        raise BootstrapStateError("bootstrap HMAC key ID is invalid")

    if index < BOOTSTRAP_STATES.index("snapshot_closed"):
        if snapshot_metadata is not None:
            raise BootstrapStateError("bootstrap snapshot closed too early")
        snapshot = None
    else:
        snapshot = _validated_bootstrap_snapshot(snapshot_metadata)
    if index < BOOTSTRAP_STATES.index("universe_closed"):
        if universe_binding is not None or smoke_policy_digest is not None:
            raise BootstrapStateError("bootstrap universe closed too early")
        universe = None
    else:
        universe = _validated_universe_binding(universe_binding)
        if not _is_sha256_tag(smoke_policy_digest):
            raise BootstrapStateError("bootstrap smoke digest is invalid")

    if type(completed_artifacts) is not dict or any(
        type(name) is not str
        or _bootstrap_basename(name, "artifact name") != name
        or not _is_sha256_tag(digest)
        for name, digest in completed_artifacts.items()
    ):
        raise BootstrapStateError("bootstrap artifact inventory is invalid")
    snapshot_artifacts = {SNAPSHOT_FILENAME}
    maps_artifacts = snapshot_artifacts | {
        SEMANTIC_OPTIONS_FILENAME,
        RUN_CONTROLS_FILENAME,
        SMOKE_POLICY_FILENAME,
        PRIVATE_CONTACT_MAP_FILENAME,
        PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
        PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
    }
    owner_artifacts = maps_artifacts | {RUN_OWNER_FILENAME}
    if index < BOOTSTRAP_STATES.index("snapshot_closed"):
        expected_artifacts: set[str] = set()
    elif index < BOOTSTRAP_STATES.index("options_maps_closed"):
        expected_artifacts = snapshot_artifacts
    elif index < BOOTSTRAP_STATES.index("owner_closed"):
        expected_artifacts = maps_artifacts
    else:
        expected_artifacts = owner_artifacts
    if set(completed_artifacts) != expected_artifacts:
        raise BootstrapStateError("bootstrap artifact closure is invalid")
    if snapshot is not None and completed_artifacts[SNAPSHOT_FILENAME] != snapshot[
        "file_sha256"
    ]:
        raise BootstrapStateError("bootstrap snapshot artifact binding drifted")
    if index >= BOOTSTRAP_STATES.index("options_maps_closed") and (
        completed_artifacts[SEMANTIC_OPTIONS_FILENAME] != semantic_options_digest
        or completed_artifacts[RUN_CONTROLS_FILENAME] != run_controls_digest
        or completed_artifacts[SMOKE_POLICY_FILENAME] != smoke_policy_digest
    ):
        raise BootstrapStateError("bootstrap option artifact binding drifted")
    return {
        "schema": "setec-imessage-atomic-bootstrap-journal/1",
        "state": state,
        "previous_journal_digest": previous_journal_digest,
        "staging_name": staging,
        "final_name": final,
        "semantic_options_digest": semantic_options_digest,
        "run_controls_digest": run_controls_digest,
        "smoke_policy_digest": smoke_policy_digest,
        "hmac_key_id": hmac_key_id_value,
        "snapshot_metadata": snapshot,
        "universe_binding": universe,
        "completed_artifacts": dict(sorted(completed_artifacts.items())),
    }


def validate_bootstrap_transition(
    previous: dict[str, Any], current: dict[str, Any]
) -> None:
    """Require one forward state and immutable bindings between journals."""

    previous = _validated_bootstrap_journal_payload(previous)
    current = _validated_bootstrap_journal_payload(current)
    previous_state = previous.get("state")
    current_state = current.get("state")
    if previous_state not in BOOTSTRAP_STATES or current_state not in BOOTSTRAP_STATES:
        raise BootstrapStateError("bootstrap journal state is invalid")
    if BOOTSTRAP_STATES.index(current_state) != BOOTSTRAP_STATES.index(
        previous_state
    ) + 1:
        raise BootstrapStateError("bootstrap journal transition is not sequential")
    if current.get("previous_journal_digest") != canonical_payload_digest(previous):
        raise BootstrapStateError("bootstrap journal chain digest drifted")
    immutable = {
        "schema",
        "staging_name",
        "final_name",
        "semantic_options_digest",
        "run_controls_digest",
        "hmac_key_id",
    }
    if any(previous.get(key) != current.get(key) for key in immutable):
        raise BootstrapStateError("bootstrap immutable binding drifted")
    for key in ("snapshot_metadata", "universe_binding", "smoke_policy_digest"):
        if previous.get(key) is not None and previous.get(key) != current.get(key):
            raise BootstrapStateError("bootstrap closed binding drifted")
    previous_artifacts = previous.get("completed_artifacts")
    current_artifacts = current.get("completed_artifacts")
    if type(previous_artifacts) is not dict or type(current_artifacts) is not dict:
        raise BootstrapStateError("bootstrap artifact transition is invalid")
    if any(current_artifacts.get(name) != digest for name, digest in previous_artifacts.items()):
        raise BootstrapStateError("bootstrap completed artifact drifted")


def _validated_bootstrap_journal_payload(value: object) -> dict[str, Any]:
    expected = {
        "schema",
        "state",
        "previous_journal_digest",
        "staging_name",
        "final_name",
        "semantic_options_digest",
        "run_controls_digest",
        "smoke_policy_digest",
        "hmac_key_id",
        "snapshot_metadata",
        "universe_binding",
        "completed_artifacts",
    }
    if type(value) is not dict or set(value) != expected:
        raise BootstrapStateError("bootstrap journal key set is invalid")
    if value["schema"] != "setec-imessage-atomic-bootstrap-journal/1":
        raise BootstrapStateError("bootstrap journal schema is invalid")
    rebuilt = bootstrap_journal_payload(
        state=value["state"],
        previous_journal_digest=value["previous_journal_digest"],
        staging_name=value["staging_name"],
        final_name=value["final_name"],
        semantic_options_digest=value["semantic_options_digest"],
        run_controls_digest=value["run_controls_digest"],
        smoke_policy_digest=value["smoke_policy_digest"],
        hmac_key_id_value=value["hmac_key_id"],
        snapshot_metadata=value["snapshot_metadata"],
        universe_binding=value["universe_binding"],
        completed_artifacts=value["completed_artifacts"],
    )
    if rebuilt != value:
        raise BootstrapStateError("bootstrap journal payload drifted")
    return rebuilt


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_private_parent_dirfd(path: Path) -> tuple[int, str]:
    """Pin every private path component and return its owner-only parent fd."""

    if os.name == "nt":
        raise BootstrapStateError(
            "descriptor-relative private state is unavailable on Windows"
        )
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise BootstrapStateError("bootstrap path contains parent traversal")
    absolute = expanded.absolute()
    private_indices = [
        index
        for index, component in enumerate(absolute.parts)
        if component == PRIVATE_ROOT_COMPONENT
    ]
    if not private_indices or not absolute.name:
        raise BootstrapStateError("bootstrap path is outside the private root")
    private_index = private_indices[-1]
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory_flag or os.open not in os.supports_dir_fd:
        raise BootstrapStateError("host lacks descriptor-relative bootstrap I/O")
    flags = (
        os.O_RDONLY
        | directory_flag
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    current: int | None = None
    try:
        current = os.open(absolute.parts[0], flags)
        for index, component in enumerate(absolute.parts[1:-1], start=1):
            following = os.open(component, flags, dir_fd=current)
            info = os.fstat(following)
            if not stat.S_ISDIR(info.st_mode):
                os.close(following)
                raise BootstrapStateError("bootstrap path component is not a directory")
            if index >= private_index and (
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                os.close(following)
                raise BootstrapStateError(
                    "bootstrap private directory ownership or mode is invalid"
                )
            os.close(current)
            current = following
        assert current is not None
        return current, absolute.name
    except BootstrapStateError:
        if current is not None:
            os.close(current)
        raise
    except OSError as exc:
        if current is not None:
            os.close(current)
        raise BootstrapStateError("cannot pin bootstrap private directory") from exc


def _decode_canonical_private_json(
    raw: bytes,
    *,
    max_bytes: int,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    artifact_label: str,
) -> dict[str, Any]:
    """Decode one exact canonical private JSON artifact under a closed schema."""

    if type(raw) is not bytes:
        raise BootstrapStateError(f"{artifact_label} payload is not bytes")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise BootstrapStateError(f"{artifact_label} byte ceiling is invalid")
    if not raw or len(raw) > max_bytes:
        raise BootstrapStateError(f"{artifact_label} size is invalid")

    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BootstrapStateError(f"{artifact_label} has duplicate keys")
            result[key] = value
        return result

    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=closed_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                BootstrapStateError(f"{artifact_label} has invalid constants")
            ),
        )
    except BootstrapStateError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BootstrapStateError(f"{artifact_label} is not valid JSON") from exc
    try:
        if type(decoded) is not dict or _canonical_json_bytes(decoded) != raw:
            raise BootstrapStateError(f"{artifact_label} bytes are noncanonical")
        validated = validator(decoded)
        if type(validated) is not dict or _canonical_json_bytes(validated) != raw:
            raise BootstrapStateError(
                f"{artifact_label} validator changed canonical bytes"
            )
    except BootstrapStateError:
        raise
    except RecursionError as exc:
        raise BootstrapStateError(f"{artifact_label} is not valid JSON") from exc
    except Exception as exc:
        raise BootstrapStateError(f"{artifact_label} schema is invalid") from exc
    return validated


def _decode_canonical_bootstrap_journal(raw: bytes) -> dict[str, Any]:
    return _decode_canonical_private_json(
        raw,
        max_bytes=MAX_BOOTSTRAP_JOURNAL_BYTES,
        validator=_validated_bootstrap_journal_payload,
        artifact_label="bootstrap journal",
    )


def _read_private_canonical_json_at(
    parent_fd: int,
    filename: str,
    *,
    max_bytes: int,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    artifact_label: str,
) -> tuple[dict[str, Any], tuple[int, int, int, int, int], str, bytes]:
    """Stably read an owner-only canonical artifact relative to a pinned dir."""

    _bootstrap_basename(filename, f"{artifact_label} filename")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise BootstrapStateError(f"{artifact_label} byte ceiling is invalid")

    descriptor: int | None = None
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise BootstrapStateError(f"{artifact_label} inode is invalid")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        path_after = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except BootstrapStateError:
        raise
    except OSError as exc:
        raise BootstrapStateError(f"cannot read {artifact_label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if _stat_identity(before) != _stat_identity(after) or _stat_identity(
        after
    ) != _stat_identity(path_after):
        raise BootstrapStateError(f"{artifact_label} changed while reading")
    payload = _decode_canonical_private_json(
        raw,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=artifact_label,
    )
    return payload, _stat_identity(after), _sha256_tag(raw), raw


def _read_bootstrap_journal_at(
    parent_fd: int, filename: str
) -> tuple[dict[str, Any], tuple[int, int, int, int, int], str]:
    payload, identity, digest, _ = _read_private_canonical_json_at(
        parent_fd,
        filename,
        max_bytes=MAX_BOOTSTRAP_JOURNAL_BYTES,
        validator=_validated_bootstrap_journal_payload,
        artifact_label="bootstrap journal",
    )
    return payload, identity, digest


def _read_canonical_bootstrap_journal(path: Path) -> dict[str, Any]:
    if os.name == "nt":
        _require_private_destination(path)
        if _is_reparse_or_symlink(path):
            raise BootstrapStateError("bootstrap journal path is indirected")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise BootstrapStateError("cannot read bootstrap journal") from exc
        return _decode_canonical_bootstrap_journal(raw)
    parent_fd, filename = _open_private_parent_dirfd(path)
    try:
        return _read_bootstrap_journal_at(parent_fd, filename)[0]
    finally:
        os.close(parent_fd)


def _verify_private_bytes_at(
    parent_fd: int,
    filename: str,
    raw: bytes,
    *,
    max_bytes: int = MAX_BOOTSTRAP_JOURNAL_BYTES,
    validator: Callable[[dict[str, Any]], dict[str, Any]] = (
        _validated_bootstrap_journal_payload
    ),
    artifact_label: str = "bootstrap journal",
) -> tuple[int, int, int, int, int]:
    payload, identity, digest, published = _read_private_canonical_json_at(
        parent_fd,
        filename,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=artifact_label,
    )
    if (
        digest != _sha256_tag(raw)
        or published != raw
        or _canonical_json_bytes(payload) != raw
    ):
        raise BootstrapStateError(f"published {artifact_label} bytes drifted")
    return identity


def _macos_swap_names_at(parent_fd: int, left: str, right: str) -> None:
    """Atomically exchange two names so the replaced inode remains verifiable."""

    if sys.platform != "darwin":
        raise BootstrapStateError("exclusive bootstrap replacement requires macOS")
    import ctypes

    rename_swap = 0x00000002
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameatx_np
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        parent_fd,
        left.encode("utf-8"),
        parent_fd,
        right.encode("utf-8"),
        rename_swap,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise BootstrapStateError("cannot atomically exchange bootstrap journal") from OSError(
            error, "renameatx_np failed"
        )


def _macos_rename_exclusive_at(parent_fd: int, source: str, destination: str) -> None:
    """Atomically rename one sibling without replacing an existing destination."""

    if sys.platform != "darwin":
        raise BootstrapStateError("exclusive private rename requires macOS")
    source = _bootstrap_basename(source, "rename source")
    destination = _bootstrap_basename(destination, "rename destination")
    if source == destination:
        raise BootstrapStateError("exclusive private rename names collide")
    import ctypes

    rename_excl = 0x00000004
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameatx_np
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        parent_fd,
        source.encode("utf-8"),
        parent_fd,
        destination.encode("utf-8"),
        rename_excl,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise BootstrapStateError("cannot exclusively rename private entry") from OSError(
            error, "renameatx_np failed"
        )


def _durably_publish_exclusive_private_file_at(
    parent_fd: int,
    temporary: str,
    filename: str,
    *,
    fsynced_identity: tuple[int, int, int, int, int],
    verify_published: Callable[[], tuple[int, int, int, int, int]],
    artifact_label: str,
) -> None:
    """Rename-create one private file without guessing after mutation begins."""

    def verify_exact_publication() -> None:
        identity = verify_published()
        try:
            named = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise BootstrapStateError(
                f"{artifact_label} create binding drifted"
            ) from exc
        if (
            identity[:2] != fsynced_identity[:2]
            or _stat_identity(named)[:2] != fsynced_identity[:2]
            or not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.getuid()
            or stat.S_IMODE(named.st_mode) != 0o600
            or named.st_nlink != 1
        ):
            raise BootstrapStateError(
                f"{artifact_label} create binding drifted"
            )

    try:
        _macos_rename_exclusive_at(parent_fd, temporary, filename)
    except BaseException as rename_exc:
        # The wrapper may have renamed successfully before reporting failure.
        # Classify both descriptor-relative names, but never mutate either one.
        try:
            try:
                temp_info = os.stat(
                    temporary, dir_fd=parent_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                temp_info = None
            try:
                final_info = os.stat(
                    filename, dir_fd=parent_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                final_info = None
        except BaseException as classify_exc:
            raise BootstrapRecoveryRequired(
                f"{artifact_label} exclusive rename outcome is ambiguous"
            ) from classify_exc
        temp_exact = (
            temp_info is not None
            and _stat_identity(temp_info)[:2] == fsynced_identity[:2]
            and stat.S_ISREG(temp_info.st_mode)
            and temp_info.st_uid == os.getuid()
            and stat.S_IMODE(temp_info.st_mode) == 0o600
            and temp_info.st_nlink == 1
        )
        final_exact = (
            final_info is not None
            and _stat_identity(final_info)[:2] == fsynced_identity[:2]
            and stat.S_ISREG(final_info.st_mode)
            and final_info.st_uid == os.getuid()
            and stat.S_IMODE(final_info.st_mode) == 0o600
            and final_info.st_nlink == 1
        )
        if temp_info is None and final_exact:
            try:
                verify_exact_publication()
            except BaseException as verify_exc:
                raise BootstrapRecoveryRequired(
                    f"{artifact_label} exclusive rename outcome is ambiguous"
                ) from verify_exc
            raise BootstrapRecoveryRequired(
                f"{artifact_label} exclusive rename may have committed"
            ) from rename_exc
        if temp_exact:
            raise BootstrapRecoveryRequired(
                f"{artifact_label} exclusive rename left temporary residue"
            ) from rename_exc
        raise BootstrapRecoveryRequired(
            f"{artifact_label} exclusive rename outcome is ambiguous"
        ) from rename_exc

    try:
        verify_exact_publication()
    except BaseException as verify_exc:
        raise BootstrapRecoveryRequired(
            f"{artifact_label} create verification requires recovery"
        ) from verify_exc

    try:
        os.fsync(parent_fd)
    except BaseException as fsync_exc:
        raise BootstrapRecoveryRequired(
            f"{artifact_label} parent durability requires recovery"
        ) from fsync_exc

    try:
        verify_exact_publication()
    except BaseException as post_fsync_exc:
        raise BootstrapRecoveryRequired(
            f"{artifact_label} post-fsync verification requires recovery"
        ) from post_fsync_exc


def _durable_atomic_private_file_at(
    parent_fd: int,
    filename: str,
    raw: bytes,
    *,
    replace_existing: bool,
    expected_existing_identity: tuple[int, int, int, int, int] | None,
    max_bytes: int = MAX_BOOTSTRAP_JOURNAL_BYTES,
    validator: Callable[[dict[str, Any]], dict[str, Any]] = (
        _validated_bootstrap_journal_payload
    ),
    artifact_label: str = "bootstrap journal",
) -> str:
    if type(raw) is not bytes:
        raise BootstrapStateError("durable private payload is not bytes")
    _bootstrap_basename(filename, f"{artifact_label} filename")
    _decode_canonical_private_json(
        raw,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=artifact_label,
    )
    try:
        initial = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        initial = None
    if replace_existing:
        if (
            initial is None
            or expected_existing_identity is None
            or _stat_identity(initial) != expected_existing_identity
        ):
            raise BootstrapStateError("bootstrap journal compare-and-swap failed")
    elif initial is not None or expected_existing_identity is not None:
        raise BootstrapStateError("bootstrap journal create precondition failed")

    temporary = f".{filename}.{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    after_write: os.stat_result | None = None
    temporary_created = False
    swapped = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
            dir_fd=parent_fd,
        )
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_uid != os.getuid()
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_nlink != 1
        ):
            raise BootstrapStateError("bootstrap temporary inode is invalid")
        view = memoryview(raw)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise BootstrapStateError("bootstrap temporary write was incomplete")
            written += count
        os.fsync(descriptor)
        after_write = os.fstat(descriptor)
        temp_path = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _stat_identity(created)[:2] != _stat_identity(after_write)[:2]
            or _stat_identity(after_write) != _stat_identity(temp_path)
            or not stat.S_ISREG(after_write.st_mode)
            or after_write.st_uid != os.getuid()
            or stat.S_IMODE(after_write.st_mode) != 0o600
            or after_write.st_nlink != 1
            or not stat.S_ISREG(temp_path.st_mode)
            or temp_path.st_uid != os.getuid()
            or stat.S_IMODE(temp_path.st_mode) != 0o600
            or temp_path.st_nlink != 1
        ):
            raise BootstrapRecoveryRequired(
                "bootstrap temporary pathname requires locked recovery"
            )

        if replace_existing:
            current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            if expected_existing_identity is None or _stat_identity(
                current
            ) != expected_existing_identity:
                raise BootstrapStateError("bootstrap journal compare-and-swap failed")
            _macos_swap_names_at(parent_fd, temporary, filename)
            swapped = True
            replaced = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
            final_after_swap = os.stat(
                filename, dir_fd=parent_fd, follow_symlinks=False
            )
            # renameatx_np legitimately changes ctime.  The predecessor was
            # matched byte/identity-exactly immediately before the exchange;
            # after it, device+inode is the stable proof that the exchanged
            # names still reference those same two inodes.
            if (
                _stat_identity(replaced)[:2] != expected_existing_identity[:2]
                or _stat_identity(final_after_swap)[:2]
                != _stat_identity(after_write)[:2]
            ):
                raise BootstrapStateError("bootstrap journal compare-and-swap failed")
        else:
            _durably_publish_exclusive_private_file_at(
                parent_fd,
                temporary,
                filename,
                fsynced_identity=_stat_identity(after_write),
                verify_published=lambda: _verify_private_bytes_at(
                    parent_fd,
                    filename,
                    raw,
                    max_bytes=max_bytes,
                    validator=validator,
                    artifact_label=artifact_label,
                ),
                artifact_label=artifact_label,
            )
        if swapped:
            final_identity = _verify_private_bytes_at(
                parent_fd,
                filename,
                raw,
                max_bytes=max_bytes,
                validator=validator,
                artifact_label=artifact_label,
            )
            if final_identity[:2] != _stat_identity(after_write)[:2]:
                raise BootstrapStateError(
                    "published bootstrap inode is not the fsynced inode"
                )
            final_info = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            if _stat_identity(final_info) != final_identity or final_info.st_nlink != 1:
                raise BootstrapStateError("published bootstrap inode has extra links")
            # The new name and retained predecessor are durable before discard.
            os.fsync(parent_fd)
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
            swapped = False
    except BaseException as caught:
        if isinstance(caught, BootstrapStateError):
            failure = caught
        elif isinstance(caught, OSError):
            failure = BootstrapStateError(
                "cannot durably write private bootstrap state"
            )
        elif temporary_created or swapped:
            failure = BootstrapRecoveryRequired(
                "bootstrap temporary residue requires locked recovery"
            )
            failure.__cause__ = caught
        else:
            raise
        if swapped:
            try:
                retained = os.stat(
                    temporary, dir_fd=parent_fd, follow_symlinks=False
                )
                published = os.stat(
                    filename, dir_fd=parent_fd, follow_symlinks=False
                )
                safe_pair = (
                    expected_existing_identity is not None
                    and after_write is not None
                        and _stat_identity(retained)[:2]
                        == expected_existing_identity[:2]
                    and _stat_identity(published)[:2]
                    == _stat_identity(after_write)[:2]
                )
            except BaseException:
                safe_pair = False
            if not safe_pair:
                raise BootstrapRecoveryRequired(
                    "bootstrap journal requires locked recovery"
                ) from failure
            try:
                _macos_swap_names_at(parent_fd, temporary, filename)
                os.fsync(parent_fd)
                swapped = False
            except BaseException as rollback_exc:
                raise BootstrapRecoveryRequired(
                    "bootstrap journal rollback requires locked recovery"
                ) from rollback_exc
            raise BootstrapRecoveryRequired(
                "bootstrap journal rollback retained temporary residue"
            ) from failure
        elif temporary_created:
            if isinstance(failure, BootstrapRecoveryRequired):
                raise failure
            raise BootstrapRecoveryRequired(
                "bootstrap temporary residue requires locked recovery"
            ) from failure
        raise failure from (caught if caught is not failure else None)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_exc:
                if temporary_created:
                    raise BootstrapRecoveryRequired(
                        "bootstrap temporary descriptor close requires recovery"
                    ) from close_exc
                raise
    return _sha256_tag(raw)


def _write_private_canonical_json_at(
    parent_fd: int,
    filename: str,
    payload: dict[str, Any],
    *,
    max_bytes: int,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
    artifact_label: str,
    replace_existing: bool,
    expected_existing_digest: str | None,
) -> str:
    """Exclusively create or digest-CAS one canonical private JSON artifact."""

    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable private artifact state is available only on the macOS host"
        )
    if type(payload) is not dict:
        raise BootstrapStateError(f"{artifact_label} payload is not an object")
    try:
        raw = _canonical_json_bytes(payload)
    except BootstrapStateError:
        raise
    except Exception as exc:
        raise BootstrapStateError(f"{artifact_label} schema is invalid") from exc
    _decode_canonical_private_json(
        raw,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=artifact_label,
    )

    expected_identity: tuple[int, int, int, int, int] | None = None
    if replace_existing:
        if not _is_sha256_tag(expected_existing_digest):
            raise BootstrapStateError(
                f"{artifact_label} replacement digest is invalid"
            )
        _, expected_identity, existing_digest, _ = _read_private_canonical_json_at(
            parent_fd,
            filename,
            max_bytes=max_bytes,
            validator=validator,
            artifact_label=artifact_label,
        )
        if existing_digest != expected_existing_digest:
            raise BootstrapStateError(
                f"{artifact_label} compare-and-swap digest failed"
            )
    elif expected_existing_digest is not None:
        raise BootstrapStateError(
            f"{artifact_label} create digest precondition is invalid"
        )

    return _durable_atomic_private_file_at(
        parent_fd,
        filename,
        raw,
        replace_existing=replace_existing,
        expected_existing_identity=expected_identity,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=artifact_label,
    )


def _read_private_bytes_at(
    parent_fd: int,
    filename: str,
    *,
    max_bytes: int,
    artifact_label: str,
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    """Read one stable owner-only regular file through its pinned parent."""

    _bootstrap_basename(filename, f"{artifact_label} filename")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > max_bytes
        ):
            raise BootstrapStateError(f"{artifact_label} inode is invalid")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        named = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except BootstrapStateError:
        raise
    except OSError as exc:
        raise BootstrapStateError(f"cannot read {artifact_label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        len(raw) > max_bytes
        or _stat_identity(before) != _stat_identity(after)
        or _stat_identity(after) != _stat_identity(named)
    ):
        raise BootstrapStateError(f"{artifact_label} changed while reading")
    return raw, _stat_identity(after)


def _durable_atomic_private_bytes_at(
    parent_fd: int,
    filename: str,
    raw: bytes,
    *,
    expected_existing: bytes | None,
    max_bytes: int,
    artifact_label: str,
) -> None:
    """Exclusive-create or byte-CAS one non-JSON private file on macOS."""

    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable private artifact state is available only on the macOS host"
        )
    _bootstrap_basename(filename, f"{artifact_label} filename")
    if type(raw) is not bytes or len(raw) > max_bytes:
        raise BootstrapStateError(f"{artifact_label} payload size is invalid")
    expected_identity: tuple[int, int, int, int, int] | None = None
    if expected_existing is not None:
        observed, expected_identity = _read_private_bytes_at(
            parent_fd,
            filename,
            max_bytes=max_bytes,
            artifact_label=artifact_label,
        )
        if observed != expected_existing:
            raise BootstrapStateError(
                f"{artifact_label} compare-and-swap bytes drifted"
            )
    else:
        try:
            os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BootstrapStateError(f"{artifact_label} already exists")

    temporary = f".{filename}.{secrets.token_hex(16)}.tmp"
    descriptor: int | None = None
    after_write: os.stat_result | None = None
    temporary_created = False
    swapped = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
            dir_fd=parent_fd,
        )
        temporary_created = True
        os.fchmod(descriptor, 0o600)
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_uid != os.getuid()
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_nlink != 1
        ):
            raise BootstrapStateError(f"{artifact_label} temporary inode is invalid")
        view = memoryview(raw)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise BootstrapStateError(f"{artifact_label} write was incomplete")
            written += count
        os.fsync(descriptor)
        after_write = os.fstat(descriptor)
        named_temp = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _stat_identity(created)[:2] != _stat_identity(after_write)[:2]
            or _stat_identity(after_write) != _stat_identity(named_temp)
            or not stat.S_ISREG(after_write.st_mode)
            or after_write.st_uid != os.getuid()
            or stat.S_IMODE(after_write.st_mode) != 0o600
            or after_write.st_nlink != 1
            or not stat.S_ISREG(named_temp.st_mode)
            or named_temp.st_uid != os.getuid()
            or stat.S_IMODE(named_temp.st_mode) != 0o600
            or named_temp.st_nlink != 1
        ):
            raise BootstrapRecoveryRequired(
                f"{artifact_label} temporary pathname requires recovery"
            )
        if expected_existing is None:
            def verify_created_bytes() -> tuple[int, int, int, int, int]:
                published_raw, published_identity = _read_private_bytes_at(
                    parent_fd,
                    filename,
                    max_bytes=max_bytes,
                    artifact_label=artifact_label,
                )
                if published_raw != raw:
                    raise BootstrapStateError(
                        f"published {artifact_label} bytes drifted"
                    )
                return published_identity

            _durably_publish_exclusive_private_file_at(
                parent_fd,
                temporary,
                filename,
                fsynced_identity=_stat_identity(after_write),
                verify_published=verify_created_bytes,
                artifact_label=artifact_label,
            )
        else:
            current = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            if (
                expected_identity is None
                or _stat_identity(current) != expected_identity
            ):
                raise BootstrapStateError(
                    f"{artifact_label} compare-and-swap identity drifted"
                )
            _macos_swap_names_at(parent_fd, temporary, filename)
            swapped = True
            retained = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
            published = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
            if (
                _stat_identity(retained)[:2] != expected_identity[:2]
                or _stat_identity(published)[:2] != _stat_identity(after_write)[:2]
            ):
                raise BootstrapRecoveryRequired(
                    f"{artifact_label} exchange identity drifted"
                )
        if swapped:
            published_raw, published_identity = _read_private_bytes_at(
                parent_fd,
                filename,
                max_bytes=max_bytes,
                artifact_label=artifact_label,
            )
            if (
                published_raw != raw
                or after_write is None
                or published_identity[:2] != _stat_identity(after_write)[:2]
            ):
                raise BootstrapRecoveryRequired(
                    f"published {artifact_label} bytes drifted"
                )
            os.fsync(parent_fd)
            os.unlink(temporary, dir_fd=parent_fd)
            os.fsync(parent_fd)
            swapped = False
    except BaseException as caught:
        if isinstance(caught, BootstrapStateError):
            failure = caught
        elif isinstance(caught, OSError):
            failure = BootstrapStateError(
                f"cannot durably publish {artifact_label}"
            )
        elif temporary_created or swapped:
            failure = BootstrapRecoveryRequired(
                f"{artifact_label} temporary residue requires recovery"
            )
            failure.__cause__ = caught
        else:
            raise
        if swapped:
            raise BootstrapRecoveryRequired(
                f"{artifact_label} exchange requires locked recovery"
            ) from failure
        if temporary_created:
            if isinstance(failure, BootstrapRecoveryRequired):
                raise failure
            raise BootstrapRecoveryRequired(
                f"{artifact_label} temporary residue requires recovery"
            ) from failure
        raise failure from (caught if caught is not failure else None)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_exc:
                if temporary_created:
                    raise BootstrapRecoveryRequired(
                        f"{artifact_label} temporary descriptor close requires recovery"
                    ) from close_exc
                raise


def _macos_rename_exclusive_between_at(
    source_fd: int,
    source: str,
    destination_fd: int,
    destination: str,
) -> None:
    """Atomically move one directory between pinned parents without replace."""

    if sys.platform != "darwin":
        raise BootstrapStateError("exclusive private rename requires macOS")
    source = _bootstrap_basename(source, "rename source")
    destination = _bootstrap_basename(destination, "rename destination")
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameatx_np
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if function(
        source_fd,
        source.encode("utf-8"),
        destination_fd,
        destination.encode("utf-8"),
        0x00000004,
    ) != 0:
        error = ctypes.get_errno()
        raise BootstrapStateError("cannot exclusively commit private row") from OSError(
            error, "renameatx_np failed"
        )


@dataclass
class _PrivateTreeSealContext:
    durability_started: bool = False


class _PrivateTreeOsOps:
    """Narrow fd-operations seam for synthetic race/durability tests."""

    def getuid(self) -> int:
        return os.getuid()

    def open(self, name: str, flags: int, *, dir_fd: int) -> int:
        return os.open(name, flags, dir_fd=dir_fd)

    def open_path(self, path: Path, flags: int) -> int:
        return os.open(path, flags)

    def mkdir(self, name: str, mode: int, *, dir_fd: int) -> None:
        os.mkdir(name, mode, dir_fd=dir_fd)

    def fchmod(self, descriptor: int, mode: int) -> None:
        os.fchmod(descriptor, mode)

    def unlink(self, name: str, *, dir_fd: int) -> None:
        os.unlink(name, dir_fd=dir_fd)

    def rename_exclusive(
        self, source: str, destination: str, *, dir_fd: int
    ) -> None:
        """Rename without replacement; an exception guarantees no mutation."""
        _macos_rename_exclusive_at(dir_fd, source, destination)

    def fstat(self, descriptor: int) -> os.stat_result:
        return os.fstat(descriptor)

    def stat(self, name: str, *, dir_fd: int) -> os.stat_result:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)

    def stat_path(self, path: Path) -> os.stat_result:
        return os.stat(path, follow_symlinks=False)

    def listdir(self, descriptor: int) -> list[str]:
        return os.listdir(descriptor)

    def read(self, descriptor: int, size: int) -> bytes:
        return os.read(descriptor, size)

    def write(self, descriptor: int, raw: bytes | memoryview) -> int:
        return os.write(descriptor, raw)

    def seek(self, descriptor: int, offset: int, whence: int) -> int:
        return os.lseek(descriptor, offset, whence)

    def fsync(self, descriptor: int) -> None:
        os.fsync(descriptor)

    def close(self, descriptor: int) -> None:
        os.close(descriptor)


def _private_node_identity(
    info: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
    )


def _device_inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _stream_hash_private_fd(
    descriptor: int,
    *,
    ops: _PrivateTreeOsOps,
) -> tuple[str, int, tuple[int, int, int, int, int, int, int, int]]:
    before = ops.fstat(descriptor)
    _validate_private_tree_inode(
        before,
        kind="file",
        owner_uid=ops.getuid(),
        label="private snapshot file",
    )
    try:
        ops.seek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise SnapshotError("cannot seek pinned snapshot file") from exc
    digest = hashlib.sha256()
    size = 0
    while True:
        try:
            chunk = ops.read(descriptor, 1024 * 1024)
        except OSError as exc:
            raise SnapshotError("cannot hash pinned snapshot file") from exc
        if not chunk:
            break
        if type(chunk) is not bytes:
            raise SnapshotError("pinned snapshot read returned non-bytes")
        digest.update(chunk)
        size += len(chunk)
    after = ops.fstat(descriptor)
    before_identity = _private_node_identity(before)
    if _private_node_identity(after) != before_identity or size != before.st_size:
        raise SnapshotError("pinned snapshot drifted while hashing")
    return f"sha256:{digest.hexdigest()}", size, before_identity


def _verify_pinned_staging_binding_at(
    parent_fd: int,
    staging_fd: int,
    staging_name: str,
    staging_path: Path,
    *,
    expected_device_inode: tuple[int, int],
    expected_names: tuple[str, ...],
    ops: _PrivateTreeOsOps,
) -> tuple[int, int, int, int, int, int, int, int]:
    parent = ops.fstat(parent_fd)
    path_parent = ops.stat_path(staging_path.parent)
    _validate_private_tree_inode(
        parent,
        kind="directory",
        owner_uid=ops.getuid(),
        label="private staging parent",
    )
    _validate_private_tree_inode(
        path_parent,
        kind="directory",
        owner_uid=ops.getuid(),
        label="private staging parent",
    )
    if _device_inode(parent) != _device_inode(path_parent):
        raise BootstrapStateError("private staging parent pathname drifted")
    inventory_identity = _stable_private_directory_inventory(
        staging_fd,
        expected_names,
        owner_uid=ops.getuid(),
        ops=ops,
        label="private staging directory",
    )
    named = ops.stat(staging_name, dir_fd=parent_fd)
    absolute = ops.stat_path(staging_path)
    if (
        inventory_identity[:2] != expected_device_inode
        or _device_inode(named) != expected_device_inode
        or _device_inode(absolute) != expected_device_inode
    ):
        raise BootstrapStateError("private staging binding drifted")
    _validate_private_tree_inode(
        named,
        kind="directory",
        owner_uid=ops.getuid(),
        label="private staging directory",
    )
    _validate_private_tree_inode(
        absolute,
        kind="directory",
        owner_uid=ops.getuid(),
        label="private staging directory",
    )
    return inventory_identity


def _verify_pinned_snapshot_binding_at(
    staging_fd: int,
    snapshot_fd: int,
    snapshot_path: Path,
    *,
    expected_device_inode: tuple[int, int],
    ops: _PrivateTreeOsOps,
) -> tuple[int, int, int, int, int, int, int, int]:
    opened = ops.fstat(snapshot_fd)
    named = ops.stat(SNAPSHOT_FILENAME, dir_fd=staging_fd)
    absolute = ops.stat_path(snapshot_path)
    for info in (opened, named, absolute):
        _validate_private_tree_inode(
            info,
            kind="file",
            owner_uid=ops.getuid(),
            label="private snapshot file",
        )
    if any(
        _device_inode(info) != expected_device_inode
        for info in (opened, named, absolute)
    ):
        raise BootstrapStateError("private snapshot binding drifted")
    return _private_node_identity(opened)


def _verify_pinned_source_binding(
    source_fd: int,
    source_path: Path,
    *,
    expected_device_inode: tuple[int, int],
    ops: _PrivateTreeOsOps,
) -> None:
    opened = ops.fstat(source_fd)
    named = ops.stat_path(source_path)
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or _device_inode(opened) != expected_device_inode
        or _device_inode(named) != expected_device_inode
    ):
        raise SnapshotError("SQLite source pathname drifted")


def _process_fd_device_inodes() -> dict[int, tuple[int, int]]:
    if sys.platform != "darwin":
        raise BootstrapStateError(
            "SQLite descriptor binding is available only on the macOS host"
        )
    try:
        names = os.listdir("/dev/fd")
    except OSError as exc:
        raise BootstrapStateError("cannot enumerate process descriptors") from exc
    result: dict[int, tuple[int, int]] = {}
    for name in names:
        if type(name) is not str or not name.isascii() or not name.isdecimal():
            continue
        descriptor = int(name)
        try:
            result[descriptor] = _device_inode(os.fstat(descriptor))
        except OSError:
            continue
    return result


def _open_inode_bound_sqlite_connection(
    opener: Callable[[Path], Any],
    path: Path,
    expected_device_inode: tuple[int, int],
    label: str,
    *,
    _fd_snapshot: Callable[[], Mapping[int, tuple[int, int]]] | None = None,
) -> Any:
    snapshot = _fd_snapshot or _process_fd_device_inodes
    before = dict(snapshot())
    connection: Any = None
    try:
        connection = opener(path)
        after = dict(snapshot())
        opened_descriptors = set(after).difference(before)
        if not any(
            after[descriptor] == expected_device_inode
            for descriptor in opened_descriptors
        ):
            raise SnapshotError(f"{label} connection inode is not pinned")
        return connection
    except BaseException:
        if connection is not None:
            try:
                connection.close()
            except (OSError, sqlite3.Error):
                pass
        raise


def _validated_expected_private_tree(
    expected: ExpectedPrivateDirectory,
) -> ExpectedPrivateDirectory:
    if type(expected) is not ExpectedPrivateDirectory:
        raise BootstrapStateError("private tree root expectation is not a directory")
    count = 0

    def visit(
        node: ExpectedPrivateFile | ExpectedPrivateDirectory, depth: int
    ) -> ExpectedPrivateFile | ExpectedPrivateDirectory:
        nonlocal count
        count += 1
        if count > MAX_PRIVATE_TREE_NODES or depth > MAX_PRIVATE_TREE_DEPTH:
            raise BootstrapStateError("private tree specification is too large")
        if type(node) is ExpectedPrivateFile:
            if (
                type(node.byte_size) is not int
                or node.byte_size < 0
                or not _is_sha256_tag(node.sha256)
            ):
                raise BootstrapStateError("private tree file expectation is invalid")
            return node
        if type(node) is not ExpectedPrivateDirectory or type(node.children) is not dict:
            raise BootstrapStateError("private tree directory expectation is invalid")
        rebuilt: dict[str, ExpectedPrivateFile | ExpectedPrivateDirectory] = {}
        names: list[str] = []
        for name in node.children:
            try:
                if type(name) is not str or _bootstrap_basename(
                    name, "tree child name"
                ) != name:
                    raise BootstrapStateError("private tree child name is invalid")
            except AtomicAcquisitionError as exc:
                raise BootstrapStateError(
                    "private tree child name is invalid"
                ) from exc
            names.append(name)
        for name in sorted(names, key=os.fsencode):
            rebuilt[name] = visit(node.children[name], depth + 1)
        return ExpectedPrivateDirectory(children=rebuilt)

    validated = visit(expected, 0)
    if type(validated) is not ExpectedPrivateDirectory:
        raise BootstrapStateError("private tree root expectation is not a directory")
    return validated


def _require_live_private_tree_ops() -> None:
    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable private tree sealing is available only on the macOS host"
        )
    if (
        not getattr(os, "O_NOFOLLOW", 0)
        or not getattr(os, "O_DIRECTORY", 0)
        or os.listdir not in os.supports_fd
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise BootstrapStateError("host lacks descriptor-relative tree sealing")


def _validate_private_tree_inode(
    info: os.stat_result,
    *,
    kind: str,
    owner_uid: int,
    label: str,
) -> None:
    if kind == "file":
        valid = (
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_nlink == 1
        )
    elif kind == "directory":
        valid = stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
    else:
        raise BootstrapStateError("private tree expected kind is invalid")
    if not valid or info.st_uid != owner_uid:
        raise BootstrapStateError(f"{label} inode is invalid")


def _open_private_tree_node_at(
    parent_fd: int,
    name: str,
    *,
    kind: str,
    owner_uid: int,
    ops: _PrivateTreeOsOps,
    label: str,
) -> tuple[int, tuple[int, int, int, int, int, int, int, int]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if kind == "directory":
        flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor = ops.open(name, flags, dir_fd=parent_fd)
    try:
        opened = ops.fstat(descriptor)
        named = ops.stat(name, dir_fd=parent_fd)
        _validate_private_tree_inode(
            opened, kind=kind, owner_uid=owner_uid, label=label
        )
        if _private_node_identity(opened) != _private_node_identity(named):
            raise BootstrapStateError(f"{label} pathname drifted while opening")
        return descriptor, _private_node_identity(opened)
    except BaseException:
        ops.close(descriptor)
        raise


def _stable_private_directory_inventory(
    descriptor: int,
    expected_names: tuple[str, ...],
    *,
    owner_uid: int,
    ops: _PrivateTreeOsOps,
    label: str,
) -> tuple[int, int, int, int, int, int, int, int]:
    names, identity = _stable_private_directory_names(
        descriptor,
        owner_uid=owner_uid,
        ops=ops,
        label=label,
    )
    if names != expected_names:
        raise BootstrapStateError(f"{label} inventory or inode drifted")
    return identity


def _stable_private_directory_names(
    descriptor: int,
    *,
    owner_uid: int,
    ops: _PrivateTreeOsOps,
    label: str,
) -> tuple[tuple[str, ...], tuple[int, int, int, int, int, int, int, int]]:
    before = ops.fstat(descriptor)
    _validate_private_tree_inode(
        before, kind="directory", owner_uid=owner_uid, label=label
    )
    first = ops.listdir(descriptor)
    middle = ops.fstat(descriptor)
    second = ops.listdir(descriptor)
    after = ops.fstat(descriptor)
    if any(type(name) is not str for name in first + second):
        raise BootstrapStateError(f"{label} inventory contains non-text names")
    first_sorted = tuple(sorted(first, key=os.fsencode))
    second_sorted = tuple(sorted(second, key=os.fsencode))
    identity = _private_node_identity(before)
    if (
        first_sorted != second_sorted
        or identity != _private_node_identity(middle)
        or identity != _private_node_identity(after)
    ):
        raise BootstrapStateError(f"{label} inventory or inode drifted")
    return first_sorted, identity


def _verify_private_tree_named_identity(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int, int, int, int, int, int, int],
    *,
    ops: _PrivateTreeOsOps,
    label: str,
) -> None:
    named = ops.stat(name, dir_fd=parent_fd)
    if _private_node_identity(named) != expected_identity:
        raise BootstrapStateError(f"{label} pathname identity drifted")


def _seal_open_private_file(
    descriptor: int,
    parent_fd: int,
    name: str,
    expected: ExpectedPrivateFile,
    *,
    relative_path: tuple[str, ...],
    owner_uid: int,
    ops: _PrivateTreeOsOps,
    context: _PrivateTreeSealContext,
) -> PrivateNodeSeal:
    label = "private tree file"
    opened = ops.fstat(descriptor)
    _validate_private_tree_inode(opened, kind="file", owner_uid=owner_uid, label=label)
    opened_identity = _private_node_identity(opened)
    digest = hashlib.sha256()
    byte_size = 0
    while True:
        chunk = ops.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        if type(chunk) is not bytes:
            raise BootstrapStateError("private tree file read returned non-bytes")
        digest.update(chunk)
        byte_size += len(chunk)
    hashed = ops.fstat(descriptor)
    hashed_identity = _private_node_identity(hashed)
    _verify_private_tree_named_identity(
        parent_fd, name, hashed_identity, ops=ops, label=label
    )
    actual_digest = f"sha256:{digest.hexdigest()}"
    if (
        opened_identity != hashed_identity
        or byte_size != expected.byte_size
        or hashed.st_size != expected.byte_size
        or actual_digest != expected.sha256
    ):
        raise BootstrapStateError("private tree file bytes or inode drifted")
    context.durability_started = True
    ops.fsync(descriptor)
    durable = ops.fstat(descriptor)
    durable_identity = _private_node_identity(durable)
    _verify_private_tree_named_identity(
        parent_fd, name, durable_identity, ops=ops, label=label
    )
    if durable_identity != hashed_identity:
        raise BootstrapStateError("private tree file drifted during fsync")
    return PrivateNodeSeal(
        relative_path=relative_path,
        kind="file",
        identity=durable_identity,
        byte_size=byte_size,
        sha256=actual_digest,
    )


def _seal_open_private_directory(
    descriptor: int,
    expected: ExpectedPrivateDirectory,
    *,
    relative_path: tuple[str, ...],
    owner_uid: int,
    ops: _PrivateTreeOsOps,
    context: _PrivateTreeSealContext,
) -> tuple[PrivateNodeSeal, ...]:
    names = tuple(sorted(expected.children, key=os.fsencode))
    initial_identity = _stable_private_directory_inventory(
        descriptor,
        names,
        owner_uid=owner_uid,
        ops=ops,
        label="private tree directory",
    )
    nodes: list[PrivateNodeSeal] = []
    direct_identities: dict[
        str, tuple[int, int, int, int, int, int, int, int]
    ] = {}
    for name in names:
        child = expected.children[name]
        child_path = (*relative_path, name)
        kind = "file" if type(child) is ExpectedPrivateFile else "directory"
        child_fd, _ = _open_private_tree_node_at(
            descriptor,
            name,
            kind=kind,
            owner_uid=owner_uid,
            ops=ops,
            label=f"private tree {kind}",
        )
        try:
            if type(child) is ExpectedPrivateFile:
                sealed = _seal_open_private_file(
                    child_fd,
                    descriptor,
                    name,
                    child,
                    relative_path=child_path,
                    owner_uid=owner_uid,
                    ops=ops,
                    context=context,
                )
                nodes.append(sealed)
                direct_identities[name] = sealed.identity
            else:
                assert type(child) is ExpectedPrivateDirectory
                descendants = _seal_open_private_directory(
                    child_fd,
                    child,
                    relative_path=child_path,
                    owner_uid=owner_uid,
                    ops=ops,
                    context=context,
                )
                nodes.extend(descendants)
                directory_seal = descendants[-1]
                direct_identities[name] = directory_seal.identity
        finally:
            ops.close(child_fd)

    def revalidate() -> tuple[int, int, int, int, int, int, int, int]:
        identity = _stable_private_directory_inventory(
            descriptor,
            names,
            owner_uid=owner_uid,
            ops=ops,
            label="private tree directory",
        )
        for child_name in names:
            _verify_private_tree_named_identity(
                descriptor,
                child_name,
                direct_identities[child_name],
                ops=ops,
                label="private tree child",
            )
        return identity

    before_fsync = revalidate()
    if before_fsync != initial_identity:
        raise BootstrapStateError("private tree directory changed during traversal")
    context.durability_started = True
    ops.fsync(descriptor)
    after_fsync = revalidate()
    if after_fsync != before_fsync:
        raise BootstrapStateError("private tree directory drifted during fsync")
    nodes.append(
        PrivateNodeSeal(
            relative_path=relative_path,
            kind="directory",
            identity=after_fsync,
            byte_size=None,
            sha256=None,
        )
    )
    return tuple(nodes)


def seal_private_tree_at(
    parent_fd: int,
    root_name: str,
    expected: ExpectedPrivateDirectory,
    *,
    _ops: _PrivateTreeOsOps | None = None,
) -> PrivateTreeSeal:
    """Hash, durably seal, and revalidate one exact owner-only private tree."""

    if _ops is None:
        _require_live_private_tree_ops()
        ops = _PrivateTreeOsOps()
    else:
        ops = _ops
    root_name = _bootstrap_basename(root_name, "private tree root name")
    closed_expected = _validated_expected_private_tree(expected)
    owner_uid = ops.getuid()
    if type(owner_uid) is not int or owner_uid < 0:
        raise BootstrapStateError("private tree owner identity is invalid")
    context = _PrivateTreeSealContext()
    root_fd: int | None = None
    try:
        root_fd, opened_identity = _open_private_tree_node_at(
            parent_fd,
            root_name,
            kind="directory",
            owner_uid=owner_uid,
            ops=ops,
            label="private tree root",
        )
        nodes = _seal_open_private_directory(
            root_fd,
            closed_expected,
            relative_path=(),
            owner_uid=owner_uid,
            ops=ops,
            context=context,
        )
        root_seal = nodes[-1]
        if root_seal.identity != opened_identity:
            raise BootstrapStateError("private tree root changed during sealing")
        _verify_private_tree_named_identity(
            parent_fd,
            root_name,
            root_seal.identity,
            ops=ops,
            label="private tree root",
        )
        context.durability_started = True
        ops.fsync(parent_fd)
        _verify_private_tree_named_identity(
            parent_fd,
            root_name,
            root_seal.identity,
            ops=ops,
            label="private tree root",
        )
        root_names = tuple(sorted(closed_expected.children, key=os.fsencode))
        final_root_identity = _stable_private_directory_inventory(
            root_fd,
            root_names,
            owner_uid=owner_uid,
            ops=ops,
            label="private tree root",
        )
        if final_root_identity != root_seal.identity:
            raise BootstrapStateError("private tree root drifted after parent fsync")
        direct = {
            node.relative_path[0]: node.identity
            for node in nodes
            if len(node.relative_path) == 1
        }
        for name in root_names:
            _verify_private_tree_named_identity(
                root_fd,
                name,
                direct[name],
                ops=ops,
                label="private tree root child",
            )
        return PrivateTreeSeal(root_identity=root_seal.identity, nodes=nodes)
    except BootstrapRecoveryRequired:
        raise
    except (BootstrapStateError, OSError) as exc:
        if context.durability_started:
            raise BootstrapRecoveryRequired(
                "private tree sealing requires locked recovery"
            ) from exc
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot seal private tree") from exc
    finally:
        if root_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(root_fd)
            except OSError as exc:
                if context.durability_started and not active_exception:
                    raise BootstrapRecoveryRequired(
                        "private tree descriptor close requires recovery"
                    ) from exc


def _closed_staging_inventory_names(names: Sequence[str]) -> tuple[str, ...]:
    if type(names) not in {tuple, list}:
        raise BootstrapStateError("private staging inventory is invalid")
    validated: list[str] = []
    for name in names:
        try:
            if type(name) is not str or _bootstrap_basename(
                name, "staging inventory name"
            ) != name:
                raise BootstrapStateError("private staging inventory name is invalid")
        except AtomicAcquisitionError as exc:
            raise BootstrapStateError(
                "private staging inventory name is invalid"
            ) from exc
        if name in validated:
            raise BootstrapStateError("private staging inventory name repeats")
        validated.append(name)
    return tuple(sorted(validated, key=os.fsencode))


def _open_private_staging_at(
    parent_fd: int,
    staging_name: str,
    *,
    expected_names: Sequence[str],
    _ops: _PrivateTreeOsOps | None = None,
) -> tuple[int, tuple[int, int, int, int, int, int, int, int]]:
    """Open and pin one exact owner-only staging directory on macOS."""

    if _ops is None:
        _require_live_private_tree_ops()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    names = _closed_staging_inventory_names(expected_names)
    ops = _ops or _PrivateTreeOsOps()
    descriptor: int | None = None
    try:
        descriptor, opened_identity = _open_private_tree_node_at(
            parent_fd,
            staging_name,
            kind="directory",
            owner_uid=ops.getuid(),
            ops=ops,
            label="private staging directory",
        )
        inventory_identity = _stable_private_directory_inventory(
            descriptor,
            names,
            owner_uid=ops.getuid(),
            ops=ops,
            label="private staging directory",
        )
        _verify_private_tree_named_identity(
            parent_fd,
            staging_name,
            inventory_identity,
            ops=ops,
            label="private staging directory",
        )
        if inventory_identity != opened_identity:
            raise BootstrapStateError("private staging directory drifted while opening")
        return descriptor, inventory_identity
    except (BootstrapStateError, OSError) as exc:
        if descriptor is not None:
            try:
                ops.close(descriptor)
            except OSError:
                pass
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot open private staging directory") from exc


def _authorized_initialization_dependency_prefix(
    names: Sequence[str],
) -> tuple[str, ...]:
    """Recognize only snapshot plus one fixed dependency prefix."""

    inventory = _closed_staging_inventory_names(names)
    for length in range(len(INITIALIZATION_DEPENDENCY_FILENAMES) + 1):
        prefix = INITIALIZATION_DEPENDENCY_FILENAMES[:length]
        expected = _closed_staging_inventory_names((SNAPSHOT_FILENAME, *prefix))
        if inventory == expected:
            return prefix
    raise BootstrapStateError(
        "private staging initialization residue is not an authorized prefix"
    )


def _open_private_staging_dependency_prefix_at(
    parent_fd: int,
    staging_name: str,
    *,
    _ops: _PrivateTreeOsOps | None = None,
) -> tuple[
    int,
    tuple[int, int, int, int, int, int, int, int],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Pin one owner-only staging tree with a recognized dependency prefix."""

    if _ops is None:
        _require_live_private_tree_ops()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    ops = _ops or _PrivateTreeOsOps()
    descriptor: int | None = None
    try:
        descriptor, opened_identity = _open_private_tree_node_at(
            parent_fd,
            staging_name,
            kind="directory",
            owner_uid=ops.getuid(),
            ops=ops,
            label="private initialization staging directory",
        )
        names, inventory_identity = _stable_private_directory_names(
            descriptor,
            owner_uid=ops.getuid(),
            ops=ops,
            label="private initialization staging directory",
        )
        prefix = _authorized_initialization_dependency_prefix(names)
        _verify_private_tree_named_identity(
            parent_fd,
            staging_name,
            inventory_identity,
            ops=ops,
            label="private initialization staging directory",
        )
        if inventory_identity != opened_identity:
            raise BootstrapStateError(
                "private initialization staging drifted while opening"
            )
        return descriptor, inventory_identity, prefix, names
    except (BootstrapStateError, OSError) as exc:
        if descriptor is not None:
            try:
                ops.close(descriptor)
            except OSError:
                pass
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError(
            "cannot open private initialization staging"
        ) from exc


def _authorized_initialization_owner_stage_inventory(
    names: Sequence[str],
) -> bool:
    """Accept the closed dependency tree with only an optional owner residue."""

    inventory = _closed_staging_inventory_names(names)
    without_owner = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_DEPENDENCY_FILENAMES)
    )
    with_owner = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )
    if inventory == without_owner:
        return False
    if inventory == with_owner:
        return True
    raise BootstrapStateError(
        "private staging owner residue inventory is invalid"
    )


def _open_private_staging_owner_stage_at(
    parent_fd: int,
    staging_name: str,
    *,
    _ops: _PrivateTreeOsOps | None = None,
) -> tuple[
    int,
    tuple[int, int, int, int, int, int, int, int],
    bool,
    tuple[str, ...],
]:
    """Pin the six-file dependency tree with an optional owner residue."""

    if _ops is None:
        _require_live_private_tree_ops()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    ops = _ops or _PrivateTreeOsOps()
    descriptor: int | None = None
    try:
        descriptor, opened_identity = _open_private_tree_node_at(
            parent_fd,
            staging_name,
            kind="directory",
            owner_uid=ops.getuid(),
            ops=ops,
            label="private owner staging directory",
        )
        names, inventory_identity = _stable_private_directory_names(
            descriptor,
            owner_uid=ops.getuid(),
            ops=ops,
            label="private owner staging directory",
        )
        owner_present = _authorized_initialization_owner_stage_inventory(names)
        _verify_private_tree_named_identity(
            parent_fd,
            staging_name,
            inventory_identity,
            ops=ops,
            label="private owner staging directory",
        )
        if inventory_identity != opened_identity:
            raise BootstrapStateError(
                "private owner staging drifted while opening"
            )
        return descriptor, inventory_identity, owner_present, names
    except (BootstrapStateError, OSError) as exc:
        if descriptor is not None:
            try:
                ops.close(descriptor)
            except OSError:
                pass
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot open private owner staging") from exc


def _create_private_staging_at(
    parent_fd: int,
    staging_name: str,
    *,
    _ops: _PrivateTreeOsOps | None = None,
) -> tuple[int, tuple[int, int, int, int, int, int, int, int]]:
    """Durably create and pin one exact-empty owner-only staging directory."""

    if _ops is None:
        _require_live_private_tree_ops()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    ops = _ops or _PrivateTreeOsOps()
    descriptor: int | None = None
    created = False
    try:
        ops.mkdir(staging_name, 0o700, dir_fd=parent_fd)
        created = True
        descriptor = ops.open(
            staging_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        ops.fchmod(descriptor, 0o700)
        opened = ops.fstat(descriptor)
        named = ops.stat(staging_name, dir_fd=parent_fd)
        _validate_private_tree_inode(
            opened,
            kind="directory",
            owner_uid=ops.getuid(),
            label="private staging directory",
        )
        identity = _private_node_identity(opened)
        if _private_node_identity(named) != identity:
            raise BootstrapStateError("private staging pathname drifted")
        if _stable_private_directory_inventory(
            descriptor,
            (),
            owner_uid=ops.getuid(),
            ops=ops,
            label="private staging directory",
        ) != identity:
            raise BootstrapStateError("private staging directory changed after creation")
        ops.fsync(descriptor)
        ops.fsync(parent_fd)
        durable_identity = _stable_private_directory_inventory(
            descriptor,
            (),
            owner_uid=ops.getuid(),
            ops=ops,
            label="private staging directory",
        )
        durable = ops.fstat(descriptor)
        durable_named = ops.stat(staging_name, dir_fd=parent_fd)
        if (
            durable_identity != identity
            or _private_node_identity(durable) != durable_identity
            or _private_node_identity(durable_named) != durable_identity
        ):
            raise BootstrapStateError("private staging durability verification drifted")
        return descriptor, durable_identity
    except (BootstrapStateError, OSError) as exc:
        if descriptor is not None:
            try:
                ops.close(descriptor)
            except OSError:
                pass
        if created:
            raise BootstrapRecoveryRequired(
                "private staging creation requires locked recovery"
            ) from exc
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot create private staging directory") from exc


def _reset_recognized_snapshot_in_progress_at(
    parent_fd: int,
    staging_fd: int,
    staging_name: str,
    *,
    expected_staging_device_inode: tuple[int, int],
    _before_unlink: Callable[[], None] | None = None,
    _ops: _PrivateTreeOsOps | None = None,
) -> tuple[str, ...]:
    """Durably empty only one structurally recognized partial snapshot tree."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    staging_name = _bootstrap_basename(staging_name, "staging name")

    def verify_staging_name() -> None:
        opened = ops.fstat(staging_fd)
        named = ops.stat(staging_name, dir_fd=parent_fd)
        for info in (opened, named):
            _validate_private_tree_inode(
                info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="snapshot-in-progress staging",
            )
        if (
            _device_inode(opened) != expected_staging_device_inode
            or _device_inode(named) != expected_staging_device_inode
        ):
            raise BootstrapStateError(
                "snapshot-in-progress staging pathname drifted"
            )

    try:
        verify_staging_name()
        names, _identity = _stable_private_directory_names(
            staging_fd,
            owner_uid=ops.getuid(),
            ops=ops,
            label="snapshot-in-progress staging",
        )
        allowed = frozenset(SNAPSHOT_PARTIAL_FILENAMES)
        if any(name not in allowed for name in names):
            raise BootstrapStateError(
                "snapshot-in-progress staging contains an unknown name"
            )
        verified: dict[
            str, tuple[int, int, int, int, int, int, int, int]
        ] = {}
        for name in names:
            descriptor: int | None = None
            try:
                descriptor, identity = _open_private_tree_node_at(
                    staging_fd,
                    name,
                    kind="file",
                    owner_uid=ops.getuid(),
                    ops=ops,
                    label="partial snapshot file",
                )
                verified[name] = identity
            finally:
                if descriptor is not None:
                    ops.close(descriptor)
        _stable_private_directory_inventory(
            staging_fd,
            names,
            owner_uid=ops.getuid(),
            ops=ops,
            label="snapshot-in-progress staging",
        )
        for name, identity in verified.items():
            _verify_private_tree_named_identity(
                staging_fd,
                name,
                identity,
                ops=ops,
                label="partial snapshot file",
            )
        verify_staging_name()
    except BootstrapStateError:
        raise
    except OSError as exc:
        raise BootstrapStateError(
            "cannot validate snapshot-in-progress staging"
        ) from exc
    deletion_order = tuple(
        name for name in names if name != SNAPSHOT_FILENAME
    ) + ((SNAPSHOT_FILENAME,) if SNAPSHOT_FILENAME in names else ())
    remaining = set(names)
    cleanup_started = False
    removed: list[str] = []
    try:
        for name in deletion_order:
            if _before_unlink is not None:
                _before_unlink()
            cleanup_started = True
            _verify_private_tree_named_identity(
                staging_fd,
                name,
                verified[name],
                ops=ops,
                label="partial snapshot file",
            )
            ops.unlink(name, dir_fd=staging_fd)
            removed.append(name)
            remaining.remove(name)
            ops.fsync(staging_fd)
            verify_staging_name()
            expected_remaining = tuple(sorted(remaining, key=os.fsencode))
            _stable_private_directory_inventory(
                staging_fd,
                expected_remaining,
                owner_uid=ops.getuid(),
                ops=ops,
                label="snapshot-in-progress staging",
            )
            for remaining_name in expected_remaining:
                _verify_private_tree_named_identity(
                    staging_fd,
                    remaining_name,
                    verified[remaining_name],
                    ops=ops,
                    label="partial snapshot file",
                )
        verify_staging_name()
    except (BootstrapStateError, OSError) as exc:
        if cleanup_started:
            raise BootstrapRecoveryRequired(
                "partial snapshot cleanup requires locked recovery"
            ) from exc
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot reset partial snapshot") from exc
    return tuple(removed)


def _flock_bootstrap_lock(descriptor: int, *, acquire: bool) -> None:
    if sys.platform != "darwin":
        raise BootstrapStateError("bootstrap flock is available only on macOS")
    import fcntl

    operation = (fcntl.LOCK_EX | fcntl.LOCK_NB) if acquire else fcntl.LOCK_UN
    fcntl.flock(descriptor, operation)


def _acquire_bootstrap_lock_at(parent_fd: int, journal_name: str) -> tuple[int, str]:
    """Acquire a crash-released flock on one persistent owner-only inode."""

    if sys.platform != "darwin":
        raise BootstrapStateError("bootstrap flock is available only on macOS")
    journal_name = _bootstrap_basename(journal_name, "journal name")
    lock_name = f".{journal_name}.lock"
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = os.open(
            lock_name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        named = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or _stat_identity(opened) != _stat_identity(named)
        ):
            raise BootstrapStateError("bootstrap lock inode is invalid")
        _flock_bootstrap_lock(descriptor, acquire=True)
        acquired = True
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, descriptor, lock_name
        )
        os.fsync(descriptor)
        os.fsync(parent_fd)
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, descriptor, lock_name
        )
        return descriptor, lock_name
    except BlockingIOError as exc:
        failure: BootstrapStateError = BootstrapStateError(
            "bootstrap journal lock is already held"
        )
        failure.__cause__ = exc
    except BootstrapStateError as exc:
        failure = exc
    except OSError as exc:
        failure = BootstrapStateError("cannot acquire bootstrap journal lock")
        failure.__cause__ = exc
    if descriptor is not None:
        if acquired:
            try:
                _flock_bootstrap_lock(descriptor, acquire=False)
            except (BootstrapStateError, OSError):
                pass
        try:
            os.close(descriptor)
        except OSError:
            pass
    raise failure


def _release_bootstrap_lock_at(
    parent_fd: int,
    journal_name: str,
    lock_fd: int,
    lock_name: str,
) -> None:
    """Release flock ownership while retaining the stable lock pathname."""

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        _flock_bootstrap_lock(lock_fd, acquire=False)
    except (BootstrapStateError, OSError) as exc:
        raise BootstrapStateError("cannot release bootstrap journal flock") from exc


def _verify_bootstrap_lock_held_at(
    parent_fd: int,
    journal_name: str,
    lock_fd: int,
    lock_name: str,
) -> tuple[int, int, int, int, int]:
    expected_name = f".{_bootstrap_basename(journal_name, 'journal name')}.lock"
    if lock_name != expected_name:
        raise BootstrapStateError("bootstrap held lock name is invalid")
    try:
        _flock_bootstrap_lock(lock_fd, acquire=True)
        opened = os.fstat(lock_fd)
        named = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    except (BlockingIOError, OSError) as exc:
        raise BootstrapStateError("bootstrap held lock cannot be verified") from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or _stat_identity(opened) != _stat_identity(named)
    ):
        raise BootstrapStateError("bootstrap held lock inode is invalid")
    return _stat_identity(opened)


def _advance_bootstrap_journal_locked_at(
    parent_fd: int,
    filename: str,
    payload: dict[str, Any],
    *,
    lock_fd: int,
    lock_name: str,
) -> str:
    """Create or advance the journal while one caller-held lock stays pinned."""

    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable bootstrap state is available only on the macOS host"
        )
    filename = _bootstrap_basename(filename, "journal name")
    current = _validated_bootstrap_journal_payload(payload)
    raw = _canonical_json_bytes(current)
    if len(raw) > MAX_BOOTSTRAP_JOURNAL_BYTES:
        raise BootstrapStateError("bootstrap journal exceeds size ceiling")
    held_identity = _verify_bootstrap_lock_held_at(
        parent_fd, filename, lock_fd, lock_name
    )
    try:
        os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        exists = False
        previous = None
        previous_identity = None
    except OSError as exc:
        raise BootstrapStateError("cannot inspect bootstrap journal name") from exc
    else:
        previous, previous_identity, _ = _read_bootstrap_journal_at(
            parent_fd, filename
        )
        exists = True
    if exists:
        if previous is None or previous_identity is None:
            raise BootstrapStateError("bootstrap prior journal evidence is invalid")
        validate_bootstrap_transition(previous, current)
    elif current.get("state") != "reserved" or current.get(
        "previous_journal_digest"
    ) is not None:
        raise BootstrapStateError("bootstrap journal must begin reserved")
    digest = _durable_atomic_private_file_at(
        parent_fd,
        filename,
        raw,
        replace_existing=exists,
        expected_existing_identity=previous_identity,
    )
    try:
        after_identity = _verify_bootstrap_lock_held_at(
            parent_fd, filename, lock_fd, lock_name
        )
    except BootstrapStateError as exc:
        raise BootstrapRecoveryRequired(
            "bootstrap journal advanced without a verifiable held lock"
        ) from exc
    if after_identity != held_identity:
        raise BootstrapRecoveryRequired(
            "bootstrap held lock drifted during journal advance"
        )
    return digest


def write_bootstrap_journal(path: Path, payload: dict[str, Any]) -> str:
    """Create or advance the external journal under one pinned exclusive lock."""

    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable bootstrap state is available only on the macOS host"
        )
    current = _validated_bootstrap_journal_payload(payload)
    parent_fd, filename = _open_private_parent_dirfd(path)
    lock_fd: int | None = None
    lock_name: str | None = None
    journal_published = False
    try:
        lock_fd, lock_name = _acquire_bootstrap_lock_at(parent_fd, filename)
        digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            filename,
            current,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_published = True
        return digest
    finally:
        try:
            active_exception = sys.exc_info()[0] is not None
            if lock_fd is not None and lock_name is not None:
                try:
                    _release_bootstrap_lock_at(
                        parent_fd, filename, lock_fd, lock_name
                    )
                except BootstrapStateError:
                    if not active_exception:
                        if journal_published:
                            raise BootstrapRecoveryRequired(
                                "published bootstrap journal lock release requires recovery"
                            )
                        raise
        finally:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
            try:
                os.close(parent_fd)
            except OSError:
                pass


def _empty_bootstrap_successor(
    current: dict[str, Any],
    current_digest: str,
    state: str,
) -> dict[str, Any]:
    if state not in {"staging_created", "snapshot_in_progress"}:
        raise BootstrapStateError("empty bootstrap successor is invalid")
    return bootstrap_journal_payload(
        state=state,
        previous_journal_digest=current_digest,
        staging_name=current["staging_name"],
        final_name=current["final_name"],
        semantic_options_digest=current["semantic_options_digest"],
        run_controls_digest=current["run_controls_digest"],
        smoke_policy_digest=None,
        hmac_key_id_value=current["hmac_key_id"],
        snapshot_metadata=None,
        universe_binding=None,
        completed_artifacts={},
    )


def _prepare_bootstrap_snapshot_in_progress_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    *,
    lock_fd: int,
    lock_name: str,
    _ops: _PrivateTreeOsOps | None = None,
) -> PreparedSnapshotInProgress:
    """Create or resume the exact-empty staging boundary under one held lock."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    if reserved["state"] != "reserved":
        raise BootstrapStateError("bootstrap preparer requires reserved payload")
    staging_name = reserved["staging_name"]
    final_name = reserved["final_name"]
    if (
        bootstrap_staging_name(final_name) != staging_name
        or bootstrap_journal_name(final_name) != journal_name
        or Path(staging_path).expanduser().absolute().name != staging_name
    ):
        raise BootstrapStateError("bootstrap preparer path derivation drifted")
    immutable = {
        key: reserved[key]
        for key in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }
    staging_fd: int | None = None
    staging_identity: tuple[int, int, int, int, int, int, int, int] | None = None
    mutation_started = False
    reserved_published_here = False

    def name_exists(name: str) -> bool:
        try:
            ops.stat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BootstrapStateError("cannot classify bootstrap sibling name") from exc
        return True

    def require_current_bindings(current: dict[str, Any]) -> None:
        if any(current.get(key) != value for key, value in immutable.items()):
            raise BootstrapStateError("bootstrap preparer immutable binding drifted")
        if name_exists(final_name):
            raise BootstrapStateError("bootstrap final name exists before promotion")

    def reread_expected(
        expected: dict[str, Any], expected_digest: str
    ) -> tuple[dict[str, Any], str]:
        payload, _identity, digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if payload != expected or digest != expected_digest:
            raise BootstrapStateError("bootstrap preparer journal reread drifted")
        return payload, digest

    def verify_empty_staging() -> None:
        if staging_fd is None or staging_identity is None:
            raise BootstrapStateError("bootstrap preparer staging is not pinned")
        if name_exists(final_name):
            raise BootstrapStateError("bootstrap final name appeared before promotion")
        current_identity = _verify_pinned_staging_binding_at(
            parent_fd,
            staging_fd,
            staging_name,
            Path(staging_path).expanduser().absolute(),
            expected_device_inode=staging_identity[:2],
            expected_names=(),
            ops=ops,
        )
        if current_identity != staging_identity:
            raise BootstrapStateError("bootstrap empty staging identity drifted")

    try:
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        try:
            ops.stat(journal_name, dir_fd=parent_fd)
        except FileNotFoundError:
            if name_exists(staging_name) or name_exists(final_name):
                raise BootstrapStateError(
                    "bootstrap names exist without journal authority"
                )
            current_digest = _advance_bootstrap_journal_locked_at(
                parent_fd,
                journal_name,
                reserved,
                lock_fd=lock_fd,
                lock_name=lock_name,
            )
            mutation_started = True
            reserved_published_here = True
            current, current_digest = reread_expected(reserved, current_digest)
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
        except OSError as exc:
            raise BootstrapStateError("cannot inspect bootstrap journal") from exc
        else:
            current, _identity, current_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
        require_current_bindings(current)
        if current["state"] not in {
            "reserved",
            "staging_created",
            "snapshot_in_progress",
        }:
            raise BootstrapStateError("bootstrap preparer state is not resumable")

        if current["state"] == "reserved":
            staging_exists = name_exists(staging_name)
            if staging_exists and reserved_published_here:
                raise BootstrapStateError(
                    "bootstrap staging appeared during reserved publication"
                )
            if staging_exists:
                staging_fd, staging_identity = _open_private_staging_at(
                    parent_fd,
                    staging_name,
                    expected_names=(),
                    _ops=ops,
                )
            else:
                mutation_started = True
                staging_fd, staging_identity = _create_private_staging_at(
                    parent_fd,
                    staging_name,
                    _ops=ops,
                )
            verify_empty_staging()
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            next_payload = _empty_bootstrap_successor(
                current, current_digest, "staging_created"
            )
            next_digest = _advance_bootstrap_journal_locked_at(
                parent_fd,
                journal_name,
                next_payload,
                lock_fd=lock_fd,
                lock_name=lock_name,
            )
            mutation_started = True
            current, current_digest = reread_expected(next_payload, next_digest)
            verify_empty_staging()
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )

        if current["state"] == "staging_created":
            if staging_fd is None:
                staging_fd, staging_identity = _open_private_staging_at(
                    parent_fd,
                    staging_name,
                    expected_names=(),
                    _ops=ops,
                )
            verify_empty_staging()
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            next_payload = _empty_bootstrap_successor(
                current, current_digest, "snapshot_in_progress"
            )
            next_digest = _advance_bootstrap_journal_locked_at(
                parent_fd,
                journal_name,
                next_payload,
                lock_fd=lock_fd,
                lock_name=lock_name,
            )
            mutation_started = True
            current, current_digest = reread_expected(next_payload, next_digest)
            verify_empty_staging()
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )

        if current["state"] == "snapshot_in_progress":
            if staging_fd is None:
                staging_fd, staging_identity = _open_private_tree_node_at(
                    parent_fd,
                    staging_name,
                    kind="directory",
                    owner_uid=ops.getuid(),
                    ops=ops,
                    label="snapshot-in-progress staging",
                )
            if staging_identity is None:
                raise BootstrapStateError("bootstrap staging identity is missing")
            before_cleanup, _identity, before_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if before_cleanup != current or before_digest != current_digest:
                raise BootstrapStateError("bootstrap cleanup journal drifted")
            removed = _reset_recognized_snapshot_in_progress_at(
                parent_fd,
                staging_fd,
                staging_name,
                expected_staging_device_inode=staging_identity[:2],
                _before_unlink=lambda: _verify_bootstrap_lock_held_at(
                    parent_fd, journal_name, lock_fd, lock_name
                ),
                _ops=ops,
            )
            if removed:
                mutation_started = True
            staging_identity = _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                staging_name,
                Path(staging_path).expanduser().absolute(),
                expected_device_inode=staging_identity[:2],
                expected_names=(),
                ops=ops,
            )
            after_cleanup, _identity, after_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if after_cleanup != current or after_digest != current_digest:
                raise BootstrapStateError("bootstrap cleanup journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            verify_empty_staging()
            result = PreparedSnapshotInProgress(
                journal=current,
                journal_digest=current_digest,
                staging_fd=staging_fd,
                staging_identity=staging_identity,
                staging_device_inode=staging_identity[:2],
            )
            staging_fd = None
            return result
        raise BootstrapStateError("bootstrap preparer did not reach snapshot state")
    except BootstrapRecoveryRequired:
        raise
    except (BootstrapStateError, SnapshotError, OSError) as exc:
        if mutation_started:
            raise BootstrapRecoveryRequired(
                "bootstrap snapshot preparation requires locked recovery"
            ) from exc
        if isinstance(exc, (BootstrapStateError, SnapshotError)):
            raise
        raise BootstrapStateError("cannot prepare bootstrap snapshot") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    if mutation_started:
                        raise BootstrapRecoveryRequired(
                            "bootstrap staging close requires recovery"
                        ) from exc
                    raise BootstrapStateError(
                        "bootstrap staging close failed"
                    ) from exc


def _stream_hash_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise SnapshotError("cannot stream-hash immutable snapshot") from exc
    return "sha256:" + digest.hexdigest(), size


def _sqlite_affinity(declared_type: str) -> str:
    declared = (declared_type or "").upper()
    if "INT" in declared:
        return "INTEGER"
    if any(token in declared for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in declared or not declared:
        return "BLOB"
    if any(token in declared for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SnapshotError("cannot inspect private snapshot path") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _require_private_destination(path: Path) -> None:
    expanded = path.expanduser()
    if ".." in expanded.parts:
        raise SnapshotError("snapshot destination contains parent traversal")
    absolute = expanded.absolute()
    indices = [
        index
        for index, part in enumerate(absolute.parts)
        if part == PRIVATE_ROOT_COMPONENT
    ]
    if not indices:
        raise SnapshotError("snapshot destination is outside the required private root")
    private_index = indices[-1]
    private_root = Path(*absolute.parts[: private_index + 1])
    if not private_root.is_dir() or _is_reparse_or_symlink(private_root):
        raise SnapshotError("required private root is missing or indirected")
    try:
        resolved_root = private_root.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError("cannot resolve required private root") from exc
    current = private_root
    last_existing = private_root
    for part in absolute.parts[private_index + 1 :]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            break
        if _is_reparse_or_symlink(current):
            raise SnapshotError("private snapshot path contains an indirection")
        last_existing = current
    try:
        resolved_existing = last_existing.resolve(strict=True)
        resolved_existing.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise SnapshotError("snapshot destination escapes the required private root") from exc


def _require_owner_only_mode(path: Path, expected: int) -> None:
    if os.name == "nt":
        return
    try:
        info = path.stat()
    except OSError as exc:
        raise SnapshotError("cannot inspect private snapshot permissions") from exc
    if stat.S_IMODE(info.st_mode) != expected or info.st_uid != os.getuid():
        raise SnapshotError("private snapshot permissions or ownership are invalid")


def _open_read_only_database(path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True
        )
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only != (1,):
            connection.close()
            raise SnapshotError("SQLite read-only connection is not query-only")
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise SnapshotError("cannot open SQLite source read-only") from exc


def _open_immutable_read_only_database(path: Path) -> sqlite3.Connection:
    """Open a closed sidecar-free SQLite copy without creating WAL/SHM files."""

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            connection.close()
            raise SnapshotError("SQLite immutable connection is not query-only")
        return connection
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise SnapshotError("cannot open immutable SQLite source") from exc


def _quick_check(conn: sqlite3.Connection) -> None:
    try:
        rows = list(conn.execute("PRAGMA quick_check"))
    except sqlite3.Error as exc:
        raise SnapshotError("SQLite quick_check failed") from exc
    if rows != [("ok",)]:
        raise SnapshotError("SQLite quick_check did not return exactly ok")


def _snapshot_sidecars(snapshot: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = snapshot.with_name(snapshot.name + suffix)
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SnapshotError("cannot inspect immutable snapshot sidecars") from exc
        found.append(candidate)
    return tuple(found)


def _reject_snapshot_sidecars(snapshot: Path) -> None:
    if _snapshot_sidecars(snapshot):
        raise SnapshotError("immutable snapshot has unexpected SQLite sidecars")


def _schema_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    try:
        rows = conn.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') "
            "FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name, tbl_name, sql"
        )
        return [
            {
                "type": str(kind),
                "name": str(name),
                "table": str(table),
                "sql": str(sql),
            }
            for kind, name, table, sql in rows
        ]
    except sqlite3.Error as exc:
        raise SnapshotError("cannot fingerprint SQLite schema") from exc


def _table_xinfo_rows(
    conn: sqlite3.Connection, table: str
) -> list[dict[str, object]]:
    try:
        return [
            {
                "cid": int(row[0]),
                "name": str(row[1]),
                "declared_type": str(row[2] or ""),
                "not_null": int(row[3]),
                "default": row[4],
                "primary_key": int(row[5]),
                "hidden": int(row[6]),
            }
            for row in conn.execute(f'PRAGMA table_xinfo("{table}")')
        ]
    except (TypeError, ValueError, sqlite3.Error) as exc:
        raise SnapshotError("cannot fingerprint SQLite table columns") from exc


def _schema_fingerprint(conn: sqlite3.Connection) -> str:
    payload = {
        "schema_rows": _schema_rows(conn),
        "required_table_xinfo": {
            table: _table_xinfo_rows(conn, table)
            for table in sorted(REQUIRED_SCHEMA_AFFINITIES)
        },
    }
    return _sha256_tag(_canonical_json_bytes(payload))


def _snapshot_metadata_from_hash(
    conn: sqlite3.Connection,
    *,
    file_hash: str,
    byte_size: int,
) -> SnapshotMetadata:
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()
        page_count = conn.execute("PRAGMA page_count").fetchone()
        user_version = conn.execute("PRAGMA user_version").fetchone()
        application_id = conn.execute("PRAGMA application_id").fetchone()
    except (OSError, sqlite3.Error) as exc:
        raise SnapshotError("cannot record SQLite snapshot metadata") from exc
    scalar_rows = (page_size, page_count, user_version, application_id)
    if any(
        type(row) is not tuple
        or len(row) != 1
        or type(row[0]) is not int
        for row in scalar_rows
    ):
        raise SnapshotError("SQLite snapshot metadata is malformed")
    return SnapshotMetadata(
        schema="setec-imessage-atomic-snapshot-metadata/1",
        file_sha256=file_hash,
        byte_size=byte_size,
        page_size=page_size[0],
        page_count=page_count[0],
        schema_fingerprint=_schema_fingerprint(conn),
        sqlite_user_version=user_version[0],
        sqlite_application_id=application_id[0],
        sqlite_library_version=sqlite3.sqlite_version,
    )


def _snapshot_metadata(
    conn: sqlite3.Connection, snapshot_path: Path
) -> SnapshotMetadata:
    file_hash, byte_size = _stream_hash_and_size(snapshot_path)
    return _snapshot_metadata_from_hash(
        conn, file_hash=file_hash, byte_size=byte_size
    )


def _snapshot_metadata_matches_creator_binding(
    observed: SnapshotMetadata,
    expected: SnapshotMetadata,
) -> bool:
    """Compare DB-intrinsic evidence while preserving creator provenance.

    ``sqlite_library_version`` describes the inspecting runtime, not bytes
    embedded in the database.  It may therefore differ cross-runtime only
    after every byte- and database-intrinsic field has matched exactly.
    """

    if type(observed) is not SnapshotMetadata or type(expected) is not SnapshotMetadata:
        return False
    return replace(
        observed,
        sqlite_library_version=expected.sqlite_library_version,
    ) == expected


def materialize_consistent_snapshot(
    source_db: Path, staging_dir: Path
) -> tuple[Path, SnapshotMetadata]:
    """Create and verify one consistent private snapshot using SQLite backup.

    This primitive stops before run-owner creation and promotion; those
    bootstrap-transaction steps are implemented in the next tranche. The new
    staging directory must not already exist.
    """

    staging = staging_dir.expanduser().absolute()
    _require_private_destination(staging)
    if staging.exists():
        raise SnapshotError("snapshot staging directory already exists")
    try:
        staging.mkdir(parents=False, mode=0o700)
        os.chmod(staging, 0o700)
    except OSError as exc:
        raise SnapshotError("cannot create private snapshot staging directory") from exc
    return _materialize_consistent_snapshot_in_precreated_staging(
        source_db, staging
    )


def _materialize_consistent_snapshot_in_precreated_staging(
    source_db: Path, staging_dir: Path
) -> tuple[Path, SnapshotMetadata]:
    """Back up into one existing, exact-empty private staging directory."""

    source = source_db.expanduser().absolute()
    staging = staging_dir.expanduser().absolute()
    _require_private_destination(staging)
    if (
        not staging.is_dir()
        or _is_reparse_or_symlink(staging)
        or not source.is_file()
        or _is_reparse_or_symlink(source)
    ):
        raise SnapshotError(
            "snapshot staging and SQLite source must be existing non-indirected paths"
        )
    _require_owner_only_mode(staging, 0o700)
    try:
        if tuple(staging.iterdir()):
            raise SnapshotError("precreated snapshot staging directory is not empty")
    except OSError as exc:
        raise SnapshotError("cannot inspect precreated snapshot staging directory") from exc
    snapshot = staging / SNAPSHOT_FILENAME
    try:
        descriptor = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(snapshot, 0o600)
    except OSError as exc:
        raise SnapshotError("cannot securely create private snapshot file") from exc
    _require_owner_only_mode(snapshot, 0o600)
    source_conn: sqlite3.Connection | None = None
    destination_conn: sqlite3.Connection | None = None
    try:
        source_conn = _open_read_only_database(source)
        destination_conn = sqlite3.connect(snapshot)
        source_conn.backup(destination_conn, pages=128, sleep=0.0)
        destination_conn.commit()
        journal_mode = destination_conn.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone()
        if journal_mode != ("delete",):
            raise SnapshotError("immutable snapshot journal mode is not single-file")
        _quick_check(destination_conn)
        metadata = _snapshot_metadata(destination_conn, snapshot)
    except (OSError, sqlite3.Error, SnapshotError) as exc:
        if isinstance(exc, SnapshotError):
            raise
        raise SnapshotError("SQLite backup snapshot failed") from exc
    finally:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()
    _reject_snapshot_sidecars(snapshot)
    verify_snapshot(snapshot, metadata)
    return snapshot, metadata


def _materialize_consistent_snapshot_in_precreated_staging_at(
    parent_fd: int,
    staging_fd: int,
    staging_name: str,
    staging_path: Path,
    source_db: Path,
    *,
    expected_staging_device_inode: tuple[int, int],
    _ops: _PrivateTreeOsOps | None = None,
    _source_opener: Callable[[Path], Any] | None = None,
    _destination_opener: Callable[[Path], Any] | None = None,
    _snapshot_opener: Callable[[Path], Any] | None = None,
    _connection_binder: Callable[
        [Callable[[Path], Any], Path, tuple[int, int], str], Any
    ]
    | None = None,
) -> ClosedSnapshotEvidence:
    """Close one SQLite backup against pinned staging and snapshot inodes."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    staging = Path(staging_path).expanduser().absolute()
    source = Path(source_db).expanduser().absolute()
    if staging.name != staging_name or ".." in Path(staging_path).parts:
        raise BootstrapStateError("private staging path binding is invalid")
    if _ops is None:
        _require_private_destination(staging)
    if (
        type(expected_staging_device_inode) is not tuple
        or len(expected_staging_device_inode) != 2
        or any(type(value) is not int or value < 0 for value in expected_staging_device_inode)
    ):
        raise BootstrapStateError("private staging inode binding is invalid")
    source_opener = _source_opener or _open_read_only_database
    destination_opener = _destination_opener or sqlite3.connect
    snapshot_opener = _snapshot_opener or _open_read_only_database
    connection_binder = _connection_binder or _open_inode_bound_sqlite_connection
    snapshot_path = staging / SNAPSHOT_FILENAME
    closed_run_reader: Any = None
    source_fd: int | None = None
    snapshot_fd: int | None = None
    source_conn: Any = None
    destination_conn: Any = None
    snapshot_conn: Any = None
    snapshot_created = False
    exact_reuse = source.name == SNAPSHOT_FILENAME
    closed_root_identity: tuple[int, int, int, int, int, int, int, int] | None = None
    closed_source_identity: tuple[int, int, int, int, int, int, int, int] | None = None
    closed_source_hash: str | None = None
    closed_source_size: int | None = None

    def validate_closed_run_evidence() -> tuple[str, int]:
        if closed_run_reader is None:
            raise SnapshotError("closed snapshot reader is unavailable")
        try:
            validate_atomic_run(source.parent, io=closed_run_reader)
            owner, _ = _read_io_object(
                closed_run_reader, RUN_OWNER_FILENAME, "closed source owner"
            )
            smoke, _ = _read_io_object(
                closed_run_reader, SMOKE_POLICY_FILENAME, "closed source policy"
            )
            receipt, _ = _read_io_object(
                closed_run_reader,
                "acquisition-receipt.json",
                "closed source receipt",
            )
            snapshot_metadata = smoke.get("snapshot_metadata")
            expected_hash = owner.get("snapshot_file_sha256")
            expected_size = (
                snapshot_metadata.get("byte_size")
                if type(snapshot_metadata) is dict
                else None
            )
            if (
                not _is_sha256_tag(expected_hash)
                or type(expected_size) is not int
                or expected_size < 0
                or snapshot_metadata.get("file_sha256") != expected_hash
                or receipt.get("snapshot_file_sha256") != expected_hash
            ):
                raise SnapshotError("closed source snapshot evidence drifted")
            return expected_hash, expected_size
        except SnapshotError:
            raise
        except AtomicAcquisitionError as exc:
            raise SnapshotError("closed atomic source run validation failed") from exc

    def verify_closed_source_binding() -> None:
        if (
            closed_run_reader is None
            or closed_run_reader.final_fd is None
            or source_fd is None
            or closed_root_identity is None
            or closed_source_identity is None
        ):
            raise SnapshotError("closed source binding is incomplete")
        closed_run_reader._verify_root()
        root_opened = ops.fstat(closed_run_reader.final_fd)
        if _private_node_identity(root_opened) != closed_root_identity:
            raise SnapshotError("closed source run root drifted")
        opened = ops.fstat(source_fd)
        named = ops.stat(SNAPSHOT_FILENAME, dir_fd=closed_run_reader.final_fd)
        absolute = ops.stat_path(source)
        for info in (opened, named, absolute):
            _validate_private_tree_inode(
                info,
                kind="file",
                owner_uid=ops.getuid(),
                label="closed source snapshot",
            )
            if _private_node_identity(info) != closed_source_identity:
                raise SnapshotError("closed source snapshot binding drifted")
    try:
        _verify_pinned_staging_binding_at(
            parent_fd,
            staging_fd,
            staging_name,
            staging,
            expected_device_inode=expected_staging_device_inode,
            expected_names=(),
            ops=ops,
        )
        source_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        if exact_reuse:
            try:
                closed_run_reader = _PrivateReadOnlyRowIo(source.parent)
                assert closed_run_reader.final_fd is not None
                closed_root_identity = _private_node_identity(
                    ops.fstat(closed_run_reader.final_fd)
                )
                source_fd = ops.open(
                    SNAPSHOT_FILENAME,
                    source_flags,
                    dir_fd=closed_run_reader.final_fd,
                )
            except (OSError, AtomicAcquisitionError) as exc:
                raise SnapshotError("cannot pin closed atomic source run") from exc
        else:
            source_fd = ops.open_path(source, source_flags)
        source_opened = ops.fstat(source_fd)
        if not stat.S_ISREG(source_opened.st_mode):
            raise SnapshotError("SQLite source is not a regular file")
        source_device_inode = _device_inode(source_opened)
        if exact_reuse:
            _validate_private_tree_inode(
                source_opened,
                kind="file",
                owner_uid=ops.getuid(),
                label="closed source snapshot",
            )
            closed_source_identity = _private_node_identity(source_opened)
            verify_closed_source_binding()
            expected_hash, expected_size = validate_closed_run_evidence()
            verify_closed_source_binding()
            closed_source_hash, closed_source_size, hashed_identity = (
                _stream_hash_private_fd(source_fd, ops=ops)
            )
            if (
                hashed_identity != closed_source_identity
                or closed_source_hash != expected_hash
                or closed_source_size != expected_size
            ):
                raise SnapshotError("closed source snapshot evidence drifted")
            verify_closed_source_binding()
        else:
            _verify_pinned_source_binding(
                source_fd,
                source,
                expected_device_inode=source_device_inode,
                ops=ops,
            )
        snapshot_fd = ops.open(
            SNAPSHOT_FILENAME,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=staging_fd,
        )
        snapshot_created = True
        ops.fchmod(snapshot_fd, 0o600)
        snapshot_opened = ops.fstat(snapshot_fd)
        _validate_private_tree_inode(
            snapshot_opened,
            kind="file",
            owner_uid=ops.getuid(),
            label="private snapshot file",
        )
        snapshot_device_inode = _device_inode(snapshot_opened)

        def guard() -> tuple[
            tuple[int, int, int, int, int, int, int, int],
            tuple[int, int, int, int, int, int, int, int],
        ]:
            staging_identity = _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                staging_name,
                staging,
                expected_device_inode=expected_staging_device_inode,
                expected_names=(SNAPSHOT_FILENAME,),
                ops=ops,
            )
            snapshot_identity = _verify_pinned_snapshot_binding_at(
                staging_fd,
                snapshot_fd,
                snapshot_path,
                expected_device_inode=snapshot_device_inode,
                ops=ops,
            )
            if exact_reuse:
                verify_closed_source_binding()
            else:
                _verify_pinned_source_binding(
                    source_fd,
                    source,
                    expected_device_inode=source_device_inode,
                    ops=ops,
                )
            return staging_identity, snapshot_identity

        guard()
        if exact_reuse:
            if closed_source_hash is None or closed_source_size is None:
                raise SnapshotError("closed source snapshot evidence is incomplete")
            ops.seek(source_fd, 0, os.SEEK_SET)
            remaining = closed_source_size
            while remaining:
                chunk = ops.read(source_fd, min(1024 * 1024, remaining))
                if type(chunk) is not bytes or not chunk:
                    raise SnapshotError("closed source snapshot copy was incomplete")
                view = memoryview(chunk)
                written = 0
                while written < len(view):
                    count = ops.write(snapshot_fd, view[written:])
                    if type(count) is not int or not 0 < count <= len(view) - written:
                        raise SnapshotError(
                            "closed source snapshot write was incomplete"
                        )
                    written += count
                remaining -= len(chunk)
            if ops.read(source_fd, 1):
                raise SnapshotError("closed source snapshot size drifted")
            guard()
            ops.fsync(snapshot_fd)
            ops.fsync(staging_fd)
            guard()
            destination_hash, destination_size, destination_identity = (
                _stream_hash_private_fd(snapshot_fd, ops=ops)
            )
            post_source_hash, post_source_size, post_source_identity = (
                _stream_hash_private_fd(source_fd, ops=ops)
            )
            if (
                destination_hash != closed_source_hash
                or destination_size != closed_source_size
                or post_source_hash != closed_source_hash
                or post_source_size != closed_source_size
                or post_source_identity != closed_source_identity
            ):
                raise SnapshotError("closed source snapshot copy drifted")
            guard()
            snapshot_conn = connection_binder(
                snapshot_opener,
                snapshot_path,
                snapshot_device_inode,
                "SQLite verifier",
            )
            guard()
            _quick_check(snapshot_conn)
            metadata = _snapshot_metadata_from_hash(
                snapshot_conn,
                file_hash=destination_hash,
                byte_size=destination_size,
            )
            guard()
            snapshot_conn.close()
            snapshot_conn = None
            ops.fsync(snapshot_fd)
            ops.fsync(staging_fd)
            guard()
            expected_hash, expected_size = validate_closed_run_evidence()
            guard()
            final_hash, final_size, final_identity = _stream_hash_private_fd(
                snapshot_fd, ops=ops
            )
            final_source_hash, final_source_size, final_source_identity = (
                _stream_hash_private_fd(source_fd, ops=ops)
            )
            staging_identity, snapshot_identity = guard()
            if (
                expected_hash != closed_source_hash
                or expected_size != closed_source_size
                or final_hash != closed_source_hash
                or final_size != closed_source_size
                or final_identity != destination_identity
                or snapshot_identity != final_identity
                or final_source_hash != closed_source_hash
                or final_source_size != closed_source_size
                or final_source_identity != closed_source_identity
                or metadata.file_sha256 != closed_source_hash
                or metadata.byte_size != closed_source_size
            ):
                raise SnapshotError("closed source snapshot reuse evidence drifted")
            return ClosedSnapshotEvidence(
                metadata=metadata,
                snapshot_identity=snapshot_identity,
                staging_identity=staging_identity,
                snapshot_device_inode=snapshot_device_inode,
                staging_device_inode=expected_staging_device_inode,
                inventory=(SNAPSHOT_FILENAME,),
            )

        source_conn = connection_binder(
            source_opener,
            source,
            source_device_inode,
            "SQLite source",
        )
        guard()
        destination_conn = connection_binder(
            destination_opener,
            snapshot_path,
            snapshot_device_inode,
            "SQLite destination",
        )
        guard()
        source_conn.backup(destination_conn, pages=128, sleep=0.0)
        destination_conn.commit()
        journal_mode = destination_conn.execute(
            "PRAGMA journal_mode=DELETE"
        ).fetchone()
        if journal_mode != ("delete",):
            raise SnapshotError("immutable snapshot journal mode is not single-file")
        _quick_check(destination_conn)
        guard()
        destination_conn.close()
        destination_conn = None
        source_conn.close()
        source_conn = None
        guard()
        ops.fsync(snapshot_fd)
        first_hash, first_size, first_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        ops.fsync(staging_fd)
        guard()
        snapshot_conn = connection_binder(
            snapshot_opener,
            snapshot_path,
            snapshot_device_inode,
            "SQLite verifier",
        )
        guard()
        _quick_check(snapshot_conn)
        metadata = _snapshot_metadata_from_hash(
            snapshot_conn,
            file_hash=first_hash,
            byte_size=first_size,
        )
        guard()
        snapshot_conn.close()
        snapshot_conn = None
        staging_identity, snapshot_identity = guard()
        second_hash, second_size, second_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        staging_identity, snapshot_identity = guard()
        if (
            second_hash != first_hash
            or second_size != first_size
            or second_identity != first_identity
            or snapshot_identity != second_identity
            or metadata.file_sha256 != second_hash
            or metadata.byte_size != second_size
        ):
            raise SnapshotError("closed snapshot evidence drifted")
        return ClosedSnapshotEvidence(
            metadata=metadata,
            snapshot_identity=snapshot_identity,
            staging_identity=staging_identity,
            snapshot_device_inode=snapshot_device_inode,
            staging_device_inode=expected_staging_device_inode,
            inventory=(SNAPSHOT_FILENAME,),
        )
    except (OSError, sqlite3.Error, AtomicAcquisitionError) as exc:
        if snapshot_created:
            raise BootstrapRecoveryRequired(
                "private snapshot materialization requires locked recovery"
            ) from exc
        if isinstance(exc, (SnapshotError, BootstrapStateError)):
            raise
        if exact_reuse:
            raise SnapshotError("closed atomic source run validation failed") from exc
        raise SnapshotError("cannot begin pinned SQLite snapshot") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failure: BaseException | None = None
        for connection in (snapshot_conn, destination_conn, source_conn):
            if connection is not None:
                try:
                    connection.close()
                except (OSError, sqlite3.Error) as exc:
                    if close_failure is None:
                        close_failure = exc
        for descriptor in (snapshot_fd, source_fd):
            if descriptor is not None:
                try:
                    ops.close(descriptor)
                except OSError as exc:
                    if close_failure is None:
                        close_failure = exc
        if closed_run_reader is not None:
            try:
                closed_run_reader.close()
            except OSError as exc:
                if close_failure is None:
                    close_failure = exc
        if close_failure is not None and not active_exception:
            if snapshot_created:
                raise BootstrapRecoveryRequired(
                    "private snapshot close requires recovery"
                ) from close_failure
            raise SnapshotError("SQLite source close failed") from close_failure


def _verify_existing_closed_snapshot_at(
    parent_fd: int,
    staging_fd: int,
    staging_name: str,
    staging_path: Path,
    expected_metadata: SnapshotMetadata,
    *,
    expected_staging_device_inode: tuple[int, int],
    expected_staging_names: Sequence[str] = (SNAPSHOT_FILENAME,),
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_opener: Callable[[Path], Any] | None = None,
    _connection_binder: Callable[
        [Callable[[Path], Any], Path, tuple[int, int], str], Any
    ]
    | None = None,
) -> ClosedSnapshotEvidence:
    """Revalidate, without rebuilding, one journal-bound closed snapshot."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    staging = Path(staging_path).expanduser().absolute()
    if staging.name != staging_name:
        raise BootstrapStateError("closed snapshot staging path is invalid")
    if _ops is None:
        _require_private_destination(staging)
    if type(expected_metadata) is not SnapshotMetadata:
        raise BootstrapStateError("closed snapshot metadata type is invalid")
    validated_metadata = _validated_bootstrap_snapshot(
        snapshot_metadata_payload(expected_metadata)
    )
    expected_inventory = _closed_staging_inventory_names(expected_staging_names)
    if SNAPSHOT_FILENAME not in expected_inventory:
        raise BootstrapStateError("closed snapshot inventory omits snapshot")
    snapshot_path = staging / SNAPSHOT_FILENAME
    snapshot_fd: int | None = None
    snapshot_conn: Any = None
    try:
        staging_identity = _verify_pinned_staging_binding_at(
            parent_fd,
            staging_fd,
            staging_name,
            staging,
            expected_device_inode=expected_staging_device_inode,
            expected_names=expected_inventory,
            ops=ops,
        )
        snapshot_fd, opened_identity = _open_private_tree_node_at(
            staging_fd,
            SNAPSHOT_FILENAME,
            kind="file",
            owner_uid=ops.getuid(),
            ops=ops,
            label="closed snapshot file",
        )
        snapshot_device_inode = opened_identity[:2]

        def guard() -> tuple[
            tuple[int, int, int, int, int, int, int, int],
            tuple[int, int, int, int, int, int, int, int],
        ]:
            current_staging = _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                staging_name,
                staging,
                expected_device_inode=expected_staging_device_inode,
                expected_names=expected_inventory,
                ops=ops,
            )
            current_snapshot = _verify_pinned_snapshot_binding_at(
                staging_fd,
                snapshot_fd,
                snapshot_path,
                expected_device_inode=snapshot_device_inode,
                ops=ops,
            )
            return current_staging, current_snapshot

        staging_identity, snapshot_identity = guard()
        ops.fsync(snapshot_fd)
        first_hash, first_size, first_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        if (
            first_hash != validated_metadata["file_sha256"]
            or first_size != validated_metadata["byte_size"]
            or first_identity != snapshot_identity
        ):
            raise BootstrapStateError("closed snapshot bytes drifted")
        ops.fsync(staging_fd)
        guard()
        opener = _snapshot_opener or _open_read_only_database
        binder = _connection_binder or _open_inode_bound_sqlite_connection
        snapshot_conn = binder(
            opener,
            snapshot_path,
            snapshot_device_inode,
            "closed SQLite verifier",
        )
        guard()
        _quick_check(snapshot_conn)
        recomputed = _snapshot_metadata_from_hash(
            snapshot_conn,
            file_hash=first_hash,
            byte_size=first_size,
        )
        if snapshot_metadata_payload(recomputed) != validated_metadata:
            raise BootstrapStateError("closed snapshot metadata drifted")
        guard()
        snapshot_conn.close()
        snapshot_conn = None
        staging_identity, snapshot_identity = guard()
        second_hash, second_size, second_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        staging_identity, snapshot_identity = guard()
        if (
            second_hash != first_hash
            or second_size != first_size
            or second_identity != first_identity
            or snapshot_identity != second_identity
        ):
            raise BootstrapStateError("closed snapshot verification drifted")
        return ClosedSnapshotEvidence(
            metadata=expected_metadata,
            snapshot_identity=snapshot_identity,
            staging_identity=staging_identity,
            snapshot_device_inode=snapshot_device_inode,
            staging_device_inode=expected_staging_device_inode,
            inventory=expected_inventory,
        )
    except (OSError, sqlite3.Error, SnapshotError, BootstrapStateError) as exc:
        if isinstance(exc, BootstrapStateError):
            raise
        raise BootstrapStateError("cannot verify closed snapshot") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failure: BaseException | None = None
        if snapshot_conn is not None:
            try:
                snapshot_conn.close()
            except (OSError, sqlite3.Error) as exc:
                close_failure = exc
        if snapshot_fd is not None:
            try:
                ops.close(snapshot_fd)
            except OSError as exc:
                if close_failure is None:
                    close_failure = exc
        if close_failure is not None and not active_exception:
            raise BootstrapStateError("closed snapshot close failed") from close_failure


def _validated_closed_snapshot_evidence(
    evidence: ClosedSnapshotEvidence,
    *,
    expected_staging_device_inode: tuple[int, int],
    expected_inventory: Sequence[str] = (SNAPSHOT_FILENAME,),
) -> ClosedSnapshotEvidence:
    inventory = _closed_staging_inventory_names(expected_inventory)
    if (
        type(evidence) is not ClosedSnapshotEvidence
        or type(evidence.metadata) is not SnapshotMetadata
        or type(evidence.snapshot_identity) is not tuple
        or len(evidence.snapshot_identity) != 8
        or type(evidence.staging_identity) is not tuple
        or len(evidence.staging_identity) != 8
        or type(evidence.snapshot_device_inode) is not tuple
        or len(evidence.snapshot_device_inode) != 2
        or type(evidence.staging_device_inode) is not tuple
        or len(evidence.staging_device_inode) != 2
        or any(
            type(value) is not int
            for value in (
                *evidence.snapshot_identity,
                *evidence.staging_identity,
                *evidence.snapshot_device_inode,
                *evidence.staging_device_inode,
            )
        )
    ):
        raise BootstrapStateError("closed snapshot evidence type is invalid")
    metadata = _validated_bootstrap_snapshot(
        snapshot_metadata_payload(evidence.metadata)
    )
    if (
        evidence.inventory != inventory
        or SNAPSHOT_FILENAME not in inventory
        or evidence.staging_device_inode != expected_staging_device_inode
        or evidence.staging_identity[:2] != expected_staging_device_inode
        or evidence.snapshot_identity[:2] != evidence.snapshot_device_inode
        or evidence.snapshot_identity[2] != metadata["byte_size"]
        or metadata["file_sha256"] != evidence.metadata.file_sha256
    ):
        raise BootstrapStateError("closed snapshot evidence binding is invalid")
    return evidence


def _closed_snapshot_identity_from_seal(
    seal: PrivateTreeSeal,
    evidence: ClosedSnapshotEvidence,
) -> tuple[int, int, int, int, int, int, int, int]:
    if type(seal) is not PrivateTreeSeal or type(seal.nodes) is not tuple:
        raise BootstrapStateError("closed snapshot tree seal is invalid")
    matches = [
        node
        for node in seal.nodes
        if type(node) is PrivateNodeSeal
        and node.relative_path == (SNAPSHOT_FILENAME,)
        and node.kind == "file"
    ]
    if (
        len(matches) != 1
        or matches[0].byte_size != evidence.metadata.byte_size
        or matches[0].sha256 != evidence.metadata.file_sha256
        or matches[0].identity != evidence.snapshot_identity
    ):
        raise BootstrapStateError("closed snapshot seal inode is unbound")
    return matches[0].identity


def _close_bootstrap_snapshot_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_fd: int,
    staging_path: Path,
    source_db: Path,
    expected_staging_device_inode: tuple[int, int],
) -> tuple[dict[str, Any], str, ClosedSnapshotEvidence]:
    """Materialize and journal the sole snapshot_closed transition."""

    journal_name = _bootstrap_basename(journal_name, "journal name")
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    current, _journal_identity, current_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if current["state"] != "snapshot_in_progress":
        raise BootstrapStateError(
            "bootstrap snapshot close requires snapshot_in_progress"
        )
    staging_name = current["staging_name"]
    if Path(staging_path).expanduser().absolute().name != staging_name:
        raise BootstrapStateError("bootstrap staging pathname binding drifted")
    evidence = _materialize_consistent_snapshot_in_precreated_staging_at(
        parent_fd,
        staging_fd,
        staging_name,
        staging_path,
        source_db,
        expected_staging_device_inode=expected_staging_device_inode,
    )
    journal_advanced = False
    try:
        evidence = _validated_closed_snapshot_evidence(
            evidence,
            expected_staging_device_inode=expected_staging_device_inode,
        )
        expected_tree = ExpectedPrivateDirectory(
            children={
                SNAPSHOT_FILENAME: ExpectedPrivateFile(
                    byte_size=evidence.metadata.byte_size,
                    sha256=evidence.metadata.file_sha256,
                )
            }
        )
        before_seal = seal_private_tree_at(
            parent_fd, staging_name, expected_tree
        )
        if before_seal.root_identity[:2] != expected_staging_device_inode:
            raise BootstrapStateError("bootstrap staging inode drifted before close")
        before_snapshot_identity = _closed_snapshot_identity_from_seal(
            before_seal, evidence
        )
        closed = bootstrap_journal_payload(
            state="snapshot_closed",
            previous_journal_digest=current_digest,
            staging_name=staging_name,
            final_name=current["final_name"],
            semantic_options_digest=current["semantic_options_digest"],
            run_controls_digest=current["run_controls_digest"],
            smoke_policy_digest=None,
            hmac_key_id_value=current["hmac_key_id"],
            snapshot_metadata=snapshot_metadata_payload(evidence.metadata),
            universe_binding=None,
            completed_artifacts={
                SNAPSHOT_FILENAME: evidence.metadata.file_sha256
            },
        )
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            closed,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        reread, _reread_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != closed or reread_digest != published_digest:
            raise BootstrapStateError("published snapshot journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        after_seal = seal_private_tree_at(
            parent_fd, staging_name, expected_tree
        )
        if after_seal.root_identity[:2] != expected_staging_device_inode:
            raise BootstrapStateError("bootstrap staging inode drifted after close")
        after_snapshot_identity = _closed_snapshot_identity_from_seal(
            after_seal, evidence
        )
        if after_snapshot_identity != before_snapshot_identity:
            raise BootstrapStateError("bootstrap snapshot inode drifted across close")
        return closed, published_digest, evidence
    except BootstrapRecoveryRequired:
        raise
    except (BootstrapStateError, SnapshotError, OSError) as exc:
        phase = "published" if journal_advanced else "materialized"
        raise BootstrapRecoveryRequired(
            f"{phase} bootstrap snapshot requires locked recovery"
        ) from exc


def _resume_bootstrap_snapshot_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    _ops: _PrivateTreeOsOps | None = None,
) -> PreparedSnapshotClosed:
    """Pin and revalidate an already authoritative snapshot_closed state."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    journal_name = _bootstrap_basename(journal_name, "journal name")
    try:
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError) as exc:
        raise BootstrapRecoveryRequired(
            "final-tree authority requires locked recovery"
        ) from exc
    if journal["state"] != "snapshot_closed":
        raise BootstrapStateError("bootstrap snapshot resume requires snapshot_closed")
    staging_name = journal["staging_name"]
    staging = Path(staging_path).expanduser().absolute()
    if staging.name != staging_name:
        raise BootstrapStateError("closed bootstrap staging pathname drifted")
    try:
        ops.stat(journal["final_name"], dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise BootstrapStateError("cannot inspect closed bootstrap final name") from exc
    else:
        raise BootstrapStateError("closed bootstrap final name exists too early")
    staging_fd: int | None = None
    try:
        staging_fd, staging_identity = _open_private_staging_at(
            parent_fd,
            staging_name,
            expected_names=(SNAPSHOT_FILENAME,),
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        evidence = _verify_existing_closed_snapshot_at(
            parent_fd,
            staging_fd,
            staging_name,
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            _ops=ops,
        )
        evidence = _validated_closed_snapshot_evidence(
            evidence,
            expected_staging_device_inode=staging_identity[:2],
        )
        expected_tree = ExpectedPrivateDirectory(
            children={
                SNAPSHOT_FILENAME: ExpectedPrivateFile(
                    byte_size=metadata.byte_size,
                    sha256=metadata.file_sha256,
                )
            }
        )
        seal = seal_private_tree_at(
            parent_fd, staging_name, expected_tree, _ops=ops
        )
        if seal.root_identity[:2] != staging_identity[:2]:
            raise BootstrapStateError("closed bootstrap staging inode drifted")
        _closed_snapshot_identity_from_seal(seal, evidence)
        reread, _reread_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("closed bootstrap journal changed on resume")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BootstrapStateError(
                "cannot recheck closed bootstrap final name"
            ) from exc
        else:
            raise BootstrapStateError("closed bootstrap final name appeared")
        final_seal = seal_private_tree_at(
            parent_fd, staging_name, expected_tree, _ops=ops
        )
        if final_seal.root_identity[:2] != staging_identity[:2]:
            raise BootstrapStateError("closed bootstrap staging changed on resume")
        _closed_snapshot_identity_from_seal(final_seal, evidence)
        result = PreparedSnapshotClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=evidence,
        )
        staging_fd = None
        return result
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "closed bootstrap staging close failed"
                    ) from exc


def _prepare_or_resume_bootstrap_snapshot_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    _ops: _PrivateTreeOsOps | None = None,
    _preparer: Callable[..., PreparedSnapshotInProgress] | None = None,
    _closer: Callable[..., tuple[dict[str, Any], str, ClosedSnapshotEvidence]]
    | None = None,
    _resumer: Callable[..., PreparedSnapshotClosed] | None = None,
) -> PreparedSnapshotClosed:
    """Reach one verified snapshot_closed boundary under the caller's lock."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    preparer = _preparer or _prepare_bootstrap_snapshot_in_progress_locked_at
    closer = _closer or _close_bootstrap_snapshot_locked_at
    resumer = _resumer or _resume_bootstrap_snapshot_closed_locked_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    if reserved["state"] != "reserved":
        raise BootstrapStateError("bootstrap snapshot integration requires reserved payload")
    staging = Path(staging_path).expanduser().absolute()
    if (
        bootstrap_staging_name(reserved["final_name"]) != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
    ):
        raise BootstrapStateError("bootstrap snapshot integration path drifted")
    immutable = {
        key: reserved[key]
        for key in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(key) != value for key, value in immutable.items()):
            raise BootstrapStateError("bootstrap snapshot integration binding drifted")

    def validate_resumed(
        result: PreparedSnapshotClosed,
        expected_journal: dict[str, Any],
        expected_digest: str,
        prior_evidence: ClosedSnapshotEvidence | None,
    ) -> PreparedSnapshotClosed:
        if type(result) is not PreparedSnapshotClosed:
            raise BootstrapStateError("bootstrap snapshot resume result is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise BootstrapStateError("bootstrap snapshot resume descriptor is invalid")
        try:
            require_bindings(result.journal)
            if (
                result.journal != expected_journal
                or result.journal_digest != expected_digest
                or expected_digest != canonical_payload_digest(expected_journal)
            ):
                raise BootstrapStateError("bootstrap snapshot resume journal drifted")
            evidence = _validated_closed_snapshot_evidence(
                result.evidence,
                expected_staging_device_inode=result.staging_identity[:2],
            )
            metadata = SnapshotMetadata(**expected_journal["snapshot_metadata"])
            if evidence.metadata != metadata:
                raise BootstrapStateError("bootstrap snapshot resume metadata drifted")
            if prior_evidence is not None and (
                evidence.snapshot_device_inode
                != prior_evidence.snapshot_device_inode
                or evidence.staging_device_inode
                != prior_evidence.staging_device_inode
            ):
                raise BootstrapStateError("bootstrap snapshot inode changed after close")
            return result
        except (BootstrapStateError, SnapshotError, OSError):
            try:
                ops.close(result_fd)
            except OSError:
                pass
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            try:
                ops.close(result_fd)
            except OSError:
                pass
            raise BootstrapStateError(
                "bootstrap snapshot resume result is malformed"
            ) from exc

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        ops.stat(journal_name, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
    except OSError as exc:
        raise BootstrapStateError("cannot classify bootstrap snapshot journal") from exc
    else:
        current, _identity, _digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        require_bindings(current)

    if current is not None and current["state"] == "snapshot_closed":
        result = resumer(
            parent_fd,
            journal_name,
            lock_fd=lock_fd,
            lock_name=lock_name,
            staging_path=staging,
            _ops=ops,
        )
        return validate_resumed(
            result, current, canonical_payload_digest(current), None
        )
    if current is not None and current["state"] not in {
        "reserved",
        "staging_created",
        "snapshot_in_progress",
    }:
        raise BootstrapStateError("bootstrap snapshot integration state is not resumable")

    prepared = preparer(
        parent_fd,
        journal_name,
        reserved,
        staging,
        lock_fd=lock_fd,
        lock_name=lock_name,
        _ops=ops,
    )
    if type(prepared) is not PreparedSnapshotInProgress:
        raise BootstrapRecoveryRequired(
            "bootstrap snapshot preparation result requires locked recovery"
        )
    prepared_fd = prepared.staging_fd
    if type(prepared_fd) is not int:
        raise BootstrapRecoveryRequired(
            "bootstrap snapshot preparation descriptor requires locked recovery"
        )
    closer_returned = False
    try:
        if (
            prepared.journal["state"] != "snapshot_in_progress"
            or prepared.journal_digest
            != canonical_payload_digest(prepared.journal)
            or prepared.staging_identity[:2] != prepared.staging_device_inode
        ):
            raise BootstrapStateError(
                "bootstrap snapshot preparation result is invalid"
            )
        require_bindings(prepared.journal)
        close_result = closer(
            parent_fd,
            journal_name,
            lock_fd=lock_fd,
            lock_name=lock_name,
            staging_fd=prepared_fd,
            staging_path=staging,
            source_db=Path(source_db).expanduser().absolute(),
            expected_staging_device_inode=prepared.staging_device_inode,
        )
        closer_returned = True
        closed, closed_digest, evidence = close_result
        closed = _validated_bootstrap_journal_payload(closed)
        validate_bootstrap_transition(prepared.journal, closed)
        require_bindings(closed)
        if (
            closed["state"] != "snapshot_closed"
            or closed_digest != canonical_payload_digest(closed)
        ):
            raise BootstrapStateError("bootstrap snapshot close result is invalid")
        evidence = _validated_closed_snapshot_evidence(
            evidence,
            expected_staging_device_inode=prepared.staging_device_inode,
        )
        if snapshot_metadata_payload(evidence.metadata) != closed["snapshot_metadata"]:
            raise BootstrapStateError("bootstrap snapshot close metadata drifted")
    except BootstrapRecoveryRequired:
        raise
    except (BootstrapStateError, SnapshotError, OSError) as exc:
        if closer_returned:
            raise BootstrapRecoveryRequired(
                "published bootstrap snapshot integration requires locked recovery"
            ) from exc
        raise BootstrapRecoveryRequired(
            "bootstrap snapshot preparation requires locked recovery"
        ) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        phase = "published" if closer_returned else "prepared"
        raise BootstrapRecoveryRequired(
            f"{phase} bootstrap snapshot integration requires locked recovery"
        ) from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        try:
            ops.close(prepared_fd)
        except OSError as exc:
            if not active_exception:
                raise BootstrapRecoveryRequired(
                    "closed bootstrap staging close requires recovery"
                ) from exc

    try:
        result = resumer(
            parent_fd,
            journal_name,
            lock_fd=lock_fd,
            lock_name=lock_name,
            staging_path=staging,
            _ops=ops,
        )
        return validate_resumed(result, closed, closed_digest, evidence)
    except BootstrapRecoveryRequired:
        raise
    except (BootstrapStateError, SnapshotError, OSError) as exc:
        raise BootstrapRecoveryRequired(
            "closed bootstrap snapshot reopen requires locked recovery"
        ) from exc


def verify_snapshot(snapshot_path: Path, expected: SnapshotMetadata) -> None:
    snapshot = snapshot_path.expanduser().absolute()
    _require_private_destination(snapshot)
    if not snapshot.is_file() or _is_reparse_or_symlink(snapshot):
        raise SnapshotError("immutable snapshot is missing or indirected")
    _require_owner_only_mode(snapshot, 0o600)
    _reject_snapshot_sidecars(snapshot)
    file_hash, byte_size = _stream_hash_and_size(snapshot)
    if byte_size != expected.byte_size or file_hash != expected.file_sha256:
        raise SnapshotError("immutable snapshot hash or size drifted")
    conn = _open_read_only_database(snapshot)
    try:
        _quick_check(conn)
        actual = _snapshot_metadata(conn, snapshot)
    finally:
        conn.close()
    _reject_snapshot_sidecars(snapshot)
    if actual != expected:
        raise SnapshotError("immutable snapshot metadata drifted")


def _table_info(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    try:
        return {
            str(row[1]): str(row[2] or "")
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
    except sqlite3.Error as exc:
        raise SchemaPreflightError("cannot inspect required SQLite schema") from exc


def atomic_schema_preflight(conn: sqlite3.Connection) -> AtomicSchemaInfo:
    """Require the closed declared-affinity surface for atomic extraction."""

    try:
        table_types = {
            str(name): str(kind)
            for name, kind in conn.execute(
                "SELECT name, type FROM sqlite_schema "
                "WHERE name IN ('message', 'chat', 'chat_message_join', "
                "'message_attachment_join')"
            )
        }
    except sqlite3.Error as exc:
        raise SchemaPreflightError("cannot inspect required SQLite tables") from exc
    for table, required in REQUIRED_SCHEMA_AFFINITIES.items():
        if table_types.get(table) != "table":
            raise SchemaPreflightError("required SQLite table is missing or retyped")
        columns = _table_info(conn, table)
        if not columns:
            raise SchemaPreflightError("required SQLite table is missing")
        for name, expected_affinity in required.items():
            if name not in columns:
                raise SchemaPreflightError("required SQLite column is missing")
            if _sqlite_affinity(columns[name]) != expected_affinity:
                raise SchemaPreflightError("required SQLite column affinity is invalid")
    message_columns = _table_info(conn, "message")
    reply_columns = [
        name for name in REPLY_LINK_COLUMN_VARIANTS if name in message_columns
    ]
    for name in reply_columns:
        if _sqlite_affinity(message_columns[name]) != "TEXT":
            raise SchemaPreflightError("reply-link column affinity is invalid")
    return AtomicSchemaInfo(
        schema="setec-imessage-atomic-schema-info/1",
        schema_fingerprint=_schema_fingerprint(conn),
        reply_column=reply_columns[0] if reply_columns else None,
    )


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _exists(conn: sqlite3.Connection, sql: str) -> bool:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        raise SchemaPreflightError("runtime SQLite preflight query failed") from exc
    if type(row) is not tuple or len(row) != 1 or row[0] not in (0, 1):
        raise SchemaPreflightError("runtime SQLite preflight result is malformed")
    return bool(row[0])


def _runtime_preflight(conn: sqlite3.Connection) -> None:
    checks = (
        "SELECT EXISTS(SELECT 1 FROM message "
        "WHERE typeof(is_from_me) <> 'integer')",
        "SELECT EXISTS(SELECT 1 FROM chat_message_join "
        "WHERE typeof(message_id) <> 'integer' OR typeof(chat_id) <> 'integer')",
        "SELECT EXISTS(SELECT 1 FROM message_attachment_join "
        "WHERE typeof(message_id) <> 'integer' "
        "OR typeof(attachment_id) <> 'integer')",
        "SELECT EXISTS(SELECT 1 FROM chat_message_join AS j "
        "LEFT JOIN message AS m ON m.ROWID = j.message_id "
        "LEFT JOIN chat AS c ON c.ROWID = j.chat_id "
        "WHERE m.ROWID IS NULL OR c.ROWID IS NULL)",
        "SELECT EXISTS(SELECT 1 FROM message_attachment_join AS j "
        "LEFT JOIN message AS m ON m.ROWID = j.message_id "
        "WHERE m.ROWID IS NULL)",
    )
    if any(_exists(conn, sql) for sql in checks):
        raise SchemaPreflightError("runtime SQLite source contract is invalid")


def _nullable_exact(value: object, expected_type: type) -> bool:
    return value is None or type(value) is expected_type


def _normalized_optional_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _attachment_map(conn: sqlite3.Connection) -> dict[int, tuple[int, ...]]:
    try:
        rows = conn.execute(
            "SELECT message_id, attachment_id FROM message_attachment_join "
            "ORDER BY message_id, attachment_id"
        )
        result: dict[int, set[int]] = {}
        for message_id, attachment_id in rows:
            if type(message_id) is not int or type(attachment_id) is not int:
                raise SchemaPreflightError("attachment join runtime type is invalid")
            result.setdefault(message_id, set()).add(attachment_id)
        return {
            message_id: tuple(sorted(attachment_ids))
            for message_id, attachment_ids in result.items()
        }
    except sqlite3.Error as exc:
        raise SchemaPreflightError("cannot read attachment evidence") from exc


def discover_candidate_universe(
    conn: sqlite3.Connection,
    schema_info: AtomicSchemaInfo,
    *,
    apple_date_unit: str,
    timezone_name: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
    max_messages: int,
) -> AtomicCandidateUniverse:
    """Validate and return the closed outgoing and date-selected universes.

    Raw GUIDs remain in memory only. Exceptions intentionally describe only
    invariant classes and never echo source values.
    """

    if since is not None and type(since) is not _dt.date:
        raise AtomicAcquisitionError("since must be an exact local date")
    if until is not None and type(until) is not _dt.date:
        raise AtomicAcquisitionError("until must be an exact local date")
    if since is not None and until is not None and since > until:
        raise AtomicAcquisitionError("since must not be after until")
    if type(max_messages) is not int or max_messages < 1:
        raise AtomicAcquisitionError("max_messages must be a positive exact integer")
    _load_explicit_zone(timezone_name)
    fresh_schema_info = atomic_schema_preflight(conn)
    if schema_info != fresh_schema_info:
        raise SchemaPreflightError("schema binding drifted before discovery")
    schema_info = fresh_schema_info
    _runtime_preflight(conn)
    attachments = _attachment_map(conn)
    reply_sql = (
        f'm.{_quoted_identifier(schema_info.reply_column)}'
        if schema_info.reply_column
        else "NULL"
    )
    sql = f"""
        SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.date,
               m.associated_message_type, m.item_type, {reply_sql},
               j.message_id, j.chat_id, c.ROWID, c.guid,
               c.chat_identifier, c.room_name, c.style
        FROM message AS m
        LEFT JOIN chat_message_join AS j ON j.message_id = m.ROWID
        LEFT JOIN chat AS c ON c.ROWID = j.chat_id
        WHERE m.is_from_me = 1
        ORDER BY m.ROWID, j.chat_id, c.ROWID
    """
    try:
        rows = list(conn.execute(sql))
    except sqlite3.Error as exc:
        raise SchemaPreflightError("cannot scan outgoing candidate identities") from exc
    grouped: dict[int, list[tuple[object, ...]]] = {}
    for row in rows:
        if type(row) is not tuple or len(row) != 15 or type(row[0]) is not int:
            raise SchemaPreflightError("candidate identity row is malformed")
        grouped.setdefault(row[0], []).append(row)
    message_guids: set[str] = set()
    chat_metadata: dict[str, tuple[str | None, str | None, int]] = {}
    candidates: list[AtomicCandidate] = []
    held: list[AtomicHeldSourceRow] = []
    ambiguous_local_dates: list[_dt.date] = []
    for rowid, joined_rows in grouped.items():
        first = joined_rows[0]
        (
            _, message_guid, text, attributed_body, raw_date,
            associated_type, item_type, reply_link,
            *_rest,
        ) = first
        message_guid = validate_stable_guid(message_guid, identity="message")
        if message_guid in message_guids:
            raise StableGuidError("duplicate stable message GUID")
        message_guids.add(message_guid)
        if (
            not _nullable_exact(text, str)
            or not _nullable_exact(attributed_body, bytes)
            or type(raw_date) is not int
            or not _nullable_exact(associated_type, int)
            or not _nullable_exact(item_type, int)
            or not _nullable_exact(reply_link, str)
        ):
            raise SchemaPreflightError("candidate message runtime type is invalid")
        unix_nanoseconds = apple_date_to_unix_ns(raw_date, apple_date_unit)
        local_date = unix_ns_to_local_date(unix_nanoseconds, timezone_name)
        identities: dict[str, tuple[str | None, str | None, int]] = {}
        for joined in joined_rows:
            (
                _, joined_message_guid, joined_text, joined_attributed,
                joined_date, joined_associated, joined_item, joined_reply,
                join_message_id, join_chat_id, chat_rowid, chat_guid,
                chat_identifier, room_name, style,
            ) = joined
            if (
                joined_message_guid != message_guid
                or joined_text != text
                or joined_attributed != attributed_body
                or joined_date != raw_date
                or joined_associated != associated_type
                or joined_item != item_type
                or joined_reply != reply_link
            ):
                raise SchemaPreflightError("candidate chat identity runtime type is invalid")
            join_projection = (
                join_message_id, join_chat_id, chat_rowid, chat_guid,
                chat_identifier, room_name, style,
            )
            if all(value is None for value in join_projection):
                if len(joined_rows) != 1:
                    raise SchemaPreflightError(
                        "candidate missing-chat join projection is malformed"
                    )
                continue
            if any(value is None for value in (join_message_id, join_chat_id, chat_rowid, chat_guid, style)):
                raise SchemaPreflightError("candidate chat join is partial or orphaned")
            if (
                type(join_message_id) is not int
                or type(join_chat_id) is not int
                or type(chat_rowid) is not int
                or type(chat_identifier) not in (str, type(None))
                or type(room_name) not in (str, type(None))
                or type(style) is not int
            ):
                raise SchemaPreflightError("candidate chat identity runtime type is invalid")
            stable_chat_guid = validate_stable_guid(chat_guid, identity="chat")
            metadata = (
                _normalized_optional_text(chat_identifier),
                _normalized_optional_text(room_name),
                style,
            )
            previous = identities.setdefault(stable_chat_guid, metadata)
            if previous != metadata:
                raise StableGuidError("contradictory stable chat identity metadata")
            global_previous = chat_metadata.setdefault(stable_chat_guid, metadata)
            if global_previous != metadata:
                raise StableGuidError("contradictory stable chat identity metadata")
        if not identities:
            held.append(
                AtomicHeldSourceRow(
                    snapshot_rowid=rowid,
                    message_guid=message_guid,
                    unix_nanoseconds=unix_nanoseconds,
                    local_date=local_date,
                    reason="missing_chat_join",
                )
            )
            continue
        if len(identities) != 1:
            ambiguous_local_dates.append(local_date)
            continue
        chat_guid, metadata = next(iter(identities.items()))
        chat_identifier, room_name, style = metadata
        candidates.append(
            AtomicCandidate(
                snapshot_rowid=rowid,
                message_guid=message_guid,
                chat_guid=chat_guid,
                chat_identifier=chat_identifier,
                room_name=room_name,
                style=style,
                group_status=classify_group_status(room_name, style),
                unix_nanoseconds=unix_nanoseconds,
                local_date=local_date,
                text=text,
                attributed_body=attributed_body,
                associated_message_type=associated_type,
                item_type=item_type,
                reply_link=reply_link,
                attachment_ids=attachments.get(rowid, ()),
            )
        )
    candidates.sort(
        key=lambda candidate: (
            candidate.unix_nanoseconds,
            candidate.message_guid.encode("utf-8"),
        )
    )
    held.sort(
        key=lambda candidate: (
            candidate.unix_nanoseconds,
            candidate.message_guid.encode("utf-8"),
        )
    )
    selected = tuple(
        candidate
        for candidate in candidates
        if (since is None or candidate.local_date >= since)
        and (until is None or candidate.local_date <= until)
    )
    selected_held = tuple(
        candidate
        for candidate in held
        if (since is None or candidate.local_date >= since)
        and (until is None or candidate.local_date <= until)
    )
    ambiguous_multi_chat_rows = len(ambiguous_local_dates)
    selected_ambiguous_multi_chat_rows = sum(
        1
        for local_date in ambiguous_local_dates
        if (since is None or local_date >= since)
        and (until is None or local_date <= until)
    )
    selected_outgoing_rows = (
        len(selected) + len(selected_held) + selected_ambiguous_multi_chat_rows
    )
    candidate_outgoing_rows = len(candidates) + len(held) + ambiguous_multi_chat_rows
    if selected_outgoing_rows > max_messages:
        raise AtomicAcquisitionError("selected outgoing rows exceed max_messages ceiling")
    if ambiguous_multi_chat_rows:
        raise StableGuidError("outgoing candidate universe contains ambiguous multi-chat rows")
    return AtomicCandidateUniverse(
        schema="setec-imessage-atomic-candidate-universe/2",
        candidate_outgoing_rows=candidate_outgoing_rows,
        candidate_eligible_rows=len(candidates),
        held_missing_chat_join_rows=len(held),
        ambiguous_multi_chat_rows=0,
        selected_outgoing_rows=selected_outgoing_rows,
        selected_eligible_rows=len(selected),
        selected_held_missing_chat_join_rows=len(selected_held),
        selected_ambiguous_multi_chat_rows=0,
        candidates=tuple(candidates),
        selected=selected,
        held=tuple(held),
        selected_held=selected_held,
    )


def discover_snapshot_candidate_universe(
    snapshot_path: Path,
    snapshot_metadata: SnapshotMetadata,
    *,
    apple_date_unit: str,
    timezone_name: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
    max_messages: int,
) -> tuple[AtomicSchemaInfo, AtomicCandidateUniverse]:
    """Scan only the immutable snapshot and rehash it after discovery."""

    verify_snapshot(snapshot_path, snapshot_metadata)
    conn = _open_read_only_database(snapshot_path)
    try:
        schema_info = atomic_schema_preflight(conn)
        universe = discover_candidate_universe(
            conn,
            schema_info,
            apple_date_unit=apple_date_unit,
            timezone_name=timezone_name,
            since=since,
            until=until,
            max_messages=max_messages,
        )
    finally:
        conn.close()
    verify_snapshot(snapshot_path, snapshot_metadata)
    return schema_info, universe


def _discover_closed_snapshot_universe_at(
    parent_fd: int,
    staging_fd: int,
    staging_name: str,
    staging_path: Path,
    snapshot_evidence: ClosedSnapshotEvidence,
    *,
    expected_staging_device_inode: tuple[int, int],
    expected_staging_names: Sequence[str] = (SNAPSHOT_FILENAME,),
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_opener: Callable[[Path], Any] | None = None,
    _connection_binder: Callable[
        [Callable[[Path], Any], Path, tuple[int, int], str], Any
    ]
    | None = None,
) -> tuple[
    ClosedSnapshotEvidence,
    AtomicSchemaInfo,
    AtomicCandidateUniverse,
]:
    """Discover one universe through the held closed-snapshot inode."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    staging_name = _bootstrap_basename(staging_name, "staging name")
    staging = Path(staging_path).expanduser().absolute()
    if staging.name != staging_name:
        raise BootstrapStateError("universe scan staging path is invalid")
    if _ops is None:
        _require_private_destination(staging)
    expected_inventory = _closed_staging_inventory_names(expected_staging_names)
    if SNAPSHOT_FILENAME not in expected_inventory:
        raise BootstrapStateError("universe inventory omits snapshot")
    snapshot_evidence = _validated_closed_snapshot_evidence(
        snapshot_evidence,
        expected_staging_device_inode=expected_staging_device_inode,
        expected_inventory=expected_inventory,
    )
    snapshot_metadata = snapshot_evidence.metadata
    validated_metadata = _validated_bootstrap_snapshot(
        snapshot_metadata_payload(snapshot_metadata)
    )
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    window = semantic["local_date_window"]

    def parse_date(value: str | None) -> _dt.date | None:
        return _dt.date.fromisoformat(value) if value is not None else None

    since = parse_date(window["since"])
    until = parse_date(window["until"])
    snapshot_path = staging / SNAPSHOT_FILENAME
    snapshot_fd: int | None = None
    snapshot_conn: Any = None
    try:
        staging_identity = _verify_pinned_staging_binding_at(
            parent_fd,
            staging_fd,
            staging_name,
            staging,
            expected_device_inode=expected_staging_device_inode,
            expected_names=expected_inventory,
            ops=ops,
        )
        if staging_identity != snapshot_evidence.staging_identity:
            raise BootstrapStateError("universe staging identity drifted")
        snapshot_fd, opened_identity = _open_private_tree_node_at(
            staging_fd,
            SNAPSHOT_FILENAME,
            kind="file",
            owner_uid=ops.getuid(),
            ops=ops,
            label="universe snapshot file",
        )
        snapshot_device_inode = opened_identity[:2]
        if opened_identity != snapshot_evidence.snapshot_identity:
            raise BootstrapStateError("universe snapshot identity drifted")

        def guard() -> tuple[
            tuple[int, int, int, int, int, int, int, int],
            tuple[int, int, int, int, int, int, int, int],
        ]:
            current_staging = _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                staging_name,
                staging,
                expected_device_inode=expected_staging_device_inode,
                expected_names=expected_inventory,
                ops=ops,
            )
            current_snapshot = _verify_pinned_snapshot_binding_at(
                staging_fd,
                snapshot_fd,
                snapshot_path,
                expected_device_inode=snapshot_device_inode,
                ops=ops,
            )
            if (
                current_staging != snapshot_evidence.staging_identity
                or current_snapshot != snapshot_evidence.snapshot_identity
            ):
                raise BootstrapStateError(
                    "universe snapshot full identity drifted"
                )
            return current_staging, current_snapshot

        staging_identity, snapshot_identity = guard()
        first_hash, first_size, first_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        if (
            first_hash != validated_metadata["file_sha256"]
            or first_size != validated_metadata["byte_size"]
            or first_identity != snapshot_identity
        ):
            raise BootstrapStateError("universe snapshot bytes drifted")
        guard()
        opener = _snapshot_opener or _open_read_only_database
        binder = _connection_binder or _open_inode_bound_sqlite_connection
        snapshot_conn = binder(
            opener,
            snapshot_path,
            snapshot_device_inode,
            "universe SQLite reader",
        )
        guard()
        query_only = snapshot_conn.execute("PRAGMA query_only").fetchone()
        if query_only != (1,):
            raise BootstrapStateError("universe SQLite reader is not query-only")
        _quick_check(snapshot_conn)
        recomputed = _snapshot_metadata_from_hash(
            snapshot_conn,
            file_hash=first_hash,
            byte_size=first_size,
        )
        if snapshot_metadata_payload(recomputed) != validated_metadata:
            raise BootstrapStateError("universe snapshot metadata drifted")
        schema_info = atomic_schema_preflight(snapshot_conn)
        if schema_info.schema_fingerprint != snapshot_metadata.schema_fingerprint:
            raise SchemaPreflightError("universe schema fingerprint drifted")
        universe = discover_candidate_universe(
            snapshot_conn,
            schema_info,
            apple_date_unit=semantic["apple_date_unit"],
            timezone_name=semantic["timezone"],
            since=since,
            until=until,
            max_messages=controls["max_messages"],
        )
        guard()
        closing_connection = snapshot_conn
        snapshot_conn = None
        closing_connection.close()
        staging_identity, snapshot_identity = guard()
        second_hash, second_size, second_identity = _stream_hash_private_fd(
            snapshot_fd, ops=ops
        )
        staging_identity, snapshot_identity = guard()
        if (
            second_hash != first_hash
            or second_size != first_size
            or second_identity != first_identity
            or snapshot_identity != second_identity
        ):
            raise BootstrapStateError("universe snapshot scan drifted")
        return (
            ClosedSnapshotEvidence(
                metadata=snapshot_metadata,
                snapshot_identity=snapshot_identity,
                staging_identity=staging_identity,
                snapshot_device_inode=snapshot_device_inode,
                staging_device_inode=expected_staging_device_inode,
                inventory=expected_inventory,
            ),
            schema_info,
            universe,
        )
    except (
        AtomicAcquisitionError,
        OSError,
        sqlite3.Error,
    ) as exc:
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot scan closed snapshot universe") from exc
    finally:
        active_exception = sys.exc_info()[0] is not None
        close_failure: BaseException | None = None
        if snapshot_conn is not None:
            try:
                snapshot_conn.close()
            except (OSError, sqlite3.Error) as exc:
                close_failure = exc
        if snapshot_fd is not None:
            try:
                ops.close(snapshot_fd)
            except OSError as exc:
                if close_failure is None:
                    close_failure = exc
        if close_failure is not None and not active_exception:
            raise BootstrapStateError(
                "universe snapshot reader close failed"
            ) from close_failure


def _validated_reconstructed_initialization_closure(
    closure: InitializationClosure,
    *,
    snapshot_metadata: SnapshotMetadata,
    schema_info: AtomicSchemaInfo,
    universe: AtomicCandidateUniverse,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
) -> InitializationClosure:
    """Bind a reconstructed closure to fresh inputs, never to itself."""

    if type(closure) is not InitializationClosure:
        raise BootstrapStateError("universe initialization closure is invalid")
    metadata = SnapshotMetadata(
        **_validated_bootstrap_snapshot(snapshot_metadata_payload(snapshot_metadata))
    )
    if metadata != snapshot_metadata or type(schema_info) is not AtomicSchemaInfo:
        raise BootstrapStateError("universe initialization source binding is invalid")
    _validated_candidate_membership(universe)
    key = _validate_hmac_key(key_bytes)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key_id = hmac_key_id(key)
    smoke = smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=metadata,
        schema_info=schema_info,
        hmac_key_id_value=key_id,
    )
    contact = private_contact_map_payload(universe, key)
    source = private_source_identity_map_payload(universe, key, contact)
    hold = private_source_hold_ledger_payload(
        universe, key, source, snapshot_file_sha256=metadata.file_sha256
    )
    contact_digest = _sha256_tag(_canonical_json_bytes(contact))
    source_digest = _sha256_tag(_canonical_json_bytes(source))
    hold_digest = _sha256_tag(_canonical_json_bytes(hold))
    owner = run_owner_payload(
        snapshot_metadata=metadata,
        semantic_options=semantic,
        run_controls=controls,
        smoke_policy=smoke,
        hmac_key_id_value=key_id,
        contact_map_hash=contact_digest,
        source_identity_map_hash=source_digest,
        source_hold_ledger_hash=hold_digest,
    )
    specifications = (
        (
            SEMANTIC_OPTIONS_FILENAME,
            "semantic options",
            MAX_SEMANTIC_OPTIONS_BYTES,
            semantic,
        ),
        (
            RUN_CONTROLS_FILENAME,
            "run controls",
            MAX_RUN_CONTROLS_BYTES,
            controls,
        ),
        (
            SMOKE_POLICY_FILENAME,
            "smoke policy",
            MAX_SMOKE_POLICY_BYTES,
            smoke,
        ),
        (
            PRIVATE_CONTACT_MAP_FILENAME,
            "private contact map",
            MAX_PRIVATE_CONTACT_MAP_BYTES,
            contact,
        ),
        (
            PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
            "private source identity map",
            MAX_PRIVATE_SOURCE_IDENTITY_MAP_BYTES,
            source,
        ),
        (
            PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
            "private source hold ledger",
            MAX_PRIVATE_SOURCE_HOLD_LEDGER_BYTES,
            hold,
        ),
        (RUN_OWNER_FILENAME, "run owner", MAX_RUN_OWNER_BYTES, owner),
    )
    if (
        type(closure.artifacts) is not tuple
        or len(closure.artifacts) != len(specifications)
    ):
        raise BootstrapStateError("universe initialization artifacts drifted")
    expected_children: dict[
        str, ExpectedPrivateFile | ExpectedPrivateDirectory
    ] = {
        SNAPSHOT_FILENAME: ExpectedPrivateFile(
            byte_size=metadata.byte_size,
            sha256=metadata.file_sha256,
        )
    }
    for artifact, (filename, label, max_bytes, payload) in zip(
        closure.artifacts, specifications, strict=True
    ):
        raw = _canonical_json_bytes(payload)
        if (
            type(artifact) is not ClosedPrivateJson
            or artifact.filename != filename
            or artifact.label != label
            or artifact.max_bytes != max_bytes
            or artifact.payload != json.loads(raw)
            or artifact.raw != raw
            or artifact.digest != _sha256_tag(raw)
            or len(raw) > max_bytes
        ):
            raise BootstrapStateError("universe initialization artifact drifted")
        validator = _closed_initialization_artifact_validator(artifact)
        validator(artifact.payload)
        expected_children[filename] = ExpectedPrivateFile(
            byte_size=len(raw), sha256=artifact.digest
        )
    expected_tree = _validated_expected_private_tree(
        ExpectedPrivateDirectory(children=expected_children)
    )
    if _validated_expected_private_tree(closure.expected_tree) != expected_tree:
        raise BootstrapStateError("universe initialization tree drifted")
    expected_universe = _validated_universe_binding(
        {
            "candidate_outgoing_rows": source["candidate_outgoing_rows"],
            "candidate_eligible_rows": source["candidate_eligible_rows"],
            "held_missing_chat_join_rows": source["held_missing_chat_join_rows"],
            "ambiguous_multi_chat_rows": source["ambiguous_multi_chat_rows"],
            "selected_outgoing_rows": source["selected_outgoing_rows"],
            "selected_eligible_rows": source["selected_eligible_rows"],
            "selected_held_missing_chat_join_rows": source[
                "selected_held_missing_chat_join_rows"
            ],
            "selected_ambiguous_multi_chat_rows": source[
                "selected_ambiguous_multi_chat_rows"
            ],
            "candidate_locator_universe_hash": source[
                "candidate_locator_universe_hash"
            ],
            "selected_locator_universe_hash": source[
                "selected_locator_universe_hash"
            ],
        }
    )
    if _validated_universe_binding(closure.universe_binding) != expected_universe:
        raise BootstrapStateError("universe initialization universe drifted")
    return closure


def _close_bootstrap_universe_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    prepared_snapshot: PreparedSnapshotClosed,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _closure_builder: Callable[..., InitializationClosure] | None = None,
) -> PreparedUniverseClosed:
    """Consume a verified snapshot and publish the sole universe_closed CAS."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    scanner = _scanner or _discover_closed_snapshot_universe_at
    closure_builder = _closure_builder or build_initialization_closure
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(prepared_snapshot) is not PreparedSnapshotClosed:
        raise BootstrapStateError("universe close prepared snapshot is invalid")
    staging_fd = prepared_snapshot.staging_fd
    if type(staging_fd) is not int:
        raise BootstrapStateError("universe close staging descriptor is invalid")
    staging = Path(staging_path).expanduser().absolute()
    journal_advanced = False
    transferred = False
    try:
        journal = _validated_bootstrap_journal_payload(prepared_snapshot.journal)
        if (
            journal["state"] != "snapshot_closed"
            or prepared_snapshot.journal_digest
            != canonical_payload_digest(journal)
            or staging.name != journal["staging_name"]
            or bootstrap_staging_name(journal["final_name"])
            != journal["staging_name"]
            or bootstrap_journal_name(journal["final_name"]) != journal_name
        ):
            raise BootstrapStateError("universe close snapshot authority drifted")
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            canonical_payload_digest(semantic)
            != journal["semantic_options_digest"]
            or canonical_payload_digest(controls) != journal["run_controls_digest"]
            or hmac_key_id(key) != journal["hmac_key_id"]
        ):
            raise BootstrapStateError("universe close input binding drifted")
        prior_evidence = _validated_closed_snapshot_evidence(
            prepared_snapshot.evidence,
            expected_staging_device_inode=prepared_snapshot.staging_identity[:2],
        )
        if prepared_snapshot.staging_identity != prior_evidence.staging_identity:
            raise BootstrapStateError("universe close staging identity drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != prepared_snapshot.journal_digest:
            raise BootstrapStateError("universe close journal changed before scan")

        def require_final_absent() -> None:
            try:
                ops.stat(journal["final_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise BootstrapStateError(
                    "cannot inspect universe close final name"
                ) from exc
            raise BootstrapStateError("universe close final name exists too early")

        require_final_absent()
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=prior_evidence.staging_device_inode,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=prior_evidence.staging_device_inode,
        )
        if (
            scanned_evidence.snapshot_identity != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity != prior_evidence.staging_identity
        ):
            raise BootstrapStateError("universe scan evidence identity drifted")
        if (
            type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("universe scan schema binding drifted")
        _validated_candidate_membership(universe)
        closure = closure_builder(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        universe_binding = _validated_universe_binding(closure.universe_binding)
        smoke_digest = closure.artifact(SMOKE_POLICY_FILENAME).digest
        next_journal = bootstrap_journal_payload(
            state="universe_closed",
            previous_journal_digest=prepared_snapshot.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=smoke_digest,
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=universe_binding,
            completed_artifacts=journal["completed_artifacts"],
        )
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != prepared_snapshot.journal_digest:
            raise BootstrapStateError("universe close journal changed before publish")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        snapshot_tree = ExpectedPrivateDirectory(
            children={
                SNAPSHOT_FILENAME: ExpectedPrivateFile(
                    byte_size=scanned_evidence.metadata.byte_size,
                    sha256=scanned_evidence.metadata.file_sha256,
                )
            }
        )
        before_seal = seal_private_tree_at(
            parent_fd, journal["staging_name"], snapshot_tree, _ops=ops
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("universe close prepublish seal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("published universe journal drifted")
        final_seal = seal_private_tree_at(
            parent_fd, journal["staging_name"], snapshot_tree, _ops=ops
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("universe close final seal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedUniverseClosed(
            journal=next_journal,
            journal_digest=published_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        if journal_advanced:
            raise BootstrapRecoveryRequired(
                "published bootstrap universe requires locked recovery"
            ) from exc
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot close bootstrap universe") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if journal_advanced:
            raise BootstrapRecoveryRequired(
                "published bootstrap universe requires locked recovery"
            ) from exc
        raise BootstrapStateError("bootstrap universe result is malformed") from exc
    finally:
        if not transferred:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    if journal_advanced:
                        raise BootstrapRecoveryRequired(
                            "published universe staging close requires recovery"
                        ) from exc
                    raise BootstrapStateError(
                        "universe staging close failed"
                    ) from exc


def _resume_bootstrap_universe_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
) -> PreparedUniverseClosed:
    """Reconstruct and verify an authoritative universe_closed journal."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "universe_closed"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("universe resume authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect universe resume final name"
            ) from exc
        raise BootstrapStateError("universe resume final name exists too early")

    require_final_absent()
    staging_fd: int | None = None
    try:
        staging_fd, staging_identity = _open_private_staging_at(
            parent_fd,
            journal["staging_name"],
            expected_names=(SNAPSHOT_FILENAME,),
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError("universe resume staging identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
        )
        if (
            scanned_evidence.snapshot_identity != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("universe resume scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        smoke_digest = closure.artifact(SMOKE_POLICY_FILENAME).digest
        universe_binding = _validated_universe_binding(closure.universe_binding)
        if (
            smoke_digest != journal["smoke_policy_digest"]
            or universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("universe resume reconstruction drifted")
        snapshot_tree = ExpectedPrivateDirectory(
            children={
                SNAPSHOT_FILENAME: ExpectedPrivateFile(
                    byte_size=scanned_evidence.metadata.byte_size,
                    sha256=scanned_evidence.metadata.file_sha256,
                )
            }
        )
        before_seal = seal_private_tree_at(
            parent_fd, journal["staging_name"], snapshot_tree, _ops=ops
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("universe resume first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("universe resume journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_seal = seal_private_tree_at(
            parent_fd, journal["staging_name"], snapshot_tree, _ops=ops
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("universe resume final seal drifted")
        final_journal, _journal_identity, final_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if final_journal != journal or final_digest != journal_digest:
            raise BootstrapStateError("universe resume terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedUniverseClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError(
            "universe resume verification failed"
        ) from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError("universe resume result is malformed") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "universe resume staging close failed"
                    ) from exc


def _prepare_or_resume_bootstrap_universe_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_integrator: Callable[..., PreparedSnapshotClosed] | None = None,
    _universe_closer: Callable[..., PreparedUniverseClosed] | None = None,
    _universe_resumer: Callable[..., PreparedUniverseClosed] | None = None,
) -> PreparedUniverseClosed:
    """Reach one verified universe_closed boundary under the caller's lock."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_integrator = (
        _snapshot_integrator
        or _prepare_or_resume_bootstrap_snapshot_closed_locked_at
    )
    universe_closer = _universe_closer or _close_bootstrap_universe_locked_at
    universe_resumer = (
        _universe_resumer or _resume_bootstrap_universe_closed_locked_at
    )
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    if (
        reserved["state"] != "reserved"
        or bootstrap_staging_name(reserved["final_name"])
        != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
        or canonical_payload_digest(semantic)
        != reserved["semantic_options_digest"]
        or canonical_payload_digest(controls) != reserved["run_controls_digest"]
        or hmac_key_id(key) != reserved["hmac_key_id"]
    ):
        raise BootstrapStateError("universe integration authority drifted")
    immutable = {
        name: reserved[name]
        for name in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(name) != value for name, value in immutable.items()):
            raise BootstrapStateError("universe integration binding drifted")

    def validate_result(
        result: PreparedUniverseClosed,
        *,
        published_here: bool,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        predecessor_journal: dict[str, Any] | None,
        expected_result_fd: int | None,
    ) -> PreparedUniverseClosed:
        error_type = BootstrapRecoveryRequired if published_here else BootstrapStateError
        if type(result) is not PreparedUniverseClosed:
            raise error_type("universe integration result is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise error_type("universe integration descriptor is invalid")

        def require_final_absent(journal: dict[str, Any]) -> None:
            try:
                ops.stat(journal["final_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise BootstrapStateError(
                    "cannot inspect universe integration final name"
                ) from exc
            raise BootstrapStateError(
                "universe integration final name exists too early"
            )

        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "universe_closed"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError("universe integration result drifted")
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError(
                        "universe integration authority changed"
                    )
            elif authoritative_digest is not None:
                raise BootstrapStateError(
                    "universe integration authority is malformed"
                )
            if predecessor_journal is not None:
                validate_bootstrap_transition(predecessor_journal, journal)
                if (
                    journal["previous_journal_digest"]
                    != canonical_payload_digest(predecessor_journal)
                ):
                    raise BootstrapStateError(
                        "universe integration predecessor drifted"
                    )
            if expected_result_fd is not None and result_fd != expected_result_fd:
                raise BootstrapStateError(
                    "universe integration descriptor transfer drifted"
                )
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="universe integration staging descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(
                    "universe integration descriptor identity drifted"
                )
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            if (
                result.staging_identity != snapshot_evidence.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(result.evidence.schema_info) is not AtomicSchemaInfo
                or result.evidence.schema_info.schema_fingerprint
                != metadata.schema_fingerprint
            ):
                raise BootstrapStateError("universe integration staging drifted")
            _validated_candidate_membership(result.evidence.universe)
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=result.evidence.schema_info,
                universe=result.evidence.universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            if (
                journal["smoke_policy_digest"]
                != closure.artifact(SMOKE_POLICY_FILENAME).digest
                or journal["universe_binding"] != closure.universe_binding
                or journal["completed_artifacts"]
                != {SNAPSHOT_FILENAME: metadata.file_sha256}
            ):
                raise BootstrapStateError("universe integration closure drifted")
            reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if reread != journal or reread_digest != result.journal_digest:
                raise BootstrapStateError("universe integration journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            snapshot_tree = ExpectedPrivateDirectory(
                children={
                    SNAPSHOT_FILENAME: ExpectedPrivateFile(
                        byte_size=metadata.byte_size,
                        sha256=metadata.file_sha256,
                    )
                }
            )
            final_seal = seal_private_tree_at(
                parent_fd,
                journal["staging_name"],
                snapshot_tree,
                _ops=ops,
            )
            if (
                final_seal.root_identity != result.staging_identity
                or _closed_snapshot_identity_from_seal(
                    final_seal, snapshot_evidence
                )
                != snapshot_evidence.snapshot_identity
            ):
                raise BootstrapStateError("universe integration final seal drifted")
            terminal, _journal_identity, terminal_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if terminal != journal or terminal_digest != result.journal_digest:
                raise BootstrapStateError(
                    "universe integration terminal journal changed"
                )
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            try:
                ops.close(result_fd)
            except (KeyError, OSError) as close_exc:
                raise error_type(
                    "universe integration result close failed"
                ) from close_exc
            if published_here:
                raise BootstrapRecoveryRequired(
                    "published universe integration requires locked recovery"
                ) from exc
            if isinstance(exc, BootstrapStateError):
                raise
            raise BootstrapStateError("universe integration result is malformed") from exc

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        ops.stat(journal_name, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
    except OSError as exc:
        raise BootstrapStateError("cannot classify universe journal") from exc
    else:
        current, _journal_identity, current_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        require_bindings(current)
    if current is not None and current["state"] == "universe_closed":
        return validate_result(
            universe_resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            published_here=False,
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            expected_result_fd=None,
        )
    if current is not None and current["state"] not in {
        "reserved",
        "staging_created",
        "snapshot_in_progress",
        "snapshot_closed",
    }:
        raise BootstrapStateError("universe integration state is not resumable")
    snapshot_published_here = (
        current is None or current["state"] != "snapshot_closed"
    )
    prepared_snapshot = snapshot_integrator(
        parent_fd,
        journal_name,
        reserved,
        staging,
        Path(source_db).expanduser().absolute(),
        lock_fd=lock_fd,
        lock_name=lock_name,
        _ops=ops,
    )
    if type(prepared_snapshot) is not PreparedSnapshotClosed:
        error_type = (
            BootstrapRecoveryRequired
            if snapshot_published_here
            else BootstrapStateError
        )
        raise error_type("snapshot integration result is invalid")
    snapshot_fd = prepared_snapshot.staging_fd
    if type(snapshot_fd) is not int:
        error_type = (
            BootstrapRecoveryRequired
            if snapshot_published_here
            else BootstrapStateError
        )
        raise error_type("snapshot integration descriptor is invalid")
    try:
        snapshot_journal = _validated_bootstrap_journal_payload(
            prepared_snapshot.journal
        )
        require_bindings(snapshot_journal)
        if (
            type(prepared_snapshot.staging_identity) is not tuple
            or len(prepared_snapshot.staging_identity) != 8
            or any(
                type(value) is not int
                for value in prepared_snapshot.staging_identity
            )
        ):
            raise BootstrapStateError("snapshot integration identity drifted")
        snapshot_evidence = _validated_closed_snapshot_evidence(
            prepared_snapshot.evidence,
            expected_staging_device_inode=prepared_snapshot.staging_identity[:2],
        )
        if (
            snapshot_journal["state"] != "snapshot_closed"
            or prepared_snapshot.journal_digest
            != canonical_payload_digest(snapshot_journal)
            or prepared_snapshot.staging_identity
            != snapshot_evidence.staging_identity
            or snapshot_evidence.metadata
            != SnapshotMetadata(**snapshot_journal["snapshot_metadata"])
        ):
            raise BootstrapStateError("snapshot integration result drifted")
    except (
        AtomicAcquisitionError,
        AttributeError,
        KeyError,
        OSError,
        TypeError,
    ) as exc:
        try:
            ops.close(snapshot_fd)
        except (KeyError, OSError) as close_exc:
            exc = close_exc
        error_type = (
            BootstrapRecoveryRequired
            if snapshot_published_here
            else BootstrapStateError
        )
        raise error_type("snapshot integration result drifted") from exc
    result = universe_closer(
        parent_fd,
        journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
        prepared_snapshot=prepared_snapshot,
        staging_path=staging,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    return validate_result(
        result,
        published_here=True,
        authoritative_journal=None,
        authoritative_digest=None,
        predecessor_journal=snapshot_journal,
        expected_result_fd=snapshot_fd,
    )


def _decode_attributed_body(blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        from acquire_imessage_sent import decode_attributed_body

        return decode_attributed_body(blob)
    except Exception as exc:
        raise AtomicAcquisitionError("attributed-body decoding failed") from exc


def _default_preprocessor(text: str) -> tuple[str, dict[str, Any]]:
    try:
        import acquisition_core

        return acquisition_core.preprocess_text(text)
    except Exception as exc:
        raise AtomicAcquisitionError("independent message preprocessing failed") from exc


def process_candidate(
    candidate: AtomicCandidate,
    *,
    include_group_chats: bool,
    reply_detection_available: bool,
    preprocessor: Callable[[str], tuple[str, dict[str, Any]]] = _default_preprocessor,
) -> AtomicProcessedRow:
    """Apply the closed exclusion precedence to one atomic message."""

    if type(include_group_chats) is not bool or type(reply_detection_available) is not bool:
        raise AtomicAcquisitionError("message processing options are invalid")

    def excluded(reason: str) -> AtomicProcessedRow:
        return AtomicProcessedRow(
            candidate=candidate,
            disposition=reason,
            cleaned_text=None,
            preprocessing_metadata=None,
        )

    if candidate.group_status == GROUP_STATUS_UNKNOWN:
        return excluded("unknown_group_status")
    if candidate.group_status == GROUP_STATUS_GROUP and not include_group_chats:
        return excluded("group_chat_excluded")
    if candidate.associated_message_type not in (0, None):
        return excluded("reaction")
    if candidate.item_type not in (0, None):
        return excluded("group_action")

    raw_plain = candidate.text or ""
    had_object_replacement = OBJECT_REPLACEMENT in raw_plain
    body = raw_plain.replace(OBJECT_REPLACEMENT, "").strip()
    if not body:
        decoded = _decode_attributed_body(candidate.attributed_body)
        attributed_unresolved = False
        if decoded:
            decoded_plain = decoded.strip()
            if decoded_plain and set(decoded_plain) == {OBJECT_REPLACEMENT}:
                had_object_replacement = True
            elif candidate.reply_link not in (None, "") or not reply_detection_available:
                attributed_unresolved = True
            else:
                body = decoded_plain
        elif candidate.attributed_body is not None:
            attributed_unresolved = True
        if not body and (candidate.attachment_ids or had_object_replacement):
            return excluded("attachment_only")
        if not body and attributed_unresolved:
            return excluded("unresolved_attributed_body")
        if not body:
            return excluded("missing_text")

    normalized_body = " ".join(body.split()).casefold()
    if normalized_body in AUTOMATED_SYSTEM_TEMPLATES:
        return excluded("automated_system")
    try:
        cleaned, metadata = preprocessor(body)
    except AtomicAcquisitionError:
        raise
    except Exception as exc:
        raise AtomicAcquisitionError("independent message preprocessing failed") from exc
    if type(cleaned) is not str or type(metadata) is not dict:
        raise AtomicAcquisitionError("message preprocessor result is invalid")
    if not cleaned.strip():
        return excluded("empty_after_preprocess")
    return AtomicProcessedRow(
        candidate=candidate,
        disposition="retained",
        cleaned_text=cleaned,
        preprocessing_metadata=metadata,
    )


def process_selected_candidates(
    universe: AtomicCandidateUniverse,
    schema_info: AtomicSchemaInfo,
    *,
    include_group_chats: bool,
    max_retained: int | None = None,
    preprocessor: Callable[[str], tuple[str, dict[str, Any]]] = _default_preprocessor,
    progress: Callable[[dict[str, int]], None] | None = None,
    progress_interval: int = 100,
) -> AtomicProcessingResult:
    """Process a full universe or the canonical prefix emitting N rows."""

    if universe.selected_eligible_rows != len(universe.selected):
        raise AtomicAcquisitionError("selected candidate universe count drifted")
    if max_retained is not None and (
        type(max_retained) is not int or max_retained < 1
    ):
        raise AtomicAcquisitionError("max_retained must be a positive exact integer")
    if type(progress_interval) is not int or progress_interval < 1:
        raise AtomicAcquisitionError("progress interval must be a positive exact integer")
    rows: list[AtomicProcessedRow] = []
    counts: Counter[str] = Counter()
    retained = 0
    for candidate in universe.selected:
        bound_reached = False
        processed = process_candidate(
            candidate,
            include_group_chats=include_group_chats,
            reply_detection_available=schema_info.reply_column is not None,
            preprocessor=preprocessor,
        )
        rows.append(processed)
        if processed.disposition == "retained":
            retained += 1
            if max_retained is not None and retained == max_retained:
                bound_reached = True
        else:
            if processed.disposition not in EXCLUSION_REASONS:
                raise AtomicAcquisitionError("unknown final exclusion reason")
            counts[processed.disposition] += 1
        considered = len(rows)
        if progress is not None and considered % progress_interval == 0:
            progress({
                "considered": considered,
                "retained": retained,
                "excluded": considered - retained,
            })
        if bound_reached:
            break
    considered = len(rows)
    not_considered = universe.selected_eligible_rows - considered
    excluded_total = sum(counts.values())
    if considered != retained + excluded_total or not_considered < 0:
        raise AtomicAcquisitionError("candidate processing accounting is invalid")
    if progress is not None and considered % progress_interval != 0:
        progress({
            "considered": considered,
            "retained": retained,
            "excluded": excluded_total,
        })
    return AtomicProcessingResult(
        schema="setec-imessage-atomic-processing-result/1",
        selected_outgoing_rows=universe.selected_eligible_rows,
        considered_rows=considered,
        not_considered_after_bound=not_considered,
        retained_rows=retained,
        excluded_considered_by_final_reason={
            reason: counts.get(reason, 0) for reason in EXCLUSION_REASONS
        },
        rows=tuple(rows),
    )


def _locator_universe_hash(locators: Sequence[str]) -> str:
    if len(locators) != len(set(locators)):
        raise AtomicAcquisitionError("entry locator universe is not unique")
    return _sha256_tag(_canonical_json_bytes(sorted(locators)))


def candidate_universe_receipt_payload(
    universe: AtomicCandidateUniverse, key_bytes: bytes
) -> dict[str, Any]:
    """Describe candidate/selected closure without raw identities or prose."""

    key = _validate_hmac_key(key_bytes)
    _validated_candidate_membership(universe)
    for value in (
        universe.candidate_outgoing_rows, universe.candidate_eligible_rows,
        universe.held_missing_chat_join_rows, universe.ambiguous_multi_chat_rows,
        universe.selected_outgoing_rows, universe.selected_eligible_rows,
        universe.selected_held_missing_chat_join_rows,
        universe.selected_ambiguous_multi_chat_rows,
    ):
        if type(value) is not int or value < 0:
            raise AtomicAcquisitionError("candidate receipt counts are invalid")
    if len(universe.candidates) != universe.candidate_eligible_rows:
        raise AtomicAcquisitionError("candidate receipt coverage drifted")
    if len(universe.selected) != universe.selected_eligible_rows:
        raise AtomicAcquisitionError("selected candidate identity coverage drifted")
    candidate_by_rowid: dict[int, AtomicCandidate] = {}
    for candidate in universe.candidates:
        if candidate.snapshot_rowid in candidate_by_rowid:
            raise AtomicAcquisitionError("candidate snapshot identity is not unique")
        candidate_by_rowid[candidate.snapshot_rowid] = candidate
    selected_rowids: set[int] = set()
    for selected_candidate in universe.selected:
        rowid = selected_candidate.snapshot_rowid
        if (
            rowid in selected_rowids
            or candidate_by_rowid.get(rowid) != selected_candidate
        ):
            raise AtomicAcquisitionError("selected candidate identity coverage drifted")
        selected_rowids.add(rowid)
    records: list[dict[str, Any]] = []
    locators: list[str] = []
    selected_locators: list[str] = []
    for candidate in universe.candidates:
        item = entry_locator(key, candidate.message_guid)
        group = group_locator(key, candidate.chat_guid)
        locators.append(item)
        selected = candidate.snapshot_rowid in selected_rowids
        if selected:
            selected_locators.append(item)
        records.append(
            {
                "entry_locator": item,
                "group_locator": group,
                "unix_nanoseconds": candidate.unix_nanoseconds,
                "local_date": candidate.local_date.isoformat(),
                "group_status": candidate.group_status,
                "attachment_count": len(candidate.attachment_ids),
                "selected_by_date": selected,
            }
        )
    selected_held_guids = {row.message_guid for row in universe.selected_held}
    for held in universe.held:
        item = entry_locator(key, held.message_guid)
        locators.append(item)
        selected = held.message_guid in selected_held_guids
        if selected:
            selected_locators.append(item)
        records.append({
            "entry_locator": item,
            "group_locator": None,
            "unix_nanoseconds": held.unix_nanoseconds,
            "local_date": held.local_date.isoformat(),
            "group_status": None,
            "attachment_count": None,
            "selected_by_date": selected,
            "chat_join_disposition": "missing_chat_join",
        })
    for record in records:
        record.setdefault("chat_join_disposition", "eligible")
    if len(records) != universe.candidate_outgoing_rows:
        raise AtomicAcquisitionError("candidate receipt coverage drifted")
    if len(selected_locators) != universe.selected_outgoing_rows:
        raise AtomicAcquisitionError("selected candidate locator coverage drifted")
    records.sort(key=lambda row: (row["unix_nanoseconds"], row["entry_locator"]))
    return {
        "schema": "setec-imessage-atomic-candidate-receipt/2",
        "candidate_outgoing_rows": universe.candidate_outgoing_rows,
        "candidate_eligible_rows": universe.candidate_eligible_rows,
        "held_missing_chat_join_rows": universe.held_missing_chat_join_rows,
        "ambiguous_multi_chat_rows": universe.ambiguous_multi_chat_rows,
        "selected_outgoing_rows": universe.selected_outgoing_rows,
        "selected_eligible_rows": universe.selected_eligible_rows,
        "selected_held_missing_chat_join_rows": universe.selected_held_missing_chat_join_rows,
        "selected_ambiguous_multi_chat_rows": universe.selected_ambiguous_multi_chat_rows,
        "hmac_key_id": hmac_key_id(key),
        "candidate_locator_universe_hash": _locator_universe_hash(locators),
        "selected_locator_universe_hash": _locator_universe_hash(selected_locators),
        "records": records,
        "privacy": {"contains_source_prose": False, "contains_raw_identity": False},
    }


def _validated_candidate_membership(
    universe: AtomicCandidateUniverse,
) -> tuple[tuple[AtomicCandidate, ...], frozenset[str]]:
    if type(universe) is not AtomicCandidateUniverse or universe.schema != (
        "setec-imessage-atomic-candidate-universe/2"
    ):
        raise AtomicAcquisitionError("private map candidate universe is invalid")
    if any(
        type(value) is not tuple
        for value in (
            universe.candidates, universe.selected, universe.held,
            universe.selected_held,
        )
    ):
        raise AtomicAcquisitionError("private map universe membership is not closed")
    candidates = universe.candidates
    selected = universe.selected
    if (
        type(universe.candidate_outgoing_rows) is not int
        or universe.candidate_outgoing_rows < 0
        or type(universe.candidate_eligible_rows) is not int
        or universe.candidate_eligible_rows < 0
        or type(universe.held_missing_chat_join_rows) is not int
        or universe.held_missing_chat_join_rows < 0
        or type(universe.ambiguous_multi_chat_rows) is not int
        or universe.ambiguous_multi_chat_rows != 0
        or type(universe.selected_outgoing_rows) is not int
        or universe.selected_outgoing_rows < 0
        or type(universe.selected_eligible_rows) is not int
        or universe.selected_eligible_rows < 0
        or type(universe.selected_held_missing_chat_join_rows) is not int
        or universe.selected_held_missing_chat_join_rows < 0
        or type(universe.selected_ambiguous_multi_chat_rows) is not int
        or universe.selected_ambiguous_multi_chat_rows != 0
        or universe.candidate_eligible_rows != len(candidates)
        or universe.held_missing_chat_join_rows != len(universe.held)
        or universe.candidate_outgoing_rows
        != universe.candidate_eligible_rows + universe.held_missing_chat_join_rows
        or universe.selected_eligible_rows != len(selected)
        or universe.selected_held_missing_chat_join_rows != len(universe.selected_held)
        or universe.selected_outgoing_rows
        != universe.selected_eligible_rows + universe.selected_held_missing_chat_join_rows
    ):
        raise AtomicAcquisitionError("private map universe counts drifted")
    by_guid: dict[str, AtomicCandidate] = {}
    rowids: set[int] = set()
    chat_metadata: dict[str, tuple[str | None, str | None, int, str]] = {}
    for candidate in candidates:
        if type(candidate) is not AtomicCandidate:
            raise AtomicAcquisitionError("private map candidate is invalid")
        if (
            type(candidate.snapshot_rowid) is not int
            or candidate.snapshot_rowid < 1
            or candidate.snapshot_rowid in rowids
        ):
            raise AtomicAcquisitionError("private map snapshot row identity is invalid")
        rowids.add(candidate.snapshot_rowid)
        guid = validate_stable_guid(candidate.message_guid, identity="message")
        if guid in by_guid:
            raise AtomicAcquisitionError("private map message identity repeats")
        chat_guid = validate_stable_guid(candidate.chat_guid, identity="chat")
        if (
            type(candidate.chat_identifier) not in (str, type(None))
            or type(candidate.room_name) not in (str, type(None))
            or type(candidate.style) is not int
            or candidate.group_status
            not in {GROUP_STATUS_GROUP, GROUP_STATUS_DIRECT, GROUP_STATUS_UNKNOWN}
        ):
            raise AtomicAcquisitionError("private contact metadata is invalid")
        chat_identifier = _normalized_optional_text(candidate.chat_identifier)
        room_name = _normalized_optional_text(candidate.room_name)
        if (
            classify_group_status(room_name, candidate.style)
            != candidate.group_status
        ):
            raise AtomicAcquisitionError("private contact metadata is invalid")
        metadata = (
            chat_identifier,
            room_name,
            candidate.style,
            candidate.group_status,
        )
        previous_metadata = chat_metadata.setdefault(chat_guid, metadata)
        if previous_metadata != metadata:
            raise AtomicAcquisitionError("private contact metadata drifted")
        by_guid[guid] = candidate
    held_by_guid: dict[str, AtomicHeldSourceRow] = {}
    for held in universe.held:
        if (
            type(held) is not AtomicHeldSourceRow
            or type(held.snapshot_rowid) is not int
            or held.snapshot_rowid < 1
            or held.snapshot_rowid in rowids
            or held.reason != "missing_chat_join"
            or type(held.unix_nanoseconds) is not int
            or type(held.local_date) is not _dt.date
        ):
            raise AtomicAcquisitionError("private map held source row is invalid")
        rowids.add(held.snapshot_rowid)
        guid = validate_stable_guid(held.message_guid, identity="message")
        if guid in by_guid or guid in held_by_guid:
            raise AtomicAcquisitionError("private map message identity repeats")
        held_by_guid[guid] = held
    selected_guids: set[str] = set()
    for candidate in selected:
        if (
            type(candidate) is not AtomicCandidate
            or by_guid.get(candidate.message_guid) != candidate
            or candidate.message_guid in selected_guids
        ):
            raise AtomicAcquisitionError("private map selected membership drifted")
        selected_guids.add(candidate.message_guid)
    selected_held_guids: set[str] = set()
    for held in universe.selected_held:
        if (
            type(held) is not AtomicHeldSourceRow
            or held_by_guid.get(held.message_guid) != held
            or held.message_guid in selected_held_guids
        ):
            raise AtomicAcquisitionError("private map selected hold membership drifted")
        selected_held_guids.add(held.message_guid)
    return candidates, frozenset(selected_guids)


def private_contact_map_payload(
    universe: AtomicCandidateUniverse, key_bytes: bytes
) -> dict[str, Any]:
    """Allocate stable aliases for the complete selected-chat universe."""

    candidates, selected_guids = _validated_candidate_membership(universe)
    key = _validate_hmac_key(key_bytes)
    selected_chats: dict[str, tuple[str | None, str | None, int, str]] = {}
    for candidate in candidates:
        if candidate.message_guid not in selected_guids:
            continue
        if (
            type(candidate.chat_identifier) not in (str, type(None))
            or type(candidate.room_name) not in (str, type(None))
        ):
            raise AtomicAcquisitionError("private contact metadata is invalid")
        chat_identifier = _normalized_optional_text(candidate.chat_identifier)
        room_name = _normalized_optional_text(candidate.room_name)
        if (
            type(candidate.style) is not int
            or candidate.group_status not in {
                GROUP_STATUS_GROUP, GROUP_STATUS_DIRECT, GROUP_STATUS_UNKNOWN
            }
            or classify_group_status(room_name, candidate.style)
            != candidate.group_status
        ):
            raise AtomicAcquisitionError("private contact metadata is invalid")
        metadata = (
            chat_identifier,
            room_name,
            candidate.style,
            candidate.group_status,
        )
        previous = selected_chats.setdefault(candidate.chat_guid, metadata)
        if previous != metadata:
            raise AtomicAcquisitionError("private contact metadata drifted")
    if len(selected_chats) > 999_999:
        raise AtomicAcquisitionError("private contact alias space is exhausted")
    located = [
        (group_locator(key, chat_guid), chat_guid, metadata)
        for chat_guid, metadata in selected_chats.items()
    ]
    if len({item[0] for item in located}) != len(located):
        raise AtomicAcquisitionError("private contact group locators collide")
    keyed = sorted(located)
    contacts = []
    for index, (group, chat_guid, metadata) in enumerate(keyed, start=1):
        chat_identifier, room_name, style, group_status = metadata
        contacts.append({
            "contact_alias": f"contact-{index:06d}",
            "group_locator": group,
            "chat_guid": chat_guid,
            "chat_identifier": chat_identifier,
            "room_name": room_name,
            "style": style,
            "group_status": group_status,
        })
    return {
        "schema": "setec-imessage-atomic-private-contact-map/1",
        "contacts": contacts,
    }


def private_source_identity_map_payload(
    universe: AtomicCandidateUniverse,
    key_bytes: bytes,
    contact_map: dict[str, Any],
) -> dict[str, Any]:
    """Bind every candidate raw message identity without persisting ROWID."""

    candidates, selected_guids = _validated_candidate_membership(universe)
    key = _validate_hmac_key(key_bytes)
    expected_contacts = private_contact_map_payload(universe, key)
    if contact_map != expected_contacts:
        raise AtomicAcquisitionError("private contact map binding drifted")
    aliases = {
        row["group_locator"]: row["contact_alias"]
        for row in expected_contacts["contacts"]
    }
    all_count = len(candidates) + len(universe.held)
    if all_count > 999_999:
        raise AtomicAcquisitionError("private source ordinal space is exhausted")
    chat_guids = sorted({candidate.chat_guid for candidate in candidates})
    located_chats = [
        (group_locator(key, chat_guid), chat_guid) for chat_guid in chat_guids
    ]
    if len({row[0] for row in located_chats}) != len(located_chats):
        raise AtomicAcquisitionError("private source group locators collide")
    locator_rows: list[tuple[str, AtomicCandidate | AtomicHeldSourceRow, str | None, str]] = [
        (
            entry_locator(key, candidate.message_guid),
            candidate,
            group_locator(key, candidate.chat_guid),
            "eligible",
        )
        for candidate in candidates
    ]
    locator_rows.extend(
        (
            entry_locator(key, held.message_guid),
            held,
            None,
            "missing_chat_join",
        )
        for held in universe.held
    )
    if len({row[0] for row in locator_rows}) != len(locator_rows):
        raise AtomicAcquisitionError("private source entry locators collide")
    ordered = sorted(locator_rows, key=lambda row: row[0])
    entries = []
    selected_locators = []
    selected_held_guids = {row.message_guid for row in universe.selected_held}
    for index, (entry, candidate, group, disposition) in enumerate(ordered, start=1):
        selected = (
            candidate.message_guid in selected_guids
            or candidate.message_guid in selected_held_guids
        )
        if selected:
            selected_locators.append(entry)
        entries.append({
            "source_ordinal": f"source-{index:06d}",
            "entry_locator": entry,
            "message_guid": candidate.message_guid,
            "group_locator": group,
            "contact_alias": (
                aliases.get(group) if selected and group is not None else None
            ),
            "selected_by_date": selected,
            "chat_join_disposition": disposition,
        })
    candidate_locators = [row["entry_locator"] for row in entries]
    return {
        "schema": "setec-imessage-atomic-private-source-identity-map/2",
        "candidate_outgoing_rows": len(entries),
        "candidate_eligible_rows": universe.candidate_eligible_rows,
        "held_missing_chat_join_rows": universe.held_missing_chat_join_rows,
        "ambiguous_multi_chat_rows": universe.ambiguous_multi_chat_rows,
        "selected_outgoing_rows": len(selected_locators),
        "selected_eligible_rows": universe.selected_eligible_rows,
        "selected_held_missing_chat_join_rows": universe.selected_held_missing_chat_join_rows,
        "selected_ambiguous_multi_chat_rows": universe.selected_ambiguous_multi_chat_rows,
        "candidate_locator_universe_hash": _locator_universe_hash(candidate_locators),
        "selected_locator_universe_hash": _locator_universe_hash(selected_locators),
        "entries": entries,
    }


def private_source_hold_ledger_payload(
    universe: AtomicCandidateUniverse,
    key_bytes: bytes,
    source_map: dict[str, Any],
    *,
    snapshot_file_sha256: str,
) -> dict[str, Any]:
    """Bind every chatless source disposition without prose or raw identity."""

    _validated_candidate_membership(universe)
    key = _validate_hmac_key(key_bytes)
    if not _is_sha256_tag(snapshot_file_sha256):
        raise AtomicAcquisitionError("private source hold snapshot binding is invalid")
    expected_contact = private_contact_map_payload(universe, key)
    expected_source = private_source_identity_map_payload(
        universe, key, expected_contact
    )
    if source_map != expected_source:
        raise AtomicAcquisitionError("private source hold map binding drifted")
    source_by_locator = {
        row["entry_locator"]: row for row in source_map["entries"]
    }
    selected_held = {row.message_guid for row in universe.selected_held}
    holds: list[dict[str, Any]] = []
    for held in universe.held:
        locator = entry_locator(key, held.message_guid)
        source = source_by_locator.get(locator)
        if (
            type(source) is not dict
            or source.get("chat_join_disposition") != "missing_chat_join"
            or source.get("group_locator") is not None
            or source.get("contact_alias") is not None
        ):
            raise AtomicAcquisitionError("private source hold identity drifted")
        holds.append({
            "source_ordinal": source["source_ordinal"],
            "entry_locator": locator,
            "reason": "missing_chat_join",
            "selected_by_date": held.message_guid in selected_held,
        })
    holds.sort(key=lambda row: row["entry_locator"])
    return {
        "schema": "setec-imessage-atomic-private-source-hold-ledger/1",
        "snapshot_file_sha256": snapshot_file_sha256,
        "chat_join_policy_version": CHAT_JOIN_POLICY_VERSION,
        "candidate_outgoing_rows": universe.candidate_outgoing_rows,
        "held_missing_chat_join_rows": universe.held_missing_chat_join_rows,
        "selected_held_missing_chat_join_rows": (
            universe.selected_held_missing_chat_join_rows
        ),
        "candidate_locator_universe_hash": source_map[
            "candidate_locator_universe_hash"
        ],
        "holds": holds,
    }


def _contextual_exact_payload_validator(
    expected: dict[str, Any],
    *,
    artifact_label: str,
    base_validator: Callable[[object], dict[str, Any]] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    expected_raw = _canonical_json_bytes(expected)

    def validate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            rebuilt = base_validator(payload) if base_validator is not None else payload
            if type(rebuilt) is not dict or _canonical_json_bytes(rebuilt) != expected_raw:
                raise BootstrapStateError(f"{artifact_label} binding drifted")
            return json.loads(expected_raw)
        except BootstrapStateError:
            raise
        except Exception as exc:
            raise BootstrapStateError(f"{artifact_label} schema is invalid") from exc

    return validate


def _close_private_json(
    *,
    filename: str,
    label: str,
    max_bytes: int,
    payload: dict[str, Any],
    validator: Callable[[dict[str, Any]], dict[str, Any]],
) -> ClosedPrivateJson:
    filename = _bootstrap_basename(filename, "initialization artifact name")
    try:
        label = _binding_text("initialization artifact label", label)
        if type(max_bytes) is not int or max_bytes < 1:
            raise BootstrapStateError("initialization artifact ceiling is invalid")
        raw = _canonical_json_bytes(payload)
        validated = _decode_canonical_private_json(
            raw,
            max_bytes=max_bytes,
            validator=validator,
            artifact_label=label,
        )
    except BootstrapStateError:
        raise
    except Exception as exc:
        raise BootstrapStateError(f"{label} cannot be closed") from exc
    return ClosedPrivateJson(
        filename=filename,
        label=label,
        max_bytes=max_bytes,
        payload=json.loads(_canonical_json_bytes(validated)),
        raw=raw,
        digest=_sha256_tag(raw),
    )


def _closed_initialization_artifact_validator(
    closed: ClosedPrivateJson,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    try:
        if (
            type(closed) is not ClosedPrivateJson
            or type(closed.raw) is not bytes
            or type(closed.max_bytes) is not int
            or closed.max_bytes < 1
            or closed.digest != _sha256_tag(closed.raw)
            or not closed.raw
            or len(closed.raw) > closed.max_bytes
            or _canonical_json_bytes(closed.payload) != closed.raw
        ):
            raise BootstrapStateError("closed initialization artifact is invalid")
    except BootstrapStateError:
        raise
    except Exception as exc:
        raise BootstrapStateError("closed initialization artifact is invalid") from exc
    base_validators: dict[str, Callable[[object], dict[str, Any]]] = {
        SEMANTIC_OPTIONS_FILENAME: _validated_semantic_options,
        RUN_CONTROLS_FILENAME: _validated_run_controls,
        SMOKE_POLICY_FILENAME: _validated_smoke_policy,
    }
    return _contextual_exact_payload_validator(
        closed.payload,
        artifact_label=closed.label,
        base_validator=base_validators.get(closed.filename),
    )


def build_initialization_closure(
    *,
    snapshot_metadata: SnapshotMetadata,
    schema_info: AtomicSchemaInfo,
    universe: AtomicCandidateUniverse,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
) -> InitializationClosure:
    """Recompute and close every deterministic pre-prose initialization file."""

    try:
        if type(snapshot_metadata) is not SnapshotMetadata:
            raise BootstrapStateError("initialization snapshot metadata is invalid")
        snapshot_payload = _validated_bootstrap_snapshot(asdict(snapshot_metadata))
        bound_snapshot = SnapshotMetadata(**snapshot_payload)
        if bound_snapshot != snapshot_metadata:
            raise BootstrapStateError("initialization snapshot metadata drifted")
        if type(schema_info) is not AtomicSchemaInfo:
            raise BootstrapStateError("initialization schema binding is invalid")
        key = _validate_hmac_key(key_bytes)
        key_id = hmac_key_id(key)
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        smoke = smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=bound_snapshot,
            schema_info=schema_info,
            hmac_key_id_value=key_id,
        )
        contact = private_contact_map_payload(universe, key)
        source = private_source_identity_map_payload(universe, key, contact)
        hold = private_source_hold_ledger_payload(
            universe,
            key,
            source,
            snapshot_file_sha256=bound_snapshot.file_sha256,
        )

        semantic_closed = _close_private_json(
            filename=SEMANTIC_OPTIONS_FILENAME,
            label="semantic options",
            max_bytes=MAX_SEMANTIC_OPTIONS_BYTES,
            payload=semantic,
            validator=_contextual_exact_payload_validator(
                semantic,
                artifact_label="semantic options",
                base_validator=_validated_semantic_options,
            ),
        )
        controls_closed = _close_private_json(
            filename=RUN_CONTROLS_FILENAME,
            label="run controls",
            max_bytes=MAX_RUN_CONTROLS_BYTES,
            payload=controls,
            validator=_contextual_exact_payload_validator(
                controls,
                artifact_label="run controls",
                base_validator=_validated_run_controls,
            ),
        )
        smoke_closed = _close_private_json(
            filename=SMOKE_POLICY_FILENAME,
            label="smoke policy",
            max_bytes=MAX_SMOKE_POLICY_BYTES,
            payload=smoke,
            validator=_contextual_exact_payload_validator(
                smoke,
                artifact_label="smoke policy",
                base_validator=_validated_smoke_policy,
            ),
        )
        contact_closed = _close_private_json(
            filename=PRIVATE_CONTACT_MAP_FILENAME,
            label="private contact map",
            max_bytes=MAX_PRIVATE_CONTACT_MAP_BYTES,
            payload=contact,
            validator=_contextual_exact_payload_validator(
                private_contact_map_payload(universe, key),
                artifact_label="private contact map",
            ),
        )
        source_closed = _close_private_json(
            filename=PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
            label="private source identity map",
            max_bytes=MAX_PRIVATE_SOURCE_IDENTITY_MAP_BYTES,
            payload=source,
            validator=_contextual_exact_payload_validator(
                private_source_identity_map_payload(
                    universe,
                    key,
                    private_contact_map_payload(universe, key),
                ),
                artifact_label="private source identity map",
            ),
        )
        hold_closed = _close_private_json(
            filename=PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
            label="private source hold ledger",
            max_bytes=MAX_PRIVATE_SOURCE_HOLD_LEDGER_BYTES,
            payload=hold,
            validator=_contextual_exact_payload_validator(
                private_source_hold_ledger_payload(
                    universe,
                    key,
                    private_source_identity_map_payload(
                        universe,
                        key,
                        private_contact_map_payload(universe, key),
                    ),
                    snapshot_file_sha256=bound_snapshot.file_sha256,
                ),
                artifact_label="private source hold ledger",
            ),
        )
        owner = run_owner_payload(
            snapshot_metadata=bound_snapshot,
            semantic_options=semantic,
            run_controls=controls,
            smoke_policy=smoke,
            hmac_key_id_value=key_id,
            contact_map_hash=contact_closed.digest,
            source_identity_map_hash=source_closed.digest,
            source_hold_ledger_hash=hold_closed.digest,
        )
        owner_closed = _close_private_json(
            filename=RUN_OWNER_FILENAME,
            label="run owner",
            max_bytes=MAX_RUN_OWNER_BYTES,
            payload=owner,
            validator=_contextual_exact_payload_validator(
                run_owner_payload(
                    snapshot_metadata=bound_snapshot,
                    semantic_options=semantic,
                    run_controls=controls,
                    smoke_policy=smoke,
                    hmac_key_id_value=key_id,
                    contact_map_hash=contact_closed.digest,
                    source_identity_map_hash=source_closed.digest,
                    source_hold_ledger_hash=hold_closed.digest,
                ),
                artifact_label="run owner",
            ),
        )
        artifacts = (
            semantic_closed,
            controls_closed,
            smoke_closed,
            contact_closed,
            source_closed,
            hold_closed,
            owner_closed,
        )
        if len({artifact.filename for artifact in artifacts}) != len(artifacts):
            raise BootstrapStateError("initialization artifact names collide")
        universe_binding = _validated_universe_binding(
            {
                "candidate_outgoing_rows": source["candidate_outgoing_rows"],
                "candidate_eligible_rows": source["candidate_eligible_rows"],
                "held_missing_chat_join_rows": source["held_missing_chat_join_rows"],
                "ambiguous_multi_chat_rows": source["ambiguous_multi_chat_rows"],
                "selected_outgoing_rows": source["selected_outgoing_rows"],
                "selected_eligible_rows": source["selected_eligible_rows"],
                "selected_held_missing_chat_join_rows": source["selected_held_missing_chat_join_rows"],
                "selected_ambiguous_multi_chat_rows": source["selected_ambiguous_multi_chat_rows"],
                "candidate_locator_universe_hash": source[
                    "candidate_locator_universe_hash"
                ],
                "selected_locator_universe_hash": source[
                    "selected_locator_universe_hash"
                ],
            }
        )
        children: dict[
            str, ExpectedPrivateFile | ExpectedPrivateDirectory
        ] = {
            SNAPSHOT_FILENAME: ExpectedPrivateFile(
                byte_size=bound_snapshot.byte_size,
                sha256=bound_snapshot.file_sha256,
            )
        }
        children.update(
            {
                artifact.filename: ExpectedPrivateFile(
                    byte_size=len(artifact.raw), sha256=artifact.digest
                )
                for artifact in artifacts
            }
        )
        expected_tree = _validated_expected_private_tree(
            ExpectedPrivateDirectory(children=children)
        )
        if set(expected_tree.children) != {
            SNAPSHOT_FILENAME,
            SEMANTIC_OPTIONS_FILENAME,
            RUN_CONTROLS_FILENAME,
            SMOKE_POLICY_FILENAME,
            PRIVATE_CONTACT_MAP_FILENAME,
            PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
            PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
            RUN_OWNER_FILENAME,
        }:
            raise BootstrapStateError("initialization tree inventory drifted")
        return InitializationClosure(
            artifacts=artifacts,
            expected_tree=expected_tree,
            universe_binding=universe_binding,
        )
    except BootstrapStateError:
        raise
    except Exception as exc:
        raise BootstrapStateError("initialization closure is invalid") from exc


def _create_or_verify_private_json_at(
    parent_fd: int,
    closed: ClosedPrivateJson,
) -> str:
    """Create one immutable initialization artifact or verify an exact residue."""

    if sys.platform != "darwin":
        raise BootstrapStateError(
            "durable initialization artifacts are available only on the macOS host"
        )
    validator = _closed_initialization_artifact_validator(closed)
    try:
        os.stat(closed.filename, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _write_private_canonical_json_at(
            parent_fd,
            closed.filename,
            closed.payload,
            max_bytes=closed.max_bytes,
            validator=validator,
            artifact_label=closed.label,
            replace_existing=False,
            expected_existing_digest=None,
        )
    except OSError as exc:
        raise BootstrapStateError(f"cannot inspect {closed.label} residue") from exc
    _, _, digest, raw = _read_private_canonical_json_at(
        parent_fd,
        closed.filename,
        max_bytes=closed.max_bytes,
        validator=validator,
        artifact_label=closed.label,
    )
    if digest != closed.digest or raw != closed.raw:
        raise BootstrapStateError(f"{closed.label} existing bytes drifted")
    return digest


def _initialization_dependency_artifacts(
    closure: InitializationClosure,
) -> tuple[ClosedPrivateJson, ...]:
    if type(closure) is not InitializationClosure:
        raise BootstrapStateError("initialization closure is invalid")
    dependencies = closure.artifacts[:-1]
    if tuple(artifact.filename for artifact in dependencies) != (
        INITIALIZATION_DEPENDENCY_FILENAMES
    ):
        raise BootstrapStateError("initialization dependency order is invalid")
    return dependencies


def _write_initialization_dependencies_at(
    parent_fd: int, closure: InitializationClosure
) -> dict[str, str]:
    written: dict[str, str] = {}
    for artifact in _initialization_dependency_artifacts(closure):
        written[artifact.filename] = _create_or_verify_private_json_at(
            parent_fd, artifact
        )
    return written


def _reread_initialization_dependencies_at(
    parent_fd: int, closure: InitializationClosure
) -> dict[str, tuple[str, bytes]]:
    evidence: dict[str, tuple[str, bytes]] = {}
    for closed in _initialization_dependency_artifacts(closure):
        validator = _closed_initialization_artifact_validator(closed)
        _, _, digest, raw = _read_private_canonical_json_at(
            parent_fd,
            closed.filename,
            max_bytes=closed.max_bytes,
            validator=validator,
            artifact_label=closed.label,
        )
        if digest != closed.digest or raw != closed.raw:
            raise BootstrapStateError(f"{closed.label} stable reread drifted")
        evidence[closed.filename] = (digest, raw)
    source = json.loads(evidence[PRIVATE_SOURCE_IDENTITY_MAP_FILENAME][1])
    source_binding = _validated_universe_binding(
        {
            "candidate_outgoing_rows": source.get("candidate_outgoing_rows"),
            "candidate_eligible_rows": source.get("candidate_eligible_rows"),
            "held_missing_chat_join_rows": source.get("held_missing_chat_join_rows"),
            "ambiguous_multi_chat_rows": source.get("ambiguous_multi_chat_rows"),
            "selected_outgoing_rows": source.get("selected_outgoing_rows"),
            "selected_eligible_rows": source.get("selected_eligible_rows"),
            "selected_held_missing_chat_join_rows": source.get(
                "selected_held_missing_chat_join_rows"
            ),
            "selected_ambiguous_multi_chat_rows": source.get(
                "selected_ambiguous_multi_chat_rows"
            ),
            "candidate_locator_universe_hash": source.get(
                "candidate_locator_universe_hash"
            ),
            "selected_locator_universe_hash": source.get(
                "selected_locator_universe_hash"
            ),
        }
    )
    if source_binding != closure.universe_binding:
        raise BootstrapStateError("initialization universe binding drifted")
    return evidence


def _initialization_dependency_prefix_expected_tree(
    closure: InitializationClosure,
    prefix: Sequence[str],
) -> ExpectedPrivateDirectory:
    dependencies = _initialization_dependency_artifacts(closure)
    names = tuple(prefix)
    if names != tuple(
        artifact.filename for artifact in dependencies[: len(names)]
    ):
        raise BootstrapStateError("initialization dependency prefix is invalid")
    allowed = (SNAPSHOT_FILENAME, *names)
    source_tree = _validated_expected_private_tree(closure.expected_tree)
    if any(name not in source_tree.children for name in allowed):
        raise BootstrapStateError("initialization dependency tree is incomplete")
    return _validated_expected_private_tree(
        ExpectedPrivateDirectory(
            children={name: source_tree.children[name] for name in allowed}
        )
    )


def _reread_initialization_dependency_prefix_at(
    parent_fd: int,
    closure: InitializationClosure,
    prefix: Sequence[str],
) -> dict[str, tuple[str, bytes]]:
    dependencies = _initialization_dependency_artifacts(closure)
    names = tuple(prefix)
    if names != tuple(
        artifact.filename for artifact in dependencies[: len(names)]
    ):
        raise BootstrapStateError("initialization dependency prefix is invalid")
    evidence: dict[str, tuple[str, bytes]] = {}
    for closed in dependencies[: len(names)]:
        validator = _closed_initialization_artifact_validator(closed)
        _, _, digest, raw = _read_private_canonical_json_at(
            parent_fd,
            closed.filename,
            max_bytes=closed.max_bytes,
            validator=validator,
            artifact_label=closed.label,
        )
        if digest != closed.digest or raw != closed.raw:
            raise BootstrapStateError(f"{closed.label} stable reread drifted")
        evidence[closed.filename] = (digest, raw)
    if len(names) == len(dependencies):
        source = json.loads(evidence[PRIVATE_SOURCE_IDENTITY_MAP_FILENAME][1])
        source_binding = _validated_universe_binding(
            {
                "candidate_outgoing_rows": source.get("candidate_outgoing_rows"),
                "candidate_eligible_rows": source.get("candidate_eligible_rows"),
                "held_missing_chat_join_rows": source.get("held_missing_chat_join_rows"),
                "ambiguous_multi_chat_rows": source.get("ambiguous_multi_chat_rows"),
                "selected_outgoing_rows": source.get("selected_outgoing_rows"),
                "selected_eligible_rows": source.get("selected_eligible_rows"),
                "selected_held_missing_chat_join_rows": source.get(
                    "selected_held_missing_chat_join_rows"
                ),
                "selected_ambiguous_multi_chat_rows": source.get(
                    "selected_ambiguous_multi_chat_rows"
                ),
                "candidate_locator_universe_hash": source.get(
                    "candidate_locator_universe_hash"
                ),
                "selected_locator_universe_hash": source.get(
                    "selected_locator_universe_hash"
                ),
            }
        )
        if source_binding != closure.universe_binding:
            raise BootstrapStateError("initialization universe binding drifted")
    return evidence


def _validated_initialization_dependency_prefix_evidence(
    closure: InitializationClosure,
    prefix: Sequence[str],
    evidence: dict[str, tuple[str, bytes]],
) -> dict[str, tuple[str, bytes]]:
    dependencies = _initialization_dependency_artifacts(closure)
    names = tuple(prefix)
    if names != tuple(
        artifact.filename for artifact in dependencies[: len(names)]
    ):
        raise BootstrapStateError("initialization dependency prefix is invalid")
    expected = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in dependencies[: len(names)]
    }
    if type(evidence) is not dict or evidence != expected:
        raise BootstrapStateError(
            "initialization dependency prefix evidence drifted"
        )
    return dict(evidence)


def _validated_initialization_closure_evidence(
    closure: InitializationClosure,
    evidence: dict[str, tuple[str, bytes]],
) -> dict[str, tuple[str, bytes]]:
    if type(closure) is not InitializationClosure:
        raise BootstrapStateError("initialization closure is invalid")
    expected = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    if type(evidence) is not dict or evidence != expected:
        raise BootstrapStateError("initialization closure evidence drifted")
    return dict(evidence)


def _owner_from_initialization_dependency_evidence(
    closure: InitializationClosure,
    evidence: dict[str, tuple[str, bytes]],
) -> ClosedPrivateJson:
    expected_names = {
        artifact.filename for artifact in _initialization_dependency_artifacts(closure)
    }
    if type(evidence) is not dict or set(evidence) != expected_names:
        raise BootstrapStateError("initialization dependency evidence is invalid")
    for name, item in evidence.items():
        if (
            type(item) is not tuple
            or len(item) != 2
            or not _is_sha256_tag(item[0])
            or type(item[1]) is not bytes
            or _sha256_tag(item[1]) != item[0]
            or item != (
                closure.artifact(name).digest,
                closure.artifact(name).raw,
            )
        ):
            raise BootstrapStateError("initialization dependency evidence drifted")
    semantic = json.loads(evidence[SEMANTIC_OPTIONS_FILENAME][1])
    controls = json.loads(evidence[RUN_CONTROLS_FILENAME][1])
    smoke = json.loads(evidence[SMOKE_POLICY_FILENAME][1])
    snapshot = SnapshotMetadata(**smoke["snapshot_metadata"])
    owner = run_owner_payload(
        snapshot_metadata=snapshot,
        semantic_options=semantic,
        run_controls=controls,
        smoke_policy=smoke,
        hmac_key_id_value=smoke["hmac"]["key_id"],
        contact_map_hash=evidence[PRIVATE_CONTACT_MAP_FILENAME][0],
        source_identity_map_hash=evidence[PRIVATE_SOURCE_IDENTITY_MAP_FILENAME][0],
        source_hold_ledger_hash=evidence[PRIVATE_SOURCE_HOLD_LEDGER_FILENAME][0],
    )
    closed = _close_private_json(
        filename=RUN_OWNER_FILENAME,
        label="run owner",
        max_bytes=MAX_RUN_OWNER_BYTES,
        payload=owner,
        validator=_contextual_exact_payload_validator(
            owner,
            artifact_label="run owner",
        ),
    )
    expected_owner = closure.artifact(RUN_OWNER_FILENAME)
    if closed.raw != expected_owner.raw or closed.digest != expected_owner.digest:
        raise BootstrapStateError("initialization owner recomputation drifted")
    return closed


def _write_initialization_owner_at(
    parent_fd: int,
    closure: InitializationClosure,
    dependency_evidence: dict[str, tuple[str, bytes]],
) -> str:
    owner = _owner_from_initialization_dependency_evidence(
        closure, dependency_evidence
    )
    return _create_or_verify_private_json_at(parent_fd, owner)


def _reread_initialization_closure_at(
    parent_fd: int, closure: InitializationClosure
) -> dict[str, tuple[str, bytes]]:
    """Stable-read all seven files from an independently rebuilt closure."""

    if type(closure) is not InitializationClosure:
        raise BootstrapStateError("initialization closure is invalid")
    evidence: dict[str, tuple[str, bytes]] = {}
    for closed in closure.artifacts:
        validator = _closed_initialization_artifact_validator(closed)
        _, _, digest, raw = _read_private_canonical_json_at(
            parent_fd,
            closed.filename,
            max_bytes=closed.max_bytes,
            validator=validator,
            artifact_label=closed.label,
        )
        if digest != closed.digest or raw != closed.raw:
            raise BootstrapStateError(f"{closed.label} stable reread drifted")
        evidence[closed.filename] = (digest, raw)
    return evidence


def _resume_bootstrap_universe_for_options_maps_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _prefix_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedUniverseClosed:
    """Reconstruct universe_closed while accepting one exact dependency prefix."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    prefix_rereader = (
        _prefix_rereader or _reread_initialization_dependency_prefix_at
    )
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "universe_closed"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("options/maps universe authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect options/maps universe final name"
            ) from exc
        raise BootstrapStateError("options/maps universe final name exists too early")

    require_final_absent()
    staging_fd: int | None = None
    try:
        (
            staging_fd,
            staging_identity,
            prefix,
            inventory,
        ) = _open_private_staging_dependency_prefix_at(
            parent_fd, journal["staging_name"], _ops=ops
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError(
                "options/maps universe staging identity drifted"
            )
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity
            != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity
            != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("options/maps universe scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError(
                "options/maps universe reconstruction drifted"
            )
        residue = prefix_rereader(staging_fd, closure, prefix)
        _validated_initialization_dependency_prefix_evidence(
            closure, prefix, residue
        )
        expected_tree = _initialization_dependency_prefix_expected_tree(
            closure, prefix
        )
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("options/maps universe first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("options/maps universe journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("options/maps universe final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError(
                "options/maps universe terminal journal changed"
            )
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedUniverseClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError(
            "options/maps universe verification failed"
        ) from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError(
            "options/maps universe result is malformed"
        ) from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "options/maps universe staging close failed"
                    ) from exc


def _close_bootstrap_options_maps_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    prepared_universe: PreparedUniverseClosed,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _writer: Callable[[int, ClosedPrivateJson], str] | None = None,
    _prefix_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedOptionsMapsClosed:
    """Consume universe_closed and publish the five dependency artifacts."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    writer = _writer or _create_or_verify_private_json_at
    prefix_rereader = (
        _prefix_rereader or _reread_initialization_dependency_prefix_at
    )
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(prepared_universe) is not PreparedUniverseClosed:
        raise BootstrapStateError("options/maps prepared universe is invalid")
    staging_fd = prepared_universe.staging_fd
    if type(staging_fd) is not int:
        raise BootstrapStateError("options/maps staging descriptor is invalid")
    staging = Path(staging_path).expanduser().absolute()
    artifact_phase_started = False
    journal_advanced = False
    transferred = False
    try:
        journal = _validated_bootstrap_journal_payload(
            prepared_universe.journal
        )
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            journal["state"] != "universe_closed"
            or prepared_universe.journal_digest
            != canonical_payload_digest(journal)
            or staging.name != journal["staging_name"]
            or bootstrap_staging_name(journal["final_name"])
            != journal["staging_name"]
            or bootstrap_journal_name(journal["final_name"]) != journal_name
            or canonical_payload_digest(semantic)
            != journal["semantic_options_digest"]
            or canonical_payload_digest(controls)
            != journal["run_controls_digest"]
            or hmac_key_id(key) != journal["hmac_key_id"]
            or type(prepared_universe.evidence) is not UniverseClosedEvidence
            or type(prepared_universe.staging_identity) is not tuple
            or len(prepared_universe.staging_identity) != 8
        ):
            raise BootstrapStateError("options/maps universe authority drifted")
        descriptor_info = ops.fstat(staging_fd)
        _validate_private_tree_inode(
            descriptor_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="options/maps staging descriptor",
        )
        if (
            _private_node_identity(descriptor_info)
            != prepared_universe.staging_identity
        ):
            raise BootstrapStateError("options/maps staging descriptor drifted")
        initial_inventory = _closed_staging_inventory_names(
            prepared_universe.evidence.snapshot_evidence.inventory
        )
        initial_prefix = _authorized_initialization_dependency_prefix(
            initial_inventory
        )
        snapshot_evidence = _validated_closed_snapshot_evidence(
            prepared_universe.evidence.snapshot_evidence,
            expected_staging_device_inode=prepared_universe.staging_identity[:2],
            expected_inventory=initial_inventory,
        )
        if snapshot_evidence.staging_identity != prepared_universe.staging_identity:
            raise BootstrapStateError("options/maps staging evidence drifted")
        schema_info = prepared_universe.evidence.schema_info
        universe = prepared_universe.evidence.universe
        closure = _validated_reconstructed_initialization_closure(
            prepared_universe.evidence.initialization,
            snapshot_metadata=snapshot_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != snapshot_evidence.metadata.schema_fingerprint
            or closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
            or journal["completed_artifacts"]
            != {SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256}
        ):
            raise BootstrapStateError("options/maps closure binding drifted")

        def require_final_absent() -> None:
            try:
                ops.stat(journal["final_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise BootstrapStateError(
                    "cannot inspect options/maps final name"
                ) from exc
            raise BootstrapStateError("options/maps final name exists too early")

        def guard(prefix: Sequence[str]) -> tuple[
            int, int, int, int, int, int, int, int
        ]:
            expected_inventory = _closed_staging_inventory_names(
                (SNAPSHOT_FILENAME, *tuple(prefix))
            )
            current, _journal_identity, current_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if (
                current != journal
                or current_digest != prepared_universe.journal_digest
            ):
                raise BootstrapStateError("options/maps journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent()
            return _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                journal["staging_name"],
                staging,
                expected_device_inode=snapshot_evidence.staging_device_inode,
                expected_names=expected_inventory,
                ops=ops,
            )

        guard(initial_prefix)
        residue = prefix_rereader(staging_fd, closure, initial_prefix)
        _validated_initialization_dependency_prefix_evidence(
            closure, initial_prefix, residue
        )
        dependencies = _initialization_dependency_artifacts(closure)
        for index in range(len(initial_prefix), len(dependencies)):
            current_prefix = INITIALIZATION_DEPENDENCY_FILENAMES[:index]
            guard(current_prefix)
            artifact_phase_started = True
            artifact = dependencies[index]
            written_digest = writer(staging_fd, artifact)
            if written_digest != artifact.digest:
                raise BootstrapStateError(
                    f"{artifact.label} writer digest drifted"
                )
            guard(INITIALIZATION_DEPENDENCY_FILENAMES[: index + 1])
        full_prefix = INITIALIZATION_DEPENDENCY_FILENAMES
        current_staging_identity = guard(full_prefix)
        dependency_evidence = prefix_rereader(
            staging_fd, closure, full_prefix
        )
        dependency_evidence = (
            _validated_initialization_dependency_prefix_evidence(
                closure, full_prefix, dependency_evidence
            )
        )
        completed_artifacts = {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{
                name: dependency_evidence[name][0]
                for name in INITIALIZATION_DEPENDENCY_FILENAMES
            },
        }
        next_journal = bootstrap_journal_payload(
            state="options_maps_closed",
            previous_journal_digest=prepared_universe.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=journal["smoke_policy_digest"],
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=journal["universe_binding"],
            completed_artifacts=completed_artifacts,
        )
        guard(full_prefix)
        expected_tree = _initialization_dependency_prefix_expected_tree(
            closure, full_prefix
        )
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if before_seal.root_identity != current_staging_identity:
            raise BootstrapStateError("options/maps prepublish seal drifted")
        current_snapshot_evidence = ClosedSnapshotEvidence(
            metadata=snapshot_evidence.metadata,
            snapshot_identity=snapshot_evidence.snapshot_identity,
            staging_identity=before_seal.root_identity,
            snapshot_device_inode=snapshot_evidence.snapshot_device_inode,
            staging_device_inode=snapshot_evidence.staging_device_inode,
            inventory=_closed_staging_inventory_names(
                (SNAPSHOT_FILENAME, *full_prefix)
            ),
        )
        _validated_closed_snapshot_evidence(
            current_snapshot_evidence,
            expected_staging_device_inode=snapshot_evidence.staging_device_inode,
            expected_inventory=current_snapshot_evidence.inventory,
        )
        _closed_snapshot_identity_from_seal(
            before_seal, current_snapshot_evidence
        )
        guard(full_prefix)
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("published options/maps journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if final_seal.root_identity != current_snapshot_evidence.staging_identity:
            raise BootstrapStateError("options/maps final seal drifted")
        _closed_snapshot_identity_from_seal(
            final_seal, current_snapshot_evidence
        )
        final_evidence = prefix_rereader(staging_fd, closure, full_prefix)
        final_evidence = _validated_initialization_dependency_prefix_evidence(
            closure, full_prefix, final_evidence
        )
        if {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{name: final_evidence[name][0] for name in full_prefix},
        } != next_journal["completed_artifacts"]:
            raise BootstrapStateError("options/maps final evidence drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != next_journal or terminal_digest != published_digest:
            raise BootstrapStateError("options/maps terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedOptionsMapsClosed(
            journal=next_journal,
            journal_digest=published_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=current_snapshot_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            dependency_evidence=final_evidence,
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        if journal_advanced or artifact_phase_started:
            raise BootstrapRecoveryRequired(
                "bootstrap options/maps requires locked recovery"
            ) from exc
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot close bootstrap options/maps") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if journal_advanced or artifact_phase_started:
            raise BootstrapRecoveryRequired(
                "bootstrap options/maps requires locked recovery"
            ) from exc
        raise BootstrapStateError("bootstrap options/maps result is malformed") from exc
    finally:
        if not transferred:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    if journal_advanced or artifact_phase_started:
                        raise BootstrapRecoveryRequired(
                            "options/maps staging close requires recovery"
                        ) from exc
                    raise BootstrapStateError(
                        "options/maps staging close failed"
                    ) from exc


def _resume_bootstrap_options_maps_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _dependency_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedOptionsMapsClosed:
    """Reconstruct and verify one authoritative options_maps_closed state."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    dependency_rereader = (
        _dependency_rereader or _reread_initialization_dependency_prefix_at
    )
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_DEPENDENCY_FILENAMES)
    )
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "options_maps_closed"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("options/maps resume authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect options/maps resume final name"
            ) from exc
        raise BootstrapStateError("options/maps resume final name exists too early")

    require_final_absent()
    staging_fd: int | None = None
    try:
        staging_fd, staging_identity = _open_private_staging_at(
            parent_fd,
            journal["staging_name"],
            expected_names=inventory,
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError("options/maps resume staging identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity
            != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity
            != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("options/maps resume scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("options/maps resume reconstruction drifted")
        dependency_evidence = dependency_rereader(
            staging_fd, closure, INITIALIZATION_DEPENDENCY_FILENAMES
        )
        dependency_evidence = (
            _validated_initialization_dependency_prefix_evidence(
                closure,
                INITIALIZATION_DEPENDENCY_FILENAMES,
                dependency_evidence,
            )
        )
        expected_completed = {
            SNAPSHOT_FILENAME: scanned_evidence.metadata.file_sha256,
            **{
                name: dependency_evidence[name][0]
                for name in INITIALIZATION_DEPENDENCY_FILENAMES
            },
        }
        if journal["completed_artifacts"] != expected_completed:
            raise BootstrapStateError("options/maps resume artifacts drifted")
        expected_tree = _initialization_dependency_prefix_expected_tree(
            closure, INITIALIZATION_DEPENDENCY_FILENAMES
        )
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("options/maps resume first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("options/maps resume journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("options/maps resume final seal drifted")
        final_evidence = dependency_rereader(
            staging_fd, closure, INITIALIZATION_DEPENDENCY_FILENAMES
        )
        final_evidence = _validated_initialization_dependency_prefix_evidence(
            closure,
            INITIALIZATION_DEPENDENCY_FILENAMES,
            final_evidence,
        )
        if final_evidence != dependency_evidence:
            raise BootstrapStateError("options/maps resume final evidence drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError("options/maps resume terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedOptionsMapsClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            dependency_evidence=final_evidence,
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError("options/maps resume verification failed") from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError("options/maps resume result is malformed") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "options/maps resume staging close failed"
                    ) from exc


def _prepare_or_resume_bootstrap_options_maps_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _universe_integrator: Callable[..., PreparedUniverseClosed] | None = None,
    _prefix_universe_resumer: Callable[..., PreparedUniverseClosed] | None = None,
    _closer: Callable[..., PreparedOptionsMapsClosed] | None = None,
    _resumer: Callable[..., PreparedOptionsMapsClosed] | None = None,
) -> PreparedOptionsMapsClosed:
    """Reach one verified options_maps_closed boundary under one held lock."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    universe_integrator = (
        _universe_integrator
        or _prepare_or_resume_bootstrap_universe_closed_locked_at
    )
    prefix_universe_resumer = (
        _prefix_universe_resumer
        or _resume_bootstrap_universe_for_options_maps_locked_at
    )
    closer = _closer or _close_bootstrap_options_maps_locked_at
    resumer = _resumer or _resume_bootstrap_options_maps_closed_locked_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    if (
        reserved["state"] != "reserved"
        or bootstrap_staging_name(reserved["final_name"])
        != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
        or canonical_payload_digest(semantic)
        != reserved["semantic_options_digest"]
        or canonical_payload_digest(controls) != reserved["run_controls_digest"]
        or hmac_key_id(key) != reserved["hmac_key_id"]
    ):
        raise BootstrapStateError("options/maps integration authority drifted")
    immutable = {
        name: reserved[name]
        for name in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }
    full_inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_DEPENDENCY_FILENAMES)
    )

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(name) != value for name, value in immutable.items()):
            raise BootstrapStateError("options/maps integration binding drifted")

    def require_final_absent(journal: dict[str, Any]) -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect options/maps integration final name"
            ) from exc
        raise BootstrapStateError(
            "options/maps integration final name exists too early"
        )

    def close_owned_fd(
        descriptor: int,
        *,
        recovery_required: bool,
        message: str,
        cause: BaseException,
    ) -> None:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        try:
            ops.close(descriptor)
        except (KeyError, OSError) as close_exc:
            raise error_type(f"{message} close failed") from close_exc
        if not recovery_required and isinstance(cause, BootstrapStateError):
            raise cause
        raise error_type(message) from cause

    def validate_prepared_universe(
        result: PreparedUniverseClosed,
        *,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        recovery_required: bool,
    ) -> PreparedUniverseClosed:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        if type(result) is not PreparedUniverseClosed:
            raise error_type("options/maps prepared universe is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise error_type("options/maps prepared universe descriptor is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "universe_closed"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError(
                    "options/maps prepared universe drifted"
                )
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError(
                        "options/maps prepared universe authority changed"
                    )
            elif authoritative_digest is not None:
                raise BootstrapStateError(
                    "options/maps prepared universe authority is malformed"
                )
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="options/maps prepared universe descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(
                    "options/maps prepared universe descriptor drifted"
                )
            inventory = _closed_staging_inventory_names(
                result.evidence.snapshot_evidence.inventory
            )
            _authorized_initialization_dependency_prefix(inventory)
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
                expected_inventory=inventory,
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            schema_info = result.evidence.schema_info
            universe = result.evidence.universe
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=schema_info,
                universe=universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            if (
                snapshot_evidence.staging_identity != result.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(schema_info) is not AtomicSchemaInfo
                or schema_info.schema_fingerprint != metadata.schema_fingerprint
                or closure.artifact(SMOKE_POLICY_FILENAME).digest
                != journal["smoke_policy_digest"]
                or closure.universe_binding != journal["universe_binding"]
                or journal["completed_artifacts"]
                != {SNAPSHOT_FILENAME: metadata.file_sha256}
            ):
                raise BootstrapStateError(
                    "options/maps prepared universe closure drifted"
                )
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError(
                    "options/maps prepared universe journal changed"
                )
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            close_owned_fd(
                result_fd,
                recovery_required=recovery_required,
                message="options/maps prepared universe is invalid",
                cause=exc,
            )

    def validate_closed_result(
        result: PreparedOptionsMapsClosed,
        *,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        predecessor_journal: dict[str, Any] | None,
        expected_result_fd: int | None,
        recovery_required: bool,
    ) -> PreparedOptionsMapsClosed:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        if type(result) is not PreparedOptionsMapsClosed:
            raise error_type("options/maps integration result is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise error_type("options/maps integration descriptor is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "options_maps_closed"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError("options/maps integration result drifted")
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError(
                        "options/maps integration authority changed"
                    )
            elif authoritative_digest is not None:
                raise BootstrapStateError(
                    "options/maps integration authority is malformed"
                )
            if predecessor_journal is not None:
                validate_bootstrap_transition(predecessor_journal, journal)
                if journal["previous_journal_digest"] != canonical_payload_digest(
                    predecessor_journal
                ):
                    raise BootstrapStateError(
                        "options/maps integration predecessor drifted"
                    )
            if expected_result_fd is not None and result_fd != expected_result_fd:
                raise BootstrapStateError(
                    "options/maps integration descriptor transfer drifted"
                )
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="options/maps integration descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(
                    "options/maps integration descriptor identity drifted"
                )
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
                expected_inventory=full_inventory,
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            schema_info = result.evidence.schema_info
            universe = result.evidence.universe
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=schema_info,
                universe=universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            dependency_evidence = (
                _validated_initialization_dependency_prefix_evidence(
                    closure,
                    INITIALIZATION_DEPENDENCY_FILENAMES,
                    result.dependency_evidence,
                )
            )
            expected_completed = {
                SNAPSHOT_FILENAME: metadata.file_sha256,
                **{
                    name: dependency_evidence[name][0]
                    for name in INITIALIZATION_DEPENDENCY_FILENAMES
                },
            }
            if (
                snapshot_evidence.staging_identity != result.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(schema_info) is not AtomicSchemaInfo
                or schema_info.schema_fingerprint != metadata.schema_fingerprint
                or closure.artifact(SMOKE_POLICY_FILENAME).digest
                != journal["smoke_policy_digest"]
                or closure.universe_binding != journal["universe_binding"]
                or journal["completed_artifacts"] != expected_completed
            ):
                raise BootstrapStateError(
                    "options/maps integration closure drifted"
                )
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError("options/maps integration journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            expected_tree = _initialization_dependency_prefix_expected_tree(
                closure, INITIALIZATION_DEPENDENCY_FILENAMES
            )
            final_seal = seal_private_tree_at(
                parent_fd,
                journal["staging_name"],
                expected_tree,
                _ops=ops,
            )
            if (
                final_seal.root_identity != result.staging_identity
                or _closed_snapshot_identity_from_seal(
                    final_seal, snapshot_evidence
                )
                != snapshot_evidence.snapshot_identity
            ):
                raise BootstrapStateError(
                    "options/maps integration final seal drifted"
                )
            terminal, _journal_identity, terminal_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if terminal != journal or terminal_digest != result.journal_digest:
                raise BootstrapStateError(
                    "options/maps integration terminal journal changed"
                )
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            close_owned_fd(
                result_fd,
                recovery_required=recovery_required,
                message="options/maps integration result is invalid",
                cause=exc,
            )

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        ops.stat(journal_name, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
        current_digest = None
    except OSError as exc:
        raise BootstrapStateError("cannot classify options/maps journal") from exc
    else:
        current, _journal_identity, current_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        require_bindings(current)
    if current is not None and current["state"] == "options_maps_closed":
        return validate_closed_result(
            resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            expected_result_fd=None,
            recovery_required=False,
        )
    if current is not None and current["state"] not in {
        "reserved",
        "staging_created",
        "snapshot_in_progress",
        "snapshot_closed",
        "universe_closed",
    }:
        raise BootstrapStateError("options/maps integration state is not resumable")
    if current is not None and current["state"] == "universe_closed":
        prepared_universe = validate_prepared_universe(
            prefix_universe_resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=current,
            authoritative_digest=current_digest,
            recovery_required=False,
        )
    else:
        prepared_universe = validate_prepared_universe(
            universe_integrator(
                parent_fd,
                journal_name,
                reserved,
                staging,
                Path(source_db).expanduser().absolute(),
                lock_fd=lock_fd,
                lock_name=lock_name,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=None,
            authoritative_digest=None,
            recovery_required=True,
        )
    predecessor_journal = prepared_universe.journal
    expected_result_fd = prepared_universe.staging_fd
    closed_result = closer(
        parent_fd,
        journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
        prepared_universe=prepared_universe,
        staging_path=staging,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    return validate_closed_result(
        closed_result,
        authoritative_journal=None,
        authoritative_digest=None,
        predecessor_journal=predecessor_journal,
        expected_result_fd=expected_result_fd,
        recovery_required=True,
    )


def _resume_bootstrap_options_maps_for_owner_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _dependency_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedOptionsMapsClosed:
    """Reconstruct options_maps_closed with an optional exact owner residue."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    dependency_rereader = (
        _dependency_rereader or _reread_initialization_dependency_prefix_at
    )
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "options_maps_closed"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("owner-stage options/maps authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect owner-stage options/maps final name"
            ) from exc
        raise BootstrapStateError(
            "owner-stage options/maps final name exists too early"
        )

    require_final_absent()
    staging_fd: int | None = None
    try:
        (
            staging_fd,
            staging_identity,
            owner_present,
            inventory,
        ) = _open_private_staging_owner_stage_at(
            parent_fd, journal["staging_name"], _ops=ops
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError("owner-stage staging identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity
            != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity
            != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("owner-stage scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("owner-stage reconstruction drifted")
        dependency_evidence = dependency_rereader(
            staging_fd, closure, INITIALIZATION_DEPENDENCY_FILENAMES
        )
        dependency_evidence = (
            _validated_initialization_dependency_prefix_evidence(
                closure,
                INITIALIZATION_DEPENDENCY_FILENAMES,
                dependency_evidence,
            )
        )
        expected_completed = {
            SNAPSHOT_FILENAME: scanned_evidence.metadata.file_sha256,
            **{
                name: dependency_evidence[name][0]
                for name in INITIALIZATION_DEPENDENCY_FILENAMES
            },
        }
        if journal["completed_artifacts"] != expected_completed:
            raise BootstrapStateError("owner-stage dependency journal drifted")
        _owner_from_initialization_dependency_evidence(
            closure, dependency_evidence
        )
        if owner_present:
            initialization_evidence = closure_rereader(staging_fd, closure)
            _validated_initialization_closure_evidence(
                closure, initialization_evidence
            )
            expected_tree = _validated_expected_private_tree(
                closure.expected_tree
            )
        else:
            expected_tree = _initialization_dependency_prefix_expected_tree(
                closure, INITIALIZATION_DEPENDENCY_FILENAMES
            )
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("owner-stage first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("owner-stage journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("owner-stage final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError("owner-stage terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedOptionsMapsClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            dependency_evidence=dependency_evidence,
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError("owner-stage verification failed") from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError("owner-stage result is malformed") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "owner-stage staging close failed"
                    ) from exc


def _close_bootstrap_owner_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    prepared_options_maps: PreparedOptionsMapsClosed,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _owner_writer: Callable[..., str] | None = None,
    _dependency_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedOwnerClosed:
    """Consume options_maps_closed and publish one recomputed owner marker."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    owner_writer = _owner_writer or _write_initialization_owner_at
    dependency_rereader = (
        _dependency_rereader or _reread_initialization_dependency_prefix_at
    )
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(prepared_options_maps) is not PreparedOptionsMapsClosed:
        raise BootstrapStateError("owner close prepared options/maps is invalid")
    staging_fd = prepared_options_maps.staging_fd
    if type(staging_fd) is not int:
        raise BootstrapStateError("owner close staging descriptor is invalid")
    staging = Path(staging_path).expanduser().absolute()
    artifact_phase_started = False
    journal_advanced = False
    transferred = False
    try:
        journal = _validated_bootstrap_journal_payload(
            prepared_options_maps.journal
        )
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            journal["state"] != "options_maps_closed"
            or prepared_options_maps.journal_digest
            != canonical_payload_digest(journal)
            or staging.name != journal["staging_name"]
            or bootstrap_staging_name(journal["final_name"])
            != journal["staging_name"]
            or bootstrap_journal_name(journal["final_name"]) != journal_name
            or canonical_payload_digest(semantic)
            != journal["semantic_options_digest"]
            or canonical_payload_digest(controls)
            != journal["run_controls_digest"]
            or hmac_key_id(key) != journal["hmac_key_id"]
            or type(prepared_options_maps.evidence) is not UniverseClosedEvidence
            or type(prepared_options_maps.staging_identity) is not tuple
            or len(prepared_options_maps.staging_identity) != 8
        ):
            raise BootstrapStateError("owner close authority drifted")
        descriptor_info = ops.fstat(staging_fd)
        _validate_private_tree_inode(
            descriptor_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="owner close staging descriptor",
        )
        if (
            _private_node_identity(descriptor_info)
            != prepared_options_maps.staging_identity
        ):
            raise BootstrapStateError("owner close descriptor drifted")
        inventory = _closed_staging_inventory_names(
            prepared_options_maps.evidence.snapshot_evidence.inventory
        )
        owner_present = _authorized_initialization_owner_stage_inventory(
            inventory
        )
        snapshot_evidence = _validated_closed_snapshot_evidence(
            prepared_options_maps.evidence.snapshot_evidence,
            expected_staging_device_inode=prepared_options_maps.staging_identity[:2],
            expected_inventory=inventory,
        )
        schema_info = prepared_options_maps.evidence.schema_info
        universe = prepared_options_maps.evidence.universe
        closure = _validated_reconstructed_initialization_closure(
            prepared_options_maps.evidence.initialization,
            snapshot_metadata=snapshot_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        prepared_dependencies = (
            _validated_initialization_dependency_prefix_evidence(
                closure,
                INITIALIZATION_DEPENDENCY_FILENAMES,
                prepared_options_maps.dependency_evidence,
            )
        )
        expected_options_completed = {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{
                name: prepared_dependencies[name][0]
                for name in INITIALIZATION_DEPENDENCY_FILENAMES
            },
        }
        if (
            snapshot_evidence.staging_identity
            != prepared_options_maps.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != snapshot_evidence.metadata.schema_fingerprint
            or closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
            or journal["completed_artifacts"] != expected_options_completed
        ):
            raise BootstrapStateError("owner close closure drifted")

        def require_final_absent() -> None:
            try:
                ops.stat(journal["final_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise BootstrapStateError("cannot inspect owner close final name") from exc
            raise BootstrapStateError("owner close final name exists too early")

        def guard(expect_owner: bool) -> tuple[
            int, int, int, int, int, int, int, int
        ]:
            expected_names = _closed_staging_inventory_names(
                (
                    SNAPSHOT_FILENAME,
                    *INITIALIZATION_DEPENDENCY_FILENAMES,
                    *((RUN_OWNER_FILENAME,) if expect_owner else ()),
                )
            )
            current, _journal_identity, current_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if (
                current != journal
                or current_digest != prepared_options_maps.journal_digest
            ):
                raise BootstrapStateError("owner close journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent()
            return _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                journal["staging_name"],
                staging,
                expected_device_inode=snapshot_evidence.staging_device_inode,
                expected_names=expected_names,
                ops=ops,
            )

        guard(owner_present)
        fresh_dependencies = dependency_rereader(
            staging_fd, closure, INITIALIZATION_DEPENDENCY_FILENAMES
        )
        fresh_dependencies = (
            _validated_initialization_dependency_prefix_evidence(
                closure,
                INITIALIZATION_DEPENDENCY_FILENAMES,
                fresh_dependencies,
            )
        )
        if fresh_dependencies != prepared_dependencies:
            raise BootstrapStateError("owner close dependency handoff drifted")
        closed_owner = _owner_from_initialization_dependency_evidence(
            closure, fresh_dependencies
        )
        if owner_present:
            initialization_evidence = closure_rereader(staging_fd, closure)
            initialization_evidence = _validated_initialization_closure_evidence(
                closure, initialization_evidence
            )
        else:
            guard(False)
            artifact_phase_started = True
            written_digest = owner_writer(
                staging_fd, closure, fresh_dependencies
            )
            if written_digest != closed_owner.digest:
                raise BootstrapStateError("run owner writer digest drifted")
            guard(True)
            initialization_evidence = closure_rereader(staging_fd, closure)
            initialization_evidence = _validated_initialization_closure_evidence(
                closure, initialization_evidence
            )
        completed_artifacts = {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{
                name: initialization_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        next_journal = bootstrap_journal_payload(
            state="owner_closed",
            previous_journal_digest=prepared_options_maps.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=journal["smoke_policy_digest"],
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=journal["universe_binding"],
            completed_artifacts=completed_artifacts,
        )
        current_staging_identity = guard(True)
        expected_tree = _validated_expected_private_tree(closure.expected_tree)
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if before_seal.root_identity != current_staging_identity:
            raise BootstrapStateError("owner close prepublish seal drifted")
        current_snapshot_evidence = ClosedSnapshotEvidence(
            metadata=snapshot_evidence.metadata,
            snapshot_identity=snapshot_evidence.snapshot_identity,
            staging_identity=before_seal.root_identity,
            snapshot_device_inode=snapshot_evidence.snapshot_device_inode,
            staging_device_inode=snapshot_evidence.staging_device_inode,
            inventory=_closed_staging_inventory_names(
                (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
            ),
        )
        _validated_closed_snapshot_evidence(
            current_snapshot_evidence,
            expected_staging_device_inode=snapshot_evidence.staging_device_inode,
            expected_inventory=current_snapshot_evidence.inventory,
        )
        _closed_snapshot_identity_from_seal(
            before_seal, current_snapshot_evidence
        )
        guard(True)
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("published owner journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_evidence = closure_rereader(staging_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{name: final_evidence[name][0] for name in INITIALIZATION_ARTIFACT_FILENAMES},
        } != next_journal["completed_artifacts"]:
            raise BootstrapStateError("owner close final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if final_seal.root_identity != current_snapshot_evidence.staging_identity:
            raise BootstrapStateError("owner close final seal drifted")
        _closed_snapshot_identity_from_seal(
            final_seal, current_snapshot_evidence
        )
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != next_journal or terminal_digest != published_digest:
            raise BootstrapStateError("owner close terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedOwnerClosed(
            journal=next_journal,
            journal_digest=published_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=current_snapshot_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        if journal_advanced or artifact_phase_started:
            raise BootstrapRecoveryRequired(
                "bootstrap owner close requires locked recovery"
            ) from exc
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot close bootstrap owner") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if journal_advanced or artifact_phase_started:
            raise BootstrapRecoveryRequired(
                "bootstrap owner close requires locked recovery"
            ) from exc
        raise BootstrapStateError("bootstrap owner close result is malformed") from exc
    finally:
        if not transferred:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    if journal_advanced or artifact_phase_started:
                        raise BootstrapRecoveryRequired(
                            "owner close staging close requires recovery"
                        ) from exc
                    raise BootstrapStateError("owner close staging close failed") from exc


def _resume_bootstrap_owner_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedOwnerClosed:
    """Reconstruct and verify one authoritative owner_closed state."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "owner_closed"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("owner resume authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError("cannot inspect owner resume final name") from exc
        raise BootstrapStateError("owner resume final name exists too early")

    require_final_absent()
    staging_fd: int | None = None
    try:
        staging_fd, staging_identity = _open_private_staging_at(
            parent_fd,
            journal["staging_name"],
            expected_names=inventory,
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError("owner resume staging identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity
            != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity
            != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("owner resume scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("owner resume reconstruction drifted")
        initialization_evidence = closure_rereader(staging_fd, closure)
        initialization_evidence = _validated_initialization_closure_evidence(
            closure, initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: scanned_evidence.metadata.file_sha256,
            **{
                name: initialization_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if journal["completed_artifacts"] != expected_completed:
            raise BootstrapStateError("owner resume artifacts drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                before_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("owner resume first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("owner resume journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_evidence = closure_rereader(staging_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != initialization_evidence:
            raise BootstrapStateError("owner resume final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(
                final_seal, scanned_evidence
            )
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("owner resume final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError("owner resume terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedOwnerClosed(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError("owner resume verification failed") from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError("owner resume result is malformed") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError("owner resume staging close failed") from exc


def _prepare_or_resume_bootstrap_owner_closed_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _options_integrator: Callable[..., PreparedOptionsMapsClosed] | None = None,
    _owner_stage_resumer: Callable[..., PreparedOptionsMapsClosed] | None = None,
    _closer: Callable[..., PreparedOwnerClosed] | None = None,
    _resumer: Callable[..., PreparedOwnerClosed] | None = None,
) -> PreparedOwnerClosed:
    """Reach one verified owner_closed boundary under one held lock."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    options_integrator = (
        _options_integrator
        or _prepare_or_resume_bootstrap_options_maps_closed_locked_at
    )
    owner_stage_resumer = (
        _owner_stage_resumer or _resume_bootstrap_options_maps_for_owner_locked_at
    )
    closer = _closer or _close_bootstrap_owner_locked_at
    resumer = _resumer or _resume_bootstrap_owner_closed_locked_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    if (
        reserved["state"] != "reserved"
        or bootstrap_staging_name(reserved["final_name"])
        != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
        or canonical_payload_digest(semantic)
        != reserved["semantic_options_digest"]
        or canonical_payload_digest(controls) != reserved["run_controls_digest"]
        or hmac_key_id(key) != reserved["hmac_key_id"]
    ):
        raise BootstrapStateError("owner integration authority drifted")
    immutable = {
        name: reserved[name]
        for name in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }
    full_inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(name) != value for name, value in immutable.items()):
            raise BootstrapStateError("owner integration binding drifted")

    def require_final_absent(journal: dict[str, Any]) -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect owner integration final name"
            ) from exc
        raise BootstrapStateError("owner integration final name exists too early")

    def close_owned_fd(
        descriptor: int,
        *,
        additional_descriptors: tuple[int, ...] = (),
        recovery_required: bool,
        message: str,
        cause: BaseException,
    ) -> None:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        close_error: BaseException | None = None
        closed: set[int] = set()
        for owned_descriptor in (descriptor, *additional_descriptors):
            if owned_descriptor in closed:
                continue
            closed.add(owned_descriptor)
            try:
                ops.close(owned_descriptor)
            except (KeyError, OSError) as close_exc:
                if close_error is None:
                    close_error = close_exc
        if close_error is not None:
            raise error_type(f"{message} close failed") from close_error
        if (
            not recovery_required
            and isinstance(cause, BootstrapStateError)
            and not isinstance(cause, BootstrapRecoveryRequired)
        ):
            raise cause
        raise error_type(message) from cause

    def validate_prepared_options(
        result: PreparedOptionsMapsClosed,
        *,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        recovery_required: bool,
    ) -> PreparedOptionsMapsClosed:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        if type(result) is not PreparedOptionsMapsClosed:
            raise error_type("owner prepared options/maps is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise error_type("owner prepared options/maps descriptor is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "options_maps_closed"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError("owner prepared options/maps drifted")
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError(
                        "owner prepared options/maps authority changed"
                    )
            elif authoritative_digest is not None:
                raise BootstrapStateError(
                    "owner prepared options/maps authority is malformed"
                )
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="owner prepared options/maps descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(
                    "owner prepared options/maps descriptor drifted"
                )
            inventory = _closed_staging_inventory_names(
                result.evidence.snapshot_evidence.inventory
            )
            _authorized_initialization_owner_stage_inventory(inventory)
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
                expected_inventory=inventory,
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            schema_info = result.evidence.schema_info
            universe = result.evidence.universe
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=schema_info,
                universe=universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            dependency_evidence = (
                _validated_initialization_dependency_prefix_evidence(
                    closure,
                    INITIALIZATION_DEPENDENCY_FILENAMES,
                    result.dependency_evidence,
                )
            )
            expected_completed = {
                SNAPSHOT_FILENAME: metadata.file_sha256,
                **{
                    name: dependency_evidence[name][0]
                    for name in INITIALIZATION_DEPENDENCY_FILENAMES
                },
            }
            if (
                snapshot_evidence.staging_identity != result.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(schema_info) is not AtomicSchemaInfo
                or schema_info.schema_fingerprint != metadata.schema_fingerprint
                or closure.artifact(SMOKE_POLICY_FILENAME).digest
                != journal["smoke_policy_digest"]
                or closure.universe_binding != journal["universe_binding"]
                or journal["completed_artifacts"] != expected_completed
            ):
                raise BootstrapStateError(
                    "owner prepared options/maps closure drifted"
                )
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError(
                    "owner prepared options/maps journal changed"
                )
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            close_owned_fd(
                result_fd,
                recovery_required=recovery_required,
                message="owner prepared options/maps is invalid",
                cause=exc,
            )

    def validate_owner_result(
        result: PreparedOwnerClosed,
        *,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        predecessor_journal: dict[str, Any] | None,
        expected_result_fd: int | None,
        recovery_required: bool,
    ) -> PreparedOwnerClosed:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        if type(result) is not PreparedOwnerClosed:
            raise error_type("owner integration result is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            raise error_type("owner integration descriptor is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "owner_closed"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError("owner integration result drifted")
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError("owner integration authority changed")
            elif authoritative_digest is not None:
                raise BootstrapStateError("owner integration authority is malformed")
            if predecessor_journal is not None:
                validate_bootstrap_transition(predecessor_journal, journal)
                if journal["previous_journal_digest"] != canonical_payload_digest(
                    predecessor_journal
                ):
                    raise BootstrapStateError("owner integration predecessor drifted")
            if expected_result_fd is not None and result_fd != expected_result_fd:
                raise BootstrapStateError(
                    "owner integration descriptor transfer drifted"
                )
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="owner integration descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(
                    "owner integration descriptor identity drifted"
                )
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
                expected_inventory=full_inventory,
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            schema_info = result.evidence.schema_info
            universe = result.evidence.universe
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=schema_info,
                universe=universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            initialization_evidence = _validated_initialization_closure_evidence(
                closure, result.initialization_evidence
            )
            expected_completed = {
                SNAPSHOT_FILENAME: metadata.file_sha256,
                **{
                    name: initialization_evidence[name][0]
                    for name in INITIALIZATION_ARTIFACT_FILENAMES
                },
            }
            if (
                snapshot_evidence.staging_identity != result.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(schema_info) is not AtomicSchemaInfo
                or schema_info.schema_fingerprint != metadata.schema_fingerprint
                or closure.artifact(SMOKE_POLICY_FILENAME).digest
                != journal["smoke_policy_digest"]
                or closure.universe_binding != journal["universe_binding"]
                or journal["completed_artifacts"] != expected_completed
            ):
                raise BootstrapStateError("owner integration closure drifted")
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError("owner integration journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            expected_tree = _validated_expected_private_tree(closure.expected_tree)
            final_seal = seal_private_tree_at(
                parent_fd,
                journal["staging_name"],
                expected_tree,
                _ops=ops,
            )
            if (
                final_seal.root_identity != result.staging_identity
                or _closed_snapshot_identity_from_seal(
                    final_seal, snapshot_evidence
                )
                != snapshot_evidence.snapshot_identity
            ):
                raise BootstrapStateError("owner integration final seal drifted")
            terminal, _journal_identity, terminal_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if terminal != journal or terminal_digest != result.journal_digest:
                raise BootstrapStateError(
                    "owner integration terminal journal changed"
                )
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            close_owned_fd(
                result_fd,
                additional_descriptors=(
                    (expected_result_fd,)
                    if expected_result_fd is not None
                    and expected_result_fd != result_fd
                    else ()
                ),
                recovery_required=recovery_required,
                message="owner integration result is invalid",
                cause=exc,
            )

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        ops.stat(journal_name, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
        current_digest = None
    except OSError as exc:
        raise BootstrapStateError("cannot classify owner journal") from exc
    else:
        current, _journal_identity, current_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        require_bindings(current)
    if current is not None and current["state"] == "owner_closed":
        return validate_owner_result(
            resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            expected_result_fd=None,
            recovery_required=False,
        )
    if current is not None and current["state"] not in {
        "reserved",
        "staging_created",
        "snapshot_in_progress",
        "snapshot_closed",
        "universe_closed",
        "options_maps_closed",
    }:
        raise BootstrapStateError("owner integration state is not resumable")
    if current is not None and current["state"] == "options_maps_closed":
        prepared_options = validate_prepared_options(
            owner_stage_resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=current,
            authoritative_digest=current_digest,
            recovery_required=False,
        )
    else:
        prepared_options = validate_prepared_options(
            options_integrator(
                parent_fd,
                journal_name,
                reserved,
                staging,
                Path(source_db).expanduser().absolute(),
                lock_fd=lock_fd,
                lock_name=lock_name,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=None,
            authoritative_digest=None,
            recovery_required=True,
        )
    predecessor_journal = prepared_options.journal
    expected_result_fd = prepared_options.staging_fd
    owner_result = closer(
        parent_fd,
        journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
        prepared_options_maps=prepared_options,
        staging_path=staging,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    return validate_owner_result(
        owner_result,
        authoritative_journal=None,
        authoritative_digest=None,
        predecessor_journal=predecessor_journal,
        expected_result_fd=expected_result_fd,
        recovery_required=True,
    )


def _close_bootstrap_ready_to_promote_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    prepared_owner: PreparedOwnerClosed,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedReadyToPromote:
    """Consume owner_closed and durably authorize one exact promotion tree."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(prepared_owner) is not PreparedOwnerClosed:
        raise BootstrapStateError("promotion close prepared owner is invalid")
    staging_fd = prepared_owner.staging_fd
    if type(staging_fd) is not int:
        raise BootstrapStateError("promotion close staging descriptor is invalid")
    staging = Path(staging_path).expanduser().absolute()
    journal_advanced = False
    transferred = False
    try:
        journal = _validated_bootstrap_journal_payload(prepared_owner.journal)
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            journal["state"] != "owner_closed"
            or prepared_owner.journal_digest != canonical_payload_digest(journal)
            or staging.name != journal["staging_name"]
            or bootstrap_staging_name(journal["final_name"])
            != journal["staging_name"]
            or bootstrap_journal_name(journal["final_name"]) != journal_name
            or canonical_payload_digest(semantic)
            != journal["semantic_options_digest"]
            or canonical_payload_digest(controls)
            != journal["run_controls_digest"]
            or hmac_key_id(key) != journal["hmac_key_id"]
            or type(prepared_owner.evidence) is not UniverseClosedEvidence
            or type(prepared_owner.staging_identity) is not tuple
            or len(prepared_owner.staging_identity) != 8
            or any(type(value) is not int for value in prepared_owner.staging_identity)
        ):
            raise BootstrapStateError("promotion close authority drifted")
        descriptor_info = ops.fstat(staging_fd)
        _validate_private_tree_inode(
            descriptor_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="promotion close staging descriptor",
        )
        if _private_node_identity(descriptor_info) != prepared_owner.staging_identity:
            raise BootstrapStateError("promotion close descriptor drifted")
        inventory = _closed_staging_inventory_names(
            (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
        )
        snapshot_evidence = _validated_closed_snapshot_evidence(
            prepared_owner.evidence.snapshot_evidence,
            expected_staging_device_inode=prepared_owner.staging_identity[:2],
            expected_inventory=inventory,
        )
        schema_info = prepared_owner.evidence.schema_info
        universe = prepared_owner.evidence.universe
        closure = _validated_reconstructed_initialization_closure(
            prepared_owner.evidence.initialization,
            snapshot_metadata=snapshot_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        prepared_evidence = _validated_initialization_closure_evidence(
            closure, prepared_owner.initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{
                name: prepared_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if (
            snapshot_evidence.staging_identity != prepared_owner.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != snapshot_evidence.metadata.schema_fingerprint
            or closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
            or journal["completed_artifacts"] != expected_completed
        ):
            raise BootstrapStateError("promotion close closure drifted")

        def require_final_absent() -> None:
            try:
                ops.stat(journal["final_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise BootstrapStateError(
                    "cannot inspect promotion close final name"
                ) from exc
            raise BootstrapStateError("promotion close final name exists too early")

        def guard(
            expected_journal: dict[str, Any], expected_digest: str
        ) -> tuple[int, int, int, int, int, int, int, int]:
            current, _journal_identity, current_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if current != expected_journal or current_digest != expected_digest:
                raise BootstrapStateError("promotion close journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent()
            return _verify_pinned_staging_binding_at(
                parent_fd,
                staging_fd,
                journal["staging_name"],
                staging,
                expected_device_inode=prepared_owner.staging_identity[:2],
                expected_names=inventory,
                ops=ops,
            )

        current_staging_identity = guard(
            journal, prepared_owner.journal_digest
        )
        if current_staging_identity != prepared_owner.staging_identity:
            raise BootstrapStateError("promotion close staging identity drifted")
        fresh_evidence = closure_rereader(staging_fd, closure)
        fresh_evidence = _validated_initialization_closure_evidence(
            closure, fresh_evidence
        )
        if fresh_evidence != prepared_evidence:
            raise BootstrapStateError("promotion close evidence handoff drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != prepared_owner.staging_identity
            or _closed_snapshot_identity_from_seal(before_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion close prepublish seal drifted")
        guard(journal, prepared_owner.journal_digest)
        next_journal = bootstrap_journal_payload(
            state="ready_to_promote",
            previous_journal_digest=prepared_owner.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=journal["smoke_policy_digest"],
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=journal["universe_binding"],
            completed_artifacts=journal["completed_artifacts"],
        )
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("published promotion journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_evidence = closure_rereader(staging_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != fresh_evidence:
            raise BootstrapStateError("promotion close final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != prepared_owner.staging_identity
            or _closed_snapshot_identity_from_seal(final_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion close final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != next_journal or terminal_digest != published_digest:
            raise BootstrapStateError("promotion close terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedReadyToPromote(
            journal=next_journal,
            journal_digest=published_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=snapshot_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        if journal_advanced:
            raise BootstrapRecoveryRequired(
                "bootstrap promotion close requires locked recovery"
            ) from exc
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot close bootstrap promotion") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if journal_advanced:
            raise BootstrapRecoveryRequired(
                "bootstrap promotion close requires locked recovery"
            ) from exc
        raise BootstrapStateError(
            "bootstrap promotion close result is malformed"
        ) from exc
    finally:
        if not transferred:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    if journal_advanced:
                        raise BootstrapRecoveryRequired(
                            "promotion close staging close requires recovery"
                        ) from exc
                    raise BootstrapStateError(
                        "promotion close staging close failed"
                    ) from exc


def _resume_bootstrap_ready_to_promote_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedReadyToPromote:
    """Reconstruct and verify one authoritative ready_to_promote state."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != "ready_to_promote"
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapStateError("promotion resume authority drifted")

    def require_final_absent() -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect promotion resume final name"
            ) from exc
        raise BootstrapStateError("promotion resume final name exists too early")

    require_final_absent()
    staging_fd: int | None = None
    try:
        staging_fd, staging_identity = _open_private_staging_at(
            parent_fd,
            journal["staging_name"],
            expected_names=inventory,
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            metadata,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != staging_identity:
            raise BootstrapStateError("promotion resume staging identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            staging_fd,
            journal["staging_name"],
            staging,
            prior_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=staging_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("promotion resume scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("promotion resume reconstruction drifted")
        initialization_evidence = closure_rereader(staging_fd, closure)
        initialization_evidence = _validated_initialization_closure_evidence(
            closure, initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: scanned_evidence.metadata.file_sha256,
            **{
                name: initialization_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if journal["completed_artifacts"] != expected_completed:
            raise BootstrapStateError("promotion resume artifacts drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(before_seal, scanned_evidence)
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion resume first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("promotion resume journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        final_evidence = closure_rereader(staging_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != initialization_evidence:
            raise BootstrapStateError("promotion resume final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(final_seal, scanned_evidence)
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion resume final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError("promotion resume terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_absent()
        result = PreparedReadyToPromote(
            journal=journal,
            journal_digest=journal_digest,
            staging_fd=staging_fd,
            staging_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        staging_fd = None
        return result
    except BootstrapRecoveryRequired as exc:
        raise BootstrapStateError("promotion resume verification failed") from exc
    except AtomicAcquisitionError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapStateError("promotion resume result is malformed") from exc
    finally:
        if staging_fd is not None:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(staging_fd)
            except OSError as exc:
                if not active_exception:
                    raise BootstrapStateError(
                        "promotion resume staging close failed"
                    ) from exc


def _prepare_or_resume_bootstrap_ready_to_promote_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _owner_integrator: Callable[..., PreparedOwnerClosed] | None = None,
    _owner_resumer: Callable[..., PreparedOwnerClosed] | None = None,
    _closer: Callable[..., PreparedReadyToPromote] | None = None,
    _resumer: Callable[..., PreparedReadyToPromote] | None = None,
) -> PreparedReadyToPromote:
    """Reach one independently verified ready_to_promote boundary."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    owner_integrator = (
        _owner_integrator or _prepare_or_resume_bootstrap_owner_closed_locked_at
    )
    owner_resumer = _owner_resumer or _resume_bootstrap_owner_closed_locked_at
    closer = _closer or _close_bootstrap_ready_to_promote_locked_at
    resumer = _resumer or _resume_bootstrap_ready_to_promote_locked_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    if (
        reserved["state"] != "reserved"
        or bootstrap_staging_name(reserved["final_name"])
        != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
        or canonical_payload_digest(semantic)
        != reserved["semantic_options_digest"]
        or canonical_payload_digest(controls) != reserved["run_controls_digest"]
        or hmac_key_id(key) != reserved["hmac_key_id"]
    ):
        raise BootstrapStateError("promotion integration authority drifted")
    immutable = {
        name: reserved[name]
        for name in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }
    full_inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(name) != value for name, value in immutable.items()):
            raise BootstrapStateError("promotion integration binding drifted")

    def require_final_absent(journal: dict[str, Any]) -> None:
        try:
            ops.stat(journal["final_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise BootstrapStateError(
                "cannot inspect promotion integration final name"
            ) from exc
        raise BootstrapStateError(
            "promotion integration final name exists too early"
        )

    def close_invalid_result(
        descriptor: int,
        *,
        additional_descriptors: tuple[int, ...] = (),
        recovery_required: bool,
        message: str,
        cause: BaseException,
    ) -> None:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        close_error: BaseException | None = None
        closed: set[int] = set()
        for owned_descriptor in (descriptor, *additional_descriptors):
            if owned_descriptor in closed:
                continue
            closed.add(owned_descriptor)
            try:
                ops.close(owned_descriptor)
            except (KeyError, OSError) as close_exc:
                if close_error is None:
                    close_error = close_exc
        if close_error is not None:
            raise error_type(f"{message} close failed") from close_error
        if (
            not recovery_required
            and isinstance(cause, BootstrapStateError)
            and not isinstance(cause, BootstrapRecoveryRequired)
        ):
            raise cause
        raise error_type(message) from cause

    def validate_full_result(
        result: PreparedOwnerClosed | PreparedReadyToPromote,
        *,
        expected_type: type[PreparedOwnerClosed] | type[PreparedReadyToPromote],
        expected_state: str,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        predecessor_journal: dict[str, Any] | None,
        expected_result_fd: int | None,
        recovery_required: bool,
        label: str,
    ) -> PreparedOwnerClosed | PreparedReadyToPromote:
        error_type = (
            BootstrapRecoveryRequired
            if recovery_required
            else BootstrapStateError
        )
        if type(result) is not expected_type:
            if expected_result_fd is not None:
                close_invalid_result(
                    expected_result_fd,
                    recovery_required=recovery_required,
                    message=f"{label} result is invalid",
                    cause=BootstrapStateError(f"{label} result is invalid"),
                )
            raise error_type(f"{label} result is invalid")
        result_fd = result.staging_fd
        if type(result_fd) is not int:
            if expected_result_fd is not None:
                close_invalid_result(
                    expected_result_fd,
                    recovery_required=recovery_required,
                    message=f"{label} descriptor is invalid",
                    cause=BootstrapStateError(f"{label} descriptor is invalid"),
                )
            raise error_type(f"{label} descriptor is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != expected_state
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.evidence) is not UniverseClosedEvidence
                or type(result.staging_identity) is not tuple
                or len(result.staging_identity) != 8
                or any(type(value) is not int for value in result.staging_identity)
            ):
                raise BootstrapStateError(f"{label} result drifted")
            if authoritative_journal is not None:
                if (
                    journal != authoritative_journal
                    or result.journal_digest != authoritative_digest
                ):
                    raise BootstrapStateError(f"{label} authority changed")
            elif authoritative_digest is not None:
                raise BootstrapStateError(f"{label} authority is malformed")
            if predecessor_journal is not None:
                validate_bootstrap_transition(predecessor_journal, journal)
                if journal["previous_journal_digest"] != canonical_payload_digest(
                    predecessor_journal
                ):
                    raise BootstrapStateError(f"{label} predecessor drifted")
            if expected_result_fd is not None and result_fd != expected_result_fd:
                raise BootstrapStateError(f"{label} descriptor transfer drifted")
            descriptor_info = ops.fstat(result_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label=f"{label} descriptor",
            )
            if _private_node_identity(descriptor_info) != result.staging_identity:
                raise BootstrapStateError(f"{label} descriptor identity drifted")
            snapshot_evidence = _validated_closed_snapshot_evidence(
                result.evidence.snapshot_evidence,
                expected_staging_device_inode=result.staging_identity[:2],
                expected_inventory=full_inventory,
            )
            metadata = SnapshotMetadata(**journal["snapshot_metadata"])
            schema_info = result.evidence.schema_info
            universe = result.evidence.universe
            closure = _validated_reconstructed_initialization_closure(
                result.evidence.initialization,
                snapshot_metadata=snapshot_evidence.metadata,
                schema_info=schema_info,
                universe=universe,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
            )
            initialization_evidence = _validated_initialization_closure_evidence(
                closure, result.initialization_evidence
            )
            expected_completed = {
                SNAPSHOT_FILENAME: metadata.file_sha256,
                **{
                    name: initialization_evidence[name][0]
                    for name in INITIALIZATION_ARTIFACT_FILENAMES
                },
            }
            if (
                snapshot_evidence.staging_identity != result.staging_identity
                or snapshot_evidence.metadata != metadata
                or type(schema_info) is not AtomicSchemaInfo
                or schema_info.schema_fingerprint != metadata.schema_fingerprint
                or closure.artifact(SMOKE_POLICY_FILENAME).digest
                != journal["smoke_policy_digest"]
                or closure.universe_binding != journal["universe_binding"]
                or journal["completed_artifacts"] != expected_completed
            ):
                raise BootstrapStateError(f"{label} closure drifted")
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError(f"{label} journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            expected_tree = _validated_expected_private_tree(closure.expected_tree)
            final_seal = seal_private_tree_at(
                parent_fd,
                journal["staging_name"],
                expected_tree,
                _ops=ops,
            )
            if (
                final_seal.root_identity != result.staging_identity
                or _closed_snapshot_identity_from_seal(
                    final_seal, snapshot_evidence
                )
                != snapshot_evidence.snapshot_identity
            ):
                raise BootstrapStateError(f"{label} final seal drifted")
            terminal, _journal_identity, terminal_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if terminal != journal or terminal_digest != result.journal_digest:
                raise BootstrapStateError(f"{label} terminal journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            require_final_absent(journal)
            return result
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            close_invalid_result(
                result_fd,
                additional_descriptors=(
                    (expected_result_fd,)
                    if expected_result_fd is not None
                    and expected_result_fd != result_fd
                    else ()
                ),
                recovery_required=recovery_required,
                message=f"{label} result is invalid",
                cause=exc,
            )

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    try:
        ops.stat(journal_name, dir_fd=parent_fd)
    except FileNotFoundError:
        current = None
        current_digest = None
    except OSError as exc:
        raise BootstrapStateError("cannot classify promotion journal") from exc
    else:
        current, _journal_identity, current_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        require_bindings(current)
    if current is not None and current["state"] == "ready_to_promote":
        validated = validate_full_result(
            resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            expected_type=PreparedReadyToPromote,
            expected_state="ready_to_promote",
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            expected_result_fd=None,
            recovery_required=False,
            label="promotion direct resume",
        )
        assert type(validated) is PreparedReadyToPromote
        return validated
    if current is not None and current["state"] == "promoted":
        raise BootstrapStateError("promotion integration state is not resumable")
    if current is not None and current["state"] == "owner_closed":
        prepared_owner = validate_full_result(
            owner_resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            expected_type=PreparedOwnerClosed,
            expected_state="owner_closed",
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            expected_result_fd=None,
            recovery_required=False,
            label="promotion owner resume",
        )
    else:
        prepared_owner = validate_full_result(
            owner_integrator(
                parent_fd,
                journal_name,
                reserved,
                staging,
                Path(source_db).expanduser().absolute(),
                lock_fd=lock_fd,
                lock_name=lock_name,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            expected_type=PreparedOwnerClosed,
            expected_state="owner_closed",
            authoritative_journal=None,
            authoritative_digest=None,
            predecessor_journal=None,
            expected_result_fd=None,
            recovery_required=True,
            label="promotion owner integration",
        )
    assert type(prepared_owner) is PreparedOwnerClosed
    predecessor_journal = prepared_owner.journal
    expected_result_fd = prepared_owner.staging_fd
    ready_result = closer(
        parent_fd,
        journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
        prepared_owner=prepared_owner,
        staging_path=staging,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    validated = validate_full_result(
        ready_result,
        expected_type=PreparedReadyToPromote,
        expected_state="ready_to_promote",
        authoritative_journal=None,
        authoritative_digest=None,
        predecessor_journal=predecessor_journal,
        expected_result_fd=expected_result_fd,
        recovery_required=True,
        label="promotion closer",
    )
    assert type(validated) is PreparedReadyToPromote
    return validated


def _promote_bootstrap_ready_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    prepared_ready: PreparedReadyToPromote,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedPromoted:
    """Exclusively rename one ready tree and publish the promoted journal."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(prepared_ready) is not PreparedReadyToPromote:
        raise BootstrapStateError("promotion prepared result is invalid")
    tree_fd = prepared_ready.staging_fd
    if type(tree_fd) is not int:
        raise BootstrapStateError("promotion staging descriptor is invalid")
    staging = Path(staging_path).expanduser().absolute()
    rename_started = False
    journal_advanced = False
    transferred = False
    try:
        journal = _validated_bootstrap_journal_payload(prepared_ready.journal)
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            journal["state"] != "ready_to_promote"
            or prepared_ready.journal_digest != canonical_payload_digest(journal)
            or staging.name != journal["staging_name"]
            or bootstrap_staging_name(journal["final_name"])
            != journal["staging_name"]
            or bootstrap_journal_name(journal["final_name"]) != journal_name
            or canonical_payload_digest(semantic)
            != journal["semantic_options_digest"]
            or canonical_payload_digest(controls)
            != journal["run_controls_digest"]
            or hmac_key_id(key) != journal["hmac_key_id"]
            or type(prepared_ready.evidence) is not UniverseClosedEvidence
            or type(prepared_ready.staging_identity) is not tuple
            or len(prepared_ready.staging_identity) != 8
            or any(type(value) is not int for value in prepared_ready.staging_identity)
        ):
            raise BootstrapStateError("promotion authority drifted")
        descriptor_info = ops.fstat(tree_fd)
        _validate_private_tree_inode(
            descriptor_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="promotion tree descriptor",
        )
        if _private_node_identity(descriptor_info) != prepared_ready.staging_identity:
            raise BootstrapStateError("promotion descriptor drifted")
        inventory = _closed_staging_inventory_names(
            (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
        )
        snapshot_evidence = _validated_closed_snapshot_evidence(
            prepared_ready.evidence.snapshot_evidence,
            expected_staging_device_inode=prepared_ready.staging_identity[:2],
            expected_inventory=inventory,
        )
        schema_info = prepared_ready.evidence.schema_info
        universe = prepared_ready.evidence.universe
        closure = _validated_reconstructed_initialization_closure(
            prepared_ready.evidence.initialization,
            snapshot_metadata=snapshot_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        prepared_evidence = _validated_initialization_closure_evidence(
            closure, prepared_ready.initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: snapshot_evidence.metadata.file_sha256,
            **{
                name: prepared_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if (
            snapshot_evidence.staging_identity != prepared_ready.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != snapshot_evidence.metadata.schema_fingerprint
            or closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
            or journal["completed_artifacts"] != expected_completed
        ):
            raise BootstrapStateError("promotion closure drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)

        def name_exists(name: str) -> bool:
            try:
                ops.stat(name, dir_fd=parent_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise BootstrapStateError("cannot inspect promotion name") from exc
            return True

        def require_ready_names() -> tuple[int, int, int, int, int, int, int, int]:
            if name_exists(journal["final_name"]):
                raise BootstrapStateError("promotion final name exists too early")
            return _verify_pinned_staging_binding_at(
                parent_fd,
                tree_fd,
                journal["staging_name"],
                staging,
                expected_device_inode=prepared_ready.staging_identity[:2],
                expected_names=inventory,
                ops=ops,
            )

        def require_renamed_names() -> tuple[int, int, int, int, int, int, int, int]:
            if name_exists(journal["staging_name"]):
                raise BootstrapStateError("promotion staging name survived rename")
            parent_info = ops.fstat(parent_fd)
            parent_path_info = ops.stat_path(staging.parent)
            for info in (parent_info, parent_path_info):
                _validate_private_tree_inode(
                    info,
                    kind="directory",
                    owner_uid=ops.getuid(),
                    label="promotion parent directory",
                )
            if _device_inode(parent_info) != _device_inode(parent_path_info):
                raise BootstrapStateError("promotion parent pathname drifted")
            inventory_identity = _stable_private_directory_inventory(
                tree_fd,
                inventory,
                owner_uid=ops.getuid(),
                ops=ops,
                label="promoted private directory",
            )
            final_info = ops.stat(journal["final_name"], dir_fd=parent_fd)
            _validate_private_tree_inode(
                final_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="promoted private directory",
            )
            if (
                inventory_identity != prepared_ready.staging_identity
                or _private_node_identity(final_info) != prepared_ready.staging_identity
            ):
                raise BootstrapStateError("promoted private tree binding drifted")
            return inventory_identity

        def require_journal(
            expected_journal: dict[str, Any], expected_digest: str
        ) -> None:
            current, _journal_identity, current_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if current != expected_journal or current_digest != expected_digest:
                raise BootstrapStateError("promotion journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )

        require_journal(journal, prepared_ready.journal_digest)
        if require_ready_names() != prepared_ready.staging_identity:
            raise BootstrapStateError("promotion staging identity drifted")
        fresh_evidence = closure_rereader(tree_fd, closure)
        fresh_evidence = _validated_initialization_closure_evidence(
            closure, fresh_evidence
        )
        if fresh_evidence != prepared_evidence:
            raise BootstrapStateError("promotion evidence handoff drifted")
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["staging_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != prepared_ready.staging_identity
            or _closed_snapshot_identity_from_seal(before_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion pre-rename seal drifted")
        require_journal(journal, prepared_ready.journal_digest)
        require_ready_names()
        ops.rename_exclusive(
            journal["staging_name"], journal["final_name"], dir_fd=parent_fd
        )
        rename_started = True
        require_renamed_names()
        require_journal(journal, prepared_ready.journal_digest)
        ops.fsync(parent_fd)
        require_renamed_names()
        renamed_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            renamed_seal.root_identity != prepared_ready.staging_identity
            or _closed_snapshot_identity_from_seal(renamed_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion renamed seal drifted")
        require_journal(journal, prepared_ready.journal_digest)
        require_renamed_names()
        next_journal = bootstrap_journal_payload(
            state="promoted",
            previous_journal_digest=prepared_ready.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=journal["smoke_policy_digest"],
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=journal["universe_binding"],
            completed_artifacts=journal["completed_artifacts"],
        )
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("published promoted journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_renamed_names()
        final_evidence = closure_rereader(tree_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != fresh_evidence:
            raise BootstrapStateError("promotion final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != prepared_ready.staging_identity
            or _closed_snapshot_identity_from_seal(final_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("promotion final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != next_journal or terminal_digest != published_digest:
            raise BootstrapStateError("promotion terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_renamed_names()
        result = PreparedPromoted(
            journal=next_journal,
            journal_digest=published_digest,
            final_fd=tree_fd,
            final_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=replace(
                    snapshot_evidence,
                    staging_identity=final_seal.root_identity,
                ),
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        if rename_started or journal_advanced:
            raise BootstrapRecoveryRequired(
                "bootstrap promotion requires locked recovery"
            ) from exc
        if isinstance(exc, AtomicAcquisitionError):
            raise
        raise BootstrapStateError("cannot promote bootstrap tree") from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        if rename_started or journal_advanced:
            raise BootstrapRecoveryRequired(
                "bootstrap promotion requires locked recovery"
            ) from exc
        raise BootstrapStateError("bootstrap promotion result is malformed") from exc
    finally:
        if not transferred:
            active_exception = sys.exc_info()[0] is not None
            try:
                ops.close(tree_fd)
            except OSError as exc:
                if not active_exception:
                    if rename_started or journal_advanced:
                        raise BootstrapRecoveryRequired(
                            "promotion tree close requires recovery"
                        ) from exc
                    raise BootstrapStateError("promotion tree close failed") from exc


def _verify_bootstrap_final_tree_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    expected_state: str,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> VerifiedBootstrapFinalTree:
    """Independently reconstruct one journal-authorized final-only tree."""

    if expected_state not in {"ready_to_promote", "promoted"}:
        raise BootstrapStateError("final-tree verification state is invalid")
    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    snapshot_verifier = _snapshot_verifier or _verify_existing_closed_snapshot_at
    scanner = _scanner or _discover_closed_snapshot_universe_at
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    inventory = _closed_staging_inventory_names(
        (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
    )
    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal, _journal_identity, journal_digest = _read_bootstrap_journal_at(
        parent_fd, journal_name
    )
    if (
        journal["state"] != expected_state
        or staging.name != journal["staging_name"]
        or bootstrap_staging_name(journal["final_name"])
        != journal["staging_name"]
        or bootstrap_journal_name(journal["final_name"]) != journal_name
        or canonical_payload_digest(semantic)
        != journal["semantic_options_digest"]
        or canonical_payload_digest(controls) != journal["run_controls_digest"]
        or hmac_key_id(key) != journal["hmac_key_id"]
    ):
        raise BootstrapRecoveryRequired("final-tree authority requires recovery")
    final_path = staging.with_name(journal["final_name"])

    def require_final_only() -> None:
        try:
            ops.stat(journal["staging_name"], dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BootstrapStateError("cannot inspect recovered staging name") from exc
        else:
            raise BootstrapStateError("recovered staging name still exists")
        try:
            final_info = ops.stat(journal["final_name"], dir_fd=parent_fd)
        except OSError as exc:
            raise BootstrapStateError("recovered final name is absent") from exc
        _validate_private_tree_inode(
            final_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="recovered final directory",
        )

    final_fd: int | None = None
    try:
        require_final_only()
        final_fd, final_identity = _open_private_staging_at(
            parent_fd,
            journal["final_name"],
            expected_names=inventory,
            _ops=ops,
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        prior_evidence = snapshot_verifier(
            parent_fd,
            final_fd,
            journal["final_name"],
            final_path,
            metadata,
            expected_staging_device_inode=final_identity[:2],
            expected_staging_names=inventory,
            _ops=ops,
        )
        prior_evidence = _validated_closed_snapshot_evidence(
            prior_evidence,
            expected_staging_device_inode=final_identity[:2],
            expected_inventory=inventory,
        )
        if prior_evidence.staging_identity != final_identity:
            raise BootstrapStateError("recovered final identity drifted")
        scanned_evidence, schema_info, universe = scanner(
            parent_fd,
            final_fd,
            journal["final_name"],
            final_path,
            prior_evidence,
            expected_staging_device_inode=final_identity[:2],
            expected_staging_names=inventory,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        scanned_evidence = _validated_closed_snapshot_evidence(
            scanned_evidence,
            expected_staging_device_inode=final_identity[:2],
            expected_inventory=inventory,
        )
        if (
            scanned_evidence.snapshot_identity != prior_evidence.snapshot_identity
            or scanned_evidence.staging_identity != prior_evidence.staging_identity
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint
            != scanned_evidence.metadata.schema_fingerprint
        ):
            raise BootstrapStateError("recovered final scan binding drifted")
        _validated_candidate_membership(universe)
        closure = build_initialization_closure(
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        closure = _validated_reconstructed_initialization_closure(
            closure,
            snapshot_metadata=scanned_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        if (
            closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
        ):
            raise BootstrapStateError("recovered final reconstruction drifted")
        initialization_evidence = closure_rereader(final_fd, closure)
        initialization_evidence = _validated_initialization_closure_evidence(
            closure, initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: scanned_evidence.metadata.file_sha256,
            **{
                name: initialization_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if journal["completed_artifacts"] != expected_completed:
            raise BootstrapStateError("recovered final artifacts drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(before_seal, scanned_evidence)
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("recovered final first seal drifted")
        reread, _journal_identity, reread_digest = _read_bootstrap_journal_at(
            parent_fd, journal_name
        )
        if reread != journal or reread_digest != journal_digest:
            raise BootstrapStateError("recovered final journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_only()
        final_evidence = closure_rereader(final_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != initialization_evidence:
            raise BootstrapStateError("recovered final evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != scanned_evidence.staging_identity
            or _closed_snapshot_identity_from_seal(final_seal, scanned_evidence)
            != scanned_evidence.snapshot_identity
        ):
            raise BootstrapStateError("recovered final seal drifted")
        terminal, _journal_identity, terminal_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if terminal != journal or terminal_digest != journal_digest:
            raise BootstrapStateError("recovered final terminal journal changed")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        require_final_only()
        result = VerifiedBootstrapFinalTree(
            journal=journal,
            journal_digest=journal_digest,
            final_fd=final_fd,
            final_identity=final_seal.root_identity,
            evidence=UniverseClosedEvidence(
                snapshot_evidence=scanned_evidence,
                schema_info=schema_info,
                universe=universe,
                initialization=closure,
            ),
            initialization_evidence=final_evidence,
        )
        final_fd = None
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error) as exc:
        raise BootstrapRecoveryRequired(
            "final-tree verification requires locked recovery"
        ) from exc
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BootstrapRecoveryRequired(
            "final-tree verification result requires locked recovery"
        ) from exc
    finally:
        if final_fd is not None:
            try:
                ops.close(final_fd)
            except OSError:
                pass


def _resume_bootstrap_promoted_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    staging_path: Path,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _snapshot_verifier: Callable[..., ClosedSnapshotEvidence] | None = None,
    _scanner: Callable[..., tuple[
        ClosedSnapshotEvidence,
        AtomicSchemaInfo,
        AtomicCandidateUniverse,
    ]]
    | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedPromoted:
    """Verify one promoted journal residue without retiring it."""

    verified = _verify_bootstrap_final_tree_locked_at(
        parent_fd,
        journal_name,
        expected_state="promoted",
        lock_fd=lock_fd,
        lock_name=lock_name,
        staging_path=staging_path,
        key_bytes=key_bytes,
        semantic_options=semantic_options,
        run_controls=run_controls,
        _ops=_ops,
        _snapshot_verifier=_snapshot_verifier,
        _scanner=_scanner,
        _closure_rereader=_closure_rereader,
    )
    return PreparedPromoted(
        journal=verified.journal,
        journal_digest=verified.journal_digest,
        final_fd=verified.final_fd,
        final_identity=verified.final_identity,
        evidence=verified.evidence,
        initialization_evidence=verified.initialization_evidence,
    )


def _recover_bootstrap_renamed_ready_locked_at(
    parent_fd: int,
    journal_name: str,
    *,
    lock_fd: int,
    lock_name: str,
    verified_final: VerifiedBootstrapFinalTree,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _closure_rereader: Callable[..., dict[str, tuple[str, bytes]]] | None = None,
) -> PreparedPromoted:
    """Publish promoted for one independently verified ready-plus-final residue."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    closure_rereader = _closure_rereader or _reread_initialization_closure_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    if type(verified_final) is not VerifiedBootstrapFinalTree:
        raise BootstrapRecoveryRequired("renamed-ready evidence is invalid")
    final_fd = verified_final.final_fd
    if type(final_fd) is not int:
        raise BootstrapRecoveryRequired("renamed-ready descriptor is invalid")
    transferred = False
    journal_advanced = False
    try:
        journal = _validated_bootstrap_journal_payload(verified_final.journal)
        semantic = _validated_semantic_options(semantic_options)
        controls = _validated_run_controls(run_controls)
        key = _validate_hmac_key(key_bytes)
        if (
            journal["state"] != "ready_to_promote"
            or verified_final.journal_digest != canonical_payload_digest(journal)
            or type(verified_final.evidence) is not UniverseClosedEvidence
            or type(verified_final.final_identity) is not tuple
            or len(verified_final.final_identity) != 8
        ):
            raise BootstrapStateError("renamed-ready authority drifted")
        descriptor_info = ops.fstat(final_fd)
        _validate_private_tree_inode(
            descriptor_info,
            kind="directory",
            owner_uid=ops.getuid(),
            label="renamed-ready final descriptor",
        )
        if _private_node_identity(descriptor_info) != verified_final.final_identity:
            raise BootstrapStateError("renamed-ready descriptor drifted")
        snapshot_evidence = _validated_closed_snapshot_evidence(
            verified_final.evidence.snapshot_evidence,
            expected_staging_device_inode=verified_final.final_identity[:2],
            expected_inventory=_closed_staging_inventory_names(
                (SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES)
            ),
        )
        metadata = SnapshotMetadata(**journal["snapshot_metadata"])
        schema_info = verified_final.evidence.schema_info
        universe = verified_final.evidence.universe
        closure = _validated_reconstructed_initialization_closure(
            verified_final.evidence.initialization,
            snapshot_metadata=snapshot_evidence.metadata,
            schema_info=schema_info,
            universe=universe,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
        )
        initialization_evidence = _validated_initialization_closure_evidence(
            closure, verified_final.initialization_evidence
        )
        expected_completed = {
            SNAPSHOT_FILENAME: metadata.file_sha256,
            **{
                name: initialization_evidence[name][0]
                for name in INITIALIZATION_ARTIFACT_FILENAMES
            },
        }
        if (
            snapshot_evidence.staging_identity != verified_final.final_identity
            or snapshot_evidence.metadata != metadata
            or type(schema_info) is not AtomicSchemaInfo
            or schema_info.schema_fingerprint != metadata.schema_fingerprint
            or closure.artifact(SMOKE_POLICY_FILENAME).digest
            != journal["smoke_policy_digest"]
            or closure.universe_binding != journal["universe_binding"]
            or journal["completed_artifacts"] != expected_completed
        ):
            raise BootstrapStateError("renamed-ready closure drifted")
        expected_tree = _validated_expected_private_tree(closure.expected_tree)

        def require_final_only(expected_journal: dict[str, Any], digest: str) -> None:
            current, _journal_identity, current_digest = (
                _read_bootstrap_journal_at(parent_fd, journal_name)
            )
            if current != expected_journal or current_digest != digest:
                raise BootstrapStateError("renamed-ready journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            try:
                ops.stat(journal["staging_name"], dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapStateError("renamed-ready staging reappeared")
            final_info = ops.stat(journal["final_name"], dir_fd=parent_fd)
            if _private_node_identity(final_info) != verified_final.final_identity:
                raise BootstrapStateError("renamed-ready final binding drifted")

        require_final_only(journal, verified_final.journal_digest)
        fresh_evidence = closure_rereader(final_fd, closure)
        fresh_evidence = _validated_initialization_closure_evidence(
            closure, fresh_evidence
        )
        if fresh_evidence != initialization_evidence:
            raise BootstrapStateError("renamed-ready evidence drifted")
        before_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            before_seal.root_identity != verified_final.final_identity
            or _closed_snapshot_identity_from_seal(before_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("renamed-ready seal drifted")
        require_final_only(journal, verified_final.journal_digest)
        next_journal = bootstrap_journal_payload(
            state="promoted",
            previous_journal_digest=verified_final.journal_digest,
            staging_name=journal["staging_name"],
            final_name=journal["final_name"],
            semantic_options_digest=journal["semantic_options_digest"],
            run_controls_digest=journal["run_controls_digest"],
            smoke_policy_digest=journal["smoke_policy_digest"],
            hmac_key_id_value=journal["hmac_key_id"],
            snapshot_metadata=journal["snapshot_metadata"],
            universe_binding=journal["universe_binding"],
            completed_artifacts=journal["completed_artifacts"],
        )
        published_digest = _advance_bootstrap_journal_locked_at(
            parent_fd,
            journal_name,
            next_journal,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        journal_advanced = True
        published, _journal_identity, confirmed_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        if published != next_journal or confirmed_digest != published_digest:
            raise BootstrapStateError("recovered promoted journal drifted")
        _verify_bootstrap_lock_held_at(
            parent_fd, journal_name, lock_fd, lock_name
        )
        final_evidence = closure_rereader(final_fd, closure)
        final_evidence = _validated_initialization_closure_evidence(
            closure, final_evidence
        )
        if final_evidence != fresh_evidence:
            raise BootstrapStateError("recovered promoted evidence drifted")
        final_seal = seal_private_tree_at(
            parent_fd,
            journal["final_name"],
            expected_tree,
            _ops=ops,
        )
        if (
            final_seal.root_identity != verified_final.final_identity
            or _closed_snapshot_identity_from_seal(final_seal, snapshot_evidence)
            != snapshot_evidence.snapshot_identity
        ):
            raise BootstrapStateError("recovered promoted seal drifted")
        require_final_only(next_journal, published_digest)
        result = PreparedPromoted(
            journal=next_journal,
            journal_digest=published_digest,
            final_fd=final_fd,
            final_identity=final_seal.root_identity,
            evidence=verified_final.evidence,
            initialization_evidence=final_evidence,
        )
        transferred = True
        return result
    except BootstrapRecoveryRequired:
        raise
    except (AtomicAcquisitionError, OSError, sqlite3.Error, AttributeError, KeyError, TypeError, ValueError) as exc:
        message = (
            "renamed-ready promoted journal requires recovery"
            if journal_advanced
            else "renamed-ready recovery verification failed"
        )
        raise BootstrapRecoveryRequired(message) from exc
    finally:
        if not transferred:
            try:
                ops.close(final_fd)
            except OSError:
                pass


def _prepare_or_resume_bootstrap_promoted_locked_at(
    parent_fd: int,
    journal_name: str,
    expected_reserved: dict[str, Any],
    staging_path: Path,
    source_db: Path,
    *,
    lock_fd: int,
    lock_name: str,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    _ops: _PrivateTreeOsOps | None = None,
    _ready_integrator: Callable[..., PreparedReadyToPromote] | None = None,
    _promoter: Callable[..., PreparedPromoted] | None = None,
    _final_verifier: Callable[..., VerifiedBootstrapFinalTree] | None = None,
    _renamed_recoverer: Callable[..., PreparedPromoted] | None = None,
    _promoted_resumer: Callable[..., PreparedPromoted] | None = None,
) -> PreparedPromoted:
    """Classify physical promotion state and reach one verified promoted residue."""

    if _ops is None:
        _require_live_private_tree_ops()
    ops = _ops or _PrivateTreeOsOps()
    ready_integrator = (
        _ready_integrator
        or _prepare_or_resume_bootstrap_ready_to_promote_locked_at
    )
    promoter = _promoter or _promote_bootstrap_ready_locked_at
    final_verifier = _final_verifier or _verify_bootstrap_final_tree_locked_at
    renamed_recoverer = (
        _renamed_recoverer or _recover_bootstrap_renamed_ready_locked_at
    )
    promoted_resumer = _promoted_resumer or _resume_bootstrap_promoted_locked_at
    journal_name = _bootstrap_basename(journal_name, "journal name")
    reserved = _validated_bootstrap_journal_payload(expected_reserved)
    semantic = _validated_semantic_options(semantic_options)
    controls = _validated_run_controls(run_controls)
    key = _validate_hmac_key(key_bytes)
    staging = Path(staging_path).expanduser().absolute()
    if (
        reserved["state"] != "reserved"
        or bootstrap_staging_name(reserved["final_name"])
        != reserved["staging_name"]
        or bootstrap_journal_name(reserved["final_name"]) != journal_name
        or staging.name != reserved["staging_name"]
        or canonical_payload_digest(semantic)
        != reserved["semantic_options_digest"]
        or canonical_payload_digest(controls) != reserved["run_controls_digest"]
        or hmac_key_id(key) != reserved["hmac_key_id"]
    ):
        raise BootstrapStateError("promoted integration authority drifted")
    immutable = {
        name: reserved[name]
        for name in (
            "schema",
            "staging_name",
            "final_name",
            "semantic_options_digest",
            "run_controls_digest",
            "hmac_key_id",
        )
    }

    def require_bindings(payload: dict[str, Any]) -> None:
        if any(payload.get(name) != value for name, value in immutable.items()):
            raise BootstrapStateError("promoted integration binding drifted")

    def exists(name: str) -> bool:
        try:
            ops.stat(name, dir_fd=parent_fd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BootstrapStateError("cannot classify promotion name") from exc
        return True

    def close_result_fd(result: object, *, message: str) -> None:
        if type(result) is not PreparedPromoted:
            raise BootstrapRecoveryRequired(message)
        descriptor = result.final_fd
        if type(descriptor) is not int:
            raise BootstrapRecoveryRequired(message)
        try:
            ops.close(descriptor)
        except (KeyError, OSError) as exc:
            raise BootstrapRecoveryRequired(f"{message} close failed") from exc

    def validate_promoted_result(
        result: PreparedPromoted,
        *,
        authoritative_journal: dict[str, Any] | None,
        authoritative_digest: str | None,
        predecessor_journal: dict[str, Any] | None,
        reopen: bool,
    ) -> PreparedPromoted:
        if type(result) is not PreparedPromoted or type(result.final_fd) is not int:
            raise BootstrapRecoveryRequired("promoted integration result is invalid")
        try:
            journal = _validated_bootstrap_journal_payload(result.journal)
            require_bindings(journal)
            if (
                journal["state"] != "promoted"
                or result.journal_digest != canonical_payload_digest(journal)
                or type(result.final_identity) is not tuple
                or len(result.final_identity) != 8
                or any(type(value) is not int for value in result.final_identity)
            ):
                raise BootstrapStateError("promoted integration result drifted")
            if authoritative_journal is not None and (
                journal != authoritative_journal
                or result.journal_digest != authoritative_digest
            ):
                raise BootstrapStateError("promoted integration authority changed")
            if predecessor_journal is not None:
                validate_bootstrap_transition(predecessor_journal, journal)
            descriptor_info = ops.fstat(result.final_fd)
            _validate_private_tree_inode(
                descriptor_info,
                kind="directory",
                owner_uid=ops.getuid(),
                label="promoted integration descriptor",
            )
            if _private_node_identity(descriptor_info) != result.final_identity:
                raise BootstrapStateError("promoted integration descriptor drifted")
            live, _journal_identity, live_digest = _read_bootstrap_journal_at(
                parent_fd, journal_name
            )
            if live != journal or live_digest != result.journal_digest:
                raise BootstrapStateError("promoted integration journal changed")
            _verify_bootstrap_lock_held_at(
                parent_fd, journal_name, lock_fd, lock_name
            )
            if exists(journal["staging_name"]) or not exists(journal["final_name"]):
                raise BootstrapStateError("promoted integration names drifted")
            if not reopen:
                return result
            prior_identity = result.final_identity
            prior_snapshot_identity = result.evidence.snapshot_evidence.snapshot_identity
            prior_journal = journal
            prior_digest = result.journal_digest
        except (
            AtomicAcquisitionError,
            AttributeError,
            KeyError,
            OSError,
            TypeError,
        ) as exc:
            try:
                ops.close(result.final_fd)
            except (KeyError, OSError):
                pass
            raise BootstrapRecoveryRequired(
                "promoted integration result requires recovery"
            ) from exc
        close_result_fd(result, message="promoted integration handoff")
        reopened = promoted_resumer(
            parent_fd,
            journal_name,
            lock_fd=lock_fd,
            lock_name=lock_name,
            staging_path=staging,
            key_bytes=key,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
        )
        if type(reopened) is not PreparedPromoted or type(reopened.final_fd) is not int:
            raise BootstrapRecoveryRequired("promoted integration reopen is invalid")
        try:
            if (
                reopened.journal != prior_journal
                or reopened.journal_digest != prior_digest
                or reopened.final_identity != prior_identity
                or reopened.evidence.snapshot_evidence.snapshot_identity
                != prior_snapshot_identity
            ):
                raise BootstrapStateError("promoted integration reopen drifted")
            return reopened
        except (AtomicAcquisitionError, AttributeError, TypeError) as exc:
            try:
                ops.close(reopened.final_fd)
            except (KeyError, OSError):
                pass
            raise BootstrapRecoveryRequired(
                "promoted integration reopen requires recovery"
            ) from exc

    _verify_bootstrap_lock_held_at(
        parent_fd, journal_name, lock_fd, lock_name
    )
    journal_exists = exists(journal_name)
    staging_exists = exists(reserved["staging_name"])
    final_exists = exists(reserved["final_name"])
    if not journal_exists:
        if staging_exists or final_exists:
            raise BootstrapStateError(
                "journal-less promotion residue is not yet resumable"
            )
        current = None
        current_digest = None
    else:
        current, _journal_identity, current_digest = (
            _read_bootstrap_journal_at(parent_fd, journal_name)
        )
        require_bindings(current)
    if current is not None and current["state"] == "promoted":
        if staging_exists or not final_exists:
            raise BootstrapRecoveryRequired("promoted physical state requires recovery")
        return validate_promoted_result(
            promoted_resumer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            ),
            authoritative_journal=current,
            authoritative_digest=current_digest,
            predecessor_journal=None,
            reopen=False,
        )
    if current is not None and current["state"] == "ready_to_promote":
        if staging_exists and not final_exists:
            prepared_ready = ready_integrator(
                parent_fd,
                journal_name,
                reserved,
                staging,
                Path(source_db).expanduser().absolute(),
                lock_fd=lock_fd,
                lock_name=lock_name,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            )
            predecessor_journal = prepared_ready.journal
            promoted = promoter(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                prepared_ready=prepared_ready,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            )
        elif not staging_exists and final_exists:
            verified = final_verifier(
                parent_fd,
                journal_name,
                expected_state="ready_to_promote",
                lock_fd=lock_fd,
                lock_name=lock_name,
                staging_path=staging,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            )
            predecessor_journal = verified.journal
            promoted = renamed_recoverer(
                parent_fd,
                journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
                verified_final=verified,
                key_bytes=key,
                semantic_options=semantic,
                run_controls=controls,
                _ops=ops,
            )
        else:
            raise BootstrapRecoveryRequired(
                "ready promotion physical state requires recovery"
            )
        return validate_promoted_result(
            promoted,
            authoritative_journal=None,
            authoritative_digest=None,
            predecessor_journal=predecessor_journal,
            reopen=True,
        )
    if final_exists:
        raise BootstrapStateError("final name exists before promotion authority")
    prepared_ready = ready_integrator(
        parent_fd,
        journal_name,
        reserved,
        staging,
        Path(source_db).expanduser().absolute(),
        lock_fd=lock_fd,
        lock_name=lock_name,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    predecessor_journal = prepared_ready.journal
    promoted = promoter(
        parent_fd,
        journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
        prepared_ready=prepared_ready,
        staging_path=staging,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
    )
    return validate_promoted_result(
        promoted,
        authoritative_journal=None,
        authoritative_digest=None,
        predecessor_journal=predecessor_journal,
        reopen=True,
    )


def processing_receipt_payload(
    result: AtomicProcessingResult, key_bytes: bytes
) -> dict[str, Any]:
    """Describe considered decisions without serializing message prose."""

    key = _validate_hmac_key(key_bytes)
    count_values = (
        result.selected_outgoing_rows,
        result.considered_rows,
        result.not_considered_after_bound,
        result.retained_rows,
    )
    if any(type(value) is not int or value < 0 for value in count_values):
        raise AtomicAcquisitionError("processing receipt counts are invalid")
    if result.selected_outgoing_rows != (
        result.considered_rows + result.not_considered_after_bound
    ):
        raise AtomicAcquisitionError("processing receipt selected accounting drifted")
    exclusions = result.excluded_considered_by_final_reason
    if type(exclusions) is not dict or set(exclusions) != set(EXCLUSION_REASONS):
        raise AtomicAcquisitionError("processing receipt exclusion reasons drifted")
    if any(type(value) is not int or value < 0 for value in exclusions.values()):
        raise AtomicAcquisitionError("processing receipt exclusion counts are invalid")
    if result.considered_rows != result.retained_rows + sum(exclusions.values()):
        raise AtomicAcquisitionError("processing receipt considered accounting drifted")
    if len(result.rows) != result.considered_rows:
        raise AtomicAcquisitionError("processing receipt coverage drifted")
    rows: list[dict[str, Any]] = []
    derived_counts: Counter[str] = Counter()
    for processed in result.rows:
        if processed.disposition not in ("retained", *EXCLUSION_REASONS):
            raise AtomicAcquisitionError("processing receipt disposition is invalid")
        row: dict[str, Any] = {
            "entry_locator": entry_locator(key, processed.candidate.message_guid),
            "disposition": processed.disposition,
        }
        if processed.disposition == "retained":
            if (
                type(processed.cleaned_text) is not str
                or not processed.cleaned_text.strip()
                or type(processed.preprocessing_metadata) is not dict
            ):
                raise AtomicAcquisitionError("retained processing row has no text")
            raw = processed.cleaned_text.encode("utf-8")
            row["content_sha256"] = _sha256_tag(raw)
            row["word_count"] = len(processed.cleaned_text.split())
        elif (
            processed.cleaned_text is not None
            or processed.preprocessing_metadata is not None
        ):
            raise AtomicAcquisitionError("excluded processing row contains retained data")
        derived_counts[processed.disposition] += 1
        rows.append(row)
    if derived_counts.get("retained", 0) != result.retained_rows or any(
        derived_counts.get(reason, 0) != exclusions[reason]
        for reason in EXCLUSION_REASONS
    ):
        raise AtomicAcquisitionError("processing receipt row accounting drifted")
    return {
        "schema": "setec-imessage-atomic-processing-receipt/2",
        "selected_eligible_rows": result.selected_outgoing_rows,
        "considered_rows": result.considered_rows,
        "not_considered_after_bound": result.not_considered_after_bound,
        "retained_rows": result.retained_rows,
        "excluded_considered_by_final_reason": result.excluded_considered_by_final_reason,
        "full_universe_eligibility_closure": result.not_considered_after_bound == 0,
        "hmac_key_id": hmac_key_id(key),
        "considered_locator_universe_hash": _locator_universe_hash(
            [row["entry_locator"] for row in rows]
        ),
        "records": rows,
        "privacy": {"contains_source_prose": False, "contains_raw_identity": False},
    }


def plan_row_artifacts(
    result: AtomicProcessingResult,
    universe: AtomicCandidateUniverse,
    initialization: InitializationClosure,
    semantic_options: dict[str, Any],
    key_bytes: bytes,
) -> tuple[PlannedAtomicRow, ...]:
    """Derive every durable row byte from the closed bootstrap and processing result."""

    key = _validate_hmac_key(key_bytes)
    semantic = _validated_semantic_options(semantic_options)
    if type(result) is not AtomicProcessingResult or type(universe) is not AtomicCandidateUniverse:
        raise AtomicAcquisitionError("row plan inputs are invalid")
    if (
        result.schema != "setec-imessage-atomic-processing-result/1"
        or result.selected_outgoing_rows != universe.selected_eligible_rows
        or result.considered_rows != len(result.rows)
        or result.considered_rows + result.not_considered_after_bound
        != result.selected_outgoing_rows
        or result.retained_rows
        + sum(result.excluded_considered_by_final_reason.values())
        != result.considered_rows
        or tuple(row.candidate for row in result.rows)
        != universe.selected[: result.considered_rows]
    ):
        raise AtomicAcquisitionError("row plan processing closure drifted")
    expected_reasons = set(EXCLUSION_REASONS)
    if set(result.excluded_considered_by_final_reason) != expected_reasons:
        raise AtomicAcquisitionError("row plan exclusion taxonomy drifted")

    contact_map = initialization.artifact(PRIVATE_CONTACT_MAP_FILENAME).payload
    source_map = initialization.artifact(PRIVATE_SOURCE_IDENTITY_MAP_FILENAME).payload
    owner = initialization.artifact(RUN_OWNER_FILENAME).payload
    if (
        contact_map.get("schema") != "setec-imessage-atomic-private-contact-map/1"
        or source_map.get("schema")
        != "setec-imessage-atomic-private-source-identity-map/2"
        or owner.get("schema") != "setec-imessage-atomic-run-owner/2"
        or owner.get("semantic_options_digest") != canonical_payload_digest(semantic)
        or owner.get("snapshot_file_sha256") is None
    ):
        raise AtomicAcquisitionError("row plan bootstrap binding drifted")
    aliases = {
        row["group_locator"]: row["contact_alias"]
        for row in contact_map.get("contacts", [])
        if type(row) is dict
    }
    sources = {
        row["entry_locator"]: row
        for row in source_map.get("entries", [])
        if type(row) is dict
    }
    if (
        len(aliases) != len(contact_map.get("contacts", []))
        or len(sources) != len(source_map.get("entries", []))
    ):
        raise AtomicAcquisitionError("row plan private map is not unique")

    planned: list[PlannedAtomicRow] = []
    seen_stems: set[str] = set()
    derived_counts: Counter[str] = Counter()
    for processed in result.rows:
        candidate = processed.candidate
        item_locator = entry_locator(key, candidate.message_guid)
        chat_locator = group_locator(key, candidate.chat_guid)
        source = sources.get(item_locator)
        if (
            type(source) is not dict
            or source.get("selected_by_date") is not True
            or source.get("group_locator") != chat_locator
            or source.get("contact_alias") != aliases.get(chat_locator)
        ):
            raise AtomicAcquisitionError("row plan source identity binding drifted")
        source_ordinal = source.get("source_ordinal")
        if type(source_ordinal) is not str:
            raise AtomicAcquisitionError("row plan source ordinal is invalid")
        disposition = processed.disposition
        if disposition not in {"retained", *EXCLUSION_REASONS}:
            raise AtomicAcquisitionError("row plan disposition is invalid")
        derived_counts[disposition] += 1
        if disposition != "retained":
            if processed.cleaned_text is not None or processed.preprocessing_metadata is not None:
                raise AtomicAcquisitionError("excluded row contains retained material")
            ledger_row = {
                "source_ordinal": source_ordinal,
                "entry_locator": item_locator,
                "disposition": disposition,
                "content_sha256": None,
                "word_count": None,
                "row_stem": None,
            }
            planned.append(PlannedAtomicRow(
                source_ordinal, item_locator, disposition, None, None, None, None,
                ledger_row,
            ))
            continue
        if (
            type(processed.cleaned_text) is not str
            or not processed.cleaned_text.strip()
            or type(processed.preprocessing_metadata) is not dict
        ):
            raise AtomicAcquisitionError("retained row has no durable text")
        alias = aliases.get(chat_locator)
        if type(alias) is not str:
            raise AtomicAcquisitionError("retained row has no contact alias")
        locator_hex = item_locator.removeprefix("hmac-sha256:")
        if len(locator_hex) != 64:
            raise AtomicAcquisitionError("retained row locator is invalid")
        row_stem = f"{alias}-{candidate.local_date.isoformat()}-{locator_hex[:16]}"
        if row_stem in seen_stems:
            raise AtomicAcquisitionError("retained row stem collides")
        seen_stems.add(row_stem)
        text_bytes = processed.cleaned_text.encode("utf-8")
        content_hash = _sha256_tag(text_bytes)
        word_count = len(processed.cleaned_text.split())
        relative_text_path = f"rows/{row_stem}/{row_stem}.txt"
        sidecar = {
            "schema": "setec-imessage-atomic-sidecar/1",
            "content_hash": content_hash,
            "word_count": word_count,
            "unix_nanoseconds": candidate.unix_nanoseconds,
            "local_date": candidate.local_date.isoformat(),
            "group_status": candidate.group_status,
            "author_corpus_group_locator": chat_locator,
            "author_corpus_entry_locator": item_locator,
            "author_corpus_unit_kind": "atomic_message",
            "author_corpus_unit_index": 0,
            "author_corpus_unit_count": 1,
            "snapshot_file_sha256": owner["snapshot_file_sha256"],
            "semantic_options_digest": owner["semantic_options_digest"],
            "preprocessing": _canonical_preprocessing_metadata(
                processed.preprocessing_metadata
            ),
            "hmac_key_id": hmac_key_id(key),
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        }
        entry = {
            "id": f"imessage-atomic-{locator_hex}",
            "path": relative_text_path,
            "author": semantic["author"],
            "persona": semantic["persona"],
            "register": semantic["register"],
            "date_written": candidate.local_date.isoformat(),
            "ai_status": ai_status_for_local_date(candidate.local_date),
            "language_status": "native",
            "word_count": word_count,
            "use": ["voice_profile"],
            "split": "baseline",
            "privacy": "private",
            "content_hash": content_hash,
            "source": "imessage_local",
            "corpus_role": "identity_baseline",
            "era": era_for_local_date(candidate.local_date),
            "consent_status": "author_consent",
            "acquired_via": "acquire_imessage_sent_atomic_1",
        }
        fragment = {
            "schema": "setec-imessage-atomic-manifest-fragment/1",
            "entry": entry,
            "entry_locator": item_locator,
            "unix_nanoseconds": candidate.unix_nanoseconds,
            "semantic_options_digest": owner["semantic_options_digest"],
            "snapshot_file_sha256": owner["snapshot_file_sha256"],
        }
        ledger_row = {
            "source_ordinal": source_ordinal,
            "entry_locator": item_locator,
            "disposition": "retained",
            "content_sha256": content_hash,
            "word_count": word_count,
            "row_stem": row_stem,
        }
        planned.append(PlannedAtomicRow(
            source_ordinal, item_locator, "retained", row_stem, text_bytes,
            sidecar, fragment, ledger_row,
        ))
    if derived_counts.get("retained", 0) != result.retained_rows or any(
        derived_counts.get(reason, 0)
        != result.excluded_considered_by_final_reason[reason]
        for reason in EXCLUSION_REASONS
    ):
        raise AtomicAcquisitionError("row plan accounting drifted")
    return tuple(planned)


def _write_new_file(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AtomicAcquisitionError("cannot publish new atomic artifact") from exc


def _atomic_rewrite(path: Path, raw: bytes, expected: bytes | None) -> None:
    if path.exists():
        if expected is None or path.read_bytes() != expected:
            raise AtomicAcquisitionError("closed atomic state changed before rewrite")
    elif expected is not None:
        raise AtomicAcquisitionError("closed atomic state disappeared before rewrite")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    _write_new_file(temporary, raw)
    try:
        os.replace(temporary, path)
    except OSError as exc:
        raise AtomicAcquisitionError("cannot atomically publish state") from exc
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        if os.name != "nt":
            raise


def _read_canonical_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AtomicAcquisitionError(f"{label} is unreadable") from exc
    if type(value) is not dict or _canonical_json_bytes(value) != raw:
        raise AtomicAcquisitionError(f"{label} is not canonical")
    return value, raw


def _row_relative_parts(relative: str) -> tuple[str, ...]:
    if type(relative) is not str or not relative:
        raise AtomicAcquisitionError("atomic row path is invalid")
    parts = tuple(relative.split("/"))
    if any(_bootstrap_basename(part, "row path component") != part for part in parts):
        raise AtomicAcquisitionError("atomic row path is invalid")
    return parts


def _canonical_object_validator(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        raise BootstrapStateError("private JSON root is invalid")
    return value


class _PathBasedSyntheticRowIo:
    """Path-based fixture publisher; never authorized for production use."""

    def __init__(self, root: Path, *, strict_private_modes: bool = True) -> None:
        self.root = Path(root).absolute()
        self.strict_private_modes = strict_private_modes
        self._checked_directory(self.root, "portable row root")

    def _checked_directory(self, path: Path, label: str) -> None:
        candidate = _portable_directory(path, label)
        if self.strict_private_modes and os.name != "nt":
            _require_owner_only_mode(candidate, 0o700)

    def _checked_file(self, path: Path, label: str) -> None:
        candidate = _portable_regular_file(path, label)
        if self.strict_private_modes and os.name != "nt":
            _require_owner_only_mode(candidate, 0o600)
            if candidate.stat().st_nlink != 1:
                raise AtomicAcquisitionError(f"{label} has an invalid link count")

    def _check_existing_ancestry(self, path: Path, label: str) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise AtomicAcquisitionError(f"{label} escapes the portable row root") from exc
        current = self.root
        self._checked_directory(current, "portable row root")
        for part in relative.parts[:-1]:
            current = current / part
            self._checked_directory(current, label)

    def _path(self, relative: str) -> Path:
        return self.root.joinpath(*_row_relative_parts(relative))

    def root_names(self) -> tuple[str, ...]:
        self._checked_directory(self.root, "portable row root")
        return tuple(sorted((item.name for item in self.root.iterdir()), key=os.fsencode))

    def exists(self, relative: str) -> bool:
        path = self._path(relative)
        self._check_existing_ancestry(path, "portable row artifact")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AtomicAcquisitionError("cannot inspect portable row artifact") from exc
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            self._checked_directory(path, "portable row artifact")
        else:
            self._checked_file(path, "portable row artifact")
        return True

    def ensure_directory(self, relative: str) -> None:
        path = self.root
        for part in _row_relative_parts(relative):
            path = path / part
            if path.exists():
                self._checked_directory(path, "portable row directory")
                continue
            path.mkdir(mode=0o700)
            if os.name != "nt":
                os.chmod(path, 0o700)
            _fsync_directory_portable(path.parent)

    def list_directory(self, relative: str) -> tuple[str, ...]:
        path = self._path(relative)
        self._check_existing_ancestry(path, "portable row directory")
        self._checked_directory(path, "portable row directory")
        return tuple(sorted((item.name for item in path.iterdir()), key=os.fsencode))

    def read_bytes(self, relative: str, label: str) -> bytes:
        path = self._path(relative)
        self._check_existing_ancestry(path, label)
        self._checked_file(path, label)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AtomicAcquisitionError(f"cannot read {label}") from exc

    def write_bytes(
        self,
        relative: str,
        raw: bytes,
        *,
        expected_existing: bytes | None,
        label: str,
    ) -> None:
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if expected_existing is None:
            _write_new_file(path, raw)
        else:
            _atomic_rewrite(path, raw, expected_existing)
        _fsync_directory_portable(path.parent)

    def write_json(
        self,
        relative: str,
        payload: dict[str, Any],
        *,
        expected_existing: bytes | None,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        label: str,
    ) -> bytes:
        raw = _canonical_json_bytes(payload)
        if validator(payload) != payload:
            raise AtomicAcquisitionError(f"{label} schema drifted")
        self.write_bytes(
            relative,
            raw,
            expected_existing=expected_existing,
            label=label,
        )
        return raw

    def remove_file(self, relative: str, *, expected: bytes, label: str) -> None:
        parts = _row_relative_parts(relative)
        parent_parts = parts[:-1]
        parent_fd = self._open_directory(parent_parts)
        parent_identity = _private_node_identity(os.fstat(parent_fd))
        mutation_started = False
        try:
            raw, identity = _read_private_bytes_at(
                parent_fd,
                parts[-1],
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
            if raw != expected:
                raise BootstrapStateError(f"{label} changed before removal")
            named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            if _stat_identity(named) != identity:
                raise BootstrapStateError(f"{label} identity drifted before removal")
            mutation_started = True
            os.unlink(parts[-1], dir_fd=parent_fd)
            os.fsync(parent_fd)
            try:
                os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapRecoveryRequired(f"{label} removal was not durable")
            self._verify_named_directory_binding(
                parent_parts,
                parent_identity,
                label=label,
            )
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutation_started:
                raise BootstrapRecoveryRequired(
                    f"{label} removal requires recovery"
                ) from exc
            raise
        finally:
            os.close(parent_fd)

    def remove_empty_directory(self, relative: str) -> None:
        parts = _row_relative_parts(relative)
        parent_parts = parts[:-1]
        parent_fd = self._open_directory(parent_parts)
        parent_identity = _private_node_identity(os.fstat(parent_fd))
        descriptor: int | None = None
        mutation_started = False
        try:
            descriptor, identity = _open_private_tree_node_at(
                parent_fd,
                parts[-1],
                kind="directory",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            )
            _stable_private_directory_inventory(
                descriptor,
                (),
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            )
            os.fsync(descriptor)
            _verify_private_tree_named_identity(
                parent_fd,
                parts[-1],
                identity,
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            )
            mutation_started = True
            os.rmdir(parts[-1], dir_fd=parent_fd)
            os.fsync(parent_fd)
            self._verify_named_directory_binding(
                parent_parts,
                parent_identity,
                label="row staging directory",
            )
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutation_started:
                raise BootstrapRecoveryRequired(
                    "row staging directory removal requires recovery"
                ) from exc
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def commit_directory(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, bytes],
    ) -> None:
        source_parts = _row_relative_parts(source)[:-1]
        destination_parts = _row_relative_parts(destination)[:-1]
        source_parent = self._open_directory(source_parts)
        destination_parent = self._open_directory(destination_parts)
        try:
            source_identity = _private_node_identity(os.fstat(source_parent))
            destination_identity = _private_node_identity(os.fstat(destination_parent))
        finally:
            os.close(source_parent)
            os.close(destination_parent)
        super().commit_directory(
            source,
            destination,
            expected_files=expected_files,
        )
        self._verify_named_directory_binding(
            source_parts,
            source_identity,
            label="row publication source",
        )
        self._verify_named_directory_binding(
            destination_parts,
            destination_identity,
            label="row publication destination",
        )

    def remove_file(self, relative: str, *, expected: bytes, label: str) -> None:
        path = self._path(relative)
        if self.read_bytes(relative, label) != expected:
            raise AtomicAcquisitionError(f"{label} changed before removal")
        path.unlink()
        _fsync_directory_portable(path.parent)

    def remove_empty_directory(self, relative: str) -> None:
        path = self._path(relative)
        if self.list_directory(relative):
            raise AtomicAcquisitionError("row staging directory is not empty")
        path.rmdir()
        _fsync_directory_portable(path.parent)

    def seal_directory(self, relative: str, expected_files: Mapping[str, bytes]) -> None:
        expected_names = tuple(expected_files)
        if self.list_directory(relative) != tuple(sorted(expected_names, key=os.fsencode)):
            raise AtomicAcquisitionError("row directory inventory drifted")
        for name, raw in expected_files.items():
            if self.read_bytes(f"{relative}/{name}", "atomic row artifact") != raw:
                raise AtomicAcquisitionError("row directory bytes drifted")

    def commit_directory(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, bytes],
    ) -> None:
        self.seal_directory(source, expected_files)
        source_path = self._path(source)
        destination_path = self._path(destination)
        if destination_path.exists():
            raise AtomicAcquisitionError("committed atomic row already exists")
        try:
            _rename_exclusive_portable(
                source_path,
                destination_path,
                label="atomic row directory",
            )
            _fsync_directory_portable(source_path.parent)
            if destination_path.parent != source_path.parent:
                _fsync_directory_portable(destination_path.parent)
        except OSError as exc:
            raise AtomicAcquisitionError("cannot commit atomic row") from exc


class _SyntheticFixtureRowIo(_PathBasedSyntheticRowIo):
    """Fixture-only name retained for tests that explicitly inject a bootstrap."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, strict_private_modes=False)


@dataclass
class _PinnedRowDirectorySeal:
    relative: str
    directory_fd: int
    directory_identity: tuple[int, int, int, int, int, int, int, int]
    children: dict[
        str,
        tuple[
            int,
            tuple[int, int, int, int, int, int, int, int],
            str,
            int,
        ],
    ]


class LiveDurableRowIo:
    """Descriptor-relative macOS publisher borrowing the pinned promoted root."""

    def __init__(
        self,
        root: Path,
        *,
        final_fd: int,
        parent_fd: int,
        final_name: str,
        journal_name: str,
        lock_fd: int,
        lock_name: str,
    ) -> None:
        _require_live_private_tree_ops()
        self.root = Path(root).absolute()
        self.final_fd = final_fd
        self.parent_fd = parent_fd
        self.final_name = _bootstrap_basename(final_name, "final name")
        self.journal_name = _bootstrap_basename(journal_name, "journal name")
        self.lock_fd = lock_fd
        self.lock_name = lock_name
        self._pending_row_seal: _PinnedRowDirectorySeal | None = None
        self._verify_root()

    def close(self) -> None:
        self._close_pending_row_seal()

    def _close_pending_row_seal(self) -> None:
        seal = self._pending_row_seal
        self._pending_row_seal = None
        if seal is None:
            return
        first_error: OSError | None = None
        for descriptor, _identity, _digest, _size in seal.children.values():
            try:
                os.close(descriptor)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        try:
            os.close(seal.directory_fd)
        except OSError as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise BootstrapRecoveryRequired(
                "pinned atomic row descriptor close requires recovery"
            ) from first_error

    def _verify_root(self) -> None:
        _verify_bootstrap_lock_held_at(
            self.parent_fd,
            self.journal_name,
            self.lock_fd,
            self.lock_name,
        )
        opened = os.fstat(self.final_fd)
        named = os.stat(
            self.final_name,
            dir_fd=self.parent_fd,
            follow_symlinks=False,
        )
        _validate_private_tree_inode(
            opened,
            kind="directory",
            owner_uid=os.getuid(),
            label="promoted atomic run",
        )
        if _private_node_identity(opened) != _private_node_identity(named):
            raise BootstrapStateError("promoted atomic run pathname drifted")

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        self._verify_root()
        descriptor = os.dup(self.final_fd)
        try:
            _validate_private_tree_inode(
                os.fstat(descriptor),
                kind="directory",
                owner_uid=os.getuid(),
                label="atomic run directory",
            )
            for part in parts:
                following, _ = _open_private_tree_node_at(
                    descriptor,
                    part,
                    kind="directory",
                    owner_uid=os.getuid(),
                    ops=_PrivateTreeOsOps(),
                    label="atomic row directory",
                )
                os.close(descriptor)
                descriptor = following
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _open_parent(self, relative: str) -> tuple[int, str]:
        parts = _row_relative_parts(relative)
        return self._open_directory(parts[:-1]), parts[-1]

    def root_names(self) -> tuple[str, ...]:
        self._verify_root()
        names, _ = _stable_private_directory_names(
            self.final_fd,
            owner_uid=os.getuid(),
            ops=_PrivateTreeOsOps(),
            label="promoted atomic run",
        )
        self._verify_root()
        return names

    def exists(self, relative: str) -> bool:
        parent_fd, name = self._open_parent(relative)
        try:
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return True
        finally:
            os.close(parent_fd)

    def ensure_directory(self, relative: str) -> None:
        current = os.dup(self.final_fd)
        try:
            for part in _row_relative_parts(relative):
                try:
                    following, _ = _open_private_tree_node_at(
                        current,
                        part,
                        kind="directory",
                        owner_uid=os.getuid(),
                        ops=_PrivateTreeOsOps(),
                        label="atomic row directory",
                    )
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                        following, identity = _open_private_tree_node_at(
                            current,
                            part,
                            kind="directory",
                            owner_uid=os.getuid(),
                            ops=_PrivateTreeOsOps(),
                            label="atomic row directory",
                        )
                        os.fchmod(following, 0o700)
                        identity = _private_node_identity(os.fstat(following))
                        if _stable_private_directory_inventory(
                            following,
                            (),
                            owner_uid=os.getuid(),
                            ops=_PrivateTreeOsOps(),
                            label="new atomic row directory",
                        ) != identity:
                            raise BootstrapRecoveryRequired(
                                "new atomic row directory identity drifted"
                            )
                        os.fsync(following)
                        os.fsync(current)
                        _verify_private_tree_named_identity(
                            current,
                            part,
                            _private_node_identity(os.fstat(following)),
                            ops=_PrivateTreeOsOps(),
                            label="new atomic row directory",
                        )
                    except BaseException:
                        raise
                os.close(current)
                current = following
            self._verify_root()
        except BaseException:
            raise
        finally:
            os.close(current)

    def list_directory(self, relative: str) -> tuple[str, ...]:
        descriptor = self._open_directory(_row_relative_parts(relative))
        try:
            names, _ = _stable_private_directory_names(
                descriptor,
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="atomic row directory",
            )
            return names
        finally:
            os.close(descriptor)

    def read_bytes(self, relative: str, label: str) -> bytes:
        parent_fd, name = self._open_parent(relative)
        try:
            raw, _ = _read_private_bytes_at(
                parent_fd,
                name,
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
            return raw
        finally:
            os.close(parent_fd)

    def write_bytes(
        self,
        relative: str,
        raw: bytes,
        *,
        expected_existing: bytes | None,
        label: str,
    ) -> None:
        self._verify_root()
        parent_fd, name = self._open_parent(relative)
        try:
            _durable_atomic_private_bytes_at(
                parent_fd,
                name,
                raw,
                expected_existing=expected_existing,
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
        finally:
            os.close(parent_fd)
        self._verify_root()

    def write_json(
        self,
        relative: str,
        payload: dict[str, Any],
        *,
        expected_existing: bytes | None,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        label: str,
    ) -> bytes:
        self._verify_root()
        parent_fd, name = self._open_parent(relative)
        raw = _canonical_json_bytes(payload)
        try:
            _write_private_canonical_json_at(
                parent_fd,
                name,
                payload,
                max_bytes=(
                    MAX_ROW_JOURNAL_BYTES
                    if relative == ROW_JOURNAL_FILENAME
                    else MAX_ROW_STATE_BYTES
                ),
                validator=validator,
                artifact_label=label,
                replace_existing=expected_existing is not None,
                expected_existing_digest=(
                    _sha256_tag(expected_existing)
                    if expected_existing is not None
                    else None
                ),
            )
        finally:
            os.close(parent_fd)
        self._verify_root()
        return raw

    def remove_file(self, relative: str, *, expected: bytes, label: str) -> None:
        self._verify_root()
        parent_fd, name = self._open_parent(relative)
        try:
            raw, identity = _read_private_bytes_at(
                parent_fd,
                name,
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
            if raw != expected:
                raise BootstrapStateError(f"{label} changed before removal")
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if _stat_identity(named) != identity:
                raise BootstrapStateError(f"{label} identity drifted before removal")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapRecoveryRequired(f"{label} removal was not durable")
        finally:
            os.close(parent_fd)
        self._verify_root()

    def remove_empty_directory(self, relative: str) -> None:
        self._verify_root()
        parent_fd, name = self._open_parent(relative)
        descriptor: int | None = None
        try:
            descriptor, identity = _open_private_tree_node_at(
                parent_fd,
                name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            )
            if _stable_private_directory_inventory(
                descriptor,
                (),
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            ) != identity:
                raise BootstrapStateError("row staging directory identity drifted")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _verify_private_tree_named_identity(
                parent_fd,
                name,
                identity,
                ops=_PrivateTreeOsOps(),
                label="row staging directory",
            )
            os.rmdir(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)
        self._verify_root()

    def _pin_row_directory(
        self,
        relative: str,
        expected_files: Mapping[str, bytes],
    ) -> _PinnedRowDirectorySeal:
        if (
            type(expected_files) is not dict
            or not expected_files
            or any(
                type(name) is not str
                or type(raw) is not bytes
                or _bootstrap_basename(name, "row artifact name") != name
                for name, raw in expected_files.items()
            )
        ):
            raise BootstrapStateError("atomic row seal expectation is invalid")
        descriptor = self._open_directory(_row_relative_parts(relative))
        children: dict[
            str,
            tuple[
                int,
                tuple[int, int, int, int, int, int, int, int],
                str,
                int,
            ],
        ] = {}
        try:
            _stable_private_directory_inventory(
                descriptor,
                tuple(sorted(expected_files, key=os.fsencode)),
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="atomic row directory",
            )
            for name, expected_raw in expected_files.items():
                child_fd, opened_identity = _open_private_tree_node_at(
                    descriptor,
                    name,
                    kind="file",
                    owner_uid=os.getuid(),
                    ops=_PrivateTreeOsOps(),
                    label="atomic row artifact",
                )
                try:
                    digest, size, hashed_identity = _stream_hash_private_fd(
                        child_fd,
                        ops=_PrivateTreeOsOps(),
                    )
                    if (
                        hashed_identity != opened_identity
                        or digest != _sha256_tag(expected_raw)
                        or size != len(expected_raw)
                    ):
                        raise BootstrapStateError("atomic row artifact bytes drifted")
                    os.fsync(child_fd)
                    digest, size, final_identity = _stream_hash_private_fd(
                        child_fd,
                        ops=_PrivateTreeOsOps(),
                    )
                    if (
                        digest != _sha256_tag(expected_raw)
                        or size != len(expected_raw)
                    ):
                        raise BootstrapRecoveryRequired(
                            "atomic row artifact changed during fsync"
                        )
                    _verify_private_tree_named_identity(
                        descriptor,
                        name,
                        final_identity,
                        ops=_PrivateTreeOsOps(),
                        label="atomic row artifact",
                    )
                    children[name] = (child_fd, final_identity, digest, size)
                except BaseException:
                    os.close(child_fd)
                    raise
            os.fsync(descriptor)
            directory_identity = _stable_private_directory_inventory(
                descriptor,
                tuple(sorted(expected_files, key=os.fsencode)),
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="atomic row directory",
            )
            for name, (_fd, identity, _digest, _size) in children.items():
                _verify_private_tree_named_identity(
                    descriptor,
                    name,
                    identity,
                    ops=_PrivateTreeOsOps(),
                    label="atomic row artifact",
                )
            self._verify_root()
            return _PinnedRowDirectorySeal(
                relative=relative,
                directory_fd=descriptor,
                directory_identity=directory_identity,
                children=children,
            )
        except BaseException as exc:
            for child_fd, _identity, _digest, _size in children.values():
                os.close(child_fd)
            os.close(descriptor)
            if isinstance(exc, OSError):
                raise BootstrapStateError("cannot seal atomic row directory") from exc
            raise

    def _verify_pinned_row_seal(
        self,
        seal: _PinnedRowDirectorySeal,
        expected_files: Mapping[str, bytes],
    ) -> None:
        directory_info = os.fstat(seal.directory_fd)
        _validate_private_tree_inode(
            directory_info,
            kind="directory",
            owner_uid=os.getuid(),
            label="pinned atomic row directory",
        )
        if _private_node_identity(directory_info) != seal.directory_identity:
            raise BootstrapStateError("pinned atomic row directory drifted")
        _stable_private_directory_inventory(
            seal.directory_fd,
            tuple(sorted(expected_files, key=os.fsencode)),
            owner_uid=os.getuid(),
            ops=_PrivateTreeOsOps(),
            label="pinned atomic row directory",
        )
        if set(seal.children) != set(expected_files):
            raise BootstrapStateError("pinned atomic row evidence drifted")
        for name, expected_raw in expected_files.items():
            child_fd, expected_identity, expected_digest, expected_size = (
                seal.children[name]
            )
            digest, size, identity = _stream_hash_private_fd(
                child_fd,
                ops=_PrivateTreeOsOps(),
            )
            if (
                identity != expected_identity
                or digest != expected_digest
                or digest != _sha256_tag(expected_raw)
                or size != expected_size
                or size != len(expected_raw)
            ):
                raise BootstrapStateError("pinned atomic row artifact drifted")
            _verify_private_tree_named_identity(
                seal.directory_fd,
                name,
                expected_identity,
                ops=_PrivateTreeOsOps(),
                label="pinned atomic row artifact",
            )

    def seal_directory(self, relative: str, expected_files: Mapping[str, bytes]) -> None:
        self._close_pending_row_seal()
        self._pending_row_seal = self._pin_row_directory(relative, expected_files)

    def commit_directory(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, bytes],
    ) -> None:
        self._verify_root()
        source_fd, source_name = self._open_parent(source)
        destination_fd, destination_name = self._open_parent(destination)
        seal = self._pending_row_seal
        if seal is None or seal.relative != source:
            self._close_pending_row_seal()
            seal = self._pin_row_directory(source, expected_files)
            self._pending_row_seal = seal
        renamed = False
        try:
            self._verify_pinned_row_seal(seal, expected_files)
            _verify_private_tree_named_identity(
                source_fd,
                source_name,
                seal.directory_identity,
                ops=_PrivateTreeOsOps(),
                label="staged atomic row",
            )
            try:
                os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapStateError("committed atomic row already exists")
            os.fsync(seal.directory_fd)
            self._verify_pinned_row_seal(seal, expected_files)
            _verify_private_tree_named_identity(
                source_fd,
                source_name,
                seal.directory_identity,
                ops=_PrivateTreeOsOps(),
                label="staged atomic row",
            )
            _macos_rename_exclusive_between_at(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )
            renamed = True
            destination_info = os.stat(
                destination_name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if (
                _private_node_identity(destination_info)[:2]
                != seal.directory_identity[:2]
            ):
                raise BootstrapRecoveryRequired("committed atomic row identity drifted")
            try:
                os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapRecoveryRequired("staged atomic row name survived commit")
            seal.directory_identity = _private_node_identity(
                os.fstat(seal.directory_fd)
            )
            self._verify_pinned_row_seal(seal, expected_files)
            os.fsync(destination_fd)
            os.fsync(source_fd)
            destination_after = os.stat(
                destination_name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if (
                _private_node_identity(destination_after)[:2]
                != seal.directory_identity[:2]
            ):
                raise BootstrapRecoveryRequired("committed atomic row changed after fsync")
            self._verify_pinned_row_seal(seal, expected_files)
        except BootstrapRecoveryRequired:
            raise
        except (BootstrapStateError, OSError) as exc:
            if renamed:
                raise BootstrapRecoveryRequired(
                    "committed atomic row requires locked recovery"
                ) from exc
            if isinstance(exc, BootstrapStateError):
                raise
            raise BootstrapStateError("cannot commit atomic row") from exc
        finally:
            os.close(source_fd)
            os.close(destination_fd)
            self._close_pending_row_seal()
        self._verify_root()


class PortableDurableRowIo(LiveDurableRowIo):
    """Descriptor-relative production tree rooted in one pinned capability.

    The portable offline adapter currently has one sound production backend:
    macOS.  Other hosts refuse here, before any namespace mutation, rather than
    falling back to pathname-based I/O.  A Windows implementation must provide
    the same handle-relative no-reparse, no-replace, exchange/CAS, and directory
    durability guarantees before it can be enabled.
    """

    def __init__(
        self,
        root: Path,
        *,
        _trusted_parent: "PortableDurableRowIo | None" = None,
        _child_name: str | None = None,
    ) -> None:
        _require_live_private_tree_ops()
        self.root = Path(root).absolute()
        self._pending_row_seal = None
        self._closed = False
        if _trusted_parent is None:
            if _child_name is not None:
                raise BootstrapStateError("portable tree child authority is invalid")
            parent_fd, name = _open_private_parent_dirfd(self.root)
        else:
            if _child_name is None:
                raise BootstrapStateError("portable tree child name is missing")
            _trusted_parent._verify_root()
            name = _bootstrap_basename(_child_name, "portable tree child name")
            parent_fd = os.dup(_trusted_parent.final_fd)
        final_fd: int | None = None
        try:
            final_fd, _ = _open_private_tree_node_at(
                parent_fd,
                name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="portable trusted root",
            )
        except BaseException:
            os.close(parent_fd)
            raise
        self.parent_fd = parent_fd
        self.final_name = name
        self.final_fd = final_fd
        self._verify_root()

    @classmethod
    def open_child(
        cls,
        parent: "PortableDurableRowIo",
        child_name: str,
    ) -> "PortableDurableRowIo":
        """Pin a child using the existing capability, never its path text."""

        name = _bootstrap_basename(child_name, "portable tree child name")
        return cls(
            parent.root / name,
            _trusted_parent=parent,
            _child_name=name,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        try:
            self._close_pending_row_seal()
        except BaseException as exc:
            first_error = exc
        for descriptor in (self.final_fd, self.parent_fd):
            try:
                os.close(descriptor)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise BootstrapRecoveryRequired(
                "portable trusted-root close requires recovery"
            ) from first_error

    def _verify_root(self) -> None:
        if self._closed:
            raise BootstrapStateError("portable trusted root is closed")
        opened = os.fstat(self.final_fd)
        named = os.stat(
            self.final_name,
            dir_fd=self.parent_fd,
            follow_symlinks=False,
        )
        _validate_private_tree_inode(
            opened,
            kind="directory",
            owner_uid=os.getuid(),
            label="portable trusted root",
        )
        if _private_node_identity(opened) != _private_node_identity(named):
            raise BootstrapStateError("portable trusted-root name drifted")

    def _verify_named_directory_binding(
        self,
        parts: tuple[str, ...],
        expected: tuple[int, int, int, int, int, int, int, int],
        *,
        label: str,
    ) -> None:
        descriptor: int | None = None
        try:
            descriptor = self._open_directory(parts)
            if _private_node_identity(os.fstat(descriptor))[:2] != expected[:2]:
                raise BootstrapRecoveryRequired(f"{label} parent binding drifted")
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            raise BootstrapRecoveryRequired(
                f"{label} parent binding requires recovery"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def ensure_directory(self, relative: str) -> None:
        parts = _row_relative_parts(relative)
        current = os.dup(self.final_fd)
        current_parts: tuple[str, ...] = ()
        mutation_started = False
        try:
            for part in parts:
                parent_identity = _private_node_identity(os.fstat(current))
                created = False
                try:
                    following, child_identity = _open_private_tree_node_at(
                        current,
                        part,
                        kind="directory",
                        owner_uid=os.getuid(),
                        ops=_PrivateTreeOsOps(),
                        label="portable tree directory",
                    )
                except FileNotFoundError:
                    mutation_started = True
                    os.mkdir(part, 0o700, dir_fd=current)
                    following, child_identity = _open_private_tree_node_at(
                        current,
                        part,
                        kind="directory",
                        owner_uid=os.getuid(),
                        ops=_PrivateTreeOsOps(),
                        label="portable tree directory",
                    )
                    created = True
                    os.fchmod(following, 0o700)
                    os.fsync(following)
                    os.fsync(current)
                if created:
                    try:
                        self._verify_named_directory_binding(
                            current_parts,
                            parent_identity,
                            label="portable directory creation",
                        )
                        self._verify_named_directory_binding(
                            (*current_parts, part),
                            child_identity,
                            label="portable directory creation",
                        )
                    except BaseException:
                        os.close(following)
                        raise
                os.close(current)
                current = following
                current_parts = (*current_parts, part)
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutation_started:
                raise BootstrapRecoveryRequired(
                    "portable directory creation requires recovery"
                ) from exc
            raise
        finally:
            os.close(current)
        self._verify_root()

    def write_bytes(
        self,
        relative: str,
        raw: bytes,
        *,
        expected_existing: bytes | None,
        label: str,
    ) -> None:
        """Mutate only through the pinned parent and rebind after durability."""

        parts = _row_relative_parts(relative)
        parent_parts = parts[:-1]
        parent_fd = self._open_directory(parent_parts)
        parent_identity = _private_node_identity(os.fstat(parent_fd))
        mutation_started = False
        try:
            mutation_started = True
            _durable_atomic_private_bytes_at(
                parent_fd,
                parts[-1],
                raw,
                expected_existing=expected_existing,
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
            self._verify_named_directory_binding(
                parent_parts,
                parent_identity,
                label=label,
            )
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutation_started:
                raise BootstrapRecoveryRequired(
                    f"{label} mutation requires recovery"
                ) from exc
            raise
        finally:
            os.close(parent_fd)

    def write_json(
        self,
        relative: str,
        payload: dict[str, Any],
        *,
        expected_existing: bytes | None,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        label: str,
    ) -> bytes:
        raw = _canonical_json_bytes(payload)
        if validator(payload) != payload:
            raise BootstrapStateError(f"{label} schema drifted")
        self.write_bytes(
            relative,
            raw,
            expected_existing=expected_existing,
            label=label,
        )
        return raw

    def verify_file(
        self,
        relative: str,
        *,
        expected_digest: str,
        expected_size: int,
        label: str,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        """Verify one named regular file through its pinned parent."""

        if not _is_sha256_tag(expected_digest) or type(expected_size) is not int:
            raise BootstrapStateError(f"{label} evidence is invalid")
        parent_fd, name = self._open_parent(relative)
        descriptor: int | None = None
        try:
            descriptor, identity = _open_private_tree_node_at(
                parent_fd,
                name,
                kind="file",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label=label,
            )
            digest, size, hashed_identity = _stream_hash_private_fd(
                descriptor,
                ops=_PrivateTreeOsOps(),
            )
            if (
                identity != hashed_identity
                or digest != expected_digest
                or size != expected_size
            ):
                raise BootstrapStateError(f"{label} bytes drifted")
            _verify_private_tree_named_identity(
                parent_fd,
                name,
                identity,
                ops=_PrivateTreeOsOps(),
                label=label,
            )
            return identity
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent_fd)

    def copy_file_resumable(
        self,
        source: Path,
        temporary: str,
        destination: str,
        *,
        expected_digest: str,
        expected_size: int,
        label: str,
    ) -> None:
        """Resume and exclusively publish a large file inside this capability."""

        source_path = _portable_regular_file(source, label)
        source_fd: int | None = None
        temporary_fd: int | None = None
        parent_fd: int | None = None
        parent_parts = _row_relative_parts(temporary)[:-1]
        parent_identity: tuple[int, int, int, int, int, int, int, int] | None = None
        namespace_started = False
        try:
            source_fd = os.open(
                source_path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            source_before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_before.st_mode)
                or source_before.st_uid != os.getuid()
                or stat.S_IMODE(source_before.st_mode) != 0o600
                or source_before.st_nlink != 1
                or source_before.st_size != expected_size
            ):
                raise BootstrapStateError(f"{label} source inode is invalid")
            temporary_parent, temporary_name = self._open_parent(temporary)
            destination_parent, destination_name = self._open_parent(destination)
            if _device_inode(os.fstat(temporary_parent)) != _device_inode(
                os.fstat(destination_parent)
            ):
                os.close(destination_parent)
                raise BootstrapStateError(f"{label} names do not share one parent")
            os.close(destination_parent)
            parent_fd = temporary_parent
            parent_identity = _private_node_identity(os.fstat(parent_fd))
            try:
                os.stat(destination_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                try:
                    os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    self.verify_file(
                        destination,
                        expected_digest=expected_digest,
                        expected_size=expected_size,
                        label=label,
                    )
                    return
                raise BootstrapStateError(f"{label} staging is ambiguous")

            try:
                temporary_fd = os.open(
                    temporary_name,
                    os.O_RDWR
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                temporary_fd = os.open(
                    temporary_name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                namespace_started = True
                os.fchmod(temporary_fd, 0o600)
                os.fsync(parent_fd)
            temporary_info = os.fstat(temporary_fd)
            if (
                not stat.S_ISREG(temporary_info.st_mode)
                or temporary_info.st_uid != os.getuid()
                or stat.S_IMODE(temporary_info.st_mode) != 0o600
                or temporary_info.st_nlink != 1
                or temporary_info.st_size > expected_size
            ):
                raise BootstrapStateError(f"{label} partial inode is invalid")
            partial_size = temporary_info.st_size
            namespace_started = True
            digest = hashlib.sha256()
            compared = 0
            while compared < partial_size:
                amount = min(1024 * 1024, partial_size - compared)
                source_chunk = os.read(source_fd, amount)
                partial_chunk = os.read(temporary_fd, amount)
                if source_chunk != partial_chunk or len(source_chunk) != amount:
                    raise BootstrapStateError(
                        f"{label} partial is not an approved prefix"
                    )
                digest.update(source_chunk)
                compared += amount
            os.lseek(temporary_fd, 0, os.SEEK_END)
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                view = memoryview(chunk)
                written = 0
                while written < len(view):
                    count = os.write(temporary_fd, view[written:])
                    if count <= 0:
                        raise BootstrapStateError(f"{label} copy was incomplete")
                    written += count
            if (
                compared + (source_before.st_size - partial_size) != expected_size
                or "sha256:" + digest.hexdigest() != expected_digest
            ):
                raise BootstrapStateError(f"{label} source bytes drifted")
            source_after = os.fstat(source_fd)
            if _stat_identity(source_before) != _stat_identity(source_after):
                raise BootstrapStateError(f"{label} source identity drifted")
            os.fsync(temporary_fd)
            copied_identity = _private_node_identity(os.fstat(temporary_fd))
            _verify_private_tree_named_identity(
                parent_fd,
                temporary_name,
                copied_identity,
                ops=_PrivateTreeOsOps(),
                label=f"{label} partial",
            )
            namespace_started = True
            _macos_rename_exclusive_at(parent_fd, temporary_name, destination_name)
            os.fsync(parent_fd)
            self.verify_file(
                destination,
                expected_digest=expected_digest,
                expected_size=expected_size,
                label=label,
            )
            assert parent_identity is not None
            self._verify_named_directory_binding(
                parent_parts,
                parent_identity,
                label=label,
            )
        except BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if namespace_started:
                raise BootstrapRecoveryRequired(
                    f"{label} copy requires recovery"
                ) from exc
            if isinstance(exc, BootstrapStateError):
                raise
            raise BootstrapStateError(f"cannot copy {label}") from exc
        finally:
            for descriptor in (temporary_fd, source_fd, parent_fd):
                if descriptor is not None:
                    os.close(descriptor)

    def commit_directory_evidence(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, tuple[str, int]],
    ) -> None:
        """Exclusively publish a flat directory verified without loading files."""

        if not expected_files:
            raise BootstrapStateError("portable directory evidence is empty")
        if any(
            _bootstrap_basename(name, "portable artifact name") != name
            or type(evidence) is not tuple
            or len(evidence) != 2
            or not _is_sha256_tag(evidence[0])
            or type(evidence[1]) is not int
            or evidence[1] < 0
            for name, evidence in expected_files.items()
        ):
            raise BootstrapStateError("portable directory evidence is invalid")
        source_fd, source_name = self._open_parent(source)
        destination_fd, destination_name = self._open_parent(destination)
        directory_fd: int | None = None
        child_fds: dict[
            str,
            tuple[int, tuple[int, int, int, int, int, int, int, int]],
        ] = {}
        renamed = False

        def verify_children() -> None:
            assert directory_fd is not None
            for name, (child_fd, expected_identity) in child_fds.items():
                digest, size, identity = _stream_hash_private_fd(
                    child_fd,
                    ops=_PrivateTreeOsOps(),
                )
                if (
                    identity != expected_identity
                    or (digest, size) != expected_files[name]
                ):
                    raise BootstrapRecoveryRequired(
                        "portable staging artifact changed during publication"
                    )
                _verify_private_tree_named_identity(
                    directory_fd,
                    name,
                    expected_identity,
                    ops=_PrivateTreeOsOps(),
                    label="portable staging artifact",
                )

        try:
            directory_fd, directory_identity = _open_private_tree_node_at(
                source_fd,
                source_name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="portable staging directory",
            )
            expected_names = tuple(sorted(expected_files, key=os.fsencode))
            _stable_private_directory_inventory(
                directory_fd,
                expected_names,
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="portable staging directory",
            )
            for name, (digest, size) in expected_files.items():
                child_fd, child_identity = _open_private_tree_node_at(
                    directory_fd,
                    name,
                    kind="file",
                    owner_uid=os.getuid(),
                    ops=_PrivateTreeOsOps(),
                    label="portable staging artifact",
                )
                observed_digest, observed_size, observed_identity = (
                    _stream_hash_private_fd(child_fd, ops=_PrivateTreeOsOps())
                )
                if (
                    observed_identity != child_identity
                    or observed_digest != digest
                    or observed_size != size
                ):
                    os.close(child_fd)
                    raise BootstrapStateError("portable staging artifact drifted")
                child_fds[name] = (child_fd, child_identity)
                os.fsync(child_fd)
            os.fsync(directory_fd)
            verify_children()
            if _stable_private_directory_inventory(
                directory_fd,
                expected_names,
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="portable staging directory",
            )[:2] != directory_identity[:2]:
                raise BootstrapRecoveryRequired(
                    "portable staging directory identity drifted"
                )
            _verify_private_tree_named_identity(
                source_fd,
                source_name,
                directory_identity,
                ops=_PrivateTreeOsOps(),
                label="portable staging directory",
            )
            try:
                os.stat(destination_name, dir_fd=destination_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapStateError("portable destination already exists")
            _macos_rename_exclusive_between_at(
                source_fd,
                source_name,
                destination_fd,
                destination_name,
            )
            renamed = True
            destination_info = os.stat(
                destination_name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if _private_node_identity(destination_info)[:2] != directory_identity[:2]:
                raise BootstrapRecoveryRequired(
                    "portable directory publication identity drifted"
                )
            try:
                os.stat(source_name, dir_fd=source_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise BootstrapRecoveryRequired(
                    "portable staging name survived publication"
                )
            verify_children()
            os.fsync(destination_fd)
            os.fsync(source_fd)
            destination_after = os.stat(
                destination_name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if _private_node_identity(destination_after)[:2] != directory_identity[:2]:
                raise BootstrapRecoveryRequired(
                    "portable directory changed after parent durability"
                )
            _stable_private_directory_inventory(
                directory_fd,
                expected_names,
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="published portable directory",
            )
            verify_children()
        except BootstrapRecoveryRequired:
            raise
        except (BootstrapStateError, OSError) as exc:
            if renamed:
                raise BootstrapRecoveryRequired(
                    "portable directory publication requires recovery"
                ) from exc
            if isinstance(exc, BootstrapStateError):
                raise
            raise BootstrapStateError("cannot publish portable directory") from exc
        finally:
            for child_fd, _identity in child_fds.values():
                os.close(child_fd)
            if directory_fd is not None:
                os.close(directory_fd)
            os.close(source_fd)
            os.close(destination_fd)
        self._verify_root()


class _PrivateReadOnlyRowIo:
    """Descriptor-pinned, no-follow reader for a completed private run."""

    def __init__(self, root: Path) -> None:
        if os.name == "nt":
            raise BootstrapStateError(
                "descriptor-relative private validation is unavailable on Windows"
            )
        self.root = Path(root).expanduser().absolute()
        self.parent_fd, self.final_name = _open_private_parent_dirfd(self.root)
        self.final_fd: int | None = None
        try:
            self.final_fd, _ = _open_private_tree_node_at(
                self.parent_fd,
                self.final_name,
                kind="directory",
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="completed atomic run",
            )
            self._verify_root()
        except BaseException:
            if self.final_fd is not None:
                os.close(self.final_fd)
            os.close(self.parent_fd)
            raise

    def close(self) -> None:
        descriptor = self.final_fd
        if descriptor is None:
            return
        self.final_fd = None
        try:
            os.close(descriptor)
        finally:
            os.close(self.parent_fd)

    def _verify_root(self) -> None:
        if self.final_fd is None:
            raise BootstrapStateError("completed atomic run reader is closed")
        opened = os.fstat(self.final_fd)
        named = os.stat(
            self.final_name,
            dir_fd=self.parent_fd,
            follow_symlinks=False,
        )
        absolute = os.stat(self.root, follow_symlinks=False)
        for info in (opened, named, absolute):
            _validate_private_tree_inode(
                info,
                kind="directory",
                owner_uid=os.getuid(),
                label="completed atomic run",
            )
        identity = _private_node_identity(opened)
        if any(_private_node_identity(info) != identity for info in (named, absolute)):
            raise BootstrapStateError("completed atomic run pathname drifted")

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        self._verify_root()
        assert self.final_fd is not None
        descriptor = os.dup(self.final_fd)
        try:
            for part in parts:
                following, _ = _open_private_tree_node_at(
                    descriptor,
                    part,
                    kind="directory",
                    owner_uid=os.getuid(),
                    ops=_PrivateTreeOsOps(),
                    label="completed atomic run directory",
                )
                os.close(descriptor)
                descriptor = following
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _open_parent(self, relative: str) -> tuple[int, str]:
        parts = _row_relative_parts(relative)
        return self._open_directory(parts[:-1]), parts[-1]

    def root_names(self) -> tuple[str, ...]:
        self._verify_root()
        assert self.final_fd is not None
        names, _ = _stable_private_directory_names(
            self.final_fd,
            owner_uid=os.getuid(),
            ops=_PrivateTreeOsOps(),
            label="completed atomic run",
        )
        self._verify_root()
        return names

    def exists(self, relative: str) -> bool:
        parent_fd, name = self._open_parent(relative)
        try:
            try:
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return True
        finally:
            os.close(parent_fd)

    def list_directory(self, relative: str) -> tuple[str, ...]:
        descriptor = self._open_directory(_row_relative_parts(relative))
        try:
            names, _ = _stable_private_directory_names(
                descriptor,
                owner_uid=os.getuid(),
                ops=_PrivateTreeOsOps(),
                label="completed atomic run directory",
            )
            return names
        finally:
            os.close(descriptor)

    def read_bytes(self, relative: str, label: str) -> bytes:
        parent_fd, name = self._open_parent(relative)
        try:
            raw, _ = _read_private_bytes_at(
                parent_fd,
                name,
                max_bytes=MAX_ROW_STATE_BYTES,
                artifact_label=label,
            )
            return raw
        finally:
            os.close(parent_fd)


def _ledger_payload(
    planned: Sequence[PlannedAtomicRow],
    closed_count: int,
    *,
    owner: dict[str, Any],
    source_map: dict[str, Any],
    result: AtomicProcessingResult,
    complete: bool,
) -> dict[str, Any]:
    if type(closed_count) is not int or not 0 <= closed_count <= len(planned):
        raise AtomicAcquisitionError("ledger closed prefix is invalid")
    rows = [dict(row.ledger_row) for row in planned[:closed_count]]
    counts = Counter(row["disposition"] for row in rows)
    considered = len(rows)
    retained = counts.get("retained", 0)
    exclusions = {reason: counts.get(reason, 0) for reason in EXCLUSION_REASONS}
    finished = complete and closed_count == len(planned)
    return {
        "schema": "setec-imessage-atomic-source-ledger/2",
        "snapshot_file_sha256": owner["snapshot_file_sha256"],
        "semantic_options_digest": owner["semantic_options_digest"],
        "run_controls_digest": owner["run_controls_digest"],
        "smoke_policy_digest": owner["smoke_policy_digest"],
        "source_hold_ledger_hash": owner["source_hold_ledger_hash"],
        "candidate_outgoing_rows": source_map["candidate_outgoing_rows"],
        "candidate_eligible_rows": source_map["candidate_eligible_rows"],
        "held_missing_chat_join_rows": source_map["held_missing_chat_join_rows"],
        "ambiguous_multi_chat_rows": source_map["ambiguous_multi_chat_rows"],
        "selected_outgoing_rows": source_map["selected_outgoing_rows"],
        "selected_eligible_rows": result.selected_outgoing_rows,
        "selected_held_missing_chat_join_rows": source_map[
            "selected_held_missing_chat_join_rows"
        ],
        "selected_ambiguous_multi_chat_rows": source_map[
            "selected_ambiguous_multi_chat_rows"
        ],
        "considered_rows": considered,
        "not_considered_after_bound": (
            result.not_considered_after_bound if finished else result.selected_outgoing_rows - considered
        ),
        "retained_rows": retained,
        "excluded_considered_by_final_reason": exclusions,
        "candidate_locator_universe_hash": source_map["candidate_locator_universe_hash"],
        "selected_locator_universe_hash": source_map["selected_locator_universe_hash"],
        "complete": finished,
        "rows": rows,
    }


def _checkpoint_payload(ledger_raw: bytes, ledger: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "setec-imessage-atomic-checkpoint/2",
        "snapshot_file_sha256": ledger["snapshot_file_sha256"],
        "semantic_options_digest": ledger["semantic_options_digest"],
        "run_controls_digest": ledger["run_controls_digest"],
        "smoke_policy_digest": ledger["smoke_policy_digest"],
        "source_hold_ledger_hash": ledger["source_hold_ledger_hash"],
        "ledger_sha256": _sha256_tag(ledger_raw),
        "candidate_outgoing_rows": ledger["candidate_outgoing_rows"],
        "candidate_eligible_rows": ledger["candidate_eligible_rows"],
        "held_missing_chat_join_rows": ledger["held_missing_chat_join_rows"],
        "ambiguous_multi_chat_rows": ledger["ambiguous_multi_chat_rows"],
        "selected_outgoing_rows": ledger["selected_outgoing_rows"],
        "selected_eligible_rows": ledger["selected_eligible_rows"],
        "selected_held_missing_chat_join_rows": ledger[
            "selected_held_missing_chat_join_rows"
        ],
        "selected_ambiguous_multi_chat_rows": ledger[
            "selected_ambiguous_multi_chat_rows"
        ],
        "considered_rows": ledger["considered_rows"],
        "retained_rows": ledger["retained_rows"],
        "complete": ledger["complete"],
    }


def _expected_row_files(row: PlannedAtomicRow) -> dict[str, bytes]:
    if row.disposition != "retained" or row.row_stem is None:
        raise AtomicAcquisitionError("excluded row has no committed files")
    if row.text_bytes is None or row.sidecar is None or row.fragment is None:
        raise AtomicAcquisitionError("retained row plan is incomplete")
    stem = row.row_stem
    return {
        f"{stem}.txt": row.text_bytes,
        f"{stem}.meta.json": _canonical_json_bytes(row.sidecar),
        f"{stem}.fragment.json": _canonical_json_bytes(row.fragment),
    }


def _validated_row_transaction_payload(value: object) -> dict[str, Any]:
    expected_keys = {
        "schema",
        "state",
        "previous_journal_digest",
        "row_index",
        "source_ordinal",
        "entry_locator",
        "disposition",
        "row_stem",
        "expected_files",
        "predecessor_ledger_digest",
        "predecessor_checkpoint_digest",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise BootstrapStateError("row transaction key set is invalid")
    state = value["state"]
    disposition = value["disposition"]
    expected_files = value["expected_files"]
    if (
        value["schema"] != "setec-imessage-atomic-row-transaction/1"
        or state not in ROW_TRANSACTION_STATES
        or type(value["row_index"]) is not int
        or value["row_index"] < 0
        or not _is_sha256_tag(value["predecessor_ledger_digest"])
        or not _is_sha256_tag(value["predecessor_checkpoint_digest"])
        or disposition not in {"retained", *EXCLUSION_REASONS}
    ):
        raise BootstrapStateError("row transaction binding is invalid")
    _binding_text("row transaction source ordinal", value["source_ordinal"])
    _binding_text("row transaction entry locator", value["entry_locator"])
    previous = value["previous_journal_digest"]
    if (state == "prepared") != (previous is None) or (
        previous is not None and not _is_sha256_tag(previous)
    ):
        raise BootstrapStateError("row transaction predecessor is invalid")
    if type(expected_files) is not dict:
        raise BootstrapStateError("row transaction file evidence is invalid")
    for name, evidence in expected_files.items():
        _bootstrap_basename(name, "row transaction file")
        if (
            type(evidence) is not dict
            or set(evidence) != {"byte_size", "sha256"}
            or type(evidence["byte_size"]) is not int
            or evidence["byte_size"] < 0
            or not _is_sha256_tag(evidence["sha256"])
        ):
            raise BootstrapStateError("row transaction file evidence is invalid")
    if disposition == "retained":
        _bootstrap_basename(value["row_stem"], "row transaction stem")
        if not expected_files:
            raise BootstrapStateError("retained row transaction has no files")
    elif value["row_stem"] is not None or expected_files:
        raise BootstrapStateError("excluded row transaction has file evidence")
    allowed = (
        ROW_TRANSACTION_STATES
        if disposition == "retained"
        else ("prepared", "ledger_closed", "checkpoint_closed")
    )
    if state not in allowed:
        raise BootstrapStateError("row transaction state is invalid for disposition")
    return json.loads(_canonical_json_bytes(value))


def _row_file_evidence(row: PlannedAtomicRow) -> dict[str, dict[str, Any]]:
    if row.disposition != "retained":
        return {}
    return {
        name: {"byte_size": len(raw), "sha256": _sha256_tag(raw)}
        for name, raw in _expected_row_files(row).items()
    }


def _row_transaction_payload(
    row: PlannedAtomicRow,
    row_index: int,
    *,
    state: str,
    previous_journal_digest: str | None,
    predecessor_ledger_digest: str,
    predecessor_checkpoint_digest: str,
) -> dict[str, Any]:
    return _validated_row_transaction_payload({
        "schema": "setec-imessage-atomic-row-transaction/1",
        "state": state,
        "previous_journal_digest": previous_journal_digest,
        "row_index": row_index,
        "source_ordinal": row.source_ordinal,
        "entry_locator": row.entry_locator,
        "disposition": row.disposition,
        "row_stem": row.row_stem,
        "expected_files": _row_file_evidence(row),
        "predecessor_ledger_digest": predecessor_ledger_digest,
        "predecessor_checkpoint_digest": predecessor_checkpoint_digest,
    })


def _read_io_object(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo,
    relative: str,
    label: str,
    *,
    validator: Callable[[dict[str, Any]], dict[str, Any]] = _canonical_object_validator,
    max_bytes: int = MAX_ROW_STATE_BYTES,
) -> tuple[dict[str, Any], bytes]:
    raw = io.read_bytes(relative, label)
    payload = _decode_canonical_private_json(
        raw,
        max_bytes=max_bytes,
        validator=validator,
        artifact_label=label,
    )
    return payload, raw


def _verify_row_directory_io(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo,
    relative: str,
    row: PlannedAtomicRow,
    *,
    allow_prefix: bool,
) -> tuple[str, ...]:
    expected = _expected_row_files(row)
    order = tuple(expected)
    actual = io.list_directory(relative)
    actual_set = set(actual)
    if allow_prefix:
        valid_sets = {frozenset(order[:index]) for index in range(len(order) + 1)}
        if frozenset(actual_set) not in valid_sets:
            raise AtomicAcquisitionError("row staging inventory is not an exact prefix")
    elif actual_set != set(order):
        raise AtomicAcquisitionError("committed atomic row inventory drifted")
    for name in actual:
        raw = io.read_bytes(f"{relative}/{name}", "atomic row artifact")
        if raw != expected[name]:
            raise AtomicAcquisitionError("committed atomic row bytes drifted")
    return actual


def _checkpoint_raw_for_ledger(ledger: dict[str, Any], ledger_raw: bytes) -> bytes:
    return _canonical_json_bytes(_checkpoint_payload(ledger_raw, ledger))


def _fault_boundary(
    fault: Callable[[str], None] | None,
    boundary: str,
) -> None:
    if fault is not None:
        fault(boundary)


def _preflight_row_publication(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo,
    planned: Sequence[PlannedAtomicRow],
    result: AtomicProcessingResult,
    *,
    owner: dict[str, Any],
    source_map: dict[str, Any],
) -> dict[str, Any]:
    """Classify the complete durable row/staging inventory before mutation."""

    fixed = {
        SNAPSHOT_FILENAME,
        *INITIALIZATION_ARTIFACT_FILENAMES,
    }
    if io.exists(OFFLINE_APPROVED_EVIDENCE_FILENAME):
        fixed.add(OFFLINE_APPROVED_EVIDENCE_FILENAME)
    mutable = {
        ROWS_DIRNAME,
        ROW_STAGING_DIRNAME,
        ROW_JOURNAL_FILENAME,
        "source-ledger.json",
        "checkpoint.json",
        "draft_manifest.jsonl",
        "acquisition-receipt.json",
    }
    root_names = set(io.root_names())
    unknown = root_names - fixed - mutable
    if unknown:
        raise AtomicAcquisitionError("atomic row top-level residue is not authorized")
    ledger_present = "source-ledger.json" in root_names
    if not ledger_present:
        forbidden = root_names & (
            mutable
            - {ROWS_DIRNAME, ROW_STAGING_DIRNAME}
        )
        rows = io.list_directory(ROWS_DIRNAME) if ROWS_DIRNAME in root_names else ()
        staging = (
            io.list_directory(ROW_STAGING_DIRNAME)
            if ROW_STAGING_DIRNAME in root_names
            else ()
        )
        if forbidden or rows or staging:
            raise AtomicAcquisitionError("unevidenced atomic row residue refuses")
        return {
            "fresh": True,
            "closed_count": 0,
            "ledger": None,
            "ledger_raw": None,
            "checkpoint_raw": None,
            "checkpoint_status": "missing",
            "journal": None,
            "journal_raw": None,
            "journal_ledgered": False,
            "staged_form": None,
        }

    ledger, ledger_raw = _read_io_object(
        io, "source-ledger.json", "source ledger"
    )
    ledger_rows = ledger.get("rows")
    if type(ledger_rows) is not list:
        raise AtomicAcquisitionError("source ledger row coverage is invalid")
    closed_count = len(ledger_rows)
    if not 0 <= closed_count <= len(planned):
        raise AtomicAcquisitionError("source ledger prefix is outside the row plan")
    expected_incomplete = _ledger_payload(
        planned,
        closed_count,
        owner=owner,
        source_map=source_map,
        result=result,
        complete=False,
    )
    expected_complete = _ledger_payload(
        planned,
        len(planned),
        owner=owner,
        source_map=source_map,
        result=result,
        complete=True,
    )
    if ledger != expected_incomplete and not (
        closed_count == len(planned) and ledger == expected_complete
    ):
        raise AtomicAcquisitionError("source ledger does not match planned prefix")
    if ledger.get("complete") is not True and closed_count == len(planned) and planned:
        raise AtomicAcquisitionError("source ledger final prefix is not closed")

    checkpoint_raw: bytes | None = None
    checkpoint_status = "missing"
    current_checkpoint_raw = _checkpoint_raw_for_ledger(ledger, ledger_raw)
    predecessor_checkpoint_raw: bytes | None = None
    if closed_count:
        predecessor_ledger = _ledger_payload(
            planned,
            closed_count - 1,
            owner=owner,
            source_map=source_map,
            result=result,
            complete=False,
        )
        predecessor_ledger_raw = _canonical_json_bytes(predecessor_ledger)
        predecessor_checkpoint_raw = _checkpoint_raw_for_ledger(
            predecessor_ledger, predecessor_ledger_raw
        )
    elif ledger.get("complete") is True:
        predecessor_ledger = _ledger_payload(
            planned,
            0,
            owner=owner,
            source_map=source_map,
            result=result,
            complete=False,
        )
        predecessor_ledger_raw = _canonical_json_bytes(predecessor_ledger)
        predecessor_checkpoint_raw = _checkpoint_raw_for_ledger(
            predecessor_ledger, predecessor_ledger_raw
        )
    if "checkpoint.json" in root_names:
        _checkpoint, checkpoint_raw = _read_io_object(
            io, "checkpoint.json", "checkpoint"
        )
        if checkpoint_raw == current_checkpoint_raw:
            checkpoint_status = "current"
        elif (
            predecessor_checkpoint_raw is not None
            and checkpoint_raw == predecessor_checkpoint_raw
        ):
            checkpoint_status = "predecessor"
        else:
            raise AtomicAcquisitionError("checkpoint is not current or immediate predecessor")

    journal: dict[str, Any] | None = None
    journal_raw: bytes | None = None
    journal_ledgered = False
    staged_form: str | None = None
    if ROW_JOURNAL_FILENAME in root_names:
        journal, journal_raw = _read_io_object(
            io,
            ROW_JOURNAL_FILENAME,
            "row transaction",
            validator=_validated_row_transaction_payload,
            max_bytes=MAX_ROW_JOURNAL_BYTES,
        )
        index = journal["row_index"]
        if not 0 <= index < len(planned):
            raise AtomicAcquisitionError("row transaction index is outside the plan")
        row = planned[index]
        predecessor_ledger = _ledger_payload(
            planned,
            index,
            owner=owner,
            source_map=source_map,
            result=result,
            complete=False,
        )
        predecessor_ledger_raw = _canonical_json_bytes(predecessor_ledger)
        predecessor_checkpoint_raw = _checkpoint_raw_for_ledger(
            predecessor_ledger, predecessor_ledger_raw
        )
        expected_prepared = _row_transaction_payload(
            row,
            index,
            state="prepared",
            previous_journal_digest=None,
            predecessor_ledger_digest=_sha256_tag(predecessor_ledger_raw),
            predecessor_checkpoint_digest=_sha256_tag(predecessor_checkpoint_raw),
        )
        immutable = set(expected_prepared) - {"state", "previous_journal_digest"}
        if any(journal[key] != expected_prepared[key] for key in immutable):
            raise AtomicAcquisitionError("row transaction does not bind the planned row")
        if closed_count == index:
            journal_ledgered = False
        elif closed_count == index + 1:
            journal_ledgered = True
        else:
            raise AtomicAcquisitionError("row transaction and ledger prefix diverged")
        state = journal["state"]
        if row.disposition == "retained":
            if state in {"prepared", "staged"} and journal_ledgered:
                raise AtomicAcquisitionError("retained row ledger advanced before commit")
            if state in {"ledger_closed", "checkpoint_closed"} and not journal_ledgered:
                raise AtomicAcquisitionError("retained row journal outran the ledger")
        else:
            if state in {"ledger_closed", "checkpoint_closed"} and not journal_ledgered:
                raise AtomicAcquisitionError("excluded row journal outran the ledger")

        if not journal_ledgered:
            if checkpoint_status != "current":
                raise AtomicAcquisitionError("row predecessor checkpoint is not closed")
        elif state == "checkpoint_closed":
            if checkpoint_status != "current":
                raise AtomicAcquisitionError("checkpoint-closed transaction lost its checkpoint")
        elif state == "ledger_closed":
            if checkpoint_status not in {"current", "predecessor", "missing"}:
                raise AtomicAcquisitionError("ledger-closed checkpoint state is invalid")
        elif checkpoint_status not in {"predecessor", "missing"}:
            raise AtomicAcquisitionError("checkpoint advanced before ledger-close authority")
    else:
        if checkpoint_status != "current":
            repairable_empty_boundary = closed_count == 0 and (
                ledger.get("complete") is False or not planned
            )
            if not repairable_empty_boundary:
                raise AtomicAcquisitionError("unevidenced checkpoint lag refuses")
        if ledger.get("complete") is not True and (
            "draft_manifest.jsonl" in root_names
            or "acquisition-receipt.json" in root_names
        ):
            raise AtomicAcquisitionError("aggregate artifacts preceded ledger closure")

    rows_names = (
        io.list_directory(ROWS_DIRNAME) if ROWS_DIRNAME in root_names else ()
    )
    staging_names = (
        io.list_directory(ROW_STAGING_DIRNAME)
        if ROW_STAGING_DIRNAME in root_names
        else ()
    )
    expected_rows = {
        row.row_stem
        for row in planned[:closed_count]
        if row.disposition == "retained" and row.row_stem is not None
    }
    authorized_extra: str | None = None
    authorized_stage: str | None = None
    if journal is not None:
        row = planned[journal["row_index"]]
        state = journal["state"]
        if row.disposition == "retained" and row.row_stem is not None:
            if journal_ledgered or state in {"committed_unledgered", "ledger_closed", "checkpoint_closed"}:
                expected_rows.add(row.row_stem)
                authorized_extra = row.row_stem
            elif state == "staged":
                in_rows = row.row_stem in rows_names
                in_staging = row.row_stem in staging_names
                if in_rows == in_staging:
                    raise AtomicAcquisitionError("staged row has ambiguous physical state")
                if in_rows:
                    expected_rows.add(row.row_stem)
                    authorized_extra = row.row_stem
                    staged_form = "committed"
                else:
                    authorized_stage = row.row_stem
                    staged_form = "staged"
            elif state == "prepared" and row.row_stem in staging_names:
                authorized_stage = row.row_stem
                staged_form = "partial"
    if set(rows_names) != expected_rows:
        raise AtomicAcquisitionError("unevidenced committed row residue refuses")
    expected_staging = {authorized_stage} if authorized_stage is not None else set()
    if set(staging_names) != expected_staging:
        raise AtomicAcquisitionError("unevidenced row staging residue refuses")
    for index, row in enumerate(planned):
        if row.disposition != "retained" or row.row_stem is None:
            continue
        if row.row_stem in expected_rows:
            _verify_row_directory_io(
                io,
                f"{ROWS_DIRNAME}/{row.row_stem}",
                row,
                allow_prefix=False,
            )
    if authorized_stage is not None and journal is not None:
        row = planned[journal["row_index"]]
        _verify_row_directory_io(
            io,
            f"{ROW_STAGING_DIRNAME}/{authorized_stage}",
            row,
            allow_prefix=journal["state"] == "prepared",
        )
    return {
        "fresh": False,
        "closed_count": closed_count,
        "ledger": ledger,
        "ledger_raw": ledger_raw,
        "checkpoint_raw": checkpoint_raw,
        "checkpoint_status": checkpoint_status,
        "journal": journal,
        "journal_raw": journal_raw,
        "journal_ledgered": journal_ledgered,
        "staged_form": staged_form,
    }


def _advance_row_journal(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo,
    journal: dict[str, Any],
    journal_raw: bytes,
    state: str,
) -> tuple[dict[str, Any], bytes]:
    advanced = dict(journal)
    advanced["state"] = state
    advanced["previous_journal_digest"] = _sha256_tag(journal_raw)
    advanced = _validated_row_transaction_payload(advanced)
    raw = io.write_json(
        ROW_JOURNAL_FILENAME,
        advanced,
        expected_existing=journal_raw,
        validator=_validated_row_transaction_payload,
        label="row transaction",
    )
    return advanced, raw


def _resume_authorized_row_transaction(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo,
    planned: Sequence[PlannedAtomicRow],
    result: AtomicProcessingResult,
    state: dict[str, Any],
    *,
    owner: dict[str, Any],
    source_map: dict[str, Any],
    fault: Callable[[str], None] | None,
) -> None:
    journal = state["journal"]
    journal_raw = state["journal_raw"]
    if type(journal) is not dict or type(journal_raw) is not bytes:
        raise AtomicAcquisitionError("row transaction resume state is absent")
    index = journal["row_index"]
    row = planned[index]
    journal_state = journal["state"]

    if journal_state == "prepared" and row.disposition == "retained":
        if row.row_stem is None:
            raise AtomicAcquisitionError("retained row stem is absent")
        stage = f"{ROW_STAGING_DIRNAME}/{row.row_stem}"
        if state["staged_form"] == "partial":
            actual = _verify_row_directory_io(io, stage, row, allow_prefix=True)
            expected = _expected_row_files(row)
            for name in actual:
                io.remove_file(
                    f"{stage}/{name}",
                    expected=expected[name],
                    label="journal-authorized staged row file",
                )
            io.remove_empty_directory(stage)
        io.ensure_directory(stage)
        boundaries = ("text", "sidecar", "fragment")
        for boundary, (name, raw) in zip(boundaries, _expected_row_files(row).items()):
            io.write_bytes(
                f"{stage}/{name}",
                raw,
                expected_existing=None,
                label=f"atomic row {boundary}",
            )
            _fault_boundary(fault, f"after_{boundary}")
        _verify_row_directory_io(io, stage, row, allow_prefix=False)
        io.seal_directory(stage, _expected_row_files(row))
        journal, journal_raw = _advance_row_journal(
            io, journal, journal_raw, "staged"
        )
        state["journal"] = journal
        state["journal_raw"] = journal_raw
        state["staged_form"] = "staged"
        _fault_boundary(fault, "after_journal_staged")
        journal_state = "staged"

    if journal_state == "staged":
        if row.row_stem is None:
            raise AtomicAcquisitionError("staged row stem is absent")
        if state["staged_form"] != "committed":
            io.commit_directory(
                f"{ROW_STAGING_DIRNAME}/{row.row_stem}",
                f"{ROWS_DIRNAME}/{row.row_stem}",
                expected_files=_expected_row_files(row),
            )
            _fault_boundary(fault, "after_row_commit")
        journal, journal_raw = _advance_row_journal(
            io, journal, journal_raw, "committed_unledgered"
        )
        state["journal"] = journal
        state["journal_raw"] = journal_raw
        state["staged_form"] = "committed"
        _fault_boundary(fault, "after_journal_committed_unledgered")
        journal_state = "committed_unledgered"

    if journal_state in {"prepared", "committed_unledgered"}:
        if not state["journal_ledgered"]:
            ledger = _ledger_payload(
                planned,
                index + 1,
                owner=owner,
                source_map=source_map,
                result=result,
                complete=index + 1 == len(planned),
            )
            ledger_raw = io.write_json(
                "source-ledger.json",
                ledger,
                expected_existing=state["ledger_raw"],
                validator=_canonical_object_validator,
                label="source ledger",
            )
            state["ledger"] = ledger
            state["ledger_raw"] = ledger_raw
            state["journal_ledgered"] = True
            state["closed_count"] = index + 1
            _fault_boundary(fault, "after_ledger")
        journal, journal_raw = _advance_row_journal(
            io, journal, journal_raw, "ledger_closed"
        )
        state["journal"] = journal
        state["journal_raw"] = journal_raw
        _fault_boundary(fault, "after_journal_ledger_closed")
        journal_state = "ledger_closed"

    if journal_state == "ledger_closed":
        ledger = state["ledger"]
        ledger_raw = state["ledger_raw"]
        expected_checkpoint = _checkpoint_raw_for_ledger(ledger, ledger_raw)
        if state["checkpoint_raw"] != expected_checkpoint:
            checkpoint_payload = _checkpoint_payload(ledger_raw, ledger)
            checkpoint_raw = io.write_json(
                "checkpoint.json",
                checkpoint_payload,
                expected_existing=state["checkpoint_raw"],
                validator=_canonical_object_validator,
                label="checkpoint",
            )
            state["checkpoint_raw"] = checkpoint_raw
            state["checkpoint_status"] = "current"
            _fault_boundary(fault, "after_checkpoint")
        journal, journal_raw = _advance_row_journal(
            io, journal, journal_raw, "checkpoint_closed"
        )
        state["journal"] = journal
        state["journal_raw"] = journal_raw
        state["checkpoint_status"] = "current"
        _fault_boundary(fault, "after_journal_checkpoint_closed")
        journal_state = "checkpoint_closed"

    if journal_state == "checkpoint_closed":
        io.remove_file(
            ROW_JOURNAL_FILENAME,
            expected=journal_raw,
            label="row transaction",
        )
        state["journal"] = None
        state["journal_raw"] = None
        state["journal_ledgered"] = False
        state["staged_form"] = None
        state["closed_count"] = max(state["closed_count"], index + 1)
        state["checkpoint_status"] = "current"
        _fault_boundary(fault, "after_journal_removed")


def _semantic_tree_payload(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo | _PrivateReadOnlyRowIo,
) -> dict[str, Any]:
    """Rebuild the semantic tree exclusively through the selected row I/O layer."""

    fixed = {
        RUN_OWNER_FILENAME,
        SEMANTIC_OPTIONS_FILENAME,
        RUN_CONTROLS_FILENAME,
        SMOKE_POLICY_FILENAME,
        PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
        "source-ledger.json",
        "checkpoint.json",
        "draft_manifest.jsonl",
    }
    if io.exists(OFFLINE_APPROVED_EVIDENCE_FILENAME):
        fixed.add(OFFLINE_APPROVED_EVIDENCE_FILENAME)
    relative_paths = list(fixed)
    if io.exists(ROWS_DIRNAME):
        for stem in io.list_directory(ROWS_DIRNAME):
            _bootstrap_basename(stem, "semantic row stem")
            for name in io.list_directory(f"{ROWS_DIRNAME}/{stem}"):
                _bootstrap_basename(name, "semantic row artifact")
                relative_paths.append(f"{ROWS_DIRNAME}/{stem}/{name}")
    entries: list[dict[str, Any]] = []
    for relative in sorted(relative_paths):
        raw = io.read_bytes(relative, "semantic tree artifact")
        entries.append({
            "path": relative,
            "sha256": _sha256_tag(raw),
            "byte_size": len(raw),
        })
    return {"schema": "setec-imessage-atomic-semantic-tree/1", "entries": entries}


def _acquisition_receipt_payload(
    *,
    owner: dict[str, Any],
    smoke_policy: dict[str, Any],
    controls: dict[str, Any],
    source_map: dict[str, Any],
    ledger: dict[str, Any],
    ledger_raw: bytes,
    manifest_raw: bytes,
    semantic_tree: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the complete receipt without accepting receipt-owned authority."""

    receipt = {
        "schema": "setec-imessage-atomic-acquisition-receipt/2",
        "tool": {
            "name": TOOL_NAME,
            "version": TOOL_VERSION,
            "capability_id": CAPABILITY_ID,
        },
        "snapshot_metadata": smoke_policy["snapshot_metadata"],
        "atomic_schema": smoke_policy["atomic_schema"],
        "snapshot_file_sha256": owner["snapshot_file_sha256"],
        "semantic_options_digest": owner["semantic_options_digest"],
        "run_controls_digest": owner["run_controls_digest"],
        "smoke_policy_digest": owner["smoke_policy_digest"],
        "hmac_key_id": owner["hmac"]["key_id"],
        "contact_map_hash": owner["contact_map_hash"],
        "source_identity_map_hash": owner["source_identity_map_hash"],
        "source_hold_ledger_hash": owner["source_hold_ledger_hash"],
        "chat_join_policy_version": owner["chat_join_policy_version"],
        "ai_boundary_version": owner["ai_boundary_version"],
        "timezone": owner["timezone"],
        "max_retained": controls["max_retained"],
        "allow_empty": controls["allow_empty"],
        "full_universe_eligibility_closure": (
            ledger["not_considered_after_bound"] == 0
        ),
        "counts": {
            "candidate": source_map["candidate_outgoing_rows"],
            "candidate_eligible": source_map["candidate_eligible_rows"],
            "held_missing_chat_join": source_map["held_missing_chat_join_rows"],
            "ambiguous_multi_chat": source_map["ambiguous_multi_chat_rows"],
            "selected": source_map["selected_outgoing_rows"],
            "selected_eligible": source_map["selected_eligible_rows"],
            "selected_held_missing_chat_join": source_map[
                "selected_held_missing_chat_join_rows"
            ],
            "selected_ambiguous_multi_chat": source_map[
                "selected_ambiguous_multi_chat_rows"
            ],
            "considered": ledger["considered_rows"],
            "not_considered_after_bound": ledger["not_considered_after_bound"],
            "retained": ledger["retained_rows"],
            "published": ledger["retained_rows"],
            "excluded_considered_by_final_reason": (
                ledger["excluded_considered_by_final_reason"]
            ),
        },
        "candidate_locator_universe_hash": (
            source_map["candidate_locator_universe_hash"]
        ),
        "selected_locator_universe_hash": (
            source_map["selected_locator_universe_hash"]
        ),
        "manifest_sha256": _sha256_tag(manifest_raw),
        "ledger_sha256": _sha256_tag(ledger_raw),
        "semantic_tree_sha256": canonical_payload_digest(semantic_tree),
        "privacy": {"contains_source_prose": False, "contains_raw_identity": False},
    }


    offline_entries = [
        entry
        for entry in semantic_tree['entries']
        if entry['path'] == OFFLINE_APPROVED_EVIDENCE_FILENAME
    ]
    if len(offline_entries) > 1:
        raise AtomicAcquisitionError('offline evidence semantic-tree binding is invalid')
    if offline_entries:
        receipt['offline_approved_evidence_sha256'] = offline_entries[0]['sha256']
    return receipt


def publish_planned_rows(
    run_dir: Path,
    planned: Sequence[PlannedAtomicRow],
    result: AtomicProcessingResult,
    fault: Callable[[str], None] | None = None,
    *,
    io: _SyntheticFixtureRowIo | LiveDurableRowIo | None = None,
) -> dict[str, Any]:
    """Resume row-journal-authorized state, then derive final aggregates."""

    root = Path(run_dir).absolute()
    row_io = io or _SyntheticFixtureRowIo(root)
    if row_io.root != root:
        raise AtomicAcquisitionError("row I/O root does not match the run directory")
    owner, _ = _read_io_object(row_io, RUN_OWNER_FILENAME, "run owner")
    source_map, _ = _read_io_object(
        row_io, PRIVATE_SOURCE_IDENTITY_MAP_FILENAME, "source identity map"
    )

    state = _preflight_row_publication(
        row_io,
        planned,
        result,
        owner=owner,
        source_map=source_map,
    )
    if state["fresh"]:
        row_io.ensure_directory(ROWS_DIRNAME)
        row_io.ensure_directory(ROW_STAGING_DIRNAME)
        ledger = _ledger_payload(
            planned,
            0,
            owner=owner,
            source_map=source_map,
            result=result,
            complete=False,
        )
        ledger_raw = row_io.write_json(
            "source-ledger.json",
            ledger,
            expected_existing=None,
            validator=_canonical_object_validator,
            label="source ledger",
        )
        state.update({
            "fresh": False,
            "ledger": ledger,
            "ledger_raw": ledger_raw,
            "closed_count": 0,
        })
        _fault_boundary(fault, "after_initial_ledger")
        checkpoint = _checkpoint_payload(ledger_raw, ledger)
        checkpoint_raw = row_io.write_json(
            "checkpoint.json",
            checkpoint,
            expected_existing=None,
            validator=_canonical_object_validator,
            label="checkpoint",
        )
        state["checkpoint_raw"] = checkpoint_raw
        state["checkpoint_status"] = "current"
        _fault_boundary(fault, "after_initial_checkpoint")

    if state["journal"] is not None:
        _resume_authorized_row_transaction(
            row_io,
            planned,
            result,
            state,
            owner=owner,
            source_map=source_map,
            fault=fault,
        )

    ledger = state["ledger"]
    ledger_raw = state["ledger_raw"]
    if type(ledger) is not dict or type(ledger_raw) is not bytes:
        raise AtomicAcquisitionError("source ledger resume state is invalid")
    if state["checkpoint_status"] != "current":
        checkpoint = _checkpoint_payload(ledger_raw, ledger)
        checkpoint_raw = row_io.write_json(
            "checkpoint.json",
            checkpoint,
            expected_existing=state["checkpoint_raw"],
            validator=_canonical_object_validator,
            label="checkpoint",
        )
        state["checkpoint_raw"] = checkpoint_raw
        state["checkpoint_status"] = "current"
        _fault_boundary(fault, "after_checkpoint")

    while state["closed_count"] < len(planned):
        closed_count = state["closed_count"]
        ledger = state["ledger"]
        ledger_raw = state["ledger_raw"]
        if type(ledger) is not dict or type(ledger_raw) is not bytes:
            raise AtomicAcquisitionError("source ledger resume state is invalid")
        row = planned[closed_count]
        predecessor_checkpoint = _checkpoint_raw_for_ledger(ledger, ledger_raw)
        journal = _row_transaction_payload(
            row,
            closed_count,
            state="prepared",
            previous_journal_digest=None,
            predecessor_ledger_digest=_sha256_tag(ledger_raw),
            predecessor_checkpoint_digest=_sha256_tag(predecessor_checkpoint),
        )
        journal_raw = row_io.write_json(
            ROW_JOURNAL_FILENAME,
            journal,
            expected_existing=None,
            validator=_validated_row_transaction_payload,
            label="row transaction",
        )
        state.update({
            "journal": journal,
            "journal_raw": journal_raw,
            "journal_ledgered": False,
            "staged_form": None,
        })
        _fault_boundary(fault, "after_journal_prepared")
        _resume_authorized_row_transaction(
            row_io,
            planned,
            result,
            state,
            owner=owner,
            source_map=source_map,
            fault=fault,
        )

    ledger = state["ledger"]
    ledger_raw = state["ledger_raw"]
    if type(ledger) is not dict or type(ledger_raw) is not bytes:
        raise AtomicAcquisitionError("source ledger resume state is invalid")
    if ledger.get("complete") is not True:
        if planned:
            raise AtomicAcquisitionError("nonempty final ledger is not complete")
        complete_ledger = _ledger_payload(
            planned,
            0,
            owner=owner,
            source_map=source_map,
            result=result,
            complete=True,
        )
        ledger_raw = row_io.write_json(
            "source-ledger.json",
            complete_ledger,
            expected_existing=ledger_raw,
            validator=_canonical_object_validator,
            label="source ledger",
        )
        state["ledger"] = complete_ledger
        state["ledger_raw"] = ledger_raw
        state["checkpoint_status"] = "predecessor"
        _fault_boundary(fault, "after_ledger")
        checkpoint = _checkpoint_payload(ledger_raw, complete_ledger)
        checkpoint_raw = row_io.write_json(
            "checkpoint.json",
            checkpoint,
            expected_existing=state["checkpoint_raw"],
            validator=_canonical_object_validator,
            label="checkpoint",
        )
        state["checkpoint_raw"] = checkpoint_raw
        state["checkpoint_status"] = "current"
        _fault_boundary(fault, "after_checkpoint")

    ledger = state["ledger"]
    ledger_raw = state["ledger_raw"]
    expected_checkpoint_raw = _checkpoint_raw_for_ledger(ledger, ledger_raw)
    if state["checkpoint_raw"] != expected_checkpoint_raw:
        raise AtomicAcquisitionError("checkpoint does not bind the closed ledger")
    fragments = [row.fragment for row in planned if row.fragment is not None]
    fragments.sort(key=lambda value: (value["unix_nanoseconds"], value["entry_locator"]))
    manifest_raw = b"".join(_canonical_json_bytes(value["entry"]) for value in fragments)
    if row_io.exists("draft_manifest.jsonl"):
        if row_io.read_bytes("draft_manifest.jsonl", "aggregate manifest") != manifest_raw:
            raise AtomicAcquisitionError("aggregate manifest drifted")
    else:
        row_io.write_bytes(
            "draft_manifest.jsonl",
            manifest_raw,
            expected_existing=None,
            label="aggregate manifest",
        )
        _fault_boundary(fault, "after_manifest")
    smoke_policy, _ = _read_io_object(row_io, SMOKE_POLICY_FILENAME, "smoke policy")
    controls, _ = _read_io_object(row_io, RUN_CONTROLS_FILENAME, "run controls")
    tree = _semantic_tree_payload(row_io)
    receipt = _acquisition_receipt_payload(
        owner=owner,
        smoke_policy=smoke_policy,
        controls=controls,
        source_map=source_map,
        ledger=ledger,
        ledger_raw=ledger_raw,
        manifest_raw=manifest_raw,
        semantic_tree=tree,
    )
    receipt_raw = _canonical_json_bytes(receipt)
    if row_io.exists("acquisition-receipt.json"):
        if row_io.read_bytes("acquisition-receipt.json", "acquisition receipt") != receipt_raw:
            raise AtomicAcquisitionError("acquisition receipt drifted")
    else:
        row_io.write_json(
            "acquisition-receipt.json",
            receipt,
            expected_existing=None,
            validator=_canonical_object_validator,
            label="acquisition receipt",
        )
        _fault_boundary(fault, "after_receipt")
    return receipt


def _is_hmac_locator(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith("hmac-sha256:")
        and len(value) == 76
        and all(char in "0123456789abcdef" for char in value[12:])
    )


def _validated_snapshot_for_run(
    io: _SyntheticFixtureRowIo | LiveDurableRowIo | _PrivateReadOnlyRowIo,
    *,
    semantic: dict[str, Any],
    controls: dict[str, Any],
    expected_metadata: dict[str, Any],
    expected_schema: dict[str, Any],
) -> AtomicCandidateUniverse:
    """Rehash, inspect, and rescan the immutable snapshot without receipt authority."""

    metadata = SnapshotMetadata(**_validated_bootstrap_snapshot(expected_metadata))
    window = semantic["local_date_window"]
    since = _dt.date.fromisoformat(window["since"]) if window["since"] else None
    until = _dt.date.fromisoformat(window["until"]) if window["until"] else None
    if (
        sys.platform == "darwin"
        and isinstance(io, (LiveDurableRowIo, _PrivateReadOnlyRowIo))
    ):
        io._verify_root()
        final_fd = io.final_fd
        if final_fd is None:
            raise BootstrapStateError("atomic run reader is closed")
        snapshot_fd, snapshot_identity = _open_private_tree_node_at(
            final_fd,
            SNAPSHOT_FILENAME,
            kind="file",
            owner_uid=os.getuid(),
            ops=_PrivateTreeOsOps(),
            label="validator snapshot file",
        )
        try:
            file_hash, byte_size, hashed_identity = _stream_hash_private_fd(
                snapshot_fd, ops=_PrivateTreeOsOps()
            )
        finally:
            os.close(snapshot_fd)
        root_identity = _private_node_identity(os.fstat(final_fd))
        if (
            hashed_identity != snapshot_identity
            or file_hash != metadata.file_sha256
            or byte_size != metadata.byte_size
        ):
            raise AtomicAcquisitionError("atomic run snapshot bytes drifted")
        evidence = ClosedSnapshotEvidence(
            metadata=metadata,
            snapshot_identity=snapshot_identity,
            staging_identity=root_identity,
            snapshot_device_inode=snapshot_identity[:2],
            staging_device_inode=root_identity[:2],
            inventory=io.root_names(),
        )
        _closed, schema_info, universe = _discover_closed_snapshot_universe_at(
            io.parent_fd,
            final_fd,
            io.final_name,
            io.root,
            evidence,
            expected_staging_device_inode=root_identity[:2],
            expected_staging_names=io.root_names(),
            semantic_options=semantic,
            run_controls=controls,
        )
    else:
        snapshot_path = io.root / SNAPSHOT_FILENAME
        try:
            snapshot_info = snapshot_path.lstat()
        except OSError as exc:
            raise SnapshotError("cannot inspect atomic run snapshot") from exc
        if not stat.S_ISREG(snapshot_info.st_mode) or stat.S_ISLNK(snapshot_info.st_mode):
            raise SnapshotError("atomic run snapshot is not a regular file")
        first_hash, first_size = _stream_hash_and_size(snapshot_path)
        conn = _open_read_only_database(snapshot_path)
        try:
            _quick_check(conn)
            recomputed = _snapshot_metadata_from_hash(
                conn, file_hash=first_hash, byte_size=first_size
            )
            schema_info = atomic_schema_preflight(conn)
            universe = discover_candidate_universe(
                conn,
                schema_info,
                apple_date_unit=semantic["apple_date_unit"],
                timezone_name=semantic["timezone"],
                since=since,
                until=until,
                max_messages=controls["max_messages"],
            )
        finally:
            conn.close()
        second_hash, second_size = _stream_hash_and_size(snapshot_path)
        if (
            not _snapshot_metadata_matches_creator_binding(recomputed, metadata)
            or second_hash != first_hash
            or second_size != first_size
        ):
            raise AtomicAcquisitionError("atomic run snapshot metadata drifted")
    if asdict(schema_info) != expected_schema:
        raise AtomicAcquisitionError("atomic run snapshot schema drifted")
    return universe


def _validate_private_identity_maps(
    contact_map: dict[str, Any],
    source_map: dict[str, Any],
    universe: AtomicCandidateUniverse,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if set(contact_map) != {"schema", "contacts"} or contact_map.get("schema") != (
        "setec-imessage-atomic-private-contact-map/1"
    ):
        raise AtomicAcquisitionError("atomic contact map schema drifted")
    contacts = contact_map.get("contacts")
    if type(contacts) is not list:
        raise AtomicAcquisitionError("atomic contact map coverage drifted")
    contact_by_group: dict[str, dict[str, Any]] = {}
    contact_by_chat: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(contacts, start=1):
        if type(row) is not dict or set(row) != {
            "contact_alias", "group_locator", "chat_guid", "chat_identifier",
            "room_name", "style", "group_status",
        }:
            raise AtomicAcquisitionError("atomic contact map row schema drifted")
        if (
            row["contact_alias"] != f"contact-{index:06d}"
            or not _is_hmac_locator(row["group_locator"])
            or row["group_locator"] in contact_by_group
            or row["chat_guid"] in contact_by_chat
            or type(row["style"]) is not int
            or type(row["chat_identifier"]) not in {str, type(None)}
            or type(row["room_name"]) not in {str, type(None)}
        ):
            raise AtomicAcquisitionError("atomic contact map row binding drifted")
        validate_stable_guid(row["chat_guid"], identity="chat")
        chat_identifier = _normalized_optional_text(row["chat_identifier"])
        room_name = _normalized_optional_text(row["room_name"])
        if (
            chat_identifier != row["chat_identifier"]
            or room_name != row["room_name"]
            or classify_group_status(room_name, row["style"]) != row["group_status"]
        ):
            raise AtomicAcquisitionError("atomic contact map metadata drifted")
        contact_by_group[row["group_locator"]] = row
        contact_by_chat[row["chat_guid"]] = row
    if [row["group_locator"] for row in contacts] != sorted(contact_by_group):
        raise AtomicAcquisitionError("atomic contact map ordering drifted")

    if set(source_map) != {
        "schema", "candidate_outgoing_rows", "candidate_eligible_rows",
        "held_missing_chat_join_rows", "ambiguous_multi_chat_rows",
        "selected_outgoing_rows", "selected_eligible_rows",
        "selected_held_missing_chat_join_rows",
        "selected_ambiguous_multi_chat_rows",
        "candidate_locator_universe_hash", "selected_locator_universe_hash", "entries",
    } or source_map.get("schema") != (
        "setec-imessage-atomic-private-source-identity-map/2"
    ):
        raise AtomicAcquisitionError("atomic source map schema drifted")
    entries = source_map.get("entries")
    if type(entries) is not list:
        raise AtomicAcquisitionError("atomic source map coverage drifted")
    source_by_locator: dict[str, dict[str, Any]] = {}
    source_by_guid: dict[str, dict[str, Any]] = {}
    chat_groups: dict[str, str] = {}
    selected_locators: list[str] = []
    for index, row in enumerate(entries, start=1):
        if type(row) is not dict or set(row) != {
            "source_ordinal", "entry_locator", "message_guid", "group_locator",
            "contact_alias", "selected_by_date", "chat_join_disposition",
        }:
            raise AtomicAcquisitionError("atomic source map row schema drifted")
        if (
            row["source_ordinal"] != f"source-{index:06d}"
            or not _is_hmac_locator(row["entry_locator"])
            or type(row["selected_by_date"]) is not bool
            or row["chat_join_disposition"] not in {"eligible", "missing_chat_join"}
            or type(row["group_locator"]) not in {str, type(None)}
            or type(row["contact_alias"]) not in {str, type(None)}
            or row["entry_locator"] in source_by_locator
            or row["message_guid"] in source_by_guid
        ):
            raise AtomicAcquisitionError("atomic source map row binding drifted")
        if (
            (row["chat_join_disposition"] == "eligible")
            != _is_hmac_locator(row["group_locator"])
            or (
                row["chat_join_disposition"] == "missing_chat_join"
                and (row["group_locator"] is not None or row["contact_alias"] is not None)
            )
        ):
            raise AtomicAcquisitionError("atomic source map disposition drifted")
        validate_stable_guid(row["message_guid"], identity="message")
        source_by_locator[row["entry_locator"]] = row
        source_by_guid[row["message_guid"]] = row
        if row["selected_by_date"]:
            selected_locators.append(row["entry_locator"])
    candidate_locators = [row["entry_locator"] for row in entries]
    if candidate_locators != sorted(candidate_locators):
        raise AtomicAcquisitionError("atomic source map ordering drifted")
    if (
        source_map["candidate_outgoing_rows"] != len(entries)
        or source_map["candidate_eligible_rows"] != universe.candidate_eligible_rows
        or source_map["held_missing_chat_join_rows"]
        != universe.held_missing_chat_join_rows
        or source_map["ambiguous_multi_chat_rows"] != 0
        or source_map["selected_outgoing_rows"] != len(selected_locators)
        or source_map["selected_eligible_rows"] != universe.selected_eligible_rows
        or source_map["selected_held_missing_chat_join_rows"]
        != universe.selected_held_missing_chat_join_rows
        or source_map["selected_ambiguous_multi_chat_rows"] != 0
        or source_map["candidate_locator_universe_hash"]
        != _locator_universe_hash(candidate_locators)
        or source_map["selected_locator_universe_hash"]
        != _locator_universe_hash(selected_locators)
        or len(entries) != universe.candidate_outgoing_rows
        or len(selected_locators) != universe.selected_outgoing_rows
    ):
        raise AtomicAcquisitionError("atomic source map universe binding drifted")

    selected_guids = {candidate.message_guid for candidate in universe.selected}
    expected_guids = {
        candidate.message_guid for candidate in universe.candidates
    } | {held.message_guid for held in universe.held}
    if set(source_by_guid) != expected_guids:
        raise AtomicAcquisitionError("atomic source map snapshot coverage drifted")
    selected_chats: set[str] = set()
    for candidate in universe.candidates:
        row = source_by_guid[candidate.message_guid]
        selected = candidate.message_guid in selected_guids
        previous_group = chat_groups.setdefault(candidate.chat_guid, row["group_locator"])
        if (
            previous_group != row["group_locator"]
            or row["selected_by_date"] is not selected
            or row["chat_join_disposition"] != "eligible"
        ):
            raise AtomicAcquisitionError("atomic source map snapshot binding drifted")
        contact = contact_by_chat.get(candidate.chat_guid)
        if selected:
            selected_chats.add(candidate.chat_guid)
            if (
                contact is None
                or contact["group_locator"] != row["group_locator"]
                or contact["contact_alias"] != row["contact_alias"]
                or contact["chat_identifier"] != _normalized_optional_text(candidate.chat_identifier)
                or contact["room_name"] != _normalized_optional_text(candidate.room_name)
                or contact["style"] != candidate.style
                or contact["group_status"] != candidate.group_status
            ):
                raise AtomicAcquisitionError("atomic source/contact map binding drifted")
        elif row["contact_alias"] is not None:
            raise AtomicAcquisitionError("atomic unselected source gained an alias")
    selected_held_guids = {held.message_guid for held in universe.selected_held}
    for held in universe.held:
        row = source_by_guid[held.message_guid]
        if (
            row["chat_join_disposition"] != "missing_chat_join"
            or row["group_locator"] is not None
            or row["contact_alias"] is not None
            or row["selected_by_date"] is not (held.message_guid in selected_held_guids)
        ):
            raise AtomicAcquisitionError("atomic held source map binding drifted")
    if set(contact_by_chat) != selected_chats:
        raise AtomicAcquisitionError("atomic contact map selected coverage drifted")
    return source_by_locator, source_by_guid


def _validate_atomic_run_io(
    root: Path,
    io: _SyntheticFixtureRowIo | LiveDurableRowIo | _PrivateReadOnlyRowIo,
) -> dict[str, Any]:
    required = {
        SNAPSHOT_FILENAME, *INITIALIZATION_ARTIFACT_FILENAMES,
        ROWS_DIRNAME, ROW_STAGING_DIRNAME, "source-ledger.json", "checkpoint.json",
        "draft_manifest.jsonl", "acquisition-receipt.json",
    }
    root_names = set(io.root_names())
    if OFFLINE_APPROVED_EVIDENCE_FILENAME in root_names:
        required.add(OFFLINE_APPROVED_EVIDENCE_FILENAME)
    if root_names != required:
        raise AtomicAcquisitionError("atomic run top-level inventory drifted")
    if io.list_directory(ROW_STAGING_DIRNAME):
        raise AtomicAcquisitionError("atomic run staging inventory is not empty")

    semantic, semantic_raw = _read_io_object(
        io, SEMANTIC_OPTIONS_FILENAME, "semantic options",
        validator=_validated_semantic_options, max_bytes=MAX_SEMANTIC_OPTIONS_BYTES,
    )
    controls, controls_raw = _read_io_object(
        io, RUN_CONTROLS_FILENAME, "run controls",
        validator=_validated_run_controls, max_bytes=MAX_RUN_CONTROLS_BYTES,
    )
    smoke, smoke_raw = _read_io_object(
        io, SMOKE_POLICY_FILENAME, "smoke policy",
        validator=_validated_smoke_policy, max_bytes=MAX_SMOKE_POLICY_BYTES,
    )
    contact_map, contact_raw = _read_io_object(
        io, PRIVATE_CONTACT_MAP_FILENAME, "contact map",
        max_bytes=MAX_PRIVATE_CONTACT_MAP_BYTES,
    )
    source_map, source_raw = _read_io_object(
        io, PRIVATE_SOURCE_IDENTITY_MAP_FILENAME, "source identity map",
        max_bytes=MAX_PRIVATE_SOURCE_IDENTITY_MAP_BYTES,
    )
    hold_ledger, hold_raw = _read_io_object(
        io, PRIVATE_SOURCE_HOLD_LEDGER_FILENAME, "private source hold ledger",
        max_bytes=MAX_PRIVATE_SOURCE_HOLD_LEDGER_BYTES,
    )
    owner, _owner_raw = _read_io_object(
        io, RUN_OWNER_FILENAME, "run owner", max_bytes=MAX_RUN_OWNER_BYTES,
    )
    if controls["checkpoint_schema"] != "setec-imessage-atomic-checkpoint/2":
        raise AtomicAcquisitionError("atomic checkpoint schema control drifted")
    universe = _validated_snapshot_for_run(
        io,
        semantic=semantic,
        controls=controls,
        expected_metadata=smoke["snapshot_metadata"],
        expected_schema=smoke["atomic_schema"],
    )
    source_by_locator, source_by_guid = _validate_private_identity_maps(
        contact_map, source_map, universe
    )
    if OFFLINE_APPROVED_EVIDENCE_FILENAME in root_names:
        offline_evidence, _ = _read_io_object(
            io,
            OFFLINE_APPROVED_EVIDENCE_FILENAME,
            'offline approved evidence',
            validator=_validated_offline_approved_evidence,
            max_bytes=MAX_OFFLINE_APPROVED_EVIDENCE_BYTES,
        )
        _validate_offline_evidence_against_run(
            offline_evidence,
            smoke_policy=smoke,
            source_map=source_map,
        )
    selected_held_guids = {row.message_guid for row in universe.selected_held}
    expected_holds = sorted(
        (
            {
                "source_ordinal": source_by_guid[held.message_guid]["source_ordinal"],
                "entry_locator": source_by_guid[held.message_guid]["entry_locator"],
                "reason": "missing_chat_join",
                "selected_by_date": held.message_guid in selected_held_guids,
            }
            for held in universe.held
        ),
        key=lambda row: row["entry_locator"],
    )
    expected_hold_ledger = {
        "schema": "setec-imessage-atomic-private-source-hold-ledger/1",
        "snapshot_file_sha256": smoke["snapshot_metadata"]["file_sha256"],
        "chat_join_policy_version": CHAT_JOIN_POLICY_VERSION,
        "candidate_outgoing_rows": universe.candidate_outgoing_rows,
        "held_missing_chat_join_rows": universe.held_missing_chat_join_rows,
        "selected_held_missing_chat_join_rows": (
            universe.selected_held_missing_chat_join_rows
        ),
        "candidate_locator_universe_hash": source_map[
            "candidate_locator_universe_hash"
        ],
        "holds": expected_holds,
    }
    if hold_ledger != expected_hold_ledger:
        raise AtomicAcquisitionError("atomic private source hold ledger drifted")
    expected_owner = run_owner_payload(
        snapshot_metadata=SnapshotMetadata(**smoke["snapshot_metadata"]),
        semantic_options=semantic,
        run_controls=controls,
        smoke_policy=smoke,
        hmac_key_id_value=smoke["hmac"]["key_id"],
        contact_map_hash=_sha256_tag(contact_raw),
        source_identity_map_hash=_sha256_tag(source_raw),
        source_hold_ledger_hash=_sha256_tag(hold_raw),
    )
    if owner != expected_owner or (
        canonical_payload_digest(semantic) != _sha256_tag(semantic_raw)
        or canonical_payload_digest(controls) != _sha256_tag(controls_raw)
        or canonical_payload_digest(smoke) != _sha256_tag(smoke_raw)
    ):
        raise AtomicAcquisitionError("atomic run owner or option binding drifted")

    ledger, ledger_raw = _read_io_object(io, "source-ledger.json", "source ledger")
    ledger_keys = {
        "schema", "snapshot_file_sha256", "semantic_options_digest",
        "run_controls_digest", "smoke_policy_digest", "source_hold_ledger_hash",
        "candidate_outgoing_rows", "candidate_eligible_rows",
        "held_missing_chat_join_rows", "ambiguous_multi_chat_rows",
        "selected_outgoing_rows", "selected_eligible_rows",
        "selected_held_missing_chat_join_rows",
        "selected_ambiguous_multi_chat_rows",
        "considered_rows", "not_considered_after_bound", "retained_rows",
        "excluded_considered_by_final_reason", "candidate_locator_universe_hash",
        "selected_locator_universe_hash", "complete", "rows",
    }
    exclusions = ledger.get("excluded_considered_by_final_reason")
    rows = ledger.get("rows")
    integer_counts = (
        ledger.get("candidate_outgoing_rows"), ledger.get("candidate_eligible_rows"),
        ledger.get("held_missing_chat_join_rows"), ledger.get("ambiguous_multi_chat_rows"),
        ledger.get("selected_outgoing_rows"), ledger.get("selected_eligible_rows"),
        ledger.get("selected_held_missing_chat_join_rows"),
        ledger.get("selected_ambiguous_multi_chat_rows"), ledger.get("considered_rows"),
        ledger.get("not_considered_after_bound"), ledger.get("retained_rows"),
    )
    if (
        set(ledger) != ledger_keys
        or ledger.get("schema") != "setec-imessage-atomic-source-ledger/2"
        or ledger.get("complete") is not True
        or type(rows) is not list
        or type(exclusions) is not dict
        or set(exclusions) != set(EXCLUSION_REASONS)
        or any(type(value) is not int or value < 0 for value in integer_counts)
        or any(type(value) is not int or value < 0 for value in exclusions.values())
        or ledger["snapshot_file_sha256"] != owner["snapshot_file_sha256"]
        or ledger["semantic_options_digest"] != owner["semantic_options_digest"]
        or ledger["run_controls_digest"] != owner["run_controls_digest"]
        or ledger["smoke_policy_digest"] != owner["smoke_policy_digest"]
        or ledger["source_hold_ledger_hash"] != owner["source_hold_ledger_hash"]
        or ledger["candidate_outgoing_rows"] != universe.candidate_outgoing_rows
        or ledger["candidate_eligible_rows"] != universe.candidate_eligible_rows
        or ledger["held_missing_chat_join_rows"]
        != universe.held_missing_chat_join_rows
        or ledger["ambiguous_multi_chat_rows"] != 0
        or ledger["candidate_locator_universe_hash"]
        != source_map["candidate_locator_universe_hash"]
        or ledger["selected_locator_universe_hash"]
        != source_map["selected_locator_universe_hash"]
        or ledger["selected_outgoing_rows"] != universe.selected_outgoing_rows
        or ledger["selected_eligible_rows"] != universe.selected_eligible_rows
        or ledger["selected_held_missing_chat_join_rows"]
        != universe.selected_held_missing_chat_join_rows
        or ledger["selected_ambiguous_multi_chat_rows"] != 0
        or ledger["candidate_outgoing_rows"]
        != ledger["candidate_eligible_rows"]
        + ledger["held_missing_chat_join_rows"]
        + ledger["ambiguous_multi_chat_rows"]
        or ledger["selected_outgoing_rows"]
        != ledger["selected_eligible_rows"]
        + ledger["selected_held_missing_chat_join_rows"]
        + ledger["selected_ambiguous_multi_chat_rows"]
        or ledger["considered_rows"] != len(rows)
        or ledger["selected_eligible_rows"]
        != ledger["considered_rows"] + ledger["not_considered_after_bound"]
        or ledger["considered_rows"]
        != ledger["retained_rows"] + sum(exclusions.values())
    ):
        raise AtomicAcquisitionError("atomic source ledger binding drifted")
    max_retained = controls["max_retained"]
    if (
        (max_retained is None and ledger["not_considered_after_bound"] != 0)
        or (max_retained is not None and ledger["retained_rows"] > max_retained)
        or (
            ledger["not_considered_after_bound"] > 0
            and ledger["retained_rows"] != max_retained
        )
        or (ledger["retained_rows"] == 0 and controls["allow_empty"] is not True)
    ):
        raise AtomicAcquisitionError("atomic bounded-run equation drifted")

    selected_locator_order = [
        source_by_guid[candidate.message_guid]["entry_locator"]
        for candidate in universe.selected
    ]
    if [row.get("entry_locator") for row in rows] != selected_locator_order[: len(rows)]:
        raise AtomicAcquisitionError("atomic ledger canonical prefix drifted")
    derived_counts: Counter[str] = Counter()
    retained_rows: list[dict[str, Any]] = []
    for row in rows:
        if type(row) is not dict or set(row) != {
            "source_ordinal", "entry_locator", "disposition", "content_sha256",
            "word_count", "row_stem",
        }:
            raise AtomicAcquisitionError("atomic ledger row schema drifted")
        source = source_by_locator.get(row["entry_locator"])
        if source is None or row["source_ordinal"] != source["source_ordinal"]:
            raise AtomicAcquisitionError("atomic ledger source binding drifted")
        disposition = row["disposition"]
        if disposition not in {"retained", *EXCLUSION_REASONS}:
            raise AtomicAcquisitionError("atomic ledger disposition drifted")
        derived_counts[disposition] += 1
        if disposition == "retained":
            if (
                not _is_sha256_tag(row["content_sha256"])
                or type(row["word_count"]) is not int
                or row["word_count"] < 0
                or type(row["row_stem"]) is not str
            ):
                raise AtomicAcquisitionError("atomic retained ledger row drifted")
            retained_rows.append(row)
        elif any(row[name] is not None for name in ("content_sha256", "word_count", "row_stem")):
            raise AtomicAcquisitionError("atomic excluded ledger row gained row data")
    if derived_counts.get("retained", 0) != ledger["retained_rows"] or any(
        derived_counts.get(reason, 0) != exclusions[reason]
        for reason in EXCLUSION_REASONS
    ):
        raise AtomicAcquisitionError("atomic ledger disposition counts drifted")

    actual_row_stems = set(io.list_directory(ROWS_DIRNAME))
    expected_row_stems = {row["row_stem"] for row in retained_rows}
    if actual_row_stems != expected_row_stems:
        raise AtomicAcquisitionError("atomic run row inventory drifted")
    candidates_by_guid = {candidate.message_guid: candidate for candidate in universe.candidates}
    source_guid_by_locator = {
        row["entry_locator"]: row["message_guid"] for row in source_map["entries"]
    }
    fragments: list[dict[str, Any]] = []
    seen_locators: set[str] = set()
    sidecar_keys = {
        "schema", "content_hash", "word_count", "unix_nanoseconds", "local_date",
        "group_status", "author_corpus_group_locator", "author_corpus_entry_locator",
        "author_corpus_unit_kind", "author_corpus_unit_index",
        "author_corpus_unit_count", "snapshot_file_sha256",
        "semantic_options_digest", "preprocessing", "hmac_key_id", "tool",
    }
    fragment_keys = {
        "schema", "entry", "entry_locator", "unix_nanoseconds",
        "semantic_options_digest", "snapshot_file_sha256",
    }
    for ledger_row in retained_rows:
        locator = ledger_row["entry_locator"]
        source = source_by_locator[locator]
        candidate = candidates_by_guid[source_guid_by_locator[locator]]
        stem = ledger_row["row_stem"]
        expected_stem = (
            f"{source['contact_alias']}-{candidate.local_date.isoformat()}-"
            f"{locator.removeprefix('hmac-sha256:')[:16]}"
        )
        if stem != expected_stem or locator in seen_locators:
            raise AtomicAcquisitionError("atomic row stem or locator drifted")
        seen_locators.add(locator)
        expected_names = {
            f"{stem}.txt", f"{stem}.meta.json", f"{stem}.fragment.json"
        }
        if set(io.list_directory(f"{ROWS_DIRNAME}/{stem}")) != expected_names:
            raise AtomicAcquisitionError("atomic run row inventory drifted")
        text_raw = io.read_bytes(
            f"{ROWS_DIRNAME}/{stem}/{stem}.txt", "atomic row text"
        )
        try:
            text = text_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AtomicAcquisitionError("atomic row text is not UTF-8") from exc
        if not text.strip():
            raise AtomicAcquisitionError("atomic retained row text is empty")
        sidecar, _ = _read_io_object(
            io, f"{ROWS_DIRNAME}/{stem}/{stem}.meta.json", "atomic sidecar"
        )
        fragment, _ = _read_io_object(
            io, f"{ROWS_DIRNAME}/{stem}/{stem}.fragment.json", "atomic fragment"
        )
        if set(sidecar) != sidecar_keys or set(fragment) != fragment_keys:
            raise AtomicAcquisitionError("atomic run row schema drifted")
        preprocessing = sidecar.get("preprocessing")
        if type(preprocessing) is not dict:
            raise AtomicAcquisitionError("atomic sidecar preprocessing drifted")
        expected_sidecar = {
            "schema": "setec-imessage-atomic-sidecar/1",
            "content_hash": _sha256_tag(text_raw),
            "word_count": len(text.split()),
            "unix_nanoseconds": candidate.unix_nanoseconds,
            "local_date": candidate.local_date.isoformat(),
            "group_status": candidate.group_status,
            "author_corpus_group_locator": source["group_locator"],
            "author_corpus_entry_locator": locator,
            "author_corpus_unit_kind": "atomic_message",
            "author_corpus_unit_index": 0,
            "author_corpus_unit_count": 1,
            "snapshot_file_sha256": owner["snapshot_file_sha256"],
            "semantic_options_digest": owner["semantic_options_digest"],
            "preprocessing": preprocessing,
            "hmac_key_id": owner["hmac"]["key_id"],
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        }
        if sidecar != expected_sidecar:
            raise AtomicAcquisitionError("atomic run row binding drifted")
        locator_hex = locator.removeprefix("hmac-sha256:")
        entry = {
            "id": f"imessage-atomic-{locator_hex}",
            "path": f"rows/{stem}/{stem}.txt",
            "author": semantic["author"],
            "persona": semantic["persona"],
            "register": semantic["register"],
            "date_written": candidate.local_date.isoformat(),
            "ai_status": ai_status_for_local_date(candidate.local_date),
            "language_status": "native",
            "word_count": len(text.split()),
            "use": ["voice_profile"],
            "split": "baseline",
            "privacy": "private",
            "content_hash": _sha256_tag(text_raw),
            "source": "imessage_local",
            "corpus_role": "identity_baseline",
            "era": era_for_local_date(candidate.local_date),
            "consent_status": "author_consent",
            "acquired_via": "acquire_imessage_sent_atomic_1",
        }
        expected_fragment = {
            "schema": "setec-imessage-atomic-manifest-fragment/1",
            "entry": entry,
            "entry_locator": locator,
            "unix_nanoseconds": candidate.unix_nanoseconds,
            "semantic_options_digest": owner["semantic_options_digest"],
            "snapshot_file_sha256": owner["snapshot_file_sha256"],
        }
        if fragment != expected_fragment or ledger_row != {
            "source_ordinal": source["source_ordinal"],
            "entry_locator": locator,
            "disposition": "retained",
            "content_sha256": _sha256_tag(text_raw),
            "word_count": len(text.split()),
            "row_stem": stem,
        }:
            raise AtomicAcquisitionError("atomic fragment or ledger rebuild drifted")
        fragments.append(fragment)

    fragments.sort(key=lambda value: (value["unix_nanoseconds"], value["entry_locator"]))
    manifest_raw = b"".join(_canonical_json_bytes(value["entry"]) for value in fragments)
    if io.read_bytes("draft_manifest.jsonl", "aggregate manifest") != manifest_raw:
        raise AtomicAcquisitionError("atomic run manifest derivation drifted")
    checkpoint, _ = _read_io_object(io, "checkpoint.json", "checkpoint")
    if checkpoint != _checkpoint_payload(ledger_raw, ledger):
        raise AtomicAcquisitionError("atomic run checkpoint drifted")
    receipt, receipt_raw = _read_io_object(io, "acquisition-receipt.json", "receipt")
    semantic_tree = _semantic_tree_payload(io)
    expected_receipt = _acquisition_receipt_payload(
        owner=owner,
        smoke_policy=smoke,
        controls=controls,
        source_map=source_map,
        ledger=ledger,
        ledger_raw=ledger_raw,
        manifest_raw=manifest_raw,
        semantic_tree=semantic_tree,
    )
    if receipt != expected_receipt:
        raise AtomicAcquisitionError("atomic acquisition receipt drifted")
    forbidden_identities = {
        row["message_guid"] for row in source_map["entries"]
    } | {
        value
        for row in contact_map["contacts"]
        for value in (
            row["chat_guid"], row["chat_identifier"], row["room_name"]
        )
        if type(value) is str and value
    }
    forbidden_raw = tuple(value.encode("utf-8") for value in forbidden_identities)
    for entry in semantic_tree["entries"]:
        raw = io.read_bytes(entry["path"], "semantic privacy artifact")
        if any(sentinel in raw for sentinel in forbidden_raw):
            raise AtomicAcquisitionError("atomic semantic tree leaks raw identity")
    if any(sentinel in receipt_raw for sentinel in forbidden_raw):
        raise AtomicAcquisitionError("atomic acquisition receipt leaks raw identity")
    return {
        "status": "closed",
        "candidate_outgoing_rows": ledger["candidate_outgoing_rows"],
        "candidate_eligible_rows": ledger["candidate_eligible_rows"],
        "retained_rows": ledger["retained_rows"],
        "held_missing_chat_join_rows": ledger["held_missing_chat_join_rows"],
        "ambiguous_multi_chat_rows": ledger["ambiguous_multi_chat_rows"],
        "selected_outgoing_rows": ledger["selected_outgoing_rows"],
        "selected_eligible_rows": ledger["selected_eligible_rows"],
        "selected_held_missing_chat_join_rows": ledger[
            "selected_held_missing_chat_join_rows"
        ],
        "selected_ambiguous_multi_chat_rows": ledger[
            "selected_ambiguous_multi_chat_rows"
        ],
        "considered_rows": ledger["considered_rows"],
        "not_considered_after_bound": ledger["not_considered_after_bound"],
    }


def validate_atomic_run(
    run_dir: Path,
    *,
    io: _SyntheticFixtureRowIo | LiveDurableRowIo | _PrivateReadOnlyRowIo | None = None,
) -> dict[str, Any]:
    """Strictly reconstruct and validate a completed atomic producer tree."""

    root = Path(run_dir).expanduser().absolute()
    owned_reader: _PrivateReadOnlyRowIo | None = None
    row_io = io
    if row_io is None:
        if os.name != "nt" and PRIVATE_ROOT_COMPONENT in root.parts:
            owned_reader = _PrivateReadOnlyRowIo(root)
            row_io = owned_reader
        else:
            row_io = _SyntheticFixtureRowIo(root)
    if row_io.root != root:
        raise AtomicAcquisitionError("validator row I/O root does not match the run")
    try:
        return _validate_atomic_run_io(root, row_io)
    finally:
        if owned_reader is not None:
            owned_reader.close()


def _date_argument(value: str) -> _dt.date:
    try:
        parsed = _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must be canonical YYYY-MM-DD")
    return parsed


def _synthetic_fixture_bootstrap(
    config: AtomicRunConfig,
    key_bytes: bytes,
    semantic: dict[str, Any],
    controls: dict[str, Any],
) -> tuple[Path, AtomicCandidateUniverse, AtomicSchemaInfo, InitializationClosure]:
    """Portable fixture-only bootstrap; never dispatched by the CLI."""

    final = config.output_root.absolute() / _bootstrap_basename(config.run_id, "run ID")
    if final.exists():
        snapshot = final / SNAPSHOT_FILENAME
        conn = _open_read_only_database(snapshot)
        try:
            metadata = _snapshot_metadata(conn, snapshot)
            schema = atomic_schema_preflight(conn)
            universe = discover_candidate_universe(
                conn, schema, apple_date_unit=config.apple_date_unit,
                timezone_name=config.timezone_name, since=config.since,
                until=config.until, max_messages=config.max_messages,
            )
        finally:
            conn.close()
        initialization = build_initialization_closure(
            snapshot_metadata=metadata, schema_info=schema, universe=universe,
            key_bytes=key_bytes, semantic_options=semantic, run_controls=controls,
        )
        for artifact in initialization.artifacts:
            if (final / artifact.filename).read_bytes() != artifact.raw:
                raise AtomicAcquisitionError("synthetic bootstrap binding drifted")
        return final, universe, schema, initialization
    staging = final.with_name(bootstrap_staging_name(final.name))
    snapshot, metadata = materialize_consistent_snapshot(config.source_db, staging)
    conn = _open_read_only_database(snapshot)
    try:
        schema = atomic_schema_preflight(conn)
        universe = discover_candidate_universe(
            conn, schema, apple_date_unit=config.apple_date_unit,
            timezone_name=config.timezone_name, since=config.since,
            until=config.until, max_messages=config.max_messages,
        )
    finally:
        conn.close()
    initialization = build_initialization_closure(
        snapshot_metadata=metadata, schema_info=schema, universe=universe,
        key_bytes=key_bytes, semantic_options=semantic, run_controls=controls,
    )
    for artifact in initialization.artifacts:
        _write_new_file(staging / artifact.filename, artifact.raw)
    os.rename(staging, final)
    return final, universe, schema, initialization


def _portable_regular_file(path: Path, label: str) -> Path:
    absolute = Path(path).expanduser().absolute()
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise AtomicAcquisitionError(f'cannot inspect {label}') from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse_or_symlink(absolute)
    ):
        raise AtomicAcquisitionError(f'{label} is not a direct regular file')
    return absolute


def _portable_directory(path: Path, label: str) -> Path:
    absolute = Path(path).expanduser().absolute()
    try:
        info = absolute.lstat()
    except OSError as exc:
        raise AtomicAcquisitionError(f'cannot inspect {label}') from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse_or_symlink(absolute)
    ):
        raise AtomicAcquisitionError(f'{label} is not a direct directory')
    return absolute


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except ValueError:
        return False
    except OSError as exc:
        raise AtomicAcquisitionError('cannot resolve offline approval path') from exc
    return True


def _consume_offline_live_smoke_receipt(
    path: Path,
    *,
    approved_run: Path,
) -> tuple[dict[str, Any], bytes]:
    receipt_path = _portable_regular_file(path, 'offline live smoke receipt')
    if receipt_path.name != 'imessage-atomic-live-smoke-receipt.json':
        raise AtomicAcquisitionError('live smoke receipt path is not exact')
    if _path_is_within(receipt_path, approved_run):
        raise AtomicAcquisitionError('live smoke receipt must remain outside the approved run')
    descriptor: int | None = None
    try:
        before = receipt_path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse_or_symlink(receipt_path)
        ):
            raise AtomicAcquisitionError('live smoke receipt is not a direct regular file')
        if before.st_size > MAX_LIVE_SMOKE_RECEIPT_BYTES:
            raise AtomicAcquisitionError('live smoke receipt exceeds its size bound')
        flags = (
            os.O_RDONLY
            | getattr(os, 'O_BINARY', 0)
            | getattr(os, 'O_NOINHERIT', 0)
            | getattr(os, 'O_NOFOLLOW', 0)
        )
        descriptor = os.open(receipt_path, flags)
        opened_before = os.fstat(descriptor)
        chunks: list[bytes] = []
        remaining = MAX_LIVE_SMOKE_RECEIPT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        opened_after = os.fstat(descriptor)
        after = receipt_path.lstat()
    except OSError as exc:
        raise AtomicAcquisitionError('cannot read live smoke receipt') from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    raw = b''.join(chunks)
    identity_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    identities = (before, opened_before, opened_after, after)
    if any(
        getattr(identities[0], field) != getattr(item, field)
        for item in identities[1:]
        for field in identity_fields
    ) or stat.S_ISLNK(after.st_mode) or _is_reparse_or_symlink(receipt_path):
        raise AtomicAcquisitionError('live smoke receipt changed while being read')
    if len(raw) > MAX_LIVE_SMOKE_RECEIPT_BYTES:
        raise AtomicAcquisitionError('live smoke receipt exceeds its size bound')
    if os.name != 'nt' and any(
        item.st_uid != os.getuid() or stat.S_IMODE(item.st_mode) != 0o600
        for item in identities
    ):
        raise AtomicAcquisitionError('live smoke receipt permissions changed while being read')
    payload = _decode_canonical_private_json(
        raw,
        max_bytes=MAX_LIVE_SMOKE_RECEIPT_BYTES,
        validator=_validated_live_smoke_receipt_payload,
        artifact_label='live smoke receipt',
    )
    return payload, raw


def _scan_portable_candidate_universe(
    database: Path,
    *,
    semantic_options: dict[str, Any],
    max_messages: int,
) -> tuple[SnapshotMetadata, AtomicSchemaInfo, AtomicCandidateUniverse]:
    source = _portable_regular_file(database, 'offline equivalence database')
    _reject_snapshot_sidecars(source)
    first_hash, first_size = _stream_hash_and_size(source)
    window = semantic_options['local_date_window']
    since = _dt.date.fromisoformat(window['since']) if window['since'] else None
    until = _dt.date.fromisoformat(window['until']) if window['until'] else None
    conn = _open_immutable_read_only_database(source)
    try:
        _quick_check(conn)
        metadata = _snapshot_metadata_from_hash(
            conn,
            file_hash=first_hash,
            byte_size=first_size,
        )
        schema = atomic_schema_preflight(conn)
        universe = discover_candidate_universe(
            conn,
            schema,
            apple_date_unit=semantic_options['apple_date_unit'],
            timezone_name=semantic_options['timezone'],
            since=since,
            until=until,
            max_messages=max_messages,
        )
    finally:
        conn.close()
    _reject_snapshot_sidecars(source)
    second_hash, second_size = _stream_hash_and_size(source)
    if os.name != 'nt':
        _require_owner_only_mode(source, 0o600)
    if (second_hash, second_size) != (first_hash, first_size):
        raise AtomicAcquisitionError('offline equivalence database changed during scan')
    return metadata, schema, universe


def _validated_offline_approved_evidence(value: object) -> dict[str, Any]:
    keys = {
        'schema',
        'archive_file_sha256',
        'archive_byte_size',
        'approved_snapshot_file_sha256',
        'schema_fingerprint',
        'counts',
        'candidate_locator_universe_hash',
        'selected_locator_universe_hash',
        'held_locator_universe_hash',
        'selected_held_locator_universe_hash',
        'approved_run_receipt_sha256',
        'live_smoke_receipt_sha256',
        'smoke_policy_digest',
        'hmac_key_id',
    }
    count_keys = {
        'candidate_outgoing_rows',
        'candidate_eligible_rows',
        'held_missing_chat_join_rows',
        'ambiguous_multi_chat_rows',
        'selected_outgoing_rows',
        'selected_eligible_rows',
        'selected_held_missing_chat_join_rows',
        'selected_ambiguous_multi_chat_rows',
    }
    if (
        type(value) is not dict
        or set(value) != keys
        or value.get('schema') != 'setec-imessage-atomic-offline-approved-evidence/1'
        or type(value.get('archive_byte_size')) is not int
        or value['archive_byte_size'] < 1
        or type(value.get('counts')) is not dict
        or set(value['counts']) != count_keys
        or any(type(count) is not int or count < 0 for count in value['counts'].values())
    ):
        raise BootstrapStateError('offline approved evidence schema is invalid')
    digest_fields = keys - {'schema', 'archive_byte_size', 'counts'}
    if any(not _is_sha256_tag(value.get(name)) for name in digest_fields):
        raise BootstrapStateError('offline approved evidence digest is invalid')
    return json.loads(_canonical_json_bytes(value))


def _offline_approved_evidence_payload(
    *,
    archive_metadata: SnapshotMetadata,
    approved_snapshot: SnapshotMetadata,
    schema_info: AtomicSchemaInfo,
    source_map: dict[str, Any],
    approved_run_receipt_sha256: str,
    live_smoke_receipt_sha256: str,
    smoke_policy_digest: str,
    hmac_key_id_value: str,
) -> dict[str, Any]:
    held = sorted(
        row['entry_locator']
        for row in source_map['entries']
        if row['chat_join_disposition'] == 'missing_chat_join'
    )
    selected_held = sorted(
        row['entry_locator']
        for row in source_map['entries']
        if row['chat_join_disposition'] == 'missing_chat_join'
        and row['selected_by_date']
    )
    count_names = (
        'candidate_outgoing_rows',
        'candidate_eligible_rows',
        'held_missing_chat_join_rows',
        'ambiguous_multi_chat_rows',
        'selected_outgoing_rows',
        'selected_eligible_rows',
        'selected_held_missing_chat_join_rows',
        'selected_ambiguous_multi_chat_rows',
    )
    return _validated_offline_approved_evidence({
        'schema': 'setec-imessage-atomic-offline-approved-evidence/1',
        'archive_file_sha256': archive_metadata.file_sha256,
        'archive_byte_size': archive_metadata.byte_size,
        'approved_snapshot_file_sha256': approved_snapshot.file_sha256,
        'schema_fingerprint': schema_info.schema_fingerprint,
        'counts': {name: source_map[name] for name in count_names},
        'candidate_locator_universe_hash': source_map[
            'candidate_locator_universe_hash'
        ],
        'selected_locator_universe_hash': source_map[
            'selected_locator_universe_hash'
        ],
        'held_locator_universe_hash': _locator_universe_hash(held),
        'selected_held_locator_universe_hash': _locator_universe_hash(selected_held),
        'approved_run_receipt_sha256': approved_run_receipt_sha256,
        'live_smoke_receipt_sha256': live_smoke_receipt_sha256,
        'smoke_policy_digest': smoke_policy_digest,
        'hmac_key_id': hmac_key_id_value,
    })


def _validate_offline_evidence_against_run(
    evidence: dict[str, Any],
    *,
    smoke_policy: dict[str, Any],
    source_map: dict[str, Any],
) -> None:
    validated = _validated_offline_approved_evidence(evidence)
    approved_snapshot = SnapshotMetadata(**smoke_policy['snapshot_metadata'])
    archive_metadata = replace(
        approved_snapshot,
        file_sha256=validated['archive_file_sha256'],
        byte_size=validated['archive_byte_size'],
    )
    expected = _offline_approved_evidence_payload(
        archive_metadata=archive_metadata,
        approved_snapshot=approved_snapshot,
        schema_info=AtomicSchemaInfo(**smoke_policy['atomic_schema']),
        source_map=source_map,
        approved_run_receipt_sha256=validated['approved_run_receipt_sha256'],
        live_smoke_receipt_sha256=validated['live_smoke_receipt_sha256'],
        smoke_policy_digest=canonical_payload_digest(smoke_policy),
        hmac_key_id_value=smoke_policy['hmac']['key_id'],
    )
    if validated != expected:
        raise AtomicAcquisitionError('offline approved evidence drifted from the run')


def _authorize_offline_approved_import(
    config: AtomicRunConfig,
    authorization: OfflineApprovedImport,
    *,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
) -> _OfflineApprovedContext:
    if type(authorization) is not OfflineApprovedImport:
        raise AtomicAcquisitionError('offline approval configuration is invalid')
    key = _validate_hmac_key(key_bytes)
    approved_run = _portable_directory(
        authorization.approved_smoke_run,
        'approved smoke run',
    )
    archive = _portable_regular_file(
        authorization.archive_equivalence_db,
        'offline equivalence database',
    )
    if os.name != 'nt':
        _require_owner_only_mode(archive, 0o600)
    if Path(config.source_db).expanduser().absolute() != archive:
        raise AtomicAcquisitionError('offline source must be the equivalence database')
    offline_paths = (
        approved_run,
        authorization.live_smoke_receipt,
        archive,
        config.output_root,
    )
    try:
        roots = {_private_root_path(Path(path)) for path in offline_paths}
        for path in offline_paths:
            _require_private_destination(Path(path))
        output_root = _portable_directory(config.output_root, 'offline output root')
        _require_owner_only_mode(output_root, 0o700)
    except (AtomicAcquisitionError, SnapshotError) as exc:
        raise AtomicAcquisitionError('offline paths are not one closed private root') from exc
    if len(roots) != 1:
        raise AtomicAcquisitionError('offline paths do not share one private root')
    try:
        same_archive = os.path.samefile(archive, approved_run / SNAPSHOT_FILENAME)
    except OSError as exc:
        raise AtomicAcquisitionError('cannot compare offline archive identity') from exc
    if same_archive or _path_is_within(archive, approved_run):
        raise AtomicAcquisitionError(
            'offline archive must be independent of the approved smoke run'
        )

    summary = validate_atomic_run(approved_run)
    approved_io = _SyntheticFixtureRowIo(approved_run)
    semantic, _ = _read_io_object(
        approved_io,
        SEMANTIC_OPTIONS_FILENAME,
        'approved semantic options',
        validator=_validated_semantic_options,
        max_bytes=MAX_SEMANTIC_OPTIONS_BYTES,
    )
    approved_controls, _ = _read_io_object(
        approved_io,
        RUN_CONTROLS_FILENAME,
        'approved run controls',
        validator=_validated_run_controls,
        max_bytes=MAX_RUN_CONTROLS_BYTES,
    )
    smoke, _ = _read_io_object(
        approved_io,
        SMOKE_POLICY_FILENAME,
        'approved smoke policy',
        validator=_validated_smoke_policy,
        max_bytes=MAX_SMOKE_POLICY_BYTES,
    )
    owner, _ = _read_io_object(
        approved_io,
        RUN_OWNER_FILENAME,
        'approved run owner',
        max_bytes=MAX_RUN_OWNER_BYTES,
    )
    approved_receipt, approved_receipt_raw = _read_io_object(
        approved_io,
        'acquisition-receipt.json',
        'approved acquisition receipt',
    )
    if (
        summary.get('retained_rows') != 1
        or approved_controls.get('max_retained') != 1
        or approved_controls.get('allow_empty') is not False
        or approved_receipt.get('counts', {}).get('retained') != 1
    ):
        raise AtomicAcquisitionError('offline approval requires a closed one-row smoke run')
    if semantic != semantic_options:
        raise AtomicAcquisitionError('offline semantic options differ from the approved run')
    expected_controls = dict(approved_controls)
    expected_controls['max_retained'] = None
    if run_controls != expected_controls:
        raise AtomicAcquisitionError(
            'offline full-run controls may change only max_retained from one to unbounded'
        )

    live_receipt, live_receipt_raw = _consume_offline_live_smoke_receipt(
        authorization.live_smoke_receipt,
        approved_run=approved_run,
    )
    approved_receipt_sha256 = _sha256_tag(approved_receipt_raw)
    approved_smoke_digest = canonical_payload_digest(smoke)
    key_id = hmac_key_id(key)
    if live_receipt['approved_run_receipt_sha256'] != approved_receipt_sha256:
        raise AtomicAcquisitionError('live smoke receipt does not bind the approved run receipt')
    if (
        live_receipt['smoke_policy_digest'] != approved_smoke_digest
        or approved_receipt.get('smoke_policy_digest') != approved_smoke_digest
        or owner.get('smoke_policy_digest') != approved_smoke_digest
    ):
        raise AtomicAcquisitionError('approved smoke-policy digest binding drifted')
    if (
        smoke.get('hmac', {}).get('key_id') != key_id
        or owner.get('hmac', {}).get('key_id') != key_id
    ):
        raise HmacKeyError('offline HMAC key does not match the approved run')

    metadata = SnapshotMetadata(**smoke['snapshot_metadata'])
    schema_info = AtomicSchemaInfo(**smoke['atomic_schema'])
    approved_universe = _validated_snapshot_for_run(
        approved_io,
        semantic=semantic,
        controls=approved_controls,
        expected_metadata=smoke['snapshot_metadata'],
        expected_schema=smoke['atomic_schema'],
    )
    archive_metadata, archive_schema, archive_universe = (
        _scan_portable_candidate_universe(
            archive,
            semantic_options=semantic,
            max_messages=approved_controls['max_messages'],
        )
    )
    if archive_schema != schema_info:
        raise AtomicAcquisitionError('archive schema differs from the approved snapshot')
    if archive_universe != approved_universe:
        raise AtomicAcquisitionError(
            'archive candidate universe differs from the approved snapshot'
        )

    initialization = build_initialization_closure(
        snapshot_metadata=metadata,
        schema_info=schema_info,
        universe=approved_universe,
        key_bytes=key,
        semantic_options=semantic_options,
        run_controls=run_controls,
    )
    if initialization.artifact(SMOKE_POLICY_FILENAME).digest != approved_smoke_digest:
        raise AtomicAcquisitionError('offline initialization changed the approved smoke policy')
    source_map = initialization.artifact(PRIVATE_SOURCE_IDENTITY_MAP_FILENAME).payload
    evidence_payload = _offline_approved_evidence_payload(
        archive_metadata=archive_metadata,
        approved_snapshot=metadata,
        schema_info=schema_info,
        source_map=source_map,
        approved_run_receipt_sha256=approved_receipt_sha256,
        live_smoke_receipt_sha256=_sha256_tag(live_receipt_raw),
        smoke_policy_digest=approved_smoke_digest,
        hmac_key_id_value=key_id,
    )
    offline_evidence = _close_private_json(
        filename=OFFLINE_APPROVED_EVIDENCE_FILENAME,
        label='offline approved evidence',
        max_bytes=MAX_OFFLINE_APPROVED_EVIDENCE_BYTES,
        payload=evidence_payload,
        validator=_validated_offline_approved_evidence,
    )
    return _OfflineApprovedContext(
        approved_snapshot=approved_run / SNAPSHOT_FILENAME,
        approved_receipt_sha256=approved_receipt_sha256,
        snapshot_metadata=metadata,
        schema_info=schema_info,
        universe=approved_universe,
        semantic_options=semantic_options,
        run_controls=run_controls,
        initialization=initialization,
        offline_evidence=offline_evidence,
    )


def _offline_bootstrap_names(run_id: str) -> tuple[str, str]:
    final_name = _bootstrap_basename(run_id, 'run ID')
    return (
        f'.{final_name}.offline-approved-staging',
        f'.{final_name}.offline-approved-journal.json',
    )


def _offline_bootstrap_journal_payload(
    config: AtomicRunConfig,
    context: _OfflineApprovedContext,
) -> dict[str, Any]:
    return {
        'schema': 'setec-imessage-atomic-offline-approved-bootstrap/1',
        'final_name': _bootstrap_basename(config.run_id, 'run ID'),
        'approved_run_receipt_sha256': context.approved_receipt_sha256,
        'snapshot_file_sha256': context.snapshot_metadata.file_sha256,
        'snapshot_byte_size': context.snapshot_metadata.byte_size,
        'semantic_options_digest': canonical_payload_digest(context.semantic_options),
        'run_controls_digest': canonical_payload_digest(context.run_controls),
        'hmac_key_id': context.initialization.artifact(RUN_OWNER_FILENAME).payload[
            'hmac'
        ]['key_id'],
        'offline_evidence_sha256': context.offline_evidence.digest,
    }


def _offline_initialization_artifacts(
    context: _OfflineApprovedContext,
) -> tuple[ClosedPrivateJson, ...]:
    return (*context.initialization.artifacts, context.offline_evidence)


def _rename_exclusive_portable(source: Path, destination: Path, *, label: str) -> None:
    """Atomically create one destination name without replacing foreign state."""

    try:
        if sys.platform == 'darwin':
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            function = libc.renamex_np
            function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            if function(os.fsencode(source), os.fsencode(destination), 0x00000004) != 0:
                value = ctypes.get_errno()
                raise OSError(value, os.strerror(value), str(destination))
            return
        if sys.platform.startswith('linux'):
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            function = getattr(libc, 'renameat2', None)
            if function is None:
                raise AtomicAcquisitionError(
                    f'{label} publication requires atomic no-replace rename'
                )
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int
            if function(
                -100,
                os.fsencode(source),
                -100,
                os.fsencode(destination),
                1,
            ) != 0:
                value = ctypes.get_errno()
                raise OSError(value, os.strerror(value), str(destination))
            return
        if os.name == 'nt':
            os.rename(source, destination)
            return
        raise AtomicAcquisitionError(
            f'{label} publication requires atomic no-replace rename'
        )
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY} \
                or getattr(exc, 'winerror', None) == 183:
            raise AtomicAcquisitionError(f'{label} destination already exists') from exc
        raise


def _fsync_directory_portable(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        if os.name != 'nt':
            raise


def _verify_offline_snapshot(
    tree: PortableDurableRowIo,
    relative: str,
    metadata: SnapshotMetadata,
) -> None:
    tree.verify_file(
        relative,
        expected_digest=metadata.file_sha256,
        expected_size=metadata.byte_size,
        label='offline approved snapshot copy',
    )
    parent = relative.rsplit('/', 1)[0] if '/' in relative else None
    names = tree.list_directory(parent) if parent is not None else tree.root_names()
    snapshot_name = relative.rsplit('/', 1)[-1]
    forbidden = {snapshot_name + '-wal', snapshot_name + '-shm', snapshot_name + '-journal'}
    if forbidden & set(names):
        raise AtomicAcquisitionError('offline approved snapshot has SQLite sidecars')


def _copy_offline_snapshot_resumable(
    tree: PortableDurableRowIo,
    source: Path,
    temporary: str,
    destination: str,
    metadata: SnapshotMetadata,
) -> None:
    tree.copy_file_resumable(
        source,
        temporary,
        destination,
        expected_digest=metadata.file_sha256,
        expected_size=metadata.byte_size,
        label='approved smoke snapshot',
    )
    _verify_offline_snapshot(tree, destination, metadata)


def _verify_offline_initialization(
    tree: PortableDurableRowIo,
    context: _OfflineApprovedContext,
) -> None:
    _verify_offline_snapshot(tree, SNAPSHOT_FILENAME, context.snapshot_metadata)
    for artifact in _offline_initialization_artifacts(context):
        raw = tree.read_bytes(artifact.filename, 'offline initialization artifact')
        if raw != artifact.raw:
            raise AtomicAcquisitionError('offline initialization artifact drifted')


def _validate_offline_staging_prefix(
    tree: PortableDurableRowIo,
    staging: str,
    context: _OfflineApprovedContext,
) -> None:
    names = tree.list_directory(staging)
    temporary_name = f'.{SNAPSHOT_FILENAME}.copying'
    artifact_order = tuple(
        item.filename for item in _offline_initialization_artifacts(context)
    )
    allowed = {temporary_name, SNAPSHOT_FILENAME, *artifact_order}
    if not set(names) <= allowed:
        raise AtomicAcquisitionError('offline bootstrap staging contains a foreign artifact')
    if temporary_name in names:
        if set(names) != {temporary_name}:
            raise AtomicAcquisitionError('offline partial snapshot staging is ambiguous')
        return
    if SNAPSHOT_FILENAME not in names:
        if names:
            raise AtomicAcquisitionError('offline bootstrap staging prefix is invalid')
        return
    _verify_offline_snapshot(
        tree,
        f'{staging}/{SNAPSHOT_FILENAME}',
        context.snapshot_metadata,
    )
    present = tuple(name for name in artifact_order if name in names)
    if present != artifact_order[: len(present)]:
        raise AtomicAcquisitionError('offline initialization is not an exact prefix')
    if set(names) != {SNAPSHOT_FILENAME, *present}:
        raise AtomicAcquisitionError('offline initialization staging is ambiguous')
    for artifact in _offline_initialization_artifacts(context)[: len(present)]:
        if tree.read_bytes(
            f'{staging}/{artifact.filename}',
            'offline initialization artifact',
        ) != artifact.raw:
            raise AtomicAcquisitionError('offline initialization artifact drifted')


def _offline_approved_bootstrap(
    config: AtomicRunConfig,
    key_bytes: bytes,
    semantic_options: dict[str, Any],
    run_controls: dict[str, Any],
    *,
    context: _OfflineApprovedContext,
    fault: Callable[[str], None] | None = None,
    return_row_io: bool = False,
) -> (
    tuple[Path, AtomicCandidateUniverse, AtomicSchemaInfo, InitializationClosure]
    | tuple[
        Path,
        AtomicCandidateUniverse,
        AtomicSchemaInfo,
        InitializationClosure,
        PortableDurableRowIo,
    ]
):
    if (
        semantic_options != context.semantic_options
        or run_controls != context.run_controls
        or hmac_key_id(_validate_hmac_key(key_bytes))
        != context.initialization.artifact(RUN_OWNER_FILENAME).payload['hmac']['key_id']
    ):
        raise AtomicAcquisitionError('offline bootstrap authority changed before use')
    output_root = Path(config.output_root).expanduser().absolute()
    final_name = _bootstrap_basename(config.run_id, 'run ID')
    final = output_root / final_name
    staging_name, journal_name = _offline_bootstrap_names(config.run_id)
    expected_journal = _offline_bootstrap_journal_payload(config, context)
    expected_journal_raw = _canonical_json_bytes(expected_journal)
    output_tree = PortableDurableRowIo(output_root)
    row_tree: PortableDurableRowIo | None = None
    transfer_row_tree = False
    try:
        if output_tree.exists(journal_name):
            actual_journal_raw = output_tree.read_bytes(
                journal_name,
                'offline bootstrap journal',
            )
            if len(actual_journal_raw) > MAX_OFFLINE_APPROVAL_JOURNAL_BYTES:
                raise AtomicAcquisitionError(
                    'offline bootstrap journal exceeds its size bound'
                )
            try:
                actual_journal = json.loads(actual_journal_raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AtomicAcquisitionError(
                    'offline bootstrap journal is unreadable'
                ) from exc
            if (
                type(actual_journal) is not dict
                or _canonical_json_bytes(actual_journal) != actual_journal_raw
                or actual_journal != expected_journal
                or actual_journal_raw != expected_journal_raw
            ):
                raise AtomicAcquisitionError(
                    'offline bootstrap journal binding drifted'
                )
        else:
            if output_tree.exists(final_name) or output_tree.exists(staging_name):
                raise AtomicAcquisitionError(
                    'offline bootstrap state lacks its approval journal'
                )
            output_tree.write_bytes(
                journal_name,
                expected_journal_raw,
                expected_existing=None,
                label='offline bootstrap journal',
            )
        _fault_boundary(fault, 'journal_closed')

        if output_tree.exists(final_name):
            if output_tree.exists(staging_name):
                raise AtomicAcquisitionError(
                    'offline bootstrap has both staging and final trees'
                )
            row_tree = PortableDurableRowIo.open_child(
                output_tree,
                final_name,
            )
            _verify_offline_initialization(row_tree, context)
        else:
            if output_tree.exists(staging_name):
                output_tree.list_directory(staging_name)
            else:
                output_tree.ensure_directory(staging_name)
            _fault_boundary(fault, 'staging_created')
            _validate_offline_staging_prefix(output_tree, staging_name, context)

            temporary = f'{staging_name}/.{SNAPSHOT_FILENAME}.copying'
            snapshot = f'{staging_name}/{SNAPSHOT_FILENAME}'
            _copy_offline_snapshot_resumable(
                output_tree,
                context.approved_snapshot,
                temporary,
                snapshot,
                context.snapshot_metadata,
            )
            _fault_boundary(fault, 'snapshot_published')
            _validate_offline_staging_prefix(output_tree, staging_name, context)
            for artifact in _offline_initialization_artifacts(context):
                relative = f'{staging_name}/{artifact.filename}'
                if output_tree.exists(relative):
                    if output_tree.read_bytes(
                        relative,
                        'offline initialization artifact',
                    ) != artifact.raw:
                        raise AtomicAcquisitionError(
                            'offline initialization artifact drifted'
                        )
                else:
                    output_tree.write_bytes(
                        relative,
                        artifact.raw,
                        expected_existing=None,
                        label='offline initialization artifact',
                    )
                _fault_boundary(fault, f'initialization:{artifact.filename}')
            _validate_offline_staging_prefix(output_tree, staging_name, context)
            staging_tree = PortableDurableRowIo.open_child(
                output_tree,
                staging_name,
            )
            try:
                _verify_offline_initialization(staging_tree, context)
            finally:
                staging_tree.close()
            evidence = {
                SNAPSHOT_FILENAME: (
                    context.snapshot_metadata.file_sha256,
                    context.snapshot_metadata.byte_size,
                ),
                **{
                    artifact.filename: (artifact.digest, len(artifact.raw))
                    for artifact in _offline_initialization_artifacts(context)
                },
            }
            output_tree.commit_directory_evidence(
                staging_name,
                final_name,
                expected_files=evidence,
            )
            _fault_boundary(fault, 'promoted')
            row_tree = PortableDurableRowIo.open_child(
                output_tree,
                final_name,
            )
            _verify_offline_initialization(row_tree, context)

        result = (
            final,
            context.universe,
            context.schema_info,
            context.initialization,
        )
        if return_row_io:
            transfer_row_tree = True
            assert row_tree is not None
            return (*result, row_tree)
        return result
    finally:
        if row_tree is not None and not transfer_row_tree:
            row_tree.close()
        output_tree.close()


def _private_root_path(path: Path) -> Path:
    absolute = Path(path).expanduser().absolute()
    indices = [
        index
        for index, component in enumerate(absolute.parts)
        if component == PRIVATE_ROOT_COMPONENT
    ]
    if not indices:
        raise AtomicAcquisitionError("path is outside the required private root")
    return Path(*absolute.parts[: indices[-1] + 1])


def _same_pinned_private_root(first: Path, second: Path) -> Path:
    first_root = _private_root_path(first)
    second_root = _private_root_path(second)
    if first_root != second_root:
        raise AtomicAcquisitionError("live smoke paths do not share one private root")
    first_fd, _ = _open_private_parent_dirfd(first_root / ".private-root-anchor")
    second_fd, _ = _open_private_parent_dirfd(second_root / ".private-root-anchor")
    try:
        if _device_inode(os.fstat(first_fd)) != _device_inode(os.fstat(second_fd)):
            raise AtomicAcquisitionError("live smoke private-root inode drifted")
    finally:
        os.close(first_fd)
        os.close(second_fd)
    return first_root


def _require_receipt_location_outside_run_or_repo(path: Path, private_root: Path) -> None:
    parent = Path(path).expanduser().absolute().parent
    try:
        parent.relative_to(private_root)
    except ValueError as exc:
        raise AtomicAcquisitionError("live smoke receipt escapes the private root") from exc
    private_parent_fd, _ = _open_private_parent_dirfd(
        parent / ".live-smoke-parent-anchor"
    )
    os.close(private_parent_fd)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(parent.parts[0], flags)
        descriptors.append(current)
        for part in parent.parts[1:]:
            following = os.open(part, flags, dir_fd=current)
            if not stat.S_ISDIR(os.fstat(following).st_mode):
                os.close(following)
                raise AtomicAcquisitionError(
                    "live smoke receipt ancestry is not a directory"
                )
            descriptors.append(following)
            current = following
        for descriptor in descriptors:
            for marker in (RUN_OWNER_FILENAME, ".git"):
                try:
                    os.stat(marker, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise AtomicAcquisitionError(
                        "cannot inspect live smoke receipt ancestry"
                    ) from exc
                raise AtomicAcquisitionError(
                    "live smoke receipt must be outside every run and repository subtree"
                )
    except AtomicAcquisitionError:
        raise
    except OSError as exc:
        raise AtomicAcquisitionError(
            "cannot pin live smoke receipt ancestry"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _validated_live_smoke_receipt_payload(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "schema", "smoke_policy_digest", "approved_run_receipt_sha256",
        "retained_rows", "approved_by", "confirmed_at",
    }:
        raise BootstrapStateError("live smoke receipt schema is invalid")
    confirmed_at = value.get("confirmed_at")
    try:
        parsed = _dt.datetime.fromisoformat(confirmed_at)
    except (TypeError, ValueError) as exc:
        raise BootstrapStateError("live smoke receipt time is invalid") from exc
    if (
        value.get("schema") != "setec-imessage-atomic-live-smoke-receipt/1"
        or not _is_sha256_tag(value.get("smoke_policy_digest"))
        or not _is_sha256_tag(value.get("approved_run_receipt_sha256"))
        or value.get("retained_rows") != 1
        or value.get("approved_by") != "owner-tty"
        or parsed.tzinfo != _dt.timezone.utc
        or parsed.isoformat(timespec="seconds") != confirmed_at
    ):
        raise BootstrapStateError("live smoke receipt binding is invalid")
    return json.loads(_canonical_json_bytes(value))


def _read_live_smoke_receipt_private(
    path: Path,
    *,
    anchor_run_dir: Path,
) -> tuple[dict[str, Any], bytes]:
    receipt_path = Path(path).expanduser().absolute()
    if receipt_path.name != "imessage-atomic-live-smoke-receipt.json":
        raise AtomicAcquisitionError("live smoke receipt path is not exact")
    private_root = _same_pinned_private_root(receipt_path, anchor_run_dir)
    _require_receipt_location_outside_run_or_repo(receipt_path, private_root)
    parent_fd, name = _open_private_parent_dirfd(receipt_path)
    try:
        payload, _identity, _digest, raw = _read_private_canonical_json_at(
            parent_fd,
            name,
            max_bytes=MAX_LIVE_SMOKE_RECEIPT_BYTES,
            validator=_validated_live_smoke_receipt_payload,
            artifact_label="live smoke receipt",
        )
        return payload, raw
    finally:
        os.close(parent_fd)


def _consume_live_smoke_receipt(
    path: Path,
    expected_digest: str,
    *,
    run_dir: Path,
) -> None:
    payload, _ = _read_live_smoke_receipt_private(path, anchor_run_dir=run_dir)
    if payload["smoke_policy_digest"] != expected_digest:
        raise AtomicAcquisitionError(
            "live smoke receipt does not authorize this source policy"
        )


def mint_live_smoke_receipt(run_receipt_path: Path, output_path: Path) -> dict[str, Any]:
    """Mint the sole human boundary; this function never acquires or rewrites a run."""

    receipt_path = Path(run_receipt_path).expanduser().absolute()
    if receipt_path.name != "acquisition-receipt.json":
        raise AtomicAcquisitionError("smoke approval requires the exact acquisition receipt path")
    run_dir = receipt_path.parent
    destination = Path(output_path).expanduser().absolute()
    if destination.name != "imessage-atomic-live-smoke-receipt.json":
        raise AtomicAcquisitionError("live smoke receipt output path is not exact")
    private_root = _same_pinned_private_root(receipt_path, destination)
    _require_receipt_location_outside_run_or_repo(destination, private_root)
    reader = _PrivateReadOnlyRowIo(run_dir)
    destination_parent_fd: int | None = None
    try:
        validate_atomic_run(run_dir, io=reader)
        receipt, receipt_raw = _read_io_object(
            reader, "acquisition-receipt.json", "smoke acquisition receipt"
        )
        controls, _ = _read_io_object(
            reader,
            RUN_CONTROLS_FILENAME,
            "run controls",
            validator=_validated_run_controls,
            max_bytes=MAX_RUN_CONTROLS_BYTES,
        )
        if (
            controls.get("max_retained") != 1
            or controls.get("allow_empty") is not False
            or receipt.get("counts", {}).get("retained") != 1
        ):
            raise AtomicAcquisitionError(
                "only a nonempty one-row smoke run can be approved"
            )
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise AtomicAcquisitionError(
                "live smoke approval requires an interactive TTY"
            )
        destination_parent_fd, destination_name = _open_private_parent_dirfd(
            destination
        )
        try:
            os.stat(
                destination_name,
                dir_fd=destination_parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise AtomicAcquisitionError("cannot publish new atomic artifact")
        phrase = "APPROVE IMESSAGE ATOMIC LIVE SMOKE"
        print(f"Type {phrase} to approve the inspected one-row smoke run:", flush=True)
        if input() != phrase:
            raise AtomicAcquisitionError("live smoke approval phrase did not match")
        validate_atomic_run(run_dir, io=reader)
        final_receipt, final_receipt_raw = _read_io_object(
            reader, "acquisition-receipt.json", "smoke acquisition receipt"
        )
        final_controls, _ = _read_io_object(
            reader,
            RUN_CONTROLS_FILENAME,
            "run controls",
            validator=_validated_run_controls,
            max_bytes=MAX_RUN_CONTROLS_BYTES,
        )
        if (
            final_receipt != receipt
            or final_receipt_raw != receipt_raw
            or final_controls != controls
        ):
            raise AtomicAcquisitionError(
                "smoke run changed during owner approval"
            )
        payload = {
            "schema": "setec-imessage-atomic-live-smoke-receipt/1",
            "smoke_policy_digest": receipt["smoke_policy_digest"],
            "approved_run_receipt_sha256": _sha256_tag(receipt_raw),
            "retained_rows": 1,
            "approved_by": "owner-tty",
            "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        _write_private_canonical_json_at(
            destination_parent_fd,
            destination_name,
            payload,
            max_bytes=MAX_LIVE_SMOKE_RECEIPT_BYTES,
            validator=_validated_live_smoke_receipt_payload,
            artifact_label="live smoke receipt",
            replace_existing=False,
            expected_existing_digest=None,
        )
    finally:
        try:
            if destination_parent_fd is not None:
                os.close(destination_parent_fd)
        finally:
            reader.close()
    return payload


def _prepare_live_bootstrap_for_run(
    config: AtomicRunConfig,
    key: bytes,
    semantic: dict[str, Any],
    controls: dict[str, Any],
) -> tuple[
    Path,
    AtomicCandidateUniverse,
    AtomicSchemaInfo,
    InitializationClosure,
    tuple[int, str, int, str, int, str],
]:
    """Acquire the live bootstrap descriptors or close every acquired handle."""

    output_root = config.output_root.absolute()
    final_name = _bootstrap_basename(config.run_id, "run ID")
    journal_name = bootstrap_journal_name(final_name)
    parent_fd, _ = _open_private_parent_dirfd(output_root / journal_name)
    final_fd: int | None = None
    try:
        lock_fd, lock_name = _acquire_bootstrap_lock_at(parent_fd, journal_name)
    except BaseException:
        os.close(parent_fd)
        raise
    try:
        reserved = bootstrap_journal_payload(
            state="reserved", previous_journal_digest=None,
            staging_name=bootstrap_staging_name(final_name), final_name=final_name,
            semantic_options_digest=canonical_payload_digest(semantic),
            run_controls_digest=canonical_payload_digest(controls),
            smoke_policy_digest=None, hmac_key_id_value=hmac_key_id(key),
            snapshot_metadata=None, universe_binding=None, completed_artifacts={},
        )
        try:
            os.stat(journal_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            _advance_bootstrap_journal_locked_at(
                parent_fd, journal_name, reserved,
                lock_fd=lock_fd, lock_name=lock_name,
            )
        prepared = _prepare_or_resume_bootstrap_promoted_locked_at(
            parent_fd, journal_name, reserved,
            output_root / bootstrap_staging_name(final_name), config.source_db,
            lock_fd=lock_fd, lock_name=lock_name, key_bytes=key,
            semantic_options=semantic, run_controls=controls,
        )
        final_fd = prepared.final_fd
        run_dir = output_root / final_name
        universe = prepared.evidence.universe
        schema = prepared.evidence.schema_info
        initialization = prepared.evidence.initialization
        return (
            run_dir,
            universe,
            schema,
            initialization,
            (parent_fd, journal_name, lock_fd, lock_name, final_fd, final_name),
        )
    except BaseException:
        if final_fd is not None:
            try:
                os.close(final_fd)
            except OSError:
                pass
        try:
            _release_bootstrap_lock_at(
                parent_fd, journal_name, lock_fd, lock_name,
            )
        finally:
            try:
                os.close(lock_fd)
            finally:
                os.close(parent_fd)
        raise


def _close_live_row_handles(
    cleanup: tuple[int, str, int, str, int, str]
) -> None:
    parent_fd, journal_name, lock_fd, lock_name, final_fd, _final_name = cleanup
    close_error: BaseException | None = None
    try:
        os.close(final_fd)
    except BaseException as exc:
        close_error = exc
    try:
        _release_bootstrap_lock_at(parent_fd, journal_name, lock_fd, lock_name)
    except BaseException as exc:
        if close_error is None:
            close_error = exc
    for descriptor in (lock_fd, parent_fd):
        try:
            os.close(descriptor)
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    if close_error is not None:
        raise close_error


def run(
    config: AtomicRunConfig,
    *,
    key_bytes: bytes | None = None,
    bootstrap: Callable[[AtomicRunConfig, bytes, dict[str, Any], dict[str, Any]], tuple[Path, AtomicCandidateUniverse, AtomicSchemaInfo, InitializationClosure]] | None = None,
    row_fault: Callable[[str], None] | None = None,
    preprocessor: Callable[[str], tuple[str, dict[str, Any]]] = _default_preprocessor,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Acquire one atomic run, using the journaled macOS bootstrap by default."""

    if type(config) is not AtomicRunConfig:
        raise AtomicAcquisitionError("atomic run config is invalid")
    key = _validate_hmac_key(key_bytes) if key_bytes is not None else None
    if key is None:
        raise HmacKeyError("run requires explicit HMAC key bytes")
    semantic = semantic_options_payload(
        since=config.since, until=config.until,
        include_group_chats=config.include_group_chats,
        apple_date_unit=config.apple_date_unit, timezone_name=config.timezone_name,
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona=config.persona, author=config.author, register=config.register,
    )
    controls = run_controls_payload(
        max_messages=config.max_messages, max_retained=config.max_retained,
        allow_empty=config.allow_empty,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    if type(config.progress_interval) is not int or config.progress_interval < 1:
        raise AtomicAcquisitionError(
            "progress interval must be a positive exact integer"
        )
    if bootstrap is not None:
        bootstrap_result = bootstrap(config, key, semantic, controls)
        cleanup = None
        if len(bootstrap_result) == 5:
            run_dir, universe, schema, initialization, row_io = bootstrap_result
            if type(row_io) is not PortableDurableRowIo:
                raise AtomicAcquisitionError(
                    'portable bootstrap returned invalid row authority'
                )
        else:
            if bootstrap is not _synthetic_fixture_bootstrap:
                raise AtomicAcquisitionError(
                    'path-based bootstrap is authorized only for synthetic fixtures'
                )
            run_dir, universe, schema, initialization = bootstrap_result
            row_io = _SyntheticFixtureRowIo(run_dir)
    else:
        if sys.platform != "darwin":
            raise AtomicAcquisitionError(
                "live iMessage acquisition is available only on the macOS host"
            )
        run_dir, universe, schema, initialization, cleanup = (
            _prepare_live_bootstrap_for_run(config, key, semantic, controls)
        )
        parent_fd, journal_name, lock_fd, lock_name, final_fd, final_name = cleanup
        try:
            row_io = LiveDurableRowIo(
                run_dir,
                final_fd=final_fd,
                parent_fd=parent_fd,
                final_name=final_name,
                journal_name=journal_name,
                lock_fd=lock_fd,
                lock_name=lock_name,
            )
        except BaseException:
            try:
                _close_live_row_handles(cleanup)
            except BaseException:
                pass
            raise
    try:
        owner = initialization.artifact(RUN_OWNER_FILENAME).payload
        if bootstrap is None and config.max_retained != 1:
            if config.live_smoke_receipt is None:
                raise AtomicAcquisitionError("non-smoke run requires a live smoke receipt")
            _consume_live_smoke_receipt(
                config.live_smoke_receipt,
                owner["smoke_policy_digest"],
                run_dir=run_dir,
            )
        result = process_selected_candidates(
            universe, schema, include_group_chats=config.include_group_chats,
            max_retained=config.max_retained, preprocessor=preprocessor,
            progress=progress, progress_interval=config.progress_interval,
        )
        if result.retained_rows == 0 and not config.allow_empty:
            raise AtomicAcquisitionError("atomic acquisition retained zero rows")
        planned = plan_row_artifacts(result, universe, initialization, semantic, key)
        receipt = publish_planned_rows(
            run_dir,
            planned,
            result,
            fault=row_fault,
            io=row_io,
        )
        summary = validate_atomic_run(run_dir, io=row_io)
        if progress is not None:
            progress(dict(summary))
        return receipt
    finally:
        try:
            if isinstance(row_io, LiveDurableRowIo):
                row_io.close()
        finally:
            if cleanup is not None:
                _close_live_row_handles(cleanup)


def run_offline_approved(
    config: AtomicRunConfig,
    authorization: OfflineApprovedImport,
    *,
    key_bytes: bytes | None = None,
    preprocessor: Callable[[str], tuple[str, dict[str, Any]]] = _default_preprocessor,
    progress: Callable[[dict[str, Any]], None] | None = None,
    publication_fault: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one receipt-bound portable import without invoking live acquisition."""

    if type(config) is not AtomicRunConfig:
        raise AtomicAcquisitionError('atomic run config is invalid')
    key = _validate_hmac_key(key_bytes) if key_bytes is not None else None
    if key is None:
        raise HmacKeyError('offline run requires explicit HMAC key bytes')
    semantic = semantic_options_payload(
        since=config.since,
        until=config.until,
        include_group_chats=config.include_group_chats,
        apple_date_unit=config.apple_date_unit,
        timezone_name=config.timezone_name,
        preprocessing_version='legacy-preprocess/1',
        preprocessing_rules_id='imessage-atomic-rules/1',
        persona=config.persona,
        author=config.author,
        register=config.register,
    )
    controls = run_controls_payload(
        max_messages=config.max_messages,
        max_retained=config.max_retained,
        allow_empty=config.allow_empty,
        checkpoint_schema='setec-imessage-atomic-checkpoint/2',
        checkpoint_interval=1,
    )
    context = _authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=key,
        semantic_options=semantic,
        run_controls=controls,
    )

    def approved_bootstrap(
        inner_config: AtomicRunConfig,
        inner_key: bytes,
        inner_semantic: dict[str, Any],
        inner_controls: dict[str, Any],
    ) -> tuple[
        Path,
        AtomicCandidateUniverse,
        AtomicSchemaInfo,
        InitializationClosure,
        PortableDurableRowIo,
    ]:
        return _offline_approved_bootstrap(
            inner_config,
            inner_key,
            inner_semantic,
            inner_controls,
            context=context,
            return_row_io=True,
        )

    return run(
        config,
        key_bytes=key,
        bootstrap=approved_bootstrap,
        row_fault=publication_fault,
        preprocessor=preprocessor,
        progress=progress,
    )


def snapshot_metadata_payload(metadata: SnapshotMetadata) -> dict[str, object]:
    """Return canonical path-free metadata for a later bootstrap journal."""

    return asdict(metadata)


def _timezone_argument(value: str) -> str:
    try:
        _load_explicit_zone(value)
    except ExplicitTimezoneError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the fail-closed parser for atomic acquisition and validation."""

    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire, validate, or approve private atomic sent-iMessage artifacts."
        ),
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--validate-run",
        type=Path,
        help="Validate one completed private atomic run and exit.",
    )
    actions.add_argument(
        "--mint-live-smoke-receipt",
        action="store_true",
        help="Approve a validated one-row smoke run and exit.",
    )
    actions.add_argument(
        '--offline-approved-import',
        action='store_true',
        help='Import a universe-equal copied archive under an existing live approval.',
    )
    group_policy = parser.add_mutually_exclusive_group()
    group_policy.add_argument(
        "--include-group-chats",
        dest="include_group_chats",
        action="store_true",
        help="Include rows classified as group chat.",
    )
    group_policy.add_argument(
        "--exclude-group-chats",
        dest="include_group_chats",
        action="store_false",
        help="Exclude rows classified as group chat.",
    )
    parser.set_defaults(include_group_chats=None)
    parser.add_argument(
        "--timezone",
        type=_timezone_argument,
        help="Required explicit IANA timezone used for local-date derivation.",
    )
    parser.add_argument(
        "--apple-date-unit",
        choices=("seconds", "nanoseconds"),
        help="Exact unit of message.date values; auto-detection is forbidden.",
    )
    parser.add_argument(
        "--hmac-key",
        type=Path,
        help="Existing owner-only persistent HMAC key path.",
    )
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--persona")
    parser.add_argument("--author")
    parser.add_argument("--register")
    parser.add_argument("--since", type=_date_argument)
    parser.add_argument("--until", type=_date_argument)
    parser.add_argument("--max-messages", type=int, default=250_000)
    parser.add_argument("--max-retained", type=int)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=100,
        help="Emit aggregate-only progress after this many considered rows.",
    )
    parser.add_argument("--live-smoke-receipt", type=Path)
    parser.add_argument('--approved-smoke-run', type=Path)
    parser.add_argument('--archive-equivalence-db', type=Path)
    parser.add_argument("--smoke-run-receipt", type=Path)
    parser.add_argument("--receipt-out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch portable validation, TTY approval, or live macOS acquisition."""

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.validate_run is not None:
        summary = validate_atomic_run(args.validate_run)
        print(json.dumps(summary, sort_keys=True))
        return 0
    if args.mint_live_smoke_receipt:
        if args.smoke_run_receipt is None or args.receipt_out is None:
            parser.error("minting requires --smoke-run-receipt and --receipt-out")
        mint_live_smoke_receipt(args.smoke_run_receipt, args.receipt_out)
        return 0
    if args.offline_approved_import and args.source_db is not None:
        parser.error('offline approved import uses --archive-equivalence-db, not --source-db')
    required = {
        "--include-group-chats/--exclude-group-chats": args.include_group_chats,
        "--timezone": args.timezone,
        "--apple-date-unit": args.apple_date_unit,
        "--hmac-key": args.hmac_key,
        "--output-root": args.output_root,
        "--run-id": args.run_id,
        "--persona": args.persona,
        "--author": args.author,
        "--register": args.register,
    }
    if args.offline_approved_import:
        required.update({
            '--approved-smoke-run': args.approved_smoke_run,
            '--archive-equivalence-db': args.archive_equivalence_db,
            '--live-smoke-receipt': args.live_smoke_receipt,
        })
    else:
        required['--source-db'] = args.source_db
    missing = [name for name, value in required.items() if value is None]
    if missing:
        action = 'offline approved import' if args.offline_approved_import else 'live acquisition'
        parser.error(action + ' requires ' + ', '.join(missing))
    if args.offline_approved_import:
        key = load_offline_approved_hmac_key(
            args.hmac_key,
            OfflineApprovedImport(
                approved_smoke_run=args.approved_smoke_run,
                live_smoke_receipt=args.live_smoke_receipt,
                archive_equivalence_db=args.archive_equivalence_db,
            ),
        )
    else:
        key = load_hmac_key(args.hmac_key)
    config = AtomicRunConfig(
        source_db=(args.archive_equivalence_db if args.offline_approved_import else args.source_db),
        output_root=args.output_root,
        run_id=args.run_id, persona=args.persona, author=args.author,
        register=args.register, since=args.since, until=args.until,
        include_group_chats=args.include_group_chats,
        apple_date_unit=args.apple_date_unit, timezone_name=args.timezone,
        max_messages=args.max_messages, max_retained=args.max_retained,
        allow_empty=args.allow_empty,
        progress_interval=args.progress_interval,
        live_smoke_receipt=args.live_smoke_receipt,
    )
    progress = lambda summary: print(json.dumps(summary, sort_keys=True))
    if args.offline_approved_import:
        run_offline_approved(
            config,
            OfflineApprovedImport(
                approved_smoke_run=args.approved_smoke_run,
                live_smoke_receipt=args.live_smoke_receipt,
                archive_equivalence_db=args.archive_equivalence_db,
            ),
            key_bytes=key,
            progress=progress,
        )
    else:
        run(config, key_bytes=key, progress=progress)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
