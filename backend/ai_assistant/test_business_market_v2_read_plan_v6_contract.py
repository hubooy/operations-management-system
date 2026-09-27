"""The proposed v6 identity root is a closed plan, never a persisted read."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_read_plan_v6_contract as contract
from .test_business_market_v2_active_synthetic_contract import plan as source_plan


def source():
    prepared = source_plan()
    return {**prepared["plan"], "planId": "f" * 64,
        "planDigest": prepared["planDigest"],
        "agentDispatchSupported": False}


def cost():
    return {"planId": "f" * 64, "ledgerId": "e" * 64,
        "ledgerRequiredCents": 100, "ledgerReservedCents": 0,
        "status": "pending_rate_and_approval_verification",
        "providerCallsAllowed": False}


class MarketV2ReadPlanV6ContractTests(TestCase):
    def test_new_report_fixes_five_distinct_job_slots_and_no_calls(self):
        result = contract.build(source(), cost(),
            "market-v6-report-1", "market-v6-flow-1")
        value = result["snapshot"]
        self.assertEqual(value["executionProfile"], contract.PROFILE)
        self.assertEqual(value["status"], "paused_pre_creation")
        self.assertFalse(value["reportPersisted"])
        self.assertFalse(value["workflowPersisted"])
        self.assertFalse(value["syntheticOnly"])
        self.assertEqual(value["providerRoundCount"], 0)
        self.assertEqual(value["toolCallCount"], 0)
        self.assertEqual(len(value["roleBindings"]), 5)
        self.assertEqual(value["futureRowBindingPolicy"]["readReceipt"],
            ["reportId", "workflowId", "jobId", "providerDispatchId",
                "toolDispatchId"])
        self.assertEqual(len({item["proposedJobId"]
            for item in value["roleBindings"]}), 5)
        self.assertTrue(all(item["reportId"] == value["reportId"]
            and item["workflowId"] == value["workflowId"]
            and item["providerDispatchIds"] == []
            and item["toolDispatchIds"] == []
            and item["readReceiptIds"] == []
            and item["jobPersisted"] is False
            for item in value["roleBindings"]))
        for flag in ("providerCallsAllowed", "agentReadPersisted",
                "numericCitationAllowed", "reportPublishAuthorized"):
            self.assertFalse(value[flag])
        self.assertFalse(result["0069PaidRoundAuthorityVerified"])
        self.assertFalse(result["0072RateAndCapAuthorityVerified"])

    def test_old_identity_paid_claim_or_digest_change_cannot_prepare_v6(self):
        original_plan, original_cost = source(), cost()
        cases = []
        changed = deepcopy(original_plan)
        changed["providerCallsAllowed"] = True
        cases.append((changed, original_cost, "new-report", "new-flow"))
        changed = deepcopy(original_plan)
        changed["modelPolicy"]["modelId"] = "some-model"
        cases.append((changed, original_cost, "new-report", "new-flow"))
        changed = deepcopy(original_plan)
        changed["planDigest"] = "0" * 64
        cases.append((changed, original_cost, "new-report", "new-flow"))
        for field, value in (("ledgerReservedCents", 1),
                ("providerCallsAllowed", True),
                ("status", "authorized"), ("planId", "0" * 64)):
            changed = deepcopy(original_cost)
            changed[field] = value
            cases.append((original_plan, changed, "new-report", "new-flow"))
        cases.extend(((original_plan, original_cost, "execution-1", "new-flow"),
            (original_plan, original_cost, "new-report", "new-report")))
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(AnalysisContractError):
                    contract.build(*item)
