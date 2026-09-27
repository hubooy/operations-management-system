"""Isolated 0062 -> 0063 closed five-Agent execution plan upgrade/restore."""
import argparse
import hashlib
import importlib
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
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant.table_manifest import (
    AI_TABLES_PRE_MARKET_V2_EXECUTION_PLANS as OLD_AI_TABLES,
    AI_TABLES_PRE_MARKET_V2_COST_LEDGER_CANDIDATES as AI_TABLES)
from ai_assistant.business_market_v2_context_catalog import verify as verify_context
from ai_assistant.business_market_v2_read_catalog import verify as verify_read
from ai_assistant.business_market_v2_execution_plan_catalog import verify as verify_plan
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-market-v2-read-receipt-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0061->0062"
        or len(OLD_AI_TABLES) != 83 or len(AI_TABLES) != 84):
    raise RuntimeError("0063 requires verified isolated 0062 83-table predecessor")

OLD = [("ai_assistant", "0062_business_market_v2_read_receipt_candidate"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0063_business_market_v2_execution_plan"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0062_business_market_v2_read_receipt_candidate',"
        "'0063_business_market_v2_execution_plan') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0063 requires exact 0062 predecessor without 0063")

migration = importlib.import_module(
    "ai_assistant.migrations.0063_business_market_v2_execution_plan")
execution = importlib.import_module(
    "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
context = importlib.import_module(
    "ai_assistant.migrations.0061_business_market_v2_context_proof")
read = importlib.import_module(
    "ai_assistant.migrations.0062_business_market_v2_read_receipt_candidate")
sidecar = "public.ai_business_market_v2_materials"
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
GUARDS = {
    "public.ai_market_v2_execution_plan_guard()": (migration.GUARD, False),
    migration.EXPECTED_SIGNATURE: (migration.EXPECTED, True),
    migration.ATTEST_SIGNATURE: (migration.ATTEST, True),
    migration.READ_SIGNATURE: (migration.READ, True),
}
TRIGGERS = {
    ("ai_business_market_v2_execution_plans", "ai_market_v2_execution_plan_guard"):
        "public.ai_market_v2_execution_plan_guard()",
    ("ai_business_market_v2_execution_plans", "ai_market_v2_execution_plan_no_truncate"):
        "public.ai_v4_seal_ticket_no_truncate()",
}


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    contents = {}
    for table in sorted(OLD_AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        contents[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(contents).encode("utf-8")).hexdigest()


def market_roots(db):
    profiles = (
        "business-agent-screening-promotion-market-reference-v2",
        "business-agent-screening-promotion-market-admitted-v2",
        execution.PROFILE)
    reports = db.execute("SELECT row_to_json(t) FROM ai_report_runs t "
        "WHERE snapshot_json::jsonb->>'executionProfile' IN (%s,%s,%s) "
        "ORDER BY id", profiles).fetchall()
    flows = db.execute("SELECT row_to_json(t) FROM ai_workflow_runs t "
        "WHERE input_json::jsonb->>'executionProfile' IN (%s,%s,%s) "
        "ORDER BY id", profiles).fetchall()
    materials = db.execute("SELECT row_to_json(t) FROM " + sidecar +
        " t ORDER BY report_id").fetchall()
    proofs = db.execute("SELECT row_to_json(t) FROM " + context.TABLE +
        " t ORDER BY execution_report_id").fetchall()
    reads = db.execute("SELECT row_to_json(t) FROM " + read.TABLE +
        " t ORDER BY tool_dispatch_id").fetchall()
    return reports, flows, materials, proofs, reads


def old_files(db):
    result = {}
    for version in range(1, 8):
        run_id = ("promotion-old-file-" + str(version) if version < 7
                  else "promotion-renderer-seven-staged")
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6, 7) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6, 7) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        if (row is None or row[:3] != (version,
                "building" if version == 7 else "ready", 1)
                or not chunks or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                    for blob, sha in chunks)):
            raise AssertionError("historical renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def functions(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1").fetchall()
    result = {signature: details for signature, *details in rows}
    if len(result) != len(rows) or not result:
        raise AssertionError("AI function catalog absent or ambiguous")
    return result


