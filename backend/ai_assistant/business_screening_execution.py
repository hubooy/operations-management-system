"""Private per-microstep checks prepared outside the global write transaction.

This module does not register a profile. Complete receipt reconstruction and
numeric diagnosis run outside mutation; reservations only recheck live roots,
the actual node, its lease and the unchanged immutable ledger prefix.
"""
from dataclasses import dataclass
import json

from django.db import connection

from . import models as m, workflows
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_screening_permission as permission, business_screening_receipts as receipts
from . import business_screening_tools as tools
from .policy import AiError, authorize_owner, canonical, current_principal, digest, fields

_TOKEN = object()


def _reject(message="筛查任务执行绑定已变化", code="conflict"):
    raise AiError(message, code, 409)


def _job(job, principal):
    current_principal(principal, admin=True, write=True)
    actual = m.AiAgentJobs.objects.filter(pk=job.id).first()
    if actual is None or not actual.workflow_run_id or actual.workflow_node_key not in contract.ROLES:
        _reject()
    authorize_owner(actual, principal)
    if actual.status != "running" or actual.cancel_requested:
        _reject("筛查Agent不在有效执行状态", "lease_lost")
    report = m.AiReportRun.objects.filter(workflow_id=actual.workflow_run_id).select_related("workflow").first()
    if report is None:
        _reject("筛查Agent缺少固定报告")
    report, snapshot, reference, _, _, _ = runtime.bound(report, principal)
    flow = report.workflow
    if flow.status not in {"queued", "running"} or flow.cancel_requested:
        _reject("父工作流已停止")
    graph = workflows.validate_graph(contract.graph("budgetRef" in snapshot))
    if flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph) or flow.dry_run:
        _reject("固定五角色图不一致")
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id).order_by("position")[:25])
    if len(nodes) != len(graph["nodes"]):
        _reject("实际节点不完整")
    by_key = {}
    for index, (node, expected) in enumerate(zip(nodes, graph["nodes"])):
        if (node.position != index or node.node_key != expected["key"] or node.node_type != expected["type"]
                or node.instruction != expected["instruction"] or node.depends_on_json != canonical(expected["dependsOn"])):
            _reject("实际节点与固定图不符")
        by_key[node.node_key] = node
    node = by_key[actual.workflow_node_key]
    deps = json.loads(node.depends_on_json)
    if any(by_key[key].status != "completed" or not by_key[key].output_json for key in deps):
        _reject("前置专业结果未完成")
    expected_input = {"workflowInput":reference, "dependencies":{key:json.loads(by_key[key].output_json) for key in deps}}
    if (node.agent_job_id != actual.id or node.status != "running" or actual.task != node.instruction
            or actual.input_json != canonical(expected_input) or node.input_json != actual.input_json
            or actual.owner_email != flow.owner_email or actual.scope_json != flow.scope_json
            or any(getattr(actual,key) != getattr(flow,key) for key in
                ("model_id", "model_version", "allowed_tools_json", "tool_policy_digest"))
            or workflows.execution_guidance(actual.id) != workflows.execution_guidance(flow.id)):
        _reject("筛查任务、依赖输入或指导与固定父任务不一致")
    guard = {key:getattr(actual,key) for key in ("id", "owner_email", "scope_json", "task", "input_json",
        "model_id", "model_version", "allowed_tools_json", "tool_policy_digest", "workflow_run_id",
        "workflow_node_key", "lease_token", "lease_epoch", "version")}
    guard.update(nodeId=node.id, nodeVersion=node.version, guidanceDigest=digest(workflows.execution_guidance(actual.id)))
    return actual, report, snapshot, guard


