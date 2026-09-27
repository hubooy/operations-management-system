"""Isolated PostgreSQL probes for a paused, distinct five-tool plan."""
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django import test as djtest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from psycopg import sql

from . import business_market_v2_execution_snapshot as service
from . import business_market_v2_execution_snapshot_contract as contract
from . import models as m
from . import test_business_market_v2_material_role_bridge as role_fixture
from .control_models import AiWriteAuthority
from .policy import AiError


def frozen_catalog():
    return json.loads((Path(__file__).parent / "fixtures" /
        "market_v2_five_tool_catalog.json").read_text(encoding="utf-8"))


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ExecutionSnapshotTests(djtest.TransactionTestCase):
    user = role_fixture.MarketV2MaterialRoleBridgeTests.user
    request_body = role_fixture.MarketV2MaterialRoleBridgeTests.request_body
    current_catalog = role_fixture.MarketV2MaterialRoleBridgeTests.current_catalog
    create_fixed_report = role_fixture.MarketV2MaterialRoleBridgeTests.create_fixed_report
    planned_evidence_body = role_fixture.MarketV2MaterialRoleBridgeTests.planned_evidence_body
    selector = role_fixture.MarketV2MaterialRoleBridgeTests.selector
    parked_id = role_fixture.MarketV2MaterialRoleBridgeTests.parked_id
    _attest_as_role = staticmethod(
        role_fixture.MarketV2MaterialRoleBridgeTests._attest_as_role)
    admitted = role_fixture.MarketV2MaterialRoleBridgeTests.admitted
    setUp = role_fixture.MarketV2MaterialRoleBridgeTests.setUp

    def _disable_generic_immutable_for_rollback_probe(self, table):
        self.assertTrue(connection.in_atomic_block)
        with connection.cursor() as cursor:
            cursor.execute("SELECT t.tgname FROM pg_catalog.pg_trigger t "
                "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal "
                "AND t.tgfoid=to_regprocedure("
                "'public.ai_immutable_record_guard()')", ["public." + table])
            names = [row[0] for row in cursor.fetchall()]
            self.assertEqual(len(names), 1)
            cursor.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                sql.Identifier("public", table), sql.Identifier(names[0])))
    def body(self, report_id, client="five-tool-execution-plan"):
        return {"schemaVersion": service.REQUEST_SCHEMA,
            "clientRequestId": client, "admittedReportId": report_id}

    def create_plan(self):
        parked_id, admitted, _, _ = self.admitted()
        with patch.object(service.transport, "catalog", return_value=frozen_catalog()):
            created = service.create(self.body(admitted["reportId"]), self.admin)
        return parked_id, admitted, created

    def test_distinct_paused_plan_and_idempotent_replay(self):
        parked_id, admitted, created = self.create_plan()
        report = m.AiReportRun.objects.select_related("workflow").get(
            pk=created["reportId"])
        snapshot = json.loads(report.snapshot_json)
        flow = report.workflow
        self.assertEqual(snapshot["executionProfile"], contract.PROFILE)
        self.assertEqual(snapshot["executionRoot"]["parkedReportId"], parked_id)
        self.assertEqual(snapshot["executionRoot"]["admittedReportId"],
            admitted["reportId"])
        self.assertEqual(flow.graph_digest,
            contract.GRAPH_DIGESTS[snapshot["withBudget"]])
        self.assertEqual(flow.tool_policy_digest, contract.CATALOG_DIGEST)
        self.assertEqual(json.loads(flow.allowed_tools_json), list(contract.TOOL_ORDER))
        self.assertEqual(flow.status, "paused")
        self.assertEqual(flow.error_code, contract.PAUSE_REASON)
        self.assertEqual(flow.model_id, "")
        self.assertFalse(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id).exists())
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
        self.assertEqual(m.AiReportRun.objects.get(pk=admitted["reportId"]).workflow.status,
            "paused")
        with patch.object(service.transport, "catalog", return_value=frozen_catalog()):
            replay = service.create(self.body(admitted["reportId"]), self.admin)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["reportId"], report.id)

    def test_catalog_owner_and_execution_descendants_fail_closed(self):
        _, admitted, created = self.create_plan()
        flow_id = created["workflowId"]
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiWorkflowNodeRuns.objects.create(id="market-execution-node-forbidden",
                run_id=flow_id, node_key="market_b2b", position=2,
                node_type="agent", instruction="forbidden")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiAgentJobs.objects.create(id="market-execution-job-forbidden",
                owner_email=self.admin.email.lower(), scope_json="null",
                client_request_id="market-execution-job-forbidden",
                request_digest="a"*64, task="forbidden", workflow_run_id=flow_id,
                workflow_node_key="market_b2b", status="paused", phase="paused")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiWorkflowRuns.objects.filter(pk=flow_id).update(status="running")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiWorkflowRuns.objects.filter(pk=flow_id).update(model_id="paid-model")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiReportRun.objects.filter(pk=created["reportId"]).update(
                snapshot_json='{}')
        other = self.user("market-plan-outside@example.invalid", "admin", None)
        with patch.object(service.transport, "catalog", return_value=frozen_catalog()):
            with self.assertRaises(AiError):
                service.create(self.body(admitted["reportId"], "outside-plan"), other)
        wrong_catalog = frozen_catalog()
        wrong_catalog[0]["name"] = "old-v1-tool"
        with patch.object(service.transport, "catalog", return_value=wrong_catalog):
            with self.assertRaises(Exception):
                service.create(self.body(admitted["reportId"], "bad-catalog"),
                    self.admin)
        self.assertEqual(m.AiReportRun.objects.filter(
            snapshot_json__contains=contract.PROFILE).count(), 1)

    def test_real_writer_persists_only_paused_plan_without_material_select(self):
        _, admitted, _, _ = self.admitted()
        AiWriteAuthority.objects.filter(id=1).update(status="postgres",
            authority_epoch=uuid4(), cutover_id="market-execution-isolated",
            migration_verify_run_id="market-execution-isolated",
            activated_at=timezone.now())
        authority = AiWriteAuthority.objects.get(id=1)
        with djtest.override_settings(DJANGO_PROCESS_ROLE="ai_writer",
                AI_WRITE_AUTHORITY_EPOCH=str(authority.authority_epoch),
                AI_WRITE_CUTOVER_ID=authority.cutover_id), role_fixture.session_role(
                    "teruisi_ai_writer"), patch.object(service.transport, "catalog",
                    return_value=frozen_catalog()):
            created = service.create(self.body(admitted["reportId"],
                "writer-market-execution"), self.admin)
            with self.assertRaises(DatabaseError):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT report_id FROM public."
                        "ai_business_market_v2_materials LIMIT 1")
        self.assertFalse(created["agentDispatchSupported"])
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=created["workflowId"]).status,
            "paused")

    def test_old_parked_profile_cannot_escape_by_update(self):
        parked_id, _, _ = self.create_plan()
        parked = m.AiReportRun.objects.get(pk=parked_id)
        # The older generic immutable trigger also rejects both writes. Disable
        # only that exact trigger inside a rollback-only test transaction to
        # prove the new market-specific guards independently enforce the fence.
        with transaction.atomic():
            self._disable_generic_immutable_for_rollback_probe("ai_report_runs")
            self._disable_generic_immutable_for_rollback_probe("ai_workflow_runs")
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_parked_report_immutable"), transaction.atomic():
                m.AiReportRun.objects.filter(pk=parked_id).update(snapshot_json="{}")
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_parked_workflow_immutable"), transaction.atomic():
                m.AiWorkflowRuns.objects.filter(pk=parked.workflow_id).update(
                    input_json="{}", allowed_tools_json="[]")
            transaction.set_rollback(True)

    def test_privileged_corrupt_old_child_cannot_move_away_from_v2(self):
        _, _, created = self.create_plan()
        # Model a preexisting corrupt child using only this rollback-only,
        # isolated superuser probe; ordinary INSERT is rejected by 0060.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
            m.AiAgentJobs.objects.create(id="market-execution-corrupt-child",
                owner_email=self.admin.email.lower(), scope_json="null",
                client_request_id="market-execution-corrupt-child",
                request_digest="a"*64, task="isolated-negative-probe",
                workflow_run_id=created["workflowId"],
                workflow_node_key="market_b2b", status="paused", phase="paused")
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = origin")
            self._disable_generic_immutable_for_rollback_probe("ai_agent_jobs")
            with self.assertRaisesRegex(DatabaseError,
                    "ai_market_v2_execution_child_dispatch_disabled"), transaction.atomic():
                m.AiAgentJobs.objects.filter(pk="market-execution-corrupt-child").update(
                    workflow_run_id=self.report.workflow_id)
            transaction.set_rollback(True)
