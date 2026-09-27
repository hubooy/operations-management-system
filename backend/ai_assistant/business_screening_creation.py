"""Internal atomic queued screening creation; not registered as a public API.

Budget facts and the signed catalog are prepared outside all transactions.
The short publication transaction checks immutable seal metadata and parameter
bindings. It never recalculates budget rows, scans screening or creates jobs.
"""
from dataclasses import dataclass
import json
from types import SimpleNamespace

from django.db import connection
from business_analysis import budget_reference as budget_contract
from . import business_budget_store as budgets, business_evidence as evidence_service
from . import business_evidence_store as evidence_store, business_screening_runtime as runtime
from . import business_screening_runtime_contract as contract, business_screening_admission as admission
from . import workflows, transport, report_library, models as m
from .policy import (AiError, _mutation_depth, authorize_owner, boolean, canonical, current_principal,
    digest, fields, identifier, mutation, passive, text, uid)

_TOKEN = object()
_TITLE = "全量规则筛查与多Agent经营分析"
_FIELDS = {"clientRequestId","evidenceRunId","question","dryRun","analysisMode","budgetPlan",
    "mappingPairs","previousReportId","expectedPrincipalKey"}


def _require(condition, message="新筛查报告准备绑定已变化", code="conflict", status=409):
    if not condition:
        raise AiError(message,code,status)


@dataclass(frozen=True, slots=True, init=False)
class _BudgetCapsule:
    """Issued only around an actual owning prepare; not an import format."""
    _id: str
    _plan_json: str
    _binding_json: str
    _result_json: str
    _digest: str

    def __init__(self, token, prepared):
        _require(token is _TOKEN and type(prepared) is budgets.PreparedBudget)
        values = (prepared.id,prepared.plan_json,prepared.binding_json,prepared.result_json)
        for key,value in zip(("_id","_plan_json","_binding_json","_result_json"),values):
            object.__setattr__(self,key,value)
        object.__setattr__(self,"_digest",digest(values))

    def budget(self):
        identifier(self._id)
        values=(self._id,self._plan_json,self._binding_json,self._result_json)
        for raw,maximum in zip(values[1:],(budget_contract.MAX_PLAN_BYTES,budget_contract.MAX_BINDING_BYTES,2*1024*1024)):
            _require(type(raw) is str and len(raw)<=maximum and len(raw.encode())<=maximum)
        _require(digest(values)==self._digest)
        return budgets.PreparedBudget(*values)


def _prepare_budget(evidence, raw_plan, principal, report_id):
    _require(not connection.in_atomic_block,"预算事实准备必须位于最外层事务之外")
    return _BudgetCapsule(_TOKEN,budgets.prepare(evidence,raw_plan,principal,report_id))


def _insert_budget(capsule, principal):
    _require(connection.in_atomic_block and _mutation_depth.get()>0,"预算参数只能在完整报告事务中保存")
    _require(type(capsule) is _BudgetCapsule,"不能用公开JSON或任意预算对象发布参数")
    current_principal(principal,admin=True,write=True)
    prepared=capsule.budget()
    plan=budgets._call(budget_contract.normalize_plan,prepared.plan)
    binding=budgets._call(budget_contract.validate_binding,prepared.binding)
    evidence=evidence_service.get_run(binding["evidenceRunId"],principal)
    _require(evidence.status=="sealed" and evidence_store.is_v2(evidence))
    evidence_store.verify_seal(evidence)
    header,seal=json.loads(evidence.plan_json),json.loads(evidence.state_json)
    expected=budgets._call(budget_contract.make_binding,plan,report_id=binding["reportId"],owner_email=principal.email.lower(),
        scope=principal.scope,evidence_run_id=evidence.id,evidence_version=evidence.version,
        evidence_plan_digest=digest(evidence.plan_json),catalog_digest=header["catalogDigest"],
        sealed_digest=seal["sealedDigest"],analysis_request=header.get("analysisRequest"))
    budgets._call(budget_contract.validate_record,plan,binding,prepared.reference,expected_binding=expected)
    _require(prepared.plan_json==canonical(plan) and prepared.binding_json==canonical(expected))
    evidence_store.assert_current(evidence)
    _require(not m.AiBusinessBudgetPlan.objects.filter(pk=prepared.id).exists(),"固定预算ID已存在，须通过原报告请求恢复")
    budgets._quota(principal.email.lower(),len(prepared.plan_json.encode())+len(prepared.binding_json.encode()))
    return m.AiBusinessBudgetPlan.objects.create(id=prepared.id,owner_email=principal.email.lower(),scope_json=canonical(principal.scope),
        evidence=evidence,evidence_version=evidence.version,plan_json=prepared.plan_json,plan_digest=binding["planDigest"],
        binding_json=prepared.binding_json,binding_digest=digest(prepared.binding_json))


