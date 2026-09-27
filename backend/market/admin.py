from __future__ import annotations

import hashlib
import math
import re
import uuid
from collections import Counter
from datetime import date

from django.db import transaction
from django.db.models import Count, Max, Min, Q, Sum
from django.utils import timezone

from sales.auth import Principal

from .errors import MarketApiError
from .models import (
    MarketAnnotationPromptVersion,
    MarketAnnotationItem,
    MarketAnnotationJob,
    MarketBrandRecognitionJob,
    MarketBrandSeed,
    MarketBrandSuggestion,
    MarketDownloadConfig,
    MarketDownloadTask,
    MarketImageCache,
    MarketImportAttempt,
    MarketImportBatch,
    MarketMasterAuditLog,
    MarketMasterIdentity,
    MarketMasterMappingRule,
    MarketPriceBandItem,
    MarketPriceBandVersion,
    MarketPriceSnapshot,
    MarketRankingEntry,
    MarketSkuAnnotation,
    MarketSkuGmvTotal,
    MarketSubcategoryTaxonomy,
)
from .query import FORMAL_OFFICIAL_PRICE_TYPES, _price_band, _price_band_versions, item_trend
from .revisions import bump_revision, canonical_json, iso
from .serialization import batch_payload
from .system_kpis import system_kpis
from .master_query import identity as _master_identity, master_page, page_context


MAX_PAGE = 10_000
MAX_PAGE_SIZE = 100
MAX_LIST_VALUES = 50
VALID_PRICE_TYPES = {
    "标准售价",
    "到手价",
    "券后价",
    "起售价",
    "价格区间",
    "最低规格价格",
}
VALID_DIMENSIONS = {"SKU", "SPU"}
VALID_MAPPING_KINDS = {"subcategory", "brand_alias", "brand_override", "operation_mode"}
VALID_MAPPING_STATUSES = {"draft", "published", "archived"}
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> MarketApiError:
    return MarketApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 必须是字符串")
    normalized = value.strip()
    if required and not normalized:
        raise _error(f"{label} 不能为空")
    if len(normalized) > maximum:
        raise _error(f"{label} 超出长度上限")
    return normalized


def _integer(
    value: object,
    label: str,
    *,
    fallback: int | None = None,
    minimum: int = 0,
    maximum: int = 100_000_000,
    nullable: bool = False,
) -> int | None:
    if value is None and nullable:
        return None
    if value is None and fallback is not None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise _error(f"{label} 必须是 {minimum} 到 {maximum} 的整数")
    return value


def _list(value: object, label: str, maximum: int = MAX_LIST_VALUES) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise _error(f"{label} 参数无效")
    result: list[str] = []
    for item in value:
        normalized = _text(item, label, 200, required=True)
        if normalized not in result:
            result.append(normalized)
    return result


def _dimension(value: object) -> str:
    normalized = _text(value, "rankingDimension", 8, required=True)
    if normalized not in VALID_DIMENSIONS:
        raise _error("rankingDimension 仅支持 SKU 或 SPU")
    return normalized


def _month(value: object) -> str:
    normalized = _text(value, "month", 7, required=True)
    if not MONTH_RE.fullmatch(normalized):
        raise _error("month 必须是 YYYY-MM")
    return normalized


