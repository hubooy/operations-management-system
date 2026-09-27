import hashlib
import json

from django.db import migrations, models


def baseline_existing_plans(apps, schema_editor):
    alias = schema_editor.connection.alias
    Stock = apps.get_model("inventory", "InventoryStockLine")
    Batch = apps.get_model("inventory", "InventoryImportBatch")
    Plan = apps.get_model("inventory", "ReplenishmentPlanItem")
    latest = Batch.objects.using(alias).filter(
        dataset="stock", status="completed",
        id__in=Stock.objects.using(alias).values("batch_id"),
    ).order_by("-snapshot_date", "-completed_at", "-id").first()
    stock = dict(Stock.objects.using(alias).filter(
        batch_id=latest.id, warehouse="广东仓",
    ).values_list("product_code", "on_hand_quantity")) if latest else {}
    pending = Plan.objects.using(alias).filter(warehouse="广东仓", planned_quantity__gt=0, guangdong_health={}).exclude(status="cancelled")
    changed = []
    count = 0
    for plan in pending.iterator(chunk_size=500):
        plan.guangdong_health = {
            "active": True, "batchId": latest.id if latest else None,
            "snapshotDate": latest.snapshot_date.isoformat() if latest else None,
            "quantity": int(stock[plan.product_code]) if plan.product_code in stock else None,
        }
        changed.append(plan)
        count += 1
        if len(changed) == 500:
            Plan.objects.using(alias).bulk_update(changed, ["guangdong_health"], batch_size=500)
            changed = []
    if changed:
        Plan.objects.using(alias).bulk_update(changed, ["guangdong_health"], batch_size=500)
    if count:
        Revision = apps.get_model("inventory", "InventoryDataRevision")
        revision = Revision.objects.using(alias).select_for_update().get(domain="inventory")
        payload = {"previous": int(revision.revision), "reason": {"kind": "guangdong_health_baseline", "count": count}}
        revision.revision += 1
        revision.source_digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        revision.save(using=alias, update_fields=["revision", "source_digest", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("inventory", "0009_guangdong_item_risk_overrides")]
    operations = [
        migrations.AddField(model_name="replenishmentplanitem", name="guangdong_health", field=models.JSONField(default=dict)),
        migrations.RunPython(baseline_existing_plans, migrations.RunPython.noop),
    ]
