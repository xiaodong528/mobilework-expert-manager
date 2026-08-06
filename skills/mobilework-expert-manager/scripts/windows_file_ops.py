#!/usr/bin/env python3
"""Narrow handle-based Win32 file primitives for workspace locking.

The module is importable on every host, but operations fail with one stable
unavailable error outside Windows.  It deliberately exposes no general-purpose
filesystem helpers: callers can only anchor a real directory chain, create or
open one regular child, compare its 128-bit identity, rename that held handle
without replacement, and mark that same handle for deletion.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


# Fixed-width Win32 ABI types.  ctypes.wintypes uses host C widths when this
# module is imported on POSIX, which makes structure-layout tests misleading.
_BOOL = ctypes.c_int32
_BOOLEAN = ctypes.c_uint8
_DWORD = ctypes.c_uint32
_HANDLE = ctypes.c_void_p
_ULONGLONG = ctypes.c_uint64
_WCHAR = ctypes.c_uint16


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
FILE_LIST_DIRECTORY = 0x00000001
FILE_TRAVERSE = 0x00000020
FILE_READ_ATTRIBUTES = 0x00000080

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004

CREATE_NEW = 1
OPEN_EXISTING = 3

FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_WRITE_THROUGH = 0x80000000

FILE_TYPE_DISK = 0x0001

FILE_BEGIN = 0
FILE_RENAME_INFO_CLASS = 3
FILE_DISPOSITION_INFO_CLASS = 4
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_ID_INFO_CLASS = 18

ERROR_ACCESS_DENIED = 5
ERROR_INVALID_FUNCTION = 1
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_SHARING_VIOLATION = 32
ERROR_HANDLE_EOF = 38
ERROR_NOT_SUPPORTED = 50
ERROR_FILE_EXISTS = 80
ERROR_ALREADY_EXISTS = 183

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_MAX_IO_BYTES = 1024 * 1024


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", _DWORD),
        ("ReparseTag", _DWORD),
    ]


class _FILE_ID_128(ctypes.Structure):
    _fields_ = [("Identifier", ctypes.c_ubyte * 16)]


class _FILE_ID_INFO(ctypes.Structure):
    _fields_ = [
        ("VolumeSerialNumber", _ULONGLONG),
        ("FileId", _FILE_ID_128),
    ]


class _FILE_RENAME_UNION(ctypes.Union):
    _fields_ = [
        ("ReplaceIfExists", _BOOLEAN),
        ("Flags", _DWORD),
    ]


class _FILE_RENAME_INFO(ctypes.Structure):
    _anonymous_ = ("Disposition",)
    _fields_ = [
        ("Disposition", _FILE_RENAME_UNION),
        ("RootDirectory", _HANDLE),
        ("FileNameLength", _DWORD),
        ("FileName", _WCHAR * 1),
    ]


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", _BOOLEAN)]


class WindowsFileOpsError(RuntimeError):
    """Stable failure raised by the narrow Windows filesystem backend."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        winerror: int | None = None,
    ) -> None:
        self.code = code
        self.winerror = winerror
        super().__init__(message)


class WindowsFileOpsUnavailable(WindowsFileOpsError):
    """Raised when the required Win32 primitives are not available."""

    def __init__(self, message: str = "Win32 file operations are unavailable") -> None:
        super().__init__("WINDOWS_FILE_OPS_UNAVAILABLE", message)


@dataclass(frozen=True)
class WindowsFileIdentity:
    """One Windows file identity: volume serial plus the full 128-bit file ID."""

    volume_serial_number: int
    file_id: bytes

    def __post_init__(self) -> None:
        if len(self.file_id) != 16:
            raise ValueError("Windows file identity must contain a 128-bit file ID")


