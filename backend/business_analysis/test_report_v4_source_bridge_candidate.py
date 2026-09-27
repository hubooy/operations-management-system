"""Pure negative checks for the still-closed v4/report identity bridge."""
from copy import deepcopy
from unittest import TestCase

from . import report_v4_source_bridge_candidate as bridge
from .contracts import AnalysisContractError, digest
from .test_period_bound_plan_v1 import plan as make_plan


def fixture():
    plan = make_plan()
    report = {"schemaVersion": bridge.INTENT_SCHEMA,
        "reportId": "report-1", "ownerEmail": "admin@example.test",
        "scope": None, "clientRequestId": plan["clientRequestId"],
        "reportSnapshotDigest": "1" * 64,
        "workflowInputDigest": "2" * 64,
        "v2EvidenceRunId": "evidence-1", "v2EvidenceVersion": 5,
        "v2SealedDigest": "3" * 64,
        "analysisRequest": plan["analysisRequest"],
        "platform": "京东",
        "shop": next(row["query"]["shop"] for row in plan["sourcePlans"]
            if row["domain"] == "netshop"),
        "originalPeriod": {"startDate": "2026-08-16",
            "endDate": "2026-09-14"},
        "v4RunId": "v4-1", "v4PlanDigest": plan["planDigest"],
        "declaredAtReportCreation": True}
    seal = {"schemaVersion": "business-v4-internal-seal-verified-v1",
        "runId": "v4-1", "evidenceVersion": 7,
        "sealedDigest": "4" * 64, "attemptId": "attempt-1",
        "sourceCount": len(plan["sourcePlans"]),
        "sourceRefs": [{"sourceKey": row["sourceKey"],
            "sourceRef": format(index + 5, "x") * 64,
            "sourceRevision": "7:" + "a" * 64,
            "liveRevision": "7:" + "a" * 64,
            "verificationFreshness": "current_revision"}
            for index, row in enumerate(plan["sourcePlans"])],
        "internalSealVerified": True, "segmentHmacVerified": True,
        "upstreamSignatureVerified": False,
        "crossDomainSnapshotAtomic": False,
        "financeDailyProrationAllowed": False, "inferSkuProfit": False,
        "sumOverlappingErpB2bAdsAllowed": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False}
    seal["proofDigest"] = digest(seal)
    period = bridge.period_bound_plan_v1.prepare_candidate(plan)
    references = {row["sourceKey"]: row for row in seal["sourceRefs"]}
    report["v4SealedDigest"] = seal["sealedDigest"]
    report["v4PeriodPlanDigest"] = period["periodPlanDigest"]
    report["promotionSources"] = [{"window": row["window"],
        "sourceKey": row["sourceKey"],
        "queryDigest": row["queryDigest"],
        "sourceRef": references[row["sourceKey"]]["sourceRef"],
        "sourceRevision": references[row["sourceKey"]]["sourceRevision"]}
        for row in sorted(period["dailySources"], key=lambda item:
            bridge.period_bound_plan_v1.WINDOWS.index(item["window"]))]
    finance = period["financeContext"]
    report["financeSource"] = {"sourceKey": finance["sourceKey"],
        "queryDigest": finance["queryDigest"],
        "sourceRef": references[finance["sourceKey"]]["sourceRef"],
        "sourceRevision": references[finance["sourceKey"]][
            "sourceRevision"]}
    report["intentDigest"] = digest(report)
    return report, plan, seal


class ReportV4SourceBridgeCandidateTests(TestCase):
    def test_same_intent_three_windows_still_cannot_read_or_publish(self):
        report, plan, seal = fixture()
        with self.assertRaises(AnalysisContractError):
            bridge.prepare_candidate(report, plan, seal)
        value = bridge.prepare_candidate(report, plan, seal, enabled=True)
        self.assertEqual(next(row["measuredRowCount"] for row in
            plan["sourcePlans"] if row["sourceKey"] ==
            "promotion-current"), 575_095)
        self.assertEqual(value["reportId"], report["reportId"])
        self.assertEqual(value["v4SealedDigest"], seal["sealedDigest"])
        self.assertEqual(value["periodPlanDigest"],
            bridge.period_bound_plan_v1.prepare_candidate(plan)[
                "periodPlanDigest"])
        self.assertEqual([row["window"] for row in value[
            "promotionWindows"]], ["current", "previous", "yearAgo"])
        self.assertTrue(value["candidateOnly"])
        for key in ("persistedSameReportLinkVerified",
                    "v4RowsReadableForReport", "agentCitationSupported",
                    "registeredRenderer", "publishable"):
            self.assertIs(value[key], False)
        self.assertEqual(value["candidateDigest"], digest({key: part
            for key, part in value.items() if key != "candidateDigest"}))

    def test_cross_report_or_retroactive_claim_refuses_even_when_rehashed(self):
        report, plan, seal = fixture()
        for name, value in (("v4RunId", "other-v4"),
                            ("v4PlanDigest", "a" * 64),
                            ("v4SealedDigest", "a" * 64),
                            ("v4PeriodPlanDigest", "a" * 64),
                            ("clientRequestId", "other-request"),
                            ("shop", "another-shop"),
                            ("originalPeriod", {"startDate": "2026-08-17",
                                "endDate": "2026-09-14"}),
                            ("analysisRequest", {**plan["analysisRequest"],
                                "question": "other request"}),
                            ("promotionSources", [{**report[
                                "promotionSources"][0], "sourceRef": "f" * 64},
                                *report["promotionSources"][1:]]),
                            ("financeSource", {**report["financeSource"],
                                "sourceRevision": "8:" + "a" * 64}),
                            ("declaredAtReportCreation", False)):
            changed = deepcopy(report)
            changed[name] = value
            changed["intentDigest"] = digest({key: part for key, part in
                changed.items() if key != "intentDigest"})
            with self.subTest(name=name), self.assertRaises(
                    AnalysisContractError):
                bridge.prepare_candidate(changed, plan, seal, enabled=True)
        # A different report ID with a recomputed self-hash remains only a
        # different *candidate*; no DB-owned same-report link is inferred.
        other = deepcopy(report)
        other["reportId"] = "report-2"
        other["intentDigest"] = digest({key: part for key, part in
            other.items() if key != "intentDigest"})
        result = bridge.prepare_candidate(other, plan, seal, enabled=True)
        self.assertEqual(result["reportId"], "report-2")
        self.assertFalse(result["persistedSameReportLinkVerified"])

    def test_missing_stale_or_cross_source_seal_refuses(self):
        report, plan, seal = fixture()
        for field, value in (("sourceKey", "other-source"),
                             ("sourceRef", "f" * 64),
                             ("liveRevision", "8:" + "a" * 64),
                             ("verificationFreshness", "historical_revision")):
            changed = deepcopy(seal)
            changed["sourceRefs"][0][field] = value
            changed["proofDigest"] = digest({key: part for key, part in
                changed.items() if key != "proofDigest"})
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                bridge.prepare_candidate(report, plan, changed,
                    enabled=True)
        changed = deepcopy(seal)
        changed["sourceRefs"].pop()
        changed["sourceCount"] -= 1
        changed["proofDigest"] = digest({key: part for key, part in
            changed.items() if key != "proofDigest"})
        with self.assertRaises(AnalysisContractError):
            bridge.prepare_candidate(report, plan, changed, enabled=True)
