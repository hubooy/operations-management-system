from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .errors import CustomerServiceApiError
from .import_service import import_customer_service, list_import_batches, record_edge_rejection
from .query import (
    customer_search,
    delete_conversation,
    get_conversation,
    get_conversations_by_ids,
    import_batch_search,
    list_conversations,
    update_annotation,
)
from .revisions import revision_value
from .uploads import CHUNK_SIZE_BYTES, MAX_FILE_SIZE_BYTES, execute_upload_action, read_chunk, receive_chunk
from .write_requests import claim_write_request, complete_write_request, fail_write_request


logger = logging.getLogger(__name__)
JSON_CONTENT_TYPE_RE = re.compile(r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I)


def _require_process(expected: str) -> None:
    role = settings.DJANGO_PROCESS_ROLE
    if role not in {"development", expected}:
        raise CustomerServiceApiError(
            "客服请求到达了错误的 Django 进程",
            code="wrong_process_role",
            status=503,
        )


def _json(payload: object, status: int = 200, *, revision: str | None = None, replayed: bool = False) -> JsonResponse:
    response = JsonResponse(payload, status=status, safe=not isinstance(payload, list), json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Customer-Service-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        payload = {"error": str(error), "code": error.code}
        if import_shape:
            payload.update({"ok": False, "message": str(error)})
        return _json(payload, error.status)
    if isinstance(error, CustomerServiceApiError):
        payload = {"error": str(error), "code": error.code}
        if import_shape:
            payload.update({"ok": False, "message": str(error)})
        return _json(payload, error.status)
    logger.exception("Unhandled customer-service API error")
    payload = {"error": fallback, "code": "internal_error"}
    if import_shape:
        payload.update({"ok": False, "message": fallback})
    return _json(payload, 500)


def _principal(request: HttpRequest, roles: set[str]) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if principal.scope is not None:
        raise PrincipalEnvelopeError("客服接口只支持未受限数据范围账号", status=403, code="access_denied")
    return principal


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CustomerServiceApiError("请求 JSON 包含重复字段")
        result[key] = value
    return result


def _body(request: HttpRequest) -> dict[str, object]:
    if not JSON_CONTENT_TYPE_RE.match(request.headers.get("Content-Type", "")):
        raise CustomerServiceApiError("Django 客服接口只接受 application/json", status=415)
    try:
        payload = json.loads(
            request.body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(CustomerServiceApiError("请求 JSON 包含非有限数字")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CustomerServiceApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise CustomerServiceApiError("请求内容必须是 JSON 对象")
    return payload


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise CustomerServiceApiError("客服数据版本持续变化，请稍后重试", code="service_unavailable", status=503)


def _replay_write(request: HttpRequest, principal: Principal, callback: Callable[[], tuple[dict[str, object], int]]) -> JsonResponse:
    claim = claim_write_request(
        request_id=request.headers.get("X-Teruisi-Request-Id", "").strip(),
        actor_email=principal.email.strip().lower(), method=request.method, path=request.path,
        body_sha256=request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower(),
        query_sha256=hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest(),
    )
    if claim.replay_payload is not None and claim.replay_status is not None:
        return _json(claim.replay_payload, claim.replay_status, revision=revision_value(), replayed=True)
    try:
        with transaction.atomic():
            payload, status = callback()
            complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status, revision=revision_value())
    except Exception:
        fail_write_request(claim)
        raise


def _unknown(request: HttpRequest, allowed: set[str], label: str) -> None:
    if any(key not in allowed for key in request.GET):
        raise CustomerServiceApiError(f"{label}包含未知查询参数")


def _one(request: HttpRequest, key: str) -> str | None:
    values = request.GET.getlist(key)
    if len(values) > 1:
        raise CustomerServiceApiError(f"{key} 参数不能重复")
    return values[0] if values else None


def _positive(value: str | None, fallback: int, label: str, maximum: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"[1-9]\d*", value) or int(value) > maximum:
        raise CustomerServiceApiError(f"{label} 超出允许范围")
    return int(value)


def _conversation_options(request: HttpRequest) -> dict[str, object]:
    _unknown(request, {"id", "shopName", "startDate", "endDate", "agent", "status", "robotScope", "problemType", "conversionStatus", "query", "productSku", "page", "pageSize", "includeOptions"}, "客服会话列表")
    include = _one(request, "includeOptions")
    if include not in {None, "true", "false"}:
        raise CustomerServiceApiError("includeOptions 必须是 true 或 false")
    return {
        "shopNames": request.GET.getlist("shopName"), "startDate": _one(request, "startDate"),
        "endDate": _one(request, "endDate"), "agents": request.GET.getlist("agent"),
        "statuses": request.GET.getlist("status"), "robotScopes": request.GET.getlist("robotScope"),
        "problemTypes": request.GET.getlist("problemType"), "conversionStatuses": request.GET.getlist("conversionStatus"),
        "query": _one(request, "query"), "productSkus": request.GET.getlist("productSku"),
        "page": _positive(_one(request, "page"), 1, "page", 10_000),
        "pageSize": _positive(_one(request, "pageSize"), 30, "pageSize", 100),
        "includeOptions": include != "false",
    }


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _require_process("customer_service_reader")
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, {"page", "pageSize", "limit"}, "客服导入历史")
            page = _positive(_one(request, "page"), 1, "page", 10_000)
            page_size = _one(request, "pageSize")
            limit = _one(request, "limit")
            if page_size is not None and limit is not None:
                raise CustomerServiceApiError("pageSize 与 limit 不能同时提供")
            size = _positive(page_size if page_size is not None else limit, 20, "pageSize", 100)
            payload, revision = _consistent_read(lambda: list_import_batches(page=page, page_size=size))
            return _json(payload, revision=revision)
        _require_process("customer_service_writer")
        principal = _principal(request, {"admin"})
        body = _body(request)
        def execute() -> tuple[dict[str, object], int]:
            result = record_edge_rejection(body, principal.email.strip().lower()) if body.get("action") == "reject" else import_customer_service(body, principal.email.strip().lower())
            if result.get("status") == "rejected":
                return {"ok": False, **result}, 200
            status = 201 if result.get("status") == "imported" else 200
            return {"ok": True, **result}, status
        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "客服导入失败", import_shape=request.method == "POST")


