"""Pure capacity preview of complete role DTOs; no owner or model authority.

The caller must later reload the actual report/model/catalog and persisted
packages. Public hashes cannot authorize a job or prove facts. This module
does not query a database, dispatch a model, reserve a job or issue a receipt.
"""
from types import SimpleNamespace

from business_analysis import budget_reference, screening_package as package
from business_analysis.contracts import AnalysisContractError
from . import business_screening_runtime_contract as contract, provider
from .chat import SYSTEM
from .model_capabilities import fit_context, estimate_tokens, options
from .policy import AiError, canonical, digest

MAX_TOTAL_PAGE_BYTES = package.MAX_ALL_PACKAGE_BYTES
SEAL_KEYS = ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount")


def _require(condition, message="筛查容量预览输入无效"):
    if not condition:
        raise AiError(message, "invalid_request", 400)


def _integer(value, low=0, high=2**53-1):
    _require(type(value) is int and low <= value <= high)
    return value


def _copy(value, maximum):
    try:
        return package._copy(value, maximum)
    except (AnalysisContractError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise AiError("筛查容量预览结构超过边界或损坏", "invalid_request", 400) from error


def _sha(value):
    try:
        return budget_reference._sha(value)
    except AnalysisContractError as error:
        raise AiError("筛查容量预览摘要无效") from error


def _id(value):
    try:
        return budget_reference._id(value)
    except AnalysisContractError as error:
        raise AiError("筛查容量预览身份无效") from error


def _inputs(role_pages, reference):
    _require(type(role_pages) is dict and set(role_pages) == set(package.ROLES), "必须提供五个角色的完整页")
    pages, decoded, size = {}, {}, 0
    for role in package.ROLES:
        supplied = role_pages[role]
        _require(type(supplied) is list and 1 <= len(supplied) <= package.MAX_RECORDS)
        values = []
        for page in supplied:
            value = _copy(page, contract.MAX_TOOL_BYTES)
            size += len(canonical(value).encode("utf-8"))
            _require(size <= MAX_TOTAL_PAGE_BYTES, "五角色完整分页超过预览容量，不得裁剪")
            values.append(value)
        try:
            decoded[role] = package.decode_pages(values)
        except AnalysisContractError as error:
            raise AiError("角色包未完整解码或页绑定损坏", "conflict", 409) from error
        _require(decoded[role]["role"] == role)
        pages[role] = values
    first = decoded[package.ROLES[0]]
    for value in decoded.values():
        for key in ("binding", "authority", "sources", "sourceInfos", "coverage", "tableBindings"):
            _require(canonical(value[key]) == canonical(first[key]), "五角色不是同一完整筛查结果")
    ref = _copy(reference, 32000)
    _require(type(ref) is dict and set(ref) == {"schemaVersion", "workflowInput", "screeningReference", "packageDigests"}
        and ref["schemaVersion"] == contract.REFERENCE_SCHEMA)
    binding, authority = first["binding"], first["authority"]
    saved = ref["screeningReference"]
    _require(type(saved) is dict and set(saved) == {"schemaVersion", "id", "reportId", "bindingDigest", "selectionPlanDigest",
        "resultDigest", "contentRootDigest", "manifestDigest"} and saved["schemaVersion"] == "business-screening-storage-reference-v1")
    _id(saved["id"]); _id(saved["reportId"])
    for key in ("bindingDigest", "selectionPlanDigest", "resultDigest", "contentRootDigest", "manifestDigest"):
        _sha(saved[key])
    _require(saved["reportId"] == binding["reportId"] and saved["bindingDigest"] == digest(binding)
        and saved["selectionPlanDigest"] == authority["selectionPlanDigest"])
    _require(type(ref["packageDigests"]) is dict and set(ref["packageDigests"]) == set(package.ROLES))
    for role, values in pages.items():
        head = values[0]
        _require(head["packageDigest"] == _sha(ref["packageDigests"][role])
            and head["serviceResultDigest"] == saved["resultDigest"]
            and head["storageManifestDigest"] == saved["manifestDigest"]
            and head["bindingDigest"] == saved["bindingDigest"])
    workflow = ref["workflowInput"]
    required = {"inputMode", "question", *SEAL_KEYS}
    _require(type(workflow) is dict and required <= set(workflow)
        and not set(workflow)-required-{"reportId", "mappingRef", "budgetRef", "screeningIntent"}
        and workflow["inputMode"] == "reference-v2")
    _require(len(canonical(workflow).encode()) <= 8000 and digest(workflow) == binding["workflowInputDigest"],
        "来源工作流输入摘要不一致")
    _require(type(workflow["question"]) is str and 1 <= len(workflow["question"]) <= 1000
        and len(workflow["question"].encode()) <= 4000)
    _integer(workflow["evidenceVersion"], 1); _integer(workflow["sourceCount"], 1, 48)
    _require(canonical({k:workflow[k] for k in SEAL_KEYS}) == canonical({k:binding[k] for k in SEAL_KEYS}))
    if "reportId" in workflow: _require(workflow["reportId"] == binding["reportId"])
    mapping = workflow.get("mappingRef")
    if binding["mappingPlanDigest"] is None:
        _require("mappingRef" not in workflow)
    else:
        _require(type(mapping) is dict and set(mapping) == {"schemaVersion", "planDigest", "pairCount"}
            and mapping["schemaVersion"] == "business-mapping-reference-v1"
            and mapping["planDigest"] == binding["mappingPlanDigest"])
        _integer(mapping["pairCount"], 1, 47)
    _require(canonical(workflow.get("budgetRef")) == canonical(binding["budgetRef"]))
    if binding["budgetRef"] is None: _require("budgetRef" not in workflow)
    intent = contract.intent(saved["id"],saved["selectionPlanDigest"])
    _require(intent["selectionPolicy"] == authority["selectionPolicy"] and intent["algorithmVersion"] == binding["algorithmVersion"])
    if "screeningIntent" in workflow:
        _require(canonical(workflow["screeningIntent"]) == canonical(intent), "已有筛查意图与持久结果不一致")
    proposed = {**workflow, "reportId":binding["reportId"], "screeningIntent":intent}
    return pages, ref, binding, proposed


def _budgets(supplied, workflow, binding):
    _require(type(supplied) is list and len(supplied) <= 100)
    budget_ref = workflow.get("budgetRef")
    if budget_ref is None:
        _require(not supplied, "没有固定预算时不能提供预算页")
        return []
    _require(type(budget_ref) is dict and set(budget_ref) == {"schemaVersion", "id", "planDigest", "bindingDigest"}
        and budget_ref["schemaVersion"] == budget_reference.REFERENCE_SCHEMA and bool(supplied), "固定预算缺少完整页")
    _id(budget_ref["id"]); _sha(budget_ref["planDigest"]); _sha(budget_ref["bindingDigest"])
    pages, offset, common = [], 0, None
    for supplied_page in supplied:
        _require(offset is not None, "预算末页后不得追加数据")
        page = _copy(supplied_page, contract.MAX_TOOL_BYTES)
        _require(set(page) == {"schemaVersion", "reportId", "budgetRef", "binding", "allocation", "scenarios", "rows", "pagination", "limitations", "pageDigest"})
        _require(page["schemaVersion"] == budget_reference.PAGE_SCHEMA and page["reportId"] == binding["reportId"]
            and canonical(page["budgetRef"]) == canonical(budget_ref)
            and page["pageDigest"] == digest({k:v for k,v in page.items() if k != "pageDigest"}))
        try:
            budget_binding = budget_reference.validate_binding(page["binding"])
        except AnalysisContractError as error:
            raise AiError("预算绑定损坏") from error
        _require(budget_ref["bindingDigest"] == digest(budget_binding) and budget_ref["planDigest"] == budget_binding["planDigest"]
            and budget_binding["reportId"] == binding["reportId"] and budget_binding["ownerEmail"] == binding["ownerEmail"]
            and budget_binding["scopeDigest"] == digest(binding["scope"])
            and budget_binding["analysisRequestDigest"] == binding["analysisRequestDigest"]
            and all(budget_binding[k] == binding[k] for k in SEAL_KEYS if k != "sourceCount"))
        fixed = {k:v for k,v in page.items() if k not in {"rows", "pagination", "pageDigest"}}
        if common is not None: _require(canonical(common) == canonical(fixed), "预算跨页参数或情景变化")
        common = fixed
        pagination, rows = page["pagination"], page["rows"]
        _require(type(pagination) is dict and set(pagination) == {"offset", "limit", "total", "returned", "nextOffset"}
            and type(rows) is list and bool(rows))
        total = _integer(pagination["total"], 1, 100)
        _integer(pagination["offset"],0,99); _integer(pagination["limit"],1,20); _integer(pagination["returned"],1,20)
        _require(pagination["offset"] == offset and pagination["returned"] == len(rows) <= pagination["limit"])
        _integer(page["allocation"]["targetCount"],1,100)
        _require(page["allocation"]["targetCount"] == total)
        for index, row in enumerate(rows):
            _require(type(row) is dict and _integer(row.get("rowIndex"),0,99) == offset+index)
        end = offset+len(rows)
        if pagination["nextOffset"] is not None: _integer(pagination["nextOffset"],1,99)
        _require(end <= total and pagination["nextOffset"] == (end if end < total else None))
        offset = pagination["nextOffset"]
        pages.append(page)
    _require(offset is None, "预算尚未读完")
    return pages


def _append_tool(frames, model, name, arguments, data, ordinal):
    call_id = str(ordinal).zfill(contract.CALL_ID_CHARACTERS)
    call = {"id":call_id, "name":name, "arguments":arguments}
    if model.protocol == "anthropic":
        frame = {"role":"assistant", "content":[{"type":"tool_use", "id":call_id, "name":name, "input":arguments}]}
    else:
        frame = {"role":"assistant", "content":None, "tool_calls":[{"id":call_id, "type":"function",
            "function":{"name":name, "arguments":canonical(arguments)}}]}
    frames.append(frame)
    frames.extend(provider.tool_frames(model,[call],[{"toolName":name,"ok":True,"auditStatus":"recorded","data":data}]))


def _measure(role_pages, reference, *, model, entries, budget_pages, guidance=""):
    """Return complete prospective measurements; malformed inputs raise AiError.

    Capacity failures are retained per node with fits=False. No candidates,
    frames or dependency bytes are dropped to force a successful preview.
    """
    pages, ref, binding, proposed = _inputs(role_pages,reference)
    budgets = _budgets(budget_pages,ref["workflowInput"],binding)
    _require(type(guidance) is str and len(guidance.encode()) <= 32000)
    catalog = _copy(entries,128*1024)
    _require(type(catalog) is list and len(catalog) == 3 and all(type(e) is dict and type(e.get("name")) is str for e in catalog)
        and {e["name"] for e in catalog} == contract.TOOLS)
    for entry in catalog:
        execution = entry.get("execution",{})
        _require(type(execution) is dict and type(execution.get("maxCallsPerRequest")) is int
            and execution["maxCallsPerRequest"] == contract.MAX_CALLS_PER_TOOL
            and type(execution.get("maxResultCharacters")) is int
            and execution["maxResultCharacters"] == contract.MAX_RESULT_CHARACTERS)
    _require(model is not None and getattr(model,"protocol",None) in ("openai_compatible","anthropic"))
    calls_limit = min(_integer(getattr(model,"max_total_tool_calls",None),1),contract.MAX_TOOL_CALLS)
    rounds_limit = min(_integer(getattr(model,"max_tool_rounds",None),1),contract.MAX_TOOL_ROUNDS)
    _integer(getattr(model,"max_tokens",None),1,131072)
    _require(type(getattr(model,"generation_options_json","{}")) is str
        and len(getattr(model,"generation_options_json","{}").encode()) <= 32000)
    cfg = options(model); _integer(cfg["contextWindowTokens"],8192,2000000)
    system = provider.system_prompt(model,SYSTEM+guidance)
    graph = contract.graph(bool(budgets))
    nodes = []
    for node in graph["nodes"]:
        role = node["key"]; agent = node["type"] == "agent"
        required_budget = bool(budgets) and role in contract.BUDGET_NODES
        package_count = len(pages[role]) if agent else 0
        budget_count = len(budgets) if required_budget else 0
        calls = package_count+budget_count
        failures = []
        def fail(code):
            if code not in failures: failures.append(code)
        if agent and (max(package_count,budget_count) > contract.MAX_CALLS_PER_TOOL or calls > calls_limit
                or calls+1 > rounds_limit or 2*calls+1 > contract.MAX_TRANSCRIPT_FRAMES):
            fail("tool_limit_exceeded")
        measured = {protocol:{"transcriptBytes":0,"estimatedInputTokens":0} for protocol in ("openai_compatible","anthropic")}
        input_bytes = 0
        for character in ('"','<'):
            data = {"workflowInput":proposed,"dependencies":{key:{"answer":character*contract.OUTPUT_LIMITS[key]} for key in node["dependsOn"]}}
            input_bytes = max(input_bytes,len(canonical(data).encode()))
            if input_bytes > contract.MAX_NODE_INPUT_BYTES: fail("node_input_limit_exceeded")
            if not agent: continue
            for protocol, totals in measured.items():
                transport = SimpleNamespace(protocol=protocol)
                frames = [{"role":"user","content":node["instruction"]+"\n<task_input>"+canonical(data).replace("<","\\u003c")+"</task_input>"}]
                ordinal = 0
                for page in pages[role]:
                    ordinal += 1
                    _append_tool(frames,transport,contract.PACKAGE_TOOL,{"runId":binding["evidenceRunId"],"reportId":binding["reportId"],
                        "screeningId":ref["screeningReference"]["id"],"role":role,"offset":page["pagination"]["offset"]},page,ordinal)
                if required_budget:
                    for page in budgets:
                        ordinal += 1
                        wrapper = {"schemaVersion":contract.BUDGET_PAGE_SCHEMA,"reference":proposed,"budget":page}
                        wrapper["pageDigest"] = digest(wrapper)
                        if len(canonical(wrapper).encode()) > contract.MAX_TOOL_BYTES: fail("budget_envelope_limit_exceeded")
                        _append_tool(frames,transport,contract.BUDGET_TOOL,{"runId":binding["evidenceRunId"],"reportId":binding["reportId"],
                            "screeningId":ref["screeningReference"]["id"],"offset":page["pagination"]["offset"]},wrapper,ordinal)
                size = len(canonical(frames).encode())
                totals["transcriptBytes"] = max(totals["transcriptBytes"],size)
                totals["estimatedInputTokens"] = max(totals["estimatedInputTokens"],estimate_tokens({"system":system,"messages":frames,"tools":catalog}))
                if size > contract.MAX_TRANSCRIPT_BYTES: fail("transcript_limit_exceeded")
                try:
                    fitted, info = fit_context(model,frames,system,catalog)
                    if info["droppedMessages"] or canonical(fitted) != canonical(frames): fail("context_would_drop_messages")
                except AiError as error:
                    if error.code != "ai_context_budget_exceeded": raise
                    fail(error.code)
        transcript = max(item["transcriptBytes"] for item in measured.values())
        nodes.append({"nodeKey":role,"inputBytes":input_bytes,"protocols":measured,"transcriptBytes":transcript,
            "packagePages":package_count,"budgetPages":budget_count,"requiredToolCalls":calls,
            "requiredModelRounds":calls+1 if agent else 0,"analysisPagesRequired":0,"optionalAnalysisReserved":False,
            "remainingTranscriptBytes":max(0,contract.MAX_TRANSCRIPT_BYTES-transcript) if agent else None,
            "remainingToolCalls":max(0,calls_limit-calls) if agent else None,
            "remainingToolRounds":max(0,rounds_limit-calls-1) if agent else None,"fits":not failures,"failures":failures})
    return {"schemaVersion":contract.PREFLIGHT_SCHEMA,"executionProfile":contract.PROFILE,"surface":contract.SURFACE,
        "previewOnly":True,"runtimeAdmissionGranted":False,"modelDispatched":False,"fits":all(n["fits"] for n in nodes),
        "sourceInputDigest":digest(ref["workflowInput"]),"proposedInputDigest":digest(proposed),
        "proposedWorkflowInput":proposed,"sourceExecutionProfile":binding["executionProfile"],
        "actualInputAlreadyProposed":canonical(ref["workflowInput"]) == canonical(proposed),
        "modelLimits":{"configuredProtocol":model.protocol,"contextWindowTokens":cfg["contextWindowTokens"],
            "maxOutputTokens":model.max_tokens,"maxToolCalls":calls_limit,"maxToolRounds":rounds_limit,
            "maxCallsPerTool":contract.MAX_CALLS_PER_TOOL},
        "screeningReference":ref["screeningReference"],"packageDigests":ref["packageDigests"],
        "graphDigest":digest(graph),"catalogDigest":digest(catalog),"nodes":nodes,
        "limitations":["公开页和摘要不能证明来源授权、预算算术或实际Agent已读；正式运行须复验真实报告和持久账本。",
            "未预留可选明细调用，后续每次调用仍受实时调用、轮次、输出、上下文及字节硬限。",
            "Token为UTF8估算，不是模型实际计费；通过预览不能派发模型。"]}


def measure(role_pages, reference, *, model, entries, budget_pages, guidance=""):
    """Measure complete caller DTOs; this is never an owning admission API."""
    try:
        return _measure(role_pages,reference,model=model,entries=entries,budget_pages=budget_pages,guidance=guidance)
    except (AnalysisContractError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("筛查容量预览协议或结构无效", "invalid_request", 400) from error
