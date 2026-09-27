"""Pure market context bytes; source authority is supplied only by SQL."""
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest


SCHEMA = "business-market-v2-context-proof-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def context(report_id, run_id, screening_id, sealed_digest):
    for value in (report_id, run_id, screening_id):
        if type(value) is not str or _ID.fullmatch(value) is None:
            raise AnalysisContractError("市场 context 来源 ID 无效")
    if type(sealed_digest) is not str or _SHA.fullmatch(sealed_digest) is None:
        raise AnalysisContractError("市场 context 封存摘要无效")
    value = {"reportId": report_id, "runId": run_id,
        "screeningId": screening_id, "sealedDigest": sealed_digest}
    return {"canonicalJson": canonical(value), "contextDigest": digest(value)}


def receipt(value, *, execution_report_id, owner_email, context_digest):
    """Validate a narrow SQL reader receipt, not its source authority."""
    if (type(value) is not dict or set(value) != {"schemaVersion", "reportId",
            "ownerEmail", "admittedReportId", "parkedReportId", "sourceReportId",
            "selectorDigest", "manifestDigest", "contextDigest", "proofDigest",
            "proofPersisted", "agentReadPersisted", "executionReady"}
            or value["schemaVersion"] != SCHEMA
            or value["reportId"] != execution_report_id
            or value["ownerEmail"] != owner_email
            or value["contextDigest"] != context_digest
            or value["proofPersisted"] is not True
            or value["agentReadPersisted"] is not False
            or value["executionReady"] is not False):
        raise AnalysisContractError("市场 context 窄回执身份或边界无效")
    for key in ("admittedReportId", "parkedReportId", "sourceReportId"):
        if type(value[key]) is not str or _ID.fullmatch(value[key]) is None:
            raise AnalysisContractError("市场 context 回执根 ID 无效")
    for key in ("selectorDigest", "manifestDigest", "contextDigest", "proofDigest"):
        if type(value[key]) is not str or _SHA.fullmatch(value[key]) is None:
            raise AnalysisContractError("市场 context 回执摘要无效")
    return value
