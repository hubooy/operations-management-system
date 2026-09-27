from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from time import monotonic

from django.db import connection, transaction
from django.db.models import Count, F, Max, Min, Q, Sum

from netshop.sales_client import read_sales_consumer
from sales.auth import Principal

from .errors import MarketApiError
from .filter_cache import cached_filters
from .models import (
    MarketAnnotationCloudRun,
    MarketAnnotationConcurrencySetting,
    MarketAnnotationItem,
    MarketAnnotationJob,
    MarketAnnotationPromptVersion,
    MarketBrandRecognitionJob,
    MarketBrandSeed,
    MarketDownloadConfig,
    MarketDownloadTask,
    MarketImageCache,
    MarketImageCacheJob,
    MarketImportBatch,
    MarketMasterAuditLog,
    MarketMasterIdentity,
    MarketMasterMappingRule,
    MarketNetshopProjection,
    MarketNetshopProjectionControl,
    MarketPriceBandItem,
    MarketPriceBandVersion,
    MarketPriceSnapshot,
    MarketRankingEntry,
    MarketSkuAnnotation,
    MarketSubcategoryTaxonomy,
)
from .revisions import iso
from .ranking_query import ranking_page
from .serialization import batch_payload


MAX_ANALYTICS_ROWS = 250_000
MAX_PAGE = 1_000_000
MAX_PAGE_SIZE = 100
MAX_FILTER_VALUES = 100
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MARKET_SALES_PRODUCT_CHUNK_SIZE = 1_000
MARKET_SALES_DATE_CHUNK_DAYS = 730
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNCONFIRMED_PRICE = "未确认价格"
FORMAL_OFFICIAL_PRICE_TYPES = {"标准售价", "到手价", "券后价"}
INVALID_OFFICIAL_PRICE_TYPES = {
    "",
    "定金",
    "分期金额",
    "无法判断",
    "起售价",
    "价格区间",
    "最低规格价格",
}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
COMMERCIAL_DIRECT_DRINKING_PROFILE = {
    "category": "商用净饮水设备",
    "coreSubcategories": [
        "商用直饮机",
        "净饮一体机",
        "校园饮水机",
        "工厂饮水机",
        "幼儿园饮水机",
        "商用管线机",
    ],
    "adjacentSubcategories": [
        "桶装水饮水机",
        "商用咖啡机",
        "商用饮料机",
        "滤芯及饮水配件",
        "其他",
    ],
    "adjacentCategories": ["商用净水设备", "商用开水器蒸气奶泡机"],
}
PRODUCT_SIGNAL_RULES = [
    ("使用场景", "校园", re.compile(r"校园|学校|学生")),
    ("使用场景", "幼儿园", re.compile(r"幼儿园|幼教")),
    ("使用场景", "工厂", re.compile(r"工厂|车间|工地")),
    ("使用场景", "办公", re.compile(r"办公|办公室|企业|公司")),
    ("使用场景", "餐饮", re.compile(r"餐饮|饭店|酒店|食堂")),
    ("过滤方案", "RO反渗透", re.compile(r"反渗透|\bRO\b", re.IGNORECASE)),
    ("过滤方案", "超滤", re.compile(r"超滤")),
    ("过滤方案", "紫外抑菌", re.compile(r"紫外|\bUV\b", re.IGNORECASE)),
    ("产品形态", "立式/柜式", re.compile(r"立式|柜式")),
    ("产品形态", "台式", re.compile(r"台式")),
    ("产品形态", "壁挂式", re.compile(r"壁挂")),
    ("供水与温控", "管线供水", re.compile(r"管线|自来水|市政水")),
    ("供水与温控", "桶装水", re.compile(r"桶装水|水桶")),
    ("供水与温控", "即热/步进", re.compile(r"即热|步进")),
    ("供水与温控", "制冷/冰水", re.compile(r"制冷|冰水|冰温热")),
    ("供水与温控", "开水/热水", re.compile(r"开水|热水|一开|二开|三开")),
    ("供水与温控", "温水", re.compile(r"温水|一温|二温|三温")),
    ("服务承诺", "安装服务", re.compile(r"安装|上门")),
    ("服务承诺", "质保/保修", re.compile(r"质保|保修|联保")),
    ("服务承诺", "滤芯供应", re.compile(r"滤芯|耗材")),
]


def _error(message: str) -> MarketApiError:
    return MarketApiError(message)


def _integer(value: object, fallback: int, minimum: int, maximum: int, label: str) -> int:
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise _error(f"{label} 参数无效")
    return value


