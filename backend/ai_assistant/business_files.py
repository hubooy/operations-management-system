"""Durable, owner-bound paired file delivery with isolated resumable attempts."""
import base64
from datetime import timedelta
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from django.conf import settings
from django.db import connection
from django.db.models import Q, Sum
from django.utils import timezone

from . import business_evidence, business_export, business_reports, models as m, reports, workflows
from .control_models import AiMutationAudit
from .policy import AiError, authorize_owner, boolean, canonical, cas, current_principal, digest, fields, identifier, integer, mutation, passive, revision, uid

CHUNK_BYTES = 512 * 1024
RUN_BYTES = 1024 * 1024 * 1024
OWNER_BYTES = 2 * RUN_BYTES
GLOBAL_BYTES = 8 * RUN_BYTES
LEASE_SECONDS = 650
BUILD_SECONDS = 600
RENDERER_VERSION = 5


def binding(report, principal, draft, *, renderer_version=RENDERER_VERSION, verify_budget=True):
    authorize_owner(report, principal)
    snapshot = json.loads(report.snapshot_json)
    if (snapshot.get("executionProfile") == "business-agent-screening-reference-v1"
            and verify_budget and connection.in_atomic_block):
        raise AiError("筛查文件完整核验须在最外层事务之外", "conflict", 409)
    if business_reports.is_v2_snapshot(snapshot) and renderer_version not in (4, 6):
        raise AiError("v2证据文件交付尚未接入，请保留分析结果", "conflict", 409)
    if renderer_version in (4, 6):
        if not business_reports.is_v2_snapshot(snapshot):
            raise AiError("多卷文件须使用v2经营报告", "conflict", 409)
        business_reports.bound_reference(snapshot, principal)
        content = None
        if (business_reports.integrated.is_snapshot(snapshot) or snapshot.get("executionProfile") == "business-agent-screening-reference-v1") and verify_budget:
            # The shared flag means full validation at create/resume/publish;
            # immutable chunk reads keep the lightweight reference-only path.
            content = business_reports.content(report, principal) if draft else business_reports.validate_review(report, principal)
        if "budgetRef" in snapshot:
            from . import business_budget_store
            fixed = business_budget_store.binding_for_report(report, principal)
            if snapshot.get("budgetRef") != fixed.reference:
                raise AiError("文件预算引用与固定参数不一致", "conflict", 409)
            if verify_budget:
                # Creation, resume and publication re-read the selected facts;
                # individual immutable chunks use only the bound parameter row.
                if content is None:
                    content = business_reports.content(report, principal) if draft else business_reports.validate_review(report, principal)
                resolved = content.get("budget")
                if resolved is None or resolved["planDigest"] != fixed.reference["planDigest"]:
                    raise AiError("文件预算未通过完整重算", "conflict", 409)
    if snapshot.get("schemaVersion") != business_reports.SCHEMA or report.workflow.dry_run:
        raise AiError("只有已分析的经营报告可以构建文件", "conflict", 409)
    if not draft and report.workflow.status != "completed":
        raise AiError("正式文件须先通过人工复核", "conflict", 409)
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    if evidence.status != "sealed" or evidence.version != snapshot["evidenceVersion"] or digest(evidence.plan_json) != snapshot["evidencePlanDigest"]:
        raise AiError("报告证据快照已变化", "conflict", 409)
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id, status="completed").order_by("node_key").values_list("node_key", "output_json"))
    if not {"commerce", "promotion", "market_b2b", "independent_review", "report"} <= {key for key, _ in nodes}:
        raise AiError("专业分析或复核尚未完成", "conflict", 409)
    # Human review may complete after a draft was built. It does not change the
    # draft's professional contents or make that draft a formally approved file.
    values = [snapshot, [(key, output) for key, output in nodes if key != "human_review"], draft]
    if snapshot.get("executionProfile") == "business-agent-screening-reference-v1":
        from .business_screening_export import binding as screening_binding
        values.append(screening_binding(report, principal))
    return digest(values)


