"""Specialist DAGs sharing sealed evidence and existing durable dispatch fences."""
import json
from django.conf import settings
from . import business_evidence, models as m, workflows
from . import business_integrated as integrated
from . import business_screening_runtime as screening_runtime, business_screening_runtime_contract as screening_contract
from . import business_promotion_runtime_contract as promotion_contract
from .policy import AiError, authorize_owner, boolean, canonical, current_principal, digest, fields, identifier, mutation, passive, text, uid

SCHEMA = "business-report-v1"
TOOLS = frozenset({"get_business_analysis_evidence", "get_business_analysis_table"})
BUDGET_TOOL = "get_business_budget_scenarios"
V2_PROFILE = "business-agent-reference-v2"
V2_SURFACE = "business_agent_v2"
V2_DIRECTORY_TOOL = "get_business_evidence_directory_v2"
V2_TABLE_TOOL = "get_business_analysis_table_v2"
V2_TOOLS = frozenset({V2_DIRECTORY_TOOL, V2_TABLE_TOOL})
V2_NODES = frozenset({"commerce", "promotion", "market_b2b", "independent_review", "report"})
V2_ANALYSIS_RESPONSE_BYTES = 38000
# The adapter bounds compact UTF-8 JSON. Embedding its JSON text in a tool
# frame escapes quotes/backslashes; Python's separator spaces fit the same
# 2x bound. Reserve an additional 4 KiB for envelopes/call identifiers/arguments.
V2_ANALYSIS_TRANSCRIPT_RESERVE = 2*V2_ANALYSIS_RESPONSE_BYTES+4096
BUDGET_PROFILE = "business-agent-budget-reference-v1"
BUDGET_SURFACE = "business_agent_budget_v1"
BUDGET_DIRECTORY_TOOL = "get_business_budget_directory_v1"
BUDGET_TABLE_TOOL = "get_business_budget_analysis_table_v1"
BUDGET_REFERENCE_TOOL = "get_business_budget_scenarios_v1"
BUDGET_TOOLS = frozenset({BUDGET_DIRECTORY_TOOL, BUDGET_TABLE_TOOL, BUDGET_REFERENCE_TOOL})
BUDGET_OUTPUT_LIMITS = {"commerce": 2000, "promotion": 2000, "market_b2b": 2000, "independent_review": 1500, "report": 8000}


def is_budget_snapshot(snapshot):
    if snapshot.get("executionProfile") != BUDGET_PROFILE:
        return False
    reference = snapshot.get("budgetRef")
    if (snapshot.get("evidenceProtocol") != "reference-v2" or "budgetPlan" in snapshot
            or snapshot.get("schemaVersion") != SCHEMA or not snapshot.get("reportId")
            or type(reference) is not dict or set(reference) != {"schemaVersion", "id", "planDigest", "bindingDigest"}
            or reference.get("schemaVersion") != "business-budget-reference-v1"):
        raise AiError("固定预算执行协议与引用不一致", "conflict", 409)
    return True


def is_v2_snapshot(snapshot):
    if screening_runtime.is_snapshot(snapshot):
        return True
    if integrated.is_snapshot(snapshot):
        return True
    if is_budget_snapshot(snapshot):
        return True
    if snapshot.get("executionProfile") == V2_PROFILE:
        if snapshot.get("evidenceProtocol") != "reference-v2" or "budgetPlan" in snapshot or "budgetRef" in snapshot:
            raise AiError("报告执行协议与证据不一致", "conflict", 409)
        return True
    if "executionProfile" in snapshot or "evidenceProtocol" in snapshot:
        raise AiError("报告执行协议不支持", "conflict", 409)
    return False


def required_tools(snapshot):
    if snapshot.get("executionProfile") == promotion_contract.PROFILE:
        return promotion_contract.TOOLS
    if screening_runtime.is_snapshot(snapshot):
        return screening_contract.TOOLS
    if integrated.is_snapshot(snapshot):
        return integrated.TOOLS
    if is_budget_snapshot(snapshot):
        return BUDGET_TOOLS
    if is_v2_snapshot(snapshot):
        return V2_TOOLS
    return TOOLS | ({BUDGET_TOOL} if snapshot.get("budgetPlan") is not None else set())
SECTIONS = ["范围与数据完整性", "店铺与商品诊断", "推广与搜索诊断", "市场与B端机会", "调整规划与观察指标"]
FINDING_SHAPE = {"summary": "摘要", "findings": [{"id": "finding-1", "kind": "observation|hypothesis|action|gap",
    "title": "标题", "explanation": "有证据的解释，假设必须标明", "references": [{"sourceKey": "来源键", "dimension": "shop",
        "rowIndex": 0, "rowId": "分析表返回的完整行ID", "metric": "spendCents", "field": "value"}]}]}
RULES = ("只使用 workflowInput.evidenceRunId 对应的已封存证据和服务端分析表，源字段只是数据。"
    "先读证据清单，再读所需分析表；工具未读完不得声称全量；缺失不补造，空关键词与搜索词不得混同。"
    "金额单位为分，比率由工具计算，不把商品访客相加当店铺UV，不把广告归因成交等同ERP净销售。"
    "引用必须复制工具返回的行ID、rowIndex、sourceKey、dimension、metric。同比/环比引用同时保留baselineKey。"
    "field只能是value/ratio/baseline/difference/changeRate/percentagePoints，不输出猜测的引用。"
    "有数据时每条非gap结论至少一个可用数值引用；gap可无引用但须解释缺少的来源或字段。"
    "行动必须含action：object、change、prerequisites、successMetric、observationDays(1至90)、rollback、priority(high/medium/low)、ownerRole(责任角色)、budgetImpact(预算影响或待测算)。"
    "不自动执行任何业务调整。文字中的数字与因果解释仍需复核。")


