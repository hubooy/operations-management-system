"""Actual fixed nodes and receipt prefixes; no provider or tool is dispatched."""
from datetime import timedelta
import json
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from . import business_screening_execution as service, business_screening_permission as permission
from . import business_screening_runtime_contract as contract, business_parallel, workflows, models as m, transport
from . import test_business_screening_admission as fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation, uid


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ScreeningExecutionTests(TransactionTestCase):
    user=fixtures.ScreeningAdmissionTests.user
    call=fixtures.ScreeningAdmissionTests.call
    collect_body=fixtures.ScreeningAdmissionTests.collect_body
    bundle=fixtures.ScreeningAdmissionTests.bundle
    input_for=fixtures.ScreeningAdmissionTests.input_for
    insert=fixtures.ScreeningAdmissionTests.insert
    seed=fixtures.ScreeningAdmissionTests.seed
    screening_bundle=fixtures.ScreeningAdmissionTests.screening_bundle
    insert_screening=fixtures.ScreeningAdmissionTests.insert_screening
    ready=fixtures.ScreeningAdmissionTests.ready

    def setUp(self):
        fixtures.ScreeningAdmissionTests.setUp(self)
        permission.clear();self.addCleanup(permission.clear)
        self.network=patch.object(transport,"catalog",return_value=fixtures.catalog()).start()
        self.addCleanup(patch.stopall)

    def running(self, **options):
        report=self.ready(**options)
        graph=workflows.validate_graph(contract.graph(bool(report.budget_plan_id)))
        # Fixture unpublished negatives construct no ready permit and create
        # their synthetic child explicitly below.
        permit=permission.get(report,self.admin) if options.get("publish",True) else None
        with mutation(self.admin):
            for index,node in enumerate(graph["nodes"]):
                m.AiWorkflowNodeRuns.objects.create(id=uid("step-node"),run_id=report.workflow_id,position=index,
                    node_key=node["key"],node_type=node["type"],instruction=node["instruction"],depends_on_json=canonical(node["dependsOn"]))
            row=m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            if permit is not None:
                business_parallel.workflow_step(row,self.admin,list(m.AiWorkflowNodeRuns.objects.filter(run=row).order_by("position")),screening_permission=permit)
            else:
                node=m.AiWorkflowNodeRuns.objects.get(run=row,node_key="commerce")
                data=canonical({"workflowInput":json.loads(row.input_json),"dependencies":{}})
                child=m.AiAgentJobs.objects.create(id=uid("unready-child"),owner_email=row.owner_email,scope_json=row.scope_json,
                    client_request_id=uid("unready-client"),request_digest=digest(data),task=node.instruction,input_json=data,
                    workflow_run_id=row.id,workflow_node_key=node.node_key,model_id=row.model_id,model_version=row.model_version,
                    allowed_tools_json=row.allowed_tools_json,tool_policy_digest=row.tool_policy_digest)
                node.agent_job=child;node.status="running";node.input_json=data;node.save()
            job=m.AiAgentJobs.objects.get(workflow_run_id=row.id,workflow_node_key="commerce")
            job.status="running";job.lease_token=uid("step-lease");job.lease_epoch=1
            job.lease_expires_at=timezone.now()+timedelta(seconds=240);job.version+=1;job.save()
        return report,job

    def provider_receipt(self,job,*,state="succeeded"):
        with mutation(self.admin):
            dispatched=m.AiAgentProviderDispatches.objects.create(id=uid("step-provider"),job=job,
                dispatch_ordinal=m.AiAgentProviderDispatches.objects.filter(job=job).count()+1,
                owner_email=job.owner_email,actor_role="admin",model_id=job.model_id,model_version=job.model_version,
                tool_policy_digest=job.tool_policy_digest,request_digest="a"*64,lease_epoch=job.lease_epoch,state=state)
            if state=="succeeded":
                value={"text":"", "calls":[], "frame":{"role":"assistant","content":""}}
                m.AiAgentProviderResults.objects.create(dispatch=dispatched,response_json=canonical(value),response_digest=digest(value))
        return dispatched

    def test_short_transaction_check_does_not_repeat_facts_catalog_or_proof(self):
        report,job=self.running(mapped=True,budget=True)
        context=service.prepare(job,self.admin)
        ref=json.loads(job.input_json)["workflowInput"]
        args={"runId":ref["evidenceRunId"],"reportId":report.id,"screeningId":ref["screeningIntent"]["id"],"role":"commerce","offset":0}
        service.validate_call(context,{"id":"one","name":contract.PACKAGE_TOOL,"arguments":args})
        for changed in ({**args,"role":"report"},{**args,"offset":1},{**args,"screeningId":"different"}):
            with self.assertRaises(AiError):service.validate_call(context,{"id":"one","name":contract.PACKAGE_TOOL,"arguments":changed})
        with self.assertRaises(AiError):service.validate_call(context,{"id":"one","name":contract.TABLE_TOOL,"arguments":args})
        with (patch.object(Reader,"pages",side_effect=AssertionError("no facts in commit")),
                patch.object(transport,"catalog",side_effect=AssertionError("no network in commit")),
                patch.object(service.receipts,"progress",side_effect=AssertionError("no proof reconstruction in commit")),mutation(self.admin)):
            self.assertEqual(service.check(context,job,self.admin),context.proof)
        self.assertFalse(context.proof["package"]["complete"])
        self.assertEqual(m.AiAgentProviderDispatches.objects.filter(job=job).count(),0)

    def test_ledger_extension_must_be_the_current_steps_exact_dispatch(self):
        _,job=self.running()
        context=service.prepare(job,self.admin)
        one=self.provider_receipt(job,state="calling")
        with self.assertRaises(AiError):service.check(context,job,self.admin)
        service.check(context,job,self.admin,dispatch=one)
        with self.assertRaises(AiError) as error:service.prepare(job,self.admin)
        self.assertEqual(error.exception.code,"provider_dispatch_unknown")
        self.provider_receipt(job)
        with self.assertRaises(AiError):service.check(context,job,self.admin,dispatch=one)

    def test_actual_parent_cancel_node_tampering_or_stale_epoch_blocks_commit(self):
        for change in ("cancel","node","epoch"):
            report,job=self.running()
            context=service.prepare(job,self.admin)
            with mutation(self.admin):
                if change=="cancel":m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(cancel_requested=1)
                elif change=="node":m.AiWorkflowNodeRuns.objects.filter(agent_job=job).update(instruction="changed")
                else:m.AiAgentJobs.objects.filter(pk=job.id).update(lease_epoch=job.lease_epoch+1)
            with self.assertRaises(AiError):service.check(context,job,self.admin)
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(),0)

    def test_unpublished_report_and_public_proof_cannot_prepare_or_authorize(self):
        _,job=self.running(publish=False)
        with self.assertRaises(AiError):service.prepare(job,self.admin)
        with self.assertRaises(AiError):service.check({"capacityVerified":True},job,self.admin)
        self.assertEqual(m.AiAgentProviderDispatches.objects.count(),0)

    def test_ignored_unknown_and_changed_provider_policy_are_not_safe_prefixes(self):
        _,job=self.running()
        context=service.prepare(job,self.admin)
        dispatched=self.provider_receipt(job,state="calling")
        with mutation(self.admin):m.AiAgentProviderDispatches.objects.filter(pk=dispatched.pk).update(state="unknown")
        with self.assertRaises(AiError):service.check(context,job,self.admin,dispatch=dispatched)
        _,second=self.running()
        known=self.provider_receipt(second)
        context=service.prepare(second,self.admin)
        with mutation(self.admin):m.AiAgentProviderDispatches.objects.filter(pk=known.pk).update(tool_policy_digest="f"*64)
        with self.assertRaises(AiError):service.check(context,second,self.admin)

    def test_post_prepare_tool_argument_change_cannot_reuse_its_saved_digest(self):
        _,job=self.running()
        context=service.prepare(job,self.admin)
        prepared=context._tools
        pages,_=service.tools.expected_pages(prepared,"commerce",self.admin)
        args={"runId":prepared.reference["evidenceRunId"],"reportId":prepared.report_id,
            "screeningId":prepared.reference["screeningIntent"]["id"],"role":"commerce","offset":0}
        parent=self.provider_receipt(job)
        with mutation(self.admin):
            tool=m.AiAgentToolDispatches.objects.create(id=uid("step-tool"),job=job,provider_dispatch=parent,tool_call_ordinal=1,
                provider_call_id="call-one",tool_name=contract.PACKAGE_TOOL,arguments_json=canonical(args),arguments_digest=digest(args),
                invocation_id=uid("invocation"),lease_epoch=job.lease_epoch,state="succeeded")
            value={"ok":True,"toolName":contract.PACKAGE_TOOL,"auditStatus":"recorded","data":pages[0]}
            m.AiAgentToolResults.objects.create(tool_dispatch=tool,result_json=canonical(value),result_digest=digest(value))
        context=service.prepare(job,self.admin)
        from django.db import ProgrammingError
        with self.assertRaisesMessage(ProgrammingError,"ai_immutable_identity"):
            with mutation(self.admin):m.AiAgentToolDispatches.objects.filter(pk=tool.pk).update(arguments_json=canonical({**args,"offset":1}))
        service.check(context,job,self.admin)
        # Separately simulate a corrupted read, without disabling the DB guard.
        tool.arguments_json=canonical({**args,"offset":1})
        with patch.object(m.AiAgentToolDispatches.objects,"filter") as query:
            query.return_value.order_by.return_value=[tool]
            with self.assertRaises(AiError):service.check(context,job,self.admin)

    def test_known_final_without_reading_is_saved_once_then_fails_without_replay(self):
        _,job=self.running()
        with mutation(self.admin):
            job.status="queued";job.lease_token="";job.lease_expires_at=None;job.version+=1;job.save()
        answer=canonical({"summary":"合成回答","findings":[{"id":"gap-one","kind":"gap","title":"未读取",
            "explanation":"测试不完整阅读仍须保留已经收到的模型结果","references":[]}]})
        response={"text":answer,"calls":[],"frame":{"role":"assistant","content":answer},"usage":{}}
        with patch("ai_assistant.provider.turn",return_value=response) as provider:
            result=workflows.agent_tick(job_id=job.id)
            self.assertEqual(result["status"],"failed")
            self.assertEqual(result["errorCode"],"screening_read_incomplete")
            self.assertEqual(workflows.agent_tick(job_id=job.id)["status"],"idle")
        provider.assert_called_once()
        saved=m.AiAgentProviderResults.objects.get(dispatch__job=job)
        self.assertEqual(json.loads(saved.response_json)["text"],answer)
        self.assertEqual(saved.dispatch.state,"succeeded")
        self.assertEqual(m.AiAgentToolDispatches.objects.filter(job=job).count(),0)
