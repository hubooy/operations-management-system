"""Owning five-Agent completed content for the new promotion report profile.

Each role is reconstructed from its own completed job and sealed tool ledger.
The detached DTO is not a credential, human approval or file publication.
"""
from copy import deepcopy
import json
import math

from django.db import connection

from business_analysis import screening_package
from . import business_promotion_budget as promotion_budget
from . import business_promotion_completed_receipts as completed
from . import business_promotion_content_contract as contract
from . import business_promotion_diagnosis as diagnosis
from . import business_promotion_read_receipts as fourth
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as promotion_contract
from . import business_promotion_tools as format_reader
from . import business_screening_claims as candidate_claims
from . import business_screening_packages as packages
from . import business_screening_store as store
from . import business_evidence as evidence_service
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


def _reject(message="词货报告五角色完成内容未通过核验"):
    raise AiError(message, "promotion_content_incomplete", 409)


def _state(report_id, principal):
    """Small current root and ledger fence, independent of fact-page reads."""
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("词货完整内容仅允许无范围管理员")
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        pk=identifier(report_id, "reportId")).first()
    if report is None:
        _reject("词货报告不存在")
    authorize_owner(report, principal)
    flow = report.workflow
    authorize_owner(flow, principal)
    fixed = runtime.bound_persisted(report.id, principal)
    snapshot = json.loads(report.snapshot_json)
    graph = promotion_contract.graph(bool(report.budget_plan_id))
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
    if (fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready"
            or flow.status != "waiting_review" or flow.current_node_key != "human_review"
            or flow.cancel_requested or flow.dry_run or len(nodes) != len(graph["nodes"])
            or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
            or any((node.position, node.node_key, node.node_type, node.instruction,
                    node.depends_on_json) != (index, part["key"], part["type"],
                    part["instruction"], canonical(part["dependsOn"]))
                for index, (node, part) in enumerate(zip(nodes, graph["nodes"])))):
        _reject("五角色图、人审等待状态或固定报告已变化")
    by_key = {node.node_key: node for node in nodes}
    human = by_key["human_review"]
    if (human.status != "waiting_review" or human.agent_job_id is not None
            or human.output_json is not None or human.depends_on_json != canonical(["report"])):
        _reject("人工节点尚不处于未经批准的等待状态")
    role_nodes = {role: by_key[role] for role in contract.ROLES}
    ids = [node.agent_job_id for node in role_nodes.values()]
    if len(set(ids)) != len(contract.ROLES) or any(job_id is None for job_id in ids):
        _reject("五个专业角色没有独立实际Agent")
    jobs = {job.id: job for job in m.AiAgentJobs.objects.filter(workflow_run_id=flow.id)}
    if set(jobs) != set(ids) or len(jobs) != len(contract.ROLES):
        _reject("五角色实际任务集合不完整或含额外任务")
    by_role = {}
    for role, node in role_nodes.items():
        job = jobs[node.agent_job_id]
        if (job.workflow_node_key != role or job.status != "completed"
                or node.status != "completed" or job.output_json != node.output_json
                or job.owner_email != report.owner_email or job.scope_json != report.scope_json):
            _reject("专业节点与实际完成结果不一致")
        by_role[role] = job
    ledger = [[role, by_role[role].id, fourth._ledger_fence(by_role[role].id)]
        for role in contract.ROLES]
    state = {"reportId": report.id, "snapshotJson": report.snapshot_json,
        "workflowId": flow.id, "workflowVersion": flow.version,
        "workflowStatus": flow.status, "workflowInputJson": flow.input_json,
        "workflowGraphJson": flow.graph_json, "workflowPolicyDigest": flow.tool_policy_digest,
        "human": [human.id, human.version, human.status, human.output_json],
        "nodes": [[role, role_nodes[role].id, role_nodes[role].version,
            role_nodes[role].status, role_nodes[role].input_json,
            role_nodes[role].output_json, role_nodes[role].agent_job_id] for role in contract.ROLES],
        "jobs": [[role, by_role[role].id, by_role[role].version,
            by_role[role].status, by_role[role].input_json,
            by_role[role].output_json, by_role[role].provider_round_count,
            by_role[role].tool_call_count] for role in contract.ROLES],
        "ledger": ledger, "fixed": fixed}
    return report, snapshot, by_role, state


