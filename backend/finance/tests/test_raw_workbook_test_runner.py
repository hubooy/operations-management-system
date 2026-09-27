"""Only the exact isolated PG runner may flush immutable 0005/0006 tables."""
from django.db import connection
from django.test import TransactionTestCase, override_settings

from teruisi_backend.isolated_test_runner import (
    FINANCE_DIGEST_TABLES, FINANCE_TRUNCATE_GUARD,
    FINANCE_WORKBOOK_TABLES, FINANCE_WORKBOOK_TRUNCATE_GUARD)


@override_settings(DJANGO_ENVIRONMENT="test",
    DJANGO_PROCESS_ROLE="development")
class RawWorkbookRunnerCoexistenceTests(TransactionTestCase):
    def _assert_guards(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires exact isolated PostgreSQL runner")
        if (connection.settings_dict["HOST"] != "127.0.0.1"
                or connection.settings_dict["NAME"] !=
                "test_teruisi_ai_rehearsal"):
            self.fail("0006 runner guard probe outside isolated test DB")
        with connection.cursor() as cursor:
            for table, guard in (
                    *((name, FINANCE_TRUNCATE_GUARD)
                        for name in FINANCE_DIGEST_TABLES),
                    *((name, FINANCE_WORKBOOK_TRUNCATE_GUARD)
                        for name in FINANCE_WORKBOOK_TABLES)):
                cursor.execute("SELECT t.tgenabled,t.tgtype FROM "
                    "pg_catalog.pg_trigger t WHERE t.tgrelid=%s::regclass "
                    "AND t.tgname=%s", ["public." + table, guard])
                self.assertEqual(cursor.fetchone(), ("O", 34))

    def test_first_transaction_flush_boundary(self):
        self._assert_guards()

    def test_second_transaction_flush_restores_each_guard(self):
        self._assert_guards()
