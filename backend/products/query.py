from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import math
import re

from sales.auth import Principal
from sales.consumers import execute_consumer_query, validate_consumer_request
from sales.models import ErpProductMaster
from sales.query import revision_token as sales_revision_token

from .errors import ProductsApiError
from .models import (
    ProductInventoryProjection,
    ProductInventoryProjectionControl,
    ProductShippingRate,
)
from .revisions import revision_value


MAX_PRODUCT_KEYS = 20_000
PRODUCT_CHUNK_SIZE = 1_000
SHOP_SEPARATOR = "\x1f"
RANGES = {"last30", "last90", "halfYear", "custom"}
MARGIN_BANDS = {"below35", "35to40", "40to45", "atLeast45", "unavailable"}
SORTS = {
    "netSalesCents",
    "grossProfitCents",
    "grossMarginRate",
    "refundRate",
    "stockValueCents",
    "netQuantity",
}
SELF_OPERATED_JD_SHOP = "志高商用厨电自营旗舰店"
SELF_OPERATED_JD_ALIASES = {
    "志高商用厨电自营旗舰店",
    "志高商用厨电京东自营旗舰店",
}
SELF_OPERATED_JD_CHANNELS = {
    "京东-志高商用厨电自营旗舰店",
    "志高商用厨电京东自营旗舰店",
}
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> ProductsApiError:
    return ProductsApiError(message, code=code, status=status)


def _valid_date(value: object, label: str) -> str:
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise _error(f"{label} 必须是有效日期")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise _error(f"{label} 必须是有效日期") from error


def _unique_strings(values: object, label: str, maximum: int, length: int = 120) -> list[str]:
    if not isinstance(values, list):
        raise _error(f"{label}筛选无效")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise _error(f"{label}筛选无效")
        value = raw.strip()
        if not value:
            continue
        if len(value) > length:
            raise _error(f"{label}筛选项长度超出限制")
        if value not in result:
            result.append(value)
    if len(result) > maximum:
        raise _error(f"{label}筛选项数量超出限制")
    return result


def canonical_outlet(platform: object, shop: object, channel: object = "") -> dict[str, str]:
    platform_text = str(platform or "").strip()
    shop_text = str(shop or "").strip()
    channel_text = str(channel or "").strip()
    if platform_text == "京东" and (
        shop_text in SELF_OPERATED_JD_ALIASES or channel_text in SELF_OPERATED_JD_CHANNELS
    ):
        shop_text = SELF_OPERATED_JD_SHOP
    return {
        "platform": platform_text,
        "shop": shop_text or channel_text or platform_text or "未分类",
    }


def shop_key(outlet: dict[str, str]) -> str:
    return f"{outlet['platform']}{SHOP_SEPARATOR}{outlet['shop']}"


