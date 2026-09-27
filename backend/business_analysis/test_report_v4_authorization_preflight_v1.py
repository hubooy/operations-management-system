"""Pure old-seal authorization gap and same-report negative vectors."""
from copy import deepcopy
import hashlib
from unittest import TestCase

from . import evidence_v4, report_v4_authorization_preflight_v1 as service
from .contracts import AnalysisContractError, canonical, coverage, digest
from .test_evidence_v4 import measurements
from .period_bound_plan_v1 import prepare_candidate as period_plan, _days
from .test_period_bound_plan_v1 import plan as make_plan


def fixture():
    base = make_plan()
    source_rows = [{"key": entry["sourceKey"],
        "domain": entry["domain"], "query": deepcopy(entry["query"])}
        for entry in base["sourcePlans"]]
    finance = next(row for row in source_rows if row["domain"] ==
        "finance")
    shop = next(row["query"]["shop"] for row in source_rows
        if row["domain"] == "netshop")
    finance["query"]["scope"]["scope_key"] = "shop:京东:" + shop
    plan = evidence_v4.build_plan(client_request_id=base["clientRequestId"],
        sources=source_rows,
        measurements=measurements(("current", "previous", "yearAgo")),
        analysis_request=base["analysisRequest"])
    periods = period_plan(plan)
    shop = periods["dailySources"][0]["shop"]
    sources, bindings, refs = [], [], []
    for index, item in enumerate(plan["sourcePlans"]):
        ref = f"{index + 1:x}" * 64
        revision = ("0:" + "b" * 64 if item["domain"] == "finance"
            else "7:" + "a" * 12)
        common = {"sourceKey": item["sourceKey"],
            "domain": item["domain"],
            "queryDigest": item["queryDigest"],
            "sourceRef": ref, "sourceRevision": revision,
            "liveRevision": revision, "sourceVersion": 2,
            "pageCount": 1, "rowCount": (0 if item["domain"] ==
                "finance" else 30), "storedBytes": 1024,
            "segmentCount": 1, "terminalSegmentDigest": "c" * 64,
            "receiptChainDigest": "d" * 64,
            "revisionFreshness": "current_revision"}
        if item["domain"] == "finance":
            common.update(scope=item["query"]["scope"],
                analysisPeriod=item["query"]["analysisPeriod"],
                missingMonths=[])
        else:
            day = next(row for row in periods["dailySources"] if row[
                "sourceKey"] == item["sourceKey"])
            common.update(window=day["window"],
                coverage=coverage(day["resolvedPeriod"],
                    _days(day["resolvedPeriod"])))
        sources.append(common)
        bindings.append({"sourceKey": item["sourceKey"],
            "ordinal": item["ordinal"], "domain": item["domain"],
            "window": item["query"].get("window"),
            "queryDigest": item["queryDigest"],
            "sourceIdentityDigest": item["sourceIdentityDigest"],
            "sourceRef": ref, "sourceRevision": revision,
            "sourceVersion": 2, "pageCount": 1,
            "rowCount": common["rowCount"], "storedBytes": 1024})
        refs.append({"sourceKey": item["sourceKey"],
            "sourceRef": ref, "sourceRevision": revision,
            "liveRevision": revision,
            "verificationFreshness": "current_revision"})
    raw_plan = canonical(plan)
    body = {"schemaVersion": "business-v4-parent-seal-internal-v1",
        "runId": "v4-run", "attemptId": "attempt-v4",
        "evidenceVersion": 6,
        "planDigest": hashlib.sha256(raw_plan.encode()).hexdigest(),
        "directoryDigest": "e" * 64, "actorVersion": 1,
        "keyId": "a" * 16, "sourceCount": 4,
        "sources": sources, "crossDomainSnapshotAtomic": False,
        "financeDailyProrationAllowed": False,
        "inferSkuProfit": False,
        "sumOverlappingErpB2bAdsAllowed": False,
        "upstreamSignatureVerified": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False,
        "humanReviewRequired": True}
    raw_body = canonical(body)
    seal = {"schemaVersion": "business-v4-internal-seal-verified-v1",
        "runId": "v4-run", "evidenceVersion": 6,
        "sealedDigest": hashlib.sha256(raw_body.encode()).hexdigest(),
        "attemptId": "attempt-v4", "sourceCount": 4,
        "sourceRefs": refs,
        "internalSealVerified": True, "segmentHmacVerified": True,
        "upstreamSignatureVerified": False,
        "crossDomainSnapshotAtomic": False,
        "financeDailyProrationAllowed": False, "inferSkuProfit": False,
        "sumOverlappingErpB2bAdsAllowed": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False}
    seal["proofDigest"] = digest(seal)
    source_bindings = {"schemaVersion":
            "business-v4-report-source-bindings-v1",
        "shop": shop,
        "originalStartDate": periods["dailySources"][0][
            "originalQueryStartDate"],
        "originalEndDate": periods["dailySources"][0][
            "originalQueryEndDate"],
        "sources": bindings,
        "financeShopMappingVerified": False,
        "authorityVerified": False}
    link = {"schemaVersion": "business-v4-report-link-read-candidate-v1",
        "reportId": "report-one", "v2EvidenceRunId": "v2-run",
        "v2EvidenceVersion": 5, "v2SealedDigest": "f" * 64,
        "v4RunId": "v4-run", "v4EvidenceVersion": 6,
        "v4PlanDigest": body["planDigest"],
        "v4SealedDigest": seal["sealedDigest"],
        "reportSnapshotDigest": "1" * 64,
        "workflowInputDigest": "2" * 64,
        "sourceBindingsDigest": "3" * 64,
        "sourceBindings": source_bindings,
        "creationTimeLinkPersisted": True,
        "appHmacVerified": False, "authorityVerified": False,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False,
        "rendererRegistered": False, "downloadSupported": False}
    report = {"reportId": "report-one",
        "ownerEmail": "admin@example.test", "scope": None,
        "reportSnapshotDigest": link["reportSnapshotDigest"],
        "workflowInputDigest": link["workflowInputDigest"],
        "v2EvidenceRunId": link["v2EvidenceRunId"],
        "v2EvidenceVersion": link["v2EvidenceVersion"],
        "v2SealedDigest": link["v2SealedDigest"],
        "shop": shop,
        "originalPeriod": {"startDate": source_bindings[
            "originalStartDate"], "endDate": source_bindings[
            "originalEndDate"]},
        "analysisRequestDigest": periods["analysisRequestDigest"]}
    return report, link, plan, seal, raw_body


