"""Verify 0011 -> 0012, real roles and restore in a fresh synthetic database."""
import argparse
import hashlib
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
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
args = parser.parse_args()
run_root = args.run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT == Path(r"D:\运营管理系统") or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1" or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal" or connection.vendor != "postgresql"
        or connection.introspection.table_names() or run_root.parent != ROOT / ".runtime"):
    raise RuntimeError("Prompt upgrade requires a fresh isolated rehearsal database")

old_target = [("ai_assistant", "0011_prompt_settings")]
executor = MigrationExecutor(connection)
executor.migrate(old_target)
old = executor.loader.project_state(old_target).apps
Conversation = old.get_model("ai_assistant", "AiConversations")
Message = old.get_model("ai_assistant", "AiConversationMessages")
Conversation.objects.create(id="retained-fixture", title="合成历史对话", created_by="fixture@example.invalid")
Message.objects.create(id="retained-message", conversation_id="retained-fixture", role="assistant", content="原始历史 **内容**", ordinal=1)
before = [list(model.objects.values()) for model in (Conversation, Message)]
target = [("ai_assistant", "0012_report_library")]
executor = MigrationExecutor(connection)
assert [(migration.app_label, migration.name) for migration, backwards in executor.migration_plan(target)] == target
executor.migrate(target)
current = executor.loader.project_state(target).apps
assert [list(current.get_model("ai_assistant", name).objects.values()) for name in ("AiConversations", "AiConversationMessages")] == before
assert current.get_model("ai_assistant", "AiLibraryRevision").objects.count() == 0
assert MigrationExecutor(connection).migration_plan(target) == []
call_command("migrate", interactive=False, verbosity=0)
call_command("makemigrations", check=True, dry_run=True, verbosity=0)

from ai_assistant.control_models import AiDataRevision, AiWriteAuthority, AiMigrationRun
from ai_assistant.database_contract import provision
from ai_assistant.report_library import DEFAULTS as DEFAULT_CONFIG
from ai_assistant.table_manifest import AI_TABLES_PRE_TOOL_RECEIPTS as AI_TABLES
epoch = str(uuid.uuid4())
AiDataRevision.objects.filter(domain="ai-assistant").update(revision=1, source_digest="c"*64)
AiWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch, cutover_id="prompt-synthetic", migration_verify_run_id="ai-apply-"+"d"*32, activated_at=timezone.now())
AiMigrationRun.objects.create(id="ai-apply-"+"d"*32, mode="apply", status="verified", source_path_digest="0"*64, source_snapshot_digest="c"*64, target_snapshot_digest="c"*64, source_counts={}, target_counts={})
reader_password, writer_password = secrets.token_hex(32), secrets.token_hex(32)
with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"]) as owner:
    provision(owner, reader_password, writer_password)

def denied(db, query, values=None):
    try:
        db.execute(query, values)
    except psycopg.Error:
        return
    raise AssertionError("Database boundary accepted a forbidden operation")

insert = "INSERT INTO ai_library_revisions(version,config_json,created_by,created_at) VALUES (1,%s,'fixture',now())"
for role, password in (("reader", reader_password), ("writer", writer_password)):
    url = f"postgresql://teruisi_ai_{role}:{password}@127.0.0.1:{database['PORT']}/teruisi_ai_rehearsal"
    role_env = {**os.environ, "TERUISI_DJANGO_DATABASE_URL": url, "TERUISI_DJANGO_PROCESS_ROLE": "ai_"+role,
        "TERUISI_DJANGO_EXPECT_READ_ONLY": str(role == "reader").lower(), "TERUISI_DJANGO_AI_AUTHORITY_EPOCH": epoch,
        "TERUISI_DJANGO_AI_CUTOVER_ID": "prompt-synthetic", "PYTHONPATH": str(ROOT / "backend")}
    result = subprocess.run([sys.executable, "-c", "import django; django.setup(); from ai_assistant.health import check; assert check()['status']=='ready'"], env=role_env, capture_output=True, timeout=30)
    if result.returncode:
        (run_root / "role-health-error.log").write_bytes(result.stderr)
        raise RuntimeError("Isolated role health failed; see role-health-error.log")
    if role == "reader":
        with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"], autocommit=True) as guard_owner:
            for table in ("ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries"):
                guard_owner.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER ai_write_fence").format(sql.Identifier(table)))
                try:
                    missing = subprocess.run([sys.executable, "-c", "import django; django.setup(); from ai_assistant.health import check; check()"], env=role_env, capture_output=True, timeout=30)
                    assert missing.returncode != 0 and b"guards missing" in missing.stderr
                finally:
                    guard_owner.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER ai_write_fence").format(sql.Identifier(table)))
    with psycopg.connect(url, autocommit=True) as limited:
        denied(limited, insert, [json.dumps(DEFAULT_CONFIG)])
        limited.execute("SELECT set_config('teruisi.ai_epoch',%s,false),set_config('teruisi.ai_cutover','prompt-synthetic',false)", [epoch])
        if role == "writer":
            limited.execute(insert, [json.dumps(DEFAULT_CONFIG)])
            assert limited.execute("SELECT count(*) FROM ai_library_revisions").fetchone()[0] == 1
        else:
            denied(limited, insert, [json.dumps(DEFAULT_CONFIG)])
            limited.execute("SELECT version,config_json FROM ai_library_revisions")
        for query in ("UPDATE ai_library_revisions SET created_by='other'", "DELETE FROM ai_library_revisions", "TRUNCATE ai_library_revisions", "UPDATE sales_order_lines SET allocated_amount_cents=0", "CREATE TABLE public.forbidden_test(id int)"):
            denied(limited, query)

def snapshot(dbname):
    result = {}
    with psycopg.connect(os.environ["TERUISI_DJANGO_DATABASE_URL"].replace("/teruisi_ai_rehearsal", "/"+dbname)) as db:
        for table in AI_TABLES:
            result[table] = sorted(json.dumps(r[0], sort_keys=True, default=str) for r in db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table))))
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()

before_digest = snapshot("teruisi_ai_rehearsal")
bin_path = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
dump = run_root / "report-library.dump"
for executable, arguments in [("pg_dump.exe", ["-Fc", "-f", str(dump), "teruisi_ai_rehearsal"]), ("createdb.exe", ["teruisi_report_restore"]), ("pg_restore.exe", ["--exit-on-error", "-d", "teruisi_report_restore", str(dump)])]:
    subprocess.run([str(bin_path / executable), *arguments], check=True, capture_output=True, timeout=60)
assert snapshot("teruisi_report_restore") == before_digest
print(json.dumps({"upgrade":"0011->0012", "existingConversationsAndMessagesPreserved":True, "defaultsRequireNoSeed":True,
    "secondApplyNoop":True, "migrationDryRun":True, "realRoleHealth":True, "missingReportFencesRejected":True, "readerReadOnly":True,
    "writerAppendOnlyAndFenced":True, "businessWritesDenied":True, "tables":len(AI_TABLES),
    "dumpRestoreDigest":before_digest, "productionWrites":False, "externalCalls":0}))
