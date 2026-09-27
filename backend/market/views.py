from __future__ import annotations

import hashlib
import json
import logging
from time import monotonic
from collections.abc import Callable
from datetime import timedelta

from django.db import connection, transaction
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .admin import execute_master_command, execute_master_query
from .annotations import execute_annotation_command, execute_annotation_query
from .consumers import execute_consumer_query, validate_consumer_request
from .errors import MarketApiError
from .images import execute_image_command, execute_image_query
from .import_service import import_market_payload
from .models import MarketWriteRequestReceipt
from .projection import execute_projection_command
from .query import daily_coverage, filter_options, item_trend, overview
from .revisions import assert_write_authority, revision_value
from .read_health import record as record_read_health


logger = logging.getLogger(__name__)


@require_GET
def analysis_options(request: HttpRequest) -> JsonResponse:
    try:
        from .analysis_options import read_page, validate_request
        principal = _principal(request, {"admin"})
        query, cursor = validate_request(request.GET)
        result = read_page(principal, query, cursor)
        # Use the same canonical byte representation measured by the contract.
        response = JsonResponse(result, json_dumps_params={"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")})
        response["Cache-Control"] = "no-store"
        response["X-Market-Data-Revision"] = result["revision"]
        return response
    except Exception as error:
        return _error(error, "市场来源选项读取失败")

ADMIN_ANNOTATION_ACTIONS = {
    "commit",
    "commit_selected",
    "rebuild_stale_selected",
    "activate_prompt",
    "rollback_prompt",
    "delete_prompt",
    "delete_job",
    "create_agent",
    "revoke_agent",
    "mark_gold",
}


def _json(
    payload: object,
    status: int = 200,
    *,
    revision: str | None = None,
    replayed: bool = False,
) -> JsonResponse:
    response = JsonResponse(
        payload,
        status=status,
        safe=not isinstance(payload, list),
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Market-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, MarketApiError):
        if import_shape:
            return _json(
                {
                    "ok": False,
                    "status": "rejected",
                    "message": str(error),
                    "code": error.code,
                    **error.payload,
                },
                error.status,
            )
        return _json({"error": str(error), "code": error.code, **error.payload}, error.status)
    logger.exception("Unhandled market API error")
    if import_shape:
        return _json(
            {
                "ok": False,
                "status": "rejected",
                "message": fallback,
                "code": "internal_error",
            },
            500,
        )
    return _json({"error": fallback, "code": "internal_error"}, 500)


