"""Actual-role PostgreSQL negatives for selected-run v4 sealer page reads."""
import hashlib
from types import SimpleNamespace

import psycopg
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from access_control.models import AppUser

from . import business_v4_validation as validation, models as m
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as gate


class BusinessV4SealerNarrowStreamTests(TransactionTestCase):
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
            cursor.execute("SELECT to_regprocedure('public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text)')")
            if cursor.fetchone()[0] is not None:
                self.skipTest("0040无票据窄流已由0041关闭；历史行为由升级演练验收")
        gate.setUp(self)

    def identity(self):
        return self.principal.email, AppUser.objects.get(
            email=self.principal.email).version

    def context(self, db, attempt_id, *, run_id=None, actor=None, version=None):
        email, current_version = self.identity()
        return db.execute("SELECT * FROM public.ai_v4_sealer_read_context("
            "%s,%s,%s,%s)", [run_id or self.parent.id, attempt_id,
                actor or email, current_version if version is None else version]).fetchone()

    def segment(self, db, attempt_id, source, index=1):
        email, version = self.identity()
        return db.execute("SELECT * FROM public.ai_v4_sealer_read_segment("
            "%s,%s,%s,%s,%s,%s)", [self.parent.id, attempt_id,
                source.id, index, email, version]).fetchone()

    def page(self, db, attempt_id, source, sequence=1, *, run_id=None):
        email, version = self.identity()
        return db.execute("SELECT * FROM public.ai_v4_sealer_read_page("
            "%s,%s,%s,%s,%s,%s)", [run_id or self.parent.id, attempt_id,
                source.id, sequence, email, version]).fetchone()

    def test_true_sealer_reads_one_bound_page_and_segment_without_direct_tables(self):
        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).order_by(
            "ordinal").first()
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            context = self.context(db, attempt_id)
            self.assertEqual((context[0], context[1], context[-1]),
                (self.parent.id, attempt_id, False))
            segment = self.segment(db, attempt_id, source)
            page = self.page(db, attempt_id, source)
            self.assertEqual((segment[0], segment[-1]), (page[5], False))
            self.assertEqual((page[0], page[1], page[2], page[3]),
                (source.source_key, source.domain, source.version,
                 source.source_ref))
            self.assertEqual(hashlib.sha256(page[9].encode("utf-8")).hexdigest(),
                page[10])
            self.assertEqual(len(page[9].encode("utf-8")), page[11])
            self.assertEqual(page[-1], False)
            record = SimpleNamespace(id=segment[0], attempt_id=attempt_id,
                run_id=self.parent.id, source_id=source.id,
                segment_index=1, start_sequence=segment[4],
                end_sequence=segment[5], source_version=segment[1],
                source_ref=segment[2], source_revision=segment[3],
                previous_segment_digest=segment[6], progress_json=segment[7],
                progress_digest=segment[8], proof_digest=segment[9],
                proof_mac=segment[10],
                attempt=SimpleNamespace(key_id=context[7]))
            self.assertEqual(validation._verified_segment(record,
                *validation._key())["pageCount"], segment[5])
            for table in ("ai_business_v4_chunks", "ai_business_v4_tool_receipts",
                    "ai_business_v4_validation_attempts",
                    "ai_business_v4_validation_segments", "ai_business_v4_seals",
                    "ai_tool_audit_logs", "access_control_users", "finance_lines"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute("SELECT * FROM public." + table + " LIMIT 1")

    def test_cross_run_source_attempt_role_and_revocation_are_rejected(self):
        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).first()
        self.assertIsNotNone(other)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.Error):
                self.context(db, attempt_id, run_id=other.id)
            with self.assertRaises(psycopg.Error):
                self.page(db, attempt_id, source, run_id=other.id)
            with self.assertRaises(psycopg.Error):
                self.page(db, attempt_id, source, sequence=source.page_count+1)
            with self.assertRaises(psycopg.Error):
                self.context(db, attempt_id, version=self.identity()[1]+1)
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.Error):
                self.page(db, attempt_id, source)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION " + role)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self.page(db, attempt_id, source)

    def test_physical_select_drift_and_modified_raw_page_fail_closed(self):
        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT ON public.ai_business_v4_chunks "
                "TO teruisi_ai_seal_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                with self.assertRaises(psycopg.Error):
                    self.page(db, attempt_id, source)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE SELECT ON public.ai_business_v4_chunks "
                    "FROM teruisi_ai_seal_writer")
        chunk = m.AiBusinessV4Chunk.objects.filter(run=self.parent,
            source=source, sequence=1).get()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_immutable_v4")
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "DISABLE TRIGGER ai_v4_state")
                cursor.execute("UPDATE public.ai_business_v4_chunks SET "
                    "payload_json=payload_json||' ' WHERE id=%s", [chunk.id])
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "ENABLE TRIGGER ai_v4_state")
                cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                    "ENABLE TRIGGER ai_immutable_v4")
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.Error):
                self.page(db, attempt_id, source)

    def test_receipt_surface_and_audit_time_chain_are_required(self):
        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        receipt = m.AiBusinessV4ToolReceipt.objects.get(run=self.parent,
            source=source, sequence=1)
        for column, value in (("surface", "other_surface"),
                ("created_at", "2000-01-01 00:00:00+00")):
            with self.subTest(column=column):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                            "DISABLE TRIGGER ai_immutable_v4")
                        cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                            "DISABLE TRIGGER ai_v4_state")
                        cursor.execute("UPDATE public.ai_business_v4_tool_receipts "
                            f"SET {column}=%s WHERE chunk_id=%s",
                            [value, receipt.chunk_id])
                        cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                            "ENABLE TRIGGER ai_v4_state")
                        cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                            "ENABLE TRIGGER ai_immutable_v4")
                with self.database() as db:
                    db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                    with self.assertRaises(psycopg.Error):
                        self.page(db, attempt_id, source)
                # Restore the prior high-privilege fixture mutation before
                # testing the next independent page-chain field.
                if column == "surface":
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                                "DISABLE TRIGGER ai_immutable_v4")
                            cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                                "DISABLE TRIGGER ai_v4_state")
                            cursor.execute("UPDATE public.ai_business_v4_tool_receipts "
                                "SET surface='business_collection' WHERE chunk_id=%s",
                                [receipt.chunk_id])
                            cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                                "ENABLE TRIGGER ai_v4_state")
                            cursor.execute("ALTER TABLE public.ai_business_v4_tool_receipts "
                                "ENABLE TRIGGER ai_immutable_v4")

    def test_removed_unique_constraint_cannot_make_duplicate_page_selected(self):
        class RollbackFixture(Exception):
            pass

        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        chunk = m.AiBusinessV4Chunk.objects.get(run=self.parent,
            source=source, sequence=1)
        actor_version = self.identity()[1]
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                        "DROP CONSTRAINT ai_v4_chunk_sequence_uq")
                    cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                        "DISABLE TRIGGER ai_v4_state")
                    cursor.execute("ALTER TABLE public.ai_business_v4_chunks "
                        "DISABLE TRIGGER ai_write_fence")
                    cursor.execute("INSERT INTO public.ai_business_v4_chunks "
                        "(id,run_id,source_id,sequence,payload_json,payload_digest,"
                        "source_ref,source_revision,row_count,created_at) "
                        "SELECT 'v4-duplicate-page-test',run_id,source_id,sequence,"
                        "payload_json,payload_digest,source_ref,source_revision,"
                        "row_count,created_at FROM public.ai_business_v4_chunks "
                        "WHERE id=%s", [chunk.id])
                    cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                try:
                    with self.assertRaises(DatabaseError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT * FROM public.ai_v4_sealer_read_page("
                                    "%s,%s,%s,%s,%s,%s)", [self.parent.id,
                                    attempt_id, source.id, 1, self.principal.email,
                                    actor_version])
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute("SET SESSION AUTHORIZATION DEFAULT")
                raise RollbackFixture()
        except RollbackFixture:
            pass
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            self.assertIsNotNone(self.page(db, attempt_id, source))

    def test_removed_segment_unique_constraint_cannot_select_duplicate_proof(self):
        class RollbackFixture(Exception):
            pass

        attempt_id = self.attempt()
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        segment = m.AiBusinessV4ValidationSegment.objects.get(
            attempt_id=attempt_id, source=source, segment_index=1)
        actor_version = self.identity()[1]
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("ALTER TABLE public.ai_business_v4_validation_segments "
                        "DROP CONSTRAINT ai_v4_segment_seq_uq")
                    cursor.execute("ALTER TABLE public.ai_business_v4_validation_segments "
                        "DISABLE TRIGGER ai_v4_state")
                    cursor.execute("ALTER TABLE public.ai_business_v4_validation_segments "
                        "DISABLE TRIGGER ai_write_fence")
                    cursor.execute("INSERT INTO public.ai_business_v4_validation_segments "
                        "(id,attempt_id,run_id,source_id,segment_index,"
                        "start_sequence,end_sequence,source_version,source_ref,"
                        "source_revision,previous_segment_digest,progress_json,"
                        "progress_digest,proof_digest,proof_mac,created_at) "
                        "SELECT 'v4-duplicate-segment-test',attempt_id,run_id,"
                        "source_id,segment_index,start_sequence,end_sequence,"
                        "source_version,source_ref,source_revision,"
                        "previous_segment_digest,progress_json,progress_digest,"
                        "proof_digest,proof_mac,created_at FROM "
                        "public.ai_business_v4_validation_segments WHERE id=%s",
                        [segment.id])
                    cursor.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                try:
                    with self.assertRaises(DatabaseError):
                        with transaction.atomic():
                            with connection.cursor() as cursor:
                                cursor.execute("SELECT * FROM public.ai_v4_sealer_read_segment("
                                    "%s,%s,%s,%s,%s,%s)", [self.parent.id,
                                    attempt_id, source.id, 1, self.principal.email,
                                    actor_version])
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute("SET SESSION AUTHORIZATION DEFAULT")
                raise RollbackFixture()
        except RollbackFixture:
            pass
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            self.assertIsNotNone(self.segment(db, attempt_id, source))