_SCREENING_FILE_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class _ScreeningFileBinding:
    _raw: str
    _digest: str

    def __init__(self, token, value):
        if token is not _SCREENING_FILE_TOKEN:
            raise AiError("不能从JSON恢复筛查文件核验", "conflict", 409)
        raw = canonical(passive(value, 32768))
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_digest", digest(raw))

    @property
    def value(self):
        if type(self._raw) is not str or len(self._raw.encode()) > 32768 or digest(self._raw) != self._digest:
            raise AiError("筛查文件核验对象损坏", "conflict", 409)
        return json.loads(self._raw)


def _prepare_screening_binding(report, principal, draft, *, renderer_version=4):
    """New profile only: full content/number/ledger validation outside mutation."""
    if json.loads(report.snapshot_json).get("executionProfile") != "business-agent-screening-reference-v1":
        return None
    if connection.in_atomic_block:
        raise AiError("筛查文件完整核验须在最外层事务之外", "conflict", 409)
    from .business_screening_content_fence import fence
    actual = reports.get(report.id, principal)
    before = fence(actual, principal)
    fingerprint = binding(actual, principal, draft, renderer_version=renderer_version)
    if canonical(fence(actual, principal)) != canonical(before):
        raise AiError("筛查文件核验期间读取账本发生变化", "conflict", 409)
    return _ScreeningFileBinding(_SCREENING_FILE_TOKEN, {"reportId":actual.id,
        "ownerEmail":principal.email.lower(),"role":principal.role,"scope":principal.scope,
        "draft":draft,"rendererVersion":renderer_version,"bindingDigest":fingerprint,"ledgerFence":before})


def _check_screening_binding(prepared, report, principal, draft, *, renderer_version=4):
    """Only live metadata and bounded ledger hashes; never scans sealed facts."""
    from .business_screening_content_fence import fence
    if type(prepared) is not _ScreeningFileBinding:
        raise AiError("缺少实际筛查文件核验", "conflict", 409)
    value = prepared.value
    current_principal(principal, admin=True, write=True)
    actual = reports.get(report.id, principal)
    expected = {"reportId":actual.id,"ownerEmail":principal.email.lower(),"role":principal.role,
        "scope":principal.scope,"draft":draft,"rendererVersion":renderer_version,
        "bindingDigest":binding(actual, principal, draft, renderer_version=renderer_version, verify_budget=False),
        "ledgerFence":fence(actual, principal)}
    if canonical(expected) != canonical(value):
        raise AiError("筛查文件内容或实际读取账本已变化", "conflict", 409)
    return value["bindingDigest"]


def mapping(row, *, include_manifest=True):
    return {"id": row.id, "reportId": row.report_id, "draft": row.draft, "rendererVersion": row.renderer_version,
        "bindingDigest": row.binding_digest, "status": row.status, "version": row.version, "attempt": row.attempt,
        "storedBytes": row.stored_bytes, "progress": json.loads(row.progress_json), "errorCode": row.error_code,
        "createdAt": row.created_at.isoformat(), "manifest": json.loads(row.manifest_json) if include_manifest and row.status == "ready" else None}


def get(run_id, principal):
    current_principal(principal, admin=True)
    row = m.AiBusinessFileRun.objects.select_related("report__workflow").filter(pk=identifier(run_id)).first()
    if row is None:
        raise AiError("报告文件任务不存在", "not_found", 404)
    return authorize_owner(row, principal)


def listing(report_id, principal):
    reports.get(report_id, principal)
    return {"items": [mapping(row, include_manifest=False) for row in m.AiBusinessFileRun.objects.filter(report_id=report_id, owner_email=principal.email.lower()).defer("manifest_json").order_by("-created_at")[:30]]}


