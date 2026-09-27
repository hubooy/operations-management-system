from django.test import SimpleTestCase
from .aggregation import VerifiedAnalysis, metric_comparison
from .contracts import (
    AnalysisContractError, PageReconciler, SCHEMA_VERSION, compare, comparison_periods,
    coverage, digest, identity_candidates, monetary_cents,
)


class AnalysisContractTests(SimpleTestCase):
    def test_page_budget_counts_utf8_and_never_slices_a_row(self):
        from .contracts import bounded_page_items, canonical
        items = [{"rowId": str(i), "name": "汉" * 10} for i in range(3)]
        budget = len(canonical(items[:2]).encode())
        selected, more = bounded_page_items(items, False, budget)
        self.assertEqual(selected, items[:2])
        self.assertTrue(more)
        self.assertEqual(bounded_page_items(items, False, len(canonical(items).encode())), (items, False))
        self.assertEqual(bounded_page_items([], False, 2), ([], False))
        with self.assertRaises(AnalysisContractError):
            bounded_page_items(items, False, 5)

    def test_dates_compare_equal_length_and_clamp_leap_day(self):
        result = comparison_periods("2024-02-29", "2024-03-01")
        self.assertEqual(result["previous"]["startDate"], "2024-02-27")
        self.assertEqual(result["current"]["endExclusive"], "2024-03-02")
        self.assertEqual(result["yearAgo"]["startDate"], "2023-02-28")
        self.assertEqual(result["yearAgo"]["endDate"], "2023-03-01")
        for first, last in [("2026-1-01", "2026-01-02"), ("2026-01-02", "2026-01-01"), ("2026-01-01", "2026-12-31")]:
            with self.assertRaises(AnalysisContractError):
                comparison_periods(first, last)

    def test_comparison_missing_zero_negative_and_percentage_points(self):
        self.assertEqual(compare(12, 10)["changeRate"], .2)
        self.assertEqual(compare(12, 0)["status"], "zero_baseline")
        self.assertIsNone(compare(12, -10)["changeRate"])
        self.assertIsNone(compare(None, 10)["difference"])
        self.assertEqual(compare(.05, .04, is_rate=True)["percentagePoints"], 1)
        self.assertEqual(compare(12, 10, comparable=False)["status"], "unavailable")

    def test_missing_date_is_not_zero_business(self):
        window = comparison_periods("2026-09-01", "2026-09-03")["current"]
        result = coverage(window, ["2026-09-01", "2026-09-03"])
        self.assertEqual(result["missingDates"], ["2026-09-02"])
        self.assertEqual(coverage(window, [])["status"], "no_records")
        with self.assertRaises(AnalysisContractError):
            coverage(window, ["2026-09-04"])

    def test_money_is_decimal_and_missing_remains_missing(self):
        self.assertEqual(monetary_cents("1,234.56"), 123456)
        self.assertEqual(monetary_cents("-12.34"), -1234)
        self.assertEqual(monetary_cents("0"), 0)
        for value in [None, "--", "NaN", "Infinity", "garbage", True, {}]:
            self.assertIsNone(monetary_cents(value))

    def test_mapping_never_duplicates_erp_facts_across_skus_or_shops(self):
        rows = [dict(platform="京东", shopName="A", merchantCode="X", skuId="1", spuId="p"),
                dict(platform="京东", shopName="B", merchantCode="X", skuId="2", spuId="p")]
        self.assertEqual(identity_candidates(rows, "京东", "A", "X")["skuId"], "1")
        rows.append({**rows[0], "skuId": "3"})
        result = identity_candidates(rows, "京东", "A", "X")
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["skuId"])
        self.assertEqual(identity_candidates(rows, "天猫", "A", "X")["status"], "unmatched")
        projected = {"platform": "京东", "shopName": "A", "skuId": "9", "spuId": "p", "dimensions": {"merchantCode": "X"}}
        self.assertEqual(identity_candidates([projected], "京东", "A", "X")["skuId"], "9")

    def page(self, *, row_id=1, more=False, control=True):
        items = [{"rowId": str(row_id), "metrics": {"spendCents": 25, "missing": None}}]
        return {"schemaVersion": SCHEMA_VERSION, "sourceRef": "fixed", "items": items,
                "control": {"rowCount": 2, "typedTotals": {"spendCents": 50, "missing": 0}} if control else None,
                "pageEvidence": {"sha256": digest(items), "rowCount": len(items)},
                "pagination": {"hasMore": more, "nextCursor": "next" if more else None}}

    def test_complete_incremental_reconciliation_and_missingness(self):
        verifier = PageReconciler()
        verifier.consume(self.page(more=True))
        with self.assertRaises(AnalysisContractError):
            verifier.result()
        verifier.consume(self.page(row_id=3, control=False), request_cursor="next")
        result = verifier.result()
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["metrics"]["spendCents"]["value"], 50)
        self.assertIsNone(result["metrics"]["missing"]["value"])
        self.assertEqual(result["metrics"]["missing"]["missingRows"], 2)
        with self.assertRaises(AnalysisContractError):
            verifier.consume(self.page())

    def test_replay_wrong_source_digest_and_false_completion_rejected(self):
        for mutation in ("replay", "source", "digest", "control", "early_end"):
            with self.subTest(mutation=mutation):
                verifier = PageReconciler()
                verifier.consume(self.page(more=True))
                page = self.page(row_id=2, control=False)
                if mutation == "replay":
                    page = self.page(row_id=1, control=False)
                elif mutation == "source":
                    page["sourceRef"] = "changed"
                elif mutation == "digest":
                    page["items"][0]["metrics"]["spendCents"] = 99
                elif mutation == "control":
                    page["control"] = {}
                elif mutation == "early_end":
                    page["items"] = []
                    page["pageEvidence"]["sha256"] = digest([])
                with self.assertRaises(AnalysisContractError):
                    verifier.consume(page, request_cursor="next")
                    verifier.result()