def normalize_options(raw: dict[str, object]) -> dict[str, object]:
    allowed = {
        "range",
        "startDate",
        "endDate",
        "days",
        "platforms",
        "shopKeys",
        "page",
        "pageSize",
        "query",
        "categories",
        "marginBands",
        "sortBy",
        "direction",
        "projection",
        "expectedSnapshotToken",
    }
    if not set(raw).issubset(allowed):
        raise _error("商品汇总包含未知查询字段")
    range_explicit = "range" in raw
    range_name = raw.get("range") or "last30"
    if not isinstance(range_name, str) or range_name not in RANGES:
        raise _error("不支持的商品统计周期")
    days = raw.get("days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 7 <= days <= 365):
        raise _error("days 必须是 7 到 365 的整数")
    page = raw.get("page", 1)
    page_size = raw.get("pageSize", 50)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10_000:
        raise _error("page 必须是 1 到 10000 的整数")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise _error("pageSize 必须是 1 到 100 的整数")
    query = raw.get("query") or ""
    if not isinstance(query, str) or len(query.strip()) > 1000:
        raise _error("搜索词不能超过 1000 个字符")
    platforms = _unique_strings(raw.get("platforms", []), "平台", 8)
    raw_shop_keys = _unique_strings(raw.get("shopKeys", []), "店铺", 4, 220)
    shops: list[dict[str, str]] = []
    for value in raw_shop_keys:
        pieces = value.split(SHOP_SEPARATOR)
        if len(pieces) != 2 or not pieces[0] or not pieces[1]:
            raise _error("店铺筛选标识格式无效")
        outlet = canonical_outlet(pieces[0], pieces[1])
        if outlet not in shops:
            shops.append(outlet)
    categories = _unique_strings(raw.get("categories", []), "类目", 10)
    margin_bands = _unique_strings(raw.get("marginBands", []), "毛利率", 5)
    if any(value not in MARGIN_BANDS for value in margin_bands):
        raise _error("毛利率筛选项不在允许清单中")
    sort_by = raw.get("sortBy") or "netSalesCents"
    if not isinstance(sort_by, str) or sort_by not in SORTS:
        raise _error("商品排序字段不在允许清单中")
    direction = raw.get("direction") or "desc"
    if direction not in {"asc", "desc"}:
        raise _error("商品排序方向必须是 asc 或 desc")
    projection = raw.get("projection") or "full"
    if projection not in {"full", "page"}:
        raise _error("商品汇总投影视图无效")
    snapshot = raw.get("expectedSnapshotToken")
    if projection == "page":
        if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot):
            raise _error("page 视图必须使用完整汇总返回的有效数据版本")
    elif snapshot is not None:
        raise _error("完整汇总不接受 snapshotToken")
    return {
        "range": range_name,
        "rangeExplicit": range_explicit,
        "startDate": raw.get("startDate"),
        "endDate": raw.get("endDate"),
        "days": days,
        "platforms": platforms,
        "shops": shops,
        "page": page,
        "pageSize": page_size,
        "query": query.strip(),
        "categories": categories,
        "marginBands": margin_bands,
        "sortBy": sort_by,
        "direction": direction,
        "projection": projection,
        "expectedSnapshotToken": snapshot,
    }


def _period(data_start: str, data_cutoff: str, options: dict[str, object]) -> dict[str, object]:
    range_name = str(options["range"])
    if range_name == "custom":
        requested_start = _valid_date(options.get("startDate"), "startDate")
        requested_end = _valid_date(options.get("endDate"), "endDate")
        if requested_start > requested_end:
            raise _error("自定义时间的开始日期不能晚于结束日期")
        if (date.fromisoformat(requested_end) - date.fromisoformat(requested_start)).days + 1 > 730:
            raise _error("商品统计周期最多支持 730 天")
    else:
        requested_days = (
            options.get("days")
            if options.get("days") is not None and not options.get("rangeExplicit")
            else None
        )
        if requested_days is None:
            requested_days = 183 if range_name == "halfYear" else 90 if range_name == "last90" else 30
        requested_days = int(requested_days)
        range_name = "halfYear" if requested_days >= 180 else "last90" if requested_days >= 90 else "last30"
        requested_end = data_cutoff
        requested_start = (date.fromisoformat(data_cutoff) - timedelta(days=requested_days - 1)).isoformat()
    start = max(requested_start, data_start)
    end = min(requested_end, data_cutoff)
    return {
        "range": range_name,
        "requestedStartDate": requested_start,
        "requestedEndDate": requested_end,
        "startDate": start if start <= end else None,
        "endDate": end if start <= end else None,
    }


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator) / float(denominator) if denominator > 0 else None


