from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import re
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
TESTS = SKILL_ROOT / "tests"
CREATE = SCRIPTS / "create_expert.py"
INSTALL = SCRIPTS / "install_expert.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import install_expert as installer
import install_state
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


SLUG = "contract-review-expert"
AGENT_ID = "contract-reviewer"
CANARY = "SENSITIVE-CANARY-DO-NOT-LEAK-DRIFT-RESTORE"
WRONG_HASH = "0" * 64
MISSING_BACKUP_ID = "20000101T000000.000000Z"


class DriftRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.reference_host = self.root / "host-references.json"
        self.reference_host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def file_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def write_manifest(self) -> Path:
        data = json.loads(load_spec_text("legacy-expert-json"))
        data["slug"] = SLUG
        data["name"] = f"{SLUG} 专家"
        data["agent"]["id"] = AGENT_ID
        data["agent"]["name"] = data["name"]
        data["agent"]["display_name"] = data["name"]
        data["avatar_url"] = f"avatars/{SLUG}.png"
        data["agent"]["avatar_url"] = f"avatars/{AGENT_ID}.png"
        data["common_skills"] = [{"purpose": "delivery-quality"}]
        data["agent"]["skills"] = [
            {"purpose": "role-guidelines"},
            {"purpose": "checklist"},
        ]
        data["agent"]["permission"].pop("skill", None)
        extensions = data["runtime_extensions"]
        extensions["reference_files"][0]["path"] = (
            f".opencode/references/{SLUG}/playbook/overview.md"
        )
        extensions["references"]["playbook"]["path"] = (
            f".opencode/references/{SLUG}/playbook"
        )
        extensions["instruction_files"][0]["path"] = (
            f".opencode/instructions/{SLUG}/evidence.md"
        )
        extensions["instruction_files"][1]["path"] = (
            f".opencode/instructions/{SLUG}/roles/source-policy.md"
        )
        extensions["role_instructions"]["source-policy"]["path"] = (
            f".opencode/instructions/{SLUG}/roles/source-policy.md"
        )
        extensions["instructions"] = [f".opencode/instructions/{SLUG}/*.md"]
        extensions.setdefault("plugins", {})["package_json"] = {
            "dependencies": {"owned-dependency": "1.2.3"}
        }
        manifest = self.root / "source" / "expert.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest

    def generate(self) -> Path:
        output = self.root / "packages"
        result = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(self.write_manifest()),
                "--output-dir",
                str(output),
            ],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output / SLUG

    def run_manager(
        self,
        *arguments: str,
        schema_version: int = 2,
        output_format: str = "json",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                *arguments,
                "--workspace-dir",
                str(self.workspace),
                "--format",
                output_format,
                "--schema-version",
                str(schema_version),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def install_arguments(self, package: Path) -> list[str]:
        return [
            "--package-dir",
            str(package),
            "--host-contract",
            str(self.reference_host),
        ]

    def prepare_drift(self) -> tuple[Path, dict[str, Path], dict[str, bytes]]:
        package = self.generate()
        installed = self.run_manager(*self.install_arguments(package))
        self.assertEqual(installed.returncode, 0, installed.stderr)

        runtime = self.workspace / ".opencode"
        receipt_path = runtime / ".expert-installs" / f"{SLUG}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        owned_relative = next(
            relative
            for relative in sorted(receipt["files"])
            if relative.endswith((".md", ".json"))
        )
        owned_path = runtime / owned_relative
        owned_path.write_text(f"{CANARY}:owned-file\n", encoding="utf-8")

        config_path = runtime / "opencode.jsonc"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["references"][f"{SLUG}-playbook"]["description"] = (
            f"{CANARY}:config"
        )
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        package_path = runtime / "package.json"
        package_json = json.loads(package_path.read_text(encoding="utf-8"))
        package_json["dependencies"]["owned-dependency"] = f"{CANARY}:dependency"
        package_path.write_text(
            json.dumps(package_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths = {
            "config": config_path,
            "owned": owned_path,
            "package": package_path,
            "receipt": receipt_path,
        }
        return package, paths, {name: path.read_bytes() for name, path in paths.items()}

    def force_preview(self, package: Path) -> tuple[subprocess.CompletedProcess[str], str]:
        result = self.run_manager(*self.install_arguments(package), "--force")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "drift-detected")
        preview_sha256 = payload["data"]["previewSha256"]
        self.assertRegex(preview_sha256, r"^[0-9a-f]{64}$")
        return result, preview_sha256

    def restore_arguments(
        self,
        backup_id: str,
        backup_sha256: str,
        slug: str = SLUG,
    ) -> list[str]:
        return [
            "--restore-drift-backup",
            backup_id,
            "--expected-backup-sha256",
            backup_sha256,
            "--confirm-restore-drift",
            slug,
        ]

    def test_discard_confirmation_triplet_is_all_or_none_and_zero_write(self) -> None:
        package, _, _ = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        before = self.file_bytes(self.workspace)
        install = self.install_arguments(package)
        triplet = [
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
        ]

        missing_cases = [
            triplet[1:],
            [triplet[0], *triplet[3:]],
            triplet[:3],
        ]
        for missing in missing_cases:
            with self.subTest(arguments=missing):
                result = self.run_manager(*install, "--force", *missing)
                self.assertEqual(result.returncode, 2, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["operation"], "install-expert")
                self.assertEqual(payload["status"], "argument-error")
                self.assertEqual(self.file_bytes(self.workspace), before)

        self.assertFalse(
            (self.workspace / ".opencode" / ".expert-drift-backups").exists()
        )

    def test_force_preview_and_wrong_confirmation_are_stable_zero_write(self) -> None:
        package, _, _ = self.prepare_drift()
        before = self.file_bytes(self.workspace)
        first, preview_sha256 = self.force_preview(package)
        second, second_sha256 = self.force_preview(package)
        self.assertEqual(second_sha256, preview_sha256)
        self.assertEqual(json.loads(first.stdout)["data"], json.loads(second.stdout)["data"])
        self.assertEqual(self.file_bytes(self.workspace), before)

        for expected_hash, confirmed_slug in [
            (WRONG_HASH, SLUG),
            (preview_sha256, "wrong-expert"),
        ]:
            with self.subTest(hash=expected_hash, slug=confirmed_slug):
                result = self.run_manager(
                    *self.install_arguments(package),
                    "--force",
                    "--discard-drift",
                    "--expected-drift-sha256",
                    expected_hash,
                    "--confirm-discard-drift",
                    confirmed_slug,
                )
                self.assertEqual(
                    result.returncode,
                    1 if os.name == "posix" else 4,
                    result.stderr,
                )
                if os.name != "posix":
                    self.assertEqual(
                        json.loads(result.stdout)["findings"][0]["code"],
                        "INSTALL_DRIFT_RECOVERY_PLATFORM_BLOCKED",
                    )
                self.assertEqual(self.file_bytes(self.workspace), before)

        self.assertFalse(
            (self.workspace / ".opencode" / ".expert-drift-backups").exists()
        )

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_discard_backup_and_restore_round_trip_with_exact_guards(self) -> None:
        package, paths, drift_bytes = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        discarded = self.run_manager(
            *self.install_arguments(package),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
        )
        self.assertEqual(discarded.returncode, 0, discarded.stderr)
        self.assertNotIn(CANARY, discarded.stdout)
        self.assertNotIn(CANARY, discarded.stderr)
        discarded_payload = json.loads(discarded.stdout)
        backup_data = discarded_payload["data"]["driftBackup"]
        backup_id = backup_data["backupId"]
        backup_sha256 = backup_data["backupSha256"]

        receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
        self.assertEqual(receipt["contract"], 3)
        runtime = self.workspace / ".opencode"
        captured = install_state.capture_runtime_inputs(
            runtime, target_paths=receipt["files"]
        )
        clean = install_state.verify_owned_state(
            runtime,
            captured.receipts[SLUG],
            captured.receipts,
            snapshot=captured,
        )
        self.assertTrue(clean["ok"])
        self.assertEqual(clean["status"], "clean")

        backup_path = (
            runtime / ".expert-drift-backups" / SLUG / backup_id
        )
        manifest_path = backup_path / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        self.assertNotIn(CANARY.encode(), manifest_bytes)
        payload_bytes = b"".join(
            path.read_bytes()
            for path in sorted((backup_path / "payload").glob("*.bin"))
        )
        self.assertIn(CANARY.encode(), payload_bytes)
        for directory in [
            runtime / ".expert-drift-backups",
            runtime / ".expert-drift-backups" / SLUG,
            backup_path,
            backup_path / "payload",
        ]:
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in backup_path.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        post_install = self.file_bytes(self.workspace)
        guarded_failures = [
            self.restore_arguments(MISSING_BACKUP_ID, backup_sha256),
            self.restore_arguments(backup_id, WRONG_HASH),
            self.restore_arguments(backup_id, backup_sha256, "wrong-expert"),
        ]
        for arguments in guarded_failures:
            with self.subTest(arguments=arguments):
                result = self.run_manager(*arguments)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout.count(CANARY), 0)
                self.assertEqual(self.file_bytes(self.workspace), post_install)

        post_owned_bytes = paths["owned"].read_bytes()
        paths["owned"].write_text("post-install-user-change\n", encoding="utf-8")
        changed_post_install = self.file_bytes(self.workspace)
        changed_restore = self.run_manager(
            *self.restore_arguments(backup_id, backup_sha256)
        )
        self.assertEqual(changed_restore.returncode, 1, changed_restore.stderr)
        self.assertEqual(json.loads(changed_restore.stdout)["status"], "restore-blocked")
        self.assertEqual(self.file_bytes(self.workspace), changed_post_install)
        paths["owned"].write_bytes(post_owned_bytes)

        restored = self.run_manager(
            *self.restore_arguments(backup_id, backup_sha256)
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertNotIn(CANARY, restored.stdout)
        restored_payload = json.loads(restored.stdout)
        self.assertEqual(restored_payload["operation"], "restore-expert-drift")
        self.assertEqual(restored_payload["status"], "drift-restored")
        self.assertEqual(restored_payload["evidenceLevel"], "valid")
        self.assertEqual(restored_payload["gates"]["install"], "blocked")
        self.assertEqual(restored_payload["data"]["restoredState"], "drifted")
        for name, expected in drift_bytes.items():
            self.assertEqual(paths[name].read_bytes(), expected, name)

        after_restore = self.file_bytes(self.workspace)
        repeated = self.run_manager(
            *self.restore_arguments(backup_id, backup_sha256)
        )
        self.assertEqual(repeated.returncode, 1, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["status"], "restore-blocked")
        self.assertEqual(self.file_bytes(self.workspace), after_restore)

    def test_restore_parse_error_uses_restore_operation_hint(self) -> None:
        result = self.run_manager(
            "--restore-drift-backup",
            MISSING_BACKUP_ID,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["operation"], "restore-expert-drift")
        self.assertEqual(payload["status"], "argument-error")

    def test_schema_adapters_execute_manager_action_once(self) -> None:
        arguments = [
            "--package-dir",
            str(self.root / "not-opened-by-mock"),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        result = installer._success_result(
            "install-expert",
            {
                "ok": True,
                "schemaVersion": 2,
                "status": "installable",
                "evidenceLevel": "installable",
            },
        )
        for schema_version in (1, 2):
            with self.subTest(schema_version=schema_version):
                stdout = io.StringIO()
                with mock.patch.object(
                    installer, "_execute", return_value=result
                ) as execute, contextlib.redirect_stdout(stdout):
                    exit_code = installer.main(
                        [*arguments, "--schema-version", str(schema_version)]
                    )
                self.assertEqual(exit_code, 0)
                self.assertEqual(execute.call_count, 1)
                json.loads(stdout.getvalue())

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_human_flow_exposes_only_required_confirmation_evidence(self) -> None:
        package, _, _ = self.prepare_drift()
        preview = self.run_manager(
            *self.install_arguments(package),
            "--force",
            output_format="human",
        )
        self.assertEqual(preview.returncode, 1, preview.stderr)
        preview_match = re.search(r"previewSha256: ([0-9a-f]{64})", preview.stdout)
        self.assertIsNotNone(preview_match)
        preview_sha256 = preview_match.group(1)
        self.assertIn(f"slug: {SLUG}", preview.stdout)

        discarded = self.run_manager(
            *self.install_arguments(package),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
            output_format="human",
        )
        self.assertEqual(discarded.returncode, 0, discarded.stderr)
        backup_id = re.search(
            r"backupId: ([0-9]{8}T[0-9]{6}\.[0-9]{6}Z(?:-[0-9]{3})?)",
            discarded.stdout,
        )
        backup_hash = re.search(r"backupSha256: ([0-9a-f]{64})", discarded.stdout)
        self.assertIsNotNone(backup_id)
        self.assertIsNotNone(backup_hash)
        self.assertNotIn(CANARY, discarded.stdout)
        self.assertNotIn(CANARY, discarded.stderr)

        restored = self.run_manager(
            *self.restore_arguments(backup_id.group(1), backup_hash.group(1)),
            output_format="human",
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("restore-expert-drift: drift-restored", restored.stdout)
        self.assertIn(f"backupId: {backup_id.group(1)}", restored.stdout)
        self.assertIn(f"backupSha256: {backup_hash.group(1)}", restored.stdout)
        self.assertNotIn(CANARY, restored.stdout)

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_backup_publish_backend_loss_reports_private_recovery_path(self) -> None:
        package, _, _ = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
            "--format",
            "json",
        ]
        blocked = installer.drift_backup.DriftBackupError(
            "DRIFT_RECOVERY_PLATFORM_BLOCKED",
            "filesystem rejected no-replace",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer.drift_backup,
            "_rename_no_replace",
            side_effect=blocked,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "backup-recovery-required")
        self.assertTrue(payload["execution"]["attempted"])
        self.assertFalse(payload["data"]["committed"])
        self.assertFalse(payload["data"]["rollbackVerified"])
        self.assertEqual(len(payload["data"]["recoveryPaths"]), 1)
        recovery_path = Path(payload["data"]["recoveryPaths"][0])
        self.assertTrue(recovery_path.is_dir())
        self.assertTrue(recovery_path.name.startswith(".tmp-"))
        self.assertNotIn(CANARY, stdout.getvalue())
        self.assertNotIn(CANARY, stderr.getvalue())

    def test_non_posix_recovery_gate_precedes_protocol_v2_lock(self) -> None:
        policy = installer.manager_contract.load_policy()
        blocked_argv = [
            "--package-dir",
            str(self.root / "not-opened"),
            "--workspace-dir",
            str(self.workspace),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            WRONG_HASH,
            "--confirm-discard-drift",
            SLUG,
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer, "_posix_recovery_backend_available", return_value=False
        ), mock.patch.object(
            installer, "_posix_platform", return_value=False
        ), mock.patch.object(
            installer.workspace_lock, "acquire"
        ) as acquire, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            exit_code = installer.main(blocked_argv)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 4)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["execution"]["attempted"])
        acquire.assert_not_called()

        ordinary_args = installer.parse_args(
            [
                "--uninstall",
                SLUG,
                "--workspace-dir",
                str(self.workspace),
                "--format",
                "json",
            ],
            policy,
        )
        ordinary_result = installer._success_result(
            "uninstall-expert",
            {
                "ok": True,
                "schemaVersion": 2,
                "status": "uninstalled",
                "evidenceLevel": "valid",
            },
        )
        mutation_lock = mock.Mock()
        with mock.patch.object(
            installer, "_posix_recovery_backend_available", return_value=False
        ), mock.patch.object(
            installer, "_posix_platform", return_value=False
        ), mock.patch.object(
            installer.workspace_lock, "acquire", return_value=mutation_lock
        ) as acquire, mock.patch.object(
            installer, "_execute_locked", return_value=ordinary_result
        ) as execute:
            self.assertIs(installer._execute(ordinary_args), ordinary_result)
        acquire.assert_called_once_with(self.workspace.resolve())
        execute.assert_called_once()
        self.assertIs(execute.call_args.args[3], mutation_lock)
        mutation_lock.release.assert_called_once_with()

        stdout = io.StringIO()
        stderr = io.StringIO()
        unsupported_posix_argv = [
            "--uninstall",
            SLUG,
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        with mock.patch.object(
            installer, "_posix_platform", return_value=True
        ), mock.patch.object(
            installer, "_posix_recovery_backend_available", return_value=False
        ), mock.patch.object(
            installer.workspace_lock, "acquire"
        ) as acquire, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            exit_code = installer.main(unsupported_posix_argv)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 4)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(
            payload["findings"][0]["code"],
            "INSTALL_TRANSACTION_PLATFORM_BLOCKED",
        )
        acquire.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "secure transactions are POSIX-only")
    def test_filesystem_no_replace_loss_is_attempted_runtime_policy_failure(self) -> None:
        package = self.generate()
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer.secure_transaction.posix_noreplace,
            "rename",
            side_effect=installer.secure_transaction.posix_noreplace.NoReplaceUnavailable(
                "filesystem rejected no-replace"
            ),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(stdout.getvalue())
        self.assertEqual(stdout.getvalue()[end:].strip(), "")
        self.assertEqual(exit_code, 4)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(
            payload["findings"][0]["code"],
            "INSTALL_TRANSACTION_PLATFORM_BLOCKED",
        )
        self.assertTrue(payload["execution"]["attempted"])
        self.assertFalse(payload["data"]["committed"])
        self.assertTrue(payload["data"]["rollbackVerified"])
        self.assertFalse((self.workspace / ".opencode").exists())

    @unittest.skipUnless(os.name == "posix", "secure transactions are POSIX-only")
    def test_verified_transaction_rollback_is_reported_and_staging_is_cleaned(self) -> None:
        package = self.generate()
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer.secure_transaction,
            "_ensure_directory",
            side_effect=OSError("injected post-write failure"),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "transaction-rolled-back")
        self.assertTrue(payload["execution"]["attempted"])
        self.assertFalse(payload["data"]["committed"])
        self.assertTrue(payload["data"]["rollbackVerified"])
        self.assertEqual(payload["data"]["recoveryPaths"], [])
        self.assertFalse((self.workspace / ".opencode").exists())
        self.assertEqual(list(self.workspace.glob(f".{SLUG}.install-*")), [])

    @unittest.skipUnless(os.name == "posix", "secure transactions are POSIX-only")
    def test_first_install_cleanup_failure_preserves_reported_runtime_recovery_path(self) -> None:
        package = self.generate()
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        recovery_paths: list[Path] = []

        def fail_with_recovery_path(
            runtime_dir: Path,
            staged: dict[str, Path],
            stale: list[str],
            required_directories: list[str] | None = None,
            pre_commit_guard=None,
            *,
            secure: bool = False,
        ) -> None:
            del staged, stale, required_directories, pre_commit_guard, secure
            recovery_path = runtime_dir / ".install-backup-injected"
            recovery_path.mkdir(mode=0o700)
            (recovery_path / "owned.bin").write_bytes(b"preserve")
            recovery_paths.append(recovery_path)
            raise installer.InstallRecoveryError(
                "injected cleanup failure",
                [str(recovery_path)],
                code="SECURE_TRANSACTION_CLEANUP_FAILED",
                committed=False,
                rollback_verified=True,
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer,
            "commit_transaction",
            side_effect=fail_with_recovery_path,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "transaction-recovery-required")
        self.assertFalse(payload["data"]["committed"])
        self.assertTrue(payload["data"]["rollbackVerified"])
        self.assertEqual(len(recovery_paths), 1)
        self.assertTrue(recovery_paths[0].is_dir())
        self.assertIn(str(recovery_paths[0]), payload["data"]["recoveryPaths"])
        self.assertEqual(list(self.workspace.glob(f".{SLUG}.install-*")), [])

    @unittest.skipUnless(os.name == "posix", "workspace lock backend is POSIX-only")
    def test_ordinary_install_and_uninstall_recheck_lock_before_secure_commit(self) -> None:
        package = self.generate()
        real_commit = installer.secure_transaction.commit
        guarded_operations: list[str] = []

        def guarded_commit(
            runtime_dir: Path,
            staged: dict[str, Path],
            stale,
            required_directories,
            pre_commit_guard,
        ) -> None:
            self.assertIsNotNone(pre_commit_guard)
            pre_commit_guard()
            guarded_operations.append(
                "uninstall" if any(path.endswith(f"/{SLUG}.json") for path in stale) else "install"
            )
            real_commit(
                runtime_dir,
                staged,
                stale,
                required_directories,
                pre_commit_guard,
            )

        install_argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        uninstall_argv = [
            "--uninstall",
            SLUG,
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        with mock.patch.object(
            installer.secure_transaction,
            "commit",
            side_effect=guarded_commit,
        ):
            for argv in (install_argv, uninstall_argv):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = installer.main(argv)
                self.assertEqual(exit_code, 0, stderr.getvalue())
                json.loads(stdout.getvalue())

        self.assertEqual(guarded_operations, ["install", "uninstall"])

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_post_commit_readback_failure_retains_backup_evidence(self) -> None:
        package, _, _ = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer.projection_contract,
            "verify_receipt",
            side_effect=RuntimeError("injected readback failure"),
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "committed-unverified")
        self.assertTrue(payload["execution"]["attempted"])
        self.assertTrue(payload["data"]["committed"])
        backup = payload["data"]["driftBackup"]
        self.assertRegex(backup["backupId"], r"^[0-9]{8}T")
        self.assertRegex(backup["backupSha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(CANARY, stdout.getvalue())
        self.assertNotIn(CANARY, stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_restore_policy_and_verified_rollback_keep_backup_evidence(self) -> None:
        package, _, _ = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        discarded = self.run_manager(
            *self.install_arguments(package),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
        )
        self.assertEqual(discarded.returncode, 0, discarded.stderr)
        backup = json.loads(discarded.stdout)["data"]["driftBackup"]
        argv = [
            *self.restore_arguments(backup["backupId"], backup["backupSha256"]),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        failures = (
            (
                4,
                installer.cli_contract.CliRuntimePolicyError(
                    "filesystem rejected no-replace",
                    code="INSTALL_TRANSACTION_PLATFORM_BLOCKED",
                    phase="install-transaction",
                    attempted=True,
                    data={"committed": False, "rollbackVerified": True},
                ),
            ),
            (
                3,
                installer.cli_contract.CliInternalError(
                    "transaction rolled back",
                    code="SECURE_TRANSACTION_ROLLED_BACK",
                    status="transaction-rolled-back",
                    phase="install-transaction",
                    attempted=True,
                    data={"committed": False, "rollbackVerified": True},
                ),
            ),
        )
        for expected_exit, failure in failures:
            with self.subTest(expected_exit=expected_exit):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(
                    installer,
                    "commit_transaction",
                    side_effect=failure,
                ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = installer.main(argv)
                payload = json.loads(stdout.getvalue())
                self.assertEqual(exit_code, expected_exit)
                self.assertTrue(payload["execution"]["attempted"])
                self.assertFalse(payload["data"]["committed"])
                self.assertTrue(payload["data"]["rollbackVerified"])
                self.assertEqual(
                    payload["data"]["driftBackup"]["backupId"],
                    backup["backupId"],
                )
                self.assertEqual(
                    payload["provenance"]["driftBackup"]["backupSha256"],
                    backup["backupSha256"],
                )

    @unittest.skipUnless(os.name == "posix", "workspace lock backend is POSIX-only")
    def test_uninstall_post_commit_readback_failure_is_one_attempted_json(self) -> None:
        package = self.generate()
        installed = self.run_manager(*self.install_arguments(package))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        argv = [
            "--uninstall",
            SLUG,
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer,
            "load_jsonc",
            return_value={"injected": "mismatch"},
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "committed-unverified")
        self.assertEqual(
            payload["findings"][0]["code"],
            "UNINSTALL_COMMITTED_READBACK_FAILED",
        )
        self.assertTrue(payload["execution"]["attempted"])
        self.assertTrue(payload["data"]["committed"])
        self.assertFalse(payload["data"]["readbackVerified"])
        self.assertEqual(stdout.getvalue()[end:].strip(), "")

    @unittest.skipUnless(os.name == "posix", "drift recovery is policy-blocked off POSIX")
    def test_restore_transaction_and_lock_release_failure_preserve_evidence(self) -> None:
        package, _, _ = self.prepare_drift()
        _, preview_sha256 = self.force_preview(package)
        discarded = self.run_manager(
            *self.install_arguments(package),
            "--force",
            "--discard-drift",
            "--expected-drift-sha256",
            preview_sha256,
            "--confirm-discard-drift",
            SLUG,
        )
        self.assertEqual(discarded.returncode, 0, discarded.stderr)
        backup = json.loads(discarded.stdout)["data"]["driftBackup"]
        argv = [
            *self.restore_arguments(backup["backupId"], backup["backupSha256"]),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        recovery_paths: list[Path] = []

        def fail_transaction(
            runtime_dir: Path,
            staged: dict[str, Path],
            stale: list[str],
            required_directories: list[str] | None = None,
            pre_commit_guard=None,
            *,
            secure: bool = False,
        ) -> None:
            del runtime_dir, stale, required_directories, pre_commit_guard, secure
            recovery_path = next(iter(staged.values()))
            recovery_paths.append(recovery_path)
            raise installer.InstallRecoveryError(
                "injected rollback failure",
                [str(recovery_path)],
                code="SECURE_TRANSACTION_ROLLBACK_FAILED",
                committed=None,
                rollback_verified=False,
            )
        release_error = installer.workspace_lock.WorkspaceLockError(
            "WORKSPACE_LOCK_RELEASE_FAILED",
            "injected release failure",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            installer,
            "commit_transaction",
            side_effect=fail_transaction,
        ), mock.patch.object(
            installer.workspace_lock.WorkspaceMutationLock,
            "release",
            side_effect=release_error,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "transaction-recovery-required")
        self.assertTrue(payload["execution"]["attempted"])
        self.assertIsNone(payload["data"]["committed"])
        self.assertFalse(payload["data"]["rollbackVerified"])
        self.assertTrue(payload["data"]["recoveryPaths"])
        self.assertEqual(len(recovery_paths), 1)
        self.assertTrue(recovery_paths[0].is_file())
        self.assertEqual(
            payload["data"]["lockReleaseCode"],
            "WORKSPACE_LOCK_RELEASE_FAILED",
        )
        self.assertFalse(payload["data"]["lockReleaseVerified"])
        self.assertEqual(
            payload["data"]["driftBackup"]["backupId"],
            backup["backupId"],
        )
        self.assertEqual(
            payload["data"]["driftBackup"]["backupSha256"],
            backup["backupSha256"],
        )
        self.assertNotIn(CANARY, stdout.getvalue())
        self.assertNotIn(CANARY, stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "workspace lock backend is POSIX-only")
    def test_successful_commit_with_release_failure_is_attempted_exit_three(self) -> None:
        package = self.generate()
        argv = [
            *self.install_arguments(package),
            "--workspace-dir",
            str(self.workspace),
            "--format",
            "json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        release_error = installer.workspace_lock.WorkspaceLockError(
            "WORKSPACE_LOCK_RELEASE_FAILED",
            "injected release failure",
        )
        with mock.patch.object(
            installer.workspace_lock.WorkspaceMutationLock,
            "release",
            side_effect=release_error,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = installer.main(argv)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(payload["status"], "lock-release-unverified")
        self.assertTrue(payload["execution"]["attempted"])
        self.assertTrue(payload["data"]["committed"])
        receipt = (
            self.workspace
            / ".opencode"
            / ".expert-installs"
            / f"{SLUG}.json"
        )
        self.assertTrue(receipt.is_file())


if __name__ == "__main__":
    unittest.main()
