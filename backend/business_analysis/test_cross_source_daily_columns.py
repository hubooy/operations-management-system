"""Source-isolated daily alignment never fabricates totals or attribution."""
from copy import deepcopy
import hashlib
import json
from unittest import TestCase

from . import (cross_source_daily_columns as service, cross_source_kpi_plan,
    erp_fact_assignment, erp_fact_rollups)
from .contracts import AnalysisContractError, PageReconciler, comparison_periods, coverage, digest
from .test_erp_fact_assignment import fixture as erp_fixture


CONTEXT = {"reportId": "report-1", "evidenceRunId": "run-1",
    "evidenceVersion": 8, "sealedDigest": "a"*64,
    "ownerEmail": "owner@example.invalid", "scope": None}
COMMON = {"platform": "京东", "shop": "合成店",
    "startDate": "2026-08-16", "endDate": "2026-08-17"}


def native_page(source, items):
    query = source["query"]
    family = query["dataset"]
    source_name, dataset = service._NATIVE[
        {"sku": "netshopSku", "spu": "netshopSpu", "promotion": "promotion"}[family]]
    keys = (cross_source_kpi_plan.PROMOTION_METRICS if family == "promotion"
        else cross_source_kpi_plan.PRODUCT_METRICS)
    period = comparison_periods(query["startDate"], query["endDate"])
    observed = {item["date"] for item in items}
    page = {"schemaVersion": "business-analysis-v1", "source": source_name,
        "sourceDataset": dataset, "sourceRef": digest(query),
        "sourceRevision": "1:aaaaaaaaaaaa", "monetaryUnit": "CNY_CENT",
        "filters": {"platform": query["platform"], "shop": query["shop"],
            "dataset": family, "window": query["window"], "periods": period},
        "coverage": coverage(period[query["window"]], observed),
        "control": {"rowCount": len(items), "typedTotals":
            {key: sum(item["metrics"][key] or 0 for item in items) for key in keys}},
        "items": items, "pageEvidence": {"rowCount": len(items),
            "sha256": digest(items)},
        "pagination": {"hasMore": False, "nextCursor": None, "limit": 100}}
    verifier = PageReconciler(); verifier.consume(page)
    return [page], {"metadata": {"sourceRevision": page["sourceRevision"],
        "coverage": page["coverage"]}, "expected": verifier.result(), "pageCount": 1}


def product(number, day, sku, spu, payment, visitors):
    metrics = {key: 0 for key in cross_source_kpi_plan.PRODUCT_METRICS}
    metrics.update(paymentCents=payment, productDayVisitors=visitors)
    return {"rowId": str(number), "sourceRowHash": digest(["product", number]),
        "platform": "京东", "shopName": "合成店", "date": day,
        "skuId": sku, "spuId": spu, "metrics": metrics}


def promotion(number, day, sku, spend, gmv):
    metrics = {key: 0 for key in cross_source_kpi_plan.PROMOTION_METRICS}
    metrics.update(spendCents=spend, reportedGmvCents=gmv)
    return {"rowId": str(number), "sourceRowHash": digest(["promotion", number]),
        "platform": "京东", "shopName": "合成店", "date": day,
        "dimensions": {"promotedSkuId": sku}, "metrics": metrics}


