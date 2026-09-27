from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class GuangdongMonitorItem(models.Model):
    product_code = models.CharField(primary_key=True, max_length=200)
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=1000, default="")
    lead_days_override = models.PositiveSmallIntegerField(null=True, blank=True)
    buffer_days_override = models.PositiveSmallIntegerField(null=True, blank=True)
    operator_name_override = models.CharField(max_length=200, null=True, blank=True)
    buyer_override = models.CharField(max_length=200, null=True, blank=True)
    risk_override = models.CharField(max_length=32, null=True, blank=True)
    risk_reason_override = models.CharField(max_length=1000, null=True, blank=True)
    updated_by = models.CharField(max_length=320)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_guangdong_monitor_items"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(lead_days_override__isnull=True, buffer_days_override__isnull=True)
                    | models.Q(
                        lead_days_override__gte=1,
                        lead_days_override__lte=365,
                        lead_days_override__isnull=False,
                        buffer_days_override__gte=0,
                        buffer_days_override__lte=365,
                        buffer_days_override__isnull=False,
                    )
                ),
                name="inv_gd_item_cycle_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(risk_override__isnull=True, risk_reason_override__isnull=True)
                    | (
                        models.Q(risk_override__in=["no_stock", "urgent", "warning", "stale", "unknown", "healthy"], risk_reason_override__isnull=False)
                        & ~models.Q(risk_reason_override="")
                    )
                ),
                name="inv_gd_item_risk_pair",
            ),
        ]


class GuangdongSupplierCycle(models.Model):
    supplier = models.CharField(primary_key=True, max_length=512)
    lead_days = models.PositiveSmallIntegerField()
    buffer_days = models.PositiveSmallIntegerField(default=7)
    updated_by = models.CharField(max_length=320)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_guangdong_supplier_cycles"
        constraints = [
            models.CheckConstraint(condition=models.Q(lead_days__gte=1, lead_days__lte=365), name="inv_gd_lead_bounds"),
            models.CheckConstraint(condition=models.Q(buffer_days__gte=0, buffer_days__lte=365), name="inv_gd_buffer_bounds"),
        ]


class GuangdongMonitorAudit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=32)
    source = models.CharField(max_length=255)
    raw_hash = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64, default="")
    actor = models.CharField(max_length=320)
    status = models.CharField(max_length=32)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "inventory_guangdong_monitor_audits"


class InventoryImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    dataset = models.CharField(max_length=16, db_index=True)
    source = models.CharField(max_length=128)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    file_hash = models.CharField(max_length=64, unique=True)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    published_state_token = models.CharField(max_length=64, default="")
    sheet_name = models.CharField(max_length=255)
    snapshot_date = models.DateField(db_index=True)
    actor_email = models.CharField(max_length=320, default="")
    status = models.CharField(max_length=32, default="processing")
    source_row_count = models.BigIntegerField(default=0)
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    excluded_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    warnings_json = models.JSONField(default=list)
    totals_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "inventory_import_batches"
        indexes = [
            models.Index(
                fields=["dataset", "snapshot_date", "status", "completed_at"],
                name="inv_batch_scope_idx",
            ),
            models.Index(fields=["created_at", "id"], name="inv_batch_created_idx"),
        ]


class InventoryStockLine(models.Model):
    batch_id = models.CharField(max_length=128, db_index=True)
    row_key = models.CharField(max_length=512)
    source_row_number = models.BigIntegerField()
    snapshot_date = models.DateField(db_index=True)
    warehouse = models.TextField()
    warehouse_type = models.CharField(max_length=32, default="other")
    warehouse_category = models.CharField(max_length=32, default="")
    include_in_inventory = models.BooleanField(default=True)
    product_code = models.CharField(max_length=512, db_index=True)
    product_name = models.TextField(default="")
    brand = models.TextField(default="")
    supplier = models.TextField(default="")
    specification = models.TextField(default="")
    barcode = models.TextField(default="")
    category = models.TextField(default="")
    on_hand_quantity = models.BigIntegerField(default=0)
    available_quantity = models.BigIntegerField(default=0)
    locked_quantity = models.BigIntegerField(default=0)
    in_transit_quantity = models.BigIntegerField(default=0)
    unit_cost_cents = models.BigIntegerField(default=0)
    inventory_age_days = models.IntegerField(null=True, blank=True)
    sales_7d_quantity = models.BigIntegerField(null=True, blank=True)
    sales_30d_quantity = models.BigIntegerField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "inventory_stock_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["batch_id", "row_key"], name="inv_stock_batch_row_uq"
            ),
            models.UniqueConstraint(
                fields=["snapshot_date", "row_key"], name="inv_stock_snapshot_row_uq"
            ),
        ]
        indexes = [
            models.Index(
                fields=["batch_id", "warehouse", "product_code"],
                name="inv_stock_lookup_idx",
            ),
            models.Index(
                fields=["snapshot_date", "product_code"],
                name="inv_stock_product_idx",
            ),
        ]


