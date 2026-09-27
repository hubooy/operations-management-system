"""Bounded, revision-fenced fact pages for complete business analysis.

Uses existing owning-reader grants and typed facts. No SQL/tool/schema supplied
by the caller, no writes, model calls, user code or host-file access.
"""
from __future__ import annotations

from django.core import signing
from django.db.models import Count, Max, Min, Sum

from business_analysis.contracts import (
    AnalysisContractError, MAX_SAFE_INTEGER, SCHEMA_VERSION, comparison_periods,
    coverage, digest, monetary_cents,
)
from .errors import NetshopApiError
from .models import NetshopImportBatch, NetshopRow
from .query import revision_value


CURSOR_SALT = "netshop-business-analysis-v1"
PAGE_LIMIT = 100
SOURCES = {
    "promotion": {"京东": ("jd_promotion", "ad"), "天猫": ("tmall_promotion", "promotion_daily")},
    "sku": {"京东": ("jd_sku_daily", "sku_daily")},
    "spu": {"京东": ("jd_sku_daily", "spu_daily"), "天猫": ("tmall_product_daily", "spu_daily")},
    "b2b": {"京东": ("jd_b2b", "b2b")},
    "master": {"京东": ("jd_product_master", "product_master"), "天猫": ("tmall_product_master", "product_master")},
}
# Existing writer projections are authoritative for these figures. The source
# names are explicit availability checks, not a second raw-number parser.
PROMOTION_METRICS = {
    "spendCents": ("spend_cents", ("spendCents", "花费")),
    "reportedGmvCents": ("net_transaction_amount_cents", ("netTransactionAmountCents", "总订单金额")),
    "impressions": ("impressions", ("impressions", "展现数")),
    "clicks": ("clicks", ("clicks", "点击数")),
    "reportedOrderLines": ("net_orders", ("netOrders", "总订单行")),
    "cartQuantity": ("cart_quantity", ("cartQuantity",)),
}
PRODUCT_METRICS = {
    "paymentCents": ("transaction_amount_cents", ("transactionAmountCents", "成交金额")),
    "paymentQuantity": ("transaction_quantity", ("transactionQuantity", "成交商品件数")),
    "productDayVisitors": ("visitors", ("visitors", "商品访客数")),
    "pageViews": ("page_views", ("pageViews", "商品浏览量")),
    "reportedOrders": ("transaction_orders", ("transactionOrders", "成交单量")),
}
DIMENSION_FIELDS = {
    "keyword": ("关键词",), "searchTerm": ("搜索词", "用户搜索词"),
    "planId": ("计划ID", "计划id"), "planName": ("推广计划", "计划名称"),
    "unitId": ("单元ID", "单元id"), "unitName": ("推广单元", "单元名称"),
    "matchType": ("匹配类型", "匹配方式"),
    "promotedSkuId": ("智能投放推广SKU ID", "智能投放推广SKU", "推广SKU"),
    "triggerSkuId": ("触发SKU ID", "触发SKU"), "attributedSkuId": ("跟单SKU ID", "跟单SKU"),
    "merchantCode": ("商家SKU", "SKU商家编码", "商家编码"),
}
RAW_MONEY_FIELDS = {
    "directGmvCents": ("直接订单金额",), "indirectGmvCents": ("间接订单金额",),
    "newCustomerGmvCents": ("新客订单金额",),
}
_EMPTY = {"", "-", "--", "—", "N/A"}


def _scalar(raw, names, *, max_length=240):
    for name in names:
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        text = str(value).strip()
        if text in _EMPTY:
            continue
        if len(text) > max_length:
            raise NetshopApiError("源字段过长，拒绝截断身份或关键词", code="source_field_too_large", status=422)
        return text
    return None


def _present(row, names):
    for container in (row["metrics_json"], row["raw_json"]):
        for name in names:
            value = container.get(name)
            if type(value) in (str, int, float) and str(value).strip() not in _EMPTY:
                try:
                    # Presence must describe a numeric source, not any JSON key.
                    if monetary_cents(value) is not None:
                        return True
                except AnalysisContractError:
                    return True  # Typed integer bounds are checked separately.
    return False


