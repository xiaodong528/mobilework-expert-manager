#!/usr/bin/env python3
"""Stable finding classification generated from manager-contract.json."""

from __future__ import annotations

import re
from dataclasses import dataclass

import manager_contract


@dataclass(frozen=True)
class CatalogEntry:
    pattern: re.Pattern[str]
    code: str
    phase: str
    root_cause: str
    remediation: str


def _load_catalog() -> tuple[tuple[CatalogEntry, ...], dict[str, dict[str, str]]]:
    policy = manager_contract.load_policy()
    catalog = policy["findingCatalog"]
    entries = tuple(
        CatalogEntry(
            pattern=re.compile(rule["pattern"], re.I),
            code=rule["code"],
            phase=rule["phase"],
            root_cause=rule["rootCause"],
            remediation=rule["remediation"],
        )
        for rule in catalog["rules"]
    )
    fallback = {
        severity: dict(metadata)
        for severity, metadata in catalog["fallbackBySeverity"].items()
    }
    return entries, fallback


ENTRIES, FALLBACK_BY_SEVERITY = _load_catalog()


def classify(message: str, severity: str) -> tuple[str, str, str, str]:
    for entry in ENTRIES:
        if entry.pattern.search(message):
            return entry.code, entry.phase, entry.root_cause, entry.remediation
    fallback = FALLBACK_BY_SEVERITY[
        "warning" if severity == "warning" else "error"
    ]
    return (
        fallback["code"],
        fallback["phase"],
        fallback["rootCause"],
        fallback["remediation"],
    )