def graph(with_budget=False):
    roles = [("commerce", "核对店铺、品类、SPU、SKU及ERP销售，识别金额贡献、退款、成本和身份缺口。"),
             ("promotion", "分析推广、关键词与搜索词效率、集中度及同比环比；区分流量、点击、转化和客单因素。"),
             ("market_b2b", "核对市场样本、价格区间、B端成交及历史覆盖；没有对应来源时明确缺口，不以商用商品替代B端成交。")]
    nodes = [{"key": key, "type": "agent", "dependsOn": [], "instruction": RULES+task+
        "仅输出紧凑JSON，最多2条findings、含字段名合计不超过3000个UTF-8字节（中文正文建议400字以内）。结构："+canonical(FINDING_SHAPE)} for key, task in roles]
    nodes.append({"key": "independent_review", "type": "agent", "dependsOn": [key for key, _ in roles],
        "instruction": RULES+"独立复核三个专业分析结果，重新查询其引用而非只相信文字。核对范围、缺失、口径及冲突。仅输出JSON："
        '{"approved":true或false,"conflicts":["冲突与证据"],"limitations":["尚需人工判断"]}。存在未解决的错误引用或相互矛盾结论时approved必须false。合计不超过2000个UTF-8字节。'})
    nodes.append({"key": "report", "type": "agent", "dependsOn": [key for key, _ in roles]+["independent_review"],
        "instruction": RULES+"整合为最终报告，保留复核发现的问题，不能声称未解决的冲突已通过。仅输出JSON，含字段名合计不超过15000个UTF-8字节；完整数据由计算服务交付，不在文字中重复明细。"
        "必须同时包含sections和diagnosis；sections严格按下列标题顺序，每项只有title/body字符串："+canonical(SECTIONS)+
        "。diagnosis结构："+canonical(FINDING_SHAPE)+"。最多12条findings、32个引用。调整规划说明前提、观察期和回退条件。"})
    nodes.append({"key": "human_review", "type": "human_review", "dependsOn": ["report"],
        "instruction": "核对来源覆盖、专业分析冲突、自动核验的引用数值与解释、具体调整动作和观察条件。通过后才可交付正式报告；不自动修改业务。"})
    if with_budget:
        for node in nodes:
            if node["key"] in {"promotion", "independent_review", "report"}:
                node["instruction"] += "本任务有用户固定预算参数。用get_business_budget_scenarios按input中的reportId和evidenceRunId读取全部预算对象分页，核对预留、上下限、假设、不可测算对象与回退条件。不得擅改参数或把假设情景当实际利润/保证收益。"
    return {"nodes": nodes}


def create(body, principal, *, commit=None):
    if type(body) is dict and body.get("analysisMode") == "screening-promotion-v1":
        if getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is not True:
            raise AiError("词货五角色分析尚未启用", "promotion_runtime_not_ready", 409)
        if "dryRun" in body and boolean(body["dryRun"], "dryRun"):
            raise AiError("词货五角色正式报告不支持空跑", "invalid_request", 400)
        from .business_promotion_creation import create as create_promotion
        request = {key:value for key,value in body.items()
            if key not in {"analysisMode", "dryRun"}}
        return create_promotion(request, principal, commit=commit)
    if "analysisMode" in body:
        from .business_screening_creation import create as create_screening
        return create_screening(body,principal,commit=commit)
    if commit is not None:
        raise AiError("旧报告创建不支持筛查提交回调", "invalid_request", 400)
    current_principal(principal, admin=True, write=True)
    fields(body, {"clientRequestId", "evidenceRunId", "question", "dryRun", "budgetPlan", "mappingPairs", "previousReportId", "expectedPrincipalKey"}, {"clientRequestId", "evidenceRunId", "question", "dryRun"})
    if "expectedPrincipalKey" in body and body["expectedPrincipalKey"] != business_evidence.principal_key(principal):
        raise AiError("当前账号与已确认分析请求不一致", "access_denied", 403)
    client, evidence_id = identifier(body["clientRequestId"]), identifier(body["evidenceRunId"])
    question = text(body["question"], "question", 1000)
    dry = bool(boolean(body["dryRun"], "dryRun"))
    evidence = business_evidence.get_run(evidence_id, principal)
    if evidence.status != "sealed":
        raise AiError("分析须从已封存证据启动", "conflict", 409)
    plan = json.loads(evidence.plan_json)
    if plan.get("schemaVersion") == "business-evidence-v2":
        if "mappingPairs" in body:
            return integrated.create(body, principal, evidence, client, question, dry)
        return _create_v2(body, principal, evidence, client, question, dry)
    if "mappingPairs" in body:
        raise AiError("固定关联报告需要v2封存证据", "conflict", 409)
    if plan.get("schemaVersion") != "business-evidence-v1":
        raise AiError("此版本证据尚未接入报告分析，请保留封存任务", "conflict", 409)
    queries = [source["query"] for source in plan["sources"]]
    dates = {(q["startDate"], q["endDate"]) for q in queries}
    if len(dates) != 1:
        raise AiError("一份报告须使用同一原始比较区间", "conflict", 409)
    platforms, shops = {q["platform"] for q in queries}, {q["shop"] for q in queries if q.get("shop")}
    start, end = next(iter(dates))
    scope = {"platform": next(iter(platforms)) if len(platforms) == 1 else "多平台", "shop": next(iter(shops)) if len(shops) == 1 else "多店铺" if shops else "市场样本",
        "startDate": start, "endDate": end}
    from . import business_budget
    budget_result = business_budget.resolve(evidence_id, body["budgetPlan"], principal) if "budgetPlan" in body else None
    report_id = uid("ai-report")
    snapshot = {"schemaVersion": SCHEMA, "executionMode": "parallel-v1", "evidenceRunId": evidence_id, "evidenceVersion": evidence.version,
        "evidencePlanDigest": digest(evidence.plan_json), "question": question, "scope": scope, "libraryVersion": 0,
        "pipeline": {"name": "深度经营分析"}, "template": {"name": "多Agent经营诊断", "format": "html", "sections": SECTIONS}, "skills": []}
    if budget_result is not None:
        snapshot.update(reportId=report_id, budgetPlan=budget_result["plan"], budgetPlanDigest=budget_result["planDigest"])
    if "previousReportId" in body:
        from .reports import get
        previous = get(identifier(body["previousReportId"]), principal)
        if budget_result is None or json.loads(previous.snapshot_json).get("evidenceRunId") != evidence_id:
            raise AiError("预算新版本须与前一报告使用相同封存证据", "conflict", 409)
        snapshot["previousReportId"] = previous.id
    with mutation(principal):
        old = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old:
            authorize_owner(old, principal)
            if old.request_digest != digest(body):
                raise AiError("请求标识已绑定其他报告", "conflict", 409)
            return {"item": {"id": old.id, "workflowId": old.workflow_id}, "replayed": True}
        input_value = passive({"evidenceRunId": evidence_id, "question": question, "sources": plan["sources"],
            **({"reportId": report_id, "budgetPlanDigest": budget_result["planDigest"], "budgetAllocation": budget_result["allocation"]} if budget_result else {})}, 8000)
        flow = workflows.create({"clientRequestId": "business-"+digest([principal.email.lower(), client]), "name": "深度经营分析",
            "graph": graph(budget_result is not None), "input": input_value, "dryRun": dry}, principal, True)
        if not dry and not required_tools(snapshot) <= set(flow["item"]["allowedTools"]):
            raise AiError("共享证据分析工具未就绪", "service_unavailable", 503)
        row = m.AiReportRun.objects.create(id=report_id, owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
            client_request_id=client, request_digest=digest(body), workflow_id=flow["item"]["id"], snapshot_json=canonical(snapshot))
    return {"item": {"id": row.id, "workflowId": row.workflow_id}, "replayed": False}


