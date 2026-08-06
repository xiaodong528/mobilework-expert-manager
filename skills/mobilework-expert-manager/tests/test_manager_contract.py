from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import manager_contract
import safe_input
import cli_contract
import finding_catalog


class ManagerContractTests(unittest.TestCase):
    def test_policy_has_no_fixed_opencode_version(self) -> None:
        policy = manager_contract.load_policy()
        self.assertEqual(policy["contractVersion"], "2.14.0")
        self.assertRegex(
            policy["contractVersion"],
            manager_contract.CONTRACT_VERSION_RE,
        )
        self.assertNotIn("targetOpenCodeVersion", policy)
        text = (SCRIPT_DIR / "manager-contract.json").read_text(encoding="utf-8")
        self.assertNotIn("1.18.3", text)
        self.assertNotIn("1.16.2", text)

    def test_cli_and_receipt_contract_are_machine_readable(self) -> None:
        policy = manager_contract.load_policy()
        self.assertEqual(
            policy["cli"]["defaultSchemaVersion"],
            cli_contract.DEFAULT_SCHEMA_VERSION,
        )
        self.assertEqual(
            tuple(policy["cli"]["supportedSchemaVersions"]),
            cli_contract.SUPPORTED_SCHEMA_VERSIONS,
        )
        self.assertEqual(
            frozenset(policy["cli"]["exitCodes"].values()),
            cli_contract.VALID_EXIT_CODES,
        )
        self.assertEqual(
            tuple(policy["gates"]["names"]),
            cli_contract.GATE_NAMES,
        )
        self.assertEqual(
            tuple(policy["runtimeStatuses"]),
            cli_contract.RUNTIME_STATUSES,
        )
        receipt = policy["receiptContract"]
        self.assertIn(receipt["writeVersion"], receipt["readVersions"])
        self.assertTrue(
            set(receipt["extendedOwnershipVersions"]).issubset(
                receipt["readVersions"]
            )
        )
        self.assertTrue(
            set(receipt["configLoadableVersions"]).issubset(
                receipt["readVersions"]
            )
        )
        recovery = policy["driftRecovery"]
        self.assertEqual(recovery["schemaVersion"], 1)
        self.assertEqual(
            recovery["publishProtocol"],
            "posix-exclusive-directory-v1",
        )
        self.assertEqual(recovery["dirMode"], 0o700)
        self.assertEqual(recovery["fileMode"], 0o600)
        self.assertEqual(policy["workspaceLock"]["protocolVersion"], 2)
        self.assertEqual(policy["workspaceLock"]["fileMode"], 0o600)
        self.assertEqual(
            policy["workspaceLock"]["platformBackends"],
            {"posix": "implemented", "windows": "implemented"},
        )
        self.assertEqual(
            policy["workspaceLock"]["windowsProtocol"][
                "targetTransactionSecurity"
            ],
            "partial",
        )
        self.assertEqual(
            policy["workspaceLock"]["staleReclaim"],
            "not-implemented",
        )
        self.assertEqual(
            policy["reservedCommands"],
            {
                "source": "validated-host-capabilities",
                "fallbackPolicy": "deny-known-server-builtins",
                "names": ["init", "review"],
            },
        )
        self.assertEqual(
            policy["trustedConversionAdapter"],
            {
                "schemaVersion": 1,
                "implementationStatus": "defined",
                "invocationAuthority": "desktop-host-only",
                "managerDirectExecution": "forbidden",
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
                "unavailableAction": "conversion-required",
                "runtimeEvidenceRequired": True,
            },
        )
        self.assertEqual(
            [rule["code"] for rule in policy["findingCatalog"]["rules"]],
            [entry.code for entry in finding_catalog.ENTRIES],
        )
        self.assertEqual(
            policy["findingCatalog"]["fallbackBySeverity"],
            finding_catalog.FALLBACK_BY_SEVERITY,
        )
        requirements = policy["requirementsDiscovery"]
        self.assertEqual(requirements["schemaVersion"], 11)
        self.assertNotIn(
            "technicalBindingAction",
            requirements["technicalMappingReturn"]["questionSelection"],
        )

    def test_policy_rejects_cli_receipt_and_root_semantic_drift(self) -> None:
        original = manager_contract.load_policy()
        mutations = (
            lambda policy: policy["cli"].update(
                {"supportedSchemaVersions": [2, 3], "defaultSchemaVersion": 3}
            ),
            lambda policy: policy["cli"].update({"formats": ["json", "yaml"]}),
            lambda policy: policy["receiptContract"].update(
                {"writeVersion": 2, "readVersions": [1, 2, 3]}
            ),
            lambda policy: policy["receiptContract"].update(
                {"v3Sha256Fields": ["renamedSha256"]}
            ),
            lambda policy: policy["receiptContract"].update(
                {"projectionSchemaVersion": 9}
            ),
            lambda policy: policy["driftRecovery"].update(
                {"backupIdPattern": "^unsafe$"}
            ),
            lambda policy: policy["driftRecovery"].update(
                {"backupIdPattern": ".*"}
            ),
            lambda policy: policy["driftRecovery"].update(
                {"fileMode": 0o644}
            ),
            lambda policy: policy["driftRecovery"].update(
                {"publishProtocol": "exists-then-rename"}
            ),
            lambda policy: policy["workspaceLock"].update(
                {"protocolVersion": 1}
            ),
            lambda policy: policy["workspaceLock"].update(
                {"fields": ["ownerToken"]}
            ),
            lambda policy: policy.update({"findingCatalogVersion": True}),
            lambda policy: policy.pop("findingCatalog"),
            lambda policy: policy["findingCatalog"].update({"rules": []}),
            lambda policy: policy["findingCatalog"]["rules"][0].update(
                {"pattern": "["}
            ),
            lambda policy: policy["findingCatalog"]["rules"][1].update(
                {"code": policy["findingCatalog"]["rules"][0]["code"]}
            ),
            lambda policy: policy["findingCatalog"]["rules"][0].update(
                {"extra": "drift"}
            ),
            lambda policy: policy["findingCatalog"]["fallbackBySeverity"][
                "warning"
            ].update({"code": "LEGACY_VALIDATION_ERROR"}),
            lambda policy: policy.pop("reservedCommands"),
            lambda policy: policy["reservedCommands"].update(
                {"source": "hardcoded-version"}
            ),
            lambda policy: policy["reservedCommands"].update(
                {"names": ["init"]}
            ),
            lambda policy: policy.pop("trustedConversionAdapter"),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"implementationStatus": "verified"}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"managerDirectExecution": "allowed"}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"forbiddenDirectExecutables": ["parse-document"]}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"inputFields": ["sourceType", "sourceSha256"]}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"outputFields": ["artifactSha256", "provider"]}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"providerFields": ["id", "version"]}
            ),
            lambda policy: policy["trustedConversionAdapter"].update(
                {"runtimeEvidenceRequired": 1}
            ),
            lambda policy: policy.update({"schemaVersion": True}),
            lambda policy: policy.update({"schemaVersion": 1.0}),
        )
        self._assert_policy_mutations_rejected(original, mutations)

    def test_policy_rejects_requirements_shape_and_invariant_drift(self) -> None:
        original = manager_contract.load_policy()
        mutations = (
            lambda policy: policy["requirementsDiscovery"].update(
                {"schemaVersion": True}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"schemaVersion": 9.0}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"ledgerFields": ["decision_id", "decision_id"]}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {
                    "statuses": [
                        status
                        for status in policy["requirementsDiscovery"]["statuses"]
                        if status != "open"
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"askedVia": [None, "tool", False]}
            ),
            lambda policy: policy["requirementsDiscovery"]["decisionIdentity"].pop(
                "basis"
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"materialImpacts": []}
            ),
            lambda policy: policy["requirementsDiscovery"]["limits"]["expert"].update(
                {"rounds": 2.0}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"questionChannelsMutuallyExclusive": 1}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"finalConfirmationConsumesBudget": 0}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"requiredSequence": []}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {
                    "requiredSequence": [
                        "record-or-update-material-decision",
                        "invalidate-prior-confirmation",
                        "merge-current-decision-states-into-card",
                        "reconcile-current-decision-states",
                        "render-complete-updated-business-card",
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"blockedConfirmationScope": True}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "responseGate"
            ].pop("name"),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "responseGate"
            ].update({"sameAssistantTurn": 1}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "responseGate"
            ].update({"beforeSequenceForbidden": []}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"cardDecisionStates": ["open", "unknown"]}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"pendingDecisionStates": ["open", "unknown"]}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"questionEligibleStates": ["confirmed"]}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].pop(
                "stateReconciliation"
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].pop(
                "questionSelection"
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "questionSelection"
            ].update({"candidateStates": ["asked"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "questionSelection"
            ].update({"resolvedPrerequisiteStates": ["open", "confirmed"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "questionSelection"
            ].update({"resolvedPrerequisiteStates": ["blocked"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "questionSelection"
            ].update({"limitSource": "alternate.limit"}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].update({"unavailablePrerequisiteState": "asked"}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].update({"dependentDecisionStates": ["confirmed"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].update({"dependentDecisionStates": ["open"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].pop("dependencyTraversal"),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ]["dependencyCycle"].update({"selfLoopIsCycle": 1}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].update({"resumeStatusValues": ["open"]}),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"][
                "stateReconciliation"
            ].update({"historyAction": False}),
            lambda policy: policy["requirementsDiscovery"]["businessStandards"].update(
                {"unavailableDecisionState": "confirmed"}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"askedViaWriteOnce": 1}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"pendingDecisionAction": False}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"noPendingDecisionAction": False}
            ),
            lambda policy: policy["requirementsDiscovery"]["businessStandards"].pop(
                "authority"
            ),
            lambda policy: policy["requirementsDiscovery"]["businessStandards"].update(
                {"executionWhileUnresolved": False}
            ),
            lambda policy: policy["requirementsDiscovery"]["capabilityDisclosure"].update(
                {"businessCard": []}
            ),
            lambda policy: policy["requirementsDiscovery"].pop(
                "presentationBoundary"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].update({"forbiddenDefaultCategories": []}),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].update({"requiredBusinessEffects": ["effect", "effect"]}),
            lambda policy: policy["requirementsDiscovery"].update(
                {
                    "businessCardSections": policy["requirementsDiscovery"][
                        "businessCardSections"
                    ][:-1]
                }
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {
                    "businessCardAppendices": [
                        policy["requirementsDiscovery"]["businessCardSections"][0]
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"]["limits"]["expert"].update(
                {"decisions": 7}
            ),
            lambda policy: policy["requirementsDiscovery"]["extension"].update(
                {"reasons": []}
            ),
            lambda policy: policy["requirementsDiscovery"]["technicalMappingReturn"].update(
                {"trigger": "   "}
            ),
            lambda policy: policy["requirementsDiscovery"].update(
                {"statuses": [" "]}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "environmentTaskFeatures"
            ].update({"generate": [" "]}),
            lambda policy: policy["requirementsDiscovery"].update(
                {"preConfirmationForbiddenEffects": 1}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "environmentTaskFeatures"
            ].update({"generate": ["core", "core"]}),
        )
        self._assert_policy_mutations_rejected(original, mutations)

    def test_policy_rejects_requirements_schema_10_drift(self) -> None:
        original = manager_contract.load_policy()
        mutations = (
            lambda policy: policy["requirementsDiscovery"].pop(
                "dependencySemantics"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "dependencySemantics"
            ].update({"edgeRequires": ["explicit-user-deferral"]}),
            lambda policy: policy["requirementsDiscovery"][
                "dependencySemantics"
            ].update({"sharedExecutionGuardCreatesEdge": 0}),
            lambda policy: policy["requirementsDiscovery"][
                "dependencySemantics"
            ].update({"sharedExecutionGuardCreatesEdge": True}),
            lambda policy: policy["requirementsDiscovery"].pop(
                "stateTransitionSemantics"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "stateTransitionSemantics"
            ].update({"newCandidateEvidenceReopensExplicitRejection": 0}),
            lambda policy: policy["requirementsDiscovery"][
                "stateTransitionSemantics"
            ].update({"newCandidateEvidenceReopensExplicitRejection": True}),
            lambda policy: policy["requirementsDiscovery"].pop(
                "materialDecisionExecutionGate"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "materialDecisionExecutionGate"
            ].update({"blockingStates": ["open", "asked"]}),
            lambda policy: policy["requirementsDiscovery"][
                "materialDecisionExecutionGate"
            ].update({"requiresCurrentWholeCardConfirmation": 1}),
            lambda policy: policy["requirementsDiscovery"][
                "materialDecisionExecutionGate"
            ].update({"requiresCurrentWholeCardConfirmation": False}),
            lambda policy: policy["requirementsDiscovery"][
                "materialDecisionExecutionGate"
            ].update({"questionFrontierCoupling": "execution-gate"}),
            lambda policy: policy["requirementsDiscovery"].pop(
                "questionChannelEvidence"
            ),
            lambda policy: policy["requirementsDiscovery"].pop(
                "decisionIntroduction"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "decisionIntroduction"
            ]["genericIntentInsufficient"].append("paid-inference-alone"),
            lambda policy: policy["requirementsDiscovery"][
                "decisionIntroduction"
            ]["outboundData"].update(
                {"noConcretePathAction": "ask-a-hypothetical-egress-question"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "decisionIntroduction"
            ]["outboundData"]["guard"].update(
                {"blocksWholeCardDesignConfirmation": True}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "questionChannelEvidence"
            ].pop("hostAssertionRequires"),
            lambda policy: policy["requirementsDiscovery"][
                "questionChannelEvidence"
            ].update({"missingHostEvidenceStatus": "passed"}),
            lambda policy: policy["requirementsDiscovery"][
                "questionChannelEvidence"
            ].update({"skillTextCountsAsHostEvidence": 0}),
            lambda policy: policy["requirementsDiscovery"][
                "questionChannelEvidence"
            ].update({"skillTextCountsAsHostEvidence": True}),
            lambda policy: policy["requirementsDiscovery"][
                "questionChannelEvidence"
            ].update({"assistantSelfReportCountsAsHostEvidence": True}),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].update(
                {"technicalBindingAction": "legacy-conflicting-action"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].pop("readyBatchAction"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].update({"readyBatchAction": " "}),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].pop("unrelatedAskedAction"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].update({"unrelatedAskedAction": False}),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].pop("frontierPrecedence"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].pop(
                "questionReadyOpenAction"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].update(
                {"recomputeAfterBlockerRecovery": 1}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].update(
                {"recomputeAfterBlockerRecovery": False}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].update(
                {"cannotBeDeferredBy": "whole-card-confirmation"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].update(
                {
                    "cannotBeDeferredBy": [
                        "whole-card-confirmation",
                        "whole-card-confirmation",
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["frontierPrecedence"].update(
                {"cannotBeDeferredBy": ["whole-card-confirmation", "other"]}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].update({"businessCandidateAction": False}),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].update({"implementationBindingAction": " "}),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].pop("candidateEvidence"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["candidateEvidence"].pop("runtimeClaim"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["candidateEvidence"].update(
                {"required": False}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["candidateEvidence"].update(
                {
                    "required": [
                        "stable-business-label",
                        "stable-business-label",
                        "trusted-provenance",
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["candidateEvidence"].update(
                {
                    "required": [
                        "stable-business-label",
                        "decision-relevant-differences",
                        "runtime-verified",
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["candidateEvidence"].update(
                {"insufficientAction": []}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"].pop("resolutionRouting"),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["resolutionRouting"].pop(
                "trustedDerivation"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "technicalMappingReturn"
            ]["questionSelection"]["resolutionRouting"].update(
                {"explicitDelegation": " "}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ].pop("decisionPairing"),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].pop("sameRoundWhen"),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update({"components": "authority-source"}),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update(
                {"components": ["authority-source", "authority-source"]}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update(
                {"components": ["authority-source", "other"]}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update({"separateDecisionIds": 1}),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update({"separateDecisionIds": False}),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["decisionPairing"].update({"crossResolution": False}),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ].pop("authorityAnswerWithoutComputableRule"),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["authorityAnswerWithoutComputableRule"].pop("frontierAction"),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["authorityAnswerWithoutComputableRule"].update(
                {"authoritySourceState": "blocked"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "businessStandards"
            ]["authorityAnswerWithoutComputableRule"].update(
                {"executableRuleValueState": "answered"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].pop("questionReadyBusinessCandidates"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].update({"questionReadyBusinessCandidates": []}),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].update({"implementationBindingDetails": " "}),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].pop("externalEntryDiscovery"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("businessQuestion"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop(
                "preConfirmationChannelEnumeration"
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("implementationChannelTypes"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("requiredPrerequisite"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("missingEntryAction"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("bindingAction"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].pop("runtimeClaim"),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"businessQuestion": "ask-for-implementation-channel"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"preConfirmationChannelEnumeration": "allowed"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"implementationChannelTypes": ["connector", "mcp", "url"]}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {
                    "implementationChannelTypes": [
                        "connector",
                        "connector",
                        "url",
                        "startup-command",
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {
                    "implementationChannelTypes": [
                        "connector",
                        "mcp",
                        "url",
                        False,
                    ]
                }
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"requiredPrerequisite": "user-claim-only"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"missingEntryAction": "continue-with-placeholder"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"bindingAction": "bind-before-card-confirmation"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ]["externalEntryDiscovery"].update(
                {"runtimeClaim": "allowed-without-runtime-evidence"}
            ),
            lambda policy: policy["requirementsDiscovery"][
                "presentationBoundary"
            ].update(
                {
                    "forbiddenDefaultCategories": [
                        category
                        for category in policy["requirementsDiscovery"][
                            "presentationBoundary"
                        ]["forbiddenDefaultCategories"]
                        if category
                        != "external-entry-implementation-channel-types"
                    ]
                }
            ),
        )
        self._assert_policy_mutations_rejected(original, mutations)

    def test_requirements_semantics_are_read_from_the_json_source(self) -> None:
        original = manager_contract.load_policy()
        changed = json.loads(json.dumps(original))
        mapping = changed["requirementsDiscovery"]["technicalMappingReturn"]
        mapping["trigger"] = "alternate-material-impact-trigger"
        mapping["responseGate"]["name"] = "alternate-first-block-gate"
        mapping["pendingDecisionAction"] = "alternate-pending-action"
        mapping["stateReconciliation"]["trigger"] = (
            "alternate-state-reconciliation-trigger"
        )
        selection = mapping["questionSelection"]
        selection["businessCandidateAction"] = "alternate-business-action"
        selection["implementationBindingAction"] = "alternate-binding-action"
        selection["frontierPrecedence"]["questionReadyOpenAction"] = (
            "alternate-question-ready-action"
        )
        selection["candidateEvidence"]["insufficientAction"] = (
            "alternate-insufficient-evidence-action"
        )
        selection["resolutionRouting"]["explicitDelegation"] = (
            "alternate-delegation-action"
        )
        selection["readyBatchAction"] = "alternate-ready-batch-action"
        selection["unrelatedAskedAction"] = "alternate-unrelated-asked-action"
        changed["requirementsDiscovery"]["dependencySemantics"][
            "relationUpdate"
        ] = "alternate-relation-update"
        changed["requirementsDiscovery"]["stateTransitionSemantics"][
            "conditionalDeferralAction"
        ] = "alternate-conditional-deferral-action"
        changed["requirementsDiscovery"]["businessStandards"]["decisionPairing"][
            "sameRoundWhen"
        ] = "alternate-same-round-condition"
        changed["requirementsDiscovery"]["businessStandards"][
            "authorityAnswerWithoutComputableRule"
        ]["questionAction"] = "alternate-authority-answer-question-action"
        changed["requirementsDiscovery"]["presentationBoundary"][
            "questionReadyBusinessCandidates"
        ] = "alternate-business-candidate-surface"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manager-contract.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            loaded = manager_contract.load_policy(path)
        self.assertEqual(loaded["requirementsDiscovery"], changed["requirementsDiscovery"])

    def _assert_policy_mutations_rejected(self, original, mutations) -> None:
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                changed = json.loads(json.dumps(original))
                mutate(changed)
                path = Path(temp) / "manager-contract.json"
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(manager_contract.ManagerContractError):
                    manager_contract.load_policy(path)

    def test_evidence_hashes_are_stable_and_domain_separated(self) -> None:
        first = manager_contract.canonical_json_sha256(
            {"b": 2, "a": 1}, domain="first"
        )
        second = manager_contract.canonical_json_sha256(
            {"a": 1, "b": 2}, domain="first"
        )
        other_domain = manager_contract.canonical_json_sha256(
            {"a": 1, "b": 2}, domain="second"
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_domain)
        target = manager_contract.resolve_target(cli_version="1.2.3", env={})
        self.assertEqual(
            manager_contract.target_capabilities_sha256(target),
            manager_contract.target_capabilities_sha256(target),
        )
        self.assertRegex(manager_contract.policy_sha256(), r"^[0-9a-f]{64}$")

    def test_input_limits_are_loaded_from_the_manager_contract(self) -> None:
        policy = manager_contract.load_policy()
        self.assertEqual(
            safe_input.default_limits().as_dict(),
            policy["inputLimits"],
        )
        self.assertLess(
            policy["inputLimits"]["maxTotalBytes"],
            policy["archiveLimits"]["maxTotalUncompressedBytes"],
        )
        self.assertLess(
            policy["inputLimits"]["maxFileBytes"],
            policy["archiveLimits"]["maxEntryUncompressedBytes"],
        )
        self.assertLessEqual(
            policy["inputLimits"]["maxFileBytes"],
            policy["inputLimits"]["maxTotalBytes"],
        )

    def test_package_snapshot_exclusions_are_loaded_from_the_contract(self) -> None:
        policy = manager_contract.load_policy()
        exclusions = safe_input.default_exclusions()
        self.assertEqual(exclusions.as_dict(), policy["packageSnapshotExclusions"])
        self.assertEqual(exclusions.root_directory_names, frozenset({".git"}))
        self.assertFalse(exclusions.directory_names)
        self.assertFalse(exclusions.file_names)
        self.assertFalse(exclusions.file_suffixes)

    def test_target_version_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contract = Path(temp) / "host.json"
            contract.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "opencodeVersion": "v3.2.1",
                        "capabilities": {"references": True},
                    }
                ),
                encoding="utf-8",
            )
            resolved = manager_contract.resolve_target(
                cli_version="4.0.0",
                env={manager_contract.TARGET_VERSION_ENV: "3.9.0"},
                host_contract=contract,
            )
            self.assertEqual(resolved.version, "4.0.0")
            self.assertEqual(resolved.source, "cli")
            self.assertEqual(resolved.capabilities, {"references": True})

            resolved = manager_contract.resolve_target(
                env={manager_contract.TARGET_VERSION_ENV: "3.9.0"},
                host_contract=contract,
            )
            self.assertEqual(resolved.version, "3.9.0")
            self.assertEqual(resolved.source, "environment")

            resolved = manager_contract.resolve_target(env={}, host_contract=contract)
            self.assertEqual(resolved.version, "3.2.1")
            self.assertEqual(resolved.source, "host-contract")

    def test_unknown_target_does_not_claim_capabilities(self) -> None:
        resolved = manager_contract.resolve_target(env={})
        self.assertEqual(resolved.version, "unknown")
        self.assertEqual(resolved.source, "unknown")
        self.assertEqual(resolved.capabilities, {})
        self.assertFalse(resolved.capability_verified)

    def test_version_string_alone_does_not_verify_capabilities(self) -> None:
        resolved = manager_contract.resolve_target(cli_version="9.9.9", env={})
        self.assertEqual(resolved.capabilities, {})
        self.assertFalse(resolved.capability_verified)

    def test_host_contract_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contract = Path(temp) / "host.json"
            contract.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "opencodeVersion": "2.0.0",
                        "unexpected": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(manager_contract.ManagerContractError, "unknown fields"):
                manager_contract.resolve_target(env={}, host_contract=contract)

    def test_host_contract_rejects_non_finite_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contract = Path(temp) / "host.json"
            contract.write_text(
                '{"schemaVersion":1,"opencodeVersion":"1.2.3",'
                '"capabilities":{"references":NaN}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                manager_contract.ManagerContractError,
                "cannot read host contract",
            ):
                manager_contract.resolve_target(env={}, host_contract=contract)

    def test_validator_cli_records_explicit_target_and_contract_errors(self) -> None:
        broken = SCRIPT_DIR.parent / "evals/files/broken-package"
        explicit = subprocess.run(
            [
                sys.executable, str(SCRIPT_DIR / "validate_expert.py"), str(broken),
                "--format", "json", "--target-opencode-version", "7.8.9",
            ],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(explicit.returncode, 1)
        payload = json.loads(explicit.stdout)
        self.assertEqual(payload["provenance"]["targetOpenCode"]["version"], "7.8.9")
        self.assertEqual(payload["provenance"]["targetOpenCode"]["source"], "cli")
        self.assertFalse(payload["provenance"]["targetOpenCode"]["capability_verified"])

        with tempfile.TemporaryDirectory() as temp:
            host = Path(temp) / "host.json"
            host.write_text('{"schemaVersion": 99, "opencodeVersion": "1.0.0"}', encoding="utf-8")
            invalid = subprocess.run(
                [
                    sys.executable, str(SCRIPT_DIR / "validate_expert.py"), str(broken),
                    "--format", "json", "--host-contract", str(host),
                ],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(json.loads(invalid.stdout)["findings"][0]["code"], "MANAGER_VERSION_CONTRACT_ERROR")


if __name__ == "__main__":
    unittest.main()
