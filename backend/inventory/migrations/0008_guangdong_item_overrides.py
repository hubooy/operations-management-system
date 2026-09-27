from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0007_guangdong_monitor"),
    ]

    operations = [
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="buyer_override",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="buffer_days_override",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="lead_days_override",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="operator_name_override",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddConstraint(
            model_name="guangdongmonitoritem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(buffer_days_override__isnull=True, lead_days_override__isnull=True)
                    | models.Q(
                        buffer_days_override__gte=0,
                        buffer_days_override__lte=365,
                        buffer_days_override__isnull=False,
                        lead_days_override__gte=1,
                        lead_days_override__lte=365,
                        lead_days_override__isnull=False,
                    )
                ),
                name="inv_gd_item_cycle_pair",
            ),
        ),
    ]
