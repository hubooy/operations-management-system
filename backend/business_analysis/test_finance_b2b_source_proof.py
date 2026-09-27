"""Pure finance-month/B2B-window proof rows with explicit missing states."""
from copy import deepcopy
from unittest import TestCase

from . import finance_b2b_source_proof as proof
from .contracts import AnalysisContractError, digest
from .finance_source import CORE_METRICS


def finance():
    def month(value, published):
        return {"month": value, "published": published,
            "metrics": {name: {"status": "present" if published else "missing_month",
                "value": 100 if published else None,
                "unit": "CNY_cent" if kind == "amount" else "basis_point"}
                for name, kind in CORE_METRICS.items()}}
    query = {"months": ["2026-08", "2026-09"],
        "scope": {"scope_type": "shop", "scope_name": "ERP别名店"},
        "analysisPeriod": {"startDate": "2026-08-16", "endDate": "2026-09-14"}}
    value = {"schemaVersion":
        "business-finance-v3-report-monthly-material-candidate-v1",
        "intentId": "finance-intent", "sourceKey": "finance-months",
        "sourceRef": "a" * 64, "sourceRevision": "1:b",
        "sealedDigest": "b" * 64, "receiptChainDigest": "c" * 64,
        "sourceQuery": query, "scope": query["scope"],
        "analysisPeriod": query["analysisPeriod"],
        "naturalMonths": query["months"],
        "monthlyCoverage": [month("2026-08", True),
            month("2026-09", False)],
        "missingMonths": ["2026-09"],
        "rowCount": 10, "pageCount": 1,
        "signedOwningBridgeVerified": True,
        "sealedSelectedSourceFullyReplayed": True,
        "agentReadReceiptRecorded": False,
        "financeDailyProrationAllowed": False,
        "financeSkuProfitAttributionAllowed": False,
        "sumOverlappingErpB2bAdsAllowed": False,
        "reportGenerationSupported": False,
        "upstreamSourceSignatureVerified": False}
    return {**value, "resultDigest": digest(value)}


def b2b():
    periods = {"current": ("2026-08-16", "2026-09-14"),
        "previous": ("2026-07-17", "2026-08-15"),
        "yearAgo": ("2025-08-16", "2025-09-14")}
    summary, windows = {}, {}
    for window, (first, last) in periods.items():
        selected = window == "current"
        key = "b2b-current" if selected else None
        coverage = ({"status": "missing_dates", "missingDates": [last]}
            if selected else None)
        summary[window] = {"sourceKey": key,
            "status": "selected_sealed_source" if selected else
                "catalogue_only_missing_source",
            "sourceRef": "d" * 64 if selected else None,
            "evidenceDigest": "e" * 64 if selected else None,
            "rowCount": 2 if selected else None,
            "pageCount": 1 if selected else None,
            "coverage": coverage}
        windows[window] = {"sourceKey": key,
            "sourceRef": summary[window]["sourceRef"],
            "evidenceDigest": summary[window]["evidenceDigest"],
            "coverage": coverage,
            "period": {"startDate": first, "endDate": last},
            "periodMetrics": {"paymentCents": {"status":
                "date_not_covered" if selected else
                "catalogue_only_missing_source"}}}
    value = {"schemaVersion":
        "business-b2b-report-owning-material-candidate-v1",
        "reportBinding": {"reportId": "b2b-report"},
        "platform": "京东", "shop": "平台店名",
        "sourceSummary": summary,
        "catalogueMissingWindows": ["previous", "yearAgo"],
        "material": {"windows": windows},
        "sealedReportDirectoryVerified": True,
        "sealedSelectedSourcesFullyReplayed": True,
        "databaseGlobalB2BAbsenceVerified": False,
        "b2bIncludedInErpSales": "unknown",
        "b2bIncludedInPlatformSkuSales": "unknown",
        "b2bShareOfErpSales": None,
        "b2bIncrementalSalesCents": None,
        "crossDomainAmountsAdded": False,
        "authorityVerified": False,
        "registeredAgentTool": False,
        "registeredRenderer": False}
    return {**value, "resultDigest": digest(value)}


def erp(*, shop="平台店名", start="2026-08-16", end="2026-09-14"):
    query = {"platform": "京东", "shop": shop, "window": "current",
        "startDate": start, "endDate": end}
    expected = {"sourceRef": "f" * 64, "evidenceDigest": "1" * 64,
        "rowCount": 2, "metrics": {"netSalesCents":
            {"value": 100, "presentRows": 2, "missingRows": 0}},
        "reconciled": True}
    source = {"key": "erp-current", "query": query,
        "info": {"expected": expected, "pageCount": 1,
            "metadata": {"coverage": {"status": "missing_dates",
                "missingDates": [end]}}}}
    value = {"schemaVersion":
        "business-erp-report-rollup-materials-candidate-v1",
        "reportBinding": {"reportId": "b2b-report"},
        "salesKey": source["key"], "salesQueryDigest": digest(query),
        "sourceProofs": {"sales": expected}, "sourceRowCount": 2,
        "authorityVerified": False, "registeredRenderer": False,
        "netshopAdFinanceCombined": False}
    return {**value, "manifestDigest": digest(value)}, source


