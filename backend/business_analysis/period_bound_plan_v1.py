"""Pure versioned period binding for one strict v4 JD promotion plan.

This is a candidate envelope, not a replacement for the frozen v4 plan, a
source receipt, a DB migration, an Agent citation, or report authority.
Expected days are an obligation; they are not evidence of observed or zero
business days. Finance remains exact natural-month context.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import business_promotion_v4_plan as promotion, comparison_rules
from .contracts import AnalysisContractError, canonical, digest, strict_date


SCHEMA = "business-v4-jd-period-bound-plan-candidate-v1"
DAY_SCHEMA = "business-v4-expected-source-days-v1"
WINDOWS = ("current", "previous", "yearAgo")
COVERAGE_POLICY = {
    "schemaVersion": "business-v4-daily-coverage-obligation-v1",
    "observedRowsDoNotCertifyZeroDays": True,
    "zeroDayRequiresOwningProof": True,
    "missingOrUncertifiedDayIsNotZero": True,
    "numericComparisonRequiresBothWindowsComplete": True,
}


def _need(value, message="v4三期日期包络与精确推广计划不一致"):
    if not value:
        raise AnalysisContractError(message)


def _days(period):
    first, last = strict_date(period["startDate"]), strict_date(period["endDate"])
    _need((last - first).days + 1 == period["days"])
    return [(first + timedelta(days=index)).isoformat()
        for index in range(period["days"])]


def prepare_candidate(v4_plan, *, comparison_rule=
                      comparison_rules.PREVIOUS_EQUAL_LENGTH_V1,
                      cutoff_date=None):
    """Freeze three exact daily windows without changing v4 plan semantics.

    A data cutoff is intentionally unsupported here. The caller must request
    an amended, explicitly approved source plan rather than silently shorten
    a 30-day period because records were not yet observed.
    """
    _need(cutoff_date is None, "v4候选计划不得用数据截止日静默缩短三期")
    _need(comparison_rule == comparison_rules.PREVIOUS_EQUAL_LENGTH_V1,
          "v4推广三期只能使用已声明的前一等长周期规则")
    plan = promotion._plan(v4_plan)  # Full normative v4 capacity-plan rebuild.
    _need(plan["runCapacitySupported"] is True
          and plan["analysisRequest"]["requestedWindows"] == list(WINDOWS)
          and len(plan["sourcePlans"]) == 4,
          "v4三期计划须含三个可支持日来源及一个自然月来源")
    daily = [item for item in plan["sourcePlans"]
        if item["temporalRole"] == "daily_fact"]
    monthly = [item for item in plan["sourcePlans"]
        if item["temporalRole"] == "monthly_context"]
    _need(len(daily) == 3 and len(monthly) == 1
          and all(item["domain"] == "netshop"
              and item["query"].get("platform") == "京东"
              and item["query"].get("dataset") == "promotion"
              for item in daily)
          and monthly[0]["domain"] == "finance",
          "此首批版本只支持同一京东店铺推广三期及自然月财报背景")
    by_window = {item["query"]["window"]: item for item in daily}
    _need(len(by_window) == 3 and set(by_window) == set(WINDOWS),
          "推广日来源须完整且唯一覆盖三个窗口")
    original = by_window["current"]["query"]
    identity = {key: value for key, value in original.items() if key != "window"}
    _need(all({key: value for key, value in item["query"].items()
              if key != "window"} == identity for item in daily),
          "推广三期须同店同平台同数据集及同一原始日期")
    selection = promotion.prepare_candidate(plan, {
        "currentSourceKey": by_window["current"]["sourceKey"],
        "previousSourceKey": by_window["previous"]["sourceKey"],
        "yearAgoSourceKey": by_window["yearAgo"]["sourceKey"],
    })
    _need(all(selection["selectedWindows"][window]["status"] ==
              "selected_in_capacity_plan" for window in WINDOWS))
    periods = comparison_rules.resolve_periods(original["startDate"],
        original["endDate"], comparison_rule=comparison_rule,
        cutoff_date=None)
    _need(periods["periodAdjustedToDataCutoff"] is False)
    rows = []
    for item in sorted(daily, key=lambda value: value["ordinal"]):
        window = item["query"]["window"]
        period = periods[window]
        expected = {"schemaVersion": DAY_SCHEMA,
            "sourceKey": item["sourceKey"], "window": window,
            "dates": _days(period)}
        rows.append({"sourceKey": item["sourceKey"],
            "ordinal": item["ordinal"], "domain": item["domain"],
            "temporalRole": item["temporalRole"],
            "platform": item["query"]["platform"],
            "shop": item["query"]["shop"],
            "dataset": item["query"]["dataset"],
            "queryDigest": item["queryDigest"],
            "sourceIdentityDigest": item["sourceIdentityDigest"],
            "originalQueryStartDate": item["query"]["startDate"],
            "originalQueryEndDate": item["query"]["endDate"],
            "window": window, "resolvedPeriod": period,
            "expectedDayCount": len(expected["dates"]),
            "expectedDayDigest": digest(expected),
            "coveragePolicy": dict(COVERAGE_POLICY),
            "observedDailyCoverageVerified": False,
            "zeroDayCertificationVerified": False})
    finance = monthly[0]
    query = finance["query"]
    _need(query["analysisPeriod"] == {"startDate": original["startDate"],
                                    "endDate": original["endDate"]})
    finance_context = {"sourceKey": finance["sourceKey"],
        "ordinal": finance["ordinal"], "domain": finance["domain"],
        "temporalRole": finance["temporalRole"],
        "queryDigest": finance["queryDigest"],
        "sourceIdentityDigest": finance["sourceIdentityDigest"],
        "months": query["months"], "scope": query["scope"],
        "analysisPeriod": query["analysisPeriod"],
        "dailyProrationAllowed": False,
        "shopIdentityMappingVerified": False}
    value = {"schemaVersion": SCHEMA,
        "basePlanSchema": plan["schemaVersion"],
        "basePlanDigest": plan["planDigest"],
        "runIdentityDigest": plan["runIdentityDigest"],
        "analysisRequestDigest": digest(plan["analysisRequest"]),
        "comparisonRule": comparison_rule,
        "resolvedPeriods": periods,
        "dailySources": rows,
        "financeContext": finance_context,
        "sourceAuthorityVerified": False,
        "observedDailyCoverageVerified": False,
        "zeroDayCertificationVerified": False,
        "financeDailyProrationAllowed": False,
        "agentCitationSupported": False,
        "registeredRenderer": False,
        "limitations": [
            "预期业务日仅是计划义务；有记录日期不证明零业务日、完整导入或最终结算。",
            "缺日和未获拥有方零日证明均不可补零，也不得计算该来源的数值增长率。",
            "财报仅作为自然月背景，不摊分到三段30日窗口、SKU或推广利润。",
        ]}
    _need(len(canonical(value).encode("utf-8")) <= 64 * 1024,
          "日期包络超过固定容量")
    return {**value, "periodPlanDigest": digest(value)}


def validate_candidate(v4_plan, value):
    """Rebuild every field; a local digest is consistency, not authority."""
    expected = prepare_candidate(v4_plan)
    try:
        actual_raw = canonical(value)
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v4日期包络不能规范编码") from error
    _need(type(value) is dict and actual_raw == canonical(expected),
          "v4日期包络字段、日期或摘要发生变化")
    return expected
