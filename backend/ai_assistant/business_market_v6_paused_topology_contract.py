"""Pure intent for one model-neutral, paused five-job market v6 topology.

The old 0065 ledger is only an unverified historical cost candidate. No
model, price, cap, provider dispatch, tool read, or citation is authorized.
Only a future SQL owner may atomically create/cancel rows and report outcome.
"""
from __future__ import annotations

import hashlib
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy

from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_read_plan_v6_contract as plan_v6
from . import business_promotion_market_runtime_v2_contract as market


SCHEMA = "business-market-v6-paused-topology-intent-v1"
SNAPSHOT_SCHEMA = "business-market-v6-paused-topology-snapshot-v1"
PROFILE = "business-agent-screening-promotion-market-paused-v6"
CANCEL_SCHEMA = "business-market-v6-paused-topology-cancel-intent-v1"
OUTCOME_SCHEMA = "business-market-v6-paused-topology-outcome-v1"
ACTIVE_WORKFLOW_LIMITS = (4, 24)
ACTIVE_JOB_LIMITS = (8, 64)
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_CLIENT = re.compile(r"[A-Za-z0-9._:-]{8,128}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_OUTCOMES = {"absent_observed", "committed_paused", "cancelled",
    "conflict", "unknown"}


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场 v6 暂停拓扑身份或零调用边界不成立")


def _stable_id(prefix, parts):
    value = hashlib.sha256(canonical([prefix, *parts]).encode("utf-8")
        ).hexdigest()[:48]
    return prefix + "-" + value


def identities(owner_email, source_execution_report_id, client_request_id):
    """Fixed IDs make an unknown create outcome inspectable without replay."""
    _need(type(owner_email) is str and "@" in owner_email
        and owner_email == owner_email.lower()
        and len(owner_email) <= 320
        and type(source_execution_report_id) is str
        and _ID.fullmatch(source_execution_report_id) is not None
        and type(client_request_id) is str
        and _CLIENT.fullmatch(client_request_id) is not None)
    root = [owner_email, source_execution_report_id, client_request_id]
    return {"reportId": _stable_id("market-v6-report", root),
        "workflowId": _stable_id("market-v6-flow", root)}


def quota_observation(active_workflows_owner, active_workflows_global,
        active_jobs_owner, active_jobs_global):
    """Arithmetic preflight only. SQL must recount under the AI revision lock."""
    counts = (active_workflows_owner, active_workflows_global,
        active_jobs_owner, active_jobs_global)
    _need(all(type(value) is int and value >= 0 for value in counts)
        and active_workflows_owner <= active_workflows_global
        and active_jobs_owner <= active_jobs_global
        and active_workflows_owner + 1 <= ACTIVE_WORKFLOW_LIMITS[0]
        and active_workflows_global + 1 <= ACTIVE_WORKFLOW_LIMITS[1]
        and active_jobs_owner + 5 <= ACTIVE_JOB_LIMITS[0]
        and active_jobs_global + 5 <= ACTIVE_JOB_LIMITS[1])
    return {"activeWorkflowsOwner": active_workflows_owner,
        "activeWorkflowsGlobal": active_workflows_global,
        "activeJobsOwner": active_jobs_owner,
        "activeJobsGlobal": active_jobs_global,
        "additionalWorkflows": 1, "additionalJobs": 5,
        "observationOnly": True, "sqlQuotaLocked": False}


