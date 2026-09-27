"""Pure market TOP observations inside two complete sealed multi-day windows.

The owning caller supplies complete pages and PageReconciler results. A missing
date is never interpreted as a product leaving the TOP sample or as zero sales.
"""
from datetime import timedelta
import calendar

from .contracts import AnalysisContractError, canonical, comparison_periods, digest, strict_date
from . import market_dynamics as previous


ALGORITHM_VERSION = "market-daily-observation-v2"


def _need(ok, message="市场单日观察合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def observation_dates(query, current_day, baseline_day, baseline_window):
    """Require the same indexed day, or the clamped prior-year day."""
    periods = comparison_periods(query["startDate"], query["endDate"])
    current = strict_date(current_day)
    baseline = strict_date(baseline_day)
    _need(baseline_window in ("previous", "yearAgo")
        and periods["current"]["startDate"] <= current_day <= periods["current"]["endDate"]
        and periods[baseline_window]["startDate"] <= baseline_day <= periods[baseline_window]["endDate"],
        "观察日期超出固定比较窗口")
    if baseline_window == "previous":
        offset = (current - strict_date(periods["current"]["startDate"])).days
        expected = strict_date(periods["previous"]["startDate"]) + timedelta(days=offset)
    else:
        year = current.year - 1
        expected = current.replace(year=year,
            day=min(current.day, calendar.monthrange(year, current.month)[1]))
    _need(baseline == expected, "基期单日不是固定规则的对应日期")
    return periods


def rank_entry_exit(current_source, current_pages, current_expected,
                    baseline_source, baseline_pages, baseline_expected,
                    current_day, baseline_day):
    """Compare two exact days after reconciling both complete market sources."""
    current, head, all_current = previous._ingest(current_source, current_pages, current_expected)
    baseline, before, all_baseline = previous._ingest(baseline_source, baseline_pages, baseline_expected)
    _need(head["inputBytes"] + before["inputBytes"] <= previous.MAX_BYTES
        and head["inputPages"] + before["inputPages"] <= previous.MAX_PAGES
        and len(all_current) + len(all_baseline) <= previous.MAX_ROWS,
        "两期完整市场输入超过边界")
    a, b = current["query"], baseline["query"]
    _need(current["key"] != baseline["key"] and a["window"] == "current"
        and b["window"] in ("previous", "yearAgo")
        and {key: value for key, value in a.items() if key != "window"}
            == {key: value for key, value in b.items() if key != "window"},
        "进出榜来源市场身份不一致")
    observation_dates(a, current_day, baseline_day, b["window"])
    dimension = "skuId" if a["rankingDimension"] == "SKU" else "spuId"
    now = {row[dimension]: row for row in all_current if row["date"] == current_day}
    past = {row[dimension]: row for row in all_baseline if row["date"] == baseline_day}
    current_date_present = current_day in head["coverage"]["presentDates"]
    baseline_date_present = baseline_day in before["coverage"]["presentDates"]
    complete = current_date_present and baseline_date_present
    rows = []
    for key in sorted(set(now) | set(past)):
        left, right = now.get(key), past.get(key)
        status = ("both_observed" if left and right else
            "entered_observed_top_sample" if left and complete else
            "left_observed_top_sample" if right and complete else
            "insufficient_date_coverage")
        def side(row, date_present):
            return {"status": ("observed" if row else
                "not_observed_in_top_sample" if date_present else "date_not_covered"),
                "date": row["date"] if row else None,
                "rank": row["sample"]["rank"] if row else None,
                "metrics": row["metrics"] if row else None,
                "sourceRowHash": row["sourceRowHash"] if row else None}
        value = {"skuId": key if dimension == "skuId" else None,
            "spuId": key if dimension == "spuId" else None,
            "current": side(left, current_date_present),
            "baseline": side(right, baseline_date_present),
            "status": status,
            "rankImprovement": (right["sample"]["rank"]-left["sample"]["rank"]
                if left and right and left["sample"]["rank"] is not None
                and right["sample"]["rank"] is not None else None)}
        value["rowId"] = digest([ALGORITHM_VERSION, current, baseline,
            head, before, current_day, baseline_day, value])
        rows.append(value)
    output = {"schemaVersion": "market-dynamics-table-v2",
        "algorithmVersion": ALGORITHM_VERSION, "view": "rank_entry_exit",
        "sources": [current, baseline], "sourceMetadata": [head, before],
        "observationDates": {"current": current_day, "baseline": baseline_day},
        "observationCoverage": {"currentDatePresent": current_date_present,
            "baselineDatePresent": baseline_date_present,
            "bothDatesPresent": complete},
        "rows": rows, "rowCount": len(rows), "authorityVerified": False,
        "ownProductIdentityVerified": False,
        "limitations": ["两份完整封存来源仅按明确单日比较TOP样本。",
            "日期缺失与商品未入TOP样本不同，二者均不等于零销量。",
            "市场SKU/SPU不证明本店销售、ERP净收入或B端成交。"]}
    output["tableDigest"] = digest(output)
    _need(len(rows) <= previous.MAX_ROWS and len(canonical(output).encode("utf-8")) <= previous.MAX_BYTES,
        "市场单日观察输出超过固定容量")
    return output
