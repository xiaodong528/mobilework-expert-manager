#!/usr/bin/env python3
"""Stable finding codes and remediations for MobileWork expert validation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogEntry:
    pattern: re.Pattern[str]
    code: str
    phase: str
    root_cause: str
    remediation: str


def _entry(pattern: str, code: str, phase: str, root: str, remediation: str) -> CatalogEntry:
    return CatalogEntry(re.compile(pattern, re.I), code, phase, root, remediation)


ENTRIES = (
    _entry(r"package directory does not exist", "PACKAGE_DIRECTORY_MISSING", "package", "missing-package", "Provide an existing expert package directory."),
    _entry(r"missing (?:expert\.json|opencode\.json)", "PACKAGE_REQUIRED_FILE_MISSING", "package", "missing-required-file", "Regenerate the package from expert.json."),
    _entry(r"(?:AGENTS\.md|root AGENTS)", "PACKAGE_ROOT_AGENTS_FORBIDDEN", "package", "unowned-root-file", "Move workspace-wide rules into declared instruction files."),
    _entry(r"undeclared package file", "PACKAGE_UNDECLARED_FILE", "package", "undeclared-resource", "Declare the resource in expert.json or remove it."),
    _entry(r"path is outside the package allowlist", "PACKAGE_PATH_NOT_OWNED", "package", "undeclared-resource", "Move the file into an owned package path."),
    _entry(r"non-distributable (?:directory|file|file suffix)", "PACKAGE_NON_DISTRIBUTABLE_CONTENT", "package", "non-distributable-content", "Remove transient or forbidden distribution content."),
    _entry(r"package\.json.*(?:scripts|only dependencies|lifecycle)", "PACKAGE_MANAGER_LIFECYCLE_FORBIDDEN", "security", "package-lifecycle-script", "Remove package-manager scripts and lifecycle hooks."),
    _entry(r"Python syntax", "PYTHON_STATIC_SYNTAX_INVALID", "static-syntax", "invalid-python-syntax", "Fix Python syntax; static diagnosis never imports the module."),
    _entry(r"non-portable", "PACKAGE_NON_PORTABLE_CONTENT", "portability", "non-portable-content", "Replace machine-specific content with portable values."),
    _entry(r"secret-like|secret detected|contains secret", "PACKAGE_SECRET_DETECTED", "security", "secret-content", "Remove the secret and reference an environment variable."),
    _entry(r"common_skills.*(?:string|purpose)|skills.*legacy", "MANIFEST_SKILL_LEGACY_FORMAT", "manifest", "legacy-manifest-contract", "Migrate the package to the unified skills catalog before structural modification."),
    _entry(r"maxTurns|max_turns|maxSteps", "AGENT_LEGACY_STEP_FIELD", "manifest", "legacy-runtime-field", "Use steps in new manifests and regenerate."),
    _entry(r"permission_reason is required", "PERMISSION_REASON_REQUIRED", "permission", "unjustified-permission-escalation", "Add permission_reason or keep the calculated action."),
    _entry(r"permission\.bash.*(?:unconditional|wildcard)|generated permission\.bash", "PERMISSION_BASH_WILDCARD_ALLOW", "permission", "unsafe-bash-permission", "Use ask or deny for Bash wildcard and exact required patterns."),
    _entry(r"permission\.external_directory.*wildcard", "PERMISSION_EXTERNAL_WILDCARD_ALLOW", "permission", "unsafe-external-directory", "Keep wildcard external access at ask or deny."),
    _entry(r"permission.*(?:match|mismatch|policy)", "PERMISSION_PROJECTION_MISMATCH", "permission", "permission-projection-drift", "Regenerate permissions from autonomy and ownership."),
    _entry(r"legacy-permission-baseline", "LEGACY_PERMISSION_BASELINE", "permission", "legacy-permission-contract", "Migrate to the unified skill schema during the next structural modification; add Workflow autonomy only when a formal Workflow is declared."),
    _entry(r"unused-role-bounded-fallback", "UNUSED_ROLE_BOUNDED_FALLBACK", "permission", "unassigned-role", "Assign the role to a workflow or accept bounded fallback."),
    _entry(r"workflow|autonomy|execution|executor", "WORKFLOW_CONTRACT_INVALID", "workflow", "workflow-contract", "Correct the workflow autonomy or execution contract."),
    _entry(r"README\.md: missing", "README_SECTION_MISSING", "documentation", "stale-derived-documentation", "Regenerate README.md from expert.json."),
    _entry(r"README\.md", "README_PROJECTION_MISMATCH", "documentation", "stale-derived-documentation", "Regenerate README.md from expert.json."),
    _entry(r"frontmatter", "MARKDOWN_FRONTMATTER_INVALID", "derived", "invalid-frontmatter", "Regenerate the derived Markdown file."),
    _entry(r"\$schema", "RUNTIME_SCHEMA_MISSING", "runtime-config", "runtime-config-contract", "Regenerate root opencode.json."),
    _entry(r"opencode\.json|runtime config", "RUNTIME_CONFIG_INVALID", "runtime-config", "runtime-config-contract", "Regenerate root opencode.json from expert.json."),
    _entry(r"avatar", "AVATAR_CONTRACT_INVALID", "resource", "avatar-contract", "Declare a valid portable avatar and regenerate."),
    _entry(r"symlink", "PACKAGE_SYMLINK_FORBIDDEN", "security", "unsafe-path", "Replace the symlink with an owned regular file."),
    _entry(r"\.name: is required|description is required", "MANIFEST_REQUIRED_FIELD_MISSING", "manifest", "missing-manifest-field", "Add the required manifest field."),
    _entry(r"subagents must contain at least one", "MANIFEST_TEAM_SUBAGENTS_MISSING", "manifest", "missing-team-role", "Declare at least one subagent."),
    _entry(r"missing agent definitions directory", "DERIVED_AGENT_DIRECTORY_MISSING", "derived", "missing-derived-agent", "Regenerate Agent Markdown files."),
    _entry(r"missing skill directory", "DERIVED_SKILL_DIRECTORY_MISSING", "derived", "missing-derived-skill", "Regenerate supplemental Skill files."),
    _entry(r"missing (?:primary |sub)?agent file", "DERIVED_AGENT_FILE_MISSING", "derived", "missing-derived-agent", "Regenerate Agent Markdown files."),
    _entry(r"missing skill file", "DERIVED_SKILL_FILE_MISSING", "derived", "missing-derived-skill", "Regenerate supplemental Skill files."),
    _entry(r"recommended count is", "MANIFEST_RECOMMENDED_COUNT", "manifest", "manifest-recommendation", "Use the recommended number of user-facing examples where practical."),
)


def classify(message: str, severity: str) -> tuple[str, str, str, str]:
    for entry in ENTRIES:
        if entry.pattern.search(message):
            return entry.code, entry.phase, entry.root_cause, entry.remediation
    code = "LEGACY_VALIDATION_WARNING" if severity == "warning" else "LEGACY_VALIDATION_ERROR"
    return (
        code,
        "validation",
        "legacy-unclassified-validation",
        "Migrate this validation site to a native finding code.",
    )
