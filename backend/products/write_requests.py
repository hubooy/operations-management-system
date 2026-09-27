from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .errors import ProductsApiError
from .models import ProductWriteAuthority, ProductWriteRequestReceipt


RECEIPT_TTL = timedelta(days=7)
PROCESSING_STALE_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class ProductWriteClaim:
    request_id: str
    claim_token: str
    replay_status: int | None = None
    replay_payload: dict[str, object] | None = None


def require_products_writer_process() -> None:
    if settings.DJANGO_PROCESS_ROLE not in {"products_writer", "development"}:
        raise ProductsApiError(
            "当前 Django 进程不是商品经营写入服务",
            code="products_writer_process_required",
            status=503,
        )
    if settings.DJANGO_EXPECT_READ_ONLY:
        raise ProductsApiError(
            "商品经营写入进程被配置为只读",
            code="products_writer_read_only",
            status=503,
        )


def lock_active_authority() -> ProductWriteAuthority:
    require_products_writer_process()
    try:
        # The terminal authority receipt is immutable to the product writer.
        # PostgreSQL requires UPDATE privilege for SELECT ... FOR UPDATE, which
        # would let the runtime role mutate the very fence it is meant to obey.
        # Only migration_writer may transition authority and activation has no
        # reverse path, so an exact plain read plus epoch/cutover comparison is
        # the least-privilege runtime fence (matching finance and market).
        authority = ProductWriteAuthority.objects.get(id=1)
    except ProductWriteAuthority.DoesNotExist as error:
        raise ProductsApiError(
            "PostgreSQL 商品经营写入权威门禁尚未初始化",
            code="products_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise ProductsApiError(
            "PostgreSQL 尚未取得商品经营唯一写入权",
            code="products_write_authority_inactive",
            status=503,
        )
    if settings.DJANGO_PROCESS_ROLE == "products_writer":
        expected_epoch = str(settings.PRODUCTS_WRITE_AUTHORITY_EPOCH or "")
        expected_cutover = str(settings.PRODUCTS_WRITE_CUTOVER_ID or "")
        if (
            not expected_epoch
            or not expected_cutover
            or str(authority.authority_epoch) != expected_epoch
            or authority.cutover_id != expected_cutover
        ):
            raise ProductsApiError(
                "PostgreSQL 商品经营写入权威的 epoch/cutover 配置不匹配",
                code="products_write_authority_mismatch",
                status=503,
            )
    return authority


def claim_write_request(
    *,
    request_id: str,
    actor_email: str,
    method: str,
    path: str,
    body_sha256: str,
    query_sha256: str,
) -> ProductWriteClaim:
    require_products_writer_process()
    if not request_id or len(request_id) > 128:
        raise ProductsApiError("内部请求标识无效", status=400)
    now = timezone.now()
    with transaction.atomic():
        lock_active_authority()
        expired_ids = list(
            ProductWriteRequestReceipt.objects.filter(expires_at__lte=now)
            .order_by("expires_at")
            .values_list("request_id", flat=True)[:20]
        )
        if expired_ids:
            ProductWriteRequestReceipt.objects.filter(request_id__in=expired_ids).delete()
        receipt = ProductWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        binding = (actor_email, method, path, body_sha256, query_sha256)
        if receipt:
            current = (
                receipt.actor_email,
                receipt.method,
                receipt.path,
                receipt.body_sha256,
                receipt.query_sha256,
            )
            if current != binding:
                raise ProductsApiError(
                    "request-id 已绑定其他商品经营写请求",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return ProductWriteClaim(
                    request_id=request_id,
                    claim_token=receipt.claim_token,
                    replay_status=receipt.response_status,
                    replay_payload=receipt.response_payload,
                )
            if receipt.status == "processing" and receipt.updated_at > now - PROCESSING_STALE_AGE:
                raise ProductsApiError("相同商品经营写请求仍在处理中", code="conflict", status=409)
            claim_token = uuid.uuid4().hex
            receipt.status = "processing"
            receipt.claim_token = claim_token
            receipt.response_status = 0
            receipt.response_payload = {}
            receipt.expires_at = now + RECEIPT_TTL
            receipt.save()
        else:
            claim_token = uuid.uuid4().hex
            ProductWriteRequestReceipt.objects.create(
                request_id=request_id,
                actor_email=actor_email,
                method=method,
                path=path,
                body_sha256=body_sha256,
                query_sha256=query_sha256,
                status="processing",
                claim_token=claim_token,
                expires_at=now + RECEIPT_TTL,
            )
        return ProductWriteClaim(request_id=request_id, claim_token=claim_token)


def complete_write_request(
    claim: ProductWriteClaim,
    *,
    response_status: int,
    response_payload: dict[str, object],
) -> None:
    updated = ProductWriteRequestReceipt.objects.filter(
        request_id=claim.request_id,
        status="processing",
        claim_token=claim.claim_token,
    ).update(
        status="completed",
        response_status=response_status,
        response_payload=response_payload,
        expires_at=timezone.now() + RECEIPT_TTL,
    )
    if updated != 1:
        raise ProductsApiError(
            "商品经营写请求回执所有权已失效",
            code="version_conflict",
            status=409,
        )


def fail_write_request(claim: ProductWriteClaim) -> None:
    ProductWriteRequestReceipt.objects.filter(
        request_id=claim.request_id,
        status="processing",
        claim_token=claim.claim_token,
    ).update(status="failed", expires_at=timezone.now() + RECEIPT_TTL)
