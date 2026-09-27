"""Fixed mapping and optional promotion budget for a separate report profile.

The profile is selected only by the internal report creator. Old report graphs,
snapshots, catalogs, and budget parameter bindings retain their own protocols.
"""
from dataclasses import dataclass
import json

from business_analysis import mapping_plan
from business_analysis.contracts import AnalysisContractError
from . import business_evidence, business_evidence_store, business_budget_store, models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier, passive, mutation, uid

PROFILE = "business-agent-integrated-reference-v1"
SURFACE = "business_agent_integrated_v1"
DIRECTORY_TOOL = "get_business_integrated_directory_v1"
TABLE_TOOL = "get_business_integrated_analysis_table_v1"
BUDGET_TOOL = "get_business_integrated_budget_v1"
TOOLS = frozenset({DIRECTORY_TOOL, TABLE_TOOL, BUDGET_TOOL})
NODES = frozenset({"commerce", "promotion", "market_b2b", "independent_review", "report"})
BUDGET_NODES = frozenset({"promotion", "independent_review", "report"})
MAPPED_NODES = frozenset({"commerce", "independent_review", "report"})
OUTPUT_LIMITS = {"commerce": 2000, "promotion": 2000, "market_b2b": 2000, "independent_review": 1500, "report": 8000}
SEAL_KEYS = ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest", "sourceCount")


def is_snapshot(snapshot):
    if snapshot.get("executionProfile") != PROFILE:
        return False
    if (snapshot.get("schemaVersion") != "business-report-v1" or snapshot.get("evidenceProtocol") != "reference-v2"
            or "budgetPlan" in snapshot or "budgetPlanDigest" in snapshot
            or type(snapshot.get("mappingPlan")) is not dict or type(snapshot.get("mappingPlanDigest")) is not str
            or not snapshot.get("reportId")):
        raise AiError("集成报告固定参数协议无效", "conflict", 409)
    identifier(snapshot["reportId"])
    if "budgetRef" in snapshot:
        ref = snapshot["budgetRef"]
        if (type(ref) is not dict or set(ref) != {"schemaVersion", "id", "planDigest", "bindingDigest"}
                or ref.get("schemaVersion") != "business-budget-reference-v1"):
            raise AiError("集成报告固定预算引用无效", "conflict", 409)
    return True


def mapping_reference(plan):
    return {"schemaVersion": "business-mapping-reference-v1", "planDigest": digest(plan), "pairCount": len(plan["pairs"])}


@dataclass(frozen=True)
class Prepared:
    owner_email: str
    scope_json: str
    snapshot_json: str
    reference_json: str
    budget: object = None

    @property
    def snapshot(self): return json.loads(self.snapshot_json)

    @property
    def reference(self): return json.loads(self.reference_json)

    @property
    def plan(self): return self.snapshot["mappingPlan"]


def prepare(evidence, choices, principal, report_id, question, *, budget=None, previous_report_id=None):
    from . import business_reports
    current_principal(principal, admin=True)
    identifier(report_id)
    reference, sources = business_reports._reference(evidence, question)
    try:
        built = mapping_plan.build(sources, choices)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("报告关联计划无效", "conflict", 409) from error
    queries = [source["query"] for source in sources]
    platforms, shops = {q["platform"] for q in queries}, {q["shop"] for q in queries if q.get("shop")}
    scope = {"platform": next(iter(platforms)) if len(platforms) == 1 else "多平台",
        "shop": next(iter(shops)) if len(shops) == 1 else "多店铺" if shops else "市场样本",
        "startDate": queries[0]["startDate"], "endDate": queries[0]["endDate"]}
    snapshot = {"schemaVersion": "business-report-v1", "executionMode": "parallel-v1", "executionProfile": PROFILE,
        "evidenceProtocol": "reference-v2", **{k:v for k,v in reference.items() if k != "inputMode"},
        "reportId": report_id, "mappingPlan": built["plan"], "mappingPlanDigest": built["planDigest"],
        "scope": scope, "libraryVersion": 0, "pipeline": {"name": "商品关联深度经营分析"},
        "template": {"name": "多Agent经营诊断", "format": "html", "sections": business_reports.SECTIONS}, "skills": []}
    reference.update(reportId=report_id, mappingRef=mapping_reference(built["plan"]))
    if budget is not None:
        if (type(budget) is not business_budget_store.PreparedBudget or budget.binding["reportId"] != report_id
                or any(budget.binding[k] != snapshot[k] for k in SEAL_KEYS if k != "sourceCount")):
            raise AiError("预算与商品关联报告身份不一致", "conflict", 409)
        snapshot["budgetRef"] = reference["budgetRef"] = budget.reference
    if previous_report_id is not None:
        snapshot["previousReportId"] = identifier(previous_report_id)
    return Prepared(principal.email.lower(), canonical(principal.scope),
        canonical(passive(snapshot, 32768)), canonical(passive(reference, 8000)), budget)


