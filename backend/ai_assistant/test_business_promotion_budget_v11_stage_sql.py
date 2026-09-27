"""Static 0066 candidate checks; isolated PostgreSQL tests run separately."""
import inspect
from importlib import import_module
from unittest import TestCase

from . import business_promotion_budget_v11_stage_sql as candidate


class BudgetV11StageSqlStaticTests(TestCase):
    def test_0066_depends_on_exact_market_0065(self):
        migration = import_module(
            "ai_assistant.migrations.0066_business_promotion_budget_v11_durable_stage")
        self.assertEqual(migration.Migration.dependencies,
            [("ai_assistant", "0065_business_market_v2_model_cost_reservation")])
        self.assertEqual(len(migration.Migration.operations), 1)

    def test_changes_only_volume_run_and_complete_guards(self):
        self.assertEqual(candidate.NEW_SQL[0], candidate.OLD_SQL[0])
        self.assertEqual(len(candidate.NEW_SQL), 5)
        self.assertEqual(candidate.OLD_VERSIONS,
            "1,2,3,4,5,6,7,9,10")
        self.assertEqual(candidate.NEW_VERSIONS,
            "1,2,3,4,5,6,7,9,10,11")
        self.assertIn("parent.renderer_version NOT IN (4,6,7,9,10,11)",
            candidate.NEW_SQL[1])
        self.assertIn("parent_renderer NOT IN (4,6,7,9,10,11)",
            candidate.NEW_SQL[2])
        self.assertIn("ai_business_volume_uint(value->'rendererVersion',4,11)",
            candidate.NEW_SQL[2])
        self.assertIn("NEW.renderer_version IN (4,5,6,7,9,10,11)",
            candidate.NEW_SQL[3])
        self.assertIn("OLD.renderer_version IN (10,11) OR NEW.renderer_version IN (10,11)",
            candidate.NEW_SQL[3])

    def test_v11_stage_is_narrow_and_ready_remains_unconditionally_denied(self):
        self.assertIn("SECURITY DEFINER", candidate.STAGE_REQUIREMENTS)
        self.assertIn("session_user<>'teruisi_ai_writer'", candidate.STAGE_REQUIREMENTS)
        self.assertIn("ai_business_promotion_budget_parent_requirements",
            candidate.STAGE_REQUIREMENTS)
        self.assertIn("'business-promotion-budget-v11-slim-proof-v1'",
            candidate.STAGE_REQUIREMENTS)
        self.assertIn("'staged_unpublished'", candidate.NEW_SQL[3])
        self.assertIn("PERFORM public.ai_budget_v11_stage_requirements(",
            candidate.NEW_SQL[3])
        self.assertIn("IF NEW.renderer_version=11 THEN\n                RAISE EXCEPTION 'ai_budget_v11_ready_unpublished'",
            candidate.NEW_SQL[3])
        self.assertIn("IF parent.renderer_version=11 THEN\n              RAISE EXCEPTION 'ai_budget_v11_ready_unpublished'",
            candidate.NEW_SQL[4])
        self.assertIn("PERFORM public.ai_budget_v11_stage_requirements(",
            candidate.NEW_SQL[4])
        self.assertNotIn("teruisi_ai_budget_v10_attestor", candidate.STAGE_REQUIREMENTS)
        self.assertFalse(hasattr(candidate, "Migration"))

    def test_acl_is_writer_only_and_reverse_refuses_any_v11_row(self):
        install = inspect.getsource(candidate.install)
        reverse = inspect.getsource(candidate.uninstall)
        self.assertIn('"REVOKE ALL ON FUNCTION "', install)
        self.assertIn('" TO teruisi_ai_writer;', install)
        self.assertNotIn("TO teruisi_ai_reader", install)
        self.assertIn("renderer_version=11", reverse)
        self.assertIn("DROP FUNCTION", reverse)
