"""Independent fixed-package proofs from the actual job's durable ledger.

Not registered in dispatch/finalization yet. Successful JSON or another job's
reading is never a substitute for this job's audited, complete page sequence.
"""
import json

from django.db.models import BigIntegerField, F, Func
from django.db.models.functions import Substr
from business_analysis import screening_package
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_screening_tools as tools, models as m
from .business_evidence_receipts import _json, _same
from .policy import AiError, authorize_owner, canonical, current_principal, digest

MAX_DISPATCHES = contract.MAX_TOOL_CALLS
MAX_ARGUMENT_BYTES = 8192
MAX_RESULT_BYTES = 256*1024
PACKAGE_TOOL, TABLE_TOOL, BUDGET_TOOL = contract.PACKAGE_TOOL, contract.TABLE_TOOL, contract.BUDGET_TOOL
TOOLS = contract.TOOLS


def _reject(message="筛查包读取证明无效，保留原回执"):
    raise AiError(message,"screening_read_incomplete",409)


def _trusted(job, snapshot, principal, *, _prepared=None):
    current_principal(principal,admin=True)
    actual_job = m.AiAgentJobs.objects.filter(pk=job.id).first()
    if actual_job is None: _reject("读取证明没有实际Agent任务")
    authorize_owner(actual_job,principal)
    if (type(snapshot) is not dict or snapshot.get("executionProfile") != contract.PROFILE
            or actual_job.workflow_node_key not in screening_package.ROLES or not actual_job.workflow_run_id
            or any(getattr(job,key,None) != getattr(actual_job,key) for key in
                ("workflow_run_id","workflow_node_key","owner_email","scope_json"))):
        _reject("筛查读取证明的执行协议、任务或角色无效")
    report = m.AiReportRun.objects.filter(workflow_id=actual_job.workflow_run_id).select_related("workflow").first()
    try:
        stored = json.loads(report.snapshot_json) if report is not None else None
        graph = contract.graph(bool(report.budget_plan_id)) if report is not None else None
        node = m.AiWorkflowNodeRuns.objects.filter(run_id=actual_job.workflow_run_id,
            node_key=actual_job.workflow_node_key,agent_job_id=actual_job.id).first()
        expected_node = next((n for n in graph["nodes"] if n["key"]==actual_job.workflow_node_key),None) if graph else None
        node_input = json.loads(actual_job.input_json)
        allowed_lists = [json.loads(actual_job.allowed_tools_json),json.loads(report.workflow.allowed_tools_json)] if report else []
        tools_match = len(allowed_lists)==2 and all(type(value) is list and len(value)==len(TOOLS)
            and all(type(name) is str for name in value) and set(value)==TOOLS for value in allowed_lists)
        if (report is None or report.owner_email != actual_job.owner_email or report.scope_json != actual_job.scope_json
                or not _same(snapshot,stored) or report.workflow.graph_json != canonical(graph)
                or report.workflow.graph_digest != digest(graph) or report.workflow.dry_run
                or node is None or expected_node is None or node.node_type != "agent"
                or node.instruction != expected_node["instruction"] or actual_job.task != node.instruction
                or node.depends_on_json != canonical(expected_node["dependsOn"])
                or node.input_json != actual_job.input_json
                or type(node_input) is not dict or set(node_input)!={"workflowInput","dependencies"}
                or not _same(node_input["workflowInput"],json.loads(report.workflow.input_json))
                or type(node_input["dependencies"]) is not dict or set(node_input["dependencies"])!=set(expected_node["dependsOn"])
                or not tools_match
                or any(getattr(actual_job,key)!=getattr(report.workflow,key) for key in
                    ("model_id","model_version","tool_policy_digest"))):
            _reject("筛查读取证明跨报告、实际节点或固定图")
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as error:
        raise AiError("筛查报告或任务协议无效","screening_read_incomplete",409) from error
    if _prepared is None:
        prepared = tools.prepare_for_report(report,principal,resolve_budget=True)
    else:
        actual, fixed, _, _, _ = tools._checked(_prepared,principal)
        if (actual.id != report.id or actual.workflow_id != report.workflow_id
                or canonical(fixed) != report.snapshot_json or not _same(fixed,snapshot)
                or bool(report.budget_plan_id) != (_prepared.budget is not None)):
            _reject("复用准备对象不属于当前实际报告或缺少固定预算")
        prepared = _prepared
    package_pages,budget_pages = tools.expected_pages(prepared,actual_job.workflow_node_key,principal)
    return report,prepared,package_pages,budget_pages


