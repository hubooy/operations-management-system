from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import json
import uuid

from django.db import transaction
from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from sales.auth import Principal

from .errors import CustomerServiceApiError
from .models import CustomerServiceConversation, CustomerServiceDeletionAudit, CustomerServiceImportBatch
from .revisions import bump_revision
from .write_requests import lock_active_authority


ROBOT_SCOPES = {"robot_only", "contains_robot", "exclude_robot"}
PROBLEM_TYPES = {"商品咨询", "价格优惠", "物流发货", "售后维修", "退换货", "安装使用", "发票开票", "催单改单", "其他"}
CONVERSION_STATUSES = {"converted", "not_converted", "unknown"}
MATCH_STATUSES = {"matched", "session_only", "chat_only", "ambiguous"}
MESSAGE_LIMIT = 200
AI_MESSAGE_LIMIT = 24
MESSAGE_CONTENT_LIMIT = 1_000
MESSAGE_BYTES_LIMIT = 64 * 1024


def _iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _bounded_messages(item: CustomerServiceConversation, include: bool, limit: int) -> tuple[list[dict[str, str]], int, bool]:
    source = item.messages if isinstance(item.messages, list) else []
    total = len(source)
    if not include:
        return [], total, total > 0
    output: list[dict[str, str]] = []
    for raw in source[:limit]:
        if not isinstance(raw, dict):
            continue
        candidate = {
            "sender": str(raw.get("sender") or "")[:120],
            "sentAt": str(raw.get("sentAt") or "")[:80],
            "content": str(raw.get("content") or "")[:MESSAGE_CONTENT_LIMIT],
        }
        if len(json.dumps(output + [candidate], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MESSAGE_BYTES_LIMIT:
            break
        output.append(candidate)
    return output, total, len(output) < total


def conversation_payload(item: CustomerServiceConversation, *, include_messages: bool = False, message_limit: int = MESSAGE_LIMIT) -> dict[str, object]:
    messages, total, truncated = _bounded_messages(item, include_messages, message_limit)
    return {
        "id": int(item.id), "shopName": item.shop_name, "consultedAt": item.consulted_at,
        "customerId": item.customer_id, "customerAlias": item.customer_alias,
        "consultationType": item.consultation_type, "agent": item.agent,
        "transferredAgent": item.transferred_agent, "skillGroup": item.skill_group,
        "productSku": item.product_sku, "matchedSkuId": "", "productSpuId": "",
        "erpProductCode": "", "productCategory": "", "productName": item.product_name,
        "firstResponseAt": item.first_response_at, "responseSeconds": _number(item.response_seconds),
        "durationMinutes": _number(item.duration_minutes), "customerMessageCount": item.customer_message_count,
        "agentMessageCount": item.agent_message_count, "satisfaction": item.satisfaction,
        "resolved": item.resolved, "conversationId": item.conversation_id,
        "matchStatus": item.match_status, "matchConfidence": item.match_confidence,
        "chatStartedAt": item.chat_started_at, "chatEndedAt": item.chat_ended_at,
        "chatCustomerAlias": item.chat_customer_alias, "messages": messages,
        "messageTotalCount": total, "messageReturnedCount": len(messages), "messagesTruncated": truncated,
        "robotScope": item.robot_scope, "problemType": item.problem_type,
        "conversionStatus": item.conversion_status, "serviceIssues": item.service_issues,
        "summaryText": item.summary_text, "analysisSource": item.analysis_source,
        "analyzedAt": _iso(item.analyzed_at) if item.analyzed_at else None,
        "annotatedAt": _iso(item.annotated_at) if item.annotated_at else None,
        "version": int(item.version), "updatedAt": _iso(item.updated_at),
    }


def _date(value: object, label: str) -> date | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise CustomerServiceApiError(f"{label}必须为 YYYY-MM-DD 格式")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise CustomerServiceApiError(f"{label}不是有效自然日期") from error
    if parsed.isoformat() != value:
        raise CustomerServiceApiError(f"{label}不是有效自然日期")
    return parsed


def _selections(value: object, label: str, maximum: int, length: int, allowed: set[str] | None = None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CustomerServiceApiError(f"{label}无效")
    if any(not isinstance(item, str) for item in value):
        raise CustomerServiceApiError(f"{label}无效")
    result = list(dict.fromkeys(item.strip() for item in value if item.strip()))
    if len(result) > maximum or any(len(item) > length for item in result) or (allowed and any(item not in allowed for item in result)):
        raise CustomerServiceApiError(f"{label}无效")
    return result


def _base_query(options: dict[str, object]) -> QuerySet[CustomerServiceConversation]:
    query = CustomerServiceConversation.objects.all()
    shops = _selections(options.get("shopNames"), "店铺筛选", 50, 100)
    agents = _selections(options.get("agents"), "客服筛选", 50, 200)
    statuses = _selections(options.get("statuses"), "匹配状态筛选", 20, 32, MATCH_STATUSES)
    robots = _selections(options.get("robotScopes"), "机器人筛选", 20, 32, ROBOT_SCOPES)
    problems = _selections(options.get("problemTypes"), "问题类型筛选", 20, 32, PROBLEM_TYPES)
    conversions = _selections(options.get("conversionStatuses"), "转化状态筛选", 20, 32, CONVERSION_STATUSES)
    products = _selections(options.get("productSkus"), "商品筛选", 5_000, 200)
    start = _date(options.get("startDate"), "开始日期")
    end = _date(options.get("endDate"), "结束日期")
    if start and end and start > end:
        raise CustomerServiceApiError("开始日期不能晚于结束日期")
    if shops:
        query = query.filter(shop_name__in=shops)
    if agents:
        query = query.filter(agent__in=agents)
    if statuses:
        query = query.filter(match_status__in=statuses)
    if robots:
        query = query.filter(robot_scope__in=robots)
    if problems:
        query = query.filter(problem_type__in=problems)
    if conversions:
        query = query.filter(conversion_status__in=conversions)
    if products:
        query = query.filter(product_sku__in=products)
    if start:
        query = query.filter(consulted_at__gte=f"{start.isoformat()} 00:00:00")
    if end:
        query = query.filter(consulted_at__lt=f"{(end + timedelta(days=1)).isoformat()} 00:00:00")
    search = str(options.get("query") or "").strip()
    if search:
        if not 2 <= len(search) <= 100:
            raise CustomerServiceApiError("搜索关键词长度必须为 2 到 100 个字符")
        query = query.annotate(messages_text=Cast("messages", TextField())).filter(
            Q(customer_id__icontains=search) | Q(customer_alias__icontains=search)
            | Q(chat_customer_alias__icontains=search) | Q(agent__icontains=search)
            | Q(product_sku__icontains=search) | Q(product_name__icontains=search)
            | Q(messages_text__icontains=search) | Q(service_issues__icontains=search)
            | Q(summary_text__icontains=search)
        )
    return query


def list_conversations(options: dict[str, object]) -> dict[str, object]:
    page = options.get("page", 1)
    page_size = options.get("pageSize", 30)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10_000:
        raise CustomerServiceApiError("page 超出允许范围")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise CustomerServiceApiError("pageSize 超出允许范围")
    include_options = options.get("includeOptions", True)
    if not isinstance(include_options, bool):
        raise CustomerServiceApiError("includeOptions 必须是布尔值")
    query = _base_query(options)
    total = query.count()
    matched = query.filter(match_status="matched").count()
    session_only = query.filter(match_status="session_only").count()
    chat_only = query.filter(match_status="chat_only").count()
    rows = list(query.order_by("-consulted_at", "-id")[(page - 1) * page_size:page * page_size])
    agents: list[str] = []
    shops: list[str] = []
    product_skus: list[str] = []
    if include_options:
        agents = list(CustomerServiceConversation.objects.exclude(agent="").order_by("agent").values_list("agent", flat=True).distinct()[:100])
        shops = list(CustomerServiceConversation.objects.exclude(shop_name="").order_by("shop_name").values_list("shop_name", flat=True).distinct()[:100])
        product_skus = list(CustomerServiceConversation.objects.exclude(product_sku="").order_by("product_sku").values_list("product_sku", flat=True).distinct()[:5001])
        if len(product_skus) > 5_000:
            raise CustomerServiceApiError("客服商品筛选范围超过 5000 个有界映射上限", code="service_unavailable", status=503)
    return {
        "items": [conversation_payload(item) for item in rows],
        "agents": agents, "shops": shops, "productSkus": product_skus,
        "summary": {"total": total, "matched": matched, "sessionOnly": session_only, "chatOnly": chat_only},
        "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(rows), "truncated": page * page_size < total},
    }


def get_conversation(conversation_id: int) -> dict[str, object]:
    item = CustomerServiceConversation.objects.filter(id=conversation_id).first()
    if item is None:
        raise CustomerServiceApiError("客服会话不存在", code="not_found", status=404)
    return conversation_payload(item, include_messages=True)


def get_conversations_by_ids(ids: list[int], *, message_limit: int = AI_MESSAGE_LIMIT) -> list[dict[str, object]]:
    if not ids:
        return []
    rows = CustomerServiceConversation.objects.filter(id__in=ids).order_by("-consulted_at", "-id")
    return [conversation_payload(item, include_messages=True, message_limit=message_limit) for item in rows]


def update_annotation(conversation_id: int, expected_version: int, payload: dict[str, object]) -> dict[str, object]:
    allowed = {"robotScope", "problemType", "conversionStatus", "serviceIssues", "summaryText", "analysisSource"}
    if not payload or not set(payload).issubset(allowed):
        raise CustomerServiceApiError("客服标注字段集合无效")
    with transaction.atomic():
        lock_active_authority()
        item = CustomerServiceConversation.objects.select_for_update().filter(id=conversation_id).first()
        if item is None:
            raise CustomerServiceApiError("客服会话不存在", code="not_found", status=404)
        if int(item.version) != expected_version:
            raise CustomerServiceApiError("客服会话已被其他操作更新，请刷新后重试", code="version_conflict", status=409)
        if "robotScope" in payload:
            if payload["robotScope"] not in ROBOT_SCOPES:
                raise CustomerServiceApiError("机器人标注无效", status=422)
            item.robot_scope = str(payload["robotScope"])
        if "problemType" in payload:
            if payload["problemType"] not in PROBLEM_TYPES:
                raise CustomerServiceApiError("问题类型无效", status=422)
            item.problem_type = str(payload["problemType"])
        if "conversionStatus" in payload:
            if payload["conversionStatus"] not in CONVERSION_STATUSES:
                raise CustomerServiceApiError("订单转化状态无效", status=422)
            item.conversion_status = str(payload["conversionStatus"])
        if "serviceIssues" in payload:
            if not isinstance(payload["serviceIssues"], str):
                raise CustomerServiceApiError("服务问题标注无效", status=422)
            item.service_issues = payload["serviceIssues"].strip()[:1000]
        if "summaryText" in payload:
            if not isinstance(payload["summaryText"], str):
                raise CustomerServiceApiError("客服摘要标注无效", status=422)
            item.summary_text = payload["summaryText"].strip()[:1000]
        if "analysisSource" in payload:
            if payload["analysisSource"] not in {"ai", "manual"}:
                raise CustomerServiceApiError("分析来源无效", status=422)
            item.analysis_source = str(payload["analysisSource"])
        item.annotated_at = timezone.now()
        if item.analysis_source == "ai":
            item.analyzed_at = timezone.now()
        item.version = int(item.version) + 1
        item.save()
        bump_revision({"kind": "annotation", "conversationId": conversation_id, "version": int(item.version)})
        return {"id": conversation_id, "updated": True, "version": int(item.version), "updatedAt": _iso(item.updated_at)}


def delete_conversation(conversation_id: int, expected_version: int, actor: str, reason: str) -> dict[str, object]:
    normalized_reason = reason.strip()
    if not normalized_reason or len(normalized_reason) > 200:
        raise CustomerServiceApiError("删除原因必须为 1 到 200 字")
    with transaction.atomic():
        lock_active_authority()
        item = CustomerServiceConversation.objects.select_for_update().filter(id=conversation_id).first()
        if item is None:
            raise CustomerServiceApiError("客服会话不存在或已被删除", code="not_found", status=404)
        if int(item.version) != expected_version:
            raise CustomerServiceApiError("客服会话已被其他操作更新，请刷新后重试", code="version_conflict", status=409)
        audit_id = uuid.uuid4()
        CustomerServiceDeletionAudit.objects.create(
            audit_id=audit_id, conversation_id=conversation_id, conversation_key=item.conversation_key,
            actor=actor[:320], old_version=int(item.version), expected_version=expected_version,
            reason=normalized_reason,
        )
        item.delete()
        bump_revision({"kind": "delete", "conversationId": conversation_id, "auditId": str(audit_id)})
        return {"id": conversation_id, "deleted": True, "auditId": str(audit_id)}


def customer_search(principal: Principal, payload: dict[str, object]) -> dict[str, object]:
    if principal.scope is not None:
        raise CustomerServiceApiError("客服会话只允许无数据范围限制的账号读取", code="access_denied", status=403)
    query = str(payload.get("query") or "").strip()
    offset = payload.get("offset")
    limit = payload.get("limit")
    include_messages = payload.get("includeMessages", False)
    if not 2 <= len(query) <= 80 or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 80_000 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(include_messages, bool):
        raise CustomerServiceApiError("客服搜索参数无效")
    rows = CustomerServiceConversation.objects.annotate(messages_text=Cast("messages", TextField()))
    conditions = (
        Q(customer_id__icontains=query) | Q(customer_alias__icontains=query)
        | Q(chat_customer_alias__icontains=query) | Q(agent__icontains=query)
        | Q(product_sku__icontains=query) | Q(product_name__icontains=query)
        | Q(conversation_id__icontains=query) | Q(problem_type__icontains=query)
        | Q(service_issues__icontains=query) | Q(summary_text__icontains=query)
    )
    if include_messages:
        conditions |= Q(messages_text__icontains=query)
    rows = rows.filter(conditions)
    total = rows.count()
    items = [{
        "resultId": str(item.id),
        "title": item.customer_alias or item.customer_id or item.chat_customer_alias or "匿名顾客",
        "subtitle": f"{item.product_name or item.product_sku or '未关联商品'}{f' · {item.agent}' if item.agent else ''}",
        "detail": f"{item.summary_text or item.service_issues or item.consultation_type}{f' · {item.problem_type}' if item.problem_type else ''}",
        "updatedAt": item.consulted_at,
        "amountCents": None,
    } for item in rows.order_by("-consulted_at", "-id")[offset:offset + limit]]
    return {"items": items, "total": total, "truncated": offset + len(items) < total}


def import_batch_search(principal: Principal, payload: dict[str, object]) -> dict[str, object]:
    if principal.scope is not None or principal.role not in {"operator", "admin"}:
        raise CustomerServiceApiError("客服导入历史只允许无范围限制的操作员或管理员读取", code="access_denied", status=403)
    query = str(payload.get("query") or "").strip()
    offset = payload.get("offset")
    limit = payload.get("limit")
    if not 2 <= len(query) <= 80 or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 80_000 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CustomerServiceApiError("客服导入搜索参数无效")
    rows = CustomerServiceImportBatch.objects.filter(
        Q(id__icontains=query) | Q(session_file_name__icontains=query)
        | Q(chat_file_name__icontains=query) | Q(status__icontains=query)
    )
    total = rows.count()
    items = [{
        "id": item.id, "source": "客服会话", "fileName": f"{item.session_file_name} / {item.chat_file_name}",
        "status": item.status, "rowCount": int(item.conversation_count),
        "createdAt": _iso(item.created_at), "completedAt": _iso(item.completed_at) if item.completed_at else None,
    } for item in rows.order_by("-created_at", "-id")[offset:offset + limit]]
    return {"items": items, "total": total, "truncated": offset + len(items) < total}
