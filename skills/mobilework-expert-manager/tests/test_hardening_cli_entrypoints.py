from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import bundle_contract
import config_loader
import create_bundle_manifest
import expert_vcs
import install_expert
import migration_planner
import plan_legacy_migration
import validate_expert_bundle
import verify_trusted_config
import version_expert


class HardeningCliEntrypointTests(unittest.TestCase):
    def run_main(self, module, argv: list[str]) -> tuple[int, str]:
        with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()) as output:
            code = module.main()
        return code, output.getvalue()

    def test_create_bundle_manifest_success_and_contract_error(self) -> None:
        manifest = {
            "schemaVersion": 1,
            "packages": [],
            "sourceRepository": (
                "https://bundle-user:bundle-password@example.invalid/repo"
                "?token=bundle-query-canary"
            ),
        }
        with patch.object(bundle_contract, "create_manifest", return_value=manifest):
            code, output = self.run_main(
                create_bundle_manifest,
                [
                    "create_bundle_manifest.py",
                    "--bundle-dir", "/tmp/bundle",
                    "--package-zip", "/tmp/expert.zip",
                    "--tests-collected", "1",
                    "--tests-passed", "1",
                    "--source-repository", "https://example.invalid/repo",
                    "--source-commit", "abc123",
                ],
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["ok"])
        self.assertNotIn("bundle-user", output)
        self.assertNotIn("bundle-password", output)
        self.assertNotIn("bundle-query-canary", output)

        with patch.object(
            bundle_contract,
            "create_manifest",
            side_effect=bundle_contract.BundleContractError(
                "duplicate Authorization: Bearer bundle-error-canary"
            ),
        ):
            code, output = self.run_main(
                create_bundle_manifest,
                [
                    "create_bundle_manifest.py",
                    "--bundle-dir", "/tmp/bundle",
                    "--package-zip", "/tmp/expert.zip",
                ],
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(output)["data"]["code"], "BUNDLE_CREATE_ERROR"
        )
        self.assertNotIn("bundle-error-canary", output)

    def test_migration_cli_all_formats_and_input_error(self) -> None:
        plan = {
            "source": (
                "https://migration-user:migration-password@example.invalid/source"
                "?token=migration-query-canary#migration-fragment-canary"
            ),
            "mode": "read-only",
            "slug": "legacy",
            "automaticActions": [],
            "jsonPatchCandidates": [],
            "resourceMoves": [],
            "permissionChanges": [],
            "sourceWarnings": [],
            "businessDecisions": [],
            "unconfirmedCount": 0,
            "regenerate": [],
            "diagnostic": "password=migration-plan-canary",
        }
        for output_format in ("json", "human"):
            with self.subTest(output_format=output_format), patch.object(
                migration_planner, "plan", return_value=plan
            ):
                code, output = self.run_main(
                    plan_legacy_migration,
                    ["plan_legacy_migration.py", "/tmp/legacy", "--format", output_format],
                )
                self.assertEqual(code, 0)
                self.assertTrue(output.strip())
                self.assertNotIn("migration-plan-canary", output)
                self.assertNotIn("migration-user", output)
                self.assertNotIn("migration-password", output)
                self.assertNotIn("migration-query-canary", output)
                self.assertNotIn("migration-fragment-canary", output)

        code, output = self.run_main(
            plan_legacy_migration,
            ["plan_legacy_migration.py", "/tmp/legacy", "--format", "markdown"],
        )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["schemaVersion"], 2)

        with patch.object(
            migration_planner,
            "plan",
            side_effect=migration_planner.MigrationPlanError(
                "bad input Cookie: session=migration-error-canary"
            ),
        ):
            code, output = self.run_main(
                plan_legacy_migration,
                [
                    "plan_legacy_migration.py",
                    "/tmp/legacy",
                    "--format",
                    "json",
                ],
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(output)["data"]["code"], "MIGRATION_PLAN_INPUT_ERROR"
        )
        self.assertNotIn("migration-error-canary", output)

    def test_validate_bundle_cli_success_failure_and_input_error(self) -> None:
        for ok, expected in ((True, 0), (False, 1)):
            with self.subTest(ok=ok), patch.object(
                bundle_contract,
                "validate_bundle",
                return_value={
                    "ok": ok,
                    "schemaVersion": 1,
                    "findings": [{"evidence": "api_key=bundle-validation-canary"}],
                },
            ):
                code, output = self.run_main(
                    validate_expert_bundle,
                    ["validate_expert_bundle.py", "/tmp/bundle"],
                )
                self.assertEqual(code, expected)
                self.assertEqual(json.loads(output)["ok"], ok)
                self.assertNotIn("bundle-validation-canary", output)

        with patch.object(
            bundle_contract,
            "validate_bundle",
            side_effect=bundle_contract.BundleContractError(
                "missing manifest token=bundle-input-canary"
            ),
        ):
            code, output = self.run_main(
                validate_expert_bundle,
                ["validate_expert_bundle.py", "/tmp/bundle"],
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["data"]["code"], "BUNDLE_INPUT_ERROR")
        self.assertNotIn("bundle-input-canary", output)

    def test_verify_trusted_config_cli_success_and_error(self) -> None:
        payload = {
            "ok": True,
            "schemaVersion": 2,
            "status": "config-loadable",
            "evidenceLevel": "config-loadable",
            "gates": {
                "archive": "not-run",
                "contract": "passed",
                "portability": "passed",
                "install": "passed",
                "configLoad": "passed",
            },
            "runtime": {"status": "not-tested", "reason": "pure-config-only"},
            "execution": {"attempted": True},
            "provenance": {},
            "findings": [],
            "debug": {"apiKey": "config-success-canary"},
        }
        with patch.object(config_loader, "verify", return_value=payload), patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            return_value=SimpleNamespace(),
        ):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--package-dir", "/tmp/package",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                    "--target-opencode-version", "1.2.3",
                ],
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["evidenceLevel"], "config-loadable")
        self.assertNotIn("config-success-canary", output)

        with patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            side_effect=verify_trusted_config.manager_contract.ManagerContractError(
                "bad target password=config-error-canary"
            ),
        ):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--package-dir", "/tmp/package",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                    "--target-opencode-version", "1.2.3",
                ],
            )
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(output)["findings"][0]["code"],
            "MANAGER_VERSION_CONTRACT_ERROR",
        )
        self.assertNotIn("config-error-canary", output)

    def test_verify_trusted_config_preserves_attempted_failure_evidence(self) -> None:
        finding = {
            "code": "CONFIG_EVIDENCE_STATE_CHANGED",
            "severity": "error",
            "phase": "config-evidence",
            "path": ".opencode",
            "location": "config-evidence",
            "message": "state changed",
            "rootCause": "untrusted-config-evidence",
            "remediation": "retry",
            "evidence": "",
        }
        error = config_loader.ConfigEvidenceError(
            "CONFIG_EVIDENCE_CHAIN_INVALID",
            "invalid",
            [finding],
            attempted=True,
            stage="post-sidecar-recheck",
            provenance={"sidecarSha256": "a" * 64, "receipt": {"contract": 3}},
        )
        with patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            return_value=SimpleNamespace(),
        ), patch.object(config_loader, "verify", side_effect=error):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--package-dir", "/tmp/package",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                    "--target-opencode-version", "1.2.3",
                ],
            )
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertTrue(payload["execution"]["attempted"])
        self.assertEqual(payload["execution"]["reason"], "post-sidecar-recheck")
        self.assertEqual(payload["gates"]["configLoad"], "failed")
        self.assertEqual(payload["provenance"]["receipt"]["contract"], 3)

        load_error = config_loader.ConfigLoadError(
            "version conflict",
            attempted=True,
            stage="sidecar-version",
            provenance={"receipt": {"contract": 3}},
        )
        with patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            return_value=SimpleNamespace(),
        ), patch.object(config_loader, "verify", side_effect=load_error):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--package-dir", "/tmp/package",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                    "--target-opencode-version", "1.2.3",
                ],
            )
        payload = json.loads(output)
        self.assertEqual(code, 2)
        self.assertTrue(payload["execution"]["attempted"])
        self.assertEqual(payload["gates"]["contract"], "passed")
        self.assertEqual(payload["gates"]["configLoad"], "failed")

    def test_verify_trusted_config_parse_and_policy_errors_use_requested_sink(self) -> None:
        code, output = self.run_main(
            verify_trusted_config,
            [
                "verify_trusted_config.py",
                "--format", "human",
                "--schema-version", "1",
            ],
        )
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("verify-trusted-config: argument-error"))
        self.assertNotIn('"schemaVersion"', output)

        with patch.object(
            verify_trusted_config.manager_contract,
            "load_policy",
            side_effect=verify_trusted_config.manager_contract.ManagerContractError(
                "policy password=policy-error-canary"
            ),
        ):
            code, output = self.run_main(
                verify_trusted_config,
                ["verify_trusted_config.py", "--format", "json"],
            )
        payload = json.loads(output)
        self.assertEqual(code, 3)
        self.assertEqual(payload["findings"][0]["code"], "MANAGER_POLICY_INVALID")
        self.assertNotIn("policy-error-canary", output)

    def test_install_policy_error_is_structured_before_execution(self) -> None:
        with patch.object(
            install_expert.manager_contract,
            "load_policy",
            side_effect=install_expert.manager_contract.ManagerContractError(
                "policy token=install-policy-canary"
            ),
        ), patch.object(
            install_expert,
            "install_package",
            side_effect=AssertionError("install must not run"),
        ):
            code, output = self.run_main(
                install_expert,
                [
                    "install_expert.py",
                    "--package-dir", "/tmp/package",
                    "--workspace-dir", "/tmp/workspace",
                ],
            )
        payload = json.loads(output)
        self.assertEqual(code, 3)
        self.assertEqual(payload["findings"][0]["code"], "MANAGER_POLICY_INVALID")
        self.assertNotIn("install-policy-canary", output)

    def test_policy_damage_on_cold_start_uses_sanitized_cli_emitter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied_scripts = Path(temp) / "password=cold-policy-canary" / "scripts"
            shutil.copytree(SCRIPTS, copied_scripts)
            (copied_scripts / "manager-contract.json").unlink()
            for script in ("install_expert.py", "verify_trusted_config.py"):
                with self.subTest(script=script):
                    completed = subprocess.run(
                        [sys.executable, str(copied_scripts / script)],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    payload = json.loads(completed.stdout)
                    self.assertEqual(completed.returncode, 3)
                    self.assertEqual(
                        payload["findings"][0]["code"],
                        "MANAGER_POLICY_INVALID",
                    )
                    combined = completed.stdout + completed.stderr
                    self.assertNotIn("cold-policy-canary", combined)
                    self.assertNotIn("Traceback", combined)

    def test_migrated_help_uses_requested_single_sink(self) -> None:
        for module, operation in (
            (install_expert, "install-expert"),
            (verify_trusted_config, "verify-trusted-config"),
        ):
            with self.subTest(module=module.__name__, output_format="json"):
                code, output = self.run_main(
                    module,
                    [module.__name__, "--help", "--format", "json"],
                )
                payload = json.loads(output)
                self.assertEqual(code, 0)
                self.assertEqual(payload["operation"], operation)
                self.assertEqual(payload["status"], "help")
                self.assertIn("usage:", payload["data"]["help"])
            with self.subTest(module=module.__name__, output_format="human"):
                code, output = self.run_main(
                    module,
                    [module.__name__, "--help", "--format", "human"],
                )
                self.assertEqual(code, 0)
                self.assertTrue(output.startswith("usage:"))

    def test_version_expert_proposal_release_and_error(self) -> None:
        proposal = SimpleNamespace(
            version="1.2.3",
            as_dict=lambda: {
                "version": "1.2.3",
                "diagnostic": "secret=proposal-canary",
            },
        )
        with patch.object(expert_vcs, "propose_version", return_value=proposal):
            code, output = self.run_main(
                version_expert,
                ["version_expert.py", "--package-dir", "/tmp/expert"],
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output)["data"]["proposal"]["version"], "1.2.3"
        )
        self.assertNotIn("proposal-canary", output)

        with patch.object(expert_vcs, "propose_version", return_value=proposal), patch.object(
            expert_vcs,
            "release",
            return_value={
                "ok": True,
                "tag": "v1.2.4",
                "diagnostic": "token=release-canary",
            },
        ) as release:
            code, output = self.run_main(
                version_expert,
                [
                    "version_expert.py",
                    "--package-dir", "/tmp/expert",
                    "--version", "1.2.4",
                    "--confirm",
                ],
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["data"]["tag"], "v1.2.4")
        self.assertNotIn("release-canary", output)
        release.assert_called_once()

        with patch.object(
            expert_vcs,
            "propose_version",
            side_effect=expert_vcs.ExpertVcsError(
                "not a trusted source api_key=version-error-canary"
            ),
        ):
            code, output = self.run_main(
                version_expert,
                ["version_expert.py", "--package-dir", "/tmp/expert"],
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["data"]["code"], "EXPERT_VCS_ERROR")
        self.assertNotIn("version-error-canary", output)


if __name__ == "__main__":
    unittest.main()
