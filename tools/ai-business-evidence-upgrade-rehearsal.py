"""0013 -> 0024, live restricted roles and independent restore; synthetic only."""
from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
run_root = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT == Path(r"D:\运营管理系统") or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1" or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999 or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql" or connection.introspection.table_names() or run_root.parent != ROOT / ".runtime"):
    raise RuntimeError("Evidence upgrade requires a fresh isolated rehearsal database")

old_target, target = [("ai_assistant", "0013_dingtalk_schedule_media")], [("ai_assistant", "0014_business_evidence")]
executor = MigrationExecutor(connection)
executor.migrate(old_target)
old = executor.loader.project_state(old_target).apps
old.get_model("ai_assistant", "AiConversations").objects.create(id="retained-fixture", title="合成旧会话", created_by="fixture@example.invalid")
old.get_model("ai_assistant", "AiConversationMessages").objects.create(id="retained-message", conversation_id="retained-fixture", role="assistant", content="历史内容", ordinal=1)
volume_table = "ai_business_volume_chunks"
budget_table = "ai_business_budget_plans"
screening_tables = {"ai_business_screening_runs", "ai_business_screening_pages"}
pre_screening_tables = set(AI_TABLES)-screening_tables
new_tables = {"ai_business_evidence_runs", "ai_business_evidence_chunks", "ai_business_file_runs", "ai_business_file_chunks", "ai_business_evidence_sources", volume_table, budget_table} | screening_tables
directory_target = [("ai_assistant", "0019_business_source_directory")]
volume_target = [("ai_assistant", "0020_business_volume_files")]
budget_target = [("ai_assistant", "0021_business_budget_plans")]
integrated_target = [("ai_assistant", "0022_business_integrated_reports")]
screening_target = [("ai_assistant", "0023_business_screening_storage")]
screening_runtime_target = [("ai_assistant", "0024_business_screening_runtime")]

def snapshot(dbname, tables, *, original_report_columns=False):
    result = {}
    with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"].replace("/teruisi_ai_rehearsal", "/"+dbname)) as db:
        for table in sorted(tables):
            projection = "to_jsonb(t)-'budget_plan_id'" if original_report_columns and table == "ai_report_runs" else "row_to_json(t)"
            result[table] = sorted(json.dumps(r[0], sort_keys=True, default=str) for r in db.execute(sql.SQL("SELECT "+projection+" FROM {} t").format(sql.Identifier(table))))
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()

before = snapshot(database["NAME"], set(AI_TABLES) - new_tables)
executor = MigrationExecutor(connection)
assert [(m.app_label, m.name) for m, backwards in executor.migration_plan(target)] == target
executor.migrate(target)
assert snapshot(database["NAME"], set(AI_TABLES) - new_tables) == before
assert MigrationExecutor(connection).migration_plan(target) == []
# Seed the previous schema before installing the directory table. These are
# synthetic persistence fixtures, not accepted business reports or source facts.
previous_target = [("ai_assistant", "0018_business_excel_renderer")]
executor = MigrationExecutor(connection)
executor.migrate(previous_target)
previous_apps = executor.loader.project_state(previous_target).apps
legacy_run = previous_apps.get_model("ai_assistant", "AiBusinessEvidenceRun")
for name, status, collection in (("manual", "collecting", "manual"), ("queued", "collecting", "queued"),
        ("paused", "collecting", "paused"), ("sealed", "sealed", "manual"), ("cancelled", "cancelled", "manual")):
    legacy_run.objects.create(id="legacy-directory-"+name, owner_email="legacy@example.invalid",
        client_request_id="legacy-"+name, request_digest="9"*64,
        plan_json=json.dumps({"schemaVersion": "business-evidence-v1", "sources": []}),
        status=status, collection_status=collection)
legacy_workflow = previous_apps.get_model("ai_assistant", "AiWorkflowRuns").objects.create(
    id="legacy-file-workflow", owner_email="legacy@example.invalid", client_request_id="legacy-file-workflow",
    request_digest="8"*64, scope_json="null", name="旧版合成持久任务", graph_json="{}", graph_digest="7"*64)
legacy_report = previous_apps.get_model("ai_assistant", "AiReportRun").objects.create(
    id="legacy-file-report", owner_email=legacy_workflow.owner_email, client_request_id="legacy-file-report",
    request_digest="8"*64, workflow=legacy_workflow, snapshot_json="{}")
for renderer in (1, 2, 3):
    previous_apps.get_model("ai_assistant", "AiBusinessFileRun").objects.create(
        id="legacy-renderer-"+str(renderer), owner_email=legacy_workflow.owner_email,
        report=legacy_report, binding_digest="6"*64, renderer_version=renderer)
previous_tables = pre_screening_tables-{"ai_business_evidence_sources", volume_table, budget_table}
previous_digest = snapshot(database["NAME"], previous_tables)
MigrationExecutor(connection).migrate(directory_target)
assert snapshot(database["NAME"], previous_tables) == previous_digest
assert MigrationExecutor(connection).migration_plan(directory_target) == []
from ai_assistant.control_models import AiDataRevision, AiWriteAuthority, AiMigrationRun
from ai_assistant.database_contract import provision
epoch = str(uuid.uuid4())
AiDataRevision.objects.filter(domain="ai-assistant").update(revision=1, source_digest="c"*64)
AiWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch, cutover_id="business-synthetic", migration_verify_run_id="ai-apply-"+"d"*32, activated_at=timezone.now())
AiMigrationRun.objects.create(id="ai-apply-"+"d"*32, mode="apply", status="verified", source_path_digest="0"*64, source_snapshot_digest="c"*64, target_snapshot_digest="c"*64, source_counts={}, target_counts={})
# Synthetic transport fixture only: verify bytea preservation and restricted
# writer guards independently of analysis/report content acceptance.
from ai_assistant import models as m
with connection.cursor() as cursor:
    cursor.execute("SELECT set_config('teruisi.ai_epoch',%s,false),set_config('teruisi.ai_cutover','business-synthetic',false)", [epoch])
from business_analysis.evidence_v2 import build_catalog
from business_analysis.contracts import canonical
directory = build_catalog([{"key": "source-"+str(i), "domain": "sales", "query": {
    "platform": "京东", "shop": "合成目录店", "channel": "合成渠道"+str(i),
    "startDate": "2026-08-01", "endDate": "2026-08-01"}} for i in range(2)])
