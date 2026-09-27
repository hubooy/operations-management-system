"""Closed same-report v4 source authorization preflight.

0071 proves only creation-time identity. The current HMAC-verified v4 seal is
explicitly *not* a report-capable seal. This pure contract can reject mismatched
shop, dates, revisions, missing days or unknown finance mapping, then describe
the remaining versioned authority gap. It never grants a reader or renderer.
"""
from __future__ import annotations

import hashlib
import re

from . import evidence_seal_v4, period_bound_plan_v1
from .contracts import AnalysisContractError, canonical, coverage, digest


SCHEMA = "business-report-v4-authorization-preflight-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS = ("current", "previous", "yearAgo")


def _need(ok, message="v4同报告来源或财报店铺身份无法核验"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def prepare_candidate(report, link, plan, seal_receipt, seal_body_json,
                      *, enabled=False):
    """Require exact current inputs, then return a blocked-only decision.

    Caller-provided booleans and SHA256 values cannot authenticate ownership.
    A future owning service must obtain the report, 0071 reader receipt and
    seal through their independent protected paths before calling this.
    """
    _need(enabled is True, "v4报告授权前置合同默认关闭")
    _need(type(report) is dict and set(report) == {
        "reportId", "ownerEmail", "scope", "reportSnapshotDigest",
        "workflowInputDigest", "v2EvidenceRunId", "v2EvidenceVersion",
        "v2SealedDigest", "shop", "originalPeriod",
        "analysisRequestDigest"})
    _need(type(report["ownerEmail"]) is str and "@" in report["ownerEmail"]
        and report["ownerEmail"] == report["ownerEmail"].lower()
        and report["scope"] is None and report["shop"])
    for key in ("reportSnapshotDigest", "workflowInputDigest",
                "v2SealedDigest", "analysisRequestDigest"):
        _sha(report[key])
    _need(type(link) is dict
        and link.get("schemaVersion") ==
            "business-v4-report-link-read-candidate-v1"
        and link.get("creationTimeLinkPersisted") is True
        and all(link.get(key) is False for key in (
            "appHmacVerified", "authorityVerified",
            "reportGenerationSupported", "agentDispatchSupported",
            "rendererRegistered", "downloadSupported"))
        and link.get("reportId") == report["reportId"]
        and link.get("v2EvidenceRunId") == report["v2EvidenceRunId"]
        and link.get("v2EvidenceVersion") == report["v2EvidenceVersion"]
        and link.get("v2SealedDigest") == report["v2SealedDigest"]
        and link.get("reportSnapshotDigest") ==
            report["reportSnapshotDigest"]
        and link.get("workflowInputDigest") ==
            report["workflowInputDigest"])
    for key in ("v4PlanDigest", "v4SealedDigest",
                "sourceBindingsDigest"):
        _sha(link[key])
    period = period_bound_plan_v1.prepare_candidate(plan)
    _need(link["v4PlanDigest"] == hashlib.sha256(
        canonical(plan).encode("utf-8")).hexdigest()
        and report["analysisRequestDigest"] ==
            period["analysisRequestDigest"])
    raw = seal_body_json
    _need(type(raw) is str)
    body = evidence_seal_v4.read(raw)
    _need(type(seal_receipt) is dict
        and seal_receipt.get("schemaVersion") ==
            "business-v4-internal-seal-verified-v1"
        and seal_receipt.get("proofDigest") == digest({key: value
            for key, value in seal_receipt.items()
            if key != "proofDigest"})
        and seal_receipt.get("internalSealVerified") is True
        and seal_receipt.get("segmentHmacVerified") is True
        and type(seal_receipt.get("sourceRefs")) is list
        and seal_receipt.get("reportGenerationSupported") is False
        and seal_receipt.get("agentDispatchSupported") is False
        and link["v4RunId"] == seal_receipt["runId"] == body["runId"]
        and link["v4EvidenceVersion"] == seal_receipt[
            "evidenceVersion"] == body["evidenceVersion"]
        and link["v4SealedDigest"] == seal_receipt["sealedDigest"]
        == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        and body["planDigest"] == link["v4PlanDigest"]
        and seal_receipt["sourceCount"] == len(body["sources"]) == 4)
    bindings = link.get("sourceBindings")
    _need(type(bindings) is dict
        and bindings.get("schemaVersion") ==
            "business-v4-report-source-bindings-v1"
        and bindings.get("authorityVerified") is False
        and bindings.get("financeShopMappingVerified") is False
        and bindings.get("shop") == report["shop"]
        and report["originalPeriod"] == {
            "startDate": bindings.get("originalStartDate"),
            "endDate": bindings.get("originalEndDate")}
        and type(bindings.get("sources")) is list
        and len(bindings["sources"]) == 4)
    actual = {row["sourceKey"]: row for row in bindings["sources"]}
    seals = {row["sourceKey"]: row for row in body["sources"]}
    refs = {row["sourceKey"]: row for row in seal_receipt["sourceRefs"]}
    planned = {row["sourceKey"]: row for row in plan["sourcePlans"]}
    _need(len(actual) == len(seals) == len(refs) == len(planned) == 4
        and set(actual) == set(seals) == set(refs) == set(planned))
    for key, row in actual.items():
        source, ref, entry = seals[key], refs[key], planned[key]
        _need(row["queryDigest"] == source["queryDigest"] ==
                entry["queryDigest"]
            and row["sourceIdentityDigest"] ==
                entry["sourceIdentityDigest"]
            and row["domain"] == source["domain"] == entry["domain"]
            and row["sourceRef"] == source["sourceRef"] ==
                ref["sourceRef"]
            and row["sourceRevision"] == source[
                "sourceRevision"] == source["liveRevision"] ==
                ref["sourceRevision"] == ref["liveRevision"]
            and source["revisionFreshness"] ==
                ref["verificationFreshness"] == "current_revision"
            and (row["sourceVersion"], row["pageCount"],
                row["rowCount"], row["storedBytes"]) ==
                (source["sourceVersion"], source["pageCount"],
                 source["rowCount"], source["storedBytes"]))
    for window in WINDOWS:
        day = next(row for row in period["dailySources"] if row[
            "window"] == window)
        row = seals[day["sourceKey"]]
        present = row["coverage"].get("presentDates")
        _need(row["window"] == window
            and day["shop"] == report["shop"]
            and {"startDate": day["originalQueryStartDate"],
                "endDate": day["originalQueryEndDate"]} ==
                report["originalPeriod"]
            and type(present) is list
            and row["coverage"] == coverage(
                day["resolvedPeriod"], present)
            and row["coverage"]["missingDates"] == [],
            "v4推广窗口缺日或错店；不能把未知日期视为零")
    finance = period["financeContext"]
    finance_source = seals[finance["sourceKey"]]
    scope = finance_source["scope"]
    _need(finance_source["domain"] == "finance"
        and finance_source["analysisPeriod"] ==
            report["originalPeriod"]
        and finance_source["scope"] == finance["scope"]
        and finance_source["missingMonths"] == []
        and scope == {"scope_key": "shop:京东:" + report["shop"],
            "scope_type": "shop", "scope_name": report["shop"],
            "group_name": scope.get("group_name")},
        "财报范围不是精确店铺或自然月缺失，禁止报告归属")
    value = {"schemaVersion": SCHEMA,
        "status": "blocked_legacy_v4_report_authority",
        "reportId": report["reportId"],
        "v2SealedDigest": report["v2SealedDigest"],
        "v4SealedDigest": link["v4SealedDigest"],
        "periodPlanDigest": period["periodPlanDigest"],
        "sourceBindingsDigest": link["sourceBindingsDigest"],
        "threePromotionWindowsFullyObserved": True,
        "financeScopeExactShopCandidate": True,
        "blockingReasons": [
            "legacy_v4_seal_explicitly_disallows_report_generation",
            "0071_link_is_identity_only_not_hmac_or_report_authority",
            "finance_shop_mapping_lacks_owning_attestation",
            "versioned_v4_report_composition_and_renderer_missing"],
        "ownerHmacAuthenticatedByThisPureContract": False,
        "v4RowsReferencableIn13Tables": False,
        "v4VolumesPublishable": False,
        "agentCitationSupported": False,
        "downloadSupported": False}
    return {**value, "resultDigest": digest(value)}