def _snapshot_token(
    sales_revision: str,
    product_revision: str,
    control: ProductInventoryProjectionControl,
) -> str:
    raw = json.dumps(
        {
            "version": 4,
            "salesRevision": sales_revision,
            "productRevision": product_revision,
            "inventoryRevision": control.active_revision,
            "inventorySourceBatchId": control.active_source_batch_id,
            "inventorySnapshotDate": control.active_snapshot_date,
            "inventoryTotal": int(control.active_total),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_dimensions() -> tuple[
    list[str], dict[str, dict[str, object]], dict[str, dict[str, object]], dict[str, float], ProductInventoryProjectionControl
]:
    control = ProductInventoryProjectionControl.objects.get(id=1)
    erp_rows = list(
        ErpProductMaster.objects.order_by("product_code").values(
            "product_code", "product_name", "brand", "specification", "category", "supplier"
        )[: MAX_PRODUCT_KEYS + 1]
    )
    if len(erp_rows) > MAX_PRODUCT_KEYS:
        raise _error("ERP 货品规模超过商品域安全上限", code="service_unavailable", status=503)
    inventory_rows: list[dict[str, object]] = []
    if control.active_revision:
        inventory_rows = list(
            ProductInventoryProjection.objects.filter(projection_revision=control.active_revision)
            .order_by("product_code")
            .values(
                "product_code",
                "brand",
                "available_quantity",
                "known_stock_value_cents",
                "priced_available_quantity",
            )[: MAX_PRODUCT_KEYS + 1]
        )
        if len(inventory_rows) > MAX_PRODUCT_KEYS or len(inventory_rows) != int(control.active_total):
            raise _error("商品库存投影版本与行数不一致", code="service_unavailable", status=503)
    elif int(control.active_total) != 0:
        raise _error("商品库存投影控制记录无效", code="service_unavailable", status=503)
    shipping_rows = list(
        ProductShippingRate.objects.order_by("product_code").values("product_code", "shipping_rate")[: MAX_PRODUCT_KEYS + 1]
    )
    if len(shipping_rows) > MAX_PRODUCT_KEYS:
        raise _error("SKU 快递费率规模超过商品域安全上限", code="service_unavailable", status=503)
    erp = {str(row["product_code"]): row for row in erp_rows}
    inventory = {str(row["product_code"]): row for row in inventory_rows}
    shipping = {str(row["product_code"]): float(row["shipping_rate"]) for row in shipping_rows}
    product_codes = sorted(set(erp) | set(inventory), key=lambda value: value.encode("utf-8"))
    if len(product_codes) > MAX_PRODUCT_KEYS:
        raise _error("商品规格数量超过安全上限", code="service_unavailable", status=503)
    return product_codes, erp, inventory, shipping, control


def _sales_performance(
    principal: Principal,
    product_codes: list[str],
    start_date: str,
    end_date: str,
    platforms: list[str],
    shops: list[dict[str, str]],
    expected_latest_batch: object,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, str]]]:
    rows: dict[str, dict[str, object]] = {}
    outlet_options: dict[str, dict[str, str]] = {}
    end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    for offset in range(0, len(product_codes), PRODUCT_CHUNK_SIZE):
        requested_codes = product_codes[offset : offset + PRODUCT_CHUNK_SIZE]
        request = validate_consumer_request(
            {
                "operation": "product_performance",
                "startDate": start_date,
                "endDate": end_exclusive,
                "platforms": platforms,
                "outlets": [
                    {"platform": outlet["platform"], "shopName": outlet["shop"]}
                    for outlet in shops
                ],
                "productCodes": requested_codes,
                "limit": 5_000,
            }
        )
        data = execute_consumer_query(principal, request)
        if data.get("truncated") is not False or data.get("latestBatch") != expected_latest_batch:
            raise _error("Django 销售读取返回的数据不完整", code="service_unavailable", status=503)
        requested = set(requested_codes)
        for raw in data.get("rows", []):
            if not isinstance(raw, dict) or str(raw.get("productCode") or "") not in requested:
                raise _error("Django 销售读取返回的数据无效", code="service_unavailable", status=503)
            code = str(raw["productCode"])
            if code in rows:
                raise _error("Django 销售读取返回重复规格", code="service_unavailable", status=503)
            rows[code] = raw
        for raw in data.get("outletOptions", []):
            if not isinstance(raw, dict):
                raise _error("Django 销售店铺维度无效", code="service_unavailable", status=503)
            outlet = canonical_outlet(raw.get("platform"), raw.get("shopName"), raw.get("channel"))
            outlet_options[shop_key(outlet)] = outlet
    return rows, outlet_options


