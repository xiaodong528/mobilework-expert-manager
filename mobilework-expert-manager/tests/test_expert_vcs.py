from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import expert_vcs
import create_expert
import package_expert
import provenance
from generator_test_support import managed_generator_env


class ExpertVcsTests(unittest.TestCase):
    def configure_identity(self, package: Path) -> None:
        subprocess.run(["git", "-C", str(package), "config", "user.name", "Fixture User"], check=True)
        subprocess.run(
            ["git", "-C", str(package), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )

    def generate(self, root: Path) -> Path:
        source = root / "source/expert.json"
        source.parent.mkdir()
        source.write_text(json.dumps({
            "slug": "versioned-expert", "type": "expert", "name": "Versioned",
            "description": "A trusted version-control fixture.",
            "common_skills": [{"purpose": "delivery"}],
            "agent": {"id": "versioned", "description": "Deliver work.", "skills": [{"purpose": "method"}]},
        }), encoding="utf-8")
        output = root / "experts"
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "create_expert.py"), "--manifest", str(source), "--output-dir", str(output)],
            env=managed_generator_env(output), text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERSION_PENDING", result.stderr)
        return (output / "versioned-expert").resolve()

    def test_initialize_release_tag_and_distribution_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.generate(root)
            self.assertEqual(expert_vcs.repository_root(package), package)
            self.configure_identity(package)
            proposal = expert_vcs.propose_version(package)
            self.assertEqual(proposal.tag, "v1.0.0")
            released = expert_vcs.release(package, proposal.version)
            self.assertTrue(released["ok"], released)
            self.assertEqual(released["commit"], released["tagCommit"])
            self.assertEqual(released["manifestVersion"], "1.0.0")
            self.assertEqual(
                released["expertJsonSha256"], provenance.file_sha256(package / "expert.json")
            )
            manifest = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "1.0.0")
            archive = package_expert.make_zip(package, root / "dist", run_external_test=False)
            with zipfile.ZipFile(archive) as zipped:
                self.assertFalse(any(".git" in Path(name).parts for name in zipped.namelist()))
            self.assertIn("versioned-expert/.gitignore", zipped.namelist())

            with (package / ".gitignore").open("a", encoding="utf-8") as stream:
                stream.write("custom-cache/\n")
            changed = json.loads((package / "expert.json").read_text(encoding="utf-8"))
            changed["description"] = "A trusted version-control fixture with a compatible correction."
            source = root / "changed/expert.json"
            source.parent.mkdir()
            source.write_text(json.dumps(changed), encoding="utf-8")
            revision = create_expert.calculate_package_revision(package)
            environment = managed_generator_env(package.parent)
            environment[create_expert.CONTROLLED_TARGET_ENV] = str(package)
            regenerated = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / "create_expert.py"),
                    "--manifest", str(source), "--output-dir", str(package.parent),
                    "--force", "--expected-revision", revision,
                ],
                env=environment, text=True, capture_output=True, check=False,
            )
            self.assertEqual(regenerated.returncode, 0, regenerated.stderr)
            self.assertIn("VERSION_PENDING", regenerated.stderr)
            self.assertIn("custom-cache/", (package / ".gitignore").read_text(encoding="utf-8"))
            self.assertEqual(expert_vcs.last_release_tag(package), "v1.0.0")
            self.assertEqual(expert_vcs.propose_version(package).tag, "v1.0.1")
            remotes = subprocess.run(
                ["git", "-C", str(package), "remote"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(remotes.stdout, "")

    def test_semver_classification(self) -> None:
        base = {"slug": "a", "type": "expert", "agent": {"id": "a"}, "workflows": []}
        breaking = {**base, "slug": "b"}
        self.assertEqual(expert_vcs.classify_change(base, breaking)[0], "major")
        compatible = {**base, "workflows": [{"name": "new"}]}
        self.assertEqual(expert_vcs.classify_change(base, compatible)[0], "minor")
        self.assertEqual(expert_vcs.classify_change(base, dict(base))[0], "patch")

    def test_release_rejects_prestaged_and_unowned_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            subprocess.run(["git", "-C", str(package), "add", "expert.json"], check=True)
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "pre-staged"):
                expert_vcs.release(package, "1.0.0")

        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            (package / "mystery.txt").write_text("unowned", encoding="utf-8")
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "unowned"):
                expert_vcs.release(package, "1.0.0")
            self.assertNotIn("version", json.loads((package / "expert.json").read_text(encoding="utf-8")))

    def test_tag_failure_keeps_release_commit_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            original_run = subprocess.run

            def fail_tag(command, **kwargs):
                if "tag" in command and "-a" in command:
                    return subprocess.CompletedProcess(command, 1, "", "simulated tag failure")
                return original_run(command, **kwargs)

            with patch("subprocess.run", side_effect=fail_tag):
                result = expert_vcs.release(package, "1.0.0")
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "release-incomplete")
            self.assertTrue(result["commit"])
            self.assertEqual(expert_vcs.last_release_tag(package), "")

    def test_proposal_rejection_is_read_only_and_cumulative(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            first = expert_vcs.propose_version(package)
            self.assertEqual(first.tag, "v1.0.0")
            self.assertNotIn("version", json.loads((package / "expert.json").read_text()))
            self.assertEqual(expert_vcs.last_release_tag(package), "")
            self.assertNotEqual(
                subprocess.run(
                    ["git", "-C", str(package), "rev-parse", "--verify", "HEAD"],
                    capture_output=True,
                    check=False,
                ).returncode,
                0,
            )

            manifest = json.loads((package / "expert.json").read_text())
            manifest["description"] = "first deferred correction"
            (package / "expert.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(expert_vcs.propose_version(package).tag, "v1.0.0")
            manifest["description"] = "second cumulative deferred correction"
            (package / "expert.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(expert_vcs.propose_version(package).tag, "v1.0.0")

    def test_release_preflights_identity_tag_branch_and_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            empty_home = Path(temp) / "empty-home"
            empty_home.mkdir()
            with patch.dict(
                "os.environ",
                {"HOME": str(empty_home), "GIT_CONFIG_GLOBAL": "/dev/null"},
                clear=False,
            ):
                with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "identity"):
                    expert_vcs.release(package, "1.0.0")
            self.assertNotIn("version", json.loads((package / "expert.json").read_text()))

        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            self.assertTrue(expert_vcs.release(package, "1.0.0")["ok"])
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "already exists"):
                expert_vcs.release(package, "1.0.0")
            subprocess.run(
                ["git", "-C", str(package), "checkout", "--detach", "HEAD"],
                check=True,
                capture_output=True,
            )
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "detached HEAD"):
                expert_vcs.release(package, "1.0.1")

        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            attributes = package / ".git/info/attributes"
            attributes.parent.mkdir(parents=True, exist_ok=True)
            attributes.write_text("* filter=unsafe\n", encoding="utf-8")
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "attributes"):
                expert_vcs.release(package, "1.0.0")

    def test_release_failure_restores_only_release_changes_and_unstages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            package = self.generate(Path(temp))
            self.configure_identity(package)
            manifest = json.loads((package / "expert.json").read_text())
            manifest["description"] = "user-owned pending correction"
            (package / "expert.json").write_text(json.dumps(manifest), encoding="utf-8")
            before = (package / "expert.json").read_bytes()
            original_run = expert_vcs._run

            def fail_commit(root, arguments, *, check=True):
                if arguments and arguments[0] == "commit":
                    raise expert_vcs.ExpertVcsError("simulated commit failure")
                return original_run(root, arguments, check=check)

            with patch.object(expert_vcs, "_run", side_effect=fail_commit):
                with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "simulated"):
                    expert_vcs.release(package, "1.0.0")
            self.assertEqual((package / "expert.json").read_bytes(), before)
            staged = subprocess.run(
                ["git", "-C", str(package), "diff", "--cached", "--name-only"],
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(staged.stdout, "")

            invalid_before = (package / "expert.json").read_bytes()
            (package / "README.md").write_text("# user-invalid-derived-file\n", encoding="utf-8")
            with self.assertRaisesRegex(expert_vcs.ExpertVcsError, "failed validation"):
                expert_vcs.release(package, "1.0.0")
            self.assertEqual((package / "expert.json").read_bytes(), invalid_before)
            self.assertEqual(
                (package / "README.md").read_text(encoding="utf-8"),
                "# user-invalid-derived-file\n",
            )

    def test_role_custom_tool_ownership_change_is_minor(self) -> None:
        previous = {
            "slug": "a", "type": "expert", "agent": {"id": "a", "custom_tools": []},
            "runtime_extensions": {"custom_tools": [{"name": "known"}]},
        }
        current = json.loads(json.dumps(previous))
        current["agent"]["custom_tools"] = ["known.ts"]
        self.assertEqual(expert_vcs.classify_change(previous, current)[0], "minor")

    def test_git_metadata_is_excluded_from_hash_and_workspace_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.generate(root)
            before = provenance.tree_sha256(package)
            marker = package / ".git" / "manager-test-marker"
            marker.write_text("must not affect package hash", encoding="utf-8")
            self.assertEqual(provenance.tree_sha256(package), before)

            workspace = root / "workspace"
            workspace.mkdir()
            installed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "install_expert.py"),
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
            self.assertFalse(any(path.name == ".git" for path in workspace.rglob(".git")))
            self.assertFalse((workspace / ".gitignore").exists())


if __name__ == "__main__":
    unittest.main()
