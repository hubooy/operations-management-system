"""Owning, read-only capacity measurement for the four-tool promotion profile.

Complete published role pages and optional budget pages are measured under
both provider message formats. A successful candidate is not a dispatch lease,
Agent reading receipt, or permission to call a model.
"""
from dataclasses import dataclass
from copy import copy
import json
from types import SimpleNamespace

from django.db import connection

from business_analysis import screening_package
from business_analysis.contracts import AnalysisContractError
from . import business_promotion_admission as policy, business_promotion_budget as budget
from . import business_promotion_readiness as readiness
from . import business_promotion_runtime as runtime, business_promotion_runtime_contract as contract
from . import business_screening_packages as packages, business_screening_runtime_contract as old
from . import business_screening_creation, model_capabilities, models as m, provider, transport, workflows
from .chat import SYSTEM
from .policy import AiError, canonical, current_principal, digest, identifier

SCHEMA = "business-promotion-capacity-preflight-v1"
MAX_TOTAL_PAGE_BYTES = screening_package.MAX_ALL_PACKAGE_BYTES
_TOKEN = object()


def _require(ok, message="词货容量固定绑定已变化", code="conflict", status=409):
    if not ok:
        raise AiError(message, code, status)


def _current(report_id, principal, *, allow_parked=False):
    current_principal(principal, admin=True)
    _require(principal.scope is None, "词货容量仅允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    bound = runtime.bound_persisted(report_id, principal)
    _require(bound["contentReady"] is True and bound["screeningStatus"] == "ready"
        and bound["screeningReference"] is not None and bound["runtimeRegistered"] is False,
        "词货筛查尚未完整发布")
    report = m.AiReportRun.objects.select_related("workflow").get(pk=report_id)
    flow = report.workflow
    parked = (flow.status == "paused" and flow.retryable == 0
        and flow.error_code == readiness.PARKED_CODE
        and not flow.lease_token and flow.lease_expires_at is None
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
    _require((parked if allow_parked else flow.status in {"queued", "running"})
        and not flow.cancel_requested and flow.dry_run == 0
        and flow.id == bound["workflowId"] and flow.model_id == bound["modelId"]
        and flow.model_version == bound["modelVersion"], "词货工作流状态或模型已变化")
    entries = policy._catalog(transport.catalog(principal, contract.SURFACE), flow)
    snapshot, reference = json.loads(report.snapshot_json), json.loads(flow.input_json)
    graph = workflows.validate_graph(contract.graph("budgetRef" in snapshot))
    _require(flow.graph_json == canonical(graph) and flow.graph_digest == digest(graph)
        and reference["promotionRef"]["promotionSelector"] == bound["promotionSelector"]
        and digest(report.snapshot_json) == bound["snapshotDigest"]
        and digest(flow.input_json) == bound["workflowInputDigest"],
        "词货实际五角色图或输入已变化")
    model = workflows.resolve_model(flow.model_id)
    _require(model.id == flow.model_id and model.version == flow.model_version,
        "词货固定模型版本已变化", "model_version_changed")
    guidance = workflows.execution_guidance(flow.id)
    _require(type(guidance) is str and len(guidance.encode()) <= 32000,
        "词货执行指引超出容量")
    cfg = model_capabilities.options(model)
    fixed = {"reportId": report_id, "workflowId": flow.id,
        "ownerEmail": principal.email.lower(),
        "workflowStatus": flow.status, "workflowVersion": flow.version,
        "workflowErrorCode": flow.error_code,
        "scopeDigest": digest(principal.scope), "boundDigest": digest(bound),
        "snapshotDigest": bound["snapshotDigest"], "workflowInputDigest": bound["workflowInputDigest"],
        "graphDigest": flow.graph_digest, "toolCatalogDigest": digest(entries),
        "screeningReference": bound["screeningReference"],
        "modelId": model.id, "modelVersion": model.version,
        "modelConfigurationDigest": business_screening_creation._model_digest(model),
        "modelOptionsDigest": digest(cfg), "guidanceDigest": digest(guidance)}
    return report, bound, reference, model, guidance, entries, fixed


def _role_pages(screening_id, principal, saved):
    ready = packages.prepare(screening_id, principal)
    description = packages.describe(ready, principal)
    _require(canonical(description["reference"]) == canonical(saved),
        "词货角色包不属于当前完整发布")
    values, total_bytes = {}, 0
    for role in screening_package.ROLES:
        offset, pages = 0, []
        while True:
            _require(offset is not None and len(pages) < screening_package.MAX_RECORDS,
                "词货角色包分页未结束")
            page = packages.page(ready, role, principal, offset=offset)
            size = len(canonical(page).encode())
            total_bytes += size
            _require(size <= old.MAX_TOOL_BYTES and total_bytes <= MAX_TOTAL_PAGE_BYTES,
                "词货五角色完整页超过容量", "payload_too_large", 413)
            _require(page["packageDigest"] == description["roles"][role]["packageDigest"]
                and page["pagination"]["offset"] == offset,
                "词货角色包页绑定变化")
            pages.append(page)
            following = page["pagination"]["nextOffset"]
            if following is None:
                break
            _require(type(following) is int and following > offset,
                "词货角色包分页未推进")
            offset = following
        try:
            decoded = screening_package.decode_pages(pages)
        except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            raise AiError("词货角色包不完整", "conflict", 409) from error
        _require(decoded["role"] == role, "词货角色包角色不一致")
        values[role] = pages
    packages.describe(ready, principal)
    return values, description


def _budget_pages(report_id, principal, required):
    if not required:
        return []
    pages, offset = [], 0
    while True:
        _require(offset is not None and len(pages) < 100,
            "词货预算分页未结束")
        value = budget.read_page(report_id, principal, offset=offset)
        page = value["budget"]
        _require(value["schemaVersion"] == budget.ENVELOPE_SCHEMA
            and value["pageDigest"] == digest({key: item for key, item in value.items() if key != "pageDigest"})
            and page["pagination"]["offset"] == offset,
            "词货预算页绑定或摘要变化")
        pages.append(value)
        following = page["pagination"]["nextOffset"]
        if following is None:
            break
        _require(type(following) is int and following > offset,
            "词货预算分页未推进")
        offset = following
    total = pages[0]["budget"]["pagination"]["total"]
    _require(sum(len(value["budget"]["rows"]) for value in pages) == total,
        "词货预算分页未完整覆盖")
    return pages


def _append(frames, protocol, name, arguments, data, ordinal):
    call_id = str(ordinal).zfill(old.CALL_ID_CHARACTERS)
    call = {"id": call_id, "name": name, "arguments": arguments}
    if protocol == "anthropic":
        frame = {"role": "assistant", "content": [{"type": "tool_use", "id": call_id,
            "name": name, "input": arguments}]}
    else:
        frame = {"role": "assistant", "content": None, "tool_calls": [{"id": call_id,
            "type": "function", "function": {"name": name, "arguments": canonical(arguments)}}]}
    frames.append(frame)
    frames.extend(provider.tool_frames(SimpleNamespace(protocol=protocol), [call],
        [{"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}]))


def _measure(role_pages, budget_pages, reference, model, guidance, entries):
    _require(model.protocol in ("openai_compatible", "anthropic"), "词货模型协议不支持")
    cfg = model_capabilities.options(model)
    _require(type(model.max_tokens) is int and 0 < model.max_tokens <= 131072
        and type(model.max_total_tool_calls) is int and model.max_total_tool_calls > 0
        and type(model.max_tool_rounds) is int and model.max_tool_rounds > 0
        and type(cfg["contextWindowTokens"]) is int and 8192 <= cfg["contextWindowTokens"] <= 2000000,
        "词货模型上下文配置无效")
    calls_limit = min(model.max_total_tool_calls, old.MAX_TOOL_CALLS)
    rounds_limit = min(model.max_tool_rounds, old.MAX_TOOL_ROUNDS)
    graph = contract.graph(bool(budget_pages))
    nodes = []
    for node in graph["nodes"]:
        role, agent = node["key"], node["type"] == "agent"
        package_pages = role_pages[role] if agent else []
        required_budget = agent and bool(budget_pages) and role in old.BUDGET_NODES
        budget_for_role = budget_pages if required_budget else []
        calls = len(package_pages) + len(budget_for_role)
        failures = []
        def fail(code):
            if code not in failures:
                failures.append(code)
        if agent and (max(len(package_pages), len(budget_for_role)) > old.MAX_CALLS_PER_TOOL
                or calls > calls_limit or calls + 1 > rounds_limit
                or 2 * calls + 1 > old.MAX_TRANSCRIPT_FRAMES):
            fail("tool_limit_exceeded")
        protocols = {}
        input_bytes = 0
        for character in ('"', '<'):
            task_input = {"workflowInput": reference,
                "dependencies": {key: {"answer": character * old.OUTPUT_LIMITS[key]}
                    for key in node["dependsOn"]}}
            input_bytes = max(input_bytes, len(canonical(task_input).encode()))
            if input_bytes > old.MAX_NODE_INPUT_BYTES:
                fail("node_input_limit_exceeded")
            if not agent:
                continue
            for protocol in ("openai_compatible", "anthropic"):
                protocol_model = copy(model)
                protocol_model.protocol = protocol
                system = provider.system_prompt(protocol_model, SYSTEM + guidance)
                frames = [{"role": "user", "content": node["instruction"] + "\n<task_input>"
                    + canonical(task_input).replace("<", "\\u003c") + "</task_input>"}]
                ordinal = 0
                for page in package_pages:
                    ordinal += 1
                    _append(frames, protocol, contract.PACKAGE_TOOL,
                        {"runId": reference["evidenceRunId"], "reportId": reference["reportId"],
                         "screeningId": reference["screeningIntent"]["id"], "role": role,
                         "offset": page["pagination"]["offset"]}, page, ordinal)
                for page in budget_for_role:
                    ordinal += 1
                    _append(frames, protocol, contract.BUDGET_TOOL,
                        {"runId": reference["evidenceRunId"], "reportId": reference["reportId"],
                         "screeningId": reference["screeningIntent"]["id"],
                         "offset": page["budget"]["pagination"]["offset"]}, page, ordinal)
                size = len(canonical(frames).encode())
                estimated = model_capabilities.estimate_tokens({"system": system,
                    "messages": frames, "tools": entries})
                previous = protocols.get(protocol)
                protocols[protocol] = {"transcriptBytes": max(size,
                    previous["transcriptBytes"] if previous else 0),
                    "estimatedInputTokens": max(estimated,
                        previous["estimatedInputTokens"] if previous else 0)}
                if size > old.MAX_TRANSCRIPT_BYTES:
                    fail("transcript_limit_exceeded")
                try:
                    fitted, info = model_capabilities.fit_context(protocol_model, frames, system, entries)
                    if info["droppedMessages"] or canonical(fitted) != canonical(frames):
                        fail("context_would_drop_messages")
                except AiError as error:
                    if error.code != "ai_context_budget_exceeded":
                        raise
                    fail(error.code)
        transcript = max((item["transcriptBytes"] for item in protocols.values()), default=0)
        nodes.append({"nodeKey": role, "inputBytes": input_bytes, "protocols": protocols,
            "transcriptBytes": transcript, "packagePages": len(package_pages),
            "budgetPages": len(budget_for_role), "requiredToolCalls": calls,
            "requiredModelRounds": calls + 1 if agent else 0,
            "promotionPagesReserved": 0, "optionalAnalysisPagesReserved": 0,
            "fits": not failures, "failures": failures})
    return {"fits": all(node["fits"] for node in nodes), "nodes": nodes,
        "modelLimits": {"configuredProtocol": model.protocol,
            "contextWindowTokens": cfg["contextWindowTokens"],
            "maxOutputTokens": model.max_tokens, "maxToolCalls": calls_limit,
            "maxToolRounds": rounds_limit, "maxCallsPerTool": old.MAX_CALLS_PER_TOOL},
        "graphDigest": digest(graph), "catalogDigest": digest(entries)}


@dataclass(frozen=True, slots=True, init=False)
class PreparedCapacity:
    _report_id: str
    _owner_email: str
    _fixed_json: str
    _proof_json: str
    _digest: str

    def __init__(self, token, report_id, owner_email, fixed, proof):
        _require(token is _TOKEN, "容量候选只能从完整当前根建立", "invalid_request", 400)
        values = (report_id, owner_email, canonical(fixed), canonical(proof))
        for key, value in zip(("_report_id", "_owner_email", "_fixed_json", "_proof_json"), values):
            object.__setattr__(self, key, value)
        object.__setattr__(self, "_digest", digest(values))

    @property
    def proof(self):
        return _proof(self)


def _proof(value):
    _require(type(value) is PreparedCapacity, "公开对象不能恢复词货容量候选", "invalid_request", 400)
    try:
        _require(digest((value._report_id, value._owner_email, value._fixed_json, value._proof_json)) == value._digest
            and len(value._fixed_json.encode()) <= 32768 and len(value._proof_json.encode()) <= 32768)
        return json.loads(value._proof_json)
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("词货容量候选内部结构无效", "conflict", 409) from error


def revalidate(value, principal):
    proof = _proof(value)
    _require(not connection.in_atomic_block,
        "词货容量复验须在最外层事务之外", "invalid_request", 400)
    _require(principal.email.lower() == value._owner_email,
        "词货容量候选账号已变化", "access_denied", 403)
    current = _current(value._report_id, principal,
        allow_parked=proof["workflowStatus"] == "paused")
    _require(canonical(current[-1]) == value._fixed_json,
        "词货容量报告、模型或目录已变化")
    description = packages.describe(packages.prepare(
        proof["screeningReference"]["id"], principal), principal)
    _require(canonical(description["reference"]) == canonical(proof["screeningReference"])
        and canonical({role: description["roles"][role]["packageDigest"]
            for role in screening_package.ROLES}) == canonical(proof["packageDigests"]),
        "词货筛查发布或角色包已变化")
    return proof


def prepare(report_id, principal, *, allow_parked=False):
    """Measure complete mandatory pages; no scheduling, provider call or grant."""
    _require(not connection.in_atomic_block,
        "词货完整容量测算须在最外层事务之外", "invalid_request", 400)
    _require(type(allow_parked) is bool, "停靠容量模式无效", "invalid_request", 400)
    report, bound, reference, model, guidance, entries, fixed = _current(
        report_id, principal, allow_parked=allow_parked)
    roles, description = _role_pages(bound["screeningId"], principal,
        bound["screeningReference"])
    budgets = _budget_pages(report.id, principal, "budgetRef" in json.loads(report.snapshot_json))
    measured = _measure(roles, budgets, reference, model, guidance, entries)
    if not measured["fits"]:
        failures = [code for node in measured["nodes"] for code in node["failures"]]
        raise AiError("词货完整必读内容超过固定容量：" + ", ".join(dict.fromkeys(failures)),
            failures[0] if failures else "ai_context_budget_exceeded", 400)
    proof = {"schemaVersion": SCHEMA, **fixed,
        "screeningReference": description["reference"],
        "packageDigests": {role: description["roles"][role]["packageDigest"]
            for role in screening_package.ROLES},
        "budgetPages": len(budgets), "capacityVerified": True,
        "requiredContentOnly": True, "runtimeAdmissionGranted": False,
        "modelDispatched": False, "agentReadVerified": False,
        "measurements": measured["nodes"], "modelLimits": measured["modelLimits"],
        "limitations": ["词货及原生分析的可选调用未预留；实际每次调用仍须独立核验上下文、次数、轮次和响应字节。",
            "容量测算只使用UTF-8估算；不代表模型实际计费、Agent已读或派发授权。"]}
    candidate = PreparedCapacity(_TOKEN, report.id, principal.email.lower(), fixed, proof)
    revalidate(candidate, principal)
    return candidate
