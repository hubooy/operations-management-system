"""Independent isolated 0036 -> 0037 read-lock function/backup rehearsal."""
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
from ai_assistant.database_contract import provision
from ai_assistant.policy import digest, uid
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_SEALS as AI_TABLES
from ai_assistant.test_business_v4_ledger import plan_fixture
from business_analysis.contracts import canonical, comparison_periods

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v3-report-intent-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0033->0034"):
    raise RuntimeError("v4 admission upgrade requires verified isolated 0034 renderer seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
BASE = [("ai_assistant", "0036_business_v4_validation_segments"),
    ("finance", "0004_finance_revision_monotonic"),
    ("netshop", "0003_netshop_source_revision_guard")]
OLD = BASE
NEW = [("ai_assistant", "0037_business_v4_seal_admission_read"), *BASE[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0034_business_v3_report_intent','0036_business_v4_validation_segments',"
        "'0037_business_v4_seal_admission_read') "
        "ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != ["0034_business_v3_report_intent"]:
        raise RuntimeError("Exact 0034 renderer seed required before empty 0036 baseline")


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


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM "
        "information_schema.role_table_grants WHERE grantee IN "
        "('teruisi_ai_reader','teruisi_ai_writer','teruisi_finance_reader') "
        "AND table_name=ANY(%s) ORDER BY 1,2,3", [list(AI_TABLES)]).fetchall()


def files(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-" + str(version)
        parent = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        if parent is None or parent[:3] != (version, "ready", 1) or not chunks or any(
                hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
            raise AssertionError("old renderer bytes absent")
        result[version] = (parent[3], hashlib.sha256(b"".join(bytes(data)
            for data, _ in chunks)).hexdigest())
    staged = db.execute("SELECT renderer_version,status,attempt,manifest_json "
        "FROM ai_business_file_runs WHERE id='promotion-renderer-seven-staged'").fetchone()
    chunks = db.execute("SELECT content,content_digest FROM ai_business_volume_chunks "
        "WHERE run_id='promotion-renderer-seven-staged' "
        "ORDER BY volume_index,format,sequence").fetchall()
    if staged is None or staged[:3] != (7, "building", 1) or not chunks or any(
            hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
        raise AssertionError("renderer 7 staged bytes absent")
    result[7] = (staged[3], hashlib.sha256(b"".join(bytes(data)
        for data, _ in chunks)).hexdigest())
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
            raise RuntimeError("isolated v4 admission archive/restore failed")


MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    provision(db, secrets.token_hex(32), secrets.token_hex(32))
with connect() as db:
    before, old_acl, old_files = table_digest(db), grants(db), files(db)
    assert db.execute("SELECT count(*) FROM ai_business_v4_validation_attempts").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM ai_business_v4_chunks").fetchone()[0] == 0
archive_and_restore("business_v4_admission_before")
with connect("business_v4_admission_before") as restored:
    assert table_digest(restored) == before
    assert grants(restored) == old_acl
    assert files(restored) == old_files

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert table_digest(db) == before and grants(db) == old_acl and files(db) == old_files
    function = "public.ai_v4_lock_source_revisions_for_admission()"
    assert db.execute("SELECT to_regprocedure(%s)", [function]).fetchone()[0] is not None
    assert db.execute("SELECT has_function_privilege('teruisi_ai_writer',%s,'EXECUTE')",
        [function]).fetchone()[0] is True
    assert db.execute("SELECT has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
        [function]).fetchone()[0] is False
    assert db.execute("SELECT has_table_privilege('teruisi_ai_writer',"
        "'public.finance_data_revisions','UPDATE'),"
        "has_table_privilege('teruisi_ai_writer',"
        "'public.netshop_data_revisions','UPDATE')").fetchone() == (False, False)
    assert db.execute("SELECT count(*) FROM ai_business_v4_chunks").fetchone()[0] == 0
    after = table_digest(db)
archive_and_restore("business_v4_admission_after")
with connect("business_v4_admission_after") as restored:
    assert table_digest(restored) == after
    assert grants(restored) == old_acl
    assert files(restored) == old_files
    assert restored.execute("SELECT has_function_privilege('teruisi_ai_writer',"
        "%s,'EXECUTE')", [function]).fetchone()[0] is True

if MigrationExecutor(connection).migration_plan(NEW):
    raise AssertionError("v4 admission repeat upgrade is not idempotent")
MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert db.execute("SELECT to_regprocedure(%s)", [function]).fetchone()[0] is None
    assert table_digest(db) == before and grants(db) == old_acl and files(db) == old_files
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert db.execute("SELECT to_regprocedure(%s)", [function]).fetchone()[0] is not None
    assert table_digest(db) == before

# Facts collected only after 0037 installation must still prevent reversal.
role, _ = AccessRole.objects.get_or_create(code="admin",
    defaults={"rank": 40, "label": "Admin"})
actor_email = "v4-admission-upgrade@example.invalid"
AppUser.objects.create(email=actor_email, display_name="Synthetic", role=role,
    status="active", scope=None, version=1,
    created_at=timezone.now(), updated_at=timezone.now())
plan = plan_fixture("v4-admission-upgrade-plan")
raw_plan = canonical(plan)
with transaction.atomic():
    parent = m.AiBusinessV4Run.objects.create(id="v4-admission-upgrade-run",
        owner_email=actor_email, client_request_id=plan["clientRequestId"],
        plan_json=raw_plan, plan_digest=digest(raw_plan),
        run_identity_digest=plan["runIdentityDigest"])
    sources = {}
    for entry in plan["sourcePlans"]:
        sources[entry["sourceKey"]] = m.AiBusinessV4Source.objects.create(
            id="v4-admission-upgrade-" + entry["sourceKey"], run=parent,
            source_key=entry["sourceKey"], ordinal=entry["ordinal"],
            domain=entry["domain"], temporal_role=entry["temporalRole"],
            query_json=canonical(entry["query"]), query_digest=entry["queryDigest"],
            source_identity_digest=entry["sourceIdentityDigest"],
            source_revision_hint=entry["sourceRevisionHint"])
query = json.loads(sources["sales"].query_json)
items = [{"rowId": "1", "platform": "京东", "shopName": "测试店",
    "channel": "京东", "date": "2026-08-20", "metrics": {"salesCents": 100}}]
page = {"schemaVersion": "business-analysis-v1", "sourceRef": "b" * 64,
    "sourceRevision": "1:2", "source": "erp_sales", "sourceDataset": None,
    "monetaryUnit": "CNY_CENT", "filters": {**query,
        "periods": comparison_periods(query["startDate"], query["endDate"]),
        "limit": 100}, "items": items,
    "control": {"rowCount": 1, "typedTotals": {"salesCents": 100}},
    "pageEvidence": {"rowCount": 1, "sha256": digest(items)},
    "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
    "metricSemantics": None}
raw = canonical(page); size = len(raw.encode("utf-8"))
with transaction.atomic():
    audit = m.AiToolAuditLogs.objects.create(id=uid("audit"),
        request_id=uid("request"), invocation_id=uid("invocation"),
        actor_email=actor_email, actor_role="admin", surface="business_collection",
        tool_name="get_business_source_page", arguments_json="{}",
        status="succeeded", duration_ms=1, response_digest=digest(raw))
    chunk = m.AiBusinessV4Chunk.objects.create(id=uid("v4-chunk"), run=parent,
        source=sources["sales"], sequence=1, payload_json=raw,
        payload_digest=digest(raw), source_ref=page["sourceRef"],
        source_revision=page["sourceRevision"], row_count=1)
    m.AiBusinessV4ToolReceipt.objects.create(chunk=chunk, audit=audit,
        run=parent, source=sources["sales"], sequence=1,
        actor_email=actor_email, request_id=audit.request_id,
        invocation_id=audit.invocation_id, tool_name=audit.tool_name,
        response_digest=chunk.payload_digest, payload_bytes=size)
    checkpoint = {"schemaVersion": "business-v4-checkpoint-v1",
        "sourceRef": page["sourceRef"], "sourceRevision": page["sourceRevision"],
        "lastChunkDigest": chunk.payload_digest, "pageCount": 1,
        "rowCount": 1, "storedBytes": size, "finished": True}
    m.AiBusinessV4Source.objects.filter(pk=sources["sales"].pk).update(
        version=2, page_count=1, row_count=1, stored_bytes=size,
        finished=True, source_ref=page["sourceRef"],
        source_revision=page["sourceRevision"],
        checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
    m.AiBusinessV4Run.objects.filter(pk=parent.pk).update(
        version=2, page_count=1, row_count=1, stored_bytes=size)
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ai_v4_chunk_complete,ai_v4_parent_complete IMMEDIATE")
try:
    MigrationExecutor(connection).migrate(OLD)
except RuntimeError as error:
    if "存在v4事实或运行分段证明" not in str(error): raise
else:
    raise AssertionError("v4 admission reverse discarded physical evidence")
assert MigrationExecutor(connection).migration_plan(NEW) == []

result = {"upgrade": "0036->0037", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": before, "afterRowsDigest": after,
    "oldAclPreserved": True, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldBackupRestored": True,
    "newBackupRestored": True, "writerExecuteOnly": True,
    "readerExecuteDenied": True, "businessRevisionDmlNotGranted": True,
    "emptyReversePassed": True, "factsReverseDenied": True,
    "sealTableCreated": False,
    "parentSealed": False, "productionWrites": False}
(folder / "business-v4-seal-admission-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
