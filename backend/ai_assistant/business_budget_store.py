"""Internal immutable budget parameters. No public creation or model profile.

The future report creator must insert parameters, workflow, report and audit in
one outer mutation. A parameter row may not commit without its bound report.
"""
from dataclasses import dataclass
import json

from django.db import connection
from django.db.models import BigIntegerField, Count, F, Func, Sum
from business_analysis import budget_reference as contract
from business_analysis.contracts import AnalysisContractError
from . import business_evidence, business_evidence_store as evidence_store, models as m
from .policy import AiError, _mutation_depth, authorize_owner, canonical, current_principal, digest, identifier, uid


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (AnalysisContractError, ValueError, TypeError, KeyError, RecursionError, UnicodeError) as error:
        raise AiError(str(error), "conflict", 409) from error


@dataclass(frozen=True)
class BudgetBinding:
    id: str
    plan_json: str
    binding_json: str

    def __post_init__(self):
        identifier(self.id)
        for raw, maximum in ((self.plan_json, contract.MAX_PLAN_BYTES), (self.binding_json, contract.MAX_BINDING_BYTES)):
            try:
                if type(raw) is not str or len(raw) > maximum or len(raw.encode("utf-8")) > maximum:
                    raise ValueError("预算准备JSON超过容量")
                value = json.loads(raw)
                if type(value) is not dict:
                    raise ValueError("预算准备JSON须为对象")
            except (ValueError, TypeError, UnicodeError, RecursionError) as error:
                raise AiError("预算准备结构无效", "conflict", 409) from error

    @property
    def plan(self):
        return json.loads(self.plan_json)

    @property
    def binding(self):
        return json.loads(self.binding_json)

    @property
    def reference(self):
        return _call(contract.make_reference, self.id, self.binding)


@dataclass(frozen=True)
class PreparedBudget(BudgetBinding):
    result_json: str

    def __post_init__(self):
        super().__post_init__()
        try:
            if type(self.result_json) is not str or len(self.result_json) > 2*1024*1024 or len(self.result_json.encode()) > 2*1024*1024:
                raise ValueError("预算结果超过容量")
            if type(json.loads(self.result_json)) is not dict:
                raise ValueError("预算结果须为对象")
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise AiError("预算准备结果无效", "conflict", 409) from error

    @property
    def result(self):
        return json.loads(self.result_json)


def _prepare(evidence, raw_plan, principal, report_id, plan_id):
    from . import business_budget
    current_principal(principal, admin=True)
    identifier(report_id); identifier(plan_id)
    actual = business_evidence.get_run(evidence.id, principal)
    if (actual.status != "sealed" or not evidence_store.is_v2(actual)
            or (evidence.version, evidence.plan_json, evidence.state_json) != (actual.version, actual.plan_json, actual.state_json)):
        raise AiError("固定预算须绑定当前已封存v2证据", "conflict", 409)
    evidence_store.verify_seal(actual)
    header, seal = json.loads(actual.plan_json), json.loads(actual.state_json)
    plan = _call(contract.normalize_plan, raw_plan)
    # resolve performs the full Reader chain and selected row identity checks.
    result = business_budget.resolve(actual.id, plan, principal)
    if (result["evidenceRunId"], result["evidenceVersion"], result["evidencePlanDigest"]) != (actual.id, actual.version, digest(actual.plan_json)):
        raise AiError("预算计算期间证据绑定变化", "conflict", 409)
    binding = _call(contract.make_binding, result["plan"], report_id=report_id, owner_email=principal.email.lower(),
        scope=principal.scope, evidence_run_id=actual.id, evidence_version=actual.version,
        evidence_plan_digest=digest(actual.plan_json), catalog_digest=header["catalogDigest"],
        sealed_digest=seal["sealedDigest"], analysis_request=header.get("analysisRequest"))
    evidence_store.assert_current(actual)
    return PreparedBudget(plan_id, canonical(result["plan"]), canonical(binding), canonical(result))


