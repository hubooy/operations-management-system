"""Promotion capacity object remains separate from runtime dispatch authority."""
from unittest.mock import patch
import unittest

from django import test as djtest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from . import business_promotion_runtime_permission as service
from . import business_promotion_preflight as preflight
from . import models as m
from . import test_business_promotion_preflight as fixtures
from .policy import AiError, mutation


class PromotionPermissionPureTests(unittest.TestCase):
    def test_json_and_untrusted_constructor_cannot_grant_permission(self):
        for value in ({}, "{}", object()):
            with self.assertRaises(AiError): service.check(value, None)
        fake = preflight.PreparedCapacity(preflight._TOKEN, "report-1", "owner@example.invalid",
            {}, {"reportId": "report-1", "ownerEmail": "owner@example.invalid",
                "capacityVerified": True, "requiredContentOnly": True,
                "runtimeAdmissionGranted": False})
        with self.assertRaises(AiError): service.PreparedPermission(None, fake,
            "report-1", "owner@example.invalid", "promotion")
        local = service.PreparedPermission(service._TOKEN, fake,
            "report-1", "owner@example.invalid", "promotion")
        object.__setattr__(local, "_role", "commerce")
        with self.assertRaises(AiError): _ = local.proof


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionPermissionPersistedTests(djtest.TransactionTestCase):
    user = fixtures.PromotionPreflightPersistedTests.user
    call = fixtures.PromotionPreflightPersistedTests.call
    collect_body = fixtures.PromotionPreflightPersistedTests.collect_body
    bundle = fixtures.PromotionPreflightPersistedTests.bundle
    input_for = fixtures.PromotionPreflightPersistedTests.input_for
    insert = fixtures.PromotionPreflightPersistedTests.insert
    seed = fixtures.PromotionPreflightPersistedTests.seed
    setUp = fixtures.PromotionPreflightPersistedTests.setUp
    request_body = fixtures.PromotionPreflightPersistedTests.request_body
    current_catalog = fixtures.PromotionPreflightPersistedTests.current_catalog
    create_published = fixtures.PromotionPreflightPersistedTests.create_published

    def prepare(self, report, role="promotion"):
        with patch.object(preflight.transport, "catalog", side_effect=self.current_catalog):
            return service.prepare(report.id, self.admin, role)

    def test_actual_published_report_gets_closed_local_capacity_object(self):
        report = self.create_published()
        with patch("ai_assistant.provider.turn", side_effect=AssertionError("paid call")) as provider, patch(
                "ai_assistant.transport.execute_tool", side_effect=AssertionError("tool dispatch")) as tool:
            prepared = self.prepare(report)
            with patch.object(preflight.transport, "catalog", side_effect=AssertionError("network in mutation")), patch.object(
                    preflight.packages, "prepare", side_effect=AssertionError("package read in mutation")), CaptureQueriesContext(connection) as queries:
                with transaction.atomic():
                    proof = service.check(prepared, self.admin)
            self.assertTrue(proof["capacityVerified"])
            self.assertFalse(proof["runtimeAdmissionGranted"])
            self.assertFalse(proof["initialProviderCallAllowed"])
            self.assertEqual(proof["role"], "promotion")
            self.assertEqual(proof["reason"], "requires_explicit_resume_and_per_microstep_dispatch_ledger")
            provider.assert_not_called(); tool.assert_not_called()
            for query in queries:
                sql = query["sql"].lower()
                self.assertNotIn("ai_business_evidence_facts", sql)
                self.assertNotIn("ai_business_screening_page", sql)

    def test_paused_role_and_model_changes_revoke_local_object(self):
        report = self.create_published()
        prepared = self.prepare(report)
        with self.assertRaises(AiError): service.check(prepared, self.viewer)
        with self.assertRaises(AiError): service.prepare(report.id, self.admin, "unknown")
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(status="paused")
        with self.assertRaises(AiError): service.check(prepared, self.admin)
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(status="queued")
            m.AiModels.objects.filter(pk=report.workflow.model_id).update(
                version=report.workflow.model_version + 1)
        with self.assertRaises(AiError): service.check(prepared, self.admin)
