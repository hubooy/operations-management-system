"""Default-NOLOGIN SQL owner of one paused, model-free five-job topology.

The predecessor migrations and their functions are never replaced. Ordinary
AI writer/reader roles receive no protected table or function privilege.
"""
from django.db import migrations

from ai_assistant import business_market_v6_paused_topology_sql as v6


MIGRATION = "0077_business_market_v6_paused_topology"
TABLES = (v6.TABLE, v6.CANCEL_TABLE)
EXPECTED_COLUMNS = {
    v6.TABLE: (("report_id", "character varying",164),
        ("workflow_id", "character varying",164),
        ("owner_email", "character varying",324),
        ("owner_version", "bigint",-1),
        ("client_request_id", "character varying",132),
        ("request_digest", "character varying",68),
        ("snapshot_json", "text",-1),
        ("snapshot_digest", "character varying",68),
        ("graph_json", "text",-1),
        ("graph_digest", "character varying",68),
        ("source_report_id", "character varying",164),
        ("source_plan_id", "character varying",68),
        ("source_plan_digest", "character varying",68),
        ("source_cost_id", "character varying",68),
        ("source_cost_digest", "character varying",68),
        ("created_at", "timestamp with time zone",-1)),
    v6.CANCEL_TABLE: (("report_id", "character varying",164),
        ("request_digest", "character varying",68),
        ("reason_digest", "character varying",68),
        ("expected_version", "bigint",-1),
        ("cancelled_at", "timestamp with time zone",-1)),
}
STATE_TABLES = ("ai_workflow_runs", "ai_agent_jobs", "ai_workflow_node_runs")
EFFECT_TABLES = ("ai_agent_provider_dispatches",
    "ai_agent_tool_dispatches", "ai_business_market_v2_read_receipts",
    "ai_agent_checkpoints", "ai_agent_events")
ANCILLARY_TABLES = ("ai_workflow_events", "ai_report_deliveries",
    "ai_business_file_runs")


