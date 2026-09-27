from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import uuid

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .errors import WorkflowApiError
from .models import (
    WorkflowOperationsWriteAuthority,
    WorkflowWriteAuthority,
    WorkflowWriteRequestReceipt,
)


RECEIPT_TTL = timedelta(days=7)
PROCESSING_STALE_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class WorkflowWriteClaim:
    request_id: str
    claim_token: str
    replay_status: int | None = None
    replay_payload: dict[str, object] | None = None


def _lock_request_identity(request_id: str) -> None:
    """Serialize an absent-or-present receipt decision for one PostgreSQL request id."""
    if connection.vendor != "postgresql":
        return
    key = int.from_bytes(
        hashlib.sha256(f"workflow-write-request:{request_id}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])


def require_workflow_writer_process() -> None:
    if settings.DJANGO_PROCESS_ROLE not in {"workflow_writer", "development"}:
        raise WorkflowApiError(
            "当前 Django 进程不是运营事务写入服务",
            code="workflow_writer_process_required",
            status=503,
        )
    if settings.DJANGO_EXPECT_READ_ONLY:
        raise WorkflowApiError(
            "运营事务写入进程被配置为只读",
            code="workflow_writer_read_only",
            status=503,
        )


def lock_active_authority() -> WorkflowWriteAuthority:
    require_workflow_writer_process()
    try:
        # The terminal authority receipt is immutable to the workflow writer.
        # PostgreSQL requires UPDATE privilege for SELECT ... FOR UPDATE, which
        # would let the runtime role mutate the fence it is meant to obey.
        # Only migration_writer may transition authority and activation has no
        # reverse path, so an exact plain read plus epoch/cutover comparison is
        # the least-privilege runtime fence (matching products and inventory).
        authority = WorkflowWriteAuthority.objects.get(id=1)
    except WorkflowWriteAuthority.DoesNotExist as error:
        raise WorkflowApiError(
            "PostgreSQL 运营事务写入权威门禁尚未初始化",
            code="workflow_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise WorkflowApiError(
            "PostgreSQL 尚未取得结构化新品上新唯一写入权",
            code="workflow_write_authority_inactive",
            status=503,
        )
    if settings.DJANGO_PROCESS_ROLE == "workflow_writer":
        expected_epoch = str(settings.WORKFLOW_WRITE_AUTHORITY_EPOCH or "")
        expected_cutover = str(settings.WORKFLOW_WRITE_CUTOVER_ID or "")
        if (
            not expected_epoch
            or not expected_cutover
            or str(authority.authority_epoch) != expected_epoch
            or authority.cutover_id != expected_cutover
        ):
            raise WorkflowApiError(
                "PostgreSQL 运营事务写入权威的 epoch/cutover 配置不匹配",
                code="workflow_write_authority_mismatch",
                status=503,
            )
    return authority


def lock_active_operations_authority() -> WorkflowOperationsWriteAuthority:
    require_workflow_writer_process()
    try:
        authority = WorkflowOperationsWriteAuthority.objects.get(id=1)
    except WorkflowOperationsWriteAuthority.DoesNotExist as error:
        raise WorkflowApiError(
            "PostgreSQL 运营事务全板块写入权威门禁尚未初始化",
            code="workflow_operations_write_authority_unavailable",
            status=503,
        ) from error
    if authority.status != "postgres":
        raise WorkflowApiError(
            "PostgreSQL 尚未取得工作计划与运营记录唯一写入权",
            code="workflow_operations_write_authority_inactive",
            status=503,
        )
    if settings.DJANGO_PROCESS_ROLE == "workflow_writer":
        expected_epoch = str(settings.WORKFLOW_OPERATIONS_WRITE_AUTHORITY_EPOCH or "")
        expected_cutover = str(settings.WORKFLOW_OPERATIONS_WRITE_CUTOVER_ID or "")
        if (
            not expected_epoch
            or not expected_cutover
            or str(authority.authority_epoch) != expected_epoch
            or authority.cutover_id != expected_cutover
        ):
            raise WorkflowApiError(
                "PostgreSQL 运营事务全板块写入权威的 epoch/cutover 配置不匹配",
                code="workflow_operations_write_authority_mismatch",
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
    authority_scope: str = "launch",
) -> WorkflowWriteClaim:
    require_workflow_writer_process()
    if not request_id or len(request_id) > 128:
        raise WorkflowApiError("内部请求标识无效")
    now = timezone.now()
    with transaction.atomic():
        if authority_scope == "operations":
            lock_active_operations_authority()
        elif authority_scope == "launch":
            lock_active_authority()
        else:
            raise WorkflowApiError("运营事务写入权威范围无效", status=503)
        _lock_request_identity(request_id)
        expired_ids = list(
            WorkflowWriteRequestReceipt.objects.filter(expires_at__lte=now)
            .order_by("expires_at")
            .values_list("request_id", flat=True)[:20]
        )
        if expired_ids:
            WorkflowWriteRequestReceipt.objects.filter(request_id__in=expired_ids).delete()
        receipt = (
            WorkflowWriteRequestReceipt.objects.select_for_update()
            .filter(request_id=request_id)
            .first()
        )
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
                raise WorkflowApiError(
                    "request-id 已绑定其他运营事务写请求",
                    code="version_conflict",
                    status=409,
                )
            if receipt.status == "completed":
                return WorkflowWriteClaim(
                    request_id=request_id,
                    claim_token=receipt.claim_token,
                    replay_status=receipt.response_status,
                    replay_payload=receipt.response_payload,
                )
            if receipt.status == "processing" and receipt.updated_at > now - PROCESSING_STALE_AGE:
                raise WorkflowApiError("相同运营事务写请求仍在处理中", code="conflict", status=409)
            claim_token = uuid.uuid4().hex
            receipt.status = "processing"
            receipt.claim_token = claim_token
            receipt.response_status = 0
            receipt.response_payload = {}
            receipt.expires_at = now + RECEIPT_TTL
            receipt.save()
        else:
            claim_token = uuid.uuid4().hex
            WorkflowWriteRequestReceipt.objects.create(
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
        return WorkflowWriteClaim(request_id=request_id, claim_token=claim_token)


def complete_write_request(
    claim: WorkflowWriteClaim,
    *,
    response_status: int,
    response_payload: dict[str, object],
) -> None:
    updated = WorkflowWriteRequestReceipt.objects.filter(
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
        raise WorkflowApiError(
            "运营事务写请求回执所有权已失效",
            code="version_conflict",
            status=409,
        )


def fail_write_request(claim: WorkflowWriteClaim) -> None:
    WorkflowWriteRequestReceipt.objects.filter(
        request_id=claim.request_id,
        status="processing",
        claim_token=claim.claim_token,
    ).update(status="failed", expires_at=timezone.now() + RECEIPT_TTL)
