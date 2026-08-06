#!/usr/bin/env python3
"""Restricted, content-addressed backups for explicit owned-state recovery."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import manager_contract
import posix_noreplace
import safe_input


BACKUP_HASH_DOMAIN = "mobilework-drift-backup-v1"
TARGET_STATE_HASH_DOMAIN = "mobilework-drift-target-state-v1"
RECEIPT_SET_HASH_DOMAIN = "mobilework-drift-receipt-set-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RECEIPT_FILENAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
UTC_CREATED_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
CANONICAL_BACKUP_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z(?:-[0-9]{3})?$"
)
MANIFEST_CORE_FIELDS = frozenset(
    {
        "schemaVersion",
        "slug",
        "backupId",
        "createdAt",
        "previewSha256",
        "postStateSha256",
        "receiptSetSha256",
        "entries",
    }
)
MANIFEST_FIELDS = MANIFEST_CORE_FIELDS | {"backupSha256"}
ENTRY_FIELDS = frozenset(
    {"relativePath", "present", "payloadFile", "sha256", "size", "mode"}
)


class DriftBackupError(ValueError):
    """Raised when a drift backup cannot cross a safety boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempted: bool = False,
        committed: bool | None = None,
        rollback_verified: bool | None = None,
        recovery_paths: tuple[str, ...] = (),
        durability_unverified: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.attempted = attempted
        self.committed = committed
        self.rollback_verified = rollback_verified
        self.recovery_paths = list(dict.fromkeys(recovery_paths))
        self.durability_unverified = durability_unverified
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TargetState:
    relative_path: str
    present: bool
    content: bytes | None
    mode: int | None


@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    path: Path
    backup_sha256: str
    created_at: str
    slug: str
    preview_sha256: str
    post_state_sha256: str
    receipt_set_sha256: str


@dataclass(frozen=True)
class BackupSnapshot:
    record: BackupRecord
    targets: tuple[TargetState, ...]

    @property
    def targets_by_path(self) -> dict[str, TargetState]:
        return {target.relative_path: target for target in self.targets}


@dataclass(frozen=True)
class _Policy:
    root_name: str
    manifest_name: str
    payload_directory: str
    publish_protocol: str
    schema_version: int
    dir_mode: int
    file_mode: int
    backup_id_pattern: re.Pattern[str]


@dataclass(frozen=True)
class _DirectoryIdentity:
    device: int
    inode: int


def _platform_supported() -> bool:
    return os.name == "posix"


def _ensure_supported_platform() -> None:
    if not _platform_supported():
        raise DriftBackupError(
            "DRIFT_RECOVERY_PLATFORM_BLOCKED",
            "drift backup and restore are blocked without the verified POSIX backend",
        )


def _filesystem_name(value: object, field: str, *, expected: str) -> str:
    if (
        value != expected
        or not isinstance(value, str)
        or value in {"", ".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            f"driftRecovery.{field} must be {expected!r}",
        )
    return value


def _policy() -> _Policy:
    try:
        raw_policy = manager_contract.load_policy()
    except (manager_contract.ManagerContractError, OSError, ValueError) as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            f"cannot load drift recovery policy: {exc}",
        ) from exc
    raw = raw_policy.get("driftRecovery")
    if not isinstance(raw, dict):
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            "manager contract driftRecovery must be an object",
        )
    root_name = _filesystem_name(
        raw.get("rootName"), "rootName", expected=".expert-drift-backups"
    )
    manifest_name = _filesystem_name(
        raw.get("manifestName"), "manifestName", expected="manifest.json"
    )
    payload_directory = _filesystem_name(
        raw.get("payloadDirectory"), "payloadDirectory", expected="payload"
    )
    publish_protocol = raw.get("publishProtocol")
    if publish_protocol != "posix-exclusive-directory-v1":
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            "driftRecovery.publishProtocol is invalid",
        )
    if isinstance(raw.get("schemaVersion"), bool) or raw.get("schemaVersion") != 1:
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            "driftRecovery.schemaVersion must be 1",
        )
    if raw.get("dirMode") != 0o700 or raw.get("fileMode") != 0o600:
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            "driftRecovery modes must be dirMode 448 and fileMode 384",
        )
    pattern_value = raw.get("backupIdPattern")
    if pattern_value != CANONICAL_BACKUP_ID_RE.pattern:
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            "driftRecovery.backupIdPattern must match the canonical timestamp pattern",
        )
    try:
        pattern = re.compile(pattern_value)
    except re.error as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_POLICY_INVALID",
            f"driftRecovery.backupIdPattern is invalid: {exc}",
        ) from exc
    return _Policy(
        root_name=root_name,
        manifest_name=manifest_name,
        payload_directory=payload_directory,
        publish_protocol=publish_protocol,
        schema_version=1,
        dir_mode=0o700,
        file_mode=0o600,
        backup_id_pattern=pattern,
    )


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise DriftBackupError(
            "DRIFT_BACKUP_ARGUMENT_INVALID", f"{field} must be a lowercase SHA-256"
        )
    return value


