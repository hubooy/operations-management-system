"""Pure, process-free checks for the default-closed synthetic 0073 shadow."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/protected-ai-shadow-snapshot-0073.py"
spec = importlib.util.spec_from_file_location("shadow_snapshot_0073", SCRIPT)
assert spec and spec.loader
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic_seed() -> dict:
    return {"upgrade": "0072->0073", "afterBackupRestored": True,
        "beforeBackupRestored": True, "emptyReverseAndReapply": True,
        "defaultRoleNoLoginAndNoPassword": True,
        "newAttestationRows": 0, "oldFileChunkCount": 2,
        "productionWrites": False}


def protected_fixture() -> dict:
    result = {f"protected_business_test_{i}": [] for i in range(8)}
    result["protected_business_budget_v11_verifier_keys"] = [
        {"key_id": "synthetic", "secret": "in-memory-test"}]
    return result


class ShadowSnapshotPureTests(unittest.TestCase):
    def test_default_refusal_precedes_any_database_or_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "not-a-source"
            with self.assertRaisesRegex(shadow.ShadowBlocked,
                    "disabled by default"):
                shadow.main(["--run-root", str(root), "--target-port", "55852"])
            self.assertFalse(root.exists())

    def test_exact_synthetic_source_and_seed_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "checkout"
            run = root / ".runtime" / "ai-pg-123456abcdef"
            run.mkdir(parents=True)
            db = {"HOST": "127.0.0.1", "PORT": "55851",
                "NAME": "teruisi_ai_rehearsal", "USER": "ai_rehearsal_admin"}
            self.assertEqual(shadow.validate_isolation(root, run, db,
                synthetic_seed(), 55852, enabled=True), 55851)
            for bad in ({**synthetic_seed(), "productionWrites": True},
                    {**synthetic_seed(), "oldFileChunkCount": 0},
                    {**synthetic_seed(), "upgrade": "0073->0074"}):
                with self.assertRaises(shadow.ShadowBlocked):
                    shadow.validate_isolation(root, run, db, bad, 55852,
                        enabled=True)
            with self.assertRaises(shadow.ShadowBlocked):
                shadow.validate_isolation(root, run, db, synthetic_seed(),
                    55851, enabled=True)
            with self.assertRaises(shadow.ShadowBlocked):
                shadow.validate_isolation(root, run, {**db, "HOST": "0.0.0.0"},
                    synthetic_seed(), 55852, enabled=True)

    def test_file_chunk_root_requires_nonempty_verified_html_and_xlsx(self):
        rows = [("run", 1, 1, "html", 1, b"<p>x</p>",
                    sha(b"<p>x</p>")),
                ("run", 1, 1, "xlsx", 1, b"xlsx-bytes",
                    sha(b"xlsx-bytes"))]
        first = shadow.file_digest_rows(rows)
        self.assertEqual(first["chunkCount"], 2)
        self.assertEqual(first["formats"], ["html", "xlsx"])
        self.assertEqual(first, shadow.file_digest_rows(list(reversed(rows))))
        self.assertNotIn("xlsx-bytes", json.dumps(first))
        for invalid in (rows[:1], rows + rows[:1],
                [rows[0], (*rows[1][:-1], sha(b"changed"))],
                [rows[0], (*rows[1][:5], b"", sha(b""))]):
            with self.assertRaises(shadow.ShadowBlocked):
                shadow.file_digest_rows(invalid)

    def test_protected_root_requires_nine_tables_and_one_synthetic_key(self):
        rows = protected_fixture()
        digest = shadow.protected_row_digest(rows)
        self.assertEqual(len(digest), 9)
        self.assertEqual(digest[
            "protected_business_budget_v11_verifier_keys"]["rowCount"], 1)
        self.assertNotIn("in-memory-test", json.dumps(digest))
        for invalid in ({**rows, "protected_business_budget_v11_verifier_keys": []},
                {**rows, "protected_business_budget_v11_verifier_keys": [
                    {"key_id": "a"}, {"key_id": "b"}]},
                {key: value for key, value in rows.items()
                    if key != "protected_business_test_0"}):
            with self.assertRaises(shadow.ShadowBlocked):
                shadow.protected_row_digest(invalid)

    def test_manifest_context_binds_snapshot_rows_files_and_roles(self):
        rows = shadow.protected_row_digest(protected_fixture())
        files = shadow.file_digest_rows([
            ("run", 1, 1, "html", 1, b"html", sha(b"html")),
            ("run", 1, 1, "xlsx", 1, b"xlsx", sha(b"xlsx"))])
        evidence = {"shadowContentSha256": "a" * 64,
            "formalContentSha256Verified": False,
            "migrationRootSha256": "d" * 64,
            "tableRowsRootSha256": "e" * 64,
            "catalogOwnerAclRootSha256": "f" * 64}
        args = ("snapshot", evidence, rows, files, [("role", False)],
            55851, 55852)
        manifest, context = shadow.snapshot_manifest(*args)
        self.assertEqual(context, shadow.snapshot_manifest(*args)[1])
        self.assertFalse(manifest["formalBackupPathVerified"])
        self.assertFalse(manifest["formalContentSha256Verified"])
        self.assertNotIn("contentSha256", manifest)
        self.assertFalse(manifest["longTermKeyCustodyVerified"])
        self.assertFalse(manifest["productionWrites"])
        for changed in (("snapshot2", *args[1:]),
                (args[0], {**evidence,
                    "shadowContentSha256": "b" * 64}, *args[2:]),
                (*args[:3], {**files, "chunkRootSha256": "c" * 64}, *args[4:]),
                (*args[:4], [("other-role", False)], *args[5:])):
            self.assertNotEqual(context, shadow.snapshot_manifest(*changed)[1])

    def test_process_key_is_strict_and_existing_formal_flags_unchanged(self):
        key = shadow.SyntheticKeyProvider(b"k" * 32)
        self.assertEqual(key.resolve_key(shadow.ARCHIVE_KEY_ID, "seal"),
            b"k" * 32)
        for key_id, purpose in (("other", "seal"),
                (shadow.ARCHIVE_KEY_ID, "publish")):
            with self.assertRaises(shadow.ShadowBlocked):
                key.resolve_key(key_id, purpose)
        with self.assertRaises(shadow.ShadowBlocked):
            shadow.SyntheticKeyProvider(b"short")
        formal = (ROOT / "tools/postgres-consistent-backup.py").read_text(
            encoding="utf-8")
        self.assertIn('FORMAL_DUMP_FLAGS = ("--no-owner", "--no-privileges")',
            formal)
        self.assertIn('FORMAL_RESTORE_FLAGS = ("--no-owner", "--no-privileges")',
            formal)

    def test_native_child_uses_file_handles_not_inherited_pipes(self):
        calls = []

        def completed(*args, **kwargs):
            self.assertNotIn("capture_output", kwargs)
            self.assertIs(kwargs["stdout"], kwargs["stderr"])
            self.assertGreaterEqual(kwargs["stdout"].fileno(), 0)
            calls.append(args[0])
            return SimpleNamespace(returncode=0)

        with patch.object(shadow.subprocess, "run", side_effect=completed):
            shadow._native(["synthetic-native", "--status"], {}, timeout=3)
        self.assertEqual(calls, [["synthetic-native", "--status"]])

        def rejected(*args, **kwargs):
            kwargs["stderr"].write(b"synthetic-error")
            return SimpleNamespace(returncode=1)

        with patch.object(shadow.subprocess, "run", side_effect=rejected):
            with self.assertRaisesRegex(shadow.ShadowBlocked,
                    sha(b"synthetic-error")):
                shadow._native(["synthetic-native"], {}, timeout=3)

    def test_current_v11_verifier_and_shadow_root_do_not_claim_formal_content(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("_verify_promotion_trial_file_guard(cursor, budget_stage_enabled=True,",
            source)
        self.assertIn("publish_gate_enabled=True, slim_stage_enabled=True)", source)
        self.assertIn("collect_shadow_evidence(db, expected_port=port,", source)
        self.assertIn('"formalContentSha256Verified": False', source)
        self.assertNotIn("helper.collect_evidence", source)
        self.assertNotIn('"contentSha256": evidence', source)

    def test_mismatch_diagnostic_reveals_only_roots_counts_and_first_table(self):
        evidence = {"shadowContentSha256": "a" * 64,
            "tableRowsRootSha256": "b" * 64,
            "protectedRowsRootSha256": "c" * 64,
            "fileRootSha256": "d" * 64,
            "roleRootSha256": "e" * 64,
            "catalogOwnerAclRootSha256": "f" * 64,
            "migrationRootSha256": "0" * 64,
            "catalogSectionRoots": {"constraints": "1" * 64,
                "functions": "2" * 64},
            "tableRoots": {"protected_business_example": {
                "rowCount": 1, "rowHashesSha256": "3" * 64}},
            "tableCount": 282, "rowCount": 12}
        target = {**evidence,
            "shadowContentSha256": "9" * 64,
            "tableRowsRootSha256": "8" * 64,
            "catalogOwnerAclRootSha256": "7" * 64,
            "catalogSectionRoots": {"constraints": "1" * 64,
                "functions": "6" * 64},
            "tableRoots": {"protected_business_example": {
                "rowCount": 2, "rowHashesSha256": "5" * 64}}}
        roles = {"entries": [("role", False)],
            "credentialPresence": [("role", False)],
            "runtimeSettings": {}, "aiAclSha256": "4" * 64}
        diagnostic = shadow.mismatch_diagnostic(evidence, target,
            {"secret": "raw-synthetic-only"},
            {"secret": "raw-synthetic-only"},
            {"chunkRootSha256": "d" * 64},
            {"chunkRootSha256": "d" * 64}, roles, roles)
        self.assertFalse(diagnostic["checks"]["shadowContentSha256"])
        self.assertTrue(diagnostic["checks"]["fileChunks"])
        self.assertFalse(diagnostic["catalogSectionEqual"]["functions"])
        self.assertEqual(diagnostic["firstDifferentTable"]["table"],
            "protected_business_example")
        self.assertEqual(diagnostic["firstDifferentTable"]["sourceRows"], 1)
        self.assertNotIn("raw-synthetic-only", str(diagnostic))

    def test_catalog_diagnostic_pins_first_object_and_only_field_digests(self):
        base = {"shadowContentSha256": "a" * 64,
            "catalogSectionRoots": {"relations": "b" * 64},
            "catalogItemRoots": {"relations": {"public.ai_table": {
                "name": "c" * 64, "kind": "d" * 64,
                "owner": "e" * 64, "acl": "f" * 64,
                "rowSecurity": "0" * 64}}}}
        target = {**base,
            "catalogSectionRoots": {"relations": "1" * 64},
            "catalogItemRoots": {"relations": {"public.ai_table": {
                **base["catalogItemRoots"]["relations"]["public.ai_table"],
                "acl": "2" * 64}}}}
        diag = shadow.mismatch_diagnostic(base, target, {}, {}, {}, {},
            {}, {})
        item = diag["firstDifferentCatalogObject"]
        self.assertEqual(item["type"], "relations")
        self.assertEqual(item["identity"], "public.ai_table")
        self.assertFalse(item["fieldEqual"]["acl"])
        self.assertTrue(item["fieldEqual"]["owner"])
        self.assertEqual(item["differentFieldDigests"]["acl"], {
            "sourceSha256": "f" * 64, "targetSha256": "2" * 64})
        self.assertEqual(diag["firstDifferentCatalogBySection"]["relations"],
            item)
        self.assertNotIn("raw catalog", str(diag))

    def test_constraint_and_index_diagnostics_stay_hash_only(self):
        for section in ("constraints", "indexes"):
            with self.subTest(section=section):
                fields = shadow.CATALOG_ITEM_FIELDS[section]
                identity = "public.ai_table.ai_object"
                before = {field: "a" * 64 for field in fields}
                after = {**before, "definition": "b" * 64}
                source = {"catalogSectionRoots": {section: "c" * 64},
                    "catalogItemRoots": {section: {identity: before}}}
                target = {"catalogSectionRoots": {section: "d" * 64},
                    "catalogItemRoots": {section: {identity: after}}}
                item = shadow.mismatch_diagnostic(source, target,
                    {}, {}, {}, {}, {}, {})["firstDifferentCatalogObject"]
                self.assertEqual(item["type"], section)
                self.assertEqual(item["identity"], identity)
                self.assertFalse(item["fieldEqual"]["definition"])
                self.assertEqual(item["differentFieldDigests"]["definition"],
                    {"sourceSha256": "a" * 64, "targetSha256": "b" * 64})

    def test_raw_acl_order_difference_reports_semantic_equality(self):
        identity = "public.ai_table"
        source = {"catalogSectionRoots": {"relations": "a" * 64},
            "catalogItemRoots": {"relations": {identity: {"acl": "b" * 64}}},
            "rawRelationAclRoots": {identity: "c" * 64}}
        target = {**source,
            "rawRelationAclRoots": {identity: "d" * 64}}
        raw = shadow.mismatch_diagnostic(source, target,
            {}, {}, {}, {}, {}, {})["firstRawAclDifference"]
        self.assertEqual(raw["identity"], identity)
        self.assertFalse(raw["rawAclEqual"])
        self.assertTrue(raw["semanticAclEqual"])
        self.assertEqual(raw["sourceRawAclSha256"], "c" * 64)
        self.assertEqual(raw["targetRawAclSha256"], "d" * 64)
        target["catalogItemRoots"] = {"relations": {identity: {
            "acl": "e" * 64}}}
        raw = shadow.mismatch_diagnostic(source, target,
            {}, {}, {}, {}, {}, {})["firstRawAclDifference"]
        self.assertFalse(raw["semanticAclEqual"])


if __name__ == "__main__":
    unittest.main()
