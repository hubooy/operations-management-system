"""Durable four-tool read proof for one completed promotion-profile Agent.

Completed jobs cannot use the running-only tool adapters or process-local
final-answer token. Every saved successful page is reconstructed from current
sealed report roots, with no provider/tool call or workflow mutation.
"""
import json

from . import business_promotion_budget as budget_reader
from . import business_promotion_claims as promotion_claims
from . import business_promotion_diagnosis as diagnosis
from . import business_promotion_read_receipts as fourth
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools as format_reader
from . import business_screening_packages as packages
from . import business_screening_runtime_contract as screening_contract
from . import business_evidence as evidence_service
from . import models as m
from .business_evidence_receipts import _same
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


def _reject(message="已完成词货Agent的持久读取证明无效"):
    raise AiError(message, "promotion_completed_read_incomplete", 409)


def _approved_parent(report, flow, nodes):
    """Queued is valid only after the actual six-node human approval."""
    human = nodes[-1]
    try:
        decision = json.loads(human.output_json)
        events = list(m.AiWorkflowEvents.objects.filter(run=flow,
            node_key="human_review", event_type="review_approved")[:2])
        specialists = nodes[:5]
        if (human.node_key != "human_review" or human.status != "completed"
                or human.agent_job_id is not None or human.reviewer_email is None
                or human.reviewer_email.lower() != report.owner_email
                or human.reviewed_at is None or human.completed_at is None
                or human.reviewed_at > human.completed_at
                or type(decision) is not dict or set(decision) != {"decision", "comment"}
                or decision["decision"] != "approve" or type(decision["comment"]) is not str
                or len(decision["comment"]) > 2000
                or human.output_json != canonical(decision)
                or flow.current_node_key is not None or len(events) != 1
                or events[0].actor_email.lower() != human.reviewer_email.lower()
                or events[0].owner_email != report.owner_email
                or events[0].from_status != "waiting_review"
                or events[0].to_status != "queued"
                or events[0].run_version > flow.version
                or events[0].created_at < human.completed_at
                or any(node.status != "completed" or not node.output_json
                    or node.agent_job_id is None for node in specialists)
                or len({node.agent_job_id for node in specialists}) != 5):
            _reject("队列或完成态工作流缺少实际六节点人工批准")
        siblings = {job.id: job for job in m.AiAgentJobs.objects.filter(
            workflow_run_id=flow.id, pk__in=[node.agent_job_id for node in specialists])[:6]}
        if (len(siblings) != 5 or any(siblings[node.agent_job_id].status != "completed"
                or siblings[node.agent_job_id].output_json != node.output_json
                for node in specialists)):
            _reject("人工批准没有五个已完成的实际专业任务")
        if flow.status == "queued":
            if flow.output_json is not None or flow.completed_at is not None:
                _reject("人工批准后的队列不能提前有正式结果")
        elif flow.status == "completed":
            expected = canonical({node.node_key: json.loads(node.output_json)
                for node in nodes})
            if (flow.output_json != expected or flow.completed_at is None
                    or flow.completed_at < human.completed_at):
                _reject("完成态工作流结果与已批准的六节点不同")
    except AiError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("人工批准持久状态无法解析", "promotion_completed_read_incomplete", 409) from error


