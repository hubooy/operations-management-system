"""Pure frozen-text contract for the dormant, role-only v10 publish lane."""
from importlib import import_module
from unittest import TestCase


class BudgetV10PublishGateSqlTests(TestCase):
    def test_only_version_ten_ready_denial_is_replaced(self):
        migration = import_module(
            "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant", "0057_business_promotion_budget_v10_attestation")])
        self.assertEqual(migration.NEW_SQL[:3], migration.OLD_SQL[:3])
        for old, new in zip(migration.OLD_SQL, migration.NEW_SQL):
            self.assertEqual(old.split("FUNCTION ", 1)[1].split("(", 1)[0],
                new.split("FUNCTION ", 1)[1].split("(", 1)[0])
        self.assertIn("ai_budget_v10_ready_direct_write_denied",
            migration.RUN_GUARD)
        self.assertIn("session_user<>'teruisi_ai_budget_v10_attestor'",
            migration.RUN_GUARD)
        self.assertIn("ai_business_promotion_trial_ready_requirements(parent.id)",
            migration.COMPLETE_GUARD)
        self.assertIn("ai_budget_v10_ready_requirements", migration.COMPLETE_GUARD)
        self.assertNotIn("SECURITY DEFINER", migration.RUN_GUARD)
        self.assertIn("RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER",
            migration.COMPLETE_GUARD)
        self.assertIn("PERFORM public.ai_business_promotion_trial_ready_requirements(parent.id)",
            migration.COMPLETE_GUARD)
        self.assertNotIn("SECURITY DEFINER", migration.OLD_SQL[4])
        for definition in (migration.READY_REQUIREMENTS,
                migration.PUBLISH, migration.OUTCOME):
            self.assertIn("SECURITY DEFINER", definition)
            self.assertIn("session_user<>'teruisi_ai_budget_v10_attestor'",
                definition)
            self.assertIn("business-budget-v10-publish-request-v1", definition)
            self.assertIn("ai_v4_replay_canonical(request_body)", definition)
            self.assertIn("fullManifestSha256", definition)
        self.assertNotIn("CREATE ROLE", migration.PUBLISH)
