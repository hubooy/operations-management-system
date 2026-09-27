"""Renderer-11 durable, unpublished-only promotion budget file staging.

The candidate SQL deliberately forbids renderer-11 ready. Staging reads
all immutable chunks, compares current approved/source/budget roots and the
temporary renderer's semantic bytes, then pauses as staged_unpublished.
"""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import time

from django.db import connection
from django.conf import settings
from django.utils import timezone

from business_analysis import html_slim_payload_v11, volume_delivery
from business_analysis.contracts import AnalysisContractError
from . import business_files as files
from . import business_promotion_approved_content as approved_content
from . import business_promotion_budget as budget_reader
from . import business_promotion_budget_v11_slim_stage_candidate as temporary
from .business_promotion_budget_v10_stage import _same_file, _same_zip
from . import business_promotion_content_contract as content_contract
from . import business_volume_files as volume_files
from . import models as m, reports
from .policy import (AiError, authorize_owner, canonical, cas,
    current_principal, digest, mutation, passive, uid)


VERSION = 11
SCHEMA = "business-promotion-budget-v11-file-staging-v1"


def _conflict(message="预算文件暂存与当前已批准报告不一致"):
    raise AiError(message, "conflict", 409)


def _enabled():
    if getattr(settings, "AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED", False) is not True:
        _conflict("renderer 11 持久暂存默认关闭")


def binding(report, principal, draft=False):
    """One current owning fingerprint, computed outside the short mutation."""
    _enabled()
    if draft is not False or connection.in_atomic_block:
        _conflict("预算文件只允许事务外核验的正式报告")
    actual = reports.get(report.id, principal)
    content = approved_content.build(actual.id, principal)
    snapshot = json.loads(actual.snapshot_json)
    budget_present = actual.budget_plan_id is not None
    if (content["binding"]["reportId"] != actual.id or
            content["binding"]["humanReview"]["status"] != "approved" or
            snapshot.get("executionProfile") != content_contract.PROFILE or
            budget_present != ("budgetRef" in snapshot) or
            budget_present != ("budgetPlanDigest" in content["binding"])):
        _conflict("预算文件缺少当前批准内容或固定预算存在性变化")
    if budget_present:
        owned = budget_reader._roots(actual.id, principal)
        prepared = owned["prepared"]
        if (prepared.reference != snapshot["budgetRef"] or
                prepared.binding["planDigest"] != content["binding"]["budgetPlanDigest"]):
            _conflict("固定预算与同报告批准内容不一致")
        budget_fingerprint = digest([owned["rowDigest"], prepared.plan_json,
            prepared.binding_json, prepared.result_json])
    else:
        budget_fingerprint = "no_fixed_budget"
    return digest([SCHEMA, actual.id, content["dtoDigest"],
        actual.snapshot_json, actual.workflow.input_json,
        budget_fingerprint, False])


def create(report_id, principal):
    """Internal-only queue entry; no public route or renderer registration."""
    _enabled()
    current_principal(principal, admin=True, write=True)
    report = reports.get(report_id, principal)
    fingerprint = binding(report, principal)
    with mutation(principal):
        current_principal(principal, admin=True, write=True)
        latest = reports.get(report_id, principal)
        if (latest.snapshot_json != report.snapshot_json or
                latest.workflow.input_json != report.workflow.input_json or
                latest.budget_plan_id != report.budget_plan_id):
            _conflict("预算文件创建期间固定报告已变化")
        old = m.AiBusinessFileRun.objects.filter(report=latest, draft=False,
            renderer_version=VERSION, binding_digest=fingerprint).first()
        if old:
            return {"item": files.mapping(authorize_owner(old, principal)),
                "replayed": True}
        if m.AiBusinessFileRun.objects.filter(owner_email=principal.email.lower(),
                status__in=["queued", "building", "paused"]).count() >= 2:
            raise AiError("未完成文件任务已达到上限", "rate_limited", 429)
        if m.AiBusinessFileRun.objects.count() >= 1000:
            raise AiError("文件任务存储容量已满", "rate_limited", 429)
        row = m.AiBusinessFileRun.objects.create(id=uid("promotion-budget-file"),
            report=latest, owner_email=principal.email.lower(), draft=False,
            renderer_version=VERSION, binding_digest=fingerprint)
        files.audit(row, principal, "promotion_budget_v11_queued")
        return {"item": files.mapping(row), "replayed": False}


