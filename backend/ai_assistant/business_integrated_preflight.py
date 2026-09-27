"""Measure every integrated node before admission, with no model dispatch."""
from types import SimpleNamespace

from . import business_integrated as contract, business_integrated_tools as tools, business_evidence, business_evidence_store as evidence_store
from . import models as m, provider, workflows
from .model_capabilities import fit_context
from .policy import AiError, authorize_owner, canonical, current_principal, digest, passive

DIRECTORY_TOOL, TABLE_TOOL, BUDGET_TOOL, TOOLS = contract.DIRECTORY_TOOL, contract.TABLE_TOOL, contract.BUDGET_TOOL, contract.TOOLS
ANALYSIS_RESPONSE_BYTES = 38000
ANALYSIS_TRANSCRIPT_RESERVE = 2*ANALYSIS_RESPONSE_BYTES+4096


def _trusted(flow, principal, graph, prepared):
    current_principal(principal, admin=True)
    authorize_owner(flow, principal)
    prepared = contract.revalidate(prepared, principal)
    if (flow.owner_email, flow.scope_json) != (prepared.owner_email, prepared.scope_json):
        raise AiError("集成报告预检创建身份不一致", "access_denied", 403)
    if prepared.budget is not None:
        budget = prepared.budget
        saved = m.AiBusinessBudgetPlan.objects.filter(pk=budget.id).first()
        binding = budget.binding
        if (saved is None or saved.owner_email != flow.owner_email or saved.scope_json != flow.scope_json
                or saved.plan_json != budget.plan_json or saved.binding_json != budget.binding_json
                or saved.plan_digest != binding["planDigest"] or saved.binding_digest != digest(budget.binding_json)
                or saved.evidence_id != binding["evidenceRunId"] or saved.evidence_version != binding["evidenceVersion"]):
            raise AiError("集成预检须绑定同事务的完整固定预算参数", "conflict", 409)
    reference = prepared.reference
    passive(reference, 8000)
    if (flow.input_json != canonical(reference)
            or canonical(graph) != canonical(workflows.validate_graph(contract.graph(prepared.budget is not None)))):
        raise AiError("集成预检输入或固定步骤不一致", "conflict", 409)
    evidence = business_evidence.get_run(prepared.snapshot["evidenceRunId"], principal)
    sources = evidence_store.catalog(evidence)
    directories, budgets = tools.expected_pages(prepared, evidence, sources)
    evidence_store.assert_current(evidence)
    return reference, sources, directories, budgets


def _append_tool(frames, model, name, args, data, ordinal):
    call_id = str(ordinal).zfill(160)
    call = {"id": call_id, "name": name, "arguments": args}
    if model.protocol == "anthropic":
        frame = {"role": "assistant", "content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}]}
    else:
        frame = {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function",
            "function": {"name": name, "arguments": canonical(args)}}]}
    frames.append(frame)
    frames.extend(provider.tool_frames(model, [call], [{"toolName": name, "ok": True, "auditStatus": "recorded", "data": data}]))


def preflight(flow, principal, graph, entries, *, prepared):
    """Measure full required pages and worst dependency escaping at every node.

Dependency strings are deliberately conservative byte reservations, not model
answers (runtime accepts JSON objects only, so literal control bytes inside
strings are invalid and allowed raw JSON whitespace escapes at most 2x). Quote and '<' probes cover respectively node JSON's 2x escaping and
the runtime's HTML-safe '<' substitution followed by outer frame encoding.
Additional unplanned model prose/tool calls remain subject to live budgets.
"""
    reference, sources, directories, budgets = _trusted(flow, principal, graph, prepared)
    model = workflows.resolve_model(flow.model_id) if not flow.dry_run else None
    if model and (len(entries) != len(TOOLS) or {e["name"] for e in entries} != TOOLS):
        raise AiError("集成分析执行工具目录不完整", "service_unavailable", 503)
    tools = {entry["name"]: entry for entry in entries}
    protocol = model or SimpleNamespace(protocol="openai_compatible")
    limits = contract.OUTPUT_LIMITS
    summary = []
    for node in graph["nodes"]:
        required_budget = bool(budgets) and node["key"] in contract.BUDGET_NODES
        if node["type"] == "agent" and model:
            tool_counts = {DIRECTORY_TOOL: len(directories), BUDGET_TOOL: len(budgets) if required_budget else 0, TABLE_TOOL: 1}
            calls = sum(tool_counts.values())
            if (any(count > tools[name]["execution"]["maxCallsPerRequest"] for name,count in tool_counts.items())
                    or calls > min(model.max_total_tool_calls, 40) or calls+1 > min(model.max_tool_rounds, 20)
                    or 2*calls+1 > 64):
                raise AiError("模型额度不足以读完完整目录、固定预算并分析", "tool_limit_exceeded", 409)
        input_bytes = transcript_bytes = estimated_tokens = 0
        for character in ('"', '<'):
            data = {"workflowInput": reference, "dependencies": {key: {"answer": character*limits[key]} for key in node["dependsOn"]}}
            passive(data, 24*1024)  # also applies to the human review node
            input_bytes = max(input_bytes, len(canonical(data).encode()))
            if node["type"] != "agent":
                continue
            frames = [{"role":"user","content":node["instruction"]+"\n<task_input>"+canonical(data).replace("<","\\u003c")+"</task_input>"}]
            ordinal = 0
            for page in directories.values():
                ordinal += 1
                _append_tool(frames, protocol, DIRECTORY_TOOL, {"runId":reference["evidenceRunId"],"reportId":reference["reportId"],"offset":page["offset"]}, page, ordinal)
            if required_budget:
                for page in budgets.values():
                    ordinal += 1
                    _append_tool(frames, protocol, BUDGET_TOOL, {"runId":reference["evidenceRunId"],"reportId":reference["reportId"],
                        "offset":page["budget"]["pagination"]["offset"]}, page, ordinal)
            _append_tool(frames, protocol, TABLE_TOOL, {"runId":reference["evidenceRunId"],"reportId":reference["reportId"],"mode":"mapped",
                    "pairKey":prepared.plan["pairs"][0]["pairKey"],"dimension":"sku","offset":250000},
                {"preflightTranscriptByteReservation":"x"*ANALYSIS_TRANSCRIPT_RESERVE}, ordinal+1)
            size = len(canonical(frames).encode())
            transcript_bytes = max(transcript_bytes,size)
            if size > 192*1024:
                raise AiError("完整集成分析读取及分析上下文超过192KiB，不得截断", "transcript_limit_exceeded", 409)
            if model:
                _, info = fit_context(model,frames,provider.system_prompt(model,workflows.SYSTEM+workflows.execution_guidance(flow.id)),entries)
                if info["droppedMessages"]:
                    raise AiError("集成分析上下文不能通过删除已读页面压缩", "ai_context_budget_exceeded", 409)
                estimated_tokens = max(estimated_tokens,info["estimatedInputTokens"])
        summary.append({"nodeKey":node["key"],"inputBytes":input_bytes,"transcriptBytes":transcript_bytes,
            "estimatedInputTokens":estimated_tokens,"directoryPages":len(directories) if node["type"] == "agent" else 0,
            "budgetPages":len(budgets) if required_budget else 0,
            "mappedRequired":node["key"] in contract.MAPPED_NODES})
    return {"executionProfile":contract.PROFILE,"mappingRef":reference["mappingRef"],
        "budgetRef":reference.get("budgetRef"),"nodes":summary}
