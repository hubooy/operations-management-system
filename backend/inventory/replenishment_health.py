"""Persist the Guangdong order-to-first-stock-increase observation cycle.

Writers call these helpers while holding the inventory stock-scope lock. Readers
never start or advance a cycle, and stock imports never change plan timestamps.
"""
from .errors import InventoryApiError
from .models import InventoryImportScopeHead, InventoryStockLine, ReplenishmentPlanItem


def lock_stock_scope():
    scope = InventoryImportScopeHead.objects.select_for_update().filter(dataset="stock").first()
    if scope is None or scope.status != "ready":
        raise InventoryApiError("库存快照正在更新，请刷新后重试备货计划", code="version_conflict", status=409)
    return scope


def start_cycle(plan, previous_quantity=0):
    if plan.warehouse != "广东仓":
        return
    if plan.status == "cancelled" or plan.planned_quantity <= 0:
        plan.guangdong_health = {}
        return
    if plan.planned_quantity <= previous_quantity:
        return
    from .query import _latest_batch

    latest = _latest_batch("stock")
    stock = InventoryStockLine.objects.filter(
        batch_id=latest.id, warehouse="广东仓", product_code=plan.product_code,
    ).first() if latest else None
    plan.guangdong_health = {
        "active": True,
        "batchId": latest.id if latest else None,
        "snapshotDate": latest.snapshot_date.isoformat() if latest else None,
        "quantity": int(stock.on_hand_quantity) if stock else None,
    }


def advance_cycles(batch):
    """Only a newly published current snapshot can release existing cycles."""
    from .query import _latest_batch

    latest = _latest_batch("stock")
    if latest is None or latest.id != batch.id:
        return
    stock = dict(InventoryStockLine.objects.filter(
        batch_id=batch.id, warehouse="广东仓",
    ).values_list("product_code", "on_hand_quantity"))
    pending = ReplenishmentPlanItem.objects.filter(
        warehouse="广东仓", planned_quantity__gt=0, guangdong_health__active=True,
    ).exclude(status="cancelled").order_by("id")
    changed = []
    for plan in pending.iterator(chunk_size=500):
        state = plan.guangdong_health
        if state.get("batchId") == batch.id or plan.product_code not in stock:
            continue
        snapshot = batch.snapshot_date.isoformat()
        if state.get("snapshotDate") and snapshot < state["snapshotDate"]:
            continue
        quantity = int(stock[plan.product_code])
        previous = state.get("quantity")
        plan.guangdong_health = {
            "active": not (previous is not None and quantity > previous),
            "batchId": batch.id, "snapshotDate": snapshot, "quantity": quantity,
        }
        changed.append(plan)
        if len(changed) == 500:
            ReplenishmentPlanItem.objects.bulk_update(changed, ["guangdong_health"], batch_size=500)
            changed = []
    if changed:
        ReplenishmentPlanItem.objects.bulk_update(changed, ["guangdong_health"], batch_size=500)


def waiting_for_stock(plan):
    return bool(plan and plan.warehouse == "广东仓" and plan.status != "cancelled"
                and plan.planned_quantity > 0 and plan.guangdong_health.get("active") is True)
