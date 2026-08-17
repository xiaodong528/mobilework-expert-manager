from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
VALIDATE = SCRIPTS / "validate_expert.py"
INSTALL = SCRIPTS / "install_expert.py"
PACKAGE = SCRIPTS / "package_expert.py"
SCAN = SCRIPTS / "scan_portable_artifacts.py"

sys.path.insert(0, str(SCRIPTS))
import archive_inspector
import manifest_contract
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class RoleResourceBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "packages"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, data: dict[str, object], name: str = "expert.json") -> Path:
        path = self.root / name if name == "expert.json" else self.root / name / "expert.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def create(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CREATE), "--manifest", str(path), "--output-dir", str(self.output)],
            env=managed_generator_env(self.output),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_role_rule_is_inlined_and_receipt_records_bindings(self) -> None:
        data = json.loads(load_spec_text("expert-json"))
        role_path = ".opencode/instructions/contract-review-expert/roles/evidence-policy.md"
        rule_content = "# Role evidence policy\n\nEvery conclusion must cite a source location.\n"
        runtime = data["runtime_extensions"]
        runtime["instruction_files"].append({"path": role_path, "content": rule_content})
        runtime["role_instructions"] = {
            "evidence-policy": {
                "path": role_path,
                "description": "Apply source citation rules to this role",
            }
        }
        data["agent"]["instructions"] = ["evidence-policy"]
        source = self.write_manifest(data)

        created = self.create(source)
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.output / "contract-review-expert"
        agent = (package / ".opencode/agents/contract-reviewer.md").read_text(encoding="utf-8")
        self.assertIn("始终遵守的专家包规则", agent)
        self.assertIn("`evidence-policy`", agent)
        self.assertIn(rule_content.strip(), agent)
        config = json.loads((package / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config["instructions"],
            [".opencode/instructions/contract-review-expert/*.md"],
        )
        self.assertNotIn(role_path, config["instructions"])

        validated = subprocess.run(
            [sys.executable, str(VALIDATE), str(package), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

        host = self.root / "host.json"
        host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )
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
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        receipt = json.loads(
            (workspace / ".opencode/.expert-installs/contract-review-expert.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            receipt["bindings"],
            {
                "references": {"contract-review-expert-playbook": ["contract-reviewer"]},
                "roleInstructions": {"evidence-policy": ["contract-reviewer"]},
            },
        )

    def test_team_projection_excludes_unassigned_resources(self) -> None:
        data: dict[str, object] = {
            "slug": "review-team",
            "type": "team",
            "name": "审查团队",
            "description": "分工审查资料并汇总结果。",
            "skills": [],
            "runtime_extensions": {
                "reference_files": [
                    {
                        "path": ".opencode/references/review-team/playbook/guide.md",
                        "content": "# Review guide\n",
                    }
                ],
                "references": {
                    "playbook": {
                        "path": ".opencode/references/review-team/playbook",
                        "description": "Use for lead review decisions",
                    }
                },
                "instruction_files": [
                    {
                        "path": ".opencode/instructions/review-team/roles/lead-policy.md",
                        "content": "Only the lead applies this policy.\n",
                    }
                ],
                "role_instructions": {
                    "lead-policy": {
                        "path": ".opencode/instructions/review-team/roles/lead-policy.md"
                    }
                },
            },
            "primary_agent": {
                "id": "lead",
                "mode": "all",
                "autonomy": "bounded",
                "name": "团长",
                "description": "分派并验收审查任务。",
                "skills": [],
                "references": ["playbook"],
                "instructions": ["lead-policy"],
            },
            "subagents": [
                {
                    "id": "reviewer",
                    "mode": "subagent",
                    "autonomy": "bounded",
                    "name": "审查员",
                    "description": "执行被分派的审查任务。",
                    "skills": [],
                    "references": [],
                    "instructions": [],
                }
            ],
        }
        created = self.create(self.write_manifest(data))
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.output / "review-team"
        lead = (package / ".opencode/agents/lead.md").read_text(encoding="utf-8")
        reviewer = (package / ".opencode/agents/reviewer.md").read_text(encoding="utf-8")
        self.assertIn("review-team-playbook", lead)
        self.assertIn("Only the lead applies this policy.", lead)
        self.assertNotIn("review-team-playbook", reviewer)
        self.assertNotIn("Only the lead applies this policy.", reviewer)

        host = self.root / "team-host.json"
        host.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "test-runtime",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )
        reviewer_path = package / ".opencode/agents/reviewer.md"
        reviewer_path.write_text(
            reviewer + "\n\nOnly the lead applies this policy.\n",
            encoding="utf-8",
        )
        tampered = subprocess.run(
            [
                sys.executable,
                str(VALIDATE),
                str(package),
                "--host-contract",
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn(
            "contains unassigned role rule content lead-policy",
            tampered.stdout + tampered.stderr,
        )
        reviewer_path.write_text(reviewer, encoding="utf-8")
        validated = subprocess.run(
            [
                sys.executable,
                str(VALIDATE),
                str(package),
                "--host-contract",
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        scanned = subprocess.run(
            [sys.executable, str(SCAN), str(package)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(scanned.returncode, 0, scanned.stdout + scanned.stderr)
        dist = self.root / "dist"
        packaged = subprocess.run(
            [
                sys.executable,
                str(PACKAGE),
                "--package-dir",
                str(package),
                "--output-dir",
                str(dist),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(packaged.returncode, 0, packaged.stdout + packaged.stderr)
        archive = dist / "review-team.zip"
        inspection = archive_inspector.inspect_archive(archive)
        self.assertFalse(inspection.errors)
        clean = self.root / "clean"
        archive_inspector.safe_extract(archive, clean, inspection)
        extracted = clean / "review-team"
        revalidated = subprocess.run(
            [
                sys.executable,
                str(VALIDATE),
                str(extracted),
                "--host-contract",
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            revalidated.returncode,
            0,
            revalidated.stdout + revalidated.stderr,
        )
        workspace = self.root / "team-workspace"
        workspace.mkdir()
        installed = subprocess.run(
            [
                sys.executable,
                str(INSTALL),
                "--package-dir",
                str(extracted),
                "--workspace-dir",
                str(workspace),
                "--host-contract",
                str(host),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        receipt = json.loads(
            (workspace / ".opencode/.expert-installs/review-team.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            receipt["bindings"],
            {
                "references": {"review-team-playbook": ["lead"]},
                "roleInstructions": {"lead-policy": ["lead"]},
            },
        )

    def test_role_binding_contract_rejects_ambiguous_or_overlapping_rules(self) -> None:
        base = json.loads(load_spec_text("expert-json"))
        cases = [
            (
                "unknown-reference",
                lambda data: data["agent"].update({"references": ["missing"]}),
                "references unknown Reference missing",
            ),
            (
                "duplicate-reference",
                lambda data: data["agent"].update({"references": ["playbook", "playbook"]}),
                "duplicates playbook",
            ),
            (
                "wildcard-reference",
                lambda data: data["agent"].update({"references": ["*"]}),
                "wildcard bindings are not allowed",
            ),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                data = json.loads(json.dumps(base))
                mutate(data)
                created = self.create(self.write_manifest(data, f"{name}.json"))
                self.assertNotEqual(created.returncode, 0)
                self.assertIn(expected, created.stderr)

        data = json.loads(json.dumps(base))
        role_path = ".opencode/instructions/contract-review-expert/roles/evidence-policy.md"
        data["runtime_extensions"]["instruction_files"].append(
            {"path": role_path, "content": "Role only.\n"}
        )
        data["runtime_extensions"]["role_instructions"] = {
            "evidence-policy": {"path": role_path}
        }
        data["runtime_extensions"]["instructions"].append(role_path)
        data["agent"]["instructions"] = ["evidence-policy"]
        created = self.create(self.write_manifest(data, "overlap.json"))
        self.assertNotEqual(created.returncode, 0)
        self.assertIn("overlaps role rule", created.stderr)

    def test_manifest_contract_reports_invalid_role_binding_types(self) -> None:
        base = json.loads(load_spec_text("expert-json"))
        role_alias = base["agent"]["instructions"][0]
        role_path = base["runtime_extensions"]["role_instructions"][role_alias]["path"]

        cases = (
            ("references", None),
            ("references", {}),
            ("instructions", None),
            ("instructions", {}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = json.loads(json.dumps(base))
                data["agent"]["instructions"] = [role_alias]
                data["agent"][field] = value
                issues = manifest_contract.collect_manifest_issues(data)
                self.assertTrue(
                    any(
                        issue.field == f"agent.{field}"
                        and issue.message == "must be a list"
                        for issue in issues
                    ),
                    issues,
                )

        created = self.create(self.write_manifest(base, "malformed-bindings.json"))
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.output / "contract-review-expert"
        manifest_path = package / "expert.json"
        generated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        def null_references(data: dict[str, object]) -> None:
            data["agent"]["references"] = None

        def object_instructions(data: dict[str, object]) -> None:
            data["agent"]["instructions"] = {}

        def null_instruction_files(data: dict[str, object]) -> None:
            data["runtime_extensions"]["instruction_files"] = None

        def object_instruction_content(data: dict[str, object]) -> None:
            files = data["runtime_extensions"]["instruction_files"]
            for item in files:
                if item["path"] == role_path:
                    item["content"] = {}

        mutations = (
            null_references,
            object_instructions,
            null_instruction_files,
            object_instruction_content,
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate.__name__):
                data = json.loads(json.dumps(generated_manifest))
                mutate(data)
                manifest_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                validated = subprocess.run(
                    [sys.executable, str(VALIDATE), str(package), "--format", "json"],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                output = validated.stdout + validated.stderr
                self.assertNotEqual(validated.returncode, 0, output)
                self.assertNotEqual(validated.returncode, 3, output)
                self.assertNotIn("manager-internal-error", output)

    def test_legacy_implicit_reference_without_description_warns(self) -> None:
        data = json.loads(load_spec_text("expert-json"))
        created = self.create(self.write_manifest(data, "legacy-warning.json"))
        self.assertEqual(created.returncode, 0, created.stderr)
        package = self.output / "contract-review-expert"
        manifest_path = package / "expert.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["agent"].pop("references")
        manifest["runtime_extensions"]["references"]["playbook"].pop("description")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config_path = package / "opencode.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["references"]["contract-review-expert-playbook"].pop("description")
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        validated = subprocess.run(
            [sys.executable, str(VALIDATE), str(package), "--format", "json"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        payload = json.loads(validated.stdout)
        codes = {finding["code"] for finding in payload["findings"]}
        self.assertIn("LEGACY_REFERENCE_BINDINGS_IMPLICIT", codes)
        self.assertIn("REFERENCE_DESCRIPTION_MISSING", codes)


if __name__ == "__main__":
    unittest.main()
