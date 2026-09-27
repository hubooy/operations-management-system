"""Independent isolated 0031 -> 0032 receipt and backup/restore rehearsal.

The receipt fixture uses a synthetic successful tool audit. It proves storage
binding, not an upstream JD/ERP/finance signature or live-source authority.
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
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ai_assistant import business_evidence_v3 as plan, business_v3_tool_receipts as receipts, models as m
from ai_assistant.database_contract import provision
from ai_assistant.table_manifest import AI_TABLES_PRE_V3_REPORT_INTENTS as AI_TABLES
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS
from ai_assistant.policy import AiError, canonical, digest
from business_analysis import finance_collection_state as verifier
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v3-daily-pages-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0030->0031"):
    raise RuntimeError("Receipt upgrade requires the verified isolated 0031 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0031_business_daily_v3_source_pages")]
new = [("ai_assistant", "0032_business_source_tool_receipts")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0031_business_daily_v3_source_pages','0032_business_source_tool_receipts') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0031 predecessor required without 0032")


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
    names = ("ai_business_v3_run_guard", "ai_business_source_guard",
             "ai_business_v3_directory_guard", "ai_business_v3_chunk_guard")
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
            raise RuntimeError("Isolated receipt archive operation failed; see private log")


MigrationExecutor(connection).migrate(old)
principal = Principal("finance-v3-pages@example.invalid", "Synthetic", "admin", None)
legacy = m.AiBusinessEvidenceRun.objects.get(client_request_id="finance-pages-upgrade-plan")
with connect() as db:
    before = table_digest(db, AI_TABLES_PRE_TOOL_RECEIPTS)
    permissions, old_files, old_guards = grants(db, AI_TABLES_PRE_TOOL_RECEIPTS), files(db), guards(db)
archive_and_restore("business_v3_receipts_before")
with connect("business_v3_receipts_before") as restored:
    assert table_digest(restored, AI_TABLES_PRE_TOOL_RECEIPTS) == before
    assert grants(restored, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions
    assert files(restored) == old_files and guards(restored) == old_guards

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_TOOL_RECEIPTS) == before
    assert grants(db, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions
    assert files(db) == old_files and guards(db) == old_guards
    assert db.execute("SELECT count(*) FROM ai_business_source_tool_receipts").fetchone()[0] == 0
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_TOOL_RECEIPTS) == before
    assert grants(db, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions
    assert files(db) == old_files and guards(db) == old_guards
MigrationExecutor(connection).migrate(new)
with connect() as db:
    provision(db, secrets.token_hex(32), secrets.token_hex(32))
with connect() as db:
    assert grants(db, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions
    new_grants = grants(db, ("ai_business_source_tool_receipts",))
    assert ("teruisi_ai_reader", "ai_business_source_tool_receipts", "SELECT") in new_grants
    assert ("teruisi_ai_writer", "ai_business_source_tool_receipts", "INSERT") in new_grants
    assert ("teruisi_ai_writer", "ai_business_source_tool_receipts", "UPDATE") not in new_grants

_, query, publication, rows = finance_fixture()
body = {"schemaVersion": "business-evidence-v3", "clientRequestId": "receipt-upgrade-plan",
    "sources": [{"key": "daily", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
        "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}},
        {"key": "finance", "domain": "finance", "query": query}],
    "analysisRequest": {"schemaVersion": "business-analysis-request-v1", "question": "收据存储验证",
        "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}
run_id = plan.create(body, principal)["item"]["id"]
page = owned_page(query, publication, rows, offset=0, total=len(rows))
checkpoint = verifier.consume(None, page, trusted_query=query)
raw = canonical(page)
request_id = "receipt-upgrade-step"
with transaction.atomic():
    parent = m.AiBusinessEvidenceRun.objects.get(pk=run_id)
    source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance")
    audit = m.AiToolAuditLogs.objects.create(id="receipt-upgrade-audit", request_id=request_id,
        invocation_id="receipt-upgrade-invocation", actor_email=principal.email, actor_role="admin",
        surface="business_collection", tool_name=receipts.FINANCE_TOOL, arguments_json="{}",
        status="succeeded", duration_ms=1, response_digest=digest(raw))
    chunk = m.AiBusinessEvidenceChunk.objects.create(id="receipt-upgrade-chunk", run=parent,
        source_key=source.source_key, sequence=1, payload_json=raw, payload_digest=digest(raw))
    receipts.bind_page(parent=parent, source=source, chunk=chunk, principal=principal,
        request_id=request_id, tool_name=receipts.FINANCE_TOOL, page=page, encoded=raw,
        audit={"id": audit.id, "invocation_id": audit.invocation_id})
    m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
        page_count=1, stored_bytes=len(raw.encode("utf-8")), row_count=checkpoint["rowsRead"],
        finished=checkpoint["finished"], checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
    m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode("utf-8")))
assert receipts.require_complete(run_id, "finance", principal)["receiptCount"] == 1
try:
    receipts.require_complete(legacy.id, "finance-context", principal)
except AiError as error:
    if error.code != "conflict" or "未绑定" not in str(error):
        raise
else:
    raise AssertionError("Old direct finance fact was silently granted a tool receipt")
with connect() as db:
    after = table_digest(db, AI_TABLES)
    assert grants(db, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions and files(db) == old_files
    assert guards(db) == old_guards
    receipt_guard = db.execute("SELECT prosrc FROM pg_proc WHERE proname='ai_business_source_tool_receipt_guard'").fetchone()
    assert receipt_guard and "response_digest" in receipt_guard[0]
    assert db.execute("SELECT status FROM ai_business_evidence_runs WHERE id=%s", [run_id]).fetchone()[0] == "collecting"
archive_and_restore("business_v3_receipts_after")
with connect("business_v3_receipts_after") as restored:
    assert table_digest(restored, AI_TABLES) == after
    assert grants(restored, AI_TABLES_PRE_TOOL_RECEIPTS) == permissions
    assert grants(restored, ("ai_business_source_tool_receipts",)) == new_grants
    assert files(restored) == old_files and guards(restored) == old_guards
    assert restored.execute("SELECT count(*) FROM ai_business_source_tool_receipts").fetchone()[0] == 1
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError as error:
    if "存在v3签名工具收据" not in str(error):
        raise
else:
    raise AssertionError("Reverse migration discarded persisted v3 tool receipts")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0031->0032", "oldAiTables": len(AI_TABLES_PRE_TOOL_RECEIPTS),
    "newAiTables": len(AI_TABLES), "oldRowsDigestPreserved": before, "afterDigest": after,
    "rendererVersions": list(old_files), "rendererBytesPreserved": True,
    "oldAclUnchanged": True, "newReceiptAcl": True, "oldBackupRestored": True,
    "newBackupRestored": True, "emptyReversePassed": True, "factsReverseDenied": True,
    "legacyDirectChunksUnverified": True, "syntheticAuditBoundReceipt": True,
    "parentStatus": "collecting", "realSourceAuthority": False,
    "sealOrReportEnabled": False, "productionWrites": False}
(folder / "business-v3-tool-receipts-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
