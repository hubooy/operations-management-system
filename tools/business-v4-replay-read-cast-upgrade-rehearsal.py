"""Isolated 0049 -> 0050 replay reader cast, restore, and empty reversal."""
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
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-sealer-source-bridge-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0048->0049"):
    raise RuntimeError("0050 rehearsal requires verified isolated 0049 seed")

OLD = [("ai_assistant", "0049_business_v4_sealer_source_bridge"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0050_business_v4_replay_read_cast"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0049_business_v4_sealer_source_bridge',"
        "'0050_business_v4_replay_read_cast') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0050 requires exact 0049 predecessor without 0050")

old_migration = importlib.import_module(
    "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
new_migration = importlib.import_module(
    "ai_assistant.migrations.0050_business_v4_replay_read_cast")
READ = old_migration.READ
OLD_BODY = old_migration.READ_SQL.split("$$")[1]
NEW_BODY = new_migration.READ.split("$$")[1]
if OLD_BODY == NEW_BODY:
    raise RuntimeError("0050 rehearsal requires a distinct cast-corrected reader")

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


def read_key(db):
    key = db.execute("SELECT to_regprocedure(%s)::text", [READ]).fetchone()[0]
    if key is None:
        raise AssertionError("replay reader missing")
    return key


def assert_catalog_change(before, after, key, expected_body):
    if before.keys() != after.keys() or key not in before:
        raise AssertionError("AI function inventory changed")
    for signature, old in before.items():
        now = after[signature]
        if signature == key:
            if (old[0] != now[0] or old[2:] != now[2:]
                    or now[1] != expected_body):
                raise AssertionError("READ OID, ACL, signature, or non-body property drift")
        elif now != old:
            raise AssertionError("non-READ AI function changed: " + signature)


def restorable_catalog(catalog):
    # OIDs can differ across independent restored databases; all other
    # properties, including function bodies and ACLs, must be exact.
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
            raise RuntimeError("isolated 0050 archive or restore failed")


def closed(db, *, read_cast=False):
    with db.cursor() as cursor:
        verify_replay(cursor, RuntimeError, finance_enabled=True,
            read_cast_enabled=read_cast)
        verify_source(cursor, RuntimeError)
    if db.execute("SELECT count(*) FROM ai_business_v4_sealer_replay_progress"
            ).fetchone() != (0,):
        raise AssertionError("replay progress must be empty for reversal")
    body = db.execute("SELECT prosrc FROM pg_catalog.pg_proc "
        "WHERE oid=to_regprocedure(%s)", [READ]).fetchone()
    if body != (NEW_BODY if read_cast else OLD_BODY,):
        raise AssertionError("replay reader body mismatch")


with connect() as db:
    assert len(AI_TABLES) == 79
    closed(db)
    before = (old_table_digest(db), old_files(db), function_catalog(db))
    reader = read_key(db)

archive_restore("v4_replay_read_cast_before")
with connect("v4_replay_read_cast_before") as restored:
    closed(restored)
    assert (old_table_digest(restored), old_files(restored),
        restorable_catalog(function_catalog(restored))) == (
        before[0], before[1], restorable_catalog(before[2]))

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, read_cast=True)
    after = (old_table_digest(db), old_files(db), function_catalog(db))
    assert after[:2] == before[:2]
    assert_catalog_change(before[2], after[2], reader, NEW_BODY)

archive_restore("v4_replay_read_cast_after")
with connect("v4_replay_read_cast_after") as restored:
    closed(restored, read_cast=True)
    assert (old_table_digest(restored), old_files(restored),
        restorable_catalog(function_catalog(restored))) == (
        after[0], after[1], restorable_catalog(after[2]))

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db)
    assert (old_table_digest(db), old_files(db), function_catalog(db)) == before

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, read_cast=True)
    assert (old_table_digest(db), old_files(db), function_catalog(db)) == after

result = {"upgrade": "0049->0050", "aiTables": 79,
    "oldRowsAndRendererBytesPreserved": True,
    "onlyReadBodyChanged": True,
    "readOidAclAndSignaturePreserved": True,
    "allOtherAiFunctionsPreserved": True,
    "sourceBridgeCatalogVerified": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True,
    "newReplayRows": 0, "roleDefaultNoLogin": True,
    "candidateOnly": True, "sealCommitEnabled": False,
    "productionWrites": False}
(folder / "business-v4-replay-read-cast-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
