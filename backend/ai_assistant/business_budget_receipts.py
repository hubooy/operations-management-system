"""Per-job directory and fixed-budget proofs rebuilt from immutable ledgers."""
import json

from django.db.models import BigIntegerField, F, Func
from django.db.models.functions import Substr
from business_analysis import budget_reference as contract
from business_analysis.evidence_v2 import directory_page
from . import business_budget_store as store, business_evidence, business_evidence_store as evidence_store, models as m
from .business_evidence_receipts import _json, _same
from .policy import AiError, authorize_owner, canonical, current_principal

DIRECTORY_TOOL = "get_business_budget_directory_v1"
TABLE_TOOL = "get_business_budget_analysis_table_v1"
BUDGET_TOOL = "get_business_budget_scenarios_v1"
TOOLS = frozenset({DIRECTORY_TOOL, TABLE_TOOL, BUDGET_TOOL})
SURFACE = "business_agent_budget_v1"
NODES = frozenset({"commerce", "promotion", "market_b2b", "independent_review", "report"})
BUDGET_NODES = frozenset({"promotion", "independent_review", "report"})
DIRECTORY_LIMIT = BUDGET_LIMIT = 20
MAX_DISPATCHES = 40
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 256*1024


def _reject(message="固定预算读取证明无效，保留原回执"):
    raise AiError(message, "budget_read_incomplete", 409)


def expected_pages(evidence, sources, prepared):
    """Build each trusted page once from one already reconciled result."""
    header = json.loads(evidence.plan_json)
    directory, budget = {}, {}
    offset = 0
    while offset is not None:
        page = store._call(directory_page, sources, run_id=evidence.id, evidence_version=evidence.version,
            offset=offset, limit=DIRECTORY_LIMIT, analysis_request=header.get("analysisRequest"))
        directory[offset] = page
        offset = page["nextOffset"]
    result, binding, reference = prepared.result, prepared.binding, prepared.reference
    offset = 0
    while offset is not None:
        page = store._call(contract.page, result, binding, budget_ref=reference,
            report_id=binding["reportId"], offset=offset, limit=BUDGET_LIMIT)
        budget[offset] = page
        offset = page["pagination"]["nextOffset"]
        if len(budget) > MAX_DISPATCHES:
            _reject("完整预算页超过工具总次数，不得截断")
    return directory, budget


def _trusted(job, snapshot, principal):
    current_principal(principal, admin=True)
    authorize_owner(job, principal)
    if (type(snapshot) is not dict or snapshot.get("executionProfile") != contract.PROFILE
            or job.workflow_node_key not in NODES or not job.workflow_run_id):
        _reject("固定预算证明的执行协议或节点无效")
    report = m.AiReportRun.objects.filter(workflow_id=job.workflow_run_id).select_related("workflow").first()
    try:
        stored_snapshot = json.loads(report.snapshot_json) if report is not None else None
    except (ValueError, TypeError, RecursionError) as error:
        raise AiError("固定预算报告快照无效", "budget_read_incomplete", 409) from error
    if (report is None or report.owner_email != job.owner_email or report.scope_json != job.scope_json
            or canonical(snapshot) != canonical(stored_snapshot)):
        _reject("固定预算证明跨报告、任务或授权范围")
    prepared = store.load(report, principal)  # exactly one full resolve per proof
    row = business_evidence.get_run(prepared.binding["evidenceRunId"], principal)
    sources = evidence_store.catalog(row)
    directory, budget = expected_pages(row, sources, prepared)
    return prepared, directory, budget