def _date(value: object, label: str) -> str:
    normalized = _text(value, label, 10, required=True)
    if not DATE_RE.fullmatch(normalized):
        raise _error(f"{label} 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(normalized)
    except ValueError as error:
        raise _error(f"{label} 不是有效日期") from error
    return normalized


def _audit(
    principal: Principal,
    action: str,
    entity_type: str,
    entity_id: str,
    before: object,
    after: object,
) -> None:
    MarketMasterAuditLog.objects.create(
        actor_email=principal.email.lower(),
        actor_role=principal.role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=before if isinstance(before, (dict, list)) else {"value": before},
        after_json=after if isinstance(after, (dict, list)) else {"value": after},
    )


def _snapshot_value(snapshot: MarketPriceSnapshot | None) -> dict[str, object]:
    if snapshot is None:
        return {}
    return {
        "id": snapshot.id,
        "category": snapshot.category,
        "scope": snapshot.scope,
        "skuCode": snapshot.sku_code,
        "rankingDimension": snapshot.ranking_dimension,
        "month": snapshot.month,
        "sourcePriceCents": snapshot.source_price_cents,
        "aiImagePriceCents": snapshot.ai_image_price_cents,
        "aiPriceType": snapshot.ai_price_type,
        "aiConfidenceBps": snapshot.ai_confidence_bps,
        "aiReason": snapshot.ai_reason,
        "confirmedMarketPriceCents": snapshot.confirmed_market_price_cents,
        "averageTransactionPriceCents": snapshot.average_transaction_price_cents,
        "priceLowCents": snapshot.price_low_cents,
        "priceHighCents": snapshot.price_high_cents,
        "imageContentSha256": snapshot.image_content_sha256,
        "imageUrl": snapshot.image_url,
        "confirmationStatus": snapshot.confirmation_status,
        "confirmedBy": snapshot.confirmed_by,
        "confirmedAt": iso(snapshot.confirmed_at),
        "updatedAt": iso(snapshot.updated_at),
    }


def _master_item(row: MarketRankingEntry, *, context=None) -> dict[str, object]:
    month = row.period_end[:7]
    snapshot = MarketPriceSnapshot.objects.filter(
        category=row.category,
        scope=row.scope,
        sku_code=row.sku_code,
        ranking_dimension=row.ranking_dimension,
        month=month,
    ).first() if context is None else context["snapshots"].get((*_master_identity(row), month))
    cache = (MarketImageCache.objects.filter(source_url=row.image_url).first() if row.image_url else None) if context is None else context["caches"].get(row.image_url)
    suggestion = MarketBrandSuggestion.objects.filter(
        category=row.category,
        scope=row.scope,
        ranking_dimension=row.ranking_dimension,
        sku_code=row.sku_code,
    ).first() if context is None else context["suggestions"].get(_master_identity(row))
    total = MarketSkuGmvTotal.objects.filter(sku_code=row.sku_code).first() if context is None else context["totals"].get(row.sku_code)
    official = (
        snapshot.confirmed_market_price_cents
        if snapshot
        and snapshot.confirmation_status == "confirmed"
        and snapshot.ai_price_type in FORMAL_OFFICIAL_PRICE_TYPES
        and SHA256_RE.fullmatch(snapshot.image_content_sha256)
        else None
    )
    if snapshot and snapshot.confirmation_status == "ai_pending" and snapshot.ai_image_price_cents is not None:
        candidate = snapshot.ai_image_price_cents
        candidate_source = "ai_suggestion"
    elif snapshot and snapshot.source_price_cents is not None:
        candidate = snapshot.source_price_cents
        candidate_source = "source_table"
    elif snapshot and snapshot.average_transaction_price_cents is not None:
        candidate = snapshot.average_transaction_price_cents
        candidate_source = "average_transaction"
    elif snapshot and snapshot.ai_image_price_cents is not None:
        candidate = snapshot.ai_image_price_cents
        candidate_source = "ai_suggestion"
    else:
        candidate = None
        candidate_source = "missing"
    price_band = _price_band(
        int(official) if official is not None else None,
        category=row.category,
        period_end=row.period_end,
        versions=_price_band_versions() if context is None else context["versions"],
    )
    image_hash = cache.content_sha256 if cache and cache.status == "ready" else snapshot.image_content_sha256 if snapshot else ""
    annotation = MarketSkuAnnotation.objects.filter(
        category=row.category,
        scope=row.scope,
        ranking_dimension=row.ranking_dimension,
        sku_code=row.sku_code,
        image_content_sha256=image_hash,
    ).first() if context is None else context["annotations"].get((*_master_identity(row), image_hash))
    return {
        "id": row.id,
        "periodStart": row.period_start,
        "periodEnd": row.period_end,
        "month": month,
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
        "gmvTotalCents": int(total.gmv_total_cents) if total else 0,
        "quantity": int(row.quantity),
        "visitors": int(row.visitors),
        "conversionBps": row.conversion_bps,
        "imageUrl": row.image_url,
        "displayImageUrl": f"/api/market/images/{image_hash}" if image_hash and cache and cache.status == "ready" else row.image_url,
        "productUrl": row.product_url,
        "imageCacheStatus": cache.status if cache else "missing" if not row.image_url else "pending",
        "imageContentSha256": image_hash,
        "officialMarketPriceCents": official,
        "candidatePriceCents": candidate,
        "candidatePriceSource": candidate_source,
        "averageTransactionPriceCents": snapshot.average_transaction_price_cents if snapshot else None,
        "priceLowCents": snapshot.price_low_cents if snapshot else None,
        "priceHighCents": snapshot.price_high_cents if snapshot else None,
        "aiImagePriceCents": snapshot.ai_image_price_cents if snapshot else None,
        "aiPriceType": snapshot.ai_price_type if snapshot else "",
        "aiConfidenceBps": snapshot.ai_confidence_bps if snapshot else None,
        "aiReason": snapshot.ai_reason if snapshot else "",
        "confirmationStatus": snapshot.confirmation_status if snapshot else "missing",
        "suggestedBrand": suggestion.ai_brand if suggestion else "",
        "brandSuggestionStatus": suggestion.status if suggestion else "",
        "annotationStatus": "committed" if annotation else "pending",
        "priceBand": price_band,
    }


def _master_queryset(params: dict[str, object], *, history: bool = False):
    if history:
        query = MarketRankingEntry.objects.all()
    else:
        query = MarketRankingEntry.objects.filter(
            id__in=MarketMasterIdentity.objects.values("latest_entry_id")
        )
    q = _text(params.get("q", ""), "q", 100)
    if q:
        query = query.filter(Q(sku_code__icontains=q) | Q(product_name__icontains=q) | Q(brand__icontains=q))
    mapping = {
        "categories": "category__in",
        "rankingDimensions": "ranking_dimension__in",
        "operationModes": "operation_mode__in",
        "brands": "brand__in",
        "subcategories": "subcategory__in",
    }
    for key, lookup in mapping.items():
        values = _list(params.get(key), key)
        if values:
            query = query.filter(**{lookup: values})
    return query.order_by("-period_end", "rank", "id")


def list_master(params: dict[str, object], *, pending: bool = False) -> dict[str, object]:
    page = int(_integer(params.get("page"), "page", fallback=1, minimum=1, maximum=MAX_PAGE) or 1)
    page_size = int(
        _integer(params.get("pageSize"), "pageSize", fallback=30, minimum=1, maximum=MAX_PAGE_SIZE)
        or 30
    )
    history = pending or bool(params.get("includeHistory"))
    query = _master_queryset(params, history=history)
    candidate_sources = set(_list(params.get("candidatePriceSources"), "candidatePriceSources"))
    price_statuses = set(_list(params.get("priceStatuses"), "priceStatuses"))
    annotation_statuses = set(_list(params.get("annotationStatuses"), "annotationStatuses"))
    ids, pagination = master_page(query, history=history, pending=pending,
        price_statuses=price_statuses, candidate_sources=candidate_sources,
        annotation_statuses=annotation_statuses, page=page, page_size=page_size)
    by_id = MarketRankingEntry.objects.filter(id__in=ids).defer("raw_json").in_bulk()
    if len(by_id) != len(ids):
        raise _error("市场数据版本已变化，请重试读取", code="service_unavailable", status=503)
    rows = [by_id[row_id] for row_id in ids]
    context = page_context(rows, _price_band_versions()) if rows else {}
    return {
        "items": [_master_item(row, context=context) for row in rows],
        "pagination": pagination,
    }


def _options(query, field: str, *, distinct_field: str | None = None) -> list[dict[str, object]]:
    counter = Count(distinct_field, distinct=True) if distinct_field else Count("pk")
    return [
        {"value": row[field], "count": row["count"]}
        for row in query.exclude(**{field: ""}).values(field).annotate(count=counter).order_by("-count", field)[:200]
    ]


def _image_summary() -> dict[str, int]:
    counts = Counter(MarketImageCache.objects.values_list("status", flat=True))
    total = sum(counts.values())
    return {
        "total": total,
        "cached": counts["ready"],
        "failed": counts["failed"],
        "pending": max(0, total - counts["ready"] - counts["failed"]),
    }


def settings_status() -> dict[str, object]:
    span = MarketRankingEntry.objects.aggregate(start=Min("period_start"), end=Max("period_end"))
    return {
        "dataRange": {"startDate": span["start"], "endDate": span["end"]},
        "batches": [batch_payload(item) for item in MarketImportBatch.objects.order_by("-created_at")[:8]],
        "imageCache": _image_summary(),
    }


def _mapping_items() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "category": item.category,
            "sourceValue": item.source_value,
            "targetValue": item.target_value,
            "status": item.status,
            "version": item.version,
            "effectiveFrom": item.effective_from,
            "createdBy": item.created_by,
            "createdAt": iso(item.created_at),
            "updatedAt": iso(item.updated_at),
        }
        for item in MarketMasterMappingRule.objects.order_by("kind", "category", "source_value")[:500]
    ]


def _price_band_items() -> list[dict[str, object]]:
    result = []
    for version in MarketPriceBandVersion.objects.order_by("category", "-version")[:100]:
        result.append(
            {
                "id": version.id,
                "category": version.category,
                "version": version.version,
                "status": version.status,
                "effectiveFrom": version.effective_from,
                "createdBy": version.created_by,
                "createdAt": iso(version.created_at),
                "publishedBy": version.published_by,
                "publishedAt": iso(version.published_at),
                "rolledBackFromId": version.rolled_back_from_id,
                "note": version.note,
                "items": [
                    {
                        "id": item.id,
                        "label": item.label,
                        "minCents": item.min_cents,
                        "maxCents": item.max_cents,
                        "sortOrder": item.sort_order,
                    }
                    for item in MarketPriceBandItem.objects.filter(version_id=version.id).order_by("sort_order")
                ],
            }
        )
    return result


def _brand_job(job: MarketBrandRecognitionJob | None) -> dict[str, object] | None:
    if job is None:
        return None
    total = int(job.total_count)
    processed = min(total, int(job.processed_count))
    return {
        "id": job.id,
        "modelId": job.model_id,
        "query": job.query_text,
        "category": job.category,
        "status": job.status,
        "totalCount": total,
        "processedCount": processed,
        "remainingCount": max(0, total - processed),
        "recognizedCount": int(job.recognized_count),
        "emptyCount": int(job.empty_count),
        "batchSize": int(job.batch_size),
        "progressBps": min(10_000, round(processed * 10_000 / total)) if total else 10_000,
        "createdBy": job.created_by,
        "createdAt": iso(job.created_at),
        "startedAt": iso(job.started_at),
        "updatedAt": iso(job.updated_at),
        "completedAt": iso(job.completed_at),
        "lastError": job.last_error,
    }


