"""Audited resumable read-only collection, independent of browser lifetime."""
import json
import time
from datetime import timedelta
from django.db import connection
from django.utils import timezone
from . import business_evidence as evidence, models as m, workflows
from .control_models import AiMutationAudit
from .policy import AiError, cas, current_principal, digest, fields, mutation, revision, uid


def audit(row, principal, action, request_id):
    # Failure audits must not reload the very catalog that failed validation.
    # Preserve legacy audit bytes; v2 binds the parent receipt independently.
    value = ({"schemaVersion": "business-collection-audit-v2", "runId": row.id,
        "version": row.version, "status": row.status, "collectionStatus": row.collection_status,
        "planDigest": digest(row.plan_json), "stateDigest": digest(row.state_json),
        "storedBytes": row.stored_bytes, "errorCode": row.collection_error_code,
        "consecutiveFailures": row.collection_failures, "nextAttemptAt": row.next_collect_at.isoformat()}
        if json.loads(row.plan_json).get("schemaVersion") == "business-evidence-v2" else evidence.mapping(row))
    AiMutationAudit.objects.create(request_id=request_id, actor_email=principal.email.lower(), actor_role=principal.role,
        action="business_collection_" + action, scope_digest=digest([principal.scope, row.id]),
        response_digest=digest(value), revision=int(revision())+1)


def control(run_id, body, principal):
    fields(body, {"expectedVersion", "action"}, {"expectedVersion", "action"})
    if body["action"] not in ("pause", "resume"):
        raise AiError("采集控制动作无效")
    with mutation(principal):
        row = evidence.get_run(run_id, principal)
        cas(row, body["expectedVersion"])
        if row.status != "collecting" or not json.loads(row.plan_json).get("collector"):
            raise AiError("只有未结束的批量证据可以控制", "conflict", 409)
        row.collection_status = "paused" if body["action"] == "pause" else "queued"
        row.next_collect_at = timezone.now()
        row.collection_failures = 0
        row.collection_error_code = ""
        row.version += 1
        row.save()
    return {"item": evidence.mapping(row)}


def tick():
    if connection.vendor != "postgresql":
        raise AiError("后台采集需要 PostgreSQL 互斥", "service_unavailable", 503)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [192839, 7302])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return {"status": "collector_busy"}
    try:
        started, steps = time.monotonic(), []
        for _ in range(16):
            if time.monotonic() - started >= 25:
                break
            result = _advance()
            if "item" not in result:
                return {"status": result["status"], "steps": steps, **{k: v for k, v in result.items() if k != "status"}}
            item = result["item"]
            steps.append({"runId": item["id"], "status": item["status"], "version": item["version"], "storedBytes": item["storedBytes"]})
            if item["status"] != "collecting":
                break
        return {"status": "advanced", "steps": steps}
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [192839, 7302])


def _advance():
    row = m.AiBusinessEvidenceRun.objects.filter(status="collecting", collection_status__in=["queued", "reading"],
        next_collect_at__lte=timezone.now()).order_by("next_collect_at", "created_at", "id").first()
    if row is None:
        return {"status": "idle"}
    request_id = uid("collection")
    try:
        principal = workflows.background(row)
        current_principal(principal, admin=True)
    except AiError as error:
        # Permission loss must stop scheduling without impersonating the owner.
        from sales.auth import Principal
        scheduler = Principal("ai-scheduler@teruisi.internal", "AI scheduler", "operator", None)
        with mutation():
            current = m.AiBusinessEvidenceRun.objects.get(pk=row.id)
            if current.version == row.version and current.status == "collecting":
                current.collection_status, current.collection_error_code = "paused", error.code
                current.version += 1
                current.save()
                audit(current, scheduler, "authorization_paused", request_id)
        return {"status": "paused", "runId": row.id, "errorCode": error.code}
    with mutation(principal):
        current = evidence.get_run(row.id, principal)
        if current.version != row.version or current.status != "collecting":
            return {"status": "superseded"}
        current.collection_status = "reading"
        current.next_collect_at = timezone.now() + timedelta(seconds=120)
        current.version += 1
        current.save()
        audit(current, principal, "claimed", request_id)
        version = current.version
    try:
        source = evidence.next_source(current)
        if source is None:
            with mutation(principal):
                result = evidence.finish(row.id, {"expectedVersion": version, "action": "seal"}, principal)
                audit(evidence.get_run(row.id, principal), principal, "sealed", request_id)
                return result
        def commit_page(payload, status):
            saved = evidence.get_run(row.id, principal)
            saved.collection_status, saved.collection_failures, saved.collection_error_code = "queued", 0, ""
            saved.next_collect_at = timezone.now()
            saved.version += 1
            saved.save()
            if evidence.all_finished(saved):
                evidence.finish(saved.id, {"expectedVersion": saved.version, "action": "seal"}, principal)
                saved.refresh_from_db()
            audit(saved, principal, "page_committed", request_id)
            return {"item": evidence.mapping(saved)}
        return evidence.collect(row.id, {"sourceKey": source["key"], "expectedVersion": version}, principal, request_id, commit=commit_page)
    except Exception as caught:
        error = caught if isinstance(caught, AiError) else AiError("后台采集失败", "collection_failed", 503)
        with mutation():
            saved = m.AiBusinessEvidenceRun.objects.get(pk=row.id)
            if saved.version != version or saved.status != "collecting":
                return {"status": "superseded", "runId": row.id}
            saved.collection_failures += 1
            saved.collection_error_code = error.code
            retry = error.code in {"service_unavailable", "rate_limited"} and saved.collection_failures < 3
            saved.collection_status = "queued" if retry else "paused"
            saved.next_collect_at = timezone.now() + timedelta(seconds=30 * 2**min(saved.collection_failures-1, 2))
            saved.version += 1
            saved.save()
            audit(saved, principal, "read_failed", request_id)
        return {"status": saved.collection_status, "runId": saved.id, "errorCode": error.code}
