from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CREATE = SCRIPTS / "create_expert.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import config_loader
import install_expert
import install_state
import manager_contract
from generator_test_support import managed_generator_env
from spec_templates import load_spec_text


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.host_contract = self.root / "host-contract.json"
        self.host_contract.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "opencodeVersion": "1.16.2",
                    "capabilities": {"references": True},
                }
            ),
            encoding="utf-8",
        )
        self.target = manager_contract.resolve_target(
            env={}, host_contract=self.host_contract
        )
        source = self.root / "source" / "expert.json"
        source.parent.mkdir()
        source.write_text(load_spec_text("legacy-expert-json"), encoding="utf-8")
        output = self.root / "packages"
        generated = subprocess.run(
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
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.package = output / "contract-review-expert"
        install_expert.install_package(
            self.package,
            self.workspace,
            force=False,
            target=self.target,
        )
        self.runtime = self.workspace / ".opencode"
        self.receipt_path = (
            self.runtime / ".expert-installs/contract-review-expert.json"
        )
        self.sidecar = self.root / "opencode"
        self.sidecar.write_text("fixture", encoding="utf-8")
        self.sidecar.chmod(0o700)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def sidecar_result(stdout: str) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def installed_config(self) -> dict[str, object]:
        return json.loads(
            (self.runtime / "opencode.jsonc").read_text(encoding="utf-8")
        )

    def pure_sidecar_run(
        self,
        _path: Path,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> SimpleNamespace:
        if args == ["--version"]:
            return self.sidecar_result("1.16.2\n")
        self.assertEqual(args, ["debug", "config", "--pure"])
        config_path = Path(env["OPENCODE_CONFIG"])
        self.assertNotEqual(
            config_path.resolve(),
            (self.runtime / "opencode.jsonc").resolve(),
        )
        self.assertTrue(str(config_path).startswith(str(cwd)))
        return self.sidecar_result(config_path.read_text(encoding="utf-8"))

    def verify_with_pure_config(self) -> dict[str, object]:
        with patch.object(config_loader, "_run", side_effect=self.pure_sidecar_run):
            return config_loader.verify(
                self.package,
                self.workspace,
                self.sidecar,
                target=self.target,
            )

    def assert_rejected_before_sidecar(self) -> config_loader.ConfigEvidenceError:
        with patch.object(
            config_loader,
            "_run",
            side_effect=AssertionError("sidecar must not run"),
        ):
            with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertEqual(raised.exception.code, "CONFIG_EVIDENCE_CHAIN_INVALID")
        return raised.exception

    def test_contract_3_chain_promotes_only_after_explicit_sidecar(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENCODE_CONFIG_CONTENT": "ambient-config-must-not-be-used"},
        ):
            result = self.verify_with_pure_config()
        self.assertEqual(result["evidenceLevel"], "config-loadable")
        self.assertEqual(result["runtime"]["status"], "not-tested")
        self.assertEqual(result["provenance"]["sidecarActualVersion"], "1.16.2")
        self.assertEqual(result["provenance"]["receipt"]["contract"], 3)
        for field in (
            *install_state.contract_3_hash_fields(),
            *install_state.contract_3_version_fields(),
        ):
            self.assertEqual(
                result["provenance"][field],
                json.loads(self.receipt_path.read_text(encoding="utf-8"))[field],
            )

    def test_contract_2_receipt_is_rejected_before_sidecar(self) -> None:
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        receipt["contract"] = 2
        for field in (
            *install_state.contract_3_hash_fields(),
            *install_state.contract_3_version_fields(),
        ):
            receipt.pop(field)
        self.receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        error = self.assert_rejected_before_sidecar()
        self.assertIn(
            "CONFIG_RECEIPT_CONTRACT_UNTRUSTED",
            {finding["code"] for finding in error.findings},
        )

    def test_handwritten_config_without_receipt_is_rejected_before_sidecar(self) -> None:
        self.receipt_path.unlink()
        error = self.assert_rejected_before_sidecar()
        self.assertEqual(error.findings[0]["code"], "CONFIG_INSTALL_RECEIPT_MISSING")

    def test_missing_config_capture_is_evidence_failure_before_sidecar(self) -> None:
        (self.runtime / "opencode.jsonc").unlink()
        error = self.assert_rejected_before_sidecar()
        self.assertFalse(error.attempted)
        self.assertIn(
            "CONFIG_WORKSPACE_CONFIG_MISSING",
            {finding["code"] for finding in error.findings},
        )

    def test_receipt_disappearing_before_capture_is_evidence_failure(self) -> None:
        original_capture = config_loader._capture_state

        def disappear_before_capture(*args, **kwargs):
            self.receipt_path.unlink()
            return original_capture(*args, **kwargs)

        with patch.object(
            config_loader,
            "_capture_state",
            side_effect=disappear_before_capture,
        ), patch.object(
            config_loader,
            "_run",
            side_effect=AssertionError("sidecar must not run"),
        ):
            with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertFalse(raised.exception.attempted)
        self.assertIn(
            "CONFIG_EVIDENCE_STATE_CHANGED",
            {finding["code"] for finding in raised.exception.findings},
        )

    def test_receipt_hash_mismatch_is_rejected_before_sidecar(self) -> None:
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        receipt["projectionSha256"] = "0" * 64
        self.receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        error = self.assert_rejected_before_sidecar()
        self.assertIn(
            "CONFIG_RECEIPT_HASH_MISMATCH",
            {finding["code"] for finding in error.findings},
        )

    def test_old_manager_contract_hash_requires_clean_upgrade_before_sidecar(self) -> None:
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        current_hash = manager_contract.policy_sha256()
        receipt["managerContractSha256"] = (
            "0" * 64 if current_hash != "0" * 64 else "1" * 64
        )
        self.receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        error = self.assert_rejected_before_sidecar()
        self.assertIn(
            "CONFIG_RECEIPT_HASH_MISMATCH",
            {finding["code"] for finding in error.findings},
        )

        install_expert.install_package(
            self.package,
            self.workspace,
            force=True,
            target=self.target,
        )
        upgraded = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(upgraded["contract"], 3)
        self.assertEqual(upgraded["managerContractSha256"], current_hash)
        self.assertEqual(self.verify_with_pure_config()["evidenceLevel"], "config-loadable")

    def test_receipt_evidence_swap_before_capture_is_rejected_before_sidecar(self) -> None:
        original_capture = config_loader._capture_state
        original_receipt = self.receipt_path.read_bytes()
        for field in (
            *install_state.contract_3_hash_fields(),
            *install_state.contract_3_version_fields(),
        ):
            with self.subTest(field=field):
                calls = 0

                def swap_before_capture(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        changed = json.loads(original_receipt)
                        changed[field] = (
                            "0" * 64
                            if field in install_state.contract_3_hash_fields()
                            else "9.9.9"
                        )
                        self.receipt_path.write_text(
                            json.dumps(changed, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    return original_capture(*args, **kwargs)

                try:
                    with patch.object(
                        config_loader,
                        "_capture_state",
                        side_effect=swap_before_capture,
                    ), patch.object(
                        config_loader,
                        "_run",
                        side_effect=AssertionError("sidecar must not run"),
                    ):
                        with self.assertRaises(
                            config_loader.ConfigEvidenceError
                        ) as raised:
                            config_loader.verify(
                                self.package,
                                self.workspace,
                                self.sidecar,
                                target=self.target,
                            )
                finally:
                    self.receipt_path.write_bytes(original_receipt)
                self.assertFalse(raised.exception.attempted)
                self.assertIn(
                    "CONFIG_RECEIPT_HASH_MISMATCH",
                    {finding["code"] for finding in raised.exception.findings},
                )

    def test_owned_file_swap_before_capture_is_rejected_before_sidecar(self) -> None:
        original_capture = config_loader._capture_state
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        relative = next(iter(receipt["files"]))
        owned_path = self.runtime / relative
        original = owned_path.read_bytes()
        calls = 0

        def swap_before_capture(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                owned_path.write_text("changed-before-capture\n", encoding="utf-8")
            return original_capture(*args, **kwargs)

        try:
            with patch.object(
                config_loader,
                "_capture_state",
                side_effect=swap_before_capture,
            ), patch.object(
                config_loader,
                "_run",
                side_effect=AssertionError("sidecar must not run"),
            ):
                with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                    config_loader.verify(
                        self.package,
                        self.workspace,
                        self.sidecar,
                        target=self.target,
                    )
        finally:
            owned_path.write_bytes(original)
        self.assertFalse(raised.exception.attempted)
        self.assertIn(
            "CONFIG_OWNED_STATE_DRIFT",
            {finding["code"] for finding in raised.exception.findings},
        )

    def test_projection_failure_is_structured_before_sidecar(self) -> None:
        with patch.object(
            install_expert,
            "derive_install_projection",
            side_effect=SystemExit("projection password=projection-secret"),
        ), patch.object(
            config_loader,
            "_run",
            side_effect=AssertionError("sidecar must not run"),
        ):
            with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertEqual(
            raised.exception.findings[0]["code"],
            "CONFIG_PACKAGE_PROJECTION_INVALID",
        )
        self.assertNotIn("projection-secret", str(raised.exception.findings))

    def test_owned_file_drift_is_rejected_before_sidecar(self) -> None:
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        relative = next(iter(receipt["files"]))
        (self.runtime / relative).write_text("drift\n", encoding="utf-8")
        error = self.assert_rejected_before_sidecar()
        self.assertIn(
            "CONFIG_OWNED_STATE_DRIFT",
            {finding["code"] for finding in error.findings},
        )

    def test_sidecar_version_conflict_is_blocked_after_evidence_chain(self) -> None:
        with patch.object(
            config_loader,
            "_run",
            return_value=self.sidecar_result("9.9.9\n"),
        ):
            with self.assertRaisesRegex(
                config_loader.ConfigLoadError,
                "conflicts",
            ) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertTrue(raised.exception.attempted)
        self.assertEqual(raised.exception.stage, "sidecar-version")
        self.assertEqual(
            raised.exception.provenance["sidecarSha256"],
            hashlib.sha256(b"fixture").hexdigest(),
        )
        self.assertEqual(
            Path(raised.exception.provenance["sidecarPath"]).resolve(),
            self.sidecar.resolve(),
        )
        self.assertEqual(
            raised.exception.provenance["sidecarActualVersion"],
            "9.9.9",
        )
        self.assertEqual(
            raised.exception.provenance["sidecarExecution"],
            "private-hashed-copy",
        )

    def test_installed_state_mutation_during_sidecar_is_rejected(self) -> None:
        receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        relative = next(iter(receipt["files"]))

        def mutate_state(*_args, **_kwargs):
            (self.runtime / relative).write_text("changed-during-load\n", encoding="utf-8")
            resolved = self.installed_config()
            return (
                {
                    "sidecarPath": str(self.sidecar),
                    "sidecarActualVersion": "1.16.2",
                    "targetOpenCode": self.target.as_dict(),
                    "workspaceConfig": "private-materialization",
                    "resolvedConfigKeys": sorted(resolved),
                },
                resolved,
            )

        with patch.object(config_loader, "_verify_sidecar", side_effect=mutate_state):
            with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertEqual(
            raised.exception.findings[0]["code"],
            "CONFIG_EVIDENCE_STATE_CHANGED",
        )
        self.assertTrue(raised.exception.attempted)

    def test_transient_workspace_swap_cannot_reach_sidecar_materialization(self) -> None:
        config_path = self.runtime / "opencode.jsonc"
        original = config_path.read_bytes()
        observed: dict[str, object] = {}

        def transient_swap(
            _path: Path,
            args: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
        ) -> SimpleNamespace:
            if args == ["--version"]:
                return self.sidecar_result("1.16.2\n")
            config_path.write_text(
                json.dumps({"transient-malicious-plugin": True}),
                encoding="utf-8",
            )
            try:
                private_path = Path(env["OPENCODE_CONFIG"])
                observed["path"] = private_path
                observed["resolved"] = json.loads(
                    private_path.read_text(encoding="utf-8")
                )
                self.assertTrue(str(private_path).startswith(str(cwd)))
                return self.sidecar_result(
                    json.dumps(observed["resolved"], ensure_ascii=False)
                )
            finally:
                config_path.write_bytes(original)

        with patch.object(config_loader, "_run", side_effect=transient_swap):
            result = config_loader.verify(
                self.package,
                self.workspace,
                self.sidecar,
                target=self.target,
            )
        self.assertEqual(result["status"], "config-loadable")
        self.assertNotIn("transient-malicious-plugin", observed["resolved"])
        self.assertEqual(config_path.read_bytes(), original)

    def test_mutation_after_capture_cannot_change_materialized_bytes(self) -> None:
        original_capture = config_loader._capture_state
        config_path = self.runtime / "opencode.jsonc"
        original = config_path.read_bytes()
        calls = 0
        observed: dict[str, object] = {}

        def capture_with_swap(*args, **kwargs):
            nonlocal calls
            calls += 1
            digest, captures = original_capture(*args, **kwargs)
            if calls == 1:
                injected = self.installed_config()
                injected.setdefault("plugin", []).append(
                    "transient-malicious-plugin@1.0.0"
                )
                config_path.write_text(json.dumps(injected), encoding="utf-8")
            return digest, captures

        def restore_after_private_read(
            _path: Path,
            args: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
        ) -> SimpleNamespace:
            if args == ["--version"]:
                return self.sidecar_result("1.16.2\n")
            private_path = Path(env["OPENCODE_CONFIG"])
            observed["resolved"] = json.loads(
                private_path.read_text(encoding="utf-8")
            )
            config_path.write_bytes(original)
            return self.sidecar_result(
                json.dumps(observed["resolved"], ensure_ascii=False)
            )

        with patch.object(
            config_loader,
            "_capture_state",
            side_effect=capture_with_swap,
        ), patch.object(
            config_loader,
            "_run",
            side_effect=restore_after_private_read,
        ):
            result = config_loader.verify(
                self.package,
                self.workspace,
                self.sidecar,
                target=self.target,
            )
        self.assertEqual(result["status"], "config-loadable")
        self.assertNotIn(
            "transient-malicious-plugin@1.0.0",
            observed["resolved"].get("plugin", []),
        )

    def test_resolved_projection_mismatch_is_attempted_failure(self) -> None:
        with patch.object(
            config_loader,
            "_run",
            side_effect=[
                self.sidecar_result("1.16.2\n"),
                self.sidecar_result("{}"),
            ],
        ):
            with self.assertRaises(config_loader.ConfigEvidenceError) as raised:
                config_loader.verify(
                    self.package,
                    self.workspace,
                    self.sidecar,
                    target=self.target,
                )
        self.assertTrue(raised.exception.attempted)
        self.assertEqual(raised.exception.stage, "sidecar-resolved-config")
        self.assertIn(
            "CONFIG_RESOLVED_PROJECTION_MISMATCH",
            {finding["code"] for finding in raised.exception.findings},
        )

    def test_pure_sidecar_environment_drops_ambient_control_variables(self) -> None:
        observed: dict[str, str] = {}

        def inspect_environment(
            path: Path,
            args: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
        ) -> SimpleNamespace:
            observed.update(env)
            return self.pure_sidecar_run(path, args, cwd=cwd, env=env)

        with patch.dict(
            os.environ,
            {
                "HOME": "/tmp/ambient-home",
                "OPENCODE_CONFIG_CONTENT": "ambient-config",
                "OPENCODE_PLUGIN": "ambient-plugin",
                "OPENCODE_TEST_HOME": "/tmp/ambient-test-home",
            },
        ), patch.object(
            config_loader,
            "_run",
            side_effect=inspect_environment,
        ):
            config_loader.verify(
                self.package,
                self.workspace,
                self.sidecar,
                target=self.target,
            )
        self.assertNotEqual(observed["HOME"], "/tmp/ambient-home")
        self.assertEqual(observed["HOME"], observed["OPENCODE_TEST_HOME"])
        self.assertNotIn("OPENCODE_CONFIG_CONTENT", observed)
        self.assertNotIn("OPENCODE_PLUGIN", observed)

    def test_sidecar_execution_is_bound_to_private_hashed_copy(self) -> None:
        executed_paths: list[Path] = []

        def mutate_source_after_copy(
            path: Path,
            args: list[str],
            *,
            cwd: Path,
            env: dict[str, str],
        ) -> SimpleNamespace:
            executed_paths.append(path)
            self.assertNotEqual(path.resolve(), self.sidecar.resolve())
            self.assertEqual(path.read_bytes(), b"fixture")
            if args == ["--version"]:
                self.sidecar.write_text("replacement", encoding="utf-8")
                return self.sidecar_result("1.16.2\n")
            return self.pure_sidecar_run(path, args, cwd=cwd, env=env)

        with patch.object(
            config_loader,
            "_run",
            side_effect=mutate_source_after_copy,
        ):
            result = config_loader.verify(
                self.package,
                self.workspace,
                self.sidecar,
                target=self.target,
            )
        self.assertEqual(result["status"], "config-loadable")
        self.assertEqual(len({str(path) for path in executed_paths}), 1)
        self.assertEqual(
            result["provenance"]["sidecarSha256"],
            hashlib.sha256(b"fixture").hexdigest(),
        )
        self.assertEqual(
            result["provenance"]["sidecarExecution"],
            "private-hashed-copy",
        )

    def test_sidecar_copy_io_failure_is_a_sanitized_contract_error(self) -> None:
        target = self.root / "private-sidecar"
        with patch.object(
            config_loader.os,
            "read",
            side_effect=OSError("password=sidecar-copy-canary"),
        ):
            with self.assertRaises(config_loader.ConfigLoadError) as raised:
                config_loader._materialize_sidecar(self.sidecar, target)
        self.assertEqual(
            str(raised.exception),
            "private sidecar materialization failed",
        )
        self.assertNotIn("sidecar-copy-canary", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