def _ledger(job_id, ignored=None):
    """Hash exact persisted prefix; allow only this microstep's own new dispatch."""
    ignored = ignored or (None, None)
    job = m.AiAgentJobs.objects.get(pk=job_id)
    providers, tool_rows = [], []
    for ordinal,row in enumerate(m.AiAgentProviderDispatches.objects.filter(job_id=job_id).order_by("dispatch_ordinal")[:41],1):
        if (ordinal > contract.MAX_TOOL_ROUNDS or row.dispatch_ordinal != ordinal or row.owner_email != job.owner_email or row.actor_role != "admin"
                or any(getattr(row,key) != getattr(job,key) for key in ("model_id","model_version","tool_policy_digest"))):
            _reject("模型派发身份、策略或连续序号不符")
        if ignored == ("provider", row.id):
            continue
        result = m.AiAgentProviderResults.objects.filter(dispatch_id=row.id).first()
        if row.state != "succeeded" or result is None:
            _reject("模型派发缺少已知成功回执，禁止重放", "provider_dispatch_unknown")
        if result is not None and digest(result.response_json) != result.response_digest:
            _reject("模型持久回执摘要损坏")
        providers.append([row.id,row.dispatch_ordinal,row.state,row.request_digest,row.lease_epoch,
            row.owner_email,row.actor_role,row.model_id,row.model_version,row.tool_policy_digest,
            result.response_digest if result else None])
    for ordinal,row in enumerate(m.AiAgentToolDispatches.objects.filter(job_id=job_id).order_by("tool_call_ordinal")[:41],1):
        if ordinal > contract.MAX_TOOL_CALLS or row.tool_call_ordinal != ordinal or digest(row.arguments_json) != row.arguments_digest:
            _reject("工具派发参数摘要或连续序号损坏")
        if ignored == ("tool", row.id):
            continue
        result = m.AiAgentToolResults.objects.filter(tool_dispatch_id=row.id).first()
        if result is not None and digest(result.result_json) != result.result_digest:
            _reject("工具持久回执摘要损坏")
        tool_rows.append([row.id,row.provider_dispatch_id,row.tool_call_ordinal,row.state,row.tool_name,
            row.arguments_digest,row.provider_call_id,row.invocation_id,row.lease_epoch,result.result_digest if result else None])
    if len(providers) > contract.MAX_TOOL_ROUNDS or len(tool_rows) > contract.MAX_TOOL_CALLS:
        _reject("持久派发次数超过固定上限")
    return digest([providers,tool_rows])


@dataclass(frozen=True, slots=True, init=False)
class PreparedStep:
    _guard_json: str
    _proof_json: str
    _ledger_digest: str
    _permit: object
    _tools: object

    def __init__(self, token, guard, proof, ledger, permit, prepared_tools):
        if token is not _TOKEN:
            _reject("不能从JSON恢复执行步骤")
        object.__setattr__(self,"_guard_json",canonical(guard))
        object.__setattr__(self,"_proof_json",canonical(proof))
        object.__setattr__(self,"_ledger_digest",ledger)
        object.__setattr__(self,"_permit",permit)
        object.__setattr__(self,"_tools",prepared_tools)

    @property
    def proof(self):
        return json.loads(self._proof_json)


def prepare(job, principal):
    if connection.in_atomic_block:
        _reject("筛查任务读取证明须在最外层事务外准备")
    actual, report, snapshot, guard = _job(job, principal)
    permit = permission.get(report, principal)
    before = _ledger(actual.id)
    ready = tools.prepare_for_report(report, principal, resolve_budget=True)
    proof = receipts.progress(actual, snapshot, principal, _prepared=ready)
    result = PreparedStep(_TOKEN,guard,proof,before,permit,ready)
    check(result, actual, principal)
    return result


