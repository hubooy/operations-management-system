"""Owning, read-only capacity preparation. No scheduler or dispatch permission.

Network catalog retrieval and complete page preparation happen outside the
outermost transaction. A local result may later be checked in a short mutation;
that check reloads fixed roots and live identity, never trusts public JSON.
"""
from dataclasses import dataclass
import json
from types import SimpleNamespace

from django.db import connection

from business_analysis import screening_package
from business_analysis.contracts import AnalysisContractError
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_screening_tools as tools, business_screening_preflight as preflight
from . import business_screening_packages as packages, business_screening_store as store
from . import workflows, transport, model_capabilities
from .policy import AiError, canonical, digest

SCHEMA = "business-screening-capacity-admission-v1"
MAX_PROOF_BYTES = 32768
_TOKEN = object()


def _require(condition, message="筛查容量准入固定绑定已变化", code="conflict", status=409):
    if not condition:
        raise AiError(message, code, status)


def _copy(value, maximum=MAX_PROOF_BYTES):
    try:
        return screening_package._copy(value, maximum)
    except (AnalysisContractError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise AiError("筛查容量准入数据结构损坏或超过边界", "conflict", 409) from error


def _catalog(entries, flow):
    entries = _copy(entries, 128*1024)
    _require(type(entries) is list and len(entries) == 3, "筛查工具目录必须恰为三个固定工具")
    _require(all(type(e) is dict and type(e.get("name")) is str for e in entries)
        and {e["name"] for e in entries} == contract.TOOLS)
    identity = {"type":"string", "pattern":"^[A-Za-z0-9_-]{1,160}$"}
    sha = {"type":"string", "pattern":"^[a-f0-9]{64}$"}
    for entry in entries:
        _require(set(entry) == {"name","title","description","inputSchema","annotations","risk","allowedRoles","scopePolicy","execution"})
        _require(entry["risk"] == "read_only" and entry["allowedRoles"] == ["admin"] and entry["scopePolicy"] == "unscoped_only")
        _require(canonical(entry["annotations"]) == canonical({"readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False}))
        _require(all(type(entry[k]) is str and 0 < len(entry[k].encode()) <= 16000 for k in ("title","description")))
        props = {"runId":identity,"reportId":identity,"screeningId":identity}
        required = ["runId","reportId","screeningId"]
        maximum = 99
        if entry["name"] == contract.PACKAGE_TOOL:
            maximum = 9999
            props["role"] = {"type":"string","enum":list(screening_package.ROLES)}
            required += ["role"]
        elif entry["name"] == contract.TABLE_TOOL:
            maximum = 250000
            props.update(mode={"type":"string","enum":["native","mapped"]},
                dimension={"type":"string","enum":["shop","category","spu","sku","keyword","searchTerm","daily","brand"]},
                sourceKey=identity, baselineKey=identity, pairKey=sha, baselinePairKey=sha)
            required += ["mode","dimension"]
        props["offset"] = {"type":"integer","minimum":0,"maximum":maximum,"default":0}
        _require(canonical(entry["inputSchema"]) == canonical({"type":"object","properties":props,"required":required,"additionalProperties":False}),
            "筛查工具参数协议未知或已变化")
        _require(canonical(entry["execution"]) == canonical({"environment":"worker_inline","mode":"direct",
            "allowedSurfaces":[contract.SURFACE],"timeoutMs":12000,"maxResultCharacters":40000,"maxCallsPerRequest":8}))
    _require(flow.allowed_tools_json == canonical([e["name"] for e in entries])
        and flow.tool_policy_digest == digest(entries), "筛查工作流固定工具目录已变化", "tool_policy_changed")
    return entries


def _current(report, principal):
    actual, snapshot, reference, _, _, _ = runtime.bound(report, principal)
    flow = actual.workflow
    graph = workflows.validate_graph(contract.graph("budgetRef" in snapshot))
    _require(snapshot["executionProfile"] == contract.PROFILE and flow.dry_run == 0,
        "容量准入要求固定筛查正式工作流，不能使用演练或旧协议")
    _require(flow.graph_json == canonical(graph) and flow.graph_digest == digest(graph), "筛查工作流图已变化")
    _require(type(flow.model_id) is str and bool(flow.model_id), "筛查工作流没有固定模型")
    model = workflows.resolve_model(flow.model_id)
    _require(model.id == flow.model_id and type(model.version) is int and model.version == flow.model_version,
        "模型版本已变化", "model_version_changed")
    guidance = workflows.execution_guidance(flow.id)
    _require(type(guidance) is str and len(guidance.encode()) <= 32000, "执行指引格式或容量无效")
    # Only non-sensitive capability fields are materialized in the proof.
    capabilities = {"protocol":model.protocol,"modelType":model.model_type,"maxOutputTokens":model.max_tokens,
        "maxToolCalls":model.max_total_tool_calls,"maxToolRounds":model.max_tool_rounds,
        "generationOptionsDigest":digest(model_capabilities.options(model))}
    for key in ("maxOutputTokens","maxToolCalls","maxToolRounds"):
        _require(type(capabilities[key]) is int and capabilities[key] > 0, "模型能力配置无效")
    capabilities["contextWindowTokens"] = model_capabilities.options(model)["contextWindowTokens"]
    saved, _, _ = store._loaded(snapshot["screeningIntent"]["id"], principal)
    fixed = {"reportId":actual.id,"workflowId":flow.id,"ownerEmail":actual.owner_email,"scopeDigest":digest(principal.scope),
        "executionProfile":contract.PROFILE,"intent":snapshot["screeningIntent"],"snapshotDigest":digest(actual.snapshot_json),
        "inputDigest":digest(flow.input_json),"graphDigest":flow.graph_digest,"guidanceDigest":digest(guidance),
        "modelId":model.id,"modelVersion":model.version,"modelCapabilities":capabilities,
        "allowedToolsDigest":digest(flow.allowed_tools_json),"catalogDigest":flow.tool_policy_digest,
        "storageReference":store._reference(saved)}
    return actual, reference, model, guidance, _copy(fixed)


@dataclass(frozen=True, slots=True, init=False)
class PreparedAdmission:
    _snapshot_json: str
    _fixed_json: str
    _proof_json: str
    _digest: str

    def __init__(self, token, snapshot_json, fixed, proof):
        _require(token is _TOKEN, "容量准入对象只能由内部完整准备构造", "invalid_request", 400)
        proof = _copy(proof)
        for key, value in (("_snapshot_json",snapshot_json),("_fixed_json",canonical(fixed)),
                ("_proof_json",canonical(proof)),("_digest",digest([snapshot_json,fixed,proof]))):
            object.__setattr__(self,key,value)

    @property
    def proof(self):
        return _proof(self)


def _proof(prepared):
    _require(type(prepared) is PreparedAdmission, "不能从公开 JSON 恢复容量准入对象", "invalid_request", 400)
    try:
        _require(type(prepared._proof_json) is str and len(prepared._proof_json.encode()) <= MAX_PROOF_BYTES)
        _require(type(prepared._fixed_json) is str and len(prepared._fixed_json.encode()) <= MAX_PROOF_BYTES)
        _require(type(prepared._snapshot_json) is str and len(prepared._snapshot_json.encode()) <= 32768)
        fixed, value = _copy(json.loads(prepared._fixed_json)), _copy(json.loads(prepared._proof_json))
        _require(digest([prepared._snapshot_json,fixed,value]) == prepared._digest)
        return value
    except (ValueError, TypeError, AttributeError, RecursionError) as error:
        raise AiError("容量准入内部对象损坏", "conflict", 409) from error


def revalidate(prepared, principal):
    """Reload current roots/capabilities; no catalog network or fact pages.

    This is not a lifecycle/lease or Agent-read authorization. The scheduler
    must separately own its CAS and verify status before using a result.
    """
    proof = _proof(prepared)
    report = SimpleNamespace(id=proof["reportId"],snapshot_json=prepared._snapshot_json)
    _, _, _, _, fixed = _current(report, principal)
    _require(canonical(fixed) == prepared._fixed_json)
    return proof


def prepare(report, principal):
    """Full owning capacity check; never creates jobs or dispatches models."""
    _require(not connection.in_atomic_block, "筛查容量准备须在最外层数据库事务之外", "invalid_request", 400)
    actual, reference, model, guidance, fixed = _current(report, principal)
    try:
        entries = _catalog(transport.catalog(principal, contract.SURFACE), actual.workflow)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("筛查中央工具目录格式无效", "service_unavailable", 503) from error
    ready = tools.prepare_for_report(actual, principal, resolve_budget=True)
    role_pages, budgets = {}, None
    for role in screening_package.ROLES:
        pages, budget_pages = tools.expected_pages(ready, role, principal)
        role_pages[role] = list(pages.values())
        current_budgets = [page["budget"] for page in budget_pages.values()]
        if budgets is not None:
            _require(canonical(current_budgets) == canonical(budgets))
        budgets = current_budgets
    description = packages.describe(ready.packages, principal)
    package_digests = {role:description["roles"][role]["packageDigest"] for role in screening_package.ROLES}
    measured = preflight.measure(role_pages,{"schemaVersion":contract.REFERENCE_SCHEMA,
        "workflowInput":reference,"screeningReference":description["reference"],"packageDigests":package_digests},
        model=model,entries=entries,budget_pages=budgets,guidance=guidance)
    _require(measured["schemaVersion"] == contract.PREFLIGHT_SCHEMA and measured["actualInputAlreadyProposed"] is True
        and measured["sourceExecutionProfile"] == contract.PROFILE and canonical(measured["proposedWorkflowInput"]) == actual.workflow.input_json)
    if measured["fits"] is not True:
        failures = [code for node in measured["nodes"] for code in node["failures"]]
        raise AiError("筛查完整角色包超过固定容量："+", ".join(dict.fromkeys(failures)), failures[0] if failures else "ai_context_budget_exceeded", 400)
    proof = {"schemaVersion":SCHEMA,**fixed,"capacityVerified":True,"runtimeAdmissionGranted":False,
        "modelDispatched":False,"agentReadVerified":False,"packageDigests":package_digests,
        "preflightDigest":digest(measured),"measurements":measured["nodes"],"modelLimits":measured["modelLimits"]}
    prepared = PreparedAdmission(_TOKEN,actual.snapshot_json,fixed,proof)
    revalidate(prepared, principal)
    return prepared
