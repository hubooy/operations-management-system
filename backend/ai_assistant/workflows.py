"""Durable one-call microsteps; serial DAGs and pinned parallel business DAGs."""

from __future__ import annotations
import json
from datetime import timedelta
from django.conf import settings
from django.db.models import Q, F, Sum, Min
from django.utils import timezone
from sales.auth import Principal
from . import models as m, provider, transport
from .chat import SYSTEM, dispatch_budget
from .configuration import resolve_model
from .policy import (
    AiError,
    authorize_owner,
    boolean,
    canonical,
    cas,
    choice,
    current_principal,
    digest,
    fields,
    identifier,
    mutation,
    owned,
    page,
    passive,
    record,
    text,
    uid,
)

AGENT_FIELDS = "id client_request_id task input_json state_json output_json status phase step_index version retryable resume_count attempt_count model_id model_version allowed_tools_json tool_policy_digest provider_round_count tool_call_count provider_dispatch_started_at workflow_run_id workflow_node_key error_code error_message started_at completed_at created_at updated_at"
WORKFLOW_FIELDS = "id client_request_id name graph_json input_json output_json dry_run status current_node_key version retryable resume_count attempt_count model_id model_version allowed_tools_json tool_policy_digest provider_round_count tool_call_count provider_dispatch_started_at error_code error_message started_at completed_at created_at updated_at"
JSON_FIELDS = {
    "input_json",
    "state_json",
    "output_json",
    "graph_json",
    "allowed_tools_json",
    "depends_on_json",
}
ACTIVE = ["queued", "running", "paused", "waiting_review"]


def mapping(row, detail=True):
    workflow = isinstance(row, m.AiWorkflowRuns)
    result = record(
        row,
        WORKFLOW_FIELDS if workflow else AGENT_FIELDS,
        json_fields=JSON_FIELDS,
        bool_fields={"retryable", "dry_run"},
    )
    if detail and workflow:
        result["nodes"] = []
        for node in m.AiWorkflowNodeRuns.objects.filter(run_id=row.id).order_by(
            "position"
        )[:24]:
            item = record(
                node,
                "id position instruction depends_on_json input_json output_json status version agent_job_id reviewer_email reviewed_at error_code error_message started_at completed_at",
                json_fields=JSON_FIELDS,
            )
            item.update(key=node.node_key, type=node.node_type)
            result["nodes"].append(item)
    elif detail:
        result["checkpoints"] = [
            record(
                v,
                "ordinal kind state_json output_digest created_at",
                json_fields=JSON_FIELDS,
            )
            for v in m.AiAgentCheckpoints.objects.filter(job_id=row.id).order_by(
                "ordinal"
            )[:64]
        ]
    return result


def get(entity_id, principal, workflow=False):
    cls = m.AiWorkflowRuns if workflow else m.AiAgentJobs
    row = cls.objects.filter(id=identifier(entity_id)).first()
    if not row:
        raise AiError("任务不存在", "not_found", 404)
    return authorize_owner(row, principal)


def listing(params, principal, workflow=False):
    fields(params, {"page", "pageSize"})
    cls = m.AiWorkflowRuns if workflow else m.AiAgentJobs
    return page(
        owned(cls.objects.all(), principal).order_by("-created_at", "id"),
        params,
        mapper=lambda r: mapping(r, False),
    )


def validate_graph(value):
    fields(value, {"nodes"}, {"nodes"})
    passive(value, 48 * 1024)
    nodes = value["nodes"]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 24:
        raise AiError("工作流节点数必须为 1–24")
    by_key = {}
    output = []
    for node in nodes:
        fields(
            node,
            {"key", "type", "dependsOn", "instruction"},
            {"key", "type", "instruction"},
        )
        key = identifier(node["key"], "key")
        if key in by_key:
            raise AiError("工作流节点键重复")
        deps = node.get("dependsOn", [])
        if (
            not isinstance(deps, list)
            or len(deps) > 8
            or len(set(deps)) != len(deps)
            or key in deps
        ):
            raise AiError("节点依赖无效")
        by_key[key] = {
            "key": key,
            "type": choice(node["type"], ["agent", "human_review"], "type"),
            "dependsOn": [identifier(d) for d in deps],
            "instruction": text(node["instruction"], "instruction", 4000),
        }
    depths = {}
    while len(output) < len(nodes):
        ready = [
            v
            for k, v in by_key.items()
            if k not in depths and all(d in depths for d in v["dependsOn"])
        ]
        if not ready:
            raise AiError("工作流有环或依赖不存在")
        for node in ready:
            depth = 1 + max([depths[d] for d in node["dependsOn"]] or [0])
            if depth > 16:
                raise AiError("工作流深度超限")
            depths[node["key"]] = depth
            output.append(node)
    return {"nodes": output}


def admission(principal, model_id=None, *, surface="ai_agent"):
    current_principal(principal, write=True)
    model = resolve_model(model_id)
    entries = transport.catalog(principal, surface)
    if not entries or len(entries) > 64:
        raise AiError("Agent 工具目录无效", "service_unavailable", 503)
    return {
        "model_id": model.id,
        "model_version": model.version,
        "allowed_tools_json": canonical([e["name"] for e in entries]),
        "tool_policy_digest": digest(entries),
    }, entries


