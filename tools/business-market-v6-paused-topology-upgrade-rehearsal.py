"""Disposable 0076→0077 upgrade, frozen predecessor and dual empty restore."""
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
parser.add_argument("--run-root", required=True, type=Path)
args = parser.parse_args()
folder = args.run_root.resolve()
database = settings.DATABASES["default"]
OLD = ("ai_assistant", "0076_business_promotion_budget_v11_ticket_bound_signer")
NEW = ("ai_assistant", "0077_business_market_v6_paused_topology")
candidate = importlib.import_module("ai_assistant.migrations." + NEW[1])
old76 = importlib.import_module("ai_assistant.migrations." + OLD[1])
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD_TABLES = frozenset({
    "protected_business_budget_v11_verifier_keys",
    "protected_business_budget_v11_proof_tickets",
    "protected_business_budget_v11_proof_ticket_claims",
    "protected_business_budget_v11_login_attestations",
    "protected_business_budget_v11_signed_receipts_v3",
    "protected_business_v4_report_link_intents",
    "protected_business_v4_report_source_links",
    "protected_business_market_v2_rate_proposals",
    "protected_business_market_v2_cap_proposals",
    "protected_business_market_v2_authority_revocations",
    "protected_business_market_v2_human_cap_approvals",
    "protected_business_market_v2_human_cap_revocations",
})
NEW_TABLES = OLD_TABLES | frozenset({
    "protected_business_market_v6_topologies",
    "protected_business_market_v6_topology_cancellations",
})


if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or connection.vendor != "postgresql"):
    raise RuntimeError("0077 upgrade requires exact disposable PostgreSQL")


def db(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def native(command, timeout=600):
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    result = subprocess.run([str(item) for item in command], cwd=ROOT,
        env=env, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("0077 archive command failed; diagnosticSha256=" +
            hashlib.sha256(result.stderr[:16384]).hexdigest())


def protected_names(source):
    return frozenset(row[0] for row in source.execute(
        "SELECT c.relname FROM pg_catalog.pg_class c JOIN "
        "pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE "
        "n.nspname='public' AND c.relname LIKE 'protected_business_%' "
        "AND c.relkind IN ('r','p')").fetchall())


def assert_empty(source, expected):
    if protected_names(source) != expected:
        raise RuntimeError("0077 protected table inventory drift")
    for table in expected:
        row = source.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)")
            .format(sql.Identifier("public", table))).fetchone()
        if row != (False,):
            raise RuntimeError("0077 refuses clear archive of protected data")


def legacy_file_rows(source):
    tables = ("ai_business_file_chunks", "ai_business_volume_chunks")
    result = []
    for table in tables:
        row = source.execute(sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier("public",table))).fetchone()
        result.append((table,int(row[0])))
    return tuple(result)


def function_catalog(source):
    signatures = [item[0] for item in candidate._old_functions(source.cursor())]
    signatures += list(old76.v3.SIGNATURES)
    rows = []
    for signature in signatures:
        row = source.execute("SELECT oid,prosrc,proacl::text,"
            "pg_catalog.pg_get_userbyid(proowner),prosecdef,proconfig "
            "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None:
            raise RuntimeError("0077 predecessor function missing")
        acl = source.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' "
            "ELSE pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_proc p CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a "
            "WHERE p.oid=to_regprocedure(%s) ORDER BY 1,2,3,4",
            [signature]).fetchall()
        rows.append((signature, row, tuple(acl)))
    return tuple(rows)


def restored_equal(actual, expected):
    return tuple((name, row[1], row[3:], acl) for name, row, acl in actual) == \
        tuple((name, row[1], row[3:], acl) for name, row, acl in expected)


def table_catalog(source, names):
    result = []
    for name in sorted(names):
        row = source.execute("SELECT c.relkind,"
            "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
            "WHERE c.oid=%s::regclass", ["public." + name]).fetchone()
        acl = source.execute("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(a.grantee) END,"
            "pg_catalog.pg_get_userbyid(a.grantor),a.privilege_type,"
            "a.is_grantable FROM pg_catalog.pg_class c CROSS JOIN LATERAL "
            "pg_catalog.aclexplode(COALESCE(c.relacl,"
            "pg_catalog.acldefault('r',c.relowner))) a "
            "WHERE c.oid=%s::regclass ORDER BY 1,2,3,4",
            ["public." + name]).fetchall()
        if row is None:
            raise RuntimeError("0077 predecessor protected table missing")
        result.append((name, row, tuple(acl)))
    return tuple(result)