class VerifiedAnalysisTests(SimpleTestCase):
    def page(self, rows, *, total=None, cursor=None):
        return {"schemaVersion": SCHEMA_VERSION, "sourceRef": "fixed-source", "items": rows,
                "control": total, "pageEvidence": {"sha256": digest(rows), "rowCount": len(rows)},
                "pagination": {"hasMore": cursor is not None, "nextCursor": cursor}}

    def row(self, identity, *, shop="A", clicks=1, impressions=10, missing=None):
        return {"rowId": str(identity), "platform": "京东", "shopName": shop,
                "dimensions": {"keyword": "饮水机"},
                "metrics": {"clicks": clicks, "impressions": impressions, "missing": missing}}

    def test_weighted_rates_and_shop_isolation_and_all_missing_metric(self):
        rows = [self.row(1), self.row(2, clicks=90, impressions=100), self.row(3, shop="B")]
        flow = VerifiedAnalysis(["keyword"], ["clicks", "impressions", "missing"])
        flow.consume(self.page(rows, total={"rowCount": 3, "typedTotals": {"clicks": 92, "impressions": 120}}))
        output = flow.result()
        self.assertEqual(len(output["items"]), 2)
        a = output["items"][0]
        self.assertAlmostEqual(a["ratios"]["ctr"], 91/110)
        self.assertIsNone(a["metrics"]["missing"]["value"])
        self.assertEqual(a["metrics"]["missing"]["missingRows"], 2)

    def test_failed_page_can_be_corrected_without_duplicate_accumulation(self):
        flow = VerifiedAnalysis(["keyword"], ["clicks", "impressions"])
        flow.consume(self.page([self.row(1)], total={"rowCount": 2, "typedTotals": {"clicks": 2, "impressions": 20}}, cursor="next"))
        with self.assertRaises(AnalysisContractError):
            flow.consume(self.page([self.row(2, clicks=99)]), request_cursor="next")
        self.assertEqual(flow.verifier.rows, 1)
        self.assertEqual(next(iter(flow.accumulator.groups.values()))["rowCount"], 1)
        flow.consume(self.page([self.row(2)]), request_cursor="next")
        self.assertEqual(flow.result()["rowCount"], 2)

    def test_group_cap_fails_closed_and_partial_ratios_are_unavailable(self):
        flow = VerifiedAnalysis(["keyword"], ["clicks", "impressions"], max_groups=1)
        page = self.page([self.row(1), self.row(2, shop="B")], total={"rowCount": 2, "typedTotals": {"clicks": 2}})
        with self.assertRaises(AnalysisContractError):
            flow.consume(page)
        self.assertEqual(flow.verifier.rows, 0)
        self.assertEqual(flow.accumulator.groups, {})
        flow.consume(self.page([self.row(1), self.row(2, impressions=None)], total={"rowCount": 2, "typedTotals": {"clicks": 2}}))
        self.assertIsNone(flow.result()["items"][0]["ratios"]["ctr"])
        self.assertEqual(metric_comparison({"value": 100, "missingRows": 1}, {"value": 50, "missingRows": 0}, dates_complete=True)["status"], "unavailable")

    def test_large_input_stream_is_not_truncated_or_retained_as_raw_rows(self):
        flow = VerifiedAnalysis(["keyword"], ["clicks", "impressions"])
        count, page_size = 100_000, 100
        for offset in range(0, count, page_size):
            rows = [self.row(index+1) for index in range(offset, offset+page_size)]
            more = offset + page_size < count
            flow.consume(self.page(rows, total={"rowCount": count, "typedTotals": {"clicks": count, "impressions": count*10}} if offset == 0 else None,
                                   cursor=str(offset+page_size) if more else None), request_cursor=str(offset) if offset else None)
        result = flow.result()
        self.assertEqual(result["rowCount"], count)
        self.assertFalse(result["truncated"])
        self.assertEqual(len(flow.accumulator.groups), 1)
        self.assertEqual(result["items"][0]["metrics"]["clicks"]["value"], count)