def _project(row, metrics):
    raw = row["raw_json"]
    values = {}
    for key, (column, names) in metrics.items():
        value = row[column]
        if abs(value) > MAX_SAFE_INTEGER:
            raise NetshopApiError("源指标超过无损整数范围", status=422)
        values[key] = value if _present(row, names) else None
    if row["source"] == "jd_promotion":
        for key, names in RAW_MONEY_FIELDS.items():
            values[key] = monetary_cents(_scalar(raw, names))
    return {"rowId": str(row["id"]), "sourceRowHash": row["source_row_hash"],
            "batchId": row["last_import_batch_id"], "platform": row["platform"], "shopName": row["shop_name"],
            "date": row["business_date"], "snapshotDate": row["snapshot_date"],
            "skuId": row["sku_id"] or None, "spuId": row["spu_id"] or None,
            "productCode": row["product_code"] or None,
            "productName": _scalar(row, ("product_name",), max_length=600), "category": row["category"] or None,
            "dimensions": {key: _scalar(raw, names) for key, names in DIMENSION_FIELDS.items()},
            "metrics": values}


def validate_request(params):
    allowed = {"platform", "shop", "dataset", "startDate", "endDate", "window", "cursor", "limit"}
    if set(params) - allowed:
        raise NetshopApiError("分析请求包含未知参数")
    if any(len(params.getlist(key)) != 1 for key in params):
        raise NetshopApiError("分析参数不得重复")
    values = {key: params.get(key, "") for key in allowed}
    for key in ("platform", "shop"):
        value = values[key]
        if not value or value != value.strip() or len(value) > 100 or any(ord(c) < 32 for c in value):
            raise NetshopApiError("必须提供精确平台与店铺身份")
    dataset = values["dataset"]
    if dataset not in SOURCES or values["platform"] not in SOURCES[dataset]:
        raise NetshopApiError("此平台/数据源尚无已验证分析适配器", code="unsupported_analysis_source", status=422)
    try:
        periods = comparison_periods(values["startDate"], values["endDate"])
    except AnalysisContractError as error:
        raise NetshopApiError(str(error)) from error
    window = values["window"] or "current"
    if window not in {"current", "previous", "yearAgo"}:
        raise NetshopApiError("比较窗口无效")
    limit = values["limit"] or "50"
    if not limit.isascii() or not limit.isdigit() or limit.startswith("0") or not 1 <= int(limit) <= PAGE_LIMIT:
        raise NetshopApiError("每页记录数必须为 1—100")
    if len(values["cursor"]) > 1600:
        raise NetshopApiError("分析游标过长")
    return {"platform": values["platform"], "shop": values["shop"], "dataset": dataset,
            "periods": periods, "window": window}, int(limit), values["cursor"] or None


