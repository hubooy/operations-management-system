"""A coherent synthetic topology must never become an Agent-read grant."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_read_admission_v5_contract as contract
from . import business_promotion_market_runtime_v2_contract as runtime
from .test_business_market_v2_result_candidate import root


PLAN = "f" * 64


def observed(profile):
    synthetic = profile == contract.SYNTHETIC_PROFILE
    return {"reportId": "synthetic-1" if synthetic else "execution-1",
        "workflowId": "synthetic-flow-1" if synthetic else "execution-flow-1",
        "ownerEmail": "owner@example.invalid", "profile": profile,
        "sourceExecutionReportId": "execution-1", "planId": PLAN,
        "workflowStatus": "paused",
        "modelId": "market-v2-synthetic-only" if synthetic else "",
        "dryRun": synthetic, "syntheticOnly": synthetic,
        "externalProviderCalled": False,
        "jobRoles": list(runtime.ROLES) if synthetic else [],
        "sameJobProviderToolChainsObserved": synthetic}


class MarketV2ReadAdmissionV5ContractTests(TestCase):
    def test_old_and_five_role_synthetic_are_distinct_closed_observations(self):
        for profile, kind in (("business-agent-screening-promotion-market-execution-v2",
                "parked_execution_v2"), (contract.SYNTHETIC_PROFILE,
                "separate_synthetic_v4")):
            with self.subTest(profile=profile):
                result = contract.assess(root(), PLAN, observed(profile))
                self.assertEqual(result["observedKind"], kind)
                self.assertEqual(result["proposedExecutionProfile"],
                    contract.PROPOSED_PROFILE)
                self.assertFalse(result["agentReadPersisted"])
                self.assertFalse(result["providerCallsAllowed"])
                self.assertFalse(result["numericCitationAllowed"])
                self.assertFalse(result["reportPublishAuthorized"])
                self.assertEqual(len(result["missingAuthorities"]), 5)

    def test_cross_report_synthetic_paid_or_forged_future_profile_fail(self):
        normal = observed(contract.SYNTHETIC_PROFILE)
        cases = []
        for field, value in (("sourceExecutionReportId", "other-execution"),
                ("ownerEmail", "other@example.invalid"),
                ("reportId", "execution-1"),
                ("externalProviderCalled", True), ("dryRun", False),
                ("modelId", "paid-model"), ("jobRoles", ["market_b2b"]),
                ("sameJobProviderToolChainsObserved", False),
                ("profile", contract.PROPOSED_PROFILE)):
            changed = deepcopy(normal)
            changed[field] = value
            cases.append(changed)
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(AnalysisContractError):
                    contract.assess(root(), PLAN, changed)
