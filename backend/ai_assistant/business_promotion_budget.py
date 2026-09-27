"""Unregistered, read-only budget pages for an actual promotion report.

Every page recalculates from the sealed evidence and checks the persisted plan.
The result is budget data, never Agent-read proof or a model dispatch permit.
"""
import json

from business_analysis import budget_reference
from business_analysis.contracts import AnalysisContractError
from . import business_budget_store as budget_store, business_evidence as evidence_service
from . import business_evidence_store as evidence_store, business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract, models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier

MAX_RESPONSE_BYTES = 38000
LIMIT = 20
# The TS bridge currently validates the shared screening budget envelope.
# Profile identity lives in reference.promotionRef and the owning report root.
ENVELOPE_SCHEMA = "business-screening-budget-v1"


def _conflict(message="词货预算与固定报告或封存证据不一致"):
    raise AiError(message, "conflict", 409)


def _roots(report_id, principal):
    current_principal(principal, admin=True)
    if principal.scope is not None:
        raise AiError("词货预算仅允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    bound = runtime.bound_persisted(report_id, principal)
    report = m.AiReportRun.objects.select_related("workflow", "budget_plan").filter(pk=report_id).first()
    if report is None:
        _conflict()
    authorize_owner(report, principal)
    authorize_owner(report.workflow, principal)
    if report.budget_plan_id is None:
        _conflict("本词货报告没有固定预算，不能返回空预算页")
    saved = report.budget_plan
    authorize_owner(saved, principal)
    try:
        snapshot = json.loads(report.snapshot_json)
        reference = json.loads(report.workflow.input_json)
        if (report.snapshot_json != canonical(snapshot)
                or report.workflow.input_json != canonical(reference)
                or snapshot["executionProfile"] != contract.PROFILE
                or report.id != bound["reportId"]
                or digest(report.snapshot_json) != bound["snapshotDigest"]
                or digest(report.workflow.input_json) != bound["workflowInputDigest"]
                or snapshot["budgetRef"] != reference["budgetRef"]
                or saved.owner_email != report.owner_email
                or saved.scope_json != report.scope_json
                or saved.evidence_id != bound["rootBindings"]["evidenceRunId"]
                or saved.evidence_version != bound["rootBindings"]["evidenceVersion"]):
            _conflict()
        evidence = evidence_service.get_run(saved.evidence_id, principal)
        if evidence.status != "sealed" or not evidence_store.is_v2(evidence):
            _conflict("词货预算证据不再是当前封存 v2")
        prepared = budget_store._prepare(evidence, json.loads(saved.plan_json),
            principal, report.id, saved.id)
        if (prepared.plan_json != saved.plan_json
                or prepared.binding_json != saved.binding_json
                or prepared.reference != snapshot["budgetRef"]
                or saved.plan_digest != prepared.binding["planDigest"]
                or saved.binding_digest != digest(saved.binding_json)):
            _conflict("词货预算参数与重算结果不一致")
        evidence_store.assert_current(evidence)
        return {"bound": bound, "reference": reference, "prepared": prepared,
            "rowDigest": digest([saved.id, saved.plan_json, saved.binding_json,
                saved.plan_digest, saved.binding_digest])}
    except AiError:
        raise
    except (AnalysisContractError, ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, RecursionError) as error:
        raise AiError("词货预算持久绑定或重算结构无效", "conflict", 409) from error


def read_page(report_id, principal, *, offset=0):
    """Return one fully verified page; explicit budget absence is an error."""
    if type(offset) is not int or not 0 <= offset < 100:
        raise AiError("词货预算分页位置无效", "invalid_request", 400)
    fixed = _roots(report_id, principal)
    prepared = fixed["prepared"]
    result = None
    try:
        for limit in range(LIMIT, 0, -1):
            page = budget_reference.page(prepared.result, prepared.binding,
                budget_ref=prepared.reference, report_id=report_id,
                offset=offset, limit=limit)
            candidate = {"schemaVersion": ENVELOPE_SCHEMA,
                "reference": fixed["reference"], "budget": page}
            candidate["pageDigest"] = digest(candidate)
            if len(canonical(candidate).encode("utf-8")) <= MAX_RESPONSE_BYTES:
                result = candidate
                break
    except (AnalysisContractError, ValueError, TypeError, KeyError,
            UnicodeError, RecursionError) as error:
        raise AiError("词货预算完整分页未通过核验", "conflict", 409) from error
    if result is None:
        raise AiError("单个词货预算对象超过响应容量", "payload_too_large", 413)
    latest = _roots(report_id, principal)
    if (canonical(latest["bound"]) != canonical(fixed["bound"])
            or canonical(latest["reference"]) != canonical(fixed["reference"])
            or latest["rowDigest"] != fixed["rowDigest"]
            or latest["prepared"].result_json != prepared.result_json):
        _conflict("词货预算在分页期间已变化")
    return result
