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

import bundle_contract
import config_loader
import create_bundle_manifest
import expert_vcs
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
        manifest = {"schemaVersion": 1, "packages": []}
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

        with patch.object(
            bundle_contract,
            "create_manifest",
            side_effect=bundle_contract.BundleContractError("duplicate"),
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
        self.assertEqual(json.loads(output)["code"], "BUNDLE_CREATE_ERROR")

    def test_migration_cli_all_formats_and_input_error(self) -> None:
        plan = {
            "source": "/tmp/legacy",
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
        }
        for output_format in ("json", "markdown", "human"):
            with self.subTest(output_format=output_format), patch.object(
                migration_planner, "plan", return_value=plan
            ):
                code, output = self.run_main(
                    plan_legacy_migration,
                    ["plan_legacy_migration.py", "/tmp/legacy", "--format", output_format],
                )
                self.assertEqual(code, 0)
                self.assertTrue(output.strip())

        with patch.object(
            migration_planner,
            "plan",
            side_effect=migration_planner.MigrationPlanError("bad input"),
        ):
            code, output = self.run_main(
                plan_legacy_migration,
                ["plan_legacy_migration.py", "/tmp/legacy"],
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["code"], "MIGRATION_PLAN_INPUT_ERROR")

    def test_validate_bundle_cli_success_failure_and_input_error(self) -> None:
        for ok, expected in ((True, 0), (False, 1)):
            with self.subTest(ok=ok), patch.object(
                bundle_contract,
                "validate_bundle",
                return_value={"ok": ok, "schemaVersion": 1, "findings": []},
            ):
                code, output = self.run_main(
                    validate_expert_bundle,
                    ["validate_expert_bundle.py", "/tmp/bundle"],
                )
                self.assertEqual(code, expected)
                self.assertEqual(json.loads(output)["ok"], ok)

        with patch.object(
            bundle_contract,
            "validate_bundle",
            side_effect=bundle_contract.BundleContractError("missing manifest"),
        ):
            code, output = self.run_main(
                validate_expert_bundle,
                ["validate_expert_bundle.py", "/tmp/bundle"],
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["code"], "BUNDLE_INPUT_ERROR")

    def test_verify_trusted_config_cli_success_and_error(self) -> None:
        payload = {"ok": True, "evidenceLevel": "config-loadable"}
        with patch.object(config_loader, "verify", return_value=payload), patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            return_value=SimpleNamespace(),
        ):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                    "--target-opencode-version", "1.2.3",
                ],
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["evidenceLevel"], "config-loadable")

        with patch.object(
            verify_trusted_config.manager_contract,
            "resolve_target",
            side_effect=verify_trusted_config.manager_contract.ManagerContractError("bad target"),
        ):
            code, output = self.run_main(
                verify_trusted_config,
                [
                    "verify_trusted_config.py",
                    "--workspace", "/tmp/workspace",
                    "--sidecar", "/tmp/opencode",
                ],
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["code"], "CONFIG_LOAD_CONTRACT_ERROR")

    def test_version_expert_proposal_release_and_error(self) -> None:
        proposal = SimpleNamespace(version="1.2.3", as_dict=lambda: {"version": "1.2.3"})
        with patch.object(expert_vcs, "propose_version", return_value=proposal):
            code, output = self.run_main(
                version_expert,
                ["version_expert.py", "--package-dir", "/tmp/expert"],
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["proposal"]["version"], "1.2.3")

        with patch.object(expert_vcs, "propose_version", return_value=proposal), patch.object(
            expert_vcs, "release", return_value={"ok": True, "tag": "v1.2.4"}
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
        self.assertEqual(json.loads(output)["tag"], "v1.2.4")
        release.assert_called_once()

        with patch.object(
            expert_vcs,
            "propose_version",
            side_effect=expert_vcs.ExpertVcsError("not a trusted source"),
        ):
            code, output = self.run_main(
                version_expert,
                ["version_expert.py", "--package-dir", "/tmp/expert"],
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["code"], "EXPERT_VCS_ERROR")


if __name__ == "__main__":
    unittest.main()
