from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import install_state
import package_contract as contract
import safe_input


HASH = "a" * 64


def receipt(
    slug: str,
    receipt_contract: int = 2,
    *,
    files: dict[str, str] | None = None,
    config_values: dict[str, object] | None = None,
    dependencies: dict[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "contract": receipt_contract,
        "slug": slug,
        "files": files or {},
        "config_values": config_values or {},
        "dependencies": dependencies or {},
    }
    if receipt_contract == 3:
        result.update(
            {
                "packageTreeSha256": HASH,
                "manifestSha256": "b" * 64,
                "managerContractSha256": "c" * 64,
                "targetOpenCodeVersion": "v1.16.2",
                "targetCapabilitiesSha256": "d" * 64,
                "projectionSha256": "e" * 64,
            }
        )
    return result


class InstallStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name) / ".opencode"
        self.receipts_dir = self.runtime / contract.INSTALL_RECEIPT_DIR
        self.receipts_dir.mkdir(parents=True)

    def write_receipt(self, value: dict[str, object]) -> None:
        path = self.receipts_dir / f"{value['slug']}.json"
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")

    def verify(self, value: dict[str, object]) -> dict[str, object]:
        return install_state.verify_owned_state(
            self.runtime,
            value,
            {str(value["slug"]): value},
        )

    def test_loads_contracts_and_applies_contract_specific_ownership(self) -> None:
        values = []
        for version in (1, 2, 3):
            value = receipt(
                f"expert-{version}",
                version,
                files={f"agents/role-{version}.md": HASH},
                config_values={
                    "agent": {f"role-{version}": {"mode": "subagent"}},
                    "plugin": ["shared-plugin@1.2.3"],
                    "__scalar__": {"lsp": False},
                },
                dependencies={"dependencies": {"shared-package": "1.2.3"}},
            )
            self.write_receipt(value)
            values.append(value)

        loaded = install_state.load_receipts(self.runtime)

        self.assertEqual(set(loaded), {"expert-1", "expert-2", "expert-3"})
        self.assertEqual(
            install_state.file_owners(loaded)["agents/role-1.md"],
            {"expert-1"},
        )
        self.assertEqual(
            install_state.config_owners(loaded, "__scalar__", "lsp"),
            {"expert-1", "expert-2", "expert-3"},
        )
        self.assertEqual(
            install_state.list_owners(loaded, "plugin", "shared-plugin@1.2.3"),
            {"expert-2", "expert-3"},
        )
        self.assertEqual(
            install_state.dependency_owners(
                loaded,
                "dependencies",
                "shared-package",
            ),
            {"expert-2", "expert-3"},
        )

        invalid_contract_3 = receipt("invalid", 3)
        invalid_contract_3.pop("projectionSha256")
        self.write_receipt(invalid_contract_3)
        with self.assertRaisesRegex(
            install_state.InstallStateError,
            "missing required contract 3 evidence",
        ):
            install_state.load_receipts(self.runtime)

        invalid_contract_3_path = self.receipts_dir / "invalid.json"
        invalid_contract_3_path.unlink()
        invalid_type = receipt("invalid-type", 2)
        invalid_type["contract"] = 2.0
        self.write_receipt(invalid_type)
        with self.assertRaisesRegex(
            install_state.InstallStateError,
            "unsupported contract",
        ):
            install_state.load_receipts(self.runtime)

    def test_detects_missing_and_changed_owned_files(self) -> None:
        changed = self.runtime / "agents/changed.md"
        changed.parent.mkdir(parents=True)
        changed.write_text("changed\n", encoding="utf-8")
        value = receipt(
            "file-owner",
            files={
                "agents/changed.md": contract.sha256_bytes(b"expected\n"),
                "agents/missing.md": contract.sha256_bytes(b"missing\n"),
            },
        )

        report = self.verify(value)

        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "drifted")
        by_path = {item["path"]: item for item in report["preview"]}
        self.assertEqual(by_path["agents/changed.md"]["code"], "OWNED_FILE_CHANGED")
        self.assertEqual(by_path["agents/missing.md"]["code"], "OWNED_FILE_MISSING")
        self.assertIsNone(by_path["agents/missing.md"]["actualSha256"])
        self.assertTrue(
            all(set(item) == install_state._PREVIEW_FIELDS for item in report["preview"])
        )

    def test_detects_mapping_list_and_scalar_config_drift(self) -> None:
        self.write_json(
            self.runtime / contract.WORKSPACE_CONFIG,
            {
                "agent": {"owned-role": {"mode": "primary"}},
                "plugin": ["unowned-plugin@9.9.9"],
                "lsp": True,
            },
        )
        value = receipt(
            "config-owner",
            3,
            config_values={
                "agent": {"owned-role": {"mode": "subagent"}},
                "plugin": ["owned-plugin@1.2.3"],
                "__scalar__": {"lsp": False},
            },
        )

        report = self.verify(value)

        self.assertEqual(
            {(item["kind"], item["code"]) for item in report["preview"]},
            {
                ("config-mapping", "OWNED_CONFIG_CHANGED"),
                ("config-list", "OWNED_CONFIG_MISSING"),
                ("config-scalar", "OWNED_CONFIG_CHANGED"),
            },
        )

    def test_detects_missing_and_changed_dependencies(self) -> None:
        self.write_json(
            self.runtime / "package.json",
            {"dependencies": {"changed-package": "2.0.0"}},
        )
        value = receipt(
            "dependency-owner",
            2,
            dependencies={
                "dependencies": {
                    "changed-package": "1.0.0",
                    "missing-package": "1.0.0",
                }
            },
        )

        report = self.verify(value)

        by_path = {item["path"]: item for item in report["preview"]}
        self.assertEqual(
            by_path["package.json#/dependencies/changed-package"]["code"],
            "OWNED_DEPENDENCY_CHANGED",
        )
        self.assertEqual(
            by_path["package.json#/dependencies/missing-package"]["code"],
            "OWNED_DEPENDENCY_MISSING",
        )

    def test_ignores_unrelated_user_additions(self) -> None:
        owned_file = self.runtime / "agents/owned-role.md"
        owned_file.parent.mkdir(parents=True)
        owned_file.write_text("owned\n", encoding="utf-8")
        (self.runtime / "agents/user-role.md").write_text("user\n", encoding="utf-8")
        self.write_json(
            self.runtime / contract.WORKSPACE_CONFIG,
            {
                "agent": {
                    "owned-role": {"mode": "subagent"},
                    "user-role": {"mode": "primary"},
                },
                "plugin": ["owned-plugin@1.2.3", "user-plugin@4.5.6"],
                "instructions": ["user-instruction.md"],
                "unrelated": {"preserved": True},
            },
        )
        self.write_json(
            self.runtime / "package.json",
            {
                "dependencies": {
                    "owned-package": "1.2.3",
                    "user-package": "4.5.6",
                }
            },
        )
        value = receipt(
            "clean-owner",
            3,
            files={"agents/owned-role.md": contract.sha256_file(owned_file)},
            config_values={
                "agent": {"owned-role": {"mode": "subagent"}},
                "plugin": ["owned-plugin@1.2.3"],
            },
            dependencies={"dependencies": {"owned-package": "1.2.3"}},
        )

        report = self.verify(value)

        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["preview"], [])
        self.assertEqual(
            report["previewSha256"],
            install_state.canonical_preview_sha256(
                [],
                slug=report["slug"],
                receipt_sha256=report["receiptSha256"],
                content_state_sha256=report["contentStateSha256"],
                receipt_set_sha256=report["receiptSetSha256"],
            ),
        )

    def test_preview_never_discloses_owned_or_current_sensitive_values(self) -> None:
        expected_secret = "ghp_ExpectedSecretCanary1234567890"
        actual_secret = "ghp_ActualSecretCanary0987654321"
        expected_list_secret = "https://example.invalid/plugin?token=list-secret-canary"
        self.write_json(
            self.runtime / contract.WORKSPACE_CONFIG,
            {
                "mcp": {"private": {"headers": {"Authorization": actual_secret}}},
                "plugin": [],
            },
        )
        value = receipt(
            "secret-owner",
            2,
            config_values={
                "mcp": {
                    "private": {"headers": {"Authorization": expected_secret}}
                },
                "plugin": [expected_list_secret],
            },
            dependencies={"dependencies": {"private-package": expected_secret}},
        )

        report = self.verify(value)
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)

        for secret in (expected_secret, actual_secret, expected_list_secret, "list-secret-canary"):
            self.assertNotIn(secret, rendered)
        self.assertNotIn("headers", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertTrue(report["preview"])

    def test_preview_order_and_hash_are_stable(self) -> None:
        value = receipt(
            "stable-owner",
            2,
            files={
                "agents/z.md": contract.sha256_bytes(b"z"),
                "agents/a.md": contract.sha256_bytes(b"a"),
            },
            config_values={
                "agent": {
                    "z-role": {"mode": "subagent"},
                    "a-role": {"mode": "subagent"},
                }
            },
        )

        first = self.verify(value)
        reordered = copy.deepcopy(value)
        reordered["files"] = dict(reversed(list(value["files"].items())))
        reordered["config_values"]["agent"] = dict(
            reversed(list(value["config_values"]["agent"].items()))
        )
        second = install_state.verify_owned_state(
            self.runtime,
            reordered,
            {"stable-owner": reordered},
        )

        self.assertEqual(first["preview"], second["preview"])
        self.assertEqual(first["previewSha256"], second["previewSha256"])
        self.assertEqual(
            first["previewSha256"],
            install_state.canonical_preview_sha256(
                first["preview"],
                slug=first["slug"],
                receipt_sha256=first["receiptSha256"],
                content_state_sha256=first["contentStateSha256"],
                receipt_set_sha256=first["receiptSetSha256"],
            ),
        )

    def test_preview_hash_binds_unrelated_content_and_receipt_state(self) -> None:
        value = receipt(
            "state-bound",
            config_values={"agent": {"owned": {"mode": "subagent"}}},
        )
        self.write_receipt(value)
        config_path = self.runtime / contract.WORKSPACE_CONFIG
        self.write_json(
            config_path,
            {"agent": {"owned": {"mode": "subagent"}}},
        )
        first = self.verify(value)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["user-only"] = {"enabled": True}
        config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")

        second = self.verify(value)

        self.assertEqual(second["status"], first["status"])
        self.assertEqual(second["preview"], first["preview"])
        self.assertNotEqual(second["contentStateSha256"], first["contentStateSha256"])
        self.assertNotEqual(second["previewSha256"], first["previewSha256"])
        self.assertEqual(
            [item["path"] for item in first["preview"]],
            sorted(item["path"] for item in first["preview"]),
        )

    @unittest.skipUnless(os.name == "posix", "permission evidence is POSIX-specific")
    def test_preview_hash_binds_permission_only_changes(self) -> None:
        owned = self.runtime / "agents/owned.md"
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b"owned\n")
        os.chmod(owned, 0o644)
        value = receipt(
            "mode-bound",
            files={"agents/owned.md": contract.sha256_file(owned)},
        )
        self.write_receipt(value)

        first = self.verify(value)
        os.chmod(owned, 0o600)
        second = self.verify(value)

        self.assertEqual(first["preview"], second["preview"])
        self.assertNotEqual(first["contentStateSha256"], second["contentStateSha256"])
        self.assertNotEqual(first["previewSha256"], second["previewSha256"])

    def test_runtime_capture_applies_one_aggregate_byte_budget(self) -> None:
        first = self.runtime / "agents/first.md"
        second = self.runtime / "agents/second.md"
        first.parent.mkdir(parents=True)
        first.write_bytes(b"first-target")
        second.write_bytes(b"second-target")
        value = receipt(
            "bounded-state",
            files={
                "agents/first.md": contract.sha256_file(first),
                "agents/second.md": contract.sha256_file(second),
            },
        )
        self.write_receipt(value)
        receipt_size = (self.receipts_dir / "bounded-state.json").stat().st_size
        limits = safe_input.InputLimits(
            max_entries=100,
            max_total_bytes=receipt_size + first.stat().st_size + 1,
            max_file_bytes=receipt_size + first.stat().st_size + 1,
            max_path_characters=512,
            max_path_depth=32,
        )

        with patch.object(safe_input, "default_limits", return_value=limits):
            with self.assertRaises(install_state.InstallStateError) as caught:
                install_state.capture_runtime_inputs(self.runtime)

        self.assertEqual(caught.exception.code, "INSTALL_INPUT_LIMIT_EXCEEDED")

    def test_runtime_capture_applies_limits_to_complete_relative_target(self) -> None:
        target = self.runtime / "agents/nested/file.md"
        target.parent.mkdir(parents=True)
        target.write_text("bounded\n", encoding="utf-8")
        value = receipt(
            "bounded-path",
            files={"agents/nested/file.md": contract.sha256_file(target)},
        )
        self.write_receipt(value)
        limits = safe_input.InputLimits(
            max_entries=100,
            max_total_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
            max_path_characters=512,
            max_path_depth=2,
        )

        with patch.object(safe_input, "default_limits", return_value=limits):
            with self.assertRaises(install_state.InstallStateError) as caught:
                install_state.capture_runtime_inputs(self.runtime)

        self.assertEqual(caught.exception.code, "INSTALL_INPUT_LIMIT_EXCEEDED")

    def test_runtime_capture_applies_limits_to_prefixed_receipt_path(self) -> None:
        self.write_receipt(receipt("x"))
        limits = safe_input.InputLimits(
            max_entries=100,
            max_total_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
            max_path_characters=20,
            max_path_depth=8,
        )

        with patch.object(safe_input, "default_limits", return_value=limits):
            with self.assertRaises(install_state.InstallStateError) as caught:
                install_state.capture_runtime_inputs(self.runtime)

        self.assertEqual(caught.exception.code, "INSTALL_INPUT_LIMIT_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