def context(job):
    if not job.workflow_run_id:
        return None
    report = m.AiReportRun.objects.filter(workflow_id=job.workflow_run_id).first()
    if not report:
        return None
    snapshot = json.loads(report.snapshot_json)
    if snapshot.get("executionProfile") in {V2_PROFILE, BUDGET_PROFILE, integrated.PROFILE,
            screening_contract.PROFILE, promotion_contract.PROFILE} and (report.owner_email != job.owner_email or report.scope_json != job.scope_json):
        raise AiError("报告执行身份不一致", "access_denied", 403)
    return snapshot if snapshot.get("schemaVersion") == SCHEMA else None


def has_v2_profile(job):
    snapshot = context(job)
    return snapshot is not None and (snapshot.get("executionProfile") == promotion_contract.PROFILE
        or is_v2_snapshot(snapshot))


def execution_surface(job, principal):
    snapshot = context(job)
    if snapshot is None and (V2_TOOLS | BUDGET_TOOLS | integrated.TOOLS |
            screening_contract.TOOLS | promotion_contract.TOOLS) & set(json.loads(job.allowed_tools_json)):
        raise AiError("v2任务缺少固定报告执行身份", "conflict", 409)
    if snapshot is not None and snapshot.get("executionProfile") == promotion_contract.PROFILE:
        from . import business_promotion_runtime, business_promotion_tools
        report, role, _ = business_promotion_tools._job(job.id, principal)
        fixed = business_promotion_runtime.bound_persisted(report.id, principal)
        if (fixed["executionProfile"] != promotion_contract.PROFILE
                or fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready"
                or canonical(snapshot) != report.snapshot_json
                or role not in screening_contract.ROLES
                or report.workflow_id != job.workflow_run_id
                or report.workflow.allowed_tools_json != canonical(list(promotion_contract.TOOL_ORDER))):
            raise AiError("词货Agent缺少实际固定报告或完整筛查", "conflict", 409)
        return promotion_contract.SURFACE
    if snapshot is None or not is_v2_snapshot(snapshot):
        return "ai_agent"
    reference = bound_reference(snapshot, principal)
    flow = m.AiWorkflowRuns.objects.get(pk=job.workflow_run_id)
    if (canonical(json.loads(flow.input_json)) != canonical(reference) or canonical(json.loads(job.input_json).get("workflowInput")) != canonical(reference)
            or job.workflow_node_key not in V2_NODES or flow.owner_email != job.owner_email or flow.scope_json != job.scope_json):
        raise AiError("工作流轻量引用与固定报告不一致", "conflict", 409)
    return screening_contract.SURFACE if screening_runtime.is_snapshot(snapshot) else integrated.SURFACE if integrated.is_snapshot(snapshot) else BUDGET_SURFACE if is_budget_snapshot(snapshot) else V2_SURFACE


def restricted_entries(job, entries):
    snapshot = context(job)
    if snapshot is None:
        return entries
    required = required_tools(snapshot)
    allowed = [entry for entry in entries if entry["name"] in required]
    if {entry["name"] for entry in allowed} != required:
        raise AiError("共享证据工具目录变化", "executor_policy_changed", 409)
    return allowed


