"""Read-only, non-executable admission candidate for the promotion profile.

The owning reader can verify published screening pages, but the new profile
does not yet have complete Agent receipts and capacity permission. This module
validates the four-tool policy and live persisted roots while deliberately
issuing no capacity permit or dispatch authority. Callers must not treat a
candidate as a workflow lease, Agent read receipt, or approval to call a model.
"""
from dataclasses import dataclass
import json

from django.db import connection

from business_analysis import screening_package
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import models as m, transport
from .policy import AiError, canonical, current_principal, digest, identifier

SCHEMA = "business-promotion-admission-candidate-v1"
_TOKEN = object()
_KEYS = {"name", "title", "description", "inputSchema", "annotations", "risk",
         "allowedRoles", "scopePolicy", "execution"}
_BOUND_KEYS = {"schemaVersion", "reportId", "workflowId", "screeningId",
    "executionProfile", "promotionSelector", "snapshotDigest", "workflowInputDigest",
    "graphDigest", "toolCatalogDigest", "promotionCatalogDigest", "rootBindings",
    "modelId", "modelVersion", "screeningStatus", "contentReady",
    "screeningReference", "runtimeRegistered"}
_ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,160}$"}
_SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}


def _require(ok, message="词货准入当前固定根已变化", code="conflict", status=409):
    if not ok:
        raise AiError(message, code, status)