with transaction.atomic():
    directory_run = m.AiBusinessEvidenceRun.objects.create(id="directory-restore", owner_email="fixture@example.invalid",
        client_request_id="directory-restore", request_digest=directory["planDigest"], plan_json=canonical(directory["header"]))
    for entry in directory["entries"]:
        m.AiBusinessEvidenceSource.objects.create(id="directory-source-"+str(entry["ordinal"]), run=directory_run,
            source_key=entry["key"], ordinal=entry["ordinal"], domain=entry["domain"],
            query_json=canonical(entry["query"]), query_digest=entry["queryDigest"])
workflow = m.AiWorkflowRuns.objects.create(id="file-restore-workflow", owner_email="fixture@example.invalid",
    client_request_id="file-restore", request_digest="e"*64, scope_json="null", name="合成文件恢复",
    graph_json="{}", graph_digest="f"*64)
directory_apps = MigrationExecutor(connection).loader.project_state(directory_target).apps
report = directory_apps.get_model("ai_assistant", "AiReportRun").objects.create(id="file-restore-report", owner_email=workflow.owner_email,
    client_request_id="file-restore", request_digest="e"*64, workflow_id=workflow.id, snapshot_json="{}")
m.AiBusinessFileRun.objects.create(id="file-restore", owner_email=workflow.owner_email, report_id=report.id,
    binding_digest="a"*64, status="building", attempt=1)
m.AiBusinessFileRun.objects.create(id="offline-file-restore", owner_email=workflow.owner_email, report_id=report.id,
    binding_digest="a"*64, renderer_version=2)
m.AiBusinessFileRun.objects.create(id="excel-file-restore", owner_email=workflow.owner_email, report_id=report.id,
    binding_digest="a"*64, renderer_version=3)
binary_payload = bytes(range(256))*2048
binary_digest = hashlib.sha256(binary_payload).hexdigest()

# Exercise the directory rollback guard before renderer 4 exists; otherwise the
# later volume guard would mask this older, independently required protection.
directory_tables = pre_screening_tables-{volume_table, budget_table}
directory_digest = snapshot(database["NAME"], directory_tables)
try:
    MigrationExecutor(connection).migrate(previous_target)
except RuntimeError as error:
    assert "v2" in str(error).lower(), str(error)
else:
    raise AssertionError("Reverse migration discarded a v2 directory")
assert snapshot(database["NAME"], directory_tables) == directory_digest
assert MigrationExecutor(connection).migration_plan(directory_target) == []

executor = MigrationExecutor(connection)
assert [(m.app_label, m.name) for m, backwards in executor.migration_plan(volume_target)] == volume_target
executor.migrate(volume_target)
assert snapshot(database["NAME"], directory_tables) == directory_digest
assert MigrationExecutor(connection).migration_plan(volume_target) == []
# Check the volume guard while no later migration can mask it or be removed.
m.AiBusinessFileRun.objects.create(id="volume-file-restore", owner_email=workflow.owner_email, report_id=report.id,
    binding_digest="b"*64, renderer_version=4)
volume_tables = pre_screening_tables-{budget_table}
volume_before = snapshot(database["NAME"], volume_tables, original_report_columns=True)
try:
    MigrationExecutor(connection).migrate(directory_target)
except RuntimeError as error:
    assert "v4" in str(error).lower() or "renderer 4" in str(error).lower(), str(error)
else:
    raise AssertionError("Reverse migration discarded renderer-4 volume persistence")
assert snapshot(database["NAME"], volume_tables, original_report_columns=True) == volume_before
assert MigrationExecutor(connection).migration_plan(volume_target) == []
executor = MigrationExecutor(connection)
# Finish all business fixture schemas while explicitly leaving AI at 0021.
executor.migrate([node for node in executor.loader.graph.leaf_nodes() if node[0] != "ai_assistant"]+budget_target)
assert snapshot(database["NAME"], volume_tables, original_report_columns=True) == volume_before
assert MigrationExecutor(connection).migration_plan(budget_target) == []
assert not m.AiReportRun.objects.exclude(budget_plan_id=None).exists()
call_command("makemigrations", check=True, dry_run=True, verbosity=0)
passwords = {role: secrets.token_hex(32) for role in ("reader", "writer")}

# A real sealed synthetic source backs the immutable parameters. No new Agent
# profile is enabled: its generic dry workflow is an inert persistence fixture.
from ai_assistant.tests import AiDomainTests
from ai_assistant.test_business_budget_store import seed_fixed_report
from ai_assistant import business_budget_store
fixture = AiDomainTests(methodName="runTest")
fixture.setUp()
budget_principal = fixture.user("budget-upgrade@example.invalid", "admin", None)
budget_report, prepared_budget = seed_fixed_report(budget_principal, report_id="budget-upgrade-report")
assert business_budget_store.load(budget_report, budget_principal).result_json == prepared_budget.result_json
budget_row = m.AiBusinessBudgetPlan.objects.get(pk=prepared_budget.id)
budget_expected = (budget_row.plan_json, budget_row.binding_json, budget_row.plan_digest,
                   budget_row.binding_digest, budget_report.snapshot_json)

# Exercise 0021 rollback before 0022 can mask its independent rejection gate.
budget_schema_before = snapshot(database["NAME"], pre_screening_tables)
try:
    MigrationExecutor(connection).migrate(volume_target)
except RuntimeError as error:
    assert "预算" in str(error), str(error)
else:
    raise AssertionError("Reverse migration discarded immutable budget parameters")
assert snapshot(database["NAME"], pre_screening_tables) == budget_schema_before
assert MigrationExecutor(connection).migration_plan(budget_target) == []
executor = MigrationExecutor(connection)
assert [(migration.app_label,migration.name) for migration,backwards in executor.migration_plan(integrated_target)] == integrated_target
executor.migrate(integrated_target)
assert snapshot(database["NAME"], pre_screening_tables) == budget_schema_before
assert MigrationExecutor(connection).migration_plan(integrated_target) == []
assert len(pre_screening_tables) == 63

# Reuse real owning-reader fixture setup without recreating its already-seeded
# access users/model. This patches test setup only, never a runtime data reader.
from ai_assistant.test_business_integrated_guard import BusinessIntegratedGuardTests
from ai_assistant import business_integrated, business_integrated_tools
from ai_assistant.policy import mutation
integrated_fixture = BusinessIntegratedGuardTests(methodName="runTest")
with patch.object(AiDomainTests, "setUp", return_value=None):
    integrated_fixture.setUp()
