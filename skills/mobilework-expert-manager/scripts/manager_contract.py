#!/usr/bin/env python3
"""Version-independent contract and explicit host capability resolution."""

from __future__ import annotations

import json
import os
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


POLICY_PATH = Path(__file__).with_name("manager-contract.json")
TARGET_VERSION_ENV = "MOBILEWORK_TARGET_OPENCODE_VERSION"
HOST_CONTRACT_KEYS = frozenset({"schemaVersion", "opencodeVersion", "capabilities"})
CONTRACT_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class ManagerContractError(ValueError):
    """Raised when manager or host contract data is invalid."""


@dataclass(frozen=True)
class TargetContract:
    version: str
    source: str
    capabilities: dict[str, Any]
    capability_verified: bool
    host_contract_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManagerContractError(f"{field} must be a JSON object")
    return dict(value)


def _version(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManagerContractError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if normalized.startswith("v") and len(normalized) > 1:
        normalized = normalized[1:]
    return normalized


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ManagerContractError(f"cannot read manager contract {path}: {exc}") from exc
    policy = _object(raw, "manager contract")
    schema_version = policy.get("schemaVersion")
    if type(schema_version) is not int or schema_version != 1:
        raise ManagerContractError("manager contract schemaVersion must be 1")
    contract_version = policy.get("contractVersion")
    if (
        not isinstance(contract_version, str)
        or not CONTRACT_VERSION_RE.fullmatch(contract_version)
    ):
        raise ManagerContractError(
            "manager contract contractVersion must be an unprefixed semantic version"
        )
    if "targetOpenCodeVersion" in policy:
        raise ManagerContractError("manager contract must not hardcode targetOpenCodeVersion")
    _validate_finding_catalog_policy(
        policy.get("findingCatalogVersion"),
        policy.get("findingCatalog"),
    )
    _validate_cli_policy(policy.get("cli"))
    _validate_evidence_policy(policy)
    _validate_receipt_policy(policy.get("receiptContract"))
    _validate_drift_recovery_policy(policy.get("driftRecovery"))
    _validate_workspace_lock_policy(policy.get("workspaceLock"))
    _validate_reserved_commands_policy(policy.get("reservedCommands"))
    _validate_trusted_conversion_adapter_policy(
        policy.get("trustedConversionAdapter")
    )
    _validate_requirements_discovery_policy(policy.get("requirementsDiscovery"))
    return policy


def _positive_int_list(value: Any, field: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value)
        or len(set(value)) != len(value)
    ):
        raise ManagerContractError(f"{field} must be a non-empty list of unique positive integers")
    return list(value)


