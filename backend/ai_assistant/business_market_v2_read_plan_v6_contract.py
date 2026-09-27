"""Closed pre-creation plan for a new same-report five-Agent identity root.

This pure proposal is not a report, job, provider call, tool result or receipt.
It cannot be persisted with the old 0060/0062/0064 write functions.
"""
import hashlib
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest

from . import business_market_v2_execution_plan_contract as old_plan
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-same-report-read-plan-v6"
PROFILE = "business-agent-screening-promotion-market-read-v6"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RECEIPT_FIELDS = {"planId", "planDigest", "agentDispatchSupported"}
_MISSING = ("protected_v6_report_flow_and_five_job_atomic_creation",
    "0069_real_provider_provenance_and_paid_reservation",
    "0072_independently_adopted_tariff_and_human_cap",
    "same_report_owning_tool_execution_and_sql_read_receipts",
    "independent_numeric_cell_and_human_review_authority")


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场 v6 同报告已读创建前身份或费用边界无效")


def _job_id(report_id, role):
    seed = ("market-v6-job|" + report_id + "|" + role).encode("utf-8")
    return "market-v6-job-" + hashlib.sha256(seed).hexdigest()[:48]


def build(raw_plan_receipt, raw_cost_receipt, new_report_id, new_workflow_id):
    """Fix proposed IDs, with all actual provider/tool/receipt rows absent."""
    source = dict(raw_plan_receipt) if type(raw_plan_receipt) is dict else None
    cost = dict(raw_cost_receipt) if type(raw_cost_receipt) is dict else None
    _need(source is not None and cost is not None
        and _RECEIPT_FIELDS <= set(source)
        and type(new_report_id) is str and _ID.fullmatch(new_report_id) is not None
        and type(new_workflow_id) is str
        and _ID.fullmatch(new_workflow_id) is not None)
    plan_id = source.pop("planId")
    plan_digest = source.pop("planDigest")
    agent_supported = source.pop("agentDispatchSupported")
    _need(type(plan_id) is str and _SHA.fullmatch(plan_id) is not None
        and type(plan_digest) is str and _SHA.fullmatch(plan_digest) is not None
        and digest(source) == plan_digest
        and agent_supported is False
        and source.get("schemaVersion") == old_plan.SCHEMA
        and source.get("executionProfile") == old_plan.PROFILE
        and source.get("toolCatalogDigest") == execution.CATALOG_DIGEST
        and source.get("proposedTools") == list(execution.TOOL_ORDER)
        and source.get("fiveAgentRoles") == list(runtime.ROLES)
        and source.get("providerCallsAllowed") is False
        and source.get("readReceiptAuthority") is False)
    root = old_plan.root(source.get("executionRoot"))
    policy = source.get("modelPolicy")
    _need(type(policy) is dict
        and policy.get("selection") == "deferred"
        and policy.get("modelId") is None
        and policy.get("modelVersion") is None
        and policy.get("paidCallsAllowed") is False
        and policy.get("maxPaidCostCents") == 0
        and policy.get("maxProviderRoundsNow") == 0
        and policy.get("maxToolCallsNow") == 0
        and source.get("graphDigest") == execution.GRAPH_DIGESTS[
            root["withBudget"]])
    _need(type(cost.get("planId")) is str and cost["planId"] == plan_id
        and type(cost.get("ledgerId")) is str
        and _SHA.fullmatch(cost["ledgerId"]) is not None
        and type(cost.get("ledgerRequiredCents")) is int
        and cost["ledgerRequiredCents"] > 0
        and cost.get("ledgerReservedCents") == 0
        and cost.get("status") == "pending_rate_and_approval_verification"
        and cost.get("providerCallsAllowed") is False)
    _need(len({new_report_id, new_workflow_id, root["executionReportId"],
        root["admittedReportId"], root["parkedReportId"],
        root["sourceReportId"]}) == 6)
    slots = [{"role": role, "proposedJobId": _job_id(new_report_id, role),
        "reportId": new_report_id, "workflowId": new_workflow_id,
        "providerDispatchIds": [], "toolDispatchIds": [],
        "readReceiptIds": [], "jobPersisted": False}
        for role in runtime.ROLES]
    snapshot = {"schemaVersion": SCHEMA, "executionProfile": PROFILE,
        "reportId": new_report_id, "workflowId": new_workflow_id,
        "sourceExecutionReportId": root["executionReportId"],
        "ownerEmail": root["ownerEmail"], "sourcePlanId": plan_id,
        "sourcePlanDigest": plan_digest, "costLedgerId": cost["ledgerId"],
        "contextProofDigest": root["contextProofDigest"],
        "marketContextDigest": root["marketContextDigest"],
        "selectorDigest": root["selectorDigest"],
        "manifestDigest": root["manifestDigest"],
        "graphDigest": source["graphDigest"],
        "toolCatalogDigest": execution.CATALOG_DIGEST,
        "allowedTools": list(execution.TOOL_ORDER),
        "roleBindings": slots, "withBudget": root["withBudget"],
        "futureRowBindingPolicy": {
            "provider": ["reportId", "workflowId", "jobId"],
            "tool": ["reportId", "workflowId", "jobId",
                "providerDispatchId", "providerCallId"],
            "readReceipt": ["reportId", "workflowId", "jobId",
                "providerDispatchId", "toolDispatchId"]},
        "status": "paused_pre_creation", "modelId": "",
        "providerRoundCount": 0, "toolCallCount": 0,
        "providerCallsAllowed": False, "externalProviderCalled": False,
        "agentReadPersisted": False, "numericCitationAllowed": False,
        "humanReviewApproved": False, "reportPublishAuthorized": False,
        "reportPersisted": False, "workflowPersisted": False,
        "syntheticOnly": False}
    text = canonical(snapshot)
    _need(len(text.encode("utf-8")) <= 16384)
    return {"snapshot": snapshot, "snapshotJson": text,
        "snapshotDigest": digest(snapshot), "candidateOnly": True,
        "providerCallsAllowed": False, "agentReadPersisted": False,
        "numericCitationAllowed": False,
        "0065CostRequirementVerified": True,
        "0069PaidRoundAuthorityVerified": False,
        "0072RateAndCapAuthorityVerified": False,
        "missingAuthorities": list(_MISSING)}