def _slug(value: object) -> str:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise DriftBackupError(
            "DRIFT_BACKUP_ARGUMENT_INVALID", "slug must be a canonical package slug"
        )
    return value


def _relative_path(value: object, policy: _Policy) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", "backup target path is not canonical"
        )
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or value != parsed.as_posix()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.parts[0] == policy.root_name
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", "backup target path is not canonical"
        )
    return value


def _normalize_target(
    key: object,
    target: object,
    policy: _Policy,
) -> TargetState:
    if not isinstance(target, TargetState):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", "pre_targets values must be TargetState"
        )
    path = _relative_path(key, policy)
    if target.relative_path != path:
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID",
            "pre_targets key must equal TargetState.relative_path",
        )
    if not isinstance(target.present, bool):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", f"target {path} present must be boolean"
        )
    mode = target.mode
    if mode is not None and (
        isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", f"target {path} mode is invalid"
        )
    if target.present:
        if not isinstance(target.content, bytes):
            raise DriftBackupError(
                "DRIFT_BACKUP_TARGET_INVALID",
                f"present target {path} must provide bytes",
            )
    elif target.content is not None or mode is not None:
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID",
            f"absent target {path} cannot provide content or mode",
        )
    return TargetState(path, target.present, target.content, mode)


def target_state_sha256(targets: Mapping[str, TargetState]) -> str:
    """Hash exact target state metadata without serializing the original bytes."""

    policy = _policy()
    if not isinstance(targets, Mapping):
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", "targets must be a mapping"
        )
    normalized = tuple(
        _normalize_target(key, value, policy)
        for key, value in sorted(targets.items(), key=lambda item: str(item[0]))
    )
    entries: list[dict[str, Any]] = []
    for target in normalized:
        if target.present:
            assert target.content is not None
            entries.append(
                {
                    "relativePath": target.relative_path,
                    "present": True,
                    "size": len(target.content),
                    "sha256": hashlib.sha256(target.content).hexdigest(),
                    "mode": target.mode,
                }
            )
        else:
            entries.append(
                {
                    "relativePath": target.relative_path,
                    "present": False,
                    "size": None,
                    "sha256": None,
                    "mode": None,
                }
            )
    return manager_contract.canonical_json_sha256(
        {"targets": entries}, domain=TARGET_STATE_HASH_DOMAIN
    )


