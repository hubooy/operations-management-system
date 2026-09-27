from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable

from django.conf import settings
from django.db import connection, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .analysis import FinanceDimensionFilterError, get_finance_analysis
from .consumers import execute_consumer_query, parse_consumer_body
from .errors import FinanceApiError
from .import_service import import_finance_payload, list_import_batches
from .models import FinanceDataRevision, FinanceWriteRequestReceipt
from .annual_progress import annual_progress, validate_year
from . import business_evidence_page as evidence_page
from . import raw_workbook_attestation_v2 as workbook_attestation
from .target_service import delete_target, import_annual_targets, list_targets, target_options, upsert_target
from business_analysis.contracts import canonical


logger = logging.getLogger(__name__)


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
        response["X-Finance-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _principal(request: HttpRequest, roles: set[str]) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError(
            "当前角色无权访问", status=403, code="insufficient_role"
        )
    if principal.scope is not None:
        raise PrincipalEnvelopeError(
            "财务数据仅允许无数据范围限制的身份访问",
            status=403,
            code="access_denied",
        )
    return principal


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, FinanceDimensionFilterError):
        return _json({"error": str(error), "code": error.code, **error.payload}, error.status)
    if isinstance(error, FinanceApiError):
        if import_shape:
            return _json(
                {"ok": False, "status": "rejected", "message": str(error), "code": error.code},
                error.status,
            )
        return _json({"error": str(error), "code": error.code, **error.payload}, error.status)
    logger.exception("Unhandled finance API error")
    if import_shape:
        return _json(
            {"ok": False, "status": "rejected", "message": fallback, "code": "internal_error"},
            500,
        )
    return _json({"error": fallback, "code": "internal_error"}, 500)


def _positive(raw: str | None, fallback: int, label: str, maximum: int) -> int:
    if raw is None:
        return fallback
    if not raw.isdigit() or raw.startswith("0"):
        raise FinanceApiError(f"{label}必须为十进制正整数。")
    value = int(raw)
    if value < 1 or value > maximum:
        raise FinanceApiError(f"{label}超出允许范围。")
    return value


def _body(request: HttpRequest) -> dict[str, object]:
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinanceApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise FinanceApiError("请求内容不是有效 JSON")
    return payload


def _revision() -> str:
    row = FinanceDataRevision.objects.filter(domain="finance").first()
    if row is None or row.revision < 0 or len(row.source_digest) != 64:
        raise FinanceApiError(
            "财务数据版本不可用", code="service_unavailable", status=503
        )
    return f"{row.revision}:{row.source_digest[:12]}"


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = _revision()
        payload = loader()
        after = _revision()
        if before == after:
            return payload, after
    raise FinanceApiError(
        "财务数据版本持续变化，请稍后重试。",
        code="service_unavailable",
        status=503,
    )


