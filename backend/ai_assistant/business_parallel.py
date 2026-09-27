"""Bounded parallel specialists; external calls stay outside domain transactions."""
import json
from concurrent.futures import ThreadPoolExecutor
from django.db import connection, close_old_connections
from django.utils import timezone
from . import models as m
from .policy import AiError, canonical, digest, passive, uid

def report_ids():
    return m.AiReportRun.objects.filter(snapshot_json__contains='"executionMode":"parallel-v1"').filter(
        snapshot_json__contains='"schemaVersion":"business-report-v1"')


def is_parallel(run_id):
    return report_ids().filter(workflow_id=run_id).exists()


def workflow_step(row, principal, nodes, *, screening_permission=None):
    from . import workflows as w, report_library
    from . import business_screening_readiness, business_screening_pipeline
    if business_screening_readiness.report_for(row) is not None:
        # Re-read actual nodes: a JSON ready event or a caller-supplied list
        # cannot authorize a new specialist.
        nodes = business_screening_pipeline.checked_nodes(row,screening_permission,principal)
    before, changed = row.status, False
    by_key = {n.node_key: n for n in nodes}
    for node in nodes:
        if node.status != "running":
            continue
        child = m.AiAgentJobs.objects.get(pk=node.agent_job_id)
        if child.status in {"queued", "running", "paused"}:
            continue
        node.status = "completed" if child.status == "completed" else "failed"
        node.output_json = child.output_json if child.status == "completed" else None
        node.error_code = child.error_code if child.status != "completed" else ""
        node.error_message = child.error_message if child.status != "completed" else ""
        node.completed_at = timezone.now()
        node.version += 1
        node.save()
        changed = True
    running = [n for n in nodes if n.status == "running"]
    failed = [n for n in nodes if n.status == "failed"]
    ready = [n for n in nodes if n.status == "pending" and all(by_key[d].status in {"completed", "skipped"} for d in json.loads(n.depends_on_json))]
    # A failed specialist never erases or cancels independent siblings. Wait for
    # their durable outcomes before pausing; dependent nodes remain unstarted.
    if failed and not running and not ready:
        children = [m.AiAgentJobs.objects.get(pk=n.agent_job_id) for n in failed]
        if not all(child.retryable for child in children):
            return w._fail(row, principal, "child_agent_failed")
        row.status = "paused"
        row.retryable = 1
        row.current_node_key = failed[0].node_key
        changed = True
    elif all(n.status in {"completed", "skipped"} for n in nodes):
        row.output_json = canonical(passive({n.node_key: json.loads(n.output_json) if n.output_json else None for n in nodes}, 96 * 1024))
        row.status, row.current_node_key = "completed", None
        row.completed_at = timezone.now()
        changed = True
    else:
        slots = max(0, 3 - len(running))
        for node in ready[:slots]:
            if node.node_type == "human_review":
                if running:
                    break
                node.status, row.status, row.current_node_key = "waiting_review", "waiting_review", node.node_key
            else:
                active = m.AiAgentJobs.objects.filter(status__in=w.ACTIVE)
                if active.count() >= 64 or active.filter(owner_email=row.owner_email).count() >= 8:
                    break
                data = passive({"workflowInput": json.loads(row.input_json),
                    "dependencies": {d: json.loads(by_key[d].output_json) for d in json.loads(node.depends_on_json)}}, 24 * 1024)
                node.input_json = canonical(data)
                child = m.AiAgentJobs.objects.create(id=uid("ai-agent"), owner_email=row.owner_email,
                    client_request_id="workflow-" + digest([row.id, node.node_key])[:64], request_digest=digest([row.id, node.node_key]),
                    scope_json=row.scope_json, task=node.instruction, input_json=node.input_json, model_id=row.model_id,
                    model_version=row.model_version, allowed_tools_json=row.allowed_tools_json, tool_policy_digest=row.tool_policy_digest,
                    workflow_run_id=row.id, workflow_node_key=node.node_key)
                report_library.pin(child.id, child.task, None, [], parent_id=row.id)
                w.event(child, principal, "created")
                node.agent_job_id, node.status, row.status = child.id, "running", "running"
                row.current_node_key = None
            node.started_at = timezone.now()
            node.version += 1
            node.save()
            changed = True
        if not running and not ready and not failed:
            return w._fail(row, principal, "workflow_no_ready_node")
    row.updated_at = timezone.now()
    if changed:
        row.version += 1
        row.started_at = row.started_at or timezone.now()
        row.save()
        w.event(row, principal, "completed" if row.status == "completed" else "node_progressed", before)
    else:
        row.save(update_fields=["updated_at"])
    return {"status": row.status, "runId": row.id, "parallelism": 3}


def agent_queue_tick():
    """One global dispatcher, at most three selected business microsteps.

    Session advisory lock only serializes dispatch batches, not domain writes;
    each microstep retains its own persisted lease, policy and unknown-result
    fences. Ordinary jobs retain one-call serial behavior and oldest-first order.
    """
    from . import workflows as w
    if connection.vendor != "postgresql":
        raise AiError("并行派发需要 PostgreSQL 互斥", "service_unavailable", 503)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [192839, 7301])
        acquired = cursor.fetchone()[0]
    if not acquired:
        return {"status": "dispatcher_busy"}
    try:
        eligible = w.agent_candidates().order_by("updated_at", "created_at", "id")
        first = eligible.first()
        if first is None:
            return {"status": "idle"}
        if not first.workflow_run_id or not is_parallel(first.workflow_run_id):
            return w.agent_tick(job_id=first.id)
        ids = list(eligible.filter(workflow_run_id__in=report_ids().values("workflow_id")).values_list("id", flat=True)[:3])
        def execute(job_id):
            close_old_connections()
            try:
                return w.agent_tick(job_id=job_id)
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="business-agent") as pool:
            results = list(pool.map(execute, ids))
        return {"status": "advanced", "selected": len(ids), "results": results}
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [192839, 7301])
