"""Unregistered, bounded fifth-tool transport projection over 0056 ownership.

No central registry entry, signed route, model dispatch or persisted receipt is
created here. The future runtime must verify an actual dispatch separately.
"""
import time

from business_analysis.contracts import AnalysisContractError
from . import business_market_v2_fifth_read_preview as owning
from . import business_market_v2_transport_contract as contract
from .policy import AiError, digest


def _deadline(fence):
    try:
        fence.check()
    except AnalysisContractError as error:
        raise AiError("市场v2第五工具超过12秒读取边界，整次拒绝",
            "timeout", 504) from error


def read(surface, profile, tool_name, call_identity, arguments, principal, *,
         checkpoint=None, limits=None, clock=time.monotonic):
    """Return one all-or-nothing summary/page/row candidate under tool limits."""
    try:
        call, selected = contract.request(surface, profile, tool_name,
            call_identity, arguments)
    except AnalysisContractError as error:
        raise AiError("市场v2第五工具表面、角色或参数不属于固定合同",
            "invalid_request", 400) from error
    fence = contract.Deadline(clock)
    _deadline(fence)

    def bounded_checkpoint(event):
        _deadline(fence)
        if checkpoint is not None:
            checkpoint(event)
        _deadline(fence)

    actual = owning.read(call, selected_selector(call, principal), selected,
        principal, numeric_selection=None,
        checkpoint=bounded_checkpoint, limits=limits)
    _deadline(fence)
    if (actual.get("admittedReportId") != call["admittedReportId"]
            or actual.get("role") != call["role"]
            or actual.get("mode") != selected["mode"]
            or actual.get("marketManifestDigest") != call["marketManifestDigest"]
            or actual.get("serverFullMarketMaterialVerified") is not True
            or actual.get("sameJobProviderPersisted") is not False
            or actual.get("persistedRead") is not False
            or actual.get("registeredTool") is not False
            or actual.get("authorityVerified") is not False
            or actual.get("resultDigest") != digest({key: item for key, item
                in actual.items() if key != "resultDigest"})):
        raise AiError("市场v2拥有方读取结果与注入调用不同", "conflict", 409)
    value = {"schemaVersion": contract.RESULT_SCHEMA,
        "surface": contract.SURFACE, "profile": contract.PROFILE,
        "toolName": contract.TOOL, "reportId": call["admittedReportId"],
        "sourceReportId": actual["sourceReportId"], "role": call["role"],
        "mode": selected["mode"], "identityClaimDigest": digest(call),
        "jobIdClaim": call["jobId"],
        "providerDispatchIdClaim": call["providerDispatchId"],
        "providerCallIdClaim": call["providerCallId"],
        "marketManifestDigest": actual["marketManifestDigest"],
        "payload": actual["payload"], "citationBases": actual["citationBases"],
        "sourceResultDigest": actual["resultDigest"],
        "numericReferenceRequiredFields": ["metric", "field"],
        "serverFullMarketMaterialVerified": True,
        "sameJobProviderPersisted": False, "persistedRead": False,
        "registeredTool": False, "authorityVerified": False}
    value["resultDigest"] = digest(value)
    _deadline(fence)
    try:
        contract.result(value)
    except AnalysisContractError as error:
        raise AiError("市场v2第五工具完整响应超过38k边界，整次拒绝",
            "payload_too_large", 413) from error
    _deadline(fence)
    return value


def selected_selector(call, principal):
    """Load the immutable selector only via the authorized admitted report."""
    from . import business_market_v2_admitted_paused as admitted
    from . import models as m
    from .policy import authorize_owner, canonical, current_principal
    current_principal(principal, admin=True)
    report = m.AiReportRun.objects.filter(pk=call["admittedReportId"]).first()
    if report is None:
        raise AiError("市场v2材料准入报告不存在", "not_found", 404)
    authorize_owner(report, principal)
    import json
    snapshot = json.loads(report.snapshot_json)
    if (canonical(snapshot) != report.snapshot_json
            or snapshot.get("executionProfile") != admitted.PROFILE
            or snapshot.get("registered") is not False):
        raise AiError("市场v2材料准入报告未通过固定版本", "conflict", 409)
    parked, original = admitted.parked_report_and_snapshot(
        snapshot["marketAdmission"]["parkedReportId"], principal)
    if parked.owner_email != report.owner_email:
        raise AiError("市场v2停放根不属于当前账号", "conflict", 409)
    return original["marketSelector"]
