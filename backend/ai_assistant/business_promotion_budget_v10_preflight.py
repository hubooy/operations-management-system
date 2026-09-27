"""Default-disabled, read-only owning builder for the closed 0057 body.

The result is a process assertion, never an attestation DB insert or a ready
file permission. SQL 0057 cannot independently verify the two process digests.
"""
from __future__ import annotations

import hashlib
import json
import time

from access_control.models import AppUser
from business_analysis import promotion_budget_attestation_v10 as pure
from business_analysis import volume_delivery
from business_analysis.contracts import AnalysisContractError
from django.db import connection

from . import business_files as files
from . import business_promotion_approved_content as approved_content
from . import business_promotion_budget as budget_reader
from . import business_promotion_budget_v10_stage as stage
from . import models as m
from .policy import AiError, canonical, current_principal, digest, identifier


MAX_SECONDS = 600
MAX_FULL_JSON = volume_delivery.MAX_MANIFEST_BYTES


def _reject(message="v10暂存预检与当前已批准报告或持久字节不一致"):
    raise AiError(message, "conflict", 409)


def _row_state(row):
    return {"runId": row.id, "reportId": row.report_id,
        "ownerEmail": row.owner_email, "scopeJson": row.scope_json,
        "rendererVersion": row.renderer_version, "draft": row.draft,
        "status": row.status, "errorCode": row.error_code,
        "version": row.version, "attempt": row.attempt,
        "storedBytes": row.stored_bytes,
        "bindingDigest": row.binding_digest,
        "progressJson": row.progress_json,
        "manifestJsonSha256": hashlib.sha256(
            row.manifest_json.encode("utf-8")).hexdigest(),
        "reportSnapshotSha256": hashlib.sha256(
            row.report.snapshot_json.encode("utf-8")).hexdigest(),
        "workflowInputSha256": hashlib.sha256(
            row.report.workflow.input_json.encode("utf-8")).hexdigest(),
        "budgetPlanId": row.report.budget_plan_id,
        "workflowStatus": row.report.workflow.status,
        "workflowCompletedAt": row.report.workflow.completed_at}


def _actor(principal):
    current_principal(principal, admin=True)
    actor = AppUser.objects.filter(email=principal.email.lower(),
        status="active").values("email", "role", "scope", "version").first()
    if (actor is None or actor["role"] != "admin" or actor["scope"] is not None
            or type(actor["version"]) is not int or actor["version"] < 1):
        _reject("v10暂存预检必须使用当前有效的无范围管理员")
    return actor


def _roots(row, principal):
    current = approved_content.build(row.report_id, principal)
    binding = current["binding"]
    present = row.report.budget_plan_id is not None
    if (binding["reportId"] != row.report_id
            or binding["humanReview"]["status"] != "approved"
            or present != ("budgetPlanDigest" in binding)):
        _reject("v10暂存已批准内容与固定预算存在性变化")
    if present:
        owned = budget_reader._roots(row.report_id, principal)
        prepared = owned["prepared"]
        plan_digest = prepared.binding["planDigest"]
        if (plan_digest != binding["budgetPlanDigest"]
                or prepared.reference != json.loads(
                    row.report.snapshot_json)["budgetRef"]):
            _reject("v10暂存预算参数或来源根变化")
        roots_digest = digest(["fixed_budget", owned["rowDigest"],
            prepared.plan_json, prepared.binding_json, prepared.result_json])
    else:
        plan_digest = None
        roots_digest = digest(["no_fixed_budget", row.report_id])
    return current, present, plan_digest, roots_digest


def _full_json(row, compact, checkpoint):
    descriptor = compact["manifestFile"]
    if (descriptor["volumeIndex"] != 0 or descriptor["format"] != "json"
            or not 1 <= descriptor["bytes"] <= MAX_FULL_JSON):
        _reject("v10完整JSON超出固定容量")
    chunks = m.AiBusinessVolumeChunk.objects.filter(run=row,
        attempt=row.attempt, volume_index=0, format="json").order_by(
        "sequence").iterator(chunk_size=4)
    parts, size = [], 0
    sha = hashlib.sha256()
    for index, part in enumerate(chunks, 1):
        checkpoint()
        raw = bytes(part.content)
        size += len(raw)
        if (index > descriptor["chunkCount"] or size > MAX_FULL_JSON
                or part.sequence != index
                or hashlib.sha256(raw).hexdigest() != part.content_digest):
            _reject("v10完整JSON分块或字节上限变化")
        sha.update(raw)
        parts.append(raw)
    if (len(parts) != descriptor["chunkCount"] or size != descriptor["bytes"]
            or sha.hexdigest() != descriptor["sha256"]):
        _reject("v10完整JSON与紧凑清单不一致")
    return b"".join(parts)


