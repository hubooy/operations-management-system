"""Internal creation of a frozen five-tool market profile; execution stays paused."""
import json

from django.db import DatabaseError

from . import business_market_v2_admitted_paused as admitted
from . import business_market_v2_execution_snapshot_contract as contract
from . import business_promotion_market_admission as owning_admission
from . import business_promotion_market_runtime_v2_contract as runtime
from . import models as m
from . import transport
from .policy import AiError, canonical, current_principal, digest, fields, identifier, mutation, uid

REQUEST_SCHEMA = "business-market-v2-execution-create-v1"
TITLE = "京东市场v2五工具执行档案（尚未激活）"


def _need(ok):
    if not ok:
        raise AiError("市场v2执行档案与准入材料或五工具目录不一致", "conflict", 409)


def _root(admitted_id, principal):
    actor = current_principal(principal, admin=True)
    prior = m.AiReportRun.objects.select_related("workflow").get(pk=admitted_id)
    snapshot = json.loads(prior.snapshot_json)
    claim = snapshot.get("marketAdmission", {})
    _need(prior.owner_email == actor.email.lower()
        and prior.scope_json == "null"
        and snapshot.get("executionProfile") == admitted.PROFILE
        and snapshot.get("schemaVersion") == admitted.SNAPSHOT_SCHEMA
        and snapshot.get("reportId") == prior.id
        and prior.workflow.status == "paused"
        and prior.workflow.error_code == admitted.PAUSE_REASON
        and prior.workflow.allowed_tools_json == "[]"
        and prior.workflow.model_id == ""
        and prior.workflow.provider_round_count == 0
        and prior.workflow.tool_call_count == 0)
    parked, parked_snapshot = admitted.parked_report_and_snapshot(
        claim.get("parkedReportId"), principal)
    _need(parked.owner_email == actor.email.lower()
        and parked_snapshot["sourceRoot"]["sourceReportId"] != prior.id)
    source_id = parked_snapshot["sourceRoot"]["sourceReportId"]
    observed = owning_admission.require_observed(source_id,
        parked_snapshot["marketSelector"], principal)
    prepared = runtime.prepare(observed,
        with_budget=parked_snapshot["withBudget"])
    _need(claim.get("selectorDigest") is not None
        and claim.get("manifestDigest") is not None
        and prepared["marketSelector"] == parked_snapshot["marketSelector"]
        and prepared["reportId"] == source_id)
    return {"admittedReportId": prior.id, "parkedReportId": parked.id,
        "sourceReportId": source_id, "ownerEmail": actor.email.lower(),
        "selectorDigest": claim["selectorDigest"],
        "manifestDigest": claim["manifestDigest"],
        "marketContextDigest": prepared["marketContextDigest"],
        "withBudget": parked_snapshot["withBudget"]}


def create(body, principal):
    """Persist a report/flow plan only; no node, job, provider or tool read."""
    fields(body, {"schemaVersion", "clientRequestId", "admittedReportId"},
        {"schemaVersion", "clientRequestId", "admittedReportId"})
    if body["schemaVersion"] != REQUEST_SCHEMA:
        raise AiError("市场v2执行档案创建协议无效")
    actor = current_principal(principal, admin=True, write=True)
    client = identifier(body["clientRequestId"], "clientRequestId")
    admitted_id = identifier(body["admittedReportId"], "admittedReportId")
    identity = digest(body)
    existing = m.AiReportRun.objects.select_related("workflow").filter(
        owner_email=actor.email.lower(), client_request_id=client).first()
    if existing is not None:
        snapshot = json.loads(existing.snapshot_json)
        _need(existing.request_digest == identity
            and snapshot.get("executionProfile") == contract.PROFILE
            and snapshot.get("executionRoot", {}).get("admittedReportId") == admitted_id
            and existing.workflow.status == "paused"
            and existing.workflow.error_code == contract.PAUSE_REASON)
        _root(admitted_id, principal)
        return {"reportId": existing.id, "workflowId": existing.workflow_id,
            "replayed": True, "agentDispatchSupported": False}
    root = _root(admitted_id, principal)
    entries = transport.catalog(principal, contract.SURFACE)
    contract.catalog(entries)
    report_id, flow_id = uid("market-execution-report"), uid("market-execution-flow")
    built = contract.build(report_id, root, entries)
    try:
        with mutation(principal):
            _need(_root(admitted_id, principal) == root)
            _need(contract.catalog(transport.catalog(principal, contract.SURFACE))
                == entries)
            _need(not m.AiReportRun.objects.filter(owner_email=actor.email.lower(),
                client_request_id=client).exists())
            flow = m.AiWorkflowRuns.objects.create(id=flow_id,
                owner_email=actor.email.lower(), scope_json="null",
                client_request_id="market-execution-" + digest([actor.email.lower(), client]),
                request_digest=identity, name=TITLE,
                graph_json=canonical(built["graph"]),
                graph_digest=contract.GRAPH_DIGESTS[root["withBudget"]],
                input_json=canonical(built["workflowInput"]), dry_run=0,
                model_id="", model_version=0,
                allowed_tools_json=canonical(list(contract.TOOL_ORDER)),
                tool_policy_digest=contract.CATALOG_DIGEST,
                status="paused", error_code=contract.PAUSE_REASON, retryable=0)
            m.AiReportRun.objects.create(id=report_id,
                owner_email=actor.email.lower(), scope_json="null",
                client_request_id=client, request_digest=identity,
                workflow=flow, budget_plan=None,
                snapshot_json=canonical(built["snapshot"]))
            _need(current_principal(principal, admin=True, write=True).email.lower()
                == actor.email.lower())
    except DatabaseError as error:
        if "ai_market_v2_execution_" not in str(error):
            raise
        raise AiError("市场v2执行档案未通过数据库固定根检查", "conflict", 409) from error
    return {"reportId": report_id, "workflowId": flow_id,
        "replayed": False, "agentDispatchSupported": False}