def _replay(client, request_digest, principal):
    row=m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(),client_request_id=client).first()
    if row is None:return None
    authorize_owner(row,principal)
    _require(row.request_digest==request_digest,"请求标识已绑定其他报告")
    runtime.bound(row,principal)
    return {"item":{"id":row.id,"workflowId":row.workflow_id},"replayed":True}


def _limits(principal):
    active=m.AiWorkflowRuns.objects.filter(status__in=workflows.ACTIVE)
    _require(active.count()<24 and active.filter(owner_email=principal.email.lower()).count()<4,
        "活动任务达到上限","rate_limited",429)


def _model_digest(model):
    # Internal change detection only. No endpoint or model credential is sent
    # to a client, event or report snapshot.
    return digest({key:getattr(model,key) for key in ("id","version","status","protocol","model_type","model_name",
        "base_url","timeout_ms","max_tokens","reasoning_mode","temperature_milli","max_tool_rounds",
        "max_total_tool_calls","generation_options_json")})


def _previous(body, prepared, principal):
    if "previousReportId" not in body:return
    from . import reports, business_reports, business_integrated
    prior=reports.get(identifier(body["previousReportId"]),principal)
    snapshot=json.loads(prior.snapshot_json)
    _require(all(snapshot.get(key)==prepared.snapshot.get(key) for key in (*runtime.SEAL_KEYS,"question","scope")),
        "新报告版本须使用相同封存证据和分析范围")
    if runtime.is_snapshot(snapshot):runtime.bound(prior,principal)
    elif business_integrated.is_snapshot(snapshot):business_integrated.bound(prior,principal)
    else:business_reports.bound_reference(snapshot,principal)


def _metadata_revalidate(prepared, capsule, principal):
    _require(type(prepared) is runtime.Prepared)
    _require((prepared.owner_email,prepared.scope_json)==(principal.email.lower(),canonical(principal.scope)))
    snapshot=prepared.snapshot
    evidence=evidence_service.get_run(snapshot["evidenceRunId"],principal)
    mapping=snapshot.get("mappingPlan")
    choices=[{key:pair[key] for key in ("salesKey","masterKey")} for pair in mapping["pairs"]] if mapping is not None else None
    # runtime.prepare uses Reader metadata only. Its revalidate counterpart
    # intentionally is not called here, because it fully resolves budgets.
    fresh=runtime.prepare(evidence,principal,snapshot["reportId"],snapshot["question"],snapshot["screeningIntent"]["id"],
        choices=choices,budget=capsule.budget() if capsule is not None else None,previous_report_id=snapshot.get("previousReportId"))
    _require((fresh.snapshot_json,fresh.reference_json,fresh.selection_plan_json)==
        (prepared.snapshot_json,prepared.reference_json,prepared.selection_plan_json))
    return fresh


