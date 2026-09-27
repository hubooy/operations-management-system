"""Closed issue/claim/consume proposal for a future report-capable v4 seal.

Existing 0052 SQL is scoped to the legacy parent seal and cannot be reused.
This contract binds the 0071 report identity to three independently replayed
source roots and a distinct HMAC purpose, then refuses issue because no
finance-shop owner witness or protected v2 signer/consumption ledger exists.
"""
from __future__ import annotations

import re

from .contracts import AnalysisContractError, digest
from .v4_report_signer_preparation_v1 import (SCHEMA as SIGNER_SCHEMA,
    NEW_SEAL_SCHEMA)


SCHEMA = "business-v4-report-seal-transaction-proposal-v1"
OPERATION = "commit-report-capable-v4-seal-v1"
ORDER = ("issue", "claim", "sign", "atomic_commit_and_consume")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="新v4报告seal票据/消费前置证据不足"):
    if not ok:
        raise AnalysisContractError(message)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def prepare_candidate(linked_report, replayed_source, signer_preparation,
                      *, finance_mapping_witness=None, enabled=False):
    """Pin every available digest but issue no ticket or signable body."""
    _need(enabled is True, "新v4报告seal事务协议默认关闭")
    _need(finance_mapping_witness is None,
        "当前版本不接受非拥有方财报同店映射声明")
    linked, source, signer = (linked_report, replayed_source,
        signer_preparation)
    _need(all(type(item) is dict for item in (linked, source, signer)))
    for item in (linked, source):
        _need(item.get("resultDigest") == digest({key: value for key,value
            in item.items() if key != "resultDigest"}))
    _need(signer.get("candidateDigest") == digest({key: value
        for key,value in signer.items() if key != "candidateDigest"}))
    _need(linked.get("schemaVersion") ==
            "business-report-v4-owning-authorization-preflight-v1"
        and linked.get("status") ==
            "blocked_legacy_v4_report_authority"
        and linked.get("sqlCreationTimeLinkRechecked") is True
        and linked.get("currentV4ApplicationHmacRechecked") is True
        and all(linked.get(key) is False for key in (
            "legacySealCanGenerateReport", "v4RowsReferencableIn13Tables",
            "v4VolumesPublishable", "agentCitationSupported",
            "downloadSupported"))
        and source.get("schemaVersion") ==
            "business-v4-report-seal-admission-owning-v1"
        and source.get("status") ==
            "unavailable_report_capable_seal_not_issued"
        and source.get("threeRealOwningStreamsReplayedTwice") is True
        and source.get("legacyApplicationHmacRechecked") is True
        and all(source.get(key) is False for key in (
            "financeShopMappingAuthorityVerified",
            "reportCapableSealIssued", "usableFor13Tables",
            "usableForVolumePublication", "usableForAgentOrDownload"))
        and signer.get("schemaVersion") == SIGNER_SCHEMA
        and signer.get("status") ==
            "blocked_no_finance_mapping_or_new_signer"
        and signer.get("runId") == source.get("runId")
        and signer.get("legacySealedDigest") == source.get(
            "legacySealedDigest")
        and signer.get("ownerDecisionDigest") == source.get(
            "decisionDigest")
        and signer.get("purposeSeparatedFromLegacy") is True
        and signer.get("proposedSealSchema") == NEW_SEAL_SCHEMA
        and signer.get("canonicalSignableBody") is None
        and signer.get("newKeyId") is None
        and signer.get("newBodyMac") is None
        and all(signer.get(key) is False for key in (
            "keyMaterialLoaded", "signerAuthorized",
            "reportCapableSealIssued", "reportGenerationSupported",
            "agentCitationSupported", "rendererRegistered",
            "downloadSupported")))
    _need(linked.get("v4RunId") == source.get("runId")
        and linked.get("v4SealedDigest") == source.get(
            "legacySealedDigest")
        and linked.get("periodPlanDigest") == source.get(
            "periodPlanDigest")
        and linked.get("shop") == source.get("shop")
        and type(linked.get("shop")) is str
        and bool(linked["shop"])
        and linked.get("originalPeriod") == source.get(
            "originalPeriod")
        and type(linked.get("originalPeriod")) is dict
        and set(linked["originalPeriod"]) ==
            {"startDate", "endDate"})
    for item,key in ((linked,"decisionDigest"),
                     (linked,"v2SealedDigest"),
                     (linked,"v4SealedDigest"),
                     (linked,"periodPlanDigest"),
                     (linked,"sourceBindingsDigest"),
                     (source,"decisionDigest"),
                     (source,"threeWindowRootsDigest"),
                     (source,"financeSourceRef"),
                     (signer,"newPurposeSha256")):
        _sha(item.get(key))
    _need(type(source.get("financeSourceRevision")) is str
        and bool(source["financeSourceRevision"]))
    body = {"schemaVersion": SCHEMA,
        "status": "blocked_no_protected_finance_map_or_v2_signer",
        "operation": OPERATION,
        "requiredTransactionOrder": list(ORDER),
        "reportId": linked["reportId"],
        "v4RunId": source["runId"],
        "shop": linked["shop"],
        "originalPeriod": linked["originalPeriod"],
        "v2SealedDigest": linked["v2SealedDigest"],
        "legacyV4SealedDigest": source["legacySealedDigest"],
        "periodPlanDigest": source["periodPlanDigest"],
        "sourceBindingsDigest": linked["sourceBindingsDigest"],
        "threeWindowRootsDigest": source["threeWindowRootsDigest"],
        "financeSourceRef":source["financeSourceRef"],
        "financeSourceRevision":source["financeSourceRevision"],
        "newPurposeSha256": signer["newPurposeSha256"],
        "linkedOwnerResultDigest": linked["resultDigest"],
        "streamOwnerResultDigest": source["resultDigest"],
        "signerPreparationDigest": signer["candidateDigest"],
        "requiredFinanceMappingWitnessDigest": None,
        "signableBody": None,
        "ticketId": None, "nonceHash": None,
        "claimHash": None, "newBodyMac": None,
        "issued": False, "claimed": False,
        "committed": False, "consumed": False,
        "unknownCommitMayRetry": False,
        "commitAndConsumeSameTransactionRequired": True,
        "persistedLedgerVerified": False,
        "authorityVerified": False,
        "reportGenerationSupported": False,
        "rendererRegistered": False,
        "downloadSupported": False}
    return {**body, "proposalDigest": digest(body)}


