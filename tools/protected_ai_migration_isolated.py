"""CLI adapter for a disposable protected-migration clone, never production.

Synthetic admin/ordinary PostgreSQL URLs are read only from process
environment. The tool prints no URL, password, SQL row or native diagnostic.
No service controller or formal release path imports this module.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlparse

import psycopg
from psycopg import sql

from protected_ai_migration_installer import (
    ADMIN_ROLE, CLONE_DATABASE, Identity, InstallerBlocked, JsonLedger,
    MigrationEntry, ORDINARY_ROLE, ROOT, ROLE_NAMES, RoleState, STEP_NAMES,
    STEPS,
    Snapshot, apply_one, audit_unknown, build_plan, source_digest,
)


SAFE_BLOCK_CODES = {
    "isolated PostgreSQL URL identity invalid": "url_identity_invalid",
    "protected installer is isolated-test only": "isolation_identity_invalid",
    "isolated 0066 seed evidence missing": "seed_missing",
    "isolated 0066 seed evidence invalid": "seed_invalid",
    "synthetic installer URLs target different DBs": "url_database_mismatch",
    "synthetic installer URLs do not match plan": "url_plan_mismatch",
    "synthetic installer connection drift": "connection_drift",
    "synthetic installer role privileges drift": "role_privileges_drift",
    "ordinary installer can read private verifier key": "ordinary_private_key_readable",
    "Django migration planner URL differs": "planner_url_drift",
    "Django migration planner database drift": "planner_database_drift",
    "reviewed migration source is missing or redirected": "source_missing",
    "Django migration plan has an extra, reverse, or missing step":
        "plan_not_exact_forward",
    "0066 predecessor receipt is absent": "baseline_receipt_missing",
    "unapproved migration after 0066 exists": "unapproved_migration",
    "protected migration receipts are not a prefix": "receipt_gap",
    "protected catalog witness is not the exact prefix": "catalog_prefix_invalid",
    "protected role properties or membership drift": "protected_role_drift",
    "completed protected migration lacks role": "protected_role_missing",
}


def _safe_reason(error: Exception) -> str:
    if isinstance(error, InstallerBlocked):
        message = str(error)
        if message.startswith("reviewed migration source digest changed:"):
            return "source_digest_changed"
        return SAFE_BLOCK_CODES.get(message, "blocked_unclassified")
    return "unexpected_error"


def _ensure_backend_import_path() -> None:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _url_identity(value: str) -> tuple[str, int, str, str]:
    try:
        parsed = urlparse(value)
        if (parsed.scheme not in ("postgres", "postgresql")
                or parsed.query or parsed.fragment or parsed.params
                or parsed.hostname != "127.0.0.1"
                or parsed.port is None or not 55440 <= parsed.port <= 55999
                or parsed.path != "/" + CLONE_DATABASE
                or not parsed.username or not parsed.password):
            raise InstallerBlocked("isolated PostgreSQL URL identity invalid")
        return parsed.hostname, parsed.port, parsed.path[1:], parsed.username
    except ValueError:
        raise InstallerBlocked("isolated PostgreSQL URL identity invalid") from None


class IsolatedPostgresAdapter:
    def __init__(self, identity: Identity, admin_url: str,
            ordinary_url: str):
        identity.validate()
        source_digest(identity.source_root)
        if (_url_identity(admin_url) != ("127.0.0.1", identity.port,
                identity.database, ADMIN_ROLE)
                or _url_identity(ordinary_url) != ("127.0.0.1",
                    identity.port, identity.database, ORDINARY_ROLE)):
            raise InstallerBlocked("synthetic installer URLs do not match plan")
        self.identity = identity
        self.admin_url = admin_url
        self.ordinary_url = ordinary_url
        self._check_connections()

    def _connect(self, *, ordinary: bool = False):
        return psycopg.connect(self.ordinary_url if ordinary else self.admin_url,
            autocommit=True, connect_timeout=5)

    def _check_connections(self) -> None:
        for ordinary, expected_user in ((False, ADMIN_ROLE),
                (True, ORDINARY_ROLE)):
            with self._connect(ordinary=ordinary) as db:
                row = db.execute("SELECT current_database(),current_user,"
                    "COALESCE(inet_server_addr()::text,''),inet_server_port()"
                    ).fetchone()
                if (row is None or row[0] != self.identity.database
                        or row[1] != expected_user
                        or row[2] not in ("127.0.0.1", "127.0.0.1/32")
                        or row[3] != self.identity.port):
                    raise InstallerBlocked("synthetic installer connection drift")
                flags = db.execute("SELECT rolsuper,rolinherit,rolcreatedb,"
                    "rolcreaterole,rolreplication,rolbypassrls "
                    "FROM pg_catalog.pg_roles WHERE rolname=current_user"
                    ).fetchone()
                if flags != ((False, False, False, False, False, False)
                        if ordinary else
                        (True, True, True, True, True, True)):
                    raise InstallerBlocked("synthetic installer role privileges drift")
                if ordinary:
                    key_table = db.execute("SELECT to_regclass(%s)",
                        ["public.protected_business_budget_v11_verifier_keys"
                         ]).fetchone()
                    if key_table and key_table[0] is not None:
                        read = db.execute("SELECT has_table_privilege("
                            "current_user,%s,'SELECT')",
                            ["public.protected_business_budget_v11_verifier_keys"
                             ]).fetchone()
                        if read != (False,):
                            raise InstallerBlocked(
                                "ordinary installer can read private verifier key")

    def _django_plan(self) -> tuple[MigrationEntry, ...]:
        # The calling test process must be configured for this exact synthetic
        # admin URL before django.setup(); no runtime URL is logged.
        self._snapshot_stage = "django_url"
        if os.getenv("TERUISI_DJANGO_DATABASE_URL") != self.admin_url:
            raise InstallerBlocked("Django migration planner URL differs")
        self._snapshot_stage = "django_setup"
        _ensure_backend_import_path()
        import django
        django.setup()
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        self._snapshot_stage = "django_settings"
        settings = connection.settings_dict
        if (settings["HOST"] != self.identity.host
                or int(settings["PORT"]) != self.identity.port
                or settings["NAME"] != self.identity.database
                or settings["USER"] != ADMIN_ROLE):
            raise InstallerBlocked("Django migration planner database drift")
        self._snapshot_stage = "django_executor"
        planned = MigrationExecutor(connection).migration_plan(
            [("ai_assistant", STEP_NAMES[-1])])
        self._snapshot_stage = "django_convert"
        return tuple(MigrationEntry(migration.app_label, migration.name,
            backwards) for migration, backwards in planned)

    def snapshot(self) -> Snapshot:
        self._snapshot_stage = "connections"
        self._check_connections()
        with self._connect() as db:
            self._snapshot_stage = "receipts"
            applied = tuple(row[0] for row in db.execute(
                "SELECT name FROM django_migrations WHERE app='ai_assistant' "
                "ORDER BY name").fetchall())
            verified = set()
            self._snapshot_stage = "catalogs"
            with db.cursor() as cursor:
                for name in STEP_NAMES:
                    if name in applied:
                        module = importlib.import_module(
                            "ai_assistant.migrations." + name)
                        module.verify_catalog(cursor)
                        verified.add(name)
            self._snapshot_stage = "roles"
            rows = db.execute("SELECT rolname,rolcanlogin,rolinherit,"
                "rolsuper,rolcreatedb,rolcreaterole,rolreplication,"
                "rolbypassrls,rolpassword IS NULL FROM pg_catalog.pg_authid "
                "WHERE rolname=ANY(%s)", [list(ROLE_NAMES)]).fetchall()
            roles = {}
            self._snapshot_stage = "memberships"
            for name, *flags in rows:
                count = db.execute("SELECT count(*) FROM "
                    "pg_catalog.pg_auth_members WHERE roleid=%s::regrole "
                    "OR member=%s::regrole", [name, name]).fetchone()[0]
                roles[name] = RoleState(*flags, count)
        self._snapshot_stage = "django_plan"
        return Snapshot(applied, frozenset(verified), roles,
            self._django_plan())

    def preprovision(self, roles: tuple[str, ...]) -> None:
        if not set(roles) <= ROLE_NAMES:
            raise InstallerBlocked("role bootstrap requested an unknown role")
        with self._connect() as db:
            for name in roles:
                existing = db.execute("SELECT to_regrole(%s)", [name]).fetchone()
                if existing is None:
                    raise InstallerBlocked("role bootstrap catalog unavailable")
                if existing[0] is None:
                    db.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD NULL").format(
                            sql.Identifier(name)))
        # apply_one requires a new fully verified snapshot before migrate_one.

    def migrate_one(self, step) -> None:
        if (step.name not in STEP_NAMES
                or step != STEPS[STEP_NAMES.index(step.name)]):
            raise InstallerBlocked("unapproved protected migration step")
        selected_url = (self.ordinary_url if step.installer == "ordinary"
            else self.admin_url)
        env = {**os.environ,
            "TERUISI_DJANGO_DATABASE_URL": selected_url,
            "TERUISI_DJANGO_ENVIRONMENT": "test",
            "TERUISI_DJANGO_PROCESS_ROLE": "development",
            "DJANGO_SETTINGS_MODULE": "teruisi_backend.settings",
            "DJANGO_SECRET_KEY": os.environ.get("DJANGO_SECRET_KEY")
                or secrets.token_hex(32),
            "TERUISI_DJANGO_INTERNAL_SECRET": os.environ.get(
                "TERUISI_DJANGO_INTERNAL_SECRET") or secrets.token_hex(32),
        }
        try:
            result = subprocess.run([sys.executable,
                ROOT / "backend/manage.py", "migrate", "ai_assistant",
                step.name, "--noinput", "--verbosity", "0"],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW
                    if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise InstallerBlocked("isolated migration command timeout") from None
        if result.returncode != 0:
            # Native output can contain source details; never print it. The
            # generic exception becomes an unknown result in apply_one.
            raise InstallerBlocked("isolated migration command failed")
        if os.getenv("TERUISI_PROTECTED_INSTALLER_TEST_LOST_REPLY_STEP") == step.name:
            # Fault injection is possible only after the synthetic clone and
            # both role identities have passed their strict test-only checks.
            raise ConnectionError("synthetic post-commit reply lost")


def _seed(identity: Identity) -> None:
    path = (Path(identity.run_root) /
        "protected-installer-0066-seed.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstallerBlocked("isolated 0066 seed evidence missing") from None
    if (value.get("schemaVersion") != "protected-installer-0066-seed-v1"
            or value.get("baseline") != "0066_business_promotion_budget_v11_durable_stage"
            or value.get("cloneRestored") is not True
            or value.get("baselineCatalogVerified") is not True
            or value.get("finance0005Applied") is not True
            or value.get("productionWrites") is not False):
        raise InstallerBlocked("isolated 0066 seed evidence invalid")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--plan", action="store_true")
    actions.add_argument("--apply-one", action="store_true")
    actions.add_argument("--audit-unknown", metavar="OPERATION_ID")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--approved-plan-digest", default="")
    arguments = parser.parse_args()
    diagnostic_stage = "identity"
    try:
        admin_url = os.environ.get("TERUISI_PROTECTED_INSTALLER_ADMIN_URL", "")
        ordinary_url = os.environ.get("TERUISI_PROTECTED_INSTALLER_ORDINARY_URL", "")
        host, port, database, admin_user = _url_identity(admin_url)
        _, ordinary_port, ordinary_database, ordinary_user = _url_identity(
            ordinary_url)
        identity = Identity(ROOT, arguments.run_root,
            os.environ.get("TERUISI_DJANGO_ENVIRONMENT", ""), host, port,
            database, admin_user, ordinary_user)
        identity.validate()
        if ordinary_port != port or ordinary_database != database:
            raise InstallerBlocked("synthetic installer URLs target different DBs")
        diagnostic_stage = "seed"
        _seed(identity)
        diagnostic_stage = "connections"
        adapter = IsolatedPostgresAdapter(identity, admin_url, ordinary_url)
        diagnostic_stage = "ledger"
        ledger = JsonLedger(identity)
        if arguments.plan:
            diagnostic_stage = "snapshot"
            snapshot = adapter.snapshot()
            diagnostic_stage = "plan"
            plan = build_plan(identity, snapshot, root=ROOT)
            payload = {"status":"candidate_only", "formalAllowed":False,
                "planDigest":plan.digest,"sourceDigest":plan.source_digest,
                "completed":list(plan.completed),"remaining":list(plan.remaining),
                "nextStep":plan.next_step.name if plan.next_step else None}
        elif arguments.apply_one:
            diagnostic_stage = "apply"
            if (not arguments.approved_plan_digest
                    or arguments.audit_unknown is not None):
                raise InstallerBlocked("isolated apply requires approved plan")
            payload = apply_one(identity, adapter, ledger,
                approved_plan_digest=arguments.approved_plan_digest, root=ROOT)
        else:
            diagnostic_stage = "audit"
            payload = audit_unknown(identity, adapter, ledger,
                arguments.audit_unknown, root=ROOT)
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0 if payload["status"] != "unknown" else 2
    except Exception as error:
        # Even parser/database errors must not print a credential-bearing URL.
        reason = _safe_reason(error)
        if reason == "unexpected_error":
            if diagnostic_stage == "snapshot":
                substage = getattr(adapter, "_snapshot_stage", "unknown")
                if substage in ("connections", "receipts", "catalogs",
                        "roles", "memberships", "django_plan", "django_url",
                        "django_setup", "django_settings", "django_executor",
                        "django_convert"):
                    reason = "unexpected_at_snapshot_" + substage
                else:
                    reason = "unexpected_at_snapshot_unknown"
            else:
                reason = "unexpected_at_" + diagnostic_stage
        print(json.dumps({"status":"blocked","formalAllowed":False,
            "reasonCode":reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
