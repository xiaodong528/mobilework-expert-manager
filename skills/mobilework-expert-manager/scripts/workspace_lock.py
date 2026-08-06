#!/usr/bin/env python3
"""Fail-closed workspace mutation lock for expert-manager writers.

The lock deliberately has no stale-lock reclamation.  A process crash leaves the
lock document in place so a later writer stops instead of guessing that it is
safe to mutate the workspace.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import manager_contract
import posix_noreplace
import windows_file_ops


LOCK_DOCUMENT_FIELDS = (
    "ownerToken",
    "pid",
    "createdAt",
    "heartbeatAt",
    "protocolVersion",
)


class WorkspaceLockError(RuntimeError):
    """Raised when a workspace mutation lock cannot be handled safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class _WorkspaceLockPolicy:
    file_name: str
    file_mode: int
    protocol_version: int
    fields: tuple[str, ...]


def _raise(code: str, message: str) -> None:
    raise WorkspaceLockError(code, message)


def _load_policy() -> _WorkspaceLockPolicy:
    try:
        raw = manager_contract.load_policy()["workspaceLock"]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock policy cannot be loaded",
        ) from exc
    if not isinstance(raw, dict):
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock policy must be an object",
        )

    file_name = raw.get("fileName")
    if (
        not isinstance(file_name, str)
        or not file_name
        or file_name in {".", ".."}
        or "/" in file_name
        or "\\" in file_name
        or "\x00" in file_name
    ):
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock fileName must be one safe path component",
        )
    protocol_version = raw.get("protocolVersion")
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version <= 0
    ):
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock protocolVersion must be a positive integer",
        )
    file_mode = raw.get("fileMode")
    if (
        isinstance(file_mode, bool)
        or not isinstance(file_mode, int)
        or file_mode <= 0
        or (os.name == "posix" and file_mode != 0o600)
    ):
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock fileMode must be POSIX 0600",
        )
    fields = raw.get("fields")
    if not isinstance(fields, list) or tuple(fields) != LOCK_DOCUMENT_FIELDS:
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock fields do not match protocol v2",
        )
    return _WorkspaceLockPolicy(
        file_name=file_name,
        file_mode=file_mode,
        protocol_version=protocol_version,
        fields=tuple(fields),
    )


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _workspace_path(value: Path | str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path(os.path.abspath(candidate))
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_WORKSPACE_INVALID",
            "workspace directory cannot be inspected",
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        _raise(
            "WORKSPACE_LOCK_WORKSPACE_INVALID",
            "workspace must be a real directory",
        )
    return candidate


def _open_workspace(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_WORKSPACE_INVALID",
            "workspace directory cannot be opened safely",
        ) from exc


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise OSError("short write while creating workspace lock")
        offset += written


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _stat_lock(workspace_fd: int, file_name: str) -> os.stat_result | None:
    try:
        return os.stat(file_name, dir_fd=workspace_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_UNSAFE",
            "existing workspace lock cannot be inspected safely",
        ) from exc


def _existing_lock_error(workspace_fd: int, file_name: str) -> WorkspaceLockError:
    metadata = _stat_lock(workspace_fd, file_name)
    if metadata is None:
        return WorkspaceLockError(
            "WORKSPACE_LOCK_CREATE_FAILED",
            "workspace lock creation lost an atomic race",
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return WorkspaceLockError(
            "WORKSPACE_LOCK_UNSAFE",
            "existing workspace lock is not a regular file",
        )
    return WorkspaceLockError(
        "WORKSPACE_LOCK_HELD",
        "workspace already has an active or stale mutation lock",
    )


def _parse_lock_document(payload: bytes, policy: _WorkspaceLockPolicy) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_INVALID",
            "workspace lock document is not valid canonical JSON",
        ) from exc
    if not isinstance(document, dict) or tuple(sorted(document)) != tuple(
        sorted(policy.fields)
    ):
        _raise(
            "WORKSPACE_LOCK_INVALID",
            "workspace lock document fields do not match the lock contract",
        )
    if document.get("protocolVersion") != policy.protocol_version:
        _raise(
            "WORKSPACE_LOCK_INVALID",
            "workspace lock protocolVersion does not match the manager contract",
        )
    owner_token = document.get("ownerToken")
    if not isinstance(owner_token, str) or not owner_token:
        _raise(
            "WORKSPACE_LOCK_INVALID",
            "workspace lock ownerToken is invalid",
        )
    return document


class WorkspaceMutationLock:
    """A cooperative POSIX lock with owner and identity verification."""

    def __init__(
        self,
        *,
        workspace: Path,
        workspace_fd: int,
        lock_fd: int,
        lock_identity: tuple[int, int],
        policy: _WorkspaceLockPolicy,
        document: dict[str, Any],
    ) -> None:
        self.workspace = workspace
        self.path = workspace / policy.file_name
        self.owner_token = str(document["ownerToken"])
        self.document = dict(document)
        self._workspace_fd: int | None = workspace_fd
        self._workspace_identity = _identity(os.fstat(workspace_fd))
        self._lock_fd: int | None = lock_fd
        self._lock_identity = lock_identity
        self._policy = policy
        self._released = False

    def __enter__(self) -> WorkspaceMutationLock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.release()
        return False

    def _close_descriptors(self) -> None:
        lock_fd, self._lock_fd = self._lock_fd, None
        workspace_fd, self._workspace_fd = self._workspace_fd, None
        first_error: OSError | None = None
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError as exc:
                first_error = exc
        if workspace_fd is not None:
            try:
                os.close(workspace_fd)
            except OSError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def assert_active_owner(self, workspace_dir: Path | str) -> None:
        """Verify that this live handle still owns the lock for one workspace."""

        if self._released or self._workspace_fd is None or self._lock_fd is None:
            _raise("WORKSPACE_LOCK_NOT_OWNER", "workspace lock is no longer active")
        requested = _workspace_path(workspace_dir)
        requested_fd = _open_workspace(requested)
        try:
            if _identity(os.fstat(requested_fd)) != self._workspace_identity:
                _raise(
                    "WORKSPACE_LOCK_WRONG_WORKSPACE",
                    "workspace lock belongs to a different workspace",
                )
        finally:
            os.close(requested_fd)
        metadata = _stat_lock(self._workspace_fd, self._policy.file_name)
        if metadata is None or _identity(metadata) != self._lock_identity:
            _raise("WORKSPACE_LOCK_CHANGED", "workspace lock identity changed")
        if _identity(os.fstat(self._lock_fd)) != self._lock_identity:
            _raise("WORKSPACE_LOCK_CHANGED", "workspace lock handle identity changed")
        verification_fd: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            verification_fd = os.open(
                self._policy.file_name,
                flags,
                dir_fd=self._workspace_fd,
            )
            if _identity(os.fstat(verification_fd)) != self._lock_identity:
                _raise("WORKSPACE_LOCK_CHANGED", "workspace lock identity changed")
            document = _parse_lock_document(
                _read_all(verification_fd),
                self._policy,
            )
            if document["ownerToken"] != self.owner_token:
                _raise("WORKSPACE_LOCK_NOT_OWNER", "workspace lock owner changed")
        except OSError as exc:
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_UNSAFE",
                "workspace lock cannot be verified safely",
            ) from exc
        finally:
            if verification_fd is not None:
                os.close(verification_fd)

    def release(self) -> None:
        """Remove the lock only if identity, protocol, and owner token still match."""

        if self._released:
            return
        workspace_fd = self._workspace_fd
        if workspace_fd is None:
            _raise(
                "WORKSPACE_LOCK_CHANGED",
                "workspace lock descriptors are no longer available",
            )

        verification_fd: int | None = None
        quarantine_fd: int | None = None
        quarantine_name = (
            f"{self._policy.file_name}.release-{self.owner_token}"
        )
        try:
            self.assert_active_owner(self.workspace)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                verification_fd = os.open(
                    self._policy.file_name,
                    flags,
                    dir_fd=workspace_fd,
                )
            except FileNotFoundError as exc:
                raise WorkspaceLockError(
                    "WORKSPACE_LOCK_CHANGED",
                    "workspace lock disappeared before owner release",
                ) from exc
            except OSError as exc:
                raise WorkspaceLockError(
                    "WORKSPACE_LOCK_UNSAFE",
                    "workspace lock cannot be reopened safely",
                ) from exc

            metadata = os.fstat(verification_fd)
            if not stat.S_ISREG(metadata.st_mode):
                _raise(
                    "WORKSPACE_LOCK_UNSAFE",
                    "workspace lock is no longer a regular file",
                )
            if _identity(metadata) != self._lock_identity:
                _raise(
                    "WORKSPACE_LOCK_CHANGED",
                    "workspace lock identity changed before owner release",
                )
            document = _parse_lock_document(
                _read_all(verification_fd),
                self._policy,
            )
            if document["ownerToken"] != self.owner_token:
                _raise(
                    "WORKSPACE_LOCK_NOT_OWNER",
                    "workspace lock owner token no longer matches",
                )

            try:
                posix_noreplace.rename(
                    workspace_fd,
                    self._policy.file_name,
                    workspace_fd,
                    quarantine_name,
                )
                quarantine_fd = os.open(
                    quarantine_name,
                    flags,
                    dir_fd=workspace_fd,
                )
                quarantine_metadata = os.fstat(quarantine_fd)
                quarantine_document = _parse_lock_document(
                    _read_all(quarantine_fd),
                    self._policy,
                )
                if (
                    _identity(quarantine_metadata) != self._lock_identity
                    or quarantine_document["ownerToken"] != self.owner_token
                ):
                    _raise(
                        "WORKSPACE_LOCK_CHANGED",
                        "workspace lock changed during atomic release quarantine",
                    )
                os.unlink(quarantine_name, dir_fd=workspace_fd)
                self._released = True
                os.fsync(workspace_fd)
            except (OSError, posix_noreplace.NoReplaceUnavailable) as exc:
                raise WorkspaceLockError(
                    "WORKSPACE_LOCK_RELEASE_FAILED",
                    "workspace lock could not be removed by its owner",
                ) from exc
        finally:
            if quarantine_fd is not None:
                os.close(quarantine_fd)
            if verification_fd is not None:
                os.close(verification_fd)
            self._close_descriptors()

    def __del__(self) -> None:
        """Close process-local handles without reclaiming a possibly stale lock."""

        try:
            self._close_descriptors()
        except OSError:
            pass