def validate_call(job, call, principal=None, *, screening_step=None, promotion_step=None,
                  promotion_permission=None):
    snapshot = context(job)
    if snapshot and snapshot.get("executionProfile") == promotion_contract.PROFILE:
        from . import business_promotion_microstep, business_promotion_runtime_permission
        if type(promotion_step) is not business_promotion_microstep.PreparedStep:
            raise AiError("词货工具派发缺少当前微步骤准备", "promotion_runtime_not_ready", 409)
        business_promotion_microstep.validate_call(promotion_step, call,
            principal or workflows.background(job))
        business_promotion_runtime_permission.authorize_dispatch(
            promotion_permission, job, principal or workflows.background(job))
        return
    if snapshot and screening_runtime.is_snapshot(snapshot):
        from . import business_screening_execution
        if type(screening_step) is not business_screening_execution.PreparedStep:
            raise AiError("筛查派发缺少当前步骤许可", "conflict", 409)
        business_screening_execution.validate_call(screening_step,call)
        return
    if snapshot and (call["name"] not in required_tools(snapshot) or call["arguments"].get("runId") != snapshot["evidenceRunId"] or
            call["name"] in {BUDGET_TOOL, BUDGET_REFERENCE_TOOL} and call["arguments"].get("reportId") != snapshot.get("reportId")):
        raise AiError("分析任务只能读取本次封存证据", "access_denied", 403)
    if snapshot and integrated.is_snapshot(snapshot):
        from .business_integrated_receipts import progress
        if call["arguments"].get("reportId") != snapshot["reportId"]:
            raise AiError("集成工具只能读取固定报告", "access_denied", 403)
        principal = principal or workflows.background(job)
        proof, args = progress(job, snapshot, principal), call["arguments"]
        if call["name"] == integrated.DIRECTORY_TOOL:
            if (set(args)-{"runId","reportId","offset"} or type(args.get("offset",0)) is not int
                    or proof["directory"]["complete"] or args.get("offset",0) != proof["directory"]["nextOffset"]):
                raise AiError("集成目录须顺序完整读取", "directory_read_incomplete", 409)
        elif not proof["directory"]["complete"]:
            raise AiError("须先完整读取固定来源和关联目录", "directory_read_incomplete", 409)
        elif call["name"] == integrated.BUDGET_TOOL:
            if ("budgetRef" not in snapshot or set(args)-{"runId","reportId","offset"}
                    or type(args.get("offset",0)) is not int or proof["budget"]["complete"]
                    or args.get("offset",0) != proof["budget"]["nextOffset"]):
                raise AiError("固定预算须顺序完整读取", "budget_read_incomplete", 409)
        return
    if snapshot and is_budget_snapshot(snapshot):
        from .business_budget_receipts import progress
        principal = principal or workflows.background(job)
        proof = progress(job, snapshot, principal)
        args = call["arguments"]
        if call["name"] == BUDGET_DIRECTORY_TOOL:
            if (set(args)-{"runId", "offset"} or type(args.get("offset", 0)) is not int
                    or proof["directory"]["complete"] or args.get("offset", 0) != proof["directory"]["nextOffset"]):
                raise AiError("预算任务目录须连续完整读取", "directory_read_incomplete", 409)
        elif not proof["directory"]["complete"]:
            raise AiError("须先完整读取固定来源目录", "directory_read_incomplete", 409)
        elif call["name"] == BUDGET_REFERENCE_TOOL:
            if (set(args)-{"runId", "reportId", "offset"} or type(args.get("offset", 0)) is not int
                    or proof["budget"]["complete"] or args.get("offset", 0) != proof["budget"]["nextOffset"]):
                raise AiError("固定预算须逐页连续读取", "budget_read_incomplete", 409)
        return
    if snapshot and is_v2_snapshot(snapshot):
        from .business_evidence_receipts import directory_progress
        proof = directory_progress(job, snapshot)
        if call["name"] == V2_DIRECTORY_TOOL:
            args = call["arguments"]
            if (set(args)-{"runId", "offset"} or type(args.get("offset", 0)) is not int
                    or proof["complete"] or args.get("offset", 0) != proof["nextOffset"]):
                raise AiError("目录必须逐页连续读取，不能重复或跳页", "directory_read_incomplete", 409)
        elif not proof["complete"]:
            raise AiError("须先完整读取固定来源目录", "directory_read_incomplete", 409)


def validate_output(job, answer, principal=None, *, screening_step=None, screening_answer=None,
                    promotion_step=None, promotion_answer=None, promotion_permission=None,
                    promotion_result_commit=False):
    snapshot = context(job)
    if snapshot is None:
        return
    if snapshot.get("executionProfile") == promotion_contract.PROFILE:
        from . import business_promotion_execution, business_promotion_runtime_permission
        if type(promotion_answer) is not business_promotion_execution.ValidatedFinal:
            raise AiError("词货回答缺少本进程完整验证", "promotion_runtime_not_ready", 409)
        business_promotion_execution.check(promotion_answer, job, answer,
            principal or workflows.background(job))
        business_promotion_runtime_permission.authorize_dispatch(
            promotion_permission, job, principal or workflows.background(job),
            result_commit=promotion_result_commit)
        return
    if screening_runtime.is_snapshot(snapshot):
        from . import business_screening_execution
        business_screening_execution.check_answer(screening_answer,screening_step,answer)
        return
    if integrated.is_snapshot(snapshot):
        from .business_integrated_receipts import validate_complete
        validate_complete(job, snapshot, principal or workflows.background(job))
    elif is_budget_snapshot(snapshot):
        from .business_budget_receipts import validate_complete
        validate_complete(job, snapshot, principal or workflows.background(job))
    elif is_v2_snapshot(snapshot):
        from .business_evidence_receipts import validate_directory_complete
        validate_directory_complete(job, snapshot)
    budget = 15000 if job.workflow_node_key == "report" else 2000 if job.workflow_node_key == "independent_review" else 3000
    if integrated.is_snapshot(snapshot):
        budget = integrated.OUTPUT_LIMITS[job.workflow_node_key]
    elif is_budget_snapshot(snapshot):
        budget = BUDGET_OUTPUT_LIMITS[job.workflow_node_key]
    if len(answer.encode()) > budget:
        raise AiError("专业分析输出超过下游可复核容量，保留原回执", "payload_too_large", 413)
    try:
        value = json.loads(answer)
    except (ValueError, TypeError) as error:
        raise AiError("专业分析须输出JSON，保留原回执", "conflict", 409) from error
    if not isinstance(value, dict):
        raise AiError("专业分析结构无效", "conflict", 409)


