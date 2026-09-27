from __future__ import annotations

import uuid

from django.db import models


class NetshopImportBatch(models.Model):
    id = models.CharField(primary_key=True, max_length=1024)
    source = models.CharField(max_length=64)
    dataset = models.CharField(max_length=64)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    file_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    published_state_token = models.CharField(max_length=64, default="")
    sheet_name = models.TextField(default="")
    status = models.CharField(max_length=32)
    row_count = models.BigIntegerField(default=0)
    inserted_count = models.BigIntegerField(default=0)
    duplicate_count = models.BigIntegerField(default=0)
    warning_count = models.BigIntegerField(default=0)
    date_min = models.CharField(max_length=10, null=True, blank=True)
    date_max = models.CharField(max_length=10, null=True, blank=True)
    snapshot_date = models.CharField(max_length=10, null=True, blank=True)
    warnings_json = models.JSONField(default=list)
    totals_json = models.JSONField(default=dict)
    note = models.TextField(default="")
    actor_email = models.CharField(max_length=320, default="")
    created_at = models.TextField()
    completed_at = models.TextField(null=True, blank=True)
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "netshop_import_batches"
        constraints = [
            models.UniqueConstraint(
                fields=["source", "platform", "shop_name", "file_hash"],
                name="netshop_batch_identity_uq",
            )
        ]
        indexes = [
            models.Index(fields=["source", "created_at"], name="net_batch_source_idx"),
            models.Index(fields=["shop_name", "dataset", "completed_at"], name="net_batch_scope_idx"),
            models.Index(
                fields=["source", "status", "platform", "shop_name", "completed_at"],
                name="net_batch_latest_idx",
            ),
        ]