def event(row, principal, kind, previous=None, node=None):
    values = {
        "id": uid("ai-event"),
        "owner_email": row.owner_email,
        "actor_email": principal.email,
        "event_type": kind,
        "from_status": previous or "",
        "to_status": row.status,
        "details_json": "{}",
    }
    if isinstance(row, m.AiWorkflowRuns):
        m.AiWorkflowEvents.objects.create(
            run_id=row.id, run_version=row.version, node_key=node, **values
        )
    else:
        m.AiAgentEvents.objects.create(job_id=row.id, job_version=row.version, **values)


def create(body, principal, workflow=False, *, skill_ids=None, library_snapshot=None, execution_profile=None, budget_prepared=None, integrated_prepared=None):
    allowed = {"clientRequestId", "input", "modelId"} | (
        {"name", "graph", "dryRun"} if workflow else {"task"}
    )
    fields(
        body,
        allowed,
        {"clientRequestId", "name", "graph"}
        if workflow
        else {"clientRequestId", "task"},
    )
    client = identifier(body["clientRequestId"])
    input_value = passive(body.get("input", {}))
    dry = boolean(body.get("dryRun", False), "dryRun") if workflow else 0
    graph = validate_graph(body["graph"]) if workflow else None
    title = text(
        body["name" if workflow else "task"],
        "name" if workflow else "task",
        120 if workflow else 8000,
    )
    if execution_profile is not None:
        from . import business_reports
        if not workflow or execution_profile not in {business_reports.V2_PROFILE, business_reports.BUDGET_PROFILE, business_reports.integrated.PROFILE}:
            raise AiError("工作流执行协议无效")
        if execution_profile == business_reports.BUDGET_PROFILE:
            from .business_budget_store import PreparedBudget
            from .policy import _mutation_depth
            if type(budget_prepared) is not PreparedBudget or _mutation_depth.get() < 1:
                raise AiError("预算工作流仅可在完整报告创建事务中使用固定参数")
        if execution_profile == business_reports.integrated.PROFILE:
            from .policy import _mutation_depth
            if type(integrated_prepared) is not business_reports.integrated.Prepared or _mutation_depth.get() < 1:
                raise AiError("集成工作流仅可在完整报告事务中使用固定关联参数")
    if integrated_prepared is not None and execution_profile != "business-agent-integrated-reference-v1":
        raise AiError("关联参数与工作流协议不一致")
    if budget_prepared is not None and execution_profile != "business-agent-budget-reference-v1":
        raise AiError("预算参数与工作流执行协议不一致")
    surface = ("business_agent_integrated_v1" if execution_profile == "business-agent-integrated-reference-v1" else
               "business_agent_budget_v1" if execution_profile == "business-agent-budget-reference-v1" else
               "business_agent_v2" if execution_profile is not None else "ai_agent")
    admitted, entries = (admission(principal, body.get("modelId"), surface=surface) if execution_profile is not None
                         else admission(principal, body.get("modelId"))) if not dry else ({}, [])
    request_digest = digest({"payload": body, "admission": admitted})
    if execution_profile is not None:
        request_digest = digest({"payload": body, "admission": admitted, "executionProfile": execution_profile})
    cls = m.AiWorkflowRuns if workflow else m.AiAgentJobs
    with mutation(principal):
        existing = cls.objects.filter(
            owner_email=principal.email.lower(), client_request_id=client
        ).first()
        if existing:
            authorize_owner(existing, principal)
            if existing.request_digest != request_digest:
                raise AiError("请求标识已绑定不同内容或执行策略", "conflict", 409)
            return {"item": mapping(existing), "replayed": True}
        active = cls.objects.filter(
            status__in=ACTIVE if workflow else ["queued", "running"]
        )
        if active.count() >= (24 if workflow else 64) or active.filter(
            owner_email=principal.email.lower()
        ).count() >= (4 if workflow else 8):
            raise AiError("活动任务达到上限", "rate_limited", 429)
        values = {
            "id": uid("ai-workflow" if workflow else "ai-agent"),
            "owner_email": principal.email.lower(),
            "client_request_id": client,
            "request_digest": request_digest,
            "scope_json": canonical(principal.scope),
            "input_json": canonical(input_value),
            **admitted,
        }
        if workflow:
            values.update(
                name=title,
                graph_json=canonical(graph),
                graph_digest=digest(graph),
                dry_run=dry,
            )
        else:
            values.update(task=title)
        row = cls.objects.create(**values)
        if not dry:
            from . import report_library
            report_library.pin(row.id, title, None, entries, skill_ids=skill_ids, library=library_snapshot)
        if execution_profile is not None:
            if execution_profile == business_reports.integrated.PROFILE:
                from .business_integrated_preflight import preflight
                preflight(row, principal, graph, entries, prepared=integrated_prepared)
            elif execution_profile == business_reports.BUDGET_PROFILE:
                from .business_budget_preflight import preflight
                preflight(row, principal, graph, entries, prepared=budget_prepared)
            else:
                business_reports.preflight_v2(row, principal, graph, entries)
        if workflow:
            for position, node in enumerate(graph["nodes"]):
                m.AiWorkflowNodeRuns.objects.create(
                    id=uid("ai-node"),
                    run_id=row.id,
                    node_key=node["key"],
                    position=position,
                    node_type=node["type"],
                    depends_on_json=canonical(node["dependsOn"]),
                    instruction=node["instruction"],
                )
        event(row, principal, "created")
        return {"item": mapping(row), "replayed": False}


