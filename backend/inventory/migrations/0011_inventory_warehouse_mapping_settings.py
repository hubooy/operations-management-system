from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0010_replenishment_health")]

    operations = [
        migrations.AddField(
            model_name="inventoryoperatingsettings",
            name="warehouse_mapping_json",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="inventoryoperatingsettings",
            name="warehouse_mapping_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inventoryoperatingsettings",
            name="warehouse_mapping_updated_by",
            field=models.CharField(default="", max_length=320),
        ),
    ]
