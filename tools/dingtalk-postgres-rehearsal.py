"""Synthetic migration/roles/dump-restore rehearsal in a disposable private cluster."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = 55457
RUN = ROOT / ".runtime" / ("dingtalk-pg-" + secrets.token_hex(6))
if ROOT == Path(r"D:\运营管理系统") or not RUN.resolve().is_relative_to((ROOT / ".runtime").resolve()):
    raise RuntimeError("An isolated worktree is required")
RUN.mkdir(parents=True)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", PORT))
password = secrets.token_hex(32)
pwfile = RUN / "password"
pwfile.write_text(password, encoding="ascii")
env = {k: v for k, v in os.environ.items() if not k.startswith(("TERUISI_", "AI_", "PG", "DJANGO_"))}
env.update(PGHOST="127.0.0.1", PGPORT=str(PORT), PGUSER="ding_rehearsal", PGPASSWORD=password, PGDATABASE="ding_fixture",
    DJANGO_SETTINGS_MODULE="teruisi_backend.settings", DJANGO_SECRET_KEY=secrets.token_hex(32),
    TERUISI_DJANGO_ENVIRONMENT="test", TERUISI_DJANGO_PROCESS_ROLE="development",
    TERUISI_DJANGO_INTERNAL_SECRET=secrets.token_hex(32), PYTHONUTF8="1",
    TERUISI_DJANGO_DATABASE_URL=f"postgresql://ding_rehearsal:{password}@127.0.0.1:{PORT}/ding_fixture")


def run(args, environment=None, timeout=180):
    logfile = RUN / ("step-" + secrets.token_hex(4) + ".log")
    with logfile.open("wb") as out:
        result = subprocess.run([str(a) for a in args], cwd=ROOT, env=environment or env,
            stdout=out, stderr=out, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("Rehearsal failed; see " + str(logfile))
    return logfile.read_text(encoding="utf-8", errors="replace")


def manage(*args):
    return run([sys.executable, ROOT / "backend/manage.py", *args])


started = False
try:
    run([BIN / "initdb.exe", "-D", RUN / "data", "-U", "ding_rehearsal", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", pwfile])
    with (RUN / "data/postgresql.conf").open("a", encoding="ascii") as out:
        out.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=128\n")
    run([BIN / "pg_ctl.exe", "-D", RUN / "data", "-l", RUN / "postgres.log", "-w", "-t", "30", "start"])
    started = True
    run([BIN / "createdb.exe", "ding_fixture"])
    manage("migrate", "ai_assistant", "0006", "--noinput")
    os.environ.update(env)
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    import psycopg
    from psycopg import sql
    from ai_assistant import models as m
    from ai_assistant.control_models import AiDataRevision, AiWriteAuthority, AiMigrationRun
    from ai_assistant.database_contract import provision
    from ai_assistant.table_manifest import AI_TABLES
    m.AiConversations.objects.create(id="rehearsal-old", title="Synthetic retained conversation", created_by="fixture@example.invalid")
    manage("migrate", "--noinput")
    manage("makemigrations", "--check", "--dry-run")
    assert m.AiConversations.objects.get(pk="rehearsal-old").title == "Synthetic retained conversation"
    tests = manage("test", "ai_assistant", "sales.tests.test_api.SalesApiContractTests.test_brand_filter_uses_exact_erp_identity_and_preserves_refunds", "--noinput")
    (RUN / "tests.log").write_text(tests, encoding="utf-8")
    epoch = str(uuid.uuid4())
    AiDataRevision.objects.filter(domain="ai-assistant").update(revision=1, source_digest="c"*64)
    from django.utils import timezone
    AiWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch, cutover_id="ding-synthetic", migration_verify_run_id="ding-fixture-apply", activated_at=timezone.now())
    AiMigrationRun.objects.create(id="ding-fixture-apply", mode="apply", status="verified", source_path_digest="0"*64,
        source_snapshot_digest="c"*64, target_snapshot_digest="c"*64, source_counts={}, target_counts={})
    reader_password, writer_password = secrets.token_hex(32), secrets.token_hex(32)
    with psycopg.connect(env["TERUISI_DJANGO_DATABASE_URL"]) as owner:
        provision(owner, reader_password, writer_password)
    for role, secret in (("reader", reader_password), ("writer", writer_password)):
        role_url = f"postgresql://teruisi_ai_{role}:{secret}@127.0.0.1:{PORT}/ding_fixture"
        role_env = {**env, "TERUISI_DJANGO_DATABASE_URL": role_url, "TERUISI_DJANGO_PROCESS_ROLE": "ai_"+role,
            "TERUISI_DJANGO_EXPECT_READ_ONLY": str(role == "reader").lower(),
            "TERUISI_DJANGO_AI_AUTHORITY_EPOCH": epoch, "TERUISI_DJANGO_AI_CUTOVER_ID": "ding-synthetic",
            "PYTHONPATH": str(ROOT / "backend")}
        run([sys.executable, "-c", "import django; django.setup(); from ai_assistant.health import check; print(check()['status'])"], role_env)
        with psycopg.connect(role_url, autocommit=True) as limited:
            for query in ("DELETE FROM ai_dingtalk_receipts", "TRUNCATE ai_dingtalk_sessions", "DELETE FROM ai_dingtalk_schedules", "TRUNCATE ai_dingtalk_schedule_runs",
                          "UPDATE sales_order_lines SET allocated_amount_cents=0", "CREATE TABLE public.forbidden_test(id int)"):
                try:
                    limited.execute(query)
                except psycopg.Error:
                    pass
                else:
                    raise AssertionError("Role escaped its boundary")
    m.AiDingTalkSettings.objects.create(identity_json='{"robotCode":"fixture"}', enabled=True, groups_json='[]', updated_by="fixture")
    session = m.AiDingTalkSession.objects.create(id="a"*64, config_digest="b"*64, corp_id="fixture", robot_code="fixture",
        sender_id="fixture", conversation_type="1", external_conversation_id="fixture", owner_email="fixture@example.invalid", conversation_id="rehearsal-old")
    m.AiDingTalkReceipt.objects.create(id="d"*64, session=session, payload_digest="e"*64, prompt="synthetic")
    m.AiDingTalkSchedule.objects.create(id="fixture-schedule", name="Synthetic schedule", prompt="Synthetic query", cadence="daily", hour=9, minute=0,
        target_type="person", target_id="fixture", sender_id="fixture", owner_email="fixture@example.invalid")
    with psycopg.connect(f"postgresql://teruisi_ai_reader:{reader_password}@127.0.0.1:{PORT}/ding_fixture", autocommit=True) as reader:
        assert reader.execute("SELECT count(*) FROM ai_dingtalk_schedules").fetchone()[0] == 1
        try:
            reader.execute("UPDATE ai_dingtalk_schedules SET enabled=true")
        except psycopg.Error:
            pass
        else:
            raise AssertionError("Reader modified schedule")
    with psycopg.connect(f"postgresql://teruisi_ai_writer:{writer_password}@127.0.0.1:{PORT}/ding_fixture", autocommit=True) as writer:
        try:
            writer.execute("UPDATE ai_dingtalk_receipts SET status='running'")
        except psycopg.Error:
            pass
        else:
            raise AssertionError("Missing epoch accepted")
        writer.execute("SELECT set_config('teruisi.ai_epoch',%s,false),set_config('teruisi.ai_cutover','ding-synthetic',false)", [epoch])
        writer.execute("UPDATE ai_dingtalk_receipts SET status='running'")
        writer.execute("UPDATE ai_dingtalk_settings SET enabled=false,version=version+1")
        writer.execute("UPDATE ai_dingtalk_schedules SET version=version+1")
        for query in ("UPDATE ai_dingtalk_sessions SET sender_id='other'", "UPDATE ai_dingtalk_sessions SET conversation_id=NULL",
                      "UPDATE ai_dingtalk_settings SET identity_json='{}'", "UPDATE ai_dingtalk_settings SET version=0",
                      "UPDATE ai_dingtalk_receipts SET session_id='unknown'", "UPDATE ai_dingtalk_receipts SET status='invalid'",
                      "UPDATE ai_dingtalk_schedules SET owner_email='other'", "UPDATE ai_dingtalk_schedules SET hour=24"):
            try:
                writer.execute(query)
            except psycopg.Error:
                pass
            else:
                raise AssertionError("Immutable identity/status constraint escaped")
    def snapshot(dbname):
        result = {}
        with psycopg.connect(host="127.0.0.1", port=PORT, user="ding_rehearsal", password=password, dbname=dbname) as db:
            for table in AI_TABLES:
                rows = [r[0] for r in db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(sql.Identifier(table)))]
                result[table] = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
        return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    before = snapshot("ding_fixture")
    writer_url = f"postgresql://teruisi_ai_writer:{writer_password}@127.0.0.1:{PORT}/ding_fixture"
    with psycopg.connect(writer_url, autocommit=True) as first, psycopg.connect(writer_url, autocommit=True) as second:
        assert first.execute("SELECT pg_try_advisory_lock(841327,1909)").fetchone()[0]
        assert not second.execute("SELECT pg_try_advisory_lock(841327,1909)").fetchone()[0]
        # The new receiver uses a separate key and can coexist with schedules.
        assert second.execute("SELECT pg_try_advisory_lock(841327,1910)").fetchone()[0]
        assert not first.execute("SELECT pg_try_advisory_lock(841327,1910)").fetchone()[0]
        assert second.execute("SELECT pg_advisory_unlock(841327,1910)").fetchone()[0]
        assert first.execute("SELECT pg_advisory_unlock(841327,1909)").fetchone()[0]
        assert second.execute("SELECT pg_try_advisory_lock(841327,1909)").fetchone()[0]
        assert second.execute("SELECT pg_advisory_unlock(841327,1909)").fetchone()[0]
    run([BIN / "pg_dump.exe", "-Fc", "-f", RUN / "fixture.dump", "ding_fixture"])
    run([BIN / "createdb.exe", "ding_restored"])
    run([BIN / "pg_restore.exe", "--exit-on-error", "-d", "ding_restored", RUN / "fixture.dump"])
    assert before == snapshot("ding_restored")
    result = {"status": "passed", "port": PORT, "aiTables": len(AI_TABLES), "oldConversationRetained": True,
        "readerWriterReadiness": True, "negativePermissions": True, "immutableIdentities": True, "receiverSingleton": True,
        "dumpRestoreDigest": before, "productionTouched": False, "externalMessages": 0}
    (RUN / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({**result, "evidence": str(RUN / "result.json")}), flush=True)
finally:
    if started:
        run([BIN / "pg_ctl.exe", "-D", RUN / "data", "-m", "fast", "-w", "-t", "30", "stop"], timeout=45)
    pwfile.unlink(missing_ok=True)
