from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import hashlib
import json
import re

from django.db import transaction
from django.utils import timezone

from .dingtalk_sync import DwsCli, NAME_SPLIT_RE, load_target, sync_replenishment_plan
from .errors import InventoryApiError
from .models import ReplenishmentGroupDelivery, ReplenishmentPlanItem
from .revisions import bump_revision
from .write_requests import lock_active_authority


PLAN_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
MAX_PLAN_COUNT = 50
APPROVED_GROUP_NAME = "志高/特睿思备货计划群"
APPROVED_ROBOT_NAME = "志高助手"


def _records(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _records(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _records(nested)


def _field(record: dict[str, object], names: tuple[str, ...]) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _page_value(payload: object, field: str) -> object | None:
    for record in _records(payload):
        if field in record:
            return record[field]
    return None


def _exact_identity(
    payloads: Iterable[object],
    *,
    expected_name: str,
    name_fields: tuple[str, ...],
    id_fields: tuple[str, ...],
    label: str,
) -> str:
    matches: dict[str, dict[str, object]] = {}
    for payload in payloads:
        for record in _records(payload):
            if _field(record, name_fields) != expected_name:
                continue
            identity = _field(record, id_fields)
            if identity:
                matches[identity] = record
    if len(matches) != 1:
        raise InventoryApiError(
            f"{label}“{expected_name}”精确匹配到 {len(matches)} 个对象，拒绝发送",
            code="conflict",
            status=409,
        )
    return next(iter(matches))


class DingTalkGroupGateway:
    def __init__(self, cli: DwsCli | None = None):
        target = load_target()
        self.cli = cli or DwsCli(target)

    def resolve_group(self, group_name: str) -> str:
        pages: list[object] = []
        cursor = "0"
        for _ in range(5):
            payload = self.cli.run(
                "chat", "search", "--query", group_name,
                "--limit", "20", "--cursor", cursor,
            )
            pages.append(payload)
            if _page_value(payload, "hasMore") is not True:
                break
            next_cursor = _page_value(payload, "nextCursor")
            if not isinstance(next_cursor, (str, int)) or str(next_cursor) == cursor:
                raise InventoryApiError("钉钉群搜索分页游标无效", code="service_unavailable", status=503)
            cursor = str(next_cursor)
        return _exact_identity(
            pages,
            expected_name=group_name,
            name_fields=("title", "name", "groupName", "conversationTitle"),
            id_fields=("openConversationId", "conversationId", "chatId"),
            label="钉钉群",
        )

    def resolve_robot(self, robot_name: str) -> str:
        pages: list[object] = []
        for page in range(1, 6):
            payload = self.cli.run(
                "chat", "bot", "search", "--name", robot_name,
                "--page", str(page), "--size", "50",
            )
            pages.append(payload)
            if _page_value(payload, "hasMore") is not True:
                break
        return _exact_identity(
            pages,
            expected_name=robot_name,
            name_fields=("robotName", "name", "title"),
            id_fields=("robotCode", "robot-code", "code"),
            label="钉钉机器人",
        )

    def assert_robot_in_group(self, group_id: str, robot_name: str, robot_code: str) -> None:
        payload = self.cli.run("chat", "group", "bots", "--group", group_id)
        for record in _records(payload):
            name = _field(record, ("robotName", "name", "title"))
            code = _field(record, ("robotCode", "robot-code", "code"))
            if code == robot_code or (name == robot_name and not code):
                return
        raise InventoryApiError(
            f"机器人“{robot_name}”尚未安装到指定钉钉群",
            code="conflict",
            status=409,
        )

    def resolve_user_id(self, name: str) -> str:
        payload = self.cli.run("contact", "user", "search", "--query", name)
        matches = {
            _field(record, ("userId",))
            for record in _records(payload)
            if any(_field(record, (field,)) == name for field in ("name", "nick", "flowerName"))
            and _field(record, ("userId",))
        }
        if len(matches) != 1:
            raise InventoryApiError(
                f"钉钉人员“{name}”无法唯一匹配，请在备货计划中填写钉钉中的准确姓名",
                code="invalid_request",
                status=422,
            )
        return next(iter(matches))

    def preflight(self, group_name: str, robot_name: str, buyers: list[str]) -> tuple[str, str, list[str]]:
        group_id = self.resolve_group(group_name)
        robot_code = self.resolve_robot(robot_name)
        self.assert_robot_in_group(group_id, robot_name, robot_code)
        user_ids = [self.resolve_user_id(name) for name in buyers]
        return group_id, robot_code, user_ids

    def send(self, *, group_id: str, robot_code: str, message: str, user_ids: list[str]) -> dict[str, object]:
        payload = self.cli.run(
            "chat", "+messages-send", "--as", "bot",
            "--robot-code", robot_code, "--groups", group_id,
            "--markdown", message, "--at-user-ids", ",".join(user_ids), "--yes",
        )
        _assert_send_receipt(payload)
        return payload


def _normalize_plan_ids(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_PLAN_COUNT:
        raise InventoryApiError(f"每次请选择 1 到 {MAX_PLAN_COUNT} 条备货计划")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not PLAN_ID_RE.fullmatch(item.strip()):
            raise InventoryApiError("备货计划 ID 无效")
        plan_id = item.strip()
        if plan_id not in seen:
            seen.add(plan_id)
            result.append(plan_id)
    return result


def _load_plans(plan_ids: list[str]) -> list[ReplenishmentPlanItem]:
    plans = {plan.id: plan for plan in ReplenishmentPlanItem.objects.filter(id__in=plan_ids)}
    if len(plans) != len(plan_ids):
        raise InventoryApiError("部分备货计划不存在，请刷新后重新选择", code="not_found", status=404)
    ordered = [plans[plan_id] for plan_id in plan_ids]
    for plan in ordered:
        if plan.status != "confirmed":
            raise InventoryApiError("只有已确认的备货计划才能发送钉钉群", code="conflict", status=409)
        if not plan.supplier.strip():
            raise InventoryApiError(f"{plan.product_code} 缺少对应工厂，拒绝发送", status=422)
        buyers = [name.strip() for name in NAME_SPLIT_RE.split(plan.buyer) if name.strip()]
        if len(buyers) != 1:
            raise InventoryApiError(f"{plan.product_code} 必须填写一名准确的对应采购", status=422)
    return ordered


def _message(
    plans: list[ReplenishmentPlanItem],
    mention_by_buyer: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    grouped: dict[str, dict[str, list[ReplenishmentPlanItem]]] = defaultdict(lambda: defaultdict(list))
    for plan in plans:
        buyer = next(name.strip() for name in NAME_SPLIT_RE.split(plan.buyer) if name.strip())
        grouped[buyer][plan.supplier.strip()].append(plan)
    sections: list[str] = []
    for buyer in sorted(grouped, key=lambda value: value.casefold()):
        mention = (mention_by_buyer or {}).get(buyer, buyer)
        lines = [f"@{mention}"]
        for supplier in sorted(
            grouped[buyer],
            key=lambda value: (
                min((plan.product_code.casefold(), plan.id) for plan in grouped[buyer][value]),
                value.casefold(),
            ),
        ):
            items = sorted(grouped[buyer][supplier], key=lambda plan: (plan.product_code.casefold(), plan.id))
            lines.append(f"**▸ {supplier}（{len(items)} 条）**")
            lines.extend(
                f"{plan.product_code} {plan.product_name}，× {int(plan.planned_quantity)}台"
                for plan in items
            )
        sections.append("  \n".join(lines))
    return "\n\n".join(sections), sorted(grouped, key=lambda value: value.casefold())


def _assert_send_receipt(payload: dict[str, object]) -> None:
    if payload.get("ok") is True:
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("success") is not False:
            return
    requested = payload.get("requestedCount")
    succeeded = payload.get("succeededCount")
    failed = payload.get("failedCount")
    results = payload.get("results")
    failures = payload.get("failures")
    if (
        requested == 1
        and succeeded == 1
        and failed == 0
        and isinstance(results, list)
        and len(results) == 1
        and isinstance(failures, list)
        and len(failures) == 0
    ):
        return
    raise InventoryApiError("钉钉消息发送回执未确认成功", code="service_unavailable", status=503)


def _target_name(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise InventoryApiError(f"{label}配置无效", code="service_unavailable", status=503)
    return value.strip()


def build_group_preview(
    plan_ids_value: object,
    target_group_name: object,
    robot_name: object,
    *,
    gateway: DingTalkGroupGateway | None = None,
) -> dict[str, object]:
    plan_ids = _normalize_plan_ids(plan_ids_value)
    group_name = _target_name(target_group_name, "钉钉群", 200)
    resolved_robot_name = _target_name(robot_name, "钉钉机器人", 160)
    if group_name != APPROVED_GROUP_NAME or resolved_robot_name != APPROVED_ROBOT_NAME:
        raise InventoryApiError(
            f"备货群消息只允许由“{APPROVED_ROBOT_NAME}”发送到“{APPROVED_GROUP_NAME}”",
            code="conflict",
            status=409,
        )
    plans = _load_plans(plan_ids)
    message, buyers = _message(plans)
    active_gateway = gateway or DingTalkGroupGateway()
    group_id, robot_code, user_ids = active_gateway.preflight(group_name, resolved_robot_name, buyers)
    if len(user_ids) != len(buyers):
        raise InventoryApiError("钉钉采购人员解析结果不完整", code="service_unavailable", status=503)
    provider_message, _ = _message(plans, dict(zip(buyers, user_ids)))
    canonical = json.dumps({
        "planIds": sorted(plan_ids),
        "targetGroupName": group_name,
        "robotName": resolved_robot_name,
        "message": message,
        "buyerNames": buyers,
        "providerMessageSha256": hashlib.sha256(provider_message.encode("utf-8")).hexdigest(),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "ok": True,
        "status": "preview",
        "planIds": plan_ids,
        "planCount": len(plans),
        "targetGroupName": group_name,
        "robotName": resolved_robot_name,
        "message": message,
        "buyerNames": buyers,
        "previewToken": token,
        "_groupId": group_id,
        "_robotCode": robot_code,
        "_userIds": user_ids,
        "_providerMessage": provider_message,
    }


def _public_preview(preview: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in preview.items() if not key.startswith("_")}


def preview_group_message(
    plan_ids: object,
    target_group_name: object,
    robot_name: object,
    *,
    gateway: DingTalkGroupGateway | None = None,
) -> dict[str, object]:
    return _public_preview(build_group_preview(
        plan_ids, target_group_name, robot_name, gateway=gateway,
    ))


def send_group_message(
    plan_ids: object,
    target_group_name: object,
    robot_name: object,
    preview_token: object,
    actor_email: str,
    *,
    gateway: DingTalkGroupGateway | None = None,
) -> dict[str, object]:
    if not isinstance(preview_token, str) or not re.fullmatch(r"[0-9a-f]{64}", preview_token):
        raise InventoryApiError("请先预览并确认本次钉钉消息")
    active_gateway = gateway or DingTalkGroupGateway()
    preview = build_group_preview(
        plan_ids, target_group_name, robot_name, gateway=active_gateway,
    )
    if preview["previewToken"] != preview_token:
        raise InventoryApiError("备货计划或钉钉目标已变化，请重新预览", code="version_conflict", status=409)

    for plan_id in preview["planIds"]:
        sync_replenishment_plan(str(plan_id), actor_email)

    message = str(preview["message"])
    message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()
    with transaction.atomic():
        lock_active_authority()
        existing = ReplenishmentGroupDelivery.objects.select_for_update().filter(
            idempotency_key=preview_token,
        ).first()
        if existing is not None:
            if existing.status == "delivered":
                return {
                    **_public_preview(preview),
                    "status": "already_delivered",
                    "deliveryId": str(existing.id),
                }
            raise InventoryApiError(
                "同一批备货消息已有发送中或结果待核对的记录，拒绝重复发送",
                code="conflict",
                status=409,
            )
        delivery = ReplenishmentGroupDelivery.objects.create(
            idempotency_key=preview_token,
            plan_ids=list(preview["planIds"]),
            target_group_name=str(preview["targetGroupName"]),
            robot_name=str(preview["robotName"]),
            message_sha256=message_sha256,
            message_text=message,
            status="sending",
            claimed_by=actor_email[:320],
        )

    try:
        receipt = active_gateway.send(
            group_id=str(preview["_groupId"]),
            robot_code=str(preview["_robotCode"]),
            message=str(preview["_providerMessage"]),
            user_ids=[str(value) for value in preview["_userIds"]],
        )
    except Exception as error:
        with transaction.atomic():
            lock_active_authority()
            current = ReplenishmentGroupDelivery.objects.select_for_update().get(id=delivery.id)
            if current.status == "sending":
                current.status = "uncertain"
                current.error_code = type(error).__name__[:120]
                current.save(update_fields=["status", "error_code", "updated_at"])
        raise

    receipt_sha256 = hashlib.sha256(json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with transaction.atomic():
        lock_active_authority()
        current = ReplenishmentGroupDelivery.objects.select_for_update().get(id=delivery.id)
        if current.status != "sending":
            raise InventoryApiError("备货群消息发送状态已变化", code="version_conflict", status=409)
        current.status = "delivered"
        current.provider_receipt = f"dws-sha256:{receipt_sha256}"
        current.error_code = ""
        current.delivered_at = timezone.now()
        current.save(update_fields=[
            "status", "provider_receipt", "error_code", "delivered_at", "updated_at",
        ])
        bump_revision({"kind": "replenishment_group_delivered", "deliveryId": str(current.id)})
    return {
        **_public_preview(preview),
        "status": "delivered",
        "deliveryId": str(delivery.id),
    }