integrated_principal = integrated_fixture.admin
integrated_expected = {}
for with_budget in (False,True):
    report_id = "integrated-upgrade-"+("budget" if with_budget else "mapping")
    optional_budget = business_budget_store.prepare(integrated_fixture.parent, integrated_fixture.budget_plan,
        integrated_principal, report_id) if with_budget else None
    fixed = business_integrated.prepare(integrated_fixture.parent,[{"salesKey":"sales","masterKey":"master"}],
        integrated_principal,report_id,"合成关联恢复验证，不调用模型",budget=optional_budget)
    with mutation(integrated_principal):
        # Explicit inert workflow: tests persistence and reading, not model admission.
        integrated_report = integrated_fixture.insert((fixed.snapshot,fixed.reference,optional_budget))
    actual, snapshot_value, fixed_reference, _, _ = business_integrated.bound(integrated_report,integrated_principal)
    mapped = business_integrated_tools.read(report_id,"analysis",{"runId":snapshot_value["evidenceRunId"],
        "mode":"mapped","pairKey":snapshot_value["mappingPlan"]["pairs"][0]["pairKey"],"dimension":"sku","offset":"0"},integrated_principal)
    integrated_expected[report_id] = {"snapshot":actual.snapshot_json,"reference":canonical(fixed_reference),
        "mappingPlanDigest":snapshot_value["mappingPlanDigest"],"mappedDigest":hashlib.sha256(canonical(mapped).encode()).hexdigest(),
        "budgetResultDigest":hashlib.sha256(business_budget_store.load(actual,integrated_principal).result_json.encode()).hexdigest() if with_budget else None}
    # First iteration proves even a mapping-only report prevents rollback.
    frozen = snapshot(database["NAME"],pre_screening_tables)
    try:
        MigrationExecutor(connection).migrate(budget_target)
    except RuntimeError as error:
        assert "integrated" in str(error),str(error)
    else:
        raise AssertionError("Reverse migration discarded integrated report references")
    assert snapshot(database["NAME"],pre_screening_tables)==frozen
    assert MigrationExecutor(connection).migration_plan(integrated_target)==[]
# 0022 did not reinterpret the earlier budget plan or binding.
budget_row.refresh_from_db();budget_report.refresh_from_db()
assert (budget_row.plan_json,budget_row.binding_json,budget_row.plan_digest,budget_row.binding_digest,budget_report.snapshot_json)==budget_expected
assert business_budget_store.load(budget_report,budget_principal).result_json==prepared_budget.result_json
screening_schema_before = snapshot(database["NAME"],pre_screening_tables)
executor = MigrationExecutor(connection)
assert [(migration.app_label,migration.name) for migration,backwards in executor.migration_plan(screening_target)] == screening_target
executor.migrate(screening_target)
assert snapshot(database["NAME"],pre_screening_tables) == screening_schema_before
assert not m.AiBusinessScreeningRun.objects.exists() and not m.AiBusinessScreeningPage.objects.exists()
assert len(AI_TABLES) == 65 and MigrationExecutor(connection).migration_plan(screening_target) == []
with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"]) as owner:
    provision(owner,passwords["reader"],passwords["writer"])
from ai_assistant import business_diagnostic_screening, business_screening_store
screen_body = deepcopy(integrated_fixture.evidence_body)
screen_body.update(clientRequestId="screening-upgrade-collection", sources=deepcopy(integrated_fixture.sources),
    analysisRequest={"schemaVersion":"business-analysis-request-v1", "question":"合成固定筛查恢复",
        "requestedDimensions":["shop","sku","spu"], "requestedWindows":["current"]})
integrated_fixture.parent = integrated_fixture.collect_body(screen_body)
screen_report,_ = integrated_fixture.seed()
screen_verified = business_diagnostic_screening.prepare_for_report(screen_report.id,integrated_principal)
screen_reference = business_screening_store.publish(screen_verified,integrated_principal)["reference"]
screen_writer_report,_ = integrated_fixture.seed()
screen_expected = {"reference":screen_reference,"pages":[]}
screen_description = business_screening_store.describe(screen_reference["id"],integrated_principal)
for group in screen_description["manifest"]["groups"]:
    offset = 0
    while True:
        page = (business_screening_store.read_coverage(screen_reference["id"],integrated_principal,offset=offset)
            if group["kind"] == "coverage" else business_screening_store.read_candidates(
                screen_reference["id"],integrated_principal,group["partitionKey"],offset=offset))
        screen_expected["pages"].append({"kind":group["kind"],"partitionKey":group["partitionKey"],
            "offset":offset,"digest":hashlib.sha256(canonical(page).encode()).hexdigest()})
        offset = page["pagination"]["nextOffset"]
        if offset is None: break
# Exercise 0023's independent rollback gate before 0024's report-intent gate
# can mask it. The next migration adds guards only: all 65 row hashes persist.
screening_runtime_before = snapshot(database["NAME"],AI_TABLES)
try:
    MigrationExecutor(connection).migrate(integrated_target)
except RuntimeError as error:
    assert "screen" in str(error).lower() or "筛查" in str(error),str(error)
else:
    raise AssertionError("0023 reverse discarded screening publication")
assert snapshot(database["NAME"],AI_TABLES)==screening_runtime_before
executor=MigrationExecutor(connection)
assert [(item.app_label,item.name) for item,backwards in executor.migration_plan(screening_runtime_target)]==screening_runtime_target
executor.migrate(screening_runtime_target)
assert snapshot(database["NAME"],AI_TABLES)==screening_runtime_before
assert MigrationExecutor(connection).migration_plan(screening_runtime_target)==[]
from ai_assistant import business_screening_runtime as screening_runtime, business_screening_runtime_contract as runtime_contract
runtime_report_id="screening-runtime-upgrade-budget"
runtime_screening_id="screening-runtime-upgrade-fixed"
runtime_budget=business_budget_store.prepare(integrated_fixture.parent,integrated_fixture.budget_plan,
    integrated_principal,runtime_report_id)
runtime_prepared=screening_runtime.prepare(integrated_fixture.parent,integrated_principal,runtime_report_id,
    "合成筛查意图及预算恢复，不调用模型",runtime_screening_id,
    choices=[{"salesKey":"sales","masterKey":"master"}],budget=runtime_budget)
runtime_graph=runtime_contract.graph(True)
with mutation(integrated_principal):
    runtime_report=integrated_fixture.insert((runtime_prepared.snapshot,runtime_prepared.reference,runtime_budget),
        flow_changes={"graph_json":canonical(runtime_graph),"graph_digest":hashlib.sha256(canonical(runtime_graph).encode()).hexdigest()})
assert not m.AiBusinessScreeningRun.objects.filter(report=runtime_report).exists()
unpublished_runtime_digest=snapshot(database["NAME"],AI_TABLES)
try:
    MigrationExecutor(connection).migrate(screening_target)
except RuntimeError as error:
    assert "screening" in str(error),str(error)
else:
    raise AssertionError("0024 reverse accepted an unpublished screening report")
