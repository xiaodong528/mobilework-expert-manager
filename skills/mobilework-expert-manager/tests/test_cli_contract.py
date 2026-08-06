from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cli_contract


class CliContractTests(unittest.TestCase):
    def result(
        self,
        *,
        exit_code: int = 0,
        legacy_payload: dict[str, object] | None = None,
    ) -> cli_contract.CliResult:
        return cli_contract.CliResult(
            operation="validate-expert",
            ok=exit_code == 0,
            status="valid" if exit_code == 0 else "invalid",
            evidence_level="valid" if exit_code == 0 else "invalid",
            gates={
                "archive": "not-run",
                "contract": "passed" if exit_code == 0 else "failed",
                "portability": "passed" if exit_code == 0 else "blocked",
                "install": "not-run",
                "configLoad": "not-run",
            },
            runtime={"status": "not-tested", "reason": "static-only"},
            execution={"policy": "static-only", "attempted": False},
            provenance={"source": "fixture"},
            findings=(),
            data={"package": "/tmp/fixture"},
            exit_code=exit_code,
            legacy_payload=legacy_payload,
        )

    def test_schema_v2_has_exact_canonical_top_level_fields(self) -> None:
        payload = self.result().as_dict(schema_version=2)
        self.assertEqual(tuple(payload), cli_contract.SCHEMA_V2_FIELDS)
        self.assertEqual(len(payload), 11)
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["operation"], "validate-expert")
        self.assertEqual(payload["data"], {"package": "/tmp/fixture"})

    def test_schema_v1_adapts_the_same_result_without_running_work(self) -> None:
        calls = 0

        def execute() -> cli_contract.CliResult:
            nonlocal calls
            calls += 1
            return self.result(
                legacy_payload={
                    "schemaVersion": 99,
                    "ok": True,
                    "status": "runtime-not-tested",
                    "rawFindingCount": 0,
                }
            )

        result = execute()
        legacy = result.as_dict(schema_version=1)
        current = result.as_dict(schema_version=2)
        self.assertEqual(calls, 1)
        self.assertEqual(legacy["schemaVersion"], 1)
        self.assertEqual(legacy["rawFindingCount"], 0)
        self.assertEqual(current["schemaVersion"], 2)

    def test_json_emitter_writes_exactly_one_redacted_document(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        result = cli_contract.CliResult(
            operation="verify-config",
            ok=True,
            status="config-loadable",
            evidence_level="config-loadable",
            gates=cli_contract.default_gates(),
            runtime={"status": "not-tested", "reason": "pure-config"},
            execution={"policy": "trusted-sidecar", "attempted": True},
            provenance={"apiKey": "stdout-canary"},
            findings=(),
            data={},
        )
        code = cli_contract.emit(
            result,
            output_format="json",
            schema_version=2,
            stdout=output,
            stderr=error,
            diagnostics=("password=stderr-canary",),
        )
        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(output.getvalue())
        self.assertFalse(output.getvalue()[end:].strip())
        self.assertEqual(code, 0)
        self.assertEqual(tuple(payload), cli_contract.SCHEMA_V2_FIELDS)
        self.assertNotIn("stdout-canary", output.getvalue())
        self.assertNotIn("stderr-canary", error.getvalue())

    def test_human_emitter_redacts_renderer_output(self) -> None:
        output = io.StringIO()
        code = cli_contract.emit(
            self.result(),
            output_format="human",
            stdout=output,
            human_renderer=lambda _result: "done token=human-canary",
        )
        self.assertEqual(code, 0)
        self.assertIn("done", output.getvalue())
        self.assertNotIn("human-canary", output.getvalue())

    def test_human_recovery_output_includes_actionable_sanitized_evidence(self) -> None:
        failure = cli_contract.CliInternalError(
            "manual recovery required",
            attempted=True,
            data={
                "committed": None,
                "rollbackVerified": False,
                "recoveryPaths": [
                    "/tmp/recovery-marker",
                    "/tmp/password=human-recovery-canary",
                ],
            },
        )
        output = cli_contract.render_human(
            cli_contract.result_for_failure("install-expert", failure)
        )

        self.assertIn("- committed: unknown", output)
        self.assertIn("- rollbackVerified: false", output)
        self.assertEqual(output.count("- recoveryPath:"), 2)
        self.assertNotIn("human-recovery-canary", output)

    def test_emitter_preserves_all_fixed_exit_codes(self) -> None:
        for expected in range(5):
            with self.subTest(exit_code=expected):
                output = io.StringIO()
                actual = cli_contract.emit(
                    self.result(exit_code=expected),
                    output_format="json",
                    stdout=output,
                )
                self.assertEqual(actual, expected)
                json.loads(output.getvalue())

    def test_typed_and_unexpected_failures_are_structured_without_traceback(self) -> None:
        cases = (
            (
                cli_contract.CliContractError("unsafe package"),
                1,
                "MANAGER_CONTRACT_ERROR",
            ),
            (
                cli_contract.CliArgumentError("bad argument"),
                2,
                "MANAGER_ARGUMENT_ERROR",
            ),
            (
                cli_contract.CliInternalError("internal failure"),
                3,
                "MANAGER_INTERNAL_ERROR",
            ),
            (
                cli_contract.CliRuntimePolicyError("runtime blocked"),
                4,
                "MANAGER_RUNTIME_POLICY_BLOCKED",
            ),
            (
                RuntimeError("Authorization: Bearer generic-internal-canary"),
                3,
                "MANAGER_INTERNAL_ERROR",
            ),
        )
        for failure, expected_exit, expected_code in cases:
            with self.subTest(expected_exit=expected_exit, expected_code=expected_code):
                output = io.StringIO()
                error = io.StringIO()

                def execute(error_to_raise: Exception = failure) -> cli_contract.CliResult:
                    raise error_to_raise

                actual = cli_contract.run_cli(
                    "fixture-operation",
                    execute,
                    output_format="json",
                    stdout=output,
                    stderr=error,
                )
                payload = json.loads(output.getvalue())
                self.assertEqual(actual, expected_exit)
                self.assertEqual(tuple(payload), cli_contract.SCHEMA_V2_FIELDS)
                self.assertEqual(payload["findings"][0]["code"], expected_code)
                self.assertNotIn("generic-internal-canary", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue() + error.getvalue())

    def test_policy_injection_accepts_defaults_and_rejects_contract_drift(self) -> None:
        policy_data = json.loads(
            (SCRIPTS / "manager-contract.json").read_text(encoding="utf-8")
        )
        policy = cli_contract.CliPolicy.from_policy(policy_data)
        self.assertEqual(policy.default_schema_version, 2)
        self.assertEqual(self.result().as_dict(policy=policy)["schemaVersion"], 2)
        with self.assertRaises(cli_contract.CliArgumentError):
            cli_contract.CliPolicy.from_policy(
                {"cli": {"v2Fields": ["schemaVersion", "ok"]}}
            )

    def test_invalid_policy_is_rejected_before_action_execution(self) -> None:
        calls = 0
        output = io.StringIO()

        def execute() -> cli_contract.CliResult:
            nonlocal calls
            calls += 1
            return self.result()

        code = cli_contract.run_cli(
            "install-expert",
            execute,
            output_format="json",
            stdout=output,
            policy={"cli": {"formats": ["json", "yaml"]}},
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(calls, 0)
        self.assertEqual(code, 3)
        self.assertEqual(payload["findings"][0]["code"], "MANAGER_POLICY_INVALID")

    def test_invalid_format_and_schema_raise_argument_failures(self) -> None:
        with self.assertRaises(cli_contract.CliArgumentError):
            cli_contract.emit(self.result(), output_format="yaml")
        with self.assertRaises(cli_contract.CliArgumentError):
            self.result().as_dict(schema_version=9)
        invalid_gates = self.result()
        object.__setattr__(invalid_gates, "gates", {"contract": "passed"})
        with self.assertRaises(cli_contract.CliArgumentError):
            invalid_gates.as_dict(schema_version=2)
        with self.assertRaises(cli_contract.CliArgumentError):
            self.result(exit_code=0).__class__(
                operation="test",
                ok=False,
                status="invalid",
                evidence_level="invalid",
                gates={},
                runtime={},
                execution={},
                provenance={},
                findings=(),
                data={},
                exit_code="invalid",
            )

    def test_failure_fallback_does_not_reuse_a_broken_human_renderer(self) -> None:
        output = io.StringIO()

        def broken_renderer(_result: cli_contract.CliResult) -> str:
            raise RuntimeError("renderer password=renderer-canary")

        code = cli_contract.run_cli(
            "fixture-operation",
            lambda: self.result(),
            output_format="human",
            stdout=output,
            human_renderer=broken_renderer,
        )
        self.assertEqual(code, 3)
        self.assertIn("internal-error", output.getvalue())
        self.assertNotIn("renderer-canary", output.getvalue())

    def test_non_finite_json_and_inconsistent_results_fail_closed(self) -> None:
        output = io.StringIO()
        code = cli_contract.run_cli(
            "fixture-operation",
            lambda: cli_contract.CliResult(
                operation="fixture-operation",
                ok=True,
                status="valid",
                evidence_level="valid",
                gates=self.result().gates,
                runtime={"status": "not-tested"},
                execution={"attempted": False},
                provenance={},
                findings=(),
                data={"value": float("nan")},
                exit_code=0,
            ),
            output_format="json",
            stdout=output,
        )
        self.assertEqual(code, 3)
        self.assertNotIn("NaN", output.getvalue())
        json.loads(output.getvalue(), parse_constant=lambda value: self.fail(value))

        inconsistent = self.result(exit_code=0)
        object.__setattr__(inconsistent, "ok", False)
        with self.assertRaises(cli_contract.CliArgumentError):
            inconsistent.as_dict(schema_version=2)

        invalid_gates = self.result()
        object.__setattr__(invalid_gates, "gates", {"contract": "passed"})
        output = io.StringIO()
        code = cli_contract.run_cli(
            "fixture-operation",
            lambda: invalid_gates,
            output_format="json",
            stdout=output,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["findings"][0]["code"], "MANAGER_INTERNAL_ERROR")

    def test_legacy_adapter_executes_once_and_derives_both_schemas(self) -> None:
        calls = 0

        def legacy_main() -> int:
            nonlocal calls
            calls += 1
            print(json.dumps({"ok": True, "status": "package-valid", "value": 7}))
            return 0

        for schema_version in (1, 2):
            with self.subTest(schema_version=schema_version):
                output = io.StringIO()
                code = cli_contract.run_legacy_entrypoint(
                    "legacy-fixture",
                    legacy_main,
                    argv=("--format", "json", "--schema-version", str(schema_version)),
                    stdout=output,
                )
                payload = json.loads(output.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(payload["schemaVersion"], schema_version)
                if schema_version == 1:
                    self.assertEqual(payload["value"], 7)
                else:
                    self.assertEqual(tuple(payload), cli_contract.SCHEMA_V2_FIELDS)
                    self.assertEqual(payload["data"]["value"], 7)
        self.assertEqual(calls, 2)

    def test_legacy_adapter_emits_one_document_and_redacts_all_sinks(self) -> None:
        output = io.StringIO()
        error = io.StringIO()

        def legacy_main() -> int:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "code": "LEGACY_FAILURE",
                        "message": "Authorization: Bearer stdout-canary",
                    }
                )
            )
            print("password=stderr-canary", file=sys.stderr)
            return 1

        code = cli_contract.run_legacy_entrypoint(
            "legacy-fixture",
            legacy_main,
            argv=("--format=json", "--schema-version=2"),
            stdout=output,
            stderr=error,
        )
        decoder = json.JSONDecoder()
        payload, end = decoder.raw_decode(output.getvalue())
        self.assertFalse(output.getvalue()[end:].strip())
        self.assertEqual(code, 1)
        self.assertEqual(payload["findings"][0]["code"], "LEGACY_FAILURE")
        self.assertNotIn("stdout-canary", output.getvalue())
        self.assertNotIn("stderr-canary", error.getvalue())
        self.assertNotIn("Traceback", output.getvalue() + error.getvalue())

    def test_legacy_adapter_normalizes_argument_and_exit_matrix(self) -> None:
        calls = 0

        def should_not_run() -> int:
            nonlocal calls
            calls += 1
            return 0

        output = io.StringIO()
        code = cli_contract.run_legacy_entrypoint(
            "legacy-fixture",
            should_not_run,
            argv=("--format", "yaml"),
            stdout=output,
        )
        self.assertEqual(code, 2)
        self.assertEqual(calls, 0)
        self.assertEqual(json.loads(output.getvalue())["schemaVersion"], 2)

        for expected in range(5):
            with self.subTest(exit_code=expected):
                output = io.StringIO()

                def legacy_main(value: int = expected) -> int:
                    print(json.dumps({"ok": value == 0}))
                    return value

                actual = cli_contract.run_legacy_entrypoint(
                    "legacy-fixture",
                    legacy_main,
                    argv=("--format", "json"),
                    stdout=output,
                    stderr=io.StringIO(),
                )
                self.assertEqual(actual, expected)
                json.loads(output.getvalue())


if __name__ == "__main__":
    unittest.main()
