from __future__ import annotations

from datetime import date, timedelta
import hashlib
import hmac
import json
import re

from django.db import transaction
from django.utils import timezone

from .errors import ProductsApiError
from .models import ProductInventoryProjection, ProductInventoryProjectionControl
from .revisions import bump_revision
from .write_requests import lock_active_authority


HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ROWS = 20_000
MAX_PAGE_ROWS = 1_000
LEASE_AGE = timedelta(minutes=10)


def _error(message: str, *, code: str = "invalid_request", status: int = 400) -> ProductsApiError:
    return ProductsApiError(message, code=code, status=status)


def _text(value: object, label: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _error(f"{label} 无效")
    normalized = value.strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum or any(
        ord(char) < 32 or ord(char) == 127 for char in normalized
    ):
        raise _error(f"{label} 无效")
    return normalized


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _error(f"{label} 无效")
    return value


def _revision(value: object) -> str:
    if not isinstance(value, str) or not HEX_RE.fullmatch(value):
        raise _error("projectionRevision 必须是 64 位小写 SHA-256")
    return value


def _snapshot_date(value: object) -> str:
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise _error("snapshotDate 必须是有效日期")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as error:
        raise _error("snapshotDate 必须是有效日期") from error
    return value


def _owner_hash(token: str) -> str:
    return hashlib.sha256(f"product-inventory-projection-owner\n{token}".encode()).hexdigest()


def _assert_owner(control: ProductInventoryProjectionControl, token: object) -> None:
    normalized = _text(token, "ownerToken", 128)
    if not control.owner_token_hash or not hmac.compare_digest(
        control.owner_token_hash, _owner_hash(normalized)
    ):
        raise _error("库存投影写入所有权已失效", code="version_conflict", status=409)
    if control.lease_expires_at is None or control.lease_expires_at <= timezone.now():
        raise _error("库存投影写入租约已过期", code="version_conflict", status=409)


def _control_payload(control: ProductInventoryProjectionControl) -> dict[str, object]:
    return {
        "activeRevision": control.active_revision or None,
        "activeTotal": int(control.active_total),
        "activeSourceBatchId": control.active_source_batch_id or None,
        "activeSnapshotDate": control.active_snapshot_date or None,
        "syncingRevision": control.syncing_revision or None,
        "syncingTotal": int(control.syncing_total),
        "syncingOffset": int(control.syncing_offset),
    }


def _stored_projection_digest(
    revision: str,
    source_batch_id: str,
    snapshot_date: str,
) -> tuple[str, int]:
    stored = list(
        ProductInventoryProjection.objects.filter(projection_revision=revision)
        .order_by("product_code")
        .values(
            "product_code",
            "brand",
            "available_quantity",
            "known_stock_value_cents",
            "priced_available_quantity",
        )
    )
    canonical = [
        {
            "productCode": row["product_code"],
            "brand": row["brand"],
            "availableQuantity": int(row["available_quantity"]),
            "knownStockValueCents": int(row["known_stock_value_cents"]),
            "pricedAvailableQuantity": int(row["priced_available_quantity"]),
        }
        for row in stored
    ]
    body = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(
        f"product-inventory-projection-v1\n{source_batch_id}\n{snapshot_date}\n{body}".encode()
    ).hexdigest()
    return digest, len(canonical)


def _normalize_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PAGE_ROWS:
        raise _error(f"rows 必须是 1 到 {MAX_PAGE_ROWS} 项的数组")
    normalized: list[dict[str, object]] = []
    previous = ""
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "productCode",
            "brand",
            "availableQuantity",
            "knownStockValueCents",
            "pricedAvailableQuantity",
        }:
            raise _error("库存投影行字段集合无效")
        product_code = _text(raw["productCode"], "productCode", 512)
        if previous and product_code.encode("utf-8") <= previous.encode("utf-8"):
            raise _error("库存投影行必须按规格代码 UTF-8 严格升序且不能重复")
        previous = product_code
        available = _integer(raw["availableQuantity"], "availableQuantity", 0, 10**15)
        value_cents = _integer(raw["knownStockValueCents"], "knownStockValueCents", 0, 10**18)
        priced = _integer(raw["pricedAvailableQuantity"], "pricedAvailableQuantity", 0, available)
        normalized.append(
            {
                "productCode": product_code,
                "brand": _text(raw["brand"], "brand", 500, allow_empty=True),
                "availableQuantity": available,
                "knownStockValueCents": value_cents,
                "pricedAvailableQuantity": priced,
            }
        )
    return normalized


