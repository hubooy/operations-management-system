"""Disposable PostgreSQL proof; never connect to the formal database."""
from contextlib import contextmanager
from importlib import import_module
import secrets

from django import test as djtest
from django.conf import settings
from django.db import DatabaseError, connection
import psycopg
from psycopg import sql

from . import business_market_v6_paused_topology_persistence as topology_writer
from . import business_market_v6_source_ticket_contract as contract
from . import business_market_v6_source_ticket_replay as replay
from . import models as m
from .test_business_market_v6_paused_topology_role import PausedTopologyRoleTests
from .test_business_market_v2_material_role_bridge import session_role


MIGRATION = import_module(
    "ai_assistant.migrations.0079_business_market_v6_source_ticket")
ISSUE_CALL = MIGRATION.source.ISSUE.split("(", 1)[0]
OUTCOME_CALL = MIGRATION.source.OUTCOME.split("(", 1)[0]


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class SourceTicketRoleTests(PausedTopologyRoleTests):
    # Reuse the exact source/0077 fixture, not the five unrelated 0077 cases.
    test_real_login_create_cancel_outcome_and_closed_dispatch = None
    test_fifth_job_collision_rolls_back_first_four_and_report = None
    test_two_creates_serialize_under_revision_and_one_quota_wins = None
    test_catalog_rejects_same_type_relaxed_check = None
    test_catalog_rejects_reused_role_login_or_membership_drift = None

    @contextmanager
    def _ticket_login(self):
        database = self._isolated()
        password = secrets.token_urlsafe(32)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(MIGRATION.source.ROLE), sql.Literal(password)))
        connection.close()
        try:
            with psycopg.connect(host=database["HOST"], port=database["PORT"],
                    dbname=database["NAME"], user=MIGRATION.source.ROLE,
                    password=password, autocommit=True) as db:
                yield db
        finally:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                    sql.Identifier(MIGRATION.source.ROLE)))
            with connection.cursor() as cursor:
                MIGRATION.verify_catalog(cursor)

    def _create_0077(self):
        built = self._built("market-v6-source-ticket-role")
        with PausedTopologyRoleTests._login(self) as db, djtest.override_settings(
                AI_MARKET_V6_PAUSED_TOPOLOGY_RECORD_ENABLED=True):
            committed = topology_writer.create_once(db, built,
                port=int(settings.DATABASES["default"]["PORT"]))
        self.assertEqual(committed["status"], "committed_paused")
        return built

    def test_real_role_market_page_only_and_exact_owner(self):
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
        built = self._create_0077()
        snapshot = built["snapshot"]
        report_id = snapshot["reportId"]
        owner = snapshot["ownerEmail"]
        version = snapshot["ownerVersion"]
        with self._ticket_login() as db:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM " + MIGRATION.TABLE + " LIMIT 1")
            absent = db.execute("SELECT " + OUTCOME_CALL +
                "(%s,%s,%s,%s)", ["market-v6-page-" + "f"*48,
                report_id, owner, version]).fetchone()[0]
            self.assertEqual(absent["status"], "absent_observed")
            self.assertFalse(absent["agentReadPersisted"])
            raw = db.execute("SELECT " + ISSUE_CALL +
                "(%s,%s,%s,%s,%s)",
                [report_id, owner, version, "rank_entry_exit", 0]).fetchone()[0]
            ticket = contract.validate(raw)
            self.assertEqual(ticket["reportId"], report_id)
            self.assertEqual(ticket["jobId"], snapshot["jobs"][2]["jobId"])
            self.assertEqual(ticket["marketStatus"], "bound_selected_top_sample")
            self.assertEqual(ticket["shopSalesStatus"], "unknown_not_supplied")
            self.assertEqual(ticket["b2bStatus"], "unknown_not_supplied")
            self.assertFalse(ticket["agentReadPersisted"])
            outcome = db.execute("SELECT " + OUTCOME_CALL +
                "(%s,%s,%s,%s)",
                [ticket["ticketId"], report_id, owner, version]).fetchone()[0]
            self.assertEqual(outcome["status"], "committed_market_pointer_only")
            self.assertEqual(outcome["ticket"], ticket)
            self.assertEqual(db.execute("SELECT " + ISSUE_CALL +
                "(%s,%s,%s,%s,%s)", [report_id, owner, version,
                "rank_entry_exit", 0]).fetchone()[0], ticket)
            for args in (("other-report", owner, version,
                    "rank_entry_exit", 0),
                    (report_id, "other@example.invalid", version,
                    "rank_entry_exit", 0),
                    (report_id, owner, version+1, "rank_entry_exit", 0),
                    (report_id, owner, version, "price_band_summary", 0),
                    (report_id, owner, version, "rank_entry_exit", 19999)):
                with self.subTest(args=args), self.assertRaises(psycopg.Error):
                    db.execute("SELECT " + ISSUE_CALL +
                        "(%s,%s,%s,%s,%s)", list(args))
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT " + OUTCOME_CALL +
                    "(%s,%s,%s,%s)",
                    [ticket["ticketId"], "other-report", owner, version])
            with connection.cursor() as cursor:
                cursor.execute("GRANT teruisi_ai_reader TO " +
                    MIGRATION.source.ROLE)
            try:
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT " + OUTCOME_CALL + "(%s,%s,%s,%s)",
                        [ticket["ticketId"], report_id, owner, version])
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT " + ISSUE_CALL + "(%s,%s,%s,%s,%s)",
                        [report_id, owner, version, "rank_entry_exit", 0])
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("REVOKE teruisi_ai_reader FROM " +
                        MIGRATION.source.ROLE)
        with self.assertRaisesRegex(RuntimeError,
                "cannot discard persisted source tickets"):
            with connection.schema_editor() as editor:
                MIGRATION.uninstall(None, editor)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM " + MIGRATION.TABLE)
            self.assertEqual(cursor.fetchone(), (1,))
        self.assertEqual(m.AiAgentJobs.objects.filter(
            workflow_run_id=snapshot["workflowId"],
            status="paused", provider_round_count=0,
            tool_call_count=0).count(), 5)
        job_ids = [item["jobId"] for item in snapshot["jobs"]]
        self.assertFalse(m.AiAgentProviderDispatches.objects.filter(
            job_id__in=job_ids).exists())
        self.assertFalse(m.AiAgentToolDispatches.objects.filter(
            job_id__in=job_ids).exists())
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            with self.subTest(role=role), session_role(role):
                with self.assertRaises(DatabaseError), connection.cursor() as cursor:
                    cursor.execute("SELECT " + ISSUE_CALL + "(%s,%s,%s,%s,%s)",
                        [report_id, owner, version, "rank_entry_exit", 0])
        with djtest.override_settings(DJANGO_PROCESS_ROLE="ai_reader",
                AI_MARKET_V6_SOURCE_TICKET_REPLAY_ENABLED=True), \
                session_role("teruisi_ai_reader"):
            page = replay.replay_candidate(ticket, self.admin)
        self.assertTrue(page["pageBytesVerified"])
        self.assertFalse(page["protectedTicketProvenanceVerified"])
        self.assertFalse(page["agentReadPersisted"])
