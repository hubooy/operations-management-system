"""The protected cap and finance sidecars coexist without weakening either ACL."""

from importlib import import_module

from django import test as djtest
from django.db import connection


CAP = import_module(
    "ai_assistant.migrations.0074_business_market_v2_human_cap_approval")
FINANCE = import_module("finance.migrations.0005_raw_column_evidence_v2")


class HumanCapFinanceCoexistenceTests(djtest.TestCase):
    def test_both_catalogs_and_runtime_roles_remain_closed(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL with both migrations")
        with connection.cursor() as cursor:
            CAP.verify_catalog(cursor)
            FINANCE.verify_catalog(cursor)
            cursor.execute("SELECT app,name FROM django_migrations WHERE "
                "(app='ai_assistant' AND name=%s) OR "
                "(app='finance' AND name=%s) ORDER BY app",
                ["0074_business_market_v2_human_cap_approval",
                 "0005_raw_column_evidence_v2"])
            self.assertEqual(cursor.fetchall(), [
                ("ai_assistant", "0074_business_market_v2_human_cap_approval"),
                ("finance", "0005_raw_column_evidence_v2")])
            for role, table in (
                    ("teruisi_ai_writer", "finance_raw_column_evidence_cells"),
                    ("teruisi_finance_writer",
                     "protected_business_market_v2_human_cap_approvals")):
                cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                    [role, "public." + table,
                     "SELECT,INSERT,UPDATE,DELETE,TRUNCATE"])
                self.assertEqual(cursor.fetchone(), (False,))
