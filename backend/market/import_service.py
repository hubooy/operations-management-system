from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .errors import MarketApiError
from .models import (
    MarketImageCache,
    MarketImageCacheJob,
    MarketImageCacheJobItem,
    MarketImportAttempt,
    MarketImportBatch,
    MarketImportFingerprint,
    MarketImportScopeHead,
    MarketMasterIdentity,
    MarketPriceSnapshot,
    MarketRankingEntry,
    MarketSkuGmvTotal,
    MarketSubcategoryTaxonomy,
)
from .revisions import assert_write_authority, bump_revision, canonical_json
from .serialization import batch_payload


MAX_ROWS = 5_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
HEX64 = re.compile(r"^[a-f0-9]{64}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROW_KEYS = {
    "naturalKey",
    "sourceRowNumber",
    "periodStart",
    "periodEnd",
    "category",
    "scope",
    "priceBandFilter",
    "rankingDimension",
    "operationMode",
    "subcategory",
    "rank",
    "skuCode",
    "productName",
    "brand",
    "priceCents",
    "priceLowCents",
    "priceHighCents",
    "priceEstimated",
    "priceRaw",
    "gmvCents",
    "gmvLowCents",
    "gmvHighCents",
    "gmvRaw",
    "quantity",
    "quantityLow",
    "quantityHigh",
    "quantityRaw",
    "pageViews",
    "pageViewsRaw",
    "visitors",
    "visitorsLow",
    "visitorsHigh",
    "visitorsRaw",
    "conversionBps",
    "conversionLowBps",
    "conversionHighBps",
    "conversionRaw",
    "cartCustomers",
    "cartCustomersRaw",
    "searchClicks",
    "searchClicksRaw",
    "imageUrl",
    "productUrl",
    "raw",
}
PAYLOAD_KEYS = {
    "contractVersion",
    "sourceType",
    "fileName",
    "fileSizeBytes",
    "rawFileHash",
    "contentHash",
    "sheetName",
    "rows",
    "warnings",
    "scope",
}


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise MarketApiError(f"{label} 必须是字符串")
    normalized = value.strip()
    if required and not normalized:
        raise MarketApiError(f"{label} 不能为空")
    if len(normalized) > maximum:
        raise MarketApiError(f"{label} 超出长度上限")
    return normalized


def _integer(
    value: object,
    label: str,
    *,
    nullable: bool = False,
    minimum: int = -MAX_SAFE_INTEGER,
    maximum: int = MAX_SAFE_INTEGER,
) -> int | None:
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise MarketApiError(f"{label} 必须是整数")
    if value < minimum or value > maximum:
        raise MarketApiError(f"{label} 超出安全范围")
    return value


def _natural_key(row: dict[str, object]) -> str:
    parts = [
        str(row["periodStart"]),
        str(row["periodEnd"]),
        str(row["category"]),
        str(row["scope"]),
        str(row["priceBandFilter"]),
        str(row["rankingDimension"]),
        str(row["skuCode"]),
    ]
    return "market-key-v2|" + "|".join(
        f"{len(value.encode('utf-8'))}:{value}" for value in parts
    )


def _scope_identity(row: dict[str, object]) -> dict[str, str]:
    return {
        "category": str(row["category"]),
        "scope": str(row["scope"]),
        "rankingDimension": str(row["rankingDimension"]),
        "priceBandFilter": str(row["priceBandFilter"]),
        "periodStart": str(row["periodStart"]),
        "periodEnd": str(row["periodEnd"]),
    }


def _lock_identity(scope: dict[str, str]) -> dict[str, str]:
    return {
        "category": scope["category"],
        "scope": scope["scope"],
        "rankingDimension": scope["rankingDimension"],
        "month": scope["periodEnd"][:7],
    }


def _scope_key(scope: dict[str, str]) -> str:
    return hashlib.sha256(
        ("market-scope-v1\n" + canonical_json(scope)).encode("utf-8")
    ).hexdigest()


def _combined_scope_key(scopes: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        ("market-scope-set-v1\n" + canonical_json(scopes)).encode("utf-8")
    ).hexdigest()


