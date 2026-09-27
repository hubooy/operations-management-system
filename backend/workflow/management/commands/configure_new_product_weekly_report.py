from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sales.auth import Principal
from workflow.followup import get_report_config, update_report_config


CONFIG_ACTOR = "new-product-weekly-report-config@local.system"


class Command(BaseCommand):
    help = "Idempotently enable or disable the governed new-product DingTalk weekly report."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--enable", action="store_true")
        mode.add_argument("--disable", action="store_true")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_PROCESS_ROLE not in {"workflow_writer", "development"}:
            raise CommandError("新品周报配置命令只能由 workflow_writer 执行")
        desired = bool(options["enable"])
        current = get_report_config()
        if bool(current["enabled"]) == desired:
            self.stdout.write(json.dumps({
                "status": "already_enabled" if desired else "already_disabled",
                "config": current,
            }, ensure_ascii=False, separators=(",", ":")))
            return
        principal = Principal(CONFIG_ACTOR, "新品周报本机配置", "admin", None)
        configured = update_report_config({
            "enabled": desired,
            "expectedVersion": current["version"],
        }, principal)
        self.stdout.write(json.dumps({
            "status": "enabled" if desired else "disabled",
            "config": configured,
        }, ensure_ascii=False, separators=(",", ":")))
