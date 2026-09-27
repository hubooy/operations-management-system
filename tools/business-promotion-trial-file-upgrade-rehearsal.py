"""Isolated 0045 -> 0046 renderer-9 DB upgrade, restore, and empty reverse."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from importlib import import_module
from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_REPLAY_PROGRESS as AI_TABLES
from ai_assistant.health import _verify_promotion_trial_file_guard
from business_analysis.contracts import canonical

backup_spec = spec_from_file_location("promotion_trial_backup_guard",
    ROOT / "tools" / "postgres-consistent-backup.py")
backup_module = module_from_spec(backup_spec)
backup_spec.loader.exec_module(backup_module)


def frozen_gate(db):
    with db.cursor() as cursor:
        _verify_promotion_trial_file_guard(cursor)
        backup_module.verify_promotion_trial_file_guard(cursor)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-market-v2-material-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0044->0045"):
    raise RuntimeError("0046 rehearsal requires verified isolated 0045 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0045_business_market_v2_material_attestation"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0046_business_promotion_trial_file_guard"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0045_business_market_v2_material_attestation',"
        "'0046_business_promotion_trial_file_guard') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0046 requires exact 0045 predecessor without 0046")

new_migration = import_module("ai_assistant.migrations.0046_business_promotion_trial_file_guard")
FUNCTIONS = (
    "public.ai_business_file_chunk_guard()",
    "public.ai_business_volume_chunk_guard()",
    "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
)
HELPERS = (
    ("public.ai_business_promotion_trial_parent_requirements(text,text,text)",
     new_migration.PARENT_REQUIREMENTS),
    ("public.ai_business_promotion_trial_ready_requirements(text)",
     new_migration.READY_REQUIREMENTS),
)


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    material = {}
    for table in sorted(AI_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def files(db):
    result = {}
    for version in range(1, 8):
        run_id = ("promotion-old-file-" + str(version) if version < 7
                  else "promotion-renderer-seven-staged")
        row = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = ("ai_business_volume_chunks" if version in (4, 6, 7)
                 else "ai_business_file_chunks")
        order = ("volume_index,format,sequence" if version in (4, 6, 7)
                 else "format,sequence")
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        expected = "building" if version == 7 else "ready"
        if row is None or row[:3] != (version, expected, 1) or not chunks \
                or any(hashlib.sha256(bytes(blob)).hexdigest() != sha
                       for blob, sha in chunks):
            raise AssertionError("historical renderer bytes absent")
        result[version] = (row[3], hashlib.sha256(b"".join(bytes(blob)
            for blob, _ in chunks)).hexdigest())
    return result


def constraint(db):
    row = db.execute("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "WHERE c.conrelid='public.ai_business_file_runs'::regclass "
        "AND c.conname='ai_business_file_bound'").fetchone()
    if row is None:
        raise AssertionError("file renderer CHECK missing")
    return row[0]


def normalized_constraint(definition):
    """Postgres rewrites the fixed status array cast during pg_dump restore."""
    statuses = ("queued", "building", "paused", "ready", "cancelled")
    original = "((ARRAY[" + ", ".join(
        f"'{status}'::character varying" for status in statuses) + "])::text[])"
    restored = "(ARRAY[" + ", ".join(
        f"('{status}'::character varying)::text" for status in statuses) + "])"
    if original not in definition and restored not in definition:
        raise AssertionError("file status CHECK shape changed")
    return definition.replace(original, "(FROZEN_STATUS_SET)").replace(
        restored, "(FROZEN_STATUS_SET)")


def function_state(db):
    result = []
    for signature in FUNCTIONS:
        row = db.execute("SELECT p.oid,p.prosrc,p.prosecdef,"
            "has_function_privilege('teruisi_ai_writer',p.oid,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',p.oid,'EXECUTE') "
            "FROM pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None:
            raise AssertionError("historical file guard missing")
        result.append(row)
    return tuple(result)


def helper_state(db):
    result = []
    for signature, definition in HELPERS:
        row = db.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "has_function_privilege('teruisi_ai_writer',p.oid,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',p.oid,'EXECUTE') "
            "FROM pg_proc p WHERE p.oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None or row[0] != definition.split("$$")[1] or row[1] is not False \
                or "search_path=pg_catalog,public" not in ",".join(row[2] or []).replace(" ", "") \
                or row[3:] != (True, False):
            raise AssertionError("renderer9 helper body or ACL drift")
        result.append(row)
    return tuple(result)


def archive_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        completed = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            (folder / (name + "-error.log")).write_bytes(completed.stderr)
            raise RuntimeError("isolated 0046 archive/restore failed")


with connect() as db:
    assert len(AI_TABLES) == 78
    before, old_files, old_check, old_functions = (
        table_digest(db), files(db), constraint(db), function_state(db))
    assert all(db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()
        == (None,) for signature, _ in HELPERS)
archive_restore("business_promotion_trial_file_before")
with connect("business_promotion_trial_file_before") as restored:
    checks = (table_digest(restored) == before,
        files(restored) == old_files,
        normalized_constraint(constraint(restored)) == normalized_constraint(old_check),
        tuple(row[1:] for row in function_state(restored)) ==
            tuple(row[1:] for row in old_functions))
    if not all(checks):
        raise AssertionError(f"0045 pre-upgrade restore mismatch: {checks}")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert (table_digest(db), files(db)) == (before, old_files)
    frozen_gate(db)
    new_check, new_functions, new_helpers = constraint(db), function_state(db), helper_state(db)
    assert new_check != old_check
    assert tuple(row[0] for row in new_functions) == tuple(row[0] for row in old_functions)
    assert tuple(row[1] for row in new_functions) == tuple(
        statement.split("$$")[1] for statement in new_migration.NEW_SQL)
    assert tuple(row[2:] for row in new_functions) == tuple(row[2:] for row in old_functions)
    assert db.execute("SELECT count(*) FROM ai_business_file_runs WHERE renderer_version=9"
        ).fetchone() == (0,)
archive_restore("business_promotion_trial_file_after")
with connect("business_promotion_trial_file_after") as restored:
    frozen_gate(restored)
    assert (table_digest(restored), files(restored), normalized_constraint(constraint(restored)),
        tuple(row[1:] for row in function_state(restored)), helper_state(restored)) == (
            before, old_files, normalized_constraint(new_check),
            tuple(row[1:] for row in new_functions), new_helpers)

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert (table_digest(db), files(db), constraint(db), function_state(db)) == (
        before, old_files, old_check, old_functions)
    assert all(db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()
        == (None,) for signature, _ in HELPERS)
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    frozen_gate(db)
    assert (table_digest(db), files(db), constraint(db), function_state(db),
        helper_state(db)) == (before, old_files, new_check, new_functions, new_helpers)

result = {"upgrade": "0045->0046", "aiTables": 78,
    "oldRowsDigestPreserved": before, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldFunctionOidAndAclPreserved": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReversePreservedFacts": True,
    "renderer8Reserved": True, "renderer9PublisherEnabled": False,
    "productionWrites": False}
(folder / "business-promotion-trial-file-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
