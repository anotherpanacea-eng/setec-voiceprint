#!/usr/bin/env python3
"""Build private Stage-A passage-remediation decisions from a bound inventory.

The output is a Stage-A-only decision artifact.  It does not read corpus text,
rerun duplicate detection, resolve Stage B, activate a consumer, or authorize
training or pairing.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Mapping, Sequence

from reconstructibility_probe_set import (
    ProbeSetError as _PortablePathError,
    portable_collision_key,
    portable_private_relative_path_v1,
)


TASK_SURFACE = "voice_coherence_acquisition"
SCRIPT_VERSION = "1.0.0"

DESCRIPTOR_SCHEMA = "setec-passage-remediation-descriptor/1"
DECISION_SCHEMA = "setec-passage-remediation-decision/1"
ARTIFACT_SCHEMA = "setec-passage-remediation-decisions/1"
RECEIPT_SCHEMA = "setec-passage-remediation-receipt/1"
ERROR_SCHEMA = "setec-passage-remediation-error/1"
POLICY = "stage-a-retain-one-loss-bearing-representative-v1"

MAX_DESCRIPTOR_BYTES = 65_536
MAX_INVENTORY_BYTES = 67_108_864
MAX_PROJECTION_RECEIPT_BYTES = 16_777_216
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 500_000
MAX_JSON_STRING_BYTES = 1_048_576
MAX_JSON_CONTAINER_ITEMS = 500_000
MAX_CLUSTERS = 50_000
MAX_DECISIONS = 500_000
MAX_STAGE_B_SPANS = 500_000
MAX_OUTPUT_BYTES = 268_435_456
_READ_CHUNK = 1024 * 1024

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PREFIXED_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RESERVED_PASSAGE_SUFFIX = re.compile(r"#p\d+\Z")

DESCRIPTOR_KEYS = {
    "schema",
    "policy",
    "inventory_path",
    "inventory_sha256",
    "projection_receipt_path",
    "projection_receipt_sha256",
    "expected_counts",
}
EXPECTED_COUNT_KEYS = {"passage_clusters", "candidate_drops", "repeated_spans"}
ROOT_KEYS = {
    "mode",
    "stages",
    "source_manifest",
    "n_documents",
    "n_passages",
    "input_rows_skipped",
    "stage_a",
    "stage_b",
    "documents_affected",
    "provenance",
    "assumptions",
    "claim_license",
}
STAGE_A_KEYS = {"run", "clusters", "kept", "dropped", "short_exact_groups"}
STAGE_B_KEYS = {"run", "repeated_spans", "duplicated_regions", "n_below_floor"}
BELOW_FLOOR_KEYS = {"repeated_spans", "duplicated_regions"}
PROVENANCE_KEYS = {"passage_clusters", "repeated_spans", "duplicated_regions"}
CLUSTER_KEYS = {"representative", "dropped", "passages"}
PASSAGE_KEYS = {
    "passage_id",
    "source_doc_id",
    "source_manifest",
    "ordinal",
    "char_start",
    "char_end",
    "n_words",
    "sha256",
}
SPAN_KEYS = {"span_sha256", "n_words", "n_occurrences", "occurrences"}
OCCURRENCE_KEYS = {
    "span_id",
    "source_doc_id",
    "source_manifest",
    "token_start",
    "token_end",
    "char_start",
    "char_end",
    "n_words",
    "sha256",
}
REGION_KEYS = {
    "source_doc_id",
    "source_manifest",
    "token_start",
    "token_end",
    "char_start",
    "char_end",
    "n_words",
}
DECISION_KEYS = {
    "schema",
    "policy",
    "cluster_index",
    "passage_id",
    "source_doc_id",
    "passage_sha256",
    "candidate_role",
    "stage_a_masking_decision",
    "stage_a_loss_excluded",
    "stage_a_pairing_excluded",
    "reason_code",
}
ARTIFACT_KEYS = {
    "schema",
    "policy",
    "inventory_sha256",
    "projection_receipt_sha256",
    "counts",
    "scope",
    "decisions",
}
COUNT_KEYS = {
    "passage_clusters",
    "candidate_drops",
    "repeated_spans_observed",
    "decision_rows",
    "representatives",
    "nonrepresentatives",
    "stage_a_loss_excluded",
    "stage_a_loss_not_excluded",
    "stage_a_pairing_excluded",
    "stage_a_pairing_not_excluded",
}
SCOPE = {
    "coverage": "itemized_stage_a_clusters_only",
    "stage_b_disposition": "unresolved",
    "noncluster_passages": "not_assessed",
    "consumer_authority": "none",
    "calibration_status": "operational_uncalibrated",
}

REFUSAL_CODES = frozenset(
    {
        "private_root_refused",
        "private_path_refused",
        "descriptor_schema_refused",
        "inventory_hash_refused",
        "projection_receipt_hash_refused",
        "inventory_schema_refused",
        "inventory_conservation_refused",
        "decision_invariant_refused",
        "output_exists_refused",
        "output_publication_refused",
        "output_recovery_required",
    }
)

_FAULT_HOOK: Any = None


def _fault(label: str) -> None:
    hook = _FAULT_HOOK
    if hook is not None:
        hook(label)


class RemediationError(Exception):
    """A stable refusal whose text is exactly one closed code."""

    def __init__(self, code: str):
        if code not in REFUSAL_CODES:
            raise ValueError("unknown refusal code")
        super().__init__(code)
        self.code = code


class UsageError(Exception):
    pass


class _RootPolicyError(Exception):
    pass


class _PrivateIOError(Exception):
    pass


class _OversizePrivateIOError(_PrivateIOError):
    pass


class _OutputExists(Exception):
    pass


class _PublicationError(Exception):
    pass


class _RecoveryRequired(Exception):
    pass


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise UsageError()


_STATIC_USAGE = (
    "usage: passage_remediation --private-root PRIVATE_ROOT "
    "--descriptor RELATIVE_DESCRIPTOR.json "
    "--output RELATIVE_DECISIONS.json [--json]\n"
)


def _canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise RemediationError("decision_invariant_refused") from None


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _strict_json_object(raw: bytes, *, maximum: int, code: str) -> dict[str, Any]:
    if len(raw) > maximum:
        raise RemediationError(code)

    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value

    try:
        text = raw.decode("utf-8", "strict")
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        if not isinstance(value, dict):
            raise ValueError("root")
        _walk_json_limits(value)
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError):
        raise RemediationError(code) from None
    return value


def _walk_json_limits(root: Any) -> None:
    stack: list[tuple[Any, int, bool]] = [(root, 0, False)]
    nodes = 0
    while stack:
        value, depth, key_node = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError("json limit")
        if isinstance(value, str):
            if len(value.encode("utf-8", "strict")) > MAX_JSON_STRING_BYTES:
                raise ValueError("string limit")
            if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("surrogate")
        elif key_node:
            raise ValueError("object key")
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("nonfinite")
        elif isinstance(value, dict):
            if len(value) > MAX_JSON_CONTAINER_ITEMS:
                raise ValueError("container limit")
            for key, child in reversed(list(value.items())):
                if not isinstance(key, str):
                    raise ValueError("object key")
                stack.append((child, depth + 1, False))
                stack.append((key, depth + 1, True))
        elif isinstance(value, list):
            if len(value) > MAX_JSON_CONTAINER_ITEMS:
                raise ValueError("container limit")
            for child in reversed(value):
                stack.append((child, depth + 1, False))
        elif value is None or isinstance(value, (bool, int)):
            continue
        else:
            raise ValueError("json type")


def _portable_parts(value: Any) -> tuple[str, ...]:
    try:
        return portable_private_relative_path_v1(value)
    except (_PortablePathError, TypeError, ValueError, UnicodeError):
        raise RemediationError("private_path_refused") from None


def _is_int(value: Any, *, positive: bool = False) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= (1 if positive else 0)
    )


def _valid_opaque(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        raw = value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return (
        len(raw) <= 4096
        and not any(ord(char) < 0x20 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _closed(value: Any, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _posix_identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _posix_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(info.st_nlink),
        int(stat.S_IFMT(info.st_mode)),
    )


def _windows_stable_identity(info: Any) -> tuple[int, int]:
    return int(info.volume_serial), int(info.file_id)


def _optional_flag(name: str) -> int:
    return int(getattr(os, name, 0))


class PinnedPrivateRoot:
    """Retained, revalidated private-root capability."""

    def __init__(self, path: str):
        self.path = Path(os.path.abspath(os.fspath(path)))
        self._fds: list[int] = []
        self._identities: list[tuple[int, int]] = []
        self._winio: Any = None
        self._handles: tuple[int, ...] = ()
        self.open()

    def __enter__(self) -> "PinnedPrivateRoot":
        return self

    def __exit__(self, _type: Any, _value: Any, _tb: Any) -> None:
        self.close()

    def open(self) -> None:
        try:
            if os.name == "nt":  # pragma: no cover - native Windows
                import windows_descriptor_io as winio

                self._winio = winio
                self._handles = tuple(
                    winio.pin_directory_chain(self.path, writable_final=True)
                )
                for handle in self._handles:
                    winio.require_direct(handle, "directory")
                winio.require_owner_private(self._handles[-1], "directory")
                self.barrier()
                return

            if os.name != "posix" or not self.path.is_absolute() or self.path == Path("/"):
                raise OSError("unsupported root")
            flags = (
                os.O_RDONLY
                | _optional_flag("O_DIRECTORY")
                | _optional_flag("O_NOFOLLOW")
                | _optional_flag("O_CLOEXEC")
            )
            current = os.open(self.path.anchor or "/", flags)
            self._fds.append(current)
            root_info = os.fstat(current)
            self._identities.append(_posix_identity(root_info))
            for component in self.path.parts[1:]:
                if component in {"", ".", ".."}:
                    raise OSError("invalid component")
                named = os.stat(component, dir_fd=current, follow_symlinks=False)
                following = os.open(component, flags, dir_fd=current)
                opened = os.fstat(following)
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or not stat.S_ISDIR(opened.st_mode)
                    or _posix_identity(named) != _posix_identity(opened)
                ):
                    os.close(following)
                    raise OSError("identity")
                self._fds.append(following)
                self._identities.append(_posix_identity(opened))
                current = following
            self._require_posix_private()
            self.barrier()
        except (OSError, TypeError, ValueError, ImportError):
            self.close()
            raise _RootPolicyError() from None

    @property
    def root_descriptor(self) -> int:
        return self._handles[-1] if os.name == "nt" else self._fds[-1]

    def _require_posix_private(self) -> None:
        info = os.fstat(self._fds[-1])
        if (
            not stat.S_ISDIR(info.st_mode)
            or int(info.st_uid) != int(os.geteuid())
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise OSError("private policy")

    def barrier(self) -> None:
        try:
            if os.name == "nt":  # pragma: no cover - native Windows
                self._winio.revalidate_directory_chain(self.path, self._handles)
                for handle in self._handles:
                    self._winio.require_direct(handle, "directory")
                self._winio.require_owner_private(self._handles[-1], "directory")
                return
            if len(self._fds) != len(self._identities):
                raise OSError("chain")
            if _posix_identity(os.fstat(self._fds[0])) != self._identities[0]:
                raise OSError("anchor")
            for index, component in enumerate(self.path.parts[1:]):
                named = os.stat(
                    component,
                    dir_fd=self._fds[index],
                    follow_symlinks=False,
                )
                opened = os.fstat(self._fds[index + 1])
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or _posix_identity(named) != self._identities[index + 1]
                    or _posix_identity(opened) != self._identities[index + 1]
                ):
                    raise OSError("chain")
            self._require_posix_private()
        except (OSError, TypeError, ValueError):
            raise _RootPolicyError() from None

    def close(self) -> None:
        if self._handles:
            for handle in reversed(self._handles):
                try:
                    self._winio.close(handle)
                except (OSError, MemoryError):
                    pass
            self._handles = ()
        for descriptor in reversed(self._fds):
            try:
                os.close(descriptor)
            except (OSError, MemoryError):
                pass
        self._fds = []

    def read_private_member(
        self, parts: Sequence[str], maximum: int
    ) -> bytes:
        self.barrier()
        try:
            if os.name == "nt":  # pragma: no cover - native Windows
                raw = self._read_windows(parts, maximum)
            else:
                raw = self._read_posix(parts, maximum)
            self.barrier()
            return raw
        except _RootPolicyError:
            raise
        except _OversizePrivateIOError:
            raise
        except (OSError, TypeError, ValueError, MemoryError):
            raise _PrivateIOError() from None

    def _read_posix(self, parts: Sequence[str], maximum: int) -> bytes:
        if not parts:
            raise OSError("empty path")
        current = os.dup(self._fds[-1])
        descriptor = -1
        try:
            dir_flags = (
                os.O_RDONLY
                | _optional_flag("O_DIRECTORY")
                | _optional_flag("O_NOFOLLOW")
                | _optional_flag("O_CLOEXEC")
            )
            for component in parts[:-1]:
                named = os.stat(component, dir_fd=current, follow_symlinks=False)
                following = os.open(component, dir_flags, dir_fd=current)
                opened = os.fstat(following)
                if (
                    not stat.S_ISDIR(named.st_mode)
                    or _posix_identity(named) != _posix_identity(opened)
                ):
                    os.close(following)
                    raise OSError("directory")
                os.close(current)
                current = following
            leaf = parts[-1]
            before = os.stat(leaf, dir_fd=current, follow_symlinks=False)
            if before.st_size < 0 or before.st_size > maximum:
                raise _OversizePrivateIOError()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise OSError("file")
            flags = (
                os.O_RDONLY
                | _optional_flag("O_NOFOLLOW")
                | _optional_flag("O_CLOEXEC")
            )
            descriptor = os.open(leaf, flags, dir_fd=current)
            opened = os.fstat(descriptor)
            if _posix_fingerprint(before) != _posix_fingerprint(opened):
                raise OSError("identity")
            chunks: list[bytes] = []
            remaining = int(opened.st_size)
            while remaining:
                chunk = os.read(descriptor, min(_READ_CHUNK, remaining))
                if not chunk:
                    raise OSError("short read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise _OversizePrivateIOError()
            after = os.fstat(descriptor)
            named_after = os.stat(leaf, dir_fd=current, follow_symlinks=False)
            if (
                _posix_fingerprint(opened) != _posix_fingerprint(after)
                or _posix_fingerprint(after) != _posix_fingerprint(named_after)
            ):
                raise OSError("mutation")
            return b"".join(chunks)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(current)

    def _read_windows(self, parts: Sequence[str], maximum: int) -> bytes:  # pragma: no cover
        if not parts:
            raise OSError("empty path")
        winio = self._winio
        current = winio.duplicate(self._handles[-1])
        handle = verify = 0
        try:
            for component in parts[:-1]:
                following = winio.open_directory(current, component)
                winio.close(current)
                current = following
            handle = winio.open_file(
                current,
                parts[-1],
                share_write=False,
                share_delete=False,
                allow_multiple_links=False,
            )
            before = winio.require_direct(handle, "file", allow_multiple_links=False)
            if before.size < 0 or before.size > maximum:
                raise _OversizePrivateIOError()
            chunks: list[bytes] = []
            remaining = before.size
            while remaining:
                chunk = winio.read(handle, min(_READ_CHUNK, remaining))
                if not chunk:
                    raise OSError("short read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if winio.read(handle, 1):
                raise _OversizePrivateIOError()
            after = winio.require_direct(handle, "file", allow_multiple_links=False)
            verify = winio.open_file(
                current,
                parts[-1],
                share_write=False,
                share_delete=False,
                allow_multiple_links=False,
            )
            named = winio.require_direct(verify, "file", allow_multiple_links=False)
            if before.identity != after.identity or before.identity != named.identity:
                raise OSError("identity")
            return b"".join(chunks)
        finally:
            for item in (verify, handle, current):
                if item:
                    winio.close(item)

    def publish(self, name: str, payload: bytes) -> None:
        self.barrier()
        try:
            if os.name == "nt":  # pragma: no cover - native Windows
                self._publish_windows(name, payload)
            else:
                self._publish_posix(name, payload)
        except (_OutputExists, _PublicationError, _RecoveryRequired):
            raise
        except _RootPolicyError:
            raise
        except (OSError, TypeError, ValueError, MemoryError):
            raise _PublicationError() from None

    def _publish_posix(self, name: str, payload: bytes) -> None:
        root = self._fds[-1]
        descriptor = -1
        created = False
        identity: tuple[int, int] | None = None
        try:
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | _optional_flag("O_NOFOLLOW")
                | _optional_flag("O_CLOEXEC")
            )
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=root)
            except FileExistsError:
                raise _OutputExists() from None
            except OSError:
                try:
                    os.stat(name, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    raise _PublicationError() from None
                except OSError:
                    raise _RecoveryRequired() from None
                raise _RecoveryRequired() from None
            created = True
            _fault("after_commit")
            os.fchmod(descriptor, 0o600)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("write")
                view = view[written:]
            os.fsync(descriptor)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1
                or opened.st_size != len(payload)
            ):
                raise OSError("created final")
            identity = _posix_identity(opened)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if _read_exact_posix_descriptor(descriptor, len(payload)) != payload:
                raise OSError("created final bytes")
            _fault("after_staging_write")
            self.barrier()
            os.fsync(root)
            _fault("after_parent_fsync")
            _fault("before_reopen_verify")
            verify = os.open(
                name,
                os.O_RDONLY | _optional_flag("O_NOFOLLOW") | _optional_flag("O_CLOEXEC"),
                dir_fd=root,
            )
            try:
                info = os.fstat(verify)
                if (
                    _posix_identity(info) != identity
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size != len(payload)
                    or _read_exact_posix_descriptor(verify, len(payload)) != payload
                ):
                    raise OSError("final")
            finally:
                os.close(verify)
            _fault("after_reopen_verify")
            self.barrier()
            _fault("after_postcleanup_read")
            named_final = os.stat(name, dir_fd=root, follow_symlinks=False)
            controlled_final = os.fstat(descriptor)
            if _posix_fingerprint(named_final) != _posix_fingerprint(
                controlled_final
            ):
                raise OSError("final rebound")
            self.barrier()
        except _OutputExists:
            raise
        except _RecoveryRequired:
            raise
        except Exception as exc:
            if not created:
                if isinstance(exc, _RootPolicyError):
                    raise
                raise _PublicationError() from None
            # POSIX has no descriptor-based unlink primitive. Once O_EXCL has
            # created the final name, any name-based rollback could delete an
            # intervening winner. Leave the create-new result in place and make
            # the operator inspect it before any retry.
            raise _RecoveryRequired() from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _publish_windows(self, name: str, payload: bytes) -> None:  # pragma: no cover
        winio = self._winio
        root = self._handles[-1]
        temp = ".passage-remediation-" + secrets.token_hex(16)
        handle = 0
        committed = False
        identity: tuple[int, int] | None = None
        try:
            if winio.probe_leaf_node(root, name) is not None:
                raise _OutputExists()
            handle = winio.create_owner_private_file(
                root,
                temp,
                share_delete=False,
                share_write=False,
            )
            winio.require_owner_private(handle, "file")
            view = memoryview(payload)
            while view:
                written = winio.write(handle, view)
                if written <= 0:
                    raise OSError("write")
                view = view[written:]
            winio.flush(handle)
            staged = winio.require_owner_private(handle, "file")
            if staged.size != len(payload):
                raise OSError("staging size")
            identity = _windows_stable_identity(staged)
            winio.seek(handle, 0)
            if _read_exact_windows(winio, handle, len(payload)) != payload:
                raise OSError("staging")
            _fault("after_staging_write")
            self.barrier()
            try:
                winio.rename(handle, root, name, replace=False)
            except FileExistsError:
                try:
                    named = winio.probe_leaf_node(root, name)
                except OSError:
                    committed = True
                    raise OSError("ambiguous rename status") from None
                if (
                    named is not None
                    and identity is not None
                    and _windows_stable_identity(named) == identity
                ):
                    committed = True
                    raise OSError("ambiguous committed rename") from None
                raise _OutputExists() from None
            except OSError:
                try:
                    named = winio.probe_leaf_node(root, name)
                except OSError:
                    committed = True
                else:
                    committed = named is not None
                raise
            committed = True
            _fault("after_commit")
            _fault("before_reopen_verify")
            verify = winio.open_file(root, name, allow_multiple_links=False)
            try:
                named = winio.require_owner_private(verify, "file")
                winio.seek(verify, 0)
                if (
                    _windows_stable_identity(named) != identity
                    or named.size != len(payload)
                    or _read_exact_windows(winio, verify, len(payload)) != payload
                ):
                    raise OSError("final")
            finally:
                winio.close(verify)
            _fault("after_reopen_verify")
            self.barrier()
            rebound = winio.probe_leaf_node(root, name)
            if (
                rebound is None
                or identity is None
                or _windows_stable_identity(rebound) != identity
            ):
                raise OSError("post-verify rebound")
        except _OutputExists:
            if handle:
                try:
                    winio.delete(handle)
                except OSError:
                    raise _PublicationError() from None
            raise
        except Exception as exc:
            if not committed:
                if handle:
                    try:
                        winio.delete(handle)
                    except OSError:
                        pass
                if isinstance(exc, _RootPolicyError):
                    raise
                raise _PublicationError() from None
            try:
                winio.delete(handle)
                rebound = winio.probe_leaf_node(root, name)
                if (
                    rebound is not None
                    and identity is not None
                    and _windows_stable_identity(rebound) == identity
                ):
                    raise OSError("rollback")
                if rebound is not None:
                    raise OSError("winner remains")
            except OSError:
                raise _RecoveryRequired() from None
            raise _PublicationError() from None
        finally:
            if handle:
                try:
                    winio.close(handle)
                except OSError:
                    pass


def _read_exact_posix_descriptor(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, min(_READ_CHUNK, remaining))
        if not chunk:
            raise OSError("short read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise OSError("growth")
    return b"".join(chunks)


def _read_exact_windows(winio: Any, handle: int, size: int) -> bytes:  # pragma: no cover
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = winio.read(handle, min(_READ_CHUNK, remaining))
        if not chunk:
            raise OSError("short read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if winio.read(handle, 1):
        raise OSError("growth")
    return b"".join(chunks)


def validate_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    if not _closed(value, DESCRIPTOR_KEYS):
        raise RemediationError("descriptor_schema_refused")
    counts = value.get("expected_counts")
    if (
        value.get("schema") != DESCRIPTOR_SCHEMA
        or value.get("policy") != POLICY
        or not isinstance(value.get("inventory_path"), str)
        or not isinstance(value.get("projection_receipt_path"), str)
        or not isinstance(value.get("inventory_sha256"), str)
        or not _PREFIXED_DIGEST.fullmatch(value["inventory_sha256"])
        or not isinstance(value.get("projection_receipt_sha256"), str)
        or not _PREFIXED_DIGEST.fullmatch(value["projection_receipt_sha256"])
        or not _closed(counts, EXPECTED_COUNT_KEYS)
        or not all(_is_int(counts[key]) for key in EXPECTED_COUNT_KEYS)
    ):
        raise RemediationError("descriptor_schema_refused")
    return value


def _validate_passage_shape(value: Any) -> dict[str, Any]:
    if not _closed(value, PASSAGE_KEYS):
        raise RemediationError("inventory_schema_refused")
    if (
        not _valid_opaque(value.get("passage_id"))
        or not _valid_opaque(value.get("source_doc_id"))
        or not isinstance(value.get("source_manifest"), str)
        or not _is_int(value.get("ordinal"))
        or not _is_int(value.get("char_start"))
        or not _is_int(value.get("char_end"), positive=True)
        or value["char_start"] >= value["char_end"]
        or not _is_int(value.get("n_words"))
        or not isinstance(value.get("sha256"), str)
        or not _HEX64.fullmatch(value["sha256"])
    ):
        raise RemediationError("inventory_schema_refused")
    return value


def _validate_span_shape(value: Any) -> None:
    if not _closed(value, SPAN_KEYS):
        raise RemediationError("inventory_schema_refused")
    occurrences = value.get("occurrences")
    if (
        not isinstance(value.get("span_sha256"), str)
        or not _HEX64.fullmatch(value["span_sha256"])
        or not _is_int(value.get("n_words"), positive=True)
        or not _is_int(value.get("n_occurrences"), positive=True)
        or value["n_occurrences"] < 2
        or not isinstance(occurrences, list)
    ):
        raise RemediationError("inventory_schema_refused")
    for occurrence in occurrences:
        if not _closed(occurrence, OCCURRENCE_KEYS):
            raise RemediationError("inventory_schema_refused")
        if (
            not _valid_opaque(occurrence.get("span_id"))
            or not _valid_opaque(occurrence.get("source_doc_id"))
            or not isinstance(occurrence.get("source_manifest"), str)
            or not _is_int(occurrence.get("token_start"))
            or not _is_int(occurrence.get("token_end"))
            or occurrence["token_start"] > occurrence["token_end"]
            or not _is_int(occurrence.get("char_start"))
            or not _is_int(occurrence.get("char_end"), positive=True)
            or occurrence["char_start"] >= occurrence["char_end"]
            or not _is_int(occurrence.get("n_words"), positive=True)
            or not isinstance(occurrence.get("sha256"), str)
            or not _HEX64.fullmatch(occurrence["sha256"])
        ):
            raise RemediationError("inventory_schema_refused")


def _validate_region_shape(value: Any) -> None:
    if not _closed(value, REGION_KEYS):
        raise RemediationError("inventory_schema_refused")
    if (
        not _valid_opaque(value.get("source_doc_id"))
        or not isinstance(value.get("source_manifest"), str)
        or not _is_int(value.get("token_start"))
        or not _is_int(value.get("token_end"))
        or value["token_start"] > value["token_end"]
        or not _is_int(value.get("char_start"))
        or not _is_int(value.get("char_end"), positive=True)
        or value["char_start"] >= value["char_end"]
        or not _is_int(value.get("n_words"), positive=True)
    ):
        raise RemediationError("inventory_schema_refused")
def validate_inventory(
    value: dict[str, Any],
    expected_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    if not _closed(value, ROOT_KEYS):
        raise RemediationError("inventory_schema_refused")
    stage_a = value.get("stage_a")
    stage_b = value.get("stage_b")
    provenance = value.get("provenance")
    if (
        not _closed(stage_a, STAGE_A_KEYS)
        or not _closed(stage_b, STAGE_B_KEYS)
        or not _closed(provenance, PROVENANCE_KEYS)
        or value.get("mode") != "passages"
        or value.get("stages") != ["a", "b"]
        or value.get("input_rows_skipped") != []
        or stage_a.get("run") is not True
        or stage_b.get("run") is not True
        or not _valid_opaque(value.get("source_manifest"))
        or not _is_int(value.get("n_documents"))
        or not _is_int(value.get("n_passages"))
        or not all(_is_int(stage_a.get(key)) for key in (
            "clusters", "kept", "dropped", "short_exact_groups"
        ))
        or not all(_is_int(stage_b.get(key)) for key in (
            "repeated_spans", "duplicated_regions"
        ))
        or not _closed(stage_b.get("n_below_floor"), BELOW_FLOOR_KEYS)
        or not all(
            _is_int(stage_b["n_below_floor"].get(key))
            for key in BELOW_FLOOR_KEYS
        )
        or not isinstance(value.get("documents_affected"), list)
        or not isinstance(value.get("assumptions"), dict)
        or not isinstance(value.get("claim_license"), dict)
        or value["assumptions"].get("calibration_status")
        != "heuristic / uncalibrated — no bands, no thresholds promoted"
        or not isinstance(provenance.get("passage_clusters"), list)
        or not isinstance(provenance.get("repeated_spans"), list)
        or not isinstance(provenance.get("duplicated_regions"), list)
    ):
        raise RemediationError("inventory_schema_refused")
    if (
        len(provenance["passage_clusters"]) > MAX_CLUSTERS
        or len(provenance["repeated_spans"]) > MAX_STAGE_B_SPANS
    ):
        raise RemediationError("inventory_schema_refused")

    # Complete schema/range pass before any conservation relation. This keeps
    # the closed first-wins refusal order stable for multiply-invalid inputs.
    member_total = 0
    for cluster in provenance["passage_clusters"]:
        if (
            not _closed(cluster, CLUSTER_KEYS)
            or not _valid_opaque(cluster.get("representative"))
            or not isinstance(cluster.get("dropped"), list)
            or not cluster["dropped"]
            or not isinstance(cluster.get("passages"), list)
        ):
            raise RemediationError("inventory_schema_refused")
        members = [cluster["representative"], *cluster["dropped"]]
        if any(not _valid_opaque(member) for member in members):
            raise RemediationError("inventory_schema_refused")
        for passage in cluster["passages"]:
            _validate_passage_shape(passage)
        member_total += len(members)
        if member_total > MAX_DECISIONS:
            raise RemediationError("inventory_schema_refused")
    for span in provenance["repeated_spans"]:
        _validate_span_shape(span)
    for region in provenance["duplicated_regions"]:
        _validate_region_shape(region)

    if (
        stage_a["clusters"] > stage_a["kept"]
        or value["n_passages"] != stage_a["kept"] + stage_a["dropped"]
        or stage_a["clusters"] + stage_a["dropped"] > value["n_passages"]
        or len(provenance["passage_clusters"]) != stage_a["clusters"]
        or len(provenance["repeated_spans"]) != stage_b["repeated_spans"]
        or len(provenance["duplicated_regions"]) != stage_b["duplicated_regions"]
    ):
        raise RemediationError("inventory_conservation_refused")

    source_manifest = value["source_manifest"]
    seen_ids: set[str] = set()
    seen_positions: set[tuple[str, int]] = set()
    clusters: list[dict[str, Any]] = []
    dropped_total = 0
    for cluster in provenance["passage_clusters"]:
        members = [cluster["representative"], *cluster["dropped"]]
        if (
            len(set(members)) != len(members)
            or len(cluster["passages"]) != len(members)
            or any(
                passage["passage_id"] != member
                for passage, member in zip(cluster["passages"], members)
            )
            or seen_ids.intersection(members)
        ):
            raise RemediationError("inventory_conservation_refused")
        validated_passages: list[dict[str, Any]] = []
        for passage, member in zip(cluster["passages"], members):
            derived = f"{passage['source_doc_id']}#p{passage['ordinal']:04d}"
            position = (passage["source_doc_id"], passage["ordinal"])
            if (
                passage["passage_id"] != member
                or passage["source_manifest"] != source_manifest
                or _RESERVED_PASSAGE_SUFFIX.search(passage["source_doc_id"])
                or passage["passage_id"] != derived
                or position in seen_positions
            ):
                raise RemediationError("inventory_conservation_refused")
            seen_positions.add(position)
            validated_passages.append(passage)
        seen_ids.update(members)
        dropped_total += len(cluster["dropped"])
        clusters.append(
            {
                "representative": cluster["representative"],
                "dropped": list(cluster["dropped"]),
                "passages": validated_passages,
            }
        )
    if dropped_total != stage_a["dropped"] or member_total > value["n_passages"]:
        raise RemediationError("inventory_conservation_refused")

    for span in provenance["repeated_spans"]:
        if span["n_occurrences"] != len(span["occurrences"]):
            raise RemediationError("inventory_conservation_refused")
        seen_occurrences: set[tuple[str, int, int]] = set()
        for occurrence in span["occurrences"]:
            expected_span_id = (
                f"{occurrence['source_doc_id']}#t"
                f"{occurrence['token_start']:06d}"
            )
            identity = (
                occurrence["source_doc_id"],
                occurrence["token_start"],
                occurrence["token_end"],
            )
            if (
                occurrence["span_id"] != expected_span_id
                or identity in seen_occurrences
                or occurrence["source_manifest"] != source_manifest
                or occurrence["n_words"] != span["n_words"]
            ):
                raise RemediationError("inventory_conservation_refused")
            seen_occurrences.add(identity)
    if any(
        region["source_manifest"] != source_manifest
        for region in provenance["duplicated_regions"]
    ):
        raise RemediationError("inventory_conservation_refused")

    actual_counts = {
        "passage_clusters": stage_a["clusters"],
        "candidate_drops": stage_a["dropped"],
        "repeated_spans": stage_b["repeated_spans"],
    }
    if dict(expected_counts) != actual_counts:
        raise RemediationError("inventory_conservation_refused")
    return clusters


def derive_cluster_decisions(
    clusters: Sequence[Mapping[str, Any]],
    *,
    inventory_sha256: str,
    projection_receipt_sha256: str,
    repeated_spans: int,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters):
        passages = list(cluster["passages"])
        for member_index, passage in enumerate(passages):
            representative = member_index == 0
            decisions.append(
                {
                    "schema": DECISION_SCHEMA,
                    "policy": POLICY,
                    "cluster_index": cluster_index,
                    "passage_id": passage["passage_id"],
                    "source_doc_id": passage["source_doc_id"],
                    "passage_sha256": passage["sha256"],
                    "candidate_role": (
                        "representative" if representative else "nonrepresentative"
                    ),
                    "stage_a_masking_decision": (
                        "unmasked"
                        if representative
                        else "mask_all_training_targets"
                    ),
                    "stage_a_loss_excluded": not representative,
                    "stage_a_pairing_excluded": not representative,
                    "reason_code": (
                        "single_loss_bearing_representative"
                        if representative
                        else "repeated_passage_nonrepresentative"
                    ),
                }
            )
    representatives = len(clusters)
    nonrepresentatives = len(decisions) - representatives
    counts = {
        "passage_clusters": representatives,
        "candidate_drops": nonrepresentatives,
        "repeated_spans_observed": repeated_spans,
        "decision_rows": len(decisions),
        "representatives": representatives,
        "nonrepresentatives": nonrepresentatives,
        "stage_a_loss_excluded": nonrepresentatives,
        "stage_a_loss_not_excluded": representatives,
        "stage_a_pairing_excluded": nonrepresentatives,
        "stage_a_pairing_not_excluded": representatives,
    }
    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "policy": POLICY,
        "inventory_sha256": inventory_sha256,
        "projection_receipt_sha256": projection_receipt_sha256,
        "counts": counts,
        "scope": dict(SCOPE),
        "decisions": decisions,
    }
    validate_decision_truth_table(artifact, clusters, repeated_spans)
    return artifact


def validate_decision_truth_table(
    artifact: Mapping[str, Any],
    clusters: Sequence[Mapping[str, Any]],
    repeated_spans_observed: int,
) -> None:
    if (
        not isinstance(artifact, dict)
        or set(artifact) != ARTIFACT_KEYS
        or artifact.get("schema") != ARTIFACT_SCHEMA
        or artifact.get("policy") != POLICY
        or artifact.get("scope") != SCOPE
        or not isinstance(artifact.get("counts"), dict)
        or set(artifact["counts"]) != COUNT_KEYS
        or not all(_is_int(value) for value in artifact["counts"].values())
        or not isinstance(artifact.get("decisions"), list)
        or not isinstance(artifact.get("inventory_sha256"), str)
        or not _PREFIXED_DIGEST.fullmatch(artifact["inventory_sha256"])
        or not isinstance(artifact.get("projection_receipt_sha256"), str)
        or not _PREFIXED_DIGEST.fullmatch(artifact["projection_receipt_sha256"])
    ):
        raise RemediationError("decision_invariant_refused")
    expected_rows: list[tuple[int, Mapping[str, Any], bool]] = []
    for cluster_index, cluster in enumerate(clusters):
        for member_index, passage in enumerate(cluster["passages"]):
            expected_rows.append((cluster_index, passage, member_index == 0))
    if len(expected_rows) != len(artifact["decisions"]):
        raise RemediationError("decision_invariant_refused")
    for row, (cluster_index, passage, representative) in zip(
        artifact["decisions"], expected_rows
    ):
        expected = {
            "schema": DECISION_SCHEMA,
            "policy": POLICY,
            "cluster_index": cluster_index,
            "passage_id": passage["passage_id"],
            "source_doc_id": passage["source_doc_id"],
            "passage_sha256": passage["sha256"],
            "candidate_role": (
                "representative" if representative else "nonrepresentative"
            ),
            "stage_a_masking_decision": (
                "unmasked" if representative else "mask_all_training_targets"
            ),
            "stage_a_loss_excluded": not representative,
            "stage_a_pairing_excluded": not representative,
            "reason_code": (
                "single_loss_bearing_representative"
                if representative
                else "repeated_passage_nonrepresentative"
            ),
        }
        if not isinstance(row, dict) or set(row) != DECISION_KEYS or row != expected:
            raise RemediationError("decision_invariant_refused")
    representatives = len(clusters)
    nonrepresentatives = len(expected_rows) - representatives
    expected_counts = {
        "passage_clusters": representatives,
        "candidate_drops": nonrepresentatives,
        "repeated_spans_observed": repeated_spans_observed,
        "decision_rows": len(expected_rows),
        "representatives": representatives,
        "nonrepresentatives": nonrepresentatives,
        "stage_a_loss_excluded": nonrepresentatives,
        "stage_a_loss_not_excluded": representatives,
        "stage_a_pairing_excluded": nonrepresentatives,
        "stage_a_pairing_not_excluded": representatives,
    }
    if artifact["counts"] != expected_counts:
        raise RemediationError("decision_invariant_refused")


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(add_help=False)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--help", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        with PinnedPrivateRoot(args.private_root) as root:
            _fault("after_root_open")
            descriptor_parts = _portable_parts(args.descriptor)
            output_parts = _portable_parts(args.output)
            if len(output_parts) != 1:
                raise RemediationError("private_path_refused")
            if (
                descriptor_parts == output_parts
                or portable_collision_key(descriptor_parts)
                == portable_collision_key(output_parts)
            ):
                raise RemediationError("private_path_refused")
            try:
                descriptor_raw = root.read_private_member(
                    descriptor_parts, MAX_DESCRIPTOR_BYTES
                )
            except _RootPolicyError:
                raise RemediationError("private_root_refused") from None
            except _PrivateIOError:
                raise RemediationError("descriptor_schema_refused") from None
            descriptor = validate_descriptor(
                _strict_json_object(
                    descriptor_raw,
                    maximum=MAX_DESCRIPTOR_BYTES,
                    code="descriptor_schema_refused",
                )
            )
            inventory_parts = _portable_parts(descriptor["inventory_path"])
            projection_parts = _portable_parts(
                descriptor["projection_receipt_path"]
            )
            all_parts = [
                descriptor_parts,
                inventory_parts,
                projection_parts,
                output_parts,
            ]
            if (
                len(set(all_parts)) != len(all_parts)
                or len({portable_collision_key(parts) for parts in all_parts})
                != len(all_parts)
            ):
                raise RemediationError("private_path_refused")
            try:
                inventory_raw = root.read_private_member(
                    inventory_parts, MAX_INVENTORY_BYTES
                )
            except _RootPolicyError:
                raise RemediationError("private_root_refused") from None
            except _OversizePrivateIOError:
                raise RemediationError("inventory_schema_refused") from None
            except _PrivateIOError:
                raise RemediationError("inventory_hash_refused") from None
            if _sha256(inventory_raw) != descriptor["inventory_sha256"]:
                raise RemediationError("inventory_hash_refused")
            try:
                projection_raw = root.read_private_member(
                    projection_parts, MAX_PROJECTION_RECEIPT_BYTES
                )
            except _RootPolicyError:
                raise RemediationError("private_root_refused") from None
            except _PrivateIOError:
                raise RemediationError(
                    "projection_receipt_hash_refused"
                ) from None
            if (
                _sha256(projection_raw)
                != descriptor["projection_receipt_sha256"]
            ):
                raise RemediationError("projection_receipt_hash_refused")
            inventory = _strict_json_object(
                inventory_raw,
                maximum=MAX_INVENTORY_BYTES,
                code="inventory_schema_refused",
            )
            clusters = validate_inventory(
                inventory,
                descriptor["expected_counts"],
            )
            artifact = derive_cluster_decisions(
                clusters,
                inventory_sha256=descriptor["inventory_sha256"],
                projection_receipt_sha256=descriptor[
                    "projection_receipt_sha256"
                ],
                repeated_spans=inventory["stage_b"]["repeated_spans"],
            )
            artifact_raw = _canonical(artifact)
            if len(artifact_raw) > MAX_OUTPUT_BYTES:
                raise RemediationError("decision_invariant_refused")
            _fault("before_publish")
            try:
                root.publish(output_parts[0], artifact_raw)
            except _RootPolicyError:
                raise RemediationError("private_root_refused") from None
            except _OutputExists:
                raise RemediationError("output_exists_refused") from None
            except _PublicationError:
                raise RemediationError("output_publication_refused") from None
            except _RecoveryRequired:
                raise RemediationError("output_recovery_required") from None
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "policy": POLICY,
                "inventory_sha256": descriptor["inventory_sha256"],
                "projection_receipt_sha256": descriptor[
                    "projection_receipt_sha256"
                ],
                "output_sha256": _sha256(artifact_raw),
                "counts": dict(artifact["counts"]),
                "scope": dict(artifact["scope"]),
            }
            return receipt
    except _RootPolicyError:
        raise RemediationError("private_root_refused") from None


def _write_console(value: Mapping[str, Any], *, error: bool = False) -> None:
    payload = _canonical(dict(value))
    stream = sys.stderr.buffer if error and hasattr(sys.stderr, "buffer") else (
        sys.stdout.buffer if hasattr(sys.stdout, "buffer") else (
            sys.stderr if error else sys.stdout
        )
    )
    try:
        stream.write(payload)
    except TypeError:
        stream.write(payload.decode("utf-8"))
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv == ["--help"]:
        sys.stdout.write(_STATIC_USAGE)
        return 0
    parser = _build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except UsageError:
        sys.stderr.write(_STATIC_USAGE)
        return 2
    if args.help:
        sys.stdout.write(_STATIC_USAGE)
        return 0
    try:
        receipt = run(args)
    except RemediationError as exc:
        _write_console(
            {"schema": ERROR_SCHEMA, "status": "error", "code": exc.code},
            error=True,
        )
        return 3
    _write_console(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