def _error_from_winerror(operation: str, winerror: int) -> WindowsFileOpsError:
    if winerror in {ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS}:
        code = "WINDOWS_FILE_EXISTS"
    elif winerror in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}:
        code = "WINDOWS_FILE_NOT_FOUND"
    elif winerror == ERROR_SHARING_VIOLATION:
        code = "WINDOWS_FILE_BUSY"
    elif winerror == ERROR_ACCESS_DENIED:
        code = "WINDOWS_FILE_ACCESS_DENIED"
    elif winerror in {ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED}:
        code = "WINDOWS_FILE_OPERATION_UNSUPPORTED"
    else:
        code = "WINDOWS_FILE_OPERATION_FAILED"
    return WindowsFileOpsError(
        code,
        f"{operation} failed with Windows error {winerror}",
        winerror=winerror,
    )


def _safe_component(value: str) -> str:
    if (
        not isinstance(value, str)
        or value in {"", ".", ".."}
        or value.endswith((" ", "."))
        or any(item in value for item in ("/", "\\", ":", "\x00"))
    ):
        raise WindowsFileOpsError(
            "WINDOWS_FILE_INVALID_COMPONENT",
            "Windows child name must be one safe path component",
        )
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{index}" for index in range(1, 10))
    reserved.update(f"LPT{index}" for index in range(1, 10))
    if stem in reserved:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_INVALID_COMPONENT",
            "Windows child name uses a reserved device component",
        )
    return value


def _native_path(path: Path) -> str:
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _rename_info_buffer(
    root_handle: int,
    target_name: str,
) -> ctypes.Array[ctypes.c_char]:
    name = _safe_component(target_name)
    encoded = name.encode("utf-16-le", errors="strict")
    if not encoded or len(encoded) > 0xFFFFFFFF:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_INVALID_COMPONENT",
            "Windows rename target has an invalid UTF-16 length",
        )
    file_name_offset = _FILE_RENAME_INFO.FileName.offset
    # SetFileInformationByHandle validates the complete native structure size,
    # not only the offset of the flexible FileName member.
    buffer = ctypes.create_string_buffer(ctypes.sizeof(_FILE_RENAME_INFO) + len(encoded))
    header = ctypes.cast(buffer, ctypes.POINTER(_FILE_RENAME_INFO)).contents
    header.ReplaceIfExists = 0
    header.RootDirectory = root_handle
    header.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + file_name_offset, encoded, len(encoded))
    return buffer


def _absolute_rename_info_buffer(
    target_path: Path,
) -> ctypes.Array[ctypes.c_char]:
    encoded = _native_path(target_path).encode("utf-16-le", errors="strict")
    if not encoded or len(encoded) > 0xFFFFFFFF:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_INVALID_PATH",
            "Windows rename target has an invalid UTF-16 length",
        )
    buffer = ctypes.create_string_buffer(
        ctypes.sizeof(_FILE_RENAME_INFO) + len(encoded)
    )
    header = ctypes.cast(buffer, ctypes.POINTER(_FILE_RENAME_INFO)).contents
    header.ReplaceIfExists = 0
    header.RootDirectory = None
    header.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + _FILE_RENAME_INFO.FileName.offset,
        encoded,
        len(encoded),
    )
    return buffer


