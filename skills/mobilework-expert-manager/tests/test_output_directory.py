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
SCRIPT_PATH = SCRIPTS / "create_expert.py"

sys.path.insert(0, str(SCRIPTS))
from spec_templates import load_spec_text


def load_generator_module():
    spec = importlib.util.spec_from_file_location("mobilework_create_expert", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_generator_module()


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.cwd = self.root / "cwd"
        self.home.mkdir()
        self.cwd.mkdir()
        self.manifest = self.cwd / "expert.json"
        self.manifest.write_text(
            load_spec_text("legacy-expert-json"), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_generator(self, *extra_args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env.pop("MOBILEWORK_EXPERT_MANAGER_HOST", None)
        env.pop("MOBILEWORK_MY_EXPERTS_DIR", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--manifest", str(self.manifest), *extra_args],
            cwd=self.cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_external_default_output_uses_current_workspace(self) -> None:
        result = self.run_generator()

        expected = self.cwd / "contract-review-expert"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).resolve(), expected.resolve())
        self.assertTrue((expected / "expert.json").is_file())
        self.assertFalse((self.home / ".mobilework" / "my-experts").exists())

    def test_external_generation_does_not_modify_workspace_root_configs(self) -> None:
        opencode_dir = self.cwd / ".opencode"
        opencode_dir.mkdir()
        sentinels = {
            opencode_dir / "sentinel.txt": b"keep project agents and skills unchanged\n",
            self.cwd / "opencode.json": b'{"sentinel":"opencode"}\n',
            self.cwd / "mobilework.jsonc": b'{"sentinel":"mobilework"}\n',
        }
        for target, content in sentinels.items():
            target.write_bytes(content)

        result = self.run_generator()

        expected = self.cwd / "contract-review-expert"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((expected / "expert.json").is_file())
        for target, content in sentinels.items():
            self.assertEqual(target.read_bytes(), content)
        self.assertEqual(
            sorted(path.name for path in self.cwd.iterdir()),
            [".opencode", "contract-review-expert", "expert.json", "mobilework.jsonc", "opencode.json"],
        )

    def test_my_experts_flag_requires_mobilework_contract(self) -> None:
        result = self.run_generator("--my-experts")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOST_CONTRACT_INCOMPLETE", result.stderr)

    def test_explicit_output_dir_only_asserts_resolved_workspace(self) -> None:
        result = self.run_generator("--output-dir", str(self.cwd))

        expected = self.cwd / "contract-review-expert"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).resolve(), expected.resolve())

    def test_explicit_custom_output_is_rejected(self) -> None:
        custom = self.root / "custom-output"
        result = self.run_generator("--output-dir", str(custom))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OUTPUT_ROOT_MISMATCH", result.stderr)
        self.assertFalse(custom.exists())

    def test_mobilework_contract_uses_injected_real_user_root(self) -> None:
        my_experts = self.root / "real-user" / ".mobilework" / "my-experts"
        env = os.environ.copy()
        env["HOME"] = str(self.root / "virtual-home")
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env["MOBILEWORK_MY_EXPERTS_DIR"] = str(my_experts)

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--manifest", str(self.manifest), "--my-experts"],
            cwd=self.cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        expected = my_experts / "contract-review-expert"
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()).resolve(), expected.resolve())
        self.assertTrue((expected / "expert.json").is_file())
        self.assertFalse((Path(env["HOME"]) / ".mobilework" / "my-experts").exists())

    def test_incomplete_mobilework_contract_fails_closed(self) -> None:
        env = os.environ.copy()
        env["MOBILEWORK_EXPERT_MANAGER_HOST"] = "mobilework"
        env.pop("MOBILEWORK_MY_EXPERTS_DIR", None)

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--manifest", str(self.manifest)],
            cwd=self.cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOST_CONTRACT_INCOMPLETE", result.stderr)

    def test_existing_symlink_target_cannot_escape_workspace(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.cwd / "contract-review-expert").symlink_to(outside, target_is_directory=True)

        result = self.run_generator()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TARGET_OUTSIDE_ROOT", result.stderr)

    def test_main_rejects_generated_project_outside_expected_destination(self) -> None:
        wrong_project = self.root / "wrong" / "contract-review-expert"
        argv = [str(SCRIPT_PATH), "--manifest", str(self.manifest)]

        with (
            patch.object(sys, "argv", argv),
            patch.object(GENERATOR.Path, "cwd", return_value=self.cwd),
            patch.object(GENERATOR, "write_project", return_value=wrong_project),
        ):
            self.assertEqual(GENERATOR.main(), 1)

    def test_main_rejects_missing_core_generated_files(self) -> None:
        project_dir = self.cwd / "contract-review-expert"
        project_dir.mkdir(parents=True)
        argv = [str(SCRIPT_PATH), "--manifest", str(self.manifest)]

        with (
            patch.object(sys, "argv", argv),
            patch.object(GENERATOR.Path, "cwd", return_value=self.cwd),
            patch.object(GENERATOR, "write_project", return_value=project_dir),
        ):
            self.assertEqual(GENERATOR.main(), 1)

    def test_generated_json_files_must_be_readable(self) -> None:
        project_dir = self.root / "generated" / "contract-review-expert"
        project_dir.mkdir(parents=True)
        (project_dir / "README.md").write_text("# Readme\n", encoding="utf-8")

        for invalid_file in ("expert.json", "opencode.json"):
            with self.subTest(invalid_file=invalid_file):
                (project_dir / "expert.json").write_text(
                    json.dumps({"slug": "contract-review-expert"}),
                    encoding="utf-8",
                )
                (project_dir / "opencode.json").write_text("{}", encoding="utf-8")
                (project_dir / invalid_file).write_text("{invalid", encoding="utf-8")

                with self.assertRaisesRegex(SystemExit, f"invalid generated JSON: {invalid_file}"):
                    GENERATOR.validate_generated_project(
                        project_dir,
                        project_dir.parent,
                        "contract-review-expert",
                    )

    def test_generated_manifest_slug_must_match_destination(self) -> None:
        project_dir = self.root / "generated" / "contract-review-expert"
        project_dir.mkdir(parents=True)
        (project_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
        (project_dir / "expert.json").write_text(
            json.dumps({"slug": "wrong-slug"}),
            encoding="utf-8",
        )
        (project_dir / "opencode.json").write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "generated expert.json slug mismatch"):
            GENERATOR.validate_generated_project(
                project_dir,
                project_dir.parent,
                "contract-review-expert",
            )


if __name__ == "__main__":
    unittest.main()