def control(entity_id, body, principal, action, workflow=False):
    fields(body, {"expectedVersion"}, {"expectedVersion"})
    with mutation(principal):
        row = get(entity_id, principal, workflow)
        cas(row, body["expectedVersion"])
        previous = row.status
        if action == "cancel":
            if row.status not in ACTIVE:
                raise AiError("任务已结束", "conflict", 409)
            row.status = "cancelled"
            row.cancel_requested = 1
            row.completed_at = timezone.now()
            if workflow:
                m.AiWorkflowNodeRuns.objects.filter(
                    run_id=row.id, status__in=["pending", "running", "waiting_review"]
                ).update(status="cancelled", completed_at=timezone.now())
                m.AiAgentJobs.objects.filter(
                    workflow_run_id=row.id, status__in=ACTIVE
                ).update(
                    status="cancelled",
                    phase="cancelled",
                    cancel_requested=1,
                    lease_token="",
                    lease_expires_at=None,
                    version=F("version") + 1,
                    completed_at=timezone.now(),
                )
            else:
                row.phase = "cancelled"
        else:
            if (
                row.status not in {"paused", "failed"}
                or not row.retryable
                or row.resume_count >= 16
            ):
                raise AiError("此任务不能恢复", "conflict", 409)
            from . import business_integrated
            report = m.AiReportRun.objects.filter(workflow_id=row.id if workflow else row.workflow_run_id).first()
            if report and business_integrated.is_snapshot(json.loads(report.snapshot_json)):
                business_integrated.bound(report, principal)
            row.status = "queued"
            row.resume_count += 1
            row.completed_at = None
            row.error_code = ""
            row.error_message = ""
            row.next_run_at = timezone.now()
            if not workflow:
                row.phase = "queued"
            elif row.current_node_key:
                node = m.AiWorkflowNodeRuns.objects.get(
                    run_id=row.id, node_key=row.current_node_key
                )
                if node.agent_job_id:
                    child = m.AiAgentJobs.objects.get(id=node.agent_job_id)
                    control(
                        child.id,
                        {"expectedVersion": child.version},
                        principal,
                        "resume",
                    )
                    node.status = "running"
                    node.version += 1
                    node.save()
        row.version += 1
        row.lease_token = ""
        row.lease_expires_at = None
        row.updated_at = timezone.now()
        row.save()
        event(
            row, principal, "cancelled" if action == "cancel" else "resumed", previous
        )
        return {"item": mapping(row)}


def review(run_id, node_key, body, principal, *, commit=None):
    fields(
        body,
        {"expectedVersion", "decision", "comment"},
        {"expectedVersion", "decision"},
    )
    decision = choice(body["decision"], ["approve", "reject"], "decision")
    comment = text(body.get("comment", ""), "comment", 2000, empty=True)
    screening_review = promotion_review = None
    if decision == "approve":
        from . import business_screening_readiness, business_screening_content
        from . import business_promotion_readiness, business_promotion_review
        candidate = get(run_id,principal,True)
        promotion_report = business_promotion_readiness.report_for(candidate)
        if promotion_report is not None:
            promotion_review = business_promotion_review.prepare_review(promotion_report.id,principal)
        else:
            screening_report = business_screening_readiness.report_for(candidate)
            if screening_report is not None:
                screening_review = business_screening_content.prepare_review(screening_report,principal)
    with mutation(principal):
        row = get(run_id, principal, True)
        if decision == "approve":
            if promotion_review is not None:
                business_promotion_review.revalidate_review(promotion_review,principal)
            elif screening_review is not None:
                business_screening_content.revalidate_review(screening_review,principal)
            else:
                from . import reports
                reports.validate_review(row.id, principal)
        node = m.AiWorkflowNodeRuns.objects.filter(
            run_id=row.id, node_key=identifier(node_key)
        ).first()
        if (
            not node
            or node.node_type != "human_review"
            or node.status != "waiting_review"
            or row.status != "waiting_review"
            or row.current_node_key != node.node_key
        ):
            raise AiError("节点不在待复核状态", "conflict", 409)
        cas(node, body["expectedVersion"])
        previous = row.status
        node.status = "completed" if decision == "approve" else "rejected"
        node.output_json = canonical({"decision": decision, "comment": comment})
        node.reviewer_email = principal.email
        node.reviewed_at = timezone.now()
        node.completed_at = timezone.now()
        node.version += 1
        node.save()
        row.status = "queued" if decision == "approve" else "failed"
        row.current_node_key = None if decision == "approve" else node.node_key
        row.version += 1
        row.retryable = 0
        row.completed_at = None if decision == "approve" else timezone.now()
        row.save()
        event(
            row,
            principal,
            "review_approved" if decision == "approve" else "review_rejected",
            previous,
            node.node_key,
        )
        result = {"item": mapping(row)}
        return commit(result,200) if commit is not None else result


def background(row):
    from access_control.models import AppUser

    if row.owner_email.lower() == "local-admin@teruisi.local":
        principal = Principal(
            row.owner_email, "本地管理员", "admin", json.loads(row.scope_json)
        )
    else:
        user = AppUser.objects.filter(email=row.owner_email, status="active").first()
        if not user:
            raise AiError("任务所有者已失效", "owner_authorization_changed", 403)
        principal = Principal(
            user.email, user.display_name, user.role_id, json.loads(row.scope_json)
        )
    return current_principal(principal, write=True, background=True)


