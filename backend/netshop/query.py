from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from django.db.models import Count, Max, Min, Q, Sum

from sales.auth import Principal

from .errors import NetshopApiError
from .models import (
    NetshopDataRevision,
    NetshopImportBatch,
    NetshopProductDailyRevision,
    NetshopProductDailyScopeRevision,
    NetshopPromotionAggregateManifest,
    NetshopPromotionProductDaily,
    NetshopPromotionScopeRevision,
    NetshopPromotionShopDaily,
    NetshopRow,
)
from .sales_client import read_sales_consumer, sales_product_metrics
from .serialization import batch_payload


MAX_DAYS = 730
MAX_PAGE = 10_000
MAX_PAGE_SIZE = 100
MAX_PROMOTION_PAGE_SIZE = 500
MAX_OUTLETS = 50
OUTLET_SEPARATOR = "\x1f"
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")


def revision_value() -> str:
    row = NetshopDataRevision.objects.filter(domain="netshop").first()
    if row is None or row.revision < 0 or not HEX_64_RE.fullmatch(row.source_digest or ""):
        raise NetshopApiError(
            "网店数据版本不可用", code="service_unavailable", status=503
        )
    return f"{row.revision}:{row.source_digest[:12]}"


def positive(raw: str | None, fallback: int, label: str, maximum: int) -> int:
    if raw is None or raw == "":
        return fallback
    if not raw.isdigit() or raw.startswith("0"):
        raise NetshopApiError(f"{label}必须为十进制正整数")
    value = int(raw)
    if value < 1 or value > maximum:
        raise NetshopApiError(f"{label}超出允许范围")
    return value


def iso_date(value: str | None, label: str) -> str:
    candidate = (value or "").strip()
    try:
        normalized = date.fromisoformat(candidate).isoformat()
    except ValueError as error:
        raise NetshopApiError(f"{label}必须是有效的 YYYY-MM-DD 自然日") from error
    if normalized != candidate:
        raise NetshopApiError(f"{label}必须是有效的 YYYY-MM-DD 自然日")
    return normalized


def period(start: str | None, end: str | None, *, required: bool = False) -> dict[str, object] | None:
    raw_start = (start or "").strip()
    raw_end = (end or "").strip()
    if not raw_start and not raw_end:
        if required:
            raise NetshopApiError("必须同时提供 startDate 和 endDate")
        return None
    if not raw_start or not raw_end:
        raise NetshopApiError("startDate 和 endDate 必须同时提供")
    start_value = iso_date(raw_start, "startDate")
    end_value = iso_date(raw_end, "endDate")
    if start_value > end_value:
        raise NetshopApiError("startDate 不能晚于 endDate")
    days = (date.fromisoformat(end_value) - date.fromisoformat(start_value)).days + 1
    if days > MAX_DAYS:
        raise NetshopApiError(f"网店统计周期最多支持 {MAX_DAYS} 天")
    return {
        "startDate": start_value,
        "endDate": end_value,
        "endExclusive": (date.fromisoformat(end_value) + timedelta(days=1)).isoformat(),
        "days": days,
    }


def normalize_platforms(values: Sequence[str]) -> list[str]:
    result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if len(result) > 20 or any(len(item) > 100 for item in result):
        raise NetshopApiError("platform 筛选超出限制")
    if any(item not in {"京东", "天猫"} for item in result):
        raise NetshopApiError("platform 包含不支持的网店平台")
    return sorted(result)


def parse_outlets(values: Sequence[str], platforms: Sequence[str]) -> list[dict[str, str]]:
    if len(values) > MAX_OUTLETS:
        raise NetshopApiError(f"outlet 筛选最多 {MAX_OUTLETS} 项")
    result: dict[tuple[str, str], dict[str, str]] = {}
    selected_platforms = set(platforms)
    for value in values:
        if value.count(OUTLET_SEPARATOR) != 1:
            raise NetshopApiError("outlet 必须使用有效的平台与店铺复合键")
        platform, shop_name = (part.strip() for part in value.split(OUTLET_SEPARATOR, 1))
        if (
            not platform
            or not shop_name
            or len(platform) > 100
            or len(shop_name) > 100
            or platform not in {"京东", "天猫"}
            or any(ord(char) < 32 or ord(char) == 127 for char in platform + shop_name)
        ):
            raise NetshopApiError("outlet 必须使用有效的平台与店铺复合键")
        if selected_platforms and platform not in selected_platforms:
            raise NetshopApiError("outlet 平台必须属于当前 platform 筛选")
        result[(platform, shop_name)] = {"platform": platform, "shopName": shop_name}
    return [result[key] for key in sorted(result)]


def _apply_platform_outlets(queryset, platforms: Sequence[str], outlets: Sequence[Mapping[str, str]]):
    if platforms:
        queryset = queryset.filter(platform__in=platforms)
    if outlets:
        outlet_filter = Q(pk__in=[])
        for item in outlets:
            outlet_filter |= Q(platform=item["platform"], shop_name=item["shopName"])
        queryset = queryset.filter(outlet_filter)
    return queryset


