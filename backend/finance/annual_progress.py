"""Annual shop targets use monthly financial facts, never monthly targets."""
from collections import defaultdict
import json
import re

from django.db.models import Q, Sum

from .analysis import METRIC_KEYS, _js_round, _metrics, _selectable_shop
from .errors import FinanceApiError
from .models import FinanceLine, FinanceMonth, FinanceTarget
from .serialization import target_payload


def validate_year(year: str) -> str:
    if not re.fullmatch(r"(?:19|20|21)\d{2}", year):
        raise FinanceApiError("年份应为 YYYY")
    return year


def annual_progress(year: str, page: int, page_size: int) -> dict[str, object]:
    validate_year(year)
    months = list(FinanceMonth.objects.filter(status="completed", month__startswith=f"{year}-").order_by("month").values_list("month", flat=True))
    cutoff = months[-1] if months else None
    expected = [f"{year}-{number:02d}" for number in range(1, int(cutoff[5:]) + 1)] if cutoff else []
    facts = FinanceLine.objects.filter(month__in=months, section="summary", scope_type="shop", metric_key__in=METRIC_KEYS)
    targets = FinanceTarget.objects.filter(period_type="year", period_key=year, category="").exclude(platform="")
    pairs = set(facts.values_list("group_name", "scope_name").distinct()[:5001])
    pairs.update(targets.values_list("platform", "shop_name").distinct()[:5001])
    if len(pairs) > 5000:
        raise FinanceApiError("年度店铺数量超过读取上限", status=503, code="service_unavailable")
    pairs = sorted((platform or "未分组", name) for platform, name in pairs if _selectable_shop(name))
    pairs = sorted(set(pairs))
    total = len(pairs)
    selected = pairs[(page - 1) * page_size:page * page_size]
    grouped = defaultdict(list)
    promotions = defaultdict(int)
    goal_map = {}
    if selected:
        fact_filter, target_filter = Q(pk__in=[]), Q(pk__in=[])
        for platform, name in selected:
            groups = [platform, ""] if platform == "未分组" else [platform]
            fact_filter |= Q(group_name__in=groups, scope_name=name)
            target_filter |= Q(platform=platform, shop_name=name)
        rows = list(facts.filter(fact_filter).values("month", "group_name", "scope_name", "metric_key", "amount_cents", "rate_bps").order_by("month", "sort_order")[:20001])
        if len(rows) > 20000:
            raise FinanceApiError("年度财报明细超过读取上限", status=503, code="service_unavailable")
        for row in rows:
            grouped[(row["group_name"] or "未分组", row["scope_name"], row["month"])].append(row)
        promotion_rows = list(
            FinanceLine.objects.filter(
                month__in=months,
                section="kingdee",
                scope_type="shop",
                subject_name__startswith="销售费用_推广费用_",
            )
            .filter(fact_filter)
            .values("month", "group_name", "scope_name")
            .annotate(amount_cents=Sum("amount_cents", default=0))[:5001]
        )
        if len(promotion_rows) > 5000:
            raise FinanceApiError("年度推广费明细超过读取上限", status=503, code="service_unavailable")
        for row in promotion_rows:
            promotions[(row["group_name"] or "未分组", row["scope_name"], row["month"])] += int(row["amount_cents"] or 0)
        goal_map = {(goal.platform, goal.shop_name): goal for goal in targets.filter(target_filter)}
    items = []
    for platform, name in selected:
        available, gross_margin_available, sales, profit = [], [], 0, 0
        gross_profit, gross_margin_sales, promotion_expense, promotion_sales = 0, 0, 0, 0
        for month in months:
            rows = grouped.get((platform, name, month), [])
            keys = {row["metric_key"] for row in rows if row["amount_cents"] is not None}
            if "net_sales" not in keys or not ("profit" in keys or {"small_profit", "other_expense_total"}.issubset(keys)):
                continue
            metrics = _metrics(rows, promotions.get((platform, name, month), 0))
            available.append(month)
            sales += metrics["netSalesCents"]
            profit += metrics["profitCents"]
            promotion_sales += metrics["netSalesCents"]
            promotion_expense += metrics["promotionExpenseCents"]
            if "gross_profit" in keys or "gross_margin" in {row["metric_key"] for row in rows}:
                gross_margin_available.append(month)
                gross_margin_sales += metrics["netSalesCents"]
                gross_profit += metrics["grossProfitCents"] if "gross_profit" in keys else _js_round(metrics["netSalesCents"] * metrics["grossMarginBps"] / 10_000)
        goal = goal_map.get((platform, name))
        sales_target = goal.sales_target_cents if goal else 0
        profit_target = goal.profit_target_cents if goal else 0
        gross_margin = _js_round(gross_profit / gross_margin_sales * 10_000) if gross_margin_available and gross_margin_sales != 0 else None
        promotion_ratio = _js_round(promotion_expense / promotion_sales * 10_000) if available and promotion_sales != 0 else None
        gross_margin_target = goal.gross_margin_bps if goal else 0
        promotion_target = goal.promotion_fee_ratio_bps if goal else 0
        items.append({
            "key": json.dumps([platform, name], ensure_ascii=False, separators=(",", ":")),
            "platform": platform, "shopName": name, "manager": goal.manager if goal else "",
            "target": target_payload(goal) if goal else None,
            "netSalesCents": sales if available else None, "profitCents": profit if available else None,
            "salesProgress": sales / sales_target if available and sales_target > 0 else None,
            "profitProgress": profit / profit_target if available and profit_target > 0 else None,
            "grossMarginBps": gross_margin,
            "grossMarginGapBps": gross_margin - gross_margin_target if gross_margin is not None and gross_margin_target > 0 else None,
            "promotionFeeRatioBps": promotion_ratio,
            "promotionFeeGapBps": promotion_ratio - promotion_target if promotion_ratio is not None and promotion_target > 0 else None,
            "availableMonths": available, "missingMonths": [month for month in expected if month not in available],
            "missingGrossMarginMonths": [month for month in expected if month not in gross_margin_available],
        })
    return {"year": year, "cutoffMonth": cutoff, "availableMonths": months,
            "missingMonths": [month for month in expected if month not in months], "items": items,
            "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(items), "truncated": page * page_size < total}}
