"""Isolated 0055 -> 0056 market material role bridge and restore."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant.table_manifest import (
    AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS as AI_TABLES)
from ai_assistant.market_v2_admitted_catalog import verify as verify_market
from ai_assistant.v4_period_plan_catalog import verify as verify_period
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-period-plan-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0054->0055"):
    raise RuntimeError("0056 requires verified isolated 0055 predecessor")

OLD = [("ai_assistant", "0055_business_v4_period_plan_candidate"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0056_business_market_v2_material_role_bridge"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0055_business_v4_period_plan_candidate',"
        "'0056_business_market_v2_material_role_bridge') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0056 requires exact 0055 predecessor without 0056")

migration = importlib.import_module(
    "ai_assistant.migrations.0056_business_market_v2_material_role_bridge")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
CHANGED = ("public.ai_market_v2_admitted_report_guard()",
           "public.ai_market_v2_admitted_workflow_guard()")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def old_table_digest(db):
    if len(AI_TABLES) != 80:
        raise AssertionError("0056 requires frozen 80-table 0055 state")
    material = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def market_rows(db):
    parked = db.execute("SELECT id,owner_email,scope_json,snapshot_json,workflow_id "
        "FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile' "
        "IN (%s,%s) ORDER BY id", [
            "business-agent-screening-promotion-market-reference-v2",
            migration.PROFILE]).fetchall()
    sidecar = db.execute("SELECT row_to_json(t) FROM " + migration.sidecar.TABLE +
        " t ORDER BY report_id").fetchall()
    flows = db.execute("SELECT id,owner_email,scope_json,input_json,status,"
        "allowed_tools_json,model_id,provider_round_count,tool_call_count "
        "FROM ai_workflow_runs WHERE input_json::jsonb->>'executionProfile' "
        "IN (%s,%s) ORDER BY id", [
            "business-agent-screening-promotion-market-reference-v2",
            migration.PROFILE]).fetchall()
    return parked, sidecar, flows


def old_files(db):
    result = {}
    for version in range(1, 8):
        run_id = ("promotion-old-file-" + str(version) if version < 7
                  else "promotion-renderer-seven-staged")
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6, 7) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6, 7) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        if (row is None or row[:3] != (version,
                "building" if version == 7 else "ready", 1)
                or not chunks or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                    for blob, sha in chunks)):
            raise AssertionError("historical renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def function_catalog(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1").fetchall()
    result = {signature: details for signature, *details in rows}
    if len(result) != len(rows) or not result:
        raise AssertionError("AI function catalog absent or ambiguous")
    return result


def relation_trigger_catalog(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind IN ('r','p','v','m','f','S','i','I') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,c.relname,t.tgname,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid),t.tgenabled "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    return relations, triggers


def normalized(catalog):
    return {key: details[1:] for key, details in catalog.items()}


def normalized_inventory(value):
    return tuple(tuple(row[1:]) for row in value[0]), tuple(
        tuple(row[1:]) for row in value[1])


def archive_restore(name):
    archive = folder / (name + ".dump")
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, arguments in (
        ("pg_dump.exe", ["-Fc", "-f", str(archive), database["NAME"]]),
        ("createdb.exe", [name]),
        ("pg_restore.exe", ["--exit-on-error", "-d", name, str(archive)]),
    ):
        done = subprocess.run([str(BIN / executable), *arguments], env=env,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if done.returncode:
            (folder / (name + "-error.log")).write_bytes(done.stderr)
            raise RuntimeError("isolated 0056 archive or restore failed")


def closed(db, *, installed=False):
    with db.cursor() as cursor:
        verify_period(cursor, RuntimeError)
        verify_market(cursor, RuntimeError)
    for role in (migration.READER, migration.WRITER):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            value = db.execute("SELECT has_table_privilege(%s,%s,%s)",
                [role, migration.sidecar.TABLE, privilege]).fetchone()
            if value != (False,):
                raise AssertionError("0045 material table ACL opened")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            value = db.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [role, migration.sidecar.TABLE, privilege]).fetchone()
            if value != (False,):
                raise AssertionError("0045 material column ACL opened")
    signature = migration.SIGNATURE
    row = db.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner) "
        "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [signature]).fetchone()
    if not installed:
        if row is not None:
            raise AssertionError("0056 metadata function appeared before install")
        return
    owner = db.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid=to_regclass(%s)",
        [migration.sidecar.TABLE]).fetchone()[0]
    if (row is None or row[1] != migration.METADATA.split("$$")[1]
            or row[2] is not True or row[5] != owner
            or {item.replace(" ", "") for item in (row[3] or [])}
                != {"search_path=pg_catalog,public"}):
        raise AssertionError("0056 metadata function body, owner or scope drift")
    acl = db.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
        "a.privilege_type,a.is_grantable FROM pg_catalog.pg_proc p,"
        "LATERAL pg_catalog.aclexplode(p.proacl) a "
        "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3",
        [signature]).fetchall()
    if acl != sorted([(owner, "EXECUTE", False),
                      (migration.READER, "EXECUTE", False)]):
        raise AssertionError("0056 metadata function ACL drift")
    if db.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
            "has_function_privilege(%s,%s,'EXECUTE')",
            [migration.READER, signature, migration.WRITER, signature]
            ).fetchone() != (True, False):
        raise AssertionError("0056 metadata role EXECUTE drift")
    if db.execute("SELECT count(*) FROM ai_agent_tool_dispatches WHERE "
            "tool_name='get_business_promotion_market_v2'").fetchone() != (0,):
        raise AssertionError("market fifth tool unexpectedly dispatched")


with connect() as db:
    closed(db)
    before = (old_table_digest(db), market_rows(db), old_files(db),
        function_catalog(db), relation_trigger_catalog(db))

archive_restore("market_role_bridge_before")
with connect("market_role_bridge_before") as copy:
    closed(copy)
    assert (old_table_digest(copy), market_rows(copy), old_files(copy),
        normalized(function_catalog(copy)), normalized_inventory(
            relation_trigger_catalog(copy))) == (
        before[0], before[1], before[2], normalized(before[3]),
        normalized_inventory(before[4]))

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = (old_table_digest(db), market_rows(db), old_files(db),
        function_catalog(db), relation_trigger_catalog(db))
    if after[:3] != before[:3] or after[4] != before[4]:
        raise AssertionError("0056 changed old AI rows, market roots, renderer bytes, or triggers")
    new_key = db.execute("SELECT to_regprocedure(%s)::text",
        [migration.SIGNATURE]).fetchone()[0]
    if new_key is None or set(after[3]) != set(before[3]) | {new_key}:
        raise AssertionError("0056 must add exactly one AI function")
    changed_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0] for signature in CHANGED}
    if None in changed_keys or len(changed_keys) != 2:
        raise AssertionError("0053 guard identities missing")
    for key, old in before[3].items():
        now = after[3][key]
        if key in changed_keys:
            if (old[:4] != now[:4] or old[5:] != now[5:]
                    or old[4] is not False or now[4] is not True):
                raise AssertionError("0056 guard OID/body/ACL/owner changed: " + key)
        elif now != old:
            raise AssertionError("0056 changed non-target AI function: " + key)

archive_restore("market_role_bridge_after")
with connect("market_role_bridge_after") as copy:
    closed(copy, installed=True)
    assert (old_table_digest(copy), market_rows(copy), old_files(copy),
        normalized(function_catalog(copy)), normalized_inventory(
            relation_trigger_catalog(copy))) == (
        after[0], after[1], after[2], normalized(after[3]),
        normalized_inventory(after[4]))

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db)
    assert (old_table_digest(db), market_rows(db), old_files(db),
        function_catalog(db), relation_trigger_catalog(db)) == before

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    assert (old_table_digest(db), market_rows(db), old_files(db),
        normalized(function_catalog(db)), relation_trigger_catalog(db)) == (
        after[0], after[1], after[2], normalized(after[3]), after[4])

result = {"upgrade": "0055->0056", "oldAiTables": 80,
    "oldRowsAndRendererBytesPreserved": True,
    "parkedAdmittedMaterialRowsPreserved": True,
    "onlyTwoGuardSecurityAttributesChanged": True,
    "sameGuardOidBodyAclOwner": True,
    "newReaderFunctionExactClaimAndAcl": True,
    "sidecarTableAndColumnAclClosed": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True,
    "marketAgentDispatchEnabled": False, "productionWrites": False}
(folder / "business-market-v2-role-bridge-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
