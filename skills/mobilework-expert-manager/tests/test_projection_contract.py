from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import manager_contract
import projection_contract
import safe_input


class ProjectionContractTests(unittest.TestCase):
    def test_projection_hash_is_stable_and_excludes_unrelated_workspace_state(self) -> None:
        first = projection_contract.build(
            sources={"agents/a.md": b"a\n"},
            runtime={"agent": {"a": {"mode": "primary"}}},
            dependencies={"dependencies": {"z": "1.0.0"}},
            bindings={},
        )
        second = projection_contract.build(
            sources={"agents/a.md": b"a\n"},
            runtime={"agent": {"a": {"mode": "primary"}}},
            dependencies={"dependencies": {"z": "1.0.0"}},
            bindings={},
        )
        self.assertEqual(projection_contract.sha256(first), projection_contract.sha256(second))
        self.assertNotIn("server", first["config"])

    def test_receipt_evidence_is_recomputed_from_package_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "package"
            package.mkdir()
            (package / "expert.json").write_text('{"slug":"demo"}\n', encoding="utf-8")
            snapshot = safe_input.inspect(package)
            projection = projection_contract.build(
                sources={}, runtime={}, dependencies={}, bindings={}
            )
            target = manager_contract.resolve_target(cli_version="1.2.3", env={})
            evidence = projection_contract.receipt_evidence(
                snapshot=snapshot, target=target, projection=projection
            )
            self.assertEqual(evidence["packageTreeSha256"], snapshot.sha256)
            self.assertEqual(evidence["manifestSha256"], snapshot.file("expert.json").sha256)
            self.assertEqual(evidence["targetOpenCodeVersion"], "1.2.3")
            receipt = {
                "contract": 3,
                "files": {},
                "config_values": {},
                "dependencies": {},
                "bindings": {},
                **evidence,
            }
            self.assertEqual(
                projection_contract.verify_receipt(
                    receipt,
                    snapshot=snapshot,
                    target=target,
                    projection=projection,
                ),
                [],
            )
            self.assertEqual(len(evidence), 6)
            for field in evidence:
                with self.subTest(field=field):
                    tampered = dict(receipt)
                    tampered[field] = (
                        "9.9.9"
                        if field == "targetOpenCodeVersion"
                        else "0" * 64
                    )
                    mismatches = projection_contract.verify_receipt(
                        tampered,
                        snapshot=snapshot,
                        target=target,
                        projection=projection,
                    )
                    self.assertIn(
                        ("CONFIG_RECEIPT_HASH_MISMATCH", f"receipt.{field}"),
                        {(item.code, item.path) for item in mismatches},
                    )

    def test_workspace_projection_allows_unrelated_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / ".opencode"
            (runtime / "agents").mkdir(parents=True)
            (runtime / "agents/demo.md").write_text("demo\n", encoding="utf-8")
            (runtime / "opencode.jsonc").write_text(
                '{"agent":{"demo":{"mode":"primary"}},"server":{"port":1234}}\n',
                encoding="utf-8",
            )
            projection = projection_contract.build(
                sources={"agents/demo.md": b"demo\n"},
                runtime={"agent": {"demo": {"mode": "primary"}}},
                dependencies={},
                bindings={},
            )
            self.assertEqual(
                projection_contract.verify_workspace_projection(runtime, projection),
                [],
            )


if __name__ == "__main__":
    unittest.main()
