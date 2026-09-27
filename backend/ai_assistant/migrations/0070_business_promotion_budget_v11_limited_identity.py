"""Default-NOLOGIN v11 proof-ticket and read-only verifier identities.

No credential, signer route, v11 ready branch, download route or model call.
The 0067/0068 functions and their grants remain byte- and OID-identical.
"""
from importlib import import_module

from django.db import migrations

from ai_assistant import business_promotion_budget_v11_identity_candidate_sql as v2


def _old_functions(cursor):
    att = import_module(
        "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
    protected = import_module(
        "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
    stage = import_module("ai_assistant.business_promotion_budget_v11_stage_sql")
    att.verify_catalog(cursor)
    protected.verify_catalog(cursor)
    definitions = (
        (att.SIGNATURE, att.ATTEST),
        (att.REQUIREMENTS_SIGNATURE, att.REQUIREMENTS),
        ("public.ai_budget_v11_attestation_guard()", att.GUARD),
        (protected.VERIFY_SIGNATURE, protected.VERIFY),
        (protected.MAC_SIGNATURE, protected.MAC),
        ("public.ai_budget_v11_key_guard()", protected.KEY_GUARD),
        ("public.ai_business_files_guard()", stage.RUN_GUARD),
        ("public.ai_business_volume_complete_guard()", stage.COMPLETE_GUARD),
    )
    snapshot = []
    for signature, definition in definitions:
        cursor.execute("SELECT oid,prosrc,proacl::text,proowner,prosecdef "
            "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        row = cursor.fetchone()
        if row is None or row[1] != definition.split("$$", 2)[1]:
            raise RuntimeError("0070 requires exact 0067/0068/v11 guard predecessor")
        snapshot.append((signature, row))
    return tuple(snapshot)


def _role(cursor, name):
    cursor.execute("SELECT to_regrole(%s)", [name])
    if cursor.fetchone()[0] is None:
        cursor.execute("CREATE ROLE " + name + " NOLOGIN NOINHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls,rolpassword IS NULL "
        "FROM pg_catalog.pg_authid WHERE rolname=%s", [name])
    if cursor.fetchone() != (False,) * 7 + (True,):
        raise RuntimeError("0070 dedicated role widened or has credentials")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [name, name])
    if cursor.fetchone() != (0,):
        raise RuntimeError("0070 dedicated role has members")
    cursor.execute("GRANT USAGE ON SCHEMA public TO " + name)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        frozen = _old_functions(cursor)
        for name in v2.ROLES:
            _role(cursor, name)
        cursor.execute("""CREATE TABLE public.protected_business_budget_v11_proof_tickets (
          id uuid PRIMARY KEY,
          run_id varchar(160) NOT NULL REFERENCES public.ai_business_file_runs(id)
            ON DELETE RESTRICT,
          attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
          parent_version bigint NOT NULL CHECK (parent_version>=1),
          report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          binding_digest varchar(64) NOT NULL CHECK (binding_digest ~ '^[0-9a-f]{64}$'),
          attestation_sha256 varchar(64) NOT NULL
            CHECK (attestation_sha256 ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          CHECK (expires_at>created_at AND
            expires_at<=created_at+interval '11 minutes'),
          CONSTRAINT budget_v11_ticket_one_per_attempt UNIQUE (run_id,attempt)
        )""")
        cursor.execute("""CREATE TABLE public.protected_business_budget_v11_proof_ticket_claims (
          id uuid PRIMARY KEY,
          ticket_id uuid NOT NULL UNIQUE REFERENCES
            public.protected_business_budget_v11_proof_tickets(id)
            ON DELETE RESTRICT,
          run_id varchar(160) NOT NULL REFERENCES public.ai_business_file_runs(id)
            ON DELETE RESTRICT,
          attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
          attestation_sha256 varchar(64) NOT NULL
            CHECK (attestation_sha256 ~ '^[0-9a-f]{64}$'),
          claimed_at timestamptz NOT NULL
        )""")
        for table in (v2.TABLE, v2.CLAIMS):
            cursor.execute("REVOKE ALL ON " + table + " FROM PUBLIC")
        cursor.execute(v2.ROW_GUARD)
        cursor.execute("REVOKE ALL ON FUNCTION "
            "public.ai_budget_v11_proof_ticket_row_guard() FROM PUBLIC")
        for table in (v2.TABLE, v2.CLAIMS):
            cursor.execute("CREATE TRIGGER ai_budget_v11_ticket_row_guard BEFORE "
                "INSERT OR UPDATE OR DELETE ON " + table + " FOR EACH ROW "
                "EXECUTE FUNCTION public.ai_budget_v11_proof_ticket_row_guard()")
            cursor.execute("CREATE TRIGGER ai_budget_v11_ticket_no_truncate "
                "BEFORE TRUNCATE ON " + table + " FOR EACH STATEMENT "
                "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")
        for definition in (v2.ISSUE_SQL, v2.READ_SQL, v2.verify_sql()):
            cursor.execute(definition)
        for signature, role in ((v2.ISSUE, v2.ATTEST), (v2.READ, v2.SIGN),
                (v2.VERIFY, v2.PUBLISH)):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + role)
        if _old_functions(cursor) != frozen:
            raise RuntimeError("0070 changed 0067/0068 or v11 file guard")
        verify_catalog(cursor)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (v2.TABLE, v2.CLAIMS):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone()[0]:
                raise RuntimeError("0070 cannot discard issued or claimed tickets")
        for name in v2.ROLES:
            cursor.execute("SELECT rolcanlogin FROM pg_catalog.pg_roles "
                "WHERE rolname=%s", [name])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0070 cannot reverse activated identity")
            cursor.execute("SELECT rolpassword IS NULL FROM pg_catalog.pg_authid "
                "WHERE rolname=%s", [name])
            if cursor.fetchone() != (True,):
                raise RuntimeError("0070 cannot reverse identity with credentials")
            cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
                "WHERE roleid=%s::regrole OR member=%s::regrole", [name,name])
            if cursor.fetchone() != (0,):
                raise RuntimeError("0070 cannot reverse role membership")
        for signature in (v2.VERIFY, v2.READ, v2.ISSUE):
            cursor.execute("DROP FUNCTION " + signature)
        for table in (v2.CLAIMS, v2.TABLE):
            cursor.execute("DROP TRIGGER ai_budget_v11_ticket_no_truncate ON " + table)
            cursor.execute("DROP TRIGGER ai_budget_v11_ticket_row_guard ON " + table)
        cursor.execute("DROP FUNCTION public.ai_budget_v11_proof_ticket_row_guard()")
        cursor.execute("DROP TABLE " + v2.CLAIMS)
        cursor.execute("DROP TABLE " + v2.TABLE)
        for name in reversed(v2.ROLES):
            cursor.execute("REVOKE USAGE ON SCHEMA public FROM " + name)
        # Roles are cluster-global. Independent restored databases may still
        # hold their grants, so keep empty NOLOGIN names as an audit record.


