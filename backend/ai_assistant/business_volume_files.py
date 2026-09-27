"""Renderer-4 durable volumes. Rebuild incomplete attempts; never mix attempts."""
import base64
from contextlib import ExitStack
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from django.utils import timezone
from business_analysis import volume_delivery
from business_analysis.contracts import AnalysisContractError
from business_analysis.volume_files import VolumeStreams
from . import business_evidence, business_export, business_files as files, business_reports, models as m, reports
from .policy import AiError, authorize_owner, boolean, canonical, current_principal, fields, integer, mutation, passive, uid


RENDERER_VERSION = 6


def create(report_id, body, principal, *, commit=None):
    fields(body, {"deliveryMode", "draft", "expectedPrincipalKey"}, {"deliveryMode", "expectedPrincipalKey"})
    current_principal(principal, admin=True, write=True)
    if body["deliveryMode"] != "volumes" or body["expectedPrincipalKey"] != business_evidence.principal_key(principal):
        raise AiError("当前账号或多卷交付模式不一致", "conflict", 409)
    draft = bool(boolean(body.get("draft", False), "draft"))
    report = reports.get(report_id, principal)
    prepared = files._prepare_screening_binding(report, principal, draft, renderer_version=RENDERER_VERSION)
    fingerprint = prepared.value["bindingDigest"] if prepared is not None else files.binding(report, principal, draft, renderer_version=RENDERER_VERSION)
    with mutation(principal):
        if prepared is not None:
            files._check_screening_binding(prepared, report, principal, draft, renderer_version=RENDERER_VERSION)
        old = m.AiBusinessFileRun.objects.filter(report=report, draft=draft, renderer_version=RENDERER_VERSION, binding_digest=fingerprint).first()
        if old:
            result = {"item": files.mapping(authorize_owner(old, principal)), "replayed": True}
            return commit(result, 200) if commit is not None else result
        if m.AiBusinessFileRun.objects.filter(owner_email=principal.email.lower(), status__in=["queued", "building", "paused"]).count() >= 2:
            raise AiError("未完成文件任务已达到上限", "rate_limited", 429)
        if m.AiBusinessFileRun.objects.count() >= 1000:
            raise AiError("文件任务存储容量已满", "rate_limited", 429)
        row = m.AiBusinessFileRun.objects.create(id=uid("business-file"), report=report, owner_email=principal.email.lower(),
            draft=draft, renderer_version=RENDERER_VERSION, binding_digest=fingerprint)
        result = {"item": files.mapping(row), "replayed": False}
        if commit is not None:
            return commit(result, 200)
    return result


class OutputBudget:
    """Charge aggregate high water, including seeks and overwrites, before writes."""
    def __init__(self, limit):
        self.limit, self.used = limit, 0

    def stream(self, raw):
        return _Output(raw, self)


class _Output:
    def __init__(self, raw, budget):
        self.raw, self.budget, self.high_water = raw, budget, 0

    def write(self, data):
        high = max(self.high_water, self.raw.tell()+len(data))
        growth = high-self.high_water
        if self.budget.used+growth > self.budget.limit:
            raise AiError("多卷临时输出总量超过任务容量", "payload_too_large", 413)
        written = self.raw.write(data)
        if written != len(data):
            raise AiError("多卷临时文件写入不完整", "file_build_failed", 503)
        self.budget.used += growth
        self.high_water = high
        return written

    def __getattr__(self, key):
        return getattr(self.raw, key)


