from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from sales.auth import Principal

from .errors import MarketApiError
from .models import (
    MarketMasterAuditLog,
    MarketNetshopProjection,
    MarketNetshopProjectionControl,
)
from .revisions import bump_revision


PAGE_SIZE = 1_000
MAX_PROJECTION_ROWS = 300_000
REVISION_RE = re.compile(r"^\d+:[0-9a-f]{12}$")
HASHED_KEY_RE = re.compile(r"^(?:identity|brand):[0-9a-f]{64}$")
METRIC_KEY_RE = re.compile(r"^metric:[^\x00-\x1f\x7f]{1,1024}$")
ROW_FIELDS = {
    "projectionKey",
    "kind",
    "source",
    "dataset",
    "platform",
    "shopName",
    "businessDate",
    "skuId",
    "spuId",
    "productCode",
    "transactionAmountCents",
    "brand",
}


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> MarketApiError:
    return MarketApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise _error(f"{label} 无效")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _revision(value: object) -> str:
    if not isinstance(value, str) or not REVISION_RE.fullmatch(value):
        raise _error("网店投影版本无效")
    return value


def _audit(principal: Principal, action: str, revision: str, after: dict[str, object]) -> None:
    MarketMasterAuditLog.objects.create(
        actor_email=principal.email.lower(),
        actor_role=principal.role,
        action=action,
        entity_type="market_netshop_projection",
        entity_id=revision,
        before_json={},
        after_json=after,
    )


def _control_value(control: MarketNetshopProjectionControl) -> dict[str, object]:
    return {
        "activeRevision": control.active_revision,
        "activeTotal": int(control.active_total),
        "syncingRevision": control.syncing_revision,
        "syncingTotal": int(control.syncing_total),
        "syncingOffset": int(control.syncing_offset),
        "leaseExpiresAt": control.lease_expires_at.isoformat() if control.lease_expires_at else None,
    }


def _row(value: object, revision: str) -> MarketNetshopProjection:
    if not isinstance(value, dict) or set(value) != ROW_FIELDS:
        raise _error("网店市场投影行字段集合无效")
    kind = _text(value["kind"], "kind", 16)
    key = _text(value["projectionKey"], "projectionKey", 1_024)
    if kind not in {"metric", "identity", "brand"}:
        raise _error("网店市场投影 kind 无效")
    if not (METRIC_KEY_RE.fullmatch(key) if kind == "metric" else HASHED_KEY_RE.fullmatch(key)):
        raise _error("网店市场投影 projectionKey 无效")
    business_date = _text(value["businessDate"], "businessDate", 10)
    if business_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date):
        raise _error("网店市场投影日期无效")
    amount = _integer(
        value["transactionAmountCents"],
        "transactionAmountCents",
        -(2**63),
        2**63 - 1,
    )
    return MarketNetshopProjection(
        projection_revision=revision,
        projection_key=key,
        kind=kind,
        source=_text(value["source"], "source", 64),
        dataset=_text(value["dataset"], "dataset", 64),
        platform=_text(value["platform"], "platform", 100),
        shop_name=_text(value["shopName"], "shopName", 100),
        business_date=business_date,
        sku_id=_text(value["skuId"], "skuId", 512),
        spu_id=_text(value["spuId"], "spuId", 512),
        product_code=_text(value["productCode"], "productCode", 512),
        transaction_amount_cents=amount,
        brand=_text(value["brand"], "brand", 120),
    )


