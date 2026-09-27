"""Keep the claim-bound replay reader usable for stored varchar digests.

PostgreSQL RETURN QUERY does not implicitly convert varchar(64) to the
declared text result. Only the digest projection changes; the existing
function identity, claim checks and privileges remain in place.
"""
from importlib import import_module

from django.db import migrations


previous = import_module(
    "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")

OLD_READ = previous.READ_SQL
_old_projection = "RETURN QUERY SELECT p.candidate_json,p.candidate_digest,p.recorded_at"
_new_projection = "RETURN QUERY SELECT p.candidate_json,p.candidate_digest::text,p.recorded_at"
if OLD_READ.count(_old_projection) != 1:
    raise RuntimeError("0050 requires the frozen 0047 replay reader")
READ = OLD_READ.replace(_old_projection, _new_projection)


def _check_predecessor(cursor, expected):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [previous.SEALER])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0050 requires unchanged NOLOGIN seal writer")
    cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
        "l.lanname,pg_catalog.pg_get_userbyid(p.proowner),"
        "pg_catalog.pg_get_function_result(p.oid) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
        "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)",
        [previous.READ])
    row = cursor.fetchone()
    if (row is None or row[1] != expected.split("$$")[1]
            or row[2] is not True
            or {item.replace(" ", "") for item in (row[3] or [])}
                != {"search_path=pg_catalog,public"}
            or row[4] != "plpgsql"
            or row[5] in {"teruisi_ai_reader", "teruisi_ai_writer",
                           previous.SEALER}
            or row[6] != "TABLE(candidate_json text, candidate_digest text, recorded_at timestamp with time zone)"):
        raise RuntimeError("0050 requires the frozen replay reader body and signature")
    for role, allowed in ((previous.SEALER, True),
                          ("teruisi_ai_reader", False),
                          ("teruisi_ai_writer", False)):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            if role == previous.SEALER:
                raise RuntimeError("0050 requires the independent sealer role")
            continue
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
            "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
            [role, previous.READ, role, previous.TABLE])
        if cursor.fetchone() != (allowed, False):
            raise RuntimeError("0050 requires unchanged 0047 READ ACL")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
        "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=%s AND "
        "acl.grantee=0 AND acl.privilege_type='EXECUTE')", [row[0]])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0050 requires revoked PUBLIC EXECUTE")
    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
        [previous.SEALER,
         "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0050 direct seal commit must remain revoked")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _check_predecessor(cursor, OLD_READ)
        cursor.execute(READ.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _check_predecessor(cursor, READ)
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + previous.TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0050 cannot restore unreadable replay reader with receipts")
        cursor.execute(OLD_READ.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0049_business_v4_sealer_source_bridge")]
    operations = [migrations.RunPython(install, uninstall)]
