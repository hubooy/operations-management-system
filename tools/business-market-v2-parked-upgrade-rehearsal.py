"""Isolated 0043 -> 0044 parked-market upgrade, restore, and empty reverse."""
import argparse
import hashlib
from importlib import import_module
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

from ai_assistant.table_manifest import AI_TABLES_PRE_MARKET_V2_MATERIALS as AI_TABLES
from business_analysis.contracts import canonical

market_migration = import_module("ai_assistant.migrations.0044_business_market_v2_profile")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-seal-consumption-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0042->0043"):
    raise RuntimeError("0044 rehearsal requires the verified isolated 0043 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0043_business_v4_seal_consumption_candidate"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0044_business_market_v2_profile"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0043_business_v4_seal_consumption_candidate',"
        "'0044_business_market_v2_profile') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0044 requires exact 0043 predecessor without 0044")


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


def old_files(db):
    result = {}
    for version in range(1, 8):
        run_id = ("promotion-old-file-" + str(version) if version < 7
                  else "promotion-renderer-seven-staged")
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = ("ai_business_volume_chunks" if version in (4, 6, 7)
                 else "ai_business_file_chunks")
        order = ("volume_index,format,sequence" if version in (4, 6, 7)
                 else "format,sequence")
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        expected = "building" if version == 7 else "ready"
        if row is None or row[:3] != (version, expected, 1) or not chunks \
                or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                       for blob, sha in chunks):
            raise AssertionError("historical renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def table_acl(db):
    # pg_dump/pg_restore may normalize ACL array ordering. Compare effective
    # role privileges, which is the compatibility contract.
    rows = db.execute("SELECT c.relname,r.role_name,"
        "has_table_privilege(r.role_name,'public.'||c.relname,'SELECT'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'INSERT'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'UPDATE'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'DELETE'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'TRUNCATE') "
        "FROM pg_class c CROSS JOIN (VALUES ('teruisi_ai_reader'),"
        "('teruisi_ai_writer'),('teruisi_ai_seal_writer')) r(role_name) "
        "WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s) "
        "ORDER BY c.relname,r.role_name", [list(AI_TABLES)]).fetchall()
    if len(rows) != len(AI_TABLES) * 3:
        raise AssertionError("AI table inventory changed")
    return rows


TRIGGERS = (
    ("ai_report_runs", "ai_market_v2_report_guard", "ai_market_v2_parked_report_guard()", 31),
    ("ai_workflow_runs", "ai_market_v2_workflow_guard", "ai_market_v2_parked_workflow_guard()", 31),
    ("ai_workflow_runs", "ai_market_v2_workflow_complete", "ai_market_v2_parked_orphan_guard()", 5),
    ("ai_agent_jobs", "ai_market_v2_job_guard", "ai_market_v2_parked_job_guard()", 7),
)


def market_guards(db):
    rows = db.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,"
        "t.tgdeferrable,t.tginitdeferred,p.proname,p.prosrc,p.prosecdef,"
        "coalesce(array_to_string(p.proconfig,','),'') "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_proc p ON p.oid=t.tgfoid AND "
        "p.pronamespace='public'::regnamespace "
        "WHERE NOT t.tgisinternal AND t.tgname LIKE 'ai_market_v2_%' "
        "ORDER BY c.relname,t.tgname").fetchall()
    return rows


def archive_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        completed = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            (folder / (name + "-error.log")).write_bytes(completed.stderr)
            raise RuntimeError("isolated 0044 archive/restore failed")


with connect() as db:
    assert len(AI_TABLES) == 77
    before, files_before, acl_before = table_digest(db), old_files(db), table_acl(db)
    assert market_guards(db) == []
archive_restore("business_market_v2_parked_before")
with connect("business_market_v2_parked_before") as restored:
    assert (table_digest(restored), old_files(restored), table_acl(restored),
        market_guards(restored)) == (before, files_before, acl_before, [])

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert (table_digest(db), old_files(db), table_acl(db)) == (
        before, files_before, acl_before)
    guards = market_guards(db)
    assert len(guards) == 4
    definitions = {
        "ai_market_v2_report_guard": market_migration.REPORT_GUARD,
        "ai_market_v2_workflow_guard": market_migration.WORKFLOW_GUARD,
        "ai_market_v2_workflow_complete": market_migration.ORPHAN_GUARD,
        "ai_market_v2_job_guard": market_migration.JOB_GUARD,
    }
    for table, name, function, trigger_type in TRIGGERS:
        matching = [row for row in guards if row[0:2] == (table, name)]
        assert len(matching) == 1
        row = matching[0]
        assert row[2:4] == (trigger_type, "O") and row[6] == function.split("(")[0]
        assert row[7] == definitions[name].split("$$")[1]
        assert row[8] is False and "search_path=pg_catalog,public" in row[9].replace(" ", "")
        assert row[4:6] == ((True, True) if name.endswith("complete") else (False, False))
    assert db.execute("SELECT count(*) FROM ai_report_runs WHERE "
        "snapshot_json::jsonb->>'executionProfile'="
        "'business-agent-screening-promotion-market-reference-v2'").fetchone() == (0,)
archive_restore("business_market_v2_parked_after")
with connect("business_market_v2_parked_after") as restored:
    assert (table_digest(restored), old_files(restored), table_acl(restored),
        market_guards(restored)) == (before, files_before, acl_before, guards)

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert (table_digest(db), old_files(db), table_acl(db), market_guards(db)) == (
        before, files_before, acl_before, [])
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert (table_digest(db), old_files(db), table_acl(db), market_guards(db)) == (
        before, files_before, acl_before, guards)

result = {"upgrade": "0043->0044", "aiTables": 77,
    "oldRowsDigestPreserved": before, "rendererVersions": list(files_before),
    "rendererBytesPreserved": True, "tableAclPreserved": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReversePreservedFacts": True,
    "parkedOnly": True, "agentOrModelDispatchEnabled": False,
    "productionWrites": False}
(folder / "business-market-v2-parked-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
