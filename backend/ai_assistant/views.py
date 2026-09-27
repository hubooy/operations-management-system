from __future__ import annotations
import hashlib
import json
import re
from threading import BoundedSemaphore
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from sales.auth import PrincipalEnvelopeError, verify_principal
from . import (
    models as m,
    configuration,
    memory,
    chat,
    workflows,
    space,
    channels,
    sandbox,
    provider,
    transport,
    knowledge,
    datasets,
    dingtalk_settings,
    prompt_settings,
    report_library,
    reports,
    business_evidence,
    business_reports,
    dingtalk_schedules,
)
from .model_capabilities import MAX_CHAT_SECONDS
from .control_models import AiWriteReceipt, AiMutationAudit
from .policy import (
    AiError,
    canonical,
    current_principal,
    digest,
    fields,
    identifier,
    mutation,
    revision,
    authority,
    uid,
)


def response(payload, status=200, replayed=False):
    result = JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False, "separators": (",", ":")},
    )
    result["Cache-Control"] = "no-store"
    result["X-AI-Revision"] = revision()
    if replayed:
        result["X-Teruisi-Write-Replay"] = "1"
    return result


def body(request):
    if request.content_type != "application/json":
        raise AiError("请求必须使用 application/json", "invalid_request", 415)
    if len(request.body) > 1024 * 1024:
        raise AiError("请求正文超限", "payload_too_large", 413)
    try:
        result = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise AiError("请求不是有效 JSON") from e
    if not isinstance(result, dict):
        raise AiError("请求不是 JSON 对象")
    return result


def write(request, principal, handler, *, external=False, audit_only=False, commit_in_handler=False):
    request_id = request.headers["X-Teruisi-Request-Id"]
    identity = {
        "actor_email": principal.email.lower(),
        "principal_digest": digest({"role": principal.role, "scope": principal.scope}),
        "method": request.method,
        "path": request.path,
        "query_sha256": hashlib.sha256(
            request.META.get("QUERY_STRING", "").encode()
        ).hexdigest(),
        "body_sha256": request.headers["X-Teruisi-Content-SHA256"].lower(),
    }
    with mutation(principal, audit_only=audit_only):
        existing = AiWriteReceipt.objects.filter(request_id=request_id).first()
        if existing:
            if any(getattr(existing, k) != v for k, v in identity.items()):
                raise AiError("请求标识绑定已变化", "conflict", 409)
            if existing.status != "completed":
                raise AiError(
                    "请求已受理，禁止重复执行不确定操作", "request_pending", 409
                )
            return response(existing.response_payload, existing.response_status, True)
        receipt = AiWriteReceipt.objects.create(request_id=request_id, **identity)
        if not external:
            payload, status = handler()
            return finish(receipt, principal, payload, status)
    if commit_in_handler:
        def complete(payload, status):
            from django.db import connection
            if not connection.in_atomic_block:
                raise AiError("证据提交缺少事务", "conflict", 409)
            receipt = AiWriteReceipt.objects.select_for_update().get(request_id=request_id)
            if receipt.status != "processing":
                raise AiError("请求完成栅栏失效", "conflict", 409)
            return finish(receipt, principal, payload, status)
        return handler(complete)
    payload, status = handler()
    with mutation(principal, audit_only=audit_only):
        receipt = AiWriteReceipt.objects.select_for_update().get(request_id=request_id)
        if receipt.status != "processing":
            raise AiError("请求完成栅栏失效", "conflict", 409)
        return finish(receipt, principal, payload, status)


def finish(receipt, principal, payload, status):
    receipt.status = "completed"
    receipt.response_payload = payload
    receipt.response_status = status
    receipt.completed_at = timezone.now()
    receipt.save()
    AiMutationAudit.objects.create(
        request_id=receipt.request_id,
        actor_email=principal.email.lower(),
        actor_role=principal.role,
        action=receipt.method + " " + receipt.path,
        scope_digest=digest(principal.scope),
        response_digest=digest(payload),
        revision=int(revision()) + 1,
    )
    return response(payload, status)


