"""Isolated 0054 -> 0055 append-only period sidecar upgrade and restore."""
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
    AI_TABLES_PRE_V4_PERIOD_CANDIDATES as OLD_AI_TABLES,
    AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS as AI_TABLES)
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-budget-v10-stage-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0053->0054"):
    raise RuntimeError("0055 rehearsal requires verified isolated 0054 seed")

OLD = [("ai_assistant", "0054_business_promotion_budget_file_staging"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0055_business_v4_period_plan_candidate"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0054_business_promotion_budget_file_staging',"
        "'0055_business_v4_period_plan_candidate') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0055 requires exact 0054 predecessor without 0055")

migration = importlib.import_module(
    "ai_assistant.migrations.0055_business_v4_period_plan_candidate")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
NEW_FUNCTIONS = {
    migration.WRITE: (migration.RECORD, True),
    "public.ai_v4_period_plan_candidate_guard()": (migration.GUARD, False),
    "public.ai_v4_period_plan_no_truncate()": (migration.NO_TRUNCATE, False),
}
OLD_GUARDS = (
    "public.ai_business_v4_run_guard()",
    "public.ai_business_v4_source_guard()",
    "public.ai_business_v4_seal_guard()",
    "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
)
NEW_TRIGGERS = {
    "ai_v4_period_candidate_state": "public.ai_v4_period_plan_candidate_guard()",
    "ai_v4_period_candidate_no_truncate": "public.ai_v4_period_plan_no_truncate()",
}


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db, tables):
    if (len(OLD_AI_TABLES) != 79 or len(AI_TABLES) != 80
            or migration.TABLE.removeprefix("public.") in OLD_AI_TABLES
            or set(AI_TABLES) != set(OLD_AI_TABLES) |
                {migration.TABLE.removeprefix("public.")}
            or tables not in (OLD_AI_TABLES, AI_TABLES)):
        raise AssertionError("0055 requires frozen 79-table prestate and 80-table poststate")
    material = {}
    for table in sorted(tables):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


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
        expected = "building" if version == 7 else "ready"
        if (row is None or row[:3] != (version, expected, 1) or not chunks
                or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                    for blob, sha in chunks)):
            raise AssertionError("historical renderer 1-7 bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def function_catalog(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1").fetchall()
    result = {signature: details for signature, *details in rows}
    if len(result) != len(rows) or not result:
        raise AssertionError("old AI function catalog absent or ambiguous")
    return result


def schema_inventory(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind IN ('r','p','v','m','f','S','i','I') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,t.tgname,t.tgrelid::regclass::text,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid),t.tgenabled "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    return relations, triggers


def restorable_catalog(catalog):
    return {signature: details[1:] for signature, details in catalog.items()}


def normalized_inventory(inventory):
    # OIDs are reassigned in a physically independent restored database.
    return tuple(tuple(row[1:]) for row in inventory[0]), tuple(
        tuple(row[1:]) for row in inventory[1])


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
            raise RuntimeError("isolated 0055 archive or restore failed")


def sidecar_closed(db, *, installed=False):
    relation = db.execute("SELECT to_regclass(%s)", [migration.TABLE]).fetchone()[0]
    functions = {signature: db.execute("SELECT to_regprocedure(%s)",
        [signature]).fetchone()[0] for signature in NEW_FUNCTIONS}
    if not installed:
        if relation is not None or any(value is not None for value in functions.values()):
            raise AssertionError("0055 objects appeared before installation")
        return
    if relation is None or any(value is None for value in functions.values()):
        raise AssertionError("0055 sidecar table or function missing")
    if db.execute("SELECT count(*) FROM " + migration.TABLE).fetchone() != (0,):
        raise AssertionError("0055 empty reverse needs no period sidecar rows")
    columns = db.execute("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid=to_regclass(%s) AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY a.attnum", [migration.TABLE]).fetchall()
    expected_columns = [
        ("run_id", "character varying(160)", True),
        ("attempt_id", "character varying(160)", True),
        ("owner_email", "character varying(320)", True),
        ("actor_version", "bigint", True),
        ("plan_digest", "character varying(64)", True),
        ("directory_digest", "character varying(64)", True),
        ("source_root", "character varying(64)", True),
        ("envelope_json", "text", True),
        ("envelope_digest", "character varying(64)", True),
        ("created_at", "timestamp with time zone", True),
    ]
    if columns != expected_columns:
        raise AssertionError("0055 sidecar column shape changed")
    owner = db.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid=to_regclass(%s)",
        [migration.TABLE]).fetchone()[0]
    old_owner = db.execute("SELECT pg_catalog.pg_get_userbyid(relowner) FROM "
        "pg_catalog.pg_class WHERE oid='public.ai_business_v4_runs'::regclass"
        ).fetchone()[0]
    privileges = db.execute("SELECT "
        "has_table_privilege('teruisi_ai_writer',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),"
        "has_table_privilege('teruisi_ai_reader',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),"
        "has_table_privilege('teruisi_ai_seal_writer',%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
        [migration.TABLE] * 3).fetchone()
    if owner != old_owner or privileges != (False, False, False):
        raise AssertionError("0055 table owner or closed ACL changed")
    table_acl = db.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
        "a.privilege_type,a.is_grantable FROM pg_catalog.pg_class c,"
        "LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) a "
        "WHERE c.oid=to_regclass(%s) ORDER BY 1,2,3",
        [migration.TABLE]).fetchall()
    if (not table_acl or any(role != owner for role, _, _ in table_acl)
            or not {privilege for _, privilege, _ in table_acl} >=
                {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"}):
        raise AssertionError("0055 table has unexpected grantee or privilege")
    for signature, (definition, secured) in NEW_FUNCTIONS.items():
        row = db.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner),p.proacl::text "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if (row is None or row[0] != definition.split("$$")[1]
                or row[1] is not secured or row[3] != "plpgsql"
                or row[4] != owner or {v.replace(" ", "") for v in (row[2] or [])}
                != {"search_path=pg_catalog,public"}):
            raise AssertionError("0055 function body/owner/scope mismatch: " + signature)
        acl = db.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type,a.is_grantable FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3",
            [signature]).fetchall()
        expected_acl = [(owner, "EXECUTE", False)]
        if signature == migration.WRITE:
            expected_acl.append(("teruisi_ai_writer", "EXECUTE", False))
        if acl != sorted(expected_acl):
            raise AssertionError("0055 function ACL not default closed: " + signature)
    trigger_rows = db.execute("SELECT t.tgname,t.tgfoid::regprocedure::text,"
        "t.tgenabled FROM pg_catalog.pg_trigger t "
        "WHERE t.tgrelid=to_regclass(%s) AND NOT t.tgisinternal "
        "ORDER BY t.tgname", [migration.TABLE]).fetchall()
    expected_triggers = sorted((name, db.execute("SELECT to_regprocedure(%s)::text",
        [function]).fetchone()[0], "O") for name, function in NEW_TRIGGERS.items())
    if trigger_rows != expected_triggers:
        raise AssertionError("0055 table triggers changed or disabled")
    if db.execute("SELECT has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE')",
            [migration.WRITE]*3).fetchone() != (True, False, False):
        raise AssertionError("0055 writer-only function privilege changed")
    if db.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE')",
            ["public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"],
            ).fetchone() != (False,):
        raise AssertionError("0055 reopened direct seal")