def _lock_request_id(request_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    digest = hashlib.sha256(f"finance-write-receipt\n{request_id}".encode()).digest()
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
    query_digest = hashlib.sha256(
        request.META.get("QUERY_STRING", "").encode("utf-8")
    ).hexdigest()
    with transaction.atomic():
        _lock_request_id(request_id)
        receipt = FinanceWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if receipt:
            if (
                receipt.body_sha256 != body_digest
                or receipt.query_sha256 != query_digest
                or receipt.method != request.method
                or receipt.path != request.path
                or receipt.actor_email != principal.email.strip().lower()
            ):
                raise FinanceApiError(
                    "内部请求标识已绑定其他财务写入",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return _json(
                    receipt.response_payload,
                    receipt.response_status,
                    replayed=True,
                )
            raise FinanceApiError(
                "相同财务请求仍在处理中",
                code="conflict",
                status=409,
            )
        receipt = FinanceWriteRequestReceipt.objects.create(
            request_id=request_id,
            body_sha256=body_digest,
            query_sha256=query_digest,
            method=request.method,
            path=request.path,
            actor_email=principal.email.strip().lower(),
        )
        payload, status = callback()
        receipt.status = "completed"
        receipt.response_status = status
        receipt.response_payload = payload
        receipt.completed_at = timezone.now()
        receipt.save()
        return _json(payload, status)


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            page = _positive(request.GET.get("page"), 1, "page", 10_000)
            page_size = _positive(
                request.GET.get("pageSize") or request.GET.get("limit"),
                20,
                "pageSize" if request.GET.get("pageSize") is not None else "limit",
                100,
            )
            payload, revision = _consistent_read(lambda: list_import_batches(page, page_size))
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            result = import_finance_payload(payload, principal.email)
            status = 201 if result.get("status") == "imported" else 200 if result.get("ok") else 422
            return result, status

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "月度财报导入失败。", import_shape=request.method == "POST")


@require_POST
def raw_workbook_attest(request: HttpRequest) -> JsonResponse:
    """Unregistered-by-default signed binary follow-up; never imports v1."""
    if not settings.FINANCE_RAW_WORKBOOK_BYTES_V2_ENABLED:
        return _json({"error": "原始财报字节验真未启用",
            "code": "not_found"}, 404)
    try:
        length_text = request.META.get("CONTENT_LENGTH", "")
        if (not length_text.isdecimal() or not 1 <= int(length_text)
                <= 8 * 1024 * 1024):
            raise FinanceApiError("原始XLSX长度无效或超过8MiB",
                status=413, code="payload_too_large")
        if request.content_type != "application/octet-stream":
            raise FinanceApiError("仅接受受签名保护的原始XLSX字节",
                status=415, code="unsupported_media_type")
        principal = _principal(request, {"admin"})
        raw = request.body
        if len(raw) != int(length_text):
            raise FinanceApiError("原始XLSX长度与请求头不一致",
                status=422, code="invalid_finance_workbook_bytes")
        month = request.GET.get("month", "")
        batch_id = request.GET.get("batchId", "")
        if (len(request.GET) != 2 or any(len(request.GET.getlist(key)) != 1
                for key in ("month", "batchId"))):
            raise FinanceApiError("验真目标月份和批次必须唯一",
                status=422, code="invalid_finance_workbook_target")

        def execute() -> tuple[dict[str, object], int]:
            result = workbook_attestation.stage(principal, raw, month,
                batch_id, enabled=True)
            return {"ok": True, **result}, 201 if not result[
                "idempotentReplay"] else 200

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "财报原始字节验真失败。")


@require_GET
def analysis(request: HttpRequest) -> JsonResponse:
    try:
        _principal(request, {"viewer", "analyst", "operator", "admin"})
        month_values = [part for value in request.GET.getlist("month") for part in value.split(",") if part]
        all_months = "*" in month_values
        requested = list(dict.fromkeys(item for item in month_values if item != "*"))
        fallback_values = request.GET.getlist("initialMonthFallback")
        if len(fallback_values) > 1 or (fallback_values and fallback_values[0] != "latest_completed"):
            raise FinanceApiError("initialMonthFallback 只能是 latest_completed")
        fallback = bool(fallback_values)
        if all_months and requested:
            raise FinanceApiError("month=* 不能与指定月份同时使用。")
        if all_months and fallback:
            raise FinanceApiError("month=* 不需要初始月份回退。")
        if fallback and not requested:
            raise FinanceApiError("初始月份回退必须与指定月份同时使用。")
        payload, revision = _consistent_read(lambda: get_finance_analysis(
            requested_months=requested,
            all_months=all_months,
            fallback_to_latest=fallback,
            platform_names=request.GET.getlist("platform"),
            shop_keys=request.GET.getlist("shop"),
        ))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "财报分析读取失败。")


