from __future__ import annotations

import hashlib
import math
import secrets
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from sales.auth import Principal

from .errors import MarketApiError
from .models import (
    MarketImageCache,
    MarketImageCacheClaim,
    MarketImageCacheJob,
    MarketImageCacheJobItem,
    MarketMasterIdentity,
    MarketMasterAuditLog,
    MarketPriceSnapshot,
    MarketRankingEntry,
)
from .revisions import bump_revision, iso


MAX_PAGE = 10_000
MAX_PAGE_SIZE = 200
MAX_CLAIMS = 16
HEX64 = frozenset("0123456789abcdef")


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


def _integer(value: object, label: str, fallback: int, minimum: int, maximum: int) -> int:
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise _error(f"{label} 参数无效")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit(principal: Principal, action: str, entity_id: str, before: object, after: object) -> None:
    MarketMasterAuditLog.objects.create(
        actor_email=principal.email.lower(),
        actor_role=principal.role,
        action=action,
        entity_type="market_image_cache",
        entity_id=entity_id,
        before_json=before if isinstance(before, (dict, list)) else {"value": before},
        after_json=after if isinstance(after, (dict, list)) else {"value": after},
    )


def _job_value(job: MarketImageCacheJob) -> dict[str, object]:
    counts = {
        row["status"]: row["count"]
        for row in MarketImageCacheJobItem.objects.filter(job_id=job.id)
        .values("status")
        .annotate(count=Count("id"))
    }
    total = sum(int(value) for value in counts.values())
    cached = int(counts.get("ready", 0))
    failed = int(counts.get("failed", 0))
    pending = max(0, total - cached - failed)
    return {
        "id": job.id,
        "batchId": job.batch_id,
        "status": job.status,
        "total": total,
        "discoveredCount": int(job.discovered_count),
        "discoveryComplete": bool(job.discovery_complete),
        "cached": cached,
        "failed": failed,
        "pending": pending,
        "propagationPending": int(job.propagation_pending_count),
        "processedCount": cached + failed,
        "runCount": int(job.run_count),
        "errorMessage": job.error_message,
        "createdAt": iso(job.created_at),
        "updatedAt": iso(job.updated_at),
        "completedAt": iso(job.completed_at),
    }


def get_job(params: dict[str, object]) -> dict[str, object]:
    job_id = _text(params.get("jobId", ""), "jobId", 128)
    batch_id = _text(params.get("batchId", ""), "batchId", 128)
    query = MarketImageCacheJob.objects.all()
    if job_id:
        query = query.filter(id=job_id)
    elif batch_id:
        query = query.filter(scope_key=f"batch:{batch_id}")
    else:
        query = query.filter(scope_key="global")
    job = query.first()
    if not job:
        raise _error("图片缓存任务不存在", code="not_found", status=404)
    return {"ok": True, "job": _job_value(job)}


def image_metadata(content_hash: str) -> dict[str, object]:
    normalized = content_hash.lower()
    if len(normalized) != 64 or any(char not in HEX64 for char in normalized):
        raise _error("图片内容哈希无效")
    rows = list(
        MarketImageCache.objects.filter(status="ready", content_sha256=normalized)
        .exclude(object_key="")
        .order_by("object_key")[:2]
    )
    if not rows:
        raise _error("图片缓存不存在", code="not_found", status=404)
    if len({row.object_key for row in rows}) != 1:
        raise _error("图片内容哈希对应多个对象", code="version_conflict", status=409)
    row = rows[0]
    return {
        "contentSha256": normalized,
        "objectKey": row.object_key,
        "mimeType": row.mime_type,
        "sizeBytes": int(row.size_bytes),
    }


def repair_candidates(params: dict[str, object]) -> dict[str, object]:
    page = _integer(params.get("page"), "page", 1, 1, MAX_PAGE)
    page_size = _integer(params.get("pageSize"), "pageSize", 100, 1, MAX_PAGE_SIZE)
    query = MarketRankingEntry.objects.filter(
        id__in=MarketMasterIdentity.objects.values("latest_entry_id"), image_url=""
    ).order_by("category", "scope", "ranking_dimension", "sku_code", "-period_end", "-id")
    # Keep one latest row per complete market identity.  A SKU code by itself is
    # never sufficient because it can legally exist in another scope/dimension.
    identities: dict[tuple[str, str, str, str], MarketRankingEntry] = {}
    for row in query.iterator(chunk_size=1_000):
        key = (row.category, row.scope, row.ranking_dimension, row.sku_code)
        if key not in identities:
            identities[key] = row
    values = list(identities.values())
    total = len(values)
    items = [
        {
            "category": row.category,
            "scope": row.scope,
            "rankingDimension": row.ranking_dimension,
            "skuCode": row.sku_code,
            "productName": row.product_name,
            "brand": row.brand,
            "productUrl": row.product_url,
            "periodEnd": row.period_end,
        }
        for row in values[(page - 1) * page_size : page * page_size]
    ]
    return {
        "items": items,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "pageCount": max(1, math.ceil(total / page_size))},
    }


