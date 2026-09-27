"""Non-executable pre-dispatch checks for one promotion-profile Agent step.

Complete saved tool output is semantically replayed outside the short mutation.
This process-local token has runtimeAdmissionGranted=False. A later workflow
integration must obtain a separate current capacity permit before reserving
any provider or tool dispatch; none is granted by these checks alone.
"""
from dataclasses import dataclass
import json
import re

from django.db import connection

from business_analysis.results import VIEWS
from . import business_diagnostic_screening as screening
from . import business_evidence_store as evidence_store
from . import business_promotion_admission as admission
from . import business_promotion_agent_tool as fourth
from . import business_promotion_full_receipts as receipts
from . import business_promotion_read_receipts as prefix
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools as tools
from . import business_screening_runtime_contract as screening_contract
from . import models as m, provider, transport, workflows
from .chat import SYSTEM
from .model_capabilities import fit_context
from .policy import AiError, canonical, current_principal, digest, fields

_TOKEN = object()
MAX_FRAMES_BYTES = 192 * 1024


def _reject(message="词货Agent步骤或持久前缀已变化", code="conflict", status=409):
    raise AiError(message, code, status)


@dataclass(frozen=True, slots=True, init=False)
class PreparedStep:
    _job_id: str
    _guard_json: str
    _proof_json: str
    _bound_json: str
    _catalog_json: str
    _sources_json: str
    _model_digest: str
    _guidance_digest: str
    _ledger_digest: str
    _digest: str

    def __init__(self, token, job, guard, proof, bound, entries, sources, model_digest, guidance_digest, ledger):
        if token is not _TOKEN:
            _reject("不能从公开JSON建立词货步骤")
        for key, value in (("_job_id", job.id), ("_guard_json", canonical(guard)),
                ("_proof_json", canonical(proof)), ("_bound_json", canonical(bound)),
                ("_catalog_json", canonical(entries)), ("_sources_json", canonical(sources)),
                ("_model_digest", model_digest), ("_guidance_digest", guidance_digest),
                ("_ledger_digest", ledger)):
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_digest", digest([getattr(self, key) for key in (
            "_job_id", "_guard_json", "_proof_json", "_bound_json", "_catalog_json",
            "_sources_json", "_model_digest", "_guidance_digest", "_ledger_digest")]))

    @property
    def proof(self):
        if type(self) is not PreparedStep:
            _reject("步骤证明对象无效")
        if digest([getattr(self, key) for key in ("_job_id", "_guard_json", "_proof_json",
                "_bound_json", "_catalog_json", "_sources_json", "_model_digest",
                "_guidance_digest", "_ledger_digest")]) != self._digest:
            _reject("词货步骤内部准备对象已损坏")
        return json.loads(self._proof_json)


def _guard(job, role, base):
    return {"jobId":job.id, "version":job.version, "leaseToken":job.lease_token,
        "leaseEpoch":job.lease_epoch, "ownerEmail":job.owner_email,
        "scopeJson":job.scope_json, "status":job.status,
        "workflowId":job.workflow_run_id, "role":role,
        "inputDigest":digest(job.input_json), "modelId":job.model_id,
        "modelVersion":job.model_version, "toolPolicyDigest":job.tool_policy_digest,
        "providerRoundCount":job.provider_round_count,
        "toolCallCount":job.tool_call_count, "nodeGuard":digest(base)}


def _current_job(job_id, principal):
    report, role, base = tools._job(job_id, principal)
    job = m.AiAgentJobs.objects.get(pk=job_id)
    return job, report, role, _guard(job, role, base)


