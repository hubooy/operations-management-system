from __future__ import annotations

import logging
import hashlib
import json
from collections.abc import Callable
from functools import lru_cache
from threading import Lock

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .auth import PrincipalEnvelopeError, verify_principal
from .category import get_category_analysis, get_category_detail
from .consumers import execute_consumer_query, parse_consumer_body
from .query import (
    SalesAccessError,
    SalesRequestError,
    parse_outlets,
    parse_product_queries,
    resolve_product_codes,
    revision_token,
    selected_values,
)
from .summary import dashboard_projection, get_sales_summary


logger = logging.getLogger(__name__)


class SalesRevisionChangedError(Exception):
    pass


@lru_cache(maxsize=1024)
def _read_cache_lock(cache_key: str) -> Lock:
    """One in-process computation per revision/query identity.

    The local production service intentionally runs one Waitress process with
    multiple threads, so this prevents a cold long-range query from being
    recomputed concurrently after a restart or revision change.
    """

    return Lock()


def _first(query, key: str, default: str | None = None) -> str | None:
    """Match URLSearchParams.get(): public edge routes use the first value."""
    values = query.getlist(key)
    return values[0] if values else default


def _cache_identity(request: HttpRequest, principal) -> str:
    material = json.dumps(
        {
            "path": request.path,
            "query": request.META.get("QUERY_STRING", ""),
            "role": principal.role,
            "scope": principal.scope,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _consistent_read(loader: Callable[[], object], cache_identity: str) -> tuple[object, str, str]:
    """Retry once when a multi-query response straddles a migration commit."""
    for _attempt in range(2):
        before = revision_token()
        cache_key = f"teruisi:sales-read:v1:{before}:{cache_identity}"
        payload = cache.get(cache_key)
        if payload is not None:
            after = revision_token()
            if before == after:
                return payload, after, "hit"
            continue
        with _read_cache_lock(cache_key):
            # The leading request may have populated the cache while this
            # thread waited. Re-read the revision before trusting that value.
            locked_before = revision_token()
            if locked_before != before:
                continue
            payload = cache.get(cache_key)
            cache_status = "hit" if payload is not None else "miss"
            if payload is None:
                payload = loader()
            after = revision_token()
            if before == after:
                if cache_status == "miss" and settings.SALES_READ_CACHE_SECONDS > 0:
                    cache.set(cache_key, payload, timeout=settings.SALES_READ_CACHE_SECONDS)
                return payload, after, cache_status
    raise SalesRevisionChangedError("销售数据版本持续变化，请稍后重试。")


def _bounded_integer(raw: str | None, fallback: int, label: str, maximum: int) -> int:
    if raw is None or raw == "":
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise SalesRequestError(f"{label} 必须为正整数") from error
    if value < 1 or value > maximum:
        raise SalesRequestError(f"{label} 必须为 1 到 {maximum} 的整数")
    return value


def _json(payload: object, status: int = 200, *, revision: str | None = None, extra_headers: dict[str, str] | None = None) -> JsonResponse:
    response = JsonResponse(payload, status=status, safe=not isinstance(payload, list), json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if 200 <= status < 300 and revision is not None:
        response["X-Sales-Data-Revision"] = revision
        response["X-Sales-Source-Revision"] = revision
    for name, value in (extra_headers or {}).items():
        response[name] = value
    return response


def _handle(view: Callable[[HttpRequest], JsonResponse], request: HttpRequest) -> JsonResponse:
    try:
        return view(request)
    except PrincipalEnvelopeError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except SalesAccessError as error:
        return _json({"error": str(error), "code": "access_denied"}, 403)
    except SalesRequestError as error:
        return _json({"error": str(error), "code": "invalid_request"}, 400)
    except SalesRevisionChangedError as error:
        return _json(
            {"error": str(error), "code": "sales_overview_revision_changed"},
            503,
            extra_headers={"Retry-After": "1"},
        )
    except Exception:
        logger.exception("Unhandled sales API error")
        return _json({"error": "读取销售分析失败。", "code": "internal_error"}, 500)


def _principal(request: HttpRequest):
    principal = verify_principal(request)
    if principal.role not in {"viewer", "analyst", "operator", "admin"}:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    return principal


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    def execute(inner: HttpRequest) -> JsonResponse:
        principal = _principal(inner)
        consumer_request = parse_consumer_body(inner)
        body_identity = hashlib.sha256(inner.body).hexdigest()
        payload, stable_revision, cache_status = _consistent_read(
            lambda: execute_consumer_query(principal, consumer_request),
            f"{_cache_identity(inner, principal)}:{body_identity}",
        )
        return _json(
            {"operation": consumer_request["operation"], "data": payload},
            revision=stable_revision,
            extra_headers={"X-Sales-Overview-Cache": cache_status},
        )

    return _handle(execute, request)


@require_GET
def summary(request: HttpRequest) -> JsonResponse:
    def execute(inner: HttpRequest) -> JsonResponse:
        principal = _principal(inner)
        if principal.scope is not None:
            raise SalesAccessError("销售汇总仅支持未受限数据范围账号")
        views = inner.GET.getlist("view")
        if len(views) > 1:
            raise SalesRequestError("view 参数不能重复。")
        requested_view = views[0] if views else None
        if requested_view not in {None, "dashboard"}:
            raise SalesRequestError("view 必须是 dashboard。")
        product_queries = parse_product_queries([*inner.GET.getlist("productQuery"), _first(inner.GET, "productCodes", "") or ""])
        categories = selected_values(inner.GET, "categories", "category", label="品类")
        platforms = selected_values(inner.GET, "platforms", "platform", label="平台")
        outlet_values = selected_values(inner.GET, "outlet", "outlets", label="outlet")
        outlets = parse_outlets(outlet_values)
        payload, stable_revision, cache_status = _consistent_read(lambda: get_sales_summary(
                range_name=_first(inner.GET, "range", "month") or "month",
                projection="dashboard" if requested_view == "dashboard" else "full",
                start_date=_first(inner.GET, "startDate"),
                end_date=_first(inner.GET, "endDate"),
                product_queries=product_queries,
                product_codes=resolve_product_codes(product_queries),
                platforms=platforms,
                shop=_first(inner.GET, "shop"),
                outlets=outlets,
                categories=categories,
            ), _cache_identity(inner, principal))
        if requested_view == "dashboard":
            payload = dashboard_projection(payload)
        return _json(payload, revision=stable_revision, extra_headers={"X-Sales-Overview-Cache": cache_status})

    return _handle(execute, request)


def _category_common(request: HttpRequest) -> tuple[object, dict[str, object]]:
    principal = _principal(request)
    start_date = _first(request.GET, "startDate", "") or ""
    end_date = _first(request.GET, "endDate", "") or ""
    product_queries = parse_product_queries([*request.GET.getlist("productQuery"), *request.GET.getlist("productQueries")])
    outlet_values = selected_values(request.GET, "outlet", "outlets", label="outlet")
    params: dict[str, object] = {
        "startDate": start_date,
        "endDate": end_date,
        "channels": selected_values(request.GET, "channel", "channels", label="channel"),
        "platforms": selected_values(request.GET, "platform", "platforms", label="platform"),
        "outlets": parse_outlets(outlet_values),
        "productQueries": product_queries,
        "productCodes": [],
    }
    return principal, params


@require_GET
def category_analysis(request: HttpRequest) -> JsonResponse:
    def execute(inner: HttpRequest) -> JsonResponse:
        principal, params = _category_common(inner)
        granularity = _first(inner.GET, "granularity", "day") or "day"
        sort_by = _first(inner.GET, "sortBy", "netSalesCents") or "netSalesCents"
        direction = _first(inner.GET, "direction", "desc") or "desc"
        if direction not in {"asc", "desc"}:
            raise SalesRequestError("direction 必须是 asc 或 desc")
        params.update(
            {
                "level": _bounded_integer(_first(inner.GET, "level"), 1, "level", 3),
                "brands": selected_values(inner.GET, "brand", "brands", label="brand"),
                "categories": selected_values(inner.GET, "category", "categories", label="category"),
                "granularity": granularity,
                "sortBy": sort_by,
                "direction": direction,
                "page": _bounded_integer(_first(inner.GET, "page"), 1, "page", 10_000),
                "pageSize": _bounded_integer(_first(inner.GET, "pageSize"), 20, "pageSize", 100),
            }
        )
        payload, stable_revision, _cache_status = _consistent_read(lambda: get_category_analysis(
            {**params, "productCodes": resolve_product_codes(params["productQueries"], principal)}, principal
        ), _cache_identity(inner, principal))
        return _json(payload, revision=stable_revision)

    return _handle(execute, request)


@require_GET
def category_detail(request: HttpRequest) -> JsonResponse:
    def execute(inner: HttpRequest) -> JsonResponse:
        principal, params = _category_common(inner)
        params["category"] = _first(inner.GET, "category", "") or ""
        payload, stable_revision, _cache_status = _consistent_read(lambda: get_category_detail(
            {**params, "productCodes": resolve_product_codes(params["productQueries"], principal)}, principal
        ), _cache_identity(inner, principal))
        return _json(payload, revision=stable_revision)

    return _handle(execute, request)


@require_GET
def analysis_options(request):
    from django.http import HttpResponse
    from . import analysis_options as service
    from .analysis_options_contract import canonical
    from .analysis_options_projection import OptionsError
    if settings.DJANGO_PROCESS_ROLE not in {"reader", "development"}:
        return _json({"code":"not_found", "error":"Not found"},status=404)
    try:
        principal=verify_principal(request)
        query,cursor=service.validate_request(request.GET)
        value=service.read_page(principal,query,cursor)
        response=HttpResponse(canonical(value),content_type="application/json",status=200)
        response["Cache-Control"]="no-store"
        response["X-Sales-Data-Revision"]=value["revision"]
        response["X-Sales-Source-Revision"]=value["revision"]
        return response
    except PrincipalEnvelopeError as error:
        return _json({"code":error.code,"error":str(error)},status=error.status)
    except OptionsError as error:
        return _json({"code":error.code,"error":str(error)},status=error.status)
