"""Default-closed, independent LOGIN-capable v11 attestation sidecar.

No credential, route, proof-ticket bridge, ready transition or download is
created. The new identity starts NOLOGIN; old 0067/0068/0070 objects freeze.
"""

from importlib import import_module

from django.db import migrations

from ai_assistant import business_promotion_budget_v11_login_attestation_sql as v2


ROLE = v2.ROLE
TABLE = v2.TABLE
GUARD_TRIGGER = "ai_budget_v11_login_attestation_guard"
TRUNCATE_TRIGGER = "ai_budget_v11_login_attestation_no_truncate"


CREATE_TABLE = """CREATE TABLE public.protected_business_budget_v11_login_attestations (
  id varchar(64) PRIMARY KEY,
  run_id varchar(160) NOT NULL REFERENCES public.ai_business_file_runs(id)
    ON DELETE RESTRICT,
  attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
  report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
    ON DELETE RESTRICT,
  owner_email varchar(320) NOT NULL,
  binding_digest varchar(64) NOT NULL,
  compact_json_sha256 varchar(64) NOT NULL,
  full_manifest_sha256 varchar(64) NOT NULL,
  full_manifest_digest varchar(64) NOT NULL,
  file_descriptors_json text NOT NULL,
  file_descriptors_digest varchar(64) NOT NULL,
  approved_content_digest varchar(64) NOT NULL,
  human_review_digest varchar(64) NOT NULL,
  budget_present boolean NOT NULL,
  budget_plan_digest varchar(64),
  budget_proof_digest varchar(64) NOT NULL,
  slim_proof_digest varchar(64) NOT NULL,
  file_byte_verification_digest varchar(64) NOT NULL,
  html_rows_digest varchar(64) NOT NULL,
  xlsx_opc_formula_digest varchar(64) NOT NULL,
  owning_verification_digest varchar(64) NOT NULL,
  attestation_sha256 varchar(64) NOT NULL UNIQUE,
  attestation_json text NOT NULL,
  recorded_at timestamptz NOT NULL,
  CONSTRAINT ai_budget_v11_login_attest_attempt_uq UNIQUE (run_id,attempt)
)"""


def _old_modules():
    return tuple(import_module("ai_assistant.migrations." + name) for name in (
        "0067_business_promotion_budget_v11_attestation",
        "0068_business_promotion_budget_v11_verifier_receipt",
        "0070_business_promotion_budget_v11_limited_identity"))


