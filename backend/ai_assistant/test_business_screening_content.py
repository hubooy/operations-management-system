"""Complete content over synthetic immutable storage and real five-job ledgers.

No model is called: test-authored answers and tool receipts are explicitly
synthetic, while readers, scanner, publication, package and proof are real.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from django import test as djtest
from . import business_screening_content as service, business_screening_tools as tools
from . import business_screening_diagnosis as diagnosis, business_screening_receipts as receipts
from . import business_screening_claims as claims, business_diagnostic_screening as scanner
from . import business_screening_store as store, models as m
from . import test_business_screening_tools as fixtures, test_business_screening_diagnosis as answers
from .policy import AiError, canonical, digest, mutation


class ScreeningContentPureTests(TestCase):
    def test_claim_prepare_many_rejects_bad_role_list_before_any_storage(self):
        with patch.object(claims.packages,"_checked",side_effect=AssertionError("no read")):
            for roles in ([],{},"report",["report","report"],[True],["other"],list(service.contract.ROLES)+["report"]):
                with self.assertRaises(AiError):claims.prepare_many({},roles,None)

    def test_supplied_prepared_cannot_bypass_loader_or_be_json(self):
        # Real type check is retained in the path shared by content and proof.
        with self.assertRaises(AiError):tools._checked({"reportId":"r"},None)
        original=receipts._trusted
        with patch.object(receipts,"_trusted",side_effect=AiError("denied","access_denied",403)) as trusted:
            with self.assertRaises(AiError):receipts.progress(SimpleNamespace(id="job"),{},None,_prepared={})
            self.assertEqual(trusted.call_args.kwargs,{"_prepared":{}})
        self.assertIs(receipts._trusted,original)


class ScreeningReviewPureTests(TestCase):
    def setUp(self):
        self.report=SimpleNamespace(id="report",snapshot_json="{}")
        self.principal=SimpleNamespace(email="synthetic@example.invalid",scope=None)
        self.state=patch.object(service,"_review_state",return_value=(self.report,'{"state":1}')).start()
        self.heavy=patch.object(service,"content",return_value={"independentReview":{"approved":True,"conflicts":[]}}).start()
        patch.object(service,"current_principal").start()
        self.addCleanup(patch.stopall)

    def test_full_prepare_is_once_and_short_revalidate_never_calls_content(self):
        prepared=service.prepare_review(self.report,self.principal)
        self.heavy.assert_called_once()
        self.heavy.side_effect=AssertionError("short transaction cannot compute")
        service.revalidate_review(prepared,self.principal)
        with self.assertRaises(AiError):service.revalidate_review({},self.principal)
        with self.assertRaises(AiError):service.PreparedReview(None,self.report,self.principal,"{}","digest")

    def test_outer_transaction_is_rejected_before_any_heavy_work(self):
        with patch.object(service,"connection",SimpleNamespace(in_atomic_block=True)),self.assertRaises(AiError):
            service.prepare_review(self.report,self.principal)
        self.state.assert_not_called();self.heavy.assert_not_called()

    def test_independent_conflict_and_mid_preparation_change_refuse(self):
        for review in ({"approved":False,"conflicts":[]},{"approved":True,"conflicts":["unresolved"]}):
            self.heavy.return_value={"independentReview":review}
            with self.assertRaises(AiError):service.prepare_review(self.report,self.principal)
        self.heavy.return_value={"independentReview":{"approved":True,"conflicts":[]}}
        self.state.side_effect=[(self.report,'{"state":1}'),(self.report,'{"state":2}')]
        with self.assertRaises(AiError):service.prepare_review(self.report,self.principal)

    def test_late_state_owner_and_private_encoding_mutation_refuse(self):
        prepared=service.prepare_review(self.report,self.principal)
        other=SimpleNamespace(email="other@example.invalid",scope=None)
        with self.assertRaises(AiError):service.revalidate_review(prepared,other)
        self.state.return_value=(self.report,'{"state":2}')
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.principal)
        for value in ("{}","\ud800",{}):
            object.__setattr__(prepared,"_state_json",value)
            with self.assertRaises(AiError):service.revalidate_review(prepared,self.principal)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningContentTests(djtest.TransactionTestCase):
    user=fixtures.ScreeningToolsTests.user
    call=fixtures.ScreeningToolsTests.call
    collect_body=fixtures.ScreeningToolsTests.collect_body
    bundle=fixtures.ScreeningToolsTests.bundle
    input_for=fixtures.ScreeningToolsTests.input_for
    insert=fixtures.ScreeningToolsTests.insert
    seed=fixtures.ScreeningToolsTests.seed
    setUp=fixtures.ScreeningToolsTests.setUp
    screening_bundle=fixtures.ScreeningToolsTests.screening_bundle
    insert_screening=fixtures.ScreeningToolsTests.insert_screening

    def create_complete(self,*,mapped=False,budget=False,candidate=False,skip_role=None,bad_role=None,missing_budget=False):
        if candidate:
            answers.candidate_evidence(self)
            if budget:
                # The candidate fixture creates a new sealed run. Resolve its
                # actual row identities; never reuse the previous run's IDs.
                from . import business_evidence
                self.budget_plan=deepcopy(self.budget_plan)
                for target in self.budget_plan["targets"]:
                    page=business_evidence.analysis_table(self.parent.id,
                        {"sourceKey":target["sourceKey"],"dimension":target["dimension"]},self.admin)
                    row=next(item for item in page["rows"] if item["rowIndex"]==target["rowIndex"])
                    target["rowId"]=row["id"]
        bundle=self.screening_bundle(mapped=mapped,budget=budget)
        with mutation(self.admin):
            report=self.insert_screening(bundle,flow_changes={"dry_run":0,"allowed_tools_json":canonical(sorted(service.contract.TOOLS)),
                "model_id":"synthetic-content-model","model_version":1,"tool_policy_digest":"e"*64})
        store.publish(scanner.prepare_for_report(report.id,self.admin),self.admin)
        prepared=tools.prepare_for_report(report,self.admin,resolve_budget=budget)
        candidate_ref=None
        if candidate:
            verified=claims.prepare(prepared.packages,"report",self.admin)
            choices=[item["candidate"] for item in json.loads(verified._index_json)["candidates"].values()]
            chosen=next(c for c in choices if any(v is not None for v in c["current"].values()))
            metric=next(k for k,v in chosen["current"].items() if v is not None)
            candidate_ref={"candidateId":chosen["candidateId"],"metric":metric,"field":"value"}
        outputs={role:{"answer":canonical(answers.answer(role,candidate_ref if role=="report" else None))} for role in service.contract.ROLES}
        if bad_role:outputs[bad_role]={"answer":canonical({**answers.answer(bad_role),"claimedNumber":100})}
        graph=service.contract.graph(budget)
        with mutation(self.admin):
            for index,definition in enumerate(graph["nodes"][:5]):
                role=definition["key"];prefix=report.id+"-"+role
                node_input={"workflowInput":prepared.reference,"dependencies":{key:outputs[key] for key in definition["dependsOn"]}}
                job=m.AiAgentJobs.objects.create(id=prefix,owner_email=self.admin.email,client_request_id=prefix,
                    request_digest=digest(prefix),scope_json="null",task=definition["instruction"],input_json=canonical(node_input),
                    output_json=canonical(outputs[role]),status="completed",phase="completed",workflow_run_id=report.workflow_id,
                    workflow_node_key=role,model_id=report.workflow.model_id,model_version=report.workflow.model_version,
                    allowed_tools_json=canonical(sorted(service.contract.TOOLS)),tool_policy_digest=report.workflow.tool_policy_digest)
                m.AiWorkflowNodeRuns.objects.create(id=prefix+"-node",run=report.workflow,node_key=role,position=index,
                    node_type="agent",instruction=definition["instruction"],depends_on_json=canonical(definition["dependsOn"]),
                    input_json=job.input_json,output_json=job.output_json,status="completed",agent_job=job)
                provider=m.AiAgentProviderDispatches.objects.create(id=prefix+"-provider",job=job,dispatch_ordinal=1,
                    owner_email=self.admin.email,actor_role="admin",model_id=job.model_id,model_version=job.model_version,
                    tool_policy_digest=job.tool_policy_digest,request_digest=digest(prefix+"-provider"),state="succeeded",lease_epoch=1)
                pages,budgets=tools.expected_pages(prepared,role,self.admin)
                entries=[] if role==skip_role else [(service.contract.PACKAGE_TOOL,offset,page) for offset,page in pages.items()]
                if budget and role in service.contract.BUDGET_NODES and not missing_budget:
                    entries += [(service.contract.BUDGET_TOOL,offset,page) for offset,page in budgets.items()]
                for ordinal,(name,offset,page) in enumerate(entries,1):
                    args={"runId":self.parent.id,"reportId":report.id,"screeningId":prepared.reference["screeningIntent"]["id"],"offset":offset}
                    if name==service.contract.PACKAGE_TOOL:args["role"]=role
                    dispatch=m.AiAgentToolDispatches.objects.create(id=prefix+"-tool-"+str(ordinal),job=job,
                        provider_dispatch=provider,tool_call_ordinal=ordinal,provider_call_id="call-"+str(ordinal),tool_name=name,
                        arguments_json=canonical(args),arguments_digest=digest(args),invocation_id=prefix+"-inv-"+str(ordinal),
                        state="succeeded",lease_epoch=1)
                    result={"toolName":name,"ok":True,"auditStatus":"recorded","data":page}
                    m.AiAgentToolResults.objects.create(tool_dispatch=dispatch,result_json=canonical(result),result_digest=digest(result))
        return report,prepared

    def test_real_four_combinations_complete_five_ledgers_coverage_and_budget_once(self):
        for mapped in (False,True):
            for budget in (False,True):
                with self.subTest(mapped=mapped,budget=budget):
                    report,_=self.create_complete(mapped=mapped,budget=budget)
                    load=tools.business_budget_store.load;build=tools.packages.prepare
                    with patch.object(tools.business_budget_store,"load",wraps=load) as loaded,patch.object(
                            tools.packages,"prepare",wraps=build) as rebuilt,patch("ai_assistant.provider.turn") as model:
                        result=service.content(report,self.admin)
                    self.assertEqual(loaded.call_count,int(budget));self.assertEqual(rebuilt.call_count,1)
                    self.assertEqual(set(result["screening"]["readProofs"]),set(service.contract.ROLES))
                    self.assertEqual(len({p["jobId"] for p in result["screening"]["readProofs"].values()}),5)
                    self.assertTrue(all(p["package"]["complete"] for p in result["screening"]["readProofs"].values()))
                    self.assertFalse(result["screening"]["authority"]["entityDailyCoverageVerified"])
                    self.assertEqual(set(r["kind"] for r in result["screening"]["coverage"]),{"family","requested","table","partition"})
                    self.assertEqual("budget" in result,budget)
                    if budget:self.assertEqual(result["budget"]["planDigest"],json.loads(report.snapshot_json)["budgetRef"]["planDigest"])
                    model.assert_not_called()

    def test_real_candidate_and_full_coverage_do_not_read_facts(self):
        report,_=self.create_complete(mapped=True,candidate=True)
        with patch.object(tools.Reader,"pages",side_effect=AssertionError("candidate content must not rescan")),patch(
                "ai_assistant.transport.execute_tool") as remote:
            result=service.content(report,self.admin)
        fact=result["diagnosis"]["findings"][0]["facts"][0]
        self.assertTrue(fact["verification"]["candidateNumberVerified"])
        self.assertFalse(fact["verification"]["agentReadVerified"])
        self.assertEqual(fact["reference"]["screening"],result["screening"]["reference"])
        remote.assert_not_called()

    def test_other_job_complete_does_not_cover_missing_role_or_budget(self):
        report,_=self.create_complete(skip_role="commerce")
        with self.assertRaises(AiError) as error:service.content(report,self.admin)
        self.assertEqual(error.exception.code,"screening_read_incomplete")
        report,_=self.create_complete(budget=True,missing_budget=True)
        with self.assertRaises(AiError) as error:service.content(report,self.admin)
        self.assertEqual(error.exception.code,"screening_read_incomplete")

    def test_invalid_specialist_does_not_hide_behind_valid_report_and_prepared_cross_report_rejected(self):
        report,prepared=self.create_complete(bad_role="commerce")
        with self.assertRaises(AiError):service.content(report,self.admin)
        second,_=self.create_complete()
        job=m.AiAgentJobs.objects.get(workflow_run_id=second.workflow_id,workflow_node_key="report")
        with self.assertRaises(AiError):receipts.validate_complete(job,json.loads(second.snapshot_json),self.admin,_prepared=prepared)
        with self.assertRaises(AiError):receipts.validate_complete(job,json.loads(second.snapshot_json),self.admin,_prepared={})

    def test_late_revocation_cross_owner_and_full_content_capacity_fail_closed(self):
        from access_control.models import AppUser
        report,_=self.create_complete()
        other=self.user("screening-content-other@example.invalid","admin",None)
        with self.assertRaises(AiError):service.content(report,other)
        with patch.object(service,"MAX_CONTENT_BYTES",1),self.assertRaises(AiError) as error:service.content(report,self.admin)
        self.assertEqual(error.exception.status,413)
        original=service._coverage
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return result
        with patch.object(service,"_coverage",side_effect=revoke),self.assertRaises(AiError) as error:service.content(report,self.admin)
        self.assertEqual(error.exception.status,403)


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningPreparedReviewTests(djtest.TransactionTestCase):
    user=ScreeningContentTests.user
    call=ScreeningContentTests.call
    collect_body=ScreeningContentTests.collect_body
    bundle=ScreeningContentTests.bundle
    input_for=ScreeningContentTests.input_for
    insert=ScreeningContentTests.insert
    seed=ScreeningContentTests.seed
    setUp=ScreeningContentTests.setUp
    screening_bundle=ScreeningContentTests.screening_bundle
    insert_screening=ScreeningContentTests.insert_screening
    create_complete=ScreeningContentTests.create_complete

    def waiting(self,*,budget=False):
        report,_=self.create_complete(mapped=True,budget=budget)
        definition=service.contract.graph(budget)["nodes"][-1]
        with mutation(self.admin):
            flow=report.workflow;flow.status="waiting_review";flow.current_node_key="human_review";flow.version+=1;flow.save()
            human=m.AiWorkflowNodeRuns.objects.create(id=report.id+"-human",run=flow,node_key="human_review",position=5,
                node_type="human_review",status="waiting_review",instruction=definition["instruction"],
                depends_on_json=canonical(definition["dependsOn"]))
        report.refresh_from_db()
        return report,human

    def test_real_complete_prepare_then_short_transaction_has_no_heavy_reads(self):
        report,_=self.waiting(budget=True)
        prepared=service.prepare_review(report,self.admin)
        with patch.object(service,"content",side_effect=AssertionError("no content")),patch.object(
                tools.Reader,"pages",side_effect=AssertionError("no facts")),patch.object(
                tools.business_budget_store,"load",side_effect=AssertionError("no budget recompute")),patch.object(
                claims,"resolve",side_effect=AssertionError("no numeric recompute")),patch.object(
                receipts,"validate_complete",side_effect=AssertionError("no full receipts")):
            with mutation(self.admin):self.assertIsNone(service.revalidate_review(prepared,self.admin))
        with self.assertRaises(AiError):service.revalidate_review({},self.admin)
        with self.assertRaises(AiError):service.PreparedReview(None,report,self.admin,"{}","a"*64)
        object.__setattr__(prepared,"_state_json","{}")
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.admin)

    def test_stale_versions_outputs_and_cancellation_refuse_original_preparation(self):
        report,human=self.waiting()
        prepared=service.prepare_review(report,self.admin)
        with mutation(self.admin):
            human.version+=1;human.save()
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.admin)
        prepared=service.prepare_review(report,self.admin)
        with mutation(self.admin):
            flow=m.AiWorkflowRuns.objects.get(pk=report.workflow_id);flow.version+=1;flow.save()
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.admin)
        prepared=service.prepare_review(report,self.admin)
        job=m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id,workflow_node_key="commerce")
        with mutation(self.admin):
            job.output_json=canonical({"answer":canonical({**answers.answer("commerce"),"summary":"迟到变化"})});job.save()
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.admin)
        with mutation(self.admin):
            flow=m.AiWorkflowRuns.objects.get(pk=report.workflow_id);flow.cancel_requested=1;flow.version+=1;flow.save()
        with self.assertRaises(AiError):service.revalidate_review(prepared,self.admin)

    def test_not_waiting_conflict_cross_owner_and_late_revocation(self):
        from access_control.models import AppUser
        report,_=self.create_complete()
        with self.assertRaises(AiError):service.prepare_review(report,self.admin)
        report,_=self.waiting()
        prepared=service.prepare_review(report,self.admin)
        other=self.user("screen-review-other@example.invalid","admin",None)
        with self.assertRaises(AiError) as error:service.revalidate_review(prepared,other)
        self.assertEqual(error.exception.status,403)
        # Both node/job are updated consistently; independent review conflicts
        # still block the heavy preparation rather than becoming permission.
        job=m.AiAgentJobs.objects.get(workflow_run_id=report.workflow_id,workflow_node_key="independent_review")
        with mutation(self.admin):
            job.output_json=canonical({"answer":canonical({"approved":False,"conflicts":["待核"],"limitations":[]})});job.save()
            m.AiWorkflowNodeRuns.objects.filter(agent_job=job).update(output_json=job.output_json)
        with self.assertRaises(AiError):service.prepare_review(report,self.admin)
        AppUser.objects.filter(email=self.admin.email).update(status="disabled")
        with self.assertRaises(AiError) as error:service.revalidate_review(prepared,self.admin)
        self.assertEqual(error.exception.status,403)
