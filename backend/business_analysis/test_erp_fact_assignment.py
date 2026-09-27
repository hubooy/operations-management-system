"""Per-ERP-fact ledger preserves every candidate and signed sales amount."""
from copy import deepcopy
from unittest import TestCase

from . import erp_fact_assignment as service
from . import cross_source_kpi_plan, mapping_plan
from .contracts import AnalysisContractError, PageReconciler, comparison_periods, coverage, digest


DATES = ("2026-08-16", "2026-08-17")
COMMON = {"platform": "京东", "shop": "合成店",
    "startDate": DATES[0], "endDate": DATES[1], "window": "current"}
SALES = {"key": "sales", "domain": "sales",
    "query": {**COMMON, "channel": "京东-合成店"}}
MASTER = {"key": "master", "domain": "netshop",
    "query": {**COMMON, "dataset": "master"}}
SOURCES = [SALES, MASTER]
PLAN = mapping_plan.build(SOURCES, [{"salesKey": "sales", "masterKey": "master"}])
PAIR = PLAN["plan"]["pairs"][0]["pairKey"]


def money(net, cost=0, quantity=1):
    value = {key: 0 for key in cross_source_kpi_plan.ERP_METRICS}
    value.update(netSalesCents=net, positiveSalesCents=max(net, 0),
        refundCents=max(-net, 0), costCents=cost,
        grossProfitCents=net-cost, netQuantity=quantity,
        positiveQuantity=max(quantity, 0), returnQuantity=max(-quantity, 0),
        netSalesExcludingAccessoriesCents=net)
    return value


def master(number, code, sku, spu, category="平台类目"):
    return {"rowId": str(number), "sourceRowHash": digest(["master", number]),
        "platform": "京东", "shopName": "合成店", "date": DATES[1],
        "snapshotDate": DATES[1], "skuId": sku, "spuId": spu,
        "category": category, "dimensions": {"merchantCode": code},
        "metrics": {}}


def sales(number, online, product, net, cost=0, *, day=DATES[0], shop="合成店"):
    return {"rowId": str(number), "sourceRowHash": digest(["sales", number]),
        "platform": "京东", "shopName": shop, "channel": "京东-合成店",
        "date": day, "onlineSpecCode": online, "productCode": product,
        "category": "ERP原类目", "metrics": money(net, cost)}


def page(source, items):
    query = source["query"]
    periods = comparison_periods(*DATES)
    filters = {"platform": query["platform"], "shop": query["shop"],
        "window": query["window"], "periods": periods}
    if source["domain"] == "sales":
        filters.update(channel=query["channel"],
            startDate=query["startDate"], endDate=query["endDate"], limit=100)
    else:
        filters["dataset"] = "master"
    metrics = {key: sum(row["metrics"][key] for row in items)
        for key in (items[0]["metrics"] if items else
            (cross_source_kpi_plan.ERP_METRICS if source["domain"] == "sales" else ())) }
    result = {"schemaVersion": "business-analysis-v1",
        "source": "erp_sales" if source["domain"] == "sales" else "jd_product_master",
        "sourceDataset": None if source["domain"] == "sales" else "product_master",
        "sourceRef": digest(query), "sourceRevision": "1:aaaaaaaaaaaa",
        "filters": filters, "items": items,
        "coverage": (coverage(periods[query["window"]],
            {row["date"] for row in items if type(row.get("date")) is str})
            if source["domain"] == "sales" else
            {"status": "current_master" if items else "no_records",
             "snapshotDate": DATES[1] if items else None,
             "batchId": "master-batch" if items else None,
             "historicalMapping": False}),
        "control": {"rowCount": len(items), "typedTotals": metrics},
        "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
        "pagination": {"hasMore": False, "nextCursor": None, "limit": 100}}
    verifier = PageReconciler(); verifier.consume(result)
    return [result], verifier.result()


def fixture():
    masters = [master(1, "C1", "S1", "P1"), master(2, "C1", "S1", "P1"),
        master(3, "C2", "S2", "P1"), master(4, "C2", "S3", "P1"),
        master(5, "C3", "S4", "P2"), master(6, "C3", "S5", "P3")]
    facts = [sales(1, "C1", "P1", 100, 40),
        sales(2, "C2", "P2", -30, -10),
        sales(3, "C3", "P3", 50, 0),
        sales(4, None, "C1", 25, 0),
        sales(5, "UNKNOWN", "P5", 10, 0)]
    sp, se = page(SALES, facts)
    mp, me = page(MASTER, masters)
    return sp, se, mp, me