def progress(job, snapshot, principal, *, _prepared=None):
    actual,prepared,package_pages,budget_pages = _trusted(job,snapshot,principal,
        **({"_prepared":_prepared} if _prepared is not None else {}))
    decoded = screening_package.decode_pages(list(package_pages.values()))
    role = job.workflow_node_key
    if decoded["role"] != role: _reject("固定角色包与实际任务角色不一致")
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=job.id).order_by("tool_call_ordinal").annotate(
        argument_bytes=Func(F("arguments_json"),function="OCTET_LENGTH",output_field=BigIntegerField()),
        arguments_text=Substr("arguments_json",1,MAX_ARGUMENT_BYTES+1)).values(
        "id","job_id","provider_dispatch__job_id","tool_call_ordinal","tool_name","state",
        "arguments_text","argument_bytes","arguments_digest")[:MAX_DISPATCHES+1])
    if len(dispatches)>MAX_DISPATCHES: _reject("读取证明的工具派发次数超限")
    package_offset = 0
    budget_offset = 0 if budget_pages else None
    package_count = budget_count = analysis_count = 0
    tool_counts = {name:0 for name in TOOLS}
    reference = prepared.reference
    screening_id = reference["screeningIntent"]["id"]
    for ordinal,dispatched in enumerate(dispatches,1):
        if (dispatched["job_id"]!=job.id or dispatched["provider_dispatch__job_id"]!=job.id
                or type(dispatched["tool_call_ordinal"]) is not int or dispatched["tool_call_ordinal"]!=ordinal
                or dispatched["tool_name"] not in TOOLS):
            _reject("读取证明的派发身份、顺序或工具无效")
        tool_counts[dispatched["tool_name"]] += 1
        if tool_counts[dispatched["tool_name"]]>contract.MAX_CALLS_PER_TOOL:
            _reject("读取证明超过单工具固定调用上限")
        if dispatched["argument_bytes"]>MAX_ARGUMENT_BYTES: _reject("工具参数超过容量")
        args = _json(dispatched["arguments_text"],dispatched["arguments_digest"],MAX_ARGUMENT_BYTES)
        if dispatched["state"] in {"calling","unknown"}:
            raise AiError("筛查工具结果未知，禁止自动重放","tool_dispatch_unknown",409)
        if dispatched["state"] not in {"succeeded","failed"}: _reject()
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatched["id"]).annotate(
            result_bytes=Func(F("result_json"),function="OCTET_LENGTH",output_field=BigIntegerField()),
            result_text=Substr("result_json",1,MAX_RESULT_BYTES+1)).values(
            "tool_dispatch_id","result_text","result_bytes","result_digest").first()
        if receipt is None:
            if dispatched["state"]=="failed": continue
            _reject("成功派发缺少持久回执")
        if receipt["tool_dispatch_id"]!=dispatched["id"] or receipt["result_bytes"]>MAX_RESULT_BYTES:
            _reject("结果跨派发或超过容量")
        result = _json(receipt["result_text"],receipt["result_digest"],MAX_RESULT_BYTES)
        name = dispatched["tool_name"]
        if result.get("toolName")!=name or result.get("auditStatus")!="recorded" or type(result.get("ok")) is not bool:
            _reject("工具结果缺少同名审计")
        if not result["ok"]: continue
        if dispatched["state"]!="succeeded": _reject("失败派发不能提供成功读取证明")
        if (args.get("runId")!=reference["evidenceRunId"] or args.get("reportId")!=actual.id
                or args.get("screeningId")!=screening_id):
            _reject("工具参数跨报告、筛查或封存证据")
        if name==PACKAGE_TOOL:
            if (set(args)-{"runId","reportId","screeningId","role","offset"} or args.get("role")!=role
                    or type(args.get("offset",0)) is not int or package_offset is None
                    or args.get("offset",0)!=package_offset or package_offset not in package_pages):
                _reject("角色包缺页、重复、乱序或冒用其他角色")
            expected = package_pages[package_offset]
            if not _same(result.get("data"),expected): _reject("角色包回执与实际固定页不一致")
            package_offset = expected["pagination"]["nextOffset"]
            package_count += 1
        else:
            if package_offset is not None: _reject("须本人先完整读完角色包")
            if name==BUDGET_TOOL:
                if (set(args)-{"runId","reportId","screeningId","offset"} or type(args.get("offset",0)) is not int
                        or budget_offset is None or args.get("offset",0)!=budget_offset or budget_offset not in budget_pages):
                    _reject("固定预算缺失、缺页、重复或乱序")
                expected = budget_pages[budget_offset]
                if not _same(result.get("data"),expected): _reject("预算回执与重新计算的完整页不一致")
                budget_offset = expected["budget"]["pagination"]["nextOffset"]
                budget_count += 1
            else:
                expected = tools.analysis_from(prepared,args,principal)
                if not _same(result.get("data"),expected): _reject("分析页回执与固定来源重算不一致")
                analysis_count += 1
    runtime.bound(actual,principal)
    first = next(iter(package_pages.values()))
    return {"schemaVersion":"business-screening-read-proof-v1","jobId":job.id,"role":role,
        "screeningId":screening_id,"packageDigest":first["packageDigest"],
        "package":{"nextOffset":package_offset,"complete":package_offset is None,"pages":package_count,
            "expectedPages":len(package_pages)},
        "budget":{"nextOffset":budget_offset,"complete":budget_offset is None,"pages":budget_count,
            "required":bool(budget_pages) and role in contract.BUDGET_NODES,"started":budget_count>0},
        "analysisPages":analysis_count,"authority":decoded["authority"],
        "catalogDigest":reference["catalogDigest"]}


def validate_complete(job, snapshot, principal, *, _prepared=None):
    proof = progress(job,snapshot,principal,**({"_prepared":_prepared} if _prepared is not None else {}))
    if not proof["package"]["complete"]: _reject("当前Agent尚未独立读完固定角色包")
    budget = proof["budget"]
    if (budget["required"] or budget["started"]) and not budget["complete"]:
        _reject("当前Agent尚未独立读完固定预算")
    return proof
