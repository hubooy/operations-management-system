"""Independent 0029 -> 0030 finance-only page guard and backup restoration.

Requires the verified 0029 promotion file seed in the same isolated cluster.
The synthetic finance page is a physical SQL guard probe, not source authority.
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

from access_control.models import AccessRole, AppUser
from ai_assistant import business_evidence_v3 as plan, models as m
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis import finance_collection_state as verifier
from business_analysis.contracts import canonical
from business_analysis.test_finance_collection_state import owned_page, sources as finance_fixture
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-file-ready-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0028->0029"):
    raise RuntimeError("Finance v3 page rehearsal requires exact isolated 0029 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0029_business_promotion_file_ready")]
new = [("ai_assistant", "0030_business_finance_source_pages")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0029_business_promotion_file_ready','0030_business_finance_source_pages') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0029 predecessor required without 0030")


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


def old_file_bytes(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-" + str(version)
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s",
                         [run_id]).fetchone()
        if row is None or row[:3] != (version, "ready", 1):
            raise AssertionError("Historical renderer row missing")
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table + " WHERE run_id=%s ORDER BY " + order,
                            [run_id]).fetchall()
        if not chunks or any(hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
            raise AssertionError("Historical file chunk digest mismatch")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(data) for data, _ in chunks)).hexdigest())
    staged = db.execute("SELECT renderer_version,status,attempt,manifest_json FROM ai_business_file_runs "
        "WHERE id='promotion-renderer-seven-staged'").fetchone()
    chunks = db.execute("SELECT content,content_digest FROM ai_business_volume_chunks "
        "WHERE run_id='promotion-renderer-seven-staged' ORDER BY volume_index,format,sequence").fetchall()
    if staged is None or staged[:3] != (7, "building", 1) or not chunks or any(
            hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
        raise AssertionError("Renderer 7 staged bytes missing")
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
            raise RuntimeError("Isolated finance page archive operation failed; see private log")


MigrationExecutor(connection).migrate(old)
principal = Principal("finance-v3-pages@example.invalid", "Synthetic", "admin", None)
role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
now = timezone.now()
AppUser.objects.create(email=principal.email, display_name="Synthetic", role=role, status="active",
    scope=None, version=1, created_at=now, updated_at=now)
_, query, publication, rows = finance_fixture()
daily = {"key": "sales-current", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
    "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}}
finance = {"key": "finance-context", "domain": "finance", "query": query}
body = {"schemaVersion": "business-evidence-v3", "clientRequestId": "finance-pages-upgrade-plan",
    "sources": [daily, finance], "analysisRequest": {"schemaVersion": "business-analysis-request-v1",
        "question": "店铺与财报", "requestedDimensions": ["shop"], "requestedWindows": ["current"]}}
created = plan.create(body, principal)
run_id = created["item"]["id"]
with connect() as db:
    before, permissions, files, old_guards = table_digest(db), grants(db), old_file_bytes(db), guards(db)
archive_and_restore("business_finance_pages_before")
with connect("business_finance_pages_before") as restored:
    assert table_digest(restored) == before and grants(restored) == permissions
    assert old_file_bytes(restored) == files and guards(restored) == old_guards

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions and old_file_bytes(db) == files
    new_guards = guards(db)
    assert new_guards != old_guards and all(new_guards[key] != old_guards[key] for key in old_guards)
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions and old_file_bytes(db) == files
    assert guards(db) == old_guards
MigrationExecutor(connection).migrate(new)

page = owned_page(query, publication, rows, offset=0, total=len(rows))
checkpoint = verifier.consume(None, page, trusted_query=query)
raw = canonical(page)
with transaction.atomic():
    parent = m.AiBusinessEvidenceRun.objects.get(pk=run_id)
    source = m.AiBusinessEvidenceSource.objects.get(run=parent, source_key="finance-context")
    m.AiBusinessEvidenceChunk.objects.create(id="finance-pages-upgrade-chunk", run=parent,
        source_key=source.source_key, sequence=1, payload_json=raw,
        payload_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest())
    m.AiBusinessEvidenceSource.objects.filter(pk=source.pk).update(version=2, checkpoint_run_version=2,
        page_count=1, stored_bytes=len(raw.encode("utf-8")), row_count=checkpoint["rowsRead"],
        finished=checkpoint["finished"], checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
    m.AiBusinessEvidenceRun.objects.filter(pk=parent.pk).update(version=2, stored_bytes=len(raw.encode("utf-8")))
with connect() as db:
    after = table_digest(db)
    assert grants(db) == permissions and old_file_bytes(db) == files
    assert db.execute("SELECT status FROM ai_business_evidence_runs WHERE id=%s", [run_id]).fetchone()[0] == "collecting"
archive_and_restore("business_finance_pages_after")
with connect("business_finance_pages_after") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert old_file_bytes(restored) == files and guards(restored) == new_guards
    assert restored.execute("SELECT page_count,finished FROM ai_business_evidence_sources "
        "WHERE run_id=%s AND source_key='finance-context'", [run_id]).fetchone() == (1, True)
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError:
    pass
else:
    raise AssertionError("Finance page guard reverse accepted persisted facts")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0029->0030", "aiTables": len(AI_TABLES), "oldRowsDigestPreserved": before,
    "afterDigest": after, "oldFileVersions": list(files), "oldFileBytesPreserved": True,
    "permissionsUnchanged": True, "oldBackupRestored": True, "newBackupRestored": True,
    "oldGuardBytesRestoredOnEmptyReverse": True, "newGuardBytesRestored": True,
    "reverseWithFinanceFactsDenied": True, "financeSourceFinished": True,
    "parentStatus": "collecting", "realFinanceSourceAuthority": False,
    "appendApiRegistered": False, "reportOrModelRegistered": False, "productionWrites": False}
(folder / "business-finance-v3-pages-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