def revalidate(prepared, principal):
    if type(prepared) is not Prepared:
        raise AiError("集成报告准备对象无效", "conflict", 409)
    current_principal(principal, admin=True)
    if (prepared.owner_email, prepared.scope_json) != (principal.email.lower(), canonical(principal.scope)):
        raise AiError("集成报告准备身份已变化", "access_denied", 403)
    snapshot = prepared.snapshot
    if not is_snapshot(snapshot):
        raise AiError("集成报告准备协议无效", "conflict", 409)
    budget = business_budget_store.revalidate(prepared.budget, principal) if prepared.budget else None
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    choices = [{k: p[k] for k in ("salesKey", "masterKey")} for p in snapshot["mappingPlan"]["pairs"]]
    fresh = prepare(evidence, choices, principal, snapshot["reportId"], snapshot["question"],
        budget=budget, previous_report_id=snapshot.get("previousReportId"))
    if fresh != prepared:
        raise AiError("集成报告准备计划或证据已变化", "conflict", 409)
    return fresh


def bound(report, principal, *, check_budget=True):
    """Return an owner-bound report and rebuilt fixed input, without facts scan."""
    from . import business_reports
    current_principal(principal, admin=True)
    actual = m.AiReportRun.objects.filter(pk=identifier(report.id)).select_related("workflow").first()
    if actual is None:
        raise AiError("集成报告不存在", "not_found", 404)
    authorize_owner(actual, principal); authorize_owner(actual.workflow, principal)
    snapshot = json.loads(actual.snapshot_json)
    if (not is_snapshot(snapshot) or snapshot["reportId"] != actual.id or actual.snapshot_json != canonical(snapshot)
            or (actual.owner_email, actual.scope_json) != (actual.workflow.owner_email, actual.workflow.scope_json)
            or report.snapshot_json != actual.snapshot_json):
        raise AiError("集成报告固定身份不一致", "conflict", 409)
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    reference, sources = business_reports._reference(evidence, snapshot["question"])
    if canonical({k:snapshot.get(k) for k in SEAL_KEYS}) != canonical({k:reference[k] for k in SEAL_KEYS}):
        raise AiError("集成报告封存绑定变化", "conflict", 409)
    try:
        plan = mapping_plan._checked_plan(snapshot["mappingPlan"], sources)
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("集成报告关联计划不再有效", "conflict", 409) from error
    if snapshot["mappingPlanDigest"] != digest(plan):
        raise AiError("集成报告关联计划摘要变化", "conflict", 409)
    reference.update(reportId=actual.id, mappingRef=mapping_reference(plan))
    if bool(actual.budget_plan_id) != ("budgetRef" in snapshot):
        raise AiError("集成报告预算参数缺失或错绑，禁止降级", "conflict", 409)
    if actual.budget_plan_id:
        if check_budget:
            fixed = business_budget_store.binding_for_report(actual, principal)
            if snapshot["budgetRef"] != fixed.reference:
                raise AiError("集成报告预算绑定不一致", "conflict", 409)
        reference["budgetRef"] = snapshot["budgetRef"]
    if canonical(reference) != actual.workflow.input_json:
        raise AiError("集成报告工作流固定引用变化", "conflict", 409)
    business_evidence_store.assert_current(evidence)
    return actual, snapshot, reference, evidence, sources


