from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import plugin_contract


class PluginContractTests(unittest.TestCase):
    def test_bare_and_explicit_latest_share_a_canonical_key(self) -> None:
        bare = plugin_contract.parse_npm_plugin_spec("opencode-wakatime")
        latest = plugin_contract.parse_npm_plugin_spec("opencode-wakatime@latest")

        self.assertEqual(bare["name"], "opencode-wakatime")
        self.assertEqual(bare["selector"], "latest")
        self.assertEqual(bare["normalized"], "opencode-wakatime")
        self.assertEqual(bare["canonicalKey"], latest["canonicalKey"])
        self.assertFalse(bare["isPinned"])
        self.assertEqual(bare["category"], plugin_contract.CATEGORY_UNPINNED_LEGACY)
        self.assertFalse(latest["isPinned"])

    def test_scoped_and_exact_registry_specs_are_clean(self) -> None:
        cases = {
            "demo-plugin@1.2.3": ("demo-plugin", "1.2.3"),
            "@mobilework/demo-plugin@1.2.3-beta.1+build.7": (
                "@mobilework/demo-plugin",
                "1.2.3-beta.1+build.7",
            ),
        }
        for raw, (name, selector) in cases.items():
            with self.subTest(raw=raw):
                parsed = plugin_contract.parse_npm_plugin_spec(raw)
                self.assertEqual(parsed["name"], name)
                self.assertEqual(parsed["selector"], selector)
                self.assertEqual(parsed["canonicalKey"], f"{name}@{selector}")
                self.assertEqual(parsed["normalized"], raw)
                self.assertTrue(parsed["isPinned"])
                self.assertEqual(parsed["category"], plugin_contract.CATEGORY_CLEAN)

    def test_ranges_and_dist_tags_remain_unpinned_legacy_specs(self) -> None:
        cases = {
            "demo-plugin@^1.2.3": "^1.2.3",
            "demo-plugin@>=1.2.0   <2.0.0": ">=1.2.0 <2.0.0",
            "demo-plugin@1.2.x": "1.2.x",
            "demo-plugin@1.2.3 - 2.0.0": "1.2.3 - 2.0.0",
            "demo-plugin@1 || 2": "1 || 2",
            "demo-plugin@next": "next",
            "@mobilework/demo-plugin@beta": "beta",
        }
        for raw, selector in cases.items():
            with self.subTest(raw=raw):
                parsed = plugin_contract.parse_npm_plugin_spec(raw)
                self.assertEqual(parsed["selector"], selector)
                self.assertFalse(parsed["isPinned"])
                self.assertEqual(
                    parsed["category"],
                    plugin_contract.CATEGORY_UNPINNED_LEGACY,
                )

    def test_rejects_non_registry_sources_and_paths(self) -> None:
        cases = [
            "file:../plugin",
            "link:../plugin",
            "workspace:*",
            "./plugin",
            "../plugin",
            "/tmp/plugin",
            "~/plugin",
            r"C:\plugins\demo",
            "C:/plugins/demo",
            r"\\server\share\plugin",
            "https://example.com/plugin.tgz",
            "http://example.com/plugin.tgz",
            "git+https://example.com/plugin.git",
            "git+ssh://git@example.com/plugin.git",
            "git://example.com/plugin.git",
            "git@example.com:owner/plugin.git",
            "github:owner/plugin",
            "owner/plugin",
            "demo-plugin@npm:replacement-plugin@1.0.0",
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(plugin_contract.PluginContractError):
                plugin_contract.parse_npm_plugin_spec(raw)

    def test_rejects_queries_fragments_credentials_and_percent_encoding(self) -> None:
        canary = "canary-password-123"
        cases = [
            "demo-plugin?token=secret",
            "demo-plugin#main",
            f"https://user:{canary}@example.com/plugin.tgz",
            "demo-plugin%ZZ",
            "%40mobilework%2Fdemo-plugin",
            "demo-plugin@1.2.3%2Fescape",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(plugin_contract.PluginContractError) as raised:
                    plugin_contract.parse_npm_plugin_spec(raw)
                self.assertNotIn(canary, str(raised.exception))
                self.assertEqual(raised.exception.code, plugin_contract.ERROR_CODE)

    def test_rejects_invalid_names_selectors_and_control_characters(self) -> None:
        cases = [
            "",
            " Demo-plugin",
            "Demo-plugin",
            "_demo-plugin",
            "@scope",
            "@scope/",
            "@scope/demo/extra",
            "demo-plugin@",
            "demo-plugin@1.x.3",
            "demo-plugin@1.2.3-beta.01",
            "demo-plugin@^1.2.3-beta.01",
            "demo-plugin@1.2.3@next",
            "demo-plugin@>=1 && <2",
            "demo-plugin\n@1.2.3",
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(plugin_contract.PluginContractError):
                plugin_contract.parse_npm_plugin_spec(raw)


if __name__ == "__main__":
    unittest.main()
