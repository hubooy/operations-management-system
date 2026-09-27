"""Internal leased rule scan for promotion reports, without Agent admission.

The scheduler routes only this new profile to ``advance``. Successful
publication parks the workflow until the four-tool Agent admission protocol is
available; no model or Agent job can start through this path.
"""
from dataclasses import dataclass
from datetime import timedelta
import json
import time

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from . import business_diagnostic_screening as screening, business_screening_store as store
from . import business_promotion_runtime_contract as promotion, models as m, workflows
from .policy import AiError, authorize_owner, canonical, current_principal, digest, mutation, uid


LEASE_SECONDS = 200
DEADLINE_SECONDS = 180
MAX_SCAN_ATTEMPTS = 3
PARKED_CODE = "promotion_admission_not_registered"
EVENT_SCHEMA = "business-promotion-screening-readiness-v1"
RESUME_SCHEMA = "business-promotion-paused-resume-candidate-v1"
_RESUME_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class PreparedResume:
    _permission: object
    _report_id: str
    _run_id: str
    _version: int
    _reference_json: str
    _digest: str

    def __init__(self, token, permission, report_id, run_id, version, reference):
        if token is not _RESUME_TOKEN:
            raise AiError("词货停靠恢复候选只能从当前筛查根建立", "invalid_request", 400)
        values = (report_id, run_id, version, canonical(reference))
        for key, value in zip(("_report_id", "_run_id", "_version", "_reference_json"), values):
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_permission", permission)
        object.__setattr__(self, "_digest", digest(values))


def prepare_resume_candidate(report_id, principal):
    """Full capacity preparation for one exact parked version; no status write."""
    from . import business_promotion_runtime_permission as permission
    if connection.in_atomic_block:
        raise AiError("词货恢复容量准备须在最外层事务之外", "invalid_request", 400)
    prepared = permission.prepare(report_id, principal, "commerce", allow_parked=True)
    proof = prepared.proof
    row = m.AiWorkflowRuns.objects.filter(pk=proof["workflowId"]).first()
    report = report_for(row) if row else None
    if report is None or report.id != proof["reportId"] or row.version != proof["workflowVersion"]:
        raise AiError("词货停靠流程版本已变化", "conflict", 409)
    reference = _published(report, principal)
    if canonical(reference) != canonical(proof["screeningReference"]):
        raise AiError("词货停靠筛查发布根已变化", "conflict", 409)
    candidate = PreparedResume(_RESUME_TOKEN, prepared, report.id, row.id, row.version, reference)
    with mutation(principal):
        check_resume_candidate(candidate, principal)
    return candidate


def check_resume_candidate(candidate, principal):
    """Short CAS precondition; safe under mutation, with no external read."""
    from . import business_promotion_runtime_permission as permission
    if (type(candidate) is not PreparedResume
            or digest((candidate._report_id, candidate._run_id,
                candidate._version, candidate._reference_json)) != candidate._digest):
        raise AiError("词货恢复候选内部结构已变化", "conflict", 409)
    proof = permission.check(candidate._permission, principal)
    current_principal(principal, admin=True, write=True)
    row = m.AiWorkflowRuns.objects.filter(pk=candidate._run_id,
        version=candidate._version, status="paused", retryable=0,
        error_code=PARKED_CODE, cancel_requested=0).first()
    report = report_for(row) if row else None
    saved = m.AiBusinessScreeningRun.objects.filter(pk=proof["screeningReference"]["id"]).first()
    if (row is None or report is None or report.id != candidate._report_id
            or proof["workflowStatus"] != "paused"
            or proof["workflowVersion"] != candidate._version
            or saved is None or saved.report_id != report.id
            or canonical(store._reference(saved)) != candidate._reference_json
            or m.AiAgentJobs.objects.filter(workflow_run_id=row.id).exists()):
        raise AiError("词货停靠恢复版本、筛查或子任务状态已变化", "conflict", 409)
    return {"schemaVersion": RESUME_SCHEMA, "runId": row.id,
        "reportId": report.id, "expectedVersion": row.version,
        "fromStatus": "paused", "proposedStatus": "queued",
        "screeningReference": json.loads(candidate._reference_json),
        "capacityVerified": True, "runtimeAdmissionGranted": False,
        "casApplied": False, "pipelineRegistered": False}


