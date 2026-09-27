"""Bounded, operation-specific finance reads for non-finance consumers.

This module intentionally exposes no SQL-shaped input.  Every operation has a
fixed request/response contract so moving finance facts out of D1 cannot turn
global search (or a future consumer) into an unbounded database proxy.
"""

from __future__ import annotations

import json
import re

from django.db.models import Q, QuerySet
from django.http import HttpRequest

from sales.auth import Principal

from .errors import FinanceApiError
from .models import FinanceImportBatch, FinanceLine, FinanceTarget


CONSUMER_OPERATIONS = frozenset(
    {"line_search", "target_search", "import_batch_search"}
)
CONSUMER_BODY_MAX_BYTES = 64 * 1024
JSON_CONTENT_TYPE_RE = re.compile(
    r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I
)


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def parse_consumer_body(request: HttpRequest) -> dict[str, object]:
    content_type = request.headers.get("Content-Type", "")
    if not JSON_CONTENT_TYPE_RE.match(content_type):
        raise FinanceApiError("消费者查询只接受 JSON 请求。")
    if len(request.body) > CONSUMER_BODY_MAX_BYTES:
        raise FinanceApiError(
            "消费者查询请求超出安全上限。",
            code="payload_too_large",
            status=413,
        )
    try:
        payload = json.loads(
            request.body.decode("utf-8"), object_pairs_hook=_duplicate_safe_object
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise FinanceApiError("消费者查询请求不是有效 JSON。") from error
    if not isinstance(payload, dict):
        raise FinanceApiError("消费者查询请求必须是 JSON 对象。")
    return validate_consumer_request(payload)


def _integer(
    payload: dict[str, object], key: str, *, minimum: int, maximum: int
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FinanceApiError(f"{key} 必须为整数。")
    if value < minimum or value > maximum:
        raise FinanceApiError(f"{key} 超出允许范围。")
    return value


def validate_consumer_request(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"operation", "query", "offset", "limit"}:
        raise FinanceApiError("消费者查询字段不完整或包含未知字段。")
    operation = payload.get("operation")
    if not isinstance(operation, str) or operation not in CONSUMER_OPERATIONS:
        raise FinanceApiError("operation 不在固定财务消费者查询清单中。")
    query = payload.get("query")
    if not isinstance(query, str):
        raise FinanceApiError("query 必须是字符串。")
    query = query.strip()
    if len(query) < 2 or len(query) > 80:
        raise FinanceApiError("query 长度必须在 2 到 80 个字符之间。")
    return {
        "operation": operation,
        "query": query,
        "offset": _integer(payload, "offset", minimum=0, maximum=80_000),
        "limit": _integer(payload, "limit", minimum=1, maximum=100),
    }


def _access(principal: Principal, operation: str) -> None:
    if operation in {"line_search", "target_search"}:
        if principal.role not in {"analyst", "admin"}:
            raise FinanceApiError(
                "当前账号无权检索财务数据。", code="access_denied", status=403
            )
        return
    if principal.role not in {"operator", "admin"} or principal.scope is not None:
        raise FinanceApiError(
            "当前账号无权检索财务导入批次。",
            code="access_denied",
            status=403,
        )


def _bounded(value: object, maximum: int) -> str:
    return str(value or "")[:maximum]


def _page(queryset: QuerySet, offset: int, limit: int) -> tuple[list[object], int]:
    total = queryset.count()
    return list(queryset[offset : offset + limit]), total


def _line_search(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    query = str(request["query"])
    rows = FinanceLine.objects.filter(
        Q(subject_name__icontains=query)
        | Q(metric_key__icontains=query)
        | Q(scope_name__icontains=query)
        | Q(scope_key__icontains=query)
        | Q(group_name__icontains=query)
        | Q(month__icontains=query)
        | Q(raw_value__icontains=query)
    )
    if principal.scope is not None:
        values = list(
            dict.fromkeys(
                [*principal.scope["channels"], *principal.scope["platforms"]]
            )
        )
        rows = (
            rows.filter(Q(scope_name__in=values) | Q(scope_key__in=values))
            if values
            else rows.none()
        )
    rows = rows.order_by("-month", "sort_order", "-id")
    offset = int(request["offset"])
    limit = int(request["limit"])
    page, total = _page(rows, offset, limit)
    return {
        "items": [
            {
                "id": str(row.id),
                "title": _bounded(row.subject_name, 200),
                "subtitle": _bounded(
                    row.month + (f" · {row.scope_name}" if row.scope_name else ""),
                    240,
                ),
                "detail": _bounded(
                    row.section + (f" · {row.group_name}" if row.group_name else ""),
                    400,
                ),
                "updatedAt": _bounded(row.created_at, 48),
                "amountCents": row.amount_cents,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + limit < total,
    }


def _target_search(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    query = str(request["query"])
    rows = FinanceTarget.objects.filter(
        Q(period_key__icontains=query)
        | Q(period_type__icontains=query)
        | Q(platform__icontains=query)
        | Q(shop_name__icontains=query)
        | Q(category__icontains=query)
        | Q(manager__icontains=query)
        | Q(id__icontains=query)
    )
    if principal.scope is not None:
        platforms = principal.scope["platforms"]
        rows = rows.filter(platform__in=platforms) if platforms else rows.none()
    rows = rows.order_by("-updated_at", "-period_key", "id")
    offset = int(request["offset"])
    limit = int(request["limit"])
    page, total = _page(rows, offset, limit)
    return {
        "items": [
            {
                "id": _bounded(row.id, 160),
                "title": _bounded(row.period_key, 200),
                "subtitle": _bounded(
                    (f"{row.platform} · " if row.platform else "")
                    + (row.shop_name or "全局")
                    + (f" · {row.category}" if row.category else ""),
                    240,
                ),
                "detail": _bounded(
                    row.period_type + (f" · {row.manager}" if row.manager else ""),
                    400,
                ),
                "updatedAt": _bounded(row.updated_at, 48),
                "amountCents": row.sales_target_cents,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + limit < total,
    }


def _import_batch_search(
    _principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    query = str(request["query"])
    rows = FinanceImportBatch.objects.filter(
        Q(id__icontains=query)
        | Q(file_name__icontains=query)
        | Q(source__icontains=query)
        | Q(status__icontains=query)
    ).order_by("-created_at", "id")
    offset = int(request["offset"])
    limit = int(request["limit"])
    page, total = _page(rows, offset, limit)
    return {
        "items": [
            {
                "id": _bounded(row.id, 160),
                "source": "月度财报",
                "fileName": _bounded(row.file_name, 500),
                "status": _bounded(row.status, 50),
                "rowCount": int(row.row_count),
                "createdAt": _bounded(row.created_at, 80),
                "completedAt": _bounded(row.completed_at, 80)
                if row.completed_at
                else None,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + limit < total,
    }


def execute_consumer_query(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    operation = str(request["operation"])
    _access(principal, operation)
    if operation == "line_search":
        return _line_search(principal, request)
    if operation == "target_search":
        return _target_search(principal, request)
    return _import_batch_search(principal, request)