def _root(job, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("完成态词货读取仅允许无范围管理员")
    actual = m.AiAgentJobs.objects.filter(pk=identifier(job.id, "jobId")).first()
    if actual is None:
        _reject("完成态实际Agent不存在")
    authorize_owner(actual, principal)
    report = m.AiReportRun.objects.select_related("workflow").filter(
        workflow_id=actual.workflow_run_id).first()
    if report is None:
        _reject("完成态Agent缺少固定报告")
    authorize_owner(report, principal)
    flow = report.workflow
    fixed = runtime.bound_persisted(report.id, principal)
    try:
        snapshot = json.loads(report.snapshot_json)
        reference = json.loads(flow.input_json)
        graph = contract.graph(bool(report.budget_plan_id))
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
        by_key = {node.node_key: node for node in nodes}
        role = actual.workflow_node_key
        node = by_key[role]
        spec = next(item for item in graph["nodes"] if item["key"] == role)
        deps = json.loads(node.depends_on_json)
        expected_input = {"workflowInput": reference,
            "dependencies": {key: json.loads(by_key[key].output_json) for key in deps}}
        output = json.loads(actual.output_json)
        if (fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready"
                or fixed["reportId"] != report.id or fixed["workflowId"] != flow.id
                or fixed["modelId"] != actual.model_id or fixed["modelVersion"] != actual.model_version
                or actual.status != "completed" or actual.phase != "completed"
                or actual.cancel_requested or actual.lease_token or actual.lease_expires_at is not None
                or actual.completed_at is None or actual.owner_email != report.owner_email
                or actual.scope_json != report.scope_json or flow.owner_email != report.owner_email
                or flow.scope_json != report.scope_json or flow.cancel_requested
                or flow.status not in {"running", "waiting_review", "queued", "completed"}
                or role not in screening_contract.ROLES
                or node.status != "completed" or node.agent_job_id != actual.id
                or node.output_json != actual.output_json or node.input_json != actual.input_json
                or actual.task != node.instruction or actual.input_json != canonical(expected_input)
                or deps != spec["dependsOn"]
                or any(by_key[key].status != "completed" for key in deps)
                or len(nodes) != len(graph["nodes"])
                or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
                or any((saved.position, saved.node_key, saved.node_type, saved.instruction,
                    saved.depends_on_json) != (index, item["key"], item["type"],
                    item["instruction"], canonical(item["dependsOn"]))
                    for index, (saved, item) in enumerate(zip(nodes, graph["nodes"])))
                or actual.allowed_tools_json != flow.allowed_tools_json
                or flow.allowed_tools_json != canonical(list(contract.TOOL_ORDER))
                or any(getattr(actual, field) != getattr(flow, field)
                    for field in ("model_id", "model_version", "tool_policy_digest"))
                or type(output) is not dict or set(output) != {"answer"}
                or type(output["answer"]) is not str
                or actual.output_json != canonical(output)):
            _reject("完成态Agent、节点、模型、输出或固定图不一致")
        if flow.status in {"queued", "completed"}:
            _approved_parent(report, flow, nodes)
        diagnosis.validate_answer(role, output["answer"])
        checkpoint = m.AiAgentCheckpoints.objects.filter(job=actual,
            ordinal=actual.step_index, kind="completed").first()
        if (checkpoint is None or checkpoint.output_digest != digest(output)
                or checkpoint.state_json != actual.state_json):
            _reject("完成态Agent缺少同一输出的完成检查点")
        evidence = evidence_service.get_run(snapshot["evidenceRunId"], principal)
        if (evidence.status != "sealed" or reference["reportId"] != report.id
                or reference["screeningIntent"] != snapshot["screeningIntent"]):
            _reject("完成态来源或筛查意图已变化")
    except AiError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, RecursionError) as error:
        raise AiError("完成态Agent协议或输出无法解析", "promotion_completed_read_incomplete", 409) from error
    guard = digest([actual.id, actual.version, actual.status, actual.input_json,
        actual.output_json, actual.provider_round_count, actual.tool_call_count,
        actual.step_index, checkpoint.id, checkpoint.output_digest,
        report.id, report.snapshot_json, flow.id, flow.version, flow.status,
        flow.input_json, flow.graph_json, node.id, node.version, node.status,
        node.input_json, node.output_json, canonical(fixed)])
    return actual, report, role, snapshot, reference, evidence, fixed, guard


