"""Independent 0030 -> 0031 daily v3 physical guard and restore rehearsal.

Requires a verified 0030 finance source and old renderer 1-7 bytes in the same
isolated cluster. The daily page is synthetic physical evidence, not a live JD
or ERP fetch.
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
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ai_assistant import business_daily_collection_v3 as daily_reader, business_finance_collection_v3 as finance_reader
from ai_assistant import models as m
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis.contracts import PageReconciler, canonical, comparison_periods, digest as page_digest
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-finance-v3-pages-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0029->0030"):
    raise RuntimeError("v3 daily upgrade requires the verified isolated 0030 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0030_business_finance_source_pages")]
new = [("ai_assistant", "0031_business_daily_v3_source_pages")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0030_business_finance_source_pages','0031_business_daily_v3_source_pages') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0030 predecessor required without 0031")


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
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer','teruisi_finance_reader') ORDER BY 1,2,3").fetchall()


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
            raise RuntimeError("Isolated daily v3 archive operation failed; see private log")


MigrationExecutor(connection).migrate(old)
principal = Principal("finance-v3-pages@example.invalid", "Synthetic", "admin", None)
parent = m.AiBusinessEvidenceRun.objects.get(client_request_id="finance-pages-upgrade-plan")
run_id = parent.id
finance_before = finance_reader.inspect(run_id, "finance-context", principal)
if not finance_before["finished"] or parent.status != "collecting":
    raise AssertionError("0030 finance source must already be complete without parent seal")
with connect() as db:
    before, permissions, old_files, old_guards = table_digest(db), grants(db), files(db), guards(db)
archive_and_restore("business_v3_daily_before")
with connect("business_v3_daily_before") as restored:
    assert table_digest(restored) == before and grants(restored) == permissions
    assert files(restored) == old_files and guards(restored) == old_guards

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions and files(db) == old_files
    changed = guards(db)
    assert changed["ai_business_v3_run_guard"] == old_guards["ai_business_v3_run_guard"]
    assert all(changed[key] != old_guards[key] for key in changed if key != "ai_business_v3_run_guard")
assert finance_reader.inspect(run_id, "finance-context", principal)["completeSource"] == finance_before["completeSource"]
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions
    assert files(db) == old_files and guards(db) == old_guards
MigrationExecutor(connection).migrate(new)

query = {"platform": "京东", "shop": "测试店", "channel": "京东",
    "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}
rows = [{"rowId": "1", "platform": "京东", "shopName": "测试店", "channel": "京东",
         "date": "2026-08-20", "metrics": {"salesCents": 100}}]
page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b"*64,
    "sourceRevision": "1:"+"a"*64, "source": "erp_sales", "sourceDataset": None,
    "monetaryUnit": "CNY_CENT", "filters": {**query,
        "periods": comparison_periods(query["startDate"], query["endDate"]), "limit": 100},
    "items": rows, "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
    "pageEvidence": {"rowCount": 1, "sha256": page_digest(rows)},
    "pagination": {"limit": 100, "hasMore": False, "nextCursor": None}, "metricSemantics": None}
verifier = PageReconciler(); verifier.consume(page); verifier.result()
metadata = {"sourceRevision": page["sourceRevision"], "coverage": None,
    "excludedOverlappingPeriodRows": None, "identityCheck": None, "availableDates": None,
    "metricSemantics": None, "freshness": None, "firstCollectedAt": "2026-09-24T00:00:00+08:00",
    "lastCollectedAt": "2026-09-24T00:00:00+08:00"}
checkpoint = {"pageCount": 1, "verifier": verifier.__dict__, "metadata": metadata}
raw = canonical(page)
with transaction.atomic():
    parent = m.AiBusinessEvidenceRun.objects.get(pk=run_id)
    source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="sales-current")
    m.AiBusinessEvidenceChunk.objects.create(id="v3-daily-upgrade-chunk", run=parent,
        source_key=source.source_key, sequence=1, payload_json=raw,
        payload_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest())
    m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2,
        checkpoint_run_version=parent.version+1, page_count=1, stored_bytes=len(raw.encode("utf-8")),
        row_count=1, finished=True, checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
    m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=parent.version+1,
        stored_bytes=parent.stored_bytes+len(raw.encode("utf-8")))
daily = daily_reader.inspect(run_id, "sales-current", principal)
if not daily["finished"] or not daily["reconciliation"]["reconciled"]:
    raise AssertionError("Restored daily fact did not pass full source replay")
assert finance_reader.inspect(run_id, "finance-context", principal)["completeSource"] == finance_before["completeSource"]
with connect() as db:
    after = table_digest(db)
    assert grants(db) == permissions and files(db) == old_files
    assert db.execute("SELECT status FROM ai_business_evidence_runs WHERE id=%s", [run_id]).fetchone()[0] == "collecting"
archive_and_restore("business_v3_daily_after")
with connect("business_v3_daily_after") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert files(restored) == old_files and guards(restored) == changed
    assert restored.execute("SELECT page_count,finished FROM ai_business_evidence_sources "
        "WHERE run_id=%s AND source_key='sales-current'", [run_id]).fetchone() == (1, True)
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted persistent v3 daily facts")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0030->0031", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": before, "afterDigest": after,
    "oldFileVersions": list(old_files), "oldFileBytesPreserved": True,
    "permissionsUnchanged": True, "oldBackupRestored": True, "newBackupRestored": True,
    "oldGuardBytesRestoredOnEmptyReverse": True, "financeSourceUnchanged": True,
    "reverseWithDailyFactsDenied": True, "dailySourceFinished": True,
    "parentStatus": "collecting", "realDailySourceAuthority": False,
    "dailyFetchRegistered": False, "reportOrModelRegistered": False, "productionWrites": False}
(folder / "business-v3-daily-pages-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
