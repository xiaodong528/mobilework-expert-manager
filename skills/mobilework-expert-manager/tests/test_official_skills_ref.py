from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
SKILLS_REF_COMMIT = "38a2ff82958afee88dadf4831509e6f7e9d8ef4e"
SKILLS_REF = os.environ.get("MOBILEWORK_SKILLS_REF") or shutil.which("skills-ref")

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class OfficialSkillsReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SKILLS_REF:
            raise AssertionError(
                "official skills-ref is required; install commit "
                f"{SKILLS_REF_COMMIT} before running the contract suite"
            )
        try:
            direct_url_text = importlib.metadata.distribution("skills-ref").read_text(
                "direct_url.json"
            )
        except importlib.metadata.PackageNotFoundError as error:
            raise AssertionError(
                "skills-ref executable is present but its Python distribution "
                "is not installed in the test interpreter"
            ) from error
        if not direct_url_text:
            raise AssertionError(
                "skills-ref must be installed from the pinned Git commit, "
                "but direct_url.json is missing"
            )
        direct_url = json.loads(direct_url_text)
        actual_commit = direct_url.get("vcs_info", {}).get("commit_id")
        if actual_commit != SKILLS_REF_COMMIT:
            raise AssertionError(
                "skills-ref commit mismatch: expected "
                f"{SKILLS_REF_COMMIT}, got {actual_commit or 'unknown'}"
            )

    def validate_official(self, skill_root: Path, *, expected_ok: bool) -> None:
        completed = subprocess.run(
            [str(SKILLS_REF), "validate", str(skill_root)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode == 0,
            expected_ok,
            completed.stdout + completed.stderr,
        )

    def test_manager_and_generated_skills_pass_official_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager_root = root / "mobilework-expert-manager"
            shutil.copytree(
                SKILL_ROOT,
                manager_root,
                ignore=shutil.ignore_patterns(
                    ".coverage",
                    ".serena",
                    "__pycache__",
                    "*.pyc",
                ),
            )
            self.validate_official(manager_root, expected_ok=True)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            manifest = source / "expert.json"
            manifest.write_text(
                load_spec_text("legacy-expert-json"),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CREATE),
                    "--manifest",
                    str(manifest),
                    "--output-dir",
                    str(output),
                ],
                env=managed_generator_env(output),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            skills_root = output / "contract-review-expert/.opencode/skills"
            generated = sorted(path for path in skills_root.iterdir() if path.is_dir())
            self.assertTrue(generated)
            for skill_root in generated:
                with self.subTest(skill=skill_root.name):
                    self.validate_official(skill_root, expected_ok=True)

    def test_official_reference_rejects_unsupported_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skill_root = Path(temp) / "unsupported-frontmatter"
            skill_root.mkdir()
            (skill_root / "SKILL.md").write_text(
                "---\n"
                + yaml.safe_dump(
                    {
                        "name": "unsupported-frontmatter",
                        "description": (
                            "Use when confirming the official reference validator."
                        ),
                        "mobilework-extra": True,
                    },
                    sort_keys=False,
                    allow_unicode=True,
                )
                + "---\n\n# Fixture\n",
                encoding="utf-8",
            )
            self.validate_official(skill_root, expected_ok=False)


if __name__ == "__main__":
    unittest.main()
