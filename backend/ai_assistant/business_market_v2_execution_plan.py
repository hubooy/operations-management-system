"""Internal SQL-attested five-Agent plan; never creates a runnable job."""
import json

from django.conf import settings
from django.db import connection, transaction

from access_control.models import AppUser
from . import business_market_v2_execution_plan_contract as contract
from . import business_market_v2_execution_snapshot as previous
from . import models as m, transport
from .policy import AiError, canonical, current_principal, digest, fields, identifier


ROLE = "teruisi_ai_market_plan_attestor"
REQUEST_SCHEMA = "business-market-v2-execution-plan-prepare-v1"


def prepare(body, principal):
    """Owner-bound snapshot; enabled only for explicit isolated preparation."""
    fields(body, {"schemaVersion", "executionReportId", "contextProofDigest"},
        {"schemaVersion", "executionReportId", "contextProofDigest"})
    if body["schemaVersion"] != REQUEST_SCHEMA:
        raise AiError("市场五Agent执行计划协议无效")
    if getattr(settings, "AI_MARKET_V2_EXECUTION_PLAN_ENABLED", False) is not True:
        raise AiError("市场五Agent执行计划尚未启用", "market_v2_plan_disabled", 409)
    actor = current_principal(principal, admin=True)
    report_id = identifier(body["executionReportId"], "executionReportId")
    report = m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
    snapshot = json.loads(report.snapshot_json)
    root = snapshot.get("executionRoot", {})
    if (report.owner_email != actor.email.lower()
            or report.scope_json != "null"
            or canonical(snapshot) != report.snapshot_json
            or snapshot.get("executionProfile") != previous.contract.PROFILE
            or snapshot.get("reportId") != report.id
            or report.workflow.status != "paused"
            or report.workflow.error_code != previous.contract.PAUSE_REASON
            or report.workflow.model_id != ""
            or report.workflow.provider_round_count != 0
            or report.workflow.tool_call_count != 0
            or m.AiAgentJobs.objects.filter(
                workflow_run_id=report.workflow_id).exists()
            or m.AiWorkflowNodeRuns.objects.filter(
                run_id=report.workflow_id).exists()):
        raise AiError("市场执行档案或账号已经变化", "conflict", 409)
    owning = previous._root(root.get("admittedReportId"), principal)
    if owning != root:
        raise AiError("市场原报告、选择和材料已变化", "conflict", 409)
    fixed = {"executionReportId": report.id, **owning,
        "contextProofDigest": body["contextProofDigest"],
        "executionSnapshotDigest": digest(report.snapshot_json)}
    entries = transport.catalog(principal, previous.contract.SURFACE)
    built = contract.build(fixed, entries)
    # Preparation is not an attestation. SQL later recomputes and binds the
    # protected 0061 proof before creating one immutable plan row.
    if (current_principal(principal, admin=True).email.lower() != actor.email.lower()
            or previous._root(root["admittedReportId"], principal) != owning
            or contract.build(fixed, transport.catalog(principal,
                previous.contract.SURFACE))["planDigest"] != built["planDigest"]):
        raise AiError("市场计划准备期间目录或来源变化", "conflict", 409)
    return built


def attest(execution_report_id, plan_json):
    """Only the independent NOLOGIN attestor can persist this closed plan."""
    if getattr(settings, "AI_MARKET_V2_EXECUTION_PLAN_ENABLED", False) is not True:
        raise AiError("市场五Agent执行计划尚未启用", "market_v2_plan_disabled", 409)
    execution_report_id = identifier(execution_report_id, "executionReportId")
    if type(plan_json) is not str or len(plan_json.encode("utf-8")) > 16384:
        raise AiError("市场计划规范JSON容量无效", "invalid_request", 400)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        if cursor.fetchone()[0] != ROLE:
            raise AiError("市场计划须由独立证明角色提交", "access_denied", 403)
        cursor.execute("SELECT public.ai_market_v2_attest_execution_plan(%s,%s)",
            [execution_report_id, plan_json])
        plan_id = cursor.fetchone()[0]
    return {"planId": plan_id, "executionReportId": execution_report_id,
        "agentDispatchSupported": False, "providerCallsAllowed": False}


def read(execution_report_id, principal):
    execution_report_id = identifier(execution_report_id, "executionReportId")
    actor = current_principal(principal, admin=True)
    row = AppUser.objects.filter(email=actor.email.lower(), status="active").first()
    if row is None:
        raise AiError("市场计划账号失效", "access_denied", 403)
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.ai_market_v2_execution_plan_receipt(%s,%s,%s)",
            [execution_report_id, actor.email.lower(), row.version])
        raw = cursor.fetchone()[0]
    value = json.loads(raw) if type(raw) is str else raw
    if (value.get("schemaVersion") != contract.SCHEMA
            or value.get("executionProfile") != contract.PROFILE
            or value.get("executionRoot", {}).get("ownerEmail") != actor.email.lower()
            or value.get("executionRoot", {}).get("executionReportId") !=
                execution_report_id
            or value.get("agentDispatchSupported") is not False):
        raise AiError("市场计划窄回执身份无效", "conflict", 409)
    return value
