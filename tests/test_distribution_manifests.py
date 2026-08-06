from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "mobilework-expert-manager"
MARKETPLACE_NAME = "mobilework-tools"
VERSION = "0.6.0"
REPOSITORY = "https://github.com/xiaodong528/mobilework-expert-manager"


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


class DistributionManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.claude_manifest = load_json(ROOT / ".claude-plugin" / "plugin.json")
        cls.claude_marketplace = load_json(
            ROOT / ".claude-plugin" / "marketplace.json"
        )
        cls.codex_manifest = load_json(ROOT / ".codex-plugin" / "plugin.json")
        cls.codex_marketplace = load_json(
            ROOT / ".agents" / "plugins" / "marketplace.json"
        )
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_plugin_identity_and_version_match_across_hosts(self) -> None:
        for manifest in (self.claude_manifest, self.codex_manifest):
            self.assertEqual(PLUGIN_NAME, manifest["name"])
            self.assertEqual(VERSION, manifest["version"])
            self.assertEqual(REPOSITORY, manifest["repository"])
            self.assertEqual("Apache-2.0", manifest["license"])
            self.assertRegex(str(manifest["version"]), r"^\d+\.\d+\.\d+$")

        self.assertEqual("./skills/", self.claude_manifest["skills"])
        self.assertEqual("./skills/", self.codex_manifest["skills"])
        self.assertTrue((ROOT / "skills" / PLUGIN_NAME / "SKILL.md").is_file())

    def test_codex_manifest_declares_only_present_components(self) -> None:
        for unsupported in ("apps", "mcpServers", "hooks"):
            self.assertNotIn(unsupported, self.codex_manifest)

        interface = self.codex_manifest["interface"]
        self.assertIsInstance(interface, dict)
        self.assertEqual("MobileWork Expert Manager", interface["displayName"])
        self.assertEqual("xiaodong528", interface["developerName"])
        self.assertEqual("Developer Tools", interface["category"])
        self.assertEqual(["Interactive", "Read", "Write"], interface["capabilities"])
        self.assertEqual(REPOSITORY, interface["websiteURL"])

        prompts = interface["defaultPrompt"]
        self.assertEqual(3, len(prompts))
        for prompt in prompts:
            self.assertLessEqual(len(prompt), 128)

    def test_claude_marketplace_exposes_repository_root_plugin(self) -> None:
        self.assertEqual(MARKETPLACE_NAME, self.claude_marketplace["name"])
        self.assertEqual("xiaodong528", self.claude_marketplace["owner"]["name"])

        plugins = self.claude_marketplace["plugins"]
        self.assertEqual(1, len(plugins))
        plugin = plugins[0]
        self.assertEqual(PLUGIN_NAME, plugin["name"])
        self.assertEqual(
            {
                "source": "github",
                "repo": "xiaodong528/mobilework-expert-manager",
            },
            plugin["source"],
        )
        self.assertNotIn("version", plugin)

    def test_codex_marketplace_exposes_repository_root_plugin(self) -> None:
        self.assertEqual(MARKETPLACE_NAME, self.codex_marketplace["name"])
        self.assertEqual(
            "MobileWork Tools",
            self.codex_marketplace["interface"]["displayName"],
        )

        plugins = self.codex_marketplace["plugins"]
        self.assertEqual(1, len(plugins))
        plugin = plugins[0]
        self.assertEqual(PLUGIN_NAME, plugin["name"])
        self.assertEqual(
            {
                "source": "url",
                "url": f"{REPOSITORY}.git",
            },
            plugin["source"],
        )
        self.assertEqual(
            {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            plugin["policy"],
        )
        self.assertEqual("Developer Tools", plugin["category"])

    def test_readme_documents_current_install_contract(self) -> None:
        for expected in (
            "| Marketplace | `mobilework-tools` |",
            "| 当前版本 | `0.6.0` |",
            "claude plugin marketplace add xiaodong528/mobilework-expert-manager",
            "claude plugin install mobilework-expert-manager@mobilework-tools",
            "claude plugin marketplace update mobilework-tools",
            "claude plugin update mobilework-expert-manager@mobilework-tools",
            "codex plugin marketplace add xiaodong528/mobilework-expert-manager",
            "codex plugin add mobilework-expert-manager@mobilework-tools",
            "codex plugin marketplace upgrade mobilework-tools",
            "codex plugin remove mobilework-expert-manager@mobilework-tools",
            "/mobilework-expert-manager:mobilework-expert-manager",
            "$mobilework-expert-manager:mobilework-expert-manager",
            "不是 OpenAI 或 Anthropic 官方市场",
        ):
            self.assertIn(expected, self.readme)

        self.assertNotIn("0.5.0", self.readme)


if __name__ == "__main__":
    unittest.main()
