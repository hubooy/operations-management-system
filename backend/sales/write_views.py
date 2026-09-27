from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.db.models import Count, Max, Min, Q, Sum
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from .auth import Principal, PrincipalEnvelopeError, verify_principal
from .models import SalesImportBatch, SalesOrderLine
from .policy import approved_sales_channels, policy_version
from .write_requests import (
    WriteRequestClaim,
    claim_write_request,
    complete_write_request,
    fail_write_request,
)
from .write_service import (
    SalesImportServiceError,
    batch_payload,
    begin_raw_upload,
    begin_staged_import,
    claim_raw_upload,
    cleanup_raw_upload_chunks,
    complete_staged_import,
    finish_raw_upload,
    list_expired_raw_uploads,
    list_import_batches,
    read_raw_upload,
    read_staged_import,
    purge_expired_raw_upload,
    read_raw_upload_chunk,
    register_raw_upload_chunk,
    stage_normalized_chunk,
    validate_import_date_range,
)


logger = logging.getLogger(__name__)


def _json(payload: dict[str, object], status: int = 200, *, replay: bool = False) -> JsonResponse:
    response = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if replay:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _principal(request: HttpRequest, roles: set[str]) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if principal.scope is not None:
        raise PrincipalEnvelopeError("销售导入不支持受限数据范围账号", status=403, code="access_denied")
    return principal


def _parse_json(request: HttpRequest) -> dict[str, object]:
    if not request.content_type or request.content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise SalesImportServiceError("内部写接口只接受 application/json", status=415)
    try:
        value: Any = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SalesImportServiceError("请求 JSON 无效", status=400) from error
    if not isinstance(value, dict):
        raise SalesImportServiceError("请求 JSON 必须是对象", status=400)
    return value


def _reject_unknown_fields(
    payload: dict[str, object], allowed: set[str]
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise SalesImportServiceError(
            f"请求包含未声明字段：{', '.join(unknown[:10])}",
            code="unknown_fields",
            status=400,
        )


def _service_error_payload(error: SalesImportServiceError) -> dict[str, object]:
    issues = list(error.issues) or [{"code": error.code, "message": str(error)}]
    return {
        "ok": False,
        "status": "rejected",
        "message": str(error),
        "code": error.code,
        "errors": issues,
        "errorCount": len(issues),
    }


def _handle_get(callback: Callable[[Principal], dict[str, object]], request: HttpRequest, roles: set[str]) -> JsonResponse:
    try:
        principal = _principal(request, roles)
        return _json(callback(principal))
    except PrincipalEnvelopeError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except SalesImportServiceError as error:
        return _json(_service_error_payload(error), error.status)
    except Exception:
        logger.exception("Unhandled sales write-side GET error")
        return _json({"error": "读取销售导入数据失败。", "code": "internal_error"}, 500)


def _handle_write(
    callback: Callable[[Principal, dict[str, object]], tuple[dict[str, object], int]],
    request: HttpRequest,
) -> JsonResponse:
    claim: WriteRequestClaim | None = None
    try:
        principal = _principal(request, {"admin"})
        request_id = request.headers.get("X-Teruisi-Request-Id", "").strip()
        body_sha256 = request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower()
        claim = claim_write_request(
            request_id=request_id,
            actor_email=principal.email,
            method=request.method.upper(),
            path=request.path,
            body_sha256=body_sha256,
        )
        if claim.replay_payload is not None and claim.replay_status is not None:
            return _json(claim.replay_payload, claim.replay_status, replay=True)
        payload, status = callback(principal, _parse_json(request))
        complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status)
    except PrincipalEnvelopeError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except SalesImportServiceError as error:
        payload = _service_error_payload(error)
        if claim is not None and claim.replay_payload is None:
            try:
                complete_write_request(claim, response_status=error.status, response_payload=payload)
            except Exception:
                logger.exception("Failed to persist rejected sales write receipt")
        return _json(payload, error.status)
    except Exception:
        if claim is not None:
            try:
                fail_write_request(claim)
            except Exception:
                logger.exception("Failed to mark sales write receipt failed")
        logger.exception("Unhandled sales write-side mutation error")
        return _json(
            {
                "ok": False,
                "status": "rejected",
                "message": "销售写入服务处理失败。",
                "code": "internal_error",
            },
            500,
        )


def _handle_post_read(
    callback: Callable[[Principal, dict[str, object]], dict[str, object]],
    request: HttpRequest,
) -> JsonResponse:
    try:
        principal = _principal(request, {"admin"})
        return _json(callback(principal, _parse_json(request)))
    except PrincipalEnvelopeError as error:
        return _json({"error": str(error), "code": error.code}, error.status)
    except SalesImportServiceError as error:
        return _json(_service_error_payload(error), error.status)
    except Exception:
        logger.exception("Unhandled sales write-side payload read error")
        return _json(
            {"error": "读取销售上传分片失败。", "code": "internal_error"}, 500
        )


