"""Process-local promotion capacity object; runtime dispatch remains closed.

Full four-tool preparation runs outside a transaction. ``check`` is a short,
metadata-only database check suitable for the workflow mutation. An explicit
CAS resume from paused and per-microstep provider/tool ledger checks are still
required before this profile can dispatch a model.
"""
from dataclasses import dataclass
import json

from django.db import connection
from django.conf import settings

from business_analysis import screening_package
from . import business_promotion_preflight as preflight
from . import business_promotion_runtime_contract as contract
from . import business_screening_creation, business_screening_store as store
from . import models as m, workflows
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier

_TOKEN = object()


def _require(ok, message="词货容量许可的当前元数据已变化", code="conflict", status=409):
    if not ok:
        raise AiError(message, code, status)


@dataclass(frozen=True, slots=True, init=False)
class PreparedPermission:
    _capacity: object
    _report_id: str
    _owner_email: str
    _role: str
    _proof_digest: str
    _binding_digest: str

    def __init__(self, token, capacity, report_id, owner_email, role):
        _require(token is _TOKEN and type(capacity) is preflight.PreparedCapacity,
            "词货许可只能由本进程完整容量准备建立", "invalid_request", 400)
        for key, value in (("_capacity", capacity), ("_report_id", report_id),
                ("_owner_email", owner_email), ("_role", role),
                ("_proof_digest", digest(capacity.proof)),
                ("_binding_digest", digest([report_id, owner_email, role, digest(capacity.proof)]))):
            object.__setattr__(self, key, value)

    @property
    def proof(self):
        return _proof(self)


def _proof(prepared):
    _require(type(prepared) is PreparedPermission
        and type(prepared._capacity) is preflight.PreparedCapacity,
        "公开对象不能恢复词货容量许可", "invalid_request", 400)
    proof = prepared._capacity.proof
    _require(digest(proof) == prepared._proof_digest
        and digest([prepared._report_id, prepared._owner_email, prepared._role,
            prepared._proof_digest]) == prepared._binding_digest
        and proof["reportId"] == prepared._report_id
        and proof["ownerEmail"] == prepared._owner_email
        and prepared._role in screening_package.ROLES
        and proof["capacityVerified"] is True
        and proof["requiredContentOnly"] is True
        and proof["runtimeAdmissionGranted"] is False,
        "词货容量对象完整性无效")
    return proof


def refresh(prepared, principal):
    """Network/catalog and package recheck outside the workflow mutation."""
    _proof(prepared)
    _require(not connection.in_atomic_block,
        "词货容量完整复验须在最外层事务之外", "invalid_request", 400)
    proof = preflight.revalidate(prepared._capacity, principal)
    _require(digest(proof) == prepared._proof_digest)
    check(prepared, principal)
    return prepared.proof


