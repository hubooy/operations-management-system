import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch, Mock
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from . import chat, provider, transport, tests as support, models as m
from .provider_stream import ProviderStream
from .chat_stream import ChatStream
from .policy import AiError, canonical
from .transport import signed_headers


def event(value):
    return ("data: " + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) + "\r\n\r\n").encode()


def openai(delta=None, reason=None):
    return {"id": "stream-fixture", "choices": [{"index": 0, "delta": delta or {}, "finish_reason": reason}]}


class ProviderStreamTests(SimpleTestCase):
    def test_utf8_bytes_reasoning_and_tool_arguments_are_not_published(self):
        published = []
        stream = ProviderStream("openai_compatible", published.append)
        wire = b"".join([
            event(openai({"reasoning_content": "private-reasoning"})),
            event(openai({"content": "中文回答"})),
            event(openai({"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "lookup", "arguments": '{"a":'}}]})),
            event(openai({"tool_calls": [{"index": 0, "function": {"arguments": '"秘密"}'}}]})),
            event(openai(reason="tool_calls")), event("[DONE]"),
        ])
        for byte in wire:
            stream.feed(bytes([byte]))
        result = stream.finish()["choices"][0]["message"]
        self.assertEqual(published, ["中文回答"])
        self.assertEqual(result["reasoning_content"], "private-reasoning")
        self.assertEqual(json.loads(result["tool_calls"][0]["function"]["arguments"]), {"a": "秘密"})

    def test_no_terminal_invalid_json_error_and_truncated_utf8_fail_closed(self):
        for chunks in [[event(openai({"content": "partial"}))], [event("invalid")], [event({"error": {"message": "secret"}})], [event(openai(reason="content_filter"))], [event("[DONE]")], [b"data: \xe4"]]:
            with self.subTest(chunks=chunks), self.assertRaises((AiError, UnicodeDecodeError)):
                stream = ProviderStream("openai_compatible", lambda _: None)
                for chunk in chunks:
                    stream.feed(chunk)
                stream.finish()

    def test_anthropic_blocks_signatures_and_input_json_are_assembled(self):
        seen = []
        stream = ProviderStream("anthropic", seen.append)
        events = [
            {"type": "message_start", "message": {"id": "a", "usage": {"input_tokens": 10}, "content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signature"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "你好"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "t", "name": "lookup", "input": {}}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"q":'}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '"值"}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 30}},
            {"type": "message_stop"},
        ]
        for item in events:
            stream.feed(event(item))
        result = stream.finish()
        self.assertEqual(seen, ["你好"])
        self.assertEqual(result["content"][0]["signature"], "signature")
        self.assertEqual(result["content"][2]["input"], {"q": "值"})
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 30})

    def test_anthropic_unclosed_or_mutated_closed_block_is_rejected(self):
        for late in [False, True]:
            stream = ProviderStream("anthropic", lambda _: None)
            stream.feed(event({"type": "message_start", "message": {}}))
            stream.feed(event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
            with self.assertRaises(AiError):
                if late:
                    stream.feed(event({"type": "content_block_stop", "index": 0}))
                    stream.feed(event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "late"}}))
                else:
                    stream.feed(event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}))
                    stream.feed(event({"type": "message_stop"}))

    @override_settings(DJANGO_ENVIRONMENT="development")
    def test_real_http_stream_delivers_text_before_server_is_allowed_to_finish(self):
        delta_received = threading.Event()
        body_seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body_seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(event(openai({"content": "先到达的正文"}))); self.wfile.flush()
                if delta_received.wait(2):
                    self.wfile.write(event(openai(reason="stop")) + event("[DONE]")); self.wfile.flush()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            def received(value):
                self.assertEqual(value, "先到达的正文"); delta_received.set()
            with patch.dict("os.environ", {"AI_ALLOW_LOCAL_MODEL_ENDPOINTS": "true"}):
                result = transport.bounded_sse(f"http://127.0.0.1:{server.server_port}/v1/chat/completions", {"model": "fixture"}, {}, timeout=4, collector=ProviderStream("openai_compatible", received))
            self.assertEqual(result["choices"][0]["message"]["content"], "先到达的正文")
            self.assertTrue(body_seen[0]["stream"])
            self.assertEqual(len(body_seen), 1)
        finally:
            server.shutdown(); server.server_close(); worker.join(2)

    def test_stream_permit_is_retained_after_disconnect_until_producer_stops(self):
        allow_exit = threading.Event()
        entered = threading.Event()
        release = Mock()
        def answer(*args, on_event):
            on_event("delta", {"content": "partial"}); entered.set()
            allow_exit.wait(2)
            return {"reply": "done"}
        session = ChatStream({}, None, "fixture")
        session.release = release
        with patch("ai_assistant.chat_stream.chat.answer", side_effect=answer), patch("ai_assistant.chat_stream.connections.close_all"):
            iterator = iter(session)
            self.assertIn(b"delta", next(iterator))
            self.assertTrue(entered.wait(1))
            iterator.close(); release.assert_not_called()
            allow_exit.set(); session.thread.join(2)
            release.assert_called_once()
            session.close(); release.assert_called_once()

    def test_stream_closed_before_iteration_never_dispatches(self):
        session = ChatStream({}, None, "fixture")
        session.release = Mock()
        with patch("ai_assistant.chat_stream.chat.answer") as answer:
            session.close(); session.close()
            session.release_once()
            answer.assert_not_called()


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class StreamChatReceiptTests(TestCase):
    user = support.AiDomainTests.user
    def setUp(self):
        support.AiDomainTests.setUp(self)

    def streamed_request(self, url, body, headers, *, timeout, collector):
        for chunk in [event(openai({"reasoning_content": "private-thinking"})), event(openai({"content": "有来源的回答"})), event(openai(reason="stop")), event("[DONE]")]:
            collector.feed(chunk)
        return collector.finish()

    def test_streamed_answer_persists_once_and_same_receipt_replays_without_provider(self):
        seen = []
        with patch.object(chat.transport, "catalog", return_value=[]), patch.object(provider, "decrypt", return_value="fixture"), patch.object(provider, "bounded_sse", side_effect=self.streamed_request) as dispatch:
            body = {"clientRequestId": "stream-receipt", "message": "业务问题"}
            result = chat.answer(body, self.owner, "fixture", on_event=lambda kind, value: seen.append((kind,value)))
            self.assertEqual(result["reply"], "有来源的回答")
            self.assertEqual(m.AiChatRequestReceipts.objects.get(client_request_id="stream-receipt").status, "succeeded")
            self.assertEqual(chat.answer(body, self.owner, "fixture-replay", on_event=lambda *_: None), result)
            self.assertEqual(dispatch.call_count, 1)
            self.assertEqual(m.AiConversationMessages.objects.filter(role="assistant").count(), 1)
            self.assertNotIn("private-thinking", canonical(seen))

    def test_stream_failure_leaves_unknown_receipt_and_does_not_replay(self):
        with patch.object(chat.transport, "catalog", return_value=[]), patch.object(provider, "decrypt", return_value="fixture"), patch.object(provider, "bounded_sse", side_effect=AiError("断线", "provider_unavailable", 503)) as dispatch:
            body = {"clientRequestId": "stream-failed", "message": "业务问题"}
            with self.assertRaises(AiError): chat.answer(body, self.owner, "fixture", on_event=lambda *_: None)
            self.assertEqual(m.AiChatRequestReceipts.objects.get(client_request_id="stream-failed").status, "unknown")
            with self.assertRaises(AiError): chat.answer(body, self.owner, "fixture", on_event=lambda *_: None)
            self.assertEqual(dispatch.call_count, 1)
            self.assertEqual(m.AiConversationMessages.objects.filter(role="assistant").count(), 0)

    def test_viewer_and_cross_owner_are_rejected_before_stream_dispatch(self):
        with patch.object(chat.transport, "catalog", return_value=[]), patch.object(provider, "decrypt", return_value="fixture"), patch.object(provider, "bounded_sse", side_effect=self.streamed_request) as dispatch:
            with self.assertRaises(AiError): chat.answer({"clientRequestId":"viewer", "message":"问题"},self.viewer,"v",on_event=lambda *_:None)
            first = chat.answer({"clientRequestId":"owner", "message":"问题"},self.owner,"o",on_event=lambda *_:None)
            with self.assertRaises(AiError): chat.answer({"clientRequestId":"other", "message":"问题", "conversationId":first["conversationId"]},self.other,"x",on_event=lambda *_:None)
            self.assertEqual(dispatch.call_count,1)


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test",
                   DJANGO_INTERNAL_SECRET="A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz")
