"""Frozen catalog gate for the default-closed v4 seal/consumption wrapper."""
from importlib import import_module


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0052_business_v4_commit_consumption")

    def need(ok, reason):
        if not ok:
            raise error_type("AI v4 commit consumption " + reason)

    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0052_business_v4_commit_consumption')")
    need(cursor.fetchone() == (True,), "migration receipt missing")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [migration.SEALER])
    need(cursor.fetchone() == (False,) * 7, "sealer role not closed")
    cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
        "p.proowner,pg_catalog.pg_get_function_result(p.oid) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
        "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)",
        [migration.SIGNATURE])
    row = cursor.fetchone()
    need(row is not None and row[1] == migration.SQL.split("$$")[1]
         and row[2] is True and row[4] == "plpgsql"
         and {item.replace(" ", "") for item in (row[3] or [])}
            == {"search_path=pg_catalog,public"}
         and row[6] == "TABLE(run_id text, evidence_version bigint, sealed_digest text, consumed_at timestamp with time zone)",
         "function body or signature drift")
    owner = row[5]
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(%s)", [owner])
    need(cursor.fetchone()[0] not in {migration.SEALER,
         "teruisi_ai_reader", "teruisi_ai_writer"},
         "protected owner is a runtime role")
    for target in (migration.OLD_COMMIT,
                   "public.ai_business_v4_seal_consumptions"):
        if target.startswith("public.ai_business_"):
            cursor.execute("SELECT relowner FROM pg_catalog.pg_class "
                "WHERE oid=to_regclass(%s)", [target])
        else:
            cursor.execute("SELECT proowner FROM pg_catalog.pg_proc "
                "WHERE oid=to_regprocedure(%s)", [target])
        need(cursor.fetchone() == (owner,), "protected owner drift")
    for role, expected in ((migration.SEALER, True),
                           ("teruisi_ai_reader", False),
                           ("teruisi_ai_writer", False)):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            need(role != migration.SEALER, "required sealer role missing")
            continue
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            [role, row[0]])
        need(cursor.fetchone() == (expected,), "function ACL drift")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
        "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=%s AND "
        "acl.grantee=0 AND acl.privilege_type='EXECUTE')", [row[0]])
    need(cursor.fetchone() == (False,), "PUBLIC EXECUTE reopened")
    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
        "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
        [migration.SEALER, migration.OLD_COMMIT, migration.SEALER,
         migration.CONSUMPTIONS])
    need(cursor.fetchone() == (False, False),
         "direct seal or consumption table reopened")
