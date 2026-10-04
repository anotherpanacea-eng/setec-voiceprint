"""Windows handle-relative implementation of the portable private tree."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import time
from typing import Any, Callable, Mapping

import acquire_imessage_sent_atomic as A
import windows_descriptor_io as W


_TRANSIENT_RENAME_ERRORS = frozenset({5, 32})
_RENAME_BACKOFF_SECONDS = (0.1, 0.3, 0.9)


@dataclass
class _Seal:
    relative: str
    directory: int
    identity: tuple[int, int, int, int, int, int, int, int]
    children: dict[str, tuple[int, tuple[int, int, int, int, int, int, int, int], str, int]]


def _same_node(first: W.NodeInfo, second: W.NodeInfo) -> bool:
    return (first.volume_serial, first.file_id) == (second.volume_serial, second.file_id)


def _read_bounded(handle: int, ceiling: int, label: str) -> bytes:
    before = W.require_direct(handle, "file")
    if before.size > ceiling:
        raise A.BootstrapStateError(f"{label} exceeds its size bound")
    W.seek(handle, 0)
    chunks: list[bytes] = []
    remaining = ceiling + 1
    while remaining:
        chunk = W.read(handle, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    after = W.require_direct(handle, "file")
    raw = b"".join(chunks)
    if before.identity != after.identity or len(raw) > ceiling:
        raise A.BootstrapStateError(f"{label} changed while being read")
    return raw


def _hash_file(handle: int) -> tuple[str, int, tuple[int, int, int, int, int, int, int, int]]:
    before = W.require_direct(handle, "file")
    W.seek(handle, 0)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = W.read(handle, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    after = W.require_direct(handle, "file")
    if before.identity != after.identity or size != after.size:
        raise A.BootstrapStateError("private file changed while hashing")
    return "sha256:" + digest.hexdigest(), size, after.identity


class WindowsPortableDurableRowIo(A.PortableDurableRowIo):
    """Portable tree confined by NT RootDirectory handles.

    Every component lookup is a single-name ``NtCreateFile`` beneath a pinned
    directory.  Reparse points are opened as reparse points and refused before
    traversal, so changing a pathname cannot redirect an in-flight mutation.
    """

    def __init__(
        self,
        root: Path,
        *,
        _trusted_parent: "WindowsPortableDurableRowIo | None" = None,
        _child_name: str | None = None,
    ) -> None:
        self.root = Path(root).absolute()
        self._pending_row_seal: _Seal | None = None
        self._closed = False
        if _trusted_parent is None:
            if _child_name is not None or A.PRIVATE_ROOT_COMPONENT not in self.root.parts:
                raise A.BootstrapStateError("portable tree root authority is invalid")
            try:
                parent, final, name = W.pin_directory(self.root)
            except OSError as exc:
                raise A.BootstrapStateError("cannot pin Windows portable tree root") from exc
        else:
            if _child_name is None:
                raise A.BootstrapStateError("portable tree child name is missing")
            _trusted_parent._verify_root()
            name = A._bootstrap_basename(_child_name, "portable tree child name")
            parent = W.duplicate(_trusted_parent.final_fd)
            try:
                final = W.open_directory(parent, name, writable=True)
            except BaseException:
                W.close(parent)
                raise
        self.parent_fd = parent
        self.final_fd = final
        self.final_name = name
        try:
            self._verify_root()
        except BaseException:
            self._closed = True
            W.close(self.final_fd)
            W.close(self.parent_fd)
            raise

    @classmethod
    def open_child(
        cls, parent: "WindowsPortableDurableRowIo", child_name: str
    ) -> "WindowsPortableDurableRowIo":
        name = A._bootstrap_basename(child_name, "portable tree child name")
        return cls(parent.root / name, _trusted_parent=parent, _child_name=name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first: BaseException | None = None
        try:
            self._close_pending_row_seal()
        except BaseException as exc:
            first = exc
        for handle in (self.final_fd, self.parent_fd):
            try:
                W.close(handle)
            except BaseException as exc:
                if first is None:
                    first = exc
        if first is not None:
            raise A.BootstrapRecoveryRequired(
                "Windows portable trusted-root close requires recovery"
            ) from first

    def _verify_root(self) -> None:
        if self._closed:
            raise A.BootstrapStateError("portable trusted root is closed")
        opened = W.require_direct(self.final_fd, "directory")
        named: int | None = None
        try:
            named = W.open_directory(self.parent_fd, self.final_name)
            if not _same_node(opened, W.require_direct(named, "directory")):
                raise A.BootstrapStateError("portable trusted-root name drifted")
        finally:
            if named is not None:
                W.close(named)

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        self._verify_root()
        current = W.duplicate(self.final_fd)
        try:
            for part in parts:
                following = W.open_directory(current, part, writable=True)
                W.close(current)
                current = following
            return current
        except BaseException:
            W.close(current)
            raise

    def _open_parent(self, relative: str) -> tuple[int, str]:
        parts = A._row_relative_parts(relative)
        return self._open_directory(parts[:-1]), parts[-1]

    def _verify_directory_binding(
        self,
        parts: tuple[str, ...],
        identity: tuple[int, int, int, int, int, int, int, int],
        label: str,
    ) -> None:
        handle: int | None = None
        try:
            handle = self._open_directory(parts)
            if W.require_direct(handle, "directory").identity[:2] != identity[:2]:
                raise A.BootstrapRecoveryRequired(f"{label} parent binding drifted")
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            raise A.BootstrapRecoveryRequired(
                f"{label} parent binding requires recovery"
            ) from exc
        finally:
            if handle is not None:
                W.close(handle)

    def root_names(self) -> tuple[str, ...]:
        self._verify_root()
        return W.list_names(self.final_fd)

    @staticmethod
    def _row_journal_rewrite_residue(name: str) -> str | None:
        prefix = f".{A.ROW_JOURNAL_FILENAME}."
        if not name.startswith(prefix):
            return None
        remainder = name[len(prefix):]
        token, separator, kind = remainder.partition(".")
        if (
            not separator
            or len(token) != 32
            or any(character not in "0123456789abcdef" for character in token)
            or kind not in {"tmp", "replaced"}
        ):
            return None
        return kind

    @staticmethod
    def _decode_row_journal_residue(raw: bytes) -> dict[str, Any]:
        return A._decode_canonical_private_json(
            raw,
            max_bytes=A.MAX_ROW_JOURNAL_BYTES,
            validator=A._validated_row_transaction_payload,
            artifact_label="row transaction recovery residue",
        )

    @staticmethod
    def _journal_rewrite_successor(
        predecessor_raw: bytes,
        predecessor: dict[str, Any],
        successor: dict[str, Any],
    ) -> bool:
        allowed = {
            "prepared": (
                {"staged"}
                if predecessor["disposition"] == "retained"
                else {"ledger_closed"}
            ),
            "staged": {"committed_unledgered"},
            "committed_unledgered": {"ledger_closed"},
            "ledger_closed": {"checkpoint_closed"},
            "checkpoint_closed": set(),
        }
        immutable = set(predecessor) - {"state", "previous_journal_digest"}
        return (
            successor["state"] in allowed[predecessor["state"]]
            and successor["previous_journal_digest"]
            == "sha256:" + hashlib.sha256(predecessor_raw).hexdigest()
            and all(successor[key] == predecessor[key] for key in immutable)
        )

    def recover_row_journal_rewrite(self) -> None:
        """Finish one provable interrupted journal CAS before closed inventory."""

        names = self.root_names()
        residues: dict[str, str] = {}
        for name in names:
            kind = self._row_journal_rewrite_residue(name)
            if kind is None:
                continue
            if kind in residues:
                raise A.BootstrapStateError(
                    "row transaction recovery residue is ambiguous"
                )
            residues[kind] = name
        if not residues:
            return

        ordered_names = [
            name
            for name in (
                residues.get("replaced"),
                A.ROW_JOURNAL_FILENAME if A.ROW_JOURNAL_FILENAME in names else None,
                residues.get("tmp"),
            )
            if name is not None
        ]
        handles: dict[str, int] = {}
        raws: dict[str, bytes] = {}
        payloads: dict[str, dict[str, Any]] = {}
        mutated = False
        try:
            for name in ordered_names:
                handle = W.open_file(
                    self.final_fd,
                    name,
                    writable=True,
                    delete_access=True,
                )
                handles[name] = handle
                raw = _read_bounded(
                    handle,
                    A.MAX_ROW_JOURNAL_BYTES,
                    "row transaction recovery residue",
                )
                raws[name] = raw
                payloads[name] = self._decode_row_journal_residue(raw)
            for predecessor_name, successor_name in zip(
                ordered_names, ordered_names[1:]
            ):
                if not self._journal_rewrite_successor(
                    raws[predecessor_name],
                    payloads[predecessor_name],
                    payloads[successor_name],
                ):
                    raise A.BootstrapStateError(
                        "row transaction recovery chain is invalid"
                    )

            temporary_name = residues.get("tmp")
            backup_name = residues.get("replaced")
            target_present = A.ROW_JOURNAL_FILENAME in handles
            if (
                temporary_name is not None
                and len(ordered_names) == 1
                and (
                    payloads[temporary_name]["state"] != "prepared"
                    or payloads[temporary_name]["previous_journal_digest"] is not None
                )
            ):
                raise A.BootstrapStateError(
                    "lone row transaction temporary is not an initial journal"
                )

            newest_name = ordered_names[-1]
            if not target_present and backup_name is not None:
                mutated = True
                W.rename(
                    handles[backup_name],
                    self.final_fd,
                    A.ROW_JOURNAL_FILENAME,
                    replace=False,
                )
                handles[A.ROW_JOURNAL_FILENAME] = handles.pop(backup_name)
                target_present = True
                W.flush(self.final_fd)
            elif backup_name is not None:
                mutated = True
                backup = handles.pop(backup_name)
                W.delete(backup)
                W.close(backup)
                W.flush(self.final_fd)
            if temporary_name is not None:
                mutated = True
                W.rename(
                    handles[temporary_name],
                    self.final_fd,
                    A.ROW_JOURNAL_FILENAME,
                    replace=target_present,
                )
                if target_present:
                    W.close(handles.pop(A.ROW_JOURNAL_FILENAME))
                handles[A.ROW_JOURNAL_FILENAME] = handles.pop(temporary_name)
                W.flush(self.final_fd)
            rebound = W.open_file(self.final_fd, A.ROW_JOURNAL_FILENAME)
            try:
                if _read_bounded(
                    rebound,
                    A.MAX_ROW_JOURNAL_BYTES,
                    "recovered row transaction",
                ) != raws[newest_name]:
                    raise A.BootstrapRecoveryRequired(
                        "recovered row transaction bytes drifted"
                    )
            finally:
                W.close(rebound)
            if any(
                self._row_journal_rewrite_residue(name) is not None
                for name in self.root_names()
            ):
                raise A.BootstrapRecoveryRequired(
                    "row transaction recovery residue survived"
                )
            self._verify_root()
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(
                    "row transaction rewrite recovery requires recovery"
                ) from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError(
                "cannot inspect row transaction recovery residue"
            ) from exc
        finally:
            for handle in handles.values():
                W.close(handle)

    def exists(self, relative: str) -> bool:
        parent, name = self._open_parent(relative)
        handle: int | None = None
        try:
            try:
                handle = W.open_node(parent, name)
            except FileNotFoundError:
                return False
            return True
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

    def ensure_directory(self, relative: str) -> None:
        parts = A._row_relative_parts(relative)
        current = W.duplicate(self.final_fd)
        current_parts: tuple[str, ...] = ()
        mutated = False
        try:
            for part in parts:
                parent_identity = W.require_direct(current, "directory").identity
                try:
                    following = W.open_directory(current, part, writable=True)
                except FileNotFoundError:
                    mutated = True
                    following = W.create_directory(current, part)
                    W.flush(following)
                    W.flush(current)
                    self._verify_directory_binding(
                        current_parts, parent_identity, "portable directory creation"
                    )
                    child_identity = W.require_direct(following, "directory").identity
                    self._verify_directory_binding(
                        (*current_parts, part), child_identity, "portable directory creation"
                    )
                W.close(current)
                current = following
                current_parts = (*current_parts, part)
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(
                    "portable directory creation requires recovery"
                ) from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError("cannot create portable directory") from exc
        finally:
            W.close(current)
        self._verify_root()

    def list_directory(self, relative: str) -> tuple[str, ...]:
        handle = self._open_directory(A._row_relative_parts(relative))
        try:
            return W.list_names(handle)
        finally:
            W.close(handle)

    def read_bytes(self, relative: str, label: str) -> bytes:
        parent, name = self._open_parent(relative)
        handle: int | None = None
        try:
            handle = W.open_file(parent, name)
            raw = _read_bounded(handle, A.MAX_ROW_STATE_BYTES, label)
            rebound = W.open_file(parent, name)
            try:
                if not _same_node(W.info(handle), W.info(rebound)):
                    raise A.BootstrapStateError(f"{label} name binding drifted")
            finally:
                W.close(rebound)
            return raw
        except OSError as exc:
            raise A.BootstrapStateError(f"cannot read {label}") from exc
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

    def write_bytes(
        self,
        relative: str,
        raw: bytes,
        *,
        expected_existing: bytes | None,
        label: str,
    ) -> None:
        if type(raw) is not bytes or len(raw) > A.MAX_ROW_STATE_BYTES:
            raise A.BootstrapStateError(f"{label} payload size is invalid")
        parts = A._row_relative_parts(relative)
        parent_parts, name = parts[:-1], parts[-1]
        parent = self._open_directory(parent_parts)
        existing: int | None = None
        temporary: int | None = None
        mutated = False
        try:
            parent_identity = W.require_direct(parent, "directory").identity
            try:
                existing = W.open_file(
                    parent,
                    name,
                    delete_access=expected_existing is not None,
                    share_delete=expected_existing is None,
                    share_write=expected_existing is None,
                )
            except FileNotFoundError:
                if expected_existing is not None:
                    raise A.BootstrapStateError(f"{label} replacement target is missing")
            else:
                if expected_existing is None:
                    raise A.BootstrapStateError(f"{label} already exists")
                if _read_bounded(existing, A.MAX_ROW_STATE_BYTES, label) != expected_existing:
                    raise A.BootstrapStateError(f"{label} compare-and-swap bytes drifted")
            temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
            temporary = W.create_file(parent, temporary_name)
            mutated = True
            view = memoryview(raw)
            written = 0
            while written < len(view):
                count = W.write(temporary, view[written:])
                if count <= 0:
                    raise A.BootstrapStateError(f"{label} write was incomplete")
                written += count
            W.flush(temporary)
            temp_identity = W.require_direct(temporary, "file").identity
            backup_name: str | None = None
            if existing is not None:
                rebound_existing = W.open_file(parent, name)
                try:
                    if not _same_node(
                        W.require_direct(existing, "file"),
                        W.require_direct(rebound_existing, "file"),
                    ):
                        raise A.BootstrapStateError(
                            f"{label} compare-and-swap binding drifted"
                        )
                    if _read_bounded(existing, A.MAX_ROW_STATE_BYTES, label) != expected_existing:
                        raise A.BootstrapStateError(
                            f"{label} compare-and-swap bytes drifted"
                        )
                finally:
                    W.close(rebound_existing)
                backup_name = f".{name}.{secrets.token_hex(16)}.replaced"
                W.rename(existing, parent, backup_name, replace=False)
                W.flush(parent)
                try:
                    raced = W.open_node(parent, name)
                except FileNotFoundError:
                    raced = None
                if raced is not None:
                    W.close(raced)
                    raise A.BootstrapRecoveryRequired(
                        f"{label} compare-and-swap destination raced"
                    )
            W.rename(temporary, parent, name, replace=False)
            rebound = W.open_file(parent, name)
            try:
                if W.info(rebound).identity[:2] != temp_identity[:2]:
                    raise A.BootstrapRecoveryRequired(f"{label} publication identity drifted")
                if _read_bounded(rebound, A.MAX_ROW_STATE_BYTES, label) != raw:
                    raise A.BootstrapRecoveryRequired(f"{label} published bytes drifted")
            finally:
                W.close(rebound)
            W.flush(parent)
            if existing is not None:
                W.delete(existing)
                W.close(existing)
                existing = None
                W.flush(parent)
                assert backup_name is not None
                try:
                    backup = W.open_node(parent, backup_name)
                except FileNotFoundError:
                    backup = None
                if backup is not None:
                    W.close(backup)
                    raise A.BootstrapRecoveryRequired(
                        f"{label} predecessor cleanup was not durable"
                    )
            self._verify_directory_binding(parent_parts, parent_identity, label)
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(f"{label} mutation requires recovery") from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError(f"cannot write {label}") from exc
        finally:
            if temporary is not None:
                W.close(temporary)
            if existing is not None:
                W.close(existing)
            W.close(parent)

    def write_json(
        self,
        relative: str,
        payload: dict[str, Any],
        *,
        expected_existing: bytes | None,
        validator: Callable[[dict[str, Any]], dict[str, Any]],
        label: str,
    ) -> bytes:
        raw = A._canonical_json_bytes(payload)
        if validator(payload) != payload:
            raise A.BootstrapStateError(f"{label} schema drifted")
        self.write_bytes(relative, raw, expected_existing=expected_existing, label=label)
        return raw

    def remove_file(self, relative: str, *, expected: bytes, label: str) -> None:
        parts = A._row_relative_parts(relative)
        parent_parts, name = parts[:-1], parts[-1]
        parent = self._open_directory(parent_parts)
        handle: int | None = None
        mutated = False
        try:
            parent_identity = W.info(parent).identity
            handle = W.open_file(parent, name, writable=True, delete_access=True)
            if _read_bounded(handle, A.MAX_ROW_STATE_BYTES, label) != expected:
                raise A.BootstrapStateError(f"{label} changed before removal")
            mutated = True
            W.delete(handle)
            W.close(handle)
            handle = None
            W.flush(parent)
            try:
                survivor = W.open_node(parent, name)
            except FileNotFoundError:
                survivor = None
            if survivor is not None:
                W.close(survivor)
                raise A.BootstrapRecoveryRequired(f"{label} removal was not durable")
            self._verify_directory_binding(parent_parts, parent_identity, label)
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(f"{label} removal requires recovery") from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError(f"cannot remove {label}") from exc
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

    def remove_empty_directory(self, relative: str) -> None:
        parts = A._row_relative_parts(relative)
        parent_parts, name = parts[:-1], parts[-1]
        parent = self._open_directory(parent_parts)
        handle: int | None = None
        mutated = False
        try:
            parent_identity = W.info(parent).identity
            handle = W.open_directory(parent, name, delete_access=True)
            if W.list_names(handle):
                raise A.BootstrapStateError("row staging directory is not empty")
            W.flush(handle)
            mutated = True
            W.delete(handle)
            W.close(handle)
            handle = None
            W.flush(parent)
            self._verify_directory_binding(
                parent_parts, parent_identity, "row staging directory"
            )
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(
                    "row staging directory removal requires recovery"
                ) from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError("cannot remove row staging directory") from exc
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

    def _close_pending_row_seal(self) -> None:
        seal, self._pending_row_seal = self._pending_row_seal, None
        if seal is None:
            return
        first: BaseException | None = None
        for handle, _identity, _digest, _size in seal.children.values():
            try:
                W.close(handle)
            except BaseException as exc:
                first = first or exc
        try:
            W.close(seal.directory)
        except BaseException as exc:
            first = first or exc
        if first is not None:
            raise A.BootstrapRecoveryRequired(
                "pinned atomic row handle close requires recovery"
            ) from first

    def _validated_evidence(
        self, evidence: Mapping[str, tuple[str, int]]
    ) -> dict[str, tuple[str, int]]:
        if type(evidence) is not dict or not evidence:
            raise A.BootstrapStateError("atomic row seal expectation is invalid")
        validated: dict[str, tuple[str, int]] = {}
        for name, value in evidence.items():
            if (
                type(name) is not str
                or A._bootstrap_basename(name, "row artifact name") != name
                or type(value) is not tuple
                or len(value) != 2
                or not A._is_sha256_tag(value[0])
                or type(value[1]) is not int
                or value[1] < 0
            ):
                raise A.BootstrapStateError("atomic row seal expectation is invalid")
            validated[name] = value
        return validated

    def _pin_directory(
        self, relative: str, expected_files: Mapping[str, tuple[str, int]]
    ) -> _Seal:
        expected_files = self._validated_evidence(expected_files)
        parent, name = self._open_parent(relative)
        directory: int | None = None
        children: dict[str, tuple[int, tuple[int, int, int, int, int, int, int, int], str, int]] = {}
        try:
            directory = W.open_directory(parent, name, writable=True, delete_access=True)
            identity = W.info(directory).identity
            if set(W.list_names(directory)) != set(expected_files):
                raise A.BootstrapStateError("atomic row directory inventory drifted")
            for child_name, (digest, size) in expected_files.items():
                child = W.open_file(directory, child_name)
                observed_digest, observed_size, child_identity = _hash_file(child)
                if (observed_digest, observed_size) != (digest, size):
                    W.close(child)
                    raise A.BootstrapStateError("atomic row artifact drifted")
                children[child_name] = (child, child_identity, digest, size)
            W.flush(directory)
            return _Seal(relative, directory, identity, children)
        except BaseException:
            for child, _identity, _digest, _size in children.values():
                W.close(child)
            if directory is not None:
                W.close(directory)
            raise
        finally:
            W.close(parent)

    def _release_seal_children(self, seal: _Seal) -> dict[str, tuple[str, int]]:
        evidence = {
            name: (digest, size)
            for name, (_handle, _identity, digest, size) in seal.children.items()
        }
        for handle, _identity, _digest, _size in seal.children.values():
            W.close(handle)
        seal.children = {}
        return evidence

    def _rebind_seal_children(
        self, seal: _Seal, evidence: Mapping[str, tuple[str, int]]
    ) -> None:
        rebound: dict[str, tuple[int, tuple[int, int, int, int, int, int, int, int], str, int]] = {}
        try:
            if set(W.list_names(seal.directory)) != set(evidence):
                raise A.BootstrapRecoveryRequired("pinned atomic row inventory drifted")
            for name, (digest, size) in evidence.items():
                handle = W.open_file(seal.directory, name)
                actual_digest, actual_size, identity = _hash_file(handle)
                if (actual_digest, actual_size) != (digest, size):
                    W.close(handle)
                    raise A.BootstrapRecoveryRequired("pinned atomic row artifact drifted")
                rebound[name] = (handle, identity, digest, size)
            seal.children = rebound
        except BaseException:
            for handle, _identity, _digest, _size in rebound.values():
                W.close(handle)
            raise

    def _verify_seal(self, seal: _Seal) -> None:
        if W.info(seal.directory).identity[:2] != seal.identity[:2]:
            raise A.BootstrapRecoveryRequired("pinned atomic row directory drifted")
        if set(W.list_names(seal.directory)) != set(seal.children):
            raise A.BootstrapRecoveryRequired("pinned atomic row inventory drifted")
        for name, (handle, identity, digest, size) in seal.children.items():
            actual_digest, actual_size, actual_identity = _hash_file(handle)
            if actual_identity != identity or (actual_digest, actual_size) != (digest, size):
                raise A.BootstrapRecoveryRequired("pinned atomic row artifact drifted")
            rebound = W.open_file(seal.directory, name)
            try:
                if W.info(rebound).identity[:2] != identity[:2]:
                    raise A.BootstrapRecoveryRequired("pinned atomic row name drifted")
            finally:
                W.close(rebound)

    def seal_directory(self, relative: str, expected_files: Mapping[str, bytes]) -> None:
        self._close_pending_row_seal()
        if (
            type(expected_files) is not dict
            or not expected_files
            or any(type(name) is not str or type(raw) is not bytes for name, raw in expected_files.items())
        ):
            raise A.BootstrapStateError("atomic row seal expectation is invalid")
        evidence = self._validated_evidence(
            {name: (A._sha256_tag(raw), len(raw)) for name, raw in expected_files.items()}
        )
        self._pending_row_seal = self._pin_directory(relative, evidence)

    def _commit_directory(
        self,
        source: str,
        destination: str,
        evidence: Mapping[str, tuple[str, int]],
        *,
        use_pending: bool,
    ) -> None:
        source_parts = A._row_relative_parts(source)
        destination_parts = A._row_relative_parts(destination)
        source_parent = self._open_directory(source_parts[:-1])
        destination_parent = self._open_directory(destination_parts[:-1])
        seal = self._pending_row_seal if use_pending else None
        if seal is None or seal.relative != source:
            if use_pending:
                self._close_pending_row_seal()
            seal = self._pin_directory(source, evidence)
            if use_pending:
                self._pending_row_seal = seal
        renamed = False
        try:
            self._verify_seal(seal)
            source_named = W.open_directory(source_parent, source_parts[-1])
            try:
                if W.info(source_named).identity[:2] != seal.identity[:2]:
                    raise A.BootstrapStateError("staged atomic row binding drifted")
            finally:
                W.close(source_named)
            try:
                destination_named = W.open_node(destination_parent, destination_parts[-1])
            except FileNotFoundError:
                destination_named = None
            if destination_named is not None:
                W.close(destination_named)
                raise A.BootstrapStateError("destination already exists")
            W.flush(seal.directory)
            self._verify_seal(seal)
            # Windows cannot rename a directory while descendant handles are
            # open.  Release only after the final seal check; rebind and rehash
            # after every failed attempt and immediately after publication.
            closed_evidence = self._release_seal_children(seal)
            delays = iter(_RENAME_BACKOFF_SECONDS)
            while True:
                try:
                    W.rename(
                        seal.directory,
                        destination_parent,
                        destination_parts[-1],
                        replace=False,
                    )
                    renamed = True
                    break
                except OSError as exc:
                    try:
                        destination_named = W.open_node(
                            destination_parent, destination_parts[-1]
                        )
                    except FileNotFoundError:
                        destination_named = None
                    try:
                        source_named = W.open_directory(source_parent, source_parts[-1])
                    except FileNotFoundError:
                        source_named = None
                    try:
                        if destination_named is not None or source_named is None:
                            raise A.BootstrapRecoveryRequired(
                                "atomic row rename outcome is ambiguous"
                            ) from exc
                        if W.info(source_named).identity[:2] != seal.identity[:2]:
                            raise A.BootstrapRecoveryRequired(
                                "staged atomic row changed during retry"
                            ) from exc
                    finally:
                        if destination_named is not None:
                            W.close(destination_named)
                        if source_named is not None:
                            W.close(source_named)
                    if getattr(exc, "winerror", None) not in _TRANSIENT_RENAME_ERRORS:
                        raise
                    try:
                        delay = next(delays)
                    except StopIteration:
                        raise exc
                    time.sleep(delay)
                    self._rebind_seal_children(seal, closed_evidence)
                    self._verify_seal(seal)
                    self._release_seal_children(seal)
            self._rebind_seal_children(seal, closed_evidence)
            self._verify_seal(seal)
            published = W.open_directory(destination_parent, destination_parts[-1])
            try:
                if W.info(published).identity[:2] != seal.identity[:2]:
                    raise A.BootstrapRecoveryRequired("committed atomic row identity drifted")
            finally:
                W.close(published)
            try:
                survivor = W.open_node(source_parent, source_parts[-1])
            except FileNotFoundError:
                survivor = None
            if survivor is not None:
                W.close(survivor)
                raise A.BootstrapRecoveryRequired("staged atomic row name survived commit")
            self._verify_seal(seal)
            W.flush(destination_parent)
            W.flush(source_parent)
            self._verify_seal(seal)
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if renamed:
                raise A.BootstrapRecoveryRequired(
                    "committed atomic row requires locked recovery"
                ) from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError("cannot commit atomic row") from exc
        finally:
            W.close(source_parent)
            W.close(destination_parent)
            if use_pending:
                self._close_pending_row_seal()
            elif seal is not None:
                for handle, _identity, _digest, _size in seal.children.values():
                    W.close(handle)
                W.close(seal.directory)
        self._verify_root()

    def commit_directory(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, bytes],
    ) -> None:
        evidence = {name: (A._sha256_tag(raw), len(raw)) for name, raw in expected_files.items()}
        self._commit_directory(source, destination, evidence, use_pending=True)

    def verify_file(
        self,
        relative: str,
        *,
        expected_digest: str,
        expected_size: int,
        label: str,
    ) -> tuple[int, int, int, int, int, int, int, int]:
        parent, name = self._open_parent(relative)
        handle: int | None = None
        try:
            handle = W.open_file(parent, name)
            digest, size, identity = _hash_file(handle)
            if (digest, size) != (expected_digest, expected_size):
                raise A.BootstrapStateError(f"{label} bytes drifted")
            rebound = W.open_file(parent, name)
            try:
                if W.info(rebound).identity[:2] != identity[:2]:
                    raise A.BootstrapStateError(f"{label} name binding drifted")
            finally:
                W.close(rebound)
            return identity
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

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
        source_handle: int | None = None
        parent: int | None = None
        partial: int | None = None
        mutated = False
        try:
            source_handle = W.open_absolute_file(A._portable_regular_file(source, label))
            if W.info(source_handle).size != expected_size:
                raise A.BootstrapStateError(f"{label} source size drifted")
            parent, temporary_name = self._open_parent(temporary)
            destination_parent, destination_name = self._open_parent(destination)
            try:
                if W.info(parent).identity[:2] != W.info(destination_parent).identity[:2]:
                    raise A.BootstrapStateError(f"{label} names do not share one parent")
            finally:
                W.close(destination_parent)
            try:
                published = W.open_file(parent, destination_name)
            except FileNotFoundError:
                published = None
            if published is not None:
                W.close(published)
                self.verify_file(
                    destination,
                    expected_digest=expected_digest,
                    expected_size=expected_size,
                    label=label,
                )
                return
            try:
                partial = W.open_file(
                    parent, temporary_name, writable=True, delete_access=True
                )
            except FileNotFoundError:
                partial = W.create_file(parent, temporary_name)
                mutated = True
                W.flush(parent)
            partial_size = W.info(partial).size
            if partial_size > expected_size:
                raise A.BootstrapStateError(f"{label} partial is oversized")
            W.seek(source_handle, 0)
            W.seek(partial, 0)
            digest = hashlib.sha256()
            compared = 0
            while compared < partial_size:
                amount = min(1024 * 1024, partial_size - compared)
                source_chunk = W.read(source_handle, amount)
                partial_chunk = W.read(partial, amount)
                if len(source_chunk) != amount or source_chunk != partial_chunk:
                    raise A.BootstrapStateError(f"{label} partial is not an approved prefix")
                digest.update(source_chunk)
                compared += amount
            W.seek(partial, 0, os.SEEK_END)
            while True:
                chunk = W.read(source_handle, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                offset = 0
                while offset < len(chunk):
                    count = W.write(partial, chunk[offset:])
                    if count <= 0:
                        raise A.BootstrapStateError(f"{label} copy was incomplete")
                    offset += count
            if W.info(partial).size != expected_size or "sha256:" + digest.hexdigest() != expected_digest:
                raise A.BootstrapStateError(f"{label} source bytes drifted")
            mutated = True
            W.flush(partial)
            W.rename(partial, parent, destination_name, replace=False)
            W.flush(parent)
            self.verify_file(
                destination,
                expected_digest=expected_digest,
                expected_size=expected_size,
                label=label,
            )
        except A.BootstrapRecoveryRequired:
            raise
        except BaseException as exc:
            if mutated:
                raise A.BootstrapRecoveryRequired(f"{label} copy requires recovery") from exc
            if isinstance(exc, A.BootstrapStateError):
                raise
            raise A.BootstrapStateError(f"cannot copy {label}") from exc
        finally:
            for handle in (partial, parent, source_handle):
                if handle is not None:
                    W.close(handle)

    def commit_directory_evidence(
        self,
        source: str,
        destination: str,
        *,
        expected_files: Mapping[str, tuple[str, int]],
    ) -> None:
        if not expected_files:
            raise A.BootstrapStateError("portable directory evidence is empty")
        self._commit_directory(source, destination, expected_files, use_pending=False)


class WindowsPrivateReadOnlyRowIo:
    """Handle-pinned, no-reparse reader for a completed Windows private run."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).absolute()
        self._closed = False
        try:
            parent, final, name = W.pin_directory(
                self.root, writable_final=False
            )
        except OSError as exc:
            raise A.BootstrapStateError(
                "cannot pin completed Windows atomic run"
            ) from exc
        self.parent_fd = parent
        self.final_fd = final
        self.final_name = name
        try:
            self._verify_root()
        except BaseException:
            self._closed = True
            W.close(self.final_fd)
            W.close(self.parent_fd)
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first: BaseException | None = None
        for handle in (self.final_fd, self.parent_fd):
            try:
                W.close(handle)
            except BaseException as exc:
                if first is None:
                    first = exc
        if first is not None:
            raise A.BootstrapStateError(
                "completed Windows atomic run reader close failed"
            ) from first

    def _verify_root(self) -> None:
        if self._closed:
            raise A.BootstrapStateError("completed atomic run reader is closed")
        opened = W.require_direct(self.final_fd, "directory")
        named: int | None = None
        try:
            named = W.open_directory(self.parent_fd, self.final_name)
            if not _same_node(opened, W.require_direct(named, "directory")):
                raise A.BootstrapStateError(
                    "completed atomic run pathname drifted"
                )
        finally:
            if named is not None:
                W.close(named)

    def _open_directory(self, parts: tuple[str, ...]) -> int:
        self._verify_root()
        current = W.duplicate(self.final_fd)
        try:
            for part in parts:
                following = W.open_directory(current, part)
                W.close(current)
                current = following
            return current
        except BaseException:
            W.close(current)
            raise

    def _open_parent(self, relative: str) -> tuple[int, str]:
        parts = A._row_relative_parts(relative)
        return self._open_directory(parts[:-1]), parts[-1]

    def root_names(self) -> tuple[str, ...]:
        self._verify_root()
        names = W.list_names(self.final_fd)
        self._verify_root()
        return names

    def exists(self, relative: str) -> bool:
        parent, name = self._open_parent(relative)
        handle: int | None = None
        try:
            try:
                handle = W.open_node(parent, name)
            except FileNotFoundError:
                return False
            return True
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)

    def list_directory(self, relative: str) -> tuple[str, ...]:
        directory = self._open_directory(A._row_relative_parts(relative))
        try:
            return W.list_names(directory)
        finally:
            W.close(directory)

    def read_bytes(self, relative: str, label: str) -> bytes:
        parent, name = self._open_parent(relative)
        handle: int | None = None
        try:
            handle = W.open_file(parent, name)
            return _read_bounded(handle, A.MAX_ROW_STATE_BYTES, label)
        finally:
            if handle is not None:
                W.close(handle)
            W.close(parent)
