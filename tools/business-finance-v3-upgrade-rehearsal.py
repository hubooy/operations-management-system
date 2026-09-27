"""Independent 0027 -> 0028 inert finance directory upgrade and restore."""
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

from access_control.models import AccessRole, AppUser
from ai_assistant import models as m
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis import evidence_v2, evidence_v3
from business_analysis.contracts import canonical, digest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime"):
    raise RuntimeError("Finance v3 rehearsal requires its isolated cluster")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0027_business_promotion_file_guard")]
new = [("ai_assistant", "0028_business_finance_v3_gate")]


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


def source_guard(db):
    return db.execute("SELECT prosrc FROM pg_proc WHERE proname='ai_business_source_guard'").fetchone()[0]


def archive_and_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
        "PGUSER": str(database["USER"]), "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        result = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            (folder / (name + "-error.log")).write_bytes(result.stderr)
            raise RuntimeError("Isolated finance v3 archive operation failed; see private log")


def daily():
    return {"key": "daily-sales", "domain": "sales", "query": {"platform": "京东", "shop": "测试店",
        "channel": "京东", "startDate": "2026-08-20", "endDate": "2026-09-18", "window": "current"}}


def finance():
    return {"key": "monthly-finance", "domain": "finance", "query": {
        "months": ["2026-08", "2026-09"],
        "scope": {"scope_key": "shop:测试店", "scope_type": "shop", "scope_name": "测试店", "group_name": "京东组"},
        "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}}


def seed(built, run_id):
    with transaction.atomic():
        parent = m.AiBusinessEvidenceRun.objects.create(id=run_id, owner_email="finance-v3-rehearsal@example.invalid",
            client_request_id=run_id, request_digest=built["planDigest"], plan_json=canonical(built["header"]))
        for entry in built["entries"]:
            m.AiBusinessEvidenceSource.objects.create(id="source-" + secrets.token_hex(8), run=parent,
                source_key=entry["key"], ordinal=entry["ordinal"], domain=entry["domain"],
                query_json=canonical(entry["query"]), query_digest=entry["queryDigest"])
    return parent


MigrationExecutor(connection).migrate([("access_control", "0001_initial"), *old])
role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
now = timezone.now()
AppUser.objects.create(email="finance-v3-rehearsal@example.invalid", display_name="Synthetic",
    role=role, status="active", scope=None, version=1, created_at=now, updated_at=now)
request = {"schemaVersion": "business-analysis-request-v1", "question": "日经营和月财报",
    "requestedDimensions": ["shop"], "requestedWindows": ["current"]}
v2 = evidence_v2.build_catalog([daily()])
seed(v2, "finance-v3-old-v2")
m.AiBusinessEvidenceRun.objects.create(id="finance-v3-old-v1", owner_email="finance-v3-rehearsal@example.invalid",
    client_request_id="finance-v3-old-v1", request_digest=digest("old-v1"),
    plan_json='{"schemaVersion":"business-evidence-v1","sources":[]}')

with connect() as db:
    before, permissions, old_function = table_digest(db), grants(db), source_guard(db)
archive_and_restore("business_finance_v3_before")
with connect("business_finance_v3_before") as restored:
    assert table_digest(restored) == before and grants(restored) == permissions
    assert source_guard(restored) == old_function

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db) == before and grants(db) == permissions
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db) == before, "Old AI rows changed on empty reverse"
    assert grants(db) == permissions, "Role grants changed on empty reverse"
    assert source_guard(db) == old_function, "Old source guard body changed on empty reverse"
MigrationExecutor(connection).migrate(new)

v3 = evidence_v3.build_catalog([daily(), finance()], analysis_request=request)
seed(v3, "finance-v3-staged")
with connect() as db:
    after = table_digest(db)
    assert grants(db) == permissions
    assert db.execute("SELECT count(*) FROM ai_business_evidence_chunks WHERE run_id='finance-v3-staged'").fetchone()[0] == 0
archive_and_restore("business_finance_v3_after")
with connect("business_finance_v3_after") as restored:
    assert table_digest(restored) == after and grants(restored) == permissions
    assert restored.execute("SELECT plan_json FROM ai_business_evidence_runs WHERE id='finance-v3-staged'").fetchone()[0] == canonical(v3["header"])
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted an uncollected v3 directory")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0027->0028", "aiTables": len(AI_TABLES), "oldRowsDigestPreserved": before,
    "postRestoreDigest": after, "oldBackupRestored": True, "newBackupRestored": True,
    "permissionsUnchanged": True, "emptyReverseAndReupgrade": True,
    "reverseWithStagedV3Denied": True, "v3Chunks": 0, "v3ReportAndCollectionRegistered": False,
    "productionWrites": False}
(folder / "business-finance-v3-upgrade-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