def _native_or_mapped(job, reference, proof, report, snapshot, principal):
    mode = "mapped" if "pairKey" in reference else "native"
    selector = {"mode": mode, **{key: reference[key] for key in
        ("sourceKey", "baselineKey", "pairKey", "baselinePairKey", "dimension")
        if key in reference}}
    saved = proof["analysisSelectors"].get(canonical(selector))
    if saved is None or not any(item["rowIndex"] == reference["rowIndex"]
            and item["rowId"] == reference["rowId"] for item in saved["seenRows"]):
        _reject("本人完成态分析工具没有读取所引用的原生或关联行")
    args = {"runId": snapshot["evidenceRunId"], "reportId": report.id,
        "screeningId": snapshot["screeningIntent"]["id"],
        "offset": reference["rowIndex"], **selector}
    evidence = evidence_service.get_run(snapshot["evidenceRunId"], principal)
    page = format_reader._analysis(args, report, snapshot,
        json.loads(report.workflow.input_json), evidence, principal)
    table = page["table"]
    rows = table["rows"]
    if (page["mode"] != mode or page["selector"] != {key: value for key, value in selector.items()
            if key != "mode"} or not rows or rows[0]["rowIndex"] != reference["rowIndex"]
            or rows[0]["id"] != reference["rowId"]):
        _reject("引用行与当前封存分析表不一致")
    row, metric, field = rows[0], reference["metric"], reference["field"]
    if field == "value":
        entry = row["metrics"].get(metric) or {}
        number, partial = entry.get("value"), bool(entry.get("missingRows"))
    elif field == "ratio":
        number, partial = row["ratios"].get(metric), False
    else:
        number = row["comparisons"].get(metric, {}).get(field)
        baseline = (row.get("baselineMetrics") or {}).get(metric) or {}
        partial = field == "baseline" and bool(baseline.get("missingRows"))
    if type(number) not in (int, float) or type(number) is float and not math.isfinite(number):
        _reject("引用行没有可用的有限数值")
    return {"reference": deepcopy(reference), "value": number, "partial": partial,
        "entity": deepcopy(row["entity"]), "reportId": report.id, "jobId": job.id,
        "verification": {"numericReferenceVerified": True,
            "referenceReadVerified": True, "completedAgentReadVerified": True,
            "causalityVerified": False, "humanReviewRequired": True},
        "limitations": deepcopy(table.get("limitations", []))}


def _analysis(job, role, parsed, proof, report, snapshot, candidate_packages, principal):
    diagnosis_value = parsed["diagnosis"] if role == "report" else parsed
    findings, verified_count = [], 0
    candidate_proof = None
    if any("candidateId" in ref for finding in diagnosis_value["findings"]
            for ref in finding["references"]):
        candidate_proof = candidate_claims.prepare(candidate_packages, role, principal)
        row, _ = candidate_claims._checked(candidate_proof, principal)
        if row.report_id != report.id or row.id != snapshot["screeningIntent"]["id"]:
            _reject("候选引用跨报告或筛查结果")
    for finding in diagnosis_value["findings"]:
        value = {key: deepcopy(item) for key, item in finding.items() if key != "references"}
        facts = []
        for reference in finding["references"]:
            if reference.get("kind") == "promotion_keyword_sku":
                fact = completed.resolve_promotion_reference(job, reference, principal)
                if finding["kind"] == "action" and not fact["actionableKeywordSku"]:
                    _reject("缺推广SKU身份的词货分组不能作为具体动作对象")
            elif "candidateId" in reference:
                fact = candidate_claims.resolve(candidate_proof, reference, principal)
                fact = deepcopy(fact)
                fact["verification"]["completedAgentReadVerified"] = True
            else:
                fact = _native_or_mapped(job, reference, proof, report, snapshot, principal)
            facts.append(fact)
            verified_count += 1
        value["facts"] = facts
        findings.append(value)
    return {"schemaVersion": "business-promotion-completed-analysis-v1",
        "reportId": report.id, "jobId": job.id, "role": role,
        "summary": diagnosis_value["summary"], "findings": findings,
        "verifiedReferenceCount": verified_count, "numericReferencesVerified": True,
        "completedAgentReadVerified": True, "humanReviewRequired": True,
        "causalityVerified": False}