def _texts(value: object, label: str, *, maximum: int = MAX_FILTER_VALUES) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise _error(f"{label} 参数无效")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 200:
            raise _error(f"{label} 参数无效")
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _date(value: object, label: str) -> str | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        raise _error(f"{label} 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise _error(f"{label} 不是有效日期") from error
    return value


def validate_filters(value: object) -> dict[str, object]:
    if value is None:
        return {
            "categories": [],
            "scopes": [],
            "brands": [],
            "priceBands": [],
            "rankingDimensions": [],
            "operationModes": [],
            "subcategories": [],
            "query": "",
            "startDate": None,
            "endDate": None,
        }
    if not isinstance(value, dict):
        raise _error("filters 必须是对象")
    allowed = {
        "categories",
        "scopes",
        "brands",
        "priceBands",
        "rankingDimensions",
        "operationModes",
        "subcategories",
        "query",
        "startDate",
        "endDate",
    }
    if not set(value).issubset(allowed):
        raise _error("filters 包含未知字段")
    filters = {
        key: _texts(value.get(key), key)
        for key in (
            "categories",
            "scopes",
            "brands",
            "priceBands",
            "rankingDimensions",
            "operationModes",
            "subcategories",
        )
    }
    query = value.get("query", "")
    if not isinstance(query, str) or len(query.strip()) > 120:
        raise _error("query 参数无效")
    filters["query"] = query.strip()
    filters["startDate"] = _date(value.get("startDate"), "startDate")
    filters["endDate"] = _date(value.get("endDate"), "endDate")
    if filters["startDate"] and filters["endDate"] and filters["startDate"] > filters["endDate"]:
        raise _error("startDate 不能晚于 endDate")
    return filters


def _queryset(filters: dict[str, object]):
    query = MarketRankingEntry.objects.all()
    search = str(filters.get("query", ""))
    if search:
        query = query.filter(
            Q(sku_code__icontains=search)
            | Q(product_name__icontains=search)
            | Q(brand__icontains=search)
        )
    field_map = {
        "categories": "category__in",
        "scopes": "scope__in",
        "brands": "brand__in",
        "rankingDimensions": "ranking_dimension__in",
        "operationModes": "operation_mode__in",
        "subcategories": "subcategory__in",
    }
    for key, lookup in field_map.items():
        values = filters[key]
        if values:
            query = query.filter(**{lookup: values})
    if filters["startDate"]:
        query = query.filter(period_end__gte=filters["startDate"])
    if filters["endDate"]:
        query = query.filter(period_start__lte=filters["endDate"])
    return query


def _preferred_rows(filters: dict[str, object]) -> list[MarketRankingEntry]:
    query = _queryset(filters).order_by("id")
    if query.count() > MAX_ANALYTICS_ROWS:
        raise MarketApiError(
            "市场分析范围过大，请缩小日期或筛选范围",
            code="payload_too_large",
            status=413,
        )
    selected: dict[tuple[object, ...], MarketRankingEntry] = {}
    preference: dict[tuple[object, ...], tuple[int, str, int]] = {}
    for row in query.iterator(chunk_size=2_000):
        key = (
            row.period_start,
            row.period_end,
            row.category,
            row.scope,
            row.ranking_dimension,
            row.sku_code,
        )
        price_preference = 0 if row.price_band_filter == "全部" else 1 if not row.price_band_filter else 2
        candidate = (price_preference, row.price_band_filter, -row.id)
        if key not in preference or candidate < preference[key]:
            preference[key] = candidate
            selected[key] = row
    return list(selected.values())


def _snapshot_map(rows: Iterable[MarketRankingEntry]) -> dict[tuple[str, str, str, str, str], MarketPriceSnapshot]:
    keys = {
        (row.category, row.scope, row.sku_code, row.ranking_dimension, row.period_end[:7])
        for row in rows
    }
    result: dict[tuple[str, str, str, str, str], MarketPriceSnapshot] = {}
    categories = {key[0] for key in keys}
    if not categories:
        return result
    identity_filter = Q()
    # Pages/trends have few identities; use their full key rather than scanning
    # every historical snapshot in the selected categories.
    if len(keys) <= 100:
        for category, scope, code, dimension, month in keys:
            identity_filter |= Q(category=category, scope=scope, sku_code=code, ranking_dimension=dimension, month=month)
    else:
        identity_filter = Q(category__in=categories, month__in={key[4] for key in keys})
    for snapshot in MarketPriceSnapshot.objects.filter(identity_filter).iterator(chunk_size=2_000):
        key = (
            snapshot.category,
            snapshot.scope,
            snapshot.sku_code,
            snapshot.ranking_dimension,
            snapshot.month,
        )
        if key in keys:
            result[key] = snapshot
    return result


def _official_price(snapshot: MarketPriceSnapshot | None) -> int | None:
    if (
        snapshot
        and snapshot.confirmation_status == "confirmed"
        and snapshot.ai_price_type in FORMAL_OFFICIAL_PRICE_TYPES
        and SHA256_RE.fullmatch(snapshot.image_content_sha256)
        and snapshot.confirmed_market_price_cents is not None
        and snapshot.confirmed_market_price_cents > 0
    ):
        return int(snapshot.confirmed_market_price_cents)
    return None


def _price_band_versions() -> dict[str, list[tuple[str, int | None, int | None, str, int]]]:
    versions = {
        item.id: item
        for item in MarketPriceBandVersion.objects.filter(status="published").order_by(
            "-effective_from", "-version"
        )
    }
    result: dict[str, list[tuple[str, int | None, int | None, str, int]]] = defaultdict(list)
    for item in MarketPriceBandItem.objects.filter(version_id__in=versions).order_by("sort_order"):
        version = versions[item.version_id]
        result[version.category].append(
            (item.label, item.min_cents, item.max_cents, version.effective_from, version.version)
        )
    return result


def _price_band(
    price: int | None,
    *,
    category: str,
    period_end: str,
    versions: dict[str, list[tuple[str, int | None, int | None, str, int]]],
) -> str:
    if price is None:
        return UNCONFIRMED_PRICE
    candidates: list[tuple[int, str, int, str]] = []
    for version_category in (category, "*"):
        for label, minimum, maximum, effective_from, version in versions.get(version_category, []):
            if effective_from <= period_end and price >= (minimum if minimum is not None else -MAX_ANALYTICS_ROWS**4) and (
                maximum is None or price < maximum
            ):
                candidates.append((0 if version_category == category else 1, effective_from, version, label))
    if not candidates:
        return UNCONFIRMED_PRICE
    candidates.sort(key=lambda item: (item[0], -int(item[1].replace("-", "")), -item[2]))
    return candidates[0][3]


def _projection_metrics(rows: list[MarketRankingEntry]) -> tuple[dict[int, int], set[str]]:
    if not rows:
        return {}, set()
    control = MarketNetshopProjectionControl.objects.filter(id=1).first()
    if not control or not control.active_revision:
        return {}, set()
    sku_codes = {row.sku_code for row in rows if row.ranking_dimension != "SPU"}
    spu_codes = {row.sku_code for row in rows if row.ranking_dimension == "SPU"}
    owned: set[str] = set()
    base = MarketNetshopProjection.objects.filter(projection_revision=control.active_revision)
    identities = base.filter(kind="identity")
    if len(sku_codes) + len(spu_codes) <= 1_000:
        identities = identities.filter(Q(sku_id__in=sku_codes) | Q(spu_id__in=spu_codes) | Q(product_code__in=sku_codes))
    for sku_id, spu_id, product_code in identities.values_list(
        "sku_id", "spu_id", "product_code"
    ).iterator(chunk_size=2_000):
        if sku_id in sku_codes or spu_id in spu_codes or product_code in sku_codes:
            owned.update(item for item in (sku_id, spu_id, product_code) if item)

    metrics_by_code_date: dict[tuple[str, str, str], int] = defaultdict(int)
    metric_rows = base.filter(
        kind="metric",
        source="jd_sku_daily",
        business_date__gte=min(row.period_start for row in rows),
        business_date__lte=max(row.period_end for row in rows),
    )
    if len(sku_codes) + len(spu_codes) <= 1_000:
        metric_rows = metric_rows.filter(Q(dataset="sku_daily", sku_id__in=sku_codes) | Q(dataset="spu_daily", spu_id__in=spu_codes))
    metric_rows = metric_rows.values_list(
        "dataset",
        "sku_id",
        "spu_id",
        "business_date",
        "transaction_amount_cents",
    )
    for dataset, sku_id, spu_id, business_date, amount in metric_rows.iterator(chunk_size=2_000):
        if dataset == "sku_daily" and sku_id in sku_codes:
            metrics_by_code_date[("SKU", sku_id, business_date)] += int(amount)
        elif dataset == "spu_daily" and spu_id in spu_codes:
            metrics_by_code_date[("SPU", spu_id, business_date)] += int(amount)

    amounts_by_identity: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for (dimension, code, business_date), amount in metrics_by_code_date.items():
        amounts_by_identity[(dimension, code)].append((business_date, amount))
    series_by_identity: dict[tuple[str, str], tuple[list[str], list[int]]] = {}
    for identity, values in amounts_by_identity.items():
        ordered = sorted(values)
        dates: list[str] = []
        prefix = [0]
        for business_date, amount in ordered:
            dates.append(business_date)
            prefix.append(prefix[-1] + amount)
        series_by_identity[identity] = (dates, prefix)

    effective: dict[int, int] = {}
    for row in rows:
        dimension = "SPU" if row.ranking_dimension == "SPU" else "SKU"
        dates, prefix = series_by_identity.get((dimension, row.sku_code), ([], [0]))
        start = bisect_left(dates, row.period_start)
        end = bisect_right(dates, row.period_end)
        real = prefix[end] - prefix[start]
        effective[row.id] = real if real > 0 else int(row.gmv_cents)
    return effective, owned


def _sales_date_ranges(filters: dict[str, object]) -> list[tuple[str | None, str | None]]:
    start_text = filters.get("startDate")
    end_text = filters.get("endDate")
    if start_text is None and end_text is None:
        return [(None, None)]
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        raise _error("市场销售周期必须同时提供开始和结束日期")
    start = date.fromisoformat(start_text)
    exclusive_end = date.fromisoformat(end_text) + timedelta(days=1)
    ranges: list[tuple[str | None, str | None]] = []
    cursor = start
    while cursor < exclusive_end:
        chunk_end = min(cursor + timedelta(days=MARKET_SALES_DATE_CHUNK_DAYS), exclusive_end)
        ranges.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end
    return ranges


def _invalid_sales_metrics() -> MarketApiError:
    return MarketApiError(
        "Django 销售读取服务返回无效",
        code="service_unavailable",
        status=503,
    )


def _sales_metrics(
    principal: Principal,
    rows: list[MarketRankingEntry],
    filters: dict[str, object],
    loader: Callable[[Principal, dict[str, object]], tuple[dict[str, object], str]] | None = None,
) -> tuple[dict[str, dict[str, object]], str]:
    product_codes = sorted({row.sku_code.strip() for row in rows if row.sku_code.strip()})
    if len(product_codes) > 20_000:
        raise MarketApiError(
            "市场关联销售商品数超过安全上限", code="payload_too_large", status=413
        )
    if not product_codes:
        return {}, "0:0"
    result: dict[str, dict[str, object]] = {
        product_code: {"owned": False, "ownSalesCents": 0}
        for product_code in product_codes
    }
    revision: str | None = None
    date_ranges = _sales_date_ranges(filters)
    for offset in range(0, len(product_codes), MARKET_SALES_PRODUCT_CHUNK_SIZE):
        chunk = product_codes[offset : offset + MARKET_SALES_PRODUCT_CHUNK_SIZE]
        expected_codes = set(chunk)
        for start_date, end_date in date_ranges:
            request = {
                "operation": "market_product_metrics",
                "productCodes": chunk,
                "startDate": start_date,
                "endDate": end_date,
            }
            try:
                data, current_revision = (loader or read_sales_consumer)(principal, request)
            except Exception as error:
                raise MarketApiError(
                    "Django 销售读取服务暂时不可用",
                    code="service_unavailable",
                    status=503,
                ) from error
            if revision is not None and current_revision != revision:
                raise _invalid_sales_metrics()
            revision = current_revision
            values = data.get("rows") if isinstance(data, dict) else None
            if not isinstance(values, list) or len(values) != len(chunk):
                raise _invalid_sales_metrics()
            returned_codes: set[str] = set()
            for item in values:
                if not isinstance(item, dict) or set(item) != {"productCode", "owned", "ownSalesCents"}:
                    raise _invalid_sales_metrics()
                code = item["productCode"]
                owned = item["owned"]
                own_sales_cents = item["ownSalesCents"]
                if (
                    not isinstance(code, str)
                    or code not in expected_codes
                    or code in returned_codes
                    or type(owned) is not bool
                    or type(own_sales_cents) is not int
                    or abs(own_sales_cents) > MAX_SAFE_INTEGER
                ):
                    raise _invalid_sales_metrics()
                returned_codes.add(code)
                current = result[code]
                combined_sales = int(current["ownSalesCents"]) + own_sales_cents
                if abs(combined_sales) > MAX_SAFE_INTEGER:
                    raise _invalid_sales_metrics()
                current["owned"] = bool(current["owned"]) or owned
                current["ownSalesCents"] = combined_sales
            if returned_codes != expected_codes:
                raise _invalid_sales_metrics()
    return result, revision or "0:0"


def _option(values: Iterable[str]) -> list[dict[str, object]]:
    counts = Counter(value or "" for value in values)
    return [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if value
    ]


def _database_options(queryset, field: str) -> list[dict[str, object]]:
    return [{"value": row[field], "count": row["count"]}
            for row in queryset.exclude(**{field: ""}).order_by().values(field)
            .annotate(count=Count("pk")).order_by("-count", field)]


def filter_options() -> dict[str, object]:
    return cached_filters(_load_filter_options)


def _load_filter_options() -> dict[str, object]:
    """Independent of overview success, including an empty/oversized range."""
    query = MarketRankingEntry.objects.all()
    fields = (
        ("categories", "category"), ("scopes", "scope"), ("brands", "brand"),
        ("rankingDimensions", "ranking_dimension"), ("operationModes", "operation_mode"),
        ("subcategories", "subcategory"),
    )
    if connection.vendor == "postgresql":
        # Each facet has a narrow index. Avoid reading wide raw import payloads
        # and starting parallel workers for these low-cardinality aggregates.
        # SET LOCAL is scoped to this read transaction; statement_timeout stays unchanged.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SHOW max_parallel_workers_per_gather")
                previous_parallelism = cursor.fetchone()[0]
                cursor.execute("SELECT setting::integer FROM pg_settings WHERE name='statement_timeout'")
                statement_timeout = cursor.fetchone()[0]
                cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
            deadline = monotonic() + 20
            options = {}
            for name, field in fields:
                remaining = int((deadline - monotonic()) * 1000)
                if remaining <= 0:
                    raise MarketApiError("筛选统计超时，请稍后重新加载。", status=503, code="query_timeout")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(min(statement_timeout or 7000, remaining))])
                options[name] = _database_options(query, field)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('max_parallel_workers_per_gather', %s, true)", [previous_parallelism])
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(statement_timeout)])
    else:
        options = {name: _database_options(query, field) for name, field in fields}
    # Published labels provide a recovery control without evaluating all rows.
    labels = MarketPriceBandItem.objects.filter(version_id__in=MarketPriceBandVersion.objects.filter(
        status="published").values("id")).values_list("label", flat=True).distinct()
    return {**options, "priceBands": [{"value": label, "count": 0} for label in sorted({UNCONFIRMED_PRICE, *labels})]}


