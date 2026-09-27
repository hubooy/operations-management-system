"""Disabled-by-default reader route; no database or model call."""
import json
from unittest.mock import patch

from django import test as djtest
from django.test import RequestFactory
from sales.auth import Principal

from . import views
from .policy import AiError


@djtest.override_settings(DJANGO_PROCESS_ROLE="ai_reader", DJANGO_ENVIRONMENT="test")
class MarketV2ToolCandidateRouteTests(djtest.SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.principal = Principal("market-route@example.invalid", "Synthetic",
            "admin", None)
        self.path = "market-v2-tool-candidate/preview-123"
        self.args = {"reportId": "admitted-report", "marketContextDigest": "a"*64,
            "marketManifestDigest": "b"*64, "role": "market_b2b", "mode": "summary"}

    def route(self, args=None, *, flag=False, request_id="preview-123", role="admin",
              method="POST", provider_call="model-call-1"):
        body = {"arguments": self.args if args is None else args,
            "providerCallId": provider_call}
        request = self.factory.generic(method, "/api/ai/" + self.path,
            data=json.dumps(body).encode() if method == "POST" else b"",
            content_type="application/json", HTTP_X_TERUISI_REQUEST_ID=request_id)
        principal = Principal(self.principal.email, self.principal.display_name,
            role, None)
        def current(_principal, *, admin=False, write=False, background=False):
            if admin and principal.role != "admin":
                raise AiError("角色不允许市场v2候选读取", "access_denied", 403)
            return principal
        with djtest.override_settings(AI_MARKET_V2_AGENT_RUNTIME_ENABLED=flag), patch.object(
                views, "verify_principal", return_value=principal), patch.object(
                views, "current_principal", side_effect=current), patch.object(
                views, "authority"), patch.object(views, "revision", return_value="1"), patch(
                "ai_assistant.business_market_v2_transport_candidate.read") as read:
            read.return_value = {"schemaVersion": "business-market-v2-tool-result-candidate-v1",
                "persistedRead": False, "registeredTool": False}
            response = views._dispatch(request, self.path)
            return response, read

    def test_flag_role_method_and_signed_request_id_keep_preview_closed(self):
        for kwargs, status in (({}, 409), ({"flag": True, "role": "viewer"}, 403),
                ({"flag": True, "request_id": "different"}, 403),
                ({"flag": True, "method": "GET"}, 405)):
            with self.subTest(kwargs=kwargs):
                result, read = self.route(**kwargs)
                self.assertEqual(result.status_code, status)
                read.assert_not_called()

    def test_enabled_route_injects_only_candidate_identity_and_exact_arguments(self):
        result, read = self.route(flag=True)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(json.loads(result.content)["persistedRead"])
        call = read.call_args.args[3]
        self.assertEqual(call["jobId"], "market-preview-job-preview-123")
        self.assertEqual(call["providerDispatchId"],
            "market-preview-provider-preview-123")
        self.assertEqual(call["providerCallId"], "model-call-1")
        self.assertEqual(call["role"], "market_b2b")
        self.assertEqual(read.call_args.args[4], {"reportId": "admitted-report",
            "marketContextDigest": "a"*64, "mode": "summary"})
        self.assertEqual(result["Cache-Control"], "no-store")

    def test_exclusive_modes_and_unsupported_role_reject_before_reader(self):
        for bad in ({**self.args, "offset": 0},
                {**self.args, "role": "commerce"},
                {**self.args, "mode": "row", "view": "price_band",
                    "rowIndex": 0, "rowId": "c"*64, "offset": 0},
                {**self.args, "mode": "page", "view": "price_band",
                    "offset": 0, "limit": 10}):
            with self.subTest(bad=bad):
                result, read = self.route(bad, flag=True)
                self.assertEqual(result.status_code, 400)
                read.assert_not_called()