def begin_sync(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {
        "action",
        "projectionRevision",
        "sourceBatchId",
        "snapshotDate",
        "totalRows",
        "ownerToken",
    }:
        raise _error("begin_sync 字段集合无效")
    revision = _revision(payload["projectionRevision"])
    source_batch_id = _text(payload["sourceBatchId"], "sourceBatchId", 128)
    snapshot = _snapshot_date(payload["snapshotDate"])
    total = _integer(payload["totalRows"], "totalRows", 0, MAX_ROWS)
    owner_token = _text(payload["ownerToken"], "ownerToken", 128)
    with transaction.atomic():
        lock_active_authority()
        control = ProductInventoryProjectionControl.objects.select_for_update().get(id=1)
        if (
            control.active_revision == revision
            and control.active_source_batch_id == source_batch_id
            and control.active_snapshot_date == snapshot
            and int(control.active_total) == total
        ):
            stored_digest, actual = _stored_projection_digest(
                revision,
                control.active_source_batch_id,
                control.active_snapshot_date,
            )
            if actual != total or not hmac.compare_digest(stored_digest, revision):
                raise _error(
                    "已激活库存投影的事实摘要不一致",
                    code="version_conflict",
                    status=409,
                )
            return {"ok": True, "status": "active", "control": _control_payload(control)}
        if control.active_revision == revision:
            raise _error(
                "相同库存投影摘要已激活但身份或行数不一致",
                code="version_conflict",
                status=409,
            )
        now = timezone.now()
        if (
            control.syncing_revision == revision
            and control.syncing_source_batch_id == source_batch_id
            and control.syncing_snapshot_date == snapshot
            and int(control.syncing_total) == total
            and control.syncing_owner == actor_email[:320]
            and control.owner_token_hash
            and hmac.compare_digest(control.owner_token_hash, _owner_hash(owner_token))
        ):
            control.lease_expires_at = now + LEASE_AGE
            control.save(update_fields=["lease_expires_at", "updated_at"])
            return {
                "ok": True,
                "status": "syncing",
                "ownerToken": owner_token,
                "control": _control_payload(control),
            }
        if (
            control.syncing_revision
            and control.lease_expires_at is not None
            and control.lease_expires_at > now
        ):
            raise _error(
                "另一库存投影同步仍持有写入租约",
                code="conflict",
                status=409,
            )
        ProductInventoryProjection.objects.exclude(
            projection_revision=control.active_revision
        ).delete()
        ProductInventoryProjection.objects.filter(projection_revision=revision).delete()
        control.syncing_revision = revision
        control.syncing_total = total
        control.syncing_offset = 0
        control.syncing_source_batch_id = source_batch_id
        control.syncing_snapshot_date = snapshot
        control.syncing_owner = actor_email[:320]
        control.owner_token_hash = _owner_hash(owner_token)
        control.lease_expires_at = now + LEASE_AGE
        control.save()
        return {
            "ok": True,
            "status": "syncing",
            "ownerToken": owner_token,
            "control": _control_payload(control),
        }


def stage_page(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "projectionRevision", "ownerToken", "offset", "rows"}:
        raise _error("stage_page 字段集合无效")
    revision = _revision(payload["projectionRevision"])
    offset = _integer(payload["offset"], "offset", 0, MAX_ROWS)
    rows = _normalize_rows(payload["rows"])
    with transaction.atomic():
        lock_active_authority()
        control = ProductInventoryProjectionControl.objects.select_for_update().get(id=1)
        if control.syncing_revision != revision or control.syncing_owner != actor_email[:320]:
            raise _error("库存投影同步身份不匹配", code="version_conflict", status=409)
        _assert_owner(control, payload["ownerToken"])
        if offset + len(rows) > int(control.syncing_total):
            raise _error("库存投影页超出声明总行数")
        if offset < int(control.syncing_offset):
            existing = list(
                ProductInventoryProjection.objects.filter(projection_revision=revision)
                .order_by("product_code")
                .values(
                    "product_code",
                    "brand",
                    "available_quantity",
                    "known_stock_value_cents",
                    "priced_available_quantity",
                )[offset : offset + len(rows)]
            )
            canonical = [
                {
                    "productCode": row["product_code"],
                    "brand": row["brand"],
                    "availableQuantity": int(row["available_quantity"]),
                    "knownStockValueCents": int(row["known_stock_value_cents"]),
                    "pricedAvailableQuantity": int(row["priced_available_quantity"]),
                }
                for row in existing
            ]
            if canonical != rows:
                raise _error("重复库存投影页与已接收内容不一致", code="version_conflict", status=409)
            return {"ok": True, "status": "staged", "control": _control_payload(control)}
        if offset != int(control.syncing_offset):
            raise _error("库存投影页 offset 不连续", code="version_conflict", status=409)
        ProductInventoryProjection.objects.bulk_create(
            [
                ProductInventoryProjection(
                    projection_revision=revision,
                    product_code=str(row["productCode"]),
                    brand=str(row["brand"]),
                    available_quantity=int(row["availableQuantity"]),
                    known_stock_value_cents=int(row["knownStockValueCents"]),
                    priced_available_quantity=int(row["pricedAvailableQuantity"]),
                    source_batch_id=control.syncing_source_batch_id,
                    snapshot_date=control.syncing_snapshot_date,
                )
                for row in rows
            ],
            batch_size=500,
        )
        control.syncing_offset = offset + len(rows)
        control.lease_expires_at = timezone.now() + LEASE_AGE
        control.save(update_fields=["syncing_offset", "lease_expires_at", "updated_at"])
        return {"ok": True, "status": "staged", "control": _control_payload(control)}


def activate_sync(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    if set(payload) != {"action", "projectionRevision", "ownerToken"}:
        raise _error("activate_sync 字段集合无效")
    revision = _revision(payload["projectionRevision"])
    with transaction.atomic():
        lock_active_authority()
        control = ProductInventoryProjectionControl.objects.select_for_update().get(id=1)
        if control.active_revision == revision and not control.syncing_revision:
            stored_digest, actual = _stored_projection_digest(
                revision,
                control.active_source_batch_id,
                control.active_snapshot_date,
            )
            if (
                actual != int(control.active_total)
                or not hmac.compare_digest(stored_digest, revision)
            ):
                raise _error(
                    "已激活库存投影的事实摘要不一致",
                    code="version_conflict",
                    status=409,
                )
            return {"ok": True, "status": "active", "control": _control_payload(control)}
        if control.syncing_revision != revision or control.syncing_owner != actor_email[:320]:
            raise _error("库存投影同步身份不匹配", code="version_conflict", status=409)
        _assert_owner(control, payload["ownerToken"])
        expected = int(control.syncing_total)
        if int(control.syncing_offset) != expected:
            raise _error("库存投影尚未接收全部页面", code="conflict", status=409)
        stored_digest, actual = _stored_projection_digest(
            revision,
            control.syncing_source_batch_id,
            control.syncing_snapshot_date,
        )
        if actual != expected or not hmac.compare_digest(stored_digest, revision):
            raise _error("库存投影落库行数或内容摘要回查不一致", code="version_conflict", status=409)
        old_revision = control.active_revision
        control.active_revision = revision
        control.active_total = expected
        control.active_source_batch_id = control.syncing_source_batch_id
        control.active_snapshot_date = control.syncing_snapshot_date
        control.syncing_revision = ""
        control.syncing_total = 0
        control.syncing_offset = 0
        control.syncing_source_batch_id = ""
        control.syncing_snapshot_date = ""
        control.syncing_owner = ""
        control.owner_token_hash = ""
        control.lease_expires_at = None
        control.save()
        ProductInventoryProjection.objects.exclude(projection_revision=revision).delete()
        bump_revision(
            {
                "kind": "inventory_projection",
                "projectionRevision": revision,
                "previousRevision": old_revision,
                "rowCount": actual,
                "sourceBatchId": control.active_source_batch_id,
            }
        )
        return {"ok": True, "status": "active", "control": _control_payload(control)}


def execute_projection_action(payload: dict[str, object], actor_email: str) -> dict[str, object]:
    action = payload.get("action")
    if action == "begin_sync":
        return begin_sync(payload, actor_email)
    if action == "stage_page":
        return stage_page(payload, actor_email)
    if action == "activate_sync":
        return activate_sync(payload, actor_email)
    raise _error("未知的库存投影操作")
