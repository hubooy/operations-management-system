"""Leased local preparation; no public creation or scheduler registration here.

Events are audit only. Only a current owning PreparedAdmission can reach the
optional internal continuation. Full scans and capacity work stay outside the
global mutation, and publication performs its CAS inside its own transaction.
"""
from dataclasses import dataclass
from datetime import timedelta
import json
import time

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from . import models as m, workflows
from . import business_screening_runtime as runtime, business_screening_store as store
from . import business_diagnostic_screening as screening
from .policy import AiError, authorize_owner, canonical, current_principal, digest, mutation, uid

LEASE_SECONDS = 200
DEADLINE_SECONDS = 180
MAX_SCAN_ATTEMPTS = 3
EVENT_SCHEMA = "business-screening-readiness-audit-v1"


def available(query):
    """Exclude active local preparation from oldest-first scheduler selection."""
    return query.filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=timezone.now()))


def report_for(row):
    report = m.AiReportRun.objects.filter(workflow_id=row.id).select_related("workflow").first()
    if report is None:
        return None
    try:
        return report if json.loads(report.snapshot_json).get("executionProfile") == runtime.PROFILE else None
    except (ValueError, TypeError, AttributeError):
        return None


def preparation_status(report, principal):
    """Display actual persisted progress; never an execution permission."""
    actual, snapshot, _, _, _, _ = runtime.bound(report,principal)
    flow = m.AiWorkflowRuns.objects.get(pk=actual.workflow_id)
    published = m.AiBusinessScreeningRun.objects.filter(
        pk=snapshot["screeningIntent"]["id"],report_id=actual.id).exists()
    started = m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists()
    leased = bool(flow.lease_token and flow.lease_expires_at and flow.lease_expires_at > timezone.now())
    if flow.cancel_requested or flow.status == "cancelled":
        status = "cancelled"
    elif flow.status in {"waiting_review", "completed", "failed", "paused"}:
        status = flow.status
    elif started:
        status = "analyzing"
    elif published:
        status = "checking_capacity" if leased else "queued_admission"
    else:
        status = "scanning" if leased else "queued_scan"
    result = {"status":status,"scanPublished":published,"agentsStarted":started}
    if flow.error_code:
        result.update(errorCode=flow.error_code,error="分析准备或执行未完成，请查看任务状态后处理。")
    return result


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
        raise AiError("筛查准备租约已失效", "lease_lost", 409)
    report = report_for(row)
    if report is None or report.id != lease.report_id or digest(report.snapshot_json) != lease.snapshot_digest:
        raise AiError("筛查准备绑定已变化", "lease_lost", 409)
    if permission:
        current_principal(principal, admin=True, write=True)
        authorize_owner(row, principal)
    return row, report


def claim(candidate, principal):
    if connection.in_atomic_block:
        raise AiError("筛查领取须在最外层事务之外", "invalid_request", 400)
    with mutation(principal):
        current_principal(principal, admin=True, write=True)
        row = available(m.AiWorkflowRuns.objects.filter(pk=candidate.id, version=candidate.version,
            status__in=["queued", "running"], cancel_requested=0, next_run_at__lte=timezone.now())).first()
        if row is None:
            return None
        report = report_for(row)
        if report is None:
            raise AiError("准备队列只接受固定筛查报告", "invalid_request", 400)
        _, snapshot, _, _, _, _ = runtime.bound(report, principal)
        needs_scan = not m.AiBusinessScreeningRun.objects.filter(pk=snapshot["screeningIntent"]["id"]).exists()
        if needs_scan and row.attempt_count >= MAX_SCAN_ATTEMPTS:
            workflows._fail(row, principal, "screening_prepare_attempts_exceeded")
            return None
        now = timezone.now()
        row.lease_token = uid("screen-lease")
        row.lease_epoch += 1
        row.version += 1
        row.attempt_count += int(needs_scan)
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.next_run_at = row.lease_expires_at
        row.updated_at = now
        row.save()
        workflows.event(row, principal, "screening_prepare_claimed", row.status)
        return Lease(row.id, report.id, row.lease_token, row.lease_epoch, row.version,
            row.owner_email, row.scope_json, digest(report.snapshot_json), digest(row.input_json))


class Check:
    """Every callback checks wall time; live DB checks are at most one second apart."""
    def __init__(self, lease, principal):
        self.lease, self.principal = lease, principal
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.last = float("-inf")

    def __call__(self, _progress=None, *, force=False):
        now = time.monotonic()
        if now >= self.deadline:
            raise AiError("完整筛查准备超过单次时间边界", "screening_prepare_deadline", 409)
        if force or now - self.last >= 1:
            _leased(self.lease, self.principal)
            self.last = now


def _release(row, principal, kind, details):
    row.lease_token = ""
    row.lease_expires_at = None
    row.next_run_at = timezone.now()
    row.updated_at = row.next_run_at
    row.version += 1
    row.save()
    value = {"schemaVersion":EVENT_SCHEMA, **details}
    encoded = canonical(value)
    if len(encoded.encode()) > 32768:
        raise AiError("筛查准备审计超过容量", "payload_too_large", 413)
    m.AiWorkflowEvents.objects.create(id=uid("screen-ready-event"), run_id=row.id, run_version=row.version,
        owner_email=row.owner_email, actor_email=principal.email, event_type=kind,
        from_status=row.status, to_status=row.status, details_json=encoded)


def advance(candidate, principal, *, on_ready=None):
    """At most one scan or one admission per tick; continuation is internal only."""
    from . import business_screening_permission as permission
    lease = claim(candidate, principal)
    if lease is None:
        return {"status":"not_claimed", "runId":candidate.id}
    check = Check(lease, principal)
    try:
        check(force=True)
        _, report = _leased(lease, principal)
        intent_id = json.loads(report.snapshot_json)["screeningIntent"]["id"]
        if not m.AiBusinessScreeningRun.objects.filter(pk=intent_id).exists():
            verified = screening.prepare_for_report(report.id, principal, checkpoint=check)
            check(force=True)
            published = store.publish(verified, principal, before_write=lambda: check(force=True))
            with mutation(principal):
                check(force=True)
                row, _ = _leased(lease, principal)
                _release(row, principal, "screening_published", {"reference":published["reference"]})
            return {"status":"screening_prepared", "runId":lease.run_id}
        # A restart with already published pages never repeats the rule scan.
        prepared = permission.get(report, principal)
        check(force=True)
        with mutation(principal):
            check(force=True)
            row, _ = _leased(lease, principal)
            proof = permission.check(prepared, principal)
            check(force=True)
            _release(row, principal, "screening_capacity_verified", {"proof":proof})
            if on_ready is not None:
                return on_ready(row, prepared)
        return {"status":"screening_ready", "runId":lease.run_id}
    except Exception as error:
        with mutation():
            try:
                row, _ = _leased(lease, principal, permission=False)
            except AiError:
                return {"status":"lease_lost", "runId":lease.run_id}
            code = error.code if isinstance(error, AiError) else "screening_prepare_failed"
            workflows._fail(row, principal, code)
            return {"status":"failed", "runId":lease.run_id, "errorCode":code}
