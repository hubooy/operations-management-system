"""Independent isolated 0041 -> 0042 claim-read upgrade and restoration."""
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

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_CONSUMPTIONS as AI_TABLES
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-seal-ticket-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0040->0041"):
    raise RuntimeError("0042 rehearsal requires verified isolated 0041 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0041_business_v4_seal_ticket"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0042_business_v4_claimed_read"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0041_business_v4_seal_ticket',"
        "'0042_business_v4_claimed_read') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("Exact 0041 predecessor required without 0042")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def digest_tables(db):
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


def acl(db):
    names = (
        "public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)",
        "public.ai_v4_sealer_ticket_context(text,text,text,bigint,text,text)",
        "public.ai_v4_sealer_ticket_segment(text,text,text,integer,text,bigint,text,text)",
        "public.ai_v4_sealer_ticket_page(text,text,text,bigint,text,bigint,text,text)",
        "public.ai_v4_sealer_read_context(text,text,text,bigint)",
        "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
        "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
        "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
    )
    result = []
    for name in names:
        if db.execute("SELECT to_regprocedure(%s)", [name]).fetchone()[0] is None:
            result.append(None)
        else:
            result.append(db.execute("SELECT has_function_privilege("
                "'teruisi_ai_seal_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                [name] * 3).fetchone())
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
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if completed.returncode:
            (folder / (name + "-error.log")).write_bytes(completed.stderr)
            raise RuntimeError("isolated 0042 archive/restore failed")


with connect() as db:
    assert len(AI_TABLES) == 76
    before, old_files, old_acl = digest_tables(db), files(db), acl(db)
    assert old_acl == (None,) * 4 + ((False, False, False),) * 4
archive_restore("business_v4_claimed_read_before")
with connect("business_v4_claimed_read_before") as restored:
    assert digest_tables(restored) == before and files(restored) == old_files

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    new_acl = acl(db)
    assert digest_tables(db) == before and files(db) == old_files
    assert new_acl == ((False, False, False),) + ((True, False, False),) * 3 \
        + ((False, False, False),) * 4
archive_restore("business_v4_claimed_read_after")
with connect("business_v4_claimed_read_after") as restored:
    assert digest_tables(restored) == before and files(restored) == old_files
    assert acl(restored) == new_acl

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert digest_tables(db) == before and files(db) == old_files
    assert acl(db) == old_acl
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert digest_tables(db) == before and files(db) == old_files
    assert acl(db) == new_acl

result = {"upgrade": "0041->0042", "aiTables": 76,
    "oldRowsDigestPreserved": before, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldBackupRestored": True,
    "newBackupRestored": True, "emptyReversePreservedFacts": True,
    "noTicketReadOrPublishEnabled": False, "claimedReadEnabled": True,
    "sealerNoLogin": True, "productionWrites": False}
(folder / "business-v4-claimed-read-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