def create(report_id, body, principal, *, commit=None):
    if body.get("deliveryMode") == "promotionTrialVolumes":
        fields(body, {"deliveryMode", "draft", "expectedPrincipalKey"},
            {"deliveryMode", "draft", "expectedPrincipalKey"})
        if (boolean(body["draft"], "draft") != 0
                or body["expectedPrincipalKey"] != business_evidence.principal_key(principal)):
            raise AiError("推广试用多卷须绑定当前账号和正式报告", "conflict", 409)
        from .business_promotion_trial_volume_stage import create as trial_create
        return trial_create(report_id, principal, commit=commit)
    if body.get("deliveryMode") == "volumes":
        report = reports.get(report_id, principal)
        if json.loads(report.snapshot_json).get("executionProfile") == "business-agent-screening-promotion-reference-v1":
            fields(body, {"deliveryMode", "draft", "expectedPrincipalKey"},
                {"deliveryMode", "draft", "expectedPrincipalKey"})
            if (body["deliveryMode"] != "volumes"
                    or boolean(body["draft"], "draft") != 0
                    or body["expectedPrincipalKey"] != business_evidence.principal_key(principal)
                    or report.workflow.status != "completed"):
                raise AiError("词货正式多卷文件须绑定当前账号及已完成人审报告", "conflict", 409)
            from .business_promotion_volume_stage import create as promotion_create
            return promotion_create(report_id, principal, commit=commit)
        from .business_volume_files import create as create_volumes
        return create_volumes(report_id, body, principal, commit=commit)
    fields(body, {"draft"})
    draft = bool(boolean(body.get("draft", False), "draft"))
    current_principal(principal, admin=True, write=True)
    report = reports.get(report_id, principal)
    fingerprint = binding(report, principal, draft)
    with mutation(principal):
        old = m.AiBusinessFileRun.objects.filter(report=report, draft=draft, renderer_version=RENDERER_VERSION, binding_digest=fingerprint).first()
        if old:
            return {"item": mapping(authorize_owner(old, principal)), "replayed": True}
        if m.AiBusinessFileRun.objects.filter(owner_email=principal.email.lower(), status__in=["queued", "building", "paused"]).count() >= 2:
            raise AiError("未完成文件任务已达到上限", "rate_limited", 429)
        if m.AiBusinessFileRun.objects.count() >= 1000:
            raise AiError("文件任务存储容量已满", "rate_limited", 429)
        row = m.AiBusinessFileRun.objects.create(id=uid("business-file"), report=report, owner_email=principal.email.lower(), draft=draft, renderer_version=RENDERER_VERSION, binding_digest=fingerprint)
    return {"item": mapping(row), "replayed": False}


def control(run_id, body, principal, *, commit=None):
    fields(body, {"expectedVersion", "action"}, {"expectedVersion", "action"})
    if body["action"] not in ("pause", "resume", "rebuild", "cancel"):
        raise AiError("文件任务控制动作无效")
    renderer = get(run_id, principal).renderer_version
    if renderer == 10:
        raise AiError("版本10预算暂存控制尚未开放公开入口", "conflict", 409)
    if renderer == 11:
        raise AiError("版本11压缩预算暂存控制尚未开放公开入口", "conflict", 409)
    prepared = None
    if body["action"] in {"resume", "rebuild"}:
        candidate = get(run_id, principal)
        if candidate.renderer_version in (7, 9):
            if candidate.renderer_version == 7:
                from .business_promotion_volume_stage import control as promotion_control
            else:
                from .business_promotion_trial_volume_stage import control as promotion_control
            return promotion_control(run_id, body["action"], body["expectedVersion"], principal,
                commit=commit)
        if json.loads(candidate.report.snapshot_json).get("executionProfile") == "business-agent-screening-reference-v1":
            cas(candidate, body["expectedVersion"])
            if candidate.status != "paused":
                raise AiError("只有暂停的文件任务可以恢复", "conflict", 409)
            prepared = _prepare_screening_binding(candidate.report, principal, candidate.draft, renderer_version=candidate.renderer_version)
    with mutation(principal):
        row = get(run_id, principal)
        cas(row, body["expectedVersion"])
        if row.status in {"ready", "cancelled"}:
            raise AiError("文件任务已结束", "conflict", 409)
        if body["action"] in {"resume", "rebuild"}:
            if row.status != "paused":
                raise AiError("只有暂停的文件任务可以恢复", "conflict", 409)
            fingerprint = (_check_screening_binding(prepared, row.report, principal, row.draft, renderer_version=row.renderer_version) if prepared is not None
                else binding(row.report, principal, row.draft, renderer_version=row.renderer_version))
            if fingerprint != row.binding_digest:
                raise AiError("报告内容已变化，须创建新文件版本", "conflict", 409)
            if body["action"] == "rebuild":
                row.manifest_json = "{}"
            if row.attempt >= 5 and row.manifest_json == "{}":
                raise AiError("文件重建次数已达到上限", "conflict", 409)
        row.status = {"pause": "paused", "resume": "queued", "rebuild": "queued", "cancel": "cancelled"}[body["action"]]
        row.error_code = ""
        row.lease_until = timezone.now()
        row.version += 1
        row.save()
        result = {"item": mapping(row)}
        if commit is not None:
            return commit(result, 200)
    return result


