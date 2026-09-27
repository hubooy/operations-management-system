from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .errors import NetshopApiError
from .asset_uploads import execute_asset_upload_action
from .consumers import execute_consumer_query, validate_consumer_request
from .import_service import import_netshop_payload, list_import_batches
from .models import NetshopPromotionShopDaily, NetshopWriteRequestReceipt
from .promotion_diagnostic import SHOP_NAME as PROMOTION_DIAGNOSTIC_SHOP, read_promotion_diagnostic
from .query import (
    MAX_DAYS,
    MAX_PAGE,
    MAX_PAGE_SIZE,
    MAX_PROMOTION_PAGE_SIZE,
    normalize_platforms,
    overview as read_overview,
    parse_outlets,
    period,
    positive,
    product_catalog,
    product_image_metadata,
    product_performance as read_product_performance,
    promotion_items as read_promotion_items,
    promotion_overview as read_promotion_overview,
    promotion_performance as read_promotion_performance,
    revision_value,
)


logger = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = ("京东", "天猫")


@require_GET
def analysis_options(request: HttpRequest) -> JsonResponse:
    from .analysis_options import read_page, validate_request
    try:
        principal = _principal(request, {"admin"})
        query, cursor = validate_request(request.GET)
        payload = read_page(principal, query, cursor)
        return _json(payload, revision=payload["revision"])
    except Exception as error:
        return _error(error, "经营分析来源选项读取失败")


