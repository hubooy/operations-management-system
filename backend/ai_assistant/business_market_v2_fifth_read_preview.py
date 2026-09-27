"""Unregistered fifth-tool read over a material-admitted, paused market root.

Injected job/provider IDs are a change fence and output identity only. 0053
forbids those rows, so this adapter cannot issue or claim a persisted read.
"""
import json

from django.db import DatabaseError, connection

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_fifth_read_contract as contract
from . import business_market_v2_admitted_paused as admitted
from . import business_promotion_market_admission as market_admission
from . import business_promotion_market_runtime_v2_contract as runtime
from . import business_promotion_market_tool_preview as owning
from . import models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest


SCHEMA = "business-market-v2-fifth-read-preview-v1"
MAX_RESPONSE_BYTES = 96_000
MATERIAL_FUNCTION = "public.ai_market_v2_admitted_material_metadata"


def _need(ok, message="市场v2第五工具读取与材料准入根不一致"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _roots(call, selector, principal):
    actor = current_principal(principal, admin=True)
    _need(actor.scope is None, "市场v2读取只允许无范围管理员")
    report = m.AiReportRun.objects.select_related("workflow").filter(
        pk=call["admittedReportId"]).first()
    _need(report is not None, "材料准入报告不存在")
    authorize_owner(report, principal)
    flow = report.workflow
    snapshot = json.loads(report.snapshot_json)
    _need(canonical(snapshot) == report.snapshot_json
        and snapshot.get("schemaVersion") == admitted.SNAPSHOT_SCHEMA
        and snapshot.get("executionProfile") == admitted.PROFILE
        and snapshot.get("reportId") == report.id
        and snapshot.get("registered") is False
        and snapshot.get("agentDispatchSupported") is False
        and report.owner_email == actor.email.lower()
        and report.scope_json == flow.scope_json == "null"
        and flow.status == "paused" and flow.error_code == admitted.PAUSE_REASON
        and flow.model_id == "" and flow.model_version == 0
        and flow.allowed_tools_json == "[]"
        and flow.provider_round_count == flow.tool_call_count == 0
        and not m.AiWorkflowNodeRuns.objects.filter(run_id=flow.id).exists()
        and not m.AiAgentJobs.objects.filter(workflow_run_id=flow.id).exists()
        and not m.AiAgentJobs.objects.filter(pk=call["jobId"]).exists()
        and not m.AiAgentProviderDispatches.objects.filter(
            pk=call["providerDispatchId"]).exists(),
        "材料准入报告仍须停放且不能冒用实际Agent或模型派发")
    parked_id = snapshot["marketAdmission"]["parkedReportId"]
    parked, parked_snapshot = admitted.parked_report_and_snapshot(
        parked_id, principal)
    _need(parked.owner_email == report.owner_email
        and canonical(selector) == canonical(parked_snapshot["marketSelector"]))
    source_id = parked_snapshot["sourceRoot"]["sourceReportId"]
    fixed = market_admission.require_observed(source_id, selector, principal)
    prepared = runtime.prepare(fixed, with_budget=snapshot["withBudget"])
    claim = snapshot["marketAdmission"]
    _need(claim.get("parkedReportId") == parked_id
        and claim.get("manifestDigest") == call["marketManifestDigest"]
        and prepared["marketContextDigest"] == call["marketContextDigest"]
        and prepared["marketSelector"] == selector)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM " + MATERIAL_FUNCTION + "(%s,%s,%s,%s,%s,%s)",
                [report.id, actor.email.lower(), fixed["binding"]["actorVersion"],
                 parked_id, claim["selectorDigest"], claim["manifestDigest"]])
            row = cursor.fetchone()
    except DatabaseError as error:
        raise AiError("市场材料元数据不属于当前报告或读取角色", "conflict", 409) from error
    _need(row is not None and len(row) == 6)
    metadata = {"sourceReportId": row[0], "admissionDigest": row[1],
        "selectorDigest": row[2], "manifestDigest": row[3],
        "observationCoverage": json.loads(row[4]) if type(row[4]) is str else row[4],
        "summaryDigest": row[5]}
    _need(metadata["sourceReportId"] == source_id
        and metadata["admissionDigest"] == fixed["bindingDigest"]
        and metadata["selectorDigest"] == claim["selectorDigest"]
        and metadata["manifestDigest"] == claim["manifestDigest"]
        and metadata["observationCoverage"] == {"currentDatePresent": True,
            "baselineDatePresent": True, "bothDatesPresent": True},
        "来源准入、观察日或注入的市场上下文不同")
    guard = digest([report.id, report.snapshot_json, flow.id, flow.version,
        flow.input_json, flow.status, parked.id, parked.snapshot_json,
        parked.workflow_id, metadata, fixed["bindingDigest"]])
    return source_id, fixed, prepared, metadata, guard