def _batch_list(limit: int = 8) -> list[dict[str, object]]:
    return [batch_payload(batch) for batch in MarketImportBatch.objects.order_by("-created_at")[:limit]]


def _identity(item: dict[str, object]) -> tuple[str, str, str, str]:
    row = item["row"]
    return (row.category, row.scope, row.ranking_dimension, row.sku_code)


def _shift_month(period: str, offset: int) -> str:
    year, month = (int(value) for value in period.split("-", 1))
    shifted = year * 12 + month - 1 + offset
    return f"{shifted // 12:04d}-{shifted % 12 + 1:02d}"


def _growth_bps(current: int, previous: int) -> int | None:
    return round((current - previous) / previous * 10_000) if previous > 0 else None


def _ratio_bps(numerator: int, denominator: int) -> int | None:
    return round(numerator / denominator * 10_000) if denominator > 0 else None


def _median(values: Iterable[int]) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2)


def _percentile_rank(value: float, values: list[float]) -> int:
    if not values:
        return 0
    return round(sum(1 for candidate in values if candidate <= value) / len(values) * 10_000)


def _missing_months(months: list[str], *, limit: int = 8) -> list[str]:
    if len(months) < 2:
        return []
    present = set(months)
    cursor = months[0]
    result: list[str] = []
    while cursor != months[-1]:
        cursor = _shift_month(cursor, 1)
        if cursor not in present:
            result.append(cursor)
            if len(result) >= limit:
                break
    return result


def _monthly_growth_by_value(
    rows: list[dict[str, object]],
    value: Callable[[dict[str, object]], str],
) -> dict[str, dict[str, object]]:
    periods = sorted({item["row"].period_end[:7] for item in rows})
    if not periods:
        return {}
    latest = periods[-1]
    previous = _shift_month(latest, -1)
    year_ago = _shift_month(latest, -12)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for item in rows:
        totals[(value(item), item["row"].period_end[:7])] += int(item["gmv"])
    values = {key[0] for key in totals}
    return {
        name: {
            "latestPeriod": latest,
            "monthOverMonthBps": (
                _growth_bps(totals[(name, latest)], totals[(name, previous)])
                if (name, latest) in totals and (name, previous) in totals
                else None
            ),
            "yearOverYearBps": (
                _growth_bps(totals[(name, latest)], totals[(name, year_ago)])
                if (name, latest) in totals and (name, year_ago) in totals
                else None
            ),
        }
        for name in values
    }


def _industry_scenario(subcategory: str) -> str:
    for pattern, label in (
        (r"校园|学校", "校园"),
        (r"幼儿园", "幼儿园"),
        (r"工厂|车间", "工厂"),
        (r"管线", "办公/餐饮"),
        (r"桶装水", "桶装水"),
        (r"配件|滤芯", "配件耗材"),
        (r"直饮|净饮", "通用商用"),
    ):
        if re.search(pattern, subcategory):
            return label
    return "其他/待确认"


