"""Pure ledger projections with real directory/budget page reconstruction."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from business_analysis import budget, budget_reference, evidence_v2
from business_analysis.test_budget import fixture
from . import business_budget_receipts as receipts
from .business_budget_store import PreparedBudget
from .policy import AiError, canonical, digest


def trusted_fixture(target_count=25, source_count=48):
    sources = [{"key":f"source-{i}","domain":"sales","query":{"platform":"京东","shop":f"店铺{i}","channel":f"渠道{i}",
        "startDate":"2026-08-01","endDate":"2026-08-01","window":"current"}} for i in range(source_count)]
    catalog = evidence_v2.build_catalog(sources)
    row = SimpleNamespace(id="evidence-fixed",version=3,plan_json=canonical(catalog["header"]))
    plan,bases = fixture(); target,base = plan["targets"][0],bases[0]
    plan["targets"] = [{**target,"rowIndex":i,"rowId":f"{i:064x}","weight":1} for i in range(target_count)]
    bases = [{**deepcopy(base),"rowId":t["rowId"]} for t in plan["targets"]]
    binding = budget_reference.make_binding(plan,report_id="report-fixed",owner_email="owner@example.invalid",scope=None,
        evidence_run_id=row.id,evidence_version=row.version,evidence_plan_digest=catalog["planDigest"],
        catalog_digest=catalog["header"]["catalogDigest"],sealed_digest="c"*64)
    result = {**budget.calculate(plan,bases),"evidenceRunId":row.id,"evidenceVersion":row.version,"evidencePlanDigest":catalog["planDigest"]}
    prepared = PreparedBudget("budget-fixed",canonical(plan),canonical(binding),canonical(result))
    directories,budgets = receipts.expected_pages(row,sources,prepared)
    return row,sources,prepared,directories,budgets


class BusinessBudgetReceiptTests(TestCase):
    def setUp(self):
        self.row,self.sources,self.prepared,self.directories,self.budgets = trusted_fixture()
        self.job = SimpleNamespace(id="job-fixed",workflow_node_key="promotion",workflow_run_id="flow-fixed",owner_email="owner@example.invalid",scope_json="null")
        self.principal = SimpleNamespace(email=self.job.owner_email,role="admin",scope=None)
        self.snapshot = {"executionProfile":budget_reference.PROFILE}
        self.dispatches,self.results = [],{}
        self.trusted = patch.object(receipts,"_trusted",return_value=(self.prepared,self.directories,self.budgets)).start()
        self.dispatch_manager = patch.object(receipts.m.AiAgentToolDispatches.objects,"filter").start()
        self.query = self.dispatch_manager.return_value.order_by.return_value.annotate.return_value.values.return_value
        self.query.__getitem__.side_effect = lambda page: [{**d,"argument_bytes":len(d["arguments_json"].encode()),"arguments_text":d["arguments_json"][:8193]} for d in self.dispatches][page]
        self.result_manager = patch.object(receipts.m.AiAgentToolResults.objects,"filter",side_effect=self.result_query).start()
        self.addCleanup(patch.stopall)

    def result_query(self,**kwargs):
        query = MagicMock(); row = self.results.get(kwargs["tool_dispatch_id"])
        query.annotate.return_value.values.return_value.first.return_value = None if row is None else {
            **row,"result_bytes":len(row["result_json"].encode()),"result_text":row["result_json"][:256*1024+1]}
        return query

    def add(self,name,offset=0,*,data=None,ok=True,state="succeeded"):
        args = {"runId":self.row.id,"offset":offset}
        if name == receipts.BUDGET_TOOL: args["reportId"] = "report-fixed"
        number = len(self.dispatches)+1
        if data is None: data = (self.directories if name == receipts.DIRECTORY_TOOL else self.budgets)[offset]
        result = {"toolName":name,"ok":ok,"auditStatus":"recorded","data":data}
        dispatched = {"id":f"dispatch-{number}","job_id":self.job.id,"provider_dispatch__job_id":self.job.id,"tool_call_ordinal":number,
            "tool_name":name,"state":state,"arguments_json":canonical(args),"arguments_digest":digest(args)}
        row = {"tool_dispatch_id":dispatched["id"],"result_json":canonical(result),"result_digest":digest(result)}
        self.dispatches.append(dispatched);self.results[dispatched["id"]] = row
        return dispatched,row

    def complete(self):
        for offset in self.directories: self.add(receipts.DIRECTORY_TOOL,offset)
        for offset in self.budgets: self.add(receipts.BUDGET_TOOL,offset)

    def reject(self):
        with self.assertRaises(AiError): receipts.validate_complete(self.job,self.snapshot,self.principal)

    def mutate(self,row,change):
        result = json.loads(row["result_json"]);change(result)
        row.update(result_json=canonical(result),result_digest=digest(result))

    def test_required_directory_and_budget_completion_and_bounded_scan(self):
        proof = receipts.progress(self.job,self.snapshot,self.principal)
        self.assertEqual(proof["directory"],{"nextOffset":0,"complete":False,"pages":0})
        self.assertTrue(proof["budget"]["required"]);self.reject()
        self.complete()
        proof = receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertEqual(proof["directory"]["pages"],3)
        self.assertEqual(proof["budget"]["pages"],2)
        self.assertEqual(proof["budgetRef"],self.prepared.reference)
        self.query.__getitem__.assert_called_with(slice(None,41,None))

    def test_other_nodes_budget_optional_until_started(self):
        for key in ("commerce","market_b2b"):
            self.job.workflow_node_key=key;self.dispatches.clear();self.results.clear()
            for offset in self.directories: self.add(receipts.DIRECTORY_TOOL,offset)
            self.assertFalse(receipts.validate_complete(self.job,self.snapshot,self.principal)["budget"]["required"])
            self.add(receipts.BUDGET_TOOL,0);self.reject()
            self.add(receipts.BUDGET_TOOL,20)
            self.assertTrue(receipts.validate_complete(self.job,self.snapshot,self.principal)["budget"]["complete"])

    def test_budget_or_analysis_before_directory_fails(self):
        for name in (receipts.BUDGET_TOOL,receipts.TABLE_TOOL):
            self.dispatches.clear();self.results.clear()
            self.add(name,data={"rows":[]});self.reject()

    def test_budget_skip_duplicate_order_and_extra_limit_fail(self):
        for offsets in ([20],[0,0],[20,0],[0,20,0]):
            self.dispatches.clear();self.results.clear()
            for offset in self.directories: self.add(receipts.DIRECTORY_TOOL,offset)
            for offset in offsets: self.add(receipts.BUDGET_TOOL,offset)
            self.reject()
        self.dispatches.clear();self.results.clear();self.complete()
        last=self.dispatches[-1];args=json.loads(last["arguments_json"]);args["limit"]=20
        last.update(arguments_json=canonical(args),arguments_digest=digest(args));self.reject()

    def test_rehashed_forged_full_budget_page_is_not_coverage(self):
        self.complete(); row=self.results[self.dispatches[-1]["id"]]
        original=deepcopy(row)
        for change in (lambda r:r["data"]["rows"][0].update(budgetCents=999999),
                       lambda r:r["data"]["binding"].update(catalogDigest="a"*64),
                       lambda r:r["data"].update(reportId="other"),
                       lambda r:r["data"]["pagination"].update(nextOffset=24)):
            row.update(original)
            def forge(r):
                change(r);r["data"]["pageDigest"]=digest({k:v for k,v in r["data"].items() if k!="pageDigest"})
            self.mutate(row,forge);self.reject()

    def test_failed_unknown_unaudited_and_cross_job_never_count(self):
        self.add(receipts.DIRECTORY_TOOL,0,ok=False)
        self.assertEqual(receipts.progress(self.job,self.snapshot,self.principal)["directory"]["pages"],0)
        self.complete();receipts.validate_complete(self.job,self.snapshot,self.principal)
        last=self.dispatches[-1]
        for state in ("unknown","calling"):
            last["state"]=state
            with self.assertRaises(AiError) as caught: receipts.progress(self.job,self.snapshot,self.principal)
            self.assertEqual(caught.exception.code,"tool_dispatch_unknown")
        last["state"]="succeeded";last["provider_dispatch__job_id"]="sibling";self.reject()
        last["provider_dispatch__job_id"]=self.job.id
        self.mutate(self.results[last["id"]],lambda r:r.update(auditStatus="unavailable"));self.reject()

    def test_hash_size_ordinal_and_missing_success_receipt_fail(self):
        for mutation in ("args","result","ordinal","missing","oversize"):
            self.dispatches.clear();self.results.clear();self.complete()
            d=self.dispatches[-1];r=self.results[d["id"]]
            if mutation=="args": d["arguments_digest"]="0"*64
            if mutation=="result": r["result_digest"]="0"*64
            if mutation=="ordinal": d["tool_call_ordinal"]=40
            if mutation=="missing": self.results.pop(d["id"])
            if mutation=="oversize": r.update(result_json="x"*300000,result_digest=digest("x"*300000))
            self.reject()

    def test_trusted_loader_resolves_once_and_rejects_snapshot_or_job_change(self):
        patch.stopall()
        report=SimpleNamespace(id="report-fixed",owner_email=self.job.owner_email,scope_json="null",snapshot_json=canonical(self.snapshot))
        with patch.object(receipts,"current_principal"),patch.object(receipts.m.AiReportRun.objects,"filter") as reports, \
                patch.object(receipts.store,"load",return_value=self.prepared) as resolve, \
                patch.object(receipts.business_evidence,"get_run",return_value=self.row), \
                patch.object(receipts.evidence_store,"catalog",return_value=self.sources):
            reports.return_value.select_related.return_value.first.return_value=report
            receipts._trusted(self.job,self.snapshot,self.principal)
            resolve.assert_called_once_with(report,self.principal)
            with self.assertRaises(AiError): receipts._trusted(self.job,{**self.snapshot,"extra":True},self.principal)
            self.job.workflow_node_key="other"
            with self.assertRaises(AiError): receipts._trusted(self.job,self.snapshot,self.principal)
