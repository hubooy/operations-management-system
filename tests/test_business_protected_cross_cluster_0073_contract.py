"""The frozen 0072 default and explicit protected synthetic generations."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from protected_ai_cross_cluster_generation import (  # noqa: E402
    BASE_MIGRATIONS, BASE_ROLES, LOGIN_MIGRATION, LOGIN_ROLE,
    contract, seed_matches,
)


class ProtectedCrossClusterGenerationTests(unittest.TestCase):
    def test_legacy_default_stays_0072_twelve_roles_eight_tables(self):
        old = contract()
        self.assertEqual(old.name, "0072")
        self.assertEqual(old.seed_file,
            "business-market-v2-authority-upgrade-evidence.json")
        self.assertEqual(old.upgrade, "0071->0072")
        self.assertEqual(old.migrations, BASE_MIGRATIONS)
        self.assertEqual(old.roles, BASE_ROLES)
        self.assertEqual((len(old.roles), len(old.migrations), old.table_count),
            (12, 5, 8))
        self.assertTrue(seed_matches({"upgrade": "0071->0072",
            "afterBackupRestored": True,
            "emptyReverseAndReapply": True}, old))
        self.assertFalse(seed_matches({"upgrade": "0072->0073",
            "afterBackupRestored": True,
            "emptyReverseAndReapply": True}, old))

    def test_0073_requires_full_new_seed_and_adds_one_role_table_generation(self):
        new = contract("0073")
        self.assertEqual(new.seed_file,
            "business-v11-login-attestation-upgrade-evidence.json")
        self.assertEqual(new.upgrade, "0072->0073")
        self.assertEqual(new.migrations, (*BASE_MIGRATIONS, LOGIN_MIGRATION))
        self.assertEqual(new.roles - BASE_ROLES, {LOGIN_ROLE})
        self.assertEqual((len(new.roles), len(new.migrations), new.table_count),
            (13, 6, 9))
        seed = {"upgrade": "0072->0073", "beforeBackupRestored": True,
            "afterBackupRestored": True, "emptyReverseAndReapply": True,
            "defaultRoleNoLoginAndNoPassword": True,
            "newAttestationRows": 0, "productionWrites": False}
        self.assertTrue(seed_matches(seed, new))
        for field in ("beforeBackupRestored", "afterBackupRestored",
                "emptyReverseAndReapply", "defaultRoleNoLoginAndNoPassword",
                "newAttestationRows", "productionWrites"):
            changed = dict(seed)
            changed[field] = None
            self.assertFalse(seed_matches(changed, new), field)
        with self.assertRaises(ValueError):
            contract("0075")

    def test_0074_extends_0073_without_changing_its_roles(self):
        prior = contract("0073")
        cap = contract("0074")
        self.assertEqual(cap.upgrade, "0073->0074")
        self.assertEqual(cap.seed_file,
            "business-market-v2-human-cap-upgrade-evidence.json")
        self.assertEqual(cap.roles, prior.roles)
        self.assertEqual(cap.migrations[:-1], prior.migrations)
        self.assertEqual((len(cap.roles), cap.table_count), (13, 11))
        self.assertTrue(seed_matches({"upgrade": cap.upgrade,
            "beforeBackupRestored": True, "afterBackupRestored": True,
            "emptyReverseAndReapply": True,
            "oldFunctionOidBodyAclOwnerPreserved": True,
            "newApprovals": 0, "productionWrites": False}, cap))

    def test_runner_only_selects_new_generation_by_explicit_flag(self):
        script = (ROOT / "tools/ai-postgres-rehearsal.py").read_text(
            encoding="utf-8")
        cross = (ROOT / "tools/business-protected-cross-cluster-restore-rehearsal.py"
            ).read_text(encoding="utf-8")
        self.assertIn("--business-protected-cross-cluster-restore-0073",
            script)
        self.assertIn('"--run-root", RUN, "--generation", "0073"', script)
        self.assertIn('default="0072"', cross)
        self.assertNotIn('"--no-owner"', cross)
        self.assertNotIn('"--no-privileges"', cross)
        self.assertIn("synthetic-source.dump.v2.aead", cross)
        self.assertIn("verify_catalog(target)", cross)
        self.assertIn("ordinary AI writer can read verifier key", cross)

    def test_legacy_default_result_field_set_stays_frozen(self):
        source = (ROOT / "tools/business-protected-cross-cluster-restore-rehearsal.py"
            ).read_text(encoding="utf-8")
        result_keys = []
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "result"
                        for target in node.targets)
                    and isinstance(node.value, ast.Dict)):
                keys = {key.value for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)}
                if keys:
                    result_keys.append(keys)
        self.assertEqual(result_keys, [{
            "status", "scope", "protectedMigrationsVerified",
            "protectedRoles", "protectedTables", "syntheticVerifierKeyRows",
            "ownersAndAclPreserved", "ownerAndAclTamperRejected",
            "ordinaryAiKeyReadDenied", "productionWrites",
            "formalBackupPathVerified", "privilegedMigrationPathVerified",
            "archiveEncryptionVerified", "archiveCipher", "archiveVersion",
            "archiveChunkBytes", "archiveChunkCount", "archiveContextSha256",
            "archiveManifestSha256", "encryptedArchiveSha256",
            "plaintextDumpFiles", "syntheticKeyPersisted",
            "archiveNegativeCasesRejected", "wrongKeyTamperAndTruncationRejected",
        }])
        self.assertIn('if generation.name in {"0073", "0074"}:\n        result.update(', source)


if __name__ == "__main__":
    unittest.main()
