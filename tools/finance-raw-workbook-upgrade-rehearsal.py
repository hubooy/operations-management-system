"""Test-only finance.0005→0006 upgrade with two independent empty restores."""
from __future__ import annotations

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

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or connection.vendor != "postgresql"):
    raise RuntimeError("finance.0006 requires disposable isolated cluster")

OLD = ("finance", "0005_raw_column_evidence_v2")
NEW = ("finance", "0006_raw_workbook_bytes_v2")
old = importlib.import_module("finance.migrations.0005_raw_column_evidence_v2")
new = importlib.import_module("finance.migrations.0006_raw_workbook_bytes_v2")
SIDE_TABLES = frozenset(old.TABLES) | frozenset(new.TABLES)
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")


with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='finance' "
        "AND name IN (%s,%s) ORDER BY name", [OLD[1], NEW[1]])
    if [row[0] for row in cursor.fetchall()] != [OLD[1]]:
        raise RuntimeError("finance.0006 requires exact 0005 predecessor")
    for table in new.TABLES:
        cursor.execute("SELECT to_regclass(%s)", ["public." + table])
        if cursor.fetchone() != (None,):
            raise RuntimeError("finance.0006 table exists in predecessor")


def db(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def assert_empty_sidecars(source, expected):
    present = {row[0] for row in source.execute("SELECT c.relname FROM "
        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND (c.relname LIKE 'finance_raw_column_evidence_%' OR "
        "c.relname LIKE 'finance_raw_workbook_%') "
        "AND c.relkind IN ('r','p')").fetchall()}
    if present != expected:
        raise RuntimeError("finance.0006 sidecar catalog inventory drift")
    for table in sorted(present):
        query = sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)").format(
            sql.Identifier("public", table))
        if source.execute(query).fetchone() != (False,):
            raise RuntimeError("finance.0006 sidecar contains source facts")


