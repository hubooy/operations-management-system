"""Isolated 0064 -> 0065 default-closed CNY requirement upgrade/restore."""
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
    AI_TABLES_PRE_MARKET_V2_COST_LEDGER_CANDIDATES as OLD_AI_TABLES,
    AI_TABLES_PRE_BUDGET_V11_ATTESTATIONS as AI_TABLES)
from ai_assistant.business_market_v2_context_catalog import verify as verify_context
from ai_assistant.business_market_v2_read_catalog import verify as verify_read
from ai_assistant.business_market_v2_execution_plan_catalog import verify as verify_plan
from ai_assistant.business_market_v2_synthetic_catalog import verify as verify_synthetic
from ai_assistant.business_market_v2_cost_catalog import verify as verify_cost
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-market-v2-synthetic-upgrade-evidence.json"
seed_proof = json.loads(seed.read_text(encoding="utf-8")) if seed.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or seed_proof.get("upgrade") != "0063->0064"
        or seed_proof.get("oldAiTables") != 84
        or seed_proof.get("newAiTables") != 84
        or seed_proof.get("beforeBackupRestored") is not True
        or seed_proof.get("afterBackupRestored") is not True
        or seed_proof.get("emptyReverseAndReapply") is not True
        or len(OLD_AI_TABLES) != 84 or len(AI_TABLES) != 85):
    raise RuntimeError("0065 requires verified isolated 0064 84-table predecessor")

OLD = [("ai_assistant", "0064_business_market_v2_synthetic_vertical"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0065_business_market_v2_model_cost_reservation"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0064_business_market_v2_synthetic_vertical',"
        "'0065_business_market_v2_model_cost_reservation') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0065 requires exact 0064 predecessor without 0065")

migration = importlib.import_module(
    "ai_assistant.migrations.0065_business_market_v2_model_cost_reservation")
synthetic = importlib.import_module(
    "ai_assistant.migrations.0064_business_market_v2_synthetic_vertical")
execution = importlib.import_module(
    "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
context = importlib.import_module(
    "ai_assistant.migrations.0061_business_market_v2_context_proof")
read = importlib.import_module(
    "ai_assistant.migrations.0062_business_market_v2_read_receipt_candidate")
sidecar = "public.ai_business_market_v2_materials"
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
GUARDS = {
    "public.ai_market_v2_cost_candidate_guard()": (migration.GUARD, False),
    migration.EXPECTED_SIGNATURE: (migration.EXPECTED, True),
    migration.WRITE_SIGNATURE: (migration.WRITE, True),
    migration.READ_SIGNATURE: (migration.READ, True),
}
TRIGGERS = {
    ("ai_business_market_v2_cost_ledger_candidates", "ai_market_v2_cost_candidate_guard"):
        "public.ai_market_v2_cost_candidate_guard()",
    ("ai_business_market_v2_cost_ledger_candidates", "ai_market_v2_cost_candidate_no_truncate"):
        "public.ai_v4_seal_ticket_no_truncate()",
}
NEW_RELATIONS = {
    "ai_business_market_v2_cost_ledger_candidates",
    "ai_business_market_v2_cost_ledger_candidates_pkey",
    "ai_business_market_v2_cost_ledger_candidates_plan_id_key",
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
            raise RuntimeError("isolated 0065 archive/restore failed")


def closed(db, installed):
    with db.cursor() as cursor:
        verify_context(cursor, RuntimeError)
        verify_read(cursor, RuntimeError)
        verify_plan(cursor, RuntimeError)
        verify_synthetic(cursor, RuntimeError)
    for signature in GUARDS:
        if (db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]
                is not None) is not installed:
            raise AssertionError("0065 function inventory drift")
    table_present = db.execute("SELECT to_regclass(%s)",
        [migration.TABLE]).fetchone()[0] is not None
    if table_present is not installed:
        raise AssertionError("0065 cost table inventory drift")
    if installed:
        if db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c,"
                "LATERAL pg_catalog.aclexplode(c.relacl) a "
                "WHERE c.oid=to_regclass(%s) AND a.grantee=0)",
                [migration.TABLE]).fetchone() != (False,):
            raise AssertionError("0065 cost table PUBLIC privilege reopened")
        with db.cursor() as cursor:
            verify_cost(cursor, RuntimeError)
        if db.execute("SELECT count(*) FROM " + migration.TABLE).fetchone() != (0,):
            raise AssertionError("empty reverse requires no cost candidate")
    role = db.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [migration.ROLE]).fetchone()
    if installed and role != (False,) * 7:
        raise AssertionError("0065 cost role not closed")
    if role is not None:
        if role != (False,) * 7 or db.execute(
                "SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
                "roleid=%s::regrole OR member=%s::regrole",
                [migration.ROLE, migration.ROLE]).fetchone() != (0,):
            raise AssertionError("0065 retained role drift")
        if not installed and db.execute("SELECT count(*) FROM "
                "pg_catalog.pg_proc p, pg_catalog.aclexplode(p.proacl) a "
                "WHERE p.pronamespace='public'::regnamespace "
                "AND p.proname LIKE %s "
                "AND a.grantee=%s::regrole",
                ["ai_market_v2_cost_%", migration.ROLE]).fetchone() != (0,):
            raise AssertionError("0065 reverse retained executable cost function")


