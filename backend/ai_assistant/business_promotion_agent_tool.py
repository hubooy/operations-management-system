"""Unregistered fourth promotion tool for one actual running Agent node.

The owning reader proves selected sealed facts. This adapter grants no model
dispatch, tool ledger entry, or Agent reading receipt by returning a page.
"""
import json
import re

from django.utils import timezone

from . import business_promotion_keyword_sku as owning
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, fields, identifier

MAX_ROWS = owning.MAX_ROWS
TOOL_FIELDS = frozenset(("reportId", "sourceKey", "view", "baselineKey", "offset", "limit", "rowIndex", "rowId"))


def _reject(message="词货工具的实际Agent绑定已变化", code="conflict", status=409):
    raise AiError(message, code, status)


def _active(job_id, principal):
    """Derive role and identity only from current persisted job and node."""
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("词货Agent必须是无范围管理员", "access_denied", 403)
    job = m.AiAgentJobs.objects.filter(pk=identifier(job_id, "jobId")).first()
    if job is None:
        _reject("词货Agent不存在", "not_found", 404)
    authorize_owner(job, principal)
    role = job.workflow_node_key
    if (role not in contract.PROMOTION_ROLES or not job.workflow_run_id
            or job.status != "running" or job.cancel_requested
            or not job.lease_token or job.lease_epoch <= 0
            or job.lease_expires_at is None or job.lease_expires_at <= timezone.now()):
        _reject("词货Agent没有有效的运行角色或租约")
    report = m.AiReportRun.objects.select_related("workflow").filter(workflow_id=job.workflow_run_id).first()
    if report is None:
        _reject("词货Agent缺少固定报告")
    authorize_owner(report, principal)
    flow = report.workflow
    if (flow.status not in ("queued", "running") or flow.cancel_requested
            or job.owner_email != flow.owner_email or job.scope_json != flow.scope_json
            or any(getattr(job, key) != getattr(flow, key) for key in
                ("model_id", "model_version", "allowed_tools_json", "tool_policy_digest"))):
        _reject("词货Agent与父工作流身份或模型不一致")
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
    try:
        graph = json.loads(flow.graph_json)
        by_key = {node.node_key: node for node in nodes}
        if (len(nodes) != 6 or len(by_key) != 6 or
                [node.node_key for node in nodes] != [spec["key"] for spec in graph["nodes"]]):
            _reject("固定工作流节点不完整")
        node = by_key[role]
        deps = json.loads(node.depends_on_json)
        if any(by_key[key].status != "completed" or not by_key[key].output_json for key in deps):
            _reject("词货Agent的前置结论尚未完成")
        expected_input = {"workflowInput":json.loads(flow.input_json),
            "dependencies":{key:json.loads(by_key[key].output_json) for key in deps}}
        if (node.status != "running" or node.agent_job_id != job.id
                or node.node_type != "agent" or job.task != node.instruction
                or job.input_json != canonical(expected_input)
                or node.input_json != job.input_json):
            _reject("实际Agent节点与固定输入不一致")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("词货Agent持久图或输入无效", "conflict", 409) from error
    guard = (job.id, job.version, job.lease_token, job.lease_epoch,
        job.input_json, job.model_id, job.model_version, job.tool_policy_digest,
        flow.id, flow.version, flow.status, node.id, node.version, node.status,
        role, report.id)
    return report.id, role, guard


def _selector(arguments, fixed):
    fields(arguments, TOOL_FIELDS, {"reportId", "sourceKey", "view"})
    selected = fixed["promotionSelector"]
    if (arguments["reportId"] != fixed["reportId"]
            or arguments["sourceKey"] != selected["sourceKey"]
            or arguments["view"] not in selected["views"]
            or ("baselineKey" in arguments) != ("baselineKey" in selected)
            or ("baselineKey" in selected and arguments["baselineKey"] != selected["baselineKey"])):
        _reject("词货工具只能读取报告固定来源、基期和视图", "access_denied", 403)
    row_mode = "rowIndex" in arguments or "rowId" in arguments
    if row_mode:
        if ({"rowIndex", "rowId"} - set(arguments)) or {"offset", "limit"} & set(arguments):
            _reject("行读取必须给完整身份且不能混合分页", "invalid_request", 400)
        index, row_id = arguments["rowIndex"], arguments["rowId"]
        if (type(index) is not int or not 0 <= index < MAX_ROWS
                or type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None):
            _reject("词货行引用无效", "invalid_request", 400)
        return "row", index, row_id
    if (type(arguments.get("offset", 0)) is not int
            or not 0 <= arguments.get("offset", 0) <= MAX_ROWS
            or type(arguments.get("limit", 20)) is not int or arguments.get("limit", 20) != 20):
        _reject("词货分页参数无效", "invalid_request", 400)
    return "page", arguments.get("offset", 0), 20


def read(job_id, arguments, principal):
    """Read sealed rows for an actual node; return no Agent receipt."""
    report_id, role, guard = _active(job_id, principal)
    fixed = runtime.bound_persisted(report_id, principal)
    if fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready":
        _reject("词货报告筛查内容尚未完整发布", "screening_read_incomplete")
    mode, first, second = _selector(arguments, fixed)
    if mode == "row":
        result = owning.read_row(report_id, arguments["sourceKey"], arguments["view"],
            first, second, principal, baseline_key=arguments.get("baselineKey"))
    else:
        result = owning.page(report_id, {"sourceKey":arguments["sourceKey"],
            "view":arguments["view"], "offset":first, "limit":second,
            **({"baselineKey":arguments["baselineKey"]} if "baselineKey" in arguments else {})}, principal)
    # The owning context has exited. Recheck the actual node and all report
    # roots before exposing the result; these checks are not receipt writes.
    if (_active(job_id, principal) != (report_id, role, guard)
            or canonical(runtime.bound_persisted(report_id, principal)) != canonical(fixed)):
        _reject("词货工具读取期间Agent或封存报告已变化")
    return result