def _product_signals(rows: list[dict[str, object]]) -> dict[str, object]:
    latest: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for item in rows:
        key = _identity(item)
        current = latest.get(key)
        if current is None or (
            item["row"].period_end,
            int(item["gmv"]),
            item["row"].id,
        ) > (
            current["row"].period_end,
            int(current["gmv"]),
            current["row"].id,
        ):
            latest[key] = item
    signals: dict[tuple[str, str], dict[str, object]] = {}
    for item in latest.values():
        row = item["row"]
        source = f"{row.product_name} {row.subcategory}"
        matched = [
            (group, label)
            for group, label, pattern in PRODUCT_SIGNAL_RULES
            if pattern.search(source)
        ]
        capacities = [int(value) for value in re.findall(r"(\d{1,4})\s*人", source) if int(value) > 0]
        if capacities:
            maximum = max(capacities)
            label = "≤50人" if maximum <= 50 else "51–100人" if maximum <= 100 else "101–300人" if maximum <= 300 else "300人以上"
            matched.append(("供水能力", label))
        for group, label in matched:
            signal = signals.setdefault(
                (group, label),
                {"group": group, "label": label, "count": 0, "examples": []},
            )
            signal["count"] = int(signal["count"]) + 1
            if row.product_name and row.product_name not in signal["examples"] and len(signal["examples"]) < 3:
                signal["examples"].append(row.product_name)
    sample_size = len(latest)
    result = [
        {
            **signal,
            "shareBps": round(int(signal["count"]) / sample_size * 10_000) if sample_size else 0,
        }
        for signal in signals.values()
    ]
    result.sort(key=lambda item: (-int(item["count"]), str(item["group"]), str(item["label"])))
    return {
        "sampleSize": sample_size,
        "source": "商品标题与已确认细分类目",
        "signals": result[:20],
    }


