"""Isolated 0053 -> 0054 renderer-10 stage-only upgrade and restore."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
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
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-market-v2-admitted-paused-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0052->0053"):
    raise RuntimeError("0054 rehearsal requires verified isolated 0053 seed")

OLD = [("ai_assistant", "0053_business_market_v2_admitted_paused"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0054_business_promotion_budget_file_staging"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0053_business_market_v2_admitted_paused',"
        "'0054_business_promotion_budget_file_staging') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0054 requires exact 0053 predecessor without 0054")

migration = importlib.import_module(
    "ai_assistant.migrations.0054_business_promotion_budget_file_staging")
prior = importlib.import_module(
    "ai_assistant.migrations.0046_business_promotion_trial_file_guard")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
FILE_FUNCTIONS = (
    "public.ai_business_file_chunk_guard()",
    "public.ai_business_volume_chunk_guard()",
    "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
)
V9_HELPERS = (
    ("public.ai_business_promotion_trial_parent_requirements(text,text,text)",
     prior.PARENT_REQUIREMENTS),
    ("public.ai_business_promotion_trial_ready_requirements(text)",
     prior.READY_REQUIREMENTS),
)
V10_HELPER = ("public.ai_business_promotion_budget_parent_requirements(text,text,text)",
              migration.BUDGET_PARENT_REQUIREMENTS)
LEGACY = FILE_FUNCTIONS + tuple(signature for signature, _ in V9_HELPERS)


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    if len(AI_TABLES) != 79:
        raise AssertionError("0054 expected 79 historical AI tables")
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
        expected = "building" if version == 7 else "ready"
        if (row is None or row[:3] != (version, expected, 1) or not chunks
                or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                    for blob, sha in chunks)):
            raise AssertionError("renderer 1-7 historical bytes absent or corrupt")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def file_constraint(db):
    row = db.execute("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "WHERE c.conrelid='public.ai_business_file_runs'::regclass "
        "AND c.conname='ai_business_file_bound'").fetchone()
    if row is None:
        raise AssertionError("file renderer CHECK missing")
    return row[0]


def normalized_constraint(definition):
    statuses = ("queued", "building", "paused", "ready", "cancelled")
    original = "((ARRAY[" + ", ".join(
        f"'{status}'::character varying" for status in statuses) + "])::text[])"
    restored = "(ARRAY[" + ", ".join(
        f"('{status}'::character varying)::text" for status in statuses) + "])"
    if original not in definition and restored not in definition:
        raise AssertionError("file status CHECK shape changed")
    return definition.replace(original, "(FROZEN_STATUS_SET)").replace(
        restored, "(FROZEN_STATUS_SET)")


def function_state(db, signatures):
    result = []
    for signature in signatures:
        row = db.execute("SELECT p.oid,p.prosrc,p.proacl::text,"
            "pg_catalog.pg_get_userbyid(p.proowner),p.prosecdef,p.proconfig,"
            "p.prokind,p.provolatile,p.proargtypes::text,"
            "has_function_privilege('teruisi_ai_writer',p.oid,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',p.oid,'EXECUTE') "
            "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None:
            raise AssertionError("file guard function missing: " + signature)
        result.append(row)
    return tuple(result)


def v9_ready_rejection(db):
    try:
        db.execute("SELECT public.ai_business_promotion_trial_ready_requirements(%s)",
            ["nonexistent-v9-file-run"])
    except psycopg.Error as error:
        if "ai_promotion_ready_state_invalid" not in str(error):
            raise AssertionError("v9 ready function changed its missing-run denial") from error
        return True
    raise AssertionError("v9 ready function unexpectedly accepted a missing run")


def stage_closed(db, installed=False):
    if db.execute("SELECT count(*) FROM ai_business_file_runs WHERE renderer_version=10"
            ).fetchone() != (0,):
        raise AssertionError("0054 empty reverse requires no v10 file runs")
    row = db.execute("SELECT p.prosrc,p.proacl::text,p.prosecdef,p.proconfig,"
        "has_function_privilege('teruisi_ai_writer',p.oid,'EXECUTE'),"
        "has_function_privilege('teruisi_ai_reader',p.oid,'EXECUTE') "
        "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [V10_HELPER[0]]).fetchone()
    if not installed:
        if row is not None:
            raise AssertionError("v10 parent helper appeared before 0054")
        return None
    if (row is None or row[0] != V10_HELPER[1].split("$$", 2)[1]
            or row[2] is not False or row[3] is None
            or {entry.replace(" ", "") for entry in row[3]}
                != {"search_path=pg_catalog,public"}
            or row[4:] != (True, False)):
        raise AssertionError("v10 parent helper body, scope or ACL drift")
    public_acl = db.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
        "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=to_regprocedure(%s) "
        "AND acl.grantee=0 AND acl.privilege_type='EXECUTE')",
        [V10_HELPER[0]]).fetchone()
    if public_acl != (False,):
        raise AssertionError("v10 parent helper reopened PUBLIC execute")
    return row


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
            raise RuntimeError("isolated 0054 archive/restore failed")


with connect() as db:
    stage_closed(db)
    before = (table_digest(db), old_files(db), file_constraint(db),
        function_state(db, LEGACY), v9_ready_rejection(db))
    if tuple(row[1] for row in before[3][:5]) != tuple(
            definition.split("$$", 2)[1] for definition in migration.OLD_SQL):
        raise AssertionError("0053 file guards no longer match frozen v9 predecessor")
    if tuple(row[1] for row in before[3][5:]) != tuple(
            definition.split("$$", 2)[1] for _, definition in V9_HELPERS):
        raise AssertionError("0053 v9 parent/ready helper drift")
archive_restore("promotion_budget_v10_stage_before")
with connect("promotion_budget_v10_stage_before") as restored:
    stage_closed(restored)
    copy = (table_digest(restored), old_files(restored), file_constraint(restored),
        function_state(restored, LEGACY), v9_ready_rejection(restored))
    if (copy[0], copy[1], normalized_constraint(copy[2]),
            tuple(row[1:] for row in copy[3]), copy[4]) != (
            before[0], before[1], normalized_constraint(before[2]),
            tuple(row[1:] for row in before[3]), before[4]):
        raise AssertionError("0053 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    stage_closed(db, installed=True)
    after = (table_digest(db), old_files(db), file_constraint(db),
        function_state(db, LEGACY), v9_ready_rejection(db))
    if after[:2] != before[:2] or after[4] != before[4]:
        raise AssertionError("0054 modified historical AI rows, file bytes or v9 ready behavior")
    if after[2] == before[2] or not re.search(r"\b10\b", after[2]):
        raise AssertionError("0054 did not add bounded renderer-10 CHECK")
    if tuple(row[0] for row in after[3]) != tuple(row[0] for row in before[3]):
        raise AssertionError("0054 replaced a legacy file function OID")
    if tuple(row[1] for row in after[3][:5]) != tuple(
            definition.split("$$", 2)[1] for definition in migration.NEW_SQL):
        raise AssertionError("0054 file guard body is not exact migration SQL")
    if tuple(row[1:] for row in after[3][5:]) != tuple(
            row[1:] for row in before[3][5:]):
        raise AssertionError("0054 changed v9 parent/ready body, ACL or attributes")
    for old, new in zip(before[3][:5], after[3][:5]):
        if old[2:] != new[2:]:
            raise AssertionError("0054 changed old file function ACL or attributes")
    if ("ai_promotion_budget_renderer_unpublished" not in migration.RUN_GUARD
            or "ai_promotion_budget_renderer_unpublished" not in migration.COMPLETE_GUARD
            or "parent.renderer_version NOT IN (4,6,7,9,10)" not in migration.VOLUME_CHUNK_GUARD
            or "parent_renderer NOT IN (4,6,7,9,10)" not in migration.MANIFEST_GUARD):
        raise AssertionError("0054 version-10 guards are not default closed")
archive_restore("promotion_budget_v10_stage_after")
with connect("promotion_budget_v10_stage_after") as restored:
    stage_closed(restored, installed=True)
    copy = (table_digest(restored), old_files(restored), file_constraint(restored),
        function_state(restored, LEGACY), v9_ready_rejection(restored))
    if (copy[0], copy[1], normalized_constraint(copy[2]),
            tuple(row[1:] for row in copy[3]), copy[4]) != (
            after[0], after[1], normalized_constraint(after[2]),
            tuple(row[1:] for row in after[3]), after[4]):
        raise AssertionError("0054 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    stage_closed(db)
    reverted = (table_digest(db), old_files(db), file_constraint(db),
        function_state(db, LEGACY), v9_ready_rejection(db))
    if reverted != before:
        raise AssertionError("0054 empty reverse did not restore exact v9 predecessors")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    stage_closed(db, installed=True)
    replayed = (table_digest(db), old_files(db), file_constraint(db),
        function_state(db, LEGACY), v9_ready_rejection(db))
    if replayed != after:
        raise AssertionError("0054 reapply differed from first installation")

result = {"upgrade": "0053->0054", "aiTables": 79,
    "oldRowsDigestPreserved": before[0], "rendererVersions": list(before[1]),
    "rendererBytesPreserved": True, "v9FunctionOidAndAclPreserved": True,
    "v9ReadyDenialPreserved": True, "v10ParentHelperClosed": True,
    "v10ReadyDefaultClosed": True, "oldBackupRestored": True,
    "newBackupRestored": True, "emptyReverseAndReapply": True,
    "productionWrites": False}
(folder / "business-promotion-budget-v10-stage-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
