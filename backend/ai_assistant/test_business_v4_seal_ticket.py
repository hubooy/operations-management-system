"""Real-role PostgreSQL checks for the closed, single-claim v4 seal ticket."""
from importlib import import_module
import re

import psycopg
from django.db import connection, DatabaseError, transaction
from django.test import TransactionTestCase

from access_control.models import AppUser

from . import models as m
from .control_models import AiWriteAuthority
from .policy import digest
from .test_business_v4_seal_admission import BusinessV4SealAdmissionTests as admission_fixture
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as fixture


class BusinessV4SealTicketTests(TransactionTestCase):
    promotion_owner = fixture.promotion_owner
    rebuild_plan = fixture.rebuild_plan
    finance_owner = fixture.finance_owner
    owner = fixture.owner
    collect = fixture.collect
    complete_mixed = fixture.complete_mixed
    attempt = fixture.attempt
    database = fixture.database

    def setUp(self):
        admission_fixture.setUp(self)
        # The historical fixture creates disposable roles by hand; production
        # ProvisionRoles grants this post-role-creation EXECUTE instead.
        with connection.cursor() as cursor:
            cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text) TO teruisi_ai_writer")

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text) FROM teruisi_ai_writer")
        admission_fixture.tearDown(self)

    def _identity(self, attempt_id):
        parent = m.AiBusinessV4Run.objects.get(pk=self.parent.pk)
        attempt = m.AiBusinessV4ValidationAttempt.objects.get(pk=attempt_id)
        actor = AppUser.objects.get(email=self.principal.email)
        return parent, attempt, actor

    def _role_connection(self, role):
        authority = AiWriteAuthority.objects.get(id=1)
        db = self.database()
        db.execute("SET SESSION AUTHORIZATION " + role)
        db.execute("BEGIN")
        db.execute("SELECT set_config('teruisi.ai_epoch',%s,true),"
            "set_config('teruisi.ai_cutover',%s,true)",
            [str(authority.authority_epoch), authority.cutover_id])
        return db

    def issue(self, attempt_id, *, request="ticket-one"):
        parent, attempt, actor = self._identity(attempt_id)
        with self._role_connection("teruisi_ai_writer") as db:
            try:
                result = db.execute("SELECT ticket_id,nonce,expires_at FROM "
                    "public.ai_v4_issue_seal_ticket(%s,%s,%s,%s,%s,%s,%s)",
                    [parent.id, attempt.id, actor.email, actor.version,
                     parent.version, attempt.directory_digest,
                     digest(request)]).fetchone()
                db.execute("COMMIT")
                return result
            except Exception:
                db.execute("ROLLBACK")
                raise

    def claim(self, attempt_id, nonce, *, run_id=None, actor=None,
              actor_version=None):
        parent, attempt, current = self._identity(attempt_id)
        with self._role_connection("teruisi_ai_seal_writer") as db:
            try:
                result = db.execute("SELECT ticket_id,claim_token,lease_until "
                    "FROM public.ai_v4_claim_seal_ticket(%s,%s,%s,%s,%s)",
                    [run_id or parent.id, attempt.id, actor or current.email,
                     current.version if actor_version is None else actor_version,
                     nonce]).fetchone()
                db.execute("COMMIT")
                return result
            except Exception:
                db.execute("ROLLBACK")
                raise

    def test_issue_claim_once_and_no_direct_ticket_or_legacy_seal_paths(self):
        attempt_id = self.attempt()
        ticket_id, nonce, expiry = self.issue(attempt_id)
        self.assertRegex(nonce, r"^[0-9a-f]{64}$")
        self.assertIsNotNone(expiry)
        with connection.cursor() as cursor:
            cursor.execute("SELECT nonce_hash FROM public.ai_business_v4_seal_tickets "
                "WHERE id=%s", [ticket_id])
            self.assertNotEqual(cursor.fetchone()[0], nonce)
            cursor.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles "
                "WHERE rolname='teruisi_ai_seal_writer'")
            self.assertEqual(cursor.fetchone(), (False,))
            for signature in (
                "public.ai_v4_sealer_read_context(text,text,text,bigint)",
                "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
                "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
                "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
                "public.ai_v4_lock_source_revisions_for_admission()",
            ):
                cursor.execute("SELECT has_function_privilege("
                    "'teruisi_ai_seal_writer',%s,'EXECUTE')", [signature])
                self.assertEqual(cursor.fetchone(), (False,))
        claimed_id, claim_token, lease = self.claim(attempt_id, nonce)
        self.assertEqual(claimed_id, ticket_id)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", claim_token))
        self.assertGreater(lease, expiry)
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, nonce)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_business_v4_seal_tickets")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_business_v4_seal_claims")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_v4_sealer_read_context(%s,%s,%s,%s)",
                    [self.parent.id, attempt_id, self.principal.email, 1])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")

    def test_wrong_nonce_cross_run_replay_and_duplicate_request_refuse(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).first()
        self.assertIsNotNone(other)
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, "0" * 64)
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, nonce, run_id=other.id)
        with self.assertRaises(psycopg.Error):
            self.issue(attempt_id, request="ticket-one")
        self.assertEqual(self.claim(attempt_id, nonce)[0], ticket_id)
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, nonce)

    def test_accidental_runtime_truncate_grant_still_cannot_erase_ticket(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        self.claim(attempt_id, nonce)
        tables = ["public.ai_business_v4_seal_tickets",
                  "public.ai_business_v4_seal_claims"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.ai_business_v4_seal_consumptions')")
            if cursor.fetchone()[0] is not None:
                tables.append("public.ai_business_v4_seal_consumptions")
            cursor.execute("SELECT to_regclass('public.ai_business_v4_sealer_replay_progress')")
            if cursor.fetchone()[0] is not None:
                tables.append("public.ai_business_v4_sealer_replay_progress")
        for role in ("teruisi_ai_writer", "teruisi_ai_seal_writer"):
            with connection.cursor() as cursor:
                cursor.execute("GRANT TRUNCATE ON " + ",".join(tables) + " TO " + role)
            try:
                with self.database() as db:
                    db.execute("SET SESSION AUTHORIZATION " + role)
                    for table in ("ai_business_v4_seal_claims",
                                  "ai_business_v4_seal_tickets"):
                        with self.assertRaisesRegex(psycopg.Error,
                                "ai_v4_ticket_truncate_denied"):
                            db.execute("TRUNCATE public." + table + " CASCADE")
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("REVOKE TRUNCATE ON " + ",".join(tables) +
                        " FROM " + role)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.ai_business_v4_seal_tickets "
                "WHERE id=%s", [ticket_id])
            self.assertEqual(cursor.fetchone(), (1,))
            cursor.execute("SELECT count(*) FROM public.ai_business_v4_seal_claims "
                "WHERE ticket_id=%s", [ticket_id])
            self.assertEqual(cursor.fetchone(), (1,))

    def test_source_root_rejects_duplicate_ordinals_even_with_plausible_bounds(self):
        attempt_id = self.attempt()
        parent, _, _ = self._identity(attempt_id)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                    "DROP CONSTRAINT ai_v4_source_ord_uq")
                for trigger in ("ai_write_fence", "ai_v4_state"):
                    cursor.execute("ALTER TABLE public.ai_business_v4_sources "
                        "DISABLE TRIGGER " + trigger)
                cursor.execute("UPDATE public.ai_business_v4_sources SET ordinal=1 "
                    "WHERE run_id=%s AND source_key='promotion-current'", [parent.id])
                cursor.execute("""INSERT INTO public.ai_business_v4_sources
                    (id,run_id,source_key,ordinal,domain,temporal_role,query_json,
                     query_digest,source_identity_digest,source_revision_hint,
                     source_ref,source_revision,checkpoint_json,version,page_count,
                     stored_bytes,row_count,finished,created_at,updated_at)
                    SELECT 'v4-duplicate-ordinal-test',run_id,'promotion-previous',3,
                      domain,temporal_role,
                      jsonb_set(query_json::jsonb,'{window}',
                        '"previous"'::jsonb)::text,
                      encode(sha256(convert_to(jsonb_set(query_json::jsonb,
                        '{window}','"previous"'::jsonb)::text,'UTF8')),'hex'),
                      source_identity_digest,source_revision_hint,source_ref,
                      source_revision,checkpoint_json,version,page_count,
                      stored_bytes,row_count,finished,created_at,updated_at
                    FROM public.ai_business_v4_sources
                    WHERE run_id=%s AND source_key='promotion-current'""",
                    [parent.id])
            with self.assertRaisesRegex(DatabaseError,
                    "ai_v4_ticket_source_root_invalid"), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM public.ai_v4_seal_ticket_source_root(%s)",
                        [parent.id])
            transaction.set_rollback(True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_count FROM "
                "public.ai_v4_seal_ticket_source_root(%s)", [parent.id])
            self.assertEqual(cursor.fetchone(), (2,))

    def test_expired_ticket_revoked_actor_and_nonempty_inverse_refuse(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        migration = import_module(
            "ai_assistant.migrations.0041_business_v4_seal_ticket")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError, "issued or claimed"):
                migration.uninstall(None, editor)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                "DISABLE TRIGGER ai_v4_ticket_immutable")
            cursor.execute("UPDATE public.ai_business_v4_seal_tickets SET "
                "issued_at=statement_timestamp()-interval '61 seconds',"
                "expires_at=statement_timestamp()-interval '1 second' WHERE id=%s",
                [ticket_id])
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                "ENABLE TRIGGER ai_v4_ticket_immutable")
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, nonce)
        _, fresh, _ = self.issue(attempt_id, request="ticket-after-expiry")
        AppUser.objects.filter(email=self.principal.email).update(status="inactive")
        with self.assertRaises(psycopg.Error):
            self.claim(attempt_id, fresh)