def prepare(job, principal):
    """Expensive semantic prefix and live catalog preparation, no dispatch."""
    if connection.in_atomic_block:
        _reject("词货步骤准备须在最外层事务之外", "invalid_request", 400)
    current_principal(principal, admin=True)
    actual, report, role, guard = _current_job(job.id, principal)
    bound = tools.runtime.bound_persisted(report.id, principal)
    if bound["contentReady"] is not True or bound["screeningStatus"] != "ready":
        _reject("词货筛查内容未完整发布", "screening_read_incomplete")
    model = workflows.resolve_model(actual.model_id)
    if model.version != actual.model_version:
        _reject("模型版本已变化", "model_version_changed")
    entries = admission._catalog(transport.catalog(principal, contract.SURFACE), report.workflow)
    guidance = workflows.execution_guidance(actual.id)
    if guidance != workflows.execution_guidance(report.workflow_id):
        _reject("实际Agent执行指引与父工作流不一致")
    proof = receipts.progress(actual, principal)
    if proof["jobId"] != actual.id or proof["reportId"] != report.id or proof["role"] != role:
        _reject("保存的四工具回执属于其他Agent")
    ledger = prefix._ledger_fence(actual.id)
    evidence = screening._load(report.id, principal)
    sources = evidence[4]
    result = PreparedStep(_TOKEN, actual, guard,
        {**proof, "runtimeAdmissionGranted":False}, bound, entries, sources,
        _model_digest(model), digest(guidance), ledger)
    check(result, actual, principal)
    return result


def _model_digest(model):
    from .business_screening_creation import _model_digest as stable
    return stable(model)


def check(prepared, job, principal):
    """Metadata/ledger-only check suitable inside a short mutation."""
    if type(prepared) is not PreparedStep or prepared._job_id != job.id:
        _reject("缺少同一Agent的本进程准备对象")
    current_principal(principal, admin=True)
    actual, report, role, guard = _current_job(job.id, principal)
    if canonical(guard) != prepared._guard_json:
        _reject("Agent租约、角色、模型或计数变化", "lease_lost")
    loaded = screening._load(report.id, principal)
    binding = loaded[0]
    bound = json.loads(prepared._bound_json)
    if (binding["reportId"] != report.id or binding["snapshotDigest"] != bound["snapshotDigest"]
            or binding["workflowInputDigest"] != bound["workflowInputDigest"]
            or binding["sealedDigest"] != bound["rootBindings"]["sealedDigest"]
            or canonical(loaded[4]) != prepared._sources_json):
        _reject("封存报告或来源目录变化")
    model = workflows.resolve_model(actual.model_id)
    if _model_digest(model) != prepared._model_digest:
        _reject("模型配置变化", "model_version_changed")
    if (digest(workflows.execution_guidance(actual.id)) != prepared._guidance_digest
            or workflows.execution_guidance(actual.id) != workflows.execution_guidance(report.workflow_id)):
        _reject("执行指引变化")
    if prefix._ledger_fence(actual.id) != prepared._ledger_digest:
        _reject("持久模型或工具派发前缀变化")
    proof = prepared.proof
    if proof["runtimeAdmissionGranted"] is not False:
        _reject("非许可步骤被篡改为已准入")
    return proof


def _before(prepared, principal):
    proof = check(prepared, m.AiAgentJobs(id=prepared._job_id), principal)
    model = workflows.resolve_model(json.loads(prepared._guard_json)["modelId"])
    entries = json.loads(prepared._catalog_json)
    if connection.in_atomic_block:
        # Exact current catalog needs the network and belongs before mutation.
        return proof, model, entries
    actual = m.AiAgentJobs.objects.get(pk=prepared._job_id)
    report = m.AiReportRun.objects.get(workflow_id=actual.workflow_run_id)
    admission._catalog(transport.catalog(principal, contract.SURFACE), report.workflow)
    return proof, model, entries


def _frames(job_id, model):
    """Rebuild the exact known provider/tool prefix before sending it onward."""
    job = m.AiAgentJobs.objects.get(pk=job_id)
    frames = [{"role":"user", "content":job.task+"\n<task_input>"
        +job.input_json.replace("<", "\\u003c")+"</task_input>"}]
    for dispatched in m.AiAgentProviderDispatches.objects.filter(job_id=job_id).order_by("dispatch_ordinal")[:21]:
        receipt = m.AiAgentProviderResults.objects.filter(dispatch_id=dispatched.id).first()
        if receipt is None:
            _reject("模型回执未获确认，禁止构造下一轮上下文", "provider_dispatch_unknown")
        response = workflows._normalize_result(json.loads(receipt.response_json))
        if type(response.get("frame")) is not dict or type(response.get("calls")) is not list:
            _reject("持久模型帧结构无效")
        frames.append(response["frame"])
        if not response["calls"]:
            _reject("已保存终答，不得再次派发模型")
        outputs = []
        for call in response["calls"]:
            tool = m.AiAgentToolDispatches.objects.filter(provider_dispatch_id=dispatched.id,
                provider_call_id=call["id"]).first()
            result = m.AiAgentToolResults.objects.filter(tool_dispatch_id=tool.id).first() if tool else None
            if result is None:
                _reject("工具回执未知，禁止构造下一轮上下文", "tool_dispatch_unknown")
            outputs.append(json.loads(result.result_json))
        frames.extend(provider.tool_frames(model, response["calls"], outputs))
    return frames


