"""Pure requirements for a future genuine same-job market-v2 tool read.

This module validates identities and recomputes a candidate numeric cell. It
cannot manufacture a persisted read, provider call, or citation authority.
"""
import re

from business_analysis import market_numeric_claims
from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.promotion_views import _copy
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-same-job-read-requirements-v1"
RECEIPT_SCHEMA = "business-market-v2-same-job-read-receipt-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
RECEIPT_FIELDS = {"schemaVersion", "executionReportId", "admittedReportId",
    "ownerEmail", "jobId", "providerDispatchId", "providerCallId",
    "toolDispatchId", "toolName", "role", "mode", "contextProofDigest",
    "toolResultDigest", "receiptDigest", "persistedRead",
    "numericCitationAllowed", "agentExecutionAuthorized"}


def _need(ok, message="市场v2同任务工具已读要求不满足"):
    if not ok:
        raise AnalysisContractError(message)


def receipt(raw):
    """Shape check only; a caller-supplied DTO is never read authority."""
    value = _copy(raw, 4096)
    _need(type(value) is dict and set(value) == RECEIPT_FIELDS
        and value["schemaVersion"] == RECEIPT_SCHEMA
        and value["toolName"] in execution.TOOL_ORDER
        and value["role"] in runtime.ROLES
        and value["mode"] in {"read", "summary", "page", "row"}
        and value["persistedRead"] is True
        and value["numericCitationAllowed"] is False
        and value["agentExecutionAuthorized"] is False)
    for field in ("executionReportId", "admittedReportId", "jobId",
            "providerDispatchId", "toolDispatchId"):
        _need(type(value[field]) is str and _ID.fullmatch(value[field]) is not None)
    _need(len({value["executionReportId"], value["admittedReportId"],
        value["jobId"], value["providerDispatchId"],
        value["toolDispatchId"]}) == 5)
    _need(type(value["providerCallId"]) is str
        and 1 <= len(value["providerCallId"]) <= 160
        and not any(ord(char) < 32 for char in value["providerCallId"]))
    _need(type(value["ownerEmail"]) is str and "@" in value["ownerEmail"]
        and value["ownerEmail"] == value["ownerEmail"].lower()
        and len(value["ownerEmail"]) <= 320)
    for field in ("contextProofDigest", "toolResultDigest", "receiptDigest"):
        _need(type(value[field]) is str and _SHA.fullmatch(value[field]) is not None)
    return value


def numeric_requirements(raw_reference, raw_receipt, owning_row, *,
        table_binding_digest, observation_coverage=None):
    """Check a same-job candidate and row cell, but keep citation closed."""
    ref = market_numeric_claims.reference(raw_reference)
    saved = receipt(raw_receipt)
    _need(saved["toolName"] == execution.TOOL_ORDER[4]
        and saved["mode"] in {"page", "row"}
        and saved["role"] in runtime.MARKET_ROLES
        and (ref["jobId"], ref["role"], ref["reportId"]) ==
          (saved["jobId"], saved["role"], saved["admittedReportId"])
        and ref["tableBindingDigest"] == table_binding_digest)
    numeric = market_numeric_claims.number(ref, owning_row,
        observation_coverage=observation_coverage)
    result = {"schemaVersion": SCHEMA, "reference": ref,
        "referenceDigest": digest(ref), "receiptDigest": saved["receiptDigest"],
        "candidateNumber": numeric, "sameJobIdentityMatched": True,
        "owningNumericCellRecomputed": True,
        "independentCellProofPersisted": False,
        "numericCitationAllowed": False, "agentExecutionAuthorized": False}
    return result
