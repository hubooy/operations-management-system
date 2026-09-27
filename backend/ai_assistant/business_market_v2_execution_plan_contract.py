"""Frozen five-Agent execution-plan proof; no model or dispatch grant."""
import re

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy
from . import business_market_v2_execution_snapshot_contract as prior


SCHEMA = "business-market-v2-five-agent-execution-plan-v3"
PROFILE = "business-agent-screening-promotion-market-execution-plan-v3"
PAUSE = "market_v2_model_policy_and_dispatch_not_activated"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED_HOOKS = ["versioned_active_report_and_graph",
    "approved_model_and_cost_policy", "atomic_first_job_creation",
    "provider_tool_result_ownership", "independent_numeric_cell_proof"]
ROOT_FIELDS = {"executionReportId", "admittedReportId", "parkedReportId",
    "sourceReportId", "ownerEmail", "selectorDigest", "manifestDigest",
    "marketContextDigest", "contextProofDigest", "executionSnapshotDigest",
    "withBudget"}


def _need(ok, message="市场v2五Agent执行计划根或策略无效"):
    if not ok:
        raise AnalysisContractError(message)


def root(raw):
    value = _copy(raw, 4096)
    _need(type(value) is dict and set(value) == ROOT_FIELDS)
    for key in ("executionReportId", "admittedReportId", "parkedReportId",
            "sourceReportId"):
        _need(type(value[key]) is str and _ID.fullmatch(value[key]) is not None)
    _need(len({value[key] for key in ("executionReportId", "admittedReportId",
        "parkedReportId", "sourceReportId")}) == 4)
    _need(type(value["ownerEmail"]) is str
        and value["ownerEmail"] == value["ownerEmail"].lower()
        and "@" in value["ownerEmail"] and len(value["ownerEmail"]) <= 320)
    for key in ("selectorDigest", "manifestDigest", "marketContextDigest",
            "contextProofDigest", "executionSnapshotDigest"):
        _need(type(value[key]) is str and _SHA.fullmatch(value[key]) is not None)
    _need(type(value["withBudget"]) is bool)
    return value


def build(raw_root, entries):
    """Return canonical plan bytes from the actual central five-tool catalog."""
    fixed = root(raw_root)
    prior.catalog(entries)
    graph = prior.graph(fixed["withBudget"])
    value = {"schemaVersion": SCHEMA, "executionProfile": PROFILE,
        "executionRoot": fixed, "catalogSurface": prior.SURFACE,
        "toolCatalogDigest": prior.CATALOG_DIGEST,
        "graphDigest": prior.GRAPH_DIGESTS[fixed["withBudget"]],
        "proposedTools": list(prior.TOOL_ORDER),
        "fiveAgentRoles": [node["key"] for node in graph["nodes"]
            if node["type"] == "agent"],
        "modelPolicy": {"selection": "deferred", "modelId": None,
            "modelVersion": None, "paidCallsAllowed": False,
            "maxPaidCostCents": 0, "maxProviderRoundsNow": 0,
            "maxProviderRoundsAfterApproval": 20,
            "maxToolCallsNow": 0, "maxToolCallsAfterApproval": 40},
        "budgetPolicy": {"withBudget": fixed["withBudget"],
            "nativeBudgetSpendAllowed": False,
            "budgetDecisionRequiresHumanReview": True},
        "activationStatus": "deferred", "agentJobsAllowed": False,
        "providerCallsAllowed": False, "readReceiptAuthority": False,
        "numericCitationAllowed": False, "humanReviewRequired": True,
        "marketAndOwnSalesAdditive": False,
        "requiredActivationHooks": list(REQUIRED_HOOKS)}
    _need(value["fiveAgentRoles"] == ["commerce", "promotion",
        "market_b2b", "independent_review", "report"])
    text = canonical(value)
    _need(len(text.encode("utf-8")) <= 16384)
    return {"plan": value, "planJson": text, "planDigest": digest(value),
        "status": "paused", "agentDispatchSupported": False}
