"""Unregistered monthly finance material from one paused sealed-v3 report intent.

The signed owning bridge supplies every immutable chunk slice. No Agent,
model, public route, file renderer or daily/SKU attribution is enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

from business_analysis import cross_source_window_compare as window_compare
from business_analysis import finance_collection_state, finance_source
from business_analysis.contracts import AnalysisContractError, MAX_SAFE_INTEGER

from . import business_v3_report_intent as intents
from . import business_v3_source_read as bridge
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-finance-v3-report-monthly-material-candidate-v1"
MAX_MATERIAL_BYTES = bridge.MAX_PREPARED_BYTES
MAX_SOURCE_PAGES = bridge.MAX_PREPARED_PAGES
_TOKEN = object()


def _need(ok, message="v3财报材料与同一封存报告意图或完整页链不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


@dataclass(frozen=True, slots=True, init=False)
class PreparedFinanceV3Material:
    _value_json: str

    def __init__(self, token, value):
        if token is not _TOKEN:
            raise AiError("财报材料只能由同报告完整封存重放创建", "conflict", 409)
        object.__setattr__(self, "_value_json", canonical(value))

    @property
    def value(self):
        return json.loads(self._value_json)


def _baseline(month, step):
    ordinal = int(month[:4]) * 12 + int(month[5:]) - 1 - step
    return f"{ordinal // 12:04d}-{ordinal % 12 + 1:02d}"


def _compare(current, baseline, kind):
    if baseline is None:
        return {"status": "outside_selected_months", "currentStatus": current["status"],
            "baselineStatus": None, "difference": None, "growthRateBps": None}
    if current["status"] != "present" or baseline["status"] != "present":
        return {"status": "incomplete_monthly_metric",
            "currentStatus": current["status"], "baselineStatus": baseline["status"],
            "difference": None, "growthRateBps": None}
    left, right = current["value"], baseline["value"]
    difference = left - right
    if abs(difference) > MAX_SAFE_INTEGER:
        return {"status": "difference_out_of_range",
            "currentStatus": "present", "baselineStatus": "present",
            "difference": None, "growthRateBps": None}
    if kind == "rate":
        return {"status": "rate_basis_point_delta_only",
            "currentStatus": "present", "baselineStatus": "present",
            "difference": difference, "growthRateBps": None}
    compared = window_compare._comparison(
        {"status": "observed_rows", "value": left, "missingRows": 0},
        {"status": "observed_rows", "value": right, "missingRows": 0})
    _need(compared["difference"] == difference,
        "月金额差额与安全比率合同不一致")
    return {"status": compared["status"], "currentStatus": "present",
        "baselineStatus": "present", "difference": difference,
        "growthRateBps": compared["growthRateBps"]}


def _comparisons(coverage):
    months = {item["month"]: item for item in coverage}
    result = {}
    for month, item in months.items():
        result[month] = {}
        for metric, kind in finance_source.CORE_METRICS.items():
            current = item["metrics"][metric]
            result[month][metric] = {
                "unit": current["unit"],
                "previous": {"baselineMonth": _baseline(month, 1),
                    **_compare(current,
                        months.get(_baseline(month, 1), {}).get("metrics", {}).get(metric),
                        kind)},
                "yearAgo": {"baselineMonth": _baseline(month, 12),
                    **_compare(current,
                        months.get(_baseline(month, 12), {}).get("metrics", {}).get(metric),
                        kind)}}
    return result


def _source(intent_id, source_key, principal):
    directory = bridge.directory(intent_id, {"offset": 0}, principal)
    _need(directory["fullSealVerifiedForHandle"] is True
        and directory["currentReadBridgeCapacitySupported"] is True
        and directory["agentReadReceiptRecorded"] is False)
    items = list(directory["items"])
    while directory["nextOffset"] is not None:
        directory = bridge.directory(intent_id, {"offset": directory["nextOffset"],
            "handle": directory["handle"]}, principal)
        items.extend(directory["items"])
    matching = [item for item in items if item["sourceKey"] == source_key]
    _need(len(matching) == 1 and matching[0]["domain"] == "finance",
        "财报来源不是此封存报告意图中的唯一所选月度来源")
    item = matching[0]
    _need(1 <= item["pageCount"] <= MAX_SOURCE_PAGES
        and item["rowCount"] <= finance_source.MAX_ROWS
        and item["queryDigest"] == digest(item["query"])
        and item["coverage"]["kind"] == "natural_month_publication")
    return item, directory["sealedDigest"], directory["runId"]


def _full_page(intent_id, item, sequence, principal):
    offset, rows, metadata, chunk_digest = 0, [], None, None
    while True:
        part = bridge.page(intent_id, item["sourceKey"], {
            "handle": item["sourceHandle"], "sequence": sequence,
            "rowOffset": offset, "rowLimit": bridge.MAX_ROWS}, principal)
        _need(part["runId"] == item["runId"]
            and part["sourceKey"] == item["sourceKey"]
            and part["domain"] == "finance"
            and part["queryDigest"] == item["queryDigest"]
            and part["sourceRef"] == item["sourceRef"]
            and part["sourceRevision"] == item["sourceRevision"]
            and part["receiptChainDigest"] == item["receiptChainDigest"]
            and part["sequence"] == sequence
            and part["rowOffset"] == offset
            and part["returned"] == len(part["rows"])
            and part["fullSealVerifiedForHandle"] is True
            and part["agentReadReceiptRecorded"] is False,
            "v3财报切片跨报告、来源、修订或工具收据")
        if metadata is None:
            metadata, chunk_digest = part["metadata"], part["chunkDigest"]
        else:
            _need(part["metadata"] == metadata and part["chunkDigest"] == chunk_digest
                and part["totalRowsInChunk"] == total, "同一财报页切片元数据变化")
        total = part["totalRowsInChunk"]
        _need(0 <= total <= 100 and len(rows) + len(part["rows"]) <= total)
        rows.extend(part["rows"])
        next_offset = part["nextRowOffset"]
        if next_offset is None:
            _need(len(rows) == total)
            break
        _need(next_offset == len(rows) and next_offset > offset)
        offset = next_offset
    page = {**metadata, "rows": rows}
    _need(digest(canonical(page)) == chunk_digest,
        "v3财报切片重组后不是封存的原始事实页")
    return page


def prepare(intent_id, source_key, principal):
    """Complete one selected monthly source, or fail without partial output."""
    intent_id, source_key = identifier(intent_id), identifier(source_key)
    try:
        item, sealed_digest, run_id = _source(intent_id, source_key, principal)
        item = {**item, "runId": run_id}
        query = item["query"]
        state, records, stored_bytes = None, [], 0
        for sequence in range(1, item["pageCount"] + 1):
            page = _full_page(intent_id, item, sequence, principal)
            stored_bytes += len(canonical(page).encode("utf-8"))
            _need(stored_bytes <= MAX_MATERIAL_BYTES,
                "当前v3读取桥无法完整承载此财报，不返回截断月份")
            state = finance_collection_state.consume(state, page,
                trusted_query=query)
            records.extend(page["rows"])
        monthly = finance_collection_state.result(state, trusted_query=query)
        actor, intent, parent, seal = bridge._context(intent_id, principal)
        proof = next((value for value in seal["sources"]
            if value["sourceKey"] == source_key), None)
        _need(proof is not None and intent.id == intent_id
            and parent.id == run_id and parent.status == "sealed"
            and seal["sealedDigest"] == sealed_digest
            and proof["domain"] == "finance"
            and proof["queryDigest"] == item["queryDigest"]
            and proof["sourceRef"] == monthly["sourceRef"] == item["sourceRef"]
            and proof["sourceRevision"] == monthly["sourceRevision"] == item["sourceRevision"]
            and proof["pageCount"] == monthly["pageCount"] == item["pageCount"]
            and proof["rowCount"] == monthly["rowCount"] == len(records)
            and proof["storedBytes"] == monthly["storedBytes"] == stored_bytes
            and proof["receiptChainDigest"] == item["receiptChainDigest"]
            and proof["coverage"] == item["coverage"]
            and proof["coverage"]["publicationDigest"] ==
                digest(monthly["publication"])
            and proof["coverage"]["detailedCoverageDigest"] ==
                digest(monthly["coverage"])
            and monthly["periodAlignment"]["dailyProrationAllowed"] is False,
            "完整财报页、自然月覆盖或同报告收据链不一致")
        missing = [row["month"] for row in monthly["coverage"]
            if row["published"] is False]
        _need(missing == item["coverage"]["missingMonths"])
        result = {"schemaVersion": SCHEMA, "intentId": intent_id,
            "evidenceRunId": run_id, "sealedDigest": sealed_digest,
            "sourceKey": source_key, "sourceRef": monthly["sourceRef"],
            "sourceRevision": monthly["sourceRevision"],
            "sourceQuery": query, "scope": query["scope"],
            "analysisPeriod": query["analysisPeriod"],
            "financePeriodAlignment": monthly["periodAlignment"],
            "naturalMonths": query["months"], "missingMonths": missing,
            "publication": monthly["publication"],
            "monthlyCoverage": monthly["coverage"],
            "comparisons": _comparisons(monthly["coverage"]),
            "rows": records, "rowCount": len(records),
            "pageCount": monthly["pageCount"], "storedBytes": stored_bytes,
            "rowChainDigest": monthly["rowChainDigest"],
            "pageChainDigest": monthly["pageChainDigest"],
            "receiptChainDigest": proof["receiptChainDigest"],
            "sealedSelectedSourceFullyReplayed": True,
            "signedOwningBridgeVerified": True,
            "agentReadReceiptRecorded": False,
            "upstreamSourceSignatureVerified": False,
            "crossDomainSnapshotAtomic": False,
            "financeDailyProrationAllowed": False,
            "financeSkuProfitAttributionAllowed": False,
            "sumOverlappingErpB2bAdsAllowed": False,
            "reportGenerationSupported": False}
        _need(len(canonical(result).encode("utf-8")) <= MAX_MATERIAL_BYTES,
            "v3财报完整报告材料超过8MiB候选容量")
        final = intents.inspect(intent_id, principal)
        _need(final["item"]["evidenceRunId"] == run_id
            and final["item"]["sealedDigest"] == sealed_digest
            and final["candidate"]["reference"]["sealedDigest"] == sealed_digest
            and final["candidate"]["reference"]["evidenceRunId"] == run_id
            and actor["email"] == principal.email.lower(),
            "v3财报返回前账号或暂停报告意图改变")
        result["resultDigest"] = digest(result)
        _need(len(canonical(result).encode("utf-8")) <= MAX_MATERIAL_BYTES,
            "v3财报完整材料和摘要超过8MiB候选容量")
        return PreparedFinanceV3Material(_TOKEN, result)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError, RecursionError) as error:
        raise AiError("v3财报月度材料未通过完整封存页或数值口径核验",
            "conflict", 409) from error