class NetshopRow(models.Model):
    id = models.BigAutoField(primary_key=True)
    source_row_key = models.TextField(unique=True)
    source_row_hash = models.CharField(max_length=64)
    first_import_batch_id = models.CharField(max_length=1024)
    last_import_batch_id = models.CharField(max_length=1024, db_index=True)
    source_row_number = models.BigIntegerField()
    source = models.CharField(max_length=64)
    dataset = models.CharField(max_length=64)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    business_date = models.CharField(max_length=10, null=True, blank=True)
    snapshot_date = models.CharField(max_length=10, null=True, blank=True)
    product_code = models.TextField(default="")
    product_name = models.TextField(default="")
    sku_id = models.TextField(default="")
    spu_id = models.TextField(default="")
    warehouse_type = models.CharField(max_length=100, default="")
    metrics_json = models.JSONField(default=dict)
    raw_json = models.JSONField(default=dict)

    # Typed projections are populated and revalidated by the Django writer.
    # JSON is retained for audit and forward-compatible source fields, while
    # all page metrics use integer PostgreSQL columns rather than floating JSON.
    page_views = models.BigIntegerField(default=0)
    visitors = models.BigIntegerField(default=0)
    search_impressions = models.BigIntegerField(default=0)
    search_clicks = models.BigIntegerField(default=0)
    add_cart_customers = models.BigIntegerField(default=0)
    add_cart_quantity = models.BigIntegerField(default=0)
    order_customers = models.BigIntegerField(default=0)
    order_quantity = models.BigIntegerField(default=0)
    order_amount_cents = models.BigIntegerField(default=0)
    transaction_orders = models.BigIntegerField(default=0)
    transaction_amount_cents = models.BigIntegerField(default=0)
    transaction_quantity = models.BigIntegerField(default=0)
    transaction_customers = models.BigIntegerField(default=0)
    favorites = models.BigIntegerField(default=0)
    refund_amount_cents = models.BigIntegerField(default=0)
    search_visitors = models.BigIntegerField(default=0)
    search_transaction_customers = models.BigIntegerField(default=0)
    spend_cents = models.BigIntegerField(default=0)
    net_transaction_amount_cents = models.BigIntegerField(default=0)
    gross_transaction_amount_cents = models.BigIntegerField(default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    net_orders = models.BigIntegerField(default=0)
    cart_quantity = models.BigIntegerField(default=0)
    inventory_quantity = models.BigIntegerField(default=0)
    total_inventory = models.BigIntegerField(null=True, blank=True)
    available_inventory = models.BigIntegerField(null=True, blank=True)
    price_cents = models.BigIntegerField(null=True, blank=True)
    sale_attribute = models.TextField(default="")
    category = models.TextField(default="")
    brand = models.TextField(default="")
    product_status = models.TextField(default="")
    product_url = models.TextField(default="")
    image_url = models.TextField(default="")
    image_content_sha256 = models.CharField(max_length=64, default="", db_index=True)
    image_object_key = models.TextField(default="")
    image_mime_type = models.CharField(max_length=100, default="")
    image_size_bytes = models.BigIntegerField(null=True, blank=True)
    image_width = models.BigIntegerField(null=True, blank=True)
    image_height = models.BigIntegerField(null=True, blank=True)
    source_created_at = models.TextField(default="")
    created_at = models.TextField()
    updated_at = models.TextField()
    migration_generation = models.CharField(max_length=64, default="", db_index=True)

    class Meta:
        db_table = "netshop_rows"
        indexes = [
            models.Index(fields=["shop_name", "dataset", "business_date"], name="net_shop_date_idx"),
            models.Index(fields=["source", "business_date"], name="net_source_date_idx"),
            models.Index(
                fields=["source", "dataset", "platform", "shop_name", "business_date"],
                name="net_scope_date_idx",
            ),
            models.Index(fields=["source", "snapshot_date", "warehouse_type"], name="net_snapshot_idx"),
            models.Index(fields=["source", "sku_id", "product_code"], name="net_source_sku_idx"),
            models.Index(fields=["dataset", "platform", "shop_name", "business_date", "spu_id"], name="net_spu_date_idx"),
            models.Index(fields=["source", "platform", "shop_name", "snapshot_date"], name="net_master_head_idx"),
            models.Index(fields=["sku_id"], name="net_sku_idx"),
            models.Index(fields=["spu_id"], name="net_spu_idx"),
            models.Index(fields=["product_code"], name="net_product_idx"),
            models.Index(fields=["last_import_batch_id", "shop_name", "product_name"], name="net_batch_page_idx"),
        ]


class NetshopPromotionProductDaily(models.Model):
    id = models.BigAutoField(primary_key=True)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    business_date = models.CharField(max_length=10)
    product_id = models.TextField()
    source = models.CharField(max_length=64)
    product_name = models.TextField(default="")
    product_line = models.TextField(default="")
    spend_cents = models.BigIntegerField(default=0)
    net_transaction_amount_cents = models.BigIntegerField(default=0)
    gross_transaction_amount_cents = models.BigIntegerField(default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    net_orders = models.BigIntegerField(default=0)
    favorites = models.BigIntegerField(default=0)
    cart_quantity = models.BigIntegerField(default=0)
    source_row_count = models.BigIntegerField(default=0)
    source_batch_id = models.CharField(max_length=1024)
    source_batch_count = models.BigIntegerField(default=0)
    rebuilt_at = models.TextField()

    class Meta:
        db_table = "netshop_promotion_product_daily"
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "shop_name", "business_date", "product_id"],
                name="net_promo_product_uq",
            )
        ]
        indexes = [
            models.Index(fields=["platform", "business_date", "shop_name"], name="net_promo_prod_date_idx"),
            models.Index(fields=["platform", "shop_name", "product_id", "business_date"], name="net_promo_prod_id_idx"),
        ]


