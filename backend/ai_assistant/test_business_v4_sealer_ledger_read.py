"""Real isolated-role checks for the default-closed v4 sealer ledger ACL."""
import re
from types import SimpleNamespace

import psycopg
from django.db import connection
from django.test import TransactionTestCase

from access_control.models import AppUser

from . import business_v4_validation as validation, models as m
from .policy import AiError
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as gate


class BusinessV4SealerLedgerReadTests(TransactionTestCase):
    promotion_owner = gate.promotion_owner
    rebuild_plan = gate.rebuild_plan
    finance_owner = gate.finance_owner
    owner = gate.owner
    collect = gate.collect
    complete_mixed = gate.complete_mixed
    attempt = gate.attempt
    database = gate.database
    tearDown = gate.tearDown

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure('public.ai_v4_sealer_read_context("
                "text,text,text,bigint)')")
            if cursor.fetchone()[0] is not None:
                self.skipTest("0039直接读候选已由0040窄流替代；历史ACL由独立升级演练验收")
        gate.setUp(self)

    def read_gate(self, db, run_id, attempt_id, actor=None, version=None):
        actual_version = AppUser.objects.get(email=self.principal.email).version
        return db.execute("SELECT run_id,attempt_id,parent_version,directory_digest,"
            "source_count FROM public.ai_v4_sealer_ledger_read_gate(%s,%s,%s,%s)",
            [run_id, attempt_id, actor or self.principal.email,
                actual_version if version is None else version]).fetchone()

    def test_true_sealer_session_reads_bounded_same_run_segments_and_mac(self):
        attempt_id = self.attempt()
        first = m.AiBusinessV4ValidationSegment.objects.filter(
            attempt_id=attempt_id).order_by("source__ordinal").first()
        self.assertIsNotNone(first)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            result = self.read_gate(db, self.parent.id, attempt_id)
            self.assertEqual(result[:2], (self.parent.id, attempt_id))
            self.assertEqual(result[4], m.AiBusinessV4Source.objects.filter(
                run=self.parent).count())
            rows = db.execute("SELECT id,attempt_id,run_id,source_id,"
                "segment_index,start_sequence,end_sequence,source_version,"
                "source_ref,source_revision,previous_segment_digest,"
                "progress_json,progress_digest,proof_digest,proof_mac "
                "FROM public.ai_business_v4_validation_segments WHERE id=%s",
                [first.id])
            record = rows.fetchone()
            fields = [column.name for column in rows.description]
            segment = SimpleNamespace(**dict(zip(fields, record)),
                attempt=SimpleNamespace(key_id=validation._key()[1]))
            progress = validation._verified_segment(segment, *validation._key())
            self.assertEqual(progress["pageCount"], first.end_sequence)
            audit = db.execute("SELECT sequence,audit_id,request_id,invocation_id,"
                "actor_email,actor_role,tool_name,arguments_digest,response_digest,"
                "created_at FROM public.ai_v4_sealer_receipt_audit_segment("
                "%s,%s,%s,%s,%s,%s)", [self.parent.id, attempt_id,
                    first.source_id, first.segment_index, self.principal.email,
                    AppUser.objects.get(email=self.principal.email).version]).fetchall()
            self.assertEqual(len(audit), first.end_sequence-first.start_sequence+1)
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", row[7])
                and row[5] == "admin" for row in audit))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT arguments_json FROM public.ai_tool_audit_logs LIMIT 1")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT email FROM public.access_control_users LIMIT 1")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.finance_lines LIMIT 1")

    def test_wrong_run_actor_and_revoked_actor_fail_closed(self):
        attempt_id = self.attempt()
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).first()
        self.assertIsNotNone(other)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.Error):
                self.read_gate(db, other.id, attempt_id)
            with self.assertRaises(psycopg.Error):
                self.read_gate(db, self.parent.id, attempt_id,
                    actor="forged@example.invalid")
            with self.assertRaises(psycopg.Error):
                self.read_gate(db, self.parent.id, attempt_id,
                    version=AppUser.objects.get(email=self.principal.email).version + 1)
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.Error):
                self.read_gate(db, self.parent.id, attempt_id)

    def test_extra_write_grant_is_rejected_and_reader_stays_denied(self):
        attempt_id = self.attempt()
        with connection.cursor() as cursor:
            cursor.execute("GRANT UPDATE ON public.ai_business_v4_chunks "
                "TO teruisi_ai_seal_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                with self.assertRaises(psycopg.Error):
                    self.read_gate(db, self.parent.id, attempt_id)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE UPDATE ON public.ai_business_v4_chunks "
                    "FROM teruisi_ai_seal_writer")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.read_gate(db, self.parent.id, attempt_id)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_business_v4_validation_segments LIMIT 1")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.read_gate(db, self.parent.id, attempt_id)

    def test_unrelated_authority_column_grant_is_rejected(self):
        attempt_id = self.attempt()
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT (migration_verify_run_id) ON "
                "public.ai_write_authority TO teruisi_ai_seal_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                with self.assertRaises(psycopg.Error):
                    self.read_gate(db, self.parent.id, attempt_id)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE SELECT (migration_verify_run_id) ON "
                    "public.ai_write_authority FROM teruisi_ai_seal_writer")

    def test_control_column_write_grants_are_rejected(self):
        attempt_id = self.attempt()
        for permission, column, table in (
                ("UPDATE", "cutover_id", "ai_write_authority"),
                ("INSERT", "arguments_json", "ai_tool_audit_logs"),
                ("UPDATE", "status", "access_control_users")):
            with self.subTest(table=table, permission=permission):
                with connection.cursor() as cursor:
                    cursor.execute(f"GRANT {permission} ({column}) ON "
                        f"public.{table} TO teruisi_ai_seal_writer")
                try:
                    with self.database() as db:
                        db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                        with self.assertRaises(psycopg.Error):
                            self.read_gate(db, self.parent.id, attempt_id)
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute(f"REVOKE {permission} ({column}) ON "
                            f"public.{table} FROM teruisi_ai_seal_writer")

    def test_segment_mac_binds_actual_run_identity(self):
        attempt_id = self.attempt()
        segment = m.AiBusinessV4ValidationSegment.objects.filter(
            attempt_id=attempt_id).first()
        segment.run_id = "forged-v4-run"
        with self.assertRaises(AiError):
            validation._verified_segment(segment, *validation._key())