def _canonical_token(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _date_sequence(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    values: list[str] = []
    while current <= final:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def overview(*, shop: str | None, platforms: Sequence[str]) -> dict[str, object]:
    rows = NetshopRow.objects.all()
    if shop:
        rows = rows.filter(Q(shop_name=shop) | Q(source="inv_selfop"))
    if platforms:
        rows = rows.filter(platform__in=platforms)
    grouped = rows.values("dataset", "source", "platform").annotate(
        date_min=Min("business_date"),
        date_max=Max("business_date"),
        snapshot_date=Max("snapshot_date"),
        row_count=Count("id"),
    ).order_by("dataset", "source", "platform")
    datasets: dict[str, object] = {}
    for row in grouped:
        batches = NetshopImportBatch.objects.filter(
            dataset=row["dataset"],
            source=row["source"],
            platform=row["platform"],
            status="completed",
        )
        if shop and row["source"] != "inv_selfop":
            batches = batches.filter(shop_name=shop)
        latest = batches.order_by("-completed_at", "-created_at", "-id").first()
        datasets[str(row["dataset"])] = {
            "source": row["source"],
            "dataset": row["dataset"],
            "dateMin": row["date_min"],
            "dateMax": row["date_max"],
            "snapshotDate": row["snapshot_date"],
            "rowCount": row["row_count"],
            "latestBatchId": latest.id if latest else None,
            "latestFileName": latest.file_name if latest else None,
            "completedAt": latest.completed_at if latest else None,
        }
    return {
        "shop": shop,
        "filters": {"shop": shop},
        "datasets": datasets,
        "date_max": {
            key: value["dateMax"] for key, value in datasets.items()  # type: ignore[index]
        },
    }


def _latest_batches(sources: Sequence[str]) -> list[NetshopImportBatch]:
    ordered = NetshopImportBatch.objects.filter(
        source__in=sources, status="completed"
    ).order_by("-completed_at", "-created_at", "-id")
    result: list[NetshopImportBatch] = []
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        key = (item.platform, item.shop_name)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _latest_asset_batches(scopes: Sequence[tuple[str, str]]) -> dict[tuple[str, str, str], NetshopImportBatch]:
    if not scopes:
        return {}
    requested = set(scopes)
    result: dict[tuple[str, str, str], NetshopImportBatch] = {}
    rows = NetshopImportBatch.objects.filter(
        source__in=["tmall_product_assets", "jd_yimei_sku"], status="completed"
    ).order_by("-snapshot_date", "-completed_at", "-created_at", "-id")
    for batch in rows:
        matched_scope = (batch.platform, batch.shop_name)
        if batch.source == "jd_yimei_sku" and batch.shop_name == "":
            jd_scopes = [scope for scope in requested if scope[0] == "京东"]
            for scope in jd_scopes:
                result.setdefault((batch.source, *scope), batch)
            continue
        if matched_scope in requested:
            result.setdefault((batch.source, *matched_scope), batch)
    return result


def _image_assets(
    product_rows: Sequence[NetshopRow],
    asset_batches: Mapping[tuple[str, str, str], NetshopImportBatch],
) -> dict[tuple[str, str, str], NetshopRow]:
    tmall_spus: dict[tuple[str, str], set[str]] = defaultdict(set)
    jd_ids: dict[tuple[str, str], tuple[set[str], set[str]]] = {}
    for row in product_rows:
        scope = (row.platform, row.shop_name)
        if row.platform == "天猫" and row.spu_id:
            tmall_spus[scope].add(row.spu_id)
        elif row.platform == "京东":
            sku_set, code_set = jd_ids.setdefault(scope, (set(), set()))
            if row.sku_id:
                sku_set.add(row.sku_id)
            if row.product_code:
                code_set.add(row.product_code)
    result: dict[tuple[str, str, str], NetshopRow] = {}
    for scope, spu_ids in tmall_spus.items():
        batch = asset_batches.get(("tmall_product_assets", *scope))
        if not batch:
            continue
        for asset in NetshopRow.objects.filter(last_import_batch_id=batch.id, spu_id__in=spu_ids).order_by("-id"):
            result.setdefault((scope[0], scope[1], asset.spu_id), asset)
    for scope, (sku_ids, codes) in jd_ids.items():
        batch = asset_batches.get(("jd_yimei_sku", *scope))
        if not batch:
            continue
        assets = NetshopRow.objects.filter(last_import_batch_id=batch.id).filter(
            Q(sku_id__in=sku_ids) | Q(product_code__in=codes)
        ).order_by("-snapshot_date", "-id")
        for asset in assets:
            if asset.sku_id:
                result.setdefault((scope[0], scope[1], f"sku:{asset.sku_id}"), asset)
            if asset.product_code:
                result.setdefault((scope[0], scope[1], f"code:{asset.product_code}"), asset)
    return result


def _empty_sales() -> dict[str, object]:
    return {
        "costPriceCents": None,
        "netSalesCents": None,
        "grossMarginRate": None,
        "refundRate": None,
        "salesMatched": False,
    }


def _catalog_item(row: NetshopRow, asset: NetshopRow | None) -> tuple[dict[str, object], str]:
    raw = row.raw_json
    asset_raw = asset.raw_json if asset else {}
    spu_id = row.spu_id or str(raw.get("商品ID") or raw.get("商品编码") or "").strip()
    sku_id = row.sku_id or str(raw.get("SKUID") or "").strip()
    product_code = str(raw.get("SKU商家编码") or raw.get("商品编码") or row.product_code).strip()
    sales_code = str(raw.get("商家SKU") or raw.get("SKU商家编码") or "").strip()
    image_url = ""
    if asset and asset.image_content_sha256:
        image_url = f"/api/netshop/product-images/{asset.image_content_sha256}"
    else:
        image_url = (asset.image_url if asset else "") or row.image_url
    product_url = (
        str(asset_raw.get("商品链接") or "").strip()
        or row.product_url
        or (f"https://detail.tmall.com/item.htm?id={spu_id}" if row.platform == "天猫" and spu_id else "")
    )
    item = {
        "platform": row.platform,
        "shopName": row.shop_name,
        "spuId": spu_id,
        "skuId": sku_id,
        "productCode": product_code,
        "productName": row.product_name or str(raw.get("商品名称") or "").strip(),
        "imageUrl": image_url,
        "saleAttribute": row.sale_attribute,
        "category": row.category,
        "brand": row.brand,
        "price": row.price_cents / 100 if row.price_cents is not None else None,
        "priceCents": row.price_cents,
        "totalInventory": row.total_inventory,
        "availableInventory": row.available_inventory,
        "status": row.product_status,
        "productUrl": product_url,
        "createdAt": row.source_created_at,
        "snapshotDate": row.snapshot_date,
        **_empty_sales(),
    }
    return item, sales_code


def product_catalog(
    principal: Principal,
    *,
    query: str,
    page: int,
    page_size: int,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    sales_period: Mapping[str, object] | None,
    view: str,
    expected_snapshot_token: str | None,
) -> dict[str, object]:
    if len(query) > 120:
        raise NetshopApiError("q 最多 120 个字符")
    if view not in {"full", "page"}:
        raise NetshopApiError("view 必须是 full 或 page")
    sales_freshness, opening_sales_revision = read_sales_consumer(
        principal, {"operation": "freshness"}
    )
    batches_all = _latest_batches(["jd_product_master", "tmall_product_master"])
    visible = [item for item in batches_all if not platforms or item.platform in platforms]
    outlet_keys = {(item["platform"], item["shopName"]) for item in outlets}
    selected = [
        item
        for item in visible
        if not outlet_keys or (item.platform, item.shop_name) in outlet_keys
    ]
    asset_batches = _latest_asset_batches([(item.platform, item.shop_name) for item in selected])
    snapshot = _canonical_token(
        {
            "version": 2,
            "kind": "catalog",
            "revision": revision_value(),
            "salesRevision": opening_sales_revision,
            "platforms": list(platforms),
            "outlets": list(outlets),
            "salesPeriod": sales_period,
            "query": query,
            "heads": [
                [item.id, item.source, item.platform, item.shop_name, item.snapshot_date]
                for item in selected
            ],
            "assetHeads": sorted(
                [key + (value.id, value.snapshot_date or "") for key, value in asset_batches.items()]
            ),
        }
    )
    if view == "page" and expected_snapshot_token != snapshot:
        raise NetshopApiError(
            "货品目录版本已变化，请重新加载",
            code="service_unavailable",
            status=503,
        )
    shops = sorted(
        [
            {
                "shopName": item.shop_name,
                "platform": item.platform,
                "snapshotDate": item.snapshot_date,
                "completedAt": item.completed_at,
            }
            for item in visible
        ],
        key=lambda item: (str(item["platform"]), str(item["shopName"])),
    )
    batch_ids = [item.id for item in selected]
    rows = NetshopRow.objects.filter(
        source__in=["jd_product_master", "tmall_product_master"],
        dataset="product_master",
        last_import_batch_id__in=batch_ids,
    )
    authoritative_total = sum(item.row_count for item in selected)
    if rows.count() != authoritative_total:
        raise NetshopApiError(
            "货品目录批次元数据与已发布事实不一致",
            code="service_unavailable",
            status=503,
        )
    summary = rows.aggregate(
        totalSkus=Count("id"),
        totalInventory=Sum("inventory_quantity"),
        availableInventory=Sum("inventory_quantity"),
    )
    on_sale = rows.filter(product_status="上架").count()
    if query:
        rows = rows.filter(
            Q(shop_name__icontains=query)
            | Q(spu_id__icontains=query)
            | Q(sku_id__icontains=query)
            | Q(product_code__icontains=query)
            | Q(product_name__icontains=query)
        )
    total = rows.count()
    offset = (page - 1) * page_size
    product_rows = list(
        rows.order_by("shop_name", "product_name", "sku_id", "platform", "id")[
            offset : offset + page_size
        ]
    )
    assets = _image_assets(product_rows, asset_batches)
    internal_items: list[tuple[dict[str, object], str]] = []
    for row in product_rows:
        asset = None
        if row.platform == "天猫":
            asset = assets.get((row.platform, row.shop_name, row.spu_id))
        else:
            asset = assets.get((row.platform, row.shop_name, f"sku:{row.sku_id}")) or assets.get(
                (row.platform, row.shop_name, f"code:{row.product_code}")
            )
        internal_items.append(_catalog_item(row, asset))
    outlet_scopes = [
        {"platform": item.platform, "shopName": item.shop_name}
        for item in selected
        if item.platform == "京东"
    ]
    allowed_channels = principal.scope["channels"] if principal.scope is not None else None
    metrics, sales_data, metrics_revision = sales_product_metrics(
        principal,
        identities=[
            {
                "platform": str(item["platform"]),
                "shopName": str(item["shopName"]),
                "salesProductCode": sales_code,
            }
            for item, sales_code in internal_items
            if item["platform"] == "京东"
        ],
        outlets=outlet_scopes,
        start_date=str(sales_period["startDate"]) if sales_period else None,
        end_exclusive=str(sales_period["endExclusive"]) if sales_period else None,
        allowed_channels=allowed_channels,
    )
    if metrics_revision != opening_sales_revision:
        raise NetshopApiError(
            "货品目录读取期间销售数据已更新",
            code="service_unavailable",
            status=503,
        )
    items: list[dict[str, object]] = []
    for item, sales_code in internal_items:
        if item["platform"] == "京东":
            item.update(metrics.get((str(item["platform"]), str(item["shopName"]), sales_code), _empty_sales()))
        items.append(item)
    closing_freshness, closing_sales_revision = read_sales_consumer(
        principal, {"operation": "freshness"}
    )
    if closing_sales_revision != opening_sales_revision:
        raise NetshopApiError(
            "货品目录读取期间销售数据已更新",
            code="service_unavailable",
            status=503,
        )
    page_payload: dict[str, object] = {
        "snapshotToken": snapshot,
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "truncated": offset + len(items) < total,
        },
    }
    if view == "page":
        return page_payload
    return {
        **page_payload,
        "batch": batch_payload(selected[0]) if selected else None,
        "summary": {
            "totalSkus": summary["totalSkus"] or 0,
            "onSaleSkus": on_sale,
            "totalInventory": summary["totalInventory"] or 0,
            "availableInventory": summary["availableInventory"] or 0,
        },
        "shops": shops,
        "sales": {
            "periodStart": sales_period["startDate"] if sales_period else None,
            "periodEnd": sales_period["endDate"] if sales_period else None,
            "dataCutoffDate": sales_data.get("dataCutoffDate"),
            "platform": sales_data.get("platform", "京东"),
        },
    }


def product_image_metadata(content_hash: str, platforms: Sequence[str]) -> dict[str, object] | None:
    normalized = content_hash.strip().lower()
    if not HEX_64_RE.fullmatch(normalized) or not platforms:
        return None
    row = (
        NetshopRow.objects.filter(
            source="tmall_product_assets",
            dataset="spu_assets",
            platform__in=platforms,
            image_content_sha256=normalized,
            last_import_batch_id__in=NetshopImportBatch.objects.filter(
                source="tmall_product_assets", dataset="spu_assets", status="completed"
            ).values("id"),
        )
        .order_by("-snapshot_date", "-id")
        .first()
    )
    if not row:
        return None
    return {
        "contentHash": row.image_content_sha256,
        "objectKey": row.image_object_key,
        "mimeType": row.image_mime_type,
        "sizeBytes": row.image_size_bytes,
    }


PERFORMANCE_SUM_FIELDS = (
    "page_views",
    "visitors",
    "search_impressions",
    "search_clicks",
    "add_cart_customers",
    "add_cart_quantity",
    "order_customers",
    "order_quantity",
    "order_amount_cents",
    "transaction_orders",
    "transaction_amount_cents",
    "transaction_quantity",
    "transaction_customers",
    "favorites",
    "refund_amount_cents",
    "search_visitors",
    "search_transaction_customers",
)


def _sum_annotations(fields: Sequence[str] = PERFORMANCE_SUM_FIELDS) -> dict[str, object]:
    return {field: Sum(field) for field in fields}


def _zero(value: object) -> int:
    return int(value or 0)


def _performance_summary_payload(
    aggregate: Mapping[str, object],
    *,
    snapshot: str,
    dimension: str,
    requested_period: Mapping[str, object] | None,
) -> dict[str, object]:
    visitors = _zero(aggregate.get("visitors"))
    transaction_customers = _zero(aggregate.get("transaction_customers"))
    search_impressions = _zero(aggregate.get("search_impressions"))
    search_clicks = _zero(aggregate.get("search_clicks"))
    transaction_amount_cents = _zero(aggregate.get("transaction_amount_cents"))
    order_amount_cents = _zero(aggregate.get("order_amount_cents"))
    return {
        "snapshotToken": snapshot,
        "dimension": dimension,
        "dataset": "sku_daily" if dimension == "sku" else "spu_daily",
        "requestedPeriod": {
            "startDate": requested_period["startDate"] if requested_period else None,
            "endDate": requested_period["endDate"] if requested_period else None,
        },
        "dateMin": aggregate.get("date_min"),
        "dataCutoffDate": aggregate.get("date_max"),
        "monetaryUnit": "cents",
        "visitorAggregation": "product_day_sum",
        "summary": {
            "productCount": _zero(aggregate.get("product_count")),
            "pageViews": _zero(aggregate.get("page_views")),
            "visitors": visitors,
            "searchImpressions": search_impressions,
            "searchClicks": search_clicks,
            "searchClickRate": search_clicks / search_impressions if search_impressions > 0 else None,
            "addCartCustomers": _zero(aggregate.get("add_cart_customers")),
            "addCartQuantity": _zero(aggregate.get("add_cart_quantity")),
            "orderCustomers": _zero(aggregate.get("order_customers")),
            "orderQuantity": _zero(aggregate.get("order_quantity")),
            "orderAmount": order_amount_cents / 100,
            "orderAmountCents": order_amount_cents,
            "transactionOrders": _zero(aggregate.get("transaction_orders")),
            "transactionAmount": transaction_amount_cents / 100,
            "transactionAmountCents": transaction_amount_cents,
            "transactionQuantity": _zero(aggregate.get("transaction_quantity")),
            "transactionCustomers": transaction_customers,
            "favorites": _zero(aggregate.get("favorites")),
            "refundAmountCents": _zero(aggregate.get("refund_amount_cents")),
            "searchVisitors": _zero(aggregate.get("search_visitors")),
            "searchTransactionCustomers": _zero(aggregate.get("search_transaction_customers")),
            "uvValue": transaction_amount_cents / 100 / visitors if visitors > 0 else None,
            "conversionRate": transaction_customers / visitors if visitors > 0 else None,
        },
    }


def _performance_assets(
    grouped: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str, str], NetshopRow]:
    scopes = {(str(item["platform"]), str(item["shop_name"])) for item in grouped}
    heads = _latest_asset_batches(sorted(scopes))
    result: dict[tuple[str, str, str], NetshopRow] = {}
    for scope in scopes:
        tmall_head = heads.get(("tmall_product_assets", *scope))
        if tmall_head:
            spus = {str(item.get("spu_id") or "") for item in grouped if (item["platform"], item["shop_name"]) == scope}
            for row in NetshopRow.objects.filter(last_import_batch_id=tmall_head.id, spu_id__in=spus).order_by("-id"):
                result.setdefault((scope[0], scope[1], f"spu:{row.spu_id}"), row)
        jd_head = heads.get(("jd_yimei_sku", *scope))
        if jd_head:
            sku_ids = {str(item.get("sku_id") or "") for item in grouped if (item["platform"], item["shop_name"]) == scope}
            product_codes = {str(item.get("product_code") or "") for item in grouped if (item["platform"], item["shop_name"]) == scope}
            for row in NetshopRow.objects.filter(last_import_batch_id=jd_head.id).filter(
                Q(sku_id__in=sku_ids) | Q(product_code__in=product_codes)
            ).order_by("-snapshot_date", "-id"):
                if row.sku_id:
                    result.setdefault((scope[0], scope[1], f"sku:{row.sku_id}"), row)
                if row.product_code:
                    result.setdefault((scope[0], scope[1], f"code:{row.product_code}"), row)
    return result


