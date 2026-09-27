"""Owning approved content version for the promotion report profile.

The actual human decision, reviewer and durable review event are required.
Five completed Agent ledgers and every numeric reference are recomputed again;
the pending draft is never relabelled. No file publication occurs here.
"""
from copy import deepcopy
import json

from django.db import connection

from business_analysis import screening_package
from . import business_promotion_budget as budget_reader
from . import business_promotion_completed_receipts as completed
from . import business_promotion_content as pending
from . import business_promotion_content_contract as content_contract
from . import business_promotion_diagnosis as diagnosis
from . import business_promotion_read_receipts as fourth
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as promotion_contract
from . import business_screening_packages as packages
from . import business_screening_store as store
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


REVIEW_SCHEMA = "business-promotion-approved-review-v1"


def _reject(message="词货正式批准内容未通过实际人审与五角色核验"):
    raise AiError(message, "promotion_approved_content_unverified", 409)


def _review(report, flow, human, nodes):
    try:
        decision = json.loads(human.output_json)
        if (type(decision) is not dict or set(decision) != {"decision", "comment"}
                or decision["decision"] != "approve"
                or type(decision["comment"]) is not str
                or len(decision["comment"]) > 2000
                or human.output_json != canonical(decision)
                or human.status != "completed" or human.agent_job_id is not None
                or human.reviewer_email is None or human.reviewer_email.lower() != report.owner_email
                or human.reviewed_at is None or human.completed_at is None
                or human.reviewed_at > human.completed_at
                or human.depends_on_json != canonical(["report"])):
            _reject("人工节点缺少明确批准、本人身份或完成时间")
        events = list(m.AiWorkflowEvents.objects.filter(run=flow,
            node_key="human_review", event_type="review_approved").order_by("created_at")[:2])
        if (len(events) != 1 or events[0].actor_email.lower() != human.reviewer_email.lower()
                or events[0].owner_email != report.owner_email
                or events[0].from_status != "waiting_review"
                or events[0].to_status != "queued"
                or events[0].run_version < 1 or events[0].run_version > flow.version
                or events[0].created_at < human.completed_at):
            _reject("人工批准缺少同一工作流的实际审核事件")
        if flow.status == "queued":
            if flow.output_json is not None or flow.completed_at is not None:
                _reject("批准后待结算的工作流结果提前出现")
        elif flow.status == "completed":
            expected = canonical({node.node_key: json.loads(node.output_json)
                for node in nodes})
            if (flow.output_json != expected or flow.completed_at is None
                    or flow.completed_at < human.completed_at):
                _reject("正式完成的工作流输出与六节点不一致")
        else:
            _reject("词货人审批准后工作流状态无效")
        value = {"schemaVersion": REVIEW_SCHEMA, "nodeId": human.id,
            "nodeVersion": human.version, "outputJson": human.output_json,
            "reviewerEmail": human.reviewer_email.lower(),
            "reviewedAt": human.reviewed_at.isoformat(),
            "completedAt": human.completed_at.isoformat(),
            "eventId": events[0].id, "eventRunVersion": events[0].run_version,
            "eventActorEmail": events[0].actor_email.lower(),
            "eventCreatedAt": events[0].created_at.isoformat()}
        return value, digest(value)
    except AiError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("实际人工批准结果无效", "promotion_approved_content_unverified", 409) from error


def _state(report_id, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("词货正式内容仅允许无范围管理员")
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        pk=identifier(report_id, "reportId")).first()
    if report is None:
        _reject("词货正式内容报告不存在")
    authorize_owner(report, principal)
    flow = report.workflow
    authorize_owner(flow, principal)
    fixed = runtime.bound_persisted(report.id, principal)
    snapshot = json.loads(report.snapshot_json)
    graph = promotion_contract.graph(bool(report.budget_plan_id))
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
    if (fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready"
            or flow.status not in {"queued", "completed"}
            or flow.current_node_key is not None or flow.cancel_requested or flow.dry_run
            or len(nodes) != len(graph["nodes"])
            or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
            or any((node.position, node.node_key, node.node_type, node.instruction,
                    node.depends_on_json) != (index, part["key"], part["type"],
                    part["instruction"], canonical(part["dependsOn"]))
                for index, (node, part) in enumerate(zip(nodes, graph["nodes"])))):
        _reject("正式内容工作流图、状态或封存报告已变化")
    human = nodes[-1]
    review, review_digest = _review(report, flow, human, nodes)
    role_nodes = {role: nodes[index] for index, role in enumerate(content_contract.ROLES)}
    ids = [node.agent_job_id for node in role_nodes.values()]
    if len(set(ids)) != 5 or any(job_id is None for job_id in ids):
        _reject("正式内容五个角色缺少独立实际任务")
    jobs = {job.id: job for job in m.AiAgentJobs.objects.filter(workflow_run_id=flow.id)}
    if set(jobs) != set(ids) or len(jobs) != 5:
        _reject("正式内容实际任务集合不完整或含额外任务")
    by_role = {}
    for role, node in role_nodes.items():
        job = jobs[node.agent_job_id]
        if (job.workflow_node_key != role or job.status != "completed"
                or node.status != "completed" or job.output_json != node.output_json
                or job.owner_email != report.owner_email or job.scope_json != report.scope_json):
            _reject("正式内容专业节点与实际任务输出不同")
        by_role[role] = job
    ledger = [[role, by_role[role].id, fourth._ledger_fence(by_role[role].id)]
        for role in content_contract.ROLES]
    state = {"reportId": report.id, "snapshotJson": report.snapshot_json,
        "workflowId": flow.id, "workflowVersion": flow.version,
        "workflowStatus": flow.status, "workflowInputJson": flow.input_json,
        "workflowOutputJson": flow.output_json,
        "workflowGraphJson": flow.graph_json,
        "workflowPolicyDigest": flow.tool_policy_digest,
        "humanReview": review, "humanReviewDigest": review_digest,
        "nodes": [[role, role_nodes[role].id, role_nodes[role].version,
            role_nodes[role].status, role_nodes[role].input_json,
            role_nodes[role].output_json, role_nodes[role].agent_job_id]
            for role in content_contract.ROLES],
        "jobs": [[role, by_role[role].id, by_role[role].version,
            by_role[role].status, by_role[role].input_json,
            by_role[role].output_json, by_role[role].provider_round_count,
            by_role[role].tool_call_count] for role in content_contract.ROLES],
        "ledger": ledger, "fixed": fixed}
    return report, snapshot, by_role, state


