from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
INSTALL = SCRIPTS / "install_expert.py"

sys.path.insert(0, str(SCRIPTS))
import package_contract as contract
import diagnose_skill
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


def load_installer_module():
    spec = importlib.util.spec_from_file_location("mobilework_install_expert", INSTALL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load installer: {INSTALL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load_installer_module()


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.reference_host = self.root / "host-references.json"
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
        self.no_reference_host = self.root / "host-no-references.json"
        self.no_reference_host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": False},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, *, slug: str = "contract-review-expert", agent_id: str = "contract-reviewer") -> Path:
        data = json.loads(load_spec_text("legacy-expert-json"))
        old_slug = data["slug"]
        old_agent = data["agent"]["id"]
        data["slug"] = slug
        data["name"] = f"{slug} 专家"
        data["agent"]["id"] = agent_id
        data["agent"]["name"] = data["name"]
        data["agent"]["display_name"] = data["name"]
        data["avatar_url"] = f"avatars/{slug}.png"
        data["agent"]["avatar_url"] = f"avatars/{agent_id}.png"
        data["common_skills"] = [{"purpose": "delivery-quality"}]
        data["agent"]["skills"] = [
            {"purpose": "role-guidelines"},
            {"purpose": "checklist"},
        ]
        data["agent"]["permission"].pop("skill", None)
        ext = data["runtime_extensions"]
        ext["reference_files"][0]["path"] = f".opencode/references/{slug}/playbook/overview.md"
        ext["references"]["playbook"]["path"] = f".opencode/references/{slug}/playbook"
        ext["instruction_files"][0]["path"] = f".opencode/instructions/{slug}/evidence.md"
        ext["instruction_files"][1]["path"] = (
            f".opencode/instructions/{slug}/roles/source-policy.md"
        )
        ext["role_instructions"]["source-policy"]["path"] = (
            f".opencode/instructions/{slug}/roles/source-policy.md"
        )
        ext["instructions"] = [f".opencode/instructions/{slug}/*.md"]
        manifest = self.root / f"{slug}.source" / "expert.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertNotEqual(old_slug, "")
        self.assertNotEqual(old_agent, "")
        return manifest

    def generate(self, *, slug: str = "contract-review-expert", agent_id: str = "contract-reviewer") -> Path:
        manifest = self.write_manifest(slug=slug, agent_id=agent_id)
        output = self.root / "packages"
        result = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(output)],
            env=managed_generator_env(output),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output / slug

    def run_install(
        self,
        package: Path,
        *,
        force: bool = False,
        reference_capability: bool | None = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(INSTALL),
            "--package-dir",
            str(package),
            "--workspace-dir",
            str(self.workspace),
        ]
        if reference_capability is not None:
            command.extend(
                [
                    "--host-contract",
                    str(self.reference_host if reference_capability else self.no_reference_host),
                ]
            )
        if force:
            command.append("--force")
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def test_copy_sources_keeps_package_files_streamable(self) -> None:
        runtime = self.root / ".opencode"
        reference = runtime / "references/example/material/package.json"
        reference.parent.mkdir(parents=True)
        reference.write_text('{"kind":"reference"}\n', encoding="utf-8")
        (runtime / "package.json").write_text('{"dependencies":{}}\n', encoding="utf-8")

        with patch.object(Path, "read_bytes", side_effect=AssertionError("must stream")):
            sources = INSTALLER.copy_sources(runtime)

        self.assertEqual(
            sources,
            {"references/example/material/package.json": reference},
        )

    def test_installs_into_opencode_and_rebases_paths(self) -> None:
        package = self.generate()
        result = self.run_install(package)
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = self.workspace / ".opencode"
        self.assertFalse((self.workspace / ".mobilework-engine").exists())
        self.assertTrue((runtime / "references/contract-review-expert/playbook/overview.md").is_file())
        self.assertTrue((runtime / "instructions/contract-review-expert/evidence.md").is_file())
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(
            config["references"],
            {
                "contract-review-expert-playbook": {
                    "path": "references/contract-review-expert/playbook",
                    "description": "Use for clause-level contract review guidance",
                }
            },
        )
        self.assertEqual(
            config["instructions"],
            [".opencode/instructions/contract-review-expert/*.md"],
        )
        receipt = json.loads(
            (runtime / ".expert-installs/contract-review-expert.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["contract"], 2)
        self.assertIn("agents/contract-reviewer.md", receipt["files"])
        self.assertEqual(receipt["config_values"]["references"], config["references"])
        self.assertEqual(
            receipt["bindings"]["references"],
            {"contract-review-expert-playbook": ["contract-reviewer"]},
        )

    def test_local_reference_uses_role_fallback_when_capability_is_unavailable(self) -> None:
        package = self.generate()
        installed = self.run_install(package, reference_capability=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        payload = json.loads(installed.stdout)
        fallback = "contract-review-expert-reference-playbook"
        self.assertEqual(payload["references"], [])
        self.assertEqual(payload["reference_fallbacks"], [fallback])

        runtime = self.workspace / ".opencode"
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertNotIn("references", config)
        self.assertEqual(
            config["agent"]["contract-reviewer"]["permission"]["skill"][fallback],
            "allow",
        )
        fallback_skill = runtime / f"skills/{fallback}/SKILL.md"
        self.assertTrue(fallback_skill.is_file())
        self.assertIn(
            "`.opencode/references/contract-review-expert/playbook`",
            fallback_skill.read_text(encoding="utf-8"),
        )
        diagnosis = diagnose_skill.diagnose(fallback_skill.parent)
        self.assertTrue(diagnosis.ok, diagnosis.as_dict())
        self.assertEqual(diagnosis.as_dict()["evidenceLevel"], "valid")
        receipt = json.loads(
            (runtime / ".expert-installs/contract-review-expert.json").read_text(encoding="utf-8")
        )
        self.assertIn(f"skills/{fallback}/SKILL.md", receipt["files"])
        self.assertEqual(
            receipt["bindings"]["references"],
            {"contract-review-expert-playbook": ["contract-reviewer"]},
        )

    def test_local_reference_fallback_keeps_long_usage_guidance_out_of_frontmatter(self) -> None:
        manifest = self.write_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"]["references"]["playbook"]["description"] = "x" * 1024
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "long-description-packages"
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
        self.assertEqual(generated.returncode, 0, generated.stderr)
        installed = self.run_install(
            output / "contract-review-expert",
            reference_capability=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        fallback = (
            self.workspace
            / ".opencode/skills/contract-review-expert-reference-playbook"
        )
        diagnosis = diagnose_skill.diagnose(fallback)
        self.assertTrue(diagnosis.ok, diagnosis.as_dict())
        self.assertEqual(diagnosis.as_dict()["evidenceLevel"], "valid")
        self.assertIn("x" * 1024, (fallback / "SKILL.md").read_text(encoding="utf-8"))

    def test_git_reference_blocks_install_without_verified_capability(self) -> None:
        manifest = self.write_manifest(slug="git-reference-expert", agent_id="git-reference-agent")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"]["references"]["upstream"] = {
            "repository": "example-org/contract-standard",
            "description": "Use for upstream contract standards",
        }
        data["agent"]["references"].append("upstream")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = self.root / "packages"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)

        installed = self.run_install(
            packages / "git-reference-expert",
            reference_capability=False,
        )
        self.assertNotEqual(installed.returncode, 0)
        self.assertIn("capability-missing", installed.stderr)
        self.assertIn("provide a trusted local checkout", installed.stderr)
        self.assertFalse((self.workspace / ".opencode").exists())

    def test_installs_and_prunes_local_and_git_reference_ownership(self) -> None:
        manifest = self.write_manifest(slug="reference-expert", agent_id="reference-agent")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"]["references"]["playbook"]["hidden"] = True
        data["runtime_extensions"]["references"]["upstream"] = {
            "repository": "https://example.com/reference.git",
            "branch": "stable",
            "description": "Upstream playbook",
            "hidden": False,
        }
        data["agent"]["references"].append("upstream")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = self.root / "packages"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = packages / "reference-expert"
        installed = self.run_install(package)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        install_result = json.loads(installed.stdout)
        self.assertEqual(
            install_result["references"],
            ["reference-expert-playbook", "reference-expert-upstream"],
        )

        runtime = self.workspace / ".opencode"
        config_path = runtime / "opencode.jsonc"
        receipt_path = runtime / ".expert-installs/reference-expert.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        expected = {
            "reference-expert-playbook": {
                "path": "references/reference-expert/playbook",
                "description": "Use for clause-level contract review guidance",
                "hidden": True,
            },
            "reference-expert-upstream": {
                "repository": "https://example.com/reference.git",
                "branch": "stable",
                "description": "Upstream playbook",
                "hidden": False,
            },
        }
        self.assertEqual(config["references"], expected)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["config_values"]["references"], expected)

        data["runtime_extensions"]["references"].pop("upstream")
        data["agent"]["references"].remove("upstream")
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regenerated = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(packages),
                "--force",
            ],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            config["references"],
            {"reference-expert-playbook": expected["reference-expert-playbook"]},
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["config_values"]["references"], config["references"])

    def test_installs_and_upgrades_oauth_mcp_with_exact_receipt_ownership(self) -> None:
        manifest = self.write_manifest(slug="oauth-expert", agent_id="oauth-agent")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        oauth = {
            "clientId": "{env:OAUTH_CLIENT_ID}",
            "clientSecret": "{env:OAUTH_CLIENT_SECRET}",
            "scope": "tools.read",
            "redirectUri": "http://127.0.0.1:19876/mcp/oauth/callback",
        }
        data["mcp_servers"] = [
            {
                "name": "oauth-canary",
                "type": "remote",
                "url": "http://127.0.0.1:43123/mcp",
                "oauth": oauth,
                "timeout": 3000,
                "enabled": True,
            }
        ]
        data["agent"]["mcp"] = ["oauth-canary"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = self.root / "packages"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = packages / "oauth-expert"
        installed = self.run_install(package)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        install_result = json.loads(installed.stdout)
        self.assertEqual(
            install_result["required_environment"],
            ["OAUTH_CLIENT_ID", "OAUTH_CLIENT_SECRET"],
        )

        runtime = self.workspace / ".opencode"
        config_path = runtime / "opencode.jsonc"
        receipt_path = runtime / ".expert-installs/oauth-expert.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        expected = {
            "type": "remote",
            "enabled": True,
            "timeout": 3000,
            "url": "http://127.0.0.1:43123/mcp",
            "oauth": oauth,
        }
        self.assertEqual(config["mcp"]["oauth-canary"], expected)
        self.assertEqual(receipt["config_values"]["mcp"]["oauth-canary"], expected)
        self.assertNotIn("OAUTH_CLIENT_SECRET=<required>", receipt_path.read_text(encoding="utf-8"))

        data["mcp_servers"][0]["oauth"]["scope"] = "tools.read tools.call"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regenerated = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(packages),
                "--force",
            ],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["mcp"]["oauth-canary"]["oauth"]["scope"], "tools.read tools.call")

    def test_rejects_cross_slug_oauth_mcp_name_conflicts(self) -> None:
        packages = self.root / "packages"
        built: list[Path] = []
        for slug, agent_id, scope in [
            ("first-oauth", "first-oauth-agent", "tools.read"),
            ("second-oauth", "second-oauth-agent", "tools.write"),
        ]:
            manifest = self.write_manifest(slug=slug, agent_id=agent_id)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["mcp_servers"] = [
                {
                    "name": "shared-oauth",
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "oauth": {"scope": scope},
                }
            ]
            data["agent"]["mcp"] = ["shared-oauth"]
            manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            generated = subprocess.run(
                [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
                env=managed_generator_env(packages),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            built.append(packages / slug)
        self.assertEqual(self.run_install(built[0]).returncode, 0)
        conflict = self.run_install(built[1])
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("mcp.shared-oauth is owned by another expert", conflict.stderr)

    def test_jsonc_comments_urls_and_trailing_commas_are_parsed(self) -> None:
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        (runtime / "opencode.jsonc").write_text(
            """{
  // keep this pre-existing value
  "server": {
    "url": "https://example.com/a/*literal*/b//c",
  },
}
""",
            encoding="utf-8",
        )
        package = self.generate()
        result = self.run_install(package)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(config["server"]["url"], "https://example.com/a/*literal*/b//c")

    def test_same_slug_requires_force_and_force_upgrades_owned_files(self) -> None:
        package = self.generate()
        first = self.run_install(package)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_install(package)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("already installed", second.stderr)
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)

    def test_legacy_receipt_without_bindings_remains_upgradable(self) -> None:
        package = self.generate()
        first = self.run_install(package)
        self.assertEqual(first.returncode, 0, first.stderr)
        receipt_path = (
            self.workspace
            / ".opencode/.expert-installs/contract-review-expert.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.pop("bindings", None)
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        refreshed = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertIn("bindings", refreshed)

    def test_same_slug_force_removes_stale_owned_extensions_and_dependencies(self) -> None:
        manifest = self.write_manifest(slug="upgrade-expert", agent_id="upgrade-agent")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"].setdefault("plugins", {})["package_json"] = {
            "dependencies": {"owned-dependency": "^1.0.0"}
        }
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = self.root / "packages"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = packages / "upgrade-expert"
        self.assertEqual(self.run_install(package).returncode, 0)

        data.pop("runtime_extensions")
        data["agent"]["references"] = []
        data["agent"].pop("instructions", None)
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regenerated = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(packages),
                "--force",
            ],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)

        runtime = self.workspace / ".opencode"
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertNotIn("references", config)
        self.assertNotIn("instructions", config)
        self.assertFalse((runtime / "references/upgrade-expert/playbook/overview.md").exists())
        self.assertFalse((runtime / "instructions/upgrade-expert/evidence.md").exists())
        package_json = json.loads((runtime / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("owned-dependency", package_json.get("dependencies", {}))

    def test_same_slug_force_removes_declared_agent_runtime_options(self) -> None:
        manifest = self.write_manifest(slug="runtime-options", agent_id="runtime-agent")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["agent"].update(
            {
                "steps": 60,
                "model": "openai/gpt-5",
                "variant": "high",
                "temperature": 0.2,
                "top_p": 0.8,
                "options": {"reasoningEffort": "high"},
            }
        )
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = self.root / "packages"
        generated = subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = packages / "runtime-options"
        installed = self.run_install(package)
        self.assertEqual(installed.returncode, 0, installed.stderr)

        runtime = self.workspace / ".opencode"
        config_path = runtime / "opencode.jsonc"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["agent"]["runtime-agent"]["options"], {"reasoningEffort": "high"})

        for key in ("model", "variant", "temperature", "top_p", "options"):
            data["agent"].pop(key)
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regenerated = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(manifest),
                "--output-dir",
                str(packages),
                "--force",
            ],
            env=managed_generator_env(packages),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        upgraded = self.run_install(package, force=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)

        config = json.loads(config_path.read_text(encoding="utf-8"))
        installed_agent = config["agent"]["runtime-agent"]
        for key in ("model", "variant", "temperature", "top_p", "options"):
            self.assertNotIn(key, installed_agent)
        self.assertEqual(installed_agent["steps"], 60)
        receipt = json.loads(
            (runtime / ".expert-installs/runtime-options.json").read_text(encoding="utf-8")
        )
        self.assertIn("agents/runtime-agent.md", receipt["files"])

    def test_different_slug_cannot_overwrite_existing_agent(self) -> None:
        first = self.generate(slug="first-expert", agent_id="shared-agent")
        second = self.generate(slug="second-expert", agent_id="shared-agent")
        self.assertEqual(self.run_install(first).returncode, 0)
        conflict = self.run_install(second, force=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("--force only upgrades the same slug", conflict.stderr)

    def test_dependency_version_conflict_is_rejected_across_slugs(self) -> None:
        first = self.generate(slug="first-deps", agent_id="first-agent")
        second = self.generate(slug="second-deps", agent_id="second-agent")
        for package, version in [(first, "^1.0.0"), (second, "^2.0.0")]:
            manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            manifest.setdefault("runtime_extensions", {}).setdefault("plugins", {})["package_json"] = {
                "dependencies": {"shared-dependency": version}
            }
            source = package / "expert.json"
            source.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            regenerated = subprocess.run(
                [
                    sys.executable,
                    str(CREATE),
                    "--manifest",
                    str(source),
                    "--output-dir",
                    str(self.root / "packages"),
                    "--force",
                ],
                env=managed_generator_env(self.root / "packages"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        self.assertEqual(self.run_install(first).returncode, 0)
        conflict = self.run_install(second, force=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("version conflicts", conflict.stderr)

    def test_dependency_group_conflict_is_rejected_across_slugs(self) -> None:
        first = self.generate(slug="first-group", agent_id="first-group-agent")
        second = self.generate(slug="second-group", agent_id="second-group-agent")
        for package, section in [
            (first, "dependencies"),
            (second, "devDependencies"),
        ]:
            manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            manifest.setdefault("runtime_extensions", {}).setdefault("plugins", {})[
                "package_json"
            ] = {section: {"shared-dependency": "^1.0.0"}}
            source = package / "expert.json"
            source.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            regenerated = subprocess.run(
                [
                    sys.executable,
                    str(CREATE),
                    "--manifest",
                    str(source),
                    "--output-dir",
                    str(self.root / "packages"),
                    "--force",
                ],
                env=managed_generator_env(self.root / "packages"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(regenerated.returncode, 0, regenerated.stderr)

        self.assertEqual(self.run_install(first).returncode, 0)
        conflict = self.run_install(second)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn(
            "devDependencies.shared-dependency conflicts with the existing dependency group dependencies",
            conflict.stderr,
        )

    def test_merge_package_json_rejects_invalid_incoming_contract(self) -> None:
        with self.assertRaisesRegex(SystemExit, "only dependencies and devDependencies"):
            INSTALLER.merge_package_json(
                {},
                {"scripts": {"prepare": "echo nope"}},
                slug="contract-review-expert",
                force=False,
                receipts={},
            )

    def test_same_slug_force_cannot_change_dependency_shared_with_another_slug(self) -> None:
        first = self.generate(slug="first-shared", agent_id="first-shared-agent")
        second = self.generate(slug="second-shared", agent_id="second-shared-agent")
        for package in [first, second]:
            manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            manifest.setdefault("runtime_extensions", {}).setdefault("plugins", {})["package_json"] = {
                "dependencies": {"shared-dependency": "^1.0.0"}
            }
            source = package / "expert.json"
            source.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            regenerated = subprocess.run(
                [
                    sys.executable,
                    str(CREATE),
                    "--manifest",
                    str(source),
                    "--output-dir",
                    str(self.root / "packages"),
                    "--force",
                ],
                env=managed_generator_env(self.root / "packages"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        self.assertEqual(self.run_install(first).returncode, 0)
        self.assertEqual(self.run_install(second).returncode, 0)

        manifest = json.loads((first / "expert.json").read_text(encoding="utf-8"))
        manifest["runtime_extensions"]["plugins"]["package_json"]["dependencies"] = {
            "shared-dependency": "^2.0.0"
        }
        source = first / "expert.json"
        source.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regenerated = subprocess.run(
            [
                sys.executable,
                str(CREATE),
                "--manifest",
                str(source),
                "--output-dir",
                str(self.root / "packages"),
                "--force",
            ],
            env=managed_generator_env(self.root / "packages"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
        conflict = self.run_install(first, force=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("another expert", conflict.stderr)

    def test_multiple_experts_merge_namespaced_references_instructions_and_lsp(self) -> None:
        packages: list[Path] = []
        for slug, agent_id in [("first-merge", "first-agent"), ("second-merge", "second-agent")]:
            package = self.generate(slug=slug, agent_id=agent_id)
            manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            manifest["runtime_extensions"]["lsp"] = {
                f"{slug}-lsp": {
                    "command": [f"{slug}-lsp", "--stdio"],
                    "extensions": [f".{slug}"],
                }
            }
            source = package / "expert.json"
            source.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            regenerated = subprocess.run(
                [
                    sys.executable,
                    str(CREATE),
                    "--manifest",
                    str(source),
                    "--output-dir",
                    str(self.root / "packages"),
                    "--force",
                ],
                env=managed_generator_env(self.root / "packages"),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
            packages.append(package)

        for package in packages:
            installed = self.run_install(package)
            self.assertEqual(installed.returncode, 0, installed.stderr)
        config = json.loads(
            (self.workspace / ".opencode/opencode.jsonc").read_text(encoding="utf-8")
        )
        self.assertEqual(
            config["references"],
            {
                "first-merge-playbook": {
                    "path": "references/first-merge/playbook",
                    "description": "Use for clause-level contract review guidance",
                },
                "second-merge-playbook": {
                    "path": "references/second-merge/playbook",
                    "description": "Use for clause-level contract review guidance",
                },
            },
        )
        self.assertEqual(len(config["instructions"]), 2)
        self.assertEqual(set(config["lsp"]), {"first-merge-lsp", "second-merge-lsp"})

    def test_uninstall_removes_only_receipt_owned_expert_resources(self) -> None:
        first = self.generate(slug="first-uninstall", agent_id="first-uninstall-agent")
        second = self.generate(slug="second-uninstall", agent_id="second-uninstall-agent")
        self.assertEqual(self.run_install(first).returncode, 0)
        self.assertEqual(self.run_install(second).returncode, 0)

        removed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "first-uninstall",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(json.loads(removed.stdout)["status"], "uninstalled")

        runtime = self.workspace / ".opencode"
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertNotIn("first-uninstall-agent", config["agent"])
        self.assertIn("second-uninstall-agent", config["agent"])
        self.assertNotIn("first-uninstall-playbook", config["references"])
        self.assertIn("second-uninstall-playbook", config["references"])
        self.assertFalse((runtime / ".expert-installs/first-uninstall.json").exists())
        self.assertTrue((runtime / ".expert-installs/second-uninstall.json").is_file())
        self.assertFalse((runtime / "agents/first-uninstall-agent.md").exists())
        self.assertTrue((runtime / "agents/second-uninstall-agent.md").is_file())

    def test_uninstall_accepts_legacy_receipt_without_bindings(self) -> None:
        package = self.generate()
        self.assertEqual(self.run_install(package).returncode, 0)
        runtime = self.workspace / ".opencode"
        receipt_path = runtime / ".expert-installs/contract-review-expert.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["contract"] = 1
        receipt.pop("bindings", None)
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        removed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(receipt_path.exists())
        self.assertFalse((runtime / "agents/contract-reviewer.md").exists())
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(
            config["instructions"],
            [".opencode/instructions/contract-review-expert/*.md"],
        )

    def test_legacy_receipt_list_and_dependency_ownership_is_not_trusted(self) -> None:
        receipt = {
            "contract": 1,
            "slug": "legacy-expert",
            "files": {},
            "config_values": {
                "plugin": ["preexisting-plugin"],
                "instructions": ["preexisting-instruction"],
            },
            "dependencies": {
                "dependencies": {"preexisting-dependency": "^1.0.0"}
            },
        }
        receipts = {"legacy-expert": receipt}
        config = {
            "plugin": ["preexisting-plugin"],
            "instructions": ["preexisting-instruction"],
        }
        package_json = {
            "dependencies": {"preexisting-dependency": "^1.0.0"}
        }
        INSTALLER.prune_owned_config(
            config,
            {},
            slug="legacy-expert",
            own_receipt=receipt,
            receipts=receipts,
            force=True,
        )
        INSTALLER.prune_owned_dependencies(
            package_json,
            {},
            slug="legacy-expert",
            own_receipt=receipt,
            receipts=receipts,
            force=True,
        )
        self.assertEqual(config["plugin"], ["preexisting-plugin"])
        self.assertEqual(config["instructions"], ["preexisting-instruction"])
        self.assertEqual(
            package_json["dependencies"],
            {"preexisting-dependency": "^1.0.0"},
        )
        self.assertEqual(
            INSTALLER.merge_list(
                config,
                "plugin",
                ["preexisting-plugin"],
                receipts=receipts,
            ),
            [],
        )
        self.assertEqual(
            INSTALLER.merge_package_json(
                package_json,
                {"dependencies": {"preexisting-dependency": "^1.0.0"}},
                slug="legacy-expert",
                force=True,
                receipts=receipts,
            )["dependencies"],
            {},
        )

    def test_uninstall_refuses_changed_owned_files_without_partial_deletion(self) -> None:
        package = self.generate()
        self.assertEqual(self.run_install(package).returncode, 0)
        runtime = self.workspace / ".opencode"
        agent = runtime / "agents/contract-reviewer.md"
        agent.write_text("user change\n", encoding="utf-8")

        rejected = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("changed owned file", rejected.stderr)
        self.assertEqual(agent.read_text(encoding="utf-8"), "user change\n")
        self.assertTrue(
            (runtime / ".expert-installs/contract-review-expert.json").is_file()
        )

    def test_uninstall_refuses_missing_owned_config_without_partial_deletion(self) -> None:
        package = self.generate()
        self.assertEqual(self.run_install(package).returncode, 0)
        runtime = self.workspace / ".opencode"
        config_path = runtime / "opencode.jsonc"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("instructions")
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rejected = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("missing owned config instructions", rejected.stderr)
        self.assertTrue((runtime / "agents/contract-reviewer.md").is_file())
        self.assertTrue(
            (runtime / ".expert-installs/contract-review-expert.json").is_file()
        )

    def test_uninstall_refuses_missing_owned_mapping_entry_without_partial_deletion(self) -> None:
        package = self.generate()
        self.assertEqual(self.run_install(package).returncode, 0)
        runtime = self.workspace / ".opencode"
        config_path = runtime / "opencode.jsonc"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["references"].pop("contract-review-expert-playbook")
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rejected = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(
            "missing owned config references.contract-review-expert-playbook",
            rejected.stderr,
        )
        self.assertTrue((runtime / "agents/contract-reviewer.md").is_file())
        self.assertTrue(
            (runtime / ".expert-installs/contract-review-expert.json").is_file()
        )

    def test_uninstall_refuses_missing_owned_dependency_without_partial_deletion(self) -> None:
        manifest = self.write_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"].setdefault("plugins", {})["package_json"] = {
            "dependencies": {"owned-dependency": "^1.0.0"}
        }
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "dependency-packages"
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
        self.assertEqual(generated.returncode, 0, generated.stderr)
        package = output / "contract-review-expert"
        self.assertEqual(self.run_install(package).returncode, 0)
        runtime = self.workspace / ".opencode"
        package_json_path = runtime / "package.json"
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
        package_json["dependencies"].pop("owned-dependency")
        package_json_path.write_text(
            json.dumps(package_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rejected = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(
            "missing owned dependency dependencies.owned-dependency",
            rejected.stderr,
        )
        self.assertTrue((runtime / "agents/contract-reviewer.md").is_file())
        self.assertTrue(
            (runtime / ".expert-installs/contract-review-expert.json").is_file()
        )

    def test_install_does_not_claim_preexisting_unowned_plugin(self) -> None:
        manifest = self.write_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"].setdefault("plugins", {})["npm"] = [
            "preexisting-plugin"
        ]
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "plugin-packages"
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
        self.assertEqual(generated.returncode, 0, generated.stderr)
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        (runtime / "opencode.jsonc").write_text(
            json.dumps({"plugin": ["preexisting-plugin"]}) + "\n",
            encoding="utf-8",
        )
        package = output / "contract-review-expert"
        self.assertEqual(self.run_install(package).returncode, 0)
        receipt = json.loads(
            (runtime / ".expert-installs/contract-review-expert.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("plugin", receipt["config_values"])

        removed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(config["plugin"], ["preexisting-plugin"])

    def test_install_does_not_claim_preexisting_unowned_dependency(self) -> None:
        manifest = self.write_manifest()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["runtime_extensions"].setdefault("plugins", {})["package_json"] = {
            "dependencies": {"preexisting-dependency": "^1.0.0"}
        }
        manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output = self.root / "preexisting-dependency-packages"
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
        self.assertEqual(generated.returncode, 0, generated.stderr)
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        (runtime / "package.json").write_text(
            json.dumps({"dependencies": {"preexisting-dependency": "^1.0.0"}})
            + "\n",
            encoding="utf-8",
        )
        package = output / "contract-review-expert"
        self.assertEqual(self.run_install(package).returncode, 0)
        receipt = json.loads(
            (runtime / ".expert-installs/contract-review-expert.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(receipt["dependencies"]["dependencies"], {})

        removed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "contract-review-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        package_json = json.loads((runtime / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(
            package_json["dependencies"],
            {"preexisting-dependency": "^1.0.0"},
        )

    def test_uninstall_rejects_receipt_path_escape(self) -> None:
        runtime = self.workspace / ".opencode"
        receipts = runtime / ".expert-installs"
        receipts.mkdir(parents=True)
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (receipts / "escape-expert.json").write_text(
            json.dumps(
                {
                    "contract": 1,
                    "slug": "escape-expert",
                    "files": {"../../outside.txt": contract.sha256_file(outside)},
                    "config_values": {},
                    "dependencies": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rejected = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--workspace-dir",
                str(self.workspace),
                "--uninstall",
                "escape-expert",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("invalid install receipt", rejected.stderr)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assertTrue((receipts / "escape-expert.json").is_file())

    def test_commit_transaction_rejects_path_escape_before_mutation(self) -> None:
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "path traversal"):
            INSTALLER.commit_transaction(runtime, {}, ["../../outside.txt"])
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_commit_transaction_rolls_back_on_replace_failure(self) -> None:
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        original = runtime / "agents/demo.md"
        original.parent.mkdir()
        original.write_text("original\n", encoding="utf-8")
        staging = self.root / "staging"
        staged_file = staging / "agents/demo.md"
        staged_file.parent.mkdir(parents=True)
        staged_file.write_text("replacement\n", encoding="utf-8")

        real_replace = os.replace
        calls = 0

        def flaky_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated commit failure")
            real_replace(source, target)

        with patch.object(INSTALLER.os, "replace", side_effect=flaky_replace):
            with self.assertRaisesRegex(OSError, "simulated"):
                INSTALLER.commit_transaction(runtime, {"agents/demo.md": staged_file}, [])
        self.assertEqual(original.read_text(encoding="utf-8"), "original\n")

    def test_commit_transaction_preserves_backup_when_rollback_fails(self) -> None:
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        original = runtime / "agents/demo.md"
        original.parent.mkdir()
        original.write_text("original\n", encoding="utf-8")
        staging = self.root / "staging"
        staged_file = staging / "agents/demo.md"
        staged_file.parent.mkdir(parents=True)
        staged_file.write_text("replacement\n", encoding="utf-8")

        real_replace = os.replace
        calls = 0

        def failed_commit_and_recovery(
            source: str | os.PathLike[str],
            target: str | os.PathLike[str],
        ) -> None:
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("simulated replace failure")
            real_replace(source, target)

        with patch.object(INSTALLER.os, "replace", side_effect=failed_commit_and_recovery):
            with self.assertRaises(INSTALLER.InstallRecoveryError) as raised:
                INSTALLER.commit_transaction(runtime, {"agents/demo.md": staged_file}, [])

        self.assertFalse(original.exists())
        self.assertIn(str(original), raised.exception.recovery_paths)
        backup_paths = [
            Path(path)
            for path in raised.exception.recovery_paths
            if ".install-backup-" in path
        ]
        self.assertEqual(len(backup_paths), 1)
        backup = backup_paths[0]
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), "original\n")
        self.assertIn(str(backup), str(raised.exception))

    def test_commit_transaction_reports_written_target_when_cleanup_fails(self) -> None:
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        staging = self.root / "staging"
        first_staged = staging / "agents/first.md"
        second_staged = staging / "agents/second.md"
        first_staged.parent.mkdir(parents=True)
        first_staged.write_text("first\n", encoding="utf-8")
        second_staged.write_text("second\n", encoding="utf-8")
        first_target = runtime / "agents/first.md"

        real_replace = os.replace
        calls = 0

        def fail_second_replace(
            source: str | os.PathLike[str],
            target: str | os.PathLike[str],
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated second write failure")
            real_replace(source, target)

        with patch.object(INSTALLER.os, "replace", side_effect=fail_second_replace), patch.object(
            Path,
            "unlink",
            side_effect=OSError("simulated cleanup failure"),
        ):
            with self.assertRaises(INSTALLER.InstallRecoveryError) as raised:
                INSTALLER.commit_transaction(
                    runtime,
                    {
                        "agents/first.md": first_staged,
                        "agents/second.md": second_staged,
                    },
                    [],
                )

        self.assertEqual(raised.exception.recovery_paths, [str(first_target)])
        self.assertTrue(first_target.is_file())

    def test_failed_first_install_removes_new_runtime_directory(self) -> None:
        package = self.generate()
        with patch.object(INSTALLER, "commit_transaction", side_effect=OSError("simulated install failure")):
            with self.assertRaisesRegex(OSError, "simulated install failure"):
                INSTALLER.install_package(package, self.workspace, force=False)
        self.assertFalse((self.workspace / ".opencode").exists())

    def test_installer_rejects_runtime_symlink_before_writing(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlink is unavailable")
        package = self.generate()
        runtime = self.workspace / ".opencode"
        runtime.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (runtime / "agents").symlink_to(outside, target_is_directory=True)
        result = self.run_install(package)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe symlink", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_jsonc_parser_rejects_unterminated_comment(self) -> None:
        with self.assertRaisesRegex(contract.ContractError, "unterminated"):
            contract.parse_jsonc('{"a": 1 /* nope')


if __name__ == "__main__":
    unittest.main()