def control(run_id, action, expected_version, principal):
    """Internal pause/resume/rebuild/cancel; never publishes."""
    _enabled()
    if action not in {"pause", "resume", "rebuild", "cancel"}:
        raise AiError("预算暂存动作无效")
    candidate = files.get(run_id, principal)
    if candidate.renderer_version != VERSION:
        _conflict("此控制器只管理版本11暂存")
    cas(candidate, expected_version)
    fingerprint = binding(candidate.report, principal) if action in {"resume", "rebuild"} else None
    with mutation(principal):
        row = files.get(run_id, principal)
        cas(row, expected_version)
        if row.status in {"ready", "cancelled"}:
            _conflict("预算暂存任务已终止")
        if action in {"resume", "rebuild"}:
            if row.status != "paused" or fingerprint != row.binding_digest:
                _conflict("暂停期间批准内容、来源或固定预算已变化")
            if action == "rebuild": row.manifest_json = "{}"
            if row.manifest_json == "{}" and row.attempt >= 5:
                _conflict("预算暂存重建已达到五次上限")
        row.status = {"pause": "paused", "resume": "queued",
            "rebuild": "queued", "cancel": "cancelled"}[action]
        row.error_code = ""
        row.lease_until = timezone.now()
        row.version += 1
        row.save()
        files.audit(row, principal, "promotion_budget_v11_" + action)
        return {"item": files.mapping(row)}


def _semantic(full):
    """Ignore only ZIP container bytes, then compare all source/row proofs."""
    value = json.loads(canonical(full))
    value.pop("manifestDigest")
    for item in value["volumes"]:
        item.pop("files")
    value["promotionSlimProof"].pop("fileBindingsDigest")
    value["promotionSlimProof"].pop("proofDigest")
    return value


def _verify_staged(row, principal, checkpoint, *, file_evidence=None):
    """Verify all stored chunks and re-render exact current semantic content."""
    _enabled()
    try:
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=VERSION)
    except (ValueError, TypeError, KeyError, AnalysisContractError) as error:
        raise AiError("预算暂存紧凑清单无效", "conflict", 409) from error
    descriptors = [*compact["files"], compact["manifestFile"]]
    total_parts = 0
    with TemporaryDirectory(prefix="teruisi-budget-stage-verify-") as folder:
        paths = {}
        for descriptor in descriptors:
            key = descriptor["volumeIndex"], descriptor["format"]
            path = Path(folder) / f"{key[0]:03}.{key[1]}"
            paths[key] = path
            sha, size, count = hashlib.sha256(), 0, 0
            query = m.AiBusinessVolumeChunk.objects.filter(run=row,
                attempt=row.attempt, volume_index=key[0],
                format=key[1]).order_by("sequence")
            with path.open("wb") as target:
                for part in query.iterator(chunk_size=4):
                    checkpoint()
                    count += 1
                    raw = bytes(part.content)
                    expected_length = min(files.CHUNK_BYTES,
                        descriptor["bytes"] - size)
                    if (part.sequence != count or len(raw) != expected_length or
                            hashlib.sha256(raw).hexdigest() != part.content_digest):
                        _conflict("预算卷分块序号、大小或内容摘要不符")
                    size += len(raw)
                    sha.update(raw)
                    if target.write(raw) != len(raw):
                        _conflict("预算卷重建字节不完整")
            if (count, size, sha.hexdigest()) != (descriptor["chunkCount"],
                    descriptor["bytes"], descriptor["sha256"]):
                _conflict("预算卷文件与紧凑清单不一致")
            total_parts += count
        if m.AiBusinessVolumeChunk.objects.filter(run=row,
                attempt=row.attempt).count() != total_parts:
            _conflict("预算暂存包含未声明的分块")
        current = approved_content.build(row.report_id, principal)
        try:
            full = volume_delivery.verify_full(compact,
                paths[0, "json"].read_bytes(), binding_digest=row.binding_digest,
                attempt=row.attempt, draft=False, report_id=row.report_id,
                evidence_digest=current["binding"]["sealedDigest"],
                renderer_version=VERSION)
        except AnalysisContractError as error:
            raise AiError("预算完整清单或文件证明无效", "conflict", 409) from error
        try:
            for volume in full["volumes"]:
                checkpoint()
                html_slim_payload_v11.verify_file(
                    paths[volume["volumeIndex"], "html"], volume,
                    checkpoint=checkpoint)
        except AnalysisContractError as error:
            raise AiError("版本11持久HTML压缩行或完整摘要无效", "conflict", 409) from error
        with temporary.open_unpublished(row.report_id, principal,
                checkpoint=checkpoint) as fresh:
            fresh_receipt = fresh.manifest
            fresh_full = volume_delivery.verify_full(
                fresh_receipt["compactManifest"],
                fresh.path(0, "json").read_bytes(),
                binding_digest=fresh_receipt["compactManifest"]["bindingDigest"],
                attempt=1, draft=False, report_id=row.report_id,
                evidence_digest=current["binding"]["sealedDigest"],
                renderer_version=VERSION)
            if (_semantic(full) != _semantic(fresh_full) or
                    fresh_receipt["promotionBudgetProof"] != full["promotionBudgetProof"] or
                    fresh_receipt["promotionTrialProof"] != full["promotionTrialProof"] or
                    fresh_receipt["promotionSlimProof"]["volumePayloadDigest"] !=
                        full["promotionSlimProof"]["volumePayloadDigest"]):
                _conflict("预算暂存与当前批准内容、预算或封存来源不同")
            for index in range(1, compact["volumeCount"] + 1):
                _same_file(paths[index, "html"],
                    fresh.path(index, "html"), checkpoint)
                _same_zip(paths[index, "xlsx"],
                    fresh.path(index, "xlsx"), checkpoint)
        if file_evidence is not None:
            file_evidence(paths, full, compact, checkpoint)
        return compact