def _merge(
    sales: dict[str, object],
    erp: dict[str, object] | None,
    inventory: dict[str, object] | None,
    shipping_rate: float | None,
) -> dict[str, object]:
    available = int(inventory["available_quantity"]) if inventory else None
    priced = int(inventory["priced_available_quantity"]) if inventory else None
    known_value = int(inventory["known_stock_value_cents"]) if inventory else None
    net_sales = int(sales["netSalesCents"])
    gross_sales = int(sales["grossSalesCents"])
    refund = int(sales["refundAmountCents"])
    return {
        "product_code": str(sales["productCode"]),
        "product_name": str((erp or {}).get("product_name") or sales.get("productName") or sales["productCode"]),
        "brand": str((erp or {}).get("brand") or (inventory or {}).get("brand") or ""),
        "supplier_name": str((erp or {}).get("supplier") or sales.get("supplier") or ""),
        "specification": str((erp or {}).get("specification") or sales.get("specification") or ""),
        "category": str((erp or {}).get("category") or sales.get("category") or "未分类"),
        "net_quantity": int(sales["netQuantity"]),
        "gross_sales_cents": gross_sales,
        "refund_amount_cents": refund,
        "net_sales_cents": net_sales,
        "cost_cents": int(sales["costCents"]),
        "fee_cents": int(sales["feeCents"]),
        "gross_profit_cents": int(sales["grossProfitCents"]),
        "absolute_quantity": int(sales["absoluteQuantity"]),
        "absolute_cost_cents": int(sales["absoluteCostCents"]),
        "available_quantity": available,
        "stock_value_cents": known_value if available is not None and priced is not None and available <= priced else None,
        "known_stock_value_cents": known_value,
        "priced_available_quantity": priced,
        "gross_margin_rate": _rate(int(sales["grossProfitCents"]), net_sales),
        "refund_rate": _rate(refund, gross_sales) or 0,
        "shipping_rate": shipping_rate,
        "outlets": sales.get("outlets") if isinstance(sales.get("outlets"), list) else [],
    }


def _matches_text(row: dict[str, object], keywords: list[str]) -> bool:
    if not keywords:
        return True
    fields = [
        str(row.get(key) or "").lower()
        for key in ("product_code", "product_name", "brand", "supplier_name", "specification", "category")
    ]
    return any(keyword in field for keyword in keywords for field in fields)


def _matches_margin(row: dict[str, object], bands: list[str]) -> bool:
    if not bands:
        return True
    margin = row["gross_margin_rate"]
    for band in bands:
        if band == "unavailable" and margin is None:
            return True
        if margin is None:
            continue
        value = float(margin)
        if band == "below35" and value < 0.35:
            return True
        if band == "35to40" and 0.35 <= value < 0.4:
            return True
        if band == "40to45" and 0.4 <= value < 0.45:
            return True
        if band == "atLeast45" and value >= 0.45:
            return True
    return False


def empty_metrics() -> dict[str, object]:
    return {
        "skuCount": 0,
        "grossSalesCents": 0,
        "netSalesCents": 0,
        "grossProfitCents": 0,
        "grossMarginRate": None,
        "lossSkuCount": 0,
        "stockedSkuCount": 0,
        "marginBuckets": {
            "below35Count": 0,
            "between35And40Count": 0,
            "between40And45Count": 0,
            "atLeast45Count": 0,
        },
    }


def _item(row: dict[str, object]) -> dict[str, object]:
    outlets: dict[str, dict[str, str]] = {}
    for raw in row["outlets"]:
        if isinstance(raw, dict):
            outlet = canonical_outlet(raw.get("platform"), raw.get("shopName"), raw.get("channel"))
            outlets[shop_key(outlet)] = outlet
    net_quantity = int(row["net_quantity"])
    net_sales = int(row["net_sales_cents"])
    absolute_quantity = int(row["absolute_quantity"])
    available = row["available_quantity"]
    return {
        "productCode": row["product_code"],
        "productName": row["product_name"],
        "brand": row["brand"],
        "supplierName": row["supplier_name"],
        "specification": row["specification"],
        "category": row["category"],
        "outlets": list(outlets.values()),
        "netQuantity": net_quantity,
        "grossSalesCents": int(row["gross_sales_cents"]),
        "refundAmountCents": int(row["refund_amount_cents"]),
        "netSalesCents": net_sales,
        "costCents": int(row["cost_cents"]),
        "feeCents": int(row["fee_cents"]),
        "grossProfitCents": int(row["gross_profit_cents"]),
        "grossMarginRate": row["gross_margin_rate"],
        "refundRate": row["refund_rate"],
        "shippingRate": row["shipping_rate"],
        "averageSalePriceCents": _rate(net_sales, net_quantity),
        "averageCostCents": _rate(int(row["absolute_cost_cents"]), absolute_quantity),
        "observedFeeRate": _rate(abs(int(row["fee_cents"])), int(row["gross_sales_cents"])),
        "availableQuantity": available,
        "stockValueCents": row["stock_value_cents"],
        "knownStockValueCents": row["known_stock_value_cents"],
        "costCoverageRate": None
        if available is None
        else min(1.0, max(0.0, int(row["priced_available_quantity"] or 0) / int(available)))
        if int(available) > 0
        else 1,
    }


