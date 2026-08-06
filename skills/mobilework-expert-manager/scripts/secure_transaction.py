#!/usr/bin/env python3
"""POSIX dirfd-anchored workspace mutation transaction.

This module deliberately owns no installer policy.  Callers provide a complete
set of staged files, stale files, and required directories after all business
validation has completed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import package_contract as contract
import posix_noreplace


class SecureTransactionError(RuntimeError):
    """Raised when a secure transaction cannot proceed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "SECURE_TRANSACTION_FAILED",
        recovery_paths: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recovery_paths = list(dict.fromkeys(recovery_paths))


class SecureTransactionRecoveryError(SecureTransactionError):
    """Raised when commit or cleanup failed and manual recovery is required."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        recovery_paths: Iterable[str],
        committed: bool | None,
        rollback_verified: bool | None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            recovery_paths=recovery_paths,
        )
        self.committed = committed
        self.rollback_verified = rollback_verified


class SecureTransactionRolledBackError(SecureTransactionError):
    """Raised when an attempted commit failed and rollback was verified."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)
        self.committed = False
        self.rollback_verified = True


@dataclass(frozen=True)
class _Identity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    file_attributes: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _Identity:
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            mode=int(value.st_mode),
            size=int(value.st_size),
            mtime_ns=int(value.st_mtime_ns),
            ctime_ns=int(value.st_ctime_ns),
            file_attributes=int(getattr(value, "st_file_attributes", 0)),
        )


@dataclass(frozen=True)
class _FileIdentity:
    metadata: _Identity
    sha256: str


def _same_file_after_rename(before: _FileIdentity, after: _FileIdentity) -> bool:
    """Allow rename-updated ctime while requiring the same inode and bytes."""

    return (
        before.sha256 == after.sha256
        and before.metadata.device == after.metadata.device
        and before.metadata.inode == after.metadata.inode
        and before.metadata.mode == after.metadata.mode
        and before.metadata.size == after.metadata.size
        and before.metadata.mtime_ns == after.metadata.mtime_ns
        and before.metadata.file_attributes == after.metadata.file_attributes
    )


@dataclass
class _StagedSource:
    relative: str
    display_path: str
    parent_fd: int
    name: str
    identity: _FileIdentity

    def close(self) -> None:
        os.close(self.parent_fd)


_MISSING = object()


def _require_posix() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise SecureTransactionError(
            "secure workspace transactions require POSIX O_DIRECTORY and O_NOFOLLOW",
            code="SECURE_TRANSACTION_POSIX_REQUIRED",
        )
    if os.open not in os.supports_dir_fd:
        raise SecureTransactionError(
            "secure workspace transactions require openat support",
            code="SECURE_TRANSACTION_POSIX_REQUIRED",
        )
    try:
        posix_noreplace.require_available()
    except posix_noreplace.NoReplaceUnavailable as exc:
        raise SecureTransactionError(
            "secure workspace transactions require atomic no-replace rename",
            code="SECURE_TRANSACTION_NOREPLACE_REQUIRED",
        ) from exc


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _absolute_without_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _open_absolute_directory(path: Path, field: str) -> tuple[Path, int]:
    absolute = _absolute_without_resolution(path)
    current = os.open(os.sep, _directory_flags())
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=current)
            except OSError as exc:
                raise SecureTransactionError(
                    f"{field} must be an existing directory without symlink or special ancestors",
                    code="SECURE_TRANSACTION_UNSAFE_DIRECTORY",
                ) from exc
            os.close(current)
            current = next_fd
        return absolute, current
    except Exception:
        os.close(current)
        raise


def _normalize_relative(value: str, field: str) -> str:
    try:
        return contract.posix_relative_path(value, field)
    except contract.ContractError as exc:
        raise SecureTransactionError(
            str(exc),
            code="SECURE_TRANSACTION_INVALID_PATH",
        ) from exc


