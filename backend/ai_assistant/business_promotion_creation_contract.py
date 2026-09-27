"""Read-only proposed persistent shape for a future promotion screening report.

This prepares no rows, dispatch permit, model call, or runtime registration.
The eventual writer must recheck this shape under its own atomic authority.
"""
from dataclasses import dataclass
import hashlib
import json

from django.db import connection

from business_analysis.contracts import AnalysisContractError
from . import business_budget_store as budgets, business_evidence as evidence_service
from . import business_evidence_store as evidence_store, business_promotion_runtime_contract as promotion
from . import business_screening_runtime as screening, models as m, transport
from .policy import AiError, canonical, current_principal, digest, fields, identifier, passive, text

SCHEMA = "business-promotion-creation-candidate-v1"
PROMOTION_REF_SCHEMA = "business-promotion-workflow-reference-candidate-v1"
REQUEST_REQUIRED = frozenset(("reportId", "screeningId", "question", "sourceKey"))
REQUEST_OPTIONAL = frozenset(("baselineKey", "mappingPairs", "budgetPlan"))
_TOKEN = object()


def _conflict(message="拟议词货报告的固定范围已变化", code="conflict", status=409):
    raise AiError(message, code, status)


def _request(value):
    fields(value, REQUEST_REQUIRED | REQUEST_OPTIONAL, REQUEST_REQUIRED)
    value = json.loads(canonical(passive(value, 128 * 1024)))
    for key in ("reportId", "screeningId", "sourceKey", "baselineKey"):
        if key in value:
            identifier(value[key], key)
    value["question"] = text(value["question"], "question", 1000)
    if "mappingPairs" in value and type(value["mappingPairs"]) is not list:
        _conflict("商品关联选择必须是完整显式列表", "invalid_request", 400)
    if "budgetPlan" in value and type(value["budgetPlan"]) is not dict:
        _conflict("预算计划必须是完整显式对象", "invalid_request", 400)
    return value


def _catalog(entries):
    try:
        value = json.loads(canonical(passive(entries, 128 * 1024)))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("词货工具目录超过容量或结构无效", "service_unavailable", 503) from error
    if (type(value) is not list or len(value) != len(promotion.TOOL_ORDER)
            or tuple(item.get("name") if type(item) is dict else None for item in value) != promotion.TOOL_ORDER):
        _conflict("词货工具目录、顺序或数量不匹配", "tool_policy_changed")
    for item in value:
        if (set(item) != {"name", "title", "description", "inputSchema", "annotations", "risk",
                          "allowedRoles", "scopePolicy", "execution"}
                or item["risk"] != "read_only" or item["allowedRoles"] != ["admin"]
                or item["scopePolicy"] != "unscoped_only"
                or type(item["inputSchema"]) is not dict
                or item["inputSchema"].get("type") != "object"
                or item["inputSchema"].get("additionalProperties") is not False
                or type(item["execution"]) is not dict
                or item["execution"].get("allowedSurfaces") != [promotion.SURFACE]
                or item["execution"].get("environment") != "worker_inline"
                or item["execution"].get("mode") != "direct"
                or type(item["execution"].get("maxCallsPerRequest")) is not int
                or item["execution"]["maxCallsPerRequest"] != 8):
            _conflict("词货工具目录权限或执行合同无效", "tool_policy_changed")
    return value


def _shape(base, fragment, principal, entries):
    snapshot = base.snapshot
    reference = base.reference
    if (snapshot.get("executionProfile") != screening.PROFILE
            or reference.get("reportId") != snapshot.get("reportId")
            or reference.get("screeningIntent") != snapshot.get("screeningIntent")
            or snapshot.get("sealedDigest") != fragment.get("sealedDigest")):
        _conflict("旧筛查基础引用与词货固定来源不一致")
    snapshot = {**snapshot, "executionProfile": promotion.PROFILE,
        "promotionSelector": fragment["promotionSelector"],
        "contextDigest": fragment["contextDigest"],
        "promotionCatalogDigest": fragment["catalogDigest"],
        "promotionAlgorithmVersion": fragment["promotionAlgorithmVersion"]}
    promotion_ref = {"schemaVersion": PROMOTION_REF_SCHEMA,
        **{key: snapshot[key] for key in ("promotionSelector", "contextDigest", "sealedDigest",
                                         "catalogDigest", "promotionCatalogDigest", "promotionAlgorithmVersion")}}
    reference = {**reference, "promotionRef": promotion_ref}
    graph = promotion.graph("budgetRef" in snapshot)
    values = {"snapshot": snapshot, "workflowInput": reference, "graph": graph,
        "allowedTools": [entry["name"] for entry in entries]}
    raw = {key: canonical(value) for key, value in values.items()}
    if len(raw["snapshot"].encode()) > 32768 or len(raw["workflowInput"].encode()) > 8000 or len(raw["graph"].encode()) > 48 * 1024:
        _conflict("拟议持久协议超过报告、输入或工作流容量", "payload_too_large", 413)
    return {"schemaVersion": SCHEMA, "ownerEmail": principal.email.lower(),
        "scopeJson": canonical(principal.scope), "evidenceRunId": snapshot["evidenceRunId"],
        "evidenceVersion": snapshot["evidenceVersion"],
        **values, "toolCatalogDigest": digest(entries), "allowedToolsDigest": digest(values["allowedTools"]),
        "canonicalBytes": {key: len(value.encode("utf-8")) for key, value in raw.items()},
        "digests": {key: hashlib.sha256(value.encode("utf-8")).hexdigest() for key, value in raw.items()},
        "authorityVerified": False, "registered": False}


