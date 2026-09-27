"""Pure per-role read/capacity candidate; no Agent dispatch or read receipt."""
from __future__ import annotations

from . import report_reference_v3
from .contracts import AnalysisContractError, digest

SCHEMA = "business-v3-agent-read-plan-candidate-v1"
MAX_PREPARED_PAGES = 64
MAX_PREPARED_BYTES = 8 * 1024 * 1024
MAX_ROWS_PER_SLICE = 10


def build(candidate):
    if (type(candidate) is not dict or candidate.get("schemaVersion") != report_reference_v3.SCHEMA
            or candidate.get("candidateDigest") != digest({k: v for k, v in candidate.items()
                if k != "candidateDigest"})
            or candidate.get("modelDispatchSupported") is not False):
        raise AnalysisContractError("Agent读取计划只能从未注册v3准入候选生成")
    daily, finance = candidate["dailyFacts"], candidate["financeMonthlyContext"]
    if type(daily) is not list or type(finance) is not list:
        raise AnalysisContractError("v3来源类型无效")
    all_sources = daily + finance
    if any(type(item) is not dict or type(item.get("sourceKey")) is not str
           or type(item.get("pageCount")) is not int or item["pageCount"] < 1
           for item in all_sources):
        raise AnalysisContractError("v3来源页数无效")
    total_pages = sum(item["pageCount"] for item in all_sources)
    # A single source page may require up to ten 10-row slices. This is an
    # upper bound, not evidence that an Agent actually read any of them.
    def select(role):
        if role in {"independent_review", "report"}:
            return all_sources
        if role == "commerce":
            return [item for item in daily if item["domain"] == "sales" or
                    item["domain"] == "netshop" and item["query"]["dataset"] in {"sku", "spu", "master"}] + finance
        if role == "promotion":
            return [item for item in daily if item["domain"] == "netshop"
                    and item["query"]["dataset"] == "promotion"] + finance
        return [item for item in daily if item["domain"] == "market" or
                item["domain"] == "netshop" and item["query"]["dataset"] == "b2b"]
    roles = []
    for role in report_reference_v3.AGENTS:
        picked = select(role)
        missing = (role == "promotion" and not any(item["domain"] == "netshop" and
            item["query"]["dataset"] == "promotion" for item in picked)
            or role == "market_b2b" and not any(item["domain"] == "market" or
                item["domain"] == "netshop" and item["query"]["dataset"] == "b2b" for item in picked))
        count = sum(item["pageCount"] for item in picked)
        roles.append({"role": role, "requiredSourceKeys": [item["sourceKey"] for item in picked],
            "directoryCompleteRequired": True, "fullSourcePageReadRequired": True,
            "sourcePageCount": count, "maximumSliceCallsAtTenRows": count * 10,
            "missingRequiredDomain": missing,
            "capacitySupportedByPageCount": total_pages <= MAX_PREPARED_PAGES,
            "agentReadReceiptPersisted": False, "dispatchSupported": False})
    base = {"schemaVersion": SCHEMA, "candidateDigest": candidate["candidateDigest"],
        "evidenceRunId": candidate["reference"]["evidenceRunId"],
        "sealedDigest": candidate["reference"]["sealedDigest"],
        "rolePlans": roles, "totalSourcePages": total_pages,
        "maximumPreparedPages": MAX_PREPARED_PAGES,
        "maximumPreparedBytes": MAX_PREPARED_BYTES,
        "maximumRowsPerSlice": MAX_ROWS_PER_SLICE,
        "currentReadBridgeCapacitySupported": total_pages <= MAX_PREPARED_PAGES,
        "byteCapacityMustBeCheckedByOwner": True,
        "humanReviewRequired": True, "agentDispatchSupported": False,
        "agentReadReceiptPersisted": False}
    return {**base, "planDigest": digest(base)}
