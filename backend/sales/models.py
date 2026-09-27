from __future__ import annotations

from datetime import date
from typing import Mapping
import uuid

from django.db import models


UNCATEGORIZED = "未分类"
EXCLUDED_WAREHOUSE = "刷刷仓"
EXCLUDED_SOURCE_CATEGORIES = frozenset({"配件", "赠品配件"})
PRICE_ADJUSTMENT_PRODUCT_CODE = "ERP_PRICE_ADJUSTMENT"
PRICE_ADJUSTMENT_PRODUCT_NAME = "补差价专用"


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _trimmed(value: object) -> str:
    return _text(value).strip()


def sales_projection_values(
    raw: Mapping[str, object], *, erp_category: object = ""
) -> dict[str, object]:
    """Build the deterministic, query-ready columns for one D1 sales row."""
    ship_time = _text(raw.get("ship_time"))
    try:
        business_date = date.fromisoformat(ship_time[:10])
    except ValueError as error:
        raise ValueError("ship_time must start with a valid ISO business date") from error

    platform = _trimmed(raw.get("platform"))
    channel = _trimmed(raw.get("channel"))
    shop = _trimmed(raw.get("shop_name"))
    source_category = _trimmed(raw.get("category"))
    product_name = _trimmed(raw.get("product_name"))
    product_code = _text(raw.get("product_code"))
    source_line_key = _text(raw.get("source_line_key"))
    order_no = _text(raw.get("order_no"))
    online_order_no = _text(raw.get("online_order_no"))
    included_category = bool(source_category) and source_category not in EXCLUDED_SOURCE_CATEGORIES

    return {
        "business_date": business_date,
        "platform_key": platform or UNCATEGORIZED,
        "channel_key": channel or UNCATEGORIZED,
        "shop_key": shop or channel or platform or UNCATEGORIZED,
        "resolved_category": _trimmed(erp_category) or source_category or UNCATEGORIZED,
        "order_identity": order_no or online_order_no or source_line_key,
        "is_business_row": _trimmed(raw.get("warehouse")) != EXCLUDED_WAREHOUSE,
        "is_net_sales_row": included_category,
        "is_net_quantity_row": (
            included_category
            and product_code != PRICE_ADJUSTMENT_PRODUCT_CODE
            and product_name != PRICE_ADJUSTMENT_PRODUCT_NAME
        ),
    }


class SalesImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=200)
    source = models.CharField(max_length=200)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    file_hash = models.CharField(max_length=128, unique=True)
    sheet_name = models.TextField()
    status = models.CharField(max_length=50)
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    duplicate_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    warnings_json = models.TextField(default="[]")
    totals_json = models.TextField(default="{}")
    created_at = models.TextField()
    completed_at = models.TextField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)
    raw_file_hash = models.CharField(max_length=64, default="", db_index=True)
    content_hash = models.CharField(max_length=64, default="", db_index=True)
    scope_key = models.CharField(max_length=64, default="", db_index=True)
    scope_json = models.JSONField(default=dict)
    published_state_token = models.CharField(max_length=64, default="")
    actor_email = models.CharField(max_length=320, default="")

    class Meta:
        db_table = "sales_import_batches"
        indexes = [models.Index(fields=["created_at"], name="sales_batch_created_idx")]


