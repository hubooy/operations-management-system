"""Isolated 0068 -> 0069 paid-round sidecar upgrade and independent restore."""
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
    AI_TABLES_PRE_MARKET_V2_PAID_REHEARSAL as OLD_TABLES,
    AI_TABLES as NEW_TABLES)
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-budget-v11-verifier-upgrade-evidence.json"
proof = json.loads(seed.read_text(encoding="utf-8")) if seed.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or proof.get("upgrade") != "0067->0068"
        or proof.get("beforeBackupRestored") is not True
        or proof.get("afterBackupRestored") is not True
        or proof.get("emptyReverseAndReapply") is not True
        or len(OLD_TABLES) != 86 or len(NEW_TABLES) != 89):
    raise RuntimeError("0069 requires independently restored isolated 0068 seed")

OLD = [("ai_assistant", "0068_business_promotion_budget_v11_verifier_receipt"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0069_business_market_v2_paid_round_rehearsal"),
       *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0068_business_promotion_budget_v11_verifier_receipt',"
        "'0069_business_market_v2_paid_round_rehearsal') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0069 requires exact 0068 predecessor")

candidate = importlib.import_module(
    "ai_assistant.migrations.0069_business_market_v2_paid_round_rehearsal")
verifier = importlib.import_module(
    "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
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
    rows = db.execute("SELECT id,renderer_version,status,attempt,manifest_json "
        "FROM public.ai_business_file_runs WHERE renderer_version<=11 "
        "ORDER BY id").fetchall()
    result = {}
    for run_id, version, status, attempt, manifest in rows:
        chunks = []
        for table, order in (("ai_business_file_chunks", "format,sequence"),
                             ("ai_business_volume_chunks",
                              "volume_index,format,sequence")):
            parts = db.execute("SELECT content,content_digest FROM public."+
                table+" WHERE run_id=%s ORDER BY "+order, [run_id]).fetchall()
            if any(hashlib.sha256(bytes(blob)).hexdigest() != saved
                    for blob, saved in parts):
                raise AssertionError("old file chunk digest mismatch")
            chunks.append((table, len(parts), hashlib.sha256(b"".join(
                bytes(blob) for blob, _ in parts)).hexdigest()))
        result[run_id] = (version, status, attempt, manifest, tuple(chunks))
    if not {1, 2, 3, 4, 5, 6, 7} <= {row[0] for row in result.values()}:
        raise AssertionError("old renderer 1-7 seed missing")
    return result


def functions(db):
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


def table_acl(db):
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
    return old_rows(db), old_files(db), functions(db), table_acl(db), roles(db), memberships(db)


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
            raise RuntimeError("isolated 0069 archive/restore failed")


def verify_old(db):
    for table in (candidate.AUTHORITY, candidate.ROUNDS, candidate.EVENTS):
        if db.execute("SELECT to_regclass(%s)", [table]).fetchone()[0]:
            raise AssertionError("0069 sidecar exists before upgrade/after reverse")
    for signature in (candidate.ADOPT_SIG, candidate.RESERVE_SIG,
                      candidate.START_SIG, candidate.EXPECTED_SIG):
        if db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]:
            raise AssertionError("0069 SQL exists before upgrade/after reverse")
    with db.cursor() as cursor:
        verifier.verify_catalog(cursor)


def verify_new(db, before, after, *, restored_db=False):
    if after[:2] != before[:2] or after[3] != before[3] or after[5] != before[5]:
        raise AssertionError("0069 changed old 86 tables, renderer bytes or grants")
    for name, details in before[2].items():
        current = after[2].get(name)
        if current is None or (current[1:] if restored_db else current) != (
                details[1:] if restored_db else details):
            raise AssertionError("0069 changed old AI function identity/body/ACL")
    wanted = {db.execute("SELECT to_regprocedure(%s)::text", [name]
        ).fetchone()[0] for name in (candidate.EXPECTED_SIG,
            candidate.ADOPT_SIG, candidate.RESERVE_SIG, candidate.START_SIG,
            "public.ai_market_v2_paid_row_guard()")}
    if None in wanted or set(after[2]) - set(before[2]) != wanted:
        raise AssertionError("0069 added unexpected AI function")
    added = {(role, *(False,)*7) for role in (
        candidate.ADOPTER, candidate.RESERVER, candidate.STARTER)}
    if set(after[4]) != set(before[4]) | added:
        raise AssertionError("0069 role drift")
    with db.cursor() as cursor:
        candidate.verify_catalog(cursor)
        verifier.verify_catalog(cursor)
    for table in (candidate.AUTHORITY, candidate.ROUNDS, candidate.EVENTS):
        if db.execute("SELECT count(*) FROM "+table).fetchone() != (0,):
            raise AssertionError("0069 provisioned synthetic approval or reservation")


with connect() as db:
    verify_old(db)
    before = snapshot(db)
archive_restore("market_paid_round_before")
with connect("market_paid_round_before") as copy:
    verify_old(copy)
    if normalized(snapshot(copy)) != normalized(before):
        raise AssertionError("0068 independent pre-upgrade restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    after = snapshot(db)
    verify_new(db, before, after)
archive_restore("market_paid_round_after")
with connect("market_paid_round_after") as copy:
    recovered = snapshot(copy)
    verify_new(copy, before, recovered, restored_db=True)
    if normalized(recovered) != normalized(after):
        raise AssertionError("0069 post-upgrade restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    verify_old(db)
    reversed_state = snapshot(db)
    if reversed_state[:4] != before[:4] or reversed_state[5] != before[5]:
        raise AssertionError("0069 empty reverse changed old schema/data")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    replayed = snapshot(db)
    verify_new(db, before, replayed)
    if normalized(replayed) != normalized(after):
        raise AssertionError("0069 empty reapply differs")

result = {"upgrade": "0068->0069", "oldAiTables": 86,
    "newAiTables": 89, "oldRowsDigestPreserved": before[0],
    "seededRendererVersions": sorted({row[0] for row in before[1].values()}),
    "oldRendererRowsAndChunksPreserved": True,
    "oldAiFunctionOidBodyAclOwnerPreserved": True,
    "readerAndWriterTablePrivilegesUnchanged": True,
    "newRolesNoLoginNoMembers": True,
    "newAuthorityAndReservationTablesEmpty": True,
    "providerCallsAllowed": False,
    "realRoleTest": "ai_assistant.test_business_market_v2_paid_round_role",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-paid-round-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
