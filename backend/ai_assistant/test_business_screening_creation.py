"""Queued creation contract; HTTP integration is tested separately."""
from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from django import test as djtest
from django.db import connection, transaction
from access_control.models import AppUser

from . import business_screening_creation as service, business_budget_store as budgets
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_diagnostic_screening as screening, transport, provider, models as m, workflows
from . import test_business_screening_tools as fixtures, test_business_screening_admission as admission_fixtures
from .business_sealed import Reader
from .policy import AiError, canonical, digest, mutation


class CreationPureTests(unittest.TestCase):
    def test_private_budget_capsule_cannot_import_json_and_rejects_alias_mutation(self):
        for given in ({},"{}",SimpleNamespace()):
            with self.assertRaises(AiError):service._BudgetCapsule(None,given)
        value=budgets.PreparedBudget("budget-test","{}","{}","{}")
        capsule=service._BudgetCapsule(service._TOKEN,value)
        self.assertEqual(capsule.budget(),value)
        with self.assertRaises(AttributeError):capsule._plan_json="{}"
        object.__setattr__(capsule,"_result_json",'{"changed":true}')
        with self.assertRaises(AiError):capsule.budget()

    def test_creation_and_fact_preparation_reject_outer_transaction(self):
        with patch.object(service,"connection",SimpleNamespace(in_atomic_block=True)),patch.object(budgets,"prepare") as prepare:
            with self.assertRaises(AiError):service.create({},None)
            with self.assertRaises(AiError):service._prepare_budget(None,{},None,"report")
        prepare.assert_not_called()

    def test_strict_new_mode_rejects_dryrun_unknown_and_null_mapping_before_reads(self):
        body={"clientRequestId":"new","evidenceRunId":"e","question":"q","dryRun":False,"analysisMode":"screening-v1"}
        actor=SimpleNamespace(email="owner@example.invalid",scope=None)
        with (patch.object(service,"connection",SimpleNamespace(in_atomic_block=False)),
                patch.object(service,"current_principal"),patch.object(service,"_replay") as replay):
            for extra in ({"analysisMode":"old"},{"dryRun":True},{"dryRun":0},{"dryRun":None},
                    {"mappingPairs":None},{"executionProfile":contract.PROFILE},{"surface":contract.SURFACE},{"modelId":"model"}):
                with self.subTest(extra=extra),self.assertRaises(AiError):service.create({**body,**extra},actor)
            for key in ("clientRequestId","evidenceRunId","question","dryRun","analysisMode"):
                bad={k:v for k,v in body.items() if k!=key}
                with self.assertRaises(AiError):service.create(bad,actor)
        replay.assert_not_called()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",DJANGO_ENVIRONMENT="test")