def validate_provider_turn(prepared, frames, entries, model, principal):
    """Check exact no-truncation fit and round limits; never grant a call."""
    if connection.in_atomic_block:
        _reject("模型上下文预算检查须在最外层事务之外", "invalid_request", 400)
    proof, current_model, catalog = _before(prepared, principal)
    if (_model_digest(model) != _model_digest(current_model)
            or canonical(entries) != canonical(catalog)):
        _reject("模型或四工具目录与准备对象不一致")
    if (type(frames) is not list or len(frames) > screening_contract.MAX_TRANSCRIPT_FRAMES
            or len(canonical(frames).encode("utf-8")) > MAX_FRAMES_BYTES):
        _reject("模型上下文超过固定容量", "ai_context_budget_exceeded", 400)
    if canonical(frames) != canonical(_frames(prepared._job_id, model)):
        _reject("拟派发上下文与实际持久模型/工具回执不同")
    guard = json.loads(prepared._guard_json)
    if (guard["providerRoundCount"] >= min(model.max_tool_rounds, 20)
            or guard["toolCallCount"] >= min(model.max_total_tool_calls, 40)):
        _reject("模型轮次或工具总次数达到上限", "provider_limit_exceeded")
    fitted, info = fit_context(model, frames, SYSTEM + workflows.execution_guidance(prepared._job_id), entries)
    if info["droppedMessages"] or canonical(fitted) != canonical(frames):
        _reject("模型上下文会丢失旧回执，拒绝派发", "ai_context_budget_exceeded", 400)
    check(prepared, m.AiAgentJobs(id=prepared._job_id), principal)
    return {"fits":True, "estimatedInputTokens":info["estimatedInputTokens"],
        "runtimeAdmissionGranted":False, "modelDispatched":False}


