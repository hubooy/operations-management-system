"""Isolated PostgreSQL test runner for protected append-only tables.

The protected ticket tables intentionally have no Django model. Django's
normal flush lists only managed model tables, so their foreign keys require
TRUNCATE CASCADE in the isolated rehearsal database. Production connections
never select this runner. finance.0005's ORM sidecar instead has an unconditional
TRUNCATE guard; finance.0006 adds three more. Only this exact test database
temporarily disables their TRUNCATE events inside one rollback-safe flush
transaction and verifies every event is re-enabled.
"""
from __future__ import annotations

from django.db import connections
from django.db import transaction
from django.test.runner import DiscoverRunner


PROTECTED_SQL_TABLES = frozenset({
    "protected_business_budget_v11_verifier_keys",
    "protected_business_budget_v11_proof_tickets",
    "protected_business_budget_v11_proof_ticket_claims",
    "protected_business_budget_v11_login_attestations",
    "protected_business_budget_v11_signed_receipts_v3",
    "protected_business_v4_report_link_intents",
    "protected_business_v4_report_source_links",
    "protected_business_market_v2_rate_proposals",
    "protected_business_market_v2_cap_proposals",
    "protected_business_market_v2_authority_revocations",
    "protected_business_market_v6_topologies",
    "protected_business_market_v6_topology_cancellations",
    "protected_business_budget_v11_publications_v2",
})
FINANCE_DIGEST_TABLES = (
    "finance_raw_column_evidence_months",
    "finance_raw_column_evidence_columns",
    "finance_raw_column_evidence_cells",
)
FINANCE_TRUNCATE_GUARD = "fin_raw_evidence_immutable_truncate"
FINANCE_WORKBOOK_TABLES = (
    "finance_raw_workbook_attestations",
    "finance_raw_workbook_columns",
    "finance_raw_workbook_cells",
)
FINANCE_WORKBOOK_TRUNCATE_GUARD = "fin_workbook_no_truncate"


def _finance_truncate_guard_state(connection, targets):
    states = []
    with connection.cursor() as cursor:
        for table, guard in targets:
            cursor.execute("SELECT t.tgenabled FROM pg_catalog.pg_trigger t "
                "WHERE t.tgrelid=%s::regclass AND t.tgname=%s",
                ["public." + table, guard])
            states.append(cursor.fetchone())
    return tuple(states)


class IsolatedPostgresTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        old_config = super().setup_databases(**kwargs)
        self._patched_flush = []
        for connection in connections.all():
            data = connection.settings_dict
            if connection.vendor != "postgresql":
                continue
            if (data["HOST"] != "127.0.0.1" or
                    data["NAME"] != "test_teruisi_ai_rehearsal" or
                    not 55440 <= int(data["PORT"]) <= 55999):
                raise RuntimeError("protected SQL flush requires isolated test "
                    "database: hostOk=" + str(data["HOST"] == "127.0.0.1") +
                    ", nameOk=" + str(data["NAME"] ==
                        "test_teruisi_ai_rehearsal") +
                    ", portOk=" + str(55440 <= int(data["PORT"]) <= 55999))
            operations = connection.ops
            original = operations.sql_flush
            original_execute = operations.execute_sql_flush

            def isolated_flush(style, tables, *, reset_sequences=False,
                               allow_cascade=False, _original=original,
                               _connection=connection):
                present = PROTECTED_SQL_TABLES.intersection(
                    _connection.introspection.table_names())
                selected = sorted(set(tables).union(present))
                return _original(style, selected,
                    reset_sequences=reset_sequences, allow_cascade=True)

            operations.sql_flush = isolated_flush

            def isolated_execute_sql_flush(sql_list, *, _original=original_execute,
                                           _connection=connection):
                present = set(_connection.introspection.table_names())
                if not sql_list or not set(FINANCE_DIGEST_TABLES).issubset(
                        present):
                    return _original(sql_list)
                workbook_present = set(FINANCE_WORKBOOK_TABLES).intersection(
                    present)
                if workbook_present and workbook_present != set(
                        FINANCE_WORKBOOK_TABLES):
                    raise RuntimeError("finance.0006 test flush table inventory drift")
                targets = tuple((table, FINANCE_TRUNCATE_GUARD)
                    for table in FINANCE_DIGEST_TABLES) + tuple(
                    (table, FINANCE_WORKBOOK_TRUNCATE_GUARD)
                    for table in FINANCE_WORKBOOK_TABLES
                    if table in workbook_present)
                # This test database's superuser must clean ORM tables between
                # TransactionTestCase methods. The finance production trigger
                # remains unconditional; disable only its TRUNCATE event inside
                # a rollback-safe transaction around this exact test flush.
                with transaction.atomic(using=_connection.alias):
                    if _finance_truncate_guard_state(_connection,
                            targets) != (("O",),) * len(targets):
                        raise RuntimeError("finance test flush guard drift")
                    with _connection.cursor() as cursor:
                        for table, guard in targets:
                            cursor.execute("ALTER TABLE public." + table +
                                " DISABLE TRIGGER " + guard)
                    _original(sql_list)
                    with _connection.cursor() as cursor:
                        for table, guard in targets:
                            cursor.execute("ALTER TABLE public." + table +
                                " ENABLE TRIGGER " + guard)
                    if _finance_truncate_guard_state(_connection,
                            targets) != (("O",),) * len(targets):
                        raise RuntimeError("finance test flush guard not restored")

            operations.execute_sql_flush = isolated_execute_sql_flush
            self._patched_flush.append((operations, original, original_execute))
        return old_config

    def teardown_databases(self, old_config, **kwargs):
        try:
            return super().teardown_databases(old_config, **kwargs)
        finally:
            for operations, original, original_execute in getattr(
                    self, "_patched_flush", ()):
                operations.sql_flush = original
                operations.execute_sql_flush = original_execute
