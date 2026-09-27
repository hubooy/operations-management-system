"""Internal sealed-report keyword × promoted-SKU service, not a registered view.

Pure DTOs remain non-authoritative. Only page/read_row return selected-source
authority, after the entire source traversal and final live report check. No
route, Agent tool, report coverage, diagnosis or file-export registration here.
"""
from contextlib import contextmanager
import json
import re

from business_analysis import promotion_keyword_sku
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from . import business_diagnostic_screening as report_binding
from .policy import AiError, canonical, digest, fields, identifier, integer

SCHEMA = "business-promotion-keyword-sku-response-v1"
MAX_RESPONSE_BYTES = 38000
MAX_ROWS = promotion_keyword_sku.LIMITS["maxGroups"]
AUTHORITY = {"completeSourceTraversalForSelectedSources": True, "reportBindingVerified": True,
    "entityDailyCoverageVerified": False, "productMasterIdentityVerified": False,
    "scopeMeaning": "selected_source_view_not_registered_report_coverage"}


def _source(loaded, key):
    key = identifier(key, "sourceKey")
    source = next((item for item in loaded[4] if item["key"] == key), None)
    if source is None:
        raise AiError("来源不在报告固定目录中", "not_found", 404)
    query = source["query"]
    if source["domain"] != "netshop" or query.get("platform") != "京东" or query.get("dataset") != "promotion":
        raise AiError("关键词与明确推广SKU仅支持固定京东推广来源", "unsupported_source_grain", 422)
    expected = loaded[5][key]["expected"]
    return {**source, **{name: expected[name] for name in ("sourceRef", "evidenceDigest")}}, expected


@contextmanager
def table(report_id, source_key, view, principal, *, baseline_key=None, checkpoint=None):
    """Yield (unpublished pure table, fixed binding); normal exit is mandatory."""
    if type(view) is not str or view not in promotion_keyword_sku.VIEWS:
        raise AiError("关键词商品视图无效")
    check = Checkpoint.wrap(checkpoint)
    if check is not None: check({"stage": "keyword_sku_authority", "phase": "start"})
    loaded = report_binding._load(report_id, principal)
    fixed, reader = loaded[:2]
    source, expected = _source(loaded, source_key)
    previous = before = None
    if baseline_key is not None:
        previous, before = _source(loaded, baseline_key)
    kwargs = ({"baseline_source": previous, "baseline_expected": before,
        "baseline_pages": reader.pages(previous["key"])} if previous else {})
    try:
        with promotion_keyword_sku.table(source, reader.pages(source["key"]), expected,
                view=view, checkpoint=check, **kwargs) as result:
            report_binding._revalidate(fixed, principal)
            if check is not None: check({"stage": "keyword_sku_authority", "phase": "validated"})
            binding = {"schemaVersion": "business-promotion-keyword-sku-binding-v1", "reportBinding": fixed,
                "sourceKey": source["key"], "baselineKey": previous["key"] if previous else None,
                "view": view, "algorithmVersion": promotion_keyword_sku.ALGORITHM_VERSION,
                "tableBindingDigest": result.header()["tableBindingDigest"]}
            yield result, json.loads(canonical(binding))
        report_binding._revalidate(fixed, principal)
        if check is not None: check({"stage": "keyword_sku_authority", "phase": "complete"})
    except (AnalysisContractError, KeyError, TypeError, ValueError, UnicodeError, OverflowError) as error:
        if check is not None: check.raise_if_failed()
        raise AiError("关键词商品视图未通过完整封存、范围或行核验", "conflict", 409) from error


def _response(binding, key, value):
    result = {"schemaVersion": SCHEMA, "binding": binding, "bindingDigest": digest(binding),
        "authority": dict(AUTHORITY), key: value}
    result["responseDigest"] = digest(result)
    return result


def _page_response(binding, page):
    # Account for the owning envelope, never silently discard an oversized row.
    while True:
        result = _response(binding, "table", page)
        if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            return result
        if len(page["rows"]) <= 1:
            raise AiError("关键词商品完整行或固定报告信息超过响应容量", "payload_too_large", 413)
        page["rows"].pop()
        pagination = page["pagination"]
        pagination["returned"] = len(page["rows"])
        end = pagination["offset"] + len(page["rows"])
        pagination["nextOffset"] = end if end < pagination["total"] else None
        page["pageDigest"] = digest({key: value for key, value in page.items() if key != "pageDigest"})


def page(report_id, params, principal, *, checkpoint=None):
    """Typed internal selector. Missing source identity stays explicitly missing."""
    fields(params, {"sourceKey", "view", "baselineKey", "offset", "limit"}, {"sourceKey", "view"})
    offset = integer(params.get("offset", 0), "offset", 0, MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(report_id, params["sourceKey"], params["view"], principal,
            baseline_key=params.get("baselineKey"), checkpoint=checkpoint) as (opened, binding):
        result = _page_response(binding, opened.page(offset, 20))
    return result


def read_row(report_id, source_key, view, row_index, row_id, principal, *, baseline_key=None, checkpoint=None):
    row_index = integer(row_index, "rowIndex", 0, MAX_ROWS - 1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None:
        raise AiError("关键词商品行身份格式无效")
    with table(report_id, source_key, view, principal, baseline_key=baseline_key, checkpoint=checkpoint) as (opened, binding):
        result = _response(binding, "row", opened.read_row(row_index, row_id))
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("完整关键词商品引用行超过响应容量", "payload_too_large", 413)
    return result
