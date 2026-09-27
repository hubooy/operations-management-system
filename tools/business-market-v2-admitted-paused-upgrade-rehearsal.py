"""Isolated 0052 -> 0053 admitted-paused market v2 upgrade and restore."""
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

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_PERIOD_CANDIDATES as AI_TABLES
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-commit-consumption-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0051->0052"):
    raise RuntimeError("0053 rehearsal requires verified isolated 0052 seed")

OLD = [("ai_assistant", "0052_business_v4_commit_consumption"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0053_business_market_v2_admitted_paused"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0052_business_v4_commit_consumption',"
        "'0053_business_market_v2_admitted_paused') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0053 requires exact 0052 predecessor without 0053")

migration = importlib.import_module(
    "ai_assistant.migrations.0053_business_market_v2_admitted_paused")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
GUARDS = {
    "ai_market_v2_admitted_workflow_guard": ("ai_workflow_runs", migration.WORKFLOW_GUARD, 31, False),
    "ai_market_v2_admitted_report_guard": ("ai_report_runs", migration.REPORT_GUARD, 31, False),
    "ai_market_v2_admitted_complete": ("ai_workflow_runs", migration.ORPHAN_GUARD, 5, True),
    "ai_market_v2_admitted_job_guard": ("ai_agent_jobs", migration.JOB_GUARD, 31, False),
    "ai_market_v2_admitted_node_guard": ("ai_workflow_node_runs", migration.NODE_GUARD, 31, False),
    "ai_market_v2_admitted_tool_guard": ("ai_agent_tool_dispatches", migration.TOOL_GUARD, 23, False),
    "ai_market_v2_admitted_result_guard": ("ai_agent_tool_results", migration.RESULT_GUARD, 7, False),
}


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    material = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def parked_material(db):
    parked = db.execute("SELECT id,owner_email,scope_json,snapshot_json,workflow_id "
        "FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile'=%s "
        "ORDER BY id", ["business-agent-screening-promotion-market-reference-v2"],
        ).fetchall()
    materials = db.execute("SELECT row_to_json(t) FROM "
        "ai_business_market_v2_materials t ORDER BY report_id").fetchall()
    return parked, materials


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


def trigger_catalog(db):
    rows = db.execute("SELECT c.relname,t.tgname,t.oid,t.tgfoid::regprocedure::text,"
        "t.tgtype,t.tgenabled,t.tgdeferrable,t.tginitdeferred,t.tgisinternal,"
        "pg_catalog.pg_get_triggerdef(t.oid) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    result = {(table, name): details for table, name, *details in rows}
    if len(result) != len(rows):
        raise AssertionError("trigger names ambiguous")
    return result


def relations(db):
    return db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.relname").fetchall()


def restored(catalog):
    return {key: details[1:] for key, details in catalog.items()}


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
            raise RuntimeError("isolated 0053 archive or restore failed")


def closed(db, *, installed=False):
    if len(AI_TABLES) != 79:
        raise AssertionError("0053 requires 79 historical AI tables")
    if db.execute("SELECT count(*) FROM ai_report_runs WHERE "
            "snapshot_json::jsonb->>'executionProfile'=%s", [migration.PROFILE],
            ).fetchone() != (0,):
        raise AssertionError("admitted report must be empty for upgrade reversal")
    if db.execute("SELECT count(*) FROM ai_workflow_runs WHERE "
            "input_json::jsonb->>'executionProfile'=%s", [migration.PROFILE],
            ).fetchone() != (0,):
        raise AssertionError("admitted workflow must be empty for reversal")
    for name, (table, definition, trigger_type, deferred) in GUARDS.items():
        signature = "public." + definition.split("CREATE FUNCTION public.", 1)[1].split("(", 1)[0] + "()"
        row = db.execute("SELECT p.prosrc,p.proacl::text,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature]).fetchone()
        guard_key = table, name
        if not installed:
            if row is not None or guard_key in trigger_catalog(db):
                raise AssertionError("0053 function or trigger already exists")
            continue
        old_owner_acl = db.execute("SELECT p.proacl::text,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)",
            ["public.ai_market_v2_parked_report_guard()"],).fetchone()
        if (row is None or row[0] != definition.split("$$")[1]
                or row[1] != old_owner_acl[0] or row[4] != old_owner_acl[1]
                or row[2] is not False or row[3] is None
                or {part.replace(" ", "") for part in row[3]}
                    != {"search_path=pg_catalog,public"}
                or row[5] != "plpgsql"):
            raise AssertionError("0053 guard body or fixed search_path drift")
        acl = db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=to_regprocedure(%s) "
            "AND acl.grantee=0 AND acl.privilege_type='EXECUTE')", [signature],
            ).fetchone()
        if acl != (False,):
            raise AssertionError("0053 guard PUBLIC execute reopened")
        trigger = trigger_catalog(db).get(guard_key)
        expected_function = db.execute("SELECT to_regprocedure(%s)::text", [signature]).fetchone()[0]
        if (trigger is None or trigger[1] != expected_function
                or trigger[2] != trigger_type or trigger[3] != "O"
                or trigger[4] != deferred or trigger[5] != deferred
                or trigger[6] is not False):
            raise AssertionError("0053 guard trigger relation, event, or deferral drift")
    if installed:
        if db.execute("SELECT count(*) FROM ai_agent_tool_dispatches WHERE "
                "tool_name='get_business_promotion_market_v2'",).fetchone() != (0,):
            raise AssertionError("market fifth tool dispatch unexpectedly present")


with connect() as db:
    closed(db)
    before = (table_digest(db), parked_material(db), old_files(db),
        function_catalog(db), trigger_catalog(db), relations(db))

archive_restore("market_v2_admitted_before")
with connect("market_v2_admitted_before") as copy:
    closed(copy)
    assert (table_digest(copy), parked_material(copy), old_files(copy),
        restored(function_catalog(copy)), restored(trigger_catalog(copy)),
        tuple(tuple(row[1:]) for row in relations(copy))) == (
        before[0], before[1], before[2], restored(before[3]),
        restored(before[4]), tuple(tuple(row[1:]) for row in before[5]))

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = (table_digest(db), parked_material(db), old_files(db),
        function_catalog(db), trigger_catalog(db), relations(db))
    if after[:3] != before[:3] or after[5] != before[5]:
        raise AssertionError("0053 changed historical AI rows, market roots, files, or tables")
    if set(after[3]) != set(before[3]) | {db.execute("SELECT to_regprocedure(%s)::text",
            ["public." + definition.split("CREATE FUNCTION public.", 1)[1].split("(", 1)[0] + "()"]
            ).fetchone()[0] for _, definition, _, _ in GUARDS.values()}:
        raise AssertionError("0053 function inventory not exactly seven new guards")
    for signature, old in before[3].items():
        if after[3][signature] != old:
            raise AssertionError("0053 changed previous AI function: " + signature)
    if set(after[4]) != set(before[4]) | {
            (table, name) for name, (table, _, _, _) in GUARDS.items()}:
        raise AssertionError("0053 trigger inventory not exactly seven new guards")
    for name, old in before[4].items():
        if after[4][name] != old:
            raise AssertionError("0053 changed previous trigger: " + name)

archive_restore("market_v2_admitted_after")
with connect("market_v2_admitted_after") as copy:
    closed(copy, installed=True)
    assert (table_digest(copy), parked_material(copy), old_files(copy),
        restored(function_catalog(copy)), restored(trigger_catalog(copy)),
        tuple(tuple(row[1:]) for row in relations(copy))) == (
        after[0], after[1], after[2], restored(after[3]),
        restored(after[4]), tuple(tuple(row[1:]) for row in after[5]))

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db)
    assert (table_digest(db), parked_material(db), old_files(db),
        function_catalog(db), trigger_catalog(db), relations(db)) == before

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    assert (table_digest(db), parked_material(db), old_files(db),
        restored(function_catalog(db)), restored(trigger_catalog(db)),
        relations(db)) == (after[0], after[1], after[2],
        restored(after[3]), restored(after[4]), after[5])

result = {"upgrade": "0052->0053", "aiTables": 79,
    "oldRowsAndRendererBytesPreserved": True,
    "parkedAndMaterialRowsPreserved": True,
    "everyPreviousAiFunctionOidBodyAclPreserved": True,
    "newFunctionsAndTriggersExact": True,
    "reportFlowJobDefaultClosed": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True,
    "productionWrites": False}
(folder / "business-market-v2-admitted-paused-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
