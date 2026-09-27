"""Isolated old-fact finance/netshop revision-guard upgrade and restore proof."""
import argparse
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ai_assistant.table_manifest import AI_TABLES_PRE_V4_SEALS as AI_TABLES
from business_analysis.contracts import canonical
from finance.import_service import import_finance_payload
from finance.models import FinanceDataRevision, FinanceLine, FinanceWriteAuthority
from finance.tests.factories import prepared_payload as finance_payload
from netshop.import_service import import_netshop_payload
from netshop.models import NetshopDataRevision, NetshopRow, NetshopWriteAuthority
from netshop.tests.factories import netshop_row, prepared_payload as netshop_payload

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != ROOT / ".runtime"):
    raise RuntimeError("source guard upgrade is isolated PostgreSQL only")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
AI_FIXED = ("ai_assistant", "0036_business_v4_validation_segments")
OLD = [("finance", "0002_finance_target_gross_margin"),
       ("netshop", "0002_migration_run_time_order"), AI_FIXED]
NEW = [("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard"), AI_FIXED]
SOURCE_TABLES = ("finance_import_batches", "finance_months", "finance_lines",
    "finance_data_revisions", "finance_write_authority",
    "netshop_import_batches", "netshop_rows", "netshop_data_revisions",
    "netshop_write_authority")
MARKERS = ("finance_source_revision_markers", "netshop_source_revision_markers")
ROLES = ("teruisi_finance_writer", "teruisi_netshop_writer")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def digest_tables(db, tables):
    material = {}
    for table in sorted(tables):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        material[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


def grants(db, tables):
    return db.execute("SELECT grantee,table_name,privilege_type FROM "
        "information_schema.role_table_grants WHERE grantee=ANY(%s) "
        "AND table_name=ANY(%s) ORDER BY 1,2,3", [list(ROLES), list(tables)]).fetchall()


def backup_restore(name):
    dump = folder / (name + ".dump")
    environment = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe", ["-Fc", "-f", str(dump), database["NAME"]]),
                             ("createdb.exe", [name]),
                             ("pg_restore.exe", ["--exit-on-error", "-d", name, str(dump)])):
        process = subprocess.run([str(BIN / executable), *args], env=environment,
            capture_output=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if process.returncode:
            (folder / (name + "-error.log")).write_bytes(process.stderr)
            raise RuntimeError("isolated source guard archive/restore failed")


def markers_installed(db):
    expected = {
        "finance_line_revision_required": "finance_lines",
        "finance_month_revision_required": "finance_months",
        "finance_batch_revision_required": "finance_import_batches",
        "finance_revision_monotonic": "finance_data_revisions",
        "netshop_row_revision_required": "netshop_rows",
        "netshop_batch_revision_required": "netshop_import_batches",
        "netshop_source_revision_monotonic": "netshop_data_revisions",
    }
    actual = dict(db.execute("SELECT tgname,c.relname FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid WHERE NOT t.tgisinternal "
        "AND t.tgname=ANY(%s) AND t.tgenabled='O'", [list(expected)]).fetchall())
    if actual != expected:
        raise AssertionError("source-write revision triggers are missing or disabled")
    for trigger, marker in (("finance_source_revision_required", MARKERS[0]),
                            ("netshop_source_revision_required", MARKERS[1])):
        actual = db.execute("SELECT t.tgdeferrable,t.tginitdeferred,t.tgenabled "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "WHERE t.tgname=%s AND c.relname=%s", [trigger, marker]).fetchone()
        if actual != (True, True, "O"):
            raise AssertionError("source marker must be deferred and enabled")
    for marker in MARKERS:
        if db.execute(sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(marker))).fetchone()[0] != 0:
            raise AssertionError("deferred source marker was not cleared")
        for role in ROLES:
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                if db.execute("SELECT has_table_privilege(%s,%s,%s)",
                        [role, "public." + marker, privilege]).fetchone()[0]:
                    raise AssertionError("source writer can alter protected marker")
    for domain, role in (("finance", ROLES[0]), ("netshop", ROLES[1])):
        function = ("public.finance_source_mark_revision_required()" if domain == "finance"
            else "public.netshop_source_mark_revision_required()")
        row = db.execute("SELECT prosecdef,proconfig FROM pg_proc WHERE oid=%s::regprocedure",
            [function]).fetchone()
        if (row is None or row[0] is not True or
                "search_path=pg_catalog,public" not in
                [item.replace(" ", "") for item in row[1]]):
            raise AssertionError("source marker definer/search_path differs")
        if db.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [role, function]).fetchone()[0]:
            raise AssertionError("source writer can invoke protected marker function")


MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    # Actual production roles are not provisioned by a fresh rehearsal cluster;
    # create narrow synthetic names solely to prove old ACL preservation and
    # new marker denial. No credential or login is granted.
    for role in ROLES:
        if db.execute("SELECT to_regrole(%s)", [role]).fetchone()[0] is None:
            db.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    db.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON finance_lines TO teruisi_finance_writer")
    db.execute("GRANT SELECT,INSERT,UPDATE ON finance_import_batches,finance_months,finance_data_revisions TO teruisi_finance_writer")
    db.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON netshop_rows TO teruisi_netshop_writer")
    db.execute("GRANT SELECT,INSERT,UPDATE ON netshop_import_batches,netshop_data_revisions TO teruisi_netshop_writer")

