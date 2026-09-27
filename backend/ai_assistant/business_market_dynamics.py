"""Internal complete sealed-market views; no route, model or export registration.

The context yields unpublished copies. Only page/read_row return after final
live report/actor validation, and never assert whole-market coverage.
"""
from contextlib import contextmanager
import json
import re

from business_analysis import market_dynamics
from business_analysis.contracts import AnalysisContractError
from business_analysis.promotion_views import _copy
from . import business_diagnostic_screening as report_binding
from .policy import AiError, canonical, digest, fields, identifier, integer

MAX_RESPONSE_BYTES = 38000
SCHEMA = "business-market-dynamics-response-v1"
AUTHORITY = {"completeSourceTraversalForSelectedSources": True, "reportBindingVerified": True,
    "wholeMarketCoverageVerified": False, "ownProductIdentityVerified": False,
    "dailyQualificationBasis": "sealed_market_daily_top_from_owning_reader_sql",
    "scopeMeaning": "selected_top_sample_not_registered_report_coverage"}


def _source(loaded, key):
    key = identifier(key, "sourceKey")
    source = next((s for s in loaded[4] if s["key"] == key), None)
    if source is None: raise AiError("来源不在固定报告中", "not_found", 404)
    if source["domain"] != "market" or source["query"].get("platform") != "京东":
        raise AiError("市场动态仅支持固定京东市场样本", "unsupported_source_grain", 422)
    return {k: source[k] for k in ("key", "domain", "query")}, loaded[5][key]["expected"]


@contextmanager
def table(report_id, source_key, view, principal, *, baseline_key=None, bands=None):
    if view not in ("price_band", "rank_entry_exit") or type(view) is not str:
        raise AiError("市场动态视图无效")
    if (view == "price_band" and (bands is None or baseline_key is not None)
            or view == "rank_entry_exit" and (baseline_key is None or bands is not None)):
        raise AiError("价格段或进出榜基期参数不完整/不兼容")
    try:
        fixed_bands = _copy(bands, 8192) if bands is not None else None
        loaded = report_binding._load(report_id, principal)
        fixed, reader = loaded[:2]
        source, expected = _source(loaded, source_key)
        baseline, before = _source(loaded, baseline_key) if baseline_key is not None else (None, None)
        if baseline is not None:
            a, b = source["query"], baseline["query"]
            if not (source["key"] != baseline["key"] and a["window"] == "current" and b["window"] in ("previous", "yearAgo")
                    and a["startDate"] == a["endDate"] and {k:v for k,v in a.items() if k != "window"} == {k:v for k,v in b.items() if k != "window"}):
                raise AiError("市场进出榜基期、榜单身份或单日窗口不一致", "conflict", 409)
        if view == "price_band":
            value = market_dynamics.price_band(source, reader.pages(source["key"]), expected, fixed_bands)
        else:
            value = market_dynamics.rank_entry_exit(source, reader.pages(source["key"]), expected,
                baseline, reader.pages(baseline["key"]), before)
        if value.get("tableDigest") != digest({k:v for k,v in value.items() if k != "tableDigest"}):
            raise AiError("市场动态完整表摘要不匹配", "conflict", 409)
        binding = {"schemaVersion": "business-market-dynamics-binding-v1", "reportBinding": fixed,
            "sourceKey": source["key"], "baselineKey": baseline["key"] if baseline else None,
            "view": view, "algorithmVersion": market_dynamics.ALGORITHM_VERSION,
            "bandsDigest": digest(fixed_bands) if fixed_bands is not None else None,
            "tableBindingDigest": value["tableDigest"]}
        report_binding._revalidate(fixed, principal)
    except (AnalysisContractError, KeyError, TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise AiError("市场动态未通过完整来源和区间核验", "conflict", 409) from error
    # Separate copies prevent caller edits from changing the final live fence.
    yield json.loads(canonical(value)), json.loads(canonical(binding))
    report_binding._revalidate(fixed, principal)


def _response(binding, key, value):
    result = {"schemaVersion": SCHEMA, "binding": binding, "bindingDigest": digest(binding),
              "authority": dict(AUTHORITY), key: value}
    result["responseDigest"] = digest(result)
    return result


def page(report_id, params, principal):
    fields(params, {"sourceKey", "view", "baselineKey", "bands", "offset", "limit"}, {"sourceKey", "view"})
    offset = integer(params.get("offset", 0), "offset", 0, market_dynamics.MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(report_id, params["sourceKey"], params["view"], principal,
               baseline_key=params.get("baselineKey"), bands=params.get("bands")) as (value, binding):
        total = len(value["rows"])
        if offset > total: raise AiError("市场动态偏移超出完整范围")
        selected = [{**row, "rowIndex": offset+i} for i,row in enumerate(value["rows"][offset:offset+20])]
        while True:
            end = offset+len(selected)
            output = {k:v for k,v in value.items() if k != "rows"}
            output.update(rows=selected, pagination={"offset": offset, "returned": len(selected),
                "total": total, "nextOffset": end if end < total else None})
            # tableDigest remains the complete pure table root, pageDigest covers this slice.
            output["pageDigest"] = digest(output)
            result = _response(binding, "table", output)
            if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES: break
            if len(selected) <= 1: raise AiError("市场动态完整单行或元数据超过响应容量", "payload_too_large", 413)
            selected.pop()
    return result


def read_row(report_id, source_key, view, row_index, row_id, principal, *, baseline_key=None, bands=None):
    integer(row_index, "rowIndex", 0, market_dynamics.MAX_ROWS-1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None: raise AiError("市场行摘要无效")
    with table(report_id, source_key, view, principal, baseline_key=baseline_key, bands=bands) as (value, binding):
        if row_index >= len(value["rows"]) or value["rows"][row_index]["rowId"] != row_id:
            raise AiError("市场动态行不属于固定视图位置", "conflict", 409)
        result = _response(binding, "row", {**value["rows"][row_index], "rowIndex": row_index})
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("完整市场行超过响应容量", "payload_too_large", 413)
    return result
