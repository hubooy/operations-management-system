from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable
from datetime import date

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from sales.auth import Principal, PrincipalEnvelopeError, verify_principal

from .consumers import execute_consumer_query, validate_consumer_request
from .dingtalk_sync import sync_replenishment_plan
from .dingtalk_group_message import preview_group_message, send_group_message
from .errors import InventoryApiError
from .import_service import import_inventory_payload, list_import_batches, record_edge_rejection
from .plans import import_plans, plan_payload, plan_summary, query_plans, update_plan, upsert_plan
from .query import inventory_age_analysis, inventory_inbound_monitor, inventory_overview, replenishment_plan_sources
from .revisions import revision_value
from .settings_service import read_settings, update_settings
from .warehouse_mapping_service import mapping_payload, update_mapping
from .uploads import CHUNK_SIZE_BYTES, MAX_FILE_SIZE_BYTES, execute_upload_action, read_chunk, receive_chunk
from .write_requests import claim_write_request, complete_write_request, fail_write_request


logger = logging.getLogger(__name__)
JSON_CONTENT_TYPE_RE = re.compile(r"^(?:application/json|application/[a-z0-9.+-]+\+json)(?:\s*;|$)", re.I)


def _json(payload: object, status: int = 200, *, revision: str | None = None, replayed: bool = False) -> JsonResponse:
    response = JsonResponse(payload, status=status, safe=not isinstance(payload, list), json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    if revision is not None and 200 <= status < 300:
        response["X-Inventory-Data-Revision"] = revision
    if replayed:
        response["X-Teruisi-Write-Replay"] = "1"
    return response


def _error(error: Exception, fallback: str, *, import_shape: bool = False) -> JsonResponse:
    if isinstance(error, PrincipalEnvelopeError):
        return _json({"error": str(error), "code": error.code}, error.status)
    if isinstance(error, InventoryApiError):
        if import_shape:
            return _json({"ok": False, "status": "rejected", "message": str(error), "code": error.code}, error.status)
        return _json({"error": str(error), "code": error.code}, error.status)
    logger.exception("Unhandled inventory API error")
    if import_shape:
        return _json({"ok": False, "status": "rejected", "message": fallback, "code": "internal_error"}, 500)
    return _json({"error": fallback, "code": "internal_error"}, 500)


def _principal(request: HttpRequest, roles: set[str], *, require_unrestricted: bool = True) -> Principal:
    principal = verify_principal(request)
    if principal.role not in roles:
        raise PrincipalEnvelopeError("当前角色无权访问", status=403, code="insufficient_role")
    if require_unrestricted and principal.scope is not None:
        raise PrincipalEnvelopeError("库存接口仅支持未受限数据范围账号", status=403, code="access_denied")
    return principal


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryApiError("请求 JSON 包含重复字段")
        result[key] = value
    return result


def _body(request: HttpRequest) -> dict[str, object]:
    if not JSON_CONTENT_TYPE_RE.match(request.headers.get("Content-Type", "")):
        raise InventoryApiError("Django 库存接口只接受 application/json", status=415)
    try:
        payload = json.loads(request.body.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=lambda _value: (_ for _ in ()).throw(InventoryApiError("请求 JSON 包含非有限数字")))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InventoryApiError("请求内容不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise InventoryApiError("请求内容必须是 JSON 对象")
    return payload


def _consistent_read(loader: Callable[[], dict[str, object]]) -> tuple[dict[str, object], str]:
    for _attempt in range(2):
        before = revision_value()
        payload = loader()
        after = revision_value()
        if before == after:
            return payload, after
    raise InventoryApiError("库存数据版本持续变化，请稍后重试", code="service_unavailable", status=503)


def _replay_write(request: HttpRequest, principal: Principal, callback: Callable[[], tuple[dict[str, object], int]]) -> JsonResponse:
    claim = claim_write_request(
        request_id=request.headers.get("X-Teruisi-Request-Id", "").strip(),
        actor_email=principal.email.strip().lower(),
        method=request.method,
        path=request.path,
        body_sha256=request.headers.get("X-Teruisi-Content-SHA256", "").strip().lower(),
        query_sha256=hashlib.sha256(request.META.get("QUERY_STRING", "").encode()).hexdigest(),
    )
    if claim.replay_payload is not None and claim.replay_status is not None:
        return _json(
            claim.replay_payload,
            claim.replay_status,
            revision=revision_value(),
            replayed=True,
        )
    try:
        payload, status = callback()
        complete_write_request(claim, response_status=status, response_payload=payload)
        return _json(payload, status, revision=revision_value())
    except Exception:
        fail_write_request(claim)
        raise


def _unknown(request: HttpRequest, allowed: set[str], label: str) -> None:
    if any(key not in allowed for key in request.GET):
        raise InventoryApiError(f"{label}包含未知查询参数")


def _one(request: HttpRequest, key: str) -> str | None:
    values = request.GET.getlist(key)
    if len(values) > 1:
        raise InventoryApiError(f"{key} 参数不能重复")
    return values[0] if values else None


def _positive(value: str | None, fallback: int, label: str, maximum: int) -> int:
    if value is None:
        return fallback
    if not re.fullmatch(r"[1-9]\d*", value) or int(value) > maximum:
        raise InventoryApiError(f"{label} 超出允许范围")
    return int(value)


def _body_text(payload: dict[str, object], key: str, label: str, maximum: int) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum:
        raise InventoryApiError(f"{label}无效")
    return value.strip()


def _body_date(payload: dict[str, object], key: str, label: str) -> date | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InventoryApiError(f"{label}无效")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InventoryApiError(f"{label}无效") from error
    if parsed.isoformat() != value:
        raise InventoryApiError(f"{label}无效")
    return parsed


REPLENISHMENT_IMPORT_ROW_KEYS = {
    "productCode", "warehouse", "plannedQuantity", "buyer", "operatorName",
    "department", "planType", "orderDate", "expectedArrivalDate",
    "expectedConsumptionDays", "status", "requiresInspection", "notes",
}


def _replenishment_import_row(value: object, row_number: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != REPLENISHMENT_IMPORT_ROW_KEYS:
        raise InventoryApiError(f"第{row_number}行备货计划字段不完整")
    product_code = _body_text(value, "productCode", f"第{row_number}行货品编码", 100)
    warehouse = _body_text(value, "warehouse", f"第{row_number}行入库库房", 100)
    if not product_code or not warehouse:
        raise InventoryApiError(f"第{row_number}行货品编码和入库库房不能为空")
    quantity = value.get("plannedQuantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10_000_000:
        raise InventoryApiError(f"第{row_number}行备货数量必须是1到10,000,000之间的整数")
    status = value.get("status")
    if status not in {"draft", "confirmed"}:
        raise InventoryApiError(f"第{row_number}行计划状态无效")
    inspection = value.get("requiresInspection")
    if not isinstance(inspection, bool):
        raise InventoryApiError(f"第{row_number}行是否验货无效")
    days = value.get("expectedConsumptionDays")
    if days is not None and (
        isinstance(days, bool)
        or not isinstance(days, (int, float))
        or not math.isfinite(float(days))
        or not 0 <= float(days) <= 3_650
        or abs(float(days) * 10 - round(float(days) * 10)) >= 1e-9
    ):
        raise InventoryApiError(f"第{row_number}行预计消耗周期无效")
    return {
        "productCode": product_code,
        "warehouse": warehouse,
        "plannedQuantity": quantity,
        "buyer": _body_text(value, "buyer", f"第{row_number}行对应采购", 200),
        "operatorName": _body_text(value, "operatorName", f"第{row_number}行对应运营", 200),
        "department": _body_text(value, "department", f"第{row_number}行部门", 200),
        "planType": _body_text(value, "planType", f"第{row_number}行备货类型", 100),
        "orderDate": _body_date(value, "orderDate", f"第{row_number}行下单日期"),
        "expectedArrivalDate": _body_date(value, "expectedArrivalDate", f"第{row_number}行预计到货日"),
        "expectedConsumptionDays": round(float(days), 1) if days is not None else None,
        "status": status,
        "requiresInspection": inspection,
        "notes": _body_text(value, "notes", f"第{row_number}行备注", 1_000),
    }


def _selections(request: HttpRequest, key: str, maximum: int, allowed: set[str] | None = None) -> list[str]:
    values = list(dict.fromkeys(value.strip() for value in request.GET.getlist(key) if value.strip()))
    if len(values) > maximum or any(len(value) > 120 for value in values) or (allowed and any(value not in allowed for value in values)):
        raise InventoryApiError(f"{key} 筛选无效")
    return values


def _overview_options(request: HttpRequest) -> dict[str, object]:
    allowed = {"view", "startDate", "endDate", "q", "warehouse", "brand", "category", "warehouseType", "status", "page", "pageSize", "planPage", "planPageSize", "planStatus", "includeCancelledPlans"}
    _unknown(request, allowed, "库存总览")
    view = _one(request, "view") or "full"
    if view not in {"full", "dashboard", "overview", "plan"}:
        raise InventoryApiError("view 必须是 full、dashboard、overview 或 plan")
    plan_status = _one(request, "planStatus")
    if plan_status is not None and plan_status not in {"draft", "confirmed", "completed", "cancelled"}:
        raise InventoryApiError("planStatus 无效")
    include_cancelled = _one(request, "includeCancelledPlans")
    if include_cancelled not in {None, "true", "false"}:
        raise InventoryApiError("includeCancelledPlans 必须是 true 或 false")
    query = _one(request, "q")
    if query and len(query.strip()) > 100:
        raise InventoryApiError("搜索词不能超过 100 个字符")
    return {
        "view": view, "startDate": _one(request, "startDate"), "endDate": _one(request, "endDate"), "query": query.strip() if query else None,
        "warehouses": _selections(request, "warehouse", 10), "brands": _selections(request, "brand", 20), "categories": _selections(request, "category", 20),
        "warehouseTypes": _selections(request, "warehouseType", 3, {"owned", "jd_rdc", "other"}), "statuses": _selections(request, "status", 6, set(("no_stock", "urgent", "warning", "stale", "slow", "healthy"))),
        "page": _positive(_one(request, "page"), 1, "page", 10_000), "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100),
        "planPage": _positive(_one(request, "planPage"), 1, "planPage", 10_000), "planPageSize": _positive(_one(request, "planPageSize"), 50, "planPageSize", 100),
        "planStatus": plan_status, "includeCancelledPlans": include_cancelled == "true" or plan_status == "cancelled",
    }


@require_GET
def overview(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        payload, revision = _consistent_read(lambda: inventory_overview(principal, _overview_options(request)))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取库存健康数据失败")


@require_GET
def age_analysis(request: HttpRequest) -> JsonResponse:
    try:
        _principal(request, {"viewer", "analyst", "operator", "admin"})
        _unknown(request, {"cardFilter", "q", "warehouse", "brand", "category", "status", "ageBucket", "page", "pageSize"}, "库龄分析")
        options = {"query": _one(request, "q"), "warehouses": _selections(request, "warehouse", 10), "brands": _selections(request, "brand", 20), "categories": _selections(request, "category", 20), "statuses": _selections(request, "status", 5, {"healthy", "aged", "slow", "stagnant", "no_stock"}), "ageBuckets": _selections(request, "ageBucket", 10, {"0-7", "8-15", "16-30", "31-60", "61-90", "91-120", "121-150", "151-180", "181-360", "361+"}), "page": _positive(_one(request, "page"), 1, "page", 10_000), "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100)}
        card_filter = _one(request, "cardFilter") or ""
        if card_filter and card_filter not in {"stagnant", "aged90", "zero_sales"}:
            raise InventoryApiError("统计卡片筛选无效")
        options["cardFilter"] = card_filter
        payload, revision = _consistent_read(lambda: inventory_age_analysis(options))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取库龄分析数据失败")


@require_GET
def inbound_monitor(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"viewer", "analyst", "operator", "admin"})
        _unknown(request, {"cardFilter", "q", "warehouse", "brand", "category", "supplier", "page", "pageSize"}, "京东入仓监控")
        options = {"query": _one(request, "q"), "warehouses": _selections(request, "warehouse", 10), "brands": _selections(request, "brand", 20), "categories": _selections(request, "category", 20), "suppliers": _selections(request, "supplier", 20), "page": _positive(_one(request, "page"), 1, "page", 10_000), "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100)}
        card_filter = _one(request, "cardFilter") or ""
        if card_filter and card_filter not in {"stale"}:
            raise InventoryApiError("统计卡片筛选无效")
        options["cardFilter"] = card_filter
        payload, revision = _consistent_read(lambda: inventory_inbound_monitor(principal, options))
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取京东入仓库存监控失败")


@require_http_methods(["GET", "POST"])
def imports(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, {"dataset", "batchId", "page", "pageSize", "limit"}, "库存导入批次")
            dataset = _one(request, "dataset")
            if dataset not in {None, "stock", "age"}:
                raise InventoryApiError("dataset 必须是 stock 或 age")
            page_size = _positive(_one(request, "pageSize") or _one(request, "limit"), 50, "pageSize", 100)
            payload, revision = _consistent_read(lambda: list_import_batches(dataset=dataset, page=_positive(_one(request, "page"), 1, "page", 10_000), page_size=page_size, batch_id=_one(request, "batchId") or ""))
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        payload = _body(request)
        return _replay_write(request, principal, lambda: ((result := record_edge_rejection(payload, principal.email.strip().lower()) if payload.get("action") == "reject" else import_inventory_payload(payload, principal.email.strip().lower())), 201 if result.get("status") == "imported" else 200))
    except Exception as error:
        return _error(error, "库存导入失败", import_shape=request.method == "POST")


@require_POST
def consumer_query(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(
            request,
            {"viewer", "analyst", "operator", "admin"},
            require_unrestricted=False,
        )
        consumer = validate_consumer_request(_body(request))
        payload, revision = _consistent_read(lambda: {"operation": consumer["operation"], "data": execute_consumer_query(principal, consumer)})
        return _json(payload, revision=revision)
    except Exception as error:
        return _error(error, "读取库存消费数据失败")


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
        return _error(error, "库存分片上传处理失败", import_shape=True)


@require_http_methods(["GET", "PUT"])
def upload_chunk(request: HttpRequest) -> HttpResponse:
    try:
        principal = _principal(request, {"admin"})
        upload_id = request.headers.get("X-Upload-Id", "").strip(); index_text = request.headers.get("X-Chunk-Index", "").strip()
        if not re.fullmatch(r"0|[1-9]\d*", index_text):
            raise InventoryApiError("分片序号无效")
        index = int(index_text)
        if request.method == "GET":
            payload, digest = read_chunk(upload_id, index, request.headers.get("X-Upload-Owner-Token", ""), principal.email.strip().lower())
            response = HttpResponse(payload, content_type="application/octet-stream"); response["Cache-Control"] = "no-store"; response["X-Chunk-SHA256"] = digest
            return response
        if len(request.body) > CHUNK_SIZE_BYTES:
            raise InventoryApiError("单个分片不能超过 1MB", code="payload_too_large", status=413)
        return _replay_write(request, principal, lambda: ({"ok": True, "status": "uploading", **receive_chunk(upload_id, index, request.body, principal.email.strip().lower())}, 200))
    except Exception as error:
        return _error(error, "库存分片上传失败", import_shape=True)


@require_http_methods(["GET", "POST", "PATCH"])
def replenishment(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            _unknown(request, {"status", "includeCancelled", "q", "page", "pageSize"}, "备货计划")
            status = _one(request, "status"); include = _one(request, "includeCancelled")
            if status not in {None, "draft", "confirmed", "completed", "cancelled"} or include not in {None, "true", "false"}:
                raise InventoryApiError("备货计划筛选无效")
            options = {"status": status, "includeCancelled": include == "true", "query": _one(request, "q"), "page": _positive(_one(request, "page"), 1, "page", 10_000), "pageSize": _positive(_one(request, "pageSize"), 50, "pageSize", 100)}
            payload, revision = _consistent_read(lambda: {**query_plans(options), "summary": plan_summary(None)})
            return _json(payload, revision=revision)
        principal = _principal(request, {"operator", "admin"}); body = _body(request)
        if request.method == "POST":
            allowed = {
                "key", "plannedQuantity", "acknowledgeStale", "manual", "startDate", "endDate",
                "buyer", "operatorName", "department", "planType", "orderDate",
                "expectedArrivalDate", "expectedConsumptionDays", "status", "requiresInspection", "notes",
            }
            if not set(body).issubset(allowed) or not isinstance(body.get("key"), str):
                raise InventoryApiError("创建备货计划请求无效")
            key = str(body["key"]); parts = key.split("\x1f")
            if len(parts) != 2 or any(not value.strip() or len(value) > 100 for value in parts):
                raise InventoryApiError("库存建议标识必须精确包含仓库与货品编码")
            requested = body.get("plannedQuantity")
            if requested is not None and (isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= 10_000_000):
                raise InventoryApiError("计划补货量必须是 1 到 10,000,000 之间的整数")
            manual = body.get("manual", False)
            if not isinstance(manual, bool):
                raise InventoryApiError("manual 必须是布尔值")
            if manual and requested is None:
                raise InventoryApiError("人工创建备货计划必须填写备货数量")
            requested_status = body.get("status", "draft")
            if requested_status not in {"draft", "confirmed"}:
                raise InventoryApiError("新建备货计划状态只能是草稿或已确认")
            requires_inspection = body.get("requiresInspection", False)
            if not isinstance(requires_inspection, bool):
                raise InventoryApiError("是否验货必须是布尔值")
            expected_consumption_days_supplied = "expectedConsumptionDays" in body
            expected_consumption_days = body.get("expectedConsumptionDays")
            if expected_consumption_days is not None:
                if (
                    isinstance(expected_consumption_days, bool)
                    or not isinstance(expected_consumption_days, (int, float))
                    or not math.isfinite(float(expected_consumption_days))
                    or not 0 <= float(expected_consumption_days) <= 3_650
                    or abs(float(expected_consumption_days) * 10 - round(float(expected_consumption_days) * 10)) >= 1e-9
                ):
                    raise InventoryApiError("预计消耗周期必须是 0 到 3,650 天之间、最多一位小数的数字")
                expected_consumption_days = round(float(expected_consumption_days), 1)
            details = {
                "buyer": _body_text(body, "buyer", "对应采购", 200),
                "operatorName": _body_text(body, "operatorName", "对应运营", 200),
                "department": _body_text(body, "department", "部门", 200),
                "planType": _body_text(body, "planType", "备货类型", 100),
                "orderDate": _body_date(body, "orderDate", "下单日期"),
                "expectedArrivalDate": _body_date(body, "expectedArrivalDate", "预计到货日"),
                "status": requested_status,
                "requiresInspection": requires_inspection,
                "notes": _body_text(body, "notes", "备注", 1_000),
            }
            def create() -> tuple[dict[str, object], int]:
                overview_data = inventory_overview(principal, {"view": "overview", "exactKey": key, "startDate": body.get("startDate"), "endDate": body.get("endDate"), "page": 1, "pageSize": 1})
                if not manual and not overview_data["controls"]["autoReplenishmentEnabled"]:
                    raise InventoryApiError("系统设置已关闭自动补货建议，请由管理员开启后再创建计划", code="conflict", status=409)
                if not manual and overview_data["quality"]["recommendationsSuppressed"]:
                    raise InventoryApiError("库存数据质量门禁未通过，已暂停创建精确补货计划", code="conflict", status=409)
                if overview_data["sync"]["inventoryStale"] and body.get("acknowledgeStale") is not True:
                    raise InventoryApiError("库存快照已过期，请先同步或明确确认继续", code="conflict", status=409)
                if not overview_data["items"]:
                    raise InventoryApiError("当前库存快照中未找到该货品与仓库", code="not_found", status=404)
                item = overview_data["items"][0]; suggested = item["suggestedQuantity"]
                if not manual and (suggested is None or int(suggested) <= 0):
                    raise InventoryApiError("当前没有可创建的精确补货量", code="conflict", status=409)
                plan = upsert_plan({
                    "sourceBatchId": overview_data["sync"]["latestInventoryBatchId"],
                    "productCode": item["productCode"], "productName": item["productName"],
                    "brand": item["brand"], "category": item["category"], "supplier": item["supplier"],
                    "warehouse": item["warehouse"], "suggestedQuantity": suggested if suggested is not None else 0,
                    "plannedQuantity": requested if requested is not None else suggested,
                    "coverageDays": expected_consumption_days if expected_consumption_days_supplied else item["coverageDays"],
                    "currentStockQuantity": item["availableQuantity"],
                    "sales30dQuantity": item.get("productSales30d"),
                    "reason": f"人工创建备货计划；{item['reason']}" if manual else item["reason"], **details,
                }, principal.email)
                return {"ok": True, "item": plan_payload(plan)}, 201
            return _replay_write(request, principal, create)
        if set(body) - {"id", "status", "plannedQuantity"} or not isinstance(body.get("id"), str) or body.get("status") not in {"draft", "confirmed", "completed", "cancelled"}:
            raise InventoryApiError("更新备货计划请求无效")
        quantity = body.get("plannedQuantity")
        if quantity is not None and (isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10_000_000):
            raise InventoryApiError("计划补货量必须是 1 到 10,000,000 之间的整数")
        def patch() -> tuple[dict[str, object], int]:
            plan = update_plan(str(body["id"]), str(body["status"]), quantity)
            if plan is None:
                raise InventoryApiError("备货计划不存在", code="not_found", status=404)
            return {"ok": True, "item": plan_payload(plan)}, 200
        return _replay_write(request, principal, patch)
    except Exception as error:
        return _error(error, "备货计划处理失败")


@require_POST
def replenishment_import(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        body = _body(request)
        if set(body) != {"rows", "acknowledgeStale", "fileSha256"}:
            raise InventoryApiError("备货计划导入请求无效")
        raw_rows = body.get("rows")
        if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= 200:
            raise InventoryApiError("单次必须导入1到200行备货计划")
        acknowledge_stale = body.get("acknowledgeStale")
        if not isinstance(acknowledge_stale, bool):
            raise InventoryApiError("acknowledgeStale 必须是布尔值")
        file_sha256 = body.get("fileSha256")
        if not isinstance(file_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", file_sha256):
            raise InventoryApiError("备货计划导入文件摘要无效")
        rows = [_replenishment_import_row(value, index + 2) for index, value in enumerate(raw_rows)]
        seen: set[str] = set()
        for index, row in enumerate(rows, start=2):
            key = f"{row['warehouse']}\x1f{row['productCode']}"
            if key in seen:
                raise InventoryApiError(f"第{index}行与前面行重复：同一货品编码和入库库房只能出现一次")
            seen.add(key)

        def create() -> tuple[dict[str, object], int]:
            sources = replenishment_plan_sources(principal)
            if not sources["latestBatchId"]:
                raise InventoryApiError("当前没有可用于创建备货计划的库存快照", code="conflict", status=409)
            if sources["inventoryStale"] and not acknowledge_stale:
                raise InventoryApiError("库存快照已过期，请先同步或明确确认继续", code="conflict", status=409)
            by_key = sources["itemsByKey"]
            if not isinstance(by_key, dict):
                raise InventoryApiError("当前库存快照无法生成备货计划", code="service_unavailable", status=503)
            payloads: list[dict[str, object]] = []
            for index, row in enumerate(rows, start=2):
                key = f"{row['warehouse']}\x1f{row['productCode']}"
                item = by_key.get(key)
                if not isinstance(item, dict):
                    raise InventoryApiError(
                        f"第{index}行在最新库存快照中未找到该货品编码与入库库房",
                        code="not_found",
                        status=404,
                    )
                coverage = row["expectedConsumptionDays"] if row["expectedConsumptionDays"] is not None else item.get("coverageDays")
                payloads.append({
                    "sourceBatchId": sources["latestBatchId"],
                    "productCode": item["productCode"],
                    "productName": item["productName"],
                    "brand": item["brand"],
                    "category": item["category"],
                    "supplier": item["supplier"],
                    "warehouse": item["warehouse"],
                    "buyer": row["buyer"],
                    "operatorName": row["operatorName"],
                    "department": row["department"],
                    "planType": row["planType"],
                    "orderDate": row["orderDate"],
                    "expectedArrivalDate": row["expectedArrivalDate"],
                    "requiresInspection": row["requiresInspection"],
                    "currentStockQuantity": item["availableQuantity"],
                    "sales30dQuantity": item.get("productSales30d"),
                    "suggestedQuantity": item.get("suggestedQuantity") or 0,
                    "plannedQuantity": row["plannedQuantity"],
                    "coverageDays": coverage,
                    "reason": f"表格导入备货计划；{item['reason']}",
                    "notes": row["notes"],
                    "status": row["status"],
                })
            plans = import_plans(payloads, principal.email, file_sha256, str(sources["latestBatchId"]))
            confirmed_ids = [plan.id for plan in plans if plan.status == "confirmed"]
            return {
                "ok": True,
                "status": "imported",
                "importedCount": len(plans),
                "confirmedCount": len(confirmed_ids),
                "draftCount": len(plans) - len(confirmed_ids),
                "confirmedPlanIds": confirmed_ids,
                "items": [plan_payload(plan) for plan in plans],
                "inventoryAsOf": sources["inventoryAsOf"],
            }, 201

        return _replay_write(request, principal, create)
    except Exception as error:
        return _error(error, "备货计划导入失败", import_shape=True)


@require_POST
def replenishment_dingtalk(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        body = _body(request)
        if set(body) != {"id"} or not isinstance(body.get("id"), str):
            raise InventoryApiError("创建钉钉备货计划请求无效")
        plan_id = str(body["id"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", plan_id):
            raise InventoryApiError("备货计划 ID 无效")
        return _replay_write(
            request,
            principal,
            lambda: (sync_replenishment_plan(plan_id, principal.email.strip().lower()), 200),
        )
    except Exception as error:
        return _error(error, "创建钉钉备货计划失败")


@require_POST
def replenishment_dingtalk_group(request: HttpRequest) -> JsonResponse:
    try:
        principal = _principal(request, {"operator", "admin"})
        body = _body(request)
        action = body.get("action")
        common_keys = {"action", "planIds", "targetGroupName", "robotName"}
        if action == "preview":
            if set(body) != common_keys:
                raise InventoryApiError("备货群消息预览请求无效")
            return _json(
                preview_group_message(
                    body.get("planIds"), body.get("targetGroupName"), body.get("robotName"),
                ),
                revision=revision_value(),
            )
        if action == "send":
            if set(body) != common_keys | {"previewToken"}:
                raise InventoryApiError("备货群消息发送请求无效")
            return _replay_write(
                request,
                principal,
                lambda: (
                    send_group_message(
                        body.get("planIds"),
                        body.get("targetGroupName"),
                        body.get("robotName"),
                        body.get("previewToken"),
                        principal.email.strip().lower(),
                    ),
                    200,
                ),
            )
        raise InventoryApiError("备货群消息操作无效")
    except Exception as error:
        return _error(error, "备货群消息处理失败")


@require_http_methods(["GET", "PUT"])
def settings_view(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            payload, revision = _consistent_read(read_settings)
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"}); payload = _body(request)
        return _replay_write(request, principal, lambda: (update_settings(payload, principal.email), 200))
    except Exception as error:
        return _error(error, "系统设置处理失败")


@require_http_methods(["GET", "PUT"])
def warehouse_mappings(request: HttpRequest) -> JsonResponse:
    try:
        if request.method == "GET":
            _principal(request, {"viewer", "analyst", "operator", "admin"})
            payload, revision = _consistent_read(mapping_payload)
            return _json(payload, revision=revision)
        principal = _principal(request, {"admin"})
        payload = _body(request)
        return _replay_write(
            request,
            principal,
            lambda: (update_mapping(payload, principal.email), 200),
        )
    except Exception as error:
        return _error(error, "仓库映射处理失败")