def _fail(row, principal, code):
    previous = row.status
    row.status = "failed"
    row.retryable = 0
    if isinstance(row, m.AiAgentJobs):
        unknown = (
            m.AiAgentProviderDispatches.objects.filter(
                job_id=row.id, state="calling"
            ).exists()
            or m.AiAgentToolDispatches.objects.filter(
                job_id=row.id, state="calling"
            ).exists()
        )
        row.retryable = int(
            not unknown
            and code
            in {"rate_limited", "ai_chat_quota_exceeded", "service_unavailable"}
        )
        for cls in [m.AiAgentProviderDispatches, m.AiAgentToolDispatches]:
            cls.objects.filter(job_id=row.id, state="calling").update(
                state="unknown",
                error_code=code,
                error_message="外部派发结果未获确认，禁止自动重放",
                completed_at=timezone.now(),
            )
    row.error_code = code
    row.error_message = "执行未获确认或当前权限/配置已变化，请检查任务记录"
    row.lease_token = ""
    row.lease_expires_at = None
    row.completed_at = timezone.now()
    row.updated_at = timezone.now()
    row.version += 1
    if isinstance(row, m.AiAgentJobs):
        row.phase = "failed"
    row.save()
    event(row, principal, "failed", previous)
    return {"status": "failed", "jobId": row.id, "errorCode": code}


def _normalize_result(value):
    if "frame" in value:
        return value
    calls = [
        {"id": v.get("id"), "name": v.get("name"), "arguments": v.get("arguments", {})}
        for v in value.get("toolCalls", [])
    ]
    return {
        "text": value.get("text", ""),
        "calls": calls,
        "frame": value.get("assistantFrame"),
        "usage": value.get("usage", {}),
        "providerRequestId": value.get("providerRequestId", ""),
    }


def prepared_candidate(query):
    row = query.order_by("updated_at", "created_at", "id").first()
    if not row:
        return None, None, None
    try:
        return row, background(row), None
    except AiError as error:
        return row, Principal(row.owner_email, "", "operator", None), error


def agent_candidates():
    return m.AiAgentJobs.objects.filter(
        Q(status="queued") | Q(status="running", lease_expires_at__lte=timezone.now()),
        next_run_at__lte=timezone.now(),
    )


