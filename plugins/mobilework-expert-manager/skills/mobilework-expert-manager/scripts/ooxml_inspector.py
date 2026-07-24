#!/usr/bin/env python3
"""Static OOXML workbook preflight performed before openpyxl loading."""

from __future__ import annotations

import zipfile
from pathlib import Path

import archive_inspector


def inspect_workbook(
    path: Path,
    *,
    limits: archive_inspector.ArchiveLimits | None = None,
) -> archive_inspector.ArchiveInspection:
    active_limits = limits or archive_inspector.default_limits("ooxmlLimits")
    base = archive_inspector.inspect_archive(
        path,
        limits=active_limits,
        require_single_root=False,
    )
    issues = list(base.issues)
    names = set(base.members)
    if not base.errors:
        for required in ("[Content_Types].xml", "xl/workbook.xml"):
            if required not in names:
                issues.append(
                    archive_inspector.ArchiveIssue(
                        "OOXML_REQUIRED_PART_MISSING",
                        "error",
                        f"workbook is missing required part {required}",
                        required,
                        "invalid-ooxml",
                    )
                )
        if "xl/vbaProject.bin" in names:
            issues.append(
                archive_inspector.ArchiveIssue(
                    "OOXML_MACRO_PRESENT",
                    "warning",
                    "workbook contains VBA macros; macros will not be executed",
                    "xl/vbaProject.bin",
                    "active-ooxml-content",
                )
            )
        if any(name.startswith("xl/externalLinks/") for name in names):
            issues.append(
                archive_inspector.ArchiveIssue(
                    "OOXML_EXTERNAL_LINK_PRESENT",
                    "warning",
                    "workbook contains external links",
                    "xl/externalLinks/",
                    "active-ooxml-content",
                )
            )
        if any(name.startswith("xl/embeddings/") for name in names):
            issues.append(
                archive_inspector.ArchiveIssue(
                    "OOXML_EMBEDDED_OBJECT_PRESENT",
                    "warning",
                    "workbook contains embedded objects",
                    "xl/embeddings/",
                    "active-ooxml-content",
                )
            )
    return archive_inspector.ArchiveInspection(
        base.source,
        base.limits,
        tuple(issues),
        base.members,
        base.roots,
        base.total_uncompressed_bytes,
    )
