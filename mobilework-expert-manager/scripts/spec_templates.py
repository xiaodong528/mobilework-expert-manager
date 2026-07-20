#!/usr/bin/env python3
"""Load executable templates embedded in MobileWork reference specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template


@dataclass(frozen=True)
class SpecTemplate:
    file_name: str
    language: str


class SpecTemplateError(ValueError):
    """Raised when a registered spec template is missing or malformed."""


SPEC_TEMPLATES: dict[str, SpecTemplate] = {
    "expert-agent": SpecTemplate("agent-md-spec.md", "markdown"),
    "primary-agent": SpecTemplate("agent-md-spec.md", "markdown"),
    "subagent": SpecTemplate("agent-md-spec.md", "markdown"),
    "common-skill": SpecTemplate("skill-md-spec.md", "markdown"),
    "role-skill": SpecTemplate("skill-md-spec.md", "markdown"),
    "readme": SpecTemplate("package-docs-spec.md", "markdown"),
    "expert-json": SpecTemplate("expert-json-spec.md", "json"),
}

REFERENCES_DIR = Path(__file__).resolve().parents[1] / "references"


def load_spec_text(
    template_id: str,
    *,
    references_dir: Path | None = None,
) -> str:
    """Return one registered fenced template without altering its bytes."""

    entry = SPEC_TEMPLATES.get(template_id)
    if entry is None:
        known = ", ".join(sorted(SPEC_TEMPLATES))
        raise SpecTemplateError(f"unknown spec template {template_id!r}; expected one of: {known}")

    path = (references_dir or REFERENCES_DIR) / entry.file_name
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SpecTemplateError(f"cannot read spec template source {path}: {exc}") from exc

    start_marker = f"<!-- mobilework-template:{template_id}:start -->"
    end_marker = f"<!-- mobilework-template:{template_id}:end -->"
    if source.count(start_marker) != 1:
        raise SpecTemplateError(f"{path}: expected exactly one {start_marker}")
    if source.count(end_marker) != 1:
        raise SpecTemplateError(f"{path}: expected exactly one {end_marker}")

    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker)
    if end <= start:
        raise SpecTemplateError(f"{path}: end marker precedes start marker for {template_id}")

    fenced = source[start:end]
    prefix = f"\n````{entry.language}\n"
    suffix = "````\n"
    if not fenced.startswith(prefix) or not fenced.endswith(suffix):
        raise SpecTemplateError(
            f"{path}: {template_id} must use a four-backtick {entry.language} fence"
        )
    text = fenced[len(prefix) : -len(suffix)]
    if not text:
        raise SpecTemplateError(f"{path}: {template_id} template must not be empty")
    return text


def load_spec_template(
    template_id: str,
    *,
    references_dir: Path | None = None,
) -> Template:
    """Return a ``string.Template`` backed by a registered reference spec."""

    return Template(load_spec_text(template_id, references_dir=references_dir))
