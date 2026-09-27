from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from django.db import connection
from django.db.models import Min, Q, Sum, TextField, Value
from django.db.models.functions import Abs, Coalesce, Collate, NullIf

from .errors import FinanceApiError
from .models import FinanceLine, FinanceMonth, FinanceTarget
from .serialization import target_payload


METRIC_KEYS = {
    "gross_sales",
    "return_amount",
    "net_sales",
    "net_cost",
    "gross_profit",
    "gross_margin",
    "selling_expense_total",
    "small_profit",
    "small_margin",
    "other_expense_total",
    "profit",
    "profit_margin",
}
MAX_FINANCE_ANALYSIS_MONTHS = 24
MAX_FINANCE_MONTH_OPTIONS = 120
MAX_FINANCE_SHOP_OPTIONS = 500
MAX_FINANCE_PLATFORM_OPTIONS = 100
MAX_PERIOD_TARGET_SCAN = 20_000
MONTH_RE = re.compile(r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])$")
FINANCE_ZH_COLLATION = "zh-Hans-CN-x-icu"


class FinanceDimensionFilterError(FinanceApiError):
    def __init__(
        self,
        message: str,
        *,
        invalid_platforms: list[str],
        invalid_shops: list[dict[str, str]],
        incompatible_shops: list[dict[str, str]],
    ) -> None:
        super().__init__(
            message,
            status=400,
            code="finance_dimension_filter_out_of_scope",
            payload={
                "invalidPlatforms": invalid_platforms,
                "invalidShops": [
                    {"platform": item["platform"], "name": item["name"]}
                    for item in invalid_shops
                ],
                "incompatibleShops": [
                    {"platform": item["platform"], "name": item["name"]}
                    for item in incompatible_shops
                ],
            },
        )


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def _shift_month(month: str, delta: int) -> str:
    year, month_number = (int(item) for item in month.split("-"))
    ordinal = year * 12 + month_number - 1 + delta
    shifted_year, shifted_month = divmod(ordinal, 12)
    return f"{shifted_year:04d}-{shifted_month + 1:02d}"


def _selectable_shop(name: str) -> bool:
    compact = re.sub(r"[\s　]+", "", name)
    if re.match(r"^分销[-—]", compact):
        return False
    return re.match(r"^(?:[1-9]|1[0-2])月(?:项目费率)?$", compact) is None


def finance_shop_key(platform: str, name: str) -> str:
    return json.dumps([platform, name], ensure_ascii=False, separators=(",", ":"))


def _parse_shop_key(value: str) -> dict[str, str] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, list) or len(parsed) != 2 or not all(isinstance(item, str) for item in parsed):
        return None
    platform, name = (item.strip() for item in parsed)
    if not platform or not name or len(platform) > 100 or len(name) > 100:
        return None
    return {"key": finance_shop_key(platform, name), "platform": platform, "name": name}


def _normalized_names(values: list[str] | None, label: str) -> list[str]:
    normalized = list(dict.fromkeys(item.strip() for item in (values or []) if item.strip()))
    if len(normalized) > 50 or any(len(item) > 100 for item in normalized):
        raise FinanceApiError(f"{label}筛选项数量或长度超出限制。")
    return normalized


def _resolve_filters(
    all_options: list[dict[str, str]],
    requested_platforms: list[str],
    requested_shop_keys: list[str],
) -> tuple[list[str], list[dict[str, str]]]:
    raw_keys = list(dict.fromkeys(item.strip() for item in requested_shop_keys if item.strip()))
    if len(raw_keys) > 50 or any(len(item) > 240 for item in raw_keys):
        raise FinanceApiError("店铺筛选项数量或长度超出限制。")
    shops = [_parse_shop_key(item) for item in raw_keys]
    if any(item is None for item in shops):
        raise FinanceApiError("店铺筛选必须使用平台与店铺组成的稳定复合标识。")
    shop_values = [item for item in shops if item is not None]
    known_shops = {item["key"] for item in all_options}
    known_platforms = {item["platform"] for item in all_options}
    invalid_shops = [item for item in shop_values if item["key"] not in known_shops]
    invalid_platforms = [item for item in requested_platforms if item not in known_platforms]
    incompatible = [
        item for item in shop_values
        if requested_platforms and item["platform"] not in requested_platforms
    ]
    if invalid_shops or invalid_platforms or incompatible:
        details: list[str] = []
        if invalid_platforms:
            details.append(f"平台：{'、'.join(invalid_platforms)}")
        if invalid_shops:
            details.append("店铺：" + "、".join(f"{item['platform']} · {item['name']}" for item in invalid_shops))
        if incompatible:
            details.append("店铺不属于所选平台：" + "、".join(f"{item['platform']} · {item['name']}" for item in incompatible))
        raise FinanceDimensionFilterError(
            f"筛选项不存在或不属于当前财务期间（{'；'.join(details)}）。",
            invalid_platforms=invalid_platforms,
            invalid_shops=invalid_shops,
            incompatible_shops=incompatible,
        )
    return requested_platforms, shop_values


