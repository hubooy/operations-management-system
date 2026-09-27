"""Create only missing closed evidence roles in a fresh 0073 test cluster.

Explicit test-only entry point. It never grants privileges, changes an
existing role, or runs against the production checkout/database/port.
"""
from __future__ import annotations

import argparse
from ipaddress import IPv4Address, IPv6Address, ip_interface
import json
import os
from pathlib import Path
import re

import psycopg

from protected_ai_shadow_runtime_roles_0073 import (
    EVIDENCE_ROLES, ShadowRoleBlocked, creation_sql,
    verify_catalog_rows, verify_preexisting_clean,
)


ROOT = Path(__file__).resolve().parents[1]
RUN_NAME = re.compile(r"ai-pg-[0-9a-f]{12}\Z")
ROLE_QUERY = ("SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
    "rolcreaterole,rolreplication,rolbypassrls,rolpassword,rolconnlimit,"
    "rolvaliduntil FROM pg_catalog.pg_authid "
    "WHERE rolname=ANY(%s) ORDER BY rolname")
MEMBERSHIP_QUERY = ("SELECT count(*) FROM pg_catalog.pg_auth_members m "
    "WHERE m.roleid=ANY(SELECT oid FROM pg_catalog.pg_authid "
    "WHERE rolname=ANY(%s)) OR m.member=ANY(SELECT oid FROM "
    "pg_catalog.pg_authid WHERE rolname=ANY(%s))")
DEPENDENCY_QUERY = ("SELECT count(*) FROM pg_catalog.pg_shdepend d "
    "WHERE d.refobjid=ANY(SELECT oid FROM pg_catalog.pg_authid "
    "WHERE rolname=ANY(%s))")
SETTINGS_QUERY = ("SELECT count(*) FROM pg_catalog.pg_db_role_setting s "
    "WHERE s.setrole=ANY(SELECT oid FROM pg_catalog.pg_authid "
    "WHERE rolname=ANY(%s))")


def canonical_loopback(value: object) -> str:
    try:
        endpoint = ip_interface(str(value))
    except ValueError as error:
        raise ShadowRoleBlocked("0073 shadow server address is not loopback") from error
    address = endpoint.ip
    if isinstance(address, IPv4Address):
        if address == IPv4Address("127.0.0.1") and endpoint.network.prefixlen == 32:
            return "127.0.0.1"
    elif isinstance(address, IPv6Address) and endpoint.network.prefixlen == 128:
        if address == IPv6Address("::1"):
            return "::1"
        if address.ipv4_mapped == IPv4Address("127.0.0.1"):
            return "127.0.0.1"
    raise ShadowRoleBlocked("0073 shadow server address is not exact loopback")


def validate_isolation(run_root: Path, port: int, *, enabled: bool,
                       env: dict[str, str]) -> None:
    if (enabled is not True
            or ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
            or run_root.resolve().parent != (ROOT / ".runtime").resolve()
            or RUN_NAME.fullmatch(run_root.name) is None
            or not run_root.is_dir() or run_root.is_symlink()
            or getattr(run_root, "is_junction", lambda: False)()
            or type(port) is not int or not 55440 <= port <= 55999
            or env.get("PGHOST") != "127.0.0.1"
            or env.get("PGPORT") != str(port)
            or env.get("PGUSER") != "ai_rehearsal_admin"
            or env.get("PGDATABASE") != "teruisi_ai_rehearsal"
            or not env.get("PGPASSWORD")):
        raise ShadowRoleBlocked("0073 role preflight requires fresh synthetic identity")


def _catalog(db) -> tuple[list[tuple], int, int, int]:
    names = list(EVIDENCE_ROLES)
    rows = db.execute(ROLE_QUERY, [names]).fetchall()
    memberships = db.execute(MEMBERSHIP_QUERY, [names, names]).fetchone()[0]
    dependencies = db.execute(DEPENDENCY_QUERY, [names]).fetchone()[0]
    settings = db.execute(SETTINGS_QUERY, [names]).fetchone()[0]
    return rows, memberships, dependencies, settings


def preprovision(db, expected_port: int) -> dict[str, object]:
    """One transaction: any drift rolls back all newly created roles."""
    identity = db.execute("SELECT current_database(),session_user,"
        "inet_server_addr()::text,inet_server_port(),pg_is_in_recovery()"
        ).fetchone()
    loopback_ok = False
    try:
        loopback_ok = canonical_loopback(identity[2]) in ("127.0.0.1", "::1")
    except ShadowRoleBlocked:
        pass
    checks = {"database": identity[0] == "teruisi_ai_rehearsal",
        "user": identity[1] == "ai_rehearsal_admin",
        "loopback": loopback_ok, "port": identity[3] == expected_port,
        "recovery": identity[4] is False}
    if not all(checks.values()):
        raise ShadowRoleBlocked("0073 role preflight database identity drifted; checks="
            + json.dumps(checks, sort_keys=True))
    existing, members, dependencies, settings = _catalog(db)
    verify_preexisting_clean(existing, members, dependencies, settings)
    before = {row[0] for row in existing}
    for name in EVIDENCE_ROLES:
        if name not in before:
            db.execute(creation_sql(name))
    after, members, _, settings = _catalog(db)
    verify_catalog_rows(after, members)
    if settings != 0:
        raise ShadowRoleBlocked("0073 shadow role settings drifted")
    return {"status": "passed", "generation": "0073",
        "identityChecks": checks,
        "evidenceRoles": len(EVIDENCE_ROLES),
        "existingRoles": len(before), "createdRoles": len(EVIDENCE_ROLES)-len(before),
        "closedNoLoginNoPassword": True, "memberships": 0,
        "productionWrites": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--enabled", action="store_true")
    args = parser.parse_args(argv)
    validate_isolation(args.run_root, args.port, enabled=args.enabled,
        env=os.environ)
    with psycopg.connect(host="127.0.0.1", port=args.port,
            dbname="teruisi_ai_rehearsal", user="ai_rehearsal_admin",
            password=os.environ["PGPASSWORD"]) as db:
        result = preprovision(db, args.port)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