class NetshopPromotionShopDaily(models.Model):
    id = models.BigAutoField(primary_key=True)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    business_date = models.CharField(max_length=10)
    source = models.CharField(max_length=64)
    product_count = models.BigIntegerField(default=0)
    spend_cents = models.BigIntegerField(default=0)
    net_transaction_amount_cents = models.BigIntegerField(default=0)
    gross_transaction_amount_cents = models.BigIntegerField(default=0)
    impressions = models.BigIntegerField(default=0)
    clicks = models.BigIntegerField(default=0)
    net_orders = models.BigIntegerField(default=0)
    favorites = models.BigIntegerField(default=0)
    cart_quantity = models.BigIntegerField(default=0)
    source_row_count = models.BigIntegerField(default=0)
    source_batch_id = models.CharField(max_length=1024)
    source_batch_count = models.BigIntegerField(default=0)
    rebuilt_at = models.TextField()

    class Meta:
        db_table = "netshop_promotion_shop_daily"
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "shop_name", "business_date"],
                name="net_promo_shop_uq",
            )
        ]
        indexes = [
            models.Index(fields=["platform", "business_date", "shop_name"], name="net_promo_shop_date_idx")
        ]


class NetshopDataRevision(models.Model):
    domain = models.CharField(primary_key=True, max_length=32)
    revision = models.BigIntegerField(default=0)
    source_digest = models.CharField(max_length=64, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_data_revisions"


class NetshopProductDailyRevision(models.Model):
    platform = models.CharField(primary_key=True, max_length=100)
    data_version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_product_daily_revisions"


class NetshopProductDailyScopeRevision(models.Model):
    id = models.BigAutoField(primary_key=True)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    data_version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_product_daily_scope_revisions"
        constraints = [
            models.UniqueConstraint(fields=["platform", "shop_name"], name="net_product_scope_revision_uq")
        ]


class NetshopPromotionScopeRevision(models.Model):
    id = models.BigAutoField(primary_key=True)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    data_version = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_promotion_scope_revisions"
        constraints = [
            models.UniqueConstraint(fields=["platform", "shop_name"], name="net_promotion_scope_revision_uq")
        ]


class NetshopPromotionAggregateState(models.Model):
    id = models.BigAutoField(primary_key=True)
    platform = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    business_date = models.CharField(max_length=10)
    source = models.CharField(max_length=64)
    ready = models.BooleanField(default=False)
    raw_row_count = models.BigIntegerField(default=0)
    product_row_count = models.BigIntegerField(default=0)
    source_batch_id = models.CharField(max_length=1024, default="")
    source_batch_count = models.BigIntegerField(default=0)
    rebuilt_at = models.TextField(null=True, blank=True)
    invalidated_at = models.TextField()

    class Meta:
        db_table = "netshop_promotion_aggregate_state"
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "shop_name", "business_date"],
                name="net_promotion_state_uq",
            )
        ]


class NetshopPromotionAggregateManifest(models.Model):
    platform = models.CharField(primary_key=True, max_length=100)
    ready = models.BooleanField(default=False)
    historical_data_cutoff = models.CharField(max_length=10, null=True, blank=True)
    source_shop_count = models.BigIntegerField(default=0)
    raw_row_count = models.BigIntegerField(default=0)
    product_row_count = models.BigIntegerField(default=0)
    shop_day_count = models.BigIntegerField(default=0)
    state_day_count = models.BigIntegerField(default=0)
    completed_at = models.TextField(null=True, blank=True)
    invalidated_at = models.TextField()
    data_version = models.BigIntegerField(default=0)

    class Meta:
        db_table = "netshop_promotion_aggregate_manifest"


class NetshopPromotionAggregateControl(models.Model):
    platform = models.CharField(primary_key=True, max_length=100)
    bootstrap_batch_id = models.CharField(max_length=1024, default="")
    bootstrap_raw_row_count = models.BigIntegerField(default=0)
    bootstrap_product_row_count = models.BigIntegerField(default=0)
    bootstrap_shop_day_count = models.BigIntegerField(default=0)
    bootstrap_data_cutoff = models.CharField(max_length=10, null=True, blank=True)
    maintenance_token = models.CharField(max_length=64, default="")
    maintenance_version = models.BigIntegerField(default=0)
    maintenance_previous_ready = models.BooleanField(default=False)
    maintenance_started_at = models.TextField(null=True, blank=True)
    updated_at = models.TextField()

    class Meta:
        db_table = "netshop_promotion_aggregate_control"


