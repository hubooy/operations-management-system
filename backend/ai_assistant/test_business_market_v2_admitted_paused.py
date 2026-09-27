"""Isolated PostgreSQL tests for material-only market v2 roots."""
from copy import deepcopy
import json

from django import test as djtest
from django.db import DatabaseError, connection, transaction

from . import business_market_v2_admitted_paused as service
from . import business_market_v2_material_admission as material_owner
from . import business_market_v2_parked_creation as parked
from . import models as m
from . import test_business_market_v2_material_admission as fixtures
from .market_v2_admitted_catalog import verify as verify_catalog
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2AdmittedPausedTests(djtest.TransactionTestCase):
    user = fixtures.MarketV2MaterialAdmissionTests.user
    request_body = fixtures.MarketV2MaterialAdmissionTests.request_body
    current_catalog = fixtures.MarketV2MaterialAdmissionTests.current_catalog
    create_fixed_report = fixtures.MarketV2MaterialAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.MarketV2MaterialAdmissionTests.planned_evidence_body
    setUp = fixtures.MarketV2MaterialAdmissionTests.setUp
    selector = fixtures.MarketV2MaterialAdmissionTests.selector
    parked_id = fixtures.MarketV2MaterialAdmissionTests.parked_id
    _attest_as_role = staticmethod(fixtures.MarketV2MaterialAdmissionTests._attest_as_role)

    def body(self, parked_id, client="admitted-market-v2"):
        return {"schemaVersion": service.REQUEST_SCHEMA,
            "clientRequestId": client, "parkedReportId": parked_id}

    def admitted(self):
        parked_id = self.parked_id()
        value = material_owner.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, value)
        return parked_id, service.create(self.body(parked_id), self.admin)

    def test_frozen_paused_catalog_rejects_disabled_job_guard(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_agent_jobs DISABLE TRIGGER "
                "ai_market_v2_admitted_job_guard")
            with self.assertRaisesRegex(ValueError, "trigger binding"):
                verify_catalog(cursor)
            transaction.set_rollback(True)

    def test_admitted_snapshot_is_distinct_paused_and_idempotent(self):
        parked_id, created = self.admitted()
        self.assertFalse(created["agentDispatchSupported"])
        self.assertFalse(created["replayed"])
        report = m.AiReportRun.objects.select_related("workflow").get(
            pk=created["reportId"])
        snapshot = json.loads(report.snapshot_json)
        flow_input = json.loads(report.workflow.input_json)
        self.assertEqual(snapshot["executionProfile"], service.PROFILE)
        self.assertEqual(snapshot["marketAdmission"]["parkedReportId"], parked_id)
        self.assertEqual(flow_input["marketAdmission"], snapshot["marketAdmission"])
        self.assertEqual(snapshot["proposedTools"], list(service.runtime.TOOL_ORDER))
        self.assertEqual(flow_input["allowedTools"], [])
        self.assertEqual(report.workflow.allowed_tools_json, "[]")
        self.assertEqual(report.workflow.status, "paused")
        self.assertEqual(report.workflow.model_id, "")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=report.workflow_id).exists())
        self.assertFalse(m.AiWorkflowNodeRuns.objects.filter(
            run_id=report.workflow_id).exists())
        original = m.AiReportRun.objects.select_related("workflow").get(pk=parked_id)
        self.assertEqual(original.workflow.status, "paused")
        self.assertEqual(original.workflow.error_code, parked.PAUSE_REASON)
        self.assertEqual(m.AiBusinessMarketV2Material.objects.count(), 1)
        replay = service.create(self.body(parked_id), self.admin)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["reportId"], report.id)

    def test_missing_material_wrong_owner_and_changed_selector_fail_closed(self):
        parked_id = self.parked_id()
        with self.assertRaises(AiError):
            service.create(self.body(parked_id), self.admin)
        value = material_owner.prepare_candidate(parked_id, self.admin)
        self._attest_as_role(parked_id, value)
        outside = self.user("market-admitted-outside@example.invalid", "admin", None)
        with self.assertRaises(AiError):
            service.create(self.body(parked_id, "outside-admitted"), outside)
        changed = deepcopy(self.body(parked_id))
        changed["parkedReportId"] = "missing-parked"
        with self.assertRaises((AiError, m.AiReportRun.DoesNotExist)):
            service.create(changed, self.admin)
        self.assertFalse(m.AiReportRun.objects.filter(
            snapshot_json__contains=service.PROFILE).exists())

    def test_database_rejects_any_node_job_or_activation(self):
        _, created = self.admitted()
        report = m.AiReportRun.objects.select_related("workflow").get(
            pk=created["reportId"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiWorkflowNodeRuns.objects.create(id="admitted-node-forbidden",
                run=report.workflow, node_key="market_b2b", position=2,
                node_type="agent", instruction="forbidden")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiAgentJobs.objects.create(id="admitted-job-forbidden",
                owner_email=report.owner_email, scope_json="null",
                client_request_id="admitted-job-forbidden", request_digest="a"*64,
                task="forbidden", workflow_run_id=report.workflow_id,
                workflow_node_key="market_b2b", status="paused", phase="paused")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(
                status="running", error_code="")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiReportRun.objects.filter(pk=report.id).update(
                snapshot_json=report.snapshot_json.replace('"registered":false',
                    '"registered":true'))
