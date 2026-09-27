# Generated for the structured TERUISI workflow domain.

import django.db.models.deletion
import django.utils.timezone
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WorkflowDataRevision",
            fields=[
                ("domain", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("revision", models.BigIntegerField(default=0)),
                ("source_digest", models.CharField(default="", max_length=64)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "workflow_data_revisions"},
        ),
        migrations.CreateModel(
            name="WorkflowWriteAuthority",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(default="disabled", max_length=16)),
                ("authority_epoch", models.UUIDField(blank=True, null=True)),
                ("cutover_id", models.CharField(default="", max_length=128)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "workflow_write_authority",
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("id", 1)), name="workflow_auth_singleton_ck"),
                    models.CheckConstraint(condition=models.Q(("status__in", ["disabled", "postgres"])), name="workflow_auth_status_ck"),
                ],
            },
        ),
        migrations.CreateModel(
            name="WorkflowWriteRequestReceipt",
            fields=[
                ("request_id", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("body_sha256", models.CharField(max_length=64)),
                ("query_sha256", models.CharField(max_length=64)),
                ("method", models.CharField(max_length=8)),
                ("path", models.CharField(max_length=240)),
                ("actor_email", models.CharField(max_length=320)),
                ("status", models.CharField(default="processing", max_length=32)),
                ("claim_token", models.CharField(default="", max_length=64)),
                ("response_status", models.PositiveIntegerField(default=0)),
                ("response_payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField()),
            ],
            options={"db_table": "workflow_write_request_receipts"},
        ),
        migrations.CreateModel(
            name="NewProductProject",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_name", models.CharField(max_length=200)),
                ("supplier_name", models.CharField(default="", max_length=200)),
                ("brand", models.CharField(default="", max_length=120)),
                ("category", models.CharField(default="", max_length=120)),
                ("erp_product_code", models.CharField(default="", max_length=160)),
                ("sku_code", models.CharField(default="", max_length=160)),
                ("spu_code", models.CharField(default="", max_length=160)),
                ("product_image_url", models.URLField(default="", max_length=1000)),
                ("proposed_by", models.CharField(default="", max_length=120)),
                ("proposed_date", models.DateField(db_index=True)),
                ("owner", models.CharField(default="", max_length=120)),
                ("target_launch_date", models.DateField(blank=True, db_index=True, null=True)),
                ("lifecycle_status", models.CharField(db_index=True, default="active", max_length=16)),
                ("priority", models.CharField(db_index=True, default="normal", max_length=16)),
                ("recommended_price_cents", models.BigIntegerField(blank=True, null=True)),
                ("approved_price_cents", models.BigIntegerField(blank=True, null=True)),
                ("estimated_gross_margin_bps", models.IntegerField(blank=True, null=True)),
                ("source", models.CharField(db_index=True, default="manual", max_length=24)),
                ("source_ref", models.CharField(default="", max_length=200)),
                ("notes", models.TextField(default="")),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("created_by", models.CharField(max_length=320)),
                ("updated_by", models.CharField(max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("deleted_by", models.CharField(default="", max_length=320)),
            ],
            options={
                "db_table": "workflow_new_product_projects",
                "indexes": [
                    models.Index(fields=["lifecycle_status", "target_launch_date"], name="workflow_np_status_due_idx"),
                    models.Index(fields=["supplier_name", "proposed_date"], name="workflow_np_supplier_idx"),
                    models.Index(fields=["updated_at", "id"], name="workflow_np_updated_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="NewProductActivity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=64)),
                ("actor_email", models.CharField(max_length=320)),
                ("actor_role", models.CharField(max_length=16)),
                ("from_version", models.PositiveBigIntegerField(blank=True, null=True)),
                ("to_version", models.PositiveBigIntegerField()),
                ("stage_key", models.CharField(default="", max_length=24)),
                ("from_status", models.CharField(default="", max_length=24)),
                ("to_status", models.CharField(default="", max_length=24)),
                ("changed_fields", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="activities", to="workflow.newproductproject")),
            ],
            options={
                "db_table": "workflow_new_product_activities",
                "indexes": [models.Index(fields=["project", "created_at"], name="workflow_np_activity_idx")],
            },
        ),
        migrations.CreateModel(
            name="NewProductStage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("stage_key", models.CharField(max_length=24)),
                ("status", models.CharField(db_index=True, default="not_started", max_length=24)),
                ("owner", models.CharField(default="", max_length=120)),
                ("planned_due_date", models.DateField(blank=True, db_index=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("blocker", models.CharField(default="", max_length=500)),
                ("notes", models.TextField(default="")),
                ("evidence_url", models.URLField(default="", max_length=1000)),
                ("evidence_label", models.CharField(default="", max_length=160)),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("updated_by", models.CharField(default="", max_length=320)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stages", to="workflow.newproductproject")),
            ],
            options={
                "db_table": "workflow_new_product_stages",
                "indexes": [models.Index(fields=["stage_key", "status", "planned_due_date"], name="workflow_np_stage_state_idx")],
                "constraints": [models.UniqueConstraint(fields=("project", "stage_key"), name="workflow_np_stage_uq")],
            },
        ),
        migrations.CreateModel(
            name="NewProductTarget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("platform", models.CharField(max_length=80)),
                ("shop_name", models.CharField(max_length=160)),
                ("channel", models.CharField(default="", max_length=80)),
                ("listing_sku", models.CharField(default="", max_length=160)),
                ("listing_url", models.URLField(default="", max_length=1000)),
                ("status", models.CharField(default="pending", max_length=24)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("project", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="targets", to="workflow.newproductproject")),
            ],
            options={
                "db_table": "workflow_new_product_targets",
                "indexes": [models.Index(fields=["platform", "shop_name"], name="workflow_np_target_shop_idx")],
                "constraints": [models.UniqueConstraint(fields=("project", "platform", "shop_name"), name="workflow_np_target_identity_uq")],
            },
        ),
    ]
