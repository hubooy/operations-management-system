"""Isolated 0058 -> 0059 narrow reader fence upgrade and restore."""
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
    AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS,
    AI_TABLES_PRE_MARKET_V2_CONTEXT_PROOFS as AI_TABLES)
from business_analysis.contracts import canonical

OLD_TABLES = (*AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS,
    "ai_business_promotion_budget_v10_attestations")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-budget-v10-publish-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0057->0058"):
    raise RuntimeError("0059 requires verified isolated 0058 predecessor")
if len(OLD_TABLES) != 81 or len(AI_TABLES) != 81:
    raise AssertionError("0059 expected frozen 81-table AI inventory")

OLD = [("ai_assistant", "0058_business_promotion_budget_v10_publish_gate"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0059_business_promotion_budget_v10_reader_fence"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0058_business_promotion_budget_v10_publish_gate',"
        "'0059_business_promotion_budget_v10_reader_fence') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0059 requires exact 0058 predecessor without 0059")

migration = importlib.import_module(
    "ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence")
publication = migration.publication
attestation = migration.attestation
closed = importlib.import_module(
    "ai_assistant.migrations.0054_business_promotion_budget_file_staging")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
NEW_FUNCTIONS = (migration.BODY, migration.READ)


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def old_table_digest(db):
    material = {}
    for table in sorted(OLD_TABLES):
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
    value = {signature: details for signature, *details in rows}
    if len(value) != len(rows) or not value:
        raise AssertionError("AI function catalog absent or ambiguous")
    return value


def relation_trigger_catalog(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relkind IN ('r','p','v','m','f','S','i','I') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,c.relname,t.tgname,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid),t.tgenabled "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "ORDER BY c.relname,t.tgname").fetchall()
    return relations, triggers


def normalized(catalog):
    return {key: details[1:] for key, details in catalog.items()}


def restored_inventory(inventory):
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
            raise RuntimeError("isolated 0059 archive/restore failed")


def ready_guards(db):
    names = ("public.ai_business_files_guard()",
             "public.ai_business_volume_complete_guard()",
             "public.ai_business_promotion_trial_ready_requirements(text)")
    expected = (publication.RUN_GUARD, publication.COMPLETE_GUARD,
                closed.previous.READY_REQUIREMENTS)
    for signature, definition in zip(names, expected):
        row = db.execute("SELECT p.prosrc,p.prosecdef FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [signature]).fetchone()
        should_define = signature == names[1]
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not should_define):
            raise AssertionError("v9/v10 ready guards changed during reader bridge")
    if ("ai_budget_v10_ready_direct_write_denied" not in publication.RUN_GUARD
            or "session_user<>'teruisi_ai_budget_v10_attestor'" not in publication.RUN_GUARD
            or "ai_budget_v10_ready_requirements" not in publication.COMPLETE_GUARD):
        raise AssertionError("v10 direct writer ready guard changed")
    try:
        db.execute("SELECT public.ai_business_promotion_trial_ready_requirements(%s)",
            ["nonexistent-v9-attestation-audit"])
    except psycopg.Error as error:
        if "ai_promotion_ready_state_invalid" not in str(error):
            raise AssertionError("v9 ready denial changed") from error
    else:
        raise AssertionError("v9 ready function accepted missing run")


def reader_closed(db, *, installed):
    role = db.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [attestation.ROLE]).fetchone()
    if role != (False,) * 7:
        raise AssertionError("0059 verifier role no longer default NOLOGIN")
    if db.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole",
        [attestation.ROLE, attestation.ROLE]).fetchone() != (0,):
        raise AssertionError("0059 verifier role gained membership")
    if db.execute("SELECT count(*) FROM " + attestation.TABLE).fetchone() != (0,):
        raise AssertionError("0059 empty inverse requires no attestation")
    with db.cursor() as cursor:
        (migration.verify_catalog if installed else
            publication.verify_catalog)(cursor)
    for signature in (migration.BODY_SIGNATURE, migration.READ_SIGNATURE):
        present = db.execute("SELECT to_regprocedure(%s)",
            [signature]).fetchone()[0] is not None
        if present is not installed:
            raise AssertionError("0059 reader function inventory drift")
    if db.execute("SELECT count(*) FROM ai_business_file_runs WHERE "
            "renderer_version=10 AND status='ready'").fetchone() != (0,):
        raise AssertionError("0059 predecessor has unexpected ready file")
    if installed:
        with connect(db.info.dbname) as reader:
            reader.execute("SET SESSION AUTHORIZATION teruisi_ai_reader")
            try:
                try:
                    reader.execute("SELECT public.ai_budget_v10_download_receipt("
                        "%s,%s,%s,%s)", ["missing-v10-file",
                        "admin@example.invalid", 1, "a" * 64])
                except psycopg.Error as error:
                    if "ai_budget_v10_download_owner_or_ready_invalid" not in str(error):
                        raise AssertionError("0059 reader missing-run denial changed") from error
                else:
                    raise AssertionError("0059 reader accepted a missing ready file")
            finally:
                reader.execute("RESET SESSION AUTHORIZATION")


