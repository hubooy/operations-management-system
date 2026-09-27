from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from typing import NoReturn

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .errors import InventoryApiError
from .models import ReplenishmentPlanItem
from .revisions import bump_revision
from .write_requests import lock_active_authority


MAX_DWS_OUTPUT_BYTES = 1_048_576
SYNC_LEASE = timedelta(minutes=5)
NAME_SPLIT_RE = re.compile(r"[,，;；/、]+")
RED_TAG_RE = re.compile(r"</?red>", re.I)
REPLENISHMENT_MARKER_VERSION = 2
logger = logging.getLogger(__name__)


def _dws_failure_details(stdout: str, stderr: str) -> tuple[str, str]:
    # Only classify known signals. Never expose CLI output: it can contain
    # tokens, profile identities, request arguments or returned business data.
    details: list[str] = []
    for output in (stdout, stderr):
        try:
            payload = json.loads(output)
        except (ValueError, RecursionError):
            details.append(output)
            continue
        if isinstance(payload, dict):
            for key in ("error", "code", "reason", "message", "error_reason", "error_category"):
                if key in payload:
                    details.append(json.dumps(payload[key], ensure_ascii=False))
    detail = "\n".join(details).lower()
    if any(signal in detail for signal in (
        "invalidparameter.authcode.notfound", "旧版登录态", "重新授权", "auth_required",
        "token_expired", "invalid_token", "invalid_grant",
    )):
        return "auth_required", "钉钉登录授权已失效，请管理员为系统绑定的钉钉账号重新授权后，再重试原备货计划。"
    if any(signal in detail for signal in ("permission_denied", "access_denied", "forbidden", "无权限", "权限不足")):
        return "permission_denied", "钉钉访问权限不足，请管理员检查系统绑定账号的表格访问权限和应用授权后，再重试原备货计划。"
    if any(signal in detail for signal in ("rate_limit", "too many requests", "限流", "请求过于频繁")):
        return "rate_limited", "钉钉请求过于频繁，请稍后重试原备货计划。"
    if any(signal in detail for signal in ("confirmation_required", "当前环境无法交互确认")):
        return "confirmation_required", "钉钉操作需要确认，请管理员检查同步程序与钉钉组件的确认流程。"
    if any(signal in detail for signal in ("timeout", "timed out", "deadline exceeded", "econnreset", "econnrefused", "enotfound")):
        return "network_error", "钉钉连接超时或网络异常，请检查网络后重试原备货计划；若已进入写入，请先核对钉钉中的记录。"
    return "request_failed", "钉钉请求失败，请管理员检查同步日志；核对钉钉记录后再重试原备货计划。"


@dataclass(frozen=True)
class DingTalkTarget:
    profile: str
    corp_id: str
    base_id: str
    base_name: str
    table_id: str
    table_name: str
    fields: dict[str, dict[str, str]]

    @property
    def table_url(self) -> str:
        return (
            f"https://alidocs.dingtalk.com/i/nodes/{self.base_id}"
            f"?entrance=data&sheetId={self.table_id}"
        )


def _configuration_path() -> Path:
    override = os.getenv("TERUISI_DINGTALK_REPLENISHMENT_CONFIG", "").strip()
    if override:
        path = Path(override)
        if not path.is_absolute():
            raise InventoryApiError(
                "钉钉备货计划配置路径必须是绝对路径",
                code="service_unavailable",
                status=503,
            )
        return path
    return settings.BASE_DIR.parent / "config" / "dingtalk-replenishment.json"


