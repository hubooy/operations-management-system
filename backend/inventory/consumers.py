from __future__ import annotations

from django.db.models import Q

from sales.auth import Principal
from sales.models import ErpProductMaster

from .errors import InventoryApiError
from .models import InventoryAgeLine, InventoryImportBatch, InventoryStockLine, ReplenishmentPlanItem
from .plans import get_plan, plan_payload
from .query import inventory_age_analysis, inventory_overview, _latest_batch


OPERATIONS = frozenset(
    {
        "freshness",
        "inventory_health",
        "import_batch_search",
        "stock_projection",
        "system_cost_snapshot",
        "inventory_search",
        "age_search",
        "replenishment_search",
        "work_item_reference",
    }
)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> InventoryApiError:
    return InventoryApiError(message, code=code, status=status)


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 无效")
    normalized = value.strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise _error(f"{label} 无效")
    return normalized


def _strings(value: object, label: str, maximum_items: int) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise _error(f"{label} 无效")
    result: list[str] = []
    for item in value:
        normalized = _text(item, label, 120, allow_empty=False)
        if normalized not in result:
            result.append(normalized)
    return result


def validate_consumer_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("operation") not in OPERATIONS:
        raise _error("库存消费查询操作无效")
    operation = str(payload["operation"])
    if operation == "freshness":
        if set(payload) != {"operation"}:
            raise _error("库存新鲜度查询字段集合无效")
        return {"operation": operation}
    if operation == "inventory_health":
        allowed = {"operation", "warehouse", "category", "status", "query", "limit"}
        if set(payload) != allowed:
            raise _error("库存健康消费查询字段集合无效")
        status = payload["status"]
        if status is not None and status not in {"no_stock", "urgent", "warning", "stale", "slow", "healthy"}:
            raise _error("status 无效")
        return {
            "operation": operation,
            "warehouse": None if payload["warehouse"] is None else _text(payload["warehouse"], "warehouse", 120, allow_empty=False),
            "category": None if payload["category"] is None else _text(payload["category"], "category", 120, allow_empty=False),
            "status": status,
            "query": None if payload["query"] is None else _text(payload["query"], "query", 100, allow_empty=False),
            "limit": _integer(payload["limit"], "limit", 1, 100),
        }
    if operation == "import_batch_search":
        if set(payload) != {"operation", "dataset", "query", "offset", "limit"}:
            raise _error("库存导入批次消费查询字段集合无效")
        dataset = payload["dataset"]
        if dataset not in {None, "stock", "age"}:
            raise _error("dataset 无效")
        return {"operation": operation, "dataset": dataset, "query": _text(payload["query"], "query", 120), "offset": _integer(payload["offset"], "offset", 0, 100_000), "limit": _integer(payload["limit"], "limit", 1, 100)}
    if operation in {"stock_projection", "system_cost_snapshot"}:
        expected = {"operation", "offset", "limit"} if operation == "stock_projection" else {"operation"}
        if set(payload) != expected:
            raise _error("库存快照消费查询字段集合无效")
        if operation == "stock_projection":
            return {"operation": operation, "offset": _integer(payload["offset"], "offset", 0, 100_000), "limit": _integer(payload["limit"], "limit", 1, 2_000)}
        return {"operation": operation}
    if operation in {"inventory_search", "age_search"}:
        if set(payload) != {"operation", "query", "offset", "limit"}:
            raise _error("库存搜索消费查询字段集合无效")
        return {"operation": operation, "query": _text(payload["query"], "query", 100), "offset": _integer(payload["offset"], "offset", 0, 100_000), "limit": _integer(payload["limit"], "limit", 1, 100)}
    if operation == "replenishment_search":
        if set(payload) != {"operation", "query", "status", "warehouse", "offset", "limit"}:
            raise _error("备货计划搜索消费查询字段集合无效")
        status = payload["status"]
        if status not in {None, "draft", "confirmed", "completed", "cancelled"}:
            raise _error("status 无效")
        warehouse = payload["warehouse"]
        return {
            "operation": operation,
            "query": _text(payload["query"], "query", 100),
            "status": status,
            "warehouse": None if warehouse is None else _text(warehouse, "warehouse", 120, allow_empty=False),
            "offset": _integer(payload["offset"], "offset", 0, 100_000),
            "limit": _integer(payload["limit"], "limit", 1, 100),
        }
    if set(payload) != {"operation", "kind", "referenceId"}:
        raise _error("库存事项参照查询字段集合无效")
    if payload["kind"] not in {"procurement", "stale_cleanup"}:
        raise _error("kind 无效")
    return {"operation": operation, "kind": payload["kind"], "referenceId": _text(payload["referenceId"], "referenceId", 240, allow_empty=False)}