def brand_seed_workspace(params: dict[str, object]) -> dict[str, object]:
    q = _text(params.get("q", ""), "q", 100)
    page = int(_integer(params.get("page"), "page", fallback=1, minimum=1, maximum=MAX_PAGE) or 1)
    page_size = int(_integer(params.get("pageSize"), "pageSize", fallback=30, minimum=1, maximum=100) or 30)
    seeds = MarketBrandSeed.objects.all()
    if q:
        seeds = seeds.filter(Q(canonical_brand__icontains=q) | Q(seed_text__icontains=q))
    seed_items = [
        {
            "id": item.id,
            "canonicalBrand": item.canonical_brand,
            "seedText": item.seed_text,
            "source": item.source,
            "sourceRef": item.source_ref,
            "status": item.status,
            "createdBy": item.created_by,
            "createdAt": iso(item.created_at),
            "updatedAt": iso(item.updated_at),
        }
        for item in seeds.order_by("canonical_brand", "seed_text")[:500]
    ]
    unknown_query = MarketRankingEntry.objects.filter(brand="", id__in=MarketMasterIdentity.objects.values("latest_entry_id"))
    category = _text(params.get("category", ""), "category", 120)
    if category:
        unknown_query = unknown_query.filter(category=category)
    if q:
        unknown_query = unknown_query.filter(Q(sku_code__icontains=q) | Q(product_name__icontains=q))
    total = unknown_query.count()
    unknown = [_master_item(row) for row in unknown_query.order_by("-period_end", "id")[(page - 1) * page_size : page * page_size]]
    return {
        "dictionary": {
            "items": seed_items,
            "counts": {
                "total": len(seed_items),
                "enabled": sum(1 for item in seed_items if item["status"] == "enabled"),
                "system": sum(1 for item in seed_items if item["source"] == "system"),
                "manual": sum(1 for item in seed_items if item["source"] == "manual"),
            },
        },
        "unknown": {
            "items": unknown,
            "pagination": {
                "total": total,
                "page": page,
                "pageSize": page_size,
                "pageCount": max(1, math.ceil(total / page_size)),
            },
        },
    }


def subcategory_workspace(category: str) -> dict[str, object]:
    categories = list(MarketRankingEntry.objects.exclude(category="").values_list("category", flat=True).distinct().order_by("category"))
    selected = category or (categories[0] if categories else "")
    return {
        "category": selected,
        "categories": categories,
        "items": [
            {
                "id": item.id,
                "category": item.category,
                "subcategory": item.subcategory,
                "status": item.status,
                "sortOrder": item.sort_order,
                "createdBy": item.created_by,
                "updatedBy": item.updated_by,
                "createdAt": iso(item.created_at),
                "updatedAt": iso(item.updated_at),
            }
            for item in MarketSubcategoryTaxonomy.objects.filter(category=selected).order_by("sort_order", "subcategory")
        ],
    }


def master_workspace(params: dict[str, object]) -> dict[str, object]:
    mode = params.get("section") if params.get("section") in {"database", "brand", "mapping", "subcategory", "data"} else "all"
    master = list_master(params) if mode in {"all", "database", "brand"} else {"items": [], "pagination": {"page": 1, "pageSize": 30, "total": 0, "pageCount": 1}}
    pending_params = {
        **params,
        "categories": params.get("pendingPriceCategories", []),
        "candidatePriceSources": params.get("pendingPriceSources", []),
        "page": params.get("pendingPricePage", 1),
        "pageSize": params.get("pendingPricePageSize", 20),
    }
    pending = list_master(pending_params, pending=True) if mode in {"all", "database"} else {"items": [], "pagination": {"page": 1, "pageSize": 20, "total": 0, "pageCount": 1}}
    category_values = _options(MarketRankingEntry.objects.all(), "category", distinct_field="sku_code")
    selected_categories = _list(params.get("categories"), "categories")
    taxonomy = MarketSubcategoryTaxonomy.objects.filter(status="active")
    if selected_categories:
        taxonomy = taxonomy.filter(category__in=selected_categories)
    sub_counts = Counter(taxonomy.values_list("subcategory", flat=True))
    status_total = MarketPriceSnapshot.objects.count()
    status_confirmed = MarketPriceSnapshot.objects.filter(confirmed_market_price_cents__isnull=False).count()
    current_job = MarketBrandRecognitionJob.objects.filter(
        query_text=_text(params.get("q", ""), "q", 100),
        category=_text(params.get("category", ""), "category", 120),
    ).order_by("-created_at").first()
    coverage = list(
        MarketRankingEntry.objects.values("category", "scope", "ranking_dimension")
        .annotate(month_min=Min("period_end"), month_max=Max("period_end"), month_count=Count("period_end", distinct=True), sku_count=Count("sku_code", distinct=True))
        .order_by("category", "scope", "ranking_dimension")[:200]
    )
    for item in coverage:
        item["month_min"] = str(item["month_min"] or "")[:7]
        item["month_max"] = str(item["month_max"] or "")[:7]
    return {
        "masterData": master,
        "pendingPrices": pending,
        "mappings": {"items": _mapping_items() if mode in {"all", "mapping"} else []},
        "priceBands": {"items": _price_band_items() if mode in {"all", "mapping"} else []},
        "downloadTasks": list(MarketDownloadTask.objects.order_by("-updated_at").values()[:100]) if mode in {"all", "data"} else [],
        "downloadConfigs": list(MarketDownloadConfig.objects.order_by("-updated_at").values()[:100]) if mode in {"all", "data"} else [],
        "coverage": coverage if mode in {"all", "data"} else [],
        "imageCache": _image_summary(),
        "categories": category_values,
        "subcategories": [{"value": key, "count": value} for key, value in sorted(sub_counts.items())],
        "priceRecognition": {
            "prompts": [
                {
                    "category": category,
                    "prompt_id": MarketAnnotationPromptVersion.objects.filter(category=category, status="active").values_list("id", flat=True).first() or "",
                    "pending_count": MarketPriceSnapshot.objects.filter(category=category, confirmed_market_price_cents__isnull=True).count(),
                }
                for category in MarketRankingEntry.objects.exclude(category="").values_list("category", flat=True).distinct().order_by("category")
            ]
        },
        "brandRecognitionJob": _brand_job(current_job) if mode in {"all", "brand"} else None,
        "brandSeeds": brand_seed_workspace(params) if mode in {"all", "brand"} else {"dictionary": {"items": [], "counts": {"total": 0, "enabled": 0, "system": 0, "manual": 0}}, "unknown": {"items": [], "pagination": {"total": 0, "page": 1, "pageCount": 1}}},
        "statusCounts": {"total": status_total, "pendingPrices": status_total - status_confirmed, "confirmedPrices": status_confirmed},
        "subcategorySettings": subcategory_workspace(_text(params.get("category", ""), "category", 120)) if mode in {"all", "subcategory"} else {"category": "", "categories": [], "items": []},
        "audits": list(MarketMasterAuditLog.objects.order_by("-created_at").values()[:100]) if mode in {"all", "data"} else [],
    }


def comparison(params: dict[str, object]) -> dict[str, object]:
    selections = params.get("selections")
    if selections is not None:
        if not isinstance(selections, list) or not 2 <= len(selections) <= 5:
            raise _error("商品对比必须选择 2 到 5 个 SKU")
        normalized: list[dict[str, str]] = []
        for item in selections:
            if not isinstance(item, dict):
                raise _error("商品对比身份无效")
            normalized.append(
                {
                    "skuCode": _text(item.get("skuCode"), "skuCode", 200, required=True),
                    "category": _text(item.get("category"), "category", 200, required=True),
                    "scope": _text(item.get("scope"), "scope", 200, required=True),
                    "rankingDimension": _dimension(item.get("rankingDimension")),
                }
            )
    else:
        codes = _list(params.get("skuCodes"), "skuCodes", 5)
        if not 2 <= len(codes) <= 5:
            raise _error("商品对比必须选择 2 到 5 个 SKU")
        normalized = [{"skuCode": code, "category": "", "scope": "", "rankingDimension": ""} for code in codes]
    items = []
    missing = []
    for identity in normalized:
        rows = MarketRankingEntry.objects.filter(sku_code=identity["skuCode"])
        if identity["category"]:
            rows = rows.filter(category=identity["category"], scope=identity["scope"], ranking_dimension=identity["rankingDimension"])
        latest = rows.order_by("-period_end", "-period_start", "-id").first()
        if latest is None:
            missing.append(identity)
            continue
        aggregates = rows.aggregate(gmv=Sum("gmv_cents"), quantity=Sum("quantity"), visitors=Sum("visitors"), best=Min("rank"))
        trend = item_trend(
            {
                "operation": "trend",
                "skuCode": latest.sku_code,
                "category": latest.category,
                "scope": latest.scope,
                "rankingDimension": latest.ranking_dimension,
            }
        )
        latest_snapshot = MarketPriceSnapshot.objects.filter(
            category=latest.category,
            scope=latest.scope,
            sku_code=latest.sku_code,
            ranking_dimension=latest.ranking_dimension,
            month=latest.period_end[:7],
        ).first()
        official = latest_snapshot.confirmed_market_price_cents if latest_snapshot and latest_snapshot.confirmation_status == "confirmed" else None
        quantity = int(aggregates["quantity"] or 0)
        gmv = int(aggregates["gmv"] or 0)
        items.append(
            {
                "skuCode": latest.sku_code,
                "productName": latest.product_name,
                "brand": latest.brand,
                "category": latest.category,
                "scope": latest.scope,
                "rankingDimension": latest.ranking_dimension,
                "gmvCents": gmv,
                "quantity": quantity,
                "visitors": int(aggregates["visitors"] or 0),
                "conversionBps": latest.conversion_bps,
                "bestRank": aggregates["best"],
                "marketPriceCents": official,
                "averageTransactionPriceCents": official if official is not None else round(gmv / quantity) if quantity else None,
                "trend": trend["items"],
                "trendTotalMonths": trend["totalMonths"],
                "trendTruncated": trend["truncated"],
            }
        )
    return {
        "items": items,
        "missingSkuCodes": [item["skuCode"] for item in missing] if selections is None else [],
        "missingSelections": missing if selections is not None else [],
    }


