from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from urllib.parse import quote

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from .errors import NetshopApiError
from .models import (
    NetshopDataRevision,
    NetshopImportAttempt,
    NetshopImportBatch,
    NetshopImportFingerprint,
    NetshopImportScopeHead,
    NetshopProductDailyRevision,
    NetshopProductDailyScopeRevision,
    NetshopPromotionAggregateControl,
    NetshopPromotionAggregateManifest,
    NetshopPromotionAggregateState,
    NetshopPromotionProductDaily,
    NetshopPromotionScopeRevision,
    NetshopPromotionShopDaily,
    NetshopRow,
    NetshopWriteAuthority,
)
from .serialization import batch_payload


logger = logging.getLogger(__name__)
NETSHOP_SCHEMA_VERSION = "netshop-normalized-v1"
MAX_IMPORT_ROWS = 50_000
MAX_WARNING_COUNT = 200
MAX_ERROR_COUNT = 200
MAX_JSON_KEYS = 500
MAX_TEXT = 20_000
JS_SAFE_INTEGER = 9_007_199_254_740_991
HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
ISO_DATE_RE = re.compile(r"^(?:19|20|21)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")
SUPPORTED_SOURCES = frozenset(
    {
        "jd_shop_overview",
        "jd_sku_daily",
        "jd_promotion",
        "jd_b2b",
        "jd_product_master",
        "jd_cs",
        "jd_yimei_sku",
        "tmall_product_master",
        "tmall_product_assets",
        "tmall_product_daily",
        "tmall_promotion",
        "inv_selfop",
    }
)
SOURCE_DATASETS: dict[str, frozenset[str]] = {
    "jd_shop_overview": frozenset({"trade_overview"}),
    "jd_sku_daily": frozenset({"sku_daily", "spu_daily"}),
    "jd_promotion": frozenset({"ad"}),
    "jd_b2b": frozenset({"b2b"}),
    "jd_product_master": frozenset({"product_master"}),
    "jd_cs": frozenset({"cs", "cs_suggest", "cs_order", "cs_chat"}),
    "jd_yimei_sku": frozenset({"yimei_sku"}),
    "tmall_product_master": frozenset({"product_master"}),
    "tmall_product_assets": frozenset({"spu_assets"}),
    "tmall_product_daily": frozenset({"spu_daily"}),
    "tmall_promotion": frozenset({"promotion_daily"}),
    "inv_selfop": frozenset({"inv_selfop"}),
}
SNAPSHOT_SOURCES = frozenset(
    {"inv_selfop", "jd_product_master", "tmall_product_master", "tmall_product_assets"}
)
ROW_FIELDS = frozenset(
    {
        "sourceRowNumber",
        "sourceRowKey",
        "sourceRowHash",
        "source",
        "dataset",
        "platform",
        "shopName",
        "businessDate",
        "snapshotDate",
        "productCode",
        "productName",
        "skuId",
        "spuId",
        "warehouseType",
        "metrics",
        "raw",
    }
)


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise NetshopApiError(f"{label}必须是字符串")
    normalized = value.strip()
    if (required and not normalized) or len(normalized) > maximum:
        raise NetshopApiError(f"{label}长度超出限制")
    if "\x00" in normalized:
        raise NetshopApiError(f"{label}包含无效字符")
    return normalized