def receipt_set_sha256(files: Mapping[str, bytes]) -> str:
    """Hash canonical receipt filenames and content evidence without raw values."""

    if not isinstance(files, Mapping):
        raise DriftBackupError(
            "DRIFT_RECEIPT_SET_INVALID", "receipt files must be a mapping"
        )
    entries: list[dict[str, Any]] = []
    for filename, content in sorted(files.items(), key=lambda item: str(item[0])):
        if (
            not isinstance(filename, str)
            or not RECEIPT_FILENAME_RE.fullmatch(filename)
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise DriftBackupError(
                "DRIFT_RECEIPT_SET_INVALID",
                "receipt filename must be a canonical single-level package-slug.json",
            )
        if not isinstance(content, bytes):
            raise DriftBackupError(
                "DRIFT_RECEIPT_SET_INVALID", "receipt content must be bytes"
            )
        entries.append(
            {
                "filename": filename,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return manager_contract.canonical_json_sha256(
        {"files": entries}, domain=RECEIPT_SET_HASH_DOMAIN
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _safe_directory(path: Path, *, mode: int) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_NOT_FOUND", f"backup directory does not exist: {path.name}"
        ) from exc
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE",
            f"cannot inspect backup directory {path.name}: {exc}",
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", f"backup path {path.name} is not a real directory"
        )
    if stat.S_IMODE(info.st_mode) != mode:
        raise DriftBackupError(
            "DRIFT_BACKUP_PERMISSION_INVALID",
            f"backup directory {path.name} must use mode {oct(mode)}",
        )


def _prepare_roots(runtime_dir: Path, slug: str, policy: _Policy) -> Path:
    runtime = _absolute_lexical(runtime_dir)
    try:
        runtime_info = os.lstat(runtime)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_NOT_FOUND", "runtime directory does not exist"
        ) from exc
    _safe_directory(runtime, mode=stat.S_IMODE(runtime_info.st_mode))
    root = runtime / policy.root_name
    if not root.exists() and not root.is_symlink():
        try:
            root.mkdir(mode=policy.dir_mode)
            os.chmod(root, policy.dir_mode, follow_symlinks=False)
        except OSError as exc:
            raise DriftBackupError(
                "DRIFT_BACKUP_WRITE_FAILED", f"cannot create backup root: {exc}"
            ) from exc
    _safe_directory(root, mode=policy.dir_mode)
    slug_root = root / slug
    if not slug_root.exists() and not slug_root.is_symlink():
        try:
            slug_root.mkdir(mode=policy.dir_mode)
            os.chmod(slug_root, policy.dir_mode, follow_symlinks=False)
        except OSError as exc:
            raise DriftBackupError(
                "DRIFT_BACKUP_WRITE_FAILED", f"cannot create slug backup root: {exc}"
            ) from exc
    _safe_directory(slug_root, mode=policy.dir_mode)
    return slug_root


def _existing_roots(runtime_dir: Path, slug: str, policy: _Policy) -> Path:
    runtime = _absolute_lexical(runtime_dir)
    try:
        runtime_info = os.lstat(runtime)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_NOT_FOUND", "runtime directory does not exist"
        ) from exc
    if stat.S_ISLNK(runtime_info.st_mode) or not stat.S_ISDIR(runtime_info.st_mode):
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", "runtime directory is not a real directory"
        )
    root = runtime / policy.root_name
    slug_root = root / slug
    _safe_directory(root, mode=policy.dir_mode)
    _safe_directory(slug_root, mode=policy.dir_mode)
    return slug_root


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _candidate_backup_id(now: datetime, collision: int) -> str:
    base = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return base if collision == 0 else f"{base}-{collision:03d}"


def _choose_backup_id(slug_root: Path, policy: _Policy) -> str:
    now = _utc_now()
    for collision in range(1000):
        candidate = _candidate_backup_id(now, collision)
        if not policy.backup_id_pattern.fullmatch(candidate):
            raise DriftBackupError(
                "DRIFT_BACKUP_POLICY_INVALID",
                "driftRecovery.backupIdPattern rejects generated UTC backup ids",
            )
        if not (slug_root / candidate).exists() and not (slug_root / candidate).is_symlink():
            return candidate
    raise DriftBackupError(
        "DRIFT_BACKUP_ID_EXHAUSTED", "cannot allocate a unique UTC backup id"
    )


def _write_restricted(path: Path, content: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, mode)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_WRITE_FAILED", f"cannot write restricted backup file: {exc}"
        ) from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _no_replace_library() -> None:
    try:
        posix_noreplace.require_available()
    except posix_noreplace.NoReplaceUnavailable as exc:
        raise DriftBackupError(
            "DRIFT_RECOVERY_PLATFORM_BLOCKED",
            "platform has no verified atomic no-replace directory publish",
        ) from exc


def _rename_no_replace(parent_fd: int, source_name: str, target_name: str) -> None:
    """Atomically publish a directory without replacing an existing name."""

    _no_replace_library()
    try:
        posix_noreplace.rename(
            parent_fd,
            source_name,
            parent_fd,
            target_name,
        )
    except posix_noreplace.NoReplaceUnavailable as exc:
        raise DriftBackupError(
            "DRIFT_RECOVERY_PLATFORM_BLOCKED",
            "filesystem has no verified atomic no-replace directory publish",
        ) from exc


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _directory_identity(metadata: os.stat_result) -> _DirectoryIdentity:
    return _DirectoryIdentity(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
    )


