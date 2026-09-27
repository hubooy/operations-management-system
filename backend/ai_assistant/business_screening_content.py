"""Internal screening content, independent job proofs and deterministic gaps.

Used by report detail, human review and file delivery. A successful result verifies
numeric references and durable reading, never causality or human approval.
"""
import json
from dataclasses import dataclass
from types import SimpleNamespace
from django.db import connection

from business_analysis import screening_package
from . import business_screening_tools as tools, business_screening_receipts as receipts
from . import business_screening_claims as claims, business_screening_diagnosis as diagnosis
from . import business_screening_runtime_contract as contract, models as m
from . import business_screening_runtime as runtime, business_screening_store as store
from .policy import AiError, canonical, digest, fields, current_principal

MAX_CONTENT_BYTES = 4*1024*1024
_REVIEW_TOKEN = object()


def _reject(message="筛查报告尚未具备完整独立分析结果"):
    raise AiError(message,"conflict",409)


def _nodes(report):
    values=list(m.AiWorkflowNodeRuns.objects.filter(run_id=report.workflow_id,
        node_key__in=contract.ROLES).order_by("node_key")[:6])
    if len(values)!=5 or {node.node_key for node in values}!=set(contract.ROLES): _reject()
    nodes={node.node_key:node for node in values}
    jobs={job.id:job for job in m.AiAgentJobs.objects.filter(workflow_run_id=report.workflow_id,
        id__in=[node.agent_job_id for node in values],owner_email=report.owner_email)}
    if len(jobs)!=5: _reject("五个角色必须各有独立实际任务")
    parsed,actual_jobs={},{}
    graph=contract.graph(bool(report.budget_plan_id))
    for role in contract.ROLES:
        node=nodes[role];job=jobs.get(node.agent_job_id)
        if (node.status!="completed" or job is None or job.status!="completed"
                or job.workflow_node_key!=role or job.scope_json!=report.scope_json
                or job.output_json!=node.output_json): _reject("专业节点或实际任务未完成，或结果不一致")
        try:
            raw=node.output_json
            if type(raw) is not str or len(raw.encode())>64*1024: _reject("专业节点输出包超限")
            outer=json.loads(raw,object_pairs_hook=diagnosis._unique)
            fields(outer,{"answer"},{"answer"})
            parsed[role]=diagnosis.validate_answer(role,outer["answer"])
            actual_jobs[role]=job
        except (ValueError,TypeError,UnicodeError,RecursionError) as error:
            raise AiError("专业节点输出结构无效","conflict",409) from error
    for definition in graph["nodes"][:5]:
        job=actual_jobs[definition["key"]]
        try:
            if len(job.input_json.encode())>contract.MAX_NODE_INPUT_BYTES: _reject("专业节点固定输入超限")
            node_input=json.loads(job.input_json,object_pairs_hook=diagnosis._unique)
            expected={role:json.loads(nodes[role].output_json) for role in definition["dependsOn"]}
            if canonical(node_input["dependencies"])!=canonical(expected): _reject("专业节点依赖不是本次实际完成结果")
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError) as error:
            raise AiError("专业节点依赖结构无效","conflict",409) from error
    marker=digest([[role,nodes[role].agent_job_id,nodes[role].output_json,actual_jobs[role].input_json]
        for role in contract.ROLES])
    return parsed,actual_jobs,marker


