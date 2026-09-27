"""Opt-in renderer-9 durable staging and verified publication.

Only an already approved promotion report may issue a queued file run. A
complete attempt stores immutable volume chunks and compact receipt, then
pauses as staged_unpublished. The completed workflow may then publish through
the owning full-byte and approval verifier.
"""
from datetime import timedelta
import hashlib
import json
import time

from django.db import connection
from django.utils import timezone

from business_analysis import promotion_trial_table_schema, volume_delivery, volume_files as pure_volume_files, volume_plan
from business_analysis.contracts import AnalysisContractError
from . import business_files as files, business_promotion_approved_content as approved_content
from . import business_promotion_content_contract as content_contract
from business_analysis import promotion_action_tables
from . import business_promotion_file_proof as file_proof
from . import business_promotion_trial_volumes as temporary
from . import business_promotion_file_tables as file_tables
from . import business_promotion_formal_export as formal_pair
from . import business_promotion_volume_stage as v7_stage
from . import business_volume_files as volume_files
from . import models as m, reports
from .policy import AiError, authorize_owner, canonical, cas, current_principal, digest, mutation, passive, uid


VERSION = 9
SCHEMA = "business-promotion-trial-file-staging-v1"


def _conflict(message="词货文件暂存与已批准报告不一致"):
    raise AiError(message, "conflict", 409)


def binding(report, principal, draft=False):
    """Full owning proof outside the short mutation; never a public token."""
    if draft is not False or connection.in_atomic_block:
        _conflict("词货文件仅支持事务外完整核验的正式批准报告")
    actual = reports.get(report.id, principal)
    value = approved_content.build(actual.id, principal)
    if (value["binding"]["reportId"] != actual.id
            or value["binding"]["humanReview"]["status"] != "approved"
            or json.loads(actual.snapshot_json).get("executionProfile") != content_contract.PROFILE):
        _conflict("词货文件缺少当前五角色与人审固定根")
    return digest([SCHEMA, actual.id, value["dtoDigest"], actual.snapshot_json,
        actual.workflow.input_json, False])


def create(report_id, principal, *, commit=None):
    """Queue a formal run after full approval and commit the signed receipt."""
    current_principal(principal, admin=True, write=True)
    report = reports.get(report_id, principal)
    fingerprint = binding(report, principal)
    with mutation(principal):
        current_principal(principal, admin=True, write=True)
        latest = reports.get(report_id, principal)
        if (latest.snapshot_json != report.snapshot_json
                or latest.workflow.input_json != report.workflow.input_json):
            _conflict("文件创建期间报告固定根已变化")
        old = m.AiBusinessFileRun.objects.filter(report=latest, draft=False,
            renderer_version=VERSION, binding_digest=fingerprint).first()
        if old:
            result = {"item": files.mapping(authorize_owner(old, principal)), "replayed": True}
            return commit(result, 200) if commit is not None else result
        if m.AiBusinessFileRun.objects.filter(owner_email=principal.email.lower(),
                status__in=["queued", "building", "paused"]).count() >= 2:
            raise AiError("未完成文件任务已达到上限", "rate_limited", 429)
        if m.AiBusinessFileRun.objects.count() >= 1000:
            raise AiError("文件任务存储容量已满", "rate_limited", 429)
        row = m.AiBusinessFileRun.objects.create(id=uid("promotion-file"), report=latest,
            owner_email=principal.email.lower(), draft=False, renderer_version=VERSION,
            binding_digest=fingerprint)
        files.audit(row, principal, "promotion_queued")
        result = {"item": files.mapping(row), "replayed": False}
        return commit(result, 200) if commit is not None else result


