from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_environment
import manager_contract


class RequirementsDiscoveryPolicyTests(unittest.TestCase):
    def test_capability_implementation_mapping_is_machine_readable(self) -> None:
        mapping = manager_contract.load_policy()["requirementsDiscovery"][
            "capabilityImplementationMapping"
        ]
        self.assertEqual(mapping["defaultMode"], "manager-selects-minimal-fit")
        self.assertFalse(mapping["rolePresenceCreatesResource"])
        self.assertEqual(
            mapping["responsibilityTextDirectProjection"], "forbidden"
        )
        self.assertEqual(
            mapping["candidateResourceTypes"],
            ["none", "skill", "custom-tool", "opencode-plugin"],
        )
        self.assertEqual(
            mapping["candidateEvidenceSources"],
            [
                "user-goal",
                "role-responsibility",
                "workflow",
                "quality-requirement",
                "trusted-material",
                "explicit-request",
                "uploaded-resource",
            ],
        )
        self.assertEqual(
            mapping["candidateOnlyEvidenceSources"],
            ["role-responsibility", "workflow", "quality-requirement"],
        )
        self.assertEqual(
            mapping["resourceSemantics"],
            {
                "none": "no-capability-resource-generated",
                "skill": (
                    "reusable-method-checklist-sop-guidance-or-python-shell-"
                    "script-bundle"
                ),
                "custom-tool": (
                    "agent-invoked-deterministic-javascript-or-typescript-"
                    "capability"
                ),
                "opencode-plugin": (
                    "event-listener-tool-interceptor-or-runtime-behavior-modifier"
                ),
            },
        )
        self.assertEqual(
            mapping["selectionConstraints"],
            {
                "externalSystemAccessUsesMcpNotPlugin": True,
                "existingPythonOrShellStaysInSkillExecutor": True,
                "preferLeastRuntimePower": True,
                "oneResourcePerRuntimeResponsibility": True,
                "multiResourceCombinationRequiresDistinctConfirmedRuntimeResponsibilities": True,
                "sharedCapabilityUsesOneResourceWithMultipleRoleReferences": True,
                "localPluginOwnership": "package-wide-not-role-owned",
                "generatedExpertRuntimeMutation": "forbidden",
            },
        )
        self.assertEqual(
            mapping["businessTruthBoundary"],
            {
                "managerMayInferTechnicalMapping": True,
                "managerMayProposeBusinessCapability": True,
                "managerMayInventBusinessCapabilityOrRule": False,
                "candidateRequires": [
                    "stable-business-label",
                    "observable-runtime-behavior",
                    "trusted-provenance",
                ],
                "missingBusinessRuleAction": (
                    "keep-material-decision-open-and-ask-authorized-source"
                ),
            },
        )
        authorization = mapping["authorization"]
        self.assertTrue(authorization["requiresCurrentWholeCardConfirmation"])
        self.assertTrue(authorization["technicalCarrierChoiceDelegatedToManager"])
        self.assertEqual(
            authorization["materialMappingChangeAction"],
            "invalidate-prior-confirmation-and-run-full-card-first",
        )
        self.assertEqual(
            authorization["generationAuthorizationScope"],
            "current-expert-package-resources-only",
        )
        self.assertEqual(
            authorization["generationDoesNotAuthorize"],
            [
                "install",
                "enable",
                "network-download",
                "external-connection",
                "permission-expansion",
                "execute-generated-code",
                "release",
            ],
        )
        self.assertFalse(mapping["naming"]["skill"]["expertOrRolePrefixRequired"])
        self.assertEqual(
            mapping["naming"]["internalReferenceFallback"],
            "<slug>-reference-<alias>",
        )
        self.assertEqual(
            mapping["zeroResourceProjection"],
            {
                "condition": "no-fit-confirmed-capability",
                "topLevelSkills": "empty-or-omitted",
                "roleSkills": "empty-or-omitted",
                "skillsDirectory": "present-and-empty",
                "toolsDirectory": "omitted",
                "pluginsDirectory": "omitted",
                "skillMarkdownCount": 0,
                "customToolCount": 0,
                "pluginCount": 0,
                "opencodePluginConfig": "omitted",
            },
        )
        self.assertEqual(
            mapping["npmPlugin"],
            {
                "requiresTrustedExistingPackage": True,
                "requiresExactVersion": True,
                "versionFormat": "exact-semver-no-range",
                "inventedPackageOrVersion": "forbidden",
            },
        )

    def test_machine_policy_defines_ledger_card_budget_and_side_effects(self) -> None:
        policy = manager_contract.load_policy()["requirementsDiscovery"]
        self.assertEqual(policy["schemaVersion"], 14)
        self.assertEqual(
            [(item["value"], item["label"], item["externalSkill"]) for item in policy["roleAutonomySelection"]["values"]],
            [
                ("scripted", "低", "deny"),
                ("fixed", "较低", "deny"),
                ("bounded", "中", "deny"),
                ("guided", "较高", "ask"),
                ("adaptive", "高", "allow"),
            ],
        )
        self.assertEqual(policy["ledgerPersistence"], "session-only")
        self.assertEqual(
            policy["sources"],
            ["user", "trusted-material", "candidate", "technical-mapping"],
        )
        self.assertEqual(
            policy["statuses"],
            [
                "open",
                "asked",
                "answered",
                "proposed",
                "confirmed",
                "blocked",
                "superseded",
            ],
        )
        self.assertEqual(policy["askedVia"], [None, "tool", "body"])
        self.assertTrue(policy["questionChannelsMutuallyExclusive"])
        self.assertEqual(
            policy["questionChannelLimits"],
            {
                "toolAvailable": "up-to-three-independent-question-ready-decisions",
                "toolUnavailable": "one-concise-composite-business-question",
                "bodyQuestionCount": 1,
                "bodyQuestionMustNotUseNumberedSubquestions": True,
            },
        )
        self.assertEqual(
            policy["questionChannelEvidence"],
            {
                "askedViaSemantics": "agent-session-bookkeeping",
                "hostAssertionRequires": "complete-host-question-channel-ledger",
                "missingHostEvidenceStatus": "not-verified",
                "skillTextCountsAsHostEvidence": False,
                "assistantSelfReportCountsAsHostEvidence": False,
            },
        )
        selection = policy["creationTargetSelection"]
        self.assertEqual(
            selection["appliesTo"],
            [
                "create-expert",
                "create-team",
                "convert-material-to-new-expert",
            ],
        )
        self.assertEqual(
            selection["excludedOperations"],
            ["modify-existing", "install", "validate", "package"],
        )
        self.assertEqual(
            selection["question"],
            {
                "toolPreference": {
                    "recognizedNames": ["AskUserQuestion", "question"],
                    "equivalentCapability": "single-select-with-custom-input",
                    "whenAvailable": "must-use",
                },
                "request": {
                    "header": "安装位置",
                    "question": "确认后，将新专家创建到哪里？",
                    "multiple": False,
                    "custom": True,
                    "options": [
                        {
                            "label": "我的专家（MobileWork 个人专家目录）",
                            "description": (
                                "由 MobileWork 宿主解析；独立运行默认使用 "
                                "~/.mobilework/experts/personal。"
                            ),
                        },
                        {
                            "label": "当前工作空间",
                            "description": "创建到当前工作空间根目录。",
                        },
                    ],
                },
                "replyMapping": {
                    "event": "question.replied",
                    "eventAnswerPath": "properties.answers[0][0]",
                    "requiredQuestionAnswerCount": 1,
                    "requiredSelectedLabelCount": 1,
                    "fixedLabels": [
                        {
                            "label": "我的专家（MobileWork 个人专家目录）",
                            "creationTarget": "my-experts",
                        },
                        {
                            "label": "当前工作空间",
                            "creationTarget": "workspace",
                        },
                    ],
                    "unmatchedSingleAnswer": "custom",
                },
                "fallback": {
                    "when": "no-equivalent-ask-user-tool-available",
                    "channel": "assistant-body",
                    "question": "确认后，将新专家创建到哪里？",
                    "choices": [
                        "我的专家（MobileWork 个人专家目录）",
                        "当前工作空间",
                    ],
                    "customInstruction": "也可以回复其他已存在的绝对父目录。",
                    "mustAwaitReply": True,
                },
            },
        )
        self.assertEqual(
            selection["budget"],
            {
                "consumesDiscoveryRound": False,
                "consumesDecisionBudget": False,
                "invalidatesConfirmedBusinessCard": False,
            },
        )
        self.assertEqual(
            selection["executionGate"]["preAnswerForbidden"],
            [
                "environment-preflight",
                "process",
                "filesystem-write",
                "network",
                "data-egress",
                "plugin",
                "mcp",
                "permission-expansion",
                "generator",
                "validation",
            ],
        )
        self.assertEqual(
            selection["executionGate"]["toolUnavailableAction"],
            "ask-in-conversation",
        )
        self.assertTrue(selection["executionGate"]["bodyFallbackAllowed"])
        self.assertNotIn("questionUnavailable", selection["errors"])
        self.assertEqual(selection["customPath"]["finalPath"], "<parent>/<slug>")
        self.assertEqual(
            selection["customPath"]["forbidden"],
            [
                "filesystem-root",
                "symlink",
                "windows-reparse-point",
                "special-file",
                "path-escape",
            ],
        )
        self.assertEqual(
            policy["ledgerFields"],
            [
                "decision_id",
                "source",
                "status",
                "dependencies",
                "asked_via",
                "blocked_by",
                "resume_status",
            ],
        )
        self.assertEqual(
            policy["decisionIdentity"],
            {
                "scope": "session",
                "basis": "semantic-material-choice",
                "paraphraseCreatesNewDecision": False,
                "newEvidenceAction": "update-existing-decision",
                "unansweredAskedAction": "carry-forward-without-reasking",
            },
        )
        self.assertEqual(
            policy["decisionIntroduction"],
            {
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
                "outboundData": {
                    "questionReadyRequires": (
                        "explicit-user-current-boundary-or-trusted-concrete-external-data-path"
                    ),
                    "noConcretePathAction": (
                        "forbid-network-and-data-egress-and-carry-guard-with-existing-blocker"
                    ),
                    "explicitUserBoundaryAction": (
                        "record-or-update-material-decision"
                    ),
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
                },
            },
        )
        self.assertEqual(
            policy["businessCardSections"],
            [
                "goal-and-success",
                "roles-and-responsibilities",
                "inputs-and-outputs",
                "stable-flow",
                "resources-and-capabilities",
                "external-connections",
                "side-effect-and-cost-boundaries",
                "quality-and-exceptions",
            ],
        )
        self.assertEqual(policy["businessCardAppendices"], ["information-sources"])
        self.assertEqual(
            policy["materialImpacts"],
            ["behavior", "permission", "cost", "runtime-prerequisite"],
        )
        self.assertEqual(
            policy["dependencySemantics"],
            {
                "scope": "selection-resolution-only",
                "edgeRequires": [
                    "explicit-user-deferral",
                    "trusted-evidence-that-prerequisite-changes-candidate-set-or-comparison",
                ],
                "sharedExecutionGuardCreatesEdge": False,
                "executionOnlyNeedAction": (
                    "keep-admitted-and-applicable-material-decision-as-independent"
                ),
                "unresolvedExecutionNeedAction": (
                    "block-generation-and-execution-without-suppressing-independent-question-frontier"
                ),
                "answeredPrerequisiteWithoutCrossResolution": (
                    "preserve-dependent-open-or-asked-and-recompute-frontier"
                ),
                "relationUpdate": (
                    "same-decision-id-with-provenance-without-budget-reset"
                ),
            },
        )
        self.assertEqual(
            policy["stateTransitionSemantics"],
            {
                "negativePrerequisiteAnswer": (
                    "answer-prerequisite-and-preserve-unsolved-dependent-open-or-asked"
                ),
                "conditionalDeferralRequires": (
                    "explicit-future-resolution-condition"
                ),
                "conditionalDeferralAction": (
                    "preserve-original-pending-decision-and-resume-when-explicit-condition-resolves"
                ),
                "explicitFinalRejectionAction": (
                    "block-or-supersede-rejected-path-and-do-not-reask-without-user-revision"
                ),
                "newCandidateEvidenceReopensExplicitRejection": False,
                "authorizationChange": (
                    "explicit-user-evidence-only-with-same-decision-id-and-budget-history"
                ),
                "targetChange": "forbidden-without-user-decision",
            },
        )
        self.assertEqual(
            policy["materialDecisionExecutionGate"],
            {
                "blockingStates": ["open", "asked", "blocked"],
                "requiresCurrentWholeCardConfirmation": True,
                "questionFrontierCoupling": "none",
            },
        )
        self.assertEqual(
            policy["preConfirmationForbiddenEffects"],
            ["filesystem-write", "network", "process", "plugin", "mcp"],
        )
        self.assertEqual(
            policy["extension"],
            {
                "rounds": 1,
                "reasons": ["safety", "permission", "connector"],
                "explanationRequired": True,
            },
        )
        self.assertEqual(
            policy["technicalMappingReturn"],
            {
                "trigger": "any-material-impact-decision-or-change",
                "requiredSequence": [
                    "record-or-update-material-decision",
                    "invalidate-prior-confirmation",
                    "reconcile-current-decision-states",
                    "merge-current-decision-states-into-card",
                    "render-complete-updated-business-card",
                ],
                "responseGate": {
                    "name": "full-card-first",
                    "sameAssistantTurn": True,
                    "sequenceMustBeFirst": True,
                    "beforeSequenceForbidden": [
                        "development-details",
                        "development-confirmation-card",
                        "architecture-or-technical-binding",
                        "blocked-summary",
                        "implementation-options",
                    ],
                    "developmentDetails": (
                        "defer-until-current-whole-card-confirmation"
                    ),
                },
                "stateReconciliation": {
                    "trigger": (
                        "explicit-unavailable-or-not-established-prerequisite-before-tail-selection"
                    ),
                    "unavailablePrerequisiteState": "blocked",
                    "dependentDecisionStates": ["open", "asked"],
                    "dependencyFieldMeaning": (
                        "decision-lists-direct-prerequisite-decision-ids"
                    ),
                    "propagationDirection": "prerequisite-to-dependent",
                    "dependencyTraversal": "transitive-reachable-closure",
                    "recomputeMode": "full-graph-before-every-tail-selection",
                    "graphValidationScope": (
                        "all-ledger-records-regardless-of-status"
                    ),
                    "blockingCondition": (
                        "any-reachable-prerequisite-blocked-or-unavailable"
                    ),
                    "independenceCondition": "no-reachable-blocking-prerequisite",
                    "blockerAggregation": "sorted-unique-root-decision-ids",
                    "explicitRootBlockerRepresentation": (
                        "blocked-by-contains-own-decision-id"
                    ),
                    "resumeStatusValues": ["open", "asked"],
                    "resumeStatusCapture": (
                        "once-on-first-block-from-open-or-asked"
                    ),
                    "unknownDependency": {
                        "code": "REQUIREMENTS_UNKNOWN_DEPENDENCY",
                        "affectedScope": (
                            "owner-and-reachable-pending-dependents"
                        ),
                    },
                    "dependencyCycle": {
                        "code": "REQUIREMENTS_DEPENDENCY_CYCLE",
                        "selfLoopIsCycle": True,
                        "affectedScope": (
                            "cycle-members-and-reachable-pending-dependents"
                        ),
                    },
                    "dependentDecisionAction": (
                        "mark-transitive-reachable-pending-dependents-blocked"
                    ),
                    "independentDecisionAction": "remain-pending",
                    "historyAction": (
                        "preserve-prior-pending-state-decision-id-asked-via-and-budget-charge"
                    ),
                    "explicitRootReopenCondition": (
                        "new-user-or-trusted-evidence-resolves-original-decision"
                    ),
                    "derivedReopenCondition": (
                        "blocked-by-empty-and-dependency-graph-valid"
                    ),
                    "reopenAction": (
                        "restore-prior-pending-state-without-new-id-budget-or-reasking"
                    ),
                    "invalidGraphConfirmation": "forbidden",
                    "invalidGraphGeneration": "forbidden",
                    "invalidGraphTailAction": (
                        "after-card-report-stable-diagnostic-only"
                    ),
                    "tailSelectionBasis": (
                        "reconciled-current-card-states-and-question-ready-frontier"
                    ),
                },
                "questionSelection": {
                    "basis": "reconciled-valid-full-graph",
                    "candidateStates": ["open"],
                    "resolvedPrerequisiteStates": [
                        "answered",
                        "proposed",
                        "confirmed",
                    ],
                    "questionReadyCondition": (
                        "all-transitive-prerequisites-resolved"
                    ),
                    "selection": "current-question-ready-roots-only",
                    "limitSource": (
                        "requirementsDiscovery.limits.perRoundDecisions"
                    ),
                    "askedRootAction": "carry-without-reasking",
                    "readyBatchAction": (
                        "process-all-mutually-independent-question-ready-open-decisions-up-to-per-round-limit"
                    ),
                    "unrelatedAskedAction": (
                        "carry-waiting-state-without-suppressing-other-question-ready-roots"
                    ),
                    "descendantAction": (
                        "defer-without-question-or-budget-charge"
                    ),
                    "noReadyButPendingAction": (
                        "carry-pending-and-forbid-whole-card-confirmation"
                    ),
                    "frontierPrecedence": {
                        "recomputeAfterBlockerRecovery": True,
                        "questionReadyOpenAction": (
                            "ask-after-card-in-same-assistant-turn"
                        ),
                        "cannotBeDeferredBy": [
                            "whole-card-confirmation",
                            "development-details-boundary",
                        ],
                        "technicalChoiceSurface": (
                            "trusted-candidate-selection-in-business-language-only"
                        ),
                    },
                    "businessCandidateAction": (
                        "route-question-ready-choice-after-card-before-whole-card-confirmation"
                    ),
                    "implementationBindingAction": (
                        "defer-identifiers-configuration-credentials-and-field-mapping-until-business-choice-and-current-card-confirmed"
                    ),
                    "candidateEvidence": {
                        "required": [
                            "stable-business-label",
                            "decision-relevant-differences",
                            "trusted-provenance",
                        ],
                        "insufficientAction": (
                            "keep-selection-pending-and-request-only-missing-comparison-evidence"
                        ),
                        "runtimeClaim": "forbidden-without-runtime-evidence",
                    },
                    "resolutionRouting": {
                        "explicitUserChoice": (
                            "ask-question-ready-business-choice"
                        ),
                        "explicitDelegation": (
                            "propose-from-sufficient-trusted-candidate-evidence"
                        ),
                        "trustedDerivation": (
                            "derive-with-provenance-without-reasking"
                        ),
                        "preserveAcrossBlocking": (
                            "decision-id-source-user-authorization-and-budget"
                        ),
                        "insufficientEvidence": (
                            "keep-open-and-request-only-missing-evidence"
                        ),
                    },
                },
                "cardDecisionStates": [
                    "open",
                    "asked",
                    "answered",
                    "proposed",
                    "confirmed",
                    "blocked",
                ],
                "pendingDecisionStates": ["open", "asked"],
                "questionEligibleStates": ["open"],
                "askedViaWriteOnce": True,
                "pendingDecisionAction": (
                    "after-card-ask-only-question-ready-open-decisions"
                ),
                "noPendingDecisionAction": (
                    "after-card-request-single-whole-card-confirmation"
                ),
                "wholeCardConfirmationCondition": (
                    "no-pending-decisions-and-dependency-graph-valid-after-state-reconciliation"
                ),
                "blockedConfirmationScope": "design-only",
                "generation": "forbidden-until-unblocked-and-reconfirmed",
            },
        )
        self.assertEqual(
            policy["businessStandards"],
            {
                "authority": "user-or-trusted-material",
                "threshold": "explicit-and-computable",
                "unsupportedDefaults": "forbidden",
                "unresolvedDecisionState": "open",
                "unavailableDecisionState": "blocked",
                "executionWhileUnresolved": "blocked",
                "decisionPairing": {
                    "components": [
                        "authority-source",
                        "executable-rule-value",
                    ],
                    "separateDecisionIds": True,
                    "sameRoundWhen": "both-question-ready-and-independent",
                    "orderedWhen": (
                        "executable-rule-value-depends-on-authority-source"
                    ),
                    "crossResolution": "explicit-evidence-only",
                    "unsupportedInference": "forbidden",
                },
                "authorityAnswerWithoutComputableRule": {
                    "authoritySourceState": "answered",
                    "executableRuleValueState": "preserve-open-or-asked",
                    "crossResolution": (
                        "none-without-explicit-computable-evidence"
                    ),
                    "frontierAction": (
                        "recompute-and-route-original-rule-decision"
                    ),
                    "questionAction": (
                        "ask-only-for-authorized-explicit-computable-rule-without-changing-target"
                    ),
                },
            },
        )
        self.assertEqual(
            policy["presentationBoundary"],
            {
                "defaultSurface": "business-card",
                "scope": [
                    "business-card",
                    "information-sources-appendix",
                    "discovery-tail-before-current-card-confirmation",
                ],
                "allowedDefaultExceptions": [
                    "role-autonomy-selection-labels",
                ],
                "developmentDetailsActivation": (
                    "explicit-request-after-current-whole-card-confirmation"
                ),
                "forbiddenDefaultCategories": [
                    "manifest-fields",
                    "paths",
                    "hashes",
                    "receipt",
                    "sidecar",
                    "permission-enums",
                    "autonomy-enums",
                    "translated-permission-or-autonomy-level-labels",
                    "machine-skill-identifiers",
                    "machine-custom-tool-identifiers",
                    "machine-plugin-identifiers",
                    "technical-carrier-types",
                    "technical-binding-syntax",
                    "external-entry-implementation-channel-types",
                ],
                "requiredBusinessEffects": [
                    "who-decides",
                    "fixed-vs-discretionary-steps",
                    "confirmation-points",
                    "allowed-side-effects",
                    "stop-rework-escalation",
                ],
                "enumTranslation": (
                    "describe-observable-behavior-without-level-label-or-enum-token"
                ),
                "userSuppliedTechnicalTerms": (
                    "preserve-only-as-provenance-fact-without-expansion"
                ),
                "questionReadyBusinessCandidates": (
                    "trusted-business-labels-and-observable-differences-allowed-before-current-card-confirmation"
                ),
                "implementationBindingDetails": (
                    "defer-until-business-choice-and-current-card-confirmed"
                ),
                "externalEntryDiscovery": {
                    "businessQuestion": (
                        "ask-whether-a-real-usable-verified-business-entry-exists"
                    ),
                    "preConfirmationChannelEnumeration": (
                        "forbidden-in-assistant-authored-questions"
                    ),
                    "implementationChannelTypes": [
                        "connector",
                        "mcp",
                        "url",
                        "startup-command",
                    ],
                    "requiredPrerequisite": (
                        "trusted-evidence-of-real-usable-entry"
                    ),
                    "missingEntryAction": (
                        "label-external-entry-integration-delivery-required-and-block-binding-and-execution"
                    ),
                    "bindingAction": (
                        "defer-channel-selection-and-details-until-prerequisite-and-current-card-confirmed"
                    ),
                    "runtimeClaim": "forbidden-without-runtime-evidence",
                },
            },
        )
        self.assertEqual(
            policy["capabilityDisclosure"],
            {
                "businessCard": [
                    "business-capability-name",
                    "usage-scope",
                    "trigger-or-invocation",
                    "inputs-and-outputs",
                    "visible-side-effects",
                    "permissions-cost-and-runtime-prerequisites",
                    "quality-gates",
                    "implementation-status",
                ],
                "machineResourceIdentifiers": (
                    "development-details-after-current-whole-card-confirmation"
                ),
                "technicalCarrierType": (
                    "manager-selected-after-current-whole-card-confirmation-without-"
                    "extra-confirmation-when-boundaries-are-unchanged"
                ),
            },
        )
        self.assertEqual(
            policy["executionLayerMapping"],
            {
                "scriptOwnsAllConfirmedSteps": "scripted-only",
                "separateFixedLayerRequires": "confirmed-agent-sop-or-branch-rules",
                "businessDisclosure": "plain-language",
                "enumDisclosure": (
                    "development-details-after-current-whole-card-confirmation"
                ),
            },
        )

    def test_external_entry_discovery_keeps_preconfirmation_questions_business_only(
        self,
    ) -> None:
        policy = manager_contract.load_policy()["requirementsDiscovery"]
        presentation = policy["presentationBoundary"]
        external_entry = presentation["externalEntryDiscovery"]
        candidate_evidence = policy["technicalMappingReturn"][
            "questionSelection"
        ]["candidateEvidence"]

        for requirement in ("real", "usable", "verified"):
            self.assertIn(requirement, external_entry["businessQuestion"])
        self.assertEqual(
            external_entry["preConfirmationChannelEnumeration"],
            "forbidden-in-assistant-authored-questions",
        )
        self.assertEqual(
            external_entry["implementationChannelTypes"],
            ["connector", "mcp", "url", "startup-command"],
        )
        self.assertEqual(
            external_entry["requiredPrerequisite"],
            "trusted-evidence-of-real-usable-entry",
        )
        self.assertIn("delivery-required", external_entry["missingEntryAction"])
        self.assertIn(
            "block-binding-and-execution",
            external_entry["missingEntryAction"],
        )
        self.assertIn(
            "current-card-confirmed",
            external_entry["bindingAction"],
        )
        self.assertEqual(
            external_entry["runtimeClaim"],
            candidate_evidence["runtimeClaim"],
        )
        self.assertIn(
            "external-entry-implementation-channel-types",
            presentation["forbiddenDefaultCategories"],
        )

    def test_dependency_closure_only_blocks_decisions_that_depend_on_unavailable_prerequisites(
        self,
    ) -> None:
        mapping = manager_contract.load_policy()["requirementsDiscovery"][
            "technicalMappingReturn"
        ]
        reconciliation = mapping["stateReconciliation"]
        question_selection = mapping["questionSelection"]
        self.assertEqual(
            set(reconciliation["dependentDecisionStates"]),
            set(mapping["pendingDecisionStates"]),
        )
        self.assertNotIn(
            reconciliation["unavailablePrerequisiteState"],
            mapping["pendingDecisionStates"],
        )
        self.assertEqual(
            reconciliation["independentDecisionAction"],
            "remain-pending",
        )
        self.assertEqual(
            reconciliation["dependencyTraversal"],
            "transitive-reachable-closure",
        )
        self.assertIn("any-reachable", reconciliation["blockingCondition"])
        self.assertIn(
            "new-user-or-trusted-evidence",
            reconciliation["explicitRootReopenCondition"],
        )
        self.assertIn(
            "blocked-by-empty",
            reconciliation["derivedReopenCondition"],
        )
        self.assertEqual(
            reconciliation["unknownDependency"]["code"],
            "REQUIREMENTS_UNKNOWN_DEPENDENCY",
        )
        self.assertEqual(
            reconciliation["dependencyCycle"]["code"],
            "REQUIREMENTS_DEPENDENCY_CYCLE",
        )
        self.assertIn(
            "after-state-reconciliation",
            mapping["wholeCardConfirmationCondition"],
        )
        self.assertEqual(
            question_selection["candidateStates"],
            mapping["questionEligibleStates"],
        )
        self.assertEqual(
            question_selection["resolvedPrerequisiteStates"],
            ["answered", "proposed", "confirmed"],
        )
        self.assertEqual(
            question_selection["questionReadyCondition"],
            "all-transitive-prerequisites-resolved",
        )
        self.assertIn("without-question", question_selection["descendantAction"])
        self.assertIn(
            "forbid-whole-card-confirmation",
            question_selection["noReadyButPendingAction"],
        )
        self.assertEqual(
            set(question_selection),
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
        )
        frontier = question_selection["frontierPrecedence"]
        self.assertTrue(frontier["recomputeAfterBlockerRecovery"])
        self.assertEqual(
            set(frontier["cannotBeDeferredBy"]),
            {"whole-card-confirmation", "development-details-boundary"},
        )
        self.assertIn("same-assistant-turn", frontier["questionReadyOpenAction"])
        self.assertIn("business-language", frontier["technicalChoiceSurface"])
        self.assertIn(
            "before-whole-card-confirmation",
            question_selection["businessCandidateAction"],
        )
        self.assertIn(
            "identifiers-configuration-credentials-and-field-mapping",
            question_selection["implementationBindingAction"],
        )
        candidate_evidence = question_selection["candidateEvidence"]
        self.assertEqual(
            set(candidate_evidence["required"]),
            {
                "stable-business-label",
                "decision-relevant-differences",
                "trusted-provenance",
            },
        )
        self.assertIn("only-missing", candidate_evidence["insufficientAction"])
        self.assertIn("runtime-evidence", candidate_evidence["runtimeClaim"])
        routing = question_selection["resolutionRouting"]
        self.assertEqual(
            set(routing),
            {
                "explicitUserChoice",
                "explicitDelegation",
                "trustedDerivation",
                "preserveAcrossBlocking",
                "insufficientEvidence",
            },
        )
        self.assertIn("ask", routing["explicitUserChoice"])
        self.assertIn("propose", routing["explicitDelegation"])
        self.assertIn("derive", routing["trustedDerivation"])
        for preserved in ("decision-id", "source", "authorization", "budget"):
            self.assertIn(preserved, routing["preserveAcrossBlocking"])
        self.assertIn("only-missing-evidence", routing["insufficientEvidence"])

        policy = manager_contract.load_policy()["requirementsDiscovery"]
        dependency = policy["dependencySemantics"]
        transitions = policy["stateTransitionSemantics"]
        execution_gate = policy["materialDecisionExecutionGate"]
        self.assertEqual(dependency["scope"], "selection-resolution-only")
        self.assertFalse(dependency["sharedExecutionGuardCreatesEdge"])
        self.assertIn("independent-question-frontier", dependency["unresolvedExecutionNeedAction"])
        self.assertIn("same-decision-id", dependency["relationUpdate"])
        self.assertIn("preserve-unsolved-dependent", transitions["negativePrerequisiteAnswer"])
        self.assertIn("future-resolution-condition", transitions["conditionalDeferralRequires"])
        self.assertFalse(transitions["newCandidateEvidenceReopensExplicitRejection"])
        self.assertIn("without-user-revision", transitions["explicitFinalRejectionAction"])
        self.assertEqual(
            set(execution_gate["blockingStates"]),
            set(mapping["pendingDecisionStates"]) | {"blocked"},
        )
        self.assertTrue(execution_gate["requiresCurrentWholeCardConfirmation"])
        self.assertEqual(execution_gate["questionFrontierCoupling"], "none")
        self.assertIn("mutually-independent", question_selection["readyBatchAction"])
        self.assertIn("without-suppressing", question_selection["unrelatedAskedAction"])

        pairing = manager_contract.load_policy()["requirementsDiscovery"][
            "businessStandards"
        ]["decisionPairing"]
        self.assertEqual(
            set(pairing),
            {
                "components",
                "separateDecisionIds",
                "sameRoundWhen",
                "orderedWhen",
                "crossResolution",
                "unsupportedInference",
            },
        )
        self.assertEqual(
            pairing["components"],
            ["authority-source", "executable-rule-value"],
        )
        self.assertTrue(pairing["separateDecisionIds"])
        self.assertIn("independent", pairing["sameRoundWhen"])
        self.assertIn("depends-on", pairing["orderedWhen"])
        self.assertEqual(pairing["crossResolution"], "explicit-evidence-only")
        self.assertEqual(pairing["unsupportedInference"], "forbidden")

        missing_rule = manager_contract.load_policy()["requirementsDiscovery"][
            "businessStandards"
        ]["authorityAnswerWithoutComputableRule"]
        self.assertEqual(missing_rule["authoritySourceState"], "answered")
        self.assertEqual(
            missing_rule["executableRuleValueState"], "preserve-open-or-asked"
        )
        self.assertIn("explicit-computable-evidence", missing_rule["crossResolution"])
        self.assertIn("original-rule-decision", missing_rule["frontierAction"])
        self.assertIn("without-changing-target", missing_rule["questionAction"])

        requirements = (SKILL / "references/requirements-discovery.md").read_text(
            encoding="utf-8"
        )
        for example in (
            "链 `A → B → C`",
            "链 `A(open) → B(open)`",
            "同时依赖多个前提",
            "未知 dependency",
            "恢复 `asked` 只携带原等待状态",
            "question-ready root",
        ):
            self.assertIn(example, requirements)

    def test_task_feature_map_keeps_consultation_process_free(self) -> None:
        tasks = manager_contract.load_policy()["requirementsDiscovery"][
            "environmentTaskFeatures"
        ]
        self.assertEqual(tasks["consultation"], [])
        self.assertEqual(tasks["design-confirmation"], [])
        self.assertEqual(tasks["generate"], ["core", "git"])
        self.assertEqual(tasks["validate"], ["core"])
        self.assertEqual(tasks["import"], ["core"])
        self.assertEqual(tasks["import-reference-excel"], ["core", "excel"])
        self.assertEqual(tasks["package"], ["core", "package"])
        self.assertEqual(tasks["bundle-docx"], ["core", "bundle-docx"])
        self.assertEqual(tasks["trusted-config"], ["core", "config-load"])
        self.assertEqual(tasks["evaluation"], ["core", "coverage"])
        self.assertEqual(
            set(tasks),
            {
                "consultation",
                "design-confirmation",
                "generate",
                "validate",
                "import",
                "import-reference-excel",
                "package",
                "bundle-docx",
                "trusted-config",
                "evaluation",
            },
        )
        supported = set(check_environment.FEATURES)
        for features in tasks.values():
            self.assertTrue(set(features).issubset(supported))

    def test_business_card_is_default_and_development_details_are_separate(self) -> None:
        requirements = (SKILL / "references/requirements-discovery.md").read_text(
            encoding="utf-8"
        )
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        template = requirements.split("```markdown\n# 业务确认卡\n", 1)[1].split(
            "```", 1
        )[0]
        for heading in (
            "目标与成功标准",
            "角色与职责",
            "输入与输出",
            "稳定流程",
            "资料与能力",
            "外部连接",
            "读写、联网、权限与成本边界",
            "质量门与异常处理",
        ):
            self.assertIn(heading, template)
        for technical in (
            "expert.json",
            "receipt",
            "sidecar",
            ".opencode/",
            "自主度",
            "低自主度",
        ):
            self.assertNotIn(technical, template)
        self.assertIn("同一决定只能选择一个提问渠道", requirements)
        self.assertIn("不再提出普通", requirements)
        self.assertIn("技术映射", requirements)
        self.assertIn("`full-card-first` 响应门", requirements)
        self.assertIn("同一条 assistant 回复", requirements)
        self.assertIn("回复的**第一块内容**", requirements)
        self.assertIn("立即完整重发八个业务区段", requirements)
        self.assertIn("未变化区段也必须保留", requirements)
        self.assertIn("禁止输出开发确认卡、开发细节", requirements)
        self.assertIn("阻塞摘要、实现", requirements)
        self.assertIn("依赖收敛", requirements)
        self.assertIn("仍可在当前独立回答", requirements)
        self.assertIn(
            "处理当前依赖前沿中全部互不依赖的 `open`", requirements
        )
        self.assertIn("question-ready 依赖前沿", requirements)
        self.assertIn("同一回复刚提出根问题并不等于其已解决", requirements)
        self.assertIn("未就绪下游只说明待前提明确后再决定", requirements)
        self.assertIn("不请求整卡确认", requirements)
        self.assertIn("收敛后没有待决项", requirements)
        self.assertIn("只请求一次当前整卡的 design-only 确认", requirements)
        self.assertIn("即使用户已要求", requirements)
        self.assertIn("也必须延后展开", requirements)
        self.assertIn("不能先解释技术方案再补卡", requirements)
        self.assertIn("不得把\u201c只是更新状态\u201d", requirements)
        self.assertIn("把该前提转为 `blocked`", requirements)
        self.assertIn("open/asked/answered/proposed/confirmed/blocked", requirements)
        self.assertIn("`answered`、`proposed`", requirements)
        self.assertIn("追溯到\nmanager contract", requirements)
        self.assertIn("提升为 `confirmed`", requirements)
        self.assertIn("业务能力名称", requirements)
        self.assertIn("机器 Skill、custom tool 或 Plugin 标识", requirements)
        self.assertIn("### 默认展示边界", requirements)
        self.assertIn("不能出现“低自主度”“高自主度”", requirements)
        self.assertIn("该限制也覆盖 provenance 和卡后提问", requirements)
        self.assertIn("内部枚举、翻译后的等级标签", skill)
        self.assertIn("全部传递前提均为 `answered/proposed/confirmed`", skill)
        self.assertIn("不得从制度名称、版本、零容差", requirements)
        self.assertIn("数值、公式或可计算规则", requirements)
        self.assertIn("两个独立的\n`decision_id`", requirements)
        self.assertIn("互不依赖时才可同轮分别提问", requirements)
        self.assertIn("不会在没有制度正文", requirements)
        self.assertIn("来源决定保持 `answered`", requirements)
        self.assertIn("规则值决定保持 `open/asked`", requirements)
        self.assertIn("不重问来源、不请求整卡确认、不改变规则目标", requirements)
        self.assertIn("question-ready 业务选择优先于整卡确认", requirements)
        self.assertIn("稳定业务标签", requirements)
        self.assertIn("provider ID、URL、配置、凭据、字段映射", requirements)
        self.assertIn("用户明确委托", requirements)
        self.assertIn("只询问缺失的比较证据", requirements)
        self.assertIn(
            "Skill 正文和 assistant 自述都不能充当 host evidence",
            requirements,
        )
        self.assertIn("`scripted` 执行", requirements)
        self.assertIn("`fixed` 编排", requirements)
        self.assertIn("若脚本拥有全部已确认步骤", requirements)
        self.assertIn("不凭空增加独立 `fixed` 层", requirements)
        self.assertIn("不另设 Agent 编排层", requirements)
        self.assertIn("只携带“等待答复”状态", requirements)
        self.assertIn("不得退回 `open`、换 id", requirements)
        self.assertIn("不重复计费", requirements)
        self.assertIn("共享同一个执行权限、成本或安全门时不能连边", requirements)
        self.assertIn("不抑制其他 ready", requirements)
        self.assertIn("这条全局门不属于 question frontier", requirements)
        self.assertIn("数据边界才是模型选择的真实前提", requirements)
        self.assertIn("不能仅凭“付费推理”", requirements)
        self.assertIn("具体、当前候选或执行路径", requirements)
        self.assertIn("不新增 `open/asked` 决定", requirements)
        self.assertIn("不能把安全守卫解释为用户已经同意外发", requirements)
        self.assertIn("明确最终拒绝某条路径", requirements)
        self.assertIn("条件性延期不是拒绝", requirements)
        self.assertIn("明确给出未来\n解决条件", requirements)
        self.assertIn("provenance 附录，不计入八个业务区段", requirements)
        self.assertIn("宿主原生的只读/context API", requirements)
        self.assertIn("不能通过 shell、CLI", requirements)
        self.assertIn("是否确认这份业务设计？", requirements)
        self.assertIn("任务没有 feature 就交付并停止", requirements)
        self.assertIn("--feature core --feature git", requirements)
        self.assertIn("**`full-card-first` 硬门：**", skill)
        self.assertIn("第一块内容必须先明确旧确认已失效", skill)
        self.assertIn("此前不得先给开发确认卡、开发细节", skill)
        self.assertIn("卡后动作前先按传递依赖闭包重算状态", skill)
        self.assertIn("真正无可达 blocker 的决定继续待决", skill)
        self.assertIn("不能因两个决定共用执行权限门而连边", skill)
        self.assertIn("无关 `asked` 决定当作附加前提", skill)
        self.assertIn("未决权限、成本或数据外发边界", skill)
        self.assertIn("不能仅凭“付费推理”", skill)
        self.assertIn("不新增 `open/asked` 决定", skill)
        self.assertIn("显式最终拒绝的路径", skill)
        self.assertIn("条件性延期必须来自用户明确的未来解决条件", skill)
        self.assertIn("图有效且没有待决项时（包括其余项为 `blocked`）", skill)
        self.assertIn("当前整卡确认前", skill)
        self.assertIn("也只用可观察的业务行为", skill)

        gate = requirements.split("### `full-card-first` 响应门", 1)[1].split(
            "确认后、执行前", 1
        )[0]
        ordered_markers = (
            "旧确认已失效",
            "尾随动作判定前先做依赖收敛",
            "合并进卡",
            "立即完整重发八个业务区段",
            "整卡之后才执行一种尾随动作",
        )
        positions = [gate.index(marker) for marker in ordered_markers]
        self.assertEqual(positions, sorted(positions))

    def test_all_without_sidecar_fails_before_environment_checks(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(
            check_environment, "check_environment"
        ) as check, patch.object(
            check_environment, "explicit_sidecar_status"
        ) as sidecar_status, patch.object(
            check_environment.manager_contract, "resolve_target"
        ) as resolve_target, patch.object(
            check_environment, "module_status"
        ) as module_status, patch.object(
            check_environment, "command_status"
        ) as command_status, patch.object(
            check_environment.execution_context, "resolve_execution_context"
        ) as resolve_context, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
            stderr
        ):
            code = check_environment.main(["--feature", "all"])

        self.assertEqual(code, 2)
        check.assert_not_called()
        sidecar_status.assert_not_called()
        resolve_target.assert_not_called()
        module_status.assert_not_called()
        command_status.assert_not_called()
        resolve_context.assert_not_called()
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["operation"], "check-environment")
        self.assertEqual(
            payload["findings"][0]["code"], "ENVIRONMENT_SIDECAR_REQUIRED"
        )
        self.assertFalse(payload["execution"]["attempted"])
        self.assertIn("ENVIRONMENT_SIDECAR_REQUIRED", stderr.getvalue())

    def test_all_with_sidecar_enters_read_only_preflight(self) -> None:
        sidecar = Path("/tmp/caller-reviewed-sidecar")
        result = {
            "ok": True,
            "features": list(check_environment.FEATURES),
            "checks": [],
            "missing": [],
            "executionContext": {},
            "hostMode": "workspace",
            "workspaceRoot": "/tmp/workspace",
            "outputRoot": "/tmp/workspace",
            "pathSource": "cwd",
            "errors": [],
        }
        stdout = io.StringIO()
        with patch.object(
            check_environment, "check_environment", return_value=result
        ) as run, contextlib.redirect_stdout(stdout):
            code = check_environment.main(
                ["--feature", "all", "--sidecar", str(sidecar)]
            )
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["sidecar"], sidecar)
        self.assertEqual(json.loads(stdout.getvalue())["data"]["features"], list(check_environment.FEATURES))

    def test_schema_v1_adapts_the_same_environment_result(self) -> None:
        result = {
            "ok": True,
            "features": ["core"],
            "checks": [],
            "missing": [],
            "executionContext": {},
            "hostMode": "workspace",
            "workspaceRoot": "/tmp/workspace",
            "outputRoot": "/tmp/workspace",
            "pathSource": "cwd",
            "errors": [],
        }
        stdout = io.StringIO()
        with patch.object(
            check_environment, "check_environment", return_value=result
        ), contextlib.redirect_stdout(stdout):
            code = check_environment.main(["--schema-version", "1"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["features"], ["core"])

    def test_multiturn_catalog_uses_compatible_extension_fields(self) -> None:
        evals = json.loads((SKILL / "evals/evals.json").read_text(encoding="utf-8"))[
            "evals"
        ]
        self.assertEqual(len(evals), 44)
        by_id = {item["id"]: item for item in evals}
        self.assertEqual(
            {
                item["id"]
                for item in evals
                if "host_expectation_indexes" in item
            },
            {104, 106, 114, 139, 140, 144},
        )
        self.assertEqual(
            {
                eval_id: by_id[eval_id]["host_expectation_indexes"]
                for eval_id in (104, 106, 114, 139, 140, 144)
            },
            {
                104: [2, 3],
                106: [5],
                114: [4],
                139: [2, 6],
                140: [7],
                144: [7],
            },
        )
        for eval_id in (104, 106, 114, 139, 140, 144):
            item = by_id[eval_id]
            host_indexes = item["host_expectation_indexes"]
            self.assertEqual(len(host_indexes), len(set(host_indexes)))
            self.assertTrue(
                all(0 <= index < len(item["expectations"]) for index in host_indexes)
            )
            visible_indexes = sorted(
                set(range(len(item["expectations"]))) - set(host_indexes)
            )
            self.assertEqual(
                visible_indexes,
                [
                    index
                    for index in range(len(item["expectations"]))
                    if index not in host_indexes
                ],
            )
        self.assertIn(
            "only if development details are requested",
            by_id[114]["expectations"][2],
        )
        self.assertIn(
            "carried only as pending status",
            by_id[139]["expectations"][1],
        )
        for eval_id in (104, 139, 140):
            item = by_id[eval_id]
            self.assertIn("multi-turn", item["suites"])
            self.assertGreaterEqual(len(item["conversation"]), 3)
            self.assertEqual(
                item["critical_expectation_indexes"],
                list(range(len(item["expectations"]))),
            )
        delegated = by_id[106]["expectations"]
        self.assertTrue(any("zero ordinary discovery questions" in item for item in delegated))
        self.assertTrue(
            any("business capabilities" in item for item in delegated)
        )
        self.assertTrue(
            any("machine Skill identifiers" in item for item in delegated)
        )
        dynamic = by_id[144]
        dynamic_prompt = dynamic["prompt"]
        self.assertEqual(dynamic["conversation"][0]["content"], dynamic_prompt)
        for forbidden_technical_choice in (
            "managed Skill",
            "Custom Tool",
            "local Plugin",
            ".opencode/",
        ):
            self.assertNotIn(forbidden_technical_choice, dynamic_prompt)
        for confirmed_scoring_rule in (
            "missing_items 是 original_evidence 为空的 id",
            "四舍五入到两位小数",
            "空列表得 0 分",
            "列表非空且 missing_items 为空时 evidence_complete 才为 true",
        ):
            self.assertIn(confirmed_scoring_rule, dynamic_prompt)
        standards = by_id[114]["expectations"]
        self.assertEqual(by_id[114]["critical_expectation_indexes"], [0, 1, 2, 3])
        self.assertIn("业务负责人直接确认", by_id[114]["prompt"])
        self.assertIn("只用于审计追溯", by_id[114]["prompt"])
        self.assertIn("不定义、也不能推出容差", by_id[114]["prompt"])
        self.assertIn("两个彼此独立的决定", by_id[114]["prompt"])
        self.assertIn("same round", standards[1])
        self.assertIn("two separate independent questions", standards[1])
        self.assertIn("used only for audit traceability", standards[1])
        self.assertIn("does not merge them", standards[1])
        self.assertIn("invent a dependency", standards[1])
        self.assertTrue(any("unsupported default" in item for item in standards))
        mapping_return = by_id[140]["expectations"]
        self.assertTrue(
            any(
                "second assistant turn" in item
                and "business-language question-ready root decisions" in item
                and "downstream binding" in item
                for item in mapping_return
            )
        )
        self.assertTrue(
            any(
                "immediately renders all eight" in item
                and "prerequisites marked blocked" in item
                for item in mapping_return
            )
        )
        self.assertTrue(
            any(
                "recomputing the dependency graph" in item
                and "no open or asked decision remains" in item
                for item in mapping_return
            )
        )
        self.assertIn(
            "translated autonomy level labels",
            by_id[139]["expectations"][5],
        )

    def test_multiturn_conversation_replaces_prompt_and_requires_full_transcript(self) -> None:
        requirements = (SKILL / "evals/README.md").read_text(encoding="utf-8")
        self.assertIn("`conversation` replaces `prompt`", requirements)
        self.assertIn("after every user turn", requirements)
        self.assertIn("transcript.json", requirements)
        self.assertIn("tool/event ledger", requirements)
        self.assertIn('status: "not-verified"', requirements)
        self.assertIn("generator-invocation", requirements)
        self.assertIn("interaction-tool events", requirements)
        self.assertIn("`host_expectation_indexes`", requirements)
        self.assertIn("zero-based list of unique expectation", requirements)
        self.assertIn("`visible = all - host`", requirements)
        self.assertIn("complete, independent host question-channel ledger", requirements)
        self.assertIn("assistant\n  self-report are not host evidence", requirements)
        self.assertIn("[not-verified]", requirements)
        self.assertIn("values are `null`", requirements)
        self.assertIn("seed `42`", requirements)
        self.assertIn("24-item train", requirements)
        self.assertIn("16-item held-out", requirements)
        self.assertIn("`pr-smoke` | 8", requirements)
        self.assertIn("`release-benchmark` | 13", requirements)
        self.assertIn("`full` | 44", requirements)

    def test_behavior_catalog_declares_nested_executable_suites(self) -> None:
        evals = json.loads((SKILL / "evals/evals.json").read_text(encoding="utf-8"))[
            "evals"
        ]
        allowed_suites = {
            "pr-smoke",
            "release-benchmark",
            "full",
            "requirements-discovery",
            "multi-turn",
        }
        self.assertTrue(all(item.get("suites") for item in evals))
        self.assertTrue(
            all(set(item["suites"]).issubset(allowed_suites) for item in evals)
        )

        suite_ids = {
            suite: {
                item["id"]
                for item in evals
                if suite in item["suites"]
            }
            for suite in ("pr-smoke", "release-benchmark", "full")
        }
        self.assertEqual(
            suite_ids["pr-smoke"],
            {101, 104, 106, 108, 109, 114, 139, 140},
        )
        self.assertEqual(
            suite_ids["release-benchmark"],
            {101, 104, 106, 108, 109, 110, 114, 127, 139, 140, 141, 142, 143},
        )
        self.assertEqual(suite_ids["full"], set(range(101, 145)))
        self.assertLess(suite_ids["pr-smoke"], suite_ids["release-benchmark"])
        self.assertLess(suite_ids["release-benchmark"], suite_ids["full"])

        by_id = {item["id"]: item for item in evals}
        for eval_id in (104, 139, 140):
            self.assertIn("multi-turn", by_id[eval_id]["suites"])
            self.assertIn("requirements-discovery", by_id[eval_id]["suites"])
        self.assertEqual(by_id[114]["critical_expectation_indexes"], [0, 1, 2, 3])
        self.assertEqual(
            by_id[104]["critical_expectation_indexes"],
            list(range(len(by_id[104]["expectations"]))),
        )
        self.assertEqual(
            by_id[139]["critical_expectation_indexes"],
            list(range(len(by_id[139]["expectations"]))),
        )
        self.assertEqual(
            by_id[140]["critical_expectation_indexes"],
            list(range(len(by_id[140]["expectations"]))),
        )


if __name__ == "__main__":
    unittest.main()
