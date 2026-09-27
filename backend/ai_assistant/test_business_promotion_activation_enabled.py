"""Flagged internal activation starts three distinct jobs atomically."""
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_activation as service
from . import business_promotion_preflight as preflight
from . import models as m, workflows
from . import test_business_promotion_activation as fixtures
from .policy import AiError, canonical, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_AGENT_RUNTIME_ENABLED=True)
class PromotionEnabledActivationTests(djtest.TransactionTestCase):
    user = fixtures.PromotionActivationTests.user
    call = fixtures.PromotionActivationTests.call
    collect_body = fixtures.PromotionActivationTests.collect_body
    bundle = fixtures.PromotionActivationTests.bundle
    input_for = fixtures.PromotionActivationTests.input_for
    insert = fixtures.PromotionActivationTests.insert
    seed = fixtures.PromotionActivationTests.seed
    setUp = fixtures.PromotionActivationTests.setUp
    request_body = fixtures.PromotionActivationTests.request_body
    current_catalog = fixtures.PromotionActivationTests.current_catalog
    create_published = fixtures.PromotionActivationTests.create_published
    parked = fixtures.PromotionActivationTests.parked

    def retire_fixture_flow(self):
        if hasattr(self, "report"):
            old = m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id)
            workflows.control(old.id, {"expectedVersion": old.version},
                self.admin, "cancel", workflow=True)

    def complete_queued_children(self, run_id):
        with mutation(self.admin):
            for job in m.AiAgentJobs.objects.filter(workflow_run_id=run_id,
                    status="queued"):
                job.status = "completed"
                job.phase = "completed"
                job.output_json = canonical({"answer": "合成已完成"})
                job.completed_at = timezone.now()
                job.save()

    def test_activation_and_scheduler_create_three_plus_one_plus_one(self):
        report, prepared = self.parked()
        self.retire_fixture_flow()
        with (patch("ai_assistant.provider.turn", side_effect=AssertionError("provider")) as provider,
              patch("ai_assistant.transport.execute_tool", side_effect=AssertionError("edge")) as edge):
            activated = service.activate(prepared, self.admin)
            self.assertEqual(activated["status"], "running")
            self.assertTrue(activated["runtimeAdmissionGranted"])
            self.assertFalse(activated["modelDispatched"])
            self.assertEqual(m.AiAgentJobs.objects.filter(
                workflow_run_id=report.workflow_id).count(), 3)
            for count in (4, 5):
                self.complete_queued_children(report.workflow_id)
                with patch.object(preflight.transport, "catalog",
                        side_effect=self.current_catalog):
                    result = workflows.workflow_tick()
                self.assertEqual(result["status"], "running")
                self.assertEqual(m.AiAgentJobs.objects.filter(
                    workflow_run_id=report.workflow_id).count(), count)
            self.complete_queued_children(report.workflow_id)
            with patch.object(preflight.transport, "catalog",
                    side_effect=self.current_catalog):
                result = workflows.workflow_tick()
            self.assertEqual(result["status"], "waiting_review")
        provider.assert_not_called(); edge.assert_not_called()
        self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(
            run_id=report.workflow_id, node_type="agent", status="completed").count(), 5)
        self.assertEqual(m.AiWorkflowNodeRuns.objects.get(
            run_id=report.workflow_id, node_key="human_review").status, "waiting_review")

    def test_failed_first_child_creation_rolls_back_paused_cas(self):
        report, prepared = self.parked()
        with (patch("ai_assistant.business_parallel.workflow_step",
                side_effect=AiError("synthetic child creation failed", "conflict", 409)),
              self.assertRaises(AiError)):
            service.activate(prepared, self.admin)
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        self.assertEqual(flow.status, "paused")
        self.assertEqual(flow.error_code, "promotion_admission_not_registered")
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