def _contract_proof(proof):
    """Keep optional selector keys as bounded values in the detached DTO."""
    value = deepcopy(proof)
    selectors = value.pop("analysisSelectors")
    value["analysisSelectorPages"] = [
        {"selector": json.loads(key), "state": state}
        for key, state in sorted(selectors.items())]
    value["sourceReadProofDigest"] = digest(proof)
    return value


def build(report_id, principal):
    """Return a detached, bounded DTO after five independent owning proofs."""
    if connection.in_atomic_block:
        raise AiError("完整五角色内容须在最外层事务之外准备", "invalid_request", 400)
    report, snapshot, jobs, before = _state(report_id, principal)
    proofs, parsed = {}, {}
    for role in contract.ROLES:
        job = jobs[role]
        proof = completed.progress(job, principal)
        if (proof["jobId"] != job.id or proof["reportId"] != report.id
                or proof["role"] != role or proof["completedAgentReadVerified"] is not True):
            _reject("某角色的完成态回执证明无效")
        proofs[role] = proof
        parsed[role] = diagnosis.validate_answer(role, json.loads(job.output_json)["answer"])
    review = parsed["independent_review"]
    if review["approved"] is not True or review["conflicts"]:
        _reject("独立复核仍存在未解决冲突，不能形成完整诊断草稿")
    ready = packages.prepare(snapshot["screeningIntent"]["id"], principal)
    report_package = next(package for role, package, _ in ready._packages if role == "report")
    decoded = packages._call(screening_package.decode_pages, report_package.pages())
    if decoded["binding"]["reportId"] != report.id:
        _reject("筛查角色包不属于当前报告")
    analyses = {role: _analysis(jobs[role], role, parsed[role], proofs[role], report,
        snapshot, ready, principal) for role in (*contract.SPECIALISTS, "report")}
    detached_proofs = {role: _contract_proof(proofs[role]) for role in contract.ROLES}
    content = {"sections": deepcopy(parsed["report"]["sections"]),
        "diagnosis": analyses["report"], "professionalAnalyses":
            {role: analyses[role] for role in contract.SPECIALISTS},
        "independentReview": deepcopy(review),
        "screening": {"schemaVersion": contract.SCREENING_SCHEMA,
            "coverage": decoded["coverage"], "readProofs": detached_proofs,
            "limitations": list(contract.LIMITATIONS),
            "candidateDisclosure": {"fullCandidatesIncluded": False,
                "crossPartitionAmountsAdditive": False}}}
    if report.budget_plan_id is not None:
        budget = promotion_budget._roots(report.id, principal)["prepared"].result
        if budget["planDigest"] != snapshot["budgetRef"]["planDigest"]:
            _reject("预算重算与报告固定摘要不一致")
        content["budget"] = budget
    saved, _, _ = store._loaded(snapshot["screeningIntent"]["id"], principal)
    if saved.report_id != report.id:
        _reject("筛查持久根不属于当前报告")
    binding = {"schemaVersion": contract.BINDING_SCHEMA,
        "executionProfile": contract.PROFILE, "reportId": report.id,
        "workflowId": report.workflow_id, "evidenceRunId": snapshot["evidenceRunId"],
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
            "readProofDigest": digest(detached_proofs[role])} for role in contract.ROLES},
        "humanReview": {"status": "pending", "reviewDigest": None}}
    if report.budget_plan_id is not None:
        binding["budgetPlanDigest"] = snapshot["budgetRef"]["planDigest"]
    try:
        prepared = contract.prepare(binding, content)
        value = prepared.value
    except contract.ContentContractError as error:
        raise AiError("完成内容未通过有界纯协议", "promotion_content_incomplete", 409) from error
    if canonical(_state(report.id, principal)[3]) != canonical(before):
        _reject("完成内容计算期间报告、角色、账本或账号状态变化")
    current_principal(principal, admin=True)
    return value
