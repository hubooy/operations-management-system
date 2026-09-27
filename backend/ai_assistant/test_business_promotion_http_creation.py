"""Signed public request creates only the exact enabled promotion profile."""
from unittest.mock import patch

from django import test as djtest
from sales.tests.factories import signed_headers, TEST_SECRET

from . import business_promotion_creation as creation, business_promotion_runtime_contract as contract
from . import models as m, workflows
from . import test_business_promotion_creation as fixtures
from .test_business_promotion_admission import catalog
from .test_business_screening_http import process_role
from .policy import canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
    AI_PROMOTION_AGENT_RUNTIME_ENABLED=True)
class PromotionHttpCreationTests(djtest.TransactionTestCase):
    user = fixtures.PromotionCreationTests.user
    call = fixtures.PromotionCreationTests.call
    collect_body = fixtures.PromotionCreationTests.collect_body
    bundle = fixtures.PromotionCreationTests.bundle
    input_for = fixtures.PromotionCreationTests.input_for
    insert = fixtures.PromotionCreationTests.insert
    seed = fixtures.PromotionCreationTests.seed
    setUp = fixtures.PromotionCreationTests.setUp
    request_body = fixtures.PromotionCreationTests.request_body

    def post(self, body, request_id):
        path = "/api/ai/business-reports"
        raw = canonical(body).encode("utf-8")
        headers = signed_headers(path, email=self.admin.email, role=self.admin.role,
            scope=self.admin.scope, method="POST", body=raw, request_id=request_id)
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
              djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET),
              patch.object(creation.transport, "catalog", return_value=catalog()),
              patch("ai_assistant.provider.turn", side_effect=AssertionError("paid model")),
              patch("ai_assistant.transport.execute_tool", side_effect=AssertionError("edge"))):
            return self.client.post(path, data=raw, content_type="application/json",
                headers=headers)

    def test_exact_profile_create_replay_and_default_off_rejection(self):
        old_count = m.AiReportRun.objects.count()
        denied = {**self.request_body(), "analysisMode": "screening-promotion-v1",
            "dryRun": False}
        with (djtest.override_settings(AI_PROMOTION_AGENT_RUNTIME_ENABLED=False),
              process_role("ai_writer")):
            refused = self.post(denied, "promotion-public-disabled")
        self.assertEqual(refused.status_code, 409, refused.content)
        self.assertEqual(m.AiReportRun.objects.count(), old_count)

        body = {**self.request_body(mapping=True),
            "analysisMode": "screening-promotion-v1", "dryRun": False}
        with process_role("ai_writer"):
            result = self.post(body, "promotion-public-create")
            replay = self.post(body, "promotion-public-create")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.headers.get("X-Teruisi-Write-Replay"), "1")
        report = m.AiReportRun.objects.select_related("workflow").get(
            pk=result.json()["item"]["id"])
        self.assertEqual(report.workflow.status, "queued")
        self.assertEqual(report.workflow.allowed_tools_json,
            canonical(list(contract.TOOL_ORDER)))
        self.assertEqual(m.AiWorkflowNodeRuns.objects.filter(run=report.workflow).count(), 6)
        self.assertEqual(m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id).count(), 0)

    def test_dry_run_and_wrong_mode_never_create_new_profile(self):
        body = {**self.request_body(), "analysisMode":"screening-promotion-v1",
            "dryRun": True}
        before = m.AiReportRun.objects.count()
        with process_role("ai_writer"):
            refused = self.post(body, "promotion-public-dry")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(m.AiReportRun.objects.count(), before)
