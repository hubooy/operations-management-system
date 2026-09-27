"""Default-closed v11 ticket/claim-bound signer and bounded ledger sidecar.

No key is installed, no service role is activated, and renderer 11 ready and
download guards are deliberately unchanged. All predecessor SQL is frozen.
"""
from importlib import import_module

from django.db import migrations

from ai_assistant import business_promotion_budget_v11_ticket_sign_sql as v3


MIGRATION = "0076_business_promotion_budget_v11_ticket_bound_signer"
GUARD_TRIGGER = "ai_budget_v11_signed_v3_guard"
TRUNCATE_TRIGGER = "ai_budget_v11_signed_v3_no_truncate"


def _old_catalog(cursor):
    modules = tuple(import_module("ai_assistant.migrations." + name) for name in (
        "0068_business_promotion_budget_v11_verifier_receipt",
        "0070_business_promotion_budget_v11_limited_identity",
        "0073_business_promotion_budget_v11_login_attestation"))
    for module in modules:
        module.verify_catalog(cursor)
    old, identities, login = modules
    signatures = (old.MAC_SIGNATURE, old.VERIFY_SIGNATURE,
        identities.v2.ISSUE, identities.v2.READ, identities.v2.VERIFY,
        *login.v2.SIGNATURES)
    frozen = []
    for signature in signatures:
        cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef,"
            "proconfig FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("0076 requires exact protected predecessor")
        frozen.append((signature, row))
    return tuple(frozen)


def _closed_sign_role(cursor):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid WHERE rolname=%s", [v3.SIGN])
    if cursor.fetchone() != (False,) * 7 + (True,):
        raise RuntimeError("0076 signer must start NOLOGIN without password")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [v3.SIGN, v3.SIGN])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0076 signer membership drift")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        frozen = _old_catalog(cursor)
        _closed_sign_role(cursor)
        cursor.execute("SELECT current_user")
        installer = cursor.fetchone()[0]
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c WHERE c.oid="
            "'public.ai_business_promotion_budget_v11_attestations'::regclass")
        owner = cursor.fetchone()[0]
        if not owner or owner in {v3.SIGN,v3.KEY_OWNER,
                "teruisi_ai_reader","teruisi_ai_writer"}:
            raise RuntimeError("0076 protected owner drift")
        quoted_owner = schema_editor.connection.ops.quote_name(owner)
        quoted_installer = schema_editor.connection.ops.quote_name(installer)
        cursor.execute(v3.CREATE_TABLE)
        cursor.execute("REVOKE ALL ON " + v3.TABLE + " FROM PUBLIC")
        cursor.execute("ALTER TABLE " + v3.TABLE + " OWNER TO " + quoted_owner)
        definitions = (v3.GUARD_SQL, v3.REQUIREMENTS_SQL,
            v3.PAGE_CORE_SQL, v3.LEDGER_ROOT_SQL, v3.INVENTORY_SQL,
            v3.READ_SQL, v3.private_mac_sql(),
            v3.RECORD_SQL, v3.OUTCOME_SQL)
        for statement, signature in zip(definitions, v3.SIGNATURES):
            cursor.execute(statement)
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        for signature in (v3.GUARD, v3.REQUIREMENTS, v3.PAGE_CORE,
                v3.LEDGER_ROOT,
                v3.INVENTORY, v3.READ, v3.RECORD, v3.OUTCOME):
            cursor.execute("ALTER FUNCTION " + signature + " OWNER TO " +
                quoted_owner)
        cursor.execute("GRANT " + v3.KEY_OWNER + " TO " + quoted_installer)
        cursor.execute("ALTER FUNCTION " + v3.PRIVATE_MAC + " OWNER TO " +
            v3.KEY_OWNER)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + v3.PRIVATE_MAC +
            " TO " + quoted_owner)
        cursor.execute("REVOKE " + v3.KEY_OWNER + " FROM " + quoted_installer)
        for signature in (v3.REQUIREMENTS,v3.INVENTORY,v3.READ,
                v3.RECORD,v3.OUTCOME):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + v3.SIGN)
        cursor.execute("CREATE TRIGGER " + GUARD_TRIGGER +
            " BEFORE INSERT OR UPDATE OR DELETE ON " + v3.TABLE +
            " FOR EACH ROW EXECUTE FUNCTION " + v3.GUARD)
        cursor.execute("CREATE TRIGGER " + TRUNCATE_TRIGGER +
            " BEFORE TRUNCATE ON " + v3.TABLE +
            " FOR EACH STATEMENT EXECUTE FUNCTION " +
            "public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)
        if _old_catalog(cursor) != frozen:
            raise RuntimeError("0076 changed frozen 0068/0070/0073 catalog")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + v3.TABLE + ")")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0076 cannot discard signed receipts")
        _closed_sign_role(cursor)
        frozen = _old_catalog(cursor)
        cursor.execute("DROP TRIGGER " + TRUNCATE_TRIGGER + " ON " + v3.TABLE)
        cursor.execute("DROP TRIGGER " + GUARD_TRIGGER + " ON " + v3.TABLE)
        for signature in reversed(v3.SIGNATURES):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + v3.TABLE)
        if _old_catalog(cursor) != frozen:
            raise RuntimeError("0076 reverse changed protected predecessor")


