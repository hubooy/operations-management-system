"""Isolated 0070 -> 0071 closed v4/report link upgrade and dual restore."""
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
    AI_TABLES, AI_FULL_TABLES_PRE_V4_REPORT_LINKS as OLD_TABLES,
    AI_FULL_TABLES_AFTER_V4_REPORT_LINKS as NEW_TABLES)
from ai_assistant import business_v4_report_link_sql as link
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-promotion-budget-v11-identity-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or seed.get("upgrade") != "0069->0070"
        or seed.get("beforeBackupRestored") is not True
        or seed.get("afterBackupRestored") is not True
        or seed.get("emptyReverseAndReapply") is not True
        or len(AI_TABLES) != 89
        or len(OLD_TABLES) != 91 or len(NEW_TABLES) != 93):
    raise RuntimeError("0071 requires independently restored isolated 0070 seed")

OLD = [("ai_assistant", "0070_business_promotion_budget_v11_limited_identity"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0071_business_v4_report_source_link"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0070_business_promotion_budget_v11_limited_identity',"
        "'0071_business_v4_report_source_link') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0071 requires exact 0070 predecessor")

candidate = importlib.import_module(
    "ai_assistant.migrations.0071_business_v4_report_source_link")
identity = importlib.import_module(
    "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def old_rows(db):
    content = {}
    for table in OLD_TABLES:
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        content[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()


def old_files(db):
    values = {}
    for run_id, version, status, attempt, manifest in db.execute(
            "SELECT id,renderer_version,status,attempt,manifest_json "
            "FROM public.ai_business_file_runs WHERE renderer_version<=11 "
            "ORDER BY id").fetchall():
        pieces = []
        for table, order in (("ai_business_file_chunks", "format,sequence"),
                             ("ai_business_volume_chunks",
                              "volume_index,format,sequence")):
            parts = db.execute("SELECT content,content_digest FROM public."+
                table+" WHERE run_id=%s ORDER BY "+order, [run_id]).fetchall()
            if any(hashlib.sha256(bytes(blob)).hexdigest() != saved
                    for blob, saved in parts):
                raise AssertionError("old file chunk digest mismatch")
            pieces.append((table, len(parts), hashlib.sha256(b"".join(
                bytes(blob) for blob, _ in parts)).hexdigest()))
        values[run_id] = (version, status, attempt, manifest, tuple(pieces))
    if not {1, 2, 3, 4, 5, 6, 7} <= {item[0] for item in values.values()}:
        raise AssertionError("old renderer 1-7 seed missing")
    return values


def functions(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1"
        ).fetchall()
    return {name: tuple(details) for name, *details in rows}


def privileges(db):
    result = {}
    for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
        for table in OLD_TABLES:
            result[role, table] = tuple(db.execute(
                "SELECT has_table_privilege(%s,%s,%s)",
                [role, "public."+table, privilege]).fetchone()[0]
                for privilege in ("SELECT","INSERT","UPDATE","DELETE",
                    "TRUNCATE","REFERENCES")) + tuple(db.execute(
                "SELECT has_any_column_privilege(%s,%s,%s)",
                [role,"public."+table,privilege]).fetchone()[0]
                for privilege in ("SELECT","INSERT","UPDATE","REFERENCES"))
    return result


def relations(db):
    return db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p','i','I') "
        "ORDER BY c.relname").fetchall(), db.execute(
        "SELECT t.oid,c.relname,t.tgname,t.tgfoid::regprocedure::text,"
        "t.tgtype,t.tgenabled,t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
        "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname").fetchall()


def snapshot(db):
    return old_rows(db), old_files(db), functions(db), privileges(db), relations(db)


def normalized(value):
    return (value[:2] + ({key: item[1:] for key,item in value[2].items()},)
        + (value[3],) + ((tuple(row[1:] for row in value[4][0]),
            tuple(row[1:] for row in value[4][1])),))


def archive_restore(name):
    archive = folder / (name + ".dump")
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe",
            ["-Fc", "-f", str(archive), database["NAME"]]),
            ("createdb.exe", [name]),
            ("pg_restore.exe", ["--exit-on-error", "-d", name, str(archive)])):
        done = subprocess.run([str(BIN / executable), *args], env=env,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if done.returncode:
            (folder / (name + "-error.log")).write_bytes(done.stderr)
            raise RuntimeError("isolated 0071 archive/restore failed")


def verify_old(db):
    for table in (link.INTENTS, link.LINKS):
        if db.execute("SELECT to_regclass(%s)", [table]).fetchone()[0]:
            raise AssertionError("0071 table exists before upgrade/after reverse")
    for signature in (link.BINDINGS, link.ISSUE, link.CREATE_REPORT, link.READ,
            "public.ai_v4_report_link_row_guard()",
            "public.ai_v4_report_link_after_insert()"):
        if db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]:
            raise AssertionError("0071 function exists before upgrade/after reverse")
    with db.cursor() as cursor:
        identity.verify_catalog(cursor)