assert snapshot(database["NAME"],AI_TABLES)==unpublished_runtime_digest
assert MigrationExecutor(connection).migration_plan(screening_runtime_target)==[]
with patch("ai_assistant.provider.turn",side_effect=AssertionError("no model")):
    runtime_verified=business_diagnostic_screening.prepare_for_report(runtime_report.id,integrated_principal)
    runtime_publication=business_screening_store.publish(runtime_verified,integrated_principal)
assert runtime_publication["reference"]["id"]==runtime_screening_id and runtime_publication["replayed"] is False
assert business_screening_store.publish(runtime_verified,integrated_principal)["replayed"] is True
screening_runtime.bound(runtime_report,integrated_principal)
budget_insert = """INSERT INTO ai_business_budget_plans
    (id,owner_email,scope_json,evidence_id,evidence_version,plan_json,plan_digest,binding_json,binding_digest,created_at)
    SELECT 'orphan-budget',owner_email,scope_json,evidence_id,evidence_version,plan_json,plan_digest,binding_json,binding_digest,now()
    FROM ai_business_budget_plans WHERE id=%s"""

# This is intentionally a paused partial delivery, not a forged ready report.
# Its arbitrary transport bytes prove persistence/role guards, not file format
# validity or acceptance by the public download API.
with connection.cursor() as cursor:
    cursor.execute("UPDATE ai_business_file_runs SET status='building',version=2,attempt=1 WHERE id='volume-file-restore' AND version=1")
    assert cursor.rowcount == 1
volume_payloads = [
    ("volume-json", 0, "json", 1, canonical({"synthetic": True, "meaning": "未完成交付"}).encode("utf-8")),
    ("volume-1-html", 1, "html", 1, "<p>合成第一卷，未完成交付</p>".encode("utf-8")),
    ("volume-1-xlsx", 1, "xlsx", 1, binary_payload),
    ("volume-2-html", 2, "html", 1, "<p>合成第二卷，未完成交付</p>".encode("utf-8")),
    ("volume-2-xlsx-1", 2, "xlsx", 1, binary_payload),
    ("volume-2-xlsx-2", 2, "xlsx", 2, b"synthetic-last-block"),
]
volume_bytes = sum(len(item[4]) for item in volume_payloads)
volume_insert = "INSERT INTO ai_business_volume_chunks(id,run_id,attempt,volume_index,format,sequence,content,content_digest,created_at) VALUES (%s,%s,1,%s,%s,%s,%s,%s,now())"

def volume_parameters(item, *, run_id="volume-file-restore"):
    key, volume, format, sequence, content = item
    return [key, run_id, volume, format, sequence, content, hashlib.sha256(content).hexdigest()]

def denied(db, statement, parameters=None, *, contains=None):
    try:
        db.execute(statement, parameters)
    except psycopg.Error as error:
        if contains is not None:
            assert contains in str(error),str(error)
        return
    raise AssertionError("Forbidden database action accepted")

def denied_volume_with_parent(db, item):
    """Do not let the missing-parent-byte guard mask bad coordinates/sequence."""
    try:
        with db.transaction():
            db.execute("SELECT id FROM ai_business_file_runs WHERE id='volume-file-restore' FOR UPDATE")
            db.execute(volume_insert, volume_parameters(item))
            result = db.execute("UPDATE ai_business_file_runs SET stored_bytes=%s,version=3 WHERE id='volume-file-restore' AND version=2", [len(item[4])])
            assert result.rowcount == 1
    except psycopg.Error:
        return
    raise AssertionError("Invalid volume coordinates/sequence accepted with matching parent bytes")

insert = "INSERT INTO ai_business_evidence_runs(id,owner_email,scope_json,client_request_id,request_digest,plan_json,state_json,status,version,stored_bytes,created_at,collection_status,next_collect_at,collection_failures,collection_error_code) VALUES ('synthetic','fixture@example.invalid','null','client',repeat('a',64),'{}','{}','collecting',1,0,now(),'manual',now(),0,'')"
integrated_insert = """INSERT INTO ai_report_runs
    (id,owner_email,scope_json,client_request_id,request_digest,workflow_id,snapshot_json,budget_plan_id,created_at)
    SELECT 'forged-integrated-report',owner_email,scope_json,'forged-integrated-report',request_digest,workflow_id,snapshot_json,NULL,now()
    FROM ai_report_runs WHERE id='integrated-upgrade-mapping'"""
