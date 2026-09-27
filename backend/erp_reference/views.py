from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .errors import ErpReferenceApiError
from .import_service import get_import_batch, import_payload, list_import_batches
from .query import execute_consumer_query, validate_consumer_request
from .revisions import revision_value
from .uploads import (
    CHUNK_SIZE_BYTES, MAX_FILE_SIZE_BYTES, execute_upload_action, read_chunk, receive_chunk,
)
from .write_requests import claim_write_request, complete_write_request, fail_write_request


logger = logging.getLogger(__name__)
JSON_CONTENT_TYPE_RE = re.compile(
    r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I
)


def _json(payload: object, status: int = 200, *, revision: str | None = None, replayed: bool = False) -> JsonResponse:
    response = JsonResponse(
        payload, status=status, safe=not isinstance(payload, list),
        json_dumps_params={"ensure_ascii": False},
    )
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Erp-Reference-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        payload: dict[str, object] = {"error": str(error), "code": error.code}
        if import_shape:
            payload.update({"ok": False, "status": "rejected", "message": str(error)})
        return _json(payload, error.status)
    if isinstance(error, ErpReferenceApiError):
        payload = {"error": str(error), "code": error.code}
        if import_shape:
            payload.update({"ok": False, "status": "rejected", "message": str(error)})
        return _json(payload, error.status)
    logger.exception("Unhandled ERP reference API error")
    payload = {"error": fallback, "code": "internal_error"}
    if import_shape:
        payload.update({"ok": False, "status": "rejected", "message": fallback})
    return _json(payload, 500)


def _principal(request: HttpRequest, roles: set[str]) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if principal.scope is not None:
        raise PrincipalEnvelopeError("ERP 主数据仅支持未受限数据范围账号", status=403, code="access_denied")
    return principal


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ErpReferenceApiError("请求 JSON 包含重复字段")
        result[key] = value
    return result


def _body(request: HttpRequest) -> dict[str, object]:
    if not JSON_CONTENT_TYPE_RE.match(request.headers.get("Content-Type", "")):
        raise ErpReferenceApiError("Django ERP 接口只接受 application/json", status=415)
    try:
        payload = json.loads(
            request.body.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ErpReferenceApiError("请求 JSON 包含非有限数字")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ErpReferenceApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise ErpReferenceApiError("请求内容必须是 JSON 对象")
    return payload


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise ErpReferenceApiError(
        "ERP 主数据版本持续变化，请稍后重试", code="service_unavailable", status=503
    )


def _replay_write(
    request: HttpRequest, principal: Principal,
    callback: Callable[[], tuple[dict[str, object], int]],
) -> JsonResponse:
    claim = claim_write_request(
        request_id=request.headers.get("X-Teruisi-Request-Id", "").strip(),
        actor_email=principal.email.strip().lower(), method=request.method, path=request.path,
        body_sha256=request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower(),
        query_sha256=hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest(),
    )
    if claim.replay_payload is not None and claim.replay_status is not None:
        return _json(
            claim.replay_payload, claim.replay_status, revision=revision_value(), replayed=True
        )
    try:
        payload, status = callback()
        complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status, revision=revision_value())
    except Exception:
        fail_write_request(claim)
        raise


def _one(request: HttpRequest, key: str) -> str | None:
    values = request.GET.getlist(key)
    if len(values) > 1:
        raise ErpReferenceApiError(f"{key} 参数不能重复")
    return values[0] if values else None


def _positive(value: str | None, fallback: int, label: str, maximum: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"[1-9]\d*", value) or int(value) > maximum:
        raise ErpReferenceApiError(f"{label} 参数无效")
    return int(value)


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            if any(key not in {"source", "batchId", "page", "pageSize", "limit"} for key in request.GET):
                raise ErpReferenceApiError("导入批次包含未知查询参数")
            source = _one(request, "source")
            if source not in {None, "products", "combos"}:
                raise ErpReferenceApiError("source 必须为 products 或 combos")
            batch_id = _one(request, "batchId")
            if batch_id and not source:
                raise ErpReferenceApiError("按精确批次查询时必须提供 source")
            page = _positive(_one(request, "page"), 1, "page", 10_000)
            page_size_value = _one(request, "pageSize")
            limit_value = _one(request, "limit")
            if page_size_value is not None and limit_value is not None:
                raise ErpReferenceApiError("pageSize 与 limit 不能同时提供")
            page_size = _positive(page_size_value or limit_value, 50, "pageSize", 100)
            if batch_id:
                exact = get_import_batch(str(source), batch_id)
                payload = {
                    "items": [exact] if exact else [],
                    "pagination": {"page": 1, "pageSize": 1, "total": 1 if exact else 0,
                                   "returned": 1 if exact else 0, "truncated": False},
                }
                return _json(payload, revision=revision_value())
            payload, revision = _consistent_read(lambda: list_import_batches(source, page, page_size))
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = import_payload(payload, principal.email.strip().lower())
            return result, 201 if result.get("status") == "imported" else 200 if result.get("ok") else 422

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "ERP 主数据导入失败", import_shape=request.method == "POST")


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        consumer_request = validate_consumer_request(_body(request))
        payload, revision = _consistent_read(
            lambda: {"operation": consumer_request["operation"],
                     "data": execute_consumer_query(principal, consumer_request)}
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取 ERP 主数据消费投影失败")


@require_POST
def uploads(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = execute_upload_action(payload, principal.email.strip().lower())
            result.setdefault("limits", {"chunkSizeBytes": CHUNK_SIZE_BYTES, "maxFileSizeBytes": MAX_FILE_SIZE_BYTES})
            return {"ok": True, "status": "ready", **result}, 200

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "ERP 分片上传处理失败", import_shape=True)


@require_http_methods(["GET", "PUT"])
def upload_chunk(request: HttpRequest) -> HttpResponse:
    try:
        principal = _principal(request, {"admin"})
        upload_id = request.headers.get("X-Upload-Id", "").strip()
        index_text = request.headers.get("X-Chunk-Index", "").strip()
        if not re.fullmatch(r"0|[1-9]\d*", index_text):
            raise ErpReferenceApiError("分片序号无效")
        index = int(index_text)
        if request.method == "GET":
            payload, digest = read_chunk(
                upload_id, index, request.headers.get("X-Upload-Owner-Token", ""),
                principal.email.strip().lower(),
            )
            response = HttpResponse(payload, content_type="application/octet-stream")
            response["Cache-Control"] = "no-store"
            response["X-Chunk-SHA256"] = digest
            return response

        def execute() -> tuple[dict[str, object], int]:
            result = receive_chunk(upload_id, index, bytes(request.body), principal.email.strip().lower())
            return {"ok": True, "status": "uploading", **result}, 200

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "ERP 分片上传失败", import_shape=request.method == "PUT")
