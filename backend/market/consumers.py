from __future__ import annotations

from django.db.models import Q

from sales.auth import Principal

from .admin import settings_status, system_kpis
from .errors import MarketApiError
from .models import MarketImportBatch, MarketMasterIdentity, MarketRankingEntry, MarketSkuAnnotation
from .query import item_trend, overview


OPERATIONS = frozenset(
    {
        "analysis_records",
        "workspace_status",
        "sku_search",
        "annotation_search",
        "import_batch_search",
        "overview",
        "sku_trend",
        "pending_review_summary",
    }
)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> MarketApiError:
    return MarketApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise _error(f"{label} 参数无效")
    return value.strip()


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise _error(f"{label} 参数无效")
    return value


def validate_consumer_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("operation") not in OPERATIONS:
        raise _error("市场消费查询操作无效")
    operation = str(payload["operation"])
    if operation == "analysis_records":
        from .analysis import validate
        return validate(payload)
    if operation == "workspace_status":
        expected = {"operation"}
    elif operation in {"sku_search", "annotation_search", "import_batch_search"}:
        expected = {"operation", "query", "offset", "limit"}
    elif operation == "overview":
        expected = {"operation", "view", "page", "pageSize", "filters"}
    elif operation == "sku_trend":
        expected = {"operation", "skuCode", "category", "scope", "rankingDimension"}
    else:
        expected = {"operation", "category"}
    if set(payload) != expected:
        raise _error("市场消费查询字段集合无效")
    normalized = dict(payload)
    if "query" in expected:
        normalized["query"] = _text(payload["query"], "query", 120)
        normalized["offset"] = _integer(payload["offset"], "offset", 0, 100_000)
        normalized["limit"] = _integer(payload["limit"], "limit", 1, 100)
    if operation == "pending_review_summary":
        normalized["category"] = _text(payload["category"], "category", 200)
    return normalized


def _sku_search(request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = MarketRankingEntry.objects.filter(id__in=MarketMasterIdentity.objects.values("latest_entry_id"))
    if query:
        rows = rows.filter(
            Q(sku_code__icontains=query)
            | Q(product_name__icontains=query)
            | Q(brand__icontains=query)
            | Q(category__icontains=query)
            | Q(scope__icontains=query)
        )
    rows = rows.order_by("-period_end", "rank", "id")
    total = rows.count()
    offset = int(request["offset"])
    limit = int(request["limit"])
    page = list(rows[offset : offset + limit])
    return {
        "items": [
            {
                "id": f"{row.category}:{row.scope}:{row.ranking_dimension}:{row.sku_code}",
                "title": row.product_name or row.sku_code,
                "subtitle": " · ".join(value for value in (row.sku_code, row.brand) if value),
                "detail": " · ".join(value for value in (row.category, row.scope, row.ranking_dimension, f"第 {row.rank} 名" if row.rank else "") if value),
                "updatedAt": row.period_end,
                "amountCents": row.price_cents,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def _annotation_search(request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = MarketSkuAnnotation.objects.all()
    if query:
        rows = rows.filter(
            Q(sku_code__icontains=query)
            | Q(segment__icontains=query)
            | Q(category__icontains=query)
        )
    rows = rows.order_by("-updated_at", "id")
    total = rows.count()
    offset = int(request["offset"])
    limit = int(request["limit"])
    page = list(rows[offset : offset + limit])
    return {
        "items": [
            {
                "id": row.id,
                "title": f"{row.sku_code} · {row.segment}",
                "subtitle": row.category,
                "detail": "已人工入库的市场细分品类与定位价",
                "updatedAt": row.updated_at,
                "amountCents": row.image_price_cents,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def _batch_search(request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = MarketImportBatch.objects.all()
    if query:
        rows = rows.filter(
            Q(id__icontains=query)
            | Q(file_name__icontains=query)
            | Q(source_type__icontains=query)
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
                "sourceType": row.source_type,
                "fileName": row.file_name,
                "status": row.status,
                "rowCount": row.row_count,
                "periodStart": row.period_start,
                "periodEnd": row.period_end,
                "createdAt": row.created_at,
                "completedAt": row.completed_at,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def execute_consumer_query(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    operation = str(request["operation"])
    if operation == "analysis_records":
        from .analysis import read_page
        return read_page(principal, request)
    if operation == "workspace_status":
        return {"settings": settings_status(), "kpis": system_kpis()}
    if operation == "sku_search":
        return _sku_search(request)
    if operation == "annotation_search":
        return _annotation_search(request)
    if operation == "import_batch_search":
        if principal.role not in {"operator", "admin"}:
            raise _error("当前角色无权读取市场导入批次", code="access_denied", status=403)
        return _batch_search(request)
    if operation == "overview":
        return overview(principal, request)
    if operation == "sku_trend":
        return item_trend({**request, "operation": "trend"})
    category = str(request["category"])
    annotations = MarketSkuAnnotation.objects.filter(category=category) if category else MarketSkuAnnotation.objects.all()
    identities = MarketMasterIdentity.objects.filter(category=category) if category else MarketMasterIdentity.objects.all()
    return {
        "category": category,
        "marketIdentityTotal": identities.count(),
        "committedAnnotationCount": annotations.count(),
        "pendingAnnotationCount": max(0, identities.count() - annotations.count()),
    }
