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

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, *, slug: str = "contract-review-expert", agent_id: str = "contract-reviewer") -> Path:
        data = json.loads(load_spec_text("expert-json"))
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

    def run_install(self, package: Path, *, force: bool = False) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(INSTALL),
            "--package-dir",
            str(package),
            "--workspace-dir",
            str(self.workspace),
        ]
        if force:
            command.append("--force")
        return subprocess.run(command, text=True, capture_output=True, check=False)

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
        self.assertEqual(receipt["contract"], 1)
        self.assertIn("agents/contract-reviewer.md", receipt["files"])
        self.assertEqual(receipt["config_values"]["references"], config["references"])

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
