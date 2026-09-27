"""Isolated PostgreSQL target: 0075 report-bound page with real DB roles.

The 0071 fixture is deliberately a synthetic SQL identity vector. This test
proves the database page ACL and linkage only; it cannot prove a valid v4 HMAC.
"""
from importlib import import_module
import os

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from access_control.models import AppUser

from . import (business_v4_report_restricted_page_sql as page_sql,
    models as m)
from .policy import canonical, digest
from .test_business_v4_report_link_role import V4ReportLinkRoleTarget as prior


@override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class V4ReportRestrictedPageRoleTarget(TransactionTestCase):
    user = prior.user
    call = prior.call
    bundle = prior.bundle
    input_for = prior.input_for
    insert = prior.insert
    seed = prior.seed
    collect_body = prior.collect_body
    database = prior.database
    three_window_v2 = prior.three_window_v2
    synthetic_v4_identity = prior.synthetic_v4_identity
    workflow_for = prior.workflow_for
    insert_with_intent = prior.insert_with_intent
    setUp = prior.setUp

    def _three_linked_pages(self):
        request = self.three_window_v2()
        parent, seal = self.synthetic_v4_identity(request, "restricted")
        bundle = self.bundle()
        bundle[0]["scope"] = {"platform": "京东",
            "shop": self.query["shop"],
            "startDate": self.query["startDate"],
            "endDate": self.query["endDate"]}
        flow = self.workflow_for(bundle)
        self.assertTrue(self.insert_with_intent(bundle, flow, parent, seal,
            expected_success=True))
        sources = {source.source_key: source for source in
            m.AiBusinessV4Source.objects.filter(run=parent)}
        raw = canonical({"items": [], "pad": "x" * 11})
        self.assertEqual(len(raw.encode("utf-8")), 32)
        now = timezone.now()
        with transaction.atomic(), connection.cursor() as cursor:
            for table in ("ai_business_v4_chunks",
                    "ai_business_v4_tool_receipts"):
                cursor.execute("ALTER TABLE public." + table +
                    " DISABLE TRIGGER USER")
            try:
                for window in ("current", "previous", "yearAgo"):
                    source = sources["promotion-" + window]
                    audit = m.AiToolAuditLogs.objects.create(
                        id="restricted-audit-" + window,
                        request_id="restricted-request-" + window,
                        invocation_id="restricted-invocation-" + window,
                        actor_email=self.admin.email, actor_role="admin",
                        surface="business_collection",
                        tool_name="get_business_source_page",
                        arguments_json=canonical({"argumentsDigest": "d" * 64}),
                        status="succeeded", response_digest=digest(raw),
                        created_at=now)
                    chunk = m.AiBusinessV4Chunk.objects.create(
                        id="restricted-chunk-" + window, run=parent,
                        source=source, sequence=1, payload_json=raw,
                        payload_digest=digest(raw),
                        source_ref=source.source_ref,
                        source_revision=source.source_revision,
                        row_count=0, created_at=now)
                    m.AiBusinessV4ToolReceipt.objects.create(
                        chunk=chunk, audit=audit, run=parent, source=source,
                        sequence=1, actor_email=self.admin.email,
                        request_id=audit.request_id,
                        invocation_id=audit.invocation_id,
                        tool_name=audit.tool_name,
                        surface="business_collection",
                        response_digest=digest(raw), payload_bytes=32,
                        created_at=now)
            finally:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                for table in ("ai_business_v4_tool_receipts",
                        "ai_business_v4_chunks"):
                    cursor.execute("ALTER TABLE public." + table +
                        " ENABLE TRIGGER USER")
        return bundle[0]["reportId"], parent, sources, raw, request

    def test_reader_gets_only_exact_linked_page_and_no_physical_table(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0075")
        migration = import_module(
            "ai_assistant.migrations.0075_business_v4_report_restricted_page")
        with connection.cursor() as cursor:
            migration.verify_catalog(cursor)
        report_id, parent, sources, raw, request = self._three_linked_pages()
        other_parent, other_seal = self.synthetic_v4_identity(request,
            "restricted-other")
        other_bundle = self.bundle()
        other_bundle[0]["scope"] = {"platform": "京东",
            "shop": self.query["shop"],
            "startDate": self.query["startDate"],
            "endDate": self.query["endDate"]}
        other_flow = self.workflow_for(other_bundle)
        self.assertTrue(self.insert_with_intent(other_bundle, other_flow,
            other_parent, other_seal, expected_success=True))
        other_report_id = other_bundle[0]["reportId"]
        actor_version = AppUser.objects.get(email=self.admin.email).version
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            source = sources["promotion-current"]
            query = ("SELECT * FROM " + page_sql.PAGE.split("(", 1)[0] +
                "(%s,%s,%s,%s,%s,%s)")
            with self.assertRaises(psycopg.errors.RaiseException):
                db.execute(query, [report_id, parent.id, source.id, 1,
                    self.admin.email, actor_version])
            db.execute("SET default_transaction_read_only=on")
            self.assertEqual(db.execute("SHOW transaction_read_only"
                ).fetchone(), ("on",))
            for window in ("current", "previous", "yearAgo"):
                source = sources["promotion-" + window]
                args = [report_id, parent.id, source.id, 1,
                    self.admin.email, actor_version]
                row = db.execute(query, args).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[1:4],
                    (raw, digest(raw), source.source_ref))
                self.assertEqual(row[5], 0)
                self.assertEqual(row[6:8], (digest(raw), digest(raw)))
            source = sources["promotion-current"]
            args = [report_id, parent.id, source.id, 1,
                self.admin.email, actor_version]
            for altered in (
                    [other_report_id, *args[1:]],
                    [report_id, parent.id, sources["finance-context"].id,
                     *args[3:]],
                    [report_id, parent.id, source.id, 2, *args[4:]],
                    [report_id, parent.id, source.id, 1,
                     self.admin.email, actor_version + 1]):
                with self.subTest(altered=altered), self.assertRaises(
                        psycopg.errors.RaiseException):
                    db.execute(query, altered).fetchone()
            for table in ("ai_business_v4_chunks",
                    "ai_business_v4_tool_receipts", "ai_tool_audit_logs",
                    "protected_business_v4_report_source_links"):
                with self.subTest(table=table), self.assertRaises(
                        psycopg.errors.InsufficientPrivilege):
                    db.execute("SELECT * FROM public." + table + " LIMIT 1")

    def test_catalog_rejects_owner_and_third_role_execute_drift(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0075")
        migration = import_module(
            "ai_assistant.migrations.0075_business_v4_report_restricted_page")
        with transaction.atomic(), connection.cursor() as cursor:
            migration.verify_catalog(cursor)
            cursor.execute("ALTER FUNCTION " + page_sql.PAGE +
                " OWNER TO teruisi_ai_budget_v11_key_owner")
            with self.assertRaisesRegex(RuntimeError, "owner|catalog"):
                migration.verify_catalog(cursor)
            transaction.set_rollback(True)
        with transaction.atomic(), connection.cursor() as cursor:
            migration.verify_catalog(cursor)
            cursor.execute("GRANT EXECUTE ON FUNCTION " + page_sql.PAGE +
                " TO teruisi_ai_budget_v11_key_owner")
            with self.assertRaisesRegex(RuntimeError, "ACL"):
                migration.verify_catalog(cursor)
            transaction.set_rollback(True)

    def test_actual_reader_login_is_read_only_and_can_use_only_scoped_read(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL 0075")
        report_id, parent, sources, raw, _ = self._three_linked_pages()
        actor_version = AppUser.objects.get(email=self.admin.email).version
        password = os.getenv("TERUISI_AI_0075_SYNTHETIC_READER_PASSWORD")
        self.assertIsNotNone(password)
        self.assertEqual(len(password), 64)
        role = "teruisi_ai_reader"
        settings = connection.settings_dict
        with psycopg.connect(host=settings["HOST"], port=settings["PORT"],
                dbname=settings["NAME"], user=role,
                password=password, autocommit=True) as reader:
            self.assertEqual(reader.execute("SELECT session_user,"
                "current_setting('transaction_read_only')"
                ).fetchone(), (role, "on"))
            source = sources["promotion-current"]
            row = reader.execute("SELECT * FROM " +
                page_sql.PAGE.split("(", 1)[0] +
                "(%s,%s,%s,%s,%s,%s)",
                [report_id, parent.id, source.id, 1,
                 self.admin.email, actor_version]).fetchone()
            self.assertEqual(row[1], raw)
            self.assertEqual(row[2], digest(raw))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                reader.execute("SELECT " +
                    page_sql.RO_BINDINGS.split("(", 1)[0] +
                    "(%s)", [parent.id])