def _clear_directory_fd(directory_fd: int) -> None:
    with os.scandir(directory_fd) as entries:
        for entry in list(entries):
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(entry.name, _directory_flags(), dir_fd=directory_fd)
                try:
                    _clear_directory_fd(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(entry.name, dir_fd=directory_fd)
            elif stat.S_ISREG(metadata.st_mode):
                os.unlink(entry.name, dir_fd=directory_fd)
            else:
                raise OSError("failed backup contains an unexpected special entry")


def _remove_failed_publish(
    path: Path,
    parent: Path,
    expected_identity: _DirectoryIdentity,
) -> None:
    """Quarantine and remove only the directory identity published by this call."""

    parent_fd: int | None = None
    quarantine_fd: int | None = None
    quarantine_name = f".{path.name}.failed-{secrets.token_hex(8)}"
    recovery_path = path
    durability_unverified = False
    try:
        parent_fd = os.open(parent, _directory_flags())
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or _directory_identity(current) != expected_identity
        ):
            raise OSError("published backup identity changed before cleanup")
        try:
            os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise OSError("failed backup cleanup quarantine already exists")
        _rename_no_replace(parent_fd, path.name, quarantine_name)
        recovery_path = parent / quarantine_name
        quarantine_fd = os.open(quarantine_name, _directory_flags(), dir_fd=parent_fd)
        if _directory_identity(os.fstat(quarantine_fd)) != expected_identity:
            raise OSError("published backup identity changed during cleanup quarantine")
        _clear_directory_fd(quarantine_fd)
        current = os.stat(
            quarantine_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if _directory_identity(current) != expected_identity:
            raise OSError("published backup cleanup quarantine was replaced")
        os.rmdir(quarantine_name, dir_fd=parent_fd)
        recovery_path = parent
        durability_unverified = True
        os.fsync(parent_fd)
        durability_unverified = False
    except (OSError, DriftBackupError) as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_CLEANUP_FAILED",
            "failed backup publish could not be removed with matching identity",
            attempted=True,
            committed=False,
            rollback_verified=False,
            recovery_paths=(str(recovery_path),),
            durability_unverified=durability_unverified,
        ) from exc
    finally:
        if quarantine_fd is not None:
            os.close(quarantine_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def _backup_sha256(core: Mapping[str, Any]) -> str:
    payload_hashes = [
        entry["sha256"]
        for entry in core["entries"]
        if isinstance(entry, dict) and entry.get("present") is True
    ]
    return manager_contract.canonical_json_sha256(
        {"manifest": dict(core), "payloadSha256": payload_hashes},
        domain=BACKUP_HASH_DOMAIN,
    )


def _record(path: Path, manifest: Mapping[str, Any]) -> BackupRecord:
    return BackupRecord(
        backup_id=manifest["backupId"],
        path=path,
        backup_sha256=manifest["backupSha256"],
        created_at=manifest["createdAt"],
        slug=manifest["slug"],
        preview_sha256=manifest["previewSha256"],
        post_state_sha256=manifest["postStateSha256"],
        receipt_set_sha256=manifest["receiptSetSha256"],
    )


def _cleanup_failed_creation(
    *,
    temporary: Path,
    final: Path,
    slug_root: Path,
    temporary_created: bool,
    temporary_identity: _DirectoryIdentity | None,
    published_identity: _DirectoryIdentity | None,
) -> bool:
    """Clean identities created by one call or raise with actionable evidence."""

    attempted = temporary_created or published_identity is not None
    if temporary_created:
        if temporary_identity is None:
            raise DriftBackupError(
                "DRIFT_BACKUP_CLEANUP_FAILED",
                "private backup staging identity was not captured before failure",
                attempted=True,
                committed=False,
                rollback_verified=False,
                recovery_paths=(str(temporary),),
            )
        _remove_failed_publish(temporary, slug_root, temporary_identity)
    if published_identity is not None:
        _remove_failed_publish(final, slug_root, published_identity)
    return attempted


def create_backup(
    runtime_dir: Path,
    slug: str,
    preview_sha256: str,
    pre_targets: Mapping[str, TargetState],
    post_state_sha256: str,
    receipt_set_sha256: str,
) -> BackupRecord:
    """Create an immutable restricted pre-image before a destructive write."""

    _ensure_supported_platform()
    _no_replace_library()
    policy = _policy()
    normalized_slug = _slug(slug)
    preview_hash = _sha256(preview_sha256, "preview_sha256")
    post_hash = _sha256(post_state_sha256, "post_state_sha256")
    receipt_hash = _sha256(receipt_set_sha256, "receipt_set_sha256")
    if not isinstance(pre_targets, Mapping) or not pre_targets:
        raise DriftBackupError(
            "DRIFT_BACKUP_TARGET_INVALID", "pre_targets must be a non-empty mapping"
        )
    targets = tuple(
        _normalize_target(key, value, policy)
        for key, value in sorted(pre_targets.items(), key=lambda item: str(item[0]))
    )
    slug_root = _prepare_roots(runtime_dir, normalized_slug, policy)

    backup_id = _choose_backup_id(slug_root, policy)
    created_at = _utc_now().astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
    temporary = slug_root / f".tmp-{backup_id}-{secrets.token_hex(8)}"
    final = slug_root / backup_id
    temporary_created = False
    temporary_identity: _DirectoryIdentity | None = None
    published_identity: _DirectoryIdentity | None = None
    try:
        temporary.mkdir(mode=policy.dir_mode)
        temporary_created = True
        os.chmod(temporary, policy.dir_mode, follow_symlinks=False)
        temporary_info = os.lstat(temporary)
        if (
            not stat.S_ISDIR(temporary_info.st_mode)
            or stat.S_IMODE(temporary_info.st_mode) != policy.dir_mode
        ):
            raise DriftBackupError(
                "DRIFT_BACKUP_WRITE_FAILED",
                "private backup staging directory is unsafe",
            )
        temporary_identity = _directory_identity(temporary_info)
        payload_root = temporary / policy.payload_directory
        payload_root.mkdir(mode=policy.dir_mode)
        os.chmod(payload_root, policy.dir_mode, follow_symlinks=False)
        entries: list[dict[str, Any]] = []
        payload_index = 0
        for target in targets:
            if target.present:
                payload_index += 1
                payload_name = f"{payload_index:06d}.bin"
                assert target.content is not None
                payload_hash = hashlib.sha256(target.content).hexdigest()
                _write_restricted(
                    payload_root / payload_name,
                    target.content,
                    policy.file_mode,
                )
                entries.append(
                    {
                        "relativePath": target.relative_path,
                        "present": True,
                        "payloadFile": payload_name,
                        "sha256": payload_hash,
                        "size": len(target.content),
                        "mode": target.mode,
                    }
                )
            else:
                entries.append(
                    {
                        "relativePath": target.relative_path,
                        "present": False,
                        "payloadFile": None,
                        "sha256": None,
                        "size": None,
                        "mode": None,
                    }
                )
        _fsync_directory(payload_root)
        core: dict[str, Any] = {
            "schemaVersion": policy.schema_version,
            "slug": normalized_slug,
            "backupId": backup_id,
            "createdAt": created_at,
            "previewSha256": preview_hash,
            "postStateSha256": post_hash,
            "receiptSetSha256": receipt_hash,
            "entries": entries,
        }
        manifest = {**core, "backupSha256": _backup_sha256(core)}
        manifest_bytes = manager_contract.canonical_json_bytes(manifest) + b"\n"
        _write_restricted(
            temporary / policy.manifest_name, manifest_bytes, policy.file_mode
        )
        _fsync_directory(temporary)
        slug_root_fd = os.open(slug_root, _directory_flags())
        try:
            try:
                _rename_no_replace(slug_root_fd, temporary.name, final.name)
            except OSError as exc:
                if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise DriftBackupError(
                        "DRIFT_BACKUP_ID_COLLISION",
                        "allocated backup id collided during atomic publish",
                    ) from exc
                raise
            assert temporary_identity is not None
            published_identity = temporary_identity
            temporary_created = False
            published_info = os.stat(
                final.name,
                dir_fd=slug_root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(published_info.st_mode)
                or stat.S_IMODE(published_info.st_mode) != policy.dir_mode
                or _directory_identity(published_info) != published_identity
            ):
                raise DriftBackupError(
                    "DRIFT_BACKUP_CHANGED_DURING_SCAN",
                    "published backup identity does not match private staging",
                )
            os.fsync(slug_root_fd)
        finally:
            os.close(slug_root_fd)
        return load_and_verify_backup(
            runtime_dir,
            normalized_slug,
            backup_id,
            manifest["backupSha256"],
        ).record
    except DriftBackupError as exc:
        try:
            attempted = _cleanup_failed_creation(
                temporary=temporary,
                final=final,
                slug_root=slug_root,
                temporary_created=temporary_created,
                temporary_identity=temporary_identity,
                published_identity=published_identity,
            )
        except DriftBackupError as cleanup_error:
            raise cleanup_error from exc
        if attempted:
            raise DriftBackupError(
                exc.code,
                exc.message,
                attempted=True,
                committed=False,
                rollback_verified=True,
                recovery_paths=tuple(exc.recovery_paths),
                durability_unverified=exc.durability_unverified,
            ) from exc
        raise
    except OSError as exc:
        try:
            attempted = _cleanup_failed_creation(
                temporary=temporary,
                final=final,
                slug_root=slug_root,
                temporary_created=temporary_created,
                temporary_identity=temporary_identity,
                published_identity=published_identity,
            )
        except DriftBackupError as cleanup_error:
            raise cleanup_error from exc
        raise DriftBackupError(
            "DRIFT_BACKUP_WRITE_FAILED",
            f"cannot publish drift backup: {exc}",
            attempted=attempted,
            committed=False if attempted else None,
            rollback_verified=True if attempted else None,
        ) from exc


def _json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key}")
        result[key] = value
    return result


