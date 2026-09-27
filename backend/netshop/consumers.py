"""Bounded operation-specific reads for non-netshop consumers."""

from __future__ import annotations

from django.db.models import Exists, Max, OuterRef, Q, Subquery, TextField, Value
from django.db.models.functions import Coalesce

from sales.auth import Principal

from .errors import NetshopApiError
from .models import NetshopImportBatch, NetshopRow


OPERATIONS = frozenset(
    {
        "row_search",
        "import_batch_search",
        "brand_options",
        "product_master_lookup",
        "market_projection_page",
    }
)


def _platform_scope(principal: Principal) -> list[str] | None:
    if principal.scope is None:
        return None
    platforms = sorted({item.strip() for item in principal.scope["platforms"] if item.strip()})
    return platforms


def _latest_rows(principal: Principal):
    scope_fields = ("source", "dataset", "platform", "shop_name")
    latest_batch_ids = (
        NetshopImportBatch.objects.filter(status="completed")
        .exclude(source__in=["jd_promotion", "tmall_promotion"])
        .annotate(head_date=Coalesce("snapshot_date", "date_max", Value("")))
        .order_by(*scope_fields, "-head_date", "-completed_at", "-created_at", "-id")
        .distinct(*scope_fields)
        .values("id")
    )
    # Select heads once, then let PostgreSQL semi-join rows to that small set.
    # A scalar ORDER BY/LIMIT correlated to every fact row repeats the batch
    # lookup during both the exact count and page query, exceeding search's
    # deadline on historical imports. Match all scope fields as well as the ID
    # so malformed cross-shop/source references cannot become visible.
    head_for_row = NetshopImportBatch.objects.filter(
        id__in=Subquery(latest_batch_ids),
        id=OuterRef("last_import_batch_id"),
        **{field: OuterRef(field) for field in scope_fields},
    )
    rows = (
        NetshopRow.objects.exclude(source__in=["jd_promotion", "tmall_promotion"])
        .filter(Exists(head_for_row))
    )
    platforms = _platform_scope(principal)
    return rows.filter(platform__in=platforms) if platforms is not None else rows


