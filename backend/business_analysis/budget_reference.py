"""Immutable budget-reference contracts; no persistence or model admission.

Pages must be built from a freshly resolved, trusted sealed-evidence result.
Recomputing arithmetic here detects mutations, but does not authenticate facts.
"""
import json
import re

from . import budget
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .planning import validate_analysis_request

BINDING_SCHEMA = "business-budget-binding-v1"
REFERENCE_SCHEMA = "business-budget-reference-v1"
PAGE_SCHEMA = "business-budget-page-v1"
PROFILE = "business-agent-budget-reference-v1"
CALCULATOR_VERSION = "business-budget-calculator-v1"
CAPACITY_PROFILE = "budget-parameters-v1"
MAX_PLAN_BYTES = 48000
MAX_BINDING_BYTES = 4096
MAX_PAGE_BYTES = 38000
OWNER_BYTES = 8 * 1024 * 1024
OWNER_ROWS = 200
GLOBAL_BYTES = 64 * 1024 * 1024
GLOBAL_ROWS = 2000
BINDING_FIELDS = frozenset({"schemaVersion", "capacityProfile", "calculatorVersion", "reportId", "ownerEmail", "scopeDigest",
    "evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "analysisRequestDigest", "planDigest"})


def _fail(message="固定预算引用合同无效"):
    raise AnalysisContractError(message)


def _copy(value, maximum, *, depth=0, count=None):
    """Bound caller containers before serialization; reject cycles and aliases by size."""
    count = [0, 0] if count is None else count
    count[0] += 1
    if depth > 12 or count[0] > 100000:
        _fail("固定预算结构超过容量")
    if type(value) is dict:
        if len(value) > 100 or any(type(k) is not str or len(k) > 160 for k in value):
            _fail()
        try:
            count[1] += 2 + max(0, len(value)-1) + sum(len(canonical(k).encode("utf-8"))+1 for k in value)
        except (ValueError, TypeError, UnicodeError) as error:
            raise AnalysisContractError("固定预算JSON无效") from error
        if count[1] > maximum: _fail("固定预算JSON超过字节容量")
        result = {k: _copy(v, maximum, depth=depth+1, count=count) for k, v in value.items()}
    elif type(value) is list:
        if len(value) > 1000:
            _fail()
        count[1] += 2 + max(0, len(value)-1)
        if count[1] > maximum: _fail("固定预算JSON超过字节容量")
        result = [_copy(v, maximum, depth=depth+1, count=count) for v in value]
    elif value is None or type(value) in (bool, int, float, str):
        if type(value) is str and len(value) > maximum:
            _fail("固定预算文本超过容量")
        if type(value) is int and not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _fail("固定预算整数超过精确范围")
        result = value
        try:
            count[1] += len(canonical(value).encode("utf-8"))
        except (ValueError, TypeError, UnicodeError) as error:
            raise AnalysisContractError("固定预算JSON无效") from error
        if count[1] > maximum: _fail("固定预算JSON超过字节容量")
    else:
        _fail()
    if depth == 0:
        try:
            encoded = canonical(result)
            if len(encoded.encode("utf-8")) > maximum:
                _fail("固定预算JSON超过字节容量")
        except (ValueError, TypeError, UnicodeError) as error:
            raise AnalysisContractError("固定预算JSON无效") from error
    return result


def _id(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        _fail("固定预算身份无效")
    return value


def _sha(value):
    if type(value) is not str or not re.fullmatch(r"[a-f0-9]{64}", value):
        _fail("固定预算摘要无效")
    return value


def normalize_plan(value):
    return budget.normalize(_copy(value, MAX_PLAN_BYTES))


def validate_binding(value):
    value = _copy(value, MAX_BINDING_BYTES)
    if type(value) is not dict or set(value) != BINDING_FIELDS:
        _fail("固定预算绑定字段无效")
    if (value["schemaVersion"] != BINDING_SCHEMA or value["capacityProfile"] != CAPACITY_PROFILE
            or value["calculatorVersion"] != CALCULATOR_VERSION):
        _fail("固定预算版本不支持")
    for field in ("reportId", "evidenceRunId"):
        _id(value[field])
    if type(value["evidenceVersion"]) is not int or not 1 <= value["evidenceVersion"] <= MAX_SAFE_INTEGER:
        _fail("固定预算证据版本无效")
    owner = value["ownerEmail"]
    if type(owner) is not str or not 1 <= len(owner) <= 320 or owner != owner.lower() or owner != owner.strip() or any(ord(c) < 33 for c in owner):
        _fail("固定预算所有者无效")
    for field in ("scopeDigest", "evidencePlanDigest", "catalogDigest", "sealedDigest", "planDigest"):
        _sha(value[field])
    if value["scopeDigest"] != digest(None):
        _fail("固定预算仅支持无范围限制的管理员")
    if value["analysisRequestDigest"] is not None:
        _sha(value["analysisRequestDigest"])
    return value


def make_binding(plan, *, report_id, owner_email, scope, evidence_run_id, evidence_version,
                 evidence_plan_digest, catalog_digest, sealed_digest, analysis_request=None):
    plan = normalize_plan(plan)
    if scope is not None:
        _fail("固定预算仅支持无范围限制的管理员")
    request_digest = None if analysis_request is None else digest(validate_analysis_request(analysis_request))
    return validate_binding({"schemaVersion": BINDING_SCHEMA, "capacityProfile": CAPACITY_PROFILE,
        "calculatorVersion": CALCULATOR_VERSION, "reportId": report_id, "ownerEmail": owner_email,
        "scopeDigest": digest(scope), "evidenceRunId": evidence_run_id, "evidenceVersion": evidence_version,
        "evidencePlanDigest": evidence_plan_digest, "catalogDigest": catalog_digest, "sealedDigest": sealed_digest,
        "analysisRequestDigest": request_digest, "planDigest": digest(plan)})


def make_reference(plan_id, binding):
    binding = validate_binding(binding)
    return {"schemaVersion": REFERENCE_SCHEMA, "id": _id(plan_id), "planDigest": binding["planDigest"], "bindingDigest": digest(binding)}


def validate_record(plan, binding, reference, *, expected_binding):
    """A caller must supply a binding rebuilt from authorized, sealed evidence."""
    plan, binding = normalize_plan(plan), validate_binding(binding)
    expected = validate_binding(expected_binding)
    reference = _copy(reference, MAX_BINDING_BYTES)
    if type(reference) is not dict or set(reference) != {"schemaVersion", "id", "planDigest", "bindingDigest"}:
        _fail("固定预算引用字段无效")
    if (canonical(binding) != canonical(expected) or digest(plan) != binding["planDigest"]
            or canonical(reference) != canonical(make_reference(reference["id"], binding))):
        _fail("固定预算参数与可信证据绑定不一致")
    return {"plan": plan, "binding": binding, "reference": reference}


def page(result, binding, *, budget_ref, report_id, offset=0, limit=10):
    """Return a complete row prefix; caller resolves evidence before every call."""
    binding = validate_binding(binding)
    if report_id != binding["reportId"]:
        _fail("预算页报告身份不一致")
    if type(offset) is not int or not 0 <= offset < 100 or type(limit) is not int or not 1 <= limit <= 20:
        _fail("预算分页无效")
    result = _copy(result, 2*1024*1024)
    try:
        verified = validate_record(result["plan"], binding, budget_ref, expected_binding=binding)
        baselines = [row["baseline"] for row in result["scenarios"][0]["rows"]]
        rebuilt = {**budget.calculate(verified["plan"], baselines), "evidenceRunId": binding["evidenceRunId"],
            "evidenceVersion": binding["evidenceVersion"], "evidencePlanDigest": binding["evidencePlanDigest"]}
        if canonical(rebuilt) != canonical(result):
            _fail("预算计算结果与固定参数不一致")
        total = result["allocation"]["targetCount"]
        if offset >= total:
            _fail("预算分页越界")
        outcomes = {"scenario", "projectedClicks", "projectedOrderLines", "projectedAttributedGmvCents", "projectedRoas", "assumedContributionAfterAdCents"}
        rows = []
        def assemble():
            end = offset+len(rows)
            value = {"schemaVersion": PAGE_SCHEMA, "reportId": report_id, "budgetRef": verified["reference"],
                "binding": binding, "allocation": result["allocation"],
                "scenarios": [{"assumptions": s["assumptions"], "summary": s["summary"]} for s in result["scenarios"]],
                "rows": rows, "pagination": {"offset": offset, "limit": limit, "total": total, "returned": len(rows),
                    "nextOffset": end if end < total else None}, "limitations": result["limitations"]}
            return {**value, "pageDigest": digest(value)}
        for index in range(offset, min(offset+limit, total)):
            item = result["scenarios"][0]["rows"][index]
            row = {"rowIndex": index, **{k: v for k, v in item.items() if k not in outcomes},
                "outcomes": [{k: v for k, v in s["rows"][index].items() if k in outcomes} for s in result["scenarios"]]}
            rows.append(row)
            if len(canonical(assemble()).encode("utf-8")) > MAX_PAGE_BYTES:
                rows.pop()
                if not rows:
                    _fail("单个预算对象超过工具字节容量，不得截断")
                break
        return json.loads(canonical(assemble()))
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
        raise AnalysisContractError("预算结果结构无效") from error
