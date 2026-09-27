"""Synthetic B2B pages only; no customer rows or database connection."""
from copy import deepcopy
from unittest import TestCase

from . import b2b_daily_candidate as candidate, evidence_v2
from .contracts import AnalysisContractError, PageReconciler, comparison_periods, coverage, digest


START, END = "2026-08-16", "2026-08-17"
PERIODS = comparison_periods(START, END)
METRICS = ("paymentCents", "paymentQuantity", "productDayVisitors",
    "pageViews", "reportedOrders")


def source(key, window="current", shop="样例店", dataset="b2b"):
    return {"key": key, "domain": "netshop", "query": {
        "platform": "京东", "shop": shop, "dataset": dataset,
        "startDate": START, "endDate": END, "window": window}}


def row(number, day, payment, *, orders=1, shop="样例店"):
    metrics = {metric: 0 for metric in METRICS}
    metrics.update(paymentCents=payment, reportedOrders=orders)
    return {"rowId": str(number), "sourceRowHash": digest(["b2b", number]),
        "platform": "京东", "shopName": shop, "date": day,
        "metrics": metrics}


def page_and_info(item_source, items):
    q = item_source["query"]
    filters = {key: q[key] for key in ("platform", "shop", "dataset", "window")}
    filters["periods"] = PERIODS
    observed = {item["date"] for item in items}
    ref = digest({"schemaVersion": "business-analysis-v1", "query": filters,
        "limit": 100, "revision": "1:synthetic", "masterBatch": None})
    page = {"schemaVersion": "business-analysis-v1", "source": "jd_b2b",
        "sourceDataset": "b2b", "sourceRef": ref,
        "sourceRevision": "1:synthetic", "monetaryUnit": "CNY_CENT",
        "filters": filters,
        "coverage": coverage(PERIODS[q["window"]], observed),
        "control": {"rowCount": len(items), "typedTotals": {metric:
            sum(item["metrics"][metric] or 0 for item in items)
            for metric in METRICS}},
        "items": items, "pageEvidence": {"rowCount": len(items),
            "sha256": digest(items)},
        "pagination": {"hasMore": False, "nextCursor": None, "limit": 100}}
    verifier = PageReconciler(); verifier.consume(page)
    info = {"metadata": {"coverage": page["coverage"],
        "sourceRevision": page["sourceRevision"]},
        "expected": verifier.result(), "pageCount": 1}
    return [page], info


def run_case(sources, infos, selected, pages):
    catalog = evidence_v2.build_catalog(sources)["header"]["catalogDigest"]
    return candidate.prepare_candidate(sources, infos, selected, pages,
        catalog_digest=catalog, platform="京东", shop="样例店",
        start_date=START, end_date=END)