def _frozen(cursor):
    old, protected, identity = _old_modules()
    from ai_assistant import business_promotion_budget_v11_stage_sql as stage
    for module in (old, protected, identity):
        module.verify_catalog(cursor)
    stage.verify_catalog(cursor)
    signatures = (*stage.FILE_SIGNATURES, stage.STAGE_SIGNATURE,
        "public.ai_budget_v11_attestation_guard()",
        old.REQUIREMENTS_SIGNATURE, old.SIGNATURE,
        "public.ai_budget_v11_key_guard()",
        protected.MAC_SIGNATURE, protected.VERIFY_SIGNATURE,
        "public.ai_budget_v11_proof_ticket_row_guard()",
        identity.v2.ISSUE, identity.v2.READ, identity.v2.VERIFY)
    frozen = []
    for signature in signatures:
        cursor.execute("SELECT p.oid,p.prosrc,p.proacl::text,p.proowner,"
            "p.prosecdef,p.proconfig FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("0073 requires exact 0067/0068/0070 predecessor")
        frozen.append((signature, row))
    return tuple(frozen)


def _role(cursor):
    cursor.execute("SELECT to_regrole(%s)", [ROLE])
    if cursor.fetchone()[0] is None:
        cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [ROLE])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0073 identity must start NOLOGIN and unprivileged")
    cursor.execute("SELECT rolpassword IS NULL FROM pg_catalog.pg_authid "
        "WHERE rolname=%s", [ROLE])
    if cursor.fetchone() != (True,):
        raise RuntimeError("0073 NOLOGIN identity must have no password")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
        "WHERE roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0073 identity must have no memberships")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        frozen = _frozen(cursor)
        _role(cursor)
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        cursor.execute(CREATE_TABLE)
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        definitions = (v2.guard_sql(), v2.requirements_sql(),
            v2.attest_sql(), v2.OUTCOME_SQL)
        for definition, signature in zip(definitions, v2.SIGNATURES):
            cursor.execute(definition)
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            if signature in (v2.ATTEST_SIGNATURE, v2.OUTCOME_SIGNATURE):
                cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                    " TO " + ROLE)
        old, _, _ = _old_modules()
        cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass", [old.TABLE])
        owner = cursor.fetchone()[0]
        if owner is None:
            raise RuntimeError("0073 frozen 0067 owner unavailable")
        quoted_owner = schema_editor.connection.ops.quote_name(owner)
        cursor.execute("ALTER TABLE " + TABLE + " OWNER TO " + quoted_owner)
        for signature in v2.SIGNATURES:
            cursor.execute("ALTER FUNCTION " + signature + " OWNER TO " +
                quoted_owner)
        cursor.execute("CREATE TRIGGER " + GUARD_TRIGGER +
            " BEFORE INSERT OR UPDATE OR DELETE ON " + TABLE +
            " FOR EACH ROW EXECUTE FUNCTION " + v2.GUARD_SIGNATURE)
        cursor.execute("CREATE TRIGGER " + TRUNCATE_TRIGGER +
            " BEFORE TRUNCATE ON " + TABLE +
            " FOR EACH STATEMENT EXECUTE FUNCTION " +
            "public.ai_v4_seal_ticket_no_truncate()")
        verify_catalog(cursor)
        if _frozen(cursor) != frozen:
            raise RuntimeError("0073 changed frozen 0067/0068/0070 catalog")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0073 cannot discard login attestations")
        cursor.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0073 cannot reverse activated login role")
        cursor.execute("SELECT rolpassword IS NULL FROM pg_catalog.pg_authid "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (True,):
            raise RuntimeError("0073 cannot reverse identity with credentials")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
            "WHERE roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0073 cannot reverse role with members")
        for signature in reversed(v2.SIGNATURES):
            if signature == v2.GUARD_SIGNATURE:
                cursor.execute("DROP TRIGGER " + TRUNCATE_TRIGGER + " ON " + TABLE)
                cursor.execute("DROP TRIGGER " + GUARD_TRIGGER + " ON " + TABLE)
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        cursor.execute("REVOKE USAGE ON SCHEMA public FROM " + ROLE)
        # Global NOLOGIN role name remains for audited restored databases.


