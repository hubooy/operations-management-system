"""Internal owning reader for two exact days in sealed multi-day TOP sources.

This is a report-bound data view, not an Agent tool, public route, file
producer, or proof of own-product sales. Every call replays both full sources.
"""
from contextlib import contextmanager
import json
import re

from business_analysis import market_dynamics, market_dynamics_v2
from business_analysis.contracts import AnalysisContractError
from . import business_diagnostic_screening as report_binding
from . import business_market_dynamics as market_reader
from .policy import AiError, canonical, digest, fields, identifier, integer


SCHEMA = "business-market-observation-response-v2"
MAX_RESPONSE_BYTES = 38000
AUTHORITY = {"completeSourceTraversalForSelectedSources": True,
    "reportBindingVerified": True, "wholeMarketCoverageVerified": False,
    "ownProductIdentityVerified": False,
    "dailyQualificationBasis": "sealed_market_daily_top_from_owning_reader_sql",
    "scopeMeaning": "two_selected_days_of_top_sample_not_whole_market"}


@contextmanager
def table(report_id, current_key, baseline_key, current_day, baseline_day, principal):
    """Yield detached full typed rows only after both sealed streams reconcile."""
    report_id = identifier(report_id, "reportId")
    current_key = identifier(current_key, "currentSourceKey")
    baseline_key = identifier(baseline_key, "baselineSourceKey")
    if current_key == baseline_key:
        raise AiError("观察本期与基期须是不同固定来源")
    try:
        loaded = report_binding._load(report_id, principal)
        fixed, reader = loaded[:2]
        current, expected = market_reader._source(loaded, current_key)
        baseline, before = market_reader._source(loaded, baseline_key)
        value = market_dynamics_v2.rank_entry_exit(current,
            reader.pages(current_key), expected, baseline,
            reader.pages(baseline_key), before, current_day, baseline_day)
        if value.get("tableDigest") != digest({key: child for key, child in value.items()
                if key != "tableDigest"}):
            raise AiError("市场两日完整表摘要不一致", "conflict", 409)
        binding = {"schemaVersion": "business-market-observation-binding-v2",
            "reportBinding": fixed, "currentSourceKey": current_key,
            "baselineSourceKey": baseline_key,
            "currentObservationDate": current_day,
            "baselineObservationDate": baseline_day,
            "algorithmVersion": market_dynamics_v2.ALGORITHM_VERSION,
            "sourceProofDigests": {current_key: digest(expected),
                baseline_key: digest(before)},
            "tableBindingDigest": value["tableDigest"]}
        report_binding._revalidate(fixed, principal)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("市场观察未通过同报告封存来源和单日核验", "conflict", 409) from error
    yield json.loads(canonical(value)), json.loads(canonical(binding))
    report_binding._revalidate(fixed, principal)


def _response(binding, key, value):
    result = {"schemaVersion": SCHEMA, "binding": binding,
        "bindingDigest": digest(binding), "authority": dict(AUTHORITY), key: value}
    result["responseDigest"] = digest(result)
    return result


def page(report_id, params, principal):
    required = {"currentSourceKey", "baselineSourceKey",
        "currentObservationDate", "baselineObservationDate"}
    fields(params, required | {"offset", "limit"}, required)
    offset = integer(params.get("offset", 0), "offset", 0, market_dynamics.MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(report_id, params["currentSourceKey"], params["baselineSourceKey"],
            params["currentObservationDate"], params["baselineObservationDate"],
            principal) as (value, binding):
        total = len(value["rows"])
        if offset > total:
            raise AiError("市场观察偏移超出完整范围")
        selected = [{**row, "rowIndex": offset+index}
            for index, row in enumerate(value["rows"][offset:offset+20])]
        while True:
            end = offset+len(selected)
            output = {key: child for key, child in value.items() if key != "rows"}
            output.update(rows=selected, pagination={"offset": offset,
                "returned": len(selected), "total": total,
                "nextOffset": end if end < total else None})
            output["pageDigest"] = digest(output)
            result = _response(binding, "table", output)
            if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
                break
            if len(selected) <= 1:
                raise AiError("市场观察单行或元信息超过响应容量", "payload_too_large", 413)
            selected.pop()
    return result


def read_row(report_id, current_key, baseline_key, current_day, baseline_day,
             row_index, row_id, principal):
    integer(row_index, "rowIndex", 0, market_dynamics.MAX_ROWS-1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None:
        raise AiError("市场观察行摘要无效")
    with table(report_id, current_key, baseline_key, current_day,
            baseline_day, principal) as (value, binding):
        if row_index >= len(value["rows"]) or value["rows"][row_index]["rowId"] != row_id:
            raise AiError("市场观察行不属于固定报告、日期或位置", "conflict", 409)
        result = _response(binding, "row", {**value["rows"][row_index],
            "rowIndex": row_index})
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("市场观察单行超过响应容量", "payload_too_large", 413)
    return result