def _canonical_business_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    ignored = {"sourceRowNumber", "raw", "naturalKey"}
    return [
        {key: row[key] for key in sorted(ROW_KEYS - ignored)}
        for row in sorted(rows, key=lambda item: str(item["naturalKey"]))
    ]


def _content_hash(
    rows: list[dict[str, object]], scope: dict[str, object]
) -> str:
    material = {
        "contractVersion": "market-import-v1",
        "scope": scope,
        "rows": _canonical_business_rows(rows),
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _normalize_row(value: object, row_index: int) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != ROW_KEYS:
        raise MarketApiError(f"第 {row_index} 条市场数据字段不完整或包含未知字段")
    row: dict[str, object] = {}
    row["sourceRowNumber"] = _integer(
        value["sourceRowNumber"], "sourceRowNumber", minimum=1, maximum=1_000_000
    )
    for key, maximum, required in (
        ("periodStart", 10, True),
        ("periodEnd", 10, True),
        ("category", 200, True),
        ("scope", 200, True),
        ("priceBandFilter", 200, True),
        ("rankingDimension", 8, True),
        ("operationMode", 16, True),
        ("subcategory", 200, False),
        ("skuCode", 200, True),
        ("productName", 1_000, False),
        ("brand", 300, False),
        ("priceRaw", 500, False),
        ("gmvRaw", 500, False),
        ("quantityRaw", 500, False),
        ("pageViewsRaw", 500, False),
        ("visitorsRaw", 500, False),
        ("conversionRaw", 500, False),
        ("cartCustomersRaw", 500, False),
        ("searchClicksRaw", 500, False),
        ("imageUrl", 2_048, False),
        ("productUrl", 2_048, False),
    ):
        row[key] = _text(value[key], key, maximum, required=required)
    if not ISO_DATE.fullmatch(str(row["periodStart"])) or not ISO_DATE.fullmatch(
        str(row["periodEnd"])
    ):
        raise MarketApiError("市场统计周期必须是 YYYY-MM-DD")
    if str(row["periodStart"]) > str(row["periodEnd"]):
        raise MarketApiError("市场统计开始日期不能晚于结束日期")
    if row["rankingDimension"] not in {"SKU", "SPU"}:
        raise MarketApiError("rankingDimension 仅支持 SKU 或 SPU")
    if row["operationMode"] not in {"POP", "自营", "未知"}:
        raise MarketApiError("operationMode 无效")
    row["rank"] = _integer(value["rank"], "rank", nullable=True, minimum=1)
    nullable_numbers = (
        "priceCents",
        "priceLowCents",
        "priceHighCents",
        "gmvLowCents",
        "gmvHighCents",
        "quantityLow",
        "quantityHigh",
        "visitorsLow",
        "visitorsHigh",
        "conversionBps",
        "conversionLowBps",
        "conversionHighBps",
    )
    for key in nullable_numbers:
        row[key] = _integer(value[key], key, nullable=True)
    for key in (
        "gmvCents",
        "quantity",
        "pageViews",
        "visitors",
        "cartCustomers",
        "searchClicks",
    ):
        row[key] = _integer(value[key], key)
    if not isinstance(value["priceEstimated"], bool):
        raise MarketApiError("priceEstimated 必须是布尔值")
    row["priceEstimated"] = value["priceEstimated"]
    if not isinstance(value["raw"], dict):
        raise MarketApiError("raw 必须是对象")
    raw_json = canonical_json(value["raw"])
    if len(raw_json.encode("utf-8")) > 256_000:
        raise MarketApiError("单行市场原始数据超过安全上限")
    row["raw"] = value["raw"]
    expected_natural_key = _natural_key(row)
    if value["naturalKey"] != expected_natural_key:
        raise MarketApiError(f"第 {row_index} 条市场数据业务身份不一致")
    row["naturalKey"] = expected_natural_key
    return row


def validate_import_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise MarketApiError("市场导入契约字段不完整或包含未知字段")
    if payload["contractVersion"] != "market-import-v1":
        raise MarketApiError("市场导入契约版本不受支持")
    source_type = _text(payload["sourceType"], "sourceType", 64, required=True)
    file_name = _text(payload["fileName"], "fileName", 1_000, required=True)
    file_size = _integer(
        payload["fileSizeBytes"], "fileSizeBytes", minimum=1, maximum=25 * 1024 * 1024
    )
    raw_hash = _text(payload["rawFileHash"], "rawFileHash", 64, required=True).lower()
    supplied_content_hash = _text(
        payload["contentHash"], "contentHash", 64, required=True
    ).lower()
    if not HEX64.fullmatch(raw_hash) or not HEX64.fullmatch(supplied_content_hash):
        raise MarketApiError("市场导入摘要必须是 64 位 SHA-256")
    if not isinstance(payload["rows"], list) or not payload["rows"]:
        raise MarketApiError("市场导入没有可发布的数据行")
    if len(payload["rows"]) > MAX_ROWS:
        raise MarketApiError(f"市场分析单次最多导入 {MAX_ROWS} 条数据")
    rows = [_normalize_row(item, index + 1) for index, item in enumerate(payload["rows"])]
    natural_keys = [str(row["naturalKey"]) for row in rows]
    source_rows = [int(row["sourceRowNumber"]) for row in rows]
    if len(set(natural_keys)) != len(rows) or len(set(source_rows)) != len(rows):
        raise MarketApiError("市场导入包含重复业务身份或源行号")
    ranges = sorted(
        {_scope_key(_scope_identity(row)): _scope_identity(row) for row in rows}.values(),
        key=canonical_json,
    )
    scope = {"sourceType": source_type, "ranges": ranges}
    if payload["scope"] != scope:
        raise MarketApiError("市场导入精确业务范围与数据行不一致")
    calculated = _content_hash(rows, scope)
    if calculated != supplied_content_hash:
        raise MarketApiError("市场导入规范化业务内容摘要不一致")
    warnings = payload["warnings"]
    if not isinstance(warnings, list) or len(warnings) > 1_000:
        raise MarketApiError("warnings 必须是有界数组")
    return {
        "sourceType": source_type,
        "fileName": file_name,
        "fileSizeBytes": int(file_size or 0),
        "rawFileHash": raw_hash,
        "contentHash": calculated,
        "sheetName": _text(payload["sheetName"], "sheetName", 300),
        "rows": rows,
        "warnings": warnings[:100],
        "scope": scope,
        "ranges": ranges,
        "lockScopes": sorted(
            {
                _scope_key(_lock_identity(item)): _lock_identity(item)
                for item in ranges
            }.values(),
            key=canonical_json,
        ),
        "combinedScopeKey": _combined_scope_key(ranges),
    }


def _advisory_scope_locks(keys: list[str]) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for key in sorted(keys):
            lock = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock])


