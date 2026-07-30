from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
INSTALL = SCRIPTS / "install_expert.py"
PACKAGE = SCRIPTS / "package_expert.py"
TEAM_EXAMPLE = SKILL_ROOT / "evals" / "files" / "software-dev-team.expert.json"

sys.path.insert(0, str(SCRIPTS))
import diagnose_skill
import import_skill
import package_contract
import skill_contract
import validate_expert
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text
from validation_result import ValidationResult


def package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or not path.is_file():
            continue
        name = relative.as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


class UnifiedSkillImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "experts"
        self.output.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_package(self, *, team: bool = False) -> Path:
        manifest = (
            self.root
            / ("team-source" if team else "expert-source")
            / "expert.json"
        )
        manifest.parent.mkdir()
        source = (
            TEAM_EXAMPLE.read_text(encoding="utf-8")
            if team
            else load_spec_text("legacy-expert-json")
        )
        manifest.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(self.output),
            ],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return self.output / ("software-dev-team" if team else "contract-review-expert")

    def create_skill(
        self,
        name: str = "uploaded-review",
        *,
        body: str = "Use the uploaded review workflow.",
        parent: str = "uploads",
    ) -> Path:
        root = self.root / parent / name
        (root / "references").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    f"name: {name}",
                    "description: Use when an uploaded review workflow is requested.",
                    "---",
                    "",
                    "# Uploaded review",
                    "",
                    body,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (root / "references" / "checklist.md").write_bytes(
            b"# Checklist\r\n\r\nKeep these exact CRLF bytes.\r\n"
        )
        (root / "scripts" / "check.py").write_text(
            "def check(value: str) -> bool:\n    return bool(value)\n",
            encoding="utf-8",
        )
        (root / "assets.bin").write_bytes(b"\x00\x01uploaded-skill\xff")
        return root

    def import_into(
        self,
        package: Path,
        skill: Path,
        *,
        assign_to: list[str] | None = None,
        all_members: bool = False,
        replace: bool = False,
        confirm_managed: bool = False,
    ) -> dict[str, object]:
        return import_skill.import_skill(
            package,
            skill,
            assign_to=assign_to or [],
            all_members=all_members,
            replace=replace,
            confirm_managed=confirm_managed,
        )

    def test_empty_unified_skill_pool_and_unassigned_role_are_valid(self) -> None:
        data = json.loads(load_spec_text("expert-json"))
        manifest = self.root / "empty-unified" / "expert.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(self.output),
            ],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        package = self.output / "contract-review-expert"
        generated = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(generated["skills"], [])
        self.assertEqual(generated["agent"]["skills"], [])
        self.assertEqual(
            runtime["agent"]["contract-reviewer"]["permission"]["skill"],
            {"*": "deny"},
        )
        self.assertTrue(validate_expert.validate_package(package).ok)

    def test_unified_skill_pool_and_role_refs_may_be_omitted(self) -> None:
        data = json.loads(load_spec_text("expert-json"))
        data.pop("skills")
        data["agent"].pop("skills")
        manifest = self.root / "omitted-unified" / "expert.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(self.output),
            ],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        generated = json.loads(
            (self.output / "contract-review-expert/expert.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(generated["skills"], [])
        self.assertNotIn("skills", generated["agent"])

    def test_single_expert_import_migrates_and_preserves_every_skill_byte(self) -> None:
        package = self.create_package()
        before_legacy = {
            path.relative_to(package / ".opencode/skills").as_posix(): path.read_bytes()
            for path in (package / ".opencode/skills").rglob("*")
            if path.is_file()
        }
        source = self.create_skill()
        source_files = {
            path.relative_to(source).as_posix(): path.read_bytes()
            for path in source.rglob("*")
            if path.is_file()
        }
        result = self.import_into(package, source)
        self.assertEqual(result["action"], "imported")
        self.assertEqual(result["assignedTo"], ["contract-reviewer"])
        self.assertEqual(result["origin"], "uploaded")
        self.assertEqual(result["editPolicy"], "preserved")

        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        self.assertNotIn("common_skills", manifest)
        self.assertIn("uploaded-review", manifest["agent"]["skills"])
        entries = {item["name"]: item for item in manifest["skills"]}
        self.assertEqual(entries["uploaded-review"]["edit_policy"], "preserved")
        self.assertTrue(
            all(
                item["origin"] == "legacy-migrated"
                for name, item in entries.items()
                if name != "uploaded-review"
            )
        )
        destination = package / ".opencode/skills/uploaded-review"
        self.assertEqual(
            {
                path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file()
            },
            source_files,
        )
        self.assertEqual(
            {
                path.relative_to(package / ".opencode/skills").as_posix(): path.read_bytes()
                for path in (package / ".opencode/skills").rglob("*")
                if path.is_file()
                and not path.relative_to(package / ".opencode/skills").parts[0]
                == "uploaded-review"
            },
            before_legacy,
        )
        declared = {
            item["path"]: item["sha256"] for item in manifest["package_resources"]
        }
        for path in destination.rglob("*"):
            if path.is_file():
                relative = path.relative_to(package).as_posix()
                self.assertEqual(
                    declared[relative],
                    package_contract.sha256_bytes(path.read_bytes()),
                )

        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        permission = runtime["agent"]["contract-reviewer"]["permission"]["skill"]
        self.assertEqual(permission["uploaded-review"], "allow")
        self.assertEqual(permission["*"], "deny")
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(
            encoding="utf-8"
        )
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("uploaded-review", agent)
        self.assertIn("uploaded-review", readme)
        self.assertTrue(validate_expert.validate_package(package).ok)

    def test_preserved_skill_drift_is_rejected_by_hash_validation(self) -> None:
        package = self.create_package()
        source = self.create_skill()
        self.import_into(package, source)
        target = package / ".opencode/skills/uploaded-review/scripts/check.py"
        target.write_text("raise RuntimeError('changed')\n", encoding="utf-8")
        validation = validate_expert.validate_package(package)
        self.assertFalse(validation.ok)
        self.assertTrue(
            any("sha256" in message and "expected" in message for message in validation.errors),
            validation.errors,
        )

    def test_team_requires_assignment_and_failed_calls_are_byte_preserving(self) -> None:
        package = self.create_package(team=True)
        source = self.create_skill()
        before = package_digest(package)
        with self.assertRaisesRegex(
            import_skill.ImportSkillError,
            "require --assign-to",
        ):
            self.import_into(package, source)
        self.assertEqual(package_digest(package), before)
        with self.assertRaisesRegex(
            import_skill.ImportSkillError,
            "unknown Agent IDs: missing-role",
        ):
            self.import_into(package, source, assign_to=["missing-role"])
        self.assertEqual(package_digest(package), before)

        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
            import_skill.ImportSkillError,
            "duplicates qa-reviewer",
        ):
            import_skill.assignment_ids(
                manifest,
                requested=["qa-reviewer", "qa-reviewer"],
                all_members=False,
            )

    def test_team_supports_multiple_and_all_member_assignment(self) -> None:
        package = self.create_package(team=True)
        first = self.create_skill("shared-review", parent="first")
        self.import_into(
            package,
            first,
            assign_to=["delivery-director", "qa-reviewer"],
        )
        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        assigned = skill_contract.role_assignments(manifest)
        self.assertIn("shared-review", assigned["delivery-director"])
        self.assertIn("shared-review", assigned["qa-reviewer"])
        self.assertNotIn("shared-review", assigned["engineer"])

        second = self.create_skill("everyone-review", parent="second")
        result = self.import_into(package, second, all_members=True)
        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        assignments = skill_contract.role_assignments(manifest)
        self.assertEqual(set(result["assignedTo"]), set(assignments))
        for role_id, role_skills in assignments.items():
            self.assertIn("everyone-review", role_skills, role_id)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        for role_id, role in runtime["agent"].items():
            skill_permission = role["permission"]["skill"]
            self.assertEqual(skill_permission["everyone-review"], "allow", role_id)
        self.assertTrue(validate_expert.validate_package(package).ok)

    def test_same_content_reuses_and_different_content_requires_confirmed_replace(
        self,
    ) -> None:
        package = self.create_package()
        source = self.create_skill()
        imported = self.import_into(package, source)
        reused = self.import_into(package, source)
        self.assertEqual(reused["action"], "reused")
        self.assertEqual(reused["treeSha256"], imported["treeSha256"])

        replacement = self.create_skill(
            body="Changed only after explicit authorization.",
            parent="replacement",
        )
        before = package_digest(package)
        with self.assertRaisesRegex(
            import_skill.ImportSkillError,
            "already exists with different content",
        ):
            self.import_into(package, replacement)
        self.assertEqual(package_digest(package), before)
        with self.assertRaisesRegex(
            import_skill.ImportSkillError,
            "--replace and --confirm-managed",
        ):
            self.import_into(package, replacement, replace=True)
        self.assertEqual(package_digest(package), before)

        replaced = self.import_into(
            package,
            replacement,
            replace=True,
            confirm_managed=True,
        )
        self.assertEqual(replaced["action"], "replaced")
        self.assertEqual(replaced["origin"], "uploaded")
        self.assertEqual(replaced["editPolicy"], "managed")
        self.assertEqual(
            (package / ".opencode/skills/uploaded-review/SKILL.md").read_bytes(),
            (replacement / "SKILL.md").read_bytes(),
        )

    def test_single_expert_rejects_explicit_assignment_flags(self) -> None:
        package = self.create_package()
        source = self.create_skill()
        before = package_digest(package)
        for assign_to, all_members in [
            (["contract-reviewer"], False),
            ([], True),
        ]:
            with self.subTest(assign_to=assign_to, all_members=all_members):
                with self.assertRaisesRegex(
                    import_skill.ImportSkillError,
                    "assign uploaded skills automatically",
                ):
                    self.import_into(
                        package,
                        source,
                        assign_to=assign_to,
                        all_members=all_members,
                    )
                self.assertEqual(package_digest(package), before)

    def test_schema_mixing_unknown_skill_and_explicit_skill_permission_fail(self) -> None:
        base = json.loads(load_spec_text("legacy-expert-json"))
        mixed = json.loads(json.dumps(base))
        mixed["skills"] = []
        with self.assertRaisesRegex(
            package_contract.ContractError,
            "cannot be mixed",
        ):
            skill_contract.validate_manifest_skills(mixed)

        unified = json.loads(load_spec_text("expert-json"))
        unified["agent"]["skills"] = ["missing-skill"]
        manifest = self.root / "invalid-unified" / "expert.json"
        manifest.parent.mkdir()
        manifest.write_text(
            json.dumps(unified, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(self.output),
            ],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("references undeclared skill missing-skill", completed.stderr)

        unified["agent"]["skills"] = []
        unified["agent"]["permission"]["skill"] = {"*": "deny"}
        manifest.write_text(
            json.dumps(unified, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(self.output),
            ],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("permission.skill is derived", completed.stderr)

    def test_diagnosis_blocks_cache_secret_symlink_and_never_executes_script(
        self,
    ) -> None:
        root = self.create_skill("unsafe-skill")
        sentinel = self.root / "sentinel"
        (root / "scripts" / "side-effect.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(sentinel)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        (root / ".cache").mkdir()
        (root / ".cache" / "entry").write_text("cached", encoding="utf-8")
        (root / "secret.txt").write_text(
            "api_key=sk-abcdefghijklmnopqrstuvwxyz123456\n",
            encoding="utf-8",
        )
        os.symlink(root / "SKILL.md", root / "linked.md")
        with patch(
            "subprocess.run",
            side_effect=AssertionError("uploaded skill subprocess started"),
        ):
            result = diagnose_skill.diagnose(root)
        self.assertFalse(result.ok)
        self.assertFalse(sentinel.exists())
        errors = "\n".join(result.errors)
        self.assertIn("non-distributable directory", errors)
        self.assertIn("possible secret-like value", errors)
        self.assertIn("symlink is not allowed", errors)
        self.assertFalse(result.execution["attempted"])

    def test_zip_path_escape_is_blocked_before_extraction(self) -> None:
        archive = self.root / "escape.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(
                "escape-skill/SKILL.md",
                "---\nname: escape-skill\ndescription: Escape fixture.\n---\n",
            )
            output.writestr("../escaped.txt", "must not escape")
        result = diagnose_skill.diagnose(archive)
        self.assertFalse(result.ok)
        self.assertIn("ZIP_PATH_ESCAPE", {item.code for item in result.findings})
        self.assertFalse((self.root.parent / "escaped.txt").exists())

    def test_zip_compression_bomb_is_blocked_before_extraction(self) -> None:
        archive = self.root / "compression-bomb.zip"
        with zipfile.ZipFile(
            archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as output:
            output.writestr(
                "bomb-skill/SKILL.md",
                "---\nname: bomb-skill\ndescription: Bomb fixture.\n---\n",
            )
            output.writestr("bomb-skill/repeated.txt", b"0" * (2 * 1024 * 1024))
        result = diagnose_skill.diagnose(archive)
        self.assertFalse(result.ok)
        self.assertIn(
            "ZIP_COMPRESSION_RATIO_LIMIT",
            {item.code for item in result.findings},
        )

    def test_valid_zip_is_diagnosed_and_materialized_without_byte_drift(self) -> None:
        source = self.create_skill("zip-review")
        archive = self.root / "zip-review.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in source.rglob("*"):
                if path.is_file():
                    output.write(path, path.relative_to(source.parent).as_posix())
        result = diagnose_skill.diagnose(archive)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.execution["reason"], "untrusted-skill-zip")
        materialized = diagnose_skill.materialize_skill(
            archive,
            self.root / "materialized",
        )
        self.assertEqual(
            skill_contract.tree_sha256(materialized),
            skill_contract.tree_sha256(source),
        )

    def test_diagnosis_reports_invalid_sources_and_frontmatter_statically(self) -> None:
        missing = diagnose_skill.diagnose(self.root / "missing")
        self.assertIn("SKILL_SOURCE_MISSING", {item.code for item in missing.findings})

        unsupported = self.root / "skill.tar"
        unsupported.write_text("not an archive", encoding="utf-8")
        result = diagnose_skill.diagnose(unsupported)
        self.assertIn(
            "SKILL_SOURCE_UNSUPPORTED",
            {item.code for item in result.findings},
        )

        empty = self.root / "empty"
        empty.mkdir()
        result = diagnose_skill.diagnose(empty)
        self.assertIn("SKILL_ROOT_INVALID", {item.code for item in result.findings})

        invalid = self.root / "Bad Skill"
        invalid.mkdir()
        (invalid / "SKILL.md").write_text(
            "\n".join(
                [
                    "---",
                    "name: wrong-name",
                    "description: ''",
                    "compatibility:",
                    "  - unsupported",
                    "metadata: []",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (invalid / ".env").write_text("TOKEN=example", encoding="utf-8")
        (invalid / "bad.py").write_text("def broken(:\n", encoding="utf-8")
        (invalid / "cache.pyc").write_bytes(b"bytecode")
        result = diagnose_skill.diagnose(invalid)
        codes = {item.code for item in result.findings}
        self.assertTrue(
            {
                "SKILL_NAME_INVALID",
                "SKILL_NAME_MISMATCH",
                "SKILL_DESCRIPTION_INVALID",
                "SKILL_COMPATIBILITY_INVALID",
                "SKILL_METADATA_INVALID",
                "PYTHON_STATIC_SYNTAX_INVALID",
            }.issubset(codes),
            result.as_dict(),
        )
        self.assertTrue(
            any("non-distributable file" in message for message in result.errors)
        )
        self.assertTrue(
            any("non-distributable file suffix" in message for message in result.errors)
        )

        direct = ValidationResult(input_path=invalid)
        diagnose_skill.diagnose_root(self.root / "missing-root", direct)
        self.assertIn(
            "SKILL_DIRECTORY_INVALID",
            {item.code for item in direct.findings},
        )

    def test_import_rejects_official_frontmatter_violations_without_writes(
        self,
    ) -> None:
        package = self.create_package()
        cases = [
            (
                "too-long-compatibility",
                {"compatibility": "x" * 501},
                "SKILL_COMPATIBILITY_INVALID",
            ),
            (
                "numeric-metadata",
                {"metadata": {"version": 1}},
                "SKILL_METADATA_INVALID",
            ),
            (
                "list-allowed-tools",
                {"allowed-tools": ["Read"]},
                "SKILL_ALLOWED_TOOLS_INVALID",
            ),
            (
                "numeric-license",
                {"license": 1},
                "SKILL_LICENSE_INVALID",
            ),
            (
                "unknown-frontmatter",
                {"mobilework-extra": True},
                "SKILL_FRONTMATTER_FIELD_UNSUPPORTED",
            ),
        ]
        for skill_name, additions, expected_code in cases:
            with self.subTest(skill_name=skill_name):
                source = self.create_skill(skill_name, parent=skill_name)
                frontmatter: dict[str, object] = {
                    "name": skill_name,
                    "description": (
                        "Use when the official frontmatter import contract is tested."
                    ),
                    **additions,
                }
                (source / "SKILL.md").write_text(
                    "---\n"
                    + yaml.safe_dump(
                        frontmatter,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    + "---\n\n# Import fixture\n",
                    encoding="utf-8",
                )
                diagnosis = diagnose_skill.diagnose(source)
                self.assertIn(
                    expected_code,
                    {finding.code for finding in diagnosis.findings},
                )
                before = package_digest(package)
                with self.assertRaisesRegex(
                    import_skill.ImportSkillError,
                    "failed static diagnosis",
                ):
                    self.import_into(package, source)
                self.assertEqual(package_digest(package), before)

    def test_existing_noncompliant_skill_blocks_validate_package_and_install(
        self,
    ) -> None:
        package = self.create_package()
        source = self.create_skill()
        self.import_into(package, source)
        skill_md = package / ".opencode/skills/uploaded-review/SKILL.md"
        skill_md.write_text(
            "---\n"
            + yaml.safe_dump(
                {
                    "name": "uploaded-review",
                    "description": "Use when an uploaded review workflow is requested.",
                    "mobilework-extra": True,
                },
                sort_keys=False,
                allow_unicode=True,
            )
            + "---\n\n# Uploaded review\n",
            encoding="utf-8",
        )
        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = skill_md.relative_to(package).as_posix()
        for resource in manifest["package_resources"]:
            if resource["path"] == relative:
                resource["sha256"] = package_contract.sha256_bytes(
                    skill_md.read_bytes()
                )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        invalid_bytes = skill_md.read_bytes()

        validation = validate_expert.validate_package(package)
        self.assertFalse(validation.ok)
        self.assertIn(
            "SKILL_FRONTMATTER_FIELD_UNSUPPORTED",
            {finding.code for finding in validation.findings},
        )

        packaged = subprocess.run(
            [
                sys.executable,
                str(PACKAGE),
                "--package-dir",
                str(package),
                "--output-dir",
                str(self.root / "dist"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(packaged.returncode, 0)
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(package),
                "--workspace-dir",
                str(self.root / "workspace"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(installed.returncode, 0)
        self.assertEqual(skill_md.read_bytes(), invalid_bytes)

    def test_diagnose_skill_cli_blocks_runtime_and_import_cli_reports_errors(
        self,
    ) -> None:
        source = self.create_skill("cli-review")
        with patch.object(
            sys,
            "argv",
            [
                "diagnose_skill.py",
                str(source),
                "--format",
                "json",
                "--runtime",
            ],
        ), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(diagnose_skill.main(), 4)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["execution"]["reason"], "untrusted-runtime-blocked")
        self.assertFalse(payload["execution"]["attempted"])

        with patch.object(
            sys,
            "argv",
            [
                "import_skill.py",
                "--package-dir",
                str(self.root / "missing-package"),
                "--skill",
                str(source),
            ],
        ), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(import_skill.main(), 2)
        self.assertIn("target expert package is invalid", error.getvalue())


if __name__ == "__main__":
    unittest.main()