def verify_catalog(cursor):
    """Default-closed exact SQL-only inventory; never admits activated roles."""
    _closed_sign_role(cursor)
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)", [v3.TABLE])
    table = cursor.fetchone()
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid="
        "'public.ai_business_promotion_budget_v11_attestations'::regclass")
    old_owner = cursor.fetchone()
    if table != ("r", old_owner[0]):
        raise RuntimeError("0076 signed receipt table owner drift")
    owner = table[1]
    cursor.execute("SELECT attname,atttypid::regtype::text,atttypmod,attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid=%s::regclass AND "
        "attnum>0 AND NOT attisdropped ORDER BY attnum", [v3.TABLE])
    expected_columns = (
        ("id","uuid",-1,True),
        ("ticket_id","uuid",-1,True),
        ("claim_id","uuid",-1,True),
        ("run_id","character varying",164,True),
        ("attempt","integer",-1,True),
        ("report_id","character varying",164,True),
        ("owner_email","character varying",324,True),
        ("run_version","bigint",-1,True),
        ("binding_digest","character varying",68,True),
        ("old_attestation_id","character varying",68,True),
        ("login_attestation_id","character varying",68,True),
        ("attestation_sha256","character varying",68,True),
        ("ledger_root","character varying",68,True),
        ("file_page_root","character varying",68,True),
        ("key_id","character varying",68,True),
        ("receipt_sha256","character varying",68,True),
        ("receipt_text","text",-1,True),
        ("receipt_mac","character varying",68,True),
        ("recorded_at","timestamp with time zone",-1,True))
    if tuple(cursor.fetchall()) != expected_columns:
        raise RuntimeError("0076 signed receipt column contract drift")
    cursor.execute("SELECT c.contype,pg_catalog.pg_get_constraintdef(c.oid),"
        "ARRAY(SELECT a.attname FROM unnest(c.conkey) AS keys(attnum) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid "
        "AND a.attnum=keys.attnum ORDER BY keys.attnum),"
        "CASE WHEN c.confrelid=0 THEN NULL ELSE c.confrelid::regclass::text END "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid=%s::regclass",
        [v3.TABLE])
    constraints=cursor.fetchall()
    if sorted(row[0] for row in constraints) != ["c"]*8+["f"]*4+["p"]+["u"]*4:
        raise RuntimeError("0076 signed receipt constraints drift")
    keys={(kind,tuple(columns)):definition for kind,definition,columns,_
        in constraints}
    if (keys.get(("p",("id",)))!="PRIMARY KEY (id)" or
            keys.get(("u",("ticket_id",)))!="UNIQUE (ticket_id)" or
            keys.get(("u",("claim_id",)))!="UNIQUE (claim_id)" or
            keys.get(("u",("receipt_sha256",)))!=
                "UNIQUE (receipt_sha256)" or
            keys.get(("u",("run_id","attempt")))!=
                "UNIQUE (run_id, attempt)"):
        raise RuntimeError("0076 signed receipt exact unique keys drift")
    foreign={tuple(columns):(definition,target) for kind,definition,columns,target
        in constraints if kind=="f"}
    for column,target in (("ticket_id","protected_business_budget_v11_proof_tickets"),
            ("claim_id","protected_business_budget_v11_proof_ticket_claims"),
            ("run_id","ai_business_file_runs"),
            ("report_id","ai_report_runs")):
        definition,actual=foreign.get((column,),(None,None))
        expected_fk={"FOREIGN KEY ("+column+") REFERENCES "+
            prefix+target+"(id) ON DELETE RESTRICT"
            for prefix in ("","public.")}
        if actual not in (target,"public."+target) or definition not in expected_fk:
            raise RuntimeError("0076 signed receipt foreign key drift")
    checks={tuple(columns):definition for kind,definition,columns,_ in
        constraints if kind=="c"}
    expected_checks={
        "attempt":"CHECK (((attempt >= 1) AND (attempt <= 5)))",
        "run_version":"CHECK ((run_version >= 1))"}
    for column in ("binding_digest","attestation_sha256","ledger_root",
            "file_page_root","receipt_sha256","receipt_mac"):
        expected_checks[column]=(
            "CHECK ((("+column+")::text ~ '^[0-9a-f]{64}$'::text))")
    for column,expected in expected_checks.items():
        if checks.get((column,))!=expected:
            raise RuntimeError("0076 signed receipt check drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c CROSS JOIN "
        "LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND acl.grantee<>c.relowner", [v3.TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0076 signed receipt table ACL drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a JOIN "
        "pg_catalog.pg_class c ON c.oid=a.attrelid CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND a.attnum>0 AND "
        "acl.grantee<>c.relowner", [v3.TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0076 signed receipt column ACL drift")
    cursor.execute("SELECT tgname,tgenabled,tgfoid,tgtype,tgdeferrable,"
        "tginitdeferred,tgqual IS NULL FROM pg_catalog.pg_trigger WHERE "
        "tgrelid=%s::regclass AND NOT tgisinternal ORDER BY tgname", [v3.TABLE])
    triggers = cursor.fetchall()
    expected_triggers = {
        GUARD_TRIGGER:(v3.GUARD,31),
        TRUNCATE_TRIGGER:("public.ai_v4_seal_ticket_no_truncate()",34)}
    if len(triggers) != 2 or {row[0] for row in triggers} != set(expected_triggers):
        raise RuntimeError("0076 signed receipt trigger inventory drift")
    for name, enabled, oid, trigger_type, deferred, initial, no_when in triggers:
        signature, expected_type = expected_triggers[name]
        cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
        if (enabled, oid, trigger_type, deferred, initial, no_when) != (
                "O", cursor.fetchone()[0], expected_type, False, False, True):
            raise RuntimeError("0076 signed receipt trigger binding drift")
    definitions = (v3.GUARD_SQL, v3.REQUIREMENTS_SQL,
        v3.PAGE_CORE_SQL, v3.LEDGER_ROOT_SQL, v3.INVENTORY_SQL,
        v3.READ_SQL, v3.private_mac_sql(),
        v3.RECORD_SQL, v3.OUTCOME_SQL)
    for statement, signature in zip(definitions, v3.SIGNATURES):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        expected_owner = v3.KEY_OWNER if signature == v3.PRIVATE_MAC else owner
        if (row is None or row[0] != statement.split("$$",2)[1]
                or row[1] != (signature != v3.GUARD)
                or row[2] != ["search_path=pg_catalog, public"] and
                    row[2] != ["search_path=pg_catalog,public"]
                or row[3] != expected_owner):
            raise RuntimeError("0076 signed receipt function drift")
        cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a WHERE "
            "p.oid=to_regprocedure(%s) AND a.grantee<>p.proowner "
            "ORDER BY 1,2,3", [signature])
        expected_grantee=(owner if signature==v3.PRIVATE_MAC else v3.SIGN
            if signature in {v3.REQUIREMENTS,v3.INVENTORY,v3.READ,
                v3.RECORD,v3.OUTCOME} else None)
        expected_acl=[] if expected_grantee is None else [
            (expected_grantee,"EXECUTE",False)]
        if cursor.fetchall()!=expected_acl:
            raise RuntimeError("0076 signed receipt effective function ACL drift")
    for role in (v3.SIGN,"teruisi_ai_reader","teruisi_ai_writer",
            "teruisi_ai_budget_v11_attestor_v2_login",
            "teruisi_ai_budget_v11_publish_login"):
        for privilege in ("SELECT","INSERT","UPDATE","DELETE",
                "TRUNCATE","REFERENCES","TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [role,v3.TABLE,privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0076 signed receipt role table ACL drift")
        for privilege in ("SELECT","INSERT","UPDATE","REFERENCES"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [role,v3.TABLE,privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0076 signed receipt role column ACL drift")
        for signature in v3.SIGNATURES:
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role,signature])
            allowed = role == v3.SIGN and signature in {
                v3.REQUIREMENTS,v3.INVENTORY,v3.READ,v3.RECORD,v3.OUTCOME}
            if cursor.fetchone() != (allowed,):
                raise RuntimeError("0076 signed receipt function ACL drift")
    for table in ("ai_agent_provider_dispatches",
            "ai_agent_provider_results","ai_agent_tool_dispatches",
            "ai_agent_tool_results","protected_business_budget_v11_verifier_keys"):
        for role in (v3.SIGN,"teruisi_ai_reader","teruisi_ai_writer"):
            if table == "protected_business_budget_v11_verifier_keys" or (
                    role in (v3.SIGN,"teruisi_ai_reader")):
                cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                    [role,"public."+table])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("0076 direct protected read was widened")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0075_business_v4_report_restricted_page")]
    operations = [migrations.RunPython(install, uninstall)]
