from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Sequence

from django.db.models import BigIntegerField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth, TruncWeek

from .auth import Principal
from .query import (
    SalesRequestError,
    ISO_DATE_RE,
    add_days,
    add_years,
    binary_order,
    binary_text_key,
    category_aggregates,
    day_count,
    sales_queryset,
)


GRANULARITIES = {"day", "week", "month"}
SORT_KEYS = {
    "netSalesCents", "shareRate", "netQuantity", "refundRate", "weekOverWeekRate",
    "yearOverYearRate", "positiveQuantity", "returnQuantity", "refundAmountCents",
    "grossProfitCents", "grossMarginRate", "productCount",
}
MAX_RANGE_DAYS = 366
MAX_PAGE_SIZE = 100
OPTION_LIMIT = 200
TREND_CATEGORY_LIMIT = 8
DETAIL_TREND_PERIOD_LIMIT = 24


def _validate_range(start_date: str, end_date: str) -> None:
    if not ISO_DATE_RE.fullmatch(start_date) or not ISO_DATE_RE.fullmatch(end_date):
        raise SalesRequestError("品类分析日期必须使用 YYYY-MM-DD")
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as error:
        raise SalesRequestError("品类分析日期必须使用 YYYY-MM-DD") from error
    if start > end:
        raise SalesRequestError("品类分析开始日期不能晚于结束日期")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise SalesRequestError(f"品类分析统计周期最长支持 {MAX_RANGE_DAYS} 天")


def _comparison_periods(start_date: str, end_date: str) -> dict[str, object]:
    return {
        "weekOverWeek": {
            "current": {"startDate": add_days(end_date, -6), "endDate": end_date},
            "previous": {"startDate": add_days(end_date, -13), "endDate": add_days(end_date, -7)},
        },
        "yearAgo": {"startDate": add_years(start_date, -1), "endDate": add_years(end_date, -1)},
    }


def _base(params: dict[str, Any], principal: Principal, start_date: str | None = None, end_date: str | None = None, *, clear_dimensions: bool = False):
    queryset, scope_mode = sales_queryset(
        start_date=start_date or params["startDate"],
        end_exclusive=add_days(end_date or params["endDate"], 1),
        product_codes=params["productCodes"],
        categories=[] if clear_dimensions else params.get("categories", []),
        channels=[] if clear_dimensions else params.get("channels", []),
        platforms=[] if clear_dimensions else params.get("platforms", []),
        outlets=[] if clear_dimensions else params.get("outlets", []),
        principal=principal,
        category_contract=True,
    )
    if params.get("brands"):
        from .models import ErpProductMaster
        queryset = queryset.filter(product_code__in=ErpProductMaster.objects.filter(brand__in=params["brands"]).values("product_code"))
    return queryset, scope_mode


def _period_filter(start_date: str, end_date: str) -> Q:
    return Q(business_date__gte=start_date, business_date__lt=add_days(end_date, 1))


