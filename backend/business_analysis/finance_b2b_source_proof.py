"""Small, non-authorizing finance-month/B2B-window source proof table.

The two inputs come from different sealed report protocols. Even internally
consistent DTOs do not prove one shared report, shop mapping, order overlap,
incremental B2B sales, daily profit, or an Agent's persisted read.
"""
from __future__ import annotations

import calendar
import re

from .contracts import AnalysisContractError, canonical, digest
from .finance_source import CORE_METRICS
from .report_files import Column, Table


SCHEMA = "business-finance-b2b-source-proof-candidate-v1"
TABLE_KEY = "finance-b2b-source-proof"
WINDOWS = ("current", "previous", "yearAgo")
MAX_ROWS = 29
MAX_BYTES = 64 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ROW_FIELDS = {"domain", "temporalRole", "periodRole", "startDate",
    "endDate", "sourceKey", "sourceStatus", "coverageStatus",
    "missingDates", "metricStatus", "rowCount", "pageCount",
    "sourceRef", "sourceEvidenceDigest", "owningResultDigest",
    "scopeOrShop", "note"}
COLUMNS = (
    Column("domain", "来源域"), Column("temporalRole", "时间粒度"),
    Column("periodRole", "月份或比较窗口"),
    Column("startDate", "本行开始日"), Column("endDate", "本行结束日"),
    Column("sourceKey", "来源键"), Column("sourceStatus", "来源状态"),
    Column("coverageStatus", "日期或月份覆盖"),
    Column("missingDates", "缺日"), Column("metricStatus", "核心指标状态"),
    Column("rowCount", "已核来源行数", "integer"),
    Column("pageCount", "已核来源页数", "integer"),
    Column("sourceRef", "来源版本"),
    Column("sourceEvidenceDigest", "来源证据摘要"),
    Column("owningResultDigest", "拥有方结果摘要"),
    Column("scopeOrShop", "财报范围或京东店铺"),
    Column("note", "口径与缺口"),
)


def _need(value, message="财报/B端来源证明表候选与拥有方材料不一致"):
    if not value:
        raise AnalysisContractError(message)


def _sha(value):
    _need(type(value) is str and HEX64.fullmatch(value) is not None)
    return value


def _row(**values):
    _need(set(values) == ROW_FIELDS)
    return values