def _final_provider(job, providers, answer):
    if len(providers) != job.provider_round_count or not providers:
        _reject("模型持久回执数量与完成态Agent不一致")
    last = max((row for row, _ in providers.values()), key=lambda row: row.dispatch_ordinal)
    receipt = m.AiAgentProviderResults.objects.filter(dispatch_id=last.id).first()
    if receipt is None:
        _reject("最终模型回执缺失")
    saved = fourth._json(receipt.response_json, receipt.response_digest, fourth.MAX_RESULT_BYTES)
    calls = saved.get("calls", saved.get("toolCalls"))
    if calls != [] or saved.get("text") != answer:
        _reject("最终模型回答与实际完成输出不一致")
    return last.id


def _base(args, report, evidence, screening_id):
    if (type(args) is not dict or args.get("runId") != evidence.id
            or args.get("reportId") != report.id or args.get("screeningId") != screening_id):
        _reject("完成态工具回执跨报告、证据或筛查意图")


def _expected(name, args, report, role, snapshot, reference, evidence, principal, package):
    if name == contract.PACKAGE_TOOL:
        if (set(args) - {"runId", "reportId", "screeningId", "role", "offset"}
                or args.get("role") != role or type(args.get("offset", 0)) is not int):
            _reject("完成态角色包参数或角色无效")
        return packages.page(package, role, principal, offset=args.get("offset", 0))
    if name == contract.BUDGET_TOOL:
        if (set(args) - {"runId", "reportId", "screeningId", "offset"}
                or type(args.get("offset", 0)) is not int or report.budget_plan_id is None):
            _reject("完成态固定预算参数无效")
        return budget_reader.read_page(report.id, principal, offset=args.get("offset", 0))
    if name == contract.TABLE_TOOL:
        return format_reader._analysis(args, report, snapshot, reference, evidence, principal)
    mode = fourth._selector(args, {"reportId": report.id,
        "promotionSelector": snapshot["promotionSelector"]})
    return fourth._expected(report.id, args, mode, principal)


