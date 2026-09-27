"""Internal mapped references; public/native diagnosis remains unchanged.

The fixed plan is supplied by trusted report code. This is not an endpoint and
does not make a caller-provided plan an immutable report snapshot.
"""
import re

from business_analysis.mapped_results import METRICS, MAX_GROUPS
from .policy import AiError, fields, integer

REFERENCE_FIELDS = {"pairKey", "baselinePairKey", "dimension", "rowIndex", "rowId", "metric", "field"}
VALUE_FIELDS = {"value", "baseline", "difference", "changeRate"}


def _hash(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise AiError("映射引用身份无效")
    return value


def resolve(reference, evidence_id, fixed_plan, principal):
    """Recompute one exact fact; absent values must be expressed as a gap."""
    from . import business_mapped_analysis

    fields(reference, REFERENCE_FIELDS, REFERENCE_FIELDS-{"baselinePairKey"})
    for key in ("pairKey", "baselinePairKey", "rowId"):
        if key in reference:
            _hash(reference[key])
    index = integer(reference["rowIndex"], "rowIndex", lo=0, hi=MAX_GROUPS-1)
    if (type(reference["dimension"]) is not str or reference["dimension"] not in {"sku", "spu"}
            or type(reference["metric"]) is not str or reference["metric"] not in METRICS
            or type(reference["field"]) is not str or reference["field"] not in VALUE_FIELDS):
        raise AiError("映射引用维度或指标无效")
    if reference["field"] != "value" and "baselinePairKey" not in reference:
        raise AiError("比较引用须包含固定基期来源对")
    with business_mapped_analysis.table(evidence_id, fixed_plan, reference["pairKey"],
            reference["dimension"], principal, baseline_pair_key=reference.get("baselinePairKey")) as result:
        page = result.page(offset=index, limit=1)
        if len(page["rows"]) != 1 or page["rows"][0]["id"] != reference["rowId"]:
            raise AiError("映射结论引用的行不存在或身份不符", "conflict", 409)
        row, metric, field = page["rows"][0], reference["metric"], reference["field"]
        if field == "value":
            entry = row["metrics"].get(metric) or {}
            value, partial = entry.get("value"), bool(entry.get("missingRows"))
        else:
            value = row["comparisons"].get(metric, {}).get(field)
            partial = bool(((row.get("baselineMetrics") or {}).get(metric) or {}).get("missingRows")) if field == "baseline" else False
        if value is None:
            raise AiError("映射引用数值不可用，须改为数据缺口", "conflict", 409)
        fact = {"reference": dict(reference), "value": value, "partial": partial,
            "entity": row["entity"], "sourceRef": page["source"]["sourceRef"],
            "evidenceDigest": page["source"]["evidenceDigest"],
            "coverage": page["sourceMetadata"]["coverage"],
            "mappingBindingDigest": page["bindingDigest"], "mappingBinding": page["binding"],
            "baselineMappingBinding": page["baselineBinding"],
            "historicalMapping": False, "limitations": page["limitations"]}
    # The context's late permission/version check must succeed before return.
    return fact
