"""Closed v11 signed-publication sidecar and independent read-only chunk.

The old renderer-11 paused/ready guards and public download rejection remain
byte-for-byte unchanged. No service credential or private key is installed.
"""
from importlib import import_module

from django.db import migrations

from ai_assistant import business_promotion_budget_v11_publication_sql as v2


TABLE=v2.TABLE
GUARD_TRIGGER="ai_budget_v11_publication_v2_guard"
TRUNCATE_TRIGGER="ai_budget_v11_publication_v2_no_truncate"


def _frozen(cursor):
    prior=import_module(
        "ai_assistant.migrations.0077_business_market_v6_paused_topology")
    sign=import_module(
        "ai_assistant.migrations.0076_business_promotion_budget_v11_ticket_bound_signer")
    prior.verify_catalog(cursor)
    sign.verify_catalog(cursor)
    stage=import_module("ai_assistant.business_promotion_budget_v11_stage_sql")
    stage.verify_catalog(cursor)
    old=import_module(
        "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
    reader=import_module(
        "ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence")
    signatures=("public.ai_business_files_guard()",
        "public.ai_business_volume_complete_guard()",
        *v2.v3.SIGNATURES,old.PUBLISH_SIGNATURE,old.OUTCOME_SIGNATURE,
        reader.READ_SIGNATURE)
    result=[]
    for signature in signatures:
        cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef,"
            "proconfig FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        row=cursor.fetchone()
        if row is None:
            raise RuntimeError("0078 requires exact signed predecessor")
        result.append((signature,row))
    return tuple(result)


def _role(cursor,name,*,create=False,allow_test_login=False):
    cursor.execute("SELECT to_regrole(%s)",[name])
    if cursor.fetchone()==(None,) and create:
        cursor.execute("CREATE ROLE "+name+" NOLOGIN NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid WHERE rolname=%s",[name])
    flags=cursor.fetchone()
    default=(False,)*7+(True,)
    active=(True,)+(False,)*6+(False,)
    if flags!=default:
        if not allow_test_login or flags!=active:
            raise RuntimeError("0078 role must remain NOLOGIN/password NULL")
        cursor.execute("SELECT current_database(),inet_server_port(),"
            "inet_server_addr()::text")
        database,port,address=cursor.fetchone()
        if (database not in ("teruisi_ai_rehearsal",
                "test_teruisi_ai_rehearsal") or
                port is None or not 55440<=port<=55999 or
                address not in ("127.0.0.1","127.0.0.1/32","::1","::1/128")):
            raise RuntimeError("0078 activated role outside isolated test DB")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole",[name,name])
    if cursor.fetchone()!=(0,):
        raise RuntimeError("0078 role membership drift")


def install(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql": return
    with schema_editor.connection.cursor() as cursor:
        frozen=_frozen(cursor)
        _role(cursor,v2.PUBLISH_ROLE)
        _role(cursor,v2.READ_ROLE,create=True)
        cursor.execute("SELECT current_user")
        installer=cursor.fetchone()[0]
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
            "pg_catalog.pg_class c WHERE c.oid="
            "'public.ai_business_promotion_budget_v11_attestations'::regclass")
        owner=cursor.fetchone()[0]
        if owner in (None,v2.PUBLISH_ROLE,v2.READ_ROLE,v2.v3.KEY_OWNER,
                "teruisi_ai_reader","teruisi_ai_writer"):
            raise RuntimeError("0078 protected owner drift")
        quoted_owner=schema_editor.connection.ops.quote_name(owner)
        quoted_installer=schema_editor.connection.ops.quote_name(installer)
        cursor.execute("GRANT USAGE ON SCHEMA public TO "+v2.READ_ROLE)
        cursor.execute(v2.CREATE_TABLE)
        cursor.execute("REVOKE ALL ON "+TABLE+" FROM PUBLIC")
        cursor.execute("ALTER TABLE "+TABLE+" OWNER TO "+quoted_owner)
        definitions=(v2.GUARD_SQL,v2.PUBLISH_REQUIREMENTS_SQL,
            v2.PUBLISH_PAGE_SQL,v2.PUBLISH_ROOT_SQL,
            v2.READ_REQUIREMENTS_SQL,v2.READ_PAGE_SQL,v2.READ_ROOT_SQL,
            v2.ro_mac_sql(),v2.PUBLISH_SQL,v2.OUTCOME_SQL,v2.CHUNK_SQL)
        if len(definitions)!=len(v2.SIGNATURES):
            raise RuntimeError("0078 function inventory mismatch")
        for statement,signature in zip(definitions,v2.SIGNATURES):
            cursor.execute(statement)
            cursor.execute("REVOKE ALL ON FUNCTION "+signature+" FROM PUBLIC")
            if signature!=v2.RO_MAC:
                cursor.execute("ALTER FUNCTION "+signature+" OWNER TO "+
                    quoted_owner)
        cursor.execute("GRANT "+v2.v3.KEY_OWNER+" TO "+quoted_installer)
        cursor.execute("ALTER FUNCTION "+v2.RO_MAC+" OWNER TO "+v2.v3.KEY_OWNER)
        cursor.execute("GRANT EXECUTE ON FUNCTION "+v2.RO_MAC+
            " TO "+quoted_owner)
        cursor.execute("REVOKE "+v2.v3.KEY_OWNER+" FROM "+quoted_installer)
        for signature in (v2.PUBLISH,v2.OUTCOME):
            cursor.execute("GRANT EXECUTE ON FUNCTION "+signature+
                " TO "+v2.PUBLISH_ROLE)
        cursor.execute("GRANT EXECUTE ON FUNCTION "+v2.CHUNK+
            " TO "+v2.READ_ROLE)
        cursor.execute("CREATE TRIGGER "+GUARD_TRIGGER+
            " BEFORE INSERT OR UPDATE OR DELETE ON "+TABLE+
            " FOR EACH ROW EXECUTE FUNCTION "+v2.GUARD)
        cursor.execute("CREATE TRIGGER "+TRUNCATE_TRIGGER+
            " BEFORE TRUNCATE ON "+TABLE+
            " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)
        if _frozen(cursor)!=frozen:
            raise RuntimeError("0078 changed frozen ready/download/signer SQL")


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor!="postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM "+TABLE+")")
        if cursor.fetchone()!=(False,):
            raise RuntimeError("0078 cannot discard signed publications")
        _role(cursor,v2.PUBLISH_ROLE)
        _role(cursor,v2.READ_ROLE)
        frozen=_frozen(cursor)
        cursor.execute("DROP TRIGGER "+TRUNCATE_TRIGGER+" ON "+TABLE)
        cursor.execute("DROP TRIGGER "+GUARD_TRIGGER+" ON "+TABLE)
        for signature in reversed(v2.SIGNATURES):
            cursor.execute("DROP FUNCTION "+signature)
        cursor.execute("DROP TABLE "+TABLE)
        cursor.execute("REVOKE USAGE ON SCHEMA public FROM "+v2.READ_ROLE)
        if _frozen(cursor)!=frozen:
            raise RuntimeError("0078 reverse changed frozen predecessor")
        # Keep the empty global NOLOGIN role for independent restored DBs.


def verify_catalog(cursor,*,allow_test_login=False):
    _role(cursor,v2.PUBLISH_ROLE,allow_test_login=allow_test_login)
    _role(cursor,v2.READ_ROLE,allow_test_login=allow_test_login)
    for role in (v2.PUBLISH_ROLE,v2.READ_ROLE):
        cursor.execute("SELECT has_schema_privilege(%s,'public','USAGE'),"
            "has_schema_privilege(%s,'public','CREATE'),"
            "has_database_privilege(%s,current_database(),'CREATE')",
            [role,role,role])
        if cursor.fetchone()!=(True,False,False):
            raise RuntimeError("0078 role schema/database privilege drift")
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)",[TABLE])
    table=cursor.fetchone()
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
        "pg_catalog.pg_class c WHERE c.oid="
        "'public.ai_business_promotion_budget_v11_attestations'::regclass")
    predecessor=cursor.fetchone()
    if table!=("r",predecessor[0]):
        raise RuntimeError("0078 publication table owner drift")
    owner=table[1]
    cursor.execute("SELECT attname,atttypid::regtype::text,atttypmod,"
        "attnotnull FROM pg_catalog.pg_attribute WHERE "
        "attrelid=%s::regclass AND attnum>0 AND NOT attisdropped "
        "ORDER BY attnum",[TABLE])
    columns=(("id","uuid",-1,True),
        ("run_id","character varying",164,True),
        ("attempt","integer",-1,True),
        ("report_id","character varying",164,True),
        ("owner_email","character varying",324,True),
        ("run_version","bigint",-1,True),
        ("signed_receipt_id","uuid",-1,True),
        ("signed_receipt_sha256","character varying",68,True),
        ("ticket_id","uuid",-1,True),
        ("claim_id","uuid",-1,True),
        ("binding_digest","character varying",68,True),
        ("ledger_root","character varying",68,True),
        ("file_page_root","character varying",68,True),
        ("key_id","character varying",68,True),
        ("request_digest","character varying",68,True),
        ("published_at","timestamp with time zone",-1,True))
    if tuple(cursor.fetchall())!=columns:
        raise RuntimeError("0078 publication column contract drift")
    cursor.execute("SELECT c.contype,ARRAY(SELECT a.attname FROM "
        "unnest(c.conkey) k(attnum) JOIN pg_catalog.pg_attribute a ON "
        "a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.attnum),"
        "CASE WHEN c.confrelid=0 THEN NULL ELSE "
        "c.confrelid::regclass::text END,c.convalidated,c.condeferrable,"
        "pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c "
        "WHERE c.conrelid=%s::regclass",[TABLE])
    constraints=cursor.fetchall()
    try:
        v2.validate_constraints(constraints)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c CROSS JOIN "
        "LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) a WHERE "
        "c.oid=%s::regclass AND a.grantee<>c.relowner",[TABLE])
    if cursor.fetchone()!=(0,):
        raise RuntimeError("0078 publication table ACL drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a JOIN "
        "pg_catalog.pg_class c ON c.oid=a.attrelid CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) acl WHERE "
        "c.oid=%s::regclass AND a.attnum>0 AND acl.grantee<>c.relowner",
        [TABLE])
    if cursor.fetchone()!=(0,):
        raise RuntimeError("0078 publication column ACL drift")
    cursor.execute("SELECT tgname,tgenabled,tgtype,tgqual IS NULL,tgfoid,"
        "tgdeferrable,tginitdeferred FROM pg_catalog.pg_trigger WHERE "
        "tgrelid=%s::regclass AND NOT tgisinternal ORDER BY tgname",[TABLE])
    triggers=cursor.fetchall()
    expected={GUARD_TRIGGER:(31,v2.GUARD),
        TRUNCATE_TRIGGER:(34,"public.ai_v4_seal_ticket_no_truncate()")}
    if len(triggers)!=2 or {row[0] for row in triggers}!=set(expected):
        raise RuntimeError("0078 publication trigger inventory drift")
    for name,enabled,event,no_when,oid,deferred,initial in triggers:
        kind,signature=expected[name]
        cursor.execute("SELECT to_regprocedure(%s)::oid",[signature])
        if (enabled,event,no_when,oid,deferred,initial)!=(
                "O",kind,True,cursor.fetchone()[0],False,False):
            raise RuntimeError("0078 publication trigger binding drift")
    definitions=(v2.GUARD_SQL,v2.PUBLISH_REQUIREMENTS_SQL,
        v2.PUBLISH_PAGE_SQL,v2.PUBLISH_ROOT_SQL,
        v2.READ_REQUIREMENTS_SQL,v2.READ_PAGE_SQL,v2.READ_ROOT_SQL,
        v2.ro_mac_sql(),v2.PUBLISH_SQL,v2.OUTCOME_SQL,v2.CHUNK_SQL)
    for statement,signature in zip(definitions,v2.SIGNATURES):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)",[signature])
        row=cursor.fetchone()
        expected_owner=v2.v3.KEY_OWNER if signature==v2.RO_MAC else owner
        if (row is None or row[0]!=statement.split("$$",2)[1]
                or row[1]!=(signature!=v2.GUARD)
                or {x.replace(" ","") for x in row[2] or []}!=
                    {"search_path=pg_catalog,public"}
                or row[3]!=expected_owner):
            raise RuntimeError("0078 publication function body/owner drift")
        cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a WHERE "
            "p.oid=to_regprocedure(%s) AND a.grantee<>p.proowner "
            "ORDER BY 1,2,3",[signature])
        grantee=(owner if signature==v2.RO_MAC else
            v2.PUBLISH_ROLE if signature in (v2.PUBLISH,v2.OUTCOME) else
            v2.READ_ROLE if signature==v2.CHUNK else None)
        acl=[] if grantee is None else [(grantee,"EXECUTE",False)]
        if cursor.fetchall()!=acl:
            raise RuntimeError("0078 publication function ACL drift")
    for role in (v2.READ_ROLE,v2.PUBLISH_ROLE,"teruisi_ai_reader",
            "teruisi_ai_writer","teruisi_ai_budget_v11_sign_login"):
        for privilege in ("SELECT","INSERT","UPDATE","DELETE",
                "TRUNCATE","REFERENCES","TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [role,TABLE,privilege])
            if cursor.fetchone()!=(False,):
                raise RuntimeError("0078 publication direct table ACL drift")
    for role in (v2.READ_ROLE,v2.PUBLISH_ROLE):
        for table in (v2.v3.TABLE,
                "public.protected_business_budget_v11_verifier_keys",
                "public.ai_business_volume_chunks"):
            cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                [role,table])
            if cursor.fetchone()!=(False,):
                raise RuntimeError("0078 role gained direct secret/file read")


class Migration(migrations.Migration):
    dependencies=[("ai_assistant","0077_business_market_v6_paused_topology")]
    operations=[migrations.RunPython(install,uninstall)]
