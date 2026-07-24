from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"
SCAN = SCRIPTS / "scan_portable_artifacts.py"
PACKAGE = SCRIPTS / "package_expert.py"
INSTALL = SCRIPTS / "install_expert.py"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text
import archive_inspector
import bundle_contract
import permission_policy


class P0AcceptanceMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads(load_spec_text("expert-json"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def single(self, level: str) -> dict:
        data = copy.deepcopy(self.base)
        data["slug"] = f"{level}-acceptance-expert"
        data["name"] = f"{level} acceptance expert"
        data["runtime_extensions"] = {
            "custom_tools": [{"path": "validate.ts", "content": "export default {}\n"}]
        }
        data["package_resources"] = []
        agent = data["agent"]
        agent["id"] = f"{level}-reviewer"
        agent["name"] = data["name"]
        agent["permission"] = {}
        agent["custom_tools"] = ["validate.ts"]
        agent.pop("tools", None)
        phase = {
            "name": f"{level} phase",
            "mode": "serial",
            "agents": [agent["id"]],
            "autonomy": level,
            "input": "controlled fixture input",
            "expected_output": "verified fixture output",
            "acceptance": ["output is read back from the generated package"],
        }
        if level in {"scripted", "fixed", "bounded"}:
            phase["execution"] = {
                "executors": [{"kind": "custom-tool", "ref": "validate.ts"}],
                "standards": ["run only the declared acceptance custom tool"],
            }
        elif level == "guided":
            phase["execution"] = {
                "executors": [],
                "standards": ["request confirmation before a high-impact decision"],
            }
        data["workflows"] = [
            {
                "name": f"{level} acceptance",
                "autonomy": level,
                "command": {
                    "name": f"run-{level}-acceptance",
                    "description": f"run the {level} acceptance workflow",
                },
                "phases": [phase],
            }
        ]
        return data

    def mixed_team(self) -> dict:
        data = copy.deepcopy(self.base)
        source = data.pop("agent")
        data.update(
            {
                "slug": "mixed-autonomy-acceptance-team",
                "type": "team",
                "name": "mixed autonomy acceptance team",
                "runtime_extensions": {},
                "package_resources": [],
            }
        )
        primary = copy.deepcopy(source)
        primary.update(
            {
                "id": "acceptance-lead",
                "name": "acceptance lead",
                "skills": [{"purpose": "lead-review"}],
                "permission": {},
            }
        )
        primary.pop("tools", None)
        worker = copy.deepcopy(source)
        worker.update(
            {
                "id": "acceptance-worker",
                "name": "acceptance worker",
                "mode": "subagent",
                "skills": [{"purpose": "worker-review"}],
                "permission": {},
            }
        )
        worker.pop("tools", None)
        data["primary_agent"] = primary
        data["subagents"] = [worker]
        data["runtime_extensions"] = {
            "custom_tools": [
                {"path": "team-verify.ts", "content": "export default {}\n"}
            ]
        }
        data["workflows"] = [
            {
                "name": "mixed autonomy acceptance",
                "autonomy": "adaptive",
                "command": {
                    "name": "run-mixed-acceptance",
                    "description": "run the mixed autonomy acceptance workflow",
                },
                "phases": [
                    {
                        "name": "adaptive planning",
                        "mode": "primary",
                        "agents": [],
                        "autonomy": "adaptive",
                        "input": "controlled fixture",
                        "expected_output": "plan",
                        "acceptance": ["plan exists"],
                    },
                    {
                        "name": "fixed verification",
                        "mode": "serial",
                        "agents": ["acceptance-lead", "acceptance-worker"],
                        "autonomy": "fixed",
                        "input": "plan",
                        "expected_output": "verified plan",
                        "execution": {
                            "executors": [
                                {"kind": "custom-tool", "ref": "team-verify.ts"}
                            ],
                            "standards": ["use the fixed verification contract"],
                        },
                        "acceptance": ["verification evidence exists"],
                    },
                ],
            }
        ]
        return data

    def run_cli(self, command: list[str], *, env: dict[str, str] | None = None) -> dict:
        result = subprocess.run(
            command, env=env, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.lstrip().startswith("{") else {}

    def accept(self, data: dict) -> Path:
        slug = data["slug"]
        case = self.root / slug
        case.mkdir()
        manifest = case / "expert.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        packages = case / "packages"
        self.run_cli(
            [sys.executable, str(CREATE), "--manifest", str(manifest), "--output-dir", str(packages)],
            env=managed_generator_env(packages),
        )
        package = packages / slug
        self.assertTrue((package / "opencode.json").is_file())
        self.assertFalse((package / ".opencode/opencode.json").exists())
        self.run_cli([sys.executable, str(VALIDATE), str(package), "--format", "json"])
        self.run_cli([sys.executable, str(SCAN), str(package)])
        dist = case / "dist"
        self.run_cli(
            [sys.executable, str(PACKAGE), "--package-dir", str(package), "--output-dir", str(dist)]
        )
        archive = dist / f"{slug}.zip"
        self.assertIsNone(zipfile.ZipFile(archive).testzip())
        clean = case / "clean"
        clean.mkdir()
        inspection = archive_inspector.inspect_archive(archive)
        self.assertFalse(inspection.errors)
        archive_inspector.safe_extract(archive, clean, inspection)
        extracted = clean / slug
        self.run_cli([sys.executable, str(VALIDATE), str(extracted), "--format", "json"])
        workspace = case / "workspace"
        workspace.mkdir()
        installed = self.run_cli(
            [sys.executable, str(INSTALL), "--package-dir", str(extracted), "--workspace-dir", str(workspace)]
        )
        self.assertEqual(installed["status"], "installable")
        self.assertEqual(installed["runtime_status"], "runtime-not-tested")
        runtime = workspace / ".opencode"
        self.assertFalse((workspace / ".mobilework-engine").exists())
        config = json.loads((runtime / "opencode.jsonc").read_text(encoding="utf-8"))
        roles = [data["agent"]] if data["type"] == "expert" else [data["primary_agent"], *data["subagents"]]
        for role in roles:
            role_id = role["id"]
            self.assertIn(role_id, config["agent"])
            self.assertTrue((runtime / f"agents/{role_id}.md").is_file())
        self.assertTrue((runtime / "skills").is_dir())
        self.assertTrue((runtime / f".expert-installs/{slug}.json").is_file())

        if data["type"] == "expert":
            level = data["workflows"][0]["autonomy"]
            permission = config["agent"][data["agent"]["id"]]["permission"]
            self.assertEqual(permission["*"], "deny" if level == "scripted" else "ask")
            self.assertEqual(permission["doom_loop"], "allow" if level == "adaptive" else "deny" if level == "scripted" else "ask")
            self.assertNotEqual(permission["bash"]["*"], "allow")
            owned_tool = Path(data["agent"]["custom_tools"][0]).stem
            self.assertEqual(permission[owned_tool], "allow")
        else:
            lead = config["agent"]["acceptance-lead"]["permission"]
            worker = config["agent"]["acceptance-worker"]["permission"]
            self.assertEqual(lead["doom_loop"], "ask")
            self.assertEqual(lead["task"], {"*": "deny", "acceptance-worker": "allow"})
            self.assertEqual(worker["task"], {"*": "deny"})
        return archive

    def test_six_trusted_packages_complete_clean_acceptance(self) -> None:
        archives: list[Path] = []
        for level in ("scripted", "fixed", "bounded", "guided", "adaptive"):
            with self.subTest(level=level):
                archives.append(self.accept(self.single(level)))
        with self.subTest(level="mixed-team"):
            archives.append(self.accept(self.mixed_team()))
        bundle = self.root / "bundle"
        manifest = bundle_contract.create_manifest(
            bundle,
            archives,
            tests={"collected": 6, "passed": 6, "failed": 0, "skipped": 0},
        )
        self.assertEqual(len(manifest["packages"]), 6)
        validated_bundle = bundle_contract.validate_bundle(bundle)
        self.assertTrue(validated_bundle["ok"], validated_bundle)

    def test_two_package_sequence_keeps_custom_tool_ownership_isolated(self) -> None:
        first = self.single("bounded")
        second = self.single("adaptive")
        for data, tool_path in (
            (first, "bounded-validate.ts"),
            (second, "adaptive-validate.ts"),
        ):
            data["runtime_extensions"]["custom_tools"][0]["path"] = tool_path
            data["agent"]["custom_tools"] = [tool_path]
            for phase in data["workflows"][0]["phases"]:
                execution = phase.get("execution")
                if isinstance(execution, dict):
                    for executor in execution.get("executors", []):
                        if executor.get("kind") == "custom-tool":
                            executor["ref"] = tool_path
        self.accept(first)
        self.accept(second)
        first_package = self.root / first["slug"] / "packages" / first["slug"]
        second_package = self.root / second["slug"] / "packages" / second["slug"]
        workspace = self.root / "coexist-workspace"
        workspace.mkdir()
        self.run_cli(
            [sys.executable, str(INSTALL), "--package-dir", str(first_package), "--workspace-dir", str(workspace)]
        )
        self.run_cli(
            [sys.executable, str(INSTALL), "--package-dir", str(second_package), "--workspace-dir", str(workspace)]
        )
        config = json.loads((workspace / ".opencode/opencode.jsonc").read_text(encoding="utf-8"))
        first_permission = config["agent"][first["agent"]["id"]]["permission"]
        second_permission = config["agent"][second["agent"]["id"]]["permission"]
        self.assertEqual(first_permission["bounded-validate"], "allow")
        self.assertEqual(second_permission["adaptive-validate"], "allow")
        self.assertEqual(permission_policy._action_for_path(first_permission, "adaptive-validate"), "ask")
        self.assertEqual(permission_policy._action_for_path(second_permission, "bounded-validate"), "ask")


if __name__ == "__main__":
    unittest.main()
