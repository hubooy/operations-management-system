"""Non-authorizing identity contract for a future same-report v4 source bridge.

The sealed-v2 report and v4 run have separate ledgers. Even a matching intent,
three windows and verified v4 seal cannot retroactively attach v4 facts to an
existing report. A future SQL-owned link must be fixed when a new report is
created and rechecked under the report/source roles before any row is read.
"""
from __future__ import annotations

import json
import re

from . import business_promotion_v4_plan, evidence_v2, period_bound_plan_v1
from .contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-report-v4-source-bridge-candidate-v1"
INTENT_SCHEMA = "business-report-v4-bridge-declared-intent-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="v4来源与同报告声明意图、三期或封存摘要不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _copy(value, maximum):
    try:
        raw = canonical(value)
        _need(len(raw.encode("utf-8")) <= maximum)
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("v4同报告桥输入无法规范编码") from error


def _id(value):
    _need(type(value) is str and _ID.fullmatch(value) is not None)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _intent(value):
    item = _copy(value, 12_000)
    _need(type(item) is dict and set(item) == {
        "schemaVersion", "reportId", "ownerEmail", "scope",
        "clientRequestId", "reportSnapshotDigest", "workflowInputDigest",
        "v2EvidenceRunId", "v2EvidenceVersion", "v2SealedDigest",
        "analysisRequest", "platform", "shop", "originalPeriod",
        "v4RunId", "v4PlanDigest", "v4SealedDigest",
        "v4PeriodPlanDigest", "promotionSources", "financeSource",
        "declaredAtReportCreation",
        "intentDigest"} and item["schemaVersion"] == INTENT_SCHEMA)
    for key in ("reportId", "clientRequestId", "v2EvidenceRunId", "v4RunId"):
        _id(item[key])
    for key in ("reportSnapshotDigest", "workflowInputDigest",
                "v2SealedDigest", "v4PlanDigest", "v4SealedDigest",
                "v4PeriodPlanDigest"):
        _sha(item[key])
    _need(type(item["ownerEmail"]) is str
        and item["ownerEmail"] == item["ownerEmail"].strip().lower()
        and "@" in item["ownerEmail"] and len(item["ownerEmail"]) <= 320
        and item["scope"] is None
        and type(item["v2EvidenceVersion"]) is int
        and item["v2EvidenceVersion"] > 0
        and item["platform"] == "京东"
        and type(item["shop"]) is str and item["shop"]
        and item["declaredAtReportCreation"] is True
        and item["intentDigest"] == digest({key: cell for key, cell in
            item.items() if key != "intentDigest"}))
    item["analysisRequest"] = evidence_v2.validate_analysis_request(
        item["analysisRequest"])
    _need(type(item["originalPeriod"]) is dict
        and set(item["originalPeriod"]) == {"startDate", "endDate"}
        and type(item["promotionSources"]) is list
        and len(item["promotionSources"]) == 3
        and type(item["financeSource"]) is dict)
    return item


def _seal(value, plan, intent):
    seal = _copy(value, 38_000)
    _need(type(seal) is dict and set(seal) == {
        "schemaVersion", "runId", "evidenceVersion", "sealedDigest",
        "attemptId", "sourceCount", "sourceRefs", "internalSealVerified",
        "segmentHmacVerified", "upstreamSignatureVerified",
        "crossDomainSnapshotAtomic", "financeDailyProrationAllowed",
        "inferSkuProfit", "sumOverlappingErpB2bAdsAllowed",
        "reportGenerationSupported", "agentDispatchSupported", "proofDigest"})
    _id(seal["runId"]); _id(seal["attemptId"])
    _sha(seal["sealedDigest"])
    _need(seal["schemaVersion"] == "business-v4-internal-seal-verified-v1"
        and seal["runId"] == intent["v4RunId"]
        and type(seal["evidenceVersion"]) is int
        and seal["evidenceVersion"] > 0
        and seal["internalSealVerified"] is True
        and seal["segmentHmacVerified"] is True
        and all(seal[key] is False for key in (
            "upstreamSignatureVerified", "crossDomainSnapshotAtomic",
            "financeDailyProrationAllowed", "inferSkuProfit",
            "sumOverlappingErpB2bAdsAllowed", "reportGenerationSupported",
            "agentDispatchSupported"))
        and seal["proofDigest"] == digest({key: cell for key, cell in
            seal.items() if key != "proofDigest"})
        and type(seal["sourceRefs"]) is list
        and seal["sourceCount"] == len(seal["sourceRefs"])
        == len(plan["sourcePlans"]))
    seen = set()
    for planned, reference in zip(plan["sourcePlans"], seal["sourceRefs"]):
        _need(type(reference) is dict and set(reference) == {
            "sourceKey", "sourceRef", "sourceRevision", "liveRevision",
            "verificationFreshness"})
        _id(reference["sourceKey"]); _sha(reference["sourceRef"])
        _need(reference["sourceKey"] == planned["sourceKey"]
            and reference["sourceKey"] not in seen
            and type(reference["sourceRevision"]) is str
            and 1 <= len(reference["sourceRevision"]) <= 128
            and reference["sourceRevision"] == reference["liveRevision"]
            and reference["verificationFreshness"] == "current_revision")
        seen.add(reference["sourceKey"])
    return seal