def _build_industry_report(
    filters: dict[str, object],
    rows: list[dict[str, object]],
    total_gmv_cents: int,
) -> dict[str, object]:
    report = _empty_industry_report(filters)
    if not rows:
        return report

    by_month: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in rows:
        by_month[item["row"].period_end[:7]].append(item)
    periods = sorted(by_month)
    present = set(periods)
    missing = _missing_months(periods)
    continuous = not missing
    latest_period = periods[-1]
    previous_period = _shift_month(latest_period, -1)
    year_ago_period = _shift_month(latest_period, -12)

    identities = {_identity(item) for item in rows}
    unknown_brand = {_identity(item) for item in rows if not item["row"].brand}
    unclassified = {_identity(item) for item in rows if not item["row"].subcategory}
    pending_price = {_identity(item) for item in rows if item["official"] is None}
    category_count = len({item["row"].category for item in rows})
    scope_count = len({item["row"].scope for item in rows})
    dimension_count = len({item["row"].ranking_dimension for item in rows})
    identity_ready = category_count == scope_count == dimension_count == 1
    coverage_ready = len(periods) >= 12 and continuous
    comparison_ready = previous_period in present

    month_gmv = {
        period: sum(int(item["gmv"]) for item in values)
        for period, values in by_month.items()
    }
    lifecycle: list[dict[str, object]] = []
    month_identities = {
        period: {_identity(item) for item in values}
        for period, values in by_month.items()
    }
    for period in periods[-24:]:
        previous = _shift_month(period, -1)
        following = _shift_month(period, 1)
        lifecycle.append(
            {
                "period": period,
                "entryCount": (
                    len(month_identities[period] - month_identities[previous])
                    if previous in month_identities
                    else None
                ),
                "exitCount": (
                    len(month_identities[period] - month_identities[following])
                    if following in month_identities
                    else None
                ),
            }
        )
    peak_period = max(periods, key=lambda period: month_gmv[period])
    trough_period = min(periods, key=lambda period: month_gmv[period])
    latest_lifecycle = next(item for item in lifecycle if item["period"] == latest_period)
    latest_comparable_exit = next(
        (item for item in reversed(lifecycle) if item["exitCount"] is not None),
        None,
    )
    report["period"] = {
        "coverageMonths": len(periods),
        "latestPeriod": latest_period,
        "latestGmvCents": month_gmv[latest_period],
        "monthOverMonthBps": (
            _growth_bps(month_gmv[latest_period], month_gmv[previous_period])
            if previous_period in month_gmv
            else None
        ),
        "yearOverYearBps": (
            _growth_bps(month_gmv[latest_period], month_gmv[year_ago_period])
            if year_ago_period in month_gmv
            else None
        ),
        "peak": {"period": peak_period, "gmvCents": month_gmv[peak_period]},
        "trough": {"period": trough_period, "gmvCents": month_gmv[trough_period]},
        "latestEntryCount": latest_lifecycle["entryCount"],
        "latestExitCount": latest_comparable_exit["exitCount"] if latest_comparable_exit else None,
        "latestExitPeriod": latest_comparable_exit["period"] if latest_comparable_exit else None,
    }
    report["lifecycle"] = lifecycle

    modes: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in rows:
        modes[item["row"].operation_mode or "未知"].append(item)
    report["operationModes"] = sorted(
        [
            {
                "operationMode": mode,
                "gmvCents": (gmv := sum(int(item["gmv"]) for item in values)),
                "quantity": (quantity := sum(int(item["row"].quantity) for item in values)),
                "skuCount": (sku_count := len({_identity(item) for item in values})),
                "visitors": (visitors := sum(int(item["row"].visitors) for item in values)),
                "conversionBps": min(10_000, max(0, round(quantity * 10_000 / visitors))) if visitors else None,
                "brandCount": len({item["row"].brand or "未识别品牌" for item in values}),
                "gmvShareBps": round(gmv / total_gmv_cents * 10_000) if total_gmv_cents else 0,
                "averageTransactionPriceCents": round(gmv / quantity) if quantity else None,
                "gmvPerSkuCents": round(gmv / sku_count) if sku_count else 0,
            }
            for mode, values in modes.items()
        ],
        key=lambda item: -int(item["gmvCents"]),
    )

    concentration: list[dict[str, object]] = []
    for period in periods:
        brand_gmv: dict[str, int] = defaultdict(int)
        for item in by_month[period]:
            brand_gmv[item["row"].brand or "未识别品牌"] += int(item["gmv"])
        values = sorted(brand_gmv.values(), reverse=True)
        total = sum(values)
        concentration.append(
            {
                "period": period,
                "gmvCents": total,
                "brandCount": len(brand_gmv),
                "cr3Bps": round(sum(values[:3]) / total * 10_000) if total else 0,
                "cr5Bps": round(sum(values[:5]) / total * 10_000) if total else 0,
            }
        )
    report["brandConcentrationTrend"] = concentration[-24:]

    product_totals: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for item in rows:
        key = _identity(item)
        total = product_totals.setdefault(
            key,
            {"gmv": 0, "quantity": 0, "visitors": 0, "latest": item},
        )
        total["gmv"] = int(total["gmv"]) + int(item["gmv"])
        total["quantity"] = int(total["quantity"]) + int(item["row"].quantity)
        total["visitors"] = int(total["visitors"]) + int(item["row"].visitors)
        current = total["latest"]
        if (item["row"].period_end, int(item["gmv"]), item["row"].id) > (
            current["row"].period_end,
            int(current["gmv"]),
            current["row"].id,
        ):
            total["latest"] = item
    visitor_threshold = _median(int(item["visitors"]) for item in product_totals.values())
    conversion_values = [
        min(10_000, max(0, round(int(item["quantity"]) * 10_000 / int(item["visitors"]))))
        for item in product_totals.values()
        if int(item["visitors"]) > 0
    ]
    conversion_threshold = _median(conversion_values)
    quadrants: dict[str, list[dict[str, object]]] = defaultdict(list)
    for total in product_totals.values():
        visitors = int(total["visitors"])
        quantity = int(total["quantity"])
        conversion = min(10_000, max(0, round(quantity * 10_000 / visitors))) if visitors else None
        high_traffic = visitors >= visitor_threshold
        high_conversion = (conversion or 0) >= conversion_threshold
        quadrant = (
            "high_traffic_high_conversion"
            if high_traffic and high_conversion
            else "high_traffic_low_conversion"
            if high_traffic
            else "low_traffic_high_conversion"
            if high_conversion
            else "low_traffic_low_conversion"
        )
        quadrants[quadrant].append(total)
    quadrant_order = [
        "high_traffic_high_conversion",
        "high_traffic_low_conversion",
        "low_traffic_high_conversion",
        "low_traffic_low_conversion",
    ]
    report["trafficQuadrants"] = []
    for quadrant in quadrant_order:
        values = quadrants.get(quadrant, [])
        if not values:
            continue
        gmv = sum(int(item["gmv"]) for item in values)
        quantity = sum(int(item["quantity"]) for item in values)
        visitors = sum(int(item["visitors"]) for item in values)
        examples = []
        seen_codes: set[str] = set()
        for item in sorted(values, key=lambda value: -int(value["gmv"])):
            latest = item["latest"]["row"]
            if latest.sku_code in seen_codes:
                continue
            seen_codes.add(latest.sku_code)
            examples.append(
                {"skuCode": latest.sku_code, "productName": latest.product_name, "gmvCents": int(item["gmv"])}
            )
            if len(examples) == 3:
                break
        report["trafficQuadrants"].append(
            {
                "quadrant": quadrant,
                "productCount": len(values),
                "gmvCents": gmv,
                "quantity": quantity,
                "visitors": visitors,
                "conversionBps": min(10_000, max(0, round(quantity * 10_000 / visitors))) if visitors else None,
                "visitorThreshold": visitor_threshold,
                "conversionThresholdBps": conversion_threshold,
                "examples": examples,
            }
        )
    report["productSignals"] = _product_signals(rows)

    cell_values: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in rows:
        cell_values[(item["row"].subcategory or "未分类", str(item["priceBand"]))].append(item)
    cells: list[dict[str, object]] = []
    for (subcategory, price_band), values in cell_values.items():
        quantity = sum(int(item["row"].quantity) for item in values)
        visitors = sum(int(item["row"].visitors) for item in values)
        cells.append(
            {
                "subcategory": subcategory,
                "priceBand": price_band,
                "gmvCents": sum(int(item["gmv"]) for item in values),
                "quantity": quantity,
                "skuCount": len({_identity(item) for item in values}),
                "visitors": visitors,
                "conversionBps": min(10_000, max(0, round(quantity * 10_000 / visitors))) if visitors else None,
                "selfGmvCents": sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "自营"),
                "brandCount": len({item["row"].brand or "未识别品牌" for item in values}),
                "latestGmvCents": sum(int(item["gmv"]) for item in values if item["row"].period_end[:7] == latest_period),
                "previousGmvCents": sum(int(item["gmv"]) for item in values if item["row"].period_end[:7] == previous_period),
                "pendingPriceCount": len({_identity(item) for item in values if item["official"] is None}),
            }
        )
    scale_values = [float(cell["gmvCents"]) for cell in cells]
    growth_values = [
        float(growth)
        for cell in cells
        if (growth := _growth_bps(int(cell["latestGmvCents"]), int(cell["previousGmvCents"]))) is not None
    ]
    efficiency_values = [
        float(cell["gmvCents"]) / int(cell["skuCount"]) if int(cell["skuCount"]) else 0.0
        for cell in cells
    ]
    conversion_percentiles = [float(cell["conversionBps"] or 0) for cell in cells]
    global_decision_ready = (
        identity_ready
        and coverage_ready
        and comparison_ready
        and not pending_price
        and not unclassified
    )
    opportunities: list[dict[str, object]] = []
    for cell in cells:
        growth = _growth_bps(int(cell["latestGmvCents"]), int(cell["previousGmvCents"]))
        share = round(int(cell["gmvCents"]) / total_gmv_cents * 10_000) if total_gmv_cents else 0
        self_share = round(int(cell["selfGmvCents"]) / int(cell["gmvCents"]) * 10_000) if int(cell["gmvCents"]) else 0
        pending_share = round(int(cell["pendingPriceCount"]) / int(cell["skuCount"]) * 10_000) if int(cell["skuCount"]) else 0
        scale_percentile = _percentile_rank(float(cell["gmvCents"]), scale_values)
        growth_percentile = 5_000 if growth is None else _percentile_rank(float(growth), growth_values)
        efficiency = float(cell["gmvCents"]) / int(cell["skuCount"]) if int(cell["skuCount"]) else 0.0
        efficiency_percentile = _percentile_rank(efficiency, efficiency_values)
        conversion_percentile = _percentile_rank(float(cell["conversionBps"] or 0), conversion_percentiles)
        openness = max(0, 10_000 - self_share)
        penalty = round(pending_share / 10_000 * 20)
        score = max(
            0,
            min(
                100,
                round(
                    (
                        scale_percentile * 30
                        + growth_percentile * 25
                        + efficiency_percentile * 20
                        + conversion_percentile * 15
                        + openness * 10
                    )
                    / 10_000
                    - penalty
                ),
            ),
        )
        decision_ready = (
            global_decision_ready
            and growth is not None
            and cell["priceBand"] != UNCONFIRMED_PRICE
            and int(cell["pendingPriceCount"]) == 0
            and cell["subcategory"] != "未分类"
        )
        recommendation = (
            "建议进入"
            if decision_ready and score >= 68 and growth >= 0
            else "谨慎回避"
            if decision_ready and score < 35 and growth < 0
            else "持续观察"
        )
        reasons: list[str] = []
        if not identity_ready:
            reasons.append("分析身份未锁定")
        if not coverage_ready:
            reasons.append("月份覆盖不连续或不足 12 个月")
        if pending_price or int(cell["pendingPriceCount"]):
            reasons.append("正式主图价格未完整覆盖")
        if unclassified or cell["subcategory"] == "未分类":
            reasons.append("细分类目未完整确认")
        if share >= 1_500:
            reasons.append("规模占比较高")
        if growth is not None and growth >= 1_000:
            reasons.append("最新月增长较快")
        if growth is not None and growth < 0:
            reasons.append("最新月销售回落")
        if conversion_percentile >= 6_700:
            reasons.append("转化效率位于前列")
        if self_share >= 7_000:
            reasons.append("自营占比较高，平台信用门槛需验证")
        opportunities.append(
            {
                **cell,
                "scenario": _industry_scenario(str(cell["subcategory"])),
                "gmvShareBps": share,
                "growthBps": growth,
                "selfOperatedShareBps": self_share,
                "pendingPriceShareBps": pending_share,
                "score": score,
                "recommendation": recommendation,
                "reasons": reasons[:3],
                "decisionReady": decision_ready,
            }
        )
    opportunities.sort(key=lambda item: (-int(item["score"]), -int(item["gmvCents"])))
    report["opportunities"] = opportunities[:60]

    warnings = ["所有指标仅代表当前 TOP 榜单覆盖，不代表完整行业大盘。"]
    if not identity_ready:
        warnings.append("行业结论需要锁定单一类目、单一榜单范围和单一 SKU/SPU 维度。")
    if len(periods) < 12:
        warnings.append("当前覆盖不足 12 个月，季节性、同比和机会结论仅作观察。")
    elif not continuous:
        warnings.append(f"月份覆盖不连续（缺少：{'、'.join(missing)}），机会结论仅作观察。")
    if not comparison_ready:
        warnings.append("当前筛选缺少最新月的连续上月基期，不能输出进入或回避建议。")
    if year_ago_period not in present:
        warnings.append("当前筛选未覆盖最新月的去年同月基期；如需同比，请向前扩展 12 个月。")
    if pending_price:
        warnings.append(
            f"{len(pending_price)} 个完整商品身份缺少人工确认且绑定当前图片哈希的正式价格，价格带和机会结论只作观察。"
        )
    if unknown_brand:
        warnings.append(f"{len(unknown_brand)} 个完整商品身份品牌未识别。")
    if unclassified:
        warnings.append(f"{len(unclassified)} 个完整商品身份尚未完成细分类目。")
    report["dataQuality"] = {
        "categoryCount": category_count,
        "scopeCount": scope_count,
        "rankingDimensionCount": dimension_count,
        "operationModeCount": len({item["row"].operation_mode for item in rows}),
        "unknownBrandSkuCount": len(unknown_brand),
        "unclassifiedSkuCount": len(unclassified),
        "pendingPriceSkuCount": len(pending_price),
        "identityReady": identity_ready,
        "coverageReady": coverage_ready,
        "comparisonReady": comparison_ready,
        "warnings": warnings,
    }
    return report


