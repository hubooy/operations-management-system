"""Pure contract: 0057 records a closed candidate, never publishes a file."""
from importlib import import_module
from unittest import TestCase


class BudgetV10AttestationSqlTests(TestCase):
    def test_role_append_only_and_ready_stays_in_0054_denial(self):
        migration = import_module(
            "ai_assistant.migrations.0057_business_promotion_budget_v10_attestation")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant", "0056_business_market_v2_material_role_bridge")])
        self.assertTrue(any(type(value) is str and
            "NOLOGIN NOINHERIT NOSUPERUSER" in value
            for value in migration.install.__code__.co_consts))
        self.assertIn("session_user<>'teruisi_ai_budget_v10_attestor'",
            migration.ATTEST)
        self.assertIn("ai_budget_v10_attestation_immutable", migration.GUARD)
        self.assertIn("ON CONFLICT (run_id,attempt) DO NOTHING",
            migration.ATTEST)
        self.assertIn("ai_business_volume_manifest_check", migration.ATTEST)
        self.assertIn("owningVerificationDigest", migration.ATTEST)
        self.assertNotIn("UPDATE public.ai_business_file_runs", migration.ATTEST)
        self.assertNotIn("status='ready'", migration.ATTEST)