def _load_manifest(content: bytes, policy: _Policy) -> dict[str, Any]:
    try:
        raw = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", f"cannot parse backup manifest: {exc}"
        ) from exc
    if not isinstance(raw, dict) or set(raw) != MANIFEST_FIELDS:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest fields are invalid"
        )
    canonical = manager_contract.canonical_json_bytes(raw) + b"\n"
    if content != canonical:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest is not canonical JSON"
        )
    if (
        isinstance(raw.get("schemaVersion"), bool)
        or raw.get("schemaVersion") != policy.schema_version
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest schemaVersion is invalid"
        )
    return raw


def _validate_manifest_identity(
    manifest: Mapping[str, Any],
    *,
    slug: str,
    backup_id: str,
    policy: _Policy,
) -> None:
    if manifest.get("slug") != slug or manifest.get("backupId") != backup_id:
        raise DriftBackupError(
            "DRIFT_BACKUP_IDENTITY_MISMATCH",
            "backup manifest does not match requested slug and backup id",
        )
    if (
        backup_id in {"", ".", ".."}
        or "/" in backup_id
        or "\\" in backup_id
        or "\x00" in backup_id
        or len(backup_id) not in {23, 27}
        or not CANONICAL_BACKUP_ID_RE.fullmatch(backup_id)
        or not policy.backup_id_pattern.fullmatch(backup_id)
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_ARGUMENT_INVALID", "backup_id is not canonical"
        )
    for field in (
        "previewSha256",
        "postStateSha256",
        "receiptSetSha256",
        "backupSha256",
    ):
        if not isinstance(manifest.get(field), str) or not SHA256_RE.fullmatch(
            manifest[field]
        ):
            raise DriftBackupError(
                "DRIFT_BACKUP_MANIFEST_INVALID", f"backup manifest {field} is invalid"
            )
    created_at = manifest.get("createdAt")
    if not isinstance(created_at, str) or not UTC_CREATED_AT_RE.fullmatch(created_at):
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest createdAt is invalid"
        )
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest createdAt is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest createdAt must be UTC"
        )