def _contract(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("多卷交付清单或摘要不一致", "conflict", 409) from error


def _compact(row):
    return _contract(volume_delivery.validate, json.loads(row.manifest_json), binding_digest=row.binding_digest,
        attempt=row.attempt, draft=row.draft, renderer_version=row.renderer_version)


def _verify_staged(row, principal, checkpoint):
    compact = _compact(row)
    descriptors = [*compact["files"], compact["manifestFile"]]
    total_parts = 0
    manifest_bytes = bytearray()
    for descriptor in descriptors:
        sha, size, count = hashlib.sha256(), 0, 0
        query = m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt,
            volume_index=descriptor["volumeIndex"], format=descriptor["format"]).order_by("sequence")
        for part in query.iterator(chunk_size=4):
            checkpoint()
            count += 1
            content = bytes(part.content)
            expected_size = min(files.CHUNK_BYTES, descriptor["bytes"]-size)
            if (part.sequence != count or len(content) != expected_size
                    or hashlib.sha256(content).hexdigest() != part.content_digest):
                raise AiError("卷分片顺序、长度或摘要不一致", "conflict", 409)
            size += len(content)
            sha.update(content)
            if descriptor["format"] == "json":
                manifest_bytes.extend(content)
        if (count, size, sha.hexdigest()) != (descriptor["chunkCount"], descriptor["bytes"], descriptor["sha256"]):
            raise AiError("完整卷文件与清单不一致", "conflict", 409)
        total_parts += count
    if m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt).count() != total_parts:
        raise AiError("存在未声明卷分片", "conflict", 409)
    reference = business_reports.bound_reference(json.loads(row.report.snapshot_json), principal)
    full = _contract(volume_delivery.verify_full, compact, bytes(manifest_bytes), binding_digest=row.binding_digest,
        attempt=row.attempt, draft=row.draft, report_id=row.report_id, evidence_digest=reference["sealedDigest"], renderer_version=row.renderer_version)
    snapshot = json.loads(row.report.snapshot_json)
    mapping_keys = {"mappingPlanDigest", "mappingAlgorithmVersion", "mappedTableAlgorithmVersion"}
    screening = snapshot.get("executionProfile") == "business-agent-screening-reference-v1"
    if screening:
        from .business_screening_export import metadata as screening_metadata
        expected_screening = screening_metadata(row.report, principal)
        if any(full.get(key) != value for key,value in expected_screening.items()):
            raise AiError("多卷筛查清单与实际固定发布结果不一致", "conflict", 409)
    elif volume_delivery.SCREENING_KEYS & full.keys():
        raise AiError("旧报告不能附加筛查清单", "conflict", 409)
    if business_reports.integrated.is_snapshot(snapshot) or (screening and "mappingPlan" in snapshot):
        from business_analysis import mapped_results
        if not screening:
            business_reports.integrated.bound(row.report, principal)
        expected = {"mappingPlanDigest":snapshot["mappingPlanDigest"],
            "mappingAlgorithmVersion":snapshot["mappingPlan"]["algorithmVersion"], "mappedTableAlgorithmVersion":mapped_results.ALGORITHM_VERSION}
        if any(full.get(key) != value for key,value in expected.items()):
            raise AiError("多卷商品关联清单与固定计划不一致", "conflict", 409)
    elif mapping_keys & full.keys():
        raise AiError("旧报告不能附加商品关联清单", "conflict", 409)
    if "budgetRef" in snapshot:
        from .business_budget_store import binding_for_report
        fixed = binding_for_report(row.report, principal)
        first = full["volumes"][0]
        if (full.get("budgetPlanDigest") != fixed.reference["planDigest"]
                or first["nativeBudgetSheets"] != 3 or first["offlineBudgetEnabled"] is not True
                or first.get("budgetCalculator", {}).get("planDigest") != fixed.reference["planDigest"]):
            raise AiError("多卷预算清单与报告固定参数不一致", "conflict", 409)
    elif "budgetPlanDigest" in full:
        raise AiError("无预算报告不能发布额外预算清单", "conflict", 409)
    return compact


def _save_file(row, principal, state, descriptor, path, checkpoint):
    sha, size, sequence = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        while True:
            batch = []
            for _ in range(16):
                content = stream.read(files.CHUNK_BYTES)
                if not content:
                    break
                sequence += 1
                size += len(content)
                sha.update(content)
                batch.append(m.AiBusinessVolumeChunk(id=uid("volume-chunk"), run=row, attempt=state["attempt"],
                    volume_index=descriptor["volumeIndex"], format=descriptor["format"], sequence=sequence,
                    content=content, content_digest=hashlib.sha256(content).hexdigest()))
            if not batch:
                break
            if size > descriptor["bytes"] or sequence > descriptor["chunkCount"]:
                raise AiError("临时卷文件超过已核验清单", "conflict", 409)
            checkpoint(force=True)
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                added = sum(len(part.content) for part in batch)
                files._check_quota(saved, added)
                m.AiBusinessVolumeChunk.objects.bulk_create(batch, batch_size=16)
                saved.stored_bytes += added
                saved.progress_json = canonical({"stage": "saving", "volumeIndex": descriptor["volumeIndex"],
                    "format": descriptor["format"], "chunks": sequence, "bytes": size})
                saved.version += 1
                saved.lease_until = timezone.now()+timedelta(seconds=files.LEASE_SECONDS)
                saved.save()
                files.audit(saved, principal, "volume_chunks_saved")
            state["version"] = saved.version
    if (sequence, size, sha.hexdigest()) != (descriptor["chunkCount"], descriptor["bytes"], descriptor["sha256"]):
        raise AiError("持久卷文件未通过完整摘要核验", "conflict", 409)


