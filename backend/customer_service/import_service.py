from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import uuid

from django.db import connection, transaction
from django.utils import timezone

from .errors import CustomerServiceApiError
from .models import (
    CustomerServiceConversation,
    CustomerServiceImportAttempt,
    CustomerServiceImportBatch,
    CustomerServiceImportFingerprint,
    CustomerServiceImportScopeHead,
)
from .revisions import bump_revision
from .write_requests import lock_active_authority


HEX_64 = re.compile(r"^[0-9a-f]{64}$")
DATE_TIME = re.compile(r"^(?:19|20|21)\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]) (?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$")
MATCH_STATUSES = {"matched", "session_only", "chat_only", "ambiguous"}
MATCH_CONFIDENCES = {"exact", "time_only", "review", "none"}
MAX_CONVERSATIONS = 20_000
MAX_MESSAGES = 200
MAX_MESSAGE_CONTENT = 4_000
MAX_MESSAGE_BYTES = 128 * 1024
WARNING_LIMIT = 50
WARNING_CONTENT_LIMIT = 500

ROW_KEYS = {
    "sourceRowNumber", "consultedAt", "customerId", "customerAlias", "consultationType",
    "agent", "transferredAgent", "skillGroup", "productSku", "productName", "firstResponseAt",
    "responseSeconds", "durationMinutes", "customerMessageCount", "agentMessageCount",
    "satisfaction", "resolved", "conversationId", "conversationKey", "matchStatus",
    "matchConfidence", "chatStartedAt", "chatEndedAt", "chatCustomerAlias", "messages",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise CustomerServiceApiError(f"{label} 必须是字符串")
    normalized = value.strip()
    if (required and not normalized) or len(normalized) > maximum:
        raise CustomerServiceApiError(f"{label} 无效")
    return normalized


def _count(value: object, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100_000_000:
        raise CustomerServiceApiError(f"{label} 必须是非负整数")
    return value


def _decimal(value: object, label: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CustomerServiceApiError(f"{label} 必须是非负数值")
    try:
        parsed = Decimal(str(value)).quantize(Decimal("0.001"))
    except InvalidOperation as error:
        raise CustomerServiceApiError(f"{label} 必须是非负数值") from error
    if parsed < 0 or parsed > Decimal("99999999999.999"):
        raise CustomerServiceApiError(f"{label} 超出允许范围")
    return parsed


def _datetime_text(value: object, label: str, *, required: bool = False) -> str:
    text = _text(value, label, 19, required=required)
    if text and not DATE_TIME.fullmatch(text):
        raise CustomerServiceApiError(f"{label} 必须为 YYYY-MM-DD HH:mm:ss")
    if text:
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError as error:
            raise CustomerServiceApiError(f"{label} 不是有效日期时间") from error
    return text


def _messages(value: object, row_number: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_MESSAGES:
        raise CustomerServiceApiError(f"第 {row_number} 条会话消息集合无效")
    output: list[dict[str, str]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {"sender", "sentAt", "content"}:
            raise CustomerServiceApiError(f"第 {row_number} 条会话第 {index} 条消息字段无效")
        output.append({
            "sender": _text(item["sender"], "消息发送者", 120),
            "sentAt": _datetime_text(item["sentAt"], "消息时间", required=True),
            "content": _text(item["content"], "消息内容", MAX_MESSAGE_CONTENT),
        })
    if len(_canonical(output)) > MAX_MESSAGE_BYTES:
        raise CustomerServiceApiError(f"第 {row_number} 条会话消息总量超过 128KB")
    return output


def _row(value: object, index: int, shop_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != ROW_KEYS:
        raise CustomerServiceApiError(f"第 {index} 条会话字段集合无效")
    source_row = _count(value["sourceRowNumber"], "sourceRowNumber")
    key = _text(value["conversationKey"], "conversationKey", 2_000, required=True)
    match_status = _text(value["matchStatus"], "matchStatus", 32, required=True)
    confidence = _text(value["matchConfidence"], "matchConfidence", 32, required=True)
    if match_status not in MATCH_STATUSES or confidence not in MATCH_CONFIDENCES:
        raise CustomerServiceApiError(f"第 {index} 条会话匹配状态无效")
    return {
        "sourceRowNumber": source_row,
        "conversationKey": f"{shop_name}:{key}" if not key.startswith(f"{shop_name}:") else key,
        "consultedAt": _datetime_text(value["consultedAt"], "consultedAt", required=True),
        "customerId": _text(value["customerId"], "customerId", 1_000),
        "customerAlias": _text(value["customerAlias"], "customerAlias", 1_000),
        "consultationType": _text(value["consultationType"], "consultationType", 1_000),
        "agent": _text(value["agent"], "agent", 200),
        "transferredAgent": _text(value["transferredAgent"], "transferredAgent", 1_000),
        "skillGroup": _text(value["skillGroup"], "skillGroup", 1_000),
        "productSku": _text(value["productSku"], "productSku", 200),
        "productName": _text(value["productName"], "productName", 4_000),
        "firstResponseAt": _datetime_text(value["firstResponseAt"], "firstResponseAt"),
        "responseSeconds": _decimal(value["responseSeconds"], "responseSeconds"),
        "durationMinutes": _decimal(value["durationMinutes"], "durationMinutes"),
        "customerMessageCount": _count(value["customerMessageCount"], "customerMessageCount", nullable=True),
        "agentMessageCount": _count(value["agentMessageCount"], "agentMessageCount", nullable=True),
        "satisfaction": _text(value["satisfaction"], "satisfaction", 1_000),
        "resolved": _text(value["resolved"], "resolved", 1_000),
        "conversationId": _text(value["conversationId"], "conversationId", 2_000),
        "matchStatus": match_status,
        "matchConfidence": confidence,
        "chatStartedAt": _datetime_text(value["chatStartedAt"], "chatStartedAt"),
        "chatEndedAt": _datetime_text(value["chatEndedAt"], "chatEndedAt"),
        "chatCustomerAlias": _text(value["chatCustomerAlias"], "chatCustomerAlias", 1_000),
        "messages": _messages(value["messages"], index),
    }


def _warnings(value: object, total: object) -> dict[str, object]:
    if not isinstance(value, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise CustomerServiceApiError("导入告警摘要无效")
    items = [str(item)[:WARNING_CONTENT_LIMIT] for item in value[:WARNING_LIMIT]]
    total_count = max(len(items), total)
    return {"items": items, "totalCount": total_count, "truncated": len(items) < total_count}


def _public_warnings(summary: dict[str, object]) -> dict[str, object]:
    return {
        "warnings": summary["items"],
        "warningTotalCount": summary["totalCount"],
        "warningsTruncated": summary["truncated"],
    }


def _scope_lock(scope_key: str) -> None:
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(f"customer-service-scope:{scope_key}".encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _batch_payload(batch: CustomerServiceImportBatch) -> dict[str, object]:
    warnings = batch.warnings_json if isinstance(batch.warnings_json, dict) else {}
    items = warnings.get("items", []) if isinstance(warnings.get("items", []), list) else []
    total = max(len(items), int(warnings.get("totalCount", len(items)) or 0))
    return {
        "id": batch.id,
        "shopName": batch.shop_name,
        "sessionFileName": batch.session_file_name,
        "chatFileName": batch.chat_file_name,
        "fileHash": batch.import_hash,
        "status": batch.status,
        "conversationCount": int(batch.conversation_count),
        "matchedCount": int(batch.matched_count),
        "sessionOnlyCount": int(batch.session_only_count),
        "chatOnlyCount": int(batch.chat_only_count),
        "ambiguousCount": int(batch.ambiguous_count),
        "warnings": [str(item)[:WARNING_CONTENT_LIMIT] for item in items[:WARNING_LIMIT]],
        "warningTotalCount": total,
        "warningsTruncated": len(items[:WARNING_LIMIT]) < total,
        "createdAt": batch.created_at.isoformat(),
        "completedAt": batch.completed_at.isoformat() if batch.completed_at else None,
    }


def record_edge_rejection(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    allowed = {"action", "rawFileHash", "scopeHint", "errorCode", "issues", "fileName", "fileSizeBytes"}
    if set(payload) != allowed or payload.get("action") != "reject":
        raise CustomerServiceApiError("客服拒绝审计请求字段集合无效")
    raw_hash = _text(payload["rawFileHash"], "rawFileHash", 64, required=True).lower()
    if not HEX_64.fullmatch(raw_hash):
        raise CustomerServiceApiError("rawFileHash 无效")
    scope_hint = payload["scopeHint"]
    issues = payload["issues"]
    if not isinstance(scope_hint, dict) or not isinstance(issues, list) or len(issues) > 50:
        raise CustomerServiceApiError("客服拒绝审计内容无效")
    lock_active_authority()
    attempt = CustomerServiceImportAttempt.objects.create(
        domain="customer-service",
        raw_file_hash=raw_hash,
        scope_json=scope_hint,
        file_name=_text(payload["fileName"], "fileName", 520),
        file_size_bytes=_count(payload["fileSizeBytes"], "fileSizeBytes") or 0,
        actor_email=actor_email[:320],
        warnings_json={"items": issues[:50]},
        outcome="rejected",
        error_code=_text(payload["errorCode"], "errorCode", 80, required=True),
    )
    return {"status": "rejected", "attemptId": str(attempt.id)}


def import_customer_service(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    allowed = {
        "action", "shopName", "sessionFileName", "chatFileName", "rawFileHash", "fileSizeBytes",
        "summary", "warnings", "warningTotalCount", "conversations",
    }
    if set(payload) != allowed or payload.get("action") != "import":
        raise CustomerServiceApiError("客服导入请求字段集合无效")
    shop_name = _text(payload["shopName"], "shopName", 100, required=True)
    session_file = _text(payload["sessionFileName"], "sessionFileName", 255, required=True)
    chat_file = _text(payload["chatFileName"], "chatFileName", 255, required=True)
    raw_hash = _text(payload["rawFileHash"], "rawFileHash", 64, required=True).lower()
    if not HEX_64.fullmatch(raw_hash):
        raise CustomerServiceApiError("rawFileHash 无效")
    file_size = _count(payload["fileSizeBytes"], "fileSizeBytes") or 0
    conversations = payload["conversations"]
    if not isinstance(conversations, list) or not 1 <= len(conversations) <= MAX_CONVERSATIONS:
        raise CustomerServiceApiError("客服导入会话数量无效")
    rows = [_row(value, index, shop_name) for index, value in enumerate(conversations, start=1)]
    keys = [str(row["conversationKey"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise CustomerServiceApiError("客服导入包含重复会话身份")
    summary = payload["summary"]
    if not isinstance(summary, dict) or set(summary) != {
        "sessionCount", "chatSessionCount", "matchedCount", "timeOnlyMatchedCount",
        "sessionOnlyCount", "chatOnlyCount", "ambiguousCount",
    }:
        raise CustomerServiceApiError("客服导入汇总字段无效")
    normalized_summary = {key: _count(value, key) or 0 for key, value in summary.items()}
    matched_count = normalized_summary["matchedCount"] + normalized_summary["timeOnlyMatchedCount"]
    expected_count = matched_count + normalized_summary["sessionOnlyCount"] + normalized_summary["chatOnlyCount"] + normalized_summary["ambiguousCount"]
    if expected_count != len(rows):
        raise CustomerServiceApiError("客服导入汇总与会话行数不一致")
    warning_summary = _warnings(payload["warnings"], payload["warningTotalCount"])
    scope_key = _digest({"domain": "customer-service", "shopName": shop_name})
    identity_hash = _digest({"shopName": shop_name, "conversationKeys": sorted(keys)})
    content_rows = [{key: value for key, value in row.items() if key != "sourceRowNumber"} for row in rows]
    content_hash = _digest({"domain": "customer-service", "scope": {"shopName": shop_name, "identitySetHash": identity_hash}, "rows": sorted(content_rows, key=lambda item: str(item["conversationKey"]))})

    with transaction.atomic():
        lock_active_authority()
        _scope_lock(scope_key)
        head, _created = CustomerServiceImportScopeHead.objects.select_for_update().get_or_create(
            scope_key=scope_key,
            defaults={"shop_name": shop_name, "state_token": "0" * 64},
        )
        if head.shop_name != shop_name or head.status == "processing":
            raise CustomerServiceApiError("同一客服店铺范围正在处理或身份冲突", code="conflict", status=409)
        current = CustomerServiceImportBatch.objects.filter(id=head.current_batch_id, status="completed").first() if head.current_batch_id else None
        if current and current.content_hash == content_hash and current.identity_set_hash == identity_hash:
            CustomerServiceImportAttempt.objects.create(
                domain="customer-service", batch_id=current.id, scope_key=scope_key,
                scope_json={"shopName": shop_name, "identitySetHash": identity_hash},
                import_hash=current.import_hash, raw_file_hash=raw_hash, content_hash=content_hash,
                row_count=len(rows), file_name=f"{session_file} + {chat_file}", file_size_bytes=file_size,
                actor_email=actor_email[:320], warnings_json=warning_summary, outcome="duplicate",
            )
            return {"status": "duplicate", "batch": _batch_payload(current), **_public_warnings(warning_summary)}
        owner_token = uuid.uuid4().hex
        head.status = "processing"
        head.owner_token = owner_token
        head.generation = int(head.generation) + 1
        head.save()
        import_hash = _digest({"scopeKey": scope_key, "contentHash": content_hash, "stateToken": head.state_token})
        batch_id = f"cs_{import_hash}"
        attempt = CustomerServiceImportAttempt.objects.create(
            domain="customer-service", batch_id=batch_id, scope_key=scope_key,
            scope_json={"shopName": shop_name, "identitySetHash": identity_hash},
            import_hash=import_hash, raw_file_hash=raw_hash, content_hash=content_hash,
            row_count=len(rows), file_name=f"{session_file} + {chat_file}", file_size_bytes=file_size,
            actor_email=actor_email[:320], warnings_json=warning_summary, outcome="processing",
        )
        batch = CustomerServiceImportBatch.objects.create(
            id=batch_id, shop_name=shop_name, session_file_name=session_file,
            chat_file_name=chat_file, raw_file_hash=raw_hash, import_hash=import_hash,
            content_hash=content_hash, identity_set_hash=identity_hash, scope_key=scope_key,
            status="processing", conversation_count=len(rows), matched_count=matched_count,
            session_only_count=normalized_summary["sessionOnlyCount"],
            chat_only_count=normalized_summary["chatOnlyCount"],
            ambiguous_count=normalized_summary["ambiguousCount"], warnings_json=warning_summary,
            actor_email=actor_email[:320],
        )
        now = timezone.now()
        for row in rows:
            if row["matchStatus"] == "chat_only" and row["chatStartedAt"] and row["messages"]:
                CustomerServiceConversation.objects.filter(
                    shop_name=shop_name,
                    match_status="chat_only",
                    chat_started_at=row["chatStartedAt"],
                    chat_ended_at=row["chatEndedAt"],
                    chat_customer_alias=row["chatCustomerAlias"],
                    messages=row["messages"],
                ).exclude(conversation_key=row["conversationKey"]).delete()
            existing = CustomerServiceConversation.objects.select_for_update().filter(conversation_key=row["conversationKey"]).first()
            values = {
                "last_import_batch_id": batch_id, "shop_name": shop_name,
                "consulted_at": row["consultedAt"], "customer_id": row["customerId"],
                "customer_alias": row["customerAlias"], "consultation_type": row["consultationType"],
                "agent": row["agent"], "transferred_agent": row["transferredAgent"],
                "skill_group": row["skillGroup"], "product_sku": row["productSku"],
                "product_name": row["productName"], "first_response_at": row["firstResponseAt"],
                "response_seconds": row["responseSeconds"], "duration_minutes": row["durationMinutes"],
                "customer_message_count": row["customerMessageCount"], "agent_message_count": row["agentMessageCount"],
                "satisfaction": row["satisfaction"], "resolved": row["resolved"],
                "conversation_id": row["conversationId"], "match_status": row["matchStatus"],
                "match_confidence": row["matchConfidence"], "chat_started_at": row["chatStartedAt"],
                "chat_ended_at": row["chatEndedAt"], "chat_customer_alias": row["chatCustomerAlias"],
                "messages": row["messages"],
            }
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.version = int(existing.version) + 1
                existing.save()
            else:
                CustomerServiceConversation.objects.create(
                    conversation_key=row["conversationKey"], first_import_batch_id=batch_id,
                    version=1, **values,
                )
        if CustomerServiceConversation.objects.filter(last_import_batch_id=batch_id).count() != len(rows):
            raise CustomerServiceApiError("客服会话发布后批次行数回查不一致", code="service_unavailable", status=503)
        state_token = _digest({"previous": head.state_token, "batchId": batch_id, "contentHash": content_hash, "rowCount": len(rows)})
        batch.status = "completed"
        batch.published_state_token = state_token
        batch.completed_at = now
        batch.save()
        CustomerServiceImportFingerprint.objects.create(
            domain="customer-service", batch_id=batch_id, scope_key=scope_key,
            scope_json={"shopName": shop_name, "identitySetHash": identity_hash},
            import_hash=import_hash, raw_file_hash=raw_hash, content_hash=content_hash,
            row_count=len(rows), outcome="imported", published_state_token=state_token,
        )
        attempt.outcome = "imported"
        attempt.save(update_fields=["outcome", "updated_at"])
        head.state_token = state_token
        head.status = "ready"
        head.owner_token = ""
        head.current_batch_id = batch_id
        head.save()
        bump_revision({"kind": "import", "batchId": batch_id, "rowCount": len(rows), "contentHash": content_hash})
        verified = CustomerServiceImportBatch.objects.get(id=batch_id)
        return {"status": "imported", "batch": _batch_payload(verified), **_public_warnings(warning_summary)}


def list_import_batches(*, page: int, page_size: int) -> dict[str, object]:
    query = CustomerServiceImportBatch.objects.order_by("-created_at", "-id")
    total = query.count()
    items = [_batch_payload(item) for item in query[(page - 1) * page_size:page * page_size]]
    return {
        "items": items,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": page * page_size < total},
    }
