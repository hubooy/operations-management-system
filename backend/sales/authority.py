"""CAS-controlled PostgreSQL sales write-authority transitions."""

from __future__ import annotations

import re
import uuid

from django.db import transaction
from django.utils import timezone

from .authority_lock import acquire_sales_write_authority_exclusive_lock
from .cutover_attestation import (
    SalesCutoverAttestationError,
    require_valid_cutover_attestation,
)
from .models import SalesWriteAuthority


CUTOVER_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class SalesWriteAuthorityError(RuntimeError):
    pass


def _expected_epoch(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise SalesWriteAuthorityError("expected authority_epoch 无效") from error


def _cutover_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not CUTOVER_ID_RE.fullmatch(normalized):
        raise SalesWriteAuthorityError("cutover_id 必须为 8 到 128 位安全标识")
    return normalized


def authority_payload(authority: SalesWriteAuthority) -> dict[str, object]:
    return {
        "status": authority.status,
        "authorityEpoch": str(authority.authority_epoch),
        "cutoverId": authority.cutover_id or None,
        "activatedAt": authority.activated_at.isoformat() if authority.activated_at else None,
        "updatedAt": authority.updated_at.isoformat(),
    }


def read_write_authority() -> dict[str, object]:
    try:
        authority = SalesWriteAuthority.objects.get(id=1)
    except SalesWriteAuthority.DoesNotExist as error:
        raise SalesWriteAuthorityError("销售写入权威门禁尚未初始化") from error
    return authority_payload(authority)


def _locked_authority() -> SalesWriteAuthority:
    acquire_sales_write_authority_exclusive_lock()
    try:
        return SalesWriteAuthority.objects.select_for_update().get(id=1)
    except SalesWriteAuthority.DoesNotExist as error:
        raise SalesWriteAuthorityError("销售写入权威门禁尚未初始化") from error


def prepare_write_authority(*, expected_epoch: str, cutover_id: str) -> dict[str, object]:
    expected = _expected_epoch(expected_epoch)
    cutover = _cutover_id(cutover_id)
    with transaction.atomic():
        authority = _locked_authority()
        if authority.authority_epoch != expected:
            raise SalesWriteAuthorityError("authority_epoch 已变化，拒绝准备切换")
        if authority.status != "pending":
            raise SalesWriteAuthorityError("只有 pending 写入权威可以准备切换")
        if authority.cutover_id:
            raise SalesWriteAuthorityError("pending 写入权威已经完成 cutover 准备")
        authority.cutover_id = cutover
        authority.authority_epoch = uuid.uuid4()
        authority.save(update_fields=["cutover_id", "authority_epoch", "updated_at"])
        return authority_payload(authority)


def activate_write_authority(
    *,
    expected_epoch: str,
    cutover_id: str,
    attestation_sha256: str,
) -> dict[str, object]:
    expected = _expected_epoch(expected_epoch)
    cutover = _cutover_id(cutover_id)
    with transaction.atomic():
        authority = _locked_authority()
        if authority.authority_epoch != expected or authority.cutover_id != cutover:
            raise SalesWriteAuthorityError("authority_epoch 或 cutover_id 不匹配")
        if authority.status != "pending":
            raise SalesWriteAuthorityError("只有 pending 写入权威可以激活")
        try:
            require_valid_cutover_attestation(
                cutover_id=cutover,
                payload_sha256=attestation_sha256,
                verify_live_baseline=True,
            )
        except SalesCutoverAttestationError as error:
            raise SalesWriteAuthorityError(str(error)) from error
        authority.status = "active"
        authority.authority_epoch = uuid.uuid4()
        authority.activated_at = timezone.now()
        authority.save(
            update_fields=["status", "authority_epoch", "activated_at", "updated_at"]
        )
        return authority_payload(authority)


def disable_write_authority(*, expected_epoch: str, cutover_id: str) -> dict[str, object]:
    """Emergency stop only; disabled cannot transition back to pending/active."""

    expected = _expected_epoch(expected_epoch)
    cutover = _cutover_id(cutover_id)
    with transaction.atomic():
        authority = _locked_authority()
        if authority.authority_epoch != expected or authority.cutover_id != cutover:
            raise SalesWriteAuthorityError("authority_epoch 或 cutover_id 不匹配")
        if authority.status != "active":
            raise SalesWriteAuthorityError("只有 active 写入权威可以停用")
        authority.status = "disabled"
        authority.authority_epoch = uuid.uuid4()
        authority.save(update_fields=["status", "authority_epoch", "updated_at"])
        return authority_payload(authority)
