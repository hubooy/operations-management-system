from django.db import migrations, models
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_stock_supplier_warehouse_mapping")]

    operations = [
        migrations.CreateModel(
            name="ReplenishmentGroupDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("plan_ids", models.JSONField(default=list)),
                ("target_group_name", models.CharField(max_length=200)),
                ("robot_name", models.CharField(max_length=160)),
                ("message_sha256", models.CharField(max_length=64)),
                ("message_text", models.TextField()),
                ("status", models.CharField(default="claimed", max_length=16)),
                ("provider_receipt", models.CharField(default="", max_length=500)),
                ("error_code", models.CharField(default="", max_length=120)),
                ("claimed_by", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "inventory_replenishment_group_deliveries",
                "indexes": [
                    models.Index(fields=["created_at"], name="inv_group_delivery_time_idx"),
                    models.Index(fields=["status", "updated_at"], name="inv_group_delivery_status_idx"),
                ],
            },
        ),
    ]