def _price_snapshot_id(row: dict[str, object]) -> str:
    return "market-price-v5-" + hashlib.sha256(
        canonical_json(
            [
                row["category"],
                row["scope"],
                row["skuCode"],
                row["rankingDimension"],
                str(row["periodEnd"])[:7],
            ]
        ).encode()
    ).hexdigest()


def _entry_model(row: dict[str, object], batch_id: str, generation: str) -> MarketRankingEntry:
    return MarketRankingEntry(
        natural_key=row["naturalKey"],
        source_row_number=row["sourceRowNumber"],
        period_start=row["periodStart"],
        period_end=row["periodEnd"],
        category=row["category"],
        scope=row["scope"],
        price_band_filter=row["priceBandFilter"],
        ranking_dimension=row["rankingDimension"],
        operation_mode=row["operationMode"],
        subcategory=row["subcategory"],
        source_brand=row["brand"],
        source_operation_mode=row["operationMode"],
        source_subcategory=row["subcategory"],
        rank=row["rank"],
        sku_code=row["skuCode"],
        product_name=row["productName"],
        brand=row["brand"],
        price_cents=row["priceCents"],
        price_low_cents=row["priceLowCents"],
        price_high_cents=row["priceHighCents"],
        price_estimated=row["priceEstimated"],
        price_raw=row["priceRaw"],
        gmv_cents=row["gmvCents"],
        gmv_low_cents=row["gmvLowCents"],
        gmv_high_cents=row["gmvHighCents"],
        gmv_raw=row["gmvRaw"],
        quantity=row["quantity"],
        quantity_low=row["quantityLow"],
        quantity_high=row["quantityHigh"],
        quantity_raw=row["quantityRaw"],
        page_views=row["pageViews"],
        page_views_raw=row["pageViewsRaw"],
        visitors=row["visitors"],
        visitors_low=row["visitorsLow"],
        visitors_high=row["visitorsHigh"],
        visitors_raw=row["visitorsRaw"],
        conversion_bps=row["conversionBps"],
        conversion_low_bps=row["conversionLowBps"],
        conversion_high_bps=row["conversionHighBps"],
        conversion_raw=row["conversionRaw"],
        cart_customers=row["cartCustomers"],
        cart_customers_raw=row["cartCustomersRaw"],
        search_clicks=row["searchClicks"],
        search_clicks_raw=row["searchClicksRaw"],
        image_url=row["imageUrl"],
        product_url=row["productUrl"],
        raw_json=row["raw"],
        last_import_batch_id=batch_id,
        migration_generation=generation,
    )