def product_performance(
    *,
    dimension: str,
    query: str,
    page: int,
    page_size: int,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object] | None,
    view: str,
    expected_snapshot_token: str | None,
) -> dict[str, object]:
    if dimension not in {"sku", "spu"}:
        raise NetshopApiError("dimension 必须且只能是 sku 或 spu")
    if view not in {"summary", "full", "page"}:
        raise NetshopApiError("view 必须是 summary、full 或 page")
    if len(query) > 120:
        raise NetshopApiError("q 最多 120 个字符")
    dataset = "sku_daily" if dimension == "sku" else "spu_daily"
    identity_field = "sku_id" if dimension == "sku" else "spu_id"
    source_filter = Q(source="jd_sku_daily") if dimension == "sku" else Q(
        source__in=["jd_sku_daily", "tmall_product_daily"]
    )
    unrestricted = NetshopRow.objects.filter(source_filter, dataset=dataset).exclude(
        **{identity_field: ""}
    )
    unrestricted = _apply_platform_outlets(unrestricted, platforms, outlets)
    available = unrestricted.aggregate(date_min=Min("business_date"), date_max=Max("business_date"))
    rows = unrestricted
    if requested_period:
        rows = rows.filter(
            business_date__gte=requested_period["startDate"],
            business_date__lt=requested_period["endExclusive"],
        )
    if query:
        query_filter = (
            Q(sku_id__icontains=query)
            | Q(spu_id__icontains=query)
            | Q(product_code__icontains=query)
            | Q(product_name__icontains=query)
        )
        query_filter |= Q(**{f"{identity_field}__icontains": query})
        rows = rows.filter(query_filter)
    revision = revision_value()
    scope_revisions = list(
        NetshopProductDailyScopeRevision.objects.filter(
            platform__in=platforms or ["京东", "天猫"]
        ).values_list("platform", "shop_name", "data_version")
    )
    snapshot = _canonical_token(
        {
            "version": 2,
            "kind": "product-performance",
            "revision": revision,
            "dimension": dimension,
            "query": query,
            "period": requested_period,
            "platforms": list(platforms),
            "outlets": list(outlets),
            "scopeRevisions": sorted(scope_revisions),
        }
    )
    if view == "page" and expected_snapshot_token != snapshot:
        raise NetshopApiError(
            "商品日数据版本已变化，请重新加载",
            code="service_unavailable",
            status=503,
        )
    identity_values = ("platform", "shop_name", identity_field)
    product_count = rows.values(*identity_values).distinct().count()
    aggregate = rows.aggregate(
        date_min=Min("business_date"),
        date_max=Max("business_date"),
        date_count=Count("business_date", distinct=True),
        **_sum_annotations(),
    )
    aggregate["product_count"] = product_count
    summary_payload = _performance_summary_payload(
        aggregate,
        snapshot=snapshot,
        dimension=dimension,
        requested_period=requested_period,
    )
    if view == "summary":
        return summary_payload
    grouped = rows.values(*identity_values).annotate(
        sku_id_value=Max("sku_id"),
        spu_id_value=Max("spu_id"),
        product_code_value=Max("product_code"),
        product_name_value=Max("product_name"),
        category_value=Max("category"),
        date_min=Min("business_date"),
        date_max=Max("business_date"),
        data_days=Count("business_date", distinct=True),
        **_sum_annotations(),
    ).order_by("-transaction_amount_cents", "-visitors", identity_field)
    offset = (page - 1) * page_size
    grouped_page = list(grouped[offset : offset + page_size])
    assets = _performance_assets(grouped_page)
    items: list[dict[str, object]] = []
    for row in grouped_page:
        platform = str(row["platform"])
        shop_name = str(row["shop_name"])
        sku_id = str(row["sku_id_value"] or "")
        spu_id = str(row["spu_id_value"] or "")
        product_code = str(row["product_code_value"] or "")
        asset = assets.get((platform, shop_name, f"spu:{spu_id}")) if platform == "天猫" else (
            assets.get((platform, shop_name, f"sku:{sku_id}"))
            or assets.get((platform, shop_name, f"code:{product_code}"))
        )
        image_url = ""
        product_url = ""
        if asset:
            image_url = (
                f"/api/netshop/product-images/{asset.image_content_sha256}"
                if asset.image_content_sha256
                else asset.image_url
            )
            product_url = asset.product_url
        if not product_url and platform == "天猫" and spu_id:
            product_url = f"https://detail.tmall.com/item.htm?id={spu_id}"
        visitors = _zero(row["visitors"])
        transaction_customers = _zero(row["transaction_customers"])
        search_impressions = _zero(row["search_impressions"])
        search_clicks = _zero(row["search_clicks"])
        transaction_amount = _zero(row["transaction_amount_cents"])
        order_amount = _zero(row["order_amount_cents"])
        items.append(
            {
                "id": str(row[identity_field]),
                "platform": platform,
                "skuId": sku_id,
                "spuId": spu_id,
                "productCode": product_code,
                "productName": str(row["product_name_value"] or ""),
                "imageUrl": image_url,
                "productUrl": product_url,
                "category": str(row["category_value"] or ""),
                "shopNames": [shop_name],
                "dateMin": row["date_min"],
                "dateMax": row["date_max"],
                "dataDays": _zero(row["data_days"]),
                "pageViews": _zero(row["page_views"]),
                "visitors": visitors,
                "searchImpressions": search_impressions,
                "searchClicks": search_clicks,
                "searchClickRate": search_clicks / search_impressions if search_impressions > 0 else None,
                "addCartCustomers": _zero(row["add_cart_customers"]),
                "addCartQuantity": _zero(row["add_cart_quantity"]),
                "orderCustomers": _zero(row["order_customers"]),
                "orderQuantity": _zero(row["order_quantity"]),
                "orderAmount": order_amount / 100,
                "orderAmountCents": order_amount,
                "transactionOrders": _zero(row["transaction_orders"]),
                "transactionAmount": transaction_amount / 100,
                "transactionAmountCents": transaction_amount,
                "transactionQuantity": _zero(row["transaction_quantity"]),
                "transactionCustomers": transaction_customers,
                "favorites": _zero(row["favorites"]),
                "refundAmountCents": _zero(row["refund_amount_cents"]),
                "searchVisitors": _zero(row["search_visitors"]),
                "searchTransactionCustomers": _zero(row["search_transaction_customers"]),
                "uvValue": transaction_amount / 100 / visitors if visitors > 0 else None,
                "conversionRate": transaction_customers / visitors if visitors > 0 else None,
            }
        )
    page_payload: dict[str, object] = {
        "snapshotToken": snapshot,
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": product_count,
            "returned": len(items),
            "truncated": offset + len(items) < product_count,
        },
    }
    if view == "page":
        return page_payload
    daily_total = int(aggregate["date_count"] or 0)
    daily_rows = list(
        rows.values("business_date")
        .annotate(
            page_views=Sum("page_views"),
            visitors=Sum("visitors"),
            transaction_customers=Sum("transaction_customers"),
            transaction_quantity=Sum("transaction_quantity"),
            transaction_amount_cents=Sum("transaction_amount_cents"),
            refund_amount_cents=Sum("refund_amount_cents"),
            favorites=Sum("favorites"),
            add_cart_customers=Sum("add_cart_customers"),
            add_cart_quantity=Sum("add_cart_quantity"),
        )
        .order_by("-business_date")[:MAX_DAYS]
    )
    daily_rows.reverse()
    actual_dates = [str(item["business_date"]) for item in daily_rows]
    missing_dates = (
        sorted(
            set(_date_sequence(str(requested_period["startDate"]), str(requested_period["endDate"])))
            - set(actual_dates)
        )
        if requested_period
        else []
    )
    shops = list(
        rows.values("platform", "shop_name")
        .annotate(product_count=Count(identity_field, distinct=True))
        .order_by("platform", "shop_name")[:MAX_OUTLETS]
    )
    return {
        **summary_payload,
        **page_payload,
        "coverage": {
            "actualDates": actual_dates,
            "missingDates": missing_dates,
            "availableDateMin": available["date_min"],
            "availableDateMax": available["date_max"],
            "total": daily_total,
            "returned": len(actual_dates),
            "truncated": len(actual_dates) < daily_total,
        },
        "platforms": sorted({str(item["platform"]) for item in shops}),
        "shops": [
            {
                "shopName": item["shop_name"],
                "platform": item["platform"],
                "productCount": _zero(item["product_count"]),
            }
            for item in shops
        ],
        "daily": [
            {
                "date": item["business_date"],
                "pageViews": _zero(item["page_views"]),
                "visitors": _zero(item["visitors"]),
                "transactionCustomers": _zero(item["transaction_customers"]),
                "transactionQuantity": _zero(item["transaction_quantity"]),
                "transactionAmountCents": _zero(item["transaction_amount_cents"]),
                "refundAmountCents": _zero(item["refund_amount_cents"]),
                "favorites": _zero(item["favorites"]),
                "addCartCustomers": _zero(item["add_cart_customers"]),
                "addCartQuantity": _zero(item["add_cart_quantity"]),
            }
            for item in daily_rows
        ],
        "dailyPagination": {
            "total": daily_total,
            "returned": len(daily_rows),
            "truncated": len(daily_rows) < daily_total,
        },
    }


