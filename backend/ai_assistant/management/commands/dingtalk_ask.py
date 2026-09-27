"""Foreground Stream worker, explicitly started by the guarded runtime operator."""
import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import quote_plus, urlsplit
from django.utils import timezone
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, close_old_connections
from ai_assistant import dingtalk as service, dingtalk_transport as platform, dingtalk_settings
from ai_assistant.policy import AiError, authority


class Command(BaseCommand):
    help = "运行志高助手只读问数 Stream 接收器；--check 仅核验配置与现有身份"

    def add_arguments(self, parser):
        parser.add_argument("--config", required=True)
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--bot-credentials", required=True)

    def ingress_event(self, event, **values):
        # Only call with fixed labels. Never include SDK bodies, identifiers,
        # exception messages, credentials, webhooks or connection tickets.
        self.stdout.write(json.dumps({"event": event, "at": timezone.now().isoformat(), **values}))

    def accept_message(self, reader, data):
        stage = "configuration"
        try:
            config = reader()
            stage = "acceptance"
            service.accept(config, data)
            self.ingress_event("callback_accepted")
            return 200, "accepted"
        except AiError as error:
            permanent = error.status in (400, 403, 409, 413)
            reasons = {
                "dingtalk_invalid_envelope", "dingtalk_identity_mismatch",
                "dingtalk_sender_unbound", "dingtalk_unsupported_message",
                "dingtalk_group_not_allowed", "dingtalk_invalid_text",
                "dingtalk_message_expired", "invalid_request", "access_denied",
                "conflict", "payload_too_large", "ai_chat_quota_exceeded",
            }
            self.ingress_event("callback_rejected" if permanent else "callback_unavailable",
                stage=stage, reason=error.code if error.code in reasons else "policy_error",
                retryable=not permanent)
            return (200, "ignored") if permanent else (503, "unavailable")
        except Exception:
            self.ingress_event("callback_unavailable", stage=stage, reason="internal_error", retryable=True)
            return 503, "unavailable"

    def handle(self, *args, **options):
        os.environ["TERUISI_DINGTALK_BOT_CREDENTIALS"] = options["bot_credentials"]
        reader = lambda: dingtalk_settings.effective(service.load_config(options["config"]))
        try:
            base = service.load_config(options["config"])
            if options["check"]:
                config = reader() if service.m.AiDingTalkSettings.objects.filter(pk=1).exists() else base
                token = platform.access_token(config)
                for group in config["groups"]:
                    platform.verify_group(config, group, token)
                for binding in config["bindings"]:
                    service.principal_for({**config, "enabled": True}, binding["senderId"])
                self.stdout.write('{"status":"verified","connected":false,"sent":0}')
                return
            if not base["enabled"] or connection.vendor != "postgresql":
                raise AiError("运行监听需显式启用配置和独立 AI PostgreSQL 写侧")
            authority()
            # Session lock belongs to this dedicated connection until process exit.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(841327, 1910)")
                if cursor.fetchone() != (True,):
                    raise AiError("已有钉钉问数接收器，拒绝重复启动")
            try:
                config = reader() if service.m.AiDingTalkSettings.objects.filter(pk=1).exists() else base
                failures = 0
                while True:
                    try:
                        platform.credentials(config)
                        break
                    except AiError as error:
                        if error.status < 500 and error.status != 429:
                            raise
                        failures = min(failures + 1, 9)
                        self.stdout.write('{"status":"recovering"}')
                        self.stderr.write('{"code":"stream_unavailable"}')
                        time.sleep(min(300, 2 ** min(failures, 8)))
                dingtalk_settings.initialize(base)
                config = reader()
                for binding in config["bindings"]:
                    service.principal_for({**config, "enabled": True}, binding["senderId"])
                service.recover_interrupted()
                self.stdout.write('{"status":"starting","replyMode":"source","queryScope":"authorized_system_modules"}')
                asyncio.run(self.run_stream(reader))
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(841327, 1910)")
        except KeyboardInterrupt:
            self.stdout.write('{"status":"stopped"}')
        except Exception:
            # SDK exceptions can contain credentials, tickets, message bodies or URLs.
            raise CommandError("钉钉问数未能运行；请核验企业机器人凭据、配置和本机 AI 服务。未自动重发消息。") from None

    async def run_stream(self, reader):
        import dingtalk_stream as sdk
        import websockets
        silent = logging.getLogger("teruisi.dingtalk.silent")
        silent.handlers = [logging.NullHandler()]
        silent.propagate = False
        silent.disabled = True
        loop = asyncio.get_running_loop()
        stopping = Event()
        original_reader = reader
        def reader():
            if stopping.is_set():
                raise AiError("接收器正在停止", "access_denied", 403)
            return original_reader()
        ingress = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ding-ingress")
        worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ding-ai")
        def db_call(fn):
            close_old_connections()
            try:
                return fn()
            finally:
                close_old_connections()
        command = self
        class Handler(sdk.CallbackHandler):
            async def process(self, message):
                command.ingress_event("callback_received")
                try:
                    return await loop.run_in_executor(ingress,
                        lambda: db_call(lambda: command.accept_message(reader, message.data)))
                except Exception:
                    command.ingress_event("callback_unavailable", stage="database_context",
                        reason="internal_error", retryable=True)
                    return 503, "unavailable"
        async def work():
            while True:
                await loop.run_in_executor(worker, lambda: db_call(lambda: service.step(reader, lambda session, content: platform.send(reader, session, content))))
                await asyncio.sleep(0.5)
        async def listen():
            failures = 0
            while True:
                # ORM policy reads must run outside the asyncio thread. Keep the
                # listener alive when disabled so a settings save can re-enable it.
                config = await loop.run_in_executor(ingress, lambda: db_call(reader))
                if not config["enabled"]:
                    await asyncio.sleep(2)
                    continue
                try:
                    key, secret = await loop.run_in_executor(
                        ingress, lambda: db_call(lambda: platform.credentials(reader())))
                    client = sdk.DingTalkStreamClient(sdk.Credential(key, secret), logger=silent)
                    handler = Handler()
                    handler.logger = silent
                    client.system_handler.logger = silent
                    client.event_handler.logger = silent
                    client.register_callback_handler("/v1.0/im/bot/messages/get", handler)
                    # Bound SDK connection creation; the stock helper has no HTTP timeout.
                    reply = await loop.run_in_executor(ingress, lambda: platform.open_stream(key, secret))
                    endpoint = urlsplit(reply.get("endpoint", ""))
                    if endpoint.scheme != "wss" or endpoint.hostname != "wss-open-connection.dingtalk.com" or endpoint.port not in (None, 443) or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path != "/connect":
                        raise AiError("Stream endpoint 无效")
                    ticket = service.opaque(reply.get("ticket"), "ticket", 4096)
                    addresses = await loop.run_in_executor(ingress, lambda: platform.stream_addresses(platform.STREAM_SOCKET))
                    async with websockets.connect(reply["endpoint"] + "?ticket=" + quote_plus(ticket), host=addresses[0][4][0], port=443, proxy=None, server_hostname=endpoint.hostname, open_timeout=15, close_timeout=5, max_size=32768, max_queue=16, ping_interval=30, ping_timeout=30, logger=silent) as websocket:
                        client.websocket = websocket
                        self.stdout.write('{"status":"connected"}')
                        failures = 0
                        async for raw in websocket:
                            if await client.route_message(json.loads(raw)) == client.TAG_DISCONNECT:
                                break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failures = min(failures + 1, 9)
                    self.stdout.write('{"status":"recovering"}')
                    self.stderr.write('{"code":"stream_unavailable"}')
                # Preserve the existing inbound reconnect policy. Each attempt
                # re-reads the bound enterprise credentials; schedules run elsewhere.
                await asyncio.sleep(min(300, 2 ** min(failures, 8)))
        tasks = [asyncio.create_task(work()), asyncio.create_task(listen())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            stopping.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Hold the PostgreSQL singleton until all synchronous work is gone.
            # Cancelling an asyncio Future alone cannot stop its worker thread.
            ingress.shutdown(wait=True, cancel_futures=True)
            worker.shutdown(wait=True, cancel_futures=True)
