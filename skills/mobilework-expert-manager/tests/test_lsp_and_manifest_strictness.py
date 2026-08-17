from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


def read_frontmatter(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    _, raw, _ = source.split("---", 2)
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"invalid frontmatter in {path}")
    return value


class LspAndManifestStrictnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads(load_spec_text("legacy-expert-json"))
        self.base["runtime_extensions"] = {}
        self.base["agent"]["references"] = []
        self.base["agent"].pop("instructions", None)
        self.base.pop("mcp_servers", None)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate(
        self,
        data: dict[str, object],
        *,
        name: str,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        source = self.root / name / "expert.json"
        source.parent.mkdir(parents=True)
        source.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / f"{name}-out"
        result = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(source),
                "--output-dir",
                str(output),
            ],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output / str(data["slug"])

    def validate(self, package: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATE), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )

    def rewrite_manifest(
        self,
        package: Path,
        mutate: Callable[[dict[str, object]], None],
    ) -> None:
        path = package / "expert.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        mutate(manifest)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_lsp_official_root_shapes_project_exactly(self) -> None:
        mapping = {
            "builtin-disabled": {"disabled": True},
            "custom-server": {
                "command": ["custom-lsp", "--stdio"],
                "extensions": ["custom", ".custom-template"],
                "disabled": False,
                "env": {"CUSTOM_MODE": "strict"},
                "initialization": {"settings": {"strict": True}},
            },
        }
        for name, lsp in [("enabled", True), ("disabled", False), ("mapping", mapping)]:
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                data["runtime_extensions"] = {"lsp": lsp}
                created, package = self.generate(data, name=f"lsp-{name}")
                self.assertEqual(created.returncode, 0, created.stderr)
                runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
                self.assertEqual(runtime["lsp"], lsp)
                validated = self.validate(package)
                self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_lsp_omission_is_distinct_from_false_and_empty_mapping_is_rejected(self) -> None:
        data = copy.deepcopy(self.base)
        created, package = self.generate(data, name="lsp-omitted")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime_path = package / "opencode.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertNotIn("lsp", runtime)
        self.assertEqual(self.validate(package).returncode, 0)

        runtime["lsp"] = False
        runtime_path.write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        rejected_projection = self.validate(package)
        self.assertNotEqual(rejected_projection.returncode, 0)
        self.assertIn("lsp must be omitted", rejected_projection.stdout)

        empty = copy.deepcopy(self.base)
        empty["runtime_extensions"] = {"lsp": {}}
        rejected_manifest, _ = self.generate(empty, name="lsp-empty")
        self.assertNotEqual(rejected_manifest.returncode, 0)
        self.assertIn("mapping must not be empty; omit lsp instead", rejected_manifest.stderr)

    def test_lsp_invalid_server_shapes_are_rejected_by_generator(self) -> None:
        cases: list[tuple[str, object, str]] = [
            ("root", [], "must be false, true, or a mapping"),
            ("server", {"demo": []}, "lsp.demo: must be a mapping"),
            ("empty-server", {"demo": {}}, "must declare command or disabled true"),
            (
                "unknown",
                {"demo": {"disabled": True, "mystery": True}},
                "lsp.demo contains unsupported fields: mystery",
            ),
            ("disabled-type", {"demo": {"disabled": "yes"}}, "disabled: must be a boolean"),
            ("disabled-false", {"demo": {"disabled": False}}, "must declare command or disabled true"),
            (
                "disabled-extra",
                {"demo": {"disabled": True, "env": {}}},
                "disabled-only server must contain only disabled",
            ),
            (
                "missing-extensions",
                {"demo": {"command": ["demo-lsp"]}},
                "extensions: is required when command is declared",
            ),
            (
                "empty-command",
                {"demo": {"command": [], "extensions": ["demo"]}},
                "command: must be a non-empty list of non-empty strings",
            ),
            (
                "blank-command",
                {"demo": {"command": [""], "extensions": ["demo"]}},
                "command: must be a non-empty list of non-empty strings",
            ),
            (
                "command-type",
                {"demo": {"command": [1], "extensions": ["demo"]}},
                "command: must be a non-empty list of non-empty strings",
            ),
            (
                "empty-extensions",
                {"demo": {"command": ["demo-lsp"], "extensions": []}},
                "extensions: must be a non-empty list of non-empty strings",
            ),
            (
                "blank-extension",
                {"demo": {"command": ["demo-lsp"], "extensions": [""]}},
                "extensions: must be a non-empty list of non-empty strings",
            ),
            (
                "extension-type",
                {"demo": {"command": ["demo-lsp"], "extensions": [1]}},
                "extensions: must be a non-empty list of non-empty strings",
            ),
            (
                "env",
                {
                    "demo": {
                        "command": ["demo-lsp"],
                        "extensions": ["demo"],
                        "env": {"MODE": 1},
                    }
                },
                "env must map strings to strings",
            ),
            (
                "initialization",
                {
                    "demo": {
                        "command": ["demo-lsp"],
                        "extensions": ["demo"],
                        "initialization": [],
                    }
                },
                "initialization: must be an object",
            ),
        ]
        for name, lsp, expected in cases:
            with self.subTest(name=name):
                data = copy.deepcopy(self.base)
                data["runtime_extensions"] = {"lsp": lsp}
                created, _ = self.generate(data, name=f"lsp-invalid-{name}")
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)

    def test_validator_rejects_invalid_lsp_manifest_and_projection_drift(self) -> None:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "lsp": {
                "demo": {
                    "command": ["demo-lsp", "--stdio"],
                    "extensions": ["demo"],
                    "env": {"MODE": "strict"},
                }
            }
        }
        created, package = self.generate(data, name="lsp-validator")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime_path = package / "opencode.json"
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        runtime["lsp"]["demo"]["env"]["MODE"] = "mutated"
        runtime_path.write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        drift = self.validate(package)
        self.assertNotEqual(drift.returncode, 0)
        self.assertIn("lsp must match expert.json runtime_extensions.lsp", drift.stdout)

        self.rewrite_manifest(
            package,
            lambda manifest: manifest["runtime_extensions"].update({"lsp": {}}),
        )
        invalid = self.validate(package)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("mapping must not be empty; omit lsp instead", invalid.stdout)

    def test_duplicate_runtime_lists_are_rejected_by_generator_and_validator(self) -> None:
        server = {
            "name": "local-demo",
            "type": "local",
            "command": ["demo-mcp"],
        }
        generator_cases = [
            (
                "npm",
                lambda data: data.update(
                    {
                        "runtime_extensions": {
                            "plugins": {"npm": ["demo-plugin@1.0.0", "demo-plugin@1.0.0"]}
                        }
                    }
                ),
                "PLUGIN_NPM_SPEC_DUPLICATE",
            ),
            (
                "instructions",
                lambda data: data.update(
                    {
                        "runtime_extensions": {
                            "instruction_files": [
                                {
                                    "path": ".opencode/instructions/contract-review-expert/rule.md",
                                    "content": "# Rule\n",
                                }
                            ],
                            "instructions": [
                                ".opencode/instructions/contract-review-expert/*.md",
                                ".opencode/instructions/contract-review-expert/*.md",
                            ],
                        }
                    }
                ),
                "instructions duplicates",
            ),
            (
                "role-mcp",
                lambda data: (
                    data.update({"mcp_servers": [server]}),
                    data["agent"].update({"mcp": ["local-demo", "local-demo"]}),
                ),
                "agent.mcp duplicates local-demo",
            ),
        ]
        for name, mutate, expected in generator_cases:
            with self.subTest(kind="generator", name=name):
                data = copy.deepcopy(self.base)
                mutate(data)
                created, _ = self.generate(data, name=f"duplicate-{name}")
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)

        valid = copy.deepcopy(self.base)
        valid["runtime_extensions"] = {
            "plugins": {"npm": ["demo-plugin@1.0.0"]},
            "instruction_files": [
                {
                    "path": ".opencode/instructions/contract-review-expert/rule.md",
                    "content": "# Rule\n",
                }
            ],
            "instructions": [".opencode/instructions/contract-review-expert/*.md"],
        }
        valid["mcp_servers"] = [server]
        valid["agent"]["mcp"] = ["local-demo"]
        created, package = self.generate(valid, name="duplicate-validator")
        self.assertEqual(created.returncode, 0, created.stderr)
        self.rewrite_manifest(
            package,
            lambda manifest: (
                manifest["runtime_extensions"]["plugins"].update(
                    {"npm": ["demo-plugin@1.0.0", "demo-plugin@1.0.0"]}
                ),
                manifest["runtime_extensions"].update(
                    {
                        "instructions": [
                            ".opencode/instructions/contract-review-expert/*.md",
                            ".opencode/instructions/contract-review-expert/*.md",
                        ]
                    }
                ),
                manifest["agent"].update({"mcp": ["local-demo", "local-demo"]}),
            ),
        )
        rejected = self.validate(package)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("PLUGIN_NPM_SPEC_DUPLICATE", rejected.stdout)
        self.assertIn("runtime_extensions.instructions: duplicates", rejected.stdout)
        self.assertIn("agent.mcp: duplicates local-demo", rejected.stdout)

    def test_runtime_command_agent_and_model_contract(self) -> None:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "commands": [
                {
                    "name": "review-contract",
                    "template": "Review the contract.",
                    "model": "openai/gpt-5",
                }
            ]
        }
        created, package = self.generate(data, name="command-valid")
        self.assertEqual(created.returncode, 0, created.stderr)
        command = read_frontmatter(package / ".opencode/commands/review-contract.md")
        self.assertEqual(command["agent"], "contract-reviewer")
        self.assertIs(command["subtask"], True)
        self.assertEqual(command["model"], "openai/gpt-5")
        generated_manifest = json.loads(
            (package / "expert.json").read_text(encoding="utf-8")
        )
        normalized_command = generated_manifest["runtime_extensions"]["commands"][0]
        self.assertEqual(normalized_command["agent"], "contract-reviewer")
        self.assertIs(normalized_command["subtask"], True)
        self.assertEqual(self.validate(package).returncode, 0)

        invalid_agent = copy.deepcopy(data)
        invalid_agent["runtime_extensions"]["commands"][0]["agent"] = "missing-agent"
        rejected_agent, _ = self.generate(invalid_agent, name="command-agent-invalid")
        self.assertNotEqual(rejected_agent.returncode, 0)
        self.assertIn("references undeclared agent missing-agent", rejected_agent.stderr)

        invalid_subtask = copy.deepcopy(data)
        invalid_subtask["runtime_extensions"]["commands"][0]["subtask"] = False
        rejected_subtask, _ = self.generate(
            invalid_subtask,
            name="command-subtask-invalid",
        )
        self.assertNotEqual(rejected_subtask.returncode, 0)
        self.assertIn("subtask must be true", rejected_subtask.stderr)

        team = copy.deepcopy(self.base)
        source_role = team.pop("agent")
        team["type"] = "team"
        primary = copy.deepcopy(source_role)
        primary.update({"id": "review-lead", "mode": "all"})
        member = copy.deepcopy(source_role)
        member.update({"id": "risk-reviewer", "mode": "subagent"})
        team["primary_agent"] = primary
        team["subagents"] = [member]
        team["runtime_extensions"] = {
            "commands": [
                {
                    "name": "review-risk",
                    "template": "Review risk.",
                    "agent": "risk-reviewer",
                }
            ]
        }
        rejected_member, _ = self.generate(team, name="command-member-invalid")
        self.assertNotEqual(rejected_member.returncode, 0)
        self.assertIn(
            "agent must reference the mode all Agent review-lead",
            rejected_member.stderr,
        )

        for index, model in enumerate(["gpt-5", "/gpt-5", "openai/", " openai/gpt-5", "openai/gpt 5"]):
            with self.subTest(model=model):
                invalid_model = copy.deepcopy(data)
                invalid_model["runtime_extensions"]["commands"][0]["model"] = model
                rejected_model, _ = self.generate(invalid_model, name=f"command-model-{index}")
                self.assertNotEqual(rejected_model.returncode, 0)
                self.assertIn("provider/model string", rejected_model.stderr)

        self.rewrite_manifest(
            package,
            lambda manifest: manifest["runtime_extensions"]["commands"][0].update(
                {"agent": "missing-agent", "model": "gpt-5"}
            ),
        )
        rejected_manifest = self.validate(package)
        self.assertNotEqual(rejected_manifest.returncode, 0)
        self.assertIn("references undeclared agent missing-agent", rejected_manifest.stdout)
        self.assertIn("provider/model string", rejected_manifest.stdout)

    def test_legacy_runtime_command_routing_warns_and_regenerates(self) -> None:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "commands": [
                {
                    "name": "legacy-review",
                    "template": "Review legacy input.",
                    "model": "openai/gpt-5",
                }
            ]
        }
        created, package = self.generate(data, name="legacy-command")
        self.assertEqual(created.returncode, 0, created.stderr)

        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        legacy_command = manifest["runtime_extensions"]["commands"][0]
        legacy_command.pop("agent")
        legacy_command.pop("subtask")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command_path = package / ".opencode/commands/legacy-review.md"
        command_text = command_path.read_text(encoding="utf-8")
        command_path.write_text(
            command_text.replace("agent: contract-reviewer\n", "").replace(
                "subtask: true\n", ""
            ),
            encoding="utf-8",
        )

        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)
        self.assertIn("legacy command routing remains readable", validated.stdout)

        regenerated, regenerated_package = self.generate(
            manifest,
            name="legacy-command-regenerated",
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        routing = read_frontmatter(
            regenerated_package / ".opencode/commands/legacy-review.md"
        )
        self.assertEqual(routing["agent"], "contract-reviewer")
        self.assertIs(routing["subtask"], True)

    def test_previous_false_subtask_remains_readable_but_cannot_regenerate(self) -> None:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "commands": [
                {
                    "name": "legacy-false-subtask",
                    "template": "Review legacy input.",
                }
            ]
        }
        created, package = self.generate(data, name="legacy-false-subtask")
        self.assertEqual(created.returncode, 0, created.stderr)

        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_extensions"]["commands"][0]["subtask"] = False
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command_path = package / ".opencode/commands/legacy-false-subtask.md"
        command_path.write_text(
            command_path.read_text(encoding="utf-8").replace(
                "subtask: true\n",
                "subtask: false\n",
            ),
            encoding="utf-8",
        )

        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)
        self.assertIn("legacy command routing remains readable", validated.stdout)

        rejected, _ = self.generate(manifest, name="legacy-false-regeneration")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("subtask must be true", rejected.stderr)

    def test_agent_title_is_legacy_name_fallback_and_is_not_projected(self) -> None:
        data = copy.deepcopy(self.base)
        source = data.pop("agent")
        data["type"] = "team"
        primary = copy.deepcopy(source)
        primary.update({"id": "review-lead", "mode": "all", "title": "审查负责人"})
        primary.pop("name", None)
        primary.pop("display_name", None)
        primary["permission"] = {}
        member = copy.deepcopy(source)
        member.update({"id": "risk-reviewer", "mode": "subagent", "title": "风险审查员"})
        member.pop("name", None)
        member.pop("display_name", None)
        member["permission"] = {}
        data["primary_agent"] = primary
        data["subagents"] = [member]

        created, package = self.generate(data, name="legacy-title")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        for role_id, expected_name in [
            ("review-lead", "审查负责人"),
            ("risk-reviewer", "风险审查员"),
        ]:
            frontmatter = read_frontmatter(package / f".opencode/agents/{role_id}.md")
            self.assertEqual(frontmatter["name"], role_id)
            self.assertEqual(
                frontmatter["displayName"],
                {"en": expected_name, "zh": expected_name},
            )
            self.assertNotIn("title", frontmatter)
            self.assertNotIn("title", runtime["agent"][role_id])
        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)

    def test_git_reference_repository_is_opaque_and_accepts_loopback_http(self) -> None:
        data = copy.deepcopy(self.base)
        reference = {
            "repository": "http://127.0.0.1:43123/reference.git",
            "branch": "test-fixture",
            "description": "Loopback Git fixture",
            "hidden": True,
        }
        data["runtime_extensions"] = {"references": {"loopback": reference}}
        data["agent"]["references"] = ["loopback"]
        created, package = self.generate(data, name="reference-loopback")
        self.assertEqual(created.returncode, 0, created.stderr)
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(runtime["references"]["contract-review-expert-loopback"], reference)
        validated = self.validate(package)
        self.assertEqual(validated.returncode, 0, validated.stdout)


if __name__ == "__main__":
    unittest.main()