def prepare(run_id, principal, *, enabled=False, checkpoint=None):
    """Return exact canonical 0057 text only after full owning verification."""
    if enabled is not True:
        _reject("v10暂存预检默认关闭")
    if connection.in_atomic_block:
        _reject("v10全量预检不能在长事务内执行")
    started = time.monotonic()

    def bounded(*args, **kwargs):
        if time.monotonic() - started > MAX_SECONDS:
            raise AiError("v10全量预检超过固定时间边界", "timeout", 408)
        if checkpoint is not None:
            checkpoint(*args, **kwargs)

    row = files.get(identifier(run_id), principal)
    state = _row_state(row)
    actor = _actor(principal)
    if (state["rendererVersion"] != 10 or state["draft"] is not False
            or state["status"] != "paused"
            or state["errorCode"] != "renderer_unpublished"
            or json.loads(state["progressJson"]) != {
                "stage": "staged_unpublished", "attempt": state["attempt"]}
            or state["attempt"] not in range(1, 6)
            or state["storedBytes"] < 1
            or state["workflowStatus"] != "completed"
            or state["workflowCompletedAt"] is None):
        _reject("v10文件任务不是固定版本的已批准暂停暂存")
    current_binding = stage.binding(row.report, principal)
    if current_binding != row.binding_digest:
        _reject("v10批准内容、证据或固定预算已变化")
    approved, present, plan_digest, budget_roots_digest = _roots(row, principal)
    bounded()
    compact = stage._verify_staged(row, principal, bounded)
    bounded()
    raw_full = _full_json(row, compact, bounded)
    try:
        full = volume_delivery.verify_full(compact, raw_full,
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, report_id=row.report_id,
            evidence_digest=approved["binding"]["sealedDigest"],
            renderer_version=10)
        result = pure.compose(run_id=row.id, attempt=row.attempt,
            run_version=row.version, binding_digest=row.binding_digest,
            compact_json=row.manifest_json, full_json_bytes=raw_full,
            full_manifest=full,
            approved_content_digest=approved["dtoDigest"],
            human_review_digest=approved["binding"]["humanReview"]["reviewDigest"],
            budget_present=present, budget_plan_digest=plan_digest,
            budget_roots_digest=budget_roots_digest,
            report_snapshot_sha256=state["reportSnapshotSha256"],
            workflow_input_sha256=state["workflowInputSha256"],
            actor_version=actor["version"],
            fresh_semantics_verified=True)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, RecursionError) as error:
        raise AiError("v10预检正文与完整文件证明不能重建", "conflict", 409) from error
    bounded()
    again = files.get(row.id, principal)
    if (_row_state(again) != state or _actor(principal) != actor
            or stage.binding(again.report, principal) != current_binding):
        _reject("v10预检期间文件版本、管理员或来源绑定变化")
    final_approved, final_present, final_plan, final_roots = _roots(
        again, principal)
    if (final_approved != approved or final_present != present
            or final_plan != plan_digest or final_roots != budget_roots_digest):
        _reject("v10预检期间批准正文或固定预算根变化")
    bounded()
    return {"schemaVersion": "business-promotion-budget-v10-owning-preflight-v1",
        "runId": row.id, "attempt": row.attempt,
        "runVersion": row.version, "bindingDigest": row.binding_digest,
        "attestationText": result["attestationText"],
        "attestationSha256": result["attestationSha256"],
        "owningVerificationDigest": result["owningVerificationDigest"],
        "publicationFenceDigest": result["publicationFenceDigest"],
        "candidateOnly": True,
        "databaseCanIndependentlyVerifyProcessAssertions": False,
        "readyAuthorized": False}
