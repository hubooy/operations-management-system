"""Fixed preparation and binding for the pending screening report profile.

This module alone does not register creation, dispatch, tools or delivery.
Published screening is an immutable child of the preallocated report intent;
neither snapshot nor workflow input is amended after fact preparation.
"""
from dataclasses import dataclass
import json

from business_analysis import mapping_plan, screening_plan
from business_analysis.contracts import AnalysisContractError
from . import business_evidence, business_evidence_store, business_budget_store, models as m, business_screening_runtime_contract as contract
from .business_sealed import Reader
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier, passive

PROFILE = contract.PROFILE
SEAL_KEYS = ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount")
INTENT_FIELDS = contract.INTENT_FIELDS
SNAPSHOT_REQUIRED = {"schemaVersion","executionMode","executionProfile","evidenceProtocol","reportId",
    *SEAL_KEYS,"question","screeningIntent","scope","libraryVersion","pipeline","template","skills"}
SNAPSHOT_OPTIONAL = {"mappingPlan","mappingPlanDigest","budgetRef","previousReportId"}
_TOKEN = object()


def _conflict(message="筛查报告固定范围或准备意图不一致"):
    raise AiError(message, "conflict", 409)


def _intent(screening_id, plan):
    return contract.intent(screening_id,plan["planDigest"])


def is_snapshot(snapshot):
    if snapshot.get("executionProfile") != PROFILE:
        return False
    intent = snapshot.get("screeningIntent")
    if (set(snapshot)-SNAPSHOT_REQUIRED-SNAPSHOT_OPTIONAL or SNAPSHOT_REQUIRED-set(snapshot)
            or snapshot.get("schemaVersion") != "business-report-v1" or snapshot.get("executionMode") != "parallel-v1"
            or snapshot.get("evidenceProtocol") != "reference-v2" or not snapshot.get("reportId")
            or type(intent) is not dict or set(intent) != INTENT_FIELDS
            or "budgetPlan" in snapshot or "budgetPlanDigest" in snapshot
            or ("mappingPlan" in snapshot) != ("mappingPlanDigest" in snapshot)):
        _conflict()
    identifier(snapshot["reportId"])
    if canonical(intent)!=canonical(contract.intent(intent["id"],intent["selectionPlanDigest"])):
        _conflict()
    if "mappingPlan" in snapshot and type(snapshot["mappingPlan"]) is not dict: _conflict()
    if "budgetRef" in snapshot:
        ref = snapshot["budgetRef"]
        if (type(ref) is not dict or set(ref)!={"schemaVersion","id","planDigest","bindingDigest"}
                or ref["schemaVersion"]!="business-budget-reference-v1"):
            _conflict()
    return True


@dataclass(frozen=True, slots=True, init=False)
class Prepared:
    owner_email: str
    scope_json: str
    snapshot_json: str
    reference_json: str
    selection_plan_json: str
    budget: object

    def __init__(self, token, owner, scope, snapshot, reference, plan, budget):
        if token is not _TOKEN:
            raise AiError("筛查报告须由内部完整范围准备")
        for name,value in (("owner_email",owner),("scope_json",canonical(scope)),
                ("snapshot_json",canonical(passive(snapshot,32768))),
                ("reference_json",canonical(passive(reference,8000))),
                ("selection_plan_json",canonical(plan)),("budget",budget)):
            object.__setattr__(self,name,value)

    @property
    def snapshot(self): return json.loads(self.snapshot_json)

    @property
    def reference(self): return json.loads(self.reference_json)

    @property
    def selection_plan(self): return json.loads(self.selection_plan_json)


def _metadata(evidence, principal, choices):
    reader = Reader(evidence,principal)
    sources = reader.sources
    infos = {source["key"]:reader.info(source["key"]) for source in sources}
    request = json.loads(evidence.plan_json).get("analysisRequest")
    if request is None:
        _conflict("新筛查报告需要证据中固定的分析维度和比较窗口")
    try:
        mapping = mapping_plan.build(sources,choices)["plan"] if choices is not None else None
        selected = screening_plan.build(request,sources,infos,mapping_plan=mapping)
    except (AnalysisContractError,ValueError,TypeError,KeyError) as error:
        raise AiError("完整筛查计划未通过来源核验", "conflict", 409) from error
    if not selected["canScreen"]:
        raise AiError("完整筛查计划超过固定能力或容量，不能减少范围后继续", "payload_too_large", 413)
    return sources,mapping,selected


