"""Independent isolated 0032 -> 0033 v3 parent seal/restore rehearsal."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant import business_daily_collection_v3 as daily, business_v3_seal as seal
from ai_assistant import models as m, transport
from ai_assistant.policy import AiError, canonical, digest
from ai_assistant.table_manifest import AI_TABLES_PRE_V3_REPORT_INTENTS as AI_TABLES
from business_analysis.contracts import comparison_periods
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v3-tool-receipts-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0031->0032"):
    raise RuntimeError("v3 seal upgrade requires the verified isolated 0032 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0032_business_source_tool_receipts")]
new = [("ai_assistant", "0033_business_v3_parent_seal")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0032_business_source_tool_receipts','0033_business_v3_parent_seal') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0032 predecessor required without 0033")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    material = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True, default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer','teruisi_finance_reader') "
        "ORDER BY 1,2,3").fetchall()


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
             "ai_business_v3_chunk_guard", "ai_business_source_tool_receipt_guard")
    return {name: db.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name]).fetchone()[0] for name in names}


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
            raise RuntimeError("Isolated v3 seal archive operation failed; see private log")


MigrationExecutor(connection).migrate(old)
principal = Principal("finance-v3-pages@example.invalid", "Synthetic", "admin", None)
parent = m.AiBusinessEvidenceRun.objects.get(client_request_id="receipt-upgrade-plan")
legacy = m.AiBusinessEvidenceRun.objects.get(client_request_id="finance-pages-upgrade-plan")
with connect() as db:
    before, permissions, old_files, old_guards = table_digest(db), grants(db), files(db), guards(db)
archive_and_restore("business_v3_seal_before")
with connect("business_v3_seal_before") as restored:
    assert table_digest(restored) == before and grants(restored) == permissions
    assert files(restored) == old_files and guards(restored) == old_guards

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions and files(db) == old_files
    after_guards = guards(db)
    assert after_guards["ai_business_v3_run_guard"] != old_guards["ai_business_v3_run_guard"]
    assert after_guards["ai_business_v3_directory_guard"] != old_guards["ai_business_v3_directory_guard"]
    assert all(after_guards[key] == old_guards[key] for key in
               ("ai_business_v3_chunk_guard", "ai_business_source_tool_receipt_guard"))
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions
    assert files(db) == old_files and guards(db) == old_guards
MigrationExecutor(connection).migrate(new)

try:
    seal.finish(legacy.id, legacy.version, principal)
except AiError as error:
    if error.code != "conflict": raise
else:
    raise AssertionError("Old direct v3 source facts were promoted to a sealed parent")

query = {"platform": "京东", "shop": "测试店", "channel": "京东",
    "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
rows = [{"rowId": "1", "platform": "京东", "shopName": "测试店", "channel": "京东",
         "date": "2026-08-20", "metrics": {"salesCents": 100}}]
page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
    "sourceRevision": "1:2", "source": "erp_sales", "sourceDataset": None,
    "monetaryUnit": "CNY_CENT", "filters": {**query,
        "periods": comparison_periods(query["startDate"], query["endDate"]), "limit": 100},
    "items": rows, "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
    "pageEvidence": {"rowCount": 1, "sha256": digest(rows)},
    "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
    "metricSemantics": None}
catalog = [{"name": daily.INITIAL_TOOL, "risk": "read_only", "allowedRoles": ["admin"],
    "scopePolicy": "unscoped_only",
    "execution": {"mode": "direct", "allowedSurfaces": ["business_collection"]}}]


def execute(name, arguments, actor, **kwargs):
    m.AiToolAuditLogs.objects.create(id="seal-upgrade-audit", request_id=kwargs["request_id"],
        invocation_id="seal-upgrade-invocation", actor_email=principal.email,
        actor_role="admin", surface="business_collection", tool_name=name,
        arguments_json="{}", status="succeeded", duration_ms=1,
        response_digest=digest(canonical(page)))
    return {"ok": True, "toolName": name, "data": page}


with patch.object(transport, "catalog", return_value=catalog), \
        patch.object(transport, "execute_tool", side_effect=execute):
    daily.advance_daily_source(parent.id, "daily", parent.version, principal, "seal-upgrade-daily")
parent.refresh_from_db()
result = seal.finish(parent.id, parent.version, principal)
assert result["status"] == "sealed" and seal.verify(parent.id, principal)["seal"] == result["seal"]
with connect() as db:
    after = table_digest(db)
    assert grants(db) == permissions and files(db) == old_files and guards(db) == after_guards
    assert db.execute("SELECT status FROM ai_business_evidence_runs WHERE id=%s", [parent.id]).fetchone()[0] == "sealed"
archive_and_restore("business_v3_seal_after")
with connect("business_v3_seal_after") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert files(restored) == old_files and guards(restored) == after_guards
    assert restored.execute("SELECT status FROM ai_business_evidence_runs WHERE id=%s", [parent.id]).fetchone()[0] == "sealed"
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError as error:
    if "存在已封存v3收据来源" not in str(error): raise
else:
    raise AssertionError("Reverse migration accepted a sealed v3 parent")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0032->0033", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": before, "afterDigest": after,
    "rendererVersions": list(old_files), "rendererBytesPreserved": True,
    "permissionsUnchanged": True, "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReversePassed": True, "sealedReverseDenied": True,
    "oldDirectV3FactsRejected": True, "sealedParentReplayed": True,
    "crossDomainAtomicSnapshot": False, "upstreamSignatureVerified": False,
    "publicReportOrModelRegistered": False, "productionWrites": False}
(folder / "business-v3-parent-seal-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