def _entry_business_row(entry: MarketRankingEntry) -> dict[str, object]:
    """Reconstruct only the normalized business fields used by the content fingerprint."""
    return {
        "naturalKey": entry.natural_key,
        "sourceRowNumber": int(entry.source_row_number),
        "periodStart": entry.period_start,
        "periodEnd": entry.period_end,
        "category": entry.category,
        "scope": entry.scope,
        "priceBandFilter": entry.price_band_filter,
        "rankingDimension": entry.ranking_dimension,
        "operationMode": entry.source_operation_mode or entry.operation_mode,
        "subcategory": entry.source_subcategory,
        "rank": entry.rank,
        "skuCode": entry.sku_code,
        "productName": entry.product_name,
        "brand": entry.source_brand,
        "priceCents": entry.price_cents,
        "priceLowCents": entry.price_low_cents,
        "priceHighCents": entry.price_high_cents,
        "priceEstimated": entry.price_estimated,
        "priceRaw": entry.price_raw,
        "gmvCents": int(entry.gmv_cents),
        "gmvLowCents": entry.gmv_low_cents,
        "gmvHighCents": entry.gmv_high_cents,
        "gmvRaw": entry.gmv_raw,
        "quantity": int(entry.quantity),
        "quantityLow": entry.quantity_low,
        "quantityHigh": entry.quantity_high,
        "quantityRaw": entry.quantity_raw,
        "pageViews": int(entry.page_views),
        "pageViewsRaw": entry.page_views_raw,
        "visitors": int(entry.visitors),
        "visitorsLow": entry.visitors_low,
        "visitorsHigh": entry.visitors_high,
        "visitorsRaw": entry.visitors_raw,
        "conversionBps": entry.conversion_bps,
        "conversionLowBps": entry.conversion_low_bps,
        "conversionHighBps": entry.conversion_high_bps,
        "conversionRaw": entry.conversion_raw,
        "cartCustomers": int(entry.cart_customers),
        "cartCustomersRaw": entry.cart_customers_raw,
        "searchClicks": int(entry.search_clicks),
        "searchClicksRaw": entry.search_clicks_raw,
        "imageUrl": entry.image_url,
        "productUrl": entry.product_url,
        "raw": {},
    }


def _current_content_hash(ranges: list[dict[str, str]], scope: dict[str, object]) -> str:
    rows: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for item in ranges:
        current = MarketRankingEntry.objects.filter(
            period_start=item["periodStart"],
            period_end=item["periodEnd"],
            category=item["category"],
            scope=item["scope"],
            price_band_filter=item["priceBandFilter"],
            ranking_dimension=item["rankingDimension"],
        )
        for entry in current.iterator(chunk_size=1_000):
            if entry.id in seen_ids:
                continue
            seen_ids.add(entry.id)
            rows.append(_entry_business_row(entry))
    return _content_hash(rows, scope)