def _body(request: HttpRequest) -> dict[str, object]:
    content_type = request.headers.get("Content-Type", "")
    if not content_type.lower().startswith("application/json"):
        raise MarketApiError("Django 市场服务只接受 JSON 规范化契约", status=415)

    def strict_object(pairs):
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MarketApiError("请求 JSON 包含重复字段")
            result[key] = value
        return result

    try:
        payload = json.loads(request.body.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketApiError("请求内容不是有效 UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise MarketApiError("请求内容必须是 JSON 对象")
    return payload


def _principal(request: HttpRequest, roles: set[str] | None = None) -> Principal:
    principal = verify_principal(request)
    if roles is not None and principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="access_denied")
    if principal.scope is not None:
        raise PrincipalEnvelopeError(
            "市场分析只支持无数据范围限制的账号",
            status=403,
            code="access_denied",
        )
    return principal


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise MarketApiError(
        "市场数据版本持续变化，请稍后重试",
        code="service_unavailable",
        status=503,
    )


def _lock_request_id(request_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"market-write-receipt\n{request_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _replay_fenced_write(
    request: HttpRequest,
    principal: Principal,
    callback: Callable[[], tuple[dict[str, object], int]],
    *,
    atomic_completion: bool = False,
) -> JsonResponse:
    request_id = request.headers.get("X-Teruisi-Request-Id", "").strip()
    body_digest = request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower()
    query_digest = hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest()

    def same_binding(receipt: MarketWriteRequestReceipt) -> bool:
        return (
            receipt.body_sha256 == body_digest
            and receipt.query_sha256 == query_digest
            and receipt.method == request.method
            and receipt.path == request.path
            and receipt.actor_email == principal.email.strip().lower()
        )

    with transaction.atomic():
        _lock_request_id(request_id)
        receipt = MarketWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if receipt:
            if not same_binding(receipt):
                raise MarketApiError(
                    "内部请求标识已绑定其他市场写入",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return _json(
                    receipt.response_payload,
                    receipt.response_status,
                    revision=revision_value(),
                    replayed=True,
                )
            if receipt.created_at > timezone.now() - timedelta(minutes=5):
                raise MarketApiError("相同市场请求仍在处理中", code="conflict", status=409)
            receipt.delete()
        MarketWriteRequestReceipt.objects.create(
            request_id=request_id,
            body_sha256=body_digest,
            query_sha256=query_digest,
            method=request.method,
            path=request.path,
            actor_email=principal.email.strip().lower(),
        )

    try:
        if atomic_completion:
            with transaction.atomic():
                _lock_request_id(request_id)
                receipt = MarketWriteRequestReceipt.objects.select_for_update().filter(
                    request_id=request_id
                ).first()
                if receipt is None or not same_binding(receipt) or receipt.status != "processing":
                    raise MarketApiError(
                        "市场写入请求回执所有权已失效",
                        code="version_conflict",
                        status=409,
                    )
                payload, status = callback()
                receipt.status = "completed"
                receipt.response_status = status
                receipt.response_payload = payload
                receipt.completed_at = timezone.now()
                receipt.save()
                return _json(payload, status, revision=revision_value())
        payload, status = callback()
    except Exception:
        with transaction.atomic():
            _lock_request_id(request_id)
            MarketWriteRequestReceipt.objects.filter(
                request_id=request_id, status="processing"
            ).delete()
        raise

    with transaction.atomic():
        _lock_request_id(request_id)
        receipt = MarketWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if receipt is None or not same_binding(receipt) or receipt.status != "processing":
            raise MarketApiError(
                "市场写入请求回执所有权已失效",
                code="version_conflict",
                status=409,
            )
        receipt.status = "completed"
        receipt.response_status = status
        receipt.response_payload = payload
        receipt.completed_at = timezone.now()
        receipt.save()
        return _json(payload, status, revision=revision_value())


def _execute_query(principal: Principal, payload: dict[str, object]) -> dict[str, object]:
    operation = payload.get("operation")
    if operation == "filter_options" and set(payload) == {"operation"}:
        return {"filters": filter_options()}
    if operation == "overview":
        return overview(principal, payload)
    if operation == "trend":
        return item_trend(payload)
    if operation == "daily_coverage":
        return daily_coverage(payload)
    if operation == "master":
        return execute_master_query(payload)
    if operation == "annotations":
        return execute_annotation_query(payload, principal)
    if operation in {"image_cache_job", "image_metadata", "image_repair_candidates"}:
        return execute_image_query(payload)
    raise MarketApiError("市场查询操作无效")


@require_POST
def queries(request: HttpRequest) -> JsonResponse:
    started = monotonic()
    operation = None
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        payload = _body(request)
        operation = "ranking" if payload.get("operation") == "overview" and payload.get("view") == "ranking" else payload.get("operation")
        result, revision = _consistent_read(lambda: _execute_query(principal, payload))
        record_read_health(operation, started)
        return _json(result, revision=revision)
    except Exception as error:
        cause = error.__cause__
        if getattr(cause, "sqlstate", None) == "57014":
            record_read_health(operation, started, "query_timeout")
            label = "筛选统计" if operation == "filter_options" else "商品榜单" if operation == "ranking" else "市场查询"
            logger.warning("Market read timed out: operation=%s", operation if operation in {"ranking", "filter_options"} else "other")
            return _json({"error": f"{label}超时，请稍后重新加载；服务仍在运行。", "code": "query_timeout"}, 503)
        if not isinstance(error, (PrincipalEnvelopeError, MarketApiError)) or getattr(error, "status", 0) >= 500:
            record_read_health(operation, started, "read_failed")
        return _error(error, "市场分析数据读取失败")


@require_POST
def consumers(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        payload = validate_consumer_request(_body(request))
        result, revision = _consistent_read(
            lambda: execute_consumer_query(principal, payload)
        )
        return _json(result, revision=revision)
    except Exception as error:
        return _error(error, "市场消费查询失败")


@require_POST
def commands(request: HttpRequest) -> JsonResponse:
    try:
        preliminary = _principal(request, {"operator", "admin"})
        payload = _body(request)
        if set(payload) != {"contractVersion", "domain", "command"}:
            raise MarketApiError("市场写命令字段集合无效")
        if payload["contractVersion"] != "market-command-v1":
            raise MarketApiError("市场写命令契约版本不受支持")
        domain = payload["domain"]
        command = payload["command"]
        if domain not in {"master", "annotations", "images", "projection"} or not isinstance(command, dict):
            raise MarketApiError("市场写命令领域无效")
        action = command.get("action")
        admin_required = domain in {"master", "images", "projection"} or action in ADMIN_ANNOTATION_ACTIONS
        principal = preliminary if not admin_required else _principal(request, {"admin"})

        def execute() -> tuple[dict[str, object], int]:
            with transaction.atomic():
                assert_write_authority()
                if domain == "master":
                    result = execute_master_command(command, principal)
                elif domain == "annotations":
                    result = execute_annotation_command(command, principal)
                elif domain == "images":
                    result = execute_image_command(command, principal)
                else:
                    result = execute_projection_command(command, principal)
            return {"ok": True, "result": result}, 200

        return _replay_fenced_write(
            request,
            principal,
            execute,
            atomic_completion=True,
        )
    except Exception as error:
        return _error(error, "市场写命令执行失败")


@require_POST
def imports(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = import_market_payload(payload, principal.email.lower())
            return result, 201 if result.get("status") == "imported" else 200

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "市场数据导入失败", import_shape=True)