class _Kernel32Api:  # pragma: no cover - exercised only by Windows CI/VM
    """Typed Kernel32 calls kept behind a mockable high-level adapter."""

    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise WindowsFileOpsUnavailable()
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._create_file = kernel32.CreateFileW
            self._close_handle = kernel32.CloseHandle
            self._get_file_type = kernel32.GetFileType
            self._get_file_information = kernel32.GetFileInformationByHandleEx
            self._set_file_information = kernel32.SetFileInformationByHandle
            self._set_file_pointer = kernel32.SetFilePointerEx
            self._set_end_of_file = kernel32.SetEndOfFile
            self._read_file = kernel32.ReadFile
            self._write_file = kernel32.WriteFile
            self._flush_file_buffers = kernel32.FlushFileBuffers
        except (AttributeError, OSError) as exc:
            raise WindowsFileOpsUnavailable(
                "required Kernel32 file operations are unavailable"
            ) from exc

        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            _DWORD,
            _DWORD,
            ctypes.c_void_p,
            _DWORD,
            _DWORD,
            _HANDLE,
        ]
        self._create_file.restype = _HANDLE
        self._close_handle.argtypes = [_HANDLE]
        self._close_handle.restype = _BOOL
        self._get_file_type.argtypes = [_HANDLE]
        self._get_file_type.restype = _DWORD
        self._get_file_information.argtypes = [
            _HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            _DWORD,
        ]
        self._get_file_information.restype = _BOOL
        self._set_file_information.argtypes = [
            _HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            _DWORD,
        ]
        self._set_file_information.restype = _BOOL
        self._set_file_pointer.argtypes = [
            _HANDLE,
            ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64),
            _DWORD,
        ]
        self._set_file_pointer.restype = _BOOL
        self._set_end_of_file.argtypes = [_HANDLE]
        self._set_end_of_file.restype = _BOOL
        self._read_file.argtypes = [
            _HANDLE,
            ctypes.c_void_p,
            _DWORD,
            ctypes.POINTER(_DWORD),
            ctypes.c_void_p,
        ]
        self._read_file.restype = _BOOL
        self._write_file.argtypes = [
            _HANDLE,
            ctypes.c_void_p,
            _DWORD,
            ctypes.POINTER(_DWORD),
            ctypes.c_void_p,
        ]
        self._write_file.restype = _BOOL
        self._flush_file_buffers.argtypes = [_HANDLE]
        self._flush_file_buffers.restype = _BOOL

    @staticmethod
    def _last_error(operation: str) -> WindowsFileOpsError:
        return _error_from_winerror(operation, ctypes.get_last_error())

    def create_file(
        self,
        path: Path,
        *,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags: int,
        operation: str,
    ) -> int:
        raw = self._create_file(
            _native_path(path),
            desired_access,
            share_mode,
            None,
            creation_disposition,
            flags,
            None,
        )
        if raw in {None, INVALID_HANDLE_VALUE}:
            raise self._last_error(operation)
        return int(raw)

    def close(self, handle: int) -> None:
        if not self._close_handle(handle):
            raise self._last_error("CloseHandle")

    def file_type(self, handle: int) -> int:
        ctypes.set_last_error(0)
        value = int(self._get_file_type(handle))
        if value == 0:
            error = ctypes.get_last_error()
            if error:
                raise _error_from_winerror("GetFileType", error)
        return value

    def attribute_tag(self, handle: int) -> tuple[int, int]:
        value = _FILE_ATTRIBUTE_TAG_INFO()
        if not self._get_file_information(
            handle,
            FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise self._last_error("GetFileInformationByHandleEx(FileAttributeTagInfo)")
        return int(value.FileAttributes), int(value.ReparseTag)

    def identity(self, handle: int) -> WindowsFileIdentity:
        value = _FILE_ID_INFO()
        if not self._get_file_information(
            handle,
            FILE_ID_INFO_CLASS,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            error = self._last_error("GetFileInformationByHandleEx(FileIdInfo)")
            if error.winerror in {ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED}:
                raise WindowsFileOpsError(
                    "WINDOWS_FILE_ID_UNAVAILABLE",
                    "filesystem does not provide the required 128-bit file identity",
                    winerror=error.winerror,
                ) from error
            raise error
        return WindowsFileIdentity(
            int(value.VolumeSerialNumber),
            bytes(value.FileId.Identifier),
        )

    def _seek_start(self, handle: int) -> None:
        new_position = ctypes.c_int64()
        if not self._set_file_pointer(
            handle,
            0,
            ctypes.byref(new_position),
            FILE_BEGIN,
        ):
            raise self._last_error("SetFilePointerEx")

    def read(self, handle: int, max_bytes: int) -> bytes:
        self._seek_start(handle)
        chunks: list[bytes] = []
        consumed = 0
        while True:
            request = min(65536, max_bytes + 1 - consumed)
            if request <= 0:
                raise WindowsFileOpsError(
                    "WINDOWS_FILE_TOO_LARGE",
                    "Windows lock document exceeds the read limit",
                )
            buffer = ctypes.create_string_buffer(request)
            count = _DWORD()
            if not self._read_file(
                handle,
                buffer,
                request,
                ctypes.byref(count),
                None,
            ):
                error_number = ctypes.get_last_error()
                if error_number == ERROR_HANDLE_EOF:
                    break
                raise _error_from_winerror("ReadFile", error_number)
            read_count = int(count.value)
            if read_count == 0:
                break
            chunks.append(buffer.raw[:read_count])
            consumed += read_count
            if consumed > max_bytes:
                raise WindowsFileOpsError(
                    "WINDOWS_FILE_TOO_LARGE",
                    "Windows lock document exceeds the read limit",
                )
        return b"".join(chunks)

    def write(self, handle: int, payload: bytes) -> None:
        self._seek_start(handle)
        offset = 0
        while offset < len(payload):
            chunk = payload[offset : offset + 65536]
            buffer = ctypes.create_string_buffer(chunk)
            count = _DWORD()
            if not self._write_file(
                handle,
                buffer,
                len(chunk),
                ctypes.byref(count),
                None,
            ):
                raise self._last_error("WriteFile")
            written = int(count.value)
            if written <= 0:
                raise WindowsFileOpsError(
                    "WINDOWS_FILE_SHORT_WRITE",
                    "WriteFile completed without writing lock bytes",
                )
            offset += written
        if not self._set_end_of_file(handle):
            raise self._last_error("SetEndOfFile")

    def flush(self, handle: int) -> None:
        if not self._flush_file_buffers(handle):
            raise self._last_error("FlushFileBuffers")

    def rename_no_replace(
        self,
        handle: int,
        target_path: Path,
    ) -> None:
        buffer = _absolute_rename_info_buffer(target_path)
        if not self._set_file_information(
            handle,
            FILE_RENAME_INFO_CLASS,
            ctypes.byref(buffer),
            ctypes.sizeof(buffer),
        ):
            raise self._last_error("SetFileInformationByHandle(FileRenameInfo)")

    def mark_delete_on_close(self, handle: int) -> None:
        value = _FILE_DISPOSITION_INFO(DeleteFile=1)
        if not self._set_file_information(
            handle,
            FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            raise self._last_error("SetFileInformationByHandle(FileDispositionInfo)")


@lru_cache(maxsize=1)
def _api() -> _Kernel32Api:
    return _Kernel32Api()


def available() -> bool:
    if os.name != "nt":
        return False
    try:
        _api()
    except WindowsFileOpsUnavailable:
        return False
    return True


def _require_api() -> _Kernel32Api:
    if os.name != "nt":
        raise WindowsFileOpsUnavailable()
    return _api()


def _validate_directory(api: Any, handle: int) -> WindowsFileIdentity:
    attributes, _tag = api.attribute_tag(handle)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN",
            "Windows directory chain contains a reparse point",
        )
    if not attributes & FILE_ATTRIBUTE_DIRECTORY or api.file_type(handle) != FILE_TYPE_DISK:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_NOT_DIRECTORY",
            "Windows directory anchor is not a real disk directory",
        )
    return api.identity(handle)


def _validate_regular(api: Any, handle: int) -> WindowsFileIdentity:
    attributes, _tag = api.attribute_tag(handle)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN",
            "Windows lock entry is a reparse point",
        )
    if attributes & FILE_ATTRIBUTE_DIRECTORY or api.file_type(handle) != FILE_TYPE_DISK:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_NOT_REGULAR",
            "Windows lock entry is not a regular disk file",
        )
    return api.identity(handle)