@lru_cache(maxsize=1)
def load_target() -> DingTalkTarget:
    try:
        raw = json.loads(_configuration_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryApiError(
            "钉钉备货计划同步配置不可用",
            code="service_unavailable",
            status=503,
        ) from error
    if not isinstance(raw, dict) or raw.get("version") != 1 or raw.get("enabled") is not True:
        raise InventoryApiError(
            "钉钉备货计划同步尚未启用",
            code="service_unavailable",
            status=503,
        )
    profile = raw.get("profile")
    base = raw.get("base")
    table = raw.get("table")
    fields = raw.get("fields")
    if (
        not isinstance(profile, str)
        or not re.fullmatch(r"ding[a-zA-Z0-9]{8,80}:[A-Za-z0-9._-]{3,80}", profile)
        or not isinstance(base, dict)
        or not isinstance(table, dict)
        or not isinstance(fields, dict)
    ):
        raise InventoryApiError("钉钉备货计划同步配置无效", code="service_unavailable", status=503)
    required_keys = {
        "productCode", "supplier", "productName", "orderDate", "plannedQuantity",
        "buyer", "notes", "expectedArrivalDate", "brand", "planType", "operatorName",
        "currentStockQuantity", "sales30dQuantity", "coverageDays", "warehouse",
        "department", "requiresInspection",
    }
    if set(fields) != required_keys:
        raise InventoryApiError("钉钉备货计划字段配置不完整", code="service_unavailable", status=503)
    for field in fields.values():
        if (
            not isinstance(field, dict)
            or set(field) != {"id", "name", "type"}
            or any(not isinstance(field.get(key), str) or not field[key] for key in ("id", "name", "type"))
        ):
            raise InventoryApiError("钉钉备货计划字段配置无效", code="service_unavailable", status=503)
    base_id, base_name = base.get("id"), base.get("name")
    table_id, table_name = table.get("id"), table.get("name")
    if any(not isinstance(value, str) or not value for value in (base_id, base_name, table_id, table_name)):
        raise InventoryApiError("钉钉备货计划目标配置无效", code="service_unavailable", status=503)
    return DingTalkTarget(
        profile=profile,
        corp_id=profile.split(":", 1)[0],
        base_id=base_id,
        base_name=base_name,
        table_id=table_id,
        table_name=table_name,
        fields=fields,
    )


class DwsCli:
    def __init__(self, target: DingTalkTarget, runner: Callable[[list[str]], dict[str, object]] | None = None):
        self.target = target
        self.runner = runner or self._run_process

    def _command_prefix(self) -> list[str]:
        node = os.getenv("TERUISI_DWS_NODE_PATH", "").strip() or shutil.which("node.exe") or shutil.which("node")
        appdata = os.getenv("APPDATA", "").strip()
        script = os.getenv("TERUISI_DWS_SCRIPT_PATH", "").strip()
        if not script and appdata:
            script = str(Path(appdata) / "npm" / "node_modules" / "dingtalk-workspace-cli" / "bin" / "dws.js")
        if not node or not Path(node).is_file() or not script or not Path(script).is_file():
            raise InventoryApiError(
                "钉钉同步运行组件不可用，请管理员检查 DWS 安装",
                code="service_unavailable",
                status=503,
            )
        return [str(Path(node).resolve()), str(Path(script).resolve())]

    def _run_process(self, args: list[str]) -> dict[str, object]:
        def fail(category: str, message: str, returncode: int | None = None) -> NoReturn:
            stage = " ".join(part if re.fullmatch(r"[a-z-]+", part) else "?" for part in args[:3])
            logger.warning("dws_sync_failed command=%s category=%s exit_code=%s", stage, category, returncode)
            raise InventoryApiError(message, code="service_unavailable", status=503) from None

        command = [
            *self._command_prefix(),
            *args,
            "--profile", self.target.profile,
            "--timeout", "25",
            "--format", "json",
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            result = subprocess.run(
                command,
                cwd=settings.BASE_DIR.parent,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=35,
                check=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired:
            fail("timeout", "钉钉请求超时；若已进入写入，请先核对钉钉记录，再重试原备货计划。")
        except OSError:
            fail("process_unavailable", "钉钉同步程序无法启动，请管理员检查 DWS 运行组件。")
        except UnicodeError:
            fail("invalid_encoding", "钉钉响应编码异常，请管理员检查 DWS 运行组件。")
        if len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) > MAX_DWS_OUTPUT_BYTES:
            fail("output_too_large", "钉钉返回内容超过安全上限", result.returncode)
        stdout = result.stdout.strip()
        # The CLI can send an authentication error to stderr with empty stdout.
        # Inspect failure diagnostics before trying to parse the success payload.
        if result.returncode != 0:
            fail(*_dws_failure_details(stdout, result.stderr), result.returncode)
        try:
            payload = json.loads(stdout)
        except (ValueError, RecursionError):
            if not stdout and result.stderr.strip():
                fail(*_dws_failure_details(stdout, result.stderr), result.returncode)
            fail("invalid_response", "钉钉返回内容无效，请管理员检查 DWS 响应格式。", result.returncode)
        if not isinstance(payload, dict):
            fail("invalid_response", "钉钉返回内容无效，请管理员检查 DWS 响应格式。", result.returncode)
        if payload.get("success") is False or payload.get("status") == "error" or payload.get("error"):
            fail(*_dws_failure_details(stdout, result.stderr), result.returncode)
        return payload

    def run(self, *args: str) -> dict[str, object]:
        payload = self.runner(list(args))
        if not isinstance(payload, dict):
            raise InventoryApiError("钉钉返回内容无效", code="service_unavailable", status=503)
        return payload


def _data(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class DingTalkReplenishmentGateway:
    def __init__(self, target: DingTalkTarget | None = None, cli: DwsCli | None = None):
        self.target = target or load_target()
        self.cli = cli or DwsCli(self.target)

    def _field(self, key: str) -> dict[str, str]:
        return self.target.fields[key]

    def _verify_schema(self, plan: ReplenishmentPlanItem) -> None:
        payload = self.cli.run(
            "aitable", "table", "get",
            "--base-id", self.target.base_id,
            "--table-ids", self.target.table_id,
        )
        tables = _data(payload).get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
            raise InventoryApiError("钉钉备货管理表结构无法确认", code="service_unavailable", status=503)
        table = tables[0]
        if table.get("tableId") != self.target.table_id or table.get("tableName") != self.target.table_name:
            raise InventoryApiError("钉钉备货管理表身份已变化", code="service_unavailable", status=503)
        actual = {
            field.get("fieldId"): (field.get("fieldName"), field.get("type"))
            for field in table.get("fields", [])
            if isinstance(field, dict)
        }
        for expected in self.target.fields.values():
            if actual.get(expected["id"]) != (expected["name"], expected["type"]):
                raise InventoryApiError(
                    f"钉钉字段“{expected['name']}”已变化，请管理员重新核验映射",
                    code="service_unavailable",
                    status=503,
                )
        options: dict[str, set[str]] = {}
        # Current DWS metadata advertises comma-separated field IDs, but the
        # live endpoint rejects that representation. Keep each request bound to
        # one exact field until the CLI contract and endpoint agree again.
        for key in ("brand", "warehouse", "requiresInspection"):
            expected_id = self._field(key)["id"]
            details = self.cli.run(
                "aitable", "field", "get",
                "--base-id", self.target.base_id,
                "--table-id", self.target.table_id,
                "--field-ids", expected_id,
            )
            fields = _data(details).get("fields")
            if not isinstance(fields, list) or len(fields) != 1 or not isinstance(fields[0], dict):
                raise InventoryApiError("钉钉单选字段配置无法确认", code="service_unavailable", status=503)
            field = fields[0]
            if field.get("fieldId") != expected_id or not isinstance(field.get("config"), dict):
                raise InventoryApiError("钉钉单选字段配置无法确认", code="service_unavailable", status=503)
            option_rows = field["config"].get("options")
            if not isinstance(option_rows, list):
                raise InventoryApiError("钉钉单选字段配置无法确认", code="service_unavailable", status=503)
            options[expected_id] = {
                str(option.get("name")) for option in option_rows if isinstance(option, dict) and option.get("name")
            }
        for key, value in (("brand", plan.brand), ("warehouse", plan.warehouse), ("requiresInspection", "是" if plan.requires_inspection else "否")):
            if value and value not in options.get(self._field(key)["id"], set()):
                raise InventoryApiError(
                    f"钉钉字段“{self._field(key)['name']}”没有选项“{value}”，请先在钉钉中维护该选项",
                    code="invalid_request",
                    status=422,
                )

    def _resolve_user(self, name: str) -> dict[str, str]:
        payload = self.cli.run("contact", "user", "search", "--query", name)
        rows = payload.get("result")
        if not isinstance(rows, list):
            rows = _data(payload).get("result")
        matches = {
            str(row.get("userId"))
            for row in rows if isinstance(row, dict)
            and any(str(row.get(key) or "").strip() == name for key in ("name", "nick", "flowerName"))
            and row.get("userId")
        } if isinstance(rows, list) else set()
        if len(matches) != 1:
            raise InventoryApiError(
                f"钉钉人员“{name}”无法唯一匹配，请在系统计划中填写钉钉中的准确姓名",
                code="invalid_request",
                status=422,
            )
        return {"userId": matches.pop(), "corpId": self.target.corp_id}

    def _resolve_users(self, value: str, *, multiple: bool) -> list[dict[str, str]]:
        names = list(dict.fromkeys(name.strip() for name in NAME_SPLIT_RE.split(value) if name.strip()))
        if not names:
            return []
        if (not multiple and len(names) != 1) or len(names) > 10:
            raise InventoryApiError("钉钉人员填写数量无效", code="invalid_request", status=422)
        return [self._resolve_user(name) for name in names]

    def _resolve_department(self, name: str) -> list[dict[str, str]]:
        if not name.strip():
            return []
        payload = self.cli.run("contact", "dept", "search", "--query", name.strip())
        rows = payload.get("deptList")
        if not isinstance(rows, list):
            rows = _data(payload).get("deptList")
        matches: dict[str, str] = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or row.get("deptId") is None:
                    continue
                full_name = RED_TAG_RE.sub("", str(row.get("deptName") or "")).strip()
                if full_name == name.strip() or full_name.rsplit("-", 1)[-1] == name.strip():
                    matches[str(row["deptId"])] = full_name
        if len(matches) != 1:
            raise InventoryApiError(
                f"钉钉部门“{name.strip()}”无法唯一匹配，请填写完整且准确的末级部门名称",
                code="invalid_request",
                status=422,
            )
        return [{"deptId": next(iter(matches))}]

    @staticmethod
    def marker(plan_id: str) -> str:
        return f"[运营管理系统备货计划ID:{plan_id}]"

    @staticmethod
    def legacy_markers(plan_id: str) -> tuple[str, ...]:
        return (f"[TERUISI备货计划ID:{plan_id}]",)

    def _cells(self, plan: ReplenishmentPlanItem) -> dict[str, object]:
        field = lambda key: self._field(key)["id"]
        notes = plan.notes.strip()
        cells: dict[str, object] = {
            field("productCode"): plan.product_code,
            field("productName"): plan.product_name,
            field("plannedQuantity"): int(plan.planned_quantity),
            field("notes"): f"{notes}\n{self.marker(plan.id)}" if notes else self.marker(plan.id),
            field("warehouse"): plan.warehouse,
            field("currentStockQuantity"): int(plan.current_stock_quantity),
            field("requiresInspection"): "是" if plan.requires_inspection else "否",
        }
        optional_text = {
            "supplier": plan.supplier,
            "brand": plan.brand,
            "planType": plan.plan_type,
        }
        for key, value in optional_text.items():
            if value.strip():
                cells[field(key)] = value.strip()
        if plan.order_date:
            cells[field("orderDate")] = plan.order_date.isoformat()
        if plan.expected_arrival_date:
            cells[field("expectedArrivalDate")] = plan.expected_arrival_date.isoformat()
        if plan.sales_30d_quantity is not None:
            cells[field("sales30dQuantity")] = int(plan.sales_30d_quantity)
        if plan.coverage_days_tenths is not None:
            cells[field("coverageDays")] = float(plan.coverage_days_tenths) / 10
        buyer = self._resolve_users(plan.buyer, multiple=False)
        if buyer:
            cells[field("buyer")] = buyer
        operators = self._resolve_users(plan.operator_name, multiple=True)
        if operators:
            cells[field("operatorName")] = operators
        department = self._resolve_department(plan.department)
        if department:
            cells[field("department")] = department
        return cells

    def _query_marker(self, plan_id: str, *, all_fields: bool = False) -> list[dict[str, object]]:
        notes_field = self._field("notes")["id"]
        field_ids = ",".join(field["id"] for field in self.target.fields.values()) if all_fields else notes_field
        matches: dict[str, dict[str, object]] = {}
        for marker in (self.marker(plan_id), *self.legacy_markers(plan_id)):
            filters = json.dumps({
                "operator": "and",
                "operands": [{"operator": "contain", "operands": [notes_field, marker]}],
            }, ensure_ascii=False, separators=(",", ":"))
            payload = self.cli.run(
                "aitable", "record", "query",
                "--base-id", self.target.base_id,
                "--table-id", self.target.table_id,
                "--field-ids", field_ids,
                "--filters", filters,
                "--limit", "10",
            )
            rows = _data(payload).get("records")
            if (
                not isinstance(rows, list)
                or _data(payload).get("hasMore") is True
                or len(rows) >= 10
                or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("recordId"), str)
                    or not row["recordId"].strip()
                    for row in rows
                )
            ):
                raise InventoryApiError(
                    "钉钉已有备货记录查询不完整，已停止提交以避免重复创建；请管理员核对后重试。",
                    code="service_unavailable",
                    status=503,
                )
            for row in rows:
                record_id = str(row.get("recordId") or "")
                matches[record_id] = row
        return list(matches.values())

    @staticmethod
    def _number_equal(actual: object, expected: object) -> bool:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False

    def _cell_equal(self, key: str, actual: object, expected: object) -> bool:
        field_type = self._field(key)["type"]
        if field_type == "number":
            return self._number_equal(actual, expected)
        if field_type == "date":
            return isinstance(actual, str) and actual[:10] == str(expected)[:10]
        if field_type == "singleSelect":
            return isinstance(actual, dict) and actual.get("name") == expected
        if field_type == "user":
            if not isinstance(actual, list) or not isinstance(expected, list):
                return False
            return {(str(row.get("corpId")), str(row.get("userId"))) for row in actual if isinstance(row, dict)} == {
                (str(row.get("corpId")), str(row.get("userId"))) for row in expected if isinstance(row, dict)
            }
        if field_type == "department":
            if not isinstance(actual, list) or not isinstance(expected, list):
                return False
            return {str(row.get("departmentId") or row.get("deptId")) for row in actual if isinstance(row, dict)} == {
                str(row.get("deptId")) for row in expected if isinstance(row, dict)
            }
        return actual == expected

    def _verify_record(self, record: dict[str, object], cells: dict[str, object]) -> None:
        actual = record.get("cells")
        if not isinstance(actual, dict):
            raise InventoryApiError("钉钉备货记录回查失败", code="service_unavailable", status=503)
        keys_by_id = {field["id"]: key for key, field in self.target.fields.items()}
        for field_id, expected in cells.items():
            key = keys_by_id[field_id]
            if not self._cell_equal(key, actual.get(field_id), expected):
                raise InventoryApiError(
                    f"钉钉字段“{self._field(key)['name']}”写入后回查不一致",
                    code="service_unavailable",
                    status=503,
                )

    def sync(self, plan: ReplenishmentPlanItem) -> tuple[str, str]:
        self._verify_schema(plan)
        cells = self._cells(plan)
        matches = self._query_marker(plan.id)
        if len(matches) > 1:
            raise InventoryApiError(
                "钉钉中存在多条相同系统计划记录，请先人工核对",
                code="conflict",
                status=409,
            )
        outcome = "updated" if matches else "created"
        if matches:
            record_id = str(matches[0].get("recordId") or "")
            if not record_id:
                raise InventoryApiError("钉钉备货记录标识无效", code="service_unavailable", status=503)
            records = [{"recordId": record_id, "cells": cells}]
            self.cli.run(
                "aitable", "record", "update",
                "--base-id", self.target.base_id,
                "--table-id", self.target.table_id,
                "--records", json.dumps(records, ensure_ascii=False, separators=(",", ":")),
            )
        else:
            self.cli.run(
                "aitable", "record", "create",
                "--base-id", self.target.base_id,
                "--table-id", self.target.table_id,
                "--records", json.dumps([{"cells": cells}], ensure_ascii=False, separators=(",", ":")),
            )
        verified = self._query_marker(plan.id, all_fields=True)
        if len(verified) != 1 or not verified[0].get("recordId"):
            raise InventoryApiError("钉钉备货记录写入后未通过唯一性回查", code="service_unavailable", status=503)
        self._verify_record(verified[0], cells)
        return str(verified[0]["recordId"]), outcome


def _plan_digest(plan: ReplenishmentPlanItem) -> str:
    payload = {
        "markerVersion": REPLENISHMENT_MARKER_VERSION,
        "id": plan.id,
        "productCode": plan.product_code,
        "productName": plan.product_name,
        "brand": plan.brand,
        "supplier": plan.supplier,
        "warehouse": plan.warehouse,
        "buyer": plan.buyer,
        "operatorName": plan.operator_name,
        "department": plan.department,
        "planType": plan.plan_type,
        "orderDate": plan.order_date.isoformat() if plan.order_date else None,
        "expectedArrivalDate": plan.expected_arrival_date.isoformat() if plan.expected_arrival_date else None,
        "requiresInspection": bool(plan.requires_inspection),
        "currentStockQuantity": int(plan.current_stock_quantity),
        "sales30dQuantity": int(plan.sales_30d_quantity) if plan.sales_30d_quantity is not None else None,
        "plannedQuantity": int(plan.planned_quantity),
        "coverageDaysTenths": plan.coverage_days_tenths,
        "notes": plan.notes,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _safe_error(error: Exception) -> str:
    if isinstance(error, InventoryApiError):
        return str(error)[:500]
    return "钉钉同步失败，请稍后重试"


def sync_replenishment_plan(
    plan_id: str,
    actor_email: str,
    *,
    gateway: DingTalkReplenishmentGateway | None = None,
) -> dict[str, object]:
    owner_token = uuid.uuid4().hex
    now = timezone.now()
    with transaction.atomic():
        lock_active_authority()
        plan = ReplenishmentPlanItem.objects.select_for_update().filter(id=plan_id).first()
        if plan is None:
            raise InventoryApiError("备货计划不存在", code="not_found", status=404)
        if plan.status != "confirmed":
            raise InventoryApiError("只有已确认的备货计划才能创建钉钉计划", code="conflict", status=409)
        current_digest = _plan_digest(plan)
        if (
            plan.dingtalk_sync_status == "synced"
            and plan.dingtalk_record_id
            and plan.dingtalk_payload_sha256 == current_digest
        ):
            return {
                "ok": True,
                "outcome": "already_synced",
                "recordId": plan.dingtalk_record_id,
                "targetUrl": load_target().table_url,
            }
        if (
            plan.dingtalk_sync_status == "syncing"
            and plan.dingtalk_sync_started_at
            and plan.dingtalk_sync_started_at > now - SYNC_LEASE
        ):
            raise InventoryApiError("该备货计划正在同步到钉钉", code="conflict", status=409)
        plan.dingtalk_sync_status = "syncing"
        plan.dingtalk_sync_owner_token = owner_token
        plan.dingtalk_sync_started_at = now
        plan.dingtalk_payload_sha256 = current_digest
        plan.dingtalk_sync_error = ""
        plan.save(update_fields=[
            "dingtalk_sync_status", "dingtalk_sync_owner_token", "dingtalk_sync_started_at",
            "dingtalk_payload_sha256", "dingtalk_sync_error", "updated_at",
        ])
        bump_revision({"kind": "replenishment_dingtalk_sync_started", "planId": plan.id})
    try:
        active_gateway = gateway or DingTalkReplenishmentGateway()
        record_id, outcome = active_gateway.sync(plan)
        with transaction.atomic():
            lock_active_authority()
            current = ReplenishmentPlanItem.objects.select_for_update().get(id=plan_id)
            if current.dingtalk_sync_owner_token != owner_token:
                raise InventoryApiError("备货计划同步所有权已失效", code="version_conflict", status=409)
            current.dingtalk_sync_status = "synced"
            current.dingtalk_record_id = record_id
            current.dingtalk_sync_owner_token = ""
            current.dingtalk_sync_error = ""
            current.dingtalk_synced_at = timezone.now()
            current.dingtalk_synced_by = actor_email[:320]
            current.save(update_fields=[
                "dingtalk_sync_status", "dingtalk_record_id", "dingtalk_sync_owner_token",
                "dingtalk_sync_error", "dingtalk_synced_at", "dingtalk_synced_by", "updated_at",
            ])
            bump_revision({"kind": "replenishment_dingtalk_synced", "planId": current.id})
        return {
            "ok": True,
            "outcome": outcome,
            "recordId": record_id,
            "targetUrl": active_gateway.target.table_url,
        }
    except Exception as error:
        with transaction.atomic():
            lock_active_authority()
            current = ReplenishmentPlanItem.objects.select_for_update().filter(id=plan_id).first()
            if current and current.dingtalk_sync_owner_token == owner_token:
                current.dingtalk_sync_status = "failed"
                current.dingtalk_sync_owner_token = ""
                current.dingtalk_sync_error = _safe_error(error)
                current.save(update_fields=[
                    "dingtalk_sync_status", "dingtalk_sync_owner_token", "dingtalk_sync_error", "updated_at",
                ])
                bump_revision({"kind": "replenishment_dingtalk_sync_failed", "planId": current.id})
        if isinstance(error, InventoryApiError):
            raise
        raise InventoryApiError("钉钉同步失败，请稍后重试", code="service_unavailable", status=503) from error