def inventory(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN "
        "('r','p','v','m','f','S','i','I') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,c.relname,t.tgname,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid),"
        "t.tgenabled,t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    return relations, {(row[1], row[2]): row for row in triggers}


def acl(db):
    roles = ("teruisi_ai_reader", "teruisi_ai_writer")
    for role in roles:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if db.execute("SELECT has_table_privilege(%s,%s,%s)",
                    [role, sidecar, privilege]).fetchone() != (False,):
                raise AssertionError("0045 sidecar table ACL opened")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            if db.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                    [role, sidecar, privilege]).fetchone() != (False,):
                raise AssertionError("0045 sidecar column ACL opened")
    narrow = "public.ai_market_v2_admitted_material_metadata(text,text,bigint,text,text,text)"
    if db.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
            "has_function_privilege(%s,%s,'EXECUTE')", [roles[0], narrow,
            roles[1], narrow]).fetchone() != (True, False):
        raise AssertionError("0056 narrow material function ACL drift")


def snapshot(db):
    acl(db)
    return table_digest(db), market_roots(db), old_files(db), functions(db), inventory(db)


def restored(value):
    return (value[0], value[1], value[2],
        {key: details[1:] for key, details in value[3].items()},
        (tuple(tuple(row[1:]) for row in value[4][0]),
         {key: (row[1], row[2], row[3], row[4], row[5], row[6], row[7])
          for key, row in value[4][1].items()}))


def archive_restore(name):
    archive = folder / (name + ".dump")
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, arguments in (
        ("pg_dump.exe", ["-Fc", "-f", str(archive), database["NAME"]]),
        ("createdb.exe", [name]),
        ("pg_restore.exe", ["--exit-on-error", "-d", name, str(archive)]),
    ):
        done = subprocess.run([str(BIN / executable), *arguments], env=env,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if done.returncode:
            (folder / (name + "-error.log")).write_bytes(done.stderr)
            raise RuntimeError("isolated 0063 archive/restore failed")


def closed(db, installed):
    with db.cursor() as cursor:
        verify_context(cursor, RuntimeError)
        verify_read(cursor, RuntimeError)
    new_rows = db.execute("SELECT (SELECT count(*) FROM ai_report_runs WHERE "
        "snapshot_json::jsonb->>'executionProfile'=%s),"
        "(SELECT count(*) FROM ai_workflow_runs WHERE "
        "input_json::jsonb->>'executionProfile'=%s)",
        [execution.PROFILE, execution.PROFILE]).fetchone()
    if new_rows != (0, 0):
        raise AssertionError("empty 0063 upgrade seed gained execution rows")
    for signature, definition in (
            ("public.ai_market_v2_parked_report_guard()",
                execution.NEW_PARKED_REPORT),
            ("public.ai_market_v2_parked_workflow_guard()",
                execution.NEW_PARKED_WORKFLOW),
            ("public.ai_market_v2_execution_workflow_guard()",
                execution.WORKFLOW_GUARD),
            ("public.ai_market_v2_execution_report_guard()",
                execution.REPORT_GUARD),
            ("public.ai_market_v2_execution_orphan_guard()",
                execution.ORPHAN_GUARD),
            ("public.ai_market_v2_execution_child_guard()",
                execution.CHILD_GUARD)):
        row = db.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE "
            "oid=to_regprocedure(%s)", [signature]).fetchone()
        if row != (definition.split("$$", 2)[1],):
            raise AssertionError("0060 paused execution guard drift")
    for signature in GUARDS:
        if (db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]
                is not None) is not installed:
            raise AssertionError("0063 function inventory drift")
    table_present = db.execute("SELECT to_regclass(%s)",
        [migration.TABLE]).fetchone()[0] is not None
    if table_present is not installed:
        raise AssertionError("0063 execution plan table inventory drift")
    if installed:
        with db.cursor() as cursor:
            verify_plan(cursor, RuntimeError)
        if db.execute("SELECT count(*) FROM " + migration.TABLE).fetchone() != (0,):
            raise AssertionError("empty reverse requires no execution plan")
    if db.execute("SELECT count(*) FROM ai_agent_tool_dispatches WHERE "
            "tool_name IN ('get_business_promotion_market_v2',"
            "'get_business_market_v2_screening_package')").fetchone() != (0,):
        raise AssertionError("market v2 unexpectedly dispatched")