def _grouped_with_comparisons(
    params: dict[str, Any], principal: Principal, comparisons: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    periods = [
        {"startDate": params["startDate"], "endDate": params["endDate"]},
        comparisons["weekOverWeek"]["current"],
        comparisons["weekOverWeek"]["previous"],
        comparisons["yearAgo"],
    ]
    start_date = min(period["startDate"] for period in periods)
    end_date = max(period["endDate"] for period in periods)
    queryset, scope_mode = _base(params, principal, start_date, end_date)
    included = Q()
    for period in periods:
        included |= _period_filter(period["startDate"], period["endDate"])
    current_filter = _period_filter(params["startDate"], params["endDate"])
    queryset = queryset.filter(included)
    rows = list(
        queryset.values("resolved_category")
        .annotate(
            **category_aggregates(filter_q=current_filter),
            current_week_net_sales_cents=Coalesce(
                Sum(
                    "allocated_amount_cents",
                    filter=_period_filter(
                        comparisons["weekOverWeek"]["current"]["startDate"],
                        comparisons["weekOverWeek"]["current"]["endDate"],
                    ),
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            previous_week_net_sales_cents=Coalesce(
                Sum(
                    "allocated_amount_cents",
                    filter=_period_filter(
                        comparisons["weekOverWeek"]["previous"]["startDate"],
                        comparisons["weekOverWeek"]["previous"]["endDate"],
                    ),
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
            year_ago_net_sales_cents=Coalesce(
                Sum(
                    "allocated_amount_cents",
                    filter=_period_filter(
                        comparisons["yearAgo"]["startDate"],
                        comparisons["yearAgo"]["endDate"],
                    ),
                ),
                Value(0),
                output_field=BigIntegerField(),
            ),
        )
        .filter(line_count__gt=0)
        .order_by(binary_order("resolved_category"))
    )
    return rows, scope_mode


def _rate(current: int, previous: int) -> float | None:
    return None if previous == 0 else (current - previous) / abs(previous)


def _serialize_category(row: dict[str, Any], total_net: int) -> dict[str, Any]:
    gross = int(row.get("gross_sales_cents") or 0)
    refund = int(row.get("refund_amount_cents") or 0)
    net = int(row.get("net_sales_cents") or 0)
    cost = int(row.get("cost_amount_cents") or 0)
    current_week = int(row.get("current_week_net_sales_cents") or 0)
    previous_week = int(row.get("previous_week_net_sales_cents") or 0)
    year_ago = int(row.get("year_ago_net_sales_cents") or 0)
    return {
        "category": row["resolved_category"],
        "grossSalesCents": gross,
        "refundAmountCents": refund,
        "netSalesCents": net,
        "costAmountCents": cost,
        "shareRate": 0 if total_net == 0 else net / total_net,
        "positiveQuantity": int(row.get("positive_quantity") or 0),
        "returnQuantity": int(row.get("return_quantity") or 0),
        "netQuantity": int(row.get("net_quantity") or 0),
        "refundRate": 0 if gross == 0 else refund / gross,
        "grossProfitCents": int(row.get("gross_profit_cents") or 0),
        "grossMarginRate": 0 if net == 0 else (net - cost) / net,
        "productCount": int(row.get("product_count") or 0),
        "lineCount": int(row.get("line_count") or 0),
        "currentWeekNetSalesCents": current_week,
        "previousWeekNetSalesCents": previous_week,
        "yearAgoNetSalesCents": year_ago,
        "weekOverWeekRate": _rate(current_week, previous_week),
        "yearOverYearRate": _rate(net, year_ago),
    }


def _empty_summary() -> dict[str, int | float]:
    return {
        "grossSalesCents": 0, "refundAmountCents": 0, "netSalesCents": 0,
        "costAmountCents": 0, "positiveQuantity": 0, "returnQuantity": 0,
        "netQuantity": 0, "grossProfitCents": 0, "grossMarginRate": 0,
        "productCount": 0, "lineCount": 0, "categoryCount": 0,
    }


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, int | float]:
    if not rows:
        return _empty_summary()
    key_map = {
        "grossSalesCents": "gross_sales_cents", "refundAmountCents": "refund_amount_cents",
        "netSalesCents": "net_sales_cents", "costAmountCents": "cost_amount_cents",
        "positiveQuantity": "positive_quantity", "returnQuantity": "return_quantity",
        "netQuantity": "net_quantity", "grossProfitCents": "gross_profit_cents",
        "productCount": "product_count", "lineCount": "line_count",
    }
    result: dict[str, int | float] = {target: sum(int(row.get(source) or 0) for row in rows) for target, source in key_map.items()}
    result["categoryCount"] = len(rows)
    net = int(result["netSalesCents"])
    result["grossMarginRate"] = 0 if net == 0 else (net - int(result["costAmountCents"])) / net
    return result


def _period_annotation(queryset, granularity: str):
    if granularity == "month":
        return queryset.annotate(period_key=TruncMonth("business_date"))
    if granularity == "week":
        return queryset.annotate(period_key=TruncWeek("business_date"))
    return queryset.annotate(period_key=F("business_date"))


def _trend_rows(params: dict[str, Any], principal: Principal, categories: Sequence[str], *, recent_periods: int | None = None) -> list[dict[str, Any]]:
    if not categories:
        return []
    queryset, _ = _base(params, principal)
    queryset = _period_annotation(queryset.filter(resolved_category__in=categories), params["granularity"])
    aggregate = category_aggregates()
    keep = {key: aggregate[key] for key in ["net_sales_cents", "gross_profit_cents", "positive_quantity", "return_quantity", "refund_amount_cents"]}
    grouped = queryset.values("period_key", "resolved_category").annotate(**keep).order_by(
        "period_key", "-net_sales_cents", binary_order("resolved_category")
    )
    rows = list(grouped if recent_periods is not None else grouped[:3000])
    for row in rows:
        value = row["period_key"]
        normalized = value.isoformat() if hasattr(value, "isoformat") else str(value)
        row["period_key"] = normalized[:7] if params["granularity"] == "month" else normalized[:10]
    if recent_periods is not None:
        periods = sorted({row["period_key"] for row in rows}, reverse=True)[:recent_periods]
        allowed = set(periods)
        rows = [row for row in rows if row["period_key"] in allowed]
        rows.sort(key=lambda row: (binary_text_key(row["resolved_category"]), row["period_key"]))
    return rows


def _category_trend(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    points = [{"period": row["period_key"], "netSalesCents": int(row["net_sales_cents"] or 0)} for row in rows]
    change = None if len(points) < 2 or points[0]["netSalesCents"] == 0 else (points[-1]["netSalesCents"] - points[0]["netSalesCents"]) / abs(points[0]["netSalesCents"])
    direction = "insufficient" if change is None else "up" if change > 0 else "down" if change < 0 else "flat"
    return {"points": points, "changeRate": change, "direction": direction}


def _filter_options(params: dict[str, Any], principal: Principal) -> dict[str, Any]:
    queryset, _ = _base(params, principal, clear_dimensions=True)
    categories = list(queryset.values_list("resolved_category", flat=True).distinct().order_by(binary_order("resolved_category")))
    channels = list(queryset.values_list("channel", flat=True).distinct().order_by(binary_order("channel")))
    channels = [value.strip() or "未分类" for value in channels]
    platforms = list(queryset.values_list("platform_key", flat=True).distinct().order_by(binary_order("platform_key")))
    outlets = list(queryset.values("platform_key", "shop_key").distinct().order_by(binary_order("shop_key"), binary_order("platform_key")))
    return {
        "categories": categories[:OPTION_LIMIT],
        "channels": list(dict.fromkeys(channels))[:OPTION_LIMIT],
        "platforms": platforms[:OPTION_LIMIT],
        "outlets": [{"key": f"{row['platform_key']}\x1f{row['shop_key']}", "platform": row["platform_key"], "name": row["shop_key"]} for row in outlets[:OPTION_LIMIT]],
        "totals": {
            "categories": len(categories), "channels": len(set(channels)),
            "platforms": len(platforms), "outlets": len(outlets),
        },
        "truncated": any(len(items) > OPTION_LIMIT for items in [categories, set(channels), platforms, outlets]),
        "limit": OPTION_LIMIT,
    }


def get_category_analysis(params: dict[str, Any], principal: Principal) -> dict[str, Any]:
    _validate_range(params["startDate"], params["endDate"])
    if params["level"] != 1:
        raise SalesRequestError("当前商品主数据仅提供 1 层品类，level 只能为 1")
    if params["granularity"] not in GRANULARITIES:
        raise SalesRequestError("granularity 必须是 day, week, month 之一")
    if params["sortBy"] not in SORT_KEYS:
        raise SalesRequestError("sortBy 无效")
    comparisons = _comparison_periods(params["startDate"], params["endDate"])
    current_rows, scope_mode = _grouped_with_comparisons(params, principal, comparisons)
    totals = _summary(current_rows)
    total_net = int(totals["netSalesCents"])
    metrics = [_serialize_category(row, total_net) for row in current_rows]
    ranking = sorted(metrics, key=lambda item: (-item["netSalesCents"], binary_text_key(item["category"])))[:10]
    ranking = [{"rank": index + 1, **item} for index, item in enumerate(ranking)]
    sort_key_map = {
        "netSalesCents": "netSalesCents", "shareRate": "netSalesCents", "netQuantity": "netQuantity",
        "refundRate": "refundRate", "weekOverWeekRate": "weekOverWeekRate", "yearOverYearRate": "yearOverYearRate",
        "positiveQuantity": "positiveQuantity", "returnQuantity": "returnQuantity", "refundAmountCents": "refundAmountCents",
        "grossProfitCents": "grossProfitCents", "grossMarginRate": "grossMarginRate", "productCount": "productCount",
    }
    key = sort_key_map[params["sortBy"]]
    reverse = params["direction"] == "desc"
    if reverse:
        ordered = sorted(metrics, key=lambda item: (item[key] is None, -(item[key] or 0), binary_text_key(item["category"])))
    else:
        # SQLite's ascending order places NULL before numeric values.
        ordered = sorted(metrics, key=lambda item: (item[key] is not None, item[key] or 0, binary_text_key(item["category"])))
    offset = (params["page"] - 1) * params["pageSize"]
    details = ordered[offset : offset + params["pageSize"]]
    detail_trends = _trend_rows(params, principal, [item["category"] for item in details], recent_periods=DETAIL_TREND_PERIOD_LIMIT)
    trend_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in detail_trends:
        trend_by_category[row["resolved_category"]].append(row)
    details = [{**item, "trend": _category_trend(trend_by_category[item["category"]])} for item in details]
    top_categories = [item["category"] for item in ranking[:TREND_CATEGORY_LIMIT]]
    trend_rows = _trend_rows(params, principal, top_categories)
    uncategorized = next((item for item in metrics if item["category"] == "未分类"), None)
    uncategorized_net = int(uncategorized["netSalesCents"]) if uncategorized else 0
    uncategorized_products = int(uncategorized["productCount"]) if uncategorized else 0
    data_cutoff = max((str(row.get("latest_business_date") or "") for row in current_rows), default="") or None
    scope_payload = {"mode": "unrestricted", "warehouses": None, "channels": None, "platforms": None}
    if principal.scope is not None:
        scope_payload = {"mode": scope_mode, **principal.scope}
    ranked_net = sum(int(item["netSalesCents"]) for item in ranking)
    return {
        "range": {"startDate": params["startDate"], "endDate": params["endDate"], "endExclusive": add_days(params["endDate"], 1), "timezone": "Asia/Shanghai"},
        "comparisonPeriods": comparisons,
        "dataCutoffDate": data_cutoff,
        "categoryHierarchy": {
            "currentLevel": 1, "levels": [{"level": 1, "key": "category", "label": "品类"}], "supportsDrilldown": False,
            "source": {"primary": "erp_product_master.category", "fallback": "sales_order_lines.category", "joinKey": "product_code", "unmatchedLabel": "未分类"},
        },
        "filtersApplied": {
            "level": 1, "categories": params["categories"], "channels": params["channels"], "platforms": params["platforms"],
            "outlets": params["outlets"], "productQueries": params["productQueries"], "productCodes": params["productCodes"], "dataScope": scope_payload,
            **({"brands": params["brands"], "brandSource": "erp_product_master.brand (current)", "unmappedBrandPolicy": "excluded"} if params.get("brands") else {}),
        },
        "summary": totals,
        "uncategorized": {
            "category": "未分类", "productCount": uncategorized_products, "netSalesCents": uncategorized_net,
            "shareRate": 0 if total_net == 0 else uncategorized_net / total_net,
            "visible": uncategorized_products > 0 or uncategorized_net != 0,
        },
        "structure": {
            "items": ranking, "otherNetSalesCents": total_net - ranked_net,
            "otherShareRate": 0 if total_net == 0 else (total_net - ranked_net) / total_net,
            "contributionRateTotal": 0 if total_net == 0 else 1,
        },
        "ranking": ranking,
        "trend": {
            "granularity": params["granularity"], "categoryLimit": TREND_CATEGORY_LIMIT, "returned": len(trend_rows),
            "truncated": len(trend_rows) >= 3000,
            "items": [{
                "period": row["period_key"], "category": row["resolved_category"], "netSalesCents": int(row["net_sales_cents"] or 0),
                "grossProfitCents": int(row["gross_profit_cents"] or 0), "positiveQuantity": int(row["positive_quantity"] or 0),
                "returnQuantity": int(row["return_quantity"] or 0), "refundAmountCents": int(row["refund_amount_cents"] or 0),
            } for row in trend_rows],
        },
        "details": {
            "items": details,
            "pagination": {"page": params["page"], "pageSize": params["pageSize"], "total": len(metrics), "returned": len(details), "truncated": offset + len(details) < len(metrics)},
            "sort": {"by": params["sortBy"], "direction": params["direction"]},
            "trend": {"granularity": params["granularity"], "periodLimit": DETAIL_TREND_PERIOD_LIMIT},
        },
        "filterOptions": _filter_options(params, principal),
    }


def get_category_detail(params: dict[str, Any], principal: Principal) -> dict[str, Any]:
    _validate_range(params["startDate"], params["endDate"])
    category = params["category"].strip()
    if not category or len(category) > 100:
        raise SalesRequestError("category 不能为空且不能超过 100 字")
    detail_params = {**params, "categories": [category]}
    queryset, _ = _base(detail_params, principal)
    grouped = queryset.values("platform_key", "shop_key").annotate(**category_aggregates()).order_by(
        "-net_sales_cents", binary_order("platform_key"), binary_order("shop_key")
    )
    fetched = list(grouped[:501])
    rows = fetched[:500]
    if len(fetched) > 500:
        total_count = grouped.count()
        total_net = int(queryset.aggregate(total=Coalesce(Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField()))["total"] or 0)
    else:
        total_count = len(rows)
        total_net = sum(int(row["net_sales_cents"] or 0) for row in rows)
    platform_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        gross = int(row["gross_sales_cents"] or 0)
        refund = int(row["refund_amount_cents"] or 0)
        net = int(row["net_sales_cents"] or 0)
        cost = int(row["cost_amount_cents"] or 0)
        shop = {
            "shop": row["shop_key"], "grossSalesCents": gross, "refundAmountCents": refund, "netSalesCents": net,
            "costAmountCents": cost, "shareRate": 0 if total_net == 0 else net / total_net,
            "positiveQuantity": int(row["positive_quantity"] or 0), "returnQuantity": int(row["return_quantity"] or 0),
            "netQuantity": int(row["net_quantity"] or 0), "refundRate": 0 if gross == 0 else refund / gross,
            "grossProfitCents": int(row["gross_profit_cents"] or 0), "grossMarginRate": 0 if net == 0 else (net - cost) / net,
            "lineCount": int(row["line_count"] or 0),
        }
        platform = platform_map.setdefault(row["platform_key"], {
            "platform": row["platform_key"], "grossSalesCents": 0, "refundAmountCents": 0, "netSalesCents": 0,
            "costAmountCents": 0, "positiveQuantity": 0, "returnQuantity": 0, "netQuantity": 0,
            "grossProfitCents": 0, "lineCount": 0, "shops": [],
        })
        for key in ["grossSalesCents", "refundAmountCents", "netSalesCents", "costAmountCents", "positiveQuantity", "returnQuantity", "netQuantity", "grossProfitCents", "lineCount"]:
            platform[key] += shop[key]
        platform["shops"].append(shop)
    platforms: list[dict[str, Any]] = []
    for platform in platform_map.values():
        platform["shareRate"] = 0 if total_net == 0 else platform["netSalesCents"] / total_net
        platform["refundRate"] = 0 if platform["grossSalesCents"] == 0 else platform["refundAmountCents"] / platform["grossSalesCents"]
        platform["grossMarginRate"] = 0 if platform["netSalesCents"] == 0 else (platform["netSalesCents"] - platform["costAmountCents"]) / platform["netSalesCents"]
        platform["shopCount"] = len(platform["shops"])
        platforms.append(platform)
    platforms.sort(key=lambda item: (-item["netSalesCents"], binary_text_key(item["platform"])))
    return {
        "range": {"startDate": params["startDate"], "endDate": params["endDate"], "endExclusive": add_days(params["endDate"], 1), "timezone": "Asia/Shanghai"},
        "category": category,
        "totals": {"netSalesCents": total_net, "platformCount": len(platforms), "shopCount": len(rows)},
        "platforms": platforms,
        "pagination": {"total": total_count, "returned": len(rows), "truncated": len(rows) < total_count, "limit": 500},
    }
