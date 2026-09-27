"""Unregistered first three tools for an actual promotion-profile Agent.

This is a read adapter over already-published screening, sealed facts and a
fixed optional budget. A returned page is not an Agent reading receipt.
"""
import json
import re
from types import SimpleNamespace

from django.utils import timezone

from business_analysis import screening_package
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import VIEWS, build_table
from . import business_evidence as evidence_service
from . import business_mapped_analysis, business_promotion_runtime as runtime
from . import business_promotion_budget as promotion_budget
from . import business_screening_packages as packages, business_screening_tools as old_format
from . import models as m
from .business_sealed import Reader
from .policy import AiError, authorize_owner, canonical, current_principal, digest, fields, identifier


def _reject(message="词货筛查工具固定绑定已变化", code="conflict", status=409):
    raise AiError(message, code, status)


def _job(job_id, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        _reject("筛查Agent必须是无范围管理员", "access_denied", 403)
    job = m.AiAgentJobs.objects.filter(pk=identifier(job_id, "jobId")).first()
    if job is None:
        _reject("筛查Agent不存在", "not_found", 404)
    authorize_owner(job, principal)
    role = job.workflow_node_key
    if (role not in screening_package.ROLES or not job.workflow_run_id
            or job.status != "running" or job.cancel_requested
            or not job.lease_token or job.lease_epoch <= 0
            or job.lease_expires_at is None or job.lease_expires_at <= timezone.now()):
        _reject("实际Agent角色或租约无效")
    report = m.AiReportRun.objects.select_related("workflow").filter(workflow_id=job.workflow_run_id).first()
    if report is None:
        _reject("筛查Agent缺少固定报告")
    authorize_owner(report, principal)
    flow = report.workflow
    if (flow.status not in {"queued", "running"} or flow.cancel_requested
            or job.owner_email != flow.owner_email or job.scope_json != flow.scope_json
            or any(getattr(job, key) != getattr(flow, key) for key in
                ("model_id", "model_version", "allowed_tools_json", "tool_policy_digest"))):
        _reject("筛查Agent与工作流身份或模型不一致")
    try:
        graph = json.loads(flow.graph_json)
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
        by_key = {node.node_key:node for node in nodes}
        if (len(nodes) != 6 or len(by_key) != 6
                or [node.node_key for node in nodes] != [part["key"] for part in graph["nodes"]]):
            _reject("固定五角色图不完整")
        node = by_key[role]
        deps = json.loads(node.depends_on_json)
        if any(by_key[key].status != "completed" or not by_key[key].output_json for key in deps):
            _reject("前置Agent结论尚未完成")
        expected = {"workflowInput":json.loads(flow.input_json),
            "dependencies":{key:json.loads(by_key[key].output_json) for key in deps}}
        if (node.status != "running" or node.node_type != "agent" or node.agent_job_id != job.id
                or job.task != node.instruction or job.input_json != canonical(expected)
                or node.input_json != job.input_json):
            _reject("实际节点与固定任务输入不一致")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("实际筛查节点结构无效", "conflict", 409) from error
    guard = (job.id, job.version, job.lease_token, job.lease_epoch, job.input_json,
        flow.id, flow.version, flow.status, node.id, node.version, node.status,
        role, report.id)
    return report, role, guard


def _current(report, principal):
    fixed = runtime.bound_persisted(report.id, principal)
    if fixed["contentReady"] is not True or fixed["screeningStatus"] != "ready":
        _reject("固定筛查尚未完整发布", "screening_read_incomplete")
    actual = m.AiReportRun.objects.select_related("workflow", "budget_plan").get(pk=report.id)
    snapshot = json.loads(actual.snapshot_json)
    reference = json.loads(actual.workflow.input_json)
    evidence = evidence_service.get_run(snapshot["evidenceRunId"], principal)
    if (fixed["reportId"] != actual.id or fixed["screeningId"] != snapshot["screeningIntent"]["id"]
            or fixed["snapshotDigest"] != digest(actual.snapshot_json)
            or fixed["workflowInputDigest"] != digest(actual.workflow.input_json)
            or reference["reportId"] != actual.id or reference["screeningIntent"] != snapshot["screeningIntent"]
            or evidence.id != snapshot["evidenceRunId"] or evidence.version != snapshot["evidenceVersion"]):
        _reject("筛查报告、意图或证据在绑定期间变化")
    return fixed, actual, snapshot, reference, evidence


def _number(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        _reject("筛查工具偏移无效", "invalid_request", 400)
    return value


def _analysis(params, report, snapshot, reference, evidence, principal, *, checkpoint=None):
    fields(params, {"runId", "reportId", "screeningId", "mode", "dimension", "offset",
        "sourceKey", "baselineKey", "pairKey", "baselinePairKey"},
        {"runId", "reportId", "screeningId", "mode", "dimension"})
    offset = _number(params.get("offset", 0), 250000)
    mode, dimension = params["mode"], params["dimension"]
    if type(dimension) is not str or dimension not in VIEWS:
        _reject("分析维度无效", "invalid_request", 400)
    if mode == "native":
        if ("sourceKey" not in params or {"pairKey", "baselinePairKey"} & set(params)):
            _reject("原生分析必须选择精确来源", "invalid_request", 400)
        key = identifier(params["sourceKey"], "sourceKey")
        baseline = identifier(params["baselineKey"], "baselineKey") if "baselineKey" in params else None
        reader = Reader(evidence, principal)
        sources = {item["key"]:item for item in reader.sources}
        if key not in sources or (baseline is not None and baseline not in sources):
            _reject("来源不在固定封存目录", "access_denied", 403)
        source = sources[key]
        if source["query"]["window"] != "current":
            _reject("主来源必须是当前期", "invalid_request", 400)
        if baseline is not None:
            before = sources[baseline]
            if (source["domain"] != before["domain"] or before["query"]["window"] not in {"previous", "yearAgo"}
                    or {k:v for k,v in source["query"].items() if k != "window"}
                        != {k:v for k,v in before["query"].items() if k != "window"}):
                _reject("基期与主来源身份或原日期范围不对应", "invalid_request", 400)
        info = reader.info(key)["expected"]
        before_info = reader.info(baseline)["expected"] if baseline is not None else None
        try:
            table = build_table(reader.pages(key, checkpoint=checkpoint), dimension,
                info, offset=offset, limit=20, checkpoint=checkpoint,
                **({"baseline_pages":reader.pages(baseline, checkpoint=checkpoint),
                    "baseline_expected":before_info} if baseline else {}))
        except AnalysisContractError as error:
            raise AiError("固定分析表未通过完整来源核验", "conflict", 409) from error
        selector = {key:params[key] for key in ("sourceKey", "baselineKey", "dimension") if key in params}
    elif mode == "mapped":
        if ("pairKey" not in params or {"sourceKey", "baselineKey"} & set(params)
                or "mappingPlan" not in snapshot or dimension not in {"sku", "spu"}):
            _reject("商品关联分析没有固定选择", "invalid_request", 400)
        for key in ("pairKey", "baselinePairKey"):
            if key in params and (type(params[key]) is not str
                    or re.fullmatch(r"[a-f0-9]{64}", params[key]) is None):
                _reject("商品关联键格式无效", "invalid_request", 400)
        selector = {key:params[key] for key in ("pairKey", "baselinePairKey", "dimension") if key in params}
        pairs = {item["pairKey"] for item in snapshot["mappingPlan"]["pairs"]}
        if (params["pairKey"] not in pairs
                or ("baselinePairKey" in params and params["baselinePairKey"] not in pairs)):
            _reject("商品关联选择不在固定报告中", "access_denied", 403)
        with business_mapped_analysis.table(evidence.id, snapshot["mappingPlan"],
                params["pairKey"], dimension, principal,
                baseline_pair_key=params.get("baselinePairKey"), checkpoint=checkpoint) as opened:
            table = opened.page(offset=offset, limit=20)
    else:
        _reject("分析模式无效", "invalid_request", 400)
    return old_format._table_page(SimpleNamespace(reference=reference), mode, selector, table, offset)


def read(job_id, operation, params, principal):
    """Read one owning page; no dispatch, receipt or model side effect."""
    if operation not in {"package", "analysis", "budget"}:
        _reject("工具操作不属于本片", "invalid_request", 400)
    if type(params) is not dict:
        _reject("工具参数必须为对象", "invalid_request", 400)
    report, role, guard = _job(job_id, principal)
    fixed, actual, snapshot, reference, evidence = _current(report, principal)
    if operation == "package":
        fields(params, {"runId", "reportId", "screeningId", "role", "offset"},
            {"runId", "reportId", "screeningId", "role"})
        if params["role"] != role:
            _reject("只能读取本人实际角色包", "access_denied", 403)
        offset = _number(params.get("offset", 0), 9999)
    if (params.get("runId") != evidence.id or params.get("reportId") != actual.id
            or params.get("screeningId") != snapshot["screeningIntent"]["id"]):
        _reject("工具跨报告、封存证据或筛查意图", "access_denied", 403)
    if operation == "package":
        ready = packages.prepare(snapshot["screeningIntent"]["id"], principal)
        value = packages.page(ready, role, principal, offset=offset)
    elif operation == "analysis":
        value = _analysis(params, actual, snapshot, reference, evidence, principal)
    else:
        fields(params, {"runId", "reportId", "screeningId", "offset"},
            {"runId", "reportId", "screeningId"})
        value = promotion_budget.read_page(actual.id, principal,
            offset=_number(params.get("offset", 0), 99))
    if (_job(job_id, principal) != (report, role, guard)
            or canonical(runtime.bound_persisted(report.id, principal)) != canonical(fixed)):
        _reject("工具读取期间实际Agent或封存报告已变化")
    return value