def check(prepared, principal, *, allow_parent_progress=False):
    """Only metadata SQL. This cannot authorize the initial provider call."""
    proof = _proof(prepared)
    current_principal(principal, admin=True)
    _require(principal.scope is None and principal.email.lower() == prepared._owner_email,
        "词货容量账号或范围已变化", "access_denied", 403)
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        pk=identifier(prepared._report_id, "reportId")).first()
    _require(report is not None, "词货容量报告不存在")
    authorize_owner(report, principal)
    flow = report.workflow
    authorize_owner(flow, principal)
    parked = (proof["workflowStatus"] == "paused"
        and flow.status == "paused" and flow.retryable == 0
        and flow.error_code == proof["workflowErrorCode"]
        and not flow.lease_token and flow.lease_expires_at is None
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
    _require(type(allow_parent_progress) is bool
        and (parked if proof["workflowStatus"] == "paused"
        else flow.status in {"queued", "running"})
        and (allow_parent_progress or flow.version == proof["workflowVersion"])
        and flow.error_code == proof["workflowErrorCode"]
        and not flow.cancel_requested and flow.dry_run == 0,
        "词货工作流未处于可检查状态；暂停后须独立 CAS 恢复")
    _require(flow.id == proof["workflowId"]
        and report.owner_email == flow.owner_email and report.scope_json == flow.scope_json
        and report.scope_json == canonical(principal.scope)
        and digest(report.snapshot_json) == proof["snapshotDigest"]
        and digest(flow.input_json) == proof["workflowInputDigest"]
        and flow.graph_digest == proof["graphDigest"]
        and flow.allowed_tools_json == canonical(list(contract.TOOL_ORDER))
        and flow.tool_policy_digest == proof["toolCatalogDigest"]
        and flow.model_id == proof["modelId"]
        and flow.model_version == proof["modelVersion"],
        "词货报告或四工具工作流固定字段已变化")
    try:
        snapshot, reference = json.loads(report.snapshot_json), json.loads(flow.input_json)
        _require(snapshot["executionProfile"] == contract.PROFILE
            and snapshot["reportId"] == report.id
            and reference["reportId"] == report.id
            and reference["screeningIntent"] == snapshot["screeningIntent"]
            and reference["promotionRef"]["promotionSelector"] == snapshot["promotionSelector"]
            and flow.graph_json == canonical(contract.graph("budgetRef" in snapshot)))
        screening_id = snapshot["screeningIntent"]["id"]
        row = m.AiBusinessScreeningRun.objects.filter(pk=screening_id).first()
        _require(row is not None and row.report_id == report.id
            and canonical(store._reference(row)) == canonical(proof["screeningReference"]),
            "词货筛查发布根已变化")
        authorize_owner(row, principal)
        evidence = m.AiBusinessEvidenceRun.objects.filter(pk=snapshot["evidenceRunId"]).first()
        _require(evidence is not None and evidence.status == "sealed"
            and evidence.version == snapshot["evidenceVersion"]
            and digest(evidence.plan_json) == snapshot["evidencePlanDigest"]
            and json.loads(evidence.state_json)["sealedDigest"] == snapshot["sealedDigest"]
            and json.loads(evidence.plan_json)["catalogDigest"] == snapshot["catalogDigest"],
            "词货封存证据元数据已变化")
        authorize_owner(evidence, principal)
        if "budgetRef" in snapshot:
            saved = report.budget_plan
            _require(saved is not None and saved.owner_email == report.owner_email
                and saved.scope_json == report.scope_json
                and saved.evidence_id == evidence.id
                and saved.evidence_version == evidence.version
                and saved.plan_digest == json.loads(saved.binding_json)["planDigest"]
                and saved.plan_digest == digest(json.loads(saved.plan_json))
                and saved.binding_digest == digest(saved.binding_json)
                and saved.id == snapshot["budgetRef"]["id"]
                and reference["budgetRef"] == snapshot["budgetRef"],
                "词货预算参数元数据已变化")
            authorize_owner(saved, principal)
        else:
            _require(report.budget_plan_id is None and "budgetRef" not in reference,
                "词货预算引用有无已变化")
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("词货许可固定元数据结构无效", "conflict", 409) from error
    model = workflows.resolve_model(flow.model_id)
    _require(model.id == flow.model_id and model.version == flow.model_version
        and business_screening_creation._model_digest(model) == proof["modelConfigurationDigest"]
        and digest(workflows.execution_guidance(flow.id)) == proof["guidanceDigest"],
        "词货模型或指引已变化")
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow,
        node_key=prepared._role).values("node_type", "instruction", "depends_on_json"))
    expected = next(node for node in contract.graph("budgetRef" in snapshot)["nodes"]
        if node["key"] == prepared._role)
    _require(len(nodes) == 1 and nodes[0] == {"node_type": "agent",
        "instruction": expected["instruction"],
        "depends_on_json": canonical(expected["dependsOn"])},
        "词货容量角色任务已变化")
    return {**proof, "role": prepared._role, "runtimeAdmissionGranted": False,
        "initialProviderCallAllowed": False,
        "reason": "requires_explicit_resume_and_per_microstep_dispatch_ledger"}


def prepare(report_id, principal, role, *, allow_parked=False):
    """Create an in-process capacity object; no jobs or provider calls."""
    _require(not connection.in_atomic_block,
        "词货容量准备须在最外层事务之外", "invalid_request", 400)
    _require(type(role) is str and role in screening_package.ROLES,
        "词货容量角色无效", "invalid_request", 400)
    _require(type(allow_parked) is bool, "停靠许可模式无效", "invalid_request", 400)
    capacity = preflight.prepare(report_id, principal, allow_parked=allow_parked)
    proof = capacity.proof
    prepared = PreparedPermission(_TOKEN, capacity, proof["reportId"],
        proof["ownerEmail"], role)
    refresh(prepared, principal)
    return prepared


def authorize_dispatch(prepared, job, principal, *, result_commit=False):
    """Process-local permission for an already resumed actual Agent only.

    The release flag defaults closed. A parked workflow, a fabricated job or a
    serialized capacity proof cannot turn the read-only preflight into grant.
    This metadata check is suitable immediately before a short reservation.
    """
    _require(getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is True,
        "词货模型运行尚未启用", "promotion_runtime_not_ready")
    _require(type(result_commit) is bool, "结果提交模式无效")
    proof = check(prepared, principal, allow_parent_progress=result_commit)
    from . import business_promotion_tools as tools
    report, role, _ = tools._job(job.id, principal)
    _require(report.id == proof["reportId"] and role == prepared._role
        and proof["workflowStatus"] in {"queued", "running"}
        and report.workflow.status in {"queued", "running"}
        and report.workflow.error_code == proof["workflowErrorCode"],
        "词货容量许可不属于当前已恢复的实际Agent")
    return {**proof, "role": role, "runtimeAdmissionGranted": True,
        "initialProviderCallAllowed": True,
        "reason": "current_process_capacity_and_running_agent_verified",
        "parentVersionAdvancedAfterReservation":result_commit
            and report.workflow.version != proof["workflowVersion"]}
