"""Offline checks for the disposable v11 three-identity SQL prototype."""
from importlib import import_module
from inspect import getsource
from unittest import TestCase

from . import business_promotion_budget_v11_identity_candidate_sql as candidate


class BudgetV11IdentityCandidateStaticTests(TestCase):
    def test_v2_is_additive_and_closed(self):
        att = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        verify = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        migration = import_module(
            "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")
        v2 = candidate.verify_sql()
        self.assertIn("ai_budget_v11_verify_protected_receipt_v2(", v2)
        self.assertIn("session_user<>'teruisi_ai_budget_v11_publish_login'", v2)
        self.assertIn("NOLOGIN NOINHERIT", getsource(migration._role))
        self.assertEqual(migration.Migration.dependencies,[("ai_assistant",
            "0069_business_market_v2_paid_round_rehearsal")])
        self.assertIn("session_user<>'teruisi_ai_budget_v11_attestor'", att.ATTEST)
        self.assertIn("session_user<>'teruisi_ai_budget_v11_publisher'",
            verify.VERIFY)
        self.assertNotIn("UPDATE public.ai_business_file_runs", v2)
        self.assertIn("readyAuthorized',false", candidate.READ_SQL)
        self.assertIn("ON CONFLICT (ticket_id) DO NOTHING", candidate.READ_SQL)
        self.assertIn("IF TG_OP<>'INSERT'", candidate.ROW_GUARD)
        self.assertIn("to_regprocedure(%s)::oid",
            getsource(migration.verify_catalog))

    def test_migration_adds_no_credentials_web_route_or_ready(self):
        migration = import_module(
            "ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity")
        self.assertEqual(set(candidate.ROLES), {candidate.ATTEST,candidate.SIGN,
            candidate.PUBLISH})
        self.assertNotIn("PASSWORD", candidate.ISSUE_SQL + candidate.READ_SQL +
            candidate.verify_sql())
        self.assertNotIn("LOGIN PASSWORD", getsource(migration.install))
        self.assertNotIn("DROP ROLE", getsource(migration.uninstall))
        self.assertNotIn("UPDATE public.ai_business_file_runs",
            getsource(migration.install)+candidate.READ_SQL+candidate.ISSUE_SQL)

    def test_full_owning_preflight_still_uses_unrestricted_orm_and_is_blocked(self):
        from pathlib import Path
        source = (Path(__file__).parent /
            "business_promotion_budget_v11_preflight.py").read_text(
            encoding="utf-8")
        signer = (Path(__file__).parents[1] / "business_analysis" /
            "promotion_budget_verifier_receipt_v11.py").read_text(
            encoding="utf-8")
        self.assertIn("files.get(identifier(run_id), principal)", source)
        self.assertIn("AiBusinessPromotionBudgetV11Attestation.objects.get", signer)
        self.assertNotIn("read_proof_ticket_v2", source + signer)

    def test_health_checks_private_verifier_and_ticket_catalogues(self):
        from pathlib import Path
        health = (Path(__file__).parent / "health.py").read_text(
            encoding="utf-8")
        self.assertIn(
            'migrations.0068_business_promotion_budget_v11_verifier_receipt").verify_catalog(cursor)',
            health)
        self.assertIn(
            'migrations.0070_business_promotion_budget_v11_limited_identity").verify_catalog(cursor)',
            health)
