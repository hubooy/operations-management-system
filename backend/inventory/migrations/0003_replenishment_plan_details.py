from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_seed_control_rows")]

    operations = [
        migrations.AddField(model_name="replenishmentplanitem", name="brand", field=models.TextField(default="")),
        migrations.AddField(model_name="replenishmentplanitem", name="category", field=models.TextField(default="")),
        migrations.AddField(model_name="replenishmentplanitem", name="supplier", field=models.TextField(default="")),
        migrations.AddField(model_name="replenishmentplanitem", name="buyer", field=models.CharField(default="", max_length=200)),
        migrations.AddField(model_name="replenishmentplanitem", name="operator_name", field=models.CharField(default="", max_length=200)),
        migrations.AddField(model_name="replenishmentplanitem", name="department", field=models.CharField(default="", max_length=200)),
        migrations.AddField(model_name="replenishmentplanitem", name="plan_type", field=models.CharField(default="", max_length=100)),
        migrations.AddField(model_name="replenishmentplanitem", name="order_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="replenishmentplanitem", name="expected_arrival_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="replenishmentplanitem", name="requires_inspection", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="replenishmentplanitem", name="current_stock_quantity", field=models.BigIntegerField(default=0)),
        migrations.AddField(model_name="replenishmentplanitem", name="sales_30d_quantity", field=models.BigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="replenishmentplanitem", name="notes", field=models.TextField(default="")),
    ]