def agent_tick(*, job_id=None):
    eligible = agent_candidates()
    if job_id is not None:
        eligible = eligible.filter(pk=job_id)
    candidate, principal, error = prepared_candidate(eligible)
    if not candidate:
        return {"status": "idle"}
    with mutation():
        now = timezone.now()
        row = eligible.filter(pk=candidate.pk).first()
        if not row:
            return {"status": "idle"}
        if error:
            return _fail(row, principal, error.code)
        try:
            current_principal(principal, write=True)
        except AiError as e:
            return _fail(row, principal, e.code)
        if row.step_index >= 64:
            return _fail(row, principal, "microstep_limit_exceeded")
        row.status = "running"
        row.phase = "executing"
        row.lease_token = uid("lease")
        row.lease_epoch += 1
        row.lease_expires_at = now + timedelta(seconds=240)
        row.started_at = row.started_at or now
        row.attempt_count += 1
        row.version += 1
        row.save()
        lease = (row.id, row.lease_token, row.lease_epoch)
    try:
        from . import business_reports
        surface = business_reports.execution_surface(row, principal)
        admitted, entries = (admission(principal, row.model_id) if surface == "ai_agent"
                             else admission(principal, row.model_id, surface=surface))
        if any(getattr(row, k) != v for k, v in admitted.items()):
            raise AiError("执行策略已变化", "executor_policy_changed", 409)
        from . import business_reports
        entries = business_reports.restricted_entries(row, entries)
        model = resolve_model(row.model_id)
        screening_step = screening_answer = None
        promotion_step = promotion_permission = promotion_answer = None
        from . import business_screening_execution, business_screening_runtime_contract
        if surface == business_screening_runtime_contract.SURFACE:
            screening_step = business_screening_execution.prepare(row,principal)
        from . import business_promotion_runtime_contract, business_promotion_microstep
        if surface == business_promotion_runtime_contract.SURFACE:
            from . import business_promotion_execution
            promotion_step = business_promotion_microstep.prepare(row, principal)
            if getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is True:
                from . import business_promotion_runtime_permission
                promotion_permission = business_promotion_runtime_permission.prepare(
                    promotion_step.proof["reportId"], principal, row.workflow_node_key)
        providers = list(
            m.AiAgentProviderDispatches.objects.filter(job_id=row.id).order_by(
                "dispatch_ordinal"
            )[:62]
        )
        tool_dispatches = list(
            m.AiAgentToolDispatches.objects.filter(job_id=row.id).order_by(
                "tool_call_ordinal"
            )[:74]
        )
        frames = [
            {
                "role": "user",
                "content": row.task
                + "\n<task_input>"
                + row.input_json.replace("<", "\\u003c")
                + "</task_input>",
            }
        ]
        pending = None
        final = None
        for dispatched in providers:
            saved = m.AiAgentProviderResults.objects.filter(
                dispatch_id=dispatched.id
            ).first()
            if not saved:
                raise AiError("派发结果未知", "provider_dispatch_unknown", 409)
            response = _normalize_result(json.loads(saved.response_json))
            frames.append(response["frame"])
            if not response["calls"]:
                final = response["text"]
                break
            outputs = []
            for call in response["calls"]:
                existing = next(
                    (
                        d
                        for d in tool_dispatches
                        if d.provider_dispatch_id == dispatched.id
                        and d.provider_call_id == call["id"]
                    ),
                    None,
                )
                result = (
                    m.AiAgentToolResults.objects.filter(
                        tool_dispatch_id=existing.id
                    ).first()
                    if existing
                    else None
                )
                if existing and not result:
                    raise AiError("工具派发结果未知", "tool_dispatch_unknown", 409)
                if not existing:
                    pending = (dispatched, call)
                    break
                outputs.append(json.loads(result.result_json))
            if pending:
                break
            frames += provider.tool_frames(model, response["calls"], outputs)
        if len(canonical(frames).encode()) > 192 * 1024:
            raise AiError("任务上下文超限", "transcript_limit_exceeded", 409)
        if screening_step is not None and final is not None:
            screening_answer = business_screening_execution.validate_answer(screening_step,final,principal)
        if promotion_step is not None and final is not None and promotion_permission is not None:
            from . import business_promotion_execution
            promotion_answer = business_promotion_execution.validate_final(row, final, principal)
        if promotion_step is not None and pending is None and final is None and promotion_permission is not None:
            business_promotion_microstep.validate_provider_turn(
                promotion_step, frames, entries, model, principal)
        with mutation(principal, background=True):
            row = _leased(lease)
            # The 0060/0064 market snapshots are not recognized by the older
            # business_reports.context helper.  Fence this family before a
            # generic provider *or* tool dispatch can be reserved.
            from . import business_market_v2_paid_gate
            business_market_v2_paid_gate.before_reservation(row)
            if promotion_step is not None:
                business_promotion_microstep.check(promotion_step, row, principal)
                if promotion_permission is None:
                    raise AiError("词货运行许可尚未开放，不能预留模型或工具派发",
                        "promotion_runtime_not_ready", 409)
                business_promotion_runtime_permission.authorize_dispatch(
                    promotion_permission, row, principal)
            if screening_step is not None:
                business_screening_execution.check(screening_step,row,principal)
            current = resolve_model(row.model_id)
            if current.version != row.model_version:
                raise AiError("模型版本已变化", "model_version_changed", 409)
            if final is not None:
                return _complete(row, principal, final,
                    **({"screening_step":screening_step,"screening_answer":screening_answer} if screening_step is not None else {}),
                    **({"promotion_permission":promotion_permission,"promotion_answer":promotion_answer}
                        if promotion_step is not None else {}))
            if pending:
                parent, call = pending
                business_reports.validate_call(row, call, principal,
                    **({"screening_step":screening_step} if screening_step is not None else {}),
                    **({"promotion_step":promotion_step,"promotion_permission":promotion_permission}
                        if promotion_step is not None else {}))
                entry = next((e for e in entries if e["name"] == call["name"]), None)
                if (
                    not entry
                    or len(tool_dispatches) >= min(model.max_total_tool_calls, 40)
                    or sum(d.tool_name == call["name"] for d in tool_dispatches)
                    >= entry["execution"]["maxCallsPerRequest"]
                ):
                    raise AiError("工具未获准或调用超限", "tool_limit_exceeded", 409)
                dispatch = m.AiAgentToolDispatches.objects.create(
                    id=uid("ai-agent-tool"),
                    job_id=row.id,
                    provider_dispatch_id=parent.id,
                    tool_call_ordinal=len(tool_dispatches) + 1,
                    provider_call_id=call["id"],
                    tool_name=call["name"],
                    arguments_json=canonical(call["arguments"]),
                    arguments_digest=digest(call["arguments"]),
                    invocation_id=uid("ai-invocation"),
                    lease_epoch=row.lease_epoch,
                )
            else:
                if promotion_step is None:
                    business_reports.validate_provider_turn(row, principal,
                        **({"screening_step":screening_step} if screening_step is not None else {}))
                if len(providers) >= min(model.max_tool_rounds, 20):
                    raise AiError("模型轮数超限", "provider_limit_exceeded", 409)
                dispatch_budget(principal.email.lower(), model.id)
                dispatch = m.AiAgentProviderDispatches.objects.create(
                    id=uid("ai-agent-provider"),
                    job_id=row.id,
                    dispatch_ordinal=len(providers) + 1,
                    owner_email=principal.email.lower(),
                    actor_role=principal.role,
                    model_id=model.id,
                    model_version=model.version,
                    tool_policy_digest=row.tool_policy_digest,
                    request_digest=digest(frames),
                    lease_epoch=row.lease_epoch,
                )
                row.provider_dispatch_started_at = (
                    row.provider_dispatch_started_at or timezone.now()
                )
                row.save(update_fields=["provider_dispatch_started_at"])
        result = (
            transport.execute_tool(
                call["name"],
                call["arguments"],
                principal,
                surface=surface,
                request_id=dispatch.id if promotion_step is not None else dispatch.invocation_id,
                provider_call_id=call["id"],
                policy_digest=row.tool_policy_digest,
            )
            if pending
            else provider.turn(model, frames, SYSTEM + execution_guidance(row.id), entries)
        )
        passive(result, 256 * 1024)
        if result.get("auditStatus") == "unavailable":
            raise AiError("工具审计不可用", "audit_unavailable", 503)
        if screening_step is not None and not pending and not result["calls"]:
            # Preserve the known external outcome before expensive owning
            # numeric validation. A later failure must not erase and replay it.
            with mutation(principal, background=True):
                row = _leased(lease)
                business_screening_execution.check(screening_step,row,principal,dispatch=dispatch)
                m.AiAgentProviderResults.objects.create(dispatch_id=dispatch.id,response_json=canonical(result),
                    response_digest=digest(result),usage_json=canonical(result.get("usage",{})),
                    provider_request_id=result.get("providerRequestId",""))
                row.provider_round_count += 1
                row.save(update_fields=["provider_round_count"])
                dispatch.state,dispatch.completed_at = "succeeded",timezone.now()
                dispatch.save()
            screening_answer = business_screening_execution.validate_answer(screening_step,result["text"],principal)
            with mutation(principal, background=True):
                row = _leased(lease)
                business_screening_execution.check(screening_step,row,principal,dispatch=dispatch)
                return _complete(row,principal,result["text"],screening_step=screening_step,screening_answer=screening_answer)
        if promotion_step is not None and not pending and not result["calls"]:
            # A known final provider response is saved before expensive full
            # Agent read and numeric diagnosis. Failure must retain this exact
            # known result and must never ask the model for it again.
            with mutation(principal, background=True):
                row = _leased(lease)
                business_promotion_runtime_permission.authorize_dispatch(
                    promotion_permission, row, principal, result_commit=True)
                _promotion_result_dispatch(row, dispatch, pending=False)
                m.AiAgentProviderResults.objects.create(dispatch_id=dispatch.id,
                    response_json=canonical(result), response_digest=digest(result),
                    usage_json=canonical(result.get("usage", {})),
                    provider_request_id=result.get("providerRequestId", ""))
                row.provider_round_count += 1
                row.save(update_fields=["provider_round_count"])
                dispatch.state, dispatch.completed_at = "succeeded", timezone.now()
                dispatch.save()
            promotion_answer = business_promotion_execution.validate_final(
                row, result["text"], principal)
            with mutation(principal, background=True):
                row = _leased(lease)
                business_promotion_runtime_permission.authorize_dispatch(
                    promotion_permission, row, principal, result_commit=True)
                business_promotion_execution.check(promotion_answer, row,
                    result["text"], principal)
                return _complete(row, principal, result["text"],
                    promotion_permission=promotion_permission,
                    promotion_answer=promotion_answer,
                    promotion_result_commit=True)
        with mutation(principal, background=True):
            row = _leased(lease)
            if screening_step is not None:
                business_screening_execution.check(screening_step,row,principal,dispatch=dispatch)
            if promotion_step is not None:
                business_promotion_runtime_permission.authorize_dispatch(
                    promotion_permission, row, principal, result_commit=True)
                _promotion_result_dispatch(row, dispatch, pending=bool(pending))
            if pending:
                m.AiAgentToolResults.objects.create(
                    tool_dispatch_id=dispatch.id,
                    result_json=canonical(result),
                    result_digest=digest(result),
                )
                row.tool_call_count += 1
            else:
                m.AiAgentProviderResults.objects.create(
                    dispatch_id=dispatch.id,
                    response_json=canonical(result),
                    response_digest=digest(result),
                    usage_json=canonical(result.get("usage", {})),
                    provider_request_id=result.get("providerRequestId", ""),
                )
                row.provider_round_count += 1
            dispatch.state = "succeeded"
            dispatch.completed_at = timezone.now()
            dispatch.save()
            if not pending and not result["calls"]:
                return _complete(row, principal, result["text"])
            row.step_index += 1
            row.state_json = canonical(
                {
                    "executor": "django-ai-v1",
                    "providerRoundCount": row.provider_round_count,
                    "toolCallCount": row.tool_call_count,
                }
            )
            row.status = "queued"
            row.phase = "queued"
            row.lease_token = ""
            row.lease_expires_at = None
            row.version += 1
            row.save()
            m.AiAgentCheckpoints.objects.create(
                id=uid("ai-checkpoint"),
                job_id=row.id,
                ordinal=row.step_index,
                kind="checkpoint",
                state_json=row.state_json,
                output_digest="",
            )
            event(row, principal, "checkpoint", "running")
            return {"status": "checkpoint", "jobId": row.id}
    except Exception as error:
        with mutation():
            try:
                row = _leased(lease)
            except AiError:
                return {"status": "lease_lost", "jobId": lease[0]}
            return _fail(
                row,
                principal,
                error.code if isinstance(error, AiError) else "execution_failed",
            )


