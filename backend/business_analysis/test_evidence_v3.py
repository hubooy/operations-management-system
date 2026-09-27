"""Prospective mixed daily/monthly plan tests; no Django or source I/O."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from . import evidence_v3
from .contracts import AnalysisContractError, canonical, digest


def request(*windows):
    return {"schemaVersion": "business-analysis-request-v1", "question": "近30天店铺与财务背景",
            "requestedDimensions": ["shop", "category"], "requestedWindows": list(windows or ("current",))}


def daily(*, window="current", start="2026-08-20", end="2026-09-18", key="sales-current"):
    return {"key": key, "domain": "sales", "query": {"platform": "京东", "shop": "志高商用设备旗舰店",
            "channel": "京东", "startDate": start, "endDate": end, "window": window}}


def finance(*, months=None, scope_key="shop:志高商用设备旗舰店", key="finance-context",
            start="2026-08-20", end="2026-09-18"):
    return {"key": key, "domain": "finance", "query": {
        "months": ["2026-08", "2026-09"] if months is None else months,
        "scope": {"scope_key": scope_key, "scope_type": "shop", "scope_name": "志高商用设备旗舰店",
                  "group_name": "京东组"},
        "analysisPeriod": {"startDate": start, "endDate": end}}}


class MixedEvidenceV3Tests(TestCase):
    def test_mixed_catalog_is_deterministic_and_prospective_only(self):
        sources = [finance(), daily()]
        first = evidence_v3.build_catalog(sources, analysis_request=request())
        second = evidence_v3.build_catalog(list(reversed(sources)), analysis_request=request())
        self.assertEqual(first, second)
        self.assertEqual(first["header"]["schemaVersion"], evidence_v3.HEADER_SCHEMA)
        self.assertEqual(first["header"]["sourceCount"], 2)
        self.assertEqual(first["header"]["limits"], {"factBytes": 64*1024*1024, "factPages": 2000})
        for flag in ("sourceAuthorityVerified", "persistentEvidenceVerified",
                     "businessCoverageVerified", "modelAnalysisCompleted", "reportGenerationSupported"):
            self.assertFalse(first["header"][flag])
        self.assertFalse(first["header"]["financePolicy"]["dailyProrationAllowed"])
        self.assertFalse(first["header"]["financePolicy"]["skuProfitAttributionAllowed"])
        entries = {item["domain"]: item for item in first["entries"]}
        self.assertEqual(entries["sales"]["temporalRole"], "daily_fact")
        self.assertEqual(entries["finance"]["temporalRole"], "monthly_context")
        self.assertEqual(entries["finance"]["periodAlignment"]["alignment"], "different_or_partial_months")
        self.assertEqual(entries["finance"]["queryDigest"], digest(entries["finance"]["query"]))
        self.assertEqual(first["planDigest"], digest(first["header"]))
        self.assertEqual(first["header"]["catalogDigest"], digest({"schemaVersion": evidence_v3.CATALOG_SCHEMA,
                                                                   "entries": first["entries"]}))
        self.assertEqual(first["header"]["analysisRequest"], request())
        self.assertNotIn("published", canonical(first))
        self.assertNotIn("sourceDigest", canonical(first))

    def test_exact_full_natural_month_is_distinct_from_30_day_context(self):
        sources = [daily(start="2026-08-01", end="2026-08-31"),
                   finance(months=["2026-08"], start="2026-08-01", end="2026-08-31")]
        result = evidence_v3.build_catalog(sources, analysis_request=request())
        monthly = next(item for item in result["entries"] if item["domain"] == "finance")
        self.assertEqual(monthly["periodAlignment"]["alignment"], "exact_full_months")
        self.assertFalse(monthly["periodAlignment"]["dailyProrationAllowed"])

    def test_daily_contract_still_enforces_existing_windows_and_combinations(self):
        valid = [daily(window="current"), daily(window="previous", key="sales-previous"), finance()]
        evidence_v3.build_catalog(valid, analysis_request=request("current", "previous"))
        unsupported = {"key": "netshop-unsupported", "domain": "netshop", "query": {
            "platform": "天猫", "shop": "店铺", "dataset": "b2b", "startDate": "2026-08-20",
            "endDate": "2026-09-18", "window": "current"}}
        for sources, req in (([daily(), finance()], request("current", "previous")),
                             ([daily(), daily(start="2026-08-21", key="different"), finance()], request()),
                             ([unsupported, finance()], request()),
                             ([dict(daily(), query={**daily()["query"], "extra": "x"}), finance()], request())):
            with self.subTest(sources=sources):
                with self.assertRaises(AnalysisContractError):
                    evidence_v3.build_catalog(sources, analysis_request=req)

    def test_finance_requires_exact_scope_months_and_analysis_period(self):
        changes = (
            {"months": ["2026-08"]},
            {"months": ["2026-08", "2026-10"]},
            {"months": ["2026-09", "2026-08"]},
            {"months": ["2026-08"]*25},
            {"scope": {"scope_key": "shop:同名店", "scope_type": "shop", "scope_name": "同名店"}},
            {"scope": {**finance()["query"]["scope"], "scope_type": "channel"}},
            {"analysisPeriod": {"startDate": "2026-08-21", "endDate": "2026-09-18"}},
            {"shop": "别名"},
        )
        for change in changes:
            with self.subTest(change=change):
                source = finance(); source["query"] = {**source["query"], **change}
                with self.assertRaises(AnalysisContractError):
                    evidence_v3.build_catalog([daily(), source], analysis_request=request())

        full = [f"{year}-{month:02d}" for year in (2025, 2026) for month in range(1, 13)]
        result = evidence_v3.build_catalog([daily(), finance(months=full)], analysis_request=request())
        self.assertEqual(next(item for item in result["entries"] if item["domain"] == "finance")["query"]["months"], full)
        unauthorized = finance()
        unauthorized["query"]["sourceAuthorityVerified"] = True
        with self.assertRaises(AnalysisContractError):
            evidence_v3.build_catalog([daily(), unauthorized], analysis_request=request())

    def test_distinct_group_identity_is_retained_and_duplicate_facts_fail(self):
        other = finance(key="other-group")
        other["query"]["scope"]["group_name"] = "天猫组"
        result = evidence_v3.build_catalog([daily(), finance(), other], analysis_request=request())
        scoped = [item["query"]["scope"]["group_name"] for item in result["entries"] if item["domain"] == "finance"]
        self.assertEqual(sorted(scoped), ["京东组", "天猫组"])
        for bad in ([daily(), finance(), finance(key="duplicate-identity")],
                    [daily(), finance(), finance(scope_key="shop:另一家", key="finance-context")]):
            with self.assertRaises(AnalysisContractError): evidence_v3.build_catalog(bad, analysis_request=request())

    def test_source_count_utf8_and_header_capacity_fail_closed(self):
        sources = [daily()] + [finance(key=f"finance-{index}", scope_key=f"shop:店{index}") for index in range(47)]
        result = evidence_v3.build_catalog(sources, analysis_request=request())
        self.assertEqual(result["header"]["sourceCount"], 48)
        with self.assertRaises(AnalysisContractError):
            evidence_v3.build_catalog(sources + [finance(key="extra", scope_key="shop:extra")], analysis_request=request())
        with patch.object(evidence_v3, "MAX_QUERY_BYTES", 20), self.assertRaises(AnalysisContractError):
            evidence_v3.build_catalog([daily(), finance()], analysis_request=request())
        with patch.object(evidence_v3, "MAX_DIRECTORY_QUERY_BYTES", 10), self.assertRaises(AnalysisContractError):
            evidence_v3.build_catalog([daily(), finance()], analysis_request=request())
        with patch.object(evidence_v3, "MAX_HEADER_BYTES", 20), self.assertRaises(AnalysisContractError):
            evidence_v3.build_catalog([daily(), finance()], analysis_request=request())

    def test_complete_directory_pages_and_untrusted_hashes(self):
        sources = [daily()] + [finance(key=f"finance-{index}", scope_key=f"shop:{index}") for index in range(23)]
        pages, offset = [], 0
        while True:
            page = evidence_v3.directory_page(sources, run_id="evidence-example", evidence_version=7,
                offset=offset, limit=20, analysis_request=request())
            pages.append(page)
            self.assertLessEqual(len(canonical(page).encode("utf-8")), evidence_v3.MAX_DIRECTORY_PAGE_BYTES)
            offset = page["nextOffset"]
            if offset is None: break
        proof = evidence_v3.validate_directory_pages(pages, sources, run_id="evidence-example",
                                                     evidence_version=7, analysis_request=request())
        self.assertEqual(proof["sourceCount"], len(sources))
        self.assertTrue(proof["complete"])
        for modified in (pages[:1], [pages[-1], pages[0]], [pages[0], pages[0]],
                         [dict(pages[0], evidenceVersion=8), *pages[1:]]):
            with self.assertRaises(AnalysisContractError):
                evidence_v3.validate_directory_pages(modified, sources, run_id="evidence-example",
                                                      evidence_version=7, analysis_request=request())
        forged = deepcopy(pages)
        first_finance = next(item for item in forged[0]["items"] if item["domain"] == "finance")
        first_finance["query"]["scope"]["group_name"] = "伪造"
        forged[0]["pageDigest"] = digest({k: v for k, v in forged[0].items() if k != "pageDigest"})
        with self.assertRaises(AnalysisContractError):
            evidence_v3.validate_directory_pages(forged, sources, run_id="evidence-example",
                                                  evidence_version=7, analysis_request=request())

    def test_header_validation_rejects_claimed_authority_and_input_mutation(self):
        sources = [daily(), finance()]
        built = evidence_v3.build_catalog(sources, analysis_request=request())
        expected = evidence_v3.validate_header(built["header"], sources, analysis_request=request())
        expected["financePolicy"].clear()
        self.assertTrue(evidence_v3.build_catalog(sources, analysis_request=request())["header"]["financePolicy"])
        for key in ("reportGenerationSupported", "sourceAuthorityVerified", "persistentEvidenceVerified",
                    "businessCoverageVerified", "modelAnalysisCompleted"):
            forged = deepcopy(built["header"]); forged[key] = True
            with self.assertRaises(AnalysisContractError):
                evidence_v3.validate_header(forged, sources, analysis_request=request())
        original = built["header"]["catalogDigest"]
        sources[1]["query"]["scope"]["group_name"] = "变更组"
        self.assertNotEqual(evidence_v3.build_catalog(sources, analysis_request=request())["header"]["catalogDigest"], original)