class SalesOrderLine(models.Model):
    # D1's row id is allocation-local and can change when its snapshot is rebuilt.
    # Django owns this surrogate key; source_line_key is the cross-store identity.
    id = models.BigAutoField(primary_key=True)
    source_line_key = models.TextField(unique=True)
    source_row_hash = models.TextField()
    first_import_batch_id = models.TextField()
    last_import_batch_id = models.TextField()
    source_row_number = models.BigIntegerField()
    order_no = models.TextField()
    online_order_no = models.TextField()
    channel = models.TextField()
    platform = models.TextField()
    shop_name = models.TextField()
    logistics_company = models.TextField()
    warehouse = models.TextField()
    product_code = models.TextField()
    online_spec_code = models.TextField(default="")
    product_name = models.TextField()
    specification = models.TextField()
    barcode = models.TextField()
    supplier = models.TextField()
    category = models.TextField()
    quantity = models.BigIntegerField()
    list_unit_price_cents = models.BigIntegerField()
    cost_amount_cents = models.BigIntegerField()
    allocated_unit_price_cents = models.BigIntegerField()
    allocated_amount_cents = models.BigIntegerField()
    fee_allocation_cents = models.BigIntegerField()
    gross_profit_cents = models.BigIntegerField()
    gross_margin_bps = models.BigIntegerField()
    untaxed_gross_profit_cents = models.BigIntegerField()
    untaxed_gross_margin_bps = models.BigIntegerField()
    order_time = models.TextField()
    sales_time = models.TextField()
    ship_time = models.TextField()
    line_ship_time = models.TextField()
    business_type = models.TextField()
    created_at = models.TextField()
    updated_at = models.TextField()
    business_date = models.DateField()
    platform_key = models.TextField()
    channel_key = models.TextField()
    shop_key = models.TextField()
    resolved_category = models.TextField()
    order_identity = models.TextField()
    is_business_row = models.BooleanField()
    is_net_sales_row = models.BooleanField()
    is_net_quantity_row = models.BooleanField()
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "sales_order_lines"
        indexes = [
            models.Index(
                fields=["business_date"],
                name="sales_biz_date_idx",
                condition=models.Q(is_business_row=True),
            ),
            models.Index(
                fields=["platform_key", "shop_key", "business_date"],
                name="sales_platform_shop_date_idx",
                condition=models.Q(is_business_row=True),
            ),
            models.Index(
                fields=["channel_key", "business_date"],
                name="sales_channel_date_idx",
                condition=models.Q(is_business_row=True),
            ),
            models.Index(
                fields=["resolved_category", "business_date"],
                name="sales_category_date_idx",
                condition=models.Q(is_business_row=True),
            ),
            models.Index(
                fields=["product_code", "business_date"],
                name="sales_product_date_idx",
                condition=models.Q(is_business_row=True),
            ),
            models.Index(fields=["last_import_batch_id"], name="sales_line_batch_idx"),
        ]


class ErpProductMaster(models.Model):
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
        indexes = [
            models.Index(fields=["product_name"], name="erp_product_name_idx"),
            models.Index(fields=["barcode"], name="erp_product_barcode_idx"),
            models.Index(fields=["last_import_batch_id"], name="erp_product_batch_idx"),
        ]


class SalesDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_data_revisions"