PROMOTION_SUM_FIELDS = (
    "spend_cents",
    "net_transaction_amount_cents",
    "gross_transaction_amount_cents",
    "impressions",
    "clicks",
    "net_orders",
    "favorites",
    "cart_quantity",
)


def _promotion_scope(
    *,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object] | None,
    expected_snapshot_token: str | None = None,
) -> tuple[object, object, str]:
    if requested_period is None:
        raise NetshopApiError("推广聚合查询必须显式提供 startDate 和 endDate")
    if not platforms:
        raise NetshopApiError("推广聚合查询必须显式选择京东或天猫平台")
    shops = NetshopPromotionShopDaily.objects.filter(
        platform__in=platforms,
        business_date__gte=requested_period["startDate"],
        business_date__lte=requested_period["endDate"],
    )
    products = NetshopPromotionProductDaily.objects.filter(
        platform__in=platforms,
        business_date__gte=requested_period["startDate"],
        business_date__lte=requested_period["endDate"],
    )
    shops = _apply_platform_outlets(shops, platforms, outlets)
    products = _apply_platform_outlets(products, platforms, outlets)
    active_platforms = sorted(
        {
            item["platform"]
            for item in outlets
            if item["platform"] in platforms
        }
        if outlets
        else set(platforms)
    )
    manifests = {
        item.platform: item
        for item in NetshopPromotionAggregateManifest.objects.filter(
            platform__in=active_platforms
        )
    }
    if any(platform not in manifests or not manifests[platform].ready for platform in active_platforms):
        raise NetshopApiError(
            "所选推广聚合尚未完成回填或已失效",
            code="service_unavailable",
            status=503,
        )
    scope_revisions = list(
        NetshopPromotionScopeRevision.objects.filter(platform__in=active_platforms).values_list(
            "platform", "shop_name", "data_version"
        )
    )
    product_revisions = list(
        NetshopProductDailyScopeRevision.objects.filter(platform__in=active_platforms).values_list(
            "platform", "shop_name", "data_version"
        )
    )
    snapshot = _canonical_token(
        {
            "version": 2,
            "kind": "promotion",
            "revision": revision_value(),
            "period": requested_period,
            "platforms": list(platforms),
            "outlets": list(outlets),
            "manifests": sorted(
                (platform, manifests[platform].data_version) for platform in active_platforms
            ),
            "promotionRevisions": sorted(scope_revisions),
            "productDailyRevisions": sorted(product_revisions),
        }
    )
    if expected_snapshot_token and expected_snapshot_token != snapshot:
        raise NetshopApiError(
            "推广商品与概览数据版本已变化，请重新加载",
            code="service_unavailable",
            status=503,
        )
    return shops, products, snapshot


