"""Internal atomic writer for the proposed promotion screening report.

This module has no public route or scheduler registration. Until the new
database guard is installed, its report insert fails and mutation rolls back.
"""
import json

from django.db import connection

from business_analysis.contracts import AnalysisContractError
from . import business_evidence as evidence_service, business_evidence_store as evidence_store
from . import business_promotion_creation_contract as contract
from . import business_promotion_runtime_contract as promotion
from . import business_screening_creation as screening_creation
from . import business_budget_store as budgets, models as m, report_library, transport, workflows
from .policy import (AiError, authorize_owner, canonical, current_principal, digest,
    fields, identifier, mutation, passive, uid)

TITLE = "关键词商品深度经营分析"
FIELDS = frozenset(("clientRequestId", "evidenceRunId", "question", "sourceKey", "baselineKey",
    "mappingPairs", "budgetPlan", "expectedPrincipalKey"))
REQUIRED = frozenset(("clientRequestId", "evidenceRunId", "question", "sourceKey"))


def _require(condition, message="词货报告固定协议已变化", code="conflict", status=409):
    if not condition:
        raise AiError(message, code, status)


def _body(value, principal):
    fields(value, FIELDS, REQUIRED)
    value = json.loads(canonical(passive(value, 128 * 1024)))
    if "expectedPrincipalKey" in value:
        _require(value["expectedPrincipalKey"] == evidence_service.principal_key(principal),
            "当前账号与确认的请求不一致", "access_denied", 403)
    identifier(value["clientRequestId"], "clientRequestId")
    identifier(value["evidenceRunId"], "evidenceRunId")
    # The preparation contract owns all selector, question and optional-input
    # shape checks. IDs are generated here, never accepted from a caller.
    return value


def _request(body, report_id, screening_id):
    return contract._request({"reportId":report_id, "screeningId":screening_id,
        **{key:body[key] for key in contract.REQUEST_REQUIRED | contract.REQUEST_OPTIONAL
           if key in body and key not in {"reportId", "screeningId"}}})


def _catalog(principal):
    return contract._catalog(transport.catalog(principal, promotion.SURFACE))


def _replay(body, principal):
    """Rebuild an existing result from current sealed roots before replay."""
    row = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        owner_email=principal.email.lower(), client_request_id=body["clientRequestId"]).first()
    if row is None:
        return None
    authorize_owner(row, principal)
    authorize_owner(row.workflow, principal)
    _require(row.request_digest == digest(body), "请求标识已绑定其他报告")
    try:
        snapshot = json.loads(row.snapshot_json)
        _require(row.snapshot_json == canonical(snapshot) and snapshot["executionProfile"] == promotion.PROFILE
            and snapshot["reportId"] == row.id
            and row.workflow.owner_email == row.owner_email and row.workflow.scope_json == row.scope_json)
        request = _request(body, row.id, snapshot["screeningIntent"]["id"])
        evidence = evidence_service.get_run(body["evidenceRunId"], principal)
        _require(evidence.status == "sealed" and evidence_store.is_v2(evidence))
        budget = None
        if "budgetPlan" in request:
            _require(row.budget_plan_id is not None)
            budget = budgets._prepare(evidence, request["budgetPlan"], principal, row.id, row.budget_plan_id)
            _require((budget.plan_json, budget.binding_json) ==
                (row.budget_plan.plan_json, row.budget_plan.binding_json))
        else:
            _require(row.budget_plan_id is None)
        entries = _catalog(principal)
        _, expected = contract._build(evidence, principal, request, budget, entries)
        graph = workflows.validate_graph(expected["graph"])
        flow = row.workflow
        _require(row.snapshot_json == canonical(expected["snapshot"])
            and flow.input_json == canonical(expected["workflowInput"])
            and flow.graph_json == canonical(graph) and flow.graph_digest == digest(graph)
            and flow.allowed_tools_json == canonical(expected["allowedTools"])
            and flow.tool_policy_digest == expected["toolCatalogDigest"]
            and flow.dry_run == 0 and flow.client_request_id == "business-"+digest([principal.email.lower(), body["clientRequestId"]]))
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position"))
        _require(len(nodes) == len(graph["nodes"]) and all(
            (node.position, node.node_key, node.node_type, node.depends_on_json, node.instruction) ==
            (index, spec["key"], spec["type"], canonical(spec["dependsOn"]), spec["instruction"])
            for index, (node, spec) in enumerate(zip(nodes, graph["nodes"]))))
        evidence_store.assert_current(evidence)
        current_principal(principal, admin=True)
    except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("既有词货报告未通过当前固定协议核验", "conflict", 409) from error
    return {"item":{"id":row.id, "workflowId":flow.id}, "replayed":True}