@dataclass
class _AnchoredDirectoryHandle:
    path: Path
    raw_handle: int
    identity: WindowsFileIdentity


class WindowsDirectoryAnchor:
    """Pinned, reparse-free absolute directory chain."""

    def __init__(
        self,
        *,
        path: Path,
        api: Any,
        handles: list[_AnchoredDirectoryHandle],
    ) -> None:
        self.path = path
        self._api = api
        self._handles = handles
        self._closed = False

    def __enter__(self) -> WindowsDirectoryAnchor:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False

    @property
    def raw_handle(self) -> int:
        if self._closed or not self._handles:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_ANCHOR_CLOSED",
                "Windows directory anchor is closed",
            )
        return self._handles[-1].raw_handle

    @property
    def identity(self) -> WindowsFileIdentity:
        if self._closed or not self._handles:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_ANCHOR_CLOSED",
                "Windows directory anchor is closed",
            )
        return self._handles[-1].identity

    def child(self, name: str) -> tuple[Path, str]:
        component = _safe_component(name)
        return self.path / component, component

    def child_path(self, name: str) -> Path:
        return self.child(name)[0]

    def assert_safe(self) -> None:
        if self._closed:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_ANCHOR_CLOSED",
                "Windows directory anchor is closed",
            )
        for entry in self._handles:
            if _validate_directory(self._api, entry.raw_handle) != entry.identity:
                raise WindowsFileOpsError(
                    "WINDOWS_FILE_IDENTITY_CHANGED",
                    "Windows directory anchor identity changed",
                )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: WindowsFileOpsError | None = None
        for entry in reversed(self._handles):
            try:
                self._api.close(entry.raw_handle)
            except WindowsFileOpsError as exc:
                if first_error is None:
                    first_error = exc
        self._handles.clear()
        if first_error is not None:
            raise first_error


