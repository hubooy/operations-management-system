"""Isolated PostgreSQL real-role 0067 target; never runs against production."""
from copy import deepcopy
from importlib import import_module
import json
import os
from unittest.mock import patch

from django import test as djtest
from django.conf import settings
from django.db import DatabaseError, connection, transaction
import psycopg

from . import business_promotion_budget_v11_attest_step as step
from . import business_promotion_budget_v11_preflight as preflight
from . import business_promotion_budget_v11_durable_stage as stage
from . import models as m
from . import test_business_promotion_approved_content as approved_fixture
from . import test_business_promotion_budget_v11_durable_stage as fixture
from .policy import canonical


ROLE = "teruisi_ai_budget_v11_attestor"


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED=True)
class BudgetV11AttestationRoleTests(fixture.BudgetV11DurableStageTests):
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    def test_0067_process_claim_can_be_replaced_but_ready_stays_denied(self):
        """Adversarial 0067 fixture: plain process digests are not a seal.

        This test intentionally does not authorize 0068 publication. It
        demonstrates the exact trust gap that a future independent verifier
        receipt must close before any ready transition can be added.
        """
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            prepared = preflight.prepare(row.id, self.admin, enabled=True)
        forged = json.loads(prepared["attestationText"])
        forged["fileByteVerificationDigest"] = "1" * 64
        forged["htmlRowsDigest"] = "2" * 64
        forged["xlsxOpcFormulaDigest"] = "3" * 64
        forged["owningVerificationDigest"] = "4" * 64
        with self._database() as db:
            db.execute("SET SESSION AUTHORIZATION " + ROLE)
            try:
                forged_receipt = db.execute(
                    "SELECT public.ai_budget_v11_attest_staged(%s,%s,%s)",
                    [row.id, row.attempt, canonical(forged)]).fetchone()[0]
            finally:
                db.execute("RESET SESSION AUTHORIZATION")
        receipt = m.AiBusinessPromotionBudgetV11Attestation.objects.get(
            run_id=row.id, attempt=row.attempt)
        self.assertEqual(receipt.id, forged_receipt)
        self.assertEqual(receipt.file_byte_verification_digest, "1" * 64)
        self.assertEqual(receipt.xlsx_opc_formula_digest, "3" * 64)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(
                status="ready", error_code="", version=row.version + 1,
                progress_json='{"stage":"ready"}')
        row.refresh_from_db()
        self.assertEqual((row.status, row.error_code),
            ("paused", "renderer_unpublished"))

    @staticmethod
    def _database():
        value = settings.DATABASES["default"]
        if (settings.DJANGO_ENVIRONMENT != "test" or
                value["HOST"] != "127.0.0.1" or
                not 55440 <= int(value["PORT"]) <= 55999 or
                str(value["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT") or
                value["NAME"] != "test_teruisi_ai_rehearsal"):
            raise AssertionError("0067 role probe requires isolated PostgreSQL")
        return psycopg.connect(host=value["HOST"], port=value["PORT"],
            dbname=value["NAME"], user=value["USER"],
            password=value["PASSWORD"], autocommit=True)

    def test_0067_owning_bytes_and_independent_role_append_only(self):
        report = self._complete_budget_report()
        self.complete_flow(report)
        row = self._stage(report)
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            prepared = preflight.prepare(row.id, self.admin, enabled=True)
        self.assertFalse(prepared["readyAuthorized"])
        body = json.loads(prepared["attestationText"])
        self.assertEqual(body["runVersion"], row.version)
        self.assertEqual(body["slimProofDigest"], json.loads(
            b"".join(bytes(part.content) for part in
                m.AiBusinessVolumeChunk.objects.filter(run=row,
                    volume_index=0, format="json").order_by("sequence")))[
                "promotionSlimProof"]["proofDigest"])
        with connection.cursor() as cursor:
            import_module(
                "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation"
            ).verify_catalog(cursor)
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                "has_function_privilege(%s,%s,'EXECUTE')",
                [ROLE, "public.ai_budget_v11_attest_staged(text,integer,text)",
                 "teruisi_ai_writer",
                 "public.ai_budget_v11_attest_staged(text,integer,text)"])
            self.assertEqual(cursor.fetchone(), (True, False))
        with self._database() as db:
            with self.assertRaises(psycopg.Error):
                db.execute("SELECT public.ai_budget_v11_attest_staged(%s,%s,%s)",
                    [row.id, row.attempt, prepared["attestationText"]])
            db.execute("SET SESSION AUTHORIZATION " + ROLE)
            try:
                with patch.object(stage.approved_content.runtime.transport,
                        "catalog", side_effect=self.current_catalog):
                    result = step.attest_staged(db, row.id, self.admin,
                        enabled=True, expected_preflight=prepared)
                self.assertEqual(result["status"], "staged_attested_unpublished",
                    result)
                receipt = m.AiBusinessPromotionBudgetV11Attestation.objects.get(
                    run_id=row.id, attempt=row.attempt)
                self.assertEqual(receipt.id, result["attestationId"])
                self.assertEqual(receipt.xlsx_opc_formula_digest,
                    body["xlsxOpcFormulaDigest"])
                forged = deepcopy(body)
                forged["slimProofDigest"] = "0" * 64
                with self.assertRaises(psycopg.Error):
                    db.execute("SELECT public.ai_budget_v11_attest_staged(%s,%s,%s)",
                        [row.id, row.attempt, canonical(forged)])
                self.assertEqual(db.execute("SELECT "
                    "public.ai_budget_v11_attest_staged(%s,%s,%s)",
                    [row.id, row.attempt, prepared["attestationText"]]
                    ).fetchone()[0], receipt.id)
            finally:
                db.execute("RESET SESSION AUTHORIZATION")
        row.refresh_from_db()
        self.assertEqual((row.status, row.error_code),
            ("paused", "renderer_unpublished"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessPromotionBudgetV11Attestation.objects.filter(
                pk=receipt.id).update(slim_proof_digest="f" * 64)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(status="ready",
                error_code="", version=row.version + 1,
                progress_json='{"stage":"ready"}')