def progress(job, snapshot, principal):
    """Validate all saved receipts, returning only this job's next offsets."""
    prepared, directories, budgets = _trusted(job, snapshot, principal)
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id).order_by("tool_call_ordinal").annotate(
        argument_bytes=Func(F("arguments_json"), function="OCTET_LENGTH", output_field=BigIntegerField()),
        arguments_text=Substr("arguments_json", 1, MAX_ARGUMENT_BYTES+1)).values(
        "id", "job_id", "provider_dispatch__job_id", "tool_call_ordinal", "tool_name", "state",
        "arguments_text", "argument_bytes", "arguments_digest")[:MAX_DISPATCHES+1])
    if len(dispatches) > MAX_DISPATCHES:
        _reject("读取证明的工具派发次数超限")
    directory_offset = budget_offset = 0
    directory_count = budget_count = 0
    binding = prepared.binding
    for ordinal, dispatched in enumerate(dispatches, 1):
        if (dispatched["job_id"] != job.id or dispatched["provider_dispatch__job_id"] != job.id
                or type(dispatched["tool_call_ordinal"]) is not int or dispatched["tool_call_ordinal"] != ordinal
                or dispatched["tool_name"] not in TOOLS):
            _reject("读取证明的派发身份、编号或工具无效")
        if dispatched["argument_bytes"] > MAX_ARGUMENT_BYTES:
            _reject("工具参数超过容量")
        args = _json(dispatched["arguments_text"], dispatched["arguments_digest"], MAX_ARGUMENT_BYTES)
        if dispatched["state"] in {"calling", "unknown"}:
            raise AiError("预算工具结果未知，禁止自动重放", "tool_dispatch_unknown", 409)
        if dispatched["state"] not in {"succeeded", "failed"}:
            _reject()
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatched["id"]).annotate(
            result_bytes=Func(F("result_json"), function="OCTET_LENGTH", output_field=BigIntegerField()),
            result_text=Substr("result_json", 1, MAX_RESULT_BYTES+1)).values(
            "tool_dispatch_id", "result_text", "result_bytes", "result_digest").first()
        if receipt is None:
            if dispatched["state"] == "failed":
                continue
            _reject("成功派发缺少持久回执")
        if receipt["tool_dispatch_id"] != dispatched["id"] or receipt["result_bytes"] > MAX_RESULT_BYTES:
            _reject("工具结果跨派发或超过容量")
        result = _json(receipt["result_text"], receipt["result_digest"], MAX_RESULT_BYTES)
        name = dispatched["tool_name"]
        if result.get("toolName") != name or result.get("auditStatus") != "recorded" or type(result.get("ok")) is not bool:
            _reject("工具结果缺少同名成功审计")
        if not result["ok"]:
            continue
        if dispatched["state"] != "succeeded":
            _reject("失败派发不能作为成功读取证明")
        if args.get("runId") != binding["evidenceRunId"]:
            _reject("工具参数跨封存证据")
        if name == DIRECTORY_TOOL:
            if (set(args)-{"runId", "offset"} or type(args.get("offset", 0)) is not int
                    or directory_offset is None or args.get("offset", 0) != directory_offset):
                _reject("目录缺页、重复或乱序")
            expected = directories[directory_offset]
            if not _same(result.get("data"), expected):
                _reject("目录回执与可信来源完整页不一致")
            directory_offset = expected["nextOffset"]
            directory_count += 1
        else:
            if directory_offset is not None:
                _reject("须先独立读完来源目录")
            if name == BUDGET_TOOL:
                if (set(args)-{"runId", "reportId", "offset"} or args.get("reportId") != binding["reportId"]
                        or type(args.get("offset", 0)) is not int or budget_offset is None or args.get("offset", 0) != budget_offset):
                    _reject("预算请求跨报告、缺页、重复或乱序")
                expected = budgets[budget_offset]
                if not _same(result.get("data"), expected):
                    _reject("预算回执与重新计算的完整固定页不一致")
                budget_offset = expected["pagination"]["nextOffset"]
                budget_count += 1
    return {"directory": {"nextOffset": directory_offset, "complete": directory_offset is None, "pages": directory_count},
        "budget": {"nextOffset": budget_offset, "complete": budget_offset is None, "pages": budget_count,
            "required": job.workflow_node_key in BUDGET_NODES, "started": budget_count > 0},
        "catalogDigest": binding["catalogDigest"], "budgetRef": prepared.reference}


def validate_complete(job, snapshot, principal):
    proof = progress(job, snapshot, principal)
    if not proof["directory"]["complete"]:
        _reject("当前Agent尚未独立读完固定来源目录")
    budget = proof["budget"]
    if (budget["required"] or budget["started"]) and not budget["complete"]:
        _reject("当前Agent尚未独立读完全部固定预算对象")
    return proof
