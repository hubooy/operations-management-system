"""Read-only, fourth-tool receipt proof for one persisted promotion Agent.

This verifies the selected keyword/SKU view against sealed rows. It does not
verify the first three tools, authorize dispatch, or grant full Agent reading.
"""
import json

from . import business_promotion_keyword_sku as owning
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import models as m
from .business_evidence_receipts import _same
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


MAX_PROVIDERS = 20
MAX_TOOLS = 40
MAX_PROMOTION_CALLS = 8
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 256 * 1024


def _reject(message="词货实际读取回执无效，保留原持久记录", code="promotion_read_incomplete"):
    raise AiError(message, code, 409)


def _json(raw, expected_digest, maximum):
    try:
        if (type(raw) is not str or len(raw.encode("utf-8")) > maximum
                or digest(raw) != expected_digest):
            _reject("派发或回执摘要、字节容量无效")
    except UnicodeError as error:
        raise AiError("派发或回执编码无效", "promotion_read_incomplete", 409) from error
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("词货持久回执不是有效JSON", "promotion_read_incomplete", 409) from error
    if type(value) is not dict or canonical(value) != raw:
        _reject("派发或回执不是无重复键的规范对象")
    return value


def _trusted(job, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("词货读取证明只允许无范围管理员", "access_denied")
    actual = m.AiAgentJobs.objects.filter(pk=identifier(job.id, "jobId")).first()
    if actual is None:
        _reject("实际Agent不存在")
    authorize_owner(actual, principal)
    report = m.AiReportRun.objects.select_related("workflow").filter(
        workflow_id=actual.workflow_run_id).first()
    if report is None:
        _reject("实际Agent缺少固定报告")
    authorize_owner(report, principal)
    flow = report.workflow
    fixed = runtime.bound_persisted(report.id, principal)
    role = actual.workflow_node_key
    try:
        graph = contract.graph(bool(report.budget_plan_id))
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
        by_key = {node.node_key: node for node in nodes}
        node = by_key[role]
        expected_node = next(spec for spec in graph["nodes"] if spec["key"] == role)
        dependencies = json.loads(node.depends_on_json)
        expected_input = {"workflowInput": json.loads(flow.input_json),
            "dependencies": {key: json.loads(by_key[key].output_json) for key in dependencies}}
        if (role not in contract.PROMOTION_ROLES or fixed["contentReady"] is not True
                or fixed["screeningStatus"] != "ready" or fixed["reportId"] != report.id
                or actual.owner_email != report.owner_email or actual.scope_json != report.scope_json
                or flow.owner_email != report.owner_email or flow.scope_json != report.scope_json
                or flow.cancel_requested or actual.cancel_requested
                or actual.status not in {"running", "completed"}
                or node.status not in {"running", "completed"} or node.agent_job_id != actual.id
                or (node.status == "completed") != (actual.status == "completed")
                or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
                or len(nodes) != len(graph["nodes"])
                or any((saved.position, saved.node_key, saved.node_type, saved.instruction,
                    saved.depends_on_json) != (index, spec["key"], spec["type"],
                    spec["instruction"], canonical(spec["dependsOn"]))
                    for index, (saved, spec) in enumerate(zip(nodes, graph["nodes"])))
                or dependencies != expected_node["dependsOn"]
                or any(by_key[key].status != "completed" for key in dependencies)
                or node.input_json != canonical(expected_input)
                or actual.input_json != node.input_json or actual.task != node.instruction
                or actual.allowed_tools_json != flow.allowed_tools_json
                or flow.allowed_tools_json != canonical(list(contract.TOOL_ORDER))
                or any(getattr(actual, key) != getattr(flow, key) for key in
                    ("model_id", "model_version", "tool_policy_digest"))):
            _reject("词货读取证明跨报告、实际节点或固定图")
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration, RecursionError) as error:
        raise AiError("词货Agent持久输入或节点无效", "promotion_read_incomplete", 409) from error
    guard = [actual.id, actual.version, actual.status, actual.owner_email, actual.scope_json,
        actual.input_json, actual.allowed_tools_json, actual.tool_policy_digest,
        actual.workflow_run_id, actual.workflow_node_key, actual.tool_call_count,
        report.id, report.snapshot_json, flow.id, flow.version, flow.status,
        flow.input_json, flow.graph_json, node.id, node.version, node.status,
        node.input_json, node.output_json, canonical(fixed)]
    return actual, report, role, fixed, digest(guard)