def prepare_candidate(report_intent, v4_plan, verified_v4_seal, *,
                      enabled=False):
    """Build exact expected link bytes, with no authority to read or publish.

    ``report_intent`` must eventually come from a new immutable report record,
    never from a caller or a retroactively edited sealed-v2 snapshot. This
    pure check cannot establish that provenance, so its result stays closed.
    """
    _need(enabled is True, "v4同报告来源桥默认关闭")
    intent = _intent(report_intent)
    plan = business_promotion_v4_plan._plan(v4_plan)
    period = period_bound_plan_v1.prepare_candidate(plan)
    seal = _seal(verified_v4_seal, plan, intent)
    current = next(row for row in period["dailySources"]
        if row["window"] == "current")
    _need(intent["clientRequestId"] == plan["clientRequestId"]
        and intent["v4PlanDigest"] == plan["planDigest"]
        and intent["v4SealedDigest"] == seal["sealedDigest"]
        and intent["v4PeriodPlanDigest"] == period["periodPlanDigest"]
        and intent["analysisRequest"] == plan["analysisRequest"]
        and intent["shop"] == current["shop"]
        and intent["originalPeriod"] == {
            "startDate": current["originalQueryStartDate"],
            "endDate": current["originalQueryEndDate"]})
    references = {row["sourceKey"]: row for row in seal["sourceRefs"]}
    windows = []
    for row in sorted(period["dailySources"], key=lambda item:
            ("current", "previous", "yearAgo").index(item["window"])):
        ref = references[row["sourceKey"]]
        windows.append({"window": row["window"],
            "sourceKey": row["sourceKey"], "queryDigest": row["queryDigest"],
            "sourceIdentityDigest": row["sourceIdentityDigest"],
            "sourceRef": ref["sourceRef"],
            "sourceRevision": ref["sourceRevision"],
            "resolvedPeriod": row["resolvedPeriod"],
            "expectedDayDigest": row["expectedDayDigest"]})
    _need(intent["promotionSources"] == [{key: row[key] for key in (
        "window", "sourceKey", "queryDigest", "sourceRef",
        "sourceRevision")} for row in windows],
        "报告创建时声明的三期来源或修订与当前v4封存不同")
    finance = period["financeContext"]
    finance_ref = references[finance["sourceKey"]]
    _need(intent["financeSource"] == {
        "sourceKey": finance["sourceKey"],
        "queryDigest": finance["queryDigest"],
        "sourceRef": finance_ref["sourceRef"],
        "sourceRevision": finance_ref["sourceRevision"]},
        "报告创建时声明的财报来源或修订与当前v4封存不同")
    body = {"schemaVersion": SCHEMA,
        "reportId": intent["reportId"],
        "reportIntentDigest": intent["intentDigest"],
        "reportSnapshotDigest": intent["reportSnapshotDigest"],
        "workflowInputDigest": intent["workflowInputDigest"],
        "v2EvidenceRunId": intent["v2EvidenceRunId"],
        "v2EvidenceVersion": intent["v2EvidenceVersion"],
        "v2SealedDigest": intent["v2SealedDigest"],
        "v4RunId": seal["runId"],
        "v4EvidenceVersion": seal["evidenceVersion"],
        "v4PlanDigest": plan["planDigest"],
        "v4SealedDigest": seal["sealedDigest"],
        "v4SealProofDigest": seal["proofDigest"],
        "periodPlanDigest": period["periodPlanDigest"],
        "analysisRequestDigest": digest(plan["analysisRequest"]),
        "shop": intent["shop"], "platform": "京东",
        "originalPeriod": intent["originalPeriod"],
        "promotionWindows": windows,
        "financeContextSource": intent["financeSource"],
        "candidateOnly": True, "persistedSameReportLinkVerified": False,
        "v4RowsReadableForReport": False, "agentCitationSupported": False,
        "registeredRenderer": False, "publishable": False}
    return {**body, "candidateDigest": digest(body)}
