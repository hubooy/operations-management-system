"""Pure, unregistered market-v2 read envelope and numeric citation shapes."""
import re

from business_analysis import market_numeric_claims
from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.promotion_views import _copy
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-fifth-read-injected-call-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
CALL_FIELDS = {"schemaVersion", "admittedReportId", "jobId", "providerDispatchId",
    "providerCallId", "role", "marketManifestDigest", "marketContextDigest"}


def _need(ok, message="市场v2第五工具注入身份或数值引用无效"):
    if not ok:
        raise AnalysisContractError(message)


def injected(raw):
    """Validate an identity claim; this never proves a persisted job/provider."""
    value = _copy(raw, 4096)
    _need(type(value) is dict and set(value) == CALL_FIELDS
        and value.get("schemaVersion") == SCHEMA
        and value.get("role") in runtime.MARKET_ROLES)
    for key in ("admittedReportId", "jobId", "providerDispatchId"):
        _need(type(value[key]) is str and _ID.fullmatch(value[key]) is not None)
    _need(len({value["admittedReportId"], value["jobId"],
        value["providerDispatchId"]}) == 3)
    _need(type(value["providerCallId"]) is str
        and 1 <= len(value["providerCallId"]) <= 160
        and not any(ord(char) < 32 for char in value["providerCallId"]))
    for key in ("marketManifestDigest", "marketContextDigest"):
        _need(type(value[key]) is str and _SHA.fullmatch(value[key]) is not None)
    return value


def citation_base(call, selector, view, row, table_binding_digest):
    """Server-selected row identity; metric/field require a later row check."""
    call = injected(call)
    _need(type(selector) is dict and view in runtime.TOOL_VIEWS
        and type(row) is dict and type(row.get("rowIndex")) is int
        and 0 <= row["rowIndex"] < runtime.MAX_ROWS
        and type(row.get("rowId")) is str and _SHA.fullmatch(row["rowId"])
        and type(table_binding_digest) is str and _SHA.fullmatch(table_binding_digest))
    base = {"jobId": call["jobId"], "role": call["role"],
        "reportId": call["admittedReportId"],
        "sourceKey": selector["rankCurrentSourceKey"], "view": view,
        "tableBindingDigest": table_binding_digest,
        "rowIndex": row["rowIndex"], "rowId": row["rowId"],
        "attribution": market_numeric_claims.ATTRIBUTION}
    if view == "price_band":
        _need(type(row.get("bandKey")) is str and row["bandKey"])
        base.update(bandsDigest=digest(selector["bands"]),
            bandKey=row["bandKey"])
    else:
        base.update(baselineKey=selector["rankBaselineKey"],
            currentObservationDate=selector["currentObservationDate"],
            baselineObservationDate=selector["baselineObservationDate"])
    return base


def verified_number(base, row, metric, field, *, observation_coverage):
    """Return a candidate reference and owning numeric cell, never a read grant."""
    _need(type(metric) is str and type(field) is str)
    reference = market_numeric_claims.reference({**base, "metric": metric,
        "field": field})
    number = market_numeric_claims.number(reference, row,
        observation_coverage=observation_coverage)
    return {"reference": reference, "number": number,
        "referenceDigest": digest(reference),
        "serverNumericRecomputed": True,
        "sameJobProviderPersisted": False, "persistedRead": False}