def _providers(job):
    rows = list(m.AiAgentProviderDispatches.objects.filter(job_id=job.id).order_by(
        "dispatch_ordinal")[:MAX_PROVIDERS + 1])
    if len(rows) > MAX_PROVIDERS:
        _reject("模型派发数量超限")
    result = {}
    for ordinal, row in enumerate(rows, 1):
        if (row.dispatch_ordinal != ordinal or row.job_id != job.id
                or row.owner_email != job.owner_email or row.actor_role != "admin"
                or row.model_id != job.model_id or row.model_version != job.model_version
                or row.tool_policy_digest != job.tool_policy_digest or row.lease_epoch < 1):
            _reject("模型派发序号、身份或策略不一致")
        if row.state != "succeeded":
            _reject("模型派发结果未知或失败，禁止推断读取", "provider_dispatch_unknown")
        receipt = m.AiAgentProviderResults.objects.filter(dispatch_id=row.id).first()
        if receipt is None:
            _reject("模型派发缺少持久回执", "provider_dispatch_unknown")
        value = _json(receipt.response_json, receipt.response_digest, MAX_RESULT_BYTES)
        calls = value.get("calls", value.get("toolCalls"))
        if type(calls) is not list or len(calls) > MAX_TOOLS:
            _reject("模型回执没有可核对的工具调用清单")
        ids = set()
        for call in calls:
            if (type(call) is not dict or type(call.get("id")) is not str
                    or not call["id"] or call["id"] in ids
                    or type(call.get("name")) is not str or type(call.get("arguments")) is not dict):
                _reject("模型回执工具调用身份无效")
            ids.add(call["id"])
        result[row.id] = (row, {call["id"]: call for call in calls})
    return result


def _ledger_fence(job_id):
    """Bounded persisted prefix fingerprint; never a substitute for replay."""
    providers = list(m.AiAgentProviderDispatches.objects.filter(job_id=job_id).order_by(
        "dispatch_ordinal")[:MAX_PROVIDERS + 1])
    tools = list(m.AiAgentToolDispatches.objects.filter(job_id=job_id).order_by(
        "tool_call_ordinal")[:MAX_TOOLS + 1])
    if len(providers) > MAX_PROVIDERS or len(tools) > MAX_TOOLS:
        _reject("派发账本超过固定容量")
    provider_rows, tool_rows = [], []
    for row in providers:
        receipt = m.AiAgentProviderResults.objects.filter(dispatch_id=row.id).first()
        provider_rows.append([row.id, row.job_id, row.dispatch_ordinal, row.owner_email,
            row.actor_role, row.model_id, row.model_version, row.tool_policy_digest,
            row.request_digest, row.lease_epoch, row.state,
            receipt.response_digest if receipt is not None else None,
            digest(receipt.response_json) if receipt is not None else None])
    for row in tools:
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=row.id).first()
        tool_rows.append([row.id, row.job_id, row.provider_dispatch_id, row.tool_call_ordinal,
            row.provider_call_id, row.tool_name, row.arguments_digest, digest(row.arguments_json),
            row.invocation_id, row.lease_epoch, row.state,
            receipt.result_digest if receipt is not None else None,
            digest(receipt.result_json) if receipt is not None else None])
    return digest([provider_rows, tool_rows])


def _selector(args, fixed):
    if type(args) is not dict or not {"reportId", "sourceKey", "view"} <= set(args):
        _reject("词货派发缺少固定选择")
    selector = fixed["promotionSelector"]
    if (args["reportId"] != fixed["reportId"] or args["sourceKey"] != selector["sourceKey"]
            or args["view"] not in selector["views"]
            or ("baselineKey" in args) != ("baselineKey" in selector)
            or ("baselineKey" in selector and args["baselineKey"] != selector["baselineKey"])):
        _reject("词货派发跨报告、来源、基期或视图")
    row_mode = "rowIndex" in args or "rowId" in args
    if row_mode:
        if (set(args) != ({"reportId", "sourceKey", "view", "rowIndex", "rowId"}
                | ({"baselineKey"} if "baselineKey" in selector else set()))
                or type(args["rowIndex"]) is not int
                or not 0 <= args["rowIndex"] < owning.MAX_ROWS
                or type(args["rowId"]) is not str or len(args["rowId"]) != 64):
            _reject("词货精确行派发身份无效")
        return "row"
    if (set(args) - {"reportId", "sourceKey", "view", "baselineKey", "offset", "limit"}
            or type(args.get("offset", 0)) is not int or not 0 <= args.get("offset", 0) <= owning.MAX_ROWS
            or type(args.get("limit", 20)) is not int or args.get("limit", 20) != 20):
        _reject("词货分页派发参数无效")
    return "page"


def _expected(report_id, args, mode, principal):
    if mode == "row":
        return owning.read_row(report_id, args["sourceKey"], args["view"],
            args["rowIndex"], args["rowId"], principal, baseline_key=args.get("baselineKey"))
    return owning.page(report_id, {"sourceKey": args["sourceKey"], "view": args["view"],
        "offset": args.get("offset", 0), "limit": 20,
        **({"baselineKey": args["baselineKey"]} if "baselineKey" in args else {})}, principal)