def _string_list(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ManagerContractError(f"{field} must be a non-empty list of unique strings")
    return list(value)


def _require_exact_object(
    value: Any,
    *,
    field: str,
    keys: set[str],
) -> dict[str, Any]:
    result = _object(value, field)
    if set(result) != keys:
        raise ManagerContractError(f"{field} fields are invalid")
    return result


def _validate_reserved_commands_policy(value: Any) -> None:
    field = "manager contract reservedCommands"
    commands = _require_exact_object(
        value,
        field=field,
        keys={"source", "fallbackPolicy", "names"},
    )
    if commands.get("source") != "validated-host-capabilities":
        raise ManagerContractError(f"{field}.source is invalid")
    if commands.get("fallbackPolicy") != "deny-known-server-builtins":
        raise ManagerContractError(f"{field}.fallbackPolicy is invalid")
    if _string_list(commands.get("names"), f"{field}.names") != [
        "init",
        "review",
    ]:
        raise ManagerContractError(f"{field}.names is invalid")


def _validate_finding_catalog_policy(version: Any, value: Any) -> None:
    field = "manager contract findingCatalog"
    if type(version) is not int or version != 2:
        raise ManagerContractError(
            "manager contract findingCatalogVersion must be 2"
        )
    catalog = _require_exact_object(
        value,
        field=field,
        keys={"fallbackBySeverity", "rules"},
    )
    fallback = _require_exact_object(
        catalog.get("fallbackBySeverity"),
        field=f"{field}.fallbackBySeverity",
        keys={"warning", "error"},
    )
    metadata_fields = {"code", "phase", "rootCause", "remediation"}
    expected_fallback_codes = {
        "warning": "LEGACY_VALIDATION_WARNING",
        "error": "LEGACY_VALIDATION_ERROR",
    }
    for severity, expected_code in expected_fallback_codes.items():
        item = _require_exact_object(
            fallback.get(severity),
            field=f"{field}.fallbackBySeverity.{severity}",
            keys=metadata_fields,
        )
        for name in metadata_fields:
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ManagerContractError(
                    f"{field}.fallbackBySeverity.{severity}.{name} is invalid"
                )
        if item["code"] != expected_code:
            raise ManagerContractError(
                f"{field}.fallbackBySeverity.{severity}.code is invalid"
            )

    rules = catalog.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ManagerContractError(f"{field}.rules must be a non-empty list")
    rule_fields = {"pattern", *metadata_fields}
    codes: list[str] = []
    for index, raw_rule in enumerate(rules):
        rule_field = f"{field}.rules[{index}]"
        rule = _require_exact_object(
            raw_rule,
            field=rule_field,
            keys=rule_fields,
        )
        for name in rule_fields:
            if not isinstance(rule.get(name), str) or not rule[name].strip():
                raise ManagerContractError(f"{rule_field}.{name} is invalid")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", rule["code"]):
            raise ManagerContractError(f"{rule_field}.code is invalid")
        try:
            re.compile(rule["pattern"], re.I)
        except re.error as error:
            raise ManagerContractError(
                f"{rule_field}.pattern is invalid: {error}"
            ) from error
        codes.append(rule["code"])
    if len(codes) != len(set(codes)):
        raise ManagerContractError(f"{field}.rules codes must be unique")


def _validate_trusted_conversion_adapter_policy(value: Any) -> None:
    field = "manager contract trustedConversionAdapter"
    adapter = _require_exact_object(
        value,
        field=field,
        keys={
            "schemaVersion",
            "implementationStatus",
            "invocationAuthority",
            "managerDirectExecution",
            "forbiddenDirectExecutables",
            "inputFields",
            "outputFields",
            "anchorTypes",
            "providerFields",
            "unavailableAction",
            "runtimeEvidenceRequired",
        },
    )
    exact_values = {
        "schemaVersion": 1,
        "implementationStatus": "defined",
        "invocationAuthority": "desktop-host-only",
        "managerDirectExecution": "forbidden",
        "unavailableAction": "conversion-required",
        "runtimeEvidenceRequired": True,
    }
    for name, expected in exact_values.items():
        if type(adapter.get(name)) is not type(expected) or adapter.get(name) != expected:
            raise ManagerContractError(f"{field}.{name} is invalid")
    expected_lists = {
        "forbiddenDirectExecutables": [
            "parse-document",
            "uv",
            "package-script",
            "arbitrary-libreoffice-path",
        ],
        "inputFields": ["sourceSha256", "sourceType"],
        "outputFields": ["artifactSha256", "anchors", "provider"],
        "anchorTypes": ["page", "table", "slide"],
        "providerFields": ["id", "version", "sha256"],
    }
    for name, expected in expected_lists.items():
        if _string_list(adapter.get(name), f"{field}.{name}") != expected:
            raise ManagerContractError(f"{field}.{name} is invalid")


def _validate_cli_policy(value: Any) -> None:
    cli = _object(value, "manager contract cli")
    supported = _positive_int_list(
        cli.get("supportedSchemaVersions"),
        "manager contract cli.supportedSchemaVersions",
    )
    if supported != [1, 2]:
        raise ManagerContractError(
            "manager contract cli.supportedSchemaVersions must be [1, 2]"
        )
    if cli.get("defaultSchemaVersion") != 2:
        raise ManagerContractError(
            "manager contract cli.defaultSchemaVersion must be 2"
        )
    if cli.get("defaultSchemaVersion") not in supported:
        raise ManagerContractError(
            "manager contract cli.defaultSchemaVersion must be supported"
        )
    formats = _string_list(cli.get("formats"), "manager contract cli.formats")
    if formats != ["human", "json"]:
        raise ManagerContractError(
            "manager contract cli.formats must be ['human', 'json']"
        )
    expected_fields = [
        "schemaVersion",
        "operation",
        "ok",
        "status",
        "evidenceLevel",
        "gates",
        "runtime",
        "execution",
        "provenance",
        "findings",
        "data",
    ]
    if cli.get("v2Fields") != expected_fields:
        raise ManagerContractError("manager contract cli.v2Fields does not match schema v2")
    exits = _object(cli.get("exitCodes"), "manager contract cli.exitCodes")
    if exits != {
        "success": 0,
        "contractOrSafety": 1,
        "argumentEnvironmentOrVersion": 2,
        "internal": 3,
        "runtimePolicyBlocked": 4,
    }:
        raise ManagerContractError("manager contract cli.exitCodes is invalid")


def _validate_evidence_policy(policy: Mapping[str, Any]) -> None:
    gates = _object(policy.get("gates"), "manager contract gates")
    if gates.get("names") != [
        "archive",
        "contract",
        "portability",
        "install",
        "configLoad",
    ]:
        raise ManagerContractError("manager contract gates.names is invalid")
    if gates.get("values") != ["passed", "failed", "blocked", "not-run"]:
        raise ManagerContractError("manager contract gates.values is invalid")
    if policy.get("evidenceLevels") != [
        "invalid",
        "valid",
        "installable",
        "config-loadable",
    ]:
        raise ManagerContractError("manager contract evidenceLevels is invalid")
    if policy.get("runtimeStatuses") != ["not-tested", "blocked", "verified"]:
        raise ManagerContractError("manager contract runtimeStatuses is invalid")


def _validate_receipt_policy(value: Any) -> None:
    receipt = _object(value, "manager contract receiptContract")
    supported = _positive_int_list(
        receipt.get("readVersions"),
        "manager contract receiptContract.readVersions",
    )
    if supported != [1, 2, 3]:
        raise ManagerContractError(
            "manager contract receiptContract.readVersions must be [1, 2, 3]"
        )
    write_version = receipt.get("writeVersion")
    if write_version != 3:
        raise ManagerContractError(
            "manager contract receiptContract.writeVersion must be 3"
        )
    if write_version not in supported:
        raise ManagerContractError(
            "manager contract receiptContract.writeVersion must be readable"
        )
    extended = _positive_int_list(
        receipt.get("extendedOwnershipVersions"),
        "manager contract receiptContract.extendedOwnershipVersions",
    )
    if extended != [2, 3]:
        raise ManagerContractError(
            "manager contract receiptContract.extendedOwnershipVersions must be [2, 3]"
        )
    config_loadable = _positive_int_list(
        receipt.get("configLoadableVersions"),
        "manager contract receiptContract.configLoadableVersions",
    )
    if config_loadable != [3]:
        raise ManagerContractError(
            "manager contract receiptContract.configLoadableVersions must be [3]"
        )
    if not set([*extended, *config_loadable]).issubset(supported):
        raise ManagerContractError(
            "manager contract receipt ownership versions must be readable"
        )
    hash_fields = _string_list(
        receipt.get("v3Sha256Fields"),
        "manager contract receiptContract.v3Sha256Fields",
    )
    if hash_fields != [
        "packageTreeSha256",
        "manifestSha256",
        "managerContractSha256",
        "targetCapabilitiesSha256",
        "projectionSha256",
    ]:
        raise ManagerContractError(
            "manager contract receiptContract.v3Sha256Fields is invalid"
        )
    string_fields = _string_list(
        receipt.get("v3StringFields"),
        "manager contract receiptContract.v3StringFields",
    )
    if string_fields != ["targetOpenCodeVersion"]:
        raise ManagerContractError(
            "manager contract receiptContract.v3StringFields is invalid"
        )
    for field in ("projectionSchemaVersion", "driftPreviewSchemaVersion"):
        if receipt.get(field) != 1:
            raise ManagerContractError(
                f"manager contract receiptContract.{field} must be 1"
            )


def _validate_drift_recovery_policy(value: Any) -> None:
    recovery = _object(value, "manager contract driftRecovery")
    expected_keys = {
        "schemaVersion",
        "rootName",
        "manifestName",
        "payloadDirectory",
        "publishProtocol",
        "backupIdPattern",
        "dirMode",
        "fileMode",
    }
    if set(recovery) != expected_keys:
        raise ManagerContractError("manager contract driftRecovery fields are invalid")
    if recovery.get("schemaVersion") != 1:
        raise ManagerContractError(
            "manager contract driftRecovery.schemaVersion must be 1"
        )
    if recovery.get("rootName") != ".expert-drift-backups":
        raise ManagerContractError(
            "manager contract driftRecovery.rootName is invalid"
        )
    if recovery.get("manifestName") != "manifest.json":
        raise ManagerContractError(
            "manager contract driftRecovery.manifestName is invalid"
        )
    if recovery.get("payloadDirectory") != "payload":
        raise ManagerContractError(
            "manager contract driftRecovery.payloadDirectory is invalid"
        )
    if recovery.get("publishProtocol") != "posix-exclusive-directory-v1":
        raise ManagerContractError(
            "manager contract driftRecovery.publishProtocol is invalid"
        )
    pattern = recovery.get("backupIdPattern")
    canonical_pattern = r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z(?:-[0-9]{3})?$"
    if pattern != canonical_pattern:
        raise ManagerContractError(
            "manager contract driftRecovery.backupIdPattern is invalid"
        )
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ManagerContractError(
            "manager contract driftRecovery.backupIdPattern is invalid"
        ) from exc
    if compiled.fullmatch("20260804T123456.123456Z") is None:
        raise ManagerContractError(
            "manager contract driftRecovery.backupIdPattern rejects canonical ids"
        )
    if recovery.get("dirMode") != 0o700 or recovery.get("fileMode") != 0o600:
        raise ManagerContractError(
            "manager contract driftRecovery permissions must be 0700/0600"
        )


def _validate_workspace_lock_policy(value: Any) -> None:
    lock = _object(value, "manager contract workspaceLock")
    if set(lock) != {
        "fileName",
        "protocolVersion",
        "fields",
        "fileMode",
        "platformBackends",
        "windowsProtocol",
        "staleReclaim",
    }:
        raise ManagerContractError("manager contract workspaceLock fields are invalid")
    if lock.get("fileName") != ".mobilework-expert-manager.lock":
        raise ManagerContractError("manager contract workspaceLock.fileName is invalid")
    if lock.get("protocolVersion") != 2:
        raise ManagerContractError(
            "manager contract workspaceLock.protocolVersion must be 2"
        )
    if lock.get("fields") != [
        "ownerToken",
        "pid",
        "createdAt",
        "heartbeatAt",
        "protocolVersion",
    ]:
        raise ManagerContractError("manager contract workspaceLock.fields is invalid")
    if lock.get("fileMode") != 0o600:
        raise ManagerContractError(
            "manager contract workspaceLock.fileMode must be 0600"
        )
    if lock.get("platformBackends") != {
        "posix": "implemented",
        "windows": "implemented",
    }:
        raise ManagerContractError(
            "manager contract workspaceLock.platformBackends is invalid"
        )
    if lock.get("windowsProtocol") != {
        "directoryAnchor": "reparse-free-held-handle-chain",
        "ownerIdentity": "volume-serial-and-128-bit-file-id",
        "release": "source-handle-no-replace-quarantine-then-delete-on-close",
        "targetTransactionSecurity": "partial",
    }:
        raise ManagerContractError(
            "manager contract workspaceLock.windowsProtocol is invalid"
        )
    if lock.get("staleReclaim") != "not-implemented":
        raise ManagerContractError(
            "manager contract workspaceLock.staleReclaim is invalid"
        )


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManagerContractError(f"{field} must be a non-empty string")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ManagerContractError(f"{field} must be a boolean")
    return value


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ManagerContractError(f"{field} must be a positive integer")
    return value


def _string_list_allow_empty(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ManagerContractError(f"{field} must be a list of unique strings")
    return list(value)


def _require_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ManagerContractError(f"{field} fields are invalid")


def _validate_requirements_discovery_policy(value: Any) -> None:
    requirements = _object(value, "manager contract requirementsDiscovery")
    expected_keys = {
        "schemaVersion",
        "ledgerPersistence",
        "ledgerFields",
        "sources",
        "statuses",
        "askedVia",
        "questionChannelsMutuallyExclusive",
        "questionChannelLimits",
        "questionChannelEvidence",
        "decisionIdentity",
        "decisionIntroduction",
        "materialImpacts",
        "dependencySemantics",
        "stateTransitionSemantics",
        "materialDecisionExecutionGate",
        "technicalMappingReturn",
        "businessStandards",
        "presentationBoundary",
        "capabilityDisclosure",
        "executionLayerMapping",
        "businessCardSections",
        "businessCardAppendices",
        "limits",
        "extension",
        "preConfirmationForbiddenEffects",
        "finalConfirmationConsumesBudget",
        "environmentTaskFeatures",
    }
    if set(requirements) != expected_keys:
        raise ManagerContractError(
            "manager contract requirementsDiscovery fields are invalid"
        )
    if type(requirements.get("schemaVersion")) is not int or requirements[
        "schemaVersion"
    ] != 11:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.schemaVersion must be 11"
        )
    _non_empty_string(
        requirements.get("ledgerPersistence"),
        "manager contract requirementsDiscovery.ledgerPersistence",
    )
    ledger_fields = _string_list(
        requirements.get("ledgerFields"),
        "manager contract requirementsDiscovery.ledgerFields",
    )
    if ledger_fields != [
        "decision_id",
        "source",
        "status",
        "dependencies",
        "asked_via",
        "blocked_by",
        "resume_status",
    ]:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.ledgerFields is invalid"
        )
    _string_list(
        requirements.get("sources"),
        "manager contract requirementsDiscovery.sources",
    )
    statuses = _string_list(
        requirements.get("statuses"),
        "manager contract requirementsDiscovery.statuses",
    )
    asked_via = requirements.get("askedVia")
    if (
        not isinstance(asked_via, list)
        or not asked_via
        or None not in asked_via
        or any(
            item is not None and (not isinstance(item, str) or not item.strip())
            for item in asked_via
        )
        or len(set(asked_via)) != len(asked_via)
    ):
        raise ManagerContractError(
            "manager contract requirementsDiscovery.askedVia is invalid"
        )
    _boolean(
        requirements.get("questionChannelsMutuallyExclusive"),
        "manager contract requirementsDiscovery.questionChannelsMutuallyExclusive",
    )
    channel_limits = _object(
        requirements.get("questionChannelLimits"),
        "manager contract requirementsDiscovery.questionChannelLimits",
    )
    expected_channel_limits = {
        "toolAvailable": "up-to-three-independent-question-ready-decisions",
        "toolUnavailable": "one-concise-composite-business-question",
        "bodyQuestionCount": 1,
        "bodyQuestionMustNotUseNumberedSubquestions": True,
    }
    _require_keys(
        channel_limits,
        set(expected_channel_limits),
        "manager contract requirementsDiscovery.questionChannelLimits",
    )
    if channel_limits != expected_channel_limits:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.questionChannelLimits is invalid"
        )
    channel_evidence = _object(
        requirements.get("questionChannelEvidence"),
        "manager contract requirementsDiscovery.questionChannelEvidence",
    )
    _require_keys(
        channel_evidence,
        {
            "askedViaSemantics",
            "hostAssertionRequires",
            "missingHostEvidenceStatus",
            "skillTextCountsAsHostEvidence",
            "assistantSelfReportCountsAsHostEvidence",
        },
        "manager contract requirementsDiscovery.questionChannelEvidence",
    )
    expected_channel_evidence = {
        "askedViaSemantics": "agent-session-bookkeeping",
        "hostAssertionRequires": "complete-host-question-channel-ledger",
        "missingHostEvidenceStatus": "not-verified",
    }
    for field, expected in expected_channel_evidence.items():
        if channel_evidence.get(field) != expected:
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.questionChannelEvidence.{field} is invalid"
            )
    for field in (
        "skillTextCountsAsHostEvidence",
        "assistantSelfReportCountsAsHostEvidence",
    ):
        if _boolean(
            channel_evidence.get(field),
            f"manager contract requirementsDiscovery.questionChannelEvidence.{field}",
        ):
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.questionChannelEvidence.{field} must be false"
            )

    identity = _object(
        requirements.get("decisionIdentity"),
        "manager contract requirementsDiscovery.decisionIdentity",
    )
    _require_keys(
        identity,
        {
            "scope",
            "basis",
            "paraphraseCreatesNewDecision",
            "newEvidenceAction",
            "unansweredAskedAction",
        },
        "manager contract requirementsDiscovery.decisionIdentity",
    )
    for field in ("scope", "basis", "newEvidenceAction", "unansweredAskedAction"):
        _non_empty_string(
            identity.get(field),
            f"manager contract requirementsDiscovery.decisionIdentity.{field}",
        )
    _boolean(
        identity.get("paraphraseCreatesNewDecision"),
        "manager contract requirementsDiscovery.decisionIdentity.paraphraseCreatesNewDecision",
    )
    decision_introduction = _object(
        requirements.get("decisionIntroduction"),
        "manager contract requirementsDiscovery.decisionIntroduction",
    )
    _require_keys(
        decision_introduction,
        {
            "scope",
            "admissionPrecedes",
            "requiresAtLeastOne",
            "genericIntentInsufficient",
            "deferredOrBlockedBindingAction",
            "outboundData",
        },
        "manager contract requirementsDiscovery.decisionIntroduction",
    )
    expected_introduction = {
        "scope": (
            "material-decisions-not-already-established-by-user-or-trusted-current-design"
        ),
        "admissionPrecedes": [
            "dependency-reconciliation",
            "question-frontier-selection",
        ],
        "requiresAtLeastOne": [
            "explicit-user-current-choice",
            "trusted-concrete-current-candidate-or-execution-path",
        ],
        "genericIntentInsufficient": [
            "paid-inference-alone",
            "user-supplied-technical-term-alone",
            "hypothetical-future-provider-or-connector",
        ],
        "deferredOrBlockedBindingAction": (
            "project-no-execution-guard-onto-existing-blocker-without-new-open-or-asked-decision"
        ),
    }
    for field in ("scope", "deferredOrBlockedBindingAction"):
        _non_empty_string(
            decision_introduction.get(field),
            f"manager contract requirementsDiscovery.decisionIntroduction.{field}",
        )
    for field in (
        "admissionPrecedes",
        "requiresAtLeastOne",
        "genericIntentInsufficient",
    ):
        values = _string_list(
            decision_introduction.get(field),
            f"manager contract requirementsDiscovery.decisionIntroduction.{field}",
        )
        if values != expected_introduction[field]:
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.decisionIntroduction.{field} is invalid"
            )
    for field in ("scope", "deferredOrBlockedBindingAction"):
        if decision_introduction.get(field) != expected_introduction[field]:
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.decisionIntroduction.{field} is invalid"
            )
    outbound_data = _object(
        decision_introduction.get("outboundData"),
        "manager contract requirementsDiscovery.decisionIntroduction.outboundData",
    )
    expected_outbound_data = {
        "questionReadyRequires": (
            "explicit-user-current-boundary-or-trusted-concrete-external-data-path"
        ),
        "noConcretePathAction": (
            "forbid-network-and-data-egress-and-carry-guard-with-existing-blocker"
        ),
        "explicitUserBoundaryAction": "record-or-update-material-decision",
        "laterConcretePathAction": (
            "introduce-or-update-decision-with-provenance-without-treating-guard-as-authorization"
        ),
        "guard": {
            "scope": ["network", "data-egress"],
            "questionState": None,
            "consumesDecisionBudget": False,
            "blocksWholeCardDesignConfirmation": False,
            "releaseCondition": (
                "concrete-path-classified-and-explicitly-authorized-or-proven-no-egress"
            ),
        },
    }
    _require_keys(
        outbound_data,
        set(expected_outbound_data),
        "manager contract requirementsDiscovery.decisionIntroduction.outboundData",
    )
    for field, expected in expected_outbound_data.items():
        if field == "guard":
            continue
        value = _non_empty_string(
            outbound_data.get(field),
            f"manager contract requirementsDiscovery.decisionIntroduction.outboundData.{field}",
        )
        if value != expected:
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.decisionIntroduction.outboundData.{field} is invalid"
            )
    guard = _object(
        outbound_data.get("guard"),
        "manager contract requirementsDiscovery.decisionIntroduction.outboundData.guard",
    )
    if guard != expected_outbound_data["guard"]:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.decisionIntroduction.outboundData.guard is invalid"
        )
    _string_list(
        requirements.get("materialImpacts"),
        "manager contract requirementsDiscovery.materialImpacts",
    )

    dependency_semantics = _object(
        requirements.get("dependencySemantics"),
        "manager contract requirementsDiscovery.dependencySemantics",
    )
    _require_keys(
        dependency_semantics,
        {
            "scope",
            "edgeRequires",
            "sharedExecutionGuardCreatesEdge",
            "executionOnlyNeedAction",
            "unresolvedExecutionNeedAction",
            "answeredPrerequisiteWithoutCrossResolution",
            "relationUpdate",
        },
        "manager contract requirementsDiscovery.dependencySemantics",
    )
    for field in (
        "scope",
        "executionOnlyNeedAction",
        "unresolvedExecutionNeedAction",
        "answeredPrerequisiteWithoutCrossResolution",
        "relationUpdate",
    ):
        _non_empty_string(
            dependency_semantics.get(field),
            f"manager contract requirementsDiscovery.dependencySemantics.{field}",
        )
    edge_requires = _string_list(
        dependency_semantics.get("edgeRequires"),
        "manager contract requirementsDiscovery.dependencySemantics.edgeRequires",
    )
    if edge_requires != [
        "explicit-user-deferral",
        "trusted-evidence-that-prerequisite-changes-candidate-set-or-comparison",
    ]:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.dependencySemantics.edgeRequires is invalid"
        )
    if _boolean(
        dependency_semantics.get("sharedExecutionGuardCreatesEdge"),
        "manager contract requirementsDiscovery.dependencySemantics.sharedExecutionGuardCreatesEdge",
    ):
        raise ManagerContractError(
            "manager contract shared execution guards must not create selection dependencies"
        )

    transition_semantics = _object(
        requirements.get("stateTransitionSemantics"),
        "manager contract requirementsDiscovery.stateTransitionSemantics",
    )
    _require_keys(
        transition_semantics,
        {
            "negativePrerequisiteAnswer",
            "conditionalDeferralRequires",
            "conditionalDeferralAction",
            "explicitFinalRejectionAction",
            "newCandidateEvidenceReopensExplicitRejection",
            "authorizationChange",
            "targetChange",
        },
        "manager contract requirementsDiscovery.stateTransitionSemantics",
    )
    for field in (
        "negativePrerequisiteAnswer",
        "conditionalDeferralRequires",
        "conditionalDeferralAction",
        "explicitFinalRejectionAction",
        "authorizationChange",
        "targetChange",
    ):
        _non_empty_string(
            transition_semantics.get(field),
            f"manager contract requirementsDiscovery.stateTransitionSemantics.{field}",
        )
    if _boolean(
        transition_semantics.get("newCandidateEvidenceReopensExplicitRejection"),
        "manager contract requirementsDiscovery.stateTransitionSemantics.newCandidateEvidenceReopensExplicitRejection",
    ):
        raise ManagerContractError(
            "manager contract new candidate evidence must not reopen an explicit rejection"
        )

    execution_gate = _object(
        requirements.get("materialDecisionExecutionGate"),
        "manager contract requirementsDiscovery.materialDecisionExecutionGate",
    )
    _require_keys(
        execution_gate,
        {
            "blockingStates",
            "requiresCurrentWholeCardConfirmation",
            "questionFrontierCoupling",
        },
        "manager contract requirementsDiscovery.materialDecisionExecutionGate",
    )
    execution_blocking_states = _string_list(
        execution_gate.get("blockingStates"),
        "manager contract requirementsDiscovery.materialDecisionExecutionGate.blockingStates",
    )
    if not _boolean(
        execution_gate.get("requiresCurrentWholeCardConfirmation"),
        "manager contract requirementsDiscovery.materialDecisionExecutionGate.requiresCurrentWholeCardConfirmation",
    ):
        raise ManagerContractError(
            "manager contract material decision execution gate must require current whole-card confirmation"
        )
    if execution_gate.get("questionFrontierCoupling") != "none":
        raise ManagerContractError(
            "manager contract material decision execution gate must not suppress the question frontier"
        )

    mapping = _object(
        requirements.get("technicalMappingReturn"),
        "manager contract requirementsDiscovery.technicalMappingReturn",
    )
    _require_keys(
        mapping,
        {
            "trigger",
            "requiredSequence",
            "responseGate",
            "stateReconciliation",
            "questionSelection",
            "cardDecisionStates",
            "pendingDecisionStates",
            "questionEligibleStates",
            "askedViaWriteOnce",
            "pendingDecisionAction",
            "noPendingDecisionAction",
            "wholeCardConfirmationCondition",
            "blockedConfirmationScope",
            "generation",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn",
    )
    _non_empty_string(
        mapping.get("trigger"),
        "manager contract requirementsDiscovery.technicalMappingReturn.trigger",
    )
    required_sequence = _string_list(
        mapping.get("requiredSequence"),
        "manager contract requirementsDiscovery.technicalMappingReturn.requiredSequence",
    )
    ordered_steps = (
        "record-or-update-material-decision",
        "invalidate-prior-confirmation",
        "reconcile-current-decision-states",
        "merge-current-decision-states-into-card",
        "render-complete-updated-business-card",
    )
    if any(step not in required_sequence for step in ordered_steps) or any(
        required_sequence.index(left) >= required_sequence.index(right)
        for left, right in zip(ordered_steps, ordered_steps[1:])
    ):
        raise ManagerContractError(
            "manager contract technicalMappingReturn.requiredSequence must preserve the state reconciliation order"
        )
    response_gate = _object(
        mapping.get("responseGate"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate",
    )
    _require_keys(
        response_gate,
        {
            "name",
            "sameAssistantTurn",
            "sequenceMustBeFirst",
            "beforeSequenceForbidden",
            "developmentDetails",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate",
    )
    _non_empty_string(
        response_gate.get("name"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate.name",
    )
    _boolean(
        response_gate.get("sameAssistantTurn"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate.sameAssistantTurn",
    )
    _boolean(
        response_gate.get("sequenceMustBeFirst"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate.sequenceMustBeFirst",
    )
    _string_list(
        response_gate.get("beforeSequenceForbidden"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate.beforeSequenceForbidden",
    )
    _non_empty_string(
        response_gate.get("developmentDetails"),
        "manager contract requirementsDiscovery.technicalMappingReturn.responseGate.developmentDetails",
    )
    status_set = set(statuses)
    card_states = _string_list(
        mapping.get("cardDecisionStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.cardDecisionStates",
    )
    pending_states = _string_list(
        mapping.get("pendingDecisionStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.pendingDecisionStates",
    )
    question_states = _string_list(
        mapping.get("questionEligibleStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionEligibleStates",
    )
    if not set(card_states).issubset(status_set):
        raise ManagerContractError(
            "manager contract technicalMappingReturn.cardDecisionStates must be statuses"
        )
    if not set(pending_states).issubset(card_states):
        raise ManagerContractError(
            "manager contract technicalMappingReturn.pendingDecisionStates must be card states"
        )
    if not set(question_states).issubset(pending_states):
        raise ManagerContractError(
            "manager contract technicalMappingReturn.questionEligibleStates must be pending states"
        )
    if set(execution_blocking_states) != set(pending_states) | {"blocked"}:
        raise ManagerContractError(
            "manager contract materialDecisionExecutionGate.blockingStates must equal pending states plus blocked"
        )
    question_selection = _object(
        mapping.get("questionSelection"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection",
    )
    _require_keys(
        question_selection,
        {
            "basis",
            "candidateStates",
            "resolvedPrerequisiteStates",
            "questionReadyCondition",
            "selection",
            "limitSource",
            "askedRootAction",
            "readyBatchAction",
            "unrelatedAskedAction",
            "descendantAction",
            "noReadyButPendingAction",
            "frontierPrecedence",
            "businessCandidateAction",
            "implementationBindingAction",
            "candidateEvidence",
            "resolutionRouting",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection",
    )
    candidate_states = _string_list(
        question_selection.get("candidateStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.candidateStates",
    )
    resolved_prerequisite_states = _string_list(
        question_selection.get("resolvedPrerequisiteStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.resolvedPrerequisiteStates",
    )
    if set(candidate_states) != set(question_states):
        raise ManagerContractError(
            "manager contract questionSelection candidate states must equal question-eligible states"
        )
    if not set(resolved_prerequisite_states).issubset(card_states):
        raise ManagerContractError(
            "manager contract questionSelection resolved prerequisite states must be card states"
        )
    if set(resolved_prerequisite_states) & set(pending_states):
        raise ManagerContractError(
            "manager contract questionSelection resolved prerequisite states must not be pending"
        )
    for field in (
        "basis",
        "questionReadyCondition",
        "selection",
        "limitSource",
        "askedRootAction",
        "readyBatchAction",
        "unrelatedAskedAction",
        "descendantAction",
        "noReadyButPendingAction",
        "businessCandidateAction",
        "implementationBindingAction",
    ):
        _non_empty_string(
            question_selection.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.{field}",
        )
    if question_selection["limitSource"] != (
        "requirementsDiscovery.limits.perRoundDecisions"
    ):
        raise ManagerContractError(
            "manager contract questionSelection limitSource must reference the per-round decision limit"
        )
    frontier_precedence = _object(
        question_selection.get("frontierPrecedence"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.frontierPrecedence",
    )
    _require_keys(
        frontier_precedence,
        {
            "recomputeAfterBlockerRecovery",
            "questionReadyOpenAction",
            "cannotBeDeferredBy",
            "technicalChoiceSurface",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.frontierPrecedence",
    )
    if not _boolean(
        frontier_precedence.get("recomputeAfterBlockerRecovery"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.frontierPrecedence.recomputeAfterBlockerRecovery",
    ):
        raise ManagerContractError(
            "manager contract questionSelection.frontierPrecedence.recomputeAfterBlockerRecovery must be true"
        )
    for field in ("questionReadyOpenAction", "technicalChoiceSurface"):
        _non_empty_string(
            frontier_precedence.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.frontierPrecedence.{field}",
        )
    cannot_be_deferred_by = _string_list(
        frontier_precedence.get("cannotBeDeferredBy"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.frontierPrecedence.cannotBeDeferredBy",
    )
    if cannot_be_deferred_by != [
        "whole-card-confirmation",
        "development-details-boundary",
    ]:
        raise ManagerContractError(
            "manager contract questionSelection.frontierPrecedence.cannotBeDeferredBy is invalid"
        )
    candidate_evidence = _object(
        question_selection.get("candidateEvidence"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.candidateEvidence",
    )
    _require_keys(
        candidate_evidence,
        {"required", "insufficientAction", "runtimeClaim"},
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.candidateEvidence",
    )
    required_candidate_evidence = _string_list(
        candidate_evidence.get("required"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.candidateEvidence.required",
    )
    if required_candidate_evidence != [
        "stable-business-label",
        "decision-relevant-differences",
        "trusted-provenance",
    ]:
        raise ManagerContractError(
            "manager contract questionSelection.candidateEvidence.required is invalid"
        )
    for field in ("insufficientAction", "runtimeClaim"):
        _non_empty_string(
            candidate_evidence.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.candidateEvidence.{field}",
        )
    resolution_routing = _object(
        question_selection.get("resolutionRouting"),
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.resolutionRouting",
    )
    _require_keys(
        resolution_routing,
        {
            "explicitUserChoice",
            "explicitDelegation",
            "trustedDerivation",
            "preserveAcrossBlocking",
            "insufficientEvidence",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.resolutionRouting",
    )
    for field in resolution_routing:
        _non_empty_string(
            resolution_routing.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.questionSelection.resolutionRouting.{field}",
        )
    reconciliation = _object(
        mapping.get("stateReconciliation"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation",
    )
    _require_keys(
        reconciliation,
        {
            "trigger",
            "unavailablePrerequisiteState",
            "dependentDecisionStates",
            "dependencyFieldMeaning",
            "propagationDirection",
            "dependencyTraversal",
            "recomputeMode",
            "graphValidationScope",
            "blockingCondition",
            "independenceCondition",
            "blockerAggregation",
            "explicitRootBlockerRepresentation",
            "resumeStatusValues",
            "resumeStatusCapture",
            "unknownDependency",
            "dependencyCycle",
            "dependentDecisionAction",
            "independentDecisionAction",
            "historyAction",
            "explicitRootReopenCondition",
            "derivedReopenCondition",
            "reopenAction",
            "invalidGraphConfirmation",
            "invalidGraphGeneration",
            "invalidGraphTailAction",
            "tailSelectionBasis",
        },
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation",
    )
    unavailable_state = _non_empty_string(
        reconciliation.get("unavailablePrerequisiteState"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.unavailablePrerequisiteState",
    )
    dependent_states = _string_list(
        reconciliation.get("dependentDecisionStates"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.dependentDecisionStates",
    )
    if unavailable_state not in card_states or unavailable_state in pending_states:
        raise ManagerContractError(
            "manager contract stateReconciliation unavailable state must be a non-pending card state"
        )
    if unavailable_state in resolved_prerequisite_states:
        raise ManagerContractError(
            "manager contract questionSelection unavailable state must not be a resolved prerequisite"
        )
    if set(dependent_states) != set(pending_states):
        raise ManagerContractError(
            "manager contract stateReconciliation dependent states must equal pending states"
        )
    resume_states = _string_list(
        reconciliation.get("resumeStatusValues"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.resumeStatusValues",
    )
    if set(resume_states) != set(pending_states):
        raise ManagerContractError(
            "manager contract stateReconciliation resume states must equal pending states"
        )
    unknown_dependency = _object(
        reconciliation.get("unknownDependency"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.unknownDependency",
    )
    dependency_cycle = _object(
        reconciliation.get("dependencyCycle"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.dependencyCycle",
    )
    _require_keys(
        unknown_dependency,
        {"code", "affectedScope"},
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.unknownDependency",
    )
    _require_keys(
        dependency_cycle,
        {"code", "selfLoopIsCycle", "affectedScope"},
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.dependencyCycle",
    )
    for field, value in (
        ("unknownDependency.code", unknown_dependency.get("code")),
        ("unknownDependency.affectedScope", unknown_dependency.get("affectedScope")),
        ("dependencyCycle.code", dependency_cycle.get("code")),
        ("dependencyCycle.affectedScope", dependency_cycle.get("affectedScope")),
    ):
        _non_empty_string(
            value,
            f"manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.{field}",
        )
    _boolean(
        dependency_cycle.get("selfLoopIsCycle"),
        "manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.dependencyCycle.selfLoopIsCycle",
    )
    business_standards = _object(
        requirements.get("businessStandards"),
        "manager contract requirementsDiscovery.businessStandards",
    )
    if business_standards.get("unavailableDecisionState") != unavailable_state:
        raise ManagerContractError(
            "manager contract unavailable decision states must agree across requirements discovery"
        )
    for field in (
        "trigger",
        "dependencyFieldMeaning",
        "propagationDirection",
        "dependencyTraversal",
        "recomputeMode",
        "graphValidationScope",
        "blockingCondition",
        "independenceCondition",
        "blockerAggregation",
        "explicitRootBlockerRepresentation",
        "resumeStatusCapture",
        "dependentDecisionAction",
        "independentDecisionAction",
        "historyAction",
        "explicitRootReopenCondition",
        "derivedReopenCondition",
        "reopenAction",
        "invalidGraphConfirmation",
        "invalidGraphGeneration",
        "invalidGraphTailAction",
        "tailSelectionBasis",
    ):
        _non_empty_string(
            reconciliation.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.stateReconciliation.{field}",
        )
    _boolean(
        mapping.get("askedViaWriteOnce"),
        "manager contract requirementsDiscovery.technicalMappingReturn.askedViaWriteOnce",
    )
    for field in (
        "pendingDecisionAction",
        "noPendingDecisionAction",
        "wholeCardConfirmationCondition",
        "blockedConfirmationScope",
        "generation",
    ):
        _non_empty_string(
            mapping.get(field),
            f"manager contract requirementsDiscovery.technicalMappingReturn.{field}",
        )

    business_standard_fields = {
        "authority",
        "threshold",
        "unsupportedDefaults",
        "unresolvedDecisionState",
        "unavailableDecisionState",
        "executionWhileUnresolved",
        "decisionPairing",
        "authorityAnswerWithoutComputableRule",
    }
    _require_keys(
        business_standards,
        business_standard_fields,
        "manager contract requirementsDiscovery.businessStandards",
    )
    for field in business_standard_fields - {
        "decisionPairing",
        "authorityAnswerWithoutComputableRule",
    }:
        _non_empty_string(
            business_standards.get(field),
            f"manager contract requirementsDiscovery.businessStandards.{field}",
        )
    decision_pairing = _object(
        business_standards.get("decisionPairing"),
        "manager contract requirementsDiscovery.businessStandards.decisionPairing",
    )
    _require_keys(
        decision_pairing,
        {
            "components",
            "separateDecisionIds",
            "sameRoundWhen",
            "orderedWhen",
            "crossResolution",
            "unsupportedInference",
        },
        "manager contract requirementsDiscovery.businessStandards.decisionPairing",
    )
    components = _string_list(
        decision_pairing.get("components"),
        "manager contract requirementsDiscovery.businessStandards.decisionPairing.components",
    )
    if components != ["authority-source", "executable-rule-value"]:
        raise ManagerContractError(
            "manager contract businessStandards.decisionPairing.components is invalid"
        )
    if not _boolean(
        decision_pairing.get("separateDecisionIds"),
        "manager contract requirementsDiscovery.businessStandards.decisionPairing.separateDecisionIds",
    ):
        raise ManagerContractError(
            "manager contract businessStandards.decisionPairing.separateDecisionIds must be true"
        )
    for field in (
        "sameRoundWhen",
        "orderedWhen",
        "crossResolution",
        "unsupportedInference",
    ):
        _non_empty_string(
            decision_pairing.get(field),
            f"manager contract requirementsDiscovery.businessStandards.decisionPairing.{field}",
        )

    missing_rule = _object(
        business_standards.get("authorityAnswerWithoutComputableRule"),
        "manager contract requirementsDiscovery.businessStandards.authorityAnswerWithoutComputableRule",
    )
    _require_keys(
        missing_rule,
        {
            "authoritySourceState",
            "executableRuleValueState",
            "crossResolution",
            "frontierAction",
            "questionAction",
        },
        "manager contract requirementsDiscovery.businessStandards.authorityAnswerWithoutComputableRule",
    )
    for field in missing_rule:
        _non_empty_string(
            missing_rule.get(field),
            f"manager contract requirementsDiscovery.businessStandards.authorityAnswerWithoutComputableRule.{field}",
        )
    if missing_rule["authoritySourceState"] not in resolved_prerequisite_states:
        raise ManagerContractError(
            "manager contract authority source without a computable rule must remain resolved"
        )
    if missing_rule["executableRuleValueState"] != "preserve-open-or-asked":
        raise ManagerContractError(
            "manager contract missing executable rule must preserve the original pending decision"
        )

    execution_mapping = _object(
        requirements.get("executionLayerMapping"),
        "manager contract requirementsDiscovery.executionLayerMapping",
    )
    execution_mapping_fields = {
        "scriptOwnsAllConfirmedSteps",
        "separateFixedLayerRequires",
        "businessDisclosure",
        "enumDisclosure",
    }
    _require_keys(
        execution_mapping,
        execution_mapping_fields,
        "manager contract requirementsDiscovery.executionLayerMapping",
    )
    for field in execution_mapping_fields:
        _non_empty_string(
            execution_mapping.get(field),
            f"manager contract requirementsDiscovery.executionLayerMapping.{field}",
        )

    presentation = _object(
        requirements.get("presentationBoundary"),
        "manager contract requirementsDiscovery.presentationBoundary",
    )
    _require_keys(
        presentation,
        {
            "defaultSurface",
            "scope",
            "developmentDetailsActivation",
            "forbiddenDefaultCategories",
            "requiredBusinessEffects",
            "enumTranslation",
            "userSuppliedTechnicalTerms",
            "questionReadyBusinessCandidates",
            "implementationBindingDetails",
            "externalEntryDiscovery",
        },
        "manager contract requirementsDiscovery.presentationBoundary",
    )
    for field in (
        "defaultSurface",
        "developmentDetailsActivation",
        "enumTranslation",
        "userSuppliedTechnicalTerms",
        "questionReadyBusinessCandidates",
        "implementationBindingDetails",
    ):
        _non_empty_string(
            presentation.get(field),
            f"manager contract requirementsDiscovery.presentationBoundary.{field}",
        )
    for field in (
        "scope",
        "forbiddenDefaultCategories",
        "requiredBusinessEffects",
    ):
        _string_list(
            presentation.get(field),
            f"manager contract requirementsDiscovery.presentationBoundary.{field}",
        )

    if "external-entry-implementation-channel-types" not in presentation[
        "forbiddenDefaultCategories"
    ]:
        raise ManagerContractError(
            "manager contract presentationBoundary must forbid external entry implementation channel types"
        )

    external_entry = _object(
        presentation.get("externalEntryDiscovery"),
        "manager contract requirementsDiscovery.presentationBoundary.externalEntryDiscovery",
    )
    _require_keys(
        external_entry,
        {
            "businessQuestion",
            "preConfirmationChannelEnumeration",
            "implementationChannelTypes",
            "requiredPrerequisite",
            "missingEntryAction",
            "bindingAction",
            "runtimeClaim",
        },
        "manager contract requirementsDiscovery.presentationBoundary.externalEntryDiscovery",
    )
    expected_external_entry_strings = {
        "businessQuestion": "ask-whether-a-real-usable-verified-business-entry-exists",
        "preConfirmationChannelEnumeration": "forbidden-in-assistant-authored-questions",
        "requiredPrerequisite": "trusted-evidence-of-real-usable-entry",
        "missingEntryAction": "label-external-entry-integration-delivery-required-and-block-binding-and-execution",
        "bindingAction": "defer-channel-selection-and-details-until-prerequisite-and-current-card-confirmed",
    }
    for field, expected in expected_external_entry_strings.items():
        actual = _non_empty_string(
            external_entry.get(field),
            f"manager contract requirementsDiscovery.presentationBoundary.externalEntryDiscovery.{field}",
        )
        if actual != expected:
            raise ManagerContractError(
                f"manager contract presentationBoundary.externalEntryDiscovery.{field} is invalid"
            )
    implementation_channel_types = _string_list(
        external_entry.get("implementationChannelTypes"),
        "manager contract requirementsDiscovery.presentationBoundary.externalEntryDiscovery.implementationChannelTypes",
    )
    if implementation_channel_types != [
        "connector",
        "mcp",
        "url",
        "startup-command",
    ]:
        raise ManagerContractError(
            "manager contract presentationBoundary.externalEntryDiscovery.implementationChannelTypes is invalid"
        )
    runtime_claim = _non_empty_string(
        external_entry.get("runtimeClaim"),
        "manager contract requirementsDiscovery.presentationBoundary.externalEntryDiscovery.runtimeClaim",
    )
    if runtime_claim != candidate_evidence["runtimeClaim"]:
        raise ManagerContractError(
            "manager contract external entry and candidate runtime claim policies must agree"
        )

    disclosure = _object(
        requirements.get("capabilityDisclosure"),
        "manager contract requirementsDiscovery.capabilityDisclosure",
    )
    _require_keys(
        disclosure,
        {"businessCard", "machineSkillIdentifiers"},
        "manager contract requirementsDiscovery.capabilityDisclosure",
    )
    _string_list(
        disclosure.get("businessCard"),
        "manager contract requirementsDiscovery.capabilityDisclosure.businessCard",
    )
    _non_empty_string(
        disclosure.get("machineSkillIdentifiers"),
        "manager contract requirementsDiscovery.capabilityDisclosure.machineSkillIdentifiers",
    )

    sections = _string_list(
        requirements.get("businessCardSections"),
        "manager contract requirementsDiscovery.businessCardSections",
    )
    if len(sections) != 8:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.businessCardSections must contain eight sections"
        )
    appendices = _string_list(
        requirements.get("businessCardAppendices"),
        "manager contract requirementsDiscovery.businessCardAppendices",
    )
    if set(sections) & set(appendices):
        raise ManagerContractError(
            "manager contract requirementsDiscovery appendices must not duplicate card sections"
        )

    limits = _object(
        requirements.get("limits"),
        "manager contract requirementsDiscovery.limits",
    )
    _require_keys(
        limits,
        {"perRoundDecisions", "expert", "team"},
        "manager contract requirementsDiscovery.limits",
    )
    per_round = _positive_int(
        limits.get("perRoundDecisions"),
        "manager contract requirementsDiscovery.limits.perRoundDecisions",
    )
    for subject in ("expert", "team"):
        subject_limits = _object(
            limits.get(subject),
            f"manager contract requirementsDiscovery.limits.{subject}",
        )
        _require_keys(
            subject_limits,
            {"rounds", "decisions"},
            f"manager contract requirementsDiscovery.limits.{subject}",
        )
        rounds = _positive_int(
            subject_limits.get("rounds"),
            f"manager contract requirementsDiscovery.limits.{subject}.rounds",
        )
        decisions = _positive_int(
            subject_limits.get("decisions"),
            f"manager contract requirementsDiscovery.limits.{subject}.decisions",
        )
        if decisions > rounds * per_round:
            raise ManagerContractError(
                f"manager contract requirementsDiscovery.limits.{subject}.decisions exceeds its round budget"
            )

    extension = _object(
        requirements.get("extension"),
        "manager contract requirementsDiscovery.extension",
    )
    _require_keys(
        extension,
        {"rounds", "reasons", "explanationRequired"},
        "manager contract requirementsDiscovery.extension",
    )
    _positive_int(
        extension.get("rounds"),
        "manager contract requirementsDiscovery.extension.rounds",
    )
    _string_list(
        extension.get("reasons"),
        "manager contract requirementsDiscovery.extension.reasons",
    )
    _boolean(
        extension.get("explanationRequired"),
        "manager contract requirementsDiscovery.extension.explanationRequired",
    )
    _string_list(
        requirements.get("preConfirmationForbiddenEffects"),
        "manager contract requirementsDiscovery.preConfirmationForbiddenEffects",
    )
    _boolean(
        requirements.get("finalConfirmationConsumesBudget"),
        "manager contract requirementsDiscovery.finalConfirmationConsumesBudget",
    )
    features = _object(
        requirements.get("environmentTaskFeatures"),
        "manager contract requirementsDiscovery.environmentTaskFeatures",
    )
    if not features:
        raise ManagerContractError(
            "manager contract requirementsDiscovery.environmentTaskFeatures must not be empty"
        )
    for task, task_features in features.items():
        _non_empty_string(
            task,
            "manager contract requirementsDiscovery.environmentTaskFeatures task",
        )
        _string_list_allow_empty(
            task_features,
            f"manager contract requirementsDiscovery.environmentTaskFeatures.{task}",
        )


def canonical_json_bytes(value: Any) -> bytes:
    """Return the single canonical JSON encoding used by evidence hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any, *, domain: str) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(value))
    return digest.hexdigest()


def policy_sha256(path: Path = POLICY_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_capabilities_sha256(target: TargetContract) -> str:
    return canonical_json_sha256(
        {
            "capabilityVerified": target.capability_verified,
            "capabilities": target.capabilities,
        },
        domain="mobilework-target-capabilities-v1",
    )


def load_host_contract(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {value}")
            ),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ManagerContractError(f"cannot read host contract {path}: {exc}") from exc
    contract = _object(raw, "host contract")
    unknown = sorted(set(contract) - HOST_CONTRACT_KEYS)
    if unknown:
        raise ManagerContractError(f"host contract contains unknown fields: {', '.join(unknown)}")
    if contract.get("schemaVersion") != 1:
        raise ManagerContractError("host contract schemaVersion must be 1")
    contract["opencodeVersion"] = _version(
        contract.get("opencodeVersion"), "host contract opencodeVersion"
    )
    capabilities = contract.get("capabilities", {})
    if not isinstance(capabilities, dict) or any(
        not isinstance(key, str) or not key for key in capabilities
    ):
        raise ManagerContractError("host contract capabilities must be a string-keyed object")
    contract["capabilities"] = dict(capabilities)
    contract["path"] = str(path)
    return contract


def resolve_target(
    *,
    cli_version: str | None = None,
    env: Mapping[str, str] | None = None,
    host_contract: Path | None = None,
) -> TargetContract:
    environment = os.environ if env is None else env
    host = load_host_contract(host_contract) if host_contract is not None else None
    environment_version = environment.get(TARGET_VERSION_ENV)

    if cli_version is not None:
        version = _version(cli_version, "target OpenCode version")
        source = "cli"
    elif environment_version:
        version = _version(environment_version, TARGET_VERSION_ENV)
        source = "environment"
    elif host is not None:
        version = host["opencodeVersion"]
        source = "host-contract"
    else:
        version = "unknown"
        source = "unknown"

    capabilities = dict(host["capabilities"]) if host is not None else {}
    capability_verified = bool(capabilities) and host is not None and (
        host["opencodeVersion"] == version
    )
    return TargetContract(
        version=version,
        source=source,
        capabilities=capabilities,
        capability_verified=capability_verified,
        host_contract_path=host["path"] if host is not None else "",
    )
