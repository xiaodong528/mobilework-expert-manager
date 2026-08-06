from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drift_backup
import manager_contract
import posix_noreplace
import secure_transaction
import workspace_lock


@unittest.skipUnless(os.name == "posix", "workspace lock coverage requires POSIX dir_fd")
class WorkspaceLockCoverageBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()
        self.policy = workspace_lock._WorkspaceLockPolicy(
            file_name=".coverage.lock",
            file_mode=0o600,
            protocol_version=2,
            fields=workspace_lock.LOCK_DOCUMENT_FIELDS,
        )

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(workspace_lock.WorkspaceLockError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_policy_loader_rejects_load_and_shape_failures(self) -> None:
        with mock.patch.object(
            workspace_lock.manager_contract,
            "load_policy",
            side_effect=OSError("unreadable"),
        ):
            self.assert_code("WORKSPACE_LOCK_POLICY_INVALID", workspace_lock._load_policy)
        with mock.patch.object(
            workspace_lock.manager_contract,
            "load_policy",
            return_value={"workspaceLock": []},
        ):
            self.assert_code("WORKSPACE_LOCK_POLICY_INVALID", workspace_lock._load_policy)

    def test_workspace_and_low_level_io_fail_closed(self) -> None:
        relative = Path("relative-workspace")
        with mock.patch.object(
            workspace_lock.os.path,
            "abspath",
            return_value=str(self.workspace),
        ):
            self.assertEqual(workspace_lock._workspace_path(relative), self.workspace)

        regular_file = Path(self.temporary.name) / "not-a-directory"
        regular_file.write_text("fixture", encoding="utf-8")
        self.assert_code(
            "WORKSPACE_LOCK_WORKSPACE_INVALID",
            lambda: workspace_lock._workspace_path(regular_file),
        )
        with mock.patch.object(workspace_lock.os, "open", side_effect=OSError("blocked")):
            self.assert_code(
                "WORKSPACE_LOCK_WORKSPACE_INVALID",
                lambda: workspace_lock._open_workspace(self.workspace),
            )
        with mock.patch.object(workspace_lock.os, "write", return_value=0):
            with self.assertRaisesRegex(OSError, "short write"):
                workspace_lock._write_all(1, b"payload")

    def test_lock_stat_and_lost_create_race_have_stable_codes(self) -> None:
        descriptor = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        self.assertIsNone(workspace_lock._stat_lock(descriptor, "missing.lock"))
        with mock.patch.object(workspace_lock.os, "stat", side_effect=OSError("blocked")):
            self.assert_code(
                "WORKSPACE_LOCK_UNSAFE",
                lambda: workspace_lock._stat_lock(descriptor, "lock"),
            )
        with mock.patch.object(workspace_lock, "_stat_lock", return_value=None):
            self.assertEqual(
                workspace_lock._existing_lock_error(descriptor, "lock").code,
                "WORKSPACE_LOCK_CREATE_FAILED",
            )

    def test_lock_document_validation_matrix(self) -> None:
        valid = {
            "ownerToken": "owner",
            "pid": 1,
            "createdAt": "2026-08-06T00:00:00Z",
            "heartbeatAt": "2026-08-06T00:00:00Z",
            "protocolVersion": 2,
        }
        invalid_documents = (
            b"\xff",
            json.dumps({"ownerToken": "owner"}).encode("utf-8"),
            json.dumps({**valid, "protocolVersion": 3}).encode("utf-8"),
            json.dumps({**valid, "ownerToken": ""}).encode("utf-8"),
        )
        for payload in invalid_documents:
            with self.subTest(payload=payload):
                self.assert_code(
                    "WORKSPACE_LOCK_INVALID",
                    lambda payload=payload: workspace_lock._parse_lock_document(
                        payload, self.policy
                    ),
                )

    def test_acquire_blocks_when_no_replace_backend_is_unavailable(self) -> None:
        unavailable = posix_noreplace.NoReplaceUnavailable("blocked")
        with mock.patch.object(
            workspace_lock.posix_noreplace,
            "require_available",
            side_effect=unavailable,
        ):
            self.assert_code(
                "WORKSPACE_LOCK_PLATFORM_BLOCKED",
                lambda: workspace_lock.acquire(self.workspace),
            )


class PosixNoReplaceCoverageBoundaryTests(unittest.TestCase):
    def tearDown(self) -> None:
        posix_noreplace._backend.cache_clear()

    def test_rejects_unsafe_components_and_non_posix_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe path component"):
            posix_noreplace._component("../escape", "source_name")
        posix_noreplace._backend.cache_clear()
        with mock.patch.object(posix_noreplace.os, "name", "nt"):
            with self.assertRaises(posix_noreplace.NoReplaceUnavailable):
                posix_noreplace._backend()

    def test_linux_backend_and_unavailable_probe(self) -> None:
        function = mock.Mock()
        library = SimpleNamespace(renameat2=function)
        posix_noreplace._backend.cache_clear()
        with mock.patch.object(posix_noreplace.os, "name", "posix"), mock.patch.object(
            posix_noreplace.sys, "platform", "linux"
        ), mock.patch.object(posix_noreplace.ctypes, "CDLL", return_value=library):
            selected, flag = posix_noreplace._backend()
        self.assertIs(selected, function)
        self.assertEqual(flag, 0x00000001)
        self.assertEqual(function.restype, posix_noreplace.ctypes.c_int)

        with mock.patch.object(
            posix_noreplace,
            "require_available",
            side_effect=posix_noreplace.NoReplaceUnavailable("blocked"),
        ):
            self.assertFalse(posix_noreplace.available())

    def test_rename_without_optional_errno_constants_preserves_os_error(self) -> None:
        function = mock.Mock(return_value=-1)
        limited_errno = SimpleNamespace(EINVAL=22, ENOSYS=38)
        with mock.patch.object(posix_noreplace, "_backend", return_value=(function, 1)), mock.patch.object(
            posix_noreplace, "errno", limited_errno
        ), mock.patch.object(posix_noreplace.ctypes, "get_errno", return_value=1):
            with self.assertRaises(OSError) as raised:
                posix_noreplace.rename(1, "source", 2, "target")
        self.assertEqual(raised.exception.errno, 1)


@unittest.skipUnless(os.name == "posix", "drift backup coverage requires POSIX dir_fd")
class DriftBackupCoverageBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_document = copy.deepcopy(manager_contract.load_policy())
        self.policy = drift_backup._policy()

    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(drift_backup.DriftBackupError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_policy_loader_and_contract_field_error_matrix(self) -> None:
        with mock.patch.object(
            drift_backup.manager_contract,
            "load_policy",
            side_effect=OSError("unreadable"),
        ):
            self.assert_code("DRIFT_BACKUP_POLICY_INVALID", drift_backup._policy)

        malformed = {"driftRecovery": []}
        with mock.patch.object(
            drift_backup.manager_contract, "load_policy", return_value=malformed
        ):
            self.assert_code("DRIFT_BACKUP_POLICY_INVALID", drift_backup._policy)

        variants = (
            ("rootName", "wrong"),
            ("publishProtocol", "wrong"),
            ("schemaVersion", True),
            ("backupIdPattern", "wrong"),
        )
        for field, value in variants:
            with self.subTest(field=field):
                document = copy.deepcopy(self.policy_document)
                document["driftRecovery"][field] = value
                with mock.patch.object(
                    drift_backup.manager_contract,
                    "load_policy",
                    return_value=document,
                ):
                    self.assert_code("DRIFT_BACKUP_POLICY_INVALID", drift_backup._policy)

    def test_argument_and_target_validation_matrix(self) -> None:
        callbacks = (
            ("DRIFT_BACKUP_ARGUMENT_INVALID", lambda: drift_backup._sha256("bad", "hash")),
            ("DRIFT_BACKUP_ARGUMENT_INVALID", lambda: drift_backup._slug("Bad Slug")),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._relative_path(None, self.policy),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target("a", object(), self.policy),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target(
                    "a", drift_backup.TargetState("b", True, b"x", 0o600), self.policy
                ),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target(
                    "a", drift_backup.TargetState("a", 1, b"x", 0o600), self.policy
                ),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target(
                    "a", drift_backup.TargetState("a", True, b"x", True), self.policy
                ),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target(
                    "a", drift_backup.TargetState("a", True, "text", 0o600), self.policy
                ),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup._normalize_target(
                    "a", drift_backup.TargetState("a", False, b"x", None), self.policy
                ),
            ),
            (
                "DRIFT_BACKUP_TARGET_INVALID",
                lambda: drift_backup.target_state_sha256([]),
            ),
            (
                "DRIFT_RECEIPT_SET_INVALID",
                lambda: drift_backup.receipt_set_sha256([]),
            ),
        )
        for code, callback in callbacks:
            with self.subTest(code=code, callback=callback):
                self.assert_code(code, callback)

    def test_directory_and_restricted_write_failures(self) -> None:
        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temp:
            root = Path(temp)
            regular = root / "regular"
            regular.write_text("fixture", encoding="utf-8")
            self.assert_code(
                "DRIFT_BACKUP_UNSAFE",
                lambda: drift_backup._safe_directory(regular, mode=0o700),
            )
            wrong_mode = root / "wrong-mode"
            wrong_mode.mkdir(mode=0o700)
            os.chmod(wrong_mode, 0o755)
            self.assert_code(
                "DRIFT_BACKUP_PERMISSION_INVALID",
                lambda: drift_backup._safe_directory(wrong_mode, mode=0o700),
            )
            with mock.patch.object(drift_backup.os, "lstat", side_effect=OSError("blocked")):
                self.assert_code(
                    "DRIFT_BACKUP_UNSAFE",
                    lambda: drift_backup._safe_directory(root, mode=0o700),
                )
            with mock.patch.object(drift_backup.os, "write", return_value=0):
                self.assert_code(
                    "DRIFT_BACKUP_WRITE_FAILED",
                    lambda: drift_backup._write_restricted(root / "short", b"x", 0o600),
                )

    def test_no_replace_and_manifest_validation_failures(self) -> None:
        unavailable = posix_noreplace.NoReplaceUnavailable("blocked")
        with mock.patch.object(
            drift_backup.posix_noreplace,
            "require_available",
            side_effect=unavailable,
        ):
            self.assert_code("DRIFT_RECOVERY_PLATFORM_BLOCKED", drift_backup._no_replace_library)
        with mock.patch.object(
            drift_backup.posix_noreplace,
            "rename",
            side_effect=unavailable,
        ):
            self.assert_code(
                "DRIFT_RECOVERY_PLATFORM_BLOCKED",
                lambda: drift_backup._rename_no_replace(1, "source", "target"),
            )

        fields = {field: None for field in drift_backup.MANIFEST_FIELDS}
        invalid_payloads = (
            b"{",
            json.dumps(fields, indent=2).encode("utf-8"),
            manager_contract.canonical_json_bytes(
                {**fields, "schemaVersion": False}
            )
            + b"\n",
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assert_code(
                    "DRIFT_BACKUP_MANIFEST_INVALID",
                    lambda payload=payload: drift_backup._load_manifest(payload, self.policy),
                )

    def test_identity_and_generated_id_helpers_reject_invalid_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            drift_backup._json_object_pairs([("duplicate", 1), ("duplicate", 2)])

        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temp:
            regular = Path(temp) / "runtime-file"
            regular.write_text("fixture", encoding="utf-8")
            self.assert_code(
                "DRIFT_BACKUP_UNSAFE",
                lambda: drift_backup._existing_roots(
                    regular, "demo-expert", self.policy
                ),
            )
            rejecting_policy = SimpleNamespace(
                backup_id_pattern=SimpleNamespace(fullmatch=lambda _value: None)
            )
            self.assert_code(
                "DRIFT_BACKUP_POLICY_INVALID",
                lambda: drift_backup._choose_backup_id(
                    Path(temp), rejecting_policy
                ),
            )

    def test_manifest_identity_rejects_each_external_identifier_boundary(self) -> None:
        backup_id = "20260804T010203.456789Z"
        valid = {
            "slug": "demo-expert",
            "backupId": backup_id,
            "previewSha256": "a" * 64,
            "postStateSha256": "b" * 64,
            "receiptSetSha256": "c" * 64,
            "backupSha256": "d" * 64,
            "createdAt": "2026-08-04T01:02:03.456789Z",
        }
        cases = (
            (
                "DRIFT_BACKUP_IDENTITY_MISMATCH",
                {**valid, "slug": "other-expert"},
                "demo-expert",
                backup_id,
            ),
            (
                "DRIFT_BACKUP_ARGUMENT_INVALID",
                {**valid, "backupId": "."},
                "demo-expert",
                ".",
            ),
            (
                "DRIFT_BACKUP_MANIFEST_INVALID",
                {**valid, "previewSha256": "bad"},
                "demo-expert",
                backup_id,
            ),
            (
                "DRIFT_BACKUP_MANIFEST_INVALID",
                {**valid, "createdAt": "not-utc"},
                "demo-expert",
                backup_id,
            ),
        )
        for code, manifest, slug, requested_id in cases:
            with self.subTest(code=code):
                self.assert_code(
                    code,
                    lambda manifest=manifest, slug=slug, requested_id=requested_id: drift_backup._validate_manifest_identity(
                        manifest,
                        slug=slug,
                        backup_id=requested_id,
                        policy=self.policy,
                    ),
                )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_recursive_cleanup_rejects_special_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "link").symlink_to(root / "missing")
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaisesRegex(OSError, "unexpected special entry"):
                    drift_backup._clear_directory_fd(descriptor)
            finally:
                os.close(descriptor)


@unittest.skipUnless(os.name == "posix", "secure transaction coverage requires POSIX")
class SecureTransactionCoverageBoundaryTests(unittest.TestCase):
    def assert_code(self, code: str, callback) -> None:
        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_platform_prerequisite_error_matrix(self) -> None:
        with mock.patch.object(secure_transaction.os, "name", "nt"):
            self.assert_code(
                "SECURE_TRANSACTION_POSIX_REQUIRED", secure_transaction._require_posix
            )
        with mock.patch.object(secure_transaction.os, "supports_dir_fd", set()):
            self.assert_code(
                "SECURE_TRANSACTION_POSIX_REQUIRED", secure_transaction._require_posix
            )
        with mock.patch.object(
            secure_transaction.posix_noreplace,
            "require_available",
            side_effect=posix_noreplace.NoReplaceUnavailable("blocked"),
        ):
            self.assert_code(
                "SECURE_TRANSACTION_NOREPLACE_REQUIRED",
                secure_transaction._require_posix,
            )

    def test_directory_open_and_input_conflicts_fail_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing" / "child"
            self.assert_code(
                "SECURE_TRANSACTION_UNSAFE_DIRECTORY",
                lambda: secure_transaction._open_absolute_directory(missing, "fixture"),
            )
        self.assert_code(
            "SECURE_TRANSACTION_PATH_CONFLICT",
            lambda: secure_transaction._normalize_inputs(
                {"parent": Path("a"), "parent/child": Path("b")}, (), ()
            ),
        )
        self.assert_code(
            "SECURE_TRANSACTION_PATH_CONFLICT",
            lambda: secure_transaction._normalize_inputs(
                {"conflict": Path("a")}, (), ("conflict",)
            ),
        )

    def test_file_identity_and_required_directory_reject_unsafe_types(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assert_code(
                    "IDENTITY_OPEN_FAILED",
                    lambda: secure_transaction._file_identity_at(
                        descriptor,
                        "missing",
                        message="missing",
                        code="IDENTITY_OPEN_FAILED",
                    ),
                )
                (root / "directory").mkdir()
                self.assert_code(
                    "IDENTITY_TYPE_INVALID",
                    lambda: secure_transaction._file_identity_at(
                        descriptor,
                        "directory",
                        message="directory",
                        code="IDENTITY_TYPE_INVALID",
                    ),
                )
                regular = root / "required"
                regular.write_text("fixture", encoding="utf-8")
                self.assert_code(
                    "SECURE_TRANSACTION_UNSAFE_TARGET",
                    lambda: secure_transaction._inspect_required_directory(
                        descriptor, "required"
                    ),
                )
            finally:
                os.close(descriptor)

    def test_staged_source_and_duplicate_source_boundaries(self) -> None:
        self.assert_code(
            "SECURE_TRANSACTION_UNSAFE_STAGING",
            lambda: secure_transaction._open_staged_source("target", Path(os.sep)),
        )
        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temp:
            root = Path(temp)
            runtime = root / "runtime"
            runtime.mkdir()
            inside = runtime / "inside"
            inside.write_text("inside", encoding="utf-8")
            self.assert_code(
                "SECURE_TRANSACTION_UNSAFE_STAGING",
                lambda: secure_transaction.commit(
                    runtime, {"target": inside}, (), (), None
                ),
            )

            staged = root / "staged"
            staged.write_text("staged", encoding="utf-8")
            self.assert_code(
                "SECURE_TRANSACTION_DUPLICATE_SOURCE",
                lambda: secure_transaction.commit(
                    runtime, {"first": staged, "second": staged}, (), (), None
                ),
            )

    def test_identity_rechecks_and_existing_types_fail_closed(self) -> None:
        metadata = secure_transaction._Identity(1, 1, stat.S_IFREG, 1, 1, 1, 0)
        expected = secure_transaction._FileIdentity(metadata, "a" * 64)
        changed = secure_transaction._FileIdentity(
            secure_transaction._Identity(2, 2, stat.S_IFREG, 1, 1, 1, 0),
            "b" * 64,
        )
        injected = secure_transaction.SecureTransactionError(
            "changed", code="IDENTITY_CHANGED"
        )
        with mock.patch.object(
            secure_transaction, "_file_identity_at", side_effect=injected
        ):
            self.assert_code(
                "IDENTITY_CHANGED",
                lambda: secure_transaction._require_file_identity(
                    1,
                    "name",
                    expected,
                    "changed",
                    code="IDENTITY_CHANGED",
                ),
            )
        with mock.patch.object(
            secure_transaction, "_file_identity_at", return_value=changed
        ):
            self.assert_code(
                "RENAMED_IDENTITY_CHANGED",
                lambda: secure_transaction._require_renamed_file_identity(
                    1,
                    "name",
                    expected,
                    "changed",
                    code="RENAMED_IDENTITY_CHANGED",
                ),
            )

        with tempfile.TemporaryDirectory(
            dir=Path(tempfile.gettempdir()).resolve()
        ) as temp:
            root = Path(temp)
            source_directory = root / "source-directory"
            source_directory.mkdir()
            self.assert_code(
                "SECURE_TRANSACTION_UNSAFE_STAGING",
                lambda: secure_transaction._open_staged_source(
                    "target", source_directory
                ),
            )

            existing = root / "existing"
            existing.write_text("fixture", encoding="utf-8")
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assert_code(
                    "SECURE_TRANSACTION_UNSAFE_TARGET",
                    lambda: secure_transaction._ensure_directory(
                        root_fd,
                        "existing",
                        mode=0o755,
                        created_directories=[],
                    ),
                )
            finally:
                os.close(root_fd)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_backup_cleanup_rejects_special_entry_and_identity_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            backup = root / "backup"
            backup.mkdir()
            (backup / "link").symlink_to(root / "missing")
            descriptor = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assert_code(
                    "SECURE_TRANSACTION_UNSAFE_BACKUP",
                    lambda: secure_transaction._clear_directory(descriptor),
                )
            finally:
                os.close(descriptor)

            other = root / "other"
            other.mkdir()
            runtime_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            backup_fd = os.open(backup, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assert_code(
                    "SECURE_TRANSACTION_BACKUP_CHANGED",
                    lambda: secure_transaction._cleanup_backup(
                        runtime_fd, backup_fd, "other"
                    ),
                )
            finally:
                os.close(backup_fd)
                os.close(runtime_fd)


if __name__ == "__main__":
    unittest.main()
