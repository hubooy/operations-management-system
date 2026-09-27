import copy
import json
import unittest

from business_analysis.contracts import AnalysisContractError, PageReconciler, comparison_periods, coverage, digest
from business_analysis.market_own_mapping import build_candidates


def _page(source, rows, *, coverage, filters):
    typed = {}
    for row in rows:
        for key, value in row["metrics"].items():
            typed[key] = typed.get(key, 0) + (value or 0)
    return {"schemaVersion": "business-analysis-v1", "source": source,
        "sourceDataset": "market_daily_top" if source == "market_daily_top" else "product_master",
        "sourceRef": digest([source, filters, rows]), "sourceRevision": "1:fixture",
        "filters": filters, "coverage": coverage, "items": rows,
        "control": {"rowCount": len(rows), "typedTotals": typed},
        "pageEvidence": {"rowCount": len(rows), "sha256": digest(rows)},
        "pagination": {"hasMore": False, "nextCursor": None, "limit": 100}}


def _proof(page):
    verifier = PageReconciler()
    verifier.consume(page)
    return verifier.result()


class MarketOwnMappingTests(unittest.TestCase):
    def setUp(self):
        self.market_filters = {"platform": "京东", "shop": "", "window": "current",
            "rankingDimension": "SKU", "periods": comparison_periods("2026-09-01", "2026-09-02")}
        self.master_filters = {"platform": "京东", "shop": "志高店", "dataset": "master", "window": "current"}
        self.market_rows = [
            {"rowId": "2", "platform": "京东", "shopName": "", "date": "2026-09-02",
                "skuId": "SKU-B", "spuId": None, "productName": "同名饮水机",
                "sample": {"rank": 2}, "metrics": {"sampleGmvLowerCents": 200}},
            {"rowId": "1", "platform": "京东", "shopName": "", "date": "2026-09-01",
                "skuId": "SKU-A", "spuId": None, "sample": {"rank": 1}, "metrics": {"sampleGmvLowerCents": 100}},
        ]
        # Owning pages order by rowId, even if result output uses date/key.
        self.market_rows.sort(key=lambda row: int(row["rowId"]))
        self.master_rows = [{"rowId": "10", "platform": "京东", "shopName": "志高店",
            "batchId": "master-1", "skuId": "SKU-A", "spuId": "SPU-A",
            "productName": "同名饮水机", "metrics": {}}]

    def _inputs(self, market=None, master=None, *, master_coverage=None):
        market_rows = self.market_rows if market is None else market
        window = self.market_filters["periods"][self.market_filters["window"]]
        m = _page("market_daily_top", market_rows,
            coverage=coverage(window, [row["date"] for row in market_rows]), filters=self.market_filters)
        n = _page("jd_product_master", self.master_rows if master is None else master,
            coverage=master_coverage or {"status": "current_master", "snapshotDate": "2026-09-03",
                "batchId": "master-1", "historicalMapping": False}, filters=self.master_filters)
        args = {"platform": "京东", "shop": "志高店", "start_date": "2026-09-01", "end_date": "2026-09-02",
            "market_expected": _proof(m), "master_expected": _proof(n)}
        return m, n, args

    def test_exact_current_sku_candidate_preserves_unresolved_and_no_shop_attribution(self):
        m, n, args = self._inputs()
        original = copy.deepcopy((m, n))
        result = build_candidates([m], [n], **args)
        page = result.page()
        self.assertEqual(page["counts"], {"candidate": 1, "unresolved": 1})
        self.assertEqual([r["status"] for r in page["rows"]], ["candidate", "unresolved"])
        self.assertEqual(page["rows"][1]["reason"], "unmatched_current_master")
        self.assertTrue(all(r["manualConfirmationRequired"] and not r["historicalOwnershipVerified"]
            and not r["marketSalesAttributedToShop"] for r in page["rows"]))
        self.assertEqual(page["rows"][0]["currentMasterSkuId"], "SKU-A")
        self.assertEqual((m, n), original)
        page["rows"][0]["status"] = "tampered"
        self.assertEqual(result.page()["rows"][0]["status"], "candidate")

    def test_duplicate_master_sku_and_unranked_market_remain_unresolved(self):
        duplicate = {**self.master_rows[0], "rowId": "11", "spuId": "SPU-B"}
        market = copy.deepcopy(self.market_rows)
        market[1]["sample"]["rank"] = None
        m, n, args = self._inputs(market=market, master=self.master_rows + [duplicate])
        rows = build_candidates([m], [n], **args).page()["rows"]
        self.assertEqual(rows[0]["reason"], "ambiguous_current_master")
        self.assertEqual(rows[1]["reason"], "unranked")

    def test_native_spu_never_picks_arbitrary_variant_sku(self):
        self.market_filters["rankingDimension"] = "SPU"
        market = [{**self.market_rows[0], "skuId": None, "spuId": "SPU-A"}]
        variants = [self.master_rows[0], {**self.master_rows[0], "rowId": "11", "skuId": "SKU-Z"}]
        m, n, args = self._inputs(market=market, master=variants)
        row = build_candidates([m], [n], **args).page()["rows"][0]
        self.assertEqual(row["status"], "candidate")
        self.assertIsNone(row["currentMasterSkuId"])
        self.assertEqual(row["currentMasterSpuId"], "SPU-A")

    def test_missing_current_master_and_coverage_are_explicit(self):
        m, n, args = self._inputs(master=[], master_coverage={"status": "no_records", "snapshotDate": None,
            "batchId": None, "historicalMapping": False})
        result = build_candidates([m], [n], **args).page()
        self.assertTrue(all(r["reason"] == "missing_current_master" for r in result["rows"]))
        self.assertEqual(result["coverage"]["currentMaster"]["status"], "no_records")

    def test_explicit_product_absent_from_complete_top_sample_is_unresolved(self):
        m, n, args = self._inputs()
        result = build_candidates([m], [n], **args,
            requested_product_ids=["SKU-Z", "SKU-A"])
        rows = result.page()["rows"]
        self.assertEqual(result.summary()["rowCount"], 3)
        self.assertEqual(rows[-1]["marketProductId"], "SKU-Z")
        self.assertEqual(rows[-1]["reason"], "not_ranked_in_sample")
        self.assertIsNone(rows[-1]["marketDate"])
        with self.assertRaises(AnalysisContractError):
            build_candidates([m], [n], **args, requested_product_ids=["SKU-Z", "SKU-Z"])

    def test_missing_market_dates_prevent_absence_from_being_called_unranked(self):
        m, n, args = self._inputs(market=[self.market_rows[0]])
        row = build_candidates([m], [n], **args, requested_product_ids=["SKU-Z"]).page()["rows"][-1]
        self.assertEqual(row["reason"], "market_coverage_incomplete")
        self.assertEqual(m["coverage"]["status"], "missing_dates")

    def test_bounded_output_pages_continue_without_dropping_requested_ids(self):
        m, n, args = self._inputs()
        result = build_candidates([m], [n], **args,
            requested_product_ids=[f"CHECK-{index:03d}" for index in range(300)])
        offset, seen = 0, []
        while True:
            page = result.page(offset, 100)
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()), 38000)
            seen.extend(page["rows"])
            offset = page["pagination"]["nextOffset"]
            if offset is None:
                break
        self.assertEqual(len(seen), 302)
        self.assertEqual(sum(row["reason"] == "not_ranked_in_sample" for row in seen), 300)

    def test_fails_closed_on_cross_platform_cross_shop_dates_and_incomplete_seal(self):
        m, n, args = self._inputs()
        with self.assertRaises(AnalysisContractError):
            build_candidates([m], [n], **{**args, "platform": "天猫"})
        with self.assertRaises(AnalysisContractError):
            build_candidates([m], [n], **{**args, "shop": "其他店"})
        with self.assertRaises(AnalysisContractError):
            build_candidates([m], [n], **{**args, "end_date": "2026-09-03"})
        with self.assertRaises(AnalysisContractError):
            build_candidates([m], [n], **{**args, "market_expected": {**args["market_expected"], "rowCount": 999}})
        with self.assertRaises(AnalysisContractError):
            build_candidates([], [n], **args)


if __name__ == "__main__":
    unittest.main()