def old_catalog(source):
    with source.cursor() as cursor:
        old.verify_catalog(cursor)
    guard = source.execute("SELECT oid,prosrc,proacl::text,"
        "pg_catalog.pg_get_userbyid(proowner),proconfig "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
        [old.GUARD]).fetchone()
    guard_acl = tuple(source.execute("SELECT CASE WHEN a.grantee=0 THEN "
        "'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
        "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,a.is_grantable "
        "FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
        "pg_catalog.aclexplode(COALESCE(p.proacl,"
        "pg_catalog.acldefault('f',p.proowner))) a "
        "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3,4",
        [old.GUARD]).fetchall())
    tables = []
    for table in old.TABLES:
        identity = source.execute("SELECT c.oid,c.relkind,c.relacl::text,"
            "pg_catalog.pg_get_userbyid(c.relowner) "
            "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass",
            ["public." + table]).fetchone()
        table_acl = tuple(source.execute("SELECT CASE WHEN a.grantee=0 "
            "THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(a.grantee) "
            "END,pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_class c CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(c.relacl,"
            "pg_catalog.acldefault('r',c.relowner))) a "
            "WHERE c.oid=%s::regclass ORDER BY 1,2,3,4",
            ["public." + table]).fetchall())
        columns = source.execute("SELECT attnum,attname,format_type(atttypid,"
            "atttypmod),attnotnull,attacl::text FROM pg_catalog.pg_attribute "
            "WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped "
            "ORDER BY attnum", ["public." + table]).fetchall()
        triggers = source.execute("SELECT tgname,tgenabled,tgtype,tgfoid::"
            "regprocedure::text FROM pg_catalog.pg_trigger WHERE tgrelid="
            "%s::regclass AND NOT tgisinternal ORDER BY tgname",
            ["public." + table]).fetchall()
        tables.append((table, identity, tuple(columns), tuple(triggers),
            table_acl))
    rows = {}
    for table in ("finance_lines", "finance_months",
            "finance_import_batches", "finance_data_revisions"):
        values = source.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier("public", table))).fetchall()
        rows[table] = sorted(json.dumps(item[0], sort_keys=True,
            ensure_ascii=False, default=str) for item in values)
    row_digest = hashlib.sha256(json.dumps(rows, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return (guard, guard_acl), tuple(tables), row_digest


def restored_equal(left, right):
    # OIDs differ between independent restored databases.
    def without_oids(item):
        guard, tables, digest = item
        raw_guard, guard_acl = guard
        return ((raw_guard[1], raw_guard[3:], guard_acl),
            tuple((name, identity[1], identity[3], columns, triggers,
                table_acl) for name, identity, columns, triggers,
                table_acl in tables), digest)
    return without_oids(left) == without_oids(right)


def native(command, timeout=600):
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    result = subprocess.run([str(value) for value in command], cwd=ROOT,
        env=env, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("finance.0006 archive command failed; diagnosticSha256="
            + hashlib.sha256(result.stderr[:16384]).hexdigest())


def archive_restore(name, expected_tables):
    archive = folder / (name + ".dump")
    if archive.exists():
        raise RuntimeError("finance.0006 test archive already exists")
    with db() as source:
        assert_empty_sidecars(source, expected_tables)
    native([BIN / "pg_dump.exe", "--format=custom", "--file", archive,
        database["NAME"]])
    native([BIN / "createdb.exe", name])
    native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
        "--dbname", name, archive])
    return archive


def formal_preflight(after_archive):
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"]),
        "PGDATABASE": str(database["NAME"])}
    helper = ROOT / "tools/postgres-consistent-backup.py"
    target = folder / "finance_0006_formal_unadmitted.dump"
    if target.exists():
        raise RuntimeError("formal target already exists")
    rejected = subprocess.run([sys.executable, helper, "backup",
        "--pg-dump", BIN / "pg_dump.exe", "--output", target,
        "--expected-database", database["NAME"], "--expected-user",
        database["USER"], "--port", str(database["PORT"]),
        "--timeout-seconds", "60"], env=env, capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    expected = hashlib.sha256(
        b"finance raw workbook daily backup is not admitted").hexdigest()
    try:
        refusal = json.loads(rejected.stderr)
    except (ValueError, UnicodeError) as error:
        raise AssertionError("finance.0006 formal refusal not structured") from error
    if (rejected.returncode != 1 or target.exists()
            or refusal.get("status") != "failed"
            or refusal.get("errorType") != "RuntimeError"
            or refusal.get("errorSha256") != expected):
        raise AssertionError("finance.0006 formal backup did not fail first")
    listed = subprocess.run([str(BIN / "pg_restore.exe"), "--list",
        str(after_archive)], capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if (listed.returncode or not all(name.encode("ascii") in listed.stdout
            for name in new.TABLES)):
        raise AssertionError("finance.0006 after archive TOC incomplete")
    restore = subprocess.run([sys.executable, helper, "restore",
        "--pg-restore", BIN / "pg_restore.exe", "--archive", after_archive,
        "--expected-database", "irrelevant", "--expected-user",
        database["USER"], "--port", str(database["PORT"]),
        "--timeout-seconds", "60"], env=env, capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    expected_restore = hashlib.sha256(
        b"finance raw evidence archive restore is not admitted").hexdigest()
    try:
        restore_refusal = json.loads(restore.stderr)
    except (ValueError, UnicodeError) as error:
        raise AssertionError("finance.0006 restore refusal not structured") from error
    if (restore.returncode != 1 or restore_refusal.get("status") != "failed"
            or restore_refusal.get("errorSha256") != expected_restore):
        raise AssertionError("finance.0006 formal restore did not fail at TOC")


with db() as source:
    assert_empty_sidecars(source, frozenset(old.TABLES))
    before = old_catalog(source)
archive_restore("finance_bytes_before", frozenset(old.TABLES))
with db("finance_bytes_before") as restored:
    if not restored_equal(old_catalog(restored), before):
        raise AssertionError("finance.0005 predecessor independent restore drift")

MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        new.verify_catalog(cursor)
    assert_empty_sidecars(source, SIDE_TABLES)
    if old_catalog(source) != before:
        raise AssertionError("finance.0006 changed old rows or 0005 OID/ACL/body")
after_archive = archive_restore("finance_bytes_after", SIDE_TABLES)
formal_preflight(after_archive)
with db("finance_bytes_after") as restored:
    with restored.cursor() as cursor:
        new.verify_catalog(cursor)
    if not restored_equal(old_catalog(restored), before):
        raise AssertionError("finance.0006 after independent restore drift")

MigrationExecutor(connection).migrate([OLD])
with db() as source:
    assert_empty_sidecars(source, frozenset(old.TABLES))
    if old_catalog(source) != before:
        raise AssertionError("finance.0006 reverse changed old rows or schema")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        new.verify_catalog(cursor)
    if old_catalog(source) != before:
        raise AssertionError("finance.0006 reapply changed old rows or schema")

result = {"upgrade": "finance.0005->0006", "status": "passed",
    "oldV1And0005RowsDigest": before[2],
    "old0005FunctionOidBodyAclOwnerFrozen": True,
    "old0005TableOidOwnerAclColumnsTriggersFrozen": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "formalBackupRejectedBeforeArchive": True,
    "formalRestoreRejectedBeforePgRestore": True,
    "emptyReverseAndReapply": True,
    "newSidecarTablesEmpty": True,
    "productionWrites": False}
(folder / "finance-raw-workbook-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