def read_page(spec, limit, cursor):
    before = revision_value()
    source, dataset = SOURCES[spec["dataset"]][spec["platform"]]
    selection = {"platform": spec["platform"], "shop_name": spec["shop"], "source": source, "dataset": dataset}
    rows = NetshopRow.objects.filter(**selection)
    batches = NetshopImportBatch.objects.filter(**selection, status="completed")
    window = spec["periods"][spec["window"]]
    master_batch = None
    if spec["dataset"] == "master":
        master_batch = batches.exclude(snapshot_date__isnull=True).exclude(snapshot_date="").order_by("-snapshot_date", "-completed_at", "-id").first()
        rows = rows.filter(last_import_batch_id=master_batch.id) if master_batch else rows.none()
    else:
        rows = rows.filter(business_date__gte=window["startDate"], business_date__lt=window["endExclusive"])
    binding = digest({"schemaVersion": SCHEMA_VERSION, "query": spec, "limit": limit, "revision": before,
                      "masterBatch": master_batch.id if master_batch else None})
    last_id = 0
    if cursor:
        try:
            previous = signing.loads(cursor, salt=CURSOR_SALT, max_age=3600)
        except signing.BadSignature as error:
            raise NetshopApiError("分析游标无效或已过期", code="invalid_cursor", status=409) from error
        if not isinstance(previous, dict) or previous.get("binding") != binding or type(previous.get("lastId")) is not int or previous["lastId"] < 1:
            raise NetshopApiError("数据或筛选已变化，请从首页重新核验", code="analysis_revision_changed", status=409)
        last_id = previous["lastId"]
    metrics = PROMOTION_METRICS if spec["dataset"] == "promotion" else PRODUCT_METRICS if spec["dataset"] in {"sku", "spu", "b2b"} else {}
    control, source_coverage, available = None, None, None
    if not cursor:
        aggregate = rows.aggregate(row_count=Count("id"), **{key: Sum(value[0]) for key, value in metrics.items()})
        totals = {key: aggregate[key] or 0 for key in metrics}
        if any(abs(value) > MAX_SAFE_INTEGER for value in totals.values()):
            raise NetshopApiError("控制汇总超出无损传输范围", status=422)
        control = {"rowCount": aggregate["row_count"], "typedTotals": totals,
                   "note": "必须读完全部页并核对指标存在性后使用汇总；默认零不证明源字段存在。"}
        source_coverage = ({"status": "current_master" if master_batch else "no_records",
                            "snapshotDate": master_batch.snapshot_date if master_batch else None,
                            "batchId": master_batch.id if master_batch else None,
                            "historicalMapping": False} if spec["dataset"] == "master" else
                           coverage(window, rows.values_list("business_date", flat=True).distinct()))
        available = NetshopRow.objects.filter(**selection).aggregate(firstDate=Min("business_date"), lastDate=Max("business_date"))
    columns = ["id", "source_row_hash", "last_import_batch_id", "source", "platform", "shop_name", "business_date", "snapshot_date",
               "sku_id", "spu_id", "product_code", "product_name", "category", "metrics_json", "raw_json", *[v[0] for v in metrics.values()]]
    raw_page = list(rows.filter(id__gt=last_id).order_by("id").values(*columns)[:limit+1])
    has_more = len(raw_page) > limit
    selected = raw_page[:limit]
    items = [_project(row, metrics) for row in selected]
    from business_analysis.contracts import bounded_page_items
    try:
        items, has_more = bounded_page_items(items, has_more)
    except AnalysisContractError as error:
        raise NetshopApiError(str(error), status=422) from error
    after = revision_value()
    if before != after:
        raise NetshopApiError("取数期间网店版本发生变化，请重新核验", code="analysis_revision_changed", status=409)
    next_cursor = signing.dumps({"binding": binding, "lastId": int(items[-1]["rowId"])}, salt=CURSOR_SALT, compress=True) if has_more else None
    return {"schemaVersion": SCHEMA_VERSION, "sourceRef": binding, "sourceRevision": before,
            "filters": spec, "source": source, "sourceDataset": dataset,
            "monetaryUnit": "CNY_CENT", "consistency": "revision_fenced_pages_not_cross_domain_snapshot",
            "metricSemantics": {"reportedGmvCents": "京东为平台总订单归因金额；天猫为源净成交口径；均不是 ERP 净销售或利润。",
                                "reportedOrderLines": "平台报告订单口径，不保证跨商品去重。",
                                "productDayVisitors": "商品×日累计访客，不能解释为店铺去重 UV。",
                                "b2b": "仅 jd_b2b 事实；字段不存在时为 null，不推断商用商品等于 B 端成交。",
                                "attributionWindow": "unknown_unless_source_separately_verified"},
            "control": control, "coverage": source_coverage, "availableDates": available,
            "items": items, "pageEvidence": {"sha256": digest(items), "rowCount": len(items)},
            "pagination": {"hasMore": has_more, "nextCursor": next_cursor, "limit": limit}}