def _integer(
    value: object,
    label: str,
    *,
    minimum: int = -JS_SAFE_INTEGER,
    maximum: int = JS_SAFE_INTEGER,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NetshopApiError(f"{label}必须是安全整数")
    if value < minimum or value > maximum:
        raise NetshopApiError(f"{label}超出安全范围")
    return value


def _number(value: object, label: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NetshopApiError(f"{label}必须是有限数值")
    if not math.isfinite(value) or abs(value) > JS_SAFE_INTEGER:
        raise NetshopApiError(f"{label}超出安全范围")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _iso_date(value: object, label: str, *, empty: bool = True) -> str:
    normalized = _text(value, label, 10, required=not empty)
    if not normalized and empty:
        return ""
    if not ISO_DATE_RE.fullmatch(normalized):
        raise NetshopApiError(f"{label}必须是有效的 YYYY-MM-DD 自然日")
    try:
        if date.fromisoformat(normalized).isoformat() != normalized:
            raise ValueError
    except ValueError as error:
        raise NetshopApiError(f"{label}必须是有效的 YYYY-MM-DD 自然日") from error
    return normalized


def _json_value(value: object, label: str, *, depth: int = 0) -> object:
    if depth > 8:
        raise NetshopApiError(f"{label}嵌套层级过深")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise NetshopApiError(f"{label}文本字段超出限制")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _number(value, label)
    if isinstance(value, list):
        if len(value) > MAX_JSON_KEYS:
            raise NetshopApiError(f"{label}数组超出限制")
        return [_json_value(item, label, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_JSON_KEYS:
            raise NetshopApiError(f"{label}字段数量超出限制")
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 500 or "\x00" in key:
                raise NetshopApiError(f"{label}字段名无效")
            result[key] = _json_value(item, label, depth=depth + 1)
        return result
    raise NetshopApiError(f"{label}包含不支持的值类型")


def _issue(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise NetshopApiError("网店导入问题清单格式无效")
    allowed = {"row", "field", "code", "message"}
    if set(value) - allowed:
        raise NetshopApiError("网店导入问题清单包含未知字段")
    result: dict[str, object] = {
        "message": _text(value.get("message"), "问题内容", 500, required=True)
    }
    if "row" in value:
        result["row"] = _integer(value["row"], "问题行号", minimum=1, maximum=10_000_000)
    if "field" in value:
        result["field"] = _text(value["field"], "问题字段", 100)
    if "code" in value:
        result["code"] = _text(value["code"], "问题代码", 100)
    return result


def _normalize_row(value: object, context: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != ROW_FIELDS:
        raise NetshopApiError("网店规范化行字段与 netshop-normalized-v1 不一致")
    source = _text(value["source"], "行来源", 64, required=True)
    dataset = _text(value["dataset"], "行数据集", 64, required=True)
    platform = _text(value["platform"], "行平台", 100, required=True)
    shop_name = _text(value["shopName"], "行店铺", 100, required=True)
    if (
        source != context["source"]
        or dataset != context["dataset"]
        or platform != context["platform"]
        or shop_name != context["shopName"]
    ):
        raise NetshopApiError("规范化行的来源、数据集、平台或店铺与请求范围不一致")
    business_date = _iso_date(value["businessDate"], "业务日期")
    snapshot_date = _iso_date(value["snapshotDate"], "快照日期")
    if source in SNAPSHOT_SOURCES:
        if snapshot_date != context["snapshotDate"]:
            raise NetshopApiError("快照行日期与请求快照范围不一致")
        if business_date:
            raise NetshopApiError("商品/库存快照不能写入经营业务日期")
    elif snapshot_date:
        raise NetshopApiError("非快照数据不能携带快照日期")
    if dataset in {"sku_daily", "spu_daily", "promotion_daily"} and not business_date:
        raise NetshopApiError("分天数据缺少业务日期", status=422)
    sku_id = _text(value["skuId"], "SKU", 2_000)
    spu_id = _text(value["spuId"], "SPU", 2_000)
    if dataset == "sku_daily" and not sku_id:
        raise NetshopApiError("SKU 分天数据缺少 SKU", status=422)
    if dataset in {"spu_daily", "spu_assets", "promotion_daily"} and not spu_id:
        raise NetshopApiError("SPU 数据缺少 SPU/主体 ID", status=422)
    metrics = _json_value(value["metrics"], "指标")
    raw = _json_value(value["raw"], "原始业务字段")
    if not isinstance(metrics, dict) or not isinstance(raw, dict):
        raise NetshopApiError("指标和原始业务字段必须是 JSON 对象")
    row_hash = _text(value["sourceRowHash"], "源行哈希", 64, required=True).lower()
    if not HEX_64_RE.fullmatch(row_hash):
        raise NetshopApiError("源行哈希必须是 64 位小写 SHA-256")
    return {
        "sourceRowNumber": _integer(
            value["sourceRowNumber"], "源行号", minimum=1, maximum=10_000_000
        ),
        "sourceRowKey": _text(value["sourceRowKey"], "源行业务键", 20_000, required=True),
        "sourceRowHash": row_hash,
        "source": source,
        "dataset": dataset,
        "platform": platform,
        "shopName": shop_name,
        "businessDate": business_date,
        "snapshotDate": snapshot_date,
        "productCode": _text(value["productCode"], "货品编码", 2_000),
        "productName": _text(value["productName"], "商品名称", 8_000),
        "skuId": sku_id,
        "spuId": spu_id,
        "warehouseType": _text(value["warehouseType"], "仓库类型", 100),
        "metrics": metrics,
        "raw": raw,
    }


def validate_import_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise NetshopApiError("请求内容不是有效 JSON")
    allowed = {
        "schemaVersion",
        "disposition",
        "fileName",
        "fileSizeBytes",
        "rawFileHash",
        "source",
        "dataset",
        "platform",
        "shopName",
        "sheetName",
        "note",
        "snapshotDate",
        "startDate",
        "endDate",
        "rows",
        "warnings",
        "errors",
        "message",
        "parserTotals",
        "imagePersistence",
    }
    if set(payload) - allowed:
        raise NetshopApiError("网店导入请求包含未知字段")
    if payload.get("schemaVersion") != NETSHOP_SCHEMA_VERSION:
        raise NetshopApiError("网店规范化契约版本不受支持", status=422)
    disposition = payload.get("disposition")
    if disposition not in {"prepared", "rejected"}:
        raise NetshopApiError("网店导入处置类型无效")
    file_name = _text(payload.get("fileName"), "文件名", 1_000, required=True)
    file_size = _integer(
        payload.get("fileSizeBytes"), "文件大小", minimum=0, maximum=128 * 1024 * 1024
    )
    raw_hash = _text(payload.get("rawFileHash"), "原文件哈希", 64, required=True).lower()
    if not HEX_64_RE.fullmatch(raw_hash):
        raise NetshopApiError("原文件哈希必须是 64 位小写 SHA-256")
    source = _text(payload.get("source", ""), "来源", 64)
    platform = _text(payload.get("platform", ""), "平台", 100)
    shop_name = _text(payload.get("shopName", ""), "店铺", 100)
    warnings_value = payload.get("warnings", [])
    if not isinstance(warnings_value, list) or len(warnings_value) > MAX_WARNING_COUNT:
        raise NetshopApiError("网店导入告警数量超出限制")
    warnings = [_issue(item) for item in warnings_value]
    base: dict[str, object] = {
        "schemaVersion": NETSHOP_SCHEMA_VERSION,
        "disposition": disposition,
        "fileName": file_name,
        "fileSizeBytes": file_size,
        "rawFileHash": raw_hash,
        "source": source,
        "platform": platform,
        "shopName": shop_name,
        "warnings": warnings,
    }
    if disposition == "rejected":
        errors_value = payload.get("errors")
        if not isinstance(errors_value, list) or not 1 <= len(errors_value) <= MAX_ERROR_COUNT:
            raise NetshopApiError("被拒绝网店导入必须携带有界错误清单")
        base["errors"] = [_issue(item) for item in errors_value]
        base["message"] = _text(payload.get("message"), "拒绝原因", 500, required=True)
        return base

    if source not in SUPPORTED_SOURCES:
        raise NetshopApiError("网店来源不受支持", status=422)
    dataset = _text(payload.get("dataset"), "数据集", 64, required=True)
    if dataset not in SOURCE_DATASETS[source]:
        raise NetshopApiError("来源与数据集不匹配", status=422)
    if platform not in {"京东", "天猫"} or not shop_name:
        raise NetshopApiError("网店平台或店铺身份无效", status=422)
    if source.startswith("tmall_") != (platform == "天猫"):
        raise NetshopApiError("来源与平台身份不匹配", status=422)
    snapshot_date = _iso_date(payload.get("snapshotDate", ""), "快照日期")
    if source in SNAPSHOT_SOURCES and not snapshot_date:
        raise NetshopApiError("快照来源必须提供有效快照日期", status=422)
    if source not in SNAPSHOT_SOURCES and snapshot_date:
        raise NetshopApiError("非快照来源不能提供快照日期", status=422)
    start_date = _iso_date(payload.get("startDate", ""), "开始日期")
    end_date = _iso_date(payload.get("endDate", ""), "结束日期")
    if bool(start_date) != bool(end_date) or (start_date and start_date > end_date):
        raise NetshopApiError("开始日期和结束日期必须同时提供且顺序有效", status=422)
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= MAX_IMPORT_ROWS:
        raise NetshopApiError("规范化网店数据必须包含 1 到 50000 行", status=422)
    context = {
        "source": source,
        "dataset": dataset,
        "platform": platform,
        "shopName": shop_name,
        "snapshotDate": snapshot_date,
    }
    rows = [_normalize_row(item, context) for item in raw_rows]
    keys = [str(row["sourceRowKey"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise NetshopApiError("规范化网店数据包含重复业务行身份", status=422)
    dates = sorted({str(row["businessDate"]) for row in rows if row["businessDate"]})
    if start_date and any(item < start_date or item > end_date for item in dates):
        raise NetshopApiError("规范化数据包含目标日期范围外的业务日期", status=422)
    if dataset in {"sku_daily", "spu_daily", "promotion_daily"}:
        if not start_date or not end_date:
            raise NetshopApiError("分天数据必须绑定目标起止日期", status=422)
        expected: list[str] = []
        cursor = date.fromisoformat(start_date)
        last = date.fromisoformat(end_date)
        if (last - cursor).days + 1 > 730:
            raise NetshopApiError("分天导入范围最多 730 天", status=422)
        while cursor <= last:
            expected.append(cursor.isoformat())
            cursor += timedelta(days=1)
        missing = sorted(set(expected) - set(dates))
        if missing:
            sample = "、".join(missing[:20])
            raise NetshopApiError(f"目标区间缺少业务日期：{sample}", status=422)
    parser_totals = _json_value(payload.get("parserTotals", {}), "解析统计")
    image_persistence = _json_value(payload.get("imagePersistence", {}), "图片回查")
    if not isinstance(parser_totals, dict) or not isinstance(image_persistence, dict):
        raise NetshopApiError("解析统计或图片回查必须是 JSON 对象")
    base.update(
        {
            "dataset": dataset,
            "sheetName": _text(payload.get("sheetName", ""), "工作表", 1_000),
            "note": _text(payload.get("note", ""), "备注", 4_000),
            "snapshotDate": snapshot_date,
            "startDate": start_date,
            "endDate": end_date,
            "rows": rows,
            "parserTotals": parser_totals,
            "imagePersistence": image_persistence,
        }
    )
    return base


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NetshopApiError("规范化网店数据包含非有限数字")
        if value == 0:
            return 0
        return int(value) if value.is_integer() else value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    raise NetshopApiError("规范化网店数据包含不支持的指纹字段")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _encode_part(value: str) -> str:
    return f"{len(value.encode('utf-8'))}:{value}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _business_rows(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "source": row["source"],
            "dataset": row["dataset"],
            "platform": row["platform"],
            "shopName": row["shopName"],
            "businessDate": row["businessDate"],
            "snapshotDate": row["snapshotDate"],
            "productCode": row["productCode"],
            "productName": row["productName"],
            "skuId": row["skuId"],
            "spuId": row["spuId"],
            "warehouseType": row["warehouseType"],
            "metrics": row["metrics"],
            "raw": row["raw"],
        }
        for row in rows
    ]


def _fingerprint(
    payload: dict[str, object], rows: Sequence[dict[str, object]]
) -> tuple[str, str, str]:
    business_dates = sorted(str(row["businessDate"]) for row in rows if row["businessDate"])
    scope = {
        "source": payload["source"],
        "dataset": payload["dataset"],
        "platform": payload["platform"],
        "shopName": payload["shopName"],
        "snapshotDate": payload["snapshotDate"] or None,
        "startDate": payload["startDate"] or (business_dates[0] if business_dates else None),
        "endDate": payload["endDate"] or (business_dates[-1] if business_dates else None),
    }
    lock_scope = {
        "source": payload["source"],
        "dataset": payload["dataset"],
        "platform": payload["platform"],
        "shopName": payload["shopName"],
    }
    scope_json = _canonical_json(scope)
    lock_json = _canonical_json(lock_scope)
    scope_key = _sha256_text(
        f"import-lock-scope-v1\n{_encode_part('netshop')}{_encode_part(lock_json)}"
    )
    row_hashes = sorted(_sha256_text(_canonical_json(row)) for row in _business_rows(rows))
    content_hash = _sha256_text(
        f"import-content-v3\n{_encode_part(scope_json)}{len(row_hashes)}\n{''.join(row_hashes)}"
    )
    return scope_key, scope_json, content_hash


def _attempt_hash(scope_key: str, content_hash: str, state_token: str) -> str:
    return _sha256_text(
        "".join(
            _encode_part(value)
            for value in (
                "import-attempt-v1",
                "netshop",
                scope_key,
                content_hash,
                state_token.strip() or "initial",
            )
        )
    )


def _next_state_token(previous: str, batch_id: str, content_hash: str, row_count: int) -> str:
    return _sha256_text(
        "".join(
            _encode_part(value)
            for value in (
                "import-scope-state-v2",
                previous.strip() or "initial",
                batch_id,
                content_hash,
                str(row_count),
            )
        )
    )


def _batch_id(source: str, platform: str, shop_name: str, attempt_hash: str) -> str:
    safe = "-_.!~*'()"
    return f"{source}:{quote(platform, safe=safe)}:{quote(shop_name, safe=safe)}:{attempt_hash}"


def assert_active_authority() -> NetshopWriteAuthority:
    try:
        authority = NetshopWriteAuthority.objects.get(id=1)
    except NetshopWriteAuthority.DoesNotExist as error:
        raise NetshopApiError(
            "PostgreSQL 网店写入权威门禁尚未初始化",
            code="netshop_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise NetshopApiError(
            "PostgreSQL 尚未取得网店唯一写入权",
            code="netshop_write_authority_inactive",
            status=503,
        )
    expected_epoch = str(getattr(settings, "NETSHOP_WRITE_AUTHORITY_EPOCH", "") or "")
    expected_cutover = str(getattr(settings, "NETSHOP_WRITE_CUTOVER_ID", "") or "")
    if settings.DJANGO_PROCESS_ROLE == "netshop_writer" and (
        not expected_epoch
        or not expected_cutover
        or str(authority.authority_epoch) != expected_epoch
        or authority.cutover_id != expected_cutover
    ):
        raise NetshopApiError(
            "PostgreSQL 网店写入权威的 epoch/cutover 配置不匹配",
            code="netshop_write_authority_mismatch",
            status=503,
        )
    return authority


def _lock_scope(scope_key: str) -> None:
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(f"netshop-scope\n{scope_key}".encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def _now_text() -> str:
    return timezone.now().isoformat()


def _metric_number(metrics: Mapping[str, object], *keys: str) -> int | float | None:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
    return None


def _js_round(value: int | float) -> int:
    return math.floor(float(value) + 0.5)


def _metric_integer(
    metrics: Mapping[str, object],
    canonical: str,
    *fallbacks: str,
    fallback_multiplier: int = 1,
) -> int:
    value = _metric_number(metrics, canonical)
    if value is not None:
        result = _js_round(value)
    else:
        value = _metric_number(metrics, *fallbacks)
        result = _js_round((value or 0) * fallback_multiplier)
    if abs(result) > JS_SAFE_INTEGER:
        raise NetshopApiError(f"指标 {canonical} 超出安全整数范围", status=422)
    return result


def _raw_text(raw: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return ""


def _raw_number(raw: Mapping[str, object], *keys: str) -> int | float | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return value
        if isinstance(value, str):
            cleaned = re.sub(r"[￥¥,]", "", value).strip()
            try:
                parsed = float(cleaned)
            except ValueError:
                continue
            if math.isfinite(parsed):
                return parsed
    return None


def _row_projection(row: Mapping[str, object]) -> dict[str, object]:
    metrics = row["metrics"]
    raw = row["raw"]
    assert isinstance(metrics, Mapping) and isinstance(raw, Mapping)
    category_parts = [
        _raw_text(raw, "类目名称"),
        _raw_text(raw, "一级类目"),
        _raw_text(raw, "二级类目"),
        _raw_text(raw, "三级类目"),
        _raw_text(raw, "末级类目"),
    ]
    category = " / ".join(value for value in category_parts if value and value != "--")
    canonical_price = _metric_number(metrics, "skuPriceCents")
    raw_price = _raw_number(raw, "SKU价格", "京东价")
    price_cents = _js_round(canonical_price) if canonical_price is not None else (
        _js_round(raw_price * 100) if raw_price is not None else None
    )
    total_inventory = _raw_number(raw, "商品总库存", "SKU库存")
    available_inventory = _raw_number(raw, "商品可用库存", "SKU库存")
    return {
        "page_views": _metric_integer(metrics, "pageViews", "商品浏览量"),
        "visitors": _metric_integer(metrics, "visitors", "商品访客数"),
        "search_impressions": _metric_integer(metrics, "searchImpressions", "搜索曝光次数"),
        "search_clicks": _metric_integer(metrics, "searchClicks", "搜索点击次数"),
        "add_cart_customers": _metric_integer(metrics, "addCartCustomers", "加购客户数"),
        "add_cart_quantity": _metric_integer(metrics, "addCartQuantity", "加购商品件数"),
        "order_customers": _metric_integer(metrics, "orderCustomers", "下单客户数"),
        "order_quantity": _metric_integer(metrics, "orderQuantity", "下单商品件数"),
        "order_amount_cents": _metric_integer(metrics, "orderAmountCents", "下单金额", fallback_multiplier=100),
        "transaction_orders": _metric_integer(metrics, "transactionOrders", "成交单量"),
        "transaction_amount_cents": _metric_integer(metrics, "transactionAmountCents", "成交金额", fallback_multiplier=100),
        "transaction_quantity": _metric_integer(metrics, "transactionQuantity", "成交商品件数"),
        "transaction_customers": _metric_integer(metrics, "transactionCustomers", "成交客户数"),
        "favorites": _metric_integer(metrics, "favorites"),
        "refund_amount_cents": _metric_integer(metrics, "refundAmountCents"),
        "search_visitors": _metric_integer(metrics, "searchVisitors"),
        "search_transaction_customers": _metric_integer(metrics, "searchTransactionCustomers"),
        "spend_cents": _metric_integer(metrics, "spendCents", "花费", fallback_multiplier=100),
        "net_transaction_amount_cents": _metric_integer(metrics, "netTransactionAmountCents", "总订单金额", fallback_multiplier=100),
        "gross_transaction_amount_cents": _metric_integer(metrics, "grossTransactionAmountCents", "总订单金额", fallback_multiplier=100),
        "impressions": _metric_integer(metrics, "impressions", "展现数"),
        "clicks": _metric_integer(metrics, "clicks", "点击数"),
        "net_orders": _metric_integer(metrics, "netOrders", "总订单行"),
        "cart_quantity": _metric_integer(metrics, "cartQuantity"),
        "inventory_quantity": _metric_integer(metrics, "inventoryQuantity"),
        "total_inventory": _js_round(total_inventory) if total_inventory is not None else None,
        "available_inventory": _js_round(available_inventory) if available_inventory is not None else None,
        "price_cents": price_cents,
        "sale_attribute": _raw_text(raw, "销售属性"),
        "category": category,
        "brand": _raw_text(raw, "品牌", "品牌名称"),
        "product_status": _raw_text(raw, "商品状态"),
        "product_url": _raw_text(raw, "商品链接"),
        "image_url": _raw_text(
            raw,
            "主图链接",
            "商品主图链接",
            "SKU主图链接",
            "图片链接",
            "商品图片链接",
            "主图",
            "商品主图",
            "SKU主图",
            "图片",
            "商品图片",
            "imageUrl",
            "image_url",
            "image",
            "img",
            "pic",
        ),
        "image_content_sha256": _raw_text(raw, "图片内容SHA256").lower(),
        "image_object_key": _raw_text(raw, "图片对象键"),
        "image_mime_type": _raw_text(raw, "图片MIME"),
        "image_size_bytes": _js_round(value) if (value := _raw_number(raw, "图片字节数")) is not None else None,
        "source_created_at": _raw_text(raw, "创建时间"),
    }


def _scope_shape(
    payload: Mapping[str, object], rows: Sequence[Mapping[str, object]]
) -> tuple[str, str, str]:
    if payload["source"] in SNAPSHOT_SOURCES:
        return "snapshot", str(payload["snapshotDate"]), str(payload["snapshotDate"])
    dates = sorted(str(row["businessDate"]) for row in rows if row["businessDate"])
    if len(dates) == len(rows) and dates:
        return (
            "range",
            str(payload["startDate"] or dates[0]),
            str(payload["endDate"] or dates[-1]),
        )
    return "full", "", ""


def _scope_queryset(
    payload: Mapping[str, object], rows: Sequence[Mapping[str, object]]
):
    queryset = NetshopRow.objects.filter(
        source=payload["source"],
        dataset=payload["dataset"],
        platform=payload["platform"],
        shop_name=payload["shopName"],
    )
    kind, start, end = _scope_shape(payload, rows)
    if kind == "snapshot":
        queryset = queryset.filter(snapshot_date=start)
    elif kind == "range":
        queryset = queryset.filter(business_date__gte=start, business_date__lte=end)
    return queryset, kind, start, end


def _database_business_rows(queryset) -> list[dict[str, object]]:
    return [
        {
            "source": row.source,
            "dataset": row.dataset,
            "platform": row.platform,
            "shopName": row.shop_name,
            "businessDate": row.business_date or "",
            "snapshotDate": row.snapshot_date or "",
            "productCode": row.product_code,
            "productName": row.product_name,
            "skuId": row.sku_id,
            "spuId": row.spu_id,
            "warehouseType": row.warehouse_type,
            "metrics": row.metrics_json,
            "raw": row.raw_json,
        }
        for row in queryset.order_by("source_row_key")
    ]


def _content_hash_for_scope(scope_json: str, rows: Sequence[Mapping[str, object]]) -> str:
    row_hashes = sorted(_sha256_text(_canonical_json(row)) for row in rows)
    return _sha256_text(
        f"import-content-v3\n{_encode_part(scope_json)}{len(row_hashes)}\n{''.join(row_hashes)}"
    )


def _record_rejection(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    with transaction.atomic():
        assert_active_authority()
        errors = list(payload["errors"])  # type: ignore[arg-type]
        scope_json = _canonical_json(
            {
                "stage": "prevalidation",
                "hint": {
                    "source": payload.get("source") or None,
                    "platform": payload.get("platform") or None,
                    "shopName": payload.get("shopName") or None,
                },
            }
        )
        scope_key = _sha256_text(
            f"import-rejected-scope-v1\n{_encode_part('netshop')}{_encode_part(scope_json)}"
        )
        NetshopImportAttempt.objects.create(
            scope_key=scope_key,
            raw_file_hash=payload["rawFileHash"],
            outcome="rejected",
            error_code=str(errors[0].get("code") or "NETSHOP_PREVALIDATION_REJECTED"),
            actor_email=actor_email.strip().lower(),
            metadata={
                "fileName": payload["fileName"],
                "fileSizeBytes": payload["fileSizeBytes"],
                "source": payload.get("source", ""),
                "platform": payload.get("platform", ""),
                "shopName": payload.get("shopName", ""),
                "warnings": payload["warnings"],
                "errors": errors,
            },
            completed_at=timezone.now(),
        )
    return {
        "ok": False,
        "status": "rejected",
        "message": payload["message"],
        "warnings": payload["warnings"],
        "errors": errors,
        "errorCount": len(errors),
    }


def _master_reconciliation(
    payload: dict[str, object], rows: Sequence[dict[str, object]]
) -> tuple[int, list[str], bool]:
    if payload["source"] not in {"tmall_product_daily", "tmall_promotion"}:
        return 0, [], True
    latest_batch = (
        NetshopImportBatch.objects.filter(
            source="tmall_product_master",
            dataset="product_master",
            platform=payload["platform"],
            shop_name=payload["shopName"],
            status="completed",
        )
        .order_by("-snapshot_date", "-completed_at", "-created_at", "-id")
        .first()
    )
    product_ids = sorted({str(row["spuId"]).strip() for row in rows if row["spuId"]})
    if latest_batch is None:
        return len(product_ids), product_ids[:20], False
    matched = set(
        NetshopRow.objects.filter(
            last_import_batch_id=latest_batch.id,
            platform=payload["platform"],
            shop_name=payload["shopName"],
            spu_id__in=product_ids,
        ).values_list("spu_id", flat=True)
    )
    unmatched = [value for value in product_ids if value not in matched]
    if len(product_ids) >= 5 and len(unmatched) == len(product_ids):
        raise NetshopApiError(
            "报表商品与该店铺最新货品主数据零交集，疑似账号或店铺上下文不一致，已阻止导入",
            code="master_identity_mismatch",
            status=422,
            payload={"unmatchedProductCount": len(unmatched), "unmatchedSample": unmatched[:20]},
        )
    return len(unmatched), unmatched[:20], True


def _append_server_warnings(
    payload: dict[str, object], rows: Sequence[dict[str, object]]
) -> tuple[list[dict[str, object]], int]:
    warnings = list(payload["warnings"])  # type: ignore[arg-type]
    unmatched_count, unmatched_sample, master_available = _master_reconciliation(payload, rows)
    if not master_available:
        warnings.append(
            {
                "code": "MASTER_DATA_UNAVAILABLE",
                "message": "尚无该店铺货品主数据，商品日数据已保留，暂无法核验商品匹配",
            }
        )
    elif unmatched_count:
        warnings.append(
            {
                "code": "UNMATCHED_MASTER_PRODUCTS",
                "message": f"{unmatched_count} 个商品ID未匹配最新货品主数据；样例：{'、'.join(unmatched_sample)}",
            }
        )
    return warnings[:MAX_WARNING_COUNT], unmatched_count


def _validate_image_metadata(projection: Mapping[str, object]) -> None:
    content_hash = str(projection["image_content_sha256"] or "")
    if not content_hash:
        return
    mime = str(projection["image_mime_type"] or "")
    size = projection["image_size_bytes"]
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime)
    expected_key = f"netshop-product-images/v1/{content_hash}.{extension}" if extension else ""
    if (
        not HEX_64_RE.fullmatch(content_hash)
        or extension is None
        or not isinstance(size, int)
        or size <= 0
        or size > 6 * 1024 * 1024
        or projection["image_object_key"] != expected_key
    ):
        raise NetshopApiError("商品图片元数据与 R2 内容寻址契约不一致", status=422)


def _build_row_models(
    rows: Sequence[dict[str, object]],
    batch_id: str,
    prior_first_batch: Mapping[str, str],
    migration_generation: str = "",
) -> list[NetshopRow]:
    now = _now_text()
    result: list[NetshopRow] = []
    for row in rows:
        projection = _row_projection(row)
        _validate_image_metadata(projection)
        result.append(
            NetshopRow(
                source_row_key=row["sourceRowKey"],
                source_row_hash=row["sourceRowHash"],
                first_import_batch_id=prior_first_batch.get(str(row["sourceRowKey"]), batch_id),
                last_import_batch_id=batch_id,
                source_row_number=row["sourceRowNumber"],
                source=row["source"],
                dataset=row["dataset"],
                platform=row["platform"],
                shop_name=row["shopName"],
                business_date=row["businessDate"] or None,
                snapshot_date=row["snapshotDate"] or None,
                product_code=row["productCode"],
                product_name=row["productName"],
                sku_id=row["skuId"],
                spu_id=row["spuId"],
                warehouse_type=row["warehouseType"],
                metrics_json=row["metrics"],
                raw_json=row["raw"],
                created_at=now,
                updated_at=now,
                migration_generation=migration_generation,
                **projection,
            )
        )
    return result


def _bump_product_daily_revision(platform: str, shop_name: str) -> None:
    revision, _ = NetshopProductDailyRevision.objects.select_for_update().get_or_create(
        platform=platform, defaults={"data_version": 0}
    )
    revision.data_version += 1
    revision.save(update_fields=["data_version", "updated_at"])
    scoped, _ = NetshopProductDailyScopeRevision.objects.select_for_update().get_or_create(
        platform=platform, shop_name=shop_name, defaults={"data_version": 0}
    )
    scoped.data_version += 1
    scoped.save(update_fields=["data_version", "updated_at"])


def _bump_promotion_revision(platform: str, shop_name: str) -> None:
    scoped, _ = NetshopPromotionScopeRevision.objects.select_for_update().get_or_create(
        platform=platform, shop_name=shop_name, defaults={"data_version": 0}
    )
    scoped.data_version += 1
    scoped.save(update_fields=["data_version", "updated_at"])


def _rebuild_promotion_scope(
    payload: Mapping[str, object], rows: Sequence[Mapping[str, object]], batch_id: str
) -> None:
    if payload["source"] not in {"jd_promotion", "tmall_promotion"}:
        return
    _kind, start, end = _scope_shape(payload, rows)
    raw_rows = list(
        NetshopRow.objects.filter(
            source=payload["source"],
            dataset=payload["dataset"],
            platform=payload["platform"],
            shop_name=payload["shopName"],
            business_date__gte=start,
            business_date__lte=end,
        ).order_by("business_date", "source_row_key")
    )
    product_groups: dict[tuple[str, str], dict[str, object]] = {}
    for row in raw_rows:
        product_id = row.sku_id.strip() if row.source == "jd_promotion" else row.spu_id.strip()
        if not product_id or not row.business_date:
            raise NetshopApiError("推广聚合行缺少商品身份或有效业务日期", status=422)
        key = (row.business_date, product_id)
        current = product_groups.get(key)
        product_line = _raw_text(row.raw_json, "产品线")
        if current is None:
            current = {
                "date": row.business_date,
                "product_id": product_id,
                "product_name": row.product_name,
                "product_lines": {product_line} if product_line else set(),
                "spend": 0,
                "net": 0,
                "gross": 0,
                "impressions": 0,
                "clicks": 0,
                "orders": 0,
                "favorites": 0,
                "cart": 0,
                "rows": 0,
                "batches": set(),
            }
            product_groups[key] = current
        elif row.product_name > str(current["product_name"]):
            current["product_name"] = row.product_name
        if product_line:
            current["product_lines"].add(product_line)  # type: ignore[union-attr]
        current["spend"] += row.spend_cents  # type: ignore[operator]
        current["net"] += row.net_transaction_amount_cents  # type: ignore[operator]
        current["gross"] += row.gross_transaction_amount_cents  # type: ignore[operator]
        current["impressions"] += row.impressions  # type: ignore[operator]
        current["clicks"] += row.clicks  # type: ignore[operator]
        current["orders"] += row.net_orders  # type: ignore[operator]
        current["favorites"] += row.favorites  # type: ignore[operator]
        current["cart"] += row.cart_quantity  # type: ignore[operator]
        current["rows"] += 1  # type: ignore[operator]
        current["batches"].add(row.last_import_batch_id)  # type: ignore[union-attr]

    now = _now_text()
    product_models: list[NetshopPromotionProductDaily] = []
    shop_groups: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "products": 0,
            "spend": 0,
            "net": 0,
            "gross": 0,
            "impressions": 0,
            "clicks": 0,
            "orders": 0,
            "favorites": 0,
            "cart": 0,
            "rows": 0,
        }
    )
    for value in product_groups.values():
        batches = sorted(value["batches"])  # type: ignore[arg-type]
        product_models.append(
            NetshopPromotionProductDaily(
                platform=payload["platform"],
                shop_name=payload["shopName"],
                business_date=value["date"],
                product_id=value["product_id"],
                source=payload["source"],
                product_name=value["product_name"],
                product_line=",".join(sorted(value["product_lines"])),  # type: ignore[arg-type]
                spend_cents=value["spend"],
                net_transaction_amount_cents=value["net"],
                gross_transaction_amount_cents=value["gross"],
                impressions=value["impressions"],
                clicks=value["clicks"],
                net_orders=value["orders"],
                favorites=value["favorites"],
                cart_quantity=value["cart"],
                source_row_count=value["rows"],
                source_batch_id=batches[-1] if batches else batch_id,
                source_batch_count=len(batches),
                rebuilt_at=now,
            )
        )
        shop = shop_groups[str(value["date"])]
        shop["products"] += 1
        for target, source in (
            ("spend", "spend"),
            ("net", "net"),
            ("gross", "gross"),
            ("impressions", "impressions"),
            ("clicks", "clicks"),
            ("orders", "orders"),
            ("favorites", "favorites"),
            ("cart", "cart"),
            ("rows", "rows"),
        ):
            shop[target] += int(value[source])
    aggregate_filter = {
        "platform": payload["platform"],
        "shop_name": payload["shopName"],
        "business_date__gte": start,
        "business_date__lte": end,
    }
    NetshopPromotionProductDaily.objects.filter(**aggregate_filter).delete()
    NetshopPromotionShopDaily.objects.filter(**aggregate_filter).delete()
    NetshopPromotionAggregateState.objects.filter(**aggregate_filter).delete()
    NetshopPromotionProductDaily.objects.bulk_create(product_models, batch_size=1_000)
    shop_models = [
        NetshopPromotionShopDaily(
            platform=payload["platform"],
            shop_name=payload["shopName"],
            business_date=business_date,
            source=payload["source"],
            product_count=value["products"],
            spend_cents=value["spend"],
            net_transaction_amount_cents=value["net"],
            gross_transaction_amount_cents=value["gross"],
            impressions=value["impressions"],
            clicks=value["clicks"],
            net_orders=value["orders"],
            favorites=value["favorites"],
            cart_quantity=value["cart"],
            source_row_count=value["rows"],
            source_batch_id=batch_id,
            source_batch_count=1,
            rebuilt_at=now,
        )
        for business_date, value in sorted(shop_groups.items())
    ]
    NetshopPromotionShopDaily.objects.bulk_create(shop_models, batch_size=1_000)
    NetshopPromotionAggregateState.objects.bulk_create(
        [
            NetshopPromotionAggregateState(
                platform=payload["platform"],
                shop_name=payload["shopName"],
                business_date=item.business_date,
                source=payload["source"],
                ready=True,
                raw_row_count=item.source_row_count,
                product_row_count=item.product_count,
                source_batch_id=batch_id,
                source_batch_count=1,
                rebuilt_at=now,
                invalidated_at=now,
            )
            for item in shop_models
        ],
        batch_size=1_000,
    )
    platform = str(payload["platform"])
    platform_products = NetshopPromotionProductDaily.objects.filter(platform=platform)
    platform_shops = NetshopPromotionShopDaily.objects.filter(platform=platform)
    platform_states = NetshopPromotionAggregateState.objects.filter(platform=platform)
    aggregate = platform_shops.aggregate(cutoff=Max("business_date"))
    manifest, _ = NetshopPromotionAggregateManifest.objects.select_for_update().get_or_create(
        platform=platform,
        defaults={"invalidated_at": now},
    )
    manifest.ready = not platform_states.filter(ready=False).exists()
    manifest.historical_data_cutoff = aggregate["cutoff"]
    manifest.source_shop_count = platform_shops.values("shop_name").distinct().count()
    manifest.raw_row_count = platform_states.aggregate(value=Sum("raw_row_count"))["value"] or 0
    # The manifest counts are independent evidence; use exact relation counts,
    # not values inferred from the imported request.
    manifest.product_row_count = platform_products.count()
    manifest.shop_day_count = platform_shops.count()
    manifest.state_day_count = platform_states.count()
    manifest.completed_at = now
    manifest.invalidated_at = now
    manifest.data_version += 1
    manifest.save()
    control, _ = NetshopPromotionAggregateControl.objects.select_for_update().get_or_create(
        platform=platform, defaults={"updated_at": now}
    )
    control.bootstrap_batch_id = batch_id
    control.bootstrap_raw_row_count = raw_rows.__len__()
    control.bootstrap_product_row_count = len(product_models)
    control.bootstrap_shop_day_count = len(shop_models)
    control.bootstrap_data_cutoff = aggregate["cutoff"]
    control.maintenance_token = ""
    control.maintenance_version += 1
    control.maintenance_previous_ready = manifest.ready
    control.maintenance_started_at = None
    control.updated_at = now
    control.save()
    _bump_promotion_revision(platform, str(payload["shopName"]))


def _bump_global_revision(batch_id: str, content_hash: str, state_token: str) -> str:
    revision, _ = NetshopDataRevision.objects.select_for_update().get_or_create(
        domain="netshop", defaults={"revision": 0, "source_digest": "0" * 64}
    )
    revision.revision += 1
    revision.source_digest = _sha256_text(
        "".join(
            _encode_part(value)
            for value in (
                "netshop-revision-v1",
                revision.source_digest or "0" * 64,
                batch_id,
                content_hash,
                state_token,
            )
        )
    )
    revision.save(update_fields=["revision", "source_digest", "updated_at"])
    return f"{revision.revision}:{revision.source_digest[:12]}"


_AUTHORITY_FAILURE_CODES = frozenset(
    {
        "netshop_write_authority_unavailable",
        "netshop_write_authority_inactive",
        "netshop_write_authority_mismatch",
    }
)


def _failure_code(error: Exception) -> str:
    value = error.code if isinstance(error, NetshopApiError) else "NETSHOP_IMPORT_INTERNAL_ERROR"
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_").upper()
    return (normalized or "NETSHOP_IMPORT_FAILED")[:64]


def _failure_metadata(
    payload: Mapping[str, object],
    *,
    scope_json: str,
    error: Exception,
    warnings: Sequence[object],
) -> dict[str, object]:
    return {
        "stage": "publication",
        "scopeJson": scope_json,
        "fileName": payload["fileName"],
        "fileSizeBytes": payload["fileSizeBytes"],
        "source": payload["source"],
        "dataset": payload["dataset"],
        "platform": payload["platform"],
        "shopName": payload["shopName"],
        "warnings": list(warnings),
        "errorStatus": error.status if isinstance(error, NetshopApiError) else 500,
    }


def _record_unreserved_failure(
    payload: dict[str, object],
    actor_email: str,
    *,
    rows: Sequence[dict[str, object]],
    scope_key: str,
    scope_json: str,
    content_hash: str,
    warnings: Sequence[object],
    error: Exception,
) -> None:
    if isinstance(error, NetshopApiError) and error.code in _AUTHORITY_FAILURE_CODES:
        return
    try:
        with transaction.atomic():
            assert_active_authority()
            NetshopImportAttempt.objects.create(
                scope_key=scope_key,
                raw_file_hash=payload["rawFileHash"],
                content_hash=content_hash,
                outcome="failed",
                error_code=_failure_code(error),
                actor_email=actor_email.strip().lower(),
                metadata={
                    **_failure_metadata(
                        payload,
                        scope_json=scope_json,
                        error=error,
                        warnings=warnings,
                    ),
                    "rowCount": len(rows),
                    "reservationPersisted": False,
                },
                completed_at=timezone.now(),
            )
    except Exception as audit_error:
        logger.exception("Failed to persist netshop import failure audit")
        raise NetshopApiError(
            "网店导入失败且失败审计无法持久化",
            code="service_unavailable",
            status=503,
        ) from audit_error


def _fail_reserved_import(
    payload: dict[str, object],
    *,
    owner: uuid.UUID,
    scope_key: str,
    scope_json: str,
    warnings: Sequence[object],
    error: Exception,
) -> None:
    try:
        with transaction.atomic():
            assert_active_authority()
            _lock_scope(scope_key)
            attempt = NetshopImportAttempt.objects.select_for_update().filter(id=owner).first()
            if attempt is None or attempt.outcome != "processing":
                # A commit whose outcome became uncertain must never be rewritten as failed.
                return
            head = NetshopImportScopeHead.objects.select_for_update().filter(
                scope_key=scope_key
            ).first()
            owns_head = bool(
                head and head.status == "processing" and head.owner_token == str(owner)
            )
            if owns_head and head is not None:
                head.status = "ready"
                head.owner_token = ""
                head.owner_started_at = None
                head.heartbeat_at = None
                head.save(
                    update_fields=[
                        "status",
                        "owner_token",
                        "owner_started_at",
                        "heartbeat_at",
                        "updated_at",
                    ]
                )
            attempt.outcome = "failed"
            attempt.error_code = (
                _failure_code(error) if owns_head else "IMPORT_RESERVATION_OWNERSHIP_LOST"
            )
            attempt.metadata = {
                **dict(attempt.metadata),
                **_failure_metadata(
                    payload,
                    scope_json=scope_json,
                    error=error,
                    warnings=warnings,
                ),
                "reservationPersisted": True,
                "reservationReleased": owns_head,
            }
            attempt.completed_at = timezone.now()
            attempt.save(
                update_fields=["outcome", "error_code", "metadata", "completed_at"]
            )
    except Exception as audit_error:
        logger.exception("Failed to close reserved netshop import attempt")
        raise NetshopApiError(
            "网店导入失败且范围占用无法安全收尾",
            code="service_unavailable",
            status=503,
        ) from audit_error


def _reserve_prepared_import(
    payload: dict[str, object],
    actor_email: str,
    *,
    rows: Sequence[dict[str, object]],
    scope_key: str,
    scope_json: str,
    content_hash: str,
    warnings: Sequence[object],
    unmatched_count: int,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    with transaction.atomic():
        assert_active_authority()
        _lock_scope(scope_key)
        NetshopImportScopeHead.objects.get_or_create(
            scope_key=scope_key,
            defaults={"state_token": "initial", "status": "ready"},
        )
        head = NetshopImportScopeHead.objects.select_for_update().get(scope_key=scope_key)
        if head.status == "processing":
            stale_before = timezone.now() - timedelta(minutes=30)
            if head.heartbeat_at is None or head.heartbeat_at > stale_before:
                raise NetshopApiError(
                    "同一网店业务范围正在导入，请稍后重试",
                    code="conflict",
                    status=409,
                )
            NetshopImportAttempt.objects.filter(
                id=head.owner_token, outcome="processing"
            ).update(
                outcome="failed",
                error_code="IMPORT_RESERVATION_EXPIRED",
                completed_at=timezone.now(),
            )
            head.status = "ready"
            head.owner_token = ""
            head.owner_started_at = None
            head.heartbeat_at = None
            head.save()

        current_state = head.state_token.strip() or "initial"
        current_scope, _kind, _start, _end = _scope_queryset(payload, rows)
        current_count = current_scope.count()
        if current_count == len(rows):
            current_rows = _database_business_rows(current_scope)
            if _content_hash_for_scope(scope_json, current_rows) == content_hash:
                scope_value = json.loads(scope_json)
                owner_ids = list(
                    current_scope.exclude(last_import_batch_id="")
                    .values_list("last_import_batch_id", flat=True)
                    .distinct()[: MAX_IMPORT_ROWS + 1]
                )
                matching_fingerprint = (
                    NetshopImportFingerprint.objects.filter(
                        scope_key=scope_key,
                        scope_json=scope_value,
                        content_hash=content_hash,
                    )
                    .order_by("-publication_sequence", "-id")
                    .first()
                )
                current_batch = (
                    NetshopImportBatch.objects.filter(
                        id__in=owner_ids,
                        status="completed",
                    )
                    .order_by("-completed_at", "-created_at", "-id")
                    .first()
                )
                if current_batch is None and matching_fingerprint is not None:
                    current_batch = NetshopImportBatch.objects.filter(
                        id=matching_fingerprint.batch_id,
                        status="completed",
                    ).first()
                if current_batch is None:
                    raise NetshopApiError(
                        "当前网店事实缺少可验证的完成批次所有权",
                        code="service_unavailable",
                        status=503,
                    )
                NetshopImportAttempt.objects.create(
                    batch_id=current_batch.id,
                    scope_key=scope_key,
                    raw_file_hash=payload["rawFileHash"],
                    content_hash=content_hash,
                    outcome="duplicate",
                    actor_email=actor_email.strip().lower(),
                    metadata={
                        "fileName": payload["fileName"],
                        "fileSizeBytes": payload["fileSizeBytes"],
                        "warnings": list(warnings),
                        "currentOwnerBatchCount": len(owner_ids),
                    },
                    completed_at=timezone.now(),
                )
                if matching_fingerprint is None and len(owner_ids) == 1:
                    NetshopImportFingerprint.objects.get_or_create(
                        batch_id=current_batch.id,
                        defaults={
                            "scope_key": scope_key,
                            "scope_json": scope_value,
                            "import_hash": current_batch.file_hash,
                            "content_hash": content_hash,
                            "raw_file_hash": payload["rawFileHash"],
                            "row_count": len(rows),
                            "published_state_token": current_state,
                            "status": "completed",
                        },
                    )
                return (
                    {
                        "ok": True,
                        "status": "duplicate",
                        "message": "全部标准化业务资料与当前数据一致，无需重复导入",
                        "batch": batch_payload(current_batch),
                        "warnings": list(warnings),
                        "verification": {
                            "verified": True,
                            "rowCount": len(rows),
                            "readbackRowCount": current_count,
                            "dataset": payload["dataset"],
                            "platform": payload["platform"],
                            "shopName": payload["shopName"],
                            "dateMin": min((row["businessDate"] for row in current_rows if row["businessDate"]), default=None),
                            "dateMax": max((row["businessDate"] for row in current_rows if row["businessDate"]), default=None),
                            "unmatchedProductCount": unmatched_count,
                            **payload["imagePersistence"],
                        },
                    },
                    None,
                )

        attempt_hash = _attempt_hash(scope_key, content_hash, current_state)
        batch_id = _batch_id(
            str(payload["source"]),
            str(payload["platform"]),
            str(payload["shopName"]),
            attempt_hash,
        )
        owner = uuid.uuid4()
        NetshopImportAttempt.objects.create(
            id=owner,
            batch_id=batch_id,
            scope_key=scope_key,
            raw_file_hash=payload["rawFileHash"],
            content_hash=content_hash,
            outcome="processing",
            actor_email=actor_email.strip().lower(),
            metadata={
                "scopeJson": scope_json,
                "fileName": payload["fileName"],
                "fileSizeBytes": payload["fileSizeBytes"],
                "warnings": list(warnings),
            },
        )
        head.status = "processing"
        head.owner_token = str(owner)
        # current_batch_id continues to name the last committed owner until publication.
        head.owner_started_at = timezone.now()
        head.heartbeat_at = timezone.now()
        head.save()
        return None, {
            "owner": owner,
            "attemptHash": attempt_hash,
            "batchId": batch_id,
            "currentState": current_state,
        }


def _publish_prepared_import(
    payload: dict[str, object],
    actor_email: str,
    *,
    rows: Sequence[dict[str, object]],
    scope_key: str,
    scope_json: str,
    content_hash: str,
    warnings: Sequence[object],
    unmatched_count: int,
    reservation: Mapping[str, object],
) -> dict[str, object]:
    owner = reservation["owner"]
    attempt_hash = str(reservation["attemptHash"])
    batch_id = str(reservation["batchId"])
    current_state = str(reservation["currentState"])
    with transaction.atomic():
        assert_active_authority()
        _lock_scope(scope_key)
        head = NetshopImportScopeHead.objects.select_for_update().get(scope_key=scope_key)
        if (
            head.status != "processing"
            or head.owner_token != str(owner)
            or (head.state_token.strip() or "initial") != current_state
        ):
            raise NetshopApiError(
                "网店导入所有权在发布前已失效", code="conflict", status=409
            )
        attempt = NetshopImportAttempt.objects.select_for_update().get(id=owner)
        if attempt.outcome != "processing" or attempt.batch_id != batch_id:
            raise NetshopApiError(
                "网店导入尝试状态在发布前已失效", code="conflict", status=409
            )

        scope_rows, kind, start, end = _scope_queryset(payload, rows)
        prior_first_batch = dict(
            scope_rows.filter(
                source_row_key__in=[row["sourceRowKey"] for row in rows]
            ).values_list("source_row_key", "first_import_batch_id")
        )
        scope_rows.delete()
        row_models = _build_row_models(rows, batch_id, prior_first_batch)
        NetshopRow.objects.bulk_create(row_models, batch_size=1_000)
        published_state = _next_state_token(current_state, batch_id, content_hash, len(rows))
        dates = sorted(str(row["businessDate"]) for row in rows if row["businessDate"])
        parser_totals = dict(payload["parserTotals"])  # type: ignore[arg-type]
        server_totals = {
            **parser_totals,
            "rawFileHash": payload["rawFileHash"],
            "contentHash": content_hash,
            "dataset": payload["dataset"],
            "dateMin": dates[0] if dates else None,
            "dateMax": dates[-1] if dates else None,
            "rowCount": len(rows),
            "uniqueProductCount": len({str(row["spuId"]) for row in rows if row["spuId"]}),
            "uniqueSkuCount": len({str(row["skuId"]) for row in rows if row["skuId"]}),
            "unmatchedProductCount": unmatched_count,
            "replaceScope": {"kind": kind, "start": start or None, "end": end or None},
            **payload["imagePersistence"],
        }
        now = _now_text()
        batch = NetshopImportBatch.objects.create(
            id=batch_id,
            source=payload["source"],
            dataset=payload["dataset"],
            platform=payload["platform"],
            shop_name=payload["shopName"],
            file_name=payload["fileName"],
            file_size_bytes=payload["fileSizeBytes"],
            file_hash=attempt_hash,
            raw_file_hash=payload["rawFileHash"],
            content_hash=content_hash,
            scope_key=scope_key,
            published_state_token=published_state,
            sheet_name=payload["sheetName"],
            status="completed",
            row_count=len(rows),
            inserted_count=len(rows),
            duplicate_count=0,
            warning_count=len(warnings),
            date_min=dates[0] if dates else None,
            date_max=dates[-1] if dates else None,
            snapshot_date=payload["snapshotDate"] or None,
            warnings_json=list(warnings),
            totals_json=server_totals,
            note=payload["note"],
            actor_email=actor_email.strip().lower(),
            created_at=now,
            completed_at=now,
        )
        if payload["dataset"] in {"sku_daily", "spu_daily"}:
            _bump_product_daily_revision(str(payload["platform"]), str(payload["shopName"]))
        _rebuild_promotion_scope(payload, rows, batch_id)

        verification_count = NetshopRow.objects.filter(last_import_batch_id=batch_id).count()
        if verification_count != len(rows):
            raise NetshopApiError(
                "网店事实落库行数回查不一致",
                code="service_unavailable",
                status=503,
            )
        ownership_count = _scope_queryset(payload, rows)[0].exclude(
            last_import_batch_id=batch_id
        ).count()
        if ownership_count:
            raise NetshopApiError(
                "网店业务范围落库归属回查不一致",
                code="service_unavailable",
                status=503,
            )

        head.state_token = published_state
        head.status = "ready"
        head.owner_token = ""
        head.current_batch_id = batch_id
        head.generation += 1
        head.owner_started_at = None
        head.heartbeat_at = None
        head.save()
        attempt.outcome = "imported"
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["outcome", "completed_at"])
        NetshopImportFingerprint.objects.create(
            batch_id=batch_id,
            scope_key=scope_key,
            scope_json=json.loads(scope_json),
            import_hash=attempt_hash,
            content_hash=content_hash,
            raw_file_hash=payload["rawFileHash"],
            row_count=len(rows),
            published_state_token=published_state,
            status="completed",
            publication_sequence=head.generation,
        )
        revision = _bump_global_revision(batch_id, content_hash, published_state)
        return {
            "ok": True,
            "status": "imported",
            "message": f"{payload['platform']}网店数据导入成功",
            "batch": batch_payload(batch),
            "warnings": list(warnings),
            "verification": {
                "verified": True,
                "rowCount": verification_count,
                "readbackRowCount": verification_count,
                "dataset": payload["dataset"],
                "platform": payload["platform"],
                "shopName": payload["shopName"],
                "dateMin": dates[0] if dates else None,
                "dateMax": dates[-1] if dates else None,
                "unmatchedProductCount": unmatched_count,
                "revision": revision,
                **payload["imagePersistence"],
            },
        }


def _import_prepared(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    rows = list(payload["rows"])  # type: ignore[arg-type]
    # Database-backed identity reconciliation is still pre-publication
    # validation. A rejection here must not claim the import scope.
    try:
        warnings, unmatched_count = _append_server_warnings(payload, rows)
    except NetshopApiError as error:
        rejection = {
            **payload,
            "errors": [{"code": error.code.upper(), "message": str(error)}],
            "message": str(error),
        }
        return _record_rejection(rejection, actor_email)

    scope_key, scope_json, content_hash = _fingerprint(payload, rows)
    try:
        duplicate, reservation = _reserve_prepared_import(
            payload,
            actor_email,
            rows=rows,
            scope_key=scope_key,
            scope_json=scope_json,
            content_hash=content_hash,
            warnings=warnings,
            unmatched_count=unmatched_count,
        )
    except Exception as error:
        _record_unreserved_failure(
            payload,
            actor_email,
            rows=rows,
            scope_key=scope_key,
            scope_json=scope_json,
            content_hash=content_hash,
            warnings=warnings,
            error=error,
        )
        raise
    if duplicate is not None:
        return duplicate
    if reservation is None:
        raise NetshopApiError(
            "网店导入范围占用结果不完整", code="service_unavailable", status=503
        )
    try:
        return _publish_prepared_import(
            payload,
            actor_email,
            rows=rows,
            scope_key=scope_key,
            scope_json=scope_json,
            content_hash=content_hash,
            warnings=warnings,
            unmatched_count=unmatched_count,
            reservation=reservation,
        )
    except Exception as error:
        _fail_reserved_import(
            payload,
            owner=reservation["owner"],  # type: ignore[arg-type]
            scope_key=scope_key,
            scope_json=scope_json,
            warnings=warnings,
            error=error,
        )
        raise


def import_netshop_payload(payload: object, actor_email: str) -> dict[str, object]:
    normalized = validate_import_payload(payload)
    if normalized["disposition"] == "rejected":
        return _record_rejection(normalized, actor_email)
    return _import_prepared(normalized, actor_email)


def list_import_batches(
    *,
    page: int,
    page_size: int,
    ids: Sequence[str] = (),
    sources: Sequence[str] = (),
    platforms: Sequence[str] = (),
    shops: Sequence[str] = (),
) -> dict[str, object]:
    queryset = NetshopImportBatch.objects.all()
    if ids:
        queryset = queryset.filter(id__in=list(dict.fromkeys(ids))[:100])
    if sources:
        queryset = queryset.filter(source__in=list(dict.fromkeys(sources))[:50])
    if platforms:
        queryset = queryset.filter(platform__in=list(dict.fromkeys(platforms))[:20])
    if shops:
        queryset = queryset.filter(shop_name__in=list(dict.fromkeys(shops))[:100])
    queryset = queryset.order_by("-completed_at", "-created_at", "-id")
    total = queryset.count()
    offset = (page - 1) * page_size
    items = [batch_payload(item) for item in queryset[offset : offset + page_size]]
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "returned": len(items),
            "truncated": offset + len(items) < total,
        },
    }
