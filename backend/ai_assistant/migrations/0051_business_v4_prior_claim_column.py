"""Disambiguate the preceding claim lookup in the v4 replay writer.

PL/pgSQL sees both a local ``ticket_id`` and the claims table column in the
0048 writer. The lookup is only reached for segment two and later; qualify
the column while preserving the writer's function identity and privileges.
"""
from importlib import import_module

from django.db import migrations


previous = import_module(
    "ai_assistant.migrations.0048_business_v4_finance_replay_progress")
base = previous.previous

OLD_RECORD = previous.RECORD
_old_lookup = """    SELECT * INTO prior_claim FROM public.ai_business_v4_seal_claims
      WHERE ticket_id=prior.ticket_id;"""
_new_lookup = """    SELECT * INTO prior_claim FROM public.ai_business_v4_seal_claims AS prior_claim_row
      WHERE prior_claim_row.ticket_id=prior.ticket_id;"""
if OLD_RECORD.count(_old_lookup) != 1:
    raise RuntimeError("0051 requires the frozen 0048 prior-claim lookup")
RECORD = OLD_RECORD.replace(_old_lookup, _new_lookup)


def _check_predecessor(cursor, expected):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [base.SEALER])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0051 requires unchanged NOLOGIN seal writer")
    cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
        "l.lanname,pg_catalog.pg_get_userbyid(p.proowner),"
        "pg_catalog.pg_get_function_result(p.oid) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
        "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)",
        [base.WRITE])
    row = cursor.fetchone()
    if (row is None or row[1] != expected.split("$$")[1]
            or row[2] is not True
            or {item.replace(" ", "") for item in (row[3] or [])}
                != {"search_path=pg_catalog,public"}
            or row[4] != "plpgsql"
            or row[5] in {"teruisi_ai_reader", "teruisi_ai_writer",
                           base.SEALER}
            or row[6] != "TABLE(candidate_digest text, recorded_at timestamp with time zone)"):
        raise RuntimeError("0051 requires the frozen replay writer body and signature")
    for role, allowed in ((base.SEALER, True),
                          ("teruisi_ai_reader", False),
                          ("teruisi_ai_writer", False)):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            if role == base.SEALER:
                raise RuntimeError("0051 requires the independent sealer role")
            continue
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
            "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
            [role, base.WRITE, role, base.TABLE])
        if cursor.fetchone() != (allowed, False):
            raise RuntimeError("0051 requires unchanged replay writer ACL")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
        "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=%s AND "
        "acl.grantee=0 AND acl.privilege_type='EXECUTE')", [row[0]])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0051 requires revoked PUBLIC EXECUTE")
    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
        [base.SEALER,
         "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0051 direct seal commit must remain revoked")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _check_predecessor(cursor, OLD_RECORD)
        cursor.execute(RECORD.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _check_predecessor(cursor, RECORD)
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + base.TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0051 cannot restore ambiguous writer with replay receipts")
        cursor.execute(OLD_RECORD.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0050_business_v4_replay_read_cast")]
    operations = [migrations.RunPython(install, uninstall)]
