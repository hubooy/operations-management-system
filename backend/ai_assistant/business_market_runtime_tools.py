"""Strict internal market GET selectors; no Agent/tool/profile registration."""
import json
import re
from business_analysis.contracts import MAX_SAFE_INTEGER
from . import business_market_dynamics as owning
from .policy import AiError, canonical, current_principal, fields, identifier


def _number(value, name, maximum):
    if type(value) is not str or len(value) > 8 or re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise AiError(f"{name}必须为规范非负整数")
    result = int(value)
    if result > maximum: raise AiError(f"{name}超过范围")
    return result


def _bands(raw):
    if type(raw) is not str: raise AiError("价格段必须是明确JSON文本")
    try:
        if len(raw.encode("utf-8")) > 8192: raise AiError("价格段JSON超过8192字节", "payload_too_large", 413)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError("duplicate key")
                result[key] = value
            return result
        def invalid_constant(_): raise ValueError("nonfinite JSON")
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("价格段JSON无效或含重复字段") from error
    if type(value) is not list or not 1 <= len(value) <= 20: raise AiError("价格段须为1—20段")
    for band in value:
        fields(band, {"key", "lowerCents", "upperExclusiveCents"}, {"key", "lowerCents", "upperExclusiveCents"})
        if type(band["key"]) is not str or not 1 <= len(band["key"]) <= 100: raise AiError("价格段名称无效")
        if type(band["lowerCents"]) is not int or not 0 <= band["lowerCents"] <= MAX_SAFE_INTEGER: raise AiError("价格段下界无效")
        upper = band["upperExclusiveCents"]
        if upper is not None and (type(upper) is not int or not 0 <= upper <= MAX_SAFE_INTEGER): raise AiError("价格段上界无效")
    return value


def read(report_id, params, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None: raise AiError("市场动态仅允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    if type(params) is not dict or params.get("view") not in ("price_band", "rank_entry_exit"):
        raise AiError("市场动态视图无效")
    row_mode = "rowIndex" in params or "rowId" in params
    common = {"sourceKey", "view", "bands" if params["view"] == "price_band" else "baselineKey"}
    fields(params, common | ({"rowIndex", "rowId"} if row_mode else {"offset", "limit"}),
           common | ({"rowIndex", "rowId"} if row_mode else set()))
    bands = _bands(params["bands"]) if params["view"] == "price_band" else None
    if row_mode:
        result = owning.read_row(report_id, params["sourceKey"], params["view"],
            _number(params["rowIndex"], "rowIndex", owning.market_dynamics.MAX_ROWS-1), params["rowId"], principal,
            baseline_key=params.get("baselineKey"), bands=bands)
    else:
        if params.get("limit", "20") != "20": raise AiError("市场动态每页固定20行")
        selected = {"sourceKey": params["sourceKey"], "view": params["view"],
            "offset": _number(params.get("offset", "0"), "offset", owning.market_dynamics.MAX_ROWS), "limit": 20}
        if bands is not None: selected["bands"] = bands
        else: selected["baselineKey"] = params["baselineKey"]
        result = owning.page(report_id, selected, principal)
    if len(canonical(result).encode("utf-8")) > owning.MAX_RESPONSE_BYTES:
        raise AiError("完整市场动态响应超过容量", "payload_too_large", 413)
    return result
