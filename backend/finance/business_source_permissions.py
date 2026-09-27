"""Minimal finance reader actor grants and read-only readiness checks."""
import re

USER_COLUMNS = ("email", "role", "status", "scope", "version")
SOURCE_TABLES = ("finance_lines", "finance_months", "finance_import_batches", "finance_data_revisions")


class FinanceSourcePermissionError(RuntimeError):
    pass


def grant_actor_read(cursor, role="teruisi_finance_reader"):
    if type(role) is not str or re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) is None:
        raise ValueError("Invalid database role")
    cursor.execute(f'GRANT SELECT (email, role, status, scope, version) ON access_control_users TO "{role}"')


def validate_reader(cursor):
    """Check actual current_user privileges, including inherited privileges."""
    for column in USER_COLUMNS:
        cursor.execute("SELECT has_column_privilege(current_user,'access_control_users',%s,'SELECT')", [column])
        if cursor.fetchone()[0] is not True:
            raise FinanceSourcePermissionError("finance_source_identity_privilege_missing")
    cursor.execute("SELECT a.attname FROM pg_attribute a WHERE a.attrelid='public.access_control_users'::regclass "
                   "AND a.attnum>0 AND NOT a.attisdropped AND NOT (a.attname=ANY(%s)) "
                   "AND has_column_privilege(current_user,a.attrelid,a.attname,'SELECT')", [list(USER_COLUMNS)])
    if cursor.fetchone() is not None:
        raise FinanceSourcePermissionError("finance_source_identity_privilege_excessive")
    for table in SOURCE_TABLES:
        cursor.execute("SELECT has_table_privilege(current_user,%s,'SELECT')", [table])
        if cursor.fetchone()[0] is not True:
            raise FinanceSourcePermissionError("finance_source_fact_privilege_missing")
    for table in (*SOURCE_TABLES, "access_control_users"):
        cursor.execute("SELECT has_any_column_privilege(current_user,%s,'INSERT'), "
                       "has_any_column_privilege(current_user,%s,'UPDATE'), "
                       "has_table_privilege(current_user,%s,'DELETE'), has_table_privilege(current_user,%s,'TRUNCATE')",
                       [table, table, table, table])
        if any(cursor.fetchone()):
            raise FinanceSourcePermissionError("finance_source_reader_write_privilege")
