"""Small live ledger fence, not an alternative to full content verification.

Only fixed five-job metadata is read. PostgreSQL hashes payloads in place;
neither evidence pages nor model, budget, or reference computation is invoked.
"""
from django.db import connection

from . import models as m, business_screening_runtime as runtime
from . import business_screening_runtime_contract as contract, business_screening_store as store
from .policy import AiError, canonical, current_principal, digest

SCHEMA = "business-screening-content-fence-v1"
# Exact application SQL only: no caller-provided table, column or expression.
PROVIDERS = """
SELECT d.id,d.dispatch_ordinal,d.owner_email,d.actor_role,d.model_id,d.model_version,d.tool_policy_digest,
 encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex'),
 r.dispatch_id,r.response_digest,
 encode(sha256(convert_to(r.response_json,'UTF8')),'hex'),octet_length(r.response_json),
 encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex')
FROM ai_agent_provider_dispatches d LEFT JOIN ai_agent_provider_results r ON r.dispatch_id=d.id
WHERE d.job_id=%s ORDER BY d.dispatch_ordinal LIMIT 21
"""
TOOLS = """
SELECT d.id,d.tool_call_ordinal,d.provider_dispatch_id,d.arguments_digest,
 encode(sha256(convert_to(d.arguments_json,'UTF8')),'hex'),octet_length(d.arguments_json),
 encode(sha256(convert_to(to_jsonb(d)::text,'UTF8')),'hex'),
 r.tool_dispatch_id,r.result_digest,
 encode(sha256(convert_to(r.result_json,'UTF8')),'hex'),octet_length(r.result_json),
 encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex')
FROM ai_agent_tool_dispatches d LEFT JOIN ai_agent_tool_results r ON r.tool_dispatch_id=d.id
WHERE d.job_id=%s ORDER BY d.tool_call_ordinal LIMIT 41
"""
GUIDANCE = """
SELECT entity_id,octet_length(snapshot_json),encode(sha256(convert_to(snapshot_json,'UTF8')),'hex')
FROM ai_execution_guidance WHERE entity_id=ANY(%s) ORDER BY entity_id LIMIT 7
"""


def _require(ok, message="筛查内容账本或固定身份已变化"):
    if not ok: raise AiError(message, "conflict", 409)


def _rows(query, job_id):
    with connection.cursor() as cursor:
        cursor.execute(query, [job_id])
        return cursor.fetchall()


def _ledger(job):
    providers, tools = _rows(PROVIDERS, job.id), _rows(TOOLS, job.id)
    _require(len(providers) <= 20 and len(tools) <= 40, "实际账本超过固定容量")
    ids = {row[0] for row in providers}
    for index, row in enumerate(providers, 1):
        _require(row[1] == index and tuple(row[2:7]) == (job.owner_email, "admin", job.model_id,
                 job.model_version, job.tool_policy_digest))
        if row[8] is not None:
            _require(row[8] == row[0] and row[9] == row[10] and 0 <= row[11] <= 256*1024,
                     "模型回执实际内容与保存摘要不一致")
    for index, row in enumerate(tools, 1):
        _require(row[1] == index and row[2] in ids and row[3] == row[4] and 0 <= row[5] <= 8192,
                 "工具派发参数摘要或所属模型调用不一致")
        if row[7] is not None:
            _require(row[7] == row[0] and row[8] == row[9] and 0 <= row[10] <= 256*1024,
                     "工具回执实际内容与保存摘要不一致")
    return {"jobId":job.id, "providerCount":len(providers), "toolCount":len(tools),
            "digest":digest([providers, tools])}


def fence(report, principal):
    """Compare before/after full verification and again inside commit mutation."""
    current_principal(principal, admin=True)
    _require(connection.vendor == "postgresql", "内容账本栅栏需要PostgreSQL实际SHA256")
    actual, snapshot, reference, _, _, _ = runtime.bound(report, principal)
    flow = actual.workflow
    saved, _, _ = store._loaded(snapshot["screeningIntent"]["id"], principal)
    definitions = contract.graph(bool(actual.budget_plan_id))["nodes"][:5]
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id, node_key__in=contract.ROLES).order_by("position")[:6])
    _require(len(nodes) == 5 and len({node.agent_job_id for node in nodes}) == 5)
    found = list(m.AiAgentJobs.objects.filter(pk__in=[node.agent_job_id for node in nodes], workflow_run_id=flow.id)[:6])
    _require(len(found) == 5)
    jobs = {job.id:job for job in found}
    ledger, roots = [], []
    for index, (node, definition) in enumerate(zip(nodes, definitions)):
        job = jobs.get(node.agent_job_id)
        _require(job is not None and node.position == index and node.node_key == definition["key"]
            and node.node_type == "agent" and node.instruction == definition["instruction"]
            and node.depends_on_json == canonical(definition["dependsOn"])
            and job.workflow_node_key == node.node_key and job.task == node.instruction
            and job.input_json == node.input_json and job.output_json == node.output_json
            and job.owner_email == flow.owner_email and job.scope_json == flow.scope_json
            and all(getattr(job, key) == getattr(flow, key) for key in
                ("model_id", "model_version", "tool_policy_digest", "allowed_tools_json")))
        roots.append([node.id, node.version, node.status, node.agent_job_id, job.version, job.status,
            job.cancel_requested, job.lease_epoch, job.lease_token, job.provider_round_count, job.tool_call_count,
            digest(job.input_json), digest(job.output_json), digest(job.task)])
        ledger.append(_ledger(job))
    with connection.cursor() as cursor:
        cursor.execute(GUIDANCE, [[flow.id, *jobs]])
        guidance = cursor.fetchall()
    _require(len(guidance) <= 6 and all(0 <= row[1] <= 65536 for row in guidance))
    value = {"schemaVersion":SCHEMA, "reportId":actual.id, "workflowId":flow.id,
        "rootDigest":digest([snapshot, reference, store._reference(saved), flow.owner_email, flow.scope_json,
            flow.version, flow.status, flow.cancel_requested, flow.graph_json, flow.graph_digest,
            flow.model_id, flow.model_version, flow.allowed_tools_json, flow.tool_policy_digest, roots, guidance]),
        "ledgerDigest":digest(ledger)}
    value["fenceDigest"] = digest(value)
    runtime.bound(actual, principal)
    current_principal(principal, admin=True)
    return value
