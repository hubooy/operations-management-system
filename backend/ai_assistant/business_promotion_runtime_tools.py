"""Strict internal HTTP selectors; not a model tool or promotion execution profile."""
import re
from . import business_promotion_keyword_sku as owning
from .policy import AiError, canonical, current_principal, fields, identifier


def _number(value, name, maximum):
    if type(value) is not str or len(value) > 8 or re.fullmatch(r"0|[1-9][0-9]*", value) is None:
        raise AiError(f"{name}必须为规范非负整数")
    result = int(value)
    if result > maximum:
        raise AiError(f"{name}超过范围")
    return result


def read(report_id, params, principal, *, checkpoint=None):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        raise AiError("推广明细仅允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    row_mode = "rowIndex" in params or "rowId" in params
    allowed = {"sourceKey", "view", "baselineKey"} | ({"rowIndex", "rowId"} if row_mode else {"offset", "limit"})
    required = {"sourceKey", "view"} | ({"rowIndex", "rowId"} if row_mode else set())
    fields(params, allowed, required)
    if row_mode:
        result = owning.read_row(report_id, params["sourceKey"], params["view"],
            _number(params["rowIndex"], "rowIndex", owning.MAX_ROWS-1), params["rowId"], principal,
            baseline_key=params.get("baselineKey"), checkpoint=checkpoint)
    else:
        if "limit" in params and params["limit"] != "20":
            raise AiError("推广视图每页固定20行")
        selector = {**params, "offset": _number(params.get("offset", "0"), "offset", owning.MAX_ROWS), "limit": 20}
        result = owning.page(report_id, selector, principal, checkpoint=checkpoint)
    # Preserve the owning envelope, digests and authority scope without projection.
    if len(canonical(result).encode("utf-8")) > owning.MAX_RESPONSE_BYTES:
        raise AiError("完整推广视图超过响应容量", "payload_too_large", 413)
    return result
