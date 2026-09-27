"""Pure frozen SQL contract for the closed v10 reader bridge."""
from importlib import import_module
from unittest import TestCase


class BudgetV10ReaderFenceSqlTests(TestCase):
    def test_only_narrow_reader_result_is_granted_and_no_download_route(self):
        migration = import_module(
            "ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant", "0058_business_promotion_budget_v10_publish_gate")])
        self.assertIn("session_user<>'teruisi_ai_reader'", migration.READ)
        self.assertIn("ai_budget_v10_download_fence_mismatch", migration.READ)
        self.assertIn("ai_v4_replay_canonical(body)", migration.READ)
        self.assertIn("business-promotion-budget-v10-download-fence-v1",
            migration.BODY)
        self.assertEqual(migration.READER, "teruisi_ai_reader")
        self.assertEqual(migration.ATTESTOR,
            "teruisi_ai_budget_v10_attestor")
        self.assertNotIn("GRANT SELECT ON " + migration.attestation.TABLE,
            migration.BODY + migration.READ)
