"""Internal screening over a real fixed report; no API, runtime or cache registration.

An internal VerifiedScreening is not an authorization credential. Every use
reloads the actual report, workflow, sealed catalog and current principal.
The pure scanner's unpublished authority flags deliberately remain unchanged.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json

from business_analysis import diagnostic_screening, mapping_plan
from business_analysis.contracts import AnalysisContractError
from business_analysis.planning import validate_analysis_request
from business_analysis.results import stream_table
from business_analysis.partitioned import Checkpoint
from . import business_evidence, business_integrated, business_mapped_analysis, business_reports, models as m
from .business_sealed import Reader
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier

MAX_RESPONSE_BYTES = 38000
PAGE_SIZE = 20
_TOKEN = object()


def _conflict(message="筛查固定报告或封存证据已变化"):
    raise AiError(message, "conflict", 409)


def _promotion_bound(report, snapshot, principal):
    """Rebuild the promotion report's screening roots without reading fact pages.

    The persisted promotion binder reads a published screening result, so using
    it here would make screening publication and replay recursively depend on
    themselves. This check uses only the sealed source directory and immutable
    report/workflow/budget metadata; the later Agent admission owns the live
    model and central tool catalog checks.
    """
    from business_analysis import budget_reference
    from . import business_evidence_store, business_promotion_runtime_contract as promotion
    from . import business_screening_runtime
    from . import workflows

    if principal.scope is not None or report.scope_json != canonical(None) or report.workflow.dry_run:
        _conflict("词货筛查只接受无范围管理员的正式报告")
    evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
    if evidence.status != "sealed" or not business_evidence_store.is_v2(evidence):
        _conflict("词货筛查需要当前封存v2证据")
    intent = snapshot["screeningIntent"]
    fixed_mapping = snapshot.get("mappingPlan")
    choices = ([{key: pair[key] for key in ("salesKey", "masterKey")} for pair in fixed_mapping["pairs"]]
        if fixed_mapping is not None else None)
    base = business_screening_runtime.prepare(evidence, principal, report.id,
        snapshot["question"], intent["id"], choices=choices)
    base_snapshot, base_reference = base.snapshot, base.reference
    budget_row = None
    if report.budget_plan_id is not None:
        budget_row = m.AiBusinessBudgetPlan.objects.filter(pk=report.budget_plan_id).first()
        if budget_row is None:
            _conflict("词货筛查固定预算参数缺失")
        authorize_owner(budget_row, principal)
        plan = budget_reference.normalize_plan(json.loads(budget_row.plan_json))
        binding = budget_reference.validate_binding(json.loads(budget_row.binding_json))
        header, seal = json.loads(evidence.plan_json), json.loads(evidence.state_json)
        expected = budget_reference.make_binding(plan, report_id=report.id,
            owner_email=principal.email.lower(), scope=principal.scope, evidence_run_id=evidence.id,
            evidence_version=evidence.version, evidence_plan_digest=digest(evidence.plan_json),
            catalog_digest=header["catalogDigest"], sealed_digest=seal["sealedDigest"],
            analysis_request=header.get("analysisRequest"))
        budget_reference.validate_record(plan, binding, snapshot["budgetRef"], expected_binding=expected)
        if (budget_row.owner_email != report.owner_email or budget_row.scope_json != report.scope_json
                or budget_row.evidence_id != evidence.id or budget_row.evidence_version != evidence.version
                or budget_row.plan_json != canonical(plan) or budget_row.binding_json != canonical(expected)
                or budget_row.plan_digest != binding["planDigest"]
                or budget_row.binding_digest != digest(budget_row.binding_json)
                or snapshot["budgetRef"]["id"] != budget_row.id):
            _conflict("词货筛查预算行与封存根不一致")
        base_snapshot["budgetRef"] = base_reference["budgetRef"] = snapshot["budgetRef"]
    elif "budgetRef" in snapshot:
        _conflict("词货筛查预算引用没有实际参数行")
    base_snapshot["executionProfile"] = promotion.PROFILE
    sources = business_evidence_store.catalog(evidence)
    context = {"reportId": report.id, "runId": evidence.id,
        "screeningId": intent["id"], "sealedDigest": base_snapshot["sealedDigest"]}
    selector = {key: snapshot["promotionSelector"][key] for key in ("sourceKey", "baselineKey")
        if key in snapshot["promotionSelector"]}
    fragment = promotion.freeze_snapshot(sources, context, selector)
    for key, value in (("promotionSelector", fragment["promotionSelector"]),
                       ("contextDigest", fragment["contextDigest"]),
                       ("promotionCatalogDigest", fragment["catalogDigest"]),
                       ("promotionAlgorithmVersion", fragment["promotionAlgorithmVersion"])):
        base_snapshot[key] = value
    promotion_ref = {"schemaVersion": "business-promotion-workflow-reference-candidate-v1",
        **{key: base_snapshot[key] for key in ("promotionSelector", "contextDigest", "sealedDigest",
            "catalogDigest", "promotionCatalogDigest", "promotionAlgorithmVersion")}}
    base_reference["promotionRef"] = promotion_ref
    graph = workflows.validate_graph(promotion.graph(budget_row is not None))
    flow = report.workflow
    nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position")[:7])
    if (report.snapshot_json != canonical(base_snapshot) or flow.input_json != canonical(base_reference)
            or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
            or flow.allowed_tools_json != canonical(list(promotion.TOOL_ORDER))
            or len(nodes) != len(graph["nodes"])
            or any((node.position, node.node_key, node.node_type, node.depends_on_json, node.instruction)
                != (index, spec["key"], spec["type"], canonical(spec["dependsOn"]), spec["instruction"])
                for index, (node, spec) in enumerate(zip(nodes, graph["nodes"])))):
        _conflict("词货筛查持久报告、工作流或固定节点不一致")
    business_evidence_store.assert_current(evidence)
    return report, snapshot, base_reference, evidence


@dataclass(frozen=True, slots=True, init=False)
class VerifiedScreening:
    """Process-local completed result, created only after all contexts exit."""
    _binding_json: str
    _result_json: str

    def __init__(self, token, binding, result):
        if token is not _TOKEN:
            raise AiError("筛查结果只能由内部完整扫描创建")
        object.__setattr__(self, "_binding_json", canonical(binding))
        object.__setattr__(self, "_result_json", canonical(result))

    @property
    def summary(self):
        value = json.loads(self._result_json)
        return {key: value[key] for key in ("schemaVersion", "authority", "bindingDigest", "planDigest", "resultDigest")}


def _load(report_id, principal):
    """Describe trusted fixed records without reading fact pages or budget math."""
    current_principal(principal, admin=True)
    report = m.AiReportRun.objects.select_related("workflow").filter(pk=identifier(report_id)).first()
    if report is None:
        raise AiError("报告不存在", "not_found", 404)
    authorize_owner(report, principal)
    authorize_owner(report.workflow, principal)
    if (report.owner_email, report.scope_json) != (report.workflow.owner_email, report.workflow.scope_json):
        _conflict("报告与工作流身份不一致")
    try:
        snapshot = json.loads(report.snapshot_json)
        from . import business_screening_runtime, business_promotion_runtime_contract as promotion
        screening_profile = type(snapshot) is dict and business_screening_runtime.is_snapshot(snapshot)
        promotion_profile = type(snapshot) is dict and snapshot.get("executionProfile") == promotion.PROFILE
        if (type(snapshot) is not dict or canonical(snapshot) != report.snapshot_json
                or snapshot.get("schemaVersion") != business_reports.SCHEMA
                or not (screening_profile or promotion_profile or business_reports.is_v2_snapshot(snapshot))):
            _conflict("筛查仅支持固定封存v2报告")
        if screening_profile:
            report, snapshot, reference, evidence, _, _ = business_screening_runtime.bound(report, principal)
        elif promotion_profile:
            report, snapshot, reference, evidence = _promotion_bound(report, snapshot, principal)
        elif business_integrated.is_snapshot(snapshot):
            report, snapshot, reference, evidence, _ = business_integrated.bound(report, principal)
        else:
            if any(key in snapshot for key in ("mappingPlan", "mappingPlanDigest")):
                _conflict("非集成报告不能携带临时关联计划")
            if bool(report.budget_plan_id) != business_reports.is_budget_snapshot(snapshot):
                _conflict("固定预算参数缺失或协议不一致")
            reference = business_reports.bound_reference(snapshot, principal)
            evidence = business_evidence.get_run(snapshot["evidenceRunId"], principal)
        if report.workflow.input_json != canonical(reference):
            _conflict("报告工作流输入与固定引用不一致")
        reader = Reader(evidence, principal)
        sources = reader.sources
        infos = {source["key"]: reader.info(source["key"]) for source in sources}
        request = json.loads(evidence.plan_json).get("analysisRequest")
        if request is not None:
            request = validate_analysis_request(request)
        fixed_mapping = snapshot.get("mappingPlan")
        if fixed_mapping is not None:
            fixed_mapping = mapping_plan._checked_plan(fixed_mapping, sources)
            if digest(fixed_mapping) != snapshot.get("mappingPlanDigest"):
                _conflict("固定关联计划摘要变化")
        binding = {"reportId":report.id, "workflowId":report.workflow_id,
            "ownerEmail":report.owner_email, "scope":json.loads(report.scope_json), "role":principal.role,
            "snapshotDigest":digest(report.snapshot_json), "workflowInputDigest":digest(report.workflow.input_json),
            "executionProfile":snapshot["executionProfile"],
            **{key:reference[key] for key in business_integrated.SEAL_KEYS},
            "sourceInfosDigest":digest(infos), "sourcesDigest":digest(sources),
            "analysisRequestDigest":digest(request) if request is not None else None,
            "mappingPlanDigest":digest(fixed_mapping) if fixed_mapping is not None else None,
            "budgetRef":snapshot.get("budgetRef"), "algorithmVersion":diagnostic_screening.ALGORITHM_VERSION}
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("筛查报告固定范围未通过核验", "conflict", 409) from error
    return binding, reader, request, fixed_mapping, sources, infos


def _revalidate(binding, principal):
    actual = _load(binding["reportId"], principal)
    if canonical(actual[0]) != canonical(binding):
        _conflict()
    return actual


def _describe(loaded):
    from business_analysis import screening_plan
    binding, _, request, fixed_mapping, sources, infos = loaded
    if request is None:
        plan = {"schemaVersion":"business-screening-plan-v1", "canScreen":False,
            "reason":"missing_fixed_analysis_request", "analysisRequest":None,
            "requestedCoverage":[], "descriptors":[], "limitations":["证据未固定分析维度和窗口，不推断默认范围"]}
        plan["planDigest"] = digest(plan)
    else:
        try:
            plan = screening_plan.build(request, sources, infos, mapping_plan=fixed_mapping)
        except AnalysisContractError as error:
            raise AiError(str(error), "conflict", 409) from error
    return {"schemaVersion":"business-diagnostic-screening-preview-v1", "previewOnly":True,
        "factsScanned":False, "binding":binding, "bindingDigest":digest(binding), "plan":plan}


def describe_for_report(report_id, principal):
    loaded = _load(report_id, principal)
    value = _describe(loaded)
    _revalidate(loaded[0], principal)
    return value


def prepare_for_report(report_id, principal, *, checkpoint=None):
    """Cooperative hooks are internal only and never authorize partial results."""
    checkpoint = Checkpoint.wrap(checkpoint)
    try:
        return _prepare_for_report(report_id,principal,checkpoint)
    except BaseException:
        # Reader.pages and existing owning adapters may translate ValueError;
        # cancellation must retain the original caller's exception instance.
        if checkpoint is not None: checkpoint.raise_if_failed()
        raise


def _prepare_for_report(report_id, principal, checkpoint):
    options = {"checkpoint":checkpoint} if checkpoint is not None else {}
    if checkpoint is not None: checkpoint({"stage":"screen_prepare","phase":"before"})
    loaded = _load(report_id, principal)
    binding, reader, _, fixed_mapping, _, infos = loaded
    plan = _describe(loaded)["plan"]
    if checkpoint is not None: checkpoint({"stage":"screen_prepare","phase":"after_describe"})
    if not plan["canScreen"]:
        reason = plan.get("reason") or "; ".join(
            f"{item['reason']} ({item['actual']}/{item['limit']})" for item in plan["admissionFailures"])
        raise AiError("完整筛查计划不可执行："+reason, "conflict", 409)

    @contextmanager
    def open_table(descriptor):
        _revalidate(binding, principal)
        if descriptor["mode"] == "mapped":
            mapped = descriptor["mapping"]
            with business_mapped_analysis.table(binding["evidenceRunId"], fixed_mapping,
                    mapped["pairKey"], descriptor["dimension"], principal,
                    baseline_pair_key=mapped["baselinePairKey"],**options) as table:
                yield table.header(), table.scan()
        else:
            key = descriptor["source"]["key"]
            baseline = descriptor["baseline"]
            kwargs = ({"baseline_pages":reader.pages(baseline["key"],**options),
                "baseline_expected":infos[baseline["key"]]["expected"]} if baseline else {})
            with stream_table(reader.pages(key,**options), descriptor["dimension"], infos[key]["expected"], **kwargs,**options) as opened:
                yield opened
        _revalidate(binding, principal)

    try:
        result = diagnostic_screening.prepare({key:binding[key] for key in (
            "reportId", "evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest")},
            plan["descriptors"], open_table, limits=plan["limits"],**options)
    except AnalysisContractError as error:
        raise AiError(str(error), "conflict", 409) from error
    _revalidate(binding, principal)
    coverage_planned = all(item["status"] in {"planned", "dependency", "not_applicable", "outside_fixed_request"}
        for item in plan["requestedCoverage"])
    source_dates_complete = coverage_planned and all(
        table["sourceCoverage"].get("status") == "dates_present"
        and (table["baselineCoverage"] is None or table["baselineCoverage"].get("status") == "dates_present")
        for table in result["coverage"]["tables"])
    authority = {"completeSourceTraversalForExecutedTables":True, "executedTablesComplete":True,
        "requestedCoveragePlanned":coverage_planned, "requestedTablesExecutedComplete":coverage_planned,
        "requestedSourceDateCoverageComplete":source_dates_complete, "entityDailyCoverageVerified":False,
        "binding":binding,
        "reportId":binding["reportId"], "evidenceRunId":binding["evidenceRunId"],
        "selectionPolicy":plan["selectionPolicy"], "selectionPlanDigest":plan["planDigest"],
        "pureResultDigest":result["resultDigest"], "tableCount":result["coverage"]["tableCount"],
        "rowVisits":result["coverage"]["rowVisits"], "partitionCount":len(result["partitions"]),
        "limitations":result["limitations"]}
    value = {"schemaVersion":"business-diagnostic-screening-v1", "authority":authority,
        "bindingDigest":digest(binding), "planDigest":plan["planDigest"], "plan":plan, "prepared":result}
    value["resultDigest"] = digest(value)
    if checkpoint is not None:
        checkpoint({"stage":"screen_complete"})
        _revalidate(binding,principal)
    return VerifiedScreening(_TOKEN, binding, value)


def _verified(value, principal):
    if type(value) is not VerifiedScreening:
        raise AiError("只接受本进程内部完整筛查结果")
    binding = json.loads(value._binding_json)
    _revalidate(binding, principal)
    return binding, json.loads(value._result_json)


def _page(base, rows, offset):
    if type(offset) is not int or not 0 <= offset <= len(rows):
        raise AiError("筛查页偏移无效")
    selected = []

    def render():
        end = offset+len(selected)
        value = {**base, "items":selected, "pagination":{"offset":offset, "limit":PAGE_SIZE,
            "returned":len(selected), "total":len(rows), "nextOffset":end if end < len(rows) else None}}
        value["pageDigest"] = digest(value)
        return value

    for row in rows[offset:offset+PAGE_SIZE]:
        selected.append(row)
        if len(canonical(render()).encode("utf-8")) > MAX_RESPONSE_BYTES:
            selected.pop()
            break
    value = render()
    if (offset < len(rows) and not selected) or len(canonical(value).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise AiError("完整筛查记录超过响应容量", "payload_too_large", 413)
    return value


def _base(value, kind):
    return {"schemaVersion":"business-diagnostic-screening-"+kind+"-v1",
        **{key:value[key] for key in ("bindingDigest", "planDigest", "resultDigest", "authority")}}


def coverage_page(verified, principal, *, offset=0):
    binding, value = _verified(verified, principal)
    plan, result = value["plan"], value["prepared"]
    entries = [{"kind":"family", "value":row} for row in plan.get("families", [])]
    entries += [{"kind":"requested", "value":row} for row in plan["requestedCoverage"]]
    entries += [{"kind":"table", "value":row} for row in result["coverage"]["tables"]]
    entries += [{"kind":"partition", "value":{key:val for key,val in row.items() if key != "candidates"}}
        for row in result["partitions"]]
    response = _page(_base(value, "coverage"), entries, offset)
    _revalidate(binding, principal)
    return response


def candidate_page(verified, principal, partition_key, *, offset=0):
    binding, value = _verified(verified, principal)
    identifier(partition_key, "partitionKey")
    partition = next((row for row in value["prepared"]["partitions"] if row["partitionKey"] == partition_key), None)
    if partition is None:
        raise AiError("筛查候选分区不存在", "not_found", 404)
    base = {**_base(value, "candidates"), "partition":{key:val for key,val in partition.items() if key != "candidates"}}
    response = _page(base, partition["candidates"], offset)
    _revalidate(binding, principal)
    return response