def _coverage(prepared, principal, proofs):
    tools._checked(prepared,principal)
    _,package,_=next(entry for entry in prepared.packages._packages if entry[0]=="report")
    decoded=tools._call(screening_package.decode_pages,package.pages())
    if decoded["role"]!="report": _reject()
    saved=json.loads(prepared._storage_reference_json)
    if decoded["binding"]["reportId"]!=prepared.report_id or saved["id"]!=prepared.reference["screeningIntent"]["id"]: _reject()
    # One copy of complete coverage; retained candidates remain in their
    # bounded role pages, rather than five duplicate copies in content JSON.
    value={"schemaVersion":"business-screening-content-v1","reference":saved,
        "packagePolicy":screening_package.POLICY,"packageDigests":dict(json.loads(prepared._package_digests_json)),
        "authority":decoded["authority"],"sources":decoded["sources"],"sourceInfos":decoded["sourceInfos"],
        "tableBindings":decoded["tableBindings"],"coverage":decoded["coverage"],"readProofs":proofs,
        "candidateDisclosure":{"kind":"retained_candidates","fullCandidatesIncluded":False,
            "candidateSource":"fixed_role_packages","countsSource":"coverage.partition",
            "notFullDetails":True,"crossPartitionAmountsAdditive":False},
        "limitations":["完整执行不等于所有请求均受支持；请求、表与分区缺口按完整覆盖记录展示",
            "来源日期完整不证明每个实体逐日有记录；不等天数未按日归一",
            "每分区保留 matchedRows、retainedRows、omittedRows；未保留候选不能当作已读明细",
            "零候选不证明没有问题；市场 TOP 是样本，跨渠道与跨规则不能无去重直接加总",
            "商品关联仅用当前主数据，不证明历史归属或推广增量收益"]}
    tools._checked(prepared,principal)
    return value


def content(row, principal):
    prepared=tools.prepare_for_report(row,principal,resolve_budget=True)
    actual,snapshot,_,_,_=tools._checked(prepared,principal)
    if actual.workflow.dry_run: _reject("空跑不生成诊断")
    parsed,jobs,marker=_nodes(actual)
    proofs={role:receipts.validate_complete(jobs[role],snapshot,principal,_prepared=prepared) for role in contract.ROLES}
    # A fresh real package build is shared only for numerical claim indexes;
    # the five independent durable ledgers above are never combined.
    roles=[role for role in (*contract.ROLES[:3],"report") if any("candidateId" in ref
        for finding in (parsed[role]["diagnosis"] if role=="report" else parsed[role])["findings"]
        for ref in finding["references"])]
    verified=claims.prepare_many(prepared.packages,roles,principal) if roles else {}
    analyses={role:diagnosis.validate(parsed[role]["diagnosis"] if role=="report" else parsed[role],actual,principal,
        role=role,prepared=prepared,claims_verified=verified.get(role)) for role in (*contract.ROLES[:3],"report")}
    result={"sections":parsed["report"]["sections"],"diagnosis":analyses["report"],
        "independentReview":parsed["independent_review"],
        "professionalAnalyses":{role:analyses[role] for role in contract.ROLES[:3]},
        "screening":_coverage(prepared,principal,proofs)}
    if prepared.budget is not None: result["budget"]=prepared.budget.result
    tools._checked(prepared,principal)
    if _nodes(actual)[2]!=marker: _reject("内容核验过程中实际专业结果已变化")
    raw=canonical(result)
    if len(raw.encode("utf-8"))>MAX_CONTENT_BYTES:
        raise AiError("完整筛查报告内容超过容量，不能裁剪缺口或引用","payload_too_large",413)
    tools._checked(prepared,principal)
    return json.loads(raw)


@dataclass(frozen=True,slots=True,init=False)
class PreparedReview:
    """Short-lived internal proof; not serializable authorization or a cache."""
    _report_id: str
    _snapshot_json: str
    _owner_email: str
    _scope_json: str
    _state_json: str
    _state_digest: str
    _content_digest: str

    def __init__(self, token, report, principal, state_json, content_digest):
        if token is not _REVIEW_TOKEN: _reject("人工复核准备对象只能由内部完整核验构造")
        for key,value in {"_report_id":report.id,"_snapshot_json":report.snapshot_json,
                "_owner_email":principal.email.lower(),"_scope_json":canonical(principal.scope),
                "_state_json":state_json,"_state_digest":digest(state_json),"_content_digest":content_digest}.items():
            object.__setattr__(self,key,value)


