"""Isolated 0073→0074 approval upgrade, old-byte freeze and dual restore."""
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
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from protected_ai_acl_compare import restored_equal


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-v11-login-attestation-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or seed.get("upgrade") != "0072->0073"
        or seed.get("beforeBackupRestored") is not True
        or seed.get("afterBackupRestored") is not True
        or seed.get("emptyReverseAndReapply") is not True):
    raise RuntimeError("0074 requires exact isolated restored 0073 seed")

old = import_module(
    "ai_assistant.migrations.0073_business_promotion_budget_v11_login_attestation")
new = import_module(
    "ai_assistant.migrations.0074_business_market_v2_human_cap_approval")
cap = new.cap
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
OLD = ("ai_assistant", "0073_business_promotion_budget_v11_login_attestation")
NEW = ("ai_assistant", "0074_business_market_v2_human_cap_approval")
DOWNLOAD = ROOT / "backend/ai_assistant/business_volume_files.py"
download_sha = hashlib.sha256(DOWNLOAD.read_bytes()).hexdigest()

with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN (%s,%s) ORDER BY name", [OLD[1], NEW[1]])
    if [row[0] for row in cursor.fetchall()] != [OLD[1]]:
        raise RuntimeError("0074 requires exact 0073 predecessor")


def db(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def native(command, timeout=600):
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    result = subprocess.run([str(x) for x in command], cwd=ROOT, env=env,
        capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("0074 isolated archive command failed; diagnosticSha256="
            + hashlib.sha256(result.stderr[:16384]).hexdigest())


def archive_restore(name):
    archive = folder / (name + ".dump")
    if archive.exists():
        raise RuntimeError("0074 rehearsal archive already exists")
    native([BIN / "pg_dump.exe", "--format=custom", "--file", archive,
        database["NAME"]])
    native([BIN / "createdb.exe", name])
    native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
        "--dbname", name, archive])