def fixture(*, two_erp_days=False):
    sources = [{"key": "master", "domain": "netshop",
        "query": {**COMMON, "dataset": "master", "window": "current"}},
        {"key": "sales", "domain": "sales",
            "query": {**COMMON, "channel": "京东-合成店", "window": "current"}}]
    keys = {"master": "master"}
    for family, dataset in (("erpSales", None), ("netshopSku", "sku"),
            ("netshopSpu", "spu"), ("promotion", "promotion")):
        keys[family] = {"current": "sales" if family == "erpSales" else family,
            "previous": None, "yearAgo": None}
        if dataset is not None:
            sources.append({"key": family, "domain": "netshop",
                "query": {**COMMON, "dataset": dataset, "window": "current"}})
    sales, se, master, me = erp_fixture()
    if two_erp_days:
        sales[0]["items"][0]["date"] = COMMON["endDate"]
        sales[0]["coverage"] = coverage(
            comparison_periods(COMMON["startDate"], COMMON["endDate"])["current"],
            {item["date"] for item in sales[0]["items"]})
        sales[0]["pageEvidence"]["sha256"] = digest(sales[0]["items"])
        verifier = PageReconciler(); verifier.consume(sales[0])
        se = verifier.result()
    infos = {"sales": {"metadata": {"sourceRevision": sales[0]["sourceRevision"],
        "coverage": sales[0]["coverage"]}, "expected": se, "pageCount": 1},
        "master": {"metadata": {"sourceRevision": master[0]["sourceRevision"],
            "coverage": master[0]["coverage"]}, "expected": me, "pageCount": 1}}
    native = {}
    entries = {
        "netshopSku": [product(10, COMMON["startDate"], "S1", "P1", 0, 3)],
        "netshopSpu": [product(11, COMMON["startDate"], None, "P1", 70, 4)],
        "promotion": [promotion(12, COMMON["startDate"], "S1", 20, 60),
            promotion(13, COMMON["endDate"], None, 5, 10)]}
    for source in sources:
        if source["key"] in entries:
            native[source["key"]], infos[source["key"]] = native_page(
                source, entries[source["key"]])
    plan = cross_source_kpi_plan.prepare_candidate(sources, infos, CONTEXT, keys)
    with erp_fact_assignment.assign_facts(sources, plan["mappingPlan"],
            plan["currentMappingPairKey"], sales, master, se, me) as ledger:
        summary = ledger.summary()
        with erp_fact_rollups.prepare(ledger) as prepared:
            raw = {kind: list(prepared.ndjson_pages(kind))
                for kind in service._ERP_KINDS}
            rollup = prepared.manifest
    binding = {"reportId": CONTEXT["reportId"],
        "evidenceRunId": CONTEXT["evidenceRunId"],
        "evidenceVersion": CONTEXT["evidenceVersion"],
        "sealedDigest": CONTEXT["sealedDigest"],
        "principalKey": digest([CONTEXT["ownerEmail"], "null"]),
        "mappingPlanDigest": plan["mappingPlanDigest"]}
    manifest = {"schemaVersion": service.ERP_SCHEMA,
        "reportBinding": binding, "pairKey": plan["currentMappingPairKey"],
        "salesKey": "sales", "masterKey": "master",
        "salesQueryDigest": digest(sources[1]["query"]),
        "masterQueryDigest": digest(sources[0]["query"]),
        "sourceProofs": summary["sourceProofs"],
        "assignmentSummaryDigest": summary["resultDigest"],
        "rollupManifestDigest": rollup["manifestDigest"],
        "tables": rollup["tables"], "sourceRowCount": rollup["sourceRowCount"],
        "sourceTotals": rollup["sourceTotals"],
        "matchedTotals": rollup["matchedTotals"],
        "unassignedTotals": rollup["unassignedTotals"],
        "authorityVerified": False, "registeredRenderer": False,
        "netshopAdFinanceCombined": False,
        "historicalOwnershipVerified": False}
    manifest["manifestDigest"] = digest(manifest)
    return plan, sources, infos, keys, manifest, raw, native