class SalesMigrationRun(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    status = models.CharField(max_length=32)
    dry_run = models.BooleanField(default=False)
    source_fingerprint = models.CharField(max_length=128)
    source_path_digest = models.CharField(max_length=64)
    generation = models.CharField(max_length=64)
    source_revision = models.CharField(max_length=64, default="")
    target_revision = models.CharField(max_length=64, default="")
    canonical_format_version = models.CharField(max_length=64, default="")
    approved_run_id = models.CharField(max_length=64, blank=True, default="")
    consumed_by_run_id = models.CharField(max_length=64, blank=True, default="")
    approval_consumed_at = models.DateTimeField(null=True, blank=True)
    source_counts = models.JSONField(default=dict)
    target_counts = models.JSONField(default=dict)
    source_digests = models.JSONField(default=dict)
    target_digests = models.JSONField(default=dict)
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_migration_runs"
        indexes = [models.Index(fields=["status", "started_at"], name="sales_migration_status_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["approved_run_id"],
                condition=models.Q(approved_run_id__gt=""),
                name="uniq_sales_mig_approval",
            )
        ]


class SalesMigrationLock(models.Model):
    name = models.CharField(primary_key=True, max_length=64)
    owner_id = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_migration_locks"


class SalesLegacyUploadAudit(models.Model):
    """Privacy-safe provenance for retired D1/R2 upload sessions.

    These rows deliberately live outside ``SalesRawUploadSession`` so a
    historical D1 upload can never be resumed or claimed by the PostgreSQL
    writer.  Raw object keys, file names, fingerprints and customer file bytes
    are not retained; their stable digests are enough to bind the archived
    manifest used during cutover verification.
    """

    source_upload_id = models.CharField(primary_key=True, max_length=128)
    source_fingerprint_sha256 = models.CharField(max_length=64)
    file_name_sha256 = models.CharField(max_length=64)
    file_size_bytes = models.BigIntegerField()
    chunk_size_bytes = models.BigIntegerField()
    declared_chunk_count = models.PositiveIntegerField()
    declared_received_chunk_count = models.PositiveIntegerField()
    declared_received_bytes = models.BigIntegerField()
    source_status = models.CharField(max_length=32)
    archive_reason = models.CharField(max_length=16)
    source_created_at = models.DateTimeField()
    source_updated_at = models.DateTimeField()
    source_expires_at = models.DateTimeField()
    manifest_chunk_count = models.PositiveIntegerField()
    manifest_bytes = models.BigIntegerField()
    manifest_sha256 = models.CharField(max_length=64)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)
    migrated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_legacy_upload_audits"
        indexes = [
            models.Index(
                fields=["source_status", "source_expires_at"],
                name="sales_legacy_status_exp_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    source_status__in=["uploading", "ready", "processing", "completed"]
                ),
                name="sales_legacy_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(archive_reason__in=["completed", "expired"]),
                name="sales_legacy_reason_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_status="completed", archive_reason="completed")
                    | (
                        ~models.Q(source_status="completed")
                        & models.Q(archive_reason="expired")
                        & models.Q(source_expires_at__lte=models.F("migrated_at"))
                    )
                ),
                name="sales_legacy_archive_state_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(file_size_bytes__gt=0),
                name="sales_legacy_file_size_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(chunk_size_bytes__gt=0),
                name="sales_legacy_chunk_size_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_chunk_count__gt=0),
                name="sales_legacy_decl_count_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_received_bytes__gte=0),
                name="sales_legacy_recv_bytes_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(manifest_bytes__gte=0),
                name="sales_legacy_manifest_bytes_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    declared_received_chunk_count__lte=models.F("declared_chunk_count")
                ),
                name="sales_legacy_recv_le_decl_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(manifest_chunk_count__lte=models.F("declared_chunk_count")),
                name="sales_legacy_manifest_le_decl_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_received_bytes__lte=models.F("file_size_bytes")),
                name="sales_legacy_recv_bytes_le_file_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(manifest_bytes__lte=models.F("file_size_bytes")),
                name="sales_legacy_manifest_bytes_le_file_ck",
            ),
        ]


class SalesImportScopeHead(models.Model):
    """O(1) ownership head for the stable sales-ledger write domain."""

    scope_key = models.CharField(primary_key=True, max_length=64)
    domain = models.CharField(max_length=32, default="sales")
    state_token = models.CharField(max_length=64, default="initial")
    status = models.CharField(max_length=32, default="ready")
    owner_token = models.CharField(max_length=64, default="", blank=True)
    current_batch_id = models.CharField(max_length=128, default="", blank=True)
    generation = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_import_scope_heads"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["ready", "processing"]),
                name="sales_scope_head_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(generation__gte=0),
                name="sales_scope_head_generation_ck",
            ),
        ]


