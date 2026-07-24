#!/usr/bin/env python3
"""Constrained local Git and SemVer lifecycle for trusted expert source directories."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import manager_contract
import package_contract
import provenance
import validate_expert


SEMVER_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SAFE_CONFIG = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "core.pager=cat",
    "-c", "commit.gpgSign=false",
    "-c", "tag.gpgSign=false",
    "-c", "core.attributesFile=/dev/null",
    "-c", "core.quotePath=false",
)


class ExpertVcsError(ValueError):
    pass


@dataclass(frozen=True)
class VersionProposal:
    tag: str
    version: str
    bump: str
    reason: str
    baseline_tag: str
    version_pending: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "version": self.version,
            "bump": self.bump,
            "reason": self.reason,
            "baselineTag": self.baseline_tag,
            "versionPending": self.version_pending,
        }


def _env() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
    })
    return environment


def _run(root: Path, arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", *SAFE_CONFIG, "-C", str(root), *arguments]
    try:
        result = subprocess.run(
            command, text=True, capture_output=True, check=False, env=_env()
        )
    except FileNotFoundError as exc:
        raise ExpertVcsError("Git is required for expert version control") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ExpertVcsError(f"Git command failed ({arguments[0]}): {detail}")
    return result


def repository_root(root: Path) -> Path | None:
    result = _run(root, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def initialize_repository(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not (root / "expert.json").is_file():
        raise ExpertVcsError("trusted expert source must contain expert.json")
    actual = repository_root(root)
    if actual == root:
        return {"initialized": False, "repositoryRoot": str(root), "versionPending": True}
    if actual is not None and actual != root and (root / ".git").exists():
        raise ExpertVcsError(f"expert repository root mismatch: {actual}")
    _run(root, ["init", "--quiet"])
    actual = repository_root(root)
    if actual != root:
        raise ExpertVcsError(f"Git init did not create an exact expert repository root: {actual}")
    return {"initialized": True, "repositoryRoot": str(root), "versionPending": True}


def _manifest(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / "expert.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpertVcsError(f"cannot read expert.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ExpertVcsError("expert.json must be an object")
    return value


def _tags(root: Path) -> list[str]:
    result = _run(root, ["tag", "--merged", "HEAD", "--list", "v[0-9]*"], check=False)
    if result.returncode != 0:
        return []
    tags = [line.strip() for line in result.stdout.splitlines() if SEMVER_RE.fullmatch(line.strip())]
    return sorted(tags, key=lambda item: tuple(int(part) for part in item[1:].split(".")))


def last_release_tag(root: Path) -> str:
    tags = _tags(root)
    return tags[-1] if tags else ""


def _tag_manifest(root: Path, tag: str) -> dict[str, Any] | None:
    result = _run(root, ["show", f"{tag}:expert.json"], check=False)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _role_ids(manifest: dict[str, Any]) -> set[str]:
    roles = []
    if isinstance(manifest.get("agent"), dict):
        roles.append(manifest["agent"])
    if isinstance(manifest.get("primary_agent"), dict):
        roles.append(manifest["primary_agent"])
    if isinstance(manifest.get("subagents"), list):
        roles.extend(item for item in manifest["subagents"] if isinstance(item, dict))
    return {str(item["id"]) for item in roles if isinstance(item.get("id"), str)}


def _named_set(manifest: dict[str, Any], key: str) -> set[str]:
    values = manifest.get(key, [])
    if not isinstance(values, list):
        return set()
    result: set[str] = set()
    for item in values:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result.add(item["name"])
    return result


def classify_change(previous: dict[str, Any] | None, current: dict[str, Any]) -> tuple[str, str]:
    if previous is None:
        return "major", "first complete expert release"
    if previous.get("slug") != current.get("slug") or previous.get("type") != current.get("type"):
        return "major", "package identity changed"
    if not _role_ids(previous).issubset(_role_ids(current)):
        return "major", "a role was removed or renamed"
    old_workflows = _named_set(previous, "workflows")
    new_workflows = _named_set(current, "workflows")
    if not old_workflows.issubset(new_workflows):
        return "major", "a workflow was removed or renamed"
    old_runtime = previous.get("runtime_extensions", {})
    new_runtime = current.get("runtime_extensions", {})
    old_commands = _named_set(old_runtime if isinstance(old_runtime, dict) else {}, "commands")
    new_commands = _named_set(new_runtime if isinstance(new_runtime, dict) else {}, "commands")
    if not old_commands.issubset(new_commands):
        return "major", "a command was removed or renamed"
    if _role_ids(previous) != _role_ids(current) or old_workflows != new_workflows or old_commands != new_commands:
        return "minor", "compatible roles, workflows, or commands were added"
    structural_keys = ("common_skills", "mcp_servers", "runtime_extensions", "package_resources")
    if any(previous.get(key) != current.get(key) for key in structural_keys):
        return "minor", "capability or owned resource contract changed; user review is required"
    previous_roles = {role.get("id"): role for role in _roles(previous)}
    ownership_keys = ("permission", "custom_tools", "mcp", "skills")
    if any(
        any(
            role.get(key) != previous_roles.get(role.get("id"), {}).get(key)
            for key in ownership_keys
        )
        for role in _roles(current)
    ):
        return "minor", "role permission or capability ownership changed; user review is required"
    return "patch", "compatible content or derived-file correction"


def _roles(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in ("agent", "primary_agent"):
        value = manifest.get(key)
        if isinstance(value, dict):
            result.append(value)
    values = manifest.get("subagents", [])
    if isinstance(values, list):
        result.extend(item for item in values if isinstance(item, dict))
    return result


def _bump(tag: str, level: str) -> str:
    if not tag:
        return "1.0.0"
    match = SEMVER_RE.fullmatch(tag)
    if match is None:
        raise ExpertVcsError(f"invalid release tag {tag}")
    major, minor, patch = (int(value) for value in match.groups())
    if level == "major":
        major, minor, patch = major + 1, 0, 0
    elif level == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def propose_version(root: Path) -> VersionProposal:
    root = root.expanduser().resolve()
    if repository_root(root) != root:
        raise ExpertVcsError("expert source is not an exact local Git repository")
    tag = last_release_tag(root)
    current = _manifest(root)
    previous = _tag_manifest(root, tag) if tag else None
    level, reason = classify_change(previous, current)
    version = _bump(tag, level)
    return VersionProposal(f"v{version}", version, level, reason, tag, True)


def _identity(root: Path) -> tuple[str, str]:
    values: list[str] = []
    for key in ("user.name", "user.email"):
        local = _run(root, ["config", "--local", "--get", key], check=False)
        if local.returncode == 0 and local.stdout.strip():
            values.append(local.stdout.strip())
            continue
        global_value = _run(root, ["config", "--global", "--get", key], check=False)
        if global_value.returncode != 0 or not global_value.stdout.strip():
            raise ExpertVcsError(f"Git identity {key} is missing; configure it explicitly before release")
        values.append(global_value.stdout.strip())
    return values[0], values[1]


def _status(root: Path) -> list[tuple[str, str]]:
    output = _run(root, ["status", "--porcelain=v1", "--untracked-files=all"]).stdout
    result: list[tuple[str, str]] = []
    for line in output.splitlines():
        if len(line) >= 4:
            path = line[3:].split(" -> ")[-1]
            result.append((line[:2], path))
    return result


def _owned_paths(root: Path, manifest: dict[str, Any]) -> set[str]:
    declared = package_contract.declared_package_files(manifest)
    tracked = _run(root, ["ls-files"], check=False)
    for path in tracked.stdout.splitlines():
        relative = Path(path)
        if path and ".git" not in relative.parts and package_contract.is_allowed_package_path(relative):
            declared.add(path)
    return declared


def _normalize_release_version(value: str) -> tuple[str, str]:
    match = SEMVER_RE.fullmatch(value.strip())
    if match is None:
        raise ExpertVcsError("release version must be SemVer X.Y.Z or vX.Y.Z")
    version = ".".join(match.groups())
    return version, f"v{version}"


def _require_attached_branch(root: Path) -> None:
    branch = _run(root, ["symbolic-ref", "--quiet", "HEAD"], check=False)
    if branch.returncode != 0 or not branch.stdout.strip().startswith("refs/heads/"):
        raise ExpertVcsError("detached HEAD is not allowed for an expert release")


def _reject_repository_attributes(root: Path) -> None:
    unsafe = [root / ".gitattributes", root / ".git" / "info" / "attributes"]
    present = [str(path.relative_to(root)) for path in unsafe if path.exists()]
    if present:
        raise ExpertVcsError(
            "repository attributes or filters are not allowed during release: "
            + ", ".join(present)
        )


def _verify_release_readback(
    root: Path,
    *,
    tag: str,
    version: str,
    commit: str,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    tag_commit = _run(root, ["rev-list", "-n", "1", tag]).stdout.strip()
    if tag_commit != commit:
        raise ExpertVcsError("release tag does not point to the release commit")
    tagged_manifest = _tag_manifest(root, tag)
    if tagged_manifest is None or tagged_manifest.get("version") != version:
        raise ExpertVcsError("tagged expert.json version does not match the requested release")
    annotation = _run(
        root,
        ["for-each-ref", "--format=%(contents)", f"refs/tags/{tag}"],
    ).stdout
    if f"expertJsonSha256: {expected_manifest_hash}" not in annotation:
        raise ExpertVcsError("annotated tag does not contain the verified expert.json hash")
    if f"version: {version}" not in annotation:
        raise ExpertVcsError("annotated tag does not contain the verified version")
    return {
        "tagCommit": tag_commit,
        "manifestVersion": tagged_manifest["version"],
        "expertJsonSha256": expected_manifest_hash,
    }


def _unstage_release_paths(root: Path, paths: list[str]) -> None:
    head = _run(root, ["rev-parse", "--verify", "HEAD"], check=False)
    if head.returncode == 0:
        _run(root, ["restore", "--staged", "--", *paths], check=False)
    else:
        _run(
            root,
            ["rm", "--cached", "-r", "--ignore-unmatch", "--", *paths],
            check=False,
        )
        remaining = _run(root, ["diff", "--cached", "--name-only"], check=False)
        if remaining.stdout.strip():
            # The release preflight proved the unborn index was empty before our add.
            # Clearing only that index cannot discard a user's pre-staged state.
            _run(root, ["read-tree", "--empty"])


def release(root: Path, version_value: str) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if repository_root(root) != root:
        raise ExpertVcsError("expert source is not an exact local Git repository")
    version, tag = _normalize_release_version(version_value)
    _require_attached_branch(root)
    _reject_repository_attributes(root)
    _identity(root)
    status = _status(root)
    if any(code[0] not in {" ", "?"} for code, _path in status):
        raise ExpertVcsError("Git index contains pre-staged changes; release is blocked")
    if _run(root, ["rev-parse", "--verify", f"refs/tags/{tag}"], check=False).returncode == 0:
        raise ExpertVcsError(f"release tag already exists: {tag}")

    manifest = _manifest(root)
    owned = _owned_paths(root, manifest)
    unknown = sorted(path for code, path in status if code == "??" and path not in owned)
    if unknown:
        raise ExpertVcsError("unowned untracked files block release: " + ", ".join(unknown))
    original = (root / "expert.json").read_bytes()
    manifest["version"] = version
    (root / "expert.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = validate_expert.validate_package(root)
    if not validation.ok:
        (root / "expert.json").write_bytes(original)
        raise ExpertVcsError("release manifest failed validation: " + "; ".join(validation.errors[:5]))

    owned = _owned_paths(root, manifest)
    deleted_owned = {path for code, path in status if code.strip() == "D" and path in owned}
    stage = sorted(
        path
        for path in owned
        if path != ".git" and ((root / path).exists() or path in deleted_owned)
    )
    if not stage:
        (root / "expert.json").write_bytes(original)
        raise ExpertVcsError("release has no package-owned changes to commit")
    _run(root, ["add", "--", *stage])
    try:
        _run(root, ["commit", "--no-verify", "-m", f"chore(release): {tag}"])
    except ExpertVcsError:
        (root / "expert.json").write_bytes(original)
        _unstage_release_paths(root, stage)
        raise
    commit = _run(root, ["rev-parse", "HEAD"]).stdout.strip()
    manifest_hash = provenance.file_sha256(root / "expert.json")
    annotation = (
        f"slug: {manifest.get('slug')}\nversion: {version}\n"
        f"expertJsonSha256: {manifest_hash}\n"
        f"contractVersion: {manager_contract.load_policy()['contractVersion']}\n"
        "evidenceLevel: valid"
    )
    tag_result = _run(root, ["tag", "-a", tag, "-m", annotation], check=False)
    if tag_result.returncode != 0:
        return {
            "ok": False, "status": "release-incomplete", "version": version,
            "tag": tag, "commit": commit, "retryable": True,
            "message": (tag_result.stderr or tag_result.stdout).strip(),
        }
    try:
        readback = _verify_release_readback(
            root,
            tag=tag,
            version=version,
            commit=commit,
            expected_manifest_hash=manifest_hash,
        )
    except ExpertVcsError as error:
        return {
            "ok": False,
            "status": "release-incomplete",
            "version": version,
            "tag": tag,
            "commit": commit,
            "retryable": False,
            "message": str(error),
        }
    return {
        "ok": True, "status": "released", "version": version, "tag": tag,
        "commit": commit, **readback,
        "workingTree": _status(root),
    }