def check(prepared, job, principal, *, dispatch=None):
    if type(prepared) is not PreparedStep:
        _reject("缺少本进程的实际读取证明")
    actual, _, _, guard = _job(job, principal)
    if canonical(guard) != prepared._guard_json:
        _reject("步骤准备期间实际任务发生变化", "lease_lost")
    workflows._leased((actual.id,actual.lease_token,actual.lease_epoch))
    permission.check(prepared._permit, principal)
    ignored = None
    if dispatch is not None:
        cls = type(dispatch)
        if cls not in (m.AiAgentProviderDispatches,m.AiAgentToolDispatches):
            _reject()
        saved = cls.objects.filter(pk=dispatch.id,job_id=actual.id,lease_epoch=actual.lease_epoch).first()
        if saved is None:
            _reject("派发不属于当前步骤")
        names = (("dispatch_ordinal","owner_email","actor_role","model_id","model_version","tool_policy_digest","request_digest")
            if cls is m.AiAgentProviderDispatches else
            ("provider_dispatch_id","tool_call_ordinal","provider_call_id","tool_name","arguments_json","arguments_digest","invocation_id"))
        if any(getattr(saved,key) != getattr(dispatch,key) for key in names):
            _reject("当前步骤的持久派发身份已变化")
        if cls is m.AiAgentProviderDispatches:
            result = m.AiAgentProviderResults.objects.filter(dispatch_id=saved.id).first()
            result_valid = result is not None and digest(result.response_json) == result.response_digest
        else:
            result = m.AiAgentToolResults.objects.filter(tool_dispatch_id=saved.id).first()
            result_valid = result is not None and digest(result.result_json) == result.result_digest
        if not ((saved.state == "calling" and result is None) or (saved.state == "succeeded" and result_valid)):
            _reject("当前派发不是可验证的已知步骤，禁止重放", "provider_dispatch_unknown" if cls is m.AiAgentProviderDispatches else "tool_dispatch_unknown")
        ignored = ("provider" if cls is m.AiAgentProviderDispatches else "tool", saved.id)
    if _ledger(actual.id,ignored) != prepared._ledger_digest:
        _reject("读取证明准备后账本已变化")
    return prepared.proof


def validate_call(prepared, call):
    guard, proof = json.loads(prepared._guard_json), prepared.proof
    reference = json.loads(guard["input_json"])["workflowInput"]
    fields(call, {"id","name","arguments"}, {"id","name","arguments"})
    name, args = call["name"], call["arguments"]
    if (name not in contract.TOOLS or type(args) is not dict
            or args.get("runId") != reference["evidenceRunId"] or args.get("reportId") != reference["reportId"]
            or args.get("screeningId") != reference["screeningIntent"]["id"]):
        _reject("工具只可读取当前固定筛查", "access_denied")
    if name == contract.PACKAGE_TOOL:
        if (set(args)-{"runId","reportId","screeningId","role","offset"}
                or args.get("role") != guard["workflow_node_key"] or type(args.get("offset",0)) is not int
                or proof["package"]["complete"] or args.get("offset",0) != proof["package"]["nextOffset"]):
            _reject("本人角色包必须连续完整读取", "screening_read_incomplete")
    elif not proof["package"]["complete"]:
        _reject("必须先完整读取本人角色包", "screening_read_incomplete")
    elif name == contract.BUDGET_TOOL:
        if ("budgetRef" not in reference or set(args)-{"runId","reportId","screeningId","offset"}
                or type(args.get("offset",0)) is not int or proof["budget"]["complete"]
                or args.get("offset",0) != proof["budget"]["nextOffset"]):
            _reject("固定预算必须连续完整读取", "screening_read_incomplete")


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAnswer:
    _step_guard: str
    _ledger_digest: str
    _answer_digest: str

    def __init__(self, token, prepared, answer):
        if token is not _TOKEN or type(prepared) is not PreparedStep:
            _reject("不能从JSON恢复已验证输出")
        object.__setattr__(self,"_step_guard",prepared._guard_json)
        object.__setattr__(self,"_ledger_digest",prepared._ledger_digest)
        object.__setattr__(self,"_answer_digest",digest(answer))


def check_answer(validated, prepared, answer):
    if (type(validated) is not ValidatedAnswer or type(prepared) is not PreparedStep
            or validated._step_guard != prepared._guard_json
            or validated._ledger_digest != prepared._ledger_digest or validated._answer_digest != digest(answer)):
        _reject("输出未经当前步骤的完整引用校验")


def validate_answer(prepared, answer, principal):
    """Expensive reference resolution stays outside the commit transaction."""
    from . import business_screening_diagnosis as diagnosis
    if connection.in_atomic_block:
        _reject("筛查输出引用须在最外层事务之外验证")
    proof = prepared.proof
    if not proof["package"]["complete"] or ((proof["budget"]["required"] or proof["budget"]["started"])
            and not proof["budget"]["complete"]):
        _reject("当前Agent没有完整独立读取证明", "screening_read_incomplete")
    role = proof["role"]
    parsed = diagnosis.validate_answer(role, answer)
    if role != "independent_review":
        diagnosis.validate(parsed["diagnosis"] if role == "report" else parsed,
            prepared._tools.report, principal, role=role, prepared=prepared._tools)
    return ValidatedAnswer(_TOKEN,prepared,answer)
