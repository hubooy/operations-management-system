"""Bounded, read-only diagnosis of one JD promotion fact scope.

The five additive measures are counted once per source row in each dimension.
Dimensions are alternative views of the same facts and must never be added
together. A missing source measure is represented by null, not a projected 0.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from .errors import NetshopApiError
from .models import NetshopImportBatch, NetshopPromotionShopDaily, NetshopRow


SHOP_NAME = "志高商用设备旗舰店"
SCHEMA_VERSION = "jd-promotion-diagnostic-v1"
MAX_SOURCE_ROWS = 150_000
MAX_GROUPS = 30_000
MAX_BATCHES = 500
MAX_RESPONSE_BYTES = 12 * 1024 * 1024

METRICS = {
    "spendCents": ("spend_cents", "spendCents", "花费"),
    "impressions": ("impressions", "impressions", "展现数"),
    "clicks": ("clicks", "clicks", "点击数"),
    "reportedOrderLines": ("net_orders", "netOrders", "总订单行"),
    "reportedGmvCents": ("net_transaction_amount_cents", "netTransactionAmountCents", "总订单金额"),
}


def _bucket() -> dict[str, object]:
    return {
        "rowCount": 0,
        "sums": {name: 0 for name in METRICS},
        "present": {name: 0 for name in METRICS},
    }


def _has_measure(metrics: dict, normalized: str, source: str) -> bool:
    # Import projection uses only finite numeric metrics. A raw placeholder must
    # not turn its projected zero into a complete observed measurement.
    for value in (metrics.get(normalized), metrics.get(source)):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return True
    return False


def _add(bucket: dict[str, object], row: dict, available: dict[str, bool]) -> None:
    bucket["rowCount"] += 1
    for name, (column, _normalized, _source) in METRICS.items():
        bucket["sums"][name] += int(row[column])
        if available[name]:
            bucket["present"][name] += 1


def _metrics(bucket: dict[str, object]) -> dict[str, int | None]:
    count = bucket["rowCount"]
    return {
        name: bucket["sums"][name] if count and bucket["present"][name] == count else None
        for name in METRICS
    }


def _text(raw: dict, *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            normalized = str(value).strip()
            if normalized and normalized not in {"-", "—"}:
                return normalized
    return None


def _group(groups: dict[tuple, dict[str, object]], key: tuple) -> dict[str, object]:
    bucket = groups.get(key)
    if bucket is None:
        if len(groups) >= MAX_GROUPS:
            raise NetshopApiError(
                "推广诊断分组超过完整响应上限，请缩短日期范围",
                code="response_too_large", status=413,
            )
        bucket = _bucket()
        groups[key] = bucket
    return bucket


def _key(parts: tuple) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _group_rows(kind: str, groups: dict[tuple, dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for parts, bucket in groups.items():
        item: dict[str, object] = {
            "key": _key(parts),
            "rowCount": bucket["rowCount"],
            "metrics": _metrics(bucket),
        }
        if kind == "plans":
            item.update(planId=parts[0], id=parts[0], name=bucket.get("name") or "未提供计划名称")
        elif kind == "products":
            item.update(skuId=parts[0], id=parts[0], name=bucket.get("name") or "未提供商品名称")
        elif kind == "keywords":
            item.update(keyword=parts[0], name=parts[0] or "未提供关键词")
        elif kind == "searchTerms":
            item.update(searchTerm=parts[0], name=parts[0] or "未提供搜索词")
        elif kind == "keywordSku":
            item.update(
                keyword=parts[0], skuId=parts[1],
                name=f"{parts[0] or '未提供关键词'} × {parts[1] or '未提供跟单SKU'}",
            )
        elif kind == "searchTermSku":
            item.update(
                searchTerm=parts[0], skuId=parts[1],
                name=f"{parts[0] or '未提供搜索词'} × {parts[1] or '未提供跟单SKU'}",
            )
        else:
            plan_parts, related_value = parts
            item.update(
                planKey=_key(plan_parts), planId=plan_parts[0],
                name=f"{bucket.get('planName') or '未提供计划名称'} × {related_value or '未提供身份'}",
            )
            if kind == "planSku":
                item["skuId"] = related_value
            else:
                item["keyword"] = related_value
        result.append(item)
    result.sort(key=lambda item: (-int(item["metrics"]["spendCents"] or 0), item["key"]))
    return result


def read_promotion_diagnostic(*, start_date: str, end_date: str, source_revision: str) -> dict[str, object]:
    requested_dates = []
    current = date.fromisoformat(start_date)
    final = date.fromisoformat(end_date)
    while current <= final:
        requested_dates.append(current.isoformat())
        current += timedelta(days=1)

    scope = NetshopRow.objects.filter(
        source="jd_promotion", dataset="ad", platform="京东", shop_name=SHOP_NAME,
        business_date__gte=start_date, business_date__lte=end_date,
    )
    row_count = scope.count()
    if row_count > MAX_SOURCE_ROWS:
        raise NetshopApiError(
            "推广诊断原始行超过完整响应上限，请缩短日期范围",
            code="response_too_large", status=413,
        )

    summary = _bucket()
    daily: dict[str, dict[str, object]] = {}
    groups: dict[str, dict[tuple, dict[str, object]]] = {
        name: {} for name in ("plans", "products", "keywords", "searchTerms", "keywordSku", "searchTermSku", "planSku", "planKeyword")
    }
    batches: dict[str, set[str]] = {}
    batch_row_counts: dict[str, int] = {}
    accounts: dict[str, set[str]] = {}
    account_present: dict[str, int] = {}
    columns = (
        "business_date", "last_import_batch_id", "sku_id", "product_name", "raw_json", "metrics_json",
        *(value[0] for value in METRICS.values()),
    )
    for row in scope.values(*columns).iterator(chunk_size=2_000):
        business_date = row["business_date"]
        raw = row["raw_json"] if isinstance(row["raw_json"], dict) else {}
        source_metrics = row["metrics_json"] if isinstance(row["metrics_json"], dict) else {}
        available = {
            name: _has_measure(source_metrics, normalized, source)
            for name, (_column, normalized, source) in METRICS.items()
        }
        _add(summary, row, available)
        _add(daily.setdefault(business_date, _bucket()), row, available)
        batch_id = row["last_import_batch_id"]
        batches.setdefault(business_date, set()).add(batch_id)
        batch_row_counts[batch_id] = batch_row_counts.get(batch_id, 0) + 1
        account = _text(raw, "账户昵称")
        if account:
            account_present[business_date] = account_present.get(business_date, 0) + 1
            account_values = accounts.setdefault(business_date, set())
            account_values.add(account)
            if len(account_values) > 10:
                raise NetshopApiError(
                    "推广来源账户昵称过多，请核对导入店铺映射",
                    code="service_unavailable", status=503,
                )

        plan_id = _text(raw, "计划ID")
        plan_name = _text(raw, "推广计划", "计划名称", "计划")
        plan_key = (plan_id,) if plan_id else (None, plan_name)
        sku_id = row["sku_id"].strip() or None
        product_name = row["product_name"].strip() or _text(raw, "跟单SKU名称")
        keyword = _text(raw, "关键词")
        search_term = _text(raw, "搜索词")
        for kind, key in (
            ("plans", plan_key),
            ("products", (sku_id,)),
            ("keywords", (keyword,)),
            ("searchTerms", (search_term,)),
            ("keywordSku", (keyword, sku_id)),
            ("searchTermSku", (search_term, sku_id)),
            ("planSku", (plan_key, sku_id)),
            ("planKeyword", (plan_key, keyword)),
        ):
            bucket = _group(groups[kind], key)
            _add(bucket, row, available)
            if kind == "plans" and plan_name and plan_name > (bucket.get("name") or ""):
                bucket["name"] = plan_name
            if kind == "products" and product_name and product_name > (bucket.get("name") or ""):
                bucket["name"] = product_name
            if kind in {"planSku", "planKeyword"} and plan_name and plan_name > (bucket.get("planName") or ""):
                bucket["planName"] = plan_name

    shop_rows = {
        item.business_date: item
        for item in NetshopPromotionShopDaily.objects.filter(
            platform="京东", shop_name=SHOP_NAME, source="jd_promotion",
            business_date__gte=start_date, business_date__lte=end_date,
        )
    }
    if set(shop_rows) != set(daily):
        raise NetshopApiError(
            "推广原始行与逐日聚合日期不一致，请等待数据恢复",
            code="service_unavailable", status=503,
        )
    for business_date, bucket in daily.items():
        aggregate = shop_rows[business_date]
        expected = {
            "spendCents": aggregate.spend_cents,
            "impressions": aggregate.impressions,
            "clicks": aggregate.clicks,
            "reportedOrderLines": aggregate.net_orders,
            "reportedGmvCents": aggregate.net_transaction_amount_cents,
        }
        if aggregate.source_row_count != bucket["rowCount"] or any(
            expected[name] != bucket["sums"][name] for name in METRICS
        ):
            raise NetshopApiError(
                "推广原始行与逐日聚合指标不一致，请等待数据恢复",
                code="service_unavailable", status=503,
            )

    # A shop-daily source_batch_id records the publication that rebuilt the
    # aggregate. Its retained source rows may belong to earlier imports.
    all_batch_ids = set(batch_row_counts) | {
        item.source_batch_id for item in shop_rows.values()
    }
    if len(all_batch_ids) > MAX_BATCHES:
        raise NetshopApiError(
            "推广诊断来源批次数超过完整核验上限，请缩短日期范围",
            code="response_too_large", status=413,
        )
    owners = {
        item.id: item for item in NetshopImportBatch.objects.filter(id__in=all_batch_ids)
    }
    def valid_batch(owner: NetshopImportBatch | None) -> bool:
        return bool(
            owner is not None and owner.status == "completed"
            and owner.source == "jd_promotion" and owner.dataset == "ad"
            and owner.platform == "京东" and owner.shop_name == SHOP_NAME
            and owner.date_min and owner.date_max and owner.date_min <= owner.date_max
            and owner.row_count >= 1 and owner.warning_count >= 0
        )

    def batch_payload(batch_id: str) -> dict[str, object]:
        owner = owners[batch_id]
        return {
            "batchId": batch_id, "status": owner.status,
            "source": owner.source, "dataset": owner.dataset,
            "platform": owner.platform, "shopName": owner.shop_name,
            "dateMin": owner.date_min, "dateMax": owner.date_max,
            "rowCount": owner.row_count, "warningCount": owner.warning_count,
        }

    for batch_id, observed_rows in batch_row_counts.items():
        owner = owners.get(batch_id)
        if not batch_id or not valid_batch(owner) or owner.row_count < observed_rows:
            raise NetshopApiError(
                "推广原始行没有可核对的系统导入批次归属",
                code="service_unavailable", status=503,
            )
    for business_date, batch_ids in batches.items():
        publication = owners.get(shop_rows[business_date].source_batch_id)
        if (
            not valid_batch(publication)
            or not (publication.date_min <= business_date <= publication.date_max)
            or any(
            not (owners[batch_id].date_min <= business_date <= owners[batch_id].date_max)
            for batch_id in batch_ids
            )
        ):
            raise NetshopApiError(
                "推广逐日聚合发布批次或原始行批次归属不一致",
                code="service_unavailable", status=503,
            )

    result: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "identity": {"platform": "京东", "shopName": SHOP_NAME},
        "period": {"startDate": start_date, "endDate": end_date},
        "coverage": {
            "requestedDates": requested_dates,
            "presentDates": sorted(daily),
            "missingDates": [value for value in requested_dates if value not in daily],
            "complete": len(daily) == len(requested_dates),
            "rowCount": summary["rowCount"],
            "aggregateReconciled": True,
            "batchOwnershipReconciled": True,
        },
        "sourceRevision": source_revision,
        "sourceBatches": [
            {"date": value, "batchIds": sorted(batches[value]), "accountNicknames": sorted(accounts.get(value, set())),
             "accountPresentRows": account_present.get(value, 0),
             "rowCount": daily[value]["rowCount"],
             "aggregateBatchId": shop_rows[value].source_batch_id,
             "aggregateOwnership": batch_payload(shop_rows[value].source_batch_id),
             "ownership": [batch_payload(batch_id) for batch_id in sorted(batches[value])]}
            for value in sorted(daily)
        ],
        "summary": _metrics(summary),
        "metricAvailability": {
            name: {"presentRows": summary["present"][name], "totalRows": summary["rowCount"],
                   "complete": bool(summary["rowCount"]) and summary["present"][name] == summary["rowCount"]}
            for name in METRICS
        },
        "daily": [
            {"date": value, "metrics": _metrics(daily.get(value, _bucket())),
             "rowCount": daily[value]["rowCount"] if value in daily else 0}
            for value in requested_dates
        ],
        "groups": {name: _group_rows(name, values) for name, values in groups.items()},
        "limitations": [
            "订单行和总订单金额是京准通推广归因口径，不代表 ERP 净销售或利润。",
            "各维度均来自同一批推广行，不可把不同维度的金额相加。",
            "最近日期的归因可能尚未成熟；短周期低样本仅适合观察和复查。",
            "字段缺失时对应指标为 null，metricAvailability 给出有值行数。",
            "来源账户昵称是平台文本，仅作信息；店铺身份依据系统已导入批次的平台与店铺复合键。",
        ],
    }
    if not result["coverage"]["complete"]:
        result["limitations"].append("请求周期有缺日，不能标记为完整周期。")
    # Match JsonResponse's actual serialization, including its separators.
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise NetshopApiError(
            "推广诊断完整响应超过大小上限，请缩短日期范围",
            code="response_too_large", status=413,
        )
    return result