class B2bDailyCandidateTests(TestCase):
    def test_complete_b2b_period_and_metric_specific_null(self):
        current, previous = source("b2b-now"), source("b2b-prev", "previous")
        before = PERIODS["previous"]
        current_rows = [row(1, START, 70), row(2, END, 30, orders=None)]
        previous_rows = [row(3, before["startDate"], 20),
            row(4, before["endDate"], 20)]
        now_pages, now_info = page_and_info(current, current_rows)
        prev_pages, prev_info = page_and_info(previous, previous_rows)
        sources = [current, previous]
        infos = {"b2b-now": now_info, "b2b-prev": prev_info}
        pages = {"b2b-now": now_pages, "b2b-prev": prev_pages}
        selected = {"current": "b2b-now", "previous": "b2b-prev", "yearAgo": None}
        snapshot = deepcopy((sources, infos, selected, pages))
        result = run_case(sources, infos, selected, pages)
        self.assertEqual(snapshot, (sources, infos, selected, pages))
        self.assertEqual(result["windows"]["current"]["periodMetrics"]
            ["paymentCents"]["value"], 100)
        self.assertEqual(result["comparisons"]["previous"]["paymentCents"],
            {"status": "comparable", "difference": 60, "growthRateBps": 15000})
        self.assertEqual(result["windows"]["current"]["periodMetrics"]
            ["reportedOrders"]["status"], "partial_metric_coverage")
        self.assertIsNone(result["comparisons"]["previous"]
            ["reportedOrders"]["growthRateBps"])
        self.assertEqual(result["windows"]["yearAgo"]["periodMetrics"]
            ["paymentCents"]["status"], "catalogue_only_missing_source")
        self.assertIsNone(result["b2bShareOfErpSales"])
        self.assertIsNone(result["b2bIncrementalSalesCents"])
        self.assertEqual(result["b2bIncludedInErpSales"], "unknown")
        self.assertFalse(result["crossDomainAmountsAdded"])

    def test_missing_source_is_only_supplied_catalogue_gap(self):
        sources = [source("sku", dataset="sku")]
        selected = dict.fromkeys(candidate.WINDOWS)
        result = run_case(sources, {}, selected, {})
        self.assertEqual(result["windows"]["current"]["periodMetrics"]
            ["paymentCents"]["status"], "catalogue_only_missing_source")
        self.assertIsNone(result["windows"]["current"]["periodMetrics"]
            ["paymentCents"]["value"])
        self.assertFalse(result["missingSourceAuthorityVerified"])
        with self.assertRaisesRegex(AnalysisContractError, "不能声明缺源"):
            run_case([source("b2b")], {}, selected, {})

    def test_missing_day_and_zero_baseline_do_not_infer_growth(self):
        current, previous = source("now"), source("prev", "previous")
        before = PERIODS["previous"]
        now_pages, now_info = page_and_info(current, [row(1, START, 10)])
        prev_pages, prev_info = page_and_info(previous,
            [row(2, before["startDate"], 0), row(3, before["endDate"], 0)])
        result = run_case([current, previous], {"now": now_info, "prev": prev_info},
            {"current": "now", "previous": "prev", "yearAgo": None},
            {"now": now_pages, "prev": prev_pages})
        self.assertEqual(result["windows"]["current"]["daily"][1]
            ["metrics"]["paymentCents"]["status"], "date_not_covered")
        self.assertIsNone(result["comparisons"]["previous"]
            ["paymentCents"]["growthRateBps"])

    def test_cross_shop_wrong_date_digest_and_content_reject(self):
        current = source("b2b")
        pages, info = page_and_info(current, [row(1, START, 25)])
        selected = {"current": "b2b", "previous": None, "yearAgo": None}
        for mutation in ("shop", "date", "sourceRef", "hash", "duplicate",
                         "rowId", "missingEvidence"):
            with self.subTest(mutation=mutation):
                wrong_pages, wrong_info = deepcopy((pages, info))
                page = wrong_pages[0]
                if mutation == "shop":
                    page["items"][0]["shopName"] = "另一店"
                elif mutation == "date":
                    page["items"][0]["date"] = "2026-08-18"
                elif mutation == "sourceRef":
                    page["sourceRef"] = "0"*64
                elif mutation == "hash":
                    page["items"][0]["metrics"]["paymentCents"] += 1
                elif mutation == "rowId":
                    page["items"][0]["rowId"] = "1e999"
                elif mutation == "missingEvidence":
                    page.pop("pageEvidence")
                else:
                    second = row(2, START, 25)
                    second["sourceRowHash"] = page["items"][0]["sourceRowHash"]
                    page["items"].append(second)
                if mutation in {"shop", "date", "duplicate"}:
                    page["pageEvidence"] = {"rowCount": len(page["items"]),
                        "sha256": digest(page["items"])}
                    page["control"]["rowCount"] = len(page["items"])
                    page["control"]["typedTotals"] = {metric:
                        sum(item["metrics"][metric] or 0 for item in page["items"])
                        for metric in METRICS}
                    verifier = PageReconciler(); verifier.consume(page)
                    wrong_info["expected"] = verifier.result()
                    wrong_info["metadata"]["coverage"] = page["coverage"]
                with self.assertRaises(AnalysisContractError):
                    run_case([current], {"b2b": wrong_info}, selected,
                        {"b2b": wrong_pages})