def _path_identity(path: Path, mode: int) -> tuple[int, int, int, int, int]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", f"cannot inspect backup path {path.name}: {exc}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", f"backup path {path.name} is not a real directory"
        )
    if stat.S_IMODE(info.st_mode) != mode:
        raise DriftBackupError(
            "DRIFT_BACKUP_PERMISSION_INVALID",
            f"backup directory {path.name} must use mode {oct(mode)}",
        )
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", info.st_mtime * 1_000_000_000)),
        int(info.st_mode),
    )


def _verify_snapshot_file_mode(path: Path, item: safe_input.InputFile, mode: int) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", f"cannot inspect backup file {path.name}: {exc}"
        ) from exc
    actual_identity = (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", info.st_mtime * 1_000_000_000)),
        int(getattr(info, "st_ctime_ns", info.st_ctime * 1_000_000_000)),
        stat.S_IMODE(info.st_mode),
        int(getattr(info, "st_file_attributes", 0)),
    )
    expected_identity = (
        item.device,
        item.inode,
        item.size,
        item.mtime_ns,
        item.ctime_ns,
        item.mode,
        item.file_attributes,
    )
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or actual_identity != expected_identity
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_CHANGED_DURING_SCAN", "backup file changed during verification"
        )
    if stat.S_IMODE(info.st_mode) != mode:
        raise DriftBackupError(
            "DRIFT_BACKUP_PERMISSION_INVALID",
            f"backup file {path.name} must use mode {oct(mode)}",
        )