def _finance_rows(value):
    if value is None:
        return [_row(domain="finance", temporalRole="monthly_context",
            periodRole="not_supplied", startDate=None, endDate=None,
            sourceKey=None, sourceStatus="not_supplied",
            coverageStatus="unavailable", missingDates=None,
            metricStatus=None, rowCount=None, pageCount=None,
            sourceRef=None, sourceEvidenceDigest=None,
            owningResultDigest=None, scopeOrShop=None,
            note="未提供同账号封存的自然月财报意图；不是财务零值或平台无数据。")]
    _need(type(value) is dict and value.get("schemaVersion") ==
        "business-finance-v3-report-monthly-material-candidate-v1"
        and value.get("signedOwningBridgeVerified") is True
        and value.get("sealedSelectedSourceFullyReplayed") is True
        and value.get("agentReadReceiptRecorded") is False
        and value.get("financeDailyProrationAllowed") is False
        and value.get("financeSkuProfitAttributionAllowed") is False
        and value.get("sumOverlappingErpB2bAdsAllowed") is False
        and value.get("reportGenerationSupported") is False
        and value.get("upstreamSourceSignatureVerified") is False
        and value.get("resultDigest") == digest({key: item for key, item
            in value.items() if key != "resultDigest"}),
        "财报材料不是完整封存自然月候选")
    for key in ("sealedDigest", "sourceRef", "receiptChainDigest",
                "resultDigest"):
        _sha(value[key])
    months = value["naturalMonths"]
    coverage = value["monthlyCoverage"]
    missing = value["missingMonths"]
    _need(type(months) is list and 1 <= len(months) <= 24
        and type(coverage) is list and len(coverage) == len(months)
        and type(missing) is list
        and value["sourceQuery"]["months"] == months
        and value["analysisPeriod"] == value["sourceQuery"]["analysisPeriod"]
        and value["scope"] == value["sourceQuery"]["scope"]
        and value["rowCount"] >= 0 and value["pageCount"] >= 1,
        "财报来源月份或范围没有完整对应")
    rows = []
    absent = []
    for month, item in zip(months, coverage):
        _need(type(month) is str and re.fullmatch(r"[12][0-9]{3}-(0[1-9]|1[0-2])", month)
            and item["month"] == month and type(item["published"]) is bool
            and type(item["metrics"]) is dict
            and set(item["metrics"]) == set(CORE_METRICS),
            "财报自然月或核心指标状态不完整")
        status = {metric: cell["status"] for metric, cell in item["metrics"].items()}
        _need(all(type(s) is str and s in {"present", "missing_month",
            "missing_subject", "ambiguous_subject", "non_numeric",
            "missing_value"} for s in status.values()))
        if not item["published"]:
            absent.append(month)
            _need(set(status.values()) == {"missing_month"})
        else:
            _need("missing_month" not in status.values())
        year, number = int(month[:4]), int(month[5:])
        rows.append(_row(domain="finance", temporalRole="monthly_context",
            periodRole=month, startDate=f"{month}-01",
            endDate=f"{month}-{calendar.monthrange(year, number)[1]:02d}",
            sourceKey=value["sourceKey"],
            sourceStatus="published_month" if item["published"] else "missing_month",
            coverageStatus=("all_core_metrics_present" if
                all(s == "present" for s in status.values()) else
                "metric_gap" if item["published"] else "missing_month"),
            missingDates=None, metricStatus=status,
            rowCount=value["rowCount"], pageCount=value["pageCount"],
            sourceRef=value["sourceRef"],
            sourceEvidenceDigest=value["receiptChainDigest"],
            owningResultDigest=value["resultDigest"],
            scopeOrShop=value["scope"]["scope_name"],
            note="自然月财报；不按日、订单或SKU摊分，不与ERP/B端/推广金额相加。"))
    _need(absent == missing, "财报缺月与拥有方完整覆盖不同")
    return rows


