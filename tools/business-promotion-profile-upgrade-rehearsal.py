"""Isolated 0025 -> 0026 promotion profile upgrade and independent restore.

All facts, reports and files are synthetic. This does not dispatch Agents,
render promotion files, or touch the production database.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ai_assistant import business_evidence, models as m
from ai_assistant.control_models import AiWriteAuthority, AiMigrationRun, AiDataRevision
from ai_assistant.database_contract import provision
from ai_assistant.policy import canonical, mutation
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from ai_assistant.test_business_promotion_profile_migration import PromotionProfileMigrationTests
from business_analysis import report_files, volume_delivery, volume_files, volume_plan


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test" or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql" or connection.introspection.table_names()
        or folder.parent != ROOT / ".runtime"):
    raise RuntimeError("Promotion profile upgrade requires a fresh isolated rehearsal database")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old_target = [("ai_assistant", "0025_business_file_opc")]
new_target = [("ai_assistant", "0026_business_promotion_profile")]
epoch = str(uuid.uuid4())
passwords = {role: secrets.token_hex(32) for role in ("reader", "writer")}


def connect(name=None, role=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user="teruisi_ai_"+role if role else database["USER"],
        password=passwords[role] if role else database["PASSWORD"], autocommit=True)


def digest_tables(db):
    data = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))).fetchall()
        data[table] = sorted(json.dumps(row[0], sort_keys=True, default=str, ensure_ascii=False)
            for row in rows)
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer') ORDER BY 1,2,3").fetchall()


def functions(db, names):
    return {name: db.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name]).fetchone()[0]
        for name in names}


def command(executable, args):
    result = subprocess.run([str(BIN / executable), *args], env={**os.environ,
        "PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
        "PGUSER": str(database["USER"]), "PGPASSWORD": str(database["PASSWORD"])},
        capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        (folder / "promotion-restore-error.log").write_bytes(result.stderr)
        raise RuntimeError("Isolated promotion archive operation failed; see private log")


def archive_and_restore(source, name):
    dump = folder / (name + ".dump")
    command("pg_dump.exe", ["-Fc", "-f", str(dump), source])
    command("createdb.exe", [name])
    command("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])


def render(version, report_id, binding):
    tables = [report_files.Table("synthetic", "合成旧表", "仅验证历史字节",
        (report_files.Column("value", "整数", "integer", True),), [[7], [11]], 2)]
    payloads = {}
    if version in (4, 6):
        request = volume_files.request_for(tables, report_id=report_id,
            evidence_digest="d"*64, renderer_version=version)
        plan = volume_plan.build(request)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
        full = volume_files.render(tables, outputs, report_id=report_id,
            evidence_digest="d"*64, renderer_version=version, plan=plan,
            title="旧文件合成", metadata={})
        compact, raw = volume_delivery.make(full, binding_digest=binding,
            attempt=1, draft=False, renderer_version=version)
        for index, output in enumerate(outputs, 1):
            payloads[index, "html"] = output.html.getvalue()
            payloads[index, "xlsx"] = output.xlsx.getvalue()
        payloads[0, "json"] = raw
    else:
        xlsx, html = io.BytesIO(), io.BytesIO()
        proof = report_files.write_pair(xlsx, html, title="旧文件合成", metadata={},
            tables=tables, xlsx_opc_version=2 if version == 5 else 1)
        payloads = {(1, "html"): html.getvalue(), (1, "xlsx"): xlsx.getvalue()}
        compact = {"schemaVersion": "business-file-delivery-v1", "rendererVersion": version,
            "attempt": 1, "bindingDigest": binding, "draft": False, "tables": proof["tables"],
            "files": {kind: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "chunkCount": (len(raw)+524287)//524288, "chunkBytes": 524288,
                "fileName": "synthetic."+kind,
                "mimeType": "text/html" if kind == "html" else
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                for (_, kind), raw in payloads.items()}}
    return compact, payloads


def publish_old_file(db, report, version):
    identity = "promotion-old-file-"+str(version)
    binding = hashlib.sha256(identity.encode()).hexdigest()
    compact, payloads = render(version, report.id, binding)
    with db.transaction():
        db.execute("INSERT INTO ai_business_file_runs(id,owner_email,scope_json,report_id,draft,renderer_version,binding_digest,"
            "status,version,attempt,lease_until,stored_bytes,progress_json,manifest_json,error_code,created_at) "
            "VALUES(%s,%s,'null',%s,false,%s,%s,'queued',1,0,now(),0,'{}','{}','',now())",
            [identity, report.owner_email, report.id, version, binding])
        db.execute("UPDATE ai_business_file_runs SET status='building',version=2,attempt=1 WHERE id=%s", [identity])
        for (index, kind), raw in payloads.items():
            for offset in range(0, len(raw), 524288):
                content = raw[offset:offset+524288]
                common = [identity+"-"+str(index)+"-"+kind+"-"+str(offset//524288+1),
                    identity, kind, offset//524288+1, content, hashlib.sha256(content).hexdigest()]
                if version in (4, 6):
                    db.execute("INSERT INTO ai_business_volume_chunks(id,run_id,attempt,format,sequence,content,content_digest,volume_index,created_at) "
                        "VALUES(%s,%s,1,%s,%s,%s,%s,%s,now())", [*common, index])
                else:
                    db.execute("INSERT INTO ai_business_file_chunks(id,run_id,attempt,format,sequence,content,content_digest,created_at) "
                        "VALUES(%s,%s,1,%s,%s,%s,%s,now())", common)
        db.execute("UPDATE ai_business_file_runs SET version=3,stored_bytes=%s,status='ready',manifest_json=%s WHERE id=%s",
            [sum(map(len, payloads.values())), canonical(compact), identity])
    return identity, compact, payloads


executor = MigrationExecutor(connection)
executor.migrate([node for node in executor.loader.graph.leaf_nodes() if node[0] != "ai_assistant"]+old_target)
assert len(AI_TABLES) == 65
AiMigrationRun.objects.create(id="ai-apply-"+"e"*32, mode="apply", status="verified",
    source_path_digest="0"*64, source_snapshot_digest="c"*64,
    target_snapshot_digest="c"*64, source_counts={}, target_counts={})
AiDataRevision.objects.filter(domain="ai-assistant").update(revision=1, source_digest="c"*64)
AiWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch,
    cutover_id="promotion-profile-synthetic", migration_verify_run_id="ai-apply-"+"e"*32,
    activated_at=timezone.now())

case = PromotionProfileMigrationTests("test_actual_sealed_report_and_old_rows_unmodified")
case.setUp()
old_report = case.report
with connect() as owner:
    provision(owner, passwords["reader"], passwords["writer"])
    old_files = {version: publish_old_file(owner, old_report, version) for version in range(1, 7)}
    baseline = digest_tables(owner)
    baseline_grants = grants(owner)
    from importlib import import_module
    file_names = [part.split("FUNCTION ", 1)[1].split("(", 1)[0] for part in
        import_module("ai_assistant.migrations.0025_business_file_opc").NEW_SQL]
    file_bodies = functions(owner, file_names)
    old_report_raw = (old_report.snapshot_json, old_report.workflow.input_json)
archive_and_restore(database["NAME"], "business_promotion_old_restore")
with connect("business_promotion_old_restore") as old_restored:
    assert digest_tables(old_restored) == baseline
    assert functions(old_restored, file_names) == file_bodies

MigrationExecutor(connection).migrate(new_target)
with connect() as owner:
    assert digest_tables(owner) == baseline and grants(owner) == baseline_grants
    assert functions(owner, file_names) == file_bodies
MigrationExecutor(connection).migrate(old_target)
with connect() as owner:
    assert digest_tables(owner) == baseline
MigrationExecutor(connection).migrate(new_target)

candidate = case.prepared("rehearsal", mapped=True, budget=True)
with mutation(case.admin):
    new_report = case.insert_shape(candidate)
assert new_report.snapshot_json == canonical(candidate.shape["snapshot"])
assert new_report.workflow.input_json == canonical(candidate.shape["workflowInput"])
old_report.refresh_from_db(); old_report.workflow.refresh_from_db()
assert old_report_raw == (old_report.snapshot_json, old_report.workflow.input_json)
source_page = business_evidence.analysis_table(case.parent.id,
    {"sourceKey": "ads", "dimension": "sku"}, case.admin)
with connect() as owner:
    after = digest_tables(owner)
    assert grants(owner) == baseline_grants and functions(owner, file_names) == file_bodies
try:
    MigrationExecutor(connection).migrate(old_target)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted a new profile report")
assert MigrationExecutor(connection).migration_plan(new_target) == []

archive_and_restore(database["NAME"], "business_promotion_new_restore")
with connect("business_promotion_new_restore") as restored:
    assert digest_tables(restored) == after and grants(restored) == baseline_grants
    assert functions(restored, file_names) == file_bodies
    assert restored.execute("SELECT snapshot_json FROM ai_report_runs WHERE id=%s", [new_report.id]).fetchone()[0] == new_report.snapshot_json
    for version, (identity, compact, payloads) in old_files.items():
        row = restored.execute("SELECT renderer_version,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s", [identity]).fetchone()
        assert row == (version, 1, canonical(compact))
        for (index, kind), original in payloads.items():
            table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
            where = " AND volume_index=%s" if version in (4, 6) else ""
            params = [identity, index] if version in (4, 6) else [identity]
            parts = restored.execute("SELECT content,content_digest FROM "+table+
                " WHERE run_id=%s"+where+" AND format='"+kind+"' ORDER BY sequence", params).fetchall()
            assert all(hashlib.sha256(bytes(content)).hexdigest() == sha for content, sha in parts)
            assert b"".join(bytes(content) for content, _ in parts) == original

# Read the same sealed page and report through the owning Django reader from the
# independent restored database, then put the connection back before exiting.
original_name = connection.settings_dict["NAME"]
try:
    connection.close()
    connection.settings_dict["NAME"] = "business_promotion_new_restore"
    restored_page = business_evidence.analysis_table(case.parent.id,
        {"sourceKey": "ads", "dimension": "sku"}, case.admin)
    restored_report = m.AiReportRun.objects.select_related("workflow").get(pk=new_report.id)
    assert canonical(restored_page) == canonical(source_page)
    assert restored_report.snapshot_json == new_report.snapshot_json
    assert restored_report.workflow.input_json == new_report.workflow.input_json
finally:
    connection.close()
    connection.settings_dict["NAME"] = original_name

evidence = {"upgrade": "0025->0026", "aiTables": len(AI_TABLES),
    "oldRowsDigestPreserved": baseline, "newRowsRestoreDigest": after,
    "oldFileVersions": list(old_files), "oldFileBytesRestored": True,
    "oldFileGuardsUnchanged": True, "grantsUnchanged": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "restoredOwningPageEqualsSource": True, "restoredReportEqualsSource": True,
    "emptyReverseAndReupgrade": True, "reverseWithNewReportDenied": True,
    "modelCalled": False, "renderer7Enabled": False, "productionWrites": False}
(folder / "business-promotion-profile-upgrade-evidence.json").write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(evidence, ensure_ascii=False))
