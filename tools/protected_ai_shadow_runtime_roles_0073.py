"""Exact, default-closed synthetic evidence roles for the 0073 shadow only.

These are not migration operators, production logins, or role grants. The
frozen backup evidence collector needs their names to test denied privileges.
"""
from __future__ import annotations

import json
import re


EVIDENCE_ROLES = (
    "teruisi_ai_reader",
    "teruisi_ai_writer",
    "teruisi_finance_reader",
    "teruisi_finance_writer",
    "teruisi_netshop_reader",
    "teruisi_netshop_writer",
)
AI_RUNTIME_ROLES = frozenset({"teruisi_ai_reader", "teruisi_ai_writer"})


class ShadowRoleBlocked(RuntimeError):
    pass


def creation_sql(role: str) -> str:
    if role not in EVIDENCE_ROLES:
        raise ShadowRoleBlocked("0073 shadow role is outside the exact allowlist")
    return ("CREATE ROLE " + role + " NOLOGIN NOINHERIT NOSUPERUSER "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL")


def verify_catalog_rows(rows: list[tuple], memberships: int, *,
                        require_complete: bool = True) -> None:
    """Pin pg_authid state without weakening post-migration object ACLs."""
    names = [row[0] for row in rows if isinstance(row, tuple) and row]
    if (type(memberships) is not int or memberships != 0
            or len(names) != len(rows) or len(set(names)) != len(names)
            or not set(names) <= set(EVIDENCE_ROLES)
            or require_complete and set(names) != set(EVIDENCE_ROLES)):
        raise ShadowRoleBlocked("0073 shadow evidence role inventory drifted")
    for row in rows:
        if len(row) != 11:
            raise ShadowRoleBlocked("0073 shadow evidence role shape drifted")
        checks = {"noLogin": row[1] is False,
            "noInherit": row[2] is False, "noSuperuser": row[3] is False,
            "noCreateDb": row[4] is False,
            "noCreateRole": row[5] is False,
            "noReplication": row[6] is False,
            "noBypassRls": row[7] is False,
            "passwordNull": row[8] is None,
            "defaultConnectionLimit": row[9] == -1,
            "validUntilNull": row[10] is None}
        if not all(checks.values()):
            # Never include the password/hash or the raw catalog row.
            raise ShadowRoleBlocked("0073 shadow evidence role properties drifted; role="
                + row[0] + ";checks=" + json.dumps(checks, sort_keys=True))


def verify_preexisting_clean(rows: list[tuple], memberships: int,
                             dependent_objects: int,
                             role_settings: int) -> None:
    verify_catalog_rows(rows, memberships, require_complete=False)
    if (type(dependent_objects) is not int or dependent_objects != 0
            or type(role_settings) is not int or role_settings != 0):
        raise ShadowRoleBlocked("0073 shadow existing role has extra authority")


def _duration_ms(value: str) -> int:
    if value.isdecimal():
        return int(value)
    if value.endswith("ms") and value[:-2].isdecimal():
        return int(value[:-2])
    if value.endswith("s") and value[:-1].isdecimal():
        return int(value[:-1]) * 1000
    raise ShadowRoleBlocked("0073 shadow role timeout setting drifted")


def verify_post_provision_rows(rows: list[tuple], memberships: int,
                               settings_rows: list[tuple]) -> dict[str, tuple]:
    """Pin normal synthetic ProvisionRoles outcome without exposing hashes."""
    names = [row[0] for row in rows if isinstance(row, tuple) and row]
    if (type(memberships) is not int or memberships != 0
            or len(names) != len(EVIDENCE_ROLES)
            or set(names) != set(EVIDENCE_ROLES)
            or len(set(names)) != len(names)):
        raise ShadowRoleBlocked("0073 post-provision role inventory drifted")
    for row in rows:
        if len(row) != 11:
            raise ShadowRoleBlocked("0073 post-provision role shape drifted")
        ai = row[0] in AI_RUNTIME_ROLES
        checks = {"loginState": row[1] is ai,
            "noInherit": row[2] is False, "noSuperuser": row[3] is False,
            "noCreateDb": row[4] is False,
            "noCreateRole": row[5] is False,
            "noReplication": row[6] is False,
            "noBypassRls": row[7] is False,
            "passwordState": (row[8] is not None) is ai,
            "defaultConnectionLimit": row[9] == -1,
            "validUntilNull": row[10] is None}
        if not all(checks.values()):
            raise ShadowRoleBlocked("0073 post-provision role property drift; role="
                + row[0] + ";checks=" + json.dumps(checks, sort_keys=True))
    configs: dict[str, tuple] = {}
    for role, database_oid, raw_settings in settings_rows:
        if (role not in AI_RUNTIME_ROLES or role in configs
                or database_oid != 0 or not isinstance(raw_settings,
                    (list, tuple))):
            raise ShadowRoleBlocked("0073 post-provision role settings scope drifted")
        pairs = {}
        for entry in raw_settings:
            if not isinstance(entry, str) or "=" not in entry:
                raise ShadowRoleBlocked("0073 post-provision role setting invalid")
            key, value = entry.split("=", 1)
            if key in pairs:
                raise ShadowRoleBlocked("0073 post-provision duplicate role setting")
            pairs[key] = value
        if set(pairs) != {"default_transaction_read_only",
                "statement_timeout", "idle_in_transaction_session_timeout"}:
            raise ShadowRoleBlocked("0073 post-provision role settings differ")
        expected_read_only = "on" if role == "teruisi_ai_reader" else "off"
        if (pairs["default_transaction_read_only"] != expected_read_only
                or _duration_ms(pairs["statement_timeout"]) != 15000
                or _duration_ms(pairs["idle_in_transaction_session_timeout"]) != 30000):
            raise ShadowRoleBlocked("0073 post-provision role setting value drifted")
        configs[role] = (expected_read_only, 15000, 30000)
    if set(configs) != AI_RUNTIME_ROLES:
        raise ShadowRoleBlocked("0073 post-provision role settings incomplete")
    return configs


TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
    "REFERENCES", "TRIGGER")


def verify_ai_acl_matrix(tables: list[str], matrix: list[tuple],
                         reader_tables: set[str], writer_privileges: dict,
                         model_columns: list[str],
                         expected_model_columns: set[str]) -> None:
    expected = []
    for role, allowed in (("teruisi_ai_reader",
            {table: ("SELECT",) for table in reader_tables}),
            ("teruisi_ai_writer", writer_privileges)):
        for table in tables:
            for privilege in TABLE_PRIVILEGES:
                expected.append((role, table, privilege,
                    privilege in allowed.get(table, ())))
    if matrix != expected or set(model_columns) != expected_model_columns:
        raise ShadowRoleBlocked("0073 post-provision AI table/column ACL drifted")


def new_target_credentials(role_names, random_hex) -> dict[str, str]:
    """Create fresh target-only synthetic secrets; no source value is input."""
    if not AI_RUNTIME_ROLES <= set(role_names):
        raise ShadowRoleBlocked("0073 target lacks AI runtime role identity")
    values = {name: random_hex(32) for name in sorted(AI_RUNTIME_ROLES)}
    if (len(set(values.values())) != len(values)
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in values.values())):
        raise ShadowRoleBlocked("0073 target synthetic credentials are not independent")
    return values
