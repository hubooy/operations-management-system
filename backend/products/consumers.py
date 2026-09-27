from __future__ import annotations

from django.db.models import Q

from sales.auth import Principal

from .errors import ProductsApiError
from .models import ProductShippingRateImportBatch
from .query import product_summary


OPERATIONS = frozenset({"product_performance", "import_batch_search"})


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> ProductsApiError:
    return ProductsApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 无效")
    normalized = value.strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise _error(f"{label} 无效")
    return normalized


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def validate_consumer_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("operation") not in OPERATIONS:
        raise _error("商品经营消费查询操作无效")
    operation = str(payload["operation"])
    if operation == "import_batch_search":
        if set(payload) != {"operation", "query", "offset", "limit"}:
            raise _error("商品导入批次消费查询字段集合无效")
        return {
            "operation": operation,
            "query": _text(payload["query"], "query", 120),
            "offset": _integer(payload["offset"], "offset", 0, 100_000),
            "limit": _integer(payload["limit"], "limit", 1, 100),
        }
    if set(payload) != {"operation", "days", "category", "query", "sortBy", "direction", "limit"}:
        raise _error("商品表现消费查询字段集合无效")
    category = payload["category"]
    query = payload["query"]
    if category is not None:
        category = _text(category, "category", 120, allow_empty=False)
    if query is not None:
        query = _text(query, "query", 100, allow_empty=False)
    sort_by = payload["sortBy"]
    if sort_by not in {
        "netSalesCents",
        "grossProfitCents",
        "grossMarginRate",
        "stockValueCents",
        "netQuantity",
    }:
        raise _error("sortBy 无效")
    if payload["direction"] not in {"asc", "desc"}:
        raise _error("direction 无效")
    return {
        "operation": operation,
        "days": _integer(payload["days"], "days", 7, 365),
        "category": category,
        "query": query,
        "sortBy": sort_by,
        "direction": payload["direction"],
        "limit": _integer(payload["limit"], "limit", 1, 100),
    }


def _import_batch_search(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    if principal.role not in {"operator", "admin"} or principal.scope is not None:
        raise _error("当前账号无权读取 SKU 快递费率导入批次", code="access_denied", status=403)
    query = str(request["query"])
    rows = ProductShippingRateImportBatch.objects.all()
    if query:
        rows = rows.filter(
            Q(id__icontains=query)
            | Q(file_name__icontains=query)
            | Q(source__icontains=query)
            | Q(status__icontains=query)
        )
    rows = rows.order_by("-created_at", "-id")
    total = rows.count()
    offset = int(request["offset"])
    limit = int(request["limit"])
    page = list(rows[offset : offset + limit])
    return {
        "items": [
            {
                "id": row.id,
                "source": "SKU 快递费率",
                "fileName": row.file_name,
                "status": row.status,
                "rowCount": int(row.row_count),
                "createdAt": row.created_at,
                "completedAt": row.completed_at,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def _product_performance(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    summary = product_summary(
        principal,
        {
            "days": request["days"],
            "page": 1,
            "pageSize": request["limit"],
            "query": request["query"] or "",
            "categories": [request["category"]] if request["category"] else [],
            "sortBy": request["sortBy"],
            "direction": request["direction"],
            "projection": "full",
        },
    )
    return {
        "sync": summary["sync"],
        "metrics": summary["metrics"],
        "days": request["days"],
        "filtersApplied": {
            "category": request["category"],
            "query": request["query"],
            "sortBy": request["sortBy"],
            "direction": request["direction"],
        },
        "totalMatched": summary["pagination"]["total"],
        "returned": summary["pagination"]["returned"],
        "truncated": summary["pagination"]["truncated"],
        "items": summary["items"],
        "currency": "CNY",
        "monetaryUnit": "cents",
    }


def execute_consumer_query(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    if request["operation"] == "import_batch_search":
        return _import_batch_search(principal, request)
    return _product_performance(principal, request)
