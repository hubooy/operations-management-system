"""Isolated 0072->0073 login-attestation upgrade, failed install and dual restore."""

from __future__ import annotations

import argparse
import hashlib
from importlib import import_module
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


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-market-v2-authority-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or seed.get("upgrade") != "0071->0072"
        or seed.get("beforeBackupRestored") is not True
        or seed.get("afterBackupRestored") is not True
        or seed.get("emptyReverseAndReapply") is not True):
    raise RuntimeError("0073 requires exact isolated restored 0072 seed")

candidate = import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
v2 = candidate.v2
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
DOWNLOAD_SOURCE = ROOT / "backend/ai_assistant/business_volume_files.py"
download_source_sha = hashlib.sha256(DOWNLOAD_SOURCE.read_bytes()).hexdigest()
OLD = ("ai_assistant", "0072_business_market_v2_authority_proposals")
NEW = ("ai_assistant", "0073_business_promotion_budget_v11_login_attestation")
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN (%s,%s) ORDER BY name", [OLD[1], NEW[1]])
    if [row[0] for row in cursor.fetchall()] != [OLD[1]]:
        raise RuntimeError("0073 requires exact 0072 predecessor")


def db(name: str | None = None) -> psycopg.Connection:
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def native(command: list[object], *, timeout: int = 300) -> None:
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    result = subprocess.run([str(item) for item in command], cwd=ROOT,
        env=env, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("isolated 0073 native command failed; diagnosticSha256="
            + hashlib.sha256(result.stderr[:16384]).hexdigest())


def archive_restore(name: str) -> None:
    archive = folder / (name + ".dump")
    if archive.exists():
        raise RuntimeError("0073 rehearsal archive already exists")
    native([BIN / "pg_dump.exe", "--format=custom", "--file", archive,
        database["NAME"]], timeout=600)
    native([BIN / "createdb.exe", name])
    native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
        "--dbname", name, archive], timeout=600)


def assert_formal_backup_closed() -> None:
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"]),
        "PGDATABASE": str(database["NAME"])}
    helper = ROOT / "tools/postgres-consistent-backup.py"
    preflight = subprocess.run([sys.executable, helper,
        "protected-preflight", "--expected-database", database["NAME"],
        "--expected-user", database["USER"], "--port",
        str(database["PORT"])], env=env, capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if preflight.returncode or "0073_business_promotion_budget_v11_login_attestation" not in (
            json.loads(preflight.stdout)["appliedProtectedMigrations"]):
        raise AssertionError("formal protected preflight missed 0073")
    blocked = folder / "v11_login_unadmitted_formal.dump"
    if blocked.exists():
        raise AssertionError("formal backup negative output already exists")
    result = subprocess.run([sys.executable, helper, "backup",
        "--pg-dump", BIN / "pg_dump.exe", "--output", blocked,
        "--expected-database", database["NAME"], "--expected-user",
        database["USER"], "--port", str(database["PORT"]),
        "--timeout-seconds", "60"], env=env, capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    payload = json.loads(result.stderr)
    expected = hashlib.sha256(
        b"protected AI daily backup is not admitted").hexdigest()
    if (result.returncode != 1 or blocked.exists() or
            payload.get("status") != "failed" or
            payload.get("errorSha256") != expected):
        raise AssertionError("formal backup did not fail before dump output")


def old_catalog(connection: psycopg.Connection):
    with connection.cursor() as cursor:
        old = candidate._frozen(cursor)
    files = connection.execute("SELECT run_id,attempt,volume_index,format,"
        "sequence,content,content_digest FROM public.ai_business_volume_chunks "
        "ORDER BY run_id,attempt,volume_index,format,sequence").fetchall()
    file_rows = []
    for run_id,attempt,index,kind,sequence,content,saved in files:
        actual = hashlib.sha256(bytes(content)).hexdigest()
        if actual != saved:
            raise AssertionError("0073 predecessor file chunk digest drift")
        file_rows.append((run_id,attempt,index,kind,sequence,actual))
    payload = json.dumps(file_rows, ensure_ascii=True,
        separators=(",", ":")).encode("ascii")
    return old, hashlib.sha256(payload).hexdigest(), len(file_rows)


def comparable(catalog):
    return (tuple((signature,(None,*row[1:])) for signature,row in catalog[0]),
        catalog[1:])


def verify_old(connection: psycopg.Connection) -> None:
    if connection.execute("SELECT to_regclass(%s)", [v2.TABLE]).fetchone()[0]:
        raise AssertionError("0073 table exists before migration/after reverse")
    for signature in v2.SIGNATURES:
        if connection.execute("SELECT to_regprocedure(%s)",
                [signature]).fetchone()[0]:
            raise AssertionError("0073 function exists before migration/after reverse")
    candidate._frozen(connection.cursor())


with db() as source:
    verify_old(source)
    before = old_catalog(source)
archive_restore("v11_login_before")
with db("v11_login_before") as copy:
    verify_old(copy)
    if comparable(old_catalog(copy)) != comparable(before):
        raise AssertionError("0073 pre-upgrade restored catalog/file differs")

# A preexisting NOLOGIN role with even a synthetic password must not be
# adopted by 0073. Django's atomic migration must leave no receipt or table.
with db() as source:
    source.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(v2.ROLE), sql.Literal("synthetic-only-0073")))