class CrossSourceDailyColumnTests(TestCase):
    def test_shop_sku_refund_missing_date_zero_and_promotion_missing_sku(self):
        plan, sources, infos, keys, manifest, raw, native = fixture()
        value = service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            "current", manifest, raw, native)
        self.assertEqual(value["schemaVersion"], service.SCHEMA)
        self.assertEqual(len(value["shopDayRows"]), 2)
        first, second = value["shopDayRows"]
        self.assertEqual(first["erpSales"]["netSalesCents"]["value"], 155)
        self.assertEqual(first["erpSales"]["refundCents"]["value"], 30)
        self.assertEqual(first["erpUnassigned"]["netSalesCents"]["value"], 55)
        self.assertEqual(first["netshopSku"]["paymentCents"],
            {"value": 0, "presentRows": 1, "missingRows": 0})
        self.assertEqual(first["netshopSku"]["productDayVisitors"]["value"], 3)
        self.assertEqual(first["netshopSpuNative"]["paymentCents"]["value"], 70)
        self.assertEqual(first["promotion"]["spendCents"]["value"], 20)
        self.assertEqual(second["netshopSku"]["paymentCents"],
            {"value": None, "presentRows": 0, "missingRows": 0})
        self.assertEqual(second["sourceDayStatus"]["netshopSku"], "date_not_covered")
        self.assertIsNone(second["erpSales"]["netSalesCents"]["value"])
        self.assertFalse(value["shopUniqueVisitorsAvailable"])
        self.assertFalse(value["crossDomainAmountsAdded"])
        missing = [row for row in value["skuDayRows"] if row["skuId"] is None]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["promotion"]["spendCents"]["value"], 5)
        self.assertIsNone(missing[0]["erpMatched"]["netSalesCents"]["value"])
        self.assertEqual(value["materialDigest"], digest({k:v for k,v in value.items()
            if k != "materialDigest"}))

    def test_cross_store_window_report_root_and_duplicate_pages_reject(self):
        plan, sources, infos, keys, manifest, raw, native = fixture()
        bad_sources = deepcopy(sources)
        next(s for s in bad_sources if s["key"] == "netshopSku")["query"]["shop"] = "另一店"
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, bad_sources, infos, CONTEXT, keys,
                "current", manifest, raw, native)
        for changed in ({"reportBinding": {**manifest["reportBinding"],
                    "reportId": "other-report"}},
                {"salesKey": "netshopSku"}, {"pairKey": "0"*64}):
            wrong = deepcopy(manifest); wrong.update(changed)
            wrong["manifestDigest"] = digest({k:v for k,v in wrong.items()
                if k != "manifestDigest"})
            with self.assertRaises(AnalysisContractError):
                service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                    "current", wrong, raw, native)
        repeated = deepcopy(native)
        repeated["promotion"][0]["items"].append(
            deepcopy(repeated["promotion"][0]["items"][0]))
        repeated["promotion"][0]["pageEvidence"].update(
            rowCount=3, sha256=digest(repeated["promotion"][0]["items"]))
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                "current", manifest, raw, repeated)
        wrong_window = deepcopy(native)
        wrong_window["netshopSku"][0]["filters"]["window"] = "previous"
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                "current", manifest, raw, wrong_window)

    def test_missing_baseline_source_is_null_not_zero(self):
        plan, sources, infos, keys, _, _, _ = fixture()
        value = service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
            "yearAgo", None, None, {})
        self.assertEqual(value["sourceKeys"], {family: None
            for family in cross_source_kpi_plan.FAMILIES})
        self.assertTrue(all(row["sourceDayStatus"]["promotion"] == "missing_source"
            and row["promotion"]["spendCents"]["value"] is None
            and row["erpSales"]["netSalesCents"]["value"] is None
            for row in value["shopDayRows"]))
        self.assertEqual(value["skuDayRows"], [])

    def test_rehashed_erp_amount_cannot_break_source_conservation(self):
        plan, sources, infos, keys, manifest, raw, native = fixture()
        wrong = deepcopy(raw)
        rows = [json.loads(line) for line in b"".join(wrong["shop_day"]).splitlines()]
        rows[0]["metrics"]["netSalesCents"] += 1
        blob = b"".join((json.dumps(row, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"))+"\n").encode() for row in rows)
        wrong["shop_day"] = [blob]
        changed = deepcopy(manifest)
        spec = changed["tables"][0]
        spec["ndjsonBytes"] = len(blob)
        spec["ndjsonSha256"] = hashlib.sha256(blob).hexdigest()
        changed["manifestDigest"] = digest({k:v for k,v in changed.items()
            if k != "manifestDigest"})
        with self.assertRaises(AnalysisContractError):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                "current", changed, wrong, native)

    def test_mixed_null_with_zero_or_positive_discloses_partial_rows_at_both_grains(self):
        for known in (0, 50):
            with self.subTest(known=known):
                plan, sources, infos, keys, manifest, raw, native = fixture()
                page = native["netshopSku"][0]
                page["items"][0]["metrics"]["paymentCents"] = known
                missing = product(14, COMMON["startDate"], "S1", "P1", 0, 2)
                missing["metrics"]["paymentCents"] = None
                page["items"].append(missing)
                page["control"]["rowCount"] = 2
                page["control"]["typedTotals"] = {metric:
                    sum(item["metrics"][metric] or 0 for item in page["items"])
                    for metric in cross_source_kpi_plan.PRODUCT_METRICS}
                page["pageEvidence"] = {"rowCount": 2,
                    "sha256": digest(page["items"])}
                verifier = PageReconciler(); verifier.consume(page)
                infos["netshopSku"]["expected"] = verifier.result()
                plan = cross_source_kpi_plan.prepare_candidate(sources, infos,
                    CONTEXT, keys)
                value = service.prepare_candidate(plan, sources, infos,
                    CONTEXT, keys, "current", manifest, raw, native)
                shop = value["shopDayRows"][0]
                sku = next(row for row in value["skuDayRows"]
                    if row["skuId"] == "S1")
                expected = {"value": known, "presentRows": 1, "missingRows": 1}
                self.assertEqual(shop["netshopSku"]["paymentCents"], expected)
                self.assertEqual(sku["netshopSku"]["paymentCents"], expected)
                self.assertEqual(shop["sourceDayStatus"]["netshopSku"],
                    "partial_metric_coverage")
                self.assertEqual(infos["netshopSku"]["expected"]["metrics"]
                    ["paymentCents"], expected)

    def test_rehashed_erp_cross_day_shift_rejects_even_with_period_totals_intact(self):
        plan, sources, infos, keys, manifest, raw, native = fixture(two_erp_days=True)
        wrong = deepcopy(raw)
        rows = [json.loads(line) for line in b"".join(wrong["shop_day"]).splitlines()]
        self.assertEqual({row["date"] for row in rows},
            {COMMON["startDate"], COMMON["endDate"]})
        by_day = {row["date"]: row for row in rows}
        for metric in ("netSalesCents", "positiveSalesCents",
                "grossProfitCents", "netSalesExcludingAccessoriesCents"):
            by_day[COMMON["startDate"]]["metrics"][metric] += 10
            by_day[COMMON["endDate"]]["metrics"][metric] -= 10
        blob = b"".join((json.dumps(row, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"))+"\n").encode() for row in rows)
        wrong["shop_day"] = [blob]
        changed = deepcopy(manifest)
        changed["tables"][0].update(ndjsonBytes=len(blob),
            ndjsonSha256=hashlib.sha256(blob).hexdigest())
        changed["manifestDigest"] = digest({k:v for k,v in changed.items()
            if k != "manifestDigest"})
        with self.assertRaisesRegex(AnalysisContractError, "ERP同业务日"):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                "current", changed, wrong, native)

    def test_rehashed_native_duplicate_content_hash_with_fresh_id_rejects(self):
        plan, sources, infos, keys, manifest, raw, native = fixture()
        repeated = deepcopy(native)
        page = repeated["netshopSku"][0]
        second = product(14, COMMON["startDate"], "S1", "P1", 10, 2)
        second["sourceRowHash"] = page["items"][0]["sourceRowHash"]
        page["items"].append(second)
        page["control"]["rowCount"] = 2
        page["control"]["typedTotals"] = {metric:
            sum(item["metrics"][metric] or 0 for item in page["items"])
            for metric in cross_source_kpi_plan.PRODUCT_METRICS}
        page["pageEvidence"] = {"rowCount": 2, "sha256": digest(page["items"])}
        verifier = PageReconciler(); verifier.consume(page)
        infos["netshopSku"]["expected"] = verifier.result()
        plan = cross_source_kpi_plan.prepare_candidate(sources, infos,
            CONTEXT, keys)
        with self.assertRaisesRegex(AnalysisContractError, "源内容摘要重复"):
            service.prepare_candidate(plan, sources, infos, CONTEXT, keys,
                "current", manifest, raw, repeated)
