"""Closed, isolated-test-only page read for a creation-linked v4 report.

0075 adds no table, report authority, Agent route, renderer or download grant.
The old 0040 sealer and 0071 link function definitions/ACLs are untouched.
"""
from django.db import migrations

from ai_assistant import business_v4_report_restricted_page_sql as page


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure(%s)", [page.PAGE])
        if cursor.fetchone()[0] is not None:
            raise RuntimeError("0075 restricted page function already exists")
        for signature in (page.RO_BINDINGS, page.RO_READ):
            cursor.execute("SELECT to_regprocedure(%s)", [signature])
            if cursor.fetchone()[0] is not None:
                raise RuntimeError("0075 read-only predecessor already exists")
        for definition in (page.RO_BINDINGS_SQL, page.RO_READ_SQL,
                page.PAGE_SQL):
            cursor.execute(definition)
        for signature in (page.RO_BINDINGS, page.RO_READ, page.PAGE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                " FROM PUBLIC")
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM " +
                page.WRITER + "," + page.READER)
        for signature in (page.RO_READ, page.PAGE):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO " + page.READER)
        verify_catalog(cursor)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP FUNCTION " + page.PAGE)
        cursor.execute("DROP FUNCTION " + page.RO_READ)
        cursor.execute("DROP FUNCTION " + page.RO_BINDINGS)


def verify_catalog(cursor):
    """Pin new function and the old 0071 contract without changing either."""
    from importlib import import_module
    import_module("ai_assistant.migrations.0071_business_v4_report_source_link"
        ).verify_catalog(cursor)
    old = import_module(
        "ai_assistant.migrations.0040_business_v4_sealer_narrow_stream")
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(proowner) "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
        ["public.ai_v4_read_report_source_link(text,text,bigint)"])
    link_owner = cursor.fetchone()
    if link_owner is None or link_owner[0] in {page.READER, page.WRITER}:
        raise RuntimeError("0075 predecessor owner unavailable")
    for signature, definition in ((page.RO_BINDINGS,
                page.RO_BINDINGS_SQL), (page.RO_READ, page.RO_READ_SQL)):
        cursor.execute("SELECT prosrc,prosecdef,proconfig,"
            "pg_catalog.pg_get_userbyid(proowner) "
            "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature])
        copied = cursor.fetchone()
        if (copied is None or copied[0] != definition.split("$$", 2)[1]
                or copied[1] is not True
                or {item.replace(" ", "") for item in (copied[2] or [])} !=
                    {"search_path=pg_catalog,public"}
                or copied[3] != link_owner[0]):
            raise RuntimeError("0075 read-only source function drift")
    cursor.execute("SELECT prosrc,prosecdef,proconfig,"
        "pg_catalog.pg_get_userbyid(proowner),proretset "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
        [page.PAGE])
    row = cursor.fetchone()
    if (row is None or row[0] != page.PAGE_SQL.split("$$", 2)[1]
            or row[1] is not True or row[4] is not True
            or {item.replace(" ", "") for item in (row[2] or [])} !=
                {"search_path=pg_catalog,public"}
            or row[3] != link_owner[0]):
        raise RuntimeError("0075 restricted page function catalog drift")

    def exact_execute_acl(signature, granted_role):
        cursor.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,"
            "a.privilege_type,a.is_grantable "
            "FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        acl = cursor.fetchall()
        if ((granted_role is not None and
                not any(grantee == granted_role and privilege == "EXECUTE"
                    and not grantable for grantee, privilege, grantable in acl))
                or any(grantee not in {link_owner[0], granted_role}
                    or privilege != "EXECUTE"
                    or (grantee == granted_role and grantable)
                    for grantee, privilege, grantable in acl)):
            raise RuntimeError("0075 function EXECUTE ACL drift")

    exact_execute_acl(page.PAGE, page.READER)
    exact_execute_acl(page.RO_BINDINGS, None)
    exact_execute_acl(page.RO_READ, page.READER)
    cursor.execute("SELECT prosrc,prosecdef,proconfig,"
        "pg_catalog.pg_get_userbyid(proowner) "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
        ["public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)"])
    old_page = cursor.fetchone()
    if (old_page is None or old_page[0] != old.PAGE.split("$$", 2)[1]
            or old_page[1] is not True
            or {item.replace(" ", "") for item in (old_page[2] or [])} !=
                {"search_path=pg_catalog,public"}
            or old_page[3] != link_owner[0]):
        raise RuntimeError("0075 predecessor 0040 page drift")
    exact_execute_acl(
        "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
        None)
    for signature, reader_allowed in ((page.RO_BINDINGS, False),
            (page.RO_READ, True), (page.PAGE, True)):
        for role in (page.READER, page.WRITER):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, signature])
            if cursor.fetchone() != (role == page.READER and
                    reader_allowed,):
                raise RuntimeError("0075 restricted function effective ACL drift")
    for table in ("public.ai_business_v4_chunks",
            "public.ai_business_v4_tool_receipts",
            "public.ai_tool_audit_logs",
            "public.protected_business_v4_report_link_intents",
            "public.protected_business_v4_report_source_links"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                "TRUNCATE"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [page.READER, table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0075 reader physical table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [page.READER, table, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0075 reader physical column ACL drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant",
        "0074_business_market_v2_human_cap_approval")]
    operations = [migrations.RunPython(install, uninstall)]
