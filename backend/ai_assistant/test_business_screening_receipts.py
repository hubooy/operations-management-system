"""Pure ledger projections with real five-role codecs, not owning admission.

Database loading, actual tool authorization and arithmetic are explicit mock
boundaries here. Root's separate PostgreSQL tests cover those real bindings.
These tests certify ledger validation behavior, never model execution or facts.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from . import business_screening_receipts as receipts, business_screening_runtime_contract as contract
from . import test_business_screening_preflight as fixtures
from .policy import AiError, canonical, digest


class ScreeningReceiptTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fixed = fixtures.fixture(rich=False,rows=2,with_budget=True,already_proposed=True)
        cls.unbudgeted = fixtures.fixture(rich=False,rows=2,with_budget=False,already_proposed=True)

    def setUp(self):
        self.principal=SimpleNamespace(email="synthetic@example.invalid",role="admin",scope=None)
        self.dispatches,self.results=[],{}
        self.set_fixture(self.fixed)
        self.trusted_impl=receipts._trusted
        self.trusted=patch.object(receipts,"_trusted",side_effect=lambda *args:self.trusted_value()).start()
        self.bound=patch.object(receipts.runtime,"bound").start()
        self.analysis=patch.object(receipts.tools,"analysis_from",side_effect=self.recomputed_analysis).start()
        self.dispatch_manager=patch.object(receipts.m.AiAgentToolDispatches.objects,"filter").start()
        self.query=self.dispatch_manager.return_value.order_by.return_value.annotate.return_value.values.return_value
        self.query.__getitem__.side_effect=lambda page:[{**d,"argument_bytes":len(d["arguments_json"].encode()),
            "arguments_text":d["arguments_json"][:receipts.MAX_ARGUMENT_BYTES+1]} for d in self.dispatches][page]
        patch.object(receipts.m.AiAgentToolResults.objects,"filter",side_effect=self.result_query).start()
        self.provider=patch("ai_assistant.provider.turn").start()
        self.transport=patch("ai_assistant.transport.execute_tool").start()
        self.addCleanup(patch.stopall)

    def set_fixture(self, fixture):
        all_pages,reference,budgets=deepcopy(fixture)
        self.reference=reference["workflowInput"]
        self.all_pages={role:{p["pagination"]["offset"]:p for p in pages} for role,pages in all_pages.items()}
        self.budgets={}
        for page in budgets:
            wrapper={"schemaVersion":contract.BUDGET_PAGE_SCHEMA,"reference":self.reference,"budget":page}
            wrapper["pageDigest"]=digest(wrapper)
            self.budgets[page["pagination"]["offset"]]=wrapper
        self.snapshot={"schemaVersion":"business-report-v1","executionProfile":contract.PROFILE,
            "evidenceProtocol":"reference-v2",**{k:v for k,v in self.reference.items() if k!="inputMode"}}
        self.graph=contract.graph(bool(budgets))
        self.report=SimpleNamespace(id=self.reference["reportId"],workflow_id="flow-fixed",owner_email=self.principal.email,
            scope_json="null",snapshot_json=canonical(self.snapshot),budget_plan_id="budget-synthetic" if budgets else None,
            workflow=SimpleNamespace(graph_json=canonical(self.graph),graph_digest=digest(self.graph),dry_run=False,
                input_json=canonical(self.reference),allowed_tools_json=canonical(sorted(contract.TOOLS)),
                model_id="model-fixed",model_version=1,tool_policy_digest="c"*64))
        # Loader return is a deliberate test boundary, not a forged private
        # Prepared instance or an assertion that these synthetic rows exist.
        self.prepared=SimpleNamespace(reference=self.reference,snapshot=self.snapshot)
        self.job=SimpleNamespace(id="job-fixed",workflow_run_id="flow-fixed",workflow_node_key="promotion",
            owner_email=self.principal.email,scope_json="null",allowed_tools_json=canonical(sorted(contract.TOOLS)),
            model_id="model-fixed",model_version=1,tool_policy_digest="c"*64)
        self.select_role("promotion")

    def select_role(self,role):
        self.job.workflow_node_key=role
        node=next(n for n in self.graph["nodes"] if n["key"]==role)
        self.job.task=node["instruction"]
        self.job.input_json=canonical({"workflowInput":self.reference,"dependencies":{key:{"answer":"synthetic prior answer"} for key in node["dependsOn"]}})
        self.node=SimpleNamespace(node_type="agent",instruction=node["instruction"],depends_on_json=canonical(node["dependsOn"]),input_json=self.job.input_json)

    def trusted_value(self):
        return self.report,self.prepared,self.all_pages[self.job.workflow_node_key],self.budgets

    def result_query(self,**kwargs):
        row=self.results.get(kwargs["tool_dispatch_id"]);query=MagicMock()
        query.annotate.return_value.values.return_value.first.return_value=None if row is None else {
            **row,"result_bytes":len(row["result_json"].encode()),"result_text":row["result_json"][:receipts.MAX_RESULT_BYTES+1]}
        return query

    def recomputed_analysis(self,prepared,args,principal):
        # This is an owning-calculation boundary value. The tests check that
        # every successful ledger result is compared with this fresh result,
        # not the numerical correctness of native/mapped algorithms.
        return {"schemaVersion":"business-screening-analysis-v1","reference":prepared.reference,"mode":args["mode"],
            "selector":{k:v for k,v in args.items() if k in {"sourceKey","baselineKey","pairKey","baselinePairKey","dimension"}},
            "table":{"rows":[{"rowId":"a"*64,"metrics":{"netSalesCents":{"value":1700}}}],
                "pagination":{"offset":args.get("offset",0),"nextOffset":None}}}

    def add(self,name,offset=0,*,data=None,ok=True,state="succeeded",mode="native",args_extra=None):
        args={"runId":self.reference["evidenceRunId"],"reportId":self.report.id,
            "screeningId":self.reference["screeningIntent"]["id"],"offset":offset}
        if name==contract.PACKAGE_TOOL:
            args["role"]=self.job.workflow_node_key
            if data is None:data=self.all_pages[self.job.workflow_node_key][offset]
        elif name==contract.BUDGET_TOOL:
            if data is None:data=self.budgets[offset]
        else:
            args.update(mode=mode,dimension="sku",**({"sourceKey":"sales"} if mode=="native" else {"pairKey":"b"*64}))
            if data is None:data=self.recomputed_analysis(self.prepared,args,self.principal)
        if args_extra:args.update(args_extra)
        ordinal=len(self.dispatches)+1
        d={"id":f"dispatch-{ordinal}","job_id":self.job.id,"provider_dispatch__job_id":self.job.id,
            "tool_call_ordinal":ordinal,"tool_name":name,"state":state,"arguments_json":canonical(args),"arguments_digest":digest(args)}
        value={"toolName":name,"ok":ok,"auditStatus":"recorded","data":deepcopy(data)}
        row={"tool_dispatch_id":d["id"],"result_json":canonical(value),"result_digest":digest(value)}
        self.dispatches.append(d);self.results[d["id"]]=row
        return d,row

    def clear(self):self.dispatches.clear();self.results.clear()

    def complete_package(self):
        for offset in self.all_pages[self.job.workflow_node_key]:self.add(contract.PACKAGE_TOOL,offset)

    def complete(self):
        self.complete_package()
        if self.job.workflow_node_key in contract.BUDGET_NODES:
            for offset in self.budgets:self.add(contract.BUDGET_TOOL,offset)

    def reject(self,code=None):
        with self.assertRaises(AiError) as caught:receipts.validate_complete(self.job,self.snapshot,self.principal)
        if code:self.assertEqual(caught.exception.code,code)

    def mutate_result(self,row,change):
        value=json.loads(row["result_json"]);change(value)
        row.update(result_json=canonical(value),result_digest=digest(value))

    def mutate_args(self,dispatch,change):
        args=json.loads(dispatch["arguments_json"]);change(args)
        dispatch.update(arguments_json=canonical(args),arguments_digest=digest(args))

    def test_five_roles_independently_require_complete_own_package_and_selected_budget(self):
        digests={}
        for role in self.all_pages:
            self.select_role(role);self.clear();self.reject()
            self.complete()
            proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
            self.assertEqual(proof["role"],role);self.assertEqual(proof["jobId"],self.job.id)
            self.assertTrue(proof["package"]["complete"])
            self.assertEqual(proof["package"]["pages"],len(self.all_pages[role]))
            self.assertEqual(proof["budget"]["required"],role in contract.BUDGET_NODES)
            self.assertEqual(proof["analysisPages"],0)  # No inherited required mapped page.
            digests[role]=proof["packageDigest"]
        self.assertEqual(len(set(digests.values())),5)
        self.query.__getitem__.assert_called_with(slice(None,41,None))
        self.provider.assert_not_called();self.transport.assert_not_called()

    def test_missing_duplicate_out_of_order_and_cross_role_offsets_fail(self):
        self.select_role("commerce")
        offsets=list(self.all_pages["commerce"])
        self.assertGreater(len(offsets),1)
        self.add(contract.PACKAGE_TOOL,offsets[1]);self.reject()
        self.clear();self.add(contract.PACKAGE_TOOL);self.add(contract.PACKAGE_TOOL);self.reject()
        self.clear();self.add(contract.PACKAGE_TOOL);self.reject()
        for invalid in (True,0.0,"0",-1):
            self.clear();d,_=self.add(contract.PACKAGE_TOOL)
            self.mutate_args(d,lambda args:args.update(offset=invalid));self.reject()
        for role in ("promotion","report",None):
            self.clear();d,_=self.add(contract.PACKAGE_TOOL)
            self.mutate_args(d,lambda args:args.update(role=role));self.reject()
        self.clear();self.add(contract.TABLE_TOOL);self.reject();self.analysis.assert_not_called()
        self.clear();self.add(contract.BUDGET_TOOL);self.reject()

    def test_read_pages_and_receipts_cannot_cross_job_provider_report_or_ready(self):
        for field,value in (("job_id","other"),("provider_dispatch__job_id","other"),("tool_call_ordinal",True),
                ("tool_call_ordinal",2),("tool_name","get_business_integrated_directory_v1")):
            self.clear();self.complete();self.dispatches[0][field]=value;self.reject()
        for field in ("runId","reportId","screeningId"):
            self.clear();self.complete();self.mutate_args(self.dispatches[0],lambda args:args.update({field:"other"}));self.reject()
        self.clear();self.complete();self.results[self.dispatches[0]["id"]]["tool_dispatch_id"]="other";self.reject()

    def test_rehashed_payload_page_and_nested_type_tampering_is_not_a_proof(self):
        for change in ("record","page_hash","float","extra"):
            self.clear();self.complete();row=self.results[self.dispatches[0]["id"]]
            def alter(value):
                page=value["data"]
                if change=="record":page["records"][0][1]["query"]["shop"]="different"
                elif change=="float":page["pagination"]["offset"]=0.0
                elif change=="extra":page["extra"]="forged"
                else:page["packageDigest"]="0"*64
                page["pageDigest"]=digest({k:v for k,v in page.items() if k!="pageDigest"})
            self.mutate_result(row,alter);self.reject()
        self.clear();self.complete()
        self.mutate_result(self.results[self.dispatches[0]["id"]],lambda value:value.update(data=next(iter(self.all_pages["commerce"].values()))))
        self.reject()

    def test_optional_budget_once_successfully_started_must_finish(self):
        self.select_role("commerce");self.complete_package()
        proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertFalse(proof["budget"]["required"]);self.assertFalse(proof["budget"]["started"])
        offsets=list(self.budgets);self.assertGreater(len(offsets),1)
        self.add(contract.BUDGET_TOOL,offsets[0]);self.reject()
        for offset in offsets[1:]:self.add(contract.BUDGET_TOOL,offset)
        self.assertTrue(receipts.validate_complete(self.job,self.snapshot,self.principal)["budget"]["complete"])
        self.mutate_result(self.results[self.dispatches[-1]["id"]],lambda value:value["data"]["budget"]["rows"][0].update(rowIndex=99))
        self.reject()
        self.set_fixture(self.unbudgeted);self.clear();self.complete_package()
        proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertFalse(proof["budget"]["required"])
        self.add(contract.BUDGET_TOOL,data={});self.reject()

    def test_known_failure_is_retained_unknown_blocks_and_no_tool_is_replayed(self):
        self.select_role("commerce")
        failed,_=self.add(contract.PACKAGE_TOOL,ok=False,state="failed")
        self.results.pop(failed["id"])
        proof=receipts.progress(self.job,self.snapshot,self.principal)
        self.assertEqual(proof["package"]["pages"],0)
        self.complete_package()
        original=deepcopy((self.dispatches,self.results))
        self.assertTrue(receipts.validate_complete(self.job,self.snapshot,self.principal)["package"]["complete"])
        self.assertEqual((self.dispatches,self.results),original)
        for state in ("calling","unknown"):
            failed["state"]=state;self.reject("tool_dispatch_unknown")
        self.provider.assert_not_called();self.transport.assert_not_called()

    def test_audit_receipt_state_hash_byte_and_scalar_boundaries(self):
        for change in ("missing","bad_hash","no_audit","integer_ok","failed_success","args_size","result_size"):
            self.clear();self.complete();d=self.dispatches[0];row=self.results[d["id"]]
            if change=="missing":self.results.pop(d["id"])
            elif change=="bad_hash":row["result_digest"]="0"*64
            elif change=="no_audit":self.mutate_result(row,lambda value:value.update(auditStatus="missing"))
            elif change=="integer_ok":self.mutate_result(row,lambda value:value.update(ok=1))
            elif change=="failed_success":d["state"]="failed"
            elif change=="args_size":d.update(arguments_json="x"*9000,arguments_digest=digest("x"*9000))
            else:row.update(result_json="x"*300000,result_digest=digest("x"*300000))
            with self.subTest(change=change):self.reject()
        self.clear();self.dispatches.extend({"arguments_json":"{}"} for _ in range(41));self.reject()

    def test_successful_native_and_mapped_analysis_each_delegate_recalculation(self):
        self.select_role("commerce");self.complete_package()
        for mode in ("native","mapped"):
            self.add(contract.TABLE_TOOL,mode=mode)
        proof=receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertEqual(proof["analysisPages"],2)
        self.assertEqual([call.args[1]["mode"] for call in self.analysis.call_args_list],["native","mapped"])
        self.mutate_result(self.results[self.dispatches[-1]["id"]],lambda value:value["data"]["table"]["rows"][0]["metrics"]["netSalesCents"].update(value=9999))
        self.reject()
        self.analysis.side_effect=AiError("owning source revoked","access_denied",403)
        with self.assertRaises(AiError) as error:receipts.progress(self.job,self.snapshot,self.principal)
        self.assertEqual(error.exception.status,403)

    def test_per_tool_cap_counts_failed_dispatches_as_well_as_success(self):
        self.select_role("commerce");self.complete_package()
        for _ in range(8):self.add(contract.TABLE_TOOL)
        self.assertEqual(receipts.validate_complete(self.job,self.snapshot,self.principal)["analysisPages"],8)
        self.add(contract.TABLE_TOOL);self.reject()
        self.clear()
        for _ in range(5):
            d,_=self.add(contract.PACKAGE_TOOL,ok=False,state="failed")
            self.results.pop(d["id"])
        self.complete_package()  # 5 failed + 4 successful pages exceeds 8.
        self.reject()

    def test_late_permission_failure_never_returns_proof_and_preserves_ledger(self):
        self.complete();before=deepcopy((self.dispatches,self.results))
        self.bound.side_effect=AiError("late denied","access_denied",403)
        with self.assertRaises(AiError) as error:receipts.validate_complete(self.job,self.snapshot,self.principal)
        self.assertEqual(error.exception.status,403);self.assertEqual((self.dispatches,self.results),before)

    def test_loader_uses_actual_job_node_exact_graph_and_one_owning_preparation(self):
        actual_job=deepcopy(self.job)
        with patch.object(receipts,"current_principal"),patch.object(receipts,"authorize_owner"), \
                patch.object(receipts.m.AiAgentJobs.objects,"filter") as jobs, \
                patch.object(receipts.m.AiReportRun.objects,"filter") as reports, \
                patch.object(receipts.m.AiWorkflowNodeRuns.objects,"filter") as nodes, \
                patch.object(receipts.tools,"prepare_for_report",return_value=self.prepared) as prepare, \
                patch.object(receipts.tools,"expected_pages",return_value=(self.all_pages[self.job.workflow_node_key],self.budgets)) as expected:
            jobs.return_value.first.return_value=actual_job
            reports.return_value.select_related.return_value.first.return_value=self.report
            nodes.return_value.first.return_value=self.node
            self.trusted_impl(self.job,self.snapshot,self.principal)
            jobs.assert_called_once_with(pk=self.job.id)
            nodes.assert_called_once_with(run_id=self.job.workflow_run_id,node_key=self.job.workflow_node_key,agent_job_id=self.job.id)
            prepare.assert_called_once_with(self.report,self.principal,resolve_budget=True)
            expected.assert_called_once_with(self.prepared,self.job.workflow_node_key,self.principal)
            for target,field,bad in ((actual_job,"owner_email","other@example.invalid"),(actual_job,"workflow_run_id","other-flow"),
                    (actual_job,"workflow_node_key","commerce"),(actual_job,"task","forged task"),(actual_job,"allowed_tools_json","[]"),
                    (self.report,"snapshot_json","{}"),(self.report.workflow,"graph_json","{}"),
                    (self.report.workflow,"graph_digest","0"*64),(self.report.workflow,"dry_run",True),
                    (actual_job,"model_id","other-model"),(actual_job,"model_version",2),(actual_job,"tool_policy_digest","d"*64),
                    (actual_job,"input_json","{}"),(self.node,"input_json","{}"),(self.report.workflow,"input_json","{}"),
                    (actual_job,"allowed_tools_json",canonical({name:1 for name in contract.TOOLS})),
                    (self.report.workflow,"allowed_tools_json",canonical({name:1 for name in contract.TOOLS})),
                    (self.node,"node_type","human_review"),(self.node,"instruction","foreign"),(self.node,"depends_on_json","[\"other\"]")):
                old=getattr(target,field);setattr(target,field,bad)
                with self.subTest(field=field),self.assertRaises(AiError):self.trusted_impl(self.job,self.snapshot,self.principal)
                setattr(target,field,old)
            jobs.return_value.first.return_value=None
            with self.assertRaises(AiError):self.trusted_impl(self.job,self.snapshot,self.principal)

    def test_loader_late_preparation_or_expected_page_failure_does_not_forge_authority(self):
        self.trusted.side_effect=AiError("ready or role binding changed","conflict",409)
        with self.assertRaises(AiError):receipts.progress(self.job,self.snapshot,self.principal)
        self.dispatch_manager.assert_not_called()