def _dispatch(request, path=""):
    try:
        principal = verify_principal(request)
        endpoint = path.strip("/")
        routes = {
            r"business-plan/preview": {"POST"},
            r"business-reports": {"POST"},
            r"market-v2-parked-reports": {"POST"},
            r"market-v2-parked-reports/[A-Za-z0-9_-]{1,160}/preview": {"GET"},
            r"market-v2-cap-approvals/[0-9a-f]{64}": {"POST"},
            r"market-v2-cap-approvals/[0-9a-f]{64}/(?:preview|outcome)": {"GET"},
            r"market-v2-cap-approvals/[0-9a-f]{64}/revoke": {"POST"},
            r"business-files/[A-Za-z0-9_-]{1,160}": {"GET"},
            r"business-files/[A-Za-z0-9_-]{1,160}/control": {"POST"},
            r"business-files/[A-Za-z0-9_-]{1,160}/chunks/(?:html|xlsx)": {"GET"},
            r"business-files/[A-Za-z0-9_-]{1,160}/volumes/(?:0|[1-9][0-9]?|100)/chunks/(?:html|xlsx|json)": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/files": {"GET", "POST"},
            r"reports/[A-Za-z0-9_-]{1,160}/budget": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/budget-reference": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/integrated-directory": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/integrated-analysis-table": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/integrated-budget": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/screening/(?:package|analysis|budget)": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/promotion-keyword-sku": {"GET"},
            r"promotion-tool-dispatch/[A-Za-z0-9_-]{1,160}": {"POST"},
            r"market-v2-tool-candidate/[A-Za-z0-9_-]{1,160}": {"POST"},
            r"market-v2-base-tool-candidate/[A-Za-z0-9_-]{1,160}": {"POST"},
            r"reports/[A-Za-z0-9_-]{1,160}/market-dynamics": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/market-observation": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/budget-preview": {"POST"},
            r"business-evidence": {"GET", "POST"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/mapping": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/mapping-v2": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/analysis": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/budget-targets": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/budget-preview": {"POST"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/sources(?:/[A-Za-z0-9_-]{1,160})?": {"GET"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/(?:collect|finish|control)": {"POST"},
            r"business-evidence/[A-Za-z0-9_-]{1,160}/chunks/[A-Za-z0-9_-]{1,160}": {"GET"},
            r"business-v3-source-read/[A-Za-z0-9_-]{1,160}/directory": {"POST"},
            r"business-v3-source-read/[A-Za-z0-9_-]{1,160}/pages/[A-Za-z0-9_-]{1,160}": {"POST"},
            r"report-library": {"GET", "POST"},
            r"reports": {"GET", "POST"},
            r"reports/[A-Za-z0-9_-]{1,160}(?:/content)?": {"GET"},
            r"reports/[A-Za-z0-9_-]{1,160}/send": {"POST"},
            r"prompt-settings": {"GET", "POST"},
            r"dingtalk-settings": {"GET", "PATCH"},
            r"dingtalk-schedules": {"GET", "POST"},
            r"dingtalk-schedules/run": {"POST"},
            r"datasets(?:/[a-z][a-z0-9_]{0,63})?": {"GET"},
            r"datasets/[a-z][a-z0-9_]{0,63}/query": {"POST"},
            r"models|channels|space/(?:profiles|templates)": {"GET", "POST", "DELETE"},
            r"conversations": {"GET", "PATCH", "DELETE"},
            r"chat|memories|sandbox|agent-jobs|workflow-runs|space/jobs": {
                "GET",
                "POST",
            },
            r"memories/[A-Za-z0-9_-]{1,160}": {"GET", "PATCH", "DELETE"},
            r"(?:agent-jobs|workflow-runs|space/jobs)/[A-Za-z0-9_-]{1,160}": {"GET"},
            r"(?:agent-jobs|workflow-runs)/[A-Za-z0-9_-]{1,160}/(?:cancel|resume)|space/jobs/[A-Za-z0-9_-]{1,160}/cancel|chat/cancel": {
                "POST"
            },
            r"workflow-runs/[A-Za-z0-9_-]{1,160}/nodes/[A-Za-z0-9_-]{1,160}/review": {
                "POST"
            },
            r"artifacts/[A-Za-z0-9_-]{1,160}|space/(?:meta|assets)|space/assets/[A-Za-z0-9_-]{1,160}/content": {
                "GET"
            },
            r"space/assets/[A-Za-z0-9_-]{1,160}": {"PATCH"},
            r"consumer|scheduler|callback/[A-Za-z0-9_-]{1,160}": {"POST"},
        }
        methods = next(
            (
                methods
                for pattern, methods in routes.items()
                if re.fullmatch(pattern, endpoint)
            ),
            None,
        )
        if not methods:
            raise AiError("AI 接口不存在", "not_found", 404)
        if request.method not in methods:
            raise AiError("AI 接口不支持当前方法", "invalid_request", 405)
        parts = path.strip("/").split("/")
        root = parts[0]
        payload = (
            body(request)
            if request.method not in {"GET", "DELETE"} or request.body
            else {}
        )
        consumer_read = root == "consumer" and payload.get("operation") in {
            "knowledge",
            "memory-recall",
            "analysis-describe",
            "datasets-describe",
            "datasets-query",
        }
        credential_read = root == "consumer" and payload.get("operation") in {
            "model-runtime", "model-list"}
        model_admin_read = root == "models" and request.method == "GET"
        report_detail_shape = (request.method == "GET" and
            re.fullmatch(r"reports/[A-Za-z0-9_-]{1,160}", endpoint) is not None)
        # URLConf accepts a trailing slash in its generic capture. Do not let
        # strip('/') turn that alternate signed path into the writer-only GET.
        if report_detail_shape and path != endpoint:
            raise AiError("AI 接口不存在", "not_found", 404)
        report_detail_read = report_detail_shape and path == endpoint
        human_cap_path = root == "market-v2-cap-approvals"
        if human_cap_path and path != endpoint:
            raise AiError("AI 接口不存在", "not_found", 404)
        writer = (
            request.method != "GET" or root in {"artifacts"} or
            root == "reports" and parts[-1] == "content" or
            model_admin_read or report_detail_read or human_cap_path
        ) and not consumer_read and root != "datasets"
        if re.fullmatch(r"(?:reports|business-evidence)/[A-Za-z0-9_-]{1,160}/budget-preview", endpoint) and request.method == "POST":
            writer = False
        if endpoint == "business-plan/preview" and request.method == "POST":
            writer = False
        if root == "promotion-tool-dispatch" and request.method == "POST":
            writer = False
        if root == "market-v2-tool-candidate" and request.method == "POST":
            writer = False
        if root == "market-v2-base-tool-candidate" and request.method == "POST":
            writer = False
        role = settings.DJANGO_PROCESS_ROLE
        if role not in {"development", "ai_writer" if writer else "ai_reader"}:
            raise AiError("接口不属于当前读写进程", "access_denied", 403)
        authority()
        if any(len(request.GET.getlist(k)) != 1 for k in request.GET):
            raise AiError("查询参数不能重复")
        params = request.GET.dict()
        if root not in {"callback", "scheduler"}:
            principal = current_principal(
                principal, write=writer and not model_admin_read and
                    root not in {"consumer", "artifacts", "reports"}
            )
        if root in {"models", "channels", "prompt-settings", "dingtalk-settings", "dingtalk-schedules", "market-v2-cap-approvals"} or parts[:2] in [
            ["space", "profiles"],
            ["space", "templates"],
        ]:
            current_principal(principal, admin=True)
        request_id = request.headers["X-Teruisi-Request-Id"]
        if human_cap_path:
            from . import business_market_v2_human_cap_owner as cap_owner
            if params:
                raise AiError("费用审批接口不接受查询参数", "invalid_request", 400)
            ledger_id = parts[1]
            if request.method == "GET":
                return response((cap_owner.preview if parts[2] == "preview"
                    else cap_owner.outcome)(ledger_id, principal))
            if parts[-1] == "revoke":
                return write(request, principal,
                    lambda: (cap_owner.revoke(ledger_id, payload, principal), 200))
            return write(request, principal,
                lambda: (cap_owner.approve(ledger_id, payload, principal), 200))
        if root == "market-v2-parked-reports":
            if getattr(settings, "AI_MARKET_V2_PREVIEW_ENABLED", False) is not True:
                raise AiError("市场v2停放预览尚未启用", "market_v2_preview_not_ready", 409)
            current_principal(principal, admin=True, write=request.method == "POST")
            if request.method == "GET":
                from .business_market_v2_parked_preview import read as parked_preview
                return response(parked_preview(parts[1], params, principal))
            from .business_market_v2_parked_creation import create as create_parked
            fields(params, set())
            return write(request, principal,
                lambda complete: create_parked(payload, principal,
                    commit=lambda result, status: complete({**result,
                        "materialAdmitted": False,
                        "agentReadPersisted": False,
                        "actualAgentBound": False,
                        "authorityVerified": False,
                        "renderer": False,
                        "requestMaterialReplayed": True}, status)),
                external=True, commit_in_handler=True)
        if root == "promotion-tool-dispatch":
            from . import business_promotion_dispatch_tool
            fields(params, set())
            fields(payload, {"name", "arguments", "providerCallId"},
                {"name", "arguments", "providerCallId"})
            if request_id != parts[1]:
                raise AiError("词货工具派发ID与签名请求不一致", "access_denied", 403)
            return response(business_promotion_dispatch_tool.read(
                parts[1], payload["name"], payload["arguments"],
                payload["providerCallId"], principal))
        if root == "market-v2-tool-candidate":
            from . import business_market_v2_transport_candidate as market_transport
            from . import business_market_v2_transport_contract as market_contract
            if getattr(settings, "AI_MARKET_V2_AGENT_RUNTIME_ENABLED", False) is not True:
                raise AiError("市场v2第五工具候选尚未启用", "conflict", 409)
            current_principal(principal, admin=True)
            fields(params, set())
            fields(payload, {"arguments", "providerCallId"},
                {"arguments", "providerCallId"})
            if (request_id != parts[1] or type(payload["providerCallId"]) is not str
                    or not 1 <= len(payload["providerCallId"]) <= 160
                    or any(ord(char) < 32 for char in payload["providerCallId"])):
                raise AiError("市场v2工具候选签名调用身份无效", "access_denied", 403)
            args = payload["arguments"]
            if type(args) is not dict:
                raise AiError("市场v2工具候选参数必须为对象")
            fields(args, {"reportId", "marketContextDigest", "marketManifestDigest",
                "role", "mode", "view", "offset", "limit", "rowIndex", "rowId"},
                {"reportId", "marketContextDigest", "marketManifestDigest",
                    "role", "mode"})
            claim = {"schemaVersion": "business-market-v2-fifth-read-injected-call-v1",
                "admittedReportId": args["reportId"],
                "jobId": "market-preview-job-" + parts[1],
                "providerDispatchId": "market-preview-provider-" + parts[1],
                "providerCallId": payload["providerCallId"],
                "role": args["role"],
                "marketManifestDigest": args["marketManifestDigest"],
                "marketContextDigest": args["marketContextDigest"]}
            selected = {key: value for key, value in args.items()
                if key not in {"role", "marketManifestDigest"}}
            from business_analysis.contracts import AnalysisContractError
            try:
                market_contract.request(market_contract.SURFACE,
                    market_contract.PROFILE, market_contract.TOOL, claim, selected)
            except AnalysisContractError as error:
                raise AiError("市场v2工具候选模式或身份参数无效",
                    "invalid_request", 400) from error
            return response(market_transport.read(market_contract.SURFACE,
                market_contract.PROFILE, market_contract.TOOL, claim,
                selected, principal))
        if root == "market-v2-base-tool-candidate":
            from . import business_market_v2_base_tool_candidate as base_tool
            if getattr(settings, "AI_MARKET_V2_AGENT_RUNTIME_ENABLED", False) is not True:
                raise AiError("市场v2基础工具别名尚未启用", "conflict", 409)
            current_principal(principal, admin=True)
            fields(params, set())
            fields(payload, {"name", "arguments", "providerCallId"},
                {"name", "arguments", "providerCallId"})
            if (request_id != parts[1] or type(payload["providerCallId"]) is not str
                    or not 1 <= len(payload["providerCallId"]) <= 160
                    or any(ord(char) < 32 for char in payload["providerCallId"])):
                raise AiError("市场v2基础工具签名候选身份无效", "access_denied", 403)
            if type(payload["name"]) is not str or payload["name"] not in base_tool.NAMES:
                raise AiError("市场v2基础工具别名不属于独立目录", "invalid_request", 400)
            return response(base_tool.read(payload["name"], payload["arguments"],
                principal))
        if root == "business-plan":
            from .business_planning import preview
            fields(params, set())
            return response(preview(payload, principal))
        if root == "reports" and parts[-1] in {"integrated-directory", "integrated-analysis-table", "integrated-budget"}:
            from . import business_integrated_tools
            operation = {"integrated-directory":"directory", "integrated-analysis-table":"analysis", "integrated-budget":"budget"}[parts[-1]]
            return response(business_integrated_tools.read(parts[1], operation, params, principal))
        if root == "reports" and len(parts)==4 and parts[2]=="screening":
            from . import business_screening_tools
            return response(business_screening_tools.read(parts[1],parts[3],params,principal))
        if root == "reports" and len(parts)==3 and parts[2]=="promotion-keyword-sku":
            from . import business_promotion_runtime_tools
            return response(business_promotion_runtime_tools.read(parts[1],params,principal))
        if root == "reports" and len(parts)==3 and parts[2]=="market-dynamics":
            from . import business_market_runtime_tools
            return response(business_market_runtime_tools.read(parts[1],params,principal))
        if root == "reports" and len(parts)==3 and parts[2]=="market-observation":
            from . import business_market_observation_runtime_tools
            return response(business_market_observation_runtime_tools.read(parts[1],params,principal))
        if root == "reports" and parts[-1] == "budget-reference":
            from . import business_budget_store
            fields(params, {"runId", "offset", "limit"}, {"runId"})
            report = reports.get(parts[1], principal)
            snapshot = json.loads(report.snapshot_json)
            if not business_reports.is_budget_snapshot(snapshot) or params["runId"] != snapshot.get("evidenceRunId"):
                raise AiError("预算报告与固定证据不一致", "access_denied", 403)
            offset = params.get("offset", "0")
            if not re.fullmatch(r"0|[1-9][0-9]?", offset) or params.get("limit", "20") != "20":
                raise AiError("固定预算分页无效")
            return response(business_budget_store.page(report, principal, offset=int(offset), limit=20))
        if root == "reports" and parts[-1] == "budget":
            from .business_budget import read as read_budget_scenarios
            return response(read_budget_scenarios(parts[1], params, principal))
        if root == "reports" and parts[-1] == "budget-preview":
            from .business_budget import preview
            fields(params, set())
            return response(preview(parts[1], payload, principal))
        if root == "business-files" or root == "reports" and parts[-1] == "files":
            from . import business_files
            current_principal(principal, admin=True)
            if root == "reports":
                fields(params, set())
                if request.method == "GET":
                    return response(business_files.listing(parts[1], principal))
                fixed = reports.get(parts[1], principal)
                if json.loads(fixed.snapshot_json).get("executionProfile") in {
                        "business-agent-screening-reference-v1", "business-agent-screening-promotion-reference-v1"}:
                    return write(request, principal, lambda commit: business_files.create(parts[1], payload, principal, commit=commit),
                        external=True, commit_in_handler=True)
                return write(request, principal, lambda: (business_files.create(parts[1], payload, principal), 200))
            if request.method == "GET":
                if len(parts) == 6:
                    from .business_volume_files import chunk as volume_chunk
                    return response(volume_chunk(parts[1], parts[3], parts[5], params, principal))
                if len(parts) == 4:
                    return response(business_files.chunk(parts[1], parts[3], params, principal))
                fields(params, set())
                return response({"item": business_files.mapping(business_files.get(parts[1], principal))})
            fields(params, set())
            if payload.get("action") in {"resume", "rebuild"}:
                fixed = business_files.get(parts[1], principal)
                if json.loads(fixed.report.snapshot_json).get("executionProfile") in {
                        "business-agent-screening-reference-v1", "business-agent-screening-promotion-reference-v1"}:
                    return write(request, principal, lambda commit: business_files.control(parts[1], payload, principal, commit=commit),
                        external=True, commit_in_handler=True)
            return write(request, principal, lambda: (business_files.control(parts[1], payload, principal), 200))
        if root == "business-reports":
            fields(params, set())
            if "analysisMode" in payload:
                if (payload.get("analysisMode") == "screening-promotion-v1"
                        and getattr(settings, "AI_PROMOTION_AGENT_RUNTIME_ENABLED", False) is not True):
                    raise AiError("词货五角色分析尚未启用", "promotion_runtime_not_ready", 409)
                return write(request, principal, lambda commit: business_reports.create(payload, principal, commit=commit),
                    external=True, commit_in_handler=True)
            return write(request, principal, lambda: (business_reports.create(payload, principal), 200))
        if root == "business-v3-source-read":
            from . import business_v3_source_read
            fields(params, set())
            current_principal(principal, admin=True, write=True)
            if parts[2] == "directory":
                return response(business_v3_source_read.directory(parts[1], payload, principal))
            return response(business_v3_source_read.page(parts[1], parts[3], payload, principal))
        if root == "business-evidence":
            current_principal(principal, admin=True)
            if parts[-1] == "budget-preview":
                from .business_budget_builder import preview as preview_initial_budget
                fields(params, set())
                return response(preview_initial_budget(parts[1], payload, principal))
            if request.method == "GET":
                if len(parts) == 1:
                    return response(business_evidence.listing(params, principal))
                if len(parts) >= 3 and parts[2] == "sources":
                    if len(parts) == 3:
                        return response(business_evidence.directory(parts[1], params, principal))
                    fields(params, set())
                    return response(business_evidence.source_detail(parts[1], parts[3], principal))
                if parts[-1] == "analysis":
                    return response(business_evidence.analysis_table(parts[1], params, principal))
                if parts[-1] == "budget-targets":
                    from .business_budget_builder import targets as initial_budget_targets
                    return response(initial_budget_targets(parts[1], params, principal))
                if parts[-1] == "mapping":
                    return response(business_evidence.reconcile_products(parts[1], params, principal))
                if parts[-1] == "mapping-v2":
                    from .business_identity import page as product_mapping_page
                    return response(product_mapping_page(parts[1], params, principal))
                if len(parts) == 4:
                    return response(business_evidence.chunk(parts[1], parts[3], params, principal))
                fields(params, set())
                return response(business_evidence.detail(parts[1], principal))
            fields(params, set())
            if len(parts) == 1:
                return write(request, principal, lambda: (business_evidence.create(payload, principal), 200))
            if parts[-1] == "collect":
                return write(request, principal, lambda commit: business_evidence.collect(parts[1], payload, principal, request_id, commit=commit), external=True, commit_in_handler=True)
            if parts[-1] == "control":
                from .business_collection import control
                return write(request, principal, lambda: (control(parts[1], payload, principal), 200))
            return write(request, principal, lambda: (business_evidence.finish(parts[1], payload, principal), 200))
        if root == "report-library":
            if request.method == "GET":
                return response(report_library.read(principal, params))
            fields(params, set())
            return write(request, principal, lambda: (report_library.save(payload, principal), 200))
        if root == "reports":
            if request.method == "GET":
                if len(parts) == 1:
                    return response(reports.listing(params, principal))
                if parts[-1] == "content":
                    return response(reports.download(parts[1], params, principal))
                fields(params, set())
                return response(reports.detail(parts[1], principal))
            fields(params, set())
            if len(parts) == 1:
                return write(request, principal, lambda: (reports.create(payload, principal), 200))
            return write(request, principal, lambda: (reports.send(parts[1], payload, principal), 200), external=True)
        if root == "prompt-settings":
            if request.method == "GET":
                return response(prompt_settings.read(principal, params))
            fields(params, set())
            return write(request, principal, lambda: (prompt_settings.save(payload, principal), 200))
        if root == "datasets":
            if request.method == "GET":
                fields(payload, set())
                fields(params, {"page", "pageSize", "domain"} if len(parts) == 1 else set())
                return response(datasets.describe(principal, parts[1] if len(parts) == 2 else None,
                    page=int(params.get("page", "1")), page_size=int(params.get("pageSize", "20")), domain=params.get("domain")))
            fields(params, set())
            return response(datasets.query(parts[1], payload, principal, request_id))
        if root == "chat" and len(parts) == 1 and request.method == "POST":
            if "text/event-stream" in request.headers.get("Accept", ""):
                from .chat_stream import response as stream_response
                return stream_response(payload, principal, request_id)
            return response(chat.answer(payload, principal, request_id))
        if (
            root in {"agent-jobs", "workflow-runs"}
            and len(parts) == 1
            and request.method == "POST"
        ):
            result = workflows.create(payload, principal, root == "workflow-runs")
            return response(result, 200 if result["replayed"] else 201)
        if parts == ["space", "jobs"] and request.method == "POST":
            result = space.create(payload, principal)
            return response(result, 200 if result["replayed"] else 201)
        if root == "sandbox" and request.method == "POST":
            return response(sandbox.run(payload, principal, request_id), 201)
        if root == "scheduler":
            if (
                principal.email != "ai-scheduler@teruisi.internal"
                or principal.role != "operator"
                or principal.scope is not None
            ):
                raise AiError("调度身份无效", "access_denied", 403)
            fields(payload, {"queue"}, {"queue"})
            from .business_parallel import agent_queue_tick
            from .business_files import tick as file_tick
            from .business_collection import tick as collection_tick
            runner = {
                "agent": agent_queue_tick,
                "files": file_tick,
                "evidence": collection_tick,
                "workflow": workflows.workflow_tick,
                "space": space.tick,
            }.get(payload["queue"])
            if not runner:
                raise AiError("队列无效")
            return response(runner())
        if root == "callback":
            if (
                principal.email != "ai-callback@teruisi.internal"
                or principal.role != "viewer"
                or principal.scope is not None
            ):
                raise AiError("回调传输身份无效", "access_denied", 403)
            with mutation():
                return response(channels.callback(parts[1], payload))
        if credential_read:
            # Read-only credential relay belongs to the protected writer
            # process.  Keep it outside write() so it creates no mutation
            # receipt and cannot be replayed as a state-changing request.
            return response(consumer(payload, principal, request_id))
        if model_admin_read:
            # The existing administrator settings response includes a key
            # suffix and diagnostics.  It belongs to the credential process,
            # but remains a read and must not enter write-receipt handling.
            return response(read(parts, params, principal))
        if consumer_read:
            return response(consumer(payload, principal, request_id))
        if not writer:
            return response(read(parts, params, principal))
        if root == "workflow-runs" and len(parts) == 5 and parts[2] == "nodes" and parts[4] == "review":
            from .business_screening_readiness import report_for as screening_report_for
            from .business_promotion_readiness import report_for as promotion_report_for
            candidate = workflows.get(parts[1],principal,True)
            if (screening_report_for(candidate) is not None
                    or promotion_report_for(candidate) is not None):
                return write(request, principal,
                    lambda commit: workflows.review(parts[1],parts[3],payload,principal,commit=commit),
                    external=True, commit_in_handler=True)
        external = (
            root == "channels"
            and payload.get("action") in {"send", "test"}
            or root == "models"
            and payload.get("action") == "test"
            or root == "consumer"
        )
        return write(
            request,
            principal,
            lambda: mutate(
                parts, params, payload, principal, request_id, request.method
            ),
            external=external,
            audit_only=root in {"artifacts", "consumer"},
        )
    except (AiError, PrincipalEnvelopeError) as error:
        return JsonResponse(
            {"error": str(error), "code": error.code},
            status=error.status,
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, TypeError, KeyError):
        return JsonResponse(
            {"error": "AI 请求格式无效", "code": "invalid_request"},
            status=400,
            headers={"Cache-Control": "no-store"},
        )
    except Exception:
        return JsonResponse(
            {"error": "AI 服务暂时不可用", "code": "service_unavailable"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )


def read(parts, params, principal):
    root = parts[0]
    if root == "dingtalk-settings":
        fields(params, set())
        return dingtalk_settings.read(principal)
    if root == "dingtalk-schedules":
        fields(params, set())
        return dingtalk_schedules.listing(principal)
    if root == "models":
        return {
            "items": [
                configuration.model_record(r)
                for r in m.AiModels.objects.only(*configuration.MODEL_ADMIN_COLUMNS).order_by(
                    "-is_default_text_model", "-updated_at"
                )[:100]
            ],
            "principal": {
                "email": principal.email,
                "displayName": principal.display_name,
                "role": principal.role,
                "scope": principal.scope,
            },
        }
    if root == "channels":
        return {
            "items": [
                channels.mapping(r)
                for r in m.AiChannels.objects.order_by("-updated_at")[:100]
            ]
        }
    if root == "conversations":
        return chat.listing(params, principal)
    if root == "chat":
        return chat.messages(params, principal)
    if root == "memories":
        return (
            memory.listing(params, principal)
            if len(parts) == 1
            else {"item": memory.mapping(memory.get(parts[1], principal))}
        )
    if root == "sandbox":
        return sandbox.history(params, principal)
    if root in {"agent-jobs", "workflow-runs"}:
        return (
            workflows.listing(params, principal, root == "workflow-runs")
            if len(parts) == 1
            else {
                "item": workflows.mapping(
                    workflows.get(parts[1], principal, root == "workflow-runs")
                )
            }
        )
    if root == "space":
        section = parts[1]
        if section == "meta":
            return space.meta(principal)
        if section == "profiles":
            return {
                "items": [
                    configuration.profile_record(r)
                    for r in m.AiSpaceModelProfiles.objects.order_by("-updated_at")[
                        :100
                    ]
                ]
            }
        if section == "templates":
            return {
                "items": [
                    space.template_record(r)
                    for r in m.AiSpaceTemplates.objects.order_by(
                        "scene", "-is_default"
                    )[:100]
                ]
            }
        if section == "jobs":
            return (
                space.listing(params, principal)
                if len(parts) == 2
                else {
                    "item": space.job_record(
                        space.get_job(parts[2], principal), principal
                    )
                }
            )
        if section == "assets":
            if len(parts) == 4 and parts[3] == "content":
                return space.download(parts[2], principal)
            return space.listing(params, principal, assets=True)
    raise AiError("AI 接口不存在", "not_found", 404)


def mutate(parts, params, payload, principal, request_id, method):
    root = parts[0]
    if root == "dingtalk-settings":
        fields(params, set())
        return dingtalk_settings.save(payload, principal), 200
    if root == "dingtalk-schedules":
        fields(params, set())
        return (dingtalk_schedules.run_now(payload, principal) if parts[1:] == ["run"] else dingtalk_schedules.save(payload, principal)), 200
    if root == "models":
        if method == "DELETE":
            return configuration.delete_model(params, principal), 200
        if payload.get("action") == "test":
            fields(payload, {"id", "action"}, {"id", "action"})
            model = configuration.resolve_model(payload["id"])
            with mutation(principal):
                chat.dispatch_budget(principal.email.lower(), model.id)
                chat.audit(
                    principal,
                    request_id,
                    "model_probe",
                    "started",
                    arguments={"modelId": model.id},
                )
            message = provider.probe(model)
            with mutation(principal):
                m.AiModels.objects.filter(id=model.id, version=model.version).update(
                    last_test_result=message, last_tested_at=timezone.now()
                )
                chat.audit(
                    principal,
                    request_id,
                    "model_probe",
                    "succeeded",
                    result={"message": message},
                )
            return {"ok": True, "message": message}, 200
        return {"item": configuration.save_model(payload, principal)}, 200
    if root == "channels":
        if method == "DELETE":
            fields(params, {"id"}, {"id"})
            count, _ = m.AiChannels.objects.filter(id=identifier(params["id"])).delete()
            return {"ok": True, "deleted": count > 0}, 200
        if payload.get("action") in {"test", "send"}:
            return channels.send(payload, principal), 200
        return channels.save(payload, principal), 200
    if root == "conversations":
        if method == "PATCH" and payload.get("action") == "activate":
            from .conversation_workspace import activate
            return activate(payload, principal), 200
        return (
            chat.delete(params["id"], principal)
            if method == "DELETE"
            else chat.change_model(payload, principal)
        ), 200
    if root == "memories":
        if method == "DELETE":
            return memory.archive(parts[1], payload, principal, request_id), 200
        result = memory.save(
            payload, principal, request_id, parts[1] if len(parts) > 1 else None
        )
        return result, 201 if result.get("created") else 200
    if root == "chat" and parts[1:] == ["cancel"]:
        fields(payload, {"clientRequestId"}, {"clientRequestId"})
        m.AiChatRequestReceipts.objects.filter(
            owner_email=principal.email.lower(),
            client_request_id=identifier(payload["clientRequestId"]),
            status__in=["processing", "dispatched"],
        ).update(cancel_requested=True)
        return {"ok": True}, 200
    if root in {"agent-jobs", "workflow-runs"}:
        if len(parts) == 5 and parts[2] == "nodes" and parts[4] == "review":
            return workflows.review(parts[1], parts[3], payload, principal), 200
        if len(parts) == 3 and parts[2] in {"cancel", "resume"}:
            return workflows.control(
                parts[1], payload, principal, parts[2], root == "workflow-runs"
            ), 200
    if root == "artifacts":
        return chat.csv_download(parts[1], principal, request_id), 200
    if root == "space":
        section = parts[1]
        if section == "profiles":
            return (
                configuration.delete_model(params, principal, image=True)
                if method == "DELETE"
                else {"item": configuration.save_model(payload, principal, image=True)}
            ), 200
        if section == "templates":
            if method == "DELETE":
                fields(params, {"id", "expectedVersion"}, {"id", "expectedVersion"})
                row = m.AiSpaceTemplates.objects.get(id=identifier(params["id"]))
                space.cas(row, int(params["expectedVersion"]))
                if m.AiSpaceJobs.objects.filter(template_id=row.id).exists():
                    raise AiError("模板仍被历史任务引用，可停用", "conflict", 409)
                space.admin_audit(
                    principal,
                    "delete_template",
                    "template",
                    row.id,
                    space.template_record(row),
                    None,
                )
                row.delete()
                return {"ok": True, "deleted": True}, 200
            return space.save_template(payload, principal), 200
        if section == "jobs" and len(parts) == 4 and parts[3] == "cancel":
            fields(payload, set())
            return space.cancel(parts[2], principal), 200
        if section == "assets" and len(parts) == 3:
            fields(payload, {"favorite"}, {"favorite"})
            row = space.get_asset(parts[2], principal)
            value = space.boolean(payload["favorite"], "favorite")
            if value:
                m.AiSpaceAssetFavorites.objects.get_or_create(
                    asset_id=row.id, actor_email=principal.email.lower()
                )
            else:
                m.AiSpaceAssetFavorites.objects.filter(
                    asset_id=row.id, actor_email=principal.email.lower()
                ).delete()
            return {"item": space.asset_record(row, principal)}, 200
    if root == "consumer":
        return consumer(payload, principal, request_id), 200
    raise AiError("AI 接口不存在", "not_found", 404)


def consumer(payload, principal, request_id):
    operation = payload.get("operation")
    if operation == "pandas-analysis":
        from .pandas_sandbox import run
        return run(payload, principal, request_id)
    if operation == "tool-audit":
        fields(payload, {"operation", "entry"}, {"operation", "entry"})
        entry = payload["entry"]
        fields(
            entry,
            {
                "requestId",
                "invocationId",
                "providerCallId",
                "actorEmail",
                "actorRole",
                "surface",
                "toolName",
                "arguments",
                "status",
                "durationMs",
                "result",
                "errorCode",
            },
            {
                "requestId",
                "actorEmail",
                "actorRole",
                "surface",
                "toolName",
                "status",
                "durationMs",
            },
        )
        if (
            entry["actorEmail"].lower() != principal.email.lower()
            or entry["actorRole"] != principal.role
        ):
            raise AiError("审计身份不匹配", "access_denied", 403)
        if entry["status"] not in {"started", "succeeded", "failed"}:
            raise AiError("审计状态无效")
        with mutation():
            chat.audit(
                principal,
                entry["requestId"],
                entry["toolName"],
                entry["status"],
                arguments=entry.get("arguments"),
                result=entry.get("result"),
                invocation_id=entry.get("invocationId", ""),
                provider_call_id=entry.get("providerCallId"),
                error_code=entry.get("errorCode"),
                duration=entry["durationMs"],
                surface=entry["surface"],
            )
        return {"ok": True}
    if operation in {"model-runtime", "model-list"}:
        fields(payload, {"operation", "id", "modelType", "allowFallback"})
        if operation == "model-list":
            query = m.AiModels.objects.only(
                *configuration.MODEL_ADMIN_COLUMNS).filter(status="enabled")
            if payload.get("modelType") == "vision":
                query = query.filter(model_type__in=["vision", "image"])
            elif payload.get("modelType"):
                query = query.filter(model_type=payload["modelType"])
            return {
                "items": [
                    configuration.model_record(r)
                    for r in query.order_by("-is_default_text_model", "-updated_at")[
                        :100
                    ]
                ]
            }
        if payload.get("id"):
            query = m.AiModels.objects.filter(
                id=identifier(payload["id"]), status="enabled"
            )
            if payload.get("modelType") == "vision":
                query = query.filter(model_type__in=["vision", "image"])
            elif payload.get("modelType"):
                query = query.filter(model_type=payload["modelType"])
            model = query.first()
            if not model:
                raise AiError("模型不存在或未启用", "not_found", 404)
        else:
            model = configuration.resolve_model()
        return {
            "model": {
                field.attname: getattr(model, field.attname)
                for field in model._meta.concrete_fields
                if field.attname not in {"created_at", "updated_at", "last_tested_at"}
            }
        }
    if operation == "analysis-reply":
        fields(
            payload,
            {"operation", "modelId", "prompt", "systemPrompt", "surface", "title"},
            {"operation", "prompt", "systemPrompt"},
        )
        model = configuration.resolve_model(payload.get("modelId"))
        prompt = configuration.text(payload["prompt"], "prompt", 48000)
        system = configuration.text(payload["systemPrompt"], "systemPrompt", 16000)
        with mutation(principal):
            chat.dispatch_budget(principal.email.lower(), model.id)
            chat.audit(
                principal,
                request_id,
                "configured_analysis",
                "started",
                arguments={"modelId": model.id, "promptDigest": digest(prompt)},
            )
        result = provider.turn(model, [{"role": "user", "content": prompt}], system, [])
        reply = configuration.text(result["text"], "模型回复", 48000)
        response_payload = {"reply": reply}
        with mutation(principal):
            chat.audit(
                principal,
                request_id,
                "configured_analysis",
                "succeeded",
                result={"replyDigest": digest(reply)},
            )
            if payload.get("title"):
                conv = m.AiConversations.objects.create(
                    id=uid("ai-conversation"),
                    title=configuration.text(payload["title"], "title", 120),
                    model_id=model.id,
                    created_by=principal.email.lower(),
                )
                m.AiConversationScopes.objects.create(
                    conversation_id=conv.id, scope_json=canonical(principal.scope)
                )
                chat.append(conv.id, "user", prompt)
                chat.append(conv.id, "assistant", reply)
                response_payload["conversationId"] = conv.id
        return response_payload
    if operation == "analysis-plan":
        return sandbox.run(payload["input"], principal, request_id)
    if operation in {"datasets-describe", "datasets-query"}:
        return datasets.consumer(payload, principal, request_id)
    if operation == "memory-recall":
        fields(payload, {"operation", "query"}, {"operation", "query"})
        return memory.recall(
            configuration.text(payload["query"], "query", 200), principal
        )
    if operation == "analysis-describe":
        fields(payload, {"operation"}, {"operation"})
        return sandbox.describe()
    if operation == "knowledge":
        fields(payload, {"operation", "query", "limit"})
        return knowledge.search(
            payload.get("query"), principal, payload.get("limit", 4)
        )
    raise AiError("AI consumer 操作不在白名单")


_primary_slots = BoundedSemaphore(2)
_consumer_slots = BoundedSemaphore(2)
_dataset_slots = BoundedSemaphore(2)


def dispatch(request, path=""):
    # Six writer threads: at most two blocking primary requests + two nested
    # consumers. Two threads remain available for tool audits and cancellation.
    gate = None
    dataset_request = path == "datasets" or path.startswith("datasets/")
    if request.method != "GET" and not path.endswith("/cancel"):
        if path == "consumer":
            try:
                operation = body(request).get("operation")
            except AiError:
                operation = None
            dataset_request = operation in {"datasets-describe", "datasets-query"}
            if operation in {"analysis-reply", "analysis-plan", "pandas-analysis"}:
                gate = _consumer_slots
        else:
            gate = _primary_slots
    if dataset_request:
        gate = _dataset_slots
    if gate and not gate.acquire(blocking=False):
        return JsonResponse(
            {"error": "AI 执行繁忙，请稍后重试", "code": "rate_limited"},
            status=429,
            headers={"Cache-Control": "no-store"},
        )
    try:
        with transport.request_budget(
            28 if dataset_request else MAX_CHAT_SECONDS if path == "chat" else 610 if path == "models" else 195 if path == "scheduler" else 120
        ):
            result = _dispatch(request, path)
        if result.status_code < 400:
            result["X-AI-Revision"] = revision()
        if getattr(result, "ai_stream", None) is not None and gate:
            # Transfer the existing permit to the producer. Returning headers or
            # closing the browser must not admit extra concurrent provider calls.
            result.ai_stream.release = gate.release
            gate = None
        return result
    finally:
        if gate:
            gate.release()
