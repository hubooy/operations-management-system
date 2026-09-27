"""Closed admission for a future, separately versioned report-capable v4 seal.

The current v4 seal's signed policy says reportGenerationSupported=false. Three
fully replayed promotion windows and a matching finance *label* cannot amend
that signed statement or prove an owning finance-to-shop mapping. This module
records both facts and never signs, registers or authorizes a new seal.
"""
from __future__ import annotations

import hashlib
import re

from . import evidence_seal_v4, period_bound_plan_v1
from .contracts import AnalysisContractError, canonical, coverage, digest


SCHEMA = "business-v4-report-seal-admission-v2-candidate-v1"
WINDOWS = ("current", "previous", "yearAgo")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="v4三期封存页或财报店铺范围不具备报告准入条件"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def inspect_candidate(plan, verified_seal, body_json, stream_receipts,
                      *, finance_mapping_witness=None, enabled=False):
    """Return only an unavailable decision after exact four-source checks.

    Supplying a caller-made mapping witness is explicitly unsupported. A new
    protected owner proof, new signed seal schema and SQL catalogue are needed
    before any future function may return report authority.
    """
    _need(enabled is True, "v4可报告封存准入候选默认关闭")
    _need(finance_mapping_witness is None,
        "当前版本不接受自称财报店铺映射的调用方证明")
    period = period_bound_plan_v1.prepare_candidate(plan)
    _need(type(body_json) is str)
    body = evidence_seal_v4.read(body_json)
    _need(type(verified_seal) is dict
        and verified_seal.get("schemaVersion") ==
            "business-v4-internal-seal-verified-v1"
        and verified_seal.get("proofDigest") == digest({key: value
            for key, value in verified_seal.items()
            if key != "proofDigest"})
        and verified_seal.get("internalSealVerified") is True
        and verified_seal.get("segmentHmacVerified") is True
        and verified_seal.get("reportGenerationSupported") is False
        and verified_seal.get("agentDispatchSupported") is False
        and verified_seal.get("runId") == body["runId"]
        and verified_seal.get("evidenceVersion") == body[
            "evidenceVersion"]
        and verified_seal.get("sealedDigest") == hashlib.sha256(
            body_json.encode("utf-8")).hexdigest()
        and body["planDigest"] == hashlib.sha256(
            canonical(plan).encode("utf-8")).hexdigest()
        and body["sourceCount"] == len(body["sources"]) == 4
        and type(verified_seal.get("sourceRefs")) is list
        and len(verified_seal["sourceRefs"]) == 4)
    sources = {item["sourceKey"]: item for item in body["sources"]}
    refs = {item["sourceKey"]: item for item in
        verified_seal["sourceRefs"]}
    planned = {item["sourceKey"]: item for item in plan["sourcePlans"]}
    _need(len(sources) == len(refs) == len(planned) == 4
        and set(sources) == set(refs) == set(planned))
    for key, source in sources.items():
        reference, entry = refs[key], planned[key]
        _need(source["queryDigest"] == entry["queryDigest"]
            and source["domain"] == entry["domain"]
            and source["sourceRef"] == reference["sourceRef"]
            and source["sourceRevision"] == source[
                "liveRevision"] == reference["sourceRevision"] ==
                reference["liveRevision"]
            and source["revisionFreshness"] ==
                reference["verificationFreshness"] == "current_revision")
    _need(type(stream_receipts) is list
        and len(stream_receipts) == 3)
    receipts = {item.get("window"): item for item in stream_receipts
        if type(item) is dict}
    _need(len(receipts) == 3 and set(receipts) == set(WINDOWS))
    roots = {}
    for window in WINDOWS:
        day = next(item for item in period["dailySources"]
            if item["window"] == window)
        source = sources[day["sourceKey"]]
        receipt = receipts[window]
        observed = source["coverage"].get("presentDates")
        _need(source["window"] == window
            and type(observed) is list
            and source["coverage"] == coverage(day["resolvedPeriod"],
                observed)
            and source["coverage"]["missingDates"] == []
            and receipt.get("schemaVersion") ==
                "business-report-v4-promotion-stream-candidate-v1"
            and receipt.get("resultDigest") == digest({key: value
                for key, value in receipt.items()
                if key != "resultDigest"})
            and receipt.get("twoCompletePassesEqual") is True
            and receipt.get("stagedUnpublished") is True
            and all(receipt.get(key) is False for key in (
                "persistedSameReportLinkVerified",
                "sourceAuthorityVerified", "registeredRenderer",
                "publishable"))
            and (receipt.get("pageCount"), receipt.get("rowCount"),
                receipt.get("storedBytes"),
                receipt.get("receiptChainDigest")) ==
                (source["pageCount"], source["rowCount"],
                 source["storedBytes"],source["receiptChainDigest"])
            and receipt.get("coverageDigest") == digest(source["coverage"]),
            "v4逐页双遍收据、封存控制数或期内日期不一致")
        for key in ("sourceManifestDigest", "rowDigest",
                    "evidenceDigest"):
            _sha(receipt.get(key))
        roots[window] = {"sourceKey": day["sourceKey"],
            "sourceRef": source["sourceRef"],
            "sourceRevision": source["sourceRevision"],
            "pageCount": receipt["pageCount"],
            "rowCount": receipt["rowCount"],
            "rowDigest": receipt["rowDigest"],
            "streamResultDigest": receipt["resultDigest"]}
    finance = period["financeContext"]
    source = sources[finance["sourceKey"]]
    scope = source["scope"]
    shop = period["dailySources"][0]["shop"]
    _need(source["domain"] == "finance"
        and source["scope"] == finance["scope"]
        and source["analysisPeriod"] == finance["analysisPeriod"]
        and source["missingMonths"] == []
        and scope == {"scope_key": "shop:京东:"+shop,
            "scope_type": "shop", "scope_name": shop,
            "group_name": scope.get("group_name")},
        "财报不是精确店铺或自然月缺失，不能映射为店铺销售")
    value = {"schemaVersion": SCHEMA,
        "status": "unavailable_report_capable_seal_not_issued",
        "runId": body["runId"],
        "legacySealedDigest": verified_seal["sealedDigest"],
        "planDigest": body["planDigest"],
        "periodPlanDigest": period["periodPlanDigest"],
        "promotionStreamRoots": roots,
        "financeSourceKey": finance["sourceKey"],
        "financeScopeExactShopCandidate": True,
        "threeWindowsReplayedAndDatesObserved": True,
        "blockingReasons": [
            "legacy_seal_signed_report_generation_false",
            "finance_shop_mapping_has_no_independent_owning_proof",
            "no_versioned_report_capable_seal_or_protected_signer",
            "no_versioned_13_table_or_volume_publication"],
        "reportCapableSealIssued": False,
        "financeShopMappingAuthorityVerified": False,
        "usableForReport": False,
        "usableForAgentCitation": False,
        "usableForRendererOrDownload": False}
    return {**value, "candidateDigest": digest(value)}
