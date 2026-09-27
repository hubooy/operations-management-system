"""Isolated 0074→0075 function-only upgrade and two independent restores.

Only a disposable PostgreSQL cluster may run this. It freezes old 0040/0071
function OID/body/ACL/owner in the source cluster and checks definitions,
owners and ACLs again in both independently restored databases.
"""
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

from ai_assistant import (business_v4_report_link_sql as link,
    business_v4_report_restricted_page_sql as page)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
parser.add_argument("--focused-current", action="store_true",
    help="Isolated current-schema predecessor with finance.0005 retained")
options = parser.parse_args()
folder = options.run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-market-v2-human-cap-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or connection.vendor != "postgresql"
        or (not options.focused_current and (
            seed.get("upgrade") != "0073->0074"
            or seed.get("beforeBackupRestored") is not True
            or seed.get("afterBackupRestored") is not True
            or seed.get("emptyReverseAndReapply") is not True))):
    raise RuntimeError("0075 requires the independently restored isolated 0074 seed")

OLD = ("ai_assistant", "0074_business_market_v2_human_cap_approval")
NEW = ("ai_assistant", "0075_business_v4_report_restricted_page")
prior = importlib.import_module(
    "ai_assistant.migrations.0040_business_v4_sealer_narrow_stream")
candidate = importlib.import_module(
    "ai_assistant.migrations.0075_business_v4_report_restricted_page")
signatures = (*prior.SIGNATURES,
    "public.ai_v4_report_link_row_guard()", link.BINDINGS, link.ISSUE,
    link.CREATE_REPORT, "public.ai_v4_report_link_after_insert()", link.READ)
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PROTECTED_TABLES = frozenset({
    "protected_business_budget_v11_verifier_keys",
    "protected_business_budget_v11_proof_tickets",
    "protected_business_budget_v11_proof_ticket_claims",
    "protected_business_v4_report_link_intents",
    "protected_business_v4_report_source_links",
    "protected_business_market_v2_rate_proposals",
    "protected_business_market_v2_cap_proposals",
    "protected_business_market_v2_authority_revocations",
    "protected_business_budget_v11_login_attestations",
    "protected_business_market_v2_human_cap_approvals",
    "protected_business_market_v2_human_cap_revocations",
})

with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN (%s,%s) ORDER BY name", [OLD[1], NEW[1]])
    if [row[0] for row in cursor.fetchall()] != [OLD[1]]:
        raise RuntimeError("0075 requires exact 0074 predecessor")
    for signature in (page.RO_BINDINGS, page.RO_READ, page.PAGE):
        cursor.execute("SELECT to_regprocedure(%s)", [signature])
        if cursor.fetchone() != (None,):
            raise RuntimeError("0075 function exists before upgrade")
    if options.focused_current:
        cursor.execute("SELECT app,name FROM django_migrations "
            "WHERE (app='finance' AND name='0005_raw_column_evidence_v2') "
            "OR (app='ai_assistant' AND name=%s) ORDER BY app,name",
            [OLD[1]])
        if cursor.fetchall() != [
                ("ai_assistant", OLD[1]),
                ("finance", "0005_raw_column_evidence_v2")]:
            raise RuntimeError("0075 focused predecessor lacks current finance")


def db(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def legacy_functions(source):
    rows = []
    for signature in signatures:
        row = source.execute("SELECT oid,prosrc,proacl::text,"
            "pg_catalog.pg_get_userbyid(proowner),prosecdef,proconfig "
            "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)",
            [signature]).fetchone()
        if row is None:
            raise AssertionError("0075 predecessor function missing")
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


def restored_equal(restored, original):
    # OIDs are database-local. All other listed fields must survive unchanged.
    # pg_restore may make an implicit owner ACL explicit; compare its expanded
    # grantor/grantee/privilege graph instead of the raw representation.
    return tuple((name, row[1], row[3:], acl) for name, row, acl in
        restored) == tuple((name, row[1], row[3:], acl) for name, row, acl in
        original)


def native(command, timeout=600):
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    result = subprocess.run([str(item) for item in command], cwd=ROOT,
        env=env, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("0075 isolated archive command failed; diagnosticSha256="
            + hashlib.sha256(result.stderr[:16384]).hexdigest())


def assert_empty_protected(source):
    """Never write private verifier bytes or receipts to a clear test dump."""
    names = {row[0] for row in source.execute("SELECT c.relname FROM "
        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relname LIKE 'protected_business_%' "
        "AND c.relkind IN ('r','p')").fetchall()}
    if names != PROTECTED_TABLES:
        raise RuntimeError("0075 protected table inventory drift")
    for name in sorted(names):
        query = sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)").format(
            sql.Identifier("public", name))
        if source.execute(query).fetchone() != (False,):
            raise RuntimeError("0075 protected table is not empty")


