"""Process-free checks for the explicit frozen 0073 runner hook."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools/ai-postgres-rehearsal.py"
SHADOW = ROOT / "tools/protected-ai-shadow-snapshot-0073.py"
REVIEWED_SHADOW_SHA256 = (
    "2ade3c82a37f649f0752887f5fcc91dd96fb50414c26116c468f829130874e5a")


class FrozenShadowRunnerTests(unittest.TestCase):
    def test_shadow_script_is_reviewed_versioned_evidence_candidate(self):
        self.assertEqual(hashlib.sha256(SHADOW.read_bytes()).hexdigest(),
            REVIEWED_SHADOW_SHA256)

    def test_invalid_modes_refuse_before_run_root_creation(self):
        runtime = ROOT / ".runtime"
        before = set(runtime.glob("ai-pg-*")) if runtime.exists() else set()
        for args in (
                ["--shadow-target-port", "55852"],
                ["--business-protected-shadow-snapshot-0073",
                    "--shadow-target-port", "55852", "--port", "55851"],
                ["--business-protected-shadow-snapshot-0073",
                    "--upgrade-only", "--shadow-target-port", "55851",
                    "--port", "55851"],
                ["--business-protected-shadow-snapshot-0073",
                    "--upgrade-only", "--shadow-target-port", "55852",
                    "--port", "55851", "--tests-only"],
                ["--business-protected-shadow-snapshot-0073",
                    "--upgrade-only", "--shadow-target-port", "55852",
                    "--port", "55851", "--business-protected-cross-cluster-restore-0073"],
        ):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, RUNNER, *args],
                    cwd=ROOT, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"error:", result.stderr)
                after = set(runtime.glob("ai-pg-*")) if runtime.exists() else set()
                self.assertEqual(after, before)

    def test_hook_is_after_seed_before_other_restore_and_explicit(self):
        source = RUNNER.read_text(encoding="utf-8")
        self.assertLess(source.index('("business-v11-login-attestation-upgrade-rehearsal.py",'),
            source.index('if arguments.business_protected_shadow_snapshot_0073:',
                source.index('for script, name in rehearsals:')))
        self.assertLess(source.index('if arguments.business_protected_shadow_snapshot_0073:',
            source.index('for script, name in rehearsals:')),
            source.index('if arguments.business_protected_cross_cluster_restore:',
                source.index('for script, name in rehearsals:')))
        self.assertIn('"--enabled"], timeout=1800, env=django_env)', source)
        self.assertIn('parser.error("Frozen 0073 shadow rejects later AI migration source")',
            source)
        self.assertIn('shutil.disk_usage(ROOT).free < 12 * 1024**3', source)
        self.assertIn('target_probe.bind(("127.0.0.1", arguments.shadow_target_port))',
            source)
        self.assertIn('shadow.get("fileChunkCount") != 7', source)
        self.assertIn('"html", "xlsx"', source)
        self.assertLess(source.index('"protected-ai-shadow-role-preflight-0073.py"'),
            source.index('django_env = {'))
        self.assertIn('role_env = {**environment, "PGDATABASE": "teruisi_ai_rehearsal"}',
            source)
        self.assertIn('if arguments.business_protected_shadow_snapshot_0073:\n'
            '        # Only the explicit shadow needs these closed role names', source)


if __name__ == "__main__":
    unittest.main()
