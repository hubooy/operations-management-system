"""Resolve one keyword/SKU numeric claim against this Agent's saved reads.

This proves a referenced number was read and can still be recomputed. It does
not prove the Agent read its complete role package or that its explanation is
causal, approved, or ready for delivery.
"""
from copy import deepcopy
import math
import re

from . import business_promotion_keyword_sku as owning
from . import business_promotion_read_receipts as receipts
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from .policy import AiError, canonical, fields


def _reject(message="词货数值引用未通过本人持久读取证明"):
    raise AiError(message, "promotion_reference_unverified", 409)


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _reject("词货行或表摘要不是完整 SHA-256")


def _reference(value):
    required = {"kind", "sourceKey", "view", "rowIndex", "rowId",
        "tableBindingDigest", "metric", "field"}
    fields(value, required | {"baselineKey"}, required)
    if value["kind"] != "promotion_keyword_sku":
        _reject("不是词货结构化数值引用")
    for key in ("rowId", "tableBindingDigest"):
        _sha(value[key])
    if (type(value["rowIndex"]) is not int or not 0 <= value["rowIndex"] < owning.MAX_ROWS
            or type(value["sourceKey"]) is not str
            or type(value["view"]) is not str or value["view"] not in contract.PROMOTION_VIEWS
            or type(value["metric"]) is not str or value["metric"] not in contract.MONEY_METRICS
            or type(value["field"]) is not str or value["field"] not in contract.VALUE_FIELDS
            or ("baselineKey" in value and type(value["baselineKey"]) is not str)
            or (value["field"] != "value" and "baselineKey" not in value)):
        _reject("词货数值引用范围或字段无效")
    return value


def _number(row, metric, field):
    current = (row.get("metrics") or {}).get(metric)
    baseline = (row.get("baselineMetrics") or {}).get(metric)
    if field == "value":
        number = current.get("value") if current else None
    elif field == "baseline":
        number = baseline.get("value") if baseline else None
    else:
        number = (row.get("comparisons") or {}).get(metric, {}).get(field)
    if type(number) not in (int, float) or (type(number) is float and not math.isfinite(number)):
        _reject("词货引用数值不可用，须披露为数据缺口")
    return number, bool((current or {}).get("missingRows") or
        (field != "value" and (baseline or {}).get("missingRows")))


def resolve(job, reference, principal):
    """Re-read one exact numeric fact, with a durable same-job row receipt."""
    reference = _reference(reference)
    proof = receipts.progress(job, principal)
    if proof["jobId"] != job.id or proof["role"] not in contract.PROMOTION_ROLES:
        _reject("此角色不能引用词货数值")
    fixed = runtime.bound_persisted(proof["reportId"], principal)
    selector = fixed["promotionSelector"]
    if (proof["screeningId"] != fixed["screeningId"]
            or proof["promotionSelector"] != selector
            or reference["sourceKey"] != selector["sourceKey"]
            or ("baselineKey" in reference) != ("baselineKey" in selector)
            or ("baselineKey" in selector and reference["baselineKey"] != selector["baselineKey"])):
        _reject("词货数值引用不属于本人固定报告和基期")
    view = proof["views"][reference["view"]]
    if not any(item["rowIndex"] == reference["rowIndex"]
            and item["rowId"] == reference["rowId"]
            and item["tableBindingDigest"] == reference["tableBindingDigest"]
            for item in view["seenRows"]):
        _reject("本人持久工具回执没有读取该词货行")
    found = owning.read_row(proof["reportId"], reference["sourceKey"], reference["view"],
        reference["rowIndex"], reference["rowId"], principal,
        baseline_key=reference.get("baselineKey"))
    row = found["row"]
    if (found["binding"]["tableBindingDigest"] != reference["tableBindingDigest"]
            or found["binding"]["reportBinding"]["reportId"] != proof["reportId"]
            or row["rowIndex"] != reference["rowIndex"] or row["id"] != reference["rowId"]):
        _reject("词货行与持久回执的表绑定不同")
    number, partial = _number(row, reference["metric"], reference["field"])
    if (canonical(receipts.progress(job, principal)) != canonical(proof)
            or canonical(runtime.bound_persisted(proof["reportId"], principal)) != canonical(fixed)):
        _reject("词货数值核验期间持久读取或报告根已变化")
    return {"reference": deepcopy(reference), "value": number, "partial": partial,
        "entity": deepcopy(row["entity"]),
        "identityQualified": row["identityQualified"],
        "actionableKeywordSku": row["identityQualified"],
        "comparisonStatus": (row.get("comparisons") or {}).get(reference["metric"], {}).get("status"),
        "reportId": proof["reportId"], "screeningId": proof["screeningId"],
        "jobId": proof["jobId"], "role": proof["role"],
        "sourceRef": found["binding"],
        "verification": {"numericReferenceVerified": True,
            "referenceReadVerified": True, "completeAgentReadingVerified": False,
            "causalityVerified": False, "humanReviewRequired": True},
        "limitations": ["关键词×推广SKU与计划上下文两表是同一推广事实的不同分组，费用不可相加",
            "平台归因成交不是ERP净销售或增量利润；缺身份桶不可作为具体词货调整对象"]}