def archive_restore(name):
    archive = folder / (name + ".dump")
    if archive.exists():
        raise RuntimeError("0075 rehearsal archive already exists")
    with db() as source:
        assert_empty_protected(source)
    native([BIN / "pg_dump.exe", "--format=custom", "--file", archive,
        database["NAME"]])
    native([BIN / "createdb.exe", name])
    native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
        "--dbname", name, archive])


def assert_formal_backup_closed():
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"]),
        "PGDATABASE": str(database["NAME"])}
    helper = ROOT / "tools/postgres-consistent-backup.py"
    preflight = subprocess.run([sys.executable, helper,
        "protected-preflight", "--expected-database", database["NAME"],
        "--expected-user", database["USER"], "--port", str(database["PORT"])],
        env=env, capture_output=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if (preflight.returncode or NEW[1] not in json.loads(
            preflight.stdout)["appliedProtectedMigrations"]):
        raise AssertionError("formal protected preflight missed 0075")
    target = folder / "restricted_page_unadmitted_formal.dump"
    if target.exists():
        raise AssertionError("formal backup target already exists")
    rejected = subprocess.run([sys.executable, helper, "backup",
        "--pg-dump", BIN / "pg_dump.exe", "--output", target,
        "--expected-database", database["NAME"], "--expected-user",
        database["USER"], "--port", str(database["PORT"]),
        "--timeout-seconds", "60"], env=env, capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        refusal = json.loads(rejected.stderr)
    except (ValueError, UnicodeError) as error:
        raise AssertionError("formal backup refusal was not structured") from error
    expected_digest = hashlib.sha256(
        b"protected AI daily backup is not admitted").hexdigest()
    if (rejected.returncode != 1 or target.exists()
            or refusal.get("status") != "failed"
            or refusal.get("errorType") != "RuntimeError"
            or refusal.get("errorSha256") != expected_digest):
        raise AssertionError("formal backup did not reject 0075 before archive")


with db() as source:
    assert_empty_protected(source)
    before = legacy_functions(source)
    # A synthetic secret inserted inside this transaction must refuse before
    # the archive target is created; rollback leaves the protected table empty.
    class ExpectedRollback(Exception):
        pass
    guard_target = folder / "restricted_page_guard_negative.dump"
    if guard_target.exists():
        raise RuntimeError("0075 guard negative target already exists")
    try:
        with source.transaction():
            source.execute("INSERT INTO public."
                "protected_business_budget_v11_verifier_keys "
                "(key_id,secret,status,created_at) VALUES "
                "('0075_guard_probe',decode(repeat('ab',32),'hex'),"
                "'active',clock_timestamp())")
            try:
                assert_empty_protected(source)
            except RuntimeError as error:
                if str(error) != "0075 protected table is not empty":
                    raise
            else:
                raise AssertionError("0075 accepted nonempty private key")
            if guard_target.exists():
                raise AssertionError("0075 negative created archive target")
            raise ExpectedRollback()
    except ExpectedRollback:
        pass
    assert_empty_protected(source)
archive_restore("restricted_page_before")
with db("restricted_page_before") as restored:
    if not restored_equal(legacy_functions(restored), before):
        raise AssertionError("0075 predecessor independent restore drift")

MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if legacy_functions(source) != before:
        raise AssertionError("0075 changed 0040/0071 OID/body/ACL/owner")
assert_formal_backup_closed()
archive_restore("restricted_page_after")
with db("restricted_page_after") as restored:
    with restored.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if not restored_equal(legacy_functions(restored), before):
        raise AssertionError("0075 after-restore 0040/0071 drift")

MigrationExecutor(connection).migrate([OLD])
with db() as source:
    if legacy_functions(source) != before:
        raise AssertionError("0075 reverse changed predecessor functions")
    for signature in (page.RO_BINDINGS, page.RO_READ, page.PAGE):
        if source.execute("SELECT to_regprocedure(%s)",
                [signature]).fetchone() != (None,):
            raise AssertionError("0075 function survived reverse")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        candidate.verify_catalog(cursor)
    if legacy_functions(source) != before:
        raise AssertionError("0075 reapply changed predecessor functions")

result = {"upgrade": "0074->0075", "status": "passed",
    "focusedCurrentFinance0005": options.focused_current,
    "old0040PageOidBodyAclOwnerFrozen": True,
    "old0071FunctionOidBodyAclOwnerFrozen": True,
    "oldFunctionCount": len(signatures), "newFunctions": 3,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "nonemptyPrivateKeyRejectedBeforeArchive": True,
    "formalBackupRejectedBeforeArchive": True,
    "emptyReverseAndReapply": True,
    "reportGenerationSupported": False, "productionWrites": False}
(folder / "business-v4-report-restricted-page-upgrade-evidence.json"
    ).write_text(json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