class StreamEndpointTransactionTests(TransactionTestCase):
    """Exercise real producer DB ownership, beyond mocked queue/parser tests."""
    user = support.AiDomainTests.user
    setUp = support.AiDomainTests.setUp
    streamed_request = StreamChatReceiptTests.streamed_request

    def test_signed_stream_commits_in_producer_before_terminal_receipt(self):
        body = {"clientRequestId": "threaded-stream-receipt", "message": "业务问题"}
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": "A-valid-isolated-signing-secret-13579-abcdefghijklmnopqrstuvwxyz"}), \
             patch.object(chat.transport, "catalog", return_value=[]), \
             patch.object(provider, "decrypt", return_value="fixture"), \
             patch.object(provider, "bounded_sse", side_effect=self.streamed_request) as dispatch:
            headers = signed_headers("/api/ai/chat", body, self.owner, "threaded-stream")
            headers["Accept"] = "text/event-stream"
            response = self.client.post("/api/ai/chat", canonical(body), content_type="application/json", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response["Content-Type"])
            wire = b"".join(response.streaming_content).decode()
            response.ai_stream.thread.join(3)
            self.assertFalse(response.ai_stream.thread.is_alive())
            self.assertIn("event: delta", wire)
            self.assertIn("event: done", wire)
            self.assertNotIn("event: failure", wire)
            receipt = m.AiChatRequestReceipts.objects.get(client_request_id=body["clientRequestId"])
            self.assertEqual(receipt.status, "succeeded")
            self.assertEqual(m.AiConversationMessages.objects.filter(role="assistant").count(), 1)
            self.assertEqual(dispatch.call_count, 1)
