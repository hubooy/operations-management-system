"""Dedicated scheduled-delivery process; no Stream connection or personal DWS login."""
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from ai_assistant import dingtalk, dingtalk_settings, dingtalk_schedules, dingtalk_transport as platform
from ai_assistant.policy import AiError, authority


class Command(BaseCommand):
    help = "独立执行 AI 钉钉定时任务；不接收 Stream 消息，不补发过期执行槽"

    def add_arguments(self, parser):
        parser.add_argument("--config", required=True)
        parser.add_argument("--bot-credentials", required=True)
        parser.add_argument("--screenshot-profile", default="")

    def run_schedules(self, reader):
        while True:
            dingtalk_schedules.step(reader,
                lambda session, content, before_send=None: platform.send(reader, session, content, before_send=before_send),
                lambda session, raw, name, kind, caption="", before_send=None:
                    platform.send_media(reader, session, raw, name, kind, caption, before_send=before_send))
            time.sleep(.5)

    def handle(self, *args, **options):
        os.environ["TERUISI_DINGTALK_BOT_CREDENTIALS"] = options["bot_credentials"]
        os.environ["TERUISI_DINGTALK_SCREENSHOT_PROFILE"] = options["screenshot_profile"]
        try:
            if connection.vendor != "postgresql":
                raise AiError("定时执行器必须使用独立 AI PostgreSQL 写侧")
            authority()
            # A separate connection owns the lock across ORM reconnects. Revalidate it
            # at every policy/dispatch check; losing this connection stops dispatch.
            with connection.Database.connect(**connection.get_connection_params()) as lease:
                lease.autocommit = True
                # Keep the old combined receiver's key: an old executable cannot
                # consume schedules alongside this new independent executor.
                if lease.execute("SELECT pg_try_advisory_lock(841327,1909)").fetchone() != (True,):
                    raise AiError("已有定时执行器或旧版合并接收器，拒绝重复执行")
                def reader():
                    lease.execute("SELECT 1").fetchone()
                    return dingtalk_settings.effective(dingtalk.load_config(options["config"]))
                config = reader()
                platform.credentials(config)  # Local bound DPAPI, no Stream/DWS/network gate.
                dingtalk_schedules.recover_interrupted()
                self.stdout.write('{"status":"started","worker":"schedules"}')
                self.run_schedules(reader)
        except KeyboardInterrupt:
            self.stdout.write('{"status":"stopped"}')
        except Exception:
            raise CommandError("钉钉定时执行器未能运行；请核验企业机器人凭据及 AI 服务。未重发消息。") from None
