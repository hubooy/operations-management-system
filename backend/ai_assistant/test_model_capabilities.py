import importlib
import json
import socket
import threading
import time
from unittest.mock import patch
from django.apps import apps
from django.test import TestCase, SimpleTestCase, override_settings
from . import model_capabilities as c, provider, chat, transport, tests as support, models as m
from .policy import AiError, canonical, mutation
from .configuration import save_model, model_record


class CapabilityValidationTests(SimpleTestCase):
    def validate(self, value, **kw):
        return c.validate(value, **{"protocol":"openai_compatible", "max_tokens":65536, "reasoning_mode":"auto", **kw})

    def test_high_output_context_and_long_budget_are_explicit(self):
        self.assertEqual(self.validate({"contextWindowTokens":128000, "taskTimeoutMs":600000})["taskTimeoutMs"], 600000)
        for value in [{"contextWindowTokens":65536}, {"taskTimeoutMs":29999}, {"contextWindowTokens":2000001}, {"taskTimeoutMs":1000000001}, {"unknown":1}, {"includeStreamUsage":1}]:
            with self.subTest(value=value), self.assertRaises(AiError): self.validate(value)

    def test_protocol_mismatch_and_incompatible_thinking_are_rejected(self):
        for value, kw in [({"reasoningFormat":"anthropic_budget"}, {}), ({"reasoningFormat":"reasoning_effort"}, {"protocol":"anthropic"}), ({"reasoningFormat":"anthropic_budget", "temperatureMode":"default", "thinkingBudgetTokens":65536}, {"protocol":"anthropic"}), ({"reasoningFormat":"anthropic_adaptive"}, {"protocol":"anthropic"})]:
            with self.subTest(value=value), self.assertRaises(AiError): self.validate(value, **kw)

    def test_nested_budget_never_extends_the_channel_deadline(self):
        with transport.request_budget(30):
            before = transport.remaining_budget(1000)
            with transport.request_budget(900):
                transport.limit_request_budget(600)
                self.assertLessEqual(transport.remaining_budget(1000), before)

    def test_long_task_defaults_do_not_retain_a_hidden_single_call_limit(self):
        self.assertEqual(self.validate({})["taskTimeoutMs"], 1_000_000_000)
        self.assertEqual(self.validate({"taskTimeoutMs": 30000})["taskTimeoutMs"], 30000)
        with transport.request_budget(c.MAX_CHAT_SECONDS):
            transport.limit_request_budget(1_000_000)
            self.assertGreater(transport.remaining_budget(c.MAX_CHAT_SECONDS), 999_000)

    def test_cancellation_interrupts_a_socket_wait_without_waiting_for_task_timeout(self):
        waiting, peer = socket.socketpair()
        cancelled = threading.Event()
        def check():
            if cancelled.is_set(): raise AiError("已停止", "ai_request_cancelled", 499)
        try:
            waiting.settimeout(2)
            with transport.request_cancellation(check):
                stop = transport.watch_socket_cancellation(lambda: waiting)
                try:
                    cancelled.set()
                    started = time.monotonic()
                    with waiting.makefile("rb") as reader:
                        try:
                            self.assertEqual(reader.read(1), b"")
                        except TimeoutError:
                            self.fail("Cancellation did not wake the socket")
                        except OSError:
                            pass  # Windows reports the closed handle, Unix returns EOF.
                    self.assertLess(time.monotonic() - started, 1.5)
                finally:
                    stop()
        finally:
            waiting.close(); peer.close()

    def test_usage_absence_is_not_zero_and_only_bounded_numbers_are_exposed(self):
        self.assertEqual(c.usage_numbers({}), {"inputTokens":None,"outputTokens":None,"reasoningTokens":None})
        self.assertEqual(c.usage_numbers({"prompt_tokens":3,"completion_tokens":8,"completion_tokens_details":{"reasoning_tokens":5},"secret":"x"}), {"inputTokens":3,"outputTokens":8,"reasoningTokens":5})
        self.assertIsNone(c.usage_numbers({"input_tokens":True})["inputTokens"])


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class CapabilityChatTests(TestCase):
    user = support.AiDomainTests.user
    call = support.AiDomainTests.call
    setUp = support.AiDomainTests.setUp

    def test_new_model_defaults_and_legacy_edits(self):
        admin = self.user("defaults-admin@example.invalid", "admin", None)
        body = {"name":"默认模型", "modelName":"fixture", "baseUrl":self.model.base_url, "apiKey":"fixture-only", "protocol":"openai_compatible", "modelType":"text"}
        with patch("ai_assistant.configuration.encrypt", return_value="opaque-fixture"), mutation(admin):
            saved = save_model(body, admin)
        row = m.AiModels.objects.get(id=saved["id"])
        self.assertEqual(row.max_tokens, 65536)
        self.assertEqual(c.options(row)["taskTimeoutMs"], 1_000_000_000)
        self.model.timeout_ms = 3000; self.model.save()
        legacy_tokens = self.model.max_tokens
        with patch("ai_assistant.configuration.encrypt", return_value="opaque-fixture"), mutation(admin):
            save_model({**body, "id":self.model.id, "expectedVersion":self.model.version}, admin)
        self.model.refresh_from_db()
        self.assertEqual(self.model.timeout_ms, 3000)
        self.assertEqual(self.model.max_tokens, legacy_tokens)
        self.assertEqual(c.options(self.model)["taskTimeoutMs"], 260000)

    def test_model_tool_call_limit_accepts_300_and_rejects_301(self):
        admin = self.user("tool-budget-admin@example.invalid", "admin", None)
        body = {
            "id": self.model.id,
            "expectedVersion": self.model.version,
            "name": self.model.name,
            "modelName": self.model.model_name,
            "baseUrl": self.model.base_url,
            "protocol": self.model.protocol,
            "modelType": "text",
            "maxTotalToolCalls": 300,
        }
        with mutation(admin):
            saved = save_model(body, admin)
        self.model.refresh_from_db()
        self.assertEqual(self.model.max_total_tool_calls, 300)
        with self.assertRaises(AiError), mutation(admin):
            save_model({**body, "expectedVersion": saved["version"], "maxTotalToolCalls": 301}, admin)

    def test_budget_migration_promotes_only_models_at_former_ceiling(self):
        self.model.max_total_tool_calls = 74
        self.model.save(update_fields=["max_total_tool_calls"])
        unchanged = m.AiModels.objects.create(
            id="model-under-old-ceiling", name="under", protocol="openai_compatible",
            model_type="text", model_name="fixture", status="enabled",
            max_total_tool_calls=62,
        )
        vision = m.AiModels.objects.create(
            id="vision-at-old-ceiling", name="vision", protocol="openai_compatible",
            model_type="vision", model_name="fixture-vision", status="enabled",
            max_total_tool_calls=74,
        )
        migration = importlib.import_module("ai_assistant.migrations.0080_model_tool_budget_300")
        migration.raise_budget(apps, None)
        self.model.refresh_from_db()
        unchanged.refresh_from_db()
        vision.refresh_from_db()
        self.assertEqual(self.model.max_total_tool_calls, 300)
        self.assertEqual(unchanged.max_total_tool_calls, 62)
        self.assertEqual(vision.max_total_tool_calls, 300)

    def test_save_high_limits_cas_and_readback_preserve_options_without_touching_old_defaults(self):
        admin = self.user("capability-admin@example.invalid", "admin", None)
        body = {"id":self.model.id,"expectedVersion":1,"name":"测试模型","modelName":"fixture","protocol":"openai_compatible","modelType":"vision","maxTokens":65536,"timeoutMs":300000,"generationOptions":{"contextWindowTokens":256000,"taskTimeoutMs":600000,"temperatureMode":"default"}}
        with mutation(admin): save_model(body, admin)
        self.model.refresh_from_db()
        self.assertEqual(self.model.max_tokens,65536)
        self.assertEqual(model_record(self.model)["generationOptions"]["taskTimeoutMs"],600000)
        with self.assertRaises(AiError), mutation(admin): save_model(body, admin)
        with self.assertRaises(AiError), mutation(self.owner): save_model({**body,"expectedVersion":2},self.owner)

    def test_request_parameters_and_stream_usage_follow_explicit_format(self):
        self.model.max_tokens=65536
        self.model.generation_options_json=canonical({"contextWindowTokens":256000,"temperatureMode":"default","reasoningFormat":"reasoning_effort","reasoningEffort":"high","outputTokenParameter":"max_completion_tokens","includeStreamUsage":True})
        seen=[]
        def request(url, body, headers, **kw):
            seen.append(body)
            self.assertEqual(kw["timeout"], 1_000_000)
            return {"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":20}}
        self.model.timeout_ms = 3000
        self.model.generation_options_json = canonical({**json.loads(self.model.generation_options_json), "taskTimeoutMs":1_000_000_000})
        with patch.object(provider,"decrypt",return_value="fixture"),patch.object(provider,"bounded_sse",side_effect=request):
            result=provider.turn(self.model,[{"role":"user","content":"问题"}],"system",[],on_text=lambda _:None)
        self.assertEqual(seen[0]["max_completion_tokens"],65536)
        self.assertNotIn("max_tokens",seen[0]); self.assertNotIn("temperature",seen[0]); self.assertNotIn("thinking",seen[0])
        self.assertEqual(seen[0]["reasoning_effort"],"high")
        self.assertEqual(seen[0]["stream_options"],{"include_usage":True})
        self.assertEqual(result["stopReason"],"stop")

    def test_anthropic_budget_and_adaptive_send_only_the_chosen_contract(self):
        self.model.protocol="anthropic";self.model.max_tokens=16000
        for mode in ["anthropic_budget","anthropic_adaptive"]:
            self.model.generation_options_json=canonical({"reasoningFormat":mode,"temperatureMode":"default","thinkingBudgetTokens":8000})
            values=c.provider_parameters(self.model)
            self.assertNotIn("temperature",values)
            self.assertEqual(values["thinking"],{"type":"enabled","budget_tokens":8000} if mode=="anthropic_budget" else {"type":"adaptive"})

    def test_context_discards_complete_old_turns_and_refuses_oversized_live_tools(self):
        self.model.generation_options_json=canonical({"contextWindowTokens":8192})
        self.model.max_tokens=1024
        transcript=[{"role":"user","content":"旧"*8000},{"role":"assistant","content":"旧回答"},{"role":"user","content":"当前问题"}]
        kept,info=c.fit_context(self.model,transcript,"system",[])
        self.assertEqual(kept,[transcript[-1]]);self.assertEqual(info["droppedMessages"],2)
        with self.assertRaises(AiError): c.fit_context(self.model,[transcript[-1],{"role":"tool","content":"大"*10000}],"system",[])

    def test_long_answer_survives_storage_readback_and_has_real_usage(self):
        answer="回答"*30000
        result={"text":answer,"calls":[],"frame":{"role":"assistant","content":answer},"usage":{"prompt_tokens":12,"completion_tokens":30000},"stopReason":"length","truncated":True}
        with patch.object(chat.transport,"catalog",return_value=[]),patch.object(provider,"turn",return_value=result) as dispatch:
            body={"clientRequestId":"long-capability","message":"请给详细分析"}
            reply=chat.answer(body,self.owner,"capability")
            self.assertEqual(reply["reply"],answer)
            self.assertEqual(reply["execution"]["outputTokens"],30000)
            self.assertTrue(reply["execution"]["outputTruncated"])
            self.assertEqual(chat.answer(body,self.owner,"replay"),reply)
            self.assertEqual(dispatch.call_count,1)
        listed=chat.messages({"conversationId":reply["conversationId"]},self.owner)
        self.assertTrue(listed["items"][-1]["contentTruncated"])
        full=chat.messages({"conversationId":reply["conversationId"],"messageId":reply["assistantMessageId"]},self.owner)
        self.assertEqual(full["items"][0]["content"],answer)
        self.assertFalse(full["items"][0]["contentTruncated"])
        self.assertEqual(full["items"][0]["execution"],reply["execution"])
        with self.assertRaises(AiError): chat.messages({"conversationId":reply["conversationId"],"messageId":reply["assistantMessageId"]},self.other)

    def test_more_than_twenty_history_messages_are_available_before_budgeting(self):
        conv=m.AiConversations.objects.create(id="history-budget",title="历史",created_by=self.owner.email)
        for i in range(30): chat.append(conv.id,"user" if i%2==0 else "assistant",str(i))
        self.assertEqual(len(chat._context(conv,self.owner,"问题",private_context=False)),30)
