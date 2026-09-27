"""Pure exact JD promotion-window choice over a prospective v4 capacity plan.

All hashes are self-consistency checks, never owning-source or report authority.
"""
from __future__ import annotations

import json

from . import evidence_v2, evidence_v3, evidence_v4
from .contracts import AnalysisContractError, canonical, comparison_periods, digest


SCHEMA = "business-v4-jd-promotion-window-selection-candidate-v1"
WINDOWS = ("current", "previous", "yearAgo")
SELECTOR = frozenset(("currentSourceKey", "previousSourceKey", "yearAgoSourceKey"))
PLAN_FIELDS = frozenset(("schemaVersion", "capacityProfile", "clientRequestId",
    "runIdentityDigest", "analysisRequest", "sourcePlans", "sourceCount",
    "estimatedTotalRows", "estimatedTotalPages", "estimatedBytesUpperBound",
    "sourcePageCap", "sourceByteCap", "runPageCap", "runByteCap",
    "maximumRowsPerPage", "runCapacitySupported", "unsupportedReasons",
    "sourceAuthorityVerified", "measurementAuthorityVerified",
    "productionRowWidthApprovalRequired", "crossDomainSnapshotAtomic",
    "financeDailyProrationAllowed", "sumOverlappingErpB2bAdsAllowed",
    "skuFinanceProfitInferenceAllowed", "reportGenerationSupported",
    "modelDispatchSupported", "meaning", "planDigest"))