def _empty_industry_report(filters: dict[str, object]) -> dict[str, object]:
    return {
        "definition": {
            "title": "京东商用直饮机行业汇报",
            "metricScope": "当前 TOP 榜单覆盖市场",
            "profile": {
                **COMMERCIAL_DIRECT_DRINKING_PROFILE,
            },
            "selectedCategories": filters["categories"],
            "selectedScopes": filters["scopes"],
            "selectedRankingDimensions": filters["rankingDimensions"],
        },
        "period": {
            "coverageMonths": 0,
            "latestPeriod": None,
            "latestGmvCents": 0,
            "monthOverMonthBps": None,
            "yearOverYearBps": None,
            "peak": None,
            "trough": None,
            "latestEntryCount": None,
            "latestExitCount": None,
            "latestExitPeriod": None,
        },
        "lifecycle": [],
        "operationModes": [],
        "brandConcentrationTrend": [],
        "trafficQuadrants": [],
        "productSignals": {"sampleSize": 0, "source": "商品标题", "signals": []},
        "opportunities": [],
        "dataQuality": {
            "categoryCount": 0,
            "scopeCount": 0,
            "rankingDimensionCount": 0,
            "operationModeCount": 0,
            "unknownBrandSkuCount": 0,
            "unclassifiedSkuCount": 0,
            "pendingPriceSkuCount": 0,
            "identityReady": False,
            "coverageReady": False,
            "comparisonReady": False,
            "warnings": ["当前筛选没有可用于行业汇报的 TOP 榜单数据。"],
        },
        "externalDataGaps": [
            {"key": "reviews", "label": "评价、问大家与搜索词", "status": "待补充", "note": "当前只能从商品标题提取卖点，不能替代消费者口碑。"},
            {"key": "service", "label": "安装、质保与滤芯服务履约", "status": "待核验", "note": "标题中的服务承诺尚未与真实履约数据交叉验证。"},
            {"key": "profit", "label": "成本、推广、退货与复购利润", "status": "未纳入", "note": "机会评分仅反映榜单市场信号，不代表最终利润可行性。"},
            {"key": "compliance", "label": "产品合规与场景准入", "status": "待核验", "note": "学校、幼儿园、工厂等场景仍需单独核验适用标准和交付条件。"},
        ],
    }


