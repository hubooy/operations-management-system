"""Actual planner/scanner/storage DTOs; synthetic facts, no Django or providers."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from unittest import TestCase
from unittest.mock import patch

from . import diagnostic_screening as scan, screening_plan as planning, screening_storage as storage, screening_package as package
from . import test_screening_plan as plans, test_diagnostic_screening as facts
from .contracts import AnalysisContractError, canonical, digest
from .evidence_v2 import normalize_sources


def fixture(*, rows=2, rich=False):
    sources, infos, mapping = plans.fixture(rows=max(1,rows))
    request = plans.request()
    if rich:
        sources = [s for s in sources if s["query"]["window"] != "yearAgo"]
        for dataset in ("sku", "b2b"):
            sources.extend(plans.source(dataset+"-"+window,dataset=dataset,window=window) for window in ("current","previous"))
        sources.append({"key":"market","domain":"market","query":{"platform":"京东","category":"合成类目",
            "scope":"POP","rankingDimension":"SKU","priceBandFilter":"全部价格带",
            "startDate":"2026-09-01","endDate":"2026-09-02","window":"current"}})
        infos = {s["key"]:plans.info(s,max(1,rows)) for s in sources}; mapping = None
        request = plans.request(["shop"],["current","previous"])
    # Reader.sources is the canonical catalog order with only these 3 fields.
    sources = [{k:s[k] for k in ("key","domain","query")} for s in normalize_sources(sources)]
    # Use the actual persisted Reader.info metadata shape, not invented query
    # or source fields: query identity lives in Reader.sources, not metadata.
    for s in sources:
        infos[s["key"]]["metadata"] = {"sourceRevision":"synthetic-1","coverage":{"status":"dates_present"},
            "excludedOverlappingPeriodRows":None,"identityCheck":None,"availableDates":None,"metricSemantics":{},
            "freshness":None,"firstCollectedAt":"2026-09-03T00:00:00+00:00","lastCollectedAt":"2026-09-03T00:00:00+00:00"}
    plan = planning.build(request,sources,infos,mapping_plan=mapping)
    binding = {**facts.binding(),"workflowId":"workflow-synthetic","ownerEmail":"synthetic@example.invalid",
        "scope":None,"role":"admin","snapshotDigest":digest("snapshot"),"workflowInputDigest":digest("input"),
        "executionProfile":"business-agent-integrated-reference-v1" if mapping else "business-agent-reference-v2",
        "sourceCount":len(sources),"sourcesDigest":digest(sources),"sourceInfosDigest":digest(infos),
        "catalogDigest":plan["catalogDigest"],"analysisRequestDigest":plan["analysisRequestDigest"],
        "mappingPlanDigest":plan["mappingPlanDigest"],"budgetRef":None,"algorithmVersion":scan.ALGORITHM_VERSION}
    @contextmanager
    def opener(desc):
        def values(current):
            if desc["source"]["domain"] == "sales":
                return {"refundCents":30 if current else 10,"positiveSalesCents":20 if current else 30,
                    "grossProfitCents":-10,"feeCents":20 if current else 10,"netSalesCents":10 if current else 20}
            if desc["source"]["query"]["dataset"] == "promotion":
                return {"spendCents":20 if current else 10,"reportedGmvCents":0 if current else 30}
            return {"productDayVisitors":30 if current else 10,"paymentCents":10 if current else 20}
        records = [facts.row(desc,i,values(desc["source"]["query"]["window"] == "current"),values(False) if desc["baseline"] else None) for i in range(rows)]
        head = facts.header(desc,rows)
        if desc["mode"] == "mapped":
            for key in ("binding","baselineBinding"):
                if head[key] is not None:
                    head[key].update({k:binding[k] for k in facts.binding() if k != "reportId"})
        yield head, iter(records)
    prepared = scan.prepare({k:binding[k] for k in facts.binding()},plan["descriptors"],opener,limits=plan["limits"])
    complete = plan["requestedCoveragePlanned"]
    authority = {"completeSourceTraversalForExecutedTables":True,"executedTablesComplete":True,
        "requestedCoveragePlanned":complete,"requestedTablesExecutedComplete":complete,
        "requestedSourceDateCoverageComplete":complete and rows>0,"entityDailyCoverageVerified":False,
        "binding":binding,"reportId":binding["reportId"],"evidenceRunId":binding["evidenceRunId"],
        "selectionPolicy":plan["selectionPolicy"],"selectionPlanDigest":plan["planDigest"],"pureResultDigest":prepared["resultDigest"],
        "tableCount":prepared["coverage"]["tableCount"],"rowVisits":prepared["coverage"]["rowVisits"],
        "partitionCount":len(prepared["partitions"]),"limitations":prepared["limitations"]}
    value = {"schemaVersion":"business-diagnostic-screening-v1","authority":authority,"bindingDigest":digest(binding),
        "planDigest":plan["planDigest"],"plan":plan,"prepared":prepared}
    value["resultDigest"] = digest(value)
    return storage.materialize(value), {"sources":sources,"source_infos":infos,"selection_plan":plan}, value


def mutate_package(original, change):
    value = json.loads(original._raw); change(value)
    return package.Package(value["header"],value["directory"],value["records"])


class ScreeningPackageTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bundle,cls.kwargs,cls.value = fixture()
        cls.packages = package.build(cls.bundle,**cls.kwargs)

    def test_five_roles_exact_lossless_55_partition_round_trip(self):
        self.assertEqual(len(self.value["prepared"]["partitions"]),55)
        expected_coverage = [item for p in self.bundle["pages"] if p["kind"] == "coverage" for item in json.loads(p["payloadJson"])["items"]]
        parts = {p["partitionKey"]:p for p in self.value["prepared"]["partitions"]}
        for role,result in self.packages.items():
            decoded = result.unpack()
            self.assertEqual(decoded["sources"],self.kwargs["sources"])
            self.assertEqual(decoded["sourceInfos"],self.kwargs["source_infos"])
            self.assertEqual(len(decoded["tableBindings"]),len(self.kwargs["selection_plan"]["descriptors"]))
            self.assertEqual(decoded["coverage"],expected_coverage)
            self.assertEqual(decoded["authority"],self.value["authority"])
            self.assertFalse(decoded["authorityVerified"])
            for group in decoded["candidates"]: self.assertEqual(group["items"],parts[group["partitionKey"]]["candidates"])
            if role in ("report","independent_review"): self.assertEqual(len(decoded["candidates"]),55)
        self.assertEqual(self.packages["market_b2b"].unpack()["candidates"],[])

    def test_real_domain_dataset_selects_b2b_not_product_alias_and_all_gaps_remain(self):
        bundle,kwargs,value = fixture(rich=True)
        packages = package.build(bundle,**kwargs)
        descs = {digest({k:v for k,v in d.items() if k!="ruleIds"}):d for d in kwargs["selection_plan"]["descriptors"]}
        for role in ("commerce","promotion","market_b2b"):
            decoded = packages[role].unpack()
            self.assertTrue(any(r["kind"]=="requested" and r["value"]["reason"]=="unsupported_market_rules" for r in decoded["coverage"]))
            families = {descs[next(p["tableKey"] for p in value["prepared"]["partitions"] if p["partitionKey"]==g["partitionKey"])]["source"]["query"].get("dataset","erp") for g in decoded["candidates"]}
            self.assertEqual(families,{"commerce":{"erp","sku"},"promotion":{"promotion"},"market_b2b":{"b2b"}}[role])

    def test_empty_candidates_keep_all_partition_counts_no_empty_tool_pages(self):
        bundle,kwargs,_ = fixture(rows=0)
        result = package.build(bundle,**kwargs)["report"]
        decoded = result.unpack()
        self.assertEqual(len(decoded["candidates"]),55)
        self.assertTrue(all(not p["items"] for p in decoded["candidates"]))
        self.assertTrue(all(page["records"] for page in result.pages()))

    def test_actual_next_offset_utf8_bytes_100_cap_and_digests(self):
        result = self.packages["report"]; pages = list(result.pages()); offset = 0
        self.assertGreater(len(pages),1)
        for index,page in enumerate(pages):
            self.assertLessEqual(len(canonical(page).encode()),38000)
            self.assertLessEqual(len(page["records"]),100)
            self.assertEqual(page["pagination"]["offset"],offset)
            offset += len(page["records"])
            self.assertEqual(page["pagination"]["nextOffset"],offset if index<len(pages)-1 else None)
            self.assertEqual(page["pageDigest"],digest({k:v for k,v in page.items() if k!="pageDigest"}))
            self.assertEqual(page["packageDigest"],result.package_digest)
            self.assertEqual(page["directory"] is not None,index==0)
        self.assertEqual(offset,pages[0]["totalRecords"])
        self.assertTrue(any(p["pagination"]["returned"]<100 for p in pages[:-1]))

    def test_stable_frozen_objects_and_caller_aliases(self):
        bundle,kwargs = deepcopy((self.bundle,self.kwargs))
        result = package.build(bundle,**kwargs)["report"]; expected = result.page()
        bundle.clear(); kwargs.clear(); page=result.page();page["records"].clear()
        self.assertEqual(result.page(),expected)
        with self.assertRaises(FrozenInstanceError):result._raw="{}"
        self.assertEqual(result.package_digest,self.packages["report"].package_digest)
        self.assertNotEqual(result.package_digest,self.packages["independent_review"].package_digest)

    def test_source_info_plan_and_cross_binding_replacement_rejected(self):
        for kind in ("source","info","plan","missing","extra"):
            kwargs=deepcopy(self.kwargs)
            if kind=="source":kwargs["sources"][0]["query"]["shop"]="另一店"
            elif kind=="info":next(iter(kwargs["source_infos"].values()))["metadata"]["sourceRevision"]="other"
            elif kind=="plan":kwargs["selection_plan"]["descriptors"][0]["dimension"]="daily"
            elif kind=="missing":kwargs["sources"].pop()
            else:kwargs["selection_plan"]["extra"]=True
            with self.subTest(kind=kind),self.assertRaises(AnalysisContractError):package.build(self.bundle,**kwargs)

    def test_missing_duplicate_reordered_mixed_role_and_late_pages_rejected(self):
        pages=list(self.packages["report"].pages())
        for changed in (pages[:-1],[pages[0],*pages],list(reversed(pages)),pages+pages[-1:],
                [pages[0],*list(self.packages["promotion"].pages())[1:]]):
            with self.assertRaises(AnalysisContractError):package.decode_pages(changed)
        def broken():
            yield from pages
            raise RuntimeError("late storage failure")
        with self.assertRaisesRegex(RuntimeError,"late"):package.decode_pages(broken())

    def test_rehashed_schema_unknown_fields_constants_and_indices_rejected(self):
        original=self.packages["report"]
        def candidate(data):return next(row for row in data["records"] if row[0]=="candidate")
        changes=[lambda d:d["directory"]["recordSchemas"]["candidate"].append("extra"),
            lambda d:d["directory"]["candidateConstants"][0].append("extra"),
            lambda d:candidate(d).__setitem__(1,True),lambda d:candidate(d).__setitem__(1,10000),
            lambda d:candidate(d).append("extra"),lambda d:d["records"][0].__setitem__(0,"unknown"),
            lambda d:d["directory"]["candidateConstants"].append(deepcopy(d["directory"]["candidateConstants"][0])),
            lambda d:d["header"].update(extra=True)]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(AnalysisContractError):
                package.decode_pages(mutate_package(original,change).pages())

    def test_rehashed_role_theft_candidate_drop_and_bad_reference_rejected(self):
        def drop(data):
            index=next(i for i,r in enumerate(data["records"]) if r[0]=="candidate")
            data["records"].pop(index);data["header"]["totalRecords"]-=1;data["directory"]["recordCounts"]["candidate"]-=1
        def reference(data):
            row=next(r for r in data["records"] if r[0]=="candidate")
            row[3]["rowIndex"]=True
        for change in (lambda d:d["header"].update(role="market_b2b"),drop,reference):
            with self.assertRaises(AnalysisContractError):package.decode_pages(mutate_package(self.packages["report"],change).pages())

    def test_page_numbers_are_not_offsets_or_permitted_bool(self):
        result=self.packages["report"]
        for offset in (True,1.0,"0",-1,100000):
            with self.assertRaises(AnalysisContractError):result.page(offset)
        pages=list(result.pages()); pages[1]["pagination"]["offset"]=1
        pages[1]["pageDigest"]=digest({k:v for k,v in pages[1].items() if k!="pageDigest"})
        with self.assertRaises(AnalysisContractError):package.decode_pages(pages)

    def test_non_maximal_prefix_even_with_valid_hashes_rejected(self):
        pages=list(self.packages["report"].pages())
        all_records=[r for p in pages for r in p["records"]]
        altered=[]
        for offset in range(0,len(all_records),10):
            page=deepcopy(pages[0]); items=all_records[offset:offset+10];end=offset+len(items)
            page.update(records=items,directory=page["directory"] if offset==0 else None)
            page["pagination"]={"offset":offset,"limit":100,"returned":len(items),"total":len(all_records),"nextOffset":end if end<len(all_records) else None}
            page["pageDigest"]=digest({k:v for k,v in page.items() if k!="pageDigest"});altered.append(page)
        with self.assertRaises(AnalysisContractError):package.decode_pages(altered)

    def test_capacity_rejects_all_packages_without_truncation(self):
        for name,value in (("MAX_INPUT_BYTES",10),("MAX_PACKAGE_BYTES",10),("MAX_ALL_PACKAGE_BYTES",10),("MAX_PAGE_BYTES",100)):
            with self.subTest(name=name),patch.object(package,name,value),self.assertRaises(AnalysisContractError):package.build(self.bundle,**self.kwargs)
        result=self.packages["report"]
        with patch.object(package,"MAX_PAGE_BYTES",100),self.assertRaises(AnalysisContractError):result.page()

    def test_deep_cycles_surrogates_and_unknown_input_fields_fail_contract(self):
        for value in ("\ud800",float("inf")):
            kwargs=deepcopy(self.kwargs);kwargs["sources"][0]["query"]["shop"]=value
            with self.assertRaises(AnalysisContractError):package.build(self.bundle,**kwargs)
        cyclic={};cyclic["self"]=cyclic
        with self.assertRaises(AnalysisContractError):package.build(cyclic,**self.kwargs)
        bundle=deepcopy(self.bundle);bundle["extra"]=True
        with self.assertRaises(AnalysisContractError):package.build(bundle,**self.kwargs)

    def test_private_raw_tampering_and_all_published_integer_fields_fail_closed(self):
        result=mutate_package(self.packages["report"],lambda d:None)
        value=json.loads(result._raw);value["records"][0][1]["query"]["shop"]="tampered"
        object.__setattr__(result,"_raw",canonical(value))
        with self.assertRaisesRegex(AnalysisContractError,"内部字节"):result.page()
        with self.assertRaises(AnalysisContractError):_ = result.package_digest
        changes=[lambda d:d["directory"]["authority"].update(tableCount=float(d["directory"]["authority"]["tableCount"])),
            lambda d:d["directory"]["authority"].update(partitionCount=True),
            lambda d:d["directory"]["authority"].update(rowVisits=True),
            lambda d:d["directory"]["authority"].update(rowVisits=d["directory"]["authority"]["rowVisits"]+1),
            lambda d:next(r for r in d["records"] if r[0]=="table").__setitem__(3,2.0),
            lambda d:next(r for r in d["records"] if r[0]=="table").__setitem__(4,True),
            lambda d:d["directory"]["binding"].update(evidenceVersion=True)]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(AnalysisContractError):
                package.decode_pages(mutate_package(self.packages["report"],change).pages())

    def test_invalid_rule_partition_index_and_table_source_binding_rejected(self):
        table_schema=package.SCHEMAS["table"]
        for value in (True,-1,1000):
            def change(data):
                row=next(r for r in data["records"] if r[0]=="table")
                row[1+table_schema.index("rulePartitionIndices")][0]=value
            with self.assertRaises(AnalysisContractError):package.decode_pages(mutate_package(self.packages["report"],change).pages())
        def source(data):next(r for r in data["records"] if r[0]=="tableBinding")[2]="master"
        with self.assertRaises(AnalysisContractError):package.decode_pages(mutate_package(self.packages["report"],source).pages())
