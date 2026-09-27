"""Disposable PostgreSQL proof of atomic paused five-job topology.

The dedicated role is activated only inside this isolated database and reset
to NOLOGIN/password NULL before the role test returns.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
from threading import Barrier
from unittest.mock import patch

from django import test as djtest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
import psycopg
from psycopg import sql

from . import business_market_v2_cost_admission as cost_service
from . import business_market_v6_paused_topology_owner as owner
from . import business_market_v6_paused_topology_contract as contract
from . import business_market_v6_paused_topology_persistence as writer
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_paid_gate as paid_gate
from . import models as m
from business_analysis.contracts import canonical
from .policy import AiError
from . import test_business_market_v2_cost_admission as source_module
from . import test_business_promotion_content as completed_module
from . import test_business_promotion_approved_content as approval_module
from .test_business_market_v2_cost_admission import cost_attestor
from .test_business_market_v2_material_role_bridge import session_role
from importlib import import_module


MIGRATION = import_module(
    "ai_assistant.migrations.0077_business_market_v6_paused_topology")


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class PausedTopologyRoleTests(djtest.TransactionTestCase):
    source_fixture = source_module.MarketV2CostAdmissionTests
    completed_fixture = completed_module.PromotionContentTests
    approval_fixture = approval_module.PromotionApprovedContentTests
    user = source_fixture.user
    request_body = source_fixture.request_body
    current_catalog = source_fixture.current_catalog
    create_fixed_report = source_fixture.create_fixed_report
    planned_evidence_body = source_fixture.planned_evidence_body
    selector = source_fixture.selector
    parked_id = source_fixture.parked_id
    _attest_as_role = staticmethod(source_fixture._attest_as_role)
    admitted = source_fixture.admitted
    running_job = completed_fixture.running_job
    base = completed_fixture.base
    read = completed_fixture.read
    append = completed_fixture.append
    package = completed_fixture.package
    promotion = completed_fixture.promotion
    complete = completed_fixture.complete
    five_completed = completed_fixture.five_completed
    approved = approval_fixture.approved
    complete_flow = approval_fixture.complete_flow
    body = source_fixture.body
    create_plan = source_fixture.create_plan
    attested = source_fixture.attested
    prepared = source_fixture.prepared
    plan_and_model = source_fixture.plan_and_model
    input = source_fixture.input
    databases = {"default"}

    def setUp(self):
        source_module.MarketV2CostAdmissionTests.setUp(self)
        # The owning source report is completed through its real synthetic
        # five-Agent receipts and review. Four historical active flows would
        # otherwise correctly exhaust the owner=4 SQL quota before 0077.
        original = self.report
        with patch.object(self, "create_fixed_report", return_value=original), \
                patch("ai_assistant.provider.turn") as provider, \
                patch("ai_assistant.transport.execute_tool") as external_tool:
            self.five_completed()
            self.approved(original)
            self.complete_flow(original)
            provider.assert_not_called()
            external_tool.assert_not_called()
        self.assertEqual(m.AiWorkflowRuns.objects.get(
            pk=original.workflow_id).status, "completed")

    @staticmethod
    def _isolated():
        database = settings.DATABASES["default"]
        if (connection.vendor != "postgresql" or
                database["NAME"] != "test_teruisi_ai_rehearsal" or
                database["HOST"] != "127.0.0.1" or
                not 55440 <= int(database["PORT"]) <= 55999 or
                str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")):
            raise AssertionError("0077 role test requires isolated PostgreSQL")
        return database

    @contextmanager
    def _login(self):
        database = self._isolated()
        password = secrets.token_urlsafe(32)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(MIGRATION.v6.ROLE), sql.Literal(password)))
        self._role_password = password
        connection.close()
        try:
            with psycopg.connect(host=database["HOST"],port=database["PORT"],
                    dbname=database["NAME"],user=MIGRATION.v6.ROLE,
                    password=password,autocommit=True) as db:
                yield db
        finally:
            self._role_password = None
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                    sql.Identifier(MIGRATION.v6.ROLE)))
            with connection.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)

    def _built(self, client):
        created, plan_id, model = self.plan_and_model()
        tariff, jobs, at = self.input(model)
        with djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True), \
                session_role("teruisi_ai_reader"):
            candidate = cost_service.prepare(created["reportId"], tariff,
                jobs, 5, "b" * 64, self.admin, at_utc=at)
        with djtest.override_settings(AI_MARKET_V2_COST_CANDIDATE_ENABLED=True), \
                cost_attestor():
            cost_service.record(plan_id, candidate["candidateJson"])
        self.assertEqual(self._active_counts(),(3,0))
        with djtest.override_settings(
                AI_MARKET_V2_READ_PLAN_V6_ENABLED=True,
                AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True), \
                session_role("teruisi_ai_reader"):
            return owner.prepare(created["reportId"], client, self.admin)

    def _active_counts(self):
        active_flows = m.AiWorkflowRuns.objects.filter(
            owner_email=self.admin.email.lower(),
            status__in=("queued","running","paused","waiting_review")).count()
        active_jobs = m.AiAgentJobs.objects.filter(
            owner_email=self.admin.email.lower(),
            status__in=("queued","running","paused")).count()
        return active_flows,active_jobs

    def test_real_login_create_cancel_outcome_and_closed_dispatch(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        built = self._built("market-v6-role-positive")
        with self._login() as db, djtest.override_settings(
                AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED=True):
            value = writer.create_once(db, built,
                port=int(settings.DATABASES["default"]["PORT"]))
            self.assertEqual(value["status"], "committed_paused", value)
            self.assertEqual(self._active_counts(),(4,5))
            report_id = built["snapshot"]["reportId"]
            flow_id = built["snapshot"]["workflowId"]
            self.assertEqual(m.AiReportRun.objects.filter(pk=report_id).count(), 1)
            self.assertEqual(m.AiWorkflowRuns.objects.filter(pk=flow_id,
                status="paused",model_id="",provider_round_count=0,
                tool_call_count=0,allowed_tools_json="[]").count(), 1)
            self.assertEqual(m.AiAgentJobs.objects.filter(
                workflow_run_id=flow_id,status="paused",model_id="",
                provider_round_count=0,tool_call_count=0,
                allowed_tools_json="[]").count(), 5)
            self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(
                run_id=flow_id,status="pending").count(), 6)
            with self.assertRaises(AiError):
                paid_gate.before_reservation(m.AiAgentJobs.objects.get(
                    pk=built["snapshot"]["jobs"][0]["jobId"]))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public." +
                    "protected_business_market_v6_topologies LIMIT 1")
            self.assertEqual(writer.outcome(db,
                owner_email=built["snapshot"]["ownerEmail"],
                source_report_id=built["snapshot"]["sourceExecutionReportId"],
                client_request_id=built["snapshot"]["clientRequestId"],
                request_digest=built["intentDigest"],
                port=int(settings.DATABASES["default"]["PORT"]))["status"],
                "committed_paused")
            with connection.cursor() as cursor:
                cursor.execute("GRANT teruisi_ai_reader TO " + MIGRATION.v6.ROLE)
            try:
                with self.assertRaises(psycopg.Error) as denied_outcome:
                    db.execute(writer.OUTCOME,[built["snapshot"]["ownerEmail"],
                        built["snapshot"]["sourceExecutionReportId"],
                        built["snapshot"]["clientRequestId"],
                        built["intentDigest"]])
                self.assertIn("ai_market_v6_outcome_unavailable",
                    str(denied_outcome.exception))
                deny_cancel = contract.cancel_request(report_id,
                    built["snapshot"]["ownerEmail"],1,"c"*64)
                with self.assertRaises(psycopg.Error) as denied_cancel:
                    db.execute(writer.CANCEL,[deny_cancel["requestJson"]])
                self.assertIn("ai_market_v6_cancel_unavailable",
                    str(denied_cancel.exception))
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("REVOKE teruisi_ai_reader FROM " +
                        MIGRATION.v6.ROLE)
            job_id = built["snapshot"]["jobs"][0]["jobId"]
            forbidden = (
                (m.AiAgentProviderDispatches,
                    {"id":"v6-forbidden-provider","job_id":job_id,
                     "dispatch_ordinal":1,
                     "owner_email":built["snapshot"]["ownerEmail"],
                     "actor_role":"admin","model_id":"synthetic-probe",
                     "model_version":1,"tool_policy_digest":"a"*64,
                     "request_digest":"b"*64,"state":"calling",
                     "lease_epoch":1}),
                (m.AiAgentCheckpoints,
                    {"id":"v6-forbidden-checkpoint","job_id":job_id,
                     "ordinal":1,"kind":"checkpoint", "state_json":"{}",
                     "output_digest":""}),
                (m.AiAgentEvents,
                    {"id":"v6-forbidden-event","job_id":job_id,
                     "owner_email":built["snapshot"]["ownerEmail"],
                     "actor_email":built["snapshot"]["ownerEmail"],
                     "event_type":"probe","from_status":"paused",
                     "to_status":"paused","job_version":1,
                     "details_json":"{}"}),
                (m.AiWorkflowEvents,
                    {"id":"v6-forbidden-flow-event","run_id":flow_id,
                     "owner_email":built["snapshot"]["ownerEmail"],
                     "actor_email":built["snapshot"]["ownerEmail"],
                     "event_type":"probe","from_status":"paused",
                     "to_status":"paused","run_version":1,
                     "details_json":"{}"}),
                (m.AiReportDelivery,
                    {"report_id":report_id,"channel_id":"probe",
                     "channel_digest":"a"*64,"status":"reserved"}),
                (m.AiBusinessFileRun,
                    {"id":"v6-forbidden-file-run","report_id":report_id,
                     "owner_email":built["snapshot"]["ownerEmail"],
                     "scope_json":"null","draft":False,
                     "renderer_version":1,"binding_digest":"b"*64,
                     "status":"queued"}),
            )
            for model, values in forbidden:
                with self.subTest(model=model.__name__), \
                        self.assertRaises(DatabaseError) as blocked:
                    with transaction.atomic():
                        model.objects.create(**values)
                self.assertIn("ai_market_v6_topology_effect_closed",
                    str(blocked.exception))
                self.assertFalse(model.objects.filter(pk=values["id"]
                    if "id" in values else values["report_id"]).exists())
            cancel = contract.cancel_request(report_id,
                built["snapshot"]["ownerEmail"],1,"a"*64)
            cancelled = writer.cancel_once(db,cancel,
                port=int(settings.DATABASES["default"]["PORT"]))
            self.assertEqual(cancelled["status"],"cancelled",cancelled)
            self.assertEqual(self._active_counts(),(3,0))
            self.assertEqual(m.AiAgentJobs.objects.filter(
                workflow_run_id=flow_id,status="cancelled").count(),5)
            self.assertEqual(writer.outcome(db,
                owner_email=built["snapshot"]["ownerEmail"],
                source_report_id=built["snapshot"]["sourceExecutionReportId"],
                client_request_id=built["snapshot"]["clientRequestId"],
                request_digest=built["intentDigest"],
                port=int(settings.DATABASES["default"]["PORT"]))["status"],
                "cancelled")
            duplicate = writer.cancel_once(db,cancel,
                port=int(settings.DATABASES["default"]["PORT"]))
            self.assertEqual(duplicate["status"],"unknown")
            self.assertFalse(duplicate["retryAllowed"])
            with djtest.override_settings(
                    AI_MARKET_V2_READ_PLAN_V6_ENABLED=True,
                    AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True), \
                    session_role("teruisi_ai_reader"):
                replacement = owner.prepare(
                    built["snapshot"]["sourceExecutionReportId"],
                    "market-v6-role-after-cancel", self.admin)
            next_value = writer.create_once(db,replacement,
                port=int(settings.DATABASES["default"]["PORT"]))
            self.assertEqual(next_value["status"],"committed_paused",
                next_value)
            self.assertEqual(self._active_counts(),(4,5))

    def test_fifth_job_collision_rolls_back_first_four_and_report(self):
        built = self._built("market-v6-role-fifth")
        last = built["snapshot"]["jobs"][4]
        m.AiAgentJobs.objects.create(id=last["jobId"],
            owner_email=built["snapshot"]["ownerEmail"],
            client_request_id="preexisting-failed-job",
            request_digest="f"*64,scope_json="null",task="collision",
            status="failed",phase="failed")
        with self._login() as db, djtest.override_settings(
                AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED=True):
            value = writer.create_once(db,built,
                port=int(settings.DATABASES["default"]["PORT"]))
            self.assertEqual(value["status"],"unknown")
            report_id = built["snapshot"]["reportId"]
            flow_id = built["snapshot"]["workflowId"]
            self.assertFalse(m.AiReportRun.objects.filter(pk=report_id).exists())
            self.assertFalse(m.AiWorkflowRuns.objects.filter(pk=flow_id).exists())
            self.assertEqual(m.AiAgentJobs.objects.filter(
                workflow_run_id=flow_id).count(),0)
            self.assertEqual(writer.outcome(db,
                owner_email=built["snapshot"]["ownerEmail"],
                source_report_id=built["snapshot"]["sourceExecutionReportId"],
                client_request_id=built["snapshot"]["clientRequestId"],
                request_digest=built["intentDigest"],
                port=int(settings.DATABASES["default"]["PORT"]))["status"],
                "absent_observed")

    def test_two_creates_serialize_under_revision_and_one_quota_wins(self):
        first = self._built("market-v6-role-race-one")
        with djtest.override_settings(
                AI_MARKET_V2_READ_PLAN_V6_ENABLED=True,
                AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True), \
                session_role("teruisi_ai_reader"):
            second = owner.prepare(first["snapshot"]["sourceExecutionReportId"],
                "market-v6-role-race-two", self.admin)
        database = self._isolated()
        barrier = Barrier(2)
        with self._login():
            def attempt(built):
                with psycopg.connect(host=database["HOST"],
                        port=database["PORT"],dbname=database["NAME"],
                        user=MIGRATION.v6.ROLE,password=self._role_password,
                        autocommit=True) as db:
                    barrier.wait(timeout=30)
                    try:
                        value = db.execute(writer.CREATE,[built["intentJson"],
                            built["snapshotJson"],canonical(execution.graph(
                                built["snapshot"]["withBudget"]))]).fetchone()[0]
                        return value["status"]
                    except psycopg.Error as error:
                        return str(error).splitlines()[0]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt,(first,second)))
        self.assertEqual(results.count("committed_paused"),1,results)
        self.assertTrue(any("ai_market_v6_topology_quota" in item
            for item in results),results)
        self.assertEqual(self._active_counts(),(4,5))
        self.assertEqual(m.AiAgentJobs.objects.filter(
            workflow_run_id__in=[first["snapshot"]["workflowId"],
                second["snapshot"]["workflowId"]]).count(),5)

    def test_catalog_rejects_same_type_relaxed_check(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT conname FROM pg_catalog.pg_constraint "
                    "WHERE conrelid=%s::regclass AND contype='c' AND "
                    "pg_catalog.pg_get_constraintdef(oid) LIKE %s",
                    [MIGRATION.v6.TABLE,"%owner_version%"])
                name = cursor.fetchone()[0]
                cursor.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                    sql.SQL(MIGRATION.v6.TABLE),sql.Identifier(name)))
                cursor.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} "
                    "CHECK (owner_version >= 0)").format(
                    sql.SQL(MIGRATION.v6.TABLE),sql.Identifier(name)))
                with self.assertRaisesRegex(RuntimeError,
                        "exact check expression drift"):
                    MIGRATION.verify_catalog(cursor)
            transaction.set_rollback(True)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)

    def test_catalog_rejects_reused_role_login_or_membership_drift(self):
        for statement in (
                "ALTER ROLE " + MIGRATION.v6.ROLE + " LOGIN PASSWORD NULL",
                "GRANT teruisi_ai_reader TO " + MIGRATION.v6.ROLE):
            with self.subTest(statement=statement), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                transaction.set_rollback(True)
            with connection.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)
