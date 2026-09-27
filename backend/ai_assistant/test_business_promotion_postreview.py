"""Actual human decision, then next new-profile workflow tick finalizes only once."""
from unittest.mock import patch

from django import test as djtest
from django.db.models import F

from . import business_promotion_postreview as service
from . import business_promotion_runtime as runtime
from . import models as m, workflows
from . import test_business_promotion_content as fixtures
from .policy import AiError, mutation


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionPostreviewTests(djtest.TransactionTestCase):
    user = fixtures.PromotionContentTests.user
    call = fixtures.PromotionContentTests.call
    collect_body = fixtures.PromotionContentTests.collect_body
    bundle = fixtures.PromotionContentTests.bundle
    input_for = fixtures.PromotionContentTests.input_for
    insert = fixtures.PromotionContentTests.insert
    seed = fixtures.PromotionContentTests.seed
    setUp = fixtures.PromotionContentTests.setUp
    request_body = fixtures.PromotionContentTests.request_body
    current_catalog = fixtures.PromotionContentTests.current_catalog
    create_fixed_report = fixtures.PromotionContentTests.create_fixed_report
    base = fixtures.PromotionContentTests.base
    read = fixtures.PromotionContentTests.read
    append = fixtures.PromotionContentTests.append
    package = fixtures.PromotionContentTests.package
    promotion = fixtures.PromotionContentTests.promotion
    complete = fixtures.PromotionContentTests.complete
    running_job = fixtures.PromotionContentTests.running_job
    five_completed = fixtures.PromotionContentTests.five_completed

    def human(self, report):
        return m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id,
            node_key="human_review")

    def review(self, report, decision="approve"):
        body = {"expectedVersion": self.human(report).version,
            "decision": decision, "comment": "已逐项人工核对"}
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            return workflows.review(report.workflow_id, "human_review", body, self.admin)

    def advance(self, report):
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog):
            return service.advance(flow, self.admin)

    def test_real_human_approval_then_workflow_tick_completes_without_agent_calls(self):
        report = self.five_completed(promotion_reference=True)
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as remote:
            reviewed = self.review(report)
            self.assertEqual(reviewed["item"]["status"], "queued")
            flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
            self.assertTrue(service.is_approved_candidate(flow))
            with patch.object(workflows, "prepared_candidate",
                    return_value=(flow, self.admin, None)), patch.object(
                    runtime.transport, "catalog", side_effect=self.current_catalog):
                result = workflows.workflow_tick()
        self.assertEqual(result["status"], "completed")
        done = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        self.assertEqual(done.status, "completed")
        self.assertIsNotNone(done.output_json)
        self.assertEqual(m.AiWorkflowEvents.objects.filter(run=done,
            event_type="review_approved").count(), 1)
        self.assertEqual(m.AiWorkflowEvents.objects.filter(run=done,
            event_type="completed").count(), 1)
        self.assertEqual(self.advance(report)["status"], "not_claimed")
        model.assert_not_called(); remote.assert_not_called()

    def test_rejection_and_late_version_race_never_finalize(self):
        rejected = self.five_completed()
        self.review(rejected, "reject")
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=rejected.workflow_id).status,
            "failed")
        self.assertEqual(self.advance(rejected)["status"], "not_claimed")
        report = self.five_completed()
        self.review(report)
        original = service.approved.build

        def race(*args):
            value = original(*args)
            with mutation(self.admin):
                m.AiWorkflowRuns.objects.filter(pk=report.workflow_id).update(
                    version=F("version")+1)
            return value

        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.approved, "build", side_effect=race):
            outcome = service.advance(m.AiWorkflowRuns.objects.get(pk=report.workflow_id), self.admin)
        self.assertEqual(outcome["status"], "not_claimed")
        self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).status,
            "queued")

    def test_late_current_account_revocation_does_not_complete(self):
        report = self.five_completed()
        self.review(report)
        from access_control.models import AppUser
        original = service.approved.build

        def revoke(*args):
            value = original(*args)
            AppUser.objects.filter(email=self.admin.email).update(status="disabled")
            return value

        with patch.object(runtime.transport, "catalog", side_effect=self.current_catalog), patch.object(
                service.approved, "build", side_effect=revoke):
            outcome = service.advance(m.AiWorkflowRuns.objects.get(pk=report.workflow_id), self.admin)
        self.assertNotEqual(outcome["status"], "completed")
        self.assertIsNone(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).output_json)

    def test_existing_screening_review_route_keeps_original_branch(self):
        from . import business_screening_readiness
        old = self.report
        candidate = m.AiWorkflowRuns.objects.get(pk=old.workflow_id)
        self.assertIsNone(service.readiness.report_for(candidate))
        self.assertEqual(business_screening_readiness.report_for(candidate) is not None,
            "screeningIntent" in old.snapshot_json)
