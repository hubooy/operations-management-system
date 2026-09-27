"""Read-only owner preflight for a future atomic paused v6 topology.

This module never creates a report, workflow, job, provider dispatch, read
receipt, or paid reservation. Its quota view and all IDs are only proposals;
the future SQL creator must re-evaluate them under the AI mutation lock.
"""
from django.conf import settings
from django.db import DatabaseError

from access_control.models import AppUser
from business_analysis.contracts import AnalysisContractError

from . import business_market_v2_cost_admission as costs
from . import business_market_v2_execution_plan as plans
from . import business_market_v2_paid_gate as paid_gate
from . import business_market_v2_read_plan_v6 as source_plan
from . import business_market_v6_paused_topology_contract as contract
from . import models as m
from .policy import AiError, current_principal, identifier
from .workflows import ACTIVE


def _closed():
    if (getattr(settings, "AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED",
            False) is not True or settings.DJANGO_ENVIRONMENT != "test"
            or settings.DJANGO_PROCESS_ROLE not in {"development", "ai_reader"}):
        raise AiError("市场 v6 暂停拓扑准备尚未启用",
            "market_v6_paused_topology_disabled", 409)


def _actor(principal):
    fixed = current_principal(principal, admin=True)
    row = AppUser.objects.filter(email=fixed.email.lower(),
        status="active", role_id="admin", scope__isnull=True).values(
        "email", "version", "status", "scope").first()
    if (row is None or type(row["version"]) is not int
            or row["version"] < 1):
        raise AiError("市场 v6 管理员账号版本不可用", "access_denied", 403)
    return {**row, "role": "admin"}


def _quota(email):
    workflows = m.AiWorkflowRuns.objects.filter(status__in=ACTIVE)
    jobs = m.AiAgentJobs.objects.filter(status__in=ACTIVE)
    return contract.quota_observation(
        workflows.filter(owner_email=email).count(), workflows.count(),
        jobs.filter(owner_email=email).count(), jobs.count())


def _unused(built):
    snapshot = built["snapshot"]
    return not (m.AiReportRun.objects.filter(pk=snapshot["reportId"]).exists()
        or m.AiWorkflowRuns.objects.filter(pk=snapshot["workflowId"]).exists()
        or m.AiAgentJobs.objects.filter(pk__in=[item["jobId"]
            for item in snapshot["jobs"]]).exists()
        or m.AiWorkflowNodeRuns.objects.filter(pk__in=[item["nodeId"]
            for item in snapshot["nodes"]]).exists())


def prepare(source_execution_report_id, client_request_id, principal):
    """Return an all-or-nothing intent with no database mutation authority."""
    _closed()
    source_id = identifier(source_execution_report_id,
        "sourceExecutionReportId")
    actor = _actor(principal)
    try:
        ids = contract.identities(actor["email"], source_id,
            client_request_id)
        proposal = source_plan.prepare(source_id, ids["reportId"],
            ids["workflowId"], principal)
        plan = plans.read(source_id, principal)
        cost = costs.read(plan["planId"], principal)
        gate = paid_gate.inspect(source_id, principal)
        if (plan["planId"] != proposal["snapshot"]["sourcePlanId"]
                or plan["planDigest"] != proposal["snapshot"][
                    "sourcePlanDigest"]
                or cost["ledgerId"] != proposal["snapshot"]["costLedgerId"]
                or gate["planId"] != plan["planId"]
                or gate["costLedgerId"] != cost["ledgerId"]
                or gate["reservedCents"] != 0
                or gate["providerCallsAllowed"] is not False):
            raise AiError("市场 v6 来源或费用候选已变化", "conflict", 409)
        quota = _quota(actor["email"])
        built = contract.build(proposal, cost, actor,
            client_request_id, quota)
        if not _unused(built):
            raise AiError("市场 v6 拟建身份已经占用", "conflict", 409)
        # These reads are not locks. They only reject ordinary drift before
        # returning a candidate. The SQL creator must repeat all checks and
        # quota counts inside the global AI mutation transaction.
        if (_actor(principal) != actor
                or plans.read(source_id, principal) != plan
                or costs.read(plan["planId"], principal) != cost
                or paid_gate.inspect(source_id, principal) != gate
                or _quota(actor["email"]) != quota
                or not _unused(built)):
            raise AiError("市场 v6 拟建拓扑在复核期间变化", "conflict", 409)
        return built
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError) as error:
        raise AiError("市场 v6 暂停拓扑候选不可核验",
            "market_v6_paused_topology_unverified", 409) from error