def verify_catalog(cursor):
    """Read-only pin: NOLOGIN roles, append-only rows and exact EXECUTE grants."""
    for name in v2.ROLES:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [name])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0070 identity role drift")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [name,name])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0070 identity membership drift")
    for table in (v2.TABLE, v2.CLAIMS):
        cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=%s::regclass", [table])
        row = cursor.fetchone()
        if row is None or row[0] != "r" or row[1] in {
                *v2.ROLES, "teruisi_ai_reader", "teruisi_ai_writer"}:
            raise RuntimeError("0070 ticket table owner drift")
        cursor.execute("SELECT tgname,tgenabled,tgfoid,"
            "tgtype,tgdeferrable,tginitdeferred "
            "FROM pg_catalog.pg_trigger WHERE tgrelid=%s::regclass "
            "AND NOT tgisinternal ORDER BY tgname", [table])
        rows = cursor.fetchall()
        if len(rows) != 2 or {item[0] for item in rows} != {
                "ai_budget_v11_ticket_row_guard",
                "ai_budget_v11_ticket_no_truncate"} or any(
                item[1] != "O" or item[4:] != (False,False) for item in rows):
            raise RuntimeError("0070 ticket trigger drift")
        expected = {
            "ai_budget_v11_ticket_row_guard":
                ("public.ai_budget_v11_proof_ticket_row_guard()",31),
            "ai_budget_v11_ticket_no_truncate":
                ("public.ai_v4_seal_ticket_no_truncate()",34)}
        for item in rows:
            signature, event_bits = expected[item[0]]
            cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
            if (item[2], item[3]) != (cursor.fetchone()[0], event_bits):
                raise RuntimeError("0070 ticket trigger binding drift")
        for role in (*v2.ROLES, "teruisi_ai_reader", "teruisi_ai_writer"):
            for privilege in ("SELECT","INSERT","UPDATE","DELETE",
                    "TRUNCATE","REFERENCES","TRIGGER"):
                cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                    [role,table,privilege])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("0070 ticket table ACL drift")
            for privilege in ("SELECT","INSERT","UPDATE","REFERENCES"):
                cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                    [role,table,privilege])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("0070 ticket column ACL drift")
    for signature, body, grantee in (
            ("public.ai_budget_v11_proof_ticket_row_guard()",v2.ROW_GUARD,None),
            (v2.ISSUE,v2.ISSUE_SQL,v2.ATTEST),
            (v2.READ,v2.READ_SQL,v2.SIGN),
            (v2.VERIFY,v2.verify_sql(),v2.PUBLISH)):
        cursor.execute("SELECT prosrc,prosecdef,proconfig,"
            "pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        row=cursor.fetchone()
        if (row is None or row[0]!=body.split("$$",2)[1]
                or row[1] is not (grantee is not None)
                or {item.replace(" ","") for item in (row[2] or [])} !=
                    {"search_path=pg_catalog,public"}
                or row[3] in {*v2.ROLES,"teruisi_ai_reader","teruisi_ai_writer"}):
            raise RuntimeError("0070 function catalog drift")
        for role in (*v2.ROLES,"teruisi_ai_reader","teruisi_ai_writer"):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role,signature])
            if cursor.fetchone() != (role == grantee,):
                raise RuntimeError("0070 function ACL drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0069_business_market_v2_paid_round_rehearsal")]
    operations = [migrations.RunPython(install, uninstall)]
