"""Signed human approval prepares new-profile proof outside the write lock."""
import json
from unittest.mock import patch

from django import test as djtest
from sales.tests.factories import signed_headers, TEST_SECRET

from . import business_promotion_runtime as promotion_runtime, models as m, workflows
from . import test_business_promotion_content as fixtures
from .test_business_screening_http import process_role
from .policy import canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionReviewRouteTests(djtest.TransactionTestCase):
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

    def test_signed_approval_then_next_tick_completes_without_model_call(self):
        old = m.AiWorkflowRuns.objects.get(pk=self.report.workflow_id)
        workflows.control(old.id, {"expectedVersion": old.version}, self.admin,
            "cancel", workflow=True)
        report = self.five_completed(promotion_reference=True)
        human = m.AiWorkflowNodeRuns.objects.get(run_id=report.workflow_id,
            node_key="human_review")
        url = f"/api/ai/workflow-runs/{report.workflow_id}/nodes/human_review/review"
        body = canonical({"expectedVersion": human.version, "decision": "approve",
            "comment": "已核对词货来源和调整前提"}).encode("utf-8")
        headers = signed_headers(url, email=self.admin.email, role=self.admin.role,
            scope=self.admin.scope, method="POST", body=body)
        with (process_role("ai_writer"),
              patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
              djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET),
              patch("ai_assistant.views.authority"),
              patch.object(promotion_runtime.transport, "catalog",
                  side_effect=self.current_catalog),
              patch("ai_assistant.provider.turn", side_effect=AssertionError("paid model")),
              patch("ai_assistant.transport.execute_tool", side_effect=AssertionError("tool"))):
            result = self.client.post(url, data=body, content_type="application/json",
                headers=headers)
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(m.AiWorkflowRuns.objects.get(pk=report.workflow_id).status,
                "queued")
            self.assertEqual(workflows.workflow_tick()["status"], "completed")
        flow = m.AiWorkflowRuns.objects.get(pk=report.workflow_id)
        human.refresh_from_db()
        self.assertEqual(flow.status, "completed")
        self.assertEqual(human.status, "completed")
        self.assertEqual(json.loads(human.output_json)["decision"], "approve")
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).count(), 5)
