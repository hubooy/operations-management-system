from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sales.auth import Principal
from workflow.followup import (
    claim_weekly_delivery,
    finish_weekly_delivery,
    learn_product_line_codes,
    mark_weekly_delivery_sending,
    mark_weekly_delivery_uncertain,
    weekly_followup,
)
from workflow.models import NewProductWeeklyReportConfig
from workflow.weekly_report_image import render_weekly_report_html, render_weekly_report_png


MAX_DWS_OUTPUT_BYTES = 2 * 1024 * 1024
AUTOMATION_ACTOR = "new-product-weekly-report@local.system"
APPROVED_PREVIEW_HOST_SUFFIXES = (".dingtalk.com", ".aliyuncs.com", ".aliyun.com", ".alicdn.com")


def _records(value: object) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _records(nested)


def _field(record: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _exact_identity(
    payloads: Iterable[object],
    *,
    expected_name: str,
    name_fields: tuple[str, ...],
    id_fields: tuple[str, ...],
    label: str,
) -> tuple[str, dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for record in _records(payload):
            name = _field(record, name_fields)
            identity = _field(record, id_fields)
            if name == expected_name and identity:
                matches[identity] = record
    if len(matches) != 1:
        raise CommandError(f"{label}“{expected_name}”精确匹配到 {len(matches)} 个对象，拒绝发送")
    identity, record = next(iter(matches.items()))
    return identity, record


def _page_value(payload: object, field: str) -> object | None:
    for record in _records(payload):
        if field in record:
            return record[field]
    return None


def _dws_command() -> list[str]:
    if os.name != "nt":
        executable = shutil.which("dws")
        if not executable:
            raise CommandError("钉钉 dws 命令不可用")
        return [executable]
    launcher = shutil.which("dws.cmd") or shutil.which("dws.ps1")
    if not launcher:
        raise CommandError("钉钉 dws 命令不可用")
    npm_root = Path(launcher).parent
    cli = npm_root / "node_modules" / "dingtalk-workspace-cli" / "bin" / "dws.js"
    bundled_node = npm_root / "node.exe"
    node = str(bundled_node) if bundled_node.is_file() else shutil.which("node.exe")
    if not node or not cli.is_file():
        raise CommandError("钉钉 dws 命令不可用")
    return [node, str(cli)]


def _run_dws(args: list[str], *, timeout: int = 30) -> object:
    try:
        completed = subprocess.run(
            [*_dws_command(), *args, "--format", "json"],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommandError("钉钉 dws 命令不可用或执行超时") from error
    if len(completed.stdout) > MAX_DWS_OUTPUT_BYTES or len(completed.stderr) > MAX_DWS_OUTPUT_BYTES:
        raise CommandError("钉钉 dws 返回内容超出安全上限")
    if completed.returncode != 0:
        raise CommandError("钉钉 dws 命令执行失败")
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CommandError("钉钉 dws 未返回有效 JSON") from error


def _search_group(group_name: str) -> tuple[str, dict[str, Any]]:
    pages: list[object] = []
    cursor = "0"
    for _ in range(5):
        payload = _run_dws(["chat", "search", "--query", group_name, "--limit", "20", "--cursor", cursor])
        pages.append(payload)
        if _page_value(payload, "hasMore") is not True:
            break
        next_cursor = _page_value(payload, "nextCursor")
        if not isinstance(next_cursor, (str, int)) or str(next_cursor) == cursor:
            raise CommandError("钉钉群搜索分页游标无效")
        cursor = str(next_cursor)
    return _exact_identity(
        pages,
        expected_name=group_name,
        name_fields=("title", "name", "groupName", "conversationTitle"),
        id_fields=("openConversationId", "conversationId", "chatId"),
        label="钉钉群",
    )


def _search_robot(robot_name: str) -> tuple[str, dict[str, Any]]:
    pages: list[object] = []
    for page in range(1, 6):
        payload = _run_dws(["chat", "bot", "search", "--name", robot_name, "--page", str(page), "--size", "50"])
        pages.append(payload)
        has_more = _page_value(payload, "hasMore")
        if has_more is not True:
            break
    return _exact_identity(
        pages,
        expected_name=robot_name,
        name_fields=("robotName", "name", "title"),
        id_fields=("robotCode", "robot-code", "code"),
        label="钉钉机器人",
    )


def _assert_robot_in_group(group_id: str, robot_name: str, robot_code: str) -> None:
    payload = _run_dws(["chat", "group", "bots", "--group", group_id])
    matches = []
    for record in _records(payload):
        name = _field(record, ("robotName", "name", "title"))
        code = _field(record, ("robotCode", "robot-code", "code"))
        if code == robot_code or (name == robot_name and not code):
            matches.append(record)
    if not matches:
        raise CommandError(f"机器人“{robot_name}”尚未安装到指定钉钉群")


def _assert_send_receipt(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise CommandError("钉钉消息发送回执未确认成功")
    result = payload.get("result")
    if isinstance(result, dict) and result.get("success") is False:
        raise CommandError("钉钉消息发送回执标记失败")
    failure_values = {"failed", "failure", "error", "rejected"}
    for record in _records(payload):
        status = record.get("status")
        if isinstance(status, str) and status.strip().lower() in failure_values:
            raise CommandError("钉钉消息发送 ledger 包含失败目标")


def _unique_value(payload: object, names: tuple[str, ...], label: str) -> str:
    values = {
        value
        for record in _records(payload)
        if (value := _field(record, names))
    }
    if len(values) != 1:
        raise CommandError(f"{label}返回了 {len(values)} 个候选值")
    return next(iter(values))


def _drive_preview_url(upload_payload: object) -> tuple[str, object]:
    node_id = _unique_value(upload_payload, ("dentryUuid", "fileId", "nodeId"), "钉盘上传")
    info_payload = _run_dws(["drive", "info", "--node", node_id])
    preview_url = _unique_value([upload_payload, info_payload], ("docUrl", "previewUrl", "webUrl"), "钉盘预览地址")
    parsed = urlparse(preview_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment or not any(
        hostname == suffix[1:] or hostname.endswith(suffix) for suffix in APPROVED_PREVIEW_HOST_SUFFIXES
    ):
        raise CommandError("钉盘预览地址不在允许的 HTTPS 域名内")
    return preview_url, info_payload


def _due_now(config: NewProductWeeklyReportConfig, now: datetime) -> bool:
    scheduled_minutes = config.send_local_time.hour * 60 + config.send_local_time.minute
    current_minutes = now.hour * 60 + now.minute
    return now.weekday() == config.send_weekday and 0 <= current_minutes - scheduled_minutes < 10


class Command(BaseCommand):
    help = "Reconcile Jackyun new-product codes and safely preview or send the previous complete weekly report."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--send", action="store_true")
        parser.add_argument("--force", action="store_true", help="Bypass the configured local weekday/time gate; still requires enabled=true.")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_PROCESS_ROLE not in {"workflow_writer", "development"}:
            raise CommandError("新品周报命令只能由 workflow_writer 执行")
        dry_run = bool(options["dry_run"])
        send = bool(options["send"])
        force = bool(options["force"])
        if force and not send:
            raise CommandError("--force 只能与 --send 一起使用")

        principal = Principal(AUTOMATION_ACTOR, "新品周报自动任务", "admin", None)
        learned = learn_product_line_codes(principal)
        report = weekly_followup(embed_uploaded_images=True)
        config = NewProductWeeklyReportConfig.objects.get(id=1)
        local_now = datetime.now().astimezone()
        base = {
            "status": "ready",
            "localNow": local_now.isoformat(),
            "timezone": report["timezone"],
            "weekStart": report["weekStart"],
            "weekEnd": report["weekEnd"],
            "reportSha256": report["reportSha256"],
            "learning": learned,
            "enabled": config.enabled,
            "deliveryMode": "png_drive_preview_by_bot",
        }

        if not dry_run and not send:
            self.stdout.write(json.dumps({**base, "messageText": report["messageText"]}, ensure_ascii=False, separators=(",", ":")))
            return
        if not config.enabled:
            if send:
                self.stdout.write(json.dumps({**base, "status": "disabled"}, ensure_ascii=False, separators=(",", ":")))
                return
            raise CommandError("新品钉钉周报尚未启用")
        if send and not force and not _due_now(config, local_now):
            self.stdout.write(json.dumps({**base, "status": "not_due"}, ensure_ascii=False, separators=(",", ":")))
            return

        group_id, _group = _search_group(config.target_group_name)
        robot_code, _robot = _search_robot(config.robot_name)
        _assert_robot_in_group(group_id, config.robot_name, robot_code)
        title = "新品销售周报"
        _image_html, image_width, image_height = render_weekly_report_html(report)
        if dry_run:
            _run_dws([
                "chat", "+messages-send", "--as", "bot", "--robot-code", robot_code,
                "--group", group_id, "--title", title,
                "--markdown", "新品销售周报\n\n[周报预览图片将在正式投递时生成]",
                "--dry-run",
            ])
            self.stdout.write(json.dumps({
                **base, "status": "dry_run_ok", "groupVerified": True, "robotVerified": True,
                "imageVerified": True, "imageWidth": image_width, "imageHeight": image_height,
            }, ensure_ascii=False, separators=(",", ":")))
            return

        with tempfile.TemporaryDirectory(prefix="teruisi-new-product-weekly-report-") as temporary_directory:
            image_path = Path(temporary_directory) / f"新品销售周报-{report['weekStart']}-{report['weekEnd']}.png"
            image = render_weekly_report_png(report, image_path)
            delivery, claimed = claim_weekly_delivery(report, config, actor=AUTOMATION_ACTOR)
            if not claimed:
                self.stdout.write(json.dumps({**base, "status": f"already_{delivery.status}"}, ensure_ascii=False, separators=(",", ":")))
                return
            mark_weekly_delivery_sending(delivery.id)
            try:
                upload_receipt = _run_dws([
                    "drive", "upload", "--file", str(image_path), "--file-name", image_path.name,
                ], timeout=60)
                preview_url, preview_receipt = _drive_preview_url(upload_receipt)
                message = f"新品销售周报\n\n[查看周报预览图片]({preview_url})"
                send_receipt = _run_dws([
                    "chat", "+messages-send", "--as", "bot", "--robot-code", robot_code,
                    "--group", group_id, "--title", title, "--markdown", message, "--yes",
                ])
                _assert_send_receipt(send_receipt)
            except Exception as error:
                mark_weekly_delivery_uncertain(delivery.id, error_code=type(error).__name__)
                raise
            receipt_sha = hashlib.sha256(json.dumps(
                {"upload": upload_receipt, "preview": preview_receipt, "send": send_receipt},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            finish_weekly_delivery(delivery.id, provider_receipt=f"dws-sha256:{receipt_sha}")
            self.stdout.write(json.dumps({
                **base, "status": "delivered", "imageSha256": image["sha256"],
                "imageSizeBytes": image["sizeBytes"],
            }, ensure_ascii=False, separators=(",", ":")))