def prepare(evidence, principal, report_id, question, screening_id, *, choices=None, budget=None, previous_report_id=None):
    from . import business_reports, business_integrated
    current_principal(principal,admin=True)
    authorize_owner(evidence,principal)
    identifier(report_id)
    reference,_ = business_reports._reference(evidence,question)
    sources,mapping,selected = _metadata(evidence,principal,choices)
    queries = [source["query"] for source in sources]
    platforms = {query["platform"] for query in queries}
    shops = {query["shop"] for query in queries if query.get("shop")}
    scope = {"platform":next(iter(platforms)) if len(platforms)==1 else "多平台",
        "shop":next(iter(shops)) if len(shops)==1 else "多店铺" if shops else "市场样本",
        "startDate":queries[0]["startDate"],"endDate":queries[0]["endDate"]}
    intent = _intent(screening_id,selected)
    snapshot = {"schemaVersion":"business-report-v1","executionMode":"parallel-v1","executionProfile":PROFILE,
        "evidenceProtocol":"reference-v2",**{k:v for k,v in reference.items() if k!="inputMode"},
        "reportId":report_id,"screeningIntent":intent,"scope":scope,"libraryVersion":0,
        "pipeline":{"name":"全量规则筛查与多Agent经营分析"},
        "template":{"name":"多Agent经营诊断","format":"html","sections":business_reports.SECTIONS},"skills":[]}
    reference.update(reportId=report_id,screeningIntent=intent)
    if mapping is not None:
        snapshot.update(mappingPlan=mapping,mappingPlanDigest=digest(mapping))
        reference["mappingRef"] = business_integrated.mapping_reference(mapping)
    if budget is not None:
        if (type(budget) is not business_budget_store.PreparedBudget or budget.binding["reportId"]!=report_id
                or any(budget.binding[key]!=snapshot[key] for key in SEAL_KEYS if key!="sourceCount")):
            _conflict("固定预算不属于本次筛查报告")
        snapshot["budgetRef"] = reference["budgetRef"] = budget.reference
    if previous_report_id is not None:
        snapshot["previousReportId"] = identifier(previous_report_id)
    business_evidence_store.assert_current(evidence)
    return Prepared(_TOKEN,principal.email.lower(),principal.scope,snapshot,reference,selected,budget)


def revalidate(prepared, principal):
    if type(prepared) is not Prepared:
        _conflict("筛查报告准备对象无效")
    current_principal(principal,admin=True)
    if (prepared.owner_email,prepared.scope_json)!=(principal.email.lower(),canonical(principal.scope)):
        raise AiError("筛查报告准备身份已变化", "access_denied", 403)
    snapshot = prepared.snapshot
    if not is_snapshot(snapshot): _conflict()
    evidence = business_evidence.get_run(snapshot["evidenceRunId"],principal)
    mapping = snapshot.get("mappingPlan")
    choices = [{key:pair[key] for key in ("salesKey","masterKey")} for pair in mapping["pairs"]] if mapping is not None else None
    budget = business_budget_store.revalidate(prepared.budget,principal) if prepared.budget is not None else None
    fresh = prepare(evidence,principal,snapshot["reportId"],snapshot["question"],snapshot["screeningIntent"]["id"],
        choices=choices,budget=budget,previous_report_id=snapshot.get("previousReportId"))
    if fresh != prepared: _conflict()
    return fresh


def bound(report, principal, *, check_budget=True):
    """Rebuild immutable input and intent from current sealed metadata only."""
    from . import business_reports, business_integrated
    current_principal(principal,admin=True)
    actual = m.AiReportRun.objects.select_related("workflow").filter(pk=identifier(report.id)).first()
    if actual is None: raise AiError("筛查报告不存在", "not_found", 404)
    authorize_owner(actual,principal); authorize_owner(actual.workflow,principal)
    try:
        snapshot = json.loads(actual.snapshot_json)
        if (not is_snapshot(snapshot) or snapshot["reportId"]!=actual.id or actual.snapshot_json!=canonical(snapshot)
                or report.snapshot_json!=actual.snapshot_json
                or (actual.owner_email,actual.scope_json)!=(actual.workflow.owner_email,actual.workflow.scope_json)):
            _conflict()
        evidence = business_evidence.get_run(snapshot["evidenceRunId"],principal)
        reference,_ = business_reports._reference(evidence,snapshot["question"])
        if canonical({k:snapshot.get(k) for k in SEAL_KEYS})!=canonical({k:reference[k] for k in SEAL_KEYS}):
            _conflict()
        fixed = snapshot.get("mappingPlan")
        choices = [{k:p[k] for k in ("salesKey","masterKey")} for p in fixed["pairs"]] if fixed is not None else None
        sources,mapping,selected = _metadata(evidence,principal,choices)
        if (canonical(mapping)!=canonical(fixed) or snapshot.get("mappingPlanDigest")!=(digest(mapping) if mapping is not None else None)
                or canonical(snapshot["screeningIntent"])!=canonical(_intent(snapshot["screeningIntent"]["id"],selected))):
            _conflict()
        reference.update(reportId=actual.id,screeningIntent=snapshot["screeningIntent"])
        if mapping is not None: reference["mappingRef"] = business_integrated.mapping_reference(mapping)
        if bool(actual.budget_plan_id)!=("budgetRef" in snapshot): _conflict()
        if actual.budget_plan_id:
            if check_budget:
                budget = business_budget_store.binding_for_report(actual,principal)
                if snapshot["budgetRef"]!=budget.reference: _conflict()
            reference["budgetRef"] = snapshot["budgetRef"]
        if canonical(reference)!=actual.workflow.input_json: _conflict()
        business_evidence_store.assert_current(evidence)
        return actual,snapshot,reference,evidence,sources,selected
    except (AnalysisContractError,ValueError,TypeError,KeyError,AttributeError,RecursionError) as error:
        raise AiError("筛查报告固定协议无效", "conflict", 409) from error
