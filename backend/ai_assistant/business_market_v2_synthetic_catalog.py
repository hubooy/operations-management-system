"""Frozen 0064 test-only synthetic flow/job/provider/tool guard catalog."""
from importlib import import_module


FUNCTIONS = (
    ("public.ai_market_v2_synthetic_flow_guard()", "FLOW_GUARD", True),
    ("public.ai_market_v2_synthetic_report_guard()", "REPORT_GUARD", True),
    ("public.ai_market_v2_synthetic_child_guard()", "CHILD_GUARD", False),
    ("public.ai_market_v2_synthetic_orphan_guard()", "ORPHAN", True),
    ("public.ai_market_v2_create_synthetic_chain(text)", "CREATE_CHAIN", True),
)
TRIGGERS = {
    ("ai_workflow_runs","ai_market_v2_synthetic_flow_guard"):
        "public.ai_market_v2_synthetic_flow_guard()",
    ("ai_report_runs","ai_market_v2_synthetic_report_guard"):
        "public.ai_market_v2_synthetic_report_guard()",
    ("ai_agent_jobs","ai_market_v2_synthetic_job_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_workflow_node_runs","ai_market_v2_synthetic_node_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_agent_provider_dispatches","ai_market_v2_synthetic_provider_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_agent_provider_results","ai_market_v2_synthetic_provider_result_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_agent_tool_dispatches","ai_market_v2_synthetic_tool_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_agent_tool_results","ai_market_v2_synthetic_tool_result_guard"):
        "public.ai_market_v2_synthetic_child_guard()",
    ("ai_workflow_runs","ai_market_v2_synthetic_complete"):
        "public.ai_market_v2_synthetic_orphan_guard()",
}


def verify(cursor, error_type=ValueError):
    migration = import_module(
        "ai_assistant.migrations.0064_business_market_v2_synthetic_vertical")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [migration.ROLE])
    if cursor.fetchone() != (False,) * 7:
        raise error_type("market synthetic role widened")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole",
        [migration.ROLE,migration.ROLE])
    if cursor.fetchone() != (0,):
        raise error_type("market synthetic role gained membership")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid=to_regclass(%s)",
        ["public.ai_business_market_v2_execution_plans"])
    owner_row=cursor.fetchone()
    if owner_row is None:
        raise error_type("market plan predecessor missing")
    owner=owner_row[0]
    cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,p.proacl::text,"
        "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
        "WHERE p.oid=to_regprocedure('public.ai_market_v2_parked_workflow_guard()')")
    prior=cursor.fetchone()
    if (prior is None or prior[0] != migration.NEW_PARKED_WORKFLOW.split("$$",2)[1]
            or prior[1] is not False or prior[4] != owner):
        raise error_type("market synthetic parked guard version drift")
    for signature, definition in (
            ("public.ai_market_v2_admitted_tool_guard()",
                migration.NEW_TOOL_GUARD),
            ("public.ai_market_v2_admitted_result_guard()",
                migration.NEW_RESULT_GUARD)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),p.proacl::text "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature])
        old_guard=cursor.fetchone()
        if (old_guard is None or old_guard[0] != definition.split("$$",2)[1]
                or old_guard[1] is not False or old_guard[3] != owner
                or {item.replace(" ","") for item in (old_guard[2] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise error_type("market synthetic fifth-tool exception drift")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(%s) "
            "AND a.grantee=0 AND a.privilege_type='EXECUTE')",[signature])
        if cursor.fetchone() != (False,):
            raise error_type("market synthetic fifth-tool PUBLIC EXECUTE reopened")
    for signature, constant, definer in FUNCTIONS:
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row=cursor.fetchone()
        if (row is None or row[0] != getattr(migration,constant).split("$$",2)[1]
                or row[1] is not definer or row[3] != owner
                or row[4] != "plpgsql"
                or {item.replace(" ","") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise error_type("market synthetic function drift")
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        grants={owner,migration.ROLE} if signature==migration.SIGNATURE else {owner}
        if set(cursor.fetchall()) != {(name,"EXECUTE") for name in grants}:
            raise error_type("market synthetic function ACL drift")
    cursor.execute("SELECT c.relname,t.tgname,t.tgfoid::regprocedure::text,"
        "t.tgenabled,t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
        "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname LIKE 'ai_market_v2_synthetic_%'")
    rows=cursor.fetchall()
    if len(rows)!=len(TRIGGERS) or {(table,name) for table,name,*_ in rows}!=set(TRIGGERS):
        raise error_type("market synthetic trigger inventory drift")
    for table,name,function,enabled,deferred,initial in rows:
        cursor.execute("SELECT to_regprocedure(%s)::text",[TRIGGERS[(table,name)]])
        expected=cursor.fetchone()[0]
        wants_deferred=name=="ai_market_v2_synthetic_complete"
        if (function,enabled,deferred,initial)!=(expected,"O",wants_deferred,wants_deferred):
            raise error_type("market synthetic trigger binding drift")
