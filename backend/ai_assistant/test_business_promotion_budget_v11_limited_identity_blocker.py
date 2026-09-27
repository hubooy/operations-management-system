"""Static release blocker until non-superuser full owning preflight exists.

0070 adds narrow SQL read and dormant identities; the application signer still
uses default ORM for the full source/file preflight and direct 0067 proof read.
"""
from importlib import import_module
from inspect import getsource
from pathlib import Path
from unittest import TestCase


class BudgetV11LimitedIdentityBlockerTests(TestCase):
    def test_no_login_roles_cannot_be_claimed_as_deployable_sessions(self):
        att = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        verifier = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        self.assertIn("session_user<>'teruisi_ai_budget_v11_attestor'", att.ATTEST)
        self.assertIn("NOT r.rolcanlogin", att.ATTEST)
        self.assertIn("NOLOGIN NOINHERIT", getsource(att.install))
        self.assertIn("session_user<>'teruisi_ai_budget_v11_publisher'",
            verifier.VERIFY)
        self.assertIn("NOLOGIN NOINHERIT", getsource(verifier._role))
        self.assertIn("rolcanlogin", getsource(verifier._role))

    def test_signer_still_uses_orm_despite_narrow_reader(self):
        signer = import_module(
            "business_analysis.promotion_budget_verifier_receipt_v11")
        source = getsource(signer.sign_after_preflight)
        self.assertIn("AiBusinessPromotionBudgetV11Attestation.objects.get", source)
        self.assertIn("preflight.prepare", source)
        att = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        from . import business_promotion_budget_v11_identity_candidate_sql as v2
        self.assertIn("ai_budget_v11_read_proof_ticket_v2", v2.READ_SQL)
        self.assertNotIn("read_proof_ticket_v2", source)
        self.assertIn('cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")',
            getsource(att.install))

    def test_identity_design_is_not_publication_authority(self):
        from ai_assistant import business_promotion_budget_v11_stage_sql as stage
        self.assertIn("ai_budget_v11_ready_unpublished", stage.RUN_GUARD)
        self.assertIn("ai_budget_v11_ready_unpublished", stage.COMPLETE_GUARD)
        doc = (Path(__file__).resolve().parents[2] / "docs" /
            "AI_BUSINESS_PROMOTION_BUDGET_V11_LIMITED_IDENTITY_DESIGN.md").read_text(
                encoding="utf-8")
        for marker in ("受限生产身份", "一次性受保护任务票据",
                "非超级用户端到端验收", "sign_login", "publish_login",
                "不变"):
            self.assertIn(marker, doc)