def old_catalog(source):
    with source.cursor() as cursor:
        old.verify_catalog(cursor)
    excluded_functions = [signature.split("(", 1)[0].removeprefix("public.")
        for signature in (cap.GUARD_SIG, cap.MODEL_SIG, cap.PREVIEW_SIG,
            cap.APPROVE_SIG, cap.REVOKE_SIG, cap.OUTCOME_SIG)]
    functions = source.execute("SELECT p.oid,p.proname,p.prosrc,p.proacl::text,"
        "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND p.proname LIKE %s "
        "AND p.proname<>ALL(%s) ORDER BY p.proname,p.oid",
        ["ai_%", excluded_functions]).fetchall()
    old_table_rows = source.execute("SELECT c.oid,c.relname,c.relkind,"
        "c.relacl::text,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p') "
        "AND c.relname LIKE %s "
        "AND c.relname<>ALL(%s) ORDER BY c.relname",
        ["protected_business_%", [cap.APPROVAL.removeprefix("public."),
          cap.REVOCATION.removeprefix("public.")]]).fetchall()
    roles = [row[0] for row in source.execute("SELECT rolname FROM "
        "pg_catalog.pg_roles WHERE rolname LIKE %s ORDER BY rolname",
        ["teruisi_%"]).fetchall()]
    old_tables = []
    for oid, name, kind, raw_acl, owner_name in old_table_rows:
        # Same-cluster comparison keeps raw ACL bytes. An independent restore
        # may materialize an implicit owner ACL as an explicit one; only that
        # representational difference is normalized below. Owner, relkind,
        # grantor/grantee/privilege/grantability, column ACL and effective
        # restricted-role permissions must still match exactly.
        table_acl = source.execute("SELECT CASE WHEN acl.grantee=0 THEN "
            "'PUBLIC' ELSE pg_catalog.pg_get_userbyid(acl.grantee) END, "
            "pg_catalog.pg_get_userbyid(acl.grantor),acl.privilege_type,"
            "acl.is_grantable FROM pg_catalog.pg_class c, "
            "LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
            "pg_catalog.acldefault('r',c.relowner))) acl "
            "WHERE c.oid=%s::oid ORDER BY 1,2,3,4", [oid]).fetchall()
        columns = source.execute("SELECT a.attnum,a.attname,a.attacl::text "
            "FROM pg_catalog.pg_attribute a WHERE a.attrelid=%s::oid "
            "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
            [oid]).fetchall()
        column_acl = source.execute("SELECT a.attnum,"
            "CASE WHEN acl.grantee=0 THEN 'PUBLIC' ELSE "
            "pg_catalog.pg_get_userbyid(acl.grantee) END,"
            "pg_catalog.pg_get_userbyid(acl.grantor),acl.privilege_type,"
            "acl.is_grantable FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid, "
            "LATERAL pg_catalog.aclexplode(COALESCE(a.attacl,"
            "pg_catalog.acldefault('c',c.relowner))) acl "
            "WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped "
            "ORDER BY 1,2,3,4,5", [oid]).fetchall()
        table_effective = source.execute("SELECT r.rolname,"
            "pg_catalog.has_table_privilege(r.oid,c.oid,'SELECT'),"
            "pg_catalog.has_table_privilege(r.oid,c.oid,'INSERT'),"
            "pg_catalog.has_table_privilege(r.oid,c.oid,'UPDATE'),"
            "pg_catalog.has_table_privilege(r.oid,c.oid,'DELETE'),"
            "pg_catalog.has_table_privilege(r.oid,c.oid,'TRUNCATE') "
            "FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_class c "
            "WHERE c.oid=%s::oid AND r.rolname=ANY(%s) "
            "ORDER BY r.rolname", [oid, roles]).fetchall()
        column_effective = source.execute("SELECT a.attnum,r.rolname,"
            "pg_catalog.has_column_privilege(r.oid,a.attrelid,a.attnum,'SELECT'),"
            "pg_catalog.has_column_privilege(r.oid,a.attrelid,a.attnum,'INSERT'),"
            "pg_catalog.has_column_privilege(r.oid,a.attrelid,a.attnum,'UPDATE') "
            "FROM pg_catalog.pg_attribute a CROSS JOIN pg_catalog.pg_roles r "
            "WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped "
            "AND r.rolname=ANY(%s) ORDER BY a.attnum,r.rolname",
            [oid, roles]).fetchall()
        old_tables.append((name, kind, owner_name, raw_acl,
            tuple(table_acl), tuple(columns), tuple(column_acl),
            tuple(table_effective), tuple(column_effective)))
    chunks = source.execute("SELECT run_id,attempt,volume_index,format,"
        "sequence,content,content_digest FROM public.ai_business_volume_chunks "
        "ORDER BY run_id,attempt,volume_index,format,sequence").fetchall()
    chunk_rows = []
    for run_id, attempt, index, kind, sequence, content, saved in chunks:
        actual = hashlib.sha256(bytes(content)).hexdigest()
        if actual != saved:
            raise AssertionError("0074 predecessor file byte drift")
        chunk_rows.append((run_id,attempt,index,kind,sequence,actual))
    bytes_sha = hashlib.sha256(json.dumps(chunk_rows, ensure_ascii=True,
        separators=(",", ":")).encode("ascii")).hexdigest()
    return functions, old_tables, bytes_sha, len(chunk_rows)


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
    if preflight.returncode or NEW[1] not in json.loads(
            preflight.stdout)["appliedProtectedMigrations"]:
        raise AssertionError("formal protected preflight missed 0074")
    target = folder / "human_cap_unadmitted_formal.dump"
    if target.exists():
        raise AssertionError("formal backup target already exists")
    result = subprocess.run([sys.executable, helper, "backup",
        "--pg-dump", BIN / "pg_dump.exe", "--output", target,
        "--expected-database", database["NAME"], "--expected-user",
        database["USER"], "--port", str(database["PORT"]),
        "--timeout-seconds", "60"], env=env, capture_output=True,
        timeout=90,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode != 1 or target.exists():
        raise AssertionError("formal backup did not reject 0074 before archive")


with db() as source:
    before = old_catalog(source)
    for table in (cap.APPROVAL, cap.REVOCATION):
        if source.execute("SELECT to_regclass(%s)", [table]).fetchone()[0]:
            raise AssertionError("0074 table exists in predecessor")
archive_restore("human_cap_before")
with db("human_cap_before") as copy:
    if not restored_equal(old_catalog(copy), before):
        raise AssertionError("0074 predecessor restored catalog/file differs")

MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        new.verify_catalog(cursor)
    if old_catalog(source) != before:
        raise AssertionError("0074 changed old function OID/body/ACL/owner or file bytes")
    for table in (cap.APPROVAL, cap.REVOCATION):
        if source.execute("SELECT count(*) FROM " + table).fetchone() != (0,):
            raise AssertionError("0074 migration created an approval")
    after = old_catalog(source)
assert_formal_backup_closed()
archive_restore("human_cap_after")
with db("human_cap_after") as copy:
    with copy.cursor() as cursor:
        new.verify_catalog(cursor)
    if not restored_equal(old_catalog(copy), after):
        raise AssertionError("0074 restored old catalog/file differs")
MigrationExecutor(connection).migrate([OLD])
with db() as source:
    if old_catalog(source) != before:
        raise AssertionError("0074 empty reverse changed predecessor")
MigrationExecutor(connection).migrate([NEW])
with db() as source:
    with source.cursor() as cursor:
        new.verify_catalog(cursor)
    if old_catalog(source) != before:
        raise AssertionError("0074 reapply changed predecessor")
if hashlib.sha256(DOWNLOAD.read_bytes()).hexdigest() != download_sha:
    raise AssertionError("0074 changed v11 download hard refusal")

result = {"upgrade": "0073->0074", "status": "passed",
    "oldFunctionOidBodyAclOwnerPreserved": True,
    "oldProtectedTableOwnerAclPreserved": True,
    "oldFileChunkCount": before[3], "oldFileBytesSha256": before[2],
    "newProtectedTables": 2, "newRoles": 0, "newApprovals": 0,
    "providerCallsAllowed": False, "formalBackupRejectedBeforeArchive": True,
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-market-v2-human-cap-upgrade-evidence.json").write_text(
    json.dumps(result,ensure_ascii=False,indent=2), encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
