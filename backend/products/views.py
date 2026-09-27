from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .consumers import execute_consumer_query, validate_consumer_request
from .errors import ProductsApiError
from .import_service import import_product_payload, list_import_batches
from .projection import execute_projection_action
from .query import product_summary
from .revisions import revision_value
from .uploads import (
    CHUNK_SIZE_BYTES,
    MAX_FILE_SIZE_BYTES,
    execute_upload_action,
    read_chunk,
    receive_chunk,
)
from .write_requests import claim_write_request, complete_write_request, fail_write_request


logger = logging.getLogger(__name__)
JSON_CONTENT_TYPE_RE = re.compile(
    r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I
)


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
        response["X-Product-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, ProductsApiError):
        if import_shape:
            return _json(
                {"ok": False, "status": "rejected", "message": str(error), "code": error.code},
                error.status,
            )
        return _json({"error": str(error), "code": error.code}, error.status)
    logger.exception("Unhandled products API error")
    if import_shape:
        return _json(
            {"ok": False, "status": "rejected", "message": fallback, "code": "internal_error"},
            500,
        )
    return _json({"error": fallback, "code": "internal_error"}, 500)


def _principal(request: HttpRequest, roles: set[str] | None = None, *, unscoped: bool = True) -> Principal:
    principal = verify_principal(request)
    if roles is not None and principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if unscoped and principal.scope is not None:
        raise PrincipalEnvelopeError(
            "商品经营接口仅支持未受限数据范围账号", status=403, code="access_denied"
        )
    return principal


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProductsApiError("请求 JSON 包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ProductsApiError("请求 JSON 包含非有限数字")


def _body(request: HttpRequest) -> dict[str, object]:
    if not JSON_CONTENT_TYPE_RE.match(request.headers.get("Content-Type", "")):
        raise ProductsApiError("Django 商品经营接口只接受 application/json", status=415)
    try:
        payload = json.loads(
            request.body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProductsApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise ProductsApiError("请求内容必须是 JSON 对象")
    return payload


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise ProductsApiError(
        "商品经营数据版本持续变化，请稍后重试",
        code="service_unavailable",
        status=503,
    )


def _replay_write(
    request: HttpRequest,
    principal: Principal,
    callback: Callable[[], tuple[dict[str, object], int]],
) -> JsonResponse:
    claim = claim_write_request(
        request_id=request.headers.get("X-Teruisi-Request-Id", "").strip(),
        actor_email=principal.email.strip().lower(),
        method=request.method,
        path=request.path,
        body_sha256=request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower(),
        query_sha256=hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest(),
    )
    if claim.replay_payload is not None and claim.replay_status is not None:
        return _json(claim.replay_payload, claim.replay_status, replayed=True)
    try:
        payload, status = callback()
        complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status, revision=revision_value())
    except Exception:
        fail_write_request(claim)
        raise


def _one(request: HttpRequest, key: str, *, required: bool = False) -> str | None:
    values = request.GET.getlist(key)
    if len(values) > 1:
        raise ProductsApiError(f"{key} 参数不能重复")
    if not values:
        if required:
            raise ProductsApiError(f"缺少 {key} 参数")
        return None
    return values[0]


def _positive(value: str | None, fallback: int, label: str, maximum: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"[1-9]\d*", value):
        raise ProductsApiError(f"{label} 必须是十进制正整数")
    parsed = int(value)
    if parsed > maximum:
        raise ProductsApiError(f"{label} 超出允许范围")
    return parsed


@require_GET
def summary(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        allowed = {
            "range", "startDate", "endDate", "days", "platform", "shop", "page", "pageSize",
            "q", "category", "marginBand", "sortBy", "direction", "view", "snapshotToken",
        }
        if any(key not in allowed for key in request.GET):
            raise ProductsApiError("商品汇总包含未知查询参数")
        view = _one(request, "view")
        if view not in {None, "page"}:
            raise ProductsApiError("view 必须是 page")
        options: dict[str, object] = {
            "platforms": request.GET.getlist("platform"),
            "shopKeys": request.GET.getlist("shop"),
            "page": _positive(_one(request, "page"), 1, "page", 10_000),
            "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100),
            "query": _one(request, "q") or "",
            "categories": request.GET.getlist("category"),
            "marginBands": request.GET.getlist("marginBand"),
            "projection": "page" if view == "page" else "full",
        }
        for query_name, option_name in {
            "range": "range", "startDate": "startDate", "endDate": "endDate",
            "sortBy": "sortBy", "direction": "direction", "snapshotToken": "expectedSnapshotToken",
        }.items():
            value = _one(request, query_name)
            if value is not None:
                options[option_name] = value
        days = _one(request, "days")
        if days is not None:
            options["days"] = _positive(days, 30, "days", 365)
        return _json(product_summary(principal, options), revision=revision_value())
    except Exception as error:
        return _error(error, "读取商品与毛利数据失败")


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            if any(key not in {"page", "pageSize", "limit"} for key in request.GET):
                raise ProductsApiError("导入批次包含未知查询参数")
            page = _positive(_one(request, "page"), 1, "page", 10_000)
            page_size_value = _one(request, "pageSize")
            limit_value = _one(request, "limit")
            if page_size_value is not None and limit_value is not None:
                raise ProductsApiError("pageSize 与 limit 不能同时提供")
            page_size = _positive(page_size_value or limit_value, 50, "pageSize", 100)
            payload, revision = _consistent_read(lambda: list_import_batches(page, page_size))
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = import_product_payload(payload, principal.email.strip().lower())
            return result, 201 if result.get("status") == "imported" else 200 if result.get("ok") else 422

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "SKU 快递费率导入失败", import_shape=request.method == "POST")


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        consumer_request = validate_consumer_request(_body(request))
        payload, revision = _consistent_read(
            lambda: {
                "operation": consumer_request["operation"],
                "data": execute_consumer_query(principal, consumer_request),
            }
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取商品经营消费数据失败")


@require_POST
def uploads(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = execute_upload_action(payload, principal.email.strip().lower())
            result.setdefault(
                "limits", {"chunkSizeBytes": CHUNK_SIZE_BYTES, "maxFileSizeBytes": MAX_FILE_SIZE_BYTES}
            )
            return {"ok": True, "status": "ready", **result}, 200

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "SKU 快递费率分片上传处理失败", import_shape=True)


@require_http_methods(["GET", "PUT"])
def upload_chunk(request: HttpRequest) -> HttpResponse:
    try:
        principal = _principal(request, {"admin"})
        upload_id = request.headers.get("X-Upload-Id", "").strip()
        index_text = request.headers.get("X-Chunk-Index", "").strip()
        if not re.fullmatch(r"0|[1-9]\d*", index_text):
            raise ProductsApiError("分片序号无效")
        index = int(index_text)
        if request.method == "GET":
            payload, digest = read_chunk(
                upload_id,
                index,
                request.headers.get("X-Upload-Owner-Token", ""),
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
        return _error(error, "SKU 快递费率分片上传失败", import_shape=request.method == "PUT")


@require_POST
def inventory_projection(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            return execute_projection_action(payload, principal.email.strip().lower()), 200

        return _replay_write(request, principal, execute)
    except Exception as error:
        return _error(error, "同步商品库存投影失败")
