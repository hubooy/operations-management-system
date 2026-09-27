"""Four unregistered market-surface aliases over the original sealed v1 report.

The admitted market report points to a parked root and the same immutable 0045
material. No Agent job or provider/tool dispatch is created or inferred here.
"""
import json
import re
import time

from django.db import DatabaseError, connection

from . import business_market_v2_admitted_paused as admitted
from . import business_promotion_market_admission as market_admission
from . import business_promotion_runtime_contract as old_profile
from . import business_promotion_tools as promotion_reader
from . import business_promotion_budget as budget_reader
from . import business_promotion_runtime_tools as keyword_reader
from . import business_screening_packages as packages
from . import business_screening_runtime_contract as screening_policy
from . import business_evidence as evidence_service
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, identifier


SCHEMA = "business-market-v2-base-tool-candidate-v1"
SURFACE = "business_agent_screening_promotion_market_v2"
PROFILE = admitted.PROFILE
NAMES = {
    "get_business_market_v2_screening_package": "package",
    "get_business_market_v2_screening_analysis": "analysis",
    "get_business_market_v2_screening_budget": "budget",
    "get_business_market_v2_keyword_sku": "keyword",
}
ORDER = tuple(NAMES)
CAPACITY = {name: 38_000 if operation == "keyword" else 40_000
    for name, operation in NAMES.items()}
LIMIT_MS = 12_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _need(ok, message="市场v2基础工具候选与同报告来源不一致", *,
          code="conflict", status=409):
    if not ok:
        raise AiError(message, code, status)


def _identity(report_id, principal):
    actor = current_principal(principal, admin=True)
    _need(actor.scope is None)
    report = m.AiReportRun.objects.select_related("workflow").filter(
        pk=identifier(report_id, "reportId")).first()
    _need(report is not None, "材料准入报告不存在")
    authorize_owner(report, principal)
    snapshot = json.loads(report.snapshot_json)
    flow = report.workflow
    _need(canonical(snapshot) == report.snapshot_json
        and snapshot.get("executionProfile") == PROFILE
        and snapshot.get("schemaVersion") == admitted.SNAPSHOT_SCHEMA
        and snapshot.get("registered") is False
        and snapshot.get("agentDispatchSupported") is False
        and report.owner_email == actor.email.lower()
        and report.scope_json == flow.scope_json == "null"
        and flow.status == "paused" and flow.error_code == admitted.PAUSE_REASON
        and flow.model_id == "" and flow.allowed_tools_json == "[]"
        and flow.provider_round_count == flow.tool_call_count == 0
        and not m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id).exists()
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists())
    parked_id = snapshot["marketAdmission"]["parkedReportId"]
    parked, parked_snapshot = admitted.parked_report_and_snapshot(parked_id,
        principal)
    _need(parked.owner_email == report.owner_email)
    source_id = parked_snapshot["sourceRoot"]["sourceReportId"]
    selector = parked_snapshot["marketSelector"]
    fixed = market_admission.require_observed(source_id, selector, principal)
    claim = snapshot["marketAdmission"]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM public.ai_market_v2_admitted_material_metadata"
                "(%s,%s,%s,%s,%s,%s)", [report.id, actor.email.lower(),
                    fixed["binding"]["actorVersion"], parked_id,
                    claim["selectorDigest"], claim["manifestDigest"]])
            row = cursor.fetchone()
    except DatabaseError as error:
        raise AiError("市场材料元数据未通过0056窄权限核验", "conflict", 409) from error
    _need(row is not None and len(row) == 6
        and row[0] == source_id and row[1] == fixed["bindingDigest"]
        and row[2] == claim["selectorDigest"]
        and row[3] == claim["manifestDigest"])
    source = m.AiReportRun.objects.select_related("workflow").filter(pk=source_id).first()
    _need(source is not None and source.owner_email == report.owner_email)
    authorize_owner(source, principal)
    source_snapshot = json.loads(source.snapshot_json)
    _need(source_snapshot.get("executionProfile") == old_profile.PROFILE
        and source_snapshot.get("reportId") == source_id
        and bool(source.budget_plan_id) == parked_snapshot["withBudget"]
        and source_snapshot.get("evidenceRunId") == parked_snapshot["sourceRoot"]["evidenceRunId"]
        and source_snapshot.get("sealedDigest") == parked_snapshot["sourceRoot"]["sealedDigest"])
    guard = digest([report.id, report.snapshot_json, flow.id, flow.version,
        flow.input_json, parked.id, parked.snapshot_json, source.snapshot_json,
        source.workflow.input_json, row, fixed["bindingDigest"]])
    return source, source_snapshot, json.loads(source.workflow.input_json), claim, guard