def _normalize_inputs(
    staged: Mapping[str, Path],
    stale: Iterable[str],
    required_directories: Iterable[str],
) -> tuple[dict[str, Path], list[str], list[str]]:
    normalized_staged: dict[str, Path] = {}
    for raw_relative, source in staged.items():
        relative = _normalize_relative(raw_relative, "secure transaction staged path")
        if relative in normalized_staged:
            raise SecureTransactionError(
                f"secure transaction duplicates staged path: {relative}",
                code="SECURE_TRANSACTION_DUPLICATE_PATH",
            )
        normalized_staged[relative] = Path(source)

    normalized_stale = sorted(
        {
            _normalize_relative(value, "secure transaction stale path")
            for value in stale
        }
    )
    normalized_required = sorted(
        {
            _normalize_relative(value, "secure transaction required directory")
            for value in required_directories
        },
        key=lambda value: (value.count("/"), value),
    )

    file_paths = sorted(set(normalized_staged) | set(normalized_stale))
    for index, path in enumerate(file_paths):
        prefix = path + "/"
        if any(candidate.startswith(prefix) for candidate in file_paths[index + 1 :]):
            raise SecureTransactionError(
                f"secure transaction file paths overlap: {path}",
                code="SECURE_TRANSACTION_PATH_CONFLICT",
            )
    conflicts = sorted(set(file_paths) & set(normalized_required))
    if conflicts:
        raise SecureTransactionError(
            f"required directory conflicts with a file target: {conflicts[0]}",
            code="SECURE_TRANSACTION_PATH_CONFLICT",
        )
    return normalized_staged, normalized_stale, normalized_required


def _open_parent_fd(
    root_fd: int,
    relative: str,
    *,
    create: bool,
    mode: int,
    created_directories: list[str] | None = None,
) -> tuple[int, str]:
    parts = relative.split("/")
    current = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for part in parts[:-1]:
            traversed.append(part)
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=mode, dir_fd=current)
                if created_directories is not None:
                    created_directories.append("/".join(traversed))
                next_fd = os.open(part, _directory_flags(), dir_fd=current)
            except OSError as exc:
                raise SecureTransactionError(
                    f"unsafe parent directory for {relative}",
                    code="SECURE_TRANSACTION_UNSAFE_TARGET",
                ) from exc
            os.close(current)
            current = next_fd
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def _lstat_at(parent_fd: int, name: str) -> os.stat_result | object:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _MISSING


def _file_identity_at(
    parent_fd: int,
    name: str,
    *,
    message: str,
    code: str,
) -> _FileIdentity:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise SecureTransactionError(message, code=code) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SecureTransactionError(message, code=code)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = _lstat_at(parent_fd, name)
        expected_metadata = _Identity.from_stat(before)
        if (
            current is _MISSING
            or not isinstance(current, os.stat_result)
            or not stat.S_ISREG(current.st_mode)
            or _Identity.from_stat(after) != expected_metadata
            or _Identity.from_stat(current) != expected_metadata
        ):
            raise SecureTransactionError(message, code=code)
        return _FileIdentity(expected_metadata, digest.hexdigest())
    finally:
        os.close(descriptor)


def _inspect_file_target(root_fd: int, relative: str) -> _FileIdentity | None:
    try:
        parent_fd, name = _open_parent_fd(
            root_fd,
            relative,
            create=False,
            mode=0o755,
        )
    except FileNotFoundError:
        return None
    try:
        metadata = _lstat_at(parent_fd, name)
        if metadata is _MISSING:
            return None
        assert isinstance(metadata, os.stat_result)
        if not stat.S_ISREG(metadata.st_mode):
            raise SecureTransactionError(
                f"transaction target must be a regular file or absent: {relative}",
                code="SECURE_TRANSACTION_UNSAFE_TARGET",
            )
        return _file_identity_at(
            parent_fd,
            name,
            message=f"transaction target changed while captured: {relative}",
            code="SECURE_TRANSACTION_TARGET_CHANGED",
        )
    finally:
        os.close(parent_fd)


def _inspect_required_directory(root_fd: int, relative: str) -> None:
    try:
        parent_fd, name = _open_parent_fd(
            root_fd,
            relative,
            create=False,
            mode=0o755,
        )
    except FileNotFoundError:
        return
    try:
        metadata = _lstat_at(parent_fd, name)
        if metadata is _MISSING:
            return
        assert isinstance(metadata, os.stat_result)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SecureTransactionError(
                f"required directory conflicts with an unsafe path: {relative}",
                code="SECURE_TRANSACTION_UNSAFE_TARGET",
            )
        check_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        os.close(check_fd)
    except OSError as exc:
        if isinstance(exc, SecureTransactionError):
            raise
        raise SecureTransactionError(
            f"required directory is unsafe: {relative}",
            code="SECURE_TRANSACTION_UNSAFE_TARGET",
        ) from exc
    finally:
        os.close(parent_fd)