def _refresh_derived(
    rows: list[dict[str, object]],
    batch_id: str,
    generation: str,
    *,
    removed_identities: set[tuple[str, str, str, str]] | None = None,
    removed_snapshot_keys: set[tuple[str, str, str, str, str]] | None = None,
    removed_sku_codes: set[str] | None = None,
) -> None:
    touched = (removed_identities or set()) | {
        (str(row["category"]), str(row["scope"]), str(row["rankingDimension"]), str(row["skuCode"]))
        for row in rows
    }
    for category, scope, dimension, sku_code in touched:
        latest = (
            MarketRankingEntry.objects.filter(
                category=category,
                scope=scope,
                ranking_dimension=dimension,
                sku_code=sku_code,
            )
            .order_by("-period_end", "-period_start", "-id")
            .first()
        )
        identity = MarketMasterIdentity.objects.filter(
            category=category,
            scope=scope,
            ranking_dimension=dimension,
            sku_code=sku_code,
        ).first()
        if latest is None:
            if identity:
                identity.delete()
            continue
        MarketMasterIdentity.objects.update_or_create(
            category=category,
            scope=scope,
            ranking_dimension=dimension,
            sku_code=sku_code,
            defaults={"latest_entry_id": latest.id},
        )
    for sku_code in (removed_sku_codes or set()) | {
        str(row["skuCode"]) for row in rows
    }:
        total = (
            MarketRankingEntry.objects.filter(sku_code=sku_code).aggregate(total=Sum("gmv_cents"))["total"]
            or 0
        )
        if total:
            MarketSkuGmvTotal.objects.update_or_create(
                sku_code=sku_code, defaults={"gmv_total_cents": total}
            )
        else:
            MarketSkuGmvTotal.objects.filter(sku_code=sku_code).delete()

    newest_by_snapshot: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for row in rows:
        key = (
            str(row["category"]),
            str(row["scope"]),
            str(row["skuCode"]),
            str(row["rankingDimension"]),
            str(row["periodEnd"])[:7],
        )
        current = newest_by_snapshot.get(key)
        if current is None or (
            str(row["periodEnd"]), str(row["periodStart"]), int(row["sourceRowNumber"])
        ) > (
            str(current["periodEnd"]),
            str(current["periodStart"]),
            int(current["sourceRowNumber"]),
        ):
            newest_by_snapshot[key] = row
    for key in (removed_snapshot_keys or set()) - set(newest_by_snapshot):
        if not MarketRankingEntry.objects.filter(
            category=key[0],
            scope=key[1],
            sku_code=key[2],
            ranking_dimension=key[3],
            period_end__startswith=key[4],
        ).exists():
            MarketPriceSnapshot.objects.filter(
                category=key[0],
                scope=key[1],
                sku_code=key[2],
                ranking_dimension=key[3],
                month=key[4],
            ).delete()
    for key, row in newest_by_snapshot.items():
        image = MarketImageCache.objects.filter(source_url=row["imageUrl"], status="ready").first()
        image_hash = image.content_sha256 if image else ""
        snapshot = MarketPriceSnapshot.objects.filter(
            category=key[0],
            scope=key[1],
            sku_code=key[2],
            ranking_dimension=key[3],
            month=key[4],
        ).first()
        image_changed = bool(snapshot and row["imageUrl"] and snapshot.image_url != row["imageUrl"])
        defaults = {
            "source_price_cents": row["priceCents"],
            "average_transaction_price_cents": (
                round(int(row["gmvCents"]) / int(row["quantity"]))
                if int(row["quantity"]) > 0
                else None
            ),
            "price_low_cents": row["priceLowCents"],
            "price_high_cents": row["priceHighCents"],
            "image_url": row["imageUrl"] or (snapshot.image_url if snapshot else ""),
            "image_content_sha256": image_hash or ("" if image_changed else snapshot.image_content_sha256 if snapshot else ""),
            "source_import_batch_id": batch_id,
            "migration_generation": generation,
        }
        if image_changed:
            defaults.update(
                {
                    "ai_image_price_cents": None,
                    "ai_price_type": "",
                    "ai_confidence_bps": None,
                    "ai_reason": "",
                    "confirmed_market_price_cents": None,
                    "confirmed_by": "",
                    "confirmed_at": None,
                    "source_job_item_id": "",
                    "prompt_version_id": "",
                    "confirmation_status": "source_table" if row["priceCents"] is not None else "missing",
                }
            )
        elif snapshot is None:
            defaults["confirmation_status"] = (
                "source_table" if row["priceCents"] is not None else "missing"
            )
        if snapshot is None:
            MarketPriceSnapshot.objects.create(
                id=_price_snapshot_id(row),
                category=key[0],
                scope=key[1],
                sku_code=key[2],
                ranking_dimension=key[3],
                month=key[4],
                **defaults,
            )
        else:
            for field, value in defaults.items():
                setattr(snapshot, field, value)
            snapshot.save(update_fields=[*defaults, "updated_at"])
        if row["subcategory"]:
            taxonomy_id = "market-subcategory-v2-" + hashlib.sha256(
                canonical_json([row["category"], row["subcategory"]]).encode()
            ).hexdigest()
            taxonomy, created = MarketSubcategoryTaxonomy.objects.get_or_create(
                category=row["category"],
                subcategory=row["subcategory"],
                defaults={
                    "id": taxonomy_id,
                    "status": "active",
                    "updated_by": "market-import",
                    "created_by": "market-import",
                },
            )
            if not created:
                taxonomy.status = "active"
                taxonomy.updated_by = "market-import"
                taxonomy.save(update_fields=["status", "updated_by", "updated_at"])


