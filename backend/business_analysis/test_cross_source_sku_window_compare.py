"""SKU/day source conservation and non-authorizing three-window comparisons."""
from copy import deepcopy
from unittest import TestCase

from . import cross_source_daily_columns as daily, cross_source_kpi_plan as planning
from . import cross_source_sku_window_compare as service
from .contracts import AnalysisContractError, comparison_periods, digest
from .test_cross_source_daily_columns import CONTEXT, COMMON, fixture, native_page, product
from .test_cross_source_window_compare import materials, resign


def full_sku_materials(*, partial_previous=False):
    plan, sources, infos, keys, manifest, erp, native = fixture()
    current = next(source for source in sources if source["key"] == "netshopSku")
    periods = comparison_periods(COMMON["startDate"], COMMON["endDate"])
    current_days = [periods["current"]["startDate"], periods["current"]["endDate"]]
    native["netshopSku"], infos["netshopSku"] = native_page(current, [
        product(10, current_days[0], "S1", "P1", 0, 3),
        product(11, current_days[1], "S1", "P1", 20, 4)])
    for window, payment in (("previous", 10), ("yearAgo", 5)):
        key = "sku-"+window
        source = {"key": key, "domain": "netshop",
            "query": {**current["query"], "window": window}}
        days = [periods[window]["startDate"], periods[window]["endDate"]]
        second_sku = "S2" if window == "previous" and partial_previous else "S1"
        pages, info = native_page(source, [
            product(20, days[0], "S1", "P1", payment, 3),
            product(21, days[1], second_sku, "P1", payment, 4)])
        sources.append(source); keys["netshopSku"][window] = key
        native[key], infos[key] = pages, info
    plan = planning.prepare_candidate(sources, infos, CONTEXT, keys)
    values = {"current": daily.prepare_candidate(plan, sources, infos, CONTEXT, keys,
        "current", manifest, erp, {key: native[key] for key in
            ("netshopSku", "netshopSpu", "promotion")})}
    for window in ("previous", "yearAgo"):
        key = keys["netshopSku"][window]
        values[window] = daily.prepare_candidate(plan, sources, infos, CONTEXT,
            keys, window, None, None, {key: native[key]})
    return plan, sources, infos, keys, values


class CrossSourceSkuWindowCompareTests(TestCase):
    def test_current_sku_and_missing_promotion_bucket_stay_separate(self):
        plan, sources, infos, keys, values = materials()
        result = service.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        self.assertFalse(result["authorityVerified"])
        self.assertTrue(result["nativeSpuExcludedFromSku"])
        self.assertTrue(result["erpUnassignedExcludedFromSku"])
        missing = [row for row in result["rows"] if row["skuId"] is None]
        self.assertTrue(missing)
        self.assertEqual({row["column"] for row in missing}, {"promotion"})
        self.assertTrue(all(not row["actionableSku"] and all(value["growthRateBps"] is None
            for value in row["comparisons"].values()) for row in missing))
        spend = next(row for row in missing if row["metric"] == "spendCents")
        self.assertEqual(spend["windows"]["current"]["value"], 5)
        self.assertEqual(spend["windows"]["previous"]["status"], "missing_source")
        self.assertEqual(result["comparisonDigest"], digest({key: value for key, value
            in result.items() if key != "comparisonDigest"}))
        self.assertFalse(result["historicalErpSkuOwnershipVerified"])
        erp = [row for row in result["rows"] if row["column"] == "erpMatched"]
        self.assertTrue(erp)
        self.assertTrue(all(cell == {"status": "historical_identity_unverified",
            "difference": None, "growthRateBps": None}
            for row in erp for cell in row["comparisons"].values()))

    def test_complete_erp_values_still_cannot_claim_historical_sku_growth(self):
        current = {"status": "observed_rows", "value": 150,
            "presentRows": 30, "missingRows": 0}
        previous = {"status": "observed_rows", "value": 100,
            "presentRows": 30, "missingRows": 0}
        self.assertEqual(service._comparison("erpMatched", False, current, previous),
            {"status": "historical_identity_unverified", "difference": None,
             "growthRateBps": None})
        self.assertEqual(service._comparison("netshopSku", False, current, previous),
            {"status": "comparable", "difference": 50, "growthRateBps": 5000})
        self.assertEqual(service._comparison("promotion", True, current, previous),
            {"status": "missing_explicit_promotion_sku", "difference": None,
             "growthRateBps": None})

    def test_complete_explicit_sku_metric_compares_without_current_master_history(self):
        plan, sources, infos, keys, values = full_sku_materials()
        result = service.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        payment = next(row for row in result["rows"] if row["skuId"] == "S1"
            and row["column"] == "netshopSku" and row["metric"] == "paymentCents")
        self.assertEqual(payment["windows"]["current"]["value"], 20)
        self.assertEqual(payment["windows"]["previous"]["value"], 20)
        self.assertEqual(payment["windows"]["yearAgo"]["value"], 10)
        self.assertEqual(payment["comparisons"]["previous"]["growthRateBps"], 0)
        self.assertEqual(payment["comparisons"]["yearAgo"]["growthRateBps"], 10000)
        self.assertEqual(payment["windows"]["previous"]["netshopSkuNativeSpuId"], "P1")
        self.assertTrue(result["currentMasterNotHistoricalOwnership"])

    def test_sku_absent_on_one_covered_day_has_no_growth(self):
        plan, sources, infos, keys, values = full_sku_materials(partial_previous=True)
        result = service.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        payment = next(row for row in result["rows"] if row["skuId"] == "S1"
            and row["column"] == "netshopSku" and row["metric"] == "paymentCents")
        self.assertEqual(payment["windows"]["previous"]["status"],
            "sku_not_observed_on_covered_day")
        self.assertIsNone(payment["comparisons"]["previous"]["growthRateBps"])

    def test_rehashed_identity_and_daily_conservation_mutations_reject(self):
        plan, sources, infos, keys, values = materials()
        base = values["current"]
        missing = next(row for row in base["skuDayRows"] if row["skuId"] is None)
        bad = deepcopy(values)
        row = next(row for row in bad["current"]["skuDayRows"] if row["id"] == missing["id"])
        row["skuId"] = "S1"
        row["id"] = digest([plan["planDigest"], "sku_day",
            {key: value for key, value in row.items() if key != "id"}])
        resign(bad["current"])
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys, bad)
        bad = deepcopy(values)
        row = next(row for row in bad["current"]["skuDayRows"] if row["skuId"] == "S1")
        row["promotion"]["spendCents"]["value"] += 1
        row["id"] = digest([plan["planDigest"], "sku_day",
            {key: value for key, value in row.items() if key != "id"}])
        resign(bad["current"])
        with self.assertRaisesRegex(AnalysisContractError, "SKU日来源"):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys, bad)
        bad = deepcopy(values)
        row = next(row for row in bad["current"]["skuDayRows"] if row["skuId"] == "S1")
        row["erpMatched"]["netSalesCents"]["value"] += 1
        row["id"] = digest([plan["planDigest"], "sku_day",
            {key: value for key, value in row.items() if key != "id"}])
        resign(bad["current"])
        with self.assertRaisesRegex(AnalysisContractError, "ERP本业务日"):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys, bad)

    def test_wrong_window_and_missing_material_reject(self):
        plan, sources, infos, keys, values = materials()
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                {"current": values["current"], "previous": values["previous"]})
        bad = deepcopy(values)
        bad["current"]["skuDayRows"][0]["window"] = "previous"
        resign(bad["current"])
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys, bad)