class SalesWriteAuthority(models.Model):
    """Explicit cutover gate; row 1 is locked by every sales write transaction."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="pending")
    authority_epoch = models.UUIDField(default=uuid.uuid4, editable=False)
    cutover_id = models.CharField(max_length=128, default="", blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_write_authority"
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="sales_auth_singleton_ck"),
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "active", "disabled"]),
                name="sales_auth_status_ck",
            ),
        ]


class SalesWriteRequestReceipt(models.Model):
    """HMAC request-id replay fence for internal POST/PUT operations."""

    request_id = models.CharField(primary_key=True, max_length=128)
    actor_email = models.CharField(max_length=320)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    body_sha256 = models.CharField(max_length=64)
    claim_token = models.CharField(max_length=64, default="", blank=True)
    status = models.CharField(max_length=16, default="processing")
    response_status = models.PositiveSmallIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "sales_write_request_receipts"
        indexes = [models.Index(fields=["expires_at"], name="sales_write_receipt_expiry_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["processing", "completed", "failed"]),
                name="sales_write_receipt_status_ck",
            )
        ]


class SalesImportAttempt(models.Model):
    """Append-only, sanitized audit for every normalized import attempt."""

    id = models.CharField(primary_key=True, max_length=64)
    domain = models.CharField(max_length=32, default="sales")
    session_id = models.CharField(max_length=36, default="", blank=True)
    batch_id = models.CharField(max_length=128, default="", blank=True)
    scope_key = models.CharField(max_length=64, default="", blank=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="", blank=True)
    raw_file_hash = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64, default="", blank=True)
    row_count = models.BigIntegerField(default=0)
    file_name = models.CharField(max_length=255, default="", blank=True)
    file_size_bytes = models.BigIntegerField(default=0)
    actor_email = models.CharField(max_length=320, default="", blank=True)
    warnings = models.JSONField(default=list)
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=100, default="", blank=True)
    recovered_from_attempt_id = models.CharField(max_length=64, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_import_attempts"
        indexes = [
            models.Index(fields=["scope_key", "created_at"], name="sales_attempt_scope_idx"),
            models.Index(fields=["raw_file_hash", "created_at"], name="sales_attempt_raw_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(row_count__gte=0),
                name="sales_attempt_rows_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(file_size_bytes__gte=0),
                name="sales_attempt_file_size_ck",
            ),
        ]


class SalesImportFingerprint(models.Model):
    """Published canonical business content, independent of source file bytes."""

    domain = models.CharField(max_length=32, default="sales")
    batch_id = models.CharField(max_length=128)
    scope_key = models.CharField(max_length=64)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64)
    content_hash = models.CharField(max_length=64)
    row_count = models.BigIntegerField()
    status = models.CharField(max_length=32, default="completed")
    publication_sequence = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_import_fingerprints"
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "batch_id"], name="uniq_sales_fingerprint_batch"
            ),
            models.UniqueConstraint(
                fields=["domain", "scope_key", "import_hash"],
                name="uniq_sales_fingerprint_import",
            ),
            models.CheckConstraint(
                condition=models.Q(row_count__gte=0),
                name="sales_fingerprint_rows_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["domain", "scope_key", "publication_sequence"],
                name="sales_fingerprint_scope_idx",
            )
        ]


class SalesRawUploadSession(models.Model):
    """PostgreSQL authority for resumable raw XLSX uploads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_fingerprint = models.CharField(max_length=255, db_index=True)
    actor_email = models.CharField(max_length=320)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField()
    chunk_size_bytes = models.BigIntegerField()
    chunk_count = models.PositiveIntegerField()
    received_chunk_count = models.PositiveIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)
    expected_start_date = models.DateField()
    expected_end_date = models.DateField()
    expected_channels = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=32, default="uploading")
    owner_token = models.CharField(max_length=64, default="", blank=True)
    owner_generation = models.BigIntegerField(default=0)
    result_batch_id = models.CharField(max_length=128, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "sales_raw_upload_sessions"
        indexes = [
            models.Index(
                fields=["client_fingerprint", "expires_at"],
                name="sales_raw_fprint_idx",
            ),
            models.Index(fields=["expires_at"], name="sales_raw_upload_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(file_size_bytes__gt=0),
                name="sales_raw_upload_size_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(chunk_size_bytes__gt=0),
                name="sales_raw_chunk_size_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(chunk_count__gt=0),
                name="sales_raw_chunk_count_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(received_chunk_count__gte=0),
                name="sales_raw_received_count_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(received_bytes__gte=0),
                name="sales_raw_received_bytes_ck",
            ),
        ]


class SalesRawUploadChunk(models.Model):
    session = models.ForeignKey(
        SalesRawUploadSession,
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
        db_table = "sales_raw_upload_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "chunk_index"], name="uniq_sales_raw_upload_chunk"
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0),
                name="sales_raw_upload_chunk_size_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["session", "chunk_index"], name="sales_raw_chunk_order_idx")
        ]


