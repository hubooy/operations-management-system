"""Daily TOP sample facts with explicit ranges, never whole-market totals."""
from django.core import signing
from django.db.models import Count, F, Max, Min, Sum
from business_analysis.contracts import AnalysisContractError, MAX_SAFE_INTEGER, SCHEMA_VERSION, comparison_periods, coverage, digest
from .errors import MarketApiError
from .models import MarketRankingEntry
from .revisions import revision_value

FIELDS = {"operation", "platform", "category", "scope", "rankingDimension", "priceBandFilter", "startDate", "endDate", "window", "limit", "cursor"}
METRICS = {"sampleGmvLowerCents": "gmv_low_cents", "sampleGmvUpperCents": "gmv_high_cents",
    "sampleQuantityLower": "quantity_low", "sampleQuantityUpper": "quantity_high",
    "productDayVisitorsLower": "visitors_low", "productDayVisitorsUpper": "visitors_high"}
SALT = "market-business-analysis-v1"


def validate(payload):
    required = FIELDS - {"window", "limit", "cursor"}
    if not isinstance(payload, dict) or set(payload) - FIELDS or not required <= set(payload):
        raise MarketApiError("市场分析字段集合无效")
    query = dict(payload)
    for key in ("category", "scope", "priceBandFilter"):
        value = query[key]
        if not isinstance(value, str) or not value or value.strip() != value or len(value) > 200 or any(ord(c) < 32 for c in value):
            raise MarketApiError("市场榜单条件须精确指定")
    if query["platform"] != "京东" or query["rankingDimension"] not in ("SKU", "SPU"):
        raise MarketApiError("市场分析平台或榜单维度不支持")
    try:
        comparison_periods(query["startDate"], query["endDate"])
    except AnalysisContractError as error:
        raise MarketApiError(str(error)) from error
    query.setdefault("window", "current")
    if query["window"] not in ("current", "previous", "yearAgo"):
        raise MarketApiError("比较窗口无效")
    query.setdefault("limit", 10)
    if type(query["limit"]) is not int or not 1 <= query["limit"] <= 100:
        raise MarketApiError("每页记录数必须为 1—100")
    query.setdefault("cursor", None)
    if query["cursor"] is not None and (not isinstance(query["cursor"], str) or not 1 <= len(query["cursor"]) <= 1600):
        raise MarketApiError("市场分析游标无效")
    return query


