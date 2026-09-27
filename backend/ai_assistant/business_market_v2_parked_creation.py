"""Internal default-paused market-v2 report root; no Agent or file launch."""
from __future__ import annotations

import json

from . import business_diagnostic_screening as screening
from . import business_market_v2_report_creation_candidate as candidate_service
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from .policy import AiError, canonical, current_principal, digest, fields, mutation, uid


REQUEST_SCHEMA = "business-market-v2-parked-create-v1"
SNAPSHOT_SCHEMA = "business-market-v2-parked-snapshot-v1"
INPUT_SCHEMA = "business-market-v2-parked-input-v1"
PAUSE_REASON = "market_material_not_admitted"
TITLE = "京东市场v2经营分析（待材料准入）"
TOOL_POLICY_DIGEST = digest(runtime._policy())
EMPTY_TOOL_POLICY_DIGEST = digest([])


def _need(ok, message="市场v2待准入报告根身份不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _saved(row):
    return {"id": row.id, "workflowId": row.workflow_id,
        "workflowStatus": row.workflow.status,
        "pauseReason": row.workflow.error_code,
        "reportGenerationSupported": False,
        "agentDispatchSupported": False,
        "marketMaterialReady": False}


def _shape(prepared, report_id):
    source = prepared["snapshot"]["sourceRoot"]
    root = {key: source[key] for key in ("sourceReportId", "sourceWorkflowId",
        "sourceSnapshotDigest", "sourceWorkflowInputDigest", "evidenceRunId",
        "evidenceVersion", "sealedDigest")}
    selector = prepared["snapshot"]["market"]["selector"]
    snapshot = {"schemaVersion": SNAPSHOT_SCHEMA,
        "executionProfile": runtime.PROFILE,
        "evidenceProtocol": "reference-v2",
        "reportId": report_id,
        "sourceRoot": root,
        "marketSelector": selector,
        "marketAlgorithms": {"priceBand": runtime.market.BAND_ALGORITHM,
            "rankEntryExit": runtime.market.RANK_ALGORITHM},
        "proposedTools": prepared["allowedTools"],
        "roleReadPolicyDigest": TOOL_POLICY_DIGEST,
        "withBudget": source["budgetRef"] is not None,
        "marketMaterialReady": False,
        "marketAndOwnSalesAdditive": False,
        "humanReviewRequired": True,
        "registered": False}
    workflow_input = {**snapshot, "schemaVersion": INPUT_SCHEMA,
        "graphDigest": prepared["workflowGraphDigest"],
        "allowedTools": []}
    return snapshot, workflow_input


def create(body, principal, *, commit=None):
    """Persist only paused report/workflow metadata after complete preparation."""
    fields(body, {"schemaVersion", "clientRequestId", "sourceReportId",
        "marketSelector"}, {"schemaVersion", "clientRequestId", "sourceReportId",
        "marketSelector"})
    if body["schemaVersion"] != REQUEST_SCHEMA:
        raise AiError("市场v2待准入创建协议无效")
    current_principal(principal, admin=True, write=True)
    body = json.loads(canonical(body))
    from .policy import identifier
    client = identifier(body["clientRequestId"])
    source_report_id = identifier(body["sourceReportId"])
    identity = digest(body)
    existing = m.AiReportRun.objects.select_related("workflow").filter(
        owner_email=principal.email.lower(), client_request_id=client).first()
    if existing is not None:
        _need(existing.request_digest == identity
            and existing.workflow.status == "paused"
            and existing.workflow.error_code == PAUSE_REASON
            and json.loads(existing.snapshot_json).get("executionProfile") == runtime.PROFILE,
            "市场v2请求标识已绑定不同报告或状态")
        screening._load(source_report_id, principal)
        result = {"item": _saved(existing), "replayed": True}
        if commit is not None:
            with mutation(principal):
                return commit(result, 200)
        return result
    report_id = uid("market-v2-report")
    prepared = candidate_service.prepare(source_report_id, report_id,
        body["marketSelector"], principal)
    snapshot, workflow_input = _shape(prepared, report_id)
    graph = prepared["workflowGraph"]
    tools = prepared["allowedTools"]
    _need(prepared["marketMaterialRowsVerified"] is True
        and prepared["registered"] is False
        and tools == list(runtime.TOOL_ORDER)
        and prepared["workflowGraphDigest"] == digest(graph))
    flow_client = "market-v2-" + digest([principal.email.lower(), client])
    with mutation(principal):
        _need(not m.AiReportRun.objects.filter(pk=report_id).exists()
            and not m.AiReportRun.objects.filter(owner_email=principal.email.lower(),
                client_request_id=client).exists()
            and not m.AiWorkflowRuns.objects.filter(owner_email=principal.email.lower(),
                client_request_id=flow_client).exists(),
            "市场v2候选准备后目标报告或请求标识冲突")
        fixed = screening._load(source_report_id, principal)[0]
        _need(fixed["snapshotDigest"] == snapshot["sourceRoot"]["sourceSnapshotDigest"],
            "市场v2来源报告准备期间变化")
        flow = m.AiWorkflowRuns.objects.create(id=uid("ai-workflow"),
            owner_email=principal.email.lower(), scope_json="null",
            client_request_id=flow_client, request_digest=identity,
            name=TITLE, graph_json=canonical(graph), graph_digest=digest(graph),
            input_json=canonical(workflow_input), dry_run=0,
            model_id="", model_version=0,
            allowed_tools_json=canonical([]),
            tool_policy_digest=EMPTY_TOOL_POLICY_DIGEST,
            status="paused", error_code=PAUSE_REASON, retryable=0)
        row = m.AiReportRun.objects.create(id=report_id,
            owner_email=principal.email.lower(), scope_json="null",
            client_request_id=client, request_digest=identity,
            workflow=flow, budget_plan=None,
            snapshot_json=canonical(snapshot))
        _need(current_principal(principal, admin=True, write=True).email.lower()
            == principal.email.lower(), "市场v2创建期间账号变化")
        result = {"item": _saved(row), "replayed": False}
        if commit is not None:
            return commit(result, 200)
    return result