def overview(
    principal: Principal,
    request: dict[str, object],
    *,
    sales_loader: Callable[[Principal, dict[str, object]], tuple[dict[str, object], str]] | None = None,
) -> dict[str, object]:
    allowed = {"operation", "view", "page", "pageSize", "filters"}
    if set(request) - {"includeFilterOptions"} != allowed or request.get("operation") != "overview":
        raise _error("市场概览请求字段无效")
    include_filters = request.get("includeFilterOptions", True)
    if type(include_filters) is not bool:
        raise _error("includeFilterOptions 必须为布尔值")
    view = request["view"]
    if view not in {"ranking", "full"}:
        raise _error("view 仅支持 ranking 或 full")
    page = _integer(request["page"], 1, 1, MAX_PAGE, "page")
    page_size = _integer(request["pageSize"], 20, 10, 50, "pageSize")
    filters = validate_filters(request["filters"])
    ranking_stats = None
    ranking_bands = None
    ranking_details = {}
    # Load the independent, inexpensive filter aggregates before ranking joins.
    global_options = filter_options() if include_filters and view == "ranking" else None
    if not include_filters:
        global_options = {name: [] for name in ("categories", "scopes", "brands", "rankingDimensions", "operationModes", "subcategories")}
    if view == "ranking":
        ranking_stats, ranking_bands, page_rows = ranking_page(_queryset(filters), filters["priceBands"], page, page_size)
        ranking_details = {item["id"]: item for item in page_rows}
        by_id = MarketRankingEntry.objects.filter(id__in=ranking_details).defer("raw_json").in_bulk()
        rows = [by_id[item["id"]] for item in page_rows]
    else:
        rows = _preferred_rows(filters)
    snapshots = _snapshot_map(rows)
    versions = _price_band_versions()
    effective_gmv, projection_owned = _projection_metrics(rows)
    enriched: list[dict[str, object]] = []
    for row in rows:
        snapshot = snapshots.get(
            (row.category, row.scope, row.sku_code, row.ranking_dimension, row.period_end[:7])
        )
        official = _official_price(snapshot)
        average = official if official is not None else (
            round(effective_gmv.get(row.id, row.gmv_cents) / row.quantity) if row.quantity > 0 else None
        )
        price_band = _price_band(
            official,
            category=row.category,
            period_end=row.period_end,
            versions=versions,
        )
        if filters["priceBands"] and price_band not in filters["priceBands"]:
            continue
        enriched.append(
            {
                "row": row,
                "snapshot": snapshot,
                "official": official,
                "average": average,
                "priceBand": price_band,
                "gmv": effective_gmv.get(row.id, int(row.gmv_cents)),
            }
        )
    enriched.sort(
        key=lambda item: (
            item["row"].rank is None,
            item["row"].rank if item["row"].rank is not None else MAX_SAFE_RANK,
            -int(item["gmv"]),
            item["row"].id,
        )
    )
    sales, sales_revision = _sales_metrics(principal, [item["row"] for item in enriched], filters, sales_loader)
    previous_rank: dict[tuple[str, str, str, str, str], int | None] = {}
    for item in sorted(enriched, key=lambda value: value["row"].period_end):
        row = item["row"]
        key = (row.category, row.scope, row.ranking_dimension, row.sku_code)
        item["previousRank"] = previous_rank.get(key)
        previous_rank[key] = row.rank
        if ranking_stats is not None:
            item["previousRank"] = ranking_details[row.id]["previous_rank"]
    total = int(ranking_stats["total"]) if ranking_stats is not None else len(enriched)
    ranking_source = enriched if ranking_stats is not None else enriched[(page - 1) * page_size : page * page_size]
    image_status = {
        item.source_url: item
        for item in MarketImageCache.objects.filter(
            source_url__in={value["row"].image_url for value in ranking_source if value["row"].image_url}
        )
    }
    items: list[dict[str, object]] = []
    for value in ranking_source:
        row = value["row"]
        snapshot = value["snapshot"]
        cached = image_status.get(row.image_url)
        previous = value.get("previousRank")
        official = value["official"]
        average = value["average"]
        discount = (
            round((1 - int(average) / int(official)) * 10_000)
            if official and average is not None
            else None
        )
        items.append(
            {
                "id": row.id,
                "periodStart": row.period_start,
                "periodEnd": row.period_end,
                "category": row.category,
                "scope": row.scope,
                "rankingDimension": row.ranking_dimension,
                "operationMode": row.operation_mode,
                "subcategory": row.subcategory,
                "rank": row.rank,
                "previousRank": previous,
                "rankChange": previous - row.rank if previous is not None and row.rank is not None else None,
                "skuCode": row.sku_code,
                "productName": row.product_name,
                "brand": row.brand,
                "priceCents": row.price_cents,
                "marketPriceCents": official,
                "candidatePriceCents": snapshot.ai_image_price_cents if snapshot else row.price_cents,
                "marketPriceSource": "manual_confirmed" if official is not None else "missing",
                "candidatePriceSource": (
                    "ai_suggestion"
                    if snapshot and snapshot.ai_image_price_cents is not None
                    else "source_table"
                    if row.price_cents is not None
                    else "missing"
                ),
                "averageTransactionPriceCents": average,
                "discountBps": discount,
                "discountReference": bool(row.price_estimated),
                "gmvCents": int(value["gmv"]),
                "quantity": int(row.quantity),
                "pageViews": int(row.page_views),
                "visitors": int(row.visitors),
                "conversionBps": row.conversion_bps,
                "cartCustomers": int(row.cart_customers),
                "searchClicks": int(row.search_clicks),
                "imageUrl": (
                    f"/api/market/images/{cached.content_sha256}"
                    if cached and cached.status == "ready" and cached.content_sha256
                    else row.image_url
                ),
                "sourceImageUrl": row.image_url,
                "imageCacheStatus": cached.status if cached else "missing" if not row.image_url else "pending",
                "productUrl": row.product_url,
                "periodCount": int(ranking_details[row.id]["period_count"]) if ranking_stats is not None else sum(
                    1
                    for candidate in enriched
                    if candidate["row"].category == row.category
                    and candidate["row"].scope == row.scope
                    and candidate["row"].ranking_dimension == row.ranking_dimension
                    and candidate["row"].sku_code == row.sku_code
                ),
                "isOwn": row.sku_code in projection_owned or bool(sales[row.sku_code]["owned"]),
                "ownSalesCents": int(sales[row.sku_code]["ownSalesCents"]),
                "gmvOutOfBand": False,
            }
        )
    summary_rows = enriched if view == "full" else []
    gmv_total = sum(int(item["gmv"]) for item in summary_rows)
    quantity_total = sum(int(item["row"].quantity) for item in summary_rows)
    product_keys = {
        (
            item["row"].category,
            item["row"].scope,
            item["row"].ranking_dimension,
            item["row"].sku_code,
        )
        for item in (summary_rows if view == "full" else enriched)
    }
    months: dict[str, list[dict[str, object]]] = defaultdict(list)
    brands: dict[str, list[dict[str, object]]] = defaultdict(list)
    subcategories: dict[str, list[dict[str, object]]] = defaultdict(list)
    price_bands: dict[str, list[dict[str, object]]] = defaultdict(list)
    operation_modes: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in summary_rows:
        row = item["row"]
        months[row.period_end[:7]].append(item)
        brands[row.brand or "未识别品牌"].append(item)
        subcategories[row.subcategory or "未分类"].append(item)
        price_bands[str(item["priceBand"])].append(item)
        operation_modes[row.operation_mode or "未知"].append(item)
    trend = []
    for month, values in sorted(months.items())[-60:]:
        month_gmv = sum(int(item["gmv"]) for item in values)
        month_quantity = sum(int(item["row"].quantity) for item in values)
        trend.append(
            {
                "period": month,
                "gmv_cents": month_gmv,
                "quantity": month_quantity,
                "visitors": sum(int(item["row"].visitors) for item in values),
                "product_count": len({_identity(item) for item in values}),
                "brand_count": len({item["row"].brand or "未识别品牌" for item in values}),
                "pop_gmv_cents": sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "POP"),
                "self_gmv_cents": sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "自营"),
                "average_transaction_price_cents": round(month_gmv / month_quantity) if month_quantity else None,
                "weighted_market_price_cents": None,
            }
        )
    price_band_summary = []
    for label, values in sorted(price_bands.items(), key=lambda item: -sum(int(row["gmv"]) for row in item[1])):
        band_gmv = sum(int(item["gmv"]) for item in values)
        self_gmv = sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "自营")
        price_band_summary.append(
            {
                "priceBand": label,
                "gmvCents": band_gmv,
                "quantity": sum(int(item["row"].quantity) for item in values),
                "skuCount": len({_identity(item) for item in values}),
                "popGmvCents": sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "POP"),
                "selfGmvCents": self_gmv,
                "gmvShareBps": round(band_gmv / gmv_total * 10_000) if gmv_total else 0,
                "selfOperatedShareBps": round(self_gmv / band_gmv * 10_000) if band_gmv else None,
                "mainBrands": [name for name, _ in Counter(item["row"].brand for item in values if item["row"].brand).most_common(5)],
            }
        )
    brand_items = []
    for name, values in sorted(brands.items(), key=lambda item: -sum(int(row["gmv"]) for row in item[1])):
        brand_gmv = sum(int(item["gmv"]) for item in values)
        hero = max((int(item["gmv"]) for item in values), default=0)
        brand_items.append(
            {
                "brand": name,
                "gmvCents": brand_gmv,
                "quantity": sum(int(item["row"].quantity) for item in values),
                "skuCount": len({_identity(item) for item in values}),
                "bestRank": min((item["row"].rank for item in values if item["row"].rank is not None), default=None),
                "gmvShareBps": round(brand_gmv / gmv_total * 10_000) if gmv_total else 0,
                "heroSkuGmvCents": hero,
                "heroSkuShareBps": round(hero / brand_gmv * 10_000) if brand_gmv else 0,
                "latestPeriod": max((item["row"].period_end[:7] for item in values), default=None),
                "monthOverMonthBps": None,
                "yearOverYearBps": None,
                "priceBands": list(dict.fromkeys(str(item["priceBand"]) for item in values))[:5],
                "subcategories": list(dict.fromkeys(item["row"].subcategory or "未分类" for item in values))[:5],
            }
        )
    cr = lambda count: round(sum(item["gmvCents"] for item in brand_items[:count]) / gmv_total * 10_000) if gmv_total else 0
    subcategory_summary = []
    for name, values in sorted(subcategories.items(), key=lambda item: -sum(int(row["gmv"]) for row in item[1]))[:60]:
        value_gmv = sum(int(item["gmv"]) for item in values)
        value_quantity = sum(int(item["row"].quantity) for item in values)
        self_gmv = sum(int(item["gmv"]) for item in values if item["row"].operation_mode == "自营")
        subcategory_summary.append(
            {
                "subcategory": name,
                "skuCount": len({_identity(item) for item in values}),
                "gmvCents": value_gmv,
                "gmvShareBps": round(value_gmv / gmv_total * 10_000) if gmv_total else 0,
                "quantity": value_quantity,
                "averageTransactionPriceCents": round(value_gmv / value_quantity) if value_quantity else None,
                "selfOperatedShareBps": round(self_gmv / value_gmv * 10_000) if value_gmv else None,
                "pendingSkuCount": len({_identity(item) for item in values if item["official"] is None}),
                "latestPeriod": max((item["row"].period_end[:7] for item in values), default=None),
                "monthOverMonthBps": None,
                "yearOverYearBps": None,
                "mainBrands": [brand for brand, _ in Counter(item["row"].brand for item in values if item["row"].brand).most_common(5)],
                "mainPriceBands": list(dict.fromkeys(str(item["priceBand"]) for item in values))[:5],
            }
        )
    brand_growth = _monthly_growth_by_value(
        summary_rows,
        lambda item: item["row"].brand or "未识别品牌",
    )
    for item in brand_items:
        item.update(brand_growth.get(str(item["brand"]), {}))
    subcategory_growth = _monthly_growth_by_value(
        summary_rows,
        lambda item: item["row"].subcategory or "未分类",
    )
    for item in subcategory_summary:
        item.update(subcategory_growth.get(str(item["subcategory"]), {}))
    price_band_months: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in summary_rows:
        price_band_months[(item["row"].period_end[:7], str(item["priceBand"]))].append(item)
    price_band_period_totals = {
        period: sum(int(item["gmv"]) for item in values)
        for period, values in months.items()
    }
    price_band_trend = []
    for (period, price_band), values in sorted(price_band_months.items()):
        value_gmv = sum(int(item["gmv"]) for item in values)
        price_band_trend.append(
            {
                "period": period,
                "priceBand": price_band,
                "gmvCents": value_gmv,
                "quantity": sum(int(item["row"].quantity) for item in values),
                "gmvShareBps": (
                    round(value_gmv / price_band_period_totals[period] * 10_000)
                    if price_band_period_totals.get(period)
                    else 0
                ),
            }
        )
    if global_options is None:
        global_options = filter_options()
    image_counts = Counter({row["status"]: row["count"] for row in MarketImageCache.objects.order_by().values("status").annotate(count=Count("pk"))})
    total_images = MarketRankingEntry.objects.exclude(image_url="").values("image_url").distinct().count()
    official_prices = sorted(int(item["official"]) for item in summary_rows if item["official"] is not None)
    weighted_denominator = sum(int(item["gmv"]) for item in summary_rows if item["official"] is not None)
    weighted_price = (
        round(
            sum(int(item["official"]) * int(item["gmv"]) for item in summary_rows if item["official"] is not None)
            / weighted_denominator
        )
        if weighted_denominator
        else None
    )
    industry = _build_industry_report(filters, summary_rows, gmv_total)
    data_range = _queryset(filters).aggregate(start=Min("period_start"), end=Max("period_end"))
    self_gmv = sum(int(item["gmv"]) for item in summary_rows if item["row"].operation_mode == "自营")
    own_count = len(
        {
            _identity(item)
            for item in summary_rows
            if item["row"].sku_code in projection_owned or sales[item["row"].sku_code]["owned"]
        }
    )
    return {
        "view": view,
        "salesRevision": sales_revision,
        "summary": {
            "productCount": int(ranking_stats["product_count"]) if ranking_stats is not None else len(product_keys),
            "categoryCount": int(ranking_stats["category_count"]) if ranking_stats is not None else len({item["row"].category for item in summary_rows}),
            "brandCount": int(ranking_stats["brand_count"]) if ranking_stats is not None else len({item["row"].brand or "未识别品牌" for item in summary_rows}),
            "gmvCents": gmv_total,
            "quantity": quantity_total,
            "pageViews": sum(int(item["row"].page_views) for item in summary_rows),
            "visitors": sum(int(item["row"].visitors) for item in summary_rows),
            "ownProductCount": own_count,
            "activeSkuCount": int(ranking_stats["product_count"]) if ranking_stats is not None else len(product_keys),
            "pendingAiCount": int(ranking_stats["pending_count"]) if ranking_stats is not None else len({_identity(item) for item in summary_rows if item["official"] is None}),
            "selfOperatedGmvCents": self_gmv,
            "selfOperatedShareBps": round(self_gmv / gmv_total * 10_000) if gmv_total else None,
            "medianMarketPriceCents": official_prices[(len(official_prices) - 1) // 2] if official_prices else None,
            "weightedMarketPriceCents": weighted_price,
            "averageTransactionPriceCents": round(gmv_total / quantity_total) if quantity_total else None,
        },
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "pageCount": max(1, math.ceil(total / page_size)),
        },
        "trend": trend,
        "trendTotal": len(months),
        "trendTruncated": len(months) > len(trend),
        "priceBands": ranking_bands if ranking_bands is not None else _option(str(item["priceBand"]) for item in enriched),
        "priceBandSummary": price_band_summary,
        "priceBandTrend": price_band_trend,
        "brandAnalysis": {
            "items": brand_items[:30],
            "cr3Bps": cr(3),
            "cr5Bps": cr(5),
            "concentration": "高" if cr(3) >= 6000 else "中" if cr(3) >= 3500 else "低",
        },
        "subcategorySummary": subcategory_summary,
        "industryReport": industry,
        "filters": {**global_options, "priceBands": ranking_bands if ranking_bands is not None else _option(str(item["priceBand"]) for item in enriched)},
        "dataRange": {"startDate": data_range["start"], "endDate": data_range["end"]},
        "batches": _batch_list(),
        "imageCache": {
            "total": total_images,
            "cached": image_counts["ready"],
            "failed": sum(count for status, count in image_counts.items() if status == "failed"),
            "pending": max(0, total_images - image_counts["ready"] - image_counts["failed"]),
        },
    }