def prepare(evidence, raw_plan, principal, report_id):
    """Read-only preparation using the authorized, fully reconciled facts."""
    return _prepare(evidence, raw_plan, principal, report_id, uid("budget-plan"))


def _quota(owner, added):
    # The outer mutation already holds ai_data_revision's global row lock.
    size = Func(F("plan_json"), function="OCTET_LENGTH", output_field=BigIntegerField()) + Func(
        F("binding_json"), function="OCTET_LENGTH", output_field=BigIntegerField())
    rows = m.AiBusinessBudgetPlan.objects
    for query, byte_limit, row_limit in ((rows.all(), contract.GLOBAL_BYTES, contract.GLOBAL_ROWS),
            (rows.filter(owner_email=owner), contract.OWNER_BYTES, contract.OWNER_ROWS)):
        used = query.aggregate(bytes=Sum(size), rows=Count("id"))
        if (used["bytes"] or 0)+added > byte_limit or used["rows"]+1 > row_limit:
            raise AiError("固定预算参数存储额度不足，历史参数仍计入额度", "rate_limited", 429)


def insert(prepared, principal):
    """Insert only inside the complete report-creation mutation; never commit here."""
    if not connection.in_atomic_block or _mutation_depth.get() < 1:
        raise AiError("预算参数须在完整报告mutation事务内写入", "conflict", 409)
    current_principal(principal, admin=True, write=True)
    if type(prepared) is not PreparedBudget:
        raise AiError("预算准备对象无效", "conflict", 409)
    binding = _call(contract.validate_binding, prepared.binding)
    evidence = business_evidence.get_run(binding["evidenceRunId"], principal)
    fresh = _prepare(evidence, prepared.plan, principal, binding["reportId"], prepared.id)
    _call(contract.validate_record, prepared.plan, binding, prepared.reference, expected_binding=fresh.binding)
    if (prepared.plan_json, prepared.binding_json, prepared.result_json) != (fresh.plan_json, fresh.binding_json, fresh.result_json):
        raise AiError("预算准备结果已变化，不能提交", "conflict", 409)
    if m.AiBusinessBudgetPlan.objects.filter(pk=prepared.id).exists():
        raise AiError("预算参数ID已存在；须由原报告请求恢复", "conflict", 409)
    _quota(principal.email.lower(), len(prepared.plan_json.encode())+len(prepared.binding_json.encode()))
    return m.AiBusinessBudgetPlan.objects.create(id=prepared.id, owner_email=principal.email.lower(), scope_json=canonical(principal.scope),
        evidence=evidence, evidence_version=evidence.version, plan_json=prepared.plan_json, plan_digest=binding["planDigest"],
        binding_json=prepared.binding_json, binding_digest=digest(prepared.binding_json))


