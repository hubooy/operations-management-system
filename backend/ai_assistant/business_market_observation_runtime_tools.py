"""Exact signed-reader selectors for internal market observation v2."""
import re

from business_analysis.contracts import AnalysisContractError, strict_date
from . import business_market_observation as owning
from .policy import AiError, canonical, current_principal, fields, identifier


def _integer(value, name, maximum):
    if type(value) is not str or len(value) > 8 or re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise AiError(f"{name}须为规范非负整数")
    result = int(value)
    if result > maximum:
        raise AiError(f"{name}超过范围")
    return result


def _date(value, name):
    try:
        return strict_date(value).isoformat()
    except AnalysisContractError as error:
        raise AiError(f"{name}须为明确日期") from error


def read(report_id, params, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        raise AiError("市场观察只允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    if type(params) is not dict:
        raise AiError("市场观察查询参数无效")
    common = {"currentSourceKey", "baselineSourceKey",
        "currentObservationDate", "baselineObservationDate"}
    row_mode = "rowIndex" in params or "rowId" in params
    fields(params, common | ({"rowIndex", "rowId"} if row_mode else {"offset", "limit"}),
        common | ({"rowIndex", "rowId"} if row_mode else set()))
    current_key = identifier(params["currentSourceKey"], "currentSourceKey")
    baseline_key = identifier(params["baselineSourceKey"], "baselineSourceKey")
    current_day = _date(params["currentObservationDate"], "currentObservationDate")
    baseline_day = _date(params["baselineObservationDate"], "baselineObservationDate")
    if row_mode:
        result = owning.read_row(report_id, current_key, baseline_key,
            current_day, baseline_day,
            _integer(params["rowIndex"], "rowIndex", 199999),
            params["rowId"], principal)
    else:
        if params.get("limit", "20") != "20":
            raise AiError("市场观察每页固定20行")
        result = owning.page(report_id, {"currentSourceKey": current_key,
            "baselineSourceKey": baseline_key,
            "currentObservationDate": current_day,
            "baselineObservationDate": baseline_day,
            "offset": _integer(params.get("offset", "0"), "offset", 200000),
            "limit": 20}, principal)
    if len(canonical(result).encode("utf-8")) > owning.MAX_RESPONSE_BYTES:
        raise AiError("完整市场观察响应超过容量", "payload_too_large", 413)
    return result
