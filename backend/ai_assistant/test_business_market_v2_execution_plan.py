"""Real-role PG probes for an immutable plan with no Agent execution."""
from contextlib import contextmanager
import json
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from . import business_market_v2_execution_plan as service
from . import business_market_v2_execution_plan_contract as contract
from . import models as m
from . import test_business_market_v2_context_proof as fixture
from .business_market_v2_execution_plan_catalog import verify as verify_catalog
from .test_business_market_v2_execution_snapshot import frozen_catalog
from .test_business_market_v2_material_role_bridge import session_role
from .policy import AiError, canonical


@contextmanager
def plan_attestor():
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_market_plan_attestor")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ExecutionPlanTests(djtest.TransactionTestCase):
    user = fixture.MarketV2ContextProofTests.user
    request_body = fixture.MarketV2ContextProofTests.request_body
    current_catalog = fixture.MarketV2ContextProofTests.current_catalog
    create_fixed_report = fixture.MarketV2ContextProofTests.create_fixed_report
    planned_evidence_body = fixture.MarketV2ContextProofTests.planned_evidence_body
    selector = fixture.MarketV2ContextProofTests.selector
    parked_id = fixture.MarketV2ContextProofTests.parked_id
    _attest_as_role = staticmethod(fixture.MarketV2ContextProofTests._attest_as_role)
    admitted = fixture.MarketV2ContextProofTests.admitted
    setUp = fixture.MarketV2ContextProofTests.setUp
    body = fixture.MarketV2ContextProofTests.body
    create_plan = fixture.MarketV2ContextProofTests.create_plan
    attested = fixture.MarketV2ContextProofTests.attested

    def prepared(self):
        _, _, created = self.create_plan()
        proof = self.attested(created["reportId"])
        body = {"schemaVersion":service.REQUEST_SCHEMA,
            "executionReportId":created["reportId"],
            "contextProofDigest":proof["proofDigest"]}
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                patch.object(service.transport, "catalog",
                    return_value=frozen_catalog()):
            plan = service.prepare(body, self.admin)
        return created, body, plan

    def test_sql_plan_is_distinct_closed_and_idempotent(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        created, body, plan = self.prepared()
        self.assertEqual(plan["plan"]["schemaVersion"], contract.SCHEMA)
        self.assertEqual(plan["plan"]["executionRoot"]["executionReportId"],
            created["reportId"])
        self.assertEqual(plan["plan"]["modelPolicy"]["maxPaidCostCents"], 0)
        self.assertFalse(plan["agentDispatchSupported"])
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), plan_attestor():
            first = service.attest(created["reportId"], plan["planJson"])
            self.assertEqual(first, service.attest(created["reportId"],
                plan["planJson"]))
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT plan_digest FROM public."
                        "ai_business_market_v2_execution_plans LIMIT 1")
        saved = m.AiBusinessMarketV2ExecutionPlan.objects.get(
            execution_report_id=created["reportId"])
        self.assertEqual(saved.plan_digest, plan["planDigest"])
        self.assertEqual(json.loads(saved.plan_json), plan["plan"])
        with session_role("teruisi_ai_reader"):
            receipt = service.read(created["reportId"], self.admin)
            self.assertEqual(receipt["planDigest"], plan["planDigest"])
            self.assertFalse(receipt["agentDispatchSupported"])
        report = m.AiReportRun.objects.get(pk=created["reportId"])
        self.assertEqual(report.workflow.status, "paused")
        self.assertEqual(report.workflow.model_id, "")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=created["workflowId"]).exists())
        self.assertFalse(m.AiWorkflowNodeRuns.objects.filter(
            run_id=created["workflowId"]).exists())

    def test_default_off_cross_account_and_policy_edits_fail(self):
        created, body, plan = self.prepared()
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=False), \
                self.assertRaises(AiError):
            service.prepare(body, self.admin)
        outsider = self.user("market-plan-outside@example.invalid", "admin", None)
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                patch.object(service.transport, "catalog",
                    return_value=frozen_catalog()):
            with self.assertRaises(AiError):
                service.prepare(body, outsider)
        with self.assertRaises(AiError):
            service.attest(created["reportId"], plan["planJson"])
        for changed in (lambda value: value["modelPolicy"].update(
                    paidCallsAllowed=True, maxPaidCostCents=1),
                lambda value: value["executionRoot"].update(
                    contextProofDigest="f"*64),
                lambda value: value.update(agentJobsAllowed=True)):
            candidate = json.loads(plan["planJson"])
            changed(candidate)
            with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), plan_attestor():
                with self.assertRaisesRegex(DatabaseError,
                        "ai_market_v2_execution_plan_root_or_policy_invalid"):
                    service.attest(created["reportId"], canonical(candidate))
        self.assertEqual(m.AiBusinessMarketV2ExecutionPlan.objects.count(), 0)
        with session_role("teruisi_ai_writer"):
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT plan_digest FROM public."
                        "ai_business_market_v2_execution_plans LIMIT 1")

    def test_missing_sql_context_proof_cannot_make_execution_plan(self):
        _, _, created = self.create_plan()
        body = {"schemaVersion":service.REQUEST_SCHEMA,
            "executionReportId":created["reportId"],
            "contextProofDigest":"a"*64}
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                patch.object(service.transport, "catalog", return_value=frozen_catalog()):
            candidate = service.prepare(body, self.admin)
        with djtest.override_settings(AI_MARKET_V2_EXECUTION_PLAN_ENABLED=True), \
                plan_attestor(), self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_execution_plan_root_or_policy_invalid"):
            service.attest(created["reportId"], candidate["planJson"])
        self.assertEqual(m.AiBusinessMarketV2ExecutionPlan.objects.count(), 0)
