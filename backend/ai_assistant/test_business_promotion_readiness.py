"""Isolated new-profile scan lease; no scheduler wiring or model dispatch."""
from datetime import timedelta
import json
from unittest.mock import patch

from django import test as djtest
from django.utils import timezone

from . import business_promotion_creation as creation, business_promotion_readiness as service
from . import business_diagnostic_screening as screening, models as m, workflows
from . import test_business_promotion_creation as fixtures
from .policy import AiError, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReadinessTests(djtest.TransactionTestCase):
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

    def create_report(self):
        with patch.object(creation.transport, "catalog", side_effect=self.current_catalog):
            result = creation.create(self.request_body(mapping=True), self.admin)
        return m.AiReportRun.objects.select_related("workflow").get(pk=result["item"]["id"])

    def flow(self, report):
        return m.AiWorkflowRuns.objects.get(pk=report.workflow_id)

    def no_dispatch(self):
        self.assertFalse(m.AiAgentJobs.objects.exists())
        self.assertFalse(m.AiAgentProviderDispatches.objects.exists())
        self.assertFalse(m.AiAgentToolDispatches.objects.exists())

    def test_one_scan_publishes_and_parks_without_dispatch_or_repeated_scan(self):
        report = self.create_report()
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            result = service.advance(self.flow(report), self.admin)
            self.assertEqual(result["status"], "screening_published_awaiting_admission")
            saved = m.AiBusinessScreeningRun.objects.get(pk=json.loads(report.snapshot_json)["screeningIntent"]["id"])
            self.assertEqual(result["screeningReference"]["id"], saved.id)
            flow = self.flow(report)
            self.assertEqual((flow.status, flow.retryable, flow.error_code),
                ("paused", 0, service.PARKED_CODE))
            self.assertEqual(flow.attempt_count, 1)
            self.assertEqual(flow.lease_token, "")
            self.assertIsNone(flow.lease_expires_at)
            self.assertEqual(m.AiWorkflowEvents.objects.filter(
                run=flow, event_type="promotion_screening_published_awaiting_admission").count(), 1)
            with patch.object(screening, "prepare_for_report", side_effect=AssertionError("repeat scan")):
                self.assertEqual(service.advance(flow, self.admin)["status"], "not_claimed")
        model.assert_not_called(); remote.assert_not_called(); self.no_dispatch()

    def test_published_then_interrupted_lease_recovers_without_rescan(self):
        report = self.create_report()
        with patch.object(service, "_park", side_effect=KeyboardInterrupt("process exited")):
            with self.assertRaises(KeyboardInterrupt):
                service.advance(self.flow(report), self.admin)
        saved = m.AiBusinessScreeningRun.objects.get(pk=json.loads(report.snapshot_json)["screeningIntent"]["id"])
        self.assertEqual(self.flow(report).status, "queued")
        with mutation(self.admin):
            m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(
                lease_expires_at=timezone.now()-timedelta(seconds=1),
                next_run_at=timezone.now()-timedelta(seconds=1))
        with patch.object(screening, "prepare_for_report", side_effect=AssertionError("cannot rescan")):
            result = service.advance(self.flow(report), self.admin)
        self.assertEqual(result["status"], "screening_published_awaiting_admission")
        self.assertEqual(result["screeningReference"]["id"], saved.id)
        self.assertEqual(self.flow(report).attempt_count, 1)
        self.assertEqual(self.flow(report).status, "paused")
        self.no_dispatch()

    def test_cancellation_during_scan_cannot_publish_or_pause(self):
        report = self.create_report()
        original = screening.prepare_for_report

        def cancel(*args, **kwargs):
            checkpoint = kwargs["checkpoint"]

            def checked(event):
                current = self.flow(report)
                workflows.control(current.id, {"expectedVersion": current.version},
                    self.admin, "cancel", workflow=True)
                checkpoint(event, force=True)

            return original(*args, **{**kwargs, "checkpoint": checked})

        with patch.object(screening, "prepare_for_report", side_effect=cancel):
            result = service.advance(self.flow(report), self.admin)
        self.assertEqual(result["status"], "lease_lost")
        self.assertEqual(self.flow(report).status, "cancelled")
        self.assertFalse(m.AiBusinessScreeningRun.objects.filter(report=report).exists())
        self.no_dispatch()

    def test_old_report_is_not_claimed_as_promotion(self):
        with self.assertRaises(AiError):
            service.claim(m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id), self.admin)
        self.no_dispatch()