with connect() as db:
    reader_closed(db, installed=False)
    ready_guards(db)
    before = (old_table_digest(db), old_files(db), function_catalog(db),
        relation_trigger_catalog(db))
archive_restore("budget_v10_reader_fence_before")
with connect("budget_v10_reader_fence_before") as copy:
    reader_closed(copy, installed=False)
    ready_guards(copy)
    restored = (old_table_digest(copy), old_files(copy), function_catalog(copy),
        relation_trigger_catalog(copy))
    if (restored[0], restored[1], normalized(restored[2]),
            restored_inventory(restored[3])) != (
            before[0], before[1], normalized(before[2]),
            restored_inventory(before[3])):
        raise AssertionError("0058 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    reader_closed(db, installed=True)
    ready_guards(db)
    after = (old_table_digest(db), old_files(db), function_catalog(db),
        relation_trigger_catalog(db))
    if after[:2] != before[:2] or after[3] != before[3]:
        raise AssertionError("0059 changed old AI rows, renderer bytes, relations or triggers")
    for name, old in before[2].items():
        if after[2].get(name) != old:
            raise AssertionError("0059 changed old AI function OID/body/ACL: " + name)
    signatures = (migration.BODY_SIGNATURE, migration.READ_SIGNATURE)
    new_keys = {db.execute("SELECT to_regprocedure(%s)::text",
        [signature]).fetchone()[0] for signature in signatures}
    if None in new_keys or len(new_keys) != 2 or (
            set(after[2]) != set(before[2]) | new_keys):
        raise AssertionError("0059 must add exactly two narrow reader functions")
    for signature, definition in zip(signatures, (migration.BODY, migration.READ)):
        key = db.execute("SELECT to_regprocedure(%s)::text",
            [signature]).fetchone()[0]
        details = after[2][key]
        if (details[1] != definition.split("$$", 2)[1]
                or details[4] is not True
                or "search_path=pg_catalog,public" not in ",".join(
                    details[5] or []).replace(" ", "")):
            raise AssertionError("0059 reader function body or definer drift")
archive_restore("budget_v10_reader_fence_after")
with connect("budget_v10_reader_fence_after") as copy:
    reader_closed(copy, installed=True)
    ready_guards(copy)
    restored = (old_table_digest(copy), old_files(copy), function_catalog(copy),
        relation_trigger_catalog(copy))
    if (restored[0], restored[1], normalized(restored[2]),
            restored_inventory(restored[3])) != (
            after[0], after[1], normalized(after[2]),
            restored_inventory(after[3])):
        raise AssertionError("0059 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    reader_closed(db, installed=False)
    ready_guards(db)
    reverted = (old_table_digest(db), old_files(db), function_catalog(db),
        relation_trigger_catalog(db))
    if reverted != before:
        raise AssertionError("0059 empty reverse did not restore exact 0058 state")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    reader_closed(db, installed=True)
    ready_guards(db)
    replayed = (old_table_digest(db), old_files(db), function_catalog(db),
        relation_trigger_catalog(db))
    if (replayed[0], replayed[1], normalized(replayed[2]),
            replayed[3]) != (after[0], after[1], normalized(after[2]),
            after[3]):
        raise AssertionError("0059 reapply differed from first installation")

result = {"upgrade": "0058->0059", "oldAiTables": 81,
    "oldRowsDigestPreserved": before[0],
    "rendererVersions": list(before[1]), "rendererBytesPreserved": True,
    "oldAiFunctionOidBodyAclPreserved": True,
    "v9ReadyBehaviorAndV10PublishGuardPreserved": True,
    "v10ReadyRowsAbsentInSeed": True,
    "readerMissingRunDownloadDenied": True,
    "verifierNoLoginNoMembership": True,
    "newBodyAttestorOnlyAndReceiptReaderOnly": True,
    "readerNoAttestationTableSelect": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-promotion-budget-v10-reader-fence-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
