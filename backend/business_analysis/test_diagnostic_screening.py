"""Pure scanner contracts; no Django, PostgreSQL, files or model calls."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
from unittest import TestCase

from . import diagnostic_screening as screening
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, PageReconciler, comparison_periods, coverage, digest
from .results import build_table


def binding():
    return {"reportId":"report-synthetic", "evidenceRunId":"evidence-synthetic", "evidenceVersion":5,
        "evidencePlanDigest":digest("plan"), "catalogDigest":digest("catalog"), "sealedDigest":digest("seal")}


def source(key="sales", *, domain="sales", window="current", dataset="promotion"):
    query = {"platform":"京东", "shop":"合成店", "startDate":"2026-09-01", "endDate":"2026-09-02", "window":window}
    query["channel" if domain == "sales" else "dataset"] = "合成渠道" if domain == "sales" else dataset
    return {"key":key, "domain":domain, "query":query, "sourceRef":digest([key,"source"]), "evidenceDigest":digest([key,"facts"])}


def descriptor(*, key="sales", family="erp", baseline=False, mapped=False, dimension=None, rules=None):
    domain = "sales" if family == "erp" else "netshop"
    dataset = "promotion" if family == "promotion" else "b2b"
    current = source(key, domain=domain, dataset=dataset)
    before = source(key+"-previous", domain=domain, dataset=dataset, window="previous") if baseline else None
    value = {"schemaVersion":screening.DESCRIPTOR_SCHEMA, "mode":"mapped" if mapped else "native",
        "dimension":dimension or ("sku" if mapped or family != "erp" else "shop"), "source":current,
        "baseline":before, "mapping":None,
        "ruleIds":rules or [rule for rule, spec in screening.RULES.items() if spec[0] == family]}
    if mapped:
        master = source(key+"-master", domain="netshop", dataset="master")
        algorithm = "exact-product-partition-v1"
        value["mapping"] = {"planDigest":digest(["mapping",key]), "algorithmVersion":algorithm,
            "pairKey":digest([algorithm,current["key"],master["key"]]),
            "baselinePairKey":digest([algorithm,before["key"],master["key"]]) if before else None, "master":master}
    return value


def metadata(src, dates=True):
    query = src["query"]
    periods = comparison_periods(query["startDate"], query["endDate"])
    filters = dict(query) if src["domain"] == "sales" else {k:v for k,v in query.items() if k not in {"startDate","endDate"}}
    filters["periods"] = periods
    if src["domain"] == "sales": name, dataset = "erp_sales", None
    else: name, dataset = screening._NETSHOP[(query["platform"],query["dataset"])]
    period=periods[query["window"]]
    actual=[(date.fromisoformat(period["startDate"])+timedelta(days=i)).isoformat() for i in range(period["days"] if dates else 1)]
    return {"source":name, "sourceDataset":dataset, "filters":filters, "coverage":coverage(period,actual)}


def header(desc, count, *, dates=True):
    before = desc["baseline"]
    result = {"schemaVersion":"business-result-table-v1", "dimension":desc["dimension"], "total":count,
        "source":{k:desc["source"][k] for k in ("sourceRef","evidenceDigest")},
        "baselineSource":{k:before[k] for k in ("sourceRef","evidenceDigest")} if before else None,
        "sourceMetadata":metadata(desc["source"],dates), "baselineMetadata":metadata(before,dates) if before else None,
        "comparisonWindow":before["query"]["window"] if before else None,
        "dateCoverageComparable":bool(before and dates), "rows":[]}
    if desc["mode"] == "mapped":
        for key in ("sourceMetadata", "baselineMetadata"):
            if result[key] is not None:
                result[key] = {"coverage":result[key]["coverage"], "sourceRevision":"synthetic-1"}
        def mapped_binding(src):
            return {"schemaVersion":"business-product-mapping-binding-v1",
                **{k:v for k,v in binding().items() if k != "reportId"}, "algorithmVersion":"exact-product-partition-v1",
                **{role:{"sourceKey":entry["key"], "sourceRef":entry["sourceRef"], "queryDigest":digest(entry["query"])}
                    for role,entry in (("sales",src),("master",desc["mapping"]["master"]))}}
        result.update(schemaVersion="business-mapped-result-table-v1", algorithmVersion="business-mapped-results-v1",
            mappingAlgorithmVersion="exact-product-partition-v1", historicalMapping=False,
            sourceWindow=desc["source"]["query"]["window"],
            binding=mapped_binding(desc["source"]), baselineBinding=mapped_binding(before) if before else None,
            bindingDigest=digest(["table",desc]))
    return result


def row(desc, index, values=None, before=None, *, status="matched", missing=False):
    values = {"refundCents":10,"positiveSalesCents":100,"grossProfitCents":-20,"feeCents":30,"netSalesCents":90} if values is None else values
    entity = {"platform":"京东", "shopName":"合成店"}
    for key in screening.VIEWS[desc["dimension"]]:
        if key != "shopName": entity[key] = None if missing else f"entity-{index:06d}"
    if desc["mode"] == "mapped": entity["mappingStatus"] = status
    def cells(items): return {key:{"value":value,"presentRows":0 if value is None else 1,"missingRows":1 if value is None else 0} for key,value in items.items()}
    return {"id":digest([desc["source"]["key"],index]), "rowIndex":index, "entity":entity,
        "currentRowCount":1, "baselineRowCount":1 if before is not None else None,
        "metrics":cells(values), "baselineMetrics":cells(before) if before is not None else None,
        "dimensionMissing":missing or desc["mode"] == "mapped" and status != "matched",
        "ratios":{}, "comparisons":{}}


def run(desc, rows, *, head=None, limits=None, events=None):
    @contextmanager
    def opened(_):
        if events is not None: events.append("enter")
        try: yield (head if head is not None else header(desc,len(rows)), iter(rows))
        finally:
            if events is not None: events.append("exit")
    return screening.prepare(binding(), [desc], opened, limits=limits)


def partition(result, name):
    return next(item for item in result["partitions"] if item["ruleId"] == name)


class DiagnosticScreeningTests(TestCase):
    def test_last_row_major_refund_is_scanned_and_top_candidate_not_full_read_claim(self):
        desc = descriptor(mapped=True, rules=["erp_refund_present"])
        rows = [row(desc,i,{"refundCents":i+1}) for i in range(41)]
        events = []
        result = run(desc,rows,events=events,limits={"candidatesPerPartition":3})
        p = result["partitions"][0]
        self.assertEqual((p["scannedRows"],p["eligibleRows"],p["matchedRows"],p["retainedRows"],p["omittedRows"]),(41,41,41,3,38))
        self.assertEqual([c["reference"]["rowIndex"] for c in p["candidates"]],[40,39,38])
        self.assertEqual(events,["enter","exit"])
        self.assertEqual(result["status"],"prepared_unpublished")
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["coverage"]["sourceAuthorityVerified"])
        self.assertEqual(result["resultDigest"],digest({k:v for k,v in result.items() if k != "resultDigest"}))

    def test_all_seven_integer_rules_and_zero_negative_baselines_do_not_need_growth_rates(self):
        cases = [
            (descriptor(family="promotion",baseline=True), {"spendCents":10,"reportedGmvCents":-2}, {"spendCents":0,"reportedGmvCents":0}, ["promotion_spend_up_gmv_down"]),
            (descriptor(family="promotion"), {"spendCents":20,"reportedGmvCents":0}, None, ["promotion_spend_without_reported_gmv"]),
            (descriptor(baseline=True), {"refundCents":20,"positiveSalesCents":0,"grossProfitCents":-10,"feeCents":1,"netSalesCents":-2},
                {"refundCents":0,"positiveSalesCents":0,"grossProfitCents":1,"feeCents":-1,"netSalesCents":-1},
                ["erp_refund_present","erp_refund_up_sales_not_up","erp_gross_profit_negative","erp_fee_up_net_sales_down"]),
            (descriptor(family="product",baseline=True), {"productDayVisitors":2,"paymentCents":0}, {"productDayVisitors":1,"paymentCents":100}, ["product_traffic_up_payment_down"]),
        ]
        for desc, values, previous, matches in cases:
            result = run(desc,[row(desc,0,values,previous)])
            self.assertEqual([p["ruleId"] for p in result["partitions"] if p["matchedRows"]], sorted(matches))
            for p in result["partitions"]:
                for c in p["candidates"]: self.assertNotIn("changeRate", c)

    def test_complete_real_native_result_engine_with_actual_netshop_filters(self):
        desc = descriptor(family="promotion",rules=["promotion_spend_without_reported_gmv"])
        src = desc["source"]
        periods = comparison_periods(src["query"]["startDate"],src["query"]["endDate"])
        items = [{"rowId":str(i+1),"platform":"京东","shopName":"合成店","skuId":str(i),
            "metrics":{"spendCents":i+10,"reportedGmvCents":0}} for i in range(25)]
        page = {"schemaVersion":"business-analysis-v1", **metadata(src),"sourceRef":src["sourceRef"],"items":items,
            "coverage":coverage(periods["current"],["2026-09-01","2026-09-02"]),
            "control":{"rowCount":25,"typedTotals":{"spendCents":sum(i+10 for i in range(25)),"reportedGmvCents":0}},
            "pageEvidence":{"rowCount":25,"sha256":digest(items)},"pagination":{"hasMore":False,"nextCursor":None}}
        verifier = PageReconciler(); verifier.consume(page); proof = verifier.result()
        desc["source"]["evidenceDigest"] = proof["evidenceDigest"]
        table = build_table([page],"sku",proof)
        rows = table.pop("rows")
        result = run(desc,rows,head=table)
        self.assertEqual(result["partitions"][0]["matchedRows"],25)
        self.assertEqual(result["coverage"]["rowVisits"],25)

    def test_tail_error_exit_error_and_suppressed_error_never_return_success(self):
        desc = descriptor(rules=["erp_refund_present"])
        for mode in ("tail","exit","suppressed"):
            events=[]
            def rows():
                yield row(desc,0)
                if mode != "exit": raise AnalysisContractError("tail corruption")
            @contextmanager
            def opened(_):
                try:
                    yield header(desc,1),rows()
                    if mode == "exit": raise AnalysisContractError("late authority changed")
                except AnalysisContractError:
                    if mode != "suppressed": raise
                finally: events.append("closed")
            with self.subTest(mode=mode), self.assertRaises(AnalysisContractError):
                screening.prepare(binding(),[desc],opened)
            self.assertEqual(events,["closed"])

    def test_stable_multiple_table_order_and_tie_ranking(self):
        a,b = descriptor(key="a",rules=["erp_refund_present"]),descriptor(key="b",rules=["erp_refund_present"])
        @contextmanager
        def opened(desc): yield header(desc,30), (row(desc,i,{"refundCents":7}) for i in range(30))
        one = screening.prepare(binding(),[a,b],opened,limits={"candidatesPerPartition":4})
        two = screening.prepare(binding(),[b,a],opened,limits={"candidatesPerPartition":4})
        self.assertEqual(one,two)
        for p in one["partitions"]:
            key = next(d["source"]["key"] for d in one["plan"]["descriptors"] if digest({k:v for k,v in d.items() if k != "ruleIds"}) == p["tableKey"])
            self.assertEqual([c["reference"]["rowId"] for c in p["candidates"]], sorted(digest([key,i]) for i in range(30))[:4])

    def test_missing_metrics_dates_sides_identity_are_explicit_not_zero(self):
        desc = descriptor(family="promotion",baseline=True,rules=["promotion_spend_up_gmv_down"])
        a,b = {"spendCents":20,"reportedGmvCents":1},{"spendCents":10,"reportedGmvCents":30}
        missing = row(desc,0,{**a,"spendCents":None},b)
        absent = row(desc,1,a)
        unknown = row(desc,2,a,b,missing=True)
        result=run(desc,[missing,absent,unknown])
        self.assertEqual(result["partitions"][0]["ineligibleReasons"],{"metric_incomplete":1,"baseline_row_missing":1,"identity_incomplete":1})
        self.assertEqual(result["partitions"][0]["eligibleRows"],0)
        result=run(desc,[row(desc,0,a,b)],head=header(desc,1,dates=False))
        self.assertEqual(result["partitions"][0]["ineligibleReasons"],{"dates_incomparable":1})

    def test_unresolved_mapping_keeps_single_period_queue_but_never_compares(self):
        desc=descriptor(mapped=True,baseline=True)
        a={"refundCents":30,"positiveSalesCents":20,"grossProfitCents":-2,"feeCents":10,"netSalesCents":0}
        b={"refundCents":2,"positiveSalesCents":50,"grossProfitCents":1,"feeCents":1,"netSalesCents":2}
        rows=[row(desc,i,a,b,status=status) for i,status in enumerate(("matched","ambiguous","unmatched"))]
        result=run(desc,rows)
        self.assertEqual(partition(result,"erp_refund_present")["matchedRows"],3)
        self.assertEqual(partition(result,"erp_refund_up_sales_not_up")["eligibleRows"],1)
        unresolved=[c for c in partition(result,"erp_refund_present")["candidates"] if not c["identityQualified"]]
        self.assertEqual(len(unresolved),2)

    def test_source_range_header_proof_and_fixed_mapping_tampering_rejected(self):
        desc=descriptor(mapped=True,baseline=True)
        for mutate in (
            lambda d:d["baseline"]["query"].update(shop="其他店"),
            lambda d:d["baseline"]["query"].update(channel="其他渠道"),
            lambda d:d["baseline"]["query"].update(startDate="2026-09-02"),
            lambda d:d["mapping"].update(baselinePairKey=digest("other")),
            lambda d:d["mapping"]["master"]["query"].update(shop="其他店"),
        ):
            changed=deepcopy(desc); mutate(changed)
            with self.assertRaises(AnalysisContractError): run(changed,[])
        for path in ("sourceRef","evidenceDigest"):
            h=header(desc,0);h["source"][path]=digest("forged")
            with self.assertRaises(AnalysisContractError): run(desc,[],head=h)
        h=header(desc,0);h["binding"]["evidenceVersion"]+=1
        with self.assertRaises(AnalysisContractError): run(desc,[],head=h)
        h=header(desc,0);h["binding"]["sales"]["queryDigest"]=digest("wrong period")
        with self.assertRaises(AnalysisContractError): run(desc,[],head=h)
        h=header(desc,0);h["sourceWindow"]="previous"
        with self.assertRaises(AnalysisContractError): run(desc,[],head=h)
        h=header(desc,0);h["sourceMetadata"].pop("sourceRevision")
        with self.assertRaises(AnalysisContractError): run(desc,[],head=h)
        desc=descriptor(baseline=True)
        h=header(desc,0);h["sourceMetadata"]["filters"]["periods"]["current"]["endDate"]="2026-09-01"
        with self.assertRaises(AnalysisContractError): run(desc,[],head=h)

    def test_empty_table_explicit_counts_and_unsupported_rules_not_silently_removed(self):
        desc=descriptor(rules=["promotion_spend_without_reported_gmv","erp_refund_present"])
        result=run(desc,[])
        self.assertEqual(result["coverage"]["tableCount"],1)
        self.assertEqual(result["coverage"]["tables"][0]["expectedRows"],0)
        p=partition(result,"promotion_spend_without_reported_gmv")
        self.assertFalse(p["supported"])
        self.assertEqual(p["unavailableReason"],"unsupported_source")
        self.assertEqual((p["matchedRows"],p["retainedRows"],p["omittedRows"]),(0,0,0))

    def test_exact_integer_and_refund_sign_validation(self):
        desc=descriptor(rules=["erp_refund_present"])
        for value in (-1,True,1.0,MAX_SAFE_INTEGER+1):
            with self.subTest(value=value), self.assertRaises(AnalysisContractError):
                run(desc,[row(desc,0,{"refundCents":value})])
        self.assertEqual(run(desc,[row(desc,0,{"refundCents":MAX_SAFE_INTEGER})])["partitions"][0]["matchedRows"],1)
        desc=descriptor(baseline=True,rules=["erp_fee_up_net_sales_down"])
        with self.assertRaises(AnalysisContractError):
            run(desc,[row(desc,0,{"feeCents":MAX_SAFE_INTEGER,"netSalesCents":0},{"feeCents":-1,"netSalesCents":1})])

    def test_total_sequence_duplicate_and_mutation_isolation(self):
        desc=descriptor(rules=["erp_refund_present"])
        rows=[row(desc,0),row(desc,1)]
        for bad in ([{**rows[0],"rowIndex":True}], [{**rows[0],"rowIndex":1}], [rows[0],{**rows[1],"id":rows[0]["id"]}]):
            with self.assertRaises(AnalysisContractError): run(desc,bad)
        for total in (1,3):
            with self.assertRaises(AnalysisContractError): run(desc,rows,head=header(desc,total))
        original=deepcopy(desc)
        @contextmanager
        def opened(d):
            h=header(d,1); item=row(d,0)
            d["source"]["query"]["shop"]="mutated opener input"
            yield h,[item]
        result=screening.prepare(binding(),[desc],opened)
        self.assertEqual(desc,original)
        self.assertEqual(result["plan"]["descriptors"][0],original)

    def test_capacity_and_nested_invalid_values_fail_without_partial_results(self):
        desc=descriptor(rules=["erp_refund_present"])
        rows=[row(desc,0),row(desc,1)]
        for limits in ({"maxRowVisits":1},{"maxRowsPerTable":1},{"maxRowIdBytes":64},
                       {"maxCandidateBytes":1},{"maxCoverageBytes":1},{"maxDescriptorBytes":1},
                       {"maxRowBytes":1},{"maxHeaderBytes":1}, {"candidatesPerPartition":True},
                       {"maxTables":0},{"maxTables":257},{"unknown":1}):
            with self.subTest(limits=limits),self.assertRaises(AnalysisContractError): run(desc,rows,limits=limits)
        bad=deepcopy(desc);bad["source"]["query"]["shop"]="汉"*130000
        with self.assertRaises(AnalysisContractError):run(bad,[])
        bad=deepcopy(desc);bad["source"]={"nested":{}}
        for _ in range(18):bad["source"]={"nested":bad["source"]}
        with self.assertRaises(AnalysisContractError):run(bad,[])

    def test_duplicate_descriptor_and_same_key_rebinding_rejected_before_open(self):
        a=descriptor(rules=["erp_refund_present"])
        b=deepcopy(a);b["dimension"]="sku";b["source"]["sourceRef"]=digest("other")
        def never(_):self.fail("opener must not run")
        for descriptors in ([a,a],[a,b]):
            with self.assertRaises(AnalysisContractError):screening.prepare(binding(),descriptors,never)

    def test_observed_single_period_dates_and_leap_comparison_days_remain_visible(self):
        desc=descriptor(family="promotion",rules=["promotion_spend_without_reported_gmv"])
        result=run(desc,[row(desc,0,{"spendCents":10,"reportedGmvCents":0})],head=header(desc,1,dates=False))
        table=result["coverage"]["tables"][0]
        self.assertEqual(table["sourceCoverage"]["status"],"missing_dates")
        self.assertEqual(table["sourceCoverage"]["missingDates"],["2026-09-02"])
        self.assertEqual(result["partitions"][0]["matchedRows"],1)
        desc=descriptor(family="promotion",baseline=True,rules=["promotion_spend_up_gmv_down"])
        for source in (desc["source"],desc["baseline"]):source["query"].update(startDate="2024-02-28",endDate="2024-03-01")
        desc["baseline"]["query"]["window"]="yearAgo"
        result=run(desc,[row(desc,0,{"spendCents":20,"reportedGmvCents":5},{"spendCents":10,"reportedGmvCents":6})])
        table=result["coverage"]["tables"][0]
        self.assertEqual((table["sourcePeriod"]["days"],table["baselinePeriod"]["days"]),(3,2))
        self.assertEqual(result["partitions"][0]["candidates"][0]["differences"],{"spendCents":10,"reportedGmvCents":-1})

    def test_real_mapped_result_engine_preserves_refund_and_nonduplicated_sku(self):
        from . import mapped_results, test_mapped_results as fixtures
        _,spec=fixtures.fixture()
        def entry(role):
            src=spec[role+"_source"];proof=spec[role+"_info"]["expected"]
            return {**src,**{key:proof[key] for key in ("sourceRef","evidenceDigest")}}
        src,master=entry("sales"),entry("master")
        mapped_source=mapped_results.MappingSource(**spec)
        desc={"schemaVersion":screening.DESCRIPTOR_SCHEMA,"mode":"mapped","dimension":"sku",
            "source":src,"baseline":None,"mapping":{"planDigest":digest("fixed-mapping"),
                "algorithmVersion":"exact-product-partition-v1","master":master,
                "pairKey":digest(["exact-product-partition-v1",src["key"],master["key"]]),"baselinePairKey":None},
            "ruleIds":["erp_refund_present"]}
        evidence_binding={"reportId":"real-derived-synthetic",**{key:value for key,value in spec["binding"].items()
            if key in binding() and key != "reportId"}}
        @contextmanager
        def opened(_):
            with mapped_results.mapped_table(mapped_source,"sku") as table:
                yield table.header(),table.scan()
        result=screening.prepare(evidence_binding,[desc],opened)
        p=result["partitions"][0]
        self.assertEqual(p["matchedRows"],1)
        self.assertEqual(p["candidates"][0]["entity"]["skuId"],"SKU1")
        self.assertEqual(p["candidates"][0]["current"]["refundCents"],20)

    def test_market_is_explicit_unsupported_and_cannot_be_labelled_as_owning_shop(self):
        src={"key":"market","domain":"market","query":{"platform":"京东","category":"合成类目","scope":"全部",
            "rankingDimension":"SKU","priceBandFilter":"不限","startDate":"2026-09-01","endDate":"2026-09-02","window":"current"},
            "sourceRef":digest("market"),"evidenceDigest":digest("market facts")}
        desc={"schemaVersion":screening.DESCRIPTOR_SCHEMA,"mode":"native","dimension":"sku","source":src,
            "baseline":None,"mapping":None,"ruleIds":["promotion_spend_without_reported_gmv"]}
        periods=comparison_periods("2026-09-01","2026-09-02")
        filters={k:v for k,v in src["query"].items() if k not in {"startDate","endDate"}}
        filters.update(shop="",periods=periods)
        h={"schemaVersion":"business-result-table-v1","dimension":"sku","total":0,"source":{k:src[k] for k in ("sourceRef","evidenceDigest")},
            "baselineSource":None,"sourceMetadata":{"source":"market_daily_top","filters":filters,"coverage":coverage(periods["current"],[])},
            "baselineMetadata":None,"comparisonWindow":None,"dateCoverageComparable":False}
        result=run(desc,[],head=h)
        self.assertFalse(result["partitions"][0]["supported"])
        self.assertEqual(result["coverage"]["tables"][0]["sourceCoverage"]["status"],"no_records")
        h["sourceMetadata"]["filters"]["shop"]="合成店"
        with self.assertRaises(AnalysisContractError):run(desc,[],head=h)

    def test_late_second_table_failure_closes_every_context_without_success(self):
        descriptors=[descriptor(key=key,rules=["erp_refund_present"]) for key in ("a","b")]
        events=[]
        @contextmanager
        def opened(desc):
            events.append("open")
            try:
                yield header(desc,1),[row(desc,0)]
                if events.count("open")==2:raise AnalysisContractError("second source revoked on exit")
            finally:events.append("close")
        with self.assertRaises(AnalysisContractError):screening.prepare(binding(),descriptors,opened)
        self.assertEqual(events,["open","close","open","close"])

    def test_coverage_digest_covers_non_candidate_tail_and_exact_number_types(self):
        desc=descriptor(rules=["erp_refund_present"])
        rows=[row(desc,i,{"refundCents":10 if i==0 else 0}) for i in range(3)]
        first=run(desc,rows)
        rows[-1]["metrics"]["unrelatedFee"]={"value":1,"presentRows":1,"missingRows":0}
        second=run(desc,rows)
        self.assertEqual(first["partitions"],second["partitions"])
        self.assertNotEqual(first["coverage"]["tables"][0]["rowDigest"],second["coverage"]["tables"][0]["rowDigest"])
        self.assertNotEqual(first["resultDigest"],second["resultDigest"])
        h=header(desc,0);h["sourceMetadata"]["filters"]["periods"]["current"]["days"]=2.0
        with self.assertRaises(AnalysisContractError):run(desc,[],head=h)
        desc=descriptor(mapped=True);h=header(desc,0);h["binding"]["evidenceVersion"]=5.0
        with self.assertRaises(AnalysisContractError):run(desc,[],head=h)