def _need(ok, message="v4推广三窗口来源与固定容量计划不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _copied(value):
    try:
        raw = canonical(value)
        _need(len(raw.encode("utf-8")) <= 256 * 1024, "v4候选计划超过固定容量")
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v4候选计划结构无法规范编码") from error


def _plan(value):
    plan = _copied(value)
    _need(type(plan) is dict and set(plan) == PLAN_FIELDS
        and plan["schemaVersion"] == evidence_v4.PLAN_SCHEMA
        and plan["capacityProfile"] == evidence_v4.CAPACITY_PROFILE
        and type(plan["sourcePlans"]) is list and 2 <= len(plan["sourcePlans"]) <= evidence_v4.MAX_SOURCES
        and plan["sourceCount"] == len(plan["sourcePlans"])
        and plan["planDigest"] == digest({key: item for key, item in plan.items()
            if key != "planDigest"}), "v4计划版本、完整摘要或来源数量不一致")
    request = evidence_v2.validate_analysis_request(plan["analysisRequest"])
    evidence_v4._id(plan["clientRequestId"], "客户端请求ID")
    _need(plan["analysisRequest"] == request)
    sources = []
    for entry in plan["sourcePlans"]:
        _need(type(entry) is dict and all(key in entry for key in
            ("sourceKey", "domain", "query", "measuredRowCount", "maxRowUtf8Bytes",
             "pageEnvelopeUtf8Bytes", "sourceRevisionHint", "sampleRowCount",
             "sampleMaxUtf8Bytes")))
        sources.append({"key": entry["sourceKey"], "domain": entry["domain"],
            "query": entry["query"]})
    entries = evidence_v3.normalize_sources(sources, analysis_request=request)
    _need(len(entries) == len(plan["sourcePlans"]))
    for actual, entry in zip(plan["sourcePlans"], entries):
        rows = evidence_v4._integer(actual["measuredRowCount"], 0,
            evidence_v4.MAX_SAFE_INTEGER, "行数")
        width = evidence_v4._integer(actual["maxRowUtf8Bytes"],
            0 if rows == 0 else 2, evidence_v4.MAX_RUN_BYTES, "最大行宽")
        overhead = evidence_v4._integer(actual["pageEnvelopeUtf8Bytes"], 1,
            evidence_v4.MAX_RUN_BYTES, "页固定开销")
        count = evidence_v4._integer(actual["sampleRowCount"], 0, min(10, rows),
            "样本行数")
        sample_width = evidence_v4._integer(actual["sampleMaxUtf8Bytes"],
            0 if count == 0 else 2, width, "样本最大行宽")
        _need((count == 0) == (sample_width == 0))
        revision = actual["sourceRevisionHint"]
        _need(type(revision) is str and 1 <= evidence_v4._utf8_length(
            revision, "来源修订提示") <= 128 and not any(ord(c) < 32 for c in revision))
        measured = {"measuredRowCount": rows, "maxRowUtf8Bytes": width,
            "pageEnvelopeUtf8Bytes": overhead, "sourceRevisionHint": revision,
            "sampleRowCount": count, "sampleMaxUtf8Bytes": sample_width}
        _need(actual == evidence_v4._estimate(entry, measured),
            "v4来源身份、窗口或容量估算与规范计划不同")
    source_plans = plan["sourcePlans"]
    page_values = [item["estimatedPageCount"] for item in source_plans]
    byte_values = [item["estimatedBytesUpperBound"] for item in source_plans]
    pages = sum(page_values) if all(value is not None for value in page_values) else None
    total_bytes = sum(byte_values) if all(value is not None for value in byte_values) else None
    total_rows = sum(item["measuredRowCount"] for item in source_plans)
    reasons = []
    if any(not item["sourceCapacitySupported"] for item in source_plans):
        reasons.append("one_or_more_sources_unsupported")
    if pages is None or pages > evidence_v4.MAX_RUN_PAGES:
        reasons.append("run_page_cap_exceeded_or_unknown")
    if total_bytes is None or total_bytes > evidence_v4.MAX_RUN_BYTES:
        reasons.append("run_byte_cap_exceeded_or_unknown")
    if total_rows > evidence_v4.MAX_SAFE_INTEGER:
        reasons.append("run_row_count_exceeds_safe_integer")
    run_identity = {"schemaVersion": evidence_v4.RUN_IDENTITY_SCHEMA,
        "clientRequestId": plan["clientRequestId"],
        "capacityProfile": evidence_v4.CAPACITY_PROFILE,
        "sourceIdentityDigests": [item["sourceIdentityDigest"] for item in source_plans],
        "analysisRequestDigest": digest(request)}
    _need(plan["runIdentityDigest"] == digest(run_identity)
        and plan["estimatedTotalRows"] == (total_rows if total_rows <= evidence_v4.MAX_SAFE_INTEGER else None)
        and plan["estimatedTotalPages"] == pages
        and plan["estimatedBytesUpperBound"] == total_bytes
        and plan["runCapacitySupported"] is (not reasons)
        and plan["unsupportedReasons"] == reasons
        and plan["sourcePageCap"] == evidence_v4.MAX_SOURCE_PAGES
        and plan["sourceByteCap"] == evidence_v4.MAX_SOURCE_BYTES
        and plan["runPageCap"] == evidence_v4.MAX_RUN_PAGES
        and plan["runByteCap"] == evidence_v4.MAX_RUN_BYTES
        and plan["maximumRowsPerPage"] == evidence_v4.MAX_ROWS_PER_PAGE
        and all(plan[key] is False for key in
            ("sourceAuthorityVerified", "measurementAuthorityVerified",
             "crossDomainSnapshotAtomic", "financeDailyProrationAllowed",
             "sumOverlappingErpB2bAdsAllowed", "skuFinanceProfitInferenceAllowed",
             "reportGenerationSupported", "modelDispatchSupported"))
        and plan["productionRowWidthApprovalRequired"] is True,
        "v4任务摘要、容量或非授权声明与规范计划不同")
    return plan


def prepare_candidate(plan, selector):
    """Choose one exact JD promotion identity; absent optional bases stay gaps."""
    plan = _plan(plan)
    chosen = _copied(selector)
    _need(type(chosen) is dict and set(chosen) == SELECTOR,
        "推广三窗口选择必须完整声明本期及两个可选基期")
    lookup = {entry["sourceKey"]: entry for entry in plan["sourcePlans"]}
    _need(type(chosen["currentSourceKey"]) is str
        and chosen["currentSourceKey"] in lookup, "推广本期来源不在固定计划")
    current = lookup[chosen["currentSourceKey"]]
    q = current["query"]
    _need(current["domain"] == "netshop" and current["temporalRole"] == "daily_fact"
        and q["platform"] == "京东" and q["dataset"] == "promotion"
        and q["window"] == "current", "本期须为固定京东推广来源")
    periods = comparison_periods(q["startDate"], q["endDate"])
    selected, used = {}, set()
    requested = set(plan["analysisRequest"]["requestedWindows"])
    for window, field in (("current", "currentSourceKey"),
                          ("previous", "previousSourceKey"),
                          ("yearAgo", "yearAgoSourceKey")):
        key = chosen[field]
        if window not in requested:
            _need(key is None, "未请求的推广比较窗口不能偷加来源")
            _need(not any(entry["domain"] == "netshop" and
                entry["query"].get("dataset") == "promotion" and
                entry["query"].get("window") == window and
                {name: value for name, value in entry["query"].items()
                    if name != "window"} ==
                {name: value for name, value in q.items() if name != "window"}
                for entry in plan["sourcePlans"]),
                "未请求的比较窗口已混入同店推广计划")
            selected[window] = {"status": "missing_source_not_requested", "sourceKey": None,
                "queryDigest": None, "sourceIdentityDigest": None,
                "period": periods[window], "capacityStatus": "not_requested",
                "numericComparisonAvailable": False}
            continue
        if key is None and window != "current":
            selected[window] = {"status": "missing_requested_source", "sourceKey": None,
                "queryDigest": None, "sourceIdentityDigest": None,
                "period": periods[window], "capacityStatus": "not_selected",
                "numericComparisonAvailable": False}
            continue
        _need(type(key) is str and key in lookup and key not in used,
            "已请求推广窗口缺来源、重复来源或不在计划")
        source = lookup[key]
        query = source["query"]
        _need(source["domain"] == "netshop" and source["temporalRole"] == "daily_fact"
            and query["platform"] == "京东" and query["dataset"] == "promotion"
            and query["window"] == window
            and {name: value for name, value in query.items() if name != "window"}
                == {name: value for name, value in q.items() if name != "window"},
            "推广基期跨店、错日期、错数据集或窗口")
        used.add(key)
        selected[window] = {"status": "selected_in_capacity_plan", "sourceKey": key,
            "queryDigest": source["queryDigest"],
            "sourceIdentityDigest": source["sourceIdentityDigest"],
            "period": periods[window],
            "capacityStatus": "supported" if source["sourceCapacitySupported"] else "unsupported",
            "numericComparisonAvailable": False}
    result = {"schemaVersion": SCHEMA, "planDigest": plan["planDigest"],
        "runIdentityDigest": plan["runIdentityDigest"],
        "analysisRequestDigest": digest(plan["analysisRequest"]),
        "platform": "京东", "shop": q["shop"],
        "originalPeriod": {"startDate": q["startDate"], "endDate": q["endDate"]},
        "comparisonRule": periods["comparisonRule"], "periods": periods,
        "runCapacitySupported": plan["runCapacitySupported"],
        "requestedWindows": [window for window in WINDOWS if window in requested],
        "selectedWindows": selected,
        "sourceAuthorityVerified": False, "measurementAuthorityVerified": False,
        "persistentEvidenceVerified": False, "agentDispatchSupported": False,
        "rendererSupported": False,
        "limitations": ["仅检验候选容量计划中的精确京东推广窗口；无拥有方读取或已发布事实证明。",
            "未请求或已请求但未选择的环比/同比基期均明确保留缺来源，不补零、不产生数值比较。",
            "环比使用本期之前的等长区间；同比按前一年同月同日，闰日收敛至月末。",
            "推广归因成交额不等于ERP净销售或利润，三个窗口不是同一时刻快照。"]}
    return {**result, "selectionDigest": digest(result)}
