"""Test-only owning preparation of a paused v6 identity proposal; no writes."""
from django.conf import settings
from django.db import DatabaseError

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_cost_admission as costs
from . import business_market_v2_execution_plan as plans
from . import business_market_v2_paid_gate as paid_gate
from . import business_market_v2_read_plan_v6_contract as contract
from . import models as m
from .policy import AiError, current_principal, identifier


def prepare(source_execution_report_id, new_report_id, new_workflow_id,
        principal):
    """Re-read current protected roots; never create v6 rows or grant calls."""
    if (getattr(settings, "AI_MARKET_V2_READ_PLAN_V6_ENABLED", False)
            is not True or settings.DJANGO_ENVIRONMENT != "test"):
        raise AiError("市场 v6 已读流程创建前准备未启用",
            "market_v2_read_plan_v6_disabled", 409)
    source_id = identifier(source_execution_report_id, "executionReportId")
    report_id = identifier(new_report_id, "newReportId")
    flow_id = identifier(new_workflow_id, "newWorkflowId")
    actor = current_principal(principal, admin=True)
    try:
        plan = plans.read(source_id, principal)
        cost = costs.read(plan["planId"], principal)
        gate = paid_gate.inspect(source_id, principal)
        if (plan["executionRoot"]["ownerEmail"] != actor.email.lower()
                or gate["planId"] != plan["planId"]
                or gate["costLedgerId"] != cost["ledgerId"]
                or gate["reservedCents"] != 0
                or gate["providerCallsAllowed"] is not False
                or m.AiReportRun.objects.filter(pk=report_id).exists()
                or m.AiWorkflowRuns.objects.filter(pk=flow_id).exists()):
            raise AiError("市场 v6 来源、费用或候选身份已变化",
                "market_v2_read_plan_v6_unverified", 409)
        built = contract.build(plan, cost, report_id, flow_id)
        # The proposal is not a write lock. A second read only detects ordinary
        # concurrent changes; future SQL creation must repeat every predicate.
        if (plans.read(source_id, principal) != plan
                or costs.read(plan["planId"], principal) != cost
                or paid_gate.inspect(source_id, principal) != gate
                or current_principal(principal, admin=True).email.lower()
                    != actor.email.lower()
                or m.AiReportRun.objects.filter(pk=report_id).exists()
                or m.AiWorkflowRuns.objects.filter(pk=flow_id).exists()):
            raise AiError("市场 v6 创建前来源在复核期间变化",
                "market_v2_read_plan_v6_unverified", 409)
        return built
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError) as error:
        raise AiError("市场 v6 创建前证明或零预留费用账不可用",
            "market_v2_read_plan_v6_unverified", 409) from error