class WindowsFileHandle:
    """One held regular-file handle whose identity survives name changes."""

    def __init__(
        self,
        *,
        path: Path,
        api: Any,
        raw_handle: int,
        identity: WindowsFileIdentity,
        delete_access: bool,
    ) -> None:
        self.path = path
        self._api = api
        self._raw_handle: int | None = raw_handle
        self._identity = identity
        self._delete_access = delete_access

    def __enter__(self) -> WindowsFileHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.close()
        return False

    @property
    def raw_handle(self) -> int:
        if self._raw_handle is None:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_HANDLE_CLOSED",
                "Windows file handle is closed",
            )
        return self._raw_handle

    def _assert_identity(self) -> None:
        if _validate_regular(self._api, self.raw_handle) != self._identity:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_IDENTITY_CHANGED",
                "Windows lock handle identity changed",
            )

    def read(self, max_bytes: int = _MAX_IO_BYTES) -> bytes:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._assert_identity()
        return self._api.read(self.raw_handle, max_bytes)

    def write(self, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > _MAX_IO_BYTES:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_TOO_LARGE",
                "Windows lock document exceeds the write limit",
            )
        self._assert_identity()
        self._api.write(self.raw_handle, payload)
        self._assert_identity()

    def flush(self) -> None:
        self._assert_identity()
        self._api.flush(self.raw_handle)

    def identity(self) -> WindowsFileIdentity:
        self._assert_identity()
        return self._identity

    def rename_no_replace(
        self,
        anchor: WindowsDirectoryAnchor,
        target_name: str,
    ) -> Path:
        if not self._delete_access:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_DELETE_ACCESS_REQUIRED",
                "Windows handle was not opened with DELETE access",
            )
        target_path, _component = anchor.child(target_name)
        anchor.assert_safe()
        self._assert_identity()
        self._api.rename_no_replace(
            self.raw_handle,
            target_path,
        )
        self.path = target_path
        self._assert_identity()
        anchor.assert_safe()
        return target_path

    def mark_delete_on_close(self) -> None:
        if not self._delete_access:
            raise WindowsFileOpsError(
                "WINDOWS_FILE_DELETE_ACCESS_REQUIRED",
                "Windows handle was not opened with DELETE access",
            )
        self._assert_identity()
        self._api.mark_delete_on_close(self.raw_handle)

    def close(self) -> None:
        raw_handle, self._raw_handle = self._raw_handle, None
        if raw_handle is not None:
            self._api.close(raw_handle)


