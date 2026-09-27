"""No database: one focused forward-only fixture is opt-in and isolated."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerFocusContractTests(unittest.TestCase):
    def test_runner_flag_is_explicit_and_exits_before_old_upgrade_chain(self):
        source = (ROOT / "tools/ai-postgres-rehearsal.py").read_text(
            encoding="utf-8")
        self.assertIn('"--business-protected-installer-focus"', source)
        branch = source.index('if arguments.business_protected_installer_focus:\n'
            '        focused = run(')
        self.assertLess(branch, source.index('if arguments.generation_upgrade:',
            branch))
        self.assertIn('sys.exit(0)  # finally stops only this synthetic',
            source[branch:branch + 550])

    def test_fixture_uses_forward_0066_and_current_finance_only(self):
        source = (ROOT / "tools/protected-ai-installer-focus-rehearsal.py"
            ).read_text(encoding="utf-8")
        self.assertIn('("ai_assistant", BASELINE)', source)
        self.assertIn('("access_control", "0001_initial")', source)
        self.assertIn('"public.access_control_users"', source)
        self.assertIn('("finance", "0005_raw_column_evidence_v2")', source)
        self.assertIn('"cloneRestored":True', source)
        self.assertIn('"--audit-unknown"', source)
        self.assertIn('"TERUISI_PROTECTED_INSTALLER_TEST_LOST_REPLY_STEP"',
            source)
        self.assertNotIn('migrate([OLD])', source)
        self.assertNotIn('D:\\teruisi-runtime\\django-sales\\data', source)


if __name__ == "__main__":
    unittest.main()