def execute_master_query(payload: dict[str, object]) -> dict[str, object]:
    if set(payload) != {"operation", "view", "params"} or payload.get("operation") != "master":
        raise _error("市场主数据查询字段无效")
    view = payload.get("view")
    params = payload.get("params")
    if not isinstance(params, dict):
        raise _error("市场主数据查询参数无效")
    if view == "system_kpis":
        return system_kpis()
    if view == "settings_status":
        return settings_status()
    if view == "database_primary":
        return {"masterData": list_master(params)}
    if view == "database_filters":
        categories = _options(MarketRankingEntry.objects.all(), "category", distinct_field="sku_code")
        taxonomy = MarketSubcategoryTaxonomy.objects.filter(status="active")
        selected = _list(params.get("categories"), "categories")
        if selected:
            taxonomy = taxonomy.filter(category__in=selected)
        return {"categories": categories, "subcategories": _options(taxonomy, "subcategory")}
    if view == "database_secondary":
        pending = list_master(params, pending=True)
        total = MarketPriceSnapshot.objects.count()
        confirmed = MarketPriceSnapshot.objects.filter(confirmed_market_price_cents__isnull=False).count()
        return {
            "pendingPrices": pending,
            "imageCache": _image_summary(),
            "priceRecognition": {"prompts": []},
            "statusCounts": {"total": total, "pendingPrices": total - confirmed, "confirmedPrices": confirmed},
        }
    if view == "master":
        return list_master(params)
    if view == "pending_prices":
        return list_master(params, pending=True)
    if view == "compare":
        return comparison(params)
    if view == "brand_job":
        job = MarketBrandRecognitionJob.objects.filter(
            query_text=_text(params.get("q", ""), "q", 100),
            category=_text(params.get("category", ""), "category", 120),
        ).order_by("-created_at").first()
        return _brand_job(job) or {}
    if view == "brand_seeds":
        return brand_seed_workspace(params)
    if view == "subcategories":
        return subcategory_workspace(_text(params.get("category", ""), "category", 120))
    if view == "download_task":
        task_id = _text(params.get("taskId"), "taskId", 128, required=True)
        task = MarketDownloadTask.objects.filter(id=task_id).first()
        if not task:
            raise _error("下载任务不存在", code="not_found", status=404)
        return {
            "id": task.id,
            "category": task.category,
            "scope": task.scope,
            "rankingDimension": task.ranking_dimension,
            "month": task.month,
            "status": task.status,
            "attemptCount": int(task.attempt_count),
        }
    if view == "workspace":
        return master_workspace(params)
    raise _error("不支持的市场主数据视图")


