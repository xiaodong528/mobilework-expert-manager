from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
INSTALL = SCRIPTS / "install_expert.py"
sys.path.insert(0, str(SCRIPTS))

from generator_test_support import managed_generator_env
from spec_templates import load_spec_text
import validate_expert


class RuntimeExtensionsMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads(load_spec_text("legacy-expert-json"))
        self.base["runtime_extensions"] = {}
        self.base.pop("mcp_servers", None)
        self.reference_host = self.root / "reference-host.json"
        self.reference_host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def generate(
        self,
        name: str,
        *,
        runtime_extensions: dict[str, object] | None = None,
        mcp_servers: list[dict[str, object]] | None = None,
        role_custom_tools: list[str] | None = None,
    ) -> Path:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = runtime_extensions or {}
        references = data["runtime_extensions"].get("references", {})
        data["agent"]["references"] = list(references) if isinstance(references, dict) else []
        role_instructions = data["runtime_extensions"].get("role_instructions", {})
        if isinstance(role_instructions, dict) and role_instructions:
            data["agent"]["instructions"] = list(role_instructions)
        else:
            data["agent"].pop("instructions", None)
        if mcp_servers is not None:
            data["mcp_servers"] = mcp_servers
        if role_custom_tools is not None:
            data["agent"]["custom_tools"] = role_custom_tools
        source = self.root / name
        source.mkdir()
        manifest = source / "expert.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output = source / "out"
        result = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output / data["slug"]

    def test_each_optional_extension_is_independent_and_omits_empty_sections(self) -> None:
        cases: list[tuple[str, dict[str, object], list[dict[str, object]] | None, str | None, str | None]] = [
            (
                "commands",
                {"commands": [{"name": "review-scope", "template": "Review scope."}]},
                None,
                ".opencode/commands/review-scope.md",
                None,
            ),
            (
                "custom-tools",
                {
                    "custom_tools": [
                        {
                            "path": "score.ts",
                            "purpose": "按已确认规则计算确定性分数。",
                            "content": "export default {}\n",
                        }
                    ]
                },
                None,
                ".opencode/tools/score.ts",
                None,
            ),
            (
                "npm-plugin",
                {"plugins": {"npm": ["opencode-example-plugin@1.0.0"]}},
                None,
                None,
                "plugin",
            ),
            (
                "local-plugin",
                {"plugins": {"local": [{"path": "notify.ts", "content": "export const plugin = {}\n"}]}},
                None,
                ".opencode/plugins/notify.ts",
                None,
            ),
            (
                "plugin-package",
                {"plugins": {"package_json": {"dependencies": {"shescape": "^2.1.0"}}}},
                None,
                ".opencode/package.json",
                None,
            ),
            (
                "references",
                {
                    "reference_files": [
                        {
                            "path": ".opencode/references/contract-review-expert/playbook/overview.md",
                            "content": "# Playbook\n",
                        }
                    ],
                    "references": {
                        "playbook": {
                            "path": ".opencode/references/contract-review-expert/playbook",
                            "description": "Review playbook",
                            "hidden": True,
                        }
                    },
                },
                None,
                ".opencode/references/contract-review-expert/playbook/overview.md",
                "references",
            ),
            (
                "git-reference",
                {
                    "references": {
                        "upstream": {
                            "repository": "https://example.com/reference.git",
                            "branch": "stable",
                            "description": "Upstream playbook",
                            "hidden": False,
                        }
                    }
                },
                None,
                None,
                "references",
            ),
            (
                "instructions",
                {
                    "instruction_files": [
                        {
                            "path": ".opencode/instructions/contract-review-expert/evidence.md",
                            "content": "# Evidence\n",
                        }
                    ],
                    "instructions": [".opencode/instructions/contract-review-expert/*.md"],
                },
                None,
                ".opencode/instructions/contract-review-expert/evidence.md",
                "instructions",
            ),
            (
                "lsp",
                {
                    "lsp": {
                        "contract-lsp": {
                            "command": ["contract-lsp", "--stdio"],
                            "extensions": [".contract"],
                        }
                    }
                },
                None,
                None,
                "lsp",
            ),
            (
                "mcp-env",
                {},
                [
                    {
                        "name": "secure-docs",
                        "type": "remote",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
                        "enabled": False,
                        "timeout": 10000,
                        "oauth": False,
                    }
                ],
                ".env.example",
                "mcp",
            ),
        ]

        optional_roots = {
            ".opencode/commands",
            ".opencode/tools",
            ".opencode/plugins",
            ".opencode/package.json",
            ".opencode/references",
            ".opencode/instructions",
            ".env.example",
        }
        optional_config = {"plugin", "references", "instructions", "lsp", "mcp"}

        for name, runtime_extensions, mcp_servers, expected_file, expected_config in cases:
            with self.subTest(name=name):
                package = self.generate(
                    name,
                    runtime_extensions=runtime_extensions,
                    mcp_servers=mcp_servers,
                )
                config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
                for relative in optional_roots:
                    should_exist = expected_file is not None and (
                        relative == expected_file or expected_file.startswith(relative.rstrip("/") + "/")
                    )
                    self.assertEqual((package / relative).exists(), should_exist, relative)
                for key in optional_config:
                    self.assertEqual(key in config, key == expected_config, key)
                if name == "references":
                    self.assertEqual(
                        config["references"],
                        {
                            "contract-review-expert-playbook": {
                                "path": ".opencode/references/contract-review-expert/playbook",
                                "description": "Review playbook",
                                "hidden": True,
                            }
                        },
                    )
                if name == "git-reference":
                    self.assertEqual(
                        config["references"],
                        {
                            "contract-review-expert-upstream": {
                                "repository": "https://example.com/reference.git",
                                "branch": "stable",
                                "description": "Upstream playbook",
                                "hidden": False,
                            }
                        },
                    )
                if name == "mcp-env":
                    self.assertEqual(
                        (package / ".env.example").read_text(encoding="utf-8"),
                        "API_TOKEN=<required>\n",
                    )
                if name == "custom-tools":
                    readme = (package / "README.md").read_text(encoding="utf-8")
                    self.assertIn("按已确认规则计算确定性分数", readme)
                    self.assertIn("使用角色 未分配", readme)

    def test_owned_namespaced_custom_tool_is_role_visible_and_allowed(self) -> None:
        path = "contract-review-expert-score.ts"
        package = self.generate(
            "owned-custom-tool",
            runtime_extensions={
                "custom_tools": [
                    {
                        "path": path,
                        "purpose": "按已确认条款规则计算确定性分数。",
                        "content": (
                            'import { tool } from "@opencode-ai/plugin"\n'
                            "export default tool({ description: \"Score confirmed clauses\", "
                            "args: {}, async execute() { return \"ok\" } })\n"
                        ),
                    }
                ]
            },
            role_custom_tools=[path],
        )
        runtime = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        permission = runtime["agent"]["contract-reviewer"]["permission"]
        self.assertEqual(permission["contract-review-expert-score"], "allow")
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "`contract-review-expert-score`"
            "（`.opencode/tools/contract-review-expert-score.ts`）",
            agent,
        )
        self.assertIn("按已确认条款规则计算确定性分数", agent)
        self.assertNotIn("Score confirmed clauses", agent)
        self.assertNotIn("这些工具只由当前角色", agent)
        self.assertIn("主动调用", agent)
        self.assertNotIn("自动触发", agent.split("主动调用的 Custom Tool", 1)[0])
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("按已确认条款规则计算确定性分数", readme)
        self.assertNotIn("Score confirmed clauses", readme)
        self.assertIn("使用角色 `contract-reviewer`", readme)

    def test_custom_tool_purpose_is_required_for_generation_and_legacy_read_warns(self) -> None:
        legacy_package = self.generate(
            "legacy-tool-purpose",
            runtime_extensions={
                "custom_tools": [
                    {
                        "path": "contract-review-expert-score.ts",
                        "purpose": "按已确认条款规则计算确定性分数。",
                        "content": "export default {}\n",
                    }
                ]
            },
            role_custom_tools=["contract-review-expert-score.ts"],
        )
        legacy_manifest_path = legacy_package / "expert.json"
        legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding="utf-8"))
        legacy_manifest["runtime_extensions"]["custom_tools"][0].pop("purpose")
        legacy_manifest_path.write_text(
            json.dumps(legacy_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        legacy_result = validate_expert.validate_package(legacy_package)
        self.assertTrue(legacy_result.ok)
        self.assertIn(
            "LEGACY_CUSTOM_TOOL_PURPOSE_MISSING",
            [finding.code for finding in legacy_result.findings],
        )

        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "custom_tools": [
                {"path": "contract-review-expert-score.ts", "content": "export default {}\n"}
            ]
        }
        data["agent"]["references"] = []
        data["agent"].pop("instructions", None)
        data["agent"]["custom_tools"] = ["contract-review-expert-score.ts"]
        source = self.root / "missing-tool-purpose"
        source.mkdir()
        manifest = source / "expert.json"
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = source / "out"
        completed = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("custom_tools[0].purpose must be non-empty", completed.stderr)
        self.assertFalse((output / data["slug"]).exists())

    def test_namespaced_local_plugin_is_package_wide_not_role_owned(self) -> None:
        package = self.generate(
            "package-wide-plugin",
            runtime_extensions={
                "plugins": {
                    "local": [
                        {
                            "path": "contract-review-expert-before-tool.ts",
                            "content": (
                                "export const BeforeTool = async () => "
                                "({ 'tool.execute.before': async () => {} })\n"
                            ),
                        }
                    ]
                }
            },
        )
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("contract-review-expert-before-tool", agent)
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("Plugin 是 package-wide 运行行为", readme)
        self.assertIn("contract-review-expert-before-tool.ts", readme)
        self.assertIn("供满足 capability contract 的目标 Runtime 发现并加载", readme)
        self.assertIn("静态校验不证明 Plugin 已被真实 Runtime 加载", readme)
        self.assertIn("`not-tested`", readme)
        self.assertNotIn("会由 Runtime 加载", readme)

    def test_full_extension_package_installs_every_projection(self) -> None:
        runtime_extensions: dict[str, object] = {
            "commands": [{"name": "review-scope", "template": "Review scope."}],
            "custom_tools": [
                {
                    "path": "score.ts",
                    "purpose": "按已确认规则计算确定性分数。",
                    "content": "export default {}\n",
                }
            ],
            "plugins": {
                "npm": ["opencode-example-plugin@1.0.0"],
                "local": [{"path": "notify.ts", "content": "export const plugin = {}\n"}],
                "package_json": {"dependencies": {"shescape": "^2.1.0"}},
            },
            "reference_files": [
                {
                    "path": ".opencode/references/contract-review-expert/playbook/overview.md",
                    "content": "# Playbook\n",
                }
            ],
            "references": {
                "playbook": {
                    "path": ".opencode/references/contract-review-expert/playbook",
                    "description": "Review playbook",
                    "hidden": True,
                },
                "upstream": {
                    "repository": "https://example.com/reference.git",
                    "branch": "stable",
                    "description": "Upstream playbook",
                    "hidden": False,
                },
            },
            "instruction_files": [
                {
                    "path": ".opencode/instructions/contract-review-expert/evidence.md",
                    "content": "# Evidence\n",
                }
            ],
            "instructions": [".opencode/instructions/contract-review-expert/*.md"],
            "lsp": {
                "contract-lsp": {
                    "command": ["contract-lsp", "--stdio"],
                    "extensions": [".contract"],
                }
            },
        }
        mcp_servers: list[dict[str, object]] = [
            {
                "name": "local-canary",
                "type": "local",
                "command": ["node", "./mocks/local-mcp.mjs"],
                "environment": {"CANARY_NONCE": "{env:MCP_CANARY_NONCE}"},
                "enabled": False,
                "timeout": 5000,
            },
            {
                "name": "secure-docs",
                "type": "remote",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
                "enabled": False,
                "timeout": 10000,
                "oauth": False,
            },
            {
                "name": "oauth-docs",
                "type": "remote",
                "url": "https://example.com/oauth-mcp",
                "enabled": False,
                "timeout": 15000,
                "oauth": {
                    "clientId": "{env:OAUTH_CLIENT_ID}",
                    "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
                    "scope": "tools.read tools.call",
                    "callbackPort": 18765,
                },
            }
        ]
        package = self.generate(
            "full",
            runtime_extensions=runtime_extensions,
            mcp_servers=mcp_servers,
        )
        package_config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(package_config),
            {"$schema", "agent", "mcp", "plugin", "references", "instructions", "lsp"},
        )
        self.assertEqual(package_config["plugin"], ["opencode-example-plugin@1.0.0"])
        self.assertEqual(package_config["mcp"], {
            "local-canary": {
                "type": "local",
                "enabled": False,
                "timeout": 5000,
                "command": ["node", "./mocks/local-mcp.mjs"],
                "environment": {"CANARY_NONCE": "{env:MCP_CANARY_NONCE}"},
            },
            "secure-docs": {
                "type": "remote",
                "enabled": False,
                "timeout": 10000,
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer {env:API_TOKEN}"},
                "oauth": False,
            },
            "oauth-docs": {
                "type": "remote",
                "enabled": False,
                "timeout": 15000,
                "url": "https://example.com/oauth-mcp",
                "oauth": {
                    "clientId": "{env:OAUTH_CLIENT_ID}",
                    "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
                    "scope": "tools.read tools.call",
                    "callbackPort": 18765,
                },
            },
        })
        self.assertNotIn("tools", package_config)
        self.assertNotIn(".opencode/plugins/notify.ts", package_config["plugin"])
        self.assertEqual(package_config["references"], {
            "contract-review-expert-playbook": {
                "path": ".opencode/references/contract-review-expert/playbook",
                "description": "Review playbook",
                "hidden": True,
            },
            "contract-review-expert-upstream": {
                "repository": "https://example.com/reference.git",
                "branch": "stable",
                "description": "Upstream playbook",
                "hidden": False,
            },
        })
        self.assertFalse((package / "AGENTS.md").exists())
        for agent_config in package_config["agent"].values():
            self.assertEqual(
                set(agent_config),
                {"mode", "description", "steps", "permission"},
            )

        agent_markdown = (
            package / ".opencode/agents/contract-reviewer.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("`score`（`.opencode/tools/score.ts`）", agent_markdown)
        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("Plugin 是 package-wide 运行行为", readme)
        workspace = self.root / "workspace"
        workspace.mkdir()
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(package),
                "--workspace-dir",
                str(workspace),
                "--host-contract",
                str(self.reference_host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)

        runtime = workspace / ".opencode"
        expected_files = {
            "commands/review-scope.md",
            "tools/score.ts",
            "plugins/notify.ts",
            "references/contract-review-expert/playbook/overview.md",
            "instructions/contract-review-expert/evidence.md",
            "package.json",
        }
        for relative in expected_files:
            self.assertTrue((runtime / relative).is_file(), relative)

        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        for key in {"agent", "mcp", "plugin", "references", "instructions", "lsp"}:
            self.assertIn(key, config)
        self.assertEqual(config["instructions"], [
            ".opencode/instructions/contract-review-expert/*.md",
        ])
        self.assertEqual(config["references"], {
            "contract-review-expert-playbook": {
                "path": "references/contract-review-expert/playbook",
                "description": "Review playbook",
                "hidden": True,
            },
            "contract-review-expert-upstream": {
                "repository": "https://example.com/reference.git",
                "branch": "stable",
                "description": "Upstream playbook",
                "hidden": False,
            },
        })
        receipt = json.loads(
            (runtime / ".expert-installs/contract-review-expert.json").read_text(encoding="utf-8")
        )
        for relative in expected_files - {"package.json"}:
            self.assertIn(relative, receipt["files"])
        self.assertEqual(
            receipt["dependencies"],
            {
                "dependencies": {"shescape": "^2.1.0"},
                "devDependencies": {},
            },
        )
        self.assertEqual(receipt["config_values"]["references"], config["references"])

    def test_multiple_workflow_commands_preserve_dynamic_and_multimodal_input_contract(self) -> None:
        self.base["workflows"] = [
            {
                "name": "范围审查",
                "trigger": "用户需要检查范围和验收风险时触发。",
                "phases": [
                    {
                        "name": "执行审查",
                        "mode": "primary",
                        "agents": [],
                        "input": "用户要求和附件",
                        "expected_output": "范围与验收风险清单",
                        "acceptance": ["结论引用输入证据"],
                    }
                ],
            },
            {
                "name": "修改建议",
                "trigger": "用户需要修改方案时触发。",
                "phases": [
                    {
                        "name": "形成建议",
                        "mode": "primary",
                        "agents": [],
                        "input": "风险清单和用户目标",
                        "expected_output": "可执行修改建议",
                        "acceptance": ["建议对应已识别风险"],
                    }
                ],
            },
        ]
        runtime_extensions: dict[str, object] = {
            "commands": [
                {
                    "name": "review-scope",
                    "description": "执行范围审查 workflow",
                    "agent": "contract-reviewer",
                    "template": (
                        "执行“范围审查”workflow。\n"
                        "用户要求：$ARGUMENTS\n"
                        "结合本次调用中可访问的图片、PDF 或其他附件。"
                    ),
                },
                {
                    "name": "draft-revisions",
                    "description": "执行修改建议 workflow",
                    "agent": "contract-reviewer",
                    "template": "执行“修改建议”workflow。\n用户要求：$ARGUMENTS",
                },
            ]
        }

        package = self.generate("workflow-commands", runtime_extensions=runtime_extensions)
        config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("command", config)

        review_command = (package / ".opencode/commands/review-scope.md").read_text(
            encoding="utf-8"
        )
        revision_command = (package / ".opencode/commands/draft-revisions.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("description: 执行范围审查 workflow", review_command)
        self.assertIn("agent: contract-reviewer", review_command)
        self.assertIn("subtask: true", review_command)
        self.assertIn("用户要求：$ARGUMENTS", review_command)
        self.assertIn("图片、PDF 或其他附件", review_command)
        self.assertIn("执行“修改建议”workflow", revision_command)
        self.assertIn("agent: contract-reviewer", revision_command)
        self.assertIn("subtask: true", revision_command)
        self.assertIn("用户要求：$ARGUMENTS", revision_command)

        readme = (package / "README.md").read_text(encoding="utf-8")
        self.assertIn("`/review-scope`：执行范围审查 workflow", readme)
        self.assertIn("`/draft-revisions`：执行修改建议 workflow", readme)

    def test_duplicate_command_names_are_rejected(self) -> None:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {
            "commands": [
                {"name": "review-scope", "template": "Review scope."},
                {"name": "review-scope", "template": "Review scope again."},
            ]
        }
        data["agent"]["references"] = []
        data["agent"].pop("instructions", None)
        source = self.root / "duplicate-commands"
        source.mkdir()
        manifest = source / "expert.json"
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(source / "out"),
            ],
            env=managed_generator_env(source / "out"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicates review-scope", result.stderr)


if __name__ == "__main__":
    unittest.main()