def build(row, principal, state):
    started, last_check, last_saved = time.monotonic(), 0, 0
    def checkpoint(progress=None, force=False):
        nonlocal last_check, last_saved
        now = time.monotonic()
        if now-started > files.BUILD_SECONDS:
            raise AiError("多卷构建超过本轮时间预算，保留已存分片", "file_build_timeout", 409)
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
                files.audit(saved, principal, "volume_progress")
            state["version"], last_saved = saved.version, now
    if row.manifest_json == "{}":
        # Include every temporary output in the same high-water budget. Leave
        # room for the largest complete trace manifest before opening writers.
        budget = OutputBudget(files.RUN_BYTES-volume_delivery.MAX_MANIFEST_BYTES)
        with TemporaryDirectory(prefix="teruisi-volume-build-") as directory:
            paths = {}
            with business_export.prepare_volumes(row.report, principal, draft=row.draft, checkpoint=checkpoint, renderer_version=row.renderer_version) as prepared:
                with ExitStack() as stack:
                    outputs = []
                    for index in range(1, prepared.plan["volumeCount"]+1):
                        streams = {}
                        for kind in ("html", "xlsx"):
                            path = Path(directory)/f"volume-{index}.{kind}"
                            paths[index, kind] = path
                            streams[kind] = budget.stream(stack.enter_context(path.open("w+b")))
                        outputs.append(VolumeStreams(**streams))
                    full = business_export.build_volumes(prepared, outputs, checkpoint=checkpoint)
            compact, raw_manifest = _contract(volume_delivery.make, full, binding_digest=row.binding_digest,
                attempt=state["attempt"], draft=row.draft, renderer_version=row.renderer_version)
            manifest_path = Path(directory)/"manifest.json"
            manifest_path.write_bytes(raw_manifest)
            paths[0, "json"] = manifest_path
            for descriptor in [*compact["files"], compact["manifestFile"]]:
                _save_file(row, principal, state, descriptor, paths[descriptor["volumeIndex"], descriptor["format"]], checkpoint)
            checkpoint(force=True)
            with mutation(principal):
                saved = files._current(row.id, principal, state)
                saved.manifest_json = canonical(passive(compact, 131072))
                saved.progress_json = canonical({"stage": "verifying"})
                saved.version += 1
                saved.save()
                files.audit(saved, principal, "volumes_staged")
            state["version"] = saved.version
    checkpoint(force=True)
    _verify_staged(files._current(row.id, principal, state), principal, checkpoint)
    checkpoint(force=True)
    current = files._current(row.id, principal, state)
    prepared_binding = files._prepare_screening_binding(current.report, principal, current.draft, renderer_version=current.renderer_version)
    checkpoint(force=True)
    with mutation(principal):
        saved = files._current(row.id, principal, state)
        fingerprint = (files._check_screening_binding(prepared_binding, saved.report, principal, saved.draft, renderer_version=saved.renderer_version)
            if prepared_binding is not None else files.binding(saved.report, principal, saved.draft, renderer_version=saved.renderer_version))
        if fingerprint != saved.binding_digest:
            raise AiError("生成期间报告内容已变化", "conflict", 409)
        saved.status, saved.error_code, saved.progress_json = "ready", "", canonical({"stage": "ready"})
        saved.version += 1
        saved.save()
        files.audit(saved, principal, "volumes_ready")
    return {"status": "ready", "runId": saved.id, "version": saved.version}


def chunk(run_id, volume_index, kind, params, principal):
    fields(params, {"sequence"}, {"sequence"})
    try:
        index, sequence = int(volume_index), int(params["sequence"])
        integer(index, "volumeIndex", lo=0, hi=100)
        integer(sequence, "sequence", hi=512)
    except (TypeError, ValueError) as error:
        raise AiError("卷号或分块序号无效") from error
    if not (index == 0 and kind == "json" or index > 0 and kind in {"html", "xlsx"}):
        raise AiError("卷号与文件格式不一致")
    row = files.get(run_id, principal)
    if row.renderer_version == 11:
        raise AiError("版本11压缩预算文件只允许未发布暂存，下载未开放", "conflict", 409)
    if row.renderer_version == 10:
        from .business_promotion_budget_v10_download import chunk as budget_v10_chunk
        return budget_v10_chunk(row, index, kind, sequence, principal)
    if row.renderer_version == 9:
        from .business_promotion_trial_volume_download import chunk as trial_chunk
        return trial_chunk(row, index, kind, sequence, principal)
    if row.renderer_version == 7:
        from .business_promotion_volume_download import chunk as promotion_chunk
        return promotion_chunk(row, index, kind, sequence, principal)
    if row.renderer_version not in (4, 6) or row.status != "ready":
        raise AiError("完整多卷交付尚未就绪", "conflict", 409)
    if files.binding(row.report, principal, row.draft, renderer_version=row.renderer_version, verify_budget=False) != row.binding_digest:
        raise AiError("报告内容绑定已变化", "conflict", 409)
    manifest = _compact(row)
    descriptor = next((item for item in [*manifest["files"], manifest["manifestFile"]]
        if item["volumeIndex"] == index and item["format"] == kind), None)
    if descriptor is None or sequence > descriptor["chunkCount"]:
        raise AiError("卷文件或分片不存在", "not_found", 404)
    part = m.AiBusinessVolumeChunk.objects.filter(run=row, attempt=row.attempt, volume_index=index, format=kind, sequence=sequence).first()
    if part is None:
        raise AiError("卷文件分片不存在", "not_found", 404)
    content = bytes(part.content)
    if (len(content) != min(files.CHUNK_BYTES, descriptor["bytes"]-(sequence-1)*files.CHUNK_BYTES)
            or hashlib.sha256(content).hexdigest() != part.content_digest):
        raise AiError("卷分片未通过摘要核验", "conflict", 409)
    current_principal(principal, admin=True)
    return {"schemaVersion": "business-volume-chunk-v1", "runId": row.id, "volumeIndex": index, "format": kind,
        "attempt": row.attempt, "sequence": sequence, "bytes": len(content), "sha256": part.content_digest,
        "fileSha256": descriptor["sha256"], "bindingDigest": row.binding_digest, "base64": base64.b64encode(content).decode()}