def _schema(name):
    properties = {"reportId": _ID}
    required = ["reportId"]
    if name != contract.PROMOTION_TOOL:
        properties = {"runId": _ID, "reportId": _ID, "screeningId": _ID}
        required = ["runId", "reportId", "screeningId"]
        if name == contract.PACKAGE_TOOL:
            properties["role"] = {"type": "string", "enum": list(screening_package.ROLES)}
            required.append("role")
            maximum = 9999
        elif name == contract.TABLE_TOOL:
            properties.update(mode={"type": "string", "enum": ["native", "mapped"]},
                dimension={"type": "string", "enum": ["shop", "category", "spu", "sku", "keyword", "searchTerm", "daily", "brand"]},
                sourceKey=_ID, baselineKey=_ID, pairKey=_SHA, baselinePairKey=_SHA)
            required += ["mode", "dimension"]
            maximum = 250000
        else:
            maximum = 99
        properties["offset"] = {"type": "integer", "minimum": 0, "maximum": maximum, "default": 0}
    else:
        properties.update(sourceKey=_ID,
            view={"type": "string", "enum": list(contract.PROMOTION_VIEWS)}, baselineKey=_ID,
            offset={"type": "integer", "minimum": 0, "maximum": 250000},
            limit={"type": "integer", "enum": [20]},
            rowIndex={"type": "integer", "minimum": 0, "maximum": 249999}, rowId=_SHA)
        required += ["sourceKey", "view"]
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _catalog(entries, flow):
    """Pin exact callable schemas, ordering, execution policy and flow digest."""
    try:
        _require(type(entries) is list and len(entries) == 4
            and len(canonical(entries).encode()) <= 128 * 1024)
        _require(tuple(e.get("name") if type(e) is dict else None for e in entries) == contract.TOOL_ORDER,
            "词货工具目录数量或顺序已变化", "tool_policy_changed")
        annotations = {"readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": False}
        for entry in entries:
            name = entry["name"]
            _require(set(entry) == _KEYS and entry["risk"] == "read_only"
                and entry["allowedRoles"] == ["admin"] and entry["scopePolicy"] == "unscoped_only"
                and canonical(entry["annotations"]) == canonical(annotations),
                "词货工具权限声明已变化", "tool_policy_changed")
            _require(all(type(entry[key]) is str and 0 < len(entry[key].encode()) <= 16000
                for key in ("title", "description")), "词货工具说明超出边界", "tool_policy_changed")
            _require(canonical(entry["inputSchema"]) == canonical(_schema(name)),
                "词货工具参数协议已变化", "tool_policy_changed")
            execution = {"environment": "worker_inline", "mode": "direct",
                "allowedSurfaces": [contract.SURFACE], "timeoutMs": 12000,
                "maxResultCharacters": 38000 if name == contract.PROMOTION_TOOL else 40000,
                "maxCallsPerRequest": 8}
            _require(canonical(entry["execution"]) == canonical(execution),
                "词货工具执行协议已变化", "tool_policy_changed")
        _require(flow.allowed_tools_json == canonical(list(contract.TOOL_ORDER))
            and flow.tool_policy_digest == digest(entries),
            "词货工作流固定目录与当前目录不一致", "tool_policy_changed")
        return entries
    except (TypeError, ValueError, KeyError, AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("词货中央工具目录格式无效", "tool_policy_changed", 409) from error


@dataclass(frozen=True, slots=True, init=False)
class Candidate:
    _report_id: str
    _owner_email: str
    _fixed_json: str
    _digest: str

    def __init__(self, token, report_id, owner_email, fixed):
        _require(token is _TOKEN, "准入候选只能由内部当前根建立", "invalid_request", 400)
        raw = canonical(fixed)
        _require(len(raw.encode()) <= 32768)
        for key, value in (("_report_id", report_id), ("_owner_email", owner_email),
                           ("_fixed_json", raw), ("_digest", digest([report_id, owner_email, raw]))):
            object.__setattr__(self, key, value)

    @property
    def proof(self):
        return _proof(self)


def _proof(candidate):
    _require(type(candidate) is Candidate, "公开对象不能恢复为词货准入", "invalid_request", 400)
    try:
        _require(type(candidate._fixed_json) is str and len(candidate._fixed_json.encode()) <= 32768
            and digest([candidate._report_id, candidate._owner_email, candidate._fixed_json]) == candidate._digest)
        return json.loads(candidate._fixed_json)
    except (AttributeError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("词货准入候选对象已损坏", "conflict", 409) from error


def _current(report_id, principal):
    current_principal(principal, admin=True)
    _require(principal.scope is None, "词货准入仅允许无范围管理员", "access_denied", 403)
    report_id = identifier(report_id, "reportId")
    bound = runtime.bound_persisted(report_id, principal)
    _require(type(bound) is dict and set(bound) == _BOUND_KEYS
        and bound.get("schemaVersion") == "business-promotion-persisted-binding-v1"
        and bound.get("reportId") == report_id
        and type(bound.get("workflowId")) is str
        and type(bound.get("modelId")) is str
        and type(bound.get("modelVersion")) is int,
        "词货持久绑定格式或身份不完整")
    flow = m.AiWorkflowRuns.objects.filter(pk=bound["workflowId"]).first()
    _require(flow is not None and flow.owner_email == principal.email.lower()
        and flow.status in {"queued", "running"} and not flow.cancel_requested
        and flow.dry_run == 0 and flow.model_id == bound["modelId"]
        and flow.model_version == bound["modelVersion"], "词货工作流不处于可检查的固定状态")
    _catalog(transport.catalog(principal, contract.SURFACE), flow)
    _require(bound["executionProfile"] == contract.PROFILE
        and bound["runtimeRegistered"] is False and bound["contentReady"] is False
        and bound["screeningStatus"] == "prepared_but_not_ready"
        and bound["screeningReference"] is None,
        "词货筛查内容状态超出当前准入协议")
    # Repeat the owning read after the separate flow and catalog checks.
    _require(canonical(runtime.bound_persisted(report_id, principal)) == canonical(bound),
        "词货当前来源或身份在准入检查期间已变化")
    return {"schemaVersion": SCHEMA, "reportId": report_id, "workflowId": flow.id,
        "ownerEmail": principal.email.lower(), "scopeDigest": digest(principal.scope),
        "boundDigest": digest(bound), "graphDigest": bound["graphDigest"],
        "toolCatalogDigest": bound["toolCatalogDigest"], "modelId": bound["modelId"],
        "modelVersion": bound["modelVersion"], "contentReady": False,
        "capacityVerified": False, "runtimeAdmissionGranted": False,
        "modelDispatched": False, "agentReadVerified": False}


def inspect(report_id, principal):
    """Return an internal, revalidatable candidate; never a permit."""
    _require(not connection.in_atomic_block,
        "词货准入的中央目录检查须在最外层事务之外", "invalid_request", 400)
    fixed = _current(report_id, principal)
    candidate = Candidate(_TOKEN, fixed["reportId"], fixed["ownerEmail"], fixed)
    revalidate(candidate, principal)
    return candidate


def revalidate(candidate, principal):
    proof = _proof(candidate)
    _require(not connection.in_atomic_block,
        "词货准入复验须在最外层事务之外", "invalid_request", 400)
    _require(principal.email.lower() == candidate._owner_email, "词货准入候选账号已变化", "access_denied", 403)
    _require(canonical(_current(candidate._report_id, principal)) == candidate._fixed_json)
    return proof


def require_permission(candidate, principal):
    """Explicit closed gate until complete content and capacity support land."""
    revalidate(candidate, principal)
    raise AiError("词货筛查内容与容量证明尚未具备，不能派发模型", "promotion_runtime_not_ready", 409)
