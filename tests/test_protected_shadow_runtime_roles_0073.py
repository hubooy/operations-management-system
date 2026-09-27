"""Pure failures and synthetic DB adapter for the six closed shadow roles."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from protected_ai_shadow_runtime_roles_0073 import (
    AI_RUNTIME_ROLES, EVIDENCE_ROLES, TABLE_PRIVILEGES, ShadowRoleBlocked,
    creation_sql, new_target_credentials, verify_ai_acl_matrix,
    verify_catalog_rows,
    verify_post_provision_rows, verify_preexisting_clean,
)

spec = importlib.util.spec_from_file_location("shadow_role_preflight",
    ROOT / "tools/protected-ai-shadow-role-preflight-0073.py")
assert spec and spec.loader
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def row(name: str, **changes) -> tuple:
    values = {"canlogin": False, "inherit": False, "super": False,
        "createdb": False, "createrole": False, "replication": False,
        "bypass": False, "password": None, "connlimit": -1,
        "validuntil": None}
    values.update(changes)
    return (name, *values.values())


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class FakeDb:
    def __init__(self, existing=(), *, memberships=0, dependencies=0,
                 settings=0, identity=None):
        self.rows = {item[0]: item for item in existing}
        self.memberships = memberships
        self.dependencies = dependencies
        self.settings = settings
        self.identity = identity or ("teruisi_ai_rehearsal",
            "ai_rehearsal_admin", "127.0.0.1", 55851, False)
        self.created = []

    def execute(self, query, args=None):
        if query.startswith("SELECT current_database()"):
            return Result(self.identity)
        if query == preflight.ROLE_QUERY:
            return Result(sorted(self.rows.values()))
        if query == preflight.MEMBERSHIP_QUERY:
            return Result((self.memberships,))
        if query == preflight.DEPENDENCY_QUERY:
            return Result((self.dependencies,))
        if query == preflight.SETTINGS_QUERY:
            return Result((self.settings,))
        for role in EVIDENCE_ROLES:
            if query == creation_sql(role):
                if role in self.rows:
                    raise AssertionError("existing role was rewritten")
                self.rows[role] = row(role)
                self.created.append(role)
                return Result(None)
        raise AssertionError("unexpected SQL in isolated role adapter")


class ShadowRoleContractTests(unittest.TestCase):
    @staticmethod
    def post_rows():
        return [row(name, canlogin=name in AI_RUNTIME_ROLES,
            password="synthetic-hash-" + name if name in AI_RUNTIME_ROLES
                else None) for name in EVIDENCE_ROLES]

    @staticmethod
    def role_settings():
        return [("teruisi_ai_reader", 0, [
            "default_transaction_read_only=on",
            "statement_timeout=15000",
            "idle_in_transaction_session_timeout=30000"]),
            ("teruisi_ai_writer", 0, [
            "default_transaction_read_only=off",
            "statement_timeout=15s",
            "idle_in_transaction_session_timeout=30s"])]

    def test_exact_postgres_loopback_text_and_cidr_forms(self):
        for value in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128",
                "::ffff:127.0.0.1", "::ffff:127.0.0.1/128"):
            with self.subTest(value=value):
                self.assertIn(preflight.canonical_loopback(value),
                    ("127.0.0.1", "::1"))
        for value in ("0.0.0.0", "192.168.1.2", "127.0.0.1/24",
                "127.0.0.2", "::", "::2", "::ffff:127.0.0.2",
                "::ffff:127.0.0.1/120", "hostname", ""):
            with self.subTest(value=value):
                with self.assertRaises(ShadowRoleBlocked):
                    preflight.canonical_loopback(value)

    def test_exact_six_no_login_no_password_role_names(self):
        self.assertEqual(set(EVIDENCE_ROLES), {
            "teruisi_ai_reader", "teruisi_ai_writer",
            "teruisi_netshop_reader", "teruisi_netshop_writer",
            "teruisi_finance_reader", "teruisi_finance_writer"})
        for role in EVIDENCE_ROLES:
            statement = creation_sql(role)
            self.assertIn(" NOLOGIN NOINHERIT NOSUPERUSER ", statement)
            self.assertIn(" NOBYPASSRLS PASSWORD NULL", statement)
        with self.assertRaises(ShadowRoleBlocked):
            creation_sql("teruisi_sales_writer")

    def test_missing_extra_or_drifted_role_fails_closed(self):
        complete = [row(name) for name in EVIDENCE_ROLES]
        verify_catalog_rows(complete, 0)
        for broken in (complete[:-1], complete + [row("teruisi_extra")],
                [row(EVIDENCE_ROLES[0], canlogin=True), *complete[1:]],
                [row(EVIDENCE_ROLES[0], inherit=True), *complete[1:]],
                [row(EVIDENCE_ROLES[0], password="hash"), *complete[1:]],
                [row(EVIDENCE_ROLES[0], connlimit=1), *complete[1:]]):
            with self.subTest(broken=broken[0]):
                with self.assertRaises(ShadowRoleBlocked):
                    verify_catalog_rows(broken, 0)
        with self.assertRaises(ShadowRoleBlocked):
            verify_catalog_rows(complete, 1)
        with self.assertRaises(ShadowRoleBlocked) as password_error:
            verify_catalog_rows([row(EVIDENCE_ROLES[0],
                password="synthetic-secret-hash"), *complete[1:]], 0)
        self.assertIn('"passwordNull": false', str(password_error.exception))
        self.assertNotIn("synthetic-secret-hash", str(password_error.exception))

    def test_existing_role_extra_authority_rejected_before_create(self):
        clean = [row(EVIDENCE_ROLES[0])]
        verify_preexisting_clean(clean, 0, 0, 0)
        for kwargs in ({"memberships": 1}, {"dependencies": 1},
                {"settings": 1}):
            db = FakeDb(clean, **kwargs)
            with self.assertRaises(ShadowRoleBlocked):
                preflight.preprovision(db, 55851)
            self.assertEqual(db.created, [])
        db = FakeDb([row(EVIDENCE_ROLES[0], password="hash")])
        with self.assertRaises(ShadowRoleBlocked):
            preflight.preprovision(db, 55851)
        self.assertEqual(db.created, [])

    def test_only_missing_roles_created_and_existing_clean_one_preserved(self):
        db = FakeDb([row(EVIDENCE_ROLES[0])])
        receipt = preflight.preprovision(db, 55851)
        self.assertEqual(receipt["createdRoles"], 5)
        self.assertEqual(receipt["existingRoles"], 1)
        self.assertEqual(receipt["identityChecks"], {
            "database": True, "user": True, "loopback": True,
            "port": True, "recovery": True})
        self.assertEqual(db.created, list(EVIDENCE_ROLES[1:]))
        verify_catalog_rows(sorted(db.rows.values()), 0)
        self.assertFalse(receipt["productionWrites"])
        with self.assertRaises(ShadowRoleBlocked):
            preflight.preprovision(db, 55852)

    def test_database_identity_accepts_cidr_and_rejects_other_identity(self):
        for address in ("127.0.0.1/32", "::1/128",
                "::ffff:127.0.0.1/128"):
            with self.subTest(address=address):
                db = FakeDb(identity=("teruisi_ai_rehearsal",
                    "ai_rehearsal_admin", address, 55851, False))
                self.assertEqual(preflight.preprovision(db, 55851)[
                    "createdRoles"], 6)
        for identity in (("wrong", "ai_rehearsal_admin", "127.0.0.1/32",
                    55851, False),
                ("teruisi_ai_rehearsal", "wrong", "127.0.0.1/32",
                    55851, False),
                ("teruisi_ai_rehearsal", "ai_rehearsal_admin", "10.0.0.1",
                    55851, False),
                ("teruisi_ai_rehearsal", "ai_rehearsal_admin",
                    "127.0.0.1/32", 55852, False),
                ("teruisi_ai_rehearsal", "ai_rehearsal_admin",
                    "127.0.0.1/32", 55851, True)):
            with self.subTest(identity=identity):
                db = FakeDb(identity=identity)
                with self.assertRaisesRegex(ShadowRoleBlocked,
                        "database identity drifted"):
                    preflight.preprovision(db, 55851)
                self.assertEqual(db.created, [])

    def test_entrypoint_default_refuses_before_connecting(self):
        with self.assertRaises(ShadowRoleBlocked):
            preflight.validate_isolation(ROOT / ".runtime/ai-pg-123456abcdef",
                55851, enabled=False, env={})

    def test_post_provision_login_password_and_settings_are_exact(self):
        rows = self.post_rows()
        settings = self.role_settings()
        actual = verify_post_provision_rows(rows, 0, settings)
        self.assertEqual(set(actual), AI_RUNTIME_ROLES)
        self.assertEqual(actual["teruisi_ai_reader"], ("on", 15000, 30000))
        for broken in (
                [row(EVIDENCE_ROLES[0]), *rows[1:]],
                [*rows[:2], row(EVIDENCE_ROLES[2], canlogin=True,
                    password="synthetic"), *rows[3:]],
                [row(EVIDENCE_ROLES[0], inherit=True, canlogin=True,
                    password="synthetic"), *rows[1:]]):
            with self.assertRaises(ShadowRoleBlocked):
                verify_post_provision_rows(broken, 0, settings)
        with self.assertRaises(ShadowRoleBlocked):
            verify_post_provision_rows(rows, 1, settings)
        for broken_settings in (settings[:1], settings + [settings[0]],
                [(settings[0][0], 1, settings[0][2]), settings[1]],
                [(settings[0][0], 0,
                    ["default_transaction_read_only=off",
                     "statement_timeout=15000",
                     "idle_in_transaction_session_timeout=30000"]),
                    settings[1]],
                [(settings[0][0], 0,
                    [*settings[0][2], "search_path=public"]), settings[1]]):
            with self.assertRaises(ShadowRoleBlocked):
                verify_post_provision_rows(rows, 0, broken_settings)

    def test_ai_acl_matrix_rejects_added_or_missing_grants(self):
        tables = ["ai_models", "ai_reports"]
        reader = {"ai_reports"}
        writer = {"ai_reports": ("SELECT", "INSERT")}
        matrix = [(role, table, privilege,
            privilege in ({"ai_reports": ("SELECT",)} if role ==
                "teruisi_ai_reader" else writer).get(table, ()))
            for role in ("teruisi_ai_reader", "teruisi_ai_writer")
            for table in tables for privilege in TABLE_PRIVILEGES]
        verify_ai_acl_matrix(tables, matrix, reader, writer,
            ["name", "status"], {"name", "status"})
        changed = list(matrix)
        changed[0] = (*changed[0][:3], not changed[0][3])
        with self.assertRaises(ShadowRoleBlocked):
            verify_ai_acl_matrix(tables, changed, reader, writer,
                ["name", "status"], {"name", "status"})
        with self.assertRaises(ShadowRoleBlocked):
            verify_ai_acl_matrix(tables, matrix, reader, writer,
                ["name", "secret"], {"name", "status"})

    def test_target_credentials_are_fresh_distinct_and_never_source_input(self):
        draws = iter(("a" * 64, "b" * 64))
        values = new_target_credentials(EVIDENCE_ROLES,
            lambda count: next(draws))
        self.assertEqual(set(values), AI_RUNTIME_ROLES)
        self.assertNotEqual(*values.values())
        self.assertTrue(all(len(value) == 64 for value in values.values()))
        with self.assertRaises(ShadowRoleBlocked):
            new_target_credentials(["teruisi_ai_reader"],
                lambda count: "a" * 64)
        with self.assertRaises(ShadowRoleBlocked):
            new_target_credentials(EVIDENCE_ROLES,
                lambda count: "a" * 64)
        with self.assertRaises(ShadowRoleBlocked):
            new_target_credentials(EVIDENCE_ROLES,
                lambda count: "not-a-hex-secret")

    def test_preflight_environment_names_the_same_explicit_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "frozen-checkout"
            run_root = root / ".runtime/ai-pg-123456abcdef"
            run_root.mkdir(parents=True)
            env = {"PGHOST": "127.0.0.1", "PGPORT": "55851",
                "PGUSER": "ai_rehearsal_admin",
                "PGDATABASE": "teruisi_ai_rehearsal",
                "PGPASSWORD": "synthetic-only"}
            with patch.object(preflight, "ROOT", root):
                preflight.validate_isolation(run_root, 55851,
                    enabled=True, env=env)
                for broken in ({**env, "PGDATABASE": "postgres"},
                        {**env, "PGUSER": "wrong"},
                        {**env, "PGPORT": "55852"}):
                    with self.assertRaises(ShadowRoleBlocked):
                        preflight.validate_isolation(run_root, 55851,
                            enabled=True, env=broken)


if __name__ == "__main__":
    unittest.main()