def _review_state(report, principal):
    """Metadata-only state fence; no fact pages, budget math or claim resolution."""
    current_principal(principal,admin=True,write=True)
    actual,snapshot,reference,_,_,_=runtime.bound(report,principal)
    flow=actual.workflow
    if (flow.dry_run or flow.status!="waiting_review" or flow.current_node_key!="human_review"
            or flow.cancel_requested): _reject("流程不在未取消的待人工复核状态")
    saved,_,_=store._loaded(snapshot["screeningIntent"]["id"],principal)
    if saved.report_id!=actual.id: _reject("人工复核固定筛查结果不匹配")
    human=m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id,node_key="human_review").first()
    expected=contract.graph(bool(actual.budget_plan_id))["nodes"][-1]
    if (human is None or human.node_type!="human_review" or human.status!="waiting_review"
            or human.agent_job_id is not None or human.instruction!=expected["instruction"]
            or human.depends_on_json!=canonical(expected["dependsOn"])): _reject("人工复核节点身份或状态无效")
    # Shape/actual output equality only. _nodes never reads a tool ledger,
    # evidence page or recalculates a reference; heavy proof remains outside.
    _,jobs,marker=_nodes(actual)
    nodes=list(m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id,node_key__in=contract.ROLES).order_by("node_key"))
    if len(nodes)!=5 or any(job.cancel_requested for job in jobs.values()): _reject("专业节点已取消或变化")
    flow_fields=("id","owner_email","scope_json","version","status","current_node_key","cancel_requested",
        "graph_json","graph_digest","input_json","model_id","model_version","tool_policy_digest","allowed_tools_json")
    node_fields=("id","node_key","position","version","status","node_type","instruction","depends_on_json",
        "input_json","output_json","agent_job_id")
    job_fields=("id","workflow_run_id","workflow_node_key","owner_email","scope_json","version","status",
        "cancel_requested","task","input_json","output_json","model_id","model_version","tool_policy_digest",
        "allowed_tools_json","provider_round_count","tool_call_count")
    from .business_screening_content_fence import fence
    value={"schemaVersion":"business-screening-review-state-v1","reportId":actual.id,"contentFence":fence(actual,principal),
        "snapshot":snapshot,"reference":reference,"screening":store._reference(saved),"outputsDigest":marker,
        "workflow":{k:getattr(flow,k) for k in flow_fields},
        "humanReview":{k:getattr(human,k) for k in node_fields},
        "nodes":[{k:getattr(node,k) for k in node_fields} for node in nodes],
        "jobs":[{k:getattr(jobs[role],k) for k in job_fields} for role in contract.ROLES]}
    raw=canonical(value)
    if len(raw.encode())>512*1024: _reject("人工复核固定状态超过容量")
    current_principal(principal,admin=True,write=True)
    return actual,raw


def prepare_review(row, principal):
    """Run before mutation: full five-job proof and fixed numeric verification."""
    if connection.in_atomic_block:
        _reject("人工复核完整准备须在最外层事务之外")
    actual,before=_review_state(row,principal)
    value=content(actual,principal)
    review=value["independentReview"]
    if review["approved"] is not True or review["conflicts"]:
        _reject("独立复核仍有未解决冲突，不能交付正式报告")
    actual,after=_review_state(actual,principal)
    if before!=after: _reject("完整核验期间流程、专业结果或人审节点已变化")
    return PreparedReview(_REVIEW_TOKEN,actual,principal,after,digest(value))


def revalidate_review(prepared, principal):
    """Call inside the caller's mutation, before its original node-version CAS."""
    if type(prepared) is not PreparedReview: _reject("不能从公开 JSON 恢复人工复核准备对象")
    current_principal(principal,admin=True,write=True)
    if (prepared._owner_email,prepared._scope_json)!=(principal.email.lower(),canonical(principal.scope)):
        raise AiError("人工复核准备身份已变化","access_denied",403)
    try:
        if (type(prepared._state_json) is not str or len(prepared._state_json.encode())>512*1024
                or digest(prepared._state_json)!=prepared._state_digest): _reject("人工复核准备状态已变化")
    except (ValueError,TypeError,UnicodeError) as error:
        raise AiError("人工复核准备编码无效","conflict",409) from error
    _,current=_review_state(SimpleNamespace(id=prepared._report_id,snapshot_json=prepared._snapshot_json),principal)
    if current!=prepared._state_json: _reject("准备后的流程、专业结果或人工复核节点已变化")