def _revalidate_metadata(prepared, principal):
    """Short transaction check; no model call, remote catalog or fact scan."""
    _require(connection.in_atomic_block)
    shape = prepared.shape
    request = json.loads(prepared._request_json)
    evidence = evidence_service.get_run(prepared._evidence_id, principal)
    capsule = (screening_creation._BudgetCapsule(screening_creation._TOKEN, prepared._base.budget)
        if prepared._base.budget is not None else None)
    fresh_base = screening_creation._metadata_revalidate(prepared._base, capsule, principal)
    sources = evidence_store.catalog(evidence)
    context = {"reportId":request["reportId"], "runId":evidence.id,
        "screeningId":request["screeningId"], "sealedDigest":fresh_base.snapshot["sealedDigest"]}
    selector = {key:request[key] for key in ("sourceKey", "baselineKey") if key in request}
    try:
        fragment = promotion.freeze_snapshot(sources, context, selector)
    except AnalysisContractError as error:
        raise AiError("词货来源与封存根已变化", "conflict", 409) from error
    entries = json.loads(prepared._catalog_json)
    actual = contract._shape(fresh_base, fragment, principal, entries)
    _require(canonical(actual) == canonical(shape) and shape["authorityVerified"] is False
        and shape["registered"] is False)
    evidence_store.assert_current(evidence)
    return shape, capsule


def create(body, principal, *, commit=None):
    """Create a queued immutable report, or fail atomically under old guards."""
    _require(not connection.in_atomic_block, "词货报告准备须在最外层事务之外", "invalid_request", 400)
    current_principal(principal, admin=True, write=True)
    body = _body(body, principal)
    old = _replay(body, principal)
    if old is not None:
        if commit is None:
            return old
        with mutation(principal):
            return commit(old, 200)
    screening_creation._limits(principal)
    model = workflows.resolve_model()
    model_digest = screening_creation._model_digest(model)
    report_id, screening_id = uid("ai-report"), uid("screening")
    prepared = contract.prepare_candidate(body["evidenceRunId"], _request(body, report_id, screening_id), principal)
    contract.revalidate_candidate(prepared, principal)
    graph = workflows.validate_graph(prepared.shape["graph"])
    _require(canonical(graph) == canonical(prepared.shape["graph"]))
    # A second live catalog read is outside the global mutation lock. Runtime
    # admission must recheck the digest again before any eventual dispatch.
    _require(canonical(_catalog(principal)) == prepared._catalog_json, "词货工具目录在准备期间已变化", "tool_policy_changed")
    entries = json.loads(prepared._catalog_json)
    allowed = prepared.shape["allowedTools"]
    admitted = {"model_id":model.id, "model_version":model.version,
        "allowed_tools_json":canonical(allowed), "tool_policy_digest":digest(entries)}
    flow_client = "business-"+digest([principal.email.lower(),body["clientRequestId"]])
    flow_body = {"clientRequestId":flow_client, "name":TITLE, "graph":graph,
        "input":prepared.shape["workflowInput"], "dryRun":False}
    raced = False
    with mutation(principal):
        if m.AiReportRun.objects.filter(owner_email=principal.email.lower(),
                client_request_id=body["clientRequestId"]).exists():
            raced = True
        else:
            current_principal(principal, admin=True, write=True)
            _require(screening_creation._model_digest(workflows.resolve_model(model.id)) == model_digest,
                "模型配置在准备期间已变化", "model_version_changed")
            screening_creation._limits(principal)
            shape, capsule = _revalidate_metadata(prepared, principal)
            _require(not m.AiReportRun.objects.filter(pk=report_id).exists()
                and not m.AiBusinessScreeningRun.objects.filter(pk=screening_id).exists())
            current_principal(principal, admin=True, write=True)
            parameters = screening_creation._insert_budget(capsule, principal) if capsule else None
            _require(not m.AiWorkflowRuns.objects.filter(owner_email=principal.email.lower(),
                client_request_id=flow_client).exists(), "工作流请求标识已被占用，不能借用")
            flow = m.AiWorkflowRuns.objects.create(id=uid("ai-workflow"), owner_email=principal.email.lower(),
                scope_json=canonical(principal.scope), client_request_id=flow_client,
                request_digest=digest({"payload":flow_body,"admission":admitted,"executionProfile":promotion.PROFILE}),
                name=TITLE, graph_json=canonical(graph), graph_digest=digest(graph),
                input_json=canonical(shape["workflowInput"]), dry_run=0, **admitted)
            report_library.pin(flow.id, TITLE, None, entries)
            for position, node in enumerate(graph["nodes"]):
                m.AiWorkflowNodeRuns.objects.create(id=uid("ai-node"), run=flow, node_key=node["key"],
                    position=position, node_type=node["type"], depends_on_json=canonical(node["dependsOn"]),
                    instruction=node["instruction"])
            row = m.AiReportRun.objects.create(id=report_id, owner_email=principal.email.lower(),
                scope_json=canonical(principal.scope), client_request_id=body["clientRequestId"],
                request_digest=digest(body), workflow=flow,
                snapshot_json=canonical(shape["snapshot"]), budget_plan=parameters)
            current_principal(principal, admin=True, write=True)
            workflows.event(flow, principal, "created")
            result = {"item":{"id":row.id, "workflowId":flow.id}, "replayed":False}
            if commit is not None:
                return commit(result, 200)
    if not raced:
        return result
    replayed = _replay(body, principal)
    if commit is None:
        return replayed
    with mutation(principal):
        return commit(replayed, 200)