def _image_job_payload(job: MarketImageCacheJob) -> dict[str, object]:
    counts = {
        row["status"]: int(row["count"])
        for row in MarketImageCacheJobItem.objects.filter(job_id=job.id)
        .values("status")
        .annotate(count=Count("id"))
    }
    total = sum(counts.values())
    cached = counts.get("ready", 0)
    failed = counts.get("failed", 0)
    return {
        "id": job.id,
        "batchId": job.batch_id,
        "status": job.status,
        "total": total,
        "discoveredCount": int(job.discovered_count),
        "discoveryComplete": bool(job.discovery_complete),
        "cached": cached,
        "failed": failed,
        "pending": max(0, total - cached - failed),
        "propagationPending": int(job.propagation_pending_count),
        "processedCount": cached + failed,
        "runCount": int(job.run_count),
        "errorMessage": job.error_message,
    }


def _create_image_job(batch_id: str, actor_email: str) -> MarketImageCacheJob:
    scope_key = f"batch:{batch_id}"
    job, _ = MarketImageCacheJob.objects.get_or_create(
        scope_key=scope_key,
        defaults={
            "id": str(uuid.uuid4()),
            "batch_id": batch_id,
            "status": "queued",
            "requested_by": actor_email,
        },
    )
    return job


def _import_receipt(batch: MarketImportBatch, ranges: list[dict[str, str]]) -> dict[str, object]:
    return {
        "batchId": batch.id,
        "rawFileSha256": batch.raw_file_hash,
        "fileName": batch.file_name,
        "fileSizeBytes": int(batch.file_size_bytes),
        "sourceType": batch.source_type,
        "rowCount": int(batch.row_count),
        "warningCount": int(batch.warning_count),
        "ranges": ranges,
    }