@transaction.atomic
def _confirm_price(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    category = _text(payload.get("category"), "category", 200, required=True)
    scope = _text(payload.get("scope", ""), "scope", 200)
    sku = _text(payload.get("skuCode"), "skuCode", 200, required=True)
    dimension = _dimension(payload.get("rankingDimension"))
    month = _month(payload.get("month"))
    query = MarketPriceSnapshot.objects.select_for_update().filter(
        category=category, sku_code=sku, ranking_dimension=dimension, month=month
    )
    if scope:
        query = query.filter(scope=scope)
    snapshots = list(query[:2])
    if len(snapshots) != 1:
        raise _error("未找到唯一价格快照；多范围身份必须提供 scope", code="version_conflict", status=409)
    snapshot = snapshots[0]
    supplied_hash = _text(payload.get("imageContentSha256", ""), "imageContentSha256", 64)
    if not SHA256_RE.fullmatch(snapshot.image_content_sha256) or supplied_hash != snapshot.image_content_sha256:
        raise _error("图片哈希不匹配，不能跨图片确认价格", code="version_conflict", status=409)
    price_type = _text(payload.get("priceType", snapshot.ai_price_type), "priceType", 40, required=True)
    if price_type not in FORMAL_OFFICIAL_PRICE_TYPES:
        raise _error("正式市场定位价只能使用标准售价、到手价或券后价")
    before = _snapshot_value(snapshot)
    snapshot.confirmed_market_price_cents = _integer(payload.get("priceCents"), "priceCents", minimum=1)
    snapshot.price_low_cents = _integer(payload.get("priceLowCents"), "priceLowCents", nullable=True)
    snapshot.price_high_cents = _integer(payload.get("priceHighCents"), "priceHighCents", nullable=True)
    snapshot.ai_price_type = price_type
    snapshot.confirmation_status = "confirmed"
    snapshot.confirmed_by = principal.email.lower()
    snapshot.confirmed_at = timezone.now()
    snapshot.save()
    after = _snapshot_value(snapshot)
    _audit(principal, "confirm_market_price", "market_price_snapshot", snapshot.id, before, {**after, "note": _text(payload.get("note", ""), "note", 300)})
    bump_revision({"kind": "confirm_price", "snapshotId": snapshot.id, "priceCents": snapshot.confirmed_market_price_cents})
    return {"ok": True, "snapshot": after}


def _confirm_brand(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    category = _text(payload.get("category"), "category", 200, required=True)
    scope = _text(payload.get("scope"), "scope", 200, required=True)
    dimension = _dimension(payload.get("rankingDimension"))
    sku = _text(payload.get("skuCode"), "skuCode", 200, required=True)
    brand = _text(payload.get("brand"), "brand", 200, required=True)
    rows = MarketRankingEntry.objects.filter(category=category, scope=scope, ranking_dimension=dimension, sku_code=sku)
    if not rows.exists():
        raise _error("未找到需要确认品牌的商品", code="not_found", status=404)
    before = {"brands": sorted(set(rows.values_list("brand", flat=True)))}
    rows.update(brand=brand, updated_at=timezone.now())
    source = canonical_json({"category": category, "scope": scope, "rankingDimension": dimension, "skuCode": sku})
    rule = MarketMasterMappingRule.objects.filter(kind="brand_override", category=category, source_value=source).first()
    if rule is None:
        rule = MarketMasterMappingRule.objects.create(
            id=f"market-mapping-{uuid.uuid4()}",
            kind="brand_override",
            category=category,
            source_value=source,
            target_value=brand,
            status="published",
            effective_from=timezone.localdate().isoformat(),
            created_by=principal.email.lower(),
        )
    else:
        rule.target_value = brand
        rule.status = "published"
        rule.version += 1
        rule.save()
    MarketBrandSuggestion.objects.filter(category=category, scope=scope, ranking_dimension=dimension, sku_code=sku).update(
        ai_brand=brand, status="confirmed", confirmed_by=principal.email.lower(), confirmed_at=timezone.now(), updated_at=timezone.now()
    )
    _audit(principal, "confirm_market_brand", "market_ranking_identity", source, before, {"brand": brand, "mappingId": rule.id})
    bump_revision({"kind": "confirm_brand", "identity": source, "brand": brand})
    return {"ok": True, "updatedRows": rows.count(), "brand": brand, "mappingId": rule.id}


def _upsert_mapping(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    kind = _text(payload.get("kind"), "kind", 32, required=True)
    status = _text(payload.get("status", "draft"), "status", 32, required=True)
    if kind not in VALID_MAPPING_KINDS or status not in VALID_MAPPING_STATUSES:
        raise _error("映射规则类型或状态无效")
    identifier = _text(payload.get("id", ""), "id", 128) or f"market-mapping-{uuid.uuid4()}"
    before = MarketMasterMappingRule.objects.filter(id=identifier).values().first() or {}
    rule, _ = MarketMasterMappingRule.objects.update_or_create(
        id=identifier,
        defaults={
            "kind": kind,
            "category": _text(payload.get("category", ""), "category", 200),
            "source_value": _text(payload.get("sourceValue"), "sourceValue", 500, required=True),
            "target_value": _text(payload.get("targetValue"), "targetValue", 500, required=True),
            "status": status,
            "effective_from": _date(payload.get("effectiveFrom", timezone.localdate().isoformat()), "effectiveFrom"),
            "created_by": principal.email.lower(),
            "version": int(before.get("version", 0)) + 1,
        },
    )
    after = MarketMasterMappingRule.objects.filter(id=rule.id).values().get()
    _audit(principal, "upsert_market_mapping", "market_master_mapping_rule", rule.id, before, after)
    bump_revision({"kind": "mapping", "id": rule.id, "version": rule.version})
    return after


def _apply_mappings(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    category = _text(payload.get("category", ""), "category", 200)
    rules = MarketMasterMappingRule.objects.filter(status="published", effective_from__lte=timezone.localdate().isoformat())
    if category:
        rules = rules.filter(Q(category="") | Q(category=category))
    changed = 0
    with transaction.atomic():
        for rule in rules.order_by("kind", "version"):
            rows = MarketRankingEntry.objects.all()
            if rule.category:
                rows = rows.filter(category=rule.category)
            if rule.kind == "subcategory":
                changed += rows.filter(subcategory=rule.source_value).update(subcategory=rule.target_value, updated_at=timezone.now())
            elif rule.kind == "brand_alias":
                changed += rows.filter(brand=rule.source_value).update(brand=rule.target_value, updated_at=timezone.now())
            elif rule.kind == "operation_mode":
                changed += rows.filter(operation_mode=rule.source_value).update(operation_mode=rule.target_value, updated_at=timezone.now())
            elif rule.kind == "brand_override":
                try:
                    identity = __import__("json").loads(rule.source_value)
                except Exception:
                    continue
                if not isinstance(identity, dict):
                    continue
                changed += rows.filter(
                    category=identity.get("category"), scope=identity.get("scope"), ranking_dimension=identity.get("rankingDimension"), sku_code=identity.get("skuCode")
                ).update(brand=rule.target_value, updated_at=timezone.now())
        _audit(principal, "apply_market_mappings", "market_ranking_entries", category or "*", {}, {"changed": changed})
        bump_revision({"kind": "apply_mappings", "category": category, "changed": changed})
    return {"ok": True, "changed": changed}


def _price_band_command(action: str, payload: dict[str, object], principal: Principal) -> dict[str, object]:
    if action == "create_price_band_version":
        category = _text(payload.get("category", "*"), "category", 200, required=True)
        items = payload.get("items")
        if not isinstance(items, list) or not items or len(items) > 50:
            raise _error("价格带必须包含 1 到 50 个区间")
        normalized = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise _error("价格带区间无效")
            minimum = _integer(item.get("minCents"), "minCents", nullable=True)
            maximum = _integer(item.get("maxCents"), "maxCents", nullable=True)
            if minimum is not None and maximum is not None and minimum >= maximum:
                raise _error("价格带下限必须小于上限")
            normalized.append((_text(item.get("label"), "label", 100, required=True), minimum, maximum, index))
        version = (MarketPriceBandVersion.objects.filter(category=category).aggregate(value=Max("version"))["value"] or 0) + 1
        identifier = f"market-price-band-{uuid.uuid4()}"
        with transaction.atomic():
            saved = MarketPriceBandVersion.objects.create(
                id=identifier,
                category=category,
                version=version,
                effective_from=_date(payload.get("effectiveFrom"), "effectiveFrom"),
                note=_text(payload.get("note", ""), "note", 500),
                created_by=principal.email.lower(),
            )
            MarketPriceBandItem.objects.bulk_create(
                [MarketPriceBandItem(id=f"market-price-band-item-{uuid.uuid4()}", version_id=identifier, label=label, min_cents=minimum, max_cents=maximum, sort_order=index) for label, minimum, maximum, index in normalized]
            )
            _audit(principal, "create_market_price_band_version", "market_price_band_version", identifier, {}, {"category": category, "version": version})
        return next(item for item in _price_band_items() if item["id"] == saved.id)
    if action == "publish_price_band_version":
        identifier = _text(payload.get("id"), "id", 128, required=True)
        with transaction.atomic():
            version = MarketPriceBandVersion.objects.select_for_update().filter(id=identifier).first()
            if version is None or version.status not in {"draft", "archived"}:
                raise _error("待发布价格带版本不存在或状态无效", code="version_conflict", status=409)
            MarketPriceBandVersion.objects.filter(category=version.category, status="published").update(status="archived")
            version.status = "published"
            version.published_by = principal.email.lower()
            version.published_at = timezone.now()
            version.save()
            _audit(principal, "publish_market_price_band_version", "market_price_band_version", identifier, {}, {"status": "published"})
            bump_revision({"kind": "price_band_publish", "id": identifier})
        return next(item for item in _price_band_items() if item["id"] == identifier)
    target_id = _text(payload.get("targetVersionId"), "targetVersionId", 128, required=True)
    target = MarketPriceBandVersion.objects.filter(id=target_id).first()
    if target is None:
        raise _error("回滚目标价格带不存在", code="not_found", status=404)
    with transaction.atomic():
        MarketPriceBandVersion.objects.filter(category=target.category, status="published").update(status="archived")
        version_number = (MarketPriceBandVersion.objects.filter(category=target.category).aggregate(value=Max("version"))["value"] or 0) + 1
        replacement = MarketPriceBandVersion.objects.create(
            id=f"market-price-band-{uuid.uuid4()}",
            category=target.category,
            version=version_number,
            status="published",
            effective_from=timezone.localdate().isoformat(),
            created_by=principal.email.lower(),
            published_by=principal.email.lower(),
            published_at=timezone.now(),
            rolled_back_from_id=target.id,
            note=f"rollback:{target.id}",
        )
        MarketPriceBandItem.objects.bulk_create(
            [MarketPriceBandItem(id=f"market-price-band-item-{uuid.uuid4()}", version_id=replacement.id, label=item.label, min_cents=item.min_cents, max_cents=item.max_cents, sort_order=item.sort_order) for item in MarketPriceBandItem.objects.filter(version_id=target.id)]
        )
        _audit(principal, "rollback_market_price_band_version", "market_price_band_version", replacement.id, {}, {"targetVersionId": target.id})
        bump_revision({"kind": "price_band_rollback", "id": replacement.id, "target": target.id})
    return next(item for item in _price_band_items() if item["id"] == replacement.id)


def _save_subcategories(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    category = _text(payload.get("category"), "category", 200, required=True)
    additions = _list(payload.get("additions"), "additions", 200)
    renames = payload.get("renames", [])
    if not isinstance(renames, list) or len(renames) > 200:
        raise _error("renames 参数无效")
    with transaction.atomic():
        for item in renames:
            if not isinstance(item, dict):
                raise _error("renames 参数无效")
            source = _text(item.get("source"), "source", 200, required=True)
            target = _text(item.get("target"), "target", 200, required=True)
            MarketRankingEntry.objects.filter(category=category, subcategory=source).update(subcategory=target, updated_at=timezone.now())
            MarketSkuAnnotation.objects.filter(category=category, segment=source).update(segment=target, updated_at=timezone.now())
            taxonomy = MarketSubcategoryTaxonomy.objects.filter(category=category, subcategory=source).first()
            if taxonomy:
                taxonomy.subcategory = target
                taxonomy.updated_by = principal.email.lower()
                taxonomy.save()
        for index, subcategory in enumerate(additions):
            digest = hashlib.sha256(canonical_json([category, subcategory]).encode()).hexdigest()
            taxonomy, created = MarketSubcategoryTaxonomy.objects.get_or_create(
                category=category,
                subcategory=subcategory,
                defaults={"id": f"market-subcategory-v2-{digest}", "status": "active", "sort_order": index, "created_by": principal.email.lower(), "updated_by": principal.email.lower()},
            )
            if not created:
                taxonomy.status = "active"
                taxonomy.sort_order = index
                taxonomy.updated_by = principal.email.lower()
                taxonomy.save(update_fields=["status", "sort_order", "updated_by", "updated_at"])
        _audit(principal, "save_market_subcategory_settings", "market_subcategory_taxonomy", category, {}, {"renames": renames, "additions": additions})
        bump_revision({"kind": "subcategory_settings", "category": category})
    return subcategory_workspace(category)


def _market_natural_key(row: MarketRankingEntry, category: str) -> str:
    parts = [
        row.period_start,
        row.period_end,
        category,
        row.scope,
        row.price_band_filter,
        row.ranking_dimension,
        row.sku_code,
    ]
    return "market-key-v2|" + "|".join(
        f"{len(value.encode('utf-8'))}:{value}" for value in parts
    )


def _refresh_master_identity(category: str, scope: str, dimension: str, sku_code: str) -> None:
    latest = MarketRankingEntry.objects.filter(
        category=category,
        scope=scope,
        ranking_dimension=dimension,
        sku_code=sku_code,
    ).order_by("-period_end", "-period_start", "-id").first()
    query = MarketMasterIdentity.objects.filter(
        category=category,
        scope=scope,
        ranking_dimension=dimension,
        sku_code=sku_code,
    )
    if latest is None:
        query.delete()
    else:
        MarketMasterIdentity.objects.update_or_create(
            category=category,
            scope=scope,
            ranking_dimension=dimension,
            sku_code=sku_code,
            defaults={"latest_entry_id": latest.id},
        )


@transaction.atomic
def _update_sku_master(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    original = _text(payload.get("originalCategory"), "originalCategory", 200, required=True)
    category = _text(payload.get("category"), "category", 200, required=True)
    scope = _text(payload.get("scope"), "scope", 200, required=True)
    dimension = _dimension(payload.get("rankingDimension"))
    sku_code = _text(payload.get("skuCode"), "skuCode", 200, required=True)
    month = _month(payload.get("month"))
    product_name = _text(payload.get("productName"), "productName", 1_000, required=True)
    brand = _text(payload.get("brand", ""), "brand", 300)
    operation_mode = _text(payload.get("operationMode"), "operationMode", 16, required=True)
    if operation_mode not in {"POP", "自营", "未知"}:
        raise _error("operationMode 无效")
    subcategory = _text(payload.get("subcategory", ""), "subcategory", 200)
    if subcategory and not MarketSubcategoryTaxonomy.objects.filter(
        category=category, subcategory=subcategory, status="active"
    ).exists():
        raise _error("细分品类不在当前三级类目的细分品类设置中", code="version_conflict", status=409)
    price = _integer(payload.get("priceCents"), "priceCents", nullable=True, minimum=0, maximum=100_000_000)
    price_type = _text(payload.get("priceType", "标准售价"), "priceType", 40, required=True)
    if price is not None and price_type not in FORMAL_OFFICIAL_PRICE_TYPES:
        raise _error("正式市场定位价只能使用标准售价、到手价或券后价")
    if price is not None and price_type not in VALID_PRICE_TYPES:
        raise _error("确认价格必须选择有效的完整售价类型")
    rows = list(MarketRankingEntry.objects.select_for_update().filter(
        category=original,
        scope=scope,
        ranking_dimension=dimension,
        sku_code=sku_code,
    ))
    if not rows:
        raise _error("未找到要编辑的 SKU 主数据", code="not_found", status=404)
    before = _master_item(max(rows, key=lambda row: (row.period_end, row.period_start, row.id)))
    if category != original:
        if MarketRankingEntry.objects.filter(category=original, sku_code=sku_code).exclude(
            scope=scope, ranking_dimension=dimension
        ).exists():
            raise _error("同一 SKU 在原三级类目仍有其他范围或榜单维度，不能只迁移其中一个身份", code="version_conflict", status=409)
        if MarketRankingEntry.objects.filter(
            category=category, scope=scope, ranking_dimension=dimension, sku_code=sku_code
        ).exists():
            raise _error("目标三级类目已经存在同一 SKU", code="version_conflict", status=409)
        if MarketAnnotationItem.objects.filter(
            category=original,
            scope=scope,
            ranking_dimension=dimension,
            sku_code=sku_code,
            status__in=["queued", "claimed", "inferencing", "failed", "review_pending", "approved", "rejected"],
            job_id__in=MarketAnnotationJob.objects.exclude(status__in=["cancelled", "committed", "deleted"]).values("id"),
        ).exists():
            raise _error("该 SKU 仍有未完成的 AI 标注候选", code="version_conflict", status=409)
        if MarketSkuAnnotation.objects.filter(
            category=category, scope=scope, ranking_dimension=dimension, sku_code=sku_code
        ).exists() or MarketPriceSnapshot.objects.filter(
            category=category, scope=scope, ranking_dimension=dimension, sku_code=sku_code
        ).exists():
            raise _error("目标三级类目已有价格或标注身份", code="version_conflict", status=409)
    snapshot = MarketPriceSnapshot.objects.select_for_update().filter(
        category=original,
        scope=scope,
        ranking_dimension=dimension,
        sku_code=sku_code,
        month=month,
    ).first()
    if snapshot is None:
        raise _error("未找到当前价格快照", code="version_conflict", status=409)
    supplied_hash = _text(payload.get("imageContentSha256", ""), "imageContentSha256", 64)
    if not SHA256_RE.fullmatch(snapshot.image_content_sha256) or supplied_hash != snapshot.image_content_sha256:
        raise _error("商品图片已变化，请刷新后重新确认价格", code="version_conflict", status=409)
    for row in rows:
        row.category = category
        row.product_name = product_name
        row.brand = brand
        row.operation_mode = operation_mode
        row.subcategory = subcategory
        row.natural_key = _market_natural_key(row, category)
        row.save(update_fields=[
            "category", "product_name", "brand", "operation_mode", "subcategory",
            "natural_key", "updated_at",
        ])
    MarketPriceSnapshot.objects.filter(
        category=original, scope=scope, ranking_dimension=dimension, sku_code=sku_code
    ).update(category=category, updated_at=timezone.now())
    MarketSkuAnnotation.objects.filter(
        category=original, scope=scope, ranking_dimension=dimension, sku_code=sku_code
    ).update(category=category, segment=subcategory, updated_at=timezone.now())
    MarketBrandSuggestion.objects.filter(
        category=original, scope=scope, ranking_dimension=dimension, sku_code=sku_code
    ).update(category=category, product_name=product_name, current_brand=brand, updated_at=timezone.now())
    snapshot.refresh_from_db()
    snapshot.confirmed_market_price_cents = price
    snapshot.ai_price_type = price_type if price is not None else ""
    snapshot.confirmation_status = "confirmed" if price is not None else "missing"
    snapshot.confirmed_by = principal.email.lower()
    snapshot.confirmed_at = timezone.now()
    snapshot.save()
    _refresh_master_identity(original, scope, dimension, sku_code)
    _refresh_master_identity(category, scope, dimension, sku_code)
    after_row = MarketRankingEntry.objects.filter(
        category=category, scope=scope, ranking_dimension=dimension, sku_code=sku_code
    ).order_by("-period_end", "-period_start", "-id").first()
    after = _master_item(after_row) if after_row else {}
    _audit(principal, "update_market_sku_master", "market_sku", f"{original}|{scope}|{dimension}|{sku_code}", before, after)
    bump_revision({"kind": "update_sku_master", "identity": [category, scope, dimension, sku_code], "month": month})
    return {"ok": True, "changedRows": len(rows), "item": after}


def _months_between(start: str, end: str) -> list[str]:
    cursor = date.fromisoformat(f"{start}-01")
    finish = date.fromisoformat(f"{end}-01")
    result: list[str] = []
    while cursor <= finish:
        result.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (1 if cursor.month == 12 else 0), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return result


@transaction.atomic
def _plan_downloads(payload: dict[str, object], principal: Principal) -> dict[str, int]:
    category = _text(payload.get("category", ""), "category", 200)
    scope = _text(payload.get("scope", ""), "scope", 200)
    dimension = _text(payload.get("rankingDimension", ""), "rankingDimension", 8)
    configs = MarketDownloadConfig.objects.filter(status="enabled")
    if category:
        configs = configs.filter(category=category)
    if scope:
        configs = configs.filter(scope=scope)
    if dimension:
        configs = configs.filter(ranking_dimension=_dimension(dimension))
    created = 0
    reused = 0
    for config in configs.order_by("category", "scope", "ranking_dimension", "month_start"):
        for month in _months_between(config.month_start, config.month_end):
            if MarketDownloadTask.objects.filter(
                category=config.category,
                scope=config.scope,
                ranking_dimension=config.ranking_dimension,
                month=month,
                status__in=["imported", "published"],
                header_valid=True,
                period_valid=True,
                category_valid=True,
                dimension_valid=True,
            ).exclude(import_batch_id="").exists():
                continue
            task, was_created = MarketDownloadTask.objects.get_or_create(
                category=config.category,
                scope=config.scope,
                ranking_dimension=config.ranking_dimension,
                month=month,
                defaults={"id": f"market-download-{uuid.uuid4()}", "status": "planned"},
            )
            if was_created:
                created += 1
            else:
                if task.status in {"failed", "planned", "waiting_login"} and task.attempt_count < 3:
                    task.status = "planned"
                    task.next_retry_at = None
                    task.save(update_fields=["status", "next_retry_at", "updated_at"])
                reused += 1
    _audit(principal, "plan_missing_downloads", "market_download_task", "*", {}, {"created": created, "reused": reused})
    return {"created": created, "reused": reused}


def execute_master_command(payload: dict[str, object], principal: Principal) -> object:
    action = _text(payload.get("action"), "action", 64, required=True)
    if action == "record_import_rejection":
        if set(payload) != {
            "action",
            "sourceType",
            "fileName",
            "fileSizeBytes",
            "rawFileHash",
            "errorCode",
            "errorMessage",
        }:
            raise _error("市场导入拒绝审计字段集合无效")
        raw_hash = _text(payload.get("rawFileHash"), "rawFileHash", 64, required=True).lower()
        if not SHA256_RE.fullmatch(raw_hash):
            raise _error("rawFileHash 必须是 SHA-256")
        attempt = MarketImportAttempt.objects.create(
            raw_file_hash=raw_hash,
            outcome="rejected",
            error_code=_text(payload.get("errorCode"), "errorCode", 64, required=True),
            actor_email=principal.email.lower(),
            metadata={
                "sourceType": _text(payload.get("sourceType"), "sourceType", 64, required=True),
                "fileName": _text(payload.get("fileName"), "fileName", 1_000, required=True),
                "fileSizeBytes": _integer(
                    payload.get("fileSizeBytes"),
                    "fileSizeBytes",
                    minimum=1,
                    maximum=25 * 1024 * 1024,
                ),
                "errorMessage": _text(payload.get("errorMessage"), "errorMessage", 500),
                "stage": "edge_prevalidation",
            },
            completed_at=timezone.now(),
        )
        return {"attemptId": str(attempt.id), "outcome": attempt.outcome}
    if action == "confirm_price":
        return _confirm_price(payload, principal)
    if action == "update_sku_master":
        return _update_sku_master(payload, principal)
    if action == "confirm_brand":
        return _confirm_brand(payload, principal)
    if action == "confirm_brand_suggestions_batch":
        batch_size = int(_integer(payload.get("batchSize"), "batchSize", fallback=25, minimum=1, maximum=50) or 25)
        suggestions = MarketBrandSuggestion.objects.filter(status="ai_pending").exclude(ai_brand="")
        category = _text(payload.get("category", ""), "category", 200)
        query = _text(payload.get("q", ""), "q", 100)
        if category:
            suggestions = suggestions.filter(category=category)
        if query:
            suggestions = suggestions.filter(
                Q(sku_code__icontains=query)
                | Q(product_name__icontains=query)
                | Q(current_brand__icontains=query)
                | Q(ai_brand__icontains=query)
            )
        confirmed = 0
        for suggestion in suggestions.order_by("updated_at", "id")[:batch_size]:
            _confirm_brand(
                {
                    "category": suggestion.category,
                    "scope": suggestion.scope,
                    "rankingDimension": suggestion.ranking_dimension,
                    "skuCode": suggestion.sku_code,
                    "brand": suggestion.ai_brand,
                },
                principal,
            )
            confirmed += 1
        return {"confirmed": confirmed, "done": confirmed < batch_size}
    if action == "save_subcategory_settings":
        return _save_subcategories(payload, principal)
    if action == "upsert_mapping":
        return _upsert_mapping(payload, principal)
    if action == "apply_mappings":
        return _apply_mappings(payload, principal)
    if action in {"create_price_band_version", "publish_price_band_version", "rollback_price_band_version"}:
        return _price_band_command(action, payload, principal)
    if action == "upsert_brand_seed":
        canonical = _text(payload.get("canonicalBrand"), "canonicalBrand", 200, required=True)
        seed = _text(payload.get("seedText"), "seedText", 200, required=True)
        normalized = re.sub(r"\s+", "", seed).casefold()
        identifier = f"market-brand-seed-{hashlib.sha256(normalized.encode()).hexdigest()[:32]}"
        before = MarketBrandSeed.objects.filter(normalized_seed=normalized).values().first() or {}
        saved, created = MarketBrandSeed.objects.get_or_create(
            normalized_seed=normalized,
            defaults={"id": identifier, "canonical_brand": canonical, "seed_text": seed, "source": "manual", "status": "enabled", "created_by": principal.email.lower(), "last_refreshed_at": timezone.now()},
        )
        if not created:
            saved.canonical_brand = canonical
            saved.seed_text = seed
            saved.source = "manual"
            saved.status = "enabled"
            saved.last_refreshed_at = timezone.now()
            saved.save()
        _audit(principal, "upsert_market_brand_seed", "market_brand_seed", saved.id, before, {"canonicalBrand": canonical, "seedText": seed})
        bump_revision({"kind": "brand_seed", "id": saved.id})
        return {"seed": {"id": saved.id, "canonicalBrand": canonical, "seedText": seed}, "appliedRows": 0}
    if action == "match_brand_seeds":
        category = _text(payload.get("category", ""), "category", 200)
        changed = 0
        seeds = list(MarketBrandSeed.objects.filter(status="enabled").order_by("-updated_at"))
        rows = MarketRankingEntry.objects.filter(brand="")
        if category:
            rows = rows.filter(category=category)
        for row in rows.iterator(chunk_size=500):
            normalized_title = re.sub(r"\s+", "", row.product_name).casefold()
            match = next((seed for seed in seeds if seed.normalized_seed and seed.normalized_seed in normalized_title), None)
            if match:
                changed += MarketRankingEntry.objects.filter(category=row.category, scope=row.scope, ranking_dimension=row.ranking_dimension, sku_code=row.sku_code).update(brand=match.canonical_brand, updated_at=timezone.now())
        _audit(principal, "match_market_brand_seeds", "market_ranking_entries", category or "*", {}, {"changed": changed})
        if changed:
            bump_revision({"kind": "match_brand_seeds", "category": category, "changed": changed})
        return {"matched": changed}
    if action == "refresh_brand_seeds":
        discovered = 0
        for brand in MarketRankingEntry.objects.exclude(brand="").values_list("brand", flat=True).distinct():
            normalized = re.sub(r"\s+", "", brand).casefold()
            saved, created = MarketBrandSeed.objects.get_or_create(
                normalized_seed=normalized,
                defaults={"id": f"market-brand-seed-{hashlib.sha256(normalized.encode()).hexdigest()[:32]}", "canonical_brand": brand, "seed_text": brand, "source": "system", "source_ref": "market-ranking", "status": "enabled", "created_by": principal.email.lower(), "last_refreshed_at": timezone.now()},
            )
            if not created and saved.source == "system":
                saved.canonical_brand = brand
                saved.seed_text = brand
                saved.status = "enabled"
                saved.last_refreshed_at = timezone.now()
                saved.save()
            discovered += int(created)
        return {"discovered": discovered, "inserted": discovered, "refreshed": 0, "disabled": 0, "manualPreserved": 0}
    if action == "create_brand_recognition_job":
        model_id = _text(payload.get("modelId"), "modelId", 128, required=True)
        query = _text(payload.get("q", ""), "q", 100)
        category = _text(payload.get("category", ""), "category", 200)
        batch_size = int(_integer(payload.get("batchSize"), "batchSize", fallback=40, minimum=1, maximum=50) or 40)
        candidates = MarketRankingEntry.objects.filter(id__in=MarketMasterIdentity.objects.values("latest_entry_id"))
        if category:
            candidates = candidates.filter(category=category)
        if query:
            candidates = candidates.filter(Q(product_name__icontains=query) | Q(sku_code__icontains=query) | Q(brand__icontains=query))
        existing = MarketBrandRecognitionJob.objects.filter(query_text=query, category=category, status__in=["queued", "running", "paused", "failed"]).order_by("-created_at").first()
        if existing:
            return {**(_brand_job(existing) or {}), "reused": True}
        total = candidates.count()
        job = MarketBrandRecognitionJob.objects.create(id=f"market-brand-job-{uuid.uuid4()}", model_id=model_id, query_text=query, category=category, status="queued" if total else "completed", total_count=total, batch_size=batch_size, created_by=principal.email.lower(), completed_at=timezone.now() if not total else None)
        return {**(_brand_job(job) or {}), "reused": False}
    if action in {"pause_brand_recognition_job", "resume_brand_recognition_job"}:
        job = MarketBrandRecognitionJob.objects.filter(id=_text(payload.get("jobId"), "jobId", 128, required=True)).first()
        if not job or job.status == "completed":
            raise _error("品牌识别任务已经完成或不存在", code="version_conflict", status=409)
        job.status = "paused" if action.startswith("pause") else "queued"
        job.last_error = ""
        job.save()
        return _brand_job(job)
    if action == "upsert_download_config":
        category = _text(payload.get("category"), "category", 200, required=True)
        scope = _text(payload.get("scope", "全部"), "scope", 200, required=True)
        dimension = _dimension(payload.get("rankingDimension"))
        month_start = _month(payload.get("monthStart"))
        month_end = _month(payload.get("monthEnd"))
        if month_start > month_end:
            raise _error("下载配置月份范围无效")
        identifier = f"market-download-config-{hashlib.sha256(canonical_json([category, scope, dimension, month_start, month_end]).encode()).hexdigest()[:32]}"
        config, _ = MarketDownloadConfig.objects.update_or_create(id=identifier, defaults={"category": category, "scope": scope, "ranking_dimension": dimension, "month_start": month_start, "month_end": month_end, "status": _text(payload.get("status", "enabled"), "status", 32), "created_by": principal.email.lower()})
        return {"id": config.id, "category": category, "scope": scope, "rankingDimension": dimension, "monthStart": month_start, "monthEnd": month_end, "status": config.status}
    if action == "plan_downloads":
        return _plan_downloads(payload, principal)
    if action == "record_download_attempt":
        task = MarketDownloadTask.objects.filter(id=_text(payload.get("taskId"), "taskId", 128, required=True)).first()
        if not task:
            raise _error("下载任务不存在", code="not_found", status=404)
        requested_status = _text(payload.get("status"), "status", 32, required=True)
        if requested_status not in {"failed", "waiting_login"}:
            raise _error("客户端只能记录等待登录或失败状态")
        task.status = requested_status
        task.error_code = _text(payload.get("errorCode", ""), "errorCode", 64)
        task.error_message = _text(payload.get("errorMessage", ""), "errorMessage", 500)
        task.attempt_count += 1
        task.last_attempt_at = timezone.now()
        if task.status == "completed":
            task.completed_at = timezone.now()
        task.save()
        return {"id": task.id, "status": task.status, "attemptCount": task.attempt_count}
    if action == "complete_download_task":
        task = MarketDownloadTask.objects.select_for_update().filter(
            id=_text(payload.get("taskId"), "taskId", 128, required=True)
        ).first()
        if not task:
            raise _error("下载任务不存在", code="not_found", status=404)
        batch_id = _text(payload.get("batchId"), "batchId", 128, required=True)
        batch = MarketImportBatch.objects.filter(id=batch_id, status="completed").first()
        if not batch:
            raise _error("导入批次未完成", code="version_conflict", status=409)
        ranges = batch.scope_json.get("ranges") if isinstance(batch.scope_json, dict) else None
        expected_month = task.month
        if not isinstance(ranges, list) or not ranges or any(
            not isinstance(item, dict)
            or item.get("category") != task.category
            or item.get("scope") != task.scope
            or item.get("rankingDimension") != task.ranking_dimension
            or not str(item.get("periodStart", "")).startswith(expected_month)
            or not str(item.get("periodEnd", "")).startswith(expected_month)
            for item in ranges
        ):
            raise _error("导入批次与下载任务身份或月份不一致", code="version_conflict", status=409)
        raw_hash = _text(payload.get("rawFileHash"), "rawFileHash", 64, required=True)
        content_hash = _text(payload.get("contentHash"), "contentHash", 64, required=True)
        row_count = _integer(payload.get("rowCount"), "rowCount", minimum=1)
        if raw_hash != batch.raw_file_hash or content_hash != batch.content_hash or row_count != batch.row_count:
            raise _error("导入批次回执与文件证据不一致", code="version_conflict", status=409)
        task.status = "imported"
        task.jd_task_id = _text(payload.get("jdTaskId", ""), "jdTaskId", 160)
        task.source_file_name = _text(payload.get("fileName"), "fileName", 1_000, required=True)
        task.file_hash = raw_hash
        task.row_count = row_count or 0
        task.header_valid = True
        task.period_valid = True
        task.category_valid = True
        task.dimension_valid = True
        task.import_batch_id = batch.id
        task.validation_json = {
            "contentHash": content_hash,
            "scope": batch.scope_json,
            "warningCount": batch.warning_count,
        }
        task.attempt_count += 1
        task.last_attempt_at = timezone.now()
        task.completed_at = timezone.now()
        task.error_code = ""
        task.error_message = ""
        task.save()
        _audit(principal, "complete_market_download_task", "market_download_task", task.id, {}, {"batchId": batch.id, "rowCount": row_count})
        return {"id": task.id, "status": task.status, "importBatchId": batch.id, "rowCount": row_count}
    if action == "compare":
        return comparison(payload)
    # Provider-dependent actions are deliberately split into claim/complete
    # commands in the Django writer.  The edge may call the model, but it never
    # owns market state or a database write after cutover.
    if action in {"infer_brand", "recognize_brand_batch", "run_brand_recognition_job_batch", "create_price_recognition_job", "run_price_recognition_next", "run_price_recognition_batch"}:
        raise _error("该 AI 操作必须使用市场任务 claim/complete 契约", code="version_conflict", status=409)
    raise _error("不支持的市场主数据操作")