def verify_new(db, before, after, *, restored_db=False):
    if after[0:2] != before[0:2] or after[3] != before[3]:
        raise AssertionError("0071 changed old 91 rows, file bytes or grants")
    for name, details in before[2].items():
        current = after[2].get(name)
        if current is None or (current[1:] if restored_db else current) != (
                details[1:] if restored_db else details):
            raise AssertionError("0071 changed old AI function identity/body/ACL")
    old_rel,old_triggers=before[4]
    new_rel,new_triggers=after[4]
    if not {tuple(row[1:] if restored_db else row) for row in old_rel} <= {
            tuple(row[1:] if restored_db else row) for row in new_rel}:
        raise AssertionError("0071 changed old relation identity")
    if not {tuple(row[1:] if restored_db else row) for row in old_triggers} <= {
            tuple(row[1:] if restored_db else row) for row in new_triggers}:
        raise AssertionError("0071 changed old trigger identity")
    if set(after[2])-set(before[2]) != {db.execute(
            "SELECT to_regprocedure(%s)::text", [signature]).fetchone()[0]
            for signature in (link.BINDINGS, link.ISSUE,
                link.CREATE_REPORT, link.READ,
                "public.ai_v4_report_link_row_guard()",
                "public.ai_v4_report_link_after_insert()") }:
        raise AssertionError("0071 unexpected function inventory")
    with db.cursor() as cursor:
        candidate.verify_catalog(cursor)
        identity.verify_catalog(cursor)
    for table in (link.INTENTS, link.LINKS):
        if db.execute("SELECT count(*) FROM " + table).fetchone() != (0,):
            raise AssertionError("0071 staged link must start empty")


with connect() as db:
    verify_old(db)
    before = snapshot(db)
archive_restore("v4_report_link_before")
with connect("v4_report_link_before") as copy:
    verify_old(copy)
    if normalized(snapshot(copy)) != normalized(before):
        raise AssertionError("0070 independent pre-upgrade restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    after = snapshot(db)
    verify_new(db,before,after)
archive_restore("v4_report_link_after")
with connect("v4_report_link_after") as copy:
    recovered = snapshot(copy)
    verify_new(copy,before,recovered,restored_db=True)
    if normalized(recovered) != normalized(after):
        raise AssertionError("0071 post-upgrade restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    verify_old(db)
    if snapshot(db) != before:
        raise AssertionError("0071 empty reverse changed old schema/data")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    replayed = snapshot(db)
    verify_new(db,before,replayed)
    if normalized(replayed) != normalized(after):
        raise AssertionError("0071 empty reapply differs")

result = {"upgrade": "0070->0071",
    "ormMappedAiTables": len(AI_TABLES),
    "oldPhysicalAiTables": len(OLD_TABLES),
    "newPhysicalAiTables": len(NEW_TABLES),
    "oldRowsDigestPreserved": before[0],
    "oldRendererRowsAndChunksPreserved": True,
    "oldAiFunctionOidBodyAclOwnerPreserved": True,
    "readerAndWriterTablePrivilegesUnchanged": True,
    "newAppendOnlyLinkTablesEmpty": True,
    "creationTimeBackfillDisabled": True,
    "authorityVerified": False,
    "reportGenerationSupported": False,
    "rendererAndDownloadDisabled": True,
    "providerCallsAllowed": False,
    "realRoleTest": "ai_assistant.test_business_v4_report_link_role",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-v4-report-link-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
