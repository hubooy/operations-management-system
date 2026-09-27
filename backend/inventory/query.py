from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from sales.auth import Principal
from sales.consumers import execute_consumer_query as execute_sales_consumer
from sales.consumers import validate_consumer_request as validate_sales_consumer
from sales.models import ErpProductMaster, SalesDataRevision

from .errors import InventoryApiError
from .models import (
    InventoryAgeLine,
    InventoryImportBatch,
    InventoryOperatingSettings,
    InventoryStockLine,
    ReplenishmentPlanItem,
)
from .plans import plan_summary, query_plans
from .warehouse_mapping import classify_warehouse
from .warehouse_mapping_service import effective_mapping


HEALTH_STATUSES = ("no_stock", "urgent", "warning", "stale", "slow", "healthy")
HEALTH_WAREHOUSE_CATEGORIES = frozenset({"jd", "cainiao", "guangdong", "selfOperated"})
LOW_TURNOVER_DAYS = 180
AGE_STATUSES = ("healthy", "aged", "slow", "stagnant", "no_stock")
AGE_BUCKETS = (
    ("0-7", "0–7 天", 0, 7),
    ("8-15", "8–15 天", 8, 15),
    ("16-30", "16–30 天", 16, 30),
    ("31-60", "31–60 天", 31, 60),
    ("61-90", "61–90 天", 61, 90),
    ("91-120", "91–120 天", 91, 120),
    ("121-150", "121–150 天", 121, 150),
    ("151-180", "151–180 天", 151, 180),
    ("181-360", "181–360 天", 181, 360),
    ("361+", "1 年以上", 361, None),
)
MIN_SALES_MATCH_RATE = 0.6
MAX_OVERVIEW_PRODUCTS = 20_000
MAX_INBOUND_PRODUCTS = 5_000
WAREHOUSE_GROUPS = (
    "jd",
    "dropship",
    "afterSales",
    "guangdong",
    "sample",
    "cainiao",
    "overseas",
    "virtual",
    "exception",
    "selfOperated",
)


def _latest_batch(dataset: str) -> InventoryImportBatch | None:
    owned_batch_ids = (
        InventoryStockLine.objects.values("batch_id")
        if dataset == "stock"
        else InventoryAgeLine.objects.values("batch_id")
    )
    return (
        InventoryImportBatch.objects.filter(
            dataset=dataset,
            status="completed",
            id__in=owned_batch_ids,
        )
        .order_by("-snapshot_date", "-completed_at", "-id")
        .first()
    )


def _sales_revision() -> str:
    values = dict(
        SalesDataRevision.objects.filter(domain__in=["sales", "erp"]).values_list(
            "domain", "revision"
        )
    )
    return f"sales:{int(values.get('sales', 0))}/erp:{int(values.get('erp', 0))}"


def _sales_query(principal: Principal, payload: dict[str, object]) -> dict[str, object]:
    try:
        return execute_sales_consumer(principal, validate_sales_consumer(payload))
    except Exception as error:
        raise InventoryApiError(
            "Django 销售读取服务返回的数据不完整，请稍后重试",
            code="service_unavailable",
            status=503,
        ) from error


def _pagination(options: dict[str, object]) -> tuple[int, int, int]:
    page = options.get("page", 1)
    page_size = options.get("pageSize", 50)
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 10_000:
        raise InventoryApiError("page 必须是 1 到 10000 的整数")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise InventoryApiError("pageSize 必须是 1 到 100 的整数")
    return page, page_size, (page - 1) * page_size


