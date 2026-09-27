import datetime
import django.db.models.deletion
import django.utils.timezone
import uuid

from django.db import migrations, models


def seed_report_config(apps, _schema_editor):
    Config = apps.get_model("workflow", "NewProductWeeklyReportConfig")
    Config.objects.get_or_create(id=1)


class Migration(migrations.Migration):
    dependencies = [("workflow", "0003_workflowmigrationrun_and_authority_run")]

    operations = [
        migrations.CreateModel(
            name="NewProductLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("match_terms", models.JSONField(default=list)),
                ("monitoring_start_date", models.DateField(db_index=True)),
                ("tracking_weeks", models.PositiveSmallIntegerField(default=8)),
                ("weekly_unit_target", models.PositiveBigIntegerField(blank=True, null=True)),
                ("weekly_sales_target_cents", models.PositiveBigIntegerField(blank=True, null=True)),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("created_by", models.CharField(max_length=320)),
                ("updated_by", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                "db_table": "workflow_new_product_lines",
                "constraints": [
                    models.UniqueConstraint(condition=models.Q(("deleted_at__isnull", True)), fields=("name",), name="workflow_npl_active_name_uq"),
                ],
                "indexes": [
                    models.Index(fields=["active", "monitoring_start_date"], name="workflow_npl_active_start_idx"),
                    models.Index(fields=["updated_at", "id"], name="workflow_npl_updated_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NewProductLineCode",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_code", models.CharField(max_length=200, unique=True)),
                ("product_name", models.CharField(max_length=500)),
                ("source", models.CharField(default="manual", max_length=16)),
                ("source_batch_id", models.CharField(default="", max_length=200)),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("added_by", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("product_line", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="codes", to="workflow.newproductline")),
            ],
            options={
                "db_table": "workflow_new_product_line_codes",
                "indexes": [
                    models.Index(fields=["product_line", "active"], name="workflow_npl_code_line_idx"),
                    models.Index(fields=["source_batch_id"], name="workflow_npl_code_batch_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NewProductWeeklyReportConfig",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("enabled", models.BooleanField(default=False)),
                ("target_group_name", models.CharField(default="测试群聊", max_length=200)),
                ("robot_name", models.CharField(default="志高助手", max_length=160)),
                ("send_weekday", models.PositiveSmallIntegerField(default=0)),
                ("send_local_time", models.TimeField(default=datetime.time(9, 30))),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("updated_by", models.CharField(default="", max_length=320)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "workflow_new_product_weekly_report_config",
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("id", 1)), name="workflow_npl_report_singleton_ck"),
                    models.CheckConstraint(condition=models.Q(("send_weekday__gte", 0), ("send_weekday__lte", 6)), name="workflow_npl_weekday_ck"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NewProductWeeklyDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("week_start", models.DateField()),
                ("week_end", models.DateField()),
                ("target_group_name", models.CharField(max_length=200)),
                ("robot_name", models.CharField(max_length=160)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("report_sha256", models.CharField(max_length=64)),
                ("data_cutoff_date", models.DateField(blank=True, null=True)),
                ("status", models.CharField(default="processing", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=1)),
                ("provider_receipt", models.CharField(default="", max_length=500)),
                ("error_code", models.CharField(default="", max_length=120)),
                ("claimed_by", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "workflow_new_product_weekly_deliveries",
                "indexes": [
                    models.Index(fields=["week_start", "status"], name="workflow_npl_delivery_week_idx"),
                    models.Index(fields=["created_at"], name="workflow_npl_delivery_time_idx"),
                ],
            },
        ),
        migrations.RunPython(seed_report_config, migrations.RunPython.noop),
    ]