class WindowsWorkspaceMutationLock:
    """Protocol-v2 lock backed by pinned Win32 directory and file handles."""

    def __init__(
        self,
        *,
        workspace: Path,
        anchor: windows_file_ops.WindowsDirectoryAnchor,
        lock_handle: windows_file_ops.WindowsFileHandle,
        policy: _WorkspaceLockPolicy,
        document: dict[str, Any],
    ) -> None:
        self.workspace = workspace
        self.path = workspace / policy.file_name
        self.owner_token = str(document["ownerToken"])
        self.document = dict(document)
        self._workspace_identity = anchor.identity
        self._lock_identity = lock_handle.identity()
        self._anchor: windows_file_ops.WindowsDirectoryAnchor | None = anchor
        self._lock_handle: windows_file_ops.WindowsFileHandle | None = lock_handle
        self._policy = policy
        self._released = False

    def __enter__(self) -> WindowsWorkspaceMutationLock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.release()
        return False

    def _close_handles(self) -> None:
        lock_handle, self._lock_handle = self._lock_handle, None
        anchor, self._anchor = self._anchor, None
        first_error: windows_file_ops.WindowsFileOpsError | None = None
        if lock_handle is not None:
            try:
                lock_handle.close()
            except windows_file_ops.WindowsFileOpsError as exc:
                first_error = exc
        if anchor is not None:
            try:
                anchor.close()
            except windows_file_ops.WindowsFileOpsError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def assert_active_owner(self, workspace_dir: Path | str) -> None:
        if self._released or self._anchor is None or self._lock_handle is None:
            _raise("WORKSPACE_LOCK_NOT_OWNER", "workspace lock is no longer active")
        requested: windows_file_ops.WindowsDirectoryAnchor | None = None
        verification: windows_file_ops.WindowsFileHandle | None = None
        try:
            requested = windows_file_ops.open_directory_chain_no_reparse(workspace_dir)
            if requested.identity != self._workspace_identity:
                _raise(
                    "WORKSPACE_LOCK_WRONG_WORKSPACE",
                    "workspace lock belongs to a different workspace",
                )
            self._anchor.assert_safe()
            if self._lock_handle.identity() != self._lock_identity:
                _raise("WORKSPACE_LOCK_CHANGED", "workspace lock handle identity changed")
            verification = windows_file_ops.open_existing_regular_no_reparse(
                self._anchor,
                self._policy.file_name,
            )
            if verification.identity() != self._lock_identity:
                _raise("WORKSPACE_LOCK_CHANGED", "workspace lock identity changed")
            document = _parse_lock_document(verification.read(), self._policy)
            if document["ownerToken"] != self.owner_token:
                _raise("WORKSPACE_LOCK_NOT_OWNER", "workspace lock owner changed")
        except WorkspaceLockError:
            raise
        except windows_file_ops.WindowsFileOpsError as exc:
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_UNSAFE",
                "workspace lock cannot be verified safely",
            ) from exc
        finally:
            if verification is not None:
                verification.close()
            if requested is not None:
                requested.close()

    def release(self) -> None:
        if self._released:
            return
        if self._anchor is None or self._lock_handle is None:
            _raise("WORKSPACE_LOCK_CHANGED", "workspace lock handles are unavailable")
        try:
            self.assert_active_owner(self.workspace)
            quarantine_name = f"{self._policy.file_name}.release-{self.owner_token}"
            self._lock_handle.rename_no_replace(self._anchor, quarantine_name)
            document = _parse_lock_document(
                self._lock_handle.read(),
                self._policy,
            )
            if (
                self._lock_handle.identity() != self._lock_identity
                or document["ownerToken"] != self.owner_token
            ):
                _raise(
                    "WORKSPACE_LOCK_CHANGED",
                    "workspace lock changed during owner release quarantine",
                )
            self._lock_handle.mark_delete_on_close()
            self._lock_handle.close()
            self._lock_handle = None
            self._released = True
        except WorkspaceLockError:
            raise
        except windows_file_ops.WindowsFileOpsError as exc:
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_RELEASE_FAILED",
                "workspace lock could not be removed by its owner",
            ) from exc
        finally:
            try:
                self._close_handles()
            except windows_file_ops.WindowsFileOpsError as exc:
                if not self._released:
                    raise WorkspaceLockError(
                        "WORKSPACE_LOCK_RELEASE_FAILED",
                        "workspace lock handles could not be closed safely",
                    ) from exc

    def __del__(self) -> None:
        try:
            self._close_handles()
        except windows_file_ops.WindowsFileOpsError:
            pass


