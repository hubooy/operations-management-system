"""Default-off owning, full-byte preflight for the 0067 SQL sidecar.

This reads current approved five-agent and budget roots, reconstructs every
staged byte, verifies slim HTML gzip/NDJSON rows, regenerates current HTML and
XLSX, compares ZIP members and inspects OPC/formulas. It never publishes.
"""
from __future__ import annotations

import json
import time

from business_analysis import (promotion_budget_attestation_v11 as pure,
    volume_delivery, xlsx_opc_v11)
from business_analysis.contracts import AnalysisContractError, digest
from django.db import connection

from . import business_files as files
from . import business_promotion_budget_v10_preflight as prior
from . import business_promotion_budget_v11_durable_stage as stage
from .policy import AiError, identifier


MAX_SECONDS = 600


def _reject(message="v11暂存独立校验与当前批准报告或持久文件不一致"):
    raise AiError(message, "conflict", 409)


def prepare(run_id, principal, *, enabled=False, checkpoint=None):
    """Build exact attestation text; no role switch or database mutation."""
    if enabled is not True:
        _reject("v11独立校验默认关闭")
    if connection.in_atomic_block:
        _reject("v11全量文件校验不能处于长事务内")
    started = time.monotonic()

    def bounded(*args, **kwargs):
        if time.monotonic() - started > MAX_SECONDS:
            raise AiError("v11全量文件校验超过固定时间边界", "timeout", 408)
        if checkpoint is not None:
            checkpoint(*args, **kwargs)

    row = files.get(identifier(run_id), principal)
    state = prior._row_state(row)
    actor = prior._actor(principal)
    if (state["rendererVersion"] != 11 or state["draft"] is not False or
            state["status"] != "paused" or
            state["errorCode"] != "renderer_unpublished" or
            json.loads(state["progressJson"]) != {
                "stage": "staged_unpublished", "attempt": state["attempt"]} or
            state["attempt"] not in range(1, 6) or
            state["storedBytes"] < 1 or
            state["workflowStatus"] != "completed" or
            state["workflowCompletedAt"] is None):
        _reject("v11文件任务不是已批准的暂存未发布状态")
    current_binding = stage.binding(row.report, principal)
    if current_binding != row.binding_digest:
        _reject("v11批准内容、证据或固定预算已变化")
    approved, present, plan_digest, budget_roots_digest = prior._roots(
        row, principal)
    evidence = {}

    def collect(paths, full, compact, check):
        opc = []
        for volume in full["volumes"]:
            index = volume["volumeIndex"]
            check()
            opc.append({"volumeIndex": index, **xlsx_opc_v11.inspect(
                paths[index, "xlsx"], volume, checkpoint=check)})
        evidence["fileByteVerificationDigest"] = digest({
            "schemaVersion": "budget-v11-full-byte-verification-v1",
            "files": compact["files"], "manifestFile": compact["manifestFile"],
            "opcMembers": [item["memberDigest"] for item in opc]})
        evidence["htmlRowsDigest"] = digest([{
            "volumeIndex": volume["volumeIndex"],
            "payloadDigest": volume["htmlPayload"]["proofDigest"],
            "tables": volume["htmlPayload"]["tables"]}
            for volume in full["volumes"]])
        evidence["xlsxOpcFormulaDigest"] = digest(opc)

    bounded()
    compact = stage._verify_staged(row, principal, bounded,
        file_evidence=collect)
    if set(evidence) != {"fileByteVerificationDigest", "htmlRowsDigest",
            "xlsxOpcFormulaDigest"}:
        _reject("v11 HTML/XLSX 独立校验结果缺失")
    bounded()
    raw_full = prior._full_json(row, compact, bounded)
    try:
        full = volume_delivery.verify_full(compact, raw_full,
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, report_id=row.report_id,
            evidence_digest=approved["binding"]["sealedDigest"],
            renderer_version=11)
        result = pure.compose(run_id=row.id, attempt=row.attempt,
            run_version=row.version, binding_digest=row.binding_digest,
            compact_json=row.manifest_json, full_json_bytes=raw_full,
            full_manifest=full,
            approved_content_digest=approved["dtoDigest"],
            human_review_digest=approved["binding"]["humanReview"][
                "reviewDigest"], budget_present=present,
            budget_plan_digest=plan_digest,
            budget_roots_digest=budget_roots_digest,
            report_snapshot_sha256=state["reportSnapshotSha256"],
            workflow_input_sha256=state["workflowInputSha256"],
            actor_version=actor["version"],
            file_byte_verification_digest=evidence["fileByteVerificationDigest"],
            html_rows_digest=evidence["htmlRowsDigest"],
            xlsx_opc_formula_digest=evidence["xlsxOpcFormulaDigest"],
            fresh_semantics_verified=True)
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, RecursionError) as error:
        raise AiError("v11独立证明无法从持久文件重建", "conflict", 409) from error
    bounded()
    again = files.get(row.id, principal)
    if (prior._row_state(again) != state or prior._actor(principal) != actor or
            stage.binding(again.report, principal) != current_binding):
        _reject("v11校验期间文件版本、管理员或来源绑定已变化")
    new_approved, new_present, new_plan, new_roots = prior._roots(
        again, principal)
    if (new_approved != approved or new_present != present or
            new_plan != plan_digest or new_roots != budget_roots_digest):
        _reject("v11校验期间批准内容或预算根已变化")
    bounded()
    return {"schemaVersion": "business-promotion-budget-v11-owning-preflight-v1",
        "runId": row.id, "attempt": row.attempt, "runVersion": row.version,
        "bindingDigest": row.binding_digest,
        "attestationText": result["attestationText"],
        "attestationSha256": result["attestationSha256"],
        "owningVerificationDigest": result["owningVerificationDigest"],
        "candidateOnly": True, "readyAuthorized": False,
        "databaseCanIndependentlyVerifyProcessAssertions": False}
