"""Small ctypes wrapper for handle-relative private-tree I/O on Windows.

The public Win32 file APIs do not expose an ``openat`` equivalent.  NT's
``NtCreateFile`` does: an ``OBJECT_ATTRIBUTES.RootDirectory`` handle confines
each single-component lookup to an already-open directory.  This module keeps
that sharp edge in one place for the atomic iMessage portable-tree writer.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path


if os.name != "nt":  # pragma: no cover - imported only by the Windows backend
    raise ImportError("windows_descriptor_io is Windows-only")


ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
OBJ_CASE_INSENSITIVE = 0x00000040
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_APPEND_DATA = 0x0004
FILE_READ_EA = 0x0008
FILE_WRITE_EA = 0x0010
FILE_EXECUTE = 0x0020
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
DELETE = 0x00010000
READ_CONTROL = 0x00020000
SYNCHRONIZE = 0x00100000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
FILE_OPEN = 0x1
FILE_CREATE = 0x2
FILE_OPEN_IF = 0x3
FILE_DIRECTORY_FILE = 0x00000001
FILE_WRITE_THROUGH = 0x00000002
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
OPEN_EXISTING = 3
FILE_BEGIN = 0
FILE_CURRENT = 1
FILE_END = 2
FILE_RENAME_INFORMATION_EX_CLASS = 65
FILE_RENAME_FLAG_REPLACE_IF_EXISTS = 0x1
FILE_RENAME_FLAG_POSIX_SEMANTICS = 0x2
FILE_DISPOSITION_INFO_CLASS = 4
FILE_NAMES_INFORMATION_CLASS = 12
FILE_DIRECTORY_INFORMATION_CLASS = 1
STATUS_NO_MORE_FILES = ctypes.c_long(0x80000006).value


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class IO_STATUS_BLOCK(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


ntdll.NtCreateFile.argtypes = [
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
    ctypes.POINTER(OBJECT_ATTRIBUTES), ctypes.POINTER(IO_STATUS_BLOCK),
    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
    wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
]
ntdll.NtCreateFile.restype = ctypes.c_long
ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
ntdll.NtQueryDirectoryFile.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p, wintypes.ULONG,
    wintypes.ULONG, wintypes.BOOLEAN, ctypes.c_void_p, wintypes.BOOLEAN,
]
ntdll.NtQueryDirectoryFile.restype = ctypes.c_long
ntdll.NtSetInformationFile.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p,
    wintypes.ULONG, wintypes.ULONG,
]
ntdll.NtSetInformationFile.restype = ctypes.c_long
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetFileInformationByHandle.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)
]
kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD
]
kernel32.SetFilePointerEx.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.SetFileInformationByHandle.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
]
kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.DuplicateHandle.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
]
kernel32.DuplicateHandle.restype = wintypes.BOOL


def _win_error(code: int | None = None) -> OSError:
    return ctypes.WinError(ctypes.get_last_error() if code is None else code)


def _nt_error(status: int) -> OSError:
    return _win_error(int(ntdll.RtlNtStatusToDosError(status)))


def _valid_component(name: str) -> str:
    if (
        type(name) is not str or not name or name in {".", ".."}
        or "\\" in name or "/" in name or ":" in name or "\x00" in name
    ):
        raise ValueError("Windows relative name is not one component")
    return name


@dataclass(frozen=True)
class NodeInfo:
    kind: str
    volume_serial: int
    file_id: int
    size: int
    creation_time: int
    write_time: int
    attributes: int
    links: int

    @property
    def identity(self) -> tuple[int, int, int, int, int, int, int, int]:
        mode = 0o40700 if self.kind == "directory" else 0o100600
        return (
            self.volume_serial, self.file_id, self.size, self.write_time,
            self.creation_time, mode, self.links, self.attributes,
        )


def close(handle: int) -> None:
    if handle and handle != INVALID_HANDLE_VALUE and not kernel32.CloseHandle(handle):
        raise _win_error()


def duplicate(handle: int) -> int:
    current = kernel32.GetCurrentProcess()
    result = wintypes.HANDLE()
    if not kernel32.DuplicateHandle(
        current, handle, current, ctypes.byref(result), 0, False, 0x2
    ):
        raise _win_error()
    return int(result.value)


def info(handle: int) -> NodeInfo:
    raw = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(raw)):
        raise _win_error()
    attrs = int(raw.dwFileAttributes)
    kind = "directory" if attrs & FILE_ATTRIBUTE_DIRECTORY else "file"
    return NodeInfo(
        kind=kind,
        volume_serial=int(raw.dwVolumeSerialNumber),
        file_id=(int(raw.nFileIndexHigh) << 32) | int(raw.nFileIndexLow),
        size=(int(raw.nFileSizeHigh) << 32) | int(raw.nFileSizeLow),
        creation_time=(int(raw.ftCreationTime.dwHighDateTime) << 32)
        | int(raw.ftCreationTime.dwLowDateTime),
        write_time=(int(raw.ftLastWriteTime.dwHighDateTime) << 32)
        | int(raw.ftLastWriteTime.dwLowDateTime),
        attributes=attrs,
        links=int(raw.nNumberOfLinks),
    )


def require_direct(
    handle: int, kind: str, *, allow_multiple_links: bool = False,
) -> NodeInfo:
    value = info(handle)
    if value.kind != kind or value.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError("private-tree node is indirected or has the wrong kind")
    if kind == "file" and not allow_multiple_links and value.links != 1:
        raise OSError("private-tree file has multiple hard links")
    return value


def _nt_open(
    parent: int,
    name: str,
    *,
    kind: str | None,
    create: bool,
    writable: bool = False,
    delete_access: bool = False,
    share_delete: bool = True,
    share_write: bool = True,
    allow_multiple_links: bool = False,
) -> int:
    component = _valid_component(name)
    buffer = ctypes.create_unicode_buffer(component)
    encoded_length = len(component.encode("utf-16-le"))
    unicode_name = UNICODE_STRING(encoded_length, encoded_length, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = OBJECT_ATTRIBUTES(
        ctypes.sizeof(OBJECT_ATTRIBUTES), parent, ctypes.pointer(unicode_name),
        OBJ_CASE_INSENSITIVE, None, None,
    )
    iosb = IO_STATUS_BLOCK()
    result = wintypes.HANDLE()
    desired = FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE
    if create or delete_access:
        desired |= DELETE
    options = FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_FOR_BACKUP_INTENT
    if kind == "directory":
        desired |= FILE_READ_DATA | FILE_EXECUTE
        if writable or create: desired |= FILE_WRITE_DATA | FILE_APPEND_DATA
        options |= FILE_DIRECTORY_FILE
    elif kind == "file":
        desired |= FILE_READ_DATA
        if writable or create:
            desired |= FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_ATTRIBUTES
        options |= FILE_NON_DIRECTORY_FILE
    else:
        desired |= FILE_READ_DATA
    if writable or create:
        options |= FILE_WRITE_THROUGH
    status = int(ntdll.NtCreateFile(
        ctypes.byref(result), desired, ctypes.byref(attributes), ctypes.byref(iosb),
        None,
        FILE_ATTRIBUTE_NORMAL,
        FILE_SHARE_READ
        | (FILE_SHARE_WRITE if share_write else 0)
        | (FILE_SHARE_DELETE if share_delete else 0),
        FILE_CREATE if create else FILE_OPEN, options, None, 0,
    ))
    if status < 0:
        raise _nt_error(status)
    handle = int(result.value)
    try:
        if kind is not None:
            require_direct(handle, kind, allow_multiple_links=allow_multiple_links)
        elif info(handle).attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("private-tree node is indirected")
        return handle
    except BaseException:
        close(handle)
        raise


def open_directory(
    parent: int, name: str, *, writable: bool = False, delete_access: bool = False
) -> int:
    return _nt_open(
        parent, name, kind="directory", create=False, writable=writable, delete_access=delete_access
    )


def create_directory(parent: int, name: str) -> int:
    return _nt_open(parent, name, kind="directory", create=True, writable=True)


def open_file(
    parent: int,
    name: str,
    *,
    writable: bool = False,
    delete_access: bool = False,
    share_delete: bool = True,
    share_write: bool = True,
    allow_multiple_links: bool = False,
) -> int:
    return _nt_open(
        parent, name, kind="file", create=False,
        writable=writable,
        delete_access=delete_access,
        share_delete=share_delete,
        share_write=share_write,
        allow_multiple_links=allow_multiple_links,
    )


def create_file(parent: int, name: str) -> int:
    return _nt_open(parent, name, kind="file", create=True, writable=True)


def open_node(parent: int, name: str) -> int:
    return _nt_open(parent, name, kind=None, create=False)


def open_absolute_file(path: Path, *, writable: bool = False) -> int:
    absolute = Path(path).absolute()
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    access = 0x80000000
    if writable:
        access |= 0x40000000
    handle = kernel32.CreateFileW(
        str(absolute), access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, flags, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise _win_error()
    value = int(handle)
    try:
        require_direct(value, "file")
        return value
    except BaseException:
        close(value)
        raise


def pin_directory(path: Path, *, writable_final: bool = True) -> tuple[int, int, str]:
    absolute = Path(path).absolute()
    if ".." in absolute.parts or not absolute.drive or not absolute.name:
        raise OSError("private root path is not an absolute drive path")
    drive_root = absolute.anchor.rstrip("\\/") + "\\"
    root = kernel32.CreateFileW(
        "\\\\?\\" + drive_root,
        0x80000000,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if root == INVALID_HANDLE_VALUE:
        raise _win_error()
    current = int(root)
    parent = 0
    try:
        require_direct(current, "directory")
        components = absolute.parts[1:]
        for index, component in enumerate(components):
            following = open_directory(
                current, component, writable=writable_final and index == len(components) - 1
            )
            if index == len(components) - 1:
                parent = current
                current = following
                return parent, current, component
            close(current)
            current = following
        raise OSError("private root path has no final component")
    except BaseException:
        if parent:
            close(parent)
        close(current)
        raise


def pin_directory_chain(path: Path, *, writable_final: bool = True) -> tuple[int, ...]:
    """Retain the drive root and every direct directory component handle."""
    absolute = Path(path).absolute()
    if ".." in absolute.parts or not absolute.drive or not absolute.name:
        raise OSError("private root path is not an absolute drive path")
    drive_root = absolute.anchor.rstrip("\\/") + "\\"
    root = kernel32.CreateFileW(
        "\\\\?\\" + drive_root, 0x80000000,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if root == INVALID_HANDLE_VALUE:
        raise _win_error()
    handles = [int(root)]
    try:
        require_direct(handles[0], "directory")
        components = absolute.parts[1:]
        for index, component in enumerate(components):
            handles.append(open_directory(
                handles[-1], component, writable=writable_final and index == len(components) - 1,
            ))
        return tuple(handles)
    except BaseException:
        for handle in reversed(handles):
            try:
                close(handle)
            except OSError:
                pass
        raise


def revalidate_directory_chain(path: Path, retained: tuple[int, ...]) -> None:
    """Require every currently named ancestor to match its retained handle."""
    probe = pin_directory_chain(path, writable_final=False)
    try:
        if len(probe) != len(retained):
            raise OSError("directory chain length changed")
        for expected, current in zip(retained, probe):
            if require_direct(expected, "directory").identity != require_direct(current, "directory").identity:
                raise OSError("directory chain identity changed")
    finally:
        for handle in reversed(probe):
            close(handle)


def list_names(directory: int) -> tuple[str, ...]:
    names: list[str] = []
    restart = True
    while True:
        buffer = ctypes.create_string_buffer(4096)
        iosb = IO_STATUS_BLOCK()
        status = int(ntdll.NtQueryDirectoryFile(
            directory, None, None, None, ctypes.byref(iosb), buffer,
            len(buffer), FILE_NAMES_INFORMATION_CLASS, True, None, restart,
        ))
        restart = False
        if status == STATUS_NO_MORE_FILES:
            break
        if status < 0:
            raise _nt_error(status)
        length = ctypes.c_ulong.from_buffer(buffer, 8).value
        name = bytes(buffer[12 : 12 + length]).decode("utf-16-le")
        if name not in {".", ".."}:
            names.append(name)
    return tuple(sorted(names, key=lambda value: value.encode("utf-16-le")))


def list_entries(directory: int) -> tuple[tuple[str, int, int, int, int, int], ...]:
    """Enumerate names and non-opening metadata from a retained directory handle."""
    entries: list[tuple[str, int, int, int, int, int]] = []
    restart = True
    while True:
        buffer = ctypes.create_string_buffer(4096)
        iosb = IO_STATUS_BLOCK()
        status = int(ntdll.NtQueryDirectoryFile(
            directory, None, None, None, ctypes.byref(iosb), buffer,
            len(buffer), FILE_DIRECTORY_INFORMATION_CLASS, True, None, restart,
        ))
        restart = False
        if status == STATUS_NO_MORE_FILES:
            break
        if status < 0:
            raise _nt_error(status)
        creation = ctypes.c_longlong.from_buffer(buffer, 8).value
        write_time = ctypes.c_longlong.from_buffer(buffer, 24).value
        change_time = ctypes.c_longlong.from_buffer(buffer, 32).value
        size = ctypes.c_longlong.from_buffer(buffer, 40).value
        attributes = ctypes.c_ulong.from_buffer(buffer, 56).value
        length = ctypes.c_ulong.from_buffer(buffer, 60).value
        name = bytes(buffer[64 : 64 + length]).decode("utf-16-le")
        if name not in {".", ".."}:
            entries.append((name, int(size), int(attributes), int(creation), int(write_time), int(change_time)))
    return tuple(sorted(entries, key=lambda item: item[0].encode("utf-16-le")))


def read(handle: int, size: int) -> bytes:
    if size <= 0:
        return b""
    buffer = ctypes.create_string_buffer(size)
    count = wintypes.DWORD()
    if not kernel32.ReadFile(handle, buffer, size, ctypes.byref(count), None):
        code = ctypes.get_last_error()
        if code == 38:  # ERROR_HANDLE_EOF
            return b""
        raise _win_error(code)
    return buffer.raw[: count.value]


def write(handle: int, raw: bytes | memoryview) -> int:
    data = bytes(raw)
    if not data:
        return 0
    buffer = ctypes.create_string_buffer(data)
    count = wintypes.DWORD()
    if not kernel32.WriteFile(handle, buffer, len(data), ctypes.byref(count), None):
        raise _win_error()
    return int(count.value)


def seek(handle: int, offset: int, whence: int = os.SEEK_SET) -> int:
    method = {os.SEEK_SET: FILE_BEGIN, os.SEEK_CUR: FILE_CURRENT, os.SEEK_END: FILE_END}[whence]
    result = ctypes.c_longlong()
    if not kernel32.SetFilePointerEx(handle, offset, ctypes.byref(result), method):
        raise _win_error()
    return int(result.value)


def flush(handle: int) -> None:
    if not kernel32.FlushFileBuffers(handle):
        raise _win_error()


def rename(handle: int, destination_parent: int, destination: str, *, replace: bool) -> None:
    name = _valid_component(destination)
    encoded = name.encode("utf-16-le")
    # FILE_RENAME_INFO: BOOLEAN, padding to HANDLE, HANDLE, DWORD, WCHAR[].
    raw = ctypes.create_string_buffer(20 + len(encoded))
    ctypes.c_ulong.from_buffer(raw, 0).value = (
        FILE_RENAME_FLAG_POSIX_SEMANTICS | (FILE_RENAME_FLAG_REPLACE_IF_EXISTS if replace else 0)
    )
    ctypes.c_void_p.from_buffer(raw, 8).value = destination_parent
    ctypes.c_ulong.from_buffer(raw, 16).value = len(encoded)
    ctypes.memmove(ctypes.addressof(raw) + 20, encoded, len(encoded))
    iosb = IO_STATUS_BLOCK()
    status = int(ntdll.NtSetInformationFile(
        handle, ctypes.byref(iosb), raw, len(raw), FILE_RENAME_INFORMATION_EX_CLASS
    ))
    if status < 0:
        raise _nt_error(status)


def delete(handle: int) -> None:
    value = wintypes.BOOLEAN(True)
    if not kernel32.SetFileInformationByHandle(
        handle, FILE_DISPOSITION_INFO_CLASS, ctypes.byref(value), ctypes.sizeof(value)
    ):
        raise _win_error()