def create(body, principal, *, commit=None):
    """Atomically create a waiting workflow, not a capacity or dispatch permit."""
    _require(not connection.in_atomic_block,"筛查报告创建准备须在最外层事务之外","invalid_request",400)
    current_principal(principal,admin=True,write=True)
    fields(body,_FIELDS,{"clientRequestId","evidenceRunId","question","dryRun","analysisMode"})
    body=json.loads(canonical(passive(body,128*1024)))
    _require(body["analysisMode"]=="screening-v1","筛查创建模式无效","invalid_request",400)
    _require(not boolean(body["dryRun"],"dryRun"),"固定筛查报告不支持dryRun","invalid_request",400)
    if "mappingPairs" in body:
        _require(type(body["mappingPairs"]) is list,"显式商品关联必须为完整列表，不能用null降级","invalid_request",400)
    if "expectedPrincipalKey" in body:
        _require(body["expectedPrincipalKey"]==evidence_service.principal_key(principal),"当前账号与确认的请求不一致","access_denied",403)
    client,evidence_id=identifier(body["clientRequestId"]),identifier(body["evidenceRunId"])
    question=text(body["question"],"question",1000)
    request_digest=digest(body)
    old=_replay(client,request_digest,principal)
    if old is not None:
        if commit is None:return old
        with mutation(principal):
            return commit(_replay(client,request_digest,principal),200)
    _limits(principal)
    evidence=evidence_service.get_run(evidence_id,principal)
    _require(evidence.status=="sealed" and evidence_store.is_v2(evidence),"新筛查报告必须使用封存v2证据")
    model=workflows.resolve_model()
    model_digest=_model_digest(model)
    entries=admission._copy(transport.catalog(principal,contract.SURFACE),128*1024)
    _require(type(entries) is list and all(type(e) is dict and type(e.get("name")) is str for e in entries),"筛查目录无效")
    admitted={"model_id":model.id,"model_version":model.version,"allowed_tools_json":canonical([e["name"] for e in entries]),
        "tool_policy_digest":digest(entries)}
    entries=admission._catalog(entries,SimpleNamespace(**admitted))
    report_id,screening_id=uid("ai-report"),uid("screening")
    capsule=_prepare_budget(evidence,body["budgetPlan"],principal,report_id) if "budgetPlan" in body else None
    prepared=runtime.prepare(evidence,principal,report_id,question,screening_id,choices=body.get("mappingPairs"),
        budget=capsule.budget() if capsule else None,previous_report_id=body.get("previousReportId"))
    _previous(body,prepared,principal)
    graph=workflows.validate_graph(contract.graph(capsule is not None))
    flow_client="business-"+digest([principal.email.lower(),client])
    flow_body={"clientRequestId":flow_client,"name":_TITLE,"graph":graph,"input":prepared.reference,"dryRun":False}
    with mutation(principal):
        old=_replay(client,request_digest,principal)
        if old is not None:return commit(old,200) if commit is not None else old
        current_principal(principal,admin=True,write=True)
        _require(_model_digest(workflows.resolve_model(model.id))==model_digest,"模型配置在准备期间已变化","model_version_changed")
        _limits(principal)
        prepared=_metadata_revalidate(prepared,capsule,principal)
        _previous(body,prepared,principal)
        current_principal(principal,admin=True,write=True)
        parameters=_insert_budget(capsule,principal) if capsule else None
        _require(not m.AiWorkflowRuns.objects.filter(owner_email=principal.email.lower(),client_request_id=flow_client).exists(),
            "工作流请求标识已存在但没有原报告，禁止借用")
        flow=m.AiWorkflowRuns.objects.create(id=uid("ai-workflow"),owner_email=principal.email.lower(),scope_json=canonical(principal.scope),
            client_request_id=flow_client,request_digest=digest({"payload":flow_body,"admission":admitted,"executionProfile":contract.PROFILE}),
            name=_TITLE,graph_json=canonical(graph),graph_digest=digest(graph),input_json=prepared.reference_json,dry_run=0,**admitted)
        report_library.pin(flow.id,_TITLE,None,entries)
        for position,node in enumerate(graph["nodes"]):
            m.AiWorkflowNodeRuns.objects.create(id=uid("ai-node"),run=flow,node_key=node["key"],position=position,
                node_type=node["type"],depends_on_json=canonical(node["dependsOn"]),instruction=node["instruction"])
        row=m.AiReportRun.objects.create(id=report_id,owner_email=principal.email.lower(),scope_json=canonical(principal.scope),
            client_request_id=client,request_digest=request_digest,workflow=flow,snapshot_json=prepared.snapshot_json,budget_plan=parameters)
        current_principal(principal,admin=True,write=True)
        workflows.event(flow,principal,"created")
        result={"item":{"id":row.id,"workflowId":flow.id},"replayed":False}
        return commit(result,200) if commit is not None else result
