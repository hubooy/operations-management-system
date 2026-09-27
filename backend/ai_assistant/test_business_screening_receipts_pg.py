"""Actual ready storage and immutable synthetic ledgers, without dispatch.

These tests verify owning proof reconstruction, not a completed model run or
the future scheduler. No ledger trigger is disabled to construct negatives.
"""
from copy import deepcopy
import json
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from . import test_business_screening_runtime_guard as fixtures
from . import business_screening_runtime_contract as contract, business_screening_receipts as receipts
from . import business_screening_tools as tools, business_screening_store as store
from . import business_diagnostic_screening as screening, models as m
from .policy import AiError, canonical, digest, mutation, uid


@override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningReceiptDatabaseTests(TransactionTestCase):
    user = fixtures.ScreeningRuntimeGuardTests.user
    call = fixtures.ScreeningRuntimeGuardTests.call
    collect_body = fixtures.ScreeningRuntimeGuardTests.collect_body
    bundle = fixtures.ScreeningRuntimeGuardTests.bundle
    input_for = fixtures.ScreeningRuntimeGuardTests.input_for
    insert = fixtures.ScreeningRuntimeGuardTests.insert
    seed = fixtures.ScreeningRuntimeGuardTests.seed
    setUp = fixtures.ScreeningRuntimeGuardTests.setUp
    screening_bundle = fixtures.ScreeningRuntimeGuardTests.screening_bundle
    insert_screening = fixtures.ScreeningRuntimeGuardTests.insert_screening

    def ready(self, *, budget=False):
        bundle = self.screening_bundle(mapped=True,budget=budget)
        with mutation(self.admin):
            report = self.insert_screening(bundle,flow_changes={"dry_run":0,"allowed_tools_json":canonical(sorted(contract.TOOLS)),
                "model_id":"synthetic-screening-model","model_version":1,"tool_policy_digest":"b"*64})
        store.publish(screening.prepare_for_report(report.id,self.admin),self.admin)
        return report,tools.prepare_for_report(report,self.admin,resolve_budget=budget)

    def job(self,report,role):
        graph = contract.graph(bool(report.budget_plan_id))
        spec = next(n for n in graph["nodes"] if n["key"]==role)
        data = {"workflowInput":json.loads(report.workflow.input_json),
            "dependencies":{key:{"answer":"合成依赖占位，不是模型结论"} for key in spec["dependsOn"]}}
        with mutation(self.admin):
            job = m.AiAgentJobs.objects.create(id=uid("receipt-job"),owner_email=self.admin.email,scope_json="null",
                client_request_id=uid("receipt-client"),request_digest=digest(data),task=spec["instruction"],input_json=canonical(data),
                workflow_run_id=report.workflow_id,workflow_node_key=role,allowed_tools_json=report.workflow.allowed_tools_json,
                model_id=report.workflow.model_id,model_version=report.workflow.model_version,tool_policy_digest=report.workflow.tool_policy_digest)
            m.AiWorkflowNodeRuns.objects.create(id=uid("receipt-node"),run=report.workflow,node_key=role,
                position=next(i for i,n in enumerate(graph["nodes"]) if n["key"]==role),node_type="agent",
                instruction=spec["instruction"],depends_on_json=canonical(spec["dependsOn"]),input_json=canonical(data),agent_job=job)
        return job

    def append(self,job,name,args,data,*,state="succeeded"):
        ordinal = m.AiAgentToolDispatches.objects.filter(job=job).count()+1
        with mutation(self.admin):
            dispatch = m.AiAgentProviderDispatches.objects.create(id=uid("receipt-provider"),job=job,
                dispatch_ordinal=ordinal,owner_email=self.admin.email,actor_role="admin",model_id=job.model_id,
                model_version=job.model_version,tool_policy_digest=job.tool_policy_digest,request_digest=digest(args),
                lease_epoch=1,state="succeeded")
            tool = m.AiAgentToolDispatches.objects.create(id=uid("receipt-tool"),job=job,provider_dispatch=dispatch,
                tool_call_ordinal=ordinal,provider_call_id="synthetic-"+str(ordinal),tool_name=name,
                arguments_json=canonical(args),arguments_digest=digest(args),invocation_id=uid("receipt-invocation"),lease_epoch=1,state=state)
            result = {"toolName":name,"ok":True,"auditStatus":"recorded","data":data}
            return m.AiAgentToolResults.objects.create(tool_dispatch=tool,result_json=canonical(result),result_digest=digest(result))

    def read_role(self,report,prepared,job,*,budgets=False):
        pages,budget_pages = tools.expected_pages(prepared,job.workflow_node_key,self.admin)
        args = {"runId":prepared.reference["evidenceRunId"],"reportId":report.id,
            "screeningId":prepared.reference["screeningIntent"]["id"]}
        for offset,page in pages.items():
            self.append(job,contract.PACKAGE_TOOL,{**args,"role":job.workflow_node_key,"offset":offset},page)
        if budgets:
            for offset,page in budget_pages.items(): self.append(job,contract.BUDGET_TOOL,{**args,"offset":offset},page)
        return args

    def test_actual_five_jobs_must_read_their_own_complete_packages(self):
        report,prepared = self.ready()
        snapshot = prepared.snapshot
        with patch("ai_assistant.provider.turn") as provider,patch("ai_assistant.transport.execute_tool") as transport:
            for role in contract.OUTPUT_LIMITS:
                job = self.job(report,role)
                with self.assertRaises(AiError): receipts.validate_complete(job,snapshot,self.admin)
                self.read_role(report,prepared,job)
                proof = receipts.validate_complete(job,snapshot,self.admin)
                self.assertEqual((proof["jobId"],proof["role"]),(job.id,role))
                self.assertTrue(proof["package"]["complete"])
                self.assertTrue(proof["authority"]["executedTablesComplete"])
                self.assertFalse(proof["authority"]["entityDailyCoverageVerified"])
            provider.assert_not_called(); transport.assert_not_called()
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(),5)

    def test_actual_budget_requirement_and_forged_known_page_preserve_receipts(self):
        report,prepared = self.ready(budget=True)
        promotion = self.job(report,"promotion")
        args = self.read_role(report,prepared,promotion)
        with self.assertRaises(AiError): receipts.validate_complete(promotion,prepared.snapshot,self.admin)
        _,budget_pages = tools.expected_pages(prepared,"promotion",self.admin)
        for offset,page in budget_pages.items(): self.append(promotion,contract.BUDGET_TOOL,{**args,"offset":offset},page)
        self.assertTrue(receipts.validate_complete(promotion,prepared.snapshot,self.admin)["budget"]["complete"])
        reviewer = self.job(report,"independent_review")
        with self.assertRaises(AiError): receipts.validate_complete(reviewer,prepared.snapshot,self.admin)
        pages,_ = tools.expected_pages(prepared,"independent_review",self.admin)
        original = deepcopy(next(iter(pages.values())))
        original["packageDigest"] = "0"*64
        original["pageDigest"] = digest({k:v for k,v in original.items() if k!="pageDigest"})
        saved = self.append(reviewer,contract.PACKAGE_TOOL,{**args,"role":"independent_review","offset":0},original)
        before = (saved.result_json,saved.result_digest)
        with patch("ai_assistant.provider.turn") as provider,patch("ai_assistant.transport.execute_tool") as transport:
            for _ in range(2):
                with self.assertRaises(AiError): receipts.validate_complete(reviewer,prepared.snapshot,self.admin)
            provider.assert_not_called(); transport.assert_not_called()
        saved.refresh_from_db()
        self.assertEqual((saved.result_json,saved.result_digest),before)
        self.assertEqual(saved.tool_dispatch.state,"succeeded")
