from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ProductShippingRateImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    source = models.CharField(max_length=64, default="sku_cumulative")
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    file_hash = models.CharField(max_length=64, unique=True)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    published_state_token = models.CharField(max_length=64, default="")
    sheet_name = models.CharField(max_length=255)
    actor_email = models.CharField(max_length=320, default="")
    status = models.CharField(max_length=32, default="processing")
    source_row_count = models.BigIntegerField(default=0)
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    updated_count = models.BigIntegerField(default=0)
    duplicate_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    warnings_json = models.JSONField(default=list)
    totals_json = models.JSONField(default=dict)
    created_at = models.TextField()
    completed_at = models.TextField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "product_shipping_rate_import_batches"
        indexes = [
            models.Index(fields=["created_at", "id"], name="prod_rate_batch_created_idx"),
            models.Index(fields=["status", "completed_at"], name="prod_rate_batch_status_idx"),
        ]


class ProductShippingRate(models.Model):
    product_code = models.CharField(primary_key=True, max_length=512)
    shipping_rate = models.DecimalField(max_digits=24, decimal_places=12)
    source_row_number = models.BigIntegerField()
    last_import_batch_id = models.CharField(max_length=128, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "product_shipping_rates"


class ProductImportScopeHead(models.Model):
    scope_key = models.CharField(primary_key=True, max_length=64)
    state_token = models.CharField(max_length=64, default="0" * 64)
    status = models.CharField(max_length=32, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    current_batch_id = models.CharField(max_length=128, default="")
    generation = models.BigIntegerField(default=0)
    owner_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_import_scope_heads"


class ProductImportAttempt(models.Model):
    id = models.CharField(primary_key=True, max_length=128, default=uuid.uuid4, editable=False)
    batch_id = models.CharField(max_length=128, default="")
    scope_key = models.CharField(max_length=64, default="", db_index=True)
    scope_json = models.JSONField(default=dict)
    raw_file_hash = models.CharField(max_length=64, default="", db_index=True)
    content_hash = models.CharField(max_length=64, default="")
    row_count = models.BigIntegerField(default=0)
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=64, default="")
    actor_email = models.CharField(max_length=320, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "product_import_attempts"
        indexes = [
            models.Index(fields=["scope_key", "created_at"], name="prod_attempt_scope_idx")
        ]


class ProductImportFingerprint(models.Model):
    id = models.BigAutoField(primary_key=True)
    batch_id = models.CharField(max_length=128, unique=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    row_count = models.BigIntegerField()
    published_state_token = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="completed")
    publication_sequence = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "product_import_fingerprints"


class ProductDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_data_revisions"


class ProductWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="prod_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["d1", "postgres"]),
                name="prod_auth_status_ck",
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
                name="prod_auth_state_ck",
            ),
        ]


class ProductWriteRequestReceipt(models.Model):
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
        db_table = "product_write_request_receipts"


class ProductInventoryProjection(models.Model):
    id = models.BigAutoField(primary_key=True)
    projection_revision = models.CharField(max_length=64)
    product_code = models.CharField(max_length=512)
    brand = models.TextField(default="")
    available_quantity = models.BigIntegerField(default=0)
    known_stock_value_cents = models.BigIntegerField(default=0)
    priced_available_quantity = models.BigIntegerField(default=0)
    source_batch_id = models.CharField(max_length=128)
    snapshot_date = models.CharField(max_length=10)

    class Meta:
        db_table = "product_inventory_projection"
        constraints = [
            models.UniqueConstraint(
                fields=["projection_revision", "product_code"],
                name="prod_inventory_projection_uq",
            )
        ]
        indexes = [
            models.Index(
                fields=["projection_revision", "product_code"],
                name="prod_inventory_projection_idx",
            )
        ]


class ProductInventoryProjectionControl(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    active_revision = models.CharField(max_length=64, default="")
    active_total = models.BigIntegerField(default=0)
    active_source_batch_id = models.CharField(max_length=128, default="")
    active_snapshot_date = models.CharField(max_length=10, default="")
    syncing_revision = models.CharField(max_length=64, default="")
    syncing_total = models.BigIntegerField(default=0)
    syncing_offset = models.BigIntegerField(default=0)
    syncing_source_batch_id = models.CharField(max_length=128, default="")
    syncing_snapshot_date = models.CharField(max_length=10, default="")
    syncing_owner = models.CharField(max_length=320, default="")
    owner_token_hash = models.CharField(max_length=64, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_inventory_projection_control"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="prod_inventory_control_singleton_ck"),
            models.CheckConstraint(condition=models.Q(active_total__gte=0), name="prod_inventory_active_total_ck"),
            models.CheckConstraint(condition=models.Q(syncing_total__gte=0), name="prod_inventory_sync_total_ck"),
            models.CheckConstraint(condition=models.Q(syncing_offset__gte=0), name="prod_inventory_sync_offset_ck"),
        ]


class ProductRawUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=255, db_index=True)
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
        db_table = "product_raw_upload_sessions"
        indexes = [
            models.Index(fields=["fingerprint", "expires_at"], name="prod_upload_fingerprint_idx"),
            models.Index(fields=["expires_at", "status"], name="prod_upload_expiry_idx"),
        ]


class ProductRawUploadChunk(models.Model):
    session = models.ForeignKey(
        ProductRawUploadSession,
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
        db_table = "product_raw_upload_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "chunk_index"],
                name="prod_raw_upload_chunk_uq",
            )
        ]
        indexes = [
            models.Index(fields=["session", "chunk_index"], name="prod_raw_chunk_order_idx")
        ]


class ProductMigrationRun(models.Model):
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
        db_table = "product_migration_runs"
