"""Independent isolated 0033 -> 0034 paused v3 intent/restore rehearsal."""
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

from ai_assistant import business_v3_report_intent as intent, business_v3_seal as seal
from ai_assistant import models as m
from ai_assistant.database_contract import provision
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_LEDGER as AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_V3_REPORT_INTENTS
from ai_assistant.policy import canonical
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v3-parent-seal-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0032->0033"):
    raise RuntimeError("v3 intent upgrade requires the verified isolated 0033 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0033_business_v3_parent_seal")]
new = [("ai_assistant", "0034_business_v3_report_intent")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0033_business_v3_parent_seal','0034_business_v3_report_intent') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0033 predecessor required without 0034")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db, tables):
    material = {}
    for table in sorted(tables):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True, default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def grants(db, tables):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer','teruisi_finance_reader') "
        "AND table_name=ANY(%s) ORDER BY 1,2,3", [list(tables)]).fetchall()


def files(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-" + str(version)
        parent = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s",
                            [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table + " WHERE run_id=%s ORDER BY " + order,
                            [run_id]).fetchall()
        if parent is None or parent[:3] != (version, "ready", 1) or not chunks or any(
                hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
            raise AssertionError("Historical renderer bytes absent")
        result[version] = (parent[3], hashlib.sha256(b"".join(bytes(data) for data, _ in chunks)).hexdigest())
    staged = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs "
        "WHERE id='promotion-renderer-seven-staged'").fetchone()
    chunks = db.execute("SELECT content,content_digest FROM ai_business_volume_chunks "
        "WHERE run_id='promotion-renderer-seven-staged' ORDER BY volume_index,format,sequence").fetchall()
    if staged is None or staged[:3] != (7, "building", 1) or not chunks or any(
            hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
        raise AssertionError("Renderer 7 staged bytes absent")
    result[7] = (staged[3], hashlib.sha256(b"".join(bytes(data) for data, _ in chunks)).hexdigest())
    return result


def guards(db):
    names = ("ai_business_v3_run_guard", "ai_business_v3_directory_guard",
             "ai_business_source_tool_receipt_guard")
    return {name: db.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name]).fetchone()[0]
            for name in names}


def archive_and_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
                   "PGUSER": str(database["USER"]), "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        completed = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            (folder / (name + "-error.log")).write_bytes(completed.stderr)
            raise RuntimeError("Isolated v3 intent archive operation failed; see private log")


MigrationExecutor(connection).migrate(old)
principal = Principal("finance-v3-pages@example.invalid", "Synthetic", "admin", None)
parent = m.AiBusinessEvidenceRun.objects.get(client_request_id="receipt-upgrade-plan")
verified = seal.verify(parent.id, principal)
job_counts = (m.AiReportRun.objects.count(), m.AiWorkflowRuns.objects.count(),
              m.AiAgentJobs.objects.count(), m.AiAgentProviderDispatches.objects.count())
with connect() as db:
    before = table_digest(db, AI_TABLES_PRE_V3_REPORT_INTENTS)
    permissions, old_files, old_guards = grants(db, AI_TABLES_PRE_V3_REPORT_INTENTS), files(db), guards(db)
archive_and_restore("business_v3_intent_before")
with connect("business_v3_intent_before") as restored:
    assert table_digest(restored, AI_TABLES_PRE_V3_REPORT_INTENTS) == before
    assert grants(restored, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions
    assert files(restored) == old_files and guards(restored) == old_guards

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == before
    assert grants(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions
    assert files(db) == old_files and guards(db) == old_guards
    assert db.execute("SELECT count(*) FROM ai_business_v3_report_intents").fetchone()[0] == 0
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == before
    assert grants(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions
    assert files(db) == old_files and guards(db) == old_guards
MigrationExecutor(connection).migrate(new)
with connect() as db:
    provision(db, secrets.token_hex(32), secrets.token_hex(32))
with connect() as db:
    assert grants(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions
    new_grants = grants(db, ("ai_business_v3_report_intents",))
    assert ("teruisi_ai_reader", "ai_business_v3_report_intents", "SELECT") in new_grants
    assert ("teruisi_ai_writer", "ai_business_v3_report_intents", "INSERT") in new_grants
    assert ("teruisi_ai_writer", "ai_business_v3_report_intents", "UPDATE") not in new_grants

created = intent.create({"schemaVersion": intent.REQUEST_SCHEMA,
    "clientRequestId": "intent-upgrade-plan", "executionProfile": "business-agent-reference-v3-candidate",
    "evidenceRunId": parent.id, "expectedEvidenceVersion": verified["version"],
    "expectedSealDigest": verified["seal"]["sealedDigest"]}, principal)
assert created["item"]["status"] == "paused"
loaded = intent.inspect(created["item"]["id"], principal)
assert loaded["workflowPlan"]["humanReviewRequired"] is True
assert len(loaded["workflowPlan"]["nodes"]) == 6
assert job_counts == (m.AiReportRun.objects.count(), m.AiWorkflowRuns.objects.count(),
                      m.AiAgentJobs.objects.count(), m.AiAgentProviderDispatches.objects.count())
with connect() as db:
    after = table_digest(db, AI_TABLES)
    assert grants(db, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions and files(db) == old_files
    assert guards(db) == old_guards
archive_and_restore("business_v3_intent_after")
with connect("business_v3_intent_after") as restored:
    assert table_digest(restored, AI_TABLES) == after
    assert grants(restored, AI_TABLES_PRE_V3_REPORT_INTENTS) == permissions
    assert grants(restored, ("ai_business_v3_report_intents",)) == new_grants
    assert files(restored) == old_files and guards(restored) == old_guards
    assert restored.execute("SELECT status,pause_reason FROM ai_business_v3_report_intents").fetchone() == (
        "paused", "v3_agents_not_registered")
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError as error:
    if "存在v3暂停报告意图" not in str(error): raise
else:
    raise AssertionError("Reverse migration discarded a persisted v3 intent")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0033->0034", "oldAiTables": len(AI_TABLES_PRE_V3_REPORT_INTENTS),
    "newAiTables": len(AI_TABLES), "oldRowsDigestPreserved": before, "afterDigest": after,
    "rendererVersions": list(old_files), "rendererBytesPreserved": True,
    "oldAclUnchanged": True, "newIntentAcl": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReversePassed": True, "factsReverseDenied": True,
    "pausedWorkflowStaged": True, "humanReviewRequired": True,
    "modelJobsCreated": False, "publicReportOrFileRegistered": False,
    "productionWrites": False}
(folder / "business-v3-report-intent-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
