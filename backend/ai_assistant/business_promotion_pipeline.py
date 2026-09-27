"""Unregistered five-role continuation for a future promotion workflow route.

The current workflow_tick still sends every non-approved promotion report to
screening readiness and would repark a resumed run. `_step_after_route` is
private and requires a process-local route token; no production caller issues
that token. A future routing change must atomically own resume and this step.
"""
import json

from business_analysis import screening_package
from . import business_promotion_runtime_contract as contract
from . import business_promotion_runtime_permission as permission
from . import models as m, workflows
from .policy import AiError, canonical, digest

_ROUTE_TOKEN = object()


def _reject(message="词货五角色工作流固定状态已变化"):
    raise AiError(message, "promotion_pipeline_unverified", 409)


def checked_nodes(row, prepared, principal):
    """Only metadata and already persisted child identities; no provider."""
    proof = permission.check(prepared, principal)
    if (proof["workflowId"] != row.id or proof["role"] != "commerce"
            or proof["workflowStatus"] not in {"queued", "running"}
            or row.status not in {"queued", "running"}
            or proof["capacityVerified"] is not True
            or proof["runtimeAdmissionGranted"] is not False):
        _reject("词货流程缺少当前进程的固定容量对象")
    report = m.AiReportRun.objects.filter(pk=proof["reportId"], workflow_id=row.id).first()
    if report is None:
        _reject("词货报告不属于当前流程")
    snapshot = json.loads(report.snapshot_json)
    graph = workflows.validate_graph(contract.graph("budgetRef" in snapshot))
    if row.graph_json != canonical(graph) or row.graph_digest != digest(graph):
        _reject("词货五角色图已变化")
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=row.id).order_by("position")[:7])
    if len(nodes) != len(graph["nodes"]):
        _reject("词货六节点不完整")
    for position, (actual, expected) in enumerate(zip(nodes, graph["nodes"])):
        if (actual.position != position or actual.node_key != expected["key"]
                or actual.node_type != expected["type"]
                or actual.instruction != expected["instruction"]
                or actual.depends_on_json != canonical(expected["dependsOn"])):
            _reject("词货节点与固定图不一致")
    _check_children(row, nodes)
    return nodes


def _check_children(row, nodes):
    ids = [node.agent_job_id for node in nodes if node.agent_job_id is not None]
    jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=row.id)[:6])
    if len(ids) != len(set(ids)) or len(jobs) > 5 or {job.id for job in jobs} != set(ids):
        _reject("词货节点与实际子任务集合不一致")
    by_key = {node.node_key: node for node in nodes}
    by_id = {job.id: job for job in jobs}
    guidance = workflows.execution_guidance(row.id)
    for node in nodes:
        if node.node_type == "human_review":
            if node.agent_job_id is not None:
                _reject("人工复核节点不能绑定模型子任务")
            continue
        if node.agent_job_id is None:
            if node.status != "pending":
                _reject("已开始角色节点缺少实际子任务")
            continue
        job = by_id[node.agent_job_id]
        dependencies = [by_key[key] for key in json.loads(node.depends_on_json)]
        if any(parent.status != "completed" or not parent.output_json for parent in dependencies):
            _reject("已绑定角色的前置结论不完整")
        expected_input = canonical({"workflowInput": json.loads(row.input_json),
            "dependencies": {parent.node_key: json.loads(parent.output_json)
                for parent in dependencies}})
        if (node.status == "pending" or job.workflow_node_key != node.node_key
                or job.owner_email != row.owner_email or job.scope_json != row.scope_json
                or job.task != node.instruction or job.input_json != expected_input
                or node.input_json != job.input_json
                or any(getattr(job, key) != getattr(row, key) for key in
                    ("model_id", "model_version", "allowed_tools_json", "tool_policy_digest"))
                or workflows.execution_guidance(job.id) != guidance
                or (node.status == "completed"
                    and (job.status != "completed" or node.output_json != job.output_json))):
            _reject("词货子任务身份、输入或已完成内容变化")


def _step_after_route(row, prepared, principal, *, route_token):
    """Private tested continuation. No current workflow route can call this."""
    if route_token is not _ROUTE_TOKEN:
        _reject("词货五角色流程尚未接入当前调度")
    from . import business_parallel
    nodes = checked_nodes(row, prepared, principal)
    return business_parallel.workflow_step(row, principal, nodes)


def step(row, prepared, principal):
    """Public entry stays closed until workflow_tick owns the new route."""
    _reject("词货五角色流程尚未接入当前调度")
