from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sales", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="salesmigrationrun",
            name="canonical_format_version",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="salesmigrationrun",
            name="approved_run_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="salesmigrationrun",
            name="consumed_by_run_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="salesmigrationrun",
            name="approval_consumed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="salesmigrationrun",
            constraint=models.UniqueConstraint(
                condition=models.Q(("approved_run_id__gt", "")),
                fields=("approved_run_id",),
                name="uniq_sales_mig_approval",
            ),
        ),
    ]
