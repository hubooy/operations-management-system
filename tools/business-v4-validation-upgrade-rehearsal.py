"""Independent isolated 0035 -> 0036 v4 candidate witness/restore rehearsal."""
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

from ai_assistant import business_v4_validation as validation, models as m
from ai_assistant.database_contract import provision
from ai_assistant.policy import canonical, digest, uid
from ai_assistant.table_manifest import AI_TABLES_PRE_V4_SEALS as AI_TABLES, AI_TABLES_PRE_V4_VALIDATION
from business_analysis import evidence_v4
from business_analysis.contracts import comparison_periods
from sales.auth import Principal

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-ledger-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0034->0035"):
    raise RuntimeError("v4 validation upgrade requires verified isolated 0035 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old = [("ai_assistant", "0035_business_v4_ledger")]
new = [("ai_assistant", "0036_business_v4_validation_segments")]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0035_business_v4_ledger','0036_business_v4_validation_segments') ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [old[0][1]]:
        raise RuntimeError("Exact 0035 predecessor required without 0036")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db, tables):
    material = {}
    for table in sorted(tables):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
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
            raise RuntimeError("Isolated v4 validation archive operation failed")


MigrationExecutor(connection).migrate(old)
with connect() as db:
    before = table_digest(db, AI_TABLES_PRE_V4_VALIDATION)
    permissions, old_files = grants(db, AI_TABLES_PRE_V4_VALIDATION), files(db)
archive_and_restore("business_v4_validation_before")
with connect("business_v4_validation_before") as restored:
    assert table_digest(restored, AI_TABLES_PRE_V4_VALIDATION) == before
    assert grants(restored, AI_TABLES_PRE_V4_VALIDATION) == permissions
    assert files(restored) == old_files

MigrationExecutor(connection).migrate(new)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V4_VALIDATION) == before
    assert grants(db, AI_TABLES_PRE_V4_VALIDATION) == permissions
    assert files(db) == old_files
    assert db.execute("SELECT count(*) FROM ai_business_v4_validation_attempts").fetchone()[0] == 0
MigrationExecutor(connection).migrate(old)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V4_VALIDATION) == before
    assert grants(db, AI_TABLES_PRE_V4_VALIDATION) == permissions
MigrationExecutor(connection).migrate(new)
with connect() as db:
    provision(db, secrets.token_hex(32), secrets.token_hex(32))
with connect() as db:
    assert grants(db, AI_TABLES_PRE_V4_VALIDATION) == permissions
    new_grants = grants(db, ("ai_business_v4_validation_attempts",
        "ai_business_v4_validation_segments"))
    for table in ("ai_business_v4_validation_attempts",
                  "ai_business_v4_validation_segments"):
        assert ("teruisi_ai_writer", table, "SELECT") in new_grants
        assert ("teruisi_ai_writer", table, "INSERT") in new_grants
        assert ("teruisi_ai_writer", table, "UPDATE") not in new_grants
        assert ("teruisi_ai_writer", table, "DELETE") not in new_grants
        assert not any(role == "teruisi_ai_reader" and name == table
            for role, name, _ in new_grants)

# The old 0035 rehearsal leaves a sales+finance physical fixture. A new
# promotion+finance run proves 0036 permits a real, fully finished directory
# but keeps parent sealing disabled. These synthetic pages are not authority.
owner_email = "finance-v3-pages@example.invalid"
principal = Principal(owner_email, "Synthetic", "admin", None)
period = {"startDate": "2026-08-20", "endDate": "2026-09-18"}
promotion_query = {"platform": "京东", "shop": "测试店", "dataset": "promotion",
    **period, "window": "current"}
finance_query = {"months": ["2026-08", "2026-09"],
    "scope": {"scope_key": "business", "scope_type": "business",
        "scope_name": "志高事业部", "group_name": ""},
    "analysisPeriod": period}
plan = evidence_v4.build_plan(client_request_id="v4-validation-upgrade-plan",
    sources=[{"key": "promotion-current", "domain": "netshop", "query": promotion_query},
        {"key": "finance-context", "domain": "finance", "query": finance_query}],
    measurements=[{"sourceKey": "promotion-current", "measuredRowCount": 0,
        "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 2048,
        "sourceRevisionHint": "1:aaaaaaaaaaaa"},
        {"sourceKey": "finance-context", "measuredRowCount": 0,
         "maxRowUtf8Bytes": 0, "pageEnvelopeUtf8Bytes": 2048,
         "sourceRevisionHint": "0:" + "a" * 64}],
    analysis_request={"schemaVersion": "business-analysis-request-v1",
        "question": "隔离迁移财务推广候选验证", "requestedDimensions": ["shop"],
        "requestedWindows": ["current"]})
with transaction.atomic():
    raw_plan = canonical(plan)
    parent = m.AiBusinessV4Run.objects.create(id="v4-validation-upgrade-run",
        owner_email=owner_email, client_request_id=plan["clientRequestId"],
        plan_json=raw_plan, plan_digest=digest(raw_plan),
        run_identity_digest=plan["runIdentityDigest"])
    sources = {}
    for entry in plan["sourcePlans"]:
        sources[entry["sourceKey"]] = m.AiBusinessV4Source.objects.create(
            id="v4-validation-upgrade-" + entry["sourceKey"], run=parent,
            source_key=entry["sourceKey"], ordinal=entry["ordinal"],
            domain=entry["domain"], temporal_role=entry["temporalRole"],
            query_json=canonical(entry["query"]), query_digest=entry["queryDigest"],
            source_identity_digest=entry["sourceIdentityDigest"],
            source_revision_hint=entry["sourceRevisionHint"])

