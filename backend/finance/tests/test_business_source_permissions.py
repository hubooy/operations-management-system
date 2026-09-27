"""Real PostgreSQL minimal-role probes; run in the isolated rehearsal only."""
from uuid import uuid4

from django.db import connection, transaction, DatabaseError
from django.test import TestCase
from finance.business_source_permissions import grant_actor_read, SOURCE_TABLES
from teruisi_backend.health import _validate_finance_source_reader_permissions, ReadinessError


class FinanceSourcePermissionsTests(TestCase):
    def setUp(self):
        if connection.vendor != "postgresql": self.skipTest("requires real PostgreSQL grants")
        self.role = "fin_source_perm_" + uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{self.role}" NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{self.role}"')
            for table in SOURCE_TABLES:
                cursor.execute(f'GRANT SELECT ON "{table}" TO "{self.role}"')
            grant_actor_read(cursor, self.role)

    def reader(self):
        with connection.cursor() as cursor: cursor.execute(f'SET LOCAL ROLE "{self.role}"')
        self.addCleanup(self.reset)

    def reset(self):
        with connection.cursor() as cursor: cursor.execute("RESET ROLE")

    def ready(self):
        with connection.cursor() as cursor: _validate_finance_source_reader_permissions(cursor)

    def test_minimal_five_columns_and_facts_readable_writes_and_private_columns_denied(self):
        self.reader()
        self.ready()
        with connection.cursor() as cursor:
            cursor.execute("SELECT email,role,status,scope,version FROM access_control_users LIMIT 1")
            for table in SOURCE_TABLES: cursor.execute(f'SELECT * FROM "{table}" LIMIT 1')
        for statement in ("SELECT display_name FROM access_control_users LIMIT 1",
                          "SELECT created_at FROM access_control_users LIMIT 1",
                          "UPDATE access_control_users SET version=version",
                          "DELETE FROM access_control_users WHERE false",
                          "UPDATE finance_lines SET amount_cents=amount_cents",
                          "DELETE FROM finance_months WHERE false"):
            with self.subTest(statement=statement), self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(statement)

    def test_revoked_version_column_fails_real_health_permission_check(self):
        with connection.cursor() as cursor:
            cursor.execute(f'REVOKE SELECT (version) ON access_control_users FROM "{self.role}"')
        self.reader()
        with self.assertRaisesMessage(ReadinessError, "finance_source_identity_privilege_missing"): self.ready()

    def test_extra_display_name_read_is_rejected(self):
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT SELECT (display_name) ON access_control_users TO "{self.role}"')
        self.reader()
        with self.assertRaisesMessage(ReadinessError, "finance_source_identity_privilege_excessive"): self.ready()

    def test_column_only_fact_update_is_rejected(self):
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT UPDATE (amount_cents) ON finance_lines TO "{self.role}"')
        self.reader()
        with self.assertRaisesMessage(ReadinessError, "finance_source_reader_write_privilege"): self.ready()

    def test_missing_fact_select_is_rejected(self):
        with connection.cursor() as cursor:
            cursor.execute(f'REVOKE SELECT ON finance_import_batches FROM "{self.role}"')
        self.reader()
        with self.assertRaisesMessage(ReadinessError, "finance_source_fact_privilege_missing"): self.ready()
