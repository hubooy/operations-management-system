from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ErpReferenceSyncCheckpoint(models.Model):
    """Durable PG checkpoint for the ERP-only D1 reference stream."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    source_epoch = models.CharField(max_length=128)
    source_path_digest = models.CharField(max_length=64)
    last_event_sequence = models.BigIntegerField(default=0)
    last_event_id = models.TextField(default="")
    erp_revision = models.BigIntegerField()
    content_hash = models.CharField(max_length=64)
    row_count = models.BigIntegerField(default=0)
    source_batch_id = models.TextField(default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "erp_reference_sync_checkpoint"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="erp_ref_checkpoint_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(last_event_sequence__gte=0),
                name="erp_ref_checkpoint_sequence_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(erp_revision__gte=1),
                name="erp_ref_checkpoint_revision_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(row_count__gte=0),
                name="erp_ref_checkpoint_rows_ck",
            ),
        ]


class ErpProductMaster(models.Model):
    """ERP-owned mapping for the pre-existing PostgreSQL projection table."""

    product_code = models.TextField(primary_key=True)
    product_name = models.TextField()
    brand = models.TextField(default="")
    specification = models.TextField(default="")
    barcode = models.TextField(default="")
    category = models.TextField(default="")
    supplier = models.TextField(default="")
    product_status = models.TextField(default="")
    source_row_number = models.BigIntegerField()
    last_import_batch_id = models.TextField()
    created_at = models.TextField()
    updated_at = models.TextField()
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "erp_product_master"
        managed = False


class ErpComboItem(models.Model):
    parent_code = models.TextField()
    parent_name = models.TextField(default="")
    child_code = models.TextField()
    child_name = models.TextField(default="")
    child_quantity_milli = models.BigIntegerField()
    source_row_number = models.BigIntegerField()
    last_import_batch_id = models.CharField(max_length=128, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "erp_combo_items"
        constraints = [
            models.UniqueConstraint(
                fields=["parent_code", "child_code"], name="erp_combo_identity_uq"
            ),
            models.CheckConstraint(
                condition=models.Q(child_quantity_milli__gt=0), name="erp_combo_quantity_ck"
            ),
        ]
        indexes = [
            models.Index(fields=["parent_code"], name="erp_combo_parent_idx"),
            models.Index(fields=["child_code"], name="erp_combo_child_idx"),
        ]


class ErpReferenceImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=128)
    source_key = models.CharField(max_length=16, db_index=True)
    source_label = models.CharField(max_length=100)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    file_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    published_state_token = models.CharField(max_length=64, default="")
    sheet_name = models.CharField(max_length=255)
    status = models.CharField(max_length=32, default="processing")
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    updated_count = models.BigIntegerField(default=0)
    excluded_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    warnings_json = models.JSONField(default=list)
    totals_json = models.JSONField(default=dict)
    actor_email = models.CharField(max_length=320, default="")
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "erp_reference_import_batches_pg"
        constraints = [
            models.UniqueConstraint(
                fields=["source_key", "file_hash"], name="erp_batch_source_hash_uq"
            )
        ]
        indexes = [
            models.Index(fields=["source_key", "created_at"], name="erp_batch_source_created_idx"),
            models.Index(fields=["status", "completed_at"], name="erp_batch_status_idx"),
        ]


class ErpReferenceImportScopeHead(models.Model):
    scope_key = models.CharField(primary_key=True, max_length=64)
    source_key = models.CharField(max_length=16, unique=True)
    state_token = models.CharField(max_length=64, default="0" * 64)
    status = models.CharField(max_length=16, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    generation = models.BigIntegerField(default=0)
    current_batch_id = models.CharField(max_length=128, default="")
    owner_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "erp_reference_import_scope_heads"


class ErpReferenceImportFingerprint(models.Model):
    id = models.BigAutoField(primary_key=True)
    batch_id = models.CharField(max_length=128, unique=True)
    source_key = models.CharField(max_length=16)
    scope_key = models.CharField(max_length=64, db_index=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64)
    row_count = models.BigIntegerField()
    published_state_token = models.CharField(max_length=64)
    outcome = models.CharField(max_length=32)
    created_at = models.DateTimeField(default=timezone.now)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "erp_reference_import_fingerprints"
        constraints = [
            models.UniqueConstraint(
                fields=["scope_key", "import_hash"], name="erp_fingerprint_scope_import_uq"
            )
        ]


class ErpReferenceImportAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_id = models.CharField(max_length=128, default="")
    source_key = models.CharField(max_length=16, default="")
    scope_key = models.CharField(max_length=64, default="", db_index=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="")
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, default="")
    row_count = models.BigIntegerField(default=0)
    file_name = models.TextField(default="")
    file_size_bytes = models.BigIntegerField(default=0)
    actor_email = models.CharField(max_length=320, default="")
    warnings_json = models.JSONField(default=list)
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=80, default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "erp_reference_import_attempts"
        indexes = [
            models.Index(fields=["source_key", "created_at"], name="erp_attempt_source_idx"),
            models.Index(fields=["raw_file_hash", "created_at"], name="erp_attempt_raw_idx"),
        ]


class ErpReferenceWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "erp_reference_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="erp_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["d1", "postgres"]), name="erp_auth_status_ck"
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
                name="erp_auth_state_ck",
            ),
        ]


class ErpReferenceWriteRequestReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    actor_email = models.CharField(max_length=320)
    method = models.CharField(max_length=12)
    path = models.CharField(max_length=300)
    body_sha256 = models.CharField(max_length=64)
    query_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16)
    claim_token = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "erp_reference_write_request_receipts"


class ErpReferenceMigrationRun(models.Model):
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
        db_table = "erp_reference_migration_runs"


class ErpReferenceRawUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fingerprint = models.CharField(max_length=255, db_index=True)
    source_key = models.CharField(max_length=16)
    actor_email = models.CharField(max_length=320)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField()
    chunk_size_bytes = models.BigIntegerField()
    chunk_count = models.PositiveIntegerField()
    received_chunk_count = models.PositiveIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, default="uploading")
    owner_token = models.CharField(max_length=64, default="")
    owner_generation = models.BigIntegerField(default=0)
    result_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "erp_reference_raw_upload_sessions"
        indexes = [
            models.Index(fields=["fingerprint", "expires_at"], name="erp_upload_fingerprint_idx"),
            models.Index(fields=["expires_at", "status"], name="erp_upload_expiry_idx"),
        ]


class ErpReferenceRawUploadChunk(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(
        ErpReferenceRawUploadSession, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    payload = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "erp_reference_raw_upload_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "chunk_index"], name="erp_upload_chunk_uq"
            )
        ]
        indexes = [models.Index(fields=["session", "chunk_index"], name="erp_upload_chunk_idx")]