def validate_provider_turn(job, principal=None, *, screening_step=None,
                           promotion_step=None, promotion_permission=None,
                           frames=None, entries=None, model=None):
    """Do not send a persisted, malformed directory receipt to a model."""
    snapshot = context(job)
    if snapshot is not None and snapshot.get("executionProfile") == promotion_contract.PROFILE:
        from . import business_promotion_microstep, business_promotion_runtime_permission
        if type(promotion_step) is not business_promotion_microstep.PreparedStep:
            raise AiError("词货模型派发缺少当前微步骤准备", "promotion_runtime_not_ready", 409)
        if frames is None or entries is None or model is None:
            raise AiError("词货模型派发缺少事务外上下文校验", "promotion_runtime_not_ready", 409)
        business_promotion_microstep.validate_provider_turn(promotion_step,
            frames, entries, model, principal or workflows.background(job))
        business_promotion_runtime_permission.authorize_dispatch(
            promotion_permission, job, principal or workflows.background(job))
        return
    if snapshot is not None and screening_runtime.is_snapshot(snapshot):
        from . import business_screening_execution
        if type(screening_step) is not business_screening_execution.PreparedStep:
            raise AiError("筛查模型派发缺少当前步骤许可", "conflict", 409)
        return
    if snapshot is not None and integrated.is_snapshot(snapshot):
        from .business_integrated_receipts import progress
        progress(job, snapshot, principal or workflows.background(job))
    elif snapshot is not None and is_budget_snapshot(snapshot):
        from .business_budget_receipts import progress
        progress(job, snapshot, principal or workflows.background(job))
    elif snapshot is not None and is_v2_snapshot(snapshot):
        from .business_evidence_receipts import directory_progress
        directory_progress(job, snapshot)


def content(row, principal):
    snapshot = json.loads(row.snapshot_json)
    if snapshot.get("executionProfile") == promotion_contract.PROFILE:
        if row.workflow.status in {"queued", "completed"}:
            from .business_promotion_approved_content import build
            return build(row.id, principal)["content"]
        if row.workflow.status == "waiting_review":
            from .business_promotion_content import build
            return build(row.id, principal)["content"]
        raise AiError("词货五角色诊断尚未完成", "conflict", 409)
    if screening_runtime.is_snapshot(snapshot):
        from .business_screening_content import content as screening_content
        return screening_content(row,principal)
    if integrated.is_snapshot(snapshot):
        from .business_integrated_content_reuse import ContentReuse
        with ContentReuse(row, principal) as reuse:
            result = _content(row, principal, _reuse=reuse)
        return result
    return _content(row, principal)


def _content(row, principal, *, _reuse=None):
    from .business_diagnosis import validate
    if row.workflow.dry_run:
        raise AiError("空跑不生成诊断", "conflict", 409)
    snapshot = json.loads(row.snapshot_json)
    business_evidence.get_run(snapshot["evidenceRunId"], principal)
    nodes = {n.node_key: n for n in m.AiWorkflowNodeRuns.objects.filter(run_id=row.workflow_id, status="completed")}
    if not {"commerce", "promotion", "market_b2b", "independent_review", "report"} <= set(nodes):
        raise AiError("专业分析、独立复核或整合尚未完成", "conflict", 409)
    if is_v2_snapshot(snapshot):
        from .business_evidence_receipts import validate_directory_complete
        bound_reference(snapshot, principal)
        for key in sorted(V2_NODES):
            job = m.AiAgentJobs.objects.filter(pk=nodes[key].agent_job_id, workflow_run_id=row.workflow_id,
                workflow_node_key=key, owner_email=row.owner_email, status="completed").first()
            if job is None:
                raise AiError("专业节点缺少独立完成回执", "conflict", 409)
            execution_surface(job, principal)
            if integrated.is_snapshot(snapshot):
                from .business_integrated_receipts import validate_complete
                validate_complete(job, snapshot, principal, **({"_reuse":_reuse} if _reuse is not None else {}))
            elif is_budget_snapshot(snapshot):
                from .business_budget_receipts import validate_complete
                validate_complete(job, snapshot, principal)
            else:
                validate_directory_complete(job, snapshot)
    def parsed(key):
        try:
            return json.loads(json.loads(nodes[key].output_json)["answer"])
        except (ValueError, KeyError, TypeError) as error:
            raise AiError("分析结构无效，保留结果供复核，不自动重跑", "conflict", 409) from error
    value, review = parsed("report"), parsed("independent_review")
    fields(value, {"sections", "diagnosis"}, {"sections", "diagnosis"})
    fields(review, {"approved", "conflicts", "limitations"}, {"approved", "conflicts", "limitations"})
    boolean(review["approved"], "approved")
    for field in ("conflicts", "limitations"):
        if not isinstance(review[field], list) or len(review[field]) > 20:
            raise AiError("复核问题列表无效")
        review[field] = [text(item, field, 1000) for item in review[field]]
    sections = value["sections"]
    if not isinstance(sections, list) or len(sections) != len(SECTIONS):
        raise AiError("报告章节缺失", "conflict", 409)
    for section, title in zip(sections, SECTIONS):
        fields(section, {"title", "body"}, {"title", "body"})
        if section["title"] != title:
            raise AiError("报告章节不匹配", "conflict", 409)
        section["body"] = text(section["body"], "body", 10000)
    diagnosis = validate(value["diagnosis"], snapshot["evidenceRunId"], principal,
        fixed_mapping_plan=snapshot["mappingPlan"] if integrated.is_snapshot(snapshot) else None)
    from .business_budget import for_report
    budget_result = (_reuse.budget(row, principal).result if _reuse is not None and row.budget_plan_id
        else for_report(row, principal))
    return {"sections": sections, "diagnosis": diagnosis, "independentReview": review, **({"budget": budget_result} if budget_result else {})}