def _date_option(value: object, label: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryApiError(f"{label} 日期格式无效，请使用 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InventoryApiError(f"{label} 日期格式无效，请使用 YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise InventoryApiError(f"{label} 日期格式无效，请使用 YYYY-MM-DD")
    return parsed


def _sales_period(_options: dict[str, object], freshness: dict[str, object]) -> tuple[date | None, date | None, int]:
    data_start = _date_option(freshness.get("dataStartDate"), "销售起始")
    data_end = _date_option(freshness.get("dataCutoffDate"), "销售截止")
    if data_end is None:
        return None, None, 30
    requested_start = data_end - timedelta(days=29)
    if data_start is None:
        return None, None, 30
    start = max(data_start, requested_start)
    end = data_end
    if start > end:
        return None, None, 30
    return start, end, (end - start).days + 1


def _warehouse_key(value: str) -> str:
    normalized = value.strip().lower()
    for token in ("配送中心", "仓库", "库房", "仓", " ", "（", "）", "(", ")", "-"):
        normalized = normalized.replace(token, "")
    return normalized


def _is_jd_warehouse(
    warehouse: str,
    warehouse_type: str,
    mapping: dict[str, dict[str, object]] | None = None,
) -> bool:
    return classify_warehouse(warehouse, stored_type=warehouse_type, mapping=mapping).warehouse_type == "jd_rdc"


def _warehouse_type(warehouse: str, stored: str) -> str:
    return classify_warehouse(warehouse, stored_type=stored).warehouse_type


def _warehouse_group(warehouse: str, warehouse_type: str, warehouse_category: str = "") -> str:
    if warehouse_category in WAREHOUSE_GROUPS:
        return warehouse_category
    category = classify_warehouse(
        warehouse,
        stored_type=warehouse_type,
        stored_category=warehouse_category,
    ).category
    return category if category in WAREHOUSE_GROUPS else "selfOperated"


def _selected(options: dict[str, object], key: str, maximum: int) -> list[str]:
    value = options.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise InventoryApiError(f"{key} 筛选无效")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 120:
            raise InventoryApiError(f"{key} 筛选无效")
        if item.strip() not in result:
            result.append(item.strip())
    return result


def _matches_text(item: dict[str, object], query: object, fields: tuple[str, ...]) -> bool:
    if query is None or query == "":
        return True
    if not isinstance(query, str) or len(query) > 100:
        raise InventoryApiError("搜索词不能超过 100 个字符")
    words = [word.lower() for word in re.split(r"[\s,，;；]+", query.strip()) if word][:8]
    haystacks = [str(item.get(field, "")).lower() for field in fields]
    return any(any(word in haystack for haystack in haystacks) for word in words)


def _settings() -> tuple[InventoryOperatingSettings, dict[str, object]]:
    row = InventoryOperatingSettings.objects.get(id=1)
    return row, {
        "targetDays": int(row.target_days),
        "criticalDays": int(row.critical_days),
        "replenishDays": int(row.target_days),
        "slowDays": int(row.slow_days),
        "stagnantDays": int(row.stagnant_days),
        "autoReplenishment": bool(row.auto_replenishment),
        "inventoryAlert": bool(row.inventory_alert),
    }


def _sales_demand(
    principal: Principal,
    product_codes: list[str],
    start: date | None,
    end: date | None,
    expected_revision: str,
) -> dict[tuple[str, str], dict[str, object]]:
    if len(product_codes) > MAX_OVERVIEW_PRODUCTS:
        raise InventoryApiError("库存货品数量超过销售消费查询上限", code="service_unavailable", status=503)
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for offset in range(0, len(product_codes), 500):
        requested = product_codes[offset : offset + 500]
        before = _sales_revision()
        result = _sales_query(
            principal,
            {
                "operation": "inventory_demand",
                "startDate": start.isoformat() if start else None,
                "endDate": (end + timedelta(days=1)).isoformat() if end else None,
                "productCodes": requested,
                "limit": 10_000,
            },
        )
        after = _sales_revision()
        if before != expected_revision or after != expected_revision or result.get("truncated") is not False:
            raise InventoryApiError("销售版本在库存计算期间发生变化", code="service_unavailable", status=503)
        allowed = set(requested)
        result_rows = result.get("rows")
        if not isinstance(result_rows, list):
            raise InventoryApiError("销售需求响应无效", code="service_unavailable", status=503)
        for row in result_rows:
            if not isinstance(row, dict) or row.get("productCode") not in allowed:
                raise InventoryApiError("销售需求响应越出请求范围", code="service_unavailable", status=503)
            warehouse_key = row.get("warehouseKey")
            if not isinstance(warehouse_key, str) or warehouse_key != _warehouse_key(warehouse_key):
                raise InventoryApiError("销售仓库键不符合规范", code="service_unavailable", status=503)
            key = (str(row["productCode"]), warehouse_key)
            if key in rows:
                raise InventoryApiError("销售需求响应包含重复业务键", code="service_unavailable", status=503)
            rows[key] = row
    return rows


def _health(
    available: int,
    daily_sales: float | None,
    coverage_days: float | None,
    age_days: int | None,
    settings: dict[str, object],
    window_days: int,
) -> tuple[str, str, str]:
    if available <= 0:
        return "no_stock", "无库存可用", "当前可用库存小于等于 0"
    if daily_sales is None:
        return "stale", "积压风险", f"所选 {window_days} 日周期未匹配到同货品、同仓库的销售明细，暂无法核算库存周转"
    if daily_sales <= 0:
        age_note = f"，库龄 {age_days} 天" if age_days is not None else ""
        return "stale", "积压风险", f"所选 {window_days} 日周期无有效销量{age_note}"
    if (coverage_days or math.inf) <= int(settings["criticalDays"]):
        return "urgent", "紧急补货", f"预计可售不超过 {settings['criticalDays']} 天"
    if (coverage_days or math.inf) < int(settings["replenishDays"]):
        return "warning", "补货预警", f"预计可售低于 {settings['replenishDays']} 天"
    if (coverage_days or 0) > LOW_TURNOVER_DAYS:
        return "slow", "低周转", f"库存周转大于 {LOW_TURNOVER_DAYS} 天"
    return "healthy", "库存健康", "库存覆盖处于目标区间"


def _quality(items: list[dict[str, object]], inventory_stale: bool, auto_replenishment: bool) -> dict[str, object]:
    total = len(items)
    matched = sum(item["sales30d"] is not None for item in items)
    match_rate = matched / total if total else 0
    issues: list[dict[str, object]] = []
    if total and match_rate < MIN_SALES_MATCH_RATE:
        issues.append(
            {
                "code": "LOW_SALES_MAPPING_COVERAGE",
                "severity": "blocking",
                "affectedCount": total - matched,
                "message": f"只有 {match_rate * 100:.1f}% 的库存行匹配到同货品、同仓库销量，低于 60% 门槛",
            }
        )
    if inventory_stale:
        issues.append(
            {
                "code": "STALE_INVENTORY_SNAPSHOT",
                "severity": "warning",
                "affectedCount": total,
                "message": "库存快照已超过 3 天，请先同步最新库存",
            }
        )
    if total and not auto_replenishment:
        issues.append(
            {
                "code": "AUTO_REPLENISHMENT_DISABLED",
                "severity": "blocking",
                "affectedCount": 0,
                "message": "系统设置已关闭自动补货建议",
            }
        )
    available = sum(int(item["availableQuantity"]) for item in items)
    known_value = sum(int(item["knownStockValueCents"]) for item in items)
    if total and available / total > 1_000_000:
        issues.append(
            {
                "code": "IMPLAUSIBLE_AVERAGE_QUANTITY",
                "severity": "blocking",
                "affectedCount": total,
                "message": "平均每个仓库货品的可用库存超过 100 万，需复核数量单位或汇总行",
            }
        )
    if total and known_value / total > 5_000_000_000:
        issues.append(
            {
                "code": "IMPLAUSIBLE_AVERAGE_STOCK_VALUE",
                "severity": "blocking",
                "affectedCount": total,
                "message": "平均每个仓库货品的库存货值超过 5,000 万元，需复核数量与成本单位",
            }
        )
    blocked = any(issue["severity"] == "blocking" for issue in issues)
    return {
        "status": "blocked" if blocked else "degraded" if issues else "reliable",
        "salesMatchThreshold": MIN_SALES_MATCH_RATE,
        "salesDemandMatchRate": match_rate,
        "recommendationsSuppressed": blocked,
        "issues": issues,
    }


def _overview_items(principal: Principal, options: dict[str, object]) -> tuple[
    InventoryImportBatch | None,
    list[dict[str, object]],
    dict[str, object],
    date | None,
    date | None,
    str,
]:
    latest = _latest_batch("stock")
    settings_row, settings = _settings()
    warehouse_mapping = effective_mapping(settings_row)
    revision = _sales_revision()
    freshness = _sales_query(principal, {"operation": "freshness"})
    if _sales_revision() != revision:
        raise InventoryApiError("销售版本在库存计算期间发生变化", code="service_unavailable", status=503)
    sales_start, sales_end, window_days = _sales_period(options, freshness)
    settings["salesWindowDays"] = 30
    settings["salesCoverageDays"] = window_days
    if latest is None:
        return None, [], settings, sales_start, sales_end, revision
    stock_rows = list(InventoryStockLine.objects.filter(batch_id=latest.id).order_by("product_code", "warehouse", "id"))
    product_codes = sorted({row.product_code for row in stock_rows if row.product_code})
    demand = _sales_demand(principal, product_codes, sales_start, sales_end, revision) if product_codes else {}
    product_sales: dict[str, int] = defaultdict(int)
    products_with_sales: set[str] = set()
    for (product_code, _warehouse), sales_row in demand.items():
        product_sales[product_code] += int(sales_row["salesQuantity"])
        products_with_sales.add(product_code)
    erp = {
        row.product_code: row
        for row in ErpProductMaster.objects.filter(product_code__in=product_codes)
    }
    from .guangdong import overview_risks
    guangdong = overview_risks(principal, sorted({row.product_code for row in stock_rows if row.warehouse == "广东仓"}))
    plans = defaultdict(lambda: {"quantity": 0, "draft": False})
    for plan in ReplenishmentPlanItem.objects.filter(
        Q(status__in=["draft", "confirmed"])
        | Q(status="completed", source_batch_id=latest.id)
    ):
        key = (plan.product_code, plan.warehouse)
        plans[key]["quantity"] += int(plan.planned_quantity)
        plans[key]["draft"] = plans[key]["draft"] or plan.status == "draft"
    items: list[dict[str, object]] = []
    for row in stock_rows:
        if row.warehouse.strip() == "刷刷仓":
            continue
        sales = demand.get((row.product_code, _warehouse_key(row.warehouse)))
        sales_quantity = int(sales["salesQuantity"]) if sales is not None else None
        product_sales_quantity = product_sales[row.product_code] if row.product_code in products_with_sales else None
        absolute_quantity = int(sales.get("absoluteQuantity", 0)) if sales else 0
        absolute_cost = int(sales.get("absoluteCostCents", 0)) if sales else 0
        fallback_cost = absolute_cost / absolute_quantity if absolute_quantity > 0 else 0
        available = int(row.available_quantity)
        priced_quantity = max(available, 0)
        imported_value = max(available, 0) * int(row.unit_cost_cents)
        missing_quantity = max(0, max(available, 0) - priced_quantity)
        fallback_quantity = missing_quantity if fallback_cost > 0 else 0
        covered = min(max(available, 0), priced_quantity + fallback_quantity)
        known_value = round(imported_value + fallback_quantity * fallback_cost)
        complete_value = known_value if covered >= max(available, 0) else None
        unit_cost = round(known_value / covered) if covered > 0 else 0
        daily_sales = sales_quantity / 30 if sales_quantity is not None else None
        coverage_days = max(available, 0) / daily_sales if daily_sales and daily_sales > 0 else None
        status, label, reason = _health(
            available,
            daily_sales,
            coverage_days,
            row.inventory_age_days,
            settings,
            30,
        )
        gd = guangdong.get(row.product_code) if row.warehouse == "广东仓" else None
        if gd:
            status = {"no_stock": "no_stock", "urgent": "urgent", "warning": "warning",
                      "healthy": "healthy", "stale": "slow", "unknown": "stale"}[gd["risk"]]
            label, reason = gd["riskLabel"], gd["riskReason"] or "库存覆盖处于广东仓目标区间"
            coverage_days = gd["turnoverDays"]
        plan = plans[(row.product_code, row.warehouse)]
        suggested = (
            max(
                0,
                math.ceil(
                    (sales_quantity or 0) * int(settings["targetDays"]) / 30
                    - available
                    - int(row.in_transit_quantity)
                    - int(plan["quantity"])
                ),
            )
            if sales_quantity is not None and sales_quantity > 0
            else None
        )
        master = erp.get(row.product_code)
        supplier = row.supplier.strip() or (master.supplier.strip() if master and master.supplier.strip() else "未映射供应商")
        supplier_source = "jikexyun_inventory" if row.supplier.strip() else "erp_fallback" if master and master.supplier.strip() else "missing"
        warehouse_classification = classify_warehouse(
            row.warehouse,
            stored_type=row.warehouse_type,
            stored_category=row.warehouse_category,
            stored_include_in_inventory=bool(row.include_in_inventory),
            mapping=warehouse_mapping,
        )
        product_name = row.product_name or (str(sales.get("productName")) if sales else "") or (master.product_name if master else "") or row.product_code
        items.append(
            {
                "key": f"{row.warehouse}\x1f{row.product_code}",
                "productCode": row.product_code,
                "productName": product_name,
                "brand": row.brand or (master.brand if master else ""),
                "specification": row.specification or (master.specification if master else ""),
                "category": row.category or (master.category if master else "") or "未分类",
                "supplier": supplier,
                "supplierSource": supplier_source,
                "warehouse": row.warehouse,
                "warehouseType": warehouse_classification.warehouse_type,
                "warehouseCategory": warehouse_classification.category,
                "includedInInventory": warehouse_classification.include_in_inventory,
                "onHandQuantity": int(row.on_hand_quantity),
                "availableQuantity": available,
                "lockedQuantity": int(row.locked_quantity),
                "sourceInTransitQuantity": int(row.in_transit_quantity),
                "plannedInTransitQuantity": int(plan["quantity"]),
                "totalInTransitQuantity": int(row.in_transit_quantity) + int(plan["quantity"]),
                "unitCostCents": unit_cost,
                "inventoryAgeDays": row.inventory_age_days,
                "stockValueCents": complete_value,
                "knownStockValueCents": known_value,
                "costCoverageRate": covered / max(available, 0) if available > 0 else 1,
                "sales30d": sales_quantity,
                "productSales30d": product_sales_quantity,
                "averageDailySales": daily_sales,
                "coverageDays": coverage_days,
                "suggestedQuantity": suggested,
                "status": status,
                "statusLabel": label,
                "reason": reason,
                "inDraftPlan": bool(plan["draft"]),
            }
        )
    if _sales_revision() != revision:
        raise InventoryApiError("销售版本在库存计算期间发生变化", code="service_unavailable", status=503)
    return latest, items, settings, sales_start, sales_end, revision


def _filtered_overview(
    items: list[dict[str, object]],
    options: dict[str, object],
    *,
    included_only: bool = True,
) -> list[dict[str, object]]:
    warehouses = set(_selected(options, "warehouses", 10))
    brands = set(_selected(options, "brands", 20))
    categories = set(_selected(options, "categories", 20))
    warehouse_types = set(_selected(options, "warehouseTypes", 3))
    statuses = set(_selected(options, "statuses", 6))
    exact_key = options.get("exactKey")
    if exact_key is not None and (not isinstance(exact_key, str) or len(exact_key) > 240):
        raise InventoryApiError("库存精确业务键无效")
    return [
        item
        for item in items
        if (not included_only or bool(item.get("includedInInventory", True)))
        and _matches_text(item, options.get("query"), ("productCode", "productName", "brand", "specification", "category", "supplier", "warehouse"))
        and (not warehouses or item["warehouse"] in warehouses)
        and (not brands or item["brand"] in brands)
        and (not categories or item["category"] in categories)
        and (not warehouse_types or item["warehouseType"] in warehouse_types)
        and (not statuses or item["status"] in statuses)
        and (exact_key is None or item["key"] == exact_key)
    ]


def _health_distribution_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Limit health indicators to the four trusted fulfillment warehouse groups."""
    return [
        item for item in items
        if item.get("warehouseCategory") in HEALTH_WAREHOUSE_CATEGORIES
    ]


def replenishment_plan_sources(
    principal: Principal,
) -> dict[str, object]:
    """Return the current authoritative plan inputs in one consistent, bounded read."""
    latest, all_items, settings, _sales_start, _sales_end, _revision = _overview_items(principal, {})
    stale = bool(latest and (timezone.localdate() - latest.snapshot_date).days > 3)
    included = [item for item in all_items if bool(item.get("includedInInventory", True))]
    return {
        "latestBatchId": latest.id if latest else None,
        "inventoryAsOf": latest.snapshot_date.isoformat() if latest else None,
        "inventoryStale": stale,
        "quality": _quality(included, stale, bool(settings["autoReplenishment"])),
        "itemsByKey": {str(item["key"]): item for item in included},
    }


def _metrics(
    items: list[dict[str, object]],
    quality: dict[str, object],
    alerts: bool,
    *,
    health_items: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, int]]:
    positive = sum(max(0, int(item["availableQuantity"])) for item in items)
    covered = sum(round(max(0, int(item["availableQuantity"])) * float(item["costCoverageRate"])) for item in items)
    known_value = sum(int(item["knownStockValueCents"]) for item in items)
    total_daily = sum(float(item["averageDailySales"] or 0) for item in items)
    demand_available = sum(max(0, int(item["availableQuantity"])) for item in items if float(item["averageDailySales"] or 0) > 0)
    scoped_health_items = items if health_items is None else health_items
    health = {status: sum(item["status"] == status for item in scoped_health_items) for status in HEALTH_STATUSES}
    suppressed = bool(quality["recommendationsSuppressed"])
    metrics = {
        "skuWarehouseCount": len(items),
        "totalAvailableQuantity": sum(int(item["availableQuantity"]) for item in items),
        "totalStockValueCents": known_value,
        "knownStockValueCents": known_value,
        "stockValueComplete": positive <= 0 or covered >= positive,
        "costCoverageRate": covered / positive if positive > 0 else 1,
        "salesDemandMatchRate": sum(item["sales30d"] is not None for item in items) / len(items) if items else 0,
        "averageCoverageDays": None if suppressed or total_daily <= 0 else demand_available / total_daily,
        "urgentCount": health["no_stock"] + health["urgent"],
        "replenishCount": health["warning"],
        "slowMovingValueCents": sum(int(item["knownStockValueCents"]) for item in scoped_health_items if item["status"] in {"stale", "slow"}),
        "noSalesCount": sum(item["sales30d"] is None or int(item["sales30d"]) <= 0 for item in scoped_health_items),
        "recommendationCount": 0 if suppressed else sum(int(item["suggestedQuantity"] or 0) > 0 for item in items),
        "inventoryAlertsEnabled": alerts,
        "recommendationsSuppressed": suppressed,
        "qualityIssues": quality["issues"],
    }
    return metrics, {
        "noStock": health["no_stock"],
        "urgent": health["urgent"],
        "warning": health["warning"],
        "stale": health["stale"],
        "slow": health["slow"],
        "healthy": health["healthy"],
    }


def _warehouse_metrics(items: list[dict[str, object]], window_days: int) -> dict[str, object]:
    inventory = sum(int(item["availableQuantity"]) for item in items)
    in_transit = sum(int(item["totalInTransitQuantity"]) for item in items)
    matched_sales = [int(item["sales30d"]) for item in items if item["sales30d"] is not None]
    sales = sum(matched_sales) if matched_sales else None
    turnover = (
        max(0, inventory) / (sales / window_days)
        if sales is not None and sales > 0 and window_days > 0
        else None
    )
    return {
        "inventoryQuantity": inventory,
        "salesQuantity": sales,
        "turnoverDays": turnover,
        "inTransitQuantity": in_transit,
    }


def _mapping_samples(
    items: list[dict[str, object]],
    window_days: int,
    suppressed: bool,
) -> list[dict[str, object]]:
    by_product: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        by_product[str(item["productCode"])].append(item)
    samples: list[dict[str, object]] = []
    for product_code, product_items in by_product.items():
        ordered = sorted(product_items, key=lambda item: (str(item["warehouse"]), str(item["key"])))
        included = [item for item in ordered if bool(item.get("includedInInventory", True))]
        first = next((item for item in ordered if item["supplier"] != "未映射供应商"), ordered[0])
        grouped = {
            key: _warehouse_metrics(
                [
                    item
                    for item in ordered
                    if _warehouse_group(
                        str(item["warehouse"]),
                        str(item["warehouseType"]),
                        str(item.get("warehouseCategory", "")),
                    ) == key
                ],
                window_days,
            )
            for key in WAREHOUSE_GROUPS
        }
        total_inventory = sum(int(item["availableQuantity"]) for item in included)
        total_in_transit = sum(int(item["totalInTransitQuantity"]) for item in included)
        product_sales = first.get("productSales30d")
        total_turnover = (
            max(0, total_inventory) / (int(product_sales) / 30)
            if product_sales is not None and int(product_sales) > 0
            else None
        )
        suggested_values = [int(item["suggestedQuantity"]) for item in included if item["suggestedQuantity"] is not None]
        alert_item = min(included or ordered, key=lambda item: HEALTH_STATUSES.index(str(item["status"])))
        samples.append(
            {
                "key": product_code,
                "productCode": product_code,
                "productName": first["productName"],
                "brand": first["brand"],
                "category": first["category"],
                "supplier": first["supplier"],
                "warehouses": grouped,
                "warehouseOptions": [
                    {
                        "key": item["key"],
                        "warehouse": item["warehouse"],
                        "availableQuantity": item["availableQuantity"],
                        "salesQuantity": item["sales30d"],
                        "coverageDays": item["coverageDays"],
                        "suggestedQuantity": item["suggestedQuantity"],
                        "inDraftPlan": item["inDraftPlan"],
                    }
                    for item in included
                ],
                "totalInventoryQuantity": total_inventory,
                "totalStockValueCents": sum(int(item["knownStockValueCents"]) for item in included),
                "totalInTransitQuantity": total_in_transit,
                "totalSalesQuantity": product_sales,
                "totalTurnoverDays": total_turnover,
                "suggestedQuantity": None if suppressed or not suggested_values else sum(suggested_values),
                "alertStatus": alert_item["status"],
                "alertLabel": alert_item["statusLabel"],
                "alertReason": alert_item["reason"],
                "unmatchedWarehouseCount": sum(item["sales30d"] is None for item in ordered),
            }
        )
    samples.sort(
        key=lambda item: (
            -(int(item["totalSalesQuantity"]) if item["totalSalesQuantity"] is not None else -1),
            -max(0, int(item["totalInventoryQuantity"])),
            str(item["productCode"]),
        )
    )
    return samples[:50]


def inventory_overview(principal: Principal, options: dict[str, object]) -> dict[str, object]:
    page, page_size, offset = _pagination(options)
    latest, all_items, settings, sales_start, sales_end, _revision = _overview_items(principal, options)
    stale = bool(latest and (timezone.localdate() - latest.snapshot_date).days > 3)
    included_items = [item for item in all_items if bool(item.get("includedInInventory", True))]
    quality = _quality(included_items, stale, bool(settings["autoReplenishment"]))
    filtered = _filtered_overview(all_items, options)
    workbench_filtered = _filtered_overview(all_items, options, included_only=False)
    filtered.sort(
        key=lambda item: (
            HEALTH_STATUSES.index(str(item["status"])),
            float(item["coverageDays"]) if item["coverageDays"] is not None else math.inf,
            str(item["productCode"]),
            str(item["warehouse"]),
        )
    )
    health_filtered = _health_distribution_items(filtered)
    metrics, health = _metrics(
        filtered,
        quality,
        bool(settings["inventoryAlert"]),
        health_items=health_filtered,
    )
    suppressed = bool(quality["recommendationsSuppressed"])
    if suppressed:
        for item in filtered:
            if item["suggestedQuantity"] is not None:
                item["suggestedQuantity"] = None
                item["reason"] = "库存数据质量门禁未通过，已暂停输出精确补货量；请先修复仓库/货品映射或数据单位"
    recommendations = [] if suppressed else sorted(
        [dict(item) for item in filtered if int(item["suggestedQuantity"] or 0) > 0],
        key=lambda item: (-int(item["suggestedQuantity"] or 0), str(item["productCode"]), str(item["warehouse"])),
    )[:50]
    facets = {
        "warehouses": sorted({str(item["warehouse"]) for item in included_items}),
        "brands": sorted({str(item["brand"]) for item in included_items if item["brand"]}),
        "categories": sorted({str(item["category"]) for item in included_items if item["category"]}),
        "statuses": list(HEALTH_STATUSES),
    }
    mapping_samples = _mapping_samples(workbench_filtered, 30, suppressed)
    sync = {
        "latestInventoryBatchId": latest.id if latest else None,
        "inventoryAsOf": latest.snapshot_date.isoformat() if latest else None,
        "inventorySyncedAt": latest.completed_at.isoformat() if latest and latest.completed_at else None,
        "salesThrough": sales_end.isoformat() if sales_end else None,
        "salesWindowStart": sales_start.isoformat() if sales_start else None,
        "latestInventoryFile": latest.file_name if latest else None,
        "inventoryStale": stale,
    }
    has_jd = any(item["warehouseType"] == "jd_rdc" for item in included_items)
    source_status = "stale" if stale else "ready"
    sources = [
        {"key": "warehouse_stock", "label": "吉客云分仓库存", "status": source_status if latest else "missing", "asOfDate": sync["inventoryAsOf"]},
        {"key": "sales_demand", "label": "近 30 天正向销量", "status": "ready" if sales_end else "missing", "asOfDate": sync["salesThrough"]},
        {"key": "jd_rdc", "label": "京东 RDC / DC", "status": source_status if has_jd else "missing", "asOfDate": sync["inventoryAsOf"] if has_jd else None},
    ]
    response: dict[str, object] = {
        "projection": "overview",
        "hasInventory": latest is not None,
        "sync": sync,
        "metrics": metrics,
        "health": health,
        "controls": {
            "autoReplenishmentEnabled": bool(settings["autoReplenishment"]),
            "alertsEnabled": bool(settings["inventoryAlert"]),
        },
        "quality": quality,
        "settings": settings,
        "sources": sources,
        "filters": facets,
        "mapping": {
            "matchedCount": sum(item["sales30d"] is not None for item in filtered),
            "unmatchedCount": sum(item["sales30d"] is None for item in filtered),
            "samples": mapping_samples,
        },
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "limit": page_size,
            "total": len(filtered),
            "returned": len(filtered[offset : offset + page_size]),
            "totalPages": (len(filtered) + page_size - 1) // page_size,
            "truncated": offset + len(filtered[offset : offset + page_size]) < len(filtered),
        },
        "recommendations": recommendations,
        "items": filtered[offset : offset + page_size],
        "plans": [],
        "plansPagination": {"page": 1, "pageSize": 50, "total": 0, "returned": 0, "totalPages": 0, "truncated": False},
        "planSummary": {"draftCount": 0, "confirmedCount": 0, "completedCount": 0, "cancelledCount": 0, "activeQuantity": 0},
    }
    view = options.get("view", "full")
    if view == "dashboard":
        return {key: response[key] for key in ("hasInventory", "sync", "metrics", "health")}
    if view == "plan":
        plan_page = query_plans(
            {
                "page": options.get("planPage", 1),
                "pageSize": options.get("planPageSize", 50),
                "status": options.get("planStatus"),
                "includeCancelled": options.get("includeCancelledPlans", False),
                "query": options.get("query"),
                "warehouses": options.get("warehouses", []),
                "brands": options.get("brands", []),
                "categories": options.get("categories", []),
            }
        )
        response.update(
            {
                "projection": "plan",
                "items": [],
                "recommendations": [],
                "pagination": {"page": 1, "pageSize": 50, "limit": 50, "total": 0, "returned": 0, "totalPages": 0, "truncated": False},
                "plans": plan_page["items"],
                "plansPagination": plan_page["pagination"],
                "planSummary": plan_summary(latest.id if latest else None),
            }
        )
        return response
    if view == "full":
        plan_page = query_plans(
            {
                "page": options.get("planPage", 1),
                "pageSize": options.get("planPageSize", 50),
                "status": options.get("planStatus"),
                "includeCancelled": options.get("includeCancelledPlans", False),
                "query": options.get("query"),
                "warehouses": options.get("warehouses", []),
                "brands": options.get("brands", []),
                "categories": options.get("categories", []),
            }
        )
        response.pop("projection", None)
        response["plans"] = plan_page["items"]
        response["plansPagination"] = plan_page["pagination"]
        response["planSummary"] = plan_summary(latest.id if latest else None)
    return response


def _age_classification(
    available: int, age_days: int | None, sales_30d: int | None
) -> tuple[str, str, str]:
    if available <= 0:
        return "no_stock", "无可用库存", "无需纳入滞销清理，等待下一次库存快照确认。"
    if age_days is None:
        return "healthy", "待补库龄", "当前报表未提供库龄，暂不参与库龄预警。"
    if sales_30d is not None and age_days >= 90 and sales_30d <= 0:
        return "stagnant", "滞销清理", "停止补货，优先评估促销、渠道调拨或清退。"
    if sales_30d is not None and age_days >= 60 and sales_30d <= 3:
        return "slow", "低动销", "控制补货，结合价格和渠道方案提升动销。"
    if age_days >= 90:
        return "aged", "高库龄", "库龄超过 90 天，建议核查动销并制定处理计划。"
    return "healthy", "库龄健康", "持续观察库存周转与近 30 日销量。"


def _age_bucket(age_days: int | None) -> tuple[str | None, str]:
    if age_days is None:
        return None, "未提供库龄"
    for key, label, minimum, maximum in AGE_BUCKETS:
        if age_days >= minimum and (maximum is None or age_days <= maximum):
            return key, label
    return None, "未提供库龄"


def inventory_age_analysis(options: dict[str, object]) -> dict[str, object]:
    page, page_size, offset = _pagination(options)
    age_batch = _latest_batch("age")
    stock_batch = _latest_batch("stock")
    source_batch = age_batch or stock_batch
    if source_batch is None:
        return {
            "hasInventory": False,
            "sync": {"inventoryAsOf": None, "latestInventoryBatchId": None, "hasAgeSales": False},
            "metrics": {"skuWarehouseCount": 0, "stockValueComplete": True, "aged90Count": 0, "aged90ValueCents": 0, "stagnantCount": 0, "stagnantValueCents": 0, "zeroSalesCount": 0, "cleanupCount": 0},
            "coverage": {"unagedStockCount": 0, "unagedQuantity": 0},
            "distribution": [],
            "fineDistribution": [],
            "filters": {"warehouses": [], "brands": [], "categories": [], "statuses": list(AGE_STATUSES), "ageBuckets": [{"value": key, "label": label} for key, label, _minimum, _maximum in AGE_BUCKETS]},
            "pagination": {"page": page, "pageSize": page_size, "limit": page_size, "total": 0, "returned": 0, "totalPages": 0, "truncated": False},
            "items": [],
        }
    product_codes: set[str] = set()
    source_rows: list[object]
    if age_batch:
        source_rows = list(InventoryAgeLine.objects.filter(batch_id=age_batch.id).order_by("product_code", "warehouse", "id"))
    else:
        source_rows = list(InventoryStockLine.objects.filter(batch_id=stock_batch.id).order_by("product_code", "warehouse", "id"))  # type: ignore[union-attr]
    for row in source_rows:
        product_codes.add(str(row.product_code))  # type: ignore[attr-defined]
    masters = {
        row.product_code: row
        for row in ErpProductMaster.objects.filter(product_code__in=product_codes)
    }
    warehouse_mapping = effective_mapping()
    items: list[dict[str, object]] = []
    for raw in source_rows:
        warehouse = str(raw.warehouse)  # type: ignore[attr-defined]
        if warehouse.strip() == "刷刷仓":
            continue
        warehouse_classification = classify_warehouse(
            warehouse,
            stored_type=str(raw.warehouse_type),  # type: ignore[attr-defined]
            stored_category=str(getattr(raw, "warehouse_category", "")),
            stored_include_in_inventory=bool(getattr(raw, "include_in_inventory", True)),
            mapping=warehouse_mapping,
        )
        available = int(raw.available_quantity)  # type: ignore[attr-defined]
        unit_cost = int(raw.unit_cost_cents)  # type: ignore[attr-defined]
        stock_value = max(available, 0) * unit_cost
        age_days = raw.inventory_age_days  # type: ignore[attr-defined]
        sales_7d = raw.sales_7d_quantity  # type: ignore[attr-defined]
        sales_30d = raw.sales_30d_quantity  # type: ignore[attr-defined]
        status, label, recommendation = _age_classification(available, age_days, sales_30d)
        bucket_key, bucket_label = _age_bucket(age_days)
        master = masters.get(str(raw.product_code))  # type: ignore[attr-defined]
        items.append(
            {
                "key": f"{warehouse}\x1f{raw.product_code}",  # type: ignore[attr-defined]
                "productCode": str(raw.product_code),  # type: ignore[attr-defined]
                "productName": str(raw.product_name or (master.product_name if master else "") or raw.product_code),  # type: ignore[attr-defined]
                "brand": master.brand if master else getattr(raw, "brand", ""),
                "specification": str(raw.specification or (master.specification if master else "")),  # type: ignore[attr-defined]
                "category": str(raw.category or (master.category if master else "") or "未分类"),  # type: ignore[attr-defined]
                "warehouse": warehouse,
                "warehouseType": warehouse_classification.warehouse_type,
                "warehouseCategory": warehouse_classification.category,
                "warehouseLabel": warehouse_classification.label,
                "includedInInventory": warehouse_classification.include_in_inventory,
                "availableQuantity": available,
                "stockValueCents": stock_value,
                "inventoryAgeDays": age_days,
                "ageBucketKey": bucket_key,
                "ageBucketLabel": bucket_label,
                "sales7dQuantity": sales_7d,
                "sales30dQuantity": sales_30d,
                "status": status,
                "statusLabel": label,
                "recommendation": recommendation,
            }
        )
    facets = {
        "warehouses": sorted({str(item["warehouse"]) for item in items}),
        "brands": sorted({str(item["brand"]) for item in items if item["brand"]}),
        "categories": sorted({str(item["category"]) for item in items if item["category"]}),
    }
    warehouses = set(_selected(options, "warehouses", 10))
    brands = set(_selected(options, "brands", 20))
    categories = set(_selected(options, "categories", 20))
    statuses = set(_selected(options, "statuses", len(AGE_STATUSES)))
    buckets = set(_selected(options, "ageBuckets", len(AGE_BUCKETS)))
    exact_key = options.get("exactKey")
    filtered = [
        item
        for item in items
        if _matches_text(item, options.get("query"), ("productCode", "productName", "brand", "specification", "category", "warehouse"))
        and (not warehouses or item["warehouse"] in warehouses)
        and (not brands or item["brand"] in brands)
        and (not categories or item["category"] in categories)
        and (not statuses or item["status"] in statuses)
        and (not buckets or item["ageBucketKey"] in buckets)
        and (exact_key is None or item["key"] == exact_key)
    ]
    card_filter = options.get("cardFilter")
    if card_filter == "stagnant":
        filtered = [item for item in filtered if item["status"] == "stagnant"]
    elif card_filter == "aged90":
        filtered = [item for item in filtered if item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= 90 and int(item["availableQuantity"]) > 0]
    elif card_filter == "zero_sales":
        filtered = [item for item in filtered if item["sales30dQuantity"] is not None and int(item["sales30dQuantity"]) <= 0 and int(item["availableQuantity"]) > 0]
    order = {"stagnant": 0, "slow": 1, "aged": 2, "healthy": 3, "no_stock": 4}
    filtered.sort(
        key=lambda item: (
            order[str(item["status"])],
            -(int(item["inventoryAgeDays"]) if item["inventoryAgeDays"] is not None else -1),
            -(int(item["stockValueCents"]) if item["stockValueCents"] is not None else -1),
            str(item["productCode"]),
            str(item["warehouse"]),
        )
    )
    total = len(filtered)
    aged_rows = [item for item in filtered if item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= 0 and int(item["availableQuantity"]) > 0]
    aged_quantity = sum(int(item["availableQuantity"]) for item in aged_rows)
    aged_value = sum(int(item["stockValueCents"] or 0) for item in aged_rows)
    def distribution(key: str, label: str, minimum: int, maximum: int | None) -> dict[str, object]:
        selected = [item for item in filtered if item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= minimum and (maximum is None or int(item["inventoryAgeDays"]) <= maximum)]
        return {"key": key, "label": label, "count": len(selected), "valueCents": sum(int(item["stockValueCents"] or 0) for item in selected)}
    fine: list[dict[str, object]] = []
    for key, label, minimum, maximum in AGE_BUCKETS:
        selected = [item for item in filtered if item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= minimum and (maximum is None or int(item["inventoryAgeDays"]) <= maximum) and int(item["availableQuantity"]) > 0]
        quantity = sum(int(item["availableQuantity"]) for item in selected)
        value = sum(int(item["stockValueCents"] or 0) for item in selected)
        fine.append({"key": key, "label": label, "count": len(selected), "quantity": quantity, "valueCents": value, "quantityShare": quantity / aged_quantity if aged_quantity else 0, "valueShare": value / aged_value if aged_value else 0})
    age_sales = any(item["sales30dQuantity"] is not None for item in items)
    return {
        "hasInventory": True,
        "sync": {"inventoryAsOf": source_batch.snapshot_date.isoformat(), "latestInventoryBatchId": source_batch.id, "sourceKey": "inventory_age" if age_batch else "inventory", "hasAgeSales": age_sales},
        "metrics": {
            "skuWarehouseCount": total,
            "stockValueComplete": sum(item["stockValueCents"] is not None for item in filtered) >= total,
            "aged90Count": sum(item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= 90 and int(item["availableQuantity"]) > 0 for item in filtered),
            "aged90ValueCents": sum(int(item["stockValueCents"] or 0) for item in filtered if item["inventoryAgeDays"] is not None and int(item["inventoryAgeDays"]) >= 90 and int(item["availableQuantity"]) > 0),
            "stagnantCount": sum(item["status"] == "stagnant" for item in filtered),
            "stagnantValueCents": sum(int(item["stockValueCents"] or 0) for item in filtered if item["status"] == "stagnant"),
            "zeroSalesCount": sum(item["sales30dQuantity"] is not None and int(item["sales30dQuantity"]) <= 0 and int(item["availableQuantity"]) > 0 for item in filtered),
            "cleanupCount": sum(item["status"] in {"stagnant", "slow", "aged"} for item in filtered),
        },
        "coverage": {"unagedStockCount": sum(item["inventoryAgeDays"] is None and int(item["availableQuantity"]) > 0 for item in filtered), "unagedQuantity": sum(int(item["availableQuantity"]) for item in filtered if item["inventoryAgeDays"] is None and int(item["availableQuantity"]) > 0)},
        "distribution": [distribution("0-30", "0–30 天", 0, 30), distribution("31-60", "31–60 天", 31, 60), distribution("61-90", "61–89 天", 61, 89), distribution("90+", "90 天以上", 90, None)],
        "fineDistribution": fine,
        "filters": {**facets, "statuses": list(AGE_STATUSES), "ageBuckets": [{"value": key, "label": label} for key, label, _minimum, _maximum in AGE_BUCKETS]},
        "pagination": {"page": page, "pageSize": page_size, "limit": page_size, "total": total, "returned": len(filtered[offset : offset + page_size]), "totalPages": (total + page_size - 1) // page_size, "truncated": offset + len(filtered[offset : offset + page_size]) < total},
        "items": filtered[offset : offset + page_size],
    }


def inventory_inbound_monitor(principal: Principal, options: dict[str, object]) -> dict[str, object]:
    page, page_size, offset = _pagination(options)
    latest = _latest_batch("stock")
    if latest is None:
        return {
            "hasInventory": False,
            "sync": {"inventoryAsOf": None, "salesThrough": None, "latestInventoryBatchId": None, "salesRevision": None},
            "scope": {"warehouseType": "jd_rdc", "valuationBasis": "fixed_cost", "supplyPriceAvailable": False, "nativeComparisonAvailable": False},
            "metrics": {"itemCount": 0, "warehouseCount": 0, "availableQuantity": 0, "inTransitQuantity": 0, "knownStockValueCents": 0, "costCoverageRate": 1, "salesMatchRate": 0, "outbound30dQuantity": 0, "turnoverDays": None, "staleItemCount": 0, "staleValueCents": 0, "missingSupplierCount": 0},
            "filters": {"warehouses": [], "brands": [], "categories": [], "suppliers": []},
            "pagination": {"page": page, "pageSize": page_size, "total": 0, "returned": 0, "totalPages": 0, "truncated": False},
            "regions": [], "items": [],
            "disclosures": ["当前没有库存快照。", "京东原生库存/周转指标尚未接入，暂不输出原生差异或残差结论。"],
        }
    warehouse_mapping = effective_mapping()
    stock = [row for row in InventoryStockLine.objects.filter(batch_id=latest.id).order_by("product_code", "warehouse", "id") if row.warehouse.strip() != "刷刷仓" and _is_jd_warehouse(row.warehouse, row.warehouse_type, warehouse_mapping)]
    product_codes = sorted({row.product_code for row in stock})
    if len(product_codes) > MAX_INBOUND_PRODUCTS:
        raise InventoryApiError("京东入仓货品数量超过销售消费查询上限", code="service_unavailable", status=503)
    revision = _sales_revision()
    sales_data: dict[str, object] = {"asOfDate": None, "rows": [], "truncated": False}
    if product_codes:
        sales_data = _sales_query(principal, {"operation": "inventory_inbound_windows", "asOfDate": None, "productCodes": product_codes, "limit": 10_000})
        if _sales_revision() != revision or sales_data.get("truncated") is not False:
            raise InventoryApiError("销售版本在京东入仓计算期间发生变化", code="service_unavailable", status=503)
    requested = set(product_codes)
    sales: dict[tuple[str, str], dict[str, object]] = {}
    for row in sales_data.get("rows", []):  # type: ignore[union-attr]
        if not isinstance(row, dict) or row.get("productCode") not in requested or not isinstance(row.get("warehouseKey"), str):
            raise InventoryApiError("京东入仓销售响应越出请求范围", code="service_unavailable", status=503)
        key = (str(row["productCode"]), str(row["warehouseKey"]))
        if key in sales:
            raise InventoryApiError("京东入仓销售响应包含重复业务键", code="service_unavailable", status=503)
        sales[key] = row
    masters = {row.product_code: row for row in ErpProductMaster.objects.filter(product_code__in=product_codes)}
    items: list[dict[str, object]] = []
    for row in stock:
        demand = sales.get((row.product_code, _warehouse_key(row.warehouse)))
        master = masters.get(row.product_code)
        available = int(row.available_quantity)
        priced = max(available, 0)
        known_value = priced * int(row.unit_cost_cents)
        sales_7 = int(demand["sales7dQuantity"]) if demand else None
        sales_30 = int(demand["sales30dQuantity"]) if demand else None
        sales_90 = int(demand["sales90dQuantity"]) if demand else None
        risk = "no_stock" if available <= 0 else "stale" if (row.inventory_age_days or 0) >= 90 or (sales_90 is not None and sales_90 <= 0) else "unknown" if sales_90 is None else "normal"
        items.append({
            "key": f"{row.warehouse}\x1f{row.product_code}", "productCode": row.product_code,
            "productName": row.product_name or (master.product_name if master else "") or row.product_code,
            "brand": row.brand or (master.brand if master else ""), "category": row.category or (master.category if master else "") or "未分类",
            "supplier": row.supplier.strip() or (master.supplier.strip() if master and master.supplier.strip() else "未映射供应商"), "warehouse": row.warehouse,
            "availableQuantity": available, "inTransitQuantity": int(row.in_transit_quantity), "inventoryAgeDays": row.inventory_age_days,
            "knownStockValueCents": known_value, "_pricedQuantity": priced,
            "costCoverageRate": priced / max(available, 0) if available > 0 else 1,
            "unitCostCents": int(row.unit_cost_cents),
            "outbound7dQuantity": sales_7, "outbound30dQuantity": sales_30, "outbound90dQuantity": sales_90,
            "turnoverDays": max(0, available) / (sales_30 / 30) if sales_30 is not None and sales_30 > 0 else None,
            "risk": risk,
        })
    facets = {key: sorted({str(item[field]) for item in items if item[field]}) for key, field in (("warehouses", "warehouse"), ("brands", "brand"), ("categories", "category"), ("suppliers", "supplier"))}
    warehouses = set(_selected(options, "warehouses", 10)); brands = set(_selected(options, "brands", 20)); categories = set(_selected(options, "categories", 20)); suppliers = set(_selected(options, "suppliers", 20))
    filtered = [item for item in items if _matches_text(item, options.get("query"), ("productCode", "productName", "brand", "category", "supplier", "warehouse")) and (not warehouses or item["warehouse"] in warehouses) and (not brands or item["brand"] in brands) and (not categories or item["category"] in categories) and (not suppliers or item["supplier"] in suppliers)]
    if options.get("cardFilter") == "stale":
        filtered = [item for item in filtered if item["risk"] == "stale"]
    filtered.sort(key=lambda item: (0 if item["risk"] == "stale" else 1, -int(item["knownStockValueCents"]), str(item["productCode"]), str(item["warehouse"])))
    total = len(filtered); positive = sum(max(0, int(item["availableQuantity"])) for item in filtered); priced = sum(int(item["_pricedQuantity"]) for item in filtered); sales_30_total = sum(max(0, int(item["outbound30dQuantity"] or 0)) for item in filtered)
    regions: list[dict[str, object]] = []
    for warehouse in sorted({str(item["warehouse"]) for item in filtered}):
        rows = [item for item in filtered if item["warehouse"] == warehouse]; quantity = sum(int(item["availableQuantity"]) for item in rows); outbound = sum(max(0, int(item["outbound30dQuantity"] or 0)) for item in rows)
        regions.append({"warehouse": warehouse, "itemCount": len(rows), "availableQuantity": quantity, "inTransitQuantity": sum(int(item["inTransitQuantity"]) for item in rows), "knownStockValueCents": sum(int(item["knownStockValueCents"]) for item in rows), "outbound30dQuantity": outbound, "turnoverDays": max(0, quantity) / (outbound / 30) if outbound > 0 else None, "salesMatchRate": sum(item["outbound30dQuantity"] is not None for item in rows) / len(rows) if rows else 0})
    regions.sort(key=lambda item: (-int(item["knownStockValueCents"]), str(item["warehouse"])))
    page_items = []
    for item in filtered[offset : offset + page_size]:
        public = dict(item); public.pop("_pricedQuantity", None); page_items.append(public)
    return {
        "hasInventory": True,
        "sync": {"inventoryAsOf": latest.snapshot_date.isoformat(), "salesThrough": sales_data.get("asOfDate"), "latestInventoryBatchId": latest.id, "salesRevision": revision if product_codes else None},
        "scope": {"warehouseType": "jd_rdc", "valuationBasis": "fixed_cost", "supplyPriceAvailable": False, "nativeComparisonAvailable": False},
        "metrics": {"itemCount": total, "warehouseCount": len({item["warehouse"] for item in filtered}), "availableQuantity": sum(int(item["availableQuantity"]) for item in filtered), "inTransitQuantity": sum(int(item["inTransitQuantity"]) for item in filtered), "knownStockValueCents": sum(int(item["knownStockValueCents"]) for item in filtered), "costCoverageRate": min(1, priced / positive) if positive > 0 else 1, "salesMatchRate": sum(item["outbound30dQuantity"] is not None for item in filtered) / total if total else 0, "outbound30dQuantity": sales_30_total, "turnoverDays": max(0, sum(int(item["availableQuantity"]) for item in filtered)) / (sales_30_total / 30) if sales_30_total > 0 else None, "staleItemCount": sum(item["risk"] == "stale" for item in filtered), "staleValueCents": sum(int(item["knownStockValueCents"]) for item in filtered if item["risk"] == "stale"), "missingSupplierCount": sum(item["supplier"] == "未映射供应商" for item in filtered)},
        "filters": facets,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "returned": len(page_items), "totalPages": (total + page_size - 1) // page_size, "truncated": offset + len(page_items) < total},
        "regions": regions[:100], "items": page_items,
        "disclosures": ["仅统计京东 RDC/DC 与可识别的京东区域平台仓；历史快照按受控仓名规则兼容识别，不改写原始事实。", "库存货值按系统固定成本计算；供应价/结算价尚未接入，不能替代京东结算口径。", "京东原生库存与原生周转指标尚未接入，因此暂不输出原生差异、残差 SKU 或一致性通过结论。", "7/30/90 日出库采用销售明细正向销量，退款不计为出库；刷刷仓与补差价专用行已排除。"],
    }