def load_and_verify_backup(
    runtime_dir: Path,
    slug: str,
    backup_id: str,
    expected_sha256: str,
) -> BackupSnapshot:
    """Load one exact backup through safe_input and verify its complete manifest."""

    _ensure_supported_platform()
    policy = _policy()
    normalized_slug = _slug(slug)
    expected_hash = _sha256(expected_sha256, "expected_sha256")
    if (
        not isinstance(backup_id, str)
        or backup_id in {"", ".", ".."}
        or "/" in backup_id
        or "\\" in backup_id
        or "\x00" in backup_id
        or len(backup_id) not in {23, 27}
        or not CANONICAL_BACKUP_ID_RE.fullmatch(backup_id)
        or not policy.backup_id_pattern.fullmatch(backup_id)
    ):
        raise DriftBackupError(
            "DRIFT_BACKUP_ARGUMENT_INVALID", "backup_id is not canonical"
        )
    slug_root = _existing_roots(runtime_dir, normalized_slug, policy)
    backup_path = slug_root / backup_id
    before_backup = _path_identity(backup_path, policy.dir_mode)
    payload_path = backup_path / policy.payload_directory
    before_payload = _path_identity(payload_path, policy.dir_mode)
    try:
        snapshot = safe_input.inspect(backup_path)
    except safe_input.InputInspectionError as exc:
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", f"backup safety inspection failed: {exc.code}"
        ) from exc
    if snapshot.kind != "directory":
        raise DriftBackupError(
            "DRIFT_BACKUP_UNSAFE", "backup path must be a directory snapshot"
        )
    manifest_item = next(
        (item for item in snapshot.files if item.relative_path == policy.manifest_name),
        None,
    )
    if manifest_item is None:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest is missing"
        )
    manifest = _load_manifest(manifest_item.content, policy)
    _validate_manifest_identity(
        manifest, slug=normalized_slug, backup_id=backup_id, policy=policy
    )
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise DriftBackupError(
            "DRIFT_BACKUP_MANIFEST_INVALID", "backup manifest entries must be non-empty"
        )
    targets: list[TargetState] = []
    expected_files = {policy.manifest_name}
    previous_path = ""
    expected_payload_index = 0
    files_by_path = {item.relative_path: item for item in snapshot.files}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise DriftBackupError(
                "DRIFT_BACKUP_MANIFEST_INVALID", "backup entry fields are invalid"
            )
        relative = _relative_path(entry.get("relativePath"), policy)
        if previous_path and relative <= previous_path:
            raise DriftBackupError(
                "DRIFT_BACKUP_MANIFEST_INVALID",
                "backup entries must be unique and sorted by relativePath",
            )
        previous_path = relative
        present = entry.get("present")
        mode = entry.get("mode")
        if not isinstance(present, bool):
            raise DriftBackupError(
                "DRIFT_BACKUP_MANIFEST_INVALID", "backup entry present must be boolean"
            )
        if present:
            expected_payload_index += 1
            payload_name = f"{expected_payload_index:06d}.bin"
            payload_relative = f"{policy.payload_directory}/{payload_name}"
            if entry.get("payloadFile") != payload_name:
                raise DriftBackupError(
                    "DRIFT_BACKUP_MANIFEST_INVALID",
                    "backup entry payload sequence is invalid",
                )
            payload_hash = entry.get("sha256")
            size = entry.get("size")
            if (
                not isinstance(payload_hash, str)
                or not SHA256_RE.fullmatch(payload_hash)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or (
                    mode is not None
                    and (
                        isinstance(mode, bool)
                        or not isinstance(mode, int)
                        or not 0 <= mode <= 0o777
                    )
                )
            ):
                raise DriftBackupError(
                    "DRIFT_BACKUP_MANIFEST_INVALID", "backup entry evidence is invalid"
                )
            payload = files_by_path.get(payload_relative)
            if payload is None:
                raise DriftBackupError(
                    "DRIFT_BACKUP_MANIFEST_INVALID", "backup payload is missing"
                )
            if payload.sha256 != payload_hash or payload.size != size:
                raise DriftBackupError(
                    "DRIFT_BACKUP_PAYLOAD_MISMATCH", "backup payload hash or size changed"
                )
            expected_files.add(payload_relative)
            targets.append(TargetState(relative, True, payload.content, mode))
        else:
            if any(entry.get(field) is not None for field in ("payloadFile", "sha256", "size", "mode")):
                raise DriftBackupError(
                    "DRIFT_BACKUP_MANIFEST_INVALID",
                    "absent backup entry contains payload evidence",
                )
            targets.append(TargetState(relative, False, None, None))
    actual_files = {item.relative_path for item in snapshot.files}
    if actual_files != expected_files or set(snapshot.directories) != {
        policy.payload_directory
    }:
        raise DriftBackupError(
            "DRIFT_BACKUP_CONTENTS_INVALID",
            "backup contains missing, unknown, or misplaced entries",
        )
    core = {field: manifest[field] for field in MANIFEST_CORE_FIELDS}
    calculated_hash = _backup_sha256(core)
    if manifest["backupSha256"] != calculated_hash:
        raise DriftBackupError(
            "DRIFT_BACKUP_HASH_MISMATCH", "backup manifest hash does not verify"
        )
    if calculated_hash != expected_hash:
        raise DriftBackupError(
            "DRIFT_BACKUP_HASH_MISMATCH", "backup hash does not match confirmation"
        )
    _verify_snapshot_file_mode(
        backup_path / policy.manifest_name, manifest_item, policy.file_mode
    )
    for relative in sorted(expected_files - {policy.manifest_name}):
        _verify_snapshot_file_mode(
            backup_path.joinpath(*PurePosixPath(relative).parts),
            files_by_path[relative],
            policy.file_mode,
        )
    if _path_identity(backup_path, policy.dir_mode) != before_backup or _path_identity(
        payload_path, policy.dir_mode
    ) != before_payload:
        raise DriftBackupError(
            "DRIFT_BACKUP_CHANGED_DURING_SCAN",
            "backup directory identity changed during verification",
        )
    return BackupSnapshot(
        record=_record(backup_path, manifest),
        targets=tuple(targets),
    )