def control(run_id, action, expected_version, principal, *, commit=None):
    """Internal pause/resume/rebuild/cancel with CAS and attempt isolation."""
    if action not in {"pause", "resume", "rebuild", "cancel"}:
        raise AiError("词货暂存动作无效")
    candidate = files.get(run_id, principal)
    if candidate.renderer_version != VERSION:
        _conflict("此控制器仅用于词货暂存任务")
    cas(candidate, expected_version)
    fingerprint = binding(candidate.report, principal) if action in {"resume", "rebuild"} else None
    with mutation(principal):
        row = files.get(run_id, principal)
        cas(row, expected_version)
        if row.status in {"ready", "cancelled"}:
            _conflict("词货文件任务已终止")
        if action in {"resume", "rebuild"}:
            if row.status != "paused" or fingerprint != row.binding_digest:
                _conflict("已暂停报告的批准内容或人审已变化")
            if action == "rebuild": row.manifest_json = "{}"
            if row.manifest_json == "{}" and row.attempt >= 5:
                _conflict("词货暂存重建已达到五次上限")
        row.status = {"pause": "paused", "resume": "queued",
            "rebuild": "queued", "cancel": "cancelled"}[action]
        row.error_code = ""
        row.lease_until = timezone.now()
        row.version += 1
        row.save()
        files.audit(row, principal, "promotion_"+action)
        result = {"item": files.mapping(row)}
        return commit(result, 200) if commit is not None else result


def _verify_staged(row, principal, checkpoint):
    """Read every persisted byte and compare to current completed roots."""
    try:
        compact = volume_delivery.validate(json.loads(row.manifest_json),
            binding_digest=row.binding_digest, attempt=row.attempt,
            draft=False, renderer_version=VERSION)
    except (ValueError, TypeError, KeyError, AnalysisContractError) as error:
        raise AiError("词货暂存紧凑清单无效", "conflict", 409) from error
    descriptors = [*compact["files"], compact["manifestFile"]]
    full_raw = bytearray()
    total_parts = 0
    for descriptor in descriptors:
        sha, size, count = hashlib.sha256(), 0, 0
        query = m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt,
            volume_index=descriptor["volumeIndex"], format=descriptor["format"]).order_by("sequence")
        for part in query.iterator(chunk_size=4):
            checkpoint()
            count += 1
            raw = bytes(part.content)
            expected_length = min(files.CHUNK_BYTES, descriptor["bytes"]-size)
            if (part.sequence != count or len(raw) != expected_length
                    or hashlib.sha256(raw).hexdigest() != part.content_digest):
                _conflict("词货卷分块序号、大小或摘要不符")
            size += len(raw); sha.update(raw)
            if descriptor["format"] == "json": full_raw.extend(raw)
        if (count, size, sha.hexdigest()) != (descriptor["chunkCount"],
                descriptor["bytes"], descriptor["sha256"]):
            _conflict("词货卷文件与紧凑清单不一致")
        total_parts += count
    if m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt).count() != total_parts:
        _conflict("词货暂存存在未声明分片")
    actual = approved_content.build(row.report_id, principal)
    snapshot = json.loads(row.report.snapshot_json)
    try:
        full = volume_delivery.verify_full(compact, bytes(full_raw),
            binding_digest=row.binding_digest, attempt=row.attempt, draft=False,
            report_id=row.report_id, evidence_digest=actual["binding"]["sealedDigest"],
            renderer_version=VERSION)
        with file_tables.open_tables(row.report_id, principal, draft=False,
                checkpoint=checkpoint) as (metadata, promotion):
            fixed, completed = metadata.value, metadata.approved_content
            if (type(completed) is not content_contract.PreparedContent
                    or completed.value["dtoDigest"] != actual["dtoDigest"]):
                _conflict("试用卷与当前已批准五角色内容不同")
            proof = file_proof.prepare(completed,
                fixed["sourceSummary"]["sourceMaterials"]).value
            with formal_pair._source_tables(row.report_id, principal,
                    fixed["reportBinding"], checkpoint, trial=True) as (sealed, source):
                action = promotion_action_tables.project(completed.value)
                action_sha = hashlib.sha256()
                for action_row in action.rows:
                    action_sha.update((canonical(list(action_row))+"\n").encode("utf-8"))
                base = formal_pair._tables(completed.value, promotion)
                expected_tables = (*base[:-2], action, *sealed, *base[-2:])
                request = pure_volume_files.request_for(expected_tables,
                    report_id=row.report_id,
                    evidence_digest=actual["binding"]["sealedDigest"],
                    renderer_version=VERSION)
                descriptor_keys = ("key", "title", "rowCount", "columnCount")
                if [{key: item[key] for key in descriptor_keys}
                        for item in request["tables"]] != [
                        {key: item[key] for key in descriptor_keys}
                        for item in full["tables"]]:
                    _conflict("试用卷完整表目录与当前来源不同")
                planned = volume_plan.build(request)
                if (planned["sourceDescriptorDigest"] != full["sourceDescriptorDigest"]
                        or planned["planDigest"] != full["planDigest"]):
                    _conflict("试用卷分卷计划与当前完整表描述不同")
                for table, recorded in zip(expected_tables, full["tables"]):
                    count, actual_hash = 0, hashlib.sha256()
                    for source_row in table.rows:
                        count += 1
                        if count % 1024 == 0: checkpoint()
                        if not isinstance(source_row, (list, tuple)) or len(source_row) != len(table.columns):
                            _conflict("试用卷来源表行宽或形状已变化")
                        values = [str(value) if type(value) is int and abs(value) >= 10**15
                            else value for value in source_row]
                        actual_hash.update((canonical(values)+"\n").encode("utf-8"))
                    if (count, actual_hash.hexdigest()) != (recorded["rowCount"],
                            recorded["rowDigest"]):
                        _conflict("试用卷表行与当前封存来源不同")
                trial = {"schemaVersion": "business-promotion-trial-file-proof-v2",
                    "rendererVersion": VERSION, "reportId": row.report_id,
                    "contentDtoDigest": completed.value["dtoDigest"],
                    "humanReviewDigest": proof["humanReviewDigest"],
                    "promotionFileProofDigest": proof["proofDigest"],
                    "sealedSourcesDigest": source["sourcesDigest"],
                    "sourceDescriptorDigest": full["sourceDescriptorDigest"],
                    "tableSchemaDigest": promotion_trial_table_schema.digest_tables(expected_tables),
                    "actionTableKey": action.key, "actionRowCount": action.row_count,
                    "actionRowDigest": action_sha.hexdigest(),
                    "scopeTableKeys": ["promotion-trial-source-scope", "promotion-trial-boundaries"],
                    "promotionTableKeys": [table.key for table in promotion],
                    "budgetDelivered": False}
                trial["proofDigest"] = digest(trial)
    except (AnalysisContractError, file_proof.FileProofError,
            content_contract.ContentContractError) as error:
        raise AiError("词货暂存完整证明与当前封存来源不一致", "conflict", 409) from error
    if (full.get("promotionFileProof") != proof
            or full.get("promotionTrialProof") != trial
            or full["reportId"] != row.report_id
            or full["evidenceDigest"] != snapshot["sealedDigest"]):
        _conflict("词货暂存清单跨报告、证据或人审")
    return compact


