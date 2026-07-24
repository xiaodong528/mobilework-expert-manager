#!/usr/bin/env python3
"""Deterministic workflow autonomy contract shared by generator and validator."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Iterable

import package_contract
import permission_policy


AUTONOMY_LEVELS = ("scripted", "fixed", "bounded", "guided", "adaptive")
AUTONOMY_RANK = {name: index for index, name in enumerate(AUTONOMY_LEVELS, start=1)}
AUTONOMY_LABELS = {
    "scripted": "极低：全程照脚本执行，不能自行换方法",
    "fixed": "低：按固定步骤执行，只能处理预设分支",
    "bounded": "中：可在明确边界内选择方法",
    "guided": "高：可根据目标灵活安排，但关键决定需确认",
    "adaptive": "极高：可自主规划、调整和返工，仍受安全与验收标准约束",
}
AUTONOMY_PREFIXES = {
    "scripted": "【自主度：极低】",
    "fixed": "【自主度：低】",
    "bounded": "【自主度：中】",
    "guided": "【自主度：高】",
    "adaptive": "【自主度：极高】",
}
AUTONOMY_BOUNDARIES = {
    "scripted": "只能按顺序调用声明的确定性执行器；禁止临时写替代代码、口算、目测或纯文字替代执行。",
    "fixed": "只能执行已确认 SOP、分支和重试规则；禁止发明新方法或更换执行器。",
    "bounded": "只能在声明的执行器、方法和标准范围内选择；禁止越出允许清单。",
    "guided": "可以探索，但关键方案、例外处理和高影响决定必须先确认。",
    "adaptive": "可以在职责、权限和验收标准内自主规划；不得绕过权威脚本、安全规则或质量门。",
}
EXECUTOR_KINDS = {
    "skill-script",
    "custom-tool",
    "mcp-tool",
    "programming-tool",
    "agent",
}
WORKFLOW_KEYS = frozenset({"name", "trigger", "autonomy", "command", "phases"})
PHASE_KEYS = frozenset(
    {
        "name", "mode", "agents", "input", "expected_output", "acceptance",
        "autonomy", "autonomy_reason", "execution", "agent_overrides",
    }
)
EXECUTION_KEYS = frozenset({"executors", "standards"})
AGENT_OVERRIDE_KEYS = frozenset({"autonomy", "execution", "reason"})
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkflowContractError(ValueError):
    """Raised when a workflow autonomy contract is invalid."""


def autonomy_label(level: str) -> str:
    return f"{AUTONOMY_LABELS[level]} (`{level}`)"


def autonomy_prefix(level: str) -> str:
    return AUTONOMY_PREFIXES[level]


def workflow_command_description(workflow: dict[str, Any]) -> str:
    command = workflow["command"]
    if command is None:
        raise WorkflowContractError(
            f"workflow {workflow['name']}: does not declare a command"
        )
    return f"{autonomy_prefix(workflow['autonomy'])} {command['description']}"


def _text(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        raise WorkflowContractError(f"{field}: must be a string")
    if required and not value.strip():
        raise WorkflowContractError(f"{field}: must be non-empty")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowContractError(f"{field}: must be a list of strings")
    return value


def _autonomy(value: Any, field: str) -> str:
    if value not in AUTONOMY_RANK:
        raise WorkflowContractError(
            f"{field}: must be one of {', '.join(AUTONOMY_LEVELS)}"
        )
    return value


def _execution(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowContractError(f"{field}: must be a mapping")
    unknown = sorted(set(value) - EXECUTION_KEYS)
    if unknown:
        raise WorkflowContractError(f"{field}: unsupported fields: {', '.join(unknown)}")
    raw_executors = value.get("executors", [])
    if not isinstance(raw_executors, list):
        raise WorkflowContractError(f"{field}.executors: must be a list")
    executors: list[dict[str, str]] = []
    for index, item in enumerate(raw_executors):
        item_field = f"{field}.executors[{index}]"
        if not isinstance(item, dict):
            raise WorkflowContractError(f"{item_field}: must be a mapping")
        if set(item) != {"kind", "ref"}:
            raise WorkflowContractError(f"{item_field}: must contain only kind and ref")
        kind = item.get("kind")
        if kind not in EXECUTOR_KINDS:
            raise WorkflowContractError(
                f"{item_field}.kind: must be one of {', '.join(sorted(EXECUTOR_KINDS))}"
            )
        ref = _text(item.get("ref"), f"{item_field}.ref", required=True)
        executors.append({"kind": kind, "ref": ref})
    return {
        "executors": executors,
        "standards": _strings(value.get("standards"), f"{field}.standards"),
    }


def _require_execution(level: str, execution: dict[str, Any] | None, field: str) -> None:
    if level == "adaptive":
        return
    if execution is None:
        raise WorkflowContractError(f"{field}: {level} requires execution")
    if not execution["standards"]:
        raise WorkflowContractError(f"{field}.standards: {level} requires process standards")
    if level in {"scripted", "fixed", "bounded"} and not execution["executors"]:
        raise WorkflowContractError(f"{field}.executors: {level} requires at least one executor")
    if level == "scripted" and any(
        item["kind"] == "agent" for item in execution["executors"]
    ):
        raise WorkflowContractError(f"{field}.executors: scripted must not use agent")


def _normalize_command(value: Any, field: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowContractError(f"{field}: must be a mapping")
    unknown = sorted(set(value) - {"name", "description"})
    if unknown:
        raise WorkflowContractError(f"{field}: unsupported fields: {', '.join(unknown)}")
    name = _text(value.get("name"), f"{field}.name", required=True)
    if not NAME_RE.fullmatch(name) or len(name) > 64:
        raise WorkflowContractError(f"{field}.name: must be lowercase-hyphen and at most 64 characters")
    description = _text(value.get("description"), f"{field}.description", required=True)
    if description.lstrip().startswith("【自主度："):
        raise WorkflowContractError(
            f"{field}.description: must not start with reserved generated prefix 【自主度："
        )
    return {"name": name, "description": description}


def normalize_workflows(
    manifest: dict[str, Any],
    *,
    role_ids: set[str],
    primary_id: str,
) -> list[dict[str, Any]]:
    raw_workflows = manifest.get("workflows", []) or []
    if not isinstance(raw_workflows, list):
        raise WorkflowContractError("workflows: must be a list")
    normalized: list[dict[str, Any]] = []
    command_names: set[str] = set()
    runtime = manifest.get("runtime_extensions", {}) or {}
    if isinstance(runtime, dict):
        commands = runtime.get("commands", []) or []
        if isinstance(commands, list):
            command_names.update(
                item["name"]
                for item in commands
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )

    for workflow_index, raw_workflow in enumerate(raw_workflows):
        workflow_field = f"workflows[{workflow_index}]"
        if not isinstance(raw_workflow, dict):
            raise WorkflowContractError(f"{workflow_field}: must be a mapping")
        unknown_workflow = sorted(set(raw_workflow) - WORKFLOW_KEYS)
        if unknown_workflow:
            raise WorkflowContractError(
                f"{workflow_field}: unsupported fields: {', '.join(unknown_workflow)}"
            )
        name = _text(
            raw_workflow.get("name"),
            f"{workflow_field}.name",
            default=f"Workflow {workflow_index + 1}",
        )
        trigger = _text(raw_workflow.get("trigger"), f"{workflow_field}.trigger")
        enabled = "autonomy" in raw_workflow
        if not enabled and any(key in raw_workflow for key in ("command",)):
            raise WorkflowContractError(
                f"{workflow_field}.autonomy: is required when workflow command is declared"
            )
        workflow_level = (
            _autonomy(raw_workflow.get("autonomy"), f"{workflow_field}.autonomy")
            if enabled
            else None
        )
        command = _normalize_command(raw_workflow.get("command"), f"{workflow_field}.command")
        if command:
            if command["name"] in command_names:
                raise WorkflowContractError(
                    f"{workflow_field}.command.name: conflicts with command {command['name']}"
                )
            command_names.add(command["name"])

        raw_phases = raw_workflow.get("phases", [])
        if not isinstance(raw_phases, list):
            raise WorkflowContractError(f"{workflow_field}.phases: must be a list")
        if enabled and not raw_phases:
            raise WorkflowContractError(f"{workflow_field}.phases: autonomy requires at least one phase")
        phases: list[dict[str, Any]] = []
        for phase_index, raw_phase in enumerate(raw_phases):
            phase_field = f"{workflow_field}.phases[{phase_index}]"
            if not isinstance(raw_phase, dict):
                raise WorkflowContractError(f"{phase_field}: must be a mapping")
            unknown_phase = sorted(set(raw_phase) - PHASE_KEYS)
            if unknown_phase:
                raise WorkflowContractError(
                    f"{phase_field}: unsupported fields: {', '.join(unknown_phase)}"
                )
            mode = raw_phase.get("mode", "serial")
            if mode not in {"primary", "serial", "parallel"}:
                raise WorkflowContractError(
                    f"{phase_field}.mode: must be primary, serial, or parallel"
                )
            agents = _strings(raw_phase.get("agents"), f"{phase_field}.agents")
            for agent_id in agents:
                if agent_id not in role_ids:
                    raise WorkflowContractError(
                        f"{phase_field}.agents: references unknown agent {agent_id}"
                    )
            new_phase_fields = {
                "autonomy",
                "autonomy_reason",
                "execution",
                "agent_overrides",
            }
            if not enabled and any(key in raw_phase for key in new_phase_fields):
                raise WorkflowContractError(
                    f"{phase_field}.autonomy: workflow.autonomy is required before phase autonomy fields"
                )
            acceptance = _strings(raw_phase.get("acceptance"), f"{phase_field}.acceptance")
            participants = agents or [primary_id]
            phase_level = workflow_level
            phase_reason = ""
            execution = None
            overrides: dict[str, dict[str, Any]] = {}
            if enabled and workflow_level is not None:
                if "autonomy" in raw_phase:
                    phase_level = _autonomy(raw_phase.get("autonomy"), f"{phase_field}.autonomy")
                phase_reason = _text(
                    raw_phase.get("autonomy_reason"),
                    f"{phase_field}.autonomy_reason",
                )
                if (
                    phase_level is not None
                    and AUTONOMY_RANK[phase_level] > AUTONOMY_RANK[workflow_level]
                    and not phase_reason.strip()
                ):
                    raise WorkflowContractError(
                        f"{phase_field}.autonomy_reason: required when phase raises autonomy"
                    )
                execution = _execution(raw_phase.get("execution"), f"{phase_field}.execution")
                if not acceptance:
                    raise WorkflowContractError(
                        f"{phase_field}.acceptance: autonomy requires at least one acceptance criterion"
                    )
                _require_execution(phase_level, execution, f"{phase_field}.execution")
                raw_overrides = raw_phase.get("agent_overrides", {}) or {}
                if not isinstance(raw_overrides, dict):
                    raise WorkflowContractError(f"{phase_field}.agent_overrides: must be a mapping")
                for agent_id, raw_override in raw_overrides.items():
                    override_field = f"{phase_field}.agent_overrides.{agent_id}"
                    if agent_id not in participants:
                        raise WorkflowContractError(
                            f"{override_field}: agent must participate in this phase"
                        )
                    if not isinstance(raw_override, dict):
                        raise WorkflowContractError(f"{override_field}: must be a mapping")
                    unknown = sorted(set(raw_override) - AGENT_OVERRIDE_KEYS)
                    if unknown:
                        raise WorkflowContractError(
                            f"{override_field}: unsupported fields: {', '.join(unknown)}"
                        )
                    if not raw_override:
                        raise WorkflowContractError(f"{override_field}: must not be empty")
                    override_level = phase_level
                    if "autonomy" in raw_override:
                        override_level = _autonomy(
                            raw_override.get("autonomy"),
                            f"{override_field}.autonomy",
                        )
                    reason = _text(raw_override.get("reason"), f"{override_field}.reason")
                    if (
                        override_level is not None
                        and phase_level is not None
                        and AUTONOMY_RANK[override_level] > AUTONOMY_RANK[phase_level]
                        and not reason.strip()
                    ):
                        raise WorkflowContractError(
                            f"{override_field}.reason: required when Agent override raises autonomy"
                        )
                    override_execution = (
                        _execution(raw_override.get("execution"), f"{override_field}.execution")
                        if "execution" in raw_override
                        else execution
                    )
                    _require_execution(
                        override_level,
                        override_execution,
                        f"{override_field}.execution",
                    )
                    overrides[agent_id] = {
                        "autonomy": raw_override.get("autonomy"),
                        "effective_autonomy": override_level,
                        "reason": reason,
                        "execution": override_execution,
                        "declares_execution": "execution" in raw_override,
                    }
            phases.append(
                {
                    "name": _text(
                        raw_phase.get("name"),
                        f"{phase_field}.name",
                        default=f"Phase {phase_index + 1}",
                    ),
                    "mode": mode,
                    "agents": agents,
                    "participants": participants,
                    "input": _text(raw_phase.get("input"), f"{phase_field}.input"),
                    "expected_output": _text(
                        raw_phase.get("expected_output"),
                        f"{phase_field}.expected_output",
                    ),
                    "acceptance": acceptance,
                    "autonomy": raw_phase.get("autonomy") if enabled else None,
                    "effective_autonomy": phase_level,
                    "autonomy_reason": phase_reason,
                    "execution": execution,
                    "agent_overrides": overrides,
                }
            )
        normalized.append(
            {
                "name": name,
                "trigger": trigger,
                "contract_enabled": enabled,
                "autonomy": workflow_level,
                "command": command,
                "phases": phases,
            }
        )

    _validate_executor_references(manifest, normalized, role_ids=role_ids)
    return normalized


def _raw_roles(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("type") == "expert":
        role = manifest.get("agent")
        return [role] if isinstance(role, dict) else []
    roles: list[dict[str, Any]] = []
    primary = manifest.get("primary_agent")
    if isinstance(primary, dict):
        roles.append(primary)
    subagents = manifest.get("subagents", [])
    if isinstance(subagents, list):
        roles.extend(item for item in subagents if isinstance(item, dict))
    return roles


def _declared_skill_names(manifest: dict[str, Any]) -> set[str]:
    slug = manifest.get("slug")
    if not isinstance(slug, str):
        return set()
    try:
        names = set(package_contract.common_skill_names(slug, manifest.get("common_skills")))
        for index, role in enumerate(_raw_roles(manifest)):
            names.update(package_contract.role_skill_names(slug, role, f"roles[{index}]"))
    except package_contract.ContractError:
        return set()
    return names


def _permission_denies(role: dict[str, Any], *keys: str) -> bool:
    for section_name in ("permission", "tools"):
        section = role.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for key in keys:
            value = section.get(key)
            if value == "deny" or value is False:
                return True
    return False


def _skill_permission_denies(role: dict[str, Any], skill_name: str) -> bool:
    permission = role.get("permission", {})
    if not isinstance(permission, dict):
        return False
    skill = permission.get("skill", {})
    return isinstance(skill, dict) and skill.get(skill_name) == "deny"


def iter_assignments(
    workflow: dict[str, Any],
) -> Iterable[tuple[int, dict[str, Any], str, str, dict[str, Any] | None]]:
    for phase_index, phase in enumerate(workflow["phases"]):
        if not workflow["contract_enabled"]:
            continue
        for agent_id in phase["participants"]:
            override = phase["agent_overrides"].get(agent_id)
            if override:
                yield phase_index, phase, agent_id, override["effective_autonomy"], override["execution"]
            else:
                yield phase_index, phase, agent_id, phase["effective_autonomy"], phase["execution"]


def _validate_executor_references(
    manifest: dict[str, Any],
    workflows: list[dict[str, Any]],
    *,
    role_ids: set[str],
) -> None:
    roles = {
        role["id"]: role
        for role in _raw_roles(manifest)
        if isinstance(role.get("id"), str)
    }
    skills = _declared_skill_names(manifest)
    resources = {
        item["path"]
        for item in manifest.get("package_resources", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    } if isinstance(manifest.get("package_resources", []), list) else set()
    runtime = manifest.get("runtime_extensions", {}) or {}
    custom_tools = set()
    if isinstance(runtime, dict) and isinstance(runtime.get("custom_tools", []), list):
        custom_tools = {
            item["path"]
            for item in runtime.get("custom_tools", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
    mcp_names = {
        item["name"]
        for item in manifest.get("mcp_servers", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(manifest.get("mcp_servers", []), list) else set()

    for workflow_index, workflow in enumerate(workflows):
        for phase_index, phase, agent_id, _level, execution in iter_assignments(workflow):
            role = roles.get(agent_id, {})
            if execution is None:
                continue
            for executor_index, executor in enumerate(execution["executors"]):
                field = (
                    f"workflows[{workflow_index}].phases[{phase_index}].execution"
                    f".executors[{executor_index}]"
                )
                kind = executor["kind"]
                ref = executor["ref"]
                if kind == "skill-script":
                    skill_name, separator, relative = ref.partition(":")
                    expected = f".opencode/skills/{skill_name}/{relative}"
                    if (
                        not separator
                        or skill_name not in skills
                        or not relative.startswith("scripts/")
                        or expected not in resources
                    ):
                        raise WorkflowContractError(
                            f"{field}.ref: skill-script must reference a declared supplemental skill script"
                        )
                    if _skill_permission_denies(role, skill_name):
                        raise WorkflowContractError(
                            f"{field}.ref: Agent {agent_id} permission denies skill {skill_name}"
                        )
                elif kind == "custom-tool":
                    if ref not in custom_tools:
                        raise WorkflowContractError(
                            f"{field}.ref: custom-tool must reference runtime_extensions.custom_tools path"
                        )
                    tool_name = Path(ref).stem
                    if _permission_denies(role, ref, tool_name):
                        raise WorkflowContractError(
                            f"{field}.ref: Agent {agent_id} permission denies custom tool {tool_name}"
                        )
                elif kind == "mcp-tool":
                    mcp_name, separator, tool_name = ref.partition("/")
                    role_mcp = role.get("mcp", [])
                    if (
                        not separator
                        or not tool_name
                        or mcp_name not in mcp_names
                        or not isinstance(role_mcp, list)
                        or mcp_name not in role_mcp
                    ):
                        raise WorkflowContractError(
                            f"{field}.ref: mcp-tool must reference an MCP owned by Agent {agent_id}"
                        )
                elif kind == "programming-tool":
                    try:
                        permission_policy.validate_bash_pattern(ref)
                        tokens = shlex.split(ref)
                    except (PermissionError, ValueError, permission_policy.PermissionPolicyError) as exc:
                        raise WorkflowContractError(f"{field}.ref: unsafe programming-tool pattern: {exc}") from exc
                    referenced_resources = {token for token in tokens if token in resources}
                    if not referenced_resources:
                        raise WorkflowContractError(
                            f"{field}.ref: programming-tool must reference a declared package resource"
                        )
                    if _permission_denies(role, "bash", ref):
                        raise WorkflowContractError(
                            f"{field}.ref: Agent {agent_id} permission denies programming tool bash"
                        )
                elif kind == "agent" and ref not in role_ids:
                    raise WorkflowContractError(f"{field}.ref: references unknown agent {ref}")


def has_autonomy_contract(workflows: Iterable[dict[str, Any]]) -> bool:
    return any(workflow.get("contract_enabled") for workflow in workflows)


def _executor_lines(execution: dict[str, Any] | None, indent: str = "") -> list[str]:
    if execution is None:
        return [f"{indent}- 执行器：未限定（仅适用于 adaptive）。", f"{indent}- 执行标准：依照职责、权限和验收标准。"]
    lines = [f"{indent}- 执行器："]
    if execution["executors"]:
        lines.extend(
            f"{indent}  - `{item['kind']}` → `{item['ref']}`"
            for item in execution["executors"]
        )
    else:
        lines.append(f"{indent}  - 未指定；按 guided 的确认点或 adaptive 边界执行。")
    lines.append(f"{indent}- 执行标准：")
    lines.extend(f"{indent}  - {item}" for item in execution["standards"])
    return lines


def _command_agent_lines(phase: dict[str, Any], indent: str = "   ") -> list[str]:
    lines = [f"{indent}- 参与 Agent："]
    for agent_id in phase["participants"]:
        override = phase["agent_overrides"].get(agent_id)
        declares_autonomy = override is not None and override["autonomy"] is not None
        declares_execution = override is not None and override["declares_execution"]
        level = (
            override["effective_autonomy"]
            if declares_autonomy
            else phase["effective_autonomy"]
        )
        lines.append(f"{indent}  - Agent `{agent_id}`：{autonomy_prefix(level)}")
        lines.append(f"{indent}    - 生效自主度：{autonomy_label(level)}")
        lines.append(
            f"{indent}    - 自主度来源："
            + ("Agent override" if declares_autonomy else "Phase")
        )
        lines.append(
            f"{indent}    - execution 来源："
            + ("Agent override" if declares_execution else "Phase")
        )
        if override is not None and override["reason"]:
            lines.append(f"{indent}    - 提高原因：{override['reason']}")
        if declares_execution and override is not None:
            lines.extend(_executor_lines(override["execution"], f"{indent}    "))
    return lines


def render_workflow(
    workflow: dict[str, Any],
    *,
    include_command: bool = True,
    command_view: bool = False,
) -> str:
    lines = [f"### {workflow['name']}"]
    if workflow["trigger"]:
        lines.append(f"触发条件：{workflow['trigger']}")
    if not workflow["contract_enabled"]:
        if not workflow["phases"]:
            lines.append("- 未声明显式阶段；按角色工作流程和质量门控执行。")
        else:
            for index, phase in enumerate(workflow["phases"], start=1):
                agents = ", ".join(
                    f"`task.subagent_type={item}`" for item in phase["agents"]
                ) or "团长"
                lines.append(f"{index}. **{phase['name']}**（{phase['mode']}）-> {agents}")
                if phase["input"]:
                    lines.append(f"   - 输入：{phase['input']}")
                if phase["expected_output"]:
                    lines.append(f"   - 预期产物：{phase['expected_output']}")
                if phase["acceptance"]:
                    lines.append("   - 验收标准：")
                    lines.extend(f"     - {item}" for item in phase["acceptance"])
        return "\n".join(lines)

    lines.append(f"- Workflow 默认自主度：{autonomy_label(workflow['autonomy'])}")
    lines.append(f"- 通俗边界：{AUTONOMY_BOUNDARIES[workflow['autonomy']]}")
    if include_command:
        command = workflow["command"]
        lines.append(
            f"- Command：`/{command['name']}`（{command['description']}）"
            if command
            else "- Command：未配置；本 workflow 只能由其他入口触发。"
        )
    for index, phase in enumerate(workflow["phases"], start=1):
        declared = phase["autonomy"] or "继承 workflow"
        participants = ", ".join(f"`{item}`" for item in phase["participants"])
        phase_title = (
            f"{autonomy_prefix(phase['effective_autonomy'])} {phase['name']}"
            if command_view
            else phase["name"]
        )
        lines.append(f"{index}. **{phase_title}**（{phase['mode']}）→ {participants}")
        lines.append(f"   - 声明自主度：`{declared}`")
        lines.append(f"   - Phase 生效自主度：{autonomy_label(phase['effective_autonomy'])}")
        if phase["autonomy_reason"]:
            lines.append(f"   - 提高原因：{phase['autonomy_reason']}")
        if command_view:
            lines.extend(_command_agent_lines(phase))
        if phase["input"]:
            lines.append(f"   - 输入：{phase['input']}")
        if phase["expected_output"]:
            lines.append(f"   - 预期产物：{phase['expected_output']}")
        lines.extend(_executor_lines(phase["execution"], "   "))
        lines.append("   - 验收标准：")
        lines.extend(f"     - {item}" for item in phase["acceptance"])
        if not command_view:
            for agent_id, override in phase["agent_overrides"].items():
                declared_override = override["autonomy"] or "继承 phase"
                lines.append(f"   - Agent override `{agent_id}`：")
                lines.append(f"     - 声明自主度：`{declared_override}`")
                lines.append(
                    f"     - 生效自主度：{autonomy_label(override['effective_autonomy'])}"
                )
                if override["reason"]:
                    lines.append(f"     - 提高原因：{override['reason']}")
                lines.append(
                    "     - 执行合同：覆盖 phase execution"
                    if override["declares_execution"]
                    else "     - 执行合同：继承 phase execution"
                )
                if override["declares_execution"]:
                    lines.extend(_executor_lines(override["execution"], "     "))
    return "\n".join(lines)


def render_all_workflows(workflows: Iterable[dict[str, Any]]) -> str:
    return "\n\n".join(render_workflow(workflow) for workflow in workflows)


def render_role_workflows(workflows: Iterable[dict[str, Any]], role_id: str) -> str:
    sections: list[str] = []
    for workflow in workflows:
        if not workflow["contract_enabled"]:
            continue
        matching = [phase for phase in workflow["phases"] if role_id in phase["participants"]]
        if not matching:
            continue
        lines = [f"### {workflow['name']}"]
        for phase in matching:
            override = phase["agent_overrides"].get(role_id)
            level = override["effective_autonomy"] if override else phase["effective_autonomy"]
            execution = override["execution"] if override else phase["execution"]
            lines.append(f"- **{phase['name']}**：{autonomy_label(level)}")
            lines.append(f"  - 通俗边界：{AUTONOMY_BOUNDARIES[level]}")
            lines.extend(_executor_lines(execution, "  "))
            lines.append("  - 验收标准：")
            lines.extend(f"    - {item}" for item in phase["acceptance"])
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def render_workflow_command(workflow: dict[str, Any]) -> str:
    lines = [
        f"# {workflow['name']}",
        "",
        "用户要求：$ARGUMENTS",
        "",
        "同时使用本次调用中可访问的图片、PDF、文档或其他附件；不要假设存在未声明资源。",
        "",
        "## 自主度与执行合同",
        "",
        render_workflow(workflow, include_command=False, command_view=True),
        "",
        "## 停止、升级与确认",
        "",
        f"- {AUTONOMY_BOUNDARIES[workflow['autonomy']]}",
        "- 缺少输入、执行器、执行标准或验收依据时，停止并只索要会改变执行结果的最少信息。",
        "- 工具失败时按当前自主度合同处理，不得静默改用未声明工具或降低验收标准。",
        "- guided 到达关键决定、例外处理或高影响分支时必须先确认。",
        "- 验证失败不得宣称完成；报告失败标准、证据和下一步。",
        "",
        "## 最终交付",
        "",
        "返回完成结果、逐项验收状态、实际调用的执行器、验证证据、失败项和剩余风险。",
    ]
    return "\n".join(lines)