def _b2b_rows(value):
    if value is None:
        return [_row(domain="b2b", temporalRole="daily_fact",
            periodRole=window, startDate=None, endDate=None,
            sourceKey=None, sourceStatus="not_supplied",
            coverageStatus="unavailable", missingDates=None,
            metricStatus=None, rowCount=None, pageCount=None,
            sourceRef=None, sourceEvidenceDigest=None,
            owningResultDigest=None, scopeOrShop=None,
            note="未提供同账号封存的京东B端报告；不是B端零销售或数据库全局无数据。")
            for window in WINDOWS]
    _need(type(value) is dict and value.get("schemaVersion") ==
        "business-b2b-report-owning-material-candidate-v1"
        and value.get("sealedReportDirectoryVerified") is True
        and value.get("sealedSelectedSourcesFullyReplayed") is True
        and value.get("databaseGlobalB2BAbsenceVerified") is False
        and value.get("b2bIncludedInErpSales") == "unknown"
        and value.get("b2bIncludedInPlatformSkuSales") == "unknown"
        and value.get("b2bShareOfErpSales") is None
        and value.get("b2bIncrementalSalesCents") is None
        and value.get("crossDomainAmountsAdded") is False
        and value.get("authorityVerified") is False
        and value.get("registeredAgentTool") is False
        and value.get("registeredRenderer") is False
        and value.get("resultDigest") == digest({key: item for key, item
            in value.items() if key != "resultDigest"}),
        "B端材料不是同店封存目录的完整候选")
    _need(value["platform"] == "京东" and type(value["shop"]) is str
        and 0 < len(value["shop"]) <= 100
        and type(value["sourceSummary"]) is dict
        and set(value["sourceSummary"]) == set(WINDOWS)
        and type(value["material"]) is dict
        and set(value["material"]["windows"]) == set(WINDOWS))
    rows = []
    missing = []
    for window in WINDOWS:
        summary = value["sourceSummary"][window]
        own = value["material"]["windows"][window]
        _need(summary["sourceKey"] == own["sourceKey"]
            and summary["sourceRef"] == own["sourceRef"]
            and summary["evidenceDigest"] == own["evidenceDigest"]
            and summary["coverage"] == own["coverage"])
        period = own["period"]
        if summary["sourceKey"] is None:
            missing.append(window)
            _need(summary["status"] == "catalogue_only_missing_source"
                and summary["rowCount"] is None
                and summary["pageCount"] is None
                and summary["sourceRef"] is None
                and summary["evidenceDigest"] is None
                and summary["coverage"] is None)
            coverage_status = "catalogue_only_missing_source"
            missing_dates = None
            status = "catalogue_only_missing_source"
        else:
            _need(summary["status"] == "selected_sealed_source"
                and type(summary["rowCount"]) is int
                and 0 <= summary["rowCount"] <= 200_000
                and type(summary["pageCount"]) is int
                and 1 <= summary["pageCount"] <= 2_000)
            _sha(summary["sourceRef"])
            _sha(summary["evidenceDigest"])
            coverage_status = summary["coverage"]["status"]
            missing_dates = summary["coverage"]["missingDates"]
            status = ("selected_no_records" if summary["rowCount"] == 0
                else "selected_sealed_source")
        metric_status = {name: cell["status"] for name, cell in
            own["periodMetrics"].items()}
        rows.append(_row(domain="b2b", temporalRole="daily_fact",
            periodRole=window, startDate=period["startDate"],
            endDate=period["endDate"], sourceKey=summary["sourceKey"],
            sourceStatus=status, coverageStatus=coverage_status,
            missingDates=missing_dates, metricStatus=metric_status,
            rowCount=summary["rowCount"], pageCount=summary["pageCount"],
            sourceRef=summary["sourceRef"],
            sourceEvidenceDigest=summary["evidenceDigest"],
            owningResultDigest=value["resultDigest"],
            scopeOrShop=value["shop"],
            note="京东jd_b2b原生事实；未证明它与ERP或平台SKU销售包含/互斥，不算占比或增量。"))
    _need(missing == value["catalogueMissingWindows"])
    return rows


def _erp_row(erp, source, b2b):
    """Project one selected ERP window, never an order-level B2B reconciliation."""
    if erp is None and source is None:
        return None
    _need(type(erp) is dict and type(source) is dict and b2b is not None,
        "ERP销售证明须与同一封存报告的B端材料成对提供")
    _need(erp.get("schemaVersion") ==
        "business-erp-report-rollup-materials-candidate-v1"
        and erp.get("manifestDigest") == digest({key: item for key, item
            in erp.items() if key != "manifestDigest"})
        and erp.get("authorityVerified") is False
        and erp.get("registeredRenderer") is False
        and erp.get("netshopAdFinanceCombined") is False
        and erp.get("reportBinding") == b2b["reportBinding"]
        and erp.get("salesKey") == source.get("key")
        and erp.get("salesQueryDigest") == digest(source.get("query")),
        "ERP回卷与B端不是同一已封存报告或来源身份")
    query, info = source["query"], source["info"]
    _need(type(query) is dict and query.get("platform") == "京东"
        and query.get("shop") == b2b["shop"]
        and query.get("window") == "current"
        and (query.get("startDate"), query.get("endDate")) ==
            (b2b["material"]["windows"]["current"]["period"]["startDate"],
             b2b["material"]["windows"]["current"]["period"]["endDate"])
        and type(info) is dict and type(info.get("expected")) is dict
        and erp.get("sourceProofs", {}).get("sales") == info["expected"]
        and erp.get("sourceRowCount") == info["expected"].get("rowCount")
        and type(info.get("pageCount")) is int and info["pageCount"] >= 1,
        "ERP和B端京东店铺、本期或销售来源控制不一致")
    proof = info["expected"]
    _sha(proof["sourceRef"]); _sha(proof["evidenceDigest"])
    _need(type(proof["metrics"]) is dict and
        all(type(cell) is dict and cell.get("presentRows") is not None
            and cell.get("missingRows") is not None for cell in proof["metrics"].values()))
    status = {name: ("missing_value" if cell["missingRows"] else
        "present" if cell["presentRows"] else "no_records")
        for name, cell in proof["metrics"].items()}
    return _row(domain="erp", temporalRole="daily_fact",
        periodRole="current", startDate=query["startDate"],
        endDate=query["endDate"], sourceKey=source["key"],
        sourceStatus="selected_no_records" if proof["rowCount"] == 0
            else "selected_sealed_source",
        coverageStatus=info.get("metadata", {}).get("coverage", {}).get("status"),
        missingDates=info.get("metadata", {}).get("coverage", {}).get("missingDates"),
        metricStatus=status, rowCount=proof["rowCount"],
        pageCount=info["pageCount"], sourceRef=proof["sourceRef"],
        sourceEvidenceDigest=proof["evidenceDigest"],
        owningResultDigest=erp["manifestDigest"], scopeOrShop=b2b["shop"],
        note="ERP净销售/退款/成本本期来源；B端与ERP可能包含，不能相加或计算B端增量。")


