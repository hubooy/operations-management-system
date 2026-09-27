"""Frozen 0049 single-source claim bridge catalog check."""
from importlib import import_module


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0049_business_v4_sealer_source_bridge")

    def need(ok, reason):
        if not ok:
            raise error_type("AI v4 sealer source bridge " + reason)

    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0049_business_v4_sealer_source_bridge')")
    need(cursor.fetchone() == (True,), "migration receipt missing")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [migration.ROLE])
    need(cursor.fetchone() == (False,) * 7, "sealer role not closed")
    cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
        "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE p.oid=to_regprocedure(%s)", [migration.SIGNATURE])
    row = cursor.fetchone()
    need(row is not None and row[1] == migration.SOURCE.split("$$")[1]
         and row[2] is True and row[4] == "plpgsql"
         and {item.replace(" ", "") for item in (row[3] or [])}
            == {"search_path=pg_catalog,public"}
         and row[5] not in {"teruisi_ai_reader", "teruisi_ai_writer",
                            migration.ROLE}, "function body or owner drift")
    for role, expected in ((migration.ROLE, True),
                           ("teruisi_ai_reader", False),
                           ("teruisi_ai_writer", False)):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            need(role != migration.ROLE, "required sealer role missing")
            continue
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            [role, row[0]])
        need(cursor.fetchone() == (expected,), "function ACL drift")
    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
        [migration.ROLE,
         "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"])
    need(cursor.fetchone() == (False,), "direct seal commit reopened")
