"""PostgreSQL checks for the versioned v4 prior-claim column fix."""
from importlib import import_module
from types import SimpleNamespace

from django.db import connection, transaction
from django.test import TransactionTestCase

from . import test_business_v4_sealer_replay_progress as fixture


migration = import_module(
    "ai_assistant.migrations.0051_business_v4_prior_claim_column")
class BusinessV4PriorClaimColumnMigrationTests(TransactionTestCase):
    def test_empty_reverse_and_reapply_preserve_writer_identity_and_acl(self):
        editor = SimpleNamespace(connection=connection)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT oid,prosrc,proacl FROM pg_catalog.pg_proc "
                "WHERE oid=to_regprocedure(%s)", [migration.base.WRITE])
            oid, body, acl = cursor.fetchone()
            self.assertEqual(body, migration.RECORD.split("$$")[1])
            migration._check_predecessor(cursor, migration.RECORD)
            migration.uninstall(None, editor)
            cursor.execute("SELECT oid,prosrc,proacl FROM pg_catalog.pg_proc "
                "WHERE oid=%s", [oid])
            self.assertEqual(cursor.fetchone(),
                (oid, migration.OLD_RECORD.split("$$")[1], acl))
            migration.install(None, editor)
            cursor.execute("SELECT oid,prosrc,proacl FROM pg_catalog.pg_proc "
                "WHERE oid=%s", [oid])
            self.assertEqual(cursor.fetchone(), (oid, body, acl))

    def test_frozen_writer_guard_rejects_body_drift(self):
        editor = SimpleNamespace(connection=connection)
        with transaction.atomic(), connection.cursor() as cursor:
            try:
                cursor.execute(migration.OLD_RECORD.replace("CREATE FUNCTION",
                    "CREATE OR REPLACE FUNCTION", 1).replace(
                    "WHERE ticket_id=prior.ticket_id;",
                    "WHERE ticket_id=prior.ticket_id /* drift */;", 1))
                with self.assertRaisesRegex(RuntimeError, "frozen replay writer"):
                    migration.install(None, editor)
            finally:
                transaction.set_rollback(True)


class BusinessV4PriorClaimReceiptGuardTests(TransactionTestCase):
    promotion_owner = fixture.BusinessV4SealerReplayProgressTests.promotion_owner
    rebuild_plan = fixture.BusinessV4SealerReplayProgressTests.rebuild_plan
    finance_owner = fixture.BusinessV4SealerReplayProgressTests.finance_owner
    owner = fixture.BusinessV4SealerReplayProgressTests.owner
    collect = fixture.BusinessV4SealerReplayProgressTests.collect
    complete_mixed = fixture.BusinessV4SealerReplayProgressTests.complete_mixed
    attempt = fixture.BusinessV4SealerReplayProgressTests.attempt
    database = fixture.BusinessV4SealerReplayProgressTests.database
    _identity = fixture.BusinessV4SealerReplayProgressTests._identity
    _role_connection = fixture.BusinessV4SealerReplayProgressTests._role_connection
    issue = fixture.BusinessV4SealerReplayProgressTests.issue
    claim = fixture.BusinessV4SealerReplayProgressTests.claim
    _candidate = fixture.BusinessV4SealerReplayProgressTests._candidate
    _record = fixture.BusinessV4SealerReplayProgressTests._record
    setUp = fixture.BusinessV4SealerReplayProgressTests.setUp
    tearDown = fixture.BusinessV4SealerReplayProgressTests.tearDown

    def test_reversal_refuses_even_first_segment_receipt(self):
        attempt_id = self.attempt()
        ticket_id, nonce, _ = self.issue(attempt_id)
        _, token, _ = self.claim(attempt_id, nonce)
        source = self.sources["promotion-current"]
        source.refresh_from_db()
        candidate = self._candidate(attempt_id, ticket_id, source)
        self._record(attempt_id, source.id, candidate, nonce, token)
        with self.assertRaisesRegex(RuntimeError, "with replay receipts"):
            migration.uninstall(None, SimpleNamespace(connection=connection))
        with connection.cursor() as cursor:
            migration._check_predecessor(cursor, migration.RECORD)
            cursor.execute("SELECT count(*) FROM " + migration.base.TABLE)
            self.assertEqual(cursor.fetchone(), (1,))