def progress(job, principal):
    """Return one completed job's exact persisted read proof, or fail closed."""
    actual, report, role, snapshot, reference, evidence, fixed, before = _root(job, principal)
    ledger_before = fourth._ledger_fence(actual.id)
    providers = fourth._providers(actual)
    final_provider_id = _final_provider(actual, providers, json.loads(actual.output_json)["answer"])
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=actual.id).order_by(
        "tool_call_ordinal")[:fourth.MAX_TOOLS + 1])
    if len(dispatches) > fourth.MAX_TOOLS or actual.tool_call_count != len(dispatches):
        _reject("完成态工具回执数量或容量不符")
    package = packages.prepare(snapshot["screeningIntent"]["id"], principal)
    package_state = {"pages": 0, "nextOffset": 0, "complete": False}
    budget_state = {"pages": 0, "nextOffset": 0, "complete": False,
        "required": report.budget_plan_id is not None and role in screening_contract.BUDGET_NODES,
        "started": False}
    views = {name: {"pages": 0, "nextOffset": 0, "complete": False,
        "rowReceipts": 0, "seenRows": [], "tableBindingDigest": None}
        for name in snapshot["promotionSelector"]["views"]}
    analyses = {}
    counts = {name: 0 for name in contract.TOOL_ORDER}
    for ordinal, dispatch in enumerate(dispatches, 1):
        if (dispatch.job_id != actual.id or dispatch.tool_call_ordinal != ordinal
                or dispatch.provider_dispatch_id not in providers
                or dispatch.provider_dispatch_id == final_provider_id
                or dispatch.tool_name not in contract.TOOLS):
            _reject("完成态工具派发不属于当前Agent的有效模型调用")
        provider, calls = providers[dispatch.provider_dispatch_id]
        if (not 1 <= provider.lease_epoch <= dispatch.lease_epoch <= actual.lease_epoch
                or dispatch.provider_call_id not in calls
                or calls[dispatch.provider_call_id]["name"] != dispatch.tool_name):
            _reject("完成态工具派发与模型回执不对应")
        args = fourth._json(dispatch.arguments_json, dispatch.arguments_digest,
            fourth.MAX_ARGUMENT_BYTES)
        if canonical(calls[dispatch.provider_call_id]["arguments"]) != dispatch.arguments_json:
            _reject("完成态工具参数与模型调用不同")
        counts[dispatch.tool_name] += 1
        if counts[dispatch.tool_name] > 8:
            _reject("完成态单工具调用超过固定上限")
        if dispatch.state in {"calling", "unknown"}:
            raise AiError("工具结果未知，禁止自动重放", "tool_dispatch_unknown", 409)
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatch.id).first()
        if dispatch.state == "failed" and receipt is None:
            continue
        if receipt is None:
            raise AiError("完成态成功工具派发缺少持久结果", "tool_dispatch_unknown", 409)
        result = fourth._json(receipt.result_json, receipt.result_digest, fourth.MAX_RESULT_BYTES)
        if (result.get("toolName") != dispatch.tool_name
                or result.get("auditStatus") != "recorded"
                or type(result.get("ok")) is not bool):
            _reject("完成态工具回执缺少同名审计")
        if result["ok"] is not True:
            continue
        if dispatch.state != "succeeded":
            _reject("失败工具派发不能提供读取证明")
        if dispatch.tool_name == contract.PROMOTION_TOOL:
            if role not in contract.PROMOTION_ROLES or not package_state["complete"]:
                _reject("词货视图在完整角色包前或无权角色中读取")
        else:
            _base(args, report, evidence, snapshot["screeningIntent"]["id"])
            if dispatch.tool_name != contract.PACKAGE_TOOL and not package_state["complete"]:
                _reject("其他工具必须在本人角色包完整读取后调用")
        expected = _expected(dispatch.tool_name, args, report, role, snapshot,
            reference, evidence, principal, package)
        if not _same(result.get("data"), expected):
            _reject("完成态持久工具回执与当前封存来源重算不一致")
        if dispatch.tool_name == contract.PACKAGE_TOOL:
            page = expected["pagination"]
            if (package_state["complete"] or page["offset"] != package_state["nextOffset"]):
                _reject("角色包页缺失、重复或乱序")
            package_state["pages"] += 1
            package_state["nextOffset"] = page["nextOffset"]
            package_state["complete"] = page["nextOffset"] is None
        elif dispatch.tool_name == contract.BUDGET_TOOL:
            page = expected["budget"]["pagination"]
            if (budget_state["complete"] or page["offset"] != budget_state["nextOffset"]):
                _reject("预算页缺失、重复或乱序")
            budget_state["pages"] += 1
            budget_state["started"] = True
            budget_state["nextOffset"] = page["nextOffset"]
            budget_state["complete"] = page["nextOffset"] is None
        elif dispatch.tool_name == contract.TABLE_TOOL:
            selector = {key: value for key, value in args.items()
                if key not in {"runId", "reportId", "screeningId", "offset"}}
            state = analyses.setdefault(canonical(selector), {"pages": 0,
                "nextOffset": 0, "complete": False, "seenRows": []})
            page = expected["table"]["pagination"]
            if state["complete"] or page["offset"] != state["nextOffset"]:
                _reject("同一分析表页缺失、重复或乱序")
            state["pages"] += 1
            state["nextOffset"] = page["nextOffset"]
            state["complete"] = page["nextOffset"] is None
            state["seenRows"].extend({"rowIndex": row["rowIndex"], "rowId": row["id"]}
                for row in expected["table"]["rows"])
        else:
            state = views[args["view"]]
            table_binding = expected["binding"]["tableBindingDigest"]
            if state["tableBindingDigest"] is not None and state["tableBindingDigest"] != table_binding:
                _reject("同一词货视图绑定在回执间变化")
            state["tableBindingDigest"] = table_binding
            if "row" in expected:
                row = expected["row"]
                state["rowReceipts"] += 1
                state["seenRows"].append({"rowIndex": row["rowIndex"],
                    "rowId": row["id"], "tableBindingDigest": table_binding, "via": "row"})
            else:
                page = expected["table"]["pagination"]
                if state["complete"] or page["offset"] != state["nextOffset"]:
                    _reject("词货视图页缺失、重复或乱序")
                state["pages"] += 1
                state["nextOffset"] = page["nextOffset"]
                state["complete"] = page["nextOffset"] is None
                state["seenRows"].extend({"rowIndex": row["rowIndex"],
                    "rowId": row["id"], "tableBindingDigest": table_binding, "via": "page"}
                    for row in expected["table"]["rows"])
    if (not package_state["complete"]
            or (budget_state["required"] or budget_state["started"]) and not budget_state["complete"]
            or any(state["pages"] > 0 and not state["complete"] for state in views.values())):
        _reject("完成态Agent仍缺少本人必读角色包、预算或已开始词货视图的末页")
    if _root(actual, principal)[7] != before or fourth._ledger_fence(actual.id) != ledger_before:
        _reject("完成态读取核验期间Agent、封存报告或派发账本变化")
    return {"schemaVersion": "business-promotion-completed-read-receipts-v1",
        "jobId": actual.id, "reportId": report.id, "role": role,
        "screeningId": snapshot["screeningIntent"]["id"],
        "promotionSelector": snapshot["promotionSelector"],
        "outputDigest": digest(actual.output_json), "finalProviderDispatchId": final_provider_id,
        "package": package_state, "budget": budget_state,
        "analysisSelectors": analyses, "promotionViews": views, "toolCounts": counts,
        "completedAgentReadVerified": True, "allAgentsReadVerified": False,
        "numericReferencesVerified": False, "independentReviewApproved": False,
        "humanReviewRequired": True, "runtimeAdmissionGranted": False,
        "limitations": ["只核验这个已完成Agent的实际回执；其余四角色仍须各自独立核验",
            "可选分析表允许仅阅读所需页；引用的精确行仍须单独证明",
            "数值文字、因果与人工复核不能由完成态回执自动批准"]}


