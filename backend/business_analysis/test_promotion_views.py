"""Pure native-projection fixtures: no Django initialization, DB or provider.

The fixture executes the actual netshop scalar projection AST, then uses the
exact read_page header shape (filters have periods, never invented dates/limit).
Owning service authorization belongs to the later integration slice.
"""
import ast
from copy import deepcopy
from pathlib import Path
import sqlite3
from unittest import TestCase
from unittest.mock import patch

from . import promotion_views as views
from .contracts import (AnalysisContractError, PageReconciler, SCHEMA_VERSION,
    canonical, comparison_periods, coverage, digest, monetary_cents)


def projection():
    code = ast.parse((Path(__file__).parents[1]/"netshop"/"analysis.py").read_text(encoding="utf-8"))
    constants = {"PROMOTION_METRICS", "DIMENSION_FIELDS", "RAW_MONEY_FIELDS", "_EMPTY"}
    nodes = [node for node in code.body if isinstance(node, ast.FunctionDef) and node.name in {"_scalar", "_present", "_project"}
        or isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id in constants for name in node.targets)]
    scope = {"monetary_cents":monetary_cents, "MAX_SAFE_INTEGER":2**53-1,
        "AnalysisContractError":AnalysisContractError, "NetshopApiError":AnalysisContractError}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"native-netshop-projection","exec"),scope)
    return scope


PROJECT = projection()


def fact(plan="P1", unit="U1", match="精确", spend=100, *, clicks=10, impressions=100, gmv=500, direct=None):
    raw = {"计划ID":plan, "单元ID":unit, "匹配类型":match, "计划名称":"同名计划",
        "直接订单金额":direct}
    metrics = {"spendCents":spend, "clicks":clicks, "impressions":impressions,
        "netTransactionAmountCents":gmv, "netOrders":2, "cartQuantity":1}
    return {"raw":raw, "metrics":metrics}


def fixture(rows=None, *, window="current", shop="合成甲店", key=None, first="2026-08-01", last="2026-08-01", limit=2):
    rows = [fact()] if rows is None else rows
    periods = comparison_periods(first,last); period = periods[window]
    spec = {"platform":"京东","shop":shop,"dataset":"promotion","window":window,"periods":periods}
    revision = "7:abcdef012345"
    ref = digest({"schemaVersion":SCHEMA_VERSION,"query":spec,"limit":limit,"revision":revision,"masterBatch":None})
    items=[]
    for i,value in enumerate(rows,1):
        raw={"id":i,"source":"jd_promotion","source_row_hash":digest([i,value]),"last_import_batch_id":"synthetic-batch",
            "platform":"京东","shop_name":shop,"business_date":value.get("date",period["startDate"]),"snapshot_date":"",
            "sku_id":"same-sku","spu_id":"spu","product_code":"code","product_name":"合成商品","category":"合成类目",
            "metrics_json":value["metrics"],"raw_json":value["raw"]}
        for _,(column,names) in PROJECT["PROMOTION_METRICS"].items():
            # Existing writer typed zero can accompany an absent raw metric.
            raw[column]=value["metrics"].get(names[0]) or 0
        items.append(PROJECT["_project"](raw,PROJECT["PROMOTION_METRICS"]))
    totals={key:sum(item["metrics"][key] or 0 for item in items) for key in views.BASE_METRICS}
    pages=[]
    for offset in range(0,max(1,len(items)),limit):
        chunk=items[offset:offset+limit];more=offset+limit<len(items)
        pages.append({"schemaVersion":SCHEMA_VERSION,"sourceRef":ref,"sourceRevision":revision,
            "source":"jd_promotion","sourceDataset":"ad","filters":deepcopy(spec),"monetaryUnit":"CNY_CENT",
            "consistency":"revision_fenced_pages_not_cross_domain_snapshot",
            "metricSemantics":{"reportedGmvCents":"京东为平台总订单归因金额；天猫为源净成交口径；均不是 ERP 净销售或利润。",
                "reportedOrderLines":"平台报告订单口径，不保证跨商品去重。",
                "productDayVisitors":"商品×日累计访客，不能解释为店铺去重 UV。",
                "b2b":"仅 jd_b2b 事实；字段不存在时为 null，不推断商用商品等于 B 端成交。",
                "attributionWindow":"unknown_unless_source_separately_verified"},
            "control":{"rowCount":len(items),"typedTotals":totals,
                "note":"必须读完全部页并核对指标存在性后使用汇总；默认零不证明源字段存在。"} if offset==0 else None,
            "coverage":coverage(period,{i["date"] for i in items}) if offset==0 else None,
            "availableDates":{"firstDate":min((i["date"] for i in items),default=None),
                "lastDate":max((i["date"] for i in items),default=None)} if offset==0 else None,
            "items":chunk,"pageEvidence":{"rowCount":len(chunk),"sha256":digest(chunk)},
            "pagination":{"hasMore":more,"nextCursor":f"synthetic-{offset+limit}" if more else None,"limit":limit}})
    verifier=PageReconciler()
    for page in pages:verifier.consume(page,request_cursor=verifier.expected_cursor)
    expected=verifier.result()
    source={"key":key or "ads-"+window,"domain":"netshop","query":{"platform":"京东","shop":shop,"dataset":"promotion",
        "startDate":first,"endDate":last,"window":window},"sourceRef":ref,"evidenceDigest":expected["evidenceDigest"]}
    return source,pages,expected