MAX_SAFE_RANK = 2_147_483_647


def item_trend(request: dict[str, object]) -> dict[str, object]:
    allowed = {"operation", "skuCode", "category", "scope", "rankingDimension"}
    if set(request) != allowed or request.get("operation") != "trend":
        raise _error("市场趋势请求字段无效")
    sku = str(request["skuCode"] or "").strip()
    category = str(request["category"] or "").strip()
    scope = str(request["scope"] or "").strip()
    dimension = request["rankingDimension"]
    if not sku or not category or not scope or dimension not in {"SKU", "SPU"}:
        raise _error("市场单品趋势身份无效")
    rows = list(
        MarketRankingEntry.objects.filter(
            sku_code=sku,
            category=category,
            scope=scope,
            ranking_dimension=dimension,
        ).order_by("-period_end", "-period_start", "-id")
    )
    total = len({row.period_end[:7] for row in rows})
    rows = rows[:60]
    snapshots = _snapshot_map(rows)
    items = []
    for row in rows:
        snapshot = snapshots.get((row.category, row.scope, row.sku_code, row.ranking_dimension, row.period_end[:7]))
        official = _official_price(snapshot)
        average = round(row.gmv_cents / row.quantity) if row.quantity else None
        items.append(
            {
                "periodStart": row.period_start,
                "periodEnd": row.period_end,
                "month": row.period_end[:7],
                "category": row.category,
                "scope": row.scope,
                "rankingDimension": row.ranking_dimension,
                "operationMode": row.operation_mode,
                "subcategory": row.subcategory,
                "rank": row.rank,
                "skuCode": row.sku_code,
                "productName": row.product_name,
                "brand": row.brand,
                "gmvCents": int(row.gmv_cents),
                "quantity": int(row.quantity),
                "visitors": int(row.visitors),
                "conversionBps": row.conversion_bps,
                "marketPriceCents": official,
                "candidatePriceCents": snapshot.ai_image_price_cents if snapshot else row.price_cents,
                "averageTransactionPriceCents": official if official is not None else average,
                "sourcePriceCents": snapshot.source_price_cents if snapshot else row.price_cents,
                "aiImagePriceCents": snapshot.ai_image_price_cents if snapshot else None,
                "aiPriceType": snapshot.ai_price_type if snapshot else "",
                "aiConfidenceBps": snapshot.ai_confidence_bps if snapshot else None,
                "confirmedMarketPriceCents": official,
                "priceStatus": "已确认" if official is not None else "暂无价格",
                "candidatePriceStatus": "AI 待确认" if snapshot and snapshot.ai_image_price_cents is not None else "暂无价格",
                "confirmationStatus": snapshot.confirmation_status if snapshot else "missing",
            }
        )
    return {"skuCode": sku, "totalMonths": total, "truncated": total > len(items), "items": items}


def daily_coverage(request: dict[str, object]) -> dict[str, object]:
    allowed = {
        "operation",
        "startDate",
        "endDate",
        "category",
        "scope",
        "rankingDimension",
        "priceBandFilter",
    }
    if set(request) != allowed or request.get("operation") != "daily_coverage":
        raise _error("市场日覆盖请求字段无效")
    start = _date(request["startDate"], "startDate")
    end = _date(request["endDate"], "endDate")
    if not start or not end or start > end:
        raise _error("市场日覆盖日期范围无效")
    category = str(request["category"] or "").strip()
    scope = str(request["scope"] or "").strip()
    price_band_filter = str(request["priceBandFilter"] or "").strip()
    dimension = request["rankingDimension"]
    if not category or not scope or not price_band_filter or dimension not in {"SKU", "SPU"}:
        raise _error("市场日覆盖业务身份无效")
    rows = (
        MarketRankingEntry.objects.filter(
            category=category,
            scope=scope,
            ranking_dimension=dimension,
            price_band_filter=price_band_filter,
            period_start=F("period_end"),
            period_end__gte=start,
            period_end__lte=end,
        )
        .values("period_end")
        .annotate(row_count=Count("id"))
        .order_by("period_end")
    )
    row_counts = {str(row["period_end"]): int(row["row_count"]) for row in rows}
    cursor = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    expected_dates = []
    while cursor <= stop:
        expected_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
        if len(expected_dates) > 4_000:
            raise _error("市场日覆盖范围不能超过 4000 天")
    present_dates = [value for value in expected_dates if row_counts.get(value, 0) > 0]
    missing_dates = [value for value in expected_dates if value not in row_counts]
    return {
        "ok": True,
        "identity": {
            "category": category,
            "scope": scope,
            "rankingDimension": dimension,
            "priceBandFilter": price_band_filter,
        },
        "startDate": start,
        "endDate": end,
        "cutoffDate": present_dates[-1] if present_dates else None,
        "presentDates": present_dates,
        "missingDates": missing_dates,
        "rowCounts": row_counts,
    }
