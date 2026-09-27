"""Read-only request planning; no source reads, model dispatch or mutations."""
from business_analysis.contracts import AnalysisContractError
from business_analysis import planning
from .policy import AiError, current_principal, digest


def analysis_request(value):
    try:
        return planning.validate_analysis_request(value)
    except AnalysisContractError as error:
        raise AiError(str(error)) from error


def validate_request_sources(request, sources):
    """Requested coverage must be scheduled; this never proves fact coverage."""
    dates, windows_by_identity = set(), {}
    expected_windows = set(request["requestedWindows"])
    for source in sources:
        query = source["query"]
        dates.add((query["startDate"], query["endDate"]))
        window = query.get("window", "current")
        if source["domain"] == "netshop" and query.get("dataset") == "master":
            if window != "current":
                raise AiError("当前商品主数据只能安排本期快照，不能作为历史主数据")
            continue
        identity = digest({"domain": source["domain"], "query": {k: v for k, v in query.items() if k != "window"}})
        windows_by_identity.setdefault(identity, set()).add(window)
    if len(dates) != 1:
        raise AiError("分析请求中的所有来源须使用同一原始比较区间")
    if any(windows != expected_windows for windows in windows_by_identity.values()):
        raise AiError("每个事实来源须完整且仅安排请求的比较窗口；不得缺少或额外添加窗口")


def preview(body, principal):
    current_principal(principal, admin=True)
    from netshop.analysis import SOURCES
    from market.analysis import validate
    from market.errors import MarketApiError
    from .business_evidence import principal_key

    def market_validator(query):
        try:
            validate({"operation": "analysis_records", **query})
        except MarketApiError as error:
            raise AnalysisContractError(str(error)) from error

    try:
        if isinstance(body, dict) and "schemaVersion" in body:
            from business_analysis import planning_v2
            result = planning_v2.preview(body, netshop_sources=SOURCES, market_validator=market_validator)
        else:
            result = planning.preview(body, max_sources=12, max_plan_bytes=16000, max_workflow_bytes=8000,
                netshop_sources=SOURCES, market_validator=market_validator)
    except AnalysisContractError as error:
        raise AiError(str(error)) from error
    return {**result, "principalKey": principal_key(principal)}
