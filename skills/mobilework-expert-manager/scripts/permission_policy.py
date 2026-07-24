#!/usr/bin/env python3
"""Autonomy-derived permission policy for MobileWork expert roles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


ACTIONS = ("deny", "ask", "allow")
ACTION_RANK = {action: index for index, action in enumerate(ACTIONS)}
AUTONOMY_ORDER = {
    "scripted": 0,
    "fixed": 1,
    "bounded": 2,
    "guided": 3,
    "adaptive": 4,
}
BASELINES = {
    "scripted": {
        "*": "deny",
        "edit": "deny",
        "bash": "deny",
        "webfetch": "deny",
        "external_directory": "deny",
        "doom_loop": "deny",
    },
    "fixed": {
        "*": "ask",
        "edit": "ask",
        "bash": "ask",
        "webfetch": "ask",
        "external_directory": "ask",
        "doom_loop": "ask",
    },
    "bounded": {
        "*": "ask",
        "edit": "allow",
        "bash": "ask",
        "webfetch": "allow",
        "external_directory": "ask",
        "doom_loop": "ask",
    },
    "guided": {
        "*": "ask",
        "edit": "allow",
        "bash": "ask",
        "webfetch": "allow",
        "external_directory": "ask",
        "doom_loop": "ask",
    },
    "adaptive": {
        "*": "ask",
        "edit": "allow",
        "bash": "ask",
        "webfetch": "allow",
        "external_directory": "ask",
        "doom_loop": "allow",
    },
}
SENSITIVE_KEYS = ("edit", "bash", "webfetch", "external_directory", "doom_loop")
BUILTIN_PERMISSION_KEYS = frozenset(
    {
        "*",
        "read",
        "edit",
        "bash",
        "webfetch",
        "websearch",
        "external_directory",
        "doom_loop",
        "skill",
        "task",
        "glob",
        "grep",
        "list",
        "lsp",
        "question",
    }
)
UNSAFE_BASH_TOKENS = ("\n", "\r", ";", "|", "&&", "||", ">", "<", "`", "$(")


class PermissionPolicyError(ValueError):
    """Raised when explicit permission violates the derived policy."""


def tools_to_permission(raw: dict[str, Any], field: str) -> dict[str, Any]:
    """Convert the legacy manifest tools booleans into permission actions."""

    permission: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise PermissionPolicyError(f"{field} keys must be strings")
        if not isinstance(value, bool):
            raise PermissionPolicyError(f"{field}.{key} must be a boolean")
        permission["edit" if key in {"write", "patch"} else key] = (
            "allow" if value else "deny"
        )
    return permission


def _tool_name(path: str, field: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.name:
        raise PermissionPolicyError(f"{field} must be a safe relative custom tool path")
    if candidate.suffix not in {".js", ".ts"}:
        raise PermissionPolicyError(f"{field} must end with .js or .ts")
    return candidate.stem


def validate_custom_tool_ownership(
    declared_paths: Iterable[str], owned_paths: Iterable[str], field: str
) -> set[str]:
    declared: dict[str, str] = {}
    declared_path_set: set[str] = set()
    for index, path in enumerate(declared_paths):
        if not isinstance(path, str) or not path:
            raise PermissionPolicyError(f"custom tool declaration [{index}] must be non-empty")
        name = _tool_name(path, f"custom tool declaration [{index}]")
        previous = declared.get(name)
        if previous is not None and previous != path:
            raise PermissionPolicyError(
                f"custom tool name collision: {previous} and {path} both map to {name}"
            )
        declared[name] = path
        declared_path_set.add(path)

    owned_names: set[str] = set()
    seen: set[str] = set()
    for index, path in enumerate(owned_paths):
        if not isinstance(path, str) or not path:
            raise PermissionPolicyError(f"{field}[{index}] must be a non-empty string")
        if path in seen:
            raise PermissionPolicyError(f"{field}[{index}] duplicates {path}")
        seen.add(path)
        if path not in declared_path_set:
            raise PermissionPolicyError(f"{field}[{index}] references undeclared custom tool {path}")
        owned_names.add(_tool_name(path, f"{field}[{index}]"))
    return owned_names


def validate_bash_pattern(pattern: str) -> None:
    if not pattern.strip() or pattern != pattern.strip():
        raise PermissionPolicyError("programming-tool ref must be a trimmed non-empty command")
    unsafe = next((token for token in UNSAFE_BASH_TOKENS if token in pattern), None)
    if unsafe is not None:
        raise PermissionPolicyError(
            f"programming-tool ref contains forbidden shell control token {unsafe!r}"
        )


def _merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            nested = dict(result[key])
            nested.update(value)
            result[key] = nested
        else:
            result[key] = value
    return result


def _legacy_permission(
    role: dict[str, Any],
    *,
    mcp_names: list[str],
    subagent_ids: list[str],
    is_primary: bool,
) -> dict[str, Any]:
    permission: dict[str, Any] = {
        "read": "allow",
        "edit": "allow",
        "bash": {"*": "allow", "git status*": "allow", "git diff*": "allow"},
        "webfetch": "allow",
        "skill": {"*": "deny", **{skill: "allow" for skill in role["allowed_skills"]}},
        "task": {"*": "deny"},
    }
    if is_primary:
        permission["task"].update({agent_id: "allow" for agent_id in subagent_ids})
    for name in mcp_names:
        permission[f"{name}_*"] = "allow" if name in role["mcp"] else "deny"
    return permission


def _assignments_for_role(
    workflows: Iterable[dict[str, Any]], role_id: str
) -> list[tuple[str, dict[str, Any] | None]]:
    assignments: list[tuple[str, dict[str, Any] | None]] = []
    for workflow in workflows:
        if not workflow.get("contract_enabled"):
            continue
        for phase in workflow.get("phases", []):
            if role_id not in phase.get("participants", []):
                continue
            override = phase.get("agent_overrides", {}).get(role_id)
            if isinstance(override, dict):
                assignments.append((override["effective_autonomy"], override.get("execution")))
            else:
                assignments.append((phase["effective_autonomy"], phase.get("execution")))
    return assignments


def _merged_action(levels: list[str], key: str) -> str:
    actions = {BASELINES[level][key] for level in levels}
    return actions.pop() if len(actions) == 1 else "ask"


def _executor_allowlists(
    assignments: Iterable[tuple[str, dict[str, Any] | None]],
    *,
    declared_custom_tools: set[str],
    declared_mcp: set[str],
    owned_mcp: set[str],
) -> tuple[set[str], set[str], set[str]]:
    bash: set[str] = set()
    tools: set[str] = set()
    mcp_tools: set[str] = set()
    for _level, execution in assignments:
        if not isinstance(execution, dict):
            continue
        for executor in execution.get("executors", []):
            if not isinstance(executor, dict):
                continue
            kind = executor.get("kind")
            ref = executor.get("ref")
            if not isinstance(ref, str) or not ref:
                continue
            if kind == "programming-tool":
                validate_bash_pattern(ref)
                bash.add(ref)
            elif kind == "custom-tool":
                if ref not in declared_custom_tools:
                    raise PermissionPolicyError(
                        f"custom-tool executor references undeclared custom tool {ref}"
                    )
                tools.add(Path(ref).stem)
            elif kind == "mcp-tool":
                server, separator, tool = ref.partition("/")
                if separator and server and tool:
                    if server not in declared_mcp:
                        raise PermissionPolicyError(
                            f"mcp-tool executor references undeclared MCP {server}"
                        )
                    if server not in owned_mcp:
                        raise PermissionPolicyError(
                            f"mcp-tool executor references MCP not owned by role: {server}"
                        )
                    mcp_tools.add(f"{server}_{tool}")
    return bash, tools, mcp_tools


def _action_for_path(permission: dict[str, Any], key: str, pattern: str | None = None) -> str:
    value = permission.get(key, permission.get("*", "deny"))
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return "deny"
    if pattern is not None and isinstance(value.get(pattern), str):
        return value[pattern]
    wildcard = value.get("*")
    return wildcard if isinstance(wildcard, str) else "deny"


def _iter_actions(value: Any, prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], str]]:
    if isinstance(value, str):
        yield prefix, value
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _iter_actions(nested, (*prefix, key))


def _validate_explicit(
    calculated: dict[str, Any],
    explicit: dict[str, Any],
    *,
    permission_reason: str,
    allowed_skills: set[str],
    owned_mcp: set[str],
    all_mcp: set[str],
    owned_custom_tools: set[str],
    expected_task: dict[str, str],
) -> None:
    bash = explicit.get("bash")
    if bash == "allow" or isinstance(bash, dict) and bash.get("*") == "allow":
        raise PermissionPolicyError('permission.bash must not grant unconditional "*": "allow"')
    external = explicit.get("external_directory")
    if external == "allow" or isinstance(external, dict) and external.get("*") == "allow":
        raise PermissionPolicyError("permission.external_directory must not grant wildcard allow")
    task = explicit.get("task")
    if task is not None and task != expected_task:
        raise PermissionPolicyError(
            f"permission.task must equal generated task topology {expected_task}"
        )
    skill = explicit.get("skill")
    if skill is not None:
        if not isinstance(skill, dict):
            raise PermissionPolicyError("permission.skill must be a mapping")
        unknown = sorted(set(skill) - {"*", *allowed_skills})
        if unknown:
            raise PermissionPolicyError(
                f"permission.skill references undeclared skills: {', '.join(unknown)}"
            )
    for path, action in _iter_actions(explicit):
        if action not in ACTION_RANK or not path:
            continue
        key = path[0]
        pattern = path[1] if len(path) > 1 else None
        server = next(
            (name for name in sorted(all_mcp, key=len, reverse=True) if key.startswith(f"{name}_")),
            None,
        )
        if server is not None:
            if server not in owned_mcp and action != "deny":
                raise PermissionPolicyError(f"permission.{key} grants undeclared MCP {server}")
        elif key not in BUILTIN_PERMISSION_KEYS and key not in owned_custom_tools:
            if action != "deny":
                raise PermissionPolicyError(
                    f"permission.{key} grants undeclared tool or MCP capability"
                )
        baseline = _action_for_path(calculated, key, pattern)
        if ACTION_RANK[action] > ACTION_RANK[baseline] and not permission_reason.strip():
            dotted = ".".join(path)
            raise PermissionPolicyError(
                f"permission_reason is required because permission.{dotted} raises {baseline} to {action}"
            )


def build_role_permission(
    role: dict[str, Any],
    *,
    workflows: list[dict[str, Any]],
    mcp_names: list[str],
    custom_tool_paths: list[str] | None = None,
    subagent_ids: list[str],
    is_primary: bool,
    legacy_tools_permission: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return generated permission and audit metadata for one normalized role."""

    expected_task = {"*": "deny"}
    if is_primary:
        expected_task.update({agent_id: "allow" for agent_id in subagent_ids})
    explicit = role.get("permission", {})
    if not isinstance(explicit, dict):
        raise PermissionPolicyError("permission must be a mapping")
    permission_reason = role.get("permission_reason", "")
    if not isinstance(permission_reason, str):
        raise PermissionPolicyError("permission_reason must be a string")
    declared_custom_tool_paths = list(custom_tool_paths or [])
    owned_custom_tools = validate_custom_tool_ownership(
        declared_custom_tool_paths,
        role.get("custom_tools", []),
        f"{role.get('id', 'role')}.custom_tools",
    )

    contract_enabled = any(item.get("contract_enabled") for item in workflows)
    if not contract_enabled:
        permission = _legacy_permission(
            role,
            mcp_names=mcp_names,
            subagent_ids=subagent_ids,
            is_primary=is_primary,
        )
        permission = _merge_mapping(permission, legacy_tools_permission or {})
        permission = _merge_mapping(permission, explicit)
        if explicit.get("task") is not None and explicit["task"] != expected_task:
            raise PermissionPolicyError(
                f"permission.task must equal generated task topology {expected_task}"
            )
        permission["task"] = expected_task
        return permission, {
            "source": "legacy",
            "levels": [],
            "effective": "legacy",
            "warning": "legacy-permission-baseline",
            "permission_reason": permission_reason,
        }

    assignments = _assignments_for_role(workflows, role["id"])
    fallback = not assignments
    if fallback:
        assignments = [("bounded", None)]
    levels = [level for level, _execution in assignments]
    highest = max(levels, key=AUTONOMY_ORDER.__getitem__)
    permission: dict[str, Any] = {
        "*": BASELINES[highest]["*"],
        "read": {
            "*": "allow",
            ".env": "deny",
            ".env.*": "deny",
            ".env.example": "allow",
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "edit": _merged_action(levels, "edit"),
        "bash": {"*": _merged_action(levels, "bash")},
        "webfetch": _merged_action(levels, "webfetch"),
        "external_directory": {"*": _merged_action(levels, "external_directory")},
        "doom_loop": _merged_action(levels, "doom_loop"),
        "skill": {"*": "deny", **{skill: "allow" for skill in role["allowed_skills"]}},
        "task": expected_task,
    }
    owned_mcp = set(role["mcp"])
    bash_allow, tool_allow, mcp_tool_allow = _executor_allowlists(
        assignments,
        declared_custom_tools=set(declared_custom_tool_paths),
        declared_mcp=set(mcp_names),
        owned_mcp=owned_mcp,
    )
    tool_allow.update(owned_custom_tools)
    permission["bash"].update({pattern: "allow" for pattern in sorted(bash_allow)})
    permission.update({name: "allow" for name in sorted(tool_allow)})

    all_high = all(AUTONOMY_ORDER[level] >= AUTONOMY_ORDER["bounded"] for level in levels)
    for name in mcp_names:
        permission[f"{name}_*"] = "allow" if name in owned_mcp and all_high else "deny"
    permission.update({name: "allow" for name in sorted(mcp_tool_allow)})

    _validate_explicit(
        permission,
        explicit,
        permission_reason=permission_reason,
        allowed_skills=set(role["allowed_skills"]),
        owned_mcp=owned_mcp,
        all_mcp=set(mcp_names),
        owned_custom_tools=owned_custom_tools | tool_allow,
        expected_task=expected_task,
    )
    _validate_explicit(
        permission,
        legacy_tools_permission or {},
        permission_reason=permission_reason,
        allowed_skills=set(role["allowed_skills"]),
        owned_mcp=owned_mcp,
        all_mcp=set(mcp_names),
        owned_custom_tools=owned_custom_tools | tool_allow,
        expected_task=expected_task,
    )
    permission = _merge_mapping(permission, legacy_tools_permission or {})
    permission = _merge_mapping(permission, explicit)
    permission["task"] = expected_task
    if permission.get("bash") == "allow" or (
        isinstance(permission.get("bash"), dict) and permission["bash"].get("*") == "allow"
    ):
        raise PermissionPolicyError('generated permission.bash must not contain "*": "allow"')
    return permission, {
        "source": "bounded-fallback" if fallback else "workflow-autonomy",
        "levels": sorted(set(levels), key=AUTONOMY_ORDER.__getitem__),
        "effective": highest,
        "warning": "unused-role-bounded-fallback" if fallback else "",
        "permission_reason": permission_reason,
    }