def _open_staged_source(relative: str, source: Path) -> _StagedSource:
    absolute = _absolute_without_resolution(source)
    if absolute == Path(os.sep):
        raise SecureTransactionError(
            f"staged source must be a regular file: {source}",
            code="SECURE_TRANSACTION_UNSAFE_STAGING",
        )
    parent, name = absolute.parent, absolute.name
    _, parent_fd = _open_absolute_directory(parent, "staged source parent")
    try:
        metadata = _lstat_at(parent_fd, name)
        if (
            metadata is _MISSING
            or not isinstance(metadata, os.stat_result)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise SecureTransactionError(
                f"staged source must be a regular file without symlinks: {source}",
                code="SECURE_TRANSACTION_UNSAFE_STAGING",
            )
        return _StagedSource(
            relative=relative,
            display_path=str(absolute),
            parent_fd=parent_fd,
            name=name,
            identity=_file_identity_at(
                parent_fd,
                name,
                message=f"staged source changed while captured: {source}",
                code="SECURE_TRANSACTION_STAGING_CHANGED",
            ),
        )
    except Exception:
        os.close(parent_fd)
        raise


def _require_file_identity(
    parent_fd: int,
    name: str,
    expected: _FileIdentity,
    message: str,
    *,
    code: str,
) -> None:
    try:
        actual = _file_identity_at(
            parent_fd,
            name,
            message=message,
            code=code,
        )
    except SecureTransactionError:
        raise
    if actual != expected:
        raise SecureTransactionError(message, code=code)


def _require_renamed_file_identity(
    parent_fd: int,
    name: str,
    expected: _FileIdentity,
    message: str,
    *,
    code: str,
) -> _FileIdentity:
    actual = _file_identity_at(
        parent_fd,
        name,
        message=message,
        code=code,
    )
    if actual != expected and not _same_file_after_rename(expected, actual):
        raise SecureTransactionError(message, code=code)
    return actual


def _rename_at(source_fd: int, source: str, target_fd: int, target: str) -> None:
    try:
        posix_noreplace.rename(source_fd, source, target_fd, target)
    except posix_noreplace.NoReplaceUnavailable as exc:
        raise SecureTransactionError(
            "atomic no-replace rename became unavailable",
            code="SECURE_TRANSACTION_NOREPLACE_REQUIRED",
        ) from exc


def _ensure_directory(
    root_fd: int,
    relative: str,
    *,
    mode: int,
    created_directories: list[str],
) -> None:
    parent_fd, name = _open_parent_fd(
        root_fd,
        relative,
        create=True,
        mode=mode,
        created_directories=created_directories,
    )
    try:
        metadata = _lstat_at(parent_fd, name)
        if metadata is _MISSING:
            os.mkdir(name, mode=mode, dir_fd=parent_fd)
            created_directories.append(relative)
            check_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
            os.close(check_fd)
            return
        assert isinstance(metadata, os.stat_result)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SecureTransactionError(
                f"required directory conflicts with an existing path: {relative}",
                code="SECURE_TRANSACTION_UNSAFE_TARGET",
            )
        check_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
        os.close(check_fd)
    finally:
        os.close(parent_fd)


def _remove_empty_directory(root_fd: int, relative: str) -> None:
    parent_fd, name = _open_parent_fd(
        root_fd,
        relative,
        create=False,
        mode=0o755,
    )
    try:
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _clear_directory(directory_fd: int) -> None:
    with os.scandir(directory_fd) as entries:
        for entry in list(entries):
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(entry.name, _directory_flags(), dir_fd=directory_fd)
                try:
                    _clear_directory(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(entry.name, dir_fd=directory_fd)
            elif stat.S_ISREG(metadata.st_mode):
                os.unlink(entry.name, dir_fd=directory_fd)
            else:
                raise SecureTransactionError(
                    "transaction backup contains an unexpected special entry",
                    code="SECURE_TRANSACTION_UNSAFE_BACKUP",
                )


def _cleanup_backup(runtime_fd: int, backup_fd: int, backup_name: str) -> None:
    expected = _Identity.from_stat(os.fstat(backup_fd))
    metadata = _lstat_at(runtime_fd, backup_name)
    if (
        metadata is _MISSING
        or not isinstance(metadata, os.stat_result)
        or not stat.S_ISDIR(metadata.st_mode)
        or _Identity.from_stat(metadata) != expected
    ):
        raise SecureTransactionError(
            "transaction backup directory changed before cleanup",
            code="SECURE_TRANSACTION_BACKUP_CHANGED",
        )
    _clear_directory(backup_fd)
    os.rmdir(backup_name, dir_fd=runtime_fd)


def commit(
    runtime_dir: Path,
    staged: Mapping[str, Path],
    stale: Iterable[str],
    required_directories: Iterable[str],
    pre_commit_guard: Callable[[], None] | None,
) -> None:
    """Commit a file transaction without following filesystem links.

    The guard runs after all read-only validation and immediately before the
    first directory or file mutation.  A failed guard therefore guarantees
    zero transaction writes.
    """

    _require_posix()
    normalized_staged, normalized_stale, normalized_required = _normalize_inputs(
        staged,
        stale,
        required_directories,
    )
    runtime_absolute, runtime_fd = _open_absolute_directory(
        runtime_dir,
        "runtime directory",
    )
    sources: dict[str, _StagedSource] = {}
    backup_fd: int | None = None
    backup_name = f".install-backup-{uuid.uuid4().hex}"
    backup_absolute = runtime_absolute / backup_name
    try:
        source_identities: set[tuple[int, int]] = set()
        for relative, source_path in sorted(normalized_staged.items()):
            source = _open_staged_source(relative, source_path)
            source_absolute = Path(source.display_path)
            if os.path.commonpath((runtime_absolute, source_absolute)) == str(runtime_absolute):
                source.close()
                raise SecureTransactionError(
                    "staged sources must be outside the runtime directory",
                    code="SECURE_TRANSACTION_UNSAFE_STAGING",
                )
            identity_key = (
                source.identity.metadata.device,
                source.identity.metadata.inode,
            )
            if identity_key in source_identities:
                source.close()
                raise SecureTransactionError(
                    "a staged source cannot be consumed by multiple targets",
                    code="SECURE_TRANSACTION_DUPLICATE_SOURCE",
                )
            source_identities.add(identity_key)
            sources[relative] = source

        target_paths = sorted(set(normalized_staged) | set(normalized_stale))
        expected_targets = {
            relative: _inspect_file_target(runtime_fd, relative)
            for relative in target_paths
        }
        for relative in normalized_required:
            _inspect_required_directory(runtime_fd, relative)

        if pre_commit_guard is not None:
            pre_commit_guard()

        # The guard can be arbitrarily expensive. Rebind every target and source
        # to both metadata and bytes before creating the transaction backup.
        for relative, expected in expected_targets.items():
            actual = _inspect_file_target(runtime_fd, relative)
            if actual != expected:
                raise SecureTransactionError(
                    f"transaction target changed after the final guard: {relative}",
                    code="SECURE_TRANSACTION_TARGET_CHANGED",
                )
        for source in sources.values():
            _require_file_identity(
                source.parent_fd,
                source.name,
                source.identity,
                f"staged source changed after the final guard: {source.display_path}",
                code="SECURE_TRANSACTION_STAGING_CHANGED",
            )

        os.mkdir(backup_name, mode=0o700, dir_fd=runtime_fd)
        try:
            backup_fd = os.open(backup_name, _directory_flags(), dir_fd=runtime_fd)
        except Exception as open_error:
            try:
                os.rmdir(backup_name, dir_fd=runtime_fd)
            except Exception as cleanup_error:
                raise SecureTransactionRecoveryError(
                    f"transaction backup cannot be opened or removed: {backup_absolute}",
                    code="SECURE_TRANSACTION_BACKUP_CREATE_FAILED",
                    recovery_paths=(str(backup_absolute),),
                    committed=False,
                    rollback_verified=True,
                ) from cleanup_error
            raise SecureTransactionError(
                "transaction backup cannot be opened safely",
                code="SECURE_TRANSACTION_UNSAFE_BACKUP",
            ) from open_error
        backups: dict[str, _FileIdentity] = {}
        written: list[tuple[str, _StagedSource, _FileIdentity]] = []
        created_directories: list[str] = []
        try:
            for relative in target_paths:
                expected = expected_targets[relative]
                try:
                    target_parent_fd, target_name = _open_parent_fd(
                        runtime_fd,
                        relative,
                        create=False,
                        mode=0o755,
                    )
                except FileNotFoundError:
                    if expected is not None:
                        raise SecureTransactionError(
                            f"transaction target changed before commit: {relative}",
                            code="SECURE_TRANSACTION_TARGET_CHANGED",
                        )
                    continue
                try:
                    actual = _lstat_at(target_parent_fd, target_name)
                    if expected is None:
                        if actual is not _MISSING:
                            raise SecureTransactionError(
                                f"transaction target appeared before commit: {relative}",
                                code="SECURE_TRANSACTION_TARGET_CHANGED",
                            )
                        continue
                    _require_file_identity(
                        target_parent_fd,
                        target_name,
                        expected,
                        f"transaction target changed before commit: {relative}",
                        code="SECURE_TRANSACTION_TARGET_CHANGED",
                    )
                    backup_parent_fd, backup_file_name = _open_parent_fd(
                        backup_fd,
                        relative,
                        create=True,
                        mode=0o700,
                    )
                    try:
                        _rename_at(
                            target_parent_fd,
                            target_name,
                            backup_parent_fd,
                            backup_file_name,
                        )
                        backups[relative] = expected
                        backup_identity = _require_renamed_file_identity(
                            backup_parent_fd,
                            backup_file_name,
                            expected,
                            message=f"transaction backup changed after rename: {relative}",
                            code="SECURE_TRANSACTION_BACKUP_CHANGED",
                        )
                        backups[relative] = backup_identity
                    finally:
                        os.close(backup_parent_fd)
                finally:
                    os.close(target_parent_fd)

            for relative, source in sorted(sources.items()):
                _require_file_identity(
                    source.parent_fd,
                    source.name,
                    source.identity,
                    f"staged source changed before commit: {source.display_path}",
                    code="SECURE_TRANSACTION_STAGING_CHANGED",
                )
                target_parent_fd, target_name = _open_parent_fd(
                    runtime_fd,
                    relative,
                    create=True,
                    mode=0o755,
                    created_directories=created_directories,
                )
                try:
                    if _lstat_at(target_parent_fd, target_name) is not _MISSING:
                        raise SecureTransactionError(
                            f"transaction target was recreated during commit: {relative}",
                            code="SECURE_TRANSACTION_TARGET_CHANGED",
                        )
                    _rename_at(
                        source.parent_fd,
                        source.name,
                        target_parent_fd,
                        target_name,
                    )
                    written.append((relative, source, source.identity))
                    written_identity = _require_renamed_file_identity(
                        target_parent_fd,
                        target_name,
                        source.identity,
                        message=f"written target changed after rename: {relative}",
                        code="SECURE_TRANSACTION_TARGET_CHANGED",
                    )
                    written[-1] = (relative, source, written_identity)
                finally:
                    os.close(target_parent_fd)

            for relative in normalized_required:
                _ensure_directory(
                    runtime_fd,
                    relative,
                    mode=0o755,
                    created_directories=created_directories,
                )
        except Exception as commit_error:
            recovery_paths: list[str] = []
            recovery_errors: list[Exception] = []
            for relative, source, written_identity in reversed(written):
                target_path = str(runtime_absolute / relative)
                try:
                    target_parent_fd, target_name = _open_parent_fd(
                        runtime_fd,
                        relative,
                        create=False,
                        mode=0o755,
                    )
                    try:
                        _require_renamed_file_identity(
                            target_parent_fd,
                            target_name,
                            written_identity,
                            f"written target changed before rollback: {relative}",
                            code="SECURE_TRANSACTION_TARGET_CHANGED",
                        )
                        if _lstat_at(source.parent_fd, source.name) is not _MISSING:
                            raise SecureTransactionError(
                                "staged source path was recreated before rollback: "
                                + source.display_path,
                                code="SECURE_TRANSACTION_STAGING_CHANGED",
                            )
                        _rename_at(
                            target_parent_fd,
                            target_name,
                            source.parent_fd,
                            source.name,
                        )
                    finally:
                        os.close(target_parent_fd)
                except Exception as recovery_error:
                    recovery_errors.append(recovery_error)
                    recovery_paths.extend((target_path, source.display_path))

            for relative, expected in reversed(list(backups.items())):
                backup_path = str(backup_absolute / relative)
                target_path = str(runtime_absolute / relative)
                try:
                    backup_parent_fd, backup_file_name = _open_parent_fd(
                        backup_fd,
                        relative,
                        create=False,
                        mode=0o700,
                    )
                    try:
                        _require_renamed_file_identity(
                            backup_parent_fd,
                            backup_file_name,
                            expected,
                            f"rollback backup changed: {relative}",
                            code="SECURE_TRANSACTION_BACKUP_CHANGED",
                        )
                        target_parent_fd, target_name = _open_parent_fd(
                            runtime_fd,
                            relative,
                            create=False,
                            mode=0o755,
                        )
                        try:
                            if _lstat_at(target_parent_fd, target_name) is not _MISSING:
                                raise SecureTransactionError(
                                    f"rollback target is no longer absent: {relative}",
                                    code="SECURE_TRANSACTION_TARGET_CHANGED",
                                )
                            _rename_at(
                                backup_parent_fd,
                                backup_file_name,
                                target_parent_fd,
                                target_name,
                            )
                        finally:
                            os.close(target_parent_fd)
                    finally:
                        os.close(backup_parent_fd)
                except Exception as recovery_error:
                    recovery_errors.append(recovery_error)
                    recovery_paths.extend((backup_path, target_path))

            for relative in sorted(
                set(created_directories),
                key=lambda value: (value.count("/"), value),
                reverse=True,
            ):
                try:
                    _remove_empty_directory(runtime_fd, relative)
                except FileNotFoundError:
                    continue
                except Exception as recovery_error:
                    recovery_errors.append(recovery_error)
                    recovery_paths.append(str(runtime_absolute / relative))

            if recovery_errors:
                recovery_paths.append(str(backup_absolute))
                raise SecureTransactionRecoveryError(
                    "secure transaction rollback failed; preserve recovery paths: "
                    + ", ".join(dict.fromkeys(recovery_paths)),
                    code="SECURE_TRANSACTION_ROLLBACK_FAILED",
                    recovery_paths=recovery_paths,
                    committed=None,
                    rollback_verified=False,
                ) from commit_error

            try:
                _cleanup_backup(runtime_fd, backup_fd, backup_name)
            except Exception as cleanup_error:
                raise SecureTransactionRecoveryError(
                    f"rollback completed but backup cleanup failed: {backup_absolute}",
                    code="SECURE_TRANSACTION_CLEANUP_FAILED",
                    recovery_paths=(str(backup_absolute),),
                    committed=False,
                    rollback_verified=True,
                ) from cleanup_error
            finally:
                os.close(backup_fd)
                backup_fd = None
            if (
                isinstance(commit_error, SecureTransactionError)
                and commit_error.code == "SECURE_TRANSACTION_NOREPLACE_REQUIRED"
            ):
                raise
            raise SecureTransactionRolledBackError(
                "secure transaction failed and rollback was verified",
                code=(
                    commit_error.code
                    if isinstance(commit_error, SecureTransactionError)
                    else "SECURE_TRANSACTION_ROLLED_BACK"
                ),
            ) from commit_error

        try:
            _cleanup_backup(runtime_fd, backup_fd, backup_name)
        except Exception as cleanup_error:
            raise SecureTransactionRecoveryError(
                f"transaction committed but backup cleanup failed: {backup_absolute}",
                code="SECURE_TRANSACTION_CLEANUP_FAILED",
                recovery_paths=(str(backup_absolute),),
                committed=True,
                rollback_verified=None,
            ) from cleanup_error
        finally:
            os.close(backup_fd)
            backup_fd = None
    finally:
        if backup_fd is not None:
            os.close(backup_fd)
        for source in sources.values():
            source.close()
        os.close(runtime_fd)