@dataclass(frozen=True, slots=True, init=False)
class PreparedCandidate:
    _request_json: str
    _evidence_id: str
    _base: object
    _catalog_json: str
    _shape_json: str

    def __init__(self, token, request, evidence_id, base, entries, shape):
        if token is not _TOKEN:
            _conflict("拟议报告只能由内部真实根准备构造")
        for key, value in (("_request_json", canonical(request)), ("_evidence_id", evidence_id),
                           ("_base", base), ("_catalog_json", canonical(entries)),
                           ("_shape_json", canonical(shape))):
            object.__setattr__(self, key, value)

    @property
    def shape(self):
        return json.loads(self._shape_json)


def _build(evidence, principal, request, budget, entries):
    base = screening.prepare(evidence, principal, request["reportId"], request["question"],
        request["screeningId"], choices=request.get("mappingPairs"), budget=budget)
    sources = evidence_store.catalog(evidence)
    context = {"reportId": request["reportId"], "runId": evidence.id,
        "screeningId": request["screeningId"], "sealedDigest": base.snapshot["sealedDigest"]}
    selector = {key: request[key] for key in ("sourceKey", "baselineKey") if key in request}
    try:
        fragment = promotion.freeze_snapshot(sources, context, selector)
    except AnalysisContractError as error:
        raise AiError("拟议词货来源或基期与封存目录不一致", "conflict", 409) from error
    shape = _shape(base, fragment, principal, entries)
    evidence_store.assert_current(evidence)
    current_principal(principal, admin=True)
    return base, shape


def prepare_candidate(evidence_id, request, principal):
    """Read-only proposal from actual sealed v2 roots; never create a report."""
    if connection.in_atomic_block:
        _conflict("完整拟议准备须在最外层事务之外", "invalid_request", 400)
    current_principal(principal, admin=True)
    request = _request(request)
    evidence = evidence_service.get_run(identifier(evidence_id, "evidenceRunId"), principal)
    if evidence.status != "sealed" or not evidence_store.is_v2(evidence):
        _conflict("词货报告需要当前已封存v2证据")
    if (m.AiReportRun.objects.filter(pk=request["reportId"]).exists()
            or m.AiBusinessScreeningRun.objects.filter(pk=request["screeningId"]).exists()):
        _conflict("报告或筛查ID已被持久任务占用")
    entries = _catalog(transport.catalog(principal, promotion.SURFACE))
    budget = budgets.prepare(evidence, request["budgetPlan"], principal, request["reportId"]) if "budgetPlan" in request else None
    base, shape = _build(evidence, principal, request, budget, entries)
    screening.revalidate(base, principal)
    evidence_store.assert_current(evidence)
    current_principal(principal, admin=True)
    return PreparedCandidate(_TOKEN, request, evidence.id, base, entries, shape)


def revalidate_candidate(prepared, principal):
    """Reload actual roots and catalog; this still grants no write permission."""
    if type(prepared) is not PreparedCandidate or connection.in_atomic_block:
        _conflict("拟议报告准备对象或检查阶段无效", "invalid_request", 400)
    current_principal(principal, admin=True)
    request = _request(json.loads(prepared._request_json))
    old = json.loads(prepared._shape_json)
    if (old.get("ownerEmail") != principal.email.lower() or old.get("scopeJson") != canonical(principal.scope)
            or old.get("authorityVerified") is not False or old.get("registered") is not False):
        _conflict("拟议报告身份或状态已变化")
    evidence = evidence_service.get_run(prepared._evidence_id, principal)
    if evidence.status != "sealed" or not evidence_store.is_v2(evidence):
        _conflict("拟议报告证据已失去封存状态")
    if (m.AiReportRun.objects.filter(pk=request["reportId"]).exists()
            or m.AiBusinessScreeningRun.objects.filter(pk=request["screeningId"]).exists()):
        _conflict("拟议ID已被其他持久任务占用")
    screening.revalidate(prepared._base, principal)
    entries = _catalog(transport.catalog(principal, promotion.SURFACE))
    if canonical(entries) != prepared._catalog_json:
        _conflict("词货工具目录在拟议期间已变化", "tool_policy_changed")
    budget = budgets.revalidate(prepared._base.budget, principal) if prepared._base.budget is not None else None
    _, fresh = _build(evidence, principal, request, budget, entries)
    if canonical(fresh) != prepared._shape_json:
        _conflict("报告快照、工作流输入、图或来源目录在拟议期间已变化")
    return json.loads(prepared._shape_json)