class InventoryAgeLine(models.Model):
    batch_id = models.CharField(max_length=128, db_index=True)
    row_key = models.CharField(max_length=512)
    source_row_number = models.BigIntegerField()
    snapshot_date = models.DateField(db_index=True)
    warehouse = models.TextField()
    warehouse_type = models.CharField(max_length=32, default="other")
    product_code = models.CharField(max_length=512, db_index=True)
    product_name = models.TextField(default="")
    specification = models.TextField(default="")
    category = models.TextField(default="")
    available_quantity = models.BigIntegerField(default=0)
    inventory_age_days = models.IntegerField(null=True, blank=True)
    sales_7d_quantity = models.BigIntegerField(null=True, blank=True)
    sales_30d_quantity = models.BigIntegerField(null=True, blank=True)
    unit_cost_cents = models.BigIntegerField(default=0)
    stock_value_cents = models.BigIntegerField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "inventory_age_lines"
        constraints = [
            models.UniqueConstraint(
                fields=["batch_id", "row_key"], name="inv_age_batch_row_uq"
            ),
            models.UniqueConstraint(
                fields=["snapshot_date", "row_key"], name="inv_age_snapshot_row_uq"
            ),
        ]
        indexes = [
            models.Index(
                fields=["batch_id", "warehouse", "product_code"],
                name="inv_age_lookup_idx",
            ),
            models.Index(
                fields=["snapshot_date", "product_code"],
                name="inv_age_product_idx",
            ),
        ]


class InventoryImportScopeHead(models.Model):
    dataset = models.CharField(primary_key=True, max_length=16)
    scope_key = models.CharField(max_length=64, unique=True)
    state_token = models.CharField(max_length=64, default="0" * 64)
    status = models.CharField(max_length=32, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    current_batch_id = models.CharField(max_length=128, default="")
    generation = models.BigIntegerField(default=0)
    owner_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_import_scope_heads"


class InventoryImportAttempt(models.Model):
    id = models.CharField(primary_key=True, max_length=128, default=uuid.uuid4, editable=False)
    dataset = models.CharField(max_length=16, db_index=True)
    batch_id = models.CharField(max_length=128, default="")
    scope_key = models.CharField(max_length=64, default="", db_index=True)
    scope_json = models.JSONField(default=dict)
    raw_file_hash = models.CharField(max_length=64, default="", db_index=True)
    content_hash = models.CharField(max_length=64, default="")
    row_count = models.BigIntegerField(default=0)
    excluded_count = models.BigIntegerField(default=0)
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=64, default="")
    actor_email = models.CharField(max_length=320, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_import_attempts"
        indexes = [
            models.Index(
                fields=["dataset", "scope_key", "created_at"],
                name="inv_attempt_scope_idx",
            )
        ]


class InventoryImportFingerprint(models.Model):
    id = models.BigAutoField(primary_key=True)
    dataset = models.CharField(max_length=16, db_index=True)
    batch_id = models.CharField(max_length=128, unique=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    row_count = models.BigIntegerField()
    published_state_token = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="completed")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "inventory_import_fingerprints"


class InventoryDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_data_revisions"


class InventoryWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="inv_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["d1", "postgres"]),
                name="inv_auth_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="d1",
                        authority_epoch__isnull=True,
                        cutover_id="",
                        activated_at__isnull=True,
                    )
                    | (
                        models.Q(
                            status="postgres",
                            authority_epoch__isnull=False,
                            activated_at__isnull=False,
                        )
                        & ~models.Q(cutover_id="")
                        & ~models.Q(migration_verify_run_id="")
                    )
                ),
                name="inv_auth_state_ck",
            ),
        ]


class InventoryWriteRequestReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    body_sha256 = models.CharField(max_length=64)
    query_sha256 = models.CharField(max_length=64)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=200)
    actor_email = models.CharField(max_length=320)
    status = models.CharField(max_length=32, default="processing")
    claim_token = models.CharField(max_length=64, default="")
    response_status = models.PositiveIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "inventory_write_request_receipts"


class InventoryRawUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=255, db_index=True)
    dataset = models.CharField(max_length=16)
    snapshot_date = models.DateField(null=True, blank=True)
    actor_email = models.CharField(max_length=320)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField()
    chunk_size_bytes = models.BigIntegerField()
    chunk_count = models.BigIntegerField()
    received_chunk_count = models.BigIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)
    status = models.CharField(max_length=32, default="uploading")
    owner_token = models.CharField(max_length=64, default="")
    owner_generation = models.BigIntegerField(default=0)
    result_batch_id = models.CharField(max_length=128, default="")
    result_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "inventory_raw_upload_sessions"
        indexes = [
            models.Index(fields=["fingerprint", "expires_at"], name="inv_upload_fingerprint_idx"),
            models.Index(fields=["expires_at", "status"], name="inv_upload_expiry_idx"),
        ]


class InventoryRawUploadChunk(models.Model):
    session = models.ForeignKey(
        InventoryRawUploadSession,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    object_key = models.TextField(unique=True)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    payload = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_raw_upload_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "chunk_index"], name="inv_raw_upload_chunk_uq"
            )
        ]
        indexes = [
            models.Index(fields=["session", "chunk_index"], name="inv_raw_chunk_order_idx")
        ]


class ReplenishmentPlanItem(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    source_batch_id = models.CharField(max_length=128, db_index=True)
    product_code = models.CharField(max_length=512, db_index=True)
    product_name = models.TextField()
    brand = models.TextField(default="")
    category = models.TextField(default="")
    supplier = models.TextField(default="")
    warehouse = models.TextField()
    buyer = models.CharField(max_length=200, default="")
    operator_name = models.CharField(max_length=200, default="")
    department = models.CharField(max_length=200, default="")
    plan_type = models.CharField(max_length=100, default="")
    order_date = models.DateField(null=True, blank=True)
    expected_arrival_date = models.DateField(null=True, blank=True)
    requires_inspection = models.BooleanField(default=False)
    current_stock_quantity = models.BigIntegerField(default=0)
    sales_30d_quantity = models.BigIntegerField(null=True, blank=True)
    suggested_quantity = models.BigIntegerField()
    planned_quantity = models.BigIntegerField()
    guangdong_health = models.JSONField(default=dict)
    coverage_days_tenths = models.IntegerField(null=True, blank=True)
    reason = models.TextField(default="")
    notes = models.TextField(default="")
    status = models.CharField(max_length=32, default="draft", db_index=True)
    dingtalk_sync_status = models.CharField(max_length=16, default="not_synced")
    dingtalk_record_id = models.CharField(max_length=128, default="")
    dingtalk_payload_sha256 = models.CharField(max_length=64, default="")
    dingtalk_sync_owner_token = models.CharField(max_length=64, default="")
    dingtalk_sync_started_at = models.DateTimeField(null=True, blank=True)
    dingtalk_synced_at = models.DateTimeField(null=True, blank=True)
    dingtalk_synced_by = models.CharField(max_length=320, default="")
    dingtalk_sync_error = models.CharField(max_length=500, default="")
    created_by = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "replenishment_plan_items"
        constraints = [
            models.UniqueConstraint(
                fields=["source_batch_id", "warehouse", "product_code", "status"],
                condition=models.Q(status="draft"),
                name="inv_plan_active_draft_uq",
            )
        ]
        indexes = [
            models.Index(fields=["status", "updated_at"], name="inv_plan_status_idx")
        ]


class ReplenishmentGroupDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    plan_ids = models.JSONField(default=list)
    target_group_name = models.CharField(max_length=200)
    robot_name = models.CharField(max_length=160)
    message_sha256 = models.CharField(max_length=64)
    message_text = models.TextField()
    status = models.CharField(max_length=16, default="claimed")
    provider_receipt = models.CharField(max_length=500, default="")
    error_code = models.CharField(max_length=120, default="")
    claimed_by = models.CharField(max_length=320)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_replenishment_group_deliveries"
        indexes = [
            models.Index(fields=["created_at"], name="inv_group_delivery_time_idx"),
            models.Index(fields=["status", "updated_at"], name="inv_group_delivery_status_idx"),
        ]


class InventoryOperatingSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    target_days = models.PositiveIntegerField(default=30)
    critical_days = models.PositiveIntegerField(default=7)
    slow_days = models.PositiveIntegerField(default=45)
    stagnant_days = models.PositiveIntegerField(default=90)
    auto_replenishment = models.BooleanField(default=False)
    inventory_alert = models.BooleanField(default=True)
    allow_negative_inventory = models.BooleanField(default=False)
    warehouse_mapping_json = models.JSONField(default=dict)
    warehouse_mapping_updated_by = models.CharField(max_length=320, default="")
    warehouse_mapping_updated_at = models.DateTimeField(null=True, blank=True)
    updated_by = models.CharField(max_length=320, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_operating_settings"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="inv_settings_singleton_ck")
        ]


class InventoryMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    mode = models.CharField(max_length=16)
    status = models.CharField(max_length=32)
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
        db_table = "inventory_migration_runs"
