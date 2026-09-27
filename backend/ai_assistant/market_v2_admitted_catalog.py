"""Frozen catalog check for the paused, material-admitted market v2 roots."""
from importlib import import_module


FUNCTIONS = (
    ("ai_market_v2_admitted_workflow_guard", "WORKFLOW_GUARD"),
    ("ai_market_v2_admitted_report_guard", "REPORT_GUARD"),
    ("ai_market_v2_admitted_orphan_guard", "ORPHAN_GUARD"),
    ("ai_market_v2_admitted_job_guard", "JOB_GUARD"),
    ("ai_market_v2_admitted_node_guard", "NODE_GUARD"),
    ("ai_market_v2_admitted_tool_guard", "TOOL_GUARD"),
    ("ai_market_v2_admitted_result_guard", "RESULT_GUARD"),
)
TRIGGERS = (
    ("ai_workflow_runs", "ai_market_v2_admitted_workflow_guard", 31, False),
    ("ai_report_runs", "ai_market_v2_admitted_report_guard", 31, False),
    ("ai_workflow_runs", "ai_market_v2_admitted_complete", 5, True),
    ("ai_agent_jobs", "ai_market_v2_admitted_job_guard", 31, False),
    ("ai_workflow_node_runs", "ai_market_v2_admitted_node_guard", 31, False),
    ("ai_agent_tool_dispatches", "ai_market_v2_admitted_tool_guard", 23, False),
    ("ai_agent_tool_results", "ai_market_v2_admitted_result_guard", 7, False),
)


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0053_business_market_v2_admitted_paused")

    def need(ok, reason):
        if not ok:
            raise error_type("AI market v2 admitted paused " + reason)

    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0053_business_market_v2_admitted_paused')")
    need(cursor.fetchone() == (True,), "migration receipt missing")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0056_business_market_v2_material_role_bridge')")
    bridged = cursor.fetchone() == (True,)
    bridge = (import_module(
        "ai_assistant.migrations.0056_business_market_v2_material_role_bridge")
        if bridged else None)
    cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
        "app='ai_assistant' AND name='0064_business_market_v2_synthetic_vertical')")
    synthetic = (import_module(
        "ai_assistant.migrations.0064_business_market_v2_synthetic_vertical")
        if cursor.fetchone() == (True,) else None)
    functions = {}
    for name, constant in FUNCTIONS:
        signature = "public." + name + "()"
        cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
            "l.lanname,pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)",
            [signature])
        row = cursor.fetchone()
        new_guard = bridge is not None and constant in {"WORKFLOW_GUARD", "REPORT_GUARD"}
        definition = (getattr(bridge, "NEW_" + constant.split("_")[0]) if new_guard
            else getattr(synthetic, "NEW_" + constant) if synthetic is not None
                and constant in {"TOOL_GUARD", "RESULT_GUARD"}
            else getattr(migration, constant))
        need(row is not None and row[1] == definition.split("$$")[1]
             and row[2] is new_guard and row[4] == "plpgsql"
             and {item.replace(" ", "") for item in (row[3] or [])}
                == {"search_path=pg_catalog,public"}
             and row[5] not in {"teruisi_ai_reader", "teruisi_ai_writer",
                                    "teruisi_ai_seal_writer"},
             "function body or owner drift")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=%s AND "
            "a.grantee=0 AND a.privilege_type='EXECUTE')", [row[0]])
        need(cursor.fetchone() == (False,), "PUBLIC function EXECUTE reopened")
        functions[name] = row[0]
    for table, trigger, kind, deferred in TRIGGERS:
        function = ("ai_market_v2_admitted_orphan_guard" if trigger ==
                    "ai_market_v2_admitted_complete" else trigger)
        cursor.execute("SELECT t.tgtype,t.tgenabled,t.tgdeferrable,"
            "t.tginitdeferred,t.tgfoid FROM pg_catalog.pg_trigger t "
            "WHERE t.tgrelid=to_regclass(%s) AND t.tgname=%s AND "
            "NOT t.tgisinternal", ["public." + table, trigger])
        need(cursor.fetchone() == (kind, "O", deferred, deferred,
                                   functions[function]),
             "trigger binding or enforcement drift")
    if bridged:
        signature = bridge.SIGNATURE
        cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)",
            ["public.ai_business_market_v2_materials"])
        owner = cursor.fetchone()
        need(row is not None and owner is not None
             and row[1] == bridge.METADATA.split("$$")[1]
             and row[2] is True and row[4] == owner[0]
             and row[5] == "plpgsql"
             and {item.replace(" ", "") for item in (row[3] or [])}
                == {"search_path=pg_catalog,public"},
             "reader metadata function drift")
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
            "has_function_privilege(%s,%s,'EXECUTE'),"
            "EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=%s AND "
            "a.grantee=0 AND a.privilege_type='EXECUTE')", [
                bridge.READER, signature, bridge.WRITER, signature, row[0]])
        need(cursor.fetchone() == (True, False, False),
             "reader metadata function ACL drift")
        for role in (bridge.READER, bridge.WRITER):
            cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                "has_any_column_privilege(%s,%s,'SELECT')", [role,
                "public.ai_business_market_v2_materials", role,
                "public.ai_business_market_v2_materials"])
            need(cursor.fetchone() == (False, False),
                 "market material table or column SELECT opened")