def build(raw_plan_proposal, raw_cost_receipt, raw_actor,
        client_request_id, raw_quota):
    """Describe exact future rows while keeping all persistence flags false."""
    proposal = _copy(raw_plan_proposal, 32 * 1024)
    cost = _copy(raw_cost_receipt, 80 * 1024)
    actor = _copy(raw_actor, 4096)
    quota = _copy(raw_quota, 4096)
    _need(type(proposal) is dict and type(cost) is dict
        and type(actor) is dict and type(quota) is dict
        and proposal.get("candidateOnly") is True
        and proposal.get("providerCallsAllowed") is False
        and proposal.get("agentReadPersisted") is False
        and proposal.get("numericCitationAllowed") is False
        and proposal.get("0069PaidRoundAuthorityVerified") is False
        and proposal.get("0072RateAndCapAuthorityVerified") is False
        and type(proposal.get("snapshot")) is dict
        and proposal.get("snapshotJson") == canonical(proposal["snapshot"])
        and proposal.get("snapshotDigest") == digest(proposal["snapshot"]))
    before = proposal["snapshot"]
    _need(before.get("schemaVersion") == plan_v6.SCHEMA
        and before.get("executionProfile") == plan_v6.PROFILE
        and before.get("status") == "paused_pre_creation"
        and before.get("reportPersisted") is False
        and before.get("workflowPersisted") is False
        and before.get("syntheticOnly") is False
        and before.get("providerCallsAllowed") is False
        and before.get("externalProviderCalled") is False
        and before.get("agentReadPersisted") is False
        and before.get("numericCitationAllowed") is False
        and before.get("modelId") == ""
        and before.get("providerRoundCount") == 0
        and before.get("toolCallCount") == 0
        and before.get("allowedTools") == list(execution.TOOL_ORDER)
        and before.get("toolCatalogDigest") == execution.CATALOG_DIGEST)
    owner = before.get("ownerEmail")
    source_id = before.get("sourceExecutionReportId")
    ids = identities(owner, source_id, client_request_id)
    _need((before.get("reportId"), before.get("workflowId")) ==
        (ids["reportId"], ids["workflowId"])
        and type(actor.get("email")) is str
        and actor["email"] == owner
        and actor.get("role") == "admin"
        and actor.get("status") == "active"
        and actor.get("scope") is None
        and type(actor.get("version")) is int
        and 1 <= actor["version"] <= 2**53 - 1)
    _need(cost.get("planId") == before.get("sourcePlanId")
        and cost.get("ledgerId") == before.get("costLedgerId")
        and type(cost.get("ledgerId")) is str
        and _SHA.fullmatch(cost["ledgerId"]) is not None
        and type(cost.get("candidateDigest")) is str
        and _SHA.fullmatch(cost["candidateDigest"]) is not None
        and cost.get("ledgerReservedCents") == 0
        and cost.get("status") == "pending_rate_and_approval_verification"
        and cost.get("providerCallsAllowed") is False
        and all(cost.get(field) is False for field in (
            "tariffAuthorityVerified", "humanApprovalAuthorityVerified",
            "extraChargeCategoryCoverageVerified",
            "currencyConversionVerified", "fundsReserved")))
    observed = quota_observation(
        quota.get("activeWorkflowsOwner"),
        quota.get("activeWorkflowsGlobal"),
        quota.get("activeJobsOwner"),
        quota.get("activeJobsGlobal"))
    _need(quota == observed)
    bindings = before.get("roleBindings")
    _need(type(bindings) is list and len(bindings) == 5
        and [item.get("role") for item in bindings] == list(market.ROLES)
        and all(item.get("reportId") == ids["reportId"]
            and item.get("workflowId") == ids["workflowId"]
            and item.get("proposedJobId") == plan_v6._job_id(
                ids["reportId"], item["role"])
            and item.get("jobPersisted") is False
            and item.get("providerDispatchIds") == []
            and item.get("toolDispatchIds") == []
            and item.get("readReceiptIds") == []
            for item in bindings))
    role_jobs = {item["role"]: item["proposedJobId"] for item in bindings}
    _need(len(set(role_jobs.values())) == 5)
    graph = execution.graph(before["withBudget"])
    _need(digest(graph) == before["graphDigest"]
        and len(graph["nodes"]) == 6)
    nodes = []
    for index, part in enumerate(graph["nodes"]):
        role = part["key"] if part["type"] == "agent" else None
        _need(role is None or role in role_jobs)
        nodes.append({"nodeId": _stable_id("market-v6-node",
            [ids["reportId"], part["key"]]),
            "nodeKey": part["key"], "nodeType": part["type"],
            "position": index, "dependsOn": part["dependsOn"],
            "jobId": role_jobs[role] if role else None,
            "intendedStatus": "pending"})
    _need({item["nodeKey"] for item in nodes if item["jobId"]}
        == set(market.ROLES))
    jobs = [{"role": role, "jobId": role_jobs[role],
        "workflowId": ids["workflowId"], "reportId": ids["reportId"],
        "intendedStatus": "paused", "modelId": "", "modelVersion": 0,
        "providerRoundCount": 0, "toolCallCount": 0,
        "providerDispatchIds": [], "toolDispatchIds": [],
        "readReceiptIds": []} for role in market.ROLES]
    snapshot = {"schemaVersion": SNAPSHOT_SCHEMA,
        "executionProfile": PROFILE, **ids,
        "clientRequestId": client_request_id,
        "ownerEmail": owner, "ownerVersion": actor["version"],
        "sourceExecutionReportId": source_id,
        "sourcePlanId": before["sourcePlanId"],
        "sourcePlanDigest": before["sourcePlanDigest"],
        "sourceUnverifiedCostCandidateId": cost["ledgerId"],
        "sourceUnverifiedCostCandidateDigest": cost["candidateDigest"],
        "sourceCostCandidateSpendable": False,
        "futureModelSpecificAuthorityId": None,
        "approvedCnyCapCents": None,
        "tariffAuthorityVerified": False,
        "humanCapApprovalVerified": False,
        "durablePaidReservation": False,
        "modelSelectionDeferred": True, "modelId": "",
        "modelVersion": 0, "withBudget": before["withBudget"],
        "graphDigest": before["graphDigest"],
        "toolCatalogDigest": execution.CATALOG_DIGEST,
        "allowedTools": list(execution.TOOL_ORDER),
        "contextProofDigest": before["contextProofDigest"],
        "marketContextDigest": before["marketContextDigest"],
        "selectorDigest": before["selectorDigest"],
        "manifestDigest": before["manifestDigest"],
        "roles": list(market.ROLES), "jobs": jobs,
        "nodes": nodes, "intendedStatus": "paused",
        "providerCallsAllowed": False,
        "toolDispatchAllowed": False,
        "externalProviderCalled": False,
        "agentReadPersisted": False,
        "numericCitationAllowed": False,
        "humanReviewApproved": False,
        "reportPublishAuthorized": False,
        "syntheticOnly": False}
    intent = {"schemaVersion": SCHEMA,
        "clientRequestId": client_request_id,
        "actorEmail": owner, "expectedActorVersion": actor["version"],
        "sourceExecutionReportId": source_id,
        "sourcePlanId": before["sourcePlanId"],
        "sourcePlanDigest": before["sourcePlanDigest"],
        "sourceUnverifiedCostCandidateId": cost["ledgerId"],
        "sourceUnverifiedCostCandidateDigest": cost["candidateDigest"],
        "reportId": ids["reportId"],
        "workflowId": ids["workflowId"],
        "snapshotDigest": digest(snapshot),
        "quotaObservation": observed,
        "providerCallsAllowed": False}
    _need(len(canonical(snapshot).encode("utf-8")) <= 16384
        and len(canonical(intent).encode("utf-8")) <= 4096)
    return {"schemaVersion": SCHEMA, "intent": intent,
        "intentJson": canonical(intent), "intentDigest": digest(intent),
        "snapshot": snapshot, "snapshotJson": canonical(snapshot),
        "snapshotDigest": digest(snapshot),
        "candidateOnly": True,
        "quotaObservationOnly": True,
        "reportPersisted": False,
        "workflowPersisted": False,
        "jobsPersisted": False,
        "providerCallsAllowed": False,
        "agentReadPersisted": False,
        "numericCitationAllowed": False}


