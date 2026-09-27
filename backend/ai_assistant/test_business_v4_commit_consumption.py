"""Targeted isolated PostgreSQL tests for the closed 0052 wrapper.

These require an ephemeral test database. They deliberately use a test-only
SESSION AUTHORIZATION switch because the real sealer remains NOLOGIN.
"""
from importlib import import_module

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase

from business_analysis.v4_final_commit_contract import commit_seal_request_digest
from business_analysis.v4_sealer_step_core import replay_one_claimed_segment

from . import business_v4_seal_hmac as seal_hmac
from . import business_v4_seal_verify as verifier
from . import business_v4_validation as validation
from . import models as m
from .policy import canonical, digest
from .test_business_v4_sealer_step_core import BusinessV4SealerStepCoreTests as fixture
from .test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests as old_gate
from .v4_commit_consumption_catalog import verify as verify_catalog


class BusinessV4CommitConsumptionTests(TransactionTestCase):
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
    claim = fixture.claim
    body = old_gate.body
    setUp = fixture.setUp
    tearDown = fixture.tearDown

    def test_frozen_wrapper_catalog_rejects_execute_acl_drift(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
        migration = import_module(
            "ai_assistant.migrations.0052_business_v4_commit_consumption")
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION " + migration.SIGNATURE +
                " FROM teruisi_ai_seal_writer")
            with self.assertRaisesRegex(ValueError, "function ACL drift"):
                verify_catalog(cursor)
            transaction.set_rollback(True)

    def _prepare(self, *, record=True):
        attempt_id = self.attempt()
        body = self.body(attempt_id)
        raw_body = canonical(body)
        signature = seal_hmac.sign(raw_body)
        parent, attempt, actor = self._identity(attempt_id)
        request_digest = commit_seal_request_digest(
            run_id=parent.id, attempt_id=attempt.id,
            actor_email=actor.email, actor_version=actor.version,
            parent_version=parent.version, plan_digest=parent.plan_digest,
            directory_digest=attempt.directory_digest,
            body_json=raw_body, body_mac=signature["bodyMac"],
            key_id=signature["keyId"], derived_key=seal_hmac._key()[0])
        with self._role_connection("teruisi_ai_writer") as db:
            ticket_id, nonce, _ = db.execute(
                "SELECT ticket_id,nonce,expires_at FROM "
                "public.ai_v4_issue_seal_ticket(%s,%s,%s,%s,%s,%s,%s)",
                [parent.id, attempt.id, actor.email, actor.version,
                 parent.version, attempt.directory_digest,
                 request_digest]).fetchone()
            db.execute("COMMIT")
        _, claim, _ = self.claim(attempt_id, nonce)
        if record:
            with self._role_connection("teruisi_ai_seal_writer") as db:
                for source in self.sources.values():
                    result = replay_one_claimed_segment(db, validation._key()[0],
                        run_id=parent.id, attempt_id=attempt_id,
                        source_id=source.id, segment_index=1,
                        actor_email=actor.email, actor_version=actor.version,
                        nonce=nonce, claim=claim, enabled=True)
                    self.assertEqual(result["status"], "recorded_candidate")
                db.execute("COMMIT")
        return (attempt_id, actor, ticket_id, nonce, claim,
                raw_body, signature, request_digest)

    def _commit(self, prepared, *, claim=None, request=None, body=None):
        attempt_id, actor, _, nonce, token, raw, signature, request_digest = prepared
        with self._role_connection("teruisi_ai_seal_writer") as db:
            try:
                result = db.execute(
                    "SELECT run_id,evidence_version,sealed_digest,consumed_at "
                    "FROM public.ai_v4_sealer_commit_with_consumption("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [self.parent.id, attempt_id, actor.email, actor.version,
                     nonce, token if claim is None else claim,
                     raw if body is None else body, digest(raw),
                     signature["bodyMac"], signature["keyId"],
                     request_digest if request is None else request]).fetchone()
                db.execute("COMMIT")
                return result
            except Exception:
                db.execute("ROLLBACK")
                raise

    def test_complete_candidates_commit_and_consume_atomically(self):
        prepared = self._prepare()
        result = self._commit(prepared)
        from json import loads
        self.assertEqual(result[:3], (self.parent.id,
            loads(prepared[5])["evidenceVersion"], digest(prepared[5])))
        seal = m.AiBusinessV4Seal.objects.get(run_id=self.parent.id)
        receipt = m.AiBusinessV4SealConsumption.objects.get(
            run_id=self.parent.id)
        self.assertEqual(receipt.ticket_id, prepared[2])
        self.assertEqual(receipt.body_digest, seal.body_digest)
        self.assertEqual(receipt.request_digest, prepared[7])
        self.assertEqual(m.AiBusinessV4Run.objects.get(pk=self.parent.id).status,
            "sealed")
        self.assertEqual(verifier.verify_seal(self.parent.id,
            self.principal)["sealedDigest"], digest(prepared[5]))
        with self._role_connection("teruisi_ai_seal_writer") as db:
            recovered = db.execute("SELECT * FROM "
                "public.ai_v4_sealer_consumption_result(%s,%s,%s,%s,%s)",
                [self.parent.id, prepared[0], prepared[7], prepared[3],
                 prepared[4]]).fetchone()
            db.execute("COMMIT")
        self.assertEqual(recovered, (self.parent.id, prepared[0],
            result[1], digest(prepared[5]), result[3]))
        with self.assertRaises(psycopg.Error):
            self._commit(prepared)

    def test_missing_replay_receipt_refuses_without_seal_or_consumption(self):
        prepared = self._prepare(record=False)
        with self.assertRaisesRegex(psycopg.Error,
                "ai_v4_commit_consumption_receipts_incomplete"):
            self._commit(prepared)
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(
            run_id=self.parent.id).exists())
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.filter(
            run_id=self.parent.id).exists())

    def test_request_and_claim_tamper_refuse(self):
        prepared = self._prepare()
        for kwargs in ({"claim": "0" * 64}, {"request": "0" * 64},
                       {"body": prepared[5] + " "}):
            with self.subTest(kwargs=kwargs), self.assertRaises(psycopg.Error):
                self._commit(prepared, **kwargs)
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(
            run_id=self.parent.id).exists())
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.filter(
            run_id=self.parent.id).exists())

    def test_expired_claim_refuses_even_when_all_receipts_exist(self):
        prepared = self._prepare()
        ticket_id = prepared[2]
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                "DISABLE TRIGGER ai_v4_ticket_immutable")
            cursor.execute("ALTER TABLE public.ai_business_v4_seal_claims "
                "DISABLE TRIGGER ai_v4_ticket_immutable")
            try:
                cursor.execute("UPDATE public.ai_business_v4_seal_tickets SET "
                    "issued_at=statement_timestamp()-interval '180 seconds',"
                    "expires_at=statement_timestamp()-interval '120 seconds' "
                    "WHERE id=%s", [ticket_id])
                cursor.execute("UPDATE public.ai_business_v4_seal_claims SET "
                    "claimed_at=statement_timestamp()-interval '150 seconds',"
                    "lease_until=statement_timestamp()-interval '1 second' "
                    "WHERE ticket_id=%s", [ticket_id])
            finally:
                cursor.execute("ALTER TABLE public.ai_business_v4_seal_claims "
                    "ENABLE TRIGGER ai_v4_ticket_immutable")
                cursor.execute("ALTER TABLE public.ai_business_v4_seal_tickets "
                    "ENABLE TRIGGER ai_v4_ticket_immutable")
        with self.assertRaises(psycopg.Error):
            self._commit(prepared)
        self.assertFalse(m.AiBusinessV4Seal.objects.filter(
            run_id=self.parent.id).exists())
        self.assertFalse(m.AiBusinessV4SealConsumption.objects.filter(
            run_id=self.parent.id).exists())

    def test_old_direct_commit_remains_revoked_and_inverse_fails_with_consumption(self):
        prepared = self._prepare()
        with self.database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_seal_writer")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM public.ai_v4_commit_seal("
                    "%s,%s,%s,%s,%s,%s,%s)",
                    [self.parent.id, prepared[0], 1, prepared[5],
                     digest(prepared[5]), prepared[6]["bodyMac"],
                     prepared[6]["keyId"]])
        self._commit(prepared)
        migration = import_module(
            "ai_assistant.migrations.0052_business_v4_commit_consumption")
        with connection.schema_editor() as editor:
            with self.assertRaisesRegex(RuntimeError,
                    "cannot remove an issued seal consumption"):
                migration.uninstall(None, editor)