def _leased(lease):
    row = m.AiAgentJobs.objects.get(id=lease[0])
    if (
        row.lease_token != lease[1]
        or row.lease_epoch != lease[2]
        or not row.lease_expires_at
        or row.lease_expires_at <= timezone.now()
        or row.cancel_requested
        or row.status != "running"
    ):
        raise AiError("租约已失效", "lease_lost", 409)
    return row


def _promotion_result_dispatch(job, dispatch, *, pending):
    """Local post-call CAS for the one reservation made by this microstep.

    The prepared prefix cannot be checked unchanged after reservation. This
    checks the exact new row and lease instead; no old profile calls it.
    """
    cls = m.AiAgentToolDispatches if pending else m.AiAgentProviderDispatches
    if type(dispatch) is not cls:
        raise AiError("词货派发类型与已知结果不一致", "conflict", 409)
    saved = cls.objects.filter(pk=dispatch.id, job_id=job.id,
        lease_epoch=job.lease_epoch, state="calling").first()
    result_cls = m.AiAgentToolResults if pending else m.AiAgentProviderResults
    result_filter = {"tool_dispatch_id":dispatch.id} if pending else {"dispatch_id":dispatch.id}
    if saved is None or result_cls.objects.filter(**result_filter).exists():
        raise AiError("词货派发已变化或结果已存在，禁止重放", "tool_dispatch_unknown" if pending else "provider_dispatch_unknown", 409)
    if pending:
        provider_row = m.AiAgentProviderDispatches.objects.filter(
            pk=saved.provider_dispatch_id, job_id=job.id, state="succeeded").first()
        provider_result = (m.AiAgentProviderResults.objects.filter(
            dispatch_id=provider_row.id).first() if provider_row else None)
        if (saved.tool_call_ordinal != job.tool_call_count+1
                or saved.tool_name != dispatch.tool_name
                or saved.provider_call_id != dispatch.provider_call_id
                or saved.arguments_json != dispatch.arguments_json
                or saved.arguments_digest != digest(saved.arguments_json)
                or saved.invocation_id != dispatch.invocation_id
                or provider_result is None
                or provider_result.response_digest != digest(provider_result.response_json)):
            raise AiError("词货工具派发与已保存模型调用不一致", "tool_dispatch_unknown", 409)
    elif (saved.dispatch_ordinal != job.provider_round_count+1
            or saved.owner_email != job.owner_email or saved.actor_role != "admin"
            or saved.model_id != job.model_id or saved.model_version != job.model_version
            or saved.tool_policy_digest != job.tool_policy_digest
            or saved.request_digest != dispatch.request_digest):
        raise AiError("词货模型派发与当前租约或固定策略不一致", "provider_dispatch_unknown", 409)
    return saved


