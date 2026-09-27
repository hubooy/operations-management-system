"""Pure fixed-source KPI selection cannot assign or invent sales facts."""
from copy import deepcopy
from unittest import TestCase

from . import cross_source_kpi_plan as service
from .contracts import AnalysisContractError, comparison_periods, coverage, digest


DATES = ("2026-08-16", "2026-09-14")
CONTEXT = {"reportId": "report-1", "evidenceRunId": "run-1",
    "evidenceVersion": 8, "sealedDigest": "a"*64,
    "ownerEmail": "owner@example.invalid", "scope": None}


def fixture():
    common = {"platform": "京东", "shop": "精确店铺",
        "startDate": DATES[0], "endDate": DATES[1]}
    master = {"key": "master", "domain": "netshop",
        "query": {**common, "dataset": "master", "window": "current"}}
    sources = [master]
    keys = {"master": "master"}
    for family, domain, extra in (("erpSales", "sales", {"channel": "京东-精确店铺"}),
            ("netshopSku", "netshop", {"dataset": "sku"}),
            ("netshopSpu", "netshop", {"dataset": "spu"}),
            ("promotion", "netshop", {"dataset": "promotion"})):
        keys[family] = {}
        for window in service.WINDOWS:
            key = f"{family}-{window}"
            keys[family][window] = key
            sources.append({"key": key, "domain": domain,
                "query": {**common, **extra, "window": window}})
    periods = comparison_periods(*DATES)
    infos = {}
    for source in sources:
        query = source["query"]
        if query.get("dataset") == "master":
            amount, metrics = 2, {}
            cov = {"status": "current_master", "snapshotDate": "2026-09-14",
                "batchId": "master-batch", "historicalMapping": False}
        else:
            window = periods[query["window"]]
            cov = coverage(window, [window["startDate"], window["endDate"]])
            keys_ = (service.ERP_METRICS if source["domain"] == "sales" else
                service.PROMOTION_METRICS if query.get("dataset") == "promotion" else
                service.PRODUCT_METRICS)
            amount, metrics = 2, {key: {"value": 300,
                "presentRows": 2, "missingRows": 0} for key in keys_}
        infos[source["key"]] = {"pageCount": 1,
            "metadata": {"sourceRevision": "7:aaaaaaaaaaaa", "coverage": cov},
            "expected": {"sourceRef": digest(source), "rowCount": amount,
                "metrics": metrics, "reconciled": True,
                "evidenceDigest": digest([source, cov])}}
    return sources, infos, keys


