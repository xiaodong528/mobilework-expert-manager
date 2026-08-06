from __future__ import annotations

import ctypes
import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import secure_transaction


@unittest.skipUnless(os.name == "posix", "secure transactions are POSIX-only")
class SecureTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        secure_temp_root = Path("/private/tmp")
        self.temp = tempfile.TemporaryDirectory(
            dir=secure_temp_root if secure_temp_root.is_dir() else None
        )
        self.root = Path(self.temp.name)
        self.runtime = self.root / "workspace" / ".opencode"
        self.runtime.mkdir(parents=True)
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def stage(self, relative: str, content: str) -> Path:
        path = self.staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def assert_no_backup(self) -> None:
        self.assertEqual(
            list(self.runtime.glob(".install-backup-*")),
            [],
        )

    def test_rejects_symlink_parent_without_touching_outside_tree(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        outside_file = outside / "demo.md"
        outside_file.write_text("outside\n", encoding="utf-8")
        (self.runtime / "agents").symlink_to(outside, target_is_directory=True)
        staged = self.stage("agents/demo.md", "replacement\n")

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                (),
                None,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_UNSAFE_TARGET")
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "outside\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    def test_rejects_symlink_target_without_touching_link_destination(self) -> None:
        target_parent = self.runtime / "agents"
        target_parent.mkdir()
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (target_parent / "demo.md").symlink_to(outside)
        staged = self.stage("agents/demo.md", "replacement\n")

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                (),
                None,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_UNSAFE_TARGET")
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assertTrue((target_parent / "demo.md").is_symlink())
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is unavailable")
    def test_rejects_non_regular_target_without_opening_it(self) -> None:
        target_parent = self.runtime / "agents"
        target_parent.mkdir()
        fifo = target_parent / "demo.md"
        os.mkfifo(fifo)
        staged = self.stage("agents/demo.md", "replacement\n")

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                (),
                None,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_UNSAFE_TARGET")
        self.assertTrue(fifo.exists())
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    def test_guard_failure_performs_zero_transaction_writes(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_text("original\n", encoding="utf-8")
        staged = self.stage("agents/demo.md", "replacement\n")

        def reject() -> None:
            raise RuntimeError("state changed")

        with self.assertRaisesRegex(RuntimeError, "state changed"):
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                ("empty/required",),
                reject,
            )

        self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assertFalse((self.runtime / "empty").exists())
        self.assert_no_backup()

    def test_rejects_same_size_target_rewrite_with_restored_mtime(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_bytes(b"GOOD")
        staged = self.stage("agents/demo.md", "NEXT")
        before = target.stat()

        def rewrite_target() -> None:
            target.write_bytes(b"EVIL")
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                (),
                rewrite_target,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_TARGET_CHANGED")
        self.assertEqual(target.read_bytes(), b"EVIL")
        self.assertEqual(staged.read_text(encoding="utf-8"), "NEXT")
        self.assert_no_backup()

    def test_rejects_same_size_staging_rewrite_with_restored_mtime(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_bytes(b"BASE")
        staged = self.stage("agents/demo.md", "GOOD")
        before = staged.stat()

        def rewrite_staging() -> None:
            staged.write_bytes(b"EVIL")
            os.utime(staged, ns=(before.st_atime_ns, before.st_mtime_ns))

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {"agents/demo.md": staged},
                (),
                (),
                rewrite_staging,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_STAGING_CHANGED")
        self.assertEqual(target.read_bytes(), b"BASE")
        self.assertEqual(staged.read_bytes(), b"EVIL")
        self.assert_no_backup()

    def test_commits_staged_and_stale_files_and_required_directory(self) -> None:
        replacement = self.runtime / "agents/replaced.md"
        replacement.parent.mkdir()
        replacement.write_text("old\n", encoding="utf-8")
        stale = self.runtime / "agents/stale.md"
        stale.write_text("stale\n", encoding="utf-8")
        replacement_source = self.stage("replacement.md", "new\n")
        new_source = self.stage("new.md", "created\n")

        secure_transaction.commit(
            self.runtime,
            {
                "agents/replaced.md": replacement_source,
                "skills/new/SKILL.md": new_source,
            },
            ("agents/stale.md",),
            ("references/empty",),
            None,
        )

        self.assertEqual(replacement.read_text(encoding="utf-8"), "new\n")
        self.assertFalse(stale.exists())
        self.assertEqual(
            (self.runtime / "skills/new/SKILL.md").read_text(encoding="utf-8"),
            "created\n",
        )
        self.assertTrue((self.runtime / "references/empty").is_dir())
        self.assertFalse(replacement_source.exists())
        self.assertFalse(new_source.exists())
        self.assert_no_backup()

    def test_late_target_creation_cannot_be_overwritten_by_staged_publish(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        staged = self.stage("agents/demo.md", "replacement\n")
        real_rename = secure_transaction._rename_at

        def create_late_target(
            source_fd: int,
            source: str,
            target_fd: int,
            target_name: str,
        ) -> None:
            descriptor = os.open(
                target_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_fd,
            )
            try:
                os.write(descriptor, b"late\n")
            finally:
                os.close(descriptor)
            real_rename(source_fd, source, target_fd, target_name)

        with patch.object(
            secure_transaction,
            "_rename_at",
            side_effect=create_late_target,
        ):
            with self.assertRaises(
                secure_transaction.SecureTransactionRolledBackError
            ) as raised:
                secure_transaction.commit(
                    self.runtime,
                    {"agents/demo.md": staged},
                    (),
                    (),
                    None,
                )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_ROLLED_BACK")
        self.assertFalse(raised.exception.committed)
        self.assertTrue(raised.exception.rollback_verified)
        self.assertEqual(target.read_text(encoding="utf-8"), "late\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    def test_filesystem_no_replace_loss_rolls_back_without_target_change(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_text("original\n", encoding="utf-8")
        staged = self.stage("agents/demo.md", "replacement\n")

        with patch.object(
            secure_transaction.posix_noreplace,
            "rename",
            side_effect=secure_transaction.posix_noreplace.NoReplaceUnavailable(
                "filesystem rejected no-replace"
            ),
        ):
            with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
                secure_transaction.commit(
                    self.runtime,
                    {"agents/demo.md": staged},
                    (),
                    (),
                    None,
                )

        self.assertEqual(
            raised.exception.code,
            "SECURE_TRANSACTION_NOREPLACE_REQUIRED",
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    def test_einval_is_classified_as_unavailable_no_replace_support(self) -> None:
        def reject_flag(*_arguments) -> int:
            ctypes.set_errno(errno.EINVAL)
            return -1

        with patch.object(
            secure_transaction.posix_noreplace,
            "_backend",
            return_value=(reject_flag, 1),
        ):
            with self.assertRaises(
                secure_transaction.posix_noreplace.NoReplaceUnavailable
            ):
                secure_transaction.posix_noreplace.rename(1, "source", 2, "target")

    def test_commit_failure_rolls_back_original_and_staged_source(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_text("original\n", encoding="utf-8")
        staged = self.stage("agents/demo.md", "replacement\n")
        real_rename = secure_transaction._rename_at
        calls = 0

        def fail_write(source_fd: int, source: str, target_fd: int, target: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            real_rename(source_fd, source, target_fd, target)

        with patch.object(secure_transaction, "_rename_at", side_effect=fail_write):
            with self.assertRaises(
                secure_transaction.SecureTransactionRolledBackError
            ) as raised:
                secure_transaction.commit(
                    self.runtime,
                    {"agents/demo.md": staged},
                    (),
                    (),
                    None,
                )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_ROLLED_BACK")
        self.assertFalse(raised.exception.committed)
        self.assertTrue(raised.exception.rollback_verified)
        self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        self.assert_no_backup()

    def test_rollback_failure_reports_preserved_recovery_paths(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_text("original\n", encoding="utf-8")
        staged = self.stage("agents/demo.md", "replacement\n")
        real_rename = secure_transaction._rename_at
        calls = 0

        def fail_write_and_restore(
            source_fd: int,
            source: str,
            target_fd: int,
            target_name: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("simulated rename failure")
            real_rename(source_fd, source, target_fd, target_name)

        with patch.object(
            secure_transaction,
            "_rename_at",
            side_effect=fail_write_and_restore,
        ):
            with self.assertRaises(
                secure_transaction.SecureTransactionRecoveryError
            ) as raised:
                secure_transaction.commit(
                    self.runtime,
                    {"agents/demo.md": staged},
                    (),
                    (),
                    None,
                )

        self.assertEqual(
            raised.exception.code,
            "SECURE_TRANSACTION_ROLLBACK_FAILED",
        )
        self.assertIsNone(raised.exception.committed)
        self.assertFalse(raised.exception.rollback_verified)
        self.assertIn(str(target), raised.exception.recovery_paths)
        backup_paths = [
            Path(value)
            for value in raised.exception.recovery_paths
            if ".install-backup-" in value and Path(value).is_file()
        ]
        self.assertEqual(len(backup_paths), 1)
        self.assertEqual(backup_paths[0].read_text(encoding="utf-8"), "original\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")

    def test_late_target_creation_cannot_be_overwritten_during_rollback(self) -> None:
        target = self.runtime / "agents/demo.md"
        target.parent.mkdir()
        target.write_text("original\n", encoding="utf-8")
        staged = self.stage("agents/demo.md", "replacement\n")
        real_rename = secure_transaction._rename_at
        calls = 0

        def create_late_rollback_target(
            source_fd: int,
            source: str,
            target_fd: int,
            target_name: str,
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 4:
                descriptor = os.open(
                    target_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=target_fd,
                )
                try:
                    os.write(descriptor, b"late\n")
                finally:
                    os.close(descriptor)
            real_rename(source_fd, source, target_fd, target_name)

        with patch.object(
            secure_transaction,
            "_rename_at",
            side_effect=create_late_rollback_target,
        ), patch.object(
            secure_transaction,
            "_ensure_directory",
            side_effect=OSError("simulated post-write failure"),
        ):
            with self.assertRaises(
                secure_transaction.SecureTransactionRecoveryError
            ) as raised:
                secure_transaction.commit(
                    self.runtime,
                    {"agents/demo.md": staged},
                    (),
                    ("references/required",),
                    None,
                )

        self.assertEqual(
            raised.exception.code,
            "SECURE_TRANSACTION_ROLLBACK_FAILED",
        )
        self.assertFalse(raised.exception.rollback_verified)
        self.assertEqual(target.read_text(encoding="utf-8"), "late\n")
        self.assertEqual(staged.read_text(encoding="utf-8"), "replacement\n")
        backup_paths = [
            Path(value)
            for value in raised.exception.recovery_paths
            if ".install-backup-" in value and Path(value).is_file()
        ]
        self.assertEqual(len(backup_paths), 1)
        self.assertEqual(backup_paths[0].read_text(encoding="utf-8"), "original\n")

    def test_rejects_path_escape_before_guard_or_mutation(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        guard_called = False

        def guard() -> None:
            nonlocal guard_called
            guard_called = True

        with self.assertRaises(secure_transaction.SecureTransactionError) as raised:
            secure_transaction.commit(
                self.runtime,
                {},
                ("../../outside.md",),
                (),
                guard,
            )

        self.assertEqual(raised.exception.code, "SECURE_TRANSACTION_INVALID_PATH")
        self.assertFalse(guard_called)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assert_no_backup()


if __name__ == "__main__":
    unittest.main()