@transaction.atomic
def _begin(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    revision = _revision(payload.get("sourceRevision"))
    total = _integer(payload.get("total"), "total", 0, MAX_PROJECTION_ROWS)
    now = timezone.now()
    owner = principal.email.lower()
    control = MarketNetshopProjectionControl.objects.select_for_update().get(id=1)
    if control.active_revision == revision and int(control.active_total) == total:
        return {"status": "active", **_control_value(control)}
    same_sync = (
        control.syncing_revision == revision
        and int(control.syncing_total) == total
        and control.syncing_owner == owner
    )
    if same_sync:
        control.lease_expires_at = now + timedelta(minutes=3)
        control.owner_token_hash = hashlib.sha256(f"{owner}\n{revision}".encode()).hexdigest()
        control.save(update_fields=["lease_expires_at", "owner_token_hash", "updated_at"])
        return {"status": "syncing", **_control_value(control)}
    if (
        control.syncing_revision
        and control.lease_expires_at is not None
        and control.lease_expires_at > now
        and control.syncing_owner != owner
    ):
        raise _error("网店市场投影正在同步", code="conflict", status=409)
    if control.syncing_revision:
        MarketNetshopProjection.objects.filter(
            projection_revision=control.syncing_revision
        ).exclude(projection_revision=control.active_revision).delete()
    MarketNetshopProjection.objects.filter(projection_revision=revision).exclude(
        projection_revision=control.active_revision
    ).delete()
    control.syncing_revision = revision
    control.syncing_total = total
    control.syncing_offset = 0
    control.syncing_owner = owner
    control.owner_token_hash = hashlib.sha256(f"{owner}\n{revision}".encode()).hexdigest()
    control.lease_expires_at = now + timedelta(minutes=3)
    control.save()
    _audit(principal, "begin_market_netshop_projection_sync", revision, {"total": total})
    return {"status": "syncing", **_control_value(control)}


@transaction.atomic
def _stage(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    revision = _revision(payload.get("sourceRevision"))
    offset = _integer(payload.get("offset"), "offset", 0, MAX_PROJECTION_ROWS)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise _error("网店市场投影 rows 必须是数组")
    control = MarketNetshopProjectionControl.objects.select_for_update().get(id=1)
    if (
        control.syncing_revision != revision
        or control.syncing_owner != principal.email.lower()
        or int(control.syncing_offset) != offset
    ):
        raise _error("网店市场投影分页所有权或 offset 已变化", code="version_conflict", status=409)
    expected = min(PAGE_SIZE, max(0, int(control.syncing_total) - offset))
    if len(rows) != expected:
        raise _error("网店市场投影分页行数与声明总数不一致")
    instances = [_row(value, revision) for value in rows]
    if len({item.projection_key for item in instances}) != len(instances):
        raise _error("网店市场投影分页包含重复 projectionKey")
    MarketNetshopProjection.objects.bulk_create(
        instances,
        batch_size=250,
        update_conflicts=True,
        unique_fields=["projection_revision", "projection_key"],
        update_fields=[
            "kind",
            "source",
            "dataset",
            "platform",
            "shop_name",
            "business_date",
            "sku_id",
            "spu_id",
            "product_code",
            "transaction_amount_cents",
            "brand",
        ],
    )
    next_offset = offset + len(instances)
    staged = MarketNetshopProjection.objects.filter(projection_revision=revision).count()
    if staged != next_offset:
        raise _error("网店市场投影分页存在跨页重复或缺失", code="version_conflict", status=409)
    control.syncing_offset = next_offset
    control.lease_expires_at = timezone.now() + timedelta(minutes=3)
    control.save(update_fields=["syncing_offset", "lease_expires_at", "updated_at"])
    return {"status": "syncing", **_control_value(control)}


@transaction.atomic
def _activate(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    revision = _revision(payload.get("sourceRevision"))
    control = MarketNetshopProjectionControl.objects.select_for_update().get(id=1)
    total = int(control.syncing_total)
    if (
        control.syncing_revision != revision
        or control.syncing_owner != principal.email.lower()
        or int(control.syncing_offset) != total
        or MarketNetshopProjection.objects.filter(projection_revision=revision).count() != total
    ):
        raise _error("网店市场投影未完整落库，拒绝激活", code="version_conflict", status=409)
    control.active_revision = revision
    control.active_total = total
    control.syncing_revision = ""
    control.syncing_total = 0
    control.syncing_offset = 0
    control.syncing_owner = ""
    control.owner_token_hash = ""
    control.lease_expires_at = None
    control.save()
    MarketNetshopProjection.objects.exclude(projection_revision=revision).delete()
    bump_revision({"kind": "netshop_projection", "revision": revision, "total": total})
    _audit(principal, "activate_market_netshop_projection", revision, {"total": total})
    return {"status": "active", **_control_value(control)}


def execute_projection_command(payload: dict[str, object], principal: Principal) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise _error("网店市场投影命令无效")
    action = payload.get("action")
    if action == "begin_sync" and set(payload) == {"action", "sourceRevision", "total"}:
        return _begin(payload, principal)
    if action == "stage_page" and set(payload) == {"action", "sourceRevision", "offset", "rows"}:
        return _stage(payload, principal)
    if action == "activate_sync" and set(payload) == {"action", "sourceRevision"}:
        return _activate(payload, principal)
    raise _error("网店市场投影命令字段集合无效")