def read(call_identity, selector, arguments, principal, *, numeric_selection=None,
         checkpoint=None, limits=None):
    """Read one summary/page/row; preserve a non-authoritative identity claim."""
    try:
        call = contract.injected(call_identity)
    except AnalysisContractError as error:
        raise AiError("市场第五工具注入身份无效", "invalid_request", 400) from error
    source_id, fixed, prepared, metadata, guard = _roots(call, selector, principal)
    if type(arguments) is not dict or arguments.get("reportId") != call["admittedReportId"]:
        raise AiError("市场第五工具只能读取本材料准入报告", "invalid_request", 400)
    source_args = {**arguments, "reportId": source_id}
    try:
        selected = runtime.arguments(prepared, call["role"], source_args)
    except AnalysisContractError as error:
        raise AiError("市场第五工具模式、角色或分页参数无效", "invalid_request", 400) from error
    if numeric_selection is not None and (selected["mode"] != "row"
            or type(numeric_selection) is not dict
            or set(numeric_selection) != {"metric", "field"}):
        raise AiError("数值核对只能指定精确行的指标字段", "invalid_request", 400)
    preview = owning.read(source_id, selector, fixed["bindingDigest"],
        call["role"], selected, principal, checkpoint=checkpoint, limits=limits)
    _need(preview["marketManifestDigest"] == metadata["manifestDigest"]
        and preview["marketContextDigest"] == prepared["marketContextDigest"]
        and preview["serverFullMarketMaterialVerified"] is True
        and preview["agentReadPersisted"] is False
        and preview["actualAgentBound"] is False)
    payload = preview["payload"]
    citation_bases = []
    if selected["mode"] in {"page", "row"}:
        rows = (payload["rows"] if selected["mode"] == "page"
            else [payload["row"]])
        try:
            citation_bases = [contract.citation_base(call, selector, selected["view"],
                row, payload["tableBindingDigest"]) for row in rows]
        except (AnalysisContractError, KeyError, TypeError) as error:
            raise AiError("市场行候选引用身份未通过拥有方核对", "conflict", 409) from error
    number = None
    if numeric_selection is not None:
        try:
            number = contract.verified_number(citation_bases[0], payload["row"],
                numeric_selection["metric"], numeric_selection["field"],
                observation_coverage=metadata["observationCoverage"])
        except AnalysisContractError as error:
            raise AiError("市场数值缺失或不是可引用的TOP样本单元格",
                "invalid_request", 400) from error
    result = {"schemaVersion": SCHEMA, "admittedReportId": call["admittedReportId"],
        "sourceReportId": source_id, "role": call["role"],
        "jobProviderIdentityClaim": call, "identityClaimDigest": digest(call),
        "mode": selected["mode"], "marketManifestDigest": metadata["manifestDigest"],
        "payload": payload, "citationBases": citation_bases,
        "verifiedNumericCandidate": number,
        "serverFullMarketMaterialVerified": True,
        "sameJobProviderIdentityInjected": True,
        "sameJobProviderPersisted": False, "persistedRead": False,
        "registeredTool": False, "authorityVerified": False,
        "limitations": ["注入的job/provider身份不是持久派发或本人已读证明。",
            "市场TOP样本不得归属本店、ERP或B端销售；价格带汇总与成员不可相加。",
            "真实数值引用仍须在后续已注册版本中重放同job模型与工具结果。"]}
    result["resultDigest"] = digest(result)
    _need(_roots(call, selector, principal)[-1] == guard,
        "读取期间账号、报告、材料或封存来源变化")
    if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise AiError("市场第五工具候选响应超过固定容量", "payload_too_large", 413)
    return result
