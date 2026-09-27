"""Frozen 0055 candidate-only three-window period sidecar catalog gate."""
from importlib import import_module


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0055_business_v4_period_plan_candidate")
    table = migration.TABLE

    def need(ok, reason):
        if not ok:
            raise error_type("AI v4 period plan candidate " + reason)

    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0055_business_v4_period_plan_candidate')")
    need(cursor.fetchone() == (True,), "migration receipt missing")
    cursor.execute("SELECT c.relowner,c.relkind FROM pg_catalog.pg_class c "
        "WHERE c.oid=to_regclass(%s)", [table])
    relation = cursor.fetchone()
    need(relation is not None and relation[1] == "r", "table missing")
    owner = relation[0]
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(%s)", [owner])
    need(cursor.fetchone()[0] not in {"teruisi_ai_reader", "teruisi_ai_writer",
         "teruisi_ai_seal_writer"}, "table runtime owner drift")
    cursor.execute("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 "
        "AND NOT a.attisdropped ORDER BY a.attnum", [table])
    need(cursor.fetchall() == [
        ("run_id", "character varying(160)", True),
        ("attempt_id", "character varying(160)", True),
        ("owner_email", "character varying(320)", True),
        ("actor_version", "bigint", True),
        ("plan_digest", "character varying(64)", True),
        ("directory_digest", "character varying(64)", True),
        ("source_root", "character varying(64)", True),
        ("envelope_json", "text", True),
        ("envelope_digest", "character varying(64)", True),
        ("created_at", "timestamp with time zone", True),
    ], "table columns drift")
    for role in ("teruisi_ai_reader", "teruisi_ai_writer",
                 "teruisi_ai_seal_writer"):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            need(role != "teruisi_ai_writer", "writer role missing")
            continue
        cursor.execute("SELECT has_table_privilege(%s,%s,"
            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')", [role, table])
        need(cursor.fetchone() == (False,), "table ACL opened")
        cursor.execute("SELECT has_any_column_privilege(%s,%s,"
            "'SELECT,INSERT,UPDATE')", [role, table])
        need(cursor.fetchone() == (False,), "column ACL opened")
    expected = (
        (migration.WRITE, migration.RECORD, True, True),
        ("public.ai_v4_period_plan_candidate_guard()", migration.GUARD,
         False, False),
        ("public.ai_v4_period_plan_no_truncate()", migration.NO_TRUNCATE,
         False, False),
    )
    function_oids = {}
    for signature, definition, secured, writer_allowed in expected:
        cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
            "l.lanname,p.proowner FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        need(row is not None and row[1] == definition.split("$$")[1]
             and row[2] is secured and row[4] == "plpgsql" and row[5] == owner
             and {item.replace(" ", "") for item in (row[3] or [])}
                == {"search_path=pg_catalog,public"},
             "function body or ownership drift")
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            ["teruisi_ai_writer", signature])
        need(cursor.fetchone() == (writer_allowed,), "function ACL drift")
        for role in ("teruisi_ai_reader", "teruisi_ai_seal_writer"):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, signature])
            need(cursor.fetchone() == (False,), "runtime function ACL opened")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=%s AND "
            "acl.grantee=0 AND acl.privilege_type='EXECUTE')", [row[0]])
        need(cursor.fetchone() == (False,), "PUBLIC function EXECUTE opened")
        function_oids[signature] = row[0]
    for name, signature, kind in (
        ("ai_v4_period_candidate_state",
         "public.ai_v4_period_plan_candidate_guard()", 31),
        ("ai_v4_period_candidate_no_truncate",
         "public.ai_v4_period_plan_no_truncate()", 34),
    ):
        cursor.execute("SELECT t.tgtype,t.tgenabled,t.tgfoid "
            "FROM pg_catalog.pg_trigger t WHERE t.tgrelid=to_regclass(%s) "
            "AND t.tgname=%s AND NOT t.tgisinternal", [table, name])
        need(cursor.fetchone() == (kind, "O", function_oids[signature]),
             "trigger binding drift")