def build_candidate(*, finance=None, b2b=None, erp=None, erp_source=None):
    """Project independent owning DTOs; no same-report authority is inferred."""
    rows = [*_finance_rows(finance), *_b2b_rows(b2b)]
    erp_row = _erp_row(erp, erp_source, b2b)
    if erp_row is not None:
        rows.append(erp_row)
    _need(4 <= len(rows) <= MAX_ROWS and all(set(row) == ROW_FIELDS for row in rows))
    value = {"schemaVersion": SCHEMA, "tableKey": TABLE_KEY,
        "rows": rows, "rowCount": len(rows),
        "financeIntentId": finance["intentId"] if finance else None,
        "b2bReportId": b2b["reportBinding"]["reportId"] if b2b else None,
        "erpReportId": erp["reportBinding"]["reportId"] if erp else None,
        "erpB2bSameReportBindingVerified": erp_row is not None,
        "erpB2bSameShopQueryVerified": erp_row is not None,
        "financeNaturalMonthsOnly": True,
        "financeDailyProrationAllowed": False,
        "sameReportAuthorityVerified": False,
        "sameShopIdentityVerified": False,
        "b2bIncludedInErpSales": "unknown",
        "b2bIncludedInPlatformSkuSales": "unknown",
        "b2bShareOfErpSales": None,
        "b2bIncrementalSalesCents": None,
        "crossDomainAmountsAdded": False,
        "agentReadPersisted": False,
        "registeredRenderer": False,
        "limitations": ["财报自然月、B端日窗口分别来自各自封存报告；未证明两者同一报告或店铺别名。",
            "目录缺源仅说明这份封存目录未选该来源，未导入不等于零业务或平台没有数据。",
            "B端与ERP/平台SKU/广告归因可能重叠，不相加、不计算B端增量或SKU利润。"]}
    _need(len(canonical(value).encode("utf-8")) <= MAX_BYTES)
    return {**value, "candidateDigest": digest(value)}


def as_table(candidate):
    """Typed renderer-neutral Table; not registered with any file version."""
    _need(type(candidate) is dict and candidate.get("schemaVersion") == SCHEMA
        and candidate.get("tableKey") == TABLE_KEY
        and type(candidate.get("rows")) is list
        and candidate.get("rowCount") == len(candidate["rows"])
        and candidate.get("candidateDigest") == digest({key: value
            for key, value in candidate.items() if key != "candidateDigest"})
        and candidate.get("sameReportAuthorityVerified") is False
        and candidate.get("financeDailyProrationAllowed") is False
        and candidate.get("crossDomainAmountsAdded") is False
        and candidate.get("registeredRenderer") is False)
    return Table(TABLE_KEY, "财报自然月与B端三期来源证明（候选）",
        "仅展示来源、缺口和证据；无财报日摊、B端增量或跨域金额合计。",
        COLUMNS, [[canonical(row[key]) if type(row[key]) in (dict, list)
            else row[key] for key in (column.key for column in COLUMNS)]
            for row in candidate["rows"]], candidate["rowCount"])
