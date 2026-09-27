"""Test-only 0067-0072 migration roles in an isolated, cloned PostgreSQL DB.

The source is the ai-postgres-rehearsal.py 0066 synthetic cluster. The probe
uses a real, password-authenticated NOSUPERUSER/NOCREATEROLE migration login.
No production database, credential or verifier key is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed_path = folder / "business-promotion-budget-v11-stage-upgrade-evidence.json"
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or database["USER"] != "ai_rehearsal_admin"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or seed.get("upgrade") != "0065->0066"
        or seed.get("afterBackupRestored") is not True
        or seed.get("emptyReverseAndReapply") is not True):
    raise RuntimeError("protected role test requires isolated 0066 seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = int(database["PORT"])
ADMIN_PASSWORD = str(database["PASSWORD"])
MIGRATOR = "teruisi_sales_owner"
CLONE = "teruisi_ai_migration_role_probe"
STEPS = (
    ("0067_business_promotion_budget_v11_attestation",
     ("teruisi_ai_budget_v11_attestor",),
     "public.ai_business_promotion_budget_v11_attestations"),
    ("0068_business_promotion_budget_v11_verifier_receipt",
     ("teruisi_ai_budget_v11_key_owner", "teruisi_ai_budget_v11_publisher"),
     "public.protected_business_budget_v11_verifier_keys"),
    ("0069_business_market_v2_paid_round_rehearsal",
     ("teruisi_ai_market_paid_adopter", "teruisi_ai_market_paid_reserver",
      "teruisi_ai_market_paid_starter"),
     "public.ai_business_market_v2_paid_authorities"),
    ("0070_business_promotion_budget_v11_limited_identity",
     ("teruisi_ai_budget_v11_attest_login",
      "teruisi_ai_budget_v11_sign_login",
      "teruisi_ai_budget_v11_publish_login"),
     "public.protected_business_budget_v11_proof_tickets"),
    ("0071_business_v4_report_source_link", (),
     "public.protected_business_v4_report_link_intents"),
    ("0072_business_market_v2_authority_proposals",
     ("teruisi_ai_market_rate_proposer",
      "teruisi_ai_market_cap_proposer",
      "teruisi_ai_market_proposal_revoker"),
     "public.protected_business_market_v2_rate_proposals"),
)
PROTECTED_TABLES_BY_STEP = {
    "0068_business_promotion_budget_v11_verifier_receipt": (
        "protected_business_budget_v11_verifier_keys",),
    "0070_business_promotion_budget_v11_limited_identity": (
        "protected_business_budget_v11_proof_tickets",
        "protected_business_budget_v11_proof_ticket_claims"),
    "0071_business_v4_report_source_link": (
        "protected_business_v4_report_link_intents",
        "protected_business_v4_report_source_links"),
    "0072_business_market_v2_authority_proposals": (
        "protected_business_market_v2_rate_proposals",
        "protected_business_market_v2_cap_proposals",
        "protected_business_market_v2_authority_revocations"),
}
modules = {name: importlib.import_module("ai_assistant.migrations." + name)
           for name, _, _ in STEPS}


def db(user: str, password: str, name: str) -> psycopg.Connection:
    return psycopg.connect(host="127.0.0.1", port=PORT, dbname=name,
        user=user, password=password, autocommit=True)


def native(command: list[object], env: dict[str, str], *, timeout: int = 300) -> None:
    completed = subprocess.run([str(value) for value in command],
        cwd=ROOT, env=env, capture_output=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if completed.returncode:
        raise RuntimeError("isolated protected migration fixture command failed")


def migration_attempt(user: str, password: str, name: str) -> tuple[bool, str, str]:
    url = (f"postgresql://{user}:{quote(password)}@127.0.0.1:"
        f"{PORT}/{CLONE}")
    env = {**os.environ, "TERUISI_DJANGO_DATABASE_URL": url,
        "TERUISI_DJANGO_PROCESS_ROLE": "development"}
    completed = subprocess.run([sys.executable, ROOT / "backend/manage.py",
        "migrate", "ai_assistant", name, "--noinput", "--verbosity", "0"],
        cwd=ROOT, env=env, capture_output=True, timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    output = (completed.stdout + completed.stderr)[:65536]
    text = output.decode("utf-8", errors="replace").lower()
    if completed.returncode == 0:
        kind = "installed"
    elif "permission denied to create role" in text or (
            "permission denied" in text and "create role" in text):
        kind = "create_role_denied"
    elif "admin option" in text or "permission denied to grant role" in text:
        kind = "grant_role_denied"
    elif "must be member of role" in text or "permission denied to change owner" in text:
        kind = "owner_transfer_denied"
    elif "permission denied for table" in text:
        kind = "table_read_denied"
    elif "permission denied" in text:
        kind = "other_permission_denied"
    else:
        kind = "unexpected_failure"
    return completed.returncode == 0, kind, hashlib.sha256(output).hexdigest()


def catalog_fingerprint(connection: psycopg.Connection) -> str:
    functions = connection.execute("SELECT p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid),p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace WHERE n.nspname='public' "
        "AND p.proname LIKE 'ai_%' ORDER BY 1,2").fetchall()
    relations = connection.execute("SELECT c.relname,c.relkind,c.relacl::text,"
        "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname LIKE 'ai_%' ORDER BY 1").fetchall()
    applied = connection.execute("SELECT name FROM django_migrations "
        "WHERE app='ai_assistant' ORDER BY name").fetchall()
    payload = json.dumps((functions, relations, applied), default=str,
        ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def assert_receipt(connection: psycopg.Connection, name: str,
        table: str, installed: bool) -> None:
    receipt = connection.execute("SELECT count(*) FROM django_migrations "
        "WHERE app='ai_assistant' AND name=%s", [name]).fetchone()
    physical = connection.execute("SELECT to_regclass(%s)", [table]).fetchone()
    if receipt != (int(installed),) or bool(physical[0]) != installed:
        raise AssertionError("migration receipt and table state diverged")


def assert_resume_prefix(connection: psycopg.Connection, next_index: int,
        preprovisioned_roles: tuple[str, ...] = ()) -> None:
    """Reconstruct the only resumable state from PostgreSQL, never local memory."""
    if not 0 <= next_index <= len(STEPS):
        raise AssertionError("invalid protected migration resume index")
    expected_roles = set(preprovisioned_roles)
    expected_tables = set()
    for index, (name, roles, table) in enumerate(STEPS):
        installed = index < next_index
        assert_receipt(connection, name, table, installed)
        if installed:
            expected_roles.update(roles)
            expected_tables.update(PROTECTED_TABLES_BY_STEP.get(name, ()))
            with connection.cursor() as cursor:
                modules[name].verify_catalog(cursor)
    actual_tables = {row[0] for row in connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
        "AND tablename LIKE 'protected_business_%'")}
    if actual_tables != expected_tables:
        raise AssertionError("protected table inventory is not an exact installed prefix")
    for role in {role for _, roles, _ in STEPS for role in roles}:
        attrs = connection.execute("SELECT rolcanlogin,rolinherit,rolsuper,"
            "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
            "rolpassword IS NULL FROM pg_catalog.pg_authid WHERE rolname=%s",
            [role]).fetchone()
        if role in expected_roles:
            if attrs != (False,) * 7 + (True,):
                raise AssertionError("protected role attributes drifted before resume")
            members = connection.execute("SELECT count(*) FROM "
                "pg_catalog.pg_auth_members WHERE roleid=%s::regrole "
                "OR member=%s::regrole", [role, role]).fetchone()
            if members != (0,):
                raise AssertionError("protected role membership drifted before resume")
        elif attrs is not None:
            raise AssertionError("future protected role exists before resume")


def preprovision(admin: psycopg.Connection, roles: tuple[str, ...]) -> None:
    for role in roles:
        if not re.fullmatch(r"teruisi_ai_[a-z0-9_]{1,64}", role):
            raise AssertionError("unapproved protected role name")
        if admin.execute("SELECT to_regrole(%s)", [role]).fetchone()[0] is None:
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                sql.Identifier(role)))
        attrs = admin.execute("SELECT rolcanlogin,rolinherit,rolsuper,"
            "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,"
            "rolpassword IS NULL FROM pg_catalog.pg_authid WHERE rolname=%s",
            [role]).fetchone()
        if attrs != (False,) * 7 + (True,):
            raise AssertionError("preprovisioned role privileges widened")
        members = admin.execute("SELECT count(*) FROM pg_catalog.pg_auth_members "
            "WHERE roleid=%s::regrole OR member=%s::regrole",
            [role, role]).fetchone()
        if members != (0,):
            raise AssertionError("preprovisioned role has members")


def transfer_public_ownership(admin: psycopg.Connection) -> None:
    relations = admin.execute("SELECT c.relname,c.relkind FROM "
        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND pg_catalog.pg_get_userbyid(c.relowner)='ai_rehearsal_admin' "
        "AND c.relkind IN ('r','p','S','v','m','f') ORDER BY c.relname").fetchall()
    kinds = {"r": "TABLE", "p": "TABLE", "S": "SEQUENCE", "v": "VIEW",
             "m": "MATERIALIZED VIEW", "f": "FOREIGN TABLE"}
    for name, kind in relations:
        admin.execute(sql.SQL("ALTER {} {} OWNER TO {}").format(
            sql.SQL(kinds[kind]), sql.Identifier("public", name),
            sql.Identifier(MIGRATOR)))
    functions = admin.execute("SELECT p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid),p.prokind "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace WHERE n.nspname='public' "
        "AND pg_catalog.pg_get_userbyid(p.proowner)='ai_rehearsal_admin' "
        "AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d "
        "WHERE d.objid=p.oid AND d.deptype='e') ORDER BY 1,2").fetchall()
    kinds = {"f": "FUNCTION", "p": "PROCEDURE", "a": "AGGREGATE", "w": "FUNCTION"}
    for name, arguments, kind in functions:
        admin.execute(sql.SQL("ALTER {} {}.{}({}) OWNER TO {}").format(
            sql.SQL(kinds[kind]), sql.Identifier("public"),
            sql.Identifier(name), sql.SQL(arguments), sql.Identifier(MIGRATOR)))


with db("ai_rehearsal_admin", ADMIN_PASSWORD, "teruisi_ai_rehearsal") as source:
    applied_cursor = source.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0066_business_promotion_budget_v11_durable_stage',"
        "'0067_business_promotion_budget_v11_attestation') ORDER BY name")
    if [row[0] for row in applied_cursor.fetchall()] != [
            "0066_business_promotion_budget_v11_durable_stage"]:
        raise RuntimeError("source migration state is not exact 0066")
    if source.execute("SELECT to_regrole(%s)", [MIGRATOR]).fetchone()[0]:
        raise RuntimeError("synthetic migrator role already exists")
    migrator_password = secrets.token_hex(32)
    source.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
        sql.Identifier(MIGRATOR), sql.Literal(migrator_password)))

native_env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": str(PORT),
    "PGUSER": "ai_rehearsal_admin", "PGPASSWORD": ADMIN_PASSWORD,
    "PGDATABASE": "teruisi_ai_rehearsal"}
archive = folder / "synthetic-0066-migration-role.dump"
if archive.exists():
    raise RuntimeError("migration-role fixture archive already exists")
native([BIN / "pg_dump.exe", "-Fc", "-f", archive,
    "teruisi_ai_rehearsal"], native_env, timeout=600)
native([BIN / "createdb.exe", "--owner", MIGRATOR, CLONE], native_env)
native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
    "-d", CLONE, archive], native_env, timeout=600)

results = []
interruptions = []
with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
    transfer_public_ownership(admin)
    admin.execute("GRANT ALL ON SCHEMA public TO " + MIGRATOR)
    role_cursor = admin.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [MIGRATOR])
    if role_cursor.fetchone() != (True, False, False, False, False, False, False):
        raise AssertionError("synthetic migration login is privileged")
with db(MIGRATOR, migrator_password, CLONE) as ordinary:
    stage = importlib.import_module(
        "ai_assistant.business_promotion_budget_v11_stage_sql")
    with ordinary.cursor() as cursor:
        stage.verify_catalog(cursor)


class _InjectedStop(Exception):
    pass


def inject_stop_and_reconnect(next_index: int, phase: str,
        preprovisioned_roles: tuple[str, ...] = ()) -> dict[str, object]:
    """Simulate a lost installer process, then rebuild state on a new connection."""
    try:
        raise _InjectedStop(phase)
    except _InjectedStop:
        pass
    with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as resumed:
        assert_resume_prefix(resumed, next_index, preprovisioned_roles)
        if preprovisioned_roles:
            preprovision(resumed, preprovisioned_roles)
            assert_resume_prefix(resumed, next_index, preprovisioned_roles)
    return {"phase": phase, "nextMigration": STEPS[next_index][0],
        "reopenedConnection": True, "exactPrefixVerified": True}


def assert_resume_rejects_drift(next_index: int) -> dict[str, bool]:
    """Neither a forged later receipt nor temporary role membership can resume."""
    result = {"futureReceiptRejected": False,
        "privateOwnerMembershipRejected": False}
    with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
        for kind in result:
            try:
                with admin.transaction():
                    if kind == "futureReceiptRejected":
                        admin.execute("INSERT INTO django_migrations "
                            "(app,name,applied) VALUES ('ai_assistant',%s,now())",
                            [STEPS[next_index][0]])
                    else:
                        admin.execute(sql.SQL("GRANT {} TO {}").format(
                            sql.Identifier(STEPS[1][1][0]),
                            sql.Identifier(MIGRATOR)))
                    try:
                        assert_resume_prefix(admin, next_index)
                    except (AssertionError, RuntimeError):
                        result[kind] = True
                    else:
                        raise AssertionError("resume accepted injected catalog drift")
                    raise _InjectedStop(kind)
            except _InjectedStop:
                assert_resume_prefix(admin, next_index)
        if not all(result.values()):
            raise AssertionError("resume drift probe did not reject every mutation")
    return result

first_name, first_roles, first_table = STEPS[0]
with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
    before = catalog_fingerprint(admin)
success, reason, output_sha = migration_attempt(MIGRATOR, migrator_password,
    first_name)
if success or reason != "create_role_denied":
    raise AssertionError("unprovisioned ordinary 0067 did not fail at CREATE ROLE")
with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
    assert_receipt(admin, first_name, first_table, False)
    if catalog_fingerprint(admin) != before or admin.execute(
            "SELECT to_regrole(%s)", [first_roles[0]]).fetchone()[0]:
        raise AssertionError("failed ordinary 0067 left schema or role changes")
    preprovision(admin, first_roles)
results.append({"migration": first_name, "withoutRole": reason,
    "failedAttemptDigest": output_sha, "failedAttemptRolledBack": True})

for index, (name, roles, table) in enumerate(STEPS):
    with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
        if index:
            preprovision(admin, roles)
        assert_resume_prefix(admin, index, roles)
        before = catalog_fingerprint(admin)
    if index == 1:
        interruptions.append(inject_stop_and_reconnect(index,
            "after_0068_role_preprovision", roles))
    success, reason, output_sha = migration_attempt(MIGRATOR,
        migrator_password, name)
    with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
        if not success:
            assert_receipt(admin, name, table, False)
            if catalog_fingerprint(admin) != before:
                raise AssertionError("failed ordinary migration changed catalog")
            assert_resume_prefix(admin, index, roles)
            if reason == "unexpected_failure":
                raise AssertionError("ordinary migration failed for unclassified reason")
        else:
            assert_receipt(admin, name, table, True)
    installed_by = "ordinary" if success else "isolated_admin"
    if not success:
        admin_success, admin_reason, _ = migration_attempt(
            "ai_rehearsal_admin", ADMIN_PASSWORD, name)
        if not admin_success:
            raise AssertionError("isolated privileged continuation failed: " + admin_reason)
    with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
        assert_receipt(admin, name, table, True)
        with admin.cursor() as cursor:
            modules[name].verify_catalog(cursor)
        assert_resume_prefix(admin, index + 1)
    if index == 1:
        interruptions.append(inject_stop_and_reconnect(index + 1,
            "after_0068_receipt_before_0069"))
        resume_drift_rejections = assert_resume_rejects_drift(index + 1)
    results.append({"migration": name, "ordinaryResult": reason,
        "installedBy": installed_by,
        "ordinaryAttemptDigest": output_sha,
        "failedAttemptRolledBack": not success})

with db("ai_rehearsal_admin", ADMIN_PASSWORD, CLONE) as admin:
    for name, roles, _ in STEPS:
        with admin.cursor() as cursor:
            modules[name].verify_catalog(cursor)
        for role in roles:
            attrs = admin.execute("SELECT rolcanlogin,rolinherit,rolsuper,"
                "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                "FROM pg_catalog.pg_roles WHERE rolname=%s", [role]).fetchone()
            if attrs != (False,) * 7:
                raise AssertionError("protected role widened after migrations")
    if admin.execute("SELECT count(*) FROM " + STEPS[1][2]).fetchone() != (0,):
        raise AssertionError("private key table is not empty")
    for role in (MIGRATOR, "teruisi_ai_writer", "teruisi_ai_reader"):
        if admin.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
                [role, STEPS[1][2]]).fetchone() != (False,):
            raise AssertionError("ordinary role can read verifier key table")
with db(MIGRATOR, migrator_password, CLONE) as ordinary:
    try:
        ordinary.execute("SELECT count(*) FROM " + STEPS[1][2])
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise AssertionError("ordinary migration login read private key table")

result = {"status": "passed", "scope": "isolated synthetic migration role only",
    "migrationLogin": "NOSUPERUSER NOCREATEROLE NOINHERIT",
    "steps": results, "privateKeyRows": 0,
    "injectedInterruptions": interruptions,
    "resumeDriftRejections": resume_drift_rejections,
    "ordinaryPrivateKeyReadDenied": True,
    "formalMigrationPathVerified": False,
    "formalBackupPathVerified": False,
    "productionWrites": False}
(folder / "business-protected-migration-role-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
