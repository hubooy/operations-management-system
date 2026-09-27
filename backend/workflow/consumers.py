"""Bounded read-only workflow projections for cross-domain consumers."""

from __future__ import annotations

from sales.auth import Principal
from django.db.models import Q

from .errors import WorkflowApiError
from .models import NewProductProject, WorkflowOperationRecord, WorkflowTask
from .new_products import search_projects
from .operations import _iso, _scope_query


OPERATIONS = frozenset({"launch_project_search", "workflow_search"})


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WorkflowApiError(f"{label} 超出允许范围")
    return value


def validate_consumer_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"operation", "query", "offset", "limit"}:
        raise WorkflowApiError("运营事务消费查询字段不完整或包含未知字段")
    operation = payload.get("operation")
    if operation not in OPERATIONS:
        raise WorkflowApiError("operation 不在固定运营事务消费查询清单中")
    query = payload.get("query")
    if not isinstance(query, str):
        raise WorkflowApiError("query 必须是字符串")
    query = query.strip()
    if not 2 <= len(query) <= 80:
        raise WorkflowApiError("query 长度必须在 2 到 80 个字符之间")
    return {
        "operation": operation,
        "query": query,
        "offset": _integer(payload.get("offset"), "offset", 0, 80_000),
        "limit": _integer(payload.get("limit"), "limit", 1, 100),
    }


def execute_consumer_query(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    if principal.role not in {"viewer", "analyst", "operator", "admin"}:
        raise WorkflowApiError("当前账号无权检索运营事务", code="access_denied", status=403)
    operation = str(request["operation"])
    if operation == "launch_project_search":
        if principal.scope is not None:
            raise WorkflowApiError("当前账号无权检索新品项目", code="access_denied", status=403)
        return search_projects(
            str(request["query"]),
            offset=int(request["offset"]),
            limit=int(request["limit"]),
        )
    return _search_workflow(
        str(request["query"]),
        offset=int(request["offset"]),
        limit=int(request["limit"]),
        principal=principal,
    )


def _search_workflow(query: str, *, offset: int, limit: int, principal: Principal) -> dict[str, object]:
    maximum = offset + limit
    rows: list[dict[str, object]] = []
    total = 0
    if principal.scope is None:
        projects = NewProductProject.objects.filter(deleted_at__isnull=True).filter(
            Q(product_name__icontains=query) | Q(supplier_name__icontains=query)
            | Q(brand__icontains=query) | Q(category__icontains=query)
            | Q(erp_product_code__icontains=query) | Q(sku_code__icontains=query)
            | Q(spu_code__icontains=query) | Q(owner__icontains=query)
        )
        total += projects.count()
        for row in projects.order_by("-updated_at", "id")[:maximum]:
            rows.append({
                "resultId": f"launch:{row.id}", "title": row.product_name,
                "subtitle": f"新品上架 · {row.lifecycle_status}",
                "detail": " · ".join(value for value in (row.supplier_name, row.owner, row.category) if value),
                "updatedAt": _iso(row.updated_at),
                "amountCents": row.approved_price_cents if row.approved_price_cents is not None else row.recommended_price_cents,
                "targetHint": "launch",
            })
        tasks = WorkflowTask.objects.filter(deleted_at__isnull=True).filter(
            Q(title__icontains=query) | Q(work_content__icontains=query) | Q(category__icontains=query)
            | Q(owner__icontains=query) | Q(shop_name__icontains=query) | Q(status__icontains=query)
            | Q(priority__icontains=query)
        )
        total += tasks.count()
        for row in tasks.order_by("-updated_at", "id")[:maximum]:
            detail = row.work_content or row.shop_name
            if row.owner:
                detail = f"{detail} · {row.owner}" if detail else row.owner
            rows.append({
                "resultId": f"task:{row.id}", "title": row.title,
                "subtitle": f"{row.category}{f' · {row.status}' if row.status else ''}", "detail": detail,
                "updatedAt": _iso(row.updated_at), "amountCents": None, "targetHint": "task",
            })
    records = WorkflowOperationRecord.objects.filter(deleted_at__isnull=True).filter(_scope_query(principal)).filter(
        Q(title__icontains=query) | Q(content__icontains=query) | Q(owner__icontains=query)
        | Q(shop_name__icontains=query) | Q(status__icontains=query) | Q(priority__icontains=query)
    )
    total += records.count()
    labels = {"inspection": "巡店检查", "review": "评价维护"}
    for row in records.order_by("-updated_at", "id")[:maximum]:
        detail = row.content or row.shop_name
        if row.owner:
            detail = f"{detail} · {row.owner}" if detail else row.owner
        rows.append({
            "resultId": f"operation:{row.id}", "title": row.title,
            "subtitle": f"{labels.get(row.record_type, '运营事务')}{f' · {row.status}' if row.status else ''}",
            "detail": detail, "updatedAt": _iso(row.updated_at), "amountCents": None,
            "targetHint": row.record_type,
        })
    rows.sort(key=lambda item: str(item["resultId"]))
    rows.sort(key=lambda item: str(item["updatedAt"]), reverse=True)
    items = rows[offset:offset + limit]
    return {"items": items, "total": total, "truncated": offset + len(items) < total}
