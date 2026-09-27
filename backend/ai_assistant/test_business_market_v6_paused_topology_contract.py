"""Pure regression for model-neutral v6 creation/cancel intent."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, canonical, digest

from . import business_market_v2_read_plan_v6_contract as old
from . import business_market_v6_paused_topology_contract as contract
from .test_business_market_v2_read_plan_v6_contract import (
    source as source_plan, cost as source_cost)


CLIENT = "market-v6-paused-1"


def vector():
    plan = source_plan()
    cost = {**source_cost(), "candidateDigest": "c" * 64,
        "tariffAuthorityVerified": False,
        "humanApprovalAuthorityVerified": False,
        "extraChargeCategoryCoverageVerified": False,
        "currencyConversionVerified": False, "fundsReserved": False}
    owner = plan["executionRoot"]["ownerEmail"]
    source_id = plan["executionRoot"]["executionReportId"]
    fixed = contract.identities(owner, source_id, CLIENT)
    proposal = old.build(plan, cost, fixed["reportId"],
        fixed["workflowId"])
    actor = {"email": owner, "version": 3, "role": "admin",
        "status": "active", "scope": None}
    quota = contract.quota_observation(0, 0, 0, 0)
    return proposal, cost, actor, quota


class PausedTopologyContractTests(TestCase):
    def test_five_persistable_ids_have_zero_provider_and_no_spend_authority(self):
        proposal, cost, actor, quota = vector()
        built = contract.build(proposal, cost, actor, CLIENT, quota)
        body = built["snapshot"]
        self.assertEqual(body["sourceUnverifiedCostCandidateId"],
            cost["ledgerId"])
        self.assertEqual(body["sourceUnverifiedCostCandidateDigest"],
            cost["candidateDigest"])
        self.assertFalse(body["sourceCostCandidateSpendable"])
        self.assertIsNone(body["futureModelSpecificAuthorityId"])
        self.assertIsNone(body["approvedCnyCapCents"])
        self.assertFalse(body["tariffAuthorityVerified"])
        self.assertFalse(body["humanCapApprovalVerified"])
        self.assertFalse(body["durablePaidReservation"])
        self.assertEqual(body["modelId"], "")
        self.assertEqual(body["modelVersion"], 0)
        self.assertEqual(len(body["jobs"]), 5)
        self.assertEqual(len({item["jobId"] for item in body["jobs"]}), 5)
        self.assertEqual(len(body["nodes"]), 6)
        self.assertEqual(sum(item["jobId"] is not None
            for item in body["nodes"]), 5)
        self.assertTrue(all(item["modelId"] == ""
            and item["providerRoundCount"] == item["toolCallCount"] == 0
            and item["providerDispatchIds"] == item["toolDispatchIds"] == []
            and item["readReceiptIds"] == []
            for item in body["jobs"]))
        self.assertEqual(built["intentJson"], canonical(built["intent"]))
        self.assertEqual(built["intentDigest"], digest(built["intent"]))
        for flag in ("providerCallsAllowed", "toolDispatchAllowed",
                "externalProviderCalled", "agentReadPersisted",
                "numericCitationAllowed", "humanReviewApproved",
                "reportPublishAuthorized"):
            self.assertFalse(body[flag], flag)
        self.assertTrue(built["candidateOnly"])
        self.assertTrue(built["quotaObservationOnly"])
        self.assertFalse(built["jobsPersisted"])
        self.assertFalse(quota["sqlQuotaLocked"])

    def test_ids_are_deterministic_but_source_or_client_changes_identity(self):
        proposal, _, actor, _ = vector()
        source_id = proposal["snapshot"]["sourceExecutionReportId"]
        same = contract.identities(actor["email"], source_id, CLIENT)
        self.assertEqual(same["reportId"], proposal["snapshot"]["reportId"])
        self.assertNotEqual(same, contract.identities(actor["email"],
            source_id, CLIENT + "-another"))
        self.assertNotEqual(same, contract.identities(actor["email"],
            "another-source-report", CLIENT))

    def test_quota_observation_is_not_a_persisted_reservation(self):
        allowed = contract.quota_observation(3, 23, 3, 59)
        self.assertEqual((allowed["additionalWorkflows"],
            allowed["additionalJobs"]), (1, 5))
        self.assertFalse(allowed["sqlQuotaLocked"])
        for counts in ((4, 4, 0, 0), (0, 24, 0, 0),
                (0, 0, 4, 4), (0, 0, 0, 60),
                (0, 0, True, 0), (-1, 0, 0, 0), (2, 1, 0, 0)):
            with self.subTest(counts=counts), self.assertRaises(
                    AnalysisContractError):
                contract.quota_observation(*counts)

    def test_paid_claim_actor_or_proposed_job_drift_is_rejected(self):
        proposal, cost, actor, quota = vector()
        cases = []
        wrong = deepcopy(proposal)
        wrong["snapshot"]["modelId"] = "unapproved-model"
        wrong["snapshotJson"] = canonical(wrong["snapshot"])
        wrong["snapshotDigest"] = digest(wrong["snapshot"])
        cases.append((wrong, cost, actor, quota))
        wrong = deepcopy(proposal)
        wrong["snapshot"]["roleBindings"][0]["proposedJobId"] = "wrong-job"
        wrong["snapshotJson"] = canonical(wrong["snapshot"])
        wrong["snapshotDigest"] = digest(wrong["snapshot"])
        cases.append((wrong, cost, actor, quota))
        for field, value in (("ledgerReservedCents", 1),
                ("providerCallsAllowed", True),
                ("tariffAuthorityVerified", True),
                ("humanApprovalAuthorityVerified", True),
                ("candidateDigest", "not-a-digest"),
                ("ledgerId", "0" * 64)):
            wrong = deepcopy(cost); wrong[field] = value
            cases.append((proposal, wrong, actor, quota))
        wrong_actor = {**actor, "version": 0}
        cases.append((proposal, cost, wrong_actor, quota))
        wrong_actor = {**actor, "email": "other@example.invalid"}
        cases.append((proposal, cost, wrong_actor, quota))
        for item in cases:
            with self.subTest(item=item), self.assertRaises(
                    AnalysisContractError):
                contract.build(item[0], item[1], item[2], CLIENT, item[3])

    def test_cancel_and_outcome_never_claim_agent_or_paid_authority(self):
        proposal, _, actor, _ = vector()
        report_id = proposal["snapshot"]["reportId"]
        cancelled = contract.cancel_request(report_id, actor["email"],
            1, "a" * 64)
        self.assertTrue(cancelled["candidateOnly"])
        self.assertFalse(cancelled["providerCallsAllowed"])
        self.assertEqual(cancelled["requestDigest"],
            digest(cancelled["request"]))
        result = {"schemaVersion": contract.OUTCOME_SCHEMA,
            "status": "committed_paused", "reportId": report_id,
            "workflowId": proposal["snapshot"]["workflowId"],
            "ownerEmail": actor["email"], "clientRequestId": CLIENT,
            "requestDigest": "b" * 64, "modelId": "",
            "providerCallsAllowed": False, "agentReadPersisted": False,
            "numericCitationAllowed": False,
            "durablePaidReservation": False}
        self.assertEqual(contract.outcome(result), result)
        for status in ("absent_observed", "cancelled", "conflict", "unknown"):
            self.assertEqual(contract.outcome({**result, "status": status})[
                "status"], status)
        for field, value in (("providerCallsAllowed", True),
                ("agentReadPersisted", True),
                ("durablePaidReservation", True),
                ("modelId", "some-model"), ("status", "ready")):
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                contract.outcome({**result, field: value})