def resume_cas(candidate, principal):
    """No write until workflow_tick routes resumed promotion runs to pipeline."""
    with mutation(principal):
        check_resume_candidate(candidate, principal)
        raise AiError("当前调度会将恢复的词货报告再次停靠，CAS 恢复尚未接入安全路由",
            "promotion_pipeline_not_routed", 409)


def available(query):
    return query.filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=timezone.now()))


def report_for(row):
    report = m.AiReportRun.objects.filter(workflow_id=row.id).select_related("workflow").first()
    if report is None:
        return None
    try:
        return report if json.loads(report.snapshot_json).get("executionProfile") == promotion.PROFILE else None
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True, slots=True)
class Lease:
    run_id: str
    report_id: str
    token: str
    epoch: int
    version: int
    owner: str
    scope: str
    snapshot_digest: str
    input_digest: str


def _leased(lease, principal, *, permission=True):
    row = m.AiWorkflowRuns.objects.filter(pk=lease.run_id).first()
    if (row is None or row.lease_token != lease.token or row.lease_epoch != lease.epoch
            or row.version != lease.version or row.status not in {"queued", "running"}
            or row.cancel_requested or row.lease_expires_at is None or row.lease_expires_at <= timezone.now()
            or row.owner_email != lease.owner or row.scope_json != lease.scope
            or digest(row.input_json) != lease.input_digest):
        raise AiError("词货筛查租约已失效", "lease_lost", 409)
    report = report_for(row)
    if report is None or report.id != lease.report_id or digest(report.snapshot_json) != lease.snapshot_digest:
        raise AiError("词货筛查固定报告已变化", "lease_lost", 409)
    if permission:
        current_principal(principal, admin=True, write=True)
        authorize_owner(row, principal)
    if screening._load(report.id, principal)[0]["executionProfile"] != promotion.PROFILE:
        raise AiError("词货筛查报告不再符合新协议", "lease_lost", 409)
    return row, report


def claim(candidate, principal):
    """Claim one fixed workflow version; a saved scan spends no new attempt."""
    if connection.in_atomic_block:
        raise AiError("词货筛查领取须在最外层事务之外", "invalid_request", 400)
    with mutation(principal):
        current_principal(principal, admin=True, write=True)
        row = available(m.AiWorkflowRuns.objects.filter(pk=candidate.id, version=candidate.version,
            status__in=["queued", "running"], cancel_requested=0, next_run_at__lte=timezone.now())).first()
        if row is None:
            return None
        report = report_for(row)
        if report is None:
            raise AiError("词货准备队列只接受新固定报告", "invalid_request", 400)
        authorize_owner(row, principal)
        binding = screening._load(report.id, principal)[0]
        snapshot = json.loads(report.snapshot_json)
        screening_id = snapshot["screeningIntent"]["id"]
        saved = m.AiBusinessScreeningRun.objects.filter(pk=screening_id).first()
        if saved is not None:
            if saved.report_id != report.id or saved.binding_json != canonical(binding):
                raise AiError("已有筛查结果不属于当前词货报告", "conflict", 409)
        elif row.attempt_count >= MAX_SCAN_ATTEMPTS:
            workflows._fail(row, principal, "promotion_screening_prepare_attempts_exceeded")
            return None
        now = timezone.now()
        row.lease_token = uid("promotion-screen-lease")
        row.lease_epoch += 1
        row.version += 1
        row.attempt_count += int(saved is None)
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.next_run_at = row.lease_expires_at
        row.updated_at = now
        row.save()
        workflows.event(row, principal, "promotion_screening_claimed", row.status)
        return Lease(row.id, report.id, row.lease_token, row.lease_epoch, row.version,
            row.owner_email, row.scope_json, digest(report.snapshot_json), digest(row.input_json))


