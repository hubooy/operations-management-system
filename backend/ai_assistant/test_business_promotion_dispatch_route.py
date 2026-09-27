"""Signed reader-only route binds one exact calling dispatch, not a report GET."""
from unittest.mock import patch
import json

from django import test as djtest
from sales.tests.factories import signed_headers, TEST_SECRET

from . import business_promotion_dispatch_tool as bridge
from . import business_promotion_runtime_contract as contract
from . import test_business_promotion_dispatch_tool as fixtures
from .policy import canonical


@djtest.override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class PromotionDispatchRouteTests(djtest.TransactionTestCase):
    user = fixtures.PromotionDispatchToolTests.user
    call = fixtures.PromotionDispatchToolTests.call
    collect_body = fixtures.PromotionDispatchToolTests.collect_body
    bundle = fixtures.PromotionDispatchToolTests.bundle
    input_for = fixtures.PromotionDispatchToolTests.input_for
    insert = fixtures.PromotionDispatchToolTests.insert
    seed = fixtures.PromotionDispatchToolTests.seed
    setUp = fixtures.PromotionDispatchToolTests.setUp
    request_body = fixtures.PromotionDispatchToolTests.request_body
    current_catalog = fixtures.PromotionDispatchToolTests.current_catalog
    create_report = fixtures.PromotionDispatchToolTests.create_report
    actual_job = fixtures.PromotionDispatchToolTests.actual_job
    dispatch = fixtures.PromotionDispatchToolTests.dispatch

    def route(self, dispatch, payload, *, signed=True, request_id=None, role="ai_reader",
              method="POST", suffix="", query=""):
        url = f"/api/ai/promotion-tool-dispatch/{dispatch.id}{suffix}{query}"
        body = canonical(payload).encode("utf-8") if method == "POST" else b""
        headers = signed_headers(url, email=self.admin.email, role=self.admin.role,
            scope=self.admin.scope, method=method, body=body,
            request_id=request_id or dispatch.id) if signed else {}
        with (patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}),
                djtest.override_settings(DJANGO_INTERNAL_SECRET=TEST_SECRET,
                    DJANGO_PROCESS_ROLE=role), patch("ai_assistant.views.authority"), patch.object(
                    bridge.runtime.transport, "catalog", side_effect=self.current_catalog)):
            return self.client.generic(method, url, data=body,
                content_type="application/json", headers=headers)

    def test_exact_signed_reader_post_returns_owning_page_without_write(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion"}
        dispatch, call_id = self.dispatch(job, contract.PACKAGE_TOOL, args)
        payload = {"name":contract.PACKAGE_TOOL, "arguments":args, "providerCallId":call_id}
        with patch("ai_assistant.provider.turn") as model, patch(
                "ai_assistant.transport.execute_tool") as external:
            result = self.route(dispatch, payload)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result["Cache-Control"], "no-store")
        self.assertEqual(result.json()["role"], "promotion")
        self.assertEqual(result.json()["reportId"], report.id)
        model.assert_not_called(); external.assert_not_called()

    def test_header_body_path_method_and_query_spoofs_are_rejected(self):
        report = self.create_report()
        job = self.actual_job(report)
        snapshot = json.loads(report.snapshot_json)
        args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":"promotion"}
        dispatch, call_id = self.dispatch(job, contract.PACKAGE_TOOL, args)
        payload = {"name":contract.PACKAGE_TOOL, "arguments":args, "providerCallId":call_id}
        cases = ((dict(signed=False), 401), (dict(request_id="other-dispatch"), 403),
            (dict(query="?spoof=1"), 400), (dict(method="GET"), 405),
            (dict(role="ai_writer"), 403), (dict(suffix="/extra"), 404))
        for kwargs, expected in cases:
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.route(dispatch, payload, **kwargs).status_code, expected)
        for changed in ({**payload, "arguments":{**args, "role":"commerce"}},
                {**payload, "providerCallId":"other-call"},
                {**payload, "extra":1}):
            with self.subTest(changed=changed):
                self.assertNotEqual(self.route(dispatch, changed).status_code, 200)
