"""One fresh isolated PG cluster: forward-only 0066 seed and seven-step installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
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
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant import business_promotion_budget_v11_stage_sql as stage
from protected_ai_migration_installer import (
    ADMIN_ROLE, BASELINE, CLONE_DATABASE, ORDINARY_ROLE, ROLE_NAMES,
    STEP_NAMES,
)
from protected_ai_migration_isolated import SAFE_BLOCK_CODES


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or database["USER"] != ADMIN_ROLE
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999):
    raise RuntimeError("installer fixture requires exact isolated source cluster")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = int(database["PORT"])
ADMIN_PASSWORD = str(database["PASSWORD"])


def db(name: str, user: str = ADMIN_ROLE, password: str = ADMIN_PASSWORD):
    return psycopg.connect(host="127.0.0.1", port=PORT, dbname=name,
        user=user, password=password, autocommit=True)


def native(command: list[object], *, timeout: int = 600):
    env = {**os.environ,"PGHOST":"127.0.0.1","PGPORT":str(PORT),
        "PGUSER":ADMIN_ROLE,"PGPASSWORD":ADMIN_PASSWORD,
        "PGDATABASE":database["NAME"]}
    result = subprocess.run([str(value) for value in command], cwd=ROOT,
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError("isolated installer fixture native command failed; "
            "diagnosticSha256=" + hashlib.sha256(result.stderr[:16384]).hexdigest())


def transfer_public_ownership(admin):
    relations = admin.execute("SELECT c.relname,c.relkind FROM "
        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND pg_catalog.pg_get_userbyid(c.relowner)=%s "
        "AND c.relkind IN ('r','p','S','v','m','f') ORDER BY c.relname",
        [ADMIN_ROLE]).fetchall()
    kinds = {"r":"TABLE","p":"TABLE","S":"SEQUENCE","v":"VIEW",
        "m":"MATERIALIZED VIEW","f":"FOREIGN TABLE"}
    for name, kind in relations:
        admin.execute(sql.SQL("ALTER {} {} OWNER TO {}").format(
            sql.SQL(kinds[kind]),sql.Identifier("public",name),
            sql.Identifier(ORDINARY_ROLE)))
    functions = admin.execute("SELECT p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid),p.prokind "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
        "ON n.oid=p.pronamespace WHERE n.nspname='public' "
        "AND pg_catalog.pg_get_userbyid(p.proowner)=%s "
        "AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_depend d "
        "WHERE d.objid=p.oid AND d.deptype='e') ORDER BY 1,2",
        [ADMIN_ROLE]).fetchall()
    kinds = {"f":"FUNCTION","p":"PROCEDURE","a":"AGGREGATE",
        "w":"FUNCTION"}
    for name, arguments, kind in functions:
        admin.execute(sql.SQL("ALTER {} {}.{}({}) OWNER TO {}").format(
            sql.SQL(kinds[kind]),sql.Identifier("public"),
            sql.Identifier(name),sql.SQL(arguments),
            sql.Identifier(ORDINARY_ROLE)))
    admin.execute("GRANT ALL ON SCHEMA public TO " + ORDINARY_ROLE)


def command(args: list[str], env: dict[str, str], *, allowed=(0,)):
    result = subprocess.run([sys.executable,
        ROOT / "tools/protected_ai_migration_isolated.py", *args,
        "--run-root", folder], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode not in allowed:
        try:
            diagnostic = json.loads(result.stdout).get("reasonCode")
        except (ValueError, UnicodeError, AttributeError):
            diagnostic = None
        known_codes = set(SAFE_BLOCK_CODES.values()) | {
            "source_digest_changed", "blocked_unclassified",
            "unexpected_at_identity", "unexpected_at_seed",
            "unexpected_at_connections", "unexpected_at_ledger",
            "unexpected_at_snapshot", "unexpected_at_plan",
            "unexpected_at_apply", "unexpected_at_audit",
            "unexpected_at_snapshot_connections",
            "unexpected_at_snapshot_receipts",
            "unexpected_at_snapshot_catalogs",
            "unexpected_at_snapshot_roles",
            "unexpected_at_snapshot_memberships",
            "unexpected_at_snapshot_django_plan",
            "unexpected_at_snapshot_django_url",
            "unexpected_at_snapshot_django_setup",
            "unexpected_at_snapshot_django_settings",
            "unexpected_at_snapshot_django_executor",
            "unexpected_at_snapshot_django_convert",
            "unexpected_at_snapshot_unknown"}
        if diagnostic not in known_codes:
            diagnostic = "invalid_response"
        raise RuntimeError("isolated installer command failed; diagnosticSha256="
            + hashlib.sha256((result.stdout + result.stderr)[:16384]).hexdigest()
            + "; reasonCode=" + diagnostic)
    try:
        payload = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise RuntimeError("isolated installer response is invalid") from None
    if not isinstance(payload, dict):
        raise RuntimeError("isolated installer response is not an object")
    return payload


# All targets move forward together from a new empty cluster; no old history
# reverse and no mixing finance.0005 forward with AI backward migrations.
with db(database["NAME"]) as admin:
    for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
        if admin.execute("SELECT to_regrole(%s)", [role]).fetchone()[0] is None:
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                "PASSWORD NULL").format(sql.Identifier(role)))
targets = [("ai_assistant", BASELINE),
    ("access_control", "0001_initial"),
    ("finance", "0005_raw_column_evidence_v2"),
    ("netshop", "0003_netshop_source_revision_guard")]
planned = MigrationExecutor(connection).migration_plan(targets)
if not planned or any(backwards for _,backwards in planned):
    raise RuntimeError("installer source fixture is not forward-only")
MigrationExecutor(connection).migrate(targets)
with db(database["NAME"]) as source:
    receipts = {row[0] for row in source.execute("SELECT name FROM "
        "django_migrations WHERE app='ai_assistant'").fetchall()}
    if BASELINE not in receipts or any(name in receipts for name in STEP_NAMES):
        raise RuntimeError("installer source did not stop at exact 0066")
    if source.execute("SELECT count(*) FROM django_migrations WHERE "
            "app='finance' AND name='0005_raw_column_evidence_v2'"
            ).fetchone() != (1,):
        raise RuntimeError("finance 0005 current baseline was not installed")
    if (source.execute("SELECT count(*) FROM django_migrations WHERE "
            "app='access_control' AND name='0001_initial'").fetchone() !=
            (1,) or source.execute("SELECT to_regclass(%s)",
                ["public.access_control_users"]).fetchone()[0] is None):
        raise RuntimeError("access-control authority baseline was not installed")
    with source.cursor() as cursor:
        stage.verify_catalog(cursor)

with db(database["NAME"]) as admin:
    if admin.execute("SELECT to_regrole(%s)", [ORDINARY_ROLE]).fetchone()[0]:
        raise RuntimeError("synthetic ordinary migrator already exists")
    ordinary_password = secrets.token_hex(32)
    admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(ORDINARY_ROLE),sql.Literal(ordinary_password)))

archive = folder / "installer-seed-0066.dump"
if archive.exists():
    raise RuntimeError("installer source archive already exists")
native([BIN / "pg_dump.exe", "--format=custom", "--file", archive,
    database["NAME"]])
native([BIN / "createdb.exe", "--owner", ORDINARY_ROLE, CLONE_DATABASE])
native([BIN / "pg_restore.exe", "--single-transaction", "--exit-on-error",
    "--dbname", CLONE_DATABASE, archive])
with db(CLONE_DATABASE) as admin:
    transfer_public_ownership(admin)
    if admin.execute("SELECT count(*) FROM django_migrations WHERE "
            "app='ai_assistant' AND name=%s", [BASELINE]).fetchone() != (1,):
        raise RuntimeError("installer cloned baseline receipt missing")
    with admin.cursor() as cursor:
        stage.verify_catalog(cursor)
with db(CLONE_DATABASE, ORDINARY_ROLE, ordinary_password) as ordinary:
    flags = ordinary.execute("SELECT rolsuper,rolinherit,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=current_user").fetchone()
    if flags != (False,) * 6:
        raise RuntimeError("installer ordinary role gained privilege")

seed = {"schemaVersion":"protected-installer-0066-seed-v1",
    "baseline":BASELINE,"cloneRestored":True,
    "baselineCatalogVerified":True,"finance0005Applied":True,
    "productionWrites":False}
(folder / "protected-installer-0066-seed.json").write_text(
    json.dumps(seed,ensure_ascii=True,sort_keys=True),encoding="utf-8")
admin_url = (f"postgresql://{ADMIN_ROLE}:{quote(ADMIN_PASSWORD)}@"
    f"127.0.0.1:{PORT}/{CLONE_DATABASE}")
ordinary_url = (f"postgresql://{ORDINARY_ROLE}:{quote(ordinary_password)}@"
    f"127.0.0.1:{PORT}/{CLONE_DATABASE}")
installer_env = {**os.environ,
    "TERUISI_PROTECTED_INSTALLER_ADMIN_URL":admin_url,
    "TERUISI_PROTECTED_INSTALLER_ORDINARY_URL":ordinary_url,
    "TERUISI_DJANGO_DATABASE_URL":admin_url,
    "TERUISI_DJANGO_ENVIRONMENT":"test",
    "TERUISI_DJANGO_PROCESS_ROLE":"development",
    "DJANGO_SETTINGS_MODULE":"teruisi_backend.settings",
    "DJANGO_SECRET_KEY":secrets.token_hex(32),
    "TERUISI_DJANGO_INTERNAL_SECRET":secrets.token_hex(32),
}

steps = []
unknown_reconciled = False
for name in STEP_NAMES:
    planned = command(["--plan"], installer_env)
    if (planned.get("nextStep") != name or
            planned.get("status") != "candidate_only" or
            planned.get("formalAllowed") is not False):
        raise RuntimeError("installer plan changed before step")
    selected_env = dict(installer_env)
    if name == "0068_business_promotion_budget_v11_verifier_receipt":
        selected_env["TERUISI_PROTECTED_INSTALLER_TEST_LOST_REPLY_STEP"] = name
    result = command(["--apply-one","--approved-plan-digest",
        planned["planDigest"]],selected_env,allowed=(0,2))
    if name == "0068_business_promotion_budget_v11_verifier_receipt":
        if result.get("status") != "unknown" or result.get("replayAllowed") is not False:
            raise RuntimeError("lost 0068 reply did not fail closed")
        recovered = command(["--audit-unknown",result["operationId"]],
            installer_env)
        if recovered.get("status") != "committed" or recovered.get(
                "replayAllowed") is not False:
            raise RuntimeError("0068 committed outcome not reconciled")
        unknown_reconciled = True
    elif result.get("status") != "committed":
        raise RuntimeError("protected installer step did not commit")
    steps.append({"name":name,"status":result["status"],
        "installer":("ordinary" if name in (STEP_NAMES[0], STEP_NAMES[2],
            STEP_NAMES[4], STEP_NAMES[5]) else "privileged")})

finished = command(["--plan"],installer_env)
if finished.get("remaining") != [] or finished.get("nextStep") is not None:
    raise RuntimeError("protected installer did not complete exact prefix")
with db(CLONE_DATABASE) as admin:
    for name in STEP_NAMES:
        module = __import__("importlib").import_module(
            "ai_assistant.migrations." + name)
        with admin.cursor() as cursor:
            module.verify_catalog(cursor)
    role_count = admin.execute("SELECT count(*) FROM pg_catalog.pg_roles "
        "WHERE rolname=ANY(%s)", [list(ROLE_NAMES)]).fetchone()[0]
    if role_count != 13 or admin.execute("SELECT count(*) FROM public."
            "protected_business_budget_v11_verifier_keys").fetchone() != (0,):
        raise RuntimeError("installer role/key inventory changed")
with db(CLONE_DATABASE, ORDINARY_ROLE, ordinary_password) as ordinary:
    private_read = ordinary.execute("SELECT has_table_privilege(current_user,"
        "%s,'SELECT')", ["public.protected_business_budget_v11_verifier_keys"
         ]).fetchone()
    if private_read != (False,):
        raise RuntimeError("ordinary installer can read private key")

evidence = {"schemaVersion":"protected-installer-isolated-result-v1",
    "status":"passed","sourcePort":PORT,"cloneDatabase":CLONE_DATABASE,
    "sourceDigest":finished["sourceDigest"],"steps":steps,
    "exactForwardPlan":True,"unknown0068ReconciledWithoutReplay":unknown_reconciled,
    "protectedRoleCount":role_count,"privateKeyRows":0,
    "ordinaryPrivateKeyReadDenied":True,"finance0005Current":True,
    "formalAllowed":False,"productionWrites":False}
(folder / "protected-installer-isolated-evidence.json").write_text(
    json.dumps(evidence,ensure_ascii=True,sort_keys=True,indent=2),
    encoding="utf-8")
print(json.dumps(evidence,ensure_ascii=True,sort_keys=True))