def build(row, principal, state):
    """Called only by the file builder's existing advisory-lock tick."""
    if row.renderer_version != VERSION or row.draft:
        _conflict("词货暂存版本或草稿状态无效")
    started, last_check, last_saved = time.monotonic(), 0, 0
    def checkpoint(progress=None, force=False):
        nonlocal last_check, last_saved
        now = time.monotonic()
        if now-started > files.BUILD_SECONDS:
            raise AiError("词货暂存超过原600秒构建预算", "file_build_timeout", 409)
        if not force and now-last_check < 1:
            return
        current_principal(principal, admin=True)
        files._current(row.id, principal, state)
        last_check = now
        if progress is not None and now-last_saved >= 5:
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                saved.progress_json = canonical(passive(progress, 4096))
                saved.lease_until = timezone.now()+timedelta(seconds=files.LEASE_SECONDS)
                saved.version += 1
                saved.save()
                files.audit(saved, principal, "promotion_progress")
            state["version"], last_saved = saved.version, now
    if row.manifest_json == "{}":
        with temporary.open_volumes(row.report_id, principal, checkpoint=checkpoint) as prepared:
            temp = prepared.manifest
            source_raw = prepared.path(0, "json").read_bytes()
            try:
                full = volume_delivery.verify_full(temp["compactManifest"], source_raw,
                    binding_digest=temp["compactManifest"]["bindingDigest"],
                    attempt=1, draft=False, report_id=row.report_id,
                    evidence_digest=json.loads(row.report.snapshot_json)["sealedDigest"],
                    renderer_version=VERSION)
                compact, raw_manifest = volume_delivery.make(full,
                    binding_digest=row.binding_digest, attempt=state["attempt"],
                    draft=False, renderer_version=VERSION)
            except AnalysisContractError as error:
                raise AiError("词货临时卷不能绑定当前持久尝试", "conflict", 409) from error
            if raw_manifest != source_raw:
                _conflict("临时多卷清单与持久绑定规范字节不同")
            for descriptor in [*compact["files"], compact["manifestFile"]]:
                checkpoint(force=True)
                volume_files._save_file(row, principal, state, descriptor,
                    prepared.path(descriptor["volumeIndex"], descriptor["format"]), checkpoint)
            checkpoint(force=True)
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                saved.manifest_json = canonical(passive(compact, 131072))
                saved.progress_json = canonical({"stage": "verifying_unpublished"})
                saved.version += 1
                saved.save()
                files.audit(saved, principal, "promotion_staged")
            state["version"] = saved.version
    checkpoint(force=True)
    _verify_staged(files._current(row.id, principal, state), principal, checkpoint)
    checkpoint(force=True)
    if binding(row.report, principal) != row.binding_digest:
        _conflict("暂存完成前实际批准内容发生变化")
    checkpoint(force=True)
    with mutation(principal):
        saved = files._current(row.id, principal, state)
        saved.status = "paused"
        saved.error_code = "renderer_unpublished"
        saved.progress_json = canonical({"stage": "staged_unpublished", "attempt": saved.attempt})
        saved.version += 1
        saved.save()
        files.audit(saved, principal, "promotion_staged_unpublished")
    return {"status": "staged_unpublished", "runId": saved.id,
        "version": saved.version, "attempt": saved.attempt}