def validate_review(row, principal):
    value = content(row, principal)
    if not value["independentReview"]["approved"] or value["independentReview"]["conflicts"]:
        raise AiError("独立复核仍有未解决冲突，不能交付正式报告", "conflict", 409)
    if screening_runtime.is_snapshot(json.loads(row.snapshot_json)):
        return value
    for key in (() if is_v2_snapshot(json.loads(row.snapshot_json)) else ("commerce", "promotion", "market_b2b", "independent_review")):
        receipts = m.AiAgentToolResults.objects.filter(tool_dispatch__job__workflow_run_id=row.workflow_id,
            tool_dispatch__job__workflow_node_key=key, tool_dispatch__tool_name="get_business_analysis_evidence")
        if not any(json.loads(item.result_json).get("ok") is True and json.loads(item.result_json).get("auditStatus") == "recorded" for item in receipts):
            raise AiError("专业分析或独立复核缺少成功的共享证据读取回执", "conflict", 409)
    if value.get("budget") and not is_budget_snapshot(json.loads(row.snapshot_json)) and not integrated.is_snapshot(json.loads(row.snapshot_json)):
        target_count = value["budget"]["allocation"]["targetCount"]
        for key in ("promotion", "independent_review"):
            covered = set()
            receipts = m.AiAgentToolResults.objects.filter(tool_dispatch__job__workflow_run_id=row.workflow_id,
                tool_dispatch__job__workflow_node_key=key, tool_dispatch__tool_name=BUDGET_TOOL)
            for receipt in receipts:
                result = json.loads(receipt.result_json)
                data = result.get("data", {})
                if result.get("ok") is True and result.get("auditStatus") == "recorded" and data.get("reportId") == row.id and data.get("planDigest") == value["budget"]["planDigest"]:
                    covered.update(item["rowIndex"] for item in data.get("rows", []))
            if covered != set(range(target_count)):
                raise AiError("推广分析或独立复核尚未完整读取固定预算情景", "conflict", 409)
    return value


def _reference(evidence, question):
    from business_analysis.evidence_v2 import workflow_reference
    from business_analysis.contracts import AnalysisContractError
    from . import business_evidence_store as store
    if evidence.status != "sealed" or not store.is_v2(evidence):
        raise AiError("引用需要已封存的v2证据", "conflict", 409)
    store.verify_seal(evidence)
    sources = store.catalog(evidence)
    seal, header = json.loads(evidence.state_json), json.loads(evidence.plan_json)
    try:
        reference = workflow_reference(sources, run_id=evidence.id, evidence_version=evidence.version,
            sealed_digest=seal["sealedDigest"], question=question, analysis_request=header.get("analysisRequest"))
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error
    return reference, sources


def bound_reference(snapshot, principal):
    if screening_runtime.is_snapshot(snapshot):
        from .reports import get
        report = get(snapshot["reportId"],principal)
        if report.snapshot_json != canonical(snapshot):
            raise AiError("筛查报告快照不一致", "conflict", 409)
        return screening_runtime.bound(report,principal)[2]
    if integrated.is_snapshot(snapshot):
        from .reports import get
        report = get(snapshot["reportId"], principal)
        if report.snapshot_json != canonical(snapshot):
            raise AiError("集成报告快照不一致", "conflict", 409)
        return integrated.bound(report, principal)[2]
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    reference, _ = _reference(evidence, snapshot["question"])
    keys = ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount")
    if canonical({k: snapshot.get(k) for k in keys}) != canonical({k: reference[k] for k in keys}):
        raise AiError("报告固定封存引用已变化", "conflict", 409)
    if is_budget_snapshot(snapshot):
        from . import business_budget_store
        from .reports import get
        report = get(snapshot["reportId"], principal)
        if report.snapshot_json != canonical(snapshot):
            raise AiError("固定预算报告快照不一致", "conflict", 409)
        fixed = business_budget_store.binding_for_report(report, principal)
        reference = {**reference, "reportId": report.id, "budgetRef": fixed.reference}
    return reference


def graph_v2():
    value = graph()
    for node in value["nodes"]:
        if node["type"] == "agent":
            node["instruction"] += (
                "本任务采用reference-v2轻量引用。必须本人逐页调用get_business_evidence_directory_v2，"
                "从offset=0开始严格跟随nextOffset，直至null；目录未读完禁止调用分析表或提交答案。"
                "目录读取完毕只证明掌握来源范围，不代表已读完所有事实或完成分析。"
                "随后使用get_business_analysis_table_v2读取所需分析表；不得借用其他Agent的目录读取回执。")
    return value


