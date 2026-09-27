"""Internal report-bound JD promotion views; no route, profile or export registration.

The context yields an unpublished pure table. Callers must finish the context
normally before publishing anything: final authorization can still fail. The
page/row helpers return only after that final check, with selected-source proof,
never a claim that an old report or Agent has adopted these new views.
"""
from contextlib import contextmanager
import json
import re

from business_analysis import promotion_views
from business_analysis.contracts import AnalysisContractError
from . import business_diagnostic_screening as report_binding
from .policy import AiError, canonical, digest, fields, identifier, integer

MAX_RESPONSE_BYTES = 38000
MAX_ROWS = promotion_views.LIMITS["maxGroups"]
SCHEMA = "business-promotion-analysis-response-v1"
AUTHORITY = {"completeSourceTraversalForSelectedSources": True,
    "reportBindingVerified": True, "entityDailyCoverageVerified": False,
    "scopeMeaning": "selected_source_view_not_registered_report_coverage"}


def _source(loaded, key):
    key = identifier(key, "sourceKey")
    source = next((item for item in loaded[4] if item["key"] == key), None)
    if source is None:
        raise AiError("来源不在报告固定目录中", "not_found", 404)
    query = source["query"]
    if source["domain"] != "netshop" or query.get("platform") != "京东" or query.get("dataset") != "promotion":
        raise AiError("计划/单元视图仅支持固定京东推广来源", "unsupported_source_grain", 422)
    expected = loaded[5][key]["expected"]
    return {**source, **{name: expected[name] for name in ("sourceRef", "evidenceDigest")}}, expected


@contextmanager
def table(report_id, source_key, view, principal, *, baseline_key=None):
    """Yield (live pure table, binding); successful authority requires normal exit."""
    if type(view) is not str or view not in promotion_views.VIEWS:
        raise AiError("计划/单元视图无效")
    loaded = report_binding._load(report_id, principal)
    fixed, reader = loaded[:2]
    source, expected = _source(loaded, source_key)
    previous = before = None
    if baseline_key is not None:
        previous, before = _source(loaded, baseline_key)
    kwargs = ({"baseline_source": previous, "baseline_expected": before,
        "baseline_pages": reader.pages(previous["key"])} if previous else {})
    try:
        with promotion_views.table(source, reader.pages(source["key"]), expected, view=view, **kwargs) as result:
            report_binding._revalidate(fixed, principal)
            binding = {"schemaVersion": "business-promotion-analysis-binding-v1", "reportBinding": fixed,
                "sourceKey": source["key"], "baselineKey": previous["key"] if previous else None,
                "view": view, "algorithmVersion": promotion_views.ALGORITHM_VERSION,
                "tableBindingDigest": result.header()["tableBindingDigest"]}
            yield result, json.loads(canonical(binding))
        # Deliberately after the pure context closes, not just before yielding.
        report_binding._revalidate(fixed, principal)
    except (AnalysisContractError, KeyError, TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise AiError("推广视图未通过完整封存、范围或行核验", "conflict", 409) from error


def _response(binding, key, value):
    result = {"schemaVersion": SCHEMA, "binding": binding, "bindingDigest": digest(binding),
        "authority": dict(AUTHORITY), key: value}
    result["responseDigest"] = digest(result)
    return result


def _page_response(binding, page):
    """Account for the owning wrapper too; keep only a complete row prefix."""
    while True:
        result = _response(binding, "table", page)
        if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            return result
        if len(page["rows"]) <= 1:
            raise AiError("推广完整行或固定报告信息超过响应容量", "payload_too_large", 413)
        page["rows"].pop()
        pagination = page["pagination"]
        pagination["returned"] = len(page["rows"])
        end = pagination["offset"] + len(page["rows"])
        pagination["nextOffset"] = end if end < pagination["total"] else None
        page["pageDigest"] = digest({key: value for key, value in page.items() if key != "pageDigest"})


def page(report_id, params, principal):
    """Internal typed parameters: exact integer offset, fixed integer limit=20."""
    fields(params, {"sourceKey", "view", "baselineKey", "offset", "limit"}, {"sourceKey", "view"})
    offset = integer(params.get("offset", 0), "offset", 0, MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(report_id, params["sourceKey"], params["view"], principal,
            baseline_key=params.get("baselineKey")) as (opened, binding):
        result = _page_response(binding, opened.page(offset, 20))
    return result


def read_row(report_id, source_key, view, row_index, row_id, principal, *, baseline_key=None):
    """Resolve an exact row in this report/source/view/baseline, not a free claim."""
    row_index = integer(row_index, "rowIndex", 0, MAX_ROWS-1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None:
        raise AiError("推广行身份格式无效")
    with table(report_id, source_key, view, principal, baseline_key=baseline_key) as (opened, binding):
        rows = opened.page(row_index, 20)["rows"]
        if not rows or rows[0]["rowIndex"] != row_index or rows[0]["id"] != row_id:
            raise AiError("推广行不属于固定视图位置", "conflict", 409)
        result = _response(binding, "row", rows[0])
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("完整推广引用行超过响应容量", "payload_too_large", 413)
    return result
