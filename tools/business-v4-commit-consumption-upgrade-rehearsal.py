"""Isolated 0051 -> 0052 commit-with-consumption upgrade and restore."""
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

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_PERIOD_CANDIDATES as AI_TABLES
from ai_assistant.v4_replay_progress_catalog import verify as verify_replay
from ai_assistant.v4_sealer_source_catalog import verify as verify_source
from ai_assistant.v4_commit_consumption_catalog import verify as verify_commit
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-prior-claim-qualification-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0050->0051"):
    raise RuntimeError("0052 rehearsal requires verified isolated 0051 seed")

OLD = [("ai_assistant", "0051_business_v4_prior_claim_column"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0052_business_v4_commit_consumption"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0051_business_v4_prior_claim_column',"
        "'0052_business_v4_commit_consumption') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0052 requires exact 0051 predecessor without 0052")

migration = importlib.import_module(
    "ai_assistant.migrations.0052_business_v4_commit_consumption")
SIGNATURE = migration.SIGNATURE
NEW_BODY = migration.SQL.split("$$")[1]
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def old_table_digest(db):
    material = {}
    for table in sorted(AI_TABLES):
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
        if (row is None or row[:3] != (version,
                "building" if version == 7 else "ready", 1)
                or not chunks or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                    for blob, sha in chunks)):
            raise AssertionError("historical renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def function_catalog(db):
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


def schema_inventory(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind IN ('r','p','v','m','f','S') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,t.tgname,t.tgrelid::regclass::text,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    return relations, triggers


def restorable_catalog(catalog):
    # PostgreSQL assigns new OIDs in independent restored databases.
    return {signature: details[1:] for signature, details in catalog.items()}


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
            raise RuntimeError("isolated 0052 archive or restore failed")


def closed(db, *, installed=False):
    with db.cursor() as cursor:
        verify_replay(cursor, RuntimeError, finance_enabled=True,
            read_cast_enabled=True, prior_claim_qualified=True)
        verify_source(cursor, RuntimeError)
        if installed:
            verify_commit(cursor, RuntimeError)
    if db.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'").fetchone() != (False,) * 7:
        raise AssertionError("sealer role no longer default NOLOGIN")
    if db.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE')",
            ["public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"],
            ).fetchone() != (False,):
        raise AssertionError("direct seal commit reopened")
    if db.execute("SELECT count(*) FROM ai_business_v4_sealer_replay_progress"
            ).fetchone() != (0,):
        raise AssertionError("replay progress must be empty for reversal")
    exists = db.execute("SELECT to_regprocedure(%s)", [SIGNATURE]).fetchone()[0]
    if (exists is not None) != installed:
        raise AssertionError("new commit function existence mismatch")
    if installed:
        expected_owner = db.execute("SELECT pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            ["public.ai_v4_sealer_consumption_result(text,text,text,text,text)"],
            ).fetchone()[0]
        prior_owner = db.execute("SELECT pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            ["public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"],
            ).fetchone()[0]
        row = db.execute("SELECT pg_catalog.pg_get_function_result(p.oid),p.prosecdef,"
            "p.proconfig,l.lanname,pg_catalog.pg_get_userbyid(p.proowner),"
            "p.prosrc,p.proacl::text FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [SIGNATURE]).fetchone()
        if (row is None or row[0] != ("TABLE(run_id text, evidence_version bigint, "
                "sealed_digest text, consumed_at timestamp with time zone)")
                or row[1] is not True or {v.replace(" ", "") for v in (row[2] or [])} != {
                    "search_path=pg_catalog,public"} or row[3] != "plpgsql"
                or row[4] != expected_owner or row[4] != prior_owner
                or row[5] != NEW_BODY
                or row[4] in {"teruisi_ai_reader", "teruisi_ai_writer",
                    "teruisi_ai_seal_writer"}):
            raise AssertionError("new commit function signature, SECDEF or owner mismatch")
        privileges = db.execute("SELECT "
            "has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE'),"
            "EXISTS(SELECT 1 FROM pg_catalog.aclexplode(p.proacl) acl "
            "WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [SIGNATURE]*4).fetchone()
        if privileges != (True, False, False, False):
            raise AssertionError("new commit function ACL mismatch")
        acl = db.execute("SELECT pg_catalog.pg_get_userbyid(a.grantee),"
            "a.privilege_type,a.is_grantable FROM pg_catalog.pg_proc p,"
            "LATERAL pg_catalog.aclexplode(p.proacl) a "
            "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3",
            [SIGNATURE]).fetchall()
        if acl != [(expected_owner, "EXECUTE", False),
                   ("teruisi_ai_seal_writer", "EXECUTE", False)]:
            raise AssertionError("new commit function exact ACL mismatch")


with connect() as db:
    if len(AI_TABLES) != 79:
        raise AssertionError("0052 requires 79 historical AI tables")
    closed(db)
    before = (old_table_digest(db), old_files(db), function_catalog(db),
        schema_inventory(db))

archive_restore("v4_commit_consumption_before")
with connect("v4_commit_consumption_before") as restored:
    closed(restored)
    assert (old_table_digest(restored), old_files(restored),
        restorable_catalog(function_catalog(restored)),
        tuple(tuple(row[1:]) for row in schema_inventory(restored)[0]),
        tuple(tuple(row[1:]) for row in schema_inventory(restored)[1])) == (
        before[0], before[1], restorable_catalog(before[2]),
        tuple(tuple(row[1:]) for row in before[3][0]),
        tuple(tuple(row[1:]) for row in before[3][1]))

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    after = (old_table_digest(db), old_files(db), function_catalog(db),
        schema_inventory(db))
    new_key = db.execute("SELECT to_regprocedure(%s)::text", [SIGNATURE]).fetchone()[0]
    if (after[:2] != before[:2] or after[3] != before[3]
            or new_key is None
            or set(after[2]) != set(before[2]) | {new_key}):
        raise AssertionError("0052 changed historical tables, files, or AI function inventory")
    for signature, old in before[2].items():
        if after[2][signature] != old:
            raise AssertionError("0052 changed previous AI function: " + signature)

archive_restore("v4_commit_consumption_after")
with connect("v4_commit_consumption_after") as restored:
    closed(restored, installed=True)
    assert (old_table_digest(restored), old_files(restored),
        restorable_catalog(function_catalog(restored)),
        tuple(tuple(row[1:]) for row in schema_inventory(restored)[0]),
        tuple(tuple(row[1:]) for row in schema_inventory(restored)[1])) == (
        after[0], after[1], restorable_catalog(after[2]),
        tuple(tuple(row[1:]) for row in after[3][0]),
        tuple(tuple(row[1:]) for row in after[3][1]))

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db)
    assert (old_table_digest(db), old_files(db), function_catalog(db),
        schema_inventory(db)) == before

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, installed=True)
    assert (old_table_digest(db), old_files(db),
        restorable_catalog(function_catalog(db)), schema_inventory(db)) == (
        after[0], after[1], restorable_catalog(after[2]), after[3])

result = {"upgrade": "0051->0052", "aiTables": 79,
    "oldRowsAndRendererBytesPreserved": True,
    "everyPreviousAiFunctionOidBodyAclPreserved": True,
    "newCommitFunctionExactSignatureSecdefAclOwner": True,
    "sourceAndReplayCatalogsVerified": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True,
    "newReplayRows": 0, "roleDefaultNoLogin": True,
    "directSealCommitEnabled": False, "productionWrites": False}
(folder / "business-v4-commit-consumption-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
