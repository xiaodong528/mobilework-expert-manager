from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "mobilework-expert-manager"
MARKETPLACE_NAME = "mobilework-tools"
VERSION = "0.5.0"
SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
SOURCE_SKILL = ROOT / "skills" / PLUGIN_NAME / "SKILL.md"


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {rendered}\n{output}"
        )
    return output


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_marketplace(root: Path) -> None:
    plugin_root = root / "plugins" / PLUGIN_NAME
    shutil.copytree(
        ROOT,
        plugin_root,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".coverage",
            "htmlcov",
        ),
    )

    write_json(
        root / ".claude-plugin" / "marketplace.json",
        {
            "name": MARKETPLACE_NAME,
            "owner": {"name": "xiaodong528"},
            "plugins": [
                {
                    "name": PLUGIN_NAME,
                    "source": f"./plugins/{PLUGIN_NAME}",
                }
            ],
        },
    )
    write_json(
        root / ".agents" / "plugins" / "marketplace.json",
        {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "MobileWork Tools"},
            "plugins": [
                {
                    "name": PLUGIN_NAME,
                    "source": {
                        "source": "local",
                        "path": f"./plugins/{PLUGIN_NAME}",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        },
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_installed_skill(profile: Path) -> Path:
    cache_root = (
        profile
        / "plugins"
        / "cache"
        / MARKETPLACE_NAME
        / PLUGIN_NAME
    )
    candidates = sorted(cache_root.glob(f"*/skills/{PLUGIN_NAME}/SKILL.md"))
    if not candidates:
        raise AssertionError(f"Installed SKILL.md not found under {cache_root}")

    source_hash = sha256(SOURCE_SKILL)
    matching = [
        candidate for candidate in candidates if sha256(candidate) == source_hash
    ]
    if not matching:
        raise AssertionError(
            f"Installed SKILL.md hash does not match source under {cache_root}"
        )
    return matching[-1]


def test_claude(marketplace_root: Path, profile: Path) -> dict[str, str]:
    if shutil.which("claude") is None:
        raise RuntimeError("claude is not available on PATH")

    profile.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(profile)

    run(
        ["claude", "plugin", "marketplace", "add", str(marketplace_root)],
        cwd=ROOT,
        env=env,
    )
    run(
        [
            "claude",
            "plugin",
            "install",
            SELECTOR,
            "--scope",
            "user",
        ],
        cwd=ROOT,
        env=env,
    )
    marketplaces = run(
        ["claude", "plugin", "marketplace", "list", "--json"],
        cwd=ROOT,
        env=env,
    )
    installed = run(["claude", "plugin", "list"], cwd=ROOT, env=env)
    details = run(
        ["claude", "plugin", "details", SELECTOR],
        cwd=ROOT,
        env=env,
    )

    for expected, output in (
        (MARKETPLACE_NAME, marketplaces),
        (SELECTOR, installed),
        (VERSION, installed),
        (PLUGIN_NAME, details),
    ):
        if expected not in output:
            raise AssertionError(f"{expected!r} not found in command output")

    skill_path = assert_installed_skill(profile)
    return {
        "status": "installed",
        "version": VERSION,
        "skillPath": str(skill_path),
        "skillSha256": sha256(skill_path),
    }


def test_codex(marketplace_root: Path, profile: Path) -> dict[str, str]:
    if shutil.which("codex") is None:
        raise RuntimeError("codex is not available on PATH")

    profile.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(profile)

    run(
        [
            "codex",
            "plugin",
            "marketplace",
            "add",
            str(marketplace_root),
            "--json",
        ],
        cwd=ROOT,
        env=env,
    )
    run(
        ["codex", "plugin", "add", SELECTOR, "--json"],
        cwd=ROOT,
        env=env,
    )
    installed = run(["codex", "plugin", "list"], cwd=ROOT, env=env)
    prompt_input = run(
        [
            "codex",
            "debug",
            "prompt-input",
            "installation smoke",
        ],
        cwd=ROOT,
        env=env,
    )

    for expected, output in (
        (SELECTOR, installed),
        ("installed, enabled", installed),
        (VERSION, installed),
        (f"{PLUGIN_NAME}:{PLUGIN_NAME}", prompt_input),
    ):
        if expected not in output:
            raise AssertionError(f"{expected!r} not found in command output")

    skill_path = assert_installed_skill(profile)
    return {
        "status": "installed",
        "version": VERSION,
        "skillPath": str(skill_path),
        "skillSha256": sha256(skill_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install and discover the plugin in isolated host profiles."
    )
    parser.add_argument(
        "--host",
        choices=("all", "claude", "codex"),
        default="all",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mobilework-tools-smoke-") as temp:
        temp_root = Path(temp)
        marketplace_root = temp_root / "marketplace"
        prepare_marketplace(marketplace_root)

        results: dict[str, dict[str, str]] = {}
        if args.host in ("all", "claude"):
            results["claude"] = test_claude(
                marketplace_root,
                temp_root / "claude-profile",
            )
        if args.host in ("all", "codex"):
            results["codex"] = test_codex(
                marketplace_root,
                temp_root / "codex-profile",
            )

        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
