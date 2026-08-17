#!/usr/bin/env python3
"""Shared semantic contract for MobileWork expert manifests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import package_contract
import skill_contract
import workflow_autonomy


TOP_LEVEL_KEYS = frozenset(
    {
        "slug", "type", "version", "name", "summary", "description", "language",
        "profession", "category_id", "display_description", "avatar_url",
        "tags", "quick_prompts", "default_prompt", "skills", "common_skills",
        "package_resources", "runtime_extensions", "mcp_servers", "agent",
        "primary_agent", "subagents", "workflows",
    }
)
SEMVER_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")


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


def collect_runtime_name_issues(
    manifest: dict[str, Any],
) -> list[ManifestIssue]:
    """Reject package-local Command, Skill, and Agent id collisions."""

    try:
        skill_names = set(skill_contract.catalog_names(manifest))
    except package_contract.ContractError:
        # The Skill contract reports its own more specific error first.
        return []
    issues: list[ManifestIssue] = []
    agent_entries = [
        (f"{field}.id", role_id)
        for field, role in manifest_roles(manifest)
        if isinstance((role_id := role.get("id")), str)
    ]
    agent_names = {role_id for _field, role_id in agent_entries}
    command_entries: list[tuple[str, str]] = []
    runtime_extensions = manifest.get("runtime_extensions")
    if isinstance(runtime_extensions, dict):
        commands = runtime_extensions.get("commands")
        if isinstance(commands, list):
            for index, command in enumerate(commands):
                if not isinstance(command, dict):
                    continue
                name = command.get("name")
                if isinstance(name, str):
                    command_entries.append(
                        (f"runtime_extensions.commands[{index}].name", name)
                    )

    workflows = manifest.get("workflows")
    if isinstance(workflows, list):
        for index, workflow in enumerate(workflows):
            if not isinstance(workflow, dict):
                continue
            command = workflow.get("command")
            if not isinstance(command, dict):
                continue
            name = command.get("name")
            if isinstance(name, str):
                command_entries.append((f"workflows[{index}].command.name", name))

    for field, name in command_entries:
        if name in skill_names:
            issues.append(ManifestIssue(field, f"conflicts with skill {name}"))
        if name in agent_names:
            issues.append(ManifestIssue(field, f"conflicts with agent {name}"))
    for field, name in agent_entries:
        if name in skill_names:
            issues.append(ManifestIssue(field, f"conflicts with skill {name}"))
    return issues


def collect_manifest_issues(manifest: dict[str, Any]) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    unexpected = sorted(set(manifest) - TOP_LEVEL_KEYS)
    if unexpected:
        issues.append(ManifestIssue("expert.json", f"unknown fields {', '.join(unexpected)}"))
    expert_type = manifest.get("type")
    if expert_type not in {"expert", "team"}:
        return [ManifestIssue("type", "must be expert or team")]

    version = manifest.get("version")
    if version is not None and (not isinstance(version, str) or not SEMVER_RE.fullmatch(version)):
        issues.append(ManifestIssue("version", "must be SemVer X.Y.Z without a v prefix"))

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
        is_main = field in {"agent", "primary_agent"}
        autonomy = role.get("autonomy")
        if autonomy is not None and autonomy not in workflow_autonomy.AUTONOMY_LEVELS:
            issues.append(
                ManifestIssue(
                    f"{field}.autonomy",
                    "must be one of " + ", ".join(workflow_autonomy.AUTONOMY_LEVELS),
                )
            )
        if is_main:
            mode = role.get("mode", "primary" if autonomy is None else "all")
            if mode not in {"primary", "all"}:
                issues.append(ManifestIssue(f"{field}.mode", "must be primary or all"))
            expected_mode = mode if mode in {"primary", "all"} else "all"
        else:
            expected_mode = "subagent"
            if role.get("mode", expected_mode) != expected_mode:
                issues.append(ManifestIssue(f"{field}.mode", "must be subagent"))
        default_steps = 80 if field == "agent" else 150 if field == "primary_agent" else 50
        try:
            package_contract.normalize_agent_runtime_options(
                role,
                field,
                expected_mode=expected_mode,
                default_steps=default_steps,
                allow_legacy_sampling=True,
            )
        except package_contract.ContractError as exc:
            issue_field, separator, message = str(exc).partition(": ")
            issues.append(
                ManifestIssue(issue_field if separator else field, message if separator else issue_field)
            )
        role_mcp = role.get("mcp", [])
        if isinstance(role_mcp, list):
            for name in role_mcp:
                if isinstance(name, str) and name not in mcp_names:
                    issues.append(ManifestIssue(f"{field}.mcp", f"references unknown MCP server {name}"))
        for resource_field in ("references", "instructions"):
            if resource_field not in role:
                continue
            try:
                package_contract.normalize_role_aliases(
                    role.get(resource_field),
                    f"{field}.{resource_field}",
                )
            except package_contract.ContractError as exc:
                issue_field, separator, message = str(exc).partition(": ")
                issues.append(
                    ManifestIssue(
                        issue_field if separator else f"{field}.{resource_field}",
                        message if separator else issue_field,
                    )
                )

    if len(role_ids) != len(set(role_ids)):
        issues.append(ManifestIssue("roles", "agent ids must be unique"))

    runtime_extensions = manifest.get("runtime_extensions")
    runtime_extensions = runtime_extensions if isinstance(runtime_extensions, dict) else {}
    references = runtime_extensions.get("references")
    reference_aliases = set(references) if isinstance(references, dict) else set()
    role_instructions = runtime_extensions.get("role_instructions")
    instruction_aliases = set(role_instructions) if isinstance(role_instructions, dict) else set()

    explicit_reference_roles = [field for field, role in roles if "references" in role]
    if explicit_reference_roles:
        consumed_references: set[str] = set()
        for field, role in roles:
            if "references" not in role:
                issues.append(ManifestIssue(f"{field}.references", "is required in explicit binding mode"))
                continue
            try:
                values = package_contract.normalize_role_aliases(
                    role.get("references"), f"{field}.references"
                )
            except package_contract.ContractError:
                continue
            consumed_references.update(values)
            for alias in values:
                if alias not in reference_aliases:
                    issues.append(
                        ManifestIssue(f"{field}.references", f"references unknown Reference {alias}")
                    )
        for alias in sorted(reference_aliases - consumed_references):
            issues.append(
                ManifestIssue(
                    f"runtime_extensions.references.{alias}",
                    "must be assigned to at least one role",
                )
            )

    if instruction_aliases:
        consumed_instructions: set[str] = set()
        for field, role in roles:
            if "instructions" not in role:
                issues.append(ManifestIssue(f"{field}.instructions", "is required when role rules exist"))
                continue
            try:
                values = package_contract.normalize_role_aliases(
                    role.get("instructions"), f"{field}.instructions"
                )
            except package_contract.ContractError:
                continue
            consumed_instructions.update(values)
            for alias in values:
                if alias not in instruction_aliases:
                    issues.append(
                        ManifestIssue(f"{field}.instructions", f"references unknown role rule {alias}")
                    )
        for alias in sorted(instruction_aliases - consumed_instructions):
            issues.append(
                ManifestIssue(
                    f"runtime_extensions.role_instructions.{alias}",
                    "must be assigned to at least one role",
                )
            )
    else:
        for field, role in roles:
            values = role.get("instructions")
            if isinstance(values, list) and values:
                issues.append(
                    ManifestIssue(f"{field}.instructions", "requires runtime_extensions.role_instructions")
                )
    try:
        skill_contract.validate_manifest_skills(manifest)
    except package_contract.ContractError as exc:
        field, separator, message = str(exc).partition(": ")
        issues.append(
            ManifestIssue(
                field if separator else "skills",
                message if separator else field,
            )
        )
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
    else:
        issues.extend(collect_runtime_name_issues(manifest))
    return issues


def assert_manifest_contract(manifest: dict[str, Any]) -> None:
    issues = collect_manifest_issues(manifest)
    if issues:
        raise package_contract.ContractError(str(issues[0]))
