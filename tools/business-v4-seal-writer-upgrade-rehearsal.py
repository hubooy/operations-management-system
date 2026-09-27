"""Independent isolated 0037 -> 0038 seal-role DB upgrade/restore proof."""
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

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_TICKETS as AI_TABLES, AI_TABLES_PRE_V4_SEALS
from ai_assistant.test_business_v4_seal_writer_gate import BusinessV4SealWriterGateTests
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-v4-seal-admission-upgrade-evidence.json"
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime" or not seed.is_file()
        or json.loads(seed.read_text(encoding="utf-8")).get("upgrade") != "0036->0037"):
    raise RuntimeError("v4 seal writer upgrade requires verified isolated 0037 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = [("ai_assistant", "0037_business_v4_seal_admission_read"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0038_business_v4_seal_writer_gate"), *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' AND name IN "
        "('0037_business_v4_seal_admission_read','0038_business_v4_seal_writer_gate') "
        "ORDER BY name")
    if [item[0] for item in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("Exact 0037 predecessor required without 0038")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db, tables):
    material = {}
    for table in sorted(tables):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def grants(db, tables, *, include_sealer=False):
    roles = ["teruisi_ai_reader", "teruisi_ai_writer", "teruisi_finance_reader"]
    if include_sealer: roles.append("teruisi_ai_seal_writer")
    return db.execute("SELECT grantee,table_name,privilege_type FROM "
        "information_schema.role_table_grants WHERE grantee=ANY(%s) "
        "AND table_name=ANY(%s) ORDER BY 1,2,3",
        [roles, list(tables)]).fetchall()


def files(db):
    result = {}
    for version in range(1, 7):
        run_id = "promotion-old-file-" + str(version)
        parent = db.execute("SELECT renderer_version,status,attempt,manifest_json "
            "FROM ai_business_file_runs WHERE id=%s", [run_id]).fetchone()
        table = "ai_business_volume_chunks" if version in (4, 6) else "ai_business_file_chunks"
        order = "volume_index,format,sequence" if version in (4, 6) else "format,sequence"
        chunks = db.execute("SELECT content,content_digest FROM " + table +
            " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
        if parent is None or parent[:3] != (version, "ready", 1) or not chunks or any(
                hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
            raise AssertionError("old renderer bytes absent")
        result[version] = (parent[3], hashlib.sha256(b"".join(bytes(data)
            for data, _ in chunks)).hexdigest())
    staged = db.execute("SELECT renderer_version,status,attempt,manifest_json "
        "FROM ai_business_file_runs WHERE id='promotion-renderer-seven-staged'").fetchone()
    chunks = db.execute("SELECT content,content_digest FROM ai_business_volume_chunks "
        "WHERE run_id='promotion-renderer-seven-staged' "
        "ORDER BY volume_index,format,sequence").fetchall()
    if staged is None or staged[:3] != (7, "building", 1) or not chunks or any(
            hashlib.sha256(bytes(data)).hexdigest() != sha for data, sha in chunks):
        raise AssertionError("renderer 7 staged bytes absent")
    result[7] = (staged[3], hashlib.sha256(b"".join(bytes(data)
        for data, _ in chunks)).hexdigest())
    return result


def archive_and_restore(name):
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
            raise RuntimeError("isolated v4 seal-writer archive/restore failed")


MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    before, old_acl, old_files = (table_digest(db, AI_TABLES_PRE_V4_SEALS),
        grants(db, AI_TABLES_PRE_V4_SEALS), files(db))
    old_sealer_acl = grants(db, AI_TABLES_PRE_V4_SEALS, include_sealer=True)
    assert db.execute("SELECT rolcanlogin,rolinherit,rolsuper FROM pg_roles "
        "WHERE rolname='teruisi_ai_seal_writer'").fetchone() == (False, False, False)
archive_and_restore("business_v4_seal_writer_before")
with connect("business_v4_seal_writer_before") as restored:
    assert table_digest(restored, AI_TABLES_PRE_V4_SEALS) == before
    assert grants(restored, AI_TABLES_PRE_V4_SEALS) == old_acl
    assert files(restored) == old_files

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V4_SEALS) == before
    assert grants(db, AI_TABLES_PRE_V4_SEALS) == old_acl
    assert files(db) == old_files
    expected_new_acl = grants(db, ("ai_business_v4_seals",), include_sealer=True)
    assert ("teruisi_ai_writer", "ai_business_v4_seals", "SELECT") in expected_new_acl
    assert not any(grantee == "teruisi_ai_writer" and privilege in
        {"INSERT","UPDATE","DELETE"} for grantee, _, privilege in expected_new_acl)
    assert not any(grantee == "teruisi_ai_seal_writer" and privilege in
        {"INSERT","UPDATE","DELETE"} for grantee, _, privilege in expected_new_acl)
    assert db.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',"
        "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
        "'EXECUTE'),has_function_privilege('teruisi_ai_writer',"
        "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
        "'EXECUTE'),has_function_privilege('teruisi_ai_reader',"
        "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
        "'EXECUTE')").fetchone() == (True, False, False)
    assert db.execute("SELECT count(*) FROM ai_business_v4_seals").fetchone()[0] == 0
    sealed_phase_acl = grants(db, AI_TABLES_PRE_V4_SEALS, include_sealer=True)
MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    assert db.execute("SELECT to_regclass('public.ai_business_v4_seals')").fetchone()[0] is None
    assert table_digest(db, AI_TABLES_PRE_V4_SEALS) == before
    assert grants(db, AI_TABLES_PRE_V4_SEALS) == old_acl
    assert grants(db, AI_TABLES_PRE_V4_SEALS, include_sealer=True) == old_sealer_acl
    assert files(db) == old_files
    assert db.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',"
        "'public.ai_v4_lock_source_revisions_for_admission()','EXECUTE')").fetchone()[0] is False
MigrationExecutor(connection).migrate(NEW)

# A disposable test fixture supplies fully validated 0036 segments and a true
# session_user switch. Its synthetic 64-hex MAC only proves the DB role/state
# boundary; no application HMAC verification or report authority is claimed.
probe = BusinessV4SealWriterGateTests(methodName=
    "test_true_sealer_session_can_atomically_seal_only_complete_mixed_run")
probe.setUp()
attempt_id = probe.attempt()
body = probe.body(attempt_id)
probe.call_as_sealer(body)
# Leave synthetic NOLOGIN business-reader role identities in this disposable
# isolated cluster so restored guard catalog checks remain meaningful.
with connect() as db:
    assert table_digest(db, AI_TABLES_PRE_V4_SEALS) != before
    after = table_digest(db, AI_TABLES)
    assert db.execute("SELECT status FROM ai_business_v4_runs WHERE id=%s",
        [body["runId"]]).fetchone() == ("sealed",)
    assert db.execute("SELECT body_mac FROM ai_business_v4_seals WHERE run_id=%s",
        [body["runId"]]).fetchone() == ("f" * 64,)
    assert files(db) == old_files
archive_and_restore("business_v4_seal_writer_after")
with connect("business_v4_seal_writer_after") as restored:
    assert table_digest(restored, AI_TABLES) == after
    assert files(restored) == old_files
    assert grants(restored, AI_TABLES_PRE_V4_SEALS) == old_acl
    assert grants(restored, AI_TABLES_PRE_V4_SEALS, include_sealer=True) == sealed_phase_acl
    assert grants(restored, ("ai_business_v4_seals",), include_sealer=True) == expected_new_acl
    assert restored.execute("SELECT status FROM ai_business_v4_runs WHERE id=%s",
        [body["runId"]]).fetchone() == ("sealed",)
try:
    MigrationExecutor(connection).migrate(OLD)
except RuntimeError as error:
    if "存在v4封存父任务" not in str(error): raise
else:
    raise AssertionError("reverse migration discarded a sealed v4 parent")
assert MigrationExecutor(connection).migration_plan(NEW) == []

result = {"upgrade": "0037->0038", "oldAiTables": len(AI_TABLES_PRE_V4_SEALS),
    "newAiTables": len(AI_TABLES), "oldRowsDigestPreserved": before,
    "afterRowsDigest": after, "rendererVersions": list(old_files),
    "rendererBytesPreserved": True, "oldBackupRestored": True,
    "newBackupRestored": True, "oldAclRestoredOnEmptyReverse": True,
    "newSealWriterExecuteOnly": True, "noLoginDefault": True,
    "emptyReversePassed": True, "sealedReverseDenied": True,
    "syntheticDbRoleSealNotApplicationHmacVerified": True,
    "sealerCliOrProductionActivated": False, "productionWrites": False}
(folder / "business-v4-seal-writer-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