def _complete(row, principal, answer, *, screening_step=None, screening_answer=None,
              promotion_permission=None, promotion_answer=None,
              promotion_result_commit=False):
    from . import business_reports
    # A received v2 answer can fail proof validation. Keep its provider receipt
    # and succeeded dispatch in this transaction; never relabel it as unknown.
    if business_reports.has_v2_profile(row):
        try:
            business_reports.validate_output(row, answer, principal,
                **({"screening_step":screening_step,"screening_answer":screening_answer} if screening_step is not None else {}),
                **({"promotion_permission":promotion_permission,"promotion_answer":promotion_answer,
                    "promotion_result_commit":promotion_result_commit}
                    if promotion_answer is not None else {}))
            output = {"answer": text(answer, "answer", 12000)}
        except (AiError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            return _fail(row, principal, error.code if isinstance(error, AiError) else "business_output_validation_failed")
    else:
        business_reports.validate_output(row, answer, principal)
        output = {"answer": text(answer, "answer", 12000)}
    row.output_json = canonical(output)
    row.status = "completed"
    row.phase = "completed"
    row.step_index += 1
    row.version += 1
    row.lease_token = ""
    row.lease_expires_at = None
    row.completed_at = timezone.now()
    row.updated_at = timezone.now()
    row.save()
    m.AiAgentCheckpoints.objects.create(
        id=uid("ai-checkpoint"),
        job_id=row.id,
        ordinal=row.step_index,
        kind="completed",
        state_json=row.state_json,
        output_digest=digest(output),
    )
    event(row, principal, "completed", "running")
    return {"status": "completed", "jobId": row.id}


def workflow_tick():
    # No external request inside this transaction. A single domain mutex protects
    # child creation, stable node identity and parent observation atomically.
    from . import business_screening_readiness, business_screening_pipeline
    from . import business_promotion_readiness, business_promotion_postreview
    eligible = business_screening_readiness.available(m.AiWorkflowRuns.objects.filter(
        status__in=["queued", "running"], next_run_at__lte=timezone.now()
    ))
    candidate, principal, error = prepared_candidate(eligible)
    if not candidate:
        return {"status": "idle"}
    if not error and business_promotion_readiness.report_for(candidate) is not None:
        if business_promotion_postreview.is_approved_candidate(candidate):
            return business_promotion_postreview.advance(candidate, principal)
        if m.AiAgentJobs.objects.filter(workflow_run_id=candidate.id).exists():
            if getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is not True:
                return {"status": "promotion_runtime_disabled", "runId": candidate.id}
            from . import business_promotion_runtime_permission, business_promotion_pipeline
            from . import business_parallel
            report = business_promotion_readiness.report_for(candidate)
            prepared = business_promotion_runtime_permission.prepare(report.id, principal, "commerce")
            with mutation(principal):
                row = eligible.filter(pk=candidate.pk, version=candidate.version).first()
                if row is None:
                    return {"status": "idle"}
                business_promotion_runtime_permission.check(prepared, principal)
                nodes = business_promotion_pipeline.checked_nodes(row, prepared, principal)
                return business_parallel.workflow_step(row, principal, nodes)
        # This new profile may publish its fixed rule scan, then parks before
        # Agent creation until the independent four-tool admission is complete.
        return business_promotion_readiness.advance(candidate, principal)
    if not error and business_screening_readiness.report_for(candidate) is not None:
        return business_screening_readiness.advance(candidate,principal,
            on_ready=lambda row,prepared:business_screening_pipeline.step(row,prepared,principal))
    with mutation():
        row = eligible.filter(pk=candidate.pk, version=candidate.version).first()
        if not row:
            return {"status": "idle"}
        if error:
            return _fail(row, principal, error.code)
        try:
            current_principal(principal, write=True)
        except AiError as e:
            return _fail(row, principal, e.code)
        counts = m.AiAgentJobs.objects.filter(workflow_run_id=row.id).aggregate(
            rounds=Sum("provider_round_count"),
            tools=Sum("tool_call_count"),
            first=Min("provider_dispatch_started_at"),
        )
        if (
            row.provider_round_count != (counts["rounds"] or 0)
            or row.tool_call_count != (counts["tools"] or 0)
            or row.provider_dispatch_started_at != counts["first"]
        ):
            row.provider_round_count = counts["rounds"] or 0
            row.tool_call_count = counts["tools"] or 0
            row.provider_dispatch_started_at = counts["first"]
            row.save(
                update_fields=[
                    "provider_round_count",
                    "tool_call_count",
                    "provider_dispatch_started_at",
                ]
            )
        nodes = list(
            m.AiWorkflowNodeRuns.objects.filter(run_id=row.id).order_by("position")[:24]
        )
        from . import business_parallel
        if not row.dry_run and business_parallel.is_parallel(row.id):
            return business_parallel.workflow_step(row, principal, nodes)
        by_key = {n.node_key: n for n in nodes}
        active = next((n for n in nodes if n.status == "running"), None)
        if active:
            child = m.AiAgentJobs.objects.get(id=active.agent_job_id)
            if child.status in {"queued", "running"}:
                row.updated_at = timezone.now()
                row.save(update_fields=["updated_at"])
                return {"status": "waiting_agent", "runId": row.id}
            if child.status != "completed":
                if child.retryable:
                    row.status = "paused"
                    row.retryable = 1
                    row.version += 1
                    row.save()
                    event(row, principal, "paused", "running")
                    return {"status": "paused", "runId": row.id}
                return _fail(row, principal, "child_agent_failed")
            active.output_json = child.output_json
            active.status = "completed"
            active.completed_at = timezone.now()
            active.version += 1
            active.save()
            row.current_node_key = None
        elif all(n.status in {"completed", "skipped"} for n in nodes):
            output = {
                n.node_key: json.loads(n.output_json) if n.output_json else None
                for n in nodes
            }
            passive(output, 96 * 1024)
            row.output_json = canonical(output)
            row.status = "completed"
            row.completed_at = timezone.now()
            row.current_node_key = None
        else:
            ready = next(
                (
                    n
                    for n in nodes
                    if n.status == "pending"
                    and all(
                        by_key[d].status in {"completed", "skipped"}
                        for d in json.loads(n.depends_on_json)
                    )
                ),
                None,
            )
            if not ready:
                return _fail(row, principal, "workflow_no_ready_node")
            data = {
                "workflowInput": json.loads(row.input_json),
                "dependencies": {
                    d: json.loads(by_key[d].output_json)
                    for d in json.loads(ready.depends_on_json)
                },
            }
            passive(data, 24 * 1024)
            ready.input_json = canonical(data)
            ready.started_at = timezone.now()
            if row.dry_run:
                ready.output_json = canonical(
                    {"dryRun": True, "nodeKey": ready.node_key}
                )
                ready.status = "skipped"
                ready.completed_at = timezone.now()
                row.status = "queued"
            elif ready.node_type == "human_review":
                ready.status = "waiting_review"
                row.status = "waiting_review"
                row.current_node_key = ready.node_key
            else:
                active_jobs = m.AiAgentJobs.objects.filter(status__in=ACTIVE)
                if (
                    active_jobs.count() >= 64
                    or active_jobs.filter(owner_email=row.owner_email).count() >= 8
                ):
                    row.updated_at = timezone.now()
                    row.save(update_fields=["updated_at"])
                    return {"status": "waiting_capacity", "runId": row.id}
                child = m.AiAgentJobs.objects.create(
                    id=uid("ai-agent"),
                    owner_email=row.owner_email,
                    client_request_id="workflow-"
                    + digest([row.id, ready.node_key])[:64],
                    request_digest=digest([row.id, ready.node_key]),
                    scope_json=row.scope_json,
                    task=ready.instruction,
                    input_json=ready.input_json,
                    model_id=row.model_id,
                    model_version=row.model_version,
                    allowed_tools_json=row.allowed_tools_json,
                    tool_policy_digest=row.tool_policy_digest,
                    workflow_run_id=row.id,
                    workflow_node_key=ready.node_key,
                )
                from . import report_library
                report_library.pin(child.id, child.task, None, [], parent_id=row.id)
                event(child, principal, "created")
                ready.agent_job_id = child.id
                ready.status = "running"
                row.status = "running"
                row.current_node_key = ready.node_key
            ready.version += 1
            ready.save()
        previous = row.status
        row.version += 1
        row.started_at = row.started_at or timezone.now()
        row.updated_at = timezone.now()
        row.save()
        event(
            row,
            principal,
            "completed" if row.status == "completed" else "node_progressed",
            previous,
        )
        return {"status": row.status, "runId": row.id}


def execution_guidance(entity_id):
    row = m.AiExecutionGuidance.objects.filter(pk=entity_id).first()
    return json.loads(row.snapshot_json)["prompt"] if row else ""  # Legacy jobs keep their original prompt.
