"""Synthetic immutable ledgers; no database, transport or paid model."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from business_analysis import mapping_plan, evidence_v2, budget_reference
from . import business_integrated as contract, business_integrated_tools as tools, business_integrated_receipts as receipts
from . import test_business_budget_receipts as legacy_fixtures
from .business_budget_store import PreparedBudget
from .policy import AiError, canonical, digest


def trusted_fixture(*, with_budget=True, target_count=25, source_count=19):
    row,sources,budget,_,_ = legacy_fixtures.trusted_fixture(target_count,source_count)
    sources[-1] = {"key":"master", "domain":"netshop", "query":{
        "platform":"京东","shop":"店铺0","dataset":"master","startDate":"2026-08-01","endDate":"2026-08-01","window":"current"}}
    catalog = evidence_v2.build_catalog(sources)
    row.plan_json = canonical(catalog["header"])
    plan = mapping_plan.normalize(sources,[{"salesKey":sources[0]["key"],"masterKey":"master"}])
    reference = {**evidence_v2.workflow_reference(sources,run_id=row.id,evidence_version=row.version,sealed_digest="c"*64,question="集成分析"),
        "reportId":"report-fixed","mappingRef":contract.mapping_reference(plan)}
    if with_budget:
        binding = budget_reference.make_binding(budget.plan,report_id="report-fixed",owner_email="owner@example.invalid",scope=None,
            evidence_run_id=row.id,evidence_version=row.version,evidence_plan_digest=catalog["planDigest"],
            catalog_digest=catalog["header"]["catalogDigest"],sealed_digest="c"*64)
        result = {**budget.result,"evidencePlanDigest":catalog["planDigest"]}
        budget = PreparedBudget(budget.id,budget.plan_json,canonical(binding),canonical(result))
        reference["budgetRef"] = budget.reference
    else: budget = None
    snapshot = {**reference,"executionProfile":contract.PROFILE,"mappingPlan":plan,"mappingPlanDigest":digest(plan)}
    prepared = contract.Prepared("owner@example.invalid","null",canonical(snapshot),canonical(reference),budget)
    report = SimpleNamespace(id="report-fixed",workflow_id="flow-fixed",owner_email=prepared.owner_email,
        scope_json="null",snapshot_json=prepared.snapshot_json)
    directories,budgets=tools.expected_pages(prepared,row,sources)
    return report,prepared,row,sources,directories,budgets


class IntegratedReceiptTests(TestCase):
    def setUp(self):
        self.actual,self.prepared,self.row,self.sources,self.directories,self.budgets=trusted_fixture()
        self.job=SimpleNamespace(id="job-fixed",workflow_node_key="promotion",workflow_run_id="flow-fixed",
            owner_email=self.prepared.owner_email,scope_json="null")
        self.principal=SimpleNamespace(email=self.job.owner_email,role="admin",scope=None)
        self.snapshot=self.prepared.snapshot
        self.dispatches,self.results=[],{}
        self.trusted_impl=receipts._trusted
        self.trusted=patch.object(receipts,"_trusted",return_value=(self.actual,self.prepared,self.row,self.sources,self.directories,self.budgets)).start()
        patch.object(contract,"bound").start()
        self.analysis=patch.object(tools,"analysis_from",side_effect=lambda prepared,evidence,sources,args,principal:{"reference":prepared.reference,"arguments":args,"rows":[]}).start()
        self.dispatch_manager=patch.object(receipts.m.AiAgentToolDispatches.objects,"filter").start()
        self.query=self.dispatch_manager.return_value.order_by.return_value.annotate.return_value.values.return_value
        self.query.__getitem__.side_effect=lambda page:[{**d,"argument_bytes":len(d["arguments_json"].encode()),"arguments_text":d["arguments_json"][:8193]} for d in self.dispatches][page]
        patch.object(receipts.m.AiAgentToolResults.objects,"filter",side_effect=self.result_query).start()
        self.addCleanup(patch.stopall)

    def result_query(self,**kwargs):
        query=MagicMock();row=self.results.get(kwargs["tool_dispatch_id"])
        query.annotate.return_value.values.return_value.first.return_value=None if row is None else {
            **row,"result_bytes":len(row["result_json"].encode()),"result_text":row["result_json"][:256*1024+1]}
        return query

    def add(self,name,offset=0,*,data=None,ok=True,state="succeeded",mode="mapped"):
        args={"runId":self.row.id,"reportId":self.actual.id,"offset":offset}
        if name==receipts.TABLE_TOOL:
            args.update(mode=mode,dimension="sku",**({"pairKey":self.prepared.plan["pairs"][0]["pairKey"]} if mode=="mapped" else {"sourceKey":self.sources[0]["key"]}))
            if data is None:data={"reference":self.prepared.reference,"arguments":args,"rows":[]}
        elif data is None:data=(self.directories if name==receipts.DIRECTORY_TOOL else self.budgets)[offset]
        number=len(self.dispatches)+1
        result={"toolName":name,"ok":ok,"auditStatus":"recorded","data":data}
        d={"id":f"dispatch-{number}","job_id":self.job.id,"provider_dispatch__job_id":self.job.id,"tool_call_ordinal":number,
            "tool_name":name,"state":state,"arguments_json":canonical(args),"arguments_digest":digest(args)}
        r={"tool_dispatch_id":d["id"],"result_json":canonical(result),"result_digest":digest(result)}
        self.dispatches.append(d);self.results[d["id"]]=r
        return d,r

    def complete(self):
        for offset in self.directories:self.add(receipts.DIRECTORY_TOOL,offset)
        for offset in self.budgets:self.add(receipts.BUDGET_TOOL,offset)

    def reject(self):
        with self.assertRaises(AiError):receipts.validate_complete(self.job,self.snapshot,self.principal)

    def mutate(self,row,change):
        value=json.loads(row["result_json"]);change(value)
        row.update(result_json=canonical(value),result_digest=digest(value))

    def test_every_job_directory_and_role_specific_budget_and_mapping(self):
        for key in contract.NODES:
            self.job.workflow_node_key=key;self.dispatches.clear();self.results.clear()
            self.reject();self.complete()
            if key in contract.MAPPED_NODES:self.reject()
            self.add(receipts.TABLE_TOOL)
            proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
            self.assertEqual(proof["mapped"]["pages"],1)
            self.assertEqual(proof["mapped"]["required"],key in contract.MAPPED_NODES)
            self.assertEqual(proof["budget"]["required"],key in contract.BUDGET_NODES)
        self.query.__getitem__.assert_called_with(slice(None,41,None))

    def test_no_budget_cannot_fabricate_budget_receipt(self):
        value=trusted_fixture(with_budget=False)
        self.actual,self.prepared,self.row,self.sources,self.directories,self.budgets=value
        self.trusted.return_value=value;self.snapshot=self.prepared.snapshot
        self.complete()
        proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertFalse(proof["budget"]["required"]);self.assertTrue(proof["budget"]["complete"])
        self.add(receipts.BUDGET_TOOL,data={});self.reject()

    def test_optional_budget_becomes_required_once_started(self):
        self.job.workflow_node_key="market_b2b"
        for offset in self.directories:self.add(receipts.DIRECTORY_TOOL,offset)
        receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.add(receipts.BUDGET_TOOL);self.reject()
        for offset in list(self.budgets)[1:]:self.add(receipts.BUDGET_TOOL,offset)
        receipts.validate_complete(self.job,self.snapshot,self.principal)

    def test_every_successful_native_and_mapped_page_recomputed_and_rehashed_forgery_fails(self):
        self.complete()
        for mode in ("native","mapped"):
            _,row=self.add(receipts.TABLE_TOOL,mode=mode)
            receipts.validate_complete(self.job,self.snapshot,self.principal)
            self.mutate(row,lambda r:r["data"].update(rows=[{"amount":999}]))
            self.reject();self.dispatches.pop();self.results.pop(row["tool_dispatch_id"])
        self.assertEqual(self.analysis.call_count,4)

    def test_native_or_failed_mapping_cannot_satisfy_mapped_role(self):
        self.job.workflow_node_key="commerce";self.complete()
        self.add(receipts.TABLE_TOOL,mode="native");self.reject()
        self.add(receipts.TABLE_TOOL,ok=False);self.reject()
        self.add(receipts.TABLE_TOOL);receipts.validate_complete(self.job,self.snapshot,self.principal)

    def test_analysis_before_full_directory_and_wrong_report_reject(self):
        self.add(receipts.TABLE_TOOL);self.reject();self.analysis.assert_not_called()
        self.dispatches.clear();self.results.clear();self.complete()
        d,_=self.add(receipts.TABLE_TOOL);args=json.loads(d["arguments_json"]);args["reportId"]="other"
        d.update(arguments_json=canonical(args),arguments_digest=digest(args));self.reject()

    def test_directory_and_budget_sequence_and_rehashed_page_binding(self):
        self.complete();last=self.results[self.dispatches[-1]["id"]]
        self.mutate(last,lambda r:r["data"]["reference"].update(mappingRef={"planDigest":"a"*64}));self.reject()
        self.dispatches.clear();self.results.clear()
        self.add(receipts.DIRECTORY_TOOL);self.add(receipts.DIRECTORY_TOOL);self.reject()

    def test_unknown_cross_job_hash_ordinal_size_and_late_owner_failure(self):
        self.complete();d=self.dispatches[-1];r=self.results[d["id"]]
        for state in ("unknown","calling"):
            d["state"]=state
            with self.assertRaises(AiError) as caught:receipts.progress(self.job,self.snapshot,self.principal)
            self.assertEqual(caught.exception.code,"tool_dispatch_unknown")
        d["state"]="succeeded"
        for key,value in (("provider_dispatch__job_id","other"),("arguments_digest","0"*64),("tool_call_ordinal",40)):
            old=d[key];d[key]=value;self.reject();d[key]=old
        old=deepcopy(r);r.update(result_json="x"*300000,result_digest=digest("x"*300000));self.reject();r.update(old)
        with patch.object(contract,"bound",side_effect=AiError("revoked","access_denied",403)):
            self.reject()

    def test_loader_resolves_once_and_matches_persisted_snapshot(self):
        with patch.object(receipts,"current_principal"),patch.object(receipts.m.AiReportRun.objects,"filter") as manager, \
                patch.object(tools,"prepare_for_report",return_value=(self.actual,self.prepared,self.row,self.sources)) as resolve:
            manager.return_value.select_related.return_value.first.return_value=self.actual
            self.trusted_impl(self.job,self.snapshot,self.principal)
            resolve.assert_called_once_with(self.actual,self.principal,resolve_budget=True)
            with self.assertRaises(AiError):self.trusted_impl(self.job,{**self.snapshot,"extra":True},self.principal)
            self.job.workflow_node_key="foreign"
            with self.assertRaises(AiError):self.trusted_impl(self.job,self.snapshot,self.principal)

    def test_missing_receipts_audit_boolean_and_failed_results_never_grant_coverage(self):
        for mutation in ("missing", "hash", "audit", "ok_integer", "failed_success", "arguments_size"):
            self.dispatches.clear();self.results.clear();self.complete()
            d=self.dispatches[-1];r=self.results[d["id"]]
            if mutation=="missing":self.results.pop(d["id"])
            if mutation=="hash":r["result_digest"]="f"*64
            if mutation=="audit":self.mutate(r,lambda value:value.update(auditStatus="missing"))
            if mutation=="ok_integer":self.mutate(r,lambda value:value.update(ok=1))
            if mutation=="failed_success":d["state"]="failed"
            if mutation=="arguments_size":d.update(arguments_json="x"*9000,arguments_digest=digest("x"*9000))
            self.reject()
        self.dispatches.clear();self.results.clear()
        d,_=self.add(receipts.DIRECTORY_TOOL,state="failed",ok=False)
        self.results.pop(d["id"])
        self.assertEqual(receipts.progress(self.job,self.snapshot,self.principal)["directory"]["pages"],0)

    def test_dispatch_count_and_exact_scalar_offset(self):
        self.dispatches[:]=[{"arguments_json":"{}"} for _ in range(41)]
        self.reject()
        for offset in (True,0.0,"0"):
            self.dispatches.clear();self.results.clear()
            d,_=self.add(receipts.DIRECTORY_TOOL)
            args=json.loads(d["arguments_json"]);args["offset"]=offset
            d.update(arguments_json=canonical(args),arguments_digest=digest(args))
            self.reject()