def archive_restore(name, expected):
    path = folder / (name + ".dump")
    if path.exists():
        raise RuntimeError("0077 isolated archive target exists")
    with db() as source:
        assert_empty(source, expected)
    native([BIN / "pg_dump.exe", "--format=custom", "--file", path,
        database["NAME"]])
    native([BIN / "createdb.exe", name])
    native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
        "--dbname", name, path])
    return path


def assert_formal_backup_closed():
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"]),
        "PGDATABASE": str(database["NAME"])}
    helper = ROOT / "tools/postgres-consistent-backup.py"
    target = folder / "v6_topology_unadmitted_formal.dump"
    if target.exists():
        raise RuntimeError("0077 formal backup target preexists")
    result = subprocess.run([sys.executable, helper, "backup", "--pg-dump",
        BIN / "pg_dump.exe", "--output", target, "--expected-database",
        database["NAME"], "--expected-user", database["USER"],
        "--port", str(database["PORT"]), "--timeout-seconds", "60"],
        env=env, capture_output=True, timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        refused = json.loads(result.stderr)
    except (ValueError, UnicodeError):
        raise AssertionError("0077 formal refusal unstructured") from None
    expected = hashlib.sha256(b"protected AI daily backup is not admitted"
        ).hexdigest()
    if (result.returncode != 1 or target.exists() or
            refused.get("status") != "failed" or
            refused.get("errorSha256") != expected):
        raise AssertionError("0077 formal backup did not reject before archive")


with db() as source:
    receipts = source.execute("SELECT name FROM django_migrations WHERE "
        "app='ai_assistant' AND name IN (%s,%s) ORDER BY name",
        [OLD[1], NEW[1]]).fetchall()
    if receipts != [(OLD[1],)]:
        raise RuntimeError("0077 requires exact 0076 predecessor")
    with source.cursor() as cursor:
        old76.verify_catalog(cursor)
    assert_empty(source, OLD_TABLES)
    before_functions = function_catalog(source)
    before_tables = table_catalog(source, OLD_TABLES)
    before_files = legacy_file_rows(source)
before_dump = archive_restore("market_v6_before", OLD_TABLES)
with db("market_v6_before") as restored:
    if (not restored_equal(function_catalog(restored), before_functions) or
            table_catalog(restored, OLD_TABLES) != before_tables or
            legacy_file_rows(restored) != before_files):
        raise AssertionError("0077 before independent restore drift")

plan = MigrationExecutor(connection).migration_plan([NEW])
if [(migration.app_label,migration.name,backward) for migration,backward
        in plan] != [(NEW[0], NEW[1], False)]:
    raise RuntimeError("0077 Django forward plan is not exact")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if (function_catalog(source) != before_functions or
            table_catalog(source, OLD_TABLES) != before_tables or
            legacy_file_rows(source) != before_files):
        raise AssertionError("0077 changed predecessor OID/body/ACL/owner")
assert_formal_backup_closed()
after_dump = archive_restore("market_v6_after", NEW_TABLES)
with db("market_v6_after") as restored:
    with restored.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if (not restored_equal(function_catalog(restored), before_functions) or
            table_catalog(restored, OLD_TABLES) != before_tables or
            legacy_file_rows(restored) != before_files):
        raise AssertionError("0077 after independent restore drift")

MigrationExecutor(connection).migrate([OLD])
with db() as source:
    if (function_catalog(source) != before_functions or
            table_catalog(source, OLD_TABLES) != before_tables or
            legacy_file_rows(source) != before_files or
            protected_names(source) != OLD_TABLES):
        raise AssertionError("0077 empty reverse changed predecessor")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if function_catalog(source) != before_functions:
        raise AssertionError("0077 reapply changed predecessor functions")

result = {"upgrade": "0076->0077", "status": "passed",
    "oldFunctionCount": len(before_functions),
    "oldFunctionOidBodyAclOwnerPreserved": True,
    "oldProtectedTableOwnerAclPreserved": True,
    "oldProtectedRows": 0, "newTableCount": len(NEW_TABLES),
    "oldFileRowCounts": dict(before_files),
    "newFunctionCount": len(candidate.v6.SIGNATURES),
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "formalBackupRejectedBeforeArchive": True,
    "emptyReverseAndReapply": True,
    "providerCallsAllowed": False, "productionWrites": False,
    "beforeDumpSha256": hashlib.sha256(before_dump.read_bytes()).hexdigest(),
    "afterDumpSha256": hashlib.sha256(after_dump.read_bytes()).hexdigest()}
(folder / "market-v6-topology-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
