from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0008_guangdong_item_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="risk_override",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="guangdongmonitoritem",
            name="risk_reason_override",
            field=models.CharField(blank=True, max_length=1000, null=True),
        ),
        migrations.AddConstraint(
            model_name="guangdongmonitoritem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(risk_override__isnull=True, risk_reason_override__isnull=True)
                    | (
                        models.Q(
                            risk_override__in=["no_stock", "urgent", "warning", "stale", "unknown", "healthy"],
                            risk_reason_override__isnull=False,
                        )
                        & ~models.Q(risk_reason_override="")
                    )
                ),
                name="inv_gd_item_risk_pair",
            ),
        ),
    ]