def _payment_daily(
    *,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object],
) -> dict[str, int]:
    rows = NetshopRow.objects.filter(
        Q(source="tmall_product_daily", dataset="spu_daily", platform="天猫")
        | Q(source="jd_sku_daily", dataset="sku_daily", platform="京东"),
        business_date__gte=requested_period["startDate"],
        business_date__lte=requested_period["endDate"],
    )
    rows = _apply_platform_outlets(rows, platforms, outlets)
    return {
        str(item["business_date"]): _zero(item["payment"])
        for item in rows.values("business_date")
        .annotate(payment=Sum("transaction_amount_cents"))
        .order_by("business_date")[:MAX_DAYS]
    }


def promotion_overview(
    *,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object],
    expected_snapshot_token: str | None = None,
) -> dict[str, object]:
    shops, products, snapshot = _promotion_scope(
        platforms=platforms,
        outlets=outlets,
        requested_period=requested_period,
        expected_snapshot_token=expected_snapshot_token,
    )
    daily_rows = list(
        shops.values("business_date")
        .annotate(**_sum_annotations(PROMOTION_SUM_FIELDS))
        .order_by("business_date")[:MAX_DAYS]
    )
    aggregate = shops.aggregate(
        date_min=Min("business_date"),
        date_max=Max("business_date"),
        date_count=Count("business_date", distinct=True),
        **_sum_annotations(PROMOTION_SUM_FIELDS),
    )
    product_count = products.values("platform", "shop_name", "product_id").distinct().count()
    payment_by_date = _payment_daily(
        platforms=platforms, outlets=outlets, requested_period=requested_period
    )
    daily_by_date = {str(item["business_date"]): item for item in daily_rows}
    promotion_dates = sorted(daily_by_date)
    product_daily_dates = sorted(payment_by_date)
    intersection_dates = sorted(set(promotion_dates) & set(product_daily_dates))
    requested_dates = _date_sequence(
        str(requested_period["startDate"]), str(requested_period["endDate"])
    )
    ratio_spend = sum(_zero(daily_by_date[item]["spend_cents"]) for item in intersection_dates)
    ratio_transaction = sum(
        _zero(daily_by_date[item]["net_transaction_amount_cents"])
        for item in intersection_dates
    )
    platform_payment = sum(payment_by_date[item] for item in intersection_dates)
    spend = _zero(aggregate["spend_cents"])
    net = _zero(aggregate["net_transaction_amount_cents"])
    impressions = _zero(aggregate["impressions"])
    clicks = _zero(aggregate["clicks"])
    option_rows = NetshopPromotionShopDaily.objects.filter(platform__in=platforms)
    option_values = list(
        option_rows.values("platform", "shop_name")
        .distinct()
        .order_by("platform", "shop_name")[:MAX_OUTLETS]
    )
    option_total = option_rows.values("platform", "shop_name").distinct().count()
    return {
        "snapshotToken": snapshot,
        "monetaryUnit": "cents",
        "requestedPeriod": {
            "startDate": requested_period["startDate"],
            "endDate": requested_period["endDate"],
        },
        "dataCutoffDate": aggregate["date_max"],
        "coverage": {
            "promotionDates": promotion_dates,
            "productDailyDates": product_daily_dates,
            "intersectionDates": intersection_dates,
            "missingProductDailyDates": [item for item in requested_dates if item not in payment_by_date],
            "missingPromotionDates": [item for item in requested_dates if item not in daily_by_date],
            "promotionDatesPagination": {
                "total": _zero(aggregate["date_count"]),
                "returned": len(promotion_dates),
                "truncated": len(promotion_dates) < _zero(aggregate["date_count"]),
            },
            "productDailyDatesPagination": {
                "total": len(product_daily_dates),
                "returned": len(product_daily_dates),
                "truncated": False,
            },
            "intersectionTruncated": False,
        },
        "summary": {
            "productCount": product_count,
            "spendCents": spend,
            "netTransactionAmountCents": net,
            "grossTransactionAmountCents": _zero(aggregate["gross_transaction_amount_cents"]),
            "platformPaymentAmountCents": platform_payment,
            "impressions": impressions,
            "clicks": clicks,
            "netOrders": _zero(aggregate["net_orders"]),
            "favorites": _zero(aggregate["favorites"]),
            "cartQuantity": _zero(aggregate["cart_quantity"]),
            "clickThroughRate": clicks / impressions if impressions > 0 else None,
            "averageClickCostCents": spend / clicks if clicks > 0 else None,
            "roas": net / spend if spend > 0 else None,
            "spendRate": ratio_spend / platform_payment if platform_payment > 0 else None,
            "promotionTransactionShare": ratio_transaction / platform_payment if platform_payment > 0 else None,
        },
        "daily": [
            {
                "date": item["business_date"],
                "spendCents": (day_spend := _zero(item["spend_cents"])),
                "netTransactionAmountCents": (day_net := _zero(item["net_transaction_amount_cents"])),
                "platformPaymentAmountCents": (payment := payment_by_date.get(str(item["business_date"]))),
                "impressions": _zero(item["impressions"]),
                "clicks": _zero(item["clicks"]),
                "netOrders": _zero(item["net_orders"]),
                "roas": day_net / day_spend if day_spend > 0 else None,
                "spendRate": day_spend / payment if payment and payment > 0 else None,
                "promotionTransactionShare": day_net / payment if payment and payment > 0 else None,
            }
            for item in daily_rows
        ],
        "dailyPagination": {
            "total": _zero(aggregate["date_count"]),
            "returned": len(daily_rows),
            "truncated": len(daily_rows) < _zero(aggregate["date_count"]),
        },
        "filterOptions": {
            "shops": [
                {"platform": item["platform"], "shopName": item["shop_name"]}
                for item in option_values
            ],
            "pagination": {
                "total": option_total,
                "returned": len(option_values),
                "truncated": len(option_values) < option_total,
            },
        },
    }


