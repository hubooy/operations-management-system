from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import BigIntegerField, CharField, F, Max, Q, Sum, TextField, Value
from django.db.models.functions import Coalesce, Concat, NullIf

from .models import ErpProductMaster, SalesOrderLine
from .auth import Principal
from .query import (
    SalesRequestError,
    ISO_DATE_RE,
    add_days,
    add_years,
    binary_order,
    binary_text_key,
    day_count,
    latest_batch_payload,
    metric_aggregates,
    sales_queryset,
    serialize_metric,
    _apply_principal_scope,
)


SALES_RANGES = {"today", "yesterday", "last7", "last15", "last30", "month", "quarter", "custom", "all"}
MAX_TREND_DAYS = 366
MAX_GROUP_ROWS = 500


def _add_months(value: str, months: int) -> str:
    parsed = date.fromisoformat(value)
    month_index = parsed.year * 12 + parsed.month - 1 + months
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    return date(year, month, min(parsed.day, monthrange(year, month)[1])).isoformat()


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _period_for(range_name: str, today: str) -> dict[str, str]:
    if range_name == "today":
        yesterday = add_days(today, -1)
        return {"startDate": today, "endDate": today, "previousStartDate": yesterday, "previousEndDate": yesterday}
    if range_name == "yesterday":
        yesterday = add_days(today, -1)
        return {"startDate": yesterday, "endDate": yesterday, "previousStartDate": add_days(today, -2), "previousEndDate": add_days(today, -2)}
    if range_name == "last7":
        return {"startDate": add_days(today, -6), "endDate": today, "previousStartDate": add_days(today, -13), "previousEndDate": add_days(today, -7)}
    if range_name == "last15":
        return {"startDate": add_days(today, -14), "endDate": today, "previousStartDate": add_days(today, -29), "previousEndDate": add_days(today, -15)}
    if range_name == "last30":
        return {"startDate": add_days(today, -29), "endDate": today, "previousStartDate": add_days(today, -59), "previousEndDate": add_days(today, -30)}
    parsed = date.fromisoformat(today)
    if range_name == "month":
        start = parsed.replace(day=1).isoformat()
        previous_start = _add_months(start, -1)
        if parsed.day == monthrange(parsed.year, parsed.month)[1]:
            comparison_end = add_days(start, -1)
        else:
            comparison_end = min(
                date.fromisoformat(add_days(previous_start, day_count(start, today) - 1)),
                date.fromisoformat(add_days(start, -1)),
            ).isoformat()
        return {"startDate": start, "endDate": today, "previousStartDate": previous_start, "previousEndDate": comparison_end}
    quarter_month = ((parsed.month - 1) // 3) * 3 + 1
    start = date(parsed.year, quarter_month, 1).isoformat()
    previous_start = _add_months(start, -3)
    quarter_end_month = quarter_month + 2
    quarter_end = date(parsed.year, quarter_end_month, monthrange(parsed.year, quarter_end_month)[1])
    if parsed == quarter_end:
        comparison_end = add_days(start, -1)
    else:
        comparison_end = min(
            date.fromisoformat(add_days(previous_start, day_count(start, today) - 1)),
            date.fromisoformat(add_days(start, -1)),
        ).isoformat()
    return {"startDate": start, "endDate": today, "previousStartDate": previous_start, "previousEndDate": comparison_end}


def _custom_comparison_period(start_date: date, end_date: date) -> tuple[str, str]:
    days = (end_date - start_date).days + 1
    if days == 1:
        previous = start_date - timedelta(days=1)
        return previous.isoformat(), previous.isoformat()
    if start_date.year == end_date.year and start_date.month == end_date.month:
        previous_start = _add_months(start_date.isoformat(), -1)
        if start_date.day == 1 and end_date.day == monthrange(end_date.year, end_date.month)[1]:
            parsed_previous_start = date.fromisoformat(previous_start)
            previous_end = date(
                parsed_previous_start.year,
                parsed_previous_start.month,
                monthrange(parsed_previous_start.year, parsed_previous_start.month)[1],
            ).isoformat()
        else:
            previous_end = _add_months(end_date.isoformat(), -1)
        return previous_start, previous_end
    previous_end = start_date - timedelta(days=1)
    return (previous_end - timedelta(days=days - 1)).isoformat(), previous_end.isoformat()


def _custom_period(start: str, end: str) -> dict[str, str]:
    if not ISO_DATE_RE.fullmatch(start) or not ISO_DATE_RE.fullmatch(end):
        raise SalesRequestError("自定义统计周期需要有效的起止日期（YYYY-MM-DD）")
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as error:
        raise SalesRequestError("自定义统计周期需要有效的起止日期（YYYY-MM-DD）") from error
    if start_date > end_date:
        raise SalesRequestError("自定义统计周期的开始日期不能晚于结束日期")
    days = (end_date - start_date).days + 1
    if days > 366:
        raise SalesRequestError("自定义统计周期最长支持 366 天")
    previous_start, previous_end = _custom_comparison_period(start_date, end_date)
    return {
        "startDate": start,
        "endDate": end,
        "previousStartDate": previous_start,
        "previousEndDate": previous_end,
    }


def _align_to_cutoff(range_name: str, requested: dict[str, str], cutoff: str | None) -> tuple[dict[str, str], bool]:
    if range_name not in {"last7", "last15", "last30", "month", "quarter", "custom"} or not cutoff:
        return dict(requested), False
    try:
        cutoff_date = date.fromisoformat(cutoff)
    except ValueError:
        return dict(requested), False
    if cutoff_date >= date.fromisoformat(requested["endDate"]) or cutoff_date < date.fromisoformat(requested["startDate"]):
        return dict(requested), False
    rolling = {"last7": 7, "last15": 15, "last30": 30}.get(range_name)
    start = add_days(cutoff, -(rolling - 1)) if rolling else requested["startDate"]
    period = {"startDate": start, "endDate": cutoff}
    days = day_count(start, cutoff)
    if range_name in {"month", "quarter"} and requested.get("previousStartDate"):
        period["previousStartDate"] = requested["previousStartDate"]
        period["previousEndDate"] = add_days(requested["previousStartDate"], days - 1)
    elif range_name == "custom":
        custom_period = _custom_period(start, cutoff)
        period["previousStartDate"] = custom_period["previousStartDate"]
        period["previousEndDate"] = custom_period["previousEndDate"]
    elif requested.get("previousStartDate") and requested.get("previousEndDate"):
        previous_end = add_days(start, -1)
        period["previousStartDate"] = add_days(previous_end, -(days - 1))
        period["previousEndDate"] = previous_end
    return period, True


def _base(period: dict[str, str], filters: dict[str, object]):
    queryset, _ = sales_queryset(
        start_date=period["startDate"],
        end_exclusive=add_days(period["endDate"], 1),
        product_codes=filters["productCodes"],
        categories=filters["categories"],
        platforms=filters["platforms"],
        outlets=filters["outlets"],
        principal=filters.get("principal"),
    )
    shop = filters.get("shop")
    # The public Worker contract treats the structured outlet filter as the
    # successor to the legacy shop scalar.  When both are present, outlets win
    # instead of intersecting the two filters.
    if shop and not filters["outlets"]:
        queryset = queryset.filter(report_shop_key=shop, shop_key=shop)
    return queryset


def _period_filter(period: dict[str, str]) -> Q:
    return Q(
        business_date__gte=period["startDate"],
        business_date__lt=add_days(period["endDate"], 1),
    )


def _combined_base(periods: list[dict[str, str]], filters: dict[str, object]):
    start_date = min(period["startDate"] for period in periods)
    end_date = max(period["endDate"] for period in periods)
    queryset = _base({"startDate": start_date, "endDate": end_date}, filters)
    included = Q()
    for period in periods:
        included |= _period_filter(period)
    return queryset.filter(included)


def _period_metrics(
    filters: dict[str, object], periods: dict[str, dict[str, str]]
) -> dict[str, dict[str, int | float]]:
    queryset = _combined_base(list(periods.values()), filters)
    aggregate_fields: dict[str, object] = {}
    for label, period in periods.items():
        for name, expression in metric_aggregates(filter_q=_period_filter(period)).items():
            aggregate_fields[f"{label}_{name}"] = expression
    row = queryset.aggregate(**aggregate_fields)
    return {
        label: serialize_metric(
            {name: row[f"{label}_{name}"] for name in metric_aggregates()}
        )
        for label in periods
    }


def _with_grouping(queryset, dimension: str):
    if dimension == "shop":
        queryset = queryset.annotate(
            group_key=Concat(F("report_platform_key"), Value("\x1f"), F("report_shop_key"), output_field=CharField()),
            group_name=F("report_shop_key"),
        )
    elif dimension == "channel":
        # Existing contract falls back to platform only when channel is exactly empty.
        queryset = queryset.annotate(
            group_name=Coalesce(
                NullIf(F("channel"), Value("")),
                NullIf(F("platform"), Value("")),
                Value("未分类"),
                output_field=TextField(),
            )
        ).annotate(group_key=F("group_name"))
    else:
        queryset = queryset.annotate(
            group_name=Coalesce(
                NullIf(F("platform"), Value("")),
                NullIf(F("channel"), Value("")),
                Value("未分类"),
                output_field=TextField(),
            )
        ).annotate(group_key=F("group_name"))
    return queryset


def _grouped_yoy(dimension: str, period: dict[str, str], year_ago: dict[str, str], filters: dict[str, object]) -> dict[str, object]:
    current_filter = _period_filter(period)
    year_filter = _period_filter(year_ago)
    queryset = _with_grouping(_combined_base([period, year_ago], filters), dimension)
    grouped = queryset.values("group_key", "group_name").annotate(
        platform_value=Max("platform", filter=current_filter),
        **metric_aggregates(filter_q=current_filter),
        year_ago_net_sales_cents=Coalesce(
            Sum("allocated_amount_cents", filter=year_filter),
            Value(0),
            output_field=BigIntegerField(),
        ),
    ).filter(line_count__gt=0).annotate(
        net_sort=F("gross_sales_cents") - F("refund_amount_cents")
    )
    rows = list(grouped.order_by("-net_sort", binary_order("group_name"), binary_order("group_key"))[: MAX_GROUP_ROWS + 1])
    truncated = len(rows) > MAX_GROUP_ROWS
    returned = rows[:MAX_GROUP_ROWS]
    if truncated:
        total = grouped.count()
        total_net = int(
            _base(period, filters).aggregate(
                total=Coalesce(Sum("allocated_amount_cents"), Value(0), output_field=BigIntegerField())
            )["total"]
            or 0
        )
    else:
        total = len(returned)
        total_net = sum(int(row["net_sort"] or 0) for row in returned)
    items: list[dict[str, object]] = []
    for row in returned:
        values = serialize_metric(row)
        items.append(
            {
                "groupKey": row["group_key"],
                "name": row["group_name"],
                "platform": row["platform_value"] or (row["group_name"] if dimension == "platform" else "未分类"),
                **values,
                "shareRate": 0 if total_net == 0 else values["netSalesCents"] / total_net,
                "yearAgoNetSalesCents": int(row["year_ago_net_sales_cents"] or 0),
            }
        )
    for item in items:
        old = int(item["yearAgoNetSalesCents"])
        item["salesYearOverYearRate"] = None if old == 0 else (int(item["netSalesCents"]) - old) / abs(old)
    return {"items": items, "pagination": {"total": total, "returned": len(items), "truncated": truncated}}


def _daily_ranges(
    periods: dict[str, dict[str, str]], filters: dict[str, object]
) -> dict[str, list[dict[str, object]]]:
    rows = (
        _combined_base(list(periods.values()), filters)
        .values("business_date")
        .annotate(**metric_aggregates())
        .order_by("business_date")
    )
    result: dict[str, list[dict[str, object]]] = {label: [] for label in periods}
    for row in rows:
        business_date = row["business_date"].isoformat()
        serialized = {"date": business_date, **serialize_metric(row)}
        for label, period in periods.items():
            if period["startDate"] <= business_date <= period["endDate"]:
                result[label].append(serialized)
    return result


def _filter_options(period: dict[str, str], product_codes: list[str], principal: Principal | None = None) -> dict[str, object]:
    filters = {"productCodes": product_codes, "categories": [], "platforms": [], "outlets": [], "shop": None, "principal": principal}
    queryset = _base(period, filters)
    shops = list(queryset.values("report_platform_key", "report_shop_key").distinct().order_by(binary_order("report_platform_key"), binary_order("report_shop_key"))[:500])
    platforms = list(queryset.values_list("report_platform_key", flat=True).distinct().order_by(binary_order("report_platform_key"))[:200])
    if product_codes:
        categories = list(queryset.values_list("resolved_category", flat=True).distinct().order_by(binary_order("resolved_category"))[:200])
    else:
        master_categories = set() if principal is not None and principal.scope is not None else set(
            ErpProductMaster.objects.annotate(category_trim=F("category")).exclude(category="").values_list("category", flat=True)
        )
        sales_categories = set(queryset.values_list("resolved_category", flat=True).distinct())
        categories = sorted(
            {category.strip() for category in master_categories | sales_categories if category.strip()},
            key=binary_text_key,
        )[:200]
    return {
        "shops": [{"key": f"{row['report_platform_key']}\x1f{row['report_shop_key']}", "name": row["report_shop_key"], "platform": row["report_platform_key"]} for row in shops],
        "platforms": platforms,
        "categories": categories,
    }


def get_sales_summary(*, range_name: str, projection: str, start_date: str | None, end_date: str | None, product_queries: list[str], product_codes: list[str], platforms: list[str], shop: str | None, outlets: list[dict[str, str]], categories: list[str], principal: Principal | None = None) -> dict[str, object]:
    if range_name not in SALES_RANGES:
        raise SalesRequestError(f"range 必须是 {', '.join(['today', 'yesterday', 'last7', 'last15', 'last30', 'month', 'quarter', 'custom', 'all'])} 之一")
    if projection not in {"full", "dashboard"}:
        raise SalesRequestError("view 必须是 dashboard。")
    filters: dict[str, object] = {
        "productCodes": product_codes,
        "categories": categories,
        "platforms": platforms,
        "shop": shop.strip() if shop and shop.strip() else None,
        "outlets": outlets,
        "principal": principal,
    }
    cutoff_queryset, _scope_mode = _apply_principal_scope(
        SalesOrderLine.objects.filter(is_business_row=True), principal
    )
    cutoff = (
        cutoff_queryset
        .order_by("-business_date")
        .values_list("business_date", flat=True)
        .first()
    )
    cutoff_date = cutoff.isoformat() if cutoff else None
    today = _today()
    if range_name == "all":
        bounded = _base({"startDate": "0001-01-01", "endDate": "9998-12-31"}, filters).order_by("business_date")
        first = bounded.values_list("business_date", flat=True).first()
        last = bounded.order_by("-business_date").values_list("business_date", flat=True).first()
        requested = {
            "startDate": first.isoformat() if first else today,
            "endDate": last.isoformat() if last else today,
        }
    elif range_name == "custom":
        requested = _custom_period(start_date or "", end_date or "")
    else:
        requested = _period_for(range_name, today)
    period, adjusted = _align_to_cutoff(range_name, requested, cutoff_date)
    year_ago = {"startDate": add_years(period["startDate"], -1), "endDate": add_years(period["endDate"], -1)}
    previous = None
    if period.get("previousStartDate") and period.get("previousEndDate"):
        previous = {"startDate": period["previousStartDate"], "endDate": period["previousEndDate"]}
    trend_truncated = day_count(period["startDate"], period["endDate"]) > MAX_TREND_DAYS
    trend_period = {
        "startDate": add_days(period["endDate"], -(MAX_TREND_DAYS - 1)) if trend_truncated else period["startDate"],
        "endDate": period["endDate"],
    }
    year_ago_trend = {"startDate": add_years(trend_period["startDate"], -1), "endDate": add_years(trend_period["endDate"], -1)}
    outlet_result = _grouped_yoy("shop", period, year_ago, filters)
    metric_periods = {"current": period, "yearAgo": year_ago}
    if previous:
        metric_periods["previous"] = previous
    metrics = _period_metrics(filters, metric_periods)
    empty_group = {"items": [], "pagination": {"total": 0, "returned": 0, "truncated": False}}
    if projection == "full":
        channel_result = _grouped_yoy("channel", period, year_ago, filters)
        platform_result = _grouped_yoy("platform", period, year_ago, filters)
        daily_periods = {"current": trend_period, "yearAgo": year_ago_trend}
        if previous:
            daily_periods["previous"] = previous
        daily_rows = _daily_ranges(daily_periods, filters)
        daily = daily_rows["current"]
        previous_daily = daily_rows.get("previous", [])
        year_ago_daily = daily_rows["yearAgo"]
        options = _filter_options(period, product_codes, principal)
    else:
        channel_result = platform_result = empty_group
        daily = _daily_ranges({"current": trend_period}, filters)["current"]
        previous_daily = []
        year_ago_daily = []
        options = {"shops": [], "platforms": [], "categories": []}
    payload: dict[str, object] = {
        "projection": projection,
        "range": range_name,
        "filters": {
            "productQueries": product_queries,
            "productCodes": product_codes,
            "platform": platforms[0] if len(platforms) == 1 else None,
            "platforms": platforms,
            "shop": filters["shop"],
            "outlets": outlets,
            "categories": categories,
        },
        "filterOptions": options,
        **period,
        "requestedStartDate": requested["startDate"],
        "requestedEndDate": requested["endDate"],
        "dataCutoffDate": cutoff_date,
        "periodAdjustedToDataCutoff": adjusted,
        "comparisonDayCount": day_count(period["startDate"], period["endDate"]),
        "current": metrics["current"],
        "yearAgo": metrics["yearAgo"],
        "yearAgoStartDate": year_ago["startDate"],
        "yearAgoEndDate": year_ago["endDate"],
        # Preserve the existing response names, including their historic channel/platform mapping.
        "channels": platform_result["items"],
        "outlets": outlet_result["items"],
        "shops": channel_result["items"],
        "platforms": platform_result["items"],
        "groupPagination": {
            "outlets": outlet_result["pagination"],
            "shops": channel_result["pagination"],
            "platforms": platform_result["pagination"],
        },
        "daily": daily,
        "previousDaily": previous_daily,
        "yearAgoDaily": year_ago_daily,
        "trendStartDate": trend_period["startDate"],
        "trendEndDate": trend_period["endDate"],
        "trendReturned": len(daily),
        "trendTruncated": trend_truncated,
        "latestBatch": latest_batch_payload(),
    }
    if previous:
        payload["previous"] = metrics["previous"]
    return payload


def dashboard_projection(payload: dict[str, object]) -> dict[str, object]:
    keys = [
        "range", "startDate", "endDate", "requestedStartDate", "requestedEndDate",
        "dataCutoffDate", "periodAdjustedToDataCutoff", "comparisonDayCount",
        "previousStartDate", "previousEndDate", "yearAgoStartDate", "yearAgoEndDate", "current",
        "previous", "yearAgo", "outlets", "daily", "latestBatch",
    ]
    return {"projection": "dashboard", **{key: payload[key] for key in keys if key in payload}}