def stage_restore(
    snapshot: BackupSnapshot,
    staging_root: Path,
) -> tuple[dict[str, Path], list[str]]:
    """Materialize verified pre-images into trusted staging; list absent targets."""

    _ensure_supported_platform()
    policy = _policy()
    if not isinstance(snapshot, BackupSnapshot):
        raise DriftBackupError(
            "DRIFT_BACKUP_ARGUMENT_INVALID", "snapshot must be BackupSnapshot"
        )
    root = _absolute_lexical(staging_root)
    try:
        info = os.lstat(root)
    except OSError as exc:
        raise DriftBackupError(
            "DRIFT_RESTORE_STAGING_INVALID", "restore staging root must exist"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DriftBackupError(
            "DRIFT_RESTORE_STAGING_INVALID", "restore staging root is not a real directory"
        )
    staged: dict[str, Path] = {}
    stale: list[str] = []
    for target in snapshot.targets:
        relative = _relative_path(target.relative_path, policy)
        if not target.present:
            stale.append(relative)
            continue
        destination = root.joinpath(*PurePosixPath(relative).parts)
        _ensure_staging_parent(root, destination.parent, policy.dir_mode)
        if destination.exists() or destination.is_symlink():
            raise DriftBackupError(
                "DRIFT_RESTORE_STAGING_INVALID",
                f"restore staging target already exists: {relative}",
            )
        assert target.content is not None
        _write_restricted(
            destination,
            target.content,
            policy.file_mode if target.mode is None else target.mode,
        )
        staged[relative] = destination
    return staged, sorted(stale)


def _ensure_staging_parent(root: Path, parent: Path, mode: int) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise DriftBackupError(
            "DRIFT_RESTORE_STAGING_INVALID",
            "restore target escapes the staging root",
        ) from exc
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                current.mkdir(mode=mode)
                os.chmod(current, mode, follow_symlinks=False)
                info = os.lstat(current)
            except OSError as exc:
                raise DriftBackupError(
                    "DRIFT_RESTORE_STAGING_INVALID",
                    f"cannot create restore staging parent: {exc}",
                ) from exc
        except OSError as exc:
            raise DriftBackupError(
                "DRIFT_RESTORE_STAGING_INVALID",
                f"cannot inspect restore staging parent: {exc}",
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise DriftBackupError(
                "DRIFT_RESTORE_STAGING_INVALID",
                "restore staging parent is not a real directory",
            )
