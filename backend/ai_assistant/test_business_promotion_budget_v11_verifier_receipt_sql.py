"""Closed verifier candidate: no key material, signer SQL, or publication path."""
from importlib import import_module
from inspect import getsource
from unittest import TestCase


class BudgetV11ProtectedReceiptSqlStaticTests(TestCase):
    def test_key_is_empty_and_private_and_receipt_only_verifies(self):
        migration = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        self.assertEqual(migration.Migration.dependencies, [
            ("ai_assistant", "0067_business_promotion_budget_v11_attestation")])
        self.assertIn("NOLOGIN NOINHERIT", getsource(migration._role))
        self.assertIn("REVOKE ALL ON", getsource(migration.install))
        self.assertIn("0068 must install with no verifier key",
            getsource(migration.install))
        self.assertIn("status='active'", migration.MAC)
        self.assertIn("sha256(outer_pad||sha256(inner_pad||", migration.MAC)
        self.assertIn("decode('00','hex')||convert_to(receipt_text,'UTF8')",
            migration.MAC)
        self.assertNotIn("chr(0)", migration.MAC)
        self.assertIn("difference:=difference|", migration.MAC)
        self.assertNotIn("secret", migration.VERIFY)
        self.assertNotIn("UPDATE public.ai_business_file_runs", migration.VERIFY)
        self.assertIn("session_user<>'teruisi_ai_budget_v11_publisher'",
            migration.VERIFY)
        self.assertNotIn("pgcrypto", migration.MAC)

    def test_0067_and_staged_ready_denial_are_unchanged(self):
        att = import_module(
            "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
        stage = import_module("ai_assistant.business_promotion_budget_v11_stage_sql")
        migration = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        self.assertNotIn("ALTER FUNCTION " + att.SIGNATURE,
            getsource(migration.install))
        self.assertIn("renderer_unpublished", migration.VERIFY)
        self.assertIn("session_user<>'teruisi_ai_writer'",
            stage.STAGE_REQUIREMENTS)

    def test_catalog_pin_includes_key_lifecycle_objects(self):
        migration = import_module(
            "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
        body = getsource(migration.verify_catalog)
        for marker in ("pg_catalog.pg_constraint", "convalidated",
                "pg_catalog.pg_index", "indisunique", "indisvalid",
                "pg_catalog.acldefault('c',c.relowner)",
                "ai_budget_v11_one_active_key", "ai_budget_v11_key_guard",
                "ai_budget_v11_key_no_truncate", "pg_catalog.pg_trigger",
                "t.tgenabled", "KEY_GUARD.split"):
            self.assertIn(marker, body)
