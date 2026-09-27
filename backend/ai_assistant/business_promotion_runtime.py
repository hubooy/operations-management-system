"""Read-only proposal for a future promotion screening execution profile.

An existing report and its published screening remain in their original
profile. This adapter only binds a proposed selector to their current sealed
roots; it cannot create, authorize, or dispatch the proposed profile.
"""
import json

from business_analysis.contracts import AnalysisContractError
from business_analysis import screening_storage
from . import business_diagnostic_screening as report_binding
from . import business_promotion_runtime_contract as contract
from . import business_promotion_creation_contract as creation_contract
from . import business_evidence as evidence_service, business_evidence_store as evidence_store
from . import business_budget_store as budgets, business_screening_creation as screening_creation
from . import business_screening_store as screening_store
from . import models as m, transport, workflows
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier

SCHEMA = "business-promotion-runtime-candidate-v1"


def _roots(report_id, screening_id, principal):
    """Reload all three owning roots; request values supply IDs only."""
    current_principal(principal, admin=True)
    if principal.scope is not None:
        raise AiError("推广运行候选仅允许无范围管理员", "access_denied", 403)
    report_id, screening_id = identifier(report_id), identifier(screening_id)
    loaded = report_binding._load(report_id, principal)
    binding, sources = loaded[0], loaded[4]
    row, screening_binding, manifest = screening_store._loaded(screening_id, principal)
    if (row.report_id != report_id or canonical(screening_binding) != canonical(binding)
            or row.owner_email != binding["ownerEmail"]
            or row.scope_json != canonical(binding["scope"])):
        raise AiError("筛查结果不属于当前固定报告", "conflict", 409)
    try:
        # _loaded checks the published manifest and plan. Validate all stored
        # pages too, so a candidate cannot cite an incomplete screening root.
        if canonical(screening_storage.validate(screening_store._all(row))) != canonical(manifest):
            raise AnalysisContractError("筛查根清单不一致")
    except (AnalysisContractError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise AiError("固定筛查页未通过完整核验", "conflict", 409) from error
    report = m.AiReportRun.objects.filter(pk=report_id).only("snapshot_json", "budget_plan_id").first()
    if report is None or digest(report.snapshot_json) != binding["snapshotDigest"]:
        raise AiError("报告快照已变化", "conflict", 409)
    snapshot = json.loads(report.snapshot_json)
    # Existing reports are evidence roots, never silently promoted in place.
    if snapshot["executionProfile"] == contract.PROFILE:
        raise AiError("新推广协议尚未注册为持久报告", "conflict", 409)
    context = {"reportId":report_id, "runId":binding["evidenceRunId"],
        "screeningId":screening_id, "sealedDigest":binding["sealedDigest"]}
    origins = {"reportId":report_id, "reportProfile":snapshot["executionProfile"],
        "reportSnapshotDigest":binding["snapshotDigest"], "workflowInputDigest":binding["workflowInputDigest"],
        "screeningId":screening_id, "screeningBindingDigest":row.binding_digest,
        "screeningManifestDigest":row.manifest_digest, "screeningContentRootDigest":row.content_root_digest}
    return sources, context, origins, bool(report.budget_plan_id), binding


def prepare_candidate(report_id, screening_id, selector, principal):
    """Return a copyable proposal, never an executable snapshot or grant."""
    sources, context, origins, with_budget, binding = _roots(report_id, screening_id, principal)
    try:
        snapshot = contract.freeze_snapshot(sources, context, selector)
        contract.checked_snapshot(sources, context, snapshot)
        graph = contract.graph(with_budget)
    except (AnalysisContractError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise AiError("推广来源、比较期或候选协议未通过固定根核验", "conflict", 409) from error
    value = {"schemaVersion":SCHEMA, "origins":origins, "proposedSnapshot":snapshot,
        "proposedGraph":graph, "authorityVerified":False, "registered":False,
        "readiness":"requires_new_persistent_profile"}
    value["candidateDigest"] = digest(value)
    # Repeat the complete owning checks after construction. A changed actor,
    # seal, report, screening or version must not return a stale candidate.
    fresh = _roots(report_id, screening_id, principal)
    if (canonical((fresh[1], fresh[2], fresh[3])) != canonical((context, origins, with_budget))
            or canonical(fresh[0]) != canonical(sources)):
        raise AiError("推广候选的固定来源或身份已变化", "conflict", 409)
    report_binding._revalidate(binding, principal)
    return json.loads(canonical(value))


def checked_candidate(report_id, screening_id, selector, candidate, principal):
    """Check caller-supplied candidate against fresh persisted roots."""
    actual = prepare_candidate(report_id, screening_id, selector, principal)
    if type(candidate) is not dict or canonical(candidate) != canonical(actual):
        raise AiError("推广候选与当前封存根不一致", "conflict", 409)
    return actual


def bound_persisted(report_id, principal):
    """Rebuild a new profile from persisted roots; content stays unready.

    A published screening may become ready only after the owning screening
    reader can verify this profile and its complete stored page chain. The
    current old reader cannot, so a detected published row fails closed.
    """
    current_principal(principal, admin=True)
    if principal.scope is not None:
        raise AiError("词货报告仅允许当前无范围管理员", "access_denied", 403)
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(
        pk=identifier(report_id, "reportId")).first()
    if report is None:
        raise AiError("词货报告不存在", "not_found", 404)
    authorize_owner(report, principal)
    flow = report.workflow
    authorize_owner(flow, principal)
    if (report.owner_email != flow.owner_email or report.scope_json != flow.scope_json
            or report.scope_json != canonical(principal.scope) or flow.dry_run != 0):
        raise AiError("词货报告与工作流身份不一致", "conflict", 409)
    try:
        snapshot = json.loads(report.snapshot_json)
        if (type(snapshot) is not dict or report.snapshot_json != canonical(snapshot)
                or snapshot.get("executionProfile") != contract.PROFILE
                or snapshot.get("reportId") != report.id):
            raise AnalysisContractError("不是固定词货报告")
        intent = snapshot["screeningIntent"]
        selector = snapshot["promotionSelector"]
        request = creation_contract._request({"reportId":report.id,
            "screeningId":intent["id"], "question":snapshot["question"],
            "sourceKey":selector["sourceKey"],
            **({"baselineKey":selector["baselineKey"]} if "baselineKey" in selector else {}),
            **({"mappingPairs":[{key:pair[key] for key in ("salesKey", "masterKey")}
                for pair in snapshot["mappingPlan"]["pairs"]]} if "mappingPlan" in snapshot else {})})
        evidence = evidence_service.get_run(snapshot["evidenceRunId"], principal)
        if evidence.status != "sealed" or not evidence_store.is_v2(evidence):
            raise AnalysisContractError("证据已失去封存v2身份")
        budget = None
        if report.budget_plan_id is not None:
            saved = report.budget_plan
            if (saved.owner_email != report.owner_email or saved.scope_json != report.scope_json
                    or saved.evidence_id != evidence.id or saved.evidence_version != evidence.version):
                raise AnalysisContractError("预算身份与封存证据不一致")
            budget = budgets._prepare(evidence, json.loads(saved.plan_json), principal, report.id, saved.id)
            if (budget.plan_json, budget.binding_json) != (saved.plan_json, saved.binding_json):
                raise AnalysisContractError("预算绑定已变化")
        if bool(budget) != ("budgetRef" in snapshot):
            raise AnalysisContractError("预算有无与报告不一致")
        entries = creation_contract._catalog(transport.catalog(principal, contract.SURFACE))
        _, expected = creation_contract._build(evidence, principal, request, budget, entries)
        sources = evidence_store.catalog(evidence)
        graph = workflows.validate_graph(expected["graph"])
        if (canonical(graph) != canonical(expected["graph"])
                or report.snapshot_json != canonical(expected["snapshot"])
                or flow.input_json != canonical(expected["workflowInput"])
                or flow.graph_json != canonical(graph) or flow.graph_digest != digest(graph)
                or flow.allowed_tools_json != canonical(expected["allowedTools"])
                or flow.tool_policy_digest != expected["toolCatalogDigest"]
                or flow.client_request_id != "business-"+digest([principal.email.lower(), report.client_request_id])):
            raise AnalysisContractError("报告、工作流、工具或固定图不一致")
        model = workflows.resolve_model(flow.model_id)
        if model.id != flow.model_id or model.version != flow.model_version:
            raise AnalysisContractError("模型配置版本已变化")
        model_digest = screening_creation._model_digest(model)
        nodes = list(m.AiWorkflowNodeRuns.objects.filter(run=flow).order_by("position"))
        if len(nodes) != len(graph["nodes"]) or any(
                (node.position, node.node_key, node.node_type, node.depends_on_json, node.instruction)
                != (index, spec["key"], spec["type"], canonical(spec["dependsOn"]), spec["instruction"])
                for index, (node, spec) in enumerate(zip(nodes, graph["nodes"]))):
            raise AnalysisContractError("持久节点与固定五角色图不一致")
        screening_id = intent["id"]
        saved_screening = m.AiBusinessScreeningRun.objects.filter(pk=screening_id).first()
        screening_reference = None
        if saved_screening is not None:
            if saved_screening.report_id != report.id:
                raise AnalysisContractError("筛查结果属于另一报告")
            # This old service currently rejects the new profile. Keep this
            # path for future owning support, but never infer readiness from
            # mere row existence or a caller-supplied manifest.
            verified, binding, manifest = screening_store._loaded(screening_id, principal)
            if (verified.id != screening_id or binding["reportId"] != report.id
                    or canonical(screening_storage.validate(screening_store._all(verified))) != canonical(manifest)):
                raise AnalysisContractError("词货筛查持久内容未通过全链验证")
            screening_reference = screening_store._reference(verified)
        fixed = {"schemaVersion":"business-promotion-persisted-binding-v1",
            "reportId":report.id, "workflowId":flow.id, "screeningId":screening_id,
            "executionProfile":contract.PROFILE, "promotionSelector":snapshot["promotionSelector"],
            "snapshotDigest":digest(report.snapshot_json), "workflowInputDigest":digest(flow.input_json),
            "graphDigest":flow.graph_digest, "toolCatalogDigest":flow.tool_policy_digest,
            "promotionCatalogDigest":snapshot["promotionCatalogDigest"],
            "rootBindings":{"evidenceRunId":evidence.id, "evidenceVersion":evidence.version,
                "sealedDigest":snapshot["sealedDigest"], "catalogDigest":snapshot["catalogDigest"],
                "sourceCount":len(sources), "sourcesDigest":digest(sources)},
            "modelId":model.id, "modelVersion":model.version,
            "screeningStatus":"ready" if screening_reference else "prepared_but_not_ready",
            "contentReady":screening_reference is not None,
            "screeningReference":screening_reference, "runtimeRegistered":False}
        # Final current checks keep a late role, evidence, report, model or
        # central catalog change from returning the earlier metadata.
        current_principal(principal, admin=True)
        latest = m.AiReportRun.objects.select_related("workflow", "budget_plan").get(pk=report.id)
        if (latest.snapshot_json != report.snapshot_json or latest.owner_email != report.owner_email
                or latest.scope_json != report.scope_json
                or latest.workflow.input_json != flow.input_json
                or latest.workflow.graph_json != flow.graph_json
                or latest.workflow.tool_policy_digest != flow.tool_policy_digest
                or latest.workflow.model_id != flow.model_id or latest.workflow.model_version != flow.model_version
                or latest.budget_plan_id != report.budget_plan_id):
            raise AnalysisContractError("词货报告在读取期间已变化")
        if report.budget_plan_id is not None and (
                latest.budget_plan.plan_json != report.budget_plan.plan_json
                or latest.budget_plan.binding_json != report.budget_plan.binding_json):
            raise AnalysisContractError("预算参数在读取期间已变化")
        if m.AiBusinessScreeningRun.objects.filter(pk=screening_id).exists() != (saved_screening is not None):
            raise AnalysisContractError("筛查发布状态在读取期间已变化")
        evidence_store.assert_current(evidence)
        fresh_model = workflows.resolve_model(model.id)
        if screening_creation._model_digest(fresh_model) != model_digest:
            raise AnalysisContractError("模型配置在读取期间已变化")
        if canonical(creation_contract._catalog(transport.catalog(principal, contract.SURFACE))) != canonical(entries):
            raise AnalysisContractError("词货中央目录在读取期间已变化")
        current_principal(principal, admin=True)
        return json.loads(canonical(fixed))
    except AiError:
        raise
    except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("词货持久报告未通过当前来源或版本核验", "conflict", 409) from error
