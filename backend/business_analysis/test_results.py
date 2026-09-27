from copy import deepcopy
from unittest import TestCase
from .contracts import AnalysisContractError, PageReconciler, SCHEMA_VERSION, MAX_SAFE_INTEGER, compare, comparison_periods, coverage, digest
from .results import build_table


def fixture(window="current", rows=None, missing=False):
    periods = comparison_periods("2026-09-01", "2026-09-02")
    rows = rows if rows is not None else [("K1", 100, 2, 10), ("K2", 300, 3, 90)]
    items = [{"rowId": str(i), "platform": "京东", "shopName": "合成店", "category": "饮水设备", "skuId": str(i), "spuId": "SPU1",
        "dimensions": {"keyword": word, "searchTerm": "搜索"+word},
        "metrics": {"spendCents": spend, "clicks": clicks, "impressions": impressions}}
        for i, (word, spend, clicks, impressions) in enumerate(rows, 1)]
    period = periods[window]
    page = {"schemaVersion": SCHEMA_VERSION, "sourceRef": "source-"+window,
        "source": "jd_promotion", "sourceDataset": "ad",
        "filters": {"platform": "京东", "shop": "合成店", "dataset": "promotion", "periods": periods, "window": window},
        "coverage": coverage(period, [period["startDate"]] if missing else [period["startDate"], period["endDate"]]),
        "items": items, "control": {"rowCount": len(items), "typedTotals": {key: sum(r["metrics"][key] or 0 for r in items) for key in ["spendCents", "clicks", "impressions"]}},
        "pageEvidence": {"rowCount": len(items), "sha256": digest(items)}, "pagination": {"hasMore": False, "nextCursor": None}}
    verifier = PageReconciler()
    verifier.consume(page)
    return page, verifier.result()


class ResultTableTests(TestCase):
    def test_ratios_are_weighted_and_ids_repeat(self):
        page, proof = fixture()
        table = build_table([page], "shop", proof)
        row = table["rows"][0]
        self.assertEqual(row["metrics"]["spendCents"]["value"], 400)
        self.assertEqual(row["ratios"]["ctr"], .05)
        self.assertEqual(table, build_table([deepcopy(page)], "shop", proof))
        self.assertEqual(table["total"], 1)

    def test_both_comparison_windows_and_percentage_points(self):
        current, proof = fixture()
        for window in ("previous", "yearAgo"):
            baseline, before = fixture(window, [("K1", 100, 1, 100)])
            table = build_table([current], "shop", proof, baseline_pages=[baseline], baseline_expected=before)
            values = table["rows"][0]["comparisons"]
            self.assertEqual(values["spendCents"]["changeRate"], 3)
            self.assertEqual(values["ctr"]["percentagePoints"], 4)
            self.assertIsNone(values["cpcCents"]["percentagePoints"])

    def test_missing_dates_fields_and_absent_entities_are_not_zero(self):
        current, proof = fixture(rows=[("K1", 100, None, 10)], missing=True)
        baseline, before = fixture("previous", [("K2", 100, 1, 100)])
        table = build_table([current], "keyword", proof, baseline_pages=[baseline], baseline_expected=before)
        self.assertEqual(table["total"], 2)
        self.assertTrue(all(row["comparisons"]["spendCents"]["status"] == "unavailable" for row in table["rows"]))
        self.assertIsNone(table["rows"][1]["metrics"]["spendCents"])
        self.assertIsNone(table["rows"][0]["ratios"]["ctr"])

    def test_keyword_search_term_and_native_spu_remain_distinct(self):
        page, proof = fixture()
        self.assertEqual(build_table([page], "spu", proof)["total"], 1)
        self.assertEqual(build_table([page], "sku", proof)["total"], 2)
        self.assertNotEqual(build_table([page], "keyword", proof)["rows"][0]["entity"],
                            build_table([page], "searchTerm", proof)["rows"][0]["entity"])
        self.assertTrue(build_table([page], "daily", proof)["rows"][0]["dimensionMissing"])

    def test_incomplete_or_tampered_evidence_fails(self):
        page, proof = fixture()
        for changed in ({**proof, "rowCount": 99}, {**proof, "evidenceDigest": "other"}):
            with self.assertRaises(AnalysisContractError):
                build_table([page], "shop", changed)
        page["items"][0]["metrics"]["spendCents"] = 999
        with self.assertRaises(AnalysisContractError):
            build_table([page], "shop", proof)

    def test_cross_scope_period_dataset_and_daily_comparison_rejected(self):
        current, proof = fixture()
        baseline, before = fixture("previous")
        for field, value in (("shop", "其他店"), ("dataset", "sku"), ("window", "current")):
            bad = deepcopy(baseline)
            bad["filters"][field] = value
            with self.assertRaises(AnalysisContractError):
                build_table([current], "shop", proof, baseline_pages=[bad], baseline_expected=before)
        with self.assertRaises(AnalysisContractError):
            build_table([current], "daily", proof, baseline_pages=[baseline], baseline_expected=before)

    def test_shop_and_keyword_comparison_reject_metric_semantics_drift(self):
        current, proof = fixture()
        baseline, before = fixture("previous")
        current["metricSemantics"] = {"visitors": "商品日访客，不是店铺去重访客"}
        baseline["metricSemantics"] = {"visitors": "店铺去重访客"}
        # PageReconciler hashes rows, not this source-level field. The report
        # comparison must still reject a changed metric contract.
        for dimension in ("shop", "keyword"):
            with self.assertRaises(AnalysisContractError):
                build_table([current], dimension, proof, baseline_pages=[baseline], baseline_expected=before)

    def test_keyword_continuation_rejects_metric_semantics_drift(self):
        first, _ = fixture()
        second = deepcopy(first)
        first["metricSemantics"] = {"spendCents": "平台广告花费分"}
        second["metricSemantics"] = {"spendCents": "ERP销售分"}
        first["items"], second["items"] = first["items"][:1], second["items"][1:]
        first["pageEvidence"] = {"rowCount": 1, "sha256": digest(first["items"])}
        second["pageEvidence"] = {"rowCount": 1, "sha256": digest(second["items"])}
        first["pagination"] = {"hasMore": True, "nextCursor": "page-2"}
        second["pagination"] = {"hasMore": False, "nextCursor": None}
        second["control"] = None
        verifier = PageReconciler()
        verifier.consume(first)
        verifier.consume(second, request_cursor="page-2")
        with self.assertRaises(AnalysisContractError):
            build_table([first, second], "keyword", verifier.result())

    def test_zero_negative_baseline_and_empty_sources(self):
        current, proof = fixture()
        for amount, status in ((0, "zero_baseline"), (-100, "negative_baseline")):
            baseline, before = fixture("previous", [("K1", amount, 1, 100)])
            table = build_table([current], "shop", proof, baseline_pages=[baseline], baseline_expected=before)
            self.assertEqual(table["rows"][0]["comparisons"]["spendCents"]["status"], status)
            self.assertIsNone(table["rows"][0]["comparisons"]["spendCents"]["changeRate"])
        empty, none = fixture(rows=[])
        self.assertEqual(build_table([empty], "shop", none)["rows"], [])
        with self.assertRaises(AnalysisContractError):
            compare(MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER)