def _empty_metrics() -> dict[str, int]:
    return {
        "grossSalesCents": 0,
        "returnAmountCents": 0,
        "netSalesCents": 0,
        "netCostCents": 0,
        "grossProfitCents": 0,
        "grossMarginBps": 0,
        "returnRateBps": 0,
        "sellingExpenseCents": 0,
        "smallProfitCents": 0,
        "smallMarginBps": 0,
        "otherExpenseCents": 0,
        "profitCents": 0,
        "profitMarginBps": 0,
        "promotionExpenseCents": 0,
        "promotionFeeRatioBps": 0,
    }


def _metrics(rows: list[dict[str, object]], promotion: int = 0) -> dict[str, int]:
    values = {
        str(row["metric_key"]): {
            "amount": int(row.get("amount_cents") or 0),
            "rate": int(row.get("rate_bps") or 0),
        }
        for row in rows
    }
    amount = lambda key: values.get(key, {}).get("amount", 0)
    rate = lambda key: values.get(key, {}).get("rate", 0)
    net_sales = int(amount("net_sales"))
    gross_sales = int(amount("gross_sales"))
    returns = int(amount("return_amount"))
    gross_profit = int(amount("gross_profit"))
    small_profit = int(amount("small_profit"))
    other_expense = int(amount("other_expense_total"))
    profit = int(amount("profit")) if "profit" in values else small_profit - other_expense
    return {
        "grossSalesCents": gross_sales,
        "returnAmountCents": returns,
        "netSalesCents": net_sales,
        "netCostCents": int(amount("net_cost")),
        "grossProfitCents": gross_profit,
        "grossMarginBps": int(rate("gross_margin")) or (0 if net_sales == 0 else _js_round(gross_profit / net_sales * 10_000)),
        "returnRateBps": 0 if gross_sales == 0 else _js_round(abs(returns) / abs(gross_sales) * 10_000),
        "sellingExpenseCents": int(amount("selling_expense_total")),
        "smallProfitCents": small_profit,
        "smallMarginBps": int(rate("small_margin")) or (0 if net_sales == 0 else _js_round(small_profit / net_sales * 10_000)),
        "otherExpenseCents": other_expense,
        "profitCents": profit,
        "profitMarginBps": int(rate("profit_margin")) or (0 if net_sales == 0 else _js_round(profit / net_sales * 10_000)),
        "promotionExpenseCents": promotion,
        "promotionFeeRatioBps": 0 if net_sales == 0 else _js_round(promotion / net_sales * 10_000),
    }


def _sum_metrics(items: list[dict[str, int]]) -> dict[str, int]:
    result = _empty_metrics()
    additive = (
        "grossSalesCents", "returnAmountCents", "netSalesCents", "netCostCents",
        "grossProfitCents", "sellingExpenseCents", "smallProfitCents", "otherExpenseCents",
        "profitCents", "promotionExpenseCents",
    )
    for item in items:
        for key in additive:
            result[key] += item[key]
    result["grossMarginBps"] = 0 if result["netSalesCents"] == 0 else _js_round(result["grossProfitCents"] / result["netSalesCents"] * 10_000)
    result["returnRateBps"] = 0 if result["grossSalesCents"] == 0 else _js_round(abs(result["returnAmountCents"]) / abs(result["grossSalesCents"]) * 10_000)
    result["smallMarginBps"] = 0 if result["netSalesCents"] == 0 else _js_round(result["smallProfitCents"] / result["netSalesCents"] * 10_000)
    result["profitMarginBps"] = 0 if result["netSalesCents"] == 0 else _js_round(result["profitCents"] / result["netSalesCents"] * 10_000)
    result["promotionFeeRatioBps"] = 0 if result["netSalesCents"] == 0 else _js_round(result["promotionExpenseCents"] / result["netSalesCents"] * 10_000)
    return result


