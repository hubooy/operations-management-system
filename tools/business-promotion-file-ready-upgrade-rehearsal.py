"""Isolated 0028 -> 0029 storage upgrade and independent backup restores.

The predecessor seed is synthetic and keeps renderer 7 in an unready state.
The real approved ready path is exercised by the separate PostgreSQL tests.
"""
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

from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis.contracts import canonical


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-file-guard-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()):
    raise RuntimeError("Renderer 7 ready upgrade requires exact isolated 0027 seed")
if json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0026->0027":
    raise RuntimeError("Renderer 7 predecessor seed is not verified")
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0027_business_promotion_file_guard','0028_business_finance_v3_gate',"
        "'0029_business_promotion_file_ready') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != ["0027_business_promotion_file_guard"]:
        raise RuntimeError("Exact 0027 predecessor must be applied, without 0028/0029")


BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old_target = [("ai_assistant", "0028_business_finance_v3_gate")]
new_target = [("ai_assistant", "0029_business_promotion_file_ready")]


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    rows = {}
    for table in sorted(AI_TABLES):
        values = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))).fetchall()
        rows[table] = sorted(json.dumps(item[0], sort_keys=True, default=str, ensure_ascii=False)
            for item in values)
    return hashlib.sha256(canonical(rows).encode()).hexdigest()


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer') ORDER BY 1,2,3").fetchall()


def function_bodies(db):
    from importlib import import_module
    definitions = import_module("ai_assistant.migrations.0027_business_promotion_file_guard").NEW_SQL
    names = [value.split("FUNCTION ", 1)[1].split("(", 1)[0] for value in definitions]
    return {name: db.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name]).fetchone()[0]
        for name in names}


def old_file_bytes(db):
    results = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-"+str(version)
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s",
            [run_id]).fetchone()
        if row is None or row[:3] != (version, "ready", 1):
            raise AssertionError("Historical renderer row missing")
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        ordering = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        parts = db.execute("SELECT content,content_digest FROM "+table+
            " WHERE run_id=%s ORDER BY "+ordering, [run_id]).fetchall()
        if not parts or any(hashlib.sha256(bytes(content)).hexdigest() != sha for content, sha in parts):
            raise AssertionError("Historical renderer chunk digest mismatch")
        results[version] = (row[3], hashlib.sha256(b"".join(bytes(raw) for raw, _ in parts)).hexdigest())
    return results


def command(executable, args):
    result = subprocess.run([str(BIN / executable), *args], env={**os.environ,
        "PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
        "PGUSER": str(database["USER"]), "PGPASSWORD": str(database["PASSWORD"])},
        capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        (folder / "promotion-ready-restore-error.log").write_bytes(result.stderr)
        raise RuntimeError("Isolated ready archive operation failed; see private log")


def archive_and_restore(name):
    dump = folder / (name+".dump")
    command("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]])
    command("createdb.exe", [name])
    command("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])


MigrationExecutor(connection).migrate(old_target)
with connect() as owner:
    before = table_digest(owner)
    permissions = grants(owner)
    files = old_file_bytes(owner)
    functions = function_bodies(owner)
    staged = owner.execute("SELECT status,attempt,stored_bytes FROM ai_business_file_runs "
        "WHERE id='promotion-renderer-seven-staged'").fetchone()
    if staged is None or staged[0] != "building" or staged[1] != 1 or staged[2] < 1:
        raise AssertionError("Existing renderer-7 staged seed missing")
archive_and_restore("business_promotion_ready_pre_restore")
with connect("business_promotion_ready_pre_restore") as restored:
    assert table_digest(restored) == before and grants(restored) == permissions
    assert old_file_bytes(restored) == files and function_bodies(restored) == functions

MigrationExecutor(connection).migrate(new_target)
with connect() as owner:
    assert table_digest(owner) == before and grants(owner) == permissions
    assert old_file_bytes(owner) == files
    updated = function_bodies(owner)
    keys = list(functions)
    assert updated[keys[0]] == functions[keys[0]] and updated[keys[1]] == functions[keys[1]]
    try:
        with owner.transaction():
            owner.execute("UPDATE ai_business_file_runs SET status='ready',error_code='',"
                "progress_json='{\"stage\":\"ready\"}',version=version+1 "
                "WHERE id='promotion-renderer-seven-staged'")
    except psycopg.Error as error:
        if "ai_promotion_ready" not in str(error):
            raise AssertionError("Wrong renderer-7 direct ready rejection") from error
    else:
        raise AssertionError("Incomplete synthetic renderer-7 row became ready")
try:
    MigrationExecutor(connection).migrate(old_target)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted a renderer-7 row")
assert MigrationExecutor(connection).migration_plan(new_target) == []
with connect() as owner:
    after = table_digest(owner)
archive_and_restore("business_promotion_ready_post_restore")
with connect("business_promotion_ready_post_restore") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert old_file_bytes(restored) == files and function_bodies(restored) == updated
    assert restored.execute("SELECT status,attempt,stored_bytes FROM ai_business_file_runs "
        "WHERE id='promotion-renderer-seven-staged'").fetchone() == staged

evidence = {"upgrade": "0028->0029", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": before, "postRestoreDigest": after,
    "oldFileVersions": list(files), "oldFileBytesPreserved": True,
    "oldChunkGuardsExact": True, "permissionsUnchanged": True,
    "preBackupRestored": True, "postBackupRestored": True,
    "unverifiedDirectReadyDenied": True, "reverseWithRenderer7Denied": True,
    "validApprovedPublisherExercisedSeparatelyInPgTests": True,
    "publicCreateOrDownloadRegistered": False, "productionWrites": False}
(folder / "business-promotion-file-ready-upgrade-evidence.json").write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False))
