"""Expire manual healthy settings only when current physical stock increases."""
from .models import GuangdongMonitorAudit, GuangdongMonitorItem, InventoryStockLine, ReplenishmentPlanItem
from .query import _latest_batch


def healthy_stock_baseline():
    latest = _latest_batch("stock")
    if latest is None:
        return {}
    return dict(InventoryStockLine.objects.filter(
        batch_id=latest.id, warehouse="广东仓",
        product_code__in=GuangdongMonitorItem.objects.filter(risk_override="healthy").values("product_code"),
    ).values_list("product_code", "on_hand_quantity"))


def reset_increased_healthy(batch, baseline, actor):
    latest = _latest_batch("stock")
    if not baseline or latest is None or latest.id != batch.id:
        return
    current = dict(InventoryStockLine.objects.filter(
        batch_id=batch.id, warehouse="广东仓",
        product_code__in=GuangdongMonitorItem.objects.filter(risk_override="healthy").values("product_code"),
    ).values_list("product_code", "on_hand_quantity"))
    for item in GuangdongMonitorItem.objects.select_for_update().filter(risk_override="healthy").order_by("product_code"):
        code = item.product_code
        if code not in baseline or code not in current or current[code] <= baseline[code]:
            continue
        before = {"risk": item.risk_override, "riskReason": item.risk_reason_override}
        item.risk_override = None
        item.risk_reason_override = None
        item.updated_by = actor
        item.save(update_fields=["risk_override", "risk_reason_override", "updated_by", "updated_at"])
        # A pending old order must not mask the restored automatic classification.
        plans = ReplenishmentPlanItem.objects.select_for_update().filter(
            product_code=code, warehouse="广东仓", guangdong_health__active=True,
        )
        for plan in plans:
            plan.guangdong_health = {**plan.guangdong_health, "active": False,
                                    "batchId": batch.id, "snapshotDate": batch.snapshot_date.isoformat(),
                                    "quantity": int(current[code])}
            plan.save(update_fields=["guangdong_health"])
        saved = GuangdongMonitorItem.objects.get(product_code=code)
        if saved.risk_override is not None or saved.risk_reason_override is not None:
            raise RuntimeError("Guangdong automatic risk reset readback failed")
        GuangdongMonitorAudit.objects.create(
            action="stock_risk_reset", source="inventory_stock_import", actor=actor,
            status="saved", result={"productCode": code, "batchId": batch.id,
                "before": before, "after": {"risk": None, "riskReason": None},
                "previousOnHandQuantity": int(baseline[code]), "onHandQuantity": int(current[code])},
        )
