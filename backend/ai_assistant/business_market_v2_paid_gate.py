"""Fail-closed boundary between market-v2 candidates and paid Agent dispatch.

The 0065 ledger records a cost *requirement*, not funds.  In particular its
reserved_cents column is constrained to zero.  A process flag, a SHA-256
tariff claim, or a synthetic provider result must never turn that row into a
provider permission.  This module is deliberately installed at the common
Agent tick before either provider or tool reservation, so a future market
profile cannot silently fall through to the generic Agent path.
"""
import json

from django.conf import settings

from . import models as m
from .policy import AiError, current_principal, identifier


MARKET_PROFILE_PREFIX = "business-agent-screening-promotion-market-"
MARKET_TOOLS = frozenset({
    "get_business_market_v2_screening_package",
    "get_business_market_v2_screening_analysis",
    "get_business_market_v2_screening_budget",
    "get_business_market_v2_keyword_sku",
    "get_business_promotion_market_v2",
})
REQUIRED_AUTHORITIES = (
    "independently_adopted_current_provider_tariff",
    "verified_cny_fx_and_all_charge_categories",
    "exact_human_per_report_cap",
    "durable_atomic_per_provider_reservation",
    "unknown_outcome_non_retry_ledger",
)


def is_market_job(job):
    """Recognize the family by protected report *or* tool policy.

    The existing business_reports.context intentionally does not recognize
    the 0060/0064 snapshot schema.  Relying on that helper here would let a
    newly queued market job be treated as an ordinary generic Agent.
    """
    try:
        tools = json.loads(job.allowed_tools_json)
    except (TypeError, ValueError):
        tools = None
    if type(tools) is list:
        if any(type(name) is not str for name in tools):
            raise AiError("Agent 工具策略不可验证", "market_v2_paid_gate_closed", 409)
        if MARKET_TOOLS.intersection(tools):
            return True
    if not job.workflow_run_id:
        return False
    row = m.AiReportRun.objects.filter(workflow_id=job.workflow_run_id).only(
        "snapshot_json").first()
    if row is None:
        return False
    try:
        snapshot = json.loads(row.snapshot_json)
    except (TypeError, ValueError) as error:
        raise AiError("市场任务报告快照不可验证", "market_v2_paid_gate_closed", 409) from error
    if type(snapshot) is not dict:
        raise AiError("市场任务报告快照不可验证", "market_v2_paid_gate_closed", 409)
    profile = snapshot.get("executionProfile")
    return type(profile) is str and profile.startswith(MARKET_PROFILE_PREFIX)


def inspect(execution_report_id, principal):
    """Read-only account-bound preflight; never returns a runtime grant.

    Reader role is required by the existing narrow 0063/0065 receipts.  All
    authority flags stay false until a separately versioned, audited durable
    reservation design replaces this closed boundary.
    """
    current_principal(principal, admin=True)
    from . import business_market_v2_execution_plan as plans
    from . import business_market_v2_cost_admission as costs

    report_id = identifier(execution_report_id, "executionReportId")
    plan = plans.read(report_id, principal)
    cost = costs.read(plan["planId"], principal)
    if (cost["planId"] != plan["planId"]
            or cost["ledgerReservedCents"] != 0
            or cost["providerCallsAllowed"] is not False):
        raise AiError("市场费用需求账不是关闭状态", "market_v2_paid_gate_closed", 409)
    return {"schemaVersion": "business-market-v2-paid-preflight-v1",
        "executionReportId": report_id, "planId": plan["planId"],
        "costLedgerId": cost["ledgerId"],
        "requiredCents": cost["requiredCents"],
        "reservedCents": 0,
        "tariffAuthorityVerified": False,
        "currencyConversionVerified": False,
        "extraChargeCategoryCoverageVerified": False,
        "humanApprovalAuthorityVerified": False,
        "durableProviderReservationVerified": False,
        "unknownOutcomeNonRetryVerified": False,
        "providerCallsAllowed": False,
        "missingAuthorities": list(REQUIRED_AUTHORITIES)}


def before_reservation(job):
    """Per-microstep guard called before *every* generic dispatch mutation.

    Even if the opt-in process flag is set, 0065 has no spendable balance or
    atomic reservation function.  No in-memory token, caller-supplied claim or
    unverified digest is an alternative.  A future positive implementation
    must reserve in the same protected transaction as its provider dispatch.
    """
    if not is_market_job(job):
        return
    if getattr(settings, "AI_MARKET_V2_PAID_RUNTIME_ENABLED", False) is not True:
        raise AiError("市场五Agent付费运行未启用", "market_v2_paid_runtime_disabled", 409)
    raise AiError("市场费用权威与逐轮原子预留尚未建立",
        "market_v2_paid_reservation_unavailable", 409)