class NetshopImportScopeHead(models.Model):
    scope_key = models.CharField(primary_key=True, max_length=64)
    state_token = models.CharField(max_length=64, default="initial")
    status = models.CharField(max_length=32, default="ready")
    owner_token = models.CharField(max_length=64, default="")
    current_batch_id = models.CharField(max_length=1024, default="")
    generation = models.BigIntegerField(default=0)
    owner_started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_import_scope_heads"


class NetshopImportAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch_id = models.CharField(max_length=1024, default="")
    scope_key = models.CharField(max_length=64, default="", db_index=True)
    raw_file_hash = models.CharField(max_length=64, default="", db_index=True)
    content_hash = models.CharField(max_length=64, default="")
    outcome = models.CharField(max_length=32)
    error_code = models.CharField(max_length=64, default="")
    actor_email = models.CharField(max_length=320, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "netshop_import_attempts"
        indexes = [models.Index(fields=["scope_key", "created_at"], name="net_attempt_scope_idx")]


class NetshopImportFingerprint(models.Model):
    id = models.BigAutoField(primary_key=True)
    batch_id = models.CharField(max_length=1024, unique=True)
    scope_key = models.CharField(max_length=64, db_index=True)
    scope_json = models.JSONField(default=dict)
    import_hash = models.CharField(max_length=64, default="")
    content_hash = models.CharField(max_length=64)
    raw_file_hash = models.CharField(max_length=64, db_index=True)
    row_count = models.BigIntegerField()
    published_state_token = models.CharField(max_length=64)
    status = models.CharField(max_length=32, default="completed")
    publication_sequence = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "netshop_import_fingerprints"


class NetshopWriteAuthority(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=16, default="d1")
    authority_epoch = models.UUIDField(null=True, blank=True)
    cutover_id = models.CharField(max_length=128, default="")
    migration_verify_run_id = models.CharField(max_length=64, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_write_authority"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="netshop_auth_singleton_ck"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["d1", "postgres"]),
                name="netshop_auth_status_ck",
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
                name="netshop_auth_state_ck",
            ),
        ]


class NetshopWriteRequestReceipt(models.Model):
    request_id = models.CharField(primary_key=True, max_length=128)
    body_sha256 = models.CharField(max_length=64)
    query_sha256 = models.CharField(max_length=64)
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=200)
    actor_email = models.CharField(max_length=320)
    status = models.CharField(max_length=32, default="processing")
    response_status = models.PositiveIntegerField(default=0)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "netshop_write_request_receipts"


class NetshopAssetUpload(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    fingerprint = models.CharField(max_length=64, unique=True)
    shop_name = models.CharField(max_length=100)
    snapshot_date = models.CharField(max_length=10)
    file_name = models.TextField()
    file_size_bytes = models.BigIntegerField()
    chunk_size_bytes = models.BigIntegerField()
    chunk_count = models.BigIntegerField()
    received_chunk_count = models.BigIntegerField(default=0)
    received_bytes = models.BigIntegerField(default=0)
    status = models.CharField(max_length=32, default="uploading")
    processing_owner = models.CharField(max_length=64, default="")
    owner_generation = models.BigIntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "netshop_asset_uploads"
        indexes = [models.Index(fields=["expires_at", "status"], name="net_upload_expiry_idx")]


class NetshopAssetUploadChunk(models.Model):
    id = models.BigAutoField(primary_key=True)
    upload_id = models.CharField(max_length=64)
    chunk_index = models.BigIntegerField()
    object_key = models.TextField()
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "netshop_asset_upload_chunks"
        constraints = [
            models.UniqueConstraint(fields=["upload_id", "chunk_index"], name="net_upload_chunk_uq")
        ]
        indexes = [models.Index(fields=["upload_id", "chunk_index"], name="net_upload_chunk_idx")]


class NetshopAssetUploadResult(models.Model):
    upload_id = models.CharField(primary_key=True, max_length=64)
    result_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "netshop_asset_upload_results"


class NetshopMigrationRun(models.Model):
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
        db_table = "netshop_migration_runs"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(completed_at__isnull=True)
                    | models.Q(completed_at__gte=models.F("created_at"))
                ),
                name="net_migration_time_order_ck",
            )
        ]
