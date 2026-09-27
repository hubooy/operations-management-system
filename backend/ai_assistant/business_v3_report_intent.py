"""Persist an inert v3 admission intent without creating any runnable AI job."""
from __future__ import annotations

import json

from business_analysis import report_intent_v3 as contract
from business_analysis.contracts import AnalysisContractError

from . import business_evidence_v3 as plan, business_report_candidate_v3 as candidate_service
from . import models as m
from .policy import AiError, authorize_owner, canonical, digest, fields, identifier, mutation, uid

REQUEST_SCHEMA = "business-v3-report-intent-create-v1"


def _mapping(row):
    return {"id": row.id, "status": row.status, "pauseReason": row.pause_reason,
            "evidenceRunId": row.evidence_run_id, "evidenceVersion": row.evidence_version,
            "sealedDigest": row.sealed_digest, "candidateDigest": row.candidate_digest,
            "createdAt": row.created_at.isoformat(), "workflowRunId": None,
            "modelDispatchSupported": False, "reportGenerationSupported": False,
            "fileGenerationSupported": False}


def _admission_body(body):
    return {"schemaVersion": candidate_service.REQUEST_SCHEMA,
            "executionProfile": body["executionProfile"],
            "evidenceRunId": body["evidenceRunId"],
            "expectedEvidenceVersion": body["expectedEvidenceVersion"],
            "expectedSealDigest": body["expectedSealDigest"]}


def create(body, principal):
    """Freshly verify v3 evidence, then atomically pin a paused intent only."""
    fields(body, {"schemaVersion", "clientRequestId", "executionProfile", "evidenceRunId",
                  "expectedEvidenceVersion", "expectedSealDigest"},
           {"schemaVersion", "clientRequestId", "executionProfile", "evidenceRunId",
            "expectedEvidenceVersion", "expectedSealDigest"})
    if body["schemaVersion"] != REQUEST_SCHEMA:
        raise AiError("v3报告意图创建协议无效")
    client = identifier(body["clientRequestId"])
    actor = plan._actor(principal)
    prepared = candidate_service.prepare(_admission_body(body), principal)
    candidate = prepared["candidate"]
    verified_seal_json = canonical(prepared["verifiedSeal"])
    try:
        staged = contract.stage(candidate)
    except (AnalysisContractError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise AiError("v3暂停意图无法从固定候选生成", "conflict", 409) from error
    snapshot_raw = canonical(staged["snapshot"])
    input_raw = canonical(staged["workflowInput"])
    plan_raw = canonical(staged["workflowPlan"])
    pinned = candidate["reference"]
    identity = digest({"schemaVersion": REQUEST_SCHEMA, "ownerEmail": principal.email.lower(),
        "request": body, "candidateDigest": candidate["candidateDigest"]})
    with mutation(principal):
        if plan._actor(principal) != actor:
            raise AiError("v3意图落地前账号权限变化", "access_denied", 403)
        parent = authorize_owner(m.AiBusinessEvidenceRun.objects.select_for_update()
            .get(pk=identifier(body["evidenceRunId"])), principal)
        if (parent.status != "sealed" or parent.version != pinned["evidenceVersion"]
                or parent.state_json != verified_seal_json
                or json.loads(parent.state_json)["sealedDigest"] != pinned["sealedDigest"]):
            raise AiError("v3封存状态在意图落地时变化", "version_conflict", 409)
        old = m.AiBusinessV3ReportIntent.objects.filter(owner_email=principal.email.lower(),
            client_request_id=client).first()
        if old is not None:
            authorize_owner(old, principal)
            if (old.request_digest != identity or old.snapshot_json != snapshot_raw
                    or old.status != "paused" or old.pause_reason != contract.PAUSE_REASON):
                raise AiError("v3请求标识已绑定不同封存或计划", "conflict", 409)
            return {"item": _mapping(old), "replayed": True}
        if (m.AiBusinessV3ReportIntent.objects.filter(owner_email=principal.email.lower()).count() >= 1000
                or m.AiBusinessV3ReportIntent.objects.count() >= 10000):
            raise AiError("v3暂停意图存储容量已满", "rate_limited", 429)
        row = m.AiBusinessV3ReportIntent.objects.create(id=uid("business-v3-intent"),
            owner_email=principal.email.lower(), client_request_id=client,
            request_digest=identity, evidence_run=parent,
            evidence_version=pinned["evidenceVersion"], sealed_digest=pinned["sealedDigest"],
            candidate_digest=candidate["candidateDigest"],
            snapshot_digest=digest(snapshot_raw), snapshot_json=snapshot_raw,
            workflow_input_digest=digest(input_raw), workflow_input_json=input_raw,
            workflow_plan_digest=digest(plan_raw), workflow_plan_json=plan_raw,
            status="paused", pause_reason=contract.PAUSE_REASON)
        if plan._actor(principal) != actor:
            raise AiError("v3意图落地期间账号权限变化", "access_denied", 403)
    return {"item": _mapping(row), "replayed": False}


def inspect(intent_id, principal):
    """Read only after re-verifying the sealed source and the exact inert plan."""
    actor = plan._actor(principal)
    row = m.AiBusinessV3ReportIntent.objects.filter(pk=identifier(intent_id)).first()
    if row is None:
        raise AiError("v3暂停意图不存在", "not_found", 404)
    authorize_owner(row, principal)
    if (row.status != "paused" or row.pause_reason != contract.PAUSE_REASON
            or row.snapshot_digest != digest(row.snapshot_json)
            or row.workflow_input_digest != digest(row.workflow_input_json)
            or row.workflow_plan_digest != digest(row.workflow_plan_json)):
        raise AiError("v3暂停意图持久摘要或状态无效", "conflict", 409)
    try:
        candidate = json.loads(row.snapshot_json)
        staged = contract.stage(candidate)
    except (AnalysisContractError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise AiError("v3暂停意图快照不可重建", "conflict", 409) from error
    if (row.candidate_digest != candidate["candidateDigest"]
            or row.workflow_input_json != canonical(staged["workflowInput"])
            or row.workflow_plan_json != canonical(staged["workflowPlan"])):
        raise AiError("v3暂停意图工作流输入与固定来源不一致", "conflict", 409)
    verified = candidate_service.prepare({"schemaVersion": candidate_service.REQUEST_SCHEMA,
        "executionProfile": candidate["executionProfile"],
        "evidenceRunId": row.evidence_run_id, "expectedEvidenceVersion": row.evidence_version,
        "expectedSealDigest": row.sealed_digest}, principal)["candidate"]
    if candidate != verified or plan._actor(principal) != actor:
        raise AiError("v3暂停意图对应的封存或账号已变化", "version_conflict", 409)
    return {"item": _mapping(row), "candidate": verified,
            "workflowInput": staged["workflowInput"], "workflowPlan": staged["workflowPlan"]}
