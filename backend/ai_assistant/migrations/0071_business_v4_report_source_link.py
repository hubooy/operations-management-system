"""Append-only creation-transaction v2 report to sealed-v4 identity link.

The two records remain candidate-only. This migration does not register an
application caller, authenticate the application HMAC, read rows for Agent,
create files, publish a renderer or grant download.
"""
from django.db import migrations

from ai_assistant import business_v4_report_link_sql as link


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (link.INTENTS, link.LINKS):
            cursor.execute("SELECT to_regclass(%s)", [table])
            if cursor.fetchone()[0] is not None:
                raise RuntimeError("0071 link table already exists")
        cursor.execute("""CREATE TABLE public.protected_business_v4_report_link_intents (
          report_id varchar(160) PRIMARY KEY REFERENCES public.ai_report_runs(id)
            ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED,
          v4_run_id varchar(160) NOT NULL UNIQUE REFERENCES
            public.ai_business_v4_runs(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          actor_version bigint NOT NULL CHECK (actor_version>=1),
          v2_run_id varchar(160) NOT NULL REFERENCES
            public.ai_business_evidence_runs(id) ON DELETE RESTRICT,
          v2_sealed_digest varchar(64) NOT NULL
            CHECK (v2_sealed_digest ~ '^[0-9a-f]{64}$'),
          v4_sealed_digest varchar(64) NOT NULL
            CHECK (v4_sealed_digest ~ '^[0-9a-f]{64}$'),
          source_bindings_digest varchar(64) NOT NULL
            CHECK (source_bindings_digest ~ '^[0-9a-f]{64}$'),
          issued_txid bigint NOT NULL CHECK (issued_txid>0),
          issued_at timestamptz NOT NULL
        )""")
        cursor.execute("""CREATE TABLE public.protected_business_v4_report_source_links (
          report_id varchar(160) PRIMARY KEY REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          v4_run_id varchar(160) NOT NULL UNIQUE REFERENCES
            public.ai_business_v4_runs(id) ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          actor_version bigint NOT NULL CHECK (actor_version>=1),
          v2_run_id varchar(160) NOT NULL REFERENCES
            public.ai_business_evidence_runs(id) ON DELETE RESTRICT,
          v2_evidence_version bigint NOT NULL CHECK (v2_evidence_version>=1),
          v2_sealed_digest varchar(64) NOT NULL
            CHECK (v2_sealed_digest ~ '^[0-9a-f]{64}$'),
          v4_evidence_version bigint NOT NULL CHECK (v4_evidence_version>=1),
          v4_plan_digest varchar(64) NOT NULL
            CHECK (v4_plan_digest ~ '^[0-9a-f]{64}$'),
          v4_sealed_digest varchar(64) NOT NULL
            CHECK (v4_sealed_digest ~ '^[0-9a-f]{64}$'),
          v4_mac_digest varchar(64) NOT NULL
            CHECK (v4_mac_digest ~ '^[0-9a-f]{64}$'),
          report_snapshot_digest varchar(64) NOT NULL
            CHECK (report_snapshot_digest ~ '^[0-9a-f]{64}$'),
          workflow_input_digest varchar(64) NOT NULL
            CHECK (workflow_input_digest ~ '^[0-9a-f]{64}$'),
          source_bindings_json text NOT NULL
            CHECK (octet_length(source_bindings_json)<=16384),
          source_bindings_digest varchar(64) NOT NULL
            CHECK (source_bindings_digest ~ '^[0-9a-f]{64}$'),
          created_txid bigint NOT NULL CHECK (created_txid>0),
          created_at timestamptz NOT NULL,
          authority_verified boolean NOT NULL DEFAULT false
            CHECK (authority_verified=false),
          report_generation_supported boolean NOT NULL DEFAULT false
            CHECK (report_generation_supported=false)
        )""")
        for table in (link.INTENTS, link.LINKS):
            cursor.execute("REVOKE ALL ON TABLE " + table + " FROM PUBLIC")
            cursor.execute("REVOKE ALL ON TABLE " + table + " FROM " +
                link.WRITER + "," + link.READER)
        cursor.execute(link.ROW_GUARD)
        cursor.execute("REVOKE ALL ON FUNCTION "
            "public.ai_v4_report_link_row_guard() FROM PUBLIC")
        for table in (link.INTENTS, link.LINKS):
            cursor.execute("CREATE TRIGGER ai_v4_report_link_row_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON " + table +
                " FOR EACH ROW EXECUTE FUNCTION "
                "public.ai_v4_report_link_row_guard()")
            cursor.execute("CREATE TRIGGER ai_v4_report_link_no_truncate "
                "BEFORE TRUNCATE ON " + table +
                " FOR EACH STATEMENT EXECUTE FUNCTION "
                "public.ai_v4_seal_ticket_no_truncate()")
        for definition in (link.BINDINGS_SQL, link.ISSUE_SQL,
                link.CREATE_REPORT_SQL, link.REPORT_TRIGGER, link.READ_SQL):
            cursor.execute(definition)
        for signature in (link.BINDINGS, link.ISSUE, link.CREATE_REPORT,
                "public.ai_v4_report_link_after_insert()", link.READ):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + link.ISSUE +
            " TO " + link.WRITER)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + link.CREATE_REPORT +
            " TO " + link.WRITER)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + link.READ +
            " TO " + link.READER)
        cursor.execute("CREATE TRIGGER ai_v4_report_link_create "
            "AFTER INSERT ON public.ai_report_runs FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_v4_report_link_after_insert()")
        verify_catalog(cursor)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in (link.LINKS, link.INTENTS):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM " + table + ")")
            if cursor.fetchone() != (False,):
                raise RuntimeError("0071 cannot discard recorded creation-time link")
        cursor.execute("DROP TRIGGER ai_v4_report_link_create "
            "ON public.ai_report_runs")
        for signature in (link.READ,
                "public.ai_v4_report_link_after_insert()",
                link.CREATE_REPORT, link.ISSUE, link.BINDINGS):
            cursor.execute("DROP FUNCTION " + signature)
        for table in (link.LINKS, link.INTENTS):
            cursor.execute("DROP TRIGGER ai_v4_report_link_no_truncate ON " +
                table)
            cursor.execute("DROP TRIGGER ai_v4_report_link_row_guard ON " +
                table)
        cursor.execute("DROP FUNCTION public.ai_v4_report_link_row_guard()")
        cursor.execute("DROP TABLE " + link.LINKS)
        cursor.execute("DROP TABLE " + link.INTENTS)