for role in ("reader", "writer"):
    url = f"postgresql://teruisi_ai_{role}:{passwords[role]}@127.0.0.1:{database['PORT']}/teruisi_ai_rehearsal"
    env = {**os.environ, "TERUISI_DJANGO_DATABASE_URL": url, "TERUISI_DJANGO_PROCESS_ROLE": "ai_"+role,
        "TERUISI_DJANGO_EXPECT_READ_ONLY": str(role == "reader").lower(), "TERUISI_DJANGO_AI_AUTHORITY_EPOCH": epoch,
        "TERUISI_DJANGO_AI_CUTOVER_ID": "business-synthetic", "PYTHONPATH": str(ROOT / "backend")}
    def health():
        return subprocess.run([sys.executable, "-c", "import django; django.setup(); from ai_assistant.health import check; assert check()['status']=='ready'"], env=env, capture_output=True, timeout=30)
    checked = health()
    if checked.returncode:
        (run_root / "role-health-error.log").write_bytes(checked.stderr)
        raise RuntimeError("Role health failed; see role-health-error.log")
    screen_role_code = """import django; django.setup()
import sys
from ai_assistant import business_diagnostic_screening as screening,business_screening_store as store
from ai_assistant import business_screening_runtime as runtime,business_screening_runtime_contract as contract,business_evidence,models
from ai_assistant.policy import canonical,digest,mutation
from sales.auth import Principal
from unittest.mock import patch
actor=Principal(sys.argv[1],'synthetic','admin',None)
with patch.object(screening.Reader,'pages',side_effect=AssertionError('fixed read must not rescan')):
    for screening_id in (sys.argv[2],sys.argv[5]):
        description=store.describe(screening_id,actor)
        for group in description['manifest']['groups']:
            offset=0
            while True:
                page=(store.read_coverage(screening_id,actor,offset=offset) if group['kind']=='coverage'
                    else store.read_candidates(screening_id,actor,group['partitionKey'],offset=offset))
                offset=page['pagination']['nextOffset']
                if offset is None: break
if sys.argv[3]=='writer':
    with patch('ai_assistant.provider.turn',side_effect=AssertionError('no model')):
        verified=screening.prepare_for_report(sys.argv[4],actor)
        result=store.publish(verified,actor)
        assert result['replayed'] is False
        replay=store.publish(verified,actor)
        assert replay['reference']==result['reference'] and replay['replayed'] is True
        evidence=business_evidence.get_run(sys.argv[6],actor)
        report_id='screening-runtime-upgrade-writer'
        result_id='screening-runtime-upgrade-writer-fixed'
        fixed=runtime.prepare(evidence,actor,report_id,'受限writer固定筛查，不调用模型',result_id)
        graph=contract.graph(False)
        with mutation(actor):
            flow=models.AiWorkflowRuns.objects.create(id='flow-'+report_id,owner_email=actor.email,scope_json='null',
                client_request_id='flow-'+report_id,request_digest=digest(fixed.reference),name='合成筛查恢复',
                graph_json=canonical(graph),graph_digest=digest(graph),input_json=fixed.reference_json,dry_run=1)
            report=models.AiReportRun.objects.create(id=report_id,owner_email=actor.email,scope_json='null',
                client_request_id=report_id,request_digest=digest(fixed.snapshot),workflow=flow,snapshot_json=fixed.snapshot_json)
        runtime.bound(report,actor)
        verified=screening.prepare_for_report(report.id,actor)
        result=store.publish(verified,actor)
        assert result['reference']['id']==result_id and result['replayed'] is False
        replay=store.publish(verified,actor)
        assert replay['reference']==result['reference'] and replay['replayed'] is True
print('screening restricted service checks passed')
"""
    screen_role = subprocess.run([sys.executable,"-c",screen_role_code,integrated_principal.email,
        screen_reference["id"],role,screen_writer_report.id,runtime_screening_id,integrated_fixture.parent.id],env=env,capture_output=True,timeout=60)
    if screen_role.returncode:
        (run_root/"screening-role-error.log").write_bytes(screen_role.stderr)
        raise RuntimeError("Restricted screening service check failed; see screening-role-error.log")
    with psycopg.connect(url, autocommit=True) as limited:
        screen_clone = "INSERT INTO ai_business_screening_runs SELECT * FROM ai_business_screening_runs WHERE id=%s"
        if role == "reader":
            denied(limited,screen_clone,[screen_reference["id"]],contains="read-only transaction")
            limited.execute("SET default_transaction_read_only=off")
            denied(limited,screen_clone,[screen_reference["id"]],contains="permission denied")
            denied(limited,"INSERT INTO ai_business_screening_pages SELECT * FROM ai_business_screening_pages WHERE run_id=%s",[screen_reference["id"]],contains="permission denied")
            limited.execute("SET default_transaction_read_only=on")
        else:
            # The valid existing row reaches the authority fence before unique
            # constraints; missing epoch must not be masked by malformed input.
            denied(limited,screen_clone,[screen_reference["id"]],contains="ai_write_authority_mismatch")
        denied(limited, insert)
        denied(limited, volume_insert, volume_parameters(volume_payloads[0]))
        denied(limited, budget_insert, [budget_row.id])
        limited.execute("SELECT set_config('teruisi.ai_epoch',%s,false),set_config('teruisi.ai_cutover','business-synthetic',false)", [epoch])
        if role == "writer":
            limited.execute(insert)
            limited.execute("INSERT INTO ai_business_evidence_chunks(id,run_id,source_key,sequence,payload_json,payload_digest,created_at) VALUES ('chunk','synthetic','sales',1,'{}',repeat('b',64),now())")
            denied(limited, "UPDATE ai_business_evidence_chunks SET payload_json='[]'")
            denied(limited, "UPDATE ai_business_evidence_runs SET owner_email='other',version=2")
            limited.execute("UPDATE ai_business_evidence_runs SET status='sealed',version=2 WHERE id='synthetic'")
            denied(limited, "UPDATE ai_business_evidence_runs SET status='collecting',version=3")
            limited.execute("INSERT INTO ai_business_file_chunks(id,run_id,attempt,format,sequence,content,content_digest,created_at) VALUES ('binary-restore','file-restore',1,'xlsx',1,%s,%s,now())", [binary_payload, binary_digest])
            limited.execute("UPDATE ai_business_file_runs SET stored_bytes=524288,version=2 WHERE id='file-restore'")
            denied(limited, "UPDATE ai_business_file_chunks SET content='broken'::bytea")
            denied(limited, "UPDATE ai_business_file_runs SET status='ready',version=3 WHERE id='file-restore'")
            denied(limited, "UPDATE ai_business_evidence_sources SET query_digest=repeat('f',64),version=2 WHERE id='directory-source-1'")
            denied(limited, "UPDATE ai_business_evidence_sources SET version=2,checkpoint_run_version=2 WHERE id='directory-source-1'")
            denied(limited, "INSERT INTO ai_business_evidence_chunks(id,run_id,source_key,sequence,payload_json,payload_digest,created_at) VALUES ('orphan-v2-page','directory-restore','source-0',1,'{}',repeat('d',64),now())")
            with limited.transaction():
                limited.execute("INSERT INTO ai_business_evidence_chunks(id,run_id,source_key,sequence,payload_json,payload_digest,created_at) VALUES ('directory-v2-page','directory-restore',%s,1,'{}',%s,now())", [directory["entries"][0]["key"], hashlib.sha256(b'{}').hexdigest()])
                limited.execute("UPDATE ai_business_evidence_sources SET checkpoint_json='{\"synthetic\":true}',version=2,checkpoint_run_version=2,page_count=1,stored_bytes=2 WHERE id='directory-source-1'")
                limited.execute("UPDATE ai_business_evidence_runs SET version=2,stored_bytes=2 WHERE id='directory-restore'")
            denied(limited, "UPDATE ai_business_evidence_runs SET status='sealed',version=3 WHERE id='directory-restore'")
            # Every attempted write is individually rolled back by autocommit,
            # including deferred failures at the end of its statement.
            denied(limited, volume_insert, volume_parameters(volume_payloads[0]))  # parent bytes/CAS absent
            denied(limited, volume_insert, volume_parameters(volume_payloads[0], run_id="file-restore"))
            denied(limited, "INSERT INTO ai_business_file_chunks(id,run_id,attempt,format,sequence,content,content_digest,created_at) VALUES ('wrong-v4-table','volume-file-restore',1,'html',1,%s,%s,now())", [b"x", hashlib.sha256(b"x").hexdigest()])
            for volume, format, sequence in ((0, "html", 1), (1, "json", 1), (101, "html", 1), (1, "xlsx", 513), (1, "xlsx", 2)):
                denied_volume_with_parent(limited, ("invalid-volume-block", volume, format, sequence, b"x"))
            with limited.transaction():
                limited.execute("SELECT id FROM ai_business_file_runs WHERE id='volume-file-restore' FOR UPDATE")
                for item in volume_payloads:
                    limited.execute(volume_insert, volume_parameters(item))
                result = limited.execute("UPDATE ai_business_file_runs SET stored_bytes=%s,version=3 WHERE id='volume-file-restore' AND version=2", [volume_bytes])
                assert result.rowcount == 1
            denied(limited, "UPDATE ai_business_volume_chunks SET content='broken'::bytea")
            denied(limited, "UPDATE ai_business_volume_chunks SET volume_index=2 WHERE id='volume-1-html'")
            denied(limited, "UPDATE ai_business_file_runs SET status='ready',version=4 WHERE id='volume-file-restore'")
            limited.execute("UPDATE ai_business_file_runs SET status='paused',version=4 WHERE id='volume-file-restore'")
            denied(limited, volume_insert, volume_parameters(("paused-volume-block", 2, "xlsx", 3, b"paused")))
        else:
            denied(limited, insert)
            denied(limited, "UPDATE ai_business_evidence_sources SET version=2,checkpoint_run_version=2 WHERE id='directory-source-1'")
            denied(limited, volume_insert, volume_parameters(volume_payloads[0]))
        denied(limited, budget_insert, [budget_row.id])
        denied(limited, "UPDATE ai_business_budget_plans SET plan_json='{}' WHERE id=%s", [budget_row.id])
        denied(limited, "UPDATE ai_report_runs SET budget_plan_id=NULL WHERE id=%s", [budget_report.id])
        denied(limited,integrated_insert,contains="ai_integrated_snapshot_shape" if role=="writer" else None)
        denied(limited,"UPDATE ai_report_runs SET snapshot_json='{}' WHERE id='integrated-upgrade-mapping'")
        assert limited.execute("SELECT count(*) FROM ai_report_runs WHERE id LIKE 'integrated-upgrade-%'").fetchone()==(2,)
        for table in new_tables:
            limited.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            denied(limited, "DELETE FROM " + table)
            denied(limited, "TRUNCATE " + table)
        denied(limited, "UPDATE sales_order_lines SET allocated_amount_cents=0")
    with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"], autocommit=True) as owner:
        for table in new_tables:
            owner.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER ai_write_fence").format(sql.Identifier(table)))
            try:
                assert health().returncode != 0
            finally:
                owner.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER ai_write_fence").format(sql.Identifier(table)))
        owner.execute("ALTER TABLE ai_report_runs DISABLE TRIGGER ai_business_integrated_report_binding")
        try:
            assert health().returncode != 0
        finally:
            owner.execute("ALTER TABLE ai_report_runs ENABLE TRIGGER ai_business_integrated_report_binding")
        from importlib import import_module
        integrated_migration=import_module("ai_assistant.migrations.0022_business_integrated_reports")
        helper_sql=integrated_migration.PLAN_GUARD.replace("CREATE FUNCTION ","CREATE OR REPLACE FUNCTION ",1)
        owner.execute(helper_sql.replace("RETURN expected;","RETURN raw;"))
        try:
            assert health().returncode != 0
        finally:
            owner.execute(helper_sql)
        if role == "writer":
            for table,trigger in (("ai_report_runs","ai_business_screening_report_binding"),
                    ("ai_workflow_runs","ai_business_screening_workflow_binding")):
                owner.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(sql.Identifier(table),sql.Identifier(trigger)))
                try:
                    assert health().returncode != 0
                finally:
                    owner.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(sql.Identifier(table),sql.Identifier(trigger)))
            runtime_migration=import_module("ai_assistant.migrations.0024_business_screening_runtime")
            # Restore the latest installed body, never the obsolete 0023
            # profile whitelist or the 0022 integrated/budget permissions.
            for definition in (runtime_migration.SCREEN_INITIAL,runtime_migration.INTEGRATED_REPORT_GUARD,
                    runtime_migration.NEW_BUDGET_GUARD):
                assert "RETURN NEW;" in definition
                owner.execute(definition.replace("RETURN NEW;","RAISE EXCEPTION 'synthetic changed guard';",1))
                try:
                    assert health().returncode != 0
                finally:
                    owner.execute(definition)
        assert health().returncode == 0

