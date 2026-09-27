"""Actual-role PostgreSQL checks for the claim-bound single-source bridge."""
import hashlib
import json

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase

from access_control.models import AppUser

from . import models as m
from .v4_sealer_source_catalog import verify as verify_catalog
from . import test_business_v4_seal_ticket as ticket_fixture


class BusinessV4SealerSourceBridgeTests(TransactionTestCase):
    promotion_owner = ticket_fixture.BusinessV4SealTicketTests.promotion_owner
    rebuild_plan = ticket_fixture.BusinessV4SealTicketTests.rebuild_plan
    finance_owner = ticket_fixture.BusinessV4SealTicketTests.finance_owner
    owner = ticket_fixture.BusinessV4SealTicketTests.owner
    collect = ticket_fixture.BusinessV4SealTicketTests.collect
    complete_mixed = ticket_fixture.BusinessV4SealTicketTests.complete_mixed
    attempt = ticket_fixture.BusinessV4SealTicketTests.attempt
    database = ticket_fixture.BusinessV4SealTicketTests.database
    _identity = ticket_fixture.BusinessV4SealTicketTests._identity
    _role_connection = ticket_fixture.BusinessV4SealTicketTests._role_connection
    issue = ticket_fixture.BusinessV4SealTicketTests.issue
    claim = ticket_fixture.BusinessV4SealTicketTests.claim
    setUp = ticket_fixture.BusinessV4SealTicketTests.setUp
    tearDown = ticket_fixture.BusinessV4SealTicketTests.tearDown

    def test_frozen_source_bridge_catalog_and_acl(self):
        with connection.cursor() as cursor:
            verify_catalog(cursor)
            verify_catalog(cursor, RuntimeError)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("REVOKE EXECUTE ON FUNCTION public.ai_v4_sealer_ticket_source("
                "text,text,text,text,bigint,text,text) FROM teruisi_ai_seal_writer")
            with self.assertRaisesRegex(ValueError, "function ACL drift"):
                verify_catalog(cursor)
            transaction.set_rollback(True)

    def read_source(self, db, attempt_id, source_id, nonce, token, *,
                    run_id=None, actor=None, version=None):
        parent, _, current = self._identity(attempt_id)
        return db.execute("SELECT * FROM public.ai_v4_sealer_ticket_source("
            "%s,%s,%s,%s,%s,%s,%s)", [run_id or parent.id, attempt_id,
            source_id, actor or current.email,
            current.version if version is None else version,
            nonce, token]).fetchall()

    def test_exact_source_identity_and_completion_are_claim_bound(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        sources = list(m.AiBusinessV4Source.objects.filter(run=self.parent)
            .order_by("ordinal"))
        with self._role_connection("teruisi_ai_seal_writer") as db:
            for source in sources:
                with self.subTest(source=source.id):
                    rows = self.read_source(db, attempt_id, source.id,
                        nonce, token)
                    self.assertEqual(len(rows), 1)
                    row = rows[0]
                    self.assertEqual(row[:5], (source.id, source.source_key,
                        source.ordinal, source.domain, source.temporal_role))
                    self.assertEqual(row[5:9], (source.query_json,
                        source.query_digest, source.source_identity_digest,
                        source.source_revision_hint))
                    self.assertEqual(row[9:15], (source.version,
                        source.source_ref, source.source_revision,
                        source.page_count, source.row_count,
                        source.stored_bytes))
                    self.assertEqual(row[17], hashlib.sha256(
                        source.checkpoint_json.encode("utf-8")).hexdigest())
                    self.assertEqual(row[18], json.loads(
                        source.checkpoint_json)["lastChunkDigest"])
                    self.assertTrue(row[-1])
                    if source.domain == "finance":
                        self.assertIsNone(row[19])
                        self.assertRegex(row[20], r"^[0-9a-f]{64}$")
                    else:
                        self.assertEqual(json.loads(row[19]), json.loads(
                            source.checkpoint_json)["metadata"])
                        self.assertIsNone(row[20])
            self.assertEqual({self.read_source(db, attempt_id, source.id,
                nonce, token)[0][15] for source in sources},
                {self.read_source(db, attempt_id, sources[0].id,
                    nonce, token)[0][15]})

    def test_wrong_run_source_claim_actor_and_revocation_fail_closed(self):
        attempt_id = self.attempt()
        _, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = m.AiBusinessV4Source.objects.filter(run=self.parent).first()
        other = m.AiBusinessV4Run.objects.exclude(pk=self.parent.pk).first()
        self.assertIsNotNone(other)
        other_source = m.AiBusinessV4Source.objects.filter(run=other).first()
        self.assertIsNotNone(other_source)
        options = (
            {"run_id": other.id}, {"source_id": other_source.id},
            {"source_id": "missing-source"},
            {"nonce": "0" * 64}, {"token": "0" * 64},
            {"actor": "forged@example.invalid"},
            {"version": AppUser.objects.get(
                email=self.principal.email).version + 1},
        )
        for option in options:
            with self.subTest(option=option):
                with self._role_connection("teruisi_ai_seal_writer") as db:
                    with self.assertRaises(psycopg.Error):
                        self.read_source(db, attempt_id,
                            option.get("source_id", source.id),
                            option.get("nonce", nonce),
                            option.get("token", token),
                            run_id=option.get("run_id"),
                            actor=option.get("actor"),
                            version=option.get("version"))
                    db.execute("ROLLBACK")
        AppUser.objects.filter(email=self.principal.email).update(
            status="disabled")
        with self._role_connection("teruisi_ai_seal_writer") as db:
            with self.assertRaises(psycopg.Error):
                self.read_source(db, attempt_id, source.id, nonce, token)

    def test_only_sealer_can_execute_without_new_table_privileges(self):
        signature = "public.ai_v4_sealer_ticket_source("
        signature += "text,text,text,text,bigint,text,text)"
        with connection.cursor() as cursor:
            for role, allowed in (("teruisi_ai_seal_writer", True),
                    ("teruisi_ai_reader", False),
                    ("teruisi_ai_writer", False)):
                cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                    [role, signature])
                self.assertEqual(cursor.fetchone(), (allowed,))
            cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p, "
                "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid="
                "to_regprocedure(%s) AND acl.grantee=0 AND "
                "acl.privilege_type='EXECUTE')", [signature])
            self.assertEqual(cursor.fetchone(), (False,))
            for table in ("ai_business_v4_seal_tickets",
                    "ai_business_v4_validation_attempts"):
                cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                    ["teruisi_ai_seal_writer", "public." + table])
                self.assertEqual(cursor.fetchone(), (False,))
            for table in ("ai_business_v4_chunks", "finance_lines"):
                cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                    ["teruisi_ai_seal_writer", "public." + table])
                self.assertEqual(cursor.fetchone(), (False,))
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            with self.database() as db:
                db.execute("SET SESSION AUTHORIZATION " + role)
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute("SELECT * FROM " + signature.replace(
                        "text,text,text,text,bigint,text,text", "%s,%s,%s,%s,%s,%s,%s"),
                        ["run", "attempt", "source", "actor", 1,
                         "0" * 64, "0" * 64])
