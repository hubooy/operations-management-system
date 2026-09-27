from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from . import market_dynamics as service
from .contracts import AnalysisContractError, PageReconciler, SCHEMA_VERSION, canonical, comparison_periods, coverage, digest


def fixture(items=None, *, window="current", dimension="SKU", days=("2026-09-03", "2026-09-03")):
    query = {"platform": "京东", "category": "商用设备", "scope": "POP", "rankingDimension": dimension,
             "priceBandFilter": "全部", "startDate": days[0], "endDate": days[1], "window": window}
    periods = comparison_periods(*days); day = periods[window]["startDate"]
    rows = []
    for index, changes in enumerate(items if items is not None else [{}], 1):
        row = {"rowId": str(index), "sourceRowHash": digest([index, window]), "batchId": "batch", "platform": "京东", "shopName": "",
               "date": day, "category": "商用设备", "skuId": str(index) if dimension == "SKU" else None,
               "spuId": str(index) if dimension == "SPU" else None, "productName": "Synthetic",
               "dimensions": {"brand": None, "marketScope": "POP", "operationMode": "POP"},
               "sample": {"rank": index, "priceLowerCents": 100, "priceUpperCents": 100, "priceEstimated": False},
               "metrics": {m: 10 if "Lower" in m else 20 for m in service.METRICS}}
        row.update(deepcopy(changes)); rows.append(row)
    page = {"schemaVersion": SCHEMA_VERSION, "source": "market_daily_top", "sourceDataset": "market_daily_top", "sourceRef": digest(query),
        "sourceRevision": "1:aaaaaaaaaaaa", "filters": {**{k:v for k,v in query.items() if k not in {"startDate", "endDate"}}, "periods": periods, "shop": "", "limit": 100},
        "monetaryUnit": "CNY_CENT", "items": rows, "control": {"rowCount": len(rows), "typedTotals": {m: sum(r["metrics"][m] or 0 for r in rows) for m in service.METRICS}},
        "coverage": coverage(periods[window], {r["date"] for r in rows}), "availableDates": {"firstDate": day, "lastDate": day},
        "excludedOverlappingPeriodRows": 0, "metricSemantics": {"population": "TOP sample"},
        "pageEvidence": {"rowCount": len(rows), "sha256": digest(rows)}, "pagination": {"hasMore": False, "nextCursor": None, "limit": 100}}
    reconcile = PageReconciler(); reconcile.consume(page)
    return {"key": "market-"+window, "domain": "market", "query": query}, [page], reconcile.result()


BANDS = [{"key": "low", "lowerCents": 0, "upperExclusiveCents": 200}, {"key": "high", "lowerCents": 200, "upperExclusiveCents": None}]


