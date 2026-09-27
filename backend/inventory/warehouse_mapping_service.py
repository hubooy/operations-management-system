from __future__ import annotations

import hashlib
import json
import re

from django.db import transaction
from django.utils import timezone

from .errors import InventoryApiError
from .models import InventoryOperatingSettings
from .revisions import bump_revision
from .warehouse_mapping import WAREHOUSE_MAPPING, classify_warehouse
from .write_requests import lock_active_authority


MAX_MAPPINGS = 2_000
CATEGORY_LABELS = {
    "jd": "京东仓",
    "dropship": "代发仓",
    "afterSales": "售后仓",
    "guangdong": "广东仓",
    "sample": "样品仓",
    "cainiao": "菜鸟仓",
    "overseas": "海外仓",
    "virtual": "虚拟仓",
    "exception": "异常仓",
    "selfOperated": "自营仓",
}
EDITABLE_CATEGORIES = frozenset(CATEGORY_LABELS)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def _normalized_entry(value: object, *, warehouse: str) -> dict[str, object]:
    if not isinstance(value, dict) or not set(value).issubset(
        {"category", "label", "includeInInventory", "pendingConfirmation"}
    ):
        raise InventoryApiError(f"仓库“{warehouse}”的映射字段无效")
    category = value.get("category")
    included = value.get("includeInInventory")
    pending = value.get("pendingConfirmation", False)
    if category not in EDITABLE_CATEGORIES:
        raise InventoryApiError(f"仓库“{warehouse}”的仓库类型无效")
    if not isinstance(included, bool):
        raise InventoryApiError(f"仓库“{warehouse}”的计入库存字段无效")
    if not isinstance(pending, bool):
        raise InventoryApiError(f"仓库“{warehouse}”的确认状态无效")
    if warehouse == "刷刷仓" and included:
        raise InventoryApiError("刷刷仓是固定业务排除仓，不能设置为计入库存")
    return {
        "category": str(category),
        "label": CATEGORY_LABELS[str(category)],
        "includeInInventory": included,
        "pendingConfirmation": pending,
    }


def normalize_mapping(value: object, *, allow_empty: bool = False) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict) or len(value) > MAX_MAPPINGS or (not allow_empty and not value):
        raise InventoryApiError("仓库映射数量无效")
    normalized: dict[str, dict[str, object]] = {}
    for raw_warehouse, entry in value.items():
        if not isinstance(raw_warehouse, str):
            raise InventoryApiError("仓库名称无效")
        warehouse = raw_warehouse.strip()
        if (
            not warehouse
            or warehouse != raw_warehouse
            or len(warehouse) > 240
            or CONTROL_CHARACTERS.search(warehouse)
        ):
            raise InventoryApiError(f"仓库名称“{raw_warehouse}”无效")
        normalized[warehouse] = _normalized_entry(entry, warehouse=warehouse)
    return normalized


def effective_mapping(settings: InventoryOperatingSettings | None = None) -> dict[str, dict[str, object]]:
    row = settings or InventoryOperatingSettings.objects.get(id=1)
    stored = row.warehouse_mapping_json
    if isinstance(stored, dict) and stored:
        return normalize_mapping(stored)
    return normalize_mapping(WAREHOUSE_MAPPING)


def mapping_revision(mapping: dict[str, dict[str, object]]) -> str:
    canonical = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def mapping_payload(settings: InventoryOperatingSettings | None = None) -> dict[str, object]:
    row = settings or InventoryOperatingSettings.objects.get(id=1)
    mapping = effective_mapping(row)
    rows = sorted([
        {"warehouse": warehouse, **entry}
        for warehouse, entry in mapping.items()
    ], key=lambda item: (not bool(item["pendingConfirmation"]), str(item["warehouse"])))
    return {
        "rows": rows,
        "mappingRevision": mapping_revision(mapping),
        "pendingConfirmationCount": sum(bool(item["pendingConfirmation"]) for item in rows),
        "updatedAt": row.warehouse_mapping_updated_at.isoformat() if row.warehouse_mapping_updated_at else None,
        "updatedBy": row.warehouse_mapping_updated_by or None,
    }


def discover_pending_mappings(
    settings: InventoryOperatingSettings,
    warehouses: set[str],
    actor_email: str,
) -> list[str]:
    """Merge newly observed warehouses into a locked settings row.

    The caller owns the inventory authority and transaction. Persisting through the
    same transaction keeps discovery atomic with the successful inventory import.
    """
    current = effective_mapping(settings)
    additions: dict[str, dict[str, object]] = {}
    for warehouse in sorted(warehouses):
        if warehouse in current:
            continue
        classification = classify_warehouse(warehouse, mapping=current)
        additions[warehouse] = _normalized_entry(
            {
                "category": classification.category,
                "includeInInventory": classification.include_in_inventory,
                "pendingConfirmation": True,
            },
            warehouse=warehouse,
        )
    if not additions:
        return []
    merged = normalize_mapping({**current, **additions})
    settings.warehouse_mapping_json = merged
    settings.warehouse_mapping_updated_by = actor_email[:320]
    settings.warehouse_mapping_updated_at = timezone.now()
    settings.save(update_fields=["warehouse_mapping_json", "warehouse_mapping_updated_by", "warehouse_mapping_updated_at"])
    return list(additions)


def update_mapping(payload: object, actor_email: str) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"expectedMappingRevision", "mappings"}:
        raise InventoryApiError("仓库映射更新内容无效")
    expected = payload.get("expectedMappingRevision")
    mappings = payload.get("mappings")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise InventoryApiError("仓库映射版本无效")
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= MAX_MAPPINGS:
        raise InventoryApiError("仓库映射更新数量无效")
    updates: dict[str, dict[str, object]] = {}
    for item in mappings:
        if not isinstance(item, dict) or set(item) != {"warehouse", "category", "includeInInventory"}:
            raise InventoryApiError("仓库映射更新字段无效")
        warehouse = item.get("warehouse")
        if not isinstance(warehouse, str):
            raise InventoryApiError("仓库名称无效")
        if warehouse in updates:
            raise InventoryApiError(f"仓库“{warehouse}”在本次更新中重复")
        updates[warehouse] = _normalized_entry(
            {
                "category": item.get("category"),
                "includeInInventory": item.get("includeInInventory"),
            },
            warehouse=warehouse,
        )
    updates = normalize_mapping(updates)

    with transaction.atomic():
        lock_active_authority()
        settings = InventoryOperatingSettings.objects.select_for_update().get(id=1)
        current = effective_mapping(settings)
        if mapping_revision(current) != expected:
            raise InventoryApiError("仓库映射已被其他操作更新，请刷新后重试", code="version_conflict", status=409)
        merged = {**current, **updates}
        settings.warehouse_mapping_json = merged
        settings.warehouse_mapping_updated_by = actor_email[:320]
        settings.warehouse_mapping_updated_at = timezone.now()
        settings.save(update_fields=["warehouse_mapping_json", "warehouse_mapping_updated_by", "warehouse_mapping_updated_at"])
        bump_revision({"kind": "warehouse_mapping", "warehouses": sorted(updates)})
        return mapping_payload(settings)
