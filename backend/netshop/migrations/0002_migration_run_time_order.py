from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("netshop", "0001_initial")]

    operations = [
        migrations.AddConstraint(
            model_name="netshopmigrationrun",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(completed_at__isnull=True)
                    | models.Q(completed_at__gte=models.F("created_at"))
                ),
                name="net_migration_time_order_ck",
            ),
        )
    ]
