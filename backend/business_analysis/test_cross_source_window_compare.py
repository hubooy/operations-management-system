"""Three-window data-only comparisons preserve source and missingness fences."""
from copy import deepcopy
from unittest import TestCase

from . import cross_source_daily_columns as daily, cross_source_window_compare as compare
from . import cross_source_kpi_plan as planning
from .contracts import AnalysisContractError, PageReconciler, comparison_periods, digest
from .test_cross_source_daily_columns import CONTEXT, COMMON, fixture, native_page, product


def materials():
    plan, sources, infos, keys, erp_manifest, erp_ndjson, native = fixture()
    values = {"current": daily.prepare_candidate(plan, sources, infos, CONTEXT, keys,
        "current", erp_manifest, erp_ndjson, native)}
    for window in ("previous", "yearAgo"):
        values[window] = daily.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            window, None, None, {})
    return plan, sources, infos, keys, values


def resign(value):
    value["materialDigest"] = digest({key: child for key, child in value.items()
        if key != "materialDigest"})
    return value


class CrossSourceWindowCompareTests(TestCase):
    def test_three_windows_keep_missing_and_partial_source_status_without_growth(self):
        plan, sources, infos, keys, values = materials()
        result = compare.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        self.assertEqual(result["schemaVersion"], compare.SCHEMA)
        self.assertEqual(result["sourceMaterialDigests"], {window: values[window]["materialDigest"]
            for window in compare.WINDOWS})
        self.assertTrue(all(row["comparisons"]["previous"]["growthRateBps"] is None
            and row["comparisons"]["yearAgo"]["growthRateBps"] is None
            for row in result["rows"]))
        payment = next(row for row in result["rows"] if row["column"] == "netshopSku"
            and row["metric"] == "paymentCents")
        self.assertEqual(payment["windows"]["current"]["value"], 0)
        self.assertEqual(payment["windows"]["current"]["status"], "date_not_covered")
        self.assertEqual(payment["windows"]["current"]["dayStatusCounts"],
            {"date_not_covered": 1, "observed_rows": 1})
        self.assertEqual(payment["windows"]["previous"]["status"], "missing_source")
        self.assertEqual(payment["windows"]["previous"]["value"], None)
        unassigned = next(row for row in result["rows"] if row["column"] == "erpUnassigned"
            and row["metric"] == "netSalesCents")
        self.assertEqual(unassigned["subsetOf"], "erpSales")
        self.assertFalse(result["crossDomainAmountsAdded"])
        self.assertFalse(result["shopUniqueVisitorsAvailable"])
        self.assertFalse(result["skuDayRowsIndependentlyValidated"])
        self.assertFalse(result["authorityVerified"])
        self.assertEqual(result["comparisonDigest"], digest({key: child for key, child
            in result.items() if key != "comparisonDigest"}))

    def test_tampered_plan_window_period_binding_and_rehashed_day_status_reject(self):
        plan, sources, infos, keys, values = materials()
        for change in (
            lambda item: item.update(planDigest="0"*64),
            lambda item: item.update(window="previous"),
            lambda item: item["period"].update(endDate="2026-08-18"),
            lambda item: item["sourceBindings"]["promotion"].update(sourceRevision="different"),
            lambda item: item["shopDayRows"][1]["sourceDayStatus"].update(netshopSku="observed_rows"),
            lambda item: item["shopDayRows"][1]["netshopSku"]["paymentCents"].update(
                value=0, presentRows=1),
        ):
            bad = deepcopy(values); change(bad["current"]); resign(bad["current"])
            with self.subTest(change=change), self.assertRaises(AnalysisContractError):
                compare.prepare_candidate(plan, sources, infos, CONTEXT, keys, bad)

    def test_rate_requires_full_same_metric_positive_baseline_and_safe_difference(self):
        whole = {"status": "observed_rows", "value": 150, "presentRows": 2, "missingRows": 0}
        baseline = {"status": "observed_rows", "value": 100, "presentRows": 2, "missingRows": 0}
        self.assertEqual(compare._comparison(whole, baseline),
            {"status": "comparable", "difference": 50, "growthRateBps": 5000})
        self.assertEqual(compare._comparison({**whole, "value": 0}, baseline)["growthRateBps"], -10000)
        for incomplete in ({**whole, "status": "date_not_covered"},
                           {**whole, "status": "partial_metric_coverage", "missingRows": 1},
                           {**whole, "value": None, "presentRows": 0}):
            self.assertIsNone(compare._comparison(incomplete, baseline)["growthRateBps"])
        self.assertEqual(compare._comparison(whole, {**baseline, "value": 0})["status"], "zero_baseline")
        self.assertIsNone(compare._comparison(whole, {**baseline, "value": -10})["growthRateBps"])

    def test_real_three_materials_compare_only_complete_same_source_metric(self):
        plan, sources, infos, keys, manifest, erp, native = fixture()
        current = next(source for source in sources if source["key"] == "netshopSku")
        current_days = [COMMON["startDate"], COMMON["endDate"]]
        native["netshopSku"], infos["netshopSku"] = native_page(current, [
            product(10, current_days[0], "S1", "P1", 0, 3),
            product(11, current_days[1], "S1", "P1", 20, None)])
        periods = comparison_periods(COMMON["startDate"], COMMON["endDate"])
        for window, payment in (("previous", 10), ("yearAgo", 5)):
            key = "sku-" + window
            source = {"key": key, "domain": "netshop",
                "query": {**current["query"], "window": window}}
            days = [periods[window]["startDate"], periods[window]["endDate"]]
            pages, info = native_page(source, [
                product(20, days[0], "S1", "P1", payment, 3),
                product(21, days[1], "S1", "P1", payment, 4)])
            sources.append(source); keys["netshopSku"][window] = key
            native[key], infos[key] = pages, info
        plan = planning.prepare_candidate(sources, infos, CONTEXT, keys)
        values = {"current": daily.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            "current", manifest, erp, {key: native[key] for key in
                ("netshopSku", "netshopSpu", "promotion")})}
        for window in ("previous", "yearAgo"):
            key = keys["netshopSku"][window]
            values[window] = daily.prepare_candidate(plan, sources, infos,
                CONTEXT, keys, window, None, None, {key: native[key]})
        result = compare.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        payment = next(row for row in result["rows"] if row["column"] == "netshopSku"
            and row["metric"] == "paymentCents")
        self.assertEqual([payment["windows"][window]["status"] for window in compare.WINDOWS],
            ["observed_rows"] * 3)
        self.assertEqual([payment["windows"][window]["value"] for window in compare.WINDOWS],
            [20, 20, 10])
        self.assertEqual(payment["windows"]["current"]["sourceStatus"],
            "partial_metric_coverage")
        self.assertEqual(payment["windows"]["current"]["status"], "observed_rows")
        self.assertEqual(payment["comparisons"]["previous"]["growthRateBps"], 0)
        self.assertEqual(payment["comparisons"]["yearAgo"]["growthRateBps"], 10000)
        visitors = next(row for row in result["rows"] if row["column"] == "netshopSku"
            and row["metric"] == "productDayVisitors")
        self.assertEqual(visitors["windows"]["current"]["status"],
            "partial_metric_coverage")
        self.assertIsNone(visitors["comparisons"]["previous"]["growthRateBps"])
        erp_metric = next(row for row in result["rows"] if row["column"] == "erpSales"
            and row["metric"] == "netSalesCents")
        self.assertIsNone(erp_metric["comparisons"]["previous"]["growthRateBps"])

    def test_requires_three_materials_and_does_not_mutate_inputs(self):
        plan, sources, infos, keys, values = materials()
        old = deepcopy(values)
        compare.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        self.assertEqual(values, old)
        with self.assertRaises(AnalysisContractError):
            compare.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                {"current": values["current"], "previous": values["previous"]})

    def test_missing_day_and_other_metric_partial_statuses_both_remain_visible(self):
        plan, sources, infos, keys, manifest, erp, native = fixture()
        page = native["netshopSku"][0]
        page["items"][0]["metrics"]["productDayVisitors"] = None
        page["control"]["typedTotals"]["productDayVisitors"] = 0
        page["pageEvidence"]["sha256"] = digest(page["items"])
        verifier = PageReconciler(); verifier.consume(page)
        infos["netshopSku"]["expected"] = verifier.result()
        plan = planning.prepare_candidate(sources, infos, CONTEXT, keys)
        values = {"current": daily.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            "current", manifest, erp, native)}
        for window in ("previous", "yearAgo"):
            values[window] = daily.prepare_candidate(plan, sources, infos,
                CONTEXT, keys, window, None, None, {})
        result = compare.prepare_candidate(plan, sources, infos, CONTEXT, keys, values)
        payment = next(row for row in result["rows"] if row["column"] == "netshopSku"
            and row["metric"] == "paymentCents")
        self.assertEqual(payment["windows"]["current"]["sourceStatus"],
            "date_not_covered")
        self.assertEqual(payment["windows"]["current"]["dayStatusCounts"],
            {"date_not_covered": 1, "partial_metric_coverage": 1})
        self.assertIsNone(payment["comparisons"]["previous"]["growthRateBps"])