class FinanceB2bSourceProofTests(TestCase):
    def test_missing_inputs_are_not_zero_or_database_absence(self):
        result = proof.build_candidate()
        self.assertEqual(result["rowCount"], 4)
        self.assertTrue(all(row["sourceStatus"] == "not_supplied"
            and row["rowCount"] is None and row["sourceRef"] is None
            for row in result["rows"]))
        self.assertFalse(result["sameReportAuthorityVerified"])
        self.assertIsNone(result["b2bIncrementalSalesCents"])
        table = proof.as_table(result)
        self.assertEqual(table.row_count, 4)
        self.assertEqual(len(table.columns), len(proof.COLUMNS))

    def test_natural_month_and_daily_b2b_proofs_stay_separate(self):
        result = proof.build_candidate(finance=finance(), b2b=b2b())
        self.assertEqual(result["rowCount"], 5)
        self.assertEqual([row["sourceStatus"] for row in result["rows"]],
            ["published_month", "missing_month", "selected_sealed_source",
             "catalogue_only_missing_source", "catalogue_only_missing_source"])
        self.assertEqual(result["rows"][0]["periodRole"], "2026-08")
        self.assertEqual(result["rows"][0]["endDate"], "2026-08-31")
        self.assertEqual(result["rows"][2]["periodRole"], "current")
        self.assertEqual(result["rows"][2]["missingDates"], ["2026-09-14"])
        self.assertIsNone(result["rows"][3]["rowCount"])
        self.assertEqual(result["rows"][0]["scopeOrShop"], "ERP别名店")
        self.assertEqual(result["rows"][2]["scopeOrShop"], "平台店名")
        self.assertFalse(result["sameShopIdentityVerified"])
        self.assertFalse(result["crossDomainAmountsAdded"])
        self.assertFalse(result["registeredRenderer"])
        table = proof.as_table(result)
        self.assertEqual(table.row_count, 5)

    def test_forged_digest_proration_overlap_or_missing_source_reject(self):
        for name in ("finance_digest", "proration", "b2b_overlap", "missing"):
            left, right = finance(), b2b()
            if name == "finance_digest": left["resultDigest"] = "0" * 64
            elif name == "proration":
                left["financeDailyProrationAllowed"] = True
                left["resultDigest"] = digest({key: value for key, value
                    in left.items() if key != "resultDigest"})
            elif name == "b2b_overlap":
                right["b2bIncludedInErpSales"] = "included"
                right["resultDigest"] = digest({key: value for key, value
                    in right.items() if key != "resultDigest"})
            else:
                right["sourceSummary"]["previous"]["status"] = (
                    "selected_sealed_source")
                right["resultDigest"] = digest({key: value for key, value
                    in right.items() if key != "resultDigest"})
            with self.subTest(name=name), self.assertRaises(AnalysisContractError):
                proof.build_candidate(finance=left, b2b=right)
        accepted = proof.build_candidate(finance=finance(), b2b=b2b())
        altered = deepcopy(accepted)
        altered["rows"][0]["sourceStatus"] = "all_good"
        with self.assertRaises(AnalysisContractError):
            proof.as_table(altered)

    def test_same_report_erp_and_b2b_are_parallel_with_finance_months(self):
        manifest, source = erp()
        result = proof.build_candidate(finance=finance(), b2b=b2b(),
            erp=manifest, erp_source=source)
        self.assertEqual(result["rowCount"], 6)
        row = result["rows"][-1]
        self.assertEqual((row["domain"], row["periodRole"], row["sourceKey"]),
            ("erp", "current", "erp-current"))
        self.assertEqual(row["metricStatus"], {"netSalesCents": "present"})
        self.assertTrue(result["erpB2bSameReportBindingVerified"])
        self.assertTrue(result["erpB2bSameShopQueryVerified"])
        self.assertFalse(result["sameReportAuthorityVerified"])
        self.assertFalse(result["sameShopIdentityVerified"])
        self.assertFalse(result["crossDomainAmountsAdded"])
        self.assertIsNone(result["b2bIncrementalSalesCents"])
        self.assertEqual(proof.as_table(result).row_count, 6)

    def test_erp_requires_exact_b2b_report_shop_window_and_source_proof(self):
        for name in ("missing_b2b", "report", "shop", "window", "period",
                "source_proof", "manifest_digest", "missing_source"):
            manifest, source = erp()
            right = b2b()
            if name == "report":
                manifest["reportBinding"]["reportId"] = "another-report"
            elif name == "shop":
                source["query"]["shop"] = "另一店"
                manifest["salesQueryDigest"] = digest(source["query"])
            elif name == "window":
                source["query"]["window"] = "previous"
                manifest["salesQueryDigest"] = digest(source["query"])
            elif name == "period":
                source["query"]["endDate"] = "2026-09-13"
                manifest["salesQueryDigest"] = digest(source["query"])
            elif name == "source_proof":
                source["info"]["expected"]["rowCount"] = 3
            elif name == "manifest_digest":
                manifest["manifestDigest"] = "0" * 64
            elif name == "missing_source":
                source = None
            if name not in {"manifest_digest", "source_proof"}:
                manifest["manifestDigest"] = digest({key: item for key, item
                    in manifest.items() if key != "manifestDigest"})
            with self.subTest(name=name), self.assertRaises(AnalysisContractError):
                proof.build_candidate(b2b=None if name == "missing_b2b"
                    else right, erp=manifest, erp_source=source)
