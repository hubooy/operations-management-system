"""Human-readable, source-bound scope and limitations for a promotion trial file.

Inputs come from one fully verified sealed Reader. This projection does not
infer store-wide sales, visitor UV, financial profit, or cross-source totals.
"""
from .contracts import AnalysisContractError, comparison_periods
from .report_files import Column, Table


WINDOWS = {"current": "本期", "previous": "前等长周期", "yearAgo": "去年对应期"}
BOUNDARIES = (
    ("店铺区间去重UV", "未交付", "缺独立店铺区间来源与去重合同；日访客和商品访客不能相加替代。"),
    ("B端销售包含关系", "待核", "B端与ERP或平台销售可能重叠，本报告不直接相加。"),
    ("市场深诊断", "未交付", "封存市场来源不等于市场Agent已分析；TOP样本不属于本店销售。"),
    ("自然月财务", "未交付", "月度财报不按日或SKU摊分，也不重复扣推广费。"),
    ("可编辑预算工作表", "未交付", "此推广试用文件不含可编辑预算公式页；批准内容原文另保留。"),
    ("词货SKU身份", "局限", "两张推广词货视图只使用明确推广SKU；触发、跟单和归因SKU不自动合一。"),
)


def _need(condition):
    if not condition:
        raise AnalysisContractError("推广试用来源范围或核对材料无效")


def _text(value, maximum=2048):
    _need(type(value) is str and len(value) <= maximum)
    return value


def _dates(value):
    if value is None:
        return "未提供"
    _need(type(value) is list and len(value) <= 93
        and all(type(item) is str and len(item) == 10 for item in value))
    return "、".join(value) if value else "无"


def tables(sources, info_by_key):
    """Project verified source coverage; row values never authorize analysis."""
    _need(type(sources) is list and 1 <= len(sources) <= 48
        and type(info_by_key) is dict
        and len(info_by_key) == len(sources))
    keys = [source.get("key") for source in sources if type(source) is dict]
    _need(len(keys) == len(sources) and len(set(keys)) == len(keys)
        and set(info_by_key) == set(keys))
    rows = []
    for source in sources:
        key = _text(source["key"], 160)
        domain = _text(source["domain"], 20)
        query = source["query"]
        info = info_by_key[key]
        _need(type(query) is dict and type(info) is dict
            and type(info.get("metadata")) is dict
            and type(info.get("expected")) is dict)
        window = query.get("window", "current")
        _need(window in WINDOWS)
        periods = comparison_periods(query["startDate"], query["endDate"])
        actual = periods[window]
        expected, metadata = info["expected"], info["metadata"]
        coverage = metadata.get("coverage")
        _need(type(coverage) is dict and expected.get("reconciled") is True
            and type(expected.get("rowCount")) is int
            and expected["rowCount"] >= 0
            and type(info.get("pageCount")) is int and info["pageCount"] >= 1)
        missing = coverage.get("missingDates")
        metrics = expected.get("metrics")
        _need(type(metrics) is dict)
        metric_gaps = []
        for metric, cell in sorted(metrics.items()):
            _need(type(metric) is str and type(cell) is dict
                and type(cell.get("missingRows")) is int
                and 0 <= cell["missingRows"] <= expected["rowCount"])
            if cell["missingRows"]:
                metric_gaps.append(f"{_text(metric, 100)}缺{cell['missingRows']}行")
        identity = query.get("shop") or query.get("category") or "未提供"
        rows.append([
            key, domain, _text(query["platform"], 100),
            _text(identity, 200), _text(query.get("dataset") or query.get("channel")
                or query.get("scope") or "未提供", 200),
            WINDOWS[window], query["startDate"], query["endDate"],
            actual["startDate"], actual["endDate"],
            _text(expected["sourceRef"], 160),
            _text(metadata.get("sourceRevision") or "未提供", 160),
            expected["rowCount"], info["pageCount"],
            _text(coverage.get("status") or "未提供", 100),
            _dates(missing), "；".join(metric_gaps) if metric_gaps else "无",
            _text(expected["evidenceDigest"], 64),
        ])
    scope = Table("promotion-trial-source-scope", "来源范围与逐项核对",
        "日期为各来源实际比较窗口；缺日不是零业务。已核对表示来源行与控制汇总一致，不证明平台最终结算。",
        (Column("sourceKey", "来源键"), Column("domain", "来源域"),
         Column("platform", "平台"), Column("identity", "店铺或类目"),
         Column("dataset", "数据集或渠道"), Column("window", "比较窗口"),
         Column("requestedStart", "原始起日"), Column("requestedEnd", "原始止日"),
         Column("actualStart", "实际起日"), Column("actualEnd", "实际止日"),
         Column("sourceRef", "来源版本"), Column("sourceRevision", "修订水位"),
         Column("rowCount", "已核明细行", "integer"),
         Column("pageCount", "已核页数", "integer"),
         Column("coverageStatus", "日期覆盖状态"), Column("missingDates", "缺失日期"),
         Column("metricGaps", "指标缺值行"), Column("evidenceDigest", "逐页证据摘要")),
        rows, len(rows))
    boundaries = Table("promotion-trial-boundaries", "本专项未交付与待核范围",
        "以下是能力与口径边界，不是对应来源的零值或已完成经营判断。",
        (Column("item", "事项"), Column("status", "状态"), Column("reason", "原因及禁用推断")),
        [list(row) for row in BOUNDARIES], len(BOUNDARIES))
    return scope, boundaries
