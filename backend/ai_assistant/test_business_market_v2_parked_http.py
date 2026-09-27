"""Signed HTTP boundary for the opt-in, non-Agent parked market preview."""
from unittest.mock import patch

from django import test as djtest
from sales.tests.factories import TEST_SECRET, signed_headers

from . import models as m
from . import business_market_v2_parked_creation as parked
from . import test_business_promotion_market_admission as fixtures
from .test_business_screening_http import process_role
from .policy import canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class MarketV2ParkedHttpTests(djtest.TransactionTestCase):
    user = fixtures.PromotionMarketAdmissionTests.user
    request_body = fixtures.PromotionMarketAdmissionTests.request_body
    current_catalog = fixtures.PromotionMarketAdmissionTests.current_catalog
    create_fixed_report = fixtures.PromotionMarketAdmissionTests.create_fixed_report
    planned_evidence_body = fixtures.PromotionMarketAdmissionTests.planned_evidence_body
    setUp = fixtures.PromotionMarketAdmissionTests.setUp
    selector = fixtures.PromotionMarketAdmissionTests.selector

    def post(self, client="parked-http", request_id="market-v2-http"):
        path = "/api/ai/market-v2-parked-reports"
        raw = canonical({"schemaVersion": parked.REQUEST_SCHEMA,
            "clientRequestId": client, "sourceReportId": self.report.id,
            "marketSelector": self.selector()}).encode("utf-8")
        headers = signed_headers(path, email=self.admin.email,
            role=self.admin.role, scope=self.admin.scope, method="POST",
            body=raw, request_id=request_id)
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            return self.client.post(path, data=raw, content_type="application/json",
                headers=headers)

    def get(self, report_id, query="", *, actor=None, request_id="market-v2-read"):
        path = "/api/ai/market-v2-parked-reports/"+report_id+"/preview"
        if query:
            path += "?"+query
        actor = actor or self.admin
        headers = signed_headers(path, email=actor.email, role=actor.role,
            scope=actor.scope, method="GET", body=b"", request_id=request_id)
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            return self.client.get(path, headers=headers)

    def test_default_off_blocks_signed_create_and_preview(self):
        before = m.AiReportRun.objects.count()
        with process_role("ai_writer"):
            denied = self.post()
        self.assertEqual(denied.status_code, 409, denied.content)
        self.assertEqual(denied.json()["code"], "market_v2_preview_not_ready")
        self.assertEqual(m.AiReportRun.objects.count(), before)
        with process_role("ai_reader"):
            denied_read = self.get(self.report.id)
        self.assertEqual(denied_read.status_code, 409, denied_read.content)

    @djtest.override_settings(AI_MARKET_V2_PREVIEW_ENABLED=True)
    def test_create_replay_and_read_only_summary_page_keep_parked(self):
        with process_role("ai_writer"), patch(
                "ai_assistant.provider.turn", side_effect=AssertionError("model")):
            created = self.post()
            replay = self.post(request_id="market-v2-http-replay")
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(created.json()["item"], replay.json()["item"])
        report_id = created.json()["item"]["id"]
        self.assertFalse(created.json()["materialAdmitted"])
        self.assertTrue(created.json()["requestMaterialReplayed"])
        row = m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
        self.assertEqual(row.workflow.status, "paused")
        self.assertEqual(row.workflow.allowed_tools_json, "[]")
        self.assertFalse(m.AiAgentJobs.objects.filter(
            workflow_run_id=row.workflow_id).exists())
        counts = (m.AiReportRun.objects.count(),
            m.AiAgentJobs.objects.count(), m.AiBusinessFileRun.objects.count())
        with process_role("ai_reader"), patch(
                "ai_assistant.transport.execute_tool",
                side_effect=AssertionError("live source")):
            summary = self.get(report_id)
            page = self.get(report_id,
                "view=price_band_members&offset=0&limit=20",
                request_id="market-v2-page")
        self.assertEqual(summary.status_code, 200, summary.content)
        self.assertEqual(page.status_code, 200, page.content)
        self.assertIsNone(summary.json()["page"])
        self.assertEqual([item["view"] for item in summary.json()["tables"]],
            ["price_band_summary", "price_band_members", "rank_entry_exit"])
        self.assertEqual(page.json()["page"]["view"], "price_band_members")
        self.assertLessEqual(len(page.json()["page"]["rows"]), 20)
        for value in (summary.json(), page.json()):
            self.assertTrue(value["requestMaterialReplayed"])
            for name in ("materialAdmitted", "agentReadPersisted",
                    "actualAgentBound", "authorityVerified", "renderer"):
                self.assertFalse(value[name])
        self.assertEqual((m.AiReportRun.objects.count(),
            m.AiAgentJobs.objects.count(), m.AiBusinessFileRun.objects.count()),
            counts)

    @djtest.override_settings(AI_MARKET_V2_PREVIEW_ENABLED=True)
    def test_other_actor_and_bad_page_fail_closed(self):
        with process_role("ai_writer"):
            created = self.post(client="market-guard", request_id="market-guard")
        self.assertEqual(created.status_code, 200, created.content)
        report_id = created.json()["item"]["id"]
        outsider = self.user("market-http-outside@example.invalid", "admin", None)
        with process_role("ai_reader"):
            other = self.get(report_id, actor=outsider)
            bad = self.get(report_id, "view=rank_entry_exit&offset=00&limit=20",
                request_id="market-bad-page")
            unsupported = self.get(report_id, "view=rank_entry_exit&offset=0&limit=21",
                request_id="market-bad-limit")
        self.assertEqual(other.status_code, 404, other.content)
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertEqual(unsupported.status_code, 400, unsupported.content)