def verify_install(db, before, after):
    if after[:3] != before[:3]:
        raise AssertionError("0063 changed old 83 rows, market roots, proofs or renderers")
    old_relations = {row[1]: row for row in before[4][0]}
    new_relations = {row[1]: row for row in after[4][0]}
    if (set(new_relations) != set(old_relations) | {
            "ai_business_market_v2_execution_plans",
            "ai_business_market_v2_execution_plans_pkey",
            "ai_business_market_v2_execution_plans_execution_report_id_key"}
            or any(new_relations[key] != old for key, old in old_relations.items())):
        raise AssertionError("0063 changed old relation identity or unexpected new relation")
    guard_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0]: (signature, definition, definer)
        for signature, (definition, definer) in GUARDS.items()}
    if (None in guard_keys
            or set(after[3]) != set(before[3]) | set(guard_keys)):
        raise AssertionError("0063 must add exactly four AI functions")
    for signature, prior in before[3].items():
        current = after[3][signature]
        if current != prior:
            raise AssertionError("0063 changed old AI function OID/body/ACL: " + signature)
    for key, (signature, definition, definer) in guard_keys.items():
        details = after[3][key]
        owner = db.execute("SELECT pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)",
            [migration.TABLE]).fetchone()[0]
        if (details[1] != definition.split("$$", 2)[1]
                or details[3] != owner
                or details[4] is not definer
                or {item.replace(" ", "") for item in (details[5] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise AssertionError("0063 new function body/security/path drift")
        privilege = db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(%s) "
            "AND a.grantee=0 AND a.privilege_type='EXECUTE')",
            [signature]).fetchone()
        if privilege != (False,):
            raise AssertionError("0063 function PUBLIC EXECUTE reopened")
    prior_triggers, current_triggers = before[4][1], after[4][1]
    if set(current_triggers) != set(prior_triggers) | set(TRIGGERS):
        raise AssertionError("0063 trigger set drift")
    for key, value in prior_triggers.items():
        if current_triggers[key] != value:
            raise AssertionError("0063 changed old trigger: " + str(key))
    for key, signature in TRIGGERS.items():
        row = current_triggers[key]
        target = db.execute("SELECT to_regprocedure(%s)::text", [signature]
            ).fetchone()[0]
        if (target is None or row[3] != target or row[5] != 'O'
                or row[6] is not False or row[7] is not False):
            raise AssertionError("0063 trigger target/enabled/deferral drift")
    with db.cursor() as cursor:
        verify_context(cursor, RuntimeError)
        verify_read(cursor, RuntimeError)
        verify_plan(cursor, RuntimeError)


with connect() as db:
    closed(db, installed=False)
    before = snapshot(db)
archive_restore("market_v2_plan_before")
with connect("market_v2_plan_before") as copy:
    closed(copy, installed=False)
    if restored(snapshot(copy)) != restored(before):
        raise AssertionError("0062 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = snapshot(db)
    verify_install(db, before, after)
archive_restore("market_v2_plan_after")
with connect("market_v2_plan_after") as copy:
    closed(copy, installed=True)
    restored_after = snapshot(copy)
    if restored(restored_after) != restored(after):
        raise AssertionError("0063 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db, installed=False)
    if snapshot(db) != before:
        raise AssertionError("0063 empty reverse did not restore exact 0062 state")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    replayed = snapshot(db)
    verify_install(db, before, replayed)
    if (replayed[:3] != after[:3]
            or {key: details[1:] for key, details in replayed[3].items()} !=
               {key: details[1:] for key, details in after[3].items()}
            or tuple(row[1:] for row in replayed[4][0]) !=
               tuple(row[1:] for row in after[4][0])
            or {key: value[1:] for key, value in replayed[4][1].items()} !=
               {key: value[1:] for key, value in after[4][1].items()}):
        raise AssertionError("0063 reapply differed from first installation")

result = {"upgrade": "0062->0063", "oldAiTables": 83,
    "newAiTables": 84,
    "oldRowsDigestPreserved": before[0], "parkedAdmittedExecutionContextRootsPreserved": True,
    "rendererVersions": list(before[2]), "rendererBytesPreserved": True,
    "oldAiFunctionsOidBodyAclPreservedIncluding0060To0062": True,
    "newFourFunctionsTwoTriggersExact": True,
    "newAttestorNoLoginNoMembership": True,
    "planTableNoReaderWriterAttestorDml": True,
    "oldMaterialContextAndReadAclClosed": True,
    "newPlanRowsAbsentInUpgradeSeed": True,
    "noRealJobExpectedUnder0060Guard": True,
    "planAttestationSeparatedToTargetPgTest":
        "ai_assistant.test_business_market_v2_execution_plan",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-execution-plan-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
