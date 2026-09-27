from __future__ import annotations

import re
import uuid

from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Q, Sum, When

from .errors import InventoryApiError
from .models import InventoryImportScopeHead, InventoryStockLine, ReplenishmentPlanItem
from .revisions import bump_revision
from .replenishment_health import lock_stock_scope, start_cycle
from .write_requests import lock_active_authority


STATUSES = frozenset({"draft", "confirmed", "completed", "cancelled"})
TRANSITIONS = {
    "draft": {"draft", "confirmed", "cancelled"},
    "confirmed": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def plan_payload(plan: ReplenishmentPlanItem) -> dict[str, object]:
    return {
        "id": plan.id,
        "sourceBatchId": plan.source_batch_id,
        "productCode": plan.product_code,
        "productName": plan.product_name,
        "brand": plan.brand,
        "category": plan.category,
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
        "suggestedQuantity": int(plan.suggested_quantity),
        "plannedQuantity": int(plan.planned_quantity),
        "coverageDays": (
            float(plan.coverage_days_tenths) / 10
            if plan.coverage_days_tenths is not None
            else None
        ),
        "reason": plan.reason,
        "notes": plan.notes,
        "status": plan.status,
        "dingTalkSync": {
            "status": plan.dingtalk_sync_status,
            "syncedAt": plan.dingtalk_synced_at.isoformat() if plan.dingtalk_synced_at else None,
            "error": plan.dingtalk_sync_error if plan.dingtalk_sync_status == "failed" else "",
        },
        "createdAt": plan.created_at.isoformat(),
        "updatedAt": plan.updated_at.isoformat(),
    }


def _selected(values: object, maximum: int) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > maximum:
        raise InventoryApiError("备货计划筛选无效")
    output: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
            raise InventoryApiError("备货计划筛选无效")
        if value.strip() not in output:
            output.append(value.strip())
    return output


def query_plans(options: dict[str, object]) -> dict[str, object]:
    page = int(options.get("page", 1))
    page_size = int(options.get("pageSize", 50))
    if not 1 <= page <= 10_000 or not 1 <= page_size <= 100:
        raise InventoryApiError("备货计划分页参数无效")
    rows = ReplenishmentPlanItem.objects.all()
    status = options.get("status")
    if status is not None:
        if status not in STATUSES:
            raise InventoryApiError("备货计划状态无效")
        rows = rows.filter(status=status)
    elif options.get("includeCancelled") is not True:
        rows = rows.exclude(status="cancelled")
    query = options.get("query")
    if query:
        if not isinstance(query, str) or len(query) > 100:
            raise InventoryApiError("备货计划搜索词无效")
        words = list(dict.fromkeys(re.split(r"[\s,，;；]+", query.strip())))[:8]
        search = Q()
        for word in words:
            search |= Q(product_code__icontains=word) | Q(product_name__icontains=word) | Q(warehouse__icontains=word)
        rows = rows.filter(search)
    warehouses = _selected(options.get("warehouses"), 10)
    if warehouses:
        rows = rows.filter(warehouse__in=warehouses)
    brands = _selected(options.get("brands"), 20)
    categories = _selected(options.get("categories"), 20)
    if brands or categories:
        matching = InventoryStockLine.objects.filter(
            batch_id__in=rows.values("source_batch_id"),
        )
        if brands:
            matching = matching.filter(brand__in=brands)
        if categories:
            matching = matching.filter(category__in=categories)
        keys = matching.values_list("batch_id", "warehouse", "product_code")
        allowed = {(batch, warehouse, product) for batch, warehouse, product in keys}
        plan_ids = [
            plan.id
            for plan in rows.only("id", "source_batch_id", "warehouse", "product_code")
            if (plan.source_batch_id, plan.warehouse, plan.product_code) in allowed
        ]
        rows = rows.filter(id__in=plan_ids)
    rows = rows.annotate(
        status_order=Case(
            When(status="draft", then=0),
            When(status="confirmed", then=1),
            default=2,
            output_field=IntegerField(),
        )
    ).order_by("status_order", "-updated_at", "-id")
    total = rows.count()
    offset = (page - 1) * page_size
    page_rows = list(rows[offset : offset + page_size])
    return {
        "items": [plan_payload(plan) for plan in page_rows],
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(page_rows),
            "totalPages": (total + page_size - 1) // page_size,
            "truncated": offset + len(page_rows) < total,
        },
    }


def plan_summary(current_batch_id: str | None = None) -> dict[str, int]:
    rows = ReplenishmentPlanItem.objects.all()
    counts = {
        status: rows.filter(status=status).count()
        for status in ("draft", "confirmed", "completed", "cancelled")
    }
    active = rows.filter(Q(status__in=["draft", "confirmed"]) | Q(status="completed", source_batch_id=current_batch_id or ""))
    active_quantity = active.aggregate(total=Sum("planned_quantity"))["total"] or 0
    return {
        "draftCount": counts["draft"],
        "confirmedCount": counts["confirmed"],
        "completedCount": counts["completed"],
        "cancelledCount": counts["cancelled"],
        "activeQuantity": int(active_quantity),
    }