def _analysis_selector(prepared, args, bound):
    mode, dimension = args["mode"], args["dimension"]
    if type(dimension) is not str or dimension not in VIEWS:
        _reject("分析表维度无效")
    if mode == "native":
        if ("sourceKey" not in args or {"pairKey", "baselinePairKey"} & set(args)
                or set(args) - {"runId", "reportId", "screeningId", "mode", "dimension",
                    "sourceKey", "baselineKey", "offset"}):
            _reject("原生分析选择字段无效")
        if (type(args["sourceKey"]) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", args["sourceKey"]) is None
                or ("baselineKey" in args and (type(args["baselineKey"]) is not str
                    or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", args["baselineKey"]) is None))):
            _reject("原生来源键格式无效")
        sources = {item["key"]:item for item in json.loads(prepared._sources_json)}
        source, baseline = sources.get(args["sourceKey"]), sources.get(args.get("baselineKey"))
        if source is None or source["query"]["window"] != "current":
            _reject("原生分析主来源不在固定当前期目录")
        if "baselineKey" in args and (baseline is None or source["domain"] != baseline["domain"]
                or baseline["query"]["window"] not in {"previous", "yearAgo"}
                or {key:value for key,value in source["query"].items() if key != "window"}
                    != {key:value for key,value in baseline["query"].items() if key != "window"}):
            _reject("原生分析基期不对应固定来源")
    elif mode == "mapped":
        if (dimension not in {"sku", "spu"} or "pairKey" not in args
                or {"sourceKey", "baselineKey"} & set(args)
                or set(args) - {"runId", "reportId", "screeningId", "mode", "dimension",
                    "pairKey", "baselinePairKey", "offset"}):
            _reject("商品关联分析选择字段无效")
        report = m.AiReportRun.objects.get(pk=bound["reportId"])
        snapshot = json.loads(report.snapshot_json)
        pairs = {item["pairKey"] for item in snapshot.get("mappingPlan", {}).get("pairs", [])}
        if (type(args["pairKey"]) is not str or args["pairKey"] not in pairs
                or ("baselinePairKey" in args and
                    (type(args["baselinePairKey"]) is not str or args["baselinePairKey"] not in pairs))):
            _reject("商品关联键不在当前报告固定映射目录")
    else:
        _reject("分析模式无效")
    return {key:value for key,value in args.items()
        if key not in {"runId", "reportId", "screeningId", "offset"}}


def validate_call(prepared, call, principal):
    """Validate one proposed call against the replayed prefix; no reservation."""
    proof, model, _ = _before(prepared, principal)
    fields(call, {"id", "name", "arguments"}, {"id", "name", "arguments"})
    if (type(call["id"]) is not str or not call["id"] or len(call["id"]) > 160
            or type(call["name"]) is not str or call["name"] not in contract.TOOLS
            or type(call["arguments"]) is not dict
            or len(canonical(call["arguments"]).encode("utf-8")) > prefix.MAX_ARGUMENT_BYTES):
        _reject("模型工具调用结构或名称无效")
    name, args = call["name"], call["arguments"]
    if (proof["toolCounts"][name] >= 8
            or json.loads(prepared._guard_json)["toolCallCount"] >= min(model.max_total_tool_calls, 40)):
        _reject("工具调用次数达到固定上限", "tool_limit_exceeded")
    bound = json.loads(prepared._bound_json)
    if name == contract.PROMOTION_TOOL:
        if proof["role"] not in contract.PROMOTION_ROLES or not proof["package"]["complete"]:
            _reject("词货视图需要允许角色已完整读取本人角色包")
        mode, first, _ = fourth._selector(args, bound)
        if mode == "page":
            view = proof["promotion"]["views"][args["view"]]
            if view["complete"] or first != view["nextOffset"]:
                _reject("词货视图须按当前下一偏移连续读取")
    else:
        fields(args, {"runId", "reportId", "screeningId", "role", "mode", "dimension",
            "sourceKey", "baselineKey", "pairKey", "baselinePairKey", "offset"},
            {"runId", "reportId", "screeningId"})
        if (args["runId"] != bound["rootBindings"]["evidenceRunId"]
                or args["reportId"] != bound["reportId"] or args["screeningId"] != bound["screeningId"]):
            _reject("工具调用跨报告或封存证据")
        offset = args.get("offset", 0)
        if type(offset) is not int:
            _reject("工具偏移类型无效")
        if name == contract.PACKAGE_TOOL:
            if (set(args) - {"runId", "reportId", "screeningId", "role", "offset"}
                    or args.get("role") != proof["role"] or proof["package"]["complete"]
                    or offset != proof["package"]["nextOffset"]):
                _reject("本人角色包必须按下一偏移完整读取")
        elif not proof["package"]["complete"]:
            _reject("须先完整读取本人角色包")
        elif name == contract.BUDGET_TOOL:
            if ("budgetRef" not in json.loads(m.AiReportRun.objects.get(pk=bound["reportId"]).snapshot_json)
                    or set(args) - {"runId", "reportId", "screeningId", "offset"}
                    or proof["budget"]["complete"] or offset != proof["budget"]["nextOffset"]):
                _reject("固定预算须按下一偏移读取且不能伪造空预算")
        else:
            if name != contract.TABLE_TOOL or "mode" not in args or "dimension" not in args:
                _reject("分析表选择字段无效")
            if not 0 <= offset <= 250000:
                _reject("分析表偏移超出固定范围")
            selector = _analysis_selector(prepared, args, bound)
            state = proof["analysisSelectors"].get(canonical(selector))
            if state is not None and (state["complete"] or offset != state["nextOffset"]):
                _reject("分析表必须沿已读取的下一偏移继续")
            if state is None and offset != 0:
                _reject("新分析表必须从零偏移开始")
    check(prepared, m.AiAgentJobs(id=prepared._job_id), principal)
    return {"callValidated":True, "runtimeAdmissionGranted":False,
        "toolDispatchReserved":False}