class Check:
    def __init__(self, lease, principal):
        self.lease, self.principal = lease, principal
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.last = float("-inf")

    def __call__(self, _progress=None, *, force=False):
        now = time.monotonic()
        if now >= self.deadline:
            raise AiError("词货完整筛查超过单次时间边界", "promotion_screening_deadline", 409)
        if force or now - self.last >= 1:
            _leased(self.lease, self.principal)
            self.last = now


def _published(report, principal):
    """Read the complete persisted page chain before deciding to park."""
    snapshot = json.loads(report.snapshot_json)
    saved, binding, manifest = store._loaded(snapshot["screeningIntent"]["id"], principal)
    if saved.report_id != report.id or binding["executionProfile"] != promotion.PROFILE:
        raise AiError("词货筛查持久结果跨报告", "conflict", 409)
    checked = store._call(store.contract.validate, store._all(saved))
    if canonical(checked) != canonical(manifest):
        raise AiError("词货筛查持久页链与清单不一致", "conflict", 409)
    return store._reference(saved)


def _park(row, report, principal, reference):
    """Stop at a visible, non-resumable gate until admission is implemented."""
    saved, binding, _ = store._loaded(reference["id"], principal)
    if (saved.report_id != report.id or binding["executionProfile"] != promotion.PROFILE
            or store._reference(saved) != reference
            or m.AiAgentJobs.objects.filter(workflow_run_id=row.id).exists()):
        raise AiError("词货筛查停靠前身份或Agent状态变化", "conflict", 409)
    previous = row.status
    now = timezone.now()
    row.status = "paused"
    row.retryable = 0
    row.error_code = PARKED_CODE
    row.error_message = "筛查已发布，四工具Agent准入尚未启用"
    row.lease_token = ""
    row.lease_expires_at = None
    row.next_run_at = now
    row.updated_at = now
    row.version += 1
    row.save()
    details = canonical({"schemaVersion": EVENT_SCHEMA, "reference": reference,
        "admissionGranted": False, "agentDispatchAllowed": False})
    m.AiWorkflowEvents.objects.create(id=uid("promotion-screen-event"), run_id=row.id,
        run_version=row.version, owner_email=row.owner_email, actor_email=principal.email,
        event_type="promotion_screening_published_awaiting_admission",
        from_status=previous, to_status=row.status, details_json=details)


def advance(candidate, principal):
    """At most one full scan; never admits or dispatches a model."""
    lease = claim(candidate, principal)
    if lease is None:
        return {"status": "not_claimed", "runId": candidate.id}
    check = Check(lease, principal)
    try:
        check(force=True)
        _, report = _leased(lease, principal)
        screening_id = json.loads(report.snapshot_json)["screeningIntent"]["id"]
        if not m.AiBusinessScreeningRun.objects.filter(pk=screening_id).exists():
            verified = screening.prepare_for_report(report.id, principal, checkpoint=check)
            check(force=True)
            store.publish(verified, principal, before_write=lambda: check(force=True))
        # Also covers recovery after a process exit between publication and park.
        reference = _published(report, principal)
        check(force=True)
        with mutation(principal):
            check(force=True)
            row, report = _leased(lease, principal)
            _park(row, report, principal, reference)
        return {"status": "screening_published_awaiting_admission", "runId": lease.run_id,
            "screeningReference": reference}
    except Exception as error:
        with mutation():
            try:
                row, _ = _leased(lease, principal, permission=False)
            except AiError:
                return {"status": "lease_lost", "runId": lease.run_id}
            code = error.code if isinstance(error, AiError) else "promotion_screening_prepare_failed"
            workflows._fail(row, principal, code)
            return {"status": "failed", "runId": lease.run_id, "errorCode": code}