def chunk(run_id, format, params, principal):
    fields(params, {"sequence"}, {"sequence"})
    if format not in {"html", "xlsx"}:
        raise AiError("文件格式无效")
    try:
        sequence = int(params["sequence"])
        integer(sequence, "sequence", hi=512)
    except (TypeError, ValueError) as error:
        raise AiError("文件分块序号无效") from error
    row = get(run_id, principal)
    if row.renderer_version in (4, 6, 7, 9, 10, 11):
        raise AiError("多卷文件须使用指定卷下载入口", "conflict", 409)
    if row.status != "ready":
        raise AiError("完整双文件尚未就绪", "conflict", 409)
    if binding(row.report, principal, row.draft, renderer_version=row.renderer_version) != row.binding_digest:
        raise AiError("报告内容绑定已变化", "conflict", 409)
    manifest = json.loads(row.manifest_json)
    record = m.AiBusinessFileChunk.objects.filter(run=row, attempt=row.attempt, format=format, sequence=sequence).first()
    if record is None:
        raise AiError("文件分块不存在", "not_found", 404)
    content = bytes(record.content)
    if hashlib.sha256(content).hexdigest() != record.content_digest:
        raise AiError("文件分块未通过摘要校验", "conflict", 409)
    return {"schemaVersion": "business-file-chunk-v1", "runId": row.id, "format": format, "attempt": row.attempt,
        "sequence": sequence, "bytes": len(content), "sha256": record.content_digest, "fileSha256": manifest["files"][format]["sha256"],
        "base64": base64.b64encode(content).decode()}


def audit(row, principal, action):
    AiMutationAudit.objects.create(request_id=uid("file-audit"), actor_email=principal.email.lower(), actor_role=principal.role,
        action="business_files_"+action, scope_digest=digest([principal.scope, row.id]), response_digest=digest(mapping(row)), revision=int(revision())+1)


def _current(run_id, principal, state):
    row = get(run_id, principal)
    if row.status != "building" or row.version != state["version"] or row.attempt != state["attempt"]:
        raise AiError("文件任务已暂停、取消或被其他版本接管", "file_superseded", 409)
    return row


def _check_quota(row, size):
    owner = m.AiBusinessFileRun.objects.filter(owner_email=row.owner_email).aggregate(n=Sum("stored_bytes"))["n"] or 0
    total = m.AiBusinessFileRun.objects.aggregate(n=Sum("stored_bytes"))["n"] or 0
    if row.stored_bytes+size > RUN_BYTES or owner+size > OWNER_BYTES or total+size > GLOBAL_BYTES:
        raise AiError("报告文件存储额度已满，已有分块保留", "payload_too_large", 413)


def _verify_staged(row):
    manifest = json.loads(row.manifest_json)
    if (manifest.get("schemaVersion") != "business-file-delivery-v1" or manifest.get("attempt") != row.attempt
            or manifest.get("bindingDigest") != row.binding_digest or manifest.get("rendererVersion", 1) != row.renderer_version):
        raise AiError("已保存文件清单不完整", "conflict", 409)
    for format in ("html", "xlsx"):
        sha, size, count = hashlib.sha256(), 0, 0
        for part in m.AiBusinessFileChunk.objects.filter(run=row, attempt=row.attempt, format=format).order_by("sequence").iterator(chunk_size=4):
            count += 1
            content = bytes(part.content)
            if part.sequence != count or hashlib.sha256(content).hexdigest() != part.content_digest:
                raise AiError("已保存分块顺序或摘要不符", "conflict", 409)
            sha.update(content)
            size += len(content)
        expected = manifest.get("files", {}).get(format, {})
        if count != expected.get("chunkCount") or size != expected.get("bytes") or sha.hexdigest() != expected.get("sha256"):
            raise AiError("完整文件摘要或长度不符", "conflict", 409)