with connect() as db:
    sidecar_closed(db)
    before = (table_digest(db, OLD_AI_TABLES), old_files(db), function_catalog(db),
        schema_inventory(db))
    guard_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0] for signature in OLD_GUARDS}
    if None in guard_keys or not guard_keys <= set(before[2]):
        raise AssertionError("old v4 source/seal/file guards missing")

archive_restore("v4_period_plan_before")
with connect("v4_period_plan_before") as restored:
    sidecar_closed(restored)
    copy = (table_digest(restored, OLD_AI_TABLES), old_files(restored),
        restorable_catalog(function_catalog(restored)),
        normalized_inventory(schema_inventory(restored)))
    if copy != (before[0], before[1], restorable_catalog(before[2]),
                normalized_inventory(before[3])):
        raise AssertionError("0054 before backup independent restore changed")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    sidecar_closed(db, installed=True)
    after = (table_digest(db, OLD_AI_TABLES), old_files(db), function_catalog(db),
        schema_inventory(db), table_digest(db, AI_TABLES))
    if after[:2] != before[:2]:
        raise AssertionError("0055 changed old 79 tables or renderer bytes")
    new_keys = {db.execute("SELECT to_regprocedure(%s)::text", [signature]
        ).fetchone()[0] for signature in NEW_FUNCTIONS}
    if set(after[2]) != set(before[2]) | new_keys:
        raise AssertionError("0055 changed the old AI function inventory")
    for signature, old in before[2].items():
        if after[2][signature] != old:
            raise AssertionError("0055 changed old function OID/body/ACL: " + signature)
    old_relations = {row[1]: row for row in before[3][0]}
    new_relations = {row[1]: row for row in after[3][0]}
    if any(new_relations.get(name) != row for name, row in old_relations.items()):
        raise AssertionError("0055 changed old relation identity or owner")
    added_relations = set(new_relations) - set(old_relations)
    if added_relations != {migration.TABLE.removeprefix("public."),
            "ai_business_v4_period_plan_candidates_pkey"}:
        raise AssertionError("0055 added unexpected relations")
    old_triggers = {row[1:3]: row for row in before[3][1]}
    new_triggers = {row[1:3]: row for row in after[3][1]}
    if any(new_triggers.get(key) != row for key, row in old_triggers.items()):
        raise AssertionError("0055 changed old source/seal/file triggers")
    if len(new_triggers) - len(old_triggers) != len(NEW_TRIGGERS):
        raise AssertionError("0055 added unexpected trigger count")

archive_restore("v4_period_plan_after")
with connect("v4_period_plan_after") as restored:
    sidecar_closed(restored, installed=True)
    copy = (table_digest(restored, OLD_AI_TABLES), old_files(restored),
        restorable_catalog(function_catalog(restored)),
        normalized_inventory(schema_inventory(restored)),
        table_digest(restored, AI_TABLES))
    if copy != (after[0], after[1], restorable_catalog(after[2]),
                normalized_inventory(after[3]), after[4]):
        raise AssertionError("0055 after backup independent restore changed")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    sidecar_closed(db)
    if (table_digest(db, OLD_AI_TABLES), old_files(db), function_catalog(db),
            schema_inventory(db)) != before:
        raise AssertionError("0055 empty reverse changed old DB state")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    sidecar_closed(db, installed=True)
    if (table_digest(db, OLD_AI_TABLES), old_files(db),
            restorable_catalog(function_catalog(db)),
            normalized_inventory(schema_inventory(db)),
            table_digest(db, AI_TABLES)) != (after[0], after[1],
                restorable_catalog(after[2]), normalized_inventory(after[3]),
                after[4]):
        raise AssertionError("0055 reapply differed from first install")

result = {"upgrade": "0054->0055", "oldAiTables": 79, "newAiTables": 80,
    "oldRowsAndRenderer1to7BytesPreserved": True,
    "everyOldAiFunctionOidBodyAclPreserved": True,
    "oldSourceSealFileGuardsUnchanged": True,
    "newSidecarTableFunctionTriggersExactAndClosed": True,
    "sidecarRows": 0, "candidateOnly": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True, "productionWrites": False}
(folder / "business-v4-period-plan-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