def execute_image_query(payload: dict[str, object]) -> dict[str, object]:
    operation = payload.get("operation")
    if operation == "image_cache_job" and set(payload) == {"operation", "params"} and isinstance(payload["params"], dict):
        return get_job(payload["params"])
    if operation == "image_metadata" and set(payload) == {"operation", "contentHash"}:
        return image_metadata(_text(payload.get("contentHash"), "contentHash", 64, required=True))
    if operation == "image_repair_candidates" and set(payload) == {"operation", "params"} and isinstance(payload["params"], dict):
        return repair_candidates(payload["params"])
    raise _error("市场图片查询字段无效")


def _create_job(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    batch_id = _text(payload.get("batchId", ""), "batchId", 128)
    scope_key = f"batch:{batch_id}" if batch_id else "global"
    job = MarketImageCacheJob.objects.filter(scope_key=scope_key).first()
    if job is None:
        job = MarketImageCacheJob.objects.create(
            id=f"market-image-cache-{uuid.uuid4()}",
            scope_key=scope_key,
            batch_id=batch_id,
            status="queued",
            requested_by=principal.email.lower(),
        )
    elif job.status in {"failed", "completed"} and not batch_id:
        job.status = "queued"
        job.discovery_cursor = ""
        job.discovery_complete = False
        job.discovered_count = 0
        job.completed_count = 0
        job.failed_count = 0
        job.pending_count = 0
        job.propagation_pending_count = 0
        job.processed_count = 0
        job.failure_count = 0
        job.error_code = ""
        job.error_message = ""
        job.completed_at = None
        job.save()
        MarketImageCacheJobItem.objects.filter(job_id=job.id).delete()
    job.requested_by = principal.email.lower()
    job.save()
    _audit(principal, "create_market_image_cache_job", job.id, {}, {"batchId": batch_id})
    return {"ok": True, "job": _job_value(job)}


def _discover(job: MarketImageCacheJob, limit: int = 1_000) -> int:
    query = MarketRankingEntry.objects.exclude(image_url="").filter(image_url__gt=job.discovery_cursor)
    if job.batch_id:
        query = query.filter(last_import_batch_id=job.batch_id)
    urls = list(query.order_by("image_url").values_list("image_url", flat=True).distinct()[:limit])
    created = 0
    for source_url in urls:
        _, inserted = MarketImageCacheJobItem.objects.get_or_create(
            job_id=job.id,
            source_url=source_url,
        )
        created += int(inserted)
    job.discovery_cursor = urls[-1] if urls else job.discovery_cursor
    job.discovery_complete = len(urls) < limit
    job.discovered_count = F("discovered_count") + created
    job.save()
    job.refresh_from_db()
    return created


@transaction.atomic
def _claim(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    requested_job = _text(payload.get("jobId", ""), "jobId", 128)
    limit = _integer(payload.get("limit"), "limit", 8, 1, MAX_CLAIMS)
    now = timezone.now()
    MarketImageCacheClaim.objects.filter(lease_expires_at__lte=now).delete()
    jobs = MarketImageCacheJob.objects.select_for_update().filter(
        Q(status="queued")
        | Q(status="running", lease_expires_at__isnull=True)
        | Q(status="running", lease_expires_at__lte=now)
    )
    if requested_job:
        jobs = jobs.filter(id=requested_job)
    job = jobs.order_by("updated_at", "id").first()
    if not job:
        return {"job": None, "claims": []}
    if not job.discovery_complete:
        _discover(job)
    token = secrets.token_hex(32)
    token_hash = _digest(token)
    job.status = "running"
    job.lease_token_hash = token_hash
    job.lease_epoch += 1
    job.lease_expires_at = now + timedelta(minutes=2)
    job.run_count += 1
    job.started_at = job.started_at or now
    job.error_code = ""
    job.error_message = ""
    job.save()
    claims = []
    items = MarketImageCacheJobItem.objects.filter(job_id=job.id, status="queued").order_by("source_url")[:limit]
    for item in items:
        cache = MarketImageCache.objects.filter(source_url=item.source_url).first()
        if cache and cache.status == "ready" and cache.content_sha256:
            item.status = "ready"
            item.content_sha256 = cache.content_sha256
            item.completed_at = now
            item.save()
            continue
        claim_token = secrets.token_hex(32)
        attempt_count = int(cache.attempt_count) + 1 if cache else 1
        try:
            with transaction.atomic():
                MarketImageCacheClaim.objects.create(
                    source_url=item.source_url,
                    job_id=job.id,
                    claim_token_hash=_digest(claim_token),
                    job_lease_token_hash=token_hash,
                    job_epoch=job.lease_epoch,
                    attempt_count=attempt_count,
                    lease_expires_at=now + timedelta(minutes=2),
                )
        except IntegrityError:
            continue
        claims.append(
            {
                "sourceUrl": item.source_url,
                "claimToken": claim_token,
                "attemptCount": attempt_count,
            }
        )
    return {
        "job": {**_job_value(job), "leaseToken": token, "leaseEpoch": job.lease_epoch, "leaseExpiresAt": iso(job.lease_expires_at)},
        "claims": claims,
    }


@transaction.atomic
def _complete_claim(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    job_id = _text(payload.get("jobId"), "jobId", 128, required=True)
    source_url = _text(payload.get("sourceUrl"), "sourceUrl", 2_048, required=True)
    job_token = _text(payload.get("jobLeaseToken"), "jobLeaseToken", 128, required=True)
    claim_token = _text(payload.get("claimToken"), "claimToken", 128, required=True)
    epoch = _integer(payload.get("jobEpoch"), "jobEpoch", 0, 1, 2**63 - 1)
    job = MarketImageCacheJob.objects.select_for_update().filter(id=job_id).first()
    claim = MarketImageCacheClaim.objects.select_for_update().filter(source_url=source_url).first()
    if (
        not job
        or not claim
        or job.status != "running"
        or job.lease_token_hash != _digest(job_token)
        or job.lease_epoch != epoch
        or job.lease_expires_at is None
        or job.lease_expires_at <= timezone.now()
        or claim.job_id != job.id
        or claim.claim_token_hash != _digest(claim_token)
        or claim.job_lease_token_hash != job.lease_token_hash
        or claim.job_epoch != epoch
    ):
        raise _error("图片缓存 claim 所有权已失效", code="version_conflict", status=409)
    error_code = _text(payload.get("errorCode", ""), "errorCode", 64)
    if error_code:
        message = _text(payload.get("errorMessage", ""), "errorMessage", 500)
        cache, _ = MarketImageCache.objects.get_or_create(source_url=source_url)
        cache.status = "failed"
        cache.attempt_count = claim.attempt_count
        cache.error_code = error_code
        cache.error_message = message
        cache.save()
        item = MarketImageCacheJobItem.objects.get(job_id=job.id, source_url=source_url)
        item.attempt_count = claim.attempt_count
        item.error_code = error_code
        item.error_message = message
        if claim.attempt_count >= 3:
            item.status = "failed"
            item.completed_at = timezone.now()
        item.save()
        claim.delete()
        return {"ok": True, "failed": True, "terminal": item.status == "failed"}
    content_hash = _text(payload.get("contentSha256"), "contentSha256", 64, required=True).lower()
    if len(content_hash) != 64 or any(char not in HEX64 for char in content_hash):
        raise _error("图片内容哈希无效")
    object_key = _text(payload.get("objectKey"), "objectKey", 1_024, required=True)
    if not object_key.startswith(f"market-images/v1/{content_hash}."):
        raise _error("图片对象键不符合内容寻址契约")
    mime_type = _text(payload.get("mimeType"), "mimeType", 100, required=True)
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise _error("图片 MIME 类型无效")
    size_bytes = _integer(payload.get("sizeBytes"), "sizeBytes", 0, 1, 6 * 1024 * 1024)
    cache, _ = MarketImageCache.objects.get_or_create(source_url=source_url)
    cache.status = "ready"
    cache.object_key = object_key
    cache.content_sha256 = content_hash
    cache.mime_type = mime_type
    cache.size_bytes = size_bytes
    cache.image_source = _text(payload.get("imageSource", "remote"), "imageSource", 32)
    cache.attempt_count = claim.attempt_count
    cache.error_code = ""
    cache.error_message = ""
    cache.save()
    item = MarketImageCacheJobItem.objects.get(job_id=job.id, source_url=source_url)
    item.status = "ready"
    item.content_sha256 = content_hash
    item.attempt_count = claim.attempt_count
    item.error_code = ""
    item.error_message = ""
    item.completed_at = timezone.now()
    item.save()
    MarketPriceSnapshot.objects.filter(image_url=source_url).update(image_content_sha256=content_hash, updated_at=timezone.now())
    claim.delete()
    return {"ok": True, "contentSha256": content_hash}


@transaction.atomic
def _finish(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    job_id = _text(payload.get("jobId"), "jobId", 128, required=True)
    token = _text(payload.get("jobLeaseToken"), "jobLeaseToken", 128, required=True)
    epoch = _integer(payload.get("jobEpoch"), "jobEpoch", 0, 1, 2**63 - 1)
    job = MarketImageCacheJob.objects.select_for_update().filter(id=job_id).first()
    if not job or job.lease_token_hash != _digest(token) or job.lease_epoch != epoch:
        raise _error("图片缓存任务租约已失效", code="version_conflict", status=409)
    if MarketImageCacheClaim.objects.filter(job_id=job.id, job_epoch=epoch).exists():
        raise _error("图片缓存任务仍有未完成 claim", code="conflict", status=409)
    if not job.discovery_complete:
        _discover(job)
    pending = MarketImageCacheJobItem.objects.filter(job_id=job.id, status="queued").exists()
    job.status = "queued" if pending or not job.discovery_complete else "completed"
    job.lease_token_hash = ""
    job.lease_expires_at = None
    job.next_run_at = timezone.now() + timedelta(seconds=5) if job.status == "queued" else None
    job.completed_at = timezone.now() if job.status == "completed" else None
    job.save()
    return {"ok": True, "job": _job_value(job)}


def _repairs(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    repairs = payload.get("repairs")
    if not isinstance(repairs, list) or len(repairs) > 1_000:
        raise _error("repairs 必须是有界数组")
    changed = 0
    with transaction.atomic():
        for value in repairs:
            if not isinstance(value, dict):
                raise _error("图片修复项无效")
            category = _text(value.get("category"), "category", 200, required=True)
            scope = _text(value.get("scope"), "scope", 200, required=True)
            dimension = _text(value.get("rankingDimension"), "rankingDimension", 8, required=True)
            if dimension not in {"SKU", "SPU"}:
                raise _error("图片修复维度无效")
            sku = _text(value.get("skuCode"), "skuCode", 200, required=True)
            image_url = _text(value.get("imageUrl"), "imageUrl", 2_048, required=True)
            if not image_url.startswith("https://"):
                raise _error("图片修复地址必须是 HTTPS")
            rows = MarketRankingEntry.objects.filter(category=category, scope=scope, ranking_dimension=dimension, sku_code=sku)
            old_urls = set(rows.values_list("image_url", flat=True))
            if old_urls - {"", image_url}:
                raise _error("商品当前图片已变化，请刷新修复清单", code="version_conflict", status=409)
            changed += rows.update(image_url=image_url, updated_at=timezone.now())
            # A changed image invalidates formal price and AI price only for the
            # exact market identity.  Classification can be reused later only
            # if it remains in the current taxonomy.
            MarketPriceSnapshot.objects.filter(category=category, scope=scope, ranking_dimension=dimension, sku_code=sku).update(
                image_url=image_url,
                image_content_sha256="",
                ai_image_price_cents=None,
                ai_price_type="",
                ai_confidence_bps=None,
                ai_reason="",
                confirmed_market_price_cents=None,
                confirmation_status="missing",
                confirmed_by="",
                confirmed_at=None,
                source_job_item_id="",
                prompt_version_id="",
                updated_at=timezone.now(),
            )
        if changed:
            _audit(principal, "apply_market_image_repairs", "batch", {}, {"changed": changed})
            bump_revision({"kind": "image_repairs", "changed": changed})
    return {"updated": changed, "queued": changed}


def execute_image_command(payload: dict[str, object], principal: Principal) -> object:
    action = payload.get("action")
    if action == "create_image_cache_job":
        return _create_job(payload, principal)
    if action == "claim_image_cache":
        return _claim(payload, principal)
    if action == "complete_image_cache_claim":
        return _complete_claim(payload, principal)
    if action == "finish_image_cache_job":
        return _finish(payload, principal)
    if action == "apply_image_repairs":
        return _repairs(payload, principal)
    raise _error("不支持的市场图片操作")
