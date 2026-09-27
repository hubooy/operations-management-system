"""Frozen SQL-owned five-Agent execution-plan schema and privileges."""
from importlib import import_module


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0063_business_market_v2_execution_plan")
    role = migration.ROLE
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [role])
    if cursor.fetchone() != (False,) * 7:
        raise error_type("market plan attestor role widened")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [role, role])
    if cursor.fetchone() != (0,):
        raise error_type("market plan attestor membership drift")
    cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
        "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)", [migration.TABLE])
    table = cursor.fetchone()
    if table is None or table[0] != "r":
        raise error_type("market plan table missing")
    owner = table[1]
    cursor.execute("SELECT attname,format_type(atttypid,atttypmod),attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass(%s) "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [migration.TABLE])
    if cursor.fetchall() != [
            ("id", "character varying(64)", True),
            ("execution_report_id", "character varying(160)", True),
            ("plan_json", "text", True),
            ("plan_digest", "character varying(64)", True),
            ("created_at", "timestamp with time zone", True)]:
        raise error_type("market plan column inventory drift")
    cursor.execute("SELECT contype,pg_catalog.pg_get_constraintdef(oid) "
        "FROM pg_catalog.pg_constraint WHERE conrelid=to_regclass(%s)",
        [migration.TABLE])
    if set(cursor.fetchall()) != {
            ("p", "PRIMARY KEY (id)"),
            ("u", "UNIQUE (execution_report_id)"),
            ("f", "FOREIGN KEY (execution_report_id) REFERENCES "
                "ai_report_runs(id) ON DELETE RESTRICT")}:
        raise error_type("market plan PK/unique/FK drift")
    for account in (role, migration.READER, migration.WRITER):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                          "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [account, migration.TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise error_type("market plan table ACL opened")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [account, migration.TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise error_type("market plan column ACL opened")
    for signature, definition, definer, grants in (
            ("public.ai_market_v2_execution_plan_guard()", migration.GUARD,
                False, {owner}),
            (migration.EXPECTED_SIGNATURE, migration.EXPECTED, True, {owner}),
            (migration.ATTEST_SIGNATURE, migration.ATTEST, True, {owner, role}),
            (migration.READ_SIGNATURE, migration.READ, True,
                {owner, migration.READER})):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not definer or row[3] != owner
                or row[4] != "plpgsql"
                or {item.replace(" ", "") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise error_type("market plan function drift")
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        if set(cursor.fetchall()) != {(name, "EXECUTE") for name in grants}:
            raise error_type("market plan function ACL drift")
    cursor.execute("SELECT t.tgname,t.tgfoid::regprocedure::text,t.tgenabled "
        "FROM pg_catalog.pg_trigger t WHERE t.tgrelid=to_regclass(%s) "
        "AND NOT t.tgisinternal", [migration.TABLE])
    triggers = cursor.fetchall()
    wanted = {"ai_market_v2_execution_plan_guard":
        "public.ai_market_v2_execution_plan_guard()",
        "ai_market_v2_execution_plan_no_truncate":
        "public.ai_v4_seal_ticket_no_truncate()"}
    if len(triggers) != 2 or {item[0] for item in triggers} != set(wanted):
        raise error_type("market plan trigger set drift")
    for name, signature, enabled in triggers:
        cursor.execute("SELECT to_regprocedure(%s)::text", [wanted[name]])
        if signature != cursor.fetchone()[0] or enabled != "O":
            raise error_type("market plan trigger binding drift")