def _args(name, args):
    _need(type(name) is str and name in NAMES and type(args) is dict,
        "市场v2基础工具名称或参数对象无效", code="invalid_request", status=400)
    operation = NAMES[name]
    role = args.get("role")
    _need(type(role) is str and role in screening_policy.ROLES,
        "市场v2基础工具角色无效", code="invalid_request", status=400)
    if operation == "budget":
        _need(role in screening_policy.BUDGET_NODES,
            "本角色不能读取固定预算", code="access_denied", status=403)
    if operation == "keyword":
        _need(role in old_profile.PROMOTION_ROLES,
            "本角色不能读取词货视图", code="access_denied", status=403)
    base = {"reportId", "role"}
    if operation == "package":
        allowed, required = base | {"runId", "screeningId", "offset"}, base | {"runId", "screeningId"}
    elif operation == "budget":
        allowed, required = base | {"runId", "screeningId", "offset"}, base | {"runId", "screeningId"}
    elif operation == "analysis":
        allowed = base | {"runId", "screeningId", "mode", "dimension",
            "sourceKey", "baselineKey", "pairKey", "baselinePairKey", "offset"}
        required = base | {"runId", "screeningId", "mode", "dimension"}
    else:
        allowed = base | {"sourceKey", "view", "baselineKey", "offset", "limit",
            "rowIndex", "rowId"}
        required = base | {"sourceKey", "view"}
    _need(required <= args.keys() and not args.keys()-allowed,
        "市场v2基础工具字段集合无效", code="invalid_request", status=400)
    identity_keys = ("reportId",) if operation == "keyword" else (
        "reportId", "runId", "screeningId")
    for key in identity_keys:
        identifier(args[key], key)
    maximum = 9999 if operation == "package" else 99 if operation == "budget" else 250000
    if "offset" in args:
        _need(type(args["offset"]) is int and 0 <= args["offset"] <= maximum,
            "市场v2基础工具偏移无效", code="invalid_request", status=400)
    if operation == "analysis":
        mode = args["mode"]
        _need(type(args["dimension"]) is str)
        if mode == "native":
            _need("sourceKey" in args and not {"pairKey", "baselinePairKey"} & args.keys())
            identifier(args["sourceKey"])
            if "baselineKey" in args: identifier(args["baselineKey"])
        elif mode == "mapped":
            _need("pairKey" in args and not {"sourceKey", "baselineKey"} & args.keys())
            _need(type(args["pairKey"]) is str and _SHA.fullmatch(args["pairKey"]))
            if "baselinePairKey" in args:
                _need(type(args["baselinePairKey"]) is str and _SHA.fullmatch(args["baselinePairKey"]))
        else: _need(False)
    if operation == "keyword":
        identifier(args["sourceKey"])
        if "baselineKey" in args: identifier(args["baselineKey"])
        _need(args["view"] in old_profile.PROMOTION_VIEWS)
        row = "rowIndex" in args or "rowId" in args
        if row:
            _need({"rowIndex", "rowId"} <= args.keys()
                and not {"offset", "limit"} & args.keys()
                and type(args["rowIndex"]) is int
                and 0 <= args["rowIndex"] < 250000
                and type(args["rowId"]) is str and _SHA.fullmatch(args["rowId"]))
        else:
            _need(type(args.get("limit", 20)) is int and args.get("limit", 20) == 20)
    _need(len(canonical(args).encode("utf-8")) <= 8192,
        "市场v2基础工具参数超出容量", code="payload_too_large", status=413)
    return operation, role


def read(name, args, principal, *, checkpoint=None, clock=time.monotonic):
    operation, role = _args(name, args)
    started = clock()
    def deadline():
        _need(0 <= (clock()-started)*1000 < LIMIT_MS,
            "市场v2基础工具超过12秒边界，整次拒绝", code="timeout", status=504)
    def checkpoint(_event):
        deadline()
    deadline()
    report_id = identifier(args["reportId"], "reportId")
    source, source_snapshot, source_reference, claim, guard = _identity(
        report_id, principal)
    source_id = source.id
    deadline()
    if operation != "keyword":
        _need(args["runId"] == source_snapshot["evidenceRunId"]
            and args["screeningId"] == source_snapshot["screeningIntent"]["id"])
    if operation == "budget" and m.AiReportRun.objects.get(pk=source_id).budget_plan_id is None:
        payload, status = None, "unavailable_no_fixed_budget"
    elif operation == "keyword":
        params = {key: str(value) if type(value) is int else value
            for key,value in args.items() if key not in {"reportId", "role"}}
        payload = keyword_reader.read(source_id, params, principal,
            checkpoint=checkpoint)
        status = "available"
    else:
        params = {key: value
            for key,value in args.items() if key not in {"reportId", "role"}}
        offset = params.get("offset", 0)
        if operation == "package":
            ready = packages.prepare(source_snapshot["screeningIntent"]["id"],
                principal)
            payload = packages.page(ready, role, principal, offset=offset)
        elif operation == "budget":
            payload = budget_reader.read_page(source_id, principal, offset=offset)
        else:
            evidence = evidence_service.get_run(source_snapshot["evidenceRunId"],
                principal)
            payload = promotion_reader._analysis({**params,
                "reportId": source_id, "offset": offset}, source,
                source_snapshot, source_reference, evidence, principal,
                checkpoint=checkpoint)
        status = "available"
    deadline()
    _need(_identity(report_id, principal)[-1] == guard,
        "市场v2基础工具读取期间报告或材料变化")
    deadline()
    result = {"schemaVersion": SCHEMA, "toolName": name,
        "reportId": report_id, "sourceReportId": source_id, "roleClaim": role,
        "status": status, "marketManifestDigest": claim["manifestDigest"],
        "payload": payload, "sourceResultDigest": digest(payload) if payload is not None else None,
        "serverSourceVerified": True, "sameJobProviderPersisted": False,
        "persistedRead": False, "registeredAgentTool": False,
        "authorityVerified": False}
    result["resultDigest"] = digest(result)
    raw = canonical({"ok": True, "toolName": name, "data": result})
    _need(len(raw.encode("utf-8")) <= CAPACITY[name]
        and len(raw.encode("utf-16-le"))//2 <= CAPACITY[name],
        "市场v2基础工具完整响应超出固定容量", code="payload_too_large", status=413)
    deadline()
    return result
