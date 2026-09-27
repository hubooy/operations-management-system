"""Independent isolated 0039 -> 0040 narrow-stream upgrade and restore."""
import argparse
import hashlib
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

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_TICKETS as AI_TABLES
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-sealer-ledger-read-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0038->0039"):
    raise RuntimeError("0040 rehearsal requires verified isolated 0039 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0039_business_v4_sealer_ledger_read"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0040_business_v4_sealer_narrow_stream"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0039_business_v4_sealer_ledger_read',"
        "'0040_business_v4_sealer_narrow_stream') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("Exact 0039 predecessor required without 0040")


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


def acl(db, *, sealer):
    roles = ["teruisi_ai_reader", "teruisi_ai_writer"]
    if sealer: roles.append("teruisi_ai_seal_writer")
    table = db.execute("SELECT grantee,table_name,privilege_type FROM "
        "information_schema.role_table_grants WHERE grantee=ANY(%s) "
        "AND table_name=ANY(%s) ORDER BY 1,2,3",
        [roles, list(AI_TABLES)]).fetchall()
    columns = db.execute("SELECT grantee,table_name,column_name,privilege_type "
        "FROM information_schema.column_privileges WHERE grantee=ANY(%s) "
        "AND table_name IN ('ai_write_authority','ai_data_revisions',"
        "'access_control_users','ai_tool_audit_logs') ORDER BY 1,2,3,4",
        [roles]).fetchall()
    return table, columns


def files(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-" + str(version)
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        if row is None or row[:3] != (version, "ready", 1) or not chunks or any(
                hashlib.sha256(bytes(blob)).hexdigest() != sha for blob, sha in chunks):
            raise AssertionError("old renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
        "FROM ai_business_file_runs WHERE id='promotion-renderer-seven-staged'").fetchone()
    chunks = db.execute("SELECT content,content_digest FROM ai_business_volume_chunks "
        "WHERE run_id='promotion-renderer-seven-staged' "
        "ORDER BY volume_index,format,sequence").fetchall()
    if row is None or row[:3] != (7, "building", 1) or not chunks or any(
            hashlib.sha256(bytes(blob)).hexdigest() != sha for blob, sha in chunks):
        raise AssertionError("renderer 7 bytes absent")
    result[7] = (row[3], hashlib.sha256(b"".join(bytes(blob)
        for blob, _ in chunks)).hexdigest())
    return result


def archive_and_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        completed = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            (folder / (name + "-error.log")).write_bytes(completed.stderr)
            raise RuntimeError("isolated 0040 archive/restore failed")


def function_acl(db):
    signatures = (
        "public.ai_v4_sealer_read_context(text,text,text,bigint)",
        "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
        "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)")
    return tuple(db.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
        "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
        "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
        ["teruisi_ai_seal_writer", signature, signature, signature]).fetchone()
        for signature in signatures)


with connect() as db:
    before = table_digest(db)
    old_acl, old_sealer_acl, old_files = acl(db, sealer=False), acl(db, sealer=True), files(db)
    assert len(AI_TABLES) == 74
    assert db.execute("SELECT count(*) FROM ai_business_v4_seals").fetchone()[0] == 1
archive_and_restore("business_v4_sealer_narrow_before")
with connect("business_v4_sealer_narrow_before") as restored:
    assert table_digest(restored) == before and acl(restored, sealer=True) == old_sealer_acl
    assert files(restored) == old_files

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert table_digest(db) == before and acl(db, sealer=False) == old_acl
    assert files(db) == old_files and function_acl(db) == ((True, False, False),)*3
    assert all(db.execute("SELECT has_any_column_privilege(%s,%s,'SELECT')",
        ["teruisi_ai_seal_writer", "public."+table]).fetchone()[0] is False
        for table in ("ai_business_v4_chunks","ai_business_v4_tool_receipts",
            "ai_business_v4_validation_attempts",
            "ai_business_v4_validation_segments","ai_business_v4_seals"))
    assert db.execute("SELECT has_any_column_privilege("
        "'teruisi_ai_seal_writer','public.ai_tool_audit_logs','SELECT'),"
        "has_any_column_privilege('teruisi_ai_seal_writer',"
        "'public.access_control_users','SELECT')").fetchone() == (False, False)
    new_acl = acl(db, sealer=True)
archive_and_restore("business_v4_sealer_narrow_after")
with connect("business_v4_sealer_narrow_after") as restored:
    assert table_digest(restored) == before and acl(restored, sealer=True) == new_acl
    assert files(restored) == old_files and function_acl(restored) == ((True, False, False),)*3

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert table_digest(db) == before and acl(db, sealer=True) == old_sealer_acl
    assert files(db) == old_files
    assert db.execute("SELECT to_regprocedure('public.ai_v4_sealer_read_context("
        "text,text,text,bigint)')").fetchone() == (None,)
    assert db.execute("SELECT count(*) FROM ai_business_v4_seals").fetchone() == (1,)
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert table_digest(db) == before and acl(db, sealer=True) == new_acl
    assert function_acl(db) == ((True, False, False),)*3

result = {"upgrade": "0039->0040", "oldAiTables": 74, "newAiTables": 74,
    "oldRowsDigestPreserved": before, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldBackupRestored": True,
    "newBackupRestored": True, "oldRolesAclUnchanged": True,
    "emptyAndSealedReversePreservedFacts": True,
    "allFivePhysicalTablesDirectSelectDenied": True,
    "runBoundCapabilityVerified": False,
    "noLoginDefault": True, "sealerCliOrProductionActivated": False,
    "productionWrites": False}
(folder / "business-v4-sealer-narrow-stream-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