def graph(with_budget):
    from . import business_reports
    value = business_reports.graph()
    for node in value["nodes"]:
        key = node["key"]
        if node["type"] != "agent": continue
        old = 15000 if key == "report" else 2000 if key == "independent_review" else 3000
        node["instruction"] = node["instruction"].replace(str(old)+"个UTF-8字节", str(OUTPUT_LIMITS[key])+"个UTF-8字节")
        node["instruction"] += ("本任务使用集成固定引用。本人先从offset=0调用get_business_integrated_directory_v1，"
            "严格跟随nextOffset直至null。每条来源的mappingPair是用户固定关联；目录不是完整事实。"
            "随后调用get_business_integrated_analysis_table_v1；mode=native用sourceKey及可选baselineKey，"
            "mode=mapped用pairKey及可选baselinePairKey且仅sku/spu。不得混用两种身份。"
            "分析页沿table.pagination.nextOffset读取，不能猜测页长或跨表复用偏移。"
            "映射引用以pairKey替换sourceKey、baselinePairKey替换baselineKey，其余行引用字段不变。"
            "ERP映射不是原生SKU，不证明历史身份、广告归因或关键词利润。歧义、未匹配、缺日必须保留。"
            "商品分析、独立复核和最终报告须本人至少读一个映射分析页，不能借其他Agent读取证明。"
            "全部成功工具页由服务端重新核验，但分页未读完不可声称读完全量；完整明细由文件交付。")
        if with_budget:
            node["instruction"] += ("本任务另有固定推广预算。推广、独立复核及最终报告须本人从offset=0调用"
                "get_business_integrated_budget_v1，跟随budget.pagination.nextOffset读完；其他节点一旦开始也须读完。"
                "参数固定，ERP毛利不能替换推广情景假设，情景不是收益保证。")
    return value


def create(body, principal, evidence, client, question, dry):
    """Called only after public request validation in business_reports.create."""
    from . import workflows, reports, business_reports

    def replay():
        old = m.AiReportRun.objects.select_related("workflow").filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old is None: return None
        authorize_owner(old, principal)
        if old.request_digest != digest(body):
            raise AiError("请求标识已绑定其他报告或关联计划", "conflict", 409)
        bound(old, principal)
        return {"item":{"id":old.id,"workflowId":old.workflow_id},"replayed":True}

    old = replay()
    if old is not None: return old
    report_id = uid("ai-report")
    budget = business_budget_store.prepare(evidence, body["budgetPlan"], principal, report_id) if "budgetPlan" in body else None
    prepared = prepare(evidence, body["mappingPairs"], principal, report_id, question, budget=budget,
        previous_report_id=body.get("previousReportId"))
    if "previousReportId" in body:
        prior = reports.get(identifier(body["previousReportId"]), principal)
        prior_snapshot = json.loads(prior.snapshot_json)
        if any(prior_snapshot.get(k) != prepared.snapshot.get(k) for k in (*SEAL_KEYS,"question","scope")):
            raise AiError("新报告版本须使用相同封存证据和分析范围", "conflict", 409)
        if is_snapshot(prior_snapshot): bound(prior, principal)
        else: business_reports.bound_reference(prior_snapshot, principal)
    with mutation(principal):
        old = replay()
        if old is not None: return old
        prepared = revalidate(prepared, principal)
        parameters = business_budget_store.insert(prepared.budget, principal) if prepared.budget else None
        flow = workflows.create({"clientRequestId":"business-"+digest([principal.email.lower(),client]),
            "name":"商品关联深度经营分析", "graph":graph(prepared.budget is not None),
            "input":prepared.reference, "dryRun":dry}, principal, True,
            execution_profile=PROFILE, integrated_prepared=prepared)
        if not dry and set(flow["item"]["allowedTools"]) != TOOLS:
            raise AiError("集成分析工具目录未就绪", "service_unavailable", 503)
        row = m.AiReportRun.objects.create(id=report_id, owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
            client_request_id=client, request_digest=digest(body), workflow_id=flow["item"]["id"],
            snapshot_json=prepared.snapshot_json, budget_plan=parameters)
    return {"item":{"id":row.id,"workflowId":row.workflow_id},"replayed":False}
