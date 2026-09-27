"""Static 0067 append-only/no-publication contract checks."""
from importlib import import_module
from unittest import TestCase


class BudgetV11AttestationSqlStaticTests(TestCase):
    def test_exact_predecessor_and_unmodified_0066_writer_guard(self):
        migration = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant", "0066_business_promotion_budget_v11_durable_stage")])
        self.assertIn("session_user<>'teruisi_ai_writer'",
            import_module("ai_assistant.business_promotion_budget_v11_stage_sql").
            STAGE_REQUIREMENTS)
        self.assertIn("session_user<>'teruisi_ai_budget_v11_attestor'",
            migration.REQUIREMENTS)
        self.assertTrue(any("NOLOGIN NOINHERIT" in value for value in
            migration.install.__code__.co_consts if isinstance(value, str)))

    def test_sidecar_does_not_publish_and_is_append_only(self):
        migration = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        self.assertIn("ai_budget_v11_attestation_immutable", migration.GUARD)
        self.assertIn("ai_budget_v11_staged_root_invalid", migration.ATTEST)
        self.assertIn("ai_budget_v11_attestation_requirements", migration.ATTEST)
        self.assertNotIn("UPDATE public.ai_business_file_runs", migration.ATTEST)
        self.assertNotIn("ai_budget_v11_ready", migration.ATTEST)
