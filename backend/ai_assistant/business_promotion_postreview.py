"""Finish an already approved promotion workflow from its durable six nodes.

The expensive owning approved-content proof runs outside mutation. The short
mutation only checks fixed metadata, ledgers, version and account before the
ordinary completed workflow state/event is written. It grants no approval.
"""
import json

from django.db import connection
from django.utils import timezone

from . import business_promotion_approved_content as approved
from . import business_promotion_content_contract as content_contract
from . import business_promotion_readiness as readiness
from . import business_promotion_review as review
from . import business_promotion_runtime_contract as contract
from . import business_screening_creation as screening_creation
from . import models as m, workflows
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier, mutation, passive


def _reject(message="词货人工批准后的工作流状态已变化"):
    raise AiError(message, "promotion_postreview_unverified", 409)


def is_approved_candidate(row):
    """Route any apparent human completion to owning proof, even if corrupt."""
    if readiness.report_for(row) is None:
        return False
    human = m.AiWorkflowNodeRuns.objects.filter(run_id=row.id,
        node_key="human_review").first()
    return human is not None and (human.status == "completed"
        or human.output_json is not None or human.reviewer_email is not None)


def _state(run_id, principal):
    current_principal(principal, admin=True, write=True)
    if principal.scope is not None:
        _reject("词货收尾仅允许无范围管理员")
    row = m.AiWorkflowRuns.objects.filter(pk=identifier(run_id, "runId")).first()
    report = readiness.report_for(row) if row is not None else None
    if (row is None or report is None or row.status != "queued"
            or row.current_node_key is not None or row.cancel_requested or row.dry_run):
        _reject("工作流未处于已批准待结算的队列状态")
    authorize_owner(row, principal)
    authorize_owner(report, principal)
    snapshot = json.loads(report.snapshot_json)
    graph = contract.graph(bool(report.budget_plan_id))
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=row).order_by("position")[:7])
    if (len(nodes) != 6 or row.graph_json != canonical(graph)
            or row.graph_digest != digest(graph)
            or any((node.position, node.node_key, node.node_type, node.instruction,
                    node.depends_on_json) != (index, spec["key"], spec["type"],
                    spec["instruction"], canonical(spec["dependsOn"]))
                for index, (node, spec) in enumerate(zip(nodes, graph["nodes"])))):
        _reject("批准后固定六节点图不完整")
    human = nodes[-1]
    review_value, review_digest = approved._review(report, row, human, nodes)
    ids = [node.agent_job_id for node in nodes[:5]]
    if len(set(ids)) != 5 or any(value is None for value in ids):
        _reject("批准后的五个专业任务不完整")
    jobs = list(m.AiAgentJobs.objects.filter(workflow_run_id=row.id).order_by(
        "workflow_node_key")[:6])
    if len(jobs) != 5 or {job.id for job in jobs} != set(ids):
        _reject("批准后实际Agent集合变化")
    for job in jobs:
        node = next(item for item in nodes[:5] if item.agent_job_id == job.id)
        if (job.status != "completed" or node.status != "completed"
                or node.output_json != job.output_json
                or job.owner_email != report.owner_email or job.scope_json != report.scope_json):
            _reject("批准后专业任务结果变化")
    evidence = m.AiBusinessEvidenceRun.objects.filter(pk=snapshot["evidenceRunId"]).first()
    saved = m.AiBusinessScreeningRun.objects.filter(pk=snapshot["screeningIntent"]["id"]).first()
    model = m.AiModels.objects.filter(pk=row.model_id).first()
    if (evidence is None or evidence.status != "sealed" or saved is None
            or saved.report_id != report.id or model is None
            or model.version != row.model_version):
        _reject("批准后封存证据、筛查根或模型变化")
    value = {"schemaVersion": "business-promotion-postreview-state-v1",
        "reportId": report.id, "ownerEmail": report.owner_email,
        "snapshotDigest": digest(report.snapshot_json),
        "workflow": [row.id, row.version, row.status, row.current_node_key,
            row.cancel_requested, row.input_json, row.graph_digest,
            row.model_id, row.model_version, row.tool_policy_digest,
            row.allowed_tools_json],
        "humanReviewDigest": review_digest, "humanReview": review_value,
        "nodes": [[node.id, node.node_key, node.version, node.status,
            digest(node.input_json), digest(node.output_json), node.agent_job_id]
            for node in nodes],
        "jobs": [[job.id, job.workflow_node_key, job.version, job.status,
            job.cancel_requested, job.provider_round_count, job.tool_call_count,
            digest(job.input_json), digest(job.output_json)] for job in jobs],
        "ledgerDigest": review._ledger(ids),
        "evidence": [evidence.id, evidence.version, evidence.status,
            digest(evidence.plan_json), digest(evidence.state_json)],
        "screening": [saved.id, saved.report_id, saved.binding_digest,
            saved.manifest_digest, saved.content_root_digest, saved.page_count,
            saved.stored_bytes],
        "budget": None if report.budget_plan_id is None else [report.budget_plan_id,
            digest(report.budget_plan.plan_json), digest(report.budget_plan.binding_json)],
        "modelDigest": screening_creation._model_digest(model)}
    current_principal(principal, admin=True, write=True)
    return row, report, nodes, canonical(value)


def advance(candidate, principal):
    """Finalize only the exact already approved candidate version."""
    if connection.in_atomic_block:
        raise AiError("词货正式内容重建须在最外层事务之外", "invalid_request", 400)
    if candidate.status != "queued":
        return {"status": "not_claimed", "runId": candidate.id}
    try:
        row, report, _, before = _state(candidate.id, principal)
        if row.version != candidate.version:
            return {"status": "not_claimed", "runId": candidate.id}
        dto = approved.build(report.id, principal)
        if (dto["binding"]["reportId"] != report.id
                or dto["binding"]["humanReview"]["status"] != "approved"
                or dto["content"]["independentReview"]["approved"] is not True
                or dto["authorityVerified"] is not False
                or dto["registered"] is not False):
            _reject("词货已批准完整内容当前证明无效")
        if _state(candidate.id, principal)[3] != before:
            _reject("批准内容重建期间工作流或账本变化")
        with mutation(principal):
            current = m.AiWorkflowRuns.objects.filter(pk=candidate.id,
                version=candidate.version, status="queued", cancel_requested=0).first()
            if current is None:
                return {"status": "not_claimed", "runId": candidate.id}
            _, _, nodes, latest = _state(candidate.id, principal)
            if latest != before:
                _reject("提交前批准节点或账本已变化")
            output = passive({node.node_key: json.loads(node.output_json)
                for node in nodes}, 96 * 1024)
            current.output_json = canonical(output)
            current.status = "completed"
            current.current_node_key = None
            current.completed_at = timezone.now()
            current.updated_at = current.completed_at
            current.version += 1
            current.save()
            workflows.event(current, principal, "completed", "queued")
            return {"status": "completed", "runId": current.id,
                "approvedContentDigest": digest(dto)}
    except Exception as error:
        with mutation():
            row = m.AiWorkflowRuns.objects.filter(pk=candidate.id,
                version=candidate.version, status="queued", cancel_requested=0).first()
            if row is None:
                return {"status": "not_claimed", "runId": candidate.id}
            code = error.code if isinstance(error, AiError) else "promotion_postreview_failed"
            workflows._fail(row, principal, code)
            return {"status": "failed", "runId": row.id, "errorCode": code}
