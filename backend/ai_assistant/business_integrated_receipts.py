"""Recompute per-job integrated directory, budget and analysis receipts.

Only immutable audited dispatches count; no model assertion or sibling job
can provide coverage. All successful analysis pages are recalculated locally.
"""
import json

from django.db.models import BigIntegerField, F, Func
from django.db.models.functions import Substr
from . import business_integrated as contract, business_integrated_tools as tools, models as m
from .business_evidence_receipts import _json, _same
from .policy import AiError, authorize_owner, current_principal

DIRECTORY_TOOL, TABLE_TOOL, BUDGET_TOOL = contract.DIRECTORY_TOOL, contract.TABLE_TOOL, contract.BUDGET_TOOL
TOOLS, NODES, BUDGET_NODES, MAPPED_NODES = contract.TOOLS, contract.NODES, contract.BUDGET_NODES, contract.MAPPED_NODES
MAX_DISPATCHES = 40
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 256*1024


def _reject(message="集成分析读取证明无效，保留原回执"):
    raise AiError(message, "integrated_read_incomplete", 409)


def _trusted(job, snapshot, principal, *, _reuse=None):
    current_principal(principal, admin=True)
    authorize_owner(job, principal)
    if (type(snapshot) is not dict or snapshot.get("executionProfile") != contract.PROFILE
            or job.workflow_node_key not in NODES or not job.workflow_run_id):
        _reject("集成读取证明的执行协议或节点无效")
    report = m.AiReportRun.objects.filter(workflow_id=job.workflow_run_id).select_related("workflow").first()
    try:
        stored = json.loads(report.snapshot_json) if report is not None else None
    except (ValueError, TypeError, RecursionError) as error:
        raise AiError("集成报告快照无效", "integrated_read_incomplete", 409) from error
    if (report is None or report.owner_email != job.owner_email or report.scope_json != job.scope_json
            or not _same(snapshot, stored)):
        _reject("集成读取证明跨报告、任务或授权范围")
    actual, prepared, evidence, sources = tools.prepare_for_report(report, principal, resolve_budget=True,
        **({"_reuse":_reuse} if _reuse is not None else {}))
    if actual.workflow_id != job.workflow_run_id:
        _reject("集成报告与当前任务工作流不一致")
    directories, budgets = tools.expected_pages(prepared, evidence, sources)
    return actual, prepared, evidence, sources, directories, budgets


def progress(job, snapshot, principal, *, _reuse=None):
    """Read <=40 dispatches and one bounded receipt at a time; never replay."""
    actual, prepared, evidence, sources, directories, budgets = _trusted(job, snapshot, principal,
        **({"_reuse":_reuse} if _reuse is not None else {}))
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id).order_by("tool_call_ordinal").annotate(
        argument_bytes=Func(F("arguments_json"), function="OCTET_LENGTH", output_field=BigIntegerField()),
        arguments_text=Substr("arguments_json", 1, MAX_ARGUMENT_BYTES+1)).values(
        "id", "job_id", "provider_dispatch__job_id", "tool_call_ordinal", "tool_name", "state",
        "arguments_text", "argument_bytes", "arguments_digest")[:MAX_DISPATCHES+1])
    if len(dispatches) > MAX_DISPATCHES:
        _reject("读取证明的工具派发次数超限")
    directory_offset = 0
    budget_offset = 0 if budgets else None
    directory_count = budget_count = mapped_count = analysis_count = 0
    binding = prepared.snapshot
    for ordinal, dispatched in enumerate(dispatches, 1):
        if (dispatched["job_id"] != job.id or dispatched["provider_dispatch__job_id"] != job.id
                or type(dispatched["tool_call_ordinal"]) is not int or dispatched["tool_call_ordinal"] != ordinal
                or dispatched["tool_name"] not in TOOLS):
            _reject("读取证明的派发身份、编号或工具无效")
        if dispatched["argument_bytes"] > MAX_ARGUMENT_BYTES:
            _reject("工具参数超过容量")
        args = _json(dispatched["arguments_text"], dispatched["arguments_digest"], MAX_ARGUMENT_BYTES)
        if dispatched["state"] in {"calling", "unknown"}:
            raise AiError("集成工具结果未知，禁止自动重放", "tool_dispatch_unknown", 409)
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
        if args.get("runId") != binding["evidenceRunId"] or args.get("reportId") != binding["reportId"]:
            _reject("工具参数跨报告或封存证据")
        if name == DIRECTORY_TOOL:
            if (set(args)-{"runId", "reportId", "offset"} or type(args.get("offset", 0)) is not int
                    or directory_offset is None or args.get("offset", 0) != directory_offset
                    or directory_offset not in directories):
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
                if (set(args)-{"runId", "reportId", "offset"} or type(args.get("offset", 0)) is not int
                        or budget_offset is None or args.get("offset", 0) != budget_offset or budget_offset not in budgets):
                    _reject("预算未固定、缺页、重复或乱序")
                expected = budgets[budget_offset]
                if not _same(result.get("data"), expected):
                    _reject("预算回执与重新计算的完整固定页不一致")
                budget_offset = expected["budget"]["pagination"]["nextOffset"]
                budget_count += 1
            else:
                expected = tools.analysis_from(prepared, evidence, sources, args, principal,
                    **({"_reuse":_reuse} if _reuse is not None else {}))
                if not _same(result.get("data"), expected):
                    _reject("分析回执与固定来源重新计算的完整页不一致")
                analysis_count += 1
                if args.get("mode") == "mapped":
                    mapped_count += 1
    # A late permission or fixed-binding failure must not publish a proof.
    contract.bound(actual, principal)
    return {"directory": {"nextOffset": directory_offset, "complete": directory_offset is None, "pages": directory_count},
        "budget": {"nextOffset": budget_offset, "complete": budget_offset is None, "pages": budget_count,
            "required": bool(budgets) and job.workflow_node_key in BUDGET_NODES, "started": budget_count > 0},
        "mapped": {"required": job.workflow_node_key in MAPPED_NODES, "complete": mapped_count > 0, "pages": mapped_count},
        "analysisPages": analysis_count, "catalogDigest": binding["catalogDigest"],
        "mappingRef": prepared.reference["mappingRef"], "budgetRef": prepared.reference.get("budgetRef")}


def validate_complete(job, snapshot, principal, *, _reuse=None):
    proof = progress(job, snapshot, principal, **({"_reuse":_reuse} if _reuse is not None else {}))
    if not proof["directory"]["complete"]:
        _reject("当前Agent尚未独立读完固定来源目录")
    budget = proof["budget"]
    if (budget["required"] or budget["started"]) and not budget["complete"]:
        _reject("当前Agent尚未独立读完全部固定预算对象")
    if proof["mapped"]["required"] and not proof["mapped"]["complete"]:
        _reject("当前Agent尚未独立读取真实映射分析页")
    return proof
