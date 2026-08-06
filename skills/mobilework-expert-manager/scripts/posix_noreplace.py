#!/usr/bin/env python3
"""Atomic POSIX rename-without-replacement shared by manager writers."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from functools import lru_cache
from typing import Any


class NoReplaceUnavailable(RuntimeError):
    """Raised when the host/filesystem cannot provide verified no-replace rename."""


def _component(value: str, field: str) -> bytes:
    if (
        not isinstance(value, str)
        or value in {"", ".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{field} must be one safe path component")
    return os.fsencode(value)


@lru_cache(maxsize=1)
def _backend() -> tuple[Any, int]:
    if os.name != "posix":
        raise NoReplaceUnavailable("atomic no-replace rename requires POSIX")
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(library, "renameatx_np"):
        function = library.renameatx_np
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        return function, 0x00000004  # RENAME_EXCL
    if sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        function = library.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        return function, 0x00000001  # RENAME_NOREPLACE
    raise NoReplaceUnavailable(
        "host has no verified atomic no-replace rename primitive"
    )


def require_available() -> None:
    _backend()


def available() -> bool:
    try:
        require_available()
    except NoReplaceUnavailable:
        return False
    return True


def rename(
    source_fd: int,
    source_name: str,
    target_fd: int,
    target_name: str,
) -> None:
    """Rename one dirfd-anchored entry only when the target name is absent."""

    function, flag = _backend()
    result = function(
        source_fd,
        _component(source_name, "source_name"),
        target_fd,
        _component(target_name, "target_name"),
        flag,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    # The wrapper fixes both the flag and path shape, so EINVAL here means the
    # kernel/filesystem rejected the verified no-replace operation itself.
    unsupported_errors = {errno.EINVAL, errno.ENOSYS}
    if hasattr(errno, "ENOTSUP"):
        unsupported_errors.add(errno.ENOTSUP)
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported_errors.add(errno.EOPNOTSUPP)
    if error_number in unsupported_errors:
        raise NoReplaceUnavailable(
            "filesystem has no verified atomic no-replace rename support"
        )
    raise OSError(error_number, os.strerror(error_number), target_name)