class MarketDynamicsTests(TestCase):
    def test_exact_band_bounds_nulls_and_sku_members_not_midpoints(self):
        base = fixture()[1][0]["items"][0]
        nulls = dict(base["metrics"], sampleGmvLowerCents=None)
        source = fixture([{}, {"sample": {**base["sample"], "priceLowerCents": 190, "priceUpperCents": 210}},
            {"sample": {**base["sample"], "priceLowerCents": None}, "metrics": nulls},
            {"sample": {**base["sample"], "priceEstimated": True}}])
        result = service.price_band(*source, BANDS)
        groups = {r["bandKey"]: r for r in result["rows"]}
        self.assertEqual(set(groups), {"low", "unallocated_interval", "unallocated_missing", "unallocated_estimated"})
        self.assertIsNone(groups["unallocated_missing"]["metrics"]["sampleGmvLowerCents"]["value"])
        self.assertEqual(groups["unallocated_missing"]["metrics"]["sampleGmvUpperCents"]["value"], 20)
        self.assertEqual(groups["low"]["members"][0]["skuId"], "1")
        self.assertFalse(result["authorityVerified"]); self.assertFalse(result["shareEstimated"])

    def test_predeclared_band_overlap_boundary_and_unknown_bounds(self):
        base = fixture()[1][0]["items"][0]
        result = service.price_band(*fixture([{"sample": {**base["sample"], "priceLowerCents": 200, "priceUpperCents": 200}}]), BANDS)
        self.assertEqual(result["rows"][0]["bandKey"], "high")
        for bands in ([BANDS[1], BANDS[0]], [BANDS[0], {**BANDS[1], "lowerCents": 199}],
                      [{**BANDS[0], "upperExclusiveCents": 0}], [{**BANDS[0], "lowerCents": True}],
                      [{**BANDS[0], "key": "unallocated_fake"}]):
            with self.assertRaises(AnalysisContractError): service.price_band(*fixture(), bands)

    def test_rank_presence_never_zero_sales_and_rank_improvement(self):
        current = fixture([{"skuId": "A"}, {"skuId": "B"}])
        previous = fixture([{"skuId": "C"}, {"skuId": "A"}], window="previous")
        result = service.rank_entry_exit(*current, *previous)
        rows = {r["skuId"]: r for r in result["rows"]}
        self.assertEqual(rows["A"]["rankImprovement"], 1)
        self.assertEqual(rows["B"]["status"], "entered_observed_top_sample")
        self.assertEqual(rows["B"]["baseline"]["status"], "not_observed_in_top_sample")
        self.assertIsNone(rows["B"]["baseline"]["metrics"])
        self.assertEqual(rows["C"]["status"], "left_observed_top_sample")
        self.assertIsNone(rows["C"]["current"]["rank"])

    def test_missing_date_and_spu_identity_and_explicit_year_ago(self):
        result = service.rank_entry_exit(*fixture(dimension="SPU"), *fixture([], dimension="SPU", window="yearAgo"))
        self.assertIsNone(result["rows"][0]["skuId"])
        self.assertEqual(result["rows"][0]["spuId"], "1")
        self.assertEqual(result["rows"][0]["status"], "insufficient_date_coverage")

    def test_duplicate_product_day_or_period_records_reject(self):
        for items in ([{"skuId": "A"}, {"skuId": "A"}], [{"period_start": "2026-09-01"}], [{"date": "2026-09-02"}]):
            with self.assertRaises(AnalysisContractError): service.price_band(*fixture(items), BANDS)
        multi = ("2026-09-01", "2026-09-03")
        with self.assertRaises(AnalysisContractError): service.rank_entry_exit(*fixture(days=multi), *fixture(window="previous", days=multi))

    def test_wrong_range_identity_control_and_coverage_fail_closed(self):
        for change in (lambda p: p["filters"].update(scope="all"), lambda p: p["control"]["typedTotals"].update(sampleGmvUpperCents=999),
                       lambda p: p["coverage"].update(status="missing_dates"), lambda p: p["pageEvidence"].update(sha256="f"*64)):
            args = fixture(); change(args[1][0])
            with self.assertRaises(AnalysisContractError): service.price_band(*args, BANDS)
        previous = fixture(window="previous"); previous[0]["query"]["scope"] = "all"
        with self.assertRaises(AnalysisContractError): service.rank_entry_exit(*fixture(), *previous)

    def test_null_rank_invalid_interval_and_unsafe_values(self):
        base = fixture()[1][0]["items"][0]
        unknown = fixture([{"sample": {**base["sample"], "rank": None}}])
        self.assertIsNone(service.rank_entry_exit(*unknown, *fixture(window="previous"))["rows"][0]["rankImprovement"])
        for sample in ({"rank": True}, {"rank": 0}, {"priceLowerCents": 300}, {"priceUpperCents": 2**53}, {"priceEstimated": 1}):
            with self.assertRaises(AnalysisContractError): service.price_band(*fixture([{"sample": {**base["sample"], **sample}}]), BANDS)

    def test_complete_multi_page_reconciled_and_late_mutation_rejects(self):
        source, pages, _ = fixture([{}, {}]); first = pages[0]; second = deepcopy(first)
        first["items"] = first["items"][:1]; second["items"] = second["items"][1:]
        first["pagination"].update(hasMore=True, nextCursor="cursor")
        for page in (first, second): page["pageEvidence"] = {"rowCount": 1, "sha256": digest(page["items"])}
        second.update(control=None, coverage=None, excludedOverlappingPeriodRows=None, availableDates=None)
        reconcile = PageReconciler(); reconcile.consume(first); reconcile.consume(second, request_cursor="cursor")
        result = service.price_band(source, iter([first, second]), reconcile.result(), BANDS)
        self.assertEqual(len(result["rows"][0]["members"]), 2)
        second["sourceRevision"] = "2:bbbbbbbbbbbb"
        with self.assertRaises(AnalysisContractError): service.price_band(source, iter([first, second]), reconcile.result(), BANDS)

    def test_caps_digests_and_input_output_immutability(self):
        args = fixture(); before = deepcopy(args); bands = deepcopy(BANDS)
        result = service.price_band(*args, bands)
        self.assertEqual(result["tableDigest"], digest({k:v for k,v in result.items() if k != "tableDigest"}))
        result["bands"].clear(); result["rows"].clear()
        self.assertEqual(args, before); self.assertEqual(bands, BANDS)
        self.assertEqual(service.price_band(*args, bands), service.price_band(*args, bands))
        for setting, maximum in (("MAX_ROWS", 0), ("MAX_PAGES", 0), ("MAX_BYTES", 100)):
            with patch.object(service, setting, maximum), self.assertRaises(AnalysisContractError): service.price_band(*args, bands)

    def test_two_period_combined_capacity_and_separate_output_cap(self):
        with patch.object(service, "MAX_PAGES", 1), self.assertRaises(AnalysisContractError):
            service.rank_entry_exit(*fixture(), *fixture(window="previous"))
        args = fixture(); result = service.price_band(*args, BANDS)
        limit = len(canonical(result).encode("utf-8")) - 1
        self.assertLess(len(canonical(args[1][0]).encode("utf-8")), limit)
        with patch.object(service, "MAX_BYTES", limit), self.assertRaises(AnalysisContractError):
            service.price_band(*args, BANDS)

    def test_multiday_price_totals_retain_partial_bounds_and_excluded_period_count(self):
        args = fixture([{}, {"date": "2026-09-02"}], days=("2026-09-01", "2026-09-02"))
        args[1][0]["excludedOverlappingPeriodRows"] = 5
        result = service.price_band(*args, BANDS)
        self.assertEqual(result["sourceMetadata"][0]["excludedOverlappingPeriodRows"], 5)
        self.assertEqual(result["sourceMetadata"][0]["coverage"]["status"], "dates_present")
        self.assertEqual(result["rows"][0]["metrics"]["sampleGmvLowerCents"]["value"], 20)
        self.assertEqual(result["rows"][0]["metrics"]["sampleGmvUpperCents"]["value"], 40)
        self.assertEqual([m["date"] for m in result["rows"][0]["members"]], ["2026-09-01", "2026-09-02"])
