"""Explicit adoption of existing enterprise application credentials, without delivery."""
from django.core.management.base import BaseCommand, CommandError
from ai_assistant import dingtalk, dingtalk_settings, dingtalk_bot_credentials
from ai_assistant.policy import authority


class Command(BaseCommand):
    help = "只读核验现有应用身份并创建 DPAPI 企业机器人凭据；不发送消息"

    def add_arguments(self, parser):
        parser.add_argument("--config", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--provision", action="store_true", required=True)

    def handle(self, *args, **options):
        try:
            authority()
            config = dingtalk_settings.effective(dingtalk.load_config(options["config"]))
            dingtalk_bot_credentials.provision(config, options["output"])
            self.stdout.write('{"status":"provisioned","sent":0}')
        except Exception:
            raise CommandError("企业机器人凭据采用失败；未覆盖现有凭据或发送消息") from None
