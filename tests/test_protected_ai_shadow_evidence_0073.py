"""Pure roots and a synthetic query adapter for shadow-only 0073 evidence."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import protected_ai_shadow_evidence_0073 as candidate


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, query):
        return None

    def __iter__(self):
        return iter((row,) for row in self.rows)


class FakeDb:
    def __init__(self):
        self.tables = ["ai_business_volume_chunks"] + [
            f"protected_business_test_{i}" for i in range(9)]
        self.identity = ("teruisi_ai_rehearsal", "ai_rehearsal_admin",
            "127.0.0.1/32", 55851, False, 170011)
        self.function_body = None
        self.relation_acl = None
        self.migrations = [("ai_assistant", candidate.REQUIRED_RECEIPT)]

    def execute(self, query):
        text = str(query)
        if text.startswith("SELECT current_database()"):
            return Result(self.identity)
        if text.startswith("SELECT app,name FROM public.django_migrations"):
            return Result(self.migrations)
        if text.startswith("SELECT tablename FROM pg_catalog.pg_tables"):
            return Result([(name,) for name in self.tables])
        if "SELECT count(*)" in text and "row_to_json" in text:
            return Result((1, 100))
        if text.startswith("SELECT p.proname"):
            return Result([] if self.function_body is None else [(
                "ai_guard", "", self.function_body, "owner", False,
                None, None, "plpgsql")])
        if text.startswith("SELECT c.relname,c.relkind"):
            return Result([] if self.relation_acl is None else [(
                "ai_example", "r", "owner", False, False,
                self.relation_acl[0], self.relation_acl[1])])
        return Result([])

    def cursor(self):
        return Cursor([{"synthetic": "not-exported"}])


class ShadowEvidencePureTests(unittest.TestCase):
    @staticmethod
    def roots(db):
        return ({name: {"rowCount": 1, "rowsSha256": "a" * 64}
                for name in db.tables if name.startswith("protected_business_")},
            {"chunkCount": 2, "formats": ["html", "xlsx"],
                "chunkRootSha256": "b" * 64},
            {"entries": [("teruisi_ai_reader", True)],
                "runtimeSettings": {}, "aiAclSha256": "c" * 64})

    def test_logical_public_rows_are_order_independent_and_non_sensitive(self):
        first = candidate.digest_public_rows({"a": [
            {"secret": "synthetic-only"}, {"id": 1}], "b": []})
        second = candidate.digest_public_rows({"b": [], "a": [
            {"id": 1}, {"secret": "synthetic-only"}]})
        self.assertEqual(first, second)
        self.assertEqual(first["rowCount"], 2)
        self.assertNotIn("synthetic-only", str(first))
        changed = candidate.digest_public_rows({"a": [
            {"secret": "different"}, {"id": 1}], "b": []})
        self.assertNotEqual(first["tableRowsRootSha256"],
            changed["tableRowsRootSha256"])
        with patch.object(candidate, "MAX_ROWS", 1):
            with self.assertRaises(candidate.ShadowEvidenceBlocked):
                candidate.digest_public_rows({"a": [{"x": 1}, {"x": 2}]})

    def test_frozen_282_public_tables_fit_but_321_are_refused(self):
        self.assertEqual(candidate.MAX_TABLES, 320)
        accepted = candidate.digest_public_rows({f"t{i:03}": []
            for i in range(282)})
        self.assertEqual(accepted["tableCount"], 282)
        with self.assertRaises(candidate.ShadowEvidenceBlocked):
            candidate.digest_public_rows({f"t{i:03}": []
                for i in range(321)})

    def test_collector_binds_current_migration_protected_file_role_and_catalog(self):
        db = FakeDb()
        rows, files, roles = self.roots(db)
        first = candidate.collect_shadow_evidence(db, expected_port=55851,
            protected_rows=rows, files=files, roles=roles)
        second = candidate.collect_shadow_evidence(db, expected_port=55851,
            protected_rows=rows, files=files, roles=roles)
        self.assertEqual(first, second)
        self.assertFalse(first["formalContentSha256Verified"])
        self.assertEqual(len(first["shadowContentSha256"]), 64)
        self.assertNotIn("not-exported", str(first))
        changed = candidate.collect_shadow_evidence(db, expected_port=55851,
            protected_rows=rows,
            files={**files, "chunkRootSha256": "d" * 64}, roles=roles)
        self.assertNotEqual(first["shadowContentSha256"],
            changed["shadowContentSha256"])
        db.function_body = "synthetic function body"
        changed_catalog = candidate.collect_shadow_evidence(db,
            expected_port=55851, protected_rows=rows, files=files,
            roles=roles)
        self.assertNotEqual(first["catalogOwnerAclRootSha256"],
            changed_catalog["catalogOwnerAclRootSha256"])
        db.function_body = None
        db.relation_acl = ("owner=SELECT", [["owner", "owner",
            "SELECT", False]])
        changed_acl = candidate.collect_shadow_evidence(db,
            expected_port=55851, protected_rows=rows, files=files,
            roles=roles)
        self.assertNotEqual(first["catalogSectionRoots"]["relations"],
            changed_acl["catalogSectionRoots"]["relations"])
        self.assertIn("acl", changed_acl["catalogItemRoots"]["relations"][
            "public.ai_example"])
        self.assertNotIn("owner=SELECT", str(changed_acl))
        db.relation_acl = None
        db.migrations.append(("ai_assistant", "0072_synthetic_predecessor"))
        changed_migrations = candidate.collect_shadow_evidence(db,
            expected_port=55851, protected_rows=rows, files=files,
            roles=roles)
        self.assertNotEqual(first["migrationRootSha256"],
            changed_migrations["migrationRootSha256"])
        with self.assertRaises(candidate.ShadowEvidenceBlocked):
            candidate.collect_shadow_evidence(db, expected_port=55852,
                protected_rows=rows, files=files, roles=roles)
        with self.assertRaises(candidate.ShadowEvidenceBlocked):
            candidate.collect_shadow_evidence(db, expected_port=55851,
                protected_rows={"protected_business_only": {}},
                files=files, roles=roles)

    def test_only_exact_loopback_text_is_admitted(self):
        for value in ("127.0.0.1", "127.0.0.1/32", "::1", "::1/128",
                "::ffff:127.0.0.1/128"):
            candidate._loopback(value)
        for value in ("10.0.0.1", "127.0.0.1/24", "::ffff:127.0.0.2"):
            with self.assertRaises(candidate.ShadowEvidenceBlocked):
                candidate._loopback(value)

    def test_acl_order_only_is_equal_but_grantor_privilege_and_null_are_not(self):
        first = [["PUBLIC", "owner", "SELECT", False],
            ["reader", "owner", "UPDATE", True]]
        reverse = list(reversed(first))
        canonical = candidate.canonical_acl_entries(False, first)
        self.assertEqual(canonical,
            candidate.canonical_acl_entries(False, reverse))
        self.assertNotEqual(canonical,
            candidate.canonical_acl_entries(False, [
                ["PUBLIC", "other-grantor", "SELECT", False], first[1]]))
        self.assertNotEqual(canonical,
            candidate.canonical_acl_entries(False, [
                ["PUBLIC", "owner", "INSERT", False], first[1]]))
        self.assertNotEqual(candidate.canonical_acl_entries(True, None),
            candidate.canonical_acl_entries(False, []))
        with self.assertRaises(candidate.ShadowEvidenceBlocked):
            candidate.canonical_acl_entries(True, first)


if __name__ == "__main__":
    unittest.main()