def cancel_request(report_id, owner_email, expected_version, reason_digest):
    """Fix a cancel intent; SQL must prove no provider/tool/read effects."""
    _need(type(report_id) is str and _ID.fullmatch(report_id) is not None
        and type(owner_email) is str and "@" in owner_email
        and owner_email == owner_email.lower()
        and type(expected_version) is int and expected_version >= 1
        and type(reason_digest) is str and _SHA.fullmatch(reason_digest)
        is not None)
    body = {"schemaVersion": CANCEL_SCHEMA, "reportId": report_id,
        "ownerEmail": owner_email, "expectedVersion": expected_version,
        "reasonDigest": reason_digest, "explicitCancel": True}
    return {"request": body, "requestJson": canonical(body),
        "requestDigest": digest(body), "candidateOnly": True,
        "providerCallsAllowed": False}


def outcome(raw):
    """Validate a future SQL result; never promote it to a dispatch grant."""
    value = _copy(raw, 4096)
    fields = {"schemaVersion", "status", "reportId", "workflowId",
        "ownerEmail", "clientRequestId", "requestDigest",
        "modelId", "providerCallsAllowed", "agentReadPersisted",
        "numericCitationAllowed", "durablePaidReservation"}
    _need(type(value) is dict and set(value) == fields
        and value["schemaVersion"] == OUTCOME_SCHEMA
        and value["status"] in _OUTCOMES
        and type(value["reportId"]) is str
        and _ID.fullmatch(value["reportId"]) is not None
        and type(value["workflowId"]) is str
        and _ID.fullmatch(value["workflowId"]) is not None
        and type(value["ownerEmail"]) is str
        and "@" in value["ownerEmail"]
        and value["ownerEmail"] == value["ownerEmail"].lower()
        and type(value["clientRequestId"]) is str
        and _CLIENT.fullmatch(value["clientRequestId"]) is not None
        and type(value["requestDigest"]) is str
        and _SHA.fullmatch(value["requestDigest"]) is not None
        and value["modelId"] == ""
        and value["providerCallsAllowed"] is False
        and value["agentReadPersisted"] is False
        and value["numericCitationAllowed"] is False
        and value["durablePaidReservation"] is False)
    return value