@require_GET
def analysis_records(request: HttpRequest) -> JsonResponse:
    from .analysis import read_page, validate_request
    try:
        principal = _principal(request, {"admin"})
        if principal.scope is not None:
            raise NetshopApiError("第一阶段分析明细仅向无范围限制管理员开放", code="access_denied", status=403)
        spec, limit, cursor = validate_request(request.GET)
        payload = read_page(spec, limit, cursor)
        return _json(payload, revision=payload["sourceRevision"])
    except Exception as error:
        return _error(error, "经营分析数据读取失败")


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
        response["X-Netshop-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _principal(request: HttpRequest, roles: set[str] | None = None) -> Principal:
    principal = verify_principal(request)
    if roles is not None and principal.role not in roles:
        raise PrincipalEnvelopeError(
            "当前角色无权访问", status=403, code="insufficient_role"
        )
    return principal


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, NetshopApiError):
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
    logger.exception("Unhandled netshop API error")
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
        raise NetshopApiError("Django 网店写入只接受 JSON 规范化契约", status=415)
    try:
        def strict_object(pairs):
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise NetshopApiError("请求 JSON 包含重复字段")
                result[key] = value
            return result

        payload = json.loads(request.body.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NetshopApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise NetshopApiError("请求内容必须是 JSON 对象")
    return payload


def _platforms(principal: Principal, requested: Sequence[str]) -> list[str]:
    normalized = normalize_platforms(requested)
    if principal.scope is None:
        return normalized
    allowed = {
        value.strip()
        for value in principal.scope["platforms"]
        if value.strip() in SUPPORTED_PLATFORMS
    }
    if not allowed:
        raise NetshopApiError("当前账号没有可读取的网店平台范围", code="access_denied", status=403)
    if any(item not in allowed for item in normalized):
        raise NetshopApiError("请求包含当前账号无权读取的网店平台", code="access_denied", status=403)
    return normalized or sorted(allowed)


def _outlets(request: HttpRequest, platforms: Sequence[str]) -> list[dict[str, str]]:
    if request.GET.getlist("shop"):
        raise NetshopApiError("店铺筛选必须使用 outlet 平台与店铺复合键")
    return parse_outlets(request.GET.getlist("outlet"), platforms)


def _single(values: Sequence[str], fallback: str, allowed: set[str], label: str) -> str:
    if not values:
        return fallback
    if len(values) != 1 or values[0] not in allowed:
        raise NetshopApiError(f"{label} 参数无效或重复")
    return values[0]


def _snapshot(values: Sequence[str], *, required: bool, allowed: bool = True) -> str | None:
    if not values:
        if required:
            raise NetshopApiError("page 视图必须提供 snapshotToken")
        return None
    if not allowed:
        raise NetshopApiError("只有 page 视图可以提供 snapshotToken")
    if len(values) != 1 or not re_full_hex(values[0]):
        raise NetshopApiError("snapshotToken 必须是唯一的 64 位十六进制版本令牌")
    return values[0].lower()


def re_full_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise NetshopApiError(
        "网店数据版本持续变化，请稍后重试",
        code="service_unavailable",
        status=503,
    )


def _lock_request_id(request_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"netshop-write-receipt\n{request_id}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _replay_fenced_write(
    request: HttpRequest,
    principal: Principal,
    callback: Callable[[], tuple[dict[str, object], int]],
) -> JsonResponse:
    request_id = request.headers.get("X-Teruisi-Request-Id", "").strip()
    body_digest = request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower()
    query_digest = hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest()
    def same_binding(receipt: NetshopWriteRequestReceipt) -> bool:
        return (
            receipt.body_sha256 == body_digest
            and receipt.query_sha256 == query_digest
            and receipt.method == request.method
            and receipt.path == request.path
            and receipt.actor_email == principal.email.strip().lower()
        )

    # Persist the processing receipt before entering the domain transaction.
    # This keeps a failed domain-attempt audit from being rolled back with the
    # request receipt and lets a response-loss retry reconcile idempotently.
    with transaction.atomic():
        _lock_request_id(request_id)
        receipt = NetshopWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if receipt:
            if not same_binding(receipt):
                raise NetshopApiError(
                    "内部请求标识已绑定其他网店写入",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return _json(receipt.response_payload, receipt.response_status, replayed=True)
            stale_before = timezone.now() - timedelta(minutes=5)
            if receipt.created_at > stale_before:
                raise NetshopApiError("相同网店请求仍在处理中", code="conflict", status=409)
            receipt.delete()
        NetshopWriteRequestReceipt.objects.create(
            request_id=request_id,
            body_sha256=body_digest,
            query_sha256=query_digest,
            method=request.method,
            path=request.path,
            actor_email=principal.email.strip().lower(),
        )

    try:
        payload, status = callback()
    except Exception:
        # A failed callback must leave its domain audit committed but should
        # not strand this request id forever. A concurrent request remains
        # fenced by the same advisory lock and exact binding check.
        with transaction.atomic():
            _lock_request_id(request_id)
            NetshopWriteRequestReceipt.objects.filter(
                request_id=request_id, status="processing"
            ).delete()
        raise

    with transaction.atomic():
        _lock_request_id(request_id)
        receipt = NetshopWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if receipt is None or not same_binding(receipt) or receipt.status != "processing":
            raise NetshopApiError(
                "网店写入请求回执所有权已失效",
                code="version_conflict",
                status=409,
            )
        receipt.status = "completed"
        receipt.response_status = status
        receipt.response_payload = payload
        receipt.completed_at = timezone.now()
        receipt.save()
        return _json(payload, status)


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        if request.method == "GET":
            page = positive(request.GET.get("page"), 1, "page", MAX_PAGE)
            page_size = positive(
                request.GET.get("pageSize") or request.GET.get("limit"),
                20,
                "pageSize",
                MAX_PAGE_SIZE,
            )
            platforms = _platforms(principal, request.GET.getlist("platform"))
            payload, revision = _consistent_read(
                lambda: list_import_batches(
                    page=page,
                    page_size=page_size,
                    ids=request.GET.getlist("batchId"),
                    sources=request.GET.getlist("source"),
                    platforms=platforms,
                    shops=request.GET.getlist("shop"),
                )
            )
            return _json(payload, revision=revision)
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = import_netshop_payload(payload, principal.email)
            return result, 201 if result.get("status") == "imported" else 200 if result.get("ok") else 422

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "网店数据导入失败", import_shape=request.method == "POST")


@require_POST
def asset_uploads(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            return execute_asset_upload_action(payload), 200

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "天猫 SPU 商品图上传会话处理失败", import_shape=True)


@require_POST
def consumers(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        consumer_request = validate_consumer_request(_body(request))
        payload, revision = _consistent_read(
            lambda: {
                "operation": consumer_request["operation"],
                "data": execute_consumer_query(principal, consumer_request),
            }
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店消费数据失败")


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        platforms = _platforms(principal, request.GET.getlist("platform"))
        shop_values = request.GET.getlist("shop")
        if len(shop_values) > 1:
            raise NetshopApiError("shop 参数不能重复")
        shop = shop_values[0].strip() if shop_values else None
        payload, revision = _consistent_read(
            lambda: read_overview(shop=shop, platforms=platforms)
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店概览失败")


@require_GET
def products(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        view = _single(request.GET.getlist("view"), "full", {"full", "page"}, "view")
        snapshot = _snapshot(
            request.GET.getlist("snapshotToken"), required=view == "page", allowed=view == "page"
        )
        page = positive(request.GET.get("page"), 1, "page", MAX_PAGE)
        page_size = positive(request.GET.get("pageSize"), 50, "pageSize", MAX_PAGE_SIZE)
        platforms = _platforms(principal, request.GET.getlist("platform"))
        outlets = _outlets(request, platforms)
        sales_period = period(request.GET.get("startDate"), request.GET.get("endDate"))
        payload, revision = _consistent_read(
            lambda: product_catalog(
                principal,
                query=(request.GET.get("q") or "").strip(),
                page=page,
                page_size=page_size,
                platforms=platforms,
                outlets=outlets,
                sales_period=sales_period,
                view=view,
                expected_snapshot_token=snapshot,
            )
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店货品数据失败")


@require_GET
def product_performance(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        dimension = _single(request.GET.getlist("dimension"), "sku", {"sku", "spu"}, "dimension")
        view = _single(
            request.GET.getlist("view"), "full", {"summary", "full", "page"}, "view"
        )
        snapshot = _snapshot(
            request.GET.getlist("snapshotToken"), required=view == "page", allowed=view == "page"
        )
        page = positive(request.GET.get("page"), 1, "page", MAX_PAGE)
        page_size = positive(request.GET.get("pageSize"), 50, "pageSize", MAX_PAGE_SIZE)
        platforms = _platforms(principal, request.GET.getlist("platform"))
        if dimension == "sku" and any(item != "京东" for item in platforms):
            raise NetshopApiError("SKU 商品日数据仅支持京东平台")
        outlets = _outlets(request, platforms)
        requested_period = period(request.GET.get("startDate"), request.GET.get("endDate"))
        payload, revision = _consistent_read(
            lambda: read_product_performance(
                dimension=dimension,
                query=(request.GET.get("q") or "").strip(),
                page=page,
                page_size=page_size,
                platforms=platforms,
                outlets=outlets,
                requested_period=requested_period,
                view=view,
                expected_snapshot_token=snapshot,
            )
        )
        if view == "full":
            allowed = ["京东"] if dimension == "sku" else list(SUPPORTED_PLATFORMS)
            payload["platforms"] = [item for item in allowed if not platforms or item in platforms]
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店商品日数据失败")


def _promotion_inputs(request: HttpRequest, principal: Principal):
    platforms = _platforms(principal, request.GET.getlist("platform"))
    if not request.GET.getlist("platform"):
        raise NetshopApiError("推广聚合查询必须显式提供 platform")
    outlets = _outlets(request, platforms)
    requested_period = period(
        request.GET.get("startDate"), request.GET.get("endDate"), required=True
    )
    assert requested_period is not None
    return platforms, outlets, requested_period


@require_GET
def promotion_overview(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        platforms, outlets, requested_period = _promotion_inputs(request, principal)
        snapshots = request.GET.getlist("snapshotToken")
        expected = _snapshot(snapshots, required=False, allowed=True)
        payload, revision = _consistent_read(
            lambda: read_promotion_overview(
                platforms=platforms,
                outlets=outlets,
                requested_period=requested_period,
                expected_snapshot_token=expected,
            )
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店推广概览失败")


@require_GET
def promotion_diagnostic(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        if request.GET.getlist("platform") != ["京东"]:
            raise NetshopApiError("推广诊断必须指定唯一京东平台")
        _platforms(principal, ["京东"])
        outlets = _outlets(request, ["京东"])
        if outlets != [{"platform": "京东", "shopName": PROMOTION_DIAGNOSTIC_SHOP}]:
            raise NetshopApiError("推广诊断必须指定唯一授权店铺的 outlet 复合键")
        if len(request.GET.getlist("startDate")) != 1 or len(request.GET.getlist("endDate")) != 1:
            raise NetshopApiError("推广诊断必须指定唯一的起止日期")
        requested_period = period(request.GET["startDate"], request.GET["endDate"], required=True)
        assert requested_period is not None
        if requested_period["days"] > 31:
            raise NetshopApiError("推广诊断单次最多查询 31 个完整自然日")
        payload, revision = _consistent_read(
            lambda: read_promotion_diagnostic(
                start_date=str(requested_period["startDate"]),
                end_date=str(requested_period["endDate"]),
                source_revision=revision_value(),
            )
        )
        payload["sourceRevision"] = revision
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取京东推广诊断失败")


@require_GET
def promotion_items(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        platforms, outlets, requested_period = _promotion_inputs(request, principal)
        page = positive(request.GET.get("page"), 1, "page", MAX_PAGE)
        page_size = positive(
            request.GET.get("pageSize"), 20, "pageSize", MAX_PROMOTION_PAGE_SIZE
        )
        payload, revision = _consistent_read(
            lambda: read_promotion_items(
                query=(request.GET.get("q") or "").strip(),
                page=page,
                page_size=page_size,
                platforms=platforms,
                outlets=outlets,
                requested_period=requested_period,
            )
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店推广商品失败")


@require_GET
def promotion_performance(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request)
        requested = request.GET.getlist("platform")
        platforms = _platforms(principal, requested)
        if not platforms:
            platforms = list(SUPPORTED_PLATFORMS)
        outlets = _outlets(request, platforms)
        requested_period = period(request.GET.get("startDate"), request.GET.get("endDate"))
        if requested_period is None:
            scope = NetshopPromotionShopDaily.objects.filter(platform__in=platforms)
            latest = scope.order_by("-business_date").values_list("business_date", flat=True).first()
            if latest:
                end = date.fromisoformat(latest)
                start = max(end - timedelta(days=MAX_DAYS - 1), date(1900, 1, 1))
                requested_period = period(start.isoformat(), end.isoformat(), required=True)
            else:
                raise NetshopApiError("推广聚合尚无可查询日期", code="service_unavailable", status=503)
        page = positive(request.GET.get("page"), 1, "page", MAX_PAGE)
        page_size = positive(
            request.GET.get("pageSize"), 20, "pageSize", MAX_PROMOTION_PAGE_SIZE
        )
        payload, revision = _consistent_read(
            lambda: read_promotion_performance(
                query=(request.GET.get("q") or "").strip(),
                page=page,
                page_size=page_size,
                platforms=platforms,
                outlets=outlets,
                requested_period=requested_period,
            )
        )
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店推广数据失败")


@require_GET
def product_image(request: HttpRequest, content_hash: str) -> JsonResponse:
    try:
        principal = _principal(request)
        platforms = _platforms(principal, ["天猫"])
        payload, revision = _consistent_read(
            lambda: {"item": product_image_metadata(content_hash, platforms)}
        )
        if payload["item"] is None:
            return _json({"error": "Not found", "code": "not_found"}, 404)
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取网店商品图片元数据失败")
