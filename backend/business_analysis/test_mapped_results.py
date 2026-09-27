from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from . import identity_partitioned as identity, mapped_results as mapped
from .contracts import AnalysisContractError, canonical, comparison_periods, coverage, digest
from .test_identity_partitioned import pages, proof


def metrics(net, cost=0, quantity=1):
    return {"netSalesCents":net,"positiveSalesCents":max(net,0),"refundCents":max(-net,0),"costCents":cost,
        "grossProfitCents":net-cost,"reportedGrossProfitCents":net-cost+1,"feeCents":2,"netQuantity":quantity,
        "positiveQuantity":max(quantity,0),"returnQuantity":max(-quantity,0),"netSalesExcludingAccessoriesCents":net}


def fixture(window="current", *, sales=None, masters=None, missing=False, channel="京东", shop="合成店",
            start="2026-08-02", end="2026-08-03", tracker=None, tamper=None, ignore_cap=False, empty_metrics=False, page_size=2):
    sales = sales if sales is not None else [("A",100,60,1),("A",-20,-12,-1),("B",70,40,1),("C",30,20,1),("D",10,6,1),(None,5,2,1)]
    masters = masters if masters is not None else [("A","SKU1","SPU1"),("B","SKU1","SPU2"),("C","SKU2",None),("D","SKU3","SPU3"),("D","SKU4","SPU4")]
    sales_rows=[{"rowId":str(i+1),"platform":"京东","shopName":shop,"onlineSpecCode":code,"productCode":"not-a-fallback",
        "metrics":metrics(net,cost,qty)} for i,(code,net,cost,qty) in enumerate(sales)]
    master_rows=[{"rowId":str(i+1),"platform":"京东","shopName":shop,"skuId":sku,"spuId":spu,
        "dimensions":{"merchantCode":code},"metrics":{}} for i,(code,sku,spu) in enumerate(masters)]
    sales_source={"key":"sales-"+window,"domain":"sales","query":{"platform":"京东","shop":shop,"channel":channel,
        "startDate":start,"endDate":end,"window":window}}
    master_source={"key":"master","domain":"netshop","query":{"platform":"京东","shop":shop,"dataset":"master",
        "startDate":start,"endDate":end,"window":"current"}}
    left=list(pages("sales",0,records=sales_rows,shop=shop,page_size=page_size))
    right=list(pages("master",0,records=master_rows,shop=shop,page_size=page_size))
    if not sales and not empty_metrics: left[0]["control"]["typedTotals"]={k:0 for k in sorted(mapped.METRICS)}
    for blocks,source in ((left,sales_source),(right,master_source)):
        for page in blocks: page["sourceRef"]=digest(source["query"])
    period=comparison_periods(start,end)[window]
    dates=([period["startDate"]] if missing else [period["startDate"],period["endDate"]]) if sales else []
    sales_info={"metadata":{"coverage":coverage(period,dates),"sourceRevision":"sales-1","metricSemantics":{"grossProfit":"netSales-cost"}},
        "expected":proof(left),"pageCount":len(left)}
    master_info={"metadata":{"coverage":{"status":"current_master" if masters else "no_records","historicalMapping":False,
        "batchId":"master-batch" if masters else None,"snapshotDate":"2026-09-01" if masters else None},"sourceRevision":"master-1"},
        "expected":proof(right),"pageCount":len(right)}
    binding={"schemaVersion":"business-product-mapping-binding-v1","evidenceRunId":"evidence-test","evidenceVersion":3,
        "evidencePlanDigest":"1"*64,"catalogDigest":"2"*64,"sealedDigest":"3"*64,"algorithmVersion":identity.ALGORITHM_VERSION,
        "sales":{"sourceKey":sales_source["key"],"queryDigest":digest(sales_source["query"]),"sourceRef":sales_info["expected"]["sourceRef"]},
        "master":{"sourceKey":master_source["key"],"queryDigest":digest(master_source["query"]),"sourceRef":master_info["expected"]["sourceRef"]}}
    @contextmanager
    def opener(*,max_scratch_bytes):
        if tracker is not None:
            tracker.setdefault("events",[]).append("open-"+window)
            tracker["active"]=tracker.get("active",0)+1
            tracker["peak"]=max(tracker.get("peak",0),tracker["active"])
        try:
            with identity.reconcile_products(iter(left),iter(right),sales_expected=sales_info["expected"],master_expected=master_info["expected"],
                    **({} if ignore_cap else {"max_scratch_bytes":max_scratch_bytes})) as result:
                if tamper: tamper(result)
                yield result,deepcopy(binding)
        finally:
            if tracker is not None:
                tracker["active"]-=1;tracker["events"].append("close-"+window)
    descriptor={"binding":binding,"sales_source":sales_source,"master_source":master_source,"sales_info":sales_info,
        "master_info":master_info,"open_mapping":opener}
    return mapped.MappingSource(**descriptor),descriptor