def import_market_payload(payload: object, actor_email: str) -> dict[str, object]:
    attempt = MarketImportAttempt.objects.create(outcome="validating", actor_email=actor_email)
    try:
        normalized = validate_import_payload(payload)
    except Exception as error:
        attempt.outcome = "rejected"
        attempt.error_code = "MARKET_IMPORT_VALIDATION_FAILED"
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["outcome", "error_code", "completed_at"])
        raise
    rows = normalized["rows"]
    ranges = normalized["ranges"]
    lock_scopes = normalized["lockScopes"]
    scope = normalized["scope"]
    assert isinstance(rows, list) and isinstance(ranges, list)
    assert isinstance(lock_scopes, list) and isinstance(scope, dict)
    combined_scope_key = str(normalized["combinedScopeKey"])
    content_hash = str(normalized["contentHash"])
    attempt.scope_key = combined_scope_key
    attempt.raw_file_hash = str(normalized["rawFileHash"])
    attempt.content_hash = content_hash
    attempt.metadata = {"scope": scope, "rowCount": len(rows)}
    attempt.save(
        update_fields=["scope_key", "raw_file_hash", "content_hash", "metadata"]
    )
    existing_fingerprint = MarketImportFingerprint.objects.filter(
        scope_key=combined_scope_key,
        content_hash=content_hash,
        status="completed",
    ).first()
    batch_id = f"market-pg-{content_hash[:24]}-{uuid.uuid4().hex[:12]}"
    generation = uuid.uuid4().hex
    owner_token = uuid.uuid4().hex
    scope_keys = [_scope_key(item) for item in lock_scopes]
    now = timezone.now()
    try:
        with transaction.atomic():
            assert_write_authority()
            _advisory_scope_locks(scope_keys)
            heads: list[MarketImportScopeHead] = []
            for scope_key in sorted(scope_keys):
                try:
                    head, _ = MarketImportScopeHead.objects.get_or_create(scope_key=scope_key)
                except IntegrityError:
                    head = MarketImportScopeHead.objects.get(scope_key=scope_key)
                head = MarketImportScopeHead.objects.select_for_update().get(scope_key=scope_key)
                if (
                    head.status == "processing"
                    and head.heartbeat_at
                    and head.heartbeat_at > now - timedelta(minutes=30)
                ):
                    raise MarketApiError(
                        "相同市场业务范围已有导入正在发布",
                        code="conflict",
                        status=409,
                    )
                heads.append(head)
            if existing_fingerprint:
                existing = MarketImportBatch.objects.filter(
                    id=existing_fingerprint.batch_id,
                    status="completed",
                ).first()
                if existing and _current_content_hash(ranges, scope) == content_hash:
                    image_job = _create_image_job(existing.id, actor_email)
                    attempt.batch_id = existing.id
                    attempt.outcome = "duplicate"
                    attempt.completed_at = timezone.now()
                    attempt.save(update_fields=["batch_id", "outcome", "completed_at"])
                    return {
                        "ok": True,
                        "status": "duplicate",
                        "message": "全部标准化市场资料与当前范围一致，无需重复导入；图片缓存已交给后台任务",
                        "batch": batch_payload(existing),
                        "importReceipt": _import_receipt(existing, ranges),
                        "imageCacheJob": _image_job_payload(image_job),
                    }
            for head in heads:
                head.status = "processing"
                head.owner_token = owner_token
                head.current_batch_id = batch_id
                head.generation += 1
                head.owner_started_at = now
                head.heartbeat_at = now
                head.save()
            dates = sorted(
                [str(row["periodStart"]) for row in rows]
                + [str(row["periodEnd"]) for row in rows]
            )
            batch = MarketImportBatch.objects.create(
                id=batch_id,
                source_type=normalized["sourceType"],
                file_name=normalized["fileName"],
                file_size_bytes=normalized["fileSizeBytes"],
                raw_file_hash=normalized["rawFileHash"],
                content_hash=content_hash,
                sheet_name=normalized["sheetName"],
                status="processing",
                row_count=len(rows),
                warning_count=len(normalized["warnings"]),
                period_start=dates[0],
                period_end=dates[-1],
                warnings_json=normalized["warnings"],
                scope_json=scope,
                actor_email=actor_email,
                migration_generation=generation,
            )
            replacement_groups = {
                (
                    row["periodStart"],
                    row["periodEnd"],
                    row["category"],
                    row["scope"],
                    row["priceBandFilter"],
                    row["rankingDimension"],
                )
                for row in rows
            }
            existing_count = 0
            removed_identities: set[tuple[str, str, str, str]] = set()
            removed_snapshot_keys: set[tuple[str, str, str, str, str]] = set()
            removed_sku_codes: set[str] = set()
            for period_start, period_end, category, scope, price_band, dimension in sorted(
                replacement_groups
            ):
                query = MarketRankingEntry.objects.filter(
                    period_start=period_start,
                    period_end=period_end,
                    category=category,
                    scope=scope,
                    price_band_filter=price_band,
                    ranking_dimension=dimension,
                )
                row_keys = {
                    str(row["naturalKey"])
                    for row in rows
                    if (
                        row["periodStart"],
                        row["periodEnd"],
                        row["category"],
                        row["scope"],
                        row["priceBandFilter"],
                        row["rankingDimension"],
                    )
                    == (period_start, period_end, category, scope, price_band, dimension)
                }
                for previous in query.values(
                    "category", "scope", "ranking_dimension", "sku_code", "period_end"
                ).iterator(chunk_size=1_000):
                    previous_identity = (
                        str(previous["category"]),
                        str(previous["scope"]),
                        str(previous["ranking_dimension"]),
                        str(previous["sku_code"]),
                    )
                    removed_identities.add(previous_identity)
                    removed_sku_codes.add(previous_identity[3])
                    removed_snapshot_keys.add(
                        (
                            previous_identity[0],
                            previous_identity[1],
                            previous_identity[3],
                            previous_identity[2],
                            str(previous["period_end"])[:7],
                        )
                    )
                existing_count += query.filter(natural_key__in=row_keys).count()
                query.delete()
            MarketRankingEntry.objects.bulk_create(
                [_entry_model(row, batch_id, generation) for row in rows], batch_size=500
            )
            _refresh_derived(
                rows,
                batch_id,
                generation,
                removed_identities=removed_identities,
                removed_snapshot_keys=removed_snapshot_keys,
                removed_sku_codes=removed_sku_codes,
            )
            inserted_count = len(rows) - existing_count
            batch.status = "completed"
            batch.inserted_count = inserted_count
            batch.updated_count = existing_count
            batch.completed_at = timezone.now()
            event = {
                "kind": "import",
                "batchId": batch_id,
                "contentHash": content_hash,
                "rowCount": len(rows),
                "scope": scope,
            }
            revision = bump_revision(event)
            from .analysis_options_projection import synchronize_import
            # The replacement loop uses ``scope`` for one ranking label. Take
            # the complete immutable import contract, not that loop variable.
            synchronize_import(normalized["scope"], revision)
            state_token = hashlib.sha256(
                canonical_json({"revision": revision, **event}).encode()
            ).hexdigest()
            batch.published_state_token = state_token
            batch.save()
            MarketImportFingerprint.objects.create(
                batch_id=batch_id,
                scope_key=combined_scope_key,
                scope_json=scope,
                import_hash=str(normalized["rawFileHash"]),
                content_hash=content_hash,
                raw_file_hash=str(normalized["rawFileHash"]),
                row_count=len(rows),
                published_state_token=state_token,
                publication_sequence=int(revision.split(":", 1)[0]),
            )
            for head in heads:
                if head.owner_token != owner_token or head.current_batch_id != batch_id:
                    raise MarketApiError(
                        "市场导入范围所有权已失效", code="version_conflict", status=409
                    )
                head.state_token = state_token
                head.status = "ready"
                head.owner_token = ""
                head.owner_started_at = None
                head.heartbeat_at = None
                head.save()
            image_job = _create_image_job(batch_id, actor_email)
        attempt.batch_id = batch_id
        attempt.outcome = "imported"
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["batch_id", "outcome", "completed_at"])
        return {
            "ok": True,
            "status": "imported",
            "message": f"成功导入 {batch.row_count} 条市场商品数据",
            "batch": batch_payload(batch),
            "importReceipt": _import_receipt(batch, ranges),
            "imageCacheJob": _image_job_payload(image_job),
            "revision": revision,
        }
    except Exception as error:
        attempt.batch_id = batch_id
        attempt.outcome = "failed"
        attempt.error_code = (
            error.code if isinstance(error, MarketApiError) else "MARKET_IMPORT_FAILED"
        )
        attempt.completed_at = timezone.now()
        attempt.save(
            update_fields=["batch_id", "outcome", "error_code", "completed_at"]
        )
        raise
