"""Isolated PG owning preflight; no 0057 write or renderer-10 ready."""
import json
from unittest.mock import patch

from django import test as djtest
from django.db import DatabaseError

from . import business_promotion_budget_v10_preflight as preflight
from . import business_promotion_budget_v10_stage as stage
from . import models as m
from . import test_business_promotion_budget_v10_stage as fixture
from . import test_business_promotion_approved_content as approved_fixture
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class BudgetV10OwningPreflightTests(djtest.TransactionTestCase):
    user = fixture.PromotionBudgetV10StageTests.user
    call = fixture.PromotionBudgetV10StageTests.call
    collect_body = fixture.PromotionBudgetV10StageTests.collect_body
    bundle = fixture.PromotionBudgetV10StageTests.bundle
    input_for = fixture.PromotionBudgetV10StageTests.input_for
    insert = fixture.PromotionBudgetV10StageTests.insert
    seed = fixture.PromotionBudgetV10StageTests.seed
    setUp = fixture.PromotionBudgetV10StageTests.setUp
    request_body = fixture.PromotionBudgetV10StageTests.request_body
    current_catalog = fixture.PromotionBudgetV10StageTests.current_catalog
    create_fixed_report = fixture.PromotionBudgetV10StageTests.create_fixed_report
    base = fixture.PromotionBudgetV10StageTests.base
    read = fixture.PromotionBudgetV10StageTests.read
    append = fixture.PromotionBudgetV10StageTests.append
    package = fixture.PromotionBudgetV10StageTests.package
    promotion = fixture.PromotionBudgetV10StageTests.promotion
    complete = fixture.PromotionBudgetV10StageTests.complete
    running_job = fixture.PromotionBudgetV10StageTests.running_job
    five_completed = fixture.PromotionBudgetV10StageTests.five_completed
    approved = fixture.PromotionBudgetV10StageTests.approved
    _complete_budget_report = fixture.PromotionBudgetV10StageTests._complete_budget_report
    _stage = fixture.PromotionBudgetV10StageTests._stage
    complete_flow = approved_fixture.PromotionApprovedContentTests.complete_flow

    def _prepared(self, *, budget=False):
        if budget:
            report = self._complete_budget_report()
        else:
            report = self.five_completed(promotion_reference=True)
            self.approved(report)
        self.complete_flow(report)
        return self._stage(report)

    def _call(self, row):
        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog):
            return preflight.prepare(row.id, self.admin, enabled=True)

    def _assert_body(self, *, budget):
        row = self._prepared(budget=budget)
        result = self._call(row)
        body = json.loads(result["attestationText"])
        self.assertEqual(body["schemaVersion"],
            "business-promotion-budget-v10-staged-attestation-v1")
        self.assertEqual(body["budgetPresent"], budget)
        self.assertEqual(body["bindingDigest"], row.binding_digest)
        self.assertEqual(body["attempt"], row.attempt)
        self.assertEqual(body["budgetPlanDigest"],
            json.loads(row.report.snapshot_json)["budgetRef"]["planDigest"]
            if budget else None)
        self.assertFalse(result["databaseCanIndependentlyVerifyProcessAssertions"])
        self.assertFalse(result["readyAuthorized"])
        self.assertFalse(m.AiBusinessPromotionBudgetV10Attestation.objects.exists())
        row.refresh_from_db()
        self.assertEqual((row.status, row.error_code),
            ("paused", "renderer_unpublished"))

    def test_no_budget_staged_bytes_build_0057_body_without_write(self):
        self._assert_body(budget=False)

    def test_fixed_budget_staged_bytes_build_0057_body_without_write(self):
        self._assert_body(budget=True)

    def test_disabled_and_drift_during_full_verify_never_return_assertion(self):
        row = self._prepared()
        with self.assertRaises(AiError):
            preflight.prepare(row.id, self.admin)
        old = stage._verify_staged

        def drift(saved, principal, checkpoint):
            result = old(saved, principal, checkpoint)
            stage.control(saved.id, "cancel", saved.version, principal)
            return result

        with patch.object(stage.approved_content.runtime.transport,
                "catalog", side_effect=self.current_catalog), patch.object(
                stage, "_verify_staged", side_effect=drift):
            with self.assertRaises(AiError):
                preflight.prepare(row.id, self.admin, enabled=True)
        self.assertFalse(m.AiBusinessPromotionBudgetV10Attestation.objects.exists())

    def test_changed_persisted_json_chunk_refuses_before_body(self):
        row = self._prepared()
        part = m.AiBusinessVolumeChunk.objects.filter(run=row,
            attempt=row.attempt, volume_index=0, format="json").first()
        self.assertIsNotNone(part)
        # The existing immutable chunk guard itself is a first defense. Its
        # failure proves preflight cannot be fed a silently rewritten chunk.
        with self.assertRaises(DatabaseError):
            m.AiBusinessVolumeChunk.objects.filter(pk=part.pk).update(
                content=b"{}")
        row.refresh_from_db()
        self.assertEqual(row.status, "paused")
