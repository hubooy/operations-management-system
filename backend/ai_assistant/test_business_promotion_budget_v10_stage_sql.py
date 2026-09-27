"""Pure frozen PostgreSQL text contract for renderer-10 stage-only lane."""
from importlib import import_module
from unittest import TestCase


class PromotionBudgetV10SqlContractTests(TestCase):
    def test_new_lane_preserves_old_function_names_and_denies_ready(self):
        sql = import_module(
            "ai_assistant.migrations.0054_business_promotion_budget_file_staging")
        self.assertEqual(len(sql.NEW_SQL), 5)
        self.assertEqual(sql.CHUNK_GUARD, sql.OLD_SQL[0])
        self.assertIn("parent_renderer NOT IN (4,6,7,9,10)", sql.MANIFEST_GUARD)
        self.assertIn("NEW.renderer_version IN (4,5,6,7,9,10)", sql.RUN_GUARD)
        self.assertIn("ai_promotion_budget_renderer_unpublished", sql.RUN_GUARD)
        self.assertIn("ai_promotion_budget_renderer_unpublished", sql.COMPLETE_GUARD)
        self.assertIn("ai_business_promotion_trial_ready_requirements(parent.id)",
            sql.COMPLETE_GUARD)
        self.assertNotIn("SECURITY DEFINER", sql.BUDGET_PARENT_REQUIREMENTS)
        for old, new in zip(sql.OLD_SQL, sql.NEW_SQL):
            self.assertEqual(old.split("FUNCTION ", 1)[1].split("(", 1)[0],
                new.split("FUNCTION ", 1)[1].split("(", 1)[0])
