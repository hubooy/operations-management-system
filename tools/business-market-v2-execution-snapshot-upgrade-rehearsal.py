"""Isolated 0059 -> 0060 five-tool paused-profile upgrade/restore."""
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
    AI_TABLES_PRE_MARKET_V2_CONTEXT_PROOFS as AI_TABLES)
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-budget-v10-reader-fence-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0058->0059"
        or len(AI_TABLES) != 81):
    raise RuntimeError("0060 requires verified isolated 0059 81-table predecessor")

OLD = [("ai_assistant", "0059_business_promotion_budget_v10_reader_fence"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0060_business_market_v2_execution_snapshot"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0059_business_promotion_budget_v10_reader_fence',"
        "'0060_business_market_v2_execution_snapshot') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0060 requires exact 0059 predecessor without 0060")

migration = importlib.import_module(
    "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
sidecar = "public.ai_business_market_v2_materials"
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
CHANGED = {
    "public.ai_market_v2_parked_report_guard()": migration.NEW_PARKED_REPORT,
    "public.ai_market_v2_parked_workflow_guard()": migration.NEW_PARKED_WORKFLOW,
}
GUARDS = {
    "public.ai_market_v2_execution_workflow_guard()": (migration.WORKFLOW_GUARD, True),
    "public.ai_market_v2_execution_report_guard()": (migration.REPORT_GUARD, True),
    "public.ai_market_v2_execution_orphan_guard()": (migration.ORPHAN_GUARD, False),
    "public.ai_market_v2_execution_child_guard()": (migration.CHILD_GUARD, False),
}
TRIGGERS = {
    ("ai_workflow_runs", "ai_market_v2_execution_workflow_guard"):
        "public.ai_market_v2_execution_workflow_guard()",
    ("ai_report_runs", "ai_market_v2_execution_report_guard"):
        "public.ai_market_v2_execution_report_guard()",
    ("ai_workflow_runs", "ai_market_v2_execution_complete"):
        "public.ai_market_v2_execution_orphan_guard()",
    ("ai_agent_jobs", "ai_market_v2_execution_job_guard"):
        "public.ai_market_v2_execution_child_guard()",
    ("ai_workflow_node_runs", "ai_market_v2_execution_node_guard"):
        "public.ai_market_v2_execution_child_guard()",
    ("ai_agent_provider_dispatches", "ai_market_v2_execution_provider_guard"):
        "public.ai_market_v2_execution_child_guard()",
    ("ai_agent_tool_dispatches", "ai_market_v2_execution_tool_guard"):
        "public.ai_market_v2_execution_child_guard()",
    ("ai_agent_provider_results", "ai_market_v2_execution_provider_result_guard"):
        "public.ai_market_v2_execution_child_guard()",
    ("ai_agent_tool_results", "ai_market_v2_execution_tool_result_guard"):
        "public.ai_market_v2_execution_child_guard()",
}


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    contents = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        contents[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(contents).encode("utf-8")).hexdigest()


def market_roots(db):
    profiles = (
        "business-agent-screening-promotion-market-reference-v2",
        "business-agent-screening-promotion-market-admitted-v2")
    reports = db.execute("SELECT row_to_json(t) FROM ai_report_runs t "
        "WHERE snapshot_json::jsonb->>'executionProfile' IN (%s,%s) "
        "ORDER BY id", profiles).fetchall()
    flows = db.execute("SELECT row_to_json(t) FROM ai_workflow_runs t "
        "WHERE input_json::jsonb->>'executionProfile' IN (%s,%s) "
        "ORDER BY id", profiles).fetchall()
    materials = db.execute("SELECT row_to_json(t) FROM " + sidecar +
        " t ORDER BY report_id").fetchall()
    return reports, flows, materials


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
            raise RuntimeError("isolated 0060 archive/restore failed")


def closed(db, installed):
    new_rows = db.execute("SELECT (SELECT count(*) FROM ai_report_runs WHERE "
        "snapshot_json::jsonb->>'executionProfile'=%s),"
        "(SELECT count(*) FROM ai_workflow_runs WHERE "
        "input_json::jsonb->>'executionProfile'=%s)",
        [migration.PROFILE, migration.PROFILE]).fetchone()
    if new_rows != (0, 0):
        raise AssertionError("empty 0060 upgrade seed gained execution rows")
    for signature in GUARDS:
        if (db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]
                is not None) is not installed:
            raise AssertionError("0060 function inventory drift")
    if db.execute("SELECT count(*) FROM ai_agent_tool_dispatches WHERE "
            "tool_name IN ('get_business_promotion_market_v2',"
            "'get_business_market_v2_screening_package')").fetchone() != (0,):
        raise AssertionError("market v2 unexpectedly dispatched")


def verify_install(db, before, after):
    if after[:3] != before[:3] or after[4][0] != before[4][0]:
        raise AssertionError("0060 changed old 81 rows, market roots, renderers or relations")
    guard_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0]: (signature, definition, definer)
        for signature, (definition, definer) in GUARDS.items()}
    changed_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0]: definition for signature, definition in CHANGED.items()}
    if (None in guard_keys or None in changed_keys
            or set(after[3]) != set(before[3]) | set(guard_keys)):
        raise AssertionError("0060 must add exactly four AI functions")
    for signature, prior in before[3].items():
        current = after[3][signature]
        if signature in changed_keys:
            if (prior[0] != current[0] or prior[2:] != current[2:]
                    or current[1] != changed_keys[signature].split("$$", 2)[1]):
                raise AssertionError("0044 guard OID/ACL/owner changed")
        elif current != prior:
            raise AssertionError("0060 changed old AI function: " + signature)
    for key, (signature, definition, definer) in guard_keys.items():
        details = after[3][key]
        owner = db.execute("SELECT pg_catalog.pg_get_userbyid(relowner) "
            "FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)",
            [sidecar]).fetchone()[0]
        if (details[1] != definition.split("$$", 2)[1]
                or details[3] != owner
                or details[4] is not definer
                or {item.replace(" ", "") for item in (details[5] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise AssertionError("0060 new guard body/security/path drift")
        privilege = db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(%s) "
            "AND a.grantee=0 AND a.privilege_type='EXECUTE')",
            [signature]).fetchone()
        if privilege != (False,):
            raise AssertionError("0060 guard PUBLIC EXECUTE reopened")
        unexpected = db.execute("SELECT count(*) FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a WHERE "
            "p.oid=to_regprocedure(%s) AND "
            "(pg_catalog.pg_get_userbyid(a.grantee)<>%s OR "
            "a.privilege_type<>'EXECUTE')", [signature, owner]).fetchone()
        if unexpected != (0,):
            raise AssertionError("0060 guard execution privilege widened")
    prior_triggers, current_triggers = before[4][1], after[4][1]
    if set(current_triggers) != set(prior_triggers) | set(TRIGGERS):
        raise AssertionError("0060 trigger set drift")
    for key, value in prior_triggers.items():
        if current_triggers[key] != value:
            raise AssertionError("0060 changed old trigger: " + str(key))
    for key, signature in TRIGGERS.items():
        row = current_triggers[key]
        target = db.execute("SELECT to_regprocedure(%s)::text", [signature]
            ).fetchone()[0]
        if (target is None or row[3] != target or row[5] != 'O'
                or row[6] is not (key[1] == "ai_market_v2_execution_complete")
                or row[7] is not (key[1] == "ai_market_v2_execution_complete")):
            raise AssertionError("0060 trigger target/enabled/deferral drift")


with connect() as db:
    closed(db, installed=False)
    before = snapshot(db)
archive_restore("market_v2_execution_before")
with connect("market_v2_execution_before") as copy:
    closed(copy, installed=False)
    if restored(snapshot(copy)) != restored(before):
        raise AssertionError("0059 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = snapshot(db)
    verify_install(db, before, after)
archive_restore("market_v2_execution_after")
with connect("market_v2_execution_after") as copy:
    closed(copy, installed=True)
    restored_after = snapshot(copy)
    if restored(restored_after) != restored(after):
        raise AssertionError("0060 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db, installed=False)
    if snapshot(db) != before:
        raise AssertionError("0060 empty reverse did not restore exact 0059 state")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    replayed = snapshot(db)
    verify_install(db, before, replayed)
    if (replayed[:3] != after[:3]
            or {key: details[1:] for key, details in replayed[3].items()} !=
               {key: details[1:] for key, details in after[3].items()}
            or replayed[4][0] != after[4][0]
            or {key: value[1:] for key, value in replayed[4][1].items()} !=
               {key: value[1:] for key, value in after[4][1].items()}):
        raise AssertionError("0060 reapply differed from first installation")

result = {"upgrade": "0059->0060", "oldAiTables": 81,
    "oldRowsDigestPreserved": before[0], "parkedAdmittedMaterialRootsPreserved": True,
    "rendererVersions": list(before[2]), "rendererBytesPreserved": True,
    "oldAiFunctionsOidBodyAclPreservedExceptTwoExact0044Guards": True,
    "old0044GuardsOidAclOwnerPreservedAndOldToOtherClosed": True,
    "newFunctionsAndTriggersExact": True, "sidecarAclClosed": True,
    "newProfileRowsAbsentInUpgradeSeed": True,
    "newPausedRowPositiveSeparatedToTargetPgTest":
        "ai_assistant.test_business_market_v2_execution_snapshot",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-execution-snapshot-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