_publication_fence = v7_stage._publication_fence


def publish(run_id, expected_version, principal):
    """Internal verified paused→ready CAS before bounded public download."""
    if connection.in_atomic_block:
        raise AiError("词货完整发布验证须在最外层事务之外", "invalid_request", 400)
    row = files.get(run_id, principal)
    cas(row, expected_version)
    if (row.renderer_version != VERSION or row.draft or row.status != "paused"
            or row.error_code != "renderer_unpublished" or row.manifest_json == "{}"
            or json.loads(row.progress_json).get("stage") != "staged_unpublished"):
        _conflict("只有已完整暂存且尚未发布的词货文件可以进入发布核验")
    if row.report.workflow.status != "completed":
        _conflict("正式文件发布须等待六节点工作流完成并保存总输出")
    fingerprint = binding(row.report, principal)
    if fingerprint != row.binding_digest:
        _conflict("词货文件绑定与当前人审结果不同")
    before = _publication_fence(row.report_id, principal)
    compact = _verify_staged(row, principal, lambda *args, **kwargs: None)
    if (_publication_fence(row.report_id, principal) != before
            or binding(row.report, principal) != row.binding_digest):
        _conflict("词货文件完整核验期间报告、人审或账本变化")
    with mutation(principal):
        saved = files.get(run_id, principal)
        cas(saved, expected_version)
        if (saved.renderer_version != VERSION or saved.draft or saved.status != "paused"
                or saved.error_code != "renderer_unpublished"
                or saved.attempt != row.attempt or saved.manifest_json != row.manifest_json
                or saved.stored_bytes != row.stored_bytes
                or json.loads(saved.progress_json).get("stage") != "staged_unpublished"
                or _publication_fence(saved.report_id, principal) != before):
            _conflict("词货文件发布事务内固定状态变化")
        try:
            checked = volume_delivery.validate(json.loads(saved.manifest_json),
                binding_digest=saved.binding_digest, attempt=saved.attempt,
                draft=False, renderer_version=VERSION)
        except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
            raise AiError("词货发布事务内紧凑清单无效", "conflict", 409) from error
        if checked != compact:
            _conflict("词货文件发布事务内紧凑清单变化")
        saved.status = "ready"
        saved.error_code = ""
        saved.progress_json = canonical({"stage": "ready",
            "publicationFenceDigest": before,
            "manifestFileSha256": compact["manifestFile"]["sha256"]})
        saved.version += 1
        saved.save()
        files.audit(saved, principal, "promotion_ready")
    return {"item": files.mapping(saved)}
