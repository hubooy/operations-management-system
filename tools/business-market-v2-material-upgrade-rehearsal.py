"""Isolated 0044 -> 0045 market material sidecar upgrade and restore."""
import argparse
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
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

from ai_assistant.table_manifest import (AI_TABLES_PRE_V4_REPLAY_PROGRESS as AI_TABLES,
    AI_TABLES_PRE_MARKET_V2_MATERIALS as OLD_TABLES)
from business_analysis.contracts import canonical

backup_spec = spec_from_file_location("market_v2_backup_gate",
    ROOT / "tools" / "postgres-consistent-backup.py")
backup_gate = module_from_spec(backup_spec)
sys.modules[backup_spec.name] = backup_gate
backup_spec.loader.exec_module(backup_gate)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-market-v2-parked-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0043->0044"):
    raise RuntimeError("0045 rehearsal requires verified isolated 0044 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0044_business_market_v2_profile"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0045_business_market_v2_material_attestation"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0044_business_market_v2_profile',"
        "'0045_business_market_v2_material_attestation') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0045 requires exact 0044 predecessor without 0045")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db, names):
    material = {}
    for table in sorted(names):
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


def old_privileges(db):
    rows = db.execute("SELECT c.relname,r.role_name,"
        "has_table_privilege(r.role_name,'public.'||c.relname,'SELECT'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'INSERT'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'UPDATE'),"
        "has_table_privilege(r.role_name,'public.'||c.relname,'DELETE') "
        "FROM pg_class c CROSS JOIN (VALUES ('teruisi_ai_reader'),"
        "('teruisi_ai_writer'),('teruisi_ai_seal_writer')) r(role_name) "
        "WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s) "
        "ORDER BY c.relname,r.role_name", [list(OLD_TABLES)]).fetchall()
    if len(rows) != len(OLD_TABLES) * 3:
        raise AssertionError("historical AI role inventory changed")
    return rows


def material_acl(db):
    signature = "public.ai_market_v2_attest_material(text,text,text,text,text,text,text,text)"
    if db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0] is None:
        return None
    role = db.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE "
        "rolname='teruisi_ai_market_attestor'").fetchone()
    execute = db.execute("SELECT has_function_privilege('teruisi_ai_market_attestor',"
        "%s,'EXECUTE'),has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
        "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
        [signature] * 3).fetchone()
    table = db.execute("SELECT has_table_privilege('teruisi_ai_market_attestor',"
        "'public.ai_business_market_v2_materials','SELECT'),"
        "has_table_privilege('teruisi_ai_market_attestor',"
        "'public.ai_business_market_v2_materials','INSERT'),"
        "has_table_privilege('teruisi_ai_writer',"
        "'public.ai_business_market_v2_materials','INSERT')").fetchone()
    return role, execute, table


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
            raise RuntimeError("isolated 0045 archive/restore failed")


def backup_evidence(db, name, *, sidecar):
    # The fresh isolated predecessor does not provision unrelated production
    # domain reader roles. Exercise the exact 0044/0045 backup guard logic on
    # real PostgreSQL, while the dump/restore checks below cover all AI rows.
    with db.cursor() as cursor:
        backup_gate.verify_market_v2_parked_guards(cursor)
        if sidecar:
            backup_gate.verify_market_v2_material_attestation(cursor)
        else:
            cursor.execute("SELECT to_regclass('public.ai_business_market_v2_materials')")
            assert cursor.fetchone() == (None,)


with connect() as db:
    assert len(OLD_TABLES) == 77 and len(AI_TABLES) == 78
    before, files_before, privileges_before = (table_digest(db, OLD_TABLES),
        files(db), old_privileges(db))
    assert db.execute("SELECT to_regclass('public.ai_business_market_v2_materials')"
        ).fetchone() == (None,)
    backup_evidence(db, database["NAME"], sidecar=False)
archive_restore("business_market_v2_material_before")
with connect("business_market_v2_material_before") as restored:
    assert (table_digest(restored, OLD_TABLES), files(restored),
        old_privileges(restored)) == (before, files_before, privileges_before)
    backup_evidence(restored, "business_market_v2_material_before", sidecar=False)

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert (table_digest(db, OLD_TABLES), files(db), old_privileges(db)) == (
        before, files_before, privileges_before)
    assert db.execute("SELECT count(*) FROM ai_business_market_v2_materials"
        ).fetchone() == (0,)
    acl_after = material_acl(db)
    assert acl_after == ((False,) * 7, (True, False, False),
                         (False, False, False))
    backup_evidence(db, database["NAME"], sidecar=True)
    after = table_digest(db, AI_TABLES)
archive_restore("business_market_v2_material_after")
with connect("business_market_v2_material_after") as restored:
    assert (table_digest(restored, AI_TABLES), files(restored),
        old_privileges(restored), material_acl(restored)) == (
            after, files_before, privileges_before, acl_after)
    backup_evidence(restored, "business_market_v2_material_after", sidecar=True)

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert (table_digest(db, OLD_TABLES), files(db), old_privileges(db),
        material_acl(db)) == (before, files_before, privileges_before, None)
    assert db.execute("SELECT to_regclass('public.ai_business_market_v2_materials')"
        ).fetchone() == (None,)
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert (table_digest(db, AI_TABLES), files(db), old_privileges(db),
        material_acl(db)) == (after, files_before, privileges_before, acl_after)

result = {"upgrade": "0044->0045", "oldAiTables": 77, "newAiTables": 78,
    "oldRowsDigestPreserved": before, "rendererVersions": list(files_before),
    "rendererBytesPreserved": True, "oldBackupRestored": True,
    "newBackupRestored": True, "oldRolePrivilegesPreserved": True,
    "emptyReversePreservedFacts": True, "attestorNoLogin": True,
    "attestationOperational": False, "agentOrRendererEnabled": False,
    "productionWrites": False}
(folder / "business-market-v2-material-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
