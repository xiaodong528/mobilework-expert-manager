#!/usr/bin/env python3
"""Shared deterministic rendering primitives for generated package documents."""

from __future__ import annotations

from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised in a subprocess fallback test
    yaml = None

from spec_templates import load_spec_template


def dump_yaml(data: dict[str, Any]) -> str:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to render block-style YAML frontmatter"
        )
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()


def render_spec(template_name: str, **values: Any) -> str:
    return load_spec_template(template_name).safe_substitute(**values)


def render_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    content = body.strip()
    if not frontmatter:
        return f"{content}\n"
    return f"---\n{dump_yaml(frontmatter)}\n---\n\n{content}\n"


def validate_skill_description(name: str, description: str) -> None:
    if not description or len(description) > 1024:
        raise ValueError(f"skill {name} description must contain 1-1024 characters")
