from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


def frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    raw = text.split("---", 2)[1]
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"invalid frontmatter in {path}")
    return value


class OpenCodeAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / "expert.json"
        self.data = json.loads(load_spec_text("legacy-expert-json"))
        self.write_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self) -> None:
        self.manifest.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def create(self) -> subprocess.CompletedProcess[str]:
        output = self.root / "out"
        return subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(self.manifest),
                "--output-dir",
                str(output),
            ],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )

    def validate(self, package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATE), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )

    @property
    def package(self) -> Path:
        return self.root / "out" / "contract-review-expert"

    def test_generated_agent_and_skill_use_opencode_authoring_contract(self) -> None:
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = json.loads((self.package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["$schema"], "https://opencode.ai/config.json")
        self.assertEqual(set(runtime), {"$schema", "agent", "references", "instructions"})
        self.assertEqual(
            runtime["references"],
            {
                "contract-review-expert-playbook": {
                    "path": ".opencode/references/contract-review-expert/playbook",
                    "description": "Use for clause-level contract review guidance",
                }
            },
        )
        self.assertEqual(runtime["instructions"], [".opencode/instructions/contract-review-expert/*.md"])
        self.assertEqual(
            set(runtime["agent"]["contract-reviewer"]),
            {"mode", "description", "steps", "permission"},
        )

        agent_path = self.package / ".opencode/agents/contract-reviewer.md"
        agent_frontmatter = frontmatter(agent_path)
        description = agent_frontmatter["description"]
        self.assertIsInstance(description, str)
        self.assertIn(self.data["agent"]["description"], description)
        self.assertIn(self.data["agent"]["route_triggers"][0], description)
        self.assertEqual(runtime["agent"]["contract-reviewer"]["description"], description)
        agent_text = agent_path.read_text(encoding="utf-8")
        self.assertIn("## 触发与不适用场景", agent_text)
        self.assertIn("## 异常处理", agent_text)

        skill_path = self.package / ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/SKILL.md"
        skill_frontmatter = frontmatter(skill_path)
        self.assertEqual(skill_frontmatter["name"], "contract-review-expert-contract-reviewer-clause-checklist")
        self.assertEqual(skill_frontmatter["compatibility"], "opencode")
        self.assertEqual(
            skill_frontmatter["metadata"],
            {
                "package": "contract-review-expert",
                "role": "contract-reviewer",
                "type": "role",
            },
        )
        self.assertLessEqual(len(skill_frontmatter["description"]), 1024)
        self.assertIn("## 资源导航", skill_path.read_text(encoding="utf-8"))

        permission = runtime["agent"]["contract-reviewer"]["permission"]
        self.assertEqual(permission["edit"], "allow")
        self.assertEqual(permission["bash"]["*"], "ask")
        self.assertEqual(permission["webfetch"], "allow")
        self.assertIn("## 配置与环境变量", (self.package / "README.md").read_text(encoding="utf-8"))

    def test_runtime_config_rejects_fields_not_owned_by_package(self) -> None:
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        config_path = self.package / "opencode.json"
        original = json.loads(config_path.read_text(encoding="utf-8"))

        for key, value in [
            ("unexpected", True),
            ("model", "example/model"),
            ("provider", {}),
            ("command", {"review": {"template": "Review."}}),
        ]:
            with self.subTest(root_key=key):
                mutated = copy.deepcopy(original)
                mutated[key] = value
                config_path.write_text(
                    json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                validated = self.validate(self.package)
                self.assertNotEqual(validated.returncode, 0)
                self.assertIn(f"root keys {key} are not owned", validated.stdout)
                self.assertIn(
                    "official OpenCode schema support does not make a field package-owned",
                    validated.stdout,
                )
                self.assertIn("Declare support through expert.json", validated.stdout)

        for key, value in [
            ("prompt", "override"),
            ("disable", True),
            ("maxSteps", 10),
            ("provider", {}),
        ]:
            with self.subTest(agent_key=key):
                mutated = copy.deepcopy(original)
                mutated["agent"]["contract-reviewer"][key] = value
                config_path.write_text(
                    json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                validated = self.validate(self.package)
                self.assertNotEqual(validated.returncode, 0)
                self.assertIn(
                    f"agent.contract-reviewer keys {key} are not owned",
                    validated.stdout,
                )
                self.assertIn(
                    "official OpenCode schema support does not make a field package-owned",
                    validated.stdout,
                )

        for key, value in [
            ("model", "example/model"),
            ("variant", "high"),
            ("temperature", 0.2),
            ("top_p", 0.8),
            ("hidden", True),
            ("options", {"reasoningEffort": "high"}),
        ]:
            with self.subTest(undeclared_agent_key=key):
                mutated = copy.deepcopy(original)
                mutated["agent"]["contract-reviewer"][key] = value
                config_path.write_text(
                    json.dumps(mutated, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                validated = self.validate(self.package)
                self.assertNotEqual(validated.returncode, 0)
                self.assertIn(
                    f"agent.contract-reviewer.{key} must be omitted when absent from expert.json",
                    validated.stdout,
                )

    def test_env_references_generate_deterministic_example(self) -> None:
        self.data["mcp_servers"] = [
            {
                "name": "secure-docs",
                "type": "remote",
                "url": "https://example.com/{env:TENANT_ID}/mcp",
                "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
                "enabled": False,
            }
        ]
        self.write_manifest()
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.package / ".env.example").read_text(encoding="utf-8"),
            "API_TOKEN=<required>\nTENANT_ID=<required>\n",
        )
        readme = (self.package / "README.md").read_text(encoding="utf-8")
        self.assertIn("`API_TOKEN`", readme)
        self.assertIn("`TENANT_ID`", readme)
        validated = self.validate(self.package)
        self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_env_example_is_omitted_without_references(self) -> None:
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.package / ".env.example").exists())

    def test_declared_skill_resources_are_navigable(self) -> None:
        relative = Path(
            ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/references/rules.md"
        )
        source = self.root / relative
        source.parent.mkdir(parents=True)
        source.write_text("# Clause rules\n", encoding="utf-8")
        self.data["package_resources"] = [{"path": relative.as_posix(), "kind": "text"}]
        self.write_manifest()
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill_path = self.package / ".opencode/skills/contract-review-expert-contract-reviewer-clause-checklist/SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("references/rules.md", text)

        skill_path.write_text(text.replace("references/rules.md", "references/other.md"), encoding="utf-8")
        validated = self.validate(self.package)
        self.assertNotEqual(validated.returncode, 0)
        self.assertIn("resource navigation missing declared path references/rules.md", validated.stdout)

    def test_skill_frontmatter_negative_cases(self) -> None:
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        skill_path = self.package / ".opencode/skills/contract-review-expert-common-delivery-quality/SKILL.md"
        original = skill_path.read_text(encoding="utf-8")

        for replacement, expected in [
            (original.replace("name: contract-review-expert-common-delivery-quality", "name: wrong-name"), "frontmatter name"),
            (original.replace("description:", "description: ''\nold-description:"), "description must be non-empty"),
            (original.replace("compatibility: opencode", "compatibility: another-host"), "compatibility must equal opencode"),
            (original.replace("metadata:\n", "metadata: invalid\nold-metadata:\n"), "metadata must map strings to strings"),
        ]:
            skill_path.write_text(replacement, encoding="utf-8")
            validated = self.validate(self.package)
            self.assertNotEqual(validated.returncode, 0)
            self.assertIn(expected, validated.stdout)
            skill_path.write_text(original, encoding="utf-8")

        data = frontmatter(skill_path)
        data["description"] = "x" * 1025
        body = original.split("---", 2)[2]
        skill_path.write_text(
            "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---" + body,
            encoding="utf-8",
        )
        too_long = self.validate(self.package)
        self.assertNotEqual(too_long.returncode, 0)
        self.assertIn("description must be 1024 characters or fewer", too_long.stdout)

    def test_missing_env_example_warns_but_mismatch_fails(self) -> None:
        self.data["mcp_servers"] = [
            {
                "name": "secure-docs",
                "type": "remote",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
            }
        ]
        self.write_manifest()
        result = self.create()
        self.assertEqual(result.returncode, 0, result.stderr)
        env_path = self.package / ".env.example"
        env_path.unlink()
        missing = self.validate(self.package)
        self.assertEqual(missing.returncode, 0, missing.stdout)
        self.assertIn(".env.example is missing", missing.stdout)

        env_path.write_text("WRONG=<required>\n", encoding="utf-8")
        mismatch = self.validate(self.package)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("must exactly list sorted referenced variables", mismatch.stdout)

        env_path.write_text("API_TOKEN=sk-abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
        secret = self.validate(self.package)
        self.assertNotEqual(secret.returncode, 0)
        self.assertIn("possible secret-like value", secret.stdout)

    def test_mcp_environment_and_headers_require_string_values(self) -> None:
        self.data["mcp_servers"] = [
            {
                "name": "invalid-mcp",
                "type": "local",
                "command": ["demo"],
                "environment": {"RETRIES": 3},
            }
        ]
        self.write_manifest()
        result = self.create()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("environment must map strings to strings", result.stderr)


if __name__ == "__main__":
    unittest.main()