def _stock_projection(request: dict[str, object]) -> dict[str, object]:
    batch = _latest_batch("stock")
    if batch is None:
        return {"batchId": None, "snapshotDate": None, "total": 0, "offset": int(request["offset"]), "rows": [], "truncated": False}
    grouped: dict[str, dict[str, object]] = {}
    for row in InventoryStockLine.objects.filter(batch_id=batch.id).exclude(warehouse="刷刷仓").order_by("product_code", "id"):
        if not row.product_code.strip():
            continue
        item = grouped.setdefault(row.product_code.strip(), {"productCode": row.product_code.strip(), "brand": row.brand.strip(), "availableQuantity": 0, "knownStockValueCents": 0, "pricedAvailableQuantity": 0})
        if not item["brand"] and row.brand.strip():
            item["brand"] = row.brand.strip()
        available = max(0, int(row.available_quantity))
        item["availableQuantity"] = int(item["availableQuantity"]) + available
        item["knownStockValueCents"] = int(item["knownStockValueCents"]) + available * int(row.unit_cost_cents)
        item["pricedAvailableQuantity"] = int(item["pricedAvailableQuantity"]) + available
    rows = [grouped[key] for key in sorted(grouped)]
    offset = int(request["offset"]); limit = int(request["limit"])
    return {"batchId": batch.id, "snapshotDate": batch.snapshot_date.isoformat(), "total": len(rows), "offset": offset, "rows": rows[offset : offset + limit], "truncated": offset + len(rows[offset : offset + limit]) < len(rows)}


def _system_cost_snapshot() -> dict[str, object]:
    batch = _latest_batch("stock")
    if batch is None:
        return {"snapshot": None}
    costs = [
        {"productCode": row.product_code.strip(), "warehouse": row.warehouse.strip(), "unitCostCents": int(row.unit_cost_cents)}
        for row in InventoryStockLine.objects.filter(batch_id=batch.id, unit_cost_cents__gt=0).exclude(warehouse="刷刷仓").order_by("product_code", "warehouse", "id")
        if row.product_code.strip()
    ]
    return {"snapshot": {"batchId": batch.id, "snapshotDate": batch.snapshot_date.isoformat(), "costs": costs}}


def _search(
    operation: str,
    request: dict[str, object],
    principal: Principal,
) -> dict[str, object]:
    query = str(request["query"]).strip()
    offset = int(request["offset"])
    limit = int(request["limit"])
    if operation == "inventory_search":
        batch = _latest_batch("stock")
        if batch is None:
            return {"items": [], "total": 0, "truncated": False}
        rows = InventoryStockLine.objects.filter(batch_id=batch.id).exclude(warehouse="刷刷仓")
        if principal.scope is not None:
            rows = rows.filter(warehouse__in=principal.scope["warehouses"])
        if query:
            rows = rows.filter(
                Q(product_code__icontains=query)
                | Q(product_name__icontains=query)
                | Q(warehouse__icontains=query)
                | Q(warehouse_type__icontains=query)
                | Q(specification__icontains=query)
                | Q(brand__icontains=query)
                | Q(category__icontains=query)
            )
        rows = rows.order_by("product_code", "warehouse", "id")
        total = rows.count()
        items = [
            {"id": f"{row.product_code}:{row.warehouse}", "title": row.product_name or row.product_code, "subtitle": row.product_code + (f" · {row.specification}" if row.specification else ""), "detail": f"{row.warehouse} · {row.warehouse_type} · 可用 {row.available_quantity}", "updatedAt": row.snapshot_date.isoformat(), "amountCents": int(row.unit_cost_cents)}
            for row in rows[offset : offset + limit]
        ]
    elif operation == "age_search":
        batch = _latest_batch("age")
        if batch is None:
            return {"items": [], "total": 0, "truncated": False}
        rows = InventoryAgeLine.objects.filter(batch_id=batch.id).exclude(warehouse="刷刷仓")
        if principal.scope is not None:
            rows = rows.filter(warehouse__in=principal.scope["warehouses"])
        if query:
            rows = rows.filter(
                Q(product_code__icontains=query)
                | Q(product_name__icontains=query)
                | Q(warehouse__icontains=query)
                | Q(specification__icontains=query)
                | Q(category__icontains=query)
            )
        rows = rows.order_by("-inventory_age_days", "product_code", "warehouse", "id")
        total = rows.count()
        items = [
            {
                "id": f"{row.product_code}:{row.warehouse}",
                "title": row.product_name or row.product_code,
                "subtitle": f"{row.product_code} · {row.warehouse}",
                "detail": f"{row.category} · 库龄 {row.inventory_age_days if row.inventory_age_days is not None else '未知'} 天 · 可用 {row.available_quantity}",
                "updatedAt": row.snapshot_date.isoformat(),
                "amountCents": int(row.stock_value_cents or 0),
            }
            for row in rows[offset : offset + limit]
        ]
    else:
        rows = ReplenishmentPlanItem.objects.all()
        if principal.scope is not None:
            rows = rows.filter(warehouse__in=principal.scope["warehouses"])
        if request["status"]:
            rows = rows.filter(status=request["status"])
        if request["warehouse"]:
            rows = rows.filter(warehouse=request["warehouse"])
        if query:
            rows = rows.filter(
                Q(product_code__icontains=query)
                | Q(product_name__icontains=query)
                | Q(warehouse__icontains=query)
                | Q(status__icontains=query)
                | Q(reason__icontains=query)
            )
        rows = rows.order_by("-updated_at", "product_code", "warehouse", "id")
        total = rows.count()
        items = [
            {
                "id": row.id,
                "title": row.product_name or row.product_code,
                "subtitle": f"{row.product_code} · {row.warehouse}",
                "detail": f"{row.status} · 计划 {row.planned_quantity}" + (f" · {row.reason}" if row.reason else ""),
                "updatedAt": row.updated_at.isoformat(),
                "amountCents": None,
            }
            for row in rows[offset : offset + limit]
        ]
    return {"items": items, "total": total, "truncated": offset + len(items) < total}