def read_page(principal, query):
    if principal.role != "admin" or principal.scope is not None:
        raise MarketApiError("分析明细仅向无范围限制管理员开放", code="access_denied", status=403)
    query = validate(query)
    before = revision_value()
    periods = comparison_periods(query["startDate"], query["endDate"])
    window = periods[query["window"]]
    base = MarketRankingEntry.objects.filter(category=query["category"], scope=query["scope"],
        ranking_dimension=query["rankingDimension"], price_band_filter=query["priceBandFilter"])
    daily = base.filter(period_start=F("period_end"))
    rows = daily.filter(period_end__gte=window["startDate"], period_end__lt=window["endExclusive"])
    filters = {key: value for key, value in query.items() if key not in {"operation", "cursor", "startDate", "endDate"}}
    filters["periods"] = periods
    filters["shop"] = ""  # Market sample has no owning shop; never label it as the user's store.
    binding = digest({"schema": SCHEMA_VERSION, "source": "market_daily_top", "query": filters, "revision": before})
    cursor, last_id = query["cursor"], 0
    if cursor:
        try:
            decoded = signing.loads(cursor, salt=SALT, max_age=3600)
        except signing.BadSignature as error:
            raise MarketApiError("市场分析游标无效或过期", code="invalid_cursor", status=409) from error
        if not isinstance(decoded, dict) or decoded.get("binding") != binding or type(decoded.get("lastId")) is not int or decoded["lastId"] < 1:
            raise MarketApiError("来源或筛选已变化，须重新收集", code="analysis_revision_changed", status=409)
        last_id = decoded["lastId"]
    control, source_coverage, available, excluded = None, None, None, None
    if not cursor:
        totals = rows.aggregate(rowCount=Count("id"), **{key: Sum(column) for key, column in METRICS.items()})
        control = {"rowCount": totals.pop("rowCount"), "typedTotals": {key: value or 0 for key, value in totals.items()}}
        if any(abs(value) > MAX_SAFE_INTEGER for value in control["typedTotals"].values()):
            raise MarketApiError("样本控制金额超出无损整数范围")
        source_coverage = coverage(window, rows.values_list("period_end", flat=True).distinct())
        available = daily.aggregate(firstDate=Min("period_end"), lastDate=Max("period_end"))
        excluded = base.exclude(period_start=F("period_end")).filter(period_start__lte=window["endDate"], period_end__gte=window["startDate"]).count()
    columns = ["id", "natural_key", "last_import_batch_id", "period_end", "sku_code", "product_name", "brand", "category", "scope",
        "ranking_dimension", "rank", "price_low_cents", "price_high_cents", "price_estimated", "operation_mode", *METRICS.values()]
    selected = list(rows.filter(id__gt=last_id).order_by("id").values(*columns)[:query["limit"]+1])
    more = len(selected) > query["limit"]
    items = []
    for row in selected[:query["limit"]]:
        values = {key: row[column] for key, column in METRICS.items()}
        for low, high in (("gmv_low_cents", "gmv_high_cents"), ("quantity_low", "quantity_high"), ("visitors_low", "visitors_high"), ("price_low_cents", "price_high_cents")):
            if any(row[k] is not None and (type(row[k]) is not int or not 0 <= row[k] <= MAX_SAFE_INTEGER) for k in (low, high)) or row[low] is not None and row[high] is not None and row[low] > row[high]:
                raise MarketApiError("市场源区间无效，不能推定精确值", code="invalid_source_range", status=422)
        items.append({"rowId": str(row["id"]), "sourceRowHash": digest(row["natural_key"]), "batchId": row["last_import_batch_id"],
            "platform": "京东", "shopName": "", "date": row["period_end"], "category": row["category"],
            "skuId": row["sku_code"] if row["ranking_dimension"] == "SKU" else None,
            "spuId": row["sku_code"] if row["ranking_dimension"] == "SPU" else None, "productName": row["product_name"],
            "dimensions": {"brand": row["brand"] or None, "marketScope": row["scope"], "operationMode": row["operation_mode"]},
            "sample": {"rank": row["rank"], "priceLowerCents": row["price_low_cents"], "priceUpperCents": row["price_high_cents"], "priceEstimated": row["price_estimated"]},
            "metrics": values})
    from business_analysis.contracts import bounded_page_items
    try:
        items, more = bounded_page_items(items, more)
    except AnalysisContractError as error:
        raise MarketApiError(str(error), status=422) from error
    if revision_value() != before:
        raise MarketApiError("市场来源在读取期间变化", code="analysis_revision_changed", status=409)
    return {"schemaVersion": SCHEMA_VERSION, "source": "market_daily_top", "sourceDataset": "market_daily_top", "sourceRef": binding,
        "sourceRevision": before, "filters": filters, "monetaryUnit": "CNY_CENT", "items": items, "control": control,
        "coverage": source_coverage, "availableDates": available, "excludedOverlappingPeriodRows": excluded,
        "metricSemantics": {"population": "固定类目/榜单范围/榜单维度/价格筛选的逐日TOP样本，不是全行业规模或真实份额。",
            "bounds": "仅保留导入源区间上下界；不把中点或模型估算当精确成交，缺界为null。",
            "period": "只累加单日榜单，排除重叠周/月区间；商品进出榜会改变样本构成。",
            "visitors": "商品日访客上下界累计，不是跨日/跨商品去重UV。", "shopName": "空字符串表示市场样本不属于用户店铺。"},
        "pageEvidence": {"rowCount": len(items), "sha256": digest(items)},
        "pagination": {"hasMore": more, "nextCursor": signing.dumps({"binding": binding, "lastId": int(items[-1]["rowId"])}, salt=SALT) if more else None, "limit": query["limit"]}}