# Capture all old/new publications, including the two produced by the actual
# restricted writer. Restoration must read these original bytes, not rescan.
screenings_expected={}
for report_id in (screen_report.id,screen_writer_report.id,runtime_report.id,"screening-runtime-upgrade-writer"):
    record=m.AiBusinessScreeningRun.objects.get(report_id=report_id)
    report=m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
    description=business_screening_store.describe(record.id,integrated_principal)
    wanted={"reference":description["reference"],"snapshot":report.snapshot_json,"input":report.workflow.input_json,"pages":[]}
    for group in description["manifest"]["groups"]:
        offset=0
        while True:
            page=(business_screening_store.read_coverage(record.id,integrated_principal,offset=offset)
                if group["kind"]=="coverage" else business_screening_store.read_candidates(record.id,integrated_principal,group["partitionKey"],offset=offset))
            wanted["pages"].append({"kind":group["kind"],"partitionKey":group["partitionKey"],"offset":offset,
                "digest":hashlib.sha256(canonical(page).encode()).hexdigest()})
            offset=page["pagination"]["nextOffset"]
            if offset is None: break
    screenings_expected[record.id]=wanted
assert len(screenings_expected)==4
complete = snapshot(database["NAME"], AI_TABLES)
try:
    MigrationExecutor(connection).migrate(screening_target)
except RuntimeError as error:
    assert "screen" in str(error).lower() or "筛查" in str(error), str(error)
else:
    raise AssertionError("Reverse migration discarded screening publication")
assert snapshot(database["NAME"], AI_TABLES) == complete
assert MigrationExecutor(connection).migration_plan(screening_runtime_target) == []
binary = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
dump = run_root / "business-evidence.dump"
for executable, arguments in [("pg_dump.exe", ["-Fc", "-f", str(dump), "teruisi_ai_rehearsal"]), ("createdb.exe", ["teruisi_business_restore"]), ("pg_restore.exe", ["--exit-on-error", "-d", "teruisi_business_restore", str(dump)])]:
    subprocess.run([str(binary / executable), *arguments], check=True, capture_output=True, timeout=60)