try:
    MigrationExecutor(connection).migrate([NEW])
except RuntimeError as error:
    if "must have no password" not in str(error):
        raise
else:
    raise AssertionError("0073 adopted preexisting NOLOGIN role with password")
connection.close()
with db() as source:
    verify_old(source)
    if source.execute("SELECT count(*) FROM django_migrations "
            "WHERE app=%s AND name=%s", NEW).fetchone() != (0,):
        raise AssertionError("0073 failed migration left a receipt")
    if old_catalog(source) != before:
        raise AssertionError("0073 failed migration changed predecessor")
    source.execute(sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
        sql.Identifier(v2.ROLE)))

MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if old_catalog(source) != before:
        raise AssertionError("0073 changed old function OID/body/ACL or file bytes")
    if source.execute("SELECT count(*) FROM " + v2.TABLE).fetchone() != (0,):
        raise AssertionError("0073 unexpectedly wrote a login attestation")
    if source.execute("SELECT rolcanlogin,rolpassword IS NULL FROM "
            "pg_catalog.pg_authid WHERE rolname=%s", [v2.ROLE]).fetchone() != (
            False, True):
        raise AssertionError("0073 role not closed after install")
    after = old_catalog(source)
assert_formal_backup_closed()
archive_restore("v11_login_after")
with db("v11_login_after") as copy:
    with copy.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if comparable(old_catalog(copy)) != comparable(after):
        raise AssertionError("0073 post-upgrade restored catalog/file differs")

MigrationExecutor(connection).migrate([OLD])
with db() as source:
    verify_old(source)
    if old_catalog(source) != before:
        raise AssertionError("0073 empty reverse changed predecessor")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if comparable(old_catalog(source)) != comparable(after):
        raise AssertionError("0073 reapply changed old functions or files")
if hashlib.sha256(DOWNLOAD_SOURCE.read_bytes()).hexdigest() != download_source_sha:
    raise AssertionError("0073 changed v11 hard-refusal download source")

result = {"upgrade":"0072->0073", "status":"passed",
    "preexistingNoLoginPasswordRejected":True,
    "failedMigrationRolledBackWithoutReceipt":True,
    "oldFunctionOidBodyAclOwnerPreserved":True,
    "oldFileChunkCount":before[2],"oldFileBytesSha256":before[1],
    "newProtectedTables":1,"newLoginRoles":1,
    "defaultRoleNoLoginAndNoPassword":True,
    "newAttestationRows":0,"readyAndDownloadAuthorized":False,
    "formalBackupPreflightFound0073":True,
    "formalBackupRejectedBeforeArchive":True,
    "downloadSourceSha256Unchanged":download_source_sha,
    "beforeBackupRestored":True,"afterBackupRestored":True,
    "emptyReverseAndReapply":True,"productionWrites":False}
(folder / "business-v11-login-attestation-upgrade-evidence.json").write_text(
    json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