def binding_for_report(report, principal):
    """Check immutable references and seal; does not certify calculated rows."""
    current_principal(principal, admin=True)
    actual = m.AiReportRun.objects.filter(pk=identifier(report.id)).select_related("workflow").first()
    if actual is None:
        raise AiError("固定预算报告不存在", "not_found", 404)
    authorize_owner(actual, principal)
    authorize_owner(actual.workflow, principal)
    if (actual.workflow.owner_email, actual.workflow.scope_json) != (actual.owner_email, actual.scope_json):
        raise AiError("固定预算工作流身份与报告不一致", "conflict", 409)
    try:
        snapshot = json.loads(actual.snapshot_json)
        from . import business_integrated, business_screening_runtime
        integrated = business_integrated.is_snapshot(snapshot)
        screening = business_screening_runtime.is_snapshot(snapshot)
        if integrated:
            # The integrated binder revalidates the complete mapping plan and
            # workflow; omit only this budget check to avoid mutual recursion.
            business_integrated.bound(actual, principal, check_budget=False)
        if screening:
            business_screening_runtime.bound(actual, principal, check_budget=False)
        if (snapshot.get("executionProfile") != contract.PROFILE and not integrated and not screening or snapshot.get("evidenceProtocol") != "reference-v2"
                or snapshot.get("schemaVersion") != "business-report-v1" or "budgetPlan" in snapshot
                or snapshot.get("reportId") != actual.id or not actual.budget_plan_id):
            raise AiError("报告未绑定独立固定预算协议", "conflict", 409)
        row = m.AiBusinessBudgetPlan.objects.filter(pk=actual.budget_plan_id).first()
        if row is None:
            raise AiError("固定预算参数缺失，禁止降级为无预算报告", "conflict", 409)
        authorize_owner(row, principal)
        binding = _call(contract.validate_binding, json.loads(row.binding_json))
        evidence = business_evidence.get_run(binding["evidenceRunId"], principal)
        if evidence.status != "sealed" or not evidence_store.is_v2(evidence):
            raise AiError("固定预算证据不再是封存v2", "conflict", 409)
        evidence_store.verify_seal(evidence)
        header = json.loads(evidence.plan_json)
        plan = _call(contract.normalize_plan, json.loads(row.plan_json))
        expected_binding = _call(contract.make_binding, plan, report_id=actual.id, owner_email=principal.email.lower(),
            scope=principal.scope, evidence_run_id=evidence.id, evidence_version=evidence.version,
            evidence_plan_digest=digest(evidence.plan_json), catalog_digest=header["catalogDigest"],
            sealed_digest=json.loads(evidence.state_json)["sealedDigest"], analysis_request=header.get("analysisRequest"))
        _call(contract.validate_record, plan, binding, snapshot.get("budgetRef"), expected_binding=expected_binding)
        expected = {k: binding[k] for k in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest")}
        expected["sourceCount"] = header["sourceCount"]
        if (row.owner_email != actual.owner_email or row.scope_json != actual.scope_json
                or row.evidence_id != evidence.id or row.evidence_version != evidence.version
                or row.plan_digest != binding["planDigest"] or row.binding_digest != digest(row.binding_json)
                or row.plan_json != canonical(plan) or row.binding_json != canonical(expected_binding)
                or any(type(snapshot.get(k)) is not type(v) or snapshot[k] != v for k, v in expected.items())
                or snapshot["budgetRef"]["id"] != row.id):
            raise AiError("固定预算参数、报告及封存绑定不一致", "conflict", 409)
        evidence_store.assert_current(evidence)
        return BudgetBinding(row.id, row.plan_json, row.binding_json)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise AiError("固定预算持久参数无效，禁止降级", "conflict", 409) from error


def revalidate(prepared, principal):
    """Recompute a prepared object, including before report persistence."""
    if type(prepared) is not PreparedBudget:
        raise AiError("预算准备对象无效", "conflict", 409)
    binding = _call(contract.validate_binding, prepared.binding)
    evidence = business_evidence.get_run(binding["evidenceRunId"], principal)
    fresh = _prepare(evidence, prepared.plan, principal, binding["reportId"], prepared.id)
    if prepared != fresh:
        raise AiError("预算准备结果已变化", "conflict", 409)
    return fresh


def load(report, principal):
    """Full reader reconciliation and arithmetic, beyond lightweight binding."""
    fixed = binding_for_report(report, principal)
    evidence = business_evidence.get_run(fixed.binding["evidenceRunId"], principal)
    fresh = _prepare(evidence, fixed.plan, principal, report.id, fixed.id)
    if (fresh.plan_json, fresh.binding_json) != (fixed.plan_json, fixed.binding_json):
        raise AiError("预算重算期间绑定变化", "conflict", 409)
    return fresh


def resolve_fixed(report, principal):
    return load(report, principal).result


def page(report, principal, *, offset=0, limit=10):
    prepared = load(report, principal)
    return _call(contract.page, prepared.result, prepared.binding, budget_ref=prepared.reference,
        report_id=report.id, offset=offset, limit=limit)
