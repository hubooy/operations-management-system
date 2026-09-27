"""Isolated PostgreSQL: real generic rows, synthetic content, no model call."""
from contextlib import contextmanager
import json
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from . import business_market_v2_active_synthetic as service
from . import business_market_v2_active_synthetic_contract as synthetic_contract
from . import business_market_v2_read_attestation as old_read
from . import models as m
from . import test_business_market_v2_execution_plan as fixture
from .policy import AiError, digest
from .test_business_market_v2_read_receipt_candidate import read_attestor


@contextmanager
def synthetic_attestor():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_market_synthetic_attestor")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2SyntheticChainTests(djtest.TransactionTestCase):
    user = fixture.MarketV2ExecutionPlanTests.user
    request_body = fixture.MarketV2ExecutionPlanTests.request_body
    current_catalog = fixture.MarketV2ExecutionPlanTests.current_catalog
    create_fixed_report = fixture.MarketV2ExecutionPlanTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2ExecutionPlanTests.planned_evidence_body
    selector = fixture.MarketV2ExecutionPlanTests.selector
    parked_id = fixture.MarketV2ExecutionPlanTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2ExecutionPlanTests._attest_as_role)
    admitted = fixture.MarketV2ExecutionPlanTests.admitted
    setUp = fixture.MarketV2ExecutionPlanTests.setUp
    body = fixture.MarketV2ExecutionPlanTests.body
    create_plan = fixture.MarketV2ExecutionPlanTests.create_plan
    attested = fixture.MarketV2ExecutionPlanTests.attested
    prepared = fixture.MarketV2ExecutionPlanTests.prepared

    def source_plan(self):
        created, _, prepared = self.prepared()
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                fixture.plan_attestor():
            saved = fixture.service.attest(created["reportId"], prepared["planJson"])
        return created, saved["planId"]

    def test_synthetic_chain_is_persisted_but_not_a_model_read(self):
        old, plan_id = self.source_plan()
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                synthetic_attestor(), patch("ai_assistant.provider.turn") as model:
            created = service.create(plan_id)
            model.assert_not_called()
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM public.ai_report_runs LIMIT 1")
        self.assertTrue(created["syntheticOnly"])
        self.assertFalse(created["externalProviderCalled"])
        self.assertFalse(created["persistedRead"])
        self.assertFalse(created["numericCitationAllowed"])
        self.assertEqual(created["paidCostCents"], 0)
        self.assertNotEqual(created["reportId"], old["reportId"])
        original = m.AiReportRun.objects.select_related("workflow").get(
            pk=old["reportId"])
        self.assertEqual(original.workflow.status, "paused")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=original.workflow_id).exists())
        report = m.AiReportRun.objects.select_related("workflow").get(
            pk=created["reportId"])
        self.assertEqual(report.workflow.status, "paused")
        self.assertEqual(report.workflow.dry_run, 1)
        self.assertEqual(report.workflow.model_id, "market-v2-synthetic-only")
        self.assertEqual((report.workflow.version, report.workflow.mutation_token,
            report.workflow.cancel_requested, report.workflow.retryable,
            report.workflow.resume_count, report.workflow.attempt_count,
            report.workflow.lease_token, report.workflow.lease_epoch,
            report.workflow.error_message), (1,"",0,0,0,0,"",0,""))
        self.assertIsNotNone(report.workflow.next_run_at)
        self.assertIsNotNone(report.workflow.created_at)
        self.assertIsNotNone(report.workflow.updated_at)
        self.assertEqual(json.loads(report.snapshot_json)["syntheticRoot"]["planId"],
            plan_id)
        self.assertEqual(set(created["jobIds"]),set(synthetic_contract.ROLES))
        self.assertEqual(set(created["providerDispatchIds"]),
            set(synthetic_contract.ROLES))
        self.assertEqual(set(created["toolDispatchIds"]),
            set(synthetic_contract.ROLES))
        self.assertEqual((report.workflow.provider_round_count,
            report.workflow.tool_call_count),(5,5))
        self.assertEqual(m.AiAgentJobs.objects.filter(
            workflow_run_id=report.workflow_id).count(),5)
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(
            run_id=report.workflow_id))
        self.assertEqual(len(nodes),6)
        graph_nodes = {item["key"]:item for item in
            json.loads(report.workflow.graph_json)["nodes"]}
        for node in nodes:
            self.assertEqual(json.loads(node.depends_on_json),
                graph_nodes[node.node_key]["dependsOn"])
            self.assertEqual(node.agent_job_id,
                created["jobIds"].get(node.node_key))
        self.assertTrue(all(node.version==1 and node.mutation_token==""
            and node.input_json=="{}" and node.error_code==""
            and node.error_message=="" and node.created_at is not None
            and node.updated_at is not None for node in nodes))
        with_budget = json.loads(report.snapshot_json)["withBudget"]
        expected_tools = synthetic_contract.role_tools(with_budget)
        for role in synthetic_contract.ROLES:
            job = m.AiAgentJobs.objects.get(pk=created["jobIds"][role])
            provider = m.AiAgentProviderDispatches.objects.get(
                pk=created["providerDispatchIds"][role])
            tool = m.AiAgentToolDispatches.objects.get(
                pk=created["toolDispatchIds"][role])
            self.assertEqual((job.workflow_run_id,job.workflow_node_key,
                job.status,job.provider_round_count,job.tool_call_count),
                (report.workflow_id,role,"paused",1,1))
            self.assertEqual((job.step_index,job.version,job.mutation_token,
                job.cancel_requested,job.retryable,job.resume_count,
                job.attempt_count,job.lease_token,job.lease_epoch,
                job.error_code,job.error_message),
                (0,1,"",0,0,0,0,"",1,"",""))
            self.assertIsNotNone(job.next_run_at)
            self.assertEqual(provider.job_id,job.id)
            self.assertEqual(tool.job_id,job.id)
            self.assertEqual(tool.provider_dispatch_id,provider.id)
            self.assertEqual(tool.tool_name,expected_tools[role])
            provider_result = m.AiAgentProviderResults.objects.get(
                dispatch_id=provider.id)
            tool_result = m.AiAgentToolResults.objects.get(
                tool_dispatch_id=tool.id)
            self.assertEqual(provider_result.response_digest,
                digest(provider_result.response_json))
            self.assertEqual(tool_result.result_digest,
                digest(tool_result.result_json))
            call = json.loads(provider_result.response_json)["calls"][0]
            self.assertEqual((call["id"],call["name"],call["arguments"]),
                (tool.provider_call_id,tool.tool_name,json.loads(tool.arguments_json)))
            self.assertTrue(json.loads(provider_result.response_json)["syntheticOnly"])
            data = json.loads(tool_result.result_json)["data"]
            self.assertEqual(data["role"],role)
            self.assertTrue(data["syntheticOnly"])
            self.assertFalse(data["persistedRead"])
            self.assertFalse(data["numericCitationAllowed"])
            self.assertEqual((provider.error_code,provider.error_message,
                tool.error_code,tool.error_message),("","","",""))
            self.assertTrue(all(value is not None for value in (
                provider.reserved_at,provider.provider_called_at,
                provider.completed_at,tool.reserved_at,tool.tool_called_at,
                tool.completed_at,provider_result.completed_at,
                tool_result.completed_at)))
        job = m.AiAgentJobs.objects.get(pk=created["jobIds"]["market_b2b"])
        provider = m.AiAgentProviderDispatches.objects.get(
            pk=created["providerDispatchIds"]["market_b2b"])
        tool = m.AiAgentToolDispatches.objects.get(
            pk=created["toolDispatchIds"]["market_b2b"])
        tool_result = m.AiAgentToolResults.objects.get(tool_dispatch_id=tool.id)
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(),0)
        with self.assertRaisesRegex(DatabaseError,
                "ai_market_v2_admitted_tool_dispatch_disabled"), transaction.atomic():
            m.AiAgentToolDispatches.objects.create(id="synthetic-ordinary-tool-denied",
                job=job, provider_dispatch=provider, tool_call_ordinal=2,
                provider_call_id="synthetic-call-2",
                tool_name="get_business_promotion_market_v2",
                arguments_json=tool.arguments_json,
                arguments_digest=tool.arguments_digest,
                invocation_id="ordinary-role-no-network",state="calling",
                lease_epoch=1)
        with self.assertRaisesRegex(DatabaseError,
                "ai_market_v2_admitted_tool_result_disabled"), transaction.atomic():
            m.AiAgentToolResults.objects.create(tool_dispatch=tool,
                result_json=tool_result.result_json,
                result_digest=tool_result.result_digest)
        promotion_job = m.AiAgentJobs.objects.get(
            pk=created["jobIds"]["promotion"])
        promotion_provider = m.AiAgentProviderDispatches.objects.get(
            pk=created["providerDispatchIds"]["promotion"])
        with self.assertRaisesRegex(DatabaseError,
                "ai_market_v2_synthetic_child_denied"), transaction.atomic():
            m.AiAgentToolDispatches.objects.create(
                id="synthetic-wrong-role-tool-denied",job=promotion_job,
                provider_dispatch=promotion_provider,tool_call_ordinal=2,
                provider_call_id="synthetic-promotion-wrong-call",
                tool_name="get_business_market_v2_screening_package",
                arguments_json="{}",arguments_digest=digest("{}"),
                invocation_id="wrong-role-no-network",state="calling",
                lease_epoch=1)
        with read_attestor(), self.assertRaises(DatabaseError):
            old_read.attest(tool.id)
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(),0)

    def test_all_five_synthetic_roles_cannot_claim_0062_agent_reads(self):
        original, plan_id = self.source_plan()
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                synthetic_attestor(), patch("ai_assistant.provider.turn") as model:
            created = service.create(plan_id)
            model.assert_not_called()
        self.assertNotEqual(created["reportId"], original["reportId"])
        self.assertFalse(created["externalProviderCalled"])
        self.assertFalse(created["persistedRead"])
        self.assertEqual(set(created["toolDispatchIds"]),
            set(synthetic_contract.ROLES))
        for role, tool_id in created["toolDispatchIds"].items():
            tool = m.AiAgentToolDispatches.objects.get(pk=tool_id)
            job = m.AiAgentJobs.objects.get(pk=tool.job_id)
            provider_result = m.AiAgentProviderResults.objects.get(
                dispatch_id=tool.provider_dispatch_id)
            result = m.AiAgentToolResults.objects.get(tool_dispatch_id=tool_id)
            self.assertEqual(job.workflow_node_key, role)
            self.assertEqual(job.status, "paused")
            self.assertFalse(json.loads(provider_result.response_json)[
                "externalProviderCalled"])
            self.assertFalse(json.loads(result.result_json)["data"]["persistedRead"])
            with read_attestor(), self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_read_chain_invalid"):
                old_read.attest(tool_id)
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(), 0)

    def test_old_admitted_profile_still_rejects_fifth_tool(self):
        _, admitted, _, _ = self.admitted()
        old_flow = m.AiReportRun.objects.get(pk=admitted["reportId"]).workflow
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
            job = m.AiAgentJobs.objects.create(id="old-admitted-negative-job",
                owner_email=self.admin.email.lower(), scope_json="null",
                client_request_id="old-admitted-negative-job",
                request_digest="a"*64, task="rollback-only-negative",
                model_id="synthetic-only",model_version=1,
                workflow_run_id=old_flow.id,workflow_node_key="market_b2b",
                status="paused",phase="paused")
            provider = m.AiAgentProviderDispatches.objects.create(
                id="old-admitted-negative-provider",job=job,
                dispatch_ordinal=1,owner_email=self.admin.email.lower(),
                actor_role="admin",model_id="synthetic-only",model_version=1,
                tool_policy_digest="a"*64,request_digest="a"*64,
                state="calling",lease_epoch=1)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = origin")
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_admitted_tool_dispatch_disabled"), transaction.atomic():
                m.AiAgentToolDispatches.objects.create(
                    id="old-admitted-negative-tool",job=job,
                    provider_dispatch=provider,tool_call_ordinal=1,
                    provider_call_id="old-admitted-call",
                    tool_name="get_business_promotion_market_v2",
                    arguments_json="{}",arguments_digest=digest("{}"),
                    invocation_id="old-admitted-negative",state="calling",
                    lease_epoch=1)
            transaction.set_rollback(True)

    def test_flag_role_and_duplicate_run_fail_closed(self):
        _, plan_id = self.source_plan()
        with self.assertRaises(AiError):
            service.create(plan_id)
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                self.assertRaises(AiError):
            service.create(plan_id)
        with djtest.override_settings(AI_MARKET_V2_SYNTHETIC_ENABLED=True), \
                synthetic_attestor():
            service.create(plan_id)
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_synthetic_duplicate"):
                service.create(plan_id)
        self.assertEqual(m.AiBusinessMarketV2ReadReceipt.objects.count(),0)
