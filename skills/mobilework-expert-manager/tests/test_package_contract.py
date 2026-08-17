from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"
PACKAGE = SCRIPTS / "package_expert.py"
TEAM_EXAMPLE = SKILL_ROOT / "evals" / "files" / "software-dev-team.expert.json"

sys.path.insert(0, str(SCRIPTS))
import package_contract as contract
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


def load_packager_module():
    spec = importlib.util.spec_from_file_location("mobilework_package_expert", PACKAGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load packager: {PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGER = load_packager_module()


def load_creator_module():
    spec = importlib.util.spec_from_file_location("mobilework_create_expert", CREATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load creator: {CREATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CREATOR = load_creator_module()


class PackageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def manifest(self, *, team: bool = False) -> tuple[Path, dict[str, object]]:
        path = self.root / "expert.json"
        source = (
            TEAM_EXAMPLE.read_text(encoding="utf-8")
            if team
            else load_spec_text("legacy-expert-json")
        )
        data = json.loads(source)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path, data

    def run_create(self, manifest: Path, *, force: bool = False) -> subprocess.CompletedProcess[str]:
        output = self.root / "out"
        command = [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(output)]
        if force:
            command.append("--force")
        return subprocess.run(
            command,
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )

    def run_validate(self, package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATE), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_generation_uses_namespaced_runtime_resources_and_steps(self) -> None:
        manifest, _ = self.manifest()
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "contract-review-expert"
        self.assertFalse((package / "references").exists())
        self.assertFalse((package / "instructions").exists())
        self.assertTrue(
            (package / ".opencode/references/contract-review-expert/playbook/overview.md").is_file()
        )
        self.assertTrue(
            (package / ".opencode/instructions/contract-review-expert/evidence.md").is_file()
        )
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(
            runtime["references"],
            {
                "contract-review-expert-playbook": {
                    "path": ".opencode/references/contract-review-expert/playbook",
                    "description": "Use for clause-level contract review guidance",
                }
            },
        )
        self.assertEqual(
            runtime["instructions"],
            [".opencode/instructions/contract-review-expert/*.md"],
        )
        self.assertEqual(runtime["agent"]["contract-reviewer"]["steps"], 80)
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("steps: 80", agent)
        self.assertNotIn("maxTurns", agent)

    def test_generator_rejects_unknown_text_resource_fields(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["reference_files"][0]["mystery"] = True
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reference_files[0] contains unsupported fields: mystery", result.stderr)

    def test_generator_rejects_invalid_package_dependency_contracts(self) -> None:
        cases = [
            ({"dependencies": {"": "1.0.0"}}, "non-empty package names"),
            ({"dependencies": {"demo": ""}}, "non-empty versions"),
            (
                {
                    "dependencies": {"shared": "1.0.0"},
                    "devDependencies": {"shared": "1.0.0"},
                },
                "cannot appear in both dependencies and devDependencies",
            ),
        ]
        for index, (package_json, expected) in enumerate(cases):
            with self.subTest(index=index):
                manifest, data = self.manifest()
                data["runtime_extensions"].setdefault("plugins", {})["package_json"] = package_json
                manifest.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.run_create(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_validator_applies_shared_manifest_contract(self) -> None:
        manifest, _ = self.manifest()
        created = self.run_create(manifest)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.root / "out" / "contract-review-expert"
        package_manifest = package / "expert.json"
        original = json.loads(package_manifest.read_text(encoding="utf-8"))

        mutated = json.loads(json.dumps(original))
        mutated["mystery"] = True
        package_manifest.write_text(
            json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = self.run_validate(package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expert.json: unknown fields mystery", result.stdout)

        mutated = json.loads(json.dumps(original))
        mutated["agent"]["mcp"] = ["missing-mcp"]
        package_manifest.write_text(
            json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = self.run_validate(package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references unknown MCP server missing-mcp", result.stdout)

        mutated = json.loads(json.dumps(original))
        mutated["runtime_extensions"].setdefault("plugins", {})["package_json"] = {
            "dependencies": {"shared": "1.0.0"},
            "devDependencies": {"shared": "1.0.0"},
        }
        package_manifest.write_text(
            json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = self.run_validate(package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "package shared cannot appear in both dependencies and devDependencies",
            result.stdout,
        )

    def test_validator_rejects_runtime_extension_content_drift(self) -> None:
        manifest, data = self.manifest()
        extensions = data["runtime_extensions"]
        extensions["commands"] = [
            {
                "name": "runtime-check",
                "description": "检查 runtime canary",
                "template": "执行 runtime canary。\n用户要求：$ARGUMENTS",
            }
        ]
        extensions["custom_tools"] = [
            {
                "path": "runtime-check.ts",
                "purpose": "执行 runtime canary 的确定性检查。",
                "content": "export default {}",
            }
        ]
        extensions["plugins"] = {
            "local": [
                {
                    "path": "runtime-hook.ts",
                    "content": "export const RuntimeHook = async () => ({})",
                }
            ],
            "package_json": {
                "dependencies": {"@opencode-ai/plugin": "1.18.3"}
            },
        }
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        created = self.run_create(manifest)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.root / "out" / "contract-review-expert"
        cases = [
            (
                package / ".opencode/commands/runtime-check.md",
                "mutated command\n",
                "runtime command projection differs from expert.json",
            ),
            (
                package / ".opencode/tools/runtime-check.ts",
                "export default { mutated: true }\n",
                "custom tool content differs from expert.json",
            ),
            (
                package / ".opencode/plugins/runtime-hook.ts",
                "export const RuntimeHook = 'mutated'\n",
                "local plugin content differs from expert.json",
            ),
            (
                package / ".opencode/package.json",
                json.dumps(
                    {"dependencies": {"@opencode-ai/plugin": "9.9.9"}},
                    indent=2,
                ) + "\n",
                ".opencode/package.json must exactly match",
            ),
        ]
        for path, mutation, expected in cases:
            with self.subTest(path=path.name):
                original = path.read_text(encoding="utf-8")
                path.write_text(mutation, encoding="utf-8")
                result = self.run_validate(package)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout)
                path.write_text(original, encoding="utf-8")

    def test_skill_purposes_generate_strict_common_and_agent_names(self) -> None:
        manifest, _ = self.manifest()
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "contract-review-expert"
        expected = {
            "contract-review-expert-common-delivery-quality",
            "contract-review-expert-contract-reviewer-role-guidelines",
            "contract-review-expert-contract-reviewer-clause-checklist",
        }
        actual = {
            path.name for path in (package / ".opencode/skills").iterdir() if path.is_dir()
        }
        self.assertEqual(actual, expected)
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(encoding="utf-8")
        readme = (package / "README.md").read_text(encoding="utf-8")
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        skill_permission = runtime["agent"]["contract-reviewer"]["permission"]["skill"]
        for skill_name in expected:
            self.assertIn(skill_name, agent)
            self.assertIn(skill_name, readme)
            self.assertEqual(skill_permission[skill_name], "allow")
            skill_text = (package / ".opencode/skills" / skill_name / "SKILL.md").read_text(
                encoding="utf-8"
            )
            self.assertIn(f"name: {skill_name}", skill_text)

    def test_team_skill_names_are_scoped_to_each_agent(self) -> None:
        manifest, _ = self.manifest(team=True)
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "software-dev-team"
        expected = {
            "software-dev-team-common-delivery-quality",
            "software-dev-team-delivery-director-role-guidelines",
            "software-dev-team-delivery-director-delivery-review",
            "software-dev-team-product-strategist-role-guidelines",
            "software-dev-team-product-strategist-product-brief",
            "software-dev-team-architect-role-guidelines",
            "software-dev-team-architect-architecture-review",
            "software-dev-team-engineer-role-guidelines",
            "software-dev-team-engineer-code-change",
            "software-dev-team-engineer-test-runner",
            "software-dev-team-qa-reviewer-role-guidelines",
            "software-dev-team-qa-reviewer-test-plan",
        }
        actual = {
            path.name for path in (package / ".opencode/skills").iterdir() if path.is_dir()
        }
        self.assertEqual(actual, expected)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        product_skills = runtime["agent"]["product-strategist"]["permission"]["skill"]
        self.assertIn("software-dev-team-product-strategist-product-brief", product_skills)
        self.assertNotIn("software-dev-team-architect-architecture-review", product_skills)

    def test_legacy_and_invalid_skill_declarations_are_rejected(self) -> None:
        cases = [
            ("legacy common", lambda data: data.__setitem__("common_skills", ["contract-review-expert-common"]), "must be a mapping"),
            ("legacy role", lambda data: data["agent"].__setitem__("skills", ["contract-review-expert-clause-checklist"]), "must be a mapping"),
            ("missing common", lambda data: data.pop("common_skills"), "non-empty list"),
            ("empty common", lambda data: data.__setitem__("common_skills", []), "non-empty list"),
            ("empty role", lambda data: data["agent"].__setitem__("skills", []), "non-empty list"),
            ("empty purpose", lambda data: data.__setitem__("common_skills", [{"purpose": ""}]), "lowercase-hyphen"),
            ("invalid purpose", lambda data: data["agent"].__setitem__("skills", [{"purpose": "Clause Review"}]), "lowercase-hyphen"),
            ("full skill name", lambda data: data["agent"].__setitem__("skills", [{"purpose": "contract-review-expert-contract-reviewer-checklist"}]), "not a complete skill name"),
            ("extra field", lambda data: data.__setitem__("common_skills", [{"purpose": "quality", "name": "legacy"}]), "only purpose"),
            ("duplicate", lambda data: data.__setitem__("common_skills", [{"purpose": "quality"}, {"purpose": "quality"}]), "duplicates quality"),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                manifest, data = self.manifest()
                mutate(data)
                manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                result = self.run_create(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_permission_skill_must_reference_computed_agent_skills(self) -> None:
        manifest, data = self.manifest(team=True)
        data["subagents"][0]["permission"]["skill"][
            "software-dev-team-architect-architecture-review"
        ] = "allow"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references undeclared skill", result.stderr)

    def test_validator_rejects_legacy_skill_manifest_after_generation(self) -> None:
        manifest, _ = self.manifest()
        generated = self.run_create(manifest)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = self.root / "out" / "contract-review-expert"
        package_manifest_path = package / "expert.json"
        package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
        package_manifest["common_skills"] = ["contract-review-expert-common"]
        package_manifest["agent"]["skills"] = ["contract-review-expert-contract-reviewer"]
        package_manifest_path.write_text(
            json.dumps(package_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = self.run_validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be a mapping with only purpose", result.stdout)

    def test_package_resource_rejects_legacy_skill_path(self) -> None:
        manifest, data = self.manifest()
        relative = Path(".opencode/skills/contract-review-expert-clause-checklist/scripts/check.py")
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_text("print('legacy')\n", encoding="utf-8")
        data["package_resources"] = [{"path": relative.as_posix(), "kind": "text"}]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("declared supplemental skill", result.stderr)

    def test_old_root_reference_and_instruction_paths_are_rejected(self) -> None:
        manifest, data = self.manifest()
        ext = data["runtime_extensions"]
        ext["reference_files"][0]["path"] = "references/playbook/overview.md"
        ext["references"]["playbook"]["path"] = "references/playbook"
        ext["instruction_files"][0]["path"] = "instructions/evidence.md"
        ext["instructions"] = ["instructions/*.md"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be under .opencode/references/contract-review-expert", result.stderr)

    def test_reference_string_shorthand_is_rejected(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["references"]["playbook"] = ".opencode/references/contract-review-expert/playbook"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("string shorthand is not allowed", result.stderr)

    def test_local_and_git_reference_options_are_projected(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["references"]["playbook"]["hidden"] = True
        data["runtime_extensions"]["references"]["upstream"] = {
            "repository": "https://example.com/reference.git",
            "branch": "stable",
            "description": "Upstream reference",
            "hidden": False,
        }
        data["agent"]["references"].append("upstream")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = json.loads(
            (self.root / "out/contract-review-expert/opencode.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            runtime["references"],
            {
                "contract-review-expert-playbook": {
                    "path": ".opencode/references/contract-review-expert/playbook",
                    "description": "Use for clause-level contract review guidance",
                    "hidden": True,
                },
                "contract-review-expert-upstream": {
                    "repository": "https://example.com/reference.git",
                    "branch": "stable",
                    "description": "Upstream reference",
                    "hidden": False,
                },
            },
        )

    def test_reference_source_and_optional_field_contract_is_strict(self) -> None:
        local_path = ".opencode/references/contract-review-expert/playbook"
        cases = [
            ({"description": "missing source"}, "must define exactly one of path or repository"),
            (
                {"path": local_path, "repository": "https://example.com/reference.git"},
                "must define exactly one of path or repository",
            ),
            ({"path": local_path, "branch": "main"}, "branch: is only valid with repository"),
            ({"repository": ""}, "repository: must be a non-empty string"),
            ({"repository": "https://example.com/reference.git", "branch": ""}, "branch: must be a non-empty string"),
            ({"path": local_path, "description": 1}, "description: must be a string"),
            ({"path": local_path, "hidden": "yes"}, "hidden: must be a boolean"),
            ({"path": local_path, "source": "local"}, "unsupported fields source"),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                manifest, data = self.manifest()
                data["runtime_extensions"]["references"]["playbook"] = entry
                manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                result = self.run_create(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_git_reference_rejects_embedded_credentials(self) -> None:
        rejected = [
            "https://user:password@example.com/reference.git",
            "https://embedded-user@example.com/reference.git",
            "https://ghp_1234567890abcdefghijkl@example.com/reference.git",
            "https://example.com/reference.git?token=secret-value",
            "https://example.com/reference.git?oauth_token=secret-value",
            "https://{env:GIT_TOKEN}@example.com/reference.git",
        ]
        for repository in rejected:
            with self.subTest(repository=repository):
                with self.assertRaisesRegex(contract.ContractError, "must not embed credentials"):
                    contract.normalize_reference_entries(
                        {"upstream": {"repository": repository}},
                        "runtime_extensions.references",
                        slug="contract-review-expert",
                        reference_file_paths=[],
                    )
        for repository in (
            "example-org/reference",
            "github.com/example-org/reference",
            "git@github.com:example-org/reference.git",
            "ssh://git@github.com/example-org/reference.git",
        ):
            with self.subTest(allowed=repository):
                normalized = contract.normalize_reference_entries(
                    {"upstream": {"repository": repository}},
                    "runtime_extensions.references",
                    slug="contract-review-expert",
                    reference_file_paths=[],
                )
                self.assertEqual(normalized["upstream"]["repository"], repository)

    def test_git_reference_rejects_local_or_ambiguous_repository_sources(self) -> None:
        rejected = (
            "file:///home/example/private-reference.git",
            "file:/home/example/private-reference.git",
            "file:relative/private-reference.git",
            "ext::sh%20-c%20id",
            "/home/example/private-reference.git",
            "~/private-reference.git",
            "../private-reference.git",
            "C:private-reference.git",
            "z:repo",
            "https://example.com/reference.git?ref=main",
            "https://example.com/reference.git#main",
        )
        for repository in rejected:
            with self.subTest(repository=repository):
                with self.assertRaises(contract.ContractError):
                    contract.normalize_reference_entries(
                        {"upstream": {"repository": repository}},
                        "runtime_extensions.references",
                        slug="contract-review-expert",
                        reference_file_paths=[],
                    )

    def test_git_reference_without_branch_reports_default_branch_warning(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["reference_files"] = []
        data["runtime_extensions"]["references"] = {
            "upstream": {
                "repository": "example-org/reference",
                "description": "Use for upstream implementation guidance",
            }
        }
        data["agent"]["references"] = ["upstream"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        created = self.run_create(manifest)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.root / "out/contract-review-expert"
        validated = subprocess.run(
            [sys.executable, str(VALIDATE), str(package), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        findings = json.loads(validated.stdout)["findings"]
        self.assertIn("REFERENCE_GIT_DEFAULT_BRANCH", {item["code"] for item in findings})

    def test_reference_files_must_be_owned_by_a_local_reference(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["references"] = {
            "upstream": {"repository": "https://example.com/reference.git"}
        }
        data["agent"]["references"] = ["upstream"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not owned by a local references entry", result.stderr)

    def test_local_reference_must_point_to_its_alias_directory(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["references"]["playbook"]["path"] = (
            ".opencode/references/contract-review-expert/playbook/overview.md"
        )
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must equal .opencode/references/contract-review-expert/playbook", result.stderr)

    def test_validator_rejects_reference_projection_drift(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["references"]["upstream"] = {
            "repository": "https://example.com/reference.git",
            "branch": "stable",
        }
        data["agent"]["references"].append("upstream")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        created = self.run_create(manifest)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.root / "out/contract-review-expert"
        config_path = package / "opencode.json"
        original = json.loads(config_path.read_text(encoding="utf-8"))
        for alias, field, value in [
            ("contract-review-expert-playbook", "description", "drifted"),
            ("contract-review-expert-upstream", "branch", "drifted"),
        ]:
            with self.subTest(alias=alias):
                mutated = json.loads(json.dumps(original))
                mutated["references"][alias][field] = value
                config_path.write_text(
                    json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.run_validate(package)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("references must use <slug>-<alias> names and exactly match", result.stdout)
        config_path.write_text(
            json.dumps(original, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_validator_rejects_invalid_reference_manifest_contract(self) -> None:
        manifest, _ = self.manifest()
        created = self.run_create(manifest)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.root / "out/contract-review-expert"
        package_manifest = package / "expert.json"
        original = json.loads(package_manifest.read_text(encoding="utf-8"))
        local_path = ".opencode/references/contract-review-expert/playbook"
        cases = [
            (
                {"path": local_path, "repository": "https://example.com/reference.git"},
                "must define exactly one of path or repository",
            ),
            ({"path": local_path, "source": "local"}, "unsupported fields source"),
            ({"path": local_path, "hidden": "yes"}, "hidden: must be a boolean"),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                mutated = json.loads(json.dumps(original))
                mutated["runtime_extensions"]["references"]["playbook"] = entry
                package_manifest.write_text(
                    json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = self.run_validate(package)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout)
        package_manifest.write_text(
            json.dumps(original, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_http_and_unmatched_instruction_globs_are_rejected(self) -> None:
        for value, expected in [
            ("http://example.com/rules.md", "must use https"),
            (".opencode/instructions/contract-review-expert/missing/*.md", "no matching"),
        ]:
            with self.subTest(value=value):
                manifest, data = self.manifest()
                data["runtime_extensions"]["instructions"] = [value]
                manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                result = self.run_create(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)

    def test_https_instruction_is_allowed_with_reproducibility_warning(self) -> None:
        manifest, data = self.manifest()
        data["runtime_extensions"]["instructions"] = ["https://example.com/rules.md"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not reproducible", result.stderr)

    def test_declared_binary_resource_is_hashed_and_survives_force_regeneration(self) -> None:
        manifest, data = self.manifest()
        relative = Path(
            ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/templates/input.xlsx"
        )
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_bytes(b"PK\x03\x04fake-xlsx-fixture")
        data["package_resources"] = [{"path": relative.as_posix(), "kind": "binary"}]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        first = self.run_create(manifest)
        self.assertEqual(first.returncode, 0, first.stderr)
        package = self.root / "out" / "contract-review-expert"
        generated = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        self.assertEqual(generated["package_resources"][0]["sha256"], contract.sha256_bytes(source.read_bytes()))
        self.assertEqual((package / relative).read_bytes(), source.read_bytes())

        second = self.run_create(package / "expert.json", force=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((package / relative).read_bytes(), source.read_bytes())

    def test_force_atomic_replace_restores_original_after_commit_failure(self) -> None:
        manifest_path, data = self.manifest()
        normalized = CREATOR.normalize_manifest(data, manifest_dir=manifest_path.parent)
        CREATOR.prepare_avatar_assets(normalized, manifest_path.parent)
        output = self.root / "atomic"
        output.mkdir()
        package = CREATOR.write_project(normalized, output, force=False)
        before = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }

        real_replace = os.replace
        calls = 0

        def flaky_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated atomic replace failure")
            real_replace(source, target)

        with patch.object(CREATOR.os, "replace", side_effect=flaky_replace):
            with self.assertRaisesRegex(OSError, "simulated atomic"):
                CREATOR.write_project(normalized, output, force=True)
        after = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_package_resource_hash_mismatch_is_rejected(self) -> None:
        manifest, data = self.manifest()
        relative = Path(".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/scripts/check.py")
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_text("print('ok')\n", encoding="utf-8")
        data["package_resources"] = [
            {"path": relative.as_posix(), "kind": "text", "sha256": "0" * 64}
        ]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result = self.run_create(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sha256 does not match", result.stderr)

    def test_validator_detects_embedded_content_drift_and_undeclared_skill_file(self) -> None:
        manifest, _ = self.manifest()
        generated = self.run_create(manifest)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = self.root / "out" / "contract-review-expert"
        reference = package / ".opencode/references/contract-review-expert/playbook/overview.md"
        reference.write_text("changed\n", encoding="utf-8")
        extra = package / ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/scripts/extra.py"
        extra.parent.mkdir(parents=True)
        extra.write_text("print('extra')\n", encoding="utf-8")
        result = self.run_validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("generated file content differs", result.stdout)
        self.assertIn("undeclared supplemental skill resource", result.stdout)

    def test_validator_rejects_binary_reference_content(self) -> None:
        manifest, _ = self.manifest()
        generated = self.run_create(manifest)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = self.root / "out" / "contract-review-expert"
        reference = package / ".opencode/references/contract-review-expert/playbook/overview.md"
        reference.write_bytes(b"\xff\xfe\x00\x01")

        result = self.run_validate(package)

        self.assertEqual(result.returncode, 1)
        self.assertIn("generated file is not UTF-8", result.stdout)

    def test_validator_rejects_root_runtime_dirs_and_symlinks(self) -> None:
        manifest, _ = self.manifest()
        generated = self.run_create(manifest)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = self.root / "out" / "contract-review-expert"
        (package / "references").mkdir()
        (package / "references/legacy.md").write_text("legacy\n", encoding="utf-8")
        (package / "instructions").mkdir()
        (package / "instructions/legacy.md").write_text("legacy\n", encoding="utf-8")
        result = self.run_validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside the package allowlist", result.stdout)

        if hasattr(os, "symlink"):
            shutil.rmtree(package / "references")
            shutil.rmtree(package / "instructions")
            (self.root / "outside.txt").write_text("secret\n", encoding="utf-8")
            (package / ".opencode/skills/contract-review-expert-common-delivery-quality/linked.txt").symlink_to(
                self.root / "outside.txt"
            )
            result = self.run_validate(package)
            self.assertEqual(result.returncode, 1)
            expected = (
                "INPUT_REPARSE_POINT_FORBIDDEN"
                if os.name == "nt"
                else "symlink is not allowed"
            )
            self.assertIn(expected, result.stdout)

    def test_avatar_content_and_svg_safety_are_enforced(self) -> None:
        for name, content, expected in [
            ("fake.PNG", b"not-a-png", "not PNG"),
            ("unsafe.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "unsafe SVG"),
            (
                "external.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><style>.x{fill:url(https://example.com/a)}</style></svg>',
                "external SVG reference",
            ),
            ("large.gif", b"GIF89a" + b"x" * contract.MAX_AVATAR_BYTES, "2 MiB"),
        ]:
            with self.subTest(name=name):
                with self.assertRaisesRegex(contract.ContractError, expected):
                    contract.validate_avatar_bytes(content, Path(name).suffix, name)

    def test_supported_avatar_magic_bytes_are_case_insensitive(self) -> None:
        fixtures = {
            ".PNG": b"\x89PNG\r\n\x1a\n",
            ".JpEg": b"\xff\xd8\xff\xff\xd9",
            ".WEBP": b"RIFF\x00\x00\x00\x00WEBP",
            ".GiF": b"GIF89a",
            ".SvG": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        }
        for suffix, content in fixtures.items():
            with self.subTest(suffix=suffix):
                contract.validate_avatar_bytes(content, suffix, f"avatar{suffix}")

    def test_remote_avatar_requires_https(self) -> None:
        manifest, data = self.manifest()
        data["avatar_url"] = "http://example.com/avatar.png"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        rejected = self.run_create(manifest)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("https URL", rejected.stderr)

        data["avatar_url"] = "https://example.com/package.png"
        data["agent"]["avatar_url"] = "https://example.com/agent.webp"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        accepted = self.run_create(manifest)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_validator_rejects_unreferenced_avatar(self) -> None:
        manifest, _ = self.manifest()
        generated = self.run_create(manifest)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = self.root / "out" / "contract-review-expert"
        (package / "avatars/unused.gif").write_bytes(b"GIF89a")
        result = self.run_validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("unreferenced avatar file", result.stdout)

    def test_team_uses_task_contract_and_not_legacy_team_tools(self) -> None:
        manifest, _ = self.manifest(team=True)
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "software-dev-team"
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((package / ".opencode/agents").glob("*.md"))
        )
        self.assertNotIn("TeamCreate", combined)
        self.assertNotIn("SendMessage", combined)
        self.assertIn("subagent_type", combined)
        self.assertIn("task_id", combined)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        task = runtime["agent"]["delivery-director"]["permission"]["task"]
        self.assertEqual(next(iter(task)), "*")
        self.assertEqual(task["product-strategist"], "allow")
        self.assertEqual(runtime["agent"]["product-strategist"]["permission"]["task"], {"*": "deny"})

    def test_packager_round_trip(self) -> None:
        manifest, _ = self.manifest()
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "contract-review-expert"
        packed = subprocess.run(
            [sys.executable, str(PACKAGE), "--package-dir", str(package), "--output-dir", str(self.root / "dist")],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(packed.returncode, 0, packed.stderr)
        archive = self.root / "dist" / "contract-review-expert.zip"
        self.assertTrue(archive.is_file())
        extracted = self.root / "extracted"
        shutil.unpack_archive(archive, extracted)
        validated = self.run_validate(extracted / "contract-review-expert")
        self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_packager_independently_rejects_symlink_and_unallowlisted_path(self) -> None:
        manifest, _ = self.manifest()
        result = self.run_create(manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.root / "out" / "contract-review-expert"
        if hasattr(os, "symlink"):
            expected = (
                "INPUT_REPARSE_POINT_FORBIDDEN"
                if os.name == "nt"
                else "symlink is not allowed"
            )
            outside = self.root / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            link = package / ".opencode/skills/contract-review-expert-common-delivery-quality/link.txt"
            link.symlink_to(outside)
            with self.assertRaisesRegex(SystemExit, expected):
                PACKAGER.make_zip(package, self.root / "dist-symlink")
            link.unlink()
            package_link = self.root / "package-link"
            package_link.symlink_to(package, target_is_directory=True)
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE),
                    "--package-dir",
                    str(package_link),
                    "--output-dir",
                    str(self.root / "dist-root-link"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn(expected, rejected.stdout)
        extra = package / ".opencode/tools/undeclared.ts"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("unexpected\n", encoding="utf-8")
        with self.assertRaisesRegex(
            SystemExit,
            "undeclared package file|not declared by expert.json",
        ):
            PACKAGER.make_zip(package, self.root / "dist-extra")


if __name__ == "__main__":
    unittest.main()
