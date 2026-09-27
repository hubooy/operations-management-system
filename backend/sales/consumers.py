"""Bounded, operation-specific sales readers for other backend domains.

This module intentionally exposes no SQL-shaped input.  Every accepted JSON
field is validated here and mapped to a fixed ORM query over the PostgreSQL
sales projection.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Callable

from django.db.models import (
    BigIntegerField,
    Case,
    Count,
    F,
    Max,
    Min,
    Q,
    Subquery,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.functions import (
    Abs,
    Coalesce,
    Concat,
    Lower,
    NullIf,
    Replace,
    Substr,
    Trim,
)
from django.http import HttpRequest

from .auth import Principal
from .models import PRICE_ADJUSTMENT_PRODUCT_CODE, SalesImportBatch, SalesOrderLine
from .query import (
    SalesAccessError,
    SalesRequestError,
    _apply_principal_scope,
    binary_text_key,
    parse_product_queries,
    resolve_product_codes,
)
from .summary import SALES_RANGES, get_sales_summary


CONSUMER_OPERATIONS = frozenset(
    {
        "analysis_records",
        "freshness",
        "summary",
        "inventory_demand",
        "inventory_inbound_windows",
        "product_performance",
        "customer_service_products",
        "netshop_product_metrics",
        "market_product_metrics",
        "order_search",
        "import_batch_search",
        "category_options",
    }
)
CONSUMER_BODY_MAX_BYTES = 512 * 1024
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSON_CONTENT_TYPE_RE = re.compile(
    r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I
)
SPECIAL_PRODUCT_NAME = "补差价专用"
SELF_OPERATED_JD_SHOP = "志高商用厨电自营旗舰店"
SELF_OPERATED_JD_ALIASES = (
    "志高商用厨电自营旗舰店",
    "志高商用厨电京东自营旗舰店",
)
SELF_OPERATED_JD_CHANNELS = (
    "京东-志高商用厨电自营旗舰店",
    "志高商用厨电京东自营旗舰店",
)


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def parse_consumer_body(request: HttpRequest) -> dict[str, object]:
    if request.META.get("QUERY_STRING", ""):
        raise SalesRequestError("消费者查询不接受 URL 查询参数")
    content_type = request.headers.get("Content-Type", "")
    if not JSON_CONTENT_TYPE_RE.match(content_type):
        raise SalesRequestError("消费者查询只接受 application/json")
    raw = request.body
    if not raw or len(raw) > CONSUMER_BODY_MAX_BYTES:
        raise SalesRequestError("消费者查询正文为空或超出 512 KiB 上限")
    try:
        payload: Any = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SalesRequestError("消费者查询 JSON 无效") from error
    if not isinstance(payload, dict):
        raise SalesRequestError("消费者查询正文必须是 JSON 对象")
    return validate_consumer_request(payload)


def _shape(
    value: object,
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SalesRequestError(f"{label} 必须是对象")
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise SalesRequestError(f"{label} 字段不完整或包含未知字段")
    return value


def _text(
    value: object,
    *,
    label: str,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SalesRequestError(f"{label} 必须是字符串")
    normalized = value.strip()
    if (not normalized and not allow_empty) or len(normalized) > maximum:
        raise SalesRequestError(f"{label} 为空或超过 {maximum} 字")
    return normalized


def _optional_text(
    payload: dict[str, object], key: str, *, maximum: int, nullable: bool = False
) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if value is None and nullable:
        return None
    return _text(value, label=key, maximum=maximum)


def _integer(
    payload: dict[str, object], key: str, *, default: int | None, maximum: int
) -> int:
    if key not in payload:
        if default is None:
            raise SalesRequestError(f"{key} 是必填字段")
        return default
    value = payload[key]
    if type(value) is not int or value < 1 or value > maximum:
        raise SalesRequestError(f"{key} 必须是 1 到 {maximum} 的整数")
    return value


def _date(value: object, label: str) -> str:
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise SalesRequestError(f"{label} 必须是 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise SalesRequestError(f"{label} 必须是有效日期") from error


def _date_value(
    payload: dict[str, object], key: str, *, required: bool, nullable: bool = False
) -> str | None:
    if key not in payload:
        if required:
            raise SalesRequestError(f"{key} 是必填字段")
        return None
    value = payload[key]
    if value is None and nullable:
        return None
    return _date(value, key)


def _exclusive_range(
    payload: dict[str, object], *, required: bool, maximum_days: int = 730
) -> tuple[str | None, str | None]:
    start = _date_value(payload, "startDate", required=required, nullable=not required)
    end = _date_value(payload, "endDate", required=required, nullable=not required)
    if (start is None) != (end is None):
        raise SalesRequestError("startDate 与 endDate 必须同时提供或同时为空")
    if start is not None and end is not None:
        start_value = date.fromisoformat(start)
        end_value = date.fromisoformat(end)
        if start_value >= end_value:
            raise SalesRequestError("左闭右开日期要求 startDate 早于 endDate")
        if (end_value - start_value).days > maximum_days:
            raise SalesRequestError(f"日期跨度不能超过 {maximum_days} 天")
    return start, end


def _strings(
    payload: dict[str, object],
    key: str,
    *,
    maximum_items: int,
    maximum_length: int,
    required: bool = False,
    nullable: bool = False,
) -> list[str] | None:
    if key not in payload:
        if required:
            raise SalesRequestError(f"{key} 是必填字段")
        return None if nullable else []
    value = payload[key]
    if value is None and nullable:
        return None
    if not isinstance(value, list) or len(value) > maximum_items:
        raise SalesRequestError(f"{key} 必须是最多 {maximum_items} 项的数组")
    normalized: list[str] = []
    for item in value:
        normalized.append(
            _text(item, label=f"{key} 项", maximum=maximum_length)
        )
    return list(dict.fromkeys(normalized))


def _outlet(value: object, label: str) -> dict[str, object]:
    item = _shape(
        value,
        allowed={"platform", "shopName", "channel"},
        required={"platform", "shopName"},
        label=label,
    )
    channel = item.get("channel")
    if channel is not None:
        channel = _text(channel, label=f"{label}.channel", maximum=100)
    return {
        "platform": _text(item["platform"], label=f"{label}.platform", maximum=100),
        "shopName": _text(item["shopName"], label=f"{label}.shopName", maximum=100),
        "channel": channel,
    }


def _outlets(
    payload: dict[str, object], key: str, *, maximum_items: int
) -> list[dict[str, object]]:
    if key not in payload:
        return []
    value = payload[key]
    if not isinstance(value, list) or len(value) > maximum_items:
        raise SalesRequestError(f"{key} 必须是最多 {maximum_items} 项的数组")
    normalized = [_outlet(item, f"{key}[{index}]") for index, item in enumerate(value)]
    return list(
        {
            (item["platform"], item["shopName"], item["channel"]): item
            for item in normalized
        }.values()
    )


def _resolved_outlets(
    payload: dict[str, object], key: str, *, products: bool, maximum_items: int
) -> list[dict[str, object]]:
    value = payload.get(key)
    if not isinstance(value, list) or len(value) > maximum_items:
        raise SalesRequestError(f"{key} 必须是最多 {maximum_items} 项的数组")
    required = {"platform", "canonicalShopName", "rawShopName"}
    if products:
        required.add("salesProductCode")
    allowed = required | {"rawChannel"}
    rows: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        item = _shape(raw, allowed=allowed, required=required, label=f"{key}[{index}]")
        row: dict[str, object] = {
            "platform": _text(item["platform"], label=f"{key}.platform", maximum=100),
            "canonicalShopName": _text(
                item["canonicalShopName"], label=f"{key}.canonicalShopName", maximum=100
            ),
            "rawShopName": _text(
                item["rawShopName"], label=f"{key}.rawShopName", maximum=100
            ),
            "rawChannel": None,
        }
        if item.get("rawChannel") is not None:
            row["rawChannel"] = _text(
                item["rawChannel"], label=f"{key}.rawChannel", maximum=100
            )
        if products:
            row["salesProductCode"] = _text(
                item["salesProductCode"], label=f"{key}.salesProductCode", maximum=200
            )
        rows.append(row)
    identity_keys = (
        ("platform", "canonicalShopName", "rawShopName", "rawChannel", "salesProductCode")
        if products
        else ("platform", "canonicalShopName", "rawShopName", "rawChannel")
    )
    return list({tuple(row[key] for key in identity_keys): row for row in rows}.values())


def validate_consumer_request(payload: dict[str, object]) -> dict[str, object]:
    operation = payload.get("operation")
    if operation == "analysis_records":
        from .analysis import validate
        return validate(payload)
    if not isinstance(operation, str) or operation not in CONSUMER_OPERATIONS:
        raise SalesRequestError("operation 不在固定消费者查询清单中")
    allowed: dict[str, set[str]] = {
        "freshness": {"operation"},
        "summary": {
            "operation", "range", "startDate", "endDate", "productQueries",
            "platforms", "outlets", "categories",
        },
        "inventory_demand": {"operation", "startDate", "endDate", "productCodes", "limit"},
        "inventory_inbound_windows": {"operation", "asOfDate", "productCodes", "limit"},
        "product_performance": {
            "operation", "startDate", "endDate", "platforms", "outlets",
            "productCodes", "limit",
        },
        "customer_service_products": {"operation", "onlineSpecCodes", "categories", "limit"},
        "netshop_product_metrics": {
            "operation", "identities", "outletScopes", "startDate", "endDate", "allowedChannels",
        },
        "market_product_metrics": {"operation", "productCodes", "startDate", "endDate"},
        "order_search": {"operation", "query", "page", "pageSize"},
        "import_batch_search": {"operation", "query", "page", "pageSize"},
        "category_options": {"operation", "limit"},
    }
    if not set(payload).issubset(allowed[operation]):
        raise SalesRequestError("消费者查询包含未知字段")
    normalized: dict[str, object] = {"operation": operation}
    if operation == "freshness":
        return normalized
    if operation == "summary":
        range_name = payload.get("range")
        if not isinstance(range_name, str) or range_name not in SALES_RANGES:
            raise SalesRequestError("summary.range 无效")
        normalized["range"] = range_name
        normalized["startDate"] = _date_value(payload, "startDate", required=False)
        normalized["endDate"] = _date_value(payload, "endDate", required=False)
        if range_name == "custom":
            start_date = normalized["startDate"]
            end_date = normalized["endDate"]
            if start_date is None or end_date is None:
                raise SalesRequestError("summary 自定义周期必须提供 startDate 和 endDate")
            start_value = date.fromisoformat(str(start_date))
            end_value = date.fromisoformat(str(end_date))
            if start_value >= end_value:
                raise SalesRequestError("summary 日期必须满足 startDate < endDate")
            if (end_value - start_value).days > 366:
                raise SalesRequestError("summary 自定义周期最长支持 366 天")
        normalized["productQueries"] = _strings(
            payload, "productQueries", maximum_items=100, maximum_length=200
        )
        normalized["platforms"] = _strings(
            payload, "platforms", maximum_items=50, maximum_length=100
        )
        normalized["outlets"] = _outlets(payload, "outlets", maximum_items=50)
        normalized["categories"] = _strings(
            payload, "categories", maximum_items=50, maximum_length=100
        )
        return normalized
    if operation == "inventory_demand":
        normalized["startDate"], normalized["endDate"] = _exclusive_range(
            payload, required=False
        )
        normalized["productCodes"] = _strings(
            payload, "productCodes", maximum_items=500, maximum_length=200
        )
        normalized["limit"] = _integer(payload, "limit", default=10_000, maximum=10_000)
        return normalized
    if operation == "inventory_inbound_windows":
        normalized["asOfDate"] = _date_value(
            payload, "asOfDate", required=False, nullable=True
        )
        normalized["productCodes"] = _strings(
            payload, "productCodes", maximum_items=5_000, maximum_length=200, required=True
        )
        if not normalized["productCodes"]:
            raise SalesRequestError("productCodes 不能为空")
        normalized["limit"] = _integer(payload, "limit", default=10_000, maximum=10_000)
        return normalized
    if operation == "product_performance":
        normalized["startDate"], normalized["endDate"] = _exclusive_range(
            payload, required=True
        )
        normalized["platforms"] = _strings(
            payload, "platforms", maximum_items=20, maximum_length=100
        )
        normalized["outlets"] = _outlets(payload, "outlets", maximum_items=50)
        normalized["productCodes"] = _strings(
            payload, "productCodes", maximum_items=1_000, maximum_length=200
        )
        normalized["limit"] = _integer(payload, "limit", default=5_000, maximum=5_000)
        return normalized
    if operation == "customer_service_products":
        normalized["onlineSpecCodes"] = _strings(
            payload, "onlineSpecCodes", maximum_items=2_000, maximum_length=200
        )
        normalized["categories"] = _strings(
            payload, "categories", maximum_items=100, maximum_length=100
        )
        normalized["limit"] = _integer(payload, "limit", default=5_000, maximum=5_000)
        return normalized
    if operation == "netshop_product_metrics":
        normalized["identities"] = _resolved_outlets(
            payload, "identities", products=True, maximum_items=1_000
        )
        normalized["outletScopes"] = _resolved_outlets(
            payload, "outletScopes", products=False, maximum_items=200
        )
        normalized["startDate"], normalized["endDate"] = _exclusive_range(
            payload, required=False
        )
        normalized["allowedChannels"] = _strings(
            payload,
            "allowedChannels",
            maximum_items=100,
            maximum_length=100,
            nullable=True,
        )
        return normalized
    if operation == "market_product_metrics":
        normalized["productCodes"] = _strings(
            payload,
            "productCodes",
            maximum_items=1_000,
            maximum_length=200,
            required=True,
        )
        normalized["startDate"], normalized["endDate"] = _exclusive_range(
            payload, required=False
        )
        return normalized
    if operation in {"order_search", "import_batch_search"}:
        query = _text(
            payload.get("query"),
            label="query",
            maximum=80,
            allow_empty=operation == "import_batch_search",
        )
        if operation == "order_search" and len(query) < 2:
            raise SalesRequestError("query 至少需要 2 个字符")
        normalized.update(
            {
                "query": query,
                "page": _integer(payload, "page", default=None, maximum=10_000),
                "pageSize": _integer(payload, "pageSize", default=None, maximum=100),
            }
        )
        return normalized
    normalized["limit"] = _integer(payload, "limit", default=300, maximum=500)
    return normalized


def _scoped_business(principal: Principal):
    queryset, _scope_mode = _apply_principal_scope(
        SalesOrderLine.objects.filter(is_business_row=True), principal
    )
    return queryset


def _without_special_products(queryset):
    return (
        queryset.exclude(product_code=PRICE_ADJUSTMENT_PRODUCT_CODE)
        .annotate(_consumer_product_name=Trim("product_name"))
        .exclude(_consumer_product_name=SPECIAL_PRODUCT_NAME)
    )


def _bounds(queryset) -> tuple[str | None, str | None]:
    values = queryset.aggregate(start=Min("business_date"), end=Max("business_date"))
    start = values["start"]
    end = values["end"]
    return (
        start.isoformat() if start is not None else None,
        end.isoformat() if end is not None else None,
    )


def _latest_batch(principal: Principal) -> dict[str, object] | None:
    batches = SalesImportBatch.objects.filter(status="completed")
    if principal.scope is not None:
        relevant = _scoped_business(principal).values("last_import_batch_id")
        batches = batches.filter(id__in=Subquery(relevant))
    row = batches.order_by("-completed_at", "-created_at", "id").first()
    if row is None:
        return None
    return {
        "id": row.id,
        "fileName": row.file_name,
        "completedAt": row.completed_at,
        "rowCount": int(row.row_count),
    }


def _freshness(principal: Principal, _request: dict[str, object]) -> dict[str, object]:
    data_start, data_cutoff = _bounds(_scoped_business(principal))
    return {
        "dataStartDate": data_start,
        "dataCutoffDate": data_cutoff,
        "latestBatch": _latest_batch(principal),
    }


def _summary(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    product_queries = parse_product_queries(request["productQueries"])
    outlets: list[dict[str, str]] = []
    for outlet in request["outlets"]:
        platform = str(outlet["platform"])
        shop = str(outlet["shopName"])
        aliases = (
            SELF_OPERATED_JD_ALIASES
            if platform == "京东" and shop == SELF_OPERATED_JD_SHOP
            else (shop,)
        )
        outlets.extend({"platform": platform, "shop": alias} for alias in aliases)
    # The consumer contract is left-closed/right-open.  The established summary
    # service is inclusive at both ends, so adapt only at this fixed boundary.
    inclusive_end = request["endDate"]
    if request["range"] == "custom" and inclusive_end is not None:
        inclusive_end = (
            date.fromisoformat(str(inclusive_end)) - timedelta(days=1)
        ).isoformat()
    payload = get_sales_summary(
        range_name=str(request["range"]),
        projection="full",
        start_date=request["startDate"],
        end_date=inclusive_end,
        product_queries=product_queries,
        product_codes=resolve_product_codes(product_queries, principal),
        platforms=request["platforms"],
        shop=None,
        outlets=outlets,
        categories=request["categories"],
        principal=principal,
    )
    keys = (
        "range", "startDate", "endDate", "previousStartDate", "previousEndDate",
        "requestedStartDate", "requestedEndDate", "dataCutoffDate",
        "periodAdjustedToDataCutoff", "comparisonDayCount", "current", "previous",
        "yearAgo", "yearAgoStartDate", "yearAgoEndDate", "channels", "outlets",
        "shops", "platforms", "daily", "previousDaily", "yearAgoDaily",
        "trendStartDate", "trendEndDate", "trendReturned", "trendTruncated",
    )
    return {key: payload[key] for key in keys if key in payload}


def _warehouse_key_expression():
    expression = Lower(Trim(F("warehouse")), output_field=TextField())
    for token in ("配送中心", "仓库", "库房", "仓", " ", "（", "）", "(", ")", "-"):
        expression = Replace(
            expression,
            Value(token, output_field=TextField()),
            Value("", output_field=TextField()),
            output_field=TextField(),
        )
    return expression


def _inventory_demand(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    base = _without_special_products(_scoped_business(principal)).exclude(product_code="")
    data_start, data_cutoff = _bounds(base)
    facts = base
    if request["startDate"] is not None:
        facts = facts.filter(
            business_date__gte=request["startDate"], business_date__lt=request["endDate"]
        )
    product_codes = request["productCodes"]
    if product_codes:
        facts = facts.filter(product_code__in=product_codes)
    grouped = (
        facts.annotate(warehouse_key=_warehouse_key_expression())
        .values("product_code", "warehouse_key")
        .annotate(
            product_name=Max(NullIf("product_name", Value("", output_field=TextField()))),
            sales_quantity=Coalesce(
                Sum(
                    Case(
                        When(quantity__gt=0, then=F("quantity")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            absolute_quantity=Coalesce(
                Sum(Abs(F("quantity"))), Value(0), output_field=BigIntegerField()
            ),
            absolute_cost_cents=Coalesce(
                Sum(Abs(F("cost_amount_cents"))),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        .order_by("product_code", "warehouse_key")
    )
    limit = request["limit"]
    selected = list(grouped[: limit + 1])
    truncated = len(selected) > limit
    return {
        "dataStartDate": data_start,
        "dataCutoffDate": data_cutoff,
        "rows": [
            {
                "productCode": str(row["product_code"])[:200],
                "warehouseKey": str(row["warehouse_key"])[:200],
                "productName": str(row["product_name"] or row["product_code"])[:300],
                "salesQuantity": int(row["sales_quantity"] or 0),
                "absoluteQuantity": int(row["absolute_quantity"] or 0),
                "absoluteCostCents": int(row["absolute_cost_cents"] or 0),
            }
            for row in selected[:limit]
        ],
        "truncated": truncated,
    }


def _inventory_inbound_windows(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    base = _without_special_products(_scoped_business(principal)).exclude(product_code="")
    data_start, data_cutoff = _bounds(base)
    requested_as_of = request["asOfDate"] or data_cutoff
    if requested_as_of is None:
        return {
            "asOfDate": None,
            "dataStartDate": data_start,
            "dataCutoffDate": data_cutoff,
            "rows": [],
            "truncated": False,
        }
    as_of = date.fromisoformat(str(requested_as_of))
    end_exclusive = as_of + timedelta(days=1)
    start_7d = as_of - timedelta(days=6)
    start_15d = as_of - timedelta(days=14)
    start_30d = as_of - timedelta(days=29)
    start_90d = as_of - timedelta(days=89)
    facts = base.filter(
        product_code__in=request["productCodes"],
        business_date__gte=start_90d,
        business_date__lt=end_exclusive,
    )
    grouped = (
        facts.annotate(warehouse_key=_warehouse_key_expression())
        .values("product_code", "warehouse_key")
        .annotate(
            product_name=Max(NullIf("product_name", Value("", output_field=TextField()))),
            sales_7d_quantity=Coalesce(
                Sum(Case(
                    When(business_date__gte=start_7d, quantity__gt=0, then=F("quantity")),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )),
                Value(0),
                output_field=BigIntegerField(),
            ),
            sales_15d_quantity=Coalesce(
                Sum(Case(
                    When(business_date__gte=start_15d, quantity__gt=0, then=F("quantity")),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )),
                Value(0),
                output_field=BigIntegerField(),
            ),
            sales_30d_quantity=Coalesce(
                Sum(Case(
                    When(business_date__gte=start_30d, quantity__gt=0, then=F("quantity")),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )),
                Value(0),
                output_field=BigIntegerField(),
            ),
            sales_90d_quantity=Coalesce(
                Sum(Case(
                    When(quantity__gt=0, then=F("quantity")),
                    default=Value(0),
                    output_field=BigIntegerField(),
                )),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        .order_by("product_code", "warehouse_key")
    )
    limit = request["limit"]
    selected = list(grouped[: limit + 1])
    return {
        "asOfDate": as_of.isoformat(),
        "dataStartDate": data_start,
        "dataCutoffDate": data_cutoff,
        "rows": [
            {
                "productCode": str(row["product_code"])[:200],
                "warehouseKey": str(row["warehouse_key"])[:200],
                "productName": str(row["product_name"] or row["product_code"])[:300],
                "sales7dQuantity": int(row["sales_7d_quantity"] or 0),
                "sales15dQuantity": int(row["sales_15d_quantity"] or 0),
                "sales30dQuantity": int(row["sales_30d_quantity"] or 0),
                "sales90dQuantity": int(row["sales_90d_quantity"] or 0),
            }
            for row in selected[:limit]
        ],
        "truncated": len(selected) > limit,
    }


def _outlet_filter(outlets: list[dict[str, object]]) -> Q:
    selected = Q(pk__in=[])
    for outlet in outlets:
        platform = str(outlet["platform"])
        shop = str(outlet["shopName"])
        aliases = (
            SELF_OPERATED_JD_ALIASES
            if platform == "京东" and shop == SELF_OPERATED_JD_SHOP
            else (shop,)
        )
        match = Q(platform_key=platform, shop_key__in=aliases)
        if outlet.get("channel") is not None:
            match &= Q(channel_key=outlet["channel"])
        selected |= match
    return selected


def _canonical_outlet(platform: object, shop: object, channel: object) -> dict[str, object]:
    platform_text = str(platform or "").strip()
    shop_text = str(shop or "").strip()
    channel_text = str(channel or "").strip()
    if platform_text == "京东" and (
        shop_text in SELF_OPERATED_JD_ALIASES or channel_text in SELF_OPERATED_JD_CHANNELS
    ):
        shop_text = SELF_OPERATED_JD_SHOP
    return {
        "platform": platform_text[:100],
        "shopName": (shop_text or channel_text or platform_text or "未分类")[:100],
        "channel": channel_text[:100] if channel_text else None,
    }


def _product_performance(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    scoped = _without_special_products(_scoped_business(principal)).exclude(product_code="")
    selected = scoped
    if request["platforms"]:
        selected = selected.filter(platform_key__in=request["platforms"])
    if request["outlets"]:
        selected = selected.filter(_outlet_filter(request["outlets"]))
    if request["productCodes"]:
        selected = selected.filter(product_code__in=request["productCodes"])
    data_start, data_cutoff = _bounds(selected)
    facts = selected.filter(
        business_date__gte=request["startDate"], business_date__lt=request["endDate"]
    )
    grouped = (
        facts.values("product_code")
        .annotate(
            product_name=Max(NullIf("product_name", Value("", output_field=TextField()))),
            specification_value=Max(NullIf("specification", Value("", output_field=TextField()))),
            category_value=Max(NullIf("category", Value("", output_field=TextField()))),
            supplier_value=Max(NullIf("supplier", Value("", output_field=TextField()))),
            net_quantity=Coalesce(Sum("quantity"), Value(0), output_field=BigIntegerField()),
            gross_sales_cents=Coalesce(
                Sum(
                    Case(
                        When(allocated_amount_cents__gt=0, then=F("allocated_amount_cents")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            refund_amount_cents=Coalesce(
                Sum(
                    Case(
                        When(allocated_amount_cents__lt=0, then=-F("allocated_amount_cents")),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            net_sales_cents=Coalesce(
                Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField()
            ),
            cost_cents=Coalesce(
                Sum("cost_amount_cents"), Value(0), output_field=BigIntegerField()
            ),
            fee_cents=Coalesce(
                Sum("fee_allocation_cents"), Value(0), output_field=BigIntegerField()
            ),
            gross_profit_cents=Coalesce(
                Sum("gross_profit_cents"), Value(0), output_field=BigIntegerField()
            ),
            absolute_quantity=Coalesce(
                Sum(Abs(F("quantity"))), Value(0), output_field=BigIntegerField()
            ),
            absolute_cost_cents=Coalesce(
                Sum(Abs(F("cost_amount_cents"))),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        .order_by("product_code")
    )
    limit = request["limit"]
    selected_rows = list(grouped[: limit + 1])
    truncated = len(selected_rows) > limit
    returned_rows = selected_rows[:limit]
    returned_codes = [str(row["product_code"]) for row in returned_rows]

    outlets_by_product: dict[str, list[dict[str, object]]] = {}
    if returned_codes:
        outlet_rows = (
            facts.filter(product_code__in=returned_codes)
            .values("product_code", "platform", "shop_name", "channel")
            .distinct()
            .order_by("product_code", "platform", "shop_name", "channel")
        )
        for row in outlet_rows:
            product_code = str(row["product_code"])
            outlet = _canonical_outlet(row["platform"], row["shop_name"], row["channel"])
            values = outlets_by_product.setdefault(product_code, [])
            identity = (outlet["platform"], outlet["shopName"], outlet["channel"])
            if not any(
                (item["platform"], item["shopName"], item["channel"]) == identity
                for item in values
            ):
                if len(values) >= 50:
                    truncated = True
                else:
                    values.append(outlet)

    facet = scoped.filter(
        business_date__gte=request["startDate"], business_date__lt=request["endDate"]
    )
    if request["productCodes"]:
        facet = facet.filter(product_code__in=request["productCodes"])
    facet_rows = list(
        facet.values("platform", "shop_name", "channel")
        .distinct()
        .order_by("platform", "shop_name", "channel")[:2001]
    )
    if len(facet_rows) > 2000:
        truncated = True
    options_by_key: dict[tuple[object, object, object], dict[str, object]] = {}
    for row in facet_rows[:2000]:
        outlet = _canonical_outlet(row["platform"], row["shop_name"], row["channel"])
        options_by_key[(outlet["platform"], outlet["shopName"], outlet["channel"])] = outlet
    outlet_options = sorted(
        options_by_key.values(),
        key=lambda item: (
            binary_text_key(item["platform"]),
            binary_text_key(item["shopName"]),
            binary_text_key(item["channel"] or ""),
        ),
    )
    if len(outlet_options) > 500:
        truncated = True
        outlet_options = outlet_options[:500]

    return {
        "dataStartDate": data_start,
        "dataCutoffDate": data_cutoff,
        "latestBatch": _latest_batch(principal),
        "rows": [
            {
                "productCode": str(row["product_code"])[:200],
                "productName": str(row["product_name"] or row["product_code"])[:300],
                "specification": str(row["specification_value"] or "")[:300],
                "category": str(row["category_value"] or "未分类")[:200],
                "supplier": str(row["supplier_value"] or "")[:300],
                "netQuantity": int(row["net_quantity"] or 0),
                "grossSalesCents": int(row["gross_sales_cents"] or 0),
                "refundAmountCents": int(row["refund_amount_cents"] or 0),
                "netSalesCents": int(row["net_sales_cents"] or 0),
                "costCents": int(row["cost_cents"] or 0),
                "feeCents": int(row["fee_cents"] or 0),
                "grossProfitCents": int(row["gross_profit_cents"] or 0),
                "absoluteQuantity": int(row["absolute_quantity"] or 0),
                "absoluteCostCents": int(row["absolute_cost_cents"] or 0),
                "outlets": outlets_by_product.get(str(row["product_code"]), []),
            }
            for row in returned_rows
        ],
        "outletOptions": outlet_options,
        "truncated": truncated,
    }


def _customer_service_products(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    facts = _scoped_business(principal).exclude(online_spec_code="").annotate(
        category_value=Trim("category")
    )
    if request["onlineSpecCodes"]:
        facts = facts.filter(online_spec_code__in=request["onlineSpecCodes"])
    if request["categories"]:
        facts = facts.filter(category_value__in=request["categories"])
    grouped = (
        facts.values("online_spec_code", "product_code", "category_value")
        .annotate(latest_at=Max("sales_time"))
        .order_by("-latest_at", "online_spec_code", "product_code", "category_value")
    )
    limit = request["limit"]
    rows = list(grouped[: limit + 1])
    return {
        "rows": [
            {
                "onlineSpecCode": str(row["online_spec_code"])[:200],
                "productCode": str(row["product_code"])[:200],
                "category": str(row["category_value"] or "")[:200],
                "latestAt": str(row["latest_at"])[:80] if row["latest_at"] else None,
            }
            for row in rows[:limit]
        ],
        "truncated": len(rows) > limit,
    }


def _channel_matches_platform() -> Q:
    query = Q(channel_key=F("platform_key"))
    for separator in ("-", "—", "–", ":", "："):
        query |= Q(channel_key__startswith=Concat(
            F("platform_key"),
            Value(separator, output_field=TextField()),
            output_field=TextField(),
        ))
    return query


def _resolved_match_query(items: list[dict[str, object]], *, include_product: bool) -> Q:
    selected = Q(pk__in=[])
    for item in items:
        match = Q(
            platform_key=item["platform"],
            _consumer_shop=item["rawShopName"],
        )
        if item["rawChannel"] is not None:
            match &= Q(channel_key=item["rawChannel"])
        if include_product:
            match &= Q(_consumer_sales_code=item["salesProductCode"])
        selected |= match
    return selected


def _netshop_base(principal: Principal):
    return _scoped_business(principal).annotate(
        _consumer_shop=Trim("shop_name"),
        _consumer_sales_code=Coalesce(
            NullIf("online_spec_code", Value("", output_field=TextField())),
            F("product_code"),
            output_field=TextField(),
        ),
    ).filter(_channel_matches_platform())


def _netshop_product_metrics(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    identities = request["identities"]
    scopes = request["outletScopes"]
    allowed_channels = request["allowedChannels"]
    platforms = {
        str(item["platform"]) for item in [*identities, *scopes]
    }
    platform = next(iter(platforms)) if len(platforms) == 1 else ""
    if allowed_channels == []:
        return {"dataCutoffDate": None, "platform": platform, "rows": []}

    base = _netshop_base(principal)
    if allowed_channels is not None:
        base = base.filter(channel_key__in=allowed_channels)
    data_cutoff = None
    if scopes:
        cutoff = base.filter(_resolved_match_query(scopes, include_product=False)).aggregate(
            value=Max("business_date")
        )["value"]
        data_cutoff = cutoff.isoformat() if cutoff is not None else None
    if request["startDate"] is None or not identities:
        return {"dataCutoffDate": data_cutoff, "platform": platform, "rows": []}

    facts = _without_special_products(base).filter(
        business_date__gte=request["startDate"],
        business_date__lt=request["endDate"],
    ).filter(_resolved_match_query(identities, include_product=True))
    grouped = facts.values(
        "platform_key", "_consumer_shop", "channel_key", "_consumer_sales_code"
    ).annotate(
        gross_sales_cents=Coalesce(
            Sum(Case(
                When(allocated_amount_cents__gt=0, then=F("allocated_amount_cents")),
                default=Value(0), output_field=BigIntegerField(),
            )), Value(0), output_field=BigIntegerField(),
        ),
        refund_amount_cents=Coalesce(
            Sum(Case(
                When(allocated_amount_cents__lt=0, then=-F("allocated_amount_cents")),
                default=Value(0), output_field=BigIntegerField(),
            )), Value(0), output_field=BigIntegerField(),
        ),
        net_sales_cents=Coalesce(
            Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField()
        ),
        gross_profit_cents=Coalesce(
            Sum("gross_profit_cents"), Value(0), output_field=BigIntegerField()
        ),
        absolute_quantity=Coalesce(
            Sum(Abs(F("quantity"))), Value(0), output_field=BigIntegerField()
        ),
        absolute_cost_cents=Coalesce(
            Sum(Abs(F("cost_amount_cents"))), Value(0), output_field=BigIntegerField()
        ),
    )
    by_raw: dict[tuple[str, str, str | None, str], list[dict[str, object]]] = {}
    for item in identities:
        key = (
            str(item["platform"]),
            str(item["rawShopName"]),
            None if item["rawChannel"] is None else str(item["rawChannel"]),
            str(item["salesProductCode"]),
        )
        by_raw.setdefault(key, []).append(item)
    combined: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in grouped:
        raw_platform = str(row["platform_key"])
        raw_shop = str(row["_consumer_shop"])
        raw_channel = str(row["channel_key"])
        product_code = str(row["_consumer_sales_code"])
        matches = [
            *by_raw.get((raw_platform, raw_shop, raw_channel, product_code), []),
            *by_raw.get((raw_platform, raw_shop, None, product_code), []),
        ]
        seen_targets: set[tuple[str, str, str]] = set()
        for item in matches:
            key = (str(item["platform"]), str(item["canonicalShopName"]), product_code)
            if key in seen_targets:
                continue
            seen_targets.add(key)
            target = combined.setdefault(
                key,
                {
                    "platform": key[0],
                    "shopName": key[1],
                    "salesProductCode": key[2],
                    "grossSalesCents": 0,
                    "refundAmountCents": 0,
                    "netSalesCents": 0,
                    "grossProfitCents": 0,
                    "absoluteQuantity": 0,
                    "absoluteCostCents": 0,
                },
            )
            for source, destination in (
                ("gross_sales_cents", "grossSalesCents"),
                ("refund_amount_cents", "refundAmountCents"),
                ("net_sales_cents", "netSalesCents"),
                ("gross_profit_cents", "grossProfitCents"),
                ("absolute_quantity", "absoluteQuantity"),
                ("absolute_cost_cents", "absoluteCostCents"),
            ):
                target[destination] = int(target[destination]) + int(row[source] or 0)
    rows = sorted(
        combined.values(),
        key=lambda item: (
            binary_text_key(item["platform"]),
            binary_text_key(item["shopName"]),
            binary_text_key(item["salesProductCode"]),
        ),
    )
    return {"dataCutoffDate": data_cutoff, "platform": platform, "rows": rows}


def _market_product_metrics(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    product_codes = request["productCodes"]
    base = _scoped_business(principal).filter(product_code__in=product_codes)
    owned = set(base.values_list("product_code", flat=True).distinct())
    sales = base
    if request["startDate"] is not None:
        sales = sales.filter(
            business_date__gte=request["startDate"], business_date__lt=request["endDate"]
        )
    amounts = {
        str(row["product_code"]): int(row["own_sales_cents"] or 0)
        for row in sales.values("product_code").annotate(
            own_sales_cents=Coalesce(
                Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField()
            )
        )
    }
    return {
        "rows": [
            {
                "productCode": product_code,
                "owned": product_code in owned,
                "ownSalesCents": amounts.get(product_code, 0),
            }
            for product_code in product_codes
        ]
    }


def _bounded(value: object, maximum: int) -> str:
    return str(value or "")[:maximum]


def _order_search(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    query = str(request["query"])
    match = (
        Q(order_no__icontains=query)
        | Q(online_order_no__icontains=query)
        | Q(product_code__icontains=query)
        | Q(online_spec_code__icontains=query)
        | Q(product_name__icontains=query)
        | Q(shop_name__icontains=query)
        | Q(platform__icontains=query)
    )
    grouped = (
        _scoped_business(principal)
        .filter(match)
        .values("order_no", "online_order_no")
        .annotate(
            platform_value=Max("platform"),
            shop_value=Max("shop_name"),
            latest_ship_time=Max("ship_time"),
            sample_product_name=Min(Substr(Coalesce(
                "product_name", Value("", output_field=TextField()), output_field=TextField()
            ), 1, 120)),
            product_name_count=Count("product_name", distinct=True, filter=~Q(product_name="")),
            net_sales_cents=Coalesce(
                Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField()
            ),
        )
        .order_by("-latest_ship_time", "order_no", "online_order_no")
    )
    total = grouped.count()
    page = request["page"]
    page_size = request["pageSize"]
    rows = list(grouped[(page - 1) * page_size : page * page_size])
    items: list[dict[str, object]] = []
    for row in rows:
        result_id = str(row["order_no"] or row["online_order_no"] or "未编号订单")
        platform = str(row["platform_value"] or "")
        shop = str(row["shop_value"] or "")
        sample = str(row["sample_product_name"] or "")
        count = int(row["product_name_count"] or 0)
        items.append(
            {
                "id": _bounded(result_id, 200),
                "title": _bounded(result_id, 300),
                "subtitle": _bounded(platform + (f" · {shop}" if shop else ""), 500),
                "detail": _bounded(sample + (f" 等 {count} 个商品" if count > 1 else ""), 1_000),
                "updatedAt": _bounded(row["latest_ship_time"], 80),
                "amountCents": int(row["net_sales_cents"] or 0),
            }
        )
    return {
        "items": items,
        "total": total,
        "truncated": page * page_size < total,
    }


def _import_batch_search(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    if principal.role not in {"operator", "admin"} or principal.scope is not None:
        raise SalesAccessError("当前账号无权检索销售导入批次")
    query = str(request["query"])
    batches = SalesImportBatch.objects.filter(
        Q(id__icontains=query)
        | Q(file_name__icontains=query)
        | Q(source__icontains=query)
        | Q(status__icontains=query)
    ).order_by("-created_at", "id")
    total = batches.count()
    page = request["page"]
    page_size = request["pageSize"]
    rows = batches[(page - 1) * page_size : page * page_size]
    return {
        "items": [
            {
                "id": _bounded(row.id, 200),
                "source": _bounded(row.source, 200),
                "fileName": _bounded(row.file_name, 500),
                "status": _bounded(row.status, 50),
                "rowCount": int(row.row_count),
                "createdAt": _bounded(row.created_at, 80),
                "completedAt": _bounded(row.completed_at, 80) if row.completed_at else None,
            }
            for row in rows
        ],
        "total": total,
        "truncated": page * page_size < total,
    }


def _category_options(principal: Principal, request: dict[str, object]) -> dict[str, object]:
    limit = request["limit"]
    rows = list(
        _scoped_business(principal)
        .annotate(category_value=Trim("category"))
        .exclude(category_value="")
        .values_list("category_value", flat=True)
        .distinct()
        .order_by("category_value")[: limit + 1]
    )
    return {"categories": rows[:limit], "truncated": len(rows) > limit}


OperationHandler = Callable[[Principal, dict[str, object]], dict[str, object]]
HANDLERS: dict[str, OperationHandler] = {
    "freshness": _freshness,
    "summary": _summary,
    "inventory_demand": _inventory_demand,
    "inventory_inbound_windows": _inventory_inbound_windows,
    "product_performance": _product_performance,
    "customer_service_products": _customer_service_products,
    "netshop_product_metrics": _netshop_product_metrics,
    "market_product_metrics": _market_product_metrics,
    "order_search": _order_search,
    "import_batch_search": _import_batch_search,
    "category_options": _category_options,
}


def execute_consumer_query(
    principal: Principal, request: dict[str, object]
) -> dict[str, object]:
    operation = str(request["operation"])
    if operation == "analysis_records":
        from .analysis import read_page
        return read_page(principal, request)
    return HANDLERS[operation](principal, request)