def resolve_promotion_reference(job, reference, principal):
    """Resolve one cited promoted-SKU number from this completed job's reads."""
    reference = promotion_claims._reference(reference)
    proof = progress(job, principal)
    if proof["role"] not in contract.PROMOTION_ROLES:
        _reject("此完成态角色不能引用词货数值")
    selector = proof["promotionSelector"]
    if (reference["sourceKey"] != selector["sourceKey"]
            or ("baselineKey" in reference) != ("baselineKey" in selector)
            or ("baselineKey" in selector and reference["baselineKey"] != selector["baselineKey"])):
        _reject("词货引用跨固定来源或基期")
    view = proof["promotionViews"][reference["view"]]
    if not any(item["rowIndex"] == reference["rowIndex"]
            and item["rowId"] == reference["rowId"]
            and item["tableBindingDigest"] == reference["tableBindingDigest"]
            for item in view["seenRows"]):
        _reject("本人完成态工具回执没有读取所引用的词货行")
    found = fourth.owning.read_row(proof["reportId"], reference["sourceKey"],
        reference["view"], reference["rowIndex"], reference["rowId"], principal,
        baseline_key=reference.get("baselineKey"))
    row = found["row"]
    if found["binding"]["tableBindingDigest"] != reference["tableBindingDigest"]:
        _reject("词货行表绑定与完成态回执不一致")
    number, partial = promotion_claims._number(row, reference["metric"], reference["field"])
    if canonical(progress(job, principal)) != canonical(proof):
        _reject("词货数值核验期间完成态回执已变化")
    return {"reference": reference, "value": number, "partial": partial,
        "entity": row["entity"], "identityQualified": row["identityQualified"],
        "actionableKeywordSku": row["identityQualified"],
        "jobId": proof["jobId"], "reportId": proof["reportId"],
        "verification": {"numericReferenceVerified": True,
            "referenceReadVerified": True, "completedAgentReadVerified": True,
            "causalityVerified": False, "humanReviewRequired": True},
        "limitations": ["两个词货视图是同一推广费用的不同分组，不得相加",
            "归因成交不是ERP净销售或增量收益；缺推广SKU身份不能作为具体动作对象"]}
