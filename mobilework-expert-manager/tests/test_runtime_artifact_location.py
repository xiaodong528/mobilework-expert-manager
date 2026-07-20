from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = SKILL_ROOT / "scripts" / "scan_portable_artifacts.py"


def scanner_environment() -> dict[str, str]:
    return os.environ.copy()


class RuntimeArtifactLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_scanner(
        self,
        artifact: Path,
        *,
        workspace_root: Path | None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        command = [sys.executable, str(SCANNER_PATH)]
        if workspace_root is not None:
            command.extend(["--workspace-root", str(workspace_root)])
        command.append(str(artifact))
        result = subprocess.run(
            command,
            env=scanner_environment(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertTrue(result.stdout, result.stderr)
        payload = json.loads(result.stdout)
        return result, payload

    def assert_location_failure(
        self,
        artifact: Path,
        expected_type: str,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        result, payload = self.run_scanner(
            artifact,
            workspace_root=workspace_root or self.workspace,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(payload["ok"])
        findings = payload["findings"]
        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), 1)
        self.assertEqual(
            set(findings[0]),
            {"file", "location", "type", "match"},
        )
        self.assertEqual(findings[0]["type"], expected_type)

    def test_business_delivery_directory_inside_workspace_passes(self) -> None:
        artifact = self.workspace / "校验输出" / "asset-validation-run"
        artifact.mkdir(parents=True)
        (artifact / "summary.json").write_text('{"ok": true}\n', encoding="utf-8")

        result, payload = self.run_scanner(
            artifact,
            workspace_root=self.workspace,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload, {"ok": True, "findings": []})

    def test_workspace_root_itself_is_allowed(self) -> None:
        (self.workspace / "summary.json").write_text('{"ok": true}\n', encoding="utf-8")

        result, payload = self.run_scanner(
            self.workspace,
            workspace_root=self.workspace,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload, {"ok": True, "findings": []})

    def test_opencode_runtime_artifact_directory_fails(self) -> None:
        artifact = self.workspace / ".opencode" / "workspace" / "run"
        artifact.mkdir(parents=True)

        self.assert_location_failure(
            artifact,
            "runtime artifact under engine directory",
        )

    def test_mobilework_engine_runtime_artifact_directory_fails(self) -> None:
        artifact = self.workspace / ".mobilework-engine" / "output" / "run"
        artifact.mkdir(parents=True)

        self.assert_location_failure(
            artifact,
            "runtime artifact under engine directory",
        )

    def test_engine_directory_check_is_case_insensitive(self) -> None:
        artifact = self.workspace / ".OpenCode" / "workspace" / "run"
        artifact.mkdir(parents=True)

        self.assert_location_failure(
            artifact,
            "runtime artifact under engine directory",
        )

    def test_directory_outside_workspace_fails(self) -> None:
        artifact = self.root / "outside" / "run"
        artifact.mkdir(parents=True)

        self.assert_location_failure(artifact, "artifact outside workspace")

    def test_symlink_resolving_outside_workspace_fails(self) -> None:
        outside = self.root / "outside" / "run"
        outside.mkdir(parents=True)
        artifact = self.workspace / "校验输出" / "linked-run"
        artifact.parent.mkdir(parents=True)
        artifact.symlink_to(outside, target_is_directory=True)

        self.assert_location_failure(artifact, "artifact outside workspace")

    def test_opencode_symlink_to_safe_workspace_directory_still_fails(self) -> None:
        safe_root = self.workspace / "safe-opencode-target"
        artifact_target = safe_root / "workspace" / "run"
        artifact_target.mkdir(parents=True)
        engine_link = self.workspace / ".opencode"
        engine_link.symlink_to(safe_root, target_is_directory=True)

        self.assert_location_failure(
            engine_link / "workspace" / "run",
            "runtime artifact under engine directory",
        )

    def test_mobilework_engine_symlink_to_safe_workspace_directory_still_fails(self) -> None:
        safe_root = self.workspace / "safe-mobilework-target"
        artifact_target = safe_root / "output" / "run"
        artifact_target.mkdir(parents=True)
        engine_link = self.workspace / ".mobilework-engine"
        engine_link.symlink_to(safe_root, target_is_directory=True)

        self.assert_location_failure(
            engine_link / "output" / "run",
            "runtime artifact under engine directory",
        )

    def test_dotdot_lexical_path_to_engine_symlink_still_fails(self) -> None:
        (self.root / "other").mkdir()
        safe_root = self.workspace / "safe-dotdot-target"
        (safe_root / "run").mkdir(parents=True)
        engine_link = self.workspace / ".OpEnCoDe"
        engine_link.symlink_to(safe_root, target_is_directory=True)
        artifact = self.root / "other" / ".." / "workspace" / ".OpEnCoDe" / "run"

        self.assert_location_failure(
            artifact,
            "runtime artifact under engine directory",
        )

    def test_real_artifact_path_with_engine_alias_fails_for_symlinked_workspace_root(self) -> None:
        workspace_alias = self.root / "workspace-alias"
        workspace_alias.symlink_to(self.workspace, target_is_directory=True)
        safe_root = self.workspace / "safe-workspace-alias-target"
        (safe_root / "run").mkdir(parents=True)
        engine_link = self.workspace / ".MoBiLeWoRk-EnGiNe"
        engine_link.symlink_to(safe_root, target_is_directory=True)
        artifact = self.workspace / ".MoBiLeWoRk-EnGiNe" / "run"

        self.assert_location_failure(
            artifact,
            "runtime artifact under engine directory",
            workspace_root=workspace_alias,
        )

    def test_package_opencode_resource_still_passes_without_workspace_root(self) -> None:
        package_resource = self.root / "package" / ".opencode" / "skills" / "demo" / "SKILL.md"
        package_resource.parent.mkdir(parents=True)
        package_resource.write_text("# Demo\n", encoding="utf-8")

        result, payload = self.run_scanner(
            package_resource,
            workspace_root=None,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload, {"ok": True, "findings": []})


if __name__ == "__main__":
    unittest.main()