def verify_catalog(cursor, *, allow_test_login=False):
    """Pin the closed sidecar; only an isolated test may admit LOGIN."""
    # pg_roles masks every password as ********, including no-password roles.
    # A real verifier must read pg_authid under a controlled installer/restore
    # identity; an ordinary catalog reader fails closed here.
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid "
        "WHERE rolname=%s", [ROLE])
    flags = cursor.fetchone()
    if flags == (False,) * 7 + (True,):
        pass
    elif flags == (True,) + (False,) * 7 and allow_test_login is True:
        cursor.execute("SELECT current_database(),"
            "COALESCE(inet_server_addr()::text,''),inet_server_port()")
        identity = cursor.fetchone()
        if (identity is None or identity[0] != "test_teruisi_ai_rehearsal"
                or identity[1] not in ("127.0.0.1", "127.0.0.1/32",
                    "::1", "::1/128")
                or not 55440 <= int(identity[2]) <= 55999):
            raise RuntimeError("0073 LOGIN activation requires isolated test DB")
    else:
        raise RuntimeError("0073 login role attributes drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
        "WHERE roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0073 login role membership drift")
    cursor.execute("SELECT has_schema_privilege(%s,'public','CREATE'),"
        "has_database_privilege(%s,current_database(),'CREATE')",
        [ROLE, ROLE])
    if cursor.fetchone() != (False, False):
        raise RuntimeError("0073 login role can create public objects")
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)", [TABLE])
    table = cursor.fetchone()
    if table is None or table[0] != "r" or table[1] in {
            ROLE, "teruisi_ai_reader", "teruisi_ai_writer",
            "teruisi_ai_budget_v11_attest_login"}:
        raise RuntimeError("0073 protected table ownership drift")
    owner = table[1]
    old, protected, identity = _old_modules()
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass", [old.TABLE])
    if cursor.fetchone() != (owner,):
        raise RuntimeError("0073 owner differs from frozen 0067 proof owner")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_class c "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND acl.grantee<>c.relowner", [TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0073 protected table has non-owner grant")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_attribute a "
        "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) acl "
        "WHERE c.oid=%s::regclass AND a.attnum>0 "
        "AND acl.grantee<>c.relowner", [TABLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0073 protected column has non-owner grant")
    cursor.execute("SELECT attname,atttypid::regtype::text,atttypmod,attnotnull "
        "FROM pg_catalog.pg_attribute WHERE attrelid=%s::regclass "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum", [TABLE])
    expected_columns = (
        ("id", "character varying", 68, True),
        ("run_id", "character varying", 164, True),
        ("attempt", "integer", -1, True),
        ("report_id", "character varying", 164, True),
        ("owner_email", "character varying", 324, True),
        *((name, "character varying", 68, True) for name in (
            "binding_digest", "compact_json_sha256", "full_manifest_sha256",
            "full_manifest_digest")),
        ("file_descriptors_json", "text", -1, True),
        *((name, "character varying", 68, True) for name in (
            "file_descriptors_digest", "approved_content_digest",
            "human_review_digest")),
        ("budget_present", "boolean", -1, True),
        ("budget_plan_digest", "character varying", 68, False),
        *((name, "character varying", 68, True) for name in (
            "budget_proof_digest", "slim_proof_digest",
            "file_byte_verification_digest", "html_rows_digest",
            "xlsx_opc_formula_digest", "owning_verification_digest",
            "attestation_sha256")),
        ("attestation_json", "text", -1, True),
        ("recorded_at", "timestamp with time zone", -1, True),
    )
    if tuple(cursor.fetchall()) != expected_columns:
        raise RuntimeError("0073 protected table column drift")
    cursor.execute("SELECT c.conname,c.contype,"
        "pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c "
        "WHERE c.conrelid=%s::regclass", [TABLE])
    constraints = cursor.fetchall()
    if (len(constraints) != 6 or
            sorted(item[1] for item in constraints) != ["c", "f", "f", "p", "u", "u"] or
            not any(name == "ai_budget_v11_login_attest_attempt_uq" and
                definition == "UNIQUE (run_id, attempt)" for name, _, definition
                in constraints)):
        raise RuntimeError("0073 protected table constraints drift")
    by_name = {name: (kind, definition) for name, kind, definition in constraints}
    if (by_name.get("protected_business_budget_v11_login_attestations_pkey") !=
            ("p", "PRIMARY KEY (id)") or
            by_name.get("ai_budget_v11_login_attest_attempt_uq") !=
            ("u", "UNIQUE (run_id, attempt)") or
            not any(kind == "u" and definition == "UNIQUE (attestation_sha256)"
                for kind, definition in by_name.values()) or
            not any(kind == "c" and all(part in definition for part in
                ("attempt", "1", "5", ">=", "<="))
                for kind, definition in by_name.values()) or
            not any(kind == "f" and "FOREIGN KEY (run_id)" in definition and
                "REFERENCES ai_business_file_runs(id)" in definition and
                "ON DELETE RESTRICT" in definition
                for kind, definition in by_name.values()) or
            not any(kind == "f" and "FOREIGN KEY (report_id)" in definition and
                "REFERENCES ai_report_runs(id)" in definition and
                "ON DELETE RESTRICT" in definition
                for kind, definition in by_name.values())):
        raise RuntimeError("0073 protected table exact constraints drift")
    for signature, definition, definer, login_allowed in (
            (v2.GUARD_SIGNATURE, v2.guard_sql(), False, False),
            (v2.REQUIREMENTS_SIGNATURE, v2.requirements_sql(), True, False),
            (v2.ATTEST_SIGNATURE, v2.attest_sql(), True, True),
            (v2.OUTCOME_SIGNATURE, v2.OUTCOME_SQL, True, True)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not definer or row[3] != "plpgsql"
                or {item.replace(" ", "") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or row[4] != owner):
            raise RuntimeError("0073 protected function body or owner drift")
        for role, expected in ((ROLE, login_allowed),
                ("teruisi_ai_reader", False), ("teruisi_ai_writer", False),
                ("teruisi_ai_budget_v11_attestor", False),
                ("teruisi_ai_budget_v11_attest_login", False),
                ("teruisi_ai_budget_v11_sign_login", False),
                ("teruisi_ai_budget_v11_publish_login", False),
                ("teruisi_ai_budget_v11_publisher", False),
                ("teruisi_ai_budget_v11_key_owner", False)):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, signature])
            if cursor.fetchone() != (expected,):
                raise RuntimeError("0073 protected function ACL drift")
        cursor.execute("SELECT COALESCE(grantee.rolname,'PUBLIC'),"
            "acl.privilege_type FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s) AND acl.grantee<>p.proowner",
            [signature])
        if set(cursor.fetchall()) != ({(ROLE, "EXECUTE")} if login_allowed else set()):
            raise RuntimeError("0073 protected function has unexpected grant")
    for role in (ROLE, "teruisi_ai_reader", "teruisi_ai_writer",
            "teruisi_ai_budget_v11_attestor",
            "teruisi_ai_budget_v11_attest_login",
            "teruisi_ai_budget_v11_sign_login",
            "teruisi_ai_budget_v11_publish_login",
            "teruisi_ai_budget_v11_publisher",
            "teruisi_ai_budget_v11_key_owner"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                          "TRUNCATE", "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [role, TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0073 protected table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [role, TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0073 protected column ACL drift")
    for old_table in (old.TABLE, protected.KEY_TABLE,
            identity.v2.TABLE, identity.v2.CLAIMS,
            "public.ai_business_file_runs", "public.ai_business_file_chunks",
            "public.ai_business_volume_chunks"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                          "TRUNCATE", "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [ROLE, old_table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0073 role can access predecessor table")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [ROLE, old_table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0073 role can access predecessor column")
    for old_function in (old.REQUIREMENTS_SIGNATURE, old.SIGNATURE,
            protected.MAC_SIGNATURE, protected.VERIFY_SIGNATURE,
            identity.v2.ISSUE, identity.v2.READ, identity.v2.VERIFY):
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            [ROLE, old_function])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0073 role can execute predecessor function")
    cursor.execute("SELECT t.tgname,t.tgenabled,t.tgdeferrable,"
        "t.tginitdeferred,t.tgfoid,t.tgtype FROM pg_catalog.pg_trigger t "
        "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal", [TABLE])
    rows = cursor.fetchall()
    expected = {GUARD_TRIGGER: (v2.GUARD_SIGNATURE, 31),
        TRUNCATE_TRIGGER: ("public.ai_v4_seal_ticket_no_truncate()", 34)}
    if len(rows) != 2 or {item[0] for item in rows} != set(expected):
        raise RuntimeError("0073 protected trigger inventory drift")
    for name, enabled, deferred, initially_deferred, oid, event_bits in rows:
        signature, expected_bits = expected[name]
        cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
        if (enabled, deferred, initially_deferred, oid, event_bits) != (
                "O", False, False, cursor.fetchone()[0], expected_bits):
            raise RuntimeError("0073 protected trigger binding drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0072_business_market_v2_authority_proposals")]
    operations = [migrations.RunPython(install, uninstall)]
