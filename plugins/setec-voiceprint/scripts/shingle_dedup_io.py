"""Identity-conscious binary I/O helpers for :mod:`shingle_dedup`.

The public functions deliberately collapse platform and path details into
``SecureIOError``.  Callers can therefore preserve the CLI's non-disclosing
exit contract while sharing the same bounded-read and create-new machinery on
POSIX and Windows.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import stat
from typing import Any


_CHUNK = 1024 * 1024


class SecureIOError(OSError):
    """A stable refusal that intentionally carries no filesystem details."""

    def __init__(self) -> None:
        super().__init__("secure filesystem operation refused")


def _fail() -> SecureIOError:
    return SecureIOError()


def _absolute(path: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise _fail()
        return Path(os.path.abspath(raw))
    except (OSError, TypeError, ValueError, UnicodeError) as exc:
        if isinstance(exc, SecureIOError):
            raise
        raise _fail() from None


def _require_beneath(path: Path, root: os.PathLike[str] | str | None) -> None:
    if root is None:
        return
    root_path = _absolute(root)
    try:
        if os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(root_path)))) != os.path.normcase(str(root_path)):
            raise _fail()
    except (OSError, ValueError):
        raise _fail() from None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _file_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Identity plus mutation-sensitive fields (atime deliberately excluded)."""
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_size),
        int(info.st_mtime_ns), int(info.st_ctime_ns),
    )


def _optional_flag(name: str) -> int:
    return int(getattr(os, name, 0))