class ErpFactAssignmentTests(TestCase):
    def opened(self, sp, se, mp, me, **kwargs):
        return service.assign_facts(SOURCES, PLAN["plan"], PAIR,
            sp, mp, se, me, **kwargs)

    def test_unique_sku_assigns_once_all_candidates_and_unmatched_pool_remains(self):
        sp, se, mp, me = fixture()
        with self.opened(sp, se, mp, me) as result:
            rows = list(result.scan()); summary = result.summary()
            self.assertEqual(summary["coverage"],
                {"matched": 1, "ambiguous": 2, "unmatched": 2, "incomplete": 0})
            self.assertEqual(summary["rowCount"], 5)
            self.assertEqual([row["sourceRowId"] for row in rows],
                ["1", "2", "3", "4", "5"])
            self.assertEqual(rows[0]["assignedSkuId"], "S1")
            self.assertEqual(len(rows[0]["masterCandidates"]), 2)
            for row in rows[1:]:
                self.assertTrue(row["unassigned"])
                self.assertIsNone(row["assignedSkuId"])
                self.assertIsNone(row["assignedSpuId"])
            self.assertEqual({candidate["skuId"] for candidate in rows[1]["masterCandidates"]},
                {"S2", "S3"})
            self.assertEqual({candidate["spuId"] for candidate in rows[1]["masterCandidates"]},
                {"P1"})
            self.assertEqual({candidate["spuId"] for candidate in rows[2]["masterCandidates"]},
                {"P2", "P3"})
            self.assertEqual(rows[3]["productCode"], "C1")
            self.assertEqual(rows[3]["masterCandidates"], [])
            self.assertEqual(rows[1]["metrics"]["netSalesCents"], -30)
            self.assertEqual(rows[1]["metrics"]["refundCents"], 30)
            self.assertEqual(rows[1]["metrics"]["costCents"], -10)
            self.assertEqual(rows[2]["metrics"]["costCents"], 0)
            self.assertEqual(summary["totals"]["netSalesCents"], 155)
            self.assertEqual(summary["unassignedTotals"]["netSalesCents"], 55)
            self.assertFalse(summary["historicalOwnershipVerified"])
            self.assertFalse(summary["netshopAdFinanceCombined"])
        with self.assertRaises(AnalysisContractError):
            list(result.scan())
        with self.opened(sp, se, mp, me) as result:
            suspended = result.scan()
            self.assertEqual(next(suspended)["sourceRowId"], "1")
        with self.assertRaises(AnalysisContractError):
            next(suspended)

    def test_duplicate_erp_row_missing_date_and_cross_shop_fail_closed(self):
        for change in (lambda facts: facts[1].update(rowId="1"),
                lambda facts: facts[1].update(date=None),
                lambda facts: facts[1].update(shopName="另一店"),
                lambda facts: facts[1]["metrics"].update(refundCents=0)):
            facts = deepcopy(fixture()[0][0]["items"])
            change(facts)
            try:
                sp, se = page(SALES, facts)
            except AnalysisContractError:
                continue
            _, _, mp, me = fixture()
            with self.assertRaises(AnalysisContractError):
                with self.opened(sp, se, mp, me):
                    pass

    def test_actual_master_filter_shape_and_cross_scope_tampering(self):
        sp, se, mp, me = fixture()
        self.assertEqual(set(mp[0]["filters"]),
            {"platform", "shop", "dataset", "periods", "window"})
        with self.opened(sp, se, mp, me) as result:
            self.assertEqual(result.summary()["rowCount"], 5)
        for change in (
                lambda filters: filters.update(limit=100),
                lambda filters: filters.update(shop="另一店"),
                lambda filters: filters.update(window="previous"),
                lambda filters: filters.update(periods={}),
                lambda filters: filters.update(startDate=DATES[0])):
            wrong = deepcopy(mp)
            change(wrong[0]["filters"])
            verifier = PageReconciler()
            verifier.consume(wrong[0])
            with self.assertRaises(AnalysisContractError):
                with self.opened(sp, se, wrong, verifier.result()):
                    pass

    def test_missing_spu_or_platform_category_stays_incomplete_and_unassigned(self):
        masters = [master(1, "NO-SPU", "S1", None),
            master(2, "NO-CATEGORY", "S2", "P2", category=None)]
        facts = [sales(1, "NO-SPU", "P1", 40, 10),
            sales(2, "NO-CATEGORY", "P2", -15, -2)]
        sp, se = page(SALES, facts)
        mp, me = page(MASTER, masters)
        with self.opened(sp, se, mp, me) as result:
            rows = list(result.scan()); summary = result.summary()
            self.assertEqual(summary["coverage"]["incomplete"], 2)
            self.assertEqual(summary["coverage"]["matched"], 0)
            self.assertTrue(all(row["unassigned"] and row["assignedSkuId"] is None
                and row["assignedSpuId"] is None
                and row["assignedPlatformCategory"] is None
                and len(row["masterCandidates"]) == 1 for row in rows))
            self.assertEqual(summary["unassignedTotals"]["netSalesCents"], 25)
            self.assertEqual(rows[1]["metrics"]["refundCents"], 15)

    def test_ambiguous_candidate_list_is_never_truncated(self):
        facts = [sales(1, "MANY", "P1", 15)]
        sp, se = page(SALES, facts)
        for total, padding in ((299, 0), (120, 300)):
            with self.subTest(total=total, padding=padding):
                masters = [master(i, "MANY", f"S{i:04}"+"X"*padding,
                    f"P{i:04}") for i in range(1, total+1)]
                pages = []
                for start in range(0, total, 100):
                    subset = masters[start:start+100]
                    part, _ = page(MASTER, subset)
                    value = part[0]
                    value["control"] = {"rowCount": total,
                        "typedTotals": {}} if start == 0 else None
                    if start:
                        value["coverage"] = None
                    value["pagination"].update(hasMore=start+100 < total,
                        nextCursor=str(start+100) if start+100 < total else None)
                    pages.append(value)
                verifier = PageReconciler()
                for part in pages:
                    verifier.consume(part, request_cursor=verifier.expected_cursor)
                with self.assertRaises(AnalysisContractError) as failure:
                    with self.opened(sp, se, pages, verifier.result()):
                        pass
                self.assertIsInstance(failure.exception.__cause__, AnalysisContractError)
                self.assertIn("完整主数据候选超过单ERP行固定容量",
                    str(failure.exception.__cause__))

    def test_pair_and_scratch_budget_are_fixed(self):
        sp, se, mp, me = fixture()
        for pair in ("0"*64, None):
            with self.assertRaises(AnalysisContractError):
                with service.assign_facts(SOURCES, PLAN["plan"], pair,
                        sp, mp, se, me):
                    pass
        with self.assertRaises(AnalysisContractError):
            with self.opened(sp, se, mp, me, max_scratch_bytes=1):
                pass

    def test_snapshot_date_coverage_and_page_revision_cannot_mix(self):
        sp, se, mp, me = fixture()
        changed = deepcopy(mp[0]["items"])
        changed[0]["snapshotDate"] = "2026-08-16"
        other_pages, other_expected = page(MASTER, changed)
        with self.assertRaises(AnalysisContractError):
            with self.opened(sp, se, other_pages, other_expected):
                pass
        changed = deepcopy(sp)
        changed[0]["coverage"]["status"] = "dates_present"
        with self.assertRaises(AnalysisContractError):
            with self.opened(changed, se, mp, me):
                pass
        masters = [master(i, "C1", f"S{i:03}", "P1") for i in range(1, 102)]
        first, _ = page(MASTER, masters[:100])
        second, _ = page(MASTER, masters[100:])
        first[0]["control"] = {"rowCount": len(masters), "typedTotals": {}}
        first[0]["pagination"].update(hasMore=True, nextCursor="100")
        second[0]["control"] = None
        second[0]["coverage"] = None
        second[0]["sourceRevision"] = "2:bbbbbbbbbbbb"
        verifier = PageReconciler()
        verifier.consume(first[0]); verifier.consume(second[0], request_cursor="100")
        with self.assertRaises(AnalysisContractError):
            with self.opened(sp, se, [first[0], second[0]], verifier.result()):
                pass
