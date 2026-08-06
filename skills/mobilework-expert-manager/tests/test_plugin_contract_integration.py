from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"
INSTALL = SCRIPTS / "install_expert.py"

sys.path.insert(0, str(SCRIPTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text
import plugin_contract
import validate_expert


class PluginContractIntegrationTests(unittest.TestCase):
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
        npm_specs: list[str],
        *,
        name: str,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        data = copy.deepcopy(self.base)
        data["runtime_extensions"] = {"plugins": {"npm": npm_specs}}
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

    def validate_json(self, package: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [sys.executable, str(VALIDATE), str(package), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        return result, json.loads(result.stdout)

    def rewrite_plugin_specs(
        self,
        package: Path,
        *,
        manifest_specs: list[str],
        projected_specs: list[str],
    ) -> None:
        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runtime_extensions"]["plugins"]["npm"] = manifest_specs
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        config_path = package / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["plugin"] = projected_specs
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_generator_projects_only_exact_semver_specs(self) -> None:
        exact = "@mobilework/demo-plugin@1.2.3-beta.1+build.7"
        generated, package = self.generate([exact], name="exact")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
        config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime_extensions"]["plugins"]["npm"], [exact])
        self.assertEqual(config["plugin"], [exact])

        for index, spec in enumerate(
            ["demo-plugin", "demo-plugin@^1.2.3", "demo-plugin@next"]
        ):
            with self.subTest(spec=spec):
                rejected, _ = self.generate([spec], name=f"unpinned-{index}")
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("PLUGIN_NPM_SPEC_UNPINNED", rejected.stderr)

    def test_generator_rejects_invalid_and_canonical_duplicates(self) -> None:
        for index, spec in enumerate(
            ["file:../plugin", "demo-plugin@1.2.3-beta.01"]
        ):
            with self.subTest(spec=spec):
                rejected, _ = self.generate([spec], name=f"invalid-{index}")
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn("PLUGIN_NPM_SPEC_INVALID", rejected.stderr)

        duplicate, _ = self.generate(
            ["demo-plugin", "demo-plugin@latest"],
            name="canonical-duplicate",
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("PLUGIN_NPM_SPEC_DUPLICATE", duplicate.stderr)

    def test_validator_accepts_legacy_specs_with_stable_warning_and_projection(self) -> None:
        cases = [
            ("demo-plugin", "demo-plugin"),
            ("demo-plugin@>=1.2.0   <2.0.0", "demo-plugin@>=1.2.0   <2.0.0"),
            ("demo-plugin@next", "demo-plugin@next"),
        ]
        for index, (manifest_spec, projected_spec) in enumerate(cases):
            with self.subTest(manifest_spec=manifest_spec):
                generated, package = self.generate(
                    ["demo-plugin@1.2.3"],
                    name=f"legacy-{index}",
                )
                self.assertEqual(generated.returncode, 0, generated.stderr)
                self.rewrite_plugin_specs(
                    package,
                    manifest_specs=[manifest_spec],
                    projected_specs=[projected_spec],
                )
                validated, payload = self.validate_json(package)
                self.assertEqual(validated.returncode, 0, validated.stderr)
                findings = payload["findings"]
                self.assertIn(
                    "PLUGIN_NPM_SPEC_UNPINNED",
                    {finding["code"] for finding in findings},
                )
                self.assertEqual(
                    sum(
                        finding["code"] == "PLUGIN_NPM_SPEC_UNPINNED"
                        for finding in findings
                    ),
                    1,
                )
                self.assertNotIn(
                    "plugin must match expert.json",
                    "\n".join(finding["message"] for finding in findings),
                )

    def test_legacy_raw_range_validates_and_installs_without_rewriting(self) -> None:
        raw_range = "demo-plugin@>=1.2.0   <2.0.0"
        generated, package = self.generate(
            ["demo-plugin@1.2.3"],
            name="legacy-raw-range-install",
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.rewrite_plugin_specs(
            package,
            manifest_specs=[raw_range],
            projected_specs=[raw_range],
        )

        validated, payload = self.validate_json(package)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertNotIn(
            "plugin must match expert.json",
            "\n".join(finding["message"] for finding in payload["findings"]),
        )

        workspace = self.root / "legacy-raw-range-workspace"
        workspace.mkdir()
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(package),
                "--workspace-dir",
                str(workspace),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        runtime_config = json.loads(
            (workspace / ".opencode/opencode.jsonc").read_text(encoding="utf-8")
        )
        self.assertEqual(runtime_config["plugin"], [raw_range])

    def test_validation_parses_manifest_and_config_plugin_once_each(self) -> None:
        generated, package = self.generate(
            ["demo-plugin@1.2.3"],
            name="single-parse",
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        original_parse = plugin_contract.parse_npm_plugin_spec

        with mock.patch.object(
            plugin_contract,
            "parse_npm_plugin_spec",
            wraps=original_parse,
        ) as parse:
            result = validate_expert.validate_package(package)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(parse.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in parse.call_args_list],
            ["demo-plugin@1.2.3", "demo-plugin@1.2.3"],
        )

    def test_validator_reports_invalid_and_canonical_duplicate_codes(self) -> None:
        generated, package = self.generate(["demo-plugin@1.2.3"], name="invalid-validator")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.rewrite_plugin_specs(
            package,
            manifest_specs=["file:../private-plugin"],
            projected_specs=["file:../private-plugin"],
        )
        invalid, payload = self.validate_json(package)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn(
            "PLUGIN_NPM_SPEC_INVALID",
            {finding["code"] for finding in payload["findings"]},
        )
        self.assertNotIn("file:../private-plugin", invalid.stdout)

        generated, package = self.generate(["demo-plugin@1.2.3"], name="duplicate-validator")
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.rewrite_plugin_specs(
            package,
            manifest_specs=["demo-plugin", "demo-plugin@latest"],
            projected_specs=["demo-plugin", "demo-plugin@latest"],
        )
        duplicate, payload = self.validate_json(package)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn(
            "PLUGIN_NPM_SPEC_DUPLICATE",
            {finding["code"] for finding in payload["findings"]},
        )


if __name__ == "__main__":
    unittest.main()
