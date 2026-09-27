"""The shared scheduler scans new promotion reports without starting Agents."""
import json
from unittest.mock import patch

from django import test as djtest

from . import business_promotion_creation as creation, business_promotion_readiness as readiness
from . import models as m, workflows
from . import test_business_promotion_creation as fixtures


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionSchedulerTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body
    current_catalog = fixtures.PromotionCreationTests.current_catalog

    def test_scheduler_publishes_once_then_parks_before_agent_creation(self):
        old = m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id)
        workflows.control(old.id, {"expectedVersion": old.version}, self.admin, "cancel", workflow=True)
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            created = creation.create(self.request_body(mapping=True), self.admin)
        report = m.AiReportRun.objects.get(pk=created["item"]["id"])
        with patch("ai_assistant.provider.turn", side_effect=AssertionError("model dispatch")), patch(
                "ai_assistant.transport.execute_tool", side_effect=AssertionError("tool dispatch")):
            result = workflows.workflow_tick()
            self.assertEqual(result["status"], "screening_published_awaiting_admission")
            self.assertEqual(result["runId"], report.workflow_id)
            self.assertEqual(workflows.workflow_tick()["status"], "idle")
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        self.assertEqual((flow.status, flow.retryable, flow.error_code),
            ("paused", 0, readiness.PARKED_CODE))
        self.assertEqual(m.AiBusinessScreeningRun.objects.filter(
            pk=json.loads(report.snapshot_json)["screeningIntent"]["id"], report=report).count(), 1)
        self.assertFalse(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())
