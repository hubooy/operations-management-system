"""Isolated PostgreSQL target; the main task runs PG suites serially."""
from importlib import import_module
import json
import secrets
from unittest.mock import patch

from django.db import connection
import psycopg

from business_analysis import promotion_budget_verifier_receipt_v11 as receipt
from . import business_promotion_budget_v11_attest_step as attest_step
from . import business_promotion_budget_v11_durable_stage as stage
from . import test_business_promotion_approved_content as approved_fixture
from . import test_business_promotion_budget_v11_attestation_role as fixture


MIGRATION = import_module(
    "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")


class BudgetV11ProtectedReceiptRoleTests(fixture.BudgetV11AttestationRoleTests):
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    def test_0068_catalog_rejects_key_object_and_acl_drift(self):
        # Isolated PG target only; no business row, key or production mutation.
        with self._database() as db:
            db.execute("BEGIN")
            try:
                with db.cursor() as cursor:
                    MIGRATION.verify_catalog(cursor)
                db.execute("SAVEPOINT guard_drift")
                db.execute("ALTER TABLE " + MIGRATION.KEY_TABLE +
                    " DISABLE TRIGGER ai_budget_v11_key_guard")
                with db.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                db.execute("ROLLBACK TO SAVEPOINT guard_drift")
                db.execute("SAVEPOINT index_drift")
                db.execute("DROP INDEX public.ai_budget_v11_one_active_key")
                with db.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                db.execute("ROLLBACK TO SAVEPOINT index_drift")
                db.execute("SAVEPOINT check_drift")
                db.execute("ALTER TABLE " + MIGRATION.KEY_TABLE +
                    " DROP CONSTRAINT " +
                    "protected_business_budget_v11_verifier_keys_secret_check")
                with db.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                db.execute("ROLLBACK TO SAVEPOINT check_drift")
                db.execute("SAVEPOINT unknown_grant")
                db.execute("CREATE ROLE teruisi_ai_budget_v11_catalog_probe NOLOGIN")
                db.execute("GRANT SELECT ON " + MIGRATION.KEY_TABLE +
                    " TO teruisi_ai_budget_v11_catalog_probe")
                with db.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
                db.execute("ROLLBACK TO SAVEPOINT unknown_grant")
                db.execute("SAVEPOINT unknown_function_grant")
                db.execute("CREATE ROLE teruisi_ai_budget_v11_catalog_probe NOLOGIN")
                db.execute("GRANT EXECUTE ON FUNCTION " + MIGRATION.MAC_SIGNATURE +
                    " TO teruisi_ai_budget_v11_catalog_probe")
                with db.cursor() as cursor:
                    with self.assertRaises(RuntimeError):
                        MIGRATION.verify_catalog(cursor)
            finally:
                db.execute("ROLLBACK")

    def test_0068_synthetic_key_only_verifies_fresh_protected_receipt(self):
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        with self._database() as db:
            db.execute("SET SESSION AUTHORIZATION teruisi_ai_budget_v11_attestor")
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog", side_effect=self.current_catalog):
                    result = attest_step.attest_staged(db, row.id, self.admin,
                        enabled=True)
                self.assertEqual(result["status"], "staged_attested_unpublished")
            finally:
                db.execute("RESET SESSION AUTHORIZATION")
        synthetic_key = secrets.token_bytes(32)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            signed = receipt.sign_after_preflight(row.id, self.admin,
                enabled=True, key_id="synthetic-v11-key-1", secret=synthetic_key)
        self.assertFalse(signed["readyAuthorized"])
        body = json.loads(signed["receiptText"])
        self.assertEqual(body["attestationId"], result["attestationId"])
        self.assertEqual(body["attestation"]["runId"], row.id)
        with connection.cursor() as cursor:
            MIGRATION.verify_catalog(cursor)
            cursor.execute("SELECT count(*) FROM " + MIGRATION.KEY_TABLE)
            self.assertEqual(cursor.fetchone(), (0,))
        with self._database() as db:
            db.execute("SET SESSION AUTHORIZATION " + MIGRATION.PUBLISHER)
            try:
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT " + MIGRATION.VERIFY_SIGNATURE.split("(")[0] +
                        "(%s,%s,%s,%s)", [row.id,row.attempt,
                        signed["receiptText"],signed["receiptMac"]])
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT secret FROM " + MIGRATION.KEY_TABLE)
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_private_mac_valid("
                        "%s,%s,%s)", ["synthetic-v11-key-1",
                        signed["receiptText"], signed["receiptMac"]])
            finally:
                db.execute("RESET SESSION AUTHORIZATION")
        with self._database() as db:
            db.execute("BEGIN")
            try:
                db.execute("SET SESSION AUTHORIZATION " + MIGRATION.KEY_OWNER)
                db.execute("INSERT INTO " + MIGRATION.KEY_TABLE +
                    " (key_id,secret,status,created_at) "
                    "VALUES (%s,%s,'active',clock_timestamp())",
                    ["synthetic-v11-key-1", synthetic_key])
                db.execute("RESET SESSION AUTHORIZATION")
                db.execute("SET SESSION AUTHORIZATION " + MIGRATION.PUBLISHER)
                query = "SELECT public.ai_budget_v11_verify_protected_receipt(%s,%s,%s,%s)"
                self.assertEqual(db.execute(query, [row.id,row.attempt,
                    signed["receiptText"],signed["receiptMac"]]).fetchone(),
                    (True,))
                forged = signed["receiptText"].replace(
                    body["attestation"]["xlsxOpcFormulaDigest"], "0" * 64, 1)
                db.execute("SAVEPOINT forged_receipt")
                with self.assertRaises(psycopg.Error):
                    db.execute(query,[row.id,row.attempt,forged,signed["receiptMac"]])
                db.execute("ROLLBACK TO SAVEPOINT forged_receipt")
                db.execute("SAVEPOINT forged_mac")
                with self.assertRaises(psycopg.Error):
                    db.execute(query,[row.id,row.attempt,signed["receiptText"],
                        "0" * 64])
                db.execute("ROLLBACK TO SAVEPOINT forged_mac")
                db.execute("RESET SESSION AUTHORIZATION")
                db.execute("SET SESSION AUTHORIZATION " + MIGRATION.KEY_OWNER)
                db.execute("UPDATE " + MIGRATION.KEY_TABLE +
                    " SET status='revoked',revoked_at=clock_timestamp() "
                    "WHERE key_id=%s", ["synthetic-v11-key-1"])
                db.execute("RESET SESSION AUTHORIZATION")
                db.execute("SET SESSION AUTHORIZATION " + MIGRATION.PUBLISHER)
                db.execute("SAVEPOINT revoked_key")
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_verify_protected_receipt("
                        "%s,%s,%s,%s)", [row.id,row.attempt,
                        signed["receiptText"],signed["receiptMac"]])
                db.execute("ROLLBACK TO SAVEPOINT revoked_key")
            finally:
                # The synthetic key never commits and never enters a backup.
                db.execute("ROLLBACK")
        row.refresh_from_db()
        self.assertEqual((row.status,row.error_code),
            ("paused","renderer_unpublished"))
