"""Versioned, pure period resolution for future business analysis requests.

This module is intentionally not registered in the existing readers or report
protocol. Existing sealed evidence continues to use contracts.comparison_periods.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .contracts import AnalysisContractError, MAX_WINDOW_DAYS, canonical, strict_date


SCHEMA_VERSION = "business-comparison-periods-v2"
PREVIOUS_EQUAL_LENGTH_V1 = "previous_equal_length_v1"
SALES_CUSTOM_CALENDAR_MONTH_V1 = "sales_custom_calendar_month_v1"
RULES = frozenset({PREVIOUS_EQUAL_LENGTH_V1, SALES_CUSTOM_CALENDAR_MONTH_V1})


def _window(first: date, last: date) -> dict[str, str | int]:
    if first > last:
        raise AnalysisContractError("比较期间无效")
    return {
        "startDate": first.isoformat(),
        "endDate": last.isoformat(),
        "endExclusive": (last + timedelta(days=1)).isoformat(),
        "days": (last - first).days + 1,
    }


def _previous_year(day: date) -> date:
    year = day.year - 1
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def _previous_month(day: date) -> date:
    year, zero_month = divmod(day.year * 12 + day.month - 2, 12)
    month = zero_month + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _previous_equal_length(first: date, last: date) -> tuple[date, date]:
    days = (last - first).days + 1
    return first - timedelta(days=days), first - timedelta(days=1)


def _previous_sales_custom(first: date, last: date) -> tuple[date, date]:
    """Match sales.summary._custom_comparison_period for a custom range."""
    if first == last:
        previous = first - timedelta(days=1)
        return previous, previous
    if (first.year, first.month) == (last.year, last.month):
        previous_first = _previous_month(first)
        if first.day == 1 and last.day == calendar.monthrange(last.year, last.month)[1]:
            previous_last = date(
                previous_first.year,
                previous_first.month,
                calendar.monthrange(previous_first.year, previous_first.month)[1],
            )
        else:
            previous_last = _previous_month(last)
        return previous_first, previous_last
    return _previous_equal_length(first, last)


def resolve_periods(
    start_date: str,
    end_date: str,
    *,
    comparison_rule: str,
    cutoff_date: str | None = None,
) -> dict[str, object]:
    """Resolve inclusive dates; pin this entire result in a future new plan.

    A cutoff within the requested range shortens the current period and then
    resolves both baselines again. A cutoff before the requested start is
    rejected because it proves no current fact date is available.
    """
    if type(comparison_rule) is not str or comparison_rule not in RULES:
        raise AnalysisContractError("比较规则版本无效")
    first, requested_last = strict_date(start_date), strict_date(end_date)
    requested_days = (requested_last - first).days + 1
    if not 1 <= requested_days <= MAX_WINDOW_DAYS:
        raise AnalysisContractError("分析窗口必须为连续 1—93 天")
    cutoff = None if cutoff_date is None else strict_date(cutoff_date)
    if cutoff is not None and cutoff < first:
        raise AnalysisContractError("数据截止日早于分析开始日，不能构造本期比较")
    last = min(requested_last, cutoff) if cutoff is not None else requested_last
    previous_first, previous_last = (
        _previous_equal_length(first, last)
        if comparison_rule == PREVIOUS_EQUAL_LENGTH_V1
        else _previous_sales_custom(first, last)
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "timezone": "Asia/Shanghai",
        "comparisonRule": comparison_rule,
        "requested": _window(first, requested_last),
        "dataCutoffDate": cutoff.isoformat() if cutoff is not None else None,
        "periodAdjustedToDataCutoff": last != requested_last,
        "current": _window(first, last),
        "previous": _window(previous_first, previous_last),
        "yearAgo": _window(_previous_year(first), _previous_year(last)),
    }


def validate_resolved_periods(value: object) -> dict[str, object]:
    """Reject changed or incomplete persisted resolutions before future use."""
    if type(value) is not dict or set(value) != {
        "schemaVersion", "timezone", "comparisonRule", "requested", "dataCutoffDate",
        "periodAdjustedToDataCutoff", "current", "previous", "yearAgo",
    }:
        raise AnalysisContractError("比较期间字段集合无效")
    requested = value["requested"]
    if type(requested) is not dict or set(requested) != {"startDate", "endDate", "endExclusive", "days"}:
        raise AnalysisContractError("请求期间字段集合无效")
    expected = resolve_periods(
        requested["startDate"], requested["endDate"],
        comparison_rule=value["comparisonRule"], cutoff_date=value["dataCutoffDate"],
    )
    try:
        if canonical(value) != canonical(expected):
            raise AnalysisContractError("比较期间与固定规则不一致")
    except (TypeError, ValueError, UnicodeError) as error:
        raise AnalysisContractError("比较期间无法规范序列化") from error
    return expected