def promotion_items(
    *,
    query: str,
    page: int,
    page_size: int,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object],
) -> dict[str, object]:
    if len(query) > 120:
        raise NetshopApiError("q 最多 120 个字符")
    _shops, products, snapshot = _promotion_scope(
        platforms=platforms, outlets=outlets, requested_period=requested_period
    )
    if query:
        products = products.filter(
            Q(product_id__icontains=query)
            | Q(product_name__icontains=query)
            | Q(product_line__icontains=query)
        )
    scope_cutoff = products.aggregate(date_max=Max("business_date"))["date_max"]
    grouped = products.values("platform", "shop_name", "product_id").annotate(
        product_name_value=Max("product_name"),
        date_min=Min("business_date"),
        date_max=Max("business_date"),
        data_days=Count("business_date", distinct=True),
        **_sum_annotations(PROMOTION_SUM_FIELDS),
    ).order_by("-net_transaction_amount_cents", "-spend_cents", "product_id")
    total = grouped.count()
    offset = (page - 1) * page_size
    page_rows = list(grouped[offset : offset + page_size])
    dates_by_identity: dict[tuple[str, str, str], list[str]] = {}
    if page_rows:
        for row in page_rows:
            key = (str(row["platform"]), str(row["shop_name"]), str(row["product_id"]))
            dates_by_identity[key] = list(
                products.filter(
                    platform=key[0], shop_name=key[1], product_id=key[2]
                )
                .order_by("business_date")
                .values_list("business_date", flat=True)
                .distinct()[:MAX_DAYS]
            )
    items: list[dict[str, object]] = []
    for row in page_rows:
        spend = _zero(row["spend_cents"])
        net = _zero(row["net_transaction_amount_cents"])
        impressions = _zero(row["impressions"])
        clicks = _zero(row["clicks"])
        key = (str(row["platform"]), str(row["shop_name"]), str(row["product_id"]))
        dates = dates_by_identity.get(key, [])
        items.append(
            {
                "id": row["product_id"],
                "platform": row["platform"],
                "productName": row["product_name_value"] or "",
                "shopName": row["shop_name"],
                "dateMin": row["date_min"],
                "dateMax": row["date_max"],
                "dates": dates,
                "datesTruncated": len(dates) < _zero(row["data_days"]),
                "dataDays": _zero(row["data_days"]),
                "spendCents": spend,
                "netTransactionAmountCents": net,
                "grossTransactionAmountCents": _zero(row["gross_transaction_amount_cents"]),
                "impressions": impressions,
                "clicks": clicks,
                "netOrders": _zero(row["net_orders"]),
                "favorites": _zero(row["favorites"]),
                "cartQuantity": _zero(row["cart_quantity"]),
                "clickThroughRate": clicks / impressions if impressions > 0 else None,
                "averageClickCostCents": spend / clicks if clicks > 0 else None,
                "roas": net / spend if spend > 0 else None,
            }
        )
    return {
        "snapshotToken": snapshot,
        "monetaryUnit": "cents",
        "requestedPeriod": {
            "startDate": requested_period["startDate"],
            "endDate": requested_period["endDate"],
        },
        "dataCutoffDate": scope_cutoff,
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "truncated": offset + len(items) < total,
        },
    }


def promotion_performance(
    *,
    query: str,
    page: int,
    page_size: int,
    platforms: Sequence[str],
    outlets: Sequence[Mapping[str, str]],
    requested_period: Mapping[str, object],
) -> dict[str, object]:
    items = promotion_items(
        query=query,
        page=page,
        page_size=page_size,
        platforms=platforms,
        outlets=outlets,
        requested_period=requested_period,
    )
    overview_payload = promotion_overview(
        platforms=platforms,
        outlets=outlets,
        requested_period=requested_period,
        expected_snapshot_token=str(items["snapshotToken"]),
    )
    return {
        key: value
        for key, value in {
            **overview_payload,
            **items,
            "dateMin": (
                min(overview_payload["coverage"]["promotionDates"])  # type: ignore[index]
                if overview_payload["coverage"]["promotionDates"]  # type: ignore[index]
                else None
            ),
        }.items()
        if key != "snapshotToken"
    }
