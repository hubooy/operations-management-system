"""Owning, unregistered followed-SKU relation over one sealed JD report.

Only this selected-source view receives report/account binding after full page
replay. It is not an Agent tool, product-master join, report-coverage proof, or
permission to add its amounts to the promoted-SKU views.
"""
from contextlib import contextmanager
import json
import re

from business_analysis import promotion_attributed_sku_relation as relation
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from . import business_diagnostic_screening as report_binding
from . import business_promotion_keyword_sku as promoted_owner
from .policy import AiError, canonical, digest, fields, integer


SCHEMA = "business-promotion-attributed-sku-owning-candidate-v1"
MAX_RESPONSE_BYTES = 38000
MAX_ROWS = relation.LIMITS["maxGroups"]
AUTHORITY = {
    "completeSourceTraversalForSelectedSources": True,
    "reportBindingVerified": True,
    "entityDailyCoverageVerified": False,
    "productMasterIdentityVerified": False,
    "agentReadPersisted": False,
    "registeredReportCoverage": False,
    "scopeMeaning": "selected_source_attributed_sku_view_only",
}


@contextmanager
def table(report_id, source_key, view, principal, *, baseline_key=None,
          checkpoint=None):
    if type(view) is not str or view not in relation.VIEWS:
        raise AiError("跟单SKU关系视图无效", "invalid_request", 400)
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage": "attributed_sku_authority", "phase": "start"})
    loaded = report_binding._load(report_id, principal)
    fixed, reader = loaded[:2]
    source, expected = promoted_owner._source(loaded, source_key)
    previous = before = None
    if baseline_key is not None:
        previous, before = promoted_owner._source(loaded, baseline_key)
    kwargs = ({"baseline_source": previous,
        "baseline_expected": before,
        "baseline_pages": reader.pages(previous["key"], checkpoint=check)}
        if previous else {})
    try:
        with relation.table(source, reader.pages(source["key"], checkpoint=check),
                expected, view=view, checkpoint=check, **kwargs) as opened:
            report_binding._revalidate(fixed, principal)
            if check is not None:
                check({"stage": "attributed_sku_authority",
                    "phase": "validated"})
            binding = {"schemaVersion":
                    "business-promotion-attributed-sku-binding-v1",
                "reportBinding": fixed, "sourceKey": source["key"],
                "baselineKey": previous["key"] if previous else None,
                "view": view, "algorithmVersion": relation.ALGORITHM_VERSION,
                "tableBindingDigest": opened.header()["tableBindingDigest"]}
            yield opened, json.loads(canonical(binding))
        report_binding._revalidate(fixed, principal)
        if check is not None:
            check({"stage": "attributed_sku_authority", "phase": "complete"})
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        if check is not None:
            check.raise_if_failed()
        raise AiError("跟单SKU关系未通过完整封存、范围或行核验",
            "conflict", 409) from error


def _response(binding, key, value):
    result = {"schemaVersion": SCHEMA, "binding": binding,
        "bindingDigest": digest(binding), "authority": dict(AUTHORITY),
        key: value}
    result["responseDigest"] = digest(result)
    return result


def _page_response(binding, page):
    # A wide source row fails closed; shortening a page preserves nextOffset.
    while True:
        result = _response(binding, "table", page)
        if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            return result
        if len(page["rows"]) <= 1:
            raise AiError("跟单SKU完整行或报告绑定超过响应容量",
                "payload_too_large", 413)
        page["rows"].pop()
        pagination = page["pagination"]
        pagination["returned"] = len(page["rows"])
        end = pagination["offset"] + len(page["rows"])
        pagination["nextOffset"] = end if end < pagination["total"] else None
        page["pageDigest"] = digest({key: value for key, value in page.items()
            if key != "pageDigest"})


def page(report_id, params, principal, *, checkpoint=None):
    fields(params, {"sourceKey", "view", "baselineKey", "offset", "limit"},
        {"sourceKey", "view"})
    offset = integer(params.get("offset", 0), "offset", 0, MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(report_id, params["sourceKey"], params["view"], principal,
            baseline_key=params.get("baselineKey"),
            checkpoint=checkpoint) as (opened, binding):
        return _page_response(binding, opened.page(offset, 20))


def read_row(report_id, source_key, view, row_index, row_id, principal, *,
             baseline_key=None, checkpoint=None):
    row_index = integer(row_index, "rowIndex", 0, MAX_ROWS - 1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None:
        raise AiError("跟单SKU行身份格式无效", "invalid_request", 400)
    with table(report_id, source_key, view, principal,
            baseline_key=baseline_key,
            checkpoint=checkpoint) as (opened, binding):
        result = _response(binding, "row", opened.read_row(row_index, row_id))
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("跟单SKU精确行超过响应容量",
                "payload_too_large", 413)
        return result
