"""Read-only first-budget selection and calculation over sealed v2 facts."""
import json
import re

from business_analysis import budget, budget_reference
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import build_table
from . import business_budget, business_evidence, business_evidence_store as store
from .business_sealed import Reader
from .policy import AiError, canonical, current_principal, digest, fields, identifier

MAX_TARGET_BYTES = 38000
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
BINDING_FIELDS = frozenset({"evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest"})


def _sealed(run_id, principal):
    current_principal(principal, admin=True)
    row = business_evidence.get_run(run_id, principal)
    if row.status != "sealed" or not store.is_v2(row):
        raise AiError("首次预算需要已封存的 v2 证据", "conflict", 409)
    reader = Reader(row, principal)
    try:
        header, seal = json.loads(row.plan_json), json.loads(row.state_json)
        binding = {"evidenceRunId": row.id, "evidenceVersion": row.version,
            "evidencePlanDigest": digest(row.plan_json), "catalogDigest": header["catalogDigest"],
            "sealedDigest": seal["sealedDigest"]}
    except (ValueError, TypeError, KeyError) as error:
        raise AiError("封存预算绑定无效", "conflict", 409) from error
    return row, reader, binding


def _size(value):
    try:
        return len(canonical(value).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("预算响应无法完整编码", "conflict", 409) from error


def _finish(row, principal):
    current_principal(principal, admin=True)
    # Re-authorize the current owner/scope and reject a stale read before return.
    actual = business_evidence.get_run(row.id, principal)
    if (actual.version, actual.plan_json, actual.state_json, actual.status) != (
            row.version, row.plan_json, row.state_json, "sealed"):
        raise AiError("预算读取期间证据绑定变化", "version_conflict", 409)
    store.assert_current(row)


def targets(run_id, query, principal):
    fields(query, {"sourceKey", "dimension", "offset", "limit"}, {"sourceKey", "dimension"})
    key = identifier(query["sourceKey"])
    dimension = query["dimension"]
    if type(dimension) is not str or dimension not in budget.DIMENSIONS:
        raise AiError("预算对象维度无效")
    raw_offset = query.get("offset", "0")
    if (type(raw_offset) is not str or len(raw_offset) > 6
            or re.fullmatch(r"0|[1-9][0-9]*", raw_offset) is None
            or int(raw_offset) > 249999 or query.get("limit", "20") != "20"):
        raise AiError("预算对象分页无效；页长固定为 20")
    offset = int(raw_offset)
    row, reader, binding = _sealed(run_id, principal)
    source = next((s for s in reader.sources if s["key"] == key), None)
    if (source is None or source["domain"] != "netshop" or source["query"].get("dataset") != "promotion"
            or source["query"].get("window", "current") != "current"):
        raise AiError("预算对象只能选择本期推广来源", "invalid_request", 400)
    try:
        table = build_table(reader.pages(key), dimension, reader.info(key)["expected"], offset=offset, limit=20)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("预算对象未通过完整来源核验", "conflict", 409) from error
    if offset > table["total"]:
        raise AiError("预算对象偏移超出完整结果")
    result = {"schemaVersion": "business-budget-targets-v1", "evidenceBinding": binding,
        "sourceKey": key, "dimension": dimension, "sourceMetadata": table["sourceMetadata"], "rows": [],
        "pagination": {"offset": offset, "limit": 20, "total": table["total"],
            "hasMore": offset < table["total"], "nextOffset": offset if offset < table["total"] else None}}
    if _size(result) > MAX_TARGET_BYTES:
        raise AiError("预算对象来源说明超过单页容量", "payload_too_large", 413)
    for item in table["rows"]:
        result["rows"].append(item)
        end = offset + len(result["rows"])
        result["pagination"].update(hasMore=end < table["total"], nextOffset=end if end < table["total"] else None)
        if _size(result) > MAX_TARGET_BYTES:
            result["rows"].pop()
            end -= 1
            result["pagination"].update(hasMore=end < table["total"], nextOffset=end if end < table["total"] else None)
            if not result["rows"]:
                raise AiError("预算对象单行超过完整交付容量", "payload_too_large", 413)
            break
    _finish(row, principal)
    return result


def preview(run_id, body, principal):
    fields(body, {"evidenceBinding", "budgetPlan"}, {"evidenceBinding", "budgetPlan"})
    supplied = body["evidenceBinding"]
    fields(supplied, BINDING_FIELDS, BINDING_FIELDS)
    if (type(supplied["evidenceVersion"]) is not int or supplied["evidenceVersion"] < 1
            or type(supplied["evidenceRunId"]) is not str or supplied["evidenceRunId"] != run_id
            or any(type(supplied[k]) is not str or re.fullmatch(r"[a-f0-9]{64}", supplied[k]) is None
                   for k in ("evidencePlanDigest", "catalogDigest", "sealedDigest"))):
        raise AiError("预算证据绑定无效", "conflict", 409)
    row, _reader, binding = _sealed(run_id, principal)
    if supplied != binding:
        raise AiError("预算证据绑定已变化，请重新选择对象", "version_conflict", 409)
    try:
        plan = budget_reference.normalize_plan(body["budgetPlan"])
        calculated = business_budget.resolve(run_id, plan, principal)
    except AnalysisContractError as error:
        raise AiError(str(error), "invalid_request", 400) from error
    if any(calculated.get(k) != binding[k] for k in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest")):
        raise AiError("预算计算结果证据绑定不一致", "conflict", 409)
    result = {"schemaVersion": "business-budget-preview-v1", "previewOnly": True,
        "evidenceBinding": binding, "budget": calculated}
    if _size(result) > MAX_PREVIEW_BYTES:
        raise AiError("完整预算预览超过 2 MiB 容量", "payload_too_large", 413)
    _finish(row, principal)
    return result
