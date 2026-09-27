"""Exact five-role continuation guarded by an actual process-local permit."""
import json

from . import models as m, workflows
from . import business_screening_runtime_contract as contract, business_screening_permission as permission
from .policy import AiError, canonical, digest


def checked_nodes(row, prepared, principal):
    proof = permission.check(prepared, principal)
    if proof["workflowId"] != row.id:
        raise AiError("容量许可不属于当前工作流", "conflict", 409)
    report = m.AiReportRun.objects.filter(pk=proof["reportId"],workflow_id=row.id).first()
    if report is None:
        raise AiError("固定报告不存在", "conflict", 409)
    snapshot = json.loads(report.snapshot_json)
    graph = workflows.validate_graph(contract.graph("budgetRef" in snapshot))
    if row.graph_json != canonical(graph) or row.graph_digest != digest(graph):
        raise AiError("固定工作流图已变化", "conflict", 409)
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=row.id).order_by("position")[:25])
    if len(nodes) != len(graph["nodes"]):
        raise AiError("实际节点不完整", "conflict", 409)
    for position, (actual, expected) in enumerate(zip(nodes,graph["nodes"])):
        if (actual.position != position or actual.node_key != expected["key"] or actual.node_type != expected["type"]
                or actual.instruction != expected["instruction"] or actual.depends_on_json != canonical(expected["dependsOn"])):
            raise AiError("实际节点与固定工作流图不一致", "conflict", 409)
    _check_children(row, nodes)
    return nodes


def _check_children(row, nodes):
    """Check before copying a terminal child's output into dependent inputs."""
    by_key = {node.node_key:node for node in nodes}
    ids = [node.agent_job_id for node in nodes if node.agent_job_id is not None]
    children = list(m.AiAgentJobs.objects.filter(workflow_run_id=row.id)[:6])
    if len(ids) != len(set(ids)) or len(children) > 5 or {child.id for child in children} != set(ids):
        raise AiError("筛查节点必须绑定本工作流的独立实际任务", "conflict", 409)
    jobs = {child.id:child for child in children}
    guidance = workflows.execution_guidance(row.id)
    for node in nodes:
        if node.node_type == "human_review":
            if node.agent_job_id is not None:
                raise AiError("人工复核不能绑定模型任务", "conflict", 409)
            continue
        if node.agent_job_id is None:
            if node.status != "pending":
                raise AiError("已开始的专业节点缺少实际任务", "conflict", 409)
            continue
        child = jobs[node.agent_job_id]
        dependencies = [by_key[key] for key in json.loads(node.depends_on_json)]
        if any(parent.status != "completed" or not parent.output_json for parent in dependencies):
            raise AiError("已绑定任务的前置专业结果无效", "conflict", 409)
        expected_input = canonical({"workflowInput":json.loads(row.input_json),
            "dependencies":{parent.node_key:json.loads(parent.output_json) for parent in dependencies}})
        if (node.status == "pending" or child.workflow_node_key != node.node_key
                or child.owner_email != row.owner_email or child.scope_json != row.scope_json
                or child.task != node.instruction or child.input_json != expected_input
                or node.input_json != child.input_json
                or any(getattr(child,key) != getattr(row,key) for key in
                    ("model_id", "model_version", "allowed_tools_json", "tool_policy_digest"))
                or workflows.execution_guidance(child.id) != guidance
                or (node.status == "completed" and (child.status != "completed" or node.output_json != child.output_json))):
            raise AiError("专业子任务身份、输入或已完成结果与当前节点不一致", "conflict", 409)


def step(row, prepared, principal):
    from . import business_parallel
    nodes = checked_nodes(row, prepared, principal)
    return business_parallel.workflow_step(row,principal,nodes,screening_permission=prepared)