def _target_totals(targets: list[FinanceTarget]) -> dict[str, int]:
    sales = sum(int(item.sales_target_cents) for item in targets)

    def weighted(field: str) -> int:
        numerator = sum(int(getattr(item, field)) * max(0, int(item.sales_target_cents)) for item in targets)
        if sales > 0:
            return _js_round(numerator / sales)
        rates = [int(getattr(item, field)) for item in targets if int(getattr(item, field)) != 0]
        return _js_round(sum(rates) / len(rates)) if rates else 0

    return {
        "salesTargetCents": sales,
        "profitTargetCents": sum(int(item.profit_target_cents) for item in targets),
        "smallMarginBps": weighted("small_margin_bps"),
        "inventoryCleanupTargetCents": sum(int(item.inventory_cleanup_target_cents) for item in targets),
        "promotionFeeRatioBps": weighted("promotion_fee_ratio_bps"),
        "stagnantInventoryTargetCents": sum(int(item.stagnant_inventory_target_cents) for item in targets),
        "targetCount": len(targets),
    }


def _progress(actual: dict[str, int], target: dict[str, int]) -> dict[str, float | int | None]:
    return {
        "sales": actual["netSalesCents"] / target["salesTargetCents"] if target["salesTargetCents"] > 0 else None,
        "profit": actual["profitCents"] / target["profitTargetCents"] if target["profitTargetCents"] > 0 else None,
        "smallMarginGapBps": actual["smallMarginBps"] - target["smallMarginBps"] if target["smallMarginBps"] > 0 else None,
        "promotionFeeGapBps": actual["promotionFeeRatioBps"] - target["promotionFeeRatioBps"] if target["promotionFeeRatioBps"] > 0 else None,
    }


def _change_rate(current: int, comparison: int | None) -> float | None:
    if comparison is None or comparison == 0:
        return None
    return (current - comparison) / abs(comparison)


def _pair_filter(pairs: list[dict[str, str]]) -> Q:
    query = Q(pk__in=[])
    for item in pairs:
        platform_query = (
            Q(group_name__in=["", "未分组"])
            if item["platform"] == "未分组"
            else Q(group_name=item["platform"])
        )
        query |= platform_query & Q(scope_name=item["name"])
    return query


def _platform_filter(queryset, platforms: list[str]):
    if not platforms:
        return queryset
    query = Q(group_name__in=[item for item in platforms if item != "未分组"])
    if "未分组" in platforms:
        query |= Q(group_name__in=["", "未分组"])
    return queryset.filter(query)


def _dimension_queryset(
    base,
    platforms: list[str],
    shops: list[dict[str, str]],
    has_filter: bool,
):
    queryset = base.filter(scope_type="shop" if has_filter else "business")
    if shops:
        queryset = queryset.filter(_pair_filter(shops))
    if platforms:
        queryset = _platform_filter(queryset, platforms)
    return queryset


def _binary_key(value: str) -> bytes:
    return value.encode("utf-8")


def _database_order(field: str, *, chinese: bool = False):
    """Match the legacy SQLite/Intl.Collator ordering explicitly on PostgreSQL."""

    if connection.vendor != "postgresql":
        return field
    return Collate(field, FINANCE_ZH_COLLATION if chinese else "C")