assert snapshot("teruisi_business_restore", AI_TABLES) == complete
with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"].replace("/teruisi_ai_rehearsal", "/teruisi_business_restore")) as restored:
    assert restored.execute("""SELECT p.plan_json,p.binding_json,p.plan_digest,p.binding_digest,r.snapshot_json
        FROM ai_business_budget_plans p JOIN ai_report_runs r ON r.budget_plan_id=p.id WHERE r.id=%s""", [budget_report.id]).fetchone() == budget_expected
    for report_id, expected in integrated_expected.items():
        assert restored.execute("SELECT r.snapshot_json,w.input_json FROM ai_report_runs r JOIN ai_workflow_runs w ON w.id=r.workflow_id WHERE r.id=%s",[report_id]).fetchone()==(expected["snapshot"],expected["reference"])
    for expected in screenings_expected.values():
        assert restored.execute("SELECT r.snapshot_json,w.input_json FROM ai_report_runs r JOIN ai_workflow_runs w ON w.id=r.workflow_id WHERE r.id=%s",
            [expected["reference"]["reportId"]]).fetchone()==(expected["snapshot"],expected["input"])
    content, digest = restored.execute("SELECT content,content_digest FROM ai_business_file_chunks WHERE id='binary-restore'").fetchone()
    assert bytes(content) == binary_payload and hashlib.sha256(content).hexdigest() == digest == binary_digest
    assert restored.execute("SELECT renderer_version FROM ai_business_file_runs WHERE id IN ('file-restore','offline-file-restore','excel-file-restore') ORDER BY renderer_version").fetchall() == [(1,), (2,), (3,)]
    assert restored.execute("SELECT renderer_version FROM ai_business_file_runs WHERE id LIKE 'legacy-renderer-%' ORDER BY renderer_version").fetchall() == [(1,), (2,), (3,)]
    assert restored.execute("SELECT source_key,ordinal,domain,query_json,query_digest FROM ai_business_evidence_sources WHERE run_id='directory-restore' ORDER BY ordinal").fetchall() == [
        (entry["key"], entry["ordinal"], entry["domain"], canonical(entry["query"]), entry["queryDigest"]) for entry in directory["entries"]]
    assert restored.execute("SELECT version,checkpoint_run_version,checkpoint_json FROM ai_business_evidence_sources WHERE id='directory-source-1'").fetchone() == (2,2,'{"synthetic":true}')
    assert restored.execute("SELECT page_count,stored_bytes FROM ai_business_evidence_sources WHERE id='directory-source-1'").fetchone() == (1,2)
    assert restored.execute("SELECT payload_json,payload_digest FROM ai_business_evidence_chunks WHERE id='directory-v2-page'").fetchone() == ('{}',hashlib.sha256(b'{}').hexdigest())
    assert restored.execute("SELECT renderer_version,status,version,attempt,stored_bytes,manifest_json FROM ai_business_file_runs WHERE id='volume-file-restore'").fetchone() == (4, "paused", 4, 1, volume_bytes, "{}")
    volume_rows = restored.execute("SELECT id,volume_index,format,sequence,content,content_digest FROM ai_business_volume_chunks WHERE run_id='volume-file-restore' ORDER BY id").fetchall()
    expected_rows = sorted(volume_payloads, key=lambda item: item[0])
    assert len(volume_rows) == len(expected_rows)
    for actual, expected in zip(volume_rows, expected_rows):
        assert actual[:4] == expected[:4]
        assert bytes(actual[4]) == expected[4]
        assert hashlib.sha256(actual[4]).hexdigest() == actual[5] == hashlib.sha256(expected[4]).hexdigest()
    assert restored.execute("SELECT count(*) FROM ai_business_file_chunks WHERE run_id='volume-file-restore'").fetchone() == (0,)
restore_env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"),
    "TERUISI_DJANGO_DATABASE_URL": os.environ["TERUISI_DJANGO_DATABASE_URL"].replace("/teruisi_ai_rehearsal", "/teruisi_business_restore")}
integrated_expected_path=run_root/"integrated-restore-expected.json"
integrated_expected_path.write_text(json.dumps({"principalEmail":integrated_principal.email,"reports":integrated_expected,
    "screening":screen_expected,"screenings":screenings_expected},ensure_ascii=False),encoding="utf-8")
restore_code = """import django; django.setup()
import hashlib,json,sys
from pathlib import Path
from ai_assistant import business_budget_store,business_integrated,business_integrated_tools,business_screening_store,business_diagnostic_screening,business_screening_runtime,models
from unittest.mock import patch
from ai_assistant.policy import canonical
from sales.auth import Principal
report = models.AiReportRun.objects.get(pk='budget-upgrade-report')
value = business_budget_store.load(report, Principal('budget-upgrade@example.invalid','fixture','admin',None))
assert value.result['allocation']['allocatedCents']==9000
old_budget=hashlib.sha256(value.result_json.encode()).hexdigest()
expected=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
principal=Principal(expected['principalEmail'],'fixture','admin',None)
digests={}
for report_id,wanted in expected['reports'].items():
    report=models.AiReportRun.objects.select_related('workflow').get(pk=report_id)
    actual,snapshot,reference,evidence,sources=business_integrated.bound(report,principal)
    assert actual.snapshot_json==wanted['snapshot']
    assert canonical(reference)==wanted['reference']
    assert snapshot['mappingPlanDigest']==wanted['mappingPlanDigest']
    mapped=business_integrated_tools.read(report_id,'analysis',{'runId':evidence.id,'mode':'mapped',
        'pairKey':snapshot['mappingPlan']['pairs'][0]['pairKey'],'dimension':'sku','offset':'0'},principal)
    mapped_digest=hashlib.sha256(canonical(mapped).encode()).hexdigest()
    assert mapped_digest==wanted['mappedDigest']
    fixed_budget=business_budget_store.load(actual,principal) if wanted['budgetResultDigest'] else None
    budget_digest=hashlib.sha256(fixed_budget.result_json.encode()).hexdigest() if fixed_budget else None
    assert budget_digest==wanted['budgetResultDigest']
    if not fixed_budget: assert actual.budget_plan_id is None and 'budgetRef' not in snapshot
    digests[report_id]={'mappedDigest':mapped_digest,'budgetResultDigest':budget_digest}
screen = expected['screening']
with patch.object(business_diagnostic_screening.Reader,'pages',side_effect=AssertionError('restore reads cannot recompute facts')):
    for result_id,fixed in expected['screenings'].items():
        restored_screen = business_screening_store.describe(result_id,principal)
        assert restored_screen['reference'] == fixed['reference']
        report=models.AiReportRun.objects.select_related('workflow').get(pk=fixed['reference']['reportId'])
        assert report.snapshot_json==fixed['snapshot'] and report.workflow.input_json==fixed['input']
        if json.loads(report.snapshot_json)['executionProfile']==business_screening_runtime.PROFILE:
            _,snapshot,reference,_,_,_=business_screening_runtime.bound(report,principal)
            assert snapshot['screeningIntent']['id']==result_id and canonical(reference)==fixed['input']
        for wanted in fixed['pages']:
            actual = (business_screening_store.read_coverage(result_id,principal,offset=wanted['offset'])
                if wanted['kind']=='coverage' else business_screening_store.read_candidates(result_id,principal,wanted['partitionKey'],offset=wanted['offset']))
            assert hashlib.sha256(canonical(actual).encode()).hexdigest()==wanted['digest']
print(json.dumps({'oldBudgetDigest':old_budget,'integrated':digests,'screening':screen['reference'],'screeningIds':sorted(expected['screenings'])},sort_keys=True))
"""
restored_budget = subprocess.run([sys.executable, "-c", restore_code,str(integrated_expected_path)], env=restore_env, capture_output=True, timeout=60)
if restored_budget.returncode:
    (run_root / "budget-restore-read-error.log").write_bytes(restored_budget.stderr)
    raise RuntimeError("Restored budget verification failed; see budget-restore-read-error.log")
