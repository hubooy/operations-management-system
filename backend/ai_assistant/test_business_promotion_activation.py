"""Versioned activation stays closed even with complete paused capacity."""
from unittest.mock import patch

from django import test as djtest
from django.db import transaction

from . import business_promotion_activation as service
from . import business_promotion_preflight as preflight
from . import business_promotion_readiness as readiness
from . import models as m
from . import test_business_promotion_resume_pipeline as fixtures
from .policy import AiError, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionActivationTests(djtest.TransactionTestCase):
    user = fixtures.PromotionResumePipelineTests.user
    call = fixtures.PromotionResumePipelineTests.call
    collect_body = fixtures.PromotionResumePipelineTests.collect_body
    bundle = fixtures.PromotionResumePipelineTests.bundle
    input_for = fixtures.PromotionResumePipelineTests.input_for
    insert = fixtures.PromotionResumePipelineTests.insert
    seed = fixtures.PromotionResumePipelineTests.seed
    setUp = fixtures.PromotionResumePipelineTests.setUp
    request_body = fixtures.PromotionResumePipelineTests.request_body
    current_catalog = fixtures.PromotionResumePipelineTests.current_catalog
    create_published = fixtures.PromotionResumePipelineTests.create_published

    def parked(self):
        report = self.create_published()
        result = readiness.advance(report.workflow, self.admin)
        self.assertEqual(result["status"], "screening_published_awaiting_admission")
        with patch.object(preflight.transport, "catalog", side_effect=self.current_catalog):
            return report, service.prepare(report.id, self.admin)

    def test_complete_paused_capacity_does_not_resume_or_call_fake_edges(self):
        report, prepared = self.parked()
        before = (m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(),
            m.AiAgentProviderDispatches.objects.count(), m.AiAgentToolDispatches.objects.count())
        with patch("ai_assistant.provider.turn", side_effect=AssertionError("paid provider")) as provider, patch(
                "ai_assistant.transport.execute_tool", side_effect=AssertionError("external tool")) as edge:
            value = service.inspect(prepared, self.admin)
            self.assertTrue(value["mandatoryCapacityVerified"])
            self.assertFalse(value["runtimeAdmissionGranted"])
            self.assertFalse(value["initialProviderCallAllowed"])
            self.assertEqual(value["missingHooks"], list(service.REQUIRED_HOOKS))
            with self.assertRaises(AiError) as error:
                service.activate(prepared, self.admin)
            self.assertEqual(error.exception.code, "promotion_activation_incomplete")
        self.assertEqual(before, (m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(),
            m.AiAgentProviderDispatches.objects.count(), m.AiAgentToolDispatches.objects.count()))
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        self.assertEqual(flow.status, "paused")
        self.assertEqual(flow.error_code, readiness.PARKED_CODE)
        provider.assert_not_called(); edge.assert_not_called()

    def test_version_actor_and_transaction_preparation_are_rejected(self):
        report, prepared = self.parked()
        with self.assertRaises(AiError): service.inspect(prepared, self.viewer)
        with transaction.atomic(), self.assertRaises(AiError):
            service.prepare(report.id, self.admin)
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=flow.id).update(version=flow.version + 1)
        with self.assertRaises(AiError): service.inspect(prepared, self.admin)
