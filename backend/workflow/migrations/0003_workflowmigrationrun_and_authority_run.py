from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workflow", "0002_seed_control_rows")]

    operations = [
        migrations.AddField(
            model_name="workflowwriteauthority",
            name="migration_verify_run_id",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.CreateModel(
            name="WorkflowMigrationRun",
            fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("mode", models.CharField(max_length=16)),
                ("status", models.CharField(max_length=24)),
                ("source_path_digest", models.CharField(max_length=64)),
                ("source_snapshot_digest", models.CharField(max_length=64)),
                ("target_snapshot_digest", models.CharField(default="", max_length=64)),
                ("source_counts", models.JSONField(default=dict)),
                ("target_counts", models.JSONField(default=dict)),
                ("gap_counts", models.JSONField(default=dict)),
                ("approved_run_id", models.CharField(default="", max_length=64)),
                ("manifest", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "workflow_migration_runs"},
        ),
    ]