def _build(row, principal, state):
    started, last_check, last_saved = time.monotonic(), 0, 0
    def checkpoint(progress=None, force=False):
        nonlocal last_check, last_saved
        now = time.monotonic()
        if now-started > BUILD_SECONDS:
            raise AiError("文件构建超过本轮时间预算，保留检查点", "file_build_timeout", 409)
        if not force and now-last_check < 1:
            return
        current_principal(principal, admin=True)
        _current(row.id, principal, state)
        last_check = now
        if progress is not None and now-last_saved >= 5:
            with mutation(principal):
                saved = _current(row.id, principal, state)
                saved.progress_json = canonical(passive(progress, 4096))
                saved.lease_until = timezone.now()+timedelta(seconds=LEASE_SECONDS)
                saved.version += 1
                saved.save()
                audit(saved, principal, "progress")
            state["version"], last_saved = saved.version, now
    if row.manifest_json == "{}":
        with TemporaryDirectory(prefix="teruisi-file-build-") as directory:
            paths = {format: Path(directory)/("report."+format) for format in ("html", "xlsx")}
            with paths["xlsx"].open("wb") as xlsx, paths["html"].open("wb") as html:
                proof = business_export.build(row.report, principal, xlsx, html, draft=row.draft, checkpoint=checkpoint, renderer_version=row.renderer_version)
            files = {}
            for format in ("html", "xlsx"):
                checkpoint(force=True)
                sha, size, sequence = hashlib.sha256(), 0, 0
                with paths[format].open("rb") as stream:
                    while True:
                        batch = []
                        for _ in range(16):
                            content = stream.read(CHUNK_BYTES)
                            if not content:
                                break
                            sequence += 1
                            size += len(content)
                            sha.update(content)
                            batch.append(m.AiBusinessFileChunk(id=uid("file-chunk"), run=row, attempt=state["attempt"], format=format,
                                sequence=sequence, content=content, content_digest=hashlib.sha256(content).hexdigest()))
                        if not batch:
                            break
                        checkpoint(force=True)
                        with mutation(principal):
                            saved = _current(row.id, principal, state)
                            added = sum(len(part.content) for part in batch)
                            _check_quota(saved, added)
                            m.AiBusinessFileChunk.objects.bulk_create(batch, batch_size=16)
                            saved.stored_bytes += added
                            saved.progress_json = canonical({"stage": "saving", "format": format, "chunks": sequence, "bytes": size})
                            saved.version += 1
                            saved.lease_until = timezone.now()+timedelta(seconds=LEASE_SECONDS)
                            saved.save()
                            audit(saved, principal, "chunks_saved")
                        state["version"] = saved.version
                files[format] = {"bytes": size, "chunkCount": sequence, "sha256": sha.hexdigest(), "chunkBytes": CHUNK_BYTES,
                    "mimeType": "text/html; charset=utf-8" if format == "html" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "fileName": f'经营分析-{row.report_id}-{"草稿" if row.draft else "已复核"}.{format}'}
            with mutation(principal):
                saved = _current(row.id, principal, state)
                saved.manifest_json = canonical(passive({"schemaVersion": "business-file-delivery-v1", "attempt": row.attempt,
                    "bindingDigest": row.binding_digest, "rendererVersion": row.renderer_version, "draft": row.draft, "files": files, "tables": proof["tables"],
                    **({"budgetCalculator": proof["budgetCalculator"]} if proof.get("budgetCalculator") else {})}, 131072))
                saved.progress_json = canonical({"stage": "verifying"})
                saved.version += 1
                saved.save()
                audit(saved, principal, "staged")
            state["version"] = saved.version
    checkpoint(force=True)
    row = _current(row.id, principal, state)
    _verify_staged(row)
    checkpoint(force=True)
    with mutation(principal):
        saved = _current(row.id, principal, state)
        if binding(saved.report, principal, saved.draft, renderer_version=saved.renderer_version) != saved.binding_digest:
            raise AiError("生成期间报告内容已变化", "conflict", 409)
        saved.status, saved.error_code, saved.progress_json = "ready", "", canonical({"stage": "ready"})
        saved.version += 1
        saved.save()
        audit(saved, principal, "ready")
    return {"status": "ready", "runId": saved.id, "version": saved.version}