def progress(job, principal):
    """Verify only this job's saved fourth-tool reads; grant no dispatch right."""
    actual, report, role, fixed, before = _trusted(job, principal)
    ledger_before = _ledger_fence(actual.id)
    providers = _providers(actual)
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=actual.id).order_by(
        "tool_call_ordinal")[:MAX_TOOLS + 1])
    if len(dispatches) > MAX_TOOLS:
        _reject("工具派发数量超限")
    if (actual.provider_round_count != len(providers)
            or actual.tool_call_count != len(dispatches)):
        _reject("实际Agent计数与完整派发账本不一致")
    views = {view: {"pages": 0, "nextOffset": 0, "complete": False,
        "rowReceipts": 0, "seenRows": [], "tableBindingDigest": None}
        for view in fixed["promotionSelector"]["views"]}
    promotion_calls = 0
    for ordinal, dispatch in enumerate(dispatches, 1):
        if (dispatch.tool_call_ordinal != ordinal or dispatch.job_id != actual.id
                or dispatch.provider_dispatch_id not in providers or dispatch.lease_epoch < 1
                or dispatch.tool_name not in contract.TOOLS):
            _reject("工具派发序号、模型归属或目录无效")
        provider, calls = providers[dispatch.provider_dispatch_id]
        # A model response and its tool are separate microsteps. Their leases
        # may advance, but must stay ordered within this same actual job.
        if (not 1 <= provider.lease_epoch <= dispatch.lease_epoch <= actual.lease_epoch
                or dispatch.provider_call_id not in calls
                or calls[dispatch.provider_call_id]["name"] != dispatch.tool_name):
            _reject("工具派发未绑定本Agent的模型调用")
        args = _json(dispatch.arguments_json, dispatch.arguments_digest, MAX_ARGUMENT_BYTES)
        if canonical(calls[dispatch.provider_call_id]["arguments"]) != dispatch.arguments_json:
            _reject("工具参数不是该模型调用的原始固定参数")
        if dispatch.state in {"calling", "unknown"}:
            _reject("工具结果未知，禁止自动重放", "tool_dispatch_unknown")
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatch.id).first()
        if dispatch.state == "failed" and receipt is None:
            continue
        if receipt is None:
            _reject("成功工具派发缺少持久回执", "tool_dispatch_unknown")
        result = _json(receipt.result_json, receipt.result_digest, MAX_RESULT_BYTES)
        if (result.get("toolName") != dispatch.tool_name or result.get("auditStatus") != "recorded"
                or type(result.get("ok")) is not bool):
            _reject("工具回执缺少同名成功审计")
        if not result["ok"]:
            if dispatch.state != "failed":
                # The runtime may persist known tool errors as succeeded. They
                # still prove no page; only an unknown outcome blocks progress.
                pass
            continue
        if dispatch.state != "succeeded":
            _reject("失败工具派发不能作为读取证明")
        if dispatch.tool_name != contract.PROMOTION_TOOL:
            continue
        promotion_calls += 1
        if promotion_calls > MAX_PROMOTION_CALLS:
            _reject("词货工具实际派发次数超过固定上限")
        mode = _selector(args, fixed)
        expected = _expected(report.id, args, mode, principal)
        if not _same(result.get("data"), expected):
            _reject("词货回执与当前封存来源重算不一致")
        state = views[args["view"]]
        table_binding = expected["binding"]["tableBindingDigest"]
        if state["tableBindingDigest"] is not None and state["tableBindingDigest"] != table_binding:
            _reject("同一词货视图的表绑定在回执间变化")
        state["tableBindingDigest"] = table_binding
        if mode == "row":
            row = expected["row"]
            state["rowReceipts"] += 1
            state["seenRows"].append({"rowIndex": row["rowIndex"], "rowId": row["id"],
                "via": "row", "tableBindingDigest": table_binding})
        else:
            page = expected["table"]
            pagination = page["pagination"]
            if state["complete"] or pagination["offset"] != state["nextOffset"]:
                _reject("词货视图页缺失、重复或偏移乱序")
            state["pages"] += 1
            state["nextOffset"] = pagination["nextOffset"]
            state["complete"] = pagination["nextOffset"] is None
            state["seenRows"].extend({"rowIndex": row["rowIndex"], "rowId": row["id"],
                "via": "page", "tableBindingDigest": table_binding} for row in page["rows"])
    if _trusted(actual, principal)[4] != before or _ledger_fence(actual.id) != ledger_before:
        _reject("词货回执校验期间Agent或封存报告已变化")
    return {"schemaVersion": "business-promotion-read-receipts-v1",
        "jobId": actual.id, "reportId": report.id, "role": role,
        "screeningId": fixed["screeningId"], "promotionSelector": fixed["promotionSelector"],
        "toolName": contract.PROMOTION_TOOL,
        "durablePromotionReceiptsVerified": promotion_calls > 0,
        "promotionToolCalls": promotion_calls, "views": views,
        "agentReadVerified": False, "fullAgentReadComplete": False,
        "limitations": ["仅核验本Agent词货工具回执；其他三项工具和完整角色包读取须另行证明",
            "已读词货页或精确行不代表整份推广来源逐行读完"]}