def verify_install(db, before, after):
    if after[:3] != before[:3]:
        raise AssertionError("0065 changed old 84 rows, market roots, proofs or renderers")
    old_relations = {row[1]: row for row in before[4][0]}
    new_relations = {row[1]: row for row in after[4][0]}
    if (set(new_relations) != set(old_relations) | NEW_RELATIONS
            or any(new_relations[key] != old for key, old in old_relations.items())):
        raise AssertionError("0065 changed old relation identity or unexpected new relation")
    guard_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0]: (signature, definition, definer)
        for signature, (definition, definer) in GUARDS.items()}
    if (None in guard_keys
            or set(after[3]) != set(before[3]) | set(guard_keys)):
        raise AssertionError("0065 must add exactly four AI functions")
    for signature, prior in before[3].items():
        current = after[3][signature]
        if current != prior:
            raise AssertionError("0065 changed old AI function OID/body/ACL: " + signature)
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
            raise AssertionError("0065 new function body/security/path drift")
        privilege = db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=to_regprocedure(%s) "
            "AND a.grantee=0 AND a.privilege_type='EXECUTE')",
            [signature]).fetchone()
        if privilege != (False,):
            raise AssertionError("0065 function PUBLIC EXECUTE reopened")
    prior_triggers, current_triggers = before[4][1], after[4][1]
    if set(current_triggers) != set(prior_triggers) | set(TRIGGERS):
        raise AssertionError("0065 trigger set drift")
    for key, value in prior_triggers.items():
        if current_triggers[key] != value:
            raise AssertionError("0065 changed old trigger: " + str(key))
    for key, signature in TRIGGERS.items():
        row = current_triggers[key]
        target = db.execute("SELECT to_regprocedure(%s)::text", [signature]
            ).fetchone()[0]
        if (target is None or row[3] != target or row[5] != 'O'
                or row[6] is not False or row[7] is not False):
            raise AssertionError("0065 trigger target/enabled/deferral drift")
    with db.cursor() as cursor:
        verify_context(cursor, RuntimeError)
        verify_read(cursor, RuntimeError)
        verify_plan(cursor, RuntimeError)
        verify_synthetic(cursor, RuntimeError)
        verify_cost(cursor, RuntimeError)


with connect() as db:
    closed(db, installed=False)
    before = snapshot(db)
archive_restore("market_v2_cost_before")
with connect("market_v2_cost_before") as copy:
    closed(copy, installed=False)
    if restored(snapshot(copy)) != restored(before):
        raise AssertionError("0064 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = snapshot(db)
    verify_install(db, before, after)
archive_restore("market_v2_cost_after")
with connect("market_v2_cost_after") as copy:
    closed(copy, installed=True)
    restored_after = snapshot(copy)
    if restored(restored_after) != restored(after):
        raise AssertionError("0065 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db, installed=False)
    if snapshot(db) != before:
        raise AssertionError("0065 empty reverse did not restore exact 0064 state")
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
        raise AssertionError("0065 reapply differed from first installation")

result = {"upgrade": "0064->0065", "oldAiTables": 84,
    "newAiTables": 85,
    "oldRowsDigestPreserved": before[0], "marketRootsAndProofsPreserved": True,
    "rendererVersions": list(before[2]), "rendererBytesPreserved": True,
    "oldAiFunctionsOidBodyAclPreservedIncluding0044And0053Versions": True,
    "newCostTableIndexesFourFunctionsTwoTriggersExact": True,
    "newCostAttestorNoLoginNoMembership": True,
    "costTableAndFunctionAclClosedExceptNarrowRoleCalls": True,
    "newCostRowsAbsentInUpgradeSeed": True,
    "reservedCentsFixedZero": True,
    "providerCallsAllowed": False,
    "costAdmissionSeparatedToTargetPgTest":
        "ai_assistant.test_business_market_v2_cost_admission",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-cost-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