@require_http_methods(["GET", "POST", "DELETE"])
def targets(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            page = _positive(request.GET.get("page"), 1, "page", 10_000)
            page_size = _positive(request.GET.get("pageSize"), 50, "pageSize", 100)
            views = request.GET.getlist("view")
            if len(views) > 1 or (views and views[0] not in {"full", "items", "options", "annual"}):
                raise FinanceApiError("view 必须且只能是 full、items、options 或 annual")
            view = views[0] if views else "full"

            year_values = request.GET.getlist("year")
            if len(year_values) > 1:
                raise FinanceApiError("year 不能重复")
            year = validate_year(year_values[0]) if year_values else None
            if view == "annual" and year is None:
                raise FinanceApiError("年度进度必须提供年份")

            def load() -> dict[str, object]:
                if view == "annual":
                    return annual_progress(year, page, page_size)
                if view == "items":
                    return list_targets(page, page_size, annual_year=year) if year is not None else list_targets(page, page_size)
                if view == "options":
                    return {"financeOptions": target_options()}
                return {**list_targets(page, page_size), "financeOptions": target_options()}

            payload, revision = _consistent_read(load)
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        if request.method == "POST":
            payload = _body(request)

            def execute_post() -> tuple[dict[str, object], int]:
                item, created = upsert_target(payload)
                return {"ok": True, "item": item}, 201 if created else 200

            return _replay_fenced_write(request, principal, execute_post)
        identifier = (request.GET.get("id") or "").strip()
        if not identifier:
            raise FinanceApiError("缺少目标 ID")
        if request.GET.get("expectedVersion") is None:
            raise FinanceApiError("缺少 expectedVersion")
        expected_version = _positive(
            request.GET.get("expectedVersion"), 1, "expectedVersion", 9_007_199_254_740_991
        )
        reason = request.GET.get("reason") or ""

        def execute_delete() -> tuple[dict[str, object], int]:
            return {"ok": True, **delete_target(identifier, expected_version, principal.email, reason)}, 200

        return _replay_fenced_write(request, principal, execute_delete)
    except Exception as error:
        fallback = {
            "GET": "目标进度情况读取失败。",
            "POST": "目标保存失败。",
            "DELETE": "目标删除失败。",
        }[request.method]
        return _error(error, fallback)


@require_POST
def target_import(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        payload = _body(request)

        def execute() -> tuple[dict[str, object], int]:
            return import_annual_targets(payload), 201

        return _replay_fenced_write(request, principal, execute)
    except Exception as error:
        return _error(error, "店铺年度目标导入失败。")


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    try:
        principal = verify_principal(request)
        consumer_request = parse_consumer_body(request)

        def load() -> dict[str, object]:
            return {
                "operation": consumer_request["operation"],
                "data": execute_consumer_query(principal, consumer_request),
            }

        payload, revision = _consistent_read(load)
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "财务消费者查询失败。")


@require_POST
def business_evidence_page(request: HttpRequest) -> HttpResponse:
    """Signed, reader-only boundary; no public AI tool or v3 collector grant."""
    try:
        principal = _principal(request, {"admin"})
        if len(request.body) > 8192:
            raise FinanceApiError("财报证据分页请求超过容量", status=413, code="payload_too_large")
        body = _body(request)
        required = {"query", "offset", "afterId"}
        optional = {"expectedSourceRef", "expectedRevision"}
        if set(body) - required - optional or required - set(body):
            raise FinanceApiError("财报证据分页请求字段无效", status=422, code="invalid_request")
        page = evidence_page.read_page(principal, body["query"], offset=body["offset"],
            after_id=body["afterId"], expected_source_ref=body.get("expectedSourceRef"),
            expected_revision=body.get("expectedRevision"))
        raw = canonical(page).encode("utf-8")
        if len(raw) > evidence_page.MAX_PAGE_BYTES:
            raise FinanceApiError("财报证据响应超过完整页容量", status=413, code="payload_too_large")
        revision, separator, full_digest = page["sourceRevision"].partition(":")
        if separator != ":" or not revision.isdecimal() or len(full_digest) != 64:
            raise FinanceApiError("财报证据版本回显无效", status=503, code="service_unavailable")
        response = HttpResponse(raw, content_type="application/json; charset=utf-8")
        response["Cache-Control"] = "no-store"
        response["X-Finance-Data-Revision"] = f"{revision}:{full_digest[:12]}"
        return response
    except Exception as error:
        return _error(error, "财报证据分页读取失败。")