def _role(cursor, *, closed):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid WHERE rolname=%s", [v6.ROLE])
    row = cursor.fetchone()
    if row is None or row[1:7] != (False,) * 6 or (closed and
            row != (False,) * 7 + (True,)):
        raise RuntimeError("0077 topology role attributes drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [v6.ROLE, v6.ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0077 topology role membership drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN "
        "pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) a WHERE n.nspname='public' "
        "AND (c.relname LIKE %s OR c.relname LIKE %s) "
        "AND a.grantee=%s::regrole", ["ai_%","protected_business_%",v6.ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0077 topology role gained direct table ACL")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a JOIN "
        "pg_catalog.pg_class c ON c.oid=a.attrelid JOIN "
        "pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) acl WHERE n.nspname='public' "
        "AND a.attnum>0 AND NOT a.attisdropped AND "
        "(c.relname LIKE %s OR c.relname LIKE %s) "
        "AND acl.grantee=%s::regrole",
        ["ai_%","protected_business_%",v6.ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0077 topology role gained direct column ACL")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_proc p JOIN "
        "pg_catalog.pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(p.proacl,"
        "pg_catalog.acldefault('f',p.proowner))) acl WHERE n.nspname='public' "
        "AND acl.grantee=%s::regrole AND p.proname NOT IN ("
        "'ai_market_v6_create_paused_topology',"
        "'ai_market_v6_cancel_paused_topology',"
        "'ai_market_v6_paused_topology_outcome')", [v6.ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0077 topology role gained unrelated function ACL")


def _owner(cursor):
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
        "pg_catalog.pg_class c WHERE c.oid="
        "'public.ai_workflow_runs'::regclass")
    row = cursor.fetchone()
    owner = row[0] if row else None
    if not owner or owner in {v6.ROLE, "teruisi_ai_reader",
            "teruisi_ai_writer"}:
        raise RuntimeError("0077 topology protected owner unavailable")
    for table in (*STATE_TABLES, *EFFECT_TABLES, *ANCILLARY_TABLES,
            "ai_report_runs",
            "ai_data_revisions", "ai_business_market_v2_execution_plans",
            "ai_business_market_v2_cost_ledger_candidates"):
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
            "pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)",
            ["public." + table])
        if cursor.fetchone() != (owner,):
            raise RuntimeError("0077 topology source owner drift: " + table)
    return owner


def _old_functions(cursor):
    from ai_assistant import business_market_v2_human_cap_sql as cap
    signatures = (
        "public.ai_market_v2_execution_workflow_guard()",
        "public.ai_market_v2_execution_report_guard()",
        "public.ai_market_v2_execution_orphan_guard()",
        "public.ai_market_v2_execution_child_guard()",
        "public.ai_market_v2_read_receipt_guard()",
        "public.ai_market_v2_read_claim(text)",
        "public.ai_market_v2_attest_read(text)",
        "public.ai_market_v2_read_receipt(text,text,bigint)",
        "public.ai_market_v2_create_synthetic_chain(text)",
        "public.ai_market_v2_synthetic_flow_guard()",
        "public.ai_market_v2_synthetic_report_guard()",
        "public.ai_market_v2_synthetic_child_guard()",
        "public.ai_market_v2_synthetic_orphan_guard()",
        cap.GUARD_SIG, cap.MODEL_SIG, cap.PREVIEW_SIG,
        cap.APPROVE_SIG, cap.REVOKE_SIG, cap.OUTCOME_SIG,
    )
    result = []
    for signature in signatures:
        cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef,"
            "proconfig FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("0077 frozen predecessor function missing")
        result.append((signature, row))
    return tuple(result)


def _predecessors(cursor):
    from importlib import import_module
    for module_name in (
            "ai_assistant.migrations.0074_business_market_v2_human_cap_approval",
            "ai_assistant.migrations.0076_business_promotion_budget_v11_ticket_bound_signer"):
        import_module(module_name).verify_catalog(cursor)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _predecessors(cursor)
        old = _old_functions(cursor)
        owner = _owner(cursor)
        quoted_owner = schema_editor.connection.ops.quote_name(owner)
        cursor.execute("SELECT to_regrole(%s)", [v6.ROLE])
        if cursor.fetchone() == (None,):
            cursor.execute("CREATE ROLE " + v6.ROLE + " NOLOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        # Empty reverse retains this closed cluster-global identity. It can
        # also be referenced by a separately restored test database.
        _role(cursor, closed=True)
        for statement in v6.CREATE_TABLES.split(";\n"):
            cursor.execute(statement)
        for table in TABLES:
            cursor.execute("REVOKE ALL ON " + table + " FROM PUBLIC")
            cursor.execute("ALTER TABLE " + table + " OWNER TO " + quoted_owner)
        definitions = (v6.ROW_GUARD_SQL, v6.STATE_GUARD_SQL,
            v6.EFFECT_GUARD_SQL, v6.ANCILLARY_GUARD_SQL,
            v6.CREATE_SQL, v6.CANCEL_SQL, v6.OUTCOME_SQL)
        for definition, signature in zip(definitions, v6.SIGNATURES):
            cursor.execute(definition)
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("ALTER FUNCTION " + signature + " OWNER TO " +
                quoted_owner)
        for signature in (v6.CREATE, v6.CANCEL, v6.OUTCOME):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + v6.ROLE)
        for table in TABLES:
            suffix = table.rsplit("_", 1)[-1]
            cursor.execute("CREATE TRIGGER ai_market_v6_" + suffix +
                "_immutable BEFORE INSERT OR UPDATE OR DELETE ON " + table +
                " FOR EACH ROW EXECUTE FUNCTION " + v6.ROW_GUARD)
            cursor.execute("CREATE TRIGGER ai_market_v6_" + suffix +
                "_no_truncate BEFORE TRUNCATE ON " + table +
                " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")
        for table in STATE_TABLES:
            cursor.execute("CREATE TRIGGER ai_market_v6_topology_state_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON public." + table +
                " FOR EACH ROW EXECUTE FUNCTION " + v6.STATE_GUARD)
        for table in EFFECT_TABLES:
            cursor.execute("CREATE TRIGGER ai_market_v6_topology_effect_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON public." + table +
                " FOR EACH ROW EXECUTE FUNCTION " + v6.EFFECT_GUARD)
        for table in ANCILLARY_TABLES:
            cursor.execute("CREATE TRIGGER ai_market_v6_topology_ancillary_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON public." + table +
                " FOR EACH ROW EXECUTE FUNCTION " + v6.ANCILLARY_GUARD)
        verify_catalog(cursor)
        if _old_functions(cursor) != old:
            raise RuntimeError("0077 changed frozen 0060/0062/0064/0074 SQL")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _role(cursor, closed=True)
        for table in TABLES:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone() != (False,):
                raise RuntimeError("0077 cannot discard paused topology history")
        old = _old_functions(cursor)
        for table in ANCILLARY_TABLES:
            cursor.execute("DROP TRIGGER ai_market_v6_topology_ancillary_guard "
                "ON public." + table)
        for table in EFFECT_TABLES:
            cursor.execute("DROP TRIGGER ai_market_v6_topology_effect_guard "
                "ON public." + table)
        for table in STATE_TABLES:
            cursor.execute("DROP TRIGGER ai_market_v6_topology_state_guard "
                "ON public." + table)
        for table in reversed(TABLES):
            suffix = table.rsplit("_", 1)[-1]
            cursor.execute("DROP TRIGGER ai_market_v6_" + suffix +
                "_no_truncate ON " + table)
            cursor.execute("DROP TRIGGER ai_market_v6_" + suffix +
                "_immutable ON " + table)
        for signature in reversed(v6.SIGNATURES):
            cursor.execute("DROP FUNCTION " + signature)
        for table in reversed(TABLES):
            cursor.execute("DROP TABLE " + table)
        _role(cursor, closed=True)
        if _old_functions(cursor) != old:
            raise RuntimeError("0077 reverse changed frozen predecessor SQL")


def verify_catalog(cursor):
    _role(cursor, closed=True)
    owner = _owner(cursor)
    for table in TABLES:
        cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)", [table])
        if cursor.fetchone() != ("r", owner):
            raise RuntimeError("0077 topology table owner/kind drift")
        cursor.execute("SELECT attname,atttypid::regtype::text,atttypmod,"
            "attnotnull FROM pg_catalog.pg_attribute "
            "WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped "
            "ORDER BY attnum", [table])
        if tuple(cursor.fetchall()) != tuple((*column, True) for column in
                EXPECTED_COLUMNS[table]):
            raise RuntimeError("0077 topology column inventory drift")
        cursor.execute("SELECT c.contype,ARRAY(SELECT a.attname FROM "
            "unnest(c.conkey) k(attnum) JOIN pg_catalog.pg_attribute a ON "
            "a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.attnum),"
            "CASE WHEN c.confrelid=0 THEN NULL ELSE "
            "c.confrelid::regclass::text END,c.convalidated,c.condeferrable,"
            "pg_catalog.pg_get_constraintdef(c.oid) "
            "FROM pg_catalog.pg_constraint c WHERE c.conrelid=%s::regclass",
            [table])
        constraints = cursor.fetchall()
        by_kind = {kind: [row for row in constraints if row[0] == kind]
            for kind in ("p", "u", "f", "c")}
        primary = {tuple(row[1]) for row in by_kind["p"]}
        unique = {tuple(row[1]) for row in by_kind["u"]}
        foreign = {tuple(row[1]): str(row[2]).split(".")[-1]
            for row in by_kind["f"]}
        if table == v6.TABLE:
            expected_unique = {("workflow_id",),
                ("owner_email", "client_request_id")}
            expected_foreign = {("report_id",): "ai_report_runs",
                ("workflow_id",): "ai_workflow_runs",
                ("source_report_id",): "ai_report_runs",
                ("source_plan_id",): "ai_business_market_v2_execution_plans",
                ("source_cost_id",): "ai_business_market_v2_cost_ledger_candidates"}
            expected_checks = {
                ("owner_version",): "CHECK ((owner_version >= 1))",
                ("request_digest",): "CHECK (((request_digest)::text ~ '^[0-9a-f]{64}$'::text))",
                ("snapshot_digest",): "CHECK (((snapshot_digest)::text ~ '^[0-9a-f]{64}$'::text))",
                ("graph_digest",): "CHECK (((graph_digest)::text ~ '^[0-9a-f]{64}$'::text))",
                ("graph_json",): "CHECK ((octet_length(graph_json) <= 32768))",
            }
        else:
            expected_unique = set()
            expected_foreign = {("report_id",):
                "protected_business_market_v6_topologies"}
            expected_checks = {
                ("request_digest",): "CHECK (((request_digest)::text ~ '^[0-9a-f]{64}$'::text))",
                ("reason_digest",): "CHECK (((reason_digest)::text ~ '^[0-9a-f]{64}$'::text))",
                ("expected_version",): "CHECK ((expected_version >= 1))",
            }
        expected_unique_defs = {"UNIQUE ("+", ".join(columns)+")"
            for columns in expected_unique}
        actual_unique_defs = {row[5] for row in by_kind["u"]}
        actual_fk = {tuple(row[1]): row[5] for row in by_kind["f"]}
        actual_checks = {tuple(row[1]): row[5] for row in by_kind["c"]}
        exact_fk = all(actual_fk.get(columns) in {
            "FOREIGN KEY ("+", ".join(columns)+") REFERENCES "+prefix+
            target+"("+("report_id" if table == v6.CANCEL_TABLE else "id")+
            ") ON DELETE RESTRICT"
            for prefix in ("", "public.")}
            for columns,target in expected_foreign.items())
        if primary != {("report_id",)} or unique != expected_unique:
            raise RuntimeError("0077 topology primary/unique key drift: " +
                repr((table,primary,unique)))
        if foreign != expected_foreign or not exact_fk:
            raise RuntimeError("0077 topology foreign key binding drift: " +
                repr((table,foreign,actual_fk)))
        if actual_checks != expected_checks:
            raise RuntimeError("0077 topology exact check expression drift: " +
                repr((table,actual_checks)))
        if ({row[5] for row in by_kind["p"]} != {"PRIMARY KEY (report_id)"}
                or actual_unique_defs != expected_unique_defs):
            raise RuntimeError("0077 topology exact primary/unique definition "
                "drift: " + repr((table,actual_unique_defs)))
        if (len(constraints) != 1 + len(expected_unique) +
                len(expected_foreign) + len(expected_checks)
                or any(row[3:5] != (True, False) for row in constraints)):
            raise RuntimeError("0077 topology constraint count/validity drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c CROSS JOIN "
            "LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
            "pg_catalog.acldefault('r',c.relowner))) a WHERE c.oid=%s::regclass "
            "AND a.grantee<>c.relowner", [table])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0077 topology table ACL drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a JOIN "
            "pg_catalog.pg_class c ON c.oid=a.attrelid CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(a.attacl,"
            "pg_catalog.acldefault('c',c.relowner))) acl WHERE "
            "c.oid=%s::regclass AND a.attnum>0 AND acl.grantee<>c.relowner",
            [table])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0077 topology column ACL drift")
        suffix = table.rsplit("_", 1)[-1]
        expected = {
            "ai_market_v6_" + suffix + "_immutable": (31, v6.ROW_GUARD),
            "ai_market_v6_" + suffix + "_no_truncate": (34,
                "public.ai_v4_seal_ticket_no_truncate()"),
        }
        cursor.execute("SELECT tgname,tgenabled,tgtype,tgqual IS NULL,"
            "tgfoid,tgdeferrable,tginitdeferred FROM pg_catalog.pg_trigger "
            "WHERE tgrelid=%s::regclass AND NOT tgisinternal ORDER BY tgname",
            [table])
        triggers = cursor.fetchall()
        if len(triggers) != 2 or {row[0] for row in triggers} != set(expected):
            raise RuntimeError("0077 protected trigger inventory drift")
        for name, enabled, event, no_when, oid, deferred, initial in triggers:
            event_expected, signature = expected[name]
            cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
            if (enabled,event,no_when,oid,deferred,initial) != (
                    "O",event_expected,True,cursor.fetchone()[0],False,False):
                raise RuntimeError("0077 protected trigger binding drift")
    for table, trigger_name, signature in (
            *((name, "ai_market_v6_topology_state_guard",v6.STATE_GUARD)
              for name in STATE_TABLES),
            *((name, "ai_market_v6_topology_effect_guard",v6.EFFECT_GUARD)
              for name in EFFECT_TABLES),
            *((name, "ai_market_v6_topology_ancillary_guard",v6.ANCILLARY_GUARD)
              for name in ANCILLARY_TABLES)):
        cursor.execute("SELECT tgenabled,tgtype,tgqual IS NULL,tgfoid,"
            "tgdeferrable,tginitdeferred FROM pg_catalog.pg_trigger WHERE "
            "tgrelid=%s::regclass AND tgname=%s AND NOT tgisinternal",
            ["public."+table, trigger_name])
        row = cursor.fetchone()
        cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
        if row != ("O",31,True,cursor.fetchone()[0],False,False):
            raise RuntimeError("0077 core closed-effect trigger drift")
    for table in (*STATE_TABLES, *EFFECT_TABLES, *ANCILLARY_TABLES,
            "ai_report_runs",
            "ai_data_revisions"):
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [v6.ROLE, "public."+table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0077 topology role gained direct core DML")
    definitions = (v6.ROW_GUARD_SQL, v6.STATE_GUARD_SQL,
        v6.EFFECT_GUARD_SQL, v6.ANCILLARY_GUARD_SQL,
        v6.CREATE_SQL, v6.CANCEL_SQL, v6.OUTCOME_SQL)
    for definition, signature in zip(definitions, v6.SIGNATURES):
        cursor.execute("SELECT prosrc,prosecdef,proconfig,"
            "pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] != (signature != v6.ROW_GUARD)
                or {item.replace(" ", "") for item in row[2] or []}
                    != {"search_path=pg_catalog,public"}
                or row[3] != owner):
            raise RuntimeError("0077 topology function body/owner drift")
        cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a WHERE "
            "p.oid=to_regprocedure(%s) AND a.grantee<>p.proowner "
            "ORDER BY 1,2,3", [signature])
        expected = [(v6.ROLE,"EXECUTE",False)] if signature in {
            v6.CREATE,v6.CANCEL,v6.OUTCOME} else []
        if cursor.fetchall() != expected:
            raise RuntimeError("0077 topology function ACL drift")
    _predecessors(cursor)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0076_business_promotion_budget_v11_ticket_bound_signer")]
    operations = [migrations.RunPython(install, uninstall)]
