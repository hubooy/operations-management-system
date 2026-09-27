"""Create a distinct material-admitted market v2 root, still unable to run.

The parked report and immutable 0045 material remain unchanged.  This writer
only records a paused snapshot and never creates nodes, jobs or tool outputs.
"""
import json
from django.db import DatabaseError, connection

from . import business_market_v2_parked_creation as parked
from . import business_market_v2_material_admission as material_owner
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from .policy import AiError, canonical, current_principal, digest, fields, identifier, mutation, uid


REQUEST_SCHEMA = "business-market-v2-admitted-paused-create-v1"
SNAPSHOT_SCHEMA = "business-market-v2-admitted-paused-snapshot-v1"
INPUT_SCHEMA = "business-market-v2-admitted-paused-input-v1"
PROFILE = "business-agent-screening-promotion-market-admitted-v2"
PAUSE_REASON = "market_v2_tool_not_registered"
TITLE = "京东市场v2材料准入报告（待工具注册）"


def _need(ok, message="市场v2材料准入快照与停放报告不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _prepared(parked_id, principal):
    parked_report, snapshot = parked_report_and_snapshot(parked_id, principal)
    # 0045 deliberately denies the writer direct SELECT on its SQL-owned
    # sidecar. Rebuild the full owning material outside the write transaction;
    # the 0056 definer guards compare these claims with the immutable row.
    candidate = material_owner.prepare_candidate(parked_id, principal)["candidate"]
    _need(candidate["sourceReportId"] == snapshot["sourceRoot"]["sourceReportId"])
    with connection.cursor() as cursor:
        cursor.execute("SELECT encode(sha256(convert_to((snapshot_json::jsonb->"
            "'marketSelector')::text,'UTF8')),'hex') FROM public.ai_report_runs "
            "WHERE id=%s", [parked_id])
        row = cursor.fetchone()
    _need(row is not None and type(row[0]) is str and len(row[0]) == 64)
    with_budget = snapshot["withBudget"]
    _need(type(with_budget) is bool)
    graph = runtime.graph(with_budget)
    admission = {"parkedReportId": parked_id,
        "selectorDigest": row[0],
        "manifestDigest": candidate["manifestDigest"]}
    return parked_report, admission, with_budget, graph


def parked_report_and_snapshot(parked_id, principal):
    # Reuse the owning read fence.  Only the database guard can finally admit
    # the sidecar when both new rows are inserted atomically.
    from . import business_market_v2_material_admission as owner
    return owner._parked(parked_id, principal)


def create(body, principal):
    fields(body, {"schemaVersion", "clientRequestId", "parkedReportId"},
        {"schemaVersion", "clientRequestId", "parkedReportId"})
    if body["schemaVersion"] != REQUEST_SCHEMA:
        raise AiError("市场v2材料准入创建协议无效")
    current_principal(principal, admin=True, write=True)
    client = identifier(body["clientRequestId"], "clientRequestId")
    parked_id = identifier(body["parkedReportId"], "parkedReportId")
    request_digest = digest(body)
    existing = m.AiReportRun.objects.select_related("workflow").filter(
        owner_email=principal.email.lower(), client_request_id=client).first()
    if existing is not None:
        snapshot = json.loads(existing.snapshot_json)
        _need(existing.request_digest == request_digest
            and snapshot.get("executionProfile") == PROFILE
            and snapshot.get("marketAdmission", {}).get("parkedReportId") == parked_id
            and existing.workflow.status == "paused"
            and existing.workflow.error_code == PAUSE_REASON)
        parked_report_and_snapshot(parked_id, principal)
        return {"reportId": existing.id, "workflowId": existing.workflow_id,
            "replayed": True, "agentDispatchSupported": False}
    parked_report, admission, with_budget, graph = _prepared(parked_id, principal)
    parked_digest = digest(parked_report.snapshot_json)
    report_id, flow_id = uid("market-admitted-report"), uid("market-admitted-flow")
    root = {"executionProfile": PROFILE, "reportId": report_id,
        "marketAdmission": admission, "withBudget": with_budget,
        "proposedTools": list(runtime.TOOL_ORDER), "registered": False,
        "agentDispatchSupported": False, "humanReviewRequired": True,
        "marketAndOwnSalesAdditive": False}
    snapshot = {"schemaVersion": SNAPSHOT_SCHEMA, **root}
    flow_input = {"schemaVersion": INPUT_SCHEMA, **root,
        "graphDigest": digest(graph), "allowedTools": []}
    try:
        with mutation(principal):
            _need(not m.AiReportRun.objects.filter(owner_email=principal.email.lower(),
                client_request_id=client).exists())
            current_parked, _ = parked_report_and_snapshot(parked_id, principal)
            _need(digest(current_parked.snapshot_json) == parked_digest)
            flow = m.AiWorkflowRuns.objects.create(id=flow_id,
                owner_email=principal.email.lower(), scope_json="null",
                client_request_id="market-admitted-" + digest([principal.email.lower(), client]),
                request_digest=request_digest, name=TITLE,
                graph_json=canonical(graph), graph_digest=digest(graph),
                input_json=canonical(flow_input), dry_run=0,
                model_id="", model_version=0, allowed_tools_json="[]",
                tool_policy_digest=digest([]), status="paused",
                error_code=PAUSE_REASON, retryable=0)
            m.AiReportRun.objects.create(id=report_id,
                owner_email=principal.email.lower(), scope_json="null",
                client_request_id=client, request_digest=request_digest,
                workflow=flow, budget_plan=None, snapshot_json=canonical(snapshot))
            _need(current_principal(principal, admin=True, write=True).email.lower()
                == principal.email.lower())
    except DatabaseError as error:
        # A missing/conflicting 0045 row is rejected by the database guard.
        # Preserve unrelated database outages and schema failures as-is.
        if "ai_market_v2_admitted_" not in str(error):
            raise
        raise AiError("市场v2材料尚未与停放报告精确准入", "conflict", 409) from error
    return {"reportId": report_id, "workflowId": flow_id,
        "replayed": False, "agentDispatchSupported": False}