class SalesStagedImportSession(models.Model):
    """Bounded normalized-row staging before one fenced fact transaction."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raw_upload = models.ForeignKey(
        SalesRawUploadSession,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="normalized_imports",
    )
    raw_upload_owner_token = models.CharField(max_length=64, default="", blank=True)
    raw_upload_owner_generation = models.BigIntegerField(default=0)
    client_fingerprint = models.CharField(max_length=255, db_index=True)
    actor_email = models.CharField(max_length=320)
    file_name = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField()
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    sheet_name = models.CharField(max_length=255)
    expected_start_date = models.DateField()
    expected_end_date = models.DateField()
    expected_channels = models.JSONField(null=True, blank=True)
    chunk_count = models.PositiveIntegerField()
    received_chunk_count = models.PositiveIntegerField(default=0)
    received_row_count = models.PositiveIntegerField(default=0)
    source_totals = models.JSONField(default=dict)
    parser_warnings = models.JSONField(default=list)
    system_cost_snapshot = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=32, default="uploading")
    owner_token = models.CharField(max_length=64, default="", blank=True)
    result_batch_id = models.CharField(max_length=128, default="", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "sales_staged_import_sessions"
        indexes = [
            models.Index(
                fields=["client_fingerprint", "expires_at"],
                name="sales_stage_fingerprint_idx",
            ),
            models.Index(fields=["expires_at"], name="sales_stage_expiry_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(file_size_bytes__gt=0),
                name="sales_stage_file_size_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(chunk_count__gt=0),
                name="sales_stage_chunk_count_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(received_chunk_count__gte=0),
                name="sales_stage_received_chunks_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(received_row_count__gte=0),
                name="sales_stage_received_rows_ck",
            ),
        ]


class SalesStagedImportChunk(models.Model):
    session = models.ForeignKey(
        SalesStagedImportSession,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    row_count = models.PositiveIntegerField()
    content_hash = models.CharField(max_length=64)
    rows = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_staged_import_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "chunk_index"], name="uniq_sales_staged_chunk"
            )
        ]
        indexes = [
            models.Index(fields=["session", "chunk_index"], name="sales_stage_chunk_order_idx")
        ]


class SalesCutoverAttestation(models.Model):
    """Durable proof that D1 reached its terminal owner before PG activation."""

    cutover_id = models.CharField(primary_key=True, max_length=128)
    d1_authority_epoch = models.BigIntegerField()
    source_path_digest = models.CharField(max_length=64)
    migration_apply_run_id = models.CharField(max_length=64)
    migration_verify_run_id = models.CharField(max_length=64)
    cleanup_manifest_id = models.CharField(max_length=64)
    cleanup_manifest_sha256 = models.CharField(max_length=64)
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64, unique=True)
    observed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sales_cutover_attestations"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(d1_authority_epoch__gte=1),
                name="sales_cutover_d1_epoch_ck",
            )
        ]


class SalesAnalysisOption(models.Model):
    platform = models.CharField(max_length=200)
    shop = models.CharField(max_length=200)
    channel = models.CharField(max_length=200)
    first_date = models.CharField(max_length=10)
    last_date = models.CharField(max_length=10)
    row_count = models.BigIntegerField()
    entry_json = models.TextField()
    entry_digest = models.CharField(max_length=64)

    class Meta:
        db_table = "sales_analysis_options"
        constraints = [models.UniqueConstraint(fields=("platform", "shop", "channel"), name="sales_options_identity_uq")]


class SalesAnalysisOptionsState(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    status = models.CharField(max_length=16, default="not_ready")
    generation = models.CharField(max_length=64, default="")
    directory_digest = models.CharField(max_length=64, default="")
    identity_count = models.PositiveIntegerField(default=0)
    stored_bytes = models.PositiveIntegerField(default=0)
    source_sales_revision = models.BigIntegerField(default=-1)
    reason = models.CharField(max_length=64, default="not_initialized")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sales_analysis_options_state"