# Empty rollback and reinstall must be possible while both authorities are d1.
MigrationExecutor(connection).migrate(NEW)
with connect() as db: markers_installed(db)
MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    if any(db.execute("SELECT to_regclass(%s)", ["public." + marker]).fetchone()[0]
            is not None for marker in MARKERS):
        raise AssertionError("empty reverse left a marker table")

FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
NetshopWriteAuthority.objects.filter(id=1).update(status="postgres",
    authority_epoch=uuid4(), cutover_id="isolated-source-guard-upgrade",
    migration_verify_run_id="source-guard-upgrade",
    activated_at=timezone.now())
# An activated writer alone must prevent removal, even before the first fact.
for name in ("finance.migrations.0004_finance_revision_monotonic",
             "finance.migrations.0003_finance_source_revision_guard",
             "netshop.migrations.0003_netshop_source_revision_guard"):
    with connection.schema_editor() as editor:
        try:
            import_module(name).uninstall(None, editor)
        except RuntimeError as error:
            if "不能逆迁移" not in str(error): raise
        else:
            raise AssertionError("active source writer permitted reverse guard removal")
finance_result = import_finance_payload(finance_payload("2026-08"),
    "source-guard-upgrade@example.invalid")
netshop_result = import_netshop_payload(netshop_payload(netshop_row(
    source="jd_promotion", dataset="ad", shop_name="隔离迁移店",
    business_date="2026-08-20", metrics={"spendCents": 100,
        "impressions": 20, "clicks": 2}), raw_seed="source-guard-upgrade"),
    "source-guard-upgrade@example.invalid")
if finance_result["status"] != "imported" or netshop_result["status"] != "imported":
    raise AssertionError("old-authority source fact seeding failed")
if FinanceLine.objects.count() == 0 or NetshopRow.objects.count() == 0:
    raise AssertionError("old source facts absent")

with connect() as db:
    old_digest = digest_tables(db, (*SOURCE_TABLES, *AI_TABLES))
    old_acl = grants(db, SOURCE_TABLES)
    old_revision = (FinanceDataRevision.objects.get(domain="finance").revision,
        NetshopDataRevision.objects.get(domain="netshop").revision)
backup_restore("source_guard_before")
with connect("source_guard_before") as restored:
    assert digest_tables(restored, (*SOURCE_TABLES, *AI_TABLES)) == old_digest
    assert grants(restored, SOURCE_TABLES) == old_acl

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    if digest_tables(db, (*SOURCE_TABLES, *AI_TABLES)) != old_digest:
        raise AssertionError("old source/AI rows changed during guard upgrade")
    if grants(db, SOURCE_TABLES) != old_acl:
        raise AssertionError("old source permissions changed during guard upgrade")
    markers_installed(db)
    for table, field, expected_error in (("finance_lines", "amount_cents",
            "finance_source_write_without_revision"),
            ("netshop_rows", "spend_cents", "netshop_source_write_without_revision")):
        try:
            db.execute(sql.SQL("UPDATE {} SET {}={}+1 WHERE id=(SELECT min(id) FROM {})").format(
                sql.Identifier(table), sql.Identifier(field), sql.Identifier(field),
                sql.Identifier(table)))
        except psycopg.Error as error:
            if expected_error not in str(error): raise
        else:
            raise AssertionError("unversioned source write escaped the new guard")
    for table, domain, expected_error in (("finance_data_revisions", "finance",
            "finance_revision_not_monotonic"),
            ("netshop_data_revisions", "netshop",
             "netshop_source_revision_rollback_or_aba")):
        try:
            db.execute(sql.SQL("UPDATE {} SET revision=revision-1 WHERE domain=%s").format(
                sql.Identifier(table)), [domain])
        except psycopg.Error as error:
            if expected_error not in str(error): raise
        else:
            raise AssertionError("source revision rollback escaped monotonic guard")
    if digest_tables(db, (*SOURCE_TABLES, *AI_TABLES)) != old_digest:
        raise AssertionError("rejected source write changed old rows")
    markers_installed(db)
    after_digest = digest_tables(db, (*SOURCE_TABLES, *AI_TABLES, *MARKERS))
backup_restore("source_guard_after")
with connect("source_guard_after") as restored:
    assert digest_tables(restored, (*SOURCE_TABLES, *AI_TABLES, *MARKERS)) == after_digest
    assert grants(restored, SOURCE_TABLES) == old_acl
    markers_installed(restored)

if MigrationExecutor(connection).migration_plan(NEW):
    raise AssertionError("repeated upgrade is not idempotent")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    assert digest_tables(db, (*SOURCE_TABLES, *AI_TABLES, *MARKERS)) == after_digest

for name in ("finance.migrations.0004_finance_revision_monotonic",
             "finance.migrations.0003_finance_source_revision_guard",
             "netshop.migrations.0003_netshop_source_revision_guard"):
    with connection.schema_editor() as editor:
        try:
            import_module(name).uninstall(None, editor)
        except RuntimeError as error:
            if "不能逆迁移" not in str(error): raise
        else:
            raise AssertionError("protected old facts permitted reverse guard removal")

result = {"upgrade": "finance.0002->0003->0004 + netshop.0002->0003",
    "oldRowsDigestPreserved": old_digest,
    "afterRowsDigest": after_digest,
    "oldAclPreserved": True, "markerWriterDenied": True,
    "oldBackupRestored": True, "newBackupRestored": True,
    "emptyReversePassed": True, "activeWriterReverseDenied": True,
    "factsReverseDenied": True,
    "repeatUpgradeIdempotent": True, "oldFinanceRevision": old_revision[0],
    "oldNetshopRevision": old_revision[1], "productionWrites": False}
(folder / "source-revision-guards-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
