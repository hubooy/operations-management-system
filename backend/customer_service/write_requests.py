from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import uuid

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .errors import CustomerServiceApiError
from .models import CustomerServiceWriteAuthority, CustomerServiceWriteRequestReceipt


RECEIPT_TTL = timedelta(days=7)
PROCESSING_STALE_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class CustomerServiceWriteClaim:
    request_id: str
    claim_token: str
    replay_status: int | None = None
    replay_payload: dict[str, object] | None = None


def require_writer_process() -> None:
    if settings.DJANGO_PROCESS_ROLE not in {"customer_service_writer", "migration_writer", "development"}:
        raise CustomerServiceApiError(
            "当前 Django 进程不是客服写入服务",
            code="customer_service_writer_process_required",
            status=503,
        )
    if settings.DJANGO_EXPECT_READ_ONLY:
        raise CustomerServiceApiError(
            "客服写入进程被配置为只读",
            code="customer_service_writer_read_only",
            status=503,
        )


def lock_active_authority() -> CustomerServiceWriteAuthority:
    require_writer_process()
    try:
        authority = CustomerServiceWriteAuthority.objects.get(id=1)
    except CustomerServiceWriteAuthority.DoesNotExist as error:
        raise CustomerServiceApiError(
            "PostgreSQL 客服写入权威门禁尚未初始化",
            code="customer_service_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise CustomerServiceApiError(
            "PostgreSQL 尚未取得客服域唯一写入权",
            code="customer_service_write_authority_inactive",
            status=503,
        )
    if settings.DJANGO_PROCESS_ROLE == "customer_service_writer":
        expected_epoch = str(settings.CUSTOMER_SERVICE_WRITE_AUTHORITY_EPOCH or "")
        expected_cutover = str(settings.CUSTOMER_SERVICE_WRITE_CUTOVER_ID or "")
        if (
            not expected_epoch
            or not expected_cutover
            or str(authority.authority_epoch) != expected_epoch
            or authority.cutover_id != expected_cutover
        ):
            raise CustomerServiceApiError(
                "PostgreSQL 客服写入权威的 epoch/cutover 配置不匹配",
                code="customer_service_write_authority_mismatch",
                status=503,
            )
    return authority


def _lock_request_identity(request_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(
        hashlib.sha256(f"customer-service-write-request:{request_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def claim_write_request(
    *,
    request_id: str,
    actor_email: str,
    method: str,
    path: str,
    body_sha256: str,
    query_sha256: str,
) -> CustomerServiceWriteClaim:
    require_writer_process()
    if not request_id or len(request_id) > 128:
        raise CustomerServiceApiError("内部请求标识无效")
    now = timezone.now()
    with transaction.atomic():
        lock_active_authority()
        _lock_request_identity(request_id)
        expired = list(
            CustomerServiceWriteRequestReceipt.objects.filter(expires_at__lte=now)
            .order_by("expires_at")
            .values_list("request_id", flat=True)[:20]
        )
        if expired:
            CustomerServiceWriteRequestReceipt.objects.filter(request_id__in=expired).delete()
        receipt = CustomerServiceWriteRequestReceipt.objects.select_for_update().filter(request_id=request_id).first()
        binding = (actor_email, method, path, body_sha256, query_sha256)
        if receipt:
            current = (receipt.actor_email, receipt.method, receipt.path, receipt.body_sha256, receipt.query_sha256)
            if current != binding:
                raise CustomerServiceApiError(
                    "request-id 已绑定其他客服写请求",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return CustomerServiceWriteClaim(
                    request_id=request_id,
                    claim_token=receipt.claim_token,
                    replay_status=receipt.response_status,
                    replay_payload=receipt.response_payload,
                )
            if receipt.status == "processing" and receipt.updated_at > now - PROCESSING_STALE_AGE:
                raise CustomerServiceApiError("相同客服写请求仍在处理中", code="conflict", status=409)
            claim_token = uuid.uuid4().hex
            receipt.status = "processing"
            receipt.claim_token = claim_token
            receipt.response_status = 0
            receipt.response_payload = {}
            receipt.expires_at = now + RECEIPT_TTL
            receipt.save()
        else:
            claim_token = uuid.uuid4().hex
            CustomerServiceWriteRequestReceipt.objects.create(
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
        return CustomerServiceWriteClaim(request_id=request_id, claim_token=claim_token)


def complete_write_request(
    claim: CustomerServiceWriteClaim,
    *,
    response_status: int,
    response_payload: dict[str, object],
) -> None:
    updated = CustomerServiceWriteRequestReceipt.objects.filter(
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
        raise CustomerServiceApiError(
            "客服写请求回执所有权已失效",
            code="version_conflict",
            status=409,
        )


def fail_write_request(claim: CustomerServiceWriteClaim) -> None:
    CustomerServiceWriteRequestReceipt.objects.filter(
        request_id=claim.request_id,
        status="processing",
        claim_token=claim.claim_token,
    ).update(status="failed", expires_at=timezone.now() + RECEIPT_TTL)
