"""Read-only remaining order quantities from complete daily physical snapshots."""
from datetime import date, timedelta

from .errors import InventoryApiError
from .models import InventoryImportBatch, InventoryStockLine

MAX_HISTORY_DAYS = 366
MAX_HISTORY_ROWS = 100_000


def add_remaining_quantities(items, latest):
    eligible = {}
    for item in items:
        item.update(replenishmentStockIncreaseQuantity=None,
                    replenishmentRemainingQuantity=None, replenishmentRemainingReason="")
        order_date = item.get("latestReplenishmentOrderDate")
        if item.get("replenishmentQuantity") is None:
            item["replenishmentRemainingReason"] = "暂无备货计划"
        elif not order_date:
            item["replenishmentRemainingReason"] = "下单日期待完善"
        elif latest is None:
            item["replenishmentRemainingReason"] = "库存快照待更新"
        else:
            order_date = date.fromisoformat(order_date)
            if order_date > latest.snapshot_date:
                item["replenishmentRemainingReason"] = "下单日期晚于库存快照，待更新"
            elif (latest.snapshot_date - order_date).days > MAX_HISTORY_DAYS:
                item["replenishmentRemainingReason"] = "下单日期超出可核算范围"
            else:
                eligible[item["productCode"]] = (item, order_date)
    if not eligible:
        return

    first_date = min(order_date for _, order_date in eligible.values())
    batches = InventoryImportBatch.objects.filter(
        dataset="stock", status="completed", snapshot_date__gte=first_date,
        snapshot_date__lte=latest.snapshot_date,
    ).values("id")
    rows = list(InventoryStockLine.objects.filter(
        batch_id__in=batches, warehouse="广东仓", product_code__in=eligible,
        snapshot_date__gte=first_date, snapshot_date__lte=latest.snapshot_date,
    ).order_by("product_code", "snapshot_date").values_list(
        "product_code", "snapshot_date", "on_hand_quantity",
    )[:MAX_HISTORY_ROWS + 1])
    if len(rows) > MAX_HISTORY_ROWS:
        raise InventoryApiError("备货库存历史超过核算范围，请缩小筛选范围", status=503)
    history = {code: {} for code in eligible}
    for code, snapshot_date, quantity in rows:
        if snapshot_date < eligible[code][1]:
            continue
        daily = history[code]
        # An ambiguous date cannot supply a trustworthy baseline or delta.
        daily[snapshot_date] = None if snapshot_date in daily else int(quantity)

    for code, (item, order_date) in eligible.items():
        daily = history[code]
        day = order_date
        previous = None
        increase = 0
        while day <= latest.snapshot_date:
            quantity = daily.get(day)
            if quantity is None:
                item["replenishmentRemainingReason"] = "下单当日或后续广东仓库存记录缺失或重复，待核算"
                break
            if previous is not None:
                increase += max(0, quantity - previous)
            previous = quantity
            day += timedelta(days=1)
        else:
            item["replenishmentStockIncreaseQuantity"] = increase
            # Preserve the user's subtraction formula, including over-receipt.
            item["replenishmentRemainingQuantity"] = item["replenishmentQuantity"] - increase