@require_http_methods(["GET", "PATCH", "DELETE"])
def conversations(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _require_process("customer_service_reader")
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            detail = _one(request, "id")
            if detail is not None:
                _unknown(request, {"id"}, "客服会话详情")
                payload, revision = _consistent_read(lambda: {"item": get_conversation(_positive(detail, 0, "会话 ID", 9_007_199_254_740_991))})
                return _json(payload, revision=revision)
            options = _conversation_options(request)
            payload, revision = _consistent_read(lambda: list_conversations(options))
            return _json(payload, revision=revision)
        _require_process("customer_service_writer")
        principal = _principal(request, {"operator", "admin"} if request.method == "PATCH" else {"admin"})
        body = _body(request)
        conversation_id = body.get("id")
        expected = body.get("expectedVersion")
        if isinstance(conversation_id, bool) or not isinstance(conversation_id, int) or conversation_id <= 0 or isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
            raise CustomerServiceApiError("会话 ID 或 expectedVersion 无效")
        if request.method == "PATCH":
            if not set(body).issubset({"id", "expectedVersion", "robotScope", "problemType", "conversionStatus", "serviceIssues", "summaryText", "analysisSource"}):
                raise CustomerServiceApiError("客服标注字段集合无效")
            annotation = {key: value for key, value in body.items() if key not in {"id", "expectedVersion"}}
            return _replay_write(request, principal, lambda: ({"ok": True, **update_annotation(conversation_id, expected, annotation)}, 200))
        if set(body) != {"id", "expectedVersion", "reason"} or not isinstance(body.get("reason"), str):
            raise CustomerServiceApiError("客服删除请求字段集合无效")
        return _replay_write(request, principal, lambda: ({"ok": True, **delete_conversation(conversation_id, expected, principal.email.strip().lower(), str(body["reason"]))}, 200))
    except Exception as error:
        return _error(error, "客服会话请求失败")


@require_POST
def snapshots(request: HttpRequest) -> JsonResponse:
    try:
        _require_process("customer_service_reader")
        _principal(request, {"operator", "admin"})
        body = _body(request)
        if set(body) != {"ids"} or not isinstance(body["ids"], list) or len(body["ids"]) > 20 or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in body["ids"]):
            raise CustomerServiceApiError("会话 IDs 无效")
        ids = list(dict.fromkeys(body["ids"]))
        payload, revision = _consistent_read(lambda: {"items": get_conversations_by_ids(ids)})
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取客服分析快照失败")


@require_POST
def consumers(request: HttpRequest) -> JsonResponse:
    try:
        _require_process("customer_service_reader")
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        body = _body(request)
        operation = body.get("operation")
        if operation == "search" and set(body) == {"operation", "query", "offset", "limit", "includeMessages"}:
            loader = lambda: {"operation": operation, "data": customer_search(principal, body)}
        elif operation == "import_batch_search" and set(body) == {"operation", "query", "offset", "limit"}:
            loader = lambda: {"operation": operation, "data": import_batch_search(principal, body)}
        else:
            raise CustomerServiceApiError("客服 consumer 请求字段集合无效")
        payload, revision = _consistent_read(loader)
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取客服消费数据失败")


@require_POST
def uploads(request: HttpRequest) -> JsonResponse:
    try:
        _require_process("customer_service_writer")
        principal = _principal(request, {"admin"})
        body = _body(request)
        def execute() -> tuple[dict[str, object], int]:
            result = execute_upload_action(body, principal.email.strip().lower())
            return {"ok": True, **result, "limits": {"chunkSizeBytes": CHUNK_SIZE_BYTES, "maxFileSizeBytes": MAX_FILE_SIZE_BYTES}}, 200
        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "客服分片上传处理失败", import_shape=True)


@require_http_methods(["GET", "PUT"])
def upload_chunk(request: HttpRequest) -> HttpResponse:
    try:
        _require_process("customer_service_writer")
        principal = _principal(request, {"admin"})
        upload_id = request.headers.get("X-Upload-Id", "").strip()
        index_text = request.headers.get("X-Chunk-Index", "").strip()
        if not re.fullmatch(r"0|[1-9]\d*", index_text):
            raise CustomerServiceApiError("分片序号无效")
        index = int(index_text)
        if request.method == "GET":
            payload, digest = read_chunk(upload_id, index, request.headers.get("X-Upload-Owner-Token", ""), principal.email.strip().lower())
            response = HttpResponse(payload, content_type="application/octet-stream")
            response["Cache-Control"] = "no-store"
            response["X-Chunk-SHA256"] = digest
            return response
        if len(request.body) > CHUNK_SIZE_BYTES:
            raise CustomerServiceApiError("单个分片不能超过 1MB", code="payload_too_large", status=413)
        return _replay_write(request, principal, lambda: ({"ok": True, **receive_chunk(upload_id, index, request.body, principal.email.strip().lower())}, 200))
    except Exception as error:
        return _error(error, "客服分片上传失败", import_shape=True)