def inspect_trace(proposal, events, *, enabled=False):
    """No event may start for a blocked proposal, even if self-hashed."""
    _need(enabled is True, "新v4报告seal事务轨迹默认关闭")
    _need(type(proposal) is dict and proposal.get("schemaVersion") == SCHEMA
        and proposal.get("status") ==
            "blocked_no_protected_finance_map_or_v2_signer"
        and proposal.get("proposalDigest") == digest({key: value
            for key,value in proposal.items() if key != "proposalDigest"})
        and proposal.get("requiredTransactionOrder") == list(ORDER)
        and proposal.get("issued") is False
        and proposal.get("claimed") is False
        and proposal.get("committed") is False
        and proposal.get("consumed") is False
        and proposal.get("signableBody") is None
        and proposal.get("requiredFinanceMappingWitnessDigest") is None
        and proposal.get("unknownCommitMayRetry") is False
        and proposal.get("commitAndConsumeSameTransactionRequired") is True)
    _need(type(events) is list and not events,
        "缺映射或受保护signer时任何issue/claim/sign/commit+consume均禁止")
    value = {"schemaVersion":"business-v4-report-seal-trace-candidate-v1",
        "status":"blocked_unissued",
        "proposalDigest":proposal["proposalDigest"],
        "eventCount":0,
        "protectedTicketVerified":False,
        "protectedClaimVerified":False,
        "protectedCommitVerified":False,
        "protectedConsumptionVerified":False,
        "authorityVerified":False}
    return {**value,"resultDigest":digest(value)}
