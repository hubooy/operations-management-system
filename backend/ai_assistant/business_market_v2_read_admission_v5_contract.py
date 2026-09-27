"""Versioned, closed diagnostic for a future same-report market read path.

The input is only useful after an owning reader has loaded the named rows.
It never converts a synthetic provider response into an Agent read receipt.
"""
import re

from business_analysis.contracts import AnalysisContractError

from . import business_market_v2_execution_plan_contract as plan
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_promotion_market_runtime_v2_contract as runtime


SCHEMA = "business-market-v2-read-admission-v5-candidate-v1"
PROPOSED_PROFILE = "business-agent-screening-promotion-market-read-v5"
SYNTHETIC_PROFILE = "business-agent-screening-promotion-market-synthetic-v4"
_OBSERVED_FIELDS = {"reportId", "workflowId", "ownerEmail", "profile",
    "sourceExecutionReportId", "planId", "workflowStatus", "modelId",
    "dryRun", "syntheticOnly", "externalProviderCalled", "jobRoles",
    "sameJobProviderToolChainsObserved"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_BLOCKERS = ("versioned_same_report_active_flow_missing",
    "independently_authorized_provider_call_missing",
    "owning_tool_read_receipts_missing",
    "durable_paid_round_authority_missing",
    "independent_numeric_cell_authority_missing")


def assess(raw_root, plan_id, observed):
    """Classify only the old parked root or the separate 0064 rehearsal.

    A future v5 profile requires a new, SQL-owned admission. A caller cannot
    introduce that profile by changing a Python DTO or a report snapshot.
    """
    root = plan.root(raw_root)
    if (type(plan_id) is not str or len(plan_id) != 64
            or any(char not in "0123456789abcdef" for char in plan_id)
            or type(observed) is not dict or set(observed) != _OBSERVED_FIELDS
            or type(observed["jobRoles"]) is not list
            or type(observed["reportId"]) is not str
            or _ID.fullmatch(observed["reportId"]) is None
            or type(observed["workflowId"]) is not str
            or _ID.fullmatch(observed["workflowId"]) is None
            or type(observed["sameJobProviderToolChainsObserved"]) is not bool
            or observed["ownerEmail"] != root["ownerEmail"]
            or observed["sourceExecutionReportId"] != root["executionReportId"]
            or observed["planId"] != plan_id
            or observed["externalProviderCalled"] is not False):
        raise AnalysisContractError("市场已读准入的拥有方身份或调用状态无效")
    if observed["profile"] == execution.PROFILE:
        if (observed["reportId"] != root["executionReportId"]
                or observed["workflowStatus"] != "paused"
                or observed["modelId"] != ""
                or observed["dryRun"] is not False
                or observed["syntheticOnly"] is not False
                or observed["jobRoles"] != []
                or observed["sameJobProviderToolChainsObserved"] is not False):
            raise AnalysisContractError("旧市场执行档案不得改挂任务")
        observed_kind = "parked_execution_v2"
    elif observed["profile"] == SYNTHETIC_PROFILE:
        if (observed["reportId"] == root["executionReportId"]
                or observed["workflowStatus"] != "paused"
                or observed["modelId"] != "market-v2-synthetic-only"
                or observed["dryRun"] is not True
                or observed["syntheticOnly"] is not True
                or observed["jobRoles"] != list(runtime.ROLES)
                or observed["sameJobProviderToolChainsObserved"] is not True):
            raise AnalysisContractError("市场合成链身份或同任务结构无效")
        observed_kind = "separate_synthetic_v4"
    else:
        raise AnalysisContractError("没有受保护的新版本同报告读取 profile")
    return {"schemaVersion": SCHEMA,
        "proposedExecutionProfile": PROPOSED_PROFILE,
        "sourceExecutionReportId": root["executionReportId"],
        "candidateReportId": observed["reportId"],
        "observedKind": observed_kind,
        "roleCount": len(observed["jobRoles"]),
        "sameJobProviderToolChainsObserved":
            observed["sameJobProviderToolChainsObserved"],
        "syntheticOnly": observed["syntheticOnly"],
        "externalProviderCalled": False,
        "agentReadPersisted": False,
        "providerCallsAllowed": False,
        "numericCitationAllowed": False,
        "humanReviewApproved": False,
        "reportPublishAuthorized": False,
        "missingAuthorities": list(_BLOCKERS)}