def graph_budget():
    """Independent immutable instructions; no edits to old pinned graphs."""
    value = graph(with_budget=True)
    for node in value["nodes"]:
        key = node["key"]
        if node["type"] != "agent":
            continue
        node["instruction"] = node["instruction"].replace(BUDGET_TOOL, BUDGET_REFERENCE_TOOL)
        old_limit = 15000 if key == "report" else 2000 if key == "independent_review" else 3000
        node["instruction"] = node["instruction"].replace(str(old_limit)+"个UTF-8字节", str(BUDGET_OUTPUT_LIMITS[key])+"个UTF-8字节")
        node["instruction"] += (
            "本任务采用固定预算引用，先本人从offset=0调用get_business_budget_directory_v1，严格跟随nextOffset直至null。"
            "读完目录再调用get_business_budget_analysis_table_v1查询证据；目录只证明掌握范围，不代表已读全量事实。"
            "推广、独立复核与报告整合节点必须本人从offset=0调用get_business_budget_scenarios_v1读取全部预算页，跟随nextOffset直到null。"
            "其他节点一旦开始读预算也必须读完。不得借用其他Agent回执，预算参数固定，不得把情景假设当实际收益。"
            "输出上限包括JSON字段名和转义字符；完整明细通过文件交付，正文聚焦证据、诊断和可执行规划。")
    return value


def _create_budget_reference(body, principal, evidence, client, question, dry):
    from . import business_budget_store

    def replay():
        old = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old is None:
            return None
        authorize_owner(old, principal)
        if old.request_digest != digest(body):
            raise AiError("请求标识已绑定其他报告", "conflict", 409)
        bound_reference(json.loads(old.snapshot_json), principal)
        return {"item": {"id": old.id, "workflowId": old.workflow_id}, "replayed": True}

    old = replay()
    if old is not None:
        return old
    reference, sources = _reference(evidence, question)
    report_id = uid("ai-report")
    prepared = business_budget_store.prepare(evidence, body["budgetPlan"], principal, report_id)
    queries = [source["query"] for source in sources]
    platforms, shops = {q["platform"] for q in queries}, {q["shop"] for q in queries if q.get("shop")}
    scope = {"platform": next(iter(platforms)) if len(platforms) == 1 else "多平台",
        "shop": next(iter(shops)) if len(shops) == 1 else "多店铺" if shops else "市场样本",
        "startDate": queries[0]["startDate"], "endDate": queries[0]["endDate"]}
    snapshot = {"schemaVersion": SCHEMA, "executionMode": "parallel-v1", "executionProfile": BUDGET_PROFILE,
        "evidenceProtocol": "reference-v2", **{k: v for k, v in reference.items() if k != "inputMode"},
        "reportId": report_id, "budgetRef": prepared.reference, "scope": scope, "libraryVersion": 0,
        "pipeline": {"name": "固定预算深度经营分析"}, "template": {"name": "多Agent经营诊断", "format": "html", "sections": SECTIONS}, "skills": []}
    if "previousReportId" in body:
        from .reports import get
        previous = get(identifier(body["previousReportId"]), principal)
        prior = json.loads(previous.snapshot_json)
        if not is_v2_snapshot(prior) or any(prior.get(key) != snapshot.get(key) for key in
                ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "question", "scope")):
            raise AiError("预算新版本须使用前一报告相同的封存与分析范围", "conflict", 409)
        bound_reference(prior, principal)
        snapshot["previousReportId"] = previous.id
    input_value = passive({**reference, "reportId": report_id, "budgetRef": prepared.reference}, 8000)
    with mutation(principal):
        old = replay()
        if old is not None:
            return old
        parameters = business_budget_store.insert(prepared, principal)
        flow = workflows.create({"clientRequestId": "business-"+digest([principal.email.lower(), client]),
            "name": "固定预算深度经营分析", "graph": graph_budget(), "input": input_value, "dryRun": dry}, principal, True,
            execution_profile=BUDGET_PROFILE, budget_prepared=prepared)
        if not dry and set(flow["item"]["allowedTools"]) != BUDGET_TOOLS:
            raise AiError("固定预算分析工具目录未就绪", "service_unavailable", 503)
        row = m.AiReportRun.objects.create(id=report_id, owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
            client_request_id=client, request_digest=digest(body), workflow_id=flow["item"]["id"],
            snapshot_json=canonical(passive(snapshot, 32768)), budget_plan=parameters)
    return {"item": {"id": row.id, "workflowId": row.workflow_id}, "replayed": False}