def _lock_document(policy: _WorkspaceLockPolicy) -> tuple[dict[str, Any], bytes]:
    timestamp = _utc_now()
    document: dict[str, Any] = {
        "ownerToken": uuid.uuid4().hex,
        "pid": os.getpid(),
        "createdAt": timestamp,
        "heartbeatAt": timestamp,
        "protocolVersion": policy.protocol_version,
    }
    if tuple(sorted(document)) != tuple(sorted(policy.fields)):
        _raise(
            "WORKSPACE_LOCK_POLICY_INVALID",
            "workspace lock document cannot satisfy the configured fields",
        )
    payload = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return document, payload


def _acquire_windows(
    workspace_dir: Path | str,
    policy: _WorkspaceLockPolicy,
) -> WindowsWorkspaceMutationLock:
    anchor: windows_file_ops.WindowsDirectoryAnchor | None = None
    lock_handle: windows_file_ops.WindowsFileHandle | None = None
    transferred = False
    try:
        anchor = windows_file_ops.open_directory_chain_no_reparse(workspace_dir)
        document, payload = _lock_document(policy)
        try:
            lock_handle = windows_file_ops.create_exclusive_regular(
                anchor,
                policy.file_name,
            )
        except windows_file_ops.WindowsFileOpsError as exc:
            if exc.code in {"WINDOWS_FILE_EXISTS", "WINDOWS_FILE_BUSY"}:
                existing: windows_file_ops.WindowsFileHandle | None = None
                try:
                    existing = windows_file_ops.open_existing_regular_no_reparse(
                        anchor,
                        policy.file_name,
                    )
                except windows_file_ops.WindowsFileOpsError as inspection_error:
                    if inspection_error.code not in {
                        "WINDOWS_FILE_BUSY",
                        "WINDOWS_FILE_ACCESS_DENIED",
                    }:
                        raise WorkspaceLockError(
                            "WORKSPACE_LOCK_UNSAFE",
                            "existing workspace lock is not a safe regular file",
                        ) from inspection_error
                finally:
                    if existing is not None:
                        existing.close()
                raise WorkspaceLockError(
                    "WORKSPACE_LOCK_HELD",
                    "workspace already has an active or stale mutation lock",
                ) from exc
            raise
        lock_handle.write(payload)
        lock_handle.flush()
        result = WindowsWorkspaceMutationLock(
            workspace=anchor.path,
            anchor=anchor,
            lock_handle=lock_handle,
            policy=policy,
            document=document,
        )
        result.assert_active_owner(anchor.path)
        transferred = True
        return result
    except WorkspaceLockError:
        raise
    except windows_file_ops.WindowsFileOpsUnavailable as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_PLATFORM_BLOCKED",
            "workspace lock requires the Win32 handle backend",
        ) from exc
    except windows_file_ops.WindowsFileOpsError as exc:
        if anchor is None and exc.code in {
            "WINDOWS_FILE_NOT_FOUND",
            "WINDOWS_FILE_NOT_DIRECTORY",
            "WINDOWS_FILE_REPARSE_POINT_FORBIDDEN",
        }:
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_WORKSPACE_INVALID",
                "workspace must be an existing reparse-free directory",
            ) from exc
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_UNSAFE",
            "workspace lock cannot be created safely",
        ) from exc
    finally:
        if lock_handle is not None and not transferred:
            lock_handle.close()
        if anchor is not None and not transferred:
            anchor.close()