def build(row, principal, state):
    """Called only by the existing file builder advisory-lock tick."""
    _enabled()
    if row.renderer_version != VERSION or row.draft:
        _conflict("预算暂存版本或草稿状态无效")
    started, last_check, last_saved = time.monotonic(), 0, 0

    def checkpoint(progress=None, force=False):
        nonlocal last_check, last_saved
        now = time.monotonic()
        if now - started > files.BUILD_SECONDS:
            raise AiError("预算暂存超过原600秒构建预算", "file_build_timeout", 409)
        if not force and now - last_check < 1:
            return
        current_principal(principal, admin=True)
        files._current(row.id, principal, state)
        last_check = now
        if progress is not None and now - last_saved >= 5:
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                saved.progress_json = canonical(passive(progress, 4096))
                saved.lease_until = timezone.now() + timedelta(seconds=files.LEASE_SECONDS)
                saved.version += 1
                saved.save()
                files.audit(saved, principal, "promotion_budget_v11_progress")
            state["version"], last_saved = saved.version, now

    if row.manifest_json == "{}":
        with temporary.open_unpublished(row.report_id, principal,
                checkpoint=checkpoint) as prepared:
            temp = prepared.manifest
            source_raw = prepared.path(0, "json").read_bytes()
            try:
                full = volume_delivery.verify_full(temp["compactManifest"],
                    source_raw,
                    binding_digest=temp["compactManifest"]["bindingDigest"],
                    attempt=1, draft=False, report_id=row.report_id,
                    evidence_digest=json.loads(row.report.snapshot_json)["sealedDigest"],
                    renderer_version=VERSION)
                compact, raw_manifest = volume_delivery.make(full,
                    binding_digest=row.binding_digest,
                    attempt=state["attempt"], draft=False,
                    renderer_version=VERSION)
            except AnalysisContractError as error:
                raise AiError("预算临时卷不能绑定当前持久尝试", "conflict", 409) from error
            if raw_manifest != source_raw:
                _conflict("临时卷清单与持久绑定规范字节不同")
            for descriptor in [*compact["files"], compact["manifestFile"]]:
                checkpoint(force=True)
                volume_files._save_file(row, principal, state,
                    descriptor, prepared.path(descriptor["volumeIndex"],
                        descriptor["format"]), checkpoint)
            checkpoint(force=True)
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                saved.manifest_json = canonical(passive(compact, 131072))
                saved.progress_json = canonical({"stage": "verifying_unpublished"})
                saved.version += 1
                saved.save()
                files.audit(saved, principal, "promotion_budget_v11_staged")
            state["version"] = saved.version
    checkpoint(force=True)
    _verify_staged(files._current(row.id, principal, state), principal, checkpoint)
    checkpoint(force=True)
    if binding(row.report, principal) != row.binding_digest:
        _conflict("暂存完成前批准内容或固定预算已变化")
    with mutation(principal):
        saved = files._current(row.id, principal, state)
        saved.status = "paused"
        saved.error_code = "renderer_unpublished"
        saved.progress_json = canonical({"stage": "staged_unpublished",
            "attempt": saved.attempt})
        saved.version += 1
        saved.save()
        files.audit(saved, principal, "promotion_budget_v11_staged_unpublished")
    return {"status": "staged_unpublished", "runId": saved.id,
        "version": saved.version, "attempt": saved.attempt}
