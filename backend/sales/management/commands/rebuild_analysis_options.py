"""Explicit one-shot ERP directory rebuild through the existing owning guards."""
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import DatabaseError, connection

from access_control.models import AppUser
from sales import analysis_options_projection as projection
from sales.auth import Principal


class Command(BaseCommand):
    help = "Explicitly rebuild ERP source identities using the current sales writer authority."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--actor-email", required=True, help="Existing active unrestricted administrator email")

    def handle(self, *args, **options):
        if settings.DJANGO_PROCESS_ROLE != "sales_writer" or settings.DJANGO_EXPECT_READ_ONLY:
            raise CommandError("ERP目录重建只能使用已配置的sales_writer进程。")
        if connection.in_atomic_block:
            raise CommandError("ERP目录准备必须在最外层事务之外。")
        email = options.get("actor_email")
        if type(email) is not str or not email.strip() or len(email) > 320:
            raise CommandError("必须显式提供有效的管理员邮箱。")
        email = email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError("必须显式提供有效的管理员邮箱。") from None
        try:
            # Only the existing five-column permission projection is needed.
            actor = AppUser.objects.filter(email=email).values("email", "role", "scope", "status", "version").first()
            if not actor or actor["role"] != "admin" or actor["scope"] is not None or actor["status"] != "active":
                raise CommandError("实际账号必须是当前启用且无范围限制的管理员。")
            principal = Principal(actor["email"], "", actor["role"], actor["scope"])
            # No transaction wrapper: prepare verifies authority/runtime and scans
            # outside the short publish transaction, which revalidates everything.
            prepared = projection.prepare_rebuild(principal)
            published = projection.publish_rebuild(prepared, principal)
        except projection.OptionsError as error:
            raise CommandError(str(error)) from None
        except DatabaseError:
            raise CommandError("ERP目录重建未完成，请检查受控数据库角色、迁移与运行状态；不要自动重试。") from None
        self.stdout.write(json.dumps({
            "generation": published["generation"],
            "identityCount": published["identityCount"],
            "coverageVerified": False,
        }, ensure_ascii=False, sort_keys=True))
