"""Process-local human-review preparation for the new promotion profile.

Full five-job content is checked outside mutation. A caller may revalidate the
small persisted state inside its own mutation before its review version CAS.
This module never approves, dispatches, publishes, or sends anything.
"""
from dataclasses import dataclass
import json

from django.db import connection

from . import business_promotion_content as content
from . import business_promotion_runtime_contract as contract
from . import business_screening_creation as screening_creation
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


_TOKEN = object()
MAX_STATE_BYTES = 512 * 1024
MAX_PROVIDERS = 20 * 5
MAX_TOOLS = 40 * 5

PROVIDERS = """
SELECT d.job_id,d.dispatch_ordinal,d.id,d.state,d.lease_epoch,
 encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex'),
 r.dispatch_id,r.response_digest,
 encode(sha256(convert_to(r.response_json,'UTF8')),'hex')
FROM ai_agent_provider_dispatches d LEFT JOIN ai_agent_provider_results r ON r.dispatch_id=d.id
WHERE d.job_id=ANY(%s) ORDER BY d.job_id,d.dispatch_ordinal LIMIT 101
"""
TOOLS = """
SELECT d.job_id,d.tool_call_ordinal,d.id,d.state,d.provider_dispatch_id,d.lease_epoch,
 encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex'),
 r.tool_dispatch_id,r.result_digest,
 encode(sha256(convert_to(r.result_json,'UTF8')),'hex')
FROM ai_agent_tool_dispatches d LEFT JOIN ai_agent_tool_results r ON r.tool_dispatch_id=d.id
WHERE d.job_id=ANY(%s) ORDER BY d.job_id,d.tool_call_ordinal LIMIT 201
"""


def _reject(message="词货人工复核准备状态已变化"):
    raise AiError(message, "promotion_review_unverified", 409)


def _ledger(job_ids):
    if connection.vendor != "postgresql" or len(job_ids) != 5 or len(set(job_ids)) != 5:
        _reject("复核账本需要五个不同的PostgreSQL实际任务")
    with connection.cursor() as cursor:
        cursor.execute(PROVIDERS, [job_ids])
        providers = cursor.fetchall()
        cursor.execute(TOOLS, [job_ids])
        tools = cursor.fetchall()
    if len(providers) > MAX_PROVIDERS or len(tools) > MAX_TOOLS:
        _reject("五角色派发账本超过固定容量")
    return digest([providers, tools])


def _state(report_id, principal, *, write):
    current_principal(principal, admin=True, write=write)
    if principal.scope is not None:
        _reject("词货人工复核仅允许无范围管理员")
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        pk=identifier(report_id, "reportId")).first()
    if report is None:
        _reject("词货待复核报告不存在")
    authorize_owner(report, principal)
    flow = report.workflow
    authorize_owner(flow, principal)
    try:
        snapshot = json.loads(report.snapshot_json)
        graph = contract.graph(bool(report.budget_plan_id))
        if (snapshot.get("executionProfile") != contract.PROFILE
                or flow.status != "waiting_review" or flow.current_node_key != "human_review"
                or flow.cancel_requested or flow.dry_run
                or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)):
            _reject("报告已离开待人工复核的固定五角色状态")
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
        if len(nodes) != 6 or any(
                (node.position, node.node_key, node.node_type, node.instruction,
                 node.depends_on_json) != (index, spec["key"], spec["type"],
                 spec["instruction"], canonical(spec["dependsOn"]))
                for index, (node, spec) in enumerate(zip(nodes, graph["nodes"]))):
            _reject("待复核节点不属于固定工作流图")
        human = nodes[-1]
        if (human.status != "waiting_review" or human.agent_job_id is not None
                or human.output_json is not None or human.reviewer_email is not None):
            _reject("人工节点已经变化或提前批准")
        job_ids = [node.agent_job_id for node in nodes[:5]]
        if len(set(job_ids)) != 5 or any(value is None for value in job_ids):
            _reject("五角色实际任务不完整")
        jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).order_by(
            "workflow_node_key")[:6])
        if len(jobs) != 5 or {job.id for job in jobs} != set(job_ids):
            _reject("待复核报告的实际Agent集合不一致")
        for job in jobs:
            node = next(item for item in nodes[:5] if item.agent_job_id == job.id)
            if (job.status != "completed" or node.status != "completed"
                    or node.output_json != job.output_json
                    or job.owner_email != report.owner_email or job.scope_json != report.scope_json):
                _reject("某角色已完成输出发生变化")
        evidence = m.AiBusinessEvidenceRun.objects.filter(pk=snapshot["evidenceRunId"]).first()
        saved = m.AiBusinessScreeningRun.objects.filter(pk=snapshot["screeningIntent"]["id"]).first()
        model = m.AiModels.objects.filter(pk=flow.model_id).first()
        if (evidence is None or evidence.status != "sealed" or saved is None
                or saved.report_id != report.id or model is None
                or model.version != flow.model_version):
            _reject("封存证据、筛查根或模型版本已变化")
        value = {"schemaVersion": "business-promotion-review-state-v1",
            "reportId": report.id, "ownerEmail": report.owner_email,
            "scopeJson": report.scope_json, "snapshotDigest": digest(report.snapshot_json),
            "workflow": [flow.id, flow.version, flow.status, flow.current_node_key,
                flow.cancel_requested, flow.input_json, flow.graph_digest,
                flow.model_id, flow.model_version, flow.tool_policy_digest,
                flow.allowed_tools_json],
            "human": [human.id, human.version, human.status, human.depends_on_json,
                human.output_json, human.reviewer_email, human.reviewed_at],
            "nodes": [[node.id, node.node_key, node.version, node.status,
                digest(node.input_json), digest(node.output_json), node.agent_job_id]
                for node in nodes[:5]],
            "jobs": [[job.id, job.workflow_node_key, job.version, job.status,
                job.cancel_requested, job.provider_round_count, job.tool_call_count,
                digest(job.input_json), digest(job.output_json)] for job in jobs],
            "ledgerDigest": _ledger(job_ids),
            "evidence": [evidence.id, evidence.version, evidence.status,
                digest(evidence.plan_json), digest(evidence.state_json)],
            "screening": [saved.id, saved.report_id, saved.binding_digest,
                saved.manifest_digest, saved.content_root_digest, saved.page_count,
                saved.stored_bytes],
            "budget": None if report.budget_plan_id is None else [report.budget_plan_id,
                digest(report.budget_plan.plan_json), digest(report.budget_plan.binding_json)],
            "modelDigest": screening_creation._model_digest(model)}
        raw = canonical(value)
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            _reject("待复核元数据状态超过容量")
        current_principal(principal, admin=True, write=write)
        return raw
    except AiError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("词货待复核持久状态格式无效", "promotion_review_unverified", 409) from error


