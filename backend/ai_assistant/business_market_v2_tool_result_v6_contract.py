"""Closed v6 bridge between a proposed job slot and an owning tool replay.

The replay callback is supplied by a future owning caller.  Equality with its
result is useful for checking a transport implementation, but cannot prove a
database job, a provider response, an Agent read, or a numeric citation.
"""
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy

from . import business_market_v2_execution_plan_contract as source_contract
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_fifth_read_contract as fifth
from . import business_market_v2_read_plan_v6_contract as v6
from . import business_market_v2_transport_contract as transport
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-owning-tool-result-v6-candidate-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场 v6 工具结果候选与同报告来源不一致")


def check(proposal, source_plan, call_identity, arguments, tool_result,
        owning_replay):
    """Compare one proposed market-tool call with a freshly replayed result.

    No input here is loaded from a protected database by this pure contract.
    A future owning service must perform that read before it can issue a new,
    separately versioned persisted receipt.
    """
    proposal = _copy(proposal, 64 * 1024)
    source_plan = _copy(source_plan, 64 * 1024)
    _need(type(proposal) is dict and type(source_plan) is dict
        and callable(owning_replay)
        and proposal.get("candidateOnly") is True
        and proposal.get("providerCallsAllowed") is False
        and proposal.get("agentReadPersisted") is False
        and proposal.get("numericCitationAllowed") is False)
    snapshot = proposal.get("snapshot")
    _need(type(snapshot) is dict
        and snapshot.get("schemaVersion") == v6.SCHEMA
        and snapshot.get("executionProfile") == v6.PROFILE
        and proposal.get("snapshotJson") == canonical(snapshot)
        and proposal.get("snapshotDigest") == digest(snapshot)
        and snapshot.get("status") == "paused_pre_creation"
        and snapshot.get("reportPersisted") is False
        and snapshot.get("workflowPersisted") is False
        and snapshot.get("providerCallsAllowed") is False
        and snapshot.get("externalProviderCalled") is False
        and snapshot.get("agentReadPersisted") is False
        and snapshot.get("numericCitationAllowed") is False
        and snapshot.get("syntheticOnly") is False
        and snapshot.get("allowedTools") == list(execution.TOOL_ORDER)
        and type(snapshot.get("reportId")) is str
        and type(snapshot.get("workflowId")) is str)
    source = dict(source_plan)
    plan_id = source.pop("planId", None)
    plan_digest = source.pop("planDigest", None)
    _need(source.pop("agentDispatchSupported", None) is False
        and plan_id == snapshot.get("sourcePlanId")
        and plan_digest == snapshot.get("sourcePlanDigest")
        and type(plan_digest) is str and _SHA.fullmatch(plan_digest) is not None
        and digest(source) == plan_digest
        and source.get("providerCallsAllowed") is False)
    root = source_contract.root(source.get("executionRoot"))
    _need(snapshot.get("sourceExecutionReportId") == root["executionReportId"]
        and snapshot.get("ownerEmail") == root["ownerEmail"]
        and snapshot.get("contextProofDigest") == root["contextProofDigest"]
        and snapshot.get("marketContextDigest") == root["marketContextDigest"]
        and snapshot.get("selectorDigest") == root["selectorDigest"]
        and snapshot.get("manifestDigest") == root["manifestDigest"]
        and snapshot.get("withBudget") == root["withBudget"])
    call = fifth.injected(call_identity)
    slots = snapshot.get("roleBindings")
    _need(type(slots) is list and len(slots) == len(runtime.ROLES)
        and all(type(slot) is dict for slot in slots)
        and [slot.get("role") for slot in slots] == list(runtime.ROLES)
        and all(slot.get("proposedJobId") == v6._job_id(
            snapshot["reportId"], slot["role"]) for slot in slots)
        and len({slot["proposedJobId"] for slot in slots}) == len(slots)
        and call["role"] in runtime.MARKET_ROLES)
    slot = next(slot for slot in slots if slot["role"] == call["role"])
    _need(slot.get("reportId") == snapshot.get("reportId")
        and slot.get("workflowId") == snapshot.get("workflowId")
        and slot.get("proposedJobId") == v6._job_id(snapshot["reportId"],
            call["role"])
        and slot.get("proposedJobId") == call["jobId"]
        and slot.get("jobPersisted") is False
        and slot.get("providerDispatchIds") == []
        and slot.get("toolDispatchIds") == []
        and slot.get("readReceiptIds") == []
        and call["admittedReportId"] == root["admittedReportId"]
        and call["marketManifestDigest"] == root["manifestDigest"]
        and call["marketContextDigest"] == root["marketContextDigest"]
        and call["providerDispatchId"] not in {
            snapshot["reportId"], snapshot["workflowId"], call["jobId"]})
    _, selected = transport.request(transport.SURFACE, transport.PROFILE,
        transport.TOOL, call, arguments)
    observed = transport.result(tool_result)
    _need(observed["reportId"] == call["admittedReportId"]
        and observed["role"] == call["role"]
        and observed["mode"] == selected["mode"]
        and observed["identityClaimDigest"] == digest(call)
        and observed["jobIdClaim"] == call["jobId"]
        and observed["providerDispatchIdClaim"] == call["providerDispatchId"]
        and observed["providerCallIdClaim"] == call["providerCallId"]
        and observed["marketManifestDigest"] == root["manifestDigest"]
        and observed["numericReferenceRequiredFields"] == ["metric", "field"])
    # A callback can be simulated in a unit test; this comparison therefore
    # remains a candidate and deliberately carries no owning authority.
    try:
        replayed = transport.result(owning_replay(call, selected))
    except Exception as error:
        raise AnalysisContractError(
            "市场 v6 拥有方回放回调不可用") from error
    _need(replayed == observed)
    return {"schemaVersion": SCHEMA,
        "newReportId": snapshot["reportId"],
        "newWorkflowId": snapshot["workflowId"],
        "sourceExecutionReportId": root["executionReportId"],
        "admittedReportId": root["admittedReportId"],
        "role": call["role"], "proposedJobId": call["jobId"],
        "providerDispatchIdClaim": call["providerDispatchId"],
        "providerCallIdClaim": call["providerCallId"],
        "mode": selected["mode"], "argumentsDigest": digest(selected),
        "toolResultDigest": digest(observed),
        "replayCallbackMatched": True,
        "protectedRowsIndependentlyLoaded": False,
        "providerResponseAuthenticated": False,
        "toolDispatchPersisted": False,
        "agentReadPersisted": False,
        "numericCitationAllowed": False,
        "humanReviewApproved": False,
        "reportPublishAuthorized": False,
        "candidateOnly": True}
