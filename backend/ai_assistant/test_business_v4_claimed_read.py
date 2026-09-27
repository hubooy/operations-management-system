"""Isolated PostgreSQL checks for claim-gated v4 reads with publication closed."""
import hashlib

import psycopg
from django.db import connection
from django.test import TransactionTestCase

from access_control.models import AppUser

from . import models as m
from .test_business_v4_seal_ticket import BusinessV4SealTicketTests as fixture


class BusinessV4ClaimedReadTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    attempt = fixture.attempt
    database = fixture.database
    _identity = fixture._identity
    _role_connection = fixture._role_connection
    issue = fixture.issue
    claim = fixture.claim
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    def _read(self, db, kind, attempt_id, nonce, token, *, run_id=None,
              actor=None, version=None, source_id=None, number=1):
        parent, _, current = self._identity(attempt_id)
        run = run_id or parent.id
        email = actor or current.email
        actor_version = current.version if version is None else version
        if kind == "context":
            signature = "public.ai_v4_sealer_ticket_context(%s,%s,%s,%s,%s,%s)"
            arguments = [run, attempt_id, email, actor_version, nonce, token]
        elif kind == "segment":
            signature = "public.ai_v4_sealer_ticket_segment(%s,%s,%s,%s,%s,%s,%s,%s)"
            arguments = [run, attempt_id, source_id, number, email, actor_version,
                nonce, token]
        else:
            signature = "public.ai_v4_sealer_ticket_page(%s,%s,%s,%s,%s,%s,%s,%s)"
            arguments = [run, attempt_id, source_id, number, email, actor_version,
                nonce, token]
        return db.execute("SELECT * FROM " + signature, arguments).fetchone()

    def test_valid_claim_reads_exact_context_segment_page_but_cannot_seal(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        claimed_id, token, _ = self.claim(attempt_id, nonce)
        self.assertEqual(ticket_id, claimed_id)
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).order_by(
            "ordinal").first()
        with self._role_connection("teruisi_ai_seal_writer") as db:
            context = self._read(db, "context", attempt_id, nonce, token)
            segment = self._read(db, "segment", attempt_id, nonce, token,
                source_id=source.id)
            page = self._read(db, "page", attempt_id, nonce, token,
                source_id=source.id)
            self.assertEqual((context[0], context[1], context[-1]),
                (self.parent.id, attempt_id, True))
            self.assertEqual((segment[0], segment[-1]), (page[5], True))
            self.assertEqual(page[-1], True)
            self.assertEqual(hashlib.sha256(page[9].encode("utf-8")).hexdigest(),
                page[10])
            for old in (
                "public.ai_v4_sealer_read_context(text,text,text,bigint)",
                "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
                "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
                "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
            ):
                self.assertFalse(db.execute("SELECT has_function_privilege("
                    "'teruisi_ai_seal_writer',%s,'EXECUTE')", [old]).fetchone()[0])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run=self.parent).exists())

    def test_wrong_claim_cross_run_source_actor_and_roles_are_rejected(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).first()
        self.assertIsNotNone(other)
        for options in (
            {"nonce": "0" * 64},
            {"token": "0" * 64},
            {"run_id": other.id},
            {"actor": "forged@example.invalid"},
            {"version": AppUser.objects.get(email=self.principal.email).version+1},
            {"source_id": "unrelated-source"},
            {"number": source.page_count+1},
        ):
            with self._role_connection("teruisi_ai_seal_writer") as db:
                with self.subTest(options=options), self.assertRaises(psycopg.Error):
                    self._read(db, "page", attempt_id,
                        options.get("nonce", nonce), options.get("token", token),
                        run_id=options.get("run_id"), actor=options.get("actor"),
                        version=options.get("version"),
                        source_id=options.get("source_id", source.id),
                        number=options.get("number", 1))
                db.execute("ROLLBACK")
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION " + role)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self._read(db, "context", attempt_id, nonce, token)
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(psycopg.Error):
                self._read(db, "context", attempt_id, nonce, token)

    def test_changed_authority_or_disabled_source_trigger_denies_claimed_read(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            db.execute("SELECT set_config('teruisi.ai_epoch','wrong-epoch',true)")
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_v4_admission_authority_mismatch"):
                self._read(db, "context", attempt_id, nonce, token)
            db.execute("ROLLBACK")
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.finance_lines "
                "DISABLE TRIGGER finance_line_revision_required")
        try:
            with self._role_connection("teruisi_ai_seal_writer") as db:
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_v4_admission_source_triggers_invalid"):
                    self._read(db, "context", attempt_id, nonce, token)
                db.execute("ROLLBACK")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.finance_lines "
                    "ENABLE TRIGGER finance_line_revision_required")

    def test_expired_claim_is_not_renewable_or_resumable(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                "DISABLE TRIGGER ai_v4_ticket_immutable")
            cursor.execute("UPDATE public.ai_business_v4_seal_tickets SET "
                "issued_at=statement_timestamp()-interval '200 seconds',"
                "expires_at=statement_timestamp()-interval '140 seconds' "
                "WHERE id=%s", [ticket_id])
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                "ENABLE TRIGGER ai_v4_ticket_immutable")
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_claims "
                "DISABLE TRIGGER ai_v4_ticket_immutable")
            cursor.execute("UPDATE public.ai_business_v4_seal_claims SET "
                "claimed_at=statement_timestamp()-interval '181 seconds',"
                "lease_until=statement_timestamp()-interval '1 second' "
                "WHERE ticket_id=%s", [ticket_id])
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_claims "
                "ENABLE TRIGGER ai_v4_ticket_immutable")
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(psycopg.Error):
                self._read(db, "context", attempt_id, nonce, token)
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, nonce)