@dataclass(frozen=True, slots=True, init=False)
class PreparedReview:
    _state_json: str
    _state_digest: str
    _content_digest: str
    _dto_digest: str
    _report_id: str
    _owner_email: str
    _scope_json: str

    def __init__(self, token, state_json, dto, principal):
        if token is not _TOKEN:
            _reject("人工复核准备只能由本进程完成")
        for key, value in {"_state_json": state_json, "_state_digest": digest(state_json),
                "_content_digest": digest(dto["content"]), "_dto_digest": digest(dto),
                "_report_id": dto["binding"]["reportId"],
                "_owner_email": principal.email.lower(),
                "_scope_json": canonical(principal.scope)}.items():
            object.__setattr__(self, key, value)

    @property
    def proof(self):
        if (type(self._state_json) is not str
                or len(self._state_json.encode("utf-8")) > MAX_STATE_BYTES
                or digest(self._state_json) != self._state_digest):
            _reject("人工复核进程内证明损坏")
        return {"schemaVersion": "business-promotion-review-prepared-v1",
            "reportId": self._report_id, "contentDigest": self._content_digest,
            "dtoDigest": self._dto_digest, "humanReviewPending": True,
            "approved": False, "runtimeAdmissionGranted": False,
            "filePublicationGranted": False}


def prepare_review(report_id, principal):
    """Check full five-job content outside the caller's review transaction."""
    if connection.in_atomic_block:
        raise AiError("完整词货人工复核准备须在最外层事务之外", "invalid_request", 400)
    before = _state(report_id, principal, write=True)
    dto = content.build(report_id, principal)
    if (dto["binding"]["humanReview"] != {"status": "pending", "reviewDigest": None}
            or dto["content"]["independentReview"]["approved"] is not True
            or dto["content"]["independentReview"]["conflicts"]):
        _reject("独立复核或人工等待边界未满足")
    after = _state(report_id, principal, write=True)
    if before != after:
        _reject("完整内容核验期间报告、任务或工具账本发生变化")
    return PreparedReview(_TOKEN, after, dto, principal)


def revalidate_review(prepared, principal):
    """Short metadata and ledger CAS check inside the caller's mutation."""
    if not connection.in_atomic_block or type(prepared) is not PreparedReview:
        _reject("人工复核需要同一进程的真实准备对象和写事务")
    proof = prepared.proof
    current_principal(principal, admin=True, write=True)
    if (prepared._owner_email != principal.email.lower()
            or prepared._scope_json != canonical(principal.scope)
            or _state(prepared._report_id, principal, write=True) != prepared._state_json):
        _reject("人工复核提交前身份、内容根或实际读取账本已变化")
    return proof


# Follow-up: an approved content version must bind the actual human node's
# completed output_json={decision:'approve', comment:...}, reviewer identity,
# reviewed_at, node/flow versions and a digest of that exact row. It must also
# re-run the completed five-job content proof after approval. A pending DTO or
# this process-local token cannot be relabelled as approved for renderer 7.