class V4AuthorizationPreflightTests(TestCase):
    def test_complete_identity_still_blocks_legacy_report_seal(self):
        inputs = fixture()
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(*inputs)
        value = service.prepare_candidate(*inputs, enabled=True)
        self.assertEqual(value["status"],
            "blocked_legacy_v4_report_authority")
        self.assertEqual(value["reportId"], "report-one")
        self.assertTrue(value["threePromotionWindowsFullyObserved"])
        self.assertFalse(value["v4RowsReferencableIn13Tables"])
        self.assertFalse(value["v4VolumesPublishable"])
        self.assertFalse(value["downloadSupported"])
        self.assertEqual(value["resultDigest"], digest({key: row for key,
            row in value.items() if key != "resultDigest"}))

    def test_cross_report_revision_and_forged_capability_refuse(self):
        report, link, plan, seal, raw = fixture()
        for field, value in (("reportId", "other-report"),
                             ("v2SealedDigest", "a" * 64),
                             ("reportSnapshotDigest", "b" * 64),
                             ("appHmacVerified", True),
                             ("authorityVerified", True)):
            changed = deepcopy(link); changed[field] = value
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                service.prepare_candidate(report, changed, plan, seal,
                    raw, enabled=True)
        changed = deepcopy(link)
        changed["sourceBindings"]["sources"][0]["sourceRevision"] = (
            "8:" + "a" * 12)
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(report, changed, plan, seal, raw,
                enabled=True)

    def test_missing_day_or_unknown_finance_shop_refuses(self):
        report, link, plan, seal, raw = fixture()
        body = deepcopy(service.evidence_seal_v4.read(raw))
        daily = next(row for row in body["sources"] if row["domain"] ==
            "netshop")
        daily["coverage"]["presentDates"].pop()
        period = period_plan(plan)
        window = next(row["resolvedPeriod"] for row in period[
            "dailySources"] if row["sourceKey"] == daily["sourceKey"])
        daily["coverage"] = coverage(window, daily["coverage"][
            "presentDates"])
        changed_raw = canonical(body)
        changed_link, changed_seal = deepcopy(link), deepcopy(seal)
        changed_link["v4SealedDigest"] = changed_seal["sealedDigest"] = (
            hashlib.sha256(changed_raw.encode()).hexdigest())
        changed_seal["proofDigest"] = digest({key: value for key, value in
            changed_seal.items() if key != "proofDigest"})
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(report, changed_link, plan,
                changed_seal, changed_raw, enabled=True)
        body = deepcopy(service.evidence_seal_v4.read(raw))
        finance = next(row for row in body["sources"] if row["domain"] ==
            "finance")
        finance["scope"] = {"scope_key": "business",
            "scope_type": "business", "scope_name": "测试事业部",
            "group_name": ""}
        changed_raw = canonical(body)
        changed_link, changed_seal = deepcopy(link), deepcopy(seal)
        changed_link["v4SealedDigest"] = changed_seal["sealedDigest"] = (
            hashlib.sha256(changed_raw.encode()).hexdigest())
        changed_seal["proofDigest"] = digest({key: value for key, value in
            changed_seal.items() if key != "proofDigest"})
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(report, changed_link, plan,
                changed_seal, changed_raw, enabled=True)

    def test_old_seal_cannot_be_flipped_into_report_capability(self):
        report, link, plan, seal, raw = fixture()
        body = deepcopy(service.evidence_seal_v4.read(raw))
        body["reportGenerationSupported"] = True
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(report, link, plan, seal,
                canonical(body), enabled=True)
