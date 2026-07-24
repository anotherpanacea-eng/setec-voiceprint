"""Shared private-file writes and atomic create-new directory publication."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import tempfile
from pathlib import Path


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004
_COLLISION_ERRNOS = {
    errno.EEXIST,
    getattr(errno, "ENOTEMPTY", errno.EEXIST),
}
_UNSUPPORTED_ERRNOS = {
    errno.EINVAL,
    getattr(errno, "ENOSYS", errno.EINVAL),
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}
_UNSUPPORTED_ERRNO = getattr(
    errno, "ENOTSUP", getattr(errno, "EOPNOTSUPP", errno.EINVAL)
)


def _unsupported_publication() -> OSError:
    return OSError(
        _UNSUPPORTED_ERRNO,
        "atomic no-replace directory publication is unsupported",
    )


def secure_private_directory(path: Path) -> None:
    """Create or harden one private directory without relying on the umask."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("private output directories must not be symlinks")
    if path.exists():
        if not path.is_dir():
            raise ValueError("private output directory path is not a directory")
    else:
        missing: list[Path] = []
        current = path
        while not current.exists():
            if current.is_symlink():
                raise ValueError("private output directories must not be symlinks")
            missing.append(current)
            current = current.parent
        if current.is_symlink() or not current.is_dir():
            raise ValueError("private output parent is not a regular directory")
        for directory in reversed(missing):
            os.mkdir(directory, PRIVATE_DIRECTORY_MODE)
            if os.name == "posix":
                os.chmod(directory, PRIVATE_DIRECTORY_MODE)
    if os.name == "posix":
        os.chmod(path, PRIVATE_DIRECTORY_MODE)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _unlink_if_owned(
    path: Path,
    owned_identity: tuple[int, int],
    owner_descriptor: int,
) -> None:
    """Best-effort cleanup only while the name still identifies our file."""
    try:
        if owner_descriptor >= 0:
            try:
                owner_info = os.fstat(owner_descriptor)
            except OSError:
                # ``os.stat(fd)`` is an independent descriptor-stat entrypoint;
                # retain cleanup proof if fstat itself is unavailable/failing.
                owner_info = os.stat(owner_descriptor)
            if _identity(owner_info) != owned_identity:
                return
        named = os.stat(path, follow_symlinks=False)
        if stat.S_ISREG(named.st_mode) and _identity(named) == owned_identity:
            os.unlink(path)
    except (OSError, MemoryError):
        pass


def atomic_write_private(path: Path, data: str | bytes) -> None:
    """Atomically replace one private file with exact bytes and private modes."""
    target = Path(path)
    if type(data) not in (str, bytes):
        raise TypeError("private atomic-write data must be str or bytes")
    payload = data.encode("utf-8") if isinstance(data, str) else data
    secure_private_directory(target.parent)
    descriptor, raw_temporary = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    owned_identity: tuple[int, int] | None = None
    owner_descriptor = -1
    try:
        owned_identity = _identity(os.fstat(descriptor))
        # A retained identity handle makes cleanup ownership durable on POSIX.
        # Keep Windows rename-compatible: a live CRT duplicate can deny rename
        # sharing, so Windows cleanup relies on the captured file identity.
        if os.name == "posix":
            owner_descriptor = os.dup(descriptor)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            written = handle.write(payload)
            if written != len(payload):
                raise OSError(errno.EIO, "short private atomic write")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            os.chmod(target, PRIVATE_FILE_MODE)
    finally:
        cleanup_descriptor = (
            owner_descriptor if owner_descriptor >= 0 else descriptor
        )
        if owned_identity is None and cleanup_descriptor >= 0:
            try:
                owned_identity = _identity(os.stat(cleanup_descriptor))
            except (OSError, TypeError, ValueError):
                pass
        if owned_identity is not None:
            _unlink_if_owned(
                temporary,
                owned_identity,
                cleanup_descriptor,
            )
        if owner_descriptor >= 0:
            os.close(owner_descriptor)
        if descriptor >= 0:
            os.close(descriptor)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_directory_publish(
    staging: Path,
    destination: Path,
) -> tuple[Path, Path]:
    source = _absolute(staging)
    target = _absolute(destination)
    if os.path.normcase(os.fspath(source.parent)) != os.path.normcase(
        os.fspath(target.parent)
    ):
        raise ValueError("staging and destination must be sibling paths")
    if os.path.normcase(os.fspath(source)) == os.path.normcase(os.fspath(target)):
        raise ValueError("staging and destination must be distinct paths")
    try:
        source_info = os.stat(source, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("staging directory is missing") from exc
    if not stat.S_ISDIR(source_info.st_mode):
        raise ValueError("staging path must be a non-symlink directory")
    return source, target


def _posix_rename_noreplace(source: Path, destination: Path) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        source_bytes = os.fsencode(source)
        destination_bytes = os.fsencode(destination)
        if sys.platform == "darwin":
            function = getattr(libc, "renamex_np", None)
            if function is None:
                raise _unsupported_publication()
            function.argtypes = (
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            function.restype = ctypes.c_int
            result = function(
                ctypes.c_char_p(source_bytes),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(_RENAME_EXCL),
            )
        elif sys.platform.startswith("linux"):
            function = getattr(libc, "renameat2", None)
            if function is None:
                raise _unsupported_publication()
            function.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            function.restype = ctypes.c_int
            result = function(
                ctypes.c_int(_AT_FDCWD),
                ctypes.c_char_p(source_bytes),
                ctypes.c_int(_AT_FDCWD),
                ctypes.c_char_p(destination_bytes),
                ctypes.c_uint(_RENAME_NOREPLACE),
            )
        else:
            raise _unsupported_publication()
    except Exception as exc:
        raise _unsupported_publication() from exc
    if result == 0:
        return
    code = ctypes.get_errno() or errno.EIO
    if code in _COLLISION_ERRNOS:
        raise FileExistsError(
            code,
            "destination already exists",
            os.fspath(destination),
        )
    if code in _UNSUPPORTED_ERRNOS:
        raise _unsupported_publication() from OSError(
            code,
            os.strerror(code),
            os.fspath(destination),
        )
    raise OSError(code, os.strerror(code), os.fspath(destination))


def _windows_rename_noreplace(source: Path, target: Path) -> None:
    try:
        os.rename(source, target)
    except OSError as exc:
        if (
            isinstance(exc, FileExistsError)
            or exc.errno in _COLLISION_ERRNOS
            or getattr(exc, "winerror", None) in {80, 183}
        ):
            raise FileExistsError(
                errno.EEXIST,
                "destination already exists",
                os.fspath(target),
            ) from exc
        raise
    except Exception as exc:
        raise _unsupported_publication() from exc


def publish_directory_noreplace(staging: Path, destination: Path) -> None:
    """Atomically publish a real staged directory without replacing any winner."""
    source, target = _validate_directory_publish(staging, destination)
    if os.name == "nt":
        _windows_rename_noreplace(source, target)
    elif os.name == "posix":
        _posix_rename_noreplace(source, target)
    else:
        raise _unsupported_publication()


__all__ = [
    "atomic_write_private",
    "publish_directory_noreplace",
    "secure_private_directory",
]