def build(report_id, principal):
    """Recompute five completed roles after the actual approved human row."""
    if connection.in_atomic_block:
        raise AiError("正式批准内容须在最外层事务之外重建", "invalid_request", 400)
    report, snapshot, jobs, before = _state(report_id, principal)
    proofs, parsed = {}, {}
    for role in content_contract.ROLES:
        job = jobs[role]
        proof = completed.progress(job, principal)
        if (proof["jobId"] != job.id or proof["reportId"] != report.id
                or proof["role"] != role or proof["completedAgentReadVerified"] is not True):
            _reject("正式内容某角色完成态证明已变化")
        proofs[role] = proof
        parsed[role] = diagnosis.validate_answer(role,
            json.loads(job.output_json)["answer"])
    review = parsed["independent_review"]
    if review["approved"] is not True or review["conflicts"]:
        _reject("独立专业复核仍未批准或有冲突")
    ready = packages.prepare(snapshot["screeningIntent"]["id"], principal)
    report_package = next(value for role, value, _ in ready._packages if role == "report")
    decoded = packages._call(screening_package.decode_pages, report_package.pages())
    if decoded["binding"]["reportId"] != report.id:
        _reject("正式内容筛查包来自另一报告")
    analyses = {role: pending._analysis(jobs[role], role, parsed[role], proofs[role],
        report, snapshot, ready, principal)
        for role in (*content_contract.SPECIALISTS, "report")}
    detached = {role: pending._contract_proof(proofs[role])
        for role in content_contract.ROLES}
    content = {"sections": deepcopy(parsed["report"]["sections"]),
        "diagnosis": analyses["report"],
        "professionalAnalyses": {role: analyses[role]
            for role in content_contract.SPECIALISTS},
        "independentReview": deepcopy(review),
        "screening": {"schemaVersion": content_contract.SCREENING_SCHEMA,
            "coverage": decoded["coverage"], "readProofs": detached,
            "limitations": list(content_contract.LIMITATIONS),
            "candidateDisclosure": {"fullCandidatesIncluded": False,
                "crossPartitionAmountsAdditive": False}}}
    if report.budget_plan_id is not None:
        budget = budget_reader._roots(report.id, principal)["prepared"].result
        if budget["planDigest"] != snapshot["budgetRef"]["planDigest"]:
            _reject("正式内容预算与固定报告摘要不一致")
        content["budget"] = budget
    saved, _, _ = store._loaded(snapshot["screeningIntent"]["id"], principal)
    if saved.report_id != report.id:
        _reject("正式内容筛查持久根来自另一报告")
    binding = {"schemaVersion": content_contract.BINDING_SCHEMA,
        "executionProfile": content_contract.PROFILE,
        "reportId": report.id, "workflowId": report.workflow_id,
        "evidenceRunId": snapshot["evidenceRunId"],
        "evidenceVersion": snapshot["evidenceVersion"],
        "sealedDigest": snapshot["sealedDigest"],
        "snapshotDigest": digest(report.snapshot_json),
        "workflowInputDigest": digest(report.workflow.input_json),
        "ledgerDigest": digest(before["ledger"]),
        "screeningId": saved.id,
        "screeningRootDigest": digest([store._reference(saved),
            saved.manifest_digest, saved.content_root_digest]),
        "ownerEmail": report.owner_email, "scope": principal.scope,
        "promotionSelector": snapshot["promotionSelector"],
        "contextDigest": snapshot["contextDigest"],
        "promotionCatalogDigest": snapshot["promotionCatalogDigest"],
        "promotionAlgorithmVersion": snapshot["promotionAlgorithmVersion"],
        "jobs": {role: {"jobId": jobs[role].id,
            "outputDigest": digest(jobs[role].output_json),
            "readProofDigest": digest(detached[role])}
            for role in content_contract.ROLES},
        "humanReview": {"status": "approved",
            "reviewDigest": before["humanReviewDigest"]}}
    if report.budget_plan_id is not None:
        binding["budgetPlanDigest"] = snapshot["budgetRef"]["planDigest"]
    try:
        value = content_contract.prepare(binding, content).value
    except content_contract.ContentContractError as error:
        raise AiError("正式内容未通过有界纯协议", "promotion_approved_content_unverified", 409) from error
    if canonical(_state(report.id, principal)[3]) != canonical(before):
        _reject("正式内容重建期间人审、五角色或封存账本发生变化")
    current_principal(principal, admin=True)
    return value
