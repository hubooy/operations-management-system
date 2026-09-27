"""Replay receipts and process-role gate for signed internal sales writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import SalesWriteRequestReceipt
from .write_service import SalesImportServiceError, lock_active_write_authority


RECEIPT_TTL = timedelta(days=7)
PROCESSING_STALE_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class WriteRequestClaim:
    request_id: str
    authority_epoch: str
    claim_token: str = ""
    replay_status: int | None = None
    replay_payload: dict[str, object] | None = None


def require_sales_writer_process() -> None:
    if settings.DJANGO_PROCESS_ROLE != "sales_writer" or settings.DJANGO_EXPECT_READ_ONLY:
        raise SalesImportServiceError(
            "当前 Django 进程不是销售写入服务",
            code="sales_writer_process_required",
            status=503,
        )


def claim_write_request(
    *,
    request_id: str,
    actor_email: str,
    method: str,
    path: str,
    body_sha256: str,
) -> WriteRequestClaim:
    require_sales_writer_process()
    with transaction.atomic():
        authority = lock_active_write_authority()
        existing = SalesWriteRequestReceipt.objects.select_for_update().filter(
            request_id=request_id
        ).first()
        if existing:
            if (
                existing.actor_email != actor_email
                or existing.method != method
                or existing.path != path
                or existing.body_sha256 != body_sha256
            ):
                raise SalesImportServiceError(
                    "request-id 已用于不同的写请求",
                    code="request_replay_mismatch",
                    status=409,
                )
            if existing.status == "completed":
                return WriteRequestClaim(
                    request_id=request_id,
                    authority_epoch=str(authority.authority_epoch),
                    claim_token=existing.claim_token,
                    replay_status=existing.response_status,
                    replay_payload=existing.response_payload,
                )
            if (
                existing.status == "processing"
                and existing.updated_at > timezone.now() - PROCESSING_STALE_AGE
            ):
                raise SalesImportServiceError(
                    "相同 request-id 的写请求仍在处理",
                    code="request_in_progress",
                    status=409,
                )
            claim_token = uuid.uuid4().hex
            existing.status = "processing"
            existing.claim_token = claim_token
            existing.response_status = 0
            existing.response_payload = {}
            existing.expires_at = timezone.now() + RECEIPT_TTL
            existing.save(
                update_fields=[
                    "status",
                    "claim_token",
                    "response_status",
                    "response_payload",
                    "expires_at",
                    "updated_at",
                ]
            )
        else:
            claim_token = uuid.uuid4().hex
            SalesWriteRequestReceipt.objects.create(
                request_id=request_id,
                actor_email=actor_email,
                method=method,
                path=path,
                body_sha256=body_sha256,
                status="processing",
                claim_token=claim_token,
                expires_at=timezone.now() + RECEIPT_TTL,
            )
        return WriteRequestClaim(
            request_id=request_id,
            authority_epoch=str(authority.authority_epoch),
            claim_token=claim_token,
        )


def complete_write_request(
    claim: WriteRequestClaim, *, response_status: int, response_payload: dict[str, object]
) -> None:
    with transaction.atomic():
        updated = SalesWriteRequestReceipt.objects.filter(
            request_id=claim.request_id,
            status="processing",
            claim_token=claim.claim_token,
        ).update(
            status="completed",
            response_status=response_status,
            response_payload=response_payload,
            expires_at=timezone.now() + RECEIPT_TTL,
            updated_at=timezone.now(),
        )
        if updated != 1:
            raise SalesImportServiceError(
                "写请求回执状态不一致", code="request_receipt_conflict", status=409
            )


def fail_write_request(claim: WriteRequestClaim) -> None:
    with transaction.atomic():
        SalesWriteRequestReceipt.objects.filter(
            request_id=claim.request_id,
            status="processing",
            claim_token=claim.claim_token,
        ).update(
            status="failed",
            expires_at=timezone.now() + RECEIPT_TTL,
            updated_at=timezone.now(),
        )
