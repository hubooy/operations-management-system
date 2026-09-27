from copy import deepcopy
from unittest import TestCase

from . import evidence_seal_v3 as seal_contract, evidence_v3, report_reference_v3 as reference
from .contracts import AnalysisContractError, digest


def fixture():
    daily = {"key": "daily", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
        "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}}
    finance = {"key": "finance", "domain": "finance", "query": {"months": ["2026-08", "2026-09"],
        "scope": {"scope_key": "shop:测试店", "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
        "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}
    built = evidence_v3.build_catalog([daily, finance], analysis_request={
        "schemaVersion": "business-analysis-request-v1", "question": "店铺经营与月财报背景",
        "requestedDimensions": ["shop", "sku"], "requestedWindows": ["current"]})
    proofs = []
    for entry in built["entries"]:
        coverage = ({"kind": "natural_month_publication", "months": ["2026-08", "2026-09"],
            "publishedBits": "10", "missingMonths": ["2026-09"],
            "publicationDigest": "e" * 64, "detailedCoverageDigest": "f" * 64}
            if entry["domain"] == "finance" else
            seal_contract.date_observations(entry["query"], [{"items": [{"date": "2026-08-20"}]}]))
        proofs.append({"sourceKey": entry["key"], "ordinal": entry["ordinal"],
            "domain": entry["domain"], "queryDigest": entry["queryDigest"],
            "sourceVersion": 2, "checkpointDigest": "b" * 64,
            "pageCount": 1, "rowCount": 1, "storedBytes": 100,
            "sourceRef": "c" * 64, "sourceRevision": "1:2", "receiptCount": 1,
            "receiptChainDigest": "d" * 64, "coverage": coverage})
    sealed = seal_contract.make(run_id="evidence-1", evidence_version=3,
        plan_digest=digest(built["header"]),
        catalog_digest=built["header"]["catalogDigest"], sources=proofs, stored_bytes=200)
    return built, sealed


class ReportReferenceV3Tests(TestCase):
    def test_daily_and_monthly_are_separate_non_executable_sources(self):
        built, sealed = fixture()
        value = reference.build(built["header"], built["entries"], sealed)
        self.assertEqual(value["executionProfile"], reference.PROFILE)
        self.assertEqual(len(value["dailyFacts"]), 1)
        self.assertEqual(len(value["financeMonthlyContext"]), 1)
        self.assertEqual(value["financeMonthlyContext"][0]["missingMonths"], ["2026-09"])
        self.assertNotIn("metrics", value["financeMonthlyContext"][0])
        self.assertFalse(value["policy"]["financeSkuProfitAttributionAllowed"])
        self.assertFalse(value["policy"]["sumOverlappingErpB2bAdsAllowed"])
        self.assertFalse(value["modelDispatchSupported"])

    def test_v2_profile_stale_seal_and_wrong_coverages_fail_closed(self):
        built, sealed = fixture()
        with self.assertRaises(AnalysisContractError):
            reference.build(built["header"], built["entries"], sealed, profile="business-agent-reference-v2")
        with self.assertRaises(AnalysisContractError):
            reference.build({**built["header"], "schemaVersion": "business-evidence-v2"}, built["entries"], sealed)
        changed = deepcopy(sealed); changed["sealedDigest"] = "0" * 64
        with self.assertRaises(AnalysisContractError): reference.build(built["header"], built["entries"], changed)
        changed = deepcopy(sealed); changed["sources"][0]["sourceRevision"] = "stale"
        with self.assertRaises(AnalysisContractError): reference.build(built["header"], built["entries"], changed)
        changed = deepcopy(sealed); changed["sources"] = changed["sources"][:-1]
        with self.assertRaises(AnalysisContractError): reference.build(built["header"], built["entries"], changed)
