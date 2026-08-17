from __future__ import annotations

import hashlib
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
MANAGER_CONTRACT = json.loads(
    (SCRIPTS / "manager-contract.json").read_text(encoding="utf-8")
)
SKILLS_REF_COMMIT = MANAGER_CONTRACT["agentSkillsSpecification"][
    "repositorySnapshot"
]["commit"]
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

    def run_official_validation(
        self, skill_root: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SKILLS_REF), "validate", str(skill_root)],
            text=True,
            capture_output=True,
            check=False,
        )

    def validate_official(self, skill_root: Path, *, expected_ok: bool) -> None:
        completed = self.run_official_validation(skill_root)
        self.assertEqual(
            completed.returncode == 0,
            expected_ok,
            completed.stdout + completed.stderr,
        )

    def generate_after_official_gate(
        self,
        *,
        skill_root: Path,
        manifest: Path,
        output: Path,
    ) -> tuple[
        subprocess.CompletedProcess[str],
        subprocess.CompletedProcess[str] | None,
    ]:
        official = self.run_official_validation(skill_root)
        if official.returncode != 0:
            return official, None
        generated = subprocess.run(
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
        return official, generated

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

    def test_unified_managed_skill_passes_official_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "output"
            skill_root = source / ".opencode/skills/clause-extraction"
            skill_root.mkdir(parents=True)
            output.mkdir()
            skill_bytes = (
                b"---\n"
                b"name: clause-extraction\n"
                b"description: Extract clauses with a reusable checklist. Use "
                b"when structured clause extraction is required.\n"
                b"---\n\n"
                b"# Clause extraction\n\n"
                b"Apply the confirmed checklist and cite source evidence.\n"
            )
            staged_skill = skill_root / "SKILL.md"
            staged_skill.write_bytes(skill_bytes)
            resource_path = ".opencode/skills/clause-extraction/SKILL.md"
            manifest = source / "expert.json"
            manifest.write_text(
                json.dumps(
                    {
                        "slug": "managed-clause-team",
                        "type": "team",
                        "name": "条款提取专家团",
                        "description": "验证统一 schema 的 managed Skill。",
                        "skills": [
                            {
                                "name": "clause-extraction",
                                "origin": "managed",
                                "edit_policy": "managed",
                            }
                        ],
                        "primary_agent": {
                            "id": "review-lead",
                            "name": "审查团长",
                            "mode": "all",
                            "autonomy": "bounded",
                            "description": "整合条款提取结果。",
                            "skills": ["clause-extraction"],
                        },
                        "subagents": [
                            {
                                "id": "clause-reviewer",
                                "name": "条款审查员",
                                "mode": "subagent",
                                "autonomy": "bounded",
                                "description": "按清单提取条款。",
                                "skills": ["clause-extraction"],
                            }
                        ],
                        "package_resources": [
                            {
                                "path": resource_path,
                                "kind": "text",
                                "sha256": hashlib.sha256(skill_bytes).hexdigest(),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            official, completed = self.generate_after_official_gate(
                skill_root=skill_root,
                manifest=manifest,
                output=output,
            )
            self.assertEqual(
                official.returncode,
                0,
                official.stdout + official.stderr,
            )
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed.returncode, 0, completed.stderr)
            generated_root = (
                output
                / "managed-clause-team/.opencode/skills/clause-extraction"
            )
            self.assertEqual((generated_root / "SKILL.md").read_bytes(), skill_bytes)
            self.assertFalse(
                any(
                    path.name.startswith("managed-clause-team-")
                    for path in generated_root.parent.iterdir()
                    if path.is_dir()
                )
            )
            self.validate_official(generated_root, expected_ok=True)

    def test_invalid_managed_skill_staging_blocks_before_generator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            output = root / "output"
            skill_root = source / ".opencode/skills/clause-extraction"
            skill_root.mkdir(parents=True)
            output.mkdir()
            marker = output / "existing.txt"
            marker.write_bytes(b"unchanged\n")
            invalid_skill_bytes = (
                b"---\n"
                b"name: clause-extraction\n"
                b"description: Invalid managed staging fixture.\n"
                b"mobilework-extra: forbidden\n"
                b"---\n\n# Invalid\n"
            )
            (skill_root / "SKILL.md").write_bytes(invalid_skill_bytes)
            manifest = source / "expert.json"
            manifest.write_text(
                json.dumps(
                    {
                        "slug": "blocked-managed-clause",
                        "type": "expert",
                        "name": "阻断的条款专家",
                        "description": "无效 managed Skill 必须在 generator 前阻断。",
                        "skills": [
                            {
                                "name": "clause-extraction",
                                "origin": "managed",
                                "edit_policy": "managed",
                            }
                        ],
                        "agent": {
                            "id": "clause-reviewer",
                            "name": "条款审查员",
                            "mode": "all",
                            "autonomy": "bounded",
                            "description": "按清单提取条款。",
                            "skills": ["clause-extraction"],
                        },
                        "package_resources": [
                            {
                                "path": (
                                    ".opencode/skills/clause-extraction/SKILL.md"
                                ),
                                "kind": "text",
                                "sha256": hashlib.sha256(
                                    invalid_skill_bytes
                                ).hexdigest(),
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            before = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            official, generated = self.generate_after_official_gate(
                skill_root=skill_root,
                manifest=manifest,
                output=output,
            )
            self.assertNotEqual(
                official.returncode,
                0,
                official.stdout + official.stderr,
            )
            self.assertIsNone(generated)
            after = {
                path.relative_to(output).as_posix(): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertFalse((output / "blocked-managed-clause").exists())

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