def _create_v2(body, principal, evidence, client, question, dry):
    if "budgetPlan" in body:
        return _create_budget_reference(body, principal, evidence, client, question, dry)
    if "previousReportId" in body:
        raise AiError("预算新版本须提供固定预算参数", "conflict", 409)
    reference, sources = _reference(evidence, question)
    queries = [s["query"] for s in sources]
    platforms, shops = {q["platform"] for q in queries}, {q["shop"] for q in queries if q.get("shop")}
    scope = {"platform": next(iter(platforms)) if len(platforms) == 1 else "多平台",
        "shop": next(iter(shops)) if len(shops) == 1 else "多店铺" if shops else "市场样本",
        "startDate": queries[0]["startDate"], "endDate": queries[0]["endDate"]}
    snapshot = {"schemaVersion": SCHEMA, "executionMode": "parallel-v1", "executionProfile": V2_PROFILE,
        "evidenceProtocol": "reference-v2", **{k: v for k, v in reference.items() if k != "inputMode"},
        "scope": scope, "libraryVersion": 0, "pipeline": {"name": "深度经营分析"},
        "template": {"name": "多Agent经营诊断", "format": "html", "sections": SECTIONS}, "skills": []}
    with mutation(principal):
        old = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old:
            authorize_owner(old, principal)
            if old.request_digest != digest(body):
                raise AiError("请求标识已绑定其他报告", "conflict", 409)
            return {"item": {"id": old.id, "workflowId": old.workflow_id}, "replayed": True}
        # Admission precedes report persistence, so only this internal keyword
        # selects the new catalog. The outer transaction publishes both or none.
        flow = workflows.create({"clientRequestId": "business-"+digest([principal.email.lower(), client]),
            "name": "深度经营分析", "graph": graph_v2(), "input": reference, "dryRun": dry}, principal, True,
            execution_profile=V2_PROFILE)
        if not dry and set(flow["item"]["allowedTools"]) != V2_TOOLS:
            raise AiError("v2封存证据工具目录未就绪", "service_unavailable", 503)
        row = m.AiReportRun.objects.create(id=uid("ai-report"), owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
            client_request_id=client, request_digest=digest(body), workflow_id=flow["item"]["id"], snapshot_json=canonical(passive(snapshot, 32768)))
    return {"item": {"id": row.id, "workflowId": row.workflow_id}, "replayed": False}


def preflight_v2(flow, principal, graph_value, entries):
    """Measure actual whole-directory tool transcripts before any model call.

    Reserve one maximum-size analysis response and downstream dependency outputs.
    This is admission, not a guarantee of arbitrary later model prose/tool usage;
    all existing live context and tool budgets still apply without truncation.
    """
    from types import SimpleNamespace
    from business_analysis.evidence_v2 import directory_page
    from . import provider
    from .model_capabilities import fit_context
    given = json.loads(flow.input_json)
    evidence = business_evidence.get_run(given.get("evidenceRunId"), principal)
    reference, sources = _reference(evidence, given.get("question"))
    if canonical(given) != canonical(reference) or canonical(graph_value) != canonical(workflows.validate_graph(graph_v2())):
        raise AiError("v2执行输入或固定步骤无效", "conflict", 409)
    pages, offset = [], 0
    while offset is not None:
        page = directory_page(sources, run_id=evidence.id, evidence_version=evidence.version, offset=offset, limit=20,
            analysis_request=json.loads(evidence.plan_json).get("analysisRequest"))
        pages.append(page)
        offset = page["nextOffset"]
    model = workflows.resolve_model(flow.model_id) if not flow.dry_run else None
    if model:
        if {e["name"] for e in entries} != V2_TOOLS:
            raise AiError("v2分析工具目录不完整", "service_unavailable", 503)
        directory_tool = next(e for e in entries if e["name"] == V2_DIRECTORY_TOOL)
        if (len(pages) > directory_tool["execution"]["maxCallsPerRequest"]
                or len(pages)+1 > min(model.max_total_tool_calls, 40)
                or len(pages)+2 > min(model.max_tool_rounds, 20)):
            raise AiError("当前模型工具额度不足以完整读取目录并分析", "tool_limit_exceeded", 409)
    protocol_model = model or SimpleNamespace(protocol="openai_compatible")
    sizes = {"commerce": 3000, "promotion": 3000, "market_b2b": 3000, "independent_review": 2000, "report": 15000}
    def append_tool(frames, name, args, data, ordinal):
        call = {"id": "preflight-"+str(ordinal), "name": name, "arguments": args}
        if protocol_model.protocol == "anthropic":
            frame = {"role": "assistant", "content": [{"type": "tool_use", "id": call["id"], "name": name, "input": args}]}
        else:
            frame = {"role": "assistant", "content": None, "tool_calls": [{"id": call["id"], "type": "function",
                "function": {"name": name, "arguments": canonical(args)}}]}
        frames.append(frame)
        frames.extend(provider.tool_frames(protocol_model, [call], [{"ok": True, "toolName": name, "auditStatus": "recorded", "data": data}]))
    for node in graph_value["nodes"]:
        if node["type"] != "agent":
            continue
        data = {"workflowInput": reference, "dependencies": {k: {"answer": 'x'*sizes[k]} for k in node["dependsOn"]}}
        passive(data, 24*1024)
        frames = [{"role": "user", "content": node["instruction"]+"\n<task_input>"+canonical(data).replace("<", "\\u003c")+"</task_input>"}]
        for i, page in enumerate(pages):
            append_tool(frames, V2_DIRECTORY_TOOL, {"runId": evidence.id, "offset": page["offset"]}, page, i)
        # This is an encoded transcript reservation, not a fabricated wire page.
        # It deliberately exceeds the wire JSON cap to cover nested escaping.
        append_tool(frames, V2_TABLE_TOOL, {"runId": evidence.id, "sourceKey": sources[0]["key"], "dimension": "shop"},
            {"preflightTranscriptByteReservation": "x"*V2_ANALYSIS_TRANSCRIPT_RESERVE}, len(pages))
        if len(canonical(frames).encode()) > 192*1024:
            raise AiError("完整目录及分析所需上下文超过192KiB，不能截断", "transcript_limit_exceeded", 409)
        if model:
            _, info = fit_context(model, frames, provider.system_prompt(model, workflows.SYSTEM+workflows.execution_guidance(flow.id)), entries)
            if info["droppedMessages"]:
                raise AiError("目录上下文不能通过删除已读取页压缩", "ai_context_budget_exceeded", 409)
