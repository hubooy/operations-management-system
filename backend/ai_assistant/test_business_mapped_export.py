"""Complete mapped export tests; inert reports are not five-Agent execution.

Pure cases use actual partitioned joins, mapped aggregation, SQLite spooling,
and real ZIP/XML renderers. PostgreSQL cases use sealed owning-reader facts.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from django import test as djtest
from business_analysis import mapped_results, mapping_plan, volume_files, volume_plan, volume_delivery
from business_analysis.contracts import AnalysisContractError
from business_analysis.report_files import MAX_COLUMNS, NS, Column, Table, write_pair
from business_analysis import test_mapped_results as pure_fixtures, test_volume_delivery as delivery_fixtures
from . import business_mapped_export as mapped_export, business_export, business_integrated
from . import test_business_integrated_tools as tools_fixtures
from .policy import AiError, canonical, digest

MAPPING_KEYS={"mappingPlanDigest","mappingAlgorithmVersion","mappedTableAlgorithmVersion"}


def synthetic():
    masters=[("A","SKU1","SPU1"),("B","SKU1","SPU2"),("C","SKU-C","SPU-C"),
        ("F","SKU-F","SPU-F"),("N","SKU-N",None),("X","SKU-X","SPU-X"),("X","SKU-Y","SPU-Y")]
    current=[("A",100,60,1),("B",70,40,1),("C",30,20,1),("N",10,5,1),("X",5,3,1),(None,-2,-1,-1)]
    before=[("A",50,30,1),("F",20,10,1),("N",5,2,1),("X",2,1,1),(None,-1,-1,-1)]
    objects={};sources=[]
    for window in ("current","previous","yearAgo"):
        source,descriptor=pure_fixtures.fixture(window,sales=current if window=="current" else before,masters=masters)
        objects[descriptor["sales_source"]["key"]]=source;sources.append(descriptor["sales_source"])
    sources.append(descriptor["master_source"])
    plan=mapping_plan.normalize(sources,[{"salesKey":key,"masterKey":"master"} for key in objects])
    snapshot={"mappingPlan":plan,"mappingPlanDigest":digest(plan)}
    report=SimpleNamespace(id="synthetic-mapped-export")
    evidence=SimpleNamespace(id="evidence-test")
    pairs={p["pairKey"]:p for p in plan["pairs"]}
    @contextmanager
    def table(run,actual_plan,key,dimension,principal,baseline_pair_key=None):
        assert actual_plan==plan
        source=objects[pairs[key]["salesKey"]]
        baseline=objects[pairs[baseline_pair_key]["salesKey"]] if baseline_pair_key else None
        with mapped_results.mapped_table(source,dimension,baseline=baseline) as result:yield result
    def reconciled(run,sales,master,principal):
        assert master=="master"
        return objects[sales].open_mapping(max_scratch_bytes=128*1024*1024)
    return report,snapshot,evidence,sources,table,reconciled


@contextmanager
def synthetic_spool():
    report,snapshot,evidence,sources,table,reconciled=synthetic()
    with patch.object(business_integrated,"bound",return_value=(report,snapshot,{},evidence,sources)), \
            patch.object(mapped_export.business_mapped_analysis,"table",side_effect=table), \
            patch.object(mapped_export.business_identity,"reconciled",side_effect=reconciled),business_export.TableSpool() as spool:
        mapped_export.append(spool,report,None)
        yield spool,report,snapshot


def spool_records(spool,index):
    return [json.loads(value) for (value,) in spool.db.execute("SELECT payload FROM rows WHERE table_id=? ORDER BY row_id",(index,))]


def mapping_metadata(snapshot):
    return {"mappingPlanDigest":snapshot["mappingPlanDigest"],"mappingAlgorithmVersion":snapshot["mappingPlan"]["algorithmVersion"],
        "mappedTableAlgorithmVersion":mapped_results.ALGORITHM_VERSION}


class MappedExportPureTests(unittest.TestCase):
    def test_three_windows_both_dimensions_comparisons_and_156_columns(self):
        with synthetic_spool() as (spool,report,snapshot):
            analysis=[(i,t) for i,t in enumerate(spool.tables) if t.key.startswith("mapped-analysis-")]
            groups=[(i,t) for i,t in enumerate(spool.tables) if t.key.startswith("mapping-groups-")]
            self.assertEqual((len(analysis),len(groups),len(spool.tables)),(10,3,27))
            expected_titles={f"sales-{window}_映射{dim}_{period}" for window,period in
                (("current","本期"),("previous","环比基期"),("yearAgo","同比基期")) for dim in ("SKU","SPU")}
            expected_titles|={f"sales-current_映射{dim}_{period}" for dim in ("SKU","SPU") for period in ("环比","同比")}
            self.assertEqual({table.title for _,table in analysis},expected_titles)
            self.assertEqual(max(len(t.columns) for _,t in analysis),156)
            self.assertTrue(all(len(t.columns)<=MAX_COLUMNS for t in spool.tables))
            for index,table in analysis:
                records=spool_records(spool,index)
                self.assertEqual(len(records),table.row_count)
                if table.title=="sales-current_映射SKU_本期":
                    row=next(r for r in records if r["/entity/skuId"]=="SKU1")
                    self.assertEqual((row["/currentRowCount"],row["/metrics/netSalesCents/value"]),(2,170))
                    self.assertEqual(sum(r["/currentRowCount"] for r in records),6)
                    self.assertEqual({r["/entity/mappingStatus"] for r in records},{"matched","ambiguous","unmatched"})
                if table.title in {"sales-current_映射SKU_环比","sales-current_映射SKU_同比"}:
                    current_only=next(r for r in records if r["/entity/skuId"]=="SKU-C")
                    prior_only=next(r for r in records if r["/entity/skuId"]=="SKU-F")
                    self.assertIsNone(current_only["/baselineMetrics"])
                    self.assertIsNone(prior_only["/metrics/netSalesCents"])
                    self.assertIsNone(prior_only["/comparisons/netSalesCents/changeRate"])

    def test_actual_multivolume_xlsx_xml_has_every_complete_table_and_mapping_binding(self):
        with synthetic_spool() as (spool,report,snapshot):
            request=volume_files.request_for(spool.tables,report_id=report.id,evidence_digest="c"*64,renderer_version=4)
            plan=volume_plan.build(request,max_tables=8)
            outputs=[volume_files.VolumeStreams(io.BytesIO(),io.BytesIO()) for _ in plan["volumes"]]
            manifest=volume_files.render(spool.tables,outputs,report_id=report.id,evidence_digest="c"*64,renderer_version=4,
                plan=plan,title="三窗口完整映射",metadata=mapping_metadata(snapshot),max_tables=8)
            self.assertEqual((manifest["volumeCount"],manifest["sourceTableCount"]),(4,27))
            actual_keys=[]
            for volume,streams in zip(manifest["volumes"],outputs):
                with zipfile.ZipFile(streams.xlsx) as archive:
                    workbook=ET.fromstring(archive.read("xl/workbook.xml"))
                    self.assertEqual(len(workbook.findall("m:sheets/m:sheet",{"m":NS})),len(volume["tables"]))
                    for number,part in enumerate(volume["tables"],1):
                        actual_keys.append(part["key"])
                        xml=ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
                        self.assertEqual(int(xml.find("m:autoFilter",{"m":NS}).attrib["ref"].split(":")[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")),part["rowLimit"]+3)
                        self.assertEqual(len(xml.findall("m:cols/m:col",{"m":NS})),part["columnCount"])
                for format in ("html","xlsx"):
                    data=getattr(streams,format).getvalue()
                    self.assertEqual(hashlib.sha256(data).hexdigest(),volume["files"][format]["sha256"])
            self.assertEqual(actual_keys,[t.key for t in spool.tables])
            root,data=volume_delivery.make(manifest,binding_digest="a"*64,attempt=1,draft=True,max_tables=8)
            self.assertEqual(volume_delivery.verify_full(root,data,binding_digest="a"*64,attempt=1,draft=True,
                report_id=report.id,evidence_digest="c"*64,max_tables=8),manifest)
            self.assertEqual({k:manifest[k] for k in MAPPING_KEYS},mapping_metadata(snapshot))

    def test_manifest_each_missing_binding_wrong_algorithm_and_legacy_bytes(self):
        full=delivery_fixtures.synthetic_manifest()
        legacy=canonical(full).encode()
        root,data=volume_delivery.make(full,**delivery_fixtures.IDENTITY,max_tables=1)
        self.assertEqual(data,legacy)
        self.assertTrue(MAPPING_KEYS.isdisjoint(json.loads(data)))
        mapped={**full,"mappingPlanDigest":"a"*64,"mappingAlgorithmVersion":"exact-product-partition-v1",
            "mappedTableAlgorithmVersion":"business-mapped-results-v1"}
        delivery_fixtures.resign(mapped)
        volume_delivery.make(mapped,**delivery_fixtures.IDENTITY,max_tables=1)
        changes=[(key,None) for key in MAPPING_KEYS]+[("mappingAlgorithmVersion","future"),("mappedTableAlgorithmVersion","future"),("mappingPlanDigest","wrong")]
        for key,value in changes:
            broken=deepcopy(mapped)
            if value is None:broken.pop(key)
            else:broken[key]=value
            delivery_fixtures.resign(broken)
            with self.subTest(key=key,value=value),self.assertRaises(AnalysisContractError):
                volume_delivery.make(broken,**delivery_fixtures.IDENTITY,max_tables=1)
        self.assertEqual(canonical(full).encode(),legacy)

    def test_budget_and_mapping_share_complete_manifest_and_first_volume_calculator(self):
        from business_analysis import budget, budget_offline, test_budget
        plan,bases=test_budget.fixture()
        with synthetic_spool() as (spool,report,snapshot):
            calculator=budget_offline.payload(budget.calculate(plan,bases),report.id)
            calculator["excelEnabled"]=True
            request=volume_files.request_for(spool.tables,report_id=report.id,evidence_digest="c"*64,renderer_version=4)
            planned=volume_plan.build(request,max_tables=8,native_budget_sheets=3)
            outputs=[volume_files.VolumeStreams(io.BytesIO(),io.BytesIO()) for _ in planned["volumes"]]
            full=volume_files.render(spool.tables,outputs,report_id=report.id,evidence_digest="c"*64,renderer_version=4,
                plan=planned,title="合成预算及映射",metadata=mapping_metadata(snapshot),max_tables=8,
                offline_budget=calculator,excel_budget=calculator)
            self.assertEqual(full["budgetPlanDigest"],calculator["planDigest"])
            self.assertEqual(full["mappingPlanDigest"],snapshot["mappingPlanDigest"])
            self.assertEqual([v["nativeBudgetSheets"] for v in full["volumes"]],[3]+[0]*(full["volumeCount"]-1))
            self.assertEqual([v["offlineBudgetEnabled"] for v in full["volumes"]],[True]+[False]*(full["volumeCount"]-1))
            self.assertEqual(sum(len(v["tables"]) for v in full["volumes"]),27)
            root,data=volume_delivery.make(full,binding_digest="a"*64,attempt=1,draft=True,max_tables=8)
            volume_delivery.verify_full(root,data,binding_digest="a"*64,attempt=1,draft=True,
                report_id=report.id,evidence_digest="c"*64,max_tables=8)

    def test_late_context_or_authority_failure_never_returns_append_success(self):
        report,snapshot,evidence,sources,table,reconciled=synthetic()
        with business_export.TableSpool() as spool,patch.object(business_integrated,"bound",side_effect=[
                (report,snapshot,{},evidence,sources),AiError("revoked","access_denied",403)]), \
                patch.object(mapped_export.business_mapped_analysis,"table",side_effect=table), \
                patch.object(mapped_export.business_identity,"reconciled",side_effect=reconciled),self.assertRaises(AiError):
            mapped_export.append(spool,report,None)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class MappedExportPostgresTests(djtest.TransactionTestCase):
    user=tools_fixtures.BusinessIntegratedToolsTests.user
    call=tools_fixtures.BusinessIntegratedToolsTests.call
    collect_body=tools_fixtures.BusinessIntegratedToolsTests.collect_body
    insert=tools_fixtures.BusinessIntegratedToolsTests.insert
    seed=tools_fixtures.BusinessIntegratedToolsTests.seed

    def setUp(self):tools_fixtures.BusinessIntegratedToolsTests.setUp(self)

    def test_real_sealed_current_append_and_optional_budget_coexist_without_dispatch(self):
        from . import business_budget_store
        for with_budget in (False,True):
            report,prepared=self.seed(budget=with_budget)
            with patch("ai_assistant.provider.turn") as model,patch("ai_assistant.transport.execute_tool") as remote,business_export.TableSpool() as spool:
                mapped_export.append(spool,report,self.admin)
                self.assertEqual(len(spool.tables),7)
                self.assertEqual(len([t for t in spool.tables if t.key.startswith("mapped-analysis-")]),2)
                if with_budget:
                    fixed=business_budget_store.load(report,self.admin)
                    self.assertEqual(fixed.reference,prepared.budget.reference)
                for i,t in enumerate(spool.tables):
                    if t.title=="sales_映射SKU_本期":
                        rows=spool_records(spool,i)
                        self.assertEqual(sum(r["/currentRowCount"] for r in rows),12)
                        self.assertEqual(sum(r["/metrics/netSalesCents/value"] for r in rows),120000)
                xlsx,html=io.BytesIO(),io.BytesIO()
                result=write_pair(xlsx,html,title="封存实际来源映射",metadata=mapping_metadata(prepared.snapshot),tables=spool.tables)
                self.assertEqual(len(result["tables"]),7)
                with zipfile.ZipFile(xlsx) as archive:self.assertEqual(len(ET.fromstring(archive.read("xl/workbook.xml")).findall("m:sheets/m:sheet",{"m":NS})),7)
                model.assert_not_called();remote.assert_not_called()

    def test_real_three_windows_with_cross_spu_and_both_missing_sides(self):
        from . import test_business_mapped_analysis as service_fixtures
        from sales.models import SalesOrderLine
        from sales.tests.factories import make_line
        add_master=service_fixtures.BusinessMappedAnalysisTests.master
        add_master(self,2,"M2","SKU1","SPU2")
        add_master(self,3,"M3","SKU-C","SPU-C")
        add_master(self,4,"M4","SKU-F","SPU-F")
        # The inherited 12-line fixture starts with pk=1 unmatched and pk=2..12 M1.
        # Moving pk=1 to M2 adds it to SKU1; moving pk=2 to M3 removes one M1.
        # Therefore SKU1 = ten M1/SPU1 facts + one M2/SPU2 fact, not ten total.
        self.assertEqual(SalesOrderLine.objects.filter(pk__lte=12,online_spec_code="M1").count(),11)
        self.assertEqual(SalesOrderLine.objects.get(pk=1).online_spec_code,"")
        SalesOrderLine.objects.filter(pk=1).update(online_spec_code="M2")
        SalesOrderLine.objects.filter(pk=2).update(online_spec_code="M3")
        self.assertEqual(SalesOrderLine.objects.filter(pk__lte=12,online_spec_code="M1").count(),10)
        for index,(day,code) in enumerate((("2026-07-31","M1"),("2026-07-31","M4"),("2025-08-01","M1"),("2025-08-01","M4")),100):
            make_line(index,f"mapped-export-{index}",channel=self.query["channel"],online_spec_code=code,
                ship_time=day+" 10:00:00",line_ship_time=day+" 10:00:00").save()
        body=deepcopy(self.evidence_body);body["clientRequestId"]="export-three-windows"
        for window in ("previous","yearAgo"):
            body["sources"].append({"key":window,"domain":"sales","query":{**self.query,"window":window}})
        self.parent=self.collect_body(body)
        report,prepared=self.seed(choices=[{"salesKey":key,"masterKey":"master"} for key in ("sales","previous","yearAgo")])
        with business_export.TableSpool() as spool:
            mapped_export.append(spool,report,self.admin)
            self.assertEqual(len(spool.tables),27)
            analysis=[(i,t) for i,t in enumerate(spool.tables) if t.key.startswith("mapped-analysis-")]
            self.assertEqual(len(analysis),10)
            self.assertEqual(max(len(t.columns) for _,t in analysis),156)
            for index,table in analysis:
                rows=spool_records(spool,index)
                if table.title=="sales_映射SKU_本期":
                    matched=next(r for r in rows if r["/entity/skuId"]=="SKU1")
                    self.assertEqual(matched["/currentRowCount"],11)
                    self.assertEqual(matched["/metrics/netSalesCents/value"],110000)
                    self.assertEqual(sum(r["/currentRowCount"] for r in rows),12)
                if table.title=="sales_映射SPU_本期":
                    counts={r["/entity/spuId"]:r["/currentRowCount"] for r in rows}
                    self.assertEqual(counts,{"SPU1":10,"SPU2":1,"SPU-C":1})
                if table.title in ("sales_映射SKU_环比","sales_映射SKU_同比"):
                    prior_only=next(r for r in rows if r["/entity/skuId"]=="SKU-F")
                    current_only=next(r for r in rows if r["/entity/skuId"]=="SKU-C")
                    self.assertIsNone(prior_only["/metrics/netSalesCents"])
                    self.assertIsNone(current_only["/baselineMetrics"])
                    self.assertIsNone(prior_only["/comparisons/netSalesCents/changeRate"])