restored_checks=json.loads(restored_budget.stdout)
assert restored_checks['oldBudgetDigest']==hashlib.sha256(prepared_budget.result_json.encode()).hexdigest()
assert set(restored_checks['integrated'])==set(integrated_expected)
assert restored_checks['screening']==screen_reference
assert restored_checks['screeningIds']==sorted(screenings_expected)
restored_role_read_code="""import django; django.setup()
import hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
from sales.auth import Principal
from ai_assistant import business_screening_store as store,business_diagnostic_screening as screening,business_screening_runtime as runtime,models
from ai_assistant.policy import canonical
expected=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
actor=Principal(expected['principalEmail'],'synthetic','admin',None)
with patch.object(screening.Reader,'pages',side_effect=AssertionError('restricted restore must not rescan')):
    for result_id,fixed in expected['screenings'].items():
        assert store.describe(result_id,actor)['reference']==fixed['reference']
        report=models.AiReportRun.objects.select_related('workflow').get(pk=fixed['reference']['reportId'])
        assert report.snapshot_json==fixed['snapshot'] and report.workflow.input_json==fixed['input']
        if json.loads(report.snapshot_json)['executionProfile']==runtime.PROFILE:
            assert runtime.bound(report,actor)[1]['screeningIntent']['id']==result_id
        for wanted in fixed['pages']:
            page=(store.read_coverage(result_id,actor,offset=wanted['offset']) if wanted['kind']=='coverage'
                else store.read_candidates(result_id,actor,wanted['partitionKey'],offset=wanted['offset']))
            assert hashlib.sha256(canonical(page).encode()).hexdigest()==wanted['digest']
print('restricted new and old screening restore reads passed')
"""
for role in ("reader","writer"):
    env={**restore_env,"TERUISI_DJANGO_DATABASE_URL":f"postgresql://teruisi_ai_{role}:{passwords[role]}@127.0.0.1:{database['PORT']}/teruisi_business_restore",
        "TERUISI_DJANGO_PROCESS_ROLE":"ai_"+role,"TERUISI_DJANGO_EXPECT_READ_ONLY":str(role=="reader").lower(),
        "TERUISI_DJANGO_AI_AUTHORITY_EPOCH":epoch,"TERUISI_DJANGO_AI_CUTOVER_ID":"business-synthetic"}
    checked=health()
    if checked.returncode:
        (run_root/"restored-role-health-error.log").write_bytes(checked.stderr)
        raise RuntimeError("Restored restricted role health failed")
    read_result=subprocess.run([sys.executable,"-c",restored_role_read_code,str(integrated_expected_path)],env=env,capture_output=True,timeout=60)
    if read_result.returncode:
        (run_root/"restored-screening-role-error.log").write_bytes(read_result.stderr)
        raise RuntimeError("Restored restricted screening read failed; see restored-screening-role-error.log")
print(json.dumps({"upgrade": "0013->0014->0015->0016->0017->0018->0019->0020->0021->0022->0023->0024", "oldAiTablesDigestPreserved": before,
    "previous60TablesDigestPreserved": previous_digest, "tables": len(AI_TABLES), "allThreeRendererVersionsRestored": True,
    "previous61TablesDigestPreserved": directory_digest, "renderer4PausedDeliveryRestored": True,
    "previous62TableOriginalColumnDigestPreserved": volume_before, "oldReportBudgetFkNull": True,
    "budgetParametersRestored": m.AiBusinessBudgetPlan.objects.count(), "budgetPlanBytes": len(prepared_budget.plan_json.encode()),
    "budgetBindingBytes": len(prepared_budget.binding_json.encode()), "budgetRollbackWithDataDenied": True,
    "budgetReaderReconciledAfterRestore": True,
    "previous63TablesDigestPreserved":budget_schema_before,"integratedReportsRestored":2,
    "previous63TablesBeforeScreeningDigestPreserved":screening_schema_before,
    "screeningReferenceRestored":screen_reference,"screeningPagesRestored":len(screen_expected["pages"]),
    "screeningRestoreReadsWithoutFactRescan":True,"screeningRollbackWithDataDenied":True,
    "screeningRestrictedReaderAndWriterPages":True,"screeningRestrictedWriterPublicationAndReplay":True,
    "previous65TablesBeforeRuntimeDigestPreserved":screening_runtime_before,
    "screeningRuntimeUnpublishedReportRollbackDenied":True,"screeningRuntimeFixedIdsRestored":restored_checks['screeningIds'],
    "screeningRuntimeRestrictedWriterPrepareCreateScanPublishReplay":True,"screeningRuntimeRestrictedRestoreReads":True,
    "screeningRuntimeTriggerAndLatestFunctionHealthProbes":True,"screeningRuntimeModelDispatched":False,
    "integratedMappingAndBudgetReadersAfterRestore":restored_checks['integrated'],
    "integratedRollbackWithoutBudgetDenied":True,"integratedRollbackWithBudgetDenied":True,
    "integratedTriggerAndFunctionHealthProbes":True,"restoredRestrictedRoleHealth":True,
    "volumeChunkCountRestored": len(volume_payloads), "volumeChunkBytesRestored": volume_bytes,
    "volumeRollbackWithDataDenied": True, "volumeReadyAcceptanceExercised": False,
    "secondApplyNoop": True, "migrationDryRun": True, "realRoleHealth": True, "fencesAndAppendOnly": True,
    "ownerAndTerminalGuards": True, "businessWritesDenied": True, "dumpRestoreDigest": complete,
    "directorySourceCountRestored": len(directory["entries"]), "directoryCatalogDigest": directory["header"]["catalogDigest"], "directoryRollbackWithDataDenied": True,
    "binaryRestoreBytes": len(binary_payload), "binaryRestoreSha256": binary_digest, "productionWrites": False}))