def tick():
    if connection.vendor != "postgresql":
        raise AiError("文件构建需要 PostgreSQL 互斥", "service_unavailable", 503)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", [192839, 7303])
        if not cursor.fetchone()[0]:
            return {"status": "file_builder_busy"}
    try:
        candidates = m.AiBusinessFileRun.objects.select_related("report__workflow").filter(
            Q(status="queued") | Q(status="building", lease_until__lte=timezone.now()))
        if getattr(settings, "AI_PROMOTION_BUDGET_V11_STAGE_CANDIDATE_ENABLED", False) is not True:
            candidates = candidates.exclude(renderer_version=11)
        row = candidates.order_by("lease_until", "created_at", "id").first()
        if row is None:
            return {"status": "idle"}
        state = {"version": row.version, "attempt": row.attempt}
        principal = None
        try:
            principal = workflows.background(row)
            current_principal(principal, admin=True)
            if row.renderer_version in (7, 9, 10, 11):
                if row.renderer_version == 7:
                    from .business_promotion_volume_stage import binding as promotion_binding
                elif row.renderer_version == 9:
                    from .business_promotion_trial_volume_stage import binding as promotion_binding
                elif row.renderer_version == 10:
                    from .business_promotion_budget_v10_stage import binding as promotion_binding
                else:
                    from .business_promotion_budget_v11_durable_stage import binding as promotion_binding
                fingerprint = promotion_binding(row.report, principal, row.draft)
            else:
                fingerprint = binding(row.report, principal, row.draft, renderer_version=row.renderer_version)
            if fingerprint != row.binding_digest:
                raise AiError("报告内容绑定已变化", "conflict", 409)
            if row.manifest_json == "{}" and row.attempt >= 5:
                raise AiError("文件构建次数已达到上限", "conflict", 409)
            with mutation(principal):
                saved = get(row.id, principal)
                if saved.version != state["version"]:
                    return {"status": "superseded"}
                saved.status, saved.error_code = "building", ""
                if saved.manifest_json == "{}":
                    saved.attempt += 1
                saved.lease_until = timezone.now()+timedelta(seconds=LEASE_SECONDS)
                saved.progress_json = canonical({"stage": "verifying" if saved.manifest_json != "{}" else "preparing"})
                saved.version += 1
                saved.save()
                audit(saved, principal, "claimed")
            state.update(version=saved.version, attempt=saved.attempt)
            if saved.renderer_version == 10:
                from .business_promotion_budget_v10_stage import build as promotion_budget_build
                # Renderer 10 is stage-only. Never invoke a publisher from tick.
                return promotion_budget_build(saved, principal, state)
            if saved.renderer_version == 11:
                from .business_promotion_budget_v11_durable_stage import build as slim_budget_build
                # Renderer 11 is independently staged-only; ready has no path.
                return slim_budget_build(saved, principal, state)
            if saved.renderer_version in (7, 9):
                if saved.renderer_version == 7:
                    from .business_promotion_volume_stage import build as promotion_build, publish as promotion_publish
                else:
                    from .business_promotion_trial_volume_stage import build as promotion_build, publish as promotion_publish
                staged = promotion_build(saved, principal, state)
                if staged["status"] != "staged_unpublished":
                    return staged
                if saved.report.workflow.status != "completed":
                    return staged
                try:
                    return {"status": "ready", **promotion_publish(saved.id, staged["version"], principal)}
                except AiError as error:
                    # The complete staged bytes remain paused for a later
                    # authorized resume. An uncertain outcome is never replayed.
                    return {"status": "paused", "runId": saved.id, "errorCode": error.code}
            if saved.renderer_version in (4, 6):
                from .business_volume_files import build
                return build(saved, principal, state)
            return _build(saved, principal, state)
        except Exception as caught:
            error = caught if isinstance(caught, AiError) else AiError("文件构建失败", "file_build_failed", 503)
            from sales.auth import Principal
            actor = principal or Principal("ai-scheduler@teruisi.internal", "AI scheduler", "operator", None)
            with mutation():
                saved = m.AiBusinessFileRun.objects.get(pk=row.id)
                if saved.version != state["version"] or saved.status in {"ready", "cancelled", "paused"}:
                    return {"status": "superseded", "runId": saved.id}
                saved.status, saved.error_code = "paused", error.code
                saved.version += 1
                saved.save()
                audit(saved, actor, "paused")
            return {"status": "paused", "runId": saved.id, "errorCode": error.code}
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", [192839, 7303])
