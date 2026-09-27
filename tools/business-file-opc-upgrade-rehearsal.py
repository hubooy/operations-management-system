"""Isolated 0024->0025 ledger/role/restore rehearsal; all inputs are synthetic.

This tests file persistence, not report authorization or real Agent diagnosis.
The separate service tests exercise real five-Agent/human-review report paths.
No native Excel or production connection is used by this script.
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
import zipfile

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
from ai_assistant import models as m
from ai_assistant.control_models import AiWriteAuthority, AiMigrationRun, AiDataRevision
from ai_assistant.database_contract import provision
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
from business_analysis import budget, budget_offline, report_files, volume_files, volume_plan, volume_delivery
from business_analysis.contracts import canonical
from business_analysis.test_budget import fixture

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT == Path(r"D:\运营管理系统") or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1" or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999 or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql" or connection.introspection.table_names()
        or folder.parent != ROOT / ".runtime"):
    raise RuntimeError("OPC upgrade requires a fresh isolated rehearsal database")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
old_target = [("ai_assistant", "0024_business_screening_runtime")]
new_target = [("ai_assistant", "0025_business_file_opc")]
epoch = str(uuid.uuid4())
passwords = {role: secrets.token_hex(32) for role in ("reader", "writer")}


def connect(name=None, role=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"], dbname=name or database["NAME"],
        user="teruisi_ai_"+role if role else database["USER"],
        password=passwords[role] if role else database["PASSWORD"], autocommit=True)


def snapshot(db):
    data = {}
    for table in sorted(AI_TABLES):
        data[table] = sorted(json.dumps(row[0], sort_keys=True, default=str, ensure_ascii=False)
            for row in db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))))
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def grants(db):
    return db.execute("SELECT grantee,table_name,privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('teruisi_ai_reader','teruisi_ai_writer') ORDER BY 1,2,3").fetchall()


def denied(db, statement, args=(), contains=None):
    try:
        with db.transaction():
            db.execute(statement, args)
    except psycopg.Error as error:
        if contains and contains not in str(error):
            raise AssertionError("Wrong database rejection") from error
        return
    raise AssertionError("Forbidden database action accepted")


executor = MigrationExecutor(connection)
executor.migrate([node for node in executor.loader.graph.leaf_nodes() if node[0] != "ai_assistant"] + old_target)
assert len(AI_TABLES) == 65
verification_id = "ai-apply-" + "e"*32
AiMigrationRun.objects.create(id=verification_id, mode="apply", status="verified",
    source_path_digest="0"*64, source_snapshot_digest="c"*64, target_snapshot_digest="c"*64,
    source_counts={}, target_counts={})
AiDataRevision.objects.filter(domain="ai-assistant").update(revision=1, source_digest="c"*64)
AiWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch,
    cutover_id="opc-synthetic", migration_verify_run_id=verification_id, activated_at=timezone.now())
flow = m.AiWorkflowRuns.objects.create(id="opc-ledger-flow", owner_email="opc@example.invalid",
    client_request_id="opc-ledger", request_digest="e"*64, name="仅存储升级合成夹具", graph_json="{}", graph_digest="f"*64)
report = m.AiReportRun.objects.create(id="opc-ledger-report", owner_email=flow.owner_email,
    client_request_id="opc-ledger", request_digest="e"*64, workflow=flow, snapshot_json="{}")
with connect() as owner:
    provision(owner, passwords["reader"], passwords["writer"])


def render(version, binding):
    plan, bases = fixture()
    model = budget_offline.payload(budget.calculate(plan, bases), report.id)
    tables = [report_files.Table("synthetic-"+str(i), "合成表"+str(i), "无业务数据",
        (report_files.Column("value", "整数", "integer", True),), [[7], [11]], 2) for i in range(2)]
    payloads = {}
    if version in (4,6):
        request = volume_files.request_for(tables, report_id=report.id, evidence_digest="d"*64, renderer_version=version)
        plan = volume_plan.build(request, max_tables=4, native_budget_sheets=3)
        outputs = [volume_files.VolumeStreams(io.BytesIO(), io.BytesIO()) for _ in plan["volumes"]]
        full = volume_files.render(tables, outputs, report_id=report.id, evidence_digest="d"*64,
            renderer_version=version, plan=plan, title="纯合成升级", metadata={}, offline_budget=model,
            excel_budget=model, max_tables=4)
        compact, manifest = volume_delivery.make(full, binding_digest=binding, attempt=1, draft=False,
            renderer_version=version, max_tables=4)
        for index, output in enumerate(outputs, 1):
            payloads[index, "html"] = output.html.getvalue()
            payloads[index, "xlsx"] = output.xlsx.getvalue()
        payloads[0, "json"] = manifest
    else:
        xlsx, html = io.BytesIO(), io.BytesIO()
        proof = report_files.write_pair(xlsx, html, title="纯合成升级", metadata={}, tables=tables,
            offline_budget=model if version >= 2 else None, excel_budget=model if version >= 3 else None,
            xlsx_opc_version=2 if version == 5 else 1)
        payloads = {(1,"html"):html.getvalue(), (1,"xlsx"):xlsx.getvalue()}
        compact = {"schemaVersion":"business-file-delivery-v1","rendererVersion":version,"attempt":1,
            "bindingDigest":binding,"draft":False,"tables":proof["tables"],"files":{
                kind:{"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
                    "chunkCount":(len(raw)+524287)//524288,"chunkBytes":524288,
                    "fileName":"synthetic."+kind,"mimeType":"text/html" if kind=="html" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
                for (_,kind),raw in payloads.items()}}
    return compact, payloads


def publish(db, version, *, paused=False):
    identity = f"opc-v{version}" + ("-paused" if paused else "")
    binding = hashlib.sha256(identity.encode()).hexdigest()
    compact, payloads = render(version, binding)
    with db.transaction():
        db.execute("INSERT INTO ai_business_file_runs(id,owner_email,scope_json,report_id,draft,renderer_version,binding_digest,"
            "status,version,attempt,lease_until,stored_bytes,progress_json,manifest_json,error_code,created_at) "
            "VALUES(%s,%s,'null',%s,false,%s,%s,'queued',1,0,now(),0,'{}','{}','',now())",
            [identity, report.owner_email, report.id, version, binding])
        db.execute("UPDATE ai_business_file_runs SET status='building',version=2,attempt=1 WHERE id=%s", [identity])
        for (index, kind), raw in payloads.items():
            for offset in range(0, len(raw), 524288):
                content = raw[offset:offset+524288]
                seq = offset//524288+1
                common = [f"{identity}-{index}-{kind}-{seq}",identity,kind,seq,content,hashlib.sha256(content).hexdigest()]
                if version in (4,6):
                    db.execute("INSERT INTO ai_business_volume_chunks(id,run_id,attempt,format,sequence,content,content_digest,volume_index,created_at) "
                        "VALUES(%s,%s,1,%s,%s,%s,%s,%s,now())", [*common,index])
                else:
                    db.execute("INSERT INTO ai_business_file_chunks(id,run_id,attempt,format,sequence,content,content_digest,created_at) "
                        "VALUES(%s,%s,1,%s,%s,%s,%s,now())", common)
        db.execute("UPDATE ai_business_file_runs SET version=3,stored_bytes=%s,status=%s,manifest_json=%s WHERE id=%s",
            [sum(map(len,payloads.values())),"paused" if paused else "ready",canonical(compact),identity])
    return identity, compact, payloads


expected = {}
with connect() as owner:
    for version in (1,2,3,4):
        identity, compact, payloads = publish(owner, version)
        expected[identity] = (version, compact, payloads)
    identity, compact, payloads = publish(owner, 4, paused=True)
    expected[identity] = (4, compact, payloads)
    before, before_grants = snapshot(owner), grants(owner)
MigrationExecutor(connection).migrate(new_target)
with connect() as owner:
    assert snapshot(owner) == before and grants(owner) == before_grants
# With no 5/6 rows, inversion restores the exact old function bodies and bounds.
MigrationExecutor(connection).migrate(old_target)
with connect() as owner:
    assert snapshot(owner) == before
MigrationExecutor(connection).migrate(new_target)
with connect(role="writer") as writer:
    denied(writer, "UPDATE ai_business_file_runs SET version=version+1 WHERE id='opc-v4-paused'", contains="authority")
    writer.execute("SELECT set_config('teruisi.ai_epoch',%s,false),set_config('teruisi.ai_cutover','opc-synthetic',false)", [epoch])
    for version in (5,6):
        identity, compact, payloads = publish(writer, version)
        expected[identity] = (version, compact, payloads)
    denied(writer, "UPDATE ai_business_file_chunks SET content='x'::bytea WHERE run_id='opc-v5'")
    denied(writer, "DELETE FROM ai_business_volume_chunks WHERE run_id='opc-v6'")
with connect(role="reader") as reader:
    assert reader.execute("SELECT count(*) FROM ai_business_file_runs").fetchone()[0] == 7
    denied(reader, "UPDATE ai_business_file_runs SET status='cancelled' WHERE id='opc-v4-paused'")
    reader.execute("SET default_transaction_read_only=off")
    denied(reader, "UPDATE ai_business_file_runs SET status='cancelled' WHERE id='opc-v4-paused'", contains="permission")
with connect() as owner:
    complete = snapshot(owner)
    assert grants(owner) == before_grants
try:
    MigrationExecutor(connection).migrate(old_target)
except RuntimeError:
    pass
else:
    raise AssertionError("Reverse migration accepted new version rows")
assert MigrationExecutor(connection).migration_plan(new_target) == []

env = {**os.environ,"PGHOST":str(database["HOST"]),"PGPORT":str(database["PORT"]),
    "PGUSER":str(database["USER"]),"PGPASSWORD":str(database["PASSWORD"])}
dump = folder/"business-file-opc.dump"
restore = "business_file_opc_restore"
for executable, args in (("pg_dump.exe",["-Fc","-f",str(dump),database["NAME"]]),
        ("createdb.exe",[restore]),("pg_restore.exe",["--exit-on-error","-d",restore,str(dump)])):
    result = subprocess.run([str(BIN/executable),*args],env=env,capture_output=True,timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    if result.returncode:
        (folder/"opc-restore-error.log").write_bytes(result.stderr)
        raise RuntimeError("Isolated OPC restore failed; see private log")

with connect(restore) as restored:
    assert snapshot(restored) == complete and grants(restored) == before_grants
    for identity,(version,compact,payloads) in expected.items():
        row = restored.execute("SELECT renderer_version,status,version,attempt,manifest_json FROM ai_business_file_runs WHERE id=%s",[identity]).fetchone()
        assert row == (version,"paused" if identity.endswith("paused") else "ready",3,1,canonical(compact))
        for (index,kind),original in payloads.items():
            if version in (4,6):
                chunks = restored.execute("SELECT content,content_digest FROM ai_business_volume_chunks WHERE run_id=%s AND attempt=1 AND volume_index=%s AND format=%s ORDER BY sequence",[identity,index,kind]).fetchall()
            else:
                chunks = restored.execute("SELECT content,content_digest FROM ai_business_file_chunks WHERE run_id=%s AND attempt=1 AND format=%s ORDER BY sequence",[identity,kind]).fetchall()
            assert all(hashlib.sha256(bytes(content)).hexdigest()==sha for content,sha in chunks)
            actual = b"".join(bytes(content) for content,_ in chunks)
            assert actual == original
            if kind == "xlsx":
                with zipfile.ZipFile(io.BytesIO(actual)) as archive:
                    has_json = b'Extension="json" ContentType="application/json"' in archive.read("[Content_Types].xml")
                    assert has_json == (version in (5,6))
        if version in (4,6):
            volume_delivery.verify_full(compact,payloads[0,"json"],binding_digest=compact["bindingDigest"],attempt=1,
                draft=False,report_id=report.id,evidence_digest="d"*64,renderer_version=version,max_tables=4)
with connect(restore, "reader") as reader:
    assert reader.execute("SELECT count(*) FROM ai_business_file_runs").fetchone()[0] == 7
    denied(reader, "DELETE FROM ai_business_file_chunks")

evidence = {"upgrade":"0024->0025","aiTables":65,"oldRowsDigestPreserved":before,"restoreDigest":complete,
    "oldVersionsPreserved":[1,2,3,4],"newVersions":[5,6],"pausedV4Restored":True,"allFileBytesRestored":True,
    "realWriterPublishedNewVersions":True,"readerWritesDenied":True,"writerWrongEpochDenied":True,
    "grantsUnchanged":True,"emptyReverseAndReupgrade":True,"reverseWithNewRowsDenied":True,
    "fullManifestsReverified":True,"syntheticLedgerOnly":True,"realReportAuthorizationExercised":False,
    "nativeExcelExercised":False,"productionWrites":False}
(folder/"business-file-opc-upgrade-evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding="utf8")
print(json.dumps(evidence,ensure_ascii=False))
