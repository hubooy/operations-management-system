import asyncio
import importlib.util
import json
from contextlib import ExitStack
from io import StringIO
from unittest import skipUnless
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase
from .management.commands import dingtalk_ask as receiver
from .policy import AiError


class DingTalkIngressTests(SimpleTestCase):
    def setUp(self):
        self.output = StringIO()
        self.errors = StringIO()
        self.command = receiver.Command(stdout=self.output, stderr=self.errors)

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_accepted_logs_no_message_or_receipt_content(self):
        data = {"text": {"content": "private-prompt"}, "sessionWebhook": "private-webhook"}
        with patch.object(receiver.service, "accept", return_value="private-receipt") as accept:
            self.assertEqual(self.command.accept_message(lambda: {}, data), (200, "accepted"))
        accept.assert_called_once_with({}, data)
        self.assertEqual(self.events()[0]["event"], "callback_accepted")
        self.assertNotIn("private", self.output.getvalue())

    def test_permanent_rejections_keep_ack_and_never_log_exception_text(self):
        for status in (400, 403, 409, 413):
            with self.subTest(status=status), patch.object(receiver.service, "accept",
                    side_effect=AiError("private-message", "dingtalk_identity_mismatch", status)):
                self.assertEqual(self.command.accept_message(lambda: {}, {}), (200, "ignored"))
        self.assertTrue(all(e["event"] == "callback_rejected" and not e["retryable"] for e in self.events()))
        self.assertNotIn("private", self.output.getvalue())

    def test_configuration_failure_is_distinct_and_does_not_accept(self):
        with patch.object(receiver.service, "accept") as accept:
            self.assertEqual(self.command.accept_message(Mock(side_effect=AiError("private", status=403)), {}), (200, "ignored"))
        accept.assert_not_called()
        self.assertEqual(self.events()[0]["stage"], "configuration")

    def test_transient_and_unknown_errors_preserve_retry_ack_and_redact_codes(self):
        for error in (AiError("private", "ai_chat_quota_exceeded", 429),
                      AiError("private", "private-exception-code", 503), RuntimeError("private-ticket")):
            with patch.object(receiver.service, "accept", side_effect=error):
                self.assertEqual(self.command.accept_message(lambda: {}, {}), (503, "unavailable"))
        self.assertTrue(all(e["event"] == "callback_unavailable" and e["retryable"] for e in self.events()))
        self.assertNotIn("private", self.output.getvalue())

    def test_startup_retries_transient_platform_validation_before_adoption(self):
        config = {"enabled": True, "bindings": [{"senderId": "staff"}]}
        cursor = Mock()
        cursor.fetchone.return_value = (True,)
        context = Mock()
        context.__enter__ = Mock(return_value=cursor)
        context.__exit__ = Mock(return_value=False)
        database = Mock(vendor="postgresql")
        database.cursor.return_value = context
        missing = Mock()
        missing.exists.return_value = False
        run_stream = AsyncMock()
        credentials = Mock(side_effect=[
            AiError("private-network", "channel_unavailable", 503),
            AiError("private-network", "channel_unavailable", 503),
            ("fixture", "fixture"),
        ])
        with ExitStack() as stack:
            for obj, attr, value in (
                (receiver, "connection", database),
                (receiver, "authority", Mock()),
                (receiver.time, "sleep", Mock()),
                (receiver.service, "load_config", Mock(return_value=config)),
                (receiver.service.m.AiDingTalkSettings.objects, "filter", Mock(return_value=missing)),
                (receiver.platform, "credentials", credentials),
                (receiver.dingtalk_settings, "initialize", Mock()),
                (receiver.dingtalk_settings, "effective", Mock(return_value=config)),
                (receiver.service, "principal_for", Mock()),
                (receiver.service, "recover_interrupted", Mock()),
                (self.command, "run_stream", run_stream),
            ):
                stack.enter_context(patch.object(obj, attr, value))
            self.command.handle(config="fixture", check=False, bot_credentials="fixture")
        self.assertEqual(credentials.call_count, 3)
        self.assertEqual(self.output.getvalue().count('"status":"recovering"'), 2)
        self.assertEqual(self.errors.getvalue().count("stream_unavailable"), 2)
        run_stream.assert_awaited_once()
        self.assertNotIn("private", self.output.getvalue() + self.errors.getvalue())

    @skipUnless(importlib.util.find_spec("dingtalk_stream"), "optional Stream SDK not installed")
    def test_real_sdk_callback_routes_on_ingress_thread_and_returns_ack(self):
        async def run():
            finished = asyncio.Event()
            received = []
            raw = json.dumps({"type": "CALLBACK", "headers": {
                "topic": "/v1.0/im/bot/messages/get", "messageId": "fixture-message"},
                "data": json.dumps({"text": {"content": "private-prompt"}})})

            class Socket:
                delivered = False

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if not self.delivered:
                        self.delivered = True
                        return raw
                    await asyncio.Event().wait()

                async def send(self, value):
                    received.append(json.loads(value))
                    finished.set()

            def accept(config, data):
                # An ORM call on the event-loop thread would be invalid.
                with self.assertRaises(RuntimeError):
                    asyncio.get_running_loop()
                self.assertEqual(data["text"]["content"], "private-prompt")
                return "private-receipt"

            with ExitStack() as stack:
                for obj, attr, value in (
                    (receiver, "close_old_connections", Mock()),
                    (receiver.platform, "credentials", Mock(return_value=("fixture", "fixture"))),
                    (receiver.platform, "open_stream", Mock(return_value={"endpoint": "wss://wss-open-connection.dingtalk.com/connect", "ticket": "fixture"})),
                    (receiver.platform, "stream_addresses", Mock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])),
                    (receiver.service, "step", Mock(return_value=False)),
                    (receiver.service, "accept", Mock(side_effect=accept)),
                ):
                    stack.enter_context(patch.object(obj, attr, value))
                stack.enter_context(patch("websockets.connect", return_value=Socket()))
                task = asyncio.create_task(self.command.run_stream(lambda: {"enabled": True}))
                try:
                    await asyncio.wait_for(finished.wait(), 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(received[0]["code"], 200)
            self.assertEqual(json.loads(received[0]["data"])["response"], "accepted")

        asyncio.run(run())
        self.assertEqual([e["event"] for e in self.events() if "event" in e],
                         ["callback_received", "callback_accepted"])
        self.assertNotIn("private", self.output.getvalue())

    @skipUnless(importlib.util.find_spec("dingtalk_stream"), "optional Stream SDK not installed")
    def test_repeated_stream_failures_preserve_existing_inbound_retry_only(self):
        async def run():
            real_sleep = asyncio.sleep
            opened = Mock(side_effect=RuntimeError("private-ticket"))
            incoming = Mock(return_value=False)

            async def fast_sleep(_delay):
                await real_sleep(0)

            with ExitStack() as stack:
                for obj, attr, value in (
                    (receiver, "close_old_connections", Mock()),
                    (receiver.platform, "credentials", Mock(return_value=("fixture", "fixture"))),
                    (receiver.platform, "open_stream", opened),
                    (receiver.service, "step", incoming),
                ):
                    stack.enter_context(patch.object(obj, attr, value))
                stack.enter_context(patch.object(receiver.asyncio, "sleep", side_effect=fast_sleep))
                task = asyncio.create_task(self.command.run_stream(lambda: {"enabled": True}))
                try:
                    for _ in range(1000):
                        if self.errors.getvalue().count("stream_unavailable") >= 6 and incoming.call_count:
                            break
                        await real_sleep(0.001)
                    self.assertGreaterEqual(opened.call_count, 6)
                    self.assertGreater(incoming.call_count, 0)
                    self.assertFalse(task.done())
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(run())
        states = [json.loads(line).get("status") for line in self.output.getvalue().splitlines()]
        self.assertIn("recovering", states)
        self.assertGreaterEqual(self.errors.getvalue().count("stream_unavailable"), 6)
        self.assertNotIn("private", self.output.getvalue() + self.errors.getvalue())
