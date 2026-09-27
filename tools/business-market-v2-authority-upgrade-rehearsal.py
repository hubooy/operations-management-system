"""Isolated 0071 -> 0072 pending authority ledger, dual restore and reverse."""
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
    AI_TABLES, AI_FULL_TABLES_AFTER_V4_REPORT_LINKS as OLD_TABLES,
    AI_FULL_TABLES_AFTER_MARKET_AUTHORITY_0072 as NEW_TABLES)
from ai_assistant import business_market_v2_authority_sql as authority
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-v4-report-link-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or seed.get("upgrade") != "0070->0071"
        or seed.get("beforeBackupRestored") is not True
        or seed.get("afterBackupRestored") is not True
        or seed.get("emptyReverseAndReapply") is not True
        or len(AI_TABLES) != 89
        or len(OLD_TABLES) != 93 or len(NEW_TABLES) != 96):
    raise RuntimeError("0072 requires independently restored isolated 0071 seed")

OLD = [("ai_assistant", "0071_business_v4_report_source_link"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0072_business_market_v2_authority_proposals"),
       *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0071_business_v4_report_source_link',"
        "'0072_business_market_v2_authority_proposals') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0072 requires exact 0071 predecessor")

candidate = importlib.import_module(
    "ai_assistant.migrations.0072_business_market_v2_authority_proposals")
previous = importlib.import_module(
    "ai_assistant.migrations.0071_business_v4_report_source_link")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def old_rows(db):
    content = {}
    for table in OLD_TABLES:
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        content[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()


def old_files(db):
    values = {}
    for run_id, version, status, attempt, manifest in db.execute(
            "SELECT id,renderer_version,status,attempt,manifest_json "
            "FROM public.ai_business_file_runs WHERE renderer_version<=11 "
            "ORDER BY id").fetchall():
        pieces = []
        for table, order in (("ai_business_file_chunks", "format,sequence"),
                             ("ai_business_volume_chunks",
                              "volume_index,format,sequence")):
            parts = db.execute("SELECT content,content_digest FROM public."+
                table+" WHERE run_id=%s ORDER BY "+order, [run_id]).fetchall()
            if any(hashlib.sha256(bytes(blob)).hexdigest() != saved
                    for blob, saved in parts):
                raise AssertionError("old file chunk digest mismatch")
            pieces.append((table, len(parts), hashlib.sha256(b"".join(
                bytes(blob) for blob, _ in parts)).hexdigest()))
        values[run_id] = (version, status, attempt, manifest, tuple(pieces))
    if not {1, 2, 3, 4, 5, 6, 7} <= {item[0] for item in values.values()}:
        raise AssertionError("old renderer 1-7 seed missing")
    return values


def old_functions(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1"
        ).fetchall()
    return {name: tuple(details) for name, *details in rows}


def old_privileges(db):
    result = {}
    for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
        for table in OLD_TABLES:
            result[role, table] = tuple(db.execute(
                "SELECT has_table_privilege(%s,%s,%s)",
                [role, "public."+table, privilege]).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                    "TRUNCATE", "REFERENCES")) + tuple(db.execute(
                "SELECT has_any_column_privilege(%s,%s,%s)",
                [role, "public."+table, privilege]).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"))
    return result


def roles(db):
    return db.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,"
        "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname LIKE 'teruisi_ai_%' "
        "ORDER BY rolname").fetchall()


def memberships(db):
    return db.execute("SELECT parent.rolname,member.rolname,"
        "membership.admin_option FROM pg_catalog.pg_auth_members membership "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
        "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
        "WHERE parent.rolname LIKE 'teruisi_ai_%' OR "
        "member.rolname LIKE 'teruisi_ai_%' ORDER BY 1,2").fetchall()


def snapshot(db):
    return (old_rows(db), old_files(db), old_functions(db),
        old_privileges(db), roles(db), memberships(db))


def normalized(value):
    return value[:2] + ({key: item[1:] for key, item in value[2].items()},) + value[3:]


def archive_restore(name):
    archive = folder / (name + ".dump")
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, args in (("pg_dump.exe",
            ["-Fc", "-f", str(archive), database["NAME"]]),
            ("createdb.exe", [name]),
            ("pg_restore.exe", ["--exit-on-error", "-d", name, str(archive)])):
        done = subprocess.run([str(BIN / executable), *args], env=env,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if done.returncode:
            (folder / (name + "-error.log")).write_bytes(done.stderr)
            raise RuntimeError("isolated 0072 archive/restore failed")


def verify_old(db):
    for table in (authority.RATE, authority.CAP, authority.REVOKE):
        if db.execute("SELECT to_regclass(%s)", [table]).fetchone()[0]:
            raise AssertionError("0072 table exists before upgrade/after reverse")
    for signature in (authority.RATE_SIG, authority.CAP_SIG,
            authority.REVOKE_SIG, authority.READ_SIG,
            "public.ai_market_v2_authority_proposal_guard()"):
        if db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]:
            raise AssertionError("0072 function exists before upgrade/after reverse")
    with db.cursor() as cursor:
        previous.verify_catalog(cursor)


def verify_new(db, before, after, *, restored_db=False):
    if after[:2] != before[:2] or after[3] != before[3] or after[5] != before[5]:
        raise AssertionError("0072 changed old 93 rows, renderer bytes or ACL")
    for name, details in before[2].items():
        current = after[2].get(name)
        if current is None or (current[1:] if restored_db else current) != (
                details[1:] if restored_db else details):
            raise AssertionError("0072 changed old AI function identity/body/ACL")
    added = {db.execute("SELECT to_regprocedure(%s)::text", [name]
        ).fetchone()[0] for name in (authority.RATE_SIG, authority.CAP_SIG,
            authority.REVOKE_SIG, authority.READ_SIG,
            "public.ai_market_v2_authority_proposal_guard()")}
    if None in added or set(after[2]) - set(before[2]) != added:
        raise AssertionError("0072 added unexpected AI function")
    new_roles = {(role, *(False,)*7) for role in (authority.RATE_ROLE,
        authority.CAP_ROLE, authority.REVOKE_ROLE)}
    if set(after[4]) != set(before[4]) | new_roles:
        raise AssertionError("0072 role inventory drift")
    with db.cursor() as cursor:
        candidate.verify_catalog(cursor)
        previous.verify_catalog(cursor)
    for table in (authority.RATE, authority.CAP, authority.REVOKE):
        if db.execute("SELECT count(*) FROM " + table).fetchone() != (0,):
            raise AssertionError("0072 migration filled a rate or approval")


with connect() as db:
    verify_old(db)
    before = snapshot(db)
archive_restore("market_authority_before")
with connect("market_authority_before") as copy:
    verify_old(copy)
    if normalized(snapshot(copy)) != normalized(before):
        raise AssertionError("0071 independent pre-upgrade restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    after = snapshot(db)
    verify_new(db, before, after)
archive_restore("market_authority_after")
with connect("market_authority_after") as copy:
    recovered = snapshot(copy)
    verify_new(copy, before, recovered, restored_db=True)
    if normalized(recovered) != normalized(after):
        raise AssertionError("0072 post-upgrade restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    verify_old(db)
    previous_state = snapshot(db)
    if previous_state[:4] != before[:4] or previous_state[5] != before[5]:
        raise AssertionError("0072 empty reverse changed old schema/data")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    replayed = snapshot(db)
    verify_new(db, before, replayed)
    if normalized(replayed) != normalized(after):
        raise AssertionError("0072 empty reapply differs")

result = {"upgrade": "0071->0072", "ormMappedAiTables": len(AI_TABLES),
    "oldPhysicalAiTables": len(OLD_TABLES),
    "newPhysicalAiTables": len(NEW_TABLES),
    "oldRowsDigestPreserved": before[0],
    "oldRendererRowsAndChunksPreserved": True,
    "oldAiFunctionOidBodyAclOwnerPreserved": True,
    "readerAndWriterTablePrivilegesUnchanged": True,
    "newProtectedRateCapRevocationTablesEmpty": True,
    "rateAuthorityVerified": False, "humanApprovalVerified": False,
    "providerCallsAllowed": False,
    "realRoleTest": "ai_assistant.test_business_market_v2_authority_proposal_role",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-authority-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
