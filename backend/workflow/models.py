from __future__ import annotations

import uuid
from datetime import time

from django.db import models
from django.utils import timezone


class WorkflowDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_data_revisions"


class WorkflowWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="disabled")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="workflow_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["disabled", "postgres"]),
                name="workflow_auth_status_ck",
            ),
        ]


class WorkflowMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=24)
    source_path_digest = models.CharField(max_length=64)
    source_snapshot_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64, default="")
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    gap_counts = models.JSONField(default=dict)
    approved_run_id = models.CharField(max_length=64, default="")
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "workflow_migration_runs"


class WorkflowWriteRequestReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    body_sha256 = models.CharField(max_length=64)
    query_sha256 = models.CharField(max_length=64)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=240)
    actor_email = models.CharField(max_length=320)
    status = models.CharField(max_length=32, default="processing")
    claim_token = models.CharField(max_length=64, default="")
    response_status = models.PositiveIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "workflow_write_request_receipts"


class WorkflowOperationsWriteAuthority(models.Model):
    """Independent cutover fence for the legacy work-plan/operations scope."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="disabled")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_operations_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="workflow_ops_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["disabled", "postgres"]),
                name="workflow_ops_auth_status_ck",
            ),
        ]


class WorkflowOperationsMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=24)
    source_path_digest = models.CharField(max_length=64)
    source_snapshot_digest = models.CharField(max_length=64)
    target_snapshot_digest = models.CharField(max_length=64, default="")
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    approved_run_id = models.CharField(max_length=64, default="")
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "workflow_operations_migration_runs"


class WorkflowTask(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    title = models.CharField(max_length=160)
    work_content = models.TextField(default="")
    category = models.CharField(max_length=80, default="工作计划")
    owner = models.CharField(max_length=120, default="")
    shop_name = models.CharField(max_length=160, default="")
    start_date = models.CharField(max_length=10, default="待排期")
    due_date = models.CharField(max_length=10, default="待排期")
    status = models.CharField(max_length=16, default="待开始")
    priority = models.CharField(max_length=16, default="normal")
    version = models.PositiveBigIntegerField(default=1)
    mutation_token = models.CharField(max_length=128, default="")
    created_by = models.CharField(max_length=320)
    updated_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.CharField(max_length=320, default="")

    class Meta:
        db_table = "workflow_tasks"
        indexes = [
            models.Index(fields=["status", "created_at"], name="workflow_task_status_idx"),
            models.Index(fields=["deleted_at", "id"], name="workflow_task_deleted_idx"),
            models.Index(fields=["updated_at", "id"], name="workflow_task_updated_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gte=1), name="workflow_task_version_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["待开始", "工作中", "已完成"]),
                name="workflow_task_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=["high", "normal", "low"]),
                name="workflow_task_priority_ck",
            ),
        ]


class WorkflowTaskComment(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE, related_name="comments")
    content = models.TextField()
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_comments"
        indexes = [models.Index(fields=["task", "created_at", "id"], name="workflow_task_comment_idx")]


class WorkflowTaskActivityLog(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE, related_name="activity_logs")
    action = models.CharField(max_length=64)
    summary = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict)
    actor_email = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_activity_logs"
        indexes = [models.Index(fields=["task", "created_at", "id"], name="workflow_task_activity_idx")]


class WorkflowTaskReminder(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE, related_name="reminders")
    remind_at = models.DateTimeField()
    note = models.CharField(max_length=500, default="")
    status = models.CharField(max_length=16, default="pending")
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_reminders"
        indexes = [models.Index(fields=["task", "status", "remind_at"], name="workflow_task_reminder_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "dismissed", "sent"]),
                name="workflow_task_reminder_status_ck",
            ),
        ]


class WorkflowTaskTemplate(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=500, default="")
    title = models.CharField(max_length=160, default="")
    work_content = models.TextField(default="")
    category = models.CharField(max_length=80, default="工作计划")
    owner = models.CharField(max_length=120, default="")
    shop_name = models.CharField(max_length=160, default="")
    start_offset_days = models.IntegerField(default=0)
    due_offset_days = models.IntegerField(default=0)
    priority = models.CharField(max_length=16, default="normal")
    active = models.BooleanField(default=True)
    version = models.PositiveBigIntegerField(default=1)
    mutation_token = models.CharField(max_length=128, default="")
    created_by = models.CharField(max_length=320)
    updated_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_templates"
        indexes = [models.Index(fields=["active", "updated_at", "id"], name="workflow_template_active_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(version__gte=1), name="workflow_template_version_ck"),
            models.CheckConstraint(
                condition=models.Q(priority__in=["high", "normal", "low"]),
                name="workflow_template_priority_ck",
            ),
        ]


class WorkflowTaskEntityLink(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE, related_name="entity_links")
    entity_type = models.CharField(max_length=24)
    entity_id = models.CharField(max_length=240)
    label = models.CharField(max_length=240)
    url = models.URLField(max_length=1000, default="")
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_entity_links"
        indexes = [models.Index(fields=["task", "created_at", "id"], name="workflow_task_link_idx")]
        constraints = [
            models.UniqueConstraint(fields=["task", "entity_type", "entity_id"], name="workflow_task_link_uq"),
            models.CheckConstraint(
                condition=models.Q(entity_type__in=["shop", "product", "campaign", "order", "report", "url"]),
                name="workflow_task_link_type_ck",
            ),
        ]


class WorkflowTaskAttachment(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    task = models.ForeignKey(WorkflowTask, on_delete=models.CASCADE, related_name="attachments")
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    object_key = models.CharField(max_length=500, unique=True)
    created_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_task_attachments"
        indexes = [models.Index(fields=["task", "created_at", "id"], name="workflow_task_attach_idx")]


class WorkflowAttachmentCleanup(models.Model):
    object_key = models.CharField(primary_key=True, max_length=500)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, default="")
    enqueued_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_attachment_cleanup_queue"
        indexes = [models.Index(fields=["updated_at", "object_key"], name="workflow_attach_cleanup_idx")]


class WorkflowOperationRecord(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    record_type = models.CharField(max_length=16)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=24)
    priority = models.CharField(max_length=16, default="normal")
    platform = models.CharField(max_length=80, default="")
    channel = models.CharField(max_length=80, default="")
    shop_name = models.CharField(max_length=160)
    owner = models.CharField(max_length=120, default="")
    occurred_at = models.DateTimeField()
    due_at = models.DateTimeField(null=True, blank=True)
    content = models.TextField(default="")
    source = models.CharField(max_length=24, default="manual")
    source_ref = models.CharField(max_length=300, default="")
    reference_code = models.CharField(max_length=160, default="")
    version = models.PositiveBigIntegerField(default=1)
    mutation_token = models.CharField(max_length=128, default="")
    created_by = models.CharField(max_length=320)
    updated_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.CharField(max_length=320, default="")

    class Meta:
        db_table = "workflow_operation_records"
        indexes = [
            models.Index(fields=["record_type", "status", "occurred_at", "id"], name="workflow_ops_type_time_idx"),
            models.Index(fields=["shop_name", "record_type", "occurred_at", "id"], name="workflow_ops_shop_time_idx"),
            models.Index(fields=["updated_at", "id"], name="workflow_ops_updated_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(record_type__in=["inspection", "review"]), name="workflow_ops_type_ck"),
            models.CheckConstraint(condition=models.Q(priority__in=["high", "normal", "low"]), name="workflow_ops_priority_ck"),
            models.CheckConstraint(condition=models.Q(source__in=["manual", "system", "import", "integration"]), name="workflow_ops_source_ck"),
            models.CheckConstraint(condition=models.Q(version__gte=1), name="workflow_ops_version_ck"),
        ]


class WorkflowOperationActivity(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    record = models.ForeignKey(WorkflowOperationRecord, on_delete=models.CASCADE, related_name="activities")
    action = models.CharField(max_length=24)
    actor_email = models.CharField(max_length=320)
    actor_role = models.CharField(max_length=16)
    from_version = models.PositiveBigIntegerField(null=True, blank=True)
    to_version = models.PositiveBigIntegerField()
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_operation_activities"
        indexes = [models.Index(fields=["record", "to_version", "id"], name="workflow_ops_activity_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=["created", "updated", "status_changed", "deleted"]),
                name="workflow_ops_activity_action_ck",
            ),
        ]


class NewProductProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_name = models.CharField(max_length=200)
    supplier_name = models.CharField(max_length=200, default="")
    brand = models.CharField(max_length=120, default="")
    category = models.CharField(max_length=120, default="")
    erp_product_code = models.CharField(max_length=160, default="")
    sku_code = models.CharField(max_length=160, default="")
    spu_code = models.CharField(max_length=160, default="")
    product_image_url = models.URLField(max_length=1000, default="")
    proposed_by = models.CharField(max_length=120, default="")
    proposed_date = models.DateField(db_index=True)
    owner = models.CharField(max_length=120, default="")
    target_launch_date = models.DateField(null=True, blank=True, db_index=True)
    lifecycle_status = models.CharField(max_length=16, default="active", db_index=True)
    priority = models.CharField(max_length=16, default="normal", db_index=True)
    recommended_price_cents = models.BigIntegerField(null=True, blank=True)
    approved_price_cents = models.BigIntegerField(null=True, blank=True)
    estimated_gross_margin_bps = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=24, default="manual", db_index=True)
    source_ref = models.CharField(max_length=200, default="")
    shop_plan = models.TextField(null=True, blank=True, default=None)
    notes = models.TextField(default="")
    version = models.PositiveBigIntegerField(default=1)
    created_by = models.CharField(max_length=320)
    updated_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.CharField(max_length=320, default="")

    class Meta:
        db_table = "workflow_new_product_projects"
        indexes = [
            models.Index(fields=["lifecycle_status", "target_launch_date"], name="workflow_np_status_due_idx"),
            models.Index(fields=["supplier_name", "proposed_date"], name="workflow_np_supplier_idx"),
            models.Index(fields=["updated_at", "id"], name="workflow_np_updated_idx"),
        ]


class NewProductTarget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NewProductProject, on_delete=models.CASCADE, related_name="targets")
    platform = models.CharField(max_length=80)
    shop_name = models.CharField(max_length=160)
    channel = models.CharField(max_length=80, default="")
    listing_sku = models.CharField(max_length=160, default="")
    listing_url = models.URLField(max_length=1000, default="")
    status = models.CharField(max_length=24, default="pending")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_new_product_targets"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "platform", "shop_name"],
                name="workflow_np_target_identity_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["platform", "shop_name"], name="workflow_np_target_shop_idx"),
        ]


class NewProductStage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NewProductProject, on_delete=models.CASCADE, related_name="stages")
    stage_key = models.CharField(max_length=24)
    status = models.CharField(max_length=24, default="not_started", db_index=True)
    owner = models.CharField(max_length=120, default="")
    planned_due_date = models.DateField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    blocker = models.CharField(max_length=500, default="")
    notes = models.TextField(default="")
    evidence_url = models.URLField(max_length=1000, default="")
    evidence_label = models.CharField(max_length=160, default="")
    version = models.PositiveBigIntegerField(default=1)
    updated_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_new_product_stages"
        constraints = [
            models.UniqueConstraint(fields=["project", "stage_key"], name="workflow_np_stage_uq"),
        ]
        indexes = [
            models.Index(fields=["stage_key", "status", "planned_due_date"], name="workflow_np_stage_state_idx"),
        ]


class NewProductActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(NewProductProject, on_delete=models.CASCADE, related_name="activities")
    action = models.CharField(max_length=64)
    actor_email = models.CharField(max_length=320)
    actor_role = models.CharField(max_length=16)
    from_version = models.PositiveBigIntegerField(null=True, blank=True)
    to_version = models.PositiveBigIntegerField()
    stage_key = models.CharField(max_length=24, default="")
    from_status = models.CharField(max_length=24, default="")
    to_status = models.CharField(max_length=24, default="")
    changed_fields = models.JSONField(default=list)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "workflow_new_product_activities"
        indexes = [
            models.Index(fields=["project", "created_at"], name="workflow_np_activity_idx"),
        ]


class NewProductLine(models.Model):
    """A user-named monitored product line backed by one or more Jackyun codes."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    match_terms = models.JSONField(default=list)
    product_image_url = models.URLField(max_length=1000, default="")
    product_image_file_name = models.CharField(max_length=255, default="")
    product_image_mime_type = models.CharField(max_length=32, default="")
    product_image_size_bytes = models.PositiveIntegerField(default=0)
    product_image_sha256 = models.CharField(max_length=64, default="")
    product_image_bytes = models.BinaryField(null=True, blank=True, editable=False)
    monitoring_start_date = models.DateField(db_index=True)
    weekly_unit_target = models.PositiveBigIntegerField(null=True, blank=True)
    weekly_sales_target_cents = models.PositiveBigIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    version = models.PositiveBigIntegerField(default=1)
    created_by = models.CharField(max_length=320)
    updated_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "workflow_new_product_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(deleted_at__isnull=True),
                name="workflow_npl_active_name_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["active", "monitoring_start_date"], name="workflow_npl_active_start_idx"),
            models.Index(fields=["updated_at", "id"], name="workflow_npl_updated_idx"),
        ]


class NewProductLineCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_line = models.ForeignKey(NewProductLine, on_delete=models.CASCADE, related_name="codes")
    product_code = models.CharField(max_length=200, unique=True)
    product_name = models.CharField(max_length=500)
    source = models.CharField(max_length=16, default="manual")
    source_batch_id = models.CharField(max_length=200, default="")
    active = models.BooleanField(default=True, db_index=True)
    added_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_new_product_line_codes"
        indexes = [
            models.Index(fields=["product_line", "active"], name="workflow_npl_code_line_idx"),
            models.Index(fields=["source_batch_id"], name="workflow_npl_code_batch_idx"),
        ]


class NewProductWeeklyReportConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    target_group_name = models.CharField(max_length=200, default="测试群聊")
    robot_name = models.CharField(max_length=160, default="志高助手")
    send_weekday = models.PositiveSmallIntegerField(default=0)
    send_local_time = models.TimeField(default=time(9, 30))
    version = models.PositiveBigIntegerField(default=1)
    updated_by = models.CharField(max_length=320, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "workflow_new_product_weekly_report_config"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="workflow_npl_report_singleton_ck"),
            models.CheckConstraint(condition=models.Q(send_weekday__gte=0, send_weekday__lte=6), name="workflow_npl_weekday_ck"),
        ]


class NewProductWeeklyDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    week_start = models.DateField()
    week_end = models.DateField()
    target_group_name = models.CharField(max_length=200)
    robot_name = models.CharField(max_length=160)
    idempotency_key = models.CharField(max_length=64, unique=True)
    report_sha256 = models.CharField(max_length=64)
    data_cutoff_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default="processing")
    attempt_count = models.PositiveIntegerField(default=1)
    provider_receipt = models.CharField(max_length=500, default="")
    error_code = models.CharField(max_length=120, default="")
    claimed_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "workflow_new_product_weekly_deliveries"
        indexes = [
            models.Index(fields=["week_start", "status"], name="workflow_npl_delivery_week_idx"),
            models.Index(fields=["created_at"], name="workflow_npl_delivery_time_idx"),
        ]
