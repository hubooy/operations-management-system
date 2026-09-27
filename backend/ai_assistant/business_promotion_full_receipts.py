"""Unregistered four-tool read proof for one running promotion-profile Agent.

Every successful page is replayed through its owning reader. This module reads
only persisted dispatches and grants neither a model call nor workflow access.
"""
from . import business_promotion_read_receipts as fourth
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools as tools
from . import business_screening_runtime_contract as screening_contract
from . import models as m
from .business_evidence_receipts import _same
from .policy import AiError, canonical, digest


def _reject(message="词货Agent完整读取证明无效"):
    raise AiError(message, "promotion_read_incomplete", 409)


def _base(args, report_id, evidence_id, screening_id):
    if (type(args) is not dict or args.get("runId") != evidence_id
            or args.get("reportId") != report_id or args.get("screeningId") != screening_id):
        _reject("工具派发跨报告、封存证据或筛查意图")


def progress(job, principal):
    """Check required role reads for this running job, not optional citations.

    ``fullAgentReadComplete`` means the role package, required budget and any
    started keyword/SKU view are complete. Native/mapped analysis tables are
    optional. If an answer cites one of their rows or a promotion row, its
    reference resolver must independently prove that exact saved read.
    """
    report, role, guard = tools._job(job.id, principal)
    fixed, actual_report, snapshot, reference, evidence = tools._current(report, principal)
    actual = m.AiAgentJobs.objects.get(pk=job.id)
    if (actual.status != "running" or actual.workflow_node_key != role
            or actual.workflow_run_id != report.workflow_id or actual.owner_email != report.owner_email):
        _reject("读取证明只适用于当前执行中的实际Agent")
    ledger_before = fourth._ledger_fence(actual.id)
    providers = fourth._providers(actual)
    dispatches = list(m.AiAgentToolDispatches.objects.filter(job_id=actual.id).order_by(
        "tool_call_ordinal")[:fourth.MAX_TOOLS + 1])
    if (len(dispatches) > fourth.MAX_TOOLS or actual.provider_round_count != len(providers)
            or actual.tool_call_count != len(dispatches)):
        _reject("实际Agent派发账本计数或容量不符")
    package = {"pages": 0, "nextOffset": 0, "complete": False}
    budget = {"pages": 0, "nextOffset": 0, "complete": False,
        "required": report.budget_plan_id is not None and role in screening_contract.BUDGET_NODES,
        "started": False}
    analyses = {}
    counts = {name: 0 for name in contract.TOOL_ORDER}
    for ordinal, dispatch in enumerate(dispatches, 1):
        if (dispatch.job_id != actual.id or dispatch.tool_call_ordinal != ordinal
                or dispatch.provider_dispatch_id not in providers
                or dispatch.tool_name not in contract.TOOLS):
            _reject("工具派发不属于本Agent的连续账本")
        provider, calls = providers[dispatch.provider_dispatch_id]
        if (not 1 <= provider.lease_epoch <= dispatch.lease_epoch <= actual.lease_epoch
                or dispatch.provider_call_id not in calls
                or calls[dispatch.provider_call_id]["name"] != dispatch.tool_name):
            _reject("工具派发没有对应的模型调用")
        args = fourth._json(dispatch.arguments_json, dispatch.arguments_digest,
            fourth.MAX_ARGUMENT_BYTES)
        if canonical(calls[dispatch.provider_call_id]["arguments"]) != dispatch.arguments_json:
            _reject("工具参数与本Agent模型回执不一致")
        counts[dispatch.tool_name] += 1
        if counts[dispatch.tool_name] > 8:
            _reject("单工具调用超过固定上限")
        if dispatch.state in {"calling", "unknown"}:
            raise AiError("工具结果未知，禁止自动重放", "tool_dispatch_unknown", 409)
        receipt = m.AiAgentToolResults.objects.filter(tool_dispatch_id=dispatch.id).first()
        if dispatch.state == "failed" and receipt is None:
            continue
        if receipt is None:
            raise AiError("成功工具派发缺少持久回执", "tool_dispatch_unknown", 409)
        result = fourth._json(receipt.result_json, receipt.result_digest,
            fourth.MAX_RESULT_BYTES)
        if (result.get("toolName") != dispatch.tool_name
                or result.get("auditStatus") != "recorded"
                or type(result.get("ok")) is not bool):
            _reject("工具回执缺少同名审计")
        if result["ok"] is not True:
            continue
        if dispatch.state != "succeeded":
            _reject("失败工具派发不能提供读取证明")
        if dispatch.tool_name == contract.PROMOTION_TOOL:
            if role not in contract.PROMOTION_ROLES or not package["complete"]:
                _reject("词货视图只可由允许角色在完整角色包之后读取")
            continue  # Fourth-tool owning replay and offset proof follow below.
        _base(args, report.id, evidence.id, snapshot["screeningIntent"]["id"])
        if dispatch.tool_name == contract.PACKAGE_TOOL:
            if (set(args) - {"runId", "reportId", "screeningId", "role", "offset"}
                    or args.get("role") != role or type(args.get("offset", 0)) is not int
                    or package["complete"] or args.get("offset", 0) != package["nextOffset"]):
                _reject("本人角色包页缺失、重复或乱序")
            operation = "package"
        elif dispatch.tool_name == contract.BUDGET_TOOL:
            if (not package["complete"] or report.budget_plan_id is None
                    or set(args) - {"runId", "reportId", "screeningId", "offset"}
                    or type(args.get("offset", 0)) is not int or budget["complete"]
                    or args.get("offset", 0) != budget["nextOffset"]):
                _reject("固定预算页缺失、重复或乱序")
            operation = "budget"
        else:
            if not package["complete"]:
                _reject("须先完整读取本人角色包")
            if (set(args) - {"runId", "reportId", "screeningId", "mode", "dimension",
                    "sourceKey", "baselineKey", "pairKey", "baselinePairKey", "offset"}
                    or type(args.get("offset", 0)) is not int):
                _reject("分析表派发选择无效")
            selector = {key: value for key, value in args.items()
                if key not in {"runId", "reportId", "screeningId", "offset"}}
            key = canonical(selector)
            state = analyses.setdefault(key, {"pages": 0, "nextOffset": 0, "complete": False})
            if state["complete"] or args.get("offset", 0) != state["nextOffset"]:
                _reject("同一分析表页缺失、重复或乱序")
            operation = "analysis"
        expected = tools.read(actual.id, operation, args, principal)
        if not _same(result.get("data"), expected):
            _reject("前三工具持久回执与当前封存来源重算不一致")
        if operation == "package":
            page = expected["pagination"]
            package["pages"] += 1
            package["nextOffset"] = page["nextOffset"]
            package["complete"] = page["nextOffset"] is None
        elif operation == "budget":
            page = expected["budget"]["pagination"]
            budget["pages"] += 1
            budget["started"] = True
            budget["nextOffset"] = page["nextOffset"]
            budget["complete"] = page["nextOffset"] is None
        else:
            page = expected["table"]["pagination"]
            state["pages"] += 1
            state["nextOffset"] = page["nextOffset"]
            state["complete"] = page["nextOffset"] is None
    promotion = fourth.progress(actual, principal) if role in contract.PROMOTION_ROLES else None
    if promotion is None and counts[contract.PROMOTION_TOOL]:
        _reject("此专业角色无权读取词货视图")
    promotion_views_complete = promotion is None or all(
        state["pages"] == 0 or state["complete"] for state in promotion["views"].values())
    if (tools._job(job.id, principal)[2] != guard
            or canonical(tools._current(actual_report, principal)[0]) != canonical(fixed)
            or fourth._ledger_fence(actual.id) != ledger_before):
        _reject("完整读取证明计算期间任务、报告或账本变化")
    complete = (package["complete"]
        and (not budget["required"] and not budget["started"] or budget["complete"])
        and promotion_views_complete)
    optional_analysis_all_complete = all(state["complete"] for state in analyses.values())
    return {"schemaVersion": "business-promotion-full-read-receipts-v1",
        "jobId": actual.id, "reportId": report.id, "role": role,
        "screeningId": fixed["screeningId"], "package": package, "budget": budget,
        "analysisSelectors": analyses,
        "optionalAnalysisAllComplete": optional_analysis_all_complete,
        "toolCounts": counts,
        "promotion": promotion, "agentReadVerified": complete,
        "fullAgentReadComplete": complete, "allAgentsReadVerified": False,
        "runtimeAdmissionGranted": False, "numericReferencesVerified": False,
        "promotionClaimsValidated": False,
        "limitations": ["仅证明当前运行中这一个Agent的必读材料和已调用表页；不代表五角色全部完成",
            "原生和商品关联分析表是可选明细；已读页可用于逐行引用，不要求读完整张表",
            "词货工具是可选读取；若结论引用词货数值，必须由本人持久回执和引用解析器逐项证明",
            "数值引用与文字结论仍须独立校验，人工批准和文件交付另行要求"]}
