#!/usr/bin/env python3
"""Owned `.gitignore` block and conservative package-resource checks."""

from __future__ import annotations

import fnmatch
from pathlib import Path


BLOCK_START = "# BEGIN MOBILEWORK MANAGED IGNORE"
BLOCK_END = "# END MOBILEWORK MANAGED IGNORE"
REQUIRED_RULES = (
    ".DS_Store",
    "__MACOSX/",
    "._*",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    "node_modules/",
    ".env",
    ".env.*",
    "!.env.example",
    "dist/",
    "*.zip",
)


def required_content() -> str:
    return "\n".join((BLOCK_START, *REQUIRED_RULES, BLOCK_END, ""))


def merge_content(existing: str) -> str:
    """Refresh the managed block while preserving user rules outside it."""

    start = existing.find(BLOCK_START)
    end = existing.find(BLOCK_END)
    if start >= 0 and end >= start:
        end += len(BLOCK_END)
        before = existing[:start].rstrip("\n")
        after = existing[end:].lstrip("\n")
        parts = [part for part in (before, required_content().rstrip("\n"), after.rstrip("\n")) if part]
        return "\n\n".join(parts) + "\n"
    suffix = existing.strip("\n")
    return required_content() + (("\n" + suffix + "\n") if suffix else "")


def _rules(text: str) -> list[str]:
    return [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def validate_content(text: str, declared_files: set[str]) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    start = text.find(BLOCK_START)
    end = text.find(BLOCK_END)
    if start < 0 or end < start:
        issues.append(("GITIGNORE_MANAGED_BLOCK_MISSING", "root .gitignore is missing the managed block"))
    else:
        managed = _rules(text[start:end])
        missing = [rule for rule in REQUIRED_RULES if rule not in managed]
        if missing:
            issues.append(("GITIGNORE_REQUIRED_RULE_MISSING", f"root .gitignore is missing required rules: {', '.join(missing)}"))

    ignored: set[str] = set()
    for rule in _rules(text):
        negate = rule.startswith("!")
        pattern = rule[1:] if negate else rule
        directory = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        for path in declared_files:
            parts = Path(path).parts
            matches = fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)
            if directory:
                matches = any(fnmatch.fnmatch(part, pattern) for part in parts[:-1])
            if matches:
                if negate:
                    ignored.discard(path)
                else:
                    ignored.add(path)
    ignored.discard(".gitignore")
    if ignored:
        issues.append((
            "GITIGNORE_OWNED_FILE_IGNORED",
            "root .gitignore would ignore package-owned files: " + ", ".join(sorted(ignored)),
        ))
    return issues
