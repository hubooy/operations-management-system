"""Isolated PostgreSQL negatives for the closed 0043 consumption candidate."""
from importlib import import_module
import psycopg
from django.db import connection
from django.test import TransactionTestCase

from business_analysis.contracts import canonical, digest

from . import (business_v4_seal_hmac as seal_hmac,
    business_v4_seal_verify as verifier, models as m)
from .policy import AiError
from .test_business_v4_seal_ticket import BusinessV4SealTicketTests as fixture
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as old_gate


class BusinessV4SealConsumptionCandidateTests(TransactionTestCase):
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
    body = old_gate.body
    call_as_sealer = old_gate.call_as_sealer
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    def test_no_ticket_consume_result_and_direct_commit_remain_closed(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, claim_token, _ = self.claim(attempt_id, nonce)
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_v4_seal_result_unavailable"):
                db.execute("SELECT * FROM public.ai_v4_sealer_consumption_result("
                    "%s,%s,%s,%s,%s)",
                    [self.parent.id, attempt_id, digest("ticket-one"), nonce,
                     claim_token])
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_business_v4_seal_consumptions")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_v4_commit_seal(%s,%s,%s,%s,%s,%s,%s)",
                    [self.parent.id, attempt_id, 1, "{}", "0" * 64,
                     "0" * 64, "0" * 16])
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.exists())
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")
        self.assertIsNotNone(ticket_id)

    def test_even_authentic_mac_old_direct_commit_rolls_back_without_consumption(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        real_mac = seal_hmac.sign(canonical(body))["bodyMac"]
        with connection.cursor() as cursor:
            cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                "text,text,bigint,text,text,text,text) TO teruisi_ai_seal_writer")
        try:
            with self.assertRaisesRegex(psycopg.Error,
                    "ai_v4_seal_consumption_missing"):
                self.call_as_sealer(body, mac=real_mac)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                    "text,text,bigint,text,text,text,text) FROM teruisi_ai_seal_writer")
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(run=self.parent).exists())
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.exists())
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.pk).status,
            "collecting")

    def test_existing_seal_without_consumption_is_not_current_authority(self):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        real_mac = seal_hmac.sign(canonical(body))["bodyMac"]
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_seals "
                "DISABLE TRIGGER ai_v4_seal_consumption_required")
            cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                "text,text,bigint,text,text,text,text) TO teruisi_ai_seal_writer")
        try:
            self.call_as_sealer(body, mac=real_mac)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                    "text,text,bigint,text,text,text,text) FROM teruisi_ai_seal_writer")
                cursor.execute("ALTER TABLE public.ai_business_v4_seals "
                    "ENABLE TRIGGER ai_v4_seal_consumption_required")
        self.assertTrue(m.AiBusinessV4Seal.objects.filter(run=self.parent).exists())
        with self.assertRaisesRegex(AiError, "缺同事务消费回执"):
            verifier.verify_seal(self.parent.id, self.principal)
        migration = import_module(
            "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate")
        with connection.cursor() as cursor:
            cursor.execute("DROP FUNCTION public.ai_v4_verify_seal_consumption("
                "text,text,bigint,text)")
        try:
            with self.assertRaisesRegex(AiError, "消费门禁缺失"):
                verifier.verify_seal(self.parent.id, self.principal)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(migration.VERIFY_CONSUMPTION)
                cursor.execute("REVOKE ALL ON FUNCTION "
                    "public.ai_v4_verify_seal_consumption(text,text,bigint,text) "
                    "FROM PUBLIC")
                cursor.execute("GRANT EXECUTE ON FUNCTION "
                    "public.ai_v4_verify_seal_consumption(text,text,bigint,text) "
                    "TO teruisi_ai_writer")
        with connection.cursor() as cursor:
            cursor.execute("""CREATE OR REPLACE FUNCTION
                public.ai_v4_verify_seal_consumption(
                  selected_run text,selected_attempt text,
                  selected_version bigint,selected_body_digest text)
                RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
                SET search_path=pg_catalog,public AS $$
                BEGIN RETURN true; END $$""")
        try:
            with self.assertRaisesRegex(AiError, "消费门禁缺失"):
                verifier.verify_seal(self.parent.id, self.principal)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP FUNCTION public.ai_v4_verify_seal_consumption("
                    "text,text,bigint,text)")
                cursor.execute(migration.VERIFY_CONSUMPTION)
                cursor.execute("REVOKE ALL ON FUNCTION "
                    "public.ai_v4_verify_seal_consumption(text,text,bigint,text) "
                    "FROM PUBLIC")
                cursor.execute("GRANT EXECUTE ON FUNCTION "
                    "public.ai_v4_verify_seal_consumption(text,text,bigint,text) "
                    "TO teruisi_ai_writer")
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER ai_v4_seal_consumption_required "
                "ON public.ai_business_v4_seals")
        try:
            with self.assertRaisesRegex(AiError, "消费延期约束缺失"):
                verifier.verify_seal(self.parent.id, self.principal)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(migration.SEAL_TRIGGER)
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.exists())

    def test_accidental_direct_insert_grant_cannot_create_result(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        self.claim(attempt_id, nonce)
        with connection.cursor() as cursor:
            cursor.execute("GRANT INSERT ON public.ai_business_v4_seal_consumptions "
                "TO teruisi_ai_seal_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_v4_consumption_direct_write_denied"):
                    db.execute("INSERT INTO public.ai_business_v4_seal_consumptions "
                        "(ticket_id,run_id,attempt_id,request_digest,evidence_version,"
                        "body_digest,consumed_at) VALUES (%s,%s,%s,%s,%s,%s,now())",
                        [ticket_id,self.parent.id,attempt_id,digest("ticket-one"),
                         1,"0" * 64])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE INSERT ON public.ai_business_v4_seal_consumptions "
                    "FROM teruisi_ai_seal_writer")
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.exists())

    def test_old_ticket_cannot_consume_after_newer_attempt_even_with_owner_helper(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        self.claim(attempt_id, nonce)
        body = self.body(attempt_id)
        real_mac = seal_hmac.sign(canonical(body))["bodyMac"]
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_seals "
                "DISABLE TRIGGER ai_v4_seal_consumption_required")
            cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                "text,text,bigint,text,text,text,text) TO teruisi_ai_seal_writer")
        try:
            self.call_as_sealer(body, mac=real_mac)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE EXECUTE ON FUNCTION public.ai_v4_commit_seal("
                    "text,text,bigint,text,text,text,text) FROM teruisi_ai_seal_writer")
                cursor.execute("ALTER TABLE public.ai_business_v4_seals "
                    "ENABLE TRIGGER ai_v4_seal_consumption_required")
        seal = m.AiBusinessV4Seal.objects.get(run=self.parent)
        with connection.cursor() as cursor:
            for trigger in ("ai_write_fence", "ai_v4_state"):
                cursor.execute("ALTER TABLE public.ai_business_v4_validation_attempts "
                    "DISABLE TRIGGER " + trigger)
            cursor.execute("""INSERT INTO public.ai_business_v4_validation_attempts
                (id,run_id,run_version,plan_digest,directory_digest,
                 actor_email,actor_version,key_id,created_at)
                SELECT 'v4-newer-attempt-consumption-test',run_id,run_version,
                  plan_digest,directory_digest,actor_email,actor_version,key_id,
                  created_at+interval '1 second'
                FROM public.ai_business_v4_validation_attempts WHERE id=%s""",
                [attempt_id])
            for trigger in ("ai_v4_state", "ai_write_fence"):
                cursor.execute("ALTER TABLE public.ai_business_v4_validation_attempts "
                    "ENABLE TRIGGER " + trigger)
            cursor.execute("""CREATE FUNCTION public.ai_v4_test_insert_consumption(
                selected_ticket uuid,selected_run text,selected_attempt text,
                selected_request text,selected_version bigint,selected_digest text)
                RETURNS void LANGUAGE plpgsql SECURITY DEFINER
                SET search_path=pg_catalog,public AS $$
                BEGIN
                  INSERT INTO public.ai_business_v4_seal_consumptions
                    (ticket_id,run_id,attempt_id,request_digest,evidence_version,
                     body_digest,consumed_at)
                  VALUES (selected_ticket,selected_run,selected_attempt,
                    selected_request,selected_version,selected_digest,
                    clock_timestamp());
                END $$""")
            cursor.execute("REVOKE ALL ON FUNCTION public.ai_v4_test_insert_consumption("
                "uuid,text,text,text,bigint,text) FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_v4_test_insert_consumption("
                "uuid,text,text,text,bigint,text) TO teruisi_ai_seal_writer")
        try:
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
                with self.assertRaisesRegex(psycopg.Error,
                        "ai_v4_consumption_binding_invalid"):
                    db.execute("SELECT public.ai_v4_test_insert_consumption("
                        "%s,%s,%s,%s,%s,%s)",
                        [ticket_id,self.parent.id,attempt_id,digest("ticket-one"),
                         seal.evidence_version,seal.body_digest])
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP FUNCTION public.ai_v4_test_insert_consumption("
                    "uuid,text,text,text,bigint,text)")
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.exists())