class ScreeningCreationTests(djtest.TransactionTestCase):
    user=fixtures.ScreeningToolsTests.user
    call=fixtures.ScreeningToolsTests.call
    collect_body=fixtures.ScreeningToolsTests.collect_body
    bundle=fixtures.ScreeningToolsTests.bundle
    input_for=fixtures.ScreeningToolsTests.input_for
    insert=fixtures.ScreeningToolsTests.insert
    seed=fixtures.ScreeningToolsTests.seed
    setUp=fixtures.ScreeningToolsTests.setUp

    def creation_body(self,*,mapped=False,budget=False):
        self.serial+=1
        value={"clientRequestId":"screen-create-"+str(self.serial),"evidenceRunId":self.parent.id,"question":"实际封存新筛查创建",
            "dryRun":False,"analysisMode":"screening-v1","expectedPrincipalKey":service.evidence_service.principal_key(self.admin)}
        if mapped:value["mappingPairs"]=[{"salesKey":"sales","masterKey":"master"}]
        if budget:value["budgetPlan"]=deepcopy(self.budget_plan)
        return value

    def counts(self):
        return tuple(cls.objects.count() for cls in (m.AiWorkflowRuns,m.AiWorkflowNodeRuns,m.AiAgentJobs,m.AiExecutionGuidance,
            m.AiReportRun,m.AiBusinessBudgetPlan,m.AiWorkflowEvents,m.AiBusinessScreeningRun))

    def catalog_response(self,*args):
        self.assertFalse(connection.in_atomic_block,"catalog network inside transaction")
        return admission_fixtures.catalog()

    def test_four_real_combinations_are_queued_six_nodes_with_no_locked_fact_reads(self):
        # The inherited evidence fixture also owns an older queued report;
        # retire only that fixture so four new reports exactly reach the cap.
        old=m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id)
        workflows.control(old.id,{"expectedVersion":old.version},self.admin,"cancel",workflow=True)
        original=Reader.pages
        scans=[]
        def pages(reader,*args,**kwargs):
            self.assertFalse(connection.in_atomic_block,"Reader.pages opened under global mutation")
            scans.append(args)
            for page in original(reader,*args,**kwargs):
                self.assertFalse(connection.in_atomic_block,"Reader.pages consumed under global mutation")
                yield page
        for mapped,budget in ((False,False),(True,False),(False,True),(True,True)):
            body=self.creation_body(mapped=mapped,budget=budget);before=len(scans)
            with (patch.object(Reader,"pages",pages),patch.object(transport,"catalog",side_effect=self.catalog_response),
                    patch.object(budgets,"insert",side_effect=AssertionError("old insert scans under lock")),
                    patch.object(budgets,"revalidate",side_effect=AssertionError("old budget revalidate scans")),
                    patch.object(runtime,"revalidate",side_effect=AssertionError("old runtime revalidate scans budget")),
                    patch.object(screening,"prepare_for_report",side_effect=AssertionError("no full screening")),
                    patch.object(provider,"turn",side_effect=AssertionError("no provider"))):
                result=service.create(body,self.admin)
            self.assertEqual(len(scans)>before,budget)
            report=m.AiReportRun.objects.select_related("workflow").get(pk=result["item"]["id"])
            snapshot=json.loads(report.snapshot_json);flow=report.workflow
            self.assertFalse(result["replayed"]);self.assertEqual(flow.status,"queued");self.assertEqual(flow.dry_run,0)
            self.assertEqual(flow.graph_json,canonical(workflows.validate_graph(contract.graph(budget))))
            self.assertEqual(flow.allowed_tools_json,canonical([e["name"] for e in admission_fixtures.catalog()]))
            self.assertEqual(flow.tool_policy_digest,digest(admission_fixtures.catalog()))
            self.assertEqual(snapshot["executionProfile"],contract.PROFILE)
            self.assertEqual(snapshot["screeningIntent"],json.loads(flow.input_json)["screeningIntent"])
            self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(run=flow,status="pending",agent_job__isnull=True).count(),6)
            self.assertTrue(m.AiExecutionGuidance.objects.filter(pk=flow.pk).exists())
            self.assertEqual(bool(report.budget_plan_id),budget)
            runtime.bound(report,self.admin)
        self.assertEqual(m.AiAgentJobs.objects.count(),0)
        self.assertEqual(m.AiBusinessScreeningRun.objects.count(),0)
        with patch.object(transport,"catalog") as network,self.assertRaises(AiError) as error:service.create(self.creation_body(),self.admin)
        self.assertEqual(error.exception.status,429);network.assert_not_called()

    def test_exact_unknown_retry_replays_without_network_or_budget_and_changed_body_rejects(self):
        body=self.creation_body(mapped=True,budget=True)
        with patch.object(transport,"catalog",side_effect=self.catalog_response):first=service.create(body,self.admin)
        before=self.counts()
        with (patch.object(transport,"catalog",side_effect=AssertionError("no replay network")),
                patch.object(Reader,"pages",side_effect=AssertionError("no replay facts")),
                patch.object(budgets,"prepare",side_effect=AssertionError("no replay budget"))):
            second=service.create(body,self.admin)
            self.assertTrue(second["replayed"]);self.assertEqual(second["item"],first["item"])
            with self.assertRaises(AiError):service.create({**body,"question":"changed"},self.admin)
        self.assertEqual(self.counts(),before)

    def test_mid_transaction_failure_rolls_back_budget_workflow_nodes_pin_report_and_event(self):
        body=self.creation_body(mapped=True,budget=True);before=self.counts()
        with (patch.object(transport,"catalog",side_effect=self.catalog_response),
                patch.object(workflows,"event",side_effect=RuntimeError("late publication failure")),self.assertRaises(RuntimeError)):
            service.create(body,self.admin)
        self.assertEqual(self.counts(),before)
        with patch.object(transport,"catalog",side_effect=self.catalog_response):result=service.create(body,self.admin)
        self.assertFalse(result["replayed"])

    def test_wrong_principal_key_cross_owner_and_late_model_change_are_zero_write(self):
        body=self.creation_body(budget=True);before=self.counts()
        with patch.object(transport,"catalog") as network,self.assertRaises(AiError):service.create({**body,"expectedPrincipalKey":"x"},self.admin)
        network.assert_not_called()
        other=self.user("screen-create-other@example.invalid","admin",None)
        changed={**body,"expectedPrincipalKey":service.evidence_service.principal_key(other)}
        with self.assertRaises(AiError):service.create(changed,other)
        def catalog(*args):
            value=self.catalog_response(*args)
            with mutation(self.admin):m.AiModels.objects.filter(pk=self.model.pk).update(version=self.model.version+1)
            return value
        with patch.object(transport,"catalog",side_effect=catalog),self.assertRaises(AiError) as error:service.create(body,self.admin)
        self.assertEqual(error.exception.code,"model_version_changed")
        self.assertEqual(self.counts(),before)

    def test_late_permissions_and_capsule_tampering_never_publish(self):
        body=self.creation_body(budget=True);before=self.counts();original=service._prepare_budget
        def altered(*args):
            value=original(*args);object.__setattr__(value,"_result_json","{}")
            return value
        with patch.object(transport,"catalog",side_effect=self.catalog_response),patch.object(service,"_prepare_budget",side_effect=altered),self.assertRaises(AiError):
            service.create(body,self.admin)
        self.assertEqual(self.counts(),before)
        def revoked(*args):
            value=original(*args)
            AppUser.objects.filter(email=self.admin.email).update(role_id="viewer")
            return value
        with patch.object(transport,"catalog",side_effect=self.catalog_response),patch.object(service,"_prepare_budget",side_effect=revoked),self.assertRaises(AiError):
            service.create(body,self.admin)
        self.assertEqual(self.counts(),before)

    def test_global_active_cap_and_catalog_shape_rejection(self):
        before=self.counts()
        entries=admission_fixtures.catalog()
        for value in ([],entries[:2],[*entries,entries[0]]):
            with (patch.object(transport,"catalog",return_value=value),patch.object(budgets,"prepare") as prepare,
                    self.assertRaises(AiError)):
                service.create(self.creation_body(budget=True),self.admin)
            prepare.assert_not_called();self.assertEqual(self.counts(),before)
        with mutation(self.admin):
            for index in range(24):
                m.AiWorkflowRuns.objects.create(id=f"active-fixture-{index}",owner_email=f"owner-{index}@example.invalid",scope_json="null",
                    client_request_id=f"fixture-{index}",request_digest="a"*64,name="capacity fixture",graph_json="{}",graph_digest=digest({}),input_json="{}",dry_run=1)
        with patch.object(transport,"catalog") as network,self.assertRaises(AiError) as error:service.create(self.creation_body(),self.admin)
        self.assertEqual(error.exception.status,429);network.assert_not_called()

    def test_previous_report_same_scope_and_transaction_guard(self):
        body=self.creation_body(mapped=True,budget=True)
        with patch.object(transport,"catalog",side_effect=self.catalog_response):first=service.create(body,self.admin)
        next_body=self.creation_body(mapped=True,budget=True);next_body["previousReportId"]=first["item"]["id"]
        with patch.object(transport,"catalog",side_effect=self.catalog_response):second=service.create(next_body,self.admin)
        self.assertEqual(json.loads(m.AiReportRun.objects.get(pk=second["item"]["id"]).snapshot_json)["previousReportId"],first["item"]["id"])
        next_body=self.creation_body(mapped=True,budget=True);next_body.update(previousReportId=first["item"]["id"],question="different scope question")
        with patch.object(transport,"catalog",side_effect=self.catalog_response),self.assertRaises(AiError):service.create(next_body,self.admin)
        with transaction.atomic(),patch.object(transport,"catalog") as network,self.assertRaises(AiError):service.create(self.creation_body(),self.admin)
        network.assert_not_called()