def get_finance_analysis(
    *,
    requested_months: list[str] | None = None,
    all_months: bool = False,
    fallback_to_latest: bool = False,
    platform_names: list[str] | None = None,
    shop_keys: list[str] | None = None,
) -> dict[str, object]:
    requested = sorted(set(requested_months or []))
    if len(requested) > MAX_FINANCE_ANALYSIS_MONTHS:
        raise FinanceApiError(f"单次最多分析 {MAX_FINANCE_ANALYSIS_MONTHS} 个财务月份。")
    if any(not MONTH_RE.fullmatch(item) for item in requested):
        raise FinanceApiError("财务月份必须使用 YYYY-MM。")

    month_total = FinanceMonth.objects.filter(status="completed").count()
    latest_rows = list(
        FinanceMonth.objects.filter(status="completed")
        .order_by("-month")[: MAX_FINANCE_MONTH_OPTIONS + 1]
    )
    month_by_key = {item.month: item for item in latest_rows[:MAX_FINANCE_MONTH_OPTIONS]}
    if requested:
        for item in FinanceMonth.objects.filter(status="completed", month__in=requested):
            month_by_key[item.month] = item
    months = [month_by_key[key] for key in sorted(month_by_key)]
    if not months:
        if requested and not fallback_to_latest:
            raise FinanceApiError(f"以下财务月份尚未导入：{'、'.join(requested)}")
        return {
            "hasData": False,
            "months": [],
            "selectedMonth": None,
            "selectedMonths": [],
            "anomalies": [],
            "expenses": [],
            "shops": [],
            "timeline": [],
            "filters": {"platforms": [], "shops": []},
        }
    month_keys = [item.month for item in months]
    missing = [item for item in requested if item not in month_keys]
    fallback_applied = bool(fallback_to_latest and requested and missing)
    if missing and not fallback_applied:
        raise FinanceApiError(f"以下财务月份尚未导入：{'、'.join(missing)}")
    selected_months = (
        [month_keys[-1]]
        if fallback_applied
        else month_keys[-MAX_FINANCE_ANALYSIS_MONTHS:]
        if all_months
        else requested
        if requested
        else [month_keys[-1]]
    )
    selected_month = selected_months[-1]
    first_selected = selected_months[0]
    previous_months = [
        _shift_month(first_selected, index - len(selected_months))
        for index in range(len(selected_months))
    ]
    year_ago_months = [_shift_month(item, -12) for item in selected_months]
    previous_available = all(item in month_keys for item in previous_months)
    year_ago_available = all(item in month_keys for item in year_ago_months)
    selected_year = selected_month[:4]
    year_months = [item for item in month_keys if item.startswith(f"{selected_year}-") and item <= selected_month]
    timeline_months = (
        [item for item in month_keys if item <= selected_month][-MAX_FINANCE_ANALYSIS_MONTHS:]
        if len(selected_months) == 1
        else selected_months
    )
    query_months = sorted(set(
        selected_months
        + (previous_months if previous_available else [])
        + (year_ago_months if year_ago_available else [])
        + year_months
        + timeline_months
    ))

    requested_platforms = _normalized_names(platform_names, "平台")
    raw_shop_keys = shop_keys or []
    parsed_requested = [_parse_shop_key(item) for item in raw_shop_keys]
    if any(item is None for item in parsed_requested):
        raise FinanceApiError("店铺筛选必须使用平台与店铺组成的稳定复合标识。")
    requested_pairs = [item for item in parsed_requested if item is not None]

    option_base = FinanceLine.objects.filter(
        section="summary", scope_type="shop", month__in=query_months
    ).exclude(scope_name="").annotate(
        option_platform=Coalesce(
            NullIf("group_name", Value("", output_field=TextField())),
            Value("未分组", output_field=TextField()),
            output_field=TextField(),
        )
    )
    pair_values = option_base.values_list("option_platform", "scope_name").distinct()
    shop_option_total = pair_values.count()
    first_pairs = [
        (platform, name)
        for platform, name in pair_values.order_by(
            _database_order("option_platform"), _database_order("scope_name")
        )[:MAX_FINANCE_SHOP_OPTIONS]
    ]
    platform_query = option_base.values_list("option_platform", flat=True).distinct()
    platform_total = platform_query.count()
    platform_values = [
        item
        for item in platform_query.order_by(
            _database_order("option_platform")
        )[:MAX_FINANCE_PLATFORM_OPTIONS]
    ]
    requested_identities = {(item["platform"], item["name"]) for item in requested_pairs}
    requested_pair_rows: list[tuple[str, str]] = []
    if requested_pairs:
        requested_pair_rows = [
            (platform, name)
            for platform, name in option_base.filter(_pair_filter(requested_pairs))
            .values_list("option_platform", "scope_name").distinct()
        ]
    requested_platform_rows: list[str] = []
    if requested_platforms:
        requested_platform_rows = [
            item
            for item in _platform_filter(option_base, requested_platforms)
            .values_list("option_platform", flat=True).distinct()
        ]
    candidate_pairs = list(dict.fromkeys(
        first_pairs + [item for item in requested_pair_rows if item in requested_identities]
    ))
    known_pairs = []
    if candidate_pairs:
        known_pairs = list(
            option_base.filter(_pair_filter([
                {"platform": platform, "name": name}
                for platform, name in candidate_pairs
            ]))
            .values_list("option_platform", "scope_name")
            .distinct()
            .order_by(
                _database_order("option_platform", chinese=True),
                _database_order("scope_name", chinese=True),
            )
        )
    known_options = [
        {"key": finance_shop_key(platform, name), "platform": platform, "name": name}
        for platform, name in known_pairs
        if _selectable_shop(name)
    ]
    # Requested platforms beyond the first option page must still validate.
    for index, platform in enumerate(requested_platform_rows):
        known_options.append({
            "key": finance_shop_key(platform, f"__platform_{index}"),
            "platform": platform,
            "name": f"__platform_{index}",
        })
    platform_filter, shop_pairs = _resolve_filters(
        known_options, requested_platforms, raw_shop_keys
    )
    has_filter = bool(platform_filter or shop_pairs)

    summary_base = _dimension_queryset(
        FinanceLine.objects.filter(
            section="summary", metric_key__in=METRIC_KEYS, month__in=query_months
        ),
        platform_filter,
        shop_pairs,
        has_filter,
    )
    summary_rows = list(
        summary_base.values("month", "metric_key")
        .annotate(amount_cents=Sum("amount_cents", default=0))
        .order_by("month", "metric_key")
    )
    for row in summary_rows:
        row.update({"rate_bps": None, "scope_type": "business", "scope_name": "", "group_name": ""})
    promotion_rows = list(
        _dimension_queryset(
            FinanceLine.objects.filter(
                section="kingdee",
                subject_name__startswith="销售费用_推广费用_",
                month__in=query_months,
            ),
            platform_filter,
            shop_pairs,
            has_filter,
        )
        .values("month")
        .annotate(amount_cents=Sum("amount_cents", default=0))
    )

    ranking_base = FinanceLine.objects.filter(
        section="summary",
        scope_type="shop",
        metric_key="net_sales",
        month__in=selected_months,
    )
    if shop_pairs:
        ranking_base = ranking_base.filter(_pair_filter(shop_pairs))
    if platform_filter:
        ranking_base = _platform_filter(ranking_base, platform_filter)
    ranking_rows = list(
        ranking_base.values("group_name", "scope_name")
        .annotate(net_sales_cents=Sum("amount_cents", default=0))
        .order_by("-net_sales_cents", "group_name", "scope_name")[: MAX_FINANCE_SHOP_OPTIONS + 1]
    )
    all_shop_options = [
        {
            "key": finance_shop_key(str(row["group_name"] or "未分组"), str(row["scope_name"])),
            "platform": str(row["group_name"] or "未分组"),
            "name": str(row["scope_name"]),
        }
        for row in ranking_rows[:MAX_FINANCE_SHOP_OPTIONS]
        if _selectable_shop(str(row["scope_name"]))
    ]

    ranked_pairs = [(item["platform"], item["name"]) for item in all_shop_options]
    ranked_summary: list[dict[str, object]] = []
    ranked_promotions: list[dict[str, object]] = []
    if ranked_pairs:
        pair_dicts = [{"platform": platform, "name": name} for platform, name in ranked_pairs]
        ranked_summary = list(
            FinanceLine.objects.filter(
                section="summary",
                scope_type="shop",
                metric_key__in=METRIC_KEYS,
                month__in=selected_months,
            )
            .filter(_pair_filter(pair_dicts))
            .values(
                "month", "metric_key", "subject_name", "scope_type", "scope_name",
                "group_name", "amount_cents", "rate_bps", "sort_order",
            )
            .order_by("month", "group_name", "scope_name", "sort_order")
        )
        ranked_promotions = list(
            FinanceLine.objects.filter(
                section="kingdee",
                scope_type="shop",
                subject_name__startswith="销售费用_推广费用_",
                month__in=selected_months,
            )
            .filter(_pair_filter(pair_dicts))
            .values("month", "group_name", "scope_name")
            .annotate(amount_cents=Sum("amount_cents", default=0))
        )

    business_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in summary_rows:
        business_rows[str(row["month"])].append(row)
    business_promotions = {
        str(row["month"]): int(row["amount_cents"] or 0) for row in promotion_rows
    }
    shop_rows: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in ranked_summary:
        shop_rows[(str(row["month"]), str(row["group_name"] or "未分组"), str(row["scope_name"]))].append(row)
    shop_promotions = {
        (str(row["month"]), str(row["group_name"] or "未分组"), str(row["scope_name"])): int(row["amount_cents"] or 0)
        for row in ranked_promotions
    }
    actual_by_month = {
        month: _metrics(business_rows.get(month, []), business_promotions.get(month, 0))
        for month in query_months
    }
    shop_actuals = {
        key: _metrics(rows, shop_promotions.get(key, 0)) for key, rows in shop_rows.items()
    }
    sum_period = lambda period: _sum_metrics([actual_by_month.get(item, _empty_metrics()) for item in period])
    current = sum_period(selected_months)
    previous = sum_period(previous_months) if previous_available else None
    year_ago = sum_period(year_ago_months) if year_ago_available else None
    year_to_date = sum_period(year_months)

    selected_years = sorted({item[:4] for item in selected_months})
    period_queryset = FinanceTarget.objects.filter(
        Q(period_type="month", period_key__in=selected_months)
        | Q(period_type="year", period_key__in=selected_years)
    )
    if period_queryset.count() > MAX_PERIOD_TARGET_SCAN:
        raise FinanceApiError(
            "财务期间目标数量超出分析上限",
            code="service_unavailable",
            status=503,
        )
    period_candidates = list(period_queryset)
    target_shop_names = {item.shop_name for item in period_candidates if not item.platform and item.shop_name}
    legacy_platforms: dict[str, set[str]] = defaultdict(set)
    if target_shop_names:
        for platform, name in FinanceLine.objects.filter(
            scope_type="shop", scope_name__in=target_shop_names
        ).values_list("group_name", "scope_name").distinct():
            legacy_platforms[name].add(platform or "未分组")

    def resolved_target_platform(target: FinanceTarget) -> str | None:
        if target.platform:
            return target.platform
        values = legacy_platforms.get(target.shop_name, set())
        return next(iter(values)) if len(values) == 1 else None

    compatible = [item for item in period_candidates if resolved_target_platform(item) is not None]
    filtered_targets = [
        item for item in compatible
        if (not platform_filter or resolved_target_platform(item) in platform_filter)
        and (
            not shop_pairs
            or any(
                pair["name"] == item.shop_name
                and pair["platform"] == resolved_target_platform(item)
                for pair in shop_pairs
            )
        )
    ]
    filtered_targets.sort(key=lambda item: (_binary_key(item.platform), _binary_key(item.shop_name), _binary_key(item.category)))
    filtered_targets.sort(key=lambda item: item.period_key, reverse=True)
    filtered_targets.sort(key=lambda item: item.period_type)
    target_count = len(filtered_targets)
    target_details = filtered_targets[:1_000]
    month_targets = _target_totals([item for item in filtered_targets if item.period_type == "month"])
    year_targets = _target_totals([item for item in filtered_targets if item.period_type == "year"])
    legacy_gap = sum(1 for item in period_candidates if not item.platform and resolved_target_platform(item) is None)
    project_total = FinanceTarget.objects.filter(period_type="project").count()
    project_targets = list(FinanceTarget.objects.filter(period_type="project").order_by("-updated_at", "-id")[:100])

    expense_base = _dimension_queryset(
        FinanceLine.objects.filter(
            section="kingdee", is_total=False, month__in=selected_months
        ),
        platform_filter,
        shop_pairs,
        has_filter,
    )
    expense_subject_total = expense_base.values("subject_name").distinct().count()
    expense_names = [
        str(row["subject_name"])
        for row in expense_base.values("subject_name")
        .annotate(magnitude=Abs(Sum("amount_cents", default=0)))
        .order_by("-magnitude", "subject_name")[:501]
    ]
    expense_rows = []
    if expense_names:
        expense_rows = list(
            _dimension_queryset(
                FinanceLine.objects.filter(
                    section="kingdee",
                    is_total=False,
                    month__in=query_months,
                    subject_name__in=expense_names[:500],
                ),
                platform_filter,
                shop_pairs,
                has_filter,
            )
            .values("month", "subject_name")
            .annotate(amount_cents=Sum("amount_cents", default=0), sort_order=Min("sort_order"))
            .order_by("month", "sort_order")
        )

    def expense_totals(period: list[str]) -> dict[str, dict[str, int]]:
        period_set = set(period)
        result: dict[str, dict[str, int]] = {}
        for row in expense_rows:
            if row["month"] not in period_set:
                continue
            name = str(row["subject_name"])
            current_item = result.setdefault(name, {"amount": 0, "sortOrder": int(row["sort_order"] or 0)})
            current_item["amount"] += int(row["amount_cents"] or 0)
            current_item["sortOrder"] = min(current_item["sortOrder"], int(row["sort_order"] or 0))
        return result

    current_expenses = expense_totals(selected_months)
    previous_expenses = expense_totals(previous_months) if previous_available else None
    year_ago_expenses = expense_totals(year_ago_months) if year_ago_available else None
    # JavaScript Set preserves first-seen order and Array.sort is stable. Keep
    # the same order here so equal-amount/zero expense rows do not drift across
    # runtimes merely because Python set iteration is intentionally unordered.
    all_expense_names = list(dict.fromkeys([
        *current_expenses,
        *(previous_expenses or {}),
        *(year_ago_expenses or {}),
    ]))
    expenses: list[dict[str, object]] = []
    for name in all_expense_names:
        current_amount = current_expenses.get(name, {}).get("amount", 0)
        previous_amount = previous_expenses.get(name, {}).get("amount", 0) if previous_expenses is not None else None
        year_ago_amount = year_ago_expenses.get(name, {}).get("amount", 0) if year_ago_expenses is not None else None
        mom_rate = _change_rate(current_amount, previous_amount)
        expenses.append({
            "name": name,
            "current": current_amount,
            "previous": previous_amount,
            "yearAgo": year_ago_amount,
            "sortOrder": current_expenses.get(name, {}).get(
                "sortOrder",
                (previous_expenses or {}).get(name, {}).get(
                    "sortOrder", (year_ago_expenses or {}).get(name, {}).get("sortOrder", 0)
                ),
            ),
            "feeRateBps": 0 if current["netSalesCents"] == 0 else _js_round(current_amount / current["netSalesCents"] * 10_000),
            "yearAgoFeeRateBps": (
                _js_round(int(year_ago_amount) / year_ago["netSalesCents"] * 10_000)
                if year_ago_expenses is not None and year_ago is not None and year_ago["netSalesCents"] != 0
                else None
            ),
            "momRate": mom_rate,
            "yoyRate": _change_rate(current_amount, year_ago_amount),
            "abnormal": previous_amount is not None and abs(current_amount - previous_amount) >= 100_000 and abs(mom_rate or 0) >= 0.3,
        })
    expenses = [
        item for item in expenses
        if item["current"] != 0 or item["previous"] is not None or item["yearAgo"] is not None
    ]
    expenses.sort(key=lambda item: -abs(int(item["current"])))

    shops: list[dict[str, object]] = []
    for shop in all_shop_options:
        actual = _sum_metrics([
            shop_actuals[(month, shop["platform"], shop["name"])]
            for month in selected_months
            if (month, shop["platform"], shop["name"]) in shop_actuals
        ])
        target_rows = [
            item for item in target_details
            if item.period_type == "month"
            and item.period_key in selected_months
            and item.shop_name == shop["name"]
            and (item.platform == shop["platform"] or item.platform == "")
        ]
        target = _target_totals(target_rows)
        if actual["netSalesCents"] == 0 and actual["grossSalesCents"] == 0 and target["targetCount"] == 0:
            continue
        shops.append({
            "name": shop["name"],
            "key": shop["key"],
            "groupName": shop["platform"],
            "manager": next((item.manager for item in target_rows if item.manager), ""),
            "actual": actual,
            "target": target,
            "progress": _progress(actual, target),
        })
    shops.sort(key=lambda item: -int(item["actual"]["netSalesCents"]))  # type: ignore[index]
    shops = shops[:MAX_FINANCE_SHOP_OPTIONS]

    month_progress = _progress(current, month_targets)
    anomalies: list[dict[str, str]] = []
    if current["profitCents"] < 0:
        anomalies.append({"level": "critical", "title": "所选期间利润为负", "detail": "建议优先检查销售费用、退货和异常成本科目。"})
    if month_targets["smallMarginBps"] > 0 and current["smallMarginBps"] < month_targets["smallMarginBps"]:
        anomalies.append({
            "level": "warning",
            "title": "小毛利率低于目标",
            "detail": f"低于目标 {(month_targets['smallMarginBps'] - current['smallMarginBps']) / 100:.1f} 个百分点。",
        })
    if month_progress["sales"] is not None and month_progress["sales"] < 0.9:
        anomalies.append({"level": "warning", "title": "销售目标完成度偏低", "detail": f"所选期间销售目标完成 {month_progress['sales'] * 100:.1f}%。"})
    for item in [expense for expense in expenses if expense["abnormal"] and expense["momRate"] is not None][:5]:
        mom = float(item["momRate"])
        title_name = re.sub(r"^销售费用_", "", str(item["name"]))
        anomalies.append({
            "level": "warning" if mom > 0 else "info",
            "title": f"{title_name}环比{'上升' if mom > 0 else '下降'}",
            "detail": f"较上月{'增加' if mom > 0 else '减少'} {abs(mom) * 100:.1f}%。",
        })
    if not anomalies:
        anomalies.append({"level": "info", "title": "暂未发现明显异常", "detail": "当前月份利润、目标进度与费用波动均在规则阈值内。"})

    selected_meta = next(item for item in months if item.month == selected_month)
    period_label = selected_month if len(selected_months) == 1 else f"{selected_months[0]} 至 {selected_months[-1]}（{len(selected_months)}个月）"
    option_map = {
        item["key"]: item
        for item in known_options
        if not item["name"].startswith("__platform_")
    }
    # known_pairs was already ordered with the same ICU zh-CN collation used
    # by JavaScript localeCompare("zh-CN"). Preserve that database order.
    shop_options = list(option_map.values())
    platforms = platform_values
    return {
        "hasData": True,
        "months": [
            {
                "month": item.month,
                "fileName": item.source_file_name,
                "importedAt": item.imported_at,
                "shopCount": int(item.shop_count),
                "subjectCount": int(item.subject_count),
            }
            for item in months
        ],
        "monthPagination": {
            "total": month_total,
            "returned": len(months),
            "truncated": len(latest_rows) > MAX_FINANCE_MONTH_OPTIONS,
        },
        "selectedMonth": selected_month,
        "selectedMonths": selected_months,
        "periodLabel": period_label,
        "previousMonth": previous_months[0] if previous_available and len(previous_months) == 1 else None,
        "previousMonths": previous_months if previous_available else [],
        "yearAgoMonth": year_ago_months[0] if year_ago_available and len(year_ago_months) == 1 else None,
        "yearAgoMonths": year_ago_months if year_ago_available else [],
        "current": current,
        "previous": previous,
        "yearAgo": year_ago,
        "yearToDate": year_to_date,
        "timeline": [{"month": month, **actual_by_month.get(month, _empty_metrics())} for month in timeline_months],
        "targets": {
            "month": month_targets,
            "year": year_targets,
            "projects": [target_payload(item) for item in project_targets],
            "projectPagination": {
                "total": project_total,
                "returned": len(project_targets),
                "truncated": len(project_targets) < project_total,
            },
            "periodPagination": {
                "total": target_count,
                "returned": len(target_details),
                "truncated": target_count > 1_000,
            },
            "legacyCompatibility": {
                "excluded": legacy_gap,
                "reason": "旧目标缺少平台，仅在全部财务数据中店铺名称只属于一个平台时兼容；跨平台同名目标已停止参与 KPI。",
            },
        },
        "progress": {"month": month_progress, "year": _progress(year_to_date, year_targets)},
        "expenses": expenses,
        "expensePagination": {
            "total": expense_subject_total,
            "returned": len(expenses),
            "truncated": len(expense_names) > 500,
        },
        "shops": shops,
        "shopPagination": {
            "total": shop_option_total,
            "returned": len(shops),
            "truncated": len(ranking_rows) > MAX_FINANCE_SHOP_OPTIONS,
        },
        "anomalies": anomalies,
        "filters": {
            "platforms": platforms,
            "shops": shop_options,
            "pagination": {
                "platforms": {
                    "total": platform_total,
                    "returned": len(platforms),
                    "truncated": len(platforms) < platform_total,
                },
                "shops": {
                    "total": shop_option_total,
                    "returned": len(shop_options),
                    "truncated": len(shop_options) < shop_option_total,
                },
            },
        },
        "selection": {
            "allMonths": bool(all_months),
            "truncated": bool(all_months and len(month_keys) > MAX_FINANCE_ANALYSIS_MONTHS),
            "availableMonthCount": month_total,
            "months": selected_months,
            "requestedMonths": requested,
            "fallbackApplied": fallback_applied,
            "platforms": platform_filter,
            "shops": [item["key"] for item in shop_pairs],
        },
        "sync": {
            "dataCutoffMonth": selected_month,
            "sourceFileName": selected_meta.source_file_name,
            "importedAt": selected_meta.imported_at,
        },
    }
