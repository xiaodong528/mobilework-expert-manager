from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
sys.path.insert(0, str(SCRIPTS))

from spec_templates import load_spec_text


def load_generator_module():
    spec = importlib.util.spec_from_file_location("controlled_create_expert", CREATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator: {CREATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator_module()


class ControlledTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source = self.root / "source"
        self.home.mkdir()
        self.source.mkdir()
        self.manifest = self.source / "expert.json"
        self.manifest.write_text(load_spec_text("expert-json"), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_generator(self, *extra: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env["MOBILEWORK_MY_EXPERTS_DIR"] = str(self.home / ".mobilework" / "my-experts")
        return subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(self.manifest), *extra],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def controlled_run(
        self,
        manifest: Path,
        target: Path,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env["MOBILEWORK_MY_EXPERTS_DIR"] = str(target.parent)
        env[GENERATOR.CONTROLLED_TARGET_ENV] = str(target)
        return subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(target.parent),
                *extra,
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def generate_target(self) -> Path:
        result = self.run_generator()
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.home / ".mobilework" / "my-experts" / "contract-review-expert"

    def temporary_manifest(self, name: str, data: dict[str, object]) -> Path:
        directory = self.root / name
        directory.mkdir()
        path = directory / "expert.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_controlled_target_rejects_source_manifest_in_place(self) -> None:
        target = self.home / ".mobilework" / "my-experts" / "contract-review-expert"
        target.mkdir(parents=True)
        source_manifest = target / "expert.json"
        source_manifest.write_text(load_spec_text("expert-json"), encoding="utf-8")
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env["MOBILEWORK_MY_EXPERTS_DIR"] = str(target.parent)
        env[GENERATOR.CONTROLLED_TARGET_ENV] = str(target)
        result = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(source_manifest), "--force"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("temporary manifest outside the source package", result.stderr)

    def test_controlled_target_rejects_a_different_output_parent(self) -> None:
        target = self.home / ".mobilework" / "my-experts" / "contract-review-expert"
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env["MOBILEWORK_MY_EXPERTS_DIR"] = str(target.parent)
        env[GENERATOR.CONTROLLED_TARGET_ENV] = str(target)
        result = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(self.manifest),
                "--output-dir",
                str(self.root / "elsewhere"),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OUTPUT_ROOT_MISMATCH", result.stderr)

    def test_controlled_target_requires_and_checks_locked_revision(self) -> None:
        target = self.generate_target()
        data = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        manifest = self.temporary_manifest("revision", data)

        missing = self.controlled_run(manifest, target, "--force")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("requires --expected-revision", missing.stderr)

        stale = self.controlled_run(
            manifest,
            target,
            "--force",
            "--expected-revision",
            "0" * 64,
        )
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("revision conflict", stale.stderr)

        revision = GENERATOR.calculate_package_revision(target)
        updated = self.controlled_run(
            manifest,
            target,
            "--force",
            "--expected-revision",
            revision,
        )
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(Path(updated.stdout.strip()).resolve(), target.resolve())

    def test_controlled_target_rejects_primary_agent_identity_change(self) -> None:
        target = self.generate_target()
        data = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        data["agent"]["id"] = "replacement-agent"
        manifest = self.temporary_manifest("identity", data)
        result = self.controlled_run(
            manifest,
            target,
            "--force",
            "--expected-revision",
            GENERATOR.calculate_package_revision(target),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot change primary Agent ID", result.stderr)
        saved = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["agent"]["id"], "contract-reviewer")

    def test_controlled_workflow_change_persists_into_regenerated_skill(self) -> None:
        target = self.generate_target()
        data = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        new_step = "强制循环补全全部未匹配的报账单信息。"
        data["agent"]["workflow"].append(new_step)
        manifest = self.temporary_manifest("workflow", data)
        result = self.controlled_run(
            manifest,
            target,
            "--force",
            "--expected-revision",
            GENERATOR.calculate_package_revision(target),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        self.assertIn(new_step, saved["agent"]["workflow"])
        role_skill = (
            target
            / ".opencode/skills/contract-review-expert-contract-reviewer-role-guidelines/SKILL.md"
        )
        self.assertIn(new_step, role_skill.read_text(encoding="utf-8"))

    def test_controlled_target_rejects_expert_team_type_change(self) -> None:
        target = self.generate_target()
        data = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        data["type"] = "team"
        data["primary_agent"] = data.pop("agent")
        member = dict(data["primary_agent"])
        member["id"] = "contract-researcher"
        member["mode"] = "subagent"
        member["name"] = "合同研究员"
        data["subagents"] = [member]
        manifest = self.temporary_manifest("type-change", data)
        result = self.controlled_run(
            manifest,
            target,
            "--force",
            "--expected-revision",
            GENERATOR.calculate_package_revision(target),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot change expert type", result.stderr)
        saved = json.loads((target / "expert.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["type"], "expert")

    def test_revision_ignores_only_shared_cache_rules(self) -> None:
        package = self.root / "revision-package"
        package.mkdir()
        (package / "expert.json").write_text("{}", encoding="utf-8")
        before = GENERATOR.calculate_package_revision(package)
        (package / ".DS_Store").write_text("cache", encoding="utf-8")
        (package / "ignored.pyc").write_text("cache", encoding="utf-8")
        pycache = package / "__pycache__"
        pycache.mkdir()
        (pycache / "module.pyc").write_text("cache", encoding="utf-8")
        self.assertEqual(GENERATOR.calculate_package_revision(package), before)
        (package / "README.md").write_text("material", encoding="utf-8")
        self.assertNotEqual(GENERATOR.calculate_package_revision(package), before)

    def test_live_readback_failure_restores_previous_package(self) -> None:
        output_root = self.root / "output"
        output_root.mkdir()
        project_dir = output_root / "contract-review-expert"
        project_dir.mkdir()
        marker = project_dir / "previous.txt"
        marker.write_text("previous", encoding="utf-8")
        raw = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest = GENERATOR.normalize_manifest(raw, manifest_dir=self.manifest.parent)
        GENERATOR.prepare_avatar_assets(manifest, self.manifest.parent)
        original_validate = GENERATOR.validate_generated_project

        def fail_live_readback(target: Path, output: Path, slug: str) -> None:
            if target == project_dir:
                raise RuntimeError("simulated live readback failure")
            original_validate(target, output, slug)

        with (
            patch.object(GENERATOR, "validate_generated_project", side_effect=fail_live_readback),
            self.assertRaisesRegex(RuntimeError, "simulated live readback failure"),
        ):
            GENERATOR.write_project(manifest, output_root, force=True)
        self.assertEqual(marker.read_text(encoding="utf-8"), "previous")
        self.assertFalse(GENERATOR.package_lock_path(output_root, "contract-review-expert").exists())

    def test_backup_cleanup_failure_keeps_validated_new_package(self) -> None:
        output_root = self.root / "cleanup-output"
        output_root.mkdir()
        project_dir = output_root / "contract-review-expert"
        project_dir.mkdir()
        (project_dir / "previous.txt").write_text("previous", encoding="utf-8")
        raw = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest = GENERATOR.normalize_manifest(raw, manifest_dir=self.manifest.parent)
        GENERATOR.prepare_avatar_assets(manifest, self.manifest.parent)
        original_rmtree = GENERATOR.shutil.rmtree

        def fail_backup_cleanup(target: Path, *args: object, **kwargs: object) -> None:
            if Path(target).name.startswith(".contract-review-expert.backup-"):
                raise OSError("simulated backup cleanup failure")
            original_rmtree(target, *args, **kwargs)

        with patch.object(GENERATOR.shutil, "rmtree", side_effect=fail_backup_cleanup):
            result = GENERATOR.write_project(manifest, output_root, force=True)
        self.assertEqual(result, project_dir)
        self.assertTrue((project_dir / "expert.json").is_file())
        self.assertFalse((project_dir / "previous.txt").exists())
        self.assertEqual(len(list(output_root.glob(".contract-review-expert.backup-*"))), 1)


if __name__ == "__main__":
    unittest.main()
