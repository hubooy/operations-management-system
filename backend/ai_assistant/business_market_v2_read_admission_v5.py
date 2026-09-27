"""Test-only owning gap inspection; no model, attestation or read grant."""
import json

from django.conf import settings
from django.db import DatabaseError

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_execution_plan as plans
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_read_admission_v5_contract as contract
from . import business_market_v2_active_synthetic_contract as synthetic_contract
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from .policy import AiError, canonical, current_principal, digest, identifier


def _invalid():
    raise AiError("市场同报告已读准入证据不足",
        "market_v2_read_admission_unverified", 409)


def _json(raw, cap=40000):
    if type(raw) is not str or len(raw.encode("utf-8")) > cap:
        _invalid()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        _invalid()
    if canonical(value) != raw:
        _invalid()
    return value


def _synthetic_chain(flow, with_budget):
    jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id)[:6])
    if len(jobs) != 5 or {job.workflow_node_key for job in jobs} != set(runtime.ROLES):
        _invalid()
    by_role = {job.workflow_node_key: job for job in jobs}
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id)
        .order_by("position")[:7])
    graph = execution.graph(with_budget)
    if (len(nodes) != 6 or any(
            (node.position, node.node_key, node.node_type,
                node.depends_on_json, node.agent_job_id) !=
            (index, part["key"], part["type"], canonical(part["dependsOn"]),
                by_role[part["key"]].id if part["type"] == "agent" else None)
            for index, (node, part) in enumerate(zip(nodes, graph["nodes"])))):
        _invalid()
    expected_tools = synthetic_contract.role_tools(with_budget)
    for job in jobs:
        providers = list(m.AiAgentProviderDispatches.objects.filter(job_id=job.id)[:2])
        tools = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id)[:2])
        if (job.status != "paused" or job.phase != "paused"
                or job.model_id != "market-v2-synthetic-only"
                or len(providers) != 1 or len(tools) != 1):
            _invalid()
        provider, tool = providers[0], tools[0]
        provider_result = m.AiAgentProviderResults.objects.filter(
            dispatch_id=provider.id).first()
        tool_result = m.AiAgentToolResults.objects.filter(
            tool_dispatch_id=tool.id).first()
        if (provider_result is None or tool_result is None
                or provider.job_id != job.id or tool.job_id != job.id
                or tool.provider_dispatch_id != provider.id
                or provider.model_id != job.model_id
                or provider.model_version != job.model_version
                or provider.state != "succeeded" or tool.state != "succeeded"):
            _invalid()
        response = _json(provider_result.response_json)
        result = _json(tool_result.result_json)
        arguments = _json(tool.arguments_json, 8192)
        if (response.get("syntheticOnly") is not True
                or response.get("externalProviderCalled") is not False
                or response.get("calls") != [{"id": tool.provider_call_id,
                    "name": tool.tool_name, "arguments": arguments}]
                or tool.tool_name != expected_tools[job.workflow_node_key]
                or tool.arguments_digest != digest(tool.arguments_json)
                or provider_result.response_digest != digest(
                    provider_result.response_json)
                or tool_result.result_digest != digest(tool_result.result_json)
                or result.get("toolName") != tool.tool_name
                or result.get("ok") is not True
                or result.get("data", {}).get("syntheticOnly") is not True
                or result.get("data", {}).get("role") != job.workflow_node_key
                or result.get("data", {}).get("persistedRead") is not False
                or result.get("data", {}).get("numericCitationAllowed") is not False):
            _invalid()
    return list(runtime.ROLES)


def inspect(source_execution_report_id, candidate_report_id, principal):
    """Describe a proven blocker; never present a candidate as an Agent read."""
    if (getattr(settings, "AI_MARKET_V2_READ_ADMISSION_V5_ENABLED", False)
            is not True or settings.DJANGO_ENVIRONMENT != "test"):
        raise AiError("市场新版本已读准入诊断未启用",
            "market_v2_read_admission_disabled", 409)
    source_id = identifier(source_execution_report_id, "executionReportId")
    candidate_id = identifier(candidate_report_id, "candidateReportId")
    actor = current_principal(principal, admin=True)
    try:
        plan = plans.read(source_id, principal)
        root = plan["executionRoot"]
        report = m.AiReportRun.objects.select_related("workflow").filter(
            pk=candidate_id).first()
        if (report is None or report.owner_email != actor.email.lower()
                or report.scope_json != "null"
                or root["ownerEmail"] != actor.email.lower()
                or root["executionReportId"] != source_id):
            _invalid()
        snapshot = _json(report.snapshot_json, 16384)
        flow = report.workflow
        if (snapshot.get("reportId") != report.id
                or flow.owner_email != report.owner_email
                or flow.scope_json != "null"
                or flow.status != "paused"
                or flow.allowed_tools_json != canonical(list(execution.TOOL_ORDER))
                or flow.tool_policy_digest != execution.CATALOG_DIGEST):
            _invalid()
        profile = snapshot.get("executionProfile")
        if profile == execution.PROFILE:
            if (report.id != source_id or flow.model_id != ""
                    or flow.dry_run != 0
                    or m.AiAgentJobs.objects.filter(
                        workflow_run_id=flow.id).exists()):
                _invalid()
            roles = []
            synthetic = False
            topology = False
        elif profile == contract.SYNTHETIC_PROFILE:
            anchor = snapshot.get("syntheticRoot")
            expected = {"planId": plan["planId"],
                "executionReportId": source_id,
                "admittedReportId": root["admittedReportId"],
                "contextProofDigest": root["contextProofDigest"],
                "marketContextDigest": root["marketContextDigest"],
                "ownerEmail": root["ownerEmail"]}
            if (report.id == source_id or anchor != expected
                    or flow.model_id != "market-v2-synthetic-only"
                    or flow.dry_run != 1
                    or flow.graph_json != canonical(execution.graph(
                        root["withBudget"]))
                    or flow.graph_digest != execution.GRAPH_DIGESTS[
                        root["withBudget"]]
                    or snapshot.get("syntheticOnly") is not True
                    or snapshot.get("externalProviderCalled") is not False):
                _invalid()
            roles = _synthetic_chain(flow, root["withBudget"])
            synthetic = True
            topology = True
        else:
            _invalid()
        observed = {"reportId": report.id, "workflowId": flow.id,
            "ownerEmail": report.owner_email, "profile": profile,
            "sourceExecutionReportId": source_id, "planId": plan["planId"],
            "workflowStatus": flow.status, "modelId": flow.model_id,
            "dryRun": flow.dry_run == 1, "syntheticOnly": synthetic,
            "externalProviderCalled": False, "jobRoles": roles,
            "sameJobProviderToolChainsObserved": topology}
        value = contract.assess(root, plan["planId"], observed)
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError, AttributeError) as error:
        raise AiError("市场同报告已读准入证据不足",
            "market_v2_read_admission_unverified", 409) from error
    return value