def get_plan(plan_id: str) -> ReplenishmentPlanItem | None:
    return ReplenishmentPlanItem.objects.filter(id=plan_id).first()


def _upsert_plan_locked(data: dict[str, object], actor_email: str) -> ReplenishmentPlanItem:
    requested_status = str(data.get("status") or "draft")
    if requested_status not in {"draft", "confirmed"}:
        raise InventoryApiError("新建备货计划状态无效")
    lookup = {
        "source_batch_id": str(data["sourceBatchId"]),
        "warehouse": str(data["warehouse"]),
        "product_code": str(data["productCode"]),
        "status": requested_status,
    }
    defaults = {
        "product_name": str(data["productName"]),
        "brand": str(data.get("brand") or ""),
        "category": str(data.get("category") or ""),
        "supplier": str(data.get("supplier") or ""),
        "buyer": str(data.get("buyer") or ""),
        "operator_name": str(data.get("operatorName") or ""),
        "department": str(data.get("department") or ""),
        "plan_type": str(data.get("planType") or ""),
        "order_date": data.get("orderDate"),
        "expected_arrival_date": data.get("expectedArrivalDate"),
        "requires_inspection": bool(data.get("requiresInspection", False)),
        "current_stock_quantity": int(data.get("currentStockQuantity") or 0),
        "sales_30d_quantity": int(data["sales30dQuantity"]) if data.get("sales30dQuantity") is not None else None,
        "suggested_quantity": int(data["suggestedQuantity"]),
        "planned_quantity": int(data["plannedQuantity"]),
        "coverage_days_tenths": (
            round(float(data["coverageDays"]) * 10)
            if data.get("coverageDays") is not None
            else None
        ),
        "reason": str(data["reason"]),
        "notes": str(data.get("notes") or ""),
        "created_by": actor_email[:320],
    }
    try:
        plan = (
            ReplenishmentPlanItem.objects.select_for_update().filter(**lookup).first()
            if requested_status == "draft"
            else None
        )
        if plan is None:
            plan = ReplenishmentPlanItem.objects.create(
                id=str(uuid.uuid4()),
                **lookup,
                **defaults,
            )
            start_cycle(plan)
            plan.save(update_fields=["guangdong_health"])
        else:
            previous_quantity = plan.planned_quantity
            for field, value in defaults.items():
                setattr(plan, field, value)
            start_cycle(plan, previous_quantity)
            plan.save()
        return plan
    except IntegrityError as error:
        raise InventoryApiError("备货草稿已被其他请求更新", code="conflict", status=409) from error


def upsert_plan(data: dict[str, object], actor_email: str) -> ReplenishmentPlanItem:
    with transaction.atomic():
        lock_active_authority()
        if data.get("warehouse") == "广东仓":
            lock_stock_scope()
        plan = _upsert_plan_locked(data, actor_email)
        bump_revision({"kind": "replenishment_upsert", "planId": plan.id})
        return plan


def import_plans(
    rows: list[dict[str, object]],
    actor_email: str,
    file_sha256: str,
    source_batch_id: str,
) -> list[ReplenishmentPlanItem]:
    if not 1 <= len(rows) <= 200:
        raise InventoryApiError("单次必须导入1到200行备货计划")
    if not source_batch_id or any(str(row.get("sourceBatchId") or "") != source_batch_id for row in rows):
        raise InventoryApiError("备货计划导入来源批次不一致", code="version_conflict", status=409)
    with transaction.atomic():
        lock_active_authority()
        scope = InventoryImportScopeHead.objects.select_for_update().filter(dataset="stock").first()
        if scope is None or scope.status != "ready" or scope.current_batch_id != source_batch_id:
            raise InventoryApiError("库存快照在导入期间已变化，请重新下载或导入", code="version_conflict", status=409)
        plans = [_upsert_plan_locked(row, actor_email) for row in rows]
        bump_revision({
            "kind": "replenishment_import",
            "count": len(plans),
            "fileSha256": file_sha256,
            "planIds": [plan.id for plan in plans],
        })
        return plans


def update_plan(plan_id: str, status: str, planned_quantity: int | None) -> ReplenishmentPlanItem | None:
    if status not in STATUSES:
        raise InventoryApiError("备货计划状态无效")
    with transaction.atomic():
        lock_active_authority()
        if ReplenishmentPlanItem.objects.filter(id=plan_id, warehouse="广东仓").exists():
            lock_stock_scope()
        plan = ReplenishmentPlanItem.objects.select_for_update().filter(id=plan_id).first()
        if plan is None:
            return None
        if status not in TRANSITIONS.get(plan.status, set()):
            raise InventoryApiError(
                f"不能将{plan.status}状态的备货计划更新为{status}",
                code="conflict",
                status=409,
            )
        if planned_quantity is not None and plan.status != "draft":
            raise InventoryApiError("只有备货草稿可以调整计划数量", code="conflict", status=409)
        previous_quantity = plan.planned_quantity
        plan.status = status
        if planned_quantity is not None:
            plan.planned_quantity = planned_quantity
        start_cycle(plan, previous_quantity)
        plan.save()
        bump_revision({"kind": "replenishment_update", "planId": plan.id, "status": status})
        return plan
