"""PostgreSQL checks for the versioned claim-bound replay reader."""
from importlib import import_module
from types import SimpleNamespace

import psycopg
from django.db import connection, transaction
from django.test import TransactionTestCase

from .policy import canonical
from . import test_business_v4_sealer_replay_progress as fixture
from . import test_business_v4_finance_replay_progress as finance_fixture


migration = import_module(
    "ai_assistant.migrations.0050_business_v4_replay_read_cast")
reader_fixture = fixture.BusinessV4SealerReplayProgressTests


class BusinessV4ReplayReadCastTests(TransactionTestCase):
    promotion_owner = reader_fixture.promotion_owner
    rebuild_plan = reader_fixture.rebuild_plan
    finance_owner = reader_fixture.finance_owner
    owner = reader_fixture.owner
    collect = reader_fixture.collect
    complete_mixed = reader_fixture.complete_mixed
    attempt = reader_fixture.attempt
    database = reader_fixture.database
    _identity = reader_fixture._identity
    _role_connection = reader_fixture._role_connection
    issue = reader_fixture.issue
    claim = reader_fixture.claim
    _candidate = reader_fixture._candidate
    _finance_candidate = finance_fixture.BusinessV4FinanceReplayProgressTests._finance_candidate
    _record = reader_fixture._record
    setUp = reader_fixture.setUp
    tearDown = reader_fixture.tearDown

    def test_stored_candidate_reads_as_text_only_for_matching_claim(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        source.refresh_from_db()
        candidate = self._candidate(attempt_id, ticket_id, source)
        recorded = self._record(attempt_id, source.id, candidate, nonce, token)
        finance_source, finance_candidate = self._finance_candidate(
            attempt_id, ticket_id)
        finance_recorded = self._record(attempt_id, finance_source.id,
            finance_candidate, nonce, token)
        parent, _, actor = self._identity(attempt_id)
        args = [parent.id, attempt_id, source.id, 1, actor.email,
                actor.version, nonce, token]
        statement = "SELECT * FROM public.ai_v4_sealer_replay_progress(" + \
            ",".join(["%s"] * 8) + ")"
        with self._role_connection("teruisi_ai_seal_writer") as db:
            result = db.execute(statement, args)
            self.assertEqual(result.description[1].type_code, 25)  # pg_catalog.text
            row = result.fetchone()
            self.assertEqual(row, (canonical(candidate), recorded[0], recorded[1]))
            self.assertIsInstance(row[1], str)
            finance_row = db.execute(statement,
                [*args[:2], finance_source.id, *args[3:]]).fetchone()
            self.assertEqual(finance_row, (canonical(finance_candidate),
                finance_recorded[0], finance_recorded[1]))
            self.assertIsNone(db.execute(statement,
                [*args[:2], "other-source", *args[3:]]).fetchone())
            with self.assertRaises(psycopg.Error):
                db.execute(statement, [*args[:-1], "0" * 64])
            db.execute("ROLLBACK")
        with connection.cursor() as cursor:
            migration._check_predecessor(cursor, migration.READ)
            cursor.execute("SELECT count(*) FROM " + migration.previous.TABLE)
            self.assertEqual(cursor.fetchone(), (2,))
        with self.assertRaisesRegex(RuntimeError, "with receipts"):
            migration.uninstall(None, SimpleNamespace(connection=connection))

    def test_empty_reverse_and_frozen_body_guard(self):
        editor = SimpleNamespace(connection=connection)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)::oid,prosrc FROM "
                "pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
                [migration.previous.READ, migration.previous.READ])
            oid, body = cursor.fetchone()
            self.assertEqual(body, migration.READ.split("$$")[1])
            migration.uninstall(None, editor)
            cursor.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE oid=%s",
                [oid])
            self.assertEqual(cursor.fetchone(),
                (migration.OLD_READ.split("$$")[1],))
            migration.install(None, editor)
            cursor.execute("SELECT oid,prosrc FROM pg_catalog.pg_proc WHERE oid=%s",
                [oid])
            self.assertEqual(cursor.fetchone(),
                (oid, migration.READ.split("$$")[1]))
            cursor.execute(migration.OLD_READ.replace("CREATE FUNCTION",
                "CREATE OR REPLACE FUNCTION", 1).replace(
                    "p.candidate_digest,p.recorded_at",
                    "p.candidate_digest /* drift */,p.recorded_at"))
            with self.assertRaisesRegex(RuntimeError, "frozen replay reader"):
                migration.install(None, editor)
            transaction.set_rollback(True)