class CrossSourceKpiPlanTests(TestCase):
    def test_single_store_full_window_plan_is_only_a_non_authorizing_selection(self):
        sources, infos, keys = fixture()
        value = service.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertEqual((value["platform"], value["shop"]), ("京东", "精确店铺"))
        self.assertEqual(len(value["sourceBindings"]), 13)
        self.assertEqual(len(value["mappingPlan"]["pairs"]), 3)
        self.assertEqual(value["baselineMappingPairs"]["previous"]["window"], "previous")
        self.assertEqual(value["baselineMappingPairs"]["yearAgo"]["window"], "yearAgo")
        self.assertEqual(value["missingSources"], [])
        self.assertFalse(value["identityAssignmentVerified"])
        self.assertFalse(value["numericTotalsVerified"])
        self.assertFalse(value["crossDomainSnapshotVerified"])
        self.assertFalse(value["authorityVerified"])
        self.assertTrue(value["metricPolicies"]["netshopSkuAndNativeSpuAreSeparate"])
        self.assertEqual(service.check_candidate(sources, infos, CONTEXT, keys, value), value)

    def test_missing_year_ago_and_no_records_stay_null_or_unavailable(self):
        sources, infos, keys = fixture()
        for family in service.FAMILIES:
            old_key = keys[family]["yearAgo"]
            infos.pop(old_key)
            sources = [item for item in sources if item["key"] != old_key]
            keys[family]["yearAgo"] = None
        value = service.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertEqual(len(value["missingSources"]), 4)
        self.assertIsNone(value["baselineMappingPairs"]["yearAgo"])
        self.assertTrue(all(value["sourceStatus"][family]["yearAgo"] ==
            "missing_source" for family in service.FAMILIES))
        self.assertNotIn("revenue", value)
        current = keys["promotion"]["current"]
        infos[current]["expected"].update(rowCount=0,
            metrics={key: {"value": None, "presentRows": 0, "missingRows": 0}
                for key in service.PROMOTION_METRICS})
        infos[current]["metadata"]["coverage"] = coverage(
            value["periods"]["current"], [])
        empty = service.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertEqual(empty["sourceStatus"]["promotion"]["current"],
            "selected_no_records")
        self.assertFalse(empty["numericTotalsVerified"])

    def test_cross_store_wrong_role_period_and_duplicate_source_reject(self):
        sources, infos, keys = fixture()
        cases = []
        hidden = deepcopy(keys)
        hidden["promotion"]["yearAgo"] = None
        cases.append((sources, infos, hidden))
        changed = deepcopy(sources)
        next(item for item in changed if item["key"] == keys["erpSales"]["previous"])["query"]["shop"] = "另一店"
        cases.append((changed, infos, keys))
        changed = deepcopy(sources)
        next(item for item in changed if item["key"] == keys["netshopSku"]["previous"])["query"]["window"] = "yearAgo"
        cases.append((changed, infos, keys))
        changed = deepcopy(sources)
        next(item for item in changed if item["key"] == keys["promotion"]["yearAgo"])["query"]["startDate"] = "2026-08-17"
        cases.append((changed, infos, keys))
        changed = deepcopy(sources)
        changed.append({**deepcopy(changed[1]), "key": "duplicate-query"})
        cases.append((changed, infos, keys))
        changed_keys = deepcopy(keys)
        changed_keys["netshopSpu"]["current"] = keys["netshopSku"]["current"]
        cases.append((sources, infos, changed_keys))
        for entries, details, selected in cases:
            with self.subTest(selected=selected), self.assertRaises(AnalysisContractError):
                service.prepare_candidate(entries, details, CONTEXT, selected)

    def test_ambiguous_identity_cannot_be_force_assigned_by_selector_or_metadata(self):
        sources, infos, keys = fixture()
        infos["master"]["expected"]["rowCount"] = 3
        value = service.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertFalse(value["identityAssignmentVerified"])
        self.assertTrue(value["metricPolicies"]["unmatchedAndAmbiguousStayUnassigned"])
        self.assertNotIn("skuId", value)
        forced = deepcopy(keys)
        forced["forcedAssignments"] = {"ambiguous": "SKU1"}
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(sources, infos, CONTEXT, forced)
        with self.assertRaises(AnalysisContractError):
            service.check_candidate(sources, infos,
                {**CONTEXT, "reportId": "other-report"}, keys, value)

    def test_coverage_and_source_ref_are_bound_but_not_self_authorizing(self):
        sources, infos, keys = fixture()
        value = service.prepare_candidate(sources, infos, CONTEXT, keys)
        stale = deepcopy(infos)
        current = keys["erpSales"]["current"]
        stale[current]["expected"]["sourceRef"] = "0"*64
        changed = service.prepare_candidate(sources, stale, CONTEXT, keys)
        self.assertNotEqual(changed["planDigest"], value["planDigest"])
        self.assertFalse(changed["authorityVerified"])
        with self.assertRaises(AnalysisContractError):
            service.check_candidate(sources, stale, CONTEXT, keys, value)
        malformed = deepcopy(infos)
        malformed[current]["metadata"]["coverage"]["status"] = "dates_present"
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(sources, malformed, CONTEXT, keys)

    def test_reference_scale_above_v2_page_budget_is_explicitly_unsupported(self):
        sources, infos, keys = fixture()
        promotion = keys["promotion"]["current"]
        infos[promotion]["expected"]["rowCount"] = 575_095
        for cell in infos[promotion]["expected"]["metrics"].values():
            cell["missingRows"] = 575_093
        infos[promotion]["pageCount"] = service.MAX_V2_SOURCE_PAGES
        candidate = service.prepare_candidate(sources, infos, CONTEXT, keys)
        self.assertEqual(candidate["referenceScaleCapacityGap"], [promotion])
        self.assertEqual(candidate["sourceBindings"][promotion]["capacityStatus"],
            "reference_scale_capacity_gap")
        self.assertEqual(candidate["sourceStatus"]["promotion"]["current"],
            "unsupported_current_v2_collector")
        self.assertFalse(candidate["currentV2CollectorSupported"])
        self.assertFalse(candidate["authorityVerified"])
        self.assertFalse(candidate["numericTotalsVerified"])

    def test_source_metric_names_and_missing_value_counts_must_match_grain(self):
        sources, infos, keys = fixture()
        current = keys["erpSales"]["current"]
        for change in (lambda item: item["expected"]["metrics"].pop("costCents"),
                lambda item: item["expected"]["metrics"]["costCents"].update(
                    value=True),
                lambda item: item["expected"]["metrics"]["costCents"].update(
                    presentRows=0),
                lambda item: item["expected"]["metrics"]["costCents"].update(
                    value=None)):
            wrong = deepcopy(infos)
            change(wrong[current])
            with self.assertRaises(AnalysisContractError):
                service.prepare_candidate(sources, wrong, CONTEXT, keys)
        wrong = deepcopy(infos)
        wrong[keys["netshopSpu"]["current"]]["expected"]["metrics"] = (
            deepcopy(wrong[current]["expected"]["metrics"]))
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(sources, wrong, CONTEXT, keys)