def _row_search(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = _latest_rows(principal).filter(
        Q(sku_id__icontains=query)
        | Q(spu_id__icontains=query)
        | Q(product_code__icontains=query)
        | Q(product_name__icontains=query)
        | Q(shop_name__icontains=query)
        | Q(platform__icontains=query)
        | Q(dataset__icontains=query)
    )
    grouped = (
        rows.values(
            "sku_id", "spu_id", "product_code", "product_name", "shop_name", "platform"
        )
        .annotate(
            dataset_value=Max("dataset"),
            latest_date=Max(
                Coalesce(
                    "business_date",
                    "snapshot_date",
                    "updated_at",
                    Value(""),
                    output_field=TextField(),
                )
            ),
        )
        .order_by("-latest_date", "sku_id", "spu_id", "product_code", "product_name")
    )
    total = grouped.count()
    offset = int(request["offset"])
    limit = int(request["limit"])
    page = list(grouped[offset : offset + limit])
    return {
        "items": [
            {
                "id": f"{row['sku_id'] or row['spu_id'] or row['product_code'] or row['product_name']}:{row['shop_name']}",
                "title": row["product_name"] or row["product_code"] or row["sku_id"] or row["spu_id"] or "未命名商品",
                "subtitle": " · ".join(
                    value
                    for value in (
                        f"SKU {row['sku_id']}" if row["sku_id"] else "",
                        f"SPU {row['spu_id']}" if row["spu_id"] else "",
                        str(row["product_code"] or ""),
                    )
                    if value
                ),
                "detail": " · ".join(
                    value
                    for value in (
                        str(row["platform"] or ""),
                        str(row["shop_name"] or ""),
                        str(row["dataset_value"] or ""),
                    )
                    if value
                ),
                "updatedAt": str(row["latest_date"] or "")[:48],
                "amountCents": None,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def _batch_search(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = NetshopImportBatch.objects.all()
    platforms = _platform_scope(principal)
    if platforms is not None:
        rows = rows.filter(platform__in=platforms)
    if query:
        rows = rows.filter(
            Q(id__icontains=query)
            | Q(file_name__icontains=query)
            | Q(source__icontains=query)
            | Q(dataset__icontains=query)
            | Q(platform__icontains=query)
            | Q(shop_name__icontains=query)
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
                "source": row.source,
                "dataset": row.dataset,
                "platform": row.platform,
                "shopName": row.shop_name,
                "fileName": row.file_name,
                "status": row.status,
                "rowCount": row.row_count,
                "createdAt": row.created_at,
                "completedAt": row.completed_at,
            }
            for row in page
        ],
        "total": total,
        "truncated": offset + len(page) < total,
    }


def _brand_options(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    rows = _latest_rows(principal).filter(dataset="product_master").exclude(brand="")
    if query:
        rows = rows.filter(brand__icontains=query)
    values = list(rows.order_by("brand").values_list("brand", flat=True).distinct()[: int(request["limit"]) + 1])
    limit = int(request["limit"])
    return {"items": values[:limit], "truncated": len(values) > limit}


def _product_master_lookup(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    lookup_codes = list(request["lookupCodes"])
    spu_ids = list(request["spuIds"])
    rows = _latest_rows(principal).filter(
        source="jd_product_master", dataset="product_master"
    )
    filters = Q()
    if lookup_codes:
        filters |= Q(sku_id__in=lookup_codes)
        filters |= Q(**{"raw_json__商家SKU__in": lookup_codes})
    if spu_ids:
        filters |= Q(spu_id__in=spu_ids) | Q(product_code__in=spu_ids)
    if lookup_codes or spu_ids:
        rows = rows.filter(filters)
    limit = int(request["limit"])
    page = list(
        rows.order_by(
            "sku_id", "spu_id", "product_code", "platform", "shop_name", "-snapshot_date", "-id"
        )[: limit + 1]
    )
    return {
        "rows": [
            {
                "skuId": row.sku_id,
                "spuId": row.spu_id,
                "productCode": row.product_code,
                "onlineSpecCode": str(row.raw_json.get("商家SKU") or "")[:200],
                "raw": row.raw_json,
            }
            for row in page[:limit]
        ],
        "truncated": len(page) > limit,
    }


def _projection_key(kind: str, *values: object) -> str:
    import hashlib

    payload = "\x1f".join([kind, *(str(value or "") for value in values)])
    return f"{kind}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _market_projection_page(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    expected_revision = request["expectedRevision"]
    from .query import revision_value

    current_revision = revision_value()
    if expected_revision is not None and expected_revision != current_revision:
        raise NetshopApiError(
            "网店市场投影版本已变化，请重新同步",
            status=409,
            code="version_conflict",
        )
    platforms = _platform_scope(principal)
    metric_rows = NetshopRow.objects.filter(
        source="jd_sku_daily", dataset__in=["sku_daily", "spu_daily"]
    )
    identity_rows = NetshopRow.objects.exclude(
        Q(sku_id="") & Q(spu_id="") & Q(product_code="")
    )
    brand_rows = _latest_rows(principal).filter(dataset="product_master").exclude(brand="")
    if platforms is not None:
        metric_rows = metric_rows.filter(platform__in=platforms)
        identity_rows = identity_rows.filter(platform__in=platforms)

    identity_values = identity_rows.values(
        "platform", "sku_id", "spu_id", "product_code"
    ).order_by().distinct()
    brand_values = brand_rows.values("brand").order_by().distinct()
    metric_count = metric_rows.count()
    identity_count = identity_values.count()
    brand_count = brand_values.count()
    total = metric_count + identity_count + brand_count
    offset = int(request["offset"])
    limit = int(request["limit"])
    remaining = limit
    rows: list[dict[str, object]] = []

    if offset < metric_count and remaining:
        start = offset
        page = metric_rows.order_by("source_row_key")[start : start + remaining]
        for row in page:
            rows.append(
                {
                    "projectionKey": f"metric:{row.source_row_key}",
                    "kind": "metric",
                    "source": row.source,
                    "dataset": row.dataset,
                    "platform": row.platform,
                    "shopName": row.shop_name,
                    "businessDate": row.business_date or "",
                    "skuId": row.sku_id,
                    "spuId": row.spu_id,
                    "productCode": row.product_code,
                    "transactionAmountCents": row.transaction_amount_cents,
                    "brand": "",
                }
            )
        remaining -= len(rows)
    identity_offset = max(0, offset - metric_count)
    if identity_offset < identity_count and remaining:
        page = list(
            identity_values.order_by("platform", "sku_id", "spu_id", "product_code")[
                identity_offset : identity_offset + remaining
            ]
        )
        for row in page:
            rows.append(
                {
                    "projectionKey": _projection_key(
                        "identity",
                        row["platform"],
                        row["sku_id"],
                        row["spu_id"],
                        row["product_code"],
                    ),
                    "kind": "identity",
                    "source": "",
                    "dataset": "",
                    "platform": row["platform"],
                    "shopName": "",
                    "businessDate": "",
                    "skuId": row["sku_id"],
                    "spuId": row["spu_id"],
                    "productCode": row["product_code"],
                    "transactionAmountCents": 0,
                    "brand": "",
                }
            )
        remaining -= len(page)
    brand_offset = max(0, offset - metric_count - identity_count)
    if brand_offset < brand_count and remaining:
        page = list(brand_values.order_by("brand")[brand_offset : brand_offset + remaining])
        for row in page:
            rows.append(
                {
                    "projectionKey": _projection_key("brand", row["brand"]),
                    "kind": "brand",
                    "source": "",
                    "dataset": "product_master",
                    "platform": "",
                    "shopName": "",
                    "businessDate": "",
                    "skuId": "",
                    "spuId": "",
                    "productCode": "",
                    "transactionAmountCents": 0,
                    "brand": row["brand"],
                }
            )
    return {
        "rows": rows,
        "total": total,
        "truncated": offset + len(rows) < total,
    }


def execute_consumer_query(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    operation = str(request["operation"])
    if operation == "row_search":
        return _row_search(principal, request)
    if operation == "import_batch_search":
        if principal.role not in {"operator", "admin"}:
            raise NetshopApiError("当前角色无权读取网店导入批次", status=403, code="access_denied")
        return _batch_search(principal, request)
    if operation == "product_master_lookup":
        return _product_master_lookup(principal, request)
    if operation == "market_projection_page":
        return _market_projection_page(principal, request)
    return _brand_options(principal, request)


def validate_consumer_request(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("operation") not in OPERATIONS:
        raise NetshopApiError("网店消费查询操作无效")
    operation = str(payload["operation"])
    if operation == "market_projection_page":
        expected = {"operation", "offset", "limit", "expectedRevision"}
    elif operation == "product_master_lookup":
        expected = {"operation", "lookupCodes", "spuIds", "limit"}
    elif operation == "brand_options":
        expected = {"operation", "query", "limit"}
    else:
        expected = {"operation", "query", "offset", "limit"}
    if set(payload) != expected:
        raise NetshopApiError("网店消费查询字段与契约不一致")
    if operation == "market_projection_page":
        expected_revision = payload.get("expectedRevision")
        if expected_revision is not None and (
            not isinstance(expected_revision, str)
            or len(expected_revision) > 96
            or not expected_revision
        ):
            raise NetshopApiError("网店市场投影 expectedRevision 无效")
        offset = payload.get("offset")
        limit = payload.get("limit")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
            raise NetshopApiError("网店市场投影 offset 无效")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise NetshopApiError("网店市场投影 limit 无效")
        return {
            "operation": operation,
            "offset": offset,
            "limit": limit,
            "expectedRevision": expected_revision,
        }
    if operation == "product_master_lookup":
        result: dict[str, object] = {"operation": operation}
        for field in ("lookupCodes", "spuIds"):
            values = payload.get(field)
            if (
                not isinstance(values, list)
                or len(values) > 5_000
                or any(
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value.strip()) > 200
                    for value in values
                )
            ):
                raise NetshopApiError(f"网店消费查询 {field} 无效")
            result[field] = list(dict.fromkeys(value.strip() for value in values))
        limit = payload.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise NetshopApiError("网店消费查询 limit 无效")
        result["limit"] = limit
        return result
    query = payload.get("query")
    if not isinstance(query, str) or len(query.strip()) > 80:
        raise NetshopApiError("网店消费查询词无效")
    query = query.strip()
    if operation == "row_search" and len(query) < 2:
        raise NetshopApiError("网店商品搜索至少需要 2 个字符")
    limit = payload.get("limit")
    maximum = 500 if operation == "brand_options" else 100
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise NetshopApiError("网店消费查询 limit 无效")
    result: dict[str, object] = {"operation": operation, "query": query, "limit": limit}
    if operation != "brand_options":
        offset = payload.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 80_000:
            raise NetshopApiError("网店消费查询 offset 无效")
        result["offset"] = offset
    return result
