from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("sales", "0009_postgres_raw_upload_payload"),
        ("inventory", "0006_replenishment_group_delivery"),
    ]
    operations = [
        migrations.CreateModel(
            name="BiMigrationRun",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("plan_id", models.CharField(max_length=72, unique=True)),
                ("status", models.CharField(default="applied", max_length=16)),
                ("contract_version", models.CharField(max_length=64)),
                ("source_digest", models.CharField(max_length=64)),
                ("source_revisions_json", models.JSONField(default=dict)),
                ("source_counts_json", models.JSONField(default=dict)),
                ("source_snapshot_json", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "bi_migration_runs",
                "indexes": [models.Index(fields=["status", "created_at"], name="bi_migration_status_idx")],
            },
        ),
    ]
