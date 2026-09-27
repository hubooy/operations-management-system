"""Candidate alias route requires the separate signed reader ID and flag."""
import json
from unittest.mock import patch

from django import test as djtest
from django.test import RequestFactory
from sales.auth import Principal

from . import views
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="ai_reader", DJANGO_ENVIRONMENT="test")
class MarketV2BaseToolRouteTests(djtest.SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.principal = Principal("market-alias@example.invalid", "Synthetic", "admin", None)
        self.path = "market-v2-base-tool-candidate/preview-123"
        self.args = {"reportId": "admitted-1", "runId": "run-1",
            "screeningId": "screen-1", "role": "market_b2b", "offset": 0}

    def route(self, *, flag=False, name="get_business_market_v2_screening_package",
              arguments=None, request_id="preview-123", role="admin", method="POST"):
        body = {"name": name, "arguments": self.args if arguments is None else arguments,
            "providerCallId": "model-call-1"}
        request = self.factory.generic(method, "/api/ai/" + self.path,
            data=json.dumps(body).encode() if method == "POST" else b"",
            content_type="application/json", HTTP_X_TERUISI_REQUEST_ID=request_id)
        principal = Principal(self.principal.email, self.principal.display_name,
            role, None)
        def current(_principal, *, admin=False, write=False, background=False):
            if admin and principal.role != "admin":
                raise AiError("当前账号无权读取市场别名", "access_denied", 403)
            return principal
        with djtest.override_settings(AI_MARKET_V2_AGENT_RUNTIME_ENABLED=flag), patch.object(
                views, "verify_principal", return_value=principal), patch.object(
                views, "current_principal", side_effect=current), patch.object(
                views, "authority"), patch.object(views, "revision", return_value="1"), patch(
                "ai_assistant.business_market_v2_base_tool_candidate.read") as read:
            read.return_value = {"schemaVersion": "business-market-v2-base-tool-candidate-v1",
                "persistedRead": False, "registeredAgentTool": False}
            response = views._dispatch(request, self.path)
            return response, read

    def test_flag_reader_role_request_id_and_old_name_are_closed(self):
        for kwargs, status in (({}, 409), ({"flag": True, "role": "viewer"}, 403),
                ({"flag": True, "request_id": "other"}, 403),
                ({"flag": True, "method": "GET"}, 405),
                ({"flag": True, "name": "get_business_promotion_screening_package_v1"}, 400)):
            with self.subTest(kwargs=kwargs):
                result, read = self.route(**kwargs)
                self.assertEqual(result.status_code, status)
                read.assert_not_called()

    def test_enabled_alias_stays_nonpersistent_and_receives_exact_arguments(self):
        result, read = self.route(flag=True)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(json.loads(result.content)["persistedRead"])
        self.assertEqual(read.call_args.args[0], "get_business_market_v2_screening_package")
        self.assertEqual(read.call_args.args[1], self.args)
        self.assertEqual(result["Cache-Control"], "no-store")
