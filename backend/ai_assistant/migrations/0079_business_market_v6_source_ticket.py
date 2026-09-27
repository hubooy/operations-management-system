"""Default-closed pointer to one bounded sealed market page for a 0077 job.

No Agent read receipt, market row copy, general reader grant, or paid authority.
The ordinary formal release and backup paths refuse this migration.
"""
from importlib import import_module

from django.db import migrations

from ai_assistant import business_market_v6_source_ticket_sql as source


TABLE = source.TABLE
MIGRATION = "0079_business_market_v6_source_ticket"
FUNCTIONS = (source.GUARD_SQL, source.EXPECTED_SQL,
    source.ISSUE_SQL, source.OUTCOME_SQL)


def _predecessor(cursor):
    import_module(
        "ai_assistant.migrations.0078_business_promotion_budget_v11_signed_publication"
        ).verify_catalog(cursor)


def _owner(cursor):
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid=%s::regclass",
        ["public.protected_business_market_v6_topologies"])
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("0079 requires frozen 0077 owner")
    return row[0]


def _role(cursor, *, closed=True, installed=True):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid WHERE rolname=%s", [source.ROLE])
    row = cursor.fetchone()
    if (row is None or row[1:7] != (False,) * 6
            or (closed and row != (False,) * 7 + (True,))):
        raise RuntimeError("0079 source role attributes drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole",
        [source.ROLE, source.ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0079 source role membership drift")
    for kind, table in (("table", "pg_catalog.pg_class"),
            ("column", "pg_catalog.pg_attribute")):
        if kind == "table":
            query = ("SELECT count(*) FROM pg_catalog.pg_class c JOIN "
                "pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
                "pg_catalog.acldefault('r',c.relowner))) a WHERE "
                "n.nspname='public' AND (c.relname LIKE %s OR "
                "c.relname LIKE %s) AND a.grantee=%s::regrole")
        else:
            query = ("SELECT count(*) FROM pg_catalog.pg_attribute att JOIN "
                "pg_catalog.pg_class c ON c.oid=att.attrelid JOIN "
                "pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(att.attacl,"
                "pg_catalog.acldefault('c',c.relowner))) a WHERE "
                "n.nspname='public' AND att.attnum>0 AND NOT att.attisdropped "
                "AND (c.relname LIKE %s OR c.relname LIKE %s) "
                "AND a.grantee=%s::regrole")
        cursor.execute(query, ["ai_%", "protected_business_%", source.ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0079 source role gained direct " + kind + " ACL")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(p.proowner),p.proname,"
        "a.privilege_type FROM pg_catalog.pg_proc p JOIN "
        "pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
        "pg_catalog.acldefault('f',p.proowner))) a WHERE n.nspname='public' "
        "AND a.grantee=%s::regrole ORDER BY p.proname,a.privilege_type",
        [source.ROLE])
    actual_grants = cursor.fetchall()
    expected_grants = ([(_owner(cursor),
            "ai_market_v6_issue_source_ticket", "EXECUTE"),
            (_owner(cursor), "ai_market_v6_source_ticket_outcome", "EXECUTE")]
        if installed else [])
    if actual_grants != expected_grants:
        raise RuntimeError("0079 source role function ACL drift: " +
            repr(actual_grants))


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _predecessor(cursor)
        old = _old_functions(cursor)
        owner = schema_editor.connection.ops.quote_name(_owner(cursor))
        cursor.execute("SELECT to_regrole(%s)", [source.ROLE])
        if cursor.fetchone() == (None,):
            cursor.execute("CREATE ROLE " + source.ROLE + " NOLOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                "NOBYPASSRLS PASSWORD NULL")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
            "FROM pg_catalog.pg_authid WHERE rolname=%s", [source.ROLE])
        if cursor.fetchone() != (False,) * 7 + (True,):
            raise RuntimeError("0079 installer requires closed role")
        cursor.execute(source.CREATE_TABLE)
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("ALTER TABLE " + TABLE + " OWNER TO " + owner)
        for definition, signature in zip(FUNCTIONS, source.SIGNATURES):
            cursor.execute(definition)
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("ALTER FUNCTION " + signature + " OWNER TO " + owner)
        for signature in (source.ISSUE, source.OUTCOME):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + source.ROLE)
        cursor.execute("CREATE TRIGGER ai_market_v6_source_ticket_immutable "
            "BEFORE INSERT OR UPDATE OR DELETE ON " + TABLE +
            " FOR EACH ROW EXECUTE FUNCTION " + source.GUARD)
        cursor.execute("CREATE TRIGGER ai_market_v6_source_ticket_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)
        if _old_functions(cursor) != old:
            raise RuntimeError("0079 changed frozen 0077/0078 function")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _role(cursor)
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0079 cannot discard persisted source tickets")
        old = _old_functions(cursor)
        cursor.execute("DROP TRIGGER ai_market_v6_source_ticket_no_truncate ON " + TABLE)
        cursor.execute("DROP TRIGGER ai_market_v6_source_ticket_immutable ON " + TABLE)
        for signature in reversed(source.SIGNATURES):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        _role(cursor, installed=False)
        if _old_functions(cursor) != old:
            raise RuntimeError("0079 reverse changed frozen 0077/0078 function")


def _old_functions(cursor):
    result = []
    for signature in ("public.ai_market_v6_create_paused_topology(text,text,text)",
            "public.ai_market_v6_cancel_paused_topology(text)",
            "public.ai_market_v6_paused_topology_outcome(text,text,text,text)",
            "public.ai_budget_v11_publication_guard_v2()"):
        cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef,"
            "proconfig FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("0079 frozen predecessor function missing")
        result.append((signature, row))
    return tuple(result)


def verify_catalog(cursor, *, allow_test_login=False):
    _predecessor(cursor)
    _role(cursor, closed=not allow_test_login)
    owner = _owner(cursor)
    cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
        "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)", [TABLE])
    if cursor.fetchone() != ("r", owner):
        raise RuntimeError("0079 source table owner/kind drift")
    cursor.execute("SELECT attname,format_type(atttypid,atttypmod),attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass(%s) "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [TABLE])
    columns = cursor.fetchall()
    expected = [("ticket_id", "character varying(160)", True),
        ("report_id", "character varying(160)", True),
        ("owner_email", "character varying(320)", True),
        ("owner_version", "bigint", True),
        ("job_id", "character varying(160)", True),
        ("view_name", "character varying(32)", True),
        ("page_index", "integer", True), ("ticket_json", "text", True),
        ("ticket_digest", "character varying(64)", True),
        ("created_at", "timestamp with time zone", True)]
    if columns != expected:
        raise RuntimeError("0079 source ticket columns drift")
    cursor.execute("SELECT c.contype,ARRAY(SELECT a.attname FROM "
        "unnest(c.conkey) k(attnum) JOIN pg_catalog.pg_attribute a ON "
        "a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.attnum),"
        "CASE WHEN c.confrelid=0 THEN NULL ELSE c.confrelid::regclass::text "
        "END,c.convalidated,c.condeferrable,"
        "pg_catalog.pg_get_constraintdef(c.oid) FROM "
        "pg_catalog.pg_constraint c WHERE c.conrelid=%s::regclass", [TABLE])
    constraints = cursor.fetchall()
    by_kind = {kind: [row for row in constraints if row[0] == kind]
        for kind in ("p", "u", "f", "c")}
    if ({tuple(row[1]) for row in by_kind["p"]} != {("ticket_id",)}
            or {tuple(row[1]) for row in by_kind["u"]} !=
                {("report_id", "job_id", "view_name", "page_index")}
            or {row[5] for row in by_kind["p"]} !=
                {"PRIMARY KEY (ticket_id)"}
            or {row[5] for row in by_kind["u"]} !=
                {"UNIQUE (report_id, job_id, view_name, page_index)"}):
        raise RuntimeError("0079 source ticket primary/unique drift")
    fk = {tuple(row[1]): (str(row[2]).split(".")[-1], row[5])
        for row in by_kind["f"]}
    if ({columns: target for columns, (target, _) in fk.items()} != {
            ("report_id",): "protected_business_market_v6_topologies",
            ("job_id",): "ai_agent_jobs"} or any(definition not in {
                "FOREIGN KEY (" + ", ".join(columns) + ") REFERENCES " +
                prefix + target + "(" + ("report_id" if columns ==
                ("report_id",) else "id") + ") ON DELETE RESTRICT"
                for prefix in ("", "public.")}
                for columns, (target, definition) in fk.items())):
        raise RuntimeError("0079 source ticket foreign key drift")
    checks = {tuple(row[1]): row[5] for row in by_kind["c"]}
    expected_checks = {
        ("owner_version",): "CHECK ((owner_version >= 1))",
        ("view_name",): "CHECK (((view_name)::text = 'rank_entry_exit'::text))",
        ("page_index",): "CHECK (((page_index >= 0) AND (page_index < 20000)))",
        ("ticket_json",): "CHECK ((octet_length(ticket_json) <= 8192))",
        ("ticket_digest",):
            "CHECK (((ticket_digest)::text ~ '^[0-9a-f]{64}$'::text))",
    }
    if (checks != expected_checks or len(constraints) != 9
            or any(row[3:5] != (True, False) for row in constraints)):
        raise RuntimeError("0079 source ticket exact check expression drift: " +
            repr(checks))
    cursor.execute("SELECT tgname,tgtype,tgqual IS NULL,"
        "tgfoid,tgenabled,tgdeferrable,tginitdeferred FROM pg_catalog.pg_trigger "
        "WHERE tgrelid=to_regclass(%s) AND NOT tgisinternal ORDER BY tgname",
        [TABLE])
    triggers = cursor.fetchall()
    expected_triggers = (
        ("ai_market_v6_source_ticket_immutable", 31, source.GUARD),
        ("ai_market_v6_source_ticket_no_truncate", 34,
            "public.ai_v4_seal_ticket_no_truncate()"))
    if len(triggers) != len(expected_triggers):
        raise RuntimeError("0079 source ticket trigger drift")
    for actual, (name, event, signature) in zip(triggers, expected_triggers):
        cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
        if actual != (name, event, True, cursor.fetchone()[0],
                "O", False, False):
            raise RuntimeError("0079 source ticket trigger binding drift")
    for definition, signature in zip(FUNCTIONS, source.SIGNATURES):
        cursor.execute("SELECT prosrc,prosecdef,proconfig,"
            "pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] != (signature != source.GUARD)
                or {part.replace(" ", "") for part in row[2] or []}
                    != {"search_path=pg_catalog,public"}
                or row[3] != owner):
            raise RuntimeError("0079 source function body/owner drift")
        cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a WHERE "
            "p.oid=to_regprocedure(%s) AND a.grantee<>p.proowner "
            "ORDER BY 1,2,3", [signature])
        expected_acl = [(source.ROLE, "EXECUTE", False)] if signature in {
            source.ISSUE, source.OUTCOME} else []
        if cursor.fetchall() != expected_acl:
            raise RuntimeError("0079 source function ACL drift")
    cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
        "pg_catalog.pg_get_userbyid(a.grantee) END,a.privilege_type "
        "FROM pg_catalog.pg_class c CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) a WHERE c.oid=to_regclass(%s) "
        "AND a.grantee<>c.relowner", [TABLE])
    if cursor.fetchall():
        raise RuntimeError("0079 source table ACL drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a JOIN "
        "pg_catalog.pg_class c ON c.oid=a.attrelid CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) acl WHERE "
        "c.oid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped "
        "AND acl.grantee<>c.relowner", [TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0079 source column ACL drift")
    _predecessor(cursor)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0078_business_promotion_budget_v11_signed_publication")]
    operations = [migrations.RunPython(install, uninstall)]
