"""Independent 0026 -> 0027 file guard upgrade over a synthetic 0026 seed.

The seed is created by business-promotion-profile-upgrade-rehearsal.py in the
same isolated cluster. This script takes its own pre/post archives and verifies
historical bytes, renderer-7 staging, rejection, and independent restoration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
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

from ai_assistant import business_evidence, models as m
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis.contracts import canonical
from sales.auth import Principal


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-profile-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()):
    raise RuntimeError("File guard rehearsal requires its exact isolated 0026 seed")
seed_result = json.loads(seed.read_text(encoding="utf-8"))
if seed_result.get("upgrade") != "0025->0026" or seed_result.get("oldFileVersions") != list(range(1, 7)):
    raise RuntimeError("Predecessor seed has not established renderer 1-6 byte preservation")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old_target = [("ai_assistant", "0026_business_promotion_profile")]
new_target = [("ai_assistant", "0027_business_promotion_file_guard")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0026_business_promotion_profile','0027_business_promotion_file_guard') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old_target[0][1]]:
        raise RuntimeError("Exact 0026 predecessor must be applied, without 0027")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    rows = {}
    for table in sorted(AI_TABLES):
        items = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))).fetchall()
        rows[table] = sorted(json.dumps(item[0], sort_keys=True, default=str, ensure_ascii=False)
            for item in items)
    return hashlib.sha256(canonical(rows).encode()).hexdigest()


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer') ORDER BY 1,2,3").fetchall()


def function_bodies(db):
    names = [item.split("FUNCTION ", 1)[1].split("(", 1)[0] for item in
        __import__("importlib").import_module("ai_assistant.migrations.0025_business_file_opc").NEW_SQL]
    return {name: db.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name]).fetchone()[0]
        for name in names}


def file_bytes(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-"+str(version)
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s",
            [run_id]).fetchone()
        if row is None or row[:3] != (version, "ready", 1):
            raise AssertionError("Historical file row missing")
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        parts = db.execute("SELECT content,content_digest FROM "+table+
            " WHERE run_id=%s ORDER BY "+order, [run_id]).fetchall()
        if not parts or any(hashlib.sha256(bytes(raw)).hexdigest() != sha for raw, sha in parts):
            raise AssertionError("Historical file chunk digest mismatch")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(raw) for raw, _ in parts)).hexdigest(), len(parts))
    return result


def command(executable, args):
    result = subprocess.run([str(BIN / executable), *args], env={**os.environ,
        "PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
        "PGUSER": str(database["USER"]), "PGPASSWORD": str(database["PASSWORD"])},
        capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        (folder / "promotion-file-restore-error.log").write_bytes(result.stderr)
        raise RuntimeError("Isolated renderer-7 archive operation failed; see private log")


def archive_and_restore(name):
    dump = folder / (name+".dump")
    command("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]])
    command("createdb.exe", [name])
    command("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])


def denied(db, statement, params=(), *, contains):
    try:
        with db.transaction():
            db.execute(statement, params)
    except psycopg.Error as error:
        if contains not in str(error):
            raise AssertionError("Wrong renderer-7 rejection") from error
        return
    raise AssertionError("Forbidden renderer-7 action accepted")


with connect() as owner:
    before = table_digest(owner)
    permissions = grants(owner)
    historical = file_bytes(owner)
    original_functions = function_bodies(owner)
    report = owner.execute("SELECT id,snapshot_json FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile'="
        "'business-agent-screening-promotion-reference-v1' LIMIT 1").fetchone()
    old = owner.execute("SELECT id FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile'<>"
        "'business-agent-screening-promotion-reference-v1' LIMIT 1").fetchone()
    if report is None or old is None:
        raise AssertionError("Both new and old reports are required")
    snapshot = json.loads(report[1])
    principal = Principal("evidence@example.invalid", "Synthetic", "admin", None)
    source_page = business_evidence.analysis_table(snapshot["evidenceRunId"],
        {"sourceKey": snapshot["promotionSelector"]["sourceKey"], "dimension": "sku"}, principal)

archive_and_restore("business_promotion_file_pre_restore")
with connect("business_promotion_file_pre_restore") as restored:
    assert table_digest(restored) == before and file_bytes(restored) == historical
    assert function_bodies(restored) == original_functions and grants(restored) == permissions

MigrationExecutor(connection).migrate(new_target)
with connect() as owner:
    assert table_digest(owner) == before and file_bytes(owner) == historical and grants(owner) == permissions
    changed = function_bodies(owner)
    names = list(original_functions)
    assert changed[names[0]] == original_functions[names[0]]
    assert changed[names[2]] == original_functions[names[2]]
MigrationExecutor(connection).migrate(old_target)
with connect() as owner:
    assert table_digest(owner) == before and function_bodies(owner) == original_functions
MigrationExecutor(connection).migrate(new_target)

run_id = "promotion-renderer-seven-staged"
binding = hashlib.sha256(run_id.encode()).hexdigest()
with connect() as owner:
    denied(owner, "INSERT INTO ai_business_file_runs(id,owner_email,scope_json,report_id,draft,renderer_version,"
        "binding_digest,status,version,attempt,lease_until,stored_bytes,progress_json,manifest_json,error_code,created_at) "
        "VALUES('promotion-seven-wrong-parent','evidence@example.invalid','null',%s,true,7,%s,'queued',1,0,now(),0,'{}','{}','',now())",
        [old[0], binding], contains="ai_promotion_file_parent_invalid")
    raw = b"synthetic-unpublished-renderer-seven"
    with owner.transaction():
        owner.execute("INSERT INTO ai_business_file_runs(id,owner_email,scope_json,report_id,draft,renderer_version,"
            "binding_digest,status,version,attempt,lease_until,stored_bytes,progress_json,manifest_json,error_code,created_at) "
            "VALUES(%s,'evidence@example.invalid','null',%s,true,7,%s,'queued',1,0,now(),0,'{}','{}','',now())",
            [run_id, report[0], binding])
        owner.execute("UPDATE ai_business_file_runs SET status='building',version=2,attempt=1 WHERE id=%s", [run_id])
        owner.execute("INSERT INTO ai_business_volume_chunks(id,run_id,attempt,volume_index,format,sequence,content,content_digest,created_at) "
            "VALUES('promotion-seven-chunk',%s,1,1,'html',1,%s,%s,now())",
            [run_id, raw, hashlib.sha256(raw).hexdigest()])
        owner.execute("UPDATE ai_business_file_runs SET stored_bytes=%s,version=3 WHERE id=%s", [len(raw), run_id])
    denied(owner, "UPDATE ai_business_file_runs SET status='ready',version=4 WHERE id=%s", [run_id],
        contains="ai_promotion_renderer_unpublished")
    assert owner.execute("SELECT status,version,stored_bytes FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone() == (
        "building", 3, len(raw))
    after = table_digest(owner)
    assert grants(owner) == permissions and file_bytes(owner) == historical
try:
    MigrationExecutor(connection).migrate(old_target)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted renderer 7")
assert MigrationExecutor(connection).migration_plan(new_target) == []

archive_and_restore("business_promotion_file_post_restore")
with connect("business_promotion_file_post_restore") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert file_bytes(restored) == historical and function_bodies(restored) == changed
    assert restored.execute("SELECT status,version,stored_bytes FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone() == (
        "building", 3, len(raw))
    assert restored.execute("SELECT snapshot_json FROM ai_report_runs WHERE id=%s", [report[0]]).fetchone()[0] == report[1]

original_name = connection.settings_dict["NAME"]
try:
    connection.close()
    connection.settings_dict["NAME"] = "business_promotion_file_post_restore"
    restored_page = business_evidence.analysis_table(snapshot["evidenceRunId"],
        {"sourceKey": snapshot["promotionSelector"]["sourceKey"], "dimension": "sku"}, principal)
    assert canonical(restored_page) == canonical(source_page)
    assert m.AiReportRun.objects.get(pk=report[0]).snapshot_json == report[1]
finally:
    connection.close()
    connection.settings_dict["NAME"] = original_name

evidence = {"upgrade": "0026->0027", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": before, "postRestoreDigest": after,
    "oldFileVersions": list(historical), "oldFileBytesPreserved": True,
    "oldManifestAndLegacyChunkGuardsExact": True, "permissionsUnchanged": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReverseAndReupgrade": True, "reverseWithRenderer7Denied": True,
    "newProfileOnly": True, "renderer7ChunksStaged": True, "renderer7ReadyRejected": True,
    "restoredOwningPageEqualsSource": True, "producerRegistered": False,
    "modelCalled": False, "productionWrites": False}
(folder / "business-promotion-file-guard-upgrade-evidence.json").write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False))
