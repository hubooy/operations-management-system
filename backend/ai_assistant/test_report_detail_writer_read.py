"""Exact report-detail writer GET without a mutation or reader ledger grant."""
import json
import secrets
from unittest.mock import patch

from django import test as djtest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from . import (business_promotion_content as promotion_content,
    business_promotion_runtime as runtime, reports, views)
from . import test_business_promotion_report_content as fixture
from .control_models import AiWriteReceipt
from .database_contract import provision
from .policy import Principal
from .test_business_market_v2_material_role_bridge import session_role


class ReportDetailWriterRouteTests(djtest.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.actor = Principal("report-owner@example.invalid", "Report owner",
            "admin", None)

    def call(self, path, role, method="GET"):
        request = (self.factory.get if method == "GET" else self.factory.post)(
            "/api/ai/" + path,
            HTTP_X_TERUISI_REQUEST_ID="report-detail-writer-read")
        with djtest.override_settings(DJANGO_PROCESS_ROLE=role), \
                patch.object(views, "verify_principal", return_value=self.actor), \
                patch.object(views, "current_principal", return_value=self.actor), \
                patch.object(views, "authority"), \
                patch.object(views.reports, "detail", return_value={
                    "item": {"id": "report_1"}}) as read, \
                patch.object(views, "write") as write:
            result = views._dispatch(request, path)
        return result, read, write

    def test_only_exact_get_uses_writer_and_never_write_wrapper(self):
        result, read, write = self.call("reports/report_1", "ai_writer")
        self.assertEqual(result.status_code, 200, result.content)
        read.assert_called_once()
        write.assert_not_called()
        denied, read, write = self.call("reports/report_1", "ai_reader")
        self.assertEqual(denied.status_code, 403)
        read.assert_not_called(); write.assert_not_called()
        for path in ("reports", "reports/report_1/files",
                "reports/report_1/budget", "reports/report_1/promotion-keyword-sku"):
            result, read, write = self.call(path, "ai_writer")
            self.assertEqual(result.status_code, 403, path)
            read.assert_not_called(); write.assert_not_called()
        result, read, write = self.call("reports/report_1/extra", "ai_writer")
        self.assertEqual(result.status_code, 404)
        read.assert_not_called(); write.assert_not_called()
        result, read, write = self.call("reports/report_1/", "ai_writer")
        self.assertEqual(result.status_code, 404)
        read.assert_not_called(); write.assert_not_called()


@djtest.override_settings(DJANGO_PROCESS_ROLE="development",
    DJANGO_ENVIRONMENT="test")
class ReportDetailWriterOwningTests(djtest.TransactionTestCase):
    user = fixture.PromotionReportContentTests.user
    call = fixture.PromotionReportContentTests.call
    collect_body = fixture.PromotionReportContentTests.collect_body
    bundle = fixture.PromotionReportContentTests.bundle
    input_for = fixture.PromotionReportContentTests.input_for
    insert = fixture.PromotionReportContentTests.insert
    seed = fixture.PromotionReportContentTests.seed
    setUp = fixture.PromotionReportContentTests.setUp
    request_body = fixture.PromotionReportContentTests.request_body
    current_catalog = fixture.PromotionReportContentTests.current_catalog
    create_fixed_report = fixture.PromotionReportContentTests.create_fixed_report
    base = fixture.PromotionReportContentTests.base
    read = fixture.PromotionReportContentTests.read
    append = fixture.PromotionReportContentTests.append
    package = fixture.PromotionReportContentTests.package
    promotion = fixture.PromotionReportContentTests.promotion
    complete = fixture.PromotionReportContentTests.complete
    running_job = fixture.PromotionReportContentTests.running_job
    five_completed = fixture.PromotionReportContentTests.five_completed

    def writer_get(self, report_id, principal):
        request = RequestFactory().get("/api/ai/reports/" + report_id,
            HTTP_X_TERUISI_REQUEST_ID="report-detail-real-read")
        with patch.object(views, "verify_principal", return_value=principal), \
                patch.object(views, "authority"), \
                patch.object(runtime.transport, "catalog",
                    side_effect=self.current_catalog), \
                patch("ai_assistant.provider.turn") as provider, \
                patch("ai_assistant.transport.execute_tool") as tool, \
                session_role("teruisi_ai_writer"), \
                djtest.override_settings(DJANGO_PROCESS_ROLE="ai_writer"), \
                CaptureQueriesContext(connection) as queries:
            response = views._dispatch(request, "reports/" + report_id)
        provider.assert_not_called(); tool.assert_not_called()
        self.assertFalse(any(item["sql"].lstrip().upper().startswith((
            "INSERT", "UPDATE", "DELETE", "TRUNCATE"))
            for item in queries.captured_queries))
        return response

    def test_completed_promotion_detail_is_writer_read_without_receipt(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL roles")
        report = self.five_completed(promotion_reference=True)
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32),
            secrets.token_hex(32))
        with connection.cursor() as cursor:
            cursor.execute("SELECT has_table_privilege('teruisi_ai_reader',"
                "'public.ai_agent_provider_dispatches','SELECT')")
            self.assertEqual(cursor.fetchone(), (False,))
        before = AiWriteReceipt.objects.count()
        response = self.writer_get(report.id, self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertRegex(response["X-AI-Revision"], r"^[0-9]+$")
        body = json.loads(response.content)
        self.assertNotIn("contentError", body)
        self.assertEqual(len(body["sections"]), 5)
        self.assertEqual(AiWriteReceipt.objects.count(), before)
        outsider = self.user("report-outsider@example.invalid", "admin", None)
        denied = self.writer_get(report.id, outsider)
        self.assertIn(denied.status_code, (403, 404))
        self.assertNotIn("sections", json.loads(denied.content))
        self.assertEqual(AiWriteReceipt.objects.count(), before)

    def test_late_report_version_drift_returns_no_stale_content(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires isolated PostgreSQL roles")
        report = self.five_completed(promotion_reference=True)
        connection.ensure_connection()
        provision(connection.connection, secrets.token_hex(32),
            secrets.token_hex(32))
        original = promotion_content._state
        calls = 0
        def drift(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                state = {**result[3], "workflowVersion":
                    result[3]["workflowVersion"] + 1}
                return (*result[:3], state)
            return result
        before = AiWriteReceipt.objects.count()
        with patch.object(promotion_content, "_state", side_effect=drift):
            response = self.writer_get(report.id, self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        body = json.loads(response.content)
        self.assertGreaterEqual(calls, 2)
        self.assertIn("contentError", body)
        self.assertNotIn("sections", body)
        self.assertEqual(AiWriteReceipt.objects.count(), before)