def _posix_open_directory(path: Path) -> tuple[int, list[int]]:
    """Open every directory component without following an indirect node."""
    if os.name != "posix" or not path.is_absolute():
        raise _fail()
    opened: list[int] = []
    flags = os.O_RDONLY | _optional_flag("O_DIRECTORY") | _optional_flag("O_CLOEXEC") | _optional_flag("O_NOFOLLOW") | _optional_flag("O_BINARY")
    try:
        current = os.open(path.anchor or "/", flags)
        opened.append(current)
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise _fail()
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise _fail()
            before = os.stat(component, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise _fail()
            following = os.open(component, flags, dir_fd=current)
            opened.append(following)
            after = os.fstat(following)
            if not stat.S_ISDIR(after.st_mode) or _identity(before) != _identity(after):
                raise _fail()
            current = following
        return current, opened
    except (OSError, TypeError, ValueError):
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise _fail() from None


def _close_all(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _posix_revalidate_chain(path: Path, descriptors: list[int]) -> None:
    components = path.parts[1:]
    if len(descriptors) != len(components) + 1:
        raise _fail()
    try:
        for index, component in enumerate(components):
            named = os.stat(component, dir_fd=descriptors[index], follow_symlinks=False)
            opened = os.fstat(descriptors[index + 1])
            if not stat.S_ISDIR(named.st_mode) or _identity(named) != _identity(opened):
                raise _fail()
    except (OSError, TypeError, ValueError):
        raise _fail() from None


def _posix_require_absent(parent: int, leaf: str, suffixes: tuple[str, ...]) -> None:
    for suffix in suffixes:
        try:
            os.stat(leaf + suffix, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise _fail() from None
        raise _fail()


def _posix_read(path: Path, maximum: int, forbidden_suffixes: tuple[str, ...] = ()) -> bytes:
    parent_fd, directories = _posix_open_directory(path.parent)
    descriptor = -1
    try:
        _posix_revalidate_chain(path.parent, directories)
        _posix_require_absent(parent_fd, path.name, forbidden_suffixes)
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > maximum:
            raise _fail()
        flags = os.O_RDONLY | _optional_flag("O_CLOEXEC") | _optional_flag("O_NOFOLLOW") | _optional_flag("O_BINARY")
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_fingerprint(before) != _file_fingerprint(opened):
            raise _fail()
        parts: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(_CHUNK, maximum + 1 - total))
            if not chunk:
                break
            parts.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise _fail()
        after = os.fstat(descriptor)
        _posix_revalidate_chain(path.parent, directories)
        _posix_require_absent(parent_fd, path.name, forbidden_suffixes)
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (_file_fingerprint(opened) != _file_fingerprint(after)
                or _file_fingerprint(opened) != _file_fingerprint(named)
                or after.st_size != total):
            raise _fail()
        return b"".join(parts)
    except (OSError, TypeError, ValueError):
        raise _fail() from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _close_all(directories)


def _windows_module() -> Any:
    try:
        import windows_descriptor_io as winio
        return winio
    except (ImportError, OSError):
        raise _fail() from None


def _windows_revalidate_directory(path: Path, retained: int) -> None:  # pragma: no cover - native Windows
    winio = _windows_module()
    anchor = directory = 0
    try:
        anchor, directory, _name = winio.pin_directory(path, writable_final=False)
        if winio.require_direct(retained, "directory").identity != winio.require_direct(directory, "directory").identity:
            raise _fail()
    except (OSError, TypeError, ValueError):
        raise _fail() from None
    finally:
        for item in (directory, anchor):
            if item:
                try:
                    winio.close(item)
                except OSError:
                    pass


def _windows_require_absent(parent: int, leaf: str, suffixes: tuple[str, ...]) -> None:  # pragma: no cover - native Windows
    winio = _windows_module()
    forbidden = {os.path.normcase(leaf + suffix) for suffix in suffixes}
    if any(os.path.normcase(name) in forbidden for name in winio.list_names(parent)):
        raise _fail()


def _windows_read(path: Path, maximum: int, forbidden_suffixes: tuple[str, ...] = ()) -> bytes:  # pragma: no cover - native Windows
    winio = _windows_module()
    parent_anchor = parent = handle = verify = 0
    try:
        parent_anchor, parent, _name = winio.pin_directory(path.parent, writable_final=False)
        _windows_revalidate_directory(path.parent, parent)
        _windows_require_absent(parent, path.name, forbidden_suffixes)
        handle = winio.open_file(parent, path.name)
        opened = winio.require_direct(handle, "file")
        if opened.size < 0 or opened.size > maximum:
            raise _fail()
        parts: list[bytes] = []
        total = 0
        while True:
            chunk = winio.read(handle, min(_CHUNK, maximum + 1 - total))
            if not chunk:
                break
            parts.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise _fail()
        after = winio.require_direct(handle, "file")
        _windows_revalidate_directory(path.parent, parent)
        _windows_require_absent(parent, path.name, forbidden_suffixes)
        verify = winio.open_file(parent, path.name)
        named = winio.require_direct(verify, "file")
        if opened.identity != after.identity or opened.identity != named.identity or after.size != total:
            raise _fail()
        return b"".join(parts)
    except (OSError, TypeError, ValueError):
        raise _fail() from None
    finally:
        for item in (verify, handle, parent, parent_anchor):
            if item:
                try:
                    winio.close(item)
                except OSError:
                    pass


def read_bounded_regular(
    path: os.PathLike[str] | str,
    maximum: int,
    *,
    root: os.PathLike[str] | str | None = None,
) -> bytes:
    """Read a direct regular file from a verified handle, bounded by bytes.

    When ``root`` is supplied, the lexical absolute target must remain beneath
    that root; the subsequent component-wise opens still reject indirect
    components rather than relying on string containment for security.
    """
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise _fail()
    absolute = _absolute(path)
    _require_beneath(absolute, root)
    return _windows_read(absolute, maximum) if os.name == "nt" else _posix_read(absolute, maximum)


def read_bounded_regular_excluding_siblings(
    path: os.PathLike[str] | str,
    maximum: int,
    *,
    forbidden_suffixes: tuple[str, ...],
) -> bytes:
    """Read exact verified bytes while requiring named sidecars to remain absent."""
    if (not isinstance(forbidden_suffixes, tuple)
            or any(type(suffix) is not str or not suffix or "/" in suffix or "\\" in suffix or "\x00" in suffix
                   for suffix in forbidden_suffixes)):
        raise _fail()
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise _fail()
    absolute = _absolute(path)
    return (_windows_read(absolute, maximum, forbidden_suffixes) if os.name == "nt"
            else _posix_read(absolute, maximum, forbidden_suffixes))


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise _fail()
        view = view[written:]


def _component(name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise _fail()
    return name


def _temp_name() -> str:
    return ".tmp-" + secrets.token_hex(16)


def _posix_publish(destination: Path, payload: bytes) -> None:
    parent_fd, directories = _posix_open_directory(destination.parent)
    temp_name = _temp_name()
    descriptor = -1
    temp_identity: tuple[int, int] | None = None
    temp_fingerprint: tuple[int, int, int, int, int] | None = None
    published_identity: tuple[int, int] | None = None
    published = False
    try:
        _posix_revalidate_chain(destination.parent, directories)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _optional_flag("O_CLOEXEC") | _optional_flag("O_BINARY")
        descriptor = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
        created_info = os.fstat(descriptor)
        # Capture ownership before the first payload syscall.  The fingerprint
        # is refreshed after the durable write for the pre-link check, while
        # cleanup deliberately authorizes deletion by inode identity: a write
        # can change ctime, and a hard link necessarily does.
        temp_identity = _identity(created_info)
        temp_fingerprint = _file_fingerprint(created_info)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        written_info = os.fstat(descriptor)
        temp_identity = _identity(written_info)
        temp_fingerprint = _file_fingerprint(written_info)
        os.close(descriptor)
        descriptor = -1
        _posix_revalidate_chain(destination.parent, directories)
        closed_named = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if temp_fingerprint is None or _file_fingerprint(closed_named) != temp_fingerprint:
            raise _fail()
        if os.link not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd:
            raise _fail()
        try:
            os.link(temp_name, destination.name, src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd, follow_symlinks=False)
        except BaseException:
            # A syscall wrapper can fail after the link took effect.  Recover
            # cleanup authority only when the final still identifies our
            # captured temp inode; never remove an intervening winner.
            if temp_identity is not None:
                try:
                    linked = os.stat(destination.name, dir_fd=parent_fd,
                                     follow_symlinks=False)
                    if _identity(linked) == temp_identity:
                        published_identity = temp_identity
                except OSError:
                    pass
            raise
        published = True
        published_identity = temp_identity
        temp_named = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        destination_named = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        _posix_revalidate_chain(destination.parent, directories)
        if _identity(temp_named) != temp_identity or _identity(destination_named) != temp_identity:
            raise _fail()
        os.unlink(temp_name, dir_fd=parent_fd)
        destination_after = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(destination_after) != temp_identity:
            raise _fail()
        temp_identity = None
        _posix_revalidate_chain(destination.parent, directories)
        published_identity = None
    except (OSError, TypeError, ValueError, MemoryError):
        raise _fail() from None
    finally:
        if descriptor >= 0:
            if temp_identity is None:
                try:
                    temp_identity = _identity(os.fstat(descriptor))
                except (OSError, MemoryError):
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_identity is not None:
            try:
                named = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
                # Never delete by a predictable temporary *name*.  The
                # identity captured from our create-new descriptor is the
                # ownership proof, so a raced replacement remains intact even
                # if its metadata happens to resemble the original file.
                if _identity(named) == temp_identity:
                    os.unlink(temp_name, dir_fd=parent_fd)
            except OSError:
                pass
        if published_identity is not None:
            try:
                named = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
                if _identity(named) == published_identity:
                    os.unlink(destination.name, dir_fd=parent_fd)
            except OSError:
                pass
        _close_all(directories)
    if not published:
        raise _fail()


def _windows_publish(destination: Path, payload: bytes) -> None:  # pragma: no cover - native Windows
    winio = _windows_module()
    parent_anchor = parent = payload_handle = control = 0
    temp_name = _temp_name()
    try:
        parent_anchor, parent, _name = winio.pin_directory(destination.parent, writable_final=True)
        payload_handle = winio.create_file(parent, temp_name)
        view = memoryview(payload)
        while view:
            written = winio.write(payload_handle, view[:_CHUNK])
            if written <= 0:
                raise _fail()
            view = view[written:]
        winio.flush(payload_handle)
        winio.close(payload_handle)
        payload_handle = 0
        # Reopen solely as the identity-control handle.  It performs no payload
        # I/O; rename is create-new and cleanup targets this same identity.
        control = winio.open_file(parent, temp_name, delete_access=True, share_delete=True, share_write=False)
        original_identity = winio.require_direct(control, "file").identity
        _windows_revalidate_directory(destination.parent, parent)
        winio.rename(control, parent, destination.name, replace=False)
        _windows_revalidate_directory(destination.parent, parent)
        published = winio.open_file(parent, destination.name)
        try:
            if winio.require_direct(control, "file").identity != original_identity or winio.require_direct(published, "file").identity != original_identity:
                raise _fail()
        finally:
            winio.close(published)
        winio.close(control)
        control = 0
    except (OSError, TypeError, ValueError, MemoryError):
        if control:
            try:
                winio.delete(control)
            except OSError:
                pass
        elif payload_handle:
            try:
                winio.delete(payload_handle)
            except OSError:
                pass
        raise _fail() from None
    finally:
        for item in (control, payload_handle, parent, parent_anchor):
            if item:
                try:
                    winio.close(item)
                except OSError:
                    pass


def publish_create_new(destination: os.PathLike[str] | str, payload: bytes) -> None:
    """Atomically publish bytes without replacing an intervening winner."""
    if not isinstance(payload, bytes):
        raise _fail()
    target = _absolute(destination)
    _component(target.name)
    if os.name == "nt":
        _windows_publish(target, payload)
    else:
        _posix_publish(target, payload)


__all__ = [
    "SecureIOError",
    "publish_create_new",
    "read_bounded_regular",
    "read_bounded_regular_excluding_siblings",
]