class MappedResultsTests(TestCase):
    def test_sku_reaggregates_across_spus_without_multiplying_rows_or_money(self):
        source,_=fixture()
        with mapped.mapped_table(source,"sku") as table:
            rows=list(table.scan()); header=table.header()
            self.assertEqual(table.page()["rows"],rows)
            self.assertEqual(sum(r["currentRowCount"] for r in rows),6)
            for metric in mapped.METRICS:
                self.assertEqual(sum(r["metrics"][metric]["value"] for r in rows),header["source"]["metrics"][metric]["value"])
            sku=next(r for r in rows if r["entity"].get("skuId")=="SKU1")
            self.assertEqual(sku["currentRowCount"],3)
            self.assertEqual(sku["metrics"]["netSalesCents"]["value"],150)
            self.assertEqual(sku["metrics"]["refundCents"]["value"],20)
            self.assertEqual(sku["metrics"]["costCents"]["value"],88)
            self.assertEqual(sku["metrics"]["grossProfitCents"]["value"],62)
            self.assertFalse(sku["dimensionMissing"])
            self.assertFalse(header["historicalMapping"])
            self.assertEqual(set(sku["metrics"]),mapped.METRICS)

    def test_spu_missing_ambiguous_unmatched_are_separate_and_pagination_is_stable(self):
        source,_=fixture()
        with mapped.mapped_table(source,"spu") as table:
            rows=list(table.scan())
            nulls=[row for row in rows if row["entity"]["spuId"] is None]
            self.assertEqual({r["entity"]["mappingStatus"] for r in nulls},{"matched","ambiguous","unmatched"})
            self.assertTrue(all(r["dimensionMissing"] for r in nulls))
            gathered=[]
            for i in range(table.header()["total"]):
                page=table.page(i,1);gathered.extend(page["rows"])
                self.assertEqual(page["pageDigest"],digest({k:v for k,v in page.items() if k!="pageDigest"}))
            self.assertEqual(gathered,rows)
            self.assertEqual([r["rowIndex"] for r in rows],list(range(len(rows))))

    def test_both_windows_preserve_zero_negative_missing_sides_and_sequence(self):
        masters=[(key,key,"P"+key) for key in "ABCDE"]
        current=[("A",100,50,1),("B",200,100,1),("C",-50,-25,-1),("D",5,2,1)]
        before=[("A",50,25,1),("B",0,0,0),("C",-10,-5,-1),("E",20,10,1)]
        for window in ("previous","yearAgo"):
            tracker={};a,_=fixture(sales=current,masters=masters,tracker=tracker);b,_=fixture(window,sales=before,masters=masters,tracker=tracker)
            with mapped.mapped_table(a,"sku",baseline=b) as table:
                rows={r["entity"]["skuId"]:r for r in table.scan()}
                self.assertEqual(rows["A"]["comparisons"]["netSalesCents"]["changeRate"],1)
                self.assertEqual(rows["B"]["comparisons"]["netSalesCents"]["status"],"zero_baseline")
                self.assertEqual(rows["C"]["comparisons"]["netSalesCents"]["status"],"negative_baseline")
                for key in "BCDE":self.assertIsNone(rows[key]["comparisons"]["netSalesCents"]["changeRate"])
                self.assertIsNone(rows["D"]["baselineMetrics"])
                self.assertIsNone(rows["E"]["metrics"]["netSalesCents"])
                self.assertIsNone(rows["E"]["currentRowCount"])
                self.assertEqual(table.header()["comparisonWindow"],window)
                self.assertEqual(table.stats()["maxConcurrentMappingContexts"],1)
                self.assertLessEqual(table.stats()["combinedScratchHighWaterBytes"],256*1024*1024)
            self.assertEqual(tracker["events"],["open-current","close-current","open-"+window,"close-"+window])
            self.assertEqual((tracker["peak"],tracker["active"]),(1,0))

    def test_missing_dates_do_not_produce_differences_and_empty_sources_are_not_zero_sales(self):
        a,_=fixture();b,_=fixture("previous",missing=True)
        with mapped.mapped_table(a,"sku",baseline=b) as table:
            self.assertFalse(table.header()["dateCoverageComparable"])
            self.assertTrue(all(c["difference"] is None for row in table.scan() for c in row["comparisons"].values()))
        a,_=fixture(sales=[]);b,_=fixture("yearAgo")
        with mapped.mapped_table(a,"sku",baseline=b) as table:
            self.assertTrue(all(r["currentRowCount"] is None for r in table.scan()))
        a,_=fixture(sales=[],masters=[])
        with mapped.mapped_table(a,"spu") as table:
            self.assertEqual(table.header()["total"],0)
            self.assertEqual(table.page()["rows"],[])
        a,_=fixture(sales=[],empty_metrics=True)
        with mapped.mapped_table(a,"sku",baseline=b) as table:
            self.assertEqual(table.header()["source"]["metrics"],{})
            self.assertEqual(table.header()["mappingProofs"][0]["totals"],{})
            self.assertTrue(all(r["currentRowCount"] is None and all(v is None for v in r["metrics"].values()) for r in table.scan()))

    def test_incomplete_identity_retains_amounts_without_growth_or_difference(self):
        a,_=fixture();b,_=fixture("previous")
        with mapped.mapped_table(a,"spu",baseline=b) as table:
            rows=list(table.scan())
            missing=[r for r in rows if r["dimensionMissing"]]
            self.assertEqual(len(missing),3)
            self.assertTrue(all(r["metrics"]["netSalesCents"]["value"] is not None and r["baselineMetrics"]["netSalesCents"]["value"] is not None for r in missing))
            self.assertTrue(all(c["difference"] is None and c["changeRate"] is None and c["status"]=="unavailable" for r in missing for c in r["comparisons"].values()))

    def test_standalone_previous_year_ago_tables_and_comparison_direction(self):
        for window in ("previous","yearAgo"):
            source,_=fixture(window)
            with mapped.mapped_table(source,"sku") as table:
                self.assertEqual(table.header()["sourceWindow"],window)
                self.assertIsNone(table.header()["comparisonWindow"])
                self.assertTrue(all(r["comparisons"]=={} for r in table.scan()))
            baseline,_=fixture()
            with self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(source,"sku",baseline=baseline):pass

    def test_scope_channel_dates_and_master_changes_reject_before_open(self):
        a,_=fixture()
        for kwargs in ({"channel":"other"},{"shop":"other"},{"start":"2026-08-01"},
                       {"masters":[("A","changed","P")]}):
            tracker={}; b,_=fixture("previous",tracker=tracker,**kwargs)
            with self.subTest(kwargs=kwargs),self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(a,"sku",baseline=b):pass
            self.assertEqual(tracker,{})
        b,data=fixture("previous")
        data["binding"]["evidenceVersion"]=4
        b=mapped.MappingSource(**data)
        with self.assertRaises(AnalysisContractError):
            with mapped.mapped_table(a,"sku",baseline=b):pass

    def test_descriptors_frozen_and_query_ref_coverage_algorithm_are_strict(self):
        source,data=fixture()
        data["sales_source"]["query"]["shop"]="mutated"
        self.assertEqual(source.descriptor["salesSource"]["query"]["shop"],"合成店")
        copied=source.descriptor;copied["binding"]["sales"].clear()
        self.assertTrue(source.descriptor["binding"]["sales"])
        mutations=(lambda d:d["binding"].update(evidenceVersion=True),lambda d:d["binding"].update(algorithmVersion="future"),
            lambda d:d["binding"]["sales"].update(queryDigest="0"*64),lambda d:d["binding"]["master"].update(sourceRef="wrong"),
            lambda d:d["sales_info"]["metadata"]["coverage"].update(status="dates_present",missingDates=["2026-08-03"]),
            lambda d:d["master_info"]["metadata"]["coverage"].update(historicalMapping=True))
        for change in mutations:
            _,d=fixture();change(d)
            with self.assertRaises(AnalysisContractError):mapped.MappingSource(**d)

    def test_fake_mapping_objects_and_ignored_disk_cap_rejected(self):
        source,d=fixture()
        @contextmanager
        def fake(**kwargs):yield SimpleNamespace(summary=lambda:{}),d["binding"]
        d["open_mapping"]=fake
        for source in (mapped.MappingSource(**d),fixture(ignore_cap=True)[0]):
            with self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(source,"sku"):pass

    def test_tampered_late_groups_and_rehashed_summary_fail_without_publication(self):
        def changed_row(result):
            original=result.scan
            def scan():
                for row in original():
                    if row["rowIndex"]==2:row["metrics"]["netSalesCents"]+=1
                    yield row
            result.scan=scan
        def changed_summary(result):
            summary=result.summary();summary["rowCount"]+=1
            summary["resultDigest"]=digest({k:v for k,v in summary.items() if k!="resultDigest"})
            result._summary=canonical(summary)
        def missing_tail(result):
            original=result.scan
            result.scan=lambda:(r for r in original() if r["rowIndex"]<2)
        for change in (changed_row,changed_summary,missing_tail):
            source,_=fixture(tamper=change);published=False
            with self.subTest(change=change.__name__),self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(source,"sku"):published=True
            self.assertFalse(published)

    def test_late_opener_failure_and_abandoned_scan_clean_up(self):
        paths=[]
        def directory(**kwargs):
            item=TemporaryDirectory(**kwargs);paths.append(Path(item.name));return item
        a,d=fixture();original=d["open_mapping"]
        @contextmanager
        def late(**kwargs):
            with original(**kwargs) as result:yield result
            raise AnalysisContractError("late permission failure")
        d["open_mapping"]=late
        with patch.object(mapped,"TemporaryDirectory",side_effect=directory),self.assertRaises(AnalysisContractError):
            with mapped.mapped_table(mapped.MappingSource(**d),"sku"):self.fail("must not publish")
        with patch.object(mapped,"TemporaryDirectory",side_effect=directory):
            with mapped.mapped_table(a,"sku") as table:
                scan=table.scan();next(scan)
        self.assertTrue(paths and all(not path.exists() for path in paths))
        for call in (table.header,table.page,table.stats,lambda:next(scan)):
            with self.assertRaises(AnalysisContractError):call()

    def test_capacity_and_exact_pagination(self):
        source,_=fixture()
        for constant,limit in (("MAX_SCRATCH_BYTES",4096),("MAX_GROUPS",1),("MAX_RESPONSE_BYTES",50)):
            with patch.object(mapped,constant,limit),self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(source,"sku"):pass
        with mapped.mapped_table(source,"sku") as table:
            for offset,limit in ((True,20),(0,True),(0.0,20),(-1,20),(250001,20),(0,101),(0,0)):
                with self.assertRaises(AnalysisContractError):table.page(offset,limit)
        for dimension in ("category","daily",None,[]):
            with self.assertRaises(AnalysisContractError):
                with mapped.mapped_table(source,dimension):pass

    def test_5001_comparison_measures_serial_combined_disk_and_conservation(self):
        count=5001;tracker={}
        masters=[(str(i),f"SKU{i:05}",f"SPU{i//2:05}") for i in range(count)]
        current=[(str(i),i-2500,i//2,1) for i in range(count)]
        previous=[(str(i),i+1,i//3,1) for i in range(count)]
        a,_=fixture(sales=current,masters=masters,tracker=tracker,page_size=100)
        b,_=fixture("previous",sales=previous,masters=masters,tracker=tracker,page_size=100)
        with mapped.mapped_table(a,"spu",baseline=b) as table:
            stats=table.stats();row_count=0;sums={k:0 for k in mapped.METRICS}
            for row in table.scan():
                row_count+=row["currentRowCount"]
                for key in mapped.METRICS:sums[key]+=row["metrics"][key]["value"]
            self.assertEqual(row_count,count)
            self.assertEqual(sums,table.header()["mappingProofs"][0]["totals"])
            self.assertEqual(table.header()["total"],2501)
            self.assertEqual(table.page(2500)["rows"][0]["rowIndex"],2500)
            self.assertGreater(stats["combinedScratchHighWaterBytes"],stats["derivedScratchBytes"])
            self.assertLessEqual(stats["derivedScratchBytes"],128*1024*1024)
            self.assertLessEqual(stats["mappingScratchPeakBytes"],128*1024*1024)
            self.assertLessEqual(stats["combinedScratchHighWaterBytes"],256*1024*1024)
            print(canonical({"case":"mapped-results-5001-comparison","rowsPerSalesSource":count,**stats}))
        self.assertEqual(tracker["peak"],1)

    def test_wide_rows_are_complete_prefix_and_wide_singleton_never_published(self):
        masters=[(str(i),"中"*2000+str(i),"P") for i in range(8)]
        sales=[(str(i),10,4,1) for i in range(8)]
        a,_=fixture(sales=sales,masters=masters)
        with mapped.mapped_table(a,"sku") as table:
            first=table.page()
            self.assertGreater(len(first["rows"]),0)
            self.assertLess(len(first["rows"]),8)
            self.assertLessEqual(len(canonical(first).encode()),38000)
            self.assertEqual(first["pageDigest"],digest({k:v for k,v in first.items() if k!="pageDigest"}))
            offset=first["pagination"]["nextOffset"];rows=first["rows"][:]
            while offset is not None:
                page=table.page(offset);rows.extend(page["rows"]);offset=page["pagination"]["nextOffset"]
            self.assertEqual(rows,list(table.scan()))
        a,_=fixture(sales=[("X",10,4,1)],masters=[("X","中"*10000,"P")])
        b,_=fixture("previous",sales=[("X",5,2,1)],masters=[("X","中"*10000,"P")])
        with self.assertRaisesRegex(AnalysisContractError,"单个完整映射分析行"):
            with mapped.mapped_table(a,"sku",baseline=b):self.fail("must reject before publication")

    def test_passive_metadata_float_is_allowed_but_metric_float_is_not(self):
        _,data=fixture();data["sales_info"]["metadata"]["samplePrecision"]=0.5
        source=mapped.MappingSource(**data)
        with mapped.mapped_table(source,"sku") as table:
            self.assertEqual(table.header()["sourceMetadata"]["samplePrecision"],0.5)
        _,data=fixture();data["sales_info"]["metadata"]["samplePrecision"]=float("nan")
        with self.assertRaises(AnalysisContractError):mapped.MappingSource(**data)
        def fake_float(result):
            original=result.scan
            def scan():
                for row in original():
                    row["metrics"]["netSalesCents"]=float(row["metrics"]["netSalesCents"])
                    yield row
            result.scan=scan
        source,_=fixture(tamper=fake_float)
        with self.assertRaises(AnalysisContractError):
            with mapped.mapped_table(source,"sku"):pass