promotion_items = []
promo_page = {"schemaVersion": "business-analysis-v1", "sourceRef": "d" * 64,
    "sourceRevision": "1:aaaaaaaaaaaa", "filters": {"platform": "京东",
        "shop": "测试店", "dataset": "promotion", "window": "current",
        "periods": comparison_periods(period["startDate"], period["endDate"])}, "items": promotion_items,
    "pagination": {"limit": 100, "hasMore": False, "nextCursor": None},
    "pageEvidence": {"rowCount": 0, "sha256": digest(promotion_items)}}
finance_page = {"schemaVersion": "business-finance-owned-page-v1",
    "sourceRef": "e" * 64, "sourceRevision": "0:" + "a" * 64,
    "query": finance_query, "periodAlignment": {"analysisPeriod": period},
    "rows": [], "pagination": {"offset": 0, "returned": 0, "total": 0,
        "nextOffset": None, "nextLastId": None}}
for key, page in (("promotion-current", promo_page),
                  ("finance-context", finance_page)):
    source = sources[key]
    raw = canonical(page); size = len(raw.encode("utf-8"))
    with transaction.atomic():
        audit = m.AiToolAuditLogs.objects.create(id=uid("audit"),
            request_id=uid("request"), invocation_id=uid("invocation"),
            actor_email=owner_email, actor_role="admin",
            surface="business_collection",
            tool_name="get_business_source_page" if key == "promotion-current"
                else "get_business_finance_source_page",
            arguments_json="{}", status="succeeded", duration_ms=1,
            response_digest=digest(raw))
        chunk = m.AiBusinessV4Chunk.objects.create(id=uid("v4-chunk"), run=parent,
            source=source, sequence=1, payload_json=raw,
            payload_digest=digest(raw), source_ref=page["sourceRef"],
            source_revision=page["sourceRevision"], row_count=0)
        m.AiBusinessV4ToolReceipt.objects.create(chunk=chunk, audit=audit,
            run=parent, source=source, sequence=1,
            actor_email=owner_email, request_id=audit.request_id,
            invocation_id=audit.invocation_id, tool_name=audit.tool_name,
            response_digest=chunk.payload_digest, payload_bytes=size)
        checkpoint = {"schemaVersion": "business-v4-checkpoint-v1",
            "sourceRef": page["sourceRef"], "sourceRevision": page["sourceRevision"],
            "lastChunkDigest": chunk.payload_digest, "pageCount": 1,
            "rowCount": 0, "storedBytes": size, "finished": True}
        m.AiBusinessV4Source.objects.filter(pk=source.pk).update(version=2,
            page_count=1, row_count=0, stored_bytes=size, finished=True,
            source_ref=page["sourceRef"], source_revision=page["sourceRevision"],
            checkpoint_json=canonical(checkpoint), updated_at=timezone.now())
        parent.version += 1; parent.page_count += 1; parent.stored_bytes += size
        m.AiBusinessV4Run.objects.filter(pk=parent.pk).update(version=parent.version,
            page_count=parent.page_count, stored_bytes=parent.stored_bytes)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_v4_chunk_complete,ai_v4_parent_complete IMMEDIATE")

actor, row, directory, directory_digest = validation._directory(parent.id, principal)
attempt = m.AiBusinessV4ValidationAttempt.objects.create(id="v4-validation-upgrade-attempt",
    run=row, run_version=row.version, plan_digest=row.plan_digest,
    directory_digest=directory_digest, actor_email=actor["email"],
    actor_version=actor["version"], key_id=validation._key()[1])
with connect() as db:
    after = table_digest(db, AI_TABLES)
    assert files(db) == old_files
archive_and_restore("business_v4_validation_after")
with connect("business_v4_validation_after") as restored:
    assert table_digest(restored, AI_TABLES) == after
    assert grants(restored, AI_TABLES_PRE_V4_VALIDATION) == permissions
    assert grants(restored, ("ai_business_v4_validation_attempts",
        "ai_business_v4_validation_segments")) == new_grants
    assert files(restored) == old_files
    assert restored.execute("SELECT status FROM ai_business_v4_runs WHERE id=%s",
        [parent.id]).fetchone() == ("collecting",)
try:
    MigrationExecutor(connection).migrate(old)
except RuntimeError as error:
    if "存在v4候选分段证明" not in str(error): raise
else:
    raise AssertionError("Reverse migration discarded v4 candidate attempt")
assert MigrationExecutor(connection).migration_plan(new) == []

result = {"upgrade": "0035->0036", "oldAiTables": len(AI_TABLES_PRE_V4_VALIDATION),
    "newAiTables": len(AI_TABLES), "oldRowsDigestPreserved": before,
    "afterDigest": after, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldAclUnchanged": True,
    "newLeastPrivilegeAcl": True, "oldBackupRestored": True,
    "newBackupRestored": True, "emptyReversePassed": True,
    "factsReverseDenied": True, "sealedStatus": False,
    "financeRevisionWriteFenceVerified": False, "productionWrites": False}
(folder / "business-v4-validation-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
