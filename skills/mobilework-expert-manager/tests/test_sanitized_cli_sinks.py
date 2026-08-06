from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_environment
import config_loader
import create_expert
import diagnose_skill
import expert_vcs
import execution_context
import import_reference
import import_skill
import install_expert
import manager_contract
import output_sanitizer
import package_expert
import scan_portable_artifacts


class SanitizedCliSinkTests(unittest.TestCase):
    def run_main(self, module, argv: list[str]) -> tuple[int, str, str]:
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(
            io.StringIO()
        ) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_import_skill_sanitizes_success_and_both_error_channels(self) -> None:
        argv = [
            "import_skill.py",
            "--package-dir", "/tmp/expert",
            "--skill", "/tmp/skill",
        ]
        with patch.object(
            import_skill,
            "import_skill",
            return_value={"status": "ok", "diagnostic": "token=skill-success-canary"},
        ):
            code, stdout, stderr = self.run_main(import_skill, argv)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["status"], "ok")
        self.assertNotIn("skill-success-canary", stdout)
        self.assertEqual(stderr, "")

        for error, expected, forbidden in (
            (
                import_skill.ImportSkillError(
                    "Authorization: Bearer skill-input-error-canary"
                ),
                2,
                "skill-input-error-canary",
            ),
            (
                RuntimeError("sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
                3,
                "AbCdEfGhIjKlMnOpQrStUvWxYz123456",
            ),
        ):
            with self.subTest(expected=expected), patch.object(
                import_skill, "import_skill", side_effect=error
            ):
                code, stdout, stderr = self.run_main(import_skill, argv)
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(stdout)["schemaVersion"], 2)
            self.assertNotIn(forbidden, stderr)
            self.assertNotIn(forbidden, stdout)

    def test_import_reference_sanitizes_success_and_both_error_channels(self) -> None:
        argv = [
            "import_reference.py",
            "--package-dir", "/tmp/expert",
            "--source", "/tmp/reference",
            "--alias", "reference",
            "--description", "fixture",
        ]
        with patch.object(
            import_reference,
            "import_reference",
            return_value={
                "status": "ok",
                "diagnostic": "Cookie: session=reference-success-canary",
            },
        ):
            code, stdout, stderr = self.run_main(import_reference, argv)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["status"], "ok")
        self.assertNotIn("reference-success-canary", stdout)
        self.assertEqual(stderr, "")

        for error, expected in (
            (
                import_reference.ImportReferenceError(
                    "password=reference-input-error-canary"
                ),
                2,
            ),
            (RuntimeError("ghp_abcdefghijklmnopqrstuvwxyz123456"), 3),
        ):
            with self.subTest(expected=expected), patch.object(
                import_reference, "import_reference", side_effect=error
            ):
                code, stdout, stderr = self.run_main(import_reference, argv)
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(stdout)["schemaVersion"], 2)
            self.assertNotIn("canary", stderr)
            self.assertNotIn("canary", stdout)
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", stderr)
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", stdout)

    def test_diagnose_skill_sanitizes_contract_and_internal_json(self) -> None:
        argv = ["diagnose_skill.py", "/tmp/skill", "--format", "json"]
        with patch.object(
            diagnose_skill.manager_contract,
            "resolve_target",
            side_effect=manager_contract.ManagerContractError(
                "api_key=diagnose-contract-canary"
            ),
        ):
            code, stdout, stderr = self.run_main(diagnose_skill, argv)
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(stdout)["findings"][0]["code"],
            "MANAGER_VERSION_CONTRACT_ERROR",
        )
        self.assertNotIn("diagnose-contract-canary", stdout)
        self.assertNotIn("diagnose-contract-canary", stderr)

        with patch.object(
            diagnose_skill.manager_contract,
            "resolve_target",
            return_value=SimpleNamespace(),
        ), patch.object(
            diagnose_skill,
            "diagnose",
            side_effect=RuntimeError("xoxb-1234567890-diagnose-internal-canary"),
        ):
            code, stdout, stderr = self.run_main(diagnose_skill, argv)
        self.assertEqual(code, 3)
        self.assertEqual(
            json.loads(stdout)["findings"][0]["code"],
            "MANAGER_INTERNAL_ERROR",
        )
        self.assertNotIn("diagnose-internal-canary", stdout)
        self.assertNotIn("diagnose-internal-canary", stderr)

    def test_environment_sanitizes_direct_errors_and_json_output(self) -> None:
        with patch.object(
            check_environment.execution_context,
            "resolve_execution_context",
            side_effect=execution_context.ExecutionContextError(
                "ROUTING_ERROR", "password=environment-routing-canary"
            ),
        ):
            result = check_environment.check_environment(["core"], env={})
        self.assertNotIn("environment-routing-canary", json.dumps(result))

        with patch.object(
            check_environment,
            "check_environment",
            return_value={
                "ok": True,
                "diagnostic": "https://user:password@example.invalid/#environment-fragment-canary",
            },
        ):
            code, stdout, stderr = self.run_main(check_environment, ["check_environment.py"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["ok"])
        self.assertNotIn("password", stdout)
        self.assertNotIn("environment-fragment-canary", stdout)
        self.assertEqual(stderr, "")

    def test_config_loader_sanitizes_sidecar_failures_and_result(self) -> None:
        failures = (
            SimpleNamespace(
                returncode=1,
                stderr="Authorization: Bearer sidecar-stderr-canary",
                stdout="",
            ),
            SimpleNamespace(
                returncode=1,
                stderr="",
                stdout="Cookie: session=sidecar-stdout-canary",
            ),
        )
        for failure in failures:
            with self.subTest(failure=failure), patch.object(
                config_loader.subprocess, "run", return_value=failure
            ):
                with self.assertRaises(config_loader.ConfigLoadError) as raised:
                    config_loader._run(
                        Path("/tmp/sidecar"),
                        ["debug", "config", "--pure"],
                        cwd=Path("/tmp"),
                        env={},
                    )
            self.assertNotIn("canary", str(raised.exception))

        with patch.object(
            config_loader.subprocess,
            "run",
            side_effect=OSError("token=sidecar-os-error-canary"),
        ):
            with self.assertRaises(config_loader.ConfigLoadError) as raised:
                config_loader._run(
                    Path("/tmp/sidecar"), ["--version"], cwd=Path("/tmp"), env={}
                )
        self.assertNotIn("sidecar-os-error-canary", str(raised.exception))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            runtime = workspace / ".opencode"
            runtime.mkdir(parents=True)
            (runtime / "opencode.jsonc").write_text("{}", encoding="utf-8")
            sidecar = root / "sidecar"
            sidecar.write_text("fixture", encoding="utf-8")
            sidecar.chmod(0o700)
            version = SimpleNamespace(
                returncode=0,
                stdout="1.2.3 sk-sidecar-success-canary-12345",
                stderr="Cookie: session=sidecar-success-stderr-canary",
            )
            loaded = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"agent": {}, "apiKey": "resolved-config-canary"}),
                stderr="",
            )
            target = manager_contract.TargetContract(
                version="1.2.3",
                source="test",
                capabilities={"apiKey": "target-capability-canary"},
                capability_verified=True,
            )
            with patch.object(config_loader, "_run", side_effect=[version, loaded]):
                evidence, _resolved = config_loader._verify_sidecar(
                    workspace, sidecar, target=target
                )
        rendered = output_sanitizer.json_dumps(evidence)
        for canary in (
            "sidecar-success-canary",
            "sidecar-success-stderr-canary",
            "resolved-config-canary",
            "target-capability-canary",
        ):
            self.assertNotIn(canary, rendered)

    def test_portable_scanner_sanitizes_final_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(
                scan_portable_artifacts,
                "scan_root",
                return_value=[
                    {
                        "file": "fixture.md",
                        "location": "line 1",
                        "type": "fixture",
                        "match": "sk-proj-ScannerSecretAbCdEfGhIjKlMnOpQrSt123456",
                        "severity": "error",
                    }
                ],
            ):
                code, stdout, stderr = self.run_main(
                    scan_portable_artifacts,
                    ["scan_portable_artifacts.py", str(root)],
                )
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(stdout)["ok"])
        self.assertNotIn("ScannerSecretAbCdEfGhIjKlMnOpQrSt123456", stdout)
        self.assertNotIn("ScannerSecretAbCdEfGhIjKlMnOpQrSt123456", stderr)

    def test_create_expert_sanitizes_fail_warnings_and_final_output(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            create_expert.fail("token=create-fail-canary")
        self.assertNotIn("create-fail-canary", str(raised.exception))

        remote_instruction = (
            "https://create-user:create-password@example.invalid/instructions"
            "?token=create-query-canary#create-fragment-canary"
        )
        with contextlib.redirect_stderr(io.StringIO()) as warning:
            self.assertEqual(
                create_expert.normalize_instructions(
                    [remote_instruction], "fixture", set()
                ),
                [remote_instruction],
            )
        for canary in (
            "create-user",
            "create-password",
            "create-query-canary",
            "create-fragment-canary",
        ):
            self.assertNotIn(canary, warning.getvalue())

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_root = root / "output"
            project = output_root / "sk-create-project-canary-12345"
            manifest_path = root / "expert.json"
            context = SimpleNamespace(output_root=output_root)
            with patch.dict(
                create_expert.os.environ,
                {create_expert.CONTROLLED_TARGET_ENV: ""},
                clear=False,
            ), patch.object(
                create_expert.shutil, "which", return_value="/usr/bin/git"
            ), patch.object(
                create_expert, "load_json", return_value={}
            ), patch.object(
                create_expert,
                "normalize_manifest",
                return_value={"slug": "fixture"},
            ), patch.object(
                create_expert, "prepare_avatar_assets"
            ), patch.object(
                create_expert, "normalized_output_dir", return_value=output_root
            ), patch.object(
                create_expert.execution_context,
                "resolve_execution_context",
                return_value=context,
            ), patch.object(
                create_expert.execution_context, "validate_package_target"
            ), patch.object(
                create_expert, "write_project", return_value=project
            ), patch.object(
                create_expert, "validate_generated_project"
            ), patch.object(
                expert_vcs,
                "initialize_repository",
                return_value={"token": "create-vcs-canary"},
            ):
                code, stdout, stderr = self.run_main(
                    create_expert,
                    ["create_expert.py", "--manifest", str(manifest_path)],
                )
        self.assertEqual(code, 0)
        self.assertIn("create-project-canary", stdout)
        self.assertNotIn("create-vcs-canary", stderr)
        self.assertIn("VERSION_PENDING", stderr)

    def test_package_expert_sanitizes_child_process_and_final_json(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            package_expert.fail("password=package-fail-canary")
        self.assertNotIn("package-fail-canary", str(raised.exception))

        failed_unzip = SimpleNamespace(
            returncode=1,
            stdout="Cookie: session=unzip-stdout-canary\n",
            stderr="ghp_abcdefghijklmnopqrstuvwxyz123456\n",
        )
        with patch.object(
            package_expert.subprocess, "run", return_value=failed_unzip
        ), contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(
            io.StringIO()
        ) as stderr, self.assertRaises(SystemExit) as raised:
            package_expert.test_zip_external(
                Path("/tmp/sk-package-path-canary-12345.zip")
            )
        combined = stdout.getvalue() + stderr.getvalue() + str(raised.exception)
        self.assertNotIn("unzip-stdout-canary", combined)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", combined)
        self.assertIn("package-path-canary", combined)

        validation = SimpleNamespace(ok=True, print_summary=lambda: print("summary"))
        with patch.object(
            package_expert.package_snapshot,
            "inspect_and_validate",
            return_value=(SimpleNamespace(), validation),
        ), patch.object(
            package_expert,
            "make_zip",
            return_value=Path("/tmp/sk-package-result-canary-12345.zip"),
        ):
            code, stdout, stderr = self.run_main(
                package_expert,
                [
                    "package_expert.py",
                    "--package-dir", "/tmp/expert",
                    "--output-dir", "/tmp/output",
                ],
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["ok"])
        self.assertIn("package-result-canary", stdout)
        self.assertEqual(stderr, "")

        with patch.object(
            package_expert.package_snapshot,
            "inspect_and_validate",
            return_value=(SimpleNamespace(), validation),
        ), patch.object(
            package_expert,
            "make_zip",
            side_effect=OSError(
                "/dev/null/password=package-traceback-canary is not a directory"
            ),
        ):
            code, stdout, stderr = self.run_main(
                package_expert,
                [
                    "package_expert.py",
                    "--package-dir", "/tmp/expert",
                    "--output-dir", "/dev/null/password=package-traceback-canary",
                ],
        )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout)["schemaVersion"], 2)
        self.assertNotIn("package-traceback-canary", stderr)
        self.assertNotIn("package-traceback-canary", stdout)
        self.assertNotIn("Traceback", stderr)

    def test_install_expert_sanitizes_fail_and_both_final_json_paths(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            install_expert.fail("api_key=install-fail-canary")
        self.assertNotIn("install-fail-canary", str(raised.exception))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            package = root / "expert"
            workspace.mkdir()
            package.mkdir()

            with patch.object(
                install_expert,
                "uninstall_package",
                return_value={
                    "ok": True,
                    "status": "uninstalled",
                    "diagnostic": "Cookie: session=uninstall-result-canary",
                },
            ):
                code, stdout, stderr = self.run_main(
                    install_expert,
                    [
                        "install_expert.py",
                        "--uninstall", "fixture",
                        "--workspace-dir", str(workspace),
                    ],
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["status"], "uninstalled")
            self.assertNotIn("uninstall-result-canary", stdout)
            self.assertEqual(stderr, "")

            with patch.object(
                install_expert.manager_contract,
                "resolve_target",
                return_value=SimpleNamespace(),
            ), patch.object(
                install_expert,
                "install_package",
                return_value={
                    "ok": True,
                    "status": "installed",
                    "diagnostic": "sk-proj-InstallSecretAbCdEfGhIjKlMnOpQrSt123456",
                },
            ):
                code, stdout, stderr = self.run_main(
                    install_expert,
                    [
                        "install_expert.py",
                        "--package-dir", str(package),
                        "--workspace-dir", str(workspace),
                    ],
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["status"], "installed")
            self.assertNotIn("InstallSecretAbCdEfGhIjKlMnOpQrSt123456", stdout)
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