def _import_batch_search(request: dict[str, object]) -> dict[str, object]:
    rows = InventoryImportBatch.objects.all()
    if request["dataset"]:
        rows = rows.filter(dataset=request["dataset"])
    query = str(request["query"]).strip()
    if query:
        rows = rows.filter(
            Q(id__icontains=query)
            | Q(file_name__icontains=query)
            | Q(source__icontains=query)
            | Q(status__icontains=query)
        )
    rows = rows.order_by("-snapshot_date", "-completed_at", "-id")
    total = rows.count()
    offset = int(request["offset"])
    limit = int(request["limit"])
    items = [
        {
            "id": row.id,
            "source": row.source,
            "dataset": row.dataset,
            "fileName": row.file_name,
            "status": row.status,
            "rowCount": int(row.row_count),
            "createdAt": row.created_at.isoformat(),
            "completedAt": row.completed_at.isoformat() if row.completed_at else None,
        }
        for row in rows[offset : offset + limit]
    ]
    return {"items": items, "total": total, "truncated": offset + len(items) < total}


def execute_consumer_query(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    operation = str(request["operation"])
    if operation == "freshness":
        stock = _latest_batch("stock"); age = _latest_batch("age")
        def batch(value):
            return None if value is None else {"id": value.id, "snapshotDate": value.snapshot_date.isoformat(), "fileName": value.file_name, "completedAt": value.completed_at.isoformat() if value.completed_at else None, "rowCount": int(value.row_count)}
        return {"stock": batch(stock), "age": batch(age)}
    if operation == "inventory_health":
        requested_warehouses = [request["warehouse"]] if request["warehouse"] else []
        if principal.scope is not None:
            allowed_warehouses = set(principal.scope["warehouses"])
            requested_warehouses = (
                [warehouse for warehouse in requested_warehouses if warehouse in allowed_warehouses]
                if requested_warehouses
                else sorted(allowed_warehouses)
            )
            if not requested_warehouses:
                requested_warehouses = ["__teruisi_no_allowed_inventory_warehouse__"]
        data = inventory_overview(principal, {"view": "overview", "page": 1, "pageSize": request["limit"], "query": request["query"], "warehouses": requested_warehouses, "categories": [request["category"]] if request["category"] else [], "statuses": [request["status"]] if request["status"] else []})
        return {"sync": data["sync"], "settings": data["settings"], "metrics": data["metrics"], "health": data["health"], "filtersApplied": {"status": request["status"], "warehouse": request["warehouse"], "category": request["category"], "query": request["query"]}, "totalMatched": data["pagination"]["total"], "returned": data["pagination"]["returned"], "truncated": data["pagination"]["truncated"], "items": data["items"], "currency": "CNY", "monetaryUnit": "cents"}
    if operation == "import_batch_search":
        if principal.scope is not None:
            raise _error("受限数据范围账号不能读取库存导入批次", code="access_denied", status=403)
        return _import_batch_search(request)
    if operation == "stock_projection":
        if principal.scope is not None:
            raise _error("受限数据范围账号不能读取库存投影", code="access_denied", status=403)
        return _stock_projection(request)
    if operation == "system_cost_snapshot":
        if principal.scope is not None:
            raise _error("受限数据范围账号不能读取系统成本快照", code="access_denied", status=403)
        return _system_cost_snapshot()
    if operation in {"inventory_search", "age_search", "replenishment_search"}:
        return _search(operation, request, principal)
    if principal.scope is not None:
        raise _error("受限数据范围账号不能创建库存事项", code="access_denied", status=403)
    if request["kind"] == "procurement":
        plan = get_plan(str(request["referenceId"]))
        supplier = ""
        if plan:
            supplier = (
                ErpProductMaster.objects.filter(product_code=plan.product_code)
                .values_list("supplier", flat=True)
                .first()
                or ""
            )
        return {"plan": plan_payload(plan) if plan else None, "supplier": str(supplier)}
    result = inventory_age_analysis({"exactKey": request["referenceId"], "page": 1, "pageSize": 1})
    return {"item": result["items"][0] if result["items"] else None, "sync": result["sync"]}
