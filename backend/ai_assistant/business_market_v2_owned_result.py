"""Default-closed owning read of a five-Agent market result candidate.

This is deliberately not an execution, citation, approval or publication path.
In particular the 0064 synthetic chain has no 0062 receipts and cannot pass.
"""
import json

from django.conf import settings
from django.db import DatabaseError

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_execution_plan as plan_reader
from . import business_market_v2_execution_plan_contract as plan_contract
from . import business_market_v2_execution_snapshot as execution
from . import business_market_v2_execution_snapshot_contract as execution_contract
from . import business_market_v2_read_attestation as receipts
from . import business_market_v2_result_candidate as pure
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from .policy import AiError, canonical, current_principal, digest, identifier


_MAX_TEXT = 40000
_MAX_TOTAL = 128 * 1024


def _reject(message="市场五Agent结果缺少拥有方持久证据"):
    raise AiError(message, "market_v2_owned_result_unverified", 409)


def _json(raw, limit):
    if type(raw) is not str or len(raw.encode("utf-8")) > limit:
        _reject()
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise AiError("市场五Agent持久JSON无效",
            "market_v2_owned_result_unverified", 409) from error
    if canonical(value) != raw:
        _reject("市场五Agent持久JSON不是规范版本")
    return value


def _load(report_id, principal):
    actor = current_principal(principal, admin=True)
    report = m.AiReportRun.objects.select_related("workflow").filter(
        pk=report_id).first()
    if report is None or report.owner_email != actor.email.lower() or \
            report.scope_json != "null":
        _reject("市场五Agent报告不属于当前管理员")
    flow = report.workflow
    snapshot = _json(report.snapshot_json, 16384)
    if (snapshot.get("executionProfile") != execution_contract.PROFILE
            or snapshot.get("reportId") != report.id
            or flow.owner_email != report.owner_email
            or flow.scope_json != "null"
            or flow.graph_json != canonical(execution_contract.graph(
                snapshot["executionRoot"]["withBudget"]))
            or flow.graph_digest != execution_contract.GRAPH_DIGESTS[
                snapshot["executionRoot"]["withBudget"]]
            or flow.allowed_tools_json != canonical(list(
                execution_contract.TOOL_ORDER))
            or flow.tool_policy_digest != execution_contract.CATALOG_DIGEST
            or flow.status not in {"paused", "running", "completed"}):
        _reject("市场五Agent报告或工作流已变化")
    plan = plan_reader.read(report.id, principal)
    root = plan_contract.root(plan["executionRoot"])
    original_root = execution._root(root["admittedReportId"], principal)
    if (root["executionReportId"] != report.id
            or root["ownerEmail"] != actor.email.lower()
            or root["executionSnapshotDigest"] != digest(report.snapshot_json)
            or snapshot["executionRoot"] != original_root
            or any(root[key] != value for key, value in original_root.items())
            or plan.get("providerCallsAllowed") is not False
            or plan.get("agentDispatchSupported") is not False):
        _reject("市场来源、计划或付费权限已变化")
    graph = execution_contract.graph(root["withBudget"])
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id)
        .order_by("position")[:7])
    if (len(nodes) != 6 or any(
            (node.position, node.node_key, node.node_type,
                node.depends_on_json) !=
            (index, part["key"], part["type"], canonical(part["dependsOn"]))
            for index, (node, part) in enumerate(zip(nodes, graph["nodes"])))):
        _reject("市场五Agent节点集合或依赖已变化")
    if (nodes[-1].node_key != "human_review"
            or nodes[-1].status not in {"pending", "waiting_review"}
            or nodes[-1].output_json is not None):
        _reject("市场结果候选不能代替人工审核")
    jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id)[:6])
    if len(jobs) != 5 or len({job.id for job in jobs}) != 5:
        _reject("市场五Agent实际任务集合不完整")
    by_id = {job.id: job for job in jobs}
    results, fence = [], [report.snapshot_json, flow.version,
        flow.status, flow.graph_json, flow.input_json, canonical(plan)]
    total = 0
    fence.append([nodes[-1].id, nodes[-1].version, nodes[-1].status,
        nodes[-1].output_json])
    for role, node in zip(runtime.ROLES, nodes):
        job = by_id.get(node.agent_job_id)
        if (job is None or node.node_key != role or node.status != "completed"
                or job.workflow_node_key != role or job.status != "completed"
                or job.phase != "completed" or job.output_json != node.output_json
                or job.owner_email != actor.email.lower() or job.scope_json != "null"
                or job.model_id != flow.model_id
                or job.model_version != flow.model_version
                or job.allowed_tools_json != flow.allowed_tools_json
                or job.tool_policy_digest != flow.tool_policy_digest):
            _reject("市场五Agent任务、模型或节点输出不一致")
        output = _json(job.output_json, 24000)
        if type(output) is not dict or set(output) != {"answer", "numericClaims"}:
            _reject("市场Agent持久输出缺少独立数值引用清单")
        providers = list(m.AiAgentProviderDispatches.objects.filter(job_id=job.id)
            .order_by("dispatch_ordinal")[:21])
        tools = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id)
            .order_by("tool_call_ordinal")[:41])
        if (not providers or len(providers) > 20 or not tools
                or len(tools) > 40
                or len({row.id for row in providers}) != len(providers)
                or len({row.id for row in tools}) != len(tools)
                or job.provider_round_count != len(providers)
                or job.tool_call_count != len(tools)):
            _reject("市场Agent派发账本数量无效")
        provider_rows = {}
        for provider in providers:
            saved = m.AiAgentProviderResults.objects.filter(
                dispatch_id=provider.id).first()
            if (saved is None or provider.state != "succeeded"
                    or provider.job_id != job.id
                    or provider.owner_email != job.owner_email
                    or provider.model_id != job.model_id
                    or provider.model_version != job.model_version
                    or provider.tool_policy_digest != job.tool_policy_digest
                    or saved.response_digest != digest(saved.response_json)):
                _reject("市场Agent模型派发与持久结果不一致")
            response = _json(saved.response_json, _MAX_TEXT)
            calls = response.get("calls", response.get("toolCalls", []))
            if type(calls) is not list or len(calls) > 40:
                _reject("市场Agent模型工具调用列表无效")
            provider_rows[provider.id] = (provider, saved, calls)
            total += len(saved.response_json.encode("utf-8"))
            if total > _MAX_TOTAL:
                _reject("市场五Agent持久结果超出有界复核容量")
            fence.append([provider.id, provider.state, provider.model_id,
                provider.model_version, saved.response_json,
                saved.response_digest])
        reads = []
        matched_calls = {provider_id: [] for provider_id in provider_rows}
        for tool in tools:
            provider_row = provider_rows.get(tool.provider_dispatch_id)
            saved = m.AiAgentToolResults.objects.filter(
                tool_dispatch_id=tool.id).first()
            if (provider_row is None or saved is None
                    or tool.job_id != job.id or tool.state != "succeeded"
                    or tool.arguments_digest != digest(tool.arguments_json)
                    or saved.result_digest != digest(saved.result_json)):
                _reject("市场Agent工具派发与持久结果不一致")
            arguments = _json(tool.arguments_json, 8192)
            result = _json(saved.result_json, _MAX_TEXT)
            provider, _, calls = provider_row
            receipt = receipts.read(tool.id, principal)
            if (receipt["jobId"] != job.id
                    or receipt["providerDispatchId"] != provider.id
                    or receipt["toolDispatchId"] != tool.id
                    or receipt["toolResultDigest"] != saved.result_digest):
                _reject("市场Agent已读回执不属于当前派发")
            matched_calls[provider.id].append({"id": tool.provider_call_id,
                "name": tool.tool_name, "arguments": arguments})
            reads.append({"receipt": receipt,
                "provider": {"jobId": job.id, "dispatchId": provider.id,
                    "state": provider.state, "callId": tool.provider_call_id,
                    "toolDispatchId": tool.id, "toolName": tool.tool_name,
                    "arguments": arguments, "calls": calls},
                "result": result})
            fence.append([tool.id, tool.provider_dispatch_id,
                tool.arguments_json, tool.arguments_digest,
                saved.result_json, saved.result_digest, receipt["receiptDigest"]])
            total += (len(tool.arguments_json.encode("utf-8"))
                + len(saved.result_json.encode("utf-8")))
            if total > _MAX_TOTAL:
                _reject("市场五Agent持久结果超出有界复核容量")
        if any(calls != matched_calls[provider_id] for provider_id,
                (_, _, calls) in provider_rows.items()):
            _reject("市场Agent模型调用与工具派发不是完整一对一账本")
        results.append({"role": role, "jobId": job.id,
            "answer": output["answer"], "reads": reads,
            "numericClaims": output["numericClaims"]})
        total += len(job.output_json.encode("utf-8"))
        fence.append([role, node.id, node.version, node.status,
            node.output_json, job.id, job.version, job.status,
            job.model_id, job.model_version, job.output_json,
            job.provider_round_count, job.tool_call_count])
    if total > _MAX_TOTAL:
        _reject("市场五Agent持久结果超出有界复核容量")
    return root, results, digest(fence)


def read(execution_report_id, principal):
    """Rebuild and double-check owned candidate, with every grant still false."""
    if getattr(settings, "AI_MARKET_V2_OWNED_RESULT_ENABLED", False) is not True:
        raise AiError("市场五Agent拥有方结果尚未启用",
            "market_v2_owned_result_disabled", 409)
    report_id = identifier(execution_report_id, "executionReportId")
    try:
        root, results, before = _load(report_id, principal)
        checked = pure.check(root, results)
        _, _, after = _load(report_id, principal)
        if before != after:
            _reject("市场五Agent复核期间报告或来源修订发生变化")
    except (AnalysisContractError, DatabaseError, KeyError, TypeError,
            ValueError, AttributeError) as error:
        raise AiError("市场五Agent结果或数值引用未通过纯校验",
            "market_v2_owned_result_unverified", 409) from error
    return {**checked, "candidateOnly": True,
        "agentReadAuthority": False, "providerCallsAllowed": False,
        "readFenceDigest": before}