def verify_catalog(cursor):
    """Read-only owner/ACL/trigger/function pin for the closed SQL bridge."""
    for table in (link.INTENTS, link.LINKS):
        cursor.execute("SELECT relkind,pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=%s::regclass", [table])
        row = cursor.fetchone()
        if row is None or row[0] != "r" or row[1] in {
                link.WRITER, link.READER}:
            raise RuntimeError("0071 table owner drift")
        for role in (link.WRITER, link.READER):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                    "TRUNCATE", "REFERENCES", "TRIGGER"):
                cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("0071 direct table privilege drift")
            for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                    [role, table, privilege])
                if cursor.fetchone() != (False,):
                    raise RuntimeError("0071 direct column privilege drift")
        cursor.execute("SELECT tgname,tgenabled,tgtype,tgfoid "
            "FROM pg_catalog.pg_trigger WHERE tgrelid=%s::regclass "
            "AND NOT tgisinternal ORDER BY tgname", [table])
        rows = cursor.fetchall()
        if len(rows) != 2 or {item[0] for item in rows} != {
                "ai_v4_report_link_row_guard",
                "ai_v4_report_link_no_truncate"} or any(
                item[1] != "O" for item in rows):
            raise RuntimeError("0071 row trigger drift")
        for name, _, event_bits, oid in rows:
            signature = ("public.ai_v4_report_link_row_guard()" if
                name == "ai_v4_report_link_row_guard" else
                "public.ai_v4_seal_ticket_no_truncate()")
            cursor.execute("SELECT to_regprocedure(%s)::oid", [signature])
            if oid != cursor.fetchone()[0] or event_bits != (
                    31 if name == "ai_v4_report_link_row_guard" else 34):
                raise RuntimeError("0071 row trigger function drift")
    cursor.execute("SELECT tgenabled,tgtype,tgfoid FROM pg_catalog.pg_trigger "
        "WHERE tgrelid='public.ai_report_runs'::regclass AND "
        "tgname='ai_v4_report_link_create'")
    row = cursor.fetchone()
    cursor.execute("SELECT to_regprocedure('public.ai_v4_report_link_after_insert()')::oid")
    if row != ("O", 5, cursor.fetchone()[0]):
        raise RuntimeError("0071 report trigger drift")
    functions = (("public.ai_v4_report_link_row_guard()",
            link.ROW_GUARD, None),
        (link.BINDINGS, link.BINDINGS_SQL, None),
        (link.ISSUE, link.ISSUE_SQL, link.WRITER),
        (link.CREATE_REPORT, link.CREATE_REPORT_SQL, link.WRITER),
        ("public.ai_v4_report_link_after_insert()",
            link.REPORT_TRIGGER, None),
        (link.READ, link.READ_SQL, link.READER))
    for signature, definition, grantee in functions:
        cursor.execute("SELECT prosrc,prosecdef,proconfig,"
            "pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$",2)[1]
                or row[1] is not (grantee is not None or signature in {
                    link.BINDINGS,
                    "public.ai_v4_report_link_after_insert()"})
                or {item.replace(" ","") for item in (row[2] or [])} !=
                    {"search_path=pg_catalog,public"}
                or row[3] in {link.WRITER,link.READER}):
            raise RuntimeError("0071 function catalog drift")
        for role in (link.WRITER, link.READER):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, signature])
            if cursor.fetchone() != (role == grantee,):
                raise RuntimeError("0071 function ACL drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0070_business_promotion_budget_v11_limited_identity")]
    operations = [migrations.RunPython(install, uninstall)]
