"""Chat must finish from available evidence without bypassing tool admission."""
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase, override_settings

from . import chat, models as m, provider
from .policy import AiError, canonical, digest
from .test_dataset_chat import wire
from . import tests as support


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class ChatToolBudgetTests(TestCase):
    user = support.AiDomainTests.user

    def setUp(self):
        support.AiDomainTests.setUp(self)
        self.entries = deepcopy(support.CATALOG)
        self.entries[0]["execution"]["maxCallsPerRequest"] = 2

    def run_chat(self, responses, execute=None, request="budget"):
        return (
            patch.object(chat.transport, "catalog", return_value=self.entries),
            patch.object(chat.transport, "execute_tool", side_effect=execute or (lambda *args, **kwargs: {"ok": True, "data": {"through": "2026-09-09"}})),
            patch.object(provider, "decrypt", return_value="isolated-fixture-key"),
            patch.object(provider, "bounded_json", side_effect=responses),
        )

    def test_parameter_failure_then_success_then_exhausted_tool_still_answers(self):
        for protocol in ["openai_compatible", "anthropic"]:
            with self.subTest(protocol=protocol):
                self.model.protocol = protocol
                self.model.save(update_fields=["protocol"])
                responses = [wire(protocol, "get_data_freshness", {}) for _ in range(3)]
                responses.append(wire(protocol, answer="已获得数据截止日期；其余范围未查询。"))
                attempts = iter([
                    {"ok": False, "error": {"code": "invalid_tool_arguments"}},
                    {"ok": True, "data": {"through": "2026-09-09"}},
                ])
                patches = self.run_chat(responses, lambda *a, **kw: next(attempts))
                with patches[0], patches[1] as source, patches[2], patches[3] as http:
                    body = {"clientRequestId": protocol, "message": "分析库存"}
                    answer = chat.answer(body, self.owner, protocol)
                    self.assertEqual(answer["outcome"], "answered")
                    self.assertEqual(source.call_count, 2)
                    self.assertEqual(http.call_count, 4)
                    bodies = [call.args[1] for call in http.call_args_list]
                    self.assertIn("剩余最多 2 次", canonical(bodies[0]["tools"]))
                    self.assertIn("剩余最多 1 次", canonical(bodies[1]["tools"]))
                    self.assertNotIn("tools", bodies[2])
                    self.assertNotIn("tools", bodies[3])
                    self.assertIn("tool_limit_exceeded", canonical(bodies[3]["messages"]))
                    self.assertIn("2026-09-09", canonical(bodies[3]["messages"]))
                    self.assertEqual(source.call_args.kwargs["policy_digest"], digest(self.entries))
                    self.assertEqual(chat.answer(body, self.owner, "replay"), answer)
                    self.assertEqual(http.call_count, 4)
                denied = m.AiToolAuditLogs.objects.get(request_id=protocol, status="denied")
                self.assertEqual(denied.error_code, "tool_limit_exceeded")
                self.assertEqual(denied.provider_call_id, "fixture-call")

    def test_total_limit_and_final_round_never_execute_extra_calls(self):
        for reason in ["total", "rounds"]:
            with self.subTest(reason=reason):
                self.model.max_total_tool_calls = 1 if reason == "total" else 12
                self.model.max_tool_rounds = 6 if reason == "total" else 2
                self.model.save()
                responses = [wire("openai_compatible", "get_data_freshness", {}),
                             wire("openai_compatible", answer="按已有数据回答。")]
                patches = self.run_chat(responses)
                with patches[0], patches[1] as source, patches[2], patches[3] as http:
                    answer = chat.answer({"clientRequestId": reason, "message": "查询"}, self.owner, reason)
                    self.assertEqual(answer["outcome"], "answered")
                    self.assertEqual(source.call_count, 1)
                    self.assertNotIn("tools", http.call_args.args[1])
                    self.assertIn("只生成最终回答", canonical(http.call_args.args[1]))

    def test_batch_only_admits_calls_within_total_budget(self):
        self.model.max_total_tool_calls = 1
        self.model.save()
        batch = wire("openai_compatible", "get_data_freshness", {})
        calls = batch["choices"][0]["message"]["tool_calls"]
        calls.append({**deepcopy(calls[0]), "id": "second-call"})
        patches = self.run_chat([batch, wire("openai_compatible", answer="只读取了一次水位。")])
        with patches[0], patches[1] as source, patches[2], patches[3] as http:
            chat.answer({"clientRequestId": "batch", "message": "查询"}, self.owner, "batch")
            self.assertEqual(source.call_count, 1)
            self.assertEqual(sum(frame["role"] == "tool" for frame in http.call_args.args[1]["messages"]), 2)
        self.assertEqual(m.AiToolAuditLogs.objects.get(status="denied").provider_call_id, "second-call")

    def test_unknown_tool_remains_denied_and_is_audited(self):
        patches = self.run_chat([wire("openai_compatible", "unregistered_write", {})])
        with patches[0], patches[1] as source, patches[2], patches[3]:
            with self.assertRaises(AiError) as error:
                chat.answer({"clientRequestId": "denied", "message": "查询"}, self.owner, "denied")
            self.assertEqual(error.exception.code, "access_denied")
            source.assert_not_called()
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())
        self.assertEqual(m.AiToolAuditLogs.objects.get(status="denied").error_code, "access_denied")

    def test_rejection_audit_failure_closes_without_final_provider_call(self):
        self.entries[0]["execution"]["maxCallsPerRequest"] = 1
        patches = self.run_chat([wire("openai_compatible", "get_data_freshness", {})] * 2)
        real_audit = chat.audit
        def broken_audit(*args, **kwargs):
            if args[3] == "denied":
                raise AiError("audit unavailable", "service_unavailable", 503)
            return real_audit(*args, **kwargs)
        with patches[0], patches[1] as source, patches[2], patches[3] as http, patch.object(chat, "audit", side_effect=broken_audit):
            with self.assertRaises(AiError):
                chat.answer({"clientRequestId": "audit-fail", "message": "查询"}, self.owner, "audit-fail")
            self.assertEqual(source.call_count, 1)
            self.assertEqual(http.call_count, 2)
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())

    def test_provider_ignoring_final_answer_instruction_is_bounded(self):
        self.model.max_tool_rounds = 2
        self.model.save()
        patches = self.run_chat([wire("openai_compatible", "get_data_freshness", {})] * 2)
        with patches[0], patches[1] as source, patches[2], patches[3] as http:
            with self.assertRaises(AiError) as error:
                chat.answer({"clientRequestId": "stubborn", "message": "查询"}, self.owner, "stubborn")
            self.assertEqual(error.exception.code, "tool_limit_exceeded")
            self.assertEqual(source.call_count, 1)
            self.assertEqual(http.call_count, 2)

    def test_glm_tool_turn_preserves_reasoning_only_in_live_transcript(self):
        self.model.model_name = "glm-5.2"
        self.model.save()
        first = wire("openai_compatible", "get_data_freshness", {})
        first["choices"][0]["message"]["reasoning_content"] = "private-reasoning-fixture"
        patches = self.run_chat([first, wire("openai_compatible", answer="查询已完成。")])
        with patches[0], patches[1], patches[2], patches[3] as http:
            result = chat.answer({"clientRequestId": "reasoning", "message": "查询"}, self.owner, "reasoning")
            frames = http.call_args.args[1]["messages"]
            self.assertEqual(frames[2]["reasoning_content"], "private-reasoning-fixture")
            self.assertEqual(frames[3]["role"], "tool")
        self.assertNotIn("private-reasoning-fixture", canonical(result))
        self.assertFalse(m.AiConversationMessages.objects.filter(content__contains="private-reasoning-fixture").exists())
        self.assertFalse(m.AiToolAuditLogs.objects.filter(arguments_json__contains="private-reasoning-fixture").exists())

    def test_completed_empty_glm_response_gets_one_direct_finalization_and_replay_is_read_only(self):
        self.model.model_name = "glm-5.2"
        self.model.save()
        empty = {"choices": [{"finish_reason": "length", "message": {"content": "  ", "reasoning_content": "private-thought"}}],
                 "usage": {"completion_tokens": 4096}}
        patches = self.run_chat([wire("openai_compatible", "get_data_freshness", {}), empty,
                                 wire("openai_compatible", answer="已取得水位，其余数据未查询。")])
        body = {"clientRequestId": "empty-finalize", "message": "查询"}
        with patches[0], patches[1] as source, patches[2], patches[3] as http:
            result = chat.answer(body, self.owner, "empty-finalize")
            self.assertEqual(http.call_count, 3)
            self.assertEqual(source.call_count, 1)
            self.assertNotIn("thinking", http.call_args.args[1])
            self.assertNotIn("tools", http.call_args.args[1])
            self.assertIn("2026-09-09", canonical(http.call_args.args[1]["messages"]))
            self.assertNotIn("private-thought", canonical(http.call_args.args[1]))
            self.assertEqual(chat.answer(body, self.owner, "replay"), result)
            self.assertEqual(http.call_count, 3)
        failed = m.AiToolAuditLogs.objects.get(status="failed")
        self.assertEqual(failed.error_code, "provider_output_limit")
        self.assertIn('"completionUnits":4096', failed.arguments_json)
        self.assertIn('"hasReasoning":true', failed.arguments_json)
        self.assertEqual(m.AiChatProviderDispatches.objects.count(), 3)
        self.model.refresh_from_db()
        self.assertEqual(self.model.reasoning_mode, "auto")

    def test_empty_finalization_never_repeats_or_exceeds_rounds(self):
        empty = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
        for rounds, expected in [(1, 1), (6, 2)]:
            self.model.model_name = "glm-5.2"
            self.model.max_tool_rounds = rounds
            self.model.save()
            patches = self.run_chat([empty, empty])
            with patches[0], patches[1] as source, patches[2], patches[3] as http:
                with self.assertRaises(AiError) as caught:
                    chat.answer({"clientRequestId": f"empty-{rounds}", "message": "查询"}, self.owner, f"empty-{rounds}")
                self.assertEqual(caught.exception.code, "provider_empty_response")
                self.assertEqual(http.call_count, expected)
                source.assert_not_called()
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())

    def test_time_budget_reserves_direct_answer_without_changing_model_config(self):
        self.model.model_name = "glm-5.2"
        self.model.timeout_ms = 120000
        self.model.save()
        patches = self.run_chat([wire("openai_compatible", "get_data_freshness", {}),
                                 wire("openai_compatible", answer="按已取得数据回答。")])
        with patches[0], patches[1] as source, patches[2], patches[3] as http, patch.object(chat.transport, "remaining_budget", side_effect=[260, 15]):
            chat.answer({"clientRequestId": "time-finalize", "message": "查询"}, self.owner, "time-finalize")
            self.assertEqual(source.call_count, 1)
            self.assertNotIn("tools", http.call_args.args[1])
            self.assertNotIn("thinking", http.call_args.args[1])

    def test_network_unknown_invalid_json_and_filter_finish_are_never_replayed(self):
        self.model.model_name = "glm-5.2"
        self.model.save()
        failures = [AiError("timeout", "provider_timeout", 503), AiError("json", "invalid_provider_response", 503),
                    {"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]}]
        for i, failure in enumerate(failures):
            patches = self.run_chat([failure])
            with patches[0], patches[1] as source, patches[2], patches[3] as http:
                with self.assertRaises(AiError):
                    chat.answer({"clientRequestId": f"no-retry-{i}", "message": "查询"}, self.owner, f"no-retry-{i}")
                self.assertEqual(http.call_count, 1)
                source.assert_not_called()

    def test_finalization_still_checks_paid_quota_and_audit(self):
        self.model.model_name = "glm-5.2"
        self.model.save()
        empty = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        patches = self.run_chat([empty, wire("openai_compatible", answer="不应调用")])
        with patches[0], patches[1], patches[2], patches[3] as http, patch.object(chat, "dispatch_budget", side_effect=[None, None, AiError("quota", "ai_chat_quota_exceeded", 429)]):
            with self.assertRaises(AiError) as caught:
                chat.answer({"clientRequestId": "quota-finalize", "message": "查询"}, self.owner, "quota-finalize")
            self.assertEqual(caught.exception.code, "ai_chat_quota_exceeded")
            self.assertEqual(http.call_count, 1)
        self.assertFalse(m.AiConversationMessages.objects.filter(role="assistant").exists())

    def test_finalization_is_closed_on_failed_audit_or_insufficient_time(self):
        self.model.model_name = "glm-5.2"
        self.model.save()
        empty = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        for gate in ("audit", "time"):
            real_audit = chat.audit
            def checked_audit(*args, **kwargs):
                if gate == "audit" and args[3] == "failed":
                    raise AiError("audit unavailable", "service_unavailable", 503)
                return real_audit(*args, **kwargs)
            patches = self.run_chat([empty])
            with patches[0], patches[1], patches[2], patches[3] as http, patch.object(chat, "audit", side_effect=checked_audit), patch.object(chat.transport, "remaining_budget", side_effect=[260, 14]):
                with self.assertRaises(AiError):
                    chat.answer({"clientRequestId": f"closed-{gate}", "message": "查询"}, self.owner, f"closed-{gate}")
                self.assertEqual(http.call_count, 1)

    def test_unknown_finish_reason_does_not_trigger_finalization(self):
        empty = {"choices": [{"finish_reason": "unknown", "message": {"content": ""}}]}
        patches = self.run_chat([empty])
        with patches[0], patches[1], patches[2], patches[3] as http:
            with self.assertRaises(AiError):
                chat.answer({"clientRequestId": "other-provider", "message": "查询"}, self.owner, "other-provider")
            self.assertEqual(http.call_count, 1)
            self.assertNotIn("thinking", http.call_args.args[1])