def _pagination(page: int, page_size: int, total: int, returned: int) -> dict[str, object]:
    return {
        "page": page,
        "pageSize": page_size,
        "total": total,
        "returned": returned,
        "totalPages": math.ceil(total / page_size) if total else 0,
        "truncated": (page - 1) * page_size + returned < total,
    }


def product_summary(principal: Principal, raw_options: dict[str, object]) -> dict[str, object]:
    if principal.scope is not None:
        raise _error("商品经营汇总仅支持未受限数据范围账号", code="access_denied", status=403)
    options = normalize_options(raw_options)
    sales_before = sales_revision_token()
    product_before = revision_value()
    freshness = execute_consumer_query(
        principal, validate_consumer_request({"operation": "freshness"})
    )
    product_codes, erp, inventory, shipping, control = _read_dimensions()
    snapshot = _snapshot_token(sales_before, product_before, control)
    if options["projection"] == "page" and options["expectedSnapshotToken"] != snapshot:
        raise _error("商品列表与汇总数据版本已变化，请重新加载", code="service_unavailable", status=503)
    page = int(options["page"])
    page_size = int(options["pageSize"])
    sort = {"by": options["sortBy"], "direction": options["direction"]}

    def stable() -> None:
        after_control = ProductInventoryProjectionControl.objects.get(id=1)
        if (
            sales_revision_token() != sales_before
            or revision_value() != product_before
            or _snapshot_token(sales_before, product_before, after_control) != snapshot
        ):
            raise _error("商品汇总在读取期间已更新，请重新加载", code="service_unavailable", status=503)

    def empty_payload(range_name: str, requested_start: str | None, requested_end: str | None) -> dict[str, object]:
        page_payload = {
            "projection": "page",
            "snapshotToken": snapshot,
            "sort": sort,
            "pagination": _pagination(page, page_size, 0, 0),
            "items": [],
        }
        stable()
        if options["projection"] == "page":
            return page_payload
        return {
            **page_payload,
            "projection": "full",
            "hasSales": bool(freshness.get("dataCutoffDate")),
            "range": range_name,
            "sync": {
                "salesThrough": None,
                "salesWindowStart": None,
                "requestedStartDate": requested_start,
                "requestedEndDate": requested_end,
                "dataStartDate": freshness.get("dataStartDate"),
                "dataCutoffDate": freshness.get("dataCutoffDate"),
                "inventoryAsOf": control.active_snapshot_date or None,
                "latestSalesFile": (freshness.get("latestBatch") or {}).get("fileName")
                if isinstance(freshness.get("latestBatch"), dict)
                else None,
            },
            "filters": {"platforms": [], "shops": [], "categories": []},
            "filtersApplied": {
                "platforms": options["platforms"],
                "shops": [
                    {"key": shop_key(value), "platform": value["platform"], "shop": value["shop"]}
                    for value in options["shops"]
                ],
                "query": options["query"],
                "categories": options["categories"],
                "marginBands": options["marginBands"],
            },
            "metrics": empty_metrics(),
        }

    data_start = freshness.get("dataStartDate")
    data_cutoff = freshness.get("dataCutoffDate")
    if not isinstance(data_start, str) or not isinstance(data_cutoff, str):
        return empty_payload(str(options["range"]), None, None)
    period = _period(data_start, data_cutoff, options)
    if period["startDate"] is None or period["endDate"] is None or not product_codes:
        return empty_payload(
            str(period["range"]),
            str(period["requestedStartDate"]),
            str(period["requestedEndDate"]),
        )
    performance, outlet_options = _sales_performance(
        principal,
        product_codes,
        str(period["startDate"]),
        str(period["endDate"]),
        options["platforms"],
        options["shops"],
        freshness.get("latestBatch"),
    )
    all_rows = [
        _merge(row, erp.get(code), inventory.get(code), shipping.get(code))
        for code, row in performance.items()
    ]
    # The validated 1000-character query bounds this list; never silently drop later codes.
    keywords = list(dict.fromkeys(value.lower() for value in re.split(r"[\s,，;；]+", str(options["query"])) if value))
    facet_rows = [row for row in all_rows if _matches_text(row, keywords)]
    filtered = [
        row
        for row in facet_rows
        if (not options["categories"] or row["category"] in options["categories"])
        and _matches_margin(row, options["marginBands"])
    ]
    sort_map = {
        "netSalesCents": "net_sales_cents",
        "grossProfitCents": "gross_profit_cents",
        "grossMarginRate": "gross_margin_rate",
        "refundRate": "refund_rate",
        "stockValueCents": "stock_value_cents",
        "netQuantity": "net_quantity",
    }
    sort_field = sort_map[str(options["sortBy"])]
    direction = str(options["direction"])
    # Stable two-pass sorting preserves product_code as the tie breaker while
    # keeping nulls first for ascending and last for descending.
    filtered.sort(key=lambda row: str(row["product_code"]).encode("utf-8"))
    filtered.sort(
        key=lambda row: (
            row[sort_field] is not None if direction == "asc" else row[sort_field] is None,
            float(row[sort_field] or 0) if direction == "asc" else -float(row[sort_field] or 0),
        )
    )
    offset = (page - 1) * page_size
    selected = filtered[offset : offset + page_size]
    page_payload = {
        "projection": "page",
        "snapshotToken": snapshot,
        "sort": sort,
        "pagination": _pagination(page, page_size, len(filtered), len(selected)),
        "items": [_item(row) for row in selected],
    }
    stable()
    if options["projection"] == "page":
        return page_payload
    gross_sales = sum(int(row["gross_sales_cents"]) for row in filtered)
    net_sales = sum(int(row["net_sales_cents"]) for row in filtered)
    gross_profit = sum(int(row["gross_profit_cents"]) for row in filtered)
    metrics = {
        "skuCount": len(filtered),
        "grossSalesCents": gross_sales,
        "netSalesCents": net_sales,
        "grossProfitCents": gross_profit,
        "grossMarginRate": _rate(gross_profit, net_sales),
        "lossSkuCount": sum(
            1 for row in filtered if int(row["net_sales_cents"]) > 0 and int(row["gross_profit_cents"]) < 0
        ),
        "stockedSkuCount": sum(1 for row in filtered if int(row["available_quantity"] or 0) > 0),
        "marginBuckets": {
            "below35Count": sum(1 for row in filtered if row["gross_margin_rate"] is not None and float(row["gross_margin_rate"]) < 0.35),
            "between35And40Count": sum(1 for row in filtered if row["gross_margin_rate"] is not None and 0.35 <= float(row["gross_margin_rate"]) < 0.4),
            "between40And45Count": sum(1 for row in filtered if row["gross_margin_rate"] is not None and 0.4 <= float(row["gross_margin_rate"]) < 0.45),
            "atLeast45Count": sum(1 for row in filtered if row["gross_margin_rate"] is not None and float(row["gross_margin_rate"]) >= 0.45),
        },
    }
    sorted_outlets = sorted(
        outlet_options.values(), key=lambda item: (item["platform"].encode("utf-8"), item["shop"].encode("utf-8"))
    )
    return {
        **page_payload,
        "projection": "full",
        "hasSales": True,
        "range": period["range"],
        "sync": {
            "salesThrough": period["endDate"],
            "salesWindowStart": period["startDate"],
            "requestedStartDate": period["requestedStartDate"],
            "requestedEndDate": period["requestedEndDate"],
            "dataStartDate": data_start,
            "dataCutoffDate": data_cutoff,
            "inventoryAsOf": control.active_snapshot_date or None,
            "latestSalesFile": (freshness.get("latestBatch") or {}).get("fileName")
            if isinstance(freshness.get("latestBatch"), dict)
            else None,
        },
        "filters": {
            "platforms": list(dict.fromkeys(value["platform"] for value in sorted_outlets)),
            "shops": [
                {"key": shop_key(value), "platform": value["platform"], "shop": value["shop"]}
                for value in sorted_outlets
            ],
            "categories": sorted(
                set(str(row["category"] or "未分类") for row in facet_rows), key=lambda value: value.encode("utf-8")
            )[:500],
        },
        "filtersApplied": {
            "platforms": options["platforms"],
            "shops": [
                {"key": shop_key(value), "platform": value["platform"], "shop": value["shop"]}
                for value in options["shops"]
            ],
            "query": options["query"],
            "categories": options["categories"],
            "marginBands": options["marginBands"],
        },
        "metrics": metrics,
    }
