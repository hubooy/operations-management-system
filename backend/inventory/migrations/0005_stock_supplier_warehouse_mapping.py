from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0004_replenishment_dingtalk_sync")]

    operations = [
        migrations.AddField(
            model_name="inventorystockline",
            name="warehouse_category",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="inventorystockline",
            name="include_in_inventory",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="inventorystockline",
            name="supplier",
            field=models.TextField(default=""),
        ),
    ]
