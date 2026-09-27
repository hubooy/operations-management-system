"""Fixed selection/capacity tests without Django or fact/model execution."""
from copy import deepcopy
from contextlib import contextmanager
from unittest import TestCase

from . import diagnostic_screening, mapping_plan, screening_plan
from .contracts import AnalysisContractError, comparison_periods, digest
from .evidence_v2 import normalize_sources


def request(dimensions=None, windows=None):
    return {"schemaVersion":"business-analysis-request-v1","question":"核查完整经营数据",
        "requestedDimensions":dimensions or ["shop","category","spu","sku","keyword"],
        "requestedWindows":windows or ["current","previous","yearAgo"]}


def source(key, *, dataset="promotion", domain="netshop", window="current", shop="合成店"):
    query={"platform":"京东","shop":shop,"startDate":"2026-09-01","endDate":"2026-09-02","window":window}
    query["channel" if domain == "sales" else "dataset"]="合成渠道" if domain == "sales" else dataset
    return {"key":key,"domain":domain,"query":query}


def info(entry, rows=1):
    if entry["domain"]=="sales": keys=["netSalesCents","positiveSalesCents","refundCents","grossProfitCents","feeCents"]
    elif entry["domain"]=="market": keys=["sampleGmvLowerCents","sampleGmvUpperCents"]
    elif entry["query"]["dataset"]=="master": keys=[]
    elif entry["query"]["dataset"]=="promotion": keys=["spendCents","reportedGmvCents"]
    else: keys=["paymentCents","productDayVisitors"]
    query=entry["query"]
    return {"metadata":{"filters":{**query,"periods":comparison_periods(query["startDate"],query["endDate"])},
            "coverage":{"status":"dates_present" if rows else "no_records"}},
        "expected":{"sourceRef":digest([entry,"ref"]),"evidenceDigest":digest([entry,"facts"]),"rowCount":rows,
            "reconciled":True,"metrics":{key:{"value":1 if rows else None,"presentRows":rows,"missingRows":0} for key in keys}},
        "pageCount":max(1,(rows+99)//100)}


def fixture(*, with_product=False, rows=1):
    entries=[]
    for window in ("current","previous","yearAgo"):
        entries.extend([source("promotion-"+window,window=window),source("sales-"+window,domain="sales",window=window)])
        if with_product:entries.append(source("sku-"+window,dataset="sku",window=window))
    entries.append(source("master",dataset="master"))
    infos={entry["key"]:info(entry,rows) for entry in entries}
    choices=[{"salesKey":"sales-"+window,"masterKey":"master"} for window in ("current","previous","yearAgo")]
    plan=mapping_plan.build(entries,choices)["plan"]
    return entries,infos,plan


def build(entries, infos, plan=None, *, req=None, limits=None):
    return screening_plan.build(req or request(),entries,infos,mapping_plan=plan,limits=limits)


class ScreeningPlanTests(TestCase):
    def test_seven_sources_fifty_five_partitions_complete_without_authority(self):
        entries,infos,plan=fixture()
        value=build(entries,infos,plan)
        self.assertTrue(value["canScreen"])
        self.assertFalse(value["authorityVerified"])
        self.assertEqual(value["capacity"]["partitionCount"],55)
        self.assertEqual(value["capacity"]["tableCount"],40)
        self.assertEqual(value["capacity"]["maxPartitions"],64)
        self.assertFalse(value["requestedCoveragePlanned"])  # ERP category/keyword remain unavailable.
        self.assertEqual(len(value["descriptors"]),40)
        for descriptor in value["descriptors"]:
            self.assertEqual(diagnostic_screening._descriptor(descriptor),descriptor)
            self.assertEqual(bool(descriptor["baseline"]),all(diagnostic_screening.RULES[rule][2] for rule in descriptor["ruleIds"]))
        self.assertEqual(value["planDigest"],digest({key:item for key,item in value.items() if key!="planDigest"}))
        self.assertEqual(value["catalogDigest"],digest({"schemaVersion":"business-evidence-directory-v2","entries":normalize_sources(entries)}))

    def test_ten_sources_sixty_five_partitions_refused_without_truncating(self):
        entries,infos,plan=fixture(with_product=True)
        value=build(entries,infos,plan)
        self.assertFalse(value["canScreen"])
        self.assertEqual(value["capacity"]["partitionCount"],65)
        self.assertEqual(value["capacity"]["tableCount"],50)
        self.assertEqual(len(value["descriptors"]),50)
        self.assertIn({"reason":"partition_limit","actual":65,"limit":64},value["admissionFailures"])
        selected={desc["source"]["key"] for desc in value["descriptors"]}
        self.assertTrue({"promotion-previous","promotion-yearAgo","sales-previous","sales-yearAgo"}<=selected)
        self.assertEqual(value["analysisRequest"]["requestedDimensions"],request()["requestedDimensions"])

    def test_empty_facts_keep_descriptors_and_zero_bound_not_no_sources(self):
        entries,infos,plan=fixture(rows=0)
        value=build(entries,infos,plan)
        self.assertTrue(value["canScreen"])
        self.assertEqual(value["capacity"]["rowVisitsUpperBound"],0)
        self.assertEqual(value["capacity"]["tableCount"],40)
        self.assertTrue(all(proof["rowCount"]==0 for proof in value["sourceProofs"]))
        with self.assertRaises(AnalysisContractError):build([],{},None)

    def test_missing_mapping_not_guessed_and_source_information_still_bound(self):
        entries,infos,_=fixture()
        value=build(entries,infos)
        self.assertTrue(value["canScreen"])
        self.assertEqual(value["capacity"]["partitionCount"],35)
        self.assertTrue(all(desc["mode"]=="native" for desc in value["descriptors"]))
        missing=[item for item in value["requestedCoverage"] if item["reason"]=="missing_mapping_pair"]
        self.assertEqual(len(missing),10)  # Two dimensions, three single windows and two comparisons.
        self.assertIsNone(value["mappingPlanDigest"])
        self.assertEqual(value["sourceCount"],7)

    def test_only_selected_pairs_no_inferred_baseline_mapping(self):
        entries,infos,_=fixture()
        plan=mapping_plan.build(entries,[{"salesKey":"sales-current","masterKey":"master"}])["plan"]
        value=build(entries,infos,plan)
        self.assertEqual(value["capacity"]["partitionCount"],39)
        mapped=[desc for desc in value["descriptors"] if desc["mode"]=="mapped"]
        self.assertEqual(len(mapped),2)
        self.assertTrue(all(desc["baseline"] is None for desc in mapped))
        self.assertTrue(any(item["kind"]=="comparison" and item["reason"]=="missing_mapping_pair" for item in value["requestedCoverage"]))

    def test_missing_baseline_source_and_missing_current_are_explicit(self):
        entries=[source("ad-current"),source("ad-previous",window="previous")]
        infos={entry["key"]:info(entry) for entry in entries}
        value=build(entries,infos,req=request(["sku"]))
        self.assertEqual(value["capacity"]["partitionCount"],3)
        year=[item for item in value["requestedCoverage"] if item["window"]=="yearAgo"]
        self.assertEqual({item["reason"] for item in year},{"missing_source","missing_baseline_source"})
        value=build(entries[1:],{"ad-previous":infos["ad-previous"]},req=request(["sku"]))
        self.assertEqual(value["capacity"]["partitionCount"],1)
        self.assertTrue(any(item["kind"]=="comparison" and item["sourceKey"] is None for item in value["requestedCoverage"]))

    def test_extra_windows_remain_visible_but_are_not_scanned_or_selected(self):
        entries,infos,plan=fixture()
        value=build(entries,infos,plan,req=request(windows=["current"]))
        self.assertEqual(value["capacity"]["partitionCount"],11)
        self.assertTrue(all(desc["source"]["query"]["window"]=="current" and desc["baseline"] is None for desc in value["descriptors"]))
        outside=[item for item in value["requestedCoverage"] if item["status"]=="outside_fixed_request"]
        self.assertEqual(len(outside),20)
        self.assertEqual({item["window"] for item in outside},{"previous","yearAgo"})
        self.assertEqual(value["sourceCount"],7)

    def test_product_comparison_only_current_scope_is_not_fake_executable_plan(self):
        entries=[source("sku",dataset="sku")];infos={"sku":info(entries[0])}
        value=build(entries,infos,req=request(["sku"],["current"]))
        self.assertFalse(value["canScreen"])
        self.assertEqual(value["descriptors"],[])
        self.assertEqual(value["requestedCoverage"][0]["reason"],"baseline_not_requested")
        self.assertEqual(value["admissionFailures"][0]["reason"],"no_executable_rules")
        entries.append(source("sku-before",dataset="sku",window="previous"));infos["sku-before"]=info(entries[1])
        value=build(entries,infos,req=request(["sku"],["current","previous"]))
        self.assertTrue(value["canScreen"])
        self.assertEqual(value["capacity"]["partitionCount"],1)
        self.assertTrue(value["requestedCoveragePlanned"])
        singles=[item for item in value["requestedCoverage"] if item["kind"]=="single"]
        self.assertTrue(all(item["reason"]=="observed_in_comparison_table" and item["tableKeys"] for item in singles))

    def test_market_and_master_have_honest_distinct_non_scanning_coverage(self):
        entry={"key":"market","domain":"market","query":{"platform":"京东","category":"合成类目","scope":"全部",
            "rankingDimension":"SKU","priceBandFilter":"不限","startDate":"2026-09-01","endDate":"2026-09-02","window":"current"}}
        value=build([entry],{"market":info(entry)},req=request(["category"],["current"]))
        self.assertFalse(value["canScreen"])
        self.assertFalse(value["requestedCoveragePlanned"])
        self.assertEqual(value["requestedCoverage"][0]["reason"],"unsupported_market_rules")
        entry=source("master",dataset="master")
        value=build([entry],{"master":info(entry)},req=request(["sku"]))
        self.assertFalse(value["canScreen"])
        self.assertEqual(len(value["requestedCoverage"]),3)
        self.assertTrue(all(item["status"]=="dependency" and item["sourceKey"]=="master" for item in value["requestedCoverage"]))

    def test_checkpoint_missing_metric_keys_is_explicit_not_lost(self):
        entries=[source("sales",domain="sales")];infos={"sales":info(entries[0],0)}
        infos["sales"]["expected"]["metrics"]={}
        value=build(entries,infos,req=request(["shop"],["current"]))
        self.assertFalse(value["canScreen"])
        self.assertEqual(value["requestedCoverage"][0]["reason"],"no_additive_metrics")

    def test_stable_source_order_and_originals_unmodified(self):
        entries,infos,plan=fixture();original=deepcopy((entries,infos,plan))
        a=build(entries,infos,plan)
        b=build(list(reversed(entries)),dict(reversed(list(infos.items()))),plan)
        self.assertEqual(a,b)
        self.assertEqual((entries,infos,plan),original)
        a["descriptors"][0]["source"]["query"]["shop"]="changed output"
        self.assertEqual((entries,infos,plan),original)
        self.assertEqual(build(entries,infos,plan),b)

    def test_scope_query_and_expected_identity_affect_plan_not_authority(self):
        entries,infos,plan=fixture();a=build(entries,infos,plan)
        infos["promotion-current"]["expected"]["sourceRef"]=digest("another ref")
        b=build(entries,infos,plan)
        self.assertNotEqual(a["planDigest"],b["planDigest"])
        self.assertEqual(a["catalogDigest"],b["catalogDigest"])
        infos["promotion-current"]["metadata"]["coverage"]["status"]="missing_dates"
        c=build(entries,infos,plan)
        self.assertNotEqual(b["planDigest"],c["planDigest"])
        self.assertFalse(c["authorityVerified"])

    def test_lower_capacity_does_not_reduce_descriptors_and_invalid_numbers_reject(self):
        entries,infos,plan=fixture()
        original=build(entries,infos,plan)
        for limits in ({"maxPartitions":1},{"maxTables":1},{"maxDescriptorBytes":1},{"maxCoverageBytes":1},{"maxHeaderBytes":1}):
            value=build(entries,infos,plan,limits=limits)
            self.assertFalse(value["canScreen"])
            self.assertEqual(value["descriptors"],original["descriptors"])
            self.assertEqual(value["requestedCoverage"],original["requestedCoverage"])
        for limits in ({"maxPartitions":True},{"maxPartitions":64.0},{"maxPartitions":65},{"maxTables":0},{"unknown":1}):
            with self.assertRaises(AnalysisContractError):build(entries,infos,plan,limits=limits)
        value=build(entries,infos,plan,limits={"maxRowVisits":1})
        self.assertTrue(value["canScreen"])
        self.assertFalse(value["capacity"]["rowVisitsKnownWithinLimit"])
        self.assertFalse(value["capacity"]["actualTableRowsKnown"])

    def test_complete_information_and_type_boundaries_required_before_selection(self):
        entries,infos,plan=fixture()
        bad=deepcopy(infos);bad.pop("master")
        with self.assertRaises(AnalysisContractError):build(entries,bad,plan)
        bad=deepcopy(infos);bad["outside"]=bad["master"]
        with self.assertRaises(AnalysisContractError):build(entries,bad,plan)
        for value in (True,1.0,-1,101):
            bad=deepcopy(infos);bad["master"]["expected"]["rowCount"]=value
            with self.assertRaises(AnalysisContractError):build(entries,bad,plan)
        bad=deepcopy(infos);bad["master"]["pageCount"]=True
        with self.assertRaises(AnalysisContractError):build(entries,bad,plan)
        bad=deepcopy(infos);bad["master"]["expected"]["reconciled"]=1
        with self.assertRaises(AnalysisContractError):build(entries,bad,plan)
        bad=deepcopy(plan);bad["pairs"][0]["pairKey"]=digest("tamper")
        with self.assertRaises(AnalysisContractError):build(entries,infos,bad)

    def test_request_and_catalog_illegal_duplicates_not_silently_normalized(self):
        entries,infos,plan=fixture()
        req=request();req["requestedDimensions"].append("sku")
        with self.assertRaises(AnalysisContractError):build(entries,infos,plan,req=req)
        req=request();req["requestedWindows"]=["previous"]
        with self.assertRaises(AnalysisContractError):build(entries,infos,plan,req=req)
        req=request();req["extra"]=True
        with self.assertRaises(AnalysisContractError):build(entries,infos,plan,req=req)
        with self.assertRaises(AnalysisContractError):build(entries+[entries[0]],infos,plan)
        changed=deepcopy(entries);changed[0]["query"]["startDate"]="2026-08-01"
        with self.assertRaises(AnalysisContractError):build(changed,infos,plan)

    def test_full_forty_eight_source_plan_returns_all_four_hundred_tables_when_over_capacity(self):
        entries=[source(f"shop-{shop}-{window}",shop=f"合成店{shop}",window=window)
            for shop in range(16) for window in ("current","previous","yearAgo")]
        infos={entry["key"]:info(entry) for entry in entries}
        value=build(entries,infos)
        self.assertFalse(value["canScreen"])
        self.assertEqual(value["sourceCount"],48)
        self.assertEqual(len(value["descriptors"]),400)
        self.assertEqual(value["capacity"]["partitionCount"],400)
        self.assertEqual(len(value["requestedCoverage"]),400)
        self.assertEqual({failure["reason"] for failure in value["admissionFailures"]},{"table_limit","partition_limit"})
        self.assertTrue(all(item["status"]=="planned" for item in value["requestedCoverage"]))

    def test_real_result_stream_can_consume_complete_generated_descriptors(self):
        from . import test_diagnostic_screening as fixtures
        from .contracts import PageReconciler, coverage
        from .results import stream_table
        desc=fixtures.descriptor(family="promotion",rules=["promotion_spend_without_reported_gmv"])
        entry={key:desc["source"][key] for key in ("key","domain","query")}
        period=comparison_periods("2026-09-01","2026-09-02")["current"]
        items=[{"rowId":str(i+1),"platform":"京东","shopName":"合成店","skuId":str(i),
            "metrics":{"spendCents":10+i,"reportedGmvCents":0}} for i in range(24)]
        metadata=fixtures.metadata(desc["source"])
        page={"schemaVersion":"business-analysis-v1",**metadata,"sourceRef":desc["source"]["sourceRef"],"items":items,
            "coverage":coverage(period,["2026-09-01","2026-09-02"]),
            "control":{"rowCount":24,"typedTotals":{"spendCents":sum(10+i for i in range(24)),"reportedGmvCents":0}},
            "pageEvidence":{"rowCount":24,"sha256":digest(items)},"pagination":{"hasMore":False,"nextCursor":None}}
        verifier=PageReconciler();verifier.consume(page);expected=verifier.result()
        infos={entry["key"]:{"metadata":metadata,"expected":expected,"pageCount":1}}
        plan=build([entry],infos,req=request(["sku"],["current"]))
        calls=[]
        @contextmanager
        def opened(descriptor):
            calls.append(descriptor["source"]["key"])
            with stream_table(iter([page]),descriptor["dimension"],expected) as pair:yield pair
        result=diagnostic_screening.prepare(fixtures.binding(),plan["descriptors"],opened,limits=plan["limits"])
        self.assertEqual(calls,[entry["key"]])
        self.assertEqual(result["coverage"]["rowVisits"],24)
        self.assertEqual(result["partitions"][0]["matchedRows"],24)
        self.assertEqual(result["partitions"][0]["omittedRows"],8)

    def test_information_bounds_checked_for_even_unused_extra_window_sources(self):
        entries,infos,plan=fixture()
        for mutate in (
            lambda info:info.update(pageCount=2000),
            lambda info:info["metadata"].update(description="汉"*131073),
            lambda info:info["expected"].update(sourceRef="not-a-sha"),
            lambda info:info["expected"]["metrics"]["refundCents"].update(presentRows=True),
        ):
            changed=deepcopy(infos);mutate(changed["sales-yearAgo"])
            with self.assertRaises(AnalysisContractError):build(entries,changed,plan,req=request(windows=["current"]))
