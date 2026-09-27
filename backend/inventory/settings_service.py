from __future__ import annotations

from django.db import transaction

from .errors import InventoryApiError
from .models import InventoryOperatingSettings
from .revisions import bump_revision
from .write_requests import lock_active_authority


FIELDS = {
    "targetDays": ("target_days", 1, 365),
    "criticalDays": ("critical_days", 1, 120),
    "slowDays": ("slow_days", 1, 730),
    "stagnantDays": ("stagnant_days", 1, 1_460),
}
BOOLEAN_FIELDS = {
    "autoReplenishment": "auto_replenishment",
    "inventoryAlert": "inventory_alert",
    "allowNegativeInventory": "allow_negative_inventory",
}


def settings_payload(settings: InventoryOperatingSettings) -> dict[str, object]:
    return {
        "targetDays": int(settings.target_days),
        "criticalDays": int(settings.critical_days),
        "slowDays": int(settings.slow_days),
        "stagnantDays": int(settings.stagnant_days),
        "autoReplenishment": bool(settings.auto_replenishment),
        "inventoryAlert": bool(settings.inventory_alert),
        "allowNegativeInventory": bool(settings.allow_negative_inventory),
        "updatedAt": settings.updated_at.isoformat() if settings.updated_at else None,
        "updatedBy": settings.updated_by or None,
    }


def read_settings() -> dict[str, object]:
    return settings_payload(InventoryOperatingSettings.objects.get(id=1))


def update_settings(payload: object, actor_email: str) -> dict[str, object]:
    if not isinstance(payload, dict) or not payload:
        raise InventoryApiError("设置内容不能为空")
    if not set(payload).issubset(set(FIELDS) | set(BOOLEAN_FIELDS)):
        raise InventoryApiError("设置包含未知字段")
    updates: dict[str, object] = {}
    for public_name, (field, minimum, maximum) in FIELDS.items():
        if public_name not in payload:
            continue
        value = payload[public_name]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise InventoryApiError(f"{public_name} 必须是 {minimum} 到 {maximum} 的整数")
        updates[field] = value
    for public_name, field in BOOLEAN_FIELDS.items():
        if public_name not in payload:
            continue
        value = payload[public_name]
        if not isinstance(value, bool):
            raise InventoryApiError(f"{public_name} 必须是布尔值")
        updates[field] = value
    with transaction.atomic():
        lock_active_authority()
        settings = InventoryOperatingSettings.objects.select_for_update().get(id=1)
        for field, value in updates.items():
            setattr(settings, field, value)
        settings.updated_by = actor_email[:320]
        settings.save()
        bump_revision({"kind": "settings", "fields": sorted(payload)})
        return settings_payload(settings)