def acquire(
    workspace_dir: Path | str,
) -> WorkspaceMutationLock | WindowsWorkspaceMutationLock:
    """Atomically acquire the canonical workspace mutation lock."""

    policy = _load_policy()
    if os.name == "nt":
        return _acquire_windows(workspace_dir, policy)
    try:
        posix_noreplace.require_available()
    except posix_noreplace.NoReplaceUnavailable as exc:
        raise WorkspaceLockError(
            "WORKSPACE_LOCK_PLATFORM_BLOCKED",
            "workspace lock requires atomic no-replace rename support",
        ) from exc
    workspace = _workspace_path(workspace_dir)
    workspace_fd = _open_workspace(workspace)
    lock_fd: int | None = None
    try:
        document, payload = _lock_document(policy)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            lock_fd = os.open(
                policy.file_name,
                flags,
                policy.file_mode,
                dir_fd=workspace_fd,
            )
        except FileExistsError as exc:
            raise _existing_lock_error(workspace_fd, policy.file_name) from exc
        except OSError as exc:
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_CREATE_FAILED",
                "workspace lock cannot be created atomically",
            ) from exc

        try:
            if os.name == "posix":
                os.fchmod(lock_fd, policy.file_mode)
            _write_all(lock_fd, payload)
            os.fsync(lock_fd)
            metadata = os.fstat(lock_fd)
            if not stat.S_ISREG(metadata.st_mode):
                _raise(
                    "WORKSPACE_LOCK_UNSAFE",
                    "new workspace lock is not a regular file",
                )
            os.fsync(workspace_fd)
        except (OSError, WorkspaceLockError) as exc:
            if isinstance(exc, WorkspaceLockError):
                raise
            raise WorkspaceLockError(
                "WORKSPACE_LOCK_WRITE_FAILED",
                "workspace lock document cannot be written durably",
            ) from exc

        return WorkspaceMutationLock(
            workspace=workspace,
            workspace_fd=workspace_fd,
            lock_fd=lock_fd,
            lock_identity=_identity(metadata),
            policy=policy,
            document=document,
        )
    except BaseException:
        if lock_fd is not None:
            os.close(lock_fd)
        # A partial file is deliberately left fail-closed.  Removing by name
        # here could delete a replacement created during the write failure.
        os.close(workspace_fd)
        raise
