"""Endpoint-contract fixtures reproduce the production final-turn regression."""
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import chat, models as m, provider, transport, test_chat_tool_budget as support
from .test_dataset_chat import wire
from .policy import AiError


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ProviderEndpointContractTests(TestCase):
    user = support.ChatToolBudgetTests.user
    setUp = support.ChatToolBudgetTests.setUp
    run_chat = support.ChatToolBudgetTests.run_chat

    def test_real_ark_glm_time_finalization_never_injects_disabled_thinking(self):
        self.model.model_name = "glm-5.2"
        self.model.base_url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        self.model.reasoning_mode = "auto"
        self.model.timeout_ms = 120000
        self.model.max_tool_rounds = 56
        self.model.max_total_tool_calls = 62
        self.model.save()
        self.entries[0]["execution"]["maxCallsPerRequest"] = 4
        sent = []

        def ark_contract(url, body, headers, **kwargs):
            sent.append(body)
            if body.get("thinking") == {"type": "disabled"}:
                raise AiError("服务返回 HTTP 400", "provider_error", 503)
            return wire("openai_compatible", "get_data_freshness", {}) if body.get("tools") else wire("openai_compatible", answer="已按现有证据完成回答。")

        patches = self.run_chat([])
        with patches[0], patches[1], patches[2], patch.dict("os.environ", {"AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST": "https://ark.cn-beijing.volces.com"}), patch.object(provider, "bounded_json", side_effect=ark_contract), patch.object(chat.transport, "remaining_budget", side_effect=[260, 216, 182, 15]):
            result = chat.answer({"clientRequestId": "ark-time-final", "message": "802昨天卖了多少台"}, self.owner, "ark-time-final")
        self.assertEqual(result["outcome"], "answered")
        self.assertEqual(len(sent), 4)
        self.assertNotIn("tools", sent[-1])
        self.assertTrue(all("thinking" not in body for body in sent))
        self.model.refresh_from_db()
        self.assertEqual(self.model.reasoning_mode, "auto")

    def test_final_turn_preserves_endpoint_configuration_across_protocols(self):
        cases = [
            ("openai_compatible", "https://ark.cn-beijing.volces.com/api/plan/v3", "glm-5.2", "auto"),
            ("openai_compatible", "https://open.bigmodel.cn/api/paas/v4", "glm-5.2", "auto"),
            ("openai_compatible", "https://example.com/v1", "unknown-model", "auto"),
            ("openai_compatible", "https://example.com/v1", "fixture-model", "disabled"),
            ("anthropic", "https://example.com/v1", "fixture-model", "auto"),
        ]
        for index, (protocol, base, name, reasoning) in enumerate(cases):
            with self.subTest(protocol=protocol, endpoint=base, reasoning=reasoning):
                self.model.protocol = protocol
                self.model.base_url = base
                self.model.model_name = name
                self.model.reasoning_mode = reasoning
                self.model.max_tool_rounds = 2
                self.model.save()
                patches = self.run_chat([wire(protocol, "get_data_freshness", {}), wire(protocol, answer="完成")])
                with patches[0], patches[1], patches[2], patches[3] as http, patch.dict("os.environ", {"AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST": ",".join(["https://ark.cn-beijing.volces.com", "https://open.bigmodel.cn", "https://example.com"])}):
                    chat.answer({"clientRequestId": f"endpoint-{index}", "message": "查询"}, self.owner, f"endpoint-{index}")
                bodies = [call.args[1] for call in http.call_args_list]
                self.assertEqual(len(bodies), 2)
                self.assertNotIn("tools", bodies[-1])
                expected = {"type": "disabled"} if reasoning == "disabled" else None
                self.assertTrue(all(body.get("thinking") == expected for body in bodies))

    def test_ark_completed_empty_response_finalization_preserves_auto(self):
        self.model.base_url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        self.model.model_name = "glm-5.2"
        self.model.save()
        sent = []
        def ark_contract(url, body, headers, **kwargs):
            sent.append(body)
            if "thinking" in body:
                raise transport.ProviderHttpError(400)
            if len(sent) == 1:
                return {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "private-fixture"}}]}
            return wire("openai_compatible", answer="已完成回复。")
        patches = self.run_chat([])
        with patches[0], patches[1], patches[2], patch.dict("os.environ", {"AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST": "https://ark.cn-beijing.volces.com"}), patch.object(provider, "bounded_json", side_effect=ark_contract):
            result = chat.answer({"clientRequestId": "ark-empty", "message": "查询"}, self.owner, "ark-empty")
        self.assertEqual(result["outcome"], "answered")
        self.assertEqual(len(sent), 2)
        self.assertNotIn("tools", sent[-1])
        final = m.AiToolAuditLogs.objects.get(request_id="ark-empty", tool_name="ai_chat_provider", status="succeeded")
        self.assertIn('"phase":"final"', final.arguments_json)
        self.assertNotIn("private-fixture", final.arguments_json)

    def test_provider_http_rejection_has_safe_phase_evidence_and_no_retry(self):
        self.model.max_tool_rounds = 2
        self.model.save()
        patches = self.run_chat([wire("openai_compatible", "get_data_freshness", {}), transport.ProviderHttpError(400)])
        body = {"clientRequestId": "final-http-400", "message": "查询"}
        with patches[0], patches[1], patches[2], patches[3] as http:
            with self.assertRaises(AiError):
                chat.answer(body, self.owner, "final-http-400")
            with self.assertRaises(AiError):
                chat.answer(body, self.owner, "replay-http-400")
            self.assertEqual(http.call_count, 2)
        failed = m.AiToolAuditLogs.objects.get(request_id="final-http-400", status="failed")
        self.assertIn('"phase":"final"', failed.arguments_json)
        self.assertIn('"thinkingParameter":"omitted"', failed.arguments_json)
        self.assertIn('"httpStatus":400', failed.arguments_json)
        self.assertEqual(failed.error_code, "provider_error")
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())
