"""Exact-store ERP evidence pages. No customer/order identifiers are exported."""
from django.core import signing
from django.db.models import Count, Max, Min, Sum, Case, When, F, Value, BigIntegerField
from business_analysis.contracts import SCHEMA_VERSION, MAX_SAFE_INTEGER, AnalysisContractError, comparison_periods, coverage, digest
from .models import SalesOrderLine
from .query import SalesAccessError, SalesRequestError, revision_token

FIELDS = {"operation", "platform", "shop", "channel", "startDate", "endDate", "window", "limit", "cursor"}
SALT = "sales-business-analysis-v1"


def validate(payload):
    if set(payload) - FIELDS:
        raise SalesRequestError("分析请求包含未知字段")
    result = {"operation": "analysis_records"}
    for key in ("platform", "shop", "channel"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise SalesRequestError("必须提供精确平台、店铺与渠道")
        result[key] = value
    try:
        comparison_periods(payload.get("startDate"), payload.get("endDate"))
    except AnalysisContractError as error:
        raise SalesRequestError(str(error)) from error
    result.update(startDate=payload["startDate"], endDate=payload["endDate"], window=payload.get("window", "current"))
    if not isinstance(result["window"], str) or result["window"] not in {"current", "previous", "yearAgo"}:
        raise SalesRequestError("比较窗口无效")
    result["limit"] = payload.get("limit", 10)
    if type(result["limit"]) is not int or not 1 <= result["limit"] <= 100:
        raise SalesRequestError("每页记录数必须为 1—100")
    result["cursor"] = payload.get("cursor")
    if result["cursor"] is not None and (not isinstance(result["cursor"], str) or not 1 <= len(result["cursor"]) <= 1600):
        raise SalesRequestError("分析游标无效")
    return result


def expressions():
    def conditional(condition, value):
        return Case(When(condition, then=value), default=Value(0), output_field=BigIntegerField())
    from django.db.models import Q
    return {
        "netSalesCents": F("allocated_amount_cents"),
        "positiveSalesCents": conditional(Q(allocated_amount_cents__gt=0), F("allocated_amount_cents")),
        "refundCents": conditional(Q(allocated_amount_cents__lt=0), -F("allocated_amount_cents")),
        "costCents": F("cost_amount_cents"),
        "grossProfitCents": F("allocated_amount_cents") - F("cost_amount_cents"),
        "reportedGrossProfitCents": F("gross_profit_cents"),
        "feeCents": F("fee_allocation_cents"),
        "netQuantity": conditional(Q(is_net_quantity_row=True), F("quantity")),
        "positiveQuantity": conditional(Q(is_net_quantity_row=True, quantity__gt=0), F("quantity")),
        "returnQuantity": conditional(Q(is_net_quantity_row=True, quantity__lt=0), -F("quantity")),
        "netSalesExcludingAccessoriesCents": conditional(Q(is_net_sales_row=True), F("allocated_amount_cents")),
    }


def read_page(principal, request):
    if principal.role != "admin" or principal.scope is not None:
        raise SalesAccessError("分析明细仅向无范围限制管理员开放")
    request = validate(request)
    before = revision_token()
    periods = comparison_periods(request["startDate"], request["endDate"])
    window = periods[request["window"]]
    base = SalesOrderLine.objects.filter(is_business_row=True, platform=request["platform"], platform_key=request["platform"],
        shop_name=request["shop"], shop_key=request["shop"], channel=request["channel"], channel_key=request["channel"])
    rows = base.filter(business_date__gte=window["startDate"], business_date__lt=window["endExclusive"])
    spec = {k: v for k, v in request.items() if k not in {"operation", "cursor"}}
    binding = digest({"schema": SCHEMA_VERSION, "source": "erp_sales", "query": spec, "revision": before})
    cursor, last_id = request["cursor"], 0
    if cursor:
        try:
            old = signing.loads(cursor, salt=SALT, max_age=3600)
        except signing.BadSignature as error:
            raise SalesRequestError("分析游标无效或过期，须重新收集") from error
        if not isinstance(old, dict) or old.get("binding") != binding or type(old.get("lastId")) is not int or old["lastId"] < 1:
            raise SalesRequestError("来源或查询已变化，须重新收集")
        last_id = old["lastId"]
    metrics = expressions()
    control, source_coverage, available, identity_check = None, None, None, None
    if not cursor:
        total = rows.aggregate(rowCount=Count("id"), **{key: Sum(value) for key, value in metrics.items()})
        control = {"rowCount": total.pop("rowCount"), "typedTotals": {key: value or 0 for key, value in total.items()}}
        source_coverage = coverage(window, [day.isoformat() for day in rows.values_list("business_date", flat=True).distinct()])
        dates = base.aggregate(firstDate=Min("business_date"), lastDate=Max("business_date"))
        available = {key: value.isoformat() if value else None for key, value in dates.items()}
        if not control["rowCount"]:
            channels = list(SalesOrderLine.objects.filter(is_business_row=True, platform=request["platform"], shop_name=request["shop"])
                .values_list("channel", flat=True).distinct().order_by("channel")[:21])
            identity_check = {"status": "no_rows_requires_identity_and_coverage_review", "knownChannels": channels[:20],
                "channelsTruncated": len(channels) > 20, "requestedChannelSeen": True if request["channel"] in channels else None if len(channels) > 20 else False,
                "meaning": "仅列精确平台和店铺的历史渠道，不自动替换请求；无记录不等于零经营。"}
    selected = list(rows.filter(id__gt=last_id).order_by("id").annotate(**metrics).values(
        "id", "source_row_hash", "last_import_batch_id", "business_date", "product_code", "online_spec_code", "resolved_category", *metrics)[:request["limit"]+1])
    more = len(selected) > request["limit"]
    items = []
    for row in selected[:request["limit"]]:
        values = {key: row[key] for key in metrics}
        if any(type(v) is not int or abs(v) > MAX_SAFE_INTEGER for v in values.values()):
            raise SalesRequestError("销售指标超出无损整数范围")
        items.append({"rowId": str(row["id"]), "sourceRowHash": row["source_row_hash"], "batchId": row["last_import_batch_id"],
            "platform": request["platform"], "shopName": request["shop"], "channel": request["channel"],
            "date": row["business_date"].isoformat(), "productCode": row["product_code"], "onlineSpecCode": row["online_spec_code"] or None,
            "category": row["resolved_category"], "metrics": values})
    from business_analysis.contracts import bounded_page_items
    try:
        items, more = bounded_page_items(items, more)
    except AnalysisContractError as error:
        raise SalesRequestError(str(error)) from error
    if control and any(abs(v) > MAX_SAFE_INTEGER for v in control["typedTotals"].values()):
        raise SalesRequestError("销售控制汇总超出无损整数范围")
    if before != revision_token():
        raise SalesRequestError("销售来源在取数期间变化，须重新收集")
    return {"schemaVersion": SCHEMA_VERSION, "source": "erp_sales", "sourceRef": binding, "sourceRevision": before,
        "filters": {**spec, "periods": periods}, "monetaryUnit": "CNY_CENT", "control": control,
        "coverage": source_coverage, "availableDates": available, "identityCheck": identity_check, "items": items,
        "metricSemantics": {"date": "business_date（发货业务日），不自动替换为下单日或付款日", "netSalesCents": "正向分摊金额减负向分摊金额绝对值，已排除刷刷仓；含配件及补差价",
            "quantity": "遵守 is_net_quantity_row，排除配件/未分类源及补差价；按数量符号拆分",
            "grossProfitCents": "净销售减源成本，不扣费用；源报告毛利另列，不按关键词分摊成本",
            "identity": "平台/店铺/渠道及投影键同时精确匹配，无模糊别名或跨店回退"},
        "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
        "pagination": {"hasMore": more, "nextCursor": signing.dumps({"binding": binding, "lastId": int(items[-1]["rowId"])}, salt=SALT) if more else None, "limit": request["limit"]}}
