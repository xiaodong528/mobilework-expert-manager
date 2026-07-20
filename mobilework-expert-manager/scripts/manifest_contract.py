#!/usr/bin/env python3
"""Shared semantic contract for MobileWork expert manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import package_contract
import workflow_autonomy


TOP_LEVEL_KEYS = frozenset(
    {
        "slug", "type", "name", "summary", "description", "language",
        "profession", "category_id", "display_description", "avatar_url",
        "tags", "quick_prompts", "default_prompt", "common_skills",
        "package_resources", "runtime_extensions", "mcp_servers", "agent",
        "primary_agent", "subagents", "workflows",
    }
)


@dataclass(frozen=True)
class ManifestIssue:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


def manifest_roles(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    expert_type = manifest.get("type")
    if expert_type == "expert":
        role = manifest.get("agent")
        return [("agent", role)] if isinstance(role, dict) else []
    if expert_type == "team":
        roles: list[tuple[str, dict[str, Any]]] = []
        primary = manifest.get("primary_agent")
        if isinstance(primary, dict):
            roles.append(("primary_agent", primary))
        subagents = manifest.get("subagents")
        if isinstance(subagents, list):
            roles.extend(
                (f"subagents[{index}]", role)
                for index, role in enumerate(subagents)
                if isinstance(role, dict)
            )
        return roles
    return []


def collect_manifest_issues(manifest: dict[str, Any]) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    unexpected = sorted(set(manifest) - TOP_LEVEL_KEYS)
    if unexpected:
        issues.append(ManifestIssue("expert.json", f"unknown fields {', '.join(unexpected)}"))
    expert_type = manifest.get("type")
    if expert_type not in {"expert", "team"}:
        return [ManifestIssue("type", "must be expert or team")]

    if expert_type == "expert":
        if not isinstance(manifest.get("agent"), dict):
            issues.append(ManifestIssue("agent", "is required for type expert"))
        for field in ("primary_agent", "subagents"):
            if field in manifest:
                issues.append(ManifestIssue(field, "is forbidden for type expert"))
    else:
        if "agent" in manifest:
            issues.append(ManifestIssue("agent", "is forbidden for type team"))
        if not isinstance(manifest.get("primary_agent"), dict):
            issues.append(ManifestIssue("primary_agent", "is required for type team"))
        subagents = manifest.get("subagents")
        if not isinstance(subagents, list) or not subagents:
            issues.append(ManifestIssue("subagents", "must contain at least one role for type team"))

    prompts = manifest.get("quick_prompts")
    default_prompt = manifest.get("default_prompt")
    if isinstance(prompts, list) and prompts and default_prompt is not None and default_prompt != prompts[0]:
        issues.append(ManifestIssue("default_prompt", "must match quick_prompts[0]"))

    slug = manifest.get("slug")
    if isinstance(slug, str):
        try:
            package_contract.common_skill_names(slug, manifest.get("common_skills"))
        except package_contract.ContractError as exc:
            issues.append(ManifestIssue("common_skills", str(exc)))

    roles = manifest_roles(manifest)
    role_ids: list[str] = []
    mcp_names = {
        item.get("name")
        for item in manifest.get("mcp_servers", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(manifest.get("mcp_servers", []), list) else set()
    for field, role in roles:
        role_id = role.get("id")
        if isinstance(role_id, str):
            role_ids.append(role_id)
        expected_mode = "primary" if field in {"agent", "primary_agent"} else "subagent"
        if role.get("mode", expected_mode) != expected_mode:
            issues.append(ManifestIssue(f"{field}.mode", f"must be {expected_mode}"))
        default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
        try:
            package_contract.normalize_agent_runtime_options(
                role,
                field,
                expected_mode=expected_mode,
                default_steps=default_steps,
            )
        except package_contract.ContractError as exc:
            issue_field, separator, message = str(exc).partition(": ")
            issues.append(
                ManifestIssue(issue_field if separator else field, message if separator else issue_field)
            )
        if isinstance(slug, str):
            try:
                package_contract.role_skill_names(slug, role, field)
            except package_contract.ContractError as exc:
                issues.append(ManifestIssue(f"{field}.skills", str(exc)))
        role_mcp = role.get("mcp", [])
        if isinstance(role_mcp, list):
            for name in role_mcp:
                if isinstance(name, str) and name not in mcp_names:
                    issues.append(ManifestIssue(f"{field}.mcp", f"references unknown MCP server {name}"))

    if len(role_ids) != len(set(role_ids)):
        issues.append(ManifestIssue("roles", "agent ids must be unique"))
    try:
        package_contract.normalize_mcp_servers(manifest.get("mcp_servers"))
    except package_contract.ContractError as exc:
        field, separator, message = str(exc).partition(": ")
        issues.append(ManifestIssue(field if separator else "mcp_servers", message if separator else field))
    role_id_set = set(role_ids)
    primary_id = role_ids[0] if role_ids else ""
    try:
        workflow_autonomy.normalize_workflows(
            manifest,
            role_ids=role_id_set,
            primary_id=primary_id,
        )
    except workflow_autonomy.WorkflowContractError as exc:
        field, separator, message = str(exc).partition(": ")
        issues.append(ManifestIssue(field if separator else "workflows", message if separator else field))
    return issues


def assert_manifest_contract(manifest: dict[str, Any]) -> None:
    issues = collect_manifest_issues(manifest)
    if issues:
        raise package_contract.ContractError(str(issues[0]))