def _directory_prefixes(path: Path) -> tuple[Path, list[Path]]:
    candidate = path.expanduser()
    absolute = Path(os.path.abspath(os.fspath(candidate)))
    if not absolute.anchor:
        raise WindowsFileOpsError(
            "WINDOWS_FILE_INVALID_PATH",
            "Windows directory anchor must be absolute",
        )
    current = Path(absolute.anchor)
    prefixes = [current]
    for component in absolute.parts[1:]:
        current = current / component
        prefixes.append(current)
    return absolute, prefixes


def open_directory_chain_no_reparse(path: Path | str) -> WindowsDirectoryAnchor:
    api = _require_api()
    absolute, prefixes = _directory_prefixes(Path(path))
    handles: list[_AnchoredDirectoryHandle] = []
    try:
        for prefix in prefixes:
            raw_handle = api.create_file(
                prefix,
                desired_access=(
                    FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES
                ),
                share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE,
                creation_disposition=OPEN_EXISTING,
                flags=FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
                operation="CreateFileW(directory-anchor)",
            )
            try:
                identity = _validate_directory(api, raw_handle)
            except BaseException:
                api.close(raw_handle)
                raise
            handles.append(_AnchoredDirectoryHandle(prefix, raw_handle, identity))
        result = WindowsDirectoryAnchor(path=absolute, api=api, handles=handles)
        result.assert_safe()
        return result
    except BaseException:
        for entry in reversed(handles):
            try:
                api.close(entry.raw_handle)
            except WindowsFileOpsError:
                pass
        raise


def create_exclusive_regular(
    anchor: WindowsDirectoryAnchor,
    name: str,
) -> WindowsFileHandle:
    path, _component = anchor.child(name)
    anchor.assert_safe()
    api = anchor._api
    raw_handle = api.create_file(
        path,
        desired_access=GENERIC_READ | GENERIC_WRITE | DELETE | FILE_READ_ATTRIBUTES,
        share_mode=FILE_SHARE_READ,
        creation_disposition=CREATE_NEW,
        flags=FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_WRITE_THROUGH,
        operation="CreateFileW(exclusive-regular)",
    )
    try:
        identity = _validate_regular(api, raw_handle)
        anchor.assert_safe()
    except BaseException:
        api.close(raw_handle)
        raise
    return WindowsFileHandle(
        path=path,
        api=api,
        raw_handle=raw_handle,
        identity=identity,
        delete_access=True,
    )


def open_existing_regular_no_reparse(
    anchor: WindowsDirectoryAnchor,
    name: str,
    delete_access: bool = False,
) -> WindowsFileHandle:
    if not isinstance(delete_access, bool):
        raise TypeError("delete_access must be bool")
    path, _component = anchor.child(name)
    anchor.assert_safe()
    api = anchor._api
    desired_access = GENERIC_READ | FILE_READ_ATTRIBUTES
    share_mode = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    if delete_access:
        desired_access |= DELETE
        # A delete-capable handle pins the exact entry while it is inspected and
        # renamed.  Readers can still open it, but no second deleter can race it.
        share_mode = FILE_SHARE_READ
    raw_handle = api.create_file(
        path,
        desired_access=desired_access,
        share_mode=share_mode,
        creation_disposition=OPEN_EXISTING,
        flags=FILE_FLAG_OPEN_REPARSE_POINT,
        operation="CreateFileW(existing-regular)",
    )
    try:
        identity = _validate_regular(api, raw_handle)
        anchor.assert_safe()
    except BaseException:
        api.close(raw_handle)
        raise
    return WindowsFileHandle(
        path=path,
        api=api,
        raw_handle=raw_handle,
        identity=identity,
        delete_access=delete_access,
    )
