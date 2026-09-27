"""Separate, non-authorizing source-rate and human-cap adoption proposals.

The 0065 SHA claims and 0069 synthetic rehearsal are not independently
verified authorities.  This contract fixes bytes for two different protected
owners to inspect later, without converting either proposal into a grant.
"""
import re

from business_analysis.contracts import AnalysisContractError, digest

from . import business_market_v2_paid_authority_contract as rehearsal
from .business_market_v2_round_reservation_contract import _candidate


RATE_SCHEMA = "business-market-v2-source-rate-adoption-proposal-v1"
CAP_SCHEMA = "business-market-v2-human-cap-adoption-proposal-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
REPORT_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场v2费率或人工上限采纳提案与执行根不一致")


def build(plan_receipt, candidate, cost_ledger_id, source, approval,
          owner_version, *, at_utc):
    """Split exact data into two pending proposals; never accept approval bits."""
    value = _candidate(candidate)
    _need(type(plan_receipt) is dict and type(plan_receipt.get("executionRoot"))
        is dict and plan_receipt.get("planId") == value["planId"]
        and type(cost_ledger_id) is str and HEX.fullmatch(cost_ledger_id)
        is not None and type(owner_version) is int and owner_version >= 1)
    root = plan_receipt["executionRoot"]
    report_id, owner = root.get("executionReportId"), root.get("ownerEmail")
    _need(type(approval) is dict
        and type(report_id) is str and REPORT_ID.fullmatch(report_id) is not None
        and type(owner) is str and owner == owner.lower() and "@" in owner
        and len(owner) <= 320 and approval.get("actorEmail") == owner)
    checked = rehearsal.build(value, source, approval, at_utc=at_utc)
    rate = {"schemaVersion": RATE_SCHEMA,
        "planId": value["planId"], "executionReportId": report_id,
        "costLedgerId": cost_ledger_id, "ownerEmail": owner,
        "ownerVersion": owner_version,
        "modelId": value["model"]["id"],
        "modelVersion": value["model"]["version"],
        "candidateDigest": value["candidateDigest"],
        "tariffDigest": value["tariffDigest"],
        "source": source, "sourceDigest": checked["sourceDigest"],
        "chargeCategories": list(rehearsal.CATEGORIES),
        "sourceIndependentlyVerified": False,
        "fxIndependentlyVerified": False,
        "allBillingCategoriesVerified": False,
        "status": "pending_independent_source_verification",
        "providerCallsAllowed": False}
    rate = {**rate, "rateProposalDigest": digest(rate)}
    cap = {"schemaVersion": CAP_SCHEMA,
        "planId": value["planId"], "executionReportId": report_id,
        "costLedgerId": cost_ledger_id, "ownerEmail": owner,
        "ownerVersion": owner_version,
        "candidateDigest": value["candidateDigest"],
        "rateProposalDigest": rate["rateProposalDigest"],
        "approval": approval, "approvalDigest": checked["approvalDigest"],
        "approvedCapClaimCents": value["approvedCapClaimCents"],
        "humanApprovalVerified": False,
        "status": "pending_explicit_human_approval",
        "providerCallsAllowed": False}
    cap = {**cap, "capProposalDigest": digest(cap)}
    return {"rate": rate, "cap": cap, "providerCallsAllowed": False}
