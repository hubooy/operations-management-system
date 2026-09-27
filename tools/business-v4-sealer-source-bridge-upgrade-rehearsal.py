"""Isolated 0048 -> 0049 claim-bound source bridge and restore."""
import argparse
import hashlib
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
from ai_assistant.v4_replay_progress_catalog import verify as verify_catalog
from ai_assistant.v4_sealer_source_catalog import verify as verify_source
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-finance-replay-progress-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0047->0048"):
    raise RuntimeError("0049 rehearsal requires verified isolated 0048 seed")

OLD = [("ai_assistant", "0048_business_v4_finance_replay_progress"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0049_business_v4_sealer_source_bridge"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0048_business_v4_finance_replay_progress',"
        "'0049_business_v4_sealer_source_bridge') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0049 requires exact 0048 predecessor without 0049")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PREVIOUS_FUNCTIONS = (
    "public.ai_business_file_chunk_guard()",
    "public.ai_business_volume_chunk_guard()",
    "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
    "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
    "public.ai_business_promotion_trial_ready_requirements(text)",
)
REPLAY_FUNCTIONS = (
    "public.ai_v4_replay_canonical(jsonb)",
    "public.ai_v4_sealer_replay_progress_guard()",
    "public.ai_v4_sealer_record_replay_progress(text,text,text,integer,text,bigint,text,text,text)",
    "public.ai_v4_sealer_replay_progress(text,text,text,integer,text,bigint,text,text)",
)


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


def old_functions(db, signatures=PREVIOUS_FUNCTIONS):
    values = []
    for signature in signatures:
        row = db.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,"
            "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner) "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None:
            raise AssertionError("frozen historical file function absent")
        values.append(row)
    return tuple(values)


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
            raise RuntimeError("isolated 0049 archive or restore failed")


def closed(db, *, source=False):
    with db.cursor() as cursor:
        verify_catalog(cursor, RuntimeError, finance_enabled=True)
        if source:
            verify_source(cursor, RuntimeError)


with connect() as db:
    assert len(AI_TABLES) == 79
    closed(db)
    before = (old_table_digest(db), old_files(db), old_functions(db),
              old_functions(db, REPLAY_FUNCTIONS))
    assert db.execute("SELECT to_regprocedure(%s)", [
        "public.ai_v4_sealer_ticket_source(text,text,text,text,bigint,text,text)"
        ]).fetchone() == (None,)
    assert db.execute("SELECT count(*) FROM ai_business_v4_sealer_replay_progress"
        ).fetchone() == (0,)
archive_restore("v4_sealer_source_before")
with connect("v4_sealer_source_before") as restored:
    closed(restored)
    assert (old_table_digest(restored), old_files(restored),
        tuple(row[1:] for row in old_functions(restored)),
        tuple(row[1:] for row in old_functions(restored, REPLAY_FUNCTIONS))) == (
            before[0], before[1], tuple(row[1:] for row in before[2]),
            tuple(row[1:] for row in before[3]))

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, source=True)
    assert (old_table_digest(db), old_files(db), old_functions(db),
        old_functions(db, REPLAY_FUNCTIONS)) == before
    assert db.execute("SELECT count(*) FROM ai_business_v4_sealer_replay_progress"
        ).fetchone() == (0,)
archive_restore("v4_sealer_source_after")
with connect("v4_sealer_source_after") as restored:
    closed(restored, source=True)
    assert (old_table_digest(restored), old_files(restored),
        tuple(row[1:] for row in old_functions(restored)),
        tuple(row[1:] for row in old_functions(restored, REPLAY_FUNCTIONS))) == (
            before[0], before[1], tuple(row[1:] for row in before[2]),
            tuple(row[1:] for row in before[3]))

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    closed(db)
    assert (old_table_digest(db), old_files(db), old_functions(db),
        old_functions(db, REPLAY_FUNCTIONS)) == before
    assert db.execute("SELECT to_regprocedure(%s)", [
        "public.ai_v4_sealer_ticket_source(text,text,text,text,bigint,text,text)"
        ]).fetchone() == (None,)
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    closed(db, source=True)
    assert (old_table_digest(db), old_files(db), old_functions(db),
        old_functions(db, REPLAY_FUNCTIONS)) == before

result = {"upgrade": "0048->0049", "aiTables": 79,
    "oldRowsAndRendererBytesPreserved": True,
    "oldFileFunctionOidBodyAndAclPreserved": True,
    "replayFunctionOidBodyAndAclPreserved": True,
    "sourceBridgeCatalogVerified": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapplyPassed": True,
    "newReplayRows": 0, "roleDefaultNoLogin": True,
    "candidateOnly": True, "sealCommitEnabled": False,
    "productionWrites": False}
(folder / "business-v4-sealer-source-bridge-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