def opened(data, **kwargs):
    source,pages,expected=data
    return views.table(source,pages,expected,view=kwargs.pop("view","unit"),**kwargs)


def baseline(data):
    source,pages,expected=data
    return {"baseline_source":source,"baseline_pages":pages,"baseline_expected":expected}


class PromotionViewTests(TestCase):
    def test_actual_native_projection_shape_three_views_and_conservation(self):
        data=fixture([fact(spend=100),fact(match="广泛",spend=200),fact(unit="U2",match=None,spend=300),fact(plan="P2",match=None,spend=400)])
        self.assertEqual(set(data[1][0]["filters"]),{"platform","shop","dataset","window","periods"})
        self.assertNotIn("directGmvCents",data[1][0]["control"]["typedTotals"])
        for view,amounts in (("plan",[600,400]),("unit",[300,300,400]),("unit_match",[100,200,300,400])):
            with opened(data,view=view) as result:
                rows=list(result.scan())
                self.assertEqual(sorted(r["metrics"]["spendCents"]["value"] for r in rows),sorted(amounts))
                self.assertEqual(sum(r["currentRowCount"] for r in rows),4)
                self.assertEqual(result.page()["rows"],rows)
                self.assertFalse(result.header()["authorityVerified"])

    def test_missing_buckets_distinct_parent_and_literal_unknown_not_actionable(self):
        data=fixture([fact(None,None,None),fact("P1",None,None),fact("P2",None,None),fact("null","U1","未知"),fact("001"),fact("1")])
        with opened(data,view="unit_match") as result:
            rows=list(result.scan());self.assertEqual(len(rows),6)
            self.assertEqual(sum(r["identityQualified"] for r in rows),3)
            self.assertEqual(result.header()["identityCoverage"]["current"],{"rowCount":6,"qualifiedRows":3,"unqualifiedRows":3})
        a=fixture([fact(None,None,None)]);b=fixture([fact(None,None,None,spend=50)],window="previous")
        with opened(a,**baseline(b)) as result:
            row=result.page()["rows"][0]
            self.assertEqual(row["comparisons"]["spendCents"]["status"],"unavailable")
            self.assertIsNone(row["comparisons"]["spendCents"]["difference"])

    def test_names_not_keys_and_unicode_exact(self):
        a,b=fact("𠮷001"),fact("𠮷001")
        b["raw"]["计划名称"]="改名"
        with opened(fixture([a,b]),view="plan") as result:
            self.assertEqual(result.header()["total"],1)
            self.assertNotIn("planName",result.page()["rows"][0]["entity"])

    def test_missing_amounts_do_not_invent_indirect_or_ratios(self):
        a,b=fact(direct="1.23"),fact(spend=None)
        with opened(fixture([a,b])) as result:
            row=result.page()["rows"][0]
            self.assertEqual(row["metrics"]["directGmvCents"],{"value":123,"presentRows":1,"missingRows":1})
            self.assertIsNone(row["metrics"]["indirectGmvCents"]["value"])
            self.assertEqual(row["metrics"]["spendCents"]["missingRows"],1)
            self.assertIsNone(row["ratios"]["roas"])
            self.assertIsNone(row["ratios"]["cpcCents"])

    def test_weighted_rates_zero_and_negative_baselines(self):
        a=fixture([fact(spend=400,clicks=3,impressions=10)])
        for amount,status in ((100,"comparable"),(0,"zero_baseline"),(-100,"negative_baseline")):
            with opened(a,**baseline(fixture([fact(spend=amount,clicks=1,impressions=10)],window="previous"))) as result:
                values=result.page()["rows"][0]["comparisons"]
                self.assertEqual(values["spendCents"]["status"],status)
                self.assertEqual(values["spendCents"]["difference"],400-amount)
                self.assertAlmostEqual(values["ctr"]["percentagePoints"],20)
                if amount<=0:self.assertIsNone(values["spendCents"]["changeRate"])
        with opened(fixture([fact(clicks=1,impressions=10),fact(clicks=3,impressions=90)])) as result:
            self.assertEqual(result.page()["rows"][0]["ratios"]["ctr"],.04)

    def test_empty_source_has_no_entity_and_complete_metadata(self):
        with opened(fixture([])) as result:
            self.assertEqual(result.header()["total"],0)
            self.assertEqual(list(result.scan()),[])
            self.assertEqual(result.page()["pagination"]["nextOffset"],None)
            self.assertEqual(result.header()["sourceMetadata"]["coverage"]["status"],"no_records")
        with opened(fixture([]),**baseline(fixture([fact()],window="previous"))) as result:
            self.assertIsNone(result.page()["rows"][0]["metrics"])

    def test_missing_days_sides_and_leap_periods(self):
        a=fixture([fact()],first="2024-02-28",last="2024-03-01")
        b=fixture([fact()],window="yearAgo",first="2024-02-28",last="2024-03-01")
        with opened(a,**baseline(b)) as result:
            h=result.header();self.assertEqual((h["periods"]["current"]["days"],h["periods"]["yearAgo"]["days"]),(3,2))
            self.assertFalse(h["dateCoverageComparable"])
            self.assertEqual(result.page()["rows"][0]["comparisons"]["spendCents"]["status"],"unavailable")
        with opened(fixture([fact("P2")]),**baseline(fixture([fact("P1")],window="previous"))) as result:
            self.assertTrue(all(r["comparisons"]["spendCents"]["status"]=="unavailable" for r in result.scan()))

    def test_source_identity_window_and_platform_rejections_before_iteration(self):
        for change in ({"platform":"天猫"},{"dataset":"sku"}):
            data=fixture();data[0]["query"].update(change)
            with self.assertRaises(AnalysisContractError),opened(data):pass
        for kwargs in (baseline(fixture(window="previous",shop="乙店")),baseline(fixture(window="current")),
                baseline(fixture(window="previous",first="2026-07-01",last="2026-07-01")),{"baseline_pages":[]}):
            with self.assertRaises(AnalysisContractError),opened(fixture(),**kwargs):pass
        with self.assertRaises(AnalysisContractError),opened(fixture(),view="keyword"):pass

    def test_pages_reconcile_tail_no_early_result_and_single_source_scan(self):
        source,pages,expected=fixture([fact(unit=str(i)) for i in range(5)])
        calls=[]
        def stream():
            for i,page in enumerate(pages):calls.append(i);yield page
        with views.table(source,stream(),expected,view="unit") as result:
            self.assertEqual(calls,[0,1,2]);result.page();list(result.scan());result.page()
            self.assertEqual(calls,[0,1,2])
        def broken():
            yield from pages
            raise AnalysisContractError("late source failure")
        with self.assertRaisesRegex(AnalysisContractError,"late"),views.table(source,broken(),expected,view="unit"):self.fail("published early")
        for changed in (pages[:-1],pages+pages[-1:]):
            with self.assertRaises(AnalysisContractError),views.table(source,changed,expected,view="unit"):pass

    def test_page_and_expected_mutations_fail_closed(self):
        changes=[lambda p:p[0].update(sourceRevision="changed"),lambda p:p[1]["filters"].update(shop="other"),
            lambda p:p[0]["items"][0]["dimensions"].update(planId=1),
            lambda p:p[0]["items"][0].update(shopName="other"),
            lambda p:p[0]["items"][0].update(planId="top-level-shadow"),
            lambda p:p[0]["items"][0]["metrics"].update(spendCents=True),
            lambda p:p[0]["pagination"].update(hasMore=1),lambda p:p[0]["filters"].update(startDate="2026-08-01"),
            lambda p:p[0]["coverage"].update(status="no_records")]
        for change in changes:
            source,pages,proof=fixture([fact(),fact(),fact()]);change(pages)
            with self.subTest(change=change),self.assertRaises(AnalysisContractError),views.table(source,pages,proof,view="unit"):pass
        source,pages,proof=fixture();proof["metrics"]["spendCents"]["value"]+=1
        with self.assertRaises(AnalysisContractError),views.table(source,pages,proof,view="unit"):pass

    def test_freeze_before_first_next_and_closed_result_and_generator(self):
        source,pages,expected=fixture();original=deepcopy((source,pages,expected))
        def alias_change():
            source["query"]["shop"]="tamper";expected["rowCount"]=999
            yield from pages
        with views.table(source,alias_change(),expected,view="plan") as result:
            self.assertEqual(result.header()["source"]["query"]["shop"],"合成甲店")
            result.header()["source"]["query"]["shop"]="not persisted"
            scan=result.scan()
        for call in (result.header,result.page,lambda:next(scan)):
            with self.assertRaises(AnalysisContractError):call()
        with opened(original) as control:self.assertEqual(control.header()["source"]["query"]["shop"],"合成甲店")

    def test_capacity_groups_union_rows_pages_disk_strict_numbers(self):
        data=fixture([fact(unit=str(i)) for i in range(3)])
        with opened(data,limits={"maxGroups":3,"maxSourceRows":3,"maxPages":2}) as result:
            self.assertEqual(result.header()["total"],3)
        for limits in ({"maxGroups":2},{"maxSourceRows":2},{"maxPages":1},{"maxScratchBytes":1},{"maxResponseBytes":1}):
            with self.subTest(limits=limits),self.assertRaises(AnalysisContractError),opened(data,limits=limits):pass
        with self.assertRaises(AnalysisContractError),opened(fixture([fact("A"),fact("B")]),
                **baseline(fixture([fact("C"),fact("D")],window="previous")),limits={"maxGroups":3}):pass
        for limits in ({"maxGroups":True},{"maxGroups":0},{"maxGroups":250001},{"unknown":1}):
            with self.assertRaises(AnalysisContractError),opened(data,limits=limits):pass

    def test_disk_error_and_consumer_context_failure_never_leave_live_table(self):
        with patch.object(views.PartitionedGroups,"consume",side_effect=sqlite3.OperationalError("synthetic disk full")):
            with self.assertRaises(AnalysisContractError),opened(fixture()):self.fail("must not yield")
        with self.assertRaisesRegex(RuntimeError,"consumer"):
            with opened(fixture()) as result:
                raise RuntimeError("consumer aborted")
        with self.assertRaises(AnalysisContractError):result.page()

    def test_partial_scans_close_before_temporary_directory_cleanup(self):
        with opened(fixture([fact(unit=str(i)) for i in range(10)])) as result:
            directory = Path(result._store.directory.name)
            scans = [result.scan(), result.scan()]
            for scan in scans: next(scan)
            self.assertTrue(directory.exists())
            self.assertEqual(len(result._scans), 2)
        self.assertFalse(directory.exists())
        self.assertEqual(len(result._scans), 0)
        for scan in scans:
            with self.assertRaisesRegex(AnalysisContractError, "生命周期"): next(scan)

    def test_partial_scan_consumer_exception_and_explicit_close_release_cursor(self):
        with self.assertRaisesRegex(RuntimeError, "consumer"):
            with opened(fixture([fact(unit=str(i)) for i in range(10)])) as result:
                directory = Path(result._store.directory.name)
                closed = result.scan(); next(closed); closed.close()
                self.assertEqual(len(result._scans), 0)
                suspended = result.scan(); next(suspended)
                raise RuntimeError("consumer aborted mid-scan")
        self.assertFalse(directory.exists())
        with self.assertRaisesRegex(AnalysisContractError, "生命周期"): next(suspended)

    def test_identifier_and_both_period_bindings_differ_and_complete_year_ago(self):
        a=fixture();previous=fixture(window="previous");year=fixture(window="yearAgo")
        ids=[]
        for view in views.VIEWS:
            for before in (previous,year):
                with opened(a,view=view,**baseline(before)) as result:
                    ids.append(result.page()["rows"][0]["id"])
                    self.assertTrue(result.header()["dateCoverageComparable"])
        self.assertEqual(len(set(ids)),6)
        with opened(fixture(shop="乙店")) as result:
            self.assertEqual(result.page()["rows"][0]["entity"]["shopName"],"乙店")

    def test_source_copy_mutation_and_recomputed_page_lies_rejected(self):
        # Rehashing a forged row is insufficient: the sealed proof still binds it.
        source,pages,expected=fixture()
        pages[0]["items"][0]["metrics"]["spendCents"]=101
        pages[0]["control"]["typedTotals"]["spendCents"]=101
        pages[0]["pageEvidence"]["sha256"]=digest(pages[0]["items"])
        with self.assertRaises(AnalysisContractError),views.table(source,pages,expected,view="unit"):pass
        data=fixture()
        data[1][0]["coverage"]=coverage(comparison_periods("2026-08-01","2026-08-01")["current"],[])
        with self.assertRaises(AnalysisContractError),opened(data):pass

    def test_single_complete_row_cannot_be_silently_omitted_for_bytes(self):
        data=fixture([fact("中"*240,"字"*240,"精"*240)])
        with opened(data,view="unit_match") as result:
            empty_size=len(canonical(result.page(1)).encode())
        with opened(data,view="unit_match",limits={"maxResponseBytes":empty_size+10}) as result:
            with self.assertRaises(AnalysisContractError):result.page()
            self.assertEqual(len(list(result.scan())),1)

    def test_utf8_paging_complete_prefix_hash_and_no_missing_rows(self):
        data=fixture([fact("中"*180+str(i),"字"*180,"精"*180) for i in range(23)],limit=50)
        with opened(data,view="unit_match",limits={"maxResponseBytes":15000}) as result:
            rows=[];offset=0
            while True:
                page=result.page(offset)
                self.assertLessEqual(len(canonical(page).encode()),15000)
                self.assertLess(len(page["rows"]),20)
                self.assertEqual(page["pageDigest"],digest({k:v for k,v in page.items() if k!="pageDigest"}))
                rows.extend(page["rows"]);offset=page["pagination"]["nextOffset"]
                if offset is None:break
            self.assertEqual(rows,list(result.scan()))
            for value in (True,-1,1.0,"0",24):
                with self.assertRaises(AnalysisContractError):result.page(value)

    def test_deep_untrusted_json_and_old_view_contract_unchanged(self):
        from .results import VIEWS
        self.assertEqual(set(VIEWS),{"shop","category","spu","sku","keyword","searchTerm","daily","brand"})
        data=fixture();deep={};deep["cycle"]=deep;data[0]["query"]=deep
        with self.assertRaises(AnalysisContractError),opened(data):pass
        for invalid in (float("inf"),"\ud800",object()):
            data=fixture();data[1][0]["unexpected"]=invalid
            with self.assertRaises(AnalysisContractError),opened(data):pass