@require_GET
def imports(request: HttpRequest) -> JsonResponse:
    def execute(_principal_value: Principal) -> dict[str, object]:
        try:
            page = int(request.GET.get("page", "1"))
            page_size = int(request.GET.get("pageSize", request.GET.get("limit", "20")))
        except ValueError as error:
            raise SalesImportServiceError("分页参数必须是整数", status=400) from error
        return list_import_batches(page, page_size)

    return _handle_get(execute, request, {"viewer", "analyst", "operator", "admin"})


@require_http_methods(["GET", "POST", "PUT"])
def raw_uploads(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        return _handle_get(
            lambda principal: read_raw_upload(request.GET.get("uploadId"), principal.email),
            request,
            {"admin"},
        )

    def execute(principal: Principal, payload: dict[str, object]) -> tuple[dict[str, object], int]:
        if request.method == "PUT":
            _reject_unknown_fields(
                payload,
                {
                    "uploadId",
                    "chunkIndex",
                    "objectKey",
                    "sizeBytes",
                    "sha256",
                    "contentBase64",
                },
            )
            return {
                "ok": True,
                "status": "uploading",
                "upload": register_raw_upload_chunk(payload, principal.email),
            }, 200
        action = payload.get("action")
        if action == "init":
            _reject_unknown_fields(
                payload,
                {
                    "action",
                    "fileName",
                    "fileSizeBytes",
                    "chunkCount",
                    "fingerprint",
                    "expectedStartDate",
                    "expectedEndDate",
                    "expectedChannels",
                },
            )
            return {
                "ok": True,
                "status": "ready",
                "upload": begin_raw_upload(payload, principal.email),
                "limits": {
                    "chunkSizeBytes": 2 * 1024 * 1024,
                    "maxFileSizeBytes": 128 * 1024 * 1024,
                },
            }, 200
        if action == "claim":
            _reject_unknown_fields(payload, {"action", "uploadId"})
            return {
                "ok": True,
                "status": "processing",
                "upload": claim_raw_upload(payload.get("uploadId"), principal.email),
            }, 200
        if action == "finish":
            _reject_unknown_fields(
                payload,
                {"action", "uploadId", "ownerToken", "completed", "resultBatchId"},
            )
            completed = payload.get("completed")
            if not isinstance(completed, bool):
                raise SalesImportServiceError("completed 必须是布尔值", status=400)
            return {
                "ok": True,
                "status": "completed" if completed else "ready",
                "upload": finish_raw_upload(
                    payload.get("uploadId"),
                    principal.email,
                    owner_token=payload.get("ownerToken"),
                    completed=completed,
                    result_batch_id=payload.get("resultBatchId"),
                ),
            }, 200
        if action == "cleanup":
            _reject_unknown_fields(payload, {"action", "uploadId"})
            return {
                "ok": True,
                "status": "completed",
                "cleanup": cleanup_raw_upload_chunks(payload.get("uploadId"), principal.email),
            }, 200
        if action == "sweep":
            _reject_unknown_fields(payload, {"action", "limit"})
            return {
                "ok": True,
                "status": "ready",
                "sweep": list_expired_raw_uploads(
                    principal.email, payload.get("limit", 10)
                ),
            }, 200
        if action == "purge":
            _reject_unknown_fields(
                payload,
                {
                    "action",
                    "uploadId",
                    "ownerGeneration",
                    "cleanupToken",
                },
            )
            return {
                "ok": True,
                "status": "purged",
                "purge": purge_expired_raw_upload(
                    payload.get("uploadId"),
                    principal.email,
                    owner_generation=payload.get("ownerGeneration"),
                    cleanup_token=payload.get("cleanupToken"),
                ),
            }, 200
        raise SalesImportServiceError("未知的原始分片操作", status=400)

    return _handle_write(execute, request)


@require_http_methods(["POST"])
def raw_upload_chunk(request: HttpRequest) -> JsonResponse:
    def execute(
        principal: Principal, payload: dict[str, object]
    ) -> dict[str, object]:
        _reject_unknown_fields(payload, {"uploadId", "chunkIndex", "ownerToken"})
        return {"chunk": read_raw_upload_chunk(payload, principal.email)}

    return _handle_post_read(execute, request)


@require_GET
def raw_upload_status(request: HttpRequest) -> JsonResponse:
    """Reader-process surface for resumable upload inspection only."""

    return _handle_get(
        lambda principal: read_raw_upload(request.GET.get("uploadId"), principal.email),
        request,
        {"admin"},
    )


@require_http_methods(["GET", "POST", "PUT"])
def staged_imports(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        return _handle_get(
            lambda principal: read_staged_import(request.GET.get("sessionId"), principal.email),
            request,
            {"admin"},
        )

    def execute(principal: Principal, payload: dict[str, object]) -> tuple[dict[str, object], int]:
        if request.method == "PUT":
            _reject_unknown_fields(
                payload,
                {"sessionId", "chunkIndex", "rows", "rawUploadOwnerToken"},
            )
            return {
                "ok": True,
                "status": "uploading",
                "session": stage_normalized_chunk(payload, principal.email),
            }, 200
        action = payload.get("action")
        if action == "init":
            _reject_unknown_fields(
                payload,
                {
                    "action",
                    "rawUploadId",
                    "rawUploadOwnerToken",
                    "fingerprint",
                    "fileName",
                    "fileSizeBytes",
                    "rawFileHash",
                    "sheetName",
                    "expectedStartDate",
                    "expectedEndDate",
                    "expectedChannels",
                    "chunkCount",
                    "sourceTotals",
                    "parserWarnings",
                    "parserErrors",
                    "systemCostSnapshot",
                },
            )
            return {
                "ok": True,
                "status": "ready",
                "session": begin_staged_import(payload, principal.email),
                "limits": {
                    "maxRows": 500_000,
                    "maxRowsPerChunk": 1_000,
                    "maxChunkCount": 1_000,
                },
            }, 200
        if action == "complete":
            _reject_unknown_fields(
                payload, {"action", "sessionId", "rawUploadOwnerToken"}
            )
            result = complete_staged_import(
                payload.get("sessionId"),
                principal.email,
                payload.get("rawUploadOwnerToken"),
            )
            return result, 201 if result.get("status") == "imported" else 200
        raise SalesImportServiceError("未知的规范化导入操作", status=400)

    return _handle_write(execute, request)


@require_GET
def verify_import(request: HttpRequest) -> JsonResponse:
    def execute(_principal_value: Principal) -> dict[str, object]:
        if request.GET.get("policyOnly") == "1":
            return {"policyVersion": policy_version()}
        start_date, end_date = validate_import_date_range(
            request.GET.get("startDate"), request.GET.get("endDate")
        )
        batch_id = (request.GET.get("batchId") or "").strip()
        end_exclusive = end_date + timedelta(days=1)
        range_queryset = SalesOrderLine.objects.filter(
            business_date__gte=start_date,
            business_date__lt=end_exclusive,
        )
        excluded_warehouse_rows = range_queryset.filter(is_business_row=False).count()
        queryset = range_queryset.filter(is_business_row=True)
        stats = queryset.aggregate(
            row_count=Count("id"),
            min_ship_time=Min("ship_time"),
            max_ship_time=Max("ship_time"),
        )
        shop_queryset = (
            queryset.values("channel", "platform", "shop_name")
            .annotate(row_count=Count("id"), net_sales_cents=Sum("allocated_amount_cents"))
            .order_by("channel", "platform", "shop_name")
        )
        shop_total = shop_queryset.count()
        shops = list(shop_queryset[:501])
        approved = approved_sales_channels()
        channels_with_data = set(
            queryset.filter(channel__in=approved).values_list("channel", flat=True).distinct()
        )
        non_whitelist = list(
            queryset.exclude(channel__in=approved)
            .order_by("channel")
            .values_list("channel", flat=True)
            .distinct()[:501]
        )
        rows_not_owned = (
            queryset.exclude(last_import_batch_id=batch_id).count() if batch_id else None
        )
        batch = (
            SalesImportBatch.objects.filter(Q(id=batch_id) | Q(file_hash=batch_id)).first()
            if batch_id
            else None
        )
        return {
            "policyVersion": policy_version(),
            "period": {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "endExclusive": end_exclusive.isoformat(),
            },
            "batch": batch_payload(batch) if batch else None,
            "stats": {
                "rowCount": int(stats["row_count"] or 0),
                "minShipTime": stats["min_ship_time"],
                "maxShipTime": stats["max_ship_time"],
                "excludedWarehouseRows": excluded_warehouse_rows,
                "rowsNotOwnedByBatch": rows_not_owned,
            },
            "shops": [
                {
                    "channel": item["channel"],
                    "platform": item["platform"],
                    "shopName": item["shop_name"],
                    "rowCount": int(item["row_count"]),
                    "netSalesCents": int(item["net_sales_cents"] or 0),
                }
                for item in shops[:500]
            ],
            "shopPagination": {
                "total": shop_total,
                "returned": min(len(shops), 500),
                "truncated": len(shops) > 500,
            },
            "nonWhitelistChannels": non_whitelist[:500],
            "nonWhitelistChannelPagination": {
                "returned": min(len(non_whitelist), 500),
                "truncated": len(non_whitelist) > 500,
            },
            "whitelistWithNoData": [item for item in approved if item not in channels_with_data],
        }

    return _handle_get(execute, request, {"admin"})
